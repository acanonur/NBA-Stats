"""RSS 2.0 and Atom headlines: a title, a link, a date and the source's name, and nothing else.

What is stored, and the line that is never crossed
--------------------------------------------------
A headline feed is a convenience for the person who owns the Mac: "here is what was published
about this team today, go and read it". It is not a copy of anyone's journalism. So the unit of
storage is exactly four facts: the **title**, the **link**, the **published date** and the
**source's name**. The parser reads exactly those child elements and no others. In particular it
never reads RSS ``description``, ``content:encoded``, Atom ``summary`` or ``content``, or any
media element: the body of an item is simply not looked at, and :class:`FeedItem` has no field
that could hold it, so a later edit cannot start storing it by accident without adding one. The
article itself is never fetched; the link is only ever shown to a person, who follows it.

Untrusted XML, handled before it is parsed
------------------------------------------
A feed is a document written by someone else, fetched from a URL, and parsed by a parser with a
history of entity-expansion attacks (a few hundred bytes of nested entity declarations that
expand into gigabytes). Three defences, applied in this order, each independently sufficient for
its own failure:

1. **A 2 MB cap**, enforced by the client while streaming (:data:`MAX_FEED_BYTES`) and checked
   again here, so a body handed in from anywhere is held to the same limit.
2. **No document type and no entity declarations.** Any ``<!DOCTYPE`` or ``<!ENTITY`` anywhere in
   the document refuses the whole feed. The check runs on the *decoded text* as well as on the
   raw bytes with NULs removed, because a UTF-16 document hides its ASCII keywords from a naive
   byte search. Without a DTD the parser can only expand the five built-in entities and numeric
   character references, which cannot amplify. (Real feeds do not use a DOCTYPE; the cost of the
   rule is zero feeds.)
3. **Fail closed.** Malformed XML, an unknown root element, a feed with no channel or entries:
   each raises :class:`FeedUnreadable` with a reason, and the caller records "unreadable" instead
   of guessing.

Cleaning what is kept
---------------------
Titles are text, not markup: markup that arrives escaped is stripped, control characters and
Unicode bidirectional overrides (which can make a headline display as something it is not) are
removed, whitespace is collapsed and the result is capped at 300 characters. A link must be an
absolute ``http`` or ``https`` URL without credentials. A date must parse and be plausible, because
``published_at`` is not nullable downstream and inventing one would present a fetch time as a
publication time; an item without a usable date is skipped and counted, never dated "now". A link
on the source denylist is dropped through the caller's ``link_allowed`` hook.

The fetch around the parser
---------------------------
:func:`fetch_feed` is the whole polite sequence for one feed URL: check ``robots.txt`` first (the
verdict is also installed as the client's ``allow`` hook so every redirect hop is checked), make
a conditional request, and translate every outcome into a :class:`FeedFetch` whose ``state`` the
caller records: ``ok``, ``notModified``, ``disallowed``, ``robotsUnavailable``, ``unreadable``,
``tooLarge``, ``blocked`` or ``error``. It never raises for an expected failure. :func:`feed_is_due`
is the "at most hourly" rule.
"""

from __future__ import annotations

import html
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Iterable, Mapping

from dateutil import parser as dateutil_parser

from .http import (
    CircuitOpenError,
    Conditional,
    FetchError,
    PoliteClient,
    RefusedError,
    TooLargeError,
    TransportFailure,
    check_url,
    link_host,
)
from .robots import DISALLOWED, UNAVAILABLE, RobotsChecker

__all__ = [
    "MAX_FEED_BYTES",
    "MAX_TITLE_CHARS",
    "MIN_FETCH_INTERVAL",
    "FeedUnreadable",
    "FeedItem",
    "ParsedFeed",
    "FeedFetch",
    "parse_feed",
    "clean_title",
    "clean_link",
    "parse_feed_date",
    "fetch_feed",
    "feed_is_due",
    "host_matches",
]

#: The response cap, in bytes. 2 MB is generous for a headline feed and tiny for an attack.
MAX_FEED_BYTES = 2_000_000
MAX_TITLE_CHARS = 300
MAX_LINK_CHARS = 2048
MAX_GUID_CHARS = 512
#: A feed is fetched at most this often.
MIN_FETCH_INTERVAL = timedelta(hours=1)
#: The newest items kept from one fetch.
MAX_ITEMS = 100
#: A publication date this far ahead of now is a clock error, not a headline.
FUTURE_TOLERANCE = timedelta(days=1)
_EARLIEST_YEAR = 2000

_ACCEPT = (
    "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.1"
)
_DECLARATION = re.compile(rb"^\s*<\?xml[^>]*?encoding\s*=\s*[\"']([A-Za-z0-9._-]+)[\"']")
_TAG = re.compile(r"<[^>]*>")
_SPACE = re.compile(r"\s+")
# C0 and C1 controls except tab/newline (collapsed to spaces anyway), plus bidi overrides/isolates.
_UNSAFE_CHARS = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069]"
)


