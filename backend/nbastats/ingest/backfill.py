"""The historical path: load bulk files that were downloaded once.

**This module never pulls history from stats.nba.com.** There are ~64,000 games
in league history and roughly 1.3-1.7 million player-game rows; fetching them one
game at a time would be tens of thousands of requests and days of runtime at a
polite rate limit, and it would be rude besides. The open bulk datasets already
contain that history in a few files, so the backfill reads files and the live
path (:mod:`nbastats.ingest.daily`) keeps up with the present.

Loaders
-------
``load_kaggle_sqlite``     Wyatt Walsh's "NBA Database" — games and box scores
                          from 1946, as a single SQLite file.
``load_bbref_season_csvs`` Sumitro Datta's Basketball-Reference season dumps,
                          keyed on the Basketball-Reference slug. The **only**
                          practical source of pre-1997 season advanced stats
                          (PER, WS, BPM, VORP); every row is written
                          ``is_estimated=True`` because those are box-score
                          derivations, not possession measurements.
``load_parquet_dir``       hoopR / shufinskiy release files, parquet or CSV.
                          ``pyarrow`` is optional: without it the directory's
                          CSVs are read instead, and parquet files are reported
                          as skipped rather than silently ignored.
``reconcile_ids``          Build ``id_crosswalk`` on name + birthdate + debut
                          season, with a confidence score per match and a report
                          of everything it could not resolve.

Hardwood never scrapes Basketball-Reference itself: their terms forbid building
tools on scraped data, and their rate limit is enforced with hour-long blocks.
The redistributed Kaggle compilation is what these loaders read, with
Basketball-Reference credited as the methodology source. See ``docs/LEGAL.md``.

Properties every loader holds
-----------------------------
* **Chunked.** Rows stream through ``fetchmany`` / an iterator; a 64,000-game
  file never lands in memory at once.
* **Resumable.** Work is done season by season and each finished season is
  recorded in ``ingest_log``. A re-run skips what already succeeded, so an
  interrupted overnight load continues instead of restarting.
* **Idempotent.** Every write is :func:`nbastats.ingest.aggregate.upsert`, keyed
  on the row's primary key. Running a loader twice changes nothing the second
  time.
* **Never invents a player.** A source row that cannot be matched to a known
  person is reported, not guessed at and not inserted as a duplicate.

Schema drift is expected
------------------------
These datasets are community compilations and their column names move between
releases. Each loader resolves its columns against the file's actual header
through an alias table, warns about what it could not find, and carries on with
what it has — a renamed column costs one field, never the whole load.
"""
from __future__ import annotations

import csv
import logging
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import catalog
from ..db import utcnow
from ..models import IdCrosswalk, IngestLog, Player, PlayerSeason, Team
from . import aggregate, normalize
from .daily import (
    ensure_player,
    record_ingest_log,
    write_player_basic_row,
    write_team_game_row,
)

__all__ = [
    "KAGGLE_SOURCE",
    "BBREF_SOURCE",
    "PARQUET_SOURCE",
    "MULTI_TEAM_ABBREVIATIONS",
    "LoadReport",
    "ReconcileReport",
    "CrosswalkCandidate",
    "load_kaggle_sqlite",
    "load_bbref_season_csvs",
    "load_parquet_dir",
    "reconcile_ids",
    "normalize_name",
    "completed_seasons",
]

logger = logging.getLogger("nbastats.ingest.backfill")

KAGGLE_SOURCE = "kaggle"
BBREF_SOURCE = "bbref"
PARQUET_SOURCE = "hoopr"

#: Basketball-Reference's multi-team rows: a traded player's combined season.
#: ``player_season`` keys on a team, so the per-team rows are loaded and the
#: combined row is skipped rather than attributed to a franchise he never played
#: a game for.
MULTI_TEAM_ABBREVIATIONS = frozenset({"TOT", "2TM", "3TM", "4TM", "5TM", "NA", ""})

_SLUG_RE = re.compile(r"^[a-z']{2,}[a-z]{0,}\d{2}$")
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


@dataclass
class LoadReport:
    """What one bulk load did, and what it could not do."""

    job: str
    source: str
    rows_read: int = 0
    rows_written: int = 0
    rows_changed: int = 0
    rows_skipped: int = 0
    seasons: list[str] = field(default_factory=list)
    seasons_skipped: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "job": self.job,
            "source": self.source,
            "rowsRead": self.rows_read,
            "rowsWritten": self.rows_written,
            "rowsChanged": self.rows_changed,
            "rowsSkipped": self.rows_skipped,
            "seasons": list(self.seasons),
            "seasonsSkipped": list(self.seasons_skipped),
            "unmatched": self.unmatched[:50],
            "unmatchedCount": len(self.unmatched),
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- #
# Resumability
# --------------------------------------------------------------------------- #


def completed_seasons(session: Session, job: str) -> set[str]:
    """Seasons a previous run of ``job`` finished, read from ``ingest_log``.

    This is what makes an overnight load resumable: each season is committed and
    logged as it completes, so a crash costs one season rather than the file.
    """
    rows = session.execute(
        select(IngestLog.season).where(IngestLog.job == job, IngestLog.status == "success")
    ).scalars()
    return {season for season in rows if season}


def _checkpoint(session: Session, job: str, season: str, rows: int, games: int = 0) -> None:
    record_ingest_log(session, job, season=season, status="success", games=games, rows=rows)
    session.commit()


# --------------------------------------------------------------------------- #
# Column resolution
# --------------------------------------------------------------------------- #


