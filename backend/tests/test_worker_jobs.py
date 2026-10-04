"""The worker and the macOS operations around it: scheduling, switches, state, installers.

Nothing here opens a socket or waits on a real clock. The worker takes its clock, zone, job
state and command runner as parameters, so a scheduler whose whole behaviour is "wait until
05:00" can be driven through a week in a few milliseconds, and the tests are about the
promises a person on a Mac actually relies on:

* the Mac slept through 05:00 and the job still runs, once, when it wakes;
* a job that does not exist yet, or one that crashes, never stops the others;
* editing ``hardwood.env`` stops a source at the next tick (the kill switch);
* a public deployment, or a demo league, never gets real data fetched into it;
* the scheduler never writes the one column that belongs to the job (``cursor_json``);
* the launchd files and the installer are valid and say what the runbook says they do.

The job-state tables are created here from the columns the design names, because this file
must pass whatever order the other packages land in.
"""
from __future__ import annotations

import ast
import asyncio
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import textwrap
import threading
import time
import types
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine, create_engine, text

from nbastats import config as stats_config
from nbastats import db as db_module
from nbastats import worker
from nbastats.accounts.config import load_dotenv
from nbastats.worker import (
    DailyAt,
    ElStore,
    ElStoreStatus,
    EnvFileSync,
    Every,
    GateContext,
    JobSpec,
    JobState,
    JobStateStore,
    Outcome,
    SingleInstance,
    StopSignal,
    WeeklyAt,
    Worker,
    build_jobs,
    call_with_menu,
    normalise_result,
    parse_env_text,
    parse_switch,
    startup_refusals,
)

UTC = timezone.utc
NEW_YORK = ZoneInfo("America/New_York")
BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
MACOS = BACKEND / "scripts" / "macos"
DOCS = REPO / "docs"

STATE_DDL = (
    "CREATE TABLE {table} (job_key VARCHAR(64) PRIMARY KEY, last_started_at DATETIME, "
    "last_success_at DATETIME, last_error TEXT, cursor_json TEXT)"
)


# ----------------------------------------------------------------------------- helpers


class Clock:
    """A clock that only moves when told to."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def state_engine(path: Path, table: str, *, create: bool = True) -> Engine:
    engine = create_engine(f"sqlite:///{path}")
    if create:
        with engine.begin() as connection:
            connection.execute(text(STATE_DDL.format(table=table)))
    return engine


class FakeElStore:
    """Stands in for :class:`ElStore`: always ready, with a state database of its own."""

    def __init__(self, engine: Engine, ready: bool = True, reason: str | None = None) -> None:
        self._engine = engine
        self._status = ElStoreStatus(ready, reason)

    def status(self) -> ElStoreStatus:
        return self._status

    def engine(self) -> Engine:
        return self._engine

    def dispose(self) -> None:
        pass


def job_module(monkeypatch: pytest.MonkeyPatch, name: str, **attrs: Any) -> str:
    """Install a throwaway module so a ``"module:callable"`` target resolves to it."""
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)
    return name


def spec(
    key: str,
    target: str,
    schedule: Any = None,
    *,
    league: str = "nba",
    gate: Callable[[GateContext], Any] = worker.gate_always,
) -> JobSpec:
    return JobSpec(
        key,
        league,
        key,
        schedule or Every(60),
        worker.CallTarget(target),
        gate,
    )


@pytest.fixture()
def make_worker(tmp_path: Path):
    """Build a worker over temporary state databases and a controllable clock."""
    built: list[Engine] = []

    def build(
        specs: list[JobSpec],
        *,
        start: datetime = datetime(2026, 10, 5, 12, 0, tzinfo=UTC),
        environ: dict[str, str] | None = None,
        tz: Any = None,
        synthetic: bool = False,
        el_ready: bool = True,
        runner: Callable[..., Any] | None = None,
        create_tables: bool = True,
    ) -> tuple[Worker, Clock]:
        env = {"HARDWOOD_DATA_DIR": str(tmp_path)}
        env.update(environ or {})
        nba = state_engine(tmp_path / "nba.db", worker.NBA_STATE_TABLE, create=create_tables)
        el = state_engine(tmp_path / "el.db", worker.EL_STATE_TABLE, create=create_tables)
        built.extend([nba, el])
        clock = Clock(start)
        instance = Worker(
            jobs=specs,
            environ=env,
            directory=tmp_path,
            clock=clock,
            tz=tz,
            stop=StopSignal(),
            states={
                "nba": JobStateStore("nba", worker.NBA_STATE_TABLE, lambda: nba),
                "euroleague": JobStateStore("euroleague", worker.EL_STATE_TABLE, lambda: el),
            },
            el_store=FakeElStore(  # type: ignore[arg-type]
                el, ready=el_ready, reason=None if el_ready else "waiting"
            ),
            synthetic_probe=lambda: synthetic,
            command_runner=runner,
        )
        instance._engines = {"nba": nba, "euroleague": el}  # type: ignore[attr-defined]
        return instance, clock

    yield build
    for engine in built:
        engine.dispose()


def stored(instance: Worker, league: str, key: str) -> dict[str, Any]:
    engine: Engine = instance._engines[league]  # type: ignore[attr-defined]
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT * FROM %s WHERE job_key = :k" % (
                worker.NBA_STATE_TABLE if league == "nba" else worker.EL_STATE_TABLE)),
            {"k": key},
        ).mappings().one_or_none()
    return dict(row) if row else {}


@pytest.fixture()
def calls() -> list[str]:
    return []


@pytest.fixture(autouse=True)
def _restore_logging():
    """``main()`` configures the root logger; leave it as the next test expects it."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    for handler in handlers:
        if handler not in root.handlers:
            root.addHandler(handler)
    root.setLevel(level)


# ------------------------------------------------------------------------ the schedules


def test_every_is_due_after_its_interval_and_catches_up_once_after_a_sleep(
    make_worker, monkeypatch, calls
) -> None:
    name = job_module(monkeypatch, "hw_every", run=lambda: calls.append("run"))
    instance, clock = make_worker([spec("projections.refresh", f"{name}:run", Every(1800))])

    assert [r.outcome.status for r in instance.tick()] == ["ok"]  # never run: due at once
    clock.advance(minutes=29)
    assert instance.tick() == []
    clock.advance(minutes=1)
    assert len(instance.tick()) == 1

    # The Mac sleeps for nine hours. It wakes, runs once, and does not replay the night.
    clock.advance(hours=9)
    assert len(instance.tick()) == 1
    assert instance.tick() == []
    assert calls == ["run", "run", "run"]


def test_a_daily_job_missed_in_sleep_runs_once_when_the_mac_wakes(
    make_worker, monkeypatch, calls
) -> None:
    name = job_module(monkeypatch, "hw_daily", run=lambda: calls.append("run"))
    start = datetime(2026, 10, 6, 4, 0, tzinfo=NEW_YORK).astimezone(UTC)
    instance, clock = make_worker(
        [spec("projections.calibrate", f"{name}:run", DailyAt(5, 0))], start=start, tz=NEW_YORK
    )

    instance.tick()  # first start: never run, so it runs now whatever the hour
    assert calls == ["run"]
    clock.now = datetime(2026, 10, 6, 4, 59, tzinfo=NEW_YORK).astimezone(UTC)
    assert instance.tick() == []
    clock.now = datetime(2026, 10, 6, 5, 0, tzinfo=NEW_YORK).astimezone(UTC)
    assert len(instance.tick()) == 1  # the 05:00 slot has arrived

    # Asleep through the 05:00 slot on the 7th and the 8th, awake at 09:00 on the 8th.
    clock.now = datetime(2026, 10, 8, 9, 0, tzinfo=NEW_YORK).astimezone(UTC)
    assert len(instance.tick()) == 1
    assert instance.tick() == []
    assert calls == ["run", "run", "run"]


def test_a_weekly_job_runs_monday_morning_and_whenever_it_is_stale() -> None:
    weekly = WeeklyAt(0, 5, 30, max_age=timedelta(days=7))
    sunday = datetime(2026, 10, 4, 12, 0, tzinfo=NEW_YORK).astimezone(UTC)
    assert weekly.latest_slot(sunday, NEW_YORK) == datetime(
        2026, 9, 28, 5, 30, tzinfo=NEW_YORK
    ).astimezone(UTC)
    monday_early = datetime(2026, 10, 5, 5, 29, tzinfo=NEW_YORK).astimezone(UTC)
    monday_slot = datetime(2026, 10, 5, 5, 30, tzinfo=NEW_YORK).astimezone(UTC)
    assert weekly.latest_slot(monday_early, NEW_YORK) < monday_slot
    assert weekly.latest_slot(monday_slot, NEW_YORK) == monday_slot
    assert weekly.describe() == "Mondays 05:30 local"


