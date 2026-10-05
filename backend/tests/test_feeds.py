"""Headline feeds end to end: the parser, ``robots.txt``, the polite fetch, and what gets stored.

Four layers, each proving a rule the design states:

**The parser** (``nbastats/intel/feeds.py``) keeps a title, a link, a date and the source's name and
*nothing else*: sentinel text planted in ``description``, ``content:encoded``, ``summary`` and
``content`` must never appear anywhere in the result. It refuses any document that declares a type
or an entity (including UTF-16 documents that hide the keyword from a byte search), refuses more
than 2 MB, and refuses to guess: a malformed document, a foreign root element, an item with no
date, a link that is not an absolute http(s) URL.

**``robots.txt``** (``nbastats/intel/robots.py``) is read automatically with
:mod:`urllib.robotparser`, cached for 24 hours, and failures are conservative: a missing file is
"no rules", a forbidden one or an unreadable one is a refusal, and an unreadable one is remembered
only briefly.

**The fetch** never requests a feed whose ``robots.txt`` disallows it, makes conditional requests,
and turns every outcome into a state a caller can record without catching anything.

**The store** (``nbastats/nba_intel/news.py`` and the ``nba.news`` job): feeds seeded only into an
empty table, a disallow switching a feed off and the daily re-check bringing it back (but never a
feed the person switched off), items kept 30 days and pasted links kept for good, and subjects
linked only on a unique name. Everything is invented; no socket is opened.
"""

from __future__ import annotations

import dataclasses
import socket
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

import httpx
import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from nbastats import config
from nbastats import db as db_module
from nbastats.db import init_db
from nbastats.intel import feeds as F
from nbastats.intel.http import Conditional, PoliteClient
from nbastats.intel.robots import (
    ALLOWED,
    DISALLOWED,
    FAILURE_TTL,
    ROBOTS_TTL,
    UNAVAILABLE,
    RobotsChecker,
)
from nbastats.models import Game, Player, PlayerGameBasic, Team
from nbastats.nba_intel import jobs, news, status, store
from nbastats.nba_intel.models import (
    NbaIntelNewsFeed,
    NbaIntelNewsItem,
    NbaIntelNewsSubject,
    NbaIntelSourceState,
)

NOW = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
FEED_URL = "https://feeds.example.org/feed"
ROBOTS_URL = "https://feeds.example.org/robots.txt"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("these tests must never open a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


# ----------------------------------------------------------------------- the documents


def rss(items: str, *, channel_title: str = "Example Wire", extra_ns: str = "") -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" '
        'xmlns:media="http://search.yahoo.com/mrss/" '
        f'xmlns:dc="http://purl.org/dc/elements/1.1/"{extra_ns}>'
        f"<channel><title>{channel_title}</title><link>https://feeds.example.org/</link>"
        f"{items}</channel></rss>"
    ).encode()


def rss_item(
    title: str = "Aces beat Bees in overtime",
    link: str | None = "https://feeds.example.org/a/1",
    pub: str | None = "Mon, 19 Oct 2026 09:30:00 GMT",
    guid: str | None = "guid-1",
    body: str = "SECRET-BODY-TEXT-123",
) -> str:
    parts = [f"<title>{title}</title>"]
    if link is not None:
        parts.append(f"<link>{link}</link>")
    if pub is not None:
        parts.append(f"<pubDate>{pub}</pubDate>")
    if guid is not None:
        parts.append(f'<guid isPermaLink="false">{guid}</guid>')
    parts.append(f"<description>{body}</description>")
    parts.append(f"<content:encoded><![CDATA[<p>{body}</p>]]></content:encoded>")
    parts.append(f'<media:content url="https://feeds.example.org/img/{body}.jpg"/>')
    return "<item>" + "".join(parts) + "</item>"


def atom(entries: str, *, title: str = "Example Atom") -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<feed xmlns="http://www.w3.org/2005/Atom"><title>{title}</title>{entries}</feed>'
    ).encode()


def atom_entry(
    title: str = "Bees sign a guard",
    href: str | None = "https://feeds.example.org/b/2",
    published: str | None = "2026-10-19T08:00:00Z",
    entry_id: str = "tag:example.org,2026:2",
    body: str = "SECRET-BODY-TEXT-123",
) -> str:
    parts = [f"<title>{title}</title>", f"<id>{entry_id}</id>"]
    if href is not None:
        parts.append(f'<link rel="alternate" href="{href}"/>')
        parts.append('<link rel="self" href="https://feeds.example.org/self"/>')
    if published is not None:
        parts.append(f"<published>{published}</published><updated>{published}</updated>")
    parts.append(f"<summary>{body}</summary><content type='html'>{body}</content>")
    return "<entry>" + "".join(parts) + "</entry>"


def parse(body: bytes, **kwargs: Any) -> F.ParsedFeed:
    return F.parse_feed(body, now=NOW, **kwargs)


# ------------------------------------------------------------------- parser: what is kept


def test_an_rss_item_keeps_four_facts_and_never_reads_the_body() -> None:
    parsed = parse(rss(rss_item()), source_name="Example Wire")
    assert parsed.kind == "rss" and len(parsed.items) == 1
    item = parsed.items[0]
    assert item.title == "Aces beat Bees in overtime"
    assert item.link == "https://feeds.example.org/a/1"
    assert item.published_at == datetime(2026, 10, 19, 9, 30)
    assert item.source_name == "Example Wire"
    assert item.guid == "guid-1"
    # The dataclass has no field that could hold a body, and the sentinel is nowhere.
    assert {f.name for f in dataclasses.fields(F.FeedItem)} == {
        "guid",
        "title",
        "link",
        "published_at",
        "source_name",
    }
    assert "SECRET-BODY-TEXT" not in repr(parsed)


