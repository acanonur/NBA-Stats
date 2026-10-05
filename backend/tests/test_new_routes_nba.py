"""The NBA matchup, defence, projection, availability, news, sources and league routes, over HTTP.

What is asserted, and how
-------------------------
**Every read answers, in the documented shape, on the seeded league.** One client over the demo
store, the clock held still by overriding the same dependency the routes use, walks all fourteen
reads with real ids and checks the keys a client decodes. The whole walk is then checked as a
body of JSON: every object key is lowerCamelCase and none carries a word the product does not
define, no probability of winning and no rank appears, and every share, coverage and chance is a
fraction in [0, 1].

**Failures are the contract's.** An unknown team (and a EuroLeague club code, which is simply not
an NBA team) is ``404 team_not_found``, an unknown game ``404 game_not_found``, a window or basis
out of range ``400 bad_request`` naming the field, an unloaded season ``422 season_not_loaded``, an
unknown status ``400 invalid_status``. Every one has the envelope and a request id.

**Writes are guarded, validated and reversible.** On a store that does not hold the demo league (a
write must never land next to invented games) three routes write: an availability override, a
pasted headline link and a model setting. Each needs the API key or a session with CSRF and answers
``401`` with neither; a body with a field the service does not know is refused, not trimmed; an
unknown status is ``invalid_status``; a link to a gambling operator is dropped (override) or refused
(headline); the demo league refuses an override outright; an unknown or out-of-range setting is
``400`` and a patch is all or nothing. A cleared override stays in the table.

**The surface is the allowed surface.** Every query, path and body name on these routes is on the
allowed-parameter list, bodies forbid extras, the routes sit behind the key and the limiter, and
``/v1/leagues`` always has a row for the EuroLeague, whether or not its package is running.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from nbastats import config
from nbastats import db as db_module
from nbastats.api import deps
from nbastats.api.app import create_app
from nbastats.db import create_db_engine, init_db
from nbastats.models import Game, Player, Team
from nbastats.nba_intel import store as intel_store
from nbastats.nba_intel.models import (
    MODEL_SETTING_KEYS,
    NbaIntelModelSetting,
    NbaIntelNewsItem,
    NbaIntelOverride,
)
from nbastats.nba_matchup import queries
from nbastats.shared import league_registry, market_guard
from tests.test_nba_matchup import LeagueBuilder, Store, at
from tests.test_nba_team_projection import squad_league

FROZEN = at("2026-03-01", 16, 0)
LIVE_NOW = at("2025-11-12", 16, 0)
KEY = "k-routes-test"
KEY_HEADERS = {"X-API-Key": KEY}

PROBABILITY_WORDS = ("probability", "winprob", "chanceofwinning", "pover", "lean", "edge")


@contextmanager
def api_client(**environment: str | None) -> Iterator[TestClient]:
    """An app built with ``environment`` applied (``None`` unsets), restored afterwards. The
    EuroLeague is switched off unless a test says otherwise, so what it registers cannot leak in."""
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


def naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None)


def hold(client: TestClient, moment: datetime) -> None:
    """Hold the routes' clock still (the dependency every route takes)."""
    client.app.dependency_overrides[queries.get_now] = lambda: moment  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _fresh_memo() -> Iterator[None]:
    queries.clear_memo()
    yield
    queries.clear_memo()


@pytest.fixture(scope="module")
def seeded_client(seeded_engine: Engine) -> Iterator[TestClient]:
    with api_client() as client:
        hold(client, FROZEN)
        yield client


@pytest.fixture(scope="module")
def ids(seeded_engine: Engine) -> dict[str, Any]:
    """Real ids from the seeded league to put into paths and queries."""
    with Session(seeded_engine, future=True) as session:
        scheduled = (
            session.execute(
                select(Game)
                .where(Game.status == "scheduled", Game.season == "2025-26")
                .order_by(Game.game_date, Game.game_id)
            )
            .scalars()
            .first()
        )
        final = (
            session.execute(
                select(Game)
                .where(
                    Game.status == "final",
                    Game.season == "2025-26",
                    Game.season_type == "Regular Season",
                )
                .order_by(Game.game_date.desc(), Game.game_id)
            )
            .scalars()
            .first()
        )
        assert scheduled is not None and final is not None
        return {
            "home": scheduled.home_team_id,
            "away": scheduled.away_team_id,
            "scheduled": scheduled.game_id,
            "final": final.game_id,
            "player": session.execute(select(Player.player_id).order_by(Player.player_id))
            .scalars()
            .first(),
            "teams": [int(t) for t in session.execute(select(Team.team_id).limit(3)).scalars()],
        }


def get(client: TestClient, path: str, **params: Any) -> Any:
    response = client.get(path, params=params or None)
    assert response.status_code == 200, f"{path} {params}: {response.text}"
    return response.json()


def keyed(client: TestClient, path: str, **params: Any) -> Any:
    """A read on a store behind the API key."""
    response = client.get(path, params=params or None, headers=KEY_HEADERS)
    assert response.status_code == 200, f"{path} {params}: {response.text}"
    return response.json()


def assert_error(response: Any, code: str, status: int, field: str | None = None) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert error["code"] == code
    assert error["message"] and error["requestId"]
    assert response.headers["X-Request-Id"] == error["requestId"]
    if field is not None:
        assert error["field"] == field
    return error


