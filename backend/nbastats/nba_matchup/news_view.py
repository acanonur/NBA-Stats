"""Headlines: links about a team or a player, and the link a person pastes.

What is stored, and what is not
-------------------------------
A headline is **a title, a link, a date and the name of the outlet**, nothing else. There is no
description column, no body and no summary anywhere in the schema, and this module never reads
article text: a feed's descriptions are not stored and an article is never fetched. A headline is a
pointer to somebody else's work, shown with who published it and when, so a reader goes there.

Where headlines come from
-------------------------
* Feeds, fetched by the worker (``nba.news``, hourly) after an automatic ``robots.txt`` check; a
  disallow turns the feed off and the reason shows on ``/v1/sources``. Fetching is none of this
  module's business.
* A link a person pastes (``POST /v1/news/links``): the "Set from headline" flow starts from one.
  The link must be ``http`` or ``https`` and its host must not belong to a gambling operator (such
  a link is refused, not stored without its link, because a headline with no link is nothing).
  Pasting the same link twice returns the first; it does not duplicate it. Given no team or player,
  the title is matched against the store the way a feed headline is, and a name that is ambiguous
  attaches to nobody.

A headline is never turned into a status. The availability flow *prefills* a source link, label
and date from a headline; a person always chooses the status.

Subjects
--------
A headline is about teams and players. Team-only links are stored with player id 0 (the table's key
requires one). A player the store does not hold cannot be attached, and the request says so.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Sequence

from sqlalchemy import select

from ..api.errors import ApiError
from ..intel.feeds import clean_link
from ..nba_intel import news as intel_news
from ..nba_intel.models import NbaIntelNewsItem, NbaIntelNewsSubject
from ..shared import refs
from .queries import ReadContext, aware, bad_request, naive_utc

__all__ = ["DEFAULT_LIMIT", "MAX_LIMIT", "build_news", "record_link"]

DEFAULT_LIMIT: Final = 10
MAX_LIMIT: Final = 50
_MAX_FUTURE_SECONDS: Final = 300


def _player_id(raw: Any, field_name: str) -> int:
    if isinstance(raw, bool):
        raise bad_request(f"{raw!r} is not a player id.", field_name)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    raise ApiError("player_not_found", f"No player with id {raw}.", field=field_name)


def _item_payload(ctx: ReadContext, row: intel_news.NewsRow) -> dict[str, Any]:
    ctx.load_players(row.player_ids)
    return {
        "itemId": row.item_id,
        "title": row.title,
        "link": row.link,
        "publishedAt": refs.rfc3339(row.published_at),
        "sourceName": row.source_name,
        "teams": [ctx.team_ref(t) for t in row.team_ids],
        "players": [ctx.player_ref(p) for p in row.player_ids],
    }


def build_news(
    ctx: ReadContext,
    *,
    team_id: int | None,
    player_id: int | None,
    limit: int | None,
    freshness: dict[str, Any],
) -> dict[str, Any]:
    """``NewsLinks``: the newest headlines, optionally about one team and/or one player."""
    size = DEFAULT_LIMIT if limit is None else limit
    if not 1 <= size <= MAX_LIMIT:
        raise bad_request(f"limit must be between 1 and {MAX_LIMIT}.", "limit")
    if team_id is not None:
        ctx.require_team(team_id)
    if player_id is not None and ctx.player(player_id) is None:
        raise ApiError("player_not_found", f"No player with id {player_id}.", field="playerId")
    rows = intel_news.list_news(ctx.session, team_id=team_id, player_id=player_id, limit=size)
    return {
        "league": "nba",
        "freshness": freshness,
        "items": [_item_payload(ctx, row) for row in rows],
        "notes": ["Title, link, date and source only: article text is never fetched or stored."],
    }


def _parse_moment(value: datetime | str, now: datetime) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise bad_request(f"{value!r} is not an ISO date-time.", "publishedAt") from exc
    moment = aware(value)
    if (moment - aware(now)).total_seconds() > _MAX_FUTURE_SECONDS:  # type: ignore[operator]
        raise bad_request("publishedAt is in the future.", "publishedAt")
    return moment  # type: ignore[return-value]


def record_link(
    ctx: ReadContext,
    *,
    title: str,
    link: str,
    published_at: datetime | str,
    source_name: str,
    team_ids: Sequence[Any] = (),
    player_ids: Sequence[Any] = (),
) -> dict[str, Any]:
    """Store a pasted headline (``POST /v1/news/links``). Does not commit.

    Every id is checked against the store first, so a typo is a ``404`` and not a headline
    attached to nobody; the intel layer cleans the link, refuses a gambling operator's host and
    returns the stored row when the same link was pasted before.
    """
    moment = _parse_moment(published_at, ctx.now)
    teams: list[int] = []
    for raw in team_ids:
        teams.append(ctx.require_team(raw, field_name="teamIds"))
    players: list[int] = []
    for raw in player_ids:
        pid = _player_id(raw, "playerIds")
        if ctx.player(pid) is None:
            raise ApiError("player_not_found", f"No player with id {pid}.", field="playerIds")
        players.append(pid)
    cleaned = clean_link(link)
    existing = None
    if cleaned is not None:
        existing = ctx.session.execute(
            select(NbaIntelNewsItem.item_id).where(
                NbaIntelNewsItem.feed_id.is_(None), NbaIntelNewsItem.link == cleaned
            )
        ).first()
    try:
        item = intel_news.add_pasted_link(
            ctx.session,
            title=title,
            link=link,
            published_at=moment,
            source_name=source_name,
            team_ids=teams,
            player_ids=players,
            now=naive_utc(ctx.now),
        )
    except ValueError as exc:
        message = str(exc)
        field_name = (
            "link"
            if "link" in message or "host" in message
            else (
                "publishedAt"
                if "date" in message
                else "sourceName" if "source" in message else "title"
            )
        )
        raise bad_request(message, field_name) from exc
    subjects = ctx.session.execute(
        select(NbaIntelNewsSubject).where(NbaIntelNewsSubject.item_id == item.item_id)
    ).scalars()
    team_set: set[int] = set()
    player_set: set[int] = set()
    for subject in subjects:
        team_set.add(subject.team_id)
        if subject.player_id:
            player_set.add(subject.player_id)
    row = intel_news.NewsRow(
        item.item_id,
        item.title,
        item.link,
        item.published_at,
        item.source_name,
        tuple(sorted(team_set)),
        tuple(sorted(player_set)),
    )
    return {"league": "nba", "created": existing is None, "item": _item_payload(ctx, row)}
