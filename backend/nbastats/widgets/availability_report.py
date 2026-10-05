"""``availability_report``: who is out, doubtful or questionable, with where each status came from.

The same object ``GET /v1/availability`` and ``GET /v1/el/availability`` return: for the NBA the
statuses read from the league's official injury report (or entered by hand), for the EuroLeague the
sourced, dated entries researched from linked pages and the user's workbook. This module only picks
the scope and the league; the payload (``AvailabilityReport``, ``contracts/CONTRACT.md`` §4 and
§10) is the builder's.

What every entry carries
------------------------
A status never travels alone. Each entry has its ``source`` (kind, label, link, the date the source
published it), its age in minutes counted from that date and never from the fetch, whether it is
still *in force* (an old entry stops driving the model, but stays on the list marked stale), and,
for the NBA, whether the team's report had been submitted yet. "No report" is ``status: null``, an
em dash, never "available". A report that is old, unreadable, not yet published, or switched off
says so in its ``state`` and ``message``.

Config
------
``team`` (NBA) or ``club`` (EuroLeague): optional; none is the whole slate (NBA: the next day's
games; EuroLeague: every club). ``includeNews`` adds headline links: title, link, date and source
name only, never an article body, and only when a news feed has been switched on. A league that is
not mounted, or the NBA's demo league (whose injury statuses are deliberately not shown beside
invented games), answers through the payload's ``state``, not through an error.

Availability
------------
The payload has no ``availability`` key, so the result's is derived from its ``state``: ``fresh`` is
``full``, ``stale`` is ``partial``, anything else (no report yet, unreadable, disabled) is
``unavailable``. A non-``full`` result carries the payload's ``message`` as its note.
"""

from __future__ import annotations

from typing import Any

from .. import nba_matchup
from ..api.errors import ApiError
from . import league_common as common
from .base import ResolveContext, resolve_subject_token

__all__ = ["resolve"]

_NBA_FIELDS = {"teamId": "team"}
_EL_FIELDS = {"clubCode": "club", "teamId": "club"}


def resolve(config: dict[str, Any], ctx: ResolveContext) -> tuple[dict[str, Any], str, list[str]]:
    """Resolve one ``availability_report``."""
    league = common.league_of(config)
    include_news = bool(config.get("includeNews", False))
    if league == common.EUROLEAGUE_KEY:
        payload = _euroleague(config, include_news)
    else:
        payload = _nba(config, ctx, include_news)
    availability = common.state_availability(payload.get("state"))
    message = payload.get("message")
    return common.outcome(
        payload, availability=availability, notes=[message] if isinstance(message, str) else []
    )


def _nba(config: dict[str, Any], ctx: ResolveContext, include_news: bool) -> dict[str, Any]:
    team_id: int | None = None
    if config.get("team") is not None:
        team_id = resolve_subject_token(config.get("team"), "team", ctx, field="team")
        ctx.note_team(team_id)
    try:
        return nba_matchup.availability_report(
            ctx.session,
            team=team_id,
            day="next",
            include_news=include_news,
            season=ctx.season,
            now=common.now(),
        )
    except ApiError as exc:
        raise common.translate(exc, _NBA_FIELDS) from exc


def _euroleague(config: dict[str, Any], include_news: bool) -> dict[str, Any]:
    stamp = common.now()
    with common.euroleague() as (session, read):
        try:
            return read.availability_report(
                session, club=config.get("club"), include_news=include_news, now=stamp
            )
        except ApiError as exc:
            raise common.translate(exc, _EL_FIELDS) from exc