def test_a_weekly_job_that_is_more_than_a_week_stale_is_due_on_start(make_worker) -> None:
    instance, _ = make_worker([], tz=NEW_YORK)
    weekly = spec("nba.rosters", "x:y", WeeklyAt(0, 5, 30, max_age=timedelta(days=7)))
    now = datetime(2026, 10, 7, 12, 0, tzinfo=NEW_YORK).astimezone(UTC)
    slot = datetime(2026, 10, 5, 5, 30, tzinfo=NEW_YORK).astimezone(UTC)

    fresh = JobState(slot + timedelta(minutes=1), slot + timedelta(minutes=2), None)
    assert instance._is_due(weekly, fresh, now) == (False, "up to date")

    # The last attempt was after the slot, but the last success is eight days old.
    stale = JobState(slot + timedelta(minutes=1), now - timedelta(days=8), None)
    due, why = instance._is_due(weekly, stale, now)
    assert due and "older than 7 days" in why


def test_slots_follow_the_clock_change_not_a_fixed_offset() -> None:
    daily = DailyAt(5, 0)
    # US clocks go forward at 02:00 on 14 March 2027: 05:00 is 10:00 UTC before, 09:00 after.
    before = daily.latest_slot(datetime(2027, 3, 13, 15, 0, tzinfo=UTC), NEW_YORK)
    after = daily.latest_slot(datetime(2027, 3, 14, 15, 0, tzinfo=UTC), NEW_YORK)
    assert before == datetime(2027, 3, 13, 10, 0, tzinfo=UTC)
    assert after == datetime(2027, 3, 14, 9, 0, tzinfo=UTC)


def test_schedules_describe_themselves_in_words() -> None:
    assert Every(300).describe() == "every 5 min"
    assert Every(3600).describe() == "hourly"
    assert Every(7200).describe() == "every 2 h"
    assert DailyAt(5, 0).describe() == "daily 05:00 local"


# --------------------------------------------------------------------- failure isolation


def test_a_crashing_job_does_not_stop_the_others_and_is_recorded(
    make_worker, monkeypatch, calls
) -> None:
    def boom() -> None:
        raise RuntimeError("the feed fell over")

    name = job_module(monkeypatch, "hw_crash", boom=boom, fine=lambda: calls.append("fine"))
    instance, _ = make_worker(
        [
            spec("nba.injuries", f"{name}:boom"),
            spec("nba.news", f"{name}:fine"),
        ]
    )
    runs = {r.job: r.outcome for r in instance.tick()}
    assert runs["nba.injuries"].status == "error"
    assert "the feed fell over" in (runs["nba.injuries"].detail or "")
    assert runs["nba.news"].status == "ok"
    row = stored(instance, "nba", "nba.injuries")
    assert "RuntimeError" in row["last_error"] and row["last_success_at"] is None
    assert stored(instance, "nba", "nba.news")["last_error"] is None
    assert calls == ["fine"]


def test_a_failing_daily_job_backs_off_and_doubles(make_worker, monkeypatch, calls) -> None:
    def always_fails() -> None:
        calls.append("try")
        raise RuntimeError("nope")

    name = job_module(monkeypatch, "hw_backoff", fail=always_fails)
    start = datetime(2026, 10, 6, 6, 0, tzinfo=NEW_YORK).astimezone(UTC)
    instance, clock = make_worker(
        [spec("projections.calibrate", f"{name}:fail", DailyAt(5, 0))], start=start, tz=NEW_YORK
    )
    instance.tick()
    assert len(calls) == 1
    clock.advance(minutes=14)
    assert instance.tick() == []
    clock.advance(minutes=1)  # 15 minutes after the first failure
    instance.tick()
    assert len(calls) == 2
    clock.advance(minutes=29)  # the second back-off is 30 minutes
    assert instance.tick() == []
    clock.advance(minutes=1)
    instance.tick()
    assert len(calls) == 3


def test_a_run_the_process_never_finished_is_retried(make_worker, monkeypatch, calls) -> None:
    name = job_module(monkeypatch, "hw_interrupted", run=lambda: calls.append("run"))
    start = datetime(2026, 10, 6, 6, 0, tzinfo=NEW_YORK).astimezone(UTC)
    instance, clock = make_worker(
        [spec("projections.calibrate", f"{name}:run", DailyAt(5, 0))], start=start, tz=NEW_YORK
    )
    # A previous process died mid-run: it left the start time and the in-progress marker.
    instance._states["nba"].begin("projections.calibrate", start - timedelta(minutes=20))
    assert stored(instance, "nba", "projections.calibrate")["last_error"] == "in progress"
    assert len(instance.tick()) == 1
    assert stored(instance, "nba", "projections.calibrate")["last_error"] is None
    assert calls == ["run"]
    assert clock() == start


def test_a_skipped_run_counts_as_done_and_is_not_repeated(make_worker, monkeypatch) -> None:
    name = job_module(monkeypatch, "hw_skip", run=lambda: {"status": "skipped", "reason": "idle"})
    instance, clock = make_worker([spec("nba.injuries", f"{name}:run", Every(900))])
    assert instance.tick()[0].outcome.status == "skipped"
    row = stored(instance, "nba", "nba.injuries")
    assert row["last_success_at"] is not None and row["last_error"] is None
    clock.advance(minutes=5)
    assert instance.tick() == []


# ------------------------------------------------------------------------- the job state


def test_the_scheduler_never_writes_the_job_cursor(make_worker, monkeypatch) -> None:
    name = job_module(monkeypatch, "hw_cursor", run=lambda: None)
    instance, _ = make_worker([spec("nba.injuries", f"{name}:run")])
    engine: Engine = instance._engines["nba"]  # type: ignore[attr-defined]
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO nba_intel_job_state (job_key, cursor_json) "
                "VALUES ('nba.injuries', '{\"newestSlot\": \"2026-10-05T17:15\"}')"
            )
        )
    instance.tick()
    row = stored(instance, "nba", "nba.injuries")
    assert row["cursor_json"] == '{"newestSlot": "2026-10-05T17:15"}'
    assert row["last_started_at"] is not None and row["last_success_at"] is not None


def test_state_survives_a_restart(make_worker, monkeypatch, calls) -> None:
    name = job_module(monkeypatch, "hw_restart", run=lambda: calls.append("run"))
    specs = [spec("nba.news", f"{name}:run", Every(3600))]
    first, clock = make_worker(specs)
    first.tick()
    # A new process: fresh in-memory mirror, same database.
    engine: Engine = first._engines["nba"]  # type: ignore[attr-defined]
    second = Worker(
        jobs=specs,
        environ={"HARDWOOD_DATA_DIR": str(first.directory)},
        directory=first.directory,
        clock=clock,
        states={
            "nba": JobStateStore("nba", worker.NBA_STATE_TABLE, lambda: engine),
            "euroleague": JobStateStore("euroleague", worker.EL_STATE_TABLE, lambda: engine),
        },
        synthetic_probe=lambda: False,
    )
    assert second.tick() == []
    clock.advance(hours=1)
    assert len(second.tick()) == 1


def test_a_missing_state_table_means_scheduling_from_memory_not_a_crash(
    make_worker, monkeypatch, calls, caplog
) -> None:
    name = job_module(monkeypatch, "hw_nostate", run=lambda: calls.append("run"))
    instance, clock = make_worker(
        [spec("nba.news", f"{name}:run", Every(3600))], create_tables=False
    )
    with caplog.at_level(logging.WARNING, logger="nbastats.worker"):
        assert len(instance.tick()) == 1
        assert instance.tick() == []  # not run again in the same hour, table or no table
        clock.advance(hours=1)
        assert len(instance.tick()) == 1
    assert instance._states["nba"].degraded
    assert "job_state_unavailable" in caplog.text
    assert calls == ["run", "run"]


# ---------------------------------------------------------------------- finding the jobs


def test_a_job_whose_package_is_not_installed_is_recorded_and_the_rest_run(
    make_worker, monkeypatch, calls
) -> None:
    name = job_module(monkeypatch, "hw_present", run=lambda: calls.append("run"))
    instance, _ = make_worker(
        [
            spec("el.round", "hw_definitely_not_a_module:run_round", league="euroleague"),
            spec("nba.news", f"{name}:run"),
        ]
    )
    runs = {r.job: r.outcome for r in instance.tick()}
    assert runs["el.round"].status == "notInstalled"
    assert "hw_definitely_not_a_module" in (runs["el.round"].detail or "")
    assert runs["nba.news"].status == "ok"
    row = stored(instance, "euroleague", "el.round")
    assert row["last_error"].startswith("notInstalled")
    assert row["last_success_at"] is None


def test_a_module_without_the_callable_is_not_installed(make_worker, monkeypatch) -> None:
    name = job_module(monkeypatch, "hw_partial", other=lambda: None)
    instance, _ = make_worker([spec("nba.news", f"{name}:run_news")])
    outcome = instance.tick()[0].outcome
    assert outcome.status == "notInstalled" and "run_news" in (outcome.detail or "")


