"""Write the golden response payloads in ``contracts/fixtures/``.

These files are the seam between the two halves of Hardwood. The iOS target decodes every
one of them in its unit tests and replays them in demo mode
(``ios/NBAStats/Networking/DemoAPIClient.swift``), so a key this module renames is a crash on
a device rather than a failing assertion in a diff. Three rules follow from that, and this
module exists to enforce all three:

1. **Never hand-written.** Every document here comes out of a real route, driven through
   ``TestClient`` against a real seeded database. A fixture cannot drift from the service,
   because it *is* the service's answer.
2. **Byte-for-byte reproducible.** Same seed, same as-of date, same bytes — so
   ``git diff --exit-code`` after a regeneration is a meaningful CI check. Two things are
   otherwise non-deterministic and are pinned here: the wall clock (:data:`FIXTURE_NOW`,
   which every ``generatedAt``/``serverTime`` quotes) and the request id
   (:data:`FIXTURE_REQUEST_ID`, sent as ``X-Request-Id`` so the middleware echoes it instead
   of minting a uuid).
3. **Field order preserved.** ``indent=2`` and *no* ``sort_keys``: the order is the pydantic
   model's declaration order, which is the order ``contracts/CONTRACT.md`` documents. A
   sorted file would still decode, but it would stop being readable next to the contract.

The demo league is the synthetic one from :mod:`nbastats.seed` — six seasons chosen to cover
every era rule the contract names: two before the 1996-97 advanced boundary (so
``availability`` is genuinely ``"estimated"`` in places), one mid-2000s, and three
consecutive modern seasons so a career arc has something to draw. ``as_of`` is 2026-01-02,
the date the contract's own examples use, which leaves the newest season in progress with
scheduled games ahead of it.

Run it with::

    python3 -m nbastats.fixtures_export --out ../contracts/fixtures

then ``scripts/sync_contracts.sh`` to copy the result into the iOS bundle.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import catalog
from . import config as config_module
from . import db as db_module
from .api import deps as deps_module
from .api.app import create_app

# Imported for their side effect as much as for their names: the clock freeze below rebinds
# ``utcnow`` on every module that imported it, so those modules have to exist first.
from .api import routes_dashboard as _routes_dashboard  # noqa: F401
from .api import routes_sync as _routes_sync  # noqa: F401
from .api.routes_players import fold_name
from .db import create_db_engine, init_db
from .models import Player, PlayerSeason
from .seed import seed_database

__all__ = [
    "FIXTURE_SEASONS",
    "FIXTURE_AS_OF",
    "FIXTURE_GAMES_PER_TEAM",
    "FIXTURE_PLAYERS_PER_TEAM",
    "FIXTURE_NOW",
    "FIXTURE_REQUEST_ID",
    "FIXTURE_LAYOUT_ID",
    "FIXTURE_ERA_SEASON",
    "WIDGET_CONFIGS",
    "ERA_WIDGET_CONFIGS",
    "ERROR_WIDGET_CONFIGS",
    "Subjects",
    "fixture_names",
    "export_fixtures",
    "build_fixtures",
    "write_fixtures",
    "dumps",
    "main",
]

# --------------------------------------------------------------------------- the demo league

#: Two pre-advanced seasons, one mid-2000s, and three consecutive modern ones. The gap
#: matters: ``availability`` is only interesting when the store actually holds a season the
#: league had no possession data for.
FIXTURE_SEASONS: tuple[str, ...] = (
    "1985-86",
    "1992-93",
    "2005-06",
    "2023-24",
    "2024-25",
    "2025-26",
)

#: "Today" for the fixtures — the date ``contracts/CONTRACT.md`` uses in its own examples.
#: The newest season is in progress on this date, so the store holds final *and* scheduled
#: games and ``GET /v1/games?date=latest`` has a real answer.
FIXTURE_AS_OF = date(2026, 1, 2)

#: Enough of a schedule that the 15-game leaderboard qualifier is cleared by early January,
#: small enough that a regeneration takes seconds rather than minutes.
FIXTURE_GAMES_PER_TEAM = 58
FIXTURE_PLAYERS_PER_TEAM = 10

#: Every ``generatedAt`` / ``serverTime`` in the fixtures. Frozen so a regeneration that
#: changed nothing produces no diff. Matches the contract's example timestamp.
FIXTURE_NOW = datetime(2026, 1, 3, 7, 12, 44)

#: Sent as ``X-Request-Id``; the middleware honours an inbound id, so error envelopes quote
#: this instead of a fresh uuid.
FIXTURE_REQUEST_ID = "0f1c4d2ba7e65930"

#: The ``layoutId`` the resolve fixture was built from.
FIXTURE_LAYOUT_ID = "hardwood.fixtures.dashboard"

#: Environment the export runs under, whatever the caller's shell says. No API key (the
#: contract's public read surface), no limiter (a deployment courtesy, not a payload), and
#: demo mode off — the store is seeded explicitly, so letting startup seed it too would only
#: hide a broken seeder.
FIXTURE_ENVIRONMENT: dict[str, Optional[str]] = {
    "HARDWOOD_API_KEY": None,
    "HARDWOOD_DEMO_MODE": "0",
    "HARDWOOD_RATE_LIMIT": "0",
    "CURRENT_SEASON": FIXTURE_SEASONS[-1],
}

# --------------------------------------------------------------------------- widget configs

#: One configuration per widget kind, in catalog order. These are the fourteen tiles the
#: ``dashboard_resolve`` fixture asks for, and each result's payload is also written out on
#: its own as ``widget_<kind>.json``, so the two can never disagree.
#:
#: The subject fields use ``$`` tokens rather than raw ids on purpose: the fixtures then
#: exercise the token resolution in ``nbastats.widgets.base`` as well as the payload shapes,
#: and the concrete ids that come back are the ones the request's ``context`` supplied.
WIDGET_CONFIGS: dict[str, dict[str, Any]] = {
    "stat_tile": {
        "subjectType": "player",
        "subjectId": "$favorite_player",
        "metric": "ts_pct",
        "secondaryMetrics": ["pts", "usg_pct"],
        "season": "latest",
        "seasonType": "Regular Season",
        "perMode": "PerGame",
        "showSparkline": True,
        "sparklineWindow": 15,
    },
    "player_snapshot": {
        "playerId": "$favorite_player",
        "season": "latest",
        "seasonType": "Regular Season",
        "metrics": [
            "ts_pct",
            "usg_pct",
            "ast_pct",
            "reb_pct",
            "off_rtg",
            "def_rtg",
            "net_rtg",
            "pie",
        ],
        "showPercentiles": True,
    },
    "leaderboard": {
        "subjectType": "player",
        "metric": "ts_pct",
        "scope": "season",
        "season": "latest",
        "seasonType": "Regular Season",
        "perMode": "PerGame",
        "limit": 10,
        "minGames": 15,
        "minMinutesPerGame": 20.0,
        "positions": [],
        "teamIds": [],
        "secondaryMetrics": ["pts", "min"],
        "ascending": False,
    },
    "game_log": {
        "playerId": "$favorite_player",
        "season": "latest",
        "seasonType": "Regular Season",
        "columns": [
            "min",
            "pts",
            "reb",
            "ast",
            "ts_pct",
            "usg_pct",
            "plus_minus",
            "game_score",
        ],
        "limit": 10,
        "highlightSeasonBest": True,
    },
    "trend_chart": {
        "subjectType": "player",
        "subjectIds": ["$favorite_player", "$league_leader"],
        "metric": "ts_pct",
        "season": "latest",
        "seasonType": "Regular Season",
        "rollingWindow": 5,
        "showLeagueAverage": True,
        "showRawPoints": True,
    },
    "four_factors": {
        "teamId": "$favorite_team",
        "season": "latest",
        "seasonType": "Regular Season",
        "showOpponent": True,
        "comparison": "league",
    },
    "shot_profile": {
        "subjectType": "player",
        "subjectId": "$favorite_player",
        "season": "latest",
        "seasonType": "Regular Season",
        "compareToLeague": True,
    },
    "comparison": {
        "playerIds": ["$favorite_player", "$league_leader", "$featured_player"],
        "metrics": ["pts", "ts_pct", "usg_pct", "ast_pct", "reb_pct", "net_rtg"],
        "season": "latest",
        "seasonType": "Regular Season",
        "normalization": "percentile",
        "style": "bars",
    },
    "scoreboard": {"date": "latest", "showTopPerformers": True, "teamIds": []},
    "daily_movers": {
        "date": "latest",
        "metric": "game_score",
        "limit": 8,
        "minMinutes": 12.0,
        "direction": "best",
    },
    "team_efficiency": {
        "season": "latest",
        "seasonType": "Regular Season",
        "sortBy": "net_rtg",
        "conference": "all",
        "limit": 30,
        "style": "table",
    },
    "next_game_projection": {
        "playerId": "$favorite_player",
        "stats": ["pts", "reb", "ast", "fg3m", "stl", "blk", "tov"],
        "opponentTeamId": None,
        "interval": "80",
        "showCombo": True,
        "showFactors": True,
        "season": "latest",
        "seasonType": "Regular Season",
    },
    "projection_board": {
        "date": "latest",
        "scope": "league",
        "playerIds": [],
        "teamId": None,
        "metrics": ["pts", "reb", "ast"],
        "limit": 6,
        "minMinutes": 20.0,
        "reference": "season_average",
        "seasonType": "Regular Season",
    },
    "career_arc": {
        "playerId": "$favorite_player",
        "metric": "per",
        "seasonType": "Regular Season",
        "includePlayoffs": True,
        "xAxis": "season",
    },
}

#: The season the era block below is written about: before 1996-97, so the league has box
#: scores but no possession data. ``contracts/CONTRACT.md`` §6 is the whole reason these
#: widgets are in the fixture — a client that has never decoded an ``"estimated"`` badge or a
#: ``null`` where a rating should be has not been tested against the league's real record.
FIXTURE_ERA_SEASON = "1985-86"

#: Four extra tiles appended to the resolve fixture, pointed at :data:`FIXTURE_ERA_SEASON`.
#: Between them they produce every availability the contract defines other than ``"full"``:
#: ``estimated`` (a career arc of box-score-derived PER), ``partial`` (a snapshot and a game
#: log whose modern columns are ``null``, never ``0``) and ``unavailable`` (a shot profile
#: for a season with no play-by-play). They come *after* the canonical set, so
#: ``resolvedContext`` still echoes the dashboard's modern subject.
ERA_WIDGET_CONFIGS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    (
        "e01.player_snapshot",
        "player_snapshot",
        {
            "playerId": "$era_player",
            "season": FIXTURE_ERA_SEASON,
            "seasonType": "Regular Season",
            "metrics": [
                "ts_pct",
                "usg_pct",
                "ast_pct",
                "reb_pct",
                "off_rtg",
                "def_rtg",
                "net_rtg",
                "pie",
            ],
            "showPercentiles": True,
        },
    ),
    (
        "e02.career_arc",
        "career_arc",
        {
            "playerId": "$era_player",
            "metric": "per",
            "seasonType": "Regular Season",
            "includePlayoffs": True,
            "xAxis": "season",
        },
    ),
    (
        "e03.game_log",
        "game_log",
        {
            "playerId": "$era_player",
            "season": FIXTURE_ERA_SEASON,
            "seasonType": "Regular Season",
            "columns": [
                "min",
                "pts",
                "reb",
                "ast",
                "ts_pct",
                "usg_pct",
                "plus_minus",
                "game_score",
            ],
            "limit": 5,
            "highlightSeasonBest": True,
        },
    ),
    (
        "e04.shot_profile",
        "shot_profile",
        {
            "subjectType": "player",
            "subjectId": "$era_player",
            "season": FIXTURE_ERA_SEASON,
            "seasonType": "Regular Season",
            "compareToLeague": True,
        },
    ),
)

#: One tile that is *expected* to fail, so the resolve fixture carries a real per-widget error.
#:
#: ``contracts/CONTRACT.md`` §3 documents ``"status": "error"`` with a populated ``error`` body
#: and a ``null`` payload, and `ResolveResult.init(from:)` is the most intricate hand-written
#: decoder in the iOS app — the payload / error / decodeError branch. Without this, no golden
#: fixture ever exercised it. A leaderboard is the honest way to produce one: the board *is* its
#: metric, so an era that never recorded it leaves nothing to rank, and the service answers
#: ``metric_unavailable`` (422) rather than an empty table. This is real service output, not a
#: hand-written result pasted into the fixture.
ERROR_WIDGET_CONFIGS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    (
        "x01.leaderboard",
        "leaderboard",
        {
            "metric": "off_rtg",
            "subjectType": "player",
            "scope": "season",
            "season": FIXTURE_ERA_SEASON,
            "seasonType": "Regular Season",
            "perMode": "PerGame",
            "limit": 10,
        },
    ),
)

#: Stand-in resolved from the seeded league rather than from the request's ``context``. It is
#: not a contract token — ``contracts/presets.json#/subjectTokens`` has no era token — so it
#: is substituted here, before the request is sent, and never reaches the service.
ERA_PLAYER_PLACEHOLDER = "$era_player"

#: The endpoint fixtures, in the order they are written.
ENDPOINT_FIXTURES: tuple[str, ...] = (
    "health",
    "meta",
    "presets",
    "sync",
    "player_search",
    "player_detail",
    "player_gamelog",
    "teams",
    "team_detail",
    "games_scoreboard",
    "game_box",
    "leaders",
    "dashboard_resolve",
    "error_not_found",
    "error_invalid_config",
)


def fixture_names() -> list[str]:
    """Every fixture this module writes, without the ``.json`` suffix.

    The widget fixtures are named from ``contracts/widgets.json`` rather than from a list
    here, so a kind added to the catalog turns into a missing file rather than into silence.
    """
    return [*ENDPOINT_FIXTURES, *(f"widget_{kind}" for kind in catalog.widget_kinds())]


# --------------------------------------------------------------------------- determinism


@contextmanager
def _frozen_clock(moment: datetime) -> Iterator[None]:
    """Pin :func:`nbastats.db.utcnow` everywhere it was imported, for the duration.

    Several modules did ``from .db import utcnow``, so rebinding the function on
    :mod:`nbastats.db` alone would miss them. Rebinding by identity catches every one that is
    already imported and leaves anything else alone.
    """
    original = db_module.utcnow

    def fixed() -> datetime:
        return moment

    patched: list[Any] = []
    for module in list(sys.modules.values()):
        if module is None:
            continue
        if getattr(module, "utcnow", None) is original:
            setattr(module, "utcnow", fixed)
            patched.append(module)
    try:
        yield
    finally:
        for module in patched:
            setattr(module, "utcnow", original)


@contextmanager
def _environment(**overrides: Optional[str]) -> Iterator[None]:
    """Apply environment overrides and drop every memoised view of them."""
    previous = {key: os.environ.get(key) for key in overrides}
    for key, value in overrides.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    config_module.reset_settings_cache()
    db_module.dispose_engine()
    deps_module.reset_rate_limiter()
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        config_module.reset_settings_cache()
        db_module.dispose_engine()
        deps_module.reset_rate_limiter()


def dumps(document: Any) -> str:
    """The canonical on-disk form: two-space indent, declaration order, trailing newline."""
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


# --------------------------------------------------------------------------- the database


def _seed_fixture_database(database_url: str) -> None:
    """Build the demo league the fixtures describe."""
    engine = create_db_engine(database_url)
    try:
        init_db(engine)
        with Session(engine, future=True) as session:
            seed_database(
                session,
                as_of=FIXTURE_AS_OF,
                seasons=list(FIXTURE_SEASONS),
                games_per_team=FIXTURE_GAMES_PER_TEAM,
                players_per_team=FIXTURE_PLAYERS_PER_TEAM,
            )
            session.commit()
    finally:
        engine.dispose()


@dataclass(frozen=True)
class Subjects:
    """Who the fixtures are about. Every id is chosen by a total order, so it never moves."""

    #: The reader's favourite: the longest-tenured player still active in the newest season.
    player_id: int
    #: The team that player logged the most minutes for in the newest season.
    team_id: int
    #: The leading scorer of :data:`FIXTURE_ERA_SEASON`, for the era block.
    era_player_id: int


def _team_for(session: Session, player_id: int, season: str) -> int:
    """The team a player logged the most minutes for in one season (ties by lowest id)."""
    return int(
        session.execute(
            select(PlayerSeason.team_id)
            .where(PlayerSeason.player_id == player_id)
            .where(PlayerSeason.season == season)
            .where(PlayerSeason.season_type == "Regular Season")
            .order_by(
                func.coalesce(PlayerSeason.minutes, 0.0).desc(),
                PlayerSeason.team_id.asc(),
            )
            .limit(1)
        ).scalar_one()
    )


def _choose_subjects(session: Session) -> Subjects:
    """Pick the players and team the fixtures are written about.

    The picks have to be stable across runs and interesting enough to exercise the payloads.
    The favourite is the longest-tenured player still active in the newest season — the most
    career seasons, then the most career minutes, then the lowest id — which is what gives
    the career arc and the trend chart something to draw.
    """
    newest = FIXTURE_SEASONS[-1]
    active_now = select(PlayerSeason.player_id).where(
        PlayerSeason.season == newest, PlayerSeason.season_type == "Regular Season"
    )
    player_id = int(
        session.execute(
            select(PlayerSeason.player_id)
            .where(PlayerSeason.season_type == "Regular Season")
            .where(PlayerSeason.player_id.in_(active_now))
            .group_by(PlayerSeason.player_id)
            .order_by(
                func.count(func.distinct(PlayerSeason.season)).desc(),
                func.coalesce(func.sum(PlayerSeason.minutes), 0.0).desc(),
                PlayerSeason.player_id.asc(),
            )
            .limit(1)
        ).scalar_one()
    )

    era_player_id = int(
        session.execute(
            select(PlayerSeason.player_id)
            .where(PlayerSeason.season == FIXTURE_ERA_SEASON)
            .where(PlayerSeason.season_type == "Regular Season")
            .order_by(
                func.coalesce(PlayerSeason.pts, 0.0).desc(), PlayerSeason.player_id.asc()
            )
            .limit(1)
        ).scalar_one()
    )

    return Subjects(
        player_id=player_id,
        team_id=_team_for(session, player_id, newest),
        era_player_id=era_player_id,
    )


def _search_needle(session: Session, player_id: int) -> str:
    """A query string that finds the fixture's player: the first letters of his surname."""
    player = session.get(Player, player_id)
    assert player is not None, f"the seeded league has no player {player_id}"
    folded = fold_name(player.last_name or player.full_name)
    return folded[:4] if len(folded) >= 4 else folded