class FeedUnreadable(Exception):
    """The feed is refused as a whole, with a reason a person can read."""

    def __init__(self, reason: str, *, code: str = "unreadable") -> None:
        super().__init__(reason)
        self.reason = reason
        #: ``unreadable`` (shape or syntax) or ``tooLarge`` (over the cap).
        self.code = code


@dataclass(frozen=True, slots=True)
class FeedItem:
    """The four facts kept about a headline, plus the id used to avoid storing it twice.

    There is intentionally no ``description``, ``summary`` or ``content`` field.
    """

    guid: str
    title: str
    link: str
    published_at: datetime  # naive UTC, like every timestamp column in Hardwood
    source_name: str


@dataclass(frozen=True, slots=True)
class ParsedFeed:
    kind: str  # "rss" | "atom"
    title: str | None
    items: tuple[FeedItem, ...]
    #: Items left out, by reason: noTitle, badLink, noDate, futureDate, deniedLink, duplicate.
    skipped: Mapping[str, int] = field(default_factory=dict)


# ------------------------------------------------------------------------------ cleaning


def clean_title(raw: str | None) -> str | None:
    """A headline as plain, single-line, bounded text; ``None`` if nothing is left."""
    if raw is None:
        return None
    text = html.unescape(raw)
    text = _TAG.sub(" ", text)
    text = _UNSAFE_CHARS.sub("", text)
    text = unicodedata.normalize("NFC", text)
    text = _SPACE.sub(" ", text).strip()
    if not text:
        return None
    if len(text) > MAX_TITLE_CHARS:
        text = text[: MAX_TITLE_CHARS - 1].rstrip() + "…"
    return text


def clean_link(raw: str | None) -> str | None:
    """An absolute http(s) URL without credentials, or ``None``."""
    if raw is None:
        return None
    text = raw.strip()
    if not text or len(text) > MAX_LINK_CHARS:
        return None
    if check_url(text) is not None:
        return None
    return text


def parse_feed_date(raw: str | None) -> datetime | None:
    """A feed date as naive UTC, or ``None`` when it is missing or unreadable.

    RSS uses RFC 822 (``Tue, 06 Oct 2026 14:30:00 GMT``); Atom uses ISO 8601. Both are tried
    for either, because feeds in the wild are not strict. A timestamp with no zone is taken to
    be UTC, which is the least surprising reading and is stated here rather than guessed at
    in a caller.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    parsed: datetime | None = None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        parsed = None
    if parsed is None:
        try:
            parsed = dateutil_parser.isoparse(text)
        except (ValueError, OverflowError):
            try:
                parsed = dateutil_parser.parse(text)
            except (ValueError, OverflowError, TypeError):
                return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    if parsed.year < _EARLIEST_YEAR:
        return None
    return parsed


def host_matches(url: str, domains: Iterable[str]) -> bool:
    """True when the host of ``url`` is one of ``domains`` or a subdomain of one.

    Used for the source denylist (sites whose links are withheld). It matches on the host, never on
    a substring of the whole URL, so a club called "Partizan Mozzart Bet" in a headline's text, or a
    path that happens to contain a domain's name, never triggers it.
    """
    if isinstance(url, str) and "\\" in url:
        return True  # a browser and urlsplit disagree on the host: never vouch for it
    host = link_host(url)
    if not host:
        return False
    for domain in domains:
        bare = domain.strip().lower().strip(".")
        if bare and (host == bare or host.endswith("." + bare)):
            return True
    return False


# ------------------------------------------------------------------------------ parsing


def _decode(body: bytes) -> str:
    """Decode a document the way an XML parser would pick its encoding, for scanning only."""
    if body.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return body.decode("utf-32", errors="replace")
    if body.startswith((b"\xff\xfe", b"\xfe\xff")):
        return body.decode("utf-16", errors="replace")
    if body.startswith(b"\xef\xbb\xbf"):
        return body[3:].decode("utf-8", errors="replace")
    # No BOM: UTF-16 without one shows up as NUL bytes interleaved with ASCII.
    if body[:4] in (b"<\x00?\x00", b"<\x00!\x00"):
        return body.decode("utf-16-le", errors="replace")
    if body[:4] in (b"\x00<\x00?", b"\x00<\x00!"):
        return body.decode("utf-16-be", errors="replace")
    declared = _DECLARATION.match(body[:200])
    if declared:
        try:
            return body.decode(declared.group(1).decode("ascii"), errors="replace")
        except LookupError:
            pass
    return body.decode("utf-8", errors="replace")


def _reject_dtd(body: bytes) -> None:
    """Refuse any document that declares a document type or an entity, however it is encoded."""
    scans = (_decode(body).lower(), body.replace(b"\x00", b"").lower().decode("latin-1"))
    for text in scans:
        if "<!doctype" in text or "<!entity" in text:
            raise FeedUnreadable(
                "the feed declares a document type or an entity, which Hardwood refuses to parse"
            )


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if _local(child.tag) == name]


def _first_text(element: ET.Element, *names: str) -> str | None:
    for name in names:
        for child in _children(element, name):
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return None


def _atom_link(entry: ET.Element) -> str | None:
    """The entry's alternate link: ``rel="alternate"`` (or no ``rel``), by ``href``."""
    fallback: str | None = None
    for child in _children(entry, "link"):
        href = (child.get("href") or "").strip()
        rel = (child.get("rel") or "alternate").strip().lower()
        if not href:
            continue
        if rel == "alternate":
            return href
        if fallback is None and rel not in ("self", "enclosure", "replies", "edit"):
            fallback = href
    return fallback


