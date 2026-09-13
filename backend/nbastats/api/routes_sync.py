"""``GET /v1/sync`` and the optional ``GET /v1/sync/stream``.

The freshness model in ``contracts/CONTRACT.md`` §8 is pull-based: ``sync_version``
increments once per finalized game, and the client polls ``/v1/sync`` on foreground, on
pull-to-refresh and from a background task. When it sends a ``since`` that already equals the
server's version the answer is deliberately tiny — ``hasChanges: false`` and nothing else —
because that is the call the app makes most often.

``/v1/sync/stream`` is the same body pushed over Server-Sent Events while the dashboard is
foregrounded. It is strictly an optimisation: everything works without it. The generator
polls the sync row, emits only on a version change, sends a comment heartbeat every 15
seconds so idle proxies keep the connection, and stops as soon as the client disconnects.
Database reads happen in a worker thread, so a slow query cannot block the event loop.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_sessionmaker, utcnow
from ..models import Game, PlayerGameBasic, SyncState
from .deps import SessionDep
from .schemas import SyncResponse
from .serializers import game_ref, load_teams

__all__ = [
    "router",
    "INVALIDATED_KINDS",
    "HEARTBEAT_SECONDS",
    "POLL_SECONDS",
    "IDLE_POLL_AFTER_SECONDS",
    "build_sync_response",
]

router = APIRouter()

#: Widget kinds whose cached payload a single finalized game invalidates outright. Season
#: aggregates are not listed: they move too, but within their own ``ttlSeconds``.
INVALIDATED_KINDS: tuple[str, ...] = ("scoreboard", "daily_movers", "leaderboard", "game_log")

#: Comment frame cadence on the SSE stream — enough to keep a proxy from timing the
#: connection out, rare enough to cost nothing.
HEARTBEAT_SECONDS = 15.0

#: How often the stream re-reads the sync row.
POLL_SECONDS = 1.0

#: What a quiet server tells the client to wait before polling again.
IDLE_POLL_AFTER_SECONDS = 900

#: Cap on how much detail one sync response carries; a client that has been away for a week
#: should re-fetch, not receive the week.
_MAX_FINALIZED_GAMES = 30
_MAX_AFFECTED_PLAYERS = 500


def build_sync_response(session: Session, since: int | None = None) -> SyncResponse:
    """The ``/v1/sync`` body, also used verbatim for each ``event: sync`` frame."""
    settings = get_settings()
    state = session.execute(select(SyncState).where(SyncState.id == 1)).scalar_one_or_none()
    version = (state.sync_version or 0) if state is not None else 0
    data_through = state.data_through if state is not None else None

    # Version 0 is a store that has ingested nothing: there is no change to report, and
    # saying otherwise would send every client chasing an empty delta.
    has_changes = version > 0 and (since is None or since < version)
    if not has_changes:
        return SyncResponse(
            sync_version=version,
            previous_version=since,
            server_time=utcnow(),
            data_through=data_through,
            has_changes=False,
            changed_dates=[],
            finalized_games=[],
            affected_player_ids=[],
            invalidate=[],
            next_poll_after_seconds=_next_poll_seconds(session),
        )

    window = max(1, settings.correction_window_days)
    changed_dates = (
        session.execute(
            select(Game.game_date)
            .where(Game.status == "final")
            .distinct()
            .order_by(Game.game_date.desc())
            .limit(window)
        )
        .scalars()
        .all()
    )

    finalized: list[Game] = []
    if changed_dates:
        finalized = (
            session.execute(
                select(Game)
                .where(Game.status == "final")
                .where(Game.game_date == changed_dates[0])
                .order_by(Game.game_id)
                .limit(_MAX_FINALIZED_GAMES)
            )
            .scalars()
            .all()
        )

    teams = load_teams(
        session,
        {game.home_team_id for game in finalized} | {game.away_team_id for game in finalized},
    )
    affected: list[int] = []
    if finalized:
        affected = sorted(
            set(
                session.execute(
                    select(PlayerGameBasic.player_id)
                    .where(PlayerGameBasic.game_id.in_([game.game_id for game in finalized]))
                    .distinct()
                    .limit(_MAX_AFFECTED_PLAYERS)
                )
                .scalars()
                .all()
            )
        )

    return SyncResponse(
        sync_version=version,
        previous_version=since,
        server_time=utcnow(),
        data_through=data_through,
        has_changes=True,
        changed_dates=list(changed_dates),
        finalized_games=[game_ref(game, teams=teams) for game in finalized],
        affected_player_ids=affected,
        invalidate=list(INVALIDATED_KINDS),
        next_poll_after_seconds=_next_poll_seconds(session),
    )


def _next_poll_seconds(session: Session) -> int:
    """Poll fast while a game is in progress, slowly when the league is asleep."""
    live = session.execute(
        select(Game.game_id).where(Game.status == "live").limit(1)
    ).first()
    if live is None:
        return IDLE_POLL_AFTER_SECONDS
    return max(30, get_settings().ingest_poll_seconds)


@router.get("/sync", response_model=SyncResponse, summary="What changed since a version")
def sync(
    session: SessionDep,
    since: Optional[int] = Query(None, ge=0, description="The client's last sync version."),
) -> SyncResponse:
    """What has changed since ``since``. Small and cheap when nothing has."""
    return build_sync_response(session, since)


def _sync_payload(since: int | None) -> dict[str, Any]:
    """Read the sync body on a worker thread, with its own short-lived session."""
    with get_sessionmaker()() as session:
        return build_sync_response(session, since).model_dump(mode="json", by_alias=True)


def _current_version() -> int:
    with get_sessionmaker()() as session:
        state = session.execute(
            select(SyncState).where(SyncState.id == 1)
        ).scalar_one_or_none()
        return (state.sync_version or 0) if state is not None else 0


@router.get(
    "/sync/stream",
    summary="Server-sent stream of sync events",
    response_class=StreamingResponse,
)
async def sync_stream(
    request: Request,
    since: Optional[int] = Query(None, ge=0),
    max_duration_seconds: Optional[int] = Query(
        None,
        alias="maxDurationSeconds",
        ge=1,
        le=3600,
        description="Close the stream after this long. Omit to stream until the client goes.",
    ),
) -> StreamingResponse:
    """Emit ``event: sync`` whenever the sync version moves; heartbeat in between.

    The first frame is always a full sync body, so a client that connects mid-session has a
    baseline without a second round trip.
    """

    async def events() -> AsyncIterator[str]:
        deadline = (
            time.monotonic() + max_duration_seconds if max_duration_seconds else None
        )
        payload = await run_in_threadpool(_sync_payload, since)
        last_version = int(payload.get("syncVersion", 0))
        yield f"event: sync\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
        next_heartbeat = time.monotonic() + HEARTBEAT_SECONDS

        try:
            while True:
                if await request.is_disconnected():
                    return
                if deadline is not None and time.monotonic() >= deadline:
                    return
                await asyncio.sleep(POLL_SECONDS)
                version = await run_in_threadpool(_current_version)
                if version != last_version:
                    body = await run_in_threadpool(_sync_payload, last_version)
                    last_version = version
                    next_heartbeat = time.monotonic() + HEARTBEAT_SECONDS
                    yield f"event: sync\ndata: {json.dumps(body, separators=(',', ':'))}\n\n"
                elif time.monotonic() >= next_heartbeat:
                    next_heartbeat = time.monotonic() + HEARTBEAT_SECONDS
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:  # pragma: no cover - client vanished mid-frame
            return

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "Connection": "keep-alive",
            # nginx buffers proxied responses by default, which would hold every frame.
            "X-Accel-Buffering": "no",
        },
    )
