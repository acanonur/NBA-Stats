"""The ``next_game_projection`` resolver, against the seeded database.

The engine's own mathematics is covered in ``tests/test_projection.py``; nothing here
re-derives a formula. What this file checks is the part the resolver owns, and the two things
that would be worth the most if they were wrong:

* **No leakage.** :func:`test_a_projection_cannot_see_the_game_it_projects` fabricates a
  monstrous box score for the very game being projected, plus a team line with an absurd pace
  and defensive rating, and asserts the payload does not move by so much as a float. A
  projection that peeks at its own game does not look broken — it looks *good* — so this is
  the most important test in the file. It carries its own power check: the same fabrication
  applied to a game that really is in the past **does** move the numbers, which proves the
  probe is potent and that the history reader is reading what it claims to read.

* **Contract shape.** The key sets are parsed out of ``contracts/CONTRACT.md`` §4 rather than
  copied into this file, so the assertion is against the document both halves of the project
  are built from. A renamed key is a crash on a device, not a diff.

Everything else follows ``docs/PROJECTION.md`` §7: never a mean without its interval, minutes
reported separately, the factors shown, thin history degraded honestly and *said out loud*,
and every projected line marked ``"estimated"`` because a projection is never a record.
"""
from __future__ import annotations

import ast
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from nbastats import catalog
from nbastats import projection_constants as C
from nbastats.api import deps
from nbastats.api.app import create_app
from nbastats.models import Game, Player, PlayerGameBasic, PlayerSeason, TeamGame, TeamSeason
from nbastats.widgets import RESOLVERS, TTL_SECONDS, ResolveContext, WidgetError
from nbastats.widgets import next_game_projection as widget

BACKEND_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = BACKEND_ROOT.parent / "contracts" / "CONTRACT.md"
SOURCE_PATH = BACKEND_ROOT / "nbastats" / "widgets" / "next_game_projection.py"

KIND = "next_game_projection"
CURRENT_SEASON = "2025-26"
COMPLETED_SEASON = "2024-25"
PRE_ADVANCED_SEASON = "1992-93"

#: A season the store does not hold at all, before per-game advanced box scores existed.
ERA_GAP_SEASON = "1985-86"

#: Enough players to prove the resolver is not tuned to one favourite, few enough that the
#: sweep stays a few seconds. The order is by id, so the sample is stable across runs.
PLAYER_SWEEP = 24


# --------------------------------------------------------------------------- the contract


def _section(name: str) -> str:
    """The body of one ``###`` section of ``contracts/CONTRACT.md``."""
    text = CONTRACT_PATH.read_text(encoding="utf-8")
    marker = f"### `{name}`"
    start = text.index(marker) + len(marker)
    rest = text[start:]
    end = rest.find("\n### ")
    return rest if end < 0 else rest[:end]


def _example(name: str) -> str:
    """The first fenced JSON example in a section."""
    body = _section(name)
    start = body.index("```json") + len("```json")
    return body[start : body.index("```", start)]


def _keys_at_depth_one(block: str) -> list[str]:
    """Keys at depth 1 of a JSON-ish example.

    The contract's examples are illustrative rather than parseable — ``{ "...TeamRef" }`` and
    ``"…": "same shape"`` both appear — so this walks the text. A string counts as a key only
    when it sits at depth 1 and is followed by a colon, which leaves the ``"...Ref"``
    placeholders out.
    """
    keys: list[str] = []
    depth = 0
    index = 0
    while index < len(block):
        char = block[index]
        if char == '"':
            end = index + 1
            while end < len(block) and block[end] != '"':
                end += 2 if block[end] == "\\" else 1
            if depth == 1 and block[end + 1 :].lstrip().startswith(":"):
                keys.append(block[index + 1 : end])
            index = end + 1
            continue
        if char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
        index += 1
    return keys


def _nested(block: str, key: str) -> str:
    """The first balanced ``{...}`` after ``"key":`` — one nested object of an example."""
    start = block.index(f'"{key}"')
    start = block.index("{", start)
    depth = 0
    for index in range(start, len(block)):
        if block[index] == "{":
            depth += 1
        elif block[index] == "}":
            depth -= 1
            if depth == 0:
                return block[start : index + 1]
    raise AssertionError(f"CONTRACT.md §4 {key!r} has no balanced object")


@pytest.fixture(scope="module")
def contract_keys() -> dict[str, set[str]]:
    """``{"payload"|"game"|...: the keys CONTRACT.md §4 documents}``, parsed from the file."""
    example = _example(KIND)
    return {
        "payload": set(_keys_at_depth_one(example)),
        "game": set(_keys_at_depth_one(_nested(example, "game"))),
        "projectedMinutes": set(_keys_at_depth_one(_nested(example, "projectedMinutes"))),
        "line": set(_keys_at_depth_one(_nested(example, "lines"))),
        "factor": set(_keys_at_depth_one(_nested(example, "factors"))),
        "combo": set(_keys_at_depth_one(_nested(example, "combo"))),
        "method": set(_keys_at_depth_one(_nested(example, "method"))),
    }