def _rss_link(item: ET.Element) -> str | None:
    text = _first_text(item, "link")
    if text:
        return text
    # Some feeds carry only a permalink GUID. Only a guid that says it is a permalink counts.
    for guid in _children(item, "guid"):
        if (guid.get("isPermaLink") or "true").lower() != "false":
            candidate = "".join(guid.itertext()).strip()
            if clean_link(candidate):
                return candidate
    return None


def parse_feed(
    body: bytes,
    *,
    source_name: str | None = None,
    now: datetime | None = None,
    link_allowed: Callable[[str], bool] | None = None,
    max_items: int = MAX_ITEMS,
) -> ParsedFeed:
    """Parse an RSS 2.0, RSS 1.0 or Atom document into headline facts.

    ``source_name`` is the label shown beside each headline; it defaults to the feed's own
    title. ``now`` is naive or aware UTC and bounds the plausible publication dates (defaults to
    the current time). ``link_allowed(url)`` returns False to drop an item (the denylist).
    Raises :class:`FeedUnreadable` for anything it will not guess at.
    """
    if len(body) > MAX_FEED_BYTES:
        raise FeedUnreadable(
            f"the feed is larger than {MAX_FEED_BYTES // 1_000_000} MB", code="tooLarge"
        )
    if not body.strip():
        raise FeedUnreadable("the feed is empty")
    _reject_dtd(body)
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise FeedUnreadable(f"the feed is not well-formed XML ({exc})") from exc
    except (RecursionError, ValueError, MemoryError) as exc:
        raise FeedUnreadable(f"the feed could not be parsed ({type(exc).__name__})") from exc

    reference = now if now is not None else datetime.now(timezone.utc)
    if reference.tzinfo is not None:
        reference = reference.astimezone(timezone.utc).replace(tzinfo=None)
    latest_allowed = reference + FUTURE_TOLERANCE

    root_name = _local(root.tag).lower()
    if root_name == "rss":
        channels = _children(root, "channel")
        if not channels:
            raise FeedUnreadable("the RSS document has no channel")
        container = channels[0]
        kind, entries = "rss", _children(container, "item")
        feed_title = _first_text(container, "title")
    elif root_name == "feed":
        kind, entries = "atom", _children(root, "entry")
        feed_title = _first_text(root, "title")
        container = root
    elif root_name == "rdf":  # RSS 1.0: items are siblings of the channel
        kind, entries = "rss", _children(root, "item")
        channels = _children(root, "channel")
        feed_title = _first_text(channels[0], "title") if channels else None
        container = root
    else:
        raise FeedUnreadable(f"the document is not RSS or Atom (its root element is <{root_name}>)")

    label = clean_title(source_name) or clean_title(feed_title) or "Unknown source"
    label = label[:80]
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    seen: set[str] = set()
    items: list[FeedItem] = []
    for entry in entries:
        title = clean_title(_first_text(entry, "title"))
        if title is None:
            skip("noTitle")
            continue
        raw_link = _atom_link(entry) if kind == "atom" else _rss_link(entry)
        link = clean_link(raw_link)
        if link is None:
            skip("badLink")
            continue
        if link_allowed is not None and not link_allowed(link):
            skip("deniedLink")
            continue
        if kind == "atom":
            raw_date = _first_text(entry, "published", "updated", "issued", "modified")
            raw_guid = _first_text(entry, "id")
        else:
            raw_date = _first_text(entry, "pubDate", "date")  # "date" is dc:date
            raw_guid = _first_text(entry, "guid")
        published = parse_feed_date(raw_date)
        if published is None:
            skip("noDate")
            continue
        if published > latest_allowed:
            skip("futureDate")
            continue
        guid = (raw_guid or link).strip()[:MAX_GUID_CHARS]
        if guid in seen:
            skip("duplicate")
            continue
        seen.add(guid)
        items.append(FeedItem(guid, title, link, published, label))

    items.sort(key=lambda item: item.published_at, reverse=True)
    return ParsedFeed(kind, clean_title(feed_title), tuple(items[:max_items]), skipped)


