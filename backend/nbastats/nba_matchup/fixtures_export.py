"""The committed NBA league payload fixtures, built from the invented demo league.

``contracts/fixtures/leagues/nba/*.json`` are one real response of every NBA league payload family
(design section 10.3), so a client (the Swift app, later) can decode against a recorded body
instead of the design's prose. They are *generated*, never hand-edited, and a test
(``tests/test_league_fixtures.py``) rebuilds them and demands byte equality with what is
committed, the same discipline ``backend/nbastats/fixtures_export.py`` holds the original
thirty-four fixtures to.

What makes them reproducible
----------------------------
* **An invented league.** Every game, score and player comes from the demo seeder
  (:mod:`nbastats.seed`), run here with its identity file switched off so that the *players* are
  invented too (the franchises are the thirty real ones: a team's name is a fact, a player's
  injury status is not, and none is ever attached to a real person). The injury statuses,
  the override, the headlines and their links are authored here; every link points at
  ``example.org``. Nothing in them is real, which is also what lets them be committed to a public
  repository.
* **A held clock.** Every payload is built with ``now`` set to :data:`FIXTURE_NOW` (11:30 Eastern on
  3 January 2026, inside the hour before the lock deadline of the next day's games), and the
  database clock the seeder and the intel layer read is frozen to the same moment, so
  ``generatedAt``, every age in minutes and every ``scheduled`` / ``resultPending`` status come out
  the same on every run.
* **A throwaway store.** The league is seeded into a fresh SQLite file in a temporary directory,
  so a developer's own store is never read and the fixtures never depend on one; the file's path
  appears in no payload.
* **The real flows.** The projections are refreshed and then locked by the same ledger functions
  the scheduler calls, at :data:`FIXTURE_NOW`, so the detail fixture shows a genuine frozen row and
  a genuine history. The newest day's finished games (all but the last) are written as frozen rows
  stamped half an hour before their deadlines, so the review fixture has a frozen block and a
  rebuilt one.

The two honest exceptions
-------------------------
The demo league refuses to show injury statuses (they are real-world facts and the games are
invented), which is right for the app and useless for showing what an entry looks like. So the
exporter lifts that refusal for its own build (it replaces
:func:`~nbastats.nba_matchup.availability_view.statuses_allowed` for the duration, and puts it
back), while every payload still says ``isDemo: true``. And the spreads have no default and the
demo refuses to fit one, so the exporter types a pair in the way a user could (provenance
``manual``), which is why the fixtures carry ranges whose ``intervalBasis`` is ``assumed``. The
build also writes ``availability_report_demo.json``, the answer the demo gives with the refusal in
force, so both states are on record.

``contracts/fixtures/leagues/`` sits below the non-recursive globs the original fixtures' checks
use, so adding these touches none of them.

Run ``python -m nbastats.nba_matchup.fixtures_export --write`` to regenerate, ``--check`` to verify
(exit status 1 on any difference).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import logging
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from sqlalchemy.orm import Session

from .. import db as db_module
from .. import identities
from ..db import create_db_engine, init_db
from ..nba_intel import news as intel_news
from ..nba_intel import settings as intel_settings
from ..nba_intel import status as intel_status
from ..nba_intel import store as intel_store
from ..nba_intel.models import (
    NbaIntelNewsFeed,
    NbaIntelStatus,
    NbaIntelTeamReport,
)
from ..seed import seed_database
from . import availability_view, ledger
from . import news_view, settings_view, sources
from . import (
    availability_report,
    defense_by_position,
    game_projection,
    projection_review,
    slate_projections,
    team_matchup,
)
from .projection import get_model, squad_stats
from .queries import build_context, clear_memo

__all__ = [
    "FIXTURE_NOW",
    "FIXTURE_AS_OF",
    "FIXTURE_DIR",
    "FIXTURE_NAMES",
    "build_fixtures",
    "render",
    "write_fixtures",
    "check_fixtures",
    "main",
]

FIXTURE_SEASONS: tuple[str, ...] = ("2024-25", "2025-26")
#: The date the newest season is "in progress" on: games after it are scheduled.
FIXTURE_AS_OF = date(2026, 1, 2)
FIXTURE_GAMES_PER_TEAM = 58
FIXTURE_PLAYERS_PER_TEAM = 10

#: The moment every fixture is built at: 11:30 Eastern on 3 January 2026, the hour in which games
#: with no recorded tip-off are locked (their deadline is noon Eastern).
FIXTURE_NOW = datetime(2026, 1, 3, 16, 30, tzinfo=timezone.utc)

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "contracts" / "fixtures" / "leagues" / "nba"

#: The invented report, and where its link claims to be.
_REPORT_URL = "https://example.org/injury-report/Injury-Report_2026-01-03_10_30AM.pdf"
_FEED_URL = "https://news.example.org/hoops/feed.xml"

FIXTURE_NAMES: tuple[str, ...] = (
    "availability_report",
    "availability_report_demo",
    "availability_review_queue",
    "defense_by_position",
    "defense_by_position_table",
    "game_matchup",
    "game_projection_detail",
    "leagues",
    "model_settings",
    "news",
    "projection_review",
    "slate_projections",
    "sources",
    "team_matchup",
)


# --------------------------------------------------------------------------- the build


@contextlib.contextmanager
def _frozen_clock(moment: datetime) -> Iterator[None]:
    """Pin :func:`nbastats.db.utcnow` everywhere it was imported, for the duration."""
    original = db_module.utcnow
    fixed = lambda: moment.replace(tzinfo=None)  # noqa: E731 - a one-line stand-in
    patched: list[tuple[Any, str]] = []
    for name, module in list(sys.modules.items()):
        if name.startswith("nbastats") and getattr(module, "utcnow", None) is original:
            setattr(module, "utcnow", fixed)
            patched.append((module, "utcnow"))
    try:
        yield
    finally:
        for module, attribute in patched:
            setattr(module, attribute, original)


@contextlib.contextmanager
def _invented_players() -> Iterator[None]:
    """Run the seeder with its identity file switched off, so every player is invented."""
    previous = os.environ.get(identities.IDENTITIES_PATH_ENV)
    os.environ[identities.IDENTITIES_PATH_ENV] = str(Path(tempfile.gettempdir()) / "none-here.json")
    identities.reset_cache()
    logger = logging.getLogger("nbastats.identities")
    level = logger.level
    logger.setLevel(logging.ERROR)
    try:
        yield
    finally:
        logger.setLevel(level)
        if previous is None:
            os.environ.pop(identities.IDENTITIES_PATH_ENV, None)
        else:
            os.environ[identities.IDENTITIES_PATH_ENV] = previous
        identities.reset_cache()


@contextlib.contextmanager
def _statuses_shown() -> Iterator[None]:
    """Lift the demo league's refusal to show injury statuses, for this build only."""
    original = availability_view.statuses_allowed
    availability_view.statuses_allowed = lambda session: True  # type: ignore[assignment]
    try:
        yield
    finally:
        availability_view.statuses_allowed = original  # type: ignore[assignment]


