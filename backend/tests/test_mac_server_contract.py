"""The server-side promises the Mac app was built on (BD-2, BD-3, BD-7 to BD-14), held in place.

The Mac design listed fourteen things it needed from the server. Some were already true of the
committed backend when the list was written (the design was drafted before the backend landed),
some have been built since, and a promise that is only true by accident is not one a client can be
built on. This file pins, with the committed fixtures and the live application, each one that the
other test modules do not already guard:

BD-2   the NBA ships a headline-feed candidate, seeded into an empty feed table and subject to the
       same robots.txt check as every feed (the fetching and matching tests live in test_feeds.py);
BD-3   ``POST /v1/availability`` has one frozen body, and says which fields the EuroLeague's has
       that the NBA's does not (there is no column to put them in, and nothing may be altered);
BD-7   a reviewed NBA game carries its combined-points miss;
BD-8   a projection says whether it was computed after tip-off, three-valued;
BD-9   the box score's per-player object is ``stats`` and no payload key is ``line``;
BD-10  ``PATCH {prefix}/model-settings`` takes ``{settings: [{key, value}]}`` and answers with the
       whole settings payload; every refusal is a ``400 bad_request`` naming the field;
BD-11  ``GET {prefix}/news`` is ``{league, freshness, items, notes}``, items are
       ``{itemId, title, link, publishedAt, sourceName, teams, players}``;
BD-12  every ``freshness.sources[]`` element is ``{key, label, state, reason, lastSuccessAt}``;
BD-13  an availability summary's ``out``/``doubtful``/``questionable``/``probable`` are counts;
BD-14  the league fixtures are flat files, and the four EuroLeague tile payloads are among them.

The EuroLeague halves of BD-4 to BD-8 are in ``tests/euroleague/test_mac_server_items.py``; BD-1
is in ``test_native_write_auth.py``; BD-15 is in ``test_refetch_games.py``.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from nbastats import config as stats_config
from nbastats import db as db_module
from nbastats.accounts import config as auth_config
from nbastats.api import deps
from nbastats.api.app import create_app
from nbastats.db import create_db_engine, init_db
from nbastats.models import Game
from nbastats.nba_intel import news as intel_news
from nbastats.nba_intel.models import SOURCE_STATE_VALUES, NbaIntelNewsFeed
from nbastats.nba_matchup import slate as nba_slate
from nbastats.nba_matchup.queries import computed_after_tipoff
from nbastats.shared.market_guard import scan_keys

ROOT = Path(__file__).resolve().parents[2]
LEAGUES = ROOT / "contracts" / "fixtures" / "leagues"
KEY = "mac-contract-test-key"


def load(league: str, name: str) -> Any:
    return json.loads((LEAGUES / league / f"{name}.json").read_text(encoding="utf-8"))


def every_league_fixture() -> list[tuple[str, str, Any]]:
    return [
        (folder.name, path.stem, json.loads(path.read_text(encoding="utf-8")))
        for folder in sorted(LEAGUES.iterdir())
        if folder.is_dir()
        for path in sorted(folder.glob("*.json"))
    ]


def walk(node: Any, path: str = "$") -> Iterator[tuple[str, Any]]:
    yield path, node
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk(value, f"{path}[{index}]")


# =========================================================================== BD-2: an NBA feed


def test_the_nba_ships_one_headline_feed_candidate_and_seeds_it_into_an_empty_table(
    tmp_path: Path,
) -> None:
    assert [url for _, url in intel_news.DEFAULT_FEEDS] == ["https://talkbasket.net/feed"]
    engine = create_db_engine(f"sqlite:///{tmp_path / 'feeds.db'}")
    init_db(engine)
    try:
        with Session(engine, future=True) as session:
            assert intel_news.ensure_default_feeds(session) == 1
            assert intel_news.ensure_default_feeds(session) == 0  # never added to a used table
            [feed] = session.query(NbaIntelNewsFeed).all()
            assert feed.enabled is True and feed.url == "https://talkbasket.net/feed"
    finally:
        engine.dispose()


SOURCE_ROW_KEYS = {
    "key", "label", "kind", "enabled", "state", "reason", "lastSuccessAt", "lastError",
    "robotsCheckedOn", "attribution",
}


def test_an_nba_source_row_for_a_feed_says_when_robots_txt_was_last_checked() -> None:
    sources = load("nba", "sources")["sources"]
    feeds = [row for row in sources if row["kind"] == "news"]
    assert feeds and all(row["key"].startswith("nba.news.") for row in feeds)
    for row in sources:
        assert set(row) == SOURCE_ROW_KEYS, row["key"]
    assert all(row["robotsCheckedOn"] for row in feeds), "a feed is only fetched after the check"


# =========================================================================== BD-3: the NBA body


@pytest.fixture(scope="module")
def openapi() -> Iterator[dict[str, Any]]:
    """The application's OpenAPI document with both leagues mounted and no key configured."""
    with pytest.MonkeyPatch.context() as patcher:
        patcher.delenv("HARDWOOD_API_KEY", raising=False)
        patcher.delenv("HARDWOOD_EL_ENABLED", raising=False)
        stats_config.reset_settings_cache()
        try:
            yield create_app().openapi()
        finally:
            stats_config.reset_settings_cache()