def test_a_module_that_is_broken_is_an_error_not_a_missing_package(
    make_worker, tmp_path, monkeypatch
) -> None:
    package = tmp_path / "pkgs"
    package.mkdir()
    (package / "hw_broken.py").write_text("raise RuntimeError('half written')\n")
    (package / "hw_needs_extra.py").write_text("import hw_the_extra_that_is_not_there\n")
    monkeypatch.syspath_prepend(str(package))
    instance, _ = make_worker(
        [spec("nba.news", "hw_broken:run"), spec("nba.injuries", "hw_needs_extra:run")]
    )
    runs = {r.job: r.outcome for r in instance.tick()}
    assert runs["nba.news"].status == "error" and "half written" in (runs["nba.news"].detail or "")
    assert runs["nba.injuries"].status == "notInstalled"


def test_a_jobs_mapping_wins_over_the_default_name(make_worker, monkeypatch, calls) -> None:
    name = job_module(
        monkeypatch,
        "hw_registry",
        run=lambda: calls.append("default"),
        JOBS={"projections.lock@euroleague": lambda: calls.append("registry")},
    )
    instance, _ = make_worker(
        [spec("projections.lock", f"{name}:run", league="euroleague")]
    )
    instance.tick()
    assert calls == ["registry"]


def test_a_job_is_called_with_only_the_arguments_it_declares() -> None:
    seen: dict[str, Any] = {}

    def narrow(now: datetime) -> None:
        seen["narrow"] = {"now": now}

    def keywords(**kwargs: Any) -> None:
        seen["keywords"] = sorted(kwargs)

    def bare() -> str:
        return "bare"

    menu = {"now": 1, "league": "nba", "shutdown": None, "force": False, "data_dir": "x"}
    call_with_menu(narrow, **menu)
    call_with_menu(keywords, **menu)
    assert seen["narrow"] == {"now": 1}
    assert seen["keywords"] == ["data_dir", "force", "league", "now", "shutdown"]
    assert call_with_menu(bare, **menu) == "bare"

    async def coroutine(league: str) -> str:
        return league.upper()

    assert call_with_menu(coroutine, **menu) == "NBA"


@pytest.mark.parametrize(
    ("value", "status", "detail"),
    [
        (None, "ok", None),
        (True, "ok", None),
        (False, "error", "the job reported failure"),
        ("skipped", "skipped", None),
        ("done", "ok", "done"),
        ({"status": "idle", "reason": "no game inside 36 h"}, "skipped", "no game inside 36 h"),
        ({"status": "ok", "summary": "3 rows"}, "ok", "3 rows"),
        ({"error": "headerMismatch"}, "error", "headerMismatch"),
        ({"status": "failed"}, "error", "the job reported failure"),
        ({"rows": 4}, "ok", None),
        (types.SimpleNamespace(status="noop", detail="nothing due"), "skipped", "nothing due"),
        (types.SimpleNamespace(status="ok", error="boom"), "error", "boom"),
    ],
)
def test_whatever_a_job_returns_becomes_an_outcome(
    value: Any, status: str, detail: str | None
) -> None:
    assert normalise_result(value) == Outcome(status, detail)


# ------------------------------------------------------------------ switches and refusals


def _gates(
    env: dict[str, str] | None = None,
    *,
    synthetic: bool = False,
    el: ElStoreStatus | None = None,
) -> dict[str, tuple[bool, str | None]]:
    ctx = GateContext(
        env=env or {},
        nba_store_is_synthetic=lambda: synthetic,
        el_store=lambda: el or ElStoreStatus(True),
    )
    out = {}
    for job in build_jobs():
        verdict = job.gate(ctx)
        out[job.id] = (verdict.enabled, verdict.reason)
    return out


def test_with_no_settings_at_all_everything_is_on() -> None:
    gates = _gates()
    assert all(enabled for enabled, _ in gates.values()), {
        k: r for k, (e, r) in gates.items() if not e
    }


@pytest.mark.parametrize(
    ("setting", "off"),
    [
        ("HARDWOOD_EL_LIVE", {"el.round", "el.box", "el.structure", "el.rosters"}),
        ("HARDWOOD_NBA_INJURIES", {"nba.injuries"}),
        ("HARDWOOD_NEWS", {"nba.news", "el.news"}),
        (
            "HARDWOOD_EL_ENABLED",
            {
                "el.round", "el.box", "el.structure", "el.rosters", "el.workbook", "el.ratings",
                "el.news", "projections.lock@euroleague", "projections.refresh@euroleague",
                "projections.calibrate@euroleague",
            },
        ),
    ],
)
@pytest.mark.parametrize("value", ["off", "0", "false", "No"])
def test_each_switch_turns_exactly_its_source_off(setting: str, off: set[str], value: str) -> None:
    gates = _gates({setting: value})
    disabled = {job for job, (enabled, _) in gates.items() if not enabled}
    assert disabled == off
    for job in off:
        assert setting in (gates[job][1] or "")


def test_a_switch_that_is_not_on_or_off_fails_safe_and_says_so() -> None:
    gates = _gates({"HARDWOOD_NEWS": "maybe"})
    assert gates["nba.news"][0] is False
    assert "'maybe'" in (gates["nba.news"][1] or "")
    assert parse_switch(None, "X", True).on is True
    assert parse_switch("  ", "X", False).on is False
    assert parse_switch("ON", "X", False).on is True


def test_the_older_el_mode_setting_is_still_honoured() -> None:
    assert _gates({"HARDWOOD_EL_MODE": "off"})["el.ratings"][0] is False
    demo = _gates({"HARDWOOD_EL_MODE": "demo"})
    assert demo["el.round"][0] is False and demo["el.ratings"][0] is True
    assert _gates({"HARDWOOD_EL_MODE": "live"})["el.round"][0] is True


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:8000", "http://localhost:9000", "http://[::1]:8000", ""]
)
def test_a_private_address_runs_everything(url: str) -> None:
    env = {"HARDWOOD_PUBLIC_BASE_URL": url} if url else {}
    assert startup_refusals(env) == []
    assert all(enabled for enabled, _ in _gates(env).values())


@pytest.mark.parametrize(
    "url",
    [
        "https://hardwood.example.org",
        "http://192.168.1.20:8000",
        "http://0.0.0.0:8000",
        "not a url",
    ],
)
def test_a_public_address_refuses_every_job_that_brings_data_in(url: str) -> None:
    env = {"HARDWOOD_PUBLIC_BASE_URL": url}
    refusal = startup_refusals(env)
    assert len(refusal) == 1 and "LEGAL.md section 2b" in refusal[0]
    gates = _gates(env)
    refused = {job for job, (enabled, _) in gates.items() if not enabled}
    assert refused == set(worker.PRIVATE_ONLY_JOBS)
    for job in worker.PRIVATE_ONLY_JOBS:
        assert "loopback" in (gates[job][1] or "")
        assert job in refusal[0]
    # Work on data already stored is not fetching, so it is not refused.
    assert gates["nba.rosters"][0] and gates["projections.lock@nba"][0]
    assert gates["el.ratings"][0] and gates["projections.refresh@euroleague"][0]


def test_a_demo_league_never_gets_real_injuries_or_positions() -> None:
    gates = _gates(synthetic=True)
    for job in ("nba.injuries", "nba.rosters"):
        enabled, reason = gates[job]
        assert not enabled and "Demo league" in (reason or "")
    assert gates["projections.refresh@nba"][0] and gates["nba.news"][0]


def test_the_demo_check_reads_the_stores_own_rows(tmp_path, monkeypatch) -> None:
    url = f"sqlite:///{tmp_path / 'nba.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    stats_config.reset_settings_cache()
    db_module.dispose_engine()
    try:
        engine = db_module.get_engine()
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE games (game_id TEXT, data_source TEXT)"))
            connection.execute(text("INSERT INTO games VALUES ('1', 'nba_api')"))
        assert worker.nba_store_is_synthetic() is False
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO games VALUES ('2', 'synthetic-demo')"))
        assert worker.nba_store_is_synthetic() is True
    finally:
        db_module.dispose_engine()
        stats_config.reset_settings_cache()


def test_the_demo_check_is_unknown_not_true_when_the_store_cannot_be_read(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'empty.db'}")
    stats_config.reset_settings_cache()
    db_module.dispose_engine()
    try:
        assert worker.nba_store_is_synthetic() is None  # no games table at all
    finally:
        db_module.dispose_engine()
        stats_config.reset_settings_cache()