def test_an_atom_entry_keeps_four_facts_and_never_reads_summary_or_content() -> None:
    parsed = parse(atom(atom_entry()))
    assert parsed.kind == "atom"
    item = parsed.items[0]
    assert item.link == "https://feeds.example.org/b/2"  # the alternate link, not rel=self
    assert item.published_at == datetime(2026, 10, 19, 8, 0)
    assert item.source_name == "Example Atom"  # defaults to the feed's own title
    assert "SECRET-BODY-TEXT" not in repr(parsed)


def test_an_rss_1_0_document_is_read() -> None:
    body = (
        '<?xml version="1.0"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
        'xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<channel><title>Old Style</title></channel>"
        "<item><title>Cats extend their coach</title><link>https://feeds.example.org/c/3</link>"
        "<dc:date>2026-10-18T12:00:00+02:00</dc:date></item></rdf:RDF>"
    ).encode()
    item = parse(body).items[0]
    assert item.published_at == datetime(2026, 10, 18, 10, 0)
    assert item.source_name == "Old Style"


def test_newest_first_deduplicated_and_capped() -> None:
    older = rss_item(title="Older", guid="g-old", pub="Sun, 18 Oct 2026 09:00:00 GMT")
    newer = rss_item(title="Newer", guid="g-new", link="https://feeds.example.org/n")
    duplicate = rss_item(title="Newer again", guid="g-new", link="https://feeds.example.org/n2")
    parsed = parse(rss(older + newer + duplicate))
    assert [i.title for i in parsed.items] == ["Newer", "Older"]
    assert parsed.skipped == {"duplicate": 1}
    many = "".join(
        rss_item(title=f"Item {n}", guid=f"g{n}", link=f"https://feeds.example.org/{n}")
        for n in range(30)
    )
    assert len(parse(rss(many), max_items=10).items) == 10


def test_an_empty_feed_is_valid_and_has_no_items() -> None:
    assert parse(rss("")).items == ()
    assert parse(atom("")).items == ()


# ------------------------------------------------------------ parser: cleaning what is kept


def test_titles_are_plain_single_line_bounded_text() -> None:
    assert F.clean_title("  Aces\n  beat   <b>Bees</b> &amp; Cats  ") == "Aces beat Bees & Cats"
    bidi_override = chr(0x202E)  # built, not typed: an invisible character in source is a smell
    assert F.clean_title(f"Evil{bidi_override}Flipped \x07 bell") == "EvilFlipped bell"
    assert (
        F.clean_title("&lt;script&gt;alert(1)&lt;/script&gt; Real title") == "alert(1) Real title"
    )
    long = F.clean_title("word " * 200)
    assert long is not None and len(long) == F.MAX_TITLE_CHARS and long.endswith("…")
    assert F.clean_title("   ") is None and F.clean_title(None) is None
    assert F.clean_title("<b></b>") is None


def test_an_item_with_no_title_is_skipped_and_counted() -> None:
    parsed = parse(rss(rss_item(title="<i> </i>")))
    assert parsed.items == () and parsed.skipped == {"noTitle": 1}


@pytest.mark.parametrize(
    "link",
    [
        "/relative/path",
        "javascript:alert(1)",
        "https://user:pw@feeds.example.org/x",
        "ftp://feeds.example.org/x",
        "https://feeds.example.org/has space",
        "",
    ],
)
def test_a_link_that_is_not_an_absolute_http_url_skips_the_item(link: str) -> None:
    parsed = parse(rss(rss_item(link=link)))
    assert parsed.items == () and parsed.skipped.get("badLink") == 1


def test_an_atom_entry_with_only_a_self_link_has_no_usable_link() -> None:
    entry = atom_entry(href=None).replace(
        "</entry>", '<link rel="self" href="https://feeds.example.org/self"/></entry>'
    )
    assert parse(atom(entry)).items == ()


def test_a_permalink_guid_stands_in_for_a_missing_link_but_a_plain_id_does_not() -> None:
    permalink = (
        "<item><title>T</title><guid>https://feeds.example.org/p/9</guid>"
        "<pubDate>Mon, 19 Oct 2026 09:30:00 GMT</pubDate></item>"
    )
    assert parse(rss(permalink)).items[0].link == "https://feeds.example.org/p/9"
    plain = permalink.replace("https://feeds.example.org/p/9", "tag-1234").replace(
        "<guid>", '<guid isPermaLink="false">'
    )
    assert parse(rss(plain)).items == ()


def test_dates_are_normalised_to_utc_and_unusable_dates_skip_the_item() -> None:
    assert F.parse_feed_date("Mon, 19 Oct 2026 11:30:00 +0200") == datetime(2026, 10, 19, 9, 30)
    assert F.parse_feed_date("2026-10-19T09:30:00-05:00") == datetime(2026, 10, 19, 14, 30)
    assert F.parse_feed_date("2026-10-19") == datetime(2026, 10, 19, 0, 0)
    assert F.parse_feed_date("2026-10-19T09:30:00") == datetime(2026, 10, 19, 9, 30)  # read as UTC
    assert F.parse_feed_date("Thu, 01 Jan 1970 00:00:00 GMT") is None
    assert F.parse_feed_date("last tuesday-ish") is None
    assert F.parse_feed_date("") is None and F.parse_feed_date(None) is None

    parsed = parse(rss(rss_item(pub=None)))
    assert parsed.items == () and parsed.skipped == {"noDate": 1}
    future = rss_item(pub="Mon, 19 Oct 2027 09:30:00 GMT")
    parsed = parse(rss(future))
    assert parsed.items == () and parsed.skipped == {"futureDate": 1}


def test_a_date_is_never_invented_from_the_fetch_time() -> None:
    parsed = parse(rss(rss_item(pub="not a date")))
    assert parsed.items == ()