def _name(row: Any) -> str:
    last = row.last_name or row.full_name.split(" ")[-1]
    first = row.first_name or row.full_name.split(" ")[0]
    return f"{last}, {first}"


def _author_intel(session: Session, now: datetime) -> dict[str, Any]:
    """Write the invented injury report, override, spreads, headlines and feed. Returns the
    teams and players the documents are built around."""
    ctx = build_context(session, None, now=now)
    upcoming = ctx.scheduled_games()
    first, second = upcoming[0], next(
        g
        for g in upcoming
        if g.game_date == upcoming[0].game_date and g.game_id != upcoming[0].game_id
    )
    stamp = now.replace(tzinfo=None)
    slot = datetime(2026, 1, 3, 15, 30)  # 10:30 Eastern
    snapshot = intel_store.insert_snapshot(
        session,
        source_kind="leagueReport",
        fetched_at=slot + timedelta(minutes=1),
        parse_status="ok",
        row_count=0,
        url=_REPORT_URL,
        slot_at_utc=slot,
        report_as_of_utc=slot,
        sha256=hashlib.sha256(b"hardwood-nba-fixture-report").hexdigest(),
    )

    def scorer(team: int, rank: int) -> int:
        squad = sorted(squad_stats(ctx, team, ctx.start_of(first)), key=lambda x: (-x[2], x[0]))
        return squad[rank][0]

    players = {}
    plan = (
        (
            first.home_team_id,
            first,
            0,
            "out",
            "Out",
            "injury",
            "Injury/Illness - Left Knee; Sprain",
        ),
        (
            first.home_team_id,
            first,
            3,
            "probable",
            "Probable",
            "injury",
            "Injury/Illness - Right Ankle; Soreness",
        ),
        (
            first.away_team_id,
            first,
            0,
            "questionable",
            "Questionable",
            "illness",
            "Injury/Illness - Illness",
        ),
        (
            first.away_team_id,
            first,
            2,
            "doubtful",
            "Doubtful",
            "injury",
            "Injury/Illness - Back; Spasms",
        ),
        (second.home_team_id, second, 1, "out", "Out", "rest", "Rest"),
    )
    ctx.load_players({scorer(t, r) for t, _, r, *_ in plan})
    written = 0
    for team, game, rank, status, raw, category, reason in plan:
        pid = scorer(team, rank)
        player = ctx.player(pid)
        players[(team, rank)] = pid
        session.add(
            NbaIntelStatus(
                team_id=team,
                player_id=pid,
                player_name_raw=_name(player),
                game_id=game.game_id,
                game_date=game.game_date,
                status=status,
                status_raw=raw,
                reason_category=category,
                reason_text=reason,
                source_kind="leagueReport",
                source_label=intel_status.REPORT_SOURCE_LABEL,
                source_url=_REPORT_URL,
                source_published_at=slot,
                as_of=slot,
                recorded_at=slot + timedelta(minutes=1),
                snapshot_id=snapshot.snapshot_id,
                provenance_note=f"Injury report page 1 line {written + 1}",
            )
        )
        written += 1
    # One row the report printed that matched nobody uniquely: it waits in the review queue.
    session.add(
        NbaIntelStatus(
            team_id=second.home_team_id,
            player_id=None,
            player_name_raw="Okafor-Lindqvist, Tobias",
            game_id=second.game_id,
            game_date=second.game_date,
            status="questionable",
            status_raw="Questionable",
            reason_category="other",
            reason_text="Injury/Illness - Hamstring; Tightness",
            source_kind="leagueReport",
            source_label=intel_status.REPORT_SOURCE_LABEL,
            source_url=_REPORT_URL,
            source_published_at=slot,
            as_of=slot,
            recorded_at=slot + timedelta(minutes=1),
            snapshot_id=snapshot.snapshot_id,
            provenance_note="Injury report page 1 line 6",
        )
    )
    snapshot.row_count = written + 1
    for team, game, state in (
        (first.home_team_id, first, "submitted"),
        (first.away_team_id, first, "submitted"),
        (second.home_team_id, second, "submitted"),
        (second.away_team_id, second, "notYetSubmitted"),
    ):
        session.add(
            NbaIntelTeamReport(
                snapshot_id=snapshot.snapshot_id, team_id=team, game_id=game.game_id, state=state
            )
        )
    session.flush()
    intel_store.set_source_state(
        session,
        intel_store.SOURCE_INJURY_REPORT,
        "ok",
        now=slot + timedelta(minutes=1),
        success=True,
    )

    helper = scorer(second.away_team_id, 4)
    intel_status.add_override(
        session,
        player_id=helper,
        status="doubtful",
        team_id=second.away_team_id,
        note="Per a club statement read on 3 January",
        source_url="https://news.example.org/hoops/club-statement-0",
        # After the report below was fetched (15:31Z): an override entered before a newer league
        # report is obsolete, and the fixture is here to show one that still applies.
        source_published_at=datetime(2026, 1, 3, 15, 45),
        now=datetime(2026, 1, 3, 16, 5),
    )
    # Spreads: typed in by hand, the way a user could, so the fixtures carry ranges.
    intel_settings.apply_patch(
        session,
        {"teamSd": 9.2, "marginSd": 11.8},
        provenance="manual",
        now=stamp - timedelta(hours=1),
    )

    feed = NbaIntelNewsFeed(
        name="Example Hoops Wire",
        url=_FEED_URL,
        enabled=True,
        robots_checked_on=date(2026, 1, 3),
        robots_state="allowed",
        last_fetch_at=datetime(2026, 1, 3, 15, 0),
        last_status="ok",
    )
    session.add(feed)
    session.flush()
    intel_store.set_source_state(
        session,
        intel_store.news_source_key(feed.feed_id),
        "ok",
        now=datetime(2026, 1, 3, 15, 0),
        success=True,
        detail={"label": feed.name, "newItems": 2},
    )
    intel_news.add_pasted_link(
        session,
        title=f"{ctx.player_name(players[(first.home_team_id, 0)])} questionable to return",
        link="https://news.example.org/hoops/story-0",
        published_at=datetime(2026, 1, 3, 13, 0),
        source_name="Example Hoops Wire",
        team_ids=[first.home_team_id],
        player_ids=[players[(first.home_team_id, 0)]],
        now=stamp,
    )
    intel_news.add_pasted_link(
        session,
        title=(
            f"Previewing {ctx.team_abbr(first.away_team_id)} "
            f"at {ctx.team_abbr(first.home_team_id)}"
        ),
        link="https://news.example.org/hoops/story-1",
        published_at=datetime(2026, 1, 3, 11, 30),
        source_name="Example Hoops Wire",
        team_ids=[first.home_team_id, first.away_team_id],
        now=stamp,
    )
    session.commit()
    return {"first": first.game_id, "home": first.home_team_id, "away": first.away_team_id}