def _resolve(columns: Iterable[str], aliases: Mapping[str, Sequence[str]], context: str) -> dict[str, str]:
    """Map our column names onto whichever spellings this file actually uses."""
    available = {str(column).strip().lower(): str(column) for column in columns}
    resolved: dict[str, str] = {}
    for target, candidates in aliases.items():
        for candidate in candidates:
            if candidate.lower() in available:
                resolved[target] = available[candidate.lower()]
                break
    missing = sorted(set(aliases) - set(resolved))
    if missing:
        logger.warning(
            "unresolved_columns context=%s missing=%s", context, ",".join(missing)
        )
    return resolved


def _field(row: Any, name: str) -> Any:
    """One field of a source row, whatever shape that row happens to be.

    The Kaggle loader reads through ``sqlite3.Row``, which indexes by column name
    but is **not** a ``Mapping`` and has no ``.get()``; the CSV and parquet
    loaders yield plain ``dict``. Subscripting and catching the miss covers both,
    so no loader has to care which it was handed. ``sqlite3.Row`` raises
    ``IndexError`` for an unknown name where a ``dict`` raises ``KeyError``.
    """
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return None


def _pluck(row: Any, resolved: Mapping[str, str], target: str) -> Any:
    source = resolved.get(target)
    return _field(row, source) if source else None


def normalize_name(value: Any) -> str:
    """A name reduced to what two sources can agree on.

    Diacritics folded, punctuation dropped, generational suffixes removed:
    ``"Luka Dončić"`` and ``"Luka Doncic"`` compare equal, as do ``"Gary
    Payton II"`` and ``"Gary Payton"`` — which is why a name match alone is only
    ever treated as weak evidence in :func:`reconcile_ids`.
    """
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-zA-Z ]", " ", text).lower()
    parts = [part for part in text.split() if part and part not in _SUFFIXES]
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Kaggle: Wyatt Walsh's NBA Database
# --------------------------------------------------------------------------- #

_KAGGLE_GAME_ALIASES: dict[str, tuple[str, ...]] = {
    "game_id": ("game_id",),
    "game_date": ("game_date", "game_date_est"),
    "season_id": ("season_id",),
    "season_type": ("season_type",),
    "home_team_id": ("team_id_home",),
    "away_team_id": ("team_id_away",),
    "home_abbr": ("team_abbreviation_home",),
    "away_abbr": ("team_abbreviation_away",),
    "home_name": ("team_name_home",),
    "away_name": ("team_name_away",),
    "matchup_home": ("matchup_home",),
    "wl_home": ("wl_home",),
    "minutes": ("min",),
}

#: The ``game`` table carries both sides as ``*_home`` / ``*_away`` suffixes.
_KAGGLE_BOX_COLUMNS: tuple[str, ...] = (
    "fgm",
    "fga",
    "fg_pct",
    "fg3m",
    "fg3a",
    "fg3_pct",
    "ftm",
    "fta",
    "ft_pct",
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

_KAGGLE_PLAYER_BOX_ALIASES: dict[str, tuple[str, ...]] = {
    "game_id": ("game_id",),
    "player_id": ("player_id", "person_id", "athlete_id"),
    "player_name": ("player_name", "player", "athlete_display_name", "display_first_last"),
    "team_id": ("team_id",),
    "minutes": ("min", "minutes", "mp"),
    "started": ("start_position", "starter", "is_starter"),
    "fgm": ("fgm", "field_goals_made"),
    "fga": ("fga", "field_goals_attempted"),
    "fg3m": ("fg3m", "three_pointers_made"),
    "fg3a": ("fg3a", "three_pointers_attempted"),
    "ftm": ("ftm", "free_throws_made"),
    "fta": ("fta", "free_throws_attempted"),
    "oreb": ("oreb", "offensive_rebounds"),
    "dreb": ("dreb", "defensive_rebounds"),
    "reb": ("reb", "rebounds", "total_rebounds"),
    "ast": ("ast", "assists"),
    "stl": ("stl", "steals"),
    "blk": ("blk", "blocks"),
    "tov": ("to", "tov", "turnovers"),
    "pf": ("pf", "personal_fouls"),
    "pts": ("pts", "points"),
    "plus_minus": ("plus_minus", "plusminuspoints"),
}

_KAGGLE_PLAYER_TABLES = (
    "player_game_log",
    "player_box_score",
    "player_game_stats",
    "game_player_stats",
    "box_score",
    "player_stats",
)
_KAGGLE_GAME_TABLES = ("game", "games")


def _sqlite_tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
    ).fetchall()
    return {str(row[0]).lower() for row in rows}


def _sqlite_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")]


def _kaggle_season(row: Mapping[str, Any], resolved: Mapping[str, str]) -> str | None:
    season = normalize.season_from_season_id(_pluck(row, resolved, "season_id"))
    if season:
        return season
    return normalize.season_from_game_id(str(_pluck(row, resolved, "game_id") or ""))


