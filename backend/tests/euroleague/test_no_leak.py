"""No EuroLeague row reaches an NBA route, and no NBA row reaches a EuroLeague one.

The two leagues share vocabulary (``MIL`` is Milwaukee and Milan, ``2026-27`` is a season on both
calendars, a game can be played on an NBA game date) and nothing else: separate files, separate
tables, separate URL spaces. This test is the proof, and it is built to fail loudly if a future
change joins them, by making the collisions as bad as they can be:

* a EuroLeague club whose official code is ``MIL``, named so it can be recognised ("Milan
  Collision Club"), in a store whose season label is ``2026-27``;
* a final EuroLeague game dated on a day the NBA seed has games.

Then it does four things (design section 2.1, item 8):

(a) re-runs the NBA's fixture exporter with that EuroLeague store in the environment and demands the
    committed NBA fixtures, byte for byte (falling back to "identical to a run without the
    EuroLeague store" when the committed files are stale for reasons that are not the EuroLeague's,
    so a leak is never excused and another package's pending regeneration is never blamed on it);
(b) walks every ``GET`` route of the real application outside ``/v1/el`` and every preset resolve
    and asserts that no EuroLeague id, club name or EuroLeague-only key appears anywhere in any body;
(c) asserts the reverse over every ``/v1/el`` ``GET``: no NBA team id, no NBA game id, no NBA team
    name;
(d) asserts the NBA store's rows are untouched by the EuroLeague's reads and writes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from nbastats import config, db as db_module, fixtures_export
from nbastats.api import deps as nba_deps
from nbastats.api.app import create_app
from nbastats.api.errors import install_error_handlers
from nbastats.euroleague import bootstrap
from nbastats.euroleague.api import deps as el_deps
from nbastats.euroleague.api.routes import router as el_router
from nbastats.euroleague.db import create_el_engine, dispose_el_engine, init_el_db
from nbastats.euroleague.demo import DEMO_AS_OF, seed_demo
from nbastats.euroleague.read.queries import clear_memo
from nbastats.models import Game, Player, Team
from nbastats.shared import league_registry

NOW = DEMO_AS_OF.replace(tzinfo=timezone.utc)
FIXTURES = Path(__file__).resolve().parents[3] / "contracts" / "fixtures"
COLLISION_NAME = "Milan Collision Club"

#: Every column that holds a club code in the EuroLeague store.
CLUB_COLUMNS = (
    ("el_club", "club_code"),
    ("el_club_alias", "code"),
    ("el_club_alias", "club_code"),
    ("el_club_season", "club_code"),
    ("el_registration", "club_code"),
    ("el_game", "home_club_code"),
    ("el_game", "away_club_code"),
    ("el_player_game", "club_code"),
    ("el_team_game", "club_code"),
    ("el_team_game", "opp_club_code"),
    ("el_team_rating", "club_code"),
    ("el_player_rate", "club_code"),
    ("el_intel_status", "club_code"),
    ("el_intel_override", "club_code"),
    ("el_intel_news_subject", "club_code"),
)

EL_ONLY_KEYS = {"clubCode", "personCode", "tvCode", "seasonCode"}
EL_ID = re.compile(r"^E\d{4}-")
NBA_TEAM_ID = re.compile(r"1610612\d{3}")
NBA_GAME_ID = re.compile(r"^00\d{8}$")


# --------------------------------------------------------------------------- the stores


@pytest.fixture(scope="module")
def colliding_store(seeded_database, tmp_path_factory):
    """A EuroLeague store built to collide with the seeded NBA one (see the module docstring)."""
    nba_engine, _ = seeded_database
    with Session(nba_engine, future=True) as session:
        nba_day = session.execute(
            select(Game.game_date)
            .where(Game.status == "final")
            .order_by(Game.game_date.desc())
            .limit(1)
        ).scalar_one()
    path = tmp_path_factory.mktemp("el-collide") / "el.db"
    engine = create_el_engine(f"sqlite:///{path}")
    init_el_db(engine)
    with Session(engine, future=True) as session:
        seed_demo(session)
        session.commit()
    with engine.begin() as connection:
        for table, column in CLUB_COLUMNS:
            connection.execute(text(f"UPDATE {table} SET {column} = 'MIL' WHERE {column} = 'ZZA'"))
        connection.execute(
            text("UPDATE el_club SET name = :n, short_name = 'Milan' WHERE club_code = 'MIL'"),
            {"n": COLLISION_NAME},
        )
        game = connection.execute(
            text("SELECT game_id FROM el_game WHERE status = 'final' ORDER BY game_id LIMIT 1")
        ).scalar_one()
        connection.execute(
            text("UPDATE el_game SET game_date = :d WHERE game_id = :g"),
            {"d": nba_day.isoformat(), "g": game},
        )
    engine.dispose()
    return path, nba_day


@pytest.fixture()
def el_environment(colliding_store, monkeypatch):
    path, _ = colliding_store
    monkeypatch.setenv("HARDWOOD_EL_DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("HARDWOOD_RATE_LIMIT", "0")
    config.reset_settings_cache()
    dispose_el_engine()
    clear_memo()
    nba_deps.reset_rate_limiter()
    yield path
    config.reset_settings_cache()
    nba_deps.reset_rate_limiter()
    dispose_el_engine()


@pytest.fixture()
def nba_client(seeded_engine, el_environment):
    with TestClient(create_app()) as client:
        yield client


@pytest.fixture()
def el_client(el_environment):
    league_registry.clear()
    bootstrap.set_state(bootstrap.BootstrapResult(state="ready", kind="synthetic", is_demo=True))
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(el_router, prefix="/v1")
    app.dependency_overrides[el_deps.get_now] = lambda: NOW
    yield TestClient(app)
    bootstrap.reset_state()
    league_registry.clear()


def strings(value):
    """Every string in a JSON body, and every key."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)
    elif isinstance(value, str):
        yield value


def keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from keys(child)


# --------------------------------------------------------------------------- (a) the NBA fixtures


def test_the_nba_fixtures_are_unchanged_with_a_colliding_euroleague_store_present(
    el_environment,
) -> None:
    with_el = {
        name: fixtures_export.dumps(doc) for name, doc in fixtures_export.export_fixtures().items()
    }
    committed = {p.stem: p.read_text(encoding="utf-8") for p in FIXTURES.glob("*.json")}
    names = sorted(with_el)
    stale = [n for n in names if n in committed and committed[n] != with_el[n]]
    if stale:
        # Not necessarily a leak: another package may not have regenerated yet. A leak is the
        # EuroLeague store changing the answer, so compare with a run that has no such store.
        previous = os.environ.pop("HARDWOOD_EL_DATABASE_URL")
        try:
            without = {
                name: fixtures_export.dumps(doc)
                for name, doc in fixtures_export.export_fixtures().items()
            }
        finally:
            os.environ["HARDWOOD_EL_DATABASE_URL"] = previous
        assert with_el == without, "the EuroLeague store changed an NBA fixture"
    else:
        assert all(committed[n] == with_el[n] for n in names if n in committed)
    assert not [n for n in names if n not in committed and not n.startswith("widget_")]


# --------------------------------------------------------------------------- (b) NBA routes


def _nba_ids(client):
    with Session(db_module.get_engine(), future=True) as session:
        player = session.execute(
            select(Player.player_id).order_by(Player.player_id).limit(1)
        ).scalar_one()
        team = session.execute(select(Team.team_id).order_by(Team.team_id).limit(1)).scalar_one()
        game = session.execute(
            select(Game.game_id).where(Game.status == "final").order_by(Game.game_id).limit(1)
        ).scalar_one()
    return {
        "player_id": player,
        "playerId": player,
        "team_id": team,
        "teamId": team,
        "homeTeamId": team,
        "awayTeamId": team + 1,
        "game_id": game,
        "gameId": game,
    }