# --------------------------------------------------------------------------- fixtures


@pytest.fixture()
def ctx(seeded_db: Session) -> ResolveContext:
    """A resolve context on the seeded database, with no favourites set."""
    return ResolveContext.from_request(seeded_db, None, request_id="test")


@pytest.fixture(scope="module")
def app_client(seeded_engine: Engine) -> Iterator[TestClient]:
    deps.reset_rate_limiter()
    with TestClient(create_app()) as client:
        yield client
    deps.reset_rate_limiter()


@pytest.fixture(scope="module")
def rotation_players(seeded_engine: Engine) -> list[int]:
    """Players with a real current-season workload — the ones a reader would ask about."""
    with Session(seeded_engine, future=True) as session:
        return [
            int(pid)
            for pid in session.execute(
                select(PlayerSeason.player_id)
                .where(PlayerSeason.season == CURRENT_SEASON)
                .where(PlayerSeason.season_type == "Regular Season")
                .where(PlayerSeason.gp >= 3)
                .order_by(PlayerSeason.player_id)
                .limit(PLAYER_SWEEP)
            ).scalars()
        ]


def _config(**overrides: Any) -> dict[str, Any]:
    """The catalog's defaults with overrides applied, cleaned the way a real resolve is."""
    supplied = {**_defaults(), **overrides}
    cleaned, errors = catalog.validate_widget_config(KIND, supplied)
    # ``playerId`` is a required subject field whose catalog default is ``null`` — a tile the
    # reader has just dropped on the grid and not yet pointed at anybody. The resolver treats
    # that as the favourite token, as ``widgets/base.resolve_subject_token`` documents, so it
    # is the one error a defaults-only config is allowed to carry. Anything else means this
    # test wrote a config the catalog would have rejected.
    unexpected = [
        error
        for error in errors
        if not (error.field == "playerId" and supplied.get("playerId") is None)
    ]
    assert not unexpected, unexpected
    return cleaned


def _defaults() -> dict[str, Any]:
    return dict(catalog.widget_config_defaults(KIND))


def _resolve(ctx: ResolveContext, **overrides: Any) -> tuple[dict[str, Any], str, list[str]]:
    return RESOLVERS[KIND](_config(**overrides), ctx)


def _fresh(session: Session) -> ResolveContext:
    """A context with an empty memo cache, so a second resolve re-reads the database."""
    return ResolveContext.from_request(session, None, request_id="test")


# --------------------------------------------------------------------------- registry


def test_the_kind_is_registered_with_the_contract_ttl() -> None:
    """§8: 600s, and the widget catalog's ``minRefreshSeconds`` has to agree."""
    assert KIND in RESOLVERS
    assert TTL_SECONDS[KIND] == 600
    assert catalog.widget(KIND)["minRefreshSeconds"] == 600


def test_resolves_from_catalog_defaults(ctx: ResolveContext) -> None:
    """A tile the reader has just dropped on the grid, with nothing configured, still draws."""
    payload, availability, notes = _resolve(ctx)
    assert availability == "estimated", "a projection is never a record"
    assert isinstance(notes, list) and all(isinstance(note, str) for note in notes)
    assert payload["player"]["playerId"]
    assert payload["lines"], "the default config asks for seven statistics"
    assert [line["metric"] for line in payload["lines"]] == _defaults()["stats"]
    assert payload["projectedMinutes"]["value"] > 0
    assert payload["notes"], "§7 rule 2: the minutes caveat is always said"


def test_every_next_game_preset_widget_resolves(app_client: TestClient) -> None:
    """The preset the widget ships in, end to end through the route."""
    preset = catalog.preset("next_game")
    body = app_client.post(
        "/v1/dashboard/resolve",
        json={
            "layoutId": "test-next-game",
            "widgets": [
                {
                    "id": w["id"],
                    "kind": w["kind"],
                    "size": w.get("size", "large"),
                    "config": w.get("config", {}),
                }
                for w in preset["widgets"]
            ],
        },
    )
    assert body.status_code == 200, body.text
    results = body.json()["results"]
    assert [r["kind"] for r in results] == [w["kind"] for w in preset["widgets"]]
    for result in results:
        assert result["status"] in ("ok", "partial"), result
        assert result["payload"] is not None
        assert result["ttlSeconds"] == TTL_SECONDS[result["kind"]]

    projection = next(r for r in results if r["kind"] == KIND)
    assert projection["availability"] == "estimated"
    assert all(line["availability"] == "estimated" for line in projection["payload"]["lines"])