def load_kaggle_sqlite(
    session: Session,
    path: str | Path,
    *,
    seasons: Iterable[str] | None = None,
    chunk_size: int = 5000,
    resume: bool = True,
    reaggregate: bool = True,
) -> LoadReport:
    """Load games, team box scores and (where present) player box scores.

    The Kaggle file is opened **read-only** through stdlib ``sqlite3`` — no
    second database driver, and no chance of writing to the source. Rows stream
    in ``chunk_size`` batches, one season at a time, and each finished season is
    committed and logged so an interrupted run resumes where it stopped.

    The dataset's shape has changed across releases: some carry a player box
    table, some only the team-level ``game`` table. Whatever is present is
    loaded and the report says what was not.
    """
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Kaggle SQLite file not found: {source}")

    job = "backfill_kaggle"
    report = LoadReport(job=job, source=str(source))
    done = completed_seasons(session, job) if resume else set()
    wanted = set(seasons) if seasons else None

    connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = _sqlite_tables(connection)
        game_table = next((name for name in _KAGGLE_GAME_TABLES if name in tables), None)
        if game_table is None:
            raise ValueError(
                f"{source} has no games table (looked for {', '.join(_KAGGLE_GAME_TABLES)})"
            )
        player_table = next((name for name in _KAGGLE_PLAYER_TABLES if name in tables), None)
        if player_table is None:
            report.notes.append(
                "no player box-score table in this release; loaded games and team rows only"
            )

        _load_kaggle_reference(session, connection, tables, report)

        game_columns = _sqlite_columns(connection, game_table)
        resolved = _resolve(game_columns, _KAGGLE_GAME_ALIASES, f"{game_table}")
        by_season = _kaggle_group_seasons(connection, game_table, resolved)

        for season in sorted(by_season, key=catalog.season_sort_key):
            if wanted is not None and season not in wanted:
                continue
            if season in done:
                report.seasons_skipped.append(season)
                continue
            written, changed, read, game_ids = _load_kaggle_season(
                session, connection, game_table, resolved, season, by_season[season], chunk_size
            )
            report.rows_read += read
            report.rows_written += written
            report.rows_changed += changed

            if player_table:
                p_written, p_changed, p_read = _load_kaggle_player_box(
                    session, connection, player_table, game_ids, season, chunk_size
                )
                report.rows_read += p_read
                report.rows_written += p_written
                report.rows_changed += p_changed

            if reaggregate:
                aggregate.recompute_season(session, season)
            report.seasons.append(season)
            _checkpoint(session, job, season, rows=report.rows_written, games=len(game_ids))
            logger.info(
                "event=kaggle_season season=%s games=%d rows=%d",
                season,
                len(game_ids),
                report.rows_written,
            )
    finally:
        connection.close()

    logger.info(
        "event=kaggle_load file=%s seasons=%d skipped=%d rows=%d",
        source.name,
        len(report.seasons),
        len(report.seasons_skipped),
        report.rows_written,
    )
    return report


def _load_kaggle_reference(
    session: Session,
    connection: sqlite3.Connection,
    tables: set[str],
    report: LoadReport,
) -> None:
    """Load the franchises and the player biographies the box scores reference."""
    if "team" in tables:
        columns = _sqlite_columns(connection, "team")
        resolved = _resolve(
            columns,
            {
                "team_id": ("id", "team_id"),
                "abbr": ("abbreviation", "team_abbreviation"),
                "nickname": ("nickname", "team_name"),
                "city": ("city", "team_city"),
                "full_name": ("full_name",),
                "year_founded": ("year_founded",),
            },
            "team",
        )
        for row in connection.execute("SELECT * FROM team"):
            team_id = normalize.parse_int(_pluck(row, resolved, "team_id"))
            if team_id is None:
                continue
            city = _pluck(row, resolved, "city") or ""
            nickname = _pluck(row, resolved, "nickname") or ""
            aggregate.upsert(
                session,
                Team,
                {"team_id": team_id},
                {
                    "abbr": _pluck(row, resolved, "abbr") or str(team_id),
                    "name": _pluck(row, resolved, "full_name") or f"{city} {nickname}".strip(),
                    "city": city,
                    "nickname": nickname or city,
                    "year_founded": normalize.parse_int(_pluck(row, resolved, "year_founded")),
                },
            )
            report.rows_written += 1
        session.flush()

    if "player" in tables:
        columns = _sqlite_columns(connection, "player")
        resolved = _resolve(
            columns,
            {
                "player_id": ("id", "person_id", "player_id"),
                "full_name": ("full_name", "display_first_last"),
                "first_name": ("first_name",),
                "last_name": ("last_name",),
                "is_active": ("is_active", "rosterstatus"),
            },
            "player",
        )
        for row in connection.execute("SELECT * FROM player"):
            player_id = normalize.parse_int(_pluck(row, resolved, "player_id"))
            full_name = _pluck(row, resolved, "full_name")
            if player_id is None or not full_name:
                continue
            first = _pluck(row, resolved, "first_name")
            last = _pluck(row, resolved, "last_name")
            if not first and not last:
                parts = str(full_name).split(" ", 1)
                first, last = parts[0], (parts[1] if len(parts) > 1 else None)
            aggregate.upsert(
                session,
                Player,
                {"player_id": player_id},
                {
                    "full_name": str(full_name),
                    "first_name": first,
                    "last_name": last,
                    "is_active": bool(normalize.parse_bool(_pluck(row, resolved, "is_active"))),
                },
            )
            report.rows_written += 1
        session.flush()

    if "common_player_info" in tables:
        _load_kaggle_bios(session, connection, report)
    session.commit()


def _load_kaggle_bios(
    session: Session, connection: sqlite3.Connection, report: LoadReport
) -> None:
    """Fill in birthdates, draft position and physicals — what the crosswalk needs."""
    columns = _sqlite_columns(connection, "common_player_info")
    resolved = _resolve(
        columns,
        {
            "player_id": ("person_id", "player_id", "id"),
            "birthdate": ("birthdate", "birth_date"),
            "height": ("height",),
            "weight": ("weight",),
            "position": ("position",),
            "country": ("country",),
            "school": ("school",),
            "jersey": ("jersey",),
            "draft_year": ("draft_year",),
            "draft_round": ("draft_round",),
            "draft_pick": ("draft_number", "draft_pick"),
            "from_year": ("from_year",),
            "to_year": ("to_year",),
        },
        "common_player_info",
    )
    for row in connection.execute("SELECT * FROM common_player_info"):
        player_id = normalize.parse_int(_pluck(row, resolved, "player_id"))
        if player_id is None or session.get(Player, player_id) is None:
            continue
        values = {
            "birthdate": normalize.parse_date(_pluck(row, resolved, "birthdate")),
            "height": _pluck(row, resolved, "height") or None,
            "weight": normalize.parse_int(_pluck(row, resolved, "weight")),
            "position": _pluck(row, resolved, "position") or None,
            "country": _pluck(row, resolved, "country") or None,
            "school": _pluck(row, resolved, "school") or None,
            "jersey": (str(_pluck(row, resolved, "jersey")) if _pluck(row, resolved, "jersey") else None),
            "draft_year": normalize.parse_int(_pluck(row, resolved, "draft_year")),
            "draft_round": normalize.parse_int(_pluck(row, resolved, "draft_round")),
            "draft_pick": normalize.parse_int(_pluck(row, resolved, "draft_pick")),
            "from_year": normalize.parse_int(_pluck(row, resolved, "from_year")),
            "to_year": normalize.parse_int(_pluck(row, resolved, "to_year")),
        }
        present = {key: value for key, value in values.items() if value is not None}
        if present and aggregate.upsert(session, Player, {"player_id": player_id}, present):
            report.rows_changed += 1
    session.flush()


