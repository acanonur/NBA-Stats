"""The incremental path: stats arrive as each game finishes.

This is the module ``CONTRACT.md`` §8 describes, and the reason the app feels
live rather than nightly:

1. :func:`poll_finalized_games` reads the scoreboard for a date, writes the live
   state of every game on the slate, and returns the ids that *just* flipped to
   Final — the diff against what we already stored, not the whole slate.
2. :func:`ingest_game` pulls the traditional and advanced box for that **one**
   game, writes the player, team and game rows, recomputes the affected season,
   and bumps ``sync_version`` **once**. A client polling ``/v1/sync`` sees the
   new game within a poll interval of the final buzzer.
3. :func:`run_correction_window` re-pulls the last few days every night, because
   the league revises box scores after the fact. Every write is an upsert, so a
   correction overwrites yesterday instead of duplicating it.

Two calls per game, not two calls per player
--------------------------------------------
:func:`ingest_game` is the per-game path and costs two requests. It is only ever
used for a game that has just gone Final. Everything else — a whole day, a whole
season, a correction pass — goes through :func:`ingest_day`, which uses the bulk
endpoints: one ``LeagueGameLog`` for the team rows plus one ``PlayerGameLogs``
per measure type for every player in the league. That is three requests for an
entire night's slate, against roughly two per game. Backfilling with the per-game
endpoint would be tens of thousands of calls; see ``docs/DATA_SOURCES.md`` §5.

``data_through`` versus ``sync_version``
----------------------------------------
``sync_version`` increments per finalized game, so the client learns about each
game as it lands. ``data_through`` only moves to a date once **every** game on
that slate is final — a half-finished night must never look complete. The two
cursors answer different questions and are maintained separately, by
:func:`ingest_game` and :func:`move_data_through` respectively.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import catalog
from ..config import get_settings
from ..db import bump_sync_version, read_sync_state, utcnow
from ..models import (
    Game,
    IngestLog,
    Player,
    PlayerGameAdvanced,
    PlayerGameBasic,
    Team,
    TeamGame,
)
from . import aggregate, normalize
from .client import StatsClient

__all__ = [
    "ADVANCED_FROM_SEASON",
    "LIVE_SOURCE",
    "GameIngestResult",
    "DayIngestResult",
    "poll_finalized_games",
    "ingest_game",
    "ingest_day",
    "run_correction_window",
    "move_data_through",
    "season_has_advanced",
    "record_ingest_log",
    "write_player_basic_row",
    "write_player_advanced_row",
    "write_team_game_row",
    "ensure_player",
    "ensure_team",
]

logger = logging.getLogger("nbastats.ingest.daily")

#: Per-game advanced box scores, plus/minus and shot charts begin here. Before
#: this season the possession data was never recorded, so no advanced row exists —
#: it is not a gap we could fill with a better source.
ADVANCED_FROM_SEASON = "1996-97"

#: ``data_source`` stamped on rows this module writes.
LIVE_SOURCE = "nba_api"

#: Columns written to ``player_game_basic`` from a box score.
_BASIC_COLUMNS: tuple[str, ...] = (
    "minutes",
    "fgm",
    "fga",
    "fg3m",
    "fg3a",
    "ftm",
    "fta",
    "oreb",
    "dreb",
    "reb",
    "ast",
    "stl",
    "blk",
    "tov",
    "pf",
    "pts",
    "plus_minus",
)

#: Columns written to ``player_game_advanced``.
_ADVANCED_COLUMNS: tuple[str, ...] = (
    "off_rtg",
    "def_rtg",
    "net_rtg",
    "ast_pct",
    "ast_tov",
    "ast_ratio",
    "oreb_pct",
    "dreb_pct",
    "reb_pct",
    "tov_pct",
    "efg_pct",
    "ts_pct",
    "usg_pct",
    "pace",
    "poss",
    "pie",
)

#: Columns written to ``team_game`` from a box score or a league game log.
_TEAM_COLUMNS: tuple[str, ...] = (
    "minutes",
    "fgm",
    "fga",
    "fg3m",
    "fg3a",
    "ftm",
    "fta",
    "oreb",
    "dreb",
    "reb",
    "ast",
    "stl",
    "blk",
    "tov",
    "pf",
    "pts",
    "plus_minus",
    "fg_pct",
    "fg3_pct",
    "ft_pct",
)


def season_has_advanced(season: str | None) -> bool:
    """True when per-game advanced box scores exist for this season.

    Asked of the metric catalog rather than a hard-coded year, so the boundary
    has exactly one definition across the whole service.
    """
    if not season or not catalog.is_season_string(season):
        return False
    return catalog.metric_availability("off_rtg", season, "game") != "unavailable"


@dataclass
class GameIngestResult:
    """What ingesting one game did."""

    game_id: str
    season: str | None = None
    season_type: str | None = None
    game_date: date | None = None
    player_rows: int = 0
    advanced_rows: int = 0
    team_rows: int = 0
    changed: bool = False
    newly_final: bool = False
    first_ingest: bool = False
    sync_version: int | None = None
    skipped: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "gameId": self.game_id,
            "season": self.season,
            "seasonType": self.season_type,
            "playerRows": self.player_rows,
            "advancedRows": self.advanced_rows,
            "teamRows": self.team_rows,
            "changed": self.changed,
            "newlyFinal": self.newly_final,
            "firstIngest": self.first_ingest,
            "syncVersion": self.sync_version,
            "skipped": self.skipped,
        }


@dataclass
class DayIngestResult:
    """What ingesting one day in bulk did."""

    game_date: date
    seasons: set[tuple[str, str]] = field(default_factory=set)
    games: int = 0
    player_rows: int = 0
    advanced_rows: int = 0
    team_rows: int = 0
    changed_rows: int = 0
    finalized: list[str] = field(default_factory=list)
    data_through_moved: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.game_date.isoformat(),
            "seasons": sorted(f"{s}/{t}" for s, t in self.seasons),
            "games": self.games,
            "playerRows": self.player_rows,
            "advancedRows": self.advanced_rows,
            "teamRows": self.team_rows,
            "changedRows": self.changed_rows,
            "finalized": list(self.finalized),
            "dataThroughMoved": self.data_through_moved,
        }


def record_ingest_log(
    session: Session,
    job: str,
    *,
    season: str | None = None,
    status: str = "success",
    games: int = 0,
    rows: int = 0,
    error: str | None = None,
    started_at: Any = None,
) -> IngestLog:
    """Append one row to ``ingest_log`` — the operational record of every run."""
    entry = IngestLog(
        started_at=started_at or utcnow(),
        finished_at=utcnow(),
        job=job,
        season=season,
        status=status,
        games_written=games,
        rows_written=rows,
        error=error,
    )
    session.add(entry)
    session.flush()
    return entry


# --------------------------------------------------------------------------- #
# Reference rows
# --------------------------------------------------------------------------- #


def ensure_team(session: Session, side: normalize.BoxScoreTeam) -> None:
    """Make sure the franchise exists before a box-score row references it.

    SQLite does not enforce foreign keys by default but Postgres does, and the
    contract promises the same behaviour on both.
    """
    if side.team_id is None:
        return
    city = side.city or ""
    nickname = side.nickname or side.abbr or str(side.team_id)
    aggregate.upsert(
        session,
        Team,
        {"team_id": side.team_id},
        {
            "abbr": side.abbr or str(side.team_id),
            "name": f"{city} {nickname}".strip(),
            "city": city,
            "nickname": nickname,
        },
    )


def ensure_player(session: Session, line: Mapping[str, Any]) -> None:
    """Make sure a player exists, without overwriting a fuller biography."""
    player_id = line.get("player_id")
    if player_id is None:
        return
    first = line.get("first_name")
    last = line.get("last_name")
    full = " ".join(part for part in (first, last) if part) or line.get("player_name")
    if not full:
        return
    existing = session.get(Player, player_id)
    if existing is not None:
        # A box score knows less about a player than CommonAllPlayers does; only
        # fill in what is missing rather than flattening the bio every night.
        values = {}
        if not existing.jersey and line.get("jersey"):
            values["jersey"] = str(line["jersey"])
        if not existing.position and line.get("position"):
            values["position"] = str(line["position"])
        if values:
            aggregate.upsert(session, Player, {"player_id": player_id}, values)
        return
    session.add(
        Player(
            player_id=player_id,
            full_name=full,
            first_name=first,
            last_name=last,
            position=(line.get("position") or None),
            jersey=(str(line["jersey"]) if line.get("jersey") else None),
            is_active=True,
        )
    )


# --------------------------------------------------------------------------- #
# Polling
# --------------------------------------------------------------------------- #


def poll_finalized_games(
    session: Session,
    game_date: date,
    *,
    client: StatsClient | None = None,
    commit: bool = True,
) -> list[str]:
    """Return the games on ``game_date`` that have *newly* gone Final.

    The scoreboard is the cheap call (one request for the whole slate), so it is
    the one that runs on a short loop. Each poll writes the current live state —
    status, period, clock, score — so the next poll can diff against it; a game
    is "newly final" when the scoreboard says final and our stored row did not.

    That diff is what keeps the expensive per-game box-score calls down to one
    pair per game, and what makes the loop safe to run every minute.
    """
    api = client or StatsClient()
    payload = api.scoreboard(game_date)
    slate = normalize.normalize_scoreboard(payload, game_date=game_date)

    newly_final: list[str] = []
    for row in slate:
        game_id = row.get("game_id")
        if not game_id:
            continue
        stored = session.get(Game, game_id)
        was_final = stored is not None and stored.status == "final"
        is_final = row.get("status") == "final"

        season = row.get("season") or normalize.season_from_game_id(game_id)
        season_type = row.get("season_type") or "Regular Season"
        values: dict[str, Any] = {
            "game_date": row.get("game_date") or game_date,
            "season": season,
            "season_type": season_type,
            "home_team_id": row.get("home_team_id"),
            "away_team_id": row.get("away_team_id"),
            "home_pts": row.get("home_pts"),
            "away_pts": row.get("away_pts"),
            "status": row.get("status"),
            "period": row.get("period"),
            # A final game has no clock; a scheduled one has no clock yet.
            "clock": row.get("clock") if row.get("status") == "live" else None,
        }
        if values["home_team_id"] is None or values["away_team_id"] is None:
            logger.warning("scoreboard_row_without_teams game_id=%s", game_id)
            continue
        if is_final and (stored is None or stored.finalized_at is None):
            values["finalized_at"] = utcnow()

        aggregate.upsert(session, Game, {"game_id": game_id}, values)
        if is_final and not was_final:
            newly_final.append(game_id)

    if commit:
        session.commit()
    else:
        session.flush()

    logger.info(
        "event=poll date=%s slate=%d newly_final=%d ids=%s",
        game_date.isoformat(),
        len(slate),
        len(newly_final),
        ",".join(newly_final) or "-",
    )
    return newly_final


# --------------------------------------------------------------------------- #
# One game
# --------------------------------------------------------------------------- #


def write_player_basic_row(
    session: Session, game_id: str, line: Mapping[str, Any], season: str | None
) -> tuple[bool, bool]:
    """Write one traditional line. Returns ``(written, changed)``.

    Era rules apply here, not only to the season rollups: a 1995-96 box score has
    no plus/minus, so the column is written ``NULL`` even if the upstream row
    offers a zero for it.
    """
    player_id = line.get("player_id")
    team_id = line.get("team_id")
    if player_id is None or team_id is None:
        return False, False
    if line.get("minutes") is None:
        # A DNP has no line to store; the game log the client renders lists only
        # players who appeared, and a row of zeros would read as a bad night.
        return False, False

    values: dict[str, Any] = {
        "team_id": team_id,
        "started": bool(line.get("started")),
        "data_source": LIVE_SOURCE,
    }
    for column in _BASIC_COLUMNS:
        values[column] = line.get(column)
    if season:
        aggregate.apply_era(values, aggregate.era_plan("player_game", season, "game")[0])
    changed = aggregate.upsert(
        session, PlayerGameBasic, {"game_id": game_id, "player_id": player_id}, values
    )
    return True, changed


def write_player_advanced_row(
    session: Session, game_id: str, line: Mapping[str, Any], season: str | None = None
) -> tuple[bool, bool]:
    """Write one advanced line. Returns ``(written, changed)``."""
    player_id = line.get("player_id")
    if player_id is None or line.get("minutes") is None:
        return False, False
    values: dict[str, Any] = {"is_estimated": False, "data_source": LIVE_SOURCE}
    for column in _ADVANCED_COLUMNS:
        values[column] = line.get(column)
    if season:
        aggregate.apply_era(
            values, aggregate.era_plan("player_game_advanced", season, "game")[0]
        )
    if all(values[column] is None for column in _ADVANCED_COLUMNS):
        return False, False
    changed = aggregate.upsert(
        session, PlayerGameAdvanced, {"game_id": game_id, "player_id": player_id}, values
    )
    return True, changed


def write_team_game_row(
    session: Session,
    game_id: str,
    stats: Mapping[str, Any],
    *,
    is_home: bool,
    opponent: Mapping[str, Any] | None,
    advanced: Mapping[str, Any] | None = None,
) -> tuple[bool, bool]:
    """Write one team's side of a game, with its four factors and ratings."""
    team_id = stats.get("team_id")
    if team_id is None:
        return False, False

    values: dict[str, Any] = {"is_home": is_home, "data_source": LIVE_SOURCE}
    for column in _TEAM_COLUMNS:
        if column in stats:
            values[column] = stats.get(column)
    own_points = stats.get("pts")
    opponent_points = opponent.get("pts") if opponent else None
    values["opp_pts"] = opponent_points
    if own_points is not None and opponent_points is not None:
        values["won"] = own_points > opponent_points

    subject = {**stats, "min": stats.get("minutes"), "season": stats.get("season")}
    opponent_row = (
        {**opponent, "min": opponent.get("minutes"), "season": stats.get("season")}
        if opponent
        else None
    )
    values.update(aggregate.derive_team_values(subject, opponent_row))
    if advanced:
        # The league's own measured values win for a single game: they come from
        # the play-by-play, not from a box-score estimate.
        for column in ("off_rtg", "def_rtg", "net_rtg", "pace", "poss"):
            if advanced.get(column) is not None:
                values[column] = advanced[column]

    season = stats.get("season")
    if season:
        aggregate.apply_era(values, aggregate.era_plan("team_game", season, "game")[0])
    changed = aggregate.upsert(
        session, TeamGame, {"game_id": game_id, "team_id": team_id}, values
    )
    return True, changed