def test_the_denylist_hook_drops_an_item_and_counts_it() -> None:
    body = rss(
        rss_item(link="https://www.mozzartsport.com/news/1", guid="a")
        + rss_item(link="https://feeds.example.org/ok", guid="b")
    )
    parsed = parse(body, link_allowed=lambda link: not status.is_denied(link))
    assert [i.guid for i in parsed.items] == ["b"]
    assert parsed.skipped == {"deniedLink": 1}


def test_host_matching_is_by_host_never_by_substring() -> None:
    domains = ("mozzartbet.com", "mozzartsport.com")
    assert F.host_matches("https://mozzartbet.com/x", domains)
    assert F.host_matches("https://News.MozzartSport.com/x", domains)
    assert not F.host_matches("https://notmozzartbet.com/x", domains)
    assert not F.host_matches("https://mozzartbet.com.example.org/x", domains)
    assert not F.host_matches("https://example.org/mozzartbet.com", domains)
    assert not F.host_matches("Partizan Mozzart Bet beat Real", domains)
    assert not F.host_matches("", domains)


# ----------------------------------------------------- parser: untrusted documents


def test_a_document_type_is_refused() -> None:
    body = b'<?xml version="1.0"?><!DOCTYPE rss SYSTEM "http://x/y.dtd"><rss><channel/></rss>'
    with pytest.raises(F.FeedUnreadable) as raised:
        parse(body)
    assert "document type" in raised.value.reason


@pytest.mark.parametrize(
    "document",
    [
        b'<!DOCTYPE foo [<!ENTITY a "x">]><rss><channel/></rss>',
        b"<!doctype rss><rss><channel/></rss>",
        b'<?xml version="1.0"?><!-- c --><!DocType rss><rss><channel/></rss>',
        b'<rss><channel><title><![CDATA[<!ENTITY x "y">]]></title></channel></rss>',
        b'<!ENTITY a "x"><rss><channel/></rss>',
    ],
)
def test_any_doctype_or_entity_declaration_is_refused(document: bytes) -> None:
    with pytest.raises(F.FeedUnreadable):
        parse(document)


def test_a_billion_laughs_document_is_refused_before_it_is_parsed() -> None:
    laughs = (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
        b'<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">]>'
        b"<rss><channel><title>&lol3;</title></channel></rss>"
    )
    with pytest.raises(F.FeedUnreadable):
        parse(laughs)


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be", "utf-32"])
def test_utf16_and_utf32_documents_cannot_hide_a_doctype(encoding: str) -> None:
    text = '<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY a "x">]><rss><channel/></rss>'
    with pytest.raises(F.FeedUnreadable):
        parse(text.encode(encoding))


def test_the_two_megabyte_cap() -> None:
    base = rss(rss_item())
    wrapper = len(b"<!--  -->")

    def padded(total: int) -> bytes:
        """A valid feed of exactly ``total`` bytes: a comment sized to fill the difference."""
        fill = total - len(base) - wrapper
        document = base.replace(b"</channel>", b"<!-- " + b"x" * fill + b" --></channel>")
        assert len(document) == total
        return document

    assert parse(padded(F.MAX_FEED_BYTES)).items  # exactly at the cap still parses
    with pytest.raises(F.FeedUnreadable) as raised:
        parse(padded(F.MAX_FEED_BYTES + 1))
    assert raised.value.code == "tooLarge"


@pytest.mark.parametrize(
    "document, why",
    [
        (b"", "empty"),
        (b"   \n ", "empty"),
        (b"<rss><channel><title>x</title>", "not well-formed"),
        (b"not xml at all", "not well-formed"),
        (b"<html><body>hello</body></html>", "not RSS or Atom"),
        (b"<rss version='2.0'><item/></rss>", "no channel"),
        (b"<rss><channel><title>&nbsp;</title></channel></rss>", "not well-formed"),
    ],
)
def test_a_document_that_is_not_a_feed_is_unreadable(document: bytes, why: str) -> None:
    with pytest.raises(F.FeedUnreadable) as raised:
        parse(document)
    assert why in raised.value.reason


def test_feed_is_due_is_the_at_most_hourly_rule() -> None:
    now = datetime(2026, 10, 20, 15, 0)
    assert F.feed_is_due(None, now)
    assert not F.feed_is_due(now - timedelta(minutes=59), now)
    assert F.feed_is_due(now - timedelta(hours=1), now)
    aware = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
    assert not F.feed_is_due(now - timedelta(minutes=5), aware)


# ------------------------------------------------------------------------ robots.txt


class Site:
    """A mock site: robots.txt and a feed, each configurable, with a record of requests."""

    def __init__(self) -> None:
        self.robots: tuple[int, bytes] | Exception = (404, b"")
        self.feed: Callable[[httpx.Request], httpx.Response] = lambda request: httpx.Response(
            200, content=rss(rss_item()), headers={"etag": '"e1"'}
        )
        self.requests: list[str] = []
        self.headers: list[httpx.Headers] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        self.headers.append(request.headers)
        if request.url.path == "/robots.txt":
            if isinstance(self.robots, Exception):
                raise self.robots
            return httpx.Response(self.robots[0], content=self.robots[1])
        return self.feed(request)

    @property
    def feed_requests(self) -> list[str]:
        return [u for u in self.requests if not u.endswith("/robots.txt")]

    @property
    def robots_requests(self) -> list[str]:
        return [u for u in self.requests if u.endswith("/robots.txt")]


class Time:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def client_for(site: Site, wall: Callable[[], datetime] | None = None) -> PoliteClient:
    return PoliteClient(
        transport=httpx.MockTransport(site),
        min_interval=0.0,
        sleep=lambda seconds: None,
        wall_clock=wall or (lambda: NOW),
        jitter=lambda: 0.0,
    )