SKIP_PREFIXES = (
    "/v1/auth",
    "/v1/me",
    "/v1/dashboards",
    "/v1/el",
    "/v1/sync/stream",
    "/v1/dashboard/resolve-preset",
)
REQUIRED_QUERY = {"q": "a", "metric": "pts", "token": "x"}


def test_no_nba_route_shows_a_euroleague_row(nba_client, colliding_store) -> None:
    document = nba_client.get("/openapi.json").json()
    ids = _nba_ids(nba_client)
    walked: list[str] = []
    for path, operations in sorted(document["paths"].items()):
        if "get" not in operations or path.startswith(SKIP_PREFIXES):
            continue
        parameters = operations["get"].get("parameters", [])
        url = path
        query: dict[str, object] = {}
        for parameter in parameters:
            if parameter["in"] == "path":
                if parameter["name"] not in ids:
                    url = None
                    break
                url = url.replace("{" + parameter["name"] + "}", str(ids[parameter["name"]]))
            elif parameter.get("required") and parameter["name"] in ids:
                query[parameter["name"]] = ids[parameter["name"]]
            elif parameter.get("required"):
                query[parameter["name"]] = REQUIRED_QUERY.get(parameter["name"], "1")
        if url is None:
            continue
        response = nba_client.get(url, params=query or None)
        if response.status_code != 200:
            continue
        body = response.json()
        walked.append(path)
        assert_no_euroleague(body, url)
    # Every preset, resolved.
    presets = nba_client.get("/v1/presets").json()
    keys_ = [p["presetKey"] for p in presets["presets"]]
    assert keys_, "the preset catalogue is empty, so there is nothing to resolve"
    for key in keys_:
        response = nba_client.get(f"/v1/dashboard/resolve-preset/{key}")
        assert response.status_code == 200, (key, response.text[:200])
        assert_no_euroleague(response.json(), f"preset {key}")
        walked.append(f"preset:{key}")
    assert len(walked) >= 12, f"the walk must exercise the app, not skim it: {walked}"
    # MIL is still Milwaukee on the NBA side, whatever the EuroLeague calls it.
    teams = nba_client.get("/v1/teams").json()["teams"]
    assert next(t for t in teams if t["abbr"] == "MIL")["name"] == "Milwaukee Bucks"


def assert_no_euroleague(body, where) -> None:
    for text_ in strings(body):
        assert not EL_ID.match(text_), (where, text_)
        assert COLLISION_NAME not in text_, (where, text_)
        assert text_ != "euroleague" or where.endswith("/leagues"), (where, text_)
    assert not (set(keys(body)) & EL_ONLY_KEYS), (where, set(keys(body)) & EL_ONLY_KEYS)


# --------------------------------------------------------------------------- (c) EuroLeague routes

EL_READS = [
    "/meta",
    "/teams",
    "/teams/MIL",
    "/teams/MIL/matchup",
    "/teams/MIL/defense-by-position",
    "/defense-by-position",
    "/rounds/5",
    "/rounds/3",
    "/rounds/5/scorers",
    "/games",
    "/games?clubCode=MIL",
    "/stats/players",
    "/ratings",
    "/projections",
    "/projections/review",
    "/availability",
    "/availability/review-queue",
    "/news",
    "/sources",
    "/method",
    "/model-settings",
    "/review-queue",
    "/sync",
    "/health",
    "/players/demo-1",
    "/players/demo-1/gamelog",
]


def test_no_euroleague_route_shows_an_nba_row(el_client, seeded_engine) -> None:
    with Session(seeded_engine, future=True) as session:
        names = {n for (n,) in session.execute(select(Team.name))}
        abbrs = {a for (a,) in session.execute(select(Team.abbr))} - {"MIL"}  # MIL is the collision
    walked = 0
    for path in EL_READS:
        response = el_client.get("/v1/el" + path)
        assert response.status_code == 200, (path, response.text[:200])
        body = response.json()
        walked += 1
        for text_ in strings(body):
            assert not NBA_TEAM_ID.search(text_) and not NBA_GAME_ID.match(text_), (path, text_)
            assert text_ not in names, (path, text_)
        # No NBA abbreviation in a team position (the colliding MIL is the EuroLeague's here).
        for node in _team_refs(body):
            assert node["abbr"] not in abbrs, (path, node)
            assert node["league"] == "euroleague"
    assert walked == len(EL_READS)
    ids = {g["gameId"] for g in el_client.get("/v1/el/games").json()["games"]}
    assert ids and all(EL_ID.match(i) for i in ids)
    # The collision itself: the EuroLeague's MIL is Milan, not Milwaukee.
    assert el_client.get("/v1/el/teams/MIL").json()["team"]["name"] == COLLISION_NAME
    assert el_client.get("/v1/el/games").status_code == 200