def ingest_game(
    session: Session,
    game_id: str,
    *,
    client: StatsClient | None = None,
    game_date: date | None = None,
    season: str | None = None,
    season_type: str | None = None,
    reaggregate: bool = True,
    commit: bool = True,
) -> GameIngestResult:
    """Ingest one finished game: traditional + advanced box, then one sync bump.

    This is what makes stats appear per match rather than per night. It writes
    ``player_game_basic``, ``player_game_advanced`` (1996-97 onward only),
    ``team_game`` and the ``games`` row, recomputes the season's aggregates, and
    increments ``sync_version`` exactly once — and only when something actually
    changed. Calling it twice for an unchanged game writes nothing and leaves the
    version alone, so a restarted worker cannot inflate the client's poll.

    The date comes from the stored ``games`` row (written by
    :func:`poll_finalized_games`) or from ``game_date``; a V3 box score does not
    reliably carry one, and inventing it would corrupt every date-scoped query.
    """
    api = client or StatsClient()
    result = GameIngestResult(game_id=game_id)

    stored = session.get(Game, game_id)
    result.season = season or (stored.season if stored else None) or normalize.season_from_game_id(
        game_id
    )
    result.season_type = (
        season_type
        or (stored.season_type if stored else None)
        or normalize.season_type_from_game_id(game_id)
        or "Regular Season"
    )
    result.game_date = game_date or (stored.game_date if stored else None)
    # Two different questions: whether the scoreboard had already flipped this
    # game to Final (it usually has, because the poll writes that first), and
    # whether we have ever pulled its box score. Only the second one counts a game.
    result.newly_final = stored is None or stored.status != "final"
    result.first_ingest = stored is None or stored.ingested_at is None

    traditional = normalize.normalize_box_score(
        api.box_score_traditional(game_id), "traditional"
    )
    if result.game_date is None:
        result.game_date = traditional.game_date
    if result.game_date is None:
        raise ValueError(
            f"No date known for game {game_id}: poll the scoreboard first or pass "
            "game_date. A V3 box score does not carry the scheduling day."
        )

    advanced_by_player: dict[int, Mapping[str, Any]] = {}
    advanced_team: dict[int, Mapping[str, Any]] = {}
    if season_has_advanced(result.season):
        advanced = normalize.normalize_box_score(
            api.box_score_advanced(game_id), "advanced"
        )
        for line in advanced.players():
            if line.get("player_id") is not None:
                advanced_by_player[int(line["player_id"])] = line
        for side in advanced.teams:
            if side.team_id is not None:
                advanced_team[side.team_id] = side.stats
    else:
        result.skipped = f"advanced box unavailable before {ADVANCED_FROM_SEASON}"

    changed = False
    for side in traditional.teams:
        ensure_team(session, side)

    home, away = traditional.home, traditional.away
    for side in traditional.teams:
        for line in side.players:
            ensure_player(session, line)
    session.flush()

    for side, other in ((home, away), (away, home)):
        for line in side.players:
            written, line_changed = write_player_basic_row(session, game_id, line, result.season)
            result.player_rows += int(written)
            changed = changed or line_changed

            player_id = line.get("player_id")
            advanced_line = (
                advanced_by_player.get(int(player_id)) if player_id is not None else None
            )
            if advanced_line is not None:
                written_adv, adv_changed = write_player_advanced_row(
                    session, game_id, advanced_line, result.season
                )
                result.advanced_rows += int(written_adv)
                changed = changed or adv_changed

        team_written, team_changed = write_team_game_row(
            session,
            game_id,
            {**side.stats, "season": result.season},
            is_home=side.is_home,
            opponent=other.stats,
            advanced=advanced_team.get(side.team_id) if side.team_id else None,
        )
        result.team_rows += int(team_written)
        changed = changed or team_changed

    game_values: dict[str, Any] = {
        "game_date": result.game_date,
        "season": result.season,
        "season_type": result.season_type,
        "home_team_id": home.team_id,
        "away_team_id": away.team_id,
        "home_pts": home.stats.get("pts"),
        "away_pts": away.stats.get("pts"),
        "status": "final",
        "clock": None,
        "data_source": LIVE_SOURCE,
        "ingested_at": utcnow(),
    }
    if stored is None or stored.finalized_at is None:
        game_values["finalized_at"] = utcnow()
    # ingested_at moves on every pass, so it must not by itself count as a change.
    game_changed = aggregate.upsert(
        session,
        Game,
        {"game_id": game_id},
        {key: value for key, value in game_values.items() if key != "ingested_at"},
    )
    aggregate.upsert(session, Game, {"game_id": game_id}, {"ingested_at": utcnow()})
    changed = changed or game_changed or result.first_ingest

    result.changed = changed
    if changed and reaggregate and result.season:
        aggregate.recompute_season(session, result.season, result.season_type)

    if changed:
        state = read_sync_state(session)
        result.sync_version = bump_sync_version(
            session,
            games_ingested=(state.games_ingested or 0) + (1 if result.first_ingest else 0),
            note=f"game {game_id}",
            commit=False,
        )
    else:
        result.sync_version = read_sync_state(session).sync_version

    if commit:
        session.commit()
    else:
        session.flush()

    logger.info(
        "event=ingest_game game_id=%s season=%s players=%d advanced=%d teams=%d "
        "changed=%s sync_version=%s%s",
        game_id,
        result.season,
        result.player_rows,
        result.advanced_rows,
        result.team_rows,
        result.changed,
        result.sync_version,
        f" skipped={result.skipped}" if result.skipped else "",
    )
    return result