def checker_for(site: Site, wall: Time | None = None) -> RobotsChecker:
    clock = wall or Time()
    return RobotsChecker(client_for(site, clock), wall_clock=clock)


def test_a_disallow_for_hardwood_refuses_the_feed() -> None:
    site = Site()
    site.robots = (200, b"User-agent: Hardwood\nDisallow: /feed\n")
    verdict = checker_for(site).check(FEED_URL)
    assert not verdict.allowed and verdict.state == DISALLOWED
    assert (
        verdict.reason is not None and "robots.txt" in verdict.reason and "/feed" in verdict.reason
    )


def test_the_star_group_applies_when_there_is_no_hardwood_group() -> None:
    site = Site()
    site.robots = (200, b"User-agent: *\nDisallow: /feed\n")
    assert not checker_for(site).check(FEED_URL).allowed
    site.robots = (200, b"User-agent: *\nDisallow: /private\n")
    assert checker_for(site).check(FEED_URL).allowed
    site.robots = (200, b"User-agent: *\nDisallow: /feed\n\nUser-agent: Hardwood\nAllow: /\n")
    assert checker_for(site).check(FEED_URL).allowed


def test_an_empty_robots_file_allows() -> None:
    site = Site()
    site.robots = (200, b"")
    assert checker_for(site).check(FEED_URL).allowed


@pytest.mark.parametrize("status_code", [404, 410, 451])
def test_a_missing_robots_file_means_no_rules(status_code: int) -> None:
    site = Site()
    site.robots = (status_code, b"gone")
    verdict = checker_for(site).check(FEED_URL)
    assert verdict.allowed and verdict.state == ALLOWED


@pytest.mark.parametrize("status_code", [401, 403])
def test_a_forbidden_robots_file_is_treated_as_disallow_all(status_code: int) -> None:
    site = Site()
    site.robots = (status_code, b"")
    verdict = checker_for(site).check(FEED_URL)
    assert not verdict.allowed and verdict.state == DISALLOWED


def test_an_unreadable_robots_file_is_a_refusal_remembered_briefly() -> None:
    site = Site()
    site.robots = (500, b"oops")
    wall = Time()
    checker = checker_for(site, wall)
    verdict = checker.check(FEED_URL)
    assert not verdict.allowed and verdict.state == UNAVAILABLE
    first = len(site.robots_requests)
    checker.check(FEED_URL)
    assert len(site.robots_requests) == first  # remembered
    wall.now += FAILURE_TTL + timedelta(seconds=1)
    site.robots = (200, b"")
    assert checker.check(FEED_URL).allowed  # and re-read soon, not after a day


def test_a_network_failure_or_an_oversize_file_is_unavailable() -> None:
    site = Site()
    site.robots = httpx.ConnectError("down")
    assert checker_for(site).check(FEED_URL).state == UNAVAILABLE
    site.robots = (200, b"#" * (600 * 1024))
    assert checker_for(site).check(FEED_URL).state == UNAVAILABLE


def test_an_answer_the_site_gave_is_cached_for_24_hours() -> None:
    site = Site()
    site.robots = (200, b"User-agent: *\nDisallow: /nope\n")
    wall = Time()
    checker = checker_for(site, wall)
    checker.check(FEED_URL)
    wall.now += ROBOTS_TTL - timedelta(minutes=1)
    checker.check(FEED_URL)
    assert len(site.robots_requests) == 1
    wall.now += timedelta(minutes=2)
    checker.check(FEED_URL)
    assert len(site.robots_requests) == 2


def test_the_cache_is_per_origin_and_can_be_forgotten() -> None:
    site = Site()
    checker = checker_for(site)
    checker.check("https://feeds.example.org/a")
    checker.check("https://feeds.example.org/b")
    checker.check("https://other.example.org/a")
    assert len(site.robots_requests) == 2
    checker.forget("https://feeds.example.org/a")
    checker.check("https://feeds.example.org/a")
    assert len(site.robots_requests) == 3


def test_refusal_is_shaped_for_the_clients_allow_hook() -> None:
    site = Site()
    site.robots = (200, b"User-agent: *\nDisallow: /feed\n")
    checker = checker_for(site)
    assert checker.refusal("https://feeds.example.org/elsewhere") is None
    reason = checker.refusal(FEED_URL)
    assert reason is not None and "robots.txt" in reason


# ---------------------------------------------------------------------- fetch_feed


def fetch(site: Site, **kwargs: Any) -> F.FeedFetch:
    client = client_for(site)
    robots = RobotsChecker(client, wall_clock=lambda: NOW)
    return F.fetch_feed(client, robots, FEED_URL, source_name="Example Wire", now=NOW, **kwargs)


def test_a_feed_that_disallows_hardwood_is_never_requested() -> None:
    site = Site()
    site.robots = (200, b"User-agent: Hardwood\nDisallow: /\n")
    result = fetch(site)
    assert result.state == "disallowed" and result.robots_state == DISALLOWED
    assert site.feed_requests == []
    assert result.reason is not None and "robots.txt" in result.reason


def test_an_unreadable_robots_file_means_nothing_is_fetched() -> None:
    site = Site()
    site.robots = (503, b"")
    result = fetch(site)
    assert result.state == "robotsUnavailable" and site.feed_requests == []


def test_a_good_feed_is_fetched_parsed_and_its_validators_returned() -> None:
    site = Site()
    result = fetch(site)
    assert result.state == "ok" and result.http_status == 200
    assert result.parsed is not None and len(result.parsed.items) == 1
    assert result.etag == '"e1"' and result.robots_state == ALLOWED
    assert site.headers[-1]["accept"].startswith("application/rss+xml")


