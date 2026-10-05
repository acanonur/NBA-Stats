"""``--refetch-games``: the per-game box-score path, run again over a date range (BD-15).

Why this exists
---------------
``player_game_basic.started`` is recorded by one source only, the per-game ``BoxScoreTraditionalV3``
(it marks a starter by giving him a position). The bulk ``PlayerGameLogs`` rows that ``--once`` and
``--nightly`` use have no starter column. Two things follow, and both leave a database with starters
missing that the bulk path can never put back:

* the **old ingest bug** wrote ``False`` for "this source does not know", so the first nightly pass
  flattened every recorded starter and ``games_started`` fell to zero for everyone; the fix stopped
  the flattening but (rightly) does not rewrite history;
* a database **filled by a bulk season walk** (``--nightly --days 200 --date ...``) never had them.

``--refetch-games --days N --date D`` is the repair: ``ingest_game`` over the stored final games of
those days. These tests prove it restores starters in both situations, from a fake client that
serves the committed recorded payloads and counts every call; that it makes no call the repair does
not need; that a game that cannot be fetched is counted and does not stop the rest; that it is safe
to interrupt and to repeat (the second run changes nothing and moves no version); that it refuses
the seeded demo league; and that the command line, the exit codes and the runbook say what they do.

Nothing here opens a socket; a socket is an immediate failure.
"""
from __future__ import annotations

import shutil
import socket
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import Engine, select, text, update
from sqlalchemy.orm import Session

from nbastats import config
from nbastats.db import read_sync_state
from nbastats.ingest import aggregate, daily, runner
from nbastats.ingest.client import StatsClient, UpstreamUnavailable
from nbastats.models import Game, IngestLog, PlayerGameBasic, PlayerSeason

FIXTURES = Path(__file__).parent / "fixtures" / "nba_api"
ROOT = Path(__file__).resolve().parents[2]

SLATE_DATE = date(2026, 1, 2)
SEASON = "2025-26"
FIRST_GAME = "0022500512"  # final on the first poll, ten starters recorded in its box score
SECOND_GAME = "0022500513"  # live on the first poll; final once the "complete" files apply


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("these tests must never open a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


class FakeClient:
    """Serves the recorded box scores and records every call.

    Only the two per-game box-score methods exist: a repair that asked for anything else (the
    scoreboard, a bulk game log) would fail loudly through ``__getattr__``, which is what proves
    it uses nothing but the per-game path.
    """

    def __init__(self, inner: StatsClient, *, fail: tuple[str, ...] = ()) -> None:
        self._inner = inner
        self.fail = set(fail)
        self.calls: list[tuple[str, str]] = []

    def box_score_traditional(self, game_id: str) -> Any:
        self.calls.append(("traditional", game_id))
        if game_id in self.fail:
            raise UpstreamUnavailable(f"the league did not answer for {game_id}")
        return self._inner.box_score_traditional(game_id)

    def box_score_advanced(self, game_id: str) -> Any:
        self.calls.append(("advanced", game_id))
        return self._inner.box_score_advanced(game_id)

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"--refetch-games must not call client.{name}")


@pytest.fixture()
def corpus(tmp_path: Path) -> StatsClient:
    """A writable copy of the base recorded corpus; the second game is live in it."""
    workspace = tmp_path / "nba_api"
    workspace.mkdir()
    for source in FIXTURES.glob("*.json"):
        if source.stem.endswith(("__corrected", "__complete")):
            continue
        shutil.copyfile(source, workspace / source.name)
    return StatsClient(fixtures_dir=workspace, min_delay=0.0)


@pytest.fixture()
def corpus_complete(tmp_path: Path) -> StatsClient:
    """The same corpus after the second game has gone Final: two final games on the slate."""
    workspace = tmp_path / "nba_api_complete"
    workspace.mkdir()
    for source in FIXTURES.glob("*.json"):
        if source.stem.endswith("__corrected"):
            continue
        name = source.name.replace("__complete", "")
        shutil.copyfile(source, workspace / name)
    return StatsClient(fixtures_dir=workspace, min_delay=0.0)


@pytest.fixture()
def db(empty_engine: Engine) -> Iterator[Session]:
    session = Session(empty_engine, future=True)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def ingest_with_starters(db: Session, client: StatsClient, *game_ids: str) -> None:
    """The way production records a game: poll the scoreboard, then the per-game box score."""
    daily.poll_finalized_games(db, SLATE_DATE, client=client)
    for game_id in game_ids:
        daily.ingest_game(db, game_id, client=client)
    db.commit()


def flags(db: Session, game_id: str) -> dict[int, bool | None]:
    db.flush()
    db.expire_all()
    rows = db.execute(select(PlayerGameBasic).where(PlayerGameBasic.game_id == game_id)).scalars()
    return {row.player_id: row.started for row in rows}


