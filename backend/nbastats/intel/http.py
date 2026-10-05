"""The polite HTTP client: the one thing in Hardwood that asks a sports host for anything new.

Why this is its own module, and why it is strict
------------------------------------------------
Hardwood is one person's private analytics tool, and the way it earns the right to keep fetching
is by being a good guest. Every rule below exists because breaking it is how a small, polite
tool becomes a blocked IP address, and because the rules are only worth anything if every caller
gets them for free. The NBA injury-report fetcher, the headline fetcher and the EuroLeague's live
client all call :meth:`PoliteClient.get`; none of them may open a socket another way.

The rules, as amended by the lead (``docs/EUROLEAGUE`` design §0.A, which overrides §4.4):

* **A descriptive User-Agent** that names Hardwood and states personal use, and that callers
  cannot replace: ``Hardwood/<version> (private single-user analytics; personal,
  non-commercial use)``.
* **A floor of one second between request starts**, applied across every host rather than per
  host. The design's §4.4 wording is "on every host" and the amendment's is "at least 1 s
  between requests"; a single global floor satisfies both readings and costs one idle second
  when a job happens to touch two hosts.
* **At most two requests in flight.** The scheduler runs jobs one at a time, so in practice this
  is one; the cap exists so a caller that adds a thread cannot quietly multiply the load.
* **Conditional requests.** ``ETag`` and ``Last-Modified`` validators go back as
  ``If-None-Match`` and ``If-Modified-Since``, and a 304 is a normal answer, not an error.
* **Backoff on 429 and 5xx**: up to three attempts, honouring ``Retry-After`` (seconds or an HTTP
  date) capped at 600 seconds, otherwise exponential backoff with jitter. Transport errors
  (timeouts, resets) take the same path.
* **A circuit breaker per host** (or per caller-chosen key). A 401, a 403, a Cloudflare 1015, or
  three 429s in a row opens it for six hours; while it is open no request leaves the machine and
  :class:`CircuitOpenError` says when it closes. The client holds the breaker in memory and
  hands callers plain values (:meth:`PoliteClient.breaker_state`) to persist, and a way to put
  them back (:meth:`PoliteClient.restore_breaker`), because this package knows no database.
* **A 20 second timeout and a hard size cap.** The cap is enforced while streaming and applies
  to the *decoded* bytes, so a small gzip that inflates into gigabytes is stopped at the cap
  too. An error response's body is read only up to 64 KiB and never raises, so a 404 page cannot
  hide its own status behind "too large".

Redirects are followed by hand, at most three, because every hop must pass the same gate as the
first request: the URL scheme is checked, an ``https`` to ``http`` downgrade is refused, the
breaker for the new host is consulted, and the caller's ``allow`` hook runs again. That hook is
how ``robots.txt`` stays binding when a feed's URL redirects somewhere its owner never checked.

What the client does *not* do: it never follows a ``file:`` URL, never sends credentials, never
parses a body, and never retries a request that came back with a final, non-retryable status
(a 404 is returned, not retried and not raised).

Testing
-------
Pass ``transport=httpx.MockTransport(handler)`` and fake ``clock``/``sleep``/``wall_clock``/
``jitter`` callables and the whole policy runs without a socket or a real second passing.
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from importlib import metadata
from typing import Callable, Collection, Mapping
from urllib.parse import urljoin, urlsplit

import httpx

__all__ = [
    "USER_AGENT",
    "MIN_INTERVAL_SECONDS",
    "MAX_IN_FLIGHT",
    "TIMEOUT_SECONDS",
    "MAX_ATTEMPTS",
    "RETRY_AFTER_CAP_SECONDS",
    "BREAKER_PAUSE",
    "CONSECUTIVE_429_TRIP",
    "MAX_REDIRECTS",
    "BACKOFF_BASE_SECONDS",
    "ERROR_BODY_LIMIT",
    "MAX_URL_LENGTH",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_BLOCKED_STATUSES",
    "FetchError",
    "RefusedError",
    "CircuitOpenError",
    "TooLargeError",
    "TransportFailure",
    "StopRequested",
    "Conditional",
    "FetchResponse",
    "BreakerState",
    "PoliteClient",
    "parse_retry_after",
    "is_cloudflare_rate_limit",
    "host_of",
    "link_host",
    "check_url",
]

logger = logging.getLogger(__name__)


def _version() -> str:
    try:
        return metadata.version("hardwood-backend")
    except metadata.PackageNotFoundError:  # pragma: no cover - an uninstalled checkout
        return "1.0.0"


#: Sent on every request and not replaceable per call. It names the project and says what the
#: traffic is, so a site operator who reads their logs knows who to expect and what it is for.
USER_AGENT = f"Hardwood/{_version()} (private single-user analytics; personal, non-commercial use)"

#: Seconds between request *starts*, across every host.
MIN_INTERVAL_SECONDS = 1.0
#: Requests allowed in flight at once.
MAX_IN_FLIGHT = 2
TIMEOUT_SECONDS = 20.0
#: Attempts per request on 429, 5xx or a transport error.
MAX_ATTEMPTS = 3
#: ``Retry-After`` is honoured up to this many seconds.
RETRY_AFTER_CAP_SECONDS = 600.0
BACKOFF_BASE_SECONDS = 2.0
#: How long an open breaker stays open.
BREAKER_PAUSE = timedelta(hours=6)
#: Consecutive 429s that open the breaker.
CONSECUTIVE_429_TRIP = 3
MAX_REDIRECTS = 3
#: The default response cap; feeds use exactly this (2 MB), PDFs and robots.txt pass their own.
DEFAULT_MAX_BYTES = 2_000_000
#: An error response's body is read this far and no further, and never raises.
ERROR_BODY_LIMIT = 64 * 1024
MAX_URL_LENGTH = 2048

#: Statuses that open the breaker at once. Callers whose host answers 403 for "that object does
#: not exist" (an object store) override this per call rather than blocking themselves.
DEFAULT_BLOCKED_STATUSES: frozenset[int] = frozenset({401, 403})

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_CONTROL_OR_SPACE = re.compile(r"[\x00-\x20\x7f]")
_CLOUDFLARE_1015 = re.compile(rb"1015")


# ------------------------------------------------------------------------------ errors


class FetchError(Exception):
    """Base for everything this client raises. ``url`` is the request that failed, if known."""

    def __init__(self, message: str, *, url: str | None = None) -> None:
        super().__init__(message)
        self.url = url


class RefusedError(FetchError):
    """The request was not made: a bad scheme, a redirect loop, or the caller's ``allow`` hook
    (``robots.txt``) said no. ``reason`` is written to be shown to the person who owns the Mac."""

    def __init__(self, reason: str, *, url: str | None = None) -> None:
        super().__init__(reason, url=url)
        self.reason = reason


class CircuitOpenError(FetchError):
    """The host has said no, so no request was made (or the one just made opened the breaker).

    ``paused_until`` is aware UTC; ``status`` is the HTTP status that tripped it, if this very
    call tripped it.
    """

    def __init__(
        self,
        key: str,
        paused_until: datetime,
        reason: str,
        *,
        status: int | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(
            f"requests to {key} are paused until {paused_until.isoformat()}: {reason}", url=url
        )
        self.key = key
        self.paused_until = paused_until
        self.reason = reason
        self.status = status


class TooLargeError(FetchError):
    """The body exceeded the cap. Nothing past the cap was kept."""

    def __init__(self, limit: int, *, url: str | None = None) -> None:
        super().__init__(f"response is larger than the {limit} byte limit", url=url)
        self.limit = limit


class TransportFailure(FetchError):
    """No HTTP response was obtained, after every attempt (DNS, TLS, a reset, a timeout)."""

    def __init__(self, cause: BaseException, attempts: int, *, url: str | None = None) -> None:
        super().__init__(f"{type(cause).__name__}: {cause}", url=url)
        self.cause = cause
        self.attempts = attempts


class StopRequested(FetchError):
    """The process is shutting down; the client stopped waiting rather than finish a backoff."""


# ----------------------------------------------------------------------------- values


@dataclass(frozen=True, slots=True)
class Conditional:
    """Validators from a previous response, sent back to make the request conditional."""

    etag: str | None = None
    last_modified: str | None = None

    def headers(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.etag:
            out["If-None-Match"] = self.etag
        if self.last_modified:
            out["If-Modified-Since"] = self.last_modified
        return out


@dataclass(frozen=True, slots=True)
class FetchResponse:
    """One final HTTP response. Any status is returned; only a *lack* of response raises."""

    url: str
    status: int
    body: bytes
    headers: Mapping[str, str]
    fetched_at: datetime
    attempts: int = 1
    #: True when an error response's body was cut at :data:`ERROR_BODY_LIMIT`.
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def not_modified(self) -> bool:
        return self.status == 304

    @property
    def etag(self) -> str | None:
        return self.headers.get("etag")

    @property
    def last_modified(self) -> str | None:
        return self.headers.get("last-modified")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()


@dataclass(slots=True)
class BreakerState:
    """A host's circuit breaker. ``paused_until`` is aware UTC."""

    paused_until: datetime | None = None
    reason: str | None = None
    consecutive_429: int = 0

    def is_open(self, now: datetime) -> bool:
        return self.paused_until is not None and now < self.paused_until