def test_a_conditional_request_and_a_304() -> None:
    site = Site()
    site.feed = lambda request: (
        httpx.Response(304)
        if request.headers.get("if-none-match") == '"e1"'
        else httpx.Response(200, content=rss(rss_item()))
    )
    result = fetch(site, conditional=Conditional('"e1"', "Sat, 17 Oct 2026 10:00:00 GMT"))
    assert result.state == "notModified" and result.http_status == 304
    assert result.etag == '"e1"'
    assert site.headers[-1]["if-modified-since"] == "Sat, 17 Oct 2026 10:00:00 GMT"


def test_a_redirect_into_a_disallowed_path_is_refused_on_that_hop() -> None:
    site = Site()
    site.robots = (200, b"User-agent: *\nDisallow: /private\n")
    site.feed = lambda request: (
        httpx.Response(302, headers={"location": "/private/feed"})
        if request.url.path == "/feed"
        else httpx.Response(200, content=rss(rss_item()))
    )
    result = fetch(site)
    assert result.state == "disallowed"
    assert site.feed_requests == [FEED_URL]  # the private path was never requested


def test_the_fetch_states_for_every_failure() -> None:
    site = Site()
    site.feed = lambda request: httpx.Response(404)
    result = fetch(site)
    assert (
        result.state == "error"
        and result.http_status == 404
        and "not found" in (result.reason or "")
    )

    site.feed = lambda request: httpx.Response(200, content=b"<!DOCTYPE rss><rss><channel/></rss>")
    assert fetch(site).state == "unreadable"

    site.feed = lambda request: httpx.Response(200, content=b"x" * (F.MAX_FEED_BYTES + 1))
    assert fetch(site).state == "tooLarge"

    site.feed = lambda request: httpx.Response(403)
    blocked = fetch(site)
    assert blocked.state == "blocked" and blocked.paused_until is not None

    site.feed = lambda request: httpx.Response(200, content=b"<html/>")
    assert fetch(site).state == "unreadable"

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    site.feed = boom
    assert fetch(site).state == "error"


def test_a_bad_feed_url_is_an_error_not_a_request() -> None:
    site = Site()
    client = client_for(site)
    result = F.fetch_feed(client, RobotsChecker(client), "file:///etc/passwd", now=NOW)
    assert result.state == "error" and site.requests == []


# ----------------------------------------------------------------------- the store


