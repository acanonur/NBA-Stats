"""NBA headlines: which feeds are configured, what is kept, and what a headline is "about".

A headline in Hardwood is a pointer, not a copy. For each item of a feed this module stores a
title, a link, a publication date and the source's name (:mod:`nbastats.intel.feeds` guarantees
that is all the parser ever reads) and, when it can do so without guessing, which team or player the
title names. Reading the article is the reader's job: they follow the link.

Feeds
-----
Two candidate feeds ship configured, ``https://eurohoops.net/feed`` and
``https://talkbasket.net/feed``, and **both are unverified**: nobody has fetched either from the
development environment, whose network policy denies every news host. They are on by default
(``HARDWOOD_NEWS``), which is the lead's decision: Hardwood is private and the posture in
``docs/LEGAL.md`` §2e covers it. What replaces a terms review is behaviour, applied to every fetch
by :func:`nbastats.intel.feeds.fetch_feed`: ``robots.txt`` is read first and cached for a day, a
feed is fetched at most hourly with conditional requests, responses are capped at 2 MB, and a
document that declares a document type or an entity is refused.

A ``robots.txt`` disallow **switches the feed off** (``enabled`` is cleared, ``robots_state`` is
``disallowed``, and the reason is stored and shown in ``/v1/sources``). The disallow is re-checked
once a day through the same cached read, and a feed is turned back on **only if a disallow turned it
off**: a feed the person switched off by hand stays off. Defaults are seeded only into an empty feed
table, so removing or disabling one is never undone by the next run.

Subjects: a unique name or no link
----------------------------------
A headline is linked to a team when its title contains the team's full name or its nickname, and to
a player when it contains his full name (at least two words), compared as folded text on word
boundaries. A city alone is never enough ("Boston" is also a hockey team), and a phrase that belongs
to more than one team or player in the store produces **no** link at all: an ambiguous name is not
resolved by taking the first. A player is linked together with the team he last played for (his
latest line in this or the previous season), because a subject row needs a team.

Retention
---------
Feed items are kept 30 days from publication (:func:`prune_items`); a link a person pasted is kept.
An item already older than that is not stored in the first place.

What this module will not do: fetch an article, store a description, or turn a headline into an
availability status. "Set from headline" is a person pre-filling a form from a link and confirming
the status themselves.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from ..db import utcnow
from ..intel.feeds import (
    FeedFetch,
    clean_link,
    clean_title,
    feed_is_due,
    fetch_feed,
)
from ..intel.http import Conditional, PoliteClient
from ..intel.robots import RobotsChecker
from . import store
from .models import (
    NbaIntelNewsFeed,
    NbaIntelNewsItem,
    NbaIntelNewsSubject,
)
from .status import fold_name, is_denied

__all__ = [
    "DEFAULT_FEEDS",
    "RETENTION",
    "MAX_NEWS_LIMIT",
    "SubjectIndex",
    "SubjectMatches",
    "FeedRefresh",
    "NewsRow",
    "ensure_default_feeds",
    "add_feed",
    "due_feeds",
    "refresh_feed",
    "add_pasted_link",
    "list_news",
    "prune_items",
]

logger = logging.getLogger(__name__)

#: ``(name, url)`` of the candidate feeds seeded into an empty feed table. Both are unverified.
DEFAULT_FEEDS: tuple[tuple[str, str], ...] = (
    ("Eurohoops", "https://eurohoops.net/feed"),
    ("TalkBasket", "https://talkbasket.net/feed"),
)
RETENTION = timedelta(days=30)
MAX_NEWS_LIMIT = 100
_FUTURE_TOLERANCE = timedelta(days=1)


# ------------------------------------------------------------------------------ feeds


def ensure_default_feeds(session: Session) -> int:
    """Seed the candidate feeds into an *empty* feed table; return how many were added."""
    existing = session.execute(select(func.count()).select_from(NbaIntelNewsFeed)).scalar_one()
    if existing:
        return 0
    for name, url in DEFAULT_FEEDS:
        session.add(NbaIntelNewsFeed(name=name, url=url, enabled=True))
    session.flush()
    return len(DEFAULT_FEEDS)


def add_feed(session: Session, name: str, url: str, *, enabled: bool = True) -> NbaIntelNewsFeed:
    """Configure a feed. Raises :class:`ValueError` for an unusable URL or a duplicate."""
    cleaned_url = clean_link(url)
    if cleaned_url is None:
        raise ValueError("the feed URL must be an absolute http or https URL")
    if is_denied(cleaned_url):
        raise ValueError("that host is on the source denylist")
    label = clean_title(name)
    if label is None:
        raise ValueError("a feed needs a name")
    if (
        session.execute(select(NbaIntelNewsFeed).where(NbaIntelNewsFeed.url == cleaned_url))
        .scalars()
        .first()
    ):
        raise ValueError("that feed is already configured")
    feed = NbaIntelNewsFeed(name=label[:80], url=cleaned_url, enabled=enabled)
    session.add(feed)
    session.flush()
    return feed


def due_feeds(session: Session, now: datetime, *, force: bool = False) -> list[NbaIntelNewsFeed]:
    """Feeds to refresh now: enabled ones, and ones a ``robots.txt`` disallow switched off (so
    they can come back), each at most hourly unless ``force``."""
    rows = session.execute(select(NbaIntelNewsFeed).order_by(NbaIntelNewsFeed.feed_id)).scalars()
    out = []
    for feed in rows:
        eligible = feed.enabled or feed.robots_state == "disallowed"
        if eligible and (force or feed_is_due(feed.last_fetch_at, now)):
            out.append(feed)
    return out


# ----------------------------------------------------------------------------- subjects


@dataclass(frozen=True, slots=True)
class SubjectMatches:
    team_ids: frozenset[int] = frozenset()
    #: player id -> the team he last played for
    players: dict[int, int] = field(default_factory=dict)


class SubjectIndex:
    """Phrases that name a team or player uniquely within the NBA store. Built once per run."""

    def __init__(self, session: Session) -> None:
        self._teams: dict[str, int] = {}
        self._players: dict[str, tuple[int, int]] = {}
        self._player_team: dict[int, int] = {}
        self._build(session)

    def _build(self, session: Session) -> None:
        if not store.table_exists(session, "teams"):
            return
        team_phrases: dict[str, set[int]] = {}
        for team_id, name, nickname in session.execute(
            text("SELECT team_id, name, nickname FROM teams")
        ):
            for phrase in (fold_name(str(name)), fold_name(str(nickname))):
                if phrase:
                    team_phrases.setdefault(phrase, set()).add(int(team_id))
        self._teams = {p: next(iter(ids)) for p, ids in team_phrases.items() if len(ids) == 1}

        if not (store.table_exists(session, "players") and store.table_exists(session, "games")):
            return
        seasons = [
            row[0]
            for row in session.execute(
                text("SELECT DISTINCT season FROM games ORDER BY season DESC LIMIT 2")
            )
        ]
        if not seasons:
            return
        latest: dict[int, tuple[str, int]] = {}
        placeholders = ", ".join(f":s{i}" for i in range(len(seasons)))
        params = {f"s{i}": season for i, season in enumerate(seasons)}
        for player_id, team_id, last_date in session.execute(
            text(
                "SELECT b.player_id, b.team_id, MAX(g.game_date) FROM player_game_basic b "
                f"JOIN games g ON g.game_id = b.game_id WHERE g.season IN ({placeholders}) "
                "GROUP BY b.player_id, b.team_id"
            ),
            params,
        ):
            current = latest.get(int(player_id))
            if current is None or str(last_date) > current[0]:
                latest[int(player_id)] = (str(last_date), int(team_id))
        self._player_team = {pid: team for pid, (_, team) in latest.items()}

        phrases: dict[str, set[int]] = {}
        for player_id, full_name in session.execute(
            text("SELECT player_id, full_name FROM players WHERE is_active = :active"),
            {"active": True},  # a bound boolean: `= 1` would not run on Postgres
        ):
            folded = fold_name(str(full_name))
            if len(folded.split()) >= 2 and int(player_id) in self._player_team:
                phrases.setdefault(folded, set()).add(int(player_id))
        for phrase, ids in phrases.items():
            if len(ids) == 1:
                pid = next(iter(ids))
                self._players[phrase] = (pid, self._player_team[pid])

    def team_of(self, player_id: int) -> int | None:
        return self._player_team.get(player_id)

    def match(self, title: str) -> SubjectMatches:
        padded = f" {fold_name(title)} "
        teams = {tid for phrase, tid in self._teams.items() if f" {phrase} " in padded}
        players = {
            pid: team for phrase, (pid, team) in self._players.items() if f" {phrase} " in padded
        }
        return SubjectMatches(frozenset(teams), players)


def _add_subjects(session: Session, item_id: int, matches: SubjectMatches) -> None:
    pairs: set[tuple[int, int]] = {(team, 0) for team in matches.team_ids}
    pairs.update((team, player) for player, team in matches.players.items())
    for team_id, player_id in sorted(pairs):
        session.add(NbaIntelNewsSubject(item_id=item_id, team_id=team_id, player_id=player_id))


# ----------------------------------------------------------------------------- refresh


@dataclass(frozen=True, slots=True)
class FeedRefresh:
    """What one feed refresh did, for the job's summary and the source state."""

    feed_id: int
    name: str
    state: str
    reason: str | None
    new_items: int = 0
    skipped: int = 0


