"""The ingest process loop: the thing that makes stats appear as each game finishes.

These tests drive :mod:`nbastats.ingest.runner` against the same recorded corpus as
``test_ingest.py`` — one slate, ``2026-01-02``, with a game that is live on the first poll
and Final on the second. Nothing here opens a socket or sleeps for real: the loop takes its
clock and its sleeper as parameters precisely so this is testable.

The behaviour under test is the product promise: a reader who opens the app minutes after the
buzzer sees that game, and does not have to wait for the rest of the slate or for a nightly
job.
"""
from __future__ import annotations

import json
import shutil
import socket
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from nbastats.db import read_sync_state
from nbastats.ingest import runner
from nbastats.ingest.client import StatsClient, UpstreamUnavailable
from nbastats.models import Game, PlayerGameBasic

FIXTURES = Path(__file__).parent / "fixtures" / "nba_api"

SLATE_DATE = date(2026, 1, 2)
LIVE_GAME = "0022500513"

# 20:00 UTC is 15:00 US Eastern — inside the game window. 09:00 UTC is 04:00 ET, outside it.
DURING_GAMES = datetime(2026, 1, 2, 20, 0, tzinfo=timezone.utc)
OVERNIGHT = datetime(2026, 1, 3, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """A socket opened anywhere in this module is a test failure, not a slow test."""

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the runner tests must never open a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


class Corpus:
    """A writable copy of the recorded payloads, so a game can flip to Final mid-test."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def apply(self, suffix: str) -> None:
        applied = 0
        for source in sorted(FIXTURES.glob(f"*__{suffix}.json")):
            shutil.copyfile(source, self.path / source.name.replace(f"__{suffix}", ""))
            applied += 1
        assert applied, f"no recorded payloads carry the {suffix!r} variant"

    def payload(self, name: str) -> dict[str, Any]:
        return json.loads((self.path / name).read_text(encoding="utf-8"))


@pytest.fixture()
def corpus(tmp_path: Path) -> Corpus:
    workspace = tmp_path / "nba_api"
    workspace.mkdir()
    for source in FIXTURES.glob("*.json"):
        if source.stem.endswith(("__corrected", "__complete")):
            continue
        shutil.copyfile(source, workspace / source.name)
    return Corpus(workspace)


@pytest.fixture()
def client(corpus: Corpus) -> StatsClient:
    return StatsClient(fixtures_dir=corpus.path, min_delay=0.0, sleeper=_never_sleep)


@pytest.fixture()
def db(empty_engine: Engine) -> Iterator[Session]:
    session = Session(empty_engine, future=True)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _never_sleep(seconds: float) -> None:
    raise AssertionError(f"fixture mode slept {seconds}s; it must not pace")


class FakeClock:
    """A clock the loop cannot outrun, so a bounded test stays bounded."""

    def __init__(self, start: datetime, step: timedelta = timedelta(minutes=5)) -> None:
        self.now = start
        self.step = step

    def __call__(self) -> datetime:
        value = self.now
        self.now = self.now + self.step
        return value


# --- the game window ---------------------------------------------------------------------


def test_the_game_window_covers_evening_games_and_their_west_coast_tail() -> None:
    assert runner.in_game_window(DURING_GAMES) is True
    # 05:00 UTC is midnight ET: a west-coast game can still be running.
    assert runner.in_game_window(datetime(2026, 1, 3, 5, 0, tzinfo=timezone.utc)) is True
    # 14:00 UTC is 09:00 ET: nothing is being played.
    assert runner.in_game_window(datetime(2026, 1, 2, 14, 0, tzinfo=timezone.utc)) is False


def test_the_game_window_wraps_midnight_rather_than_ending_at_it() -> None:
    """A naive range check would call 01:00 ET 'outside', abandoning the late slate."""
    one_am_eastern = datetime(2026, 1, 3, 6, 0, tzinfo=timezone.utc)
    assert runner.in_game_window(one_am_eastern) is True


def test_the_scheduling_date_is_eastern_not_utc() -> None:
    """A 23:00 ET tip is already 'tomorrow' in UTC; the slate it belongs to is not."""
    late_tip = datetime(2026, 1, 3, 4, 0, tzinfo=timezone.utc)  # 23:00 ET on the 2nd
    assert runner.eastern_today(late_tip) == date(2026, 1, 2)


def test_a_naive_datetime_is_treated_as_utc_rather_than_crashing() -> None:
    assert runner.eastern_today(datetime(2026, 1, 2, 20, 0)) == date(2026, 1, 2)


# --- the watch loop ----------------------------------------------------------------------


def test_a_game_that_goes_final_is_ingested_on_the_next_poll(
    db: Session, client: StatsClient, corpus: Corpus
) -> None:
    """The product promise, end to end: buzzer, one poll, the box score is in the database."""
    report = runner.watch(
        client=client, session=db, poll_seconds=1, max_polls=1,
        sleeper=lambda _: None, clock=FakeClock(DURING_GAMES),
    )
    assert report.polls == 1
    first_pass = report.games_ingested
    assert first_pass >= 1, "the already-final game should land on the first poll"

    before = read_sync_state(db).sync_version

    corpus.apply("complete")  # the live game is now Final, as the next poll would see it
    second = runner.watch(
        client=client, session=db, poll_seconds=1, max_polls=1,
        sleeper=lambda _: None, clock=FakeClock(DURING_GAMES),
    )

    assert second.games_ingested >= 1, "the newly finished game must be picked up"
    assert read_sync_state(db).sync_version > before

    stored = db.execute(
        select(Game.status).where(Game.game_id == LIVE_GAME)
    ).scalar_one_or_none()
    assert stored == "final"

    lines = db.execute(
        select(func.count()).select_from(PlayerGameBasic)
        .where(PlayerGameBasic.game_id == LIVE_GAME)
    ).scalar_one()
    assert lines > 0, "a finalized game must bring its player box score with it"


def test_a_second_poll_over_an_unchanged_slate_ingests_nothing_new(
    db: Session, client: StatsClient
) -> None:
    """Polling is cheap only if it is idempotent; a re-poll must not re-bump the version."""
    runner.watch(client=client, session=db, max_polls=1,
                 sleeper=lambda _: None, clock=FakeClock(DURING_GAMES))
    settled = read_sync_state(db).sync_version

    again = runner.watch(client=client, session=db, max_polls=1,
                         sleeper=lambda _: None, clock=FakeClock(DURING_GAMES))

    assert again.games_ingested == 0
    assert read_sync_state(db).sync_version == settled


def test_the_loop_paces_itself_with_the_configured_interval(
    db: Session, client: StatsClient
) -> None:
    slept: list[float] = []
    runner.watch(client=client, session=db, poll_seconds=45, max_polls=2,
                 sleeper=slept.append, clock=FakeClock(DURING_GAMES))
    assert slept, "the loop must pace between polls"
    assert slept[0] == pytest.approx(45.0)


def test_outside_the_game_window_the_loop_idles_instead_of_polling(
    db: Session, client: StatsClient
) -> None:
    """Overnight the loop should not burn requests against a league that is asleep."""
    slept: list[float] = []
    report = runner.watch(
        client=client, session=db, poll_seconds=30, idle_seconds=600, max_polls=2,
        sleeper=slept.append, clock=FakeClock(OVERNIGHT, step=timedelta(0)),
    )
    assert report.games_ingested == 0
    assert report.dates_seen == [], "no slate should be polled outside the window"
    # Two passes, one sleep: the loop deliberately does not pause on its way out.
    assert slept == [600.0], "it should wait the idle interval, not the poll interval"


def test_shutdown_stops_the_loop_without_tearing_up_a_game(
    db: Session, client: StatsClient
) -> None:
    flag = runner.ShutdownFlag()
    flag.request()
    report = runner.watch(client=client, session=db, shutdown=flag,
                          sleeper=_never_sleep, clock=FakeClock(DURING_GAMES))
    assert report.polls == 0
    assert bool(flag) is True


def test_an_upstream_failure_is_recorded_and_the_loop_keeps_going(
    db: Session, monkeypatch: pytest.MonkeyPatch, client: StatsClient
) -> None:
    """A blocked datacenter IP must degrade to a logged error, not a dead process."""

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise UpstreamUnavailable("stats.nba.com did not answer")

    monkeypatch.setattr(runner.daily, "poll_finalized_games", unavailable)
    report = runner.watch(client=client, session=db, max_polls=2,
                          sleeper=lambda _: None, clock=FakeClock(DURING_GAMES))

    assert report.errors >= 1
    assert report.polls == 2, "the loop must survive the failure and poll again"


def test_one_failing_game_does_not_abandon_the_rest_of_the_slate(
    db: Session, monkeypatch: pytest.MonkeyPatch, client: StatsClient, corpus: Corpus
) -> None:
    corpus.apply("complete")  # both games final, so there is a rest of the slate to abandon
    calls: list[str] = []
    real = runner.daily.ingest_game

    def flaky(session: Session, game_id: str, **kwargs: Any) -> Any:
        calls.append(game_id)
        if len(calls) == 1:
            raise RuntimeError("a malformed box score")
        return real(session, game_id, **kwargs)

    monkeypatch.setattr(runner.daily, "ingest_game", flaky)
    report = runner.watch(client=client, session=db, max_polls=1,
                          sleeper=lambda _: None, clock=FakeClock(DURING_GAMES))

    assert len(calls) >= 2, "the second game must still be attempted"
    assert report.errors == 1


# --- the one-shot and nightly modes ------------------------------------------------------


def test_run_once_pulls_a_whole_day_in_bulk(db: Session, client: StatsClient) -> None:
    result = runner.run_once(SLATE_DATE, client=client, session=db)
    assert result.games >= 1
    assert result.player_rows > 0


def test_run_nightly_applies_a_stat_correction(
    db: Session, client: StatsClient, corpus: Corpus
) -> None:
    """The league revises box scores after the fact; the window exists to catch that."""
    runner.run_once(SLATE_DATE, client=client, session=db)

    corpus.apply("corrected")
    results = runner.run_nightly(days=1, end_date=SLATE_DATE, client=client, session=db)

    assert results, "the correction window must cover at least the requested day"
    assert sum(r.changed_rows for r in results) > 0, "the corrected line should be rewritten"


def test_the_correction_window_defaults_to_the_configured_span(
    db: Session, client: StatsClient
) -> None:
    from nbastats.config import get_settings

    results = runner.run_nightly(end_date=SLATE_DATE, client=client, session=db)
    assert len(results) == get_settings().correction_window_days


# --- the command line --------------------------------------------------------------------


def test_the_cli_requires_exactly_one_mode() -> None:
    with pytest.raises(SystemExit):
        runner.build_parser().parse_args([])
    with pytest.raises(SystemExit):
        runner.build_parser().parse_args(["--watch", "--nightly"])


def test_the_cli_parses_every_documented_invocation() -> None:
    parser = runner.build_parser()
    assert parser.parse_args(["--watch"]).watch is True
    assert parser.parse_args(["--once", "--date", "2026-01-02"]).date == SLATE_DATE
    assert parser.parse_args(["--nightly", "--days", "5"]).days == 5
    assert parser.parse_args(["--backfill-kaggle", "/data/nba.sqlite"]).backfill_kaggle
    assert parser.parse_args(["--backfill-bbref", "/data/bbref"]).backfill_bbref
    assert parser.parse_args(["--reconcile-ids"]).reconcile_ids is True


def test_the_cli_rejects_a_malformed_date() -> None:
    with pytest.raises(SystemExit):
        runner.build_parser().parse_args(["--once", "--date", "02-01-2026"])