def flatten(db: Session) -> None:
    """What the old bug did: every recorded start becomes ``False``, and the season rolls up
    with ``gs`` of zero."""
    db.execute(update(PlayerGameBasic).values(started=False))
    aggregate.recompute_season(db, SEASON, "Regular Season")
    db.commit()


def games_started(db: Session) -> dict[int, int]:
    db.expire_all()
    return {
        row.player_id: row.gs
        for row in db.execute(select(PlayerSeason).where(PlayerSeason.season == SEASON)).scalars()
    }


def sync_version(db: Session) -> int:
    db.expire_all()
    return read_sync_state(db).sync_version


# --------------------------------------------------------------------------- the repair


def test_a_database_the_old_bug_flattened_gets_its_starting_fives_back(
    db: Session, corpus: StatsClient
) -> None:
    ingest_with_starters(db, corpus, FIRST_GAME)
    recorded = flags(db, FIRST_GAME)
    assert sum(1 for flag in recorded.values() if flag is True) == 10
    flatten(db)
    assert set(flags(db, FIRST_GAME).values()) == {False}
    assert set(games_started(db).values()) == {0}  # "games started" is zero for everyone
    before = sync_version(db)

    fake = FakeClient(corpus)
    report = runner.run_refetch_games(days=1, end_date=SLATE_DATE, client=fake, session=db)

    assert flags(db, FIRST_GAME) == recorded  # exactly the flags the box score recorded
    assert (report.starts_before, report.starts_after, report.unrecorded_after) == (0, 10, 0)
    assert (report.games_found, report.games_refetched, report.games_changed) == (1, 1, 1)
    assert report.games_failed == 0 and report.state == "ok"
    starters = [pid for pid, flag in recorded.items() if flag is True]
    assert all(games_started(db)[pid] == 1 for pid in starters)
    assert sync_version(db) > before  # clients refresh
    assert runner.refetch_exit_code(report) == 0


def test_a_database_a_bulk_season_walk_filled_has_unrecorded_starts_and_gets_them(
    db: Session, corpus: StatsClient
) -> None:
    """``--nightly --days N`` never records a start; this is how they are filled in."""
    daily.ingest_day(db, SLATE_DATE, client=corpus)
    db.commit()
    assert set(flags(db, FIRST_GAME).values()) == {None}

    report = runner.run_refetch_games(
        days=1, end_date=SLATE_DATE, client=FakeClient(corpus), session=db
    )

    after = flags(db, FIRST_GAME)
    assert sum(1 for flag in after.values() if flag is True) == 10
    assert None not in after.values()  # every line now says started or did not
    assert report.unrecorded_after == 0 and report.starts_after == 10


def test_the_repair_uses_only_the_per_game_box_score_calls(
    db: Session, corpus: StatsClient
) -> None:
    ingest_with_starters(db, corpus, FIRST_GAME)
    flatten(db)
    fake = FakeClient(corpus)
    runner.run_refetch_games(days=1, end_date=SLATE_DATE, client=fake, session=db)
    assert fake.calls == [("traditional", FIRST_GAME), ("advanced", FIRST_GAME)]  # two a game


def test_a_second_run_changes_nothing_and_moves_no_version(
    db: Session, corpus: StatsClient
) -> None:
    ingest_with_starters(db, corpus, FIRST_GAME)
    flatten(db)
    runner.run_refetch_games(days=1, end_date=SLATE_DATE, client=FakeClient(corpus), session=db)
    version = sync_version(db)
    lines = flags(db, FIRST_GAME)

    again = runner.run_refetch_games(
        days=1, end_date=SLATE_DATE, client=FakeClient(corpus), session=db
    )

    assert again.games_refetched == 1 and again.games_changed == 0
    assert again.starts_before == again.starts_after == 10
    assert sync_version(db) == version
    assert flags(db, FIRST_GAME) == lines