# --------------------------------------------------------------------------- the fixtures


def _get(client: TestClient, url: str, /, **params: Any) -> Any:
    """A ``GET`` that must succeed, decoded with its key order intact."""
    response = client.get(url, params=params or None)
    if response.status_code != 200:
        raise RuntimeError(f"GET {url} {params} -> {response.status_code}: {response.text}")
    return response.json()


def _failing(client: TestClient, url: str, expected: int, /, **params: Any) -> Any:
    """A ``GET`` that must fail with ``expected``, for the error-envelope fixtures."""
    response = client.get(url, params=params or None)
    if response.status_code != expected:
        raise RuntimeError(
            f"GET {url} {params} -> {response.status_code}, expected {expected}: "
            f"{response.text}"
        )
    return response.json()


def _resolve_request(subjects: Subjects) -> dict[str, Any]:
    """The body of the ``dashboard_resolve`` fixture's request.

    One tile of every catalog kind first — those twelve results are what the
    ``widget_<kind>.json`` fixtures are cut from — then the era block from
    :data:`ERA_WIDGET_CONFIGS`, then the deliberately failing tile from
    :data:`ERROR_WIDGET_CONFIGS`.
    """
    canonical = [
        {
            "id": f"w{index:02d}.{kind}",
            "kind": kind,
            "size": catalog.widget(kind)["defaultSize"],
            "title": catalog.widget(kind)["name"],
            "config": WIDGET_CONFIGS[kind],
        }
        for index, kind in enumerate(catalog.widget_kinds(), start=1)
    ]
    era = [
        {
            "id": widget_id,
            "kind": kind,
            "size": catalog.widget(kind)["defaultSize"],
            "title": f"{catalog.widget(kind)['name']} · {FIXTURE_ERA_SEASON}",
            "config": {
                key: subjects.era_player_id if value == ERA_PLAYER_PLACEHOLDER else value
                for key, value in config.items()
            },
        }
        for widget_id, kind, config in ERA_WIDGET_CONFIGS
    ]
    failing = [
        {
            "id": widget_id,
            "kind": kind,
            "size": catalog.widget(kind)["defaultSize"],
            "title": f"{catalog.widget(kind)['name']} · {FIXTURE_ERA_SEASON}",
            "config": dict(config),
        }
        for widget_id, kind, config in ERROR_WIDGET_CONFIGS
    ]
    return {
        "layoutId": FIXTURE_LAYOUT_ID,
        "context": {
            "favoritePlayerId": subjects.player_id,
            "favoriteTeamId": subjects.team_id,
            "timeZone": "America/New_York",
            "asOf": FIXTURE_AS_OF.isoformat(),
        },
        "widgets": [*canonical, *era, *failing],
    }