def _documents(session: Session, now: datetime, subjects: Mapping[str, Any]) -> dict[str, Any]:
    ctx = build_context(session, None, now=now)
    model = get_model(ctx)
    # Freeze the projections exactly as the scheduler would: refresh, then lock.
    written = ledger.refresh_latest(session, ctx, model)
    locked = ledger.lock_due(session, ctx, model)
    session.commit()
    assert written and locked, "the fixture build expected games inside the lock window"
    clear_memo()

    finals = sorted(
        (g for g in ctx.games if g.status == "final" and g.season_type == "Regular Season"),
        key=lambda g: (g.game_date, g.game_id),
    )
    played = finals[-1]
    # The newest day's finished games, all but the last, are frozen as the scheduler would have
    # frozen them thirty minutes before their deadlines, from the model's own rebuild at that time
    # (:func:`ledger.write_row` still refuses a lock at or after the deadline). That gives the
    # review fixture both of its blocks: projections that were frozen, and the last game's, which
    # was not and is rebuilt.
    for game in [g for g in finals if g.game_date == played.game_date][:-1]:
        built = model.reconstruct(ctx, game)
        stamp = ctx.deadline_of(game) - timedelta(minutes=30)
        ledger.write_row(
            session, ctx, game, built, kind="locked", computed_at=stamp, inputs_cutoff=stamp
        )
    session.commit()
    clear_memo()
    home = subjects["home"]
    docs: dict[str, Any] = {}
    docs["leagues"] = _leagues(session, now)
    docs["team_matchup"] = team_matchup(session, team=home, now=now)
    docs["game_matchup"] = team_matchup(session, game_id=played.game_id, now=now)
    docs["defense_by_position"] = defense_by_position(session, team=home, now=now)
    docs["defense_by_position_table"] = defense_by_position(session, team=None, now=now)
    docs["slate_projections"] = slate_projections(session, now=now)
    docs["game_projection_detail"] = game_projection(session, subjects["first"], now=now)
    docs["projection_review"] = projection_review(session, day=played.game_date, now=now)
    docs["availability_report"] = availability_report(session, include_news=True, now=now)
    with _statuses_refused():
        docs["availability_report_demo"] = availability_report(session, now=now)
    docs["availability_review_queue"] = availability_view.review_queue(ctx)
    docs["news"] = news_view.build_news(
        ctx,
        team_id=None,
        player_id=None,
        limit=None,
        freshness=sources.freshness_for(ctx, sources.news_keys(ctx)),
    )
    docs["sources"] = sources.build_source_list(ctx, sources.freshness_for(ctx, ()))
    docs["model_settings"] = settings_view.model_settings_payload(
        ctx, sources.freshness_for(ctx, ())
    )
    return {name: docs[name] for name in FIXTURE_NAMES}


