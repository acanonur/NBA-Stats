"""The four league widgets: ``team_matchup``, ``defense_by_position``, ``availability_report`` and
``slate_projections``, from the catalog down to the tile.

What is asserted, and why it is these things
--------------------------------------------
**The catalog is the design's, to the letter.** Twenty kinds, the four last; each has a required
``league`` field (``nba`` or ``euroleague``, default ``nba``); the sizes and refresh intervals of
the design's table; the new ``club`` field type is declared; no config key splits into a word the
market guard forbids. A ``date`` field may widen its accepted words (``slate_projections`` takes
``next``) without changing a word for the fields that never asked: ``scoreboard`` still refuses
``next``.

**The ``club`` type is checked in two steps.** Offline it is three capital letters and nothing
else. Against the store it must exist, but only for a tile whose league is the EuroLeague, and only
when the registry can answer: "cannot say" (no provider, or a check that failed) is not "no".

**A tile is the route.** For the NBA, with the clock held still on both sides, the payload a tile
resolves is equal, key for key, to the payload the matching ``/v1`` route returns. For the
EuroLeague it equals what the EuroLeague's own builder returns for the same arguments. That is the
whole point of a thin resolver, and it is checked rather than assumed.

**The league is the tile's own.** An NBA tile never opens the EuroLeague's store; a EuroLeague tile
never touches the context's NBA session and closes its own. A EuroLeague that is off, not ready or
unreadable is the recoverable per-widget ``league_unavailable`` on that tile, and the tiles beside
it are untouched.

**Honest edges.** No opponent chosen and no game scheduled falls back to the two sides of the team's
most recent game, with a note and ``game: null`` (and with nothing played either, an error on the
``opponent`` field), never an invented opponent. An opponent who hosts is found whichever of the two
is configured as the subject. A club is required for the EuroLeague (there is no favourite club),
and ``$favorite_team`` is not honoured there. A degraded payload is a ``partial`` result with the
payload's own sentences; a projection is ``estimated`` and carries none.

**The dashboard treats them as foreign.** None of the four is ever answered ``unchanged``, whatever
the client's ``knownSyncVersion``, because their freshness is not the NBA's sync version.

**The contract holds together.** ``check_contracts.py`` check (j) tolerates exactly the four kinds
in the iOS ``PENDING.txt`` under a ceiling of four, and refuses a stale entry, an unknown kind and a
fifth; no web ``PENDING.txt`` exists; the generated web registry draws none of the four yet. The
four committed widget fixtures are NBA-configured league payloads with no forbidden key.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from nbastats import catalog, config
from nbastats.accounts import layouts
from nbastats.api import deps, routes_dashboard
from nbastats.api.app import create_app
from nbastats.euroleague import bootstrap, registry_provider
from nbastats.euroleague.db import create_el_engine, dispose_el_engine, init_el_db
from nbastats.euroleague.demo import DEMO_AS_OF, seed_demo
from nbastats.euroleague.read import queries as el_queries
from nbastats.models import Game
from nbastats.nba_matchup import queries as nba_queries
from nbastats.shared import league_registry, market_guard
from nbastats.widgets import RESOLVERS, TTL_SECONDS, ResolveContext, WidgetError
from nbastats.widgets import league_common

ROOT = Path(__file__).resolve().parents[2]
KINDS = ("team_matchup", "defense_by_position", "availability_report", "slate_projections")

#: "Now" for the NBA tiles and routes: a day before the seeded league's next scheduled games.
FROZEN = datetime(2026, 3, 1, 16, 0, tzinfo=timezone.utc)
#: "Now" for the EuroLeague tiles: the invented league's own clock (four rounds behind, one ahead).
EL_NOW = DEMO_AS_OF.replace(tzinfo=timezone.utc)


# =========================================================================== fixtures


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Hold the tiles' clock still, and leave no registry entry or memo from a neighbour."""
    nba_queries.clear_memo()
    el_queries.clear_memo()
    league_registry.clear()
    monkeypatch.setattr(league_common, "utcnow", lambda: FROZEN.replace(tzinfo=None))
    yield
    league_registry.clear()
    nba_queries.clear_memo()
    el_queries.clear_memo()


@contextmanager
def api_client(**environment: str | None) -> Iterator[TestClient]:
    """An app over the seeded league. The EuroLeague is off unless a test registers it itself, so
    nothing the application registers can leak into what a test says about the registry."""
    environment = {"HARDWOOD_EL_ENABLED": "0", "HARDWOOD_API_KEY": None, **environment}
    previous = {key: os.environ.get(key) for key in environment}
    for key, value in environment.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    config.reset_settings_cache()
    deps.reset_rate_limiter()
    try:
        with TestClient(create_app()) as client:
            yield client
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        config.reset_settings_cache()
        deps.reset_rate_limiter()


@pytest.fixture(scope="module")
def app_client(seeded_engine: Engine) -> Iterator[TestClient]:
    with api_client() as client:
        overrides = client.app.dependency_overrides  # type: ignore[attr-defined]
        overrides[nba_queries.get_now] = lambda: FROZEN
        yield client


@pytest.fixture()
def ctx(seeded_db: Session) -> ResolveContext:
    return ResolveContext.from_request(seeded_db, None, request_id="test")


