"""End-to-end tests for the core HTTP surface (``contracts/CONTRACT.md`` §3).

Every route is exercised against the seeded database from ``conftest.py`` through FastAPI's
``TestClient``. Four things are checked harder than the rest, because they are the ones a
refactor breaks silently:

* **The key contract.** Responses are asserted against the literal ``lowerCamelCase`` strings
  in the raw body, not against a parsed model — a Swift client decodes key names, so a test
  that reads ``payload["player_id"]`` through an alias would pass while the app broke.
* **Null, never zero.** A metric the era did not record has to be *present* and ``null``.
  Both halves matter: an absent key fails to decode, and a ``0`` is a lie.
* **Fractions.** Every percentage crossing the wire is in ``[0, 1]``.
* **The error envelope.** Every failure carries a code from §7 and a request id.

The ``app_client`` fixture lives here rather than in ``conftest.py`` because it is the only
consumer; ``api_client()`` builds extra clients for the tests that need a different
environment (an API key, a tiny rate limit).
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from nbastats import config
from nbastats.api import deps, serializers
from nbastats.api.app import create_app
from nbastats.api.routes_players import fold_name
from nbastats.models import Game, Player, PlayerGameBasic, PlayerSeason

PRE_ADVANCED_SEASON = "1992-93"
CURRENT_SEASON = "2025-26"


@contextmanager
def api_client(**environment: str | None) -> Iterator[TestClient]:
    """A client for an app built with ``environment`` applied, restored afterwards.

    Settings are memoised, so both the cache and the limiter are reset on the way in and on
    the way out; ``DATABASE_URL`` is left alone, which keeps the seeded fixture database.
    """
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
    """One client over the seeded database for the whole module."""
    with api_client() as client:
        yield client


# --------------------------------------------------------------------------- helpers


def _ok(client: TestClient, url: str, **params: Any) -> dict[str, Any]:
    response = client.get(url, params=params or None)
    assert response.status_code == 200, response.text
    return response.json()


def _a_player_with_games(session: Session, season: str) -> tuple[int, str]:
    """``(player_id, game_id)`` for someone who played in ``season``."""
    row = session.execute(
        select(PlayerGameBasic.player_id, PlayerGameBasic.game_id)
        .join(Game, Game.game_id == PlayerGameBasic.game_id)
        .where(Game.season == season)
        .where(Game.season_type == "Regular Season")
        .limit(1)
    ).first()
    assert row is not None, f"the seeded database has no {season} games"
    return int(row[0]), str(row[1])


def _a_team_in_season(session: Session, season: str) -> int:
    team_id = session.execute(
        select(Game.home_team_id).where(Game.season == season).limit(1)
    ).scalar_one()
    return int(team_id)


def _career_minutes(session: Session, player_id: int) -> float:
    rows = session.execute(
        select(PlayerSeason.minutes).where(PlayerSeason.player_id == player_id)
    ).scalars()
    return sum(float(value or 0.0) for value in rows)


# --------------------------------------------------------------------------- health


def test_health_is_ok_and_uses_camel_case(app_client: TestClient) -> None:
    response = app_client.get("/v1/health")
    assert response.status_code == 200
    for key in ('"syncVersion"', '"dataThrough"', '"databaseReady"', '"seededDemoData"'):
        assert key in response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["databaseReady"] is True
    assert body["version"]
    assert body["syncVersion"] > 0
    assert body["dataThrough"] is not None
    assert body["seededDemoData"] is True  # the fixture database is the synthetic league


def test_every_response_carries_a_request_id(app_client: TestClient) -> None:
    response = app_client.get("/v1/health")
    assert response.headers["X-Request-Id"]

    supplied = app_client.get("/v1/health", headers={"X-Request-Id": "abc123"})
    assert supplied.headers["X-Request-Id"] == "abc123"

    failed = app_client.get("/v1/players/99999999")
    assert failed.json()["error"]["requestId"] == failed.headers["X-Request-Id"]


# --------------------------------------------------------------------------- meta


def test_meta_carries_league_state_and_the_catalogs(app_client: TestClient) -> None:
    response = app_client.get("/v1/meta")
    assert response.status_code == 200
    for key in ('"currentSeason"', '"seasonTypes"', '"hasAdvanced"', '"gameCount"', '"advancedFrom"'):
        assert key in response.text

    body = response.json()
    assert body["currentSeason"] == CURRENT_SEASON
    assert len(body["metrics"]["metrics"]) == 61
    assert len(body["widgets"]["widgets"]) == 13
    assert len(body["teams"]) == 30
    assert body["coverage"]["advancedFrom"] == "1996-97"
    assert body["attribution"]

    seasons = {season["season"]: season for season in body["seasons"]}
    assert CURRENT_SEASON in seasons and PRE_ADVANCED_SEASON in seasons
    assert seasons[CURRENT_SEASON]["isCurrent"] is True
    assert seasons[CURRENT_SEASON]["hasAdvanced"] is True
    assert seasons[PRE_ADVANCED_SEASON]["hasAdvanced"] is False
    assert seasons[PRE_ADVANCED_SEASON]["gameCount"] > 0
    assert "Regular Season" in seasons[PRE_ADVANCED_SEASON]["seasonTypes"]


def test_presets_are_served_from_the_catalog(app_client: TestClient) -> None:
    response = app_client.get("/v1/presets")
    assert response.status_code == 200
    assert '"schemaVersion"' in response.text and '"subjectTokens"' in response.text

    body = response.json()
    assert body["schemaVersion"] == 1
    assert isinstance(body["version"], int)
    assert len(body["presets"]) == 10
    assert {token["token"] for token in body["subjectTokens"]} >= {
        "$favorite_player",
        "$favorite_team",
    }
    first = body["presets"][0]
    assert first["presetKey"] and first["widgets"]


# --------------------------------------------------------------------------- search


def test_search_is_case_and_diacritic_insensitive(
    app_client: TestClient, seeded_db: Session
) -> None:
    name = seeded_db.execute(select(Player.full_name).limit(1)).scalar_one()
    surname = name.split()[-1]

    plain = _ok(app_client, "/v1/players/search", q=surname)
    shouted = _ok(app_client, "/v1/players/search", q=surname.upper())
    accented = _ok(
        app_client, "/v1/players/search", q=surname[0] + "́" + surname[1:]
    )
    assert [row["playerId"] for row in plain["results"]]
    assert [row["playerId"] for row in plain["results"]] == [
        row["playerId"] for row in shouted["results"]
    ]
    assert [row["playerId"] for row in plain["results"]] == [
        row["playerId"] for row in accented["results"]
    ]


def test_search_result_has_the_contract_keys(app_client: TestClient, seeded_db: Session) -> None:
    name = seeded_db.execute(select(Player.full_name).limit(1)).scalar_one()
    response = app_client.get("/v1/players/search", params={"q": name})
    assert response.status_code == 200
    for key in ('"playerId"', '"matchScore"', '"fromYear"', '"toYear"', '"isActive"', '"teamAbbr"'):
        assert key in response.text
    top = response.json()["results"][0]
    assert top["name"] == name
    assert top["matchScore"] == 1.0  # an exact name is the strongest possible match


def test_search_ranks_prefixes_before_substrings(
    app_client: TestClient, seeded_db: Session
) -> None:
    """A surname prefix must outrank a name that merely contains the query."""
    names = seeded_db.execute(select(Player.full_name)).scalars().all()
    folded = [fold_name(name) for name in names]

    needle = None
    for name in folded:
        candidate = name.split()[-1][:3]
        if len(candidate) < 3:
            continue
        starts = any(other.split()[-1].startswith(candidate) for other in folded)
        interior = any(
            candidate in other and not any(part.startswith(candidate) for part in other.split())
            for other in folded
        )
        if starts and interior:
            needle = candidate
            break
    assert needle is not None, "no seeded surname produces both a prefix and a substring match"

    results = _ok(app_client, "/v1/players/search", q=needle, limit=50)["results"]
    assert len(results) >= 2

    scores = [row["matchScore"] for row in results]
    assert scores == sorted(scores, reverse=True)

    def is_prefix(row: dict[str, Any]) -> bool:
        parts = fold_name(row["name"]).split()
        return any(part.startswith(needle) for part in parts)

    last_prefix = max(index for index, row in enumerate(results) if is_prefix(row))
    substrings = [index for index, row in enumerate(results) if not is_prefix(row)]
    if substrings:
        assert last_prefix < min(substrings)


def test_search_breaks_ties_by_active_then_career_minutes(
    app_client: TestClient, seeded_db: Session
) -> None:
    names = seeded_db.execute(select(Player.full_name)).scalars().all()
    needle = fold_name(names[0]).split()[-1][:3]
    results = _ok(app_client, "/v1/players/search", q=needle, limit=50)["results"]

    groups: dict[float, list[dict[str, Any]]] = {}
    for row in results:
        groups.setdefault(row["matchScore"], []).append(row)

    for rows in groups.values():
        actives = [row["isActive"] for row in rows]
        assert actives == sorted(actives, reverse=True), "active players rank first"
        for flag in (True, False):
            minutes = [
                _career_minutes(seeded_db, row["playerId"])
                for row in rows
                if row["isActive"] is flag
            ]
            assert minutes == sorted(minutes, reverse=True), "then career minutes"


def test_search_honours_active_only_and_limit(app_client: TestClient, seeded_db: Session) -> None:
    needle = fold_name(
        seeded_db.execute(select(Player.full_name).limit(1)).scalar_one()
    ).split()[-1][:3]
    limited = _ok(app_client, "/v1/players/search", q=needle, limit=2)
    assert len(limited["results"]) <= 2

    active = _ok(app_client, "/v1/players/search", q=needle, activeOnly=True, limit=50)
    assert all(row["isActive"] for row in active["results"])


def test_search_rejects_a_one_character_query(app_client: TestClient) -> None:
    response = app_client.get("/v1/players/search", params={"q": "a"})
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "bad_request"
    assert error["field"] == "q"


# --------------------------------------------------------------------------- player


def test_player_detail_shape(app_client: TestClient, seeded_db: Session) -> None:
    player_id, _ = _a_player_with_games(seeded_db, CURRENT_SEASON)
    response = app_client.get(f"/v1/players/{player_id}")
    assert response.status_code == 200
    for key in ('"careerTotals"', '"seasonType"', '"teamAbbr"', '"bbrefSlug"', '"fromYear"'):
        assert key in response.text

    body = response.json()
    assert body["player"]["playerId"] == player_id
    assert body["bio"]["draft"] is not None
    assert body["seasons"], "a player with games has seasons"

    seasons = [row["season"] for row in body["seasons"]]
    assert seasons == sorted(seasons, reverse=True), "newest season first"

    career = body["careerTotals"]
    assert career["season"] == "Career"
    assert career["gp"] and career["gp"] > 0
    assert career["values"]["pts"] is not None
    assert 0.0 <= career["values"]["ts_pct"] <= 1.0, "percentages are fractions"


def test_player_detail_404_uses_the_envelope(app_client: TestClient) -> None:
    response = app_client.get("/v1/players/99999999")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "player_not_found"
    assert error["recoverable"] is False
    assert error["field"] is None
    assert error["requestId"]


# --------------------------------------------------------------------------- game log


def test_gamelog_is_newest_first_and_pages_with_a_cursor(
    app_client: TestClient, seeded_db: Session
) -> None:
    player_id, _ = _a_player_with_games(seeded_db, CURRENT_SEASON)
    first = _ok(
        app_client, f"/v1/players/{player_id}/gamelog", season=CURRENT_SEASON, limit=3
    )
    assert first["season"] == CURRENT_SEASON
    assert first["seasonType"] == "Regular Season"
    assert len(first["rows"]) == 3
    assert first["nextCursor"], "a player with more than three games has a next page"

    dates = [row["date"] for row in first["rows"]]
    assert dates == sorted(dates, reverse=True)

    second = _ok(
        app_client,
        f"/v1/players/{player_id}/gamelog",
        season=CURRENT_SEASON,
        limit=3,
        cursor=first["nextCursor"],
    )
    ids_first = {row["gameId"] for row in first["rows"]}
    ids_second = {row["gameId"] for row in second["rows"]}
    assert ids_first and ids_second
    assert not (ids_first & ids_second), "a cursor page never repeats a game"
    assert max(row["date"] for row in second["rows"]) <= min(dates)


def test_gamelog_row_keys_and_metric_selection(
    app_client: TestClient, seeded_db: Session
) -> None:
    player_id, _ = _a_player_with_games(seeded_db, CURRENT_SEASON)
    response = app_client.get(
        f"/v1/players/{player_id}/gamelog",
        params={"season": CURRENT_SEASON, "limit": 1, "metrics": "pts,ts_pct"},
    )
    assert response.status_code == 200
    for key in ('"gameId"', '"opponentAbbr"', '"isHome"', '"nextCursor"', '"availability"'):
        assert key in response.text

    row = response.json()["rows"][0]
    assert set(row["values"]) == {"pts", "ts_pct"}, "values holds exactly what was asked for"
    assert row["result"] in ("W", "L", "T")
    assert "-" in row["score"]
    assert 0.0 <= row["values"]["ts_pct"] <= 1.0


def test_gamelog_nulls_era_unavailable_metrics_rather_than_zeroing_them(
    app_client: TestClient, seeded_db: Session
) -> None:
    """1992-93 has no per-game TS%, usage or plus/minus. Those must be null, not 0."""
    player_id, _ = _a_player_with_games(seeded_db, PRE_ADVANCED_SEASON)
    response = app_client.get(
        f"/v1/players/{player_id}/gamelog",
        params={
            "season": PRE_ADVANCED_SEASON,
            "limit": 1,
            "metrics": "pts,reb,ts_pct,usg_pct,plus_minus",
        },
    )
    assert response.status_code == 200
    assert '"ts_pct":null' in response.text.replace(", ", ",").replace(": ", ":")

    row = response.json()["rows"][0]
    values = row["values"]
    assert values["pts"] is not None, "points existed in 1992-93"
    assert values["reb"] is not None
    for missing in ("ts_pct", "usg_pct", "plus_minus"):
        assert missing in values, "an era-missing key is present, not omitted"
        assert values[missing] is None, "and it is null"
        assert values[missing] != 0
    assert row["availability"] == "partial"


def test_gamelog_rejects_a_bad_cursor(app_client: TestClient, seeded_db: Session) -> None:
    player_id, _ = _a_player_with_games(seeded_db, CURRENT_SEASON)
    response = app_client.get(
        f"/v1/players/{player_id}/gamelog",
        params={"season": CURRENT_SEASON, "cursor": "not-a-cursor"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["field"] == "cursor"


def test_gamelog_rejects_an_unknown_metric(app_client: TestClient, seeded_db: Session) -> None:
    player_id, _ = _a_player_with_games(seeded_db, CURRENT_SEASON)
    response = app_client.get(
        f"/v1/players/{player_id}/gamelog",
        params={"season": CURRENT_SEASON, "metrics": "pts,not_a_metric"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


# --------------------------------------------------------------------------- teams


def test_team_list_and_detail(app_client: TestClient, seeded_db: Session) -> None:
    listed = app_client.get("/v1/teams")
    assert listed.status_code == 200
    assert '"teamId"' in listed.text and '"nickname"' in listed.text
    teams = listed.json()["teams"]
    assert len(teams) == 30
    assert {team["conference"] for team in teams} == {"East", "West"}

    team_id = _a_team_in_season(seeded_db, CURRENT_SEASON)
    response = app_client.get(f"/v1/teams/{team_id}", params={"season": CURRENT_SEASON})
    assert response.status_code == 200
    assert '"roster"' in response.text and '"record"' in response.text

    body = response.json()
    assert body["team"]["teamId"] == team_id
    assert body["season"] == CURRENT_SEASON
    assert body["record"]["wins"] is not None and body["record"]["losses"] is not None
    assert 0.0 <= body["values"]["efg_pct"] <= 1.0
    assert body["values"]["off_rtg"] > 50
    assert body["roster"], "a team in a loaded season has a roster"
    assert body["roster"][0]["teamAbbr"] == body["team"]["abbr"]
    assert body["roster"][0]["values"]["pts"] is not None


def test_team_values_are_null_before_the_advanced_era(
    app_client: TestClient, seeded_db: Session
) -> None:
    team_id = _a_team_in_season(seeded_db, PRE_ADVANCED_SEASON)
    body = _ok(app_client, f"/v1/teams/{team_id}", season=PRE_ADVANCED_SEASON)
    assert body["values"]["off_rtg"] is None, "no per-game ratings before 1996-97"
    assert body["values"]["net_rtg"] is None
    assert body["values"]["efg_pct"] is not None, "but the box score is real"
    assert body["record"]["wins"] is not None


def test_team_404_uses_the_envelope(app_client: TestClient) -> None:
    response = app_client.get("/v1/teams/1")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "team_not_found"


def test_unloaded_season_is_422(app_client: TestClient, seeded_db: Session) -> None:
    team_id = _a_team_in_season(seeded_db, CURRENT_SEASON)
    response = app_client.get(f"/v1/teams/{team_id}", params={"season": "1899-00"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "season_not_loaded"


# --------------------------------------------------------------------------- games


def test_games_latest_day(app_client: TestClient) -> None:
    response = app_client.get("/v1/games", params={"date": "latest"})
    assert response.status_code == 200
    assert '"isLatestCompleted"' in response.text and '"homePts"' in response.text

    body = response.json()
    assert body["isLatestCompleted"] is True
    assert body["date"]
    assert body["games"], "the latest completed day has games"
    game = body["games"][0]
    assert game["status"] == "final"
    assert game["home"]["abbr"] and game["away"]["abbr"]
    assert game["homePts"] is not None and game["awayPts"] is not None
    assert game["date"] == body["date"]


def test_games_by_season_paginate(app_client: TestClient) -> None:
    first = _ok(app_client, "/v1/games", season=CURRENT_SEASON, limit=5)
    assert first["date"] is None, "a season query is not a single day"
    assert first["isLatestCompleted"] is False
    assert len(first["games"]) == 5
    assert first["nextCursor"]

    second = _ok(
        app_client,
        "/v1/games",
        season=CURRENT_SEASON,
        limit=5,
        cursor=first["nextCursor"],
    )
    assert not {game["gameId"] for game in first["games"]} & {
        game["gameId"] for game in second["games"]
    }


def test_games_for_one_team_only_include_that_team(
    app_client: TestClient, seeded_db: Session
) -> None:
    team_id = _a_team_in_season(seeded_db, CURRENT_SEASON)
    body = _ok(app_client, "/v1/games", season=CURRENT_SEASON, teamId=team_id, limit=10)
    assert body["games"]
    for game in body["games"]:
        assert team_id in (game["home"]["teamId"], game["away"]["teamId"])


def test_box_score_views(app_client: TestClient, seeded_db: Session) -> None:
    _, game_id = _a_player_with_games(seeded_db, CURRENT_SEASON)

    basic = _ok(app_client, f"/v1/games/{game_id}/box", view="basic")
    assert len(basic["teams"]) == 2
    line = basic["teams"][0]["players"][0]
    assert "pts" in line["values"] and "ts_pct" not in line["values"]
    assert line["player"]["playerId"]

    advanced = _ok(app_client, f"/v1/games/{game_id}/box", view="advanced")
    assert "pts" not in advanced["teams"][0]["players"][0]["values"]
    assert "ts_pct" in advanced["teams"][0]["players"][0]["values"]

    both = app_client.get(f"/v1/games/{game_id}/box")
    assert both.status_code == 200
    assert '"availability"' in both.text and '"minutes"' in both.text
    body = both.json()
    assert body["game"]["gameId"] == game_id
    player = body["teams"][0]["players"][0]
    assert player["values"]["pts"] is not None
    assert 0.0 <= player["values"]["ts_pct"] <= 1.0
    assert body["teams"][0]["values"]["off_rtg"] > 50


def test_box_score_before_the_advanced_era_is_null_not_zero(
    app_client: TestClient, seeded_db: Session
) -> None:
    _, game_id = _a_player_with_games(seeded_db, PRE_ADVANCED_SEASON)
    body = _ok(app_client, f"/v1/games/{game_id}/box", view="both")
    player = body["teams"][0]["players"][0]
    assert player["values"]["pts"] is not None
    assert player["values"]["ts_pct"] is None
    assert player["values"]["off_rtg"] is None
    assert player["availability"] in ("partial", "unavailable")
    assert body["teams"][0]["values"]["pace"] is None


def test_box_score_404_and_bad_view(app_client: TestClient, seeded_db: Session) -> None:
    missing = app_client.get("/v1/games/0000000000/box")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "game_not_found"

    _, game_id = _a_player_with_games(seeded_db, CURRENT_SEASON)
    bad = app_client.get(f"/v1/games/{game_id}/box", params={"view": "sideways"})
    assert bad.status_code == 400
    assert bad.json()["error"]["field"] == "view"


# --------------------------------------------------------------------------- sync


def test_sync_reports_changes_then_nothing(app_client: TestClient) -> None:
    response = app_client.get("/v1/sync")
    assert response.status_code == 200
    for key in ('"syncVersion"', '"hasChanges"', '"changedDates"', '"nextPollAfterSeconds"'):
        assert key in response.text

    body = response.json()
    assert body["hasChanges"] is True, "a client with no version has everything to learn"
    assert body["previousVersion"] is None
    assert body["serverTime"].endswith("Z")
    assert body["changedDates"] and body["finalizedGames"]
    assert body["affectedPlayerIds"]
    assert "scoreboard" in body["invalidate"]

    current = body["syncVersion"]
    quiet = _ok(app_client, "/v1/sync", since=current)
    assert quiet["hasChanges"] is False
    assert quiet["previousVersion"] == current
    assert quiet["syncVersion"] == current
    assert quiet["changedDates"] == []
    assert quiet["finalizedGames"] == []
    assert quiet["invalidate"] == []


def test_sync_stream_emits_an_initial_sync_event(app_client: TestClient) -> None:
    with app_client.stream(
        "GET", "/v1/sync/stream", params={"maxDurationSeconds": 2}
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        event = None
        payload = None
        for line in response.iter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                payload = json.loads(line.split(":", 1)[1])
                break
    assert event == "sync"
    assert payload is not None and payload["syncVersion"] > 0
    assert payload["serverTime"].endswith("Z")


# --------------------------------------------------------------------------- gates


def test_no_api_key_is_required_by_default(app_client: TestClient) -> None:
    assert app_client.get("/v1/teams").status_code == 200


def test_api_key_is_enforced_when_configured(seeded_engine: Engine) -> None:
    with api_client(HARDWOOD_API_KEY="s3cret") as client:
        assert client.get("/v1/health").status_code == 200, "health is never gated"

        missing = client.get("/v1/teams")
        assert missing.status_code == 401
        assert missing.json()["error"]["code"] == "unauthorized"

        wrong = client.get("/v1/teams", headers={"X-API-Key": "nope"})
        assert wrong.status_code == 401

        right = client.get("/v1/teams", headers={"X-API-Key": "s3cret"})
        assert right.status_code == 200


def test_rate_limiter_returns_429_with_retry_after(seeded_engine: Engine) -> None:
    with api_client(HARDWOOD_RATE_LIMIT="2", HARDWOOD_RATE_WINDOW_SECONDS="60") as client:
        assert client.get("/v1/teams").status_code == 200
        assert client.get("/v1/teams").status_code == 200

        limited = client.get("/v1/teams")
        assert limited.status_code == 429
        assert int(limited.headers["Retry-After"]) >= 1
        error = limited.json()["error"]
        assert error["code"] == "rate_limited"
        assert error["recoverable"] is True

        assert client.get("/v1/health").status_code == 200, "health is never limited"


def test_the_package_resolves_its_lazy_entry_points() -> None:
    """``from nbastats.api import create_app`` is documented, so it has to work.

    ``nbastats/api/__init__.py`` keeps FastAPI out of the import path of the pure-Python
    modules by resolving its two public names in ``__getattr__``. Doing that with
    ``from . import app`` made the package probe itself with ``hasattr`` and recurse, which no
    other test caught because every one of them imports ``nbastats.api.app`` directly.
    """
    import importlib

    package = importlib.import_module("nbastats.api")
    assert package.create_app.__name__ == "create_app"
    # The ASGI callable is `nbastats.api.app:app`; the package attribute is the module.
    assert package.app.__name__ == "nbastats.api.app"
    assert package.app.app is package.app.create_app.__globals__["app"]
    with pytest.raises(AttributeError):
        package.not_a_public_name


def test_optional_widget_routers_are_optional(seeded_engine: Engine, monkeypatch) -> None:
    """The service starts whether or not the widget layer's modules exist yet."""
    from nbastats.api import app as app_module

    monkeypatch.setattr(
        app_module, "OPTIONAL_ROUTE_MODULES", ("routes_dashboard", "routes_not_written_yet")
    )
    with TestClient(app_module.create_app()) as client:
        assert client.get("/v1/health").status_code == 200
        assert client.get("/v1/teams").status_code == 200

    # …and a module that *is* present gets its router mounted under /v1.
    monkeypatch.setattr(app_module, "OPTIONAL_ROUTE_MODULES", ("routes_teams",))
    with TestClient(app_module.create_app()) as client:
        assert client.get("/v1/teams").status_code == 200