@pytest.fixture()
def intel_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """A fresh store, wired in as the process-wide database, with an invented league."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'intel.db'}")
    monkeypatch.setenv("HARDWOOD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("HARDWOOD_NEWS", raising=False)
    config.reset_settings_cache()
    db_module.dispose_engine()
    engine = db_module.get_engine()
    init_db(engine)
    with Session(engine) as session:
        add_league(session)
        session.commit()
    try:
        yield engine
    finally:
        db_module.dispose_engine()
        config.reset_settings_cache()


def add_league(session: Session) -> None:
    """Four invented teams, two sharing a nickname, and a few invented players."""
    session.add_all(
        [
            Team(
                team_id=9001, abbr="AAA", name="Alpha City Aces", city="Alpha City", nickname="Aces"
            ),
            Team(
                team_id=9002, abbr="BBB", name="Bravo Town Bees", city="Bravo Town", nickname="Bees"
            ),
            Team(
                team_id=9003,
                abbr="EEE",
                name="Echo Falls Sparks",
                city="Echo Falls",
                nickname="Sparks",
            ),
            Team(
                team_id=9004,
                abbr="FFF",
                name="Foxtrot Rock Sparks",
                city="Foxtrot Rock",
                nickname="Sparks",
            ),
        ]
    )
    session.add_all(
        [
            Player(player_id=7001, full_name="Alex Sample", is_active=True),
            Player(player_id=7002, full_name="Casey Mock", is_active=True),
            Player(player_id=7003, full_name="Casey Mock", is_active=True),
            Player(player_id=7004, full_name="Dana Fake", is_active=True),
            Player(player_id=7005, full_name="Retired Randy", is_active=False),
        ]
    )
    session.add(
        Game(
            game_id="G-9001",
            game_date=date(2026, 10, 15),
            season="2026-27",
            season_type="Regular Season",
            home_team_id=9001,
            away_team_id=9002,
            status="final",
            data_source="nba_api",
        )
    )
    session.flush()
    for player_id, team_id in (
        (7001, 9001),
        (7002, 9001),
        (7003, 9002),
        (7004, 9002),
        (7005, 9001),
    ):
        session.add(PlayerGameBasic(game_id="G-9001", player_id=player_id, team_id=team_id))


def site_with(items: str, *, robots: tuple[int, bytes] = (404, b"")) -> Site:
    site = Site()
    site.robots = robots
    site.feed = lambda request: httpx.Response(200, content=rss(items), headers={"etag": '"v1"'})
    return site


def refresh(engine: Engine, site: Site, feed_id: int = 1, now: datetime = NOW) -> news.FeedRefresh:
    client = client_for(site)
    robots = RobotsChecker(client, wall_clock=lambda: now)
    with Session(engine) as session:
        feed = session.get(NbaIntelNewsFeed, feed_id)
        assert feed is not None
        outcome = news.refresh_feed(session, client, robots, feed, now=now)
        session.commit()
    return outcome


def test_default_feeds_are_seeded_only_into_an_empty_table(intel_db: Engine) -> None:
    with Session(intel_db) as session:
        assert news.ensure_default_feeds(session) == 1
        urls = {f.url for f in session.execute(select(NbaIntelNewsFeed)).scalars()}
        # the EuroLeague-only candidate belongs to the EuroLeague's own feed table
        assert urls == {"https://talkbasket.net/feed"}
        assert all(f.enabled for f in session.execute(select(NbaIntelNewsFeed)).scalars())
        assert news.ensure_default_feeds(session) == 0
        # A feed the person switched off is not seeded back on.
        feed = session.execute(select(NbaIntelNewsFeed)).scalars().first()
        assert feed is not None
        feed.enabled = False
        session.commit()
        assert news.ensure_default_feeds(session) == 0
        reread = session.get(NbaIntelNewsFeed, feed.feed_id)
        assert reread is not None and reread.enabled is False


def seed_one_feed(engine: Engine, *, enabled: bool = True) -> None:
    with Session(engine) as session:
        news.add_feed(session, "Example Wire", FEED_URL, enabled=enabled)
        session.commit()


def test_a_refresh_stores_items_and_links_only_unique_names(intel_db: Engine) -> None:
    seed_one_feed(intel_db)
    items = (
        rss_item(title="Alpha City Aces sign Alex Sample to a deal", guid="1")
        + rss_item(
            title="Casey Mock returns to practice", guid="2", link="https://feeds.example.org/2"
        )
        + rss_item(title="Sparks win again", guid="3", link="https://feeds.example.org/3")
        + rss_item(
            title="Echo Falls Sparks win again", guid="4", link="https://feeds.example.org/4"
        )
        + rss_item(
            title="Alpha City is lovely in autumn", guid="5", link="https://feeds.example.org/5"
        )
        + rss_item(title="Bees and Aces meet", guid="6", link="https://feeds.example.org/6")
        + rss_item(
            title="Retired Randy waves goodbye", guid="7", link="https://feeds.example.org/7"
        )
    )
    outcome = refresh(intel_db, site_with(items))
    # four headlines name no team or player uniquely: they are not NBA news, so not stored
    assert outcome.state == "ok" and outcome.new_items == 3 and outcome.skipped == 4
    with Session(intel_db) as session:
        by_title = {
            row.title: sorted(
                (s.team_id, s.player_id)
                for s in session.execute(
                    select(NbaIntelNewsSubject).where(NbaIntelNewsSubject.item_id == row.item_id)
                ).scalars()
            )
            for row in session.execute(select(NbaIntelNewsItem)).scalars()
        }
    # a team named and a player named: both rows, the player under his own team
    assert by_title["Alpha City Aces sign Alex Sample to a deal"] == [(9001, 0), (9001, 7001)]
    # two players share the name: no player link at all, and no team named either
    assert "Casey Mock returns to practice" not in by_title
    # "Sparks" belongs to two teams: no link; the full name belongs to one
    assert "Sparks win again" not in by_title
    assert by_title["Echo Falls Sparks win again"] == [(9003, 0)]
    # a city alone is never enough
    assert "Alpha City is lovely in autumn" not in by_title
    assert by_title["Bees and Aces meet"] == [(9001, 0), (9002, 0)]
    # an inactive player is not in the index
    assert "Retired Randy waves goodbye" not in by_title


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # a nickname alone is an ordinary word: lower case, or capitalised with nothing else NBA
        ("Real Madrid turn up the aces on Olympiacos", set()),
        ("Monaco bees of Europe? Fenerbahce wait", set()),
        ("Aces of Europe: Belgrade crowd lifts the Serbs", set()),
        # capitalised and beside another team, a player, or the word NBA: a team
        ("Aces beat Bees in overtime", {9001, 9002}),
        ("Aces rally late as Alex Sample scores 40", {9001}),
        ("NBA: Aces rally late", {9001}),
        # the full name always names the team
        ("Alpha City Aces rally late", {9001}),
        # a nickname two teams share never names either, nor counts as basketball company
        ("Sparks beat Bees", set()),
    ],
)
def test_a_nickname_names_a_team_only_capitalised_and_in_basketball_company(
    intel_db: Engine, title: str, expected: set[int]
) -> None:
    with Session(intel_db) as session:
        index = news.SubjectIndex(session)
    assert set(index.match(title).team_ids) == expected


def test_the_same_items_are_not_stored_twice_and_validators_are_kept(intel_db: Engine) -> None:
    seed_one_feed(intel_db)
    site = site_with(rss_item(guid="only"))
    assert refresh(intel_db, site).new_items == 1
    assert refresh(intel_db, site).new_items == 0
    with Session(intel_db) as session:
        assert session.execute(select(func.count()).select_from(NbaIntelNewsItem)).scalar() == 1
        feed = session.get(NbaIntelNewsFeed, 1)
        assert feed is not None
        assert feed.etag == '"v1"' and feed.last_status == "ok"
        assert feed.robots_state == "allowed" and feed.robots_checked_on == NOW.date()
        state = session.get(NbaIntelSourceState, "nba.news.1")
        assert state is not None and state.state == "ok" and state.last_success_at is not None


def test_a_disallow_switches_the_feed_off_with_the_reason(intel_db: Engine) -> None:
    seed_one_feed(intel_db)
    site = site_with(rss_item(), robots=(200, b"User-agent: *\nDisallow: /\n"))
    outcome = refresh(intel_db, site)
    assert outcome.state == "disallowed"
    assert site.feed_requests == []
    with Session(intel_db) as session:
        feed = session.get(NbaIntelNewsFeed, 1)
        assert feed is not None
        assert feed.enabled is False and feed.robots_state == "disallowed"
        assert feed.robots_reason is not None and "robots.txt" in feed.robots_reason
        state = session.get(NbaIntelSourceState, "nba.news.1")
        assert state is not None and state.state == "disabled"
        assert "robots.txt" in (store.source_detail(state).get("reason") or "")
        # and the feed stays eligible, so a daily re-check can bring it back
        assert [
            f.feed_id
            for f in news.due_feeds(session, NOW.replace(tzinfo=None) + timedelta(hours=2))
        ] == [1]


def test_a_feed_a_disallow_turned_off_comes_back_when_robots_allows_it(intel_db: Engine) -> None:
    seed_one_feed(intel_db)
    refresh(intel_db, site_with(rss_item(), robots=(200, b"User-agent: *\nDisallow: /\n")))
    later = NOW + timedelta(days=2)
    outcome = refresh(intel_db, site_with(rss_item(guid="back")), now=later)
    assert outcome.state == "ok" and outcome.new_items == 1
    with Session(intel_db) as session:
        feed = session.get(NbaIntelNewsFeed, 1)
        assert feed is not None and feed.enabled is True and feed.robots_state == "allowed"


def test_a_feed_the_person_switched_off_is_never_refreshed_or_revived(intel_db: Engine) -> None:
    seed_one_feed(intel_db, enabled=False)
    with Session(intel_db) as session:
        assert news.due_feeds(session, NOW.replace(tzinfo=None), force=True) == []


def test_an_unreadable_feed_is_recorded_as_unreadable_and_stores_nothing(intel_db: Engine) -> None:
    seed_one_feed(intel_db)
    site = Site()
    site.feed = lambda request: httpx.Response(200, content=b"<!DOCTYPE rss><rss><channel/></rss>")
    outcome = refresh(intel_db, site)
    assert outcome.state == "unreadable"
    with Session(intel_db) as session:
        assert session.execute(select(func.count()).select_from(NbaIntelNewsItem)).scalar() == 0
        state = session.get(NbaIntelSourceState, "nba.news.1")
        assert state is not None and state.state == "unreadable" and state.consecutive_failures == 1


def test_a_blocked_host_records_its_pause(intel_db: Engine) -> None:
    seed_one_feed(intel_db)
    site = Site()
    site.feed = lambda request: httpx.Response(403)
    outcome = refresh(intel_db, site)
    assert outcome.state == "blocked"
    with Session(intel_db) as session:
        state = session.get(NbaIntelSourceState, "nba.news.1")
        assert state is not None and state.state == "blocked"
        assert state.paused_until == (NOW + timedelta(hours=6)).replace(tzinfo=None)


def test_a_304_updates_the_fetch_time_and_stores_nothing(intel_db: Engine) -> None:
    seed_one_feed(intel_db)
    refresh(intel_db, site_with(rss_item()))
    site = Site()
    site.feed = lambda request: httpx.Response(304)
    later = NOW + timedelta(hours=2)
    outcome = refresh(intel_db, site, now=later)
    assert outcome.state == "notModified" and outcome.new_items == 0
    with Session(intel_db) as session:
        feed = session.get(NbaIntelNewsFeed, 1)
        assert feed is not None and feed.last_fetch_at == later.replace(tzinfo=None)
        assert feed.etag == '"v1"'


def test_a_denylisted_link_is_never_stored(intel_db: Engine) -> None:
    seed_one_feed(intel_db)
    items = rss_item(link="https://www.mozzartsport.com/n/1", guid="a") + rss_item(
        link="https://feeds.example.org/ok", guid="b"
    )
    refresh(intel_db, site_with(items))
    with Session(intel_db) as session:
        links = [i.link for i in session.execute(select(NbaIntelNewsItem)).scalars()]
    assert links == ["https://feeds.example.org/ok"]


def test_items_older_than_thirty_days_are_not_stored_and_are_pruned(intel_db: Engine) -> None:
    seed_one_feed(intel_db)
    old = rss_item(
        title="Ancient Aces beat Bees",
        guid="old",
        pub="Sat, 10 Sep 2026 09:00:00 GMT",
        link="https://feeds.example.org/old",
    )
    fresh = rss_item(title="Fresh Aces beat Bees", guid="new", link="https://feeds.example.org/new")
    outcome = refresh(intel_db, site_with(old + fresh))
    assert outcome.new_items == 1
    with Session(intel_db) as session:
        # an item that was fresh when stored and has since aged out
        aging = NbaIntelNewsItem(
            feed_id=1,
            guid="aging",
            title="Aging",
            link="https://feeds.example.org/aging",
            published_at=datetime(2026, 9, 1),
            fetched_at=datetime(2026, 9, 2),
            source_name="X",
        )
        session.add(aging)
        session.flush()
        session.add(NbaIntelNewsSubject(item_id=aging.item_id, team_id=9001, player_id=0))
        pasted = news.add_pasted_link(
            session,
            title="Pasted long ago",
            link="https://feeds.example.org/pasted",
            published_at=datetime(2026, 8, 1),
            source_name="Me",
            now=NOW,
        )
        session.commit()
        removed = news.prune_items(session, NOW)
        session.commit()
        titles = {i.title for i in session.execute(select(NbaIntelNewsItem)).scalars()}
    assert removed == 1
    assert titles == {"Fresh Aces beat Bees", "Pasted long ago"}
    assert pasted.feed_id is None


def test_pasted_links_are_validated_deduplicated_and_matched(intel_db: Engine) -> None:
    with Session(intel_db) as session:
        row = news.add_pasted_link(
            session,
            title="Alpha City Aces sign Alex Sample",
            link="https://feeds.example.org/p/1",
            published_at=datetime(2026, 10, 19, 9, 0),
            source_name="Example Wire",
            now=NOW,
        )
        again = news.add_pasted_link(
            session,
            title="Different title",
            link="https://feeds.example.org/p/1",
            published_at=datetime(2026, 10, 19, 9, 0),
            source_name="Example Wire",
            now=NOW,
        )
        assert again.item_id == row.item_id
        listed = news.list_news(session, player_id=7001)
        assert [n.item_id for n in listed] == [row.item_id]
        assert listed[0].team_ids == (9001,) and listed[0].player_ids == (7001,)

        explicit = news.add_pasted_link(
            session,
            title="Nothing recognisable",
            link="https://feeds.example.org/p/2",
            published_at=datetime(2026, 10, 19, 9, 0),
            source_name="Example Wire",
            team_ids=[9002],
            player_ids=[7004],
            now=NOW,
        )
        assert [n.item_id for n in news.list_news(session, team_id=9002)] == [explicit.item_id]

        for bad in (
            dict(link="javascript:alert(1)"),
            dict(link="https://www.mozzartbet.com/x"),
            dict(title="   "),
            dict(source_name=""),
            dict(published_at=datetime(2030, 1, 1)),
        ):
            kwargs: dict[str, Any] = dict(
                title="T",
                link="https://feeds.example.org/p/9",
                published_at=datetime(2026, 10, 1),
                source_name="S",
                now=NOW,
            )
            kwargs.update(bad)
            with pytest.raises(ValueError):
                news.add_pasted_link(session, **kwargs)


def test_list_news_is_newest_first_and_the_limit_is_clamped(intel_db: Engine) -> None:
    with Session(intel_db) as session:
        for n in range(5):
            news.add_pasted_link(
                session,
                title=f"Story {n}",
                link=f"https://feeds.example.org/s/{n}",
                published_at=datetime(2026, 10, 10 + n),
                source_name="S",
                now=NOW,
            )
        titles = [row.title for row in news.list_news(session, limit=3)]
        assert titles == ["Story 4", "Story 3", "Story 2"]
        assert len(news.list_news(session, limit=0)) == 1
        assert len(news.list_news(session, limit=10_000)) == 5


def test_add_feed_validates(intel_db: Engine) -> None:
    with Session(intel_db) as session:
        news.add_feed(session, "One", "https://feeds.example.org/one")
        for name, url in (
            ("Dup", "https://feeds.example.org/one"),
            ("Bad", "javascript:1"),
            ("Betting", "https://www.mozzartsport.com/feed"),
            ("   ", "https://feeds.example.org/two"),
        ):
            with pytest.raises(ValueError):
                news.add_feed(session, name, url)


# --------------------------------------------------------------------------- the job


@pytest.fixture()
def patched_client(monkeypatch: pytest.MonkeyPatch) -> Callable[[Site], None]:
    def install(site: Site) -> None:
        def factory(**kwargs: Any) -> PoliteClient:
            return PoliteClient(
                transport=httpx.MockTransport(site),
                min_interval=0.0,
                sleep=lambda seconds: None,
                jitter=lambda: 0.0,
                **kwargs,
            )

        monkeypatch.setattr(jobs, "PoliteClient", factory)

    return install


def test_the_job_seeds_fetches_and_reports(intel_db: Engine, patched_client: Any) -> None:
    site = site_with(rss_item(title="Alpha City Aces win", guid="j1"))
    patched_client(site)
    result = jobs.run_news(now=NOW)
    assert result["status"] == "ok" and result["newItems"] == 1  # one seeded feed, one item
    assert "1 feeds checked" in result["detail"]
    with Session(intel_db) as session:
        assert session.execute(select(func.count()).select_from(NbaIntelNewsFeed)).scalar() == 1
        assert session.execute(select(func.count()).select_from(NbaIntelNewsItem)).scalar() == 1


def test_the_job_fetches_each_feed_at_most_hourly(intel_db: Engine, patched_client: Any) -> None:
    patched_client(site_with(rss_item(guid="j2")))
    assert jobs.run_news(now=NOW)["status"] == "ok"
    skipped = jobs.run_news(now=NOW + timedelta(minutes=30))
    assert skipped["status"] == "skipped" and "no feed is due" in skipped["detail"]
    assert jobs.run_news(now=NOW + timedelta(hours=1, minutes=1))["status"] == "ok"
    assert jobs.run_news(now=NOW + timedelta(minutes=5), force=True)["status"] == "ok"


def test_the_job_honours_the_switch(
    intel_db: Engine, patched_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = site_with(rss_item())
    patched_client(site)
    for value in ("off", "0", "false", "no", "banana"):
        monkeypatch.setenv("HARDWOOD_NEWS", value)
        assert jobs.run_news(now=NOW)["status"] == "skipped"
    assert site.requests == []
    monkeypatch.setenv("HARDWOOD_NEWS", "on")
    assert jobs.run_news(now=NOW)["status"] == "ok"


def test_the_job_reports_a_disabled_feed_and_survives_one_that_fails(
    intel_db: Engine, patched_client: Any
) -> None:
    seed_one_feed(intel_db)
    site = Site()
    site.robots = (200, b"User-agent: *\nDisallow: /\n")
    patched_client(site)
    result = jobs.run_news(now=NOW)
    assert result["status"] == "ok" and "switched off by robots.txt" in result["detail"]

    failing = Site()
    failing.feed = lambda request: httpx.Response(500)
    patched_client(failing)
    with Session(intel_db) as session:
        session.execute(NbaIntelNewsFeed.__table__.delete())
        news.add_feed(session, "Failing", FEED_URL)
        session.commit()
    result = jobs.run_news(now=NOW + timedelta(days=1))
    assert result["status"] == "error" and "problems" in result["detail"]


def test_a_persisted_pause_is_restored_so_a_blocked_host_is_not_asked_again(
    intel_db: Engine, patched_client: Any
) -> None:
    seed_one_feed(intel_db)
    site = Site()
    site.feed = lambda request: httpx.Response(403)
    patched_client(site)
    jobs.run_news(now=NOW)
    first = len(site.feed_requests)
    assert first == 1
    jobs.run_news(now=NOW + timedelta(hours=2))  # an hour later the feed is due, the host is paused
    assert len(site.feed_requests) == first


def test_the_switch_parser_matches_the_workers_reading() -> None:
    assert jobs.switch_on("X", env={}) is True
    assert jobs.switch_on("X", env={"X": ""}) is True
    assert jobs.switch_on("X", env={"X": " ON "}) is True
    assert jobs.switch_on("X", env={"X": "off"}) is False
    assert jobs.switch_on("X", env={"X": "maybe"}) is False