# --------------------------------------------------------------------------------- fetch


@dataclass(frozen=True, slots=True)
class FeedFetch:
    """The outcome of fetching one feed. ``state`` is what the caller records.

    ``ok`` (items in ``parsed``), ``notModified`` (a 304: nothing to do), ``disallowed``
    (robots.txt says no), ``robotsUnavailable`` (robots.txt could not be read, so nothing was
    fetched), ``unreadable`` (a refused document), ``tooLarge``, ``blocked`` (the host's circuit
    breaker is open; ``paused_until`` says until when) or ``error`` (anything else).
    """

    state: str
    reason: str | None
    http_status: int | None
    fetched_at: datetime
    parsed: ParsedFeed | None = None
    etag: str | None = None
    last_modified: str | None = None
    paused_until: datetime | None = None
    robots_state: str | None = None
    robots_checked_at: datetime | None = None
    robots_reason: str | None = None


def feed_is_due(
    last_fetch_at: datetime | None,
    now: datetime,
    *,
    min_interval: timedelta = MIN_FETCH_INTERVAL,
) -> bool:
    """True when a feed may be fetched again: never fetched, or the interval has passed."""
    if last_fetch_at is None:
        return True

    def naive(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    return naive(now) - naive(last_fetch_at) >= min_interval


def fetch_feed(
    client: PoliteClient,
    robots: RobotsChecker,
    url: str,
    *,
    source_name: str | None = None,
    conditional: Conditional | None = None,
    now: datetime | None = None,
    link_allowed: Callable[[str], bool] | None = None,
) -> FeedFetch:
    """Fetch and parse one feed politely. Never raises for an expected failure.

    ``robots.txt`` is consulted *before* the feed is requested, and again for every redirect hop
    through the client's ``allow`` hook, so a feed that disallows Hardwood is never requested at
    all and the reason travels back in ``robots_reason``.
    """
    fetched_at = now or datetime.now(timezone.utc)
    problem = check_url(url)
    if problem:
        return FeedFetch("error", problem, None, fetched_at)

    verdict = robots.check(url)
    robots_fields = {
        "robots_state": verdict.state,
        "robots_checked_at": verdict.checked_at,
        "robots_reason": verdict.reason,
    }
    if verdict.state == DISALLOWED:
        return FeedFetch("disallowed", verdict.reason, None, fetched_at, **robots_fields)
    if verdict.state == UNAVAILABLE:
        return FeedFetch("robotsUnavailable", verdict.reason, None, fetched_at, **robots_fields)

    try:
        response = client.get(
            url,
            headers={"Accept": _ACCEPT},
            conditional=conditional,
            max_bytes=MAX_FEED_BYTES,
            allow=robots.refusal,
        )
    except CircuitOpenError as exc:
        return FeedFetch(
            "blocked",
            exc.reason,
            exc.status,
            fetched_at,
            paused_until=exc.paused_until,
            **robots_fields,
        )
    except TooLargeError:
        return FeedFetch(
            "tooLarge",
            f"the feed is larger than {MAX_FEED_BYTES // 1_000_000} MB",
            None,
            fetched_at,
            **robots_fields,
        )
    except RefusedError as exc:
        state = "disallowed" if "robots.txt" in exc.reason else "error"
        return FeedFetch(state, exc.reason, None, fetched_at, **robots_fields)
    except (TransportFailure, FetchError) as exc:
        return FeedFetch("error", str(exc), None, fetched_at, **robots_fields)

    if response.not_modified:
        return FeedFetch(
            "notModified",
            None,
            304,
            fetched_at,
            etag=(conditional.etag if conditional else None),
            last_modified=(conditional.last_modified if conditional else None),
            **robots_fields,
        )
    if response.status != 200:
        reason = f"the feed answered {response.status}"
        if response.status in (404, 410):
            reason = f"the feed was not found ({response.status})"
        return FeedFetch("error", reason, response.status, fetched_at, **robots_fields)
    try:
        parsed = parse_feed(
            response.body, source_name=source_name, now=fetched_at, link_allowed=link_allowed
        )
    except FeedUnreadable as exc:
        state = "tooLarge" if exc.code == "tooLarge" else "unreadable"
        return FeedFetch(state, exc.reason, 200, fetched_at, **robots_fields)
    return FeedFetch(
        "ok",
        None,
        200,
        fetched_at,
        parsed=parsed,
        etag=response.etag,
        last_modified=response.last_modified,
        **robots_fields,
    )