def test_demo_mode_does_not_reseed_a_populated_database(app_client: TestClient) -> None:
    """``HARDWOOD_DEMO_MODE`` seeds only an empty store; it never clobbers real rows."""
    before = _ok(app_client, "/v1/meta")["seasons"]
    with api_client(HARDWOOD_DEMO_MODE="1") as client:
        after = _ok(client, "/v1/meta")["seasons"]
        assert client.get("/v1/health").json()["seededDemoData"] is True
    assert [row["gameCount"] for row in after] == [row["gameCount"] for row in before]


def test_unknown_path_still_uses_the_envelope(app_client: TestClient) -> None:
    response = app_client.get("/v1/not-a-route")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["requestId"]


# --------------------------------------------------------------------------- serializer


def test_metric_value_is_era_honest() -> None:
    """The one place a number becomes a rendered value: it must never invent one."""
    modern = serializers.metric_value("ts_pct", 0.6153, 12, 0.93, 0.5671, 0.0482, CURRENT_SEASON)
    assert modern.value == 0.6153
    assert modern.display_value == "61.5%"
    assert modern.availability == "full"
    assert modern.is_estimated is False
    assert modern.rank == 12

    ancient = serializers.metric_value("off_rtg", 110.0, season="1962-63")
    assert ancient.value is None, "a rating that did not exist is dropped, not served"
    assert ancient.display_value == "—"
    assert ancient.availability == "unavailable"
    assert ancient.rank is None

    estimated = serializers.metric_value("usg_pct", 0.28, season="1992-93")
    assert estimated.availability == "estimated"
    assert estimated.is_estimated is True
    assert 0.0 <= estimated.value <= 1.0

    per_game = serializers.metric_value("ts_pct", 0.6, season="1992-93", granularity="game")
    assert per_game.value is None, "no per-game TS% before 1996-97"

    missing = serializers.metric_value("pts", None, season=CURRENT_SEASON)
    assert missing.value is None
    assert missing.display_value == "—"
    assert missing.availability == "unavailable"


def test_camel_case_only_on_the_wire(app_client: TestClient, seeded_db: Session) -> None:
    """No snake_case field name may reach the client; metric keys inside `values` may."""
    player_id, game_id = _a_player_with_games(seeded_db, CURRENT_SEASON)
    team_id = _a_team_in_season(seeded_db, CURRENT_SEASON)
    paths = [
        "/v1/health",
        "/v1/meta",
        "/v1/teams",
        f"/v1/teams/{team_id}",
        f"/v1/players/{player_id}",
        f"/v1/players/{player_id}/gamelog?season={CURRENT_SEASON}&limit=1",
        "/v1/games?date=latest",
        f"/v1/games/{game_id}/box",
        "/v1/sync",
    ]
    forbidden = (
        '"player_id"',
        '"team_id"',
        '"team_abbr"',
        '"game_id"',
        '"season_type"',
        '"display_value"',
        '"sync_version"',
        '"data_through"',
        '"is_home"',
        '"next_cursor"',
        '"headshot_url"',
        '"first_name"',
    )
    for path in paths:
        response = app_client.get(path)
        assert response.status_code == 200, path
        for key in forbidden:
            assert key not in response.text, f"{key} leaked from {path}"