@pytest.fixture(scope="module")
def nba(seeded_engine: Engine) -> SimpleNamespace:
    """Real ids from the seeded league: a scheduled game, strangers, and a closed season."""
    with Session(seeded_engine, future=True) as session:
        scheduled = (
            session.execute(
                select(Game)
                .where(Game.status == "scheduled", Game.season == "2025-26")
                .where(Game.season_type == "Regular Season")
                .order_by(Game.game_date, Game.game_id)
            )
            .scalars()
            .first()
        )
        assert scheduled is not None, "the seeded league has no scheduled game"
        every_scheduled = session.execute(
            select(Game.home_team_id, Game.away_team_id).where(
                Game.status == "scheduled", Game.season == "2025-26"
            )
        ).all()
        pairs = {frozenset(pair) for pair in every_scheduled}
        teams = sorted({t for pair in every_scheduled for t in pair})
        strangers = next(
            (a, b) for a in teams for b in teams if a < b and frozenset((a, b)) not in pairs
        )
        closed = (
            session.execute(
                select(Game)
                .where(Game.status == "final", Game.season == "2024-25")
                .order_by(Game.game_date, Game.game_id)
            )
            .scalars()
            .all()
        )
        last_closed = max(closed, key=lambda g: (g.game_date, g.game_id))
        return SimpleNamespace(
            game_id=scheduled.game_id,
            home=scheduled.home_team_id,
            away=scheduled.away_team_id,
            strangers=strangers,
            closed_team=last_closed.home_team_id,
            closed_pair=(last_closed.home_team_id, last_closed.away_team_id),
        )