def build_fixtures(client: TestClient, session: Session) -> dict[str, Any]:
    """Drive every route and return ``{fixture name: decoded document}``."""
    subjects = _choose_subjects(session)
    player_id, team_id = subjects.player_id, subjects.team_id
    documents: dict[str, Any] = {}

    documents["health"] = _get(client, "/v1/health")
    documents["meta"] = _get(client, "/v1/meta")
    documents["presets"] = _get(client, "/v1/presets")

    # ``since`` one behind the server, so the fixture shows a real delta rather than the
    # empty "nothing has changed" body a caught-up client gets.
    sync_version = int(documents["health"]["syncVersion"])
    documents["sync"] = _get(client, "/v1/sync", since=max(sync_version - 1, 0))

    documents["player_search"] = _get(
        client, "/v1/players/search", q=_search_needle(session, player_id), limit=10
    )
    documents["player_detail"] = _get(client, f"/v1/players/{player_id}")
    documents["player_gamelog"] = _get(
        client, f"/v1/players/{player_id}/gamelog", season="latest", limit=10
    )

    documents["teams"] = _get(client, "/v1/teams")
    documents["team_detail"] = _get(client, f"/v1/teams/{team_id}", season="latest")

    scoreboard = _get(client, "/v1/games", date="latest")
    documents["games_scoreboard"] = scoreboard
    if not scoreboard["games"]:
        raise RuntimeError("the seeded league has no games on its latest completed date")
    game_id = scoreboard["games"][0]["gameId"]
    documents["game_box"] = _get(client, f"/v1/games/{game_id}/box", view="both")

    documents["leaders"] = _get(
        client,
        "/v1/leaders",
        metric="ts_pct",
        limit=10,
        minGames=15,
        secondaryMetrics="pts,min",
    )

    resolve = client.post("/v1/dashboard/resolve", json=_resolve_request(subjects))
    if resolve.status_code != 200:
        raise RuntimeError(f"POST /v1/dashboard/resolve -> {resolve.status_code}: {resolve.text}")
    resolved = resolve.json()
    documents["dashboard_resolve"] = resolved

    # Each tile's payload, on its own, under its catalog kind — this is what demo mode reads.
    # The canonical block comes first, so the first result for a kind is the one to cut; the
    # era block that follows repeats three kinds deliberately and must not overwrite them.
    expected_failures = {widget_id for widget_id, _, _ in ERROR_WIDGET_CONFIGS}
    for result in resolved["results"]:
        if result["widgetId"] in expected_failures:
            # Declared failure (see ERROR_WIDGET_CONFIGS): it has no payload to cut, and it is
            # the only golden coverage the client's error branch gets. Still checked, because a
            # tile that quietly *stopped* failing would take that coverage away silently.
            if result["status"] != "error" or not result.get("error"):
                raise RuntimeError(
                    f"widget {result['widgetId']} is declared as a failing fixture but "
                    f"resolved as {result['status']}"
                )
            continue
        if result["status"] not in ("ok", "partial"):
            raise RuntimeError(
                f"widget {result['widgetId']} ({result['kind']}) resolved as "
                f"{result['status']}: {result.get('error')}"
            )
        documents.setdefault(f"widget_{result['kind']}", result["payload"])

    missing = [kind for kind in catalog.widget_kinds() if f"widget_{kind}" not in documents]
    if missing:
        raise RuntimeError(f"no payload was produced for {missing}")

    # §7's envelope, from the two failures a client meets most: an id that is not there, and
    # a widget configuration the catalog rejects.
    documents["error_not_found"] = _failing(client, "/v1/players/99999999", 404)
    documents["error_invalid_config"] = _failing(
        client, "/v1/leaders", 400, metric="pts", seasonType="Summer League"
    )

    return documents


