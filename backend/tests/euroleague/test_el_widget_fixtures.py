"""The four EuroLeague tile payload fixtures are what the tiles really resolve (BD-14).

``contracts/fixtures/leagues/el/widget_<kind>.json`` exist so the Mac app's demo mode can show a
EuroLeague tile from a recorded body, and so its decoding tests have one real payload per kind
(``team_matchup``, ``defense_by_position``, ``availability_report``, ``slate_projections``).
A fixture that the tile resolver would not actually produce is worse than none: the app would
decode a shape the server never sends. So the exporter builds them with the builders' own
arguments (:data:`nbastats.euroleague.fixtures_export.WIDGET_CONFIGS`), and this file closes the
loop by resolving those same tile configurations through the **real** dashboard route, on a
registered invented league at the fixtures' own clock, and demanding byte-for-byte equality of the
payload with what is committed.

Also pinned here: the set of files (nothing but the four beside the route fixtures), the league
tag, the absence of any forbidden key word, and that no fixture name can collide in the iOS bundle
once the sync script has prefixed it (``league_el_<name>.json``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from nbastats import catalog, config
from nbastats import db as stats_db
from nbastats.api import deps
from nbastats.api.app import create_app
from nbastats.euroleague import bootstrap, fixtures_export, registry_provider
from nbastats.euroleague.db import create_el_engine, dispose_el_engine, init_el_db
from nbastats.euroleague.demo import DEMO_AS_OF, seed_demo
from nbastats.euroleague.read import queries as el_queries
from nbastats.nba_matchup import queries as nba_queries
from nbastats.shared import league_registry
from nbastats.shared.market_guard import scan_keys
from nbastats.widgets import league_common

KINDS = ("availability_report", "defense_by_position", "slate_projections", "team_matchup")
LEAGUE_FIXTURES = fixtures_export.FIXTURE_DIR


@pytest.fixture()
def tiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The whole application over an empty NBA store, with the invented EuroLeague registered the
    way the application registers it and the tiles' clock on the league's own."""
    el_url = f"sqlite:///{tmp_path / 'el_widget_fixtures.db'}"
    engine = create_el_engine(el_url)
    init_el_db(engine)
    with Session(engine, future=True) as session:
        seed_demo(session)
        session.commit()
    monkeypatch.setenv("HARDWOOD_EL_DATABASE_URL", el_url)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'nba.db'}")
    monkeypatch.setenv("HARDWOOD_EL_ENABLED", "0")  # the application does not register it itself
    monkeypatch.delenv("HARDWOOD_API_KEY", raising=False)
    monkeypatch.setenv("HARDWOOD_RATE_LIMIT", "0")
    config.reset_settings_cache()
    stats_db.dispose_engine()
    dispose_el_engine()
    nba_queries.clear_memo()
    el_queries.clear_memo()
    league_registry.clear()
    bootstrap.set_state(bootstrap.BootstrapResult(state="ready", kind="synthetic", is_demo=True))
    registry_provider.register()
    monkeypatch.setattr(league_common, "utcnow", lambda: DEMO_AS_OF)
    deps.reset_rate_limiter()
    try:
        with TestClient(create_app()) as client:
            yield client
    finally:
        registry_provider.unregister()
        bootstrap.reset_state()
        league_registry.clear()
        nba_queries.clear_memo()
        el_queries.clear_memo()
        dispose_el_engine()
        stats_db.dispose_engine()
        engine.dispose()
        config.reset_settings_cache()
        deps.reset_rate_limiter()


def _committed(kind: str) -> Any:
    return json.loads((LEAGUE_FIXTURES / f"widget_{kind}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("kind", KINDS)
def test_the_committed_tile_fixture_is_what_the_real_tile_resolves(
    tiles: TestClient, kind: str
) -> None:
    cleaned, errors = catalog.validate_widget_config(kind, fixtures_export.WIDGET_CONFIGS[kind])
    assert errors == [], errors
    response = tiles.post(
        "/v1/dashboard/resolve",
        json={
            "layoutId": "el-widget-fixtures",
            "widgets": [{"id": "w1", "kind": kind, "size": "large", "config": cleaned}],
        },
    )
    assert response.status_code == 200, response.text
    [result] = response.json()["results"]
    assert result["status"] in ("ok", "partial"), result
    assert result["payload"] == _committed(kind)


def test_the_tile_configurations_are_valid_catalog_configurations() -> None:
    """The exporter's configs are what a dashboard could actually hold, in the catalog's own
    words, for every kind, and each names the EuroLeague."""
    assert set(fixtures_export.WIDGET_CONFIGS) == set(KINDS)
    for kind, config_ in fixtures_export.WIDGET_CONFIGS.items():
        cleaned, errors = catalog.validate_widget_config(kind, config_)
        assert errors == [], (kind, errors)
        assert cleaned["league"] == "euroleague"


@pytest.mark.parametrize("kind", KINDS)
def test_each_tile_fixture_is_a_euroleague_payload_with_no_forbidden_key(kind: str) -> None:
    document = _committed(kind)
    assert document["league"] == "euroleague"
    assert scan_keys(document) == []
    assert "freshness" in document


def test_the_four_tile_fixtures_are_exported_and_nothing_else_is_stray() -> None:
    exported = {name for name in fixtures_export.FIXTURE_NAMES if name.startswith("widget_")}
    assert exported == {f"widget_{kind}" for kind in KINDS}
    on_disk = {path.stem for path in LEAGUE_FIXTURES.glob("*.json")}
    assert on_disk == set(fixtures_export.FIXTURE_NAMES)
    assert [path for path in LEAGUE_FIXTURES.iterdir() if path.is_dir()] == []  # flat files


def test_a_tile_fixture_names_its_subject_and_a_game() -> None:
    """Not an empty tile: a matchup with a game and a projection, a defence for the club, a
    report for the club, a slate with games."""
    matchup = _committed("team_matchup")
    assert matchup["game"] is not None and matchup["projection"] is not None
    assert [side["team"]["clubCode"] for side in matchup["teams"]] == ["ZZA", "ZZP"]
    defence = _committed("defense_by_position")
    assert defence["team"]["clubCode"] == "ZZA" and "buckets" in defence
    report = _committed("availability_report")
    assert report["state"] and [t["team"]["clubCode"] for t in report["teams"]] == ["ZZA"]
    slate = _committed("slate_projections")
    assert slate["games"] and slate["model"]["kind"] == "latest"


def test_no_league_fixture_name_collides_with_another_once_the_sync_script_prefixes_it() -> None:
    """``scripts/sync_contracts.sh`` copies ``leagues/<dir>/<name>.json`` into the app bundle as
    ``league_<dir>_<name>.json``; the bundle flattens folders, so those names, and the flat
    fixtures' own names, must all be distinct or Xcode stops with "multiple commands produce"."""
    root = LEAGUE_FIXTURES.parents[1]
    flat = {path.name for path in root.glob("*.json")}
    bundled: list[str] = []
    for league_dir in ("nba", "el"):
        folder = root / "leagues" / league_dir
        bundled.extend(f"league_{league_dir}_{path.name}" for path in folder.glob("*.json"))
    assert len(bundled) == len(set(bundled))
    assert not (set(bundled) & flat)