def test_the_projection_resolves_for_every_player_the_preset_can_point_at(
    seeded_db: Session, rotation_players: list[int]
) -> None:
    """``$favorite_player`` is whoever the reader pinned, so every player has to work.

    One context for the whole sweep: the memo cache is per request by design, and sharing it
    is also the honest performance claim — a dashboard does not pay for the league dispersion
    sample once per tile.
    """
    assert len(rotation_players) >= 10, "the seeded league lost its rotation players"
    shared = _fresh(seeded_db)
    for player_id in rotation_players:
        payload, availability, _notes = _resolve(shared, playerId=player_id)
        assert availability == "estimated", player_id
        assert payload["player"]["playerId"] == player_id
        _assert_payload_is_coherent(payload, player_id)


def _assert_payload_is_coherent(payload: dict[str, Any], label: Any = "") -> None:
    """The invariants every projection has to satisfy, whoever it is about."""
    minutes = payload["projectedMinutes"]
    assert 0.0 <= minutes["value"] <= 48.0, label
    assert minutes["low"] <= minutes["value"] <= minutes["high"], label
    assert minutes["halfLifeGames"] == 2, label

    for line in payload["lines"]:
        assert line["availability"] == "estimated", (label, line["metric"])
        assert line["mean"] >= 0.0, (label, line["metric"])
        assert line["mean"] < 100.0, (label, line["metric"], "a projected line is not a typo")
        assert line["low"] >= 0, (label, line["metric"], "a count cannot be negative")
        assert line["low"] <= line["high"], (label, line["metric"])
        assert line["shrinkageK"] == C.stabilisation_k(line["metric"]), label
        assert line["halfLifeGames"] == C.halflife_games(line["metric"]), label
        assert 0.0 <= line["shrinkageWeight"] <= 1.0, label
        assert line["exposureMinutes"] >= 0.0, label
        assert line["dispersionMultiplier"] > 0.0, label
        _assert_interval_brackets_the_mean(line, label)


def _assert_interval_brackets_the_mean(line: dict[str, Any], label: Any = "") -> None:
    """``low <= mean <= high``, on the scale the bounds are actually reported on.

    The bounds are whole counts — the contract writes them as ``19`` and ``38`` — while the
    mean is continuous. For a mean of 15.9 in ``[11, 21]`` the containment is literal. For a
    mean of 0.08 the shortest 80% interval of the count distribution is ``{0}``, because
    ``P(X = 0) > 0.8``, and no interval of whole numbers can do better; the honest statement
    there is that the mean is inside the interval *up to the discreteness of a count*, which
    is what the second branch asserts. Nothing is being let through: a mean outside its
    bounds by a whole count or more fails either way.
    """
    mean, low, high = line["mean"], line["low"], line["high"]
    if mean >= 1.0:
        assert low <= mean <= high, (label, line["metric"], mean, low, high)
    else:
        assert low - 1 < mean < high + 1, (label, line["metric"], mean, low, high)


# --------------------------------------------------------------------------- the contract


def test_the_payload_key_set_is_exactly_the_contract(
    ctx: ResolveContext, contract_keys: dict[str, set[str]]
) -> None:
    """§4, key for key, parsed from the document rather than copied into this file."""
    payload, _availability, _notes = _resolve(ctx)

    assert set(payload) == contract_keys["payload"]
    assert set(payload["game"]) == contract_keys["game"]
    assert set(payload["projectedMinutes"]) == contract_keys["projectedMinutes"]
    assert set(payload["combo"]) == contract_keys["combo"]
    assert set(payload["method"]) == contract_keys["method"]
    assert payload["lines"] and payload["factors"]
    for line in payload["lines"]:
        assert set(line) == contract_keys["line"]
    for factor in payload["factors"]:
        assert set(factor) == contract_keys["factor"]

    # The nested shared objects are §2's, unchanged.
    assert set(payload["player"]) == {
        "playerId", "name", "firstName", "lastName", "teamId", "teamAbbr", "position",
        "jersey", "headshotUrl", "isActive",
    }
    assert set(payload["game"]["opponent"]) == {
        "teamId", "abbr", "name", "city", "nickname", "conference", "division",
    }


def test_the_payload_is_json_and_the_descriptors_are_the_catalog_entries(
    ctx: ResolveContext,
) -> None:
    payload, _availability, _notes = _resolve(ctx)
    round_tripped = json.loads(json.dumps(payload))
    assert round_tripped == payload, "the payload must be plain JSON, not model objects"
    for line in payload["lines"]:
        assert line["descriptor"] == dict(catalog.metric(line["metric"]))
        assert line["displayValue"] == catalog.format_metric(line["metric"], line["mean"])