def test_aggregates_are_recomputed_once_at_the_end_not_once_per_game(
    db: Session, corpus_complete: StatsClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    ingest_with_starters(db, corpus_complete, FIRST_GAME, SECOND_GAME)
    flatten(db)
    one_season: list[tuple[str, str | None]] = []
    real = aggregate.recompute_season

    def spy(session: Session, season: str, season_type: str | None = None, **kwargs: Any) -> Any:
        one_season.append((season, season_type))
        return real(session, season, season_type, **kwargs)

    monkeypatch.setattr(aggregate, "recompute_season", spy)

    report = runner.run_refetch_games(
        days=1, end_date=SLATE_DATE, client=FakeClient(corpus_complete), session=db
    )

    assert report.games_refetched == 2 and report.games_changed == 2
    # Two changed games in one season: the season is rebuilt once, after both, not after each.
    assert one_season == [(SEASON, "Regular Season")]
    assert set(games_started(db).values()) >= {1}


# --------------------------------------------------------------------------- the window


def test_only_the_stored_final_games_of_the_window_are_refetched(
    db: Session, corpus: StatsClient
) -> None:
    ingest_with_starters(db, corpus, FIRST_GAME)  # the second game is still live in the corpus
    fake = FakeClient(corpus)

    before = runner.run_refetch_games(
        days=1, end_date=SLATE_DATE - timedelta(days=1), client=fake, session=db
    )
    assert before.games_found == 0 and fake.calls == []  # a day with no stored games: no request
    assert runner.refetch_exit_code(before) == 0

    wide = runner.run_refetch_games(days=5, end_date=SLATE_DATE, client=fake, session=db)
    assert wide.games_found == 1  # the live game is not final, so it is not refetched
    assert (wide.first_date, wide.last_date) == (SLATE_DATE - timedelta(days=4), SLATE_DATE)
    assert {game_id for _, game_id in fake.calls} == {FIRST_GAME}


def test_the_default_window_is_the_correction_window_ending_at_data_through(
    db: Session, corpus: StatsClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORRECTION_WINDOW_DAYS", "3")
    config.reset_settings_cache()
    try:
        ingest_with_starters(db, corpus, FIRST_GAME)
        state = read_sync_state(db)
        state.data_through = SLATE_DATE
        db.commit()
        report = runner.run_refetch_games(client=FakeClient(corpus), session=db)
        assert (report.first_date, report.last_date) == (
            SLATE_DATE - timedelta(days=2), SLATE_DATE
        )
        assert report.games_found == 1
    finally:
        config.reset_settings_cache()


def test_a_window_of_less_than_one_day_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one day"):
        daily.refetch_games(None, 0)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- failures and stops


def test_a_game_that_cannot_be_fetched_is_counted_and_the_rest_still_run(
    db: Session, corpus_complete: StatsClient
) -> None:
    ingest_with_starters(db, corpus_complete, FIRST_GAME, SECOND_GAME)
    flatten(db)
    fake = FakeClient(corpus_complete, fail=(FIRST_GAME,))

    report = runner.run_refetch_games(days=1, end_date=SLATE_DATE, client=fake, session=db)

    assert (report.games_found, report.games_refetched, report.games_failed) == (2, 1, 1)
    assert report.failed_game_ids == [FIRST_GAME]
    assert set(flags(db, FIRST_GAME).values()) == {False}  # untouched: still waiting for a re-run
    assert True in flags(db, SECOND_GAME).values()  # the other game was repaired
    log = db.execute(select(IngestLog).where(IngestLog.job == "refetch")).scalars().one()
    assert log.status == "partial" and FIRST_GAME in (log.error or "")
    assert runner.refetch_exit_code(report) == 0  # something was fetched; the log names the rest


def test_when_nothing_could_be_fetched_the_exit_status_says_so(
    db: Session, corpus: StatsClient
) -> None:
    ingest_with_starters(db, corpus, FIRST_GAME)
    version = sync_version(db)
    fake = FakeClient(corpus, fail=(FIRST_GAME,))
    report = runner.run_refetch_games(days=1, end_date=SLATE_DATE, client=fake, session=db)
    assert (report.games_found, report.games_refetched, report.games_failed) == (1, 0, 1)
    assert runner.refetch_exit_code(report) == 3
    assert sync_version(db) == version  # nothing changed, so nothing is bumped


def test_a_stop_request_ends_the_run_cleanly_before_the_next_game(
    db: Session, corpus_complete: StatsClient
) -> None:
    ingest_with_starters(db, corpus_complete, FIRST_GAME, SECOND_GAME)
    flatten(db)
    flag = runner.ShutdownFlag()
    flag.request()
    fake = FakeClient(corpus_complete)

    report = runner.run_refetch_games(
        days=1, end_date=SLATE_DATE, client=fake, session=db, shutdown=flag
    )

    assert report.interrupted and report.games_refetched == 0 and fake.calls == []
    assert runner.refetch_exit_code(report) == 130
    assert set(flags(db, FIRST_GAME).values()) == {False}


def test_an_interrupted_run_leaves_the_finished_games_repaired_and_the_aggregates_right(
    db: Session, corpus_complete: StatsClient
) -> None:
    ingest_with_starters(db, corpus_complete, FIRST_GAME, SECOND_GAME)
    flatten(db)
    flag = runner.ShutdownFlag()

    class StopAfterOne(FakeClient):
        def box_score_advanced(self, game_id: str) -> Any:
            result = super().box_score_advanced(game_id)
            flag.request()  # the stop arrives while the first game is being written
            return result

    report = runner.run_refetch_games(
        days=1, end_date=SLATE_DATE, client=StopAfterOne(corpus_complete), session=db,
        shutdown=flag,
    )

    assert report.interrupted and report.games_refetched == 1
    repaired = [g for g in (FIRST_GAME, SECOND_GAME) if True in flags(db, g).values()]
    assert len(repaired) == 1
    assert max(games_started(db).values()) == 1  # the season rollup follows what was repaired


# --------------------------------------------------------------------------- refusal


def test_the_seeded_demo_league_is_refused_and_no_request_is_made(
    db: Session, corpus: StatsClient
) -> None:
    ingest_with_starters(db, corpus, FIRST_GAME)
    db.execute(text("UPDATE games SET data_source = 'synthetic-demo' WHERE game_id = :g"),
               {"g": FIRST_GAME})
    db.commit()
    fake = FakeClient(corpus)

    report = runner.run_refetch_games(days=1, end_date=SLATE_DATE, client=fake, session=db)

    assert report.state == "refused" and "demo league" in (report.reason or "")
    assert fake.calls == []
    assert runner.refetch_exit_code(report) == 5
    assert db.execute(select(IngestLog).where(IngestLog.job == "refetch")).first() is None


# --------------------------------------------------------------------------- the command line


@pytest.fixture()
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """``main`` with the work stubbed out: what it hands ``run_refetch_games`` is recorded."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    config.reset_settings_cache()
    seen: list[dict[str, Any]] = []

    def fake_run(**kwargs: Any) -> daily.RefetchReport:
        seen.append(kwargs)
        return daily.RefetchReport(games_found=1, games_refetched=1)

    monkeypatch.setattr(runner, "run_refetch_games", fake_run)
    yield seen
    config.reset_settings_cache()


def test_the_command_line_passes_the_window_through(cli: list[dict[str, Any]]) -> None:
    assert runner.main(["--refetch-games", "--days", "200", "--date", "2026-04-15"]) == 0
    [call] = cli
    assert call["days"] == 200 and call["end_date"] == date(2026, 4, 15)
    assert isinstance(call["shutdown"], runner.ShutdownFlag)


def test_the_command_line_defaults_to_the_correction_window(cli: list[dict[str, Any]]) -> None:
    assert runner.main(["--refetch-games"]) == 0
    assert cli[0]["days"] is None and cli[0]["end_date"] is None


def test_the_exit_codes_come_from_the_report(
    cli: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    for report, code in (
        (daily.RefetchReport(state="refused", reason="demo"), 5),
        (daily.RefetchReport(interrupted=True, games_found=3, games_refetched=1), 130),
        (daily.RefetchReport(games_found=2, games_failed=2), 3),
        (daily.RefetchReport(games_found=0), 0),
        (daily.RefetchReport(games_found=4, games_refetched=3, games_failed=1), 0),
    ):
        monkeypatch.setattr(runner, "run_refetch_games", lambda report=report, **_: report)
        assert runner.main(["--refetch-games"]) == code


def test_a_window_below_one_day_and_a_second_mode_are_command_line_errors(
    cli: list[dict[str, Any]],
) -> None:
    with pytest.raises(SystemExit) as zero:
        runner.main(["--refetch-games", "--days", "0"])
    assert zero.value.code == 2
    with pytest.raises(SystemExit) as both:
        runner.main(["--refetch-games", "--nightly"])
    assert both.value.code == 2
    assert cli == []


# --------------------------------------------------------------------------- what the docs say


def _text(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_the_runbook_documents_the_repair_and_says_the_bulk_pass_cannot_do_it() -> None:
    runbook = _text("docs", "RUNBOOK.md")
    assert "--refetch-games" in runbook
    assert "--refetch-games --days" in runbook and "--date" in runbook
    flat = " ".join(runbook.split())
    assert "cannot restore" in flat or "can not restore" in flat or "never restores" in flat


def test_no_doc_claims_the_nightly_pass_restores_starters() -> None:
    """The bulk rows have no starter column; a doc that says a nightly run brings starters back
    is wrong. This reads every Markdown doc for a sentence putting 'nightly' and 'restore' (or
    'repair') and 'starter' together without the refetch flag in the same paragraph."""
    offenders: list[str] = []
    for path in [*ROOT.joinpath("docs").glob("*.md"), ROOT / "README.md",
                 ROOT / "backend" / "README.md", ROOT / "ios" / "README.md"]:
        if not path.exists():
            continue
        for paragraph in path.read_text(encoding="utf-8").split("\n\n"):
            low = paragraph.lower()
            if "starter" in low and "nightly" in low and ("restor" in low or "repair" in low):
                if "--refetch-games" not in paragraph and "cannot" not in low:
                    offenders.append(f"{path.name}: {paragraph[:90]!r}")
    assert offenders == []