def test_the_euroleague_store_is_probed_never_created(tmp_path) -> None:
    nba = f"sqlite:///{tmp_path / 'hardwood.db'}"
    el_file = tmp_path / "hardwood_el.db"
    env = {"DATABASE_URL": nba, "HARDWOOD_EL_DATABASE_URL": f"sqlite:///{el_file}"}
    store = ElStore(lambda: env)
    try:
        missing = store.status()
        assert not missing.ready and "open the API once" in (missing.reason or "")
        assert not el_file.exists(), "asking about the store must not create it"

        el_file.touch()
        no_table = store.status()
        assert not no_table.ready and "el_job_state" in (no_table.reason or "")

        engine = create_engine(f"sqlite:///{el_file}")
        with engine.begin() as connection:
            connection.execute(text(STATE_DDL.format(table="el_job_state")))
        engine.dispose()
        assert store.status() == ElStoreStatus(True)

        same = ElStore(lambda: {"DATABASE_URL": nba, "HARDWOOD_EL_DATABASE_URL": nba})
        refused = same.status()
        assert not refused.ready and "same database" in (refused.reason or "")
        same.dispose()
    finally:
        store.dispose()


def test_the_euroleague_default_file_sits_beside_the_nba_one(tmp_path) -> None:
    env = {"DATABASE_URL": f"sqlite:///{tmp_path / 'hardwood.db'}"}
    assert worker.el_database_url(env) == f"sqlite:///{tmp_path / 'hardwood_el.db'}"
    postgres = {"DATABASE_URL": "postgresql://u:p@h/db", "HARDWOOD_DATA_DIR": str(tmp_path)}
    assert worker.el_database_url(postgres) == f"sqlite:///{tmp_path / 'hardwood_el.db'}"


# --------------------------------------------------------------------- the env file


def test_the_env_file_parses_exactly_as_the_api_loader_does(tmp_path, monkeypatch) -> None:
    body = textwrap.dedent(
        """\
        # a comment

        HARDWOOD_NEWS=off
          LOG_LEVEL = DEBUG
        QUOTED="two words"
        SINGLE='x'
        NOT_AN_ASSIGNMENT
        =novalue
        EMPTY=
        """
    )
    path = tmp_path / "h.env"
    path.write_text(body)
    parsed = parse_env_text(body)
    keys = [k for k in parsed]
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    load_dotenv(path)
    for key, value in parsed.items():
        assert os.environ.get(key) == value, key
    assert parsed["QUOTED"] == "two words" and "NOT_AN_ASSIGNMENT" not in parsed
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def test_the_env_file_is_reapplied_and_forgotten_but_the_real_environment_wins(tmp_path) -> None:
    environ = {"FROM_PLIST": "plist"}
    path = tmp_path / "h.env"
    sync = EnvFileSync(path, environ)
    path.write_text("A=1\nFROM_PLIST=file\n")
    assert sync.refresh() is True
    assert environ == {"FROM_PLIST": "plist", "A": "1"}
    assert sync.refresh() is False  # unchanged
    path.write_text("A=2\nB=3\n")
    sync.refresh()
    assert environ == {"FROM_PLIST": "plist", "A": "2", "B": "3"}
    path.write_text("B=3\n")  # the line is deleted: the value goes with it
    sync.refresh()
    assert environ == {"FROM_PLIST": "plist", "B": "3"}
    path.unlink()
    sync.refresh()
    assert environ == {"FROM_PLIST": "plist"}


def test_an_unreadable_env_file_keeps_the_last_good_values(tmp_path, monkeypatch) -> None:
    environ: dict[str, str] = {}
    path = tmp_path / "h.env"
    path.write_text("HARDWOOD_NEWS=off\n")
    sync = EnvFileSync(path, environ)
    sync.refresh()

    def unreadable(self: Path, *args: Any, **kwargs: Any) -> str:
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "read_text", unreadable)
    assert sync.refresh() is False
    assert environ == {"HARDWOOD_NEWS": "off"}


def test_editing_the_env_file_stops_a_source_at_the_next_tick(
    make_worker, tmp_path, monkeypatch, calls
) -> None:
    name = job_module(monkeypatch, "hw_killswitch", run=lambda: calls.append("news"))
    news = JobSpec(
        "nba.news", "nba", "NBA headlines", Every(60), worker.CallTarget(f"{name}:run"),
        worker.gate_news,
    )
    instance, clock = make_worker([news])
    env_file = tmp_path / "hardwood.env"

    env_file.write_text("HARDWOOD_NEWS=off\n")
    assert instance.tick() == [] and calls == []  # off from the start

    env_file.write_text("# HARDWOOD_NEWS=off\n")  # switched back on
    clock.advance(seconds=30)
    assert len(instance.tick()) == 1

    env_file.write_text("HARDWOOD_NEWS=off\n")  # and off again: no restart needed
    clock.advance(minutes=5)
    assert instance.tick() == []
    assert calls == ["news"]


