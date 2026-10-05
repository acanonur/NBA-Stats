"""``/v1/el``: every route, its errors, its state gate and its writes.

The router is mounted on a small application of its own (with the same error envelope the real one
installs), pointed at a throwaway copy of the invented league with the clock held at the demo's
"now". When ``api/app.py`` mounts it (a later package), nothing here changes: the routes are the
same objects.

What is held
------------
* the route table is the design's (section 9.3), method for method;
* every parameter name is on the allowed list, so no route can take a number to compare a
  projection with (the OpenAPI document is walked, not trusted);
* an unusable EuroLeague answers ``503 league_unavailable`` with the reason *before the store is
  opened*, except ``/meta`` and ``/health``, which answer in every state because the state is
  their message;
* writes need the API key (or a session, which this app does not have) and validate before they
  touch a row; a status's unknown word is ``400 invalid_status``;
* no payload carries a non-finite number, a forbidden key word or a leaked NBA identifier.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from nbastats import config as stats_config
from nbastats import db as stats_db
from nbastats.api.errors import install_error_handlers
from nbastats.euroleague import bootstrap
from nbastats.euroleague.api import deps
from nbastats.euroleague.api.routes import router
from nbastats.euroleague.read import availability as _availability
from nbastats.euroleague.db import dispose_el_engine
from nbastats.euroleague.demo import DEMO_AS_OF
from nbastats.euroleague.models import (
    ElGame,
    ElIntelNewsFeed,
    ElIntelNewsItem,
    ElIntelStatus,
    ElModelSetting,
    ElPlayerGame,
    ElPlayerRate,
)
from nbastats.euroleague.read.queries import clear_memo
from nbastats.shared import league_registry
from nbastats.shared.market_guard import ALLOWED_PARAMETERS, parameter_violations, scan_keys

NOW = DEMO_AS_OF.replace(tzinfo=timezone.utc)
KEY = "test-key-123"
HEADERS = {"X-API-Key": KEY}

EXPECTED_ROUTES = {
    ("GET", "/el/meta"),
    ("GET", "/el/health"),
    ("GET", "/el/sync"),
    ("GET", "/el/teams"),
    ("GET", "/el/teams/{clubCode}"),
    ("GET", "/el/rounds/{round}"),
    ("GET", "/el/rounds/{round}/scorers"),
    ("GET", "/el/games"),
    ("GET", "/el/games/{gameId}"),
    ("GET", "/el/players/{personCode}"),
    ("GET", "/el/players/{personCode}/gamelog"),
    ("GET", "/el/stats/players"),
    ("GET", "/el/ratings"),
    ("GET", "/el/method"),
    ("GET", "/el/review-queue"),
    ("GET", "/el/matchups"),
    ("GET", "/el/teams/{clubCode}/matchup"),
    ("GET", "/el/games/{gameId}/matchup"),
    ("GET", "/el/teams/{clubCode}/defense-by-position"),
    ("GET", "/el/defense-by-position"),
    ("GET", "/el/projections"),
    ("GET", "/el/games/{gameId}/projection"),
    ("GET", "/el/projections/review"),
    ("GET", "/el/availability"),
    ("GET", "/el/availability/review-queue"),
    ("POST", "/el/availability"),
    ("DELETE", "/el/availability/{statusId}"),
    ("GET", "/el/news"),
    ("POST", "/el/news/links"),
    ("GET", "/el/sources"),
    ("GET", "/el/model-settings"),
    ("PATCH", "/el/model-settings"),
}


@pytest.fixture(autouse=True)
def _statuses_writable_in_the_demo_store(monkeypatch):
    """The invented demo store refuses a hand-entered status; these tests exercise the write
    path itself, so the one refusal is lifted here (it is tested in
    ``test_availability_in_force.py`` and below with the real function)."""
    real = _availability.statuses_allowed
    monkeypatch.setattr(_availability, "statuses_allowed", lambda session: True)
    return real


@pytest.fixture()
def app(fresh_demo_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("HARDWOOD_EL_DATABASE_URL", str(fresh_demo_engine.url))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'nba.db'}")
    monkeypatch.setenv("HARDWOOD_API_KEY", KEY)
    stats_config.reset_settings_cache()
    stats_db.dispose_engine()
    dispose_el_engine()
    clear_memo()
    league_registry.clear()
    bootstrap.set_state(bootstrap.BootstrapResult(state="ready", kind="synthetic", is_demo=True))
    application = FastAPI()
    install_error_handlers(application)
    application.include_router(router, prefix="/v1")
    application.dependency_overrides[deps.get_now] = lambda: NOW
    yield application
    bootstrap.reset_state()
    league_registry.clear()
    stats_config.reset_settings_cache()
    stats_db.dispose_engine()


@pytest.fixture()
def client(app):
    return TestClient(app)


def get(client, path, status=200, **kwargs):
    response = client.get("/v1/el" + path, **kwargs)
    assert response.status_code == status, (path, response.status_code, response.text[:400])
    return response.json()


def error_of(response, code, status):
    assert response.status_code == status, response.text[:300]
    body = response.json()["error"]
    assert body["code"] == code
    return body


# --------------------------------------------------------------------------- the table


def test_the_route_table_is_the_designs(app) -> None:
    # FastAPI keeps included routers as placeholders, so the table is read from the OpenAPI document.
    document = app.openapi()
    seen = {
        (method.upper(), path.removeprefix("/v1"))
        for path, operations in document["paths"].items()
        for method in operations
    }
    assert seen == EXPECTED_ROUTES
    assert all(path.startswith("/v1/el/") for path in document["paths"])


def test_every_parameter_name_is_on_the_allowed_list(app) -> None:
    document = app.openapi()
    names: set[str] = set()

    def properties(schema):
        if "$ref" in schema:
            schema = document["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]]
        return set(schema.get("properties", {}))

    for path, methods in document["paths"].items():
        assert path.startswith("/v1/el/")
        for operation in methods.values():
            names.update(p["name"] for p in operation.get("parameters", []))
            body = operation.get("requestBody")
            if body:
                names.update(properties(body["content"]["application/json"]["schema"]))
    # Nested body objects (the settings list) are checked through the component schemas.
    for name, schema in document["components"]["schemas"].items():
        if name in ("AvailabilityBody", "NewsLinkBody", "ModelSettingsPatch", "SettingItem"):
            names.update(schema.get("properties", {}))
    assert parameter_violations(names) == [], sorted(names - ALLOWED_PARAMETERS)


def test_no_route_takes_a_number_to_compare_with_a_projection(app) -> None:
    for path, methods in app.openapi()["paths"].items():
        for operation in methods.values():
            for parameter in operation.get("parameters", []):
                assert not re.search(
                    r"line|odds|edge|pick|spread|prob|lean|over|under", parameter["name"], re.I
                ), (path, parameter)


# --------------------------------------------------------------------------- the reads


READS = [
    "/meta",
    "/health",
    "/sync",
    "/teams",
    "/teams/ZZA",
    "/rounds/5",
    "/rounds/4",
    "/rounds/5/scorers?perClub=2",
    "/games",
    "/games?round=5&clubCode=ZZA",
    "/games/E2026-0021",
    "/games/E2026-R05-01",
    "/players/demo-1",
    "/players/demo-1/gamelog",
    "/stats/players",
    "/stats/players?perMode=Per40&sort=ast&clubCode=ZZA&minGames=2",
    "/stats/players?perMode=Totals&limit=5",
    "/ratings",
    "/ratings?asOfRound=3",
    "/method",
    "/review-queue",
    "/matchups?homeTeamId=ZZA&awayTeamId=ZZB&window=3",
    "/teams/ZZA/matchup",
    "/games/E2026-0021/matchup",
    "/teams/ZZA/defense-by-position",
    "/teams/ZZA/defense-by-position?scheme=workbook5&basis=perMinute&window=2",
    "/defense-by-position",
    "/projections",
    "/projections?round=4",
    "/games/E2026-R05-01/projection",
    "/games/E2026-0030/projection",
    "/projections/review",
    "/projections/review?round=3",
    "/availability",
    "/availability?clubCode=ZZP&includeNews=true",
    "/availability?round=5&statuses=out,doubtful",
    "/availability/review-queue",
    "/news",
    "/news?teamId=ZZA&limit=3",
    "/sources",
    "/model-settings",
]


def walk(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k, v
            yield from walk(v)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


@pytest.mark.parametrize("path", READS)
def test_every_read_answers_with_clean_json(client, path) -> None:
    body = get(client, path)
    assert body["league"] == "euroleague"
    assert scan_keys(body) == []
    for key, value in walk(body):
        if isinstance(value, float):
            assert math.isfinite(value), (path, key)
    text = client.get("/v1/el" + path).text
    assert "NaN" not in text and "Infinity" not in text
    assert not re.search(r"1610612\d{3}|\"00\d{8}\"", text)  # no NBA identifier
    if path not in ("/health", "/sync"):
        if "freshness" in body and body["freshness"] is not None:
            assert (
                body["freshness"]["isDemo"] is True and body["freshness"]["league"] == "euroleague"
            )


def test_reads_are_deterministic(client) -> None:
    for path in ("/rounds/5", "/teams/ZZA", "/availability", "/stats/players"):
        assert client.get("/v1/el" + path).json() == client.get("/v1/el" + path).json()


def test_meta_tells_the_user_what_to_expect(client) -> None:
    meta = get(client, "/meta")
    assert meta["state"] == "ready" and meta["isDemo"] is True and meta["mode"] == "demo"
    assert meta["currentSeason"] == "2026-27" and meta["currentSeasonCode"] == "E2026"
    assert [r["status"] for r in meta["rounds"]] == [
        "complete",
        "complete",
        "complete",
        "complete",
        "upcoming",
    ]
    assert len(meta["clubs"]) == 20 and meta["regulationMinutes"] == 40
    assert meta["perModes"] == ["PerGame", "Totals", "Per40"] and meta["positionBuckets"] == [
        "G",
        "F",
        "C",
    ]
    assert meta["unverifiedClubCodes"] == []  # the demo's invented codes are not in the crosswalk
    assert "Demo league" in meta["dayOneNotice"]
    assert meta["dataThrough"] == "2026-10-09"


def test_a_round_nobody_loaded_the_results_for_is_not_upcoming(client, app) -> None:
    app.dependency_overrides[deps.get_now] = lambda: datetime(
        2026, 10, 17, 6, 0, tzinfo=timezone.utc
    )
    round_view = get(client, "/rounds/5")
    assert round_view["status"] == "resultPending"
    assert (
        "Round 5 results are not loaded: enable live ingest or import an updated workbook."
        in round_view["notes"]
    )
    assert {g["game"]["status"] for g in round_view["games"]} == {"resultPending"}
    meta = get(client, "/meta")
    assert meta["rounds"][-1]["status"] == "resultPending"
    assert (
        get(client, "/projections")["round"] == 5
    )  # nothing is scheduled, so the pending round is "next"


def test_the_season_parameter_takes_either_spelling_and_refuses_the_rest(client) -> None:
    assert get(client, "/teams?season=E2026")["seasonCode"] == "E2026"
    assert get(client, "/teams?season=2026-27")["season"] == "2026-27"
    assert get(client, "/teams?season=latest")["seasonCode"] == "E2026"
    error_of(client.get("/v1/el/teams?season=2026-28"), "bad_request", 400)
    error_of(client.get("/v1/el/teams?season=NBA"), "bad_request", 400)
    error_of(client.get("/v1/el/teams?season=E2025"), "season_not_loaded", 422)


@pytest.mark.parametrize(
    "path,code,status",
    [
        ("/teams/NOPE", "club_not_found", 404),
        ("/teams/NOPE/matchup", "club_not_found", 404),
        ("/teams/NOPE/defense-by-position", "club_not_found", 404),
        ("/games/E2026-9999", "game_not_found", 404),
        ("/games/E2026-9999/matchup", "game_not_found", 404),
        ("/games/E2026-9999/projection", "game_not_found", 404),
        ("/players/nobody", "player_not_found", 404),
        ("/rounds/99", "bad_request", 400),
        ("/rounds/99/scorers", "bad_request", 400),
        ("/games?round=99", "bad_request", 400),
        ("/games?round=0", "bad_request", 400),
        ("/projections?round=zero", "bad_request", 400),
        ("/projections?round=99", "bad_request", 400),
        ("/projections/review?round=99", "bad_request", 400),
        ("/stats/players?perMode=Per36", "bad_request", 400),
        ("/stats/players?sort=lines", "bad_request", 400),
        ("/stats/players?limit=0", "bad_request", 400),
        ("/stats/players?clubCode=NOPE", "club_not_found", 404),
        ("/matchups?homeTeamId=ZZA&awayTeamId=ZZA", "bad_request", 400),
        ("/matchups?homeTeamId=ZZA", "bad_request", 400),
        ("/matchups?homeTeamId=ZZA&awayTeamId=ZZB&window=2", "bad_request", 400),
        ("/teams/ZZA/defense-by-position?basis=perShot", "bad_request", 400),
        ("/teams/ZZA/defense-by-position?scheme=six", "bad_request", 400),
        ("/teams/ZZA/defense-by-position?window=-1", "bad_request", 400),
        ("/teams/ZZA/defense-by-position?phase=XX", "bad_request", 400),
        ("/availability?statuses=maybe", "invalid_status", 400),
        ("/availability?clubCode=NOPE", "club_not_found", 404),
        ("/availability?round=99", "bad_request", 400),
        ("/ratings?asOfRound=1", "bad_request", 400),
        ("/news?teamId=NOPE", "club_not_found", 404),
        ("/news?limit=999", "bad_request", 400),
        ("/rounds/5/scorers?perClub=0", "bad_request", 400),
        ("/rounds/5/scorers?perClub=16", "bad_request", 400),
    ],
)
def test_errors_use_the_envelope_and_the_designs_codes(client, path, code, status) -> None:
    error_of(client.get("/v1/el" + path), code, status)


def test_a_club_code_is_case_insensitive_in_the_path(client) -> None:
    assert get(client, "/teams/zza")["team"]["id"] == "ZZA"


# --------------------------------------------------------------------------- the null rule, end to end


def test_a_stat_the_workbook_shaped_games_never_recorded_is_averaged_over_the_games_that_did(
    client, fresh_demo_engine
) -> None:
    with Session(fresh_demo_engine, future=True) as session:
        games = {g.game_id: g for g in session.execute(select(ElGame)).scalars()}
        by_player: dict[str, list[ElPlayerGame]] = {}
        for line in session.execute(
            select(ElPlayerGame).where(ElPlayerGame.participation == "played")
        ).scalars():
            by_player.setdefault(line.person_code, []).append(line)
        person = next(
            p
            for p, lines in by_player.items()
            if {games[l.game_id].round_number <= 2 for l in lines} == {True, False}
            and len(lines) >= 3
        )
        lines = by_player[person]
    row = next(
        r
        for r in get(client, "/stats/players?limit=500&minGames=1")["rows"]
        if r["player"]["id"] == person
    )
    carrying = [l for l in lines if l.fouls_drawn is not None]
    assert 0 < len(carrying) < len(lines), "rounds 1 and 2 carry no fouls drawn; rounds 3 and 4 do"
    assert row["games"] == len(lines)
    assert row["gamesWithStat"]["fouls_drawn"] == len(carrying)
    assert row["values"]["fouls_drawn"] == pytest.approx(
        sum(l.fouls_drawn for l in carrying) / len(carrying)
    )
    assert row["values"]["fouls_drawn"] != pytest.approx(
        sum(l.fouls_drawn for l in carrying) / len(lines)
    )
    assert row["gamesWithStat"]["pts"] == len(lines)
    totals = next(
        r
        for r in get(client, "/stats/players?limit=500&perMode=Totals")["rows"]
        if r["player"]["id"] == person
    )
    assert totals["values"]["fouls_drawn"] == sum(l.fouls_drawn for l in carrying)


def test_a_player_who_never_shot_a_three_has_no_three_point_percentage(
    client, fresh_demo_engine
) -> None:
    with Session(fresh_demo_engine, future=True) as session:
        person = (
            session.execute(
                select(ElPlayerGame.person_code)
                .where(ElPlayerGame.participation == "played")
                .group_by(ElPlayerGame.person_code)
                .having(func_sum(ElPlayerGame.fga3) == 0)
            )
            .scalars()
            .first()
        )
    if person is None:
        pytest.skip("every demo player attempted a three")
    row = next(
        r for r in get(client, "/stats/players?limit=500")["rows"] if r["player"]["id"] == person
    )
    assert row["values"]["fg3_pct"] is None and row["values"]["fg3a"] == 0


def func_sum(column):
    from sqlalchemy import func

    return func.coalesce(func.sum(column), 0)


def test_dnp_lines_are_null_in_the_box_score_and_do_not_count_as_games(
    client, fresh_demo_engine
) -> None:
    with Session(fresh_demo_engine, future=True) as session:
        played = {
            code
            for (code,) in session.execute(
                select(ElPlayerGame.person_code).where(ElPlayerGame.participation == "played")
            )
        }
        dnp = next(
            (
                l
                for l in session.execute(
                    select(ElPlayerGame)
                    .where(ElPlayerGame.participation == "dnp")
                    .order_by(ElPlayerGame.game_id, ElPlayerGame.person_code)
                ).scalars()
                if l.person_code in played
            ),
            None,
        )
        assert (
            dnp is not None
        ), "the demo has a player who was listed in one game and played in another"
    box = get(client, f"/games/{dnp.game_id}")
    players = [p for team in box["teams"] for p in team["players"]]
    entry = next(p for p in players if p["player"]["id"] == dnp.person_code)
    assert entry["participation"] == "dnp" and entry["stats"] is None and entry["isStarter"] is None
    log = get(client, f"/players/{dnp.person_code}/gamelog")
    row = next(g for g in log["games"] if g["game"]["gameId"] == dnp.game_id)
    assert row["participation"] == "dnp" and row["stats"] is None
    stats = next(
        r
        for r in get(client, "/stats/players?limit=500&minGames=0")["rows"]
        if r["player"]["id"] == dnp.person_code
    )
    played = [g for g in log["games"] if g["participation"] == "played"]
    assert stats["games"] == len(played)


def test_a_box_score_has_totals_partials_and_percentages_as_fractions(client) -> None:
    box = get(client, "/games/E2026-0030")
    assert box["statsStatus"] == "ok" and box["sourceRef"]["kind"] == "demo"
    assert box["game"]["status"] == "final" and len(box["teams"]) == 2
    assert box["partials"] is None or len(box["partials"]["home"]) >= 4
    for team in box["teams"]:
        totals = team["totals"]
        assert totals["minutes"] == pytest.approx(200.0, abs=0.1) or totals[
            "minutes"
        ] == pytest.approx(225.0, abs=0.1)
        played = [p["stats"] for p in team["players"] if p["stats"]]
        assert totals["pts"] == sum(s["pts"] for s in played)
        for stats in played:
            for key in ("fg2Pct", "fg3Pct", "ftPct"):
                assert stats[key] is None or 0.0 <= stats[key] <= 1.0
            assert (stats["fg3Pct"] is None) == (not stats["fga3"])
    upcoming = get(client, "/games/E2026-R05-01")
    assert upcoming["teams"] == [] and any("No box score" in n for n in upcoming["notes"])
    assert upcoming["partials"] is None and upcoming["attendance"] is None


def test_a_quarantined_game_shows_its_result_and_no_lines(client, fresh_demo_engine) -> None:
    with Session(fresh_demo_engine, future=True) as session:
        session.execute(
            update(ElGame).where(ElGame.game_id == "E2026-0030").values(stats_status="quarantined")
        )
        session.commit()
    box = get(client, "/games/E2026-0030")
    assert box["teams"] == [] and box["game"]["homePts"] is not None
    assert any("did not agree" in n for n in box["notes"])
    assert (
        get(client, "/games/E2026-0030/matchup")["game"]["status"] == "final"
    )  # the game is still a result


def test_an_estimated_rate_is_labelled_and_never_shown_as_official(
    client, fresh_demo_engine
) -> None:
    with Session(fresh_demo_engine, future=True) as session:
        rate = (
            session.execute(select(ElPlayerRate).order_by(ElPlayerRate.person_code))
            .scalars()
            .first()
        )
        session.execute(
            update(ElPlayerRate)
            .where(ElPlayerRate.person_code == rate.person_code)
            .values(basis="workbookEstimate", prior_minutes=150.0)
        )
        session.commit()
        person, club = rate.person_code, rate.club_code
    squad = get(client, f"/teams/{club}")["squad"]
    row = next(r for r in squad if r["player"]["id"] == person)
    assert row["basis"] == "workbookEstimate" and row["isEstimate"] is True
    assert row["basisNote"] == "your estimate; source not recorded"
    official = next(r for r in squad if r["player"]["id"] != person)
    assert official["basisNote"] is None and official["isEstimate"] is False


def test_the_club_view_is_the_workbooks_team_and_squad_sheets(client) -> None:
    view = get(client, "/teams/ZZA")
    assert (
        view["team"]["clubCode"] == "ZZA"
        and view["coach"]
        and view["nextGame"]["gameId"] == "E2026-R05-01"
    )
    assert view["record"] == {
        "wins": view["scoring"]["record"]["wins"],
        "losses": view["scoring"]["record"]["losses"],
    }
    rating = view["rating"]
    assert rating["asOfRound"] == 4 and rating["attackIndex"] == pytest.approx(
        rating["projectedPointsFor"] / 84.5035
    )
    assert 14 <= len(view["squad"]) <= 18
    assert sum(r["projectedMinutes"] for r in view["squad"]) == pytest.approx(200.0, abs=1e-6)
    assert all(
        r["seasonAverages"] is None or r["seasonAverages"]["games"] >= 1 for r in view["squad"]
    )
    assert view["defenseSummary"]["buckets"][0]["position"] == "G"


def test_teams_and_ratings_tables(client) -> None:
    teams = get(client, "/teams")["teams"]
    assert [t["team"]["id"] for t in teams] == sorted(t["team"]["id"] for t in teams) and len(
        teams
    ) == 20
    assert all(t["games"] == 4 and t["rating"]["asOfRound"] == 4 for t in teams)
    ratings = get(client, "/ratings")
    assert (
        ratings["asOfRound"] == 4
        and ratings["baseRound"] == 2
        and ratings["leagueAveragePoints"] == pytest.approx(84.5035)
    )
    assert len(ratings["rows"]) == 20 and len(ratings["updates"]) == 40
    third = get(client, "/ratings?asOfRound=3")
    assert third["asOfRound"] == 3 and len(third["updates"]) == 20
    assert {r["updateWeight"] for r in third["rows"]} == {1 / 12}


def test_the_method_page_lists_constants_with_provenance_and_the_deviations(client) -> None:
    method = get(client, "/method")
    keys = {c["key"]: c for c in method["constants"]}
    assert keys["teamSd"]["provenance"] == "workbookUnvalidated"
    assert (
        keys["priorRegression"]["value"] == 0.3
        and keys["leagueAveragePoints"]["provenance"] == "derived"
    )
    assert (
        keys["formGames"]["value"] == 10
        and "roundWeight.10" in keys
        and "roundWeight.11" not in keys
    )
    assert any("regulation length" in d for d in method["deviations"])
    assert any("consistent" in d for d in method["deviations"])
    assert any("not live" in l for l in method["limitations"])
    assert any("who guarded whom" in l or "guarded" in l for l in method["limitations"])


def test_the_sources_panel_for_a_demo_league(client) -> None:
    sources = get(client, "/sources")
    states = {s["key"]: s["state"] for s in sources["sources"]}
    assert states["el.workbook"] == "disabled" and states["el.dataService"] == "disabled"
    assert states["el.manual"] == "ok"
    assert sources["store"]["kind"] == "synthetic"
    assert all(
        set(s)
        >= {
            "key",
            "label",
            "kind",
            "enabled",
            "state",
            "reason",
            "lastSuccessAt",
            "lastError",
            "robotsCheckedOn",
            "attribution",
        }
        for s in sources["sources"]
    )
    assert not [k for k in walk(sources) if k[0] in ("termsReviewedOn", "termsUrl")]


# --------------------------------------------------------------------------- the state gate


def test_an_unusable_euroleague_answers_503_before_the_store_is_opened(client, monkeypatch) -> None:
    bootstrap.set_state(
        bootstrap.BootstrapResult(state="misconfigured", reason="Both leagues share one file.")
    )

    def boom():  # the store must not be touched
        raise AssertionError("the store was opened")

    monkeypatch.setattr(deps, "get_el_sessionmaker", boom)
    for path in (
        "/teams",
        "/teams/ZZA",
        "/rounds/5",
        "/games",
        "/availability",
        "/news",
        "/sources",
        "/method",
        "/model-settings",
        "/defense-by-position",
        "/projections",
        "/sync",
        "/stats/players",
    ):
        body = error_of(client.get("/v1/el" + path), "league_unavailable", 503)
        assert body["message"] == "Both leagues share one file." and body["recoverable"] is True
    for method, path, payload in (
        ("post", "/availability", {}),
        ("post", "/news/links", {}),
        ("patch", "/model-settings", {}),
        ("delete", "/availability/1", None),
    ):
        response = getattr(client, method)(
            "/v1/el" + path, headers=HEADERS, **({"json": payload} if payload is not None else {})
        )
        assert response.status_code in (400, 503, 422), response.text


def test_meta_and_health_answer_in_every_state(client, monkeypatch) -> None:
    for state, reason in (
        ("disabled", "HARDWOOD_EL_ENABLED is off"),
        ("notConfigured", "No source is on."),
        ("error", "boom"),
        ("notPrepared", "not prepared yet"),
    ):
        bootstrap.set_state(bootstrap.BootstrapResult(state=state, reason=reason))
        monkeypatch.setattr(
            deps, "get_el_sessionmaker", lambda: (_ for _ in ()).throw(AssertionError("opened"))
        )
        meta = get(client, "/meta")
        assert (
            meta["state"] == state
            and meta["reason"] == reason
            and meta["rounds"] == []
            and meta["clubs"] == []
        )
        assert (
            meta["currentSeason"] is None
            and meta["regulationMinutes"] == 40
            and meta["dayOneNotice"]
        )
        health = get(client, "/health")
        assert (
            health["status"] == "unavailable"
            and health["state"] == state
            and health["syncVersion"] is None
        )


def test_health_and_sync_report_the_euroleagues_own_cursor(client) -> None:
    health, sync = get(client, "/health"), get(client, "/sync")
    assert health["status"] == "ok" and health["state"] == "ready" and health["isDemo"] is True
    assert health["syncVersion"] == sync["syncVersion"] == 1 and sync["mode"] == "demo"
    assert health["dataThrough"] == sync["dataThrough"] == "2026-10-09"
    assert sync["pausedUntil"] is None


def test_the_league_registry_can_find_the_euroleague_without_importing_it(client) -> None:
    from nbastats.euroleague import registry_provider

    registry_provider.register()
    entry = league_registry.league_entry("euroleague")
    assert entry["enabled"] is True and entry["state"] == "ready" and entry["isDemo"] is True
    assert (
        entry["currentSeason"] == "2026-27"
        and entry["syncVersion"] == 1
        and entry["apiPrefix"] == "/v1/el"
    )
    assert "defenseByPosition" in entry["features"] and entry["regulationMinutes"] == 40
    with league_registry.session_for("euroleague") as session:
        assert session.execute(select(ElGame)).first() is not None
    assert league_registry.team_exists("euroleague", "zza") is True
    assert league_registry.team_exists("euroleague", "XXX") is False
    bootstrap.set_state(bootstrap.BootstrapResult(state="disabled", reason="off"))
    with pytest.raises(league_registry.LeagueUnavailableError):
        league_registry.session_for("euroleague")
    row = league_registry.league_entry("euroleague")
    assert row["enabled"] is False and row["state"] == "disabled" and row["features"] == []


# --------------------------------------------------------------------------- the writes


AVAILABILITY = {
    "clubCode": "ZZA",
    "personCode": "demo-3",
    "status": "out",
    "sourceLabel": "Alderwick statement",
    "sourcePublishedAt": "2026-10-11",
    "reasonCategory": "injury",
    "expectedReturnText": "Rounds 5-6",
    "sourceUrl": "https://clubs.example.org/zza/1",
}


def test_every_write_needs_a_credential(client) -> None:
    for method, path, body in (
        ("post", "/availability", AVAILABILITY),
        ("delete", "/availability/1", None),
        (
            "post",
            "/news/links",
            {
                "title": "t",
                "link": "https://example.org/a",
                "publishedAt": "2026-10-11",
                "sourceName": "x",
                "teamIds": ["ZZA"],
            },
        ),
        ("patch", "/model-settings", {"settings": [{"key": "formWeight", "value": 0.3}]}),
    ):
        kwargs = {"json": body} if body is not None else {}
        error_of(getattr(client, method)("/v1/el" + path, **kwargs), "unauthorized", 401)
        error_of(
            getattr(client, method)("/v1/el" + path, headers={"X-API-Key": "wrong"}, **kwargs),
            "unauthorized",
            401,
        )


def test_a_status_entered_through_the_api_appears_with_its_source_and_can_be_retracted(
    client,
) -> None:
    created = client.post("/v1/el/availability", json=AVAILABILITY, headers=HEADERS)
    assert created.status_code == 201, created.text
    body = created.json()
    assert (
        body["matched"] is True
        and body["sourceKind"] == "clubStatement"
        and body["status"] == "out"
    )
    report = get(client, "/availability?clubCode=ZZA")
    entries = [e for e in report["teams"][0]["entries"] if e["statusId"] == body["statusId"]]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["status"] == "out" and entry["source"]["url"] == "https://clubs.example.org/zza/1"
    assert entry["expectedReturn"]["roundFrom"] == 5 and entry["inForce"] is True
    assert (
        entry["source"]["publishedAt"] == "2026-10-11T00:00:00Z" and entry["ageMinutes"] == 33 * 60
    )
    assert get(client, "/sync")["syncVersion"] == 2
    deleted = client.delete(f"/v1/el/availability/{body['statusId']}", headers=HEADERS)
    assert deleted.status_code == 200 and deleted.json()["alreadyRetracted"] is False
    after = get(client, "/availability?clubCode=ZZA")
    assert body["statusId"] not in {e["statusId"] for e in after["teams"][0]["entries"]}
    again = client.delete(f"/v1/el/availability/{body['statusId']}", headers=HEADERS)
    assert again.json()["alreadyRetracted"] is True
    error_of(client.delete("/v1/el/availability/99999", headers=HEADERS), "not_found", 404)


def test_an_entered_status_changes_the_projection(client) -> None:
    before = get(client, "/games/E2026-R05-01/projection")["current"]
    game = before["game"]
    club = game["home"]["id"]
    squad = get(client, f"/teams/{club}")["squad"]
    star = max(
        (r for r in squad if r["per40"] and r["projectedMinutes"]),
        key=lambda r: r["per40"]["pts"] * r["projectedMinutes"],
    )
    response = client.post(
        "/v1/el/availability",
        json={
            "clubCode": club,
            "personCode": star["player"]["id"],
            "status": "out",
            "sourceLabel": "test",
            "sourcePublishedAt": "2026-10-12T08:00:00Z",
        },
        headers=HEADERS,
    )
    assert response.status_code == 201, response.text
    after = get(client, "/games/E2026-R05-01/projection")["current"]
    assert after["home"]["projectedPoints"] < before["home"]["projectedPoints"]
    assert after["home"]["availabilityEffect"] < before["home"]["availabilityEffect"]
    assert any(
        a["player"]["id"] == star["player"]["id"] and a["status"] == "out"
        for a in after["home"]["keyAbsences"]
    )


def test_write_validation_names_the_field_and_changes_nothing(client, fresh_demo_engine) -> None:
    def rows():
        with Session(fresh_demo_engine, future=True) as s:
            return len(s.execute(select(ElIntelStatus)).all())

    base = rows()
    cases = [
        ({**AVAILABILITY, "status": "maybe"}, "invalid_status", "status"),
        ({**AVAILABILITY, "clubCode": "NOPE"}, "club_not_found", None),
        ({**AVAILABILITY, "personCode": "ghost"}, "bad_request", "personCode"),
        ({**AVAILABILITY, "gameId": "E2026-R05-02"}, "bad_request", "gameId"),
        ({**AVAILABILITY, "sourcePublishedAt": "2099-01-01"}, "bad_request", "sourcePublishedAt"),
        ({**AVAILABILITY, "sourcePublishedAt": "yesterday"}, "bad_request", "sourcePublishedAt"),
        ({**AVAILABILITY, "reasonCategory": "vibes"}, "bad_request", "reasonCategory"),
        ({**AVAILABILITY, "sourceUrl": "ftp://x"}, "bad_request", "sourceUrl"),
        ({**AVAILABILITY, "sourceLabel": ""}, "bad_request", "sourceLabel"),
    ]
    for body, code, field in cases:
        response = client.post("/v1/el/availability", json=body, headers=HEADERS)
        envelope = error_of(response, code, 404 if code == "club_not_found" else 400)
        if field:
            assert envelope["field"] == field, (body, envelope)
    # A field this service does not know (a typed number, say) is refused, not dropped.
    error_of(
        client.post("/v1/el/availability", json={**AVAILABILITY, "line": 160.5}, headers=HEADERS),
        "bad_request",
        400,
    )
    missing = {k: v for k, v in AVAILABILITY.items() if k != "status"}
    error_of(client.post("/v1/el/availability", json=missing, headers=HEADERS), "bad_request", 400)
    assert rows() == base


def test_a_status_from_a_gambling_operators_site_keeps_its_label_and_loses_its_link(client) -> None:
    body = {
        **AVAILABILITY,
        "sourceUrl": "https://www.mozzartsport.com/a/1",
        "sourceLabel": "Mozzart",
    }
    response = client.post("/v1/el/availability", json=body, headers=HEADERS)
    assert response.status_code == 201 and response.json()["linkWithheld"] is True
    entry = next(
        e
        for e in get(client, "/availability?clubCode=ZZA")["teams"][0]["entries"]
        if e["statusId"] == response.json()["statusId"]
    )
    assert entry["source"]["url"] is None and entry["source"]["label"].endswith(
        "(link withheld: betting operator)"
    )


def test_an_unmatched_name_is_queued_not_guessed(client) -> None:
    body = {k: v for k, v in AVAILABILITY.items() if k != "personCode"}
    body["playerName"] = "Nobody Atall"
    response = client.post("/v1/el/availability", json=body, headers=HEADERS)
    assert response.status_code == 201 and response.json()["matched"] is False
    queue = get(client, "/availability/review-queue")["items"]
    assert any(i["playerName"] == "Nobody Atall" for i in queue)
    assert any(
        i["kind"] == "status" and i["playerName"] == "Nobody Atall"
        for i in get(client, "/review-queue")["items"]
    )


def test_model_settings_are_validated_atomically_and_become_manual(
    client, fresh_demo_engine
) -> None:
    before = get(client, "/model-settings")
    values = {s["key"]: s for s in before["settings"]}
    assert values["boostCap"]["provenance"] == "default" and values["boostCap"]["isDefault"] is True
    assert values["teamSd"]["provenance"] == "workbookUnvalidated"
    assert len(before["settings"]) == 12 + 40 + 5 + 1  # the allowlist, and nothing else
    for bad in (
        {"settings": [{"key": "totalSd", "value": 13.5}]},
        {"settings": [{"key": "boostCap", "value": 0.5}]},
        {"settings": [{"key": "boostCap", "value": "big"}]},
        {"settings": [{"key": "capPolicyConsistent", "value": 0.5}]},
        {"settings": []},
        {"settings": [{"key": "formWeight", "value": 0.3, "provenance": "fittedLedger"}]},
    ):
        error_of(
            client.patch("/v1/el/model-settings", json=bad, headers=HEADERS), "bad_request", 400
        )
    # One valid and one invalid: nothing is applied.
    error_of(
        client.patch(
            "/v1/el/model-settings",
            json={
                "settings": [{"key": "formWeight", "value": 0.3}, {"key": "boostCap", "value": 9}]
            },
            headers=HEADERS,
        ),
        "bad_request",
        400,
    )
    assert {s["key"]: s for s in get(client, "/model-settings")["settings"]}["formWeight"][
        "value"
    ] == 0.25
    ok = client.patch(
        "/v1/el/model-settings",
        json={"settings": [{"key": "formWeight", "value": 0.4}, {"key": "boostCap", "value": 1.2}]},
        headers=HEADERS,
    )
    assert ok.status_code == 200
    after = {s["key"]: s for s in ok.json()["settings"]}
    assert (
        after["formWeight"]["value"] == 0.4
        and after["formWeight"]["provenance"] == "manual"
        and after["formWeight"]["isDefault"] is False
    )
    assert (
        after["boostCap"]["value"] == 1.2 and after["formWeight"]["setAt"] == "2026-10-12T09:00:00Z"
    )
    assert get(client, "/sync")["syncVersion"] == 2
    with Session(fresh_demo_engine, future=True) as s:
        assert s.get(ElModelSetting, "formWeight").provenance == "manual"


def test_a_setting_change_moves_the_projections_that_use_it(client) -> None:
    before = get(client, "/games/E2026-R05-01/projection")["current"]
    assert (
        client.patch(
            "/v1/el/model-settings",
            json={"settings": [{"key": "homeAdvantagePoints", "value": 7.0}]},
            headers=HEADERS,
        ).status_code
        == 200
    )
    after = get(client, "/games/E2026-R05-01/projection")["current"]
    assert after["homeAdvantagePoints"] == 7.0
    assert after["margin"] > before["margin"] + 2.0
    # The change reaches the ratings too: rounds 3 and 4 are rebuilt with the new value, and the
    # misses that moved the ratings were measured against those rebuilt projections.
    assert get(client, "/ratings")["rows"][0]["attackAdj"] != 0


NEWS = {
    "title": "Alderwick sign a guard",
    "link": "https://news.example.org/a/1",
    "publishedAt": "2026-10-11T10:00:00Z",
    "sourceName": "Example News",
    "teamIds": ["ZZA"],
    "playerIds": ["demo-3"],
}


def test_a_pasted_link_is_stored_once_and_found_by_club_or_player(
    client, fresh_demo_engine
) -> None:
    first = client.post("/v1/el/news/links", json=NEWS, headers=HEADERS)
    assert first.status_code == 201 and first.json()["created"] is True
    again = client.post("/v1/el/news/links", json=NEWS, headers=HEADERS)
    assert again.status_code == 200 and again.json()["created"] is False
    assert again.json()["item"]["itemId"] == first.json()["item"]["itemId"]
    items = get(client, "/news")["items"]
    assert len(items) == 1
    item = items[0]
    assert (
        item["title"] == NEWS["title"]
        and item["link"] == NEWS["link"]
        and item["sourceName"] == "Example News"
    )
    assert item["publishedAt"] == "2026-10-11T10:00:00Z"
    assert [t["id"] for t in item["teams"]] == ["ZZA"] and [p["id"] for p in item["players"]] == [
        "demo-3"
    ]
    assert (
        len(get(client, "/news?teamId=ZZA")["items"]) == 1
        and get(client, "/news?teamId=ZZB")["items"] == []
    )
    assert (
        len(get(client, "/news?playerId=demo-3")["items"]) == 1
        and get(client, "/news?playerId=demo-4")["items"] == []
    )
    with Session(fresh_demo_engine, future=True) as s:
        columns = {c.name for c in ElIntelNewsItem.__table__.columns}
        assert not columns & {"description", "body", "summary", "content"}


def test_a_pasted_link_is_validated(client) -> None:
    for patch, field in (
        ({"link": "ftp://example.org/x"}, "link"),
        ({"link": "https://www.mozzartsport.com/news"}, "link"),
        ({"link": "not a url"}, "link"),
        ({"title": "  "}, "title"),
        ({"title": "x" * 301}, "title"),
        ({"sourceName": ""}, "sourceName"),
        ({"publishedAt": "soon"}, "publishedAt"),
        ({"publishedAt": "2099-01-01T00:00:00Z"}, "publishedAt"),
        ({"teamIds": [], "playerIds": []}, "teamIds"),
        ({"teamIds": ["NOPE"]}, None),
        ({"playerIds": ["ghost"]}, "playerIds"),
    ):
        response = client.post("/v1/el/news/links", json={**NEWS, **patch}, headers=HEADERS)
        assert response.status_code in (400, 404), (patch, response.text)
        if field:
            assert response.json()["error"]["field"] == field
    assert get(client, "/news")["items"] == []
    error_of(
        client.post("/v1/el/news/links", json={**NEWS, "description": "text"}, headers=HEADERS),
        "bad_request",
        400,
    )


def test_news_feeds_show_in_the_sources_panel_with_their_state(client, fresh_demo_engine) -> None:
    with Session(fresh_demo_engine, future=True) as s:
        s.add(ElIntelNewsFeed(name="Example feed", url="https://example.org/feed", enabled=True))
        s.add(
            ElIntelNewsFeed(
                name="Blocked feed",
                url="https://example.org/other",
                enabled=False,
                disabled_reason="robots.txt disallows it",
            )
        )
        s.commit()
    sources = {s["key"]: s for s in get(client, "/sources")["sources"]}
    assert (
        sources["el.news.1"]["state"] == "noReportYet" and sources["el.news.1"]["enabled"] is True
    )
    assert (
        sources["el.news.2"]["state"] == "disabled"
        and sources["el.news.2"]["reason"] == "robots.txt disallows it"
    )
    availability = get(client, "/availability?includeNews=true")
    assert {s["key"] for s in availability["freshness"]["sources"]} >= {
        "el.news.1",
        "el.news.2",
        "el.manual",
    }
    assert availability["news"] == []
    assert get(client, "/availability")["news"] is None


# --------------------------------------------------------------------------- misc


def test_the_availability_report_lists_every_club_and_only_asked_rounds(client) -> None:
    everyone = get(client, "/availability")
    assert len(everyone["teams"]) == 20
    assert everyone["attribution"].startswith("EuroLeague statistics from")
    one_club = get(client, "/availability?teamId=ZZP")  # the NBA-style alias works too
    assert [t["team"]["id"] for t in one_club["teams"]] == ["ZZP"]
    assert all(e["source"]["publishedAt"] for t in everyone["teams"] for e in t["entries"])
    assert {e["status"] for t in everyone["teams"] for e in t["entries"]} <= {
        "out",
        "doubtful",
        "questionable",
        "probable",
        "available",
    }


def test_player_detail_and_game_log(client) -> None:
    person = "demo-1"
    detail = get(client, f"/players/{person}")
    assert (
        detail["player"]["id"] == person
        and detail["club"] is not None
        and detail["registration"]["dorsal"]
    )
    assert detail["seasonAverages"]["games"] >= 1 and set(detail["seasonAverages"]) == {
        "games",
        "values",
        "gamesWithStat",
    }
    assert detail["rate"]["basis"] == "syntheticDemo" and detail["rate"]["asOfRound"] == 4
    assert detail["seasonPer40"]["min"] is None  # minutes per forty minutes is not a statistic
    log = get(client, f"/players/{person}/gamelog")
    assert [g["game"]["round"] for g in log["games"]] == sorted(
        (g["game"]["round"] for g in log["games"]), reverse=True
    )
    assert (
        get(client, f"/players/{person}/gamelog?limit=1")["games"][0]["game"]["round"]
        == log["games"][0]["game"]["round"]
    )
    assert log["notes"] == ["Official EuroLeague games only."]


def test_the_scorers_route_has_no_lines_picks_or_spreads(client) -> None:
    scorers = get(client, "/rounds/5/scorers?perClub=3")
    assert len(scorers["clubs"]) == 20 and all(len(c["players"]) == 3 for c in scorers["clubs"])
    for club in scorers["clubs"]:
        pts = [p["projectedPoints"] for p in club["players"]]
        assert pts == sorted(pts, reverse=True)
    assert scan_keys(scorers) == []


def test_the_registry_is_registered_by_importing_the_router(tmp_path) -> None:
    league_registry.clear()
    import importlib

    import nbastats.euroleague.api.routes as routes_module

    importlib.reload(routes_module)
    assert league_registry.is_registered("euroleague")
    league_registry.clear()


def test_an_empty_live_store_still_answers_sources_method_and_settings(tmp_path, monkeypatch):
    """First launch, live ingest on, nothing fetched yet (or the host blocked): no season row
    exists. The store-wide routes must still say why nothing has arrived, and the settings must
    be readable and writable; only the season-bound routes answer ``season_not_loaded``."""
    from nbastats.euroleague.db import create_el_engine, ensure_identity, init_el_db

    url = f"sqlite:///{tmp_path / 'empty_el.db'}"
    engine = create_el_engine(url)
    init_el_db(engine)
    with Session(engine, future=True) as s:
        ensure_identity(s, "live")
        s.commit()
    engine.dispose()
    monkeypatch.setenv("HARDWOOD_EL_DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'nba.db'}")
    monkeypatch.setenv("HARDWOOD_API_KEY", KEY)
    stats_config.reset_settings_cache()
    stats_db.dispose_engine()
    dispose_el_engine()
    clear_memo()
    bootstrap.set_state(bootstrap.BootstrapResult(state="ready", kind="live", is_demo=False))
    try:
        application = FastAPI()
        install_error_handlers(application)
        application.include_router(router, prefix="/v1")
        application.dependency_overrides[deps.get_now] = lambda: NOW
        client = TestClient(application)
        sources = get(client, "/sources")
        assert sources["store"]["kind"] == "live" and sources["sources"]
        assert get(client, "/method")["league"] == "euroleague"
        settings = get(client, "/model-settings")
        assert any(s["key"] == "capPolicyConsistent" for s in settings["settings"])
        patched = client.patch(
            "/v1/el/model-settings",
            json={"settings": [{"key": "formWeight", "value": 0.3}]},
            headers={"X-API-Key": KEY},
        )
        assert patched.status_code == 200, patched.text[:300]
        error_of(client.get("/v1/el/teams"), "season_not_loaded", 422)
    finally:
        bootstrap.reset_state()
        dispose_el_engine()
        stats_config.reset_settings_cache()
        stats_db.dispose_engine()


def test_a_line_with_no_recorded_minutes_adds_to_neither_side_of_a_per_40_rate(
    fresh_demo_engine,
) -> None:
    """A played line whose minutes were not recorded (or recorded as zero) counts for per-game
    averages but adds neither points nor minutes to a per-40 rate."""
    from nbastats.euroleague.read import stats as stats_module
    from nbastats.euroleague.read.queries import build_context

    with Session(fresh_demo_engine, future=True) as s:
        lines = (
            s.execute(
                select(ElPlayerGame)
                .join(ElGame, ElGame.game_id == ElPlayerGame.game_id)
                .where(
                    ElPlayerGame.participation == "played",
                    ElPlayerGame.pts.is_not(None),
                    ElGame.status == "final",
                    ElGame.stats_status == "ok",  # the games the stats table counts
                )
                .order_by(ElPlayerGame.person_code, ElPlayerGame.game_id)
            )
            .scalars()
            .all()
        )
        person = next(
            p
            for p in sorted({line.person_code for line in lines})
            if sum(1 for line in lines if line.person_code == p) >= 3
        )
        mine = [line for line in lines if line.person_code == person]
        mine[0].seconds_played = None
        mine[1].seconds_played = 0
        s.commit()
        clear_memo()
        ctx = build_context(s, None, now=NOW)
        agg = stats_module.aggregate_players(ctx)[person]
        values, counts = stats_module.season_values(agg, "Per40")
        timed = [line for line in mine[2:] if line.seconds_played]
        expected = 40.0 * sum(line.pts for line in timed) / (
            sum(line.seconds_played for line in timed) / 60.0
        )
        assert values["pts"] == pytest.approx(expected)
        assert counts["pts"] == len(mine)
        per_game, _ = stats_module.season_values(agg, "PerGame")
        assert per_game["pts"] == pytest.approx(sum(line.pts for line in mine) / len(mine))


def test_the_demo_league_refuses_a_hand_entered_status_over_http(
    client, monkeypatch, _statuses_writable_in_the_demo_store
) -> None:
    """``POST /v1/el/availability`` on the invented league is refused, as the NBA demo refuses
    it: a real player's status and source must never sit beside invented clubs."""
    monkeypatch.setattr(_availability, "statuses_allowed", _statuses_writable_in_the_demo_store)
    body = {**AVAILABILITY, "playerName": "Real Person Name", "sourceUrl": "https://example.org/n"}
    body.pop("personCode", None)
    response = client.post("/v1/el/availability", json=body, headers=HEADERS)
    assert "Demo league" in error_of(response, "bad_request", 400)["message"]


def test_the_day_one_notice_matches_the_sources_that_are_actually_on() -> None:
    """A live-mode user with no workbook is never told the data comes from a workbook, nor that
    live ingest needs turning on."""
    from nbastats.euroleague.read import sources as sources_module

    base = sources_module._day_one_base
    live_only = base(is_demo=False, live=True, has_workbook=False)
    assert "workbook" not in live_only.split("drop a workbook")[0]
    assert "live ingest" in live_only and "until live ingest is on" not in live_only
    both = base(is_demo=False, live=True, has_workbook=True)
    assert "your workbook" in both and "until live ingest is on" not in both
    workbook_only = base(is_demo=False, live=False, has_workbook=True)
    assert "until live ingest is on" in workbook_only
    assert "No EuroLeague source is on" in base(is_demo=False, live=False, has_workbook=False)
    assert "Demo league" in base(is_demo=True, live=True, has_workbook=False)
