"""The widget layer and the two routes built on it (``contracts/CONTRACT.md`` §3, §4, §8).

Four things are checked harder than the rest, because they are the ones that break silently:

* **Key sets, literally.** The iOS client decodes each payload into a fixed Swift struct, so a
  renamed or missing key is a crash on a device rather than a cosmetic difference. The
  assertions below spell out the key set from §4 instead of round-tripping through a model,
  which would hide exactly the mistake they exist to catch.
* **Every preset, every widget.** ``contracts/presets.json`` is iterated rather than sampled:
  the ten starter dashboards are what most users will ever see, and "the app opens" means
  every tile in them resolves.
* **Null, never zero.** A 1985-86 request for a per-game advanced stat has to come back as a
  ``null`` value with ``"unavailable"`` and an explanation — not a zero, not a 500.
* **One bad tile, one bad tile.** An invalid config, an unknown kind and an exploding resolver
  each degrade their own result and leave the rest of the dashboard standing.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, func, select
from sqlalchemy.orm import Session

from nbastats import catalog, config
from nbastats.api import deps
from nbastats.api.app import create_app
from nbastats.models import PlayerSeason, TeamSeason
from nbastats.widgets import RESOLVERS, TTL_SECONDS, ResolveContext, WidgetError
from nbastats.widgets import base as widget_base
from nbastats.widgets import queries as widget_queries

CURRENT_SEASON = "2025-26"
PRE_ADVANCED_SEASON = "1992-93"

#: A season the store does not hold, before per-game advanced box scores existed.
ERA_GAP_SEASON = "1985-86"


# --------------------------------------------------------------------------- fixtures


@contextmanager
def api_client(**environment: str | None) -> Iterator[TestClient]:
    """A client over the seeded database, built the same way ``test_api_core`` builds one."""
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


@pytest.fixture()
def ctx(seeded_db: Session) -> ResolveContext:
    """A resolve context on the seeded database, with no favourites set."""
    return ResolveContext.from_request(seeded_db, None, request_id="test")


@pytest.fixture(scope="module")
def featured(seeded_engine: Engine) -> dict[str, int]:
    """The subjects the ``$``-tokens must resolve to, computed independently of the resolver.

    The qualification rule (a fifth of the leader's games, ten minutes a night) is written out
    here on purpose: it is the documented rule, and a test that re-used the implementation's
    own helper would pass even if that rule quietly changed.
    """
    with Session(seeded_engine, future=True) as session:
        most_games = session.execute(
            select(func.max(PlayerSeason.gp))
            .where(PlayerSeason.season == CURRENT_SEASON)
            .where(PlayerSeason.season_type == "Regular Season")
        ).scalar_one()
        floor = max(1, int(0.2 * (most_games or 0)))

        def leader(column: Any) -> int:
            return int(
                session.execute(
                    select(PlayerSeason.player_id)
                    .where(PlayerSeason.season == CURRENT_SEASON)
                    .where(PlayerSeason.season_type == "Regular Season")
                    .where(PlayerSeason.gp >= floor)
                    .where(PlayerSeason.min_pg >= 10.0)
                    .where(column.is_not(None))
                    .order_by(column.desc())
                    .limit(1)
                ).scalar_one()
            )

        team_id = int(
            session.execute(
                select(TeamSeason.team_id)
                .where(TeamSeason.season == CURRENT_SEASON)
                .where(TeamSeason.season_type == "Regular Season")
                .order_by(TeamSeason.net_rtg.desc())
                .limit(1)
            ).scalar_one()
        )
        return {
            "pie_leader": leader(PlayerSeason.pie),
            "scoring_leader": leader(PlayerSeason.pts),
            "net_rating_team": team_id,
        }


@pytest.fixture(scope="module")
def a_player(seeded_engine: Engine) -> int:
    """Someone with a full current season, for the tiles that need a concrete subject."""
    with Session(seeded_engine, future=True) as session:
        return int(
            session.execute(
                select(PlayerSeason.player_id)
                .where(PlayerSeason.season == CURRENT_SEASON)
                .where(PlayerSeason.season_type == "Regular Season")
                .order_by(PlayerSeason.gp.desc(), PlayerSeason.player_id)
                .limit(1)
            ).scalar_one()
        )


@pytest.fixture(scope="module")
def a_team(seeded_engine: Engine) -> int:
    with Session(seeded_engine, future=True) as session:
        return int(
            session.execute(
                select(TeamSeason.team_id)
                .where(TeamSeason.season == CURRENT_SEASON)
                .limit(1)
            ).scalar_one()
        )


# --------------------------------------------------------------------------- helpers


def _resolve(client: TestClient, widgets: list[dict[str, Any]], **body: Any) -> dict[str, Any]:
    payload = {"layoutId": "test-layout", "widgets": widgets, **body}
    response = client.post("/v1/dashboard/resolve", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _one(client: TestClient, kind: str, config_: dict[str, Any], **body: Any) -> dict[str, Any]:
    """Resolve a single widget and return its result object."""
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


#: The ``MetricValue`` key set, from ``contracts/CONTRACT.md`` §2.
METRIC_VALUE_KEYS = {
    "metric",
    "value",
    "displayValue",
    "rank",
    "percentile",
    "leagueAverage",
    "delta",
    "isEstimated",
    "availability",
}

#: The ``GameRef`` key set, from §2.
GAME_REF_KEYS = {
    "gameId",
    "date",
    "season",
    "seasonType",
    "home",
    "away",
    "homePts",
    "awayPts",
    "status",
    "period",
    "clock",
    "finalizedAt",
}


# --------------------------------------------------------------------------- registry


def test_registry_covers_every_catalog_kind() -> None:
    """A kind in the catalog with no resolver must fail at import, not at request time."""
    assert set(RESOLVERS) == set(catalog.widget_kinds())
    assert len(RESOLVERS) == 13


def test_ttl_matches_the_freshness_model() -> None:
    """``contracts/CONTRACT.md`` §8 fixes every one of these numbers."""
    assert TTL_SECONDS["scoreboard"] == 60
    assert TTL_SECONDS["daily_movers"] == 60
    for kind in ("stat_tile", "player_snapshot", "game_log", "trend_chart"):
        assert TTL_SECONDS[kind] == 300, kind
    for kind in ("leaderboard", "team_efficiency", "four_factors"):
        assert TTL_SECONDS[kind] == 600, kind
    assert TTL_SECONDS["career_arc"] == 3600
    # …and the catalog agrees, which is what the import-time assertion checks.
    for kind, ttl in TTL_SECONDS.items():
        assert catalog.widget(kind)["minRefreshSeconds"] == ttl, kind


@pytest.mark.parametrize("kind", sorted(catalog.widget_kinds()))
def test_every_kind_resolves_from_catalog_defaults(kind: str, ctx: ResolveContext) -> None:
    """A tile the reader has just dropped on the grid, with nothing configured, still draws."""
    payload, availability, notes = RESOLVERS[kind](catalog.widget_config_defaults(kind), ctx)
    assert isinstance(payload, dict) and payload
    assert availability in ("full", "estimated", "partial", "unavailable")
    assert isinstance(notes, list)
    assert all(isinstance(note, str) for note in notes)


# --------------------------------------------------------------------------- presets


def _preset_widgets() -> list[tuple[str, dict[str, Any]]]:
    return [
        (preset["presetKey"], widget)
        for preset in catalog.presets()
        for widget in preset.get("widgets", [])
    ]


def test_every_preset_widget_resolves(app_client: TestClient) -> None:
    """The real integration test: every tile of all ten starter dashboards.

    Iterated from ``contracts/presets.json`` rather than hard-coded, so a preset added to the
    contract is covered the moment it lands.
    """
    entries = _preset_widgets()
    assert len(entries) >= 30, "contracts/presets.json lost most of its preset widgets"
    assert set(kind for _, widget in entries for kind in [widget["kind"]]) <= set(
        catalog.widget_kinds()
    )
    resolved = 0

    for preset in catalog.presets():
        widgets = preset.get("widgets", [])
        if not widgets:  # the blank canvas
            continue
        body = _resolve(
            app_client,
            [
                {
                    "id": widget["id"],
                    "kind": widget["kind"],
                    "size": widget.get("size", "large"),
                    "config": widget.get("config", {}),
                }
                for widget in widgets
            ],
        )
        assert len(body["results"]) == len(widgets)
        for result in body["results"]:
            assert result["status"] in ("ok", "partial"), (
                preset["presetKey"],
                result["widgetId"],
                result.get("error"),
            )
            assert result["payload"] is not None
            assert result["ttlSeconds"] == TTL_SECONDS[result["kind"]]
            resolved += 1

    assert resolved == len(entries), "every preset widget must have been resolved"


def test_resolve_preset_route_serves_a_whole_preset(app_client: TestClient) -> None:
    response = app_client.get("/v1/dashboard/resolve-preset/player_deep_dive")
    assert response.status_code == 200, response.text
    body = response.json()
    kinds = [result["kind"] for result in body["results"]]
    assert kinds == [w["kind"] for w in catalog.preset("player_deep_dive")["widgets"]]
    assert all(result["status"] in ("ok", "partial") for result in body["results"])


def test_resolve_preset_rejects_an_unknown_key(app_client: TestClient) -> None:
    response = app_client.get("/v1/dashboard/resolve-preset/not_a_preset")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# --------------------------------------------------------------------------- the envelope


def test_a_mixed_request_is_200_with_one_ok_and_one_error(app_client: TestClient) -> None:
    """§3: a per-widget failure never changes the HTTP status."""
    body = _resolve(
        app_client,
        [
            {"id": "good", "kind": "scoreboard", "size": "large", "config": {"date": "latest"}},
            {
                "id": "bad",
                "kind": "leaderboard",
                "size": "large",
                "config": {"metric": "not_a_real_metric"},
            },
        ],
    )
    by_id = {result["widgetId"]: result for result in body["results"]}

    assert by_id["good"]["status"] == "ok"
    assert by_id["good"]["payload"] is not None
    assert by_id["good"]["error"] is None

    assert by_id["bad"]["status"] == "error"
    assert by_id["bad"]["payload"] is None
    assert by_id["bad"]["error"]["code"] == "invalid_config"
    assert by_id["bad"]["error"]["field"] == "metric"
    assert by_id["bad"]["error"]["requestId"]


def test_an_unknown_kind_is_one_widgets_problem(app_client: TestClient) -> None:
    result = _one(app_client, "space_elevator", {})
    assert result["status"] == "error"
    assert result["error"]["code"] == "invalid_config"
    assert result["error"]["field"] == "kind"


def test_an_exploding_resolver_does_not_break_the_dashboard(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unhandled exception inside one resolver becomes that tile's ``internal_error``."""

    def boom(config_: dict[str, Any], context: ResolveContext) -> Any:
        raise RuntimeError("the rim fell off")

    monkeypatch.setitem(RESOLVERS, "team_efficiency", boom)
    body = _resolve(
        app_client,
        [
            {"id": "ok", "kind": "scoreboard", "size": "large", "config": {"date": "latest"}},
            {"id": "boom", "kind": "team_efficiency", "size": "large", "config": {}},
        ],
    )
    by_id = {result["widgetId"]: result for result in body["results"]}
    assert by_id["ok"]["status"] == "ok"
    assert by_id["boom"]["status"] == "error"
    assert by_id["boom"]["error"]["code"] == "internal_error"
    assert by_id["boom"]["error"]["requestId"]
    assert "rim fell off" not in by_id["boom"]["error"]["message"]


def test_more_than_twenty_four_widgets_is_rejected(app_client: TestClient) -> None:
    widgets = [
        {"id": f"w{index}", "kind": "scoreboard", "size": "large", "config": {"date": "latest"}}
        for index in range(25)
    ]
    response = app_client.post(
        "/v1/dashboard/resolve", json={"layoutId": "big", "widgets": widgets}
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "too_many_widgets"
    assert error["field"] == "widgets"


def test_exactly_twenty_four_widgets_is_allowed(app_client: TestClient) -> None:
    widgets = [
        {"id": f"w{index}", "kind": "scoreboard", "size": "large", "config": {"date": "latest"}}
        for index in range(24)
    ]
    body = _resolve(app_client, widgets)
    assert len(body["results"]) == 24


def test_known_sync_version_answers_unchanged(app_client: TestClient) -> None:
    """§3: a client already on the server's version keeps its cached payloads."""
    widgets = [
        {
            "id": "w1",
            "kind": "leaderboard",
            "size": "large",
            "config": {"metric": "pts", "season": "latest", "limit": 5},
        }
    ]
    first = _resolve(app_client, widgets)
    assert first["results"][0]["status"] == "ok"

    again = _resolve(app_client, widgets, knownSyncVersion=first["syncVersion"])
    result = again["results"][0]
    assert result["status"] == "unchanged"
    assert result["payload"] is None
    assert result["ttlSeconds"] == 600

    stale = _resolve(app_client, widgets, knownSyncVersion=first["syncVersion"] - 1)
    assert stale["results"][0]["status"] == "ok"
    assert stale["results"][0]["payload"] is not None


def test_a_live_widget_is_never_answered_unchanged(app_client: TestClient) -> None:
    """§3 licenses ``unchanged`` for data that has not changed, not for a matching version.

    ``sync_version`` increments once per *finalized* game (§8), so it stands still all through
    a live game while the scoreboard it feeds keeps moving, and ``date: "latest"`` re-reads the
    wall clock. Answering ``unchanged`` for those would freeze the tile until the final buzzer.
    """
    for kind, config in (
        ("scoreboard", {"date": "latest"}),
        ("daily_movers", {"date": "latest", "metric": "game_score", "limit": 5}),
    ):
        widgets = [{"id": "w1", "kind": kind, "size": "large", "config": config}]
        first = _resolve(app_client, widgets)
        assert first["results"][0]["status"] == "ok", kind

        again = _resolve(app_client, widgets, knownSyncVersion=first["syncVersion"])
        result = again["results"][0]
        assert result["status"] != "unchanged", kind
        assert result["payload"] is not None, kind


def test_the_response_envelope_is_camel_case(app_client: TestClient) -> None:
    response = app_client.post(
        "/v1/dashboard/resolve",
        json={
            "layoutId": "L",
            "widgets": [
                {"id": "w1", "kind": "scoreboard", "size": "large", "config": {"date": "latest"}}
            ],
        },
    )
    for key in ('"syncVersion"', '"dataThrough"', '"generatedAt"', '"resolvedContext"',
                '"widgetId"', '"ttlSeconds"'):
        assert key in response.text


# --------------------------------------------------------------------------- subject tokens


def test_favorite_player_token_uses_the_context(
    app_client: TestClient, a_player: int
) -> None:
    body = _resolve(
        app_client,
        [
            {
                "id": "w1",
                "kind": "stat_tile",
                "size": "small",
                "config": {"subjectType": "player", "subjectId": "$favorite_player"},
            }
        ],
        context={"favoritePlayerId": a_player},
    )
    payload = body["results"][0]["payload"]
    assert payload["subject"]["player"]["playerId"] == a_player
    assert body["resolvedContext"]["favoritePlayerId"] == a_player


def test_favorite_player_falls_back_to_the_featured_player(
    app_client: TestClient, featured: dict[str, int]
) -> None:
    """With no favourite set, ``$favorite_player`` becomes the season's PIE leader."""
    body = _resolve(
        app_client,
        [
            {
                "id": "w1",
                "kind": "stat_tile",
                "size": "small",
                "config": {"subjectType": "player", "subjectId": "$favorite_player"},
            }
        ],
        context={"favoritePlayerId": None},
    )
    payload = body["results"][0]["payload"]
    assert payload["subject"]["player"]["playerId"] == featured["pie_leader"]
    # …and the server says who it picked, so the client can offer to pin them.
    assert body["resolvedContext"]["favoritePlayerId"] == featured["pie_leader"]


def test_favorite_team_falls_back_to_the_net_rating_leader(
    app_client: TestClient, featured: dict[str, int]
) -> None:
    payload = _payload(
        app_client, "four_factors", {"teamId": "$favorite_team", "season": "latest"}
    )
    assert payload["team"]["teamId"] == featured["net_rating_team"]


def test_league_leader_token_is_the_scoring_leader(
    app_client: TestClient, featured: dict[str, int]
) -> None:
    payload = _payload(
        app_client,
        "career_arc",
        {"playerId": "$league_leader", "metric": "per"},
    )
    assert payload["player"]["playerId"] == featured["scoring_leader"]


def test_a_stale_favorite_degrades_to_the_featured_subject(
    app_client: TestClient, featured: dict[str, int]
) -> None:
    """A favourite id the store has never heard of must not produce an empty tile."""
    body = _resolve(
        app_client,
        [
            {
                "id": "w1",
                "kind": "stat_tile",
                "size": "small",
                "config": {"subjectType": "player", "subjectId": "$favorite_player"},
            }
        ],
        context={"favoritePlayerId": 99_999_999},
    )
    payload = body["results"][0]["payload"]
    assert payload["subject"]["player"]["playerId"] == featured["pie_leader"]


def test_an_unknown_token_is_invalid_config(app_client: TestClient) -> None:
    result = _one(
        app_client,
        "stat_tile",
        {"subjectType": "player", "subjectId": "$most_improved"},
    )
    assert result["status"] == "error"
    assert result["error"]["code"] == "invalid_config"
    assert result["error"]["field"] == "subjectId"


def test_a_team_token_cannot_fill_a_player_field(ctx: ResolveContext) -> None:
    with pytest.raises(WidgetError) as caught:
        widget_base.resolve_subject_token("$favorite_team", "player", ctx, field="playerId")
    assert caught.value.code == "invalid_config"
    assert caught.value.field == "playerId"


def test_an_unknown_player_id_is_player_not_found(app_client: TestClient) -> None:
    result = _one(
        app_client, "player_snapshot", {"playerId": 99_999_999, "season": "latest"}
    )
    assert result["status"] == "error"
    assert result["error"]["code"] == "player_not_found"


def test_team_ids_accept_a_token(
    app_client: TestClient, featured: dict[str, int]
) -> None:
    """``team_pulse`` filters its scoreboard with ``["$favorite_team"]``."""
    payload = _payload(
        app_client,
        "scoreboard",
        {"date": "latest", "teamIds": ["$favorite_team"]},
    )
    for game in payload["games"]:
        assert featured["net_rating_team"] in (
            game["home"]["teamId"],
            game["away"]["teamId"],
        )


# --------------------------------------------------------------------------- era honesty


def test_a_pre_1997_per_game_advanced_metric_is_unavailable_not_zero(
    app_client: TestClient, a_player: int
) -> None:
    """A 1985-86 tile asking for a per-game advanced stat: ``null``, a note, no 500.

    ``off_rtg`` begins in 1996-97 with play-by-play. The tile still renders — it just renders
    honestly (``contracts/CONTRACT.md`` §6).
    """
    result = _one(
        app_client,
        "stat_tile",
        {
            "subjectType": "player",
            "subjectId": a_player,
            "metric": "off_rtg",
            "secondaryMetrics": [],
            "season": ERA_GAP_SEASON,
            "showSparkline": True,
        },
    )
    assert result["status"] == "partial"
    assert result["availability"] == "unavailable"
    assert result["notes"], "an unavailable metric must explain itself"
    assert any(ERA_GAP_SEASON in note for note in result["notes"])
    assert any("1996-97" in note for note in result["notes"])

    primary = result["payload"]["primary"]
    assert primary["value"] is None
    assert primary["value"] != 0
    assert primary["displayValue"] == catalog.EM_DASH
    assert primary["availability"] == "unavailable"
    assert primary["rank"] is None and primary["percentile"] is None
    assert result["payload"]["sparkline"] == []


def test_a_pre_1997_trend_chart_is_unavailable_not_empty_of_meaning(
    app_client: TestClient, a_player: int
) -> None:
    """TS% exists at season level from 1946-47 but per game only from 1996-97."""
    result = _one(
        app_client,
        "trend_chart",
        {
            "subjectType": "player",
            "subjectIds": [a_player],
            "metric": "ts_pct",
            "season": ERA_GAP_SEASON,
        },
    )
    assert result["status"] == "partial"
    assert result["availability"] == "unavailable"
    assert result["payload"]["series"] == []
    assert any("1996-97" in note for note in result["notes"])


def test_a_league_table_is_never_ordered_by_a_metric_the_era_lacks(
    app_client: TestClient, seeded_db: Session
) -> None:
    """A rank is a claim about the data; with no data there is no rank to claim.

    Sorting by an era-unavailable metric leaves every entry with the same sort key, so the order
    that survives is whatever the database returned. Handing that out as ranks 1..N produced a
    league table where a losing team outranked a winning one beside a row of em dashes —
    ``contracts/CONTRACT.md`` §6 in spirit: never render a number the data does not support.
    """
    seasons = sorted({season for (season,) in seeded_db.execute(
        select(TeamSeason.season).distinct()
    ).all()})
    old = [season for season in seasons if season < "1996-97"]
    assert old, "the seeded league needs a pre-1996-97 season for this test"
    season = old[-1]

    result = _one(
        app_client,
        "team_efficiency",
        {"season": season, "sortBy": "net_rtg", "style": "table"},
    )
    rows = result["payload"]["rows"]
    assert rows, f"no {season} team rows came back"

    # No fabricated positions: the client renders a non-positive rank as an em dash.
    assert all(row["rank"] == 0 for row in rows)
    assert all(row["values"]["net_rtg"] is None for row in rows)

    # Ordered by something the era did record, and the table says so.
    records = [
        (row["wins"] or 0) / max(1, (row["wins"] or 0) + (row["losses"] or 0)) for row in rows
    ]
    assert records == sorted(records, reverse=True), records
    assert any("win percentage" in note for note in result["notes"])


def test_a_pre_1997_shot_profile_explains_itself_rather_than_failing(
    app_client: TestClient, a_player: int
) -> None:
    result = _one(
        app_client,
        "shot_profile",
        {
            "subjectType": "player",
            "subjectId": a_player,
            "season": PRE_ADVANCED_SEASON,
        },
    )
    assert result["status"] == "partial"
    assert result["availability"] == "unavailable"
    payload = result["payload"]
    assert [zone["zone"] for zone in payload["zones"]] == [
        "rim",
        "paint_non_rim",
        "mid_range",
        "corner_three",
        "above_break_three",
    ]
    assert all(zone["fga"] is None for zone in payload["zones"])
    assert payload["note"] and "1996-97" in payload["note"]


def test_a_pre_advanced_season_snapshot_marks_the_missing_ratings(
    app_client: TestClient, seeded_db: Session
) -> None:
    """1992-93 has box scores but no ratings: those metrics are null, the rest are real."""
    player_id = int(
        seeded_db.execute(
            select(PlayerSeason.player_id)
            .where(PlayerSeason.season == PRE_ADVANCED_SEASON)
            .where(PlayerSeason.season_type == "Regular Season")
            .order_by(PlayerSeason.gp.desc(), PlayerSeason.player_id)
            .limit(1)
        ).scalar_one()
    )
    result = _one(
        app_client,
        "player_snapshot",
        {
            "playerId": player_id,
            "season": PRE_ADVANCED_SEASON,
            "metrics": ["ts_pct", "off_rtg"],
        },
    )
    values = {entry["metric"]: entry for entry in result["payload"]["metrics"]}
    assert values["off_rtg"]["value"] is None
    assert values["off_rtg"]["availability"] == "unavailable"
    assert values["ts_pct"]["value"] is not None
    assert 0.0 <= values["ts_pct"]["value"] <= 1.0
    assert result["payload"]["eraNote"]


def test_percentages_cross_the_wire_as_fractions(app_client: TestClient) -> None:
    payload = _payload(
        app_client, "leaderboard", {"metric": "ts_pct", "season": "latest", "limit": 5}
    )
    for row in payload["rows"]:
        value = row["value"]["value"]
        assert value is None or 0.0 <= value <= 1.0
        assert "%" in row["value"]["displayValue"]


# --------------------------------------------------------------------------- payload shapes


def test_stat_tile_payload_matches_section_four(app_client: TestClient, a_player: int) -> None:
    payload = _payload(
        app_client,
        "stat_tile",
        {
            "subjectType": "player",
            "subjectId": a_player,
            "metric": "ts_pct",
            "secondaryMetrics": ["pts", "usg_pct"],
            "season": "latest",
            "sparklineWindow": 5,
        },
    )
    assert set(payload) == {
        "subject",
        "context",
        "primary",
        "secondary",
        "sparkline",
        "sparklineMetric",
    }
    assert set(payload["subject"]) == {"type", "player", "team"}
    assert payload["subject"]["type"] == "player"
    assert payload["subject"]["team"] is None
    assert set(payload["primary"]) == METRIC_VALUE_KEYS
    assert [value["metric"] for value in payload["secondary"]] == ["pts", "usg_pct"]
    assert payload["sparklineMetric"] == "ts_pct"
    assert payload["sparkline"], "a current-season tile has games to draw"
    assert len(payload["sparkline"]) <= 5
    for point in payload["sparkline"]:
        assert set(point) == {"x", "y", "gameId"}
    # Oldest first, so the client can draw it left to right without sorting.
    dates = [point["x"] for point in payload["sparkline"]]
    assert dates == sorted(dates)
    assert payload["context"].startswith(CURRENT_SEASON)


def test_leaderboard_payload_matches_section_four(app_client: TestClient) -> None:
    payload = _payload(
        app_client,
        "leaderboard",
        {
            "subjectType": "player",
            "metric": "pie",
            "season": "latest",
            "limit": 5,
            "minGames": 5,
            "minMinutesPerGame": 20.0,
            "secondaryMetrics": ["pts", "min"],
        },
    )
    assert set(payload) == {
        "metric",
        "subjectType",
        "scope",
        "season",
        "seasonType",
        "perMode",
        "qualifier",
        "secondaryMetrics",
        "rows",
    }
    assert payload["metric"]["key"] == "pie"
    assert payload["qualifier"] == "Minimum 5 games and 20.0 minutes per game"
    assert [d["key"] for d in payload["secondaryMetrics"]] == ["pts", "min"]
    assert payload["rows"]
    ranks = [row["rank"] for row in payload["rows"]]
    assert ranks == sorted(ranks) and ranks[0] == 1
    for row in payload["rows"]:
        assert set(row) == {"rank", "player", "team", "value", "secondary", "season"}
        assert row["player"] is not None and row["team"] is None
        assert set(row["value"]) == METRIC_VALUE_KEYS
        assert [value["metric"] for value in row["secondary"]] == ["pts", "min"]
        assert row["season"] == CURRENT_SEASON
    values = [row["value"]["value"] for row in payload["rows"]]
    assert values == sorted(values, reverse=True), "PIE is better when higher"


def test_leaderboard_honours_its_qualifiers(app_client: TestClient, seeded_db: Session) -> None:
    payload = _payload(
        app_client,
        "leaderboard",
        {
            "subjectType": "player",
            "metric": "pts",
            "season": "latest",
            "limit": 50,
            "minGames": 10,
            "minMinutesPerGame": 30.0,
            "positions": ["C"],
        },
    )
    ids = [row["player"]["playerId"] for row in payload["rows"]]
    assert ids
    rows = (
        seeded_db.execute(
            select(PlayerSeason)
            .where(PlayerSeason.season == CURRENT_SEASON)
            .where(PlayerSeason.season_type == "Regular Season")
            .where(PlayerSeason.player_id.in_(ids))
        )
        .scalars()
        .all()
    )
    assert len(rows) == len(ids)
    for row in rows:
        assert (row.gp or 0) >= 10
        assert (row.min_pg or 0.0) >= 30.0
    for row_payload in payload["rows"]:
        assert "C" in (row_payload["player"]["position"] or "")
    assert "positions C" in payload["qualifier"]


def test_leaderboard_ascending_reverses_the_metrics_direction(app_client: TestClient) -> None:
    ascending = _payload(
        app_client,
        "leaderboard",
        {"metric": "pts", "season": "latest", "limit": 5, "minGames": 5, "ascending": True},
    )
    values = [row["value"]["value"] for row in ascending["rows"]]
    assert values == sorted(values), "reverse order means the lowest scorers first"
    assert "reversed order" in ascending["qualifier"]


def test_all_time_leaderboard_names_each_rows_season(app_client: TestClient) -> None:
    payload = _payload(
        app_client,
        "leaderboard",
        {
            "metric": "pts",
            "scope": "all_time",
            "limit": 10,
            "minGames": 5,
            "minMinutesPerGame": 0.0,
        },
    )
    assert payload["scope"] == "all_time"
    assert payload["rows"]
    seasons = {row["season"] for row in payload["rows"]}
    assert all(catalog.is_season_string(season) for season in seasons)
    assert "best single seasons in league history" in payload["qualifier"]


def test_game_log_payload_matches_section_four(app_client: TestClient, a_player: int) -> None:
    payload = _payload(
        app_client,
        "game_log",
        {
            "playerId": a_player,
            "season": "latest",
            "limit": 5,
            "columns": ["min", "pts", "reb", "ast", "ts_pct"],
        },
    )
    assert set(payload) == {
        "player",
        "season",
        "seasonType",
        "columns",
        "seasonBests",
        "rows",
    }
    assert [d["key"] for d in payload["columns"]] == ["min", "pts", "reb", "ast", "ts_pct"]
    assert payload["rows"] and len(payload["rows"]) <= 5
    for row in payload["rows"]:
        # §4's example elides ``minutes``; §3's ``GameLogRow`` carries it and the client's
        # ``GameLogPayloadRow`` reads it for the MIN column, so it is present here too.
        assert set(row) == {
            "gameId",
            "date",
            "opponentAbbr",
            "isHome",
            "result",
            "score",
            "started",
            "minutes",
            "values",
            "availability",
        }
        assert set(row["values"]) == {"min", "pts", "reb", "ast", "ts_pct"}
        assert row["result"] in ("W", "L", "T", None)
    dates = [row["date"] for row in payload["rows"]]
    assert dates == sorted(dates, reverse=True), "rows are newest first"


def test_game_log_season_bests_span_the_whole_season(
    app_client: TestClient, app_client_season_best_player: int
) -> None:
    """The best is the season's, not the page's — that is the whole point of the field."""
    player_id = app_client_season_best_player
    short = _payload(
        app_client,
        "game_log",
        {"playerId": player_id, "season": "latest", "limit": 5, "columns": ["pts"]},
    )
    whole = _payload(
        app_client,
        "game_log",
        {"playerId": player_id, "season": "latest", "limit": 82, "columns": ["pts"]},
    )
    page_best = max(row["values"]["pts"] for row in short["rows"])
    season_best = max(row["values"]["pts"] for row in whole["rows"])
    # The fixture picks someone whose best night is *outside* the page on purpose: if the
    # bests were computed from the returned rows this assertion is the one that catches it.
    assert season_best > page_best
    assert short["seasonBests"]["pts"] == season_best
    assert short["seasonBests"] == whole["seasonBests"]


@pytest.fixture(scope="module")
def app_client_season_best_player(seeded_engine: Engine) -> int:
    """A player whose best scoring game is *not* in his five most recent — the hard case."""
    from nbastats.models import Game, PlayerGameBasic

    with Session(seeded_engine, future=True) as session:
        candidates = (
            session.execute(
                select(PlayerSeason.player_id)
                .where(PlayerSeason.season == CURRENT_SEASON)
                .where(PlayerSeason.season_type == "Regular Season")
                .where(PlayerSeason.gp >= 10)
                .order_by(PlayerSeason.gp.desc())
                .limit(40)
            )
            .scalars()
            .all()
        )
        for player_id in candidates:
            rows = session.execute(
                select(PlayerGameBasic.pts)
                .join(Game, Game.game_id == PlayerGameBasic.game_id)
                .where(PlayerGameBasic.player_id == player_id)
                .where(Game.season == CURRENT_SEASON)
                .where(Game.season_type == "Regular Season")
                .order_by(Game.game_date.desc(), Game.game_id.desc())
            ).scalars().all()
            points = [value for value in rows if value is not None]
            if len(points) > 5 and max(points[:5]) < max(points):
                return int(player_id)
        pytest.skip("the seeded league has nobody whose best game is outside his last five")


def test_trend_chart_payload_matches_section_four(
    app_client: TestClient, a_player: int
) -> None:
    payload = _payload(
        app_client,
        "trend_chart",
        {
            "subjectType": "player",
            "subjectIds": [a_player],
            "metric": "ts_pct",
            "season": "latest",
            "rollingWindow": 3,
        },
    )
    assert set(payload) == {"metric", "rollingWindow", "leagueAverage", "yDomain", "series"}
    assert payload["rollingWindow"] == 3
    assert set(payload["yDomain"]) == {"min", "max"}
    assert payload["yDomain"]["min"] < payload["yDomain"]["max"]
    assert len(payload["series"]) == 1
    series = payload["series"][0]
    assert set(series) == {"id", "label", "colorIndex", "points"}
    assert series["id"] == str(a_player)
    assert series["colorIndex"] == 0
    assert series["points"]
    for point in series["points"]:
        assert set(point) == {"x", "y", "rolling", "gameId", "opponentAbbr"}
    # The rolling line only starts once the window is full (metrics.rolling_average).
    assert all(point["rolling"] is None for point in series["points"][:2])
    assert series["points"][2]["rolling"] is not None

    values = [point["y"] for point in series["points"] if point["y"] is not None]
    assert min(values) >= payload["yDomain"]["min"]
    assert max(values) <= payload["yDomain"]["max"]


def test_trend_chart_gives_each_series_its_own_colour(
    app_client: TestClient, seeded_db: Session
) -> None:
    ids = [
        int(value)
        for value in seeded_db.execute(
            select(PlayerSeason.player_id)
            .where(PlayerSeason.season == CURRENT_SEASON)
            .where(PlayerSeason.season_type == "Regular Season")
            .order_by(PlayerSeason.gp.desc(), PlayerSeason.player_id)
            .limit(4)
        ).scalars()
    ]
    payload = _payload(
        app_client,
        "trend_chart",
        {"subjectType": "player", "subjectIds": ids, "metric": "pts", "season": "latest"},
    )
    assert [series["colorIndex"] for series in payload["series"]] == [0, 1, 2, 3]
    assert [series["id"] for series in payload["series"]] == [str(i) for i in ids]


def test_four_factors_payload_matches_section_four(
    app_client: TestClient, a_team: int
) -> None:
    payload = _payload(
        app_client,
        "four_factors",
        {"teamId": a_team, "season": "latest", "showOpponent": True, "comparison": "league"},
    )
    assert set(payload) == {"team", "season", "seasonType", "offense", "defense"}
    assert [entry["key"] for entry in payload["offense"]] == [
        "efg_pct",
        "tov_pct",
        "oreb_pct",
        "ftr",
    ]
    assert [entry["key"] for entry in payload["defense"]] == [
        "opp_efg_pct",
        "opp_tov_pct",
        "opp_oreb_pct",
        "opp_ftr",
    ]
    assert [entry["weight"] for entry in payload["offense"]] == [0.40, 0.25, 0.20, 0.15]
    assert [entry["weight"] for entry in payload["defense"]] == [0.40, 0.25, 0.20, 0.15]
    for entry in payload["offense"] + payload["defense"]:
        assert set(entry) == {
            "key",
            "label",
            "weight",
            "value",
            "displayValue",
            "leagueAverage",
            "percentile",
            "rank",
        }
        assert entry["value"] is None or 0.0 <= entry["value"] <= 1.0
        assert entry["percentile"] is None or 0.0 <= entry["percentile"] <= 1.0


def test_four_factors_can_drop_the_defensive_side(app_client: TestClient, a_team: int) -> None:
    payload = _payload(
        app_client, "four_factors", {"teamId": a_team, "showOpponent": False}
    )
    assert payload["defense"] == []
    assert len(payload["offense"]) == 4


def test_scoreboard_payload_matches_section_four(app_client: TestClient) -> None:
    payload = _payload(app_client, "scoreboard", {"date": "latest"})
    assert set(payload) == {"date", "isLatestCompleted", "allFinal", "games"}
    assert payload["isLatestCompleted"] is True
    assert payload["games"]
    for game in payload["games"]:
        assert set(game) == GAME_REF_KEYS | {"topPerformers"}
        assert set(game["home"]) == {
            "teamId",
            "abbr",
            "name",
            "city",
            "nickname",
            "conference",
            "division",
        }
        assert game["date"] == payload["date"]
        for performer in game["topPerformers"]:
            assert set(performer) == {"player", "teamAbbr", "line", "value"}
            assert set(performer["value"]) == METRIC_VALUE_KEYS
            assert "PTS" in (performer["line"] or "")
        teams = {performer["teamAbbr"] for performer in game["topPerformers"]}
        assert len(teams) == len(game["topPerformers"]), "one performer per side"


def test_player_snapshot_payload_matches_section_four(
    app_client: TestClient, a_player: int
) -> None:
    payload = _payload(
        app_client,
        "player_snapshot",
        {"playerId": a_player, "season": "latest", "metrics": ["ts_pct", "usg_pct"]},
    )
    assert set(payload) == {
        "player",
        "season",
        "seasonType",
        "teamAbbr",
        "gp",
        "gs",
        "minutesPerGame",
        "metrics",
        "eraNote",
    }
    assert [entry["metric"] for entry in payload["metrics"]] == ["ts_pct", "usg_pct"]
    for entry in payload["metrics"]:
        assert set(entry) == METRIC_VALUE_KEYS
        assert entry["percentile"] is None or 0.0 <= entry["percentile"] <= 1.0


def test_comparison_payload_matches_section_four(
    app_client: TestClient, seeded_db: Session
) -> None:
    ids = [
        int(value)
        for value in seeded_db.execute(
            select(PlayerSeason.player_id)
            .where(PlayerSeason.season == CURRENT_SEASON)
            .where(PlayerSeason.season_type == "Regular Season")
            .order_by(PlayerSeason.pts.desc())
            .limit(3)
        ).scalars()
    ]
    payload = _payload(
        app_client,
        "comparison",
        {"playerIds": ids, "metrics": ["pts", "ts_pct"], "season": "latest"},
    )
    assert set(payload) == {
        "season",
        "seasonType",
        "normalization",
        "style",
        "metrics",
        "subjects",
    }
    assert len(payload["subjects"]) == 3
    for index, subject in enumerate(payload["subjects"]):
        assert set(subject) == {"player", "colorIndex", "values"}
        assert subject["colorIndex"] == index
        assert [value["metric"] for value in subject["values"]] == ["pts", "ts_pct"]
        assert all(value["percentile"] is not None for value in subject["values"])


def test_comparison_raw_normalization_drops_the_percentile(
    app_client: TestClient, seeded_db: Session
) -> None:
    ids = [
        int(value)
        for value in seeded_db.execute(
            select(PlayerSeason.player_id)
            .where(PlayerSeason.season == CURRENT_SEASON)
            .where(PlayerSeason.season_type == "Regular Season")
            .order_by(PlayerSeason.pts.desc())
            .limit(2)
        ).scalars()
    ]
    payload = _payload(
        app_client,
        "comparison",
        {
            "playerIds": ids,
            "metrics": ["pts"],
            "season": "latest",
            "normalization": "raw",
            "style": "table",
        },
    )
    assert payload["normalization"] == "raw"
    assert payload["style"] == "table"
    for subject in payload["subjects"]:
        assert subject["values"][0]["percentile"] is None
        assert subject["values"][0]["value"] is not None


def test_daily_movers_payload_matches_section_four(app_client: TestClient) -> None:
    payload = _payload(
        app_client, "daily_movers", {"date": "latest", "metric": "game_score", "limit": 5}
    )
    assert set(payload) == {"date", "metric", "direction", "rows"}
    assert payload["direction"] == "best"
    assert payload["rows"]
    for index, row in enumerate(payload["rows"], start=1):
        assert set(row) == {
            "rank",
            "player",
            "gameId",
            "opponentAbbr",
            "isHome",
            "result",
            "line",
            "value",
            "seasonAverage",
            "delta",
        }
        assert row["rank"] == index
        assert set(row["value"]) == METRIC_VALUE_KEYS
    values = [row["value"]["value"] for row in payload["rows"]]
    assert values == sorted(values, reverse=True)


def test_daily_movers_surprise_ranks_by_the_gap(app_client: TestClient) -> None:
    payload = _payload(
        app_client,
        "daily_movers",
        {"date": "latest", "metric": "game_score", "limit": 5, "direction": "surprise"},
    )
    assert payload["direction"] == "surprise"
    gaps = [abs(row["delta"]) for row in payload["rows"]]
    assert gaps == sorted(gaps, reverse=True)
    for row in payload["rows"]:
        assert row["seasonAverage"] is not None
        assert row["delta"] == pytest.approx(
            row["value"]["value"] - row["seasonAverage"], rel=1e-9
        )


def test_daily_movers_worst_is_the_other_end_of_the_table(app_client: TestClient) -> None:
    best = _payload(app_client, "daily_movers", {"date": "latest", "limit": 3})
    worst = _payload(
        app_client, "daily_movers", {"date": "latest", "limit": 3, "direction": "worst"}
    )
    assert worst["rows"][0]["value"]["value"] <= best["rows"][0]["value"]["value"]
    values = [row["value"]["value"] for row in worst["rows"]]
    assert values == sorted(values)


def test_team_efficiency_payload_matches_section_four(app_client: TestClient) -> None:
    payload = _payload(
        app_client, "team_efficiency", {"season": "latest", "sortBy": "net_rtg"}
    )
    assert set(payload) == {
        "season",
        "seasonType",
        "sortBy",
        "style",
        "leagueAverage",
        "rows",
    }
    assert set(payload["leagueAverage"]) == {"off_rtg", "def_rtg", "net_rtg", "pace"}
    assert len(payload["rows"]) == 30
    for row in payload["rows"]:
        assert set(row) == {"rank", "team", "wins", "losses", "values", "ranks"}
        assert set(row["values"]) == {"off_rtg", "def_rtg", "net_rtg", "pace"}
        assert set(row["ranks"]) == {"off_rtg", "def_rtg", "net_rtg", "pace"}
    net = [row["values"]["net_rtg"] for row in payload["rows"]]
    assert net == sorted(net, reverse=True)
    assert payload["rows"][0]["rank"] == 1
    assert payload["rows"][0]["ranks"]["net_rtg"] == 1


def test_team_efficiency_sorts_a_lower_is_better_metric_correctly(
    app_client: TestClient,
) -> None:
    payload = _payload(
        app_client, "team_efficiency", {"season": "latest", "sortBy": "def_rtg", "limit": 5}
    )
    ratings = [row["values"]["def_rtg"] for row in payload["rows"]]
    assert ratings == sorted(ratings), "the best defence is the lowest rating"
    assert payload["rows"][0]["ranks"]["def_rtg"] == 1


def test_team_efficiency_can_filter_to_one_conference(app_client: TestClient) -> None:
    payload = _payload(
        app_client, "team_efficiency", {"season": "latest", "conference": "East"}
    )
    assert payload["rows"]
    assert all(row["team"]["conference"] == "East" for row in payload["rows"])


def test_career_arc_payload_matches_section_four(
    app_client: TestClient, a_player: int
) -> None:
    payload = _payload(
        app_client,
        "career_arc",
        {"playerId": a_player, "metric": "per", "includePlayoffs": True},
    )
    assert set(payload) == {
        "player",
        "metric",
        "xAxis",
        "seasons",
        "playoffSeasons",
        "eraBoundaries",
        "peak",
    }
    assert payload["xAxis"] == "season"
    assert payload["seasons"]
    for entry in payload["seasons"]:
        assert set(entry) == {
            "season",
            "seasonType",
            "age",
            "teamAbbr",
            "gp",
            "value",
            "displayValue",
            "availability",
        }
        assert entry["seasonType"] == "Regular Season"
    seasons = [entry["season"] for entry in payload["seasons"]]
    assert seasons == sorted(seasons, key=catalog.season_sort_key), "oldest first"
    assert len(seasons) == len(set(seasons)), "one point per season"

    for boundary in payload["eraBoundaries"]:
        assert set(boundary) == {"season", "label", "detail"}
    assert {b["season"] for b in payload["eraBoundaries"]} == {"1951-52", "1996-97"}

    peak = payload["peak"]
    assert set(peak) == {"season", "value", "displayValue"}
    assert peak["value"] == max(
        entry["value"] for entry in payload["seasons"] if entry["value"] is not None
    )


def test_career_arc_overlays_the_postseason(
    app_client: TestClient, seeded_db: Session
) -> None:
    player_id = int(
        seeded_db.execute(
            select(PlayerSeason.player_id)
            .where(PlayerSeason.season_type == "Playoffs")
            .order_by(PlayerSeason.gp.desc())
            .limit(1)
        ).scalar_one()
    )
    payload = _payload(
        app_client,
        "career_arc",
        {"playerId": player_id, "metric": "ts_pct", "includePlayoffs": True},
    )
    assert payload["playoffSeasons"]
    assert all(
        entry["seasonType"] == "Playoffs" for entry in payload["playoffSeasons"]
    )

    without = _payload(
        app_client,
        "career_arc",
        {"playerId": player_id, "metric": "ts_pct", "includePlayoffs": False},
    )
    assert without["playoffSeasons"] == []


def test_shot_profile_payload_matches_section_four(
    app_client: TestClient, a_player: int
) -> None:
    payload = _payload(
        app_client,
        "shot_profile",
        {"subjectType": "player", "subjectId": a_player, "season": "latest"},
    )
    assert set(payload) == {
        "subject",
        "season",
        "seasonType",
        "zones",
        "threePointRate",
        "freeThrowRate",
        "note",
    }
    assert [zone["zone"] for zone in payload["zones"]] == [
        "rim",
        "paint_non_rim",
        "mid_range",
        "corner_three",
        "above_break_three",
    ]
    for zone in payload["zones"]:
        assert set(zone) == {
            "zone",
            "label",
            "fga",
            "fgPct",
            "shareOfFga",
            "pointsPerShot",
            "leagueFgPct",
            "leagueShareOfFga",
        }
        for key in ("fgPct", "shareOfFga", "leagueFgPct", "leagueShareOfFga"):
            assert zone[key] is None or 0.0 <= zone[key] <= 1.0


# --------------------------------------------------------------------------- /v1/leaders


def test_leaders_returns_the_leaderboard_payload_plus_a_cursor(
    app_client: TestClient,
) -> None:
    response = app_client.get(
        "/v1/leaders",
        params={"metric": "pts", "season": "latest", "limit": 5, "minGames": 5},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {
        "metric",
        "subjectType",
        "scope",
        "season",
        "seasonType",
        "perMode",
        "qualifier",
        "secondaryMetrics",
        "rows",
        "nextCursor",
    }
    assert len(payload["rows"]) == 5
    assert payload["nextCursor"]

    second = app_client.get(
        "/v1/leaders",
        params={
            "metric": "pts",
            "season": "latest",
            "limit": 5,
            "minGames": 5,
            "cursor": payload["nextCursor"],
        },
    ).json()
    assert [row["rank"] for row in second["rows"]] == [6, 7, 8, 9, 10]
    first_ids = {row["player"]["playerId"] for row in payload["rows"]}
    second_ids = {row["player"]["playerId"] for row in second["rows"]}
    assert not (first_ids & second_ids)


def test_leaders_and_the_widget_agree(app_client: TestClient) -> None:
    """One code path: the drill-down screen cannot disagree with the tile it came from."""
    query = {
        "metric": "ts_pct",
        "season": "latest",
        "limit": 5,
        "minGames": 5,
        "minMinutesPerGame": 20.0,
        "secondaryMetrics": "pts,min",
    }
    route = app_client.get("/v1/leaders", params=query).json()
    widget = _payload(
        app_client,
        "leaderboard",
        {
            "metric": "ts_pct",
            "season": "latest",
            "limit": 5,
            "minGames": 5,
            "minMinutesPerGame": 20.0,
            "secondaryMetrics": ["pts", "min"],
        },
    )
    route.pop("nextCursor")
    assert route == widget


def test_leaders_rejects_an_unknown_metric(app_client: TestClient) -> None:
    response = app_client.get("/v1/leaders", params={"metric": "not_a_metric"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"
    assert response.json()["error"]["field"] == "metric"


def test_leaders_rejects_a_forged_cursor(app_client: TestClient) -> None:
    response = app_client.get("/v1/leaders", params={"metric": "pts", "cursor": "!!!"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_leaders_serves_teams_too(app_client: TestClient) -> None:
    payload = app_client.get(
        "/v1/leaders",
        params={"metric": "net_rtg", "subjectType": "team", "season": "latest", "limit": 3},
    ).json()
    assert payload["subjectType"] == "team"
    for row in payload["rows"]:
        assert row["team"] is not None
        assert row["player"] is None


# --------------------------------------------------------------------------- plumbing


def test_the_memo_cache_stops_a_dashboard_from_repeating_itself(
    seeded_engine: Engine, a_player: int
) -> None:
    """Ten tiles asking the same question must hit the database once, not ten times."""
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    widget_config = {
        "subjectType": "player",
        "subjectId": a_player,
        "metric": "ts_pct",
        "season": "latest",
    }

    def count(copies: int) -> int:
        statements.clear()
        with Session(seeded_engine, future=True) as session:
            event.listen(seeded_engine, "before_cursor_execute", record)
            try:
                ctx = ResolveContext.from_request(session, None)
                config_, errors = catalog.validate_widget_config("stat_tile", widget_config)
                assert not errors
                for _ in range(copies):
                    RESOLVERS["stat_tile"](config_, ctx)
            finally:
                event.remove(seeded_engine, "before_cursor_execute", record)
        return len(statements)

    one = count(1)
    ten = count(10)
    assert one > 0
    assert ten <= one + 2, f"{ten} statements for ten identical tiles, {one} for one"


def test_an_all_time_board_does_not_query_once_per_row(seeded_engine: Engine) -> None:
    """Ranking fifty seasons must not mean fifty round trips for fifty league averages."""
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    with Session(seeded_engine, future=True) as session:
        event.listen(seeded_engine, "before_cursor_execute", record)
        try:
            ctx_ = ResolveContext.from_request(session, None)
            config_, errors_ = catalog.validate_widget_config(
                "leaderboard",
                {
                    "metric": "pts",
                    "scope": "all_time",
                    "limit": 20,
                    "minGames": 1,
                    "minMinutesPerGame": 0.0,
                },
            )
            assert not errors_
            payload, _availability, _notes = RESOLVERS["leaderboard"](config_, ctx_)
        finally:
            event.remove(seeded_engine, "before_cursor_execute", record)

    assert len(payload["rows"]) == 20
    assert {row["season"] for row in payload["rows"]}
    assert len(statements) <= 8, f"{len(statements)} statements for a 20-row all-time board"
    # …and an all-time row carries no league average, because eighty seasons have no one
    # league to average.
    assert all(row["value"]["leagueAverage"] is None for row in payload["rows"])


def test_distribution_prefers_the_stored_league_average(ctx: ResolveContext) -> None:
    """The percentile bars and the "vs league" caption must describe the same field."""
    from nbastats.models import LeagueSeason

    stored = ctx.session.execute(
        select(LeagueSeason)
        .where(LeagueSeason.subject_type == "player")
        .where(LeagueSeason.season == CURRENT_SEASON)
        .where(LeagueSeason.season_type == "Regular Season")
        .where(LeagueSeason.metric_key == "ts_pct")
    ).scalar_one()
    spread = widget_queries.distribution(
        ctx, "ts_pct", "player", CURRENT_SEASON, "Regular Season"
    )
    assert spread.average == pytest.approx(stored.average)
    assert spread.sample_size >= 2
    assert list(spread.values) == sorted(spread.values)


def test_distribution_is_empty_for_a_metric_the_era_never_recorded(
    ctx: ResolveContext,
) -> None:
    spread = widget_queries.distribution(
        ctx, "off_rtg", "player", PRE_ADVANCED_SEASON, "Regular Season"
    )
    assert spread.sample_size == 0
    assert spread.for_subject(1) == (None, None)
    assert spread.delta(10.0) is None


def test_resolve_date_latest_is_the_last_completed_slate(ctx: ResolveContext) -> None:
    from nbastats.models import Game

    latest = ctx.session.execute(
        select(func.max(Game.game_date)).where(Game.status == "final")
    ).scalar_one()
    assert widget_base.resolve_date(ctx, "latest") == latest
    assert widget_base.resolve_date(ctx, None) == latest
    assert widget_base.resolve_date(ctx, "2026-01-02").isoformat() == "2026-01-02"
    with pytest.raises(WidgetError) as caught:
        widget_base.resolve_date(ctx, "not-a-date")
    assert caught.value.code == "invalid_config"
    assert caught.value.field == "date"


def test_resolve_season_accepts_a_season_the_store_does_not_hold(
    ctx: ResolveContext,
) -> None:
    """An unloaded season is a tile full of em dashes, not an error."""
    assert widget_base.resolve_season(ctx, "latest") == CURRENT_SEASON
    assert widget_base.resolve_season(ctx, None) == CURRENT_SEASON
    assert widget_base.resolve_season(ctx, ERA_GAP_SEASON) == ERA_GAP_SEASON
    with pytest.raises(WidgetError):
        widget_base.resolve_season(ctx, "last year")


def test_stat_line_only_mentions_what_is_worth_mentioning() -> None:
    assert widget_base.stat_line({"pts": 32, "reb": 8, "ast": 11}) == "32 PTS · 8 REB · 11 AST"
    assert widget_base.stat_line({"pts": 46, "reb": 2, "ast": 9}) == "46 PTS · 9 AST"
    assert widget_base.stat_line({"pts": None, "reb": None}) is None
