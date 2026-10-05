"""``GET /v1/sources``: where every NBA number came from, and how current it is.

The workbook this feature grew from had a "Method / sources" sheet. A product that shows a
projection owes the same thing live, because the honest answer to "is this current?" differs per
source and changes while the app is open. The payload is a list of sources, each with a state
(``ok``, ``stale``, ``disabled``, ``notConfigured``, ``noReportYet``, ``blocked``, ``unreadable``
or ``error``), the sentence that explains it, when it last succeeded, its last error, when
``robots.txt`` was last checked (for a headline feed) and its attribution. A client renders it as a
"Sources and freshness" panel.

The keys are ``nba.stats``, ``nba.rosters``, ``nba.injuryReport`` and one ``nba.news.<feedId>`` per
headline feed. The injury report says plainly when its reader has *not yet been confirmed against a
real report*; a layout it does not recognise is ``unreadable``, never a guess; the demo league
reports it ``disabled`` with the reason that real statuses are not shown next to invented games.

This route answers even before any season is loaded, because an empty store is exactly when a
person needs to be told why. The builders are :mod:`nbastats.nba_matchup.sources`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from ..nba_matchup.queries import build_context, get_now
from ..nba_matchup.sources import build_source_list, freshness_for
from .deps import SessionDep

__all__ = ["router"]

router = APIRouter(tags=["sources"])

NowDep = Annotated[datetime, Depends(get_now)]


@router.get("/sources", summary="Where every number came from, and how current it is")
def sources(session: SessionDep, now: NowDep) -> dict[str, Any]:
    ctx = build_context(session, None, now=now, allow_empty=True)
    return build_source_list(ctx, freshness_for(ctx, ()))