# --------------------------------------------------------------------------- #
# One day, in bulk
# --------------------------------------------------------------------------- #


def _seasons_on(session: Session, game_date: date) -> set[tuple[str, str]]:
    rows = session.execute(
        select(Game.season, Game.season_type).where(Game.game_date == game_date).distinct()
    ).all()
    return {(season, season_type) for season, season_type in rows if season and season_type}


def ingest_day(
    session: Session,
    game_date: date,
    *,
    client: StatsClient | None = None,
    seasons: Iterable[tuple[str, str]] | None = None,
    reaggregate: bool = True,
    commit: bool = True,
) -> DayIngestResult:
    """Ingest a whole day with the bulk endpoints — the efficient form.

    One ``LeagueGameLog`` call gives every team's line for the date; one
    ``PlayerGameLogs`` call per measure type gives **every player in the league**
    for that date. Three requests cover a full slate no matter how many games it
    holds, which is why a backfill or a correction pass never loops the per-game
    box-score endpoint.

    Games and their statuses come from the scoreboard first, so the slate is
    known even for a date nobody has polled yet.
    """
    api = client or StatsClient()
    result = DayIngestResult(game_date=game_date)

    finalized = poll_finalized_games(session, game_date, client=api, commit=False)
    result.finalized = list(finalized)

    scopes = set(seasons) if seasons else _seasons_on(session, game_date)
    if not scopes:
        logger.info("event=ingest_day date=%s games=0", game_date.isoformat())
        if commit:
            session.commit()
        return result
    result.seasons = scopes

    games_on_date = {
        game.game_id: game
        for game in session.execute(select(Game).where(Game.game_date == game_date))
        .scalars()
        .all()
    }
    result.games = len(games_on_date)
    final_ids = {
        game_id for game_id, game in games_on_date.items() if game.status == "final"
    }

    for season, season_type in sorted(scopes):
        team_rows = normalize.normalize_league_game_log(
            api.league_game_log(
                season, season_type, date_from=game_date, date_to=game_date
            ),
            season=season,
            season_type=season_type,
        )
        by_game: dict[str, list[dict[str, Any]]] = {}
        for row in team_rows:
            game_id = row.get("game_id")
            if game_id in final_ids:
                by_game.setdefault(str(game_id), []).append(row)

        for game_id, sides in by_game.items():
            for index, side in enumerate(sides):
                other = sides[1 - index] if len(sides) == 2 else None
                written, changed = write_team_game_row(
                    session,
                    game_id,
                    {**side, "season": season},
                    is_home=bool(side.get("is_home")),
                    opponent=other,
                )
                result.team_rows += int(written)
                result.changed_rows += int(changed)

        base_rows = normalize.normalize_player_game_logs(
            api.player_game_logs(
                season, season_type, "Base", date_from=game_date, date_to=game_date
            ),
            "Base",
            season=season,
            season_type=season_type,
        )
        for row in base_rows:
            game_id = str(row.get("game_id") or "")
            if game_id not in final_ids:
                continue
            ensure_player(session, row)
            written, changed = write_player_basic_row(session, game_id, row, season)
            result.player_rows += int(written)
            result.changed_rows += int(changed)

        if season_has_advanced(season):
            advanced_rows = normalize.normalize_player_game_logs(
                api.player_game_logs(
                    season,
                    season_type,
                    "Advanced",
                    date_from=game_date,
                    date_to=game_date,
                ),
                "Advanced",
                season=season,
                season_type=season_type,
            )
            for row in advanced_rows:
                game_id = str(row.get("game_id") or "")
                if game_id not in final_ids:
                    continue
                written, changed = write_player_advanced_row(session, game_id, row, season)
                result.advanced_rows += int(written)
                result.changed_rows += int(changed)

    session.flush()
    if reaggregate:
        aggregate.recompute_seasons(session, [(s, t) for s, t in sorted(scopes)])

    result.data_through_moved = move_data_through(session, game_date, commit=False)
    if result.changed_rows or finalized:
        bump_sync_version(
            session,
            note=f"day {game_date.isoformat()}",
            commit=False,
        )

    if commit:
        session.commit()

    logger.info(
        "event=ingest_day date=%s games=%d players=%d advanced=%d teams=%d changed=%d "
        "data_through_moved=%s",
        game_date.isoformat(),
        result.games,
        result.player_rows,
        result.advanced_rows,
        result.team_rows,
        result.changed_rows,
        result.data_through_moved,
    )
    return result