def walk(value: Any, path: str = "$") -> Iterator[tuple[str, str, Any]]:
    """Every ``(path, key, value)`` pair of a body, at any depth."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield f"{path}.{key}", key, child
            yield from walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, f"{path}[{index}]")


# =========================================================================== the reads


def reads(ids: dict[str, Any]) -> list[tuple[str, dict[str, Any], set[str]]]:
    """``(path, query, keys a client decodes)`` for every read route."""
    team = str(ids["home"])
    return [
        (
            "/v1/matchups",
            {"homeTeamId": team, "awayTeamId": str(ids["away"])},
            {
                "league",
                "season",
                "seasonType",
                "phase",
                "freshness",
                "game",
                "teams",
                "leagueAverage",
                "projection",
                "availability",
                "notes",
            },
        ),
        (
            f"/v1/teams/{team}/matchup",
            {},
            {
                "league",
                "season",
                "freshness",
                "game",
                "teams",
                "projection",
                "availability",
                "notes",
            },
        ),
        (
            f"/v1/games/{ids['final']}/matchup",
            {},
            {"league", "season", "freshness", "game", "teams", "projection", "notes"},
        ),
        (
            f"/v1/teams/{team}/defense-by-position",
            {},
            {
                "league",
                "team",
                "season",
                "seasonType",
                "scheme",
                "basis",
                "regulationMinutes",
                "freshness",
                "window",
                "pointsAllowedPerGame",
                "leaguePointsAllowedPerGame",
                "buckets",
                "provisional",
                "withheld",
                "coverage",
                "reconciliation",
                "method",
                "availability",
                "caveat",
                "notes",
            },
        ),
        (
            "/v1/defense-by-position",
            {"window": "10"},
            {
                "league",
                "season",
                "scheme",
                "basis",
                "freshness",
                "leaguePointsAllowedPerGame",
                "teams",
                "method",
                "caveat",
            },
        ),
        (
            "/v1/projections",
            {},
            {
                "league",
                "date",
                "round",
                "freshness",
                "model",
                "games",
                "review",
                "availability",
                "notes",
            },
        ),
        (
            f"/v1/games/{ids['scheduled']}/projection",
            {},
            {"current", "locked", "history", "game", "availability", "notes"},
        ),
        (
            "/v1/projections/review",
            {},
            {"league", "scope", "freshness", "byModel", "reconstructed", "games", "notes"},
        ),
        (
            "/v1/availability",
            {"includeNews": "true"},
            {"league", "asOf", "freshness", "state", "message", "teams", "news", "attribution"},
        ),
        ("/v1/availability/review-queue", {}, {"league", "items"}),
        ("/v1/news", {}, {"league", "freshness", "items", "notes"}),
        ("/v1/sources", {}, {"league", "freshness", "sources", "dayOneNotice", "attribution"}),
        ("/v1/model-settings", {}, {"league", "freshness", "settings"}),
        ("/v1/leagues", {}, set()),
    ]


@pytest.fixture(scope="module")
def walked(seeded_client: TestClient, ids: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for path, query, _ in reads(ids):
        out[path] = get(seeded_client, path, **query)
    return out


def test_every_read_route_answers_with_the_keys_a_client_decodes(
    walked: dict[str, Any], ids: dict[str, Any]
) -> None:
    for path, _, keys in reads(ids):
        body = walked[path]
        if path == "/v1/leagues":
            assert isinstance(body, list) and [row["key"] for row in body] == ["nba", "euroleague"]
            continue
        assert keys <= set(body), (path, sorted(keys - set(body)))
        assert body["league"] == "nba" if "league" in body else True


def test_the_walk_is_clean_camel_case_with_no_market_words_no_rank_and_no_probability(
    walked: dict[str, Any],
) -> None:
    for path, body in walked.items():
        violations = market_guard.scan_keys(body, root=path)
        assert violations == [], [str(v) for v in violations]
        for where, key, _ in walk(body, path):
            # lowerCamelCase on the wire; the only capitals are the position buckets used as keys.
            assert "_" not in key and (not key[:1].isupper() or key in {"G", "F", "C"}), where
            lowered = key.lower()
            assert "rank" not in lowered and not any(w in lowered for w in PROBABILITY_WORDS), where
    # The tables that could tempt a ranking are sorted by a recorded fact and say nothing more.
    table = walked["/v1/defense-by-position"]["teams"]
    values = [t["pointsAllowedPerGame"] for t in table if t["pointsAllowedPerGame"] is not None]
    assert values == sorted(values)


def test_every_share_coverage_and_chance_is_a_fraction(walked: dict[str, Any]) -> None:
    seen = 0
    for path, body in walked.items():
        for where, key, value in walk(body, path):
            if key in {
                "share",
                "leagueShare",
                "chanceOfPlaying",
                "listed",
                "workbookListing",
                "unknown",
            }:
                if value is None:
                    continue
                assert isinstance(value, float) and 0.0 <= value <= 1.0, (where, value)
                seen += 1
    assert seen > 50


def test_the_matchup_the_projection_and_the_slate_agree_about_the_same_game(
    seeded_client: TestClient, ids: dict[str, Any]
) -> None:
    matchup = get(
        seeded_client, "/v1/matchups", homeTeamId=str(ids["home"]), awayTeamId=str(ids["away"])
    )
    detail = get(seeded_client, f"/v1/games/{ids['scheduled']}/projection")
    slate = get(seeded_client, "/v1/projections", date=matchup["game"]["date"])
    assert matchup["game"]["gameId"] == ids["scheduled"] == detail["current"]["game"]["gameId"]
    on_slate = next(g for g in slate["games"] if g["game"]["gameId"] == ids["scheduled"])
    for payload in (matchup["projection"], on_slate):
        assert payload["home"]["projectedPoints"] == detail["current"]["home"]["projectedPoints"]
        assert payload["margin"] == detail["current"]["margin"]
    # The team's own route is the same game: its next scheduled one.
    own = get(seeded_client, f"/v1/teams/{ids['home']}/matchup")
    assert own["game"]["gameId"] == ids["scheduled"]
    assert own["projection"]["margin"] == detail["current"]["margin"]


def test_the_clock_is_the_dependency_and_a_held_clock_holds_every_figure(
    seeded_client: TestClient,
) -> None:
    body = get(seeded_client, "/v1/sources")
    assert body["freshness"]["generatedAt"] == "2026-03-01T16:00:00Z"
    assert body["freshness"]["isDemo"] is True
    again = get(seeded_client, "/v1/sources")
    assert again == body


def test_the_default_clock_is_the_real_one(seeded_engine: Engine) -> None:
    with api_client() as client:
        stamp = get(client, "/v1/sources")["freshness"]["generatedAt"]
    moment = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    assert abs((datetime.utcnow() - moment).total_seconds()) < 60


# =========================================================================== failures


def test_unknown_things_are_404_with_the_envelope(
    seeded_client: TestClient, ids: dict[str, Any]
) -> None:
    for path in (
        "/v1/teams/ZZA/matchup",  # a EuroLeague club code is not an NBA team
        "/v1/teams/999/matchup",
        "/v1/teams/ZZA/defense-by-position",
        "/v1/teams/not-a-number/defense-by-position",
    ):
        assert_error(seeded_client.get(path), "team_not_found", 404)
    assert_error(
        seeded_client.get(
            "/v1/matchups", params={"homeTeamId": "ZZA", "awayTeamId": str(ids["away"])}
        ),
        "team_not_found",
        404,
    )
    assert_error(seeded_client.get("/v1/games/0000000000/matchup"), "game_not_found", 404)
    assert_error(seeded_client.get("/v1/games/0000000000/projection"), "game_not_found", 404)
    assert_error(
        seeded_client.get("/v1/availability", params={"teamId": "ZZA"}), "team_not_found", 404
    )
    assert_error(seeded_client.get("/v1/news", params={"teamId": "ZZA"}), "team_not_found", 404)
    assert_error(seeded_client.get("/v1/news", params={"playerId": 1}), "player_not_found", 404)


def test_bad_parameters_are_400_naming_the_field(
    seeded_client: TestClient, ids: dict[str, Any]
) -> None:
    home, away = str(ids["home"]), str(ids["away"])
    both = {"homeTeamId": home, "awayTeamId": away}
    for window in ("2", "16", "-3"):
        assert_error(
            seeded_client.get("/v1/matchups", params={**both, "window": window}),
            "bad_request",
            400,
            "window",
        )
    assert_error(
        seeded_client.get("/v1/matchups", params={**both, "seasonType": "Exhibition"}),
        "bad_request",
        400,
        "seasonType",
    )
    assert_error(seeded_client.get("/v1/matchups", params={"homeTeamId": home}), "bad_request", 400)
    assert_error(
        seeded_client.get("/v1/matchups", params={"homeTeamId": home, "awayTeamId": home}),
        "bad_request",
        400,
    )
    assert_error(
        seeded_client.get(f"/v1/teams/{home}/defense-by-position", params={"basis": "per100"}),
        "bad_request",
        400,
        "basis",
    )
    assert_error(
        seeded_client.get(f"/v1/teams/{home}/defense-by-position", params={"window": -1}),
        "bad_request",
        400,
        "window",
    )
    assert_error(
        seeded_client.get("/v1/projections", params={"date": "tomorrow-ish"}),
        "bad_request",
        400,
        "date",
    )
    assert_error(
        seeded_client.get("/v1/availability", params={"date": "2026-13-45"}),
        "bad_request",
        400,
        "date",
    )
    assert_error(
        seeded_client.get("/v1/availability", params={"statuses": "out,maybe"}),
        "invalid_status",
        400,
        "statuses",
    )
    for limit in (0, 51, -1):
        assert_error(
            seeded_client.get("/v1/news", params={"limit": limit}), "bad_request", 400, "limit"
        )
    assert_error(
        seeded_client.get("/v1/news", params={"limit": "many"}), "bad_request", 400, "limit"
    )
    assert_error(
        seeded_client.get("/v1/projections", params={"teamIds": "ZZA"}), "team_not_found", 404
    )


def test_a_season_that_is_not_loaded_is_422_and_a_euroleague_code_is_not_a_season(
    seeded_client: TestClient, ids: dict[str, Any]
) -> None:
    home = str(ids["home"])
    assert_error(
        seeded_client.get(f"/v1/teams/{home}/defense-by-position", params={"season": "2031-32"}),
        "season_not_loaded",
        422,
        "season",
    )
    assert_error(
        seeded_client.get(f"/v1/teams/{home}/defense-by-position", params={"season": "E2026"}),
        "bad_request",
        400,
        "season",
    )
    assert_error(
        seeded_client.get("/v1/projections/review", params={"season": "2031-32"}),
        "season_not_loaded",
        422,
    )


def test_a_filter_by_status_keeps_only_those_statuses(seeded_client: TestClient) -> None:
    every = get(seeded_client, "/v1/availability")
    only = get(seeded_client, "/v1/availability", statuses="out,doubtful")
    assert every["state"] == "disabled" == only["state"]  # the demo league shows no statuses
    assert "Demo league" in every["message"]
    for team in only["teams"]:
        assert team["entries"] == []


def test_the_demo_league_shows_no_injury_statuses_and_says_why(
    seeded_client: TestClient,
) -> None:
    body = get(seeded_client, "/v1/availability")
    assert body["state"] == "disabled" and body["freshness"]["isDemo"] is True
    assert body["teams"] and all(
        t["reportState"] == "noReport" and t["entries"] == [] for t in body["teams"]
    )
    assert body["asOf"] is None
    assert get(seeded_client, "/v1/availability/review-queue")["items"] == []
    sources = {s["key"]: s for s in get(seeded_client, "/v1/sources")["sources"]}
    assert sources["nba.injuryReport"]["state"] == "disabled"
    assert sources["nba.injuryReport"]["enabled"] is False
    assert sources["nba.stats"]["state"] == "ok" and "invented" in sources["nba.stats"]["reason"]
    notice = get(seeded_client, "/v1/sources")["dayOneNotice"]
    assert notice.startswith("Demo league: every game and player here is invented")
    assert "Real injury statuses are not shown next to invented games" in notice


# =========================================================================== the writes


@pytest.fixture()
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, Engine]]:
    """The app over a store that is *not* the demo league (the squad league of the projection
    tests), with the API key set and the clock held at 16:00 UTC on 12 November 2025."""
    url = f"sqlite:///{tmp_path / 'live.db'}"
    engine = create_db_engine(url)
    init_db(engine)
    with Session(engine, future=True) as session:
        squad_league(Store(engine, session, LeagueBuilder(session)))
        league = LeagueBuilder(session)
        league.game("g7", "2025-11-13", 1, 2)
        league.commit()
    monkeypatch.setenv("DATABASE_URL", url)
    config.reset_settings_cache()
    db_module.dispose_engine()
    try:
        with api_client(HARDWOOD_API_KEY=KEY) as client:
            hold(client, LIVE_NOW)
            yield client, engine
    finally:
        engine.dispose()
        db_module.dispose_engine()
        config.reset_settings_cache()


def count(engine: Engine, model: Any) -> int:
    with Session(engine, future=True) as session:
        return session.execute(select(func.count()).select_from(model)).scalar_one()


def test_a_team_with_no_scheduled_game_has_no_next_matchup(live: tuple[TestClient, Engine]) -> None:
    client, _ = live
    response = client.get("/v1/teams/3/matchup", headers=KEY_HEADERS)
    error = assert_error(response, "game_not_found", 404)
    assert "T03" in error["message"]
    body = keyed(client, "/v1/teams/1/matchup")  # team 1 hosts team 2 in g7
    assert body["game"]["gameId"] == "g7" and body["projection"] is not None
    assert [t["team"]["id"] for t in body["teams"]] == ["1", "2"]


def test_the_writes_need_the_key_or_a_session_and_say_401_without(
    live: tuple[TestClient, Engine],
) -> None:
    client, engine = live
    attempts = (
        client.post("/v1/availability", json={"playerId": 101, "status": "out"}),
        client.delete("/v1/availability/1"),
        client.post(
            "/v1/news/links",
            json={
                "title": "t",
                "link": "https://example.org/x",
                "publishedAt": "2025-11-12T10:00:00Z",
                "sourceName": "s",
            },
        ),
        client.patch("/v1/model-settings", json={"settings": [{"key": "boostCap", "value": 1.3}]}),
    )
    for response in attempts:
        assert_error(response, "unauthorized", 401)
    # A wrong key is no better, and a read needs the key too when one is configured.
    wrong = client.post(
        "/v1/availability", json={"playerId": 101, "status": "out"}, headers={"X-API-Key": "nope"}
    )
    assert_error(wrong, "unauthorized", 401)
    assert_error(client.get("/v1/sources"), "unauthorized", 401)
    assert count(engine, NbaIntelOverride) == 0 and count(engine, NbaIntelNewsItem) == 0
    assert count(engine, NbaIntelModelSetting) == 0


def test_writes_without_any_key_configured_and_no_session_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write endpoint with no credential at all is the one thing this service does not offer."""
    url = f"sqlite:///{tmp_path / 'open.db'}"
    engine = create_db_engine(url)
    init_db(engine)
    with Session(engine, future=True) as session:
        squad_league(Store(engine, session, LeagueBuilder(session)))
    monkeypatch.setenv("DATABASE_URL", url)
    config.reset_settings_cache()
    db_module.dispose_engine()
    try:
        with api_client() as client:  # no HARDWOOD_API_KEY
            hold(client, LIVE_NOW)
            assert client.get("/v1/sources").status_code == 200
            assert_error(
                client.post("/v1/availability", json={"playerId": 101, "status": "out"}),
                "unauthorized",
                401,
            )
            assert_error(
                client.patch(
                    "/v1/model-settings", json={"settings": [{"key": "boostCap", "value": 1.3}]}
                ),
                "unauthorized",
                401,
            )
    finally:
        engine.dispose()
        db_module.dispose_engine()
        config.reset_settings_cache()


