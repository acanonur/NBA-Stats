"""Headlines: links about a club or a player, and the link a person pastes.

What is stored, and what is not
-------------------------------
A headline is **a title, a link, a date and the name of the outlet**, nothing else. There is no
description column, no body and no summary anywhere in the schema, and this module never reads
article text: a feed's descriptions are not stored and an article is never fetched. A headline is
a pointer to somebody else's work, shown with who published it and when, so a reader goes there.

Where headlines come from
-------------------------
* Feeds, fetched by the worker (``el.news``, hourly) after an automatic ``robots.txt`` check; a
  disallow turns the feed off and the reason shows on ``/v1/el/sources``. Fetching is none of this
  module's business.
* A link a person pastes (``POST /v1/el/news/links``): the "Set from headline" flow starts from
  one. The link must be ``http`` or ``https``, its host must not be a gambling operator's (the
  same denylist the availability entries use: such a link is refused, not stored without its
  link, because a headline with no link is nothing), and it is attached to at least one club or
  player. Pasting the same link twice returns the first; it does not duplicate it.

A headline is never turned into a status. The availability flow *prefills* a source link, label
and date from a headline; a person always chooses the status.

Subjects
--------
A headline is about clubs and players. Club-only links are stored with an empty person code (the
table's key requires one). A player's club is the one he is registered with this season; a person
the store has never registered cannot be attached, and the request says which.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final
from urllib.parse import urlsplit

from sqlalchemy import select

from ...shared import refs
from ..models import ElIntelNewsItem, ElIntelNewsSubject
from ..profile import is_denied_url
from .queries import ReadContext, aware, bad_request, naive_utc

__all__ = ["DEFAULT_LIMIT", "build_news", "record_link"]

DEFAULT_LIMIT: Final = 10
_MAX_LIMIT: Final = 50
_MAX_FUTURE_SECONDS: Final = 300


def _item_payload(
    ctx: ReadContext, item: ElIntelNewsItem, subjects: list[ElIntelNewsSubject]
) -> dict[str, Any]:
    clubs = sorted({s.club_code for s in subjects})
    people = sorted({(s.person_code, s.club_code) for s in subjects if s.person_code})
    return {
        "itemId": item.item_id,
        "title": item.title,
        "link": item.link,
        "publishedAt": refs.rfc3339(item.published_at),
        "sourceName": item.source_name,
        "teams": [ctx.team_ref(c) for c in clubs],
        "players": [ctx.player_ref(p, c) for p, c in people],
    }


def build_news(
    ctx: ReadContext,
    *,
    team_id: str | None,
    player_id: str | None,
    limit: int | None,
    freshness: dict[str, Any],
) -> dict[str, Any]:
    """``NewsLinks``: the newest headlines, optionally about one club and/or one player."""
    size = DEFAULT_LIMIT if limit is None else limit
    if not 1 <= size <= _MAX_LIMIT:
        raise bad_request(f"limit must be between 1 and {_MAX_LIMIT}.", "limit")
    club = ctx.require_club(team_id) if team_id else None
    session = ctx.session
    statement = select(ElIntelNewsItem).order_by(
        ElIntelNewsItem.published_at.desc(), ElIntelNewsItem.item_id.desc()
    )
    if club is not None or player_id:
        subject = select(ElIntelNewsSubject.item_id)
        if club is not None:
            subject = subject.where(ElIntelNewsSubject.club_code == club)
        if player_id:
            subject = subject.where(ElIntelNewsSubject.person_code == player_id.strip())
        statement = statement.where(ElIntelNewsItem.item_id.in_(subject))
    items = list(session.execute(statement.limit(size)).scalars())
    by_item: dict[int, list[ElIntelNewsSubject]] = {}
    if items:
        for row in session.execute(
            select(ElIntelNewsSubject).where(
                ElIntelNewsSubject.item_id.in_([i.item_id for i in items])
            )
        ).scalars():
            by_item.setdefault(row.item_id, []).append(row)
    return {
        "league": "euroleague",
        "freshness": freshness,
        "items": [_item_payload(ctx, i, by_item.get(i.item_id, [])) for i in items],
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
    team_ids: list[str],
    player_ids: list[str],
) -> dict[str, Any]:
    """Store a pasted headline (``POST /v1/el/news/links``). Does not commit."""
    name_title = title.strip()
    outlet = source_name.strip()
    if not name_title or len(name_title) > 300:
        raise bad_request("title must be between 1 and 300 characters.", "title")
    if not outlet or len(outlet) > 80:
        raise bad_request("sourceName must be between 1 and 80 characters.", "sourceName")
    parts = urlsplit(link.strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise bad_request("link must be an http or https link.", "link")
    if is_denied_url(link):
        raise bad_request(
            "That link points at a gambling operator's site and is not stored.", "link"
        )
    moment = _parse_moment(published_at, ctx.now)
    if not team_ids and not player_ids:
        raise bad_request("Attach the link to at least one club or player.", "teamIds")

    subjects: set[tuple[str, str]] = set()
    for code in team_ids:
        subjects.add((ctx.require_club(code), ""))
    for person in player_ids:
        code = person.strip()
        reg = ctx.registration_of(code)
        if reg is None:
            raise bad_request(f"{code!r} is not registered with any club this season.", "playerIds")
        subjects.add((reg.club_code, code))

    session = ctx.session
    url = link.strip()
    existing = (
        session.execute(
            select(ElIntelNewsItem).where(
                ElIntelNewsItem.link == url, ElIntelNewsItem.feed_id.is_(None)
            )
        )
        .scalars()
        .first()
    )
    created = existing is None
    item = existing
    if item is None:
        item = ElIntelNewsItem(
            feed_id=None,
            guid=None,
            title=name_title,
            link=url,
            published_at=naive_utc(moment),
            fetched_at=naive_utc(ctx.now),
            source_name=outlet,
        )
        session.add(item)
        session.flush()
    have = {
        (s.club_code, s.person_code)
        for s in session.execute(
            select(ElIntelNewsSubject).where(ElIntelNewsSubject.item_id == item.item_id)
        ).scalars()
    }
    for club, person in sorted(subjects - have):
        session.add(ElIntelNewsSubject(item_id=item.item_id, club_code=club, person_code=person))
    session.flush()
    stored = list(
        session.execute(
            select(ElIntelNewsSubject).where(ElIntelNewsSubject.item_id == item.item_id)
        ).scalars()
    )
    return {"league": "euroleague", "created": created, "item": _item_payload(ctx, item, stored)}