def _naive(value: datetime) -> datetime:
    return value if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)


def _record_robots(feed: NbaIntelNewsFeed, result: FeedFetch, now: datetime) -> None:
    if result.robots_state is not None:
        feed.robots_state = result.robots_state
        feed.robots_checked_on = _naive(result.robots_checked_at or now).date()
        feed.robots_reason = (result.robots_reason or "")[:300] or None


def refresh_feed(
    session: Session,
    client: PoliteClient,
    robots: RobotsChecker,
    feed: NbaIntelNewsFeed,
    *,
    now: datetime,
    index: SubjectIndex | None = None,
) -> FeedRefresh:
    """Fetch one feed, store its new items and record the outcome. The caller commits.

    The network call happens inside this function; callers that care about holding a write lock
    across it should pass a session that has not written yet (the jobs do).
    """
    moment = _naive(now)
    was_disallowed = feed.robots_state == "disallowed"
    conditional = (
        Conditional(feed.etag, feed.last_modified) if (feed.etag or feed.last_modified) else None
    )
    result = fetch_feed(
        client,
        robots,
        feed.url,
        source_name=feed.name,
        conditional=conditional,
        now=now,
        link_allowed=lambda link: not is_denied(link),
    )
    key = store.news_source_key(feed.feed_id)
    feed.last_fetch_at = moment
    feed.last_status = result.state[:40]
    _record_robots(feed, result, moment)

    if result.state == "disallowed":
        feed.enabled = False
        feed.robots_state = "disallowed"
        store.set_source_state(
            session,
            key,
            "disabled",
            now=moment,
            error=result.reason,
            detail={"label": feed.name, "reason": result.reason},
        )
        return FeedRefresh(feed.feed_id, feed.name, "disallowed", result.reason)

    if result.state in ("ok", "notModified"):
        if was_disallowed and not feed.enabled:
            feed.enabled = True  # robots.txt now allows it, and a disallow is what turned it off
        feed.robots_reason = None if result.robots_state == "allowed" else feed.robots_reason
        new_items = skipped = 0
        if result.state == "ok" and result.parsed is not None:
            feed.etag = (result.etag or "")[:200] or None
            feed.last_modified = (result.last_modified or "")[:100] or None
            new_items, skipped = _store_items(session, feed, result, moment, index)
        store.set_source_state(
            session,
            key,
            "ok",
            now=moment,
            success=True,
            detail={"label": feed.name, "newItems": new_items},
        )
        return FeedRefresh(feed.feed_id, feed.name, result.state, None, new_items, skipped)

    state = {
        "blocked": "blocked",
        "unreadable": "unreadable",
        "tooLarge": "unreadable",
    }.get(result.state, "error")
    store.set_source_state(
        session,
        key,
        state,
        now=moment,
        error=result.reason,
        paused_until=(_naive(result.paused_until) if result.paused_until else None),
        detail={"label": feed.name},
    )
    return FeedRefresh(feed.feed_id, feed.name, result.state, result.reason)