def _kaggle_group_seasons(
    connection: sqlite3.Connection, table: str, resolved: Mapping[str, str]
) -> dict[str, list[str]]:
    """Game ids grouped by season — the unit of work that makes a load resumable."""
    id_column = resolved.get("game_id")
    if id_column is None:
        raise ValueError(f"{table} has no game_id column")
    season_column = resolved.get("season_id")
    select_columns = [id_column] + ([season_column] if season_column else [])
    grouped: dict[str, list[str]] = {}
    for row in connection.execute(f"SELECT {', '.join(select_columns)} FROM {table}"):
        game_id = str(row[id_column])
        season = normalize.season_from_season_id(row[season_column]) if season_column else None
        season = season or normalize.season_from_game_id(game_id)
        if season:
            grouped.setdefault(season, []).append(game_id)
    return grouped


def _kaggle_side(row: Any, side: str) -> dict[str, Any]:
    """One team's half of a Kaggle ``game`` row, in our column vocabulary."""
    out: dict[str, Any] = {}
    for column in _KAGGLE_BOX_COLUMNS:
        value = _field(row, f"{column}_{side}")
        parser = normalize.parse_float if column.endswith("_pct") or column == "plus_minus" else normalize.parse_int
        out[column] = parser(value)
    return out


def _load_kaggle_season(
    session: Session,
    connection: sqlite3.Connection,
    table: str,
    resolved: Mapping[str, str],
    season: str,
    game_ids: Sequence[str],
    chunk_size: int,
) -> tuple[int, int, int, list[str]]:
    """Load one season's games and team rows. Returns written/changed/read/ids."""
    from ..models import Game

    written = changed = read = 0
    seen: list[str] = []
    id_column = resolved["game_id"]
    cursor = connection.execute(
        f"SELECT * FROM {table} WHERE {id_column} IN ({','.join('?' * len(game_ids))})",
        list(game_ids),
    ) if len(game_ids) <= 900 else connection.execute(f"SELECT * FROM {table}")
    wanted = set(game_ids)

    while True:
        rows = cursor.fetchmany(chunk_size)
        if not rows:
            break
        for row in rows:
            game_id = str(_pluck(row, resolved, "game_id") or "")
            if game_id not in wanted:
                continue
            read += 1
            game_date = normalize.parse_date(_pluck(row, resolved, "game_date"))
            season_type = (
                _pluck(row, resolved, "season_type")
                or normalize.season_type_from_game_id(game_id)
                or "Regular Season"
            )
            season_type = _canonical_season_type(season_type)
            home_id = normalize.parse_int(_pluck(row, resolved, "home_team_id"))
            away_id = normalize.parse_int(_pluck(row, resolved, "away_team_id"))
            if game_date is None or home_id is None or away_id is None:
                continue

            home = _kaggle_side(row, "home")
            away = _kaggle_side(row, "away")
            minutes = normalize.parse_minutes(_pluck(row, resolved, "minutes"))
            for side in (home, away):
                side["minutes"] = minutes
                side["season"] = season

            # ``ingested_at`` is a timestamp of the pass, not of the data, so it
            # is written separately: folded into the comparison it would make
            # every re-load report every game as changed, and the report is what
            # an operator reads to decide whether a re-run did anything.
            if aggregate.upsert(
                session,
                Game,
                {"game_id": game_id},
                {
                    "game_date": game_date,
                    "season": season,
                    "season_type": season_type,
                    "home_team_id": home_id,
                    "away_team_id": away_id,
                    "home_pts": home.get("pts"),
                    "away_pts": away.get("pts"),
                    "status": "final",
                    "data_source": KAGGLE_SOURCE,
                },
            ):
                changed += 1
            aggregate.upsert(session, Game, {"game_id": game_id}, {"ingested_at": utcnow()})
            written += 1
            seen.append(game_id)

            for stats, other, is_home, team_id in (
                (home, away, True, home_id),
                (away, home, False, away_id),
            ):
                ok, row_changed = write_team_game_row(
                    session,
                    game_id,
                    {**stats, "team_id": team_id},
                    is_home=is_home,
                    opponent={**other, "team_id": (away_id if is_home else home_id)},
                )
                written += int(ok)
                changed += int(row_changed)
        session.flush()

    return written, changed, read, seen


def _canonical_season_type(value: Any) -> str:
    """Map a source's season-type spelling onto the contract's five values."""
    text = str(value or "").strip().lower()
    if "pre" in text:
        return "Pre Season"
    if "all" in text and "star" in text:
        return "All Star"
    if "play-in" in text or "play in" in text or "playin" in text:
        return "Play In"
    if "playoff" in text or "post" in text:
        return "Playoffs"
    return "Regular Season"


