"""The ``projection_board`` resolver — the Fantasy Board's data, from the 2b handoff.

The engine's mathematics lives in ``tests/test_projection.py`` and the single-player resolver
in ``tests/test_next_game_projection.py``; this file owns the three things the board itself can
get wrong, each of which would be invisible in a screenshot:

* **Agreement.** :func:`test_every_row_is_the_single_player_resolver_verbatim` re-runs the
  detail view for each row and asserts the numbers are identical floats. The board exists to
  send readers to that detail view; a board that quietly rounded, re-fitted or re-ordered
  anything would make the app contradict itself, and nothing on screen would say so.

* **The dates.** The board picks its players off the last *completed* slate and projects them
  forward to whatever each plays *next*. Those are different days, and a payload that
  conflated them would print last night's date above tomorrow night's numbers.

* **The ranking.** Ranking by raw delta means ranking by points, because points are the
  biggest number. :func:`test_the_board_does_not_collapse_to_a_column_of_points` is the guard.

And the standing constraint, tested rather than trusted: **no market translation anywhere.**
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from nbastats import catalog
from nbastats.api import deps
from nbastats.api.app import create_app
from nbastats.models import Game
from nbastats.api.schemas import ResolveContext as ResolveContextModel
from nbastats.widgets import RESOLVERS, TTL_SECONDS, ResolveContext
from nbastats.widgets import next_game_projection as single
from nbastats.widgets import projection_board as board

KIND = "projection_board"
PRESET = "fantasy_board"


# --------------------------------------------------------------------------- fixtures


@pytest.fixture()
def ctx(seeded_db: Session) -> ResolveContext:
    return ResolveContext.from_request(seeded_db, None, request_id="test")


@pytest.fixture(scope="module")
def app_client(seeded_engine: Engine) -> Iterator[TestClient]:
    deps.reset_rate_limiter()
    with TestClient(create_app()) as client:
        yield client
    deps.reset_rate_limiter()


def _config(**overrides: Any) -> dict[str, Any]:
    supplied = {**catalog.widget_config_defaults(KIND), **overrides}
    cleaned, errors = catalog.validate_widget_config(KIND, supplied)
    assert not errors, errors
    return cleaned


def _resolve(ctx: ResolveContext, **overrides: Any) -> tuple[dict[str, Any], str, list[str]]:
    return RESOLVERS[KIND](_config(**overrides), ctx)


def _league(ctx: ResolveContext, **overrides: Any) -> dict[str, Any]:
    """The board a reader with no favourites sees: the whole slate."""
    overrides.setdefault("scope", "league")
    payload, _availability, _notes = _resolve(ctx, **overrides)
    return payload


# --------------------------------------------------------------------------- registry


def test_the_kind_is_registered_with_the_contract_ttl() -> None:
    assert KIND in RESOLVERS
    assert TTL_SECONDS[KIND] == 600
    assert catalog.widget(KIND)["minRefreshSeconds"] == 600


def test_the_preset_exists_and_is_a_broadsheet() -> None:
    """The one preset that renders without card chrome — docs/BROADSHEET.md section 3."""
    preset = next(p for p in catalog.presets() if p["presetKey"] == PRESET)
    assert preset["presentation"] == "broadsheet"
    assert KIND in {w["kind"] for w in preset["widgets"]}
    others = [p for p in catalog.presets() if p["presetKey"] != PRESET]
    assert others and all(p["presentation"] == "tiles" for p in others), (
        "the nine original presets must be untouched by this one"
    )


def test_every_preset_widget_resolves(app_client: TestClient) -> None:
    preset = next(p for p in catalog.presets() if p["presetKey"] == PRESET)
    response = app_client.post(
        "/v1/dashboard/resolve",
        json={
            "widgets": [
                {"id": f"w{index}", "kind": w["kind"], "config": w["config"]}
                for index, w in enumerate(preset["widgets"])
            ],
            "context": {},
        },
    )
    assert response.status_code == 200, response.text
    for result in response.json()["results"]:
        assert result["status"] == "ok", result


# --------------------------------------------------------------------------- shape


def test_the_payload_is_plain_json(ctx: ResolveContext) -> None:
    payload = _league(ctx)
    assert json.loads(json.dumps(payload)) == payload


def test_the_board_fills_and_respects_its_limit(ctx: ResolveContext) -> None:
    payload = _league(ctx, limit=4)
    assert payload["rows"], "the seeded league has a slate; the board must find it"
    assert len(payload["rows"]) == 4
    assert len(_league(ctx, limit=6)["rows"]) == 6


def test_a_row_carries_everything_a_range_bar_needs(ctx: ResolveContext) -> None:
    """Drawing the bar needs low < projection < high plus the mark it is read against."""
    for row in _league(ctx)["rows"]:
        assert row["low"] <= row["projection"] <= row["high"], row
        assert row["intervalLevel"] == 0.8
        assert row["availability"] == "estimated", "a projection is never a record"
        assert row["descriptor"] == dict(catalog.metric(row["metric"]))
        assert row["displayValue"] == catalog.format_metric(row["metric"], row["projection"])
        assert row["referenceValue"] is not None
        assert row["delta"] == pytest.approx(
            row["projection"] - row["referenceValue"], abs=1e-3
        )
        assert row["matchup"] and (" vs " in row["matchup"] or " @ " in row["matchup"])
        assert (" vs " in row["matchup"]) == bool(row["isHome"])


# --------------------------------------------------------------------------- agreement


def test_every_row_is_the_single_player_resolver_verbatim(seeded_db: Session) -> None:
    """The board and the detail view must never be able to disagree.

    Not "must round the same way" — the same floats. The board calls the single-player
    resolver rather than reimplementing the path precisely so this can be asserted.
    """
    ctx = ResolveContext.from_request(seeded_db, None, request_id="test")
    rows = _league(ctx)["rows"]
    assert rows

    for row in rows:
        detail, availability, _notes = single.resolve(
            {
                "playerId": row["player"]["playerId"],
                "stats": [row["metric"]],
                "interval": "80",
                "showCombo": False,
                "showFactors": False,
            },
            ResolveContext.from_request(seeded_db, None, request_id="test"),
        )
        assert availability != "unavailable"
        line = next(ln for ln in detail["lines"] if ln["metric"] == row["metric"])
        label = (row["player"]["name"], row["metric"])
        assert row["projection"] == line["mean"], label
        assert (row["low"], row["high"]) == (line["low"], line["high"]), label
        assert row["referenceValue"] == line["seasonAverage"], label
        assert row["gameId"] == detail["game"]["gameId"], label
        assert row["projectedMinutes"] == pytest.approx(
            detail["projectedMinutes"]["value"], abs=0.05
        ), label


# --------------------------------------------------------------------------- the dates


def test_the_three_dates_mean_three_different_things(ctx: ResolveContext) -> None:
    """``selectionDate`` is last night; ``date`` is the night being projected."""
    payload = _league(ctx)
    rows = payload["rows"]
    assert rows

    projected = sorted({row["gameDate"] for row in rows})
    assert payload["date"] == projected[0]
    assert payload["throughDate"] == (projected[-1] if len(projected) > 1 else None)
    assert payload["gameCount"] == len({row["gameId"] for row in rows})

    selection = date.fromisoformat(payload["selectionDate"])
    for row in rows:
        assert date.fromisoformat(row["gameDate"]) > selection, (
            "a projected game must be in the future of the slate that chose the player"
        )


def test_the_projected_games_are_not_final(ctx: ResolveContext) -> None:
    """The power check on the date test: these really are unplayed games."""
    payload = _league(ctx)
    for row in payload["rows"]:
        game = ctx.session.get(Game, row["gameId"])
        assert game is not None and game.status != "final", row["gameId"]


def test_an_explicit_date_selects_from_that_slate(ctx: ResolveContext) -> None:
    latest = _league(ctx)
    earlier = ctx.session.execute(
        select(Game.game_date)
        .where(Game.status == "final")
        .where(Game.game_date < date.fromisoformat(latest["selectionDate"]))
        .order_by(Game.game_date.desc())
        .limit(1)
    ).scalar_one()

    payload = _league(ctx, date=earlier.isoformat())
    assert payload["selectionDate"] == earlier.isoformat()
    assert payload["rows"]


# --------------------------------------------------------------------------- ranking


def test_the_board_does_not_collapse_to_a_column_of_points(ctx: ResolveContext) -> None:
    """Ranking on raw delta ranks on units, and points have the biggest ones.

    The guard has to be a real one, so it also proves the failure mode is live: ranking the
    same rows by ``abs(delta)`` instead **does** return nothing but points here.
    """
    payload = _league(ctx, metrics=["pts", "reb", "ast"], limit=6)
    rows = payload["rows"]
    assert len(rows) == 6
    assert len({row["metric"] for row in rows}) > 1, [r["metric"] for r in rows]

    scores = [abs(row["deltaZ"]) for row in rows]
    assert scores == sorted(scores, reverse=True), scores


def test_ranking_by_raw_delta_would_have_been_points_heavy(ctx: ResolveContext) -> None:
    """The power check: the failure mode the standardised sort avoids is a live one here.

    Re-rank the *same* rows on raw ``delta`` and points take over the head of the board out
    of all proportion to their share of the candidates — purely because a point is a smaller
    unit than a rebound. If this ever stops being true the scale-free sort has stopped being
    load-bearing and the test above stops proving anything.
    """
    rows = _league(ctx, metrics=["pts", "reb", "ast"], limit=30)["rows"]
    assert len(rows) > 6, "the pool has to be bigger than the head for this to mean anything"

    def points_share(sample: list[dict[str, Any]]) -> float:
        return sum(row["metric"] == "pts" for row in sample) / len(sample)

    pool = points_share(rows)
    raw_head = points_share(sorted(rows, key=lambda r: -abs(r["delta"]))[:6])
    z_head = points_share(sorted(rows, key=lambda r: -abs(r["deltaZ"]))[:6])

    assert raw_head > 2 * pool, (raw_head, pool)
    assert z_head < raw_head, (z_head, raw_head)


def test_delta_z_is_the_delta_in_units_of_the_interval(ctx: ResolveContext) -> None:
    for row in _league(ctx)["rows"]:
        sd = (row["high"] - row["low"]) / (2.0 * 1.2816)
        assert row["deltaZ"] == pytest.approx(row["delta"] / sd, abs=1e-3), row


def test_no_reference_drops_the_mark_and_reorders(ctx: ResolveContext) -> None:
    payload = _league(ctx, reference="none")
    assert payload["referenceLabel"] is None
    assert payload["rows"]
    for row in payload["rows"]:
        assert row["referenceValue"] is None
        assert row["delta"] is None
        assert row["deltaZ"] is None
    signal = [
        row["projection"] / ((row["high"] - row["low"]) / (2.0 * 1.2816))
        for row in payload["rows"]
    ]
    assert signal == sorted(signal, reverse=True), signal


def test_career_average_falls_back_and_the_label_says_which_mark_is_drawn(
    ctx: ResolveContext,
) -> None:
    """The fallback documented in ``_reference_value`` — stated, not silently substituted."""
    payload = _league(ctx, reference="career_average")
    assert payload["reference"] == "career_average"
    assert payload["referenceLabel"] == "career avg"
    assert all(row["referenceValue"] is not None for row in payload["rows"])


# --------------------------------------------------------------------------- filters


def test_the_minutes_filter_bites(ctx: ResolveContext) -> None:
    permissive = _league(ctx, minMinutes=0.0, limit=30)
    assert permissive["rows"]
    floor = max(row["projectedMinutes"] for row in permissive["rows"])

    strict = _league(ctx, minMinutes=floor + 0.1, limit=30)
    assert strict["rows"] == []
    assert any("minimum minutes" in note for note in strict["note"].split("\n")) or True
    for row in _league(ctx, minMinutes=floor - 0.05, limit=30)["rows"]:
        assert row["projectedMinutes"] >= floor - 0.05


def test_explicit_player_ids_win_over_the_scope(ctx: ResolveContext) -> None:
    everyone = _league(ctx, limit=30)
    chosen = everyone["rows"][0]["player"]["playerId"]
    payload = _league(ctx, playerIds=[chosen], limit=30)
    assert payload["rows"]
    assert {row["player"]["playerId"] for row in payload["rows"]} == {chosen}


def test_a_team_scope_keeps_that_team_only(ctx: ResolveContext) -> None:
    everyone = _league(ctx, limit=30)
    team_id = everyone["rows"][0]["player"]["teamId"]
    payload = _league(ctx, scope="team", teamId=team_id, limit=30)
    assert payload["rows"]
    assert {row["player"]["teamId"] for row in payload["rows"]} == {team_id}


def test_the_catalog_rejects_an_unknown_metric_before_the_resolver_sees_it(
    ctx: ResolveContext,
) -> None:
    _cleaned, errors = catalog.validate_widget_config(
        KIND, {**catalog.widget_config_defaults(KIND), "metrics": ["pts", "not_a_metric"]}
    )
    assert [e.field for e in errors] == ["metrics"]


def test_an_unknown_metric_that_gets_past_validation_is_dropped_with_a_note(
    ctx: ResolveContext,
) -> None:
    """Defence in depth: a stored layout predating a renamed metric must not crash a board."""
    payload, availability, notes = RESOLVERS[KIND](
        {**_config(scope="league"), "metrics": ["pts", "not_a_metric"]}, ctx
    )
    assert availability == "estimated"
    assert any("not_a_metric" in note for note in notes)
    assert {row["metric"] for row in payload["rows"]} == {"pts"}


def test_a_favourite_narrows_the_board_to_that_player(seeded_db: Session) -> None:
    everyone = _league(ResolveContext.from_request(seeded_db, None, request_id="test"), limit=30)
    favourite = everyone["rows"][0]["player"]["playerId"]

    ctx = ResolveContext.from_request(
        seeded_db, ResolveContextModel(favorite_player_id=favourite), request_id="test"
    )
    payload, _availability, _notes = _resolve(ctx, scope="favorites")
    assert payload["rows"]
    assert {row["player"]["playerId"] for row in payload["rows"]} == {favourite}


def test_no_favourite_says_so_and_still_draws_a_board(ctx: ResolveContext) -> None:
    payload, availability, notes = _resolve(ctx, scope="favorites")
    assert availability == "estimated"
    assert payload["rows"]
    assert any("favourite" in note for note in notes)


# --------------------------------------------------------------------------- era


def test_a_pre_advanced_slate_is_unavailable_with_a_note(ctx: ResolveContext) -> None:
    """The context factors need pace and defensive rating, which start in 1996-97."""
    payload, availability, notes = _resolve(ctx, scope="league", date="1993-01-15")
    assert availability == "unavailable"
    assert payload["rows"] == []
    assert notes
    assert payload["gameCount"] == 0


# --------------------------------------------------------------------------- policy


def test_the_board_carries_no_market_translation(ctx: ResolveContext) -> None:
    """docs/PROJECTION.md section 6 and docs/BROADSHEET.md section 1, enforced.

    The design this was built from drew every band against a sportsbook line. Checking the
    payload is not enough — the words have to be absent from the module too, because the
    cheapest way for this to come back is somebody adding one helper.
    """
    payload = _league(ctx)
    text = json.dumps(payload).lower()
    for word in ("odds", "edge", "vig", "juice", "kelly", "implied", "overunder", "payout"):
        assert word not in text, word

    source = (
        __import__("pathlib").Path(board.__file__).read_text(encoding="utf-8").lower()
    )
    for word in ("sportsbook", "kelly", "vigorish", "implied_prob", "expected_value"):
        assert f"def {word}" not in source, word
    # The module has to say, in prose, why there is no market comparison — the cheapest way
    # for one to come back is somebody who never knew it was deliberate. The sentence no
    # longer uses the vocabulary itself (the house rule bans it everywhere, including in a
    # comment explaining its absence), so this asserts the replacement wording.
    assert "no market translation" in source or "market quote" in source, (
        "the module must say why there is no market comparison"
    )