def _store_items(
    session: Session,
    feed: NbaIntelNewsFeed,
    result: FeedFetch,
    now: datetime,
    index: SubjectIndex | None,
) -> tuple[int, int]:
    assert result.parsed is not None
    cutoff = now - RETENTION
    existing = {
        guid
        for (guid,) in session.execute(
            select(NbaIntelNewsItem.guid).where(NbaIntelNewsItem.feed_id == feed.feed_id)
        )
    }
    subjects = index if index is not None else SubjectIndex(session)
    added = skipped = 0
    for item in result.parsed.items:
        if item.guid in existing or item.published_at < cutoff:
            skipped += 1
            continue
        row = NbaIntelNewsItem(
            feed_id=feed.feed_id,
            guid=item.guid,
            title=item.title,
            link=item.link,
            published_at=item.published_at,
            fetched_at=now,
            source_name=item.source_name,
        )
        session.add(row)
        session.flush()
        _add_subjects(session, row.item_id, subjects.match(item.title))
        existing.add(item.guid)
        added += 1
    return added, skipped


# --------------------------------------------------------------------------- pasted links


def add_pasted_link(
    session: Session,
    *,
    title: str,
    link: str,
    published_at: datetime,
    source_name: str,
    team_ids: Iterable[int] = (),
    player_ids: Iterable[int] = (),
    now: datetime | None = None,
    index: SubjectIndex | None = None,
) -> NbaIntelNewsItem:
    """Store a link a person pasted (no feed, kept past retention). The caller commits.

    The same cleaning as a feed item applies, and a link to a host on the source denylist is
    refused outright (there is no label-only form of a headline). Re-pasting an existing link
    returns the stored row. When no teams or players are given, the title is matched against the
    store exactly as a feed headline would be.
    """
    moment = _naive(now) if now is not None else utcnow()
    clean = clean_link(link)
    if clean is None:
        raise ValueError("the link must be an absolute http or https URL")
    if is_denied(clean):
        raise ValueError("that host is on the source denylist, so its link is not stored")
    headline = clean_title(title)
    if headline is None:
        raise ValueError("a headline needs a title")
    source = clean_title(source_name)
    if source is None:
        raise ValueError("a headline needs the source's name")
    published = _naive(published_at)
    if published > moment + _FUTURE_TOLERANCE:
        raise ValueError("the publication date is in the future")

    found = (
        session.execute(
            select(NbaIntelNewsItem).where(
                NbaIntelNewsItem.feed_id.is_(None), NbaIntelNewsItem.link == clean
            )
        )
        .scalars()
        .first()
    )
    if found is not None:
        return found

    row = NbaIntelNewsItem(
        feed_id=None,
        guid=None,
        title=headline,
        link=clean,
        published_at=published,
        fetched_at=moment,
        source_name=source[:80],
    )
    session.add(row)
    session.flush()

    teams, players = set(team_ids), set(player_ids)
    subjects = index if index is not None else SubjectIndex(session)
    if not teams and not players:
        _add_subjects(session, row.item_id, subjects.match(headline))
    else:
        matches = SubjectMatches(
            frozenset(teams),
            {pid: team for pid in players if (team := subjects.team_of(pid)) is not None},
        )
        _add_subjects(session, row.item_id, matches)
    session.flush()
    return row