def test_the_factors_are_the_four_the_paper_names(ctx: ResolveContext) -> None:
    """§7 rule 3: showing the multipliers is what makes it an argument, not an oracle."""
    payload, _availability, _notes = _resolve(ctx)
    assert [f["key"] for f in payload["factors"]] == ["pace", "opponent", "venue", "rest"]
    for factor in payload["factors"]:
        assert factor["explanation"], factor["key"]
        assert 0.5 < factor["value"] < 1.5, factor


def test_every_factor_carries_a_contribution_for_every_projected_line(
    ctx: ResolveContext,
) -> None:
    """The "Why" section's numbers, in each statistic's own units.

    A multiplier of 1.021 means nothing to a reader until it is turned into points, which is
    what docs/BROADSHEET.md §5 does. The check that matters is the sign agreement: a factor
    above 1.0 must never produce a contribution that reads as a penalty.
    """
    payload, _availability, _notes = _resolve(ctx)
    projected = {line["metric"]: line["mean"] for line in payload["lines"]}
    assert projected

    for factor in payload["factors"]:
        contributions = factor["contributions"]
        assert set(contributions) == set(projected), factor["key"]
        for metric, contribution in contributions.items():
            assert contribution == pytest.approx(
                projected[metric] * (factor["value"] - 1.0), abs=5e-3
            ), (factor["key"], metric)
            if factor["value"] > 1.0:
                assert contribution >= 0.0, (factor["key"], metric)
            elif factor["value"] < 1.0:
                assert contribution <= 0.0, (factor["key"], metric)
            else:
                assert contribution == 0.0, (factor["key"], metric)


def test_the_contributions_are_not_sold_as_a_decomposition(ctx: ResolveContext) -> None:
    """They are first-order, and the contract says so rather than the payload pretending.

    If this ever *does* sum exactly, something has been changed to make it sum — which would
    mean the factors stopped being multiplicative, and far more than this test would be wrong.
    """
    payload, _availability, _notes = _resolve(ctx)
    multiplier = 1.0
    for factor in payload["factors"]:
        multiplier *= factor["value"]

    points = next(line for line in payload["lines"] if line["metric"] == "pts")
    neutral = points["mean"] / multiplier
    summed = sum(f["contributions"]["pts"] for f in payload["factors"])

    assert summed == pytest.approx(points["mean"] - neutral, abs=0.25), (
        "a first-order attribution should still land close to the truth"
    )

    contract = CONTRACT_PATH.read_text(encoding="utf-8")
    assert "first-order attribution, not a decomposition" in contract


# --------------------------------------------------------------------------- no leakage


def _target_game(ctx: ResolveContext, payload: dict[str, Any]) -> Game:
    game = ctx.session.get(Game, payload["game"]["gameId"])
    assert game is not None and game.status == "scheduled"
    return game


def _fabricated_line(game_id: str, player_id: int, team_id: int) -> PlayerGameBasic:
    """A box score no human has ever produced. If it leaks, the numbers move a long way."""
    return PlayerGameBasic(
        game_id=game_id,
        player_id=player_id,
        team_id=team_id,
        started=True,
        minutes=48.0,
        fgm=40, fga=45, fg3m=20, fg3a=22, ftm=20, fta=20,
        oreb=20, dreb=30, reb=50, ast=30, stl=15, blk=15, tov=0, pf=0,
        pts=120,
        plus_minus=60.0,
        fantasy_pts=200.0,
        data_source="test",
    )


def test_a_projection_cannot_see_the_game_it_projects(seeded_db: Session) -> None:
    """**The most important test in the file.**

    A projection for game G must be identical whether or not G's own rows are in the store.
    The probe writes the player a 120-point, 48-minute line on G and gives the opponent a
    140-pace, 60-defensive-rating team line on the same game, then resolves again and
    demands the payload be equal — not close, equal.

    Everything is written through the session and rolled back by the ``seeded_db`` fixture,
    so the database the rest of the suite sees is untouched.
    """
    before, _availability, _notes = _resolve(_fresh(seeded_db))
    player_id = before["player"]["playerId"]
    game = _target_game(_fresh(seeded_db), before)
    team_id = before["player"]["teamId"]
    opponent_id = before["game"]["opponent"]["teamId"]
    assert team_id and opponent_id and team_id != opponent_id

    seeded_db.add(_fabricated_line(game.game_id, player_id, team_id))
    seeded_db.add(
        TeamGame(
            game_id=game.game_id,
            team_id=opponent_id,
            is_home=(game.home_team_id == opponent_id),
            won=False,
            minutes=240.0,
            pts=60,
            opp_pts=200,
            pace=140.0,
            def_rtg=60.0,
            off_rtg=60.0,
            net_rtg=0.0,
            data_source="test",
        )
    )
    seeded_db.flush()

    after, _availability, _notes = _resolve(_fresh(seeded_db))
    assert after == before, (
        "the projection moved when the game it is projecting acquired a box score — the "
        "history reader is not filtering on the target game's date"
    )
    seeded_db.rollback()