# ---------------------------------------------------------------------------- helpers


def host_of(url: str) -> str:
    """The lower-case host of ``url`` without trailing dots (``example.org.`` is
    ``example.org``), or an empty string."""
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def link_host(url: str) -> str | None:
    """The host a browser would open for ``url``, normalised for comparison, or ``None``.

    Lower-cased with trailing dots stripped (``WWW.Example.org.`` is ``www.example.org``). A URL
    containing a backslash is ``None``: a browser reads ``\\`` as ``/`` in an http(s) URL, so
    ``https://bad.example\\@good.example/`` opens ``bad.example`` while ``urlsplit`` reports
    ``good.example``. Callers that screen links treat ``None`` as "cannot vouch for it".
    """
    if not isinstance(url, str) or "\\" in url:
        return None
    host = host_of(url.strip())
    return host or None


def check_url(url: str) -> str | None:
    """``None`` when ``url`` may be requested at all, else the reason it may not.

    Only ``http`` and ``https``, a host, no embedded credentials, no whitespace or control
    characters, and a sane length. Everything else is refused before a socket is considered.
    """
    if not isinstance(url, str) or not url:
        return "the URL is empty"
    if len(url) > MAX_URL_LENGTH:
        return "the URL is too long"
    if _CONTROL_OR_SPACE.search(url):
        return "the URL contains whitespace or control characters"
    if "\\" in url:
        return "the URL contains a backslash, which a browser would read differently"
    try:
        parts = urlsplit(url)
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError:
        return "the URL is malformed"
    if parts.scheme not in ("http", "https"):
        return f"the URL scheme {parts.scheme!r} is not http or https"
    if not parts.hostname:
        return "the URL has no host"
    if parts.username is not None or parts.password is not None:
        return "the URL carries credentials"
    return None