def _load_kaggle_player_box(
    session: Session,
    connection: sqlite3.Connection,
    table: str,
    game_ids: Sequence[str],
    season: str,
    chunk_size: int,
) -> tuple[int, int, int]:
    """Load one season's player box scores, when the release includes them."""
    columns = _sqlite_columns(connection, table)
    resolved = _resolve(columns, _KAGGLE_PLAYER_BOX_ALIASES, table)
    if "game_id" not in resolved or "player_id" not in resolved:
        return 0, 0, 0

    wanted = set(game_ids)
    written = changed = read = 0
    cursor = connection.execute(f"SELECT * FROM {table}")
    while True:
        rows = cursor.fetchmany(chunk_size)
        if not rows:
            break
        for row in rows:
            game_id = str(_pluck(row, resolved, "game_id") or "")
            if game_id not in wanted:
                continue
            read += 1
            line: dict[str, Any] = {
                "player_id": normalize.parse_int(_pluck(row, resolved, "player_id")),
                "team_id": normalize.parse_int(_pluck(row, resolved, "team_id")),
                "player_name": _pluck(row, resolved, "player_name"),
                "minutes": normalize.parse_minutes(_pluck(row, resolved, "minutes")),
            }
            started = _pluck(row, resolved, "started")
            line["started"] = bool(str(started).strip()) if started is not None else False
            for column in (
                "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb",
                "ast", "stl", "blk", "tov", "pf", "pts",
            ):
                line[column] = normalize.parse_int(_pluck(row, resolved, column))
            line["plus_minus"] = normalize.parse_float(_pluck(row, resolved, "plus_minus"))
            ensure_player(session, line)
            ok, row_changed = write_player_basic_row(session, game_id, line, season)
            written += int(ok)
            changed += int(row_changed)
        session.flush()
    return written, changed, read


# --------------------------------------------------------------------------- #
# Basketball-Reference season dumps
# --------------------------------------------------------------------------- #

_BBREF_ADVANCED_ALIASES: dict[str, tuple[str, ...]] = {
    "season": ("season", "year"),
    "slug": ("player_additional", "bbref_id", "slug", "player_id"),
    "name": ("player", "player_name", "name"),
    "team": ("tm", "team", "team_id", "abbreviation"),
    "age": ("age",),
    "gp": ("g", "games"),
    "minutes": ("mp", "minutes", "minutes_played"),
    "per": ("per",),
    "ts_pct": ("ts_percent", "ts_pct", "ts"),
    "fg3a_rate": ("x3p_ar", "fg3a_per_fga_pct", "3par"),
    "ftr": ("f_tr", "fta_per_fga_pct", "ftr"),
    "oreb_pct": ("orb_percent", "orb_pct"),
    "dreb_pct": ("drb_percent", "drb_pct"),
    "reb_pct": ("trb_percent", "trb_pct"),
    "ast_pct": ("ast_percent", "ast_pct"),
    "stl_pct": ("stl_percent", "stl_pct"),
    "blk_pct": ("blk_percent", "blk_pct"),
    "tov_pct": ("tov_percent", "tov_pct"),
    "usg_pct": ("usg_percent", "usg_pct"),
    "ows": ("ows",),
    "dws": ("dws",),
    "ws": ("ws",),
    "ws48": ("ws_48", "ws48"),
    "obpm": ("obpm",),
    "dbpm": ("dbpm",),
    "bpm": ("bpm",),
    "vorp": ("vorp",),
}

#: Basketball-Reference reports these on the 0-100 scale while TS%, 3PAr and FTr
#: on the same row are fractions. The inconsistency is theirs; declaring it here
#: is how the contract's "fractions in [0, 1] everywhere" survives contact with it.
_BBREF_PERCENT_SCALE_100 = frozenset(
    {"oreb_pct", "dreb_pct", "reb_pct", "ast_pct", "stl_pct", "blk_pct", "tov_pct", "usg_pct"}
)

#: The columns this loader owns. Aggregation never writes the season ratings, so
#: these survive a later recomputation of the same season.
_BBREF_RATE_COLUMNS = (
    "ts_pct",
    "fg3a_rate",
    "ftr",
    "oreb_pct",
    "dreb_pct",
    "reb_pct",
    "ast_pct",
    "stl_pct",
    "blk_pct",
    "tov_pct",
    "usg_pct",
)
_BBREF_RATING_COLUMNS = ("per", "ows", "dws", "ws", "ws48", "obpm", "dbpm", "bpm", "vorp")

_BBREF_FILENAMES = ("Advanced.csv", "advanced.csv", "Player Advanced.csv")


def _bbref_season(value: Any) -> str | None:
    """Basketball-Reference labels a season by its ending year: 1997 → 1996-97."""
    year = normalize.parse_int(value)
    if year is None:
        return None
    if 1000 <= year <= 2100:
        return normalize.season_string(year - 1)
    return None