def test_the_leakage_probe_is_potent(seeded_db: Session) -> None:
    """The power check for the test above: the same fabrication *in the past* does move it.

    Without this, a resolver that read no history at all would pass the leakage test
    trivially. Here the player's earliest pre-tip-off game is rewritten into the same absurd
    line, and the projection has to notice.
    """
    before, _availability, _notes = _resolve(_fresh(seeded_db))
    player_id = before["player"]["playerId"]

    earliest = seeded_db.execute(
        select(PlayerGameBasic)
        .join(Game, Game.game_id == PlayerGameBasic.game_id)
        .where(PlayerGameBasic.player_id == player_id)
        .where(Game.game_date < date.fromisoformat(before["game"]["date"]))
        .order_by(Game.game_date, Game.game_id)
        .limit(1)
    ).scalar_one()
    earliest.minutes = 48.0
    earliest.pts = 120
    earliest.reb = 50
    earliest.ast = 30
    seeded_db.flush()

    after, _availability, _notes = _resolve(_fresh(seeded_db))
    assert after["lines"][0]["mean"] != before["lines"][0]["mean"], (
        "rewriting a game the player really played changed nothing, so the leakage test "
        "above proves nothing"
    )
    seeded_db.rollback()


def test_a_game_after_the_target_cannot_leak_either(seeded_db: Session) -> None:
    """The cutoff is a date, not "the one game": a later fixture is just as invisible."""
    before, _availability, _notes = _resolve(_fresh(seeded_db))
    player_id = before["player"]["playerId"]
    team_id = before["player"]["teamId"]
    target = date.fromisoformat(before["game"]["date"])

    later = seeded_db.execute(
        select(Game)
        .where(Game.status == "scheduled")
        .where(Game.game_date > target)
        .where((Game.home_team_id == team_id) | (Game.away_team_id == team_id))
        .order_by(Game.game_date, Game.game_id)
        .limit(1)
    ).scalar_one()
    seeded_db.add(_fabricated_line(later.game_id, player_id, team_id))
    seeded_db.flush()

    after, _availability, _notes = _resolve(_fresh(seeded_db))
    assert after == before
    seeded_db.rollback()


# --------------------------------------------------------------------------- degradation


@pytest.fixture()
def thin_player(seeded_db: Session) -> tuple[int, str]:
    """A player with exactly one game to his name, written into the rolled-back session."""
    game = seeded_db.execute(
        select(Game)
        .where(Game.season == CURRENT_SEASON)
        .where(Game.season_type == "Regular Season")
        .where(Game.status == "final")
        .order_by(Game.game_date.desc(), Game.game_id.desc())
        .limit(1)
    ).scalar_one()
    player_id = 9_999_001
    seeded_db.add(
        Player(
            player_id=player_id,
            full_name="Rookie Callup",
            first_name="Rookie",
            last_name="Callup",
            position="G",
            is_active=True,
            from_year=2026,
            to_year=2026,
        )
    )
    seeded_db.add(
        PlayerGameBasic(
            game_id=game.game_id,
            player_id=player_id,
            team_id=game.home_team_id,
            started=False,
            minutes=20.0,
            fgm=5, fga=11, fg3m=1, fg3a=3, ftm=1, fta=2,
            oreb=1, dreb=4, reb=5, ast=3, stl=1, blk=0, tov=2, pf=2,
            pts=12,
            plus_minus=2.0,
            fantasy_pts=24.0,
            data_source="test",
        )
    )
    seeded_db.flush()
    yield player_id, game.game_id
    seeded_db.rollback()


def test_a_player_with_almost_no_history_leans_on_the_league_prior(
    seeded_db: Session, thin_player: tuple[int, str]
) -> None:
    """``docs/PROJECTION.md`` §7 rule 4: that is not a confident projection, and must not read
    as one.

    Twenty minutes of exposure against a stabilisation constant of 81 leaves the points rate
    mostly the league prior, the dispersion multiplier at the prior 1.0, and an interval wide
    enough that nobody could mistake the mean for a forecast.
    """
    player_id, _game_id = thin_player
    payload, availability, notes = _resolve(_fresh(seeded_db), playerId=player_id)
    assert availability == "estimated"

    points = next(line for line in payload["lines"] if line["metric"] == "pts")
    prior = C.league_rate("pts")
    observed = 12.0 / 20.0

    assert points["exposureMinutes"] == pytest.approx(20.0)
    assert points["shrinkageWeight"] < 0.45, "most of this rate is the league prior"
    # The rate sits between the prior and what he actually did, and nearer the prior.
    assert prior < points["ratePerMinute"] < observed
    assert abs(points["ratePerMinute"] - prior) < abs(points["ratePerMinute"] - observed)

    # One game of history cannot move a variance ratio: eq. 10.5 shrinks it at k_c = 60.
    assert points["dispersionMultiplier"] == pytest.approx(1.0, abs=0.05)

    # Never a mean without its interval, and this interval is visibly wide: it spans more
    # than half the mean, which is what "do not read this as a point estimate" looks like.
    assert points["low"] < points["mean"] < points["high"]
    assert (points["high"] - points["low"]) / points["mean"] > 0.5

    marker = widget.THIN_HISTORY_MARKER
    assert any(marker in note for note in payload["notes"]), payload["notes"]
    assert any(marker in note for note in notes), "the result has to say so too"


