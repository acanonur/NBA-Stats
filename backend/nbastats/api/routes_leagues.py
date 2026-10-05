"""``GET /v1/leagues``: the leagues this process serves, so a client never hard-codes a prefix.

One row per league, each with its ``apiPrefix``, whether it is enabled, its state and the reason,
whether its data is invented, the current season, its own sync cursor, the units a client formats
with (``regulationMinutes``, ``perModes``) and its position buckets. A client builds league
switching from this and from nothing else: the EuroLeague lives at ``/v1/el`` today, and a client
that read the prefix from here would not notice if that moved.

The NBA's row is built here, from the stats store (``nba_matchup.sources.league_row``). The
EuroLeague's is built by the EuroLeague's own package and reaches this module only through the
late-bound registry (:mod:`nbastats.shared.league_registry`), because nothing outside
``nbastats/euroleague/`` may import it. A league that is switched off, misconfigured or simply not
registered still gets a row: ``enabled: false`` with a ``state`` and a ``reason``, so a client can
show it as unavailable and say why instead of silently omitting it.

The row of a league that fails to describe itself is an ``error`` row, never a 500: this route is
how a client finds out what is wrong, so it is the one that must keep answering.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from ..nba_matchup import sources
from ..nba_matchup.queries import get_now
from ..shared import league_registry
from ..shared.league_profile import EUROLEAGUE_KEY
from .deps import SessionDep

__all__ = ["router"]

router = APIRouter(tags=["leagues"])

NowDep = Annotated[datetime, Depends(get_now)]


@router.get("/leagues", summary="The leagues this server serves, with their prefixes and states")
def leagues(session: SessionDep, now: NowDep) -> list[dict[str, Any]]:
    return [sources.league_row(session, now), league_registry.league_entry(EUROLEAGUE_KEY)]