def move_data_through(session: Session, game_date: date, *, commit: bool = True) -> bool:
    """Advance ``data_through`` to ``game_date`` if the whole slate is final.

    A night with one game still in progress is not a complete day, and the
    contract's ``dataThrough`` promises completeness. The cursor also only ever
    moves forward, so re-ingesting an old date cannot make the service look stale.
    """
    games = (
        session.execute(select(Game).where(Game.game_date == game_date)).scalars().all()
    )
    if not games or any(game.status != "final" for game in games):
        return False

    state = read_sync_state(session)
    if state.data_through is not None and state.data_through >= game_date:
        return False
    state.data_through = game_date
    state.last_success_at = utcnow()
    session.flush()
    if commit:
        session.commit()
    logger.info("event=data_through date=%s games=%d", game_date.isoformat(), len(games))
    return True


# --------------------------------------------------------------------------- #
# Corrections
# --------------------------------------------------------------------------- #


def run_correction_window(
    session: Session,
    days: int | None = None,
    *,
    client: StatsClient | None = None,
    end_date: date | None = None,
    commit: bool = True,
) -> list[DayIngestResult]:
    """Re-pull the last ``days`` days, because the league corrects box scores.

    A stat line can change days after the game — a rebound reassigned, an assist
    added — and a pipeline that only ever inserts would keep the original
    forever. Every write on this path is an upsert, so the corrected value
    replaces the stored one and the season aggregates follow it; nothing is
    duplicated and nothing is deleted.

    Defaults to ``CORRECTION_WINDOW_DAYS`` (3) ending at ``data_through``, or
    today when the store is empty.
    """
    settings = get_settings()
    window = settings.correction_window_days if days is None else days
    if window < 1:
        return []

    state = read_sync_state(session)
    last = end_date or state.data_through or date.today()
    started = utcnow()
    results: list[DayIngestResult] = []
    rows = 0

    for offset in range(window - 1, -1, -1):
        day = last - timedelta(days=offset)
        outcome = ingest_day(session, day, client=client, commit=False)
        results.append(outcome)
        rows += outcome.player_rows + outcome.team_rows + outcome.advanced_rows

    record_ingest_log(
        session,
        "correction",
        status="success",
        games=sum(item.games for item in results),
        rows=rows,
        started_at=started,
    )
    if commit:
        session.commit()

    logger.info(
        "event=correction_window days=%d through=%s changed=%d",
        window,
        last.isoformat(),
        sum(item.changed_rows for item in results),
    )
    return results