def test_a_player_with_no_history_at_all_is_the_prior_and_says_so(seeded_db: Session) -> None:
    """No games, no minutes: the league prior, a multiplier of exactly 1.0, and a note."""
    player_id = 9_999_002
    seeded_db.add(
        Player(player_id=player_id, full_name="Unplayed Signing", is_active=True)
    )
    seeded_db.flush()

    payload, availability, notes = _resolve(_fresh(seeded_db), playerId=player_id)
    assert availability == "estimated"
    assert payload["game"] is None, "no team, so no schedule to project against"
    assert payload["projectedMinutes"]["value"] == 0.0
    for line in payload["lines"]:
        assert line["ratePerMinute"] == pytest.approx(C.league_rate(line["metric"]))
        assert line["shrinkageWeight"] == pytest.approx(0.0)
        assert line["dispersionMultiplier"] == 1.0
        assert line["exposureMinutes"] == 0.0
        assert line["mean"] == 0.0
    assert any("no pre-tip-off game history" in note.lower() for note in notes)
    seeded_db.rollback()


def test_no_scheduled_game_still_projects_and_says_so(seeded_db: Session) -> None:
    """§4: ``game`` is null, the projection is against a league-average opponent, notes say it."""
    player_id = int(
        seeded_db.execute(
            select(PlayerSeason.player_id)
            .where(PlayerSeason.season == COMPLETED_SEASON)
            .where(PlayerSeason.season_type == "Regular Season")
            .order_by(PlayerSeason.gp.desc(), PlayerSeason.player_id)
            .limit(1)
        ).scalars().first()
    )
    payload, availability, notes = _resolve(
        _fresh(seeded_db), playerId=player_id, season=COMPLETED_SEASON
    )
    assert availability == "estimated"
    assert payload["game"] is None
    assert payload["lines"] and payload["projectedMinutes"]["value"] > 0
    assert any("no scheduled" in note.lower() for note in notes)
    assert any("league-average opponent" in note for note in payload["notes"])

    # A league-average opponent is exactly neutral, and an unknown venue and rest with it.
    factors = {f["key"]: f for f in payload["factors"]}
    assert factors["opponent"]["value"] == pytest.approx(1.0)
    assert factors["venue"]["value"] == 1.0
    assert "venue is unknown" in factors["venue"]["explanation"]
    assert factors["rest"]["value"] == 1.0


# --------------------------------------------------------------------------- the era gate


def test_a_pre_advanced_season_is_unavailable_with_a_note(ctx: ResolveContext) -> None:
    """1985-86 has box scores but no pace and no defensive rating, so there is no f_pace and
    no f_opp — and a projection computed from neutral factors would hide that.

    The contract's answer is a shaped, empty payload with ``"unavailable"``, the same
    treatment ``shot_profile`` gives a season with no shot chart. Not a 500, and not a number.
    """
    payload, availability, notes = _resolve(ctx, season=ERA_GAP_SEASON)

    assert availability == "unavailable"
    assert notes and any(widget.PROJECTION_FROM in note for note in notes)
    assert widget.PROJECTION_FROM == "1996-97", "contracts/CONTRACT.md §6's boundary"
    assert payload["lines"] == [], "no line may be projected without its context factors"
    assert payload["factors"] == []
    assert payload["combo"] is None
    assert payload["game"] is None
    assert payload["projectedMinutes"]["value"] is None
    assert payload["projectedMinutes"]["displayValue"] == catalog.EM_DASH
    assert payload["player"]["playerId"]
    assert payload["notes"]



def test_the_era_payload_still_has_the_contract_key_set(
    ctx: ResolveContext, contract_keys: dict[str, set[str]]
) -> None:
    """An unavailable projection is still decoded by the same Swift struct."""
    payload, _availability, _notes = _resolve(ctx, season=ERA_GAP_SEASON)
    assert set(payload) == contract_keys["payload"]
    assert set(payload["projectedMinutes"]) == contract_keys["projectedMinutes"]
    assert set(payload["method"]) == contract_keys["method"]


