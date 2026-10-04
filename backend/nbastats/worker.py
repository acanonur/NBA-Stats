"""The one scheduler process behind every job the EuroLeague, injury and matchup work adds.

``python -m nbastats.worker`` is what ``com.hardwood.worker`` runs under launchd. It owns the
*clock* for those jobs and nothing else: when each one is due, whether it is allowed to run
right now, what happened the last time, and how to survive a Mac that sleeps. The jobs
themselves (fetching a PDF, parsing a box score, writing a projection) live in the packages
that own that data. This module opens no socket and parses no sports payload, so the politeness
rules (one request in flight, a one-second floor, a descriptive User-Agent, a circuit breaker)
stay in exactly one place per source and cannot be skipped by a scheduling shortcut.

Why one process rather than a launchd job per task
--------------------------------------------------
launchd gives a job a calendar *or* an interval *or* KeepAlive, and a plist per task would mean
eleven agents to install, to inspect and to stop when something misbehaves. One KeepAlive
process with an internal scheduler is one thing to look at (``--list``), one log, and one kill
switch. It also serialises the work, which suits SQLite (one writer at a time) and suits the
sources (a polite client is easier to be polite with when only one request ever leaves the
machine at once). The existing ``--watch`` ingest and the 06:10 ``--nightly`` correction pass
stay separate agents on purpose: they predate this process, they have their own tests and
their own signal handling, and a hang in a PDF parser must not stop box scores arriving.

Catching up after sleep needs no code, by construction
------------------------------------------------------
A laptop is asleep for hours and a LaunchAgent only exists while the Mac is awake. So nothing
here is "fire at 05:00": a job is *due* when the newest slot at or before now has not been
attempted yet (daily and weekly jobs), or when its interval has elapsed since the last attempt
(interval jobs). Wake the Mac at 09:00 and the 05:00 slot is simply the latest one, so the job
runs once, now, and never replays the nights it missed. That is also why every job is written to
converge on "the latest valid state at or before ``now``" rather than to process a backlog.
``nba.rosters`` is weekly (Monday 05:30 local) and is additionally due whenever its last success
is more than seven days old, which is the "on start if stale" rule.

How the two kinds of job differ
-------------------------------
*Scheduled by the worker*: ``nba.rosters`` (weekly), ``nba.news`` and ``el.news`` (hourly),
``projections.refresh`` (every 30 minutes), ``projections.lock`` (every five), and
``projections.calibrate`` (daily 05:00), each for the league(s) that have one.

*Called often and expected to decide for themselves*: ``nba.injuries`` (every 15 minutes),
``el.round`` and ``el.box`` (every 10), ``el.rosters`` and ``el.structure`` (hourly) and
``el.ratings`` (every 15). Their real cadence depends on data the worker does not read: the
next tip-off, the day before a round, whether a game has just gone final. The worker calls them
at that base cadence and the job returns ``"skipped"`` quickly when nothing is due. That is a
contract, not a convenience: a job in this group that fetched on every call would break the
request budget its source was designed around.

Jobs are found by string name, and a missing one is a state, not a crash
-------------------------------------------------------------------------
Every job target is a ``"package.module:callable"`` string resolved with
:func:`importlib.import_module` at the moment the job is due. The packages behind them land in
any order and some may never be installed (the ``injuries`` extra, a checkout part-way through a
build), and ``nbastats.euroleague`` is sealed behind an import boundary the isolation test
enforces: no module outside it imports it statically, and this module is one of those. An
``ImportError`` therefore records ``notInstalled`` against the job and the other jobs carry on;
so does a module that exists but exposes no such callable. A module may also export a ``JOBS``
mapping of job id to callable, which wins over the default attribute name. A job id is its key
(``nba.injuries``); the projection jobs exist once per league, so theirs are
``projections.lock@nba`` and ``projections.lock@euroleague``, and the plain key is accepted
too. The callable is
invoked with whichever of ``now`` (aware UTC), ``league``, ``shutdown`` (truthy while the
process is stopping), ``force`` and ``data_dir`` its signature declares, and may return nothing,
a bool, a dict or an object with ``status`` / ``error``; see :func:`normalise_result`.

Settings are re-read on every tick, which is what makes the switches kill switches
-----------------------------------------------------------------------------------
``hardwood.env`` is re-read each tick and applied to ``os.environ``. The API reads it once at
start, and the stock :func:`nbastats.accounts.config.load_env_file` never overrides a variable
that already exists, so calling it from a long-running process would freeze the first values it
saw forever and ``HARDWOOD_EL_LIVE=off`` would only work after a restart. :class:`EnvFileSync`
keeps the same rule where it matters (a variable in the real environment, such as the paths the
launchd plists set, always wins over the file) and adds the missing half: it forgets a value
when the line is removed from the file. Changing a switch therefore stops the fetching at the
next tick, within the tick interval (30 seconds by default).

Defaults are ON; each switch turns its source OFF
--------------------------------------------------
``HARDWOOD_EL_ENABLED`` and ``HARDWOOD_EL_LIVE`` (EuroLeague), ``HARDWOOD_NBA_INJURIES`` and
``HARDWOOD_NEWS`` (headlines) default to on, because this process runs on one person's Mac for
their own use. There is deliberately no review date, no terms URL and no "outcome" to record
before a source runs: ``docs/LEGAL.md`` §2d and §2e state the posture applied to each source and
that its terms could not be read from the development environment. A feed's ``robots.txt`` is
checked by the feed job itself before every fetch.

Two refusals are kept because they protect that posture rather than gate on paperwork:

* the worker refuses to start the jobs that *acquire* EuroLeague data, injury reports or
  headlines when ``HARDWOOD_PUBLIC_BASE_URL`` is not a loopback address. A page other people
  can reach changes everything §2b describes, so the fetching stops instead of continuing
  quietly (:func:`startup_refusals`);
* the NBA injury and roster jobs refuse a store that holds the synthetic demo league, judged
  by the store's own contents (``games.data_source = 'synthetic-demo'``), so real statuses and
  real positions cannot be joined to invented games by a manual re-seed.

What this module deliberately does not touch
--------------------------------------------
Job state lives in ``nba_intel_job_state`` and ``el_job_state``. The worker writes three
columns, ``last_started_at``, ``last_success_at`` and ``last_error``, and never ``cursor_json``,
which belongs to the job (the newest injury slot it fetched, the last round it rated). Those
tables are described here as small private :class:`~sqlalchemy.Table` objects rather than
imported models, so this module neither depends on their ORM class names nor can drift into
writing a column it does not own. When the table does not exist yet the worker schedules from
memory and says so; the EuroLeague store is never created by this process (the API's bootstrap
creates it, and two processes racing to stamp one new file is a bad idea), so EuroLeague jobs
wait, with the reason shown by ``--list``, until the API has run once.

Commands
--------
``python -m nbastats.worker``                  run forever (what launchd runs)
``python -m nbastats.worker --once``           run whatever is due, then exit
``python -m nbastats.worker --list``           every job, its gate, schedule and last run
``python -m nbastats.worker --run KEY``        run one job now (still honours switches)

One worker per data directory: a second copy exits at once (``worker.lock``).
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.util
import inspect
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import threading
import time as time_module
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import (
    Column,
    DateTime,
    Engine,
    MetaData,
    String,
    Table,
    Text,
    insert,
    inspect as sa_inspect,
    select,
    text,
    update,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from .accounts.config import DEFAULT_PUBLIC_BASE_URL, LOOPBACK_HOSTS
from .config import DEFAULT_DATABASE_URL, reset_settings_cache
from .db import create_db_engine, get_engine, init_db

__all__ = [
    "OK",
    "SKIPPED",
    "ERROR",
    "NOT_INSTALLED",
    "TICK_SECONDS",
    "RETRY_BASE_SECONDS",
    "RETRY_MAX_SECONDS",
    "SETTLE_SECONDS",
    "PRIVATE_ONLY_JOBS",
    "Every",
    "DailyAt",
    "WeeklyAt",
    "Gate",
    "Switch",
    "GateContext",
    "CallTarget",
    "CommandTarget",
    "InternalTarget",
    "JobSpec",
    "Outcome",
    "JobState",
    "JobStateStore",
    "ElStoreStatus",
    "ElStore",
    "EnvFileSync",
    "StopSignal",
    "SingleInstance",
    "Worker",
    "JobRun",
    "build_jobs",
    "data_dir",
    "env_file_path",
    "parse_env_text",
    "parse_switch",
    "is_loopback_url",
    "startup_refusals",
    "adopt_installed_database",
    "normalise_result",
    "resolve_callable",
    "call_with_menu",
    "run_command",
    "main",
]

logger = logging.getLogger("nbastats.worker")

# ------------------------------------------------------------------------- outcomes

#: What a job reports. ``skipped`` is a success for scheduling (the job looked and had nothing
#: to do); ``notInstalled`` and ``error`` are failures, retried with a growing back-off.
OK = "ok"
SKIPPED = "skipped"
ERROR = "error"
NOT_INSTALLED = "notInstalled"

#: Seconds between worker ticks. Short enough that a kill switch bites within the half minute,
#: long enough that an idle worker costs nothing. ``HARDWOOD_WORKER_TICK_SECONDS`` overrides it.
TICK_SECONDS = 30.0

#: Back-off after a failed attempt of a daily or weekly job: 15 minutes, doubling per
#: consecutive failure, never more than six hours. Interval jobs simply wait their interval.
RETRY_BASE_SECONDS = 15 * 60
RETRY_MAX_SECONDS = 6 * 60 * 60

#: A file dropped into the inbox is left alone until its modification time is this old, so a
#: workbook that is still being copied is never handed to the importer half-written.
SETTLE_SECONDS = 5.0

#: How long one subprocess job may run before it is stopped.
COMMAND_TIMEOUT_SECONDS = 20 * 60

#: ``last_error`` while a job is running. If the process dies mid-job the marker survives, the
#: job counts as a failed attempt, and it is retried rather than silently skipped until its
#: next slot.
_IN_PROGRESS = "in progress"

_TRUTHY = frozenset({"1", "true", "t", "yes", "y", "on"})
_FALSEY = frozenset({"0", "false", "f", "no", "n", "off"})

_ERROR_WORDS = frozenset({"error", "failed", "failure", "fail"})
_SKIP_WORDS = frozenset({"skipped", "skip", "noop", "idle", "nothing", "notdue"})


def _log_line(event: str, **fields: Any) -> str:
    """One greppable line, ``event=x key=value``, the same shape the ingest runner logs."""
    parts = [f"event={event}"]
    for key, value in fields.items():
        if value is None:
            continue
        rendered = str(value)
        parts.append(f"{key}={rendered!r}" if " " in rendered else f"{key}={rendered}")
    return " ".join(parts)


def _aware(moment: datetime | None) -> datetime | None:
    """Naive UTC (how every column stores time) to an aware UTC datetime."""
    if moment is None:
        return None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def _naive_utc(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


# ------------------------------------------------------------------------ settings


def parse_env_text(raw: str) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines the way :func:`nbastats.accounts.config.load_dotenv` does.

    Blank lines and ``#`` lines are skipped, a matching pair of quotes is stripped, and there is
    no inline-comment syntax, so a ``# note`` after a value becomes part of the value. The same
    rules are kept on purpose: the API and this process read the same file and must never
    disagree about what it says.
    """
    values: dict[str, str] = {}
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


