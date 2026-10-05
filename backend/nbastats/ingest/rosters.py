"""Listed positions, one per player per season: the basis defence by position stands on.

Defence by position asks, for every opposing player, "which bucket do his points belong to?".
The answer has to come from somewhere that is the same for every game he plays, for every
reader, regardless of how this machine happened to be running that night. That is what
``player_position_season`` is, and this module is how a live store fills it.

Why not ``players.position``, and why not the lineup card
---------------------------------------------------------
``players.position`` is filled by the box-score path from whatever the first box score said, and
a V3 box score marks only the *starters* with a position (the bench has an empty string), so a
bench player's value is whatever a later start happened to say, or nothing. It is also a single
string that ``queries._matches_position`` counts twice for a hybrid. The start slot itself is a
lineup-card label that exists only for nights the watch loop ran, so a bucket built on it would
depend on Mac uptime. A roster listing has none of these problems: it covers the whole roster,
including the bench, and it is the league saying what it lists each player as. So this module
reads ``CommonTeamRoster`` and never reads ``players.position``.

The sources, and who wins
-------------------------
Three writers can put a row at the same (player, season); :data:`~nbastats.models.POSITION_SOURCES`
lists them strongest first.

``commonTeamRoster``
    The league's roster listing, fetched once per team per week by :func:`refresh_rosters`
    (thirty requests). It wins over everything else for its season.
``kaggleCurrent``
    The ``common_player_info`` table of the Kaggle bulk file, by :func:`load_kaggle_positions`.
    It is a snapshot of *today's* listing, so it is written for the season the backfill ran in
    and never for a past one (a 2019 season must not inherit a 2026 listing). It never
    replaces a roster row.
``seedArchetype``
    The demo seeder. Seeded rows only ever sit beside seeded rows: both ingest paths here
    **refuse to write into a store that holds the demo league** (judged by its rows, the way
    ``nba_intel.store.store_is_synthetic`` is), and :func:`upsert_position` never overwrites a
    seeded row even if called directly. Real positions next to invented games would be a
    statement about people that nothing supports.

Two further rules apply within a rank. A source that names *no usable position* for a player
(an empty string, or a label the schema does not understand) never erases a usable one: it can
add a row where there was none, and that is all. And a row is rewritten only when something in
it changed, so a weekly run over an unchanged league moves no ``fetched_at``.

Fail closed
-----------
The ``POSITION`` column of ``CommonTeamRoster`` is **unverified** (no response could be fetched
from the development environment). A team's response counts as readable only if it has that
column *and* at least one player in it carries a position the shared normaliser understands.
Anything else is "unreadable": that team writes nothing, the job logs why, and when no team at
all was readable the job ends in state ``unreadable`` and the ``nba.rosters`` source (what
``/v1/sources`` shows) says so. The consequence is honest and visible: with no positions,
defence by position is withheld league-wide (``positionCoverage``) rather than computed from a
guess. A run that stops after three consecutive bad teams does so deliberately: when the first
three of thirty are unreadable the other twenty-seven will be too, and each unavailable fetch
costs the client's full backoff.

Players the store does not hold are skipped and counted, not created. A player with no
box-score line has no points to allocate, so a roster row for him adds nothing to defence, and
inventing a ``players`` row from a roster would put never-played names in every player search.
The next weekly run picks him up once he has a line.

The job is safe to repeat (writes are keyed and idempotent), safe to interrupt (each team is its
own transaction) and safe to run beside the watch loop (SQLite's WAL and busy timeout).
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence
from urllib.parse import quote

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import POSITION_SOURCES, Player, PlayerPositionSeason, Team
from ..shared import positions as shared_positions
from . import normalize
from .backfill import KAGGLE_SOURCE
from .client import IngestError, IngestUnavailable, StatsClient, log_line
from .daily import LIVE_SOURCE

__all__ = [
    "SOURCE_ROSTER",
    "SOURCE_KAGGLE",
    "SOURCE_SEED",
    "SOURCE_KEY",
    "ABORT_AFTER_BAD_TEAMS",
    "RosterReport",
    "RosterSource",
    "current_season",
    "store_is_synthetic",
    "source_rank",
    "upsert_position",
    "refresh_rosters",
    "load_kaggle_positions",
]

logger = logging.getLogger("nbastats.ingest.rosters")

SOURCE_ROSTER = "commonTeamRoster"
SOURCE_KAGGLE = "kaggleCurrent"
SOURCE_SEED = "seedArchetype"

#: The ``nba_intel_source_state`` key ``/v1/sources`` reads for this job.
SOURCE_KEY = "nba.rosters"

#: Stop the weekly sweep after this many teams in a row that could not be used (unreadable
#: response or failed fetch): the rest would fail the same way, each at full backoff cost.
ABORT_AFTER_BAD_TEAMS = 3

#: Strongest source has the highest rank. Derived from the tuple the schema documents, so the
#: precedence cannot be restated differently here.
_RANK = {source: len(POSITION_SOURCES) - index for index, source in enumerate(POSITION_SOURCES)}

_SEASON_START_MONTH = 10  # a new NBA season is labelled from October

_DEMO_REFUSAL = "Demo league: real roster positions are not written next to invented games."


class RosterSource(Protocol):
    """What :func:`refresh_rosters` needs of a client: one roster response per team.

    :class:`~nbastats.ingest.client.StatsClient` is one; a test double is another. Extra
    keyword arguments (``league_id``) are the client's own business.
    """

    def common_team_roster(self, team_id: int, season: str) -> Mapping[str, Any]: ...


def current_season(now: datetime | None = None) -> str:
    """The season label for ``now`` (UTC): ``"2026-27"`` from 1 October 2026 to 30 September.

    The NBA season is labelled by the year it starts in, and opening night is in October, so a
    July roster fetch is still about the season just finished (``CommonTeamRoster`` takes the
    season as a parameter and answers for *that* season whatever the date). Pass ``season=`` to
    :func:`refresh_rosters` to ask for another.
    """
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    start_year = moment.year if moment.month >= _SEASON_START_MONTH else moment.year - 1
    return normalize.season_string(start_year)


def store_is_synthetic(session: Session) -> bool:
    """True when the store holds the seeded demo league, judged by its rows.

    The same single question ``nbastats.nba_intel.store.store_is_synthetic`` asks (does
    ``games`` hold a row stamped ``synthetic-demo``?), repeated here so the ingest package does
    not import a feature package for one ``SELECT``. A test asserts the two agree. Keyed on
    content, not on ``HARDWOOD_DEMO_MODE``, so a manual re-seed of a live file cannot be
    skipped past.
    """
    if not inspect(session.get_bind()).has_table("games"):
        return False  # a store with no games at all has nothing invented in it
    row = session.execute(
        text("SELECT 1 FROM games WHERE data_source = 'synthetic-demo' LIMIT 1")
    ).first()
    return row is not None


def source_rank(source: str) -> int:
    """How strong a source is; higher wins. Unknown sources rank below every known one."""
    return _RANK.get(source, 0)


def _naive_utc(moment: datetime) -> datetime:
    """``moment`` as a naive UTC ``datetime``, the form every timestamp column stores."""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


@dataclass
class RosterReport:
    """The outcome of one roster or Kaggle position run.

    ``state`` is one of ``ok`` (at least one team was readable; ``partial`` says whether some
    were not), ``unreadable`` (a response arrived but no team's carried a usable ``POSITION``),
    ``error`` (nothing could be fetched, or the sweep was stopped early) or ``refused`` (the
    store holds the demo league; nothing was attempted).
    """

    season: str
    source: str = SOURCE_ROSTER
    state: str = "ok"
    reason: str | None = None
    teams_requested: int = 0
    teams_read: int = 0
    teams_unreadable: list[int] = field(default_factory=list)
    teams_failed: list[int] = field(default_factory=list)
    rows_read: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    #: Rows left as they were because a stronger source, or a usable position, already stood.
    kept: int = 0
    #: Roster players the store does not hold, skipped (see the module docstring).
    unknown_player: int = 0
    #: Rows whose position string the normaliser does not understand, or that had none.
    unrecognised: int = 0
    unrecognised_labels: list[str] = field(default_factory=list)
    interrupted: bool = False

    @property
    def partial(self) -> bool:
        return self.state == "ok" and bool(self.teams_unreadable or self.teams_failed)

    @property
    def written(self) -> int:
        return self.inserted + self.updated

    def note_label(self, label: str) -> None:
        if label not in self.unrecognised_labels and len(self.unrecognised_labels) < 12:
            self.unrecognised_labels.append(label)

    def as_dict(self) -> dict[str, Any]:
        return {
            "season": self.season,
            "source": self.source,
            "state": self.state,
            "reason": self.reason,
            "partial": self.partial,
            "teamsRequested": self.teams_requested,
            "teamsRead": self.teams_read,
            "teamsUnreadable": list(self.teams_unreadable),
            "teamsFailed": list(self.teams_failed),
            "rowsRead": self.rows_read,
            "inserted": self.inserted,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "kept": self.kept,
            "unknownPlayer": self.unknown_player,
            "unrecognised": self.unrecognised,
            "unrecognisedLabels": list(self.unrecognised_labels),
            "interrupted": self.interrupted,
        }


# ------------------------------------------------------------------------------ writing


def _weight_columns(weights: shared_positions.Weights | None) -> dict[str, float | None]:
    """The three weight columns for a normalised position: all ``None``, or all numbers."""
    if weights is None:
        return {"g_weight": None, "f_weight": None, "c_weight": None}
    return {
        "g_weight": float(weights.get("G", 0.0)),
        "f_weight": float(weights.get("F", 0.0)),
        "c_weight": float(weights.get("C", 0.0)),
    }


def upsert_position(
    session: Session,
    *,
    player_id: int,
    season: str,
    team_id: int | None,
    position_raw: str | None,
    source: str,
    data_source: str,
    fetched_at: datetime | None = None,
) -> str:
    """Write one (player, season) listing under the precedence rules. Flushes, never commits.

    Returns what happened: ``inserted``, ``updated``, ``unchanged`` or ``kept``. ``kept`` means
    the stored row stands, because it is a seeded row (never overwritten), or from a stronger
    source, or holds a usable position that this one (naming none) must not erase.
    ``position_raw`` is stored as given (stripped, at most 32 characters); the weights come from
    the shared normaliser, so this table and the demo seeder cannot disagree about what ``G-F``
    means.
    """
    if source not in POSITION_SOURCES:
        raise ValueError(f"{source!r} is not a position source ({', '.join(POSITION_SOURCES)})")
    raw = None
    if isinstance(position_raw, str) and position_raw.strip():
        raw = position_raw.strip()[:32]
    weights = shared_positions.normalise_nba_position(raw)
    values: dict[str, Any] = {
        "team_id": team_id,
        "position_raw": raw,
        **_weight_columns(weights),
        "source": source,
        "data_source": data_source,
    }
    stamp = fetched_at or utcnow()

    existing = session.get(PlayerPositionSeason, (player_id, season))
    if existing is None:
        session.add(
            PlayerPositionSeason(player_id=player_id, season=season, fetched_at=stamp, **values)
        )
        session.flush()
        return "inserted"

    if existing.source == SOURCE_SEED:
        return "kept"
    if source_rank(source) < source_rank(existing.source):
        return "kept"
    stored_usable = existing.g_weight is not None
    if stored_usable and weights is None:
        return "kept"

    changed = [name for name, value in values.items() if getattr(existing, name) != value]
    if not changed:
        return "unchanged"
    for name in changed:
        setattr(existing, name, values[name])
    existing.fetched_at = stamp
    session.flush()
    return "updated"


def _tally(report: RosterReport, outcome: str) -> None:
    if outcome == "inserted":
        report.inserted += 1
    elif outcome == "updated":
        report.updated += 1
    elif outcome == "unchanged":
        report.unchanged += 1
    else:
        report.kept += 1


# ------------------------------------------------------------------------ source state


def _record_source_state(session: Session, report: RosterReport, now: datetime) -> None:
    """Tell ``/v1/sources`` how the roster source stands. Best effort; never raises.

    Imported lazily and guarded: the intel package may not be installed, or its tables may not
    exist in an old file, and neither is a reason for the roster job to fail after its data is
    already written.
    """
    if report.state == "refused":
        return
    try:
        from ..nba_intel import store

        detail = {
            "season": report.season,
            "teamsRead": report.teams_read,
            "teamsRequested": report.teams_requested,
            "teamsUnreadable": report.teams_unreadable,
            "teamsFailed": report.teams_failed,
            "rowsWritten": report.written,
        }
        if report.reason:
            detail["reason"] = report.reason
        store.set_source_state(
            session,
            SOURCE_KEY,
            report.state,
            now=_naive_utc(now),
            error=None if report.state == "ok" else report.reason,
            success=report.state == "ok",
            detail=detail,
        )
        session.commit()
    except Exception as exc:  # noqa: BLE001 - bookkeeping must not undo the data
        session.rollback()
        logger.warning(log_line("roster_source_state_failed", error=str(exc)))


# ------------------------------------------------------------------------ the weekly job


def refresh_rosters(
    session: Session,
    client: RosterSource | None = None,
    *,
    season: str | None = None,
    team_ids: Sequence[int] | None = None,
    now: datetime | None = None,
    shutdown: Any = None,
    record_state: bool = True,
) -> RosterReport:
    """Fetch every team's roster and write each listed player's position. See the module docstring.

    ``team_ids`` defaults to the active franchises in ``teams``. ``shutdown`` is anything whose
    truthiness means "stop" (the runner's :class:`ShutdownFlag`); it is checked between teams.
    Raises :class:`~nbastats.ingest.client.IngestUnavailable` (``nba_api`` missing) without
    having written anything, so the runner can say how to fix it; every other per-team failure
    is recorded in the report. With ``record_state=False`` the ``nba.rosters`` source state is
    left alone (tests, and a dry run).
    """
    moment = now or datetime.now(timezone.utc)
    target_season = season or current_season(moment)
    report = RosterReport(season=target_season)

    if store_is_synthetic(session):
        report.state = "refused"
        report.reason = _DEMO_REFUSAL
        logger.warning(log_line("rosters_refused", reason=report.reason))
        return report

    api = client or StatsClient()
    if team_ids is None:
        team_ids = list(
            session.execute(
                select(Team.team_id).where(Team.is_active.is_(True)).order_by(Team.team_id)
            ).scalars()
        )
    wanted = [int(team_id) for team_id in team_ids]
    report.teams_requested = len(wanted)
    if not wanted:
        report.state = "error"
        report.reason = "no teams in the store to fetch rosters for"
        logger.warning(log_line("rosters_no_teams"))
        if record_state:
            _record_source_state(session, report, moment)
        return report

    known_teams = set(session.execute(select(Team.team_id)).scalars())
    stamp = _naive_utc(moment)
    consecutive_bad = 0
    stopped_early = False

    for team_id in wanted:
        if shutdown:
            report.interrupted = True
            report.reason = "interrupted"
            break
        try:
            payload = api.common_team_roster(team_id, target_season)
        except IngestUnavailable:
            raise
        except IngestError as exc:
            report.teams_failed.append(team_id)
            consecutive_bad += 1
            logger.warning(log_line("roster_fetch_failed", team_id=team_id, error=str(exc)))
        else:
            try:
                readable = _write_team(
                    session, report, payload, team_id, target_season, known_teams, stamp
                )
            except Exception as exc:  # noqa: BLE001 - an unexpected shape fails closed, per team
                session.rollback()
                readable = False
                report.teams_unreadable.append(team_id)
                logger.warning(
                    log_line("roster_unexpected_shape", team_id=team_id, error=repr(exc))
                )
            consecutive_bad = 0 if readable else consecutive_bad + 1
        if consecutive_bad >= ABORT_AFTER_BAD_TEAMS and report.teams_read == 0:
            stopped_early = True
            break

    _settle(report, stopped_early=stopped_early)
    summary = {k: v for k, v in report.as_dict().items() if not isinstance(v, list)}
    logger.info(log_line("rosters_done", **summary))
    if record_state and not report.interrupted:
        _record_source_state(session, report, moment)
    return report


def _write_team(
    session: Session,
    report: RosterReport,
    payload: Mapping[str, Any],
    team_id: int,
    season: str,
    known_teams: set[int],
    stamp: datetime,
) -> bool:
    """Write one team's roster. Returns whether the response was readable."""
    roster = normalize.normalize_common_team_roster(payload)
    if not roster.position_column_present:
        report.teams_unreadable.append(team_id)
        logger.warning(log_line("roster_position_column_missing", team_id=team_id))
        return False

    classified: list[tuple[dict[str, Any], shared_positions.Weights | None]] = [
        (row, shared_positions.normalise_nba_position(row.get("position"))) for row in roster.rows
    ]
    if not any(weights is not None for _row, weights in classified):
        report.teams_unreadable.append(team_id)
        logger.warning(
            log_line("roster_no_usable_position", team_id=team_id, rows=len(classified))
        )
        return False

    ids = [row["player_id"] for row, _weights in classified]
    known_players = set(
        session.execute(select(Player.player_id).where(Player.player_id.in_(ids))).scalars()
    )
    report.teams_read += 1
    for row, weights in classified:
        report.rows_read += 1
        raw = row.get("position")
        if weights is None:
            report.unrecognised += 1
            if raw:
                report.note_label(str(raw))
        player_id = row["player_id"]
        if player_id not in known_players:
            report.unknown_player += 1
            continue
        # The team the roster lists him on; failing that, the team whose roster this is.
        listed_team = next(
            (candidate for candidate in (row.get("team_id"), team_id) if candidate in known_teams),
            None,
        )
        outcome = upsert_position(
            session,
            player_id=player_id,
            season=season,
            team_id=listed_team,
            position_raw=raw,
            source=SOURCE_ROSTER,
            data_source=LIVE_SOURCE,
            fetched_at=stamp,
        )
        _tally(report, outcome)
    session.commit()
    return True


def _settle(report: RosterReport, *, stopped_early: bool) -> None:
    """Decide the final state of a sweep from what its teams did."""
    if report.interrupted:
        report.state = "ok" if report.teams_read else "error"
        return
    if report.teams_read:
        report.state = "ok"
        if report.partial:
            report.reason = (
                f"{len(report.teams_unreadable)} team(s) unreadable and "
                f"{len(report.teams_failed)} failed of {report.teams_requested}"
            )
        return
    if report.teams_unreadable:
        report.state = "unreadable"
        report.reason = (
            "CommonTeamRoster responses carried no usable POSITION: the column is missing or "
            "no listed position was understood. Nothing was written."
        )
    else:
        report.state = "error"
        report.reason = "no roster could be fetched"
    if stopped_early:
        report.reason += f" Stopped after {ABORT_AFTER_BAD_TEAMS} consecutive teams."


# -------------------------------------------------------------------------- Kaggle file

_KAGGLE_TABLE = "common_player_info"

_KAGGLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "player_id": ("person_id", "player_id", "id"),
    "position": ("position",),
    "team_id": ("team_id",),
    "roster_status": ("rosterstatus", "roster_status"),
    "to_year": ("to_year",),
}


def _resolve_columns(columns: Iterable[str]) -> dict[str, str]:
    """Map our roles to the Kaggle file's actual (case-varying) column names."""
    present = {name.lower(): name for name in columns}
    resolved: dict[str, str] = {}
    for role, candidates in _KAGGLE_COLUMNS.items():
        for candidate in candidates:
            if candidate in present:
                resolved[role] = present[candidate]
                break
    return resolved


def _is_active(status: Any) -> bool:
    """The Kaggle ``rosterstatus`` flag: ``Active`` or a truthy number."""
    if isinstance(status, str):
        return status.strip().lower() in {"active", "1", "true", "y", "yes"}
    return bool(status)


def load_kaggle_positions(
    session: Session,
    path: str | Path,
    *,
    season: str | None = None,
    now: datetime | None = None,
) -> RosterReport:
    """Write ``kaggleCurrent`` rows from the Kaggle file's ``common_player_info``, for one season.

    The table is a snapshot of today's listing, so it is written for ``season`` (default: the
    season the run happens in, :func:`current_season`) and for no other. Only players it marks
    active are written: a retired player's team and position would otherwise land in the
    current season's roster pool, where the injury-report matcher looks players up by team. If
    the file has neither a roster-status nor a last-season column there is no way to tell, and
    the run is ``unreadable`` rather than a guess.

    The file is opened read-only through stdlib ``sqlite3``. Rows never replace a
    ``commonTeamRoster`` row (precedence), a store holding the demo league refuses the run, and
    ``players.position`` is not read or written. Commits once at the end.
    """
    moment = now or datetime.now(timezone.utc)
    target = season or current_season(moment)
    report = RosterReport(season=target, source=SOURCE_KAGGLE)
    if store_is_synthetic(session):
        report.state = "refused"
        report.reason = _DEMO_REFUSAL
        return report

    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Kaggle SQLite file not found: {source}")
    start_year = normalize.season_start_year(target)
    stamp = _naive_utc(moment)

    uri = f"file:{quote(source.resolve().as_posix())}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.row_factory = sqlite3.Row
        tables = {
            str(row[0]).lower()
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        if _KAGGLE_TABLE not in tables:
            report.state = "unreadable"
            report.reason = f"the Kaggle file has no {_KAGGLE_TABLE} table"
            return report
        columns = _resolve_columns(
            str(row[1]) for row in connection.execute(f"PRAGMA table_info({_KAGGLE_TABLE})")
        )
        if "player_id" not in columns or "position" not in columns:
            report.state = "unreadable"
            report.reason = f"{_KAGGLE_TABLE} has no player id or no position column"
            return report
        if "roster_status" not in columns and "to_year" not in columns:
            report.state = "unreadable"
            report.reason = (
                f"{_KAGGLE_TABLE} cannot tell active players from retired ones "
                "(no roster status and no last season)"
            )
            return report
        rows = connection.execute(f"SELECT * FROM {_KAGGLE_TABLE}").fetchall()
    finally:
        connection.close()

    known_teams = set(session.execute(select(Team.team_id)).scalars())
    known_players = set(session.execute(select(Player.player_id)).scalars())
    for row in rows:
        player_id = normalize.parse_int(row[columns["player_id"]])
        if player_id is None:
            continue
        if "roster_status" in columns:
            active = _is_active(row[columns["roster_status"]])
        else:
            last_year = normalize.parse_int(row[columns["to_year"]])
            active = last_year is not None and start_year is not None and last_year >= start_year
        if not active:
            continue
        report.rows_read += 1
        raw = row[columns["position"]]
        raw = str(raw).strip() if raw is not None and str(raw).strip() else None
        if shared_positions.normalise_nba_position(raw) is None:
            report.unrecognised += 1
            if raw:
                report.note_label(raw)
        if player_id not in known_players:
            report.unknown_player += 1
            continue
        team_id = normalize.parse_int(row[columns["team_id"]]) if "team_id" in columns else None
        _tally(
            report,
            upsert_position(
                session,
                player_id=player_id,
                season=target,
                team_id=team_id if team_id in known_teams else None,
                position_raw=raw,
                source=SOURCE_KAGGLE,
                data_source=KAGGLE_SOURCE,
                fetched_at=stamp,
            ),
        )
    session.commit()
    report.state = "ok" if report.rows_read else "unreadable"
    if not report.rows_read:
        report.reason = f"{_KAGGLE_TABLE} listed no active player"
    logger.info(log_line("kaggle_positions_done", season=target, written=report.written,
                         kept=report.kept, unknown_player=report.unknown_player))
    return report