@contextlib.contextmanager
def _statuses_refused() -> Iterator[None]:
    """Put the demo league's refusal back, for the one document that shows it."""
    original = availability_view.statuses_allowed
    availability_view.statuses_allowed = lambda session: False  # type: ignore[assignment]
    try:
        yield
    finally:
        availability_view.statuses_allowed = original  # type: ignore[assignment]


def _leagues(session: Session, now: datetime) -> list[dict[str, Any]]:
    from ..shared import league_registry

    return [sources.league_row(session, now), league_registry.league_entry("euroleague")]


def build_fixtures(now: datetime = FIXTURE_NOW) -> dict[str, Any]:
    """Every fixture document, built from a freshly seeded throwaway demo store at ``now``."""
    clear_memo()
    from ..shared import league_registry

    registered = league_registry.get("euroleague")
    league_registry.unregister("euroleague")  # the fixture must not depend on what is running
    try:
        with tempfile.TemporaryDirectory(prefix="hardwood-nba-fixtures-") as directory:
            engine = create_db_engine(f"sqlite:///{Path(directory) / 'nba_fixture.db'}")
            try:
                with _frozen_clock(now), _invented_players(), _statuses_shown():
                    init_db(engine)
                    with Session(engine, future=True) as session:
                        seed_database(
                            session,
                            as_of=FIXTURE_AS_OF,
                            seasons=list(FIXTURE_SEASONS),
                            games_per_team=FIXTURE_GAMES_PER_TEAM,
                            players_per_team=FIXTURE_PLAYERS_PER_TEAM,
                        )
                        session.commit()
                    with Session(engine, future=True) as session:
                        subjects = _author_intel(session, now)
                    with Session(engine, future=True) as session:
                        return _documents(session, now, subjects)
            finally:
                engine.dispose()
                clear_memo()
    finally:
        if registered is not None:
            league_registry.register(registered)