def _team_refs(value):
    if isinstance(value, dict):
        if {"abbr", "league", "id"} <= set(value):
            yield value
        for child in value.values():
            yield from _team_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _team_refs(child)


def test_a_euroleague_game_dated_on_an_nba_day_stays_out_of_the_nba_games_route(
    nba_client, el_client, colliding_store
) -> None:
    _, day = colliding_store
    nba_day = nba_client.get("/v1/games", params={"date": day.isoformat()})
    assert nba_day.status_code == 200
    body = nba_day.json()
    assert all(not EL_ID.match(g["gameId"]) for g in body["games"]) and body["games"]
    el_day = [
        g for g in el_client.get("/v1/el/games").json()["games"] if g["date"] == day.isoformat()
    ]
    assert len(el_day) == 1 and EL_ID.match(el_day[0]["gameId"])
    assert {g["gameId"] for g in body["games"]}.isdisjoint({el_day[0]["gameId"]})


# --------------------------------------------------------------------------- (d) the NBA store


def _snapshot(engine) -> dict[str, str]:
    """A digest per NBA table over every row (so a single changed cell shows)."""
    out: dict[str, str] = {}
    with engine.connect() as connection:
        tables = [
            r[0]
            for r in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            )
        ]
        for table in tables:
            rows = connection.execute(text(f'SELECT * FROM "{table}" ORDER BY 1, 2')).fetchall()
            out[table] = hashlib.sha256(
                json.dumps([list(map(str, r)) for r in rows]).encode()
            ).hexdigest()
    return out


def test_the_nba_store_is_untouched_by_the_euroleagues_reads_and_writes(
    el_client, seeded_engine, monkeypatch
) -> None:
    monkeypatch.setenv("HARDWOOD_API_KEY", "k")
    config.reset_settings_cache()
    with seeded_engine.connect() as connection:
        before_pts = connection.execute(
            text("SELECT COUNT(*), AVG(average) FROM league_season")
        ).one()
    before = _snapshot(seeded_engine)
    for path in EL_READS:
        el_client.get("/v1/el" + path)
    headers = {"X-API-Key": "k"}
    assert (
        el_client.post(
            "/v1/el/availability",
            headers=headers,
            json={
                "clubCode": "MIL",
                "personCode": "demo-1",
                "status": "out",
                "sourceLabel": "t",
                "sourcePublishedAt": "2026-10-11",
            },
        ).status_code
        == 201
    )
    assert (
        el_client.patch(
            "/v1/el/model-settings",
            headers=headers,
            json={"settings": [{"key": "formWeight", "value": 0.3}]},
        ).status_code
        == 200
    )
    assert (
        el_client.post(
            "/v1/el/news/links",
            headers=headers,
            json={
                "title": "t",
                "link": "https://example.org/x",
                "publishedAt": "2026-10-11T00:00:00Z",
                "sourceName": "x",
                "teamIds": ["MIL"],
            },
        ).status_code
        == 201
    )
    with seeded_engine.connect() as connection:
        after_pts = connection.execute(
            text("SELECT COUNT(*), AVG(average) FROM league_season")
        ).one()
    assert tuple(after_pts) == tuple(before_pts)
    assert _snapshot(seeded_engine) == before
    # ...and nothing EuroLeague-shaped was created in the NBA file.
    with seeded_engine.connect() as connection:
        tables = {
            r[0]
            for r in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
    assert not [t for t in tables if t.startswith("el_")]