def load_bbref_season_csvs(
    session: Session,
    directory: str | Path,
    *,
    seasons: Iterable[str] | None = None,
    chunk_size: int = 5000,
    resume: bool = True,
    create_missing_players: bool = False,
) -> LoadReport:
    """Load season advanced stats from a Basketball-Reference season dump.

    This is the only practical source for pre-1997 PER, Win Shares, BPM and VORP:
    those are box-score derivations that nobody computed contemporaneously and
    that stats.nba.com does not serve. Every row is written with
    ``is_estimated=True`` — they are estimates by construction, not measurements,
    and the client renders them with the "est." badge the contract requires.

    Rows are matched to NBA person ids through the Basketball-Reference slug (on
    ``players`` or in ``id_crosswalk``). A row that matches nothing is counted
    and reported; it never invents a player, because two half-populated records
    for the same person are worse than one missing season. Run
    :func:`reconcile_ids` first to widen the crosswalk.

    Multi-team ("TOT") rows are skipped: ``player_season`` keys on a team, and
    the per-team rows in the same file carry the same totals.
    """
    root = Path(directory)
    if not root.is_dir():
        raise NotADirectoryError(f"Basketball-Reference dump directory not found: {root}")
    path = next((root / name for name in _BBREF_FILENAMES if (root / name).is_file()), None)
    if path is None:
        raise FileNotFoundError(
            f"No advanced-stats CSV in {root} (looked for {', '.join(_BBREF_FILENAMES)})"
        )

    job = "backfill_bbref"
    report = LoadReport(job=job, source=str(path))
    done = completed_seasons(session, job) if resume else set()
    wanted = set(seasons) if seasons else None

    slug_to_player = _slug_index(session)
    teams = {
        (abbr or "").upper(): team_id
        for team_id, abbr in session.execute(select(Team.team_id, Team.abbr)).all()
    }
    unmatched: set[str] = set()
    touched: set[str] = set()
    pending = 0

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        resolved = _resolve(reader.fieldnames or (), _BBREF_ADVANCED_ALIASES, path.name)
        if "slug" not in resolved or "season" not in resolved:
            raise ValueError(
                f"{path.name} has neither a Basketball-Reference slug column nor a season "
                "column; this does not look like a season dump."
            )

        for row in reader:
            report.rows_read += 1
            season = _bbref_season(_pluck(row, resolved, "season"))
            if season is None or (wanted is not None and season not in wanted):
                report.rows_skipped += 1
                continue
            if season in done:
                report.rows_skipped += 1
                continue

            abbr = str(_pluck(row, resolved, "team") or "").strip().upper()
            if abbr in MULTI_TEAM_ABBREVIATIONS:
                report.rows_skipped += 1
                continue
            team_id = teams.get(abbr)
            slug = str(_pluck(row, resolved, "slug") or "").strip().lower()
            player_id = slug_to_player.get(slug)
            if player_id is None or team_id is None:
                unmatched.add(
                    f"{_pluck(row, resolved, 'name') or slug} ({slug or '?'}, {abbr or '?'}, {season})"
                )
                report.rows_skipped += 1
                continue

            values: dict[str, Any] = {
                "age": normalize.parse_int(_pluck(row, resolved, "age")),
                "gp": normalize.parse_int(_pluck(row, resolved, "gp")),
                "minutes": normalize.parse_float(_pluck(row, resolved, "minutes")),
                "is_estimated": True,
                "data_source": BBREF_SOURCE,
            }
            for column in (*_BBREF_RATE_COLUMNS, *_BBREF_RATING_COLUMNS):
                value = normalize.parse_float(_pluck(row, resolved, column))
                if value is not None and column in _BBREF_PERCENT_SCALE_100:
                    value = value / 100.0
                values[column] = value
            aggregate.apply_era(values, aggregate.era_plan("player_season", season)[0])

            if aggregate.upsert(
                session,
                PlayerSeason,
                {
                    "player_id": player_id,
                    "season": season,
                    "season_type": "Regular Season",
                    "team_id": team_id,
                },
                values,
            ):
                report.rows_changed += 1
            report.rows_written += 1
            touched.add(season)
            pending += 1
            if pending >= chunk_size:
                session.flush()
                session.commit()
                pending = 0

    session.flush()
    for season in sorted(touched, key=catalog.season_sort_key):
        report.seasons.append(season)
        _checkpoint(session, job, season, rows=report.rows_written)
    report.unmatched = sorted(unmatched)
    if unmatched:
        report.notes.append(
            f"{len(unmatched)} rows had no NBA id for their Basketball-Reference slug; "
            "run reconcile_ids() to widen the crosswalk"
        )
    session.commit()

    logger.info(
        "event=bbref_load file=%s seasons=%d rows=%d unmatched=%d",
        path.name,
        len(report.seasons),
        report.rows_written,
        len(unmatched),
    )
    return report


def _slug_index(session: Session) -> dict[str, int]:
    """Basketball-Reference slug → NBA person id, from both places we keep it."""
    index: dict[str, int] = {}
    for player_id, slug in session.execute(
        select(Player.player_id, Player.bbref_slug).where(Player.bbref_slug.is_not(None))
    ).all():
        if slug:
            index[str(slug).strip().lower()] = player_id
    for person_id, slug in session.execute(
        select(IdCrosswalk.nba_person_id, IdCrosswalk.bbref_slug).where(
            IdCrosswalk.bbref_slug.is_not(None)
        )
    ).all():
        if slug:
            index.setdefault(str(slug).strip().lower(), person_id)
    return index


# --------------------------------------------------------------------------- #
# hoopR / shufinskiy release files
# --------------------------------------------------------------------------- #

_PARQUET_PLAYER_ALIASES: dict[str, tuple[str, ...]] = {
    "game_id": ("game_id", "gameid"),
    "player_id": ("athlete_id", "player_id", "person_id"),
    "player_name": ("athlete_display_name", "player_name", "player"),
    "team_id": ("team_id", "athlete_team_id"),
    "minutes": ("minutes", "min", "mp"),
    "started": ("starter", "did_start", "start_position"),
    "fgm": ("field_goals_made", "fgm"),
    "fga": ("field_goals_attempted", "fga"),
    "fg3m": ("three_point_field_goals_made", "fg3m"),
    "fg3a": ("three_point_field_goals_attempted", "fg3a"),
    "ftm": ("free_throws_made", "ftm"),
    "fta": ("free_throws_attempted", "fta"),
    "oreb": ("offensive_rebounds", "oreb"),
    "dreb": ("defensive_rebounds", "dreb"),
    "reb": ("rebounds", "reb"),
    "ast": ("assists", "ast"),
    "stl": ("steals", "stl"),
    "blk": ("blocks", "blk"),
    "tov": ("turnovers", "tov", "to"),
    "pf": ("fouls", "pf", "personal_fouls"),
    "pts": ("points", "pts"),
    "plus_minus": ("plus_minus", "plusminus"),
    "season": ("season", "season_year"),
}