def test_an_override_is_entered_listed_cleared_and_kept(live: tuple[TestClient, Engine]) -> None:
    client, engine = live
    created = client.post(
        "/v1/availability",
        headers=KEY_HEADERS,
        json={
            "playerId": 101,
            "status": "OUT",
            "teamId": "1",
            "note": "Club statement",
            "sourceUrl": "https://news.example.org/hoops/statement",
            "sourcePublishedAt": "2025-11-12T14:00:00Z",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["league"] == "nba" and body["status"] == "out"  # a status is any capitalisation
    assert (
        body["player"]["id"] == "101"
        and body["team"]["id"] == "1"
        and body["linkWithheld"] is False
    )
    override_id = body["overrideId"]

    # Information stamped at the very instant a read is made is not yet known (the rule that makes
    # a rebuilt projection honest), so let the clock run on a few minutes, as it does in life.
    hold(client, LIVE_NOW + timedelta(minutes=5))
    report = keyed(client, "/v1/availability", teamId="1", date="2025-11-13")
    [team] = report["teams"]
    [entry] = team["entries"]
    assert entry["isOverride"] is True and entry["overrideId"] == override_id
    assert entry["status"] == "out" and entry["chanceOfPlaying"] == 0.0 and entry["inForce"] is True
    assert entry["source"]["kind"] == "manual" and entry["source"]["url"].startswith(
        "https://news.example.org"
    )
    assert entry["source"]["publishedAt"] == "2025-11-12T14:00:00Z"
    assert entry["ageMinutes"] == 125
    assert report["state"] in {"noReportYet", "stale", "fresh"} and report["state"] != "disabled"

    # It reaches the projection: the home side now has an absence and a lower projected score.
    detail = keyed(client, "/v1/games/g7/projection")
    absence = detail["current"]["home"]["keyAbsences"][0]
    assert absence["player"]["id"] == "101" and absence["status"] == "out"
    assert detail["current"]["home"]["availabilityEffect"] < 0

    cleared = client.delete(f"/v1/availability/{override_id}", headers=KEY_HEADERS)
    assert cleared.status_code == 200
    assert (
        cleared.json()["alreadyCleared"] is False
        and cleared.json()["clearedAt"] == "2025-11-12T16:05:00Z"
    )
    again = client.delete(f"/v1/availability/{override_id}", headers=KEY_HEADERS).json()
    assert again["alreadyCleared"] is True  # idempotent
    assert count(engine, NbaIntelOverride) == 1  # the row stays, with the time it was cleared
    assert (
        keyed(client, "/v1/availability", teamId="1", date="2025-11-13")["teams"][0]["entries"]
        == []
    )
    assert_error(client.delete("/v1/availability/9999", headers=KEY_HEADERS), "not_found", 404)


def test_an_override_is_refused_for_what_it_cannot_honestly_say(
    live: tuple[TestClient, Engine],
) -> None:
    client, engine = live
    post = lambda **body: client.post(
        "/v1/availability", headers=KEY_HEADERS, json=body
    )  # noqa: E731
    assert_error(post(playerId=101, status="maybe"), "invalid_status", 400, "status")
    assert_error(post(playerId=101, status=""), "invalid_status", 400)
    assert_error(post(playerId=999999, status="out"), "player_not_found", 404)
    assert_error(post(playerId=101, status="out", teamId="ZZA"), "team_not_found", 404)
    assert_error(post(playerId=101, status="out", teamId="99"), "team_not_found", 404)
    assert_error(post(playerId=101, status="out", gameId="0000000000"), "game_not_found", 404)
    assert_error(
        post(playerId=101, status="out", teamId="2", gameId="g3"), "bad_request", 400, "gameId"
    )  # g3 is 3 v 1
    assert_error(
        post(playerId=101, status="out", sourceUrl="ftp://example.org/x"),
        "bad_request",
        400,
        "sourceUrl",
    )
    assert_error(
        post(playerId=101, status="out", sourcePublishedAt="2030-01-01"),
        "bad_request",
        400,
        "sourcePublishedAt",
    )
    assert_error(
        post(playerId=101, status="out", sourcePublishedAt="yesterday"),
        "bad_request",
        400,
        "sourcePublishedAt",
    )
    assert_error(post(status="out"), "bad_request", 400, "playerId")
    # A field the service does not know is refused, not silently dropped: nothing can slip in a
    # number to compare with.
    for extra in ("line", "overUnder", "pOver", "edge", "threshold"):
        assert_error(post(playerId=101, status="out", **{extra: 3.5}), "bad_request", 400, extra)
    assert count(engine, NbaIntelOverride) == 0


def test_a_link_to_a_gambling_operator_is_dropped_from_an_override_and_the_notice_kept(
    live: tuple[TestClient, Engine],
) -> None:
    client, engine = live
    response = client.post(
        "/v1/availability",
        headers=KEY_HEADERS,
        json={
            "playerId": 102,
            "status": "doubtful",
            "teamId": "1",
            "sourceUrl": "https://www.mozzartsport.com/news/1",
            "note": "Per a report",
        },
    )
    assert response.status_code == 201 and response.json()["linkWithheld"] is True
    with Session(engine, future=True) as session:
        row = session.execute(select(NbaIntelOverride)).scalar_one()
        assert row.source_url is None and "link withheld" in (row.note or "")


def test_the_demo_league_refuses_an_override_outright(
    seeded_client: TestClient, seeded_engine: Engine, ids: dict[str, Any]
) -> None:
    """The one write on the demo store: refused, and nothing is written next to invented games."""
    with Session(seeded_engine, future=True) as session:
        before = session.execute(select(func.count()).select_from(NbaIntelOverride)).scalar_one()
    # The seeded client has no key configured, so supply a session-less write the way the key
    # route would: the guard refuses first, which is itself the point.
    response = seeded_client.post(
        "/v1/availability", json={"playerId": ids["player"], "status": "out"}
    )
    assert_error(response, "unauthorized", 401)
    with api_client(HARDWOOD_API_KEY=KEY) as client:
        hold(client, FROZEN)
        refused = client.post(
            "/v1/availability",
            headers=KEY_HEADERS,
            json={"playerId": ids["player"], "status": "out"},
        )
        error = assert_error(refused, "bad_request", 400)
        assert "Demo league" in error["message"]
    with Session(seeded_engine, future=True) as session:
        assert (
            session.execute(select(func.count()).select_from(NbaIntelOverride)).scalar_one()
            == before
        )


def test_a_headline_is_pasted_once_listed_and_a_gambling_link_refused(
    live: tuple[TestClient, Engine],
) -> None:
    client, engine = live
    body = {
        "title": "Star back at practice",
        "link": "https://news.example.org/hoops/story-9?utm_source=x",
        "publishedAt": "2025-11-12T13:00:00Z",
        "sourceName": "Example Hoops Wire",
        "teamIds": ["1"],
        "playerIds": [101],
    }
    first = client.post("/v1/news/links", headers=KEY_HEADERS, json=body)
    assert first.status_code == 201, first.text
    item = first.json()["item"]
    assert first.json()["created"] is True and item["title"] == "Star back at practice"
    assert item["teams"][0]["id"] == "1" and item["players"][0]["id"] == "101"
    assert set(item) == {"itemId", "title", "link", "publishedAt", "sourceName", "teams", "players"}
    again = client.post("/v1/news/links", headers=KEY_HEADERS, json=body)
    assert again.status_code == 200 and again.json()["created"] is False
    assert again.json()["item"]["itemId"] == item["itemId"]
    assert count(engine, NbaIntelNewsItem) == 1

    listing = keyed(client, "/v1/news", teamId="1", limit=5)
    assert [i["itemId"] for i in listing["items"]] == [item["itemId"]]
    assert keyed(client, "/v1/news", teamId="2")["items"] == []
    assert keyed(client, "/v1/news", playerId=101)["items"][0]["itemId"] == item["itemId"]
    assert "never fetched or stored" in " ".join(listing["notes"])

    bad = lambda **change: client.post(
        "/v1/news/links", headers=KEY_HEADERS, json={**body, **change}
    )  # noqa: E731
    assert_error(bad(link="https://www.mozzartsport.com/story"), "bad_request", 400, "link")
    assert_error(bad(link="javascript:alert(1)"), "bad_request", 400, "link")
    assert_error(bad(title="   "), "bad_request", 400)
    assert_error(bad(teamIds=["ZZA"]), "team_not_found", 404)
    assert_error(bad(playerIds=[999999]), "player_not_found", 404)
    assert_error(bad(publishedAt="2031-01-01T00:00:00Z"), "bad_request", 400, "publishedAt")
    assert_error(
        bad(excerpt="the article text itself"), "bad_request", 400, "excerpt"
    )  # no text field
    assert count(engine, NbaIntelNewsItem) == 1


def test_model_settings_are_the_allowlist_and_a_patch_is_all_or_nothing(
    live: tuple[TestClient, Engine],
) -> None:
    client, engine = live
    listed = keyed(client, "/v1/model-settings")
    keys = [s["key"] for s in listed["settings"]]
    assert keys == list(MODEL_SETTING_KEYS) and len(keys) == 16
    by_key = {s["key"]: s for s in listed["settings"]}
    assert (
        by_key["homeAdvantagePoints"]["isDefault"] is True
        and by_key["homeAdvantagePoints"]["provenance"] == "default"
    )
    assert (
        by_key["teamSd"]["value"] is None and by_key["marginSd"]["value"] is None
    )  # never invented
    assert all(by_key[k]["setAt"] is None for k in keys)

    patched = client.patch(
        "/v1/model-settings",
        headers=KEY_HEADERS,
        json={
            "settings": [
                {"key": "homeAdvantagePoints", "value": 3.1},
                {"key": "statusChance.doubtful", "value": 0.3},
            ]
        },
    )
    assert patched.status_code == 200, patched.text
    now = {s["key"]: s for s in patched.json()["settings"]}
    assert (
        now["homeAdvantagePoints"]["value"] == 3.1
        and now["homeAdvantagePoints"]["isDefault"] is False
    )
    assert (
        now["homeAdvantagePoints"]["provenance"] == "manual"
        and now["homeAdvantagePoints"]["setAt"] == "2025-11-12T16:00:00Z"
    )
    assert now["statusChance.doubtful"]["value"] == 0.3 and now["boostCap"]["isDefault"] is True
    assert count(engine, NbaIntelModelSetting) == 2

    # Not on the allowlist: no betting number has anywhere to live.
    for key in ("totalSd", "playerSdBase", "edgeThreshold", "line", "capPolicy", ""):
        assert_error(
            client.patch(
                "/v1/model-settings",
                headers=KEY_HEADERS,
                json={"settings": [{"key": key, "value": 1.0}]},
            ),
            "bad_request",
            400,
            "key",
        )
    for key, value in (
        ("boostCap", 0.5),
        ("priorRegression", 1.5),
        ("homeAdvantagePoints", -1.0),
        ("capPolicyConsistent", 0.5),
    ):
        assert_error(
            client.patch(
                "/v1/model-settings",
                headers=KEY_HEADERS,
                json={"settings": [{"key": key, "value": value}]},
            ),
            "bad_request",
            400,
            "value",
        )
    # A status table that stops rising from out to available is refused as a whole.
    assert_error(
        client.patch(
            "/v1/model-settings",
            headers=KEY_HEADERS,
            json={"settings": [{"key": "statusChance.questionable", "value": 0.9}]},
        ),
        "bad_request",
        400,
        "value",
    )
    # Atomic: the good item beside a bad one is not applied either.
    before = count(engine, NbaIntelModelSetting)
    assert_error(
        client.patch(
            "/v1/model-settings",
            headers=KEY_HEADERS,
            json={"settings": [{"key": "boostCap", "value": 1.4}, {"key": "nope", "value": 1}]},
        ),
        "bad_request",
        400,
        "key",
    )
    assert count(engine, NbaIntelModelSetting) == before
    assert_error(
        client.patch("/v1/model-settings", headers=KEY_HEADERS, json={"settings": []}),
        "bad_request",
        400,
        "settings",
    )
    assert_error(
        client.patch(
            "/v1/model-settings",
            headers=KEY_HEADERS,
            json={
                "settings": [{"key": "boostCap", "value": 1.2}, {"key": "boostCap", "value": 1.3}]
            },
        ),
        "bad_request",
        400,
        "key",
    )
    # The body has no provenance: a value set here is manual by definition.
    assert_error(
        client.patch(
            "/v1/model-settings",
            headers=KEY_HEADERS,
            json={"settings": [{"key": "boostCap", "value": 1.2, "provenance": "fittedLedger"}]},
        ),
        "bad_request",
        400,
        "provenance",
    )
    # A setting reaches the projection that uses it.
    detail = keyed(client, "/v1/games/g7/projection")
    constants = {c["key"]: c for c in detail["current"]["model"]["constants"]}
    assert (
        constants["homeAdvantagePoints"]["value"] == 3.1
        and constants["homeAdvantagePoints"]["provenance"] == "manual"
    )
    assert detail["current"]["homeAdvantagePoints"] == 3.1


def test_the_day_one_notice_says_what_the_user_has_and_never_more(
    live: tuple[TestClient, Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/v1/sources`` prints what is available today, written from the state of the sources: with
    no injury report read it says no status is shown and everyone is projected to play; it does
    not promise a report that has not arrived, and it owns up to a reader that has never read a
    real one."""
    client, engine = live

    def notice() -> str:
        body = keyed(client, "/v1/sources")
        assert body["dayOneNotice"] == body["dayOneNotice"].strip()
        return body["dayOneNotice"]

    first = notice()
    assert first.startswith(
        "Scores, form, points allowed, defence by position and team projections"
    )
    assert "computed from the games in this store" in first
    assert "No injury report has been read yet, so no status is shown" in first
    assert "every player is projected to play" in first and "game days" in first
    assert "title, a link, a date and the outlet's name" in first
    assert "Demo league" not in first

    # A report has been read, but never confirmed against a real one on this machine.
    with Session(engine, future=True) as session:
        snapshot = intel_store.insert_snapshot(
            session,
            source_kind="leagueReport",
            fetched_at=naive(LIVE_NOW - timedelta(hours=1)),
            parse_status="ok",
            row_count=0,
            slot_at_utc=naive(LIVE_NOW - timedelta(hours=1)),
        )
        intel_store.set_source_state(
            session,
            intel_store.SOURCE_INJURY_REPORT,
            "ok",
            now=naive(LIVE_NOW - timedelta(hours=1)),
            success=True,
        )
        session.commit()
        assert snapshot.snapshot_id
    queries.clear_memo()
    read = notice()
    assert "official report, dated and never presented as live" in read
    assert "has not yet been confirmed against a real report" in read
    with Session(engine, future=True) as session:
        intel_store.record_parser_confirmation(session, now=naive(LIVE_NOW))
        session.commit()
    assert "not yet been confirmed" not in notice()

    # The reader's extra is not installed: the notice says it cannot read, and why.
    with Session(engine, future=True) as session:
        intel_store.set_source_state(
            session,
            intel_store.SOURCE_INJURY_REPORT,
            "notConfigured",
            now=naive(LIVE_NOW),
            detail={"reason": "pypdf is not installed (pip install hardwood-backend[injuries])"},
        )
        session.commit()
    assert "The injury report cannot be read here: pypdf is not installed" in notice()

    # Unreadable is said as such: nothing is guessed.
    with Session(engine, future=True) as session:
        intel_store.set_source_state(
            session,
            intel_store.SOURCE_INJURY_REPORT,
            "unreadable",
            now=naive(LIVE_NOW),
            error="its layout was not recognised",
        )
        session.commit()
    assert "The injury report is unreadable: nothing is guessed" in notice()

    monkeypatch.setenv("HARDWOOD_NBA_INJURIES", "off")
    assert "Injury statuses are off: The injury report is switched off" in notice()


# =========================================================================== the surface


MY_PATHS = (
    re.compile(r"^/v1/matchups$"),
    re.compile(r"^/v1/teams/\{teamId\}/(matchup|defense-by-position)$"),
    re.compile(r"^/v1/games/\{gameId\}/(matchup|projection)$"),
    re.compile(r"^/v1/defense-by-position$"),
    re.compile(r"^/v1/projections(/review)?$"),
    re.compile(r"^/v1/availability(/review-queue|/\{overrideId\})?$"),
    re.compile(r"^/v1/news(/links)?$"),
    re.compile(r"^/v1/sources$"),
    re.compile(r"^/v1/model-settings$"),
    re.compile(r"^/v1/leagues$"),
)


def declared_names(operation: dict[str, Any], components: dict[str, Any]) -> set[str]:
    names = {p["name"] for p in operation.get("parameters", [])}
    schema = (
        operation.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema")
    )

    def properties(node: dict[str, Any]) -> Iterator[str]:
        if "$ref" in node:
            yield from properties(components[node["$ref"].rsplit("/", 1)[-1]])
            return
        for name, child in node.get("properties", {}).items():
            yield name
            yield from properties(child)
        for child in (node.get("items"), *node.get("anyOf", []), *node.get("allOf", [])):
            if isinstance(child, dict):
                yield from properties(child)
        for key in ("additionalProperties",):
            if isinstance(node.get(key), dict):
                yield from properties(node[key])

    if schema:
        names |= set(properties(schema))
    return names


def test_every_name_these_routes_declare_is_on_the_allowed_list(seeded_client: TestClient) -> None:
    spec = seeded_client.app.openapi()  # type: ignore[attr-defined]
    components = spec["components"]["schemas"]
    covered: set[str] = set()
    for path, operations in spec["paths"].items():
        if not any(p.match(path) for p in MY_PATHS):
            continue
        for method, operation in operations.items():
            names = declared_names(operation, components)
            assert market_guard.parameter_violations(names) == [], (method, path)
            covered.add(f"{method.upper()} {path}")
    assert {
        "GET /v1/matchups",
        "POST /v1/availability",
        "DELETE /v1/availability/{overrideId}",
        "POST /v1/news/links",
        "PATCH /v1/model-settings",
        "GET /v1/leagues",
        "GET /v1/sources",
        "GET /v1/games/{gameId}/projection",
    } <= covered
    assert len(covered) == 18, sorted(covered)


def test_the_request_bodies_forbid_unknown_fields(seeded_client: TestClient) -> None:
    components = seeded_client.app.openapi()["components"]["schemas"]  # type: ignore[attr-defined]
    for name in ("AvailabilityBody", "NewsLinkBody", "ModelSettingsPatch", "SettingItem"):
        assert components[name].get("additionalProperties") is False, name


def test_the_routes_sit_behind_the_key_and_the_limiter(
    seeded_engine: Engine, ids: dict[str, Any]
) -> None:
    with api_client(HARDWOOD_API_KEY=KEY) as client:
        hold(client, FROZEN)
        for path in (
            "/v1/sources",
            "/v1/model-settings",
            "/v1/leagues",
            "/v1/availability",
            f"/v1/teams/{ids['home']}/matchup",
            "/v1/defense-by-position",
            "/v1/news",
        ):
            assert_error(client.get(path), "unauthorized", 401)
            assert client.get(path, headers=KEY_HEADERS).status_code == 200, path
    with api_client(HARDWOOD_RATE_LIMIT="3", HARDWOOD_RATE_WINDOW_SECONDS="60") as client:
        hold(client, FROZEN)
        statuses = [client.get("/v1/sources").status_code for _ in range(5)]
        assert statuses[:3] == [200, 200, 200] and statuses[3:] == [429, 429]
        limited = client.get("/v1/sources")
        assert_error(limited, "rate_limited", 429)
        assert int(limited.headers["Retry-After"]) >= 1


def test_leagues_always_has_a_row_for_the_euroleague_whatever_its_state(
    seeded_client: TestClient, seed_summary: dict[str, Any]
) -> None:
    original = league_registry.get("euroleague")
    league_registry.unregister("euroleague")
    try:
        rows = get(seeded_client, "/v1/leagues")
        nba, euro = rows
        assert nba["key"] == "nba" and nba["apiPrefix"] == "/v1" and nba["enabled"] is True
        assert (
            nba["state"] == "ready" and nba["isDemo"] is True and nba["currentSeason"] == "2025-26"
        )
        assert nba["regulationMinutes"] == 48 and nba["perModes"] == [
            "PerGame",
            "Totals",
            "Per36",
            "Per100",
        ]
        assert nba["positionBuckets"] == ["G", "F", "C"] and "matchup" in nba["features"]
        assert nba["syncVersion"] == seed_summary["sync_version"] > 0
        assert nba["dataThrough"] == str(seed_summary["data_through"])
        # Not registered: a disabled row with the reason and the units, never a missing one.
        assert (
            euro["key"] == "euroleague"
            and euro["apiPrefix"] == "/v1/el"
            and euro["enabled"] is False
        )
        assert euro["state"] == "unavailable" and euro["reason"] and euro["regulationMinutes"] == 40
        assert euro["perModes"] == ["PerGame", "Totals", "Per40"] and euro["features"] == []

        league_registry.register(
            league_registry.LeagueProvider(
                key="euroleague",
                session_factory=lambda: None,
                describe=lambda: {
                    "enabled": True,
                    "state": "ready",
                    "reason": None,
                    "isDemo": True,
                    "currentSeason": "E2026",
                    "apiPrefix": "/not/mine",
                    "key": "nba",
                },
            )
        )
        registered = get(seeded_client, "/v1/leagues")[1]
        assert registered["enabled"] is True and registered["currentSeason"] == "E2026"
        assert (
            registered["key"] == "euroleague" and registered["apiPrefix"] == "/v1/el"
        )  # cannot rename itself

        def broken() -> dict[str, Any]:
            raise RuntimeError("the store is on fire")

        league_registry.register(league_registry.LeagueProvider("euroleague", lambda: None, broken))
        failing = get(seeded_client, "/v1/leagues")[1]
        assert (
            failing["state"] == "error"
            and "on fire" in failing["reason"]
            and failing["enabled"] is False
        )
    finally:
        league_registry.unregister("euroleague")
        if original is not None:
            league_registry.register(original)


def test_an_empty_store_still_answers_the_routes_that_explain_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before any season is loaded the sources and settings routes still answer (an empty store is
    exactly when a person needs telling why), and everything that needs a season says so."""
    url = f"sqlite:///{tmp_path / 'empty.db'}"
    engine = create_db_engine(url)
    init_db(engine)
    monkeypatch.setenv("DATABASE_URL", url)
    config.reset_settings_cache()
    db_module.dispose_engine()
    try:
        with api_client() as client:
            hold(client, LIVE_NOW)
            sources = {s["key"]: s for s in get(client, "/v1/sources")["sources"]}
            assert sources["nba.stats"]["state"] == "noReportYet"
            assert sources["nba.rosters"]["state"] == "noReportYet"
            assert sources["nba.injuryReport"]["state"] == "noReportYet"
            assert len(get(client, "/v1/model-settings")["settings"]) == 16
            rows = get(client, "/v1/leagues")
            assert rows[0]["enabled"] is False and rows[0]["state"] == "notConfigured"
            assert rows[0]["features"] == [] and rows[0]["currentSeason"] is None
            assert_error(client.get("/v1/defense-by-position"), "season_not_loaded", 422)
            assert_error(client.get("/v1/projections"), "season_not_loaded", 422)
            assert_error(client.get("/v1/teams/1/matchup"), "season_not_loaded", 422)
    finally:
        engine.dispose()
        db_module.dispose_engine()
        config.reset_settings_cache()


def test_the_seeded_store_is_untouched_by_the_write_tests(seeded_engine: Engine) -> None:
    """The shared store these reads run on must hold no setting, override or link: the write tests
    use their own, and this fails loudly if one of them ever reaches the shared one."""
    with Session(seeded_engine, future=True) as session:
        for model in (NbaIntelModelSetting, NbaIntelOverride, NbaIntelNewsItem):
            assert session.execute(select(func.count()).select_from(model)).scalar_one() == 0, model