@dataclass(frozen=True, slots=True)
class Switch:
    """A parsed on/off setting. ``problem`` is set when the value was not a boolean; the
    switch then reads as off, because a kill switch with a typo must fail safe."""

    on: bool
    problem: str | None = None


def parse_switch(raw: str | None, name: str, default: bool) -> Switch:
    """Read an on/off value. Unset or empty means ``default``; garbage means off, with a reason."""
    if raw is None or not raw.strip():
        return Switch(default)
    lowered = raw.strip().lower()
    if lowered in _TRUTHY:
        return Switch(True)
    if lowered in _FALSEY:
        return Switch(False)
    return Switch(False, f"{name}={raw.strip()!r} is not on or off, so it is treated as off")


def data_dir(env: Mapping[str, str] | None = None) -> Path:
    """The folder holding the databases, raw payloads, inbox, recordings and ``hardwood.env``.

    ``HARDWOOD_DATA_DIR`` wins; otherwise the macOS home for per-user application data,
    ``~/Library/Application Support/Hardwood``. Anywhere else (CI, a Linux dev box) falls back to
    the XDG location so a test run never writes into a real Mac-style path.
    """
    source = os.environ if env is None else env
    raw = (source.get("HARDWOOD_DATA_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Hardwood"
    base = (source.get("XDG_DATA_HOME") or "").strip()
    root = Path(base).expanduser() if base else Path.home() / ".local" / "share"
    return root / "hardwood"


def env_file_path(env: Mapping[str, str] | None = None) -> Path:
    """``HARDWOOD_ENV_FILE`` if set (the plists set it), else ``<data dir>/hardwood.env``."""
    source = os.environ if env is None else env
    raw = (source.get("HARDWOOD_ENV_FILE") or "").strip()
    return Path(raw).expanduser() if raw else data_dir(source) / "hardwood.env"


def is_loopback_url(url: str) -> bool:
    """True when ``url`` names this machine and nobody else. Unparseable counts as not."""
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return False
    return host.lower() in LOOPBACK_HOSTS


#: Jobs that bring real EuroLeague data, injury reports or headlines onto the machine. They are
#: the ones refused when the deployment is not private (docs/LEGAL.md §2b).
PRIVATE_ONLY_JOBS = (
    "nba.injuries",
    "nba.news",
    "el.workbook",
    "el.structure",
    "el.rosters",
    "el.round",
    "el.box",
    "el.news",
)


def startup_refusals(env: Mapping[str, str] | None = None) -> list[str]:
    """Reasons the worker will not start the acquisition jobs, empty when all is well.

    Reusable on purpose: anything else that wants to refuse the same configuration can ask the
    same question instead of re-deciding what counts as private.
    """
    source = os.environ if env is None else env
    url = (source.get("HARDWOOD_PUBLIC_BASE_URL") or "").strip() or DEFAULT_PUBLIC_BASE_URL
    if is_loopback_url(url):
        return []
    return [
        f"HARDWOOD_PUBLIC_BASE_URL is {url!r}, which is not a loopback address. Hardwood fetches "
        "EuroLeague data, NBA injury reports and headlines only for a private deployment "
        "(docs/LEGAL.md section 2b), so these jobs will not run: "
        + ", ".join(PRIVATE_ONLY_JOBS)
        + ". Set HARDWOOD_PUBLIC_BASE_URL back to http://127.0.0.1:8000 to run them."
    ]


class EnvFileSync:
    """Keep ``os.environ`` in step with ``hardwood.env`` for the life of a long-running process.

    The variables present when this object is created are *protected*: they came from the real
    environment (the launchd plist, an ``export``), and a real value always beats the file, the
    same rule the API's loader follows. Everything else follows the file in both directions: a
    changed line is applied, and a line that is deleted is removed again. An unreadable file
    leaves the last good values in place rather than wiping them on a transient error.
    """

    def __init__(self, path: Path, environ: MutableMapping[str, str] | None = None) -> None:
        self.path = path
        self._environ: MutableMapping[str, str] = os.environ if environ is None else environ
        self._protected = frozenset(self._environ)
        self._applied: dict[str, str] = {}
        self._warned = False

    def refresh(self) -> bool:
        """Re-read the file; return True when anything in the environment changed."""
        try:
            raw = self.path.read_text(encoding="utf-8") if self.path.is_file() else ""
            values = parse_env_text(raw)
        except (OSError, UnicodeDecodeError) as exc:
            if not self._warned:
                logger.warning(_log_line("env_file_unreadable", path=self.path, error=exc))
                self._warned = True
            return False
        self._warned = False
        changed = False
        for key in [k for k in self._applied if k not in values]:
            self._environ.pop(key, None)
            del self._applied[key]
            changed = True
        for key, value in values.items():
            if key in self._protected or self._applied.get(key) == value:
                continue
            self._environ[key] = value
            self._applied[key] = value
            changed = True
        if changed:
            self._drop_caches()
        return changed

    @staticmethod
    def _drop_caches() -> None:
        """Anything memoised from the environment must see the new values."""
        reset_settings_cache()
        try:
            from .accounts.config import reset_auth_settings_cache

            reset_auth_settings_cache()
        except ImportError:  # pragma: no cover - the accounts package is part of the base
            pass


# ------------------------------------------------------------------------ schedules


@dataclass(frozen=True, slots=True)
class Every:
    """Due once ``seconds`` have passed since the last attempt started."""

    seconds: int

    def describe(self) -> str:
        if self.seconds % 3600 == 0:
            hours = self.seconds // 3600
            return "hourly" if hours == 1 else f"every {hours} h"
        if self.seconds % 60 == 0:
            return f"every {self.seconds // 60} min"
        return f"every {self.seconds} s"


def _local_to_utc(naive_local: datetime, tz: tzinfo | None) -> datetime:
    """A wall-clock time in the viewer's zone to aware UTC.

    ``tz=None`` means the machine's own zone, resolved by the C library, which knows the DST
    rules for the date in question. A fixed-offset zone captured once from ``now`` would put a
    05:00 slot an hour off after a clock change.
    """
    if tz is None:
        return naive_local.astimezone(timezone.utc)
    return naive_local.replace(tzinfo=tz).astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class DailyAt:
    """Due once per local calendar day, from ``hour:minute`` onwards."""

    hour: int
    minute: int = 0

    def latest_slot(self, now_utc: datetime, tz: tzinfo | None) -> datetime:
        local_today = now_utc.astimezone(tz).date()
        for back in range(0, 3):
            slot = _local_to_utc(
                datetime.combine(local_today - timedelta(days=back), time(self.hour, self.minute)),
                tz,
            )
            if slot <= now_utc:
                return slot
        raise AssertionError("no daily slot in the last three days")  # pragma: no cover

    def describe(self) -> str:
        return f"daily {self.hour:02d}:{self.minute:02d} local"


@dataclass(frozen=True, slots=True)
class WeeklyAt:
    """Due once per week (Monday is 0) from ``hour:minute`` on that day, and also whenever the
    last success is older than ``max_age``."""

    weekday: int
    hour: int
    minute: int = 0
    max_age: timedelta | None = None

    _NAMES = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")

    def latest_slot(self, now_utc: datetime, tz: tzinfo | None) -> datetime:
        local_today = now_utc.astimezone(tz).date()
        for back in range(0, 8):
            day = local_today - timedelta(days=back)
            if day.weekday() != self.weekday:
                continue
            slot = _local_to_utc(datetime.combine(day, time(self.hour, self.minute)), tz)
            if slot <= now_utc:
                return slot
        raise AssertionError("no weekly slot in the last eight days")  # pragma: no cover

    def describe(self) -> str:
        return f"{self._NAMES[self.weekday]} {self.hour:02d}:{self.minute:02d} local"


Schedule = Every | DailyAt | WeeklyAt


# ---------------------------------------------------------------------------- gates


@dataclass(frozen=True, slots=True)
class Gate:
    """Whether a job may run right now, and if not, the sentence that says why."""

    enabled: bool
    reason: str | None = None


ENABLED = Gate(True)


@dataclass(frozen=True, slots=True)
class ElStoreStatus:
    """What the worker can tell about the EuroLeague store without creating it."""

    ready: bool
    reason: str | None = None


class _Lazy:
    """Compute once per tick, on first use. Probes touch the databases, so each runs at most
    once however many jobs ask."""

    def __init__(self, compute: Callable[[], Any]) -> None:
        self._compute = compute
        self._done = False
        self._value: Any = None

    def __call__(self) -> Any:
        if not self._done:
            self._value = self._compute()
            self._done = True
        return self._value


@dataclass
class GateContext:
    """Everything a gate may look at, snapshotted for one tick."""

    env: Mapping[str, str]
    nba_store_is_synthetic: Callable[[], bool | None]
    el_store: Callable[[], ElStoreStatus]

    def switch(self, name: str, default: bool = True) -> Switch:
        return parse_switch(self.env.get(name), name, default)

    def public_refusal(self) -> str | None:
        refusals = startup_refusals(self.env)
        return refusals[0] if refusals else None


GateFn = Callable[[GateContext], Gate]


def _switch_gate(ctx: GateContext, name: str, what: str) -> Gate:
    switch = ctx.switch(name)
    if switch.problem:
        return Gate(False, switch.problem)
    if not switch.on:
        return Gate(False, f"{what} is switched off ({name})")
    return ENABLED


def _private_gate(ctx: GateContext) -> Gate:
    refusal = ctx.public_refusal()
    return Gate(False, refusal) if refusal else ENABLED


def _first_refusal(ctx: GateContext, *checks: GateFn) -> Gate:
    for check in checks:
        verdict = check(ctx)
        if not verdict.enabled:
            return verdict
    return ENABLED


def gate_always(ctx: GateContext) -> Gate:  # noqa: ARG001 - the shape every gate has
    return ENABLED


def gate_nba_real_store(ctx: GateContext) -> Gate:
    """Refuse a store that holds the synthetic demo league."""
    if ctx.nba_store_is_synthetic():
        return Gate(
            False,
            "Demo league: real statuses and positions are not shown next to invented games",
        )
    return ENABLED


def gate_nba_injuries(ctx: GateContext) -> Gate:
    return _first_refusal(
        ctx,
        lambda c: _switch_gate(c, "HARDWOOD_NBA_INJURIES", "The NBA injury report"),
        _private_gate,
        gate_nba_real_store,
    )


def gate_news(ctx: GateContext) -> Gate:
    return _first_refusal(
        ctx, lambda c: _switch_gate(c, "HARDWOOD_NEWS", "Headlines"), _private_gate
    )


def gate_el_store(ctx: GateContext) -> Gate:
    """The EuroLeague is on, and its store exists and is not the NBA's."""
    verdict = _switch_gate(ctx, "HARDWOOD_EL_ENABLED", "The EuroLeague")
    if not verdict.enabled:
        return verdict
    mode = (ctx.env.get("HARDWOOD_EL_MODE") or "").strip().lower()
    if mode == "off":
        return Gate(False, "The EuroLeague is switched off (HARDWOOD_EL_MODE=off)")
    store = ctx.el_store()
    if not store.ready:
        return Gate(False, store.reason)
    return ENABLED


def gate_el_acquire(ctx: GateContext) -> Gate:
    """A job that brings EuroLeague data in: the store, plus the private-deployment rule."""
    return _first_refusal(ctx, gate_el_store, _private_gate)


def gate_el_live(ctx: GateContext) -> Gate:
    """A job that fetches from the EuroLeague data service."""
    verdict = gate_el_acquire(ctx)
    if not verdict.enabled:
        return verdict
    verdict = _switch_gate(ctx, "HARDWOOD_EL_LIVE", "EuroLeague live ingest")
    if not verdict.enabled:
        return verdict
    mode = (ctx.env.get("HARDWOOD_EL_MODE") or "").strip().lower()
    if mode in ("demo", "workbook"):
        return Gate(False, f"EuroLeague live ingest is off (HARDWOOD_EL_MODE={mode})")
    return ENABLED


def gate_el_news(ctx: GateContext) -> Gate:
    """EuroLeague headlines: the store, the headlines switch and the private-deployment rule.
    Deliberately not behind ``HARDWOOD_EL_LIVE``: feeds are not the data service."""
    return _first_refusal(ctx, gate_el_store, gate_news)


# --------------------------------------------------------------------------- targets


@dataclass(frozen=True, slots=True)
class CallTarget:
    """A Python callable named ``"package.module:callable"``."""

    spec: str


@dataclass(frozen=True, slots=True)
class CommandTarget:
    """A subprocess: ``python <args>``. Used where the designed interface is a command line,
    which is also what keeps a long fetch killable."""

    args: tuple[str, ...]
    requires: tuple[str, ...] = ()
    timeout: float = COMMAND_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class InternalTarget:
    """Work the worker does itself (the inbox scan). ``requires`` are modules that must exist."""

    name: str
    requires: tuple[str, ...] = ()


Target = CallTarget | CommandTarget | InternalTarget


@dataclass(frozen=True, slots=True)
class JobSpec:
    """One scheduled job. ``league`` says which store holds its state, and which league it is
    asked to work on; ``key`` is the name the design and the docs use, and is also the
    ``job_key`` of its state row (each league's table is its own, so a key is unique there)."""

    key: str
    league: str
    title: str
    schedule: Schedule
    target: Target
    gate: GateFn
    decides: bool = False

    @property
    def id(self) -> str:
        """Unique per process, for logs and ``JOBS`` lookups. The projection jobs exist once per
        league, so their id carries it (``projections.lock@nba``); every other id is the key."""
        return f"{self.key}@{self.league}" if self.key.startswith("projections.") else self.key


def build_jobs() -> tuple[JobSpec, ...]:
    """Every job, in priority order: the time-critical local work first, then the fetches.

    The order matters when several jobs are due in one tick (the first start of the day, a wake
    from sleep). A lock window closes at tip-off, so it must never queue behind a fetch.
    """
    nba, el = "nba", "euroleague"
    jobs = "nbastats.euroleague.ingest.jobs"
    intel = "nbastats.nba_intel.jobs"
    return (
        JobSpec("projections.lock", nba, "Freeze NBA projections before tip-off", Every(300),
                CallTarget("nbastats.nba_matchup.ledger:run_lock"), gate_always),
        JobSpec("projections.lock", el, "Freeze EuroLeague projections before tip-off", Every(300),
                CallTarget("nbastats.euroleague.model.ledger:run_lock"), gate_el_store),
        JobSpec("projections.refresh", nba, "Recompute NBA team projections", Every(1800),
                CallTarget("nbastats.nba_matchup.ledger:run_refresh"), gate_always),
        JobSpec("projections.refresh", el, "Recompute EuroLeague projections", Every(1800),
                CallTarget("nbastats.euroleague.model.ledger:run_refresh"), gate_el_store),
        JobSpec("nba.injuries", nba, "NBA official injury report", Every(900),
                CallTarget(f"{intel}:run_injuries"), gate_nba_injuries, decides=True),
        JobSpec("el.round", el, "EuroLeague fixtures and results", Every(600),
                CallTarget(f"{jobs}:run_round"), gate_el_live, decides=True),
        JobSpec("el.box", el, "EuroLeague box scores", Every(600),
                CallTarget(f"{jobs}:run_box"), gate_el_live, decides=True),
        JobSpec("el.workbook", el, "Import a workbook dropped in the inbox", Every(60),
                InternalTarget(
                    "inbox",
                    requires=(
                        "nbastats.euroleague.ingest",
                        "nbastats.euroleague.importers.workbook",
                    ),
                ),
                gate_el_acquire),
        JobSpec("el.ratings", el, "EuroLeague ratings after each completed round", Every(900),
                CallTarget("nbastats.euroleague.model.ratings:run_ratings"), gate_el_store,
                decides=True),
        JobSpec("nba.news", nba, "NBA headlines", Every(3600),
                CallTarget(f"{intel}:run_news"), gate_news),
        JobSpec("el.news", el, "EuroLeague headlines", Every(3600),
                CallTarget(f"{jobs}:run_news"), gate_el_news),
        JobSpec("el.structure", el, "EuroLeague round calendar and clubs", Every(3600),
                CallTarget(f"{jobs}:run_structure"), gate_el_live, decides=True),
        JobSpec("el.rosters", el, "EuroLeague rosters", Every(3600),
                CallTarget(f"{jobs}:run_rosters"), gate_el_live, decides=True),
        JobSpec("nba.rosters", nba, "NBA rosters and listed positions",
                WeeklyAt(0, 5, 30, max_age=timedelta(days=7)),
                CommandTarget(
                    ("-m", "nbastats.ingest.runner", "--rosters"),
                    requires=("nbastats.ingest.rosters",),
                ),
                gate_nba_real_store),
        JobSpec("projections.calibrate", nba, "Fit NBA projection spreads", DailyAt(5, 0),
                CallTarget("nbastats.nba_matchup.projection:run_calibrate"), gate_always),
        JobSpec("projections.calibrate", el, "Fit EuroLeague projection spreads", DailyAt(5, 0),
                CallTarget("nbastats.euroleague.model.ledger:run_calibrate"), gate_el_store),
    )


# ---------------------------------------------------------------------------- results


@dataclass(frozen=True, slots=True)
class Outcome:
    """What one attempt amounted to."""

    status: str
    detail: str | None = None

    @property
    def failed(self) -> bool:
        return self.status in (ERROR, NOT_INSTALLED)


def normalise_result(value: Any) -> Outcome:
    """Turn whatever a job returned into an :class:`Outcome`.

    ``None`` and ``True`` are success. ``False`` is a failure. A string, a mapping or an object
    may carry a ``status`` (``ok``, ``skipped``/``noop``/``idle``, ``error``/``failed``) and an
    ``error``, ``detail``, ``summary`` or ``reason``. Anything unrecognised counts as success:
    a job that raises is the signal for failure, and guessing at a return shape must not turn a
    working job into a broken one.
    """
    if value is None or value is True:
        return Outcome(OK)
    if value is False:
        return Outcome(ERROR, "the job reported failure")

    status: Any
    error: Any
    detail: Any
    if isinstance(value, str):
        status, error, detail = value, None, None
    elif isinstance(value, Mapping):
        status = value.get("status")
        error = value.get("error")
        detail = value.get("detail") or value.get("summary") or value.get("reason")
    else:
        status = getattr(value, "status", None)
        error = getattr(value, "error", None)
        detail = (
            getattr(value, "detail", None)
            or getattr(value, "summary", None)
            or getattr(value, "reason", None)
        )

    label = str(status).strip().lower().replace("_", "").replace("-", "") if status else ""
    if error:
        return Outcome(ERROR, _clip(str(error)))
    if label in _ERROR_WORDS:
        return Outcome(ERROR, _clip(str(detail)) if detail else "the job reported failure")
    if label in _SKIP_WORDS:
        return Outcome(SKIPPED, _clip(str(detail)) if detail else None)
    if isinstance(value, str) and label and label != OK:
        return Outcome(OK, _clip(value))
    return Outcome(OK, _clip(str(detail)) if detail else None)


def _clip(message: str, limit: int = 400) -> str:
    message = " ".join(message.split())
    return message if len(message) <= limit else message[: limit - 1] + "…"


class _Unavailable(Exception):
    """A target that cannot be used here, with the reason to show."""

    def __init__(self, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def modules_available(names: Sequence[str]) -> str | None:
    """``None`` when every module can be found, else the first one that cannot."""
    for name in names:
        try:
            if importlib.util.find_spec(name) is None:
                return name
        except (ImportError, ValueError, AttributeError):
            return name
    return None


def resolve_callable(spec: str, *, job_id: str, key: str) -> Callable[..., Any]:
    """Import ``"module:attr"`` and return the callable, or raise :class:`_Unavailable`.

    ``ImportError`` and a missing attribute are ``notInstalled`` (the package has not landed, or
    the extra is absent). Any other exception while importing is an ``error``: the module exists
    but is broken, and calling that "not installed" would hide a real fault.
    """
    module_name, _, attr = spec.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise _Unavailable(NOT_INSTALLED, f"{module_name} is not installed ({exc})") from exc
    except Exception as exc:  # noqa: BLE001 - a half-written module must not stop the worker
        raise _Unavailable(ERROR, f"{module_name} failed to import: {type(exc).__name__}: {exc}") \
            from exc
    registry = getattr(module, "JOBS", None)
    if isinstance(registry, Mapping):
        for candidate in (job_id, key):
            if callable(registry.get(candidate)):
                return registry[candidate]
    target = getattr(module, attr, None)
    if not callable(target):
        raise _Unavailable(NOT_INSTALLED, f"{module_name} has no callable {attr!r}")
    return target


def call_with_menu(fn: Callable[..., Any], **menu: Any) -> Any:
    """Call ``fn`` with the keyword arguments from ``menu`` that its signature declares.

    A callable taking ``**kwargs`` gets the whole menu. A coroutine function is run to
    completion. This is what lets a job ask for ``now`` or ``shutdown`` only if it wants them,
    without every package having to agree on one signature.
    """
    try:
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        kwargs: dict[str, Any] = {}
    else:
        if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
            kwargs = dict(menu)
        else:
            keyword_kinds = (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            )
            kwargs = {
                name: value
                for name, value in menu.items()
                if name in parameters and parameters[name].kind in keyword_kinds
            }
    result = fn(**kwargs)
    if inspect.isawaitable(result):
        result = asyncio.run(_await(result))
    return result


async def _await(awaitable: Any) -> Any:
    return await awaitable


# ----------------------------------------------------------------------- subprocesses


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    output: str
    timed_out: bool = False
    stopped: bool = False


CommandRunner = Callable[[Sequence[str], float, "StopSignal | None"], CommandResult]


def run_command(
    args: Sequence[str], timeout: float, stop: "StopSignal | None" = None
) -> CommandResult:
    """Run ``python <args>`` and wait, watching for a timeout and for the worker being stopped.

    The child is the same interpreter as the worker, so a venv install is used throughout. A
    stop request terminates the child (SIGTERM, then SIGKILL after ten seconds): the runner it
    starts handles SIGTERM cooperatively, and a partly finished roster pass is safe to repeat.
    """
    process = subprocess.Popen(
        [sys.executable, *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    deadline = time_module.monotonic() + timeout
    chunks: list[str] = []
    reader = threading.Thread(
        target=lambda: chunks.append(process.stdout.read() if process.stdout else ""), daemon=True
    )
    reader.start()
    timed_out = stopped = False
    while process.poll() is None:
        if stop is not None and stop.is_set():
            stopped = True
        elif time_module.monotonic() >= deadline:
            timed_out = True
        else:
            time_module.sleep(0.2)
            continue
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        break
    reader.join(timeout=5)
    return CommandResult(
        process.returncode if process.returncode is not None else -1,
        "".join(chunks),
        timed_out=timed_out,
        stopped=stopped,
    )


# -------------------------------------------------------------------------- job state

#: The columns of ``nba_intel_job_state`` and ``el_job_state`` the worker may touch. The tables
#: also have ``cursor_json``, which is deliberately absent here: it belongs to the job.
NBA_STATE_TABLE = "nba_intel_job_state"
EL_STATE_TABLE = "el_job_state"


def _state_table(name: str) -> Table:
    return Table(
        name,
        MetaData(),
        Column("job_key", String(64), primary_key=True),
        Column("last_started_at", DateTime),
        Column("last_success_at", DateTime),
        Column("last_error", Text),
    )


@dataclass(frozen=True, slots=True)
class JobState:
    """The scheduler's memory of one job. Times are aware UTC."""

    last_started_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None

    @property
    def failed(self) -> bool:
        return bool(self.last_error)


class JobStateStore:
    """Read and write the scheduler columns of one league's job-state table.

    Writes go to an in-memory mirror first and to the database second, so a database that is
    locked or missing never makes a job run twice in a row, and reads take whichever of the two
    saw the later start. When the table does not exist the store says so once and carries on
    from memory.
    """

    def __init__(self, label: str, table_name: str, engine: Callable[[], Engine]) -> None:
        self.label = label
        self._table = _state_table(table_name)
        self._engine = engine
        self._memory: dict[str, JobState] = {}
        self.degraded: str | None = None

    def read(self, key: str) -> JobState:
        mirror = self._memory.get(key, JobState())
        try:
            with self._engine().connect() as connection:
                row = connection.execute(
                    select(
                        self._table.c.last_started_at,
                        self._table.c.last_success_at,
                        self._table.c.last_error,
                    ).where(self._table.c.job_key == key)
                ).one_or_none()
        except SQLAlchemyError as exc:
            self._note_degraded(exc)
            return mirror
        self.degraded = None
        stored = (
            JobState(_aware(row[0]), _aware(row[1]), row[2]) if row is not None else JobState()
        )
        if (mirror.last_started_at or datetime.min.replace(tzinfo=timezone.utc)) > (
            stored.last_started_at or datetime.min.replace(tzinfo=timezone.utc)
        ):
            return mirror
        return stored

    def begin(self, key: str, started: datetime) -> None:
        previous = self._memory.get(key, JobState())
        self._memory[key] = JobState(started, previous.last_success_at, _IN_PROGRESS)
        self._write(key, last_started_at=_naive_utc(started), last_error=_IN_PROGRESS)

    def finish(self, key: str, started: datetime, finished: datetime, outcome: Outcome) -> None:
        previous = self._memory.get(key, JobState())
        succeeded = not outcome.failed
        error = None
        if not succeeded:
            error = _clip(f"{outcome.status}: {outcome.detail or ''}".strip(": "))
        success_at = finished if succeeded else previous.last_success_at
        self._memory[key] = JobState(started, success_at, error)
        values: dict[str, Any] = {
            "last_started_at": _naive_utc(started),
            "last_error": error,
        }
        if succeeded:
            values["last_success_at"] = _naive_utc(finished)
        self._write(key, **values)

    def _write(self, key: str, **values: Any) -> None:
        try:
            with self._engine().begin() as connection:
                done = connection.execute(
                    update(self._table).where(self._table.c.job_key == key).values(**values)
                )
                if done.rowcount == 0:
                    connection.execute(insert(self._table).values(job_key=key, **values))
        except SQLAlchemyError as exc:
            self._note_degraded(exc)

    def _note_degraded(self, exc: Exception) -> None:
        reason = f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else exc}"
        if self.degraded != reason:
            logger.warning(
                _log_line("job_state_unavailable", store=self.label, error=_clip(reason, 200),
                          scheduling="from memory")
            )
        self.degraded = reason


# ------------------------------------------------------------------ the two databases


def _sqlite_path(url: str) -> str | None:
    """The file behind a SQLite URL, or ``None`` for memory and non-SQLite stores."""
    try:
        parsed = make_url(url)
    except Exception:  # noqa: BLE001 - a malformed URL is reported where it is used
        return None
    if not parsed.get_backend_name() == "sqlite":
        return None
    database = parsed.database
    if not database or database == ":memory:":
        return None
    return database


def _store_identity(url: str) -> str:
    """A comparable identity for a store: the real path for SQLite, else the URL minus
    credentials, the same rule the EuroLeague's own same-store guard applies."""
    path = _sqlite_path(url)
    if path is not None:
        return os.path.realpath(path)
    try:
        return make_url(url).set(username=None, password=None).render_as_string(hide_password=True)
    except Exception:  # noqa: BLE001
        return url


def nba_database_url(env: Mapping[str, str]) -> str:
    return (env.get("DATABASE_URL") or "").strip() or DEFAULT_DATABASE_URL


def adopt_installed_database(env: MutableMapping[str, str]) -> str | None:
    """Point a hand-run command at the Mac install's database when nothing says otherwise.

    The launch agents carry ``DATABASE_URL`` in their own environment; a command typed into
    Terminal does not, and would silently open ``./hardwood.db`` in whatever folder it was run
    from, report "the EuroLeague store does not exist yet" and send someone hunting for a fault
    that is only a path. When ``DATABASE_URL`` is unset and the data folder holds a
    ``hardwood.db``, use that one. Returns the URL adopted, or ``None`` when nothing changed.
    """
    if (env.get("DATABASE_URL") or "").strip():
        return None
    candidate = data_dir(env) / "hardwood.db"
    if not candidate.is_file():
        return None
    env["DATABASE_URL"] = f"sqlite:///{candidate}"
    reset_settings_cache()
    return env["DATABASE_URL"]


def el_database_url(env: Mapping[str, str]) -> str:
    """``HARDWOOD_EL_DATABASE_URL``, else a ``hardwood_el.db`` beside the NBA file (the
    ``hardwood.db`` / ``hardwood_el.db`` pair the Mac install uses), else one in the data dir."""
    explicit = (env.get("HARDWOOD_EL_DATABASE_URL") or "").strip()
    if explicit:
        return explicit
    nba_file = _sqlite_path(nba_database_url(env))
    if nba_file is not None:
        path = Path(nba_file)
        return f"sqlite:///{path.with_name(f'{path.stem}_el{path.suffix}')}"
    return f"sqlite:///{data_dir(env) / 'hardwood_el.db'}"


class ElStore:
    """The EuroLeague store as the worker may see it: probed, never created.

    The API's bootstrap creates and stamps the file. If the worker did it too, two processes
    could race to create one new store, and the identity stamp (synthetic, workbook or live)
    must be decided exactly once. Until the file and its job-state table exist the EuroLeague
    jobs wait, and the reason names the fix.
    """

    def __init__(self, env: Callable[[], Mapping[str, str]]) -> None:
        self._env = env
        self._engine: Engine | None = None
        self._engine_url: str | None = None

    def url(self) -> str:
        return el_database_url(self._env())

    def engine(self) -> Engine:
        url = self.url()
        if self._engine is None or self._engine_url != url:
            self.dispose()
            self._engine = create_db_engine(url)
            self._engine_url = url
        return self._engine

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
        self._engine = None
        self._engine_url = None

    def status(self) -> ElStoreStatus:
        env = self._env()
        url = self.url()
        if _store_identity(url) == _store_identity(nba_database_url(env)):
            return ElStoreStatus(
                False,
                "The EuroLeague store is the same database as the NBA store "
                "(HARDWOOD_EL_DATABASE_URL), which is refused",
            )
        path = _sqlite_path(url)
        if path is not None and not Path(path).is_file():
            return ElStoreStatus(
                False,
                "The EuroLeague store does not exist yet: open the API once so it can create it",
            )
        try:
            engine = self.engine()
            if not sa_inspect(engine).has_table(EL_STATE_TABLE):
                return ElStoreStatus(
                    False,
                    f"The EuroLeague store has no {EL_STATE_TABLE} table yet: open the API once",
                )
        except SQLAlchemyError as exc:
            return ElStoreStatus(
                False, f"The EuroLeague store cannot be read: {_clip(str(exc), 160)}"
            )
        return ElStoreStatus(True)


def nba_store_is_synthetic() -> bool | None:
    """True when the NBA store holds the seeded demo league, judged by its own rows.

    This is the one query the design names (``games.data_source = 'synthetic-demo'``), asked of
    the data rather than of ``HARDWOOD_DEMO_MODE``, so a manual re-seed of a live file is caught
    too. ``None`` when the store cannot be read, or does not exist yet, which is treated as
    "not demo": an unreadable store fails the job on its own terms. A SQLite file that is not
    there is never connected to, because connecting would create it, and a question such as
    ``--list`` must not leave a database behind.
    """
    from .config import get_settings

    path = _sqlite_path(get_settings().database_url)
    if path is not None and not Path(path).is_file():
        return None
    try:
        with get_engine().connect() as connection:
            return (
                connection.execute(
                    text("SELECT 1 FROM games WHERE data_source = 'synthetic-demo' LIMIT 1")
                ).first()
                is not None
            )
    except SQLAlchemyError:
        return None


# -------------------------------------------------------------------- process plumbing


class StopSignal:
    """Cooperative shutdown with an interruptible sleep.

    SIGTERM (what ``launchctl bootout`` sends) sets it. The tick loop waits on it instead of
    sleeping, so stopping the agent does not wait out the tick, and jobs that accept
    ``shutdown`` can check it between units of work.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def __bool__(self) -> bool:
        return self._event.is_set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def request(self, signum: int | None = None, frame: Any = None) -> None:
        if not self._event.is_set():
            name = signal.Signals(signum).name if signum is not None else "shutdown"
            logger.info(_log_line("shutdown_requested", signal=name))
        self._event.set()

    def wait(self, seconds: float) -> bool:
        return self._event.wait(seconds)

    def install(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, self.request)
            except (ValueError, OSError):  # not the main thread, or unsupported
                logger.debug("could not install a handler for %s", sig)


class SingleInstance:
    """An advisory lock on ``worker.lock`` so two workers never share a data directory.

    Two schedulers would each believe a job was due and both run it. ``flock`` is released by
    the kernel when the process exits however it exits, so a crash never leaves a stale lock.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: Any = None

    def acquire(self) -> bool:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - not on macOS or Linux
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+")  # noqa: SIM115 - held for the life of the process
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


# ----------------------------------------------------------------------------- worker


@dataclass(frozen=True, slots=True)
class JobRun:
    """One attempt, returned by :meth:`Worker.tick` so tests need not read logs."""

    job: str
    outcome: Outcome
    seconds: float


@dataclass(frozen=True, slots=True)
class Decision:
    """Why a job is or is not running now."""

    due: bool
    gate: Gate
    state: JobState
    why: str


@dataclass(frozen=True, slots=True)
class _InboxFile:
    path: Path
    signature: tuple[int, int]
    settled: bool


class Worker:
    """The scheduler. Construct with :meth:`from_environment` in production.

    Everything with a side effect is a parameter (the clock, the zone, the state stores, the
    command runner, the probes), because a scheduler whose behaviour is "wait for 05:00" can
    only be tested if time is not real.
    """

    def __init__(
        self,
        *,
        jobs: Sequence[JobSpec] | None = None,
        environ: MutableMapping[str, str] | None = None,
        directory: Path | None = None,
        clock: Callable[[], datetime] | None = None,
        tz: tzinfo | None = None,
        stop: StopSignal | None = None,
        states: Mapping[str, JobStateStore] | None = None,
        el_store: ElStore | None = None,
        synthetic_probe: Callable[[], bool | None] | None = None,
        command_runner: CommandRunner | None = None,
        env_sync: EnvFileSync | None = None,
        tick_seconds: float = TICK_SECONDS,
    ) -> None:
        self.environ: MutableMapping[str, str] = os.environ if environ is None else environ
        self.jobs = tuple(jobs) if jobs is not None else build_jobs()
        self.directory = directory or data_dir(self.environ)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.tz = tz
        self.stop = stop or StopSignal()
        self.tick_seconds = tick_seconds
        self.el_store = el_store or ElStore(lambda: self.environ)
        self._synthetic_probe = synthetic_probe or nba_store_is_synthetic
        self.command_runner: CommandRunner = command_runner or run_command
        self.env_sync = env_sync or EnvFileSync(env_file_path(self.environ), self.environ)
        self._states: dict[str, JobStateStore] = dict(states) if states is not None else {
            "nba": JobStateStore("nba", NBA_STATE_TABLE, get_engine),
            "euroleague": JobStateStore("euroleague", EL_STATE_TABLE, self.el_store.engine),
        }
        self._failures: dict[str, int] = {}
        self._gate_seen: dict[str, str | None] = {}
        self._warned_missing: set[str] = set()
        self._inbox_seen: dict[Path, tuple[int, int]] = {}

    # ------------------------------------------------------------------ construction

    @classmethod
    def from_environment(cls, **overrides: Any) -> "Worker":
        return cls(**overrides)

    # ----------------------------------------------------------------------- deciding

    def _context(self) -> GateContext:
        return GateContext(
            env=dict(self.environ),
            nba_store_is_synthetic=_Lazy(self._synthetic_probe),
            el_store=_Lazy(self.el_store.status),
        )

    def decide(self, spec: JobSpec, now: datetime, ctx: GateContext) -> Decision:
        gate = spec.gate(ctx)
        state = JobState()
        if not gate.enabled:
            return Decision(False, gate, state, gate.reason or "disabled")
        state = self._states[spec.league].read(spec.key)
        due, why = self._is_due(spec, state, now)
        return Decision(due, gate, state, why)

    def _is_due(self, spec: JobSpec, state: JobState, now: datetime) -> tuple[bool, str]:
        started = state.last_started_at
        if started is None:
            return True, "never run"
        schedule = spec.schedule
        if isinstance(schedule, Every):
            elapsed = (now - started).total_seconds()
            return (elapsed >= schedule.seconds), f"{elapsed:.0f}s since the last attempt"
        slot = schedule.latest_slot(now, self.tz)
        backoff = self._backoff(spec)
        retry_ready = state.failed and (now - started).total_seconds() >= backoff
        if started < slot:
            return True, f"the {slot:%Y-%m-%d %H:%M} UTC slot has not been attempted"
        if retry_ready:
            return True, "retrying after a failure"
        if isinstance(schedule, WeeklyAt) and schedule.max_age is not None:
            success = state.last_success_at
            stale = success is None or (now - success) > schedule.max_age
            if stale and (not state.failed or retry_ready):
                return True, f"last success is older than {schedule.max_age.days} days"
        return False, "up to date"

    def _backoff(self, spec: JobSpec) -> float:
        count = max(1, self._failures.get(spec.id, 1))
        return float(min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * (2 ** (count - 1))))

    def _note_gate(self, spec: JobSpec, gate: Gate) -> None:
        reason = None if gate.enabled else gate.reason
        if spec.id in self._gate_seen and self._gate_seen[spec.id] == reason:
            return
        first = spec.id not in self._gate_seen
        self._gate_seen[spec.id] = reason
        if reason is None:
            if not first:
                logger.info(_log_line("job_enabled", job=spec.id))
        else:
            logger.info(_log_line("job_disabled", job=spec.id, reason=reason))

    # ------------------------------------------------------------------------- running

    def tick(self) -> list[JobRun]:
        """Re-read the settings, then run every job that is due, highest priority first."""
        self.env_sync.refresh()
        ctx = self._context()
        ran: list[JobRun] = []
        pending = list(self.jobs)
        while pending and not self.stop.is_set():
            now = self.clock()
            chosen: JobSpec | None = None
            for spec in list(pending):
                try:
                    decision = self.decide(spec, now, ctx)
                except Exception:  # noqa: BLE001 - one broken probe must not stop the rest
                    logger.exception(_log_line("job_check_failed", job=spec.id))
                    pending.remove(spec)
                    continue
                self._note_gate(spec, decision.gate)
                if decision.due:
                    chosen = spec
                    break
            if chosen is None:
                break
            pending.remove(chosen)
            ran.append(self.run_job(chosen))
        return ran

    def run_job(self, spec: JobSpec, *, force: bool = False) -> JobRun:
        """Run one job now: record the start, resolve and call it, record the outcome."""
        store = self._states[spec.league]
        started = self.clock()
        store.begin(spec.key, started)
        t0 = time_module.monotonic()
        try:
            outcome = self._invoke(spec, started, force=force)
        except Exception as exc:  # noqa: BLE001 - failure isolation is the point
            logger.exception(_log_line("job_crashed", job=spec.id, league=spec.league))
            outcome = Outcome(ERROR, f"{type(exc).__name__}: {exc}")
        seconds = time_module.monotonic() - t0
        finished = self.clock()
        store.finish(spec.key, started, finished, outcome)
        self._record(spec, outcome, seconds)
        return JobRun(spec.id, outcome, seconds)

    def _record(self, spec: JobSpec, outcome: Outcome, seconds: float) -> None:
        fields = dict(job=spec.id, league=spec.league, seconds=f"{seconds:.2f}",
                      detail=outcome.detail)
        if outcome.failed:
            self._failures[spec.id] = self._failures.get(spec.id, 0) + 1
        else:
            self._failures.pop(spec.id, None)
        if outcome.status == OK:
            # A five-minute job that did its work and has nothing to say would otherwise write
            # several hundred identical lines a day.
            frequent = isinstance(spec.schedule, Every) and spec.schedule.seconds < 3600
            level = logging.DEBUG if frequent and not outcome.detail else logging.INFO
            logger.log(level, _log_line("job_ok", **fields))
        elif outcome.status == SKIPPED:
            logger.debug(_log_line("job_skipped", **fields))
        elif outcome.status == NOT_INSTALLED:
            level = logging.DEBUG if spec.id in self._warned_missing else logging.WARNING
            self._warned_missing.add(spec.id)
            logger.log(level, _log_line("job_not_installed", **fields))
        else:
            logger.error(_log_line("job_failed", **fields))

    def _invoke(self, spec: JobSpec, now: datetime, *, force: bool) -> Outcome:
        target = spec.target
        if isinstance(target, CallTarget):
            try:
                fn = resolve_callable(target.spec, job_id=spec.id, key=spec.key)
            except _Unavailable as exc:
                return Outcome(exc.status, exc.reason)
            result = call_with_menu(
                fn, now=now, league=spec.league, shutdown=self.stop, force=force,
                data_dir=self.directory,
            )
            return normalise_result(result)
        if isinstance(target, CommandTarget):
            missing = modules_available(target.requires)
            if missing:
                return Outcome(NOT_INSTALLED, f"{missing} is not installed")
            return self._outcome_of_command(target.args, target.timeout)
        if isinstance(target, InternalTarget) and target.name == "inbox":
            missing = modules_available(target.requires)
            if missing:
                return Outcome(NOT_INSTALLED, f"{missing} is not installed")
            return self._import_inbox(now)
        return Outcome(ERROR, f"unknown target {target!r}")

    def _outcome_of_command(self, args: Sequence[str], timeout: float) -> Outcome:
        result = self.command_runner(args, timeout, self.stop)
        tail = _clip(" | ".join(result.output.strip().splitlines()[-3:]), 300)
        if result.stopped:
            return Outcome(ERROR, "stopped before it finished; it will run again")
        if result.timed_out:
            return Outcome(ERROR, f"timed out after {timeout:.0f}s")
        if result.returncode != 0:
            return Outcome(ERROR, f"exit {result.returncode}: {tail}")
        return Outcome(OK, tail or None)

    # --------------------------------------------------------------------- the inbox

    def _inbox_files(self, now: datetime) -> list[_InboxFile]:
        folders = [self.directory / "inbox"]
        found: list[Path] = []
        for folder in folders:
            if folder.is_dir():
                found.extend(
                    p for p in folder.iterdir()
                    if p.suffix.lower() == ".xlsx" and not p.name.startswith(("~$", "."))
                    and p.is_file()
                )
        explicit = (self.environ.get("HARDWOOD_WORKBOOK_PATH") or "").strip()
        if explicit and Path(explicit).expanduser().is_file():
            found.append(Path(explicit).expanduser())
        files: list[_InboxFile] = []
        for path in sorted(set(found), key=lambda p: (p.stat().st_mtime, str(p))):
            info = path.stat()
            files.append(
                _InboxFile(
                    path,
                    (info.st_size, info.st_mtime_ns),
                    now.timestamp() - info.st_mtime >= SETTLE_SECONDS,
                )
            )
        return files

    def _import_inbox(self, now: datetime) -> Outcome:
        """Hand every new or changed workbook to the importer, once.

        The importer is idempotent by file hash, so re-handing a known file would be harmless,
        but doing it every minute would be noise. A file is retried only when its size or
        modification time changes, which is also what happens when the user drops in an
        updated workbook under the same name. A failed import is not retried in a loop.
        """
        try:
            files = self._inbox_files(now)
        except OSError as exc:
            return Outcome(
                ERROR,
                f"cannot read a workbook ({exc}). macOS hides Documents, Desktop, Downloads and "
                "iCloud Drive from background programs: copy the file into the inbox folder "
                f"({self.directory / 'inbox'}) instead of pointing at it",
            )
        todo = [f for f in files if f.settled and self._inbox_seen.get(f.path) != f.signature]
        if not todo:
            return Outcome(SKIPPED, "nothing new in the inbox")
        failures: list[str] = []
        done = 0
        for item in todo:
            if self.stop.is_set():
                break
            result = self.command_runner(
                ("-m", "nbastats.euroleague.ingest", "import-workbook", str(item.path)),
                COMMAND_TIMEOUT_SECONDS,
                self.stop,
            )
            if result.stopped:
                break
            self._inbox_seen[item.path] = item.signature
            if result.returncode == 0 and not result.timed_out:
                done += 1
                logger.info(_log_line("workbook_imported", file=item.path.name))
            else:
                tail = _clip(" | ".join(result.output.strip().splitlines()[-3:]), 300)
                failures.append(f"{item.path.name}: exit {result.returncode}: {tail}")
        if failures:
            return Outcome(ERROR, "; ".join(failures))
        return Outcome(OK, f"imported {done} workbook(s)")

    # ---------------------------------------------------------------------- the loop

    def startup(self) -> None:
        """Make sure the NBA store has its tables, and say what will and will not run."""
        self.env_sync.refresh()
        init_db()
        for refusal in startup_refusals(self.environ):
            logger.error(_log_line("startup_refusal", reason=refusal))
        ctx = self._context()
        enabled = []
        for spec in self.jobs:
            gate = spec.gate(ctx)
            self._note_gate(spec, gate)
            if gate.enabled:
                enabled.append(spec.id)
        logger.info(_log_line("worker_started", pid=os.getpid(), jobs=len(self.jobs),
                              enabled=len(enabled), data_dir=self.directory))

    def run_forever(self) -> int:
        self.startup()
        last_heartbeat = time_module.monotonic()
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - the loop outlives any single failure
                logger.exception(_log_line("tick_failed"))
            if time_module.monotonic() - last_heartbeat >= 3600:
                logger.info(_log_line("worker_alive", jobs=len(self.jobs)))
                last_heartbeat = time_module.monotonic()
            self.stop.wait(self.tick_seconds)
        self.el_store.dispose()
        logger.info(_log_line("worker_stopped"))
        return 0

    # --------------------------------------------------------------------- reporting

    def describe_target(self, spec: JobSpec) -> str:
        """``installed`` or the reason it is not, without running anything."""
        target = spec.target
        if isinstance(target, CallTarget):
            try:
                resolve_callable(target.spec, job_id=spec.id, key=spec.key)
            except _Unavailable as exc:
                return f"{exc.status}: {exc.reason}"
            return "installed"
        missing = modules_available(target.requires)
        return f"{NOT_INSTALLED}: {missing} is not installed" if missing else "installed"

    def listing(self) -> list[dict[str, str]]:
        """One row per job for ``--list``: gate, schedule, target, last run. Reads only; a
        store that does not exist yet is not created by asking about it."""
        now = self.clock()
        ctx = self._context()
        nba_file = _sqlite_path(nba_database_url(self.environ))
        nba_readable = nba_file is None or Path(nba_file).is_file()
        rows = []
        for spec in self.jobs:
            gate = spec.gate(ctx)
            state = JobState()
            if not gate.enabled:
                status, note = "off", gate.reason or ""
            else:
                if spec.league != "nba" or nba_readable:
                    state = self._states[spec.league].read(spec.key)
                installed = self.describe_target(spec)
                due, why = self._is_due(spec, state, now)
                if installed != "installed":
                    status, note = "not installed", installed
                else:
                    status, note = ("due" if due else "ready"), why
            started = state.last_started_at
            rows.append(
                {
                    "job": spec.id,
                    "league": spec.league,
                    "schedule": spec.schedule.describe()
                    + (" (job decides)" if spec.decides else ""),
                    "status": status,
                    "last started": f"{started:%Y-%m-%d %H:%M} UTC" if started else "never",
                    "last error": state.last_error or "",
                    "note": note,
                }
            )
        return rows


# -------------------------------------------------------------------------------- CLI


def _configure_logging(level: str, log_dir: str | None) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handler: logging.Handler
    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            Path(log_dir) / "worker.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
    else:
        handler = logging.StreamHandler()
    handler.setFormatter(fmt)
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())
    # SQLAlchemy and httpx are chatty at INFO and say nothing the job lines do not.
    for noisy in ("sqlalchemy", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _print_table(rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    columns = list(rows[0])
    widths = {c: max(len(c), *(len(r[c]) for r in rows)) for c in columns}
    print("  ".join(c.upper().ljust(widths[c]) for c in columns).rstrip())
    for row in rows:
        print("  ".join(row[c].ljust(widths[c]) for c in columns).rstrip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m nbastats.worker",
        description="Hardwood's one scheduler: projections, injuries, headlines and EuroLeague "
        "ingest for both leagues. With no options it runs until it is stopped.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="run whatever is due, then exit")
    mode.add_argument(
        "--list", action="store_true", help="show every job and why it will or will not run"
    )
    mode.add_argument("--run", metavar="KEY", help="run one job now, for example nba.rosters")
    parser.add_argument(
        "--league", choices=("nba", "euroleague"), help="with --run: only this league"
    )
    parser.add_argument("--tick-seconds", type=float, help="seconds between checks (default 30)")
    parser.add_argument("--log-level", help="DEBUG, INFO (default), WARNING")
    return parser


def _tick_seconds(args: argparse.Namespace, env: Mapping[str, str]) -> float:
    raw = args.tick_seconds if args.tick_seconds is not None else env.get(
        "HARDWOOD_WORKER_TICK_SECONDS"
    )
    try:
        value = float(raw) if raw not in (None, "") else TICK_SECONDS
    except ValueError:
        value = TICK_SECONDS
    return min(300.0, max(5.0, value))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sync = EnvFileSync(env_file_path())
    sync.refresh()
    env = os.environ
    adopted = adopt_installed_database(env)
    log_dir = (env.get("HARDWOOD_LOG_DIR") or "").strip() or None
    interactive = bool(args.list or args.run)
    _configure_logging(
        args.log_level or env.get("LOG_LEVEL") or "INFO", None if interactive else log_dir
    )

    if adopted:
        logger.info(_log_line("adopted_database", url=adopted))
    worker = Worker.from_environment(env_sync=sync, tick_seconds=_tick_seconds(args, env))
    try:
        if args.list:
            _print_table(worker.listing())
            for refusal in startup_refusals(worker.environ):
                print(f"\nREFUSED: {refusal}")
            return 0

        if args.run:
            return _run_named(worker, args)

        lock = SingleInstance(worker.directory / "worker.lock")
        if not lock.acquire():
            print(
                f"Another Hardwood worker is already running for {worker.directory} "
                "(see worker.lock). Only one may run at a time.",
                file=sys.stderr,
            )
            return 4
        try:
            worker.stop.install()
            if args.once:
                worker.startup()
                runs = worker.tick()
                for run in runs:
                    print(_describe_run(run))
                return 1 if any(r.outcome.failed for r in runs) else 0
            return worker.run_forever()
        finally:
            lock.release()
    except KeyboardInterrupt:  # pragma: no cover - interactive use
        return 130
    finally:
        worker.el_store.dispose()


def _describe_run(run: JobRun) -> str:
    detail = f" ({run.outcome.detail})" if run.outcome.detail else ""
    return f"{run.job}: {run.outcome.status}{detail}"


def _run_named(worker: Worker, args: argparse.Namespace) -> int:
    """``--run KEY``: one job now. Switches and refusals still apply, because they are the
    user's own kill switches; only the schedule is bypassed."""
    wanted = [
        s for s in worker.jobs
        if args.run in (s.key, s.id) and (args.league is None or s.league == args.league)
    ]
    if not wanted:
        print(f"No job named {args.run!r}. Run with --list to see them.", file=sys.stderr)
        return 2
    init_db()
    ctx = worker._context()
    code = 0
    for spec in wanted:
        gate = spec.gate(ctx)
        if not gate.enabled:
            print(f"{spec.id}: not run. {gate.reason}", file=sys.stderr)
            code = max(code, 3)
            continue
        run = worker.run_job(spec, force=True)
        print(_describe_run(run))
        if run.outcome.failed:
            code = max(code, 1)
    return code


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI
    sys.exit(main())
