"""The committed EuroLeague payload fixtures, built from the invented demo league.

``contracts/fixtures/leagues/el/*.json`` are one real response of every EuroLeague payload family
(design section 10.3), so a client (the Swift app, later) can decode against a recorded body
instead of the design's prose. They are *generated*, never hand-edited, and a test
(``tests/euroleague/test_el_fixtures.py``) rebuilds them and demands byte equality with what is
committed, the same discipline ``backend/nbastats/fixtures_export.py`` holds the NBA's to.

What makes them reproducible
----------------------------
* **An invented league.** Every club (``ZZA`` to ``ZZT``), player, venue and link comes from
  :mod:`nbastats.euroleague.demo`, which draws from its own seeded random stream and stamps every
  timestamp from a fixed moment. Nothing in them is real, which is also what lets them be
  committed to a public repository.
* **A held clock.** Every payload is built with ``now`` set to :data:`~nbastats.euroleague.demo.
  DEMO_AS_OF` (12 October 2026, 09:00 UTC: four rounds behind it, the fifth ahead), so
  ``generatedAt``, every age in minutes and every ``scheduled`` / ``resultPending`` status come out
  the same on every run.
* **A throwaway store.** The league is seeded into a fresh SQLite file in a temporary directory,
  so a developer's own EuroLeague store is never read and the fixtures never depend on one; the
  file's path appears in no payload.
* **The state is stated.** ``/meta`` and ``/sources`` report the bootstrap state; the exporter
  sets it to a ready, demo store for the build and puts back whatever was there.

The fixtures are NBA-free by construction (this package never imports the NBA's modules), and
``contracts/fixtures/leagues/`` sits below the non-recursive globs the 34 existing fixtures'
checks use, so adding these touches none of them.

Run ``python -m nbastats.euroleague.fixtures_export --write`` to regenerate, ``--check`` to verify
(exit status 1 on any difference).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy.orm import Session

from . import bootstrap, read
from .db import create_el_engine, init_el_db
from .demo import DEMO_AS_OF, seed_demo
from .read import availability as availability_module
from .read import games as games_module
from .read import method as method_module
from .read import news as news_module
from .read import review as review_module
from .read import round as round_module
from .read import sources as sources_module
from .read import stats as stats_module
from .read import teams as teams_module
from .read.queries import build_context, context_for_game
from .api.serializers import model_settings_payload

__all__ = [
    "FIXTURE_NOW",
    "FIXTURE_DIR",
    "FIXTURE_NAMES",
    "WIDGET_CONFIGS",
    "build_fixtures",
    "render",
    "write_fixtures",
    "check_fixtures",
    "main",
]

#: The moment every fixture is built at.
FIXTURE_NOW = DEMO_AS_OF.replace(tzinfo=timezone.utc)

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "contracts" / "fixtures" / "leagues" / "el"

#: The invented club, the neutral-site game (a result, with every column), and a game still to play.
_CLUB = "ZZA"
_PLAYED_GAME = "E2026-0021"
_UPCOMING_GAME = "E2026-R05-01"

FIXTURE_NAMES: tuple[str, ...] = (
    "availability_report",
    "box_score",
    "club_view",
    "defense_by_position",
    "defense_by_position_table",
    "game_matchup",
    "game_projection_detail",
    "games",
    "meta",
    "method",
    "model_settings",
    "news",
    "player_detail",
    "player_gamelog",
    "player_stats",
    "projection_review",
    "ratings",
    "review_queue",
    "round_scorers",
    "round_view",
    "round_view_complete",
    "slate_projections",
    "sources",
    "team_matchup",
    "teams",
    "widget_availability_report",
    "widget_defense_by_position",
    "widget_slate_projections",
    "widget_team_matchup",
)

#: The four league tiles' configurations for the invented league: the ``config`` of a dashboard
#: widget whose ``league`` is ``euroleague``. The ``widget_<kind>`` fixtures below are what the
#: tile resolvers return for exactly these (``tests/euroleague/test_el_widget_fixtures.py`` resolves
#: them through the real dashboard route and demands equality), which is why they are built here
#: with the builders' own keyword arguments and not copied from the route fixtures: availability,
#: for one, is the club's report for its next game here, where the route fixture is a whole round.
WIDGET_CONFIGS: dict[str, dict[str, Any]] = {
    "team_matchup": {
        "league": "euroleague",
        "team": None,
        "club": _CLUB,
        "opponent": None,
        "opponentClub": None,
        "season": "latest",
        "window": 5,
    },
    "defense_by_position": {
        "league": "euroleague",
        "team": None,
        "club": _CLUB,
        "season": "latest",
        "window": 0,
        "basis": "perGame",
        "scheme": "gfc",
    },
    "availability_report": {
        "league": "euroleague",
        "team": None,
        "club": _CLUB,
        "includeNews": False,
    },
    "slate_projections": {"league": "euroleague", "date": "next", "round": 0},
}


def _documents(session: Session, now: datetime) -> dict[str, Any]:
    ctx = build_context(session, None, now=now)
    keys = read.KEYS_MATCHUP
    stats_keys = sources_module.KEYS_STATS

    def fresh(sources: tuple[str, ...]) -> dict[str, Any]:
        return sources_module.freshness_for(ctx, sources)

    person = sorted(ctx.registrations_for_club(_CLUB), key=lambda r: r.person_code)[0].person_code
    played = context_for_game(session, _PLAYED_GAME, now=now)
    upcoming = ctx.game(_UPCOMING_GAME)
    docs: dict[str, Any] = {
        "meta": sources_module.build_meta(session, now=now),
        "teams": teams_module.build_teams(ctx, fresh(stats_keys)),
        "club_view": teams_module.build_club_view(ctx, _CLUB, fresh(keys)),
        "round_view": round_module.build_round_view(ctx, 5, fresh(keys)),
        "round_view_complete": round_module.build_round_view(ctx, 4, fresh(keys)),
        "round_scorers": round_module.build_round_scorers(ctx, 5, 3, fresh(keys)),
        "slate_projections": read.slate_projections(session, round_number="next", now=now),
        "game_projection_detail": round_module.projection_detail(ctx, upcoming, fresh(keys)),
        "projection_review": review_module.build_review(
            ctx, round_number=None, freshness=fresh(keys)
        ),
        "team_matchup": read.team_matchup(session, club=_CLUB, now=now),
        "game_matchup": read.team_matchup(session, game_id=_PLAYED_GAME, now=now),
        "defense_by_position": read.defense_by_position(session, club=_CLUB, now=now),
        "defense_by_position_table": read.defense_by_position(session, club=None, now=now),
        "availability_report": read.availability_report(session, round_number=5, now=now),
        "box_score": games_module.build_box_score(
            played, played.game(_PLAYED_GAME), sources_module.freshness_for(played, stats_keys)
        ),
        "games": games_module.build_games(
            ctx, round_number=5, club_code=None, phase=None, freshness=fresh(stats_keys)
        ),
        "player_stats": stats_module.build_stats_table(
            ctx,
            per_mode="PerGame",
            sort="pts",
            club_code=None,
            min_games=1,
            limit=10,
            freshness=fresh(stats_keys),
        ),
        "player_detail": stats_module.build_player_detail(ctx, person, fresh(keys)),
        "player_gamelog": stats_module.build_player_gamelog(ctx, person, None, fresh(stats_keys)),
        "ratings": teams_module.build_ratings(ctx, None, fresh(stats_keys)),
        "method": method_module.build_method(ctx, fresh(())),
        "sources": sources_module.build_source_list(ctx, fresh(())),
        "model_settings": model_settings_payload(ctx, fresh(())),
        "news": news_module.build_news(
            ctx,
            team_id=None,
            player_id=None,
            limit=None,
            freshness=fresh(sources_module.news_keys(ctx)),
        ),
        "review_queue": availability_module.build_review_queue(ctx),
        # The tiles: the same builders, called the way the tile resolvers call them.
        "widget_team_matchup": read.team_matchup(session, club=_CLUB, window=5, now=now),
        "widget_defense_by_position": read.defense_by_position(
            session, club=_CLUB, window=0, basis="perGame", scheme="gfc", now=now
        ),
        "widget_availability_report": read.availability_report(
            session, club=_CLUB, include_news=False, now=now
        ),
        "widget_slate_projections": read.slate_projections(
            session, round_number="next", now=now
        ),
    }
    return {name: docs[name] for name in FIXTURE_NAMES}


def build_fixtures(now: datetime = FIXTURE_NOW) -> dict[str, Any]:
    """Every fixture document, built from a freshly seeded throwaway demo store at ``now``."""
    previous = bootstrap.get_state()
    bootstrap.set_state(
        bootstrap.BootstrapResult(
            state=bootstrap.STATE_READY, kind="synthetic", is_demo=True, seeded_demo=True
        )
    )
    try:
        with tempfile.TemporaryDirectory(prefix="hardwood-el-fixtures-") as directory:
            engine = create_el_engine(f"sqlite:///{Path(directory) / 'el_fixture.db'}")
            try:
                init_el_db(engine)
                with Session(engine, future=True) as session:
                    seed_demo(session)
                    session.commit()
                with Session(engine, future=True) as session:
                    return _documents(session, now)
            finally:
                engine.dispose()
    finally:
        bootstrap.set_state(previous)


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
