"""``defense_by_position``: the points a defence allows, by the position of who scored them.

One team's defence, or the whole league's table, as the same object ``GET /v1/teams/{teamId}/
defense-by-position`` and ``GET /v1/defense-by-position`` return (and the ``/v1/el`` pair for the
EuroLeague). This module only chooses between the two by whether a team was named: the payload is
:data:`DefenseByPosition` for a team and ``DefenseByPositionTable`` for none, and the catalog says
so, so a client keys on the payload's own shape (a ``team`` key, or a ``teams`` list), not on the
config.

What the payload is, and is not
-------------------------------
It reports the points scored by opposing players *listed at* a position. It does not say who guarded
whom (that needs tracking data), and every payload carries a ``caveat`` that says so. The buckets
sum to the team's points allowed, a checksum the payload shows. Where a defence has played too few
games, or too much of the points allowed went to players with no listed position, the indices are
withheld (``withheld``, with the reason in words) while the raw buckets still show. There is no rank
anywhere, and the injury layer never moves a defence.

Config
------
``team`` (NBA) or ``club`` (EuroLeague): optional; none means the league table. ``window`` is 0 for
the whole season or the team's last N games. ``basis`` is per game or per 48 (NBA) or 40
(EuroLeague) minutes the opposing position played. ``scheme`` is ``gfc`` (guard, forward, center),
or ``workbook5`` (five positions), which is **EuroLeague only** and always an estimate: the NBA
publishes three positions, so asking the NBA for five shows the three and says why.

Availability
------------
The payload's own (``full``, or ``partial`` when coverage or the sample is thin; the EuroLeague's
estimate basis is labelled in the payload). When ``partial`` its notes become the result's.
"""

from __future__ import annotations

from typing import Any

from .. import nba_matchup
from ..api.errors import ApiError
from . import league_common as common
from .base import ResolveContext, resolve_season, resolve_subject_token

__all__ = ["resolve"]

_NBA_FIELDS = {"teamId": "team"}
_EL_FIELDS = {"clubCode": "club", "teamId": "club"}

_NOTE_NBA_THREE_WAY = (
    "The NBA publishes guard, forward and center only, so the five-position scheme is not "
    "available for it; the three positions are shown."
)


def resolve(config: dict[str, Any], ctx: ResolveContext) -> tuple[dict[str, Any], str, list[str]]:
    """Resolve one ``defense_by_position``."""
    league = common.league_of(config)
    window = int(config.get("window") or 0)
    basis = config.get("basis") or "perGame"
    if league == common.EUROLEAGUE_KEY:
        payload = _euroleague(config, window, basis)
        return common.outcome(payload)

    season = resolve_season(ctx, config.get("season"))
    team_id: int | None = None
    if config.get("team") is not None:
        team_id = resolve_subject_token(config.get("team"), "team", ctx, field="team")
        ctx.note_team(team_id)
    try:
        payload = nba_matchup.defense_by_position(
            ctx.session,
            team=team_id,
            season=season,
            window=window,
            basis=basis,
            now=common.now(),
        )
    except ApiError as exc:
        raise common.translate(exc, _NBA_FIELDS) from exc
    notes: list[str] | None = None
    availability = payload.get("availability")
    if config.get("scheme") == "workbook5":
        # Said whatever the data's own state is, so it is the result's note even on a full payload.
        notes = [_NOTE_NBA_THREE_WAY, *(payload.get("notes") or [])]
        if availability == "full":
            availability = "partial"
    return common.outcome(payload, availability=availability, notes=notes)


def _euroleague(config: dict[str, Any], window: int, basis: str) -> dict[str, Any]:
    season = common.euroleague_season(config.get("season"))
    stamp = common.now()
    with common.euroleague() as (session, read):
        try:
            return read.defense_by_position(
                session,
                club=config.get("club"),
                season=season,
                window=window,
                basis=basis,
                scheme=config.get("scheme") or "gfc",
                now=stamp,
            )
        except ApiError as exc:
            raise common.translate(exc, _EL_FIELDS) from exc