def body_schema(document: dict[str, Any], path: str, method: str) -> dict[str, Any]:
    content = document["paths"][path][method]["requestBody"]["content"]["application/json"]
    schema = content["schema"]
    if "$ref" in schema:
        schema = document["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]]
    return schema


def test_the_nba_status_body_is_frozen_and_refuses_what_it_does_not_know(
    openapi: dict[str, Any],
) -> None:
    schema = body_schema(openapi, "/v1/availability", "post")
    assert set(schema["properties"]) == {
        "playerId", "status", "teamId", "gameId", "note", "sourceUrl", "sourcePublishedAt",
    }
    assert set(schema["required"]) == {"playerId", "status"}
    assert schema["additionalProperties"] is False


def test_the_euroleague_status_body_has_the_extra_fields_the_nba_one_cannot_store(
    openapi: dict[str, Any],
) -> None:
    el = body_schema(openapi, "/v1/el/availability", "post")
    nba = body_schema(openapi, "/v1/availability", "post")
    assert set(el["properties"]) == {
        "clubCode", "personCode", "playerName", "gameId", "status", "reasonCategory", "reasonText",
        "expectedReturnText", "sourceUrl", "sourceLabel", "sourcePublishedAt",
    }
    assert set(el["required"]) == {"clubCode", "status", "sourceLabel", "sourcePublishedAt"}
    assert el["additionalProperties"] is False
    # What only the EuroLeague stores, so the Record Status sheet shows these for it alone.
    assert set(el["properties"]) - set(nba["properties"]) >= {
        "reasonCategory", "reasonText", "expectedReturnText", "sourceLabel",
    }


def test_the_link_body_is_one_shape_in_both_leagues(openapi: dict[str, Any]) -> None:
    nba = body_schema(openapi, "/v1/news/links", "post")
    el = body_schema(openapi, "/v1/el/news/links", "post")
    names = {"title", "link", "publishedAt", "sourceName", "teamIds", "playerIds"}
    assert set(nba["properties"]) == set(el["properties"]) == names
    assert set(nba["required"]) == set(el["required"]) == {
        "title", "link", "publishedAt", "sourceName",
    }
    assert nba["additionalProperties"] is False and el["additionalProperties"] is False


# =========================================================================== BD-7 and BD-8 (NBA)


def test_an_nba_reviewed_game_carries_the_combined_miss_signed() -> None:
    review = load("nba", "projection_review")
    assert review["games"]
    for item in review["games"]:
        actual = item["result"]["homePts"] + item["result"]["awayPts"]
        projected = item["projected"]["homePts"] + item["projected"]["awayPts"]
        assert item["combinedMiss"] == pytest.approx(actual - projected)
    row = nba_slate.ReviewRow(
        SimpleNamespace(home_pts=110, away_pts=100), "locked", 104.0, 99.0  # type: ignore[arg-type]
    )
    assert row.combined_miss == pytest.approx(7.0)  # finished higher than projected: positive
    assert row.margin_miss == pytest.approx(5.0)
    low = nba_slate.ReviewRow(
        SimpleNamespace(home_pts=90, away_pts=80), "locked", 104.0, 99.0  # type: ignore[arg-type]
    )
    assert low.combined_miss == pytest.approx(-33.0)


def _nba_game(day: date = date(2026, 1, 4)) -> Game:
    return Game(game_id="g", game_date=day)


def test_an_nba_game_with_a_known_tipoff_is_a_plain_comparison() -> None:
    tip = datetime(2026, 1, 4, 0, 30, tzinfo=timezone.utc)  # 19:30 Eastern on the 3rd
    game = _nba_game(date(2026, 1, 3))
    assert computed_after_tipoff(game, tip, datetime(2026, 1, 4, 0, 29, 59)) is False
    assert computed_after_tipoff(game, tip, datetime(2026, 1, 4, 0, 30)) is True
    assert computed_after_tipoff(game, tip, datetime(2026, 1, 4, 5, 0, tzinfo=timezone.utc)) is True


def test_an_nba_game_with_no_tipoff_says_only_what_is_certain() -> None:
    # Eastern: the lock deadline (noon) is 17:00 UTC, the day's last minute is 04:59 UTC the next.
    game = _nba_game(date(2026, 1, 3))
    before_lock = datetime(2026, 1, 3, 16, 59, tzinfo=timezone.utc)
    midday = datetime(2026, 1, 3, 23, 0, tzinfo=timezone.utc)
    after_day = datetime(2026, 1, 4, 5, 0, tzinfo=timezone.utc)
    assert computed_after_tipoff(game, None, before_lock) is False
    assert computed_after_tipoff(game, None, midday) is None
    assert computed_after_tipoff(game, None, after_day) is True
    assert computed_after_tipoff(game, None, None) is None


def _models(document: Any) -> list[tuple[str, dict[str, Any]]]:
    return [
        (path, node)
        for path, node in walk(document)
        if isinstance(node, dict) and "computedAfterTipoff" in node
    ]


@pytest.mark.parametrize("league", ["nba", "el"])
def test_every_model_block_in_the_fixtures_has_the_key_and_the_kinds_agree(league: str) -> None:
    seen: dict[str, set[Any]] = {}
    for name in ("slate_projections", "game_projection_detail", "team_matchup", "game_matchup"):
        for path, model in _models(load(league, name)):
            value = model["computedAfterTipoff"]
            assert value is None or isinstance(value, bool), (name, path)
            seen.setdefault(model["kind"], set()).add(value)
    assert seen["latest"] <= {False, None}
    assert seen.get("locked", {False}) == {False}
    assert seen.get("reconstructed", {True}) == {True}


def test_a_games_model_block_is_tied_to_its_game_but_the_slates_own_block_is_not() -> None:
    for league in ("nba", "el"):
        slate = load(league, "slate_projections")
        assert slate["model"]["computedAfterTipoff"] is None
        assert all(g["model"]["computedAfterTipoff"] is False for g in slate["games"])


# =========================================================================== BD-9: stats, not line


def test_the_box_score_players_object_is_stats_and_no_payload_key_is_line() -> None:
    box = load("el", "box_score")
    for team in box["teams"]:
        assert team["players"]
        for player in team["players"]:
            assert "stats" in player and "line" not in player
    for league, name, document in every_league_fixture():
        assert scan_keys(document) == [], (league, name)
        assert not [p for p, _ in walk(document) if p.endswith(".line")], (league, name)


# =========================================================================== BD-10: model settings


@pytest.fixture()
def keyed_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'settings.db'}")
    monkeypatch.setenv("HARDWOOD_API_KEY", KEY)
    monkeypatch.setenv("HARDWOOD_EL_ENABLED", "0")
    monkeypatch.setenv("HARDWOOD_RATE_LIMIT", "0")
    stats_config.reset_settings_cache()
    auth_config.reset_auth_settings_cache()
    db_module.dispose_engine()
    deps.reset_rate_limiter()
    with TestClient(create_app()) as client:
        yield client
    db_module.dispose_engine()
    stats_config.reset_settings_cache()
    auth_config.reset_auth_settings_cache()
    deps.reset_rate_limiter()


