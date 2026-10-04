"""``robots.txt``, checked automatically, cached for a day, and conservative when it cannot be read.

What replaced the review step
-----------------------------
The first design asked the person who owns the Mac to read each feed's terms and robots file and
record a date before a headline could be fetched. The lead removed that gate: Hardwood is private
and personal, and a form to fill in is not the same thing as good behaviour. What *is* good
behaviour, and costs nothing, is to ask the site what it wants. So before a feed is fetched this
module reads that host's ``robots.txt`` with the standard library's
:class:`urllib.robotparser.RobotFileParser`, caches the answer for 24 hours, and refuses the
fetch if the file disallows it for the ``Hardwood`` agent. The feed job turns a refusal into a
disabled feed with the reason attached, which ``/v1/sources`` shows.

How each way of failing is treated
----------------------------------
A robots file that is missing is not a refusal, and a robots file that cannot be *read* is not
permission. The rules follow RFC 9309 where it is clear and are cautious where it is not:

======================================  ====================================================
what came back                           verdict
======================================  ====================================================
200 and a parseable body                 the file's rules for ``Hardwood`` (else ``*``)
404, 410 and other 4xx except 401/403    allowed: there is no file, so no rules
401, 403                                 disallowed (what Python's own parser does as well)
5xx or 429 after the client's retries    unavailable: refused, remembered for 15 minutes
a network failure, a timeout, or a       unavailable: refused, remembered for 15 minutes
circuit that is open
a body over 512 KiB                      unavailable: a file that size is not a robots file
======================================  ====================================================

An unavailable verdict is cached for only 15 minutes, not 24 hours, so a transient outage does not
switch a feed off for a day, and it is cached at all so a down host is not hit once per feed per
tick. The 24 hour cache applies to answers the site actually gave.

``robots.txt`` is requested through the same :class:`~nbastats.intel.http.PoliteClient` as
everything else, so it inherits the User-Agent, the one-second floor and the breaker. The check
is keyed by origin (scheme, host and port), because robots files are per origin.

The verdict is also wired in as the client's ``allow`` hook (:meth:`RobotsChecker.refusal`), so a
redirect from an allowed feed URL to a different path or host is checked again on every hop.

What this module does not do
----------------------------
It does not obey ``Crawl-delay`` (Hardwood fetches a feed at most hourly and floors every
request at a second, which is slower than any crawl delay a news site publishes), it does not
read ``Sitemap`` lines, and it is not applied to the NBA's injury-report PDFs: those are
documents the league publishes at a documented path for readers, not a feed Hardwood discovered.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib import robotparser
from urllib.parse import urlsplit

from .http import (
    CircuitOpenError,
    FetchError,
    PoliteClient,
    RefusedError,
    StopRequested,
    TooLargeError,
    TransportFailure,
)

__all__ = [
    "AGENT_TOKEN",
    "ROBOTS_TTL",
    "FAILURE_TTL",
    "MAX_ROBOTS_BYTES",
    "ALLOWED",
    "DISALLOWED",
    "UNAVAILABLE",
    "RobotsVerdict",
    "RobotsChecker",
    "robots_url_for",
]

logger = logging.getLogger(__name__)

#: The product token matched against ``User-agent:`` lines.
AGENT_TOKEN = "Hardwood"
#: An answer the site gave is trusted this long.
ROBOTS_TTL = timedelta(hours=24)
#: A failure to get an answer is remembered this long.
FAILURE_TTL = timedelta(minutes=15)
#: RFC 9309 asks a crawler to parse at least 500 KiB; a larger file is refused here.
MAX_ROBOTS_BYTES = 512 * 1024

ALLOWED = "allowed"
DISALLOWED = "disallowed"
UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class RobotsVerdict:
    """The answer for one URL. ``allowed`` is what the fetcher acts on; ``state`` and ``reason``
    are what a person is shown. ``checked_at`` is when the robots file was last read, so a
    cached answer reports its true age."""

    allowed: bool
    state: str
    reason: str | None
    robots_url: str
    checked_at: datetime


@dataclass(slots=True)
class _Entry:
    parser: robotparser.RobotFileParser | None
    state: str
    reason: str | None
    checked_at: datetime
    expires_at: datetime


def robots_url_for(url: str) -> str:
    """The ``robots.txt`` URL for the origin of ``url``."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/robots.txt"


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RobotsChecker:
    """Read, cache and apply ``robots.txt`` for the ``Hardwood`` agent.

    One instance per job run is typical; the cache lives on the instance, so a long-running
    caller (the worker keeps one for the life of the process) gets the 24 hour behaviour.
    Thread-safe: concurrent checks of the same origin may both fetch once, which is harmless.
    """

    def __init__(
        self,
        client: PoliteClient,
        *,
        agent: str = AGENT_TOKEN,
        ttl: timedelta = ROBOTS_TTL,
        failure_ttl: timedelta = FAILURE_TTL,
        wall_clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._client = client
        self._agent = agent
        self._ttl = ttl
        self._failure_ttl = failure_ttl
        self._wall_clock = wall_clock
        self._cache: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    # -- public ---------------------------------------------------------------------------

    def check(self, url: str) -> RobotsVerdict:
        """May Hardwood fetch ``url``? Reads ``robots.txt`` first if the cache has no answer."""
        entry = self._entry_for(url)
        robots_url = robots_url_for(url)
        if entry.state == UNAVAILABLE:
            return RobotsVerdict(False, UNAVAILABLE, entry.reason, robots_url, entry.checked_at)
        if entry.state == DISALLOWED and entry.parser is None:
            return RobotsVerdict(False, DISALLOWED, entry.reason, robots_url, entry.checked_at)
        if entry.parser is None:  # no file: nothing to obey
            return RobotsVerdict(True, ALLOWED, entry.reason, robots_url, entry.checked_at)
        path = urlsplit(url)
        target = path.path or "/"
        if path.query:
            target += "?" + path.query
        if entry.parser.can_fetch(self._agent, target):
            return RobotsVerdict(True, ALLOWED, None, robots_url, entry.checked_at)
        return RobotsVerdict(
            False,
            DISALLOWED,
            f"{robots_url} disallows fetching {path.path or '/'} for {self._agent}",
            robots_url,
            entry.checked_at,
        )

    def refusal(self, url: str) -> str | None:
        """The reason ``url`` may not be fetched, or ``None`` if it may. Shaped to be passed to
        :meth:`PoliteClient.get` as ``allow=``."""
        verdict = self.check(url)
        if verdict.allowed:
            return None
        return verdict.reason or f"robots.txt does not allow fetching {url}"

    def forget(self, url: str | None = None) -> None:
        """Drop the cached answer for ``url``'s origin, or every answer (for tests)."""
        with self._lock:
            if url is None:
                self._cache.clear()
            else:
                self._cache.pop(_origin(url), None)

    # -- internals ------------------------------------------------------------------------

    def _entry_for(self, url: str) -> _Entry:
        origin = _origin(url)
        now = self._wall_clock()
        with self._lock:
            cached = self._cache.get(origin)
            if cached is not None and now < cached.expires_at:
                return cached
        entry = self._fetch(url, now)
        with self._lock:
            self._cache[origin] = entry
        return entry

    def _fetch(self, url: str, now: datetime) -> _Entry:
        robots_url = robots_url_for(url)

        def unavailable(reason: str) -> _Entry:
            logger.info("robots unavailable url=%s reason=%s", robots_url, reason)
            return _Entry(None, UNAVAILABLE, reason, now, now + self._failure_ttl)

        try:
            # No status opens the client's breaker here: a 401 or 403 on robots.txt is a statement
            # about robots.txt (handled below as "disallow everything"), not a block on the host.
            response = self._client.get(
                robots_url, max_bytes=MAX_ROBOTS_BYTES, blocked_statuses=frozenset()
            )
        except StopRequested:
            raise  # shutting down is not a verdict about the site
        except CircuitOpenError as exc:
            return unavailable(f"requests to this host are paused: {exc.reason}")
        except TooLargeError:
            return unavailable(f"{robots_url} is larger than {MAX_ROBOTS_BYTES // 1024} KiB")
        except (TransportFailure, RefusedError, FetchError) as exc:
            return unavailable(f"{robots_url} could not be fetched: {exc}")

        expires = now + self._ttl
        status = response.status
        if status in (401, 403):
            # Python's own parser treats these as "disallow everything"; so do we.
            return _Entry(
                None,
                DISALLOWED,
                f"{robots_url} answered {status}, which is treated as disallowing everything",
                now,
                expires,
            )
        if status == 429 or status >= 500:
            return unavailable(f"{robots_url} answered {status}")
        if status >= 400 or status == 304:
            # 404/410 and the other 4xx: there is no robots file, so there are no rules.
            return _Entry(None, ALLOWED, f"{robots_url} answered {status}: no rules", now, expires)
        if status != 200:
            return unavailable(f"{robots_url} answered {status}")

        parser = robotparser.RobotFileParser()
        parser.parse(response.body.decode("utf-8", errors="replace").splitlines())
        return _Entry(parser, ALLOWED, None, now, expires)