def test_the_example_env_file_is_valid_and_documents_every_switch() -> None:
    raw = (MACOS / "hardwood.env.example").read_text(encoding="utf-8")
    for stale in ("TERMS_REVIEWED_ON", "TERMS_URL", "TERMS_OUTCOME"):
        assert stale not in raw, "there is no terms gate to record anything for"
    uncommented = []
    for line in raw.splitlines():
        match = re.match(r"#\s([A-Z][A-Z0-9_]*)=(.*)$", line)
        if match:
            uncommented.append(f"{match.group(1)}={match.group(2)}")
    parsed = parse_env_text("\n".join(uncommented))
    for name in (
        "HARDWOOD_EL_LIVE",
        "HARDWOOD_EL_ENABLED",
        "HARDWOOD_NBA_INJURIES",
        "HARDWOOD_NEWS",
        "HARDWOOD_WORKBOOK_PATH",
        "HARDWOOD_API_KEY",
        "HARDWOOD_PUBLIC_BASE_URL",
        "HARDWOOD_WORKER_TICK_SECONDS",
        "INGEST_POLL_SECONDS",
        "CORRECTION_WINDOW_DAYS",
        "LOG_LEVEL",
    ):
        assert name in parsed, f"{name} is not documented in hardwood.env.example"
    for name in (
        "HARDWOOD_EL_LIVE", "HARDWOOD_EL_ENABLED", "HARDWOOD_NBA_INJURIES", "HARDWOOD_NEWS"
    ):
        assert parsed[name] == "on", "every source is on by default"
    for key, value in parsed.items():
        assert " #" not in value, f"{key} has an inline comment, which the loader keeps as value"
    # Every assignment is a default switched off: the shipped file changes nothing by itself.
    live = [ln for ln in raw.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert live == []
    # What the plists set belongs to the plists, not to this file.
    for name in ("DATABASE_URL", "HARDWOOD_EL_DATABASE_URL", "HARDWOOD_DATA_DIR"):
        assert not re.search(rf"^#\s{name}=", raw, re.M)


# ------------------------------------------------------------------------ the inbox


def _open(job: JobSpec) -> JobSpec:
    """The same job with its gate opened, so a test exercises the job and not the switches."""
    return JobSpec(job.key, job.league, job.title, job.schedule, job.target, worker.gate_always)


def _inbox_worker(make_worker, tmp_path, monkeypatch, *, runner, **kwargs):
    monkeypatch.setattr(worker, "modules_available", lambda names: None)
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    job = next(j for j in build_jobs() if j.key == "el.workbook")
    open_gate = _open(job)
    instance, clock = make_worker([open_gate], runner=runner, **kwargs)
    return instance, clock, inbox


def _workbook(inbox: Path, name: str = "league.xlsx", age: float = 60) -> Path:
    path = inbox / name
    path.write_bytes(b"PK-not-a-real-workbook")
    old = time.time() - age
    os.utime(path, (old, old))
    return path


def test_a_workbook_dropped_in_the_inbox_is_imported_once(
    make_worker, tmp_path, monkeypatch
) -> None:
    seen: list[tuple[str, ...]] = []

    def runner(args, timeout, stop):
        seen.append(tuple(args))
        return worker.CommandResult(0, "imported")

    instance, clock, inbox = _inbox_worker(
        make_worker, tmp_path, monkeypatch, runner=runner, start=datetime.now(UTC)
    )
    path = _workbook(inbox)
    assert [r.outcome.status for r in instance.tick()] == ["ok"]
    assert seen == [("-m", "nbastats.euroleague.ingest", "import-workbook", str(path))]

    clock.advance(minutes=2)
    assert [r.outcome.status for r in instance.tick()] == ["skipped"]
    assert len(seen) == 1

    # An updated workbook under the same name is a change, and is imported again.
    path.write_bytes(b"PK-an-updated-workbook!")
    clock.advance(minutes=2)
    assert [r.outcome.status for r in instance.tick()] == ["ok"]
    assert len(seen) == 2


def test_a_file_still_being_copied_is_left_alone(make_worker, tmp_path, monkeypatch) -> None:
    seen: list[Any] = []
    instance, clock, inbox = _inbox_worker(
        make_worker, tmp_path, monkeypatch,
        runner=lambda *a: seen.append(a) or worker.CommandResult(0, ""),
        start=datetime.now(UTC),
    )
    _workbook(inbox, age=1)
    assert instance.tick()[0].outcome.status == "skipped" and seen == []
    clock.advance(seconds=70)  # the next scan, by which time the copy has settled
    assert instance.tick()[0].outcome.status == "ok" and len(seen) == 1


def test_only_real_workbooks_are_taken_from_the_inbox(make_worker, tmp_path, monkeypatch) -> None:
    seen: list[str] = []
    instance, _, inbox = _inbox_worker(
        make_worker, tmp_path, monkeypatch,
        runner=lambda args, t, s: seen.append(Path(args[-1]).name) or worker.CommandResult(0, ""),
        start=datetime.now(UTC),
    )
    for name in ("~$league.xlsx", ".hidden.xlsx", "notes.txt", "data.csv", "Round4.XLSX"):
        _workbook(inbox, name)
    instance.tick()
    assert seen == ["Round4.XLSX"]


def test_a_failed_import_is_reported_and_not_retried_in_a_loop(
    make_worker, tmp_path, monkeypatch
) -> None:
    attempts: list[int] = []

    def runner(args, timeout, stop):
        attempts.append(1)
        return worker.CommandResult(1, "header row not found\nunreadable workbook")

    instance, clock, inbox = _inbox_worker(
        make_worker, tmp_path, monkeypatch, runner=runner, start=datetime.now(UTC)
    )
    _workbook(inbox)
    outcome = instance.tick()[0].outcome
    assert outcome.status == "error" and "unreadable workbook" in (outcome.detail or "")
    for _ in range(3):
        clock.advance(minutes=2)
        assert instance.tick()[0].outcome.status == "skipped"
    assert len(attempts) == 1


def test_the_workbook_path_setting_is_imported_too(make_worker, tmp_path, monkeypatch) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    path = _workbook(elsewhere, "toolkit.xlsx")
    seen: list[str] = []
    instance, _, _ = _inbox_worker(
        make_worker, tmp_path, monkeypatch,
        runner=lambda args, t, s: seen.append(args[-1]) or worker.CommandResult(0, ""),
        environ={"HARDWOOD_WORKBOOK_PATH": str(path)},
        start=datetime.now(UTC),
    )
    instance.tick()
    assert seen == [str(path)]


def test_a_workbook_macos_will_not_let_a_background_program_read_is_explained(
    make_worker, tmp_path, monkeypatch
) -> None:
    instance, _, _ = _inbox_worker(
        make_worker, tmp_path, monkeypatch,
        runner=lambda *a: worker.CommandResult(0, ""), start=datetime.now(UTC),
        environ={"HARDWOOD_WORKBOOK_PATH": str(tmp_path / "Downloads" / "toolkit.xlsx")},
    )

    def refused(self: Path, *args: Any, **kwargs: Any) -> bool:
        if self.name == "toolkit.xlsx":
            raise PermissionError(1, "Operation not permitted")
        return False

    monkeypatch.setattr(Path, "is_file", refused)
    outcome = instance.tick()[0].outcome
    assert outcome.status == "error"
    assert "Operation not permitted" in (outcome.detail or "")
    assert "copy the file into the inbox" in (outcome.detail or "")


def test_a_busy_five_minute_job_does_not_fill_the_log(
    make_worker, monkeypatch, caplog
) -> None:
    name = job_module(
        monkeypatch, "hw_logs", quiet=lambda: None, chatty=lambda: {"summary": "3 rows"}
    )
    instance, _ = make_worker(
        [spec("projections.lock", f"{name}:quiet", Every(300)),
         spec("projections.refresh", f"{name}:chatty", Every(300))]
    )
    with caplog.at_level(logging.INFO, logger="nbastats.worker"):
        instance.tick()
    assert "job=projections.refresh" in caplog.text and "job=projections.lock" not in caplog.text


def test_the_inbox_job_is_not_installed_until_the_importer_exists(
    make_worker, tmp_path, monkeypatch
) -> None:
    job = next(j for j in build_jobs() if j.key == "el.workbook")
    monkeypatch.setattr(worker, "modules_available", lambda names: "nbastats.euroleague.ingest")
    instance, _ = make_worker(
        [_open(job)]
    )
    outcome = instance.tick()[0].outcome
    assert outcome.status == "notInstalled"
    assert "nbastats.euroleague.ingest" in (outcome.detail or "")


# ----------------------------------------------------------------------- subprocesses


def test_a_command_job_reports_its_exit_code_and_output() -> None:
    ok = worker.run_command(("-c", "print('rosters written: 30')"), 30)
    assert ok.returncode == 0 and "rosters written: 30" in ok.output and not ok.timed_out
    bad = worker.run_command(("-c", "import sys; print('no nba_api'); sys.exit(2)"), 30)
    assert bad.returncode == 2 and "no nba_api" in bad.output


def test_a_command_job_that_hangs_is_stopped() -> None:
    started = time.monotonic()
    result = worker.run_command(("-c", "import time; time.sleep(60)"), 1)
    assert result.timed_out and time.monotonic() - started < 15


def test_stopping_the_worker_stops_the_command_it_is_running() -> None:
    stop = StopSignal()
    threading.Timer(0.5, stop.request).start()
    started = time.monotonic()
    result = worker.run_command(("-c", "import time; time.sleep(60)"), 60, stop)
    assert result.stopped and time.monotonic() - started < 15


def test_the_roster_job_runs_the_runner_and_needs_its_module(
    make_worker, monkeypatch
) -> None:
    job = next(j for j in build_jobs() if j.key == "nba.rosters")
    assert job.target == worker.CommandTarget(
        ("-m", "nbastats.ingest.runner", "--rosters"), requires=("nbastats.ingest.rosters",)
    )
    seen: list[tuple[str, ...]] = []

    def runner(args, timeout, stop):
        seen.append(tuple(args))
        return worker.CommandResult(0, "30 teams")

    open_gate = _open(job)
    instance, clock = make_worker([open_gate], runner=runner, tz=NEW_YORK)

    monkeypatch.setattr(worker, "modules_available", lambda names: "nbastats.ingest.rosters")
    assert instance.tick()[0].outcome.status == "notInstalled" and seen == []

    # Once the module exists the failed attempt is retried after its back-off.
    monkeypatch.setattr(worker, "modules_available", lambda names: None)
    clock.advance(minutes=15)
    assert instance.tick()[0].outcome == Outcome("ok", "30 teams")
    assert seen == [("-m", "nbastats.ingest.runner", "--rosters")]


def test_a_command_that_exits_non_zero_is_an_error_with_its_last_lines(
    make_worker, monkeypatch
) -> None:
    job = next(j for j in build_jobs() if j.key == "nba.rosters")
    monkeypatch.setattr(worker, "modules_available", lambda names: None)
    instance, _ = make_worker(
        [_open(job)],
        runner=lambda args, t, s: worker.CommandResult(2, "Install nba_api\nor set NBA_API_PROXY"),
        tz=NEW_YORK,
    )
    outcome = instance.tick()[0].outcome
    assert outcome.status == "error" and "exit 2" in (outcome.detail or "")
    assert "NBA_API_PROXY" in (outcome.detail or "")


# ----------------------------------------------------------------- the loop and the CLI


def test_stopping_between_jobs_runs_no_further_job(make_worker, monkeypatch, calls) -> None:
    holder: dict[str, Worker] = {}

    def first() -> None:
        calls.append("first")
        holder["w"].stop.request()

    name = job_module(monkeypatch, "hw_stop", first=first, second=lambda: calls.append("second"))
    instance, _ = make_worker(
        [spec("nba.injuries", f"{name}:first"), spec("nba.news", f"{name}:second")]
    )
    holder["w"] = instance
    instance.tick()
    assert calls == ["first"]


def test_the_time_critical_jobs_come_before_the_fetches_in_priority_order() -> None:
    """When several jobs are due at once (first start, a wake from sleep) the freeze before
    tip-off must never queue behind a network fetch."""
    order = [job.key for job in build_jobs()]
    assert order[:2] == ["projections.lock", "projections.lock"]
    assert order.index("projections.refresh") < order.index("el.round")
    assert order.index("projections.refresh") < order.index("nba.injuries")
    assert order.index("nba.injuries") < order.index("nba.news")
    assert order.index("nba.rosters") > order.index("el.rosters"), "the weekly pass goes last"


def test_when_several_jobs_are_due_they_run_in_priority_order(
    make_worker, monkeypatch, calls
) -> None:
    name = job_module(
        monkeypatch, "hw_order",
        lock=lambda: calls.append("lock"), fetch=lambda: calls.append("fetch"),
    )
    instance, _ = make_worker(
        [spec("projections.lock", f"{name}:lock"), spec("nba.news", f"{name}:fetch")]
    )
    instance.tick()
    assert calls == ["lock", "fetch"]


def test_run_forever_ticks_until_it_is_stopped(
    make_worker, monkeypatch, tmp_path, calls
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'loop.db'}")
    stats_config.reset_settings_cache()
    db_module.dispose_engine()
    holder: dict[str, Worker] = {}

    def run() -> None:
        calls.append("tick")
        if len(calls) >= 3:
            holder["w"].stop.request()

    name = job_module(monkeypatch, "hw_loop", run=run)
    instance, clock = make_worker([spec("nba.news", f"{name}:run", Every(1))])
    instance.tick_seconds = 0.01
    holder["w"] = instance
    original = instance.tick

    def ticking() -> Any:
        clock.advance(seconds=5)
        return original()

    instance.tick = ticking  # type: ignore[method-assign]
    try:
        assert instance.run_forever() == 0
    finally:
        db_module.dispose_engine()
        stats_config.reset_settings_cache()
    assert calls == ["tick", "tick", "tick"]


def test_only_one_worker_may_hold_a_data_directory(tmp_path) -> None:
    lock = tmp_path / "worker.lock"
    first, second = SingleInstance(lock), SingleInstance(lock)
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()
    assert (tmp_path / "worker.lock").read_text().strip().isdigit()


def test_the_stop_signal_wakes_a_sleeping_loop_at_once() -> None:
    stop = StopSignal()
    threading.Timer(0.1, stop.request).start()
    started = time.monotonic()
    assert stop.wait(30) is True
    assert time.monotonic() - started < 5 and bool(stop)


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    for name in (
        "HARDWOOD_EL_LIVE", "HARDWOOD_EL_ENABLED", "HARDWOOD_NBA_INJURIES", "HARDWOOD_NEWS",
        "HARDWOOD_EL_MODE", "HARDWOOD_PUBLIC_BASE_URL", "HARDWOOD_WORKBOOK_PATH",
        "HARDWOOD_LOG_DIR", "HARDWOOD_EL_DATABASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HARDWOOD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HARDWOOD_ENV_FILE", str(tmp_path / "hardwood.env"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'hardwood.db'}")
    stats_config.reset_settings_cache()
    db_module.dispose_engine()
    yield tmp_path
    db_module.dispose_engine()
    stats_config.reset_settings_cache()


def test_a_command_typed_by_hand_adopts_the_installed_database(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(worker, "reset_settings_cache", lambda: None)
    env: dict[str, str] = {"HARDWOOD_DATA_DIR": str(tmp_path)}
    assert worker.adopt_installed_database(env) is None  # no database there: nothing to adopt
    assert "DATABASE_URL" not in env
    (tmp_path / "hardwood.db").write_bytes(b"")
    assert worker.adopt_installed_database(env) == f"sqlite:///{tmp_path / 'hardwood.db'}"
    assert env["DATABASE_URL"] == f"sqlite:///{tmp_path / 'hardwood.db'}"
    # An explicit setting always wins, so a developer's own database is never replaced.
    mine = {"HARDWOOD_DATA_DIR": str(tmp_path), "DATABASE_URL": "sqlite:///./mine.db"}
    assert worker.adopt_installed_database(mine) is None
    assert mine["DATABASE_URL"] == "sqlite:///./mine.db"


def test_list_names_every_job_and_creates_no_database(cli_env, capsys) -> None:
    assert worker.main(["--list"]) == 0
    out = capsys.readouterr().out
    for job in build_jobs():
        assert job.id in out
    assert "The EuroLeague store does not exist yet" in out
    assert not (cli_env / "hardwood.db").exists(), "--list must not create the NBA database"
    assert not (cli_env / "hardwood_el.db").exists()


def test_list_says_why_a_public_address_is_refused(cli_env, monkeypatch, capsys) -> None:
    (cli_env / "hardwood.env").write_text("HARDWOOD_PUBLIC_BASE_URL=https://hardwood.example.org\n")
    assert worker.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "REFUSED" in out and "not a loopback address" in out


def test_run_refuses_a_switched_off_source_and_an_unknown_name(
    cli_env, monkeypatch, capsys
) -> None:
    (cli_env / "hardwood.env").write_text("HARDWOOD_NEWS=off\n")
    assert worker.main(["--run", "nba.news"]) == 3
    assert "HARDWOOD_NEWS" in capsys.readouterr().err
    assert worker.main(["--run", "no.such.job"]) == 2


def test_run_one_job_now_honours_the_leagues_and_reports(
    cli_env, monkeypatch, capsys
) -> None:
    name = job_module(monkeypatch, "hw_cli_run", run=lambda: {"status": "ok", "summary": "3 rows"})
    monkeypatch.setattr(
        worker, "build_jobs",
        lambda: (spec("nba.news", f"{name}:run"), spec("projections.lock", f"{name}:run")),
    )
    assert worker.main(["--run", "nba.news"]) == 0
    assert "nba.news: ok (3 rows)" in capsys.readouterr().out


def test_once_runs_what_is_due_and_exits(cli_env, monkeypatch, capsys) -> None:
    name = job_module(monkeypatch, "hw_cli_once", run=lambda: None, bad=lambda: False)
    monkeypatch.setattr(
        worker,
        "build_jobs",
        lambda: (spec("nba.news", f"{name}:run"), spec("nba.injuries", f"{name}:bad")),
    )
    assert worker.main(["--once"]) == 1  # one job failed
    out = capsys.readouterr().out
    assert "nba.news: ok" in out and "nba.injuries: error" in out


def test_a_second_worker_exits_instead_of_running(cli_env, capsys) -> None:
    holder = SingleInstance(cli_env / "worker.lock")
    assert holder.acquire()
    try:
        assert worker.main(["--once"]) == 4
        assert "already running" in capsys.readouterr().err
    finally:
        holder.release()


# ------------------------------------------------------------- the module's own shape


def test_the_worker_names_every_job_the_design_lists() -> None:
    by_id = {job.id: job for job in build_jobs()}
    assert len(by_id) == len(build_jobs()), "job ids must be unique"
    keys = {job.key for job in build_jobs()}
    assert keys == {
        "nba.rosters", "nba.injuries", "nba.news", "projections.refresh", "projections.lock",
        "projections.calibrate", "el.structure", "el.round", "el.box", "el.rosters", "el.news",
        "el.ratings", "el.workbook",
    }
    for key in ("projections.refresh", "projections.lock", "projections.calibrate"):
        assert {j.league for j in build_jobs() if j.key == key} == {"nba", "euroleague"}, key
    assert by_id["nba.rosters"].schedule == WeeklyAt(0, 5, 30, max_age=timedelta(days=7))
    assert by_id["nba.news"].schedule == Every(3600) == by_id["el.news"].schedule
    assert by_id["projections.refresh@nba"].schedule == Every(1800)
    assert by_id["projections.lock@euroleague"].schedule == Every(300)
    assert by_id["projections.calibrate@nba"].schedule == DailyAt(5, 0)
    # The jobs whose real cadence depends on data are marked as deciding for themselves.
    assert {j.id for j in build_jobs() if j.decides} == {
        "nba.injuries", "el.round", "el.box", "el.rosters", "el.structure", "el.ratings"
    }


def test_job_targets_are_well_formed_strings() -> None:
    for job in build_jobs():
        target = job.target
        if isinstance(target, worker.CallTarget):
            module, _, attr = target.spec.partition(":")
            assert re.fullmatch(r"nbastats(\.[a-z_]+)+", module), target.spec
            assert attr.startswith("run_"), target.spec
        elif isinstance(target, worker.CommandTarget):
            assert target.args[0] == "-m" and target.args[1].startswith("nbastats.")


def test_every_job_the_private_deployment_rule_names_exists() -> None:
    """A typo in that list would silently exempt a job from the refusal."""
    ids = {job.id for job in build_jobs()}
    assert set(worker.PRIVATE_ONLY_JOBS) <= ids
    refusal = _gates({"HARDWOOD_PUBLIC_BASE_URL": "https://example.org"})
    assert {job for job, (enabled, _) in refusal.items() if not enabled} == set(
        worker.PRIVATE_ONLY_JOBS
    )


def test_the_worker_imports_no_feature_package_statically() -> None:
    """The EuroLeague is sealed behind an import boundary and the other packages land in any
    order, so every one of them is reached by string name. A static import would turn a
    missing package into a crash at startup instead of a ``notInstalled`` row."""
    tree = ast.parse((BACKEND / "nbastats" / "worker.py").read_text(encoding="utf-8"))
    forbidden = ("euroleague", "nba_intel", "nba_matchup", "intel", "shared")
    offences = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [("." * node.level) + (node.module or "")]
            names += [(("." * node.level) + (node.module or "") + "." + a.name) for a in node.names]
        else:
            continue
        for name in names:
            parts = name.replace("nbastats.", ".").lstrip(".").split(".")
            if parts[0] in forbidden or parts[:2] == ["ingest", "rosters"]:
                offences.append(name)
    assert offences == []
    assert "importlib.import_module" in (BACKEND / "nbastats" / "worker.py").read_text()


def test_the_worker_module_imports_without_the_other_packages() -> None:
    code = (
        "import sys\n"
        "import nbastats.worker\n"
        "bad = [m for m in sys.modules if m.startswith(('nbastats.euroleague', "
        "'nbastats.nba_intel', 'nbastats.nba_matchup', 'nbastats.intel'))]\n"
        "assert not bad, bad\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=BACKEND
    )
    assert result.returncode == 0, result.stderr


def test_ticking_opens_no_socket(make_worker, monkeypatch, calls) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the worker itself must never open a socket")

    name = job_module(monkeypatch, "hw_nonet", run=lambda: calls.append("run"))
    instance, clock = make_worker([spec("nba.news", f"{name}:run", Every(60))])
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    assert len(instance.tick()) == 1
    clock.advance(minutes=2)
    assert len(instance.tick()) == 1


def test_the_module_docstring_explains_why_not_just_what() -> None:
    doc = worker.__doc__ or ""
    assert len(doc.split()) > 700
    for phrase in ("Why one process", "Catching up after sleep", "kill switches", "cursor_json"):
        assert phrase.lower() in doc.lower().replace("\n", " "), phrase


# ----------------------------------------------------------------------- launchd files

PLISTS = {
    "com.hardwood.api": MACOS / "com.hardwood.api.plist",
    "com.hardwood.nba-watch": MACOS / "com.hardwood.nba-watch.plist",
    "com.hardwood.nba-nightly": MACOS / "com.hardwood.nba-nightly.plist",
    "com.hardwood.worker": MACOS / "com.hardwood.worker.plist",
}
KNOWN_PLACEHOLDERS = {
    "__PYTHON__", "__BACKEND_DIR__", "__DATA_DIR__", "__LOG_DIR__", "__ENV_FILE__", "__PORT__",
    "__DB_URL__", "__EL_DB_URL__",
}


def _plist(label: str) -> dict[str, Any]:
    import plistlib

    return plistlib.loads(PLISTS[label].read_bytes())


def test_the_four_templates_are_valid_and_use_only_known_placeholders() -> None:
    for label, path in PLISTS.items():
        data = _plist(label)
        assert data["Label"] == label == path.stem
        used = set(re.findall(r"__[A-Z][A-Z_]*__", path.read_text(encoding="utf-8")))
        assert used <= KNOWN_PLACEHOLDERS, used - KNOWN_PLACEHOLDERS
        assert data["ProgramArguments"][0] == "__PYTHON__"
        assert data["WorkingDirectory"] == "__BACKEND_DIR__"
        assert data["StandardOutPath"].startswith("__LOG_DIR__/")
        assert data["StandardErrorPath"].startswith("__LOG_DIR__/")
        env = data["EnvironmentVariables"]
        for needed in ("HARDWOOD_DATA_DIR", "HARDWOOD_ENV_FILE", "DATABASE_URL",
                       "HARDWOOD_EL_DATABASE_URL", "HARDWOOD_LOG_DIR"):
            assert needed in env, f"{label} sets no {needed}"
        # Switches belong in hardwood.env, where the worker re-reads them; a value set here
        # would beat the file and make a kill switch silently do nothing.
        for switch in ("HARDWOOD_EL_LIVE", "HARDWOOD_EL_ENABLED", "HARDWOOD_NBA_INJURIES",
                       "HARDWOOD_NEWS", "HARDWOOD_PUBLIC_BASE_URL"):
            assert switch not in env, f"{label} sets {switch}, which would beat hardwood.env"


def test_the_api_listens_on_loopback_only_and_restarts_itself() -> None:
    data = _plist("com.hardwood.api")
    args = data["ProgramArguments"]
    assert args[1:4] == ["-m", "uvicorn", "nbastats.api.app:app"]
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert "0.0.0.0" not in args and "::" not in args
    assert args[args.index("--port") + 1] == "__PORT__"
    assert data["KeepAlive"] is True and data["RunAtLoad"] is True


def test_the_worker_and_the_watch_loop_are_kept_alive_and_the_nightly_is_a_calendar_job() -> None:
    worker_plist = _plist("com.hardwood.worker")
    assert worker_plist["ProgramArguments"][1:] == ["-m", "nbastats.worker"]
    assert worker_plist["KeepAlive"] is True and worker_plist["ExitTimeOut"] >= 20
    watch = _plist("com.hardwood.nba-watch")
    assert watch["KeepAlive"] is True and watch["ThrottleInterval"] >= 30
    nightly = _plist("com.hardwood.nba-nightly")
    assert nightly["StartCalendarInterval"] == {"Hour": 6, "Minute": 10}
    assert "KeepAlive" not in nightly and nightly.get("RunAtLoad") is False


def test_the_runner_agents_read_hardwood_env_before_they_start() -> None:
    for label, flag in (
        ("com.hardwood.nba-watch", "--watch"),
        ("com.hardwood.nba-nightly", "--nightly"),
    ):
        args = _plist(label)["ProgramArguments"]
        assert args[1] == "-c"
        code = args[2]
        compile(code, label, "exec")  # a syntax error here would fail at 06:10, silently
        assert f'main(["{flag}"])' in code
        assert code.index("load_env_file()") < code.index("from nbastats.ingest.runner")


# --------------------------------------------------------------------- the installer


def _bash(*args: str, env: dict[str, str] | None = None, cwd: Path | None = None):
    base = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/root")}
    base.update(env or {})
    return subprocess.run(
        ["bash", *args], capture_output=True, text=True, env=base, cwd=cwd, timeout=180
    )


needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")


@needs_bash
@pytest.mark.parametrize("script", ["install.sh", "uninstall.sh"])
def test_the_scripts_parse_and_show_help(script: str) -> None:
    assert _bash("-n", str(MACOS / script)).returncode == 0
    helped = _bash(str(MACOS / script), "--help")
    assert helped.returncode == 0 and "Usage:" in helped.stdout
    assert os.access(MACOS / script, os.X_OK)


def test_the_scripts_avoid_what_macos_bash_3_2_cannot_run() -> None:
    for script in ("install.sh", "uninstall.sh"):
        body = (MACOS / script).read_text(encoding="utf-8")
        code = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
        for banned in ("declare -A", "mapfile", "readarray", ",,}", "^^}", "&>>", "|&", "[[ -v"):
            assert banned not in code, f"{script} uses {banned!r}, which bash 3.2 lacks"
        assert "sed -i" not in code, "BSD sed needs a different -i syntax"
        assert "readlink -f" not in code, "readlink -f is not on every macOS"


@needs_bash
def test_a_dry_run_changes_nothing(tmp_path) -> None:
    result = _bash(
        str(MACOS / "install.sh"), "--dry-run", "--python", sys.executable,
        env={"HARDWOOD_INSTALL_HOME": str(tmp_path), "HARDWOOD_INSTALL_ALLOW_NON_MAC": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert "Dry run" in result.stdout and "127.0.0.1:8000" in result.stdout
    assert list(tmp_path.iterdir()) == []


def _installed(tmp_path: Path, *extra: str):
    return _bash(
        str(MACOS / "install.sh"), "--no-load", "--skip-pip", "--python", sys.executable, *extra,
        env={"HARDWOOD_INSTALL_HOME": str(tmp_path), "HARDWOOD_INSTALL_ALLOW_NON_MAC": "1"},
    )


@needs_bash
def test_install_writes_real_plists_a_settings_file_and_the_folders(tmp_path) -> None:
    pytest.importorskip("ensurepip")
    result = _installed(tmp_path, "--port", "8123")
    assert result.returncode == 0, result.stdout + result.stderr
    data = tmp_path / "Library" / "Application Support" / "Hardwood"
    agents = tmp_path / "Library" / "LaunchAgents"
    for folder in ("raw", "el-raw", "recordings", "inbox"):
        assert (data / folder).is_dir()
    assert (tmp_path / "Library" / "Logs" / "Hardwood").is_dir()
    assert oct(data.stat().st_mode & 0o777) == "0o700"
    env_file = data / "hardwood.env"
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"
    assert "HARDWOOD_PUBLIC_BASE_URL=http://127.0.0.1:8123" in env_file.read_text()

    import plistlib

    for label in PLISTS:
        path = agents / f"{label}.plist"
        raw = path.read_text(encoding="utf-8")
        assert not re.search(r"__[A-Z][A-Z_]*__", raw), f"{label} still has a placeholder"
        loaded = plistlib.loads(raw.encode())
        assert Path(loaded["ProgramArguments"][0]).is_absolute()
        assert loaded["ProgramArguments"][0] == str(data / "venv" / "bin" / "python")
        assert loaded["WorkingDirectory"] == str(BACKEND)
        env = loaded["EnvironmentVariables"]
        assert env["HARDWOOD_DATA_DIR"] == str(data)
        assert env["HARDWOOD_ENV_FILE"] == str(env_file)
        assert env["DATABASE_URL"] == f"sqlite:///{data / 'hardwood.db'}"
        assert env["HARDWOOD_EL_DATABASE_URL"] == f"sqlite:///{data / 'hardwood_el.db'}"
        assert oct(path.stat().st_mode & 0o777) == "0o644"
    wrapper = data / "hardwood-python"
    assert os.access(wrapper, os.X_OK)
    shown = subprocess.run(
        [str(wrapper), "-c",
         "import os; print(os.environ['DATABASE_URL']); print(os.environ['HARDWOOD_ENV_FILE'])"],
        capture_output=True, text=True, env={"PATH": os.environ["PATH"]}, timeout=60,
    )
    assert shown.returncode == 0, shown.stderr
    assert shown.stdout.split("\n")[:2] == [f"sqlite:///{data / 'hardwood.db'}", str(env_file)]
    api = plistlib.loads((agents / "com.hardwood.api.plist").read_bytes())
    assert api["ProgramArguments"][-2:] == ["--port", "8123"]
    assert api["ProgramArguments"][api["ProgramArguments"].index("--host") + 1] == "127.0.0.1"


@needs_bash
def test_running_the_installer_again_keeps_the_users_settings(tmp_path) -> None:
    pytest.importorskip("ensurepip")
    assert _installed(tmp_path).returncode == 0
    env_file = tmp_path / "Library" / "Application Support" / "Hardwood" / "hardwood.env"
    env_file.write_text("HARDWOOD_NEWS=off\n")
    again = _installed(tmp_path)
    assert again.returncode == 0, again.stderr
    assert env_file.read_text() == "HARDWOOD_NEWS=off\n"
    assert "Keeping your existing settings file" in again.stdout


@needs_bash
def test_a_path_with_a_space_and_an_ampersand_survives_into_the_plist(tmp_path) -> None:
    pytest.importorskip("ensurepip")
    import plistlib

    data = tmp_path / "Hard & Wood data"
    result = _bash(
        str(MACOS / "install.sh"), "--no-load", "--skip-pip", "--python", sys.executable,
        "--data-dir", str(data),
        env={"HARDWOOD_INSTALL_HOME": str(tmp_path), "HARDWOOD_INSTALL_ALLOW_NON_MAC": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    loaded = plistlib.loads(
        (tmp_path / "Library" / "LaunchAgents" / "com.hardwood.worker.plist").read_bytes()
    )
    assert loaded["EnvironmentVariables"]["HARDWOOD_DATA_DIR"] == str(data)


@needs_bash
def test_the_installer_refuses_a_python_that_is_too_old(tmp_path) -> None:
    old = tmp_path / "old-python"
    old.write_text("#!/bin/sh\nexit 1\n")
    old.chmod(0o755)
    result = _bash(
        str(MACOS / "install.sh"), "--python", str(old),
        env={
            "HARDWOOD_INSTALL_HOME": str(tmp_path / "home"),
            "HARDWOOD_INSTALL_ALLOW_NON_MAC": "1",
        },
    )
    assert result.returncode != 0
    assert "3.11" in result.stderr and "old-python" in result.stderr
    assert not (tmp_path / "home").exists()
    body = (MACOS / "install.sh").read_text(encoding="utf-8")
    # The search order and the instructions the design asks for.
    assert body.index("3.13 3.12 3.11") > 0
    assert "brew install python@3.12" in body and "macOS" in body


@needs_bash
def test_the_installer_refuses_a_folder_macos_hides_from_background_programs(tmp_path) -> None:
    home = tmp_path / "home"
    tree = home / "Documents" / "Hardwood" / "backend"
    shutil.copytree(MACOS, tree / "scripts" / "macos")
    (tree / "pyproject.toml").write_text("[project]\nname='x'\n")
    result = _bash(
        str(tree / "scripts" / "macos" / "install.sh"), "--no-load", "--skip-pip",
        "--python", sys.executable,
        env={"HARDWOOD_INSTALL_HOME": str(home), "HARDWOOD_INSTALL_ALLOW_NON_MAC": "1"},
    )
    assert result.returncode != 0
    assert "Documents" in result.stderr and "Move the whole Hardwood folder" in result.stderr
    assert not (home / "Library").exists()


@needs_bash
def test_the_installer_will_not_run_outside_macos_unless_told(tmp_path) -> None:
    if sys.platform == "darwin":
        pytest.skip("this is the Mac")
    result = _bash(
        str(MACOS / "install.sh"), "--dry-run", "--python", sys.executable,
        env={"HARDWOOD_INSTALL_HOME": str(tmp_path)},
    )
    assert result.returncode != 0 and "macOS" in result.stderr


@needs_bash
def test_uninstall_removes_the_agents_and_keeps_the_data(tmp_path) -> None:
    pytest.importorskip("ensurepip")
    env = {
        "HARDWOOD_INSTALL_HOME": str(tmp_path),
        "HARDWOOD_INSTALL_ALLOW_NON_MAC": "1",
        "HARDWOOD_UNINSTALL_NO_LAUNCHCTL": "1",
    }
    assert _installed(tmp_path).returncode == 0
    data = tmp_path / "Library" / "Application Support" / "Hardwood"
    (data / "hardwood.db").write_text("precious")
    result = _bash(str(MACOS / "uninstall.sh"), env=env)
    assert result.returncode == 0, result.stderr
    assert list((tmp_path / "Library" / "LaunchAgents").iterdir()) == []
    assert (data / "hardwood.db").read_text() == "precious" and (data / "hardwood.env").exists()
    assert "Your data was kept" in result.stdout


@needs_bash
def test_purging_data_asks_first_and_refuses_anything_that_is_not_a_data_folder(tmp_path) -> None:
    pytest.importorskip("ensurepip")
    env = {
        "HARDWOOD_INSTALL_HOME": str(tmp_path),
        "HARDWOOD_INSTALL_ALLOW_NON_MAC": "1",
        "HARDWOOD_UNINSTALL_NO_LAUNCHCTL": "1",
    }
    assert _installed(tmp_path).returncode == 0
    data = tmp_path / "Library" / "Application Support" / "Hardwood"

    declined = subprocess.run(
        ["bash", str(MACOS / "uninstall.sh"), "--purge-data"], input="nope\n",
        capture_output=True, text=True,
        env={"PATH": os.environ["PATH"], "HOME": "/root", **env},
    )
    assert declined.returncode != 0 and "Not confirmed" in declined.stderr and data.exists()

    elsewhere = tmp_path / "not-hardwood"
    elsewhere.mkdir()
    (elsewhere / "photos.txt").write_text("mine")
    for target in (elsewhere, tmp_path):
        refused = _bash(
            str(MACOS / "uninstall.sh"), "--purge-data", "--yes", "--data-dir", str(target), env=env
        )
        assert refused.returncode != 0
        assert "does not look like a Hardwood data folder" in refused.stderr
    assert (elsewhere / "photos.txt").exists() and data.exists()

    confirmed = _bash(str(MACOS / "uninstall.sh"), "--purge-data", "--yes", env=env)
    assert confirmed.returncode == 0 and not data.exists()


# ------------------------------------------------------------------------------- docs

DOC_TEXT = {
    name: (DOCS / f"{name}.md").read_text(encoding="utf-8")
    for name in ("RUNBOOK", "EUROLEAGUE", "DATA_SOURCES", "PROJECTION", "ARCHITECTURE")
    if (DOCS / f"{name}.md").exists()
}


def test_the_runbook_names_every_job_and_every_switch() -> None:
    runbook = DOC_TEXT["RUNBOOK"]
    for job in build_jobs():
        assert job.key in runbook, f"RUNBOOK.md does not mention {job.key}"
    for name in (
        "HARDWOOD_EL_LIVE", "HARDWOOD_EL_ENABLED", "HARDWOOD_NBA_INJURIES", "HARDWOOD_NEWS",
        "HARDWOOD_WORKBOOK_PATH", "HARDWOOD_WORKER_TICK_SECONDS", "hardwood.env",
        "install.sh", "uninstall.sh", "inbox", "launchctl", "hardwood-python",
    ):
        assert name in runbook, f"RUNBOOK.md does not mention {name}"


def test_the_docs_describe_no_terms_gate_and_do_not_blame_the_user_for_a_design_choice() -> None:
    for name, body in DOC_TEXT.items():
        for stale in ("TERMS_REVIEWED_ON", "TERMS_OUTCOME", "termsNotReviewed", "terms gate"):
            assert stale not in body, f"docs/{name}.md still mentions {stale}"
        for paragraph in body.split("\n\n"):
            if "win probability" in paragraph.lower():
                lowered = paragraph.lower()
                assert "you decided" not in lowered and "user decision" not in lowered, (
                    f"docs/{name}.md attributes the win-probability omission to the user"
                )


def test_the_data_sources_doc_covers_each_source_and_what_could_not_be_verified() -> None:
    body = DOC_TEXT["DATA_SOURCES"]
    for needle in (
        "api-live.euroleague.net", "ak-static.cms.nba.com", "CommonTeamRoster",
        "eurohoops.net/feed", "talkbasket.net/feed", "robots.txt", "workbook",
    ):
        assert needle in body, f"DATA_SOURCES.md does not cover {needle}"
    assert "could not be" in body and "development environment" in body
