"""``slate_projections``: projected scores for the next slate (NBA) or round (EuroLeague).

The same object ``GET /v1/projections`` and ``GET /v1/el/projections`` return. Each game carries
both sides' projected points, the margin and its range, the projected winner (or ``isTossUp``), the
combined points, how much each side's missing players are expected to cost it, and the model's
stated assumptions. The model is the workbook's team-score model, and nothing here compares it with
anything but what happened: there is no line to measure it against, no probability of winning, and
no input that could carry either. The payload's ``review`` block, when present, is how locked
projections compared with results.

Which slate
-----------
NBA: ``date`` is an ISO date, ``next`` (the next ET day with scheduled games; the default) or
``latest`` (the most recent completed day). A token is resolved within the dashboard's own season so
a dashboard opened on an old season shows that season; an explicit ISO date decides its season
itself. EuroLeague: ``round`` is a round number, or 0 for the next round, and ``date`` is ignored.
The slate is the same whichever league answers, by design, and a league that cannot answer does so
as the recoverable ``league_unavailable`` on this tile.

Availability
------------
A projection is an estimate, never a record, so a slate with games is ``estimated``, which is a
label and carries no result notes. A slate with no games (nothing scheduled, or a season the model
does not cover) is ``unavailable`` and says why, from the payload's notes.
"""

from __future__ import annotations

from typing import Any

from .. import nba_matchup
from ..api.errors import ApiError
from . import league_common as common
from .base import ResolveContext

__all__ = ["resolve"]

_TOKENS = ("next", "latest")

_NOTE_NO_GAMES = "No games are scheduled for this slate, so there is nothing to project."


def resolve(config: dict[str, Any], ctx: ResolveContext) -> tuple[dict[str, Any], str, list[str]]:
    """Resolve one ``slate_projections``."""
    league = common.league_of(config)
    if league == common.EUROLEAGUE_KEY:
        payload = _euroleague(config)
    else:
        payload = _nba(config, ctx)
    availability = payload.get("availability")
    if availability not in common.AVAILABILITIES:
        availability = "estimated" if payload.get("games") else "unavailable"
    if not payload.get("games"):
        availability = "unavailable"
    notes = list(payload.get("notes") or [])
    if availability == "unavailable" and _NOTE_NO_GAMES not in notes:
        notes.insert(0, _NOTE_NO_GAMES)
    return common.outcome(payload, availability=availability, notes=notes)


def _nba(config: dict[str, Any], ctx: ResolveContext) -> dict[str, Any]:
    day = config.get("date") or "next"
    # A token means "within the dashboard's season"; an explicit date finds its own season.
    season = ctx.season if day in _TOKENS else None
    try:
        return nba_matchup.slate_projections(ctx.session, day=day, season=season, now=common.now())
    except ApiError as exc:
        raise common.translate(exc, {"date": "date"}) from exc


def _euroleague(config: dict[str, Any]) -> dict[str, Any]:
    number = int(config.get("round") or 0)
    stamp = common.now()
    with common.euroleague() as (session, read):
        try:
            return read.slate_projections(
                session, round_number="next" if number == 0 else number, now=stamp
            )
        except ApiError as exc:
            raise common.translate(exc, {"round": "round", "round_number": "round"}) from exc