@pytest.fixture()
def el(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    """The invented EuroLeague, seeded into its own file and registered the way the application
    registers it, with the tiles' clock on the league's own."""
    url = f"sqlite:///{tmp_path / 'el_widgets.db'}"
    engine = create_el_engine(url)
    init_el_db(engine)
    with Session(engine, future=True) as session:
        seed_demo(session)
        session.commit()
    monkeypatch.setenv("HARDWOOD_EL_DATABASE_URL", url)
    config.reset_settings_cache()
    dispose_el_engine()
    bootstrap.set_state(bootstrap.BootstrapResult(state="ready", kind="synthetic", is_demo=True))
    registry_provider.register()
    monkeypatch.setattr(league_common, "utcnow", lambda: DEMO_AS_OF)
    try:
        yield SimpleNamespace(engine=engine, now=EL_NOW)
    finally:
        registry_provider.unregister()
        bootstrap.reset_state()
        dispose_el_engine()
        engine.dispose()
        config.reset_settings_cache()


def _builders() -> Any:
    return importlib.import_module("nbastats.euroleague.read")


def _el_payload(call: str, **kwargs: Any) -> Any:
    """What the EuroLeague's own builder returns, opened the way a route opens it."""
    with registry_provider.session_factory() as session:
        return getattr(_builders(), call)(session, now=EL_NOW, **kwargs)


def _resolve(client: TestClient, widgets: list[dict[str, Any]], **body: Any) -> dict[str, Any]:
    response = client.post(
        "/v1/dashboard/resolve", json={"layoutId": "test-league", "widgets": widgets, **body}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _one(client: TestClient, kind: str, config_: dict[str, Any], **body: Any) -> dict[str, Any]:
    results = _resolve(
        client, [{"id": "w1", "kind": kind, "size": "large", "config": config_}], **body
    )["results"]
    assert len(results) == 1
    return results[0]


def _payload(client: TestClient, kind: str, config_: dict[str, Any], **body: Any) -> dict[str, Any]:
    result = _one(client, kind, config_, **body)
    assert result["status"] in ("ok", "partial"), result
    assert result["payload"] is not None
    return result["payload"]


def _config(kind: str, **overrides: Any) -> dict[str, Any]:
    """The catalog defaults for ``kind`` plus ``overrides``: what validation hands a resolver."""
    cleaned, errors = catalog.validate_widget_config(
        kind, {**catalog.widget_config_defaults(kind), **overrides}
    )
    assert errors == [], errors
    return cleaned


def _error(result: dict[str, Any], code: str, field: str | None = None) -> dict[str, Any]:
    assert result["status"] == "error", result
    assert result["payload"] is None
    body = result["error"]
    assert body["code"] == code, body
    if field is not None:
        assert body["field"] == field, body
    return body


# =========================================================================== the catalog


def test_the_catalog_has_twenty_kinds_and_the_league_four_come_last() -> None:
    kinds = catalog.widget_kinds()
    assert len(kinds) == 20
    assert tuple(kinds[-4:]) == KINDS
    assert set(RESOLVERS) == set(kinds)


def test_sizes_refresh_and_defaults_are_the_designs() -> None:
    expected = {
        "team_matchup": (["medium", "large"], 600),
        "defense_by_position": (["medium", "large"], 600),
        "availability_report": (["medium", "large"], 300),
        "slate_projections": (["large"], 600),
    }
    for kind, (sizes, refresh) in expected.items():
        spec = catalog.widget(kind)
        assert spec["sizes"] == sizes, kind
        assert spec["defaultSize"] in spec["sizes"], kind
        assert spec["minRefreshSeconds"] == refresh == TTL_SECONDS[kind], kind
        assert spec.get("availableFrom") is None, kind


@pytest.mark.parametrize("kind", KINDS)
def test_every_league_kind_has_a_required_league_field_defaulting_to_the_nba(kind: str) -> None:
    field = next(f for f in catalog.widget(kind)["config"] if f["key"] == "league")
    assert field["type"] == "enum"
    assert field["options"] == ["nba", "euroleague"]
    assert field["default"] == "nba"
    assert field["required"] is True
    assert catalog.widget_config_defaults(kind)["league"] == "nba"
    cleaned, errors = catalog.validate_widget_config(kind, {"league": "premier"})
    assert cleaned["league"] == "nba" and [e.field for e in errors] == ["league"]


def test_the_fields_the_design_lists_are_there_with_its_defaults() -> None:
    fields = {kind: {f["key"]: f for f in catalog.widget(kind)["config"]} for kind in KINDS}
    assert set(fields["team_matchup"]) == {
        "league",
        "team",
        "club",
        "opponent",
        "opponentClub",
        "season",
        "window",
    }
    assert fields["team_matchup"]["team"]["type"] == "team"
    assert fields["team_matchup"]["team"]["default"] == "$favorite_team"
    assert fields["team_matchup"]["club"]["type"] == "club"
    assert fields["team_matchup"]["opponent"]["default"] is None
    assert fields["team_matchup"]["opponentClub"]["type"] == "club"
    window = fields["team_matchup"]["window"]
    assert (window["type"], window["default"], window["min"], window["max"]) == ("int", 5, 3, 15)

    assert set(fields["defense_by_position"]) == {
        "league",
        "team",
        "club",
        "season",
        "window",
        "basis",
        "scheme",
    }
    assert fields["defense_by_position"]["team"]["default"] is None
    assert fields["defense_by_position"]["window"]["default"] == 0
    assert fields["defense_by_position"]["basis"]["options"] == ["perGame", "perMinute"]
    assert fields["defense_by_position"]["scheme"]["options"] == ["gfc", "workbook5"]

    assert set(fields["availability_report"]) == {"league", "team", "club", "includeNews"}
    assert fields["availability_report"]["includeNews"]["type"] == "bool"
    assert fields["availability_report"]["includeNews"]["default"] is False

    assert set(fields["slate_projections"]) == {"league", "date", "round"}
    assert fields["slate_projections"]["date"]["type"] == "date"
    assert fields["slate_projections"]["date"]["default"] == "next"
    assert fields["slate_projections"]["round"]["default"] == 0


def test_the_club_type_is_declared_and_every_field_type_in_use_is_too() -> None:
    declared = set(catalog.widgets_document()["configFieldTypes"])
    assert "club" in declared
    used = {f["type"] for w in catalog.all_widgets() for f in w["config"]}
    assert used <= declared, sorted(used - declared)


@pytest.mark.parametrize("kind", KINDS)
def test_no_config_key_is_market_vocabulary(kind: str) -> None:
    """No league tile can take a number to compare a projection with: its config has no such key."""
    keys = {f["key"] for f in catalog.widget(kind)["config"]}
    assert market_guard.scan_keys({key: None for key in keys}) == []


# =========================================================================== validation


def test_a_club_is_trimmed_upper_cased_and_three_letters() -> None:
    cleaned, errors = catalog.validate_widget_config("team_matchup", {"club": " pan "})
    assert cleaned["club"] == "PAN" and errors == []
    for bad in ("PANA", "PA", "P1N", "", "P N", 7, ["PAN"], True):
        cleaned, errors = catalog.validate_widget_config("team_matchup", {"club": bad})
        assert cleaned["club"] is None, bad
        assert [e.field for e in errors] == ["club"], bad
        assert "three-letter" in errors[0].message


def test_an_explicit_null_club_is_the_same_as_none_chosen() -> None:
    cleaned, errors = catalog.validate_widget_config(
        "team_matchup", {"club": None, "opponentClub": None}
    )
    assert cleaned["club"] is None and cleaned["opponentClub"] is None and errors == []


def _provider(answer: Any) -> league_registry.LeagueProvider:
    return league_registry.LeagueProvider(
        key="euroleague",
        session_factory=lambda: (_ for _ in ()).throw(AssertionError("no session for a check")),
        describe=lambda: {},
        team_exists=answer,
    )


def test_a_club_the_registry_denies_is_a_config_error_for_a_euroleague_tile() -> None:
    league_registry.register(_provider(lambda code: code in {"ZZA", "ZZB"}))
    cleaned, errors = catalog.validate_widget_config(
        "team_matchup", {"league": "euroleague", "club": "zzb", "opponentClub": "ZZQ"}
    )
    assert cleaned["club"] == "ZZB"
    assert cleaned["opponentClub"] is None  # falls back to the default, like any invalid value
    assert [(e.field, "ZZQ" in e.message) for e in errors] == [("opponentClub", True)]


def test_the_existence_check_is_skipped_for_an_nba_tile() -> None:
    """A club left over from before the reader switched leagues cannot make an NBA tile invalid."""
    league_registry.register(_provider(lambda code: False))
    cleaned, errors = catalog.validate_widget_config(
        "team_matchup", {"league": "nba", "club": "ZZZ"}
    )
    assert cleaned["club"] == "ZZZ" and errors == []


@pytest.mark.parametrize("cannot_say", ["no provider", "no checker", "checker raises"])
def test_cannot_say_is_not_no(cannot_say: str) -> None:
    """With the EuroLeague off or unable to check, the club is kept: the resolver, not the
    validator, reports the recoverable ``league_unavailable``."""
    if cannot_say == "no checker":
        league_registry.register(_provider(None))
    elif cannot_say == "checker raises":

        def broken(code: str) -> bool:
            raise RuntimeError("the store is locked")

        league_registry.register(_provider(broken))
    cleaned, errors = catalog.validate_widget_config(
        "team_matchup", {"league": "euroleague", "club": "ZZA"}
    )
    assert cleaned["club"] == "ZZA" and errors == []


def test_a_date_field_may_widen_its_words_without_changing_anyone_elses() -> None:
    for word in ("next", "latest", "2026-03-04"):
        cleaned, errors = catalog.validate_widget_config("slate_projections", {"date": word})
        assert cleaned["date"] == word and errors == [], word
    cleaned, errors = catalog.validate_widget_config("slate_projections", {"date": "tomorrow"})
    assert cleaned["date"] == "next"
    assert errors[0].message == "expected an ISO date, 'latest' or 'next', got 'tomorrow'"
    # The sixteen older kinds took ``latest`` only, and still say so in the same words.
    for kind in ("scoreboard", "daily_movers", "projection_board"):
        cleaned, errors = catalog.validate_widget_config(kind, {"date": "next"})
        assert [e.field for e in errors] == ["date"], kind
        assert errors[0].message == "expected an ISO date or 'latest', got 'next'", kind


# =========================================================================== the catalog on disk


def _load_check_contracts() -> Any:
    spec = importlib.util.spec_from_file_location(
        "hardwood_check_contracts_for_widget_tests", ROOT / "scripts" / "check_contracts.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pascal(kind: str) -> str:
    return "".join(part.capitalize() for part in kind.split("_"))


def _ios_tree(tmp_path: Path, *, without: tuple[str, ...], pending: list[str] | None) -> Path:
    """A stand-in ``ios/NBAStats/Widgets``: a Swift file for every kind but ``without``."""
    directory = tmp_path / "Widgets"
    directory.mkdir()
    for kind in catalog.widget_kinds():
        if kind not in without:
            (directory / f"{_pascal(kind)}Widget.swift").write_text("// stub\n")
    if pending is not None:
        (directory / "PENDING.txt").write_text("# comment\n\n" + "\n".join(pending) + "\n")
    return directory


def test_the_ios_pending_file_names_exactly_the_four_and_the_web_has_none() -> None:
    names = {
        line.strip()
        for line in (ROOT / "ios/NBAStats/Widgets/PENDING.txt").read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    assert names == set(KINDS)
    # The web draws none of the four and says so through the filesystem, not a list: the web
    # ratchet reads directories, and four missing is inside its ceiling of twelve.
    assert not (ROOT / "web/src/widgets/PENDING.txt").exists()
    for kind in KINDS:
        assert not (ROOT / "web/src/widgets" / kind).exists(), kind


def test_check_j_passes_on_the_real_tree() -> None:
    summary = _load_check_contracts().check_widget_kinds_agree()
    assert summary.startswith("20 widget kinds agree")
    assert "pending on iOS: availability_report, defense_by_position" in summary


def test_check_j_tolerates_the_four_under_the_ceiling(tmp_path: Path) -> None:
    module = _load_check_contracts()
    module.IOS_WIDGETS = _ios_tree(tmp_path, without=KINDS, pending=list(KINDS))
    assert module.check_widget_kinds_agree().startswith("20 widget kinds agree")


def test_check_j_refuses_a_missing_swift_widget_that_is_not_pending(tmp_path: Path) -> None:
    module = _load_check_contracts()
    module.IOS_WIDGETS = _ios_tree(tmp_path, without=KINDS, pending=None)
    with pytest.raises(module.CheckFailure) as caught:
        module.check_widget_kinds_agree()
    assert "not tolerated as pending" in " ".join(caught.value.details)
    for kind in KINDS:
        assert kind in " ".join(caught.value.details)


def test_check_j_refuses_a_stale_pending_entry(tmp_path: Path) -> None:
    """A kind that already has its Swift widget must come off the list."""
    module = _load_check_contracts()
    module.IOS_WIDGETS = _ios_tree(tmp_path, without=KINDS[1:], pending=list(KINDS))
    with pytest.raises(module.CheckFailure) as caught:
        module.check_widget_kinds_agree()
    assert "already have a *Widget.swift" in " ".join(caught.value.details)
    assert "team_matchup" in " ".join(caught.value.details)


def test_check_j_refuses_an_unknown_pending_kind_and_a_fifth(tmp_path: Path) -> None:
    module = _load_check_contracts()
    module.IOS_WIDGETS = _ios_tree(tmp_path, without=KINDS, pending=[*KINDS, "teleporter"])
    with pytest.raises(module.CheckFailure) as caught:
        module.check_widget_kinds_agree()
    joined = " ".join(caught.value.details)
    assert "teleporter" in joined and "more than the 4" in joined

    fifth = tmp_path / "fifth"
    fifth.mkdir()
    module.IOS_WIDGETS = _ios_tree(
        fifth, without=(*KINDS, "career_arc"), pending=[*KINDS, "career_arc"]
    )
    with pytest.raises(module.CheckFailure) as caught:
        module.check_widget_kinds_agree()
    assert "more than the 4" in " ".join(caught.value.details)


def test_the_generated_web_registry_draws_none_of_the_four_yet() -> None:
    text = (ROOT / "web/src/generated/registry.ts").read_text()
    for kind in KINDS:
        assert re.search(rf"^\s*{kind}: \{{ component: null,", text, re.M), kind


def test_the_committed_widget_fixtures_are_nba_league_payloads_with_no_forbidden_key() -> None:
    for kind in KINDS:
        document = json.loads((ROOT / f"contracts/fixtures/widget_{kind}.json").read_text())
        assert document["league"] == "nba", kind
        assert document["freshness"]["league"] == "nba", kind
        assert document["freshness"]["isDemo"] is True, kind
        assert market_guard.scan_keys(document) == [], kind


def test_the_widget_config_keys_survive_a_layout_save() -> None:
    """The league lives in the widget's own config, because the layout migrators keep declared
    config keys and drop the rest (so a layout-level ``league`` would not survive)."""
    raw = {
        "id": "dash-1",
        "name": "League tiles",
        "schemaVersion": 1,
        "widgets": [
            {
                "id": "w1",
                "kind": "team_matchup",
                "size": "large",
                "config": {"league": "euroleague", "club": "ZZA", "bogus": 1},
            }
        ],
        "league": "euroleague",
    }
    result = layouts.migrate_and_normalize(raw)
    widget = result.layout["widgets"][0]
    assert widget["config"]["league"] == "euroleague"
    assert widget["config"]["club"] == "ZZA"
    assert "bogus" not in widget["config"]
    assert "league" not in result.layout  # the layout-level key is dropped, as the design says


# =========================================================================== the NBA tiles


@pytest.mark.parametrize("kind", KINDS)
def test_every_league_kind_resolves_from_its_defaults(kind: str, ctx: ResolveContext) -> None:
    payload, availability, notes = RESOLVERS[kind](catalog.widget_config_defaults(kind), ctx)
    assert payload["league"] == "nba"
    assert availability in ("full", "estimated", "partial", "unavailable")
    assert all(isinstance(note, str) and note for note in notes)
    assert (notes == []) == (availability in ("full", "estimated")), (availability, notes)
    assert market_guard.scan_keys(payload) == []


def test_a_matchup_tile_is_the_matchup_route(app_client: TestClient, nba: SimpleNamespace) -> None:
    tile = _payload(app_client, "team_matchup", {"league": "nba", "team": nba.home})
    route = app_client.get(f"/v1/teams/{nba.home}/matchup")
    assert route.status_code == 200
    assert tile == route.json()
    assert tile["game"]["gameId"] == nba.game_id
    assert [side["side"] for side in tile["teams"]] == ["home", "away"]


def test_a_matchup_tile_takes_a_window_and_a_season(
    app_client: TestClient, nba: SimpleNamespace
) -> None:
    tile = _payload(
        app_client, "team_matchup", {"team": nba.home, "window": 3, "season": "2025-26"}
    )
    route = app_client.get(f"/v1/teams/{nba.home}/matchup", params={"window": 3}).json()
    assert tile == route
    assert all(len(side["form"]) <= 3 for side in tile["teams"])
    assert all(side["lastN"]["window"] == 3 for side in tile["teams"])


def test_the_favorite_team_token_resolves_and_is_recorded(
    app_client: TestClient, nba: SimpleNamespace
) -> None:
    body = _resolve(
        app_client,
        [{"id": "w1", "kind": "team_matchup", "size": "large", "config": {}}],
        context={"favoriteTeamId": nba.away},
    )
    assert body["resolvedContext"]["favoriteTeamId"] == nba.away
    payload = body["results"][0]["payload"]
    assert nba.away in {side["team"]["teamId"] for side in payload["teams"]}


@pytest.mark.parametrize("subject_is_host", [True, False])
def test_the_opponent_may_be_chosen_from_either_side(
    app_client: TestClient, nba: SimpleNamespace, subject_is_host: bool
) -> None:
    team, opponent = (nba.home, nba.away) if subject_is_host else (nba.away, nba.home)
    tile = _payload(app_client, "team_matchup", {"team": team, "opponent": opponent})
    assert tile["game"] is not None and tile["game"]["gameId"] == nba.game_id
    assert tile["projection"] is not None
    # The game decides who hosts, not which of the two is the tile's subject.
    assert [side["team"]["teamId"] for side in tile["teams"]] == [nba.home, nba.away]


def test_two_teams_that_never_meet_are_compared_with_no_game_and_no_projection(
    app_client: TestClient, nba: SimpleNamespace
) -> None:
    team, opponent = nba.strangers
    tile = _payload(app_client, "team_matchup", {"team": team, "opponent": opponent})
    assert tile["game"] is None and tile["projection"] is None
    assert [side["team"]["teamId"] for side in tile["teams"]] == [team, opponent]
    assert any("No game is scheduled between these teams" in note for note in tile["notes"])


def test_with_nothing_scheduled_the_tile_compares_the_sides_of_the_last_game_and_says_so(
    app_client: TestClient, nba: SimpleNamespace
) -> None:
    """The closed 2024-25 season has no scheduled game: no error tile for the whole summer, and no
    invented opponent either."""
    result = _one(app_client, "team_matchup", {"team": nba.closed_team, "season": "2024-25"})
    assert result["status"] == "partial"
    tile = result["payload"]
    assert tile["game"] is None and tile["projection"] is None
    assert tile["season"] == "2024-25"
    assert [side["team"]["teamId"] for side in tile["teams"]] == list(nba.closed_pair)
    assert any("No game is scheduled for this team" in note for note in tile["notes"])
    assert result["availability"] in ("partial", "full")


def test_a_team_that_does_not_exist_is_a_per_tile_error_on_the_team_field(
    app_client: TestClient,
) -> None:
    _error(_one(app_client, "team_matchup", {"team": 99999999}), "team_not_found", "team")
    _error(_one(app_client, "availability_report", {"team": 99999999}), "team_not_found", "team")
    _error(_one(app_client, "defense_by_position", {"team": 99999999}), "team_not_found", "team")


def test_a_team_cannot_be_its_own_opponent(app_client: TestClient, nba: SimpleNamespace) -> None:
    _error(
        _one(app_client, "team_matchup", {"team": nba.home, "opponent": nba.home}),
        "invalid_config",
        "opponent",
    )


def test_an_unknown_season_is_a_recoverable_tile_error(app_client: TestClient) -> None:
    body = _error(_one(app_client, "team_matchup", {"season": "1850-51"}), "season_not_loaded")
    assert body["recoverable"] is True


def test_a_club_on_an_nba_tile_is_ignored(app_client: TestClient, nba: SimpleNamespace) -> None:
    tile = _payload(
        app_client,
        "team_matchup",
        {"team": nba.home, "club": "ZZA", "opponentClub": "ZZB"},
    )
    assert tile["league"] == "nba" and tile["game"]["gameId"] == nba.game_id


def test_a_defence_tile_is_the_team_route_and_the_league_table_route(
    app_client: TestClient, nba: SimpleNamespace
) -> None:
    team = _payload(app_client, "defense_by_position", {"team": nba.home})
    route = app_client.get(f"/v1/teams/{nba.home}/defense-by-position", params={"window": 0})
    assert team == route.json()
    assert "team" in team and "teams" not in team

    table = _payload(app_client, "defense_by_position", {"team": None})
    assert table == app_client.get("/v1/defense-by-position", params={"window": 0}).json()
    assert "teams" in table and "team" not in table
    assert all("rank" not in team_row for team_row in table["teams"])


def test_a_defence_tile_passes_its_window_and_basis_on(
    app_client: TestClient, nba: SimpleNamespace
) -> None:
    tile = _payload(
        app_client, "defense_by_position", {"team": nba.home, "window": 3, "basis": "perMinute"}
    )
    assert tile["basis"] == "perMinute"
    assert tile["window"]["kind"] == "lastGames" and tile["window"]["requested"] == 3
    route = app_client.get(
        f"/v1/teams/{nba.home}/defense-by-position", params={"window": 3, "basis": "perMinute"}
    )
    assert tile == route.json()


def test_the_five_position_scheme_is_not_the_nbas_and_the_tile_says_so(
    app_client: TestClient, nba: SimpleNamespace
) -> None:
    result = _one(app_client, "defense_by_position", {"team": nba.home, "scheme": "workbook5"})
    assert result["status"] == "partial"
    assert result["notes"][0].startswith("The NBA publishes guard, forward and center only")
    positions = {bucket["position"] for bucket in result["payload"]["buckets"]}
    assert positions <= {"G", "F", "C", "unknown"}
    assert result["payload"]["scheme"] == "gfc"


def test_an_availability_tile_is_the_availability_route(
    app_client: TestClient, nba: SimpleNamespace
) -> None:
    slate = _payload(app_client, "availability_report", {"team": None})
    assert slate == app_client.get("/v1/availability").json()
    team = _payload(app_client, "availability_report", {"team": nba.home})
    assert team == app_client.get("/v1/availability", params={"teamId": nba.home}).json()


def test_the_demo_league_refuses_statuses_and_the_tile_says_why_instead_of_guessing(
    app_client: TestClient,
) -> None:
    result = _one(app_client, "availability_report", {})
    assert result["status"] == "partial"
    assert result["availability"] == "unavailable"
    payload = result["payload"]
    assert payload["state"] == "disabled"
    assert result["notes"] == [payload["message"]]
    assert all(not team["entries"] for team in payload["teams"])
    assert payload["freshness"]["isDemo"] is True


def test_headlines_are_only_added_when_asked(app_client: TestClient) -> None:
    without = _payload(app_client, "availability_report", {"includeNews": False})
    with_news = _payload(app_client, "availability_report", {"includeNews": True})
    assert without["news"] is None
    assert isinstance(with_news["news"], list)


def test_a_slate_tile_is_the_projections_route(app_client: TestClient) -> None:
    tile = _payload(app_client, "slate_projections", {"date": "next"})
    assert tile == app_client.get("/v1/projections", params={"date": "next"}).json()
    assert tile["games"] and tile["date"] is not None and tile["round"] is None
    for game in tile["games"]:
        assert "probability" not in json.dumps(game).lower()
        assert game["availability"] == "estimated"


def test_a_projected_slate_is_an_estimate_and_carries_no_result_notes(
    app_client: TestClient,
) -> None:
    result = _one(app_client, "slate_projections", {"date": "next"})
    assert result["status"] == "ok" and result["availability"] == "estimated"
    assert result["notes"] == []


def test_a_slate_can_be_asked_for_by_token_or_by_date(app_client: TestClient) -> None:
    latest = _payload(app_client, "slate_projections", {"date": "latest"})
    route = app_client.get("/v1/projections", params={"date": "latest"}).json()
    assert latest == route
    day = latest["date"]
    explicit = _payload(app_client, "slate_projections", {"date": day})
    assert explicit == app_client.get("/v1/projections", params={"date": day}).json()


def test_a_day_with_no_games_is_an_unavailable_slate_with_a_reason(
    app_client: TestClient,
) -> None:
    result = _one(app_client, "slate_projections", {"date": "2025-07-04"})
    assert result["status"] == "partial" and result["availability"] == "unavailable"
    assert result["payload"]["games"] == []
    assert result["notes"] and "No games are scheduled" in result["notes"][0]


def test_a_bad_league_is_the_tiles_invalid_config(app_client: TestClient) -> None:
    _error(_one(app_client, "team_matchup", {"league": "premier"}), "invalid_config", "league")
    _error(_one(app_client, "slate_projections", {"date": "tomorrow"}), "invalid_config", "date")


# =========================================================================== the EuroLeague tiles


def test_a_euroleague_matchup_tile_is_the_euroleague_builders_payload(
    app_client: TestClient, el: SimpleNamespace
) -> None:
    tile = _payload(app_client, "team_matchup", {"league": "euroleague", "club": "ZZA"})
    assert tile == _el_payload("team_matchup", club="ZZA")
    assert tile["league"] == "euroleague"
    assert tile["seasonCode"] == "E2026" and tile["freshness"]["isDemo"] is True
    assert market_guard.scan_keys(tile) == []


def test_a_euroleague_club_with_a_chosen_opponent_is_found_from_either_side(
    app_client: TestClient, el: SimpleNamespace
) -> None:
    game = _el_payload("team_matchup", game_id="E2026-R05-01")["game"]
    home, away = game["home"]["id"], game["away"]["id"]
    for club, opponent in ((home, away), (away, home)):
        tile = _payload(
            app_client,
            "team_matchup",
            {"league": "euroleague", "club": club, "opponentClub": opponent},
        )
        assert tile["game"]["gameId"] == game["gameId"]
        assert [side["team"]["id"] for side in tile["teams"]] == [home, away]


def test_a_euroleague_tile_needs_a_club_and_never_uses_the_favourite_team(
    app_client: TestClient, nba: SimpleNamespace, el: SimpleNamespace
) -> None:
    result = _one(
        app_client,
        "team_matchup",
        {"league": "euroleague"},
        context={"favoriteTeamId": nba.home},
    )
    body = _error(result, "invalid_config", "club")
    assert "Choose a club" in body["message"]


def test_a_club_that_is_not_in_the_store_is_refused_before_the_tile_resolves(
    app_client: TestClient, el: SimpleNamespace
) -> None:
    result = _one(app_client, "team_matchup", {"league": "euroleague", "club": "ZZZ"})
    body = _error(result, "invalid_config", "club")
    assert "ZZZ" in body["message"]


def test_a_euroleague_club_with_nothing_scheduled_falls_back_to_its_last_game(
    app_client: TestClient, el: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Held a year on, every Round 5 game is long past its result: nothing is scheduled."""
    monkeypatch.setattr(league_common, "utcnow", lambda: DEMO_AS_OF + timedelta(days=400))
    result = _one(app_client, "team_matchup", {"league": "euroleague", "club": "ZZA"})
    assert result["status"] in ("ok", "partial"), result
    tile = result["payload"]
    assert tile["game"] is None and tile["projection"] is None
    assert any("No game is scheduled for this club" in note for note in tile["notes"])
    assert {side["team"]["id"] for side in tile["teams"]} >= {"ZZA"}


def test_a_euroleague_defence_tile_is_the_builders_payload_for_a_club_and_for_the_table(
    app_client: TestClient, el: SimpleNamespace
) -> None:
    club = _payload(app_client, "defense_by_position", {"league": "euroleague", "club": "ZZA"})
    assert club == _el_payload("defense_by_position", club="ZZA", window=0, basis="perGame")
    assert club["league"] == "euroleague" and club["scheme"] == "gfc"

    table = _payload(
        app_client,
        "defense_by_position",
        {"league": "euroleague", "club": None, "scheme": "workbook5", "basis": "perMinute"},
    )
    assert table == _el_payload(
        "defense_by_position", club=None, window=0, basis="perMinute", scheme="workbook5"
    )
    assert table["scheme"] == "workbook5" and "teams" in table


def test_a_euroleague_availability_tile_is_the_builders_payload(
    app_client: TestClient, el: SimpleNamespace
) -> None:
    everyone = _one(app_client, "availability_report", {"league": "euroleague"})
    assert everyone["payload"] == _el_payload("availability_report", club=None)
    assert everyone["availability"] == "full" and everyone["notes"] == []
    club = _payload(app_client, "availability_report", {"league": "euroleague", "club": "ZZP"})
    assert club == _el_payload("availability_report", club="ZZP")
    with_news = _payload(
        app_client, "availability_report", {"league": "euroleague", "includeNews": True}
    )
    assert with_news == _el_payload("availability_report", club=None, include_news=True)


def test_a_euroleague_slate_tile_is_the_builders_payload_by_round(
    app_client: TestClient, el: SimpleNamespace
) -> None:
    next_round = _one(app_client, "slate_projections", {"league": "euroleague", "round": 0})
    assert next_round["payload"] == _el_payload("slate_projections", round_number="next")
    assert next_round["payload"]["round"] == 5 and next_round["payload"]["date"] is None
    assert next_round["availability"] == "estimated" and next_round["notes"] == []
    fourth = _payload(app_client, "slate_projections", {"league": "euroleague", "round": 4})
    assert fourth == _el_payload("slate_projections", round_number=4)
    assert fourth["round"] == 4


def test_a_euroleague_tile_never_touches_the_nba_session_and_closes_its_own(
    el: SimpleNamespace,
) -> None:
    opened: list[Any] = []

    class Tracked(Session):
        closed = False

        def close(self) -> None:
            Tracked.closed = True
            super().close()

    def factory() -> Session:
        session = Tracked(el.engine, future=True)
        opened.append(session)
        return session

    league_registry.register(
        league_registry.LeagueProvider(
            key="euroleague",
            session_factory=factory,
            describe=lambda: {},
            team_exists=lambda code: True,
            read_side=registry_provider.read_side,
        )
    )

    class Untouchable:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"a EuroLeague tile reached into the NBA session ({name})")

    ctx = ResolveContext(
        session=Untouchable(),  # type: ignore[arg-type]
        settings=config.get_settings(),
        season="2025-26",
        favorite_team_id=1610612747,
    )
    for kind, overrides in (
        ("team_matchup", {"club": "ZZA"}),
        ("defense_by_position", {"club": "ZZA"}),
        ("availability_report", {}),
        ("slate_projections", {}),
    ):
        opened.clear()
        Tracked.closed = False
        payload, _availability, _notes = RESOLVERS[kind](
            _config(kind, league="euroleague", **overrides), ctx
        )
        assert payload["league"] == "euroleague", kind
        assert len(opened) == 1 and Tracked.closed, kind


def test_an_nba_tile_never_opens_the_euroleague(
    app_client: TestClient, nba: SimpleNamespace
) -> None:
    def refuse() -> Any:
        raise AssertionError("an NBA tile opened the EuroLeague's store")

    league_registry.register(
        league_registry.LeagueProvider(
            key="euroleague", session_factory=refuse, describe=lambda: {}, team_exists=None
        )
    )
    for kind, overrides in (
        ("team_matchup", {"team": nba.home}),
        ("defense_by_position", {"team": nba.home}),
        ("availability_report", {}),
        ("slate_projections", {}),
    ):
        assert _payload(app_client, kind, {"league": "nba", **overrides})["league"] == "nba"


# --- a league that cannot answer: one tile, never the dashboard


@pytest.mark.parametrize("kind", KINDS)
def test_a_euroleague_that_is_not_registered_is_a_recoverable_league_unavailable(
    app_client: TestClient, kind: str
) -> None:
    # No club needed: with the league off, "choose a club" would be the wrong thing to say.
    result = _one(app_client, kind, {"league": "euroleague"})
    body = _error(result, "league_unavailable", "league")
    assert body["recoverable"] is True
    assert "not available" in body["message"]


def test_one_unavailable_league_tile_leaves_the_tiles_beside_it_standing(
    app_client: TestClient, nba: SimpleNamespace
) -> None:
    results = _resolve(
        app_client,
        [
            {
                "id": "a",
                "kind": "team_matchup",
                "size": "large",
                "config": {"league": "nba", "team": nba.home},
            },
            {
                "id": "b",
                "kind": "team_matchup",
                "size": "large",
                "config": {"league": "euroleague", "club": "ZZA"},
            },
            {"id": "c", "kind": "slate_projections", "size": "large", "config": {}},
        ],
    )["results"]
    assert [r["status"] for r in results] == ["ok", "error", "ok"]
    assert results[1]["error"]["code"] == "league_unavailable"


def test_a_euroleague_that_is_not_ready_says_why(
    app_client: TestClient, el: SimpleNamespace
) -> None:
    bootstrap.set_state(
        bootstrap.BootstrapResult(
            state="misconfigured", reason="the two stores are the same database"
        )
    )
    body = _error(
        _one(app_client, "availability_report", {"league": "euroleague"}),
        "league_unavailable",
        "league",
    )
    assert body["recoverable"] is True
    assert "the two stores are the same database" in body["message"]


def test_a_read_side_that_cannot_be_loaded_fails_closed(
    app_client: TestClient, el: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(module: str = "read") -> Any:
        raise ImportError("the read side is missing")

    monkeypatch.setattr(registry_provider, "read_side", broken)
    registry_provider.register()  # the provider now hands out the broken read side
    body = _error(
        _one(app_client, "slate_projections", {"league": "euroleague"}),
        "league_unavailable",
        "league",
    )
    assert "could not be loaded" in body["message"]


def test_the_widget_error_a_resolver_raises_directly_is_the_same(ctx: ResolveContext) -> None:
    with pytest.raises(WidgetError) as caught:
        RESOLVERS["slate_projections"](_config("slate_projections", league="euroleague"), ctx)
    assert caught.value.code == "league_unavailable"
    assert caught.value.recoverable is True and caught.value.field == "league"


# =========================================================================== the result's shape


@pytest.mark.parametrize(
    ("state", "availability"),
    [
        ("fresh", "full"),
        ("stale", "partial"),
        ("noReportYet", "unavailable"),
        ("unreadable", "unavailable"),
        ("disabled", "unavailable"),
        (None, "unavailable"),
    ],
)
def test_a_reports_state_is_the_availability_its_tile_advertises(
    state: str | None, availability: str
) -> None:
    assert league_common.state_availability(state) == availability


def test_a_result_carries_notes_only_when_it_is_degraded() -> None:
    payload = {"availability": "full", "notes": ["method commentary"]}
    assert league_common.outcome(payload) == (payload, "full", [])
    assert league_common.outcome({"availability": "estimated", "notes": ["an estimate"]})[2] == []

    degraded = {"availability": "partial", "notes": ["a window is short", "", 7]}
    _payload_back, availability, notes = league_common.outcome(degraded)
    assert availability == "partial" and notes == ["a window is short"]  # only real sentences

    _p, availability, notes = league_common.outcome({"availability": "unavailable", "notes": []})
    assert availability == "unavailable" and len(notes) == 1  # never silent about being degraded

    # An availability the contract does not define is not passed on.
    assert league_common.outcome({"availability": "bogus"})[1:] == ("full", [])
    # The caller's own availability and notes win over the payload's.
    assert league_common.outcome(
        {"availability": "full", "notes": ["ignored"]}, availability="partial", notes=["mine"]
    )[1:] == ("partial", ["mine"])


# =========================================================================== the dashboard


def test_the_league_kinds_are_never_answered_unchanged(
    app_client: TestClient, nba: SimpleNamespace
) -> None:
    assert routes_dashboard._FOREIGN_CURSOR_KINDS == frozenset(KINDS)
    assert not routes_dashboard._FOREIGN_CURSOR_KINDS & routes_dashboard._CLOCK_DEPENDENT_KINDS
    widgets = [
        {"id": "s", "kind": "scoreboard", "size": "large", "config": {}},
        {
            "id": "t",
            "kind": "stat_tile",
            "size": "small",
            "config": {"subjectType": "team", "subjectId": nba.home},
        },
        *({"id": kind, "kind": kind, "size": "large", "config": {}} for kind in KINDS),
    ]
    first = _resolve(app_client, widgets)
    caught_up = _resolve(app_client, widgets, knownSyncVersion=first["syncVersion"])
    statuses = {r["widgetId"]: r["status"] for r in caught_up["results"]}
    assert statuses["t"] == "unchanged"  # an ordinary kind: the NBA's cursor is its freshness
    for kind in KINDS:
        assert statuses[kind] in ("ok", "partial"), (kind, statuses[kind])
        result = next(r for r in caught_up["results"] if r["widgetId"] == kind)
        assert result["payload"] is not None


def test_each_result_carries_the_catalogs_refresh_interval(app_client: TestClient) -> None:
    results = _resolve(
        app_client,
        [{"id": kind, "kind": kind, "size": "large", "config": {}} for kind in KINDS],
    )["results"]
    assert {r["kind"]: r["ttlSeconds"] for r in results} == {
        "team_matchup": 600,
        "defense_by_position": 600,
        "availability_report": 300,
        "slate_projections": 600,
    }


def test_the_resolve_fixture_has_one_tile_per_kind_inside_the_request_cap() -> None:
    from nbastats import fixtures_export

    request = fixtures_export._resolve_request(
        fixtures_export.Subjects(player_id=1, team_id=2, era_player_id=3)
    )
    assert len(request["widgets"]) <= 24
    assert [w["kind"] for w in request["widgets"][:20]] == catalog.widget_kinds()
    for kind in KINDS:
        assert fixtures_export.WIDGET_CONFIGS[kind]["league"] == "nba"
        cleaned, errors = catalog.validate_widget_config(kind, fixtures_export.WIDGET_CONFIGS[kind])
        assert errors == [], (kind, errors)


# =========================================================================== no market vocabulary


@pytest.mark.parametrize("kind", KINDS)
def test_no_tile_payload_carries_a_forbidden_key_or_a_probability_of_winning(
    app_client: TestClient, kind: str, nba: SimpleNamespace, el: SimpleNamespace
) -> None:
    overrides = {"team": nba.home} if kind in ("team_matchup", "defense_by_position") else {}
    for league, extra in (("nba", overrides), ("euroleague", {"club": "ZZA"})):
        result = _one(app_client, kind, {"league": league, **extra})
        assert result["status"] in ("ok", "partial"), (kind, league, result)
        payload = result["payload"]
        assert market_guard.scan_keys(payload) == [], (kind, league)
        text = json.dumps(payload).lower()
        for word in ("winprobability", "probabilityofwinning", "moneyline", "pover"):
            assert word not in text, (kind, league, word)


def test_a_provider_with_no_read_side_is_league_unavailable(ctx: ResolveContext) -> None:
    """The widget layer reaches the EuroLeague's builders only through its provider; a provider
    that offers none is the recoverable league_unavailable, never an import."""
    league_registry.register(
        league_registry.LeagueProvider(
            key="euroleague",
            session_factory=lambda: (_ for _ in ()).throw(AssertionError("no session")),
            describe=lambda: {},
        )
    )
    with pytest.raises(WidgetError) as caught:
        league_common.read_module("euroleague")
    assert caught.value.code == "league_unavailable" and "no read side" in caught.value.message