def write_fixtures(out_dir: Path, documents: Mapping[str, Any]) -> list[Path]:
    """Write every document as ``<name>.json``; returns the paths, in write order."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name in fixture_names():
        if name not in documents:
            raise RuntimeError(f"nothing was built for fixture {name!r}")
        path = out_dir / f"{name}.json"
        path.write_text(dumps(documents[name]), encoding="utf-8")
        written.append(path)
    extra = sorted(set(documents) - set(fixture_names()))
    if extra:
        raise RuntimeError(f"built documents nothing is named for: {extra}")
    return written


def export_fixtures(out_dir: Path | str | None = None) -> dict[str, Any]:
    """Seed a demo league, drive every route, and return the fixture documents.

    When ``out_dir`` is given the documents are written there as well. The database lives in
    a temporary directory and is thrown away afterwards: nothing about the caller's own store
    can leak into a fixture.
    """
    target = Path(out_dir) if out_dir is not None else None
    with tempfile.TemporaryDirectory(prefix="hardwood-fixtures-") as workspace:
        database_url = f"sqlite:///{Path(workspace) / 'fixtures.db'}"
        with _environment(DATABASE_URL=database_url, **FIXTURE_ENVIRONMENT):
            with _frozen_clock(FIXTURE_NOW):
                _seed_fixture_database(database_url)
                engine = create_db_engine(database_url)
                try:
                    with TestClient(create_app()) as client, Session(
                        engine, future=True
                    ) as session:
                        client.headers["X-Request-Id"] = FIXTURE_REQUEST_ID
                        documents = build_fixtures(client, session)
                finally:
                    engine.dispose()
    if target is not None:
        write_fixtures(target, documents)
    return documents


# --------------------------------------------------------------------------- CLI


def default_out_dir() -> Path:
    """``<repo>/contracts/fixtures``, beside the catalogs the service already reads."""
    return catalog.contracts_dir() / "fixtures"


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``python3 -m nbastats.fixtures_export --out ../contracts/fixtures``."""
    parser = argparse.ArgumentParser(
        description="Write the golden contract fixtures the iOS client decodes."
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Directory to write into (default: <repo>/contracts/fixtures).",
    )
    parser.add_argument("--quiet", action="store_true", help="Print nothing on success.")
    args = parser.parse_args(argv)

    out_dir = Path(args.out).expanduser() if args.out else default_out_dir()
    documents = export_fixtures(out_dir)
    if not args.quiet:
        print(f"wrote {len(documents)} fixtures to {out_dir}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI
    raise SystemExit(main())