def parse_retry_after(value: str | None, now: datetime) -> float | None:
    """Seconds to wait for a ``Retry-After`` header, or ``None`` when it is absent or unusable.

    The header is either delta-seconds or an HTTP date. A date in the past is zero. The caller
    applies the cap.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - now).total_seconds())


def is_cloudflare_rate_limit(status: int, headers: Mapping[str, str], body: bytes) -> bool:
    """True for Cloudflare's "error 1015, you are being rate limited" page.

    It arrives as a 429 (sometimes a 403) with a short HTML body naming the code, and unlike an
    ordinary 429 it comes with a ban measured in minutes to hours, so retrying is the wrong
    response. The check needs Cloudflare's fingerprint as well as the number, so a page that
    merely contains "1015" does not trip it.
    """
    if status not in (403, 429):
        return False
    head = body[:4096]
    if not _CLOUDFLARE_1015.search(head):
        return False
    lowered = head.lower()
    return (
        b"cloudflare" in lowered
        or headers.get("server", "").lower().startswith("cloudflare")
        or "cf-ray" in headers
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


# ----------------------------------------------------------------------------- client


@dataclass
class PoliteClient:
    """A synchronous, thread-safe, deliberately slow HTTP client. See the module docstring.

    Construct one per job run (it is cheap) and use it as a context manager, or call
    :meth:`close`. Every parameter has the design's value as its default; tests override the
    clocks and the transport.
    """

    user_agent: str = USER_AGENT
    min_interval: float = MIN_INTERVAL_SECONDS
    max_in_flight: int = MAX_IN_FLIGHT
    timeout: float = TIMEOUT_SECONDS
    max_attempts: int = MAX_ATTEMPTS
    retry_after_cap: float = RETRY_AFTER_CAP_SECONDS
    backoff_base: float = BACKOFF_BASE_SECONDS
    breaker_pause: timedelta = BREAKER_PAUSE
    rate_limit_trip: int = CONSECUTIVE_429_TRIP
    max_redirects: int = MAX_REDIRECTS
    transport: httpx.BaseTransport | None = None
    clock: Callable[[], float] = time.monotonic
    wall_clock: Callable[[], datetime] = _utcnow
    sleep: Callable[[float], None] | None = None
    jitter: Callable[[], float] = random.random
    #: Polled while waiting; when it returns true the wait ends with :class:`StopRequested`.
    stop_requested: Callable[[], bool] | None = None

    _breakers: dict[str, BreakerState] = field(default_factory=dict, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _slots: threading.BoundedSemaphore = field(init=False, repr=False)
    _next_start: float = field(default=0.0, init=False, repr=False)
    _client: httpx.Client = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_in_flight < 1:
            raise ValueError("max_in_flight must be at least 1")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._slots = threading.BoundedSemaphore(self.max_in_flight)
        self._client = httpx.Client(
            transport=self.transport,
            timeout=httpx.Timeout(self.timeout),
            follow_redirects=False,
        )

    # -- lifecycle ------------------------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- breaker --------------------------------------------------------------------------

    def breaker_state(self, key: str) -> BreakerState:
        """A copy of the breaker for ``key`` (a host unless the caller chose another key)."""
        with self._lock:
            state = self._breakers.get(key)
            return BreakerState(
                state.paused_until if state else None,
                state.reason if state else None,
                state.consecutive_429 if state else 0,
            )

    def restore_breaker(self, key: str, paused_until: datetime | None, reason: str | None) -> None:
        """Put back a pause the caller persisted, so a restart does not forget a block."""
        with self._lock:
            state = self._breakers.setdefault(key, BreakerState())
            state.paused_until = _aware(paused_until)
            state.reason = reason

    def reset_breaker(self, key: str) -> None:
        with self._lock:
            self._breakers.pop(key, None)

    def _open_breaker(
        self, key: str, reason: str, status: int | None, url: str
    ) -> CircuitOpenError:
        with self._lock:
            state = self._breakers.setdefault(key, BreakerState())
            state.paused_until = self.wall_clock() + self.breaker_pause
            state.reason = reason
            state.consecutive_429 = 0
            paused = state.paused_until
        logger.warning("fetch breaker opened key=%s status=%s reason=%s", key, status, reason)
        return CircuitOpenError(key, paused, reason, status=status, url=url)

    def _check_breaker(self, key: str, url: str) -> None:
        with self._lock:
            state = self._breakers.get(key)
            now = self.wall_clock()
            if state is None or state.paused_until is None:
                return
            if state.is_open(now):
                raise CircuitOpenError(
                    key, state.paused_until, state.reason or "an earlier response", url=url
                )
            # The pause has run out: forget it, and the 429 streak that caused it.
            state.paused_until = None
            state.reason = None
            state.consecutive_429 = 0

    # -- waiting --------------------------------------------------------------------------

    def _wait(self, seconds: float) -> None:
        if seconds <= 0:
            return
        if self.sleep is not None:
            self.sleep(seconds)
            return
        deadline = time.monotonic() + seconds
        while True:
            if self.stop_requested is not None and self.stop_requested():
                raise StopRequested("shutdown requested while waiting to make a request")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.25, remaining))

    def _acquire(self) -> None:
        """Take an in-flight slot, then wait out the floor since the previous request start."""
        self._slots.acquire()
        try:
            with self._lock:
                now = self.clock()
                start = max(now, self._next_start)
                self._next_start = start + self.min_interval
            self._wait(start - now)
        except BaseException:
            self._slots.release()
            raise

    def _backoff(self, attempt: int) -> float:
        raw = self.backoff_base * (2 ** (attempt - 1)) + self.jitter() * self.backoff_base
        return min(raw, self.retry_after_cap)

    # -- requests -------------------------------------------------------------------------

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        conditional: Conditional | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        allow: Callable[[str], str | None] | None = None,
        blocked_statuses: Collection[int] | None = None,
        breaker_key: str | None = None,
    ) -> FetchResponse:
        """GET ``url`` under every rule in the module docstring and return the final response.

        ``allow(url)`` returns ``None`` to permit a URL or a reason to refuse it; it runs for
        the first request and again for every redirect hop. ``blocked_statuses`` replaces
        :data:`DEFAULT_BLOCKED_STATUSES` for this call. ``breaker_key`` groups hosts under one
        breaker; the default is the host of each hop.

        Returns for any HTTP status (a 404 or a 304 is an answer). Raises
        :class:`RefusedError`, :class:`CircuitOpenError`, :class:`TooLargeError`,
        :class:`TransportFailure` or :class:`StopRequested` otherwise.
        """
        blocked = (
            DEFAULT_BLOCKED_STATUSES if blocked_statuses is None else frozenset(blocked_statuses)
        )
        current = url
        for _hop in range(self.max_redirects + 1):
            problem = check_url(current)
            if problem:
                raise RefusedError(problem, url=current)
            if allow is not None:
                refusal = allow(current)
                if refusal:
                    raise RefusedError(refusal, url=current)
            key = breaker_key or host_of(current)
            self._check_breaker(key, current)
            response = self._get_with_retries(
                current, key, headers, conditional, max_bytes, blocked
            )
            location = response.headers.get("location")
            if response.status in _REDIRECT_STATUSES and location:
                target = urljoin(current, location)
                if urlsplit(current).scheme == "https" and urlsplit(target).scheme == "http":
                    raise RefusedError(
                        "a redirect would downgrade the connection from https to http",
                        url=target,
                    )
                current = target
                continue
            return response
        raise RefusedError(f"more than {self.max_redirects} redirects", url=current)

    def _get_with_retries(
        self,
        url: str,
        key: str,
        headers: Mapping[str, str] | None,
        conditional: Conditional | None,
        max_bytes: int,
        blocked: frozenset[int],
    ) -> FetchResponse:
        for attempt in range(1, self.max_attempts + 1):
            self._acquire()
            error: BaseException | None = None
            response: FetchResponse | None = None
            try:
                response = self._send_once(url, headers, conditional, max_bytes, attempt)
            except (httpx.TransportError, httpx.DecodingError) as exc:
                error = exc
            except httpx.InvalidURL as exc:
                raise RefusedError(f"the URL is not valid: {exc}", url=url) from exc
            finally:
                # Released *before* any backoff wait, so one slow retry cannot starve the other
                # in-flight slot.
                self._slots.release()

            if error is not None:
                logger.info(
                    "fetch host=%s transport_error=%s attempt=%d",
                    key,
                    type(error).__name__,
                    attempt,
                )
                if attempt < self.max_attempts:
                    self._wait(self._backoff(attempt))
                    continue
                raise TransportFailure(error, attempt, url=url) from error
            assert response is not None

            status = response.status
            logger.info(
                "fetch host=%s status=%s attempt=%d bytes=%d",
                key,
                status,
                attempt,
                len(response.body),
            )

            if is_cloudflare_rate_limit(status, response.headers, response.body):
                raise self._open_breaker(key, "Cloudflare 1015 rate limit", status, url)
            if status in blocked:
                label = {401: "unauthorized (401)", 403: "forbidden (403)"}.get(
                    status, f"blocked ({status})"
                )
                raise self._open_breaker(key, label, status, url)

            if status == 429:
                with self._lock:
                    state = self._breakers.setdefault(key, BreakerState())
                    state.consecutive_429 += 1
                    streak = state.consecutive_429
                if streak >= self.rate_limit_trip:
                    raise self._open_breaker(
                        key, f"{self.rate_limit_trip} 429 responses in a row", status, url
                    )
            else:
                with self._lock:
                    state = self._breakers.get(key)
                    if state is not None:
                        state.consecutive_429 = 0

            if (status == 429 or status >= 500) and attempt < self.max_attempts:
                delay = parse_retry_after(response.headers.get("retry-after"), self.wall_clock())
                wait = (
                    min(delay, self.retry_after_cap)
                    if delay is not None
                    else self._backoff(attempt)
                )
                self._wait(wait)
                continue
            return FetchResponse(
                url=response.url,
                status=status,
                body=response.body,
                headers=response.headers,
                fetched_at=response.fetched_at,
                attempts=attempt,
                truncated=response.truncated,
            )
        # Unreachable: every iteration returns, raises, or continues to a later attempt, and the
        # last attempt always returns or raises. Kept so a future edit cannot fall off the end.
        raise TransportFailure(
            RuntimeError("no attempt was made"), self.max_attempts, url=url
        )  # pragma: no cover

    def _send_once(
        self,
        url: str,
        headers: Mapping[str, str] | None,
        conditional: Conditional | None,
        max_bytes: int,
        attempt: int,
    ) -> FetchResponse:
        request_headers: dict[str, str] = {"Accept": "*/*"}
        for name, value in (headers or {}).items():
            if name.lower() != "user-agent":  # ours is not negotiable
                request_headers[name] = value
        request_headers["User-Agent"] = self.user_agent
        if conditional is not None:
            request_headers.update(conditional.headers())

        with self._client.stream("GET", url, headers=request_headers) as response:
            status = response.status_code
            lowered = {name.lower(): value for name, value in response.headers.items()}
            success = 200 <= status < 300
            limit = max_bytes if success else ERROR_BODY_LIMIT
            declared = lowered.get("content-length", "")
            if success and declared.isdigit() and int(declared) > max_bytes:
                raise TooLargeError(max_bytes, url=url)
            chunks: list[bytes] = []
            total = 0
            truncated = False
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > limit:
                    if success:
                        raise TooLargeError(max_bytes, url=url)
                    keep = len(chunk) - (total - limit)
                    chunks.append(chunk[:keep])
                    truncated = True
                    break
                chunks.append(chunk)
            return FetchResponse(
                url=str(response.url),
                status=status,
                body=b"".join(chunks),
                headers=lowered,
                fetched_at=self.wall_clock(),
                attempts=attempt,
                truncated=truncated,
            )