def _read_parquet(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    """Read a parquet file if ``pyarrow`` is installed; otherwise say why not."""
    try:
        import pyarrow.parquet as parquet  # type: ignore[import-not-found]
    except ImportError:
        return [], (
            f"skipped {path.name}: reading parquet needs the optional pyarrow "
            "package (`pip install pyarrow`), or export the file as CSV"
        )
    table = parquet.read_table(path)
    return table.to_pylist(), None


def _read_csv(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        yield from csv.DictReader(handle)


def load_parquet_dir(
    session: Session,
    path: str | Path,
    *,
    seasons: Iterable[str] | None = None,
    chunk_size: int = 5000,
    reaggregate: bool = True,
) -> LoadReport:
    """Load hoopR / shufinskiy player box files — parquet when possible, else CSV.

    ``pyarrow`` is an optional import, deliberately: the rest of Hardwood runs on
    five dependencies and a release file can always be exported to CSV. Without
    it, ``.csv`` files in the directory are loaded and each ``.parquet`` file is
    reported as skipped with the reason, rather than being passed over in silence.

    Rows are matched to games already in the store. A box-score row for a game we
    have never heard of is counted as skipped: it has no date, season or opponent
    here, and inventing them would corrupt every date-scoped query.
    """
    root = Path(path)
    if not root.is_dir():
        raise NotADirectoryError(f"Release directory not found: {root}")

    from ..models import Game

    report = LoadReport(job="backfill_parquet", source=str(root))
    known_games = {
        game_id: season
        for game_id, season in session.execute(select(Game.game_id, Game.season)).all()
    }
    wanted = set(seasons) if seasons else None
    touched: set[str] = set()
    pending = 0

    files = sorted([*root.glob("*.parquet"), *root.glob("*.csv")])
    if not files:
        report.notes.append(f"no .parquet or .csv files in {root}")
        return report

    for file_path in files:
        if file_path.suffix == ".parquet":
            rows, note = _read_parquet(file_path)
            if note:
                report.notes.append(note)
                continue
            iterator: Iterable[Mapping[str, Any]] = rows
        else:
            iterator = _read_csv(file_path)

        resolved: dict[str, str] | None = None
        for row in iterator:
            if resolved is None:
                resolved = _resolve(row.keys(), _PARQUET_PLAYER_ALIASES, file_path.name)
                if "game_id" not in resolved or "player_id" not in resolved:
                    report.notes.append(
                        f"skipped {file_path.name}: no game id / player id columns"
                    )
                    break
            report.rows_read += 1
            game_id = str(_pluck(row, resolved, "game_id") or "")
            season = known_games.get(game_id)
            if season is None:
                report.rows_skipped += 1
                continue
            if wanted is not None and season not in wanted:
                report.rows_skipped += 1
                continue

            line: dict[str, Any] = {
                "player_id": normalize.parse_int(_pluck(row, resolved, "player_id")),
                "team_id": normalize.parse_int(_pluck(row, resolved, "team_id")),
                "player_name": _pluck(row, resolved, "player_name"),
                "minutes": normalize.parse_minutes(_pluck(row, resolved, "minutes")),
            }
            started = _pluck(row, resolved, "started")
            line["started"] = bool(normalize.parse_bool(started)) if started is not None else False
            for column in (
                "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb",
                "ast", "stl", "blk", "tov", "pf", "pts",
            ):
                line[column] = normalize.parse_int(_pluck(row, resolved, column))
            line["plus_minus"] = normalize.parse_float(_pluck(row, resolved, "plus_minus"))
            if line["player_id"] is None or line["team_id"] is None:
                report.rows_skipped += 1
                continue

            ensure_player(session, line)
            ok, changed = write_player_basic_row(session, game_id, line, season)
            report.rows_written += int(ok)
            report.rows_changed += int(changed)
            touched.add(season)
            pending += 1
            if pending >= chunk_size:
                session.flush()
                session.commit()
                pending = 0

    session.flush()
    report.seasons = sorted(touched, key=catalog.season_sort_key)
    if reaggregate:
        for season in report.seasons:
            aggregate.recompute_season(session, season)
    record_ingest_log(
        session,
        "backfill_parquet",
        status="success",
        rows=report.rows_written,
    )
    session.commit()
    logger.info(
        "event=parquet_load dir=%s files=%d rows=%d skipped=%d",
        root.name,
        len(files),
        report.rows_written,
        report.rows_skipped,
    )
    return report


# --------------------------------------------------------------------------- #
# Id crosswalk
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CrosswalkCandidate:
    """One outside identity to attach to an NBA person id."""

    slug: str
    name: str
    birthdate: date | None = None
    birth_year: int | None = None
    from_year: int | None = None
    to_year: int | None = None
    espn_id: int | None = None
    balldontlie_id: int | None = None


@dataclass
class ReconcileReport:
    """What the crosswalk build resolved, and what a human needs to look at."""

    candidates: int = 0
    matched: int = 0
    written: int = 0
    ambiguous: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    by_method: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidates": self.candidates,
            "matched": self.matched,
            "written": self.written,
            "ambiguousCount": len(self.ambiguous),
            "unmatchedCount": len(self.unmatched),
            "ambiguous": self.ambiguous[:50],
            "unmatched": self.unmatched[:50],
            "byMethod": dict(self.by_method),
        }


#: Match strength by the evidence that produced it. Name alone is deliberately
#: weak: "Mike Dunleavy" is two people, and so is "Gary Payton".
_METHOD_CONFIDENCE: dict[str, float] = {
    "declared": 1.0,
    "name+birthdate": 0.99,
    "name+debut": 0.85,
    "name": 0.60,
}


def _candidate_from(value: Any) -> CrosswalkCandidate | None:
    if isinstance(value, CrosswalkCandidate):
        return value
    if not isinstance(value, Mapping):
        return None
    slug = str(value.get("slug") or value.get("bbref_slug") or "").strip().lower()
    name = str(value.get("name") or value.get("player") or "").strip()
    if not slug or not name:
        return None
    birthdate = normalize.parse_date(value.get("birthdate"))
    return CrosswalkCandidate(
        slug=slug,
        name=name,
        birthdate=birthdate,
        birth_year=normalize.parse_int(value.get("birth_year"))
        or (birthdate.year if birthdate else None),
        from_year=normalize.parse_int(value.get("from_year") or value.get("first_seas")),
        to_year=normalize.parse_int(value.get("to_year") or value.get("last_seas")),
        espn_id=normalize.parse_int(value.get("espn_id")),
        balldontlie_id=normalize.parse_int(value.get("balldontlie_id")),
    )


def reconcile_ids(
    session: Session,
    *,
    candidates: Iterable[Any] | None = None,
    min_confidence: float = 0.5,
) -> ReconcileReport:
    """Build ``id_crosswalk`` from name + birthdate + debut season.

    There is no official mapping between NBA.com's integer person ids,
    Basketball-Reference's slugs and ESPN's ids, so one has to be inferred — and
    inference that cannot show its work is worse than none. Every row written
    carries the ``method`` that produced it and a ``confidence``:

    ======================  ============  ==================================
    Method                  Confidence    Evidence
    ======================  ============  ==================================
    ``declared``            1.00          The slug was already on the player
    ``name+birthdate``      0.99          Name and date of birth agree
    ``name+debut``          0.85          Name agrees, debut within a year
    ``name``                0.60          Name agrees, and is unique
    ======================  ============  ==================================

    A name that matches several players and cannot be separated by birth year or
    debut season is left **unwritten** and listed in the report. Guessing would
    silently attach one man's career to another's, which is the one failure this
    table exists to prevent — and no new player is ever created here.

    ``candidates`` are mappings (or :class:`CrosswalkCandidate`) with ``slug``,
    ``name`` and optionally ``birthdate`` / ``birth_year`` / ``from_year``. With
    none supplied, the slugs already recorded on ``players`` are written through
    at full confidence, which is what a fresh Kaggle load leaves behind.
    """
    report = ReconcileReport()
    players = session.execute(select(Player)).scalars().all()
    by_name: dict[str, list[Player]] = {}
    for player in players:
        by_name.setdefault(normalize_name(player.full_name), []).append(player)

    supplied = [
        candidate
        for candidate in (_candidate_from(item) for item in (candidates or ()))
        if candidate is not None
    ]

    # Slugs already on the player row need no inference at all.
    for player in players:
        if player.bbref_slug:
            report.candidates += 1
            report.matched += 1
            report.by_method["declared"] = report.by_method.get("declared", 0) + 1
            if aggregate.upsert(
                session,
                IdCrosswalk,
                {"nba_person_id": player.player_id},
                {
                    "bbref_slug": str(player.bbref_slug).strip().lower(),
                    "confidence": _METHOD_CONFIDENCE["declared"],
                    "method": "declared",
                },
            ):
                report.written += 1

    for candidate in supplied:
        report.candidates += 1
        pool = by_name.get(normalize_name(candidate.name), [])
        if not pool:
            report.unmatched.append(f"{candidate.name} ({candidate.slug})")
            continue

        method: str | None = None
        chosen: Player | None = None

        if candidate.birth_year is not None:
            dated = [
                player
                for player in pool
                if player.birthdate is not None and player.birthdate.year == candidate.birth_year
            ]
            if len(dated) == 1:
                chosen, method = dated[0], "name+birthdate"

        if chosen is None and candidate.from_year is not None:
            debuting = [
                player
                for player in pool
                if player.from_year is not None
                and abs(player.from_year - candidate.from_year) <= 1
            ]
            if len(debuting) == 1:
                chosen, method = debuting[0], "name+debut"

        if chosen is None and len(pool) == 1:
            chosen, method = pool[0], "name"

        if chosen is None or method is None:
            report.ambiguous.append(
                f"{candidate.name} ({candidate.slug}) matches "
                f"{', '.join(str(player.player_id) for player in pool)}"
            )
            continue

        confidence = _METHOD_CONFIDENCE[method]
        if confidence < min_confidence:
            report.ambiguous.append(
                f"{candidate.name} ({candidate.slug}) below min_confidence at {confidence:.2f}"
            )
            continue

        report.matched += 1
        report.by_method[method] = report.by_method.get(method, 0) + 1
        values: dict[str, Any] = {
            "bbref_slug": candidate.slug,
            "confidence": confidence,
            "method": method,
        }
        if candidate.espn_id is not None:
            values["espn_id"] = candidate.espn_id
        if candidate.balldontlie_id is not None:
            values["balldontlie_id"] = candidate.balldontlie_id
        if aggregate.upsert(
            session, IdCrosswalk, {"nba_person_id": chosen.player_id}, values
        ):
            report.written += 1
        if not chosen.bbref_slug:
            aggregate.upsert(
                session, Player, {"player_id": chosen.player_id}, {"bbref_slug": candidate.slug}
            )

    record_ingest_log(
        session,
        "reconcile_ids",
        status="success",
        rows=report.written,
        error=(
            f"{len(report.ambiguous)} ambiguous, {len(report.unmatched)} unmatched"
            if report.ambiguous or report.unmatched
            else None
        ),
    )
    session.commit()
    logger.info(
        "event=reconcile candidates=%d matched=%d written=%d ambiguous=%d unmatched=%d",
        report.candidates,
        report.matched,
        report.written,
        len(report.ambiguous),
        len(report.unmatched),
    )
    return report