def test_a_stored_pre_advanced_season_is_unavailable_too(ctx: ResolveContext) -> None:
    """1992-93 *is* in the store, with real box scores. It is still not projectable."""
    payload, availability, _notes = _resolve(ctx, season=PRE_ADVANCED_SEASON)
    assert availability == "unavailable"
    assert payload["lines"] == []


# --------------------------------------------------------------------------- configuration


def test_show_combo_false_omits_the_combination_line(ctx: ResolveContext) -> None:
    with_combo, _a, _n = _resolve(ctx, showCombo=True)
    without, _a2, _n2 = _resolve(_fresh(ctx.session), showCombo=False)

    assert with_combo["combo"]["label"] == "PTS+REB+AST"
    assert without["combo"] is None
    assert "combo" in without, "the key stays; the block goes"
    assert without["method"]["correlationApplied"] is False
    assert with_combo["method"]["correlationApplied"] is True
    # The lines themselves are untouched by the switch.
    assert [line["mean"] for line in without["lines"]] == [
        line["mean"] for line in with_combo["lines"]
    ]


def test_the_combination_line_uses_the_residual_correlation(ctx: ResolveContext) -> None:
    """§5: ignoring the covariance understates the spread, and the gap is the point."""
    payload, _availability, _notes = _resolve(ctx)
    combo = payload["combo"]
    assert combo["correlation"] == [[1.0, 0.3, 0.17], [0.3, 1.0, 0.2], [0.17, 0.2, 1.0]]
    assert combo["sd"] > combo["sdIfIndependent"] > 0
    expected_inflation = combo["sd"] / combo["sdIfIndependent"] - 1.0
    assert combo["inflation"] == pytest.approx(expected_inflation, rel=1e-3)
    by_key = {line["metric"]: line["mean"] for line in payload["lines"]}
    # combo.mean is rounded once, from unrounded components; the right-hand side sums three
    # values that were each rounded to three decimals first, so the two can differ by up to
    # 1.5e-3 without anything being wrong.
    assert combo["mean"] == pytest.approx(
        by_key["pts"] + by_key["reb"] + by_key["ast"], abs=2e-3)
    assert combo["low"] <= combo["mean"] <= combo["high"]


def test_show_factors_false_omits_the_factors(ctx: ResolveContext) -> None:
    with_factors, _a, _n = _resolve(ctx, showFactors=True)
    without, _a2, _n2 = _resolve(_fresh(ctx.session), showFactors=False)

    assert len(with_factors["factors"]) == 4
    assert without["factors"] == []
    assert "factors" in without
    # Hiding the factors does not remove them from the arithmetic, and must not.
    assert [line["mean"] for line in without["lines"]] == [
        line["mean"] for line in with_factors["lines"]
    ]


def test_the_opponent_override_changes_f_opp(seeded_db: Session) -> None:
    """The catalog's override is the "what if they played X instead" control."""
    base, _availability, _notes = _resolve(_fresh(seeded_db))
    scheduled_opponent = base["game"]["opponent"]["teamId"]

    # The season's worst defence that is not the scheduled opponent: a big, signed change.
    override = int(
        seeded_db.execute(
            select(TeamSeason.team_id)
            .where(TeamSeason.season == CURRENT_SEASON)
            .where(TeamSeason.season_type == "Regular Season")
            .where(TeamSeason.team_id != scheduled_opponent)
            .where(TeamSeason.team_id != base["player"]["teamId"])
            .order_by(TeamSeason.def_rtg.desc())
            .limit(1)
        ).scalars().one()
    )
    changed, availability, _notes = _resolve(_fresh(seeded_db), opponentTeamId=override)

    assert availability == "estimated"
    base_opp = next(f for f in base["factors"] if f["key"] == "opponent")["value"]
    new_opp = next(f for f in changed["factors"] if f["key"] == "opponent")["value"]
    assert new_opp != base_opp, "the override did not reach f_opp"
    assert changed["game"]["opponent"]["teamId"] == override
    assert changed["game"]["gameId"] == base["game"]["gameId"], "the fixture itself is real"
    assert any("overridden" in note for note in changed["notes"])
    # f_opp is DRtg_opp / DRtg_lg, so a worse defence projects more production.
    assert new_opp > base_opp
    assert changed["lines"][0]["mean"] > base["lines"][0]["mean"]


def test_interval_none_suppresses_the_bounds_but_not_the_provenance(
    ctx: ResolveContext,
) -> None:
    payload, _availability, _notes = _resolve(ctx, interval="none")
    for line in payload["lines"]:
        assert line["low"] is None and line["high"] is None
        assert line["intervalLevel"] is None
        assert line["mean"] > 0 or line["ratePerMinute"] >= 0
        assert line["ratePerMinute"] is not None and line["exposureMinutes"] is not None
    assert any("misleading" in note for note in payload["notes"]), (
        "§7 rule 1: hiding the interval has to be said out loud"
    )