# -------------------------------------------------------------------------------- reading


@dataclass(frozen=True, slots=True)
class NewsRow:
    item_id: int
    title: str
    link: str
    published_at: datetime
    source_name: str
    team_ids: tuple[int, ...]
    player_ids: tuple[int, ...]


def list_news(
    session: Session,
    *,
    team_id: int | None = None,
    player_id: int | None = None,
    limit: int = 10,
) -> list[NewsRow]:
    """Newest headlines first, optionally only those linked to a team or player."""
    count = max(1, min(int(limit), MAX_NEWS_LIMIT))
    stmt = select(NbaIntelNewsItem)
    if team_id is not None or player_id is not None:
        match = select(NbaIntelNewsSubject.item_id)
        if team_id is not None:
            match = match.where(NbaIntelNewsSubject.team_id == team_id)
        if player_id is not None:
            match = match.where(NbaIntelNewsSubject.player_id == player_id)
        stmt = stmt.where(NbaIntelNewsItem.item_id.in_(match))
    items = (
        session.execute(
            stmt.order_by(
                NbaIntelNewsItem.published_at.desc(), NbaIntelNewsItem.item_id.desc()
            ).limit(count)
        )
        .scalars()
        .all()
    )
    if not items:
        return []
    subjects = session.execute(
        select(NbaIntelNewsSubject).where(
            NbaIntelNewsSubject.item_id.in_([i.item_id for i in items])
        )
    ).scalars()
    teams: dict[int, set[int]] = {}
    players: dict[int, set[int]] = {}
    for subject in subjects:
        teams.setdefault(subject.item_id, set()).add(subject.team_id)
        if subject.player_id:
            players.setdefault(subject.item_id, set()).add(subject.player_id)
    return [
        NewsRow(
            i.item_id,
            i.title,
            i.link,
            i.published_at,
            i.source_name,
            tuple(sorted(teams.get(i.item_id, ()))),
            tuple(sorted(players.get(i.item_id, ()))),
        )
        for i in items
    ]


def prune_items(session: Session, now: datetime) -> int:
    """Delete feed items published more than 30 days ago, and their subjects. Pasted links stay.

    Returns the number of items removed. The caller commits.
    """
    cutoff = _naive(now) - RETENTION
    old = select(NbaIntelNewsItem.item_id).where(
        NbaIntelNewsItem.feed_id.is_not(None), NbaIntelNewsItem.published_at < cutoff
    )
    ids = [row[0] for row in session.execute(old)]
    if not ids:
        return 0
    session.execute(delete(NbaIntelNewsSubject).where(NbaIntelNewsSubject.item_id.in_(ids)))
    session.execute(delete(NbaIntelNewsItem).where(NbaIntelNewsItem.item_id.in_(ids)))
    session.flush()
    return len(ids)
