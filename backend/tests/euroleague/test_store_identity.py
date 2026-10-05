"""The store's stamp, and what start-up does with it.

``el_store_identity`` says whether a file holds the invented league or real data, and that stamp
decides what may be written: invented rows only with invented rows, real rows (a workbook import
or live ingest) only with real rows. ``bootstrap.prepare`` is the one place that decides, from the
environment, what a new store should be; this module walks its decision table, and also stands in
for the old terms-gate tests: with the gate removed, a terms variable has no effect and an import
simply runs.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from nbastats.euroleague import bootstrap, db
from nbastats.euroleague.db import (
    ElStoreError,
    StoreKindMismatch,
    bump_sync_version,
    ensure_identity,
    is_synthetic_store,
    kinds_compatible,
    promote_to_live,
    read_identity,
    read_sync_state,
    require_writer,
)
from nbastats.euroleague.models import ElIntelNewsFeed, ElStoreIdentity
from nbastats.euroleague.profile import STORE_KINDS

NOW = datetime(2026, 10, 4, 12, 0, 0)
EnvFactory = Callable[..., tuple[dict[str, str], Any]]


def rows(engine: Engine, sql: str) -> list[tuple[Any, ...]]:
    with engine.connect() as connection:
        return [tuple(r) for r in connection.execute(text(sql)).all()]


# --------------------------------------------------------------------------- the stamp


@pytest.fixture(autouse=True)
def _fresh_bootstrap_state() -> Iterator[None]:
    """``bootstrap`` remembers its last outcome in the process; none of it may leak out."""
    bootstrap.reset_state()
    yield
    bootstrap.reset_state()


def test_a_new_store_has_no_identity_until_something_writes(el_session: Session) -> None:
    assert read_identity(el_session) is None
    assert not is_synthetic_store(el_session)


def test_ensure_identity_stamps_once_and_returns_the_same_row(el_session: Session) -> None:
    first = ensure_identity(el_session, "workbook", NOW)
    assert (first.id, first.league, first.kind, first.created_at) == (
        1,
        "euroleague",
        "workbook",
        NOW,
    )
    again = ensure_identity(el_session, "workbook", datetime(2030, 1, 1))
    assert again is first and again.created_at == NOW  # the first stamp stands


def test_the_stamp_has_no_terms_fields(el_session: Session) -> None:
    identity = ensure_identity(el_session, "live", NOW)
    assert not [name for name in vars(identity) if "terms" in name]
    assert not hasattr(ElStoreIdentity, "terms_reviewed_on")
    assert not hasattr(ElStoreIdentity, "terms_outcome")


@pytest.mark.parametrize(
    ("existing", "requested", "ok"),
    [
        ("synthetic", "synthetic", True),
        ("workbook", "workbook", True),
        ("live", "live", True),
        ("workbook", "live", True),  # real with real: the workbook and the service are one source
        ("live", "workbook", True),
        ("synthetic", "workbook", False),
        ("synthetic", "live", False),
        ("workbook", "synthetic", False),
        ("live", "synthetic", False),
        ("scraped", "live", False),
        ("live", "scraped", False),
    ],
)
def test_which_kinds_may_share_a_store(existing: str, requested: str, ok: bool) -> None:
    assert kinds_compatible(existing, requested) is ok


def test_invented_and_real_data_never_meet(el_session: Session) -> None:
    ensure_identity(el_session, "synthetic", NOW)
    for real in ("workbook", "live"):
        with pytest.raises(StoreKindMismatch, match="new file"):
            require_writer(el_session, real)
    assert read_identity(el_session).kind == "synthetic"  # type: ignore[union-attr]


def test_a_real_store_refuses_invented_writes(el_session: Session) -> None:
    ensure_identity(el_session, "live", NOW)
    with pytest.raises(StoreKindMismatch, match="invented"):
        require_writer(el_session, "synthetic")


def test_a_mismatch_is_an_el_store_error(el_session: Session) -> None:
    ensure_identity(el_session, "synthetic", NOW)
    with pytest.raises(ElStoreError):
        ensure_identity(el_session, "live")
    with pytest.raises(ElStoreError, match="unknown store kind"):
        ensure_identity(el_session, "scraped")


def test_another_leagues_stamp_is_refused(el_engine: Engine) -> None:
    with el_engine.begin() as connection:
        connection.execute(text("PRAGMA ignore_check_constraints = ON"))
        connection.execute(
            text("INSERT INTO el_store_identity VALUES (1, 'nba', 'live', '2026-01-01')")
        )
    with Session(el_engine) as session:
        with pytest.raises(ElStoreError, match="league"):
            ensure_identity(session, "live")


def test_promote_to_live_is_real_to_real_only(el_session: Session) -> None:
    assert promote_to_live(el_session) is None  # no identity yet
    ensure_identity(el_session, "workbook", NOW)
    assert promote_to_live(el_session).kind == "live"  # type: ignore[union-attr]
    assert promote_to_live(el_session).kind == "live"  # type: ignore[union-attr]  # idempotent
    el_session.execute(text("DELETE FROM el_store_identity"))
    el_session.expire_all()
    ensure_identity(el_session, "synthetic", NOW)
    assert promote_to_live(el_session).kind == "synthetic"  # type: ignore[union-attr]


def test_the_three_kinds() -> None:
    assert STORE_KINDS == ("synthetic", "workbook", "live")


# --------------------------------------------------------------------------- the sync cursor


def test_the_cursor_is_created_on_first_read_with_the_mode_of_the_stamp(
    el_session: Session,
) -> None:
    ensure_identity(el_session, "synthetic", NOW)
    state = read_sync_state(el_session)
    assert (state.id, state.sync_version, state.mode, state.data_through) == (1, 0, "demo", None)
    assert read_sync_state(el_session) is state


@pytest.mark.parametrize(
    ("kind", "mode"),
    [("synthetic", "demo"), ("workbook", "workbook"), ("live", "live"), (None, "workbook")],
)
def test_the_mode_that_goes_with_a_kind(kind: str | None, mode: str) -> None:
    assert db.sync_mode_for_kind(kind) == mode


def test_the_version_bumps_once_and_data_through_only_moves_forward(el_session: Session) -> None:
    from datetime import date

    ensure_identity(el_session, "live", NOW)
    assert bump_sync_version(el_session, date(2026, 10, 2), now=NOW) == 1
    assert (
        bump_sync_version(el_session, date(2026, 9, 24), now=NOW) == 2
    )  # a back-fill of an old round
    state = read_sync_state(el_session)
    assert state.data_through == date(
        2026, 10, 2
    )  # never moves back: the store must not look stale
    assert bump_sync_version(el_session, None, "live", NOW) == 3
    assert state.mode == "live" and state.last_success_at == NOW


# --------------------------------------------------------------------------- the decision table


def _engine_of(settings: Any) -> Engine:
    return db.get_el_engine(settings.database_url)


def test_nothing_configured_means_no_store_and_no_file(el_env: EnvFactory) -> None:
    _, settings = el_env(HARDWOOD_EL_LIVE="0")
    result = bootstrap.prepare(settings)
    assert result.state == bootstrap.STATE_NOT_CONFIGURED and not result.ready
    assert "HARDWOOD_WORKBOOK_PATH" in (result.reason or "")
    assert settings.store_path is not None and not settings.store_path.exists()
    assert bootstrap.get_state() == result


def test_live_alone_makes_an_empty_real_store_with_the_candidate_feeds(el_env: EnvFactory) -> None:
    _, settings = el_env()
    result = bootstrap.prepare(settings)
    assert result.ready and result.kind == "live" and not result.is_demo and result.live
    assert result.imports == () and not result.seeded_demo and result.reason is None
    engine = _engine_of(settings)
    assert rows(engine, "SELECT kind FROM el_store_identity") == [("live",)]
    assert rows(engine, "SELECT COUNT(*) FROM el_game") == [(0,)]
    feeds = rows(engine, "SELECT name, url, enabled FROM el_intel_news_feed ORDER BY name")
    assert feeds == [
        ("Eurohoops", "https://eurohoops.net/feed", 1),
        ("TalkBasket", "https://talkbasket.net/feed", 1),
    ]
    assert rows(engine, "SELECT mode FROM el_sync_state") == [("live",)]
    # preparing again changes nothing and adds no second set of feeds
    again = bootstrap.prepare(settings)
    assert again.ready and rows(engine, "SELECT COUNT(*) FROM el_intel_news_feed") == [(2,)]


def test_demo_makes_the_invented_store_and_seeds_it_once(el_env: EnvFactory) -> None:
    _, settings = el_env(HARDWOOD_EL_DEMO="1")
    result = bootstrap.prepare(settings)
    assert result.ready and result.kind == "synthetic" and result.is_demo and result.seeded_demo
    assert not result.live  # an invented league is never fetched into
    engine = _engine_of(settings)
    assert rows(engine, "SELECT COUNT(*) FROM el_game") == [(50,)]
    assert rows(engine, "SELECT COUNT(*) FROM el_intel_news_feed") == [
        (0,)
    ]  # the demo never touches the network
    assert rows(engine, "SELECT mode FROM el_sync_state") == [("demo",)]
    again = bootstrap.prepare(settings)
    assert (
        again.ready
        and not again.seeded_demo
        and rows(engine, "SELECT COUNT(*) FROM el_game") == [(50,)]
    )


def test_the_nba_demo_switch_brings_the_invented_league_with_it(el_env: EnvFactory) -> None:
    _, settings = el_env(HARDWOOD_DEMO_MODE="1")
    assert bootstrap.prepare(settings).kind == "synthetic"


def test_a_workbook_path_makes_a_real_store_and_imports_it(
    el_env: EnvFactory, tmp_path: Path, mini_league: Any
) -> None:
    path = tmp_path / "toolkit.xlsx"
    path.write_bytes(mini_league.workbook())
    # the committed crosswalk does not know the invented clubs, so this import is refused cleanly
    _, settings = el_env(HARDWOOD_WORKBOOK_PATH=str(path))
    result = bootstrap.prepare(settings)
    assert result.ready and result.kind == "workbook" and not result.is_demo
    assert len(result.imports) == 1 and result.imports[0]["status"] == "aborted"
    assert "unknown club code" in result.reason  # type: ignore[operator]
    engine = _engine_of(settings)
    assert rows(engine, "SELECT COUNT(*) FROM el_game") == [(0,)]


def test_a_workbook_that_the_crosswalk_knows_is_imported_at_start_up(
    el_env: EnvFactory, tmp_path: Path, xl: Any
) -> None:
    ratings = {
        5: dict(
            enumerate(
                ["Code", "Club", "PF/g 2025-26", "PA/g 2025-26", "Attack adj", "Defence adj"], 1
            )
        ),
        6: {1: "OLY", 2: "x", 3: 85.0, 4: 84.0, 5: 0.5, 6: -0.5},
    }
    path = tmp_path / "toolkit.xlsx"
    path.write_bytes(
        xl.make([("Team Ratings", ratings), ("Start Here", {2: {2: "Toolkit 2026-27"}})])
    )
    _, settings = el_env(HARDWOOD_WORKBOOK_PATH=str(path))
    result = bootstrap.prepare(settings)
    assert result.ready and result.kind == "workbook" and result.imports[0]["status"] == "ok"
    engine = _engine_of(settings)
    assert rows(engine, "SELECT COUNT(*) FROM el_team_rating") == [(1,)]
    assert rows(engine, "SELECT kind FROM el_store_identity") == [("workbook",)]
    assert rows(engine, "SELECT COUNT(*) FROM el_intel_news_feed") == [(2,)]
    assert rows(engine, "SELECT mode FROM el_sync_state") == [
        ("live",)
    ]  # live stays on beside the workbook
    # restarting re-imports nothing: the file's hash is already in the log
    again = bootstrap.prepare(settings)
    assert again.ready and again.imports == ()
    assert rows(engine, "SELECT COUNT(*) FROM el_ingest_log") == [(1,)]


def test_a_dropped_inbox_file_is_picked_up_and_junk_is_not_retried(
    el_env: EnvFactory, tmp_path: Path, xl: Any
) -> None:
    inbox = tmp_path / "data" / "inbox"
    inbox.mkdir(parents=True)
    ratings = {
        5: dict(
            enumerate(
                ["Code", "Club", "PF/g 2025-26", "PA/g 2025-26", "Attack adj", "Defence adj"], 1
            )
        ),
        6: {1: "RMA", 2: "x", 3: 85.0, 4: 84.0, 5: 0.5, 6: -0.5},
    }
    (inbox / "good.xlsx").write_bytes(
        xl.make([("Team Ratings", ratings), ("Start Here", {2: {2: "Toolkit 2026-27"}})])
    )
    (inbox / "junk.xlsx").write_bytes(b"this is not a workbook")
    (inbox / "~$good.xlsx").write_bytes(b"an Excel lock file")
    _, settings = el_env()
    result = bootstrap.prepare(settings)
    by_name = {i["fileName"]: i["status"] for i in result.imports}
    assert by_name == {
        "good.xlsx": "ok",
        "junk.xlsx": "failed",
    }  # the lock file was never looked at
    assert any("junk.xlsx" in note for note in result.notes)
    # a restart does not retry the junk file, and picks up the next good one
    ratings[6][1] = "BAR"
    (inbox / "later.xlsx").write_bytes(
        xl.make([("Team Ratings", ratings), ("Start Here", {2: {2: "Toolkit 2026-27"}})])
    )
    again = bootstrap.prepare(settings)
    assert {i["fileName"]: i["status"] for i in again.imports} == {"later.xlsx": "ok"}


def test_a_workbook_cannot_be_imported_into_the_invented_store(
    el_env: EnvFactory, tmp_path: Path, mini_league: Any
) -> None:
    _, demo_settings = el_env(HARDWOOD_EL_DEMO="1")
    assert bootstrap.prepare(demo_settings).kind == "synthetic"
    path = tmp_path / "toolkit.xlsx"
    path.write_bytes(mini_league.workbook())
    _, settings = el_env(HARDWOOD_WORKBOOK_PATH=str(path))  # the same default store file
    result = bootstrap.prepare(settings)
    assert result.ready and result.kind == "synthetic" and result.is_demo and result.imports == ()
    assert "invented demo league" in (result.reason or "") and "never takes real data" in (
        result.reason or ""
    )
    assert rows(
        _engine_of(settings), "SELECT COUNT(*) FROM el_game WHERE data_source LIKE 'workbook:%'"
    ) == [(0,)]


def test_a_store_that_was_only_stamped_live_can_still_become_the_demo(el_env: EnvFactory) -> None:
    """An ordinary first start (live ingest on by default) stamps the new store ``live`` before
    anything is fetched. Asking for the demo afterwards must work: nothing real is in it."""
    _, settings = el_env()
    first = bootstrap.prepare(settings)
    assert first.kind == "live"
    assert rows(_engine_of(settings), "SELECT COUNT(*) FROM el_intel_news_feed") == [(2,)]
    _, demo_settings = el_env(HARDWOOD_EL_DEMO="1")
    result = bootstrap.prepare(demo_settings)
    assert result.ready and result.kind == "synthetic" and result.is_demo and result.seeded_demo
    assert "never received any" in (result.reason or "")
    assert rows(_engine_of(settings), "SELECT kind FROM el_store_identity") == [("synthetic",)]
    assert rows(_engine_of(settings), "SELECT COUNT(*) FROM el_intel_news_feed") == [(0,)]


def test_a_real_store_holding_real_data_is_never_given_the_invented_league(
    el_env: EnvFactory, tmp_path: Path, mini_league: Any
) -> None:
    path = tmp_path / "toolkit.xlsx"
    path.write_bytes(mini_league.workbook())
    _, settings = el_env(HARDWOOD_WORKBOOK_PATH=str(path))
    imported = bootstrap.prepare(settings)
    assert imported.kind in ("workbook", "live")
    games = rows(_engine_of(settings), "SELECT COUNT(*) FROM el_game")
    _, demo_settings = el_env(HARDWOOD_EL_DEMO="1")
    result = bootstrap.prepare(demo_settings)
    assert result.ready and result.kind == imported.kind and not result.is_demo
    assert not result.seeded_demo
    assert "stamped for real data and holds some" in (result.reason or "")
    assert rows(_engine_of(settings), "SELECT COUNT(*) FROM el_game") == games


def test_the_switch_turns_the_whole_thing_off(el_env: EnvFactory) -> None:
    _, settings = el_env(HARDWOOD_EL_ENABLED="0", HARDWOOD_EL_DEMO="1")
    result = bootstrap.prepare(settings)
    assert result.state == bootstrap.STATE_DISABLED and "HARDWOOD_EL_ENABLED" in (
        result.reason or ""
    )
    assert settings.store_path is not None and not settings.store_path.exists()


def test_prepare_never_raises(el_env: EnvFactory, tmp_path: Path) -> None:
    directory = tmp_path / "a_directory"
    directory.mkdir()
    _, settings = el_env(HARDWOOD_EL_DATABASE_URL=f"sqlite:///{directory}", HARDWOOD_EL_DEMO="1")
    result = bootstrap.prepare(settings)  # a directory is not a database file
    assert result.state == bootstrap.STATE_ERROR and result.reason and not result.ready
    assert bootstrap.get_state() is result


def test_a_malformed_switch_is_an_error_state_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARDWOOD_EL_ENABLED", "perhaps")
    result = bootstrap.prepare()
    assert result.state == bootstrap.STATE_ERROR and "HARDWOOD_EL_ENABLED" in (result.reason or "")


def test_the_state_before_prepare_is_not_ready() -> None:
    bootstrap.reset_state()
    assert bootstrap.get_state().state == bootstrap.STATE_NOT_PREPARED
    assert not bootstrap.get_state().ready


def test_the_result_serialises(el_env: EnvFactory) -> None:
    _, settings = el_env(HARDWOOD_EL_DEMO="1")
    document = bootstrap.prepare(settings).to_dict()
    assert {
        "state",
        "reason",
        "kind",
        "isDemo",
        "seededDemo",
        "live",
        "imports",
        "notes",
        "checkedAt",
    } <= set(document)
    assert document["state"] == "ready" and document["isDemo"] is True


# --------------------------------------------------------------------------- the terms gate is gone


def test_a_terms_variable_changes_nothing(
    el_env: EnvFactory, tmp_path: Path, mini_league: Any
) -> None:
    """With the gate removed, 'denied' is just a stray variable and the import still runs."""
    path = tmp_path / "toolkit.xlsx"
    path.write_bytes(mini_league.workbook())
    _, settings = el_env(
        HARDWOOD_WORKBOOK_PATH=str(path),
        HARDWOOD_EL_TERMS_OUTCOME="denied",
        HARDWOOD_EL_TERMS_URL="https://example.org/terms",
        HARDWOOD_EL_TERMS_REVIEWED_ON="never",
    )
    engine = bootstrap.open_store(settings)
    from nbastats.euroleague.importers.workbook import import_workbook

    report = import_workbook(path, engine, crosswalk=mini_league.crosswalk)
    assert report.status == "ok"
    assert "termsNotReviewed" not in repr(bootstrap.BootstrapResult(state="ready"))


def test_no_state_is_called_terms_not_reviewed() -> None:
    names = {getattr(bootstrap, n) for n in dir(bootstrap) if n.startswith("STATE_")}
    assert "termsNotReviewed" not in names
    assert names == {"ready", "disabled", "misconfigured", "notConfigured", "error", "notPrepared"}


def test_default_feeds_are_added_only_to_a_store_with_none(el_session: Session) -> None:
    assert bootstrap.ensure_default_feeds(el_session) == 2
    assert bootstrap.ensure_default_feeds(el_session) == 0
    el_session.execute(text("UPDATE el_intel_news_feed SET enabled = 0"))
    assert (
        bootstrap.ensure_default_feeds(el_session) == 0
    )  # a feed the user disabled stays disabled
    assert {f.enabled for f in el_session.query(ElIntelNewsFeed)} == {False}


def test_run_workbook_import_dry_run_leaves_no_store_behind(
    el_env: EnvFactory, tmp_path: Path, xl: Any
) -> None:
    ratings = {
        5: dict(
            enumerate(
                ["Code", "Club", "PF/g 2025-26", "PA/g 2025-26", "Attack adj", "Defence adj"], 1
            )
        ),
        6: {1: "VIR", 2: "x", 3: 85.0, 4: 84.0, 5: 0.5, 6: -0.5},
    }
    path = tmp_path / "toolkit.xlsx"
    path.write_bytes(
        xl.make([("Team Ratings", ratings), ("Start Here", {2: {2: "Toolkit 2026-27"}})])
    )
    _, settings = el_env()
    dry = bootstrap.run_workbook_import(path, dry_run=True, settings=settings)
    assert dry.status == "ok" and dry.dry_run and dry.counts["ratings"] == 1
    assert (
        settings.store_path is not None and not settings.store_path.exists()
    )  # no file was created
    real = bootstrap.run_workbook_import(path, settings=settings)
    assert real.status == "ok" and settings.store_path.exists()
    again = bootstrap.run_workbook_import(path, settings=settings)
    assert again.status == "alreadyImported"
    missing = bootstrap.run_workbook_import(tmp_path / "missing.xlsx", settings=settings)
    assert missing.status == "failed"


def test_run_workbook_import_respects_the_off_switch_and_the_estimates_flag(
    el_env: EnvFactory, tmp_path: Path, mini_league: Any
) -> None:
    path = tmp_path / "toolkit.xlsx"
    path.write_bytes(mini_league.workbook())
    _, off = el_env(HARDWOOD_EL_ENABLED="0")
    refused = bootstrap.run_workbook_import(path, settings=off)
    assert refused.status == "failed" and "switched off" in (refused.error or "")


# --------------------------------------------------------------------------- engine plumbing


def test_the_engine_is_cached_per_url_and_disposable(tmp_path: Path) -> None:
    one = f"sqlite:///{tmp_path / 'one.db'}"
    two = f"sqlite:///{tmp_path / 'two.db'}"
    first = db.get_el_engine(one)
    assert db.get_el_engine(one) is first
    assert db.get_el_engine(two) is not first
    assert db.get_el_sessionmaker(one) is db.get_el_sessionmaker(one)
    db.dispose_el_engine()
    assert db.get_el_engine(one) is not first


def test_the_request_dependency_yields_a_session_on_the_configured_store(
    el_env: EnvFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    environ, settings = el_env(HARDWOOD_EL_DEMO="1")
    for key, value in environ.items():
        monkeypatch.setenv(key, value)
    bootstrap.prepare(settings)
    generator = db.get_el_db()
    session = next(generator)
    try:
        assert session.execute(text("SELECT COUNT(*) FROM el_game")).scalar_one() == 50
        assert str(session.get_bind().url) == settings.database_url
    finally:
        generator.close()


def test_the_session_scope_commits_on_success_and_rolls_back_on_failure(el_engine: Engine) -> None:
    with db.el_session_scope(el_engine) as session:
        ensure_identity(session, "live", NOW)
    assert rows(el_engine, "SELECT kind FROM el_store_identity") == [("live",)]
    with pytest.raises(RuntimeError):
        with db.el_session_scope(el_engine) as session:
            session.execute(text("DELETE FROM el_store_identity"))
            raise RuntimeError("boom")
    assert rows(el_engine, "SELECT kind FROM el_store_identity") == [("live",)]


def test_open_store_creates_and_stamps_a_store(el_env: EnvFactory) -> None:
    _, settings = el_env()
    engine = bootstrap.open_store(settings, "workbook")
    assert rows(engine, "SELECT league, kind FROM el_store_identity") == [
        ("euroleague", "workbook")
    ]
    again = bootstrap.open_store(settings, "live")  # real with real: fine
    assert again is engine
    with pytest.raises(StoreKindMismatch):
        bootstrap.open_store(settings, "synthetic")
    unstamped = bootstrap.open_store(settings)  # opening without a kind stamps nothing
    assert unstamped is engine