# --------------------------------------------------------------------------- files


def render(document: Any) -> str:
    """The canonical on-disk form: two-space indent, declaration order, trailing newline."""
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def write_fixtures(out_dir: Path, documents: Mapping[str, Any]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, document in documents.items():
        path = out_dir / f"{name}.json"
        path.write_text(render(document), encoding="utf-8")
        written.append(path)
    for stale in sorted(out_dir.glob("*.json")):
        if stale.stem not in documents:
            stale.unlink()
    return written


def check_fixtures(out_dir: Path, documents: Mapping[str, Any]) -> list[str]:
    """What differs between ``out_dir`` and a fresh build: empty when they are byte-identical."""
    problems: list[str] = []
    for name, document in documents.items():
        path = out_dir / f"{name}.json"
        if not path.exists():
            problems.append(f"{path.name}: missing")
        elif path.read_text(encoding="utf-8") != render(document):
            problems.append(f"{path.name}: differs from a fresh build")
    for extra in sorted(out_dir.glob("*.json")) if out_dir.exists() else []:
        if extra.stem not in documents:
            problems.append(f"{extra.name}: not produced by the exporter")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate the committed fixtures")
    mode.add_argument("--check", action="store_true", help="verify them (exit 1 on a difference)")
    parser.add_argument("--out", type=Path, default=FIXTURE_DIR)
    args = parser.parse_args(argv)
    documents = build_fixtures()
    if args.write:
        paths = write_fixtures(args.out, documents)
        print(f"wrote {len(paths)} fixtures to {args.out}")
        return 0
    problems = check_fixtures(args.out, documents)
    for problem in problems:
        print(problem, file=sys.stderr)
    print("fixtures are up to date" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