SETTING_KEYS = {"key", "value", "provenance", "isDefault", "setAt", "description"}


def patch(client: TestClient, body: dict[str, Any]) -> Any:
    return client.patch("/v1/model-settings", json=body, headers={"X-API-Key": KEY})


def test_the_patch_body_is_settings_of_key_and_value_in_both_leagues(
    openapi: dict[str, Any],
) -> None:
    for path in ("/v1/model-settings", "/v1/el/model-settings"):
        schema = body_schema(openapi, path, "patch")
        assert set(schema["properties"]) == {"settings"} and schema["required"] == ["settings"]
        assert schema["additionalProperties"] is False
        item = openapi["components"]["schemas"][
            schema["properties"]["settings"]["items"]["$ref"].rsplit("/", 1)[1]
        ]
        assert set(item["properties"]) == {"key", "value"}
        assert item["properties"]["value"]["type"] == "number"
        assert item["additionalProperties"] is False


def test_a_good_patch_answers_with_the_whole_settings_payload(keyed_app: TestClient) -> None:
    before = keyed_app.get("/v1/model-settings", headers={"X-API-Key": KEY}).json()
    response = patch(keyed_app, {"settings": [{"key": "boostCap", "value": 1.3}]})
    assert response.status_code == 200, response.text
    after = response.json()
    assert list(after) == ["league", "freshness", "settings"] == list(before)
    assert [row["key"] for row in after["settings"]] == [row["key"] for row in before["settings"]]
    row = next(r for r in after["settings"] if r["key"] == "boostCap")
    assert set(row) == SETTING_KEYS
    assert (row["value"], row["isDefault"], row["provenance"]) == (1.3, False, "manual")
    assert row["setAt"] is not None


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"settings": [{"key": "lineSpread", "value": 1}]}, "key"),
        ({"settings": [{"key": "boostCap", "value": 99}]}, "value"),
        ({"settings": []}, "settings"),
        ({"settings": [{"key": "boostCap", "value": 1.2, "extra": 1}]}, "extra"),
        ({"settings": [{"key": "boostCap", "value": "high"}]}, "value"),
    ],
)
def test_a_refused_patch_is_a_400_naming_the_field_and_changes_nothing(
    keyed_app: TestClient, body: dict[str, Any], field: str
) -> None:
    response = patch(keyed_app, body)
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "bad_request" and error["field"] == field
    unchanged = keyed_app.get("/v1/model-settings", headers={"X-API-Key": KEY}).json()
    assert all(row["isDefault"] for row in unchanged["settings"])


