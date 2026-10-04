"""The EuroLeague client: the polite HTTP client, narrowed to five URLs, with raw bodies kept.

What this adds to :class:`nbastats.intel.http.PoliteClient`
-----------------------------------------------------------
``PoliteClient`` already owns every rule about *how* to ask a sports host for something: a
User-Agent that names Hardwood and says it is private personal use, a one-second floor between
requests, at most two in flight, conditional requests, ``Retry-After`` and exponential backoff on
429 and 5xx, a circuit breaker that opens for six hours on a 401, a 403, a Cloudflare 1015 or three
429s in a row, a twenty second timeout and a size cap enforced while streaming. This module does
not repeat any of it, and it does not open a socket any other way. What it adds is about *what*
may be asked:

* **Only the five allowlisted URLs** (:mod:`~nbastats.euroleague.ingest.endpoints`). The check
  runs before the request and again, through the client's ``allow`` hook, on every redirect hop,
  so a redirect to another host is refused instead of followed.
* **One breaker for the whole data service** (:data:`BREAKER_KEY`, which is also the
  ``el_source_state`` row it is persisted to), so a refusal on one path stops requests to the rest.
  The client keeps it in memory and hands the caller plain values to store
  (:meth:`EuroLeagueClient.breaker`) and a way to put them back
  (:meth:`EuroLeagueClient.restore_breaker`), because a restarted worker must not forget that the
  service said no.
* **A per-run request budget.** A job that decides it needs forty requests is a job that is wrong
  about its cadence; ``max_requests`` turns that into :class:`BudgetExhausted` instead of a burst.
* **Raw bodies on disk, never in git, never served.** :class:`RawPayloadStore` gzips each ``200``
  body under ``HARDWOOD_DATA_DIR/el-raw/<endpoint>/<aa>/<sha256>.json.gz`` (content-addressed, so an
  unchanged response is stored once) so a parser fixed next week can be re-run over what arrived
  today. The rows that point at them are written by :mod:`~nbastats.euroleague.ingest.write`.

A 404 is an answer, not an error
--------------------------------
:meth:`EuroLeagueClient.get` returns for any HTTP status. A box score that is not published yet
is a ``404`` and the job tries again at its next tick; ``304`` means the validators matched; a
``5xx`` that survived the retries comes back as itself so the job can record "the service is
having a bad day". Only a *lack* of response, a refusal, an open breaker or an oversize body is
an exception, and :func:`classify_failure` turns each into the ``el_source_state`` it means.

Nothing here has been run against the real service. See ``endpoints`` for what that means and
``probe`` for how the first real run is made diagnosable.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from ...intel.http import (
    BreakerState,
    CircuitOpenError,
    Conditional,
    FetchError,
    FetchResponse,
    PoliteClient,
    RefusedError,
    StopRequested,
    TooLargeError,
    TransportFailure,
)
from .endpoints import (
    EndpointNotAllowed,
    MatchedEndpoint,
    club_people_url,
    clubs_url,
    game_stats_url,
    games_url,
    is_allowed,
    match_endpoint,
    rounds_url,
)

__all__ = [
    "SOURCE_KEY",
    "BREAKER_KEY",
    "MAX_BODY_BYTES",
    "DEFAULT_REQUEST_BUDGET",
    "BudgetExhausted",
    "Fetched",
    "Failure",
    "classify_failure",
    "RawRef",
    "RawPayloadStore",
    "el_raw_dir",
    "EuroLeagueClient",
]

logger = logging.getLogger(__name__)

#: The ``el_source_state`` row for the data service, and the breaker's key inside the client.
SOURCE_KEY = "el.dataService"
BREAKER_KEY = SOURCE_KEY
#: A box score is a few hundred kilobytes; four megabytes is a refusal to read a surprise.
MAX_BODY_BYTES = 4_000_000
#: Requests one job run may make. The busiest sweep (twenty rosters) needs twenty-one.
DEFAULT_REQUEST_BUDGET = 40


class BudgetExhausted(RuntimeError):
    """A job asked for more requests than its run is allowed. Nothing further was requested."""

    def __init__(self, budget: int) -> None:
        super().__init__(f"this run has used its budget of {budget} requests")
        self.budget = budget


@dataclass(frozen=True, slots=True)
class Fetched:
    """One final HTTP answer from the data service, with the endpoint it was for."""

    match: MatchedEndpoint
    url: str
    status: int
    body: bytes
    headers: Mapping[str, str]
    fetched_at: datetime
    attempts: int = 1

    @property
    def endpoint_key(self) -> str:
        return self.match.key

    @property
    def ok(self) -> bool:
        return self.status == 200

    @property
    def not_modified(self) -> bool:
        return self.status == 304

    @property
    def missing(self) -> bool:
        return self.status in (404, 410)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    @property
    def etag(self) -> str | None:
        return self.headers.get("etag")

    @property
    def last_modified(self) -> str | None:
        return self.headers.get("last-modified")

    def validators(self) -> Conditional | None:
        """The validators to send next time, or ``None`` if the service offered none."""
        if self.etag or self.last_modified:
            return Conditional(self.etag, self.last_modified)
        return None


# ------------------------------------------------------------------------------ failures


@dataclass(frozen=True, slots=True)
class Failure:
    """What an exception from the client means for ``el_source_state``.

    ``state`` is ``blocked`` (the service said no; ``paused_until`` is when to try again),
    ``unreadable`` is never produced here (that is a parser's verdict), and ``error`` covers
    everything else. ``reason`` is written for the person who owns the Mac.
    """

    state: str
    reason: str
    paused_until: datetime | None = None
    #: True when retrying at the next tick is pointless or harmful (a block, a refusal).
    stop: bool = False


def classify_failure(exc: BaseException) -> Failure:
    """Translate what the client raised into the state a source row should show."""
    if isinstance(exc, CircuitOpenError):
        return Failure("blocked", exc.reason, exc.paused_until, stop=True)
    if isinstance(exc, EndpointNotAllowed):
        return Failure("error", f"refused by the allowlist: {exc}", stop=True)
    if isinstance(exc, RefusedError):
        return Failure(
            "error", f"the request was refused before it was made: {exc.reason}", stop=True
        )
    if isinstance(exc, TooLargeError):
        return Failure("error", f"the response was larger than {exc.limit} bytes and was not read")
    if isinstance(exc, TransportFailure):
        return Failure("error", f"no response from the service: {exc}")
    if isinstance(exc, StopRequested):
        return Failure("error", "stopped because the process is shutting down", stop=True)
    if isinstance(exc, BudgetExhausted):
        return Failure("error", str(exc), stop=True)
    if isinstance(exc, FetchError):
        return Failure("error", str(exc))
    return Failure("error", f"{type(exc).__name__}: {exc}")


# ----------------------------------------------------------------------------- raw bodies


def el_raw_dir(data_dir: Path | str) -> Path:
    """``<data dir>/el-raw``: where gzipped responses are kept."""
    return Path(data_dir) / "el-raw"


@dataclass(frozen=True, slots=True)
class RawRef:
    """A stored raw body: where, how large it was before compression, and its hash."""

    path: Path
    sha256: str
    bytes: int
    created: bool


class RawPayloadStore:
    """Gzipped, content-addressed copies of ``200`` responses. Owner-readable only.

    A file is named for the SHA-256 of the *uncompressed* body, so an unchanged response is one
    file however many times it was fetched, and a compressed file is byte-for-byte reproducible
    (the gzip header's timestamp is fixed at zero). Nothing outside the folder can be written or
    deleted through this class.
    """

    def __init__(self, base: Path | str) -> None:
        self.base = Path(base)

    def _inside(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.base.resolve())
        except (ValueError, OSError):
            return False
        return True

    def save(self, fetched: Fetched) -> RawRef | None:
        """Store a ``200`` body; any other status stores nothing and returns ``None``."""
        if not fetched.ok:
            return None
        digest = fetched.sha256
        path = self.base / fetched.endpoint_key / digest[:2] / f"{digest}.json.gz"
        if path.is_file():
            try:
                os.utime(path)  # seen again: pruning by age counts from the latest fetch
            except OSError:
                pass
            return RawRef(path, digest, len(fetched.body), created=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".part")
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(gzip.compress(fetched.body, compresslevel=6, mtime=0))
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        return RawRef(path, digest, len(fetched.body), created=True)

    def read(self, path: Path | str) -> bytes:
        target = Path(path)
        if not self._inside(target):
            raise ValueError(f"{path} is not inside the raw payload folder")
        return gzip.decompress(target.read_bytes())

    def delete(self, path: Path | str) -> bool:
        """Remove one stored file if (and only if) it is inside the folder. ``True`` if removed."""
        target = Path(path)
        if not self._inside(target) or not target.is_file():
            return False
        try:
            target.unlink()
        except OSError:
            return False
        return True


# -------------------------------------------------------------------------------- client


def _only_allowed(url: str) -> str | None:
    """The ``allow`` hook for :meth:`PoliteClient.get`: runs on the first request and on every
    redirect hop, so a redirect away from the five URLs is refused rather than followed."""
    if is_allowed(url):
        return None
    return "the URL is not one of the five EuroLeague endpoints the ingest may request"


class EuroLeagueClient:
    """Five typed methods over :class:`PoliteClient`. Construct one per job run.

    ``polite`` may be supplied (tests give it a mock transport and fake clocks); otherwise
    ``polite_options`` build one. ``stop_requested`` is polled while the client waits, so a
    shutdown interrupts a backoff. Use it as a context manager or call :meth:`close`.
    """

    def __init__(
        self,
        polite: PoliteClient | None = None,
        *,
        max_requests: int | None = DEFAULT_REQUEST_BUDGET,
        stop_requested: Callable[[], bool] | None = None,
        **polite_options: Any,
    ) -> None:
        if polite is not None and (polite_options or stop_requested is not None):
            raise ValueError("pass either a PoliteClient or options for one, not both")
        if polite is None:
            if stop_requested is not None:
                polite_options["stop_requested"] = stop_requested
            polite = PoliteClient(**polite_options)
        self._polite = polite
        self.max_requests = max_requests
        #: Calls made through :meth:`get`.
        self.requests_made = 0
        #: Network attempts, counting each retry the polite client made.
        self.attempts_made = 0

    # -- lifecycle ------------------------------------------------------------------------

    def close(self) -> None:
        self._polite.close()

    def __enter__(self) -> "EuroLeagueClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- breaker --------------------------------------------------------------------------

    def breaker(self) -> BreakerState:
        """A copy of the data service's breaker, for the caller to persist."""
        return self._polite.breaker_state(BREAKER_KEY)

    def restore_breaker(self, paused_until: datetime | None, reason: str | None) -> None:
        """Put back a pause the caller persisted (a restart must not forget a block)."""
        self._polite.restore_breaker(BREAKER_KEY, paused_until, reason)

    def reset_breaker(self) -> None:
        self._polite.reset_breaker(BREAKER_KEY)

    @property
    def remaining(self) -> int | None:
        """Requests left in this run's budget, or ``None`` when there is no limit."""
        return None if self.max_requests is None else max(0, self.max_requests - self.requests_made)

    # -- requests -------------------------------------------------------------------------

    def get(self, url: str, *, validators: Conditional | None = None) -> Fetched:
        """GET one allowlisted URL and return the final response, whatever its status.

        Raises :class:`~nbastats.euroleague.ingest.endpoints.EndpointNotAllowed` for any other URL
        (before anything is sent), :class:`BudgetExhausted` past the run's budget, and the
        polite client's :class:`CircuitOpenError`, :class:`RefusedError`, :class:`TooLargeError`,
        :class:`TransportFailure` or :class:`StopRequested` as they come.
        """
        match = match_endpoint(url)
        if self.max_requests is not None and self.requests_made >= self.max_requests:
            raise BudgetExhausted(self.max_requests)
        self.requests_made += 1
        response: FetchResponse = self._polite.get(
            url,
            headers={"Accept": "application/json"},
            conditional=validators,
            max_bytes=MAX_BODY_BYTES,
            allow=_only_allowed,
            breaker_key=BREAKER_KEY,
        )
        self.attempts_made += response.attempts
        return Fetched(
            match=match,
            url=url,
            status=response.status,
            body=response.body,
            headers=dict(response.headers),
            fetched_at=response.fetched_at,
            attempts=response.attempts,
        )

    def rounds(self, season: str, *, validators: Conditional | None = None) -> Fetched:
        """E1."""
        return self.get(rounds_url(season), validators=validators)

    def games(
        self, season: str, round_number: int, *, validators: Conditional | None = None
    ) -> Fetched:
        """E2."""
        return self.get(games_url(season, round_number), validators=validators)

    def box_score(
        self, season: str, game_code: int, *, validators: Conditional | None = None
    ) -> Fetched:
        """E3."""
        return self.get(game_stats_url(season, game_code), validators=validators)

    def clubs(self, season: str, *, validators: Conditional | None = None) -> Fetched:
        """E4."""
        return self.get(clubs_url(season), validators=validators)

    def people(
        self, season: str, club_code: str, *, validators: Conditional | None = None
    ) -> Fetched:
        """E5."""
        return self.get(club_people_url(season, club_code), validators=validators)