def test_interval_fifty_is_narrower_than_eighty(ctx: ResolveContext) -> None:
    eighty, _a, _n = _resolve(ctx, interval="80")
    fifty, _a2, _n2 = _resolve(_fresh(ctx.session), interval="50")
    for wide, narrow in zip(eighty["lines"], fifty["lines"]):
        assert narrow["intervalLevel"] == 0.5
        assert (narrow["high"] - narrow["low"]) <= (wide["high"] - wide["low"])


def test_an_unprojectable_statistic_is_dropped_with_a_note(ctx: ResolveContext) -> None:
    """TS% has no per-minute rate and no fitted ``k``; it cannot be projected, so it is not."""
    payload, _availability, notes = _resolve(ctx, stats=["pts", "ts_pct", "reb"])
    assert [line["metric"] for line in payload["lines"]] == ["pts", "reb"]
    assert any("ts_pct" in note for note in notes)


def test_a_config_with_nothing_projectable_is_one_bad_tile(ctx: ResolveContext) -> None:
    """§7: ``invalid_config`` naming the field, so the client opens the right row."""
    with pytest.raises(WidgetError) as raised:
        _resolve(ctx, stats=["ts_pct", "usg_pct"])
    assert raised.value.code == "invalid_config"
    assert raised.value.field == "stats"


def test_an_unknown_player_is_player_not_found(ctx: ResolveContext) -> None:
    with pytest.raises(WidgetError) as raised:
        _resolve(ctx, playerId=99_999_999)
    assert raised.value.code == "player_not_found"


# --------------------------------------------------------------------------- shrinkage


def test_steals_are_shrunk_harder_than_rebounds_at_the_same_exposure(
    ctx: ResolveContext,
) -> None:
    """The structural claim of §2, visible in a real payload.

    ``k`` is 322 minutes for steals and 34 for rebounds, so at one player's single exposure
    the steals rate must carry far more of the league prior than the rebounding rate does.
    """
    payload, _availability, _notes = _resolve(ctx)
    lines = {line["metric"]: line for line in payload["lines"]}
    assert lines["stl"]["exposureMinutes"] == lines["reb"]["exposureMinutes"]
    assert lines["stl"]["shrinkageK"] == 322
    assert lines["reb"]["shrinkageK"] == 34
    assert lines["stl"]["shrinkageWeight"] < lines["reb"]["shrinkageWeight"]


def test_the_projected_mean_is_the_master_formula(ctx: ResolveContext) -> None:
    """``S = M * r * f_pace * f_opp * f_home * f_rest``, multiplied out from the payload."""
    payload, _availability, _notes = _resolve(ctx)
    multiplier = 1.0
    for factor in payload["factors"]:
        multiplier *= factor["value"]
    minutes = payload["projectedMinutes"]["value"]
    for line in payload["lines"]:
        expected = minutes * line["ratePerMinute"] * multiplier
        # The payload rounds mean to three decimals, so a purely relative tolerance is
        # unusable for a small mean: 0.064 against 0.06427 is a 0.4% relative gap that is
        # entirely the rounding. Allow the rounding in absolute terms as well.
        assert line["mean"] == pytest.approx(expected, rel=2e-3, abs=1e-3), line["metric"]


# --------------------------------------------------------------------------- policy


def test_the_resolver_carries_no_market_translation() -> None:
    """``docs/PROJECTION.md`` §6, enforced on this module's own source.

    NBA.com's terms forbid gambling use of their statistics and the paper makes no
    profitability claim, so there is no price, no edge and no stake anywhere in the widget.
    """
    banned = {
        "kelly", "vig", "devig", "odds", "implied_prob", "ev_per_unit", "expected_value",
        "edge", "edge_erosion", "stake", "staking", "bankroll", "wager", "bet", "payout",
        "juice", "moneyline", "sportsbook", "decimal_odds",
    }
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            names.add(node.attr.lower())
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name.lower())
        elif isinstance(node, ast.arg):
            names.add(node.arg.lower())
    assert not names & banned, sorted(names & banned)


def test_the_resolver_needs_no_new_dependency() -> None:
    """numpy and scipy are not installed and must not become requirements."""
    source = SOURCE_PATH.read_text(encoding="utf-8")
    for module in ("numpy", "scipy", "pandas", "lightgbm", "statsmodels"):
        assert f"import {module}" not in source
        assert f"from {module}" not in source
    assert "numpy" not in sys.modules and "scipy" not in sys.modules


def test_the_widget_projects_exactly_what_the_engine_has_constants_for() -> None:
    """The catalog's default stat list may not outrun ``projection_constants``."""
    for key in catalog.widget_config_defaults(KIND)["stats"]:
        assert C.has_stat(key), key
    assert set(catalog.widget_config_defaults(KIND)["stats"]) <= set(C.PROJECTABLE_STATS)