def test_a_patch_is_applied_whole_or_not_at_all(keyed_app: TestClient) -> None:
    response = patch(
        keyed_app,
        {"settings": [{"key": "boostCap", "value": 1.3}, {"key": "boostCap", "value": 99}]},
    )
    assert response.status_code == 400
    unchanged = keyed_app.get("/v1/model-settings", headers={"X-API-Key": KEY}).json()
    assert next(r for r in unchanged["settings"] if r["key"] == "boostCap")["isDefault"] is True


@pytest.mark.parametrize("league", ["nba", "el"])
def test_the_settings_fixture_rows_have_the_documented_keys(league: str) -> None:
    payload = load(league, "model_settings")
    assert list(payload) == ["league", "freshness", "settings"]
    assert payload["settings"]
    for row in payload["settings"]:
        assert set(row) == SETTING_KEYS


# =========================================================================== BD-11: news


NEWS_ITEM_KEYS = {"itemId", "title", "link", "publishedAt", "sourceName", "teams", "players"}


@pytest.mark.parametrize("league", ["nba", "el"])
def test_the_news_envelope_is_league_freshness_items_notes(league: str) -> None:
    news = load(league, "news")
    assert list(news) == ["league", "freshness", "items", "notes"]
    for item in news["items"]:
        assert set(item) == NEWS_ITEM_KEYS
    assert scan_keys(news) == []


def test_an_nba_headline_has_a_title_a_link_a_date_a_source_and_who_it_is_about() -> None:
    items = load("nba", "news")["items"]
    assert items
    for item in items:
        assert item["title"] and item["link"].startswith("https://")
        assert item["publishedAt"].endswith("Z") and item["sourceName"]
        assert item["teams"] or item["players"]
    # Nothing but pointers: no excerpt, no body, no description.
    assert not [k for item in items for k in item if k in {"summary", "description", "body"}]


# =========================================================================== BD-12: source rows


SOURCE_STATE_KEYS = {"key", "label", "state", "reason", "lastSuccessAt"}


def test_every_freshness_source_in_every_league_fixture_has_the_five_keys() -> None:
    checked = 0
    for league, name, document in every_league_fixture():
        for path, node in walk(document):
            if path.endswith(".freshness") and isinstance(node, dict) and "sources" in node:
                for source in node["sources"]:
                    assert set(source) == SOURCE_STATE_KEYS, (league, name, path)
                    assert isinstance(source["key"], str) and isinstance(source["label"], str)
                    assert source["state"] in SOURCE_STATE_VALUES, source
                    checked += 1
    assert checked >= 30


# =========================================================================== BD-13: counts


@pytest.mark.parametrize("league", ["nba", "el"])
def test_an_availability_summary_counts_are_integers(league: str) -> None:
    summaries = []
    for name in ("team_matchup", "game_matchup"):
        summaries.extend(side["availability"] for side in load(league, name)["teams"])
    assert summaries
    for summary in summaries:
        for status in ("out", "doubtful", "questionable", "probable"):
            assert type(summary[status]) is int and summary[status] >= 0, summary
        assert isinstance(summary["keyAbsences"], list)
        assert {"freshnessState", "asOf"} <= set(summary)


# =========================================================================== BD-14: flat fixtures


def test_the_league_fixtures_are_flat_files_in_two_folders() -> None:
    folders = sorted(p.name for p in LEAGUES.iterdir())
    assert folders == ["el", "nba"]
    for folder in LEAGUES.iterdir():
        assert [p for p in folder.iterdir() if p.is_dir()] == []
        assert all(p.suffix == ".json" for p in folder.iterdir())


def test_the_four_euroleague_tile_payloads_are_among_them() -> None:
    for kind in ("team_matchup", "defense_by_position", "availability_report", "slate_projections"):
        document = load("el", f"widget_{kind}")
        assert document["league"] == "euroleague"
