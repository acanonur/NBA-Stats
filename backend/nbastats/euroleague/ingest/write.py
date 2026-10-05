"""Everything the live ingest writes to the EuroLeague store, and the rules for writing it.

The unit of work is a game, and a game is written whole or not at all
---------------------------------------------------------------------
:func:`write_box_score` replaces one game's lines in one go: the previous player and team lines of
that game are deleted and the new ones inserted in one transaction (the job commits after each
game), so a failure half-way leaves the old lines exactly as they were. Whether to write at all
is decided first, by the invariants:

* **Hard failure: quarantine.** The game's final score stays on record, ``stats_status`` becomes
  ``quarantined``, the rule that failed is returned for the log, and **no line is touched**. If an
  earlier good copy of the lines exists it is kept, because a good copy that is a day old is a
  better thing to have than none, and readers exclude a quarantined game anyway (the read side
  counts a game only when ``stats_status = 'ok'``).
* **Unchanged: no write.** The digest of what the box score *says* (not of its bytes; see
  :func:`~nbastats.euroleague.ingest.parse.box_digest`) is stored on the game; the same digest on a
  game already ``ok`` writes nothing and bumps nothing. A correction re-fetch that finds no
  correction is free.
* **Otherwise: write**, set ``stats_status = 'ok'`` and bump ``el_sync_state.sync_version``
  **once**. A client treats a new version as "something changed", so one game, one bump.

The schedule is a dumb upsert; the box score is where facts are checked
-----------------------------------------------------------------------
:func:`upsert_games` stores what the schedule (E2) says: who plays, when, where, the score and the
partials as reported. It does not judge them. The judgement (do the partials add up to the final
score, do the players add up to the team) happens when the box score arrives and everything can be
compared at once, and a game whose box never arrives stays ``stats_status = 'none'``, which no
reader counts. Three rules protect what is already stored:

* **A final game never goes back to scheduled.** If the service stops saying ``played`` for a game
  the store holds as final, the store keeps its result and the anomaly is counted in the result.
* **A corrected final score invalidates the lines.** If a final score changes and the game's lines
  were ``ok`` or ``quarantined``, ``stats_status`` returns to ``none`` so the box score is fetched
  and checked again against the new score.
* **A club is never created twice.** A club the service names that looks like one already stored
  under another code is refused, with its games, until a person has looked
  (:func:`~nbastats.euroleague.ingest.reconcile.find_alias_conflict`).

A workbook never overwrites live data, and live data may replace the workbook's: a game this module
changes becomes ``data_source = 'euroleague-v2'``, which the workbook importer then leaves alone.

Identity
--------
The people in a box score are written as ``official`` persons with the name the rest of Hardwood
uses (``"Given Surname"``; the service's ``"SURNAME, GIVEN"`` goes in no column, it only feeds
matching) and registered with their club for the season if no registration exists. Reconciliation
later folds a workbook-minted person into the official one; see :mod:`.reconcile`.

Nothing here commits
--------------------
Every function works in the caller's session and returns a result; jobs commit after each unit
(a schedule, a game) so one bad game cannot hold up the others.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Final, Mapping, Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db import (
    bump_sync_version,
    ensure_identity,
    promote_to_live,
    read_sync_state,
    utcnow,
)
from ..models import (
    ElClub,
    ElClubSeason,
    ElGame,
    ElIngestLog,
    ElJobState,
    ElPerson,
    ElPlayerGame,
    ElRawPayload,
    ElRegistration,
    ElSeason,
    ElSourceState,
    ElTeamGame,
)
from ..profile import DATA_SOURCE_LIVE, KIND_LIVE, official_game_id, parse_season_ref
from . import invariants, parse
from .client import Fetched, RawPayloadStore, RawRef
from .invariants import Violation
from .parse import BoxScore, ClubInfo, GameInfo, PersonInfo, PlayerLine, RegistrationInfo, Rejected
from .reconcile import (
    ensure_aliases,
    find_alias_conflict,
    find_game_by_matchup,
    rekey_game,
)

__all__ = [
    "RAW_PAYLOADS_KEPT_PER_URL",
    "ScheduleResult",
    "BoxResult",
    "PeopleResult",
    "ensure_live_writer",
    "mark_live_wrote",
    "ensure_season",
    "ensure_club",
    "upsert_games",
    "upsert_people",
    "write_box_score",
    "record_raw_payload",
    "log_run",
    "read_cursor",
    "write_cursor",
    "mark_source_ok",
    "mark_source_failed",
    "get_source_state",
]

logger = logging.getLogger(__name__)

#: Raw payload rows (and files) kept for one URL; older ones are deleted.
RAW_PAYLOADS_KEPT_PER_URL: Final = 3


# ---------------------------------------------------------------------------------- results


@dataclass
class ScheduleResult:
    """What upserting a round's games did."""

    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    #: Games that became final in this call.
    results: list[str] = field(default_factory=list)
    #: Final games whose score the service changed.
    corrections: list[str] = field(default_factory=list)
    #: Final games the service no longer calls played; their stored result was kept.
    regressions: list[str] = field(default_factory=list)
    rekeyed: list[tuple[str, str]] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    version: int | None = None

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated)

    def summary(self) -> str:
        parts = [
            f"{len(self.created)} new",
            f"{len(self.updated)} updated",
            f"{len(self.unchanged)} unchanged",
        ]
        if self.results:
            parts.append(f"{len(self.results)} new result(s)")
        if self.corrections:
            parts.append(f"{len(self.corrections)} correction(s)")
        if self.rejected:
            parts.append(f"{len(self.rejected)} rejected")
        return ", ".join(parts)


@dataclass
class BoxResult:
    """What writing one box score did. ``outcome`` is ``written``, ``unchanged``, ``quarantined``
    or ``refused`` (nothing could be checked: the game has no final score in the store)."""

    game_id: str
    outcome: str
    stats_status: str | None
    hard: tuple[Violation, ...] = ()
    soft: tuple[Violation, ...] = ()
    lines: int = 0
    reason: str | None = None
    version: int | None = None
    digest: str | None = None

    @property
    def wrote(self) -> bool:
        return self.outcome == "written"


@dataclass
class PeopleResult:
    persons_created: int = 0
    persons_updated: int = 0
    registrations_created: int = 0
    registrations_updated: int = 0
    rejected: list[Rejected] = field(default_factory=list)

    @property
    def rows(self) -> int:
        return (
            self.persons_created
            + self.persons_updated
            + self.registrations_created
            + self.registrations_updated
        )


# ------------------------------------------------------------------------------------ setup


def ensure_live_writer(session: Session) -> None:
    """Stamp the store as ``live`` if it has no identity, or check that it may take live writes.

    A synthetic (invented) store raises :class:`~nbastats.euroleague.db.StoreKindMismatch`, which
    is what stops real rows from ever being written beside the demo league. A ``workbook`` store
    is accepted (both are real) and is promoted to ``live`` by :func:`mark_live_wrote`.
    """
    ensure_identity(session, KIND_LIVE)


def mark_live_wrote(session: Session) -> None:
    """Promote a ``workbook`` store to ``live`` once live ingest has written to it."""
    promote_to_live(session)


def ensure_season(session: Session, season_code: str) -> ElSeason:
    """The ``el_season`` row for ``season_code``, created if needed; the newest is current."""
    ref = parse_season_ref(season_code)
    if ref is None:
        raise ValueError(f"{season_code!r} is not a season code such as 'E2026'")
    seasons = {s.season_code: s for s in session.execute(select(ElSeason)).scalars()}
    row = seasons.get(season_code)
    newest = max([s.start_year for s in seasons.values()] + [ref.start_year])
    if row is None:
        row = ElSeason(
            season_code=ref.code,
            competition_code=ref.competition_code,
            label=ref.label,
            start_year=ref.start_year,
            is_current=ref.start_year == newest,
        )
        session.add(row)
        seasons[season_code] = row
    for other in seasons.values():
        other.is_current = other.start_year == newest
    session.flush()
    return row


def ensure_club(
    session: Session, info: ClubInfo, season_code: str | None = None
) -> tuple[ElClub | None, str | None]:
    """``(club, None)``, or ``(None, reason)`` when the club must not be created.

    An existing club is only ever *filled in* (a missing broadcast code, short name or country);
    its name is never overwritten. A new club is created unless it resembles one already stored
    under a different code. ``season_code`` also makes sure the club has its season row.
    """
    club = session.get(ElClub, info.code)
    if club is None:
        conflict = find_alias_conflict(session, info)
        if conflict is not None:
            return None, conflict
        club = ElClub(
            club_code=info.code,
            tv_code=info.tv_code,
            name=info.name or info.code,
            short_name=info.short_name,
            country_code=info.country_code,
        )
        session.add(club)
    else:
        if info.tv_code and not club.tv_code:
            club.tv_code = info.tv_code
        if info.short_name and not club.short_name:
            club.short_name = info.short_name
        if info.country_code and not club.country_code:
            club.country_code = info.country_code
    ensure_aliases(session, info.code, info)
    if season_code is not None and session.get(ElClubSeason, (season_code, info.code)) is None:
        session.add(ElClubSeason(season_code=season_code, club_code=info.code))
    session.flush()
    return club, None


# ----------------------------------------------------------------------------- schedule


def _assign(row: Any, name: str, value: Any) -> bool:
    """Set ``row.name`` to ``value`` if it differs. ``True`` when it changed."""
    if getattr(row, name) != value:
        setattr(row, name, value)
        return True
    return False


def _later(current: date | None, candidate: date) -> date:
    return candidate if current is None or candidate > current else current


def _partials_json(partials: tuple[int, ...] | None) -> str | None:
    return json.dumps(list(partials)) if partials is not None else None


def upsert_games(
    session: Session,
    season_code: str,
    infos: Sequence[GameInfo],
    *,
    now: datetime | None = None,
) -> ScheduleResult:
    """Store what the schedule says about these games. See the module docstring for the rules.

    Bumps ``sync_version`` once if anything changed. Does not commit.
    """
    moment = now or utcnow()
    result = ScheduleResult()
    ensure_season(session, season_code)
    through: date | None = None
    for info in infos:
        gid = official_game_id(season_code, info.game_code)
        home, home_conflict = ensure_club(session, info.home.club, season_code)
        away, away_conflict = ensure_club(session, info.away.club, season_code)
        if home is None or away is None:
            reason = home_conflict or away_conflict or "an unknown club"
            result.rejected.append(Rejected(f"game {info.game_code}", reason))
            continue

        row = session.get(ElGame, gid)
        rekeyed_now = False
        if row is None:
            same = find_game_by_matchup(
                session, season_code, info.round, home.club_code, away.club_code
            )
            if same is not None:
                if same.game_code is None:
                    provisional_id = same.game_id  # read first: the re-key deletes that row
                    rekey_game(session, provisional_id, gid, game_code=info.game_code)
                    result.rekeyed.append((provisional_id, gid))
                    row = session.get(ElGame, gid)
                    rekeyed_now = True
                elif same.game_code != info.game_code:
                    result.rejected.append(
                        Rejected(
                            f"game {info.game_code}",
                            f"round {info.round} {home.club_code} v {away.club_code} is already "
                            f"stored as {same.game_id}; left alone",
                        )
                    )
                    continue
        if row is not None and (
            row.home_club_code != home.club_code or row.away_club_code != away.club_code
        ):
            result.rejected.append(
                Rejected(
                    f"game {info.game_code}",
                    f"the store has it as {row.home_club_code} v {row.away_club_code} but the "
                    f"service says {home.club_code} v {away.club_code}; left alone",
                )
            )
            continue

        final = info.status == "final"
        facts: dict[str, Any] = {
            "season_code": season_code,
            "game_code": info.game_code,
            "round_number": info.round,
            "phase_code": info.phase_code,
            "game_date": info.game_date,
            "home_club_code": home.club_code,
            "away_club_code": away.club_code,
        }
        for key, value in (
            ("tipoff_utc", info.tipoff_utc),
            ("venue_name", info.venue_name),
            ("venue_code", info.venue_code),
            ("is_neutral", info.is_neutral),
        ):
            if value is not None:
                facts[key] = value
        for issue in info.issues:
            result.notes.append(f"game {info.game_code}: {issue}")

        if row is None:
            values = dict(facts)
            values["status"] = info.status
            values["stats_status"] = "none"
            if final:
                values.update(_result_values(info))
            session.add(
                ElGame(game_id=gid, data_source=DATA_SOURCE_LIVE, ingested_at=moment, **values)
            )
            result.created.append(gid)
            if final:
                result.results.append(gid)
                through = _later(through, info.game_date)
            continue

        changed = rekeyed_now
        if row.status == "final" and not final:
            result.regressions.append(gid)
            result.notes.append(
                f"{gid}: the service no longer calls this game played; the stored result was kept"
            )
        else:
            for name, value in facts.items():
                changed |= _assign(row, name, value)
            if final:
                values = _result_values(info)
                was_final = row.status == "final"
                score_changed = was_final and (
                    row.home_pts != values["home_pts"] or row.away_pts != values["away_pts"]
                )
                new_partials = values.get("home_partials_json")
                partials_changed = (
                    was_final
                    and new_partials is not None
                    and (
                        row.home_partials_json != new_partials
                        or row.away_partials_json != values.get("away_partials_json")
                    )
                )
                if not was_final:
                    result.results.append(gid)
                elif score_changed:
                    result.corrections.append(gid)
                changed |= _assign(row, "status", "final")
                for name, value in values.items():
                    if value is not None:
                        changed |= _assign(row, name, value)
                if (score_changed or partials_changed) and row.stats_status in (
                    "ok",
                    "quarantined",
                ):
                    # The lines were checked against the old numbers; check them against these.
                    changed |= _assign(row, "stats_status", "none")
                through = _later(through, info.game_date)
            else:
                changed |= _assign(row, "status", info.status)
        if changed:
            _assign(row, "data_source", DATA_SOURCE_LIVE)
            _assign(row, "ingested_at", moment)
            result.updated.append(gid)
        else:
            result.unchanged.append(gid)
    session.flush()
    if result.changed:
        result.version = bump_sync_version(session, through, "live", moment)
    return result


def _result_values(info: GameInfo) -> dict[str, Any]:
    """The columns a final game sets: scores, partials as reported, overtime, attendance."""
    assert info.home.score is not None and info.away.score is not None
    values: dict[str, Any] = {
        "home_pts": info.home.score,
        "away_pts": info.away.score,
        "home_partials_json": _partials_json(info.home.partials),
        "away_partials_json": _partials_json(info.away.partials),
        "ot_periods": info.ot_periods,
        "attendance": info.attendance,
    }
    return values


# ------------------------------------------------------------------------------ people


def _fill_person(person: ElPerson, info: PersonInfo) -> bool:
    changed = False
    for column, value in (
        ("display_name", info.abbreviated_name),
        ("jersey_name", info.jersey_name),
        ("birth_date", info.birth_date),
        ("height_cm", info.height_cm),
        ("weight_kg", info.weight_kg),
        ("country_code", info.country_code),
    ):
        if value is not None and getattr(person, column) is None:
            setattr(person, column, value)
            changed = True
    return changed


def _ensure_person(session: Session, info: PersonInfo) -> tuple[ElPerson, bool]:
    """The official person row for ``info``, created or filled in. ``(row, created)``."""
    person = session.get(ElPerson, info.code)
    if person is None:
        person = ElPerson(
            person_code=info.code,
            code_system="official",
            name=info.name[:96],
            display_name=(info.abbreviated_name or None),
            jersey_name=info.jersey_name,
            birth_date=info.birth_date,
            height_cm=info.height_cm,
            weight_kg=info.weight_kg,
            country_code=info.country_code,
        )
        session.add(person)
        return person, True
    _fill_person(person, info)
    return person, False


def upsert_people(
    session: Session,
    season_code: str,
    club_code: str,
    registrations: Sequence[RegistrationInfo],
    *,
    now: datetime | None = None,
) -> PeopleResult:
    """Store a club's registered players (E5): persons, and their registrations for the season.

    A registration written here is the official one (``source = 'euroleague-v2'``) and carries the
    service's position code, which is what defence by position reads. Staff were already filtered
    out by the parser. Players are never deleted by absence.
    """
    result = PeopleResult()
    ensure_season(session, season_code)
    for reg in registrations:
        if reg.club_code not in (None, club_code):
            result.rejected.append(
                Rejected(reg.person.code, f"registered with {reg.club_code}, not {club_code}")
            )
            continue
        person, created = _ensure_person(session, reg.person)
        if created:
            result.persons_created += 1
        row = session.get(ElRegistration, (season_code, club_code, reg.person.code))
        values: dict[str, Any] = {
            "dorsal": reg.dorsal,
            "position_code": reg.position_code,
            "position_name": reg.position_name,
            "active": reg.active,
            "start_date": reg.start_date,
            "end_date": reg.end_date,
        }
        if row is None:
            session.add(
                ElRegistration(
                    season_code=season_code,
                    club_code=club_code,
                    person_code=reg.person.code,
                    source=DATA_SOURCE_LIVE,
                    **values,
                )
            )
            result.registrations_created += 1
        else:
            changed = False
            for name, value in values.items():
                if value is not None and getattr(row, name) != value:
                    setattr(row, name, value)
                    changed = True
            if changed:
                result.registrations_updated += 1
    session.flush()
    return result


# ------------------------------------------------------------------------------ box score


_LINE_COLUMNS: Final[tuple[str, ...]] = (
    "seconds_played",
    "pts",
    "fgm2",
    "fga2",
    "fgm3",
    "fga3",
    "ftm",
    "fta",
    "oreb",
    "dreb",
    "reb",
    "ast",
    "stl",
    "tov",
    "blk",
    "blk_against",
    "pf",
    "fouls_drawn",
    "plus_minus",
    "pir_official",
)


def _ensure_registration(
    session: Session, season_code: str, club_code: str, line: PlayerLine
) -> None:
    row = session.get(ElRegistration, (season_code, club_code, line.person.code))
    if row is None:
        session.add(
            ElRegistration(
                season_code=season_code,
                club_code=club_code,
                person_code=line.person.code,
                dorsal=line.dorsal,
                position_code=line.position_code,
                position_name=line.position_name,
                source=DATA_SOURCE_LIVE,
            )
        )
        return
    for name, value in (
        ("dorsal", line.dorsal),
        ("position_code", line.position_code),
        ("position_name", line.position_name),
    ):
        if value is not None and getattr(row, name) is None:
            setattr(row, name, value)


def write_box_score(
    session: Session,
    game_id: str,
    box: BoxScore,
    *,
    now: datetime | None = None,
) -> BoxResult:
    """Check a box score against the game the store holds, then write it, keep it, or refuse it.

    See the module docstring. The game must exist and be final (the schedule supplies the final
    score the box is checked against); otherwise the result is ``refused`` and nothing changes.
    """
    moment = now or utcnow()
    game = session.get(ElGame, game_id)
    if game is None:
        return BoxResult(game_id, "refused", None, reason="no such game in the store")
    if game.status != "final" or game.home_pts is None or game.away_pts is None:
        return BoxResult(
            game_id,
            "refused",
            game.stats_status,
            reason="the schedule has not given this game a final score yet",
        )
    if box.season_code is not None and box.season_code != game.season_code:
        return BoxResult(
            game_id,
            "refused",
            game.stats_status,
            reason=f"the box score is for season {box.season_code}, not {game.season_code}",
        )

    def stored_partials(raw: str | None) -> list[int] | None:
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if isinstance(value, list) and all(isinstance(v, int) for v in value) else None

    facts = invariants.box_to_facts(
        box,
        final_home=game.home_pts,
        final_away=game.away_pts,
        home_club=game.home_club_code,
        away_club=game.away_club_code,
        home_partials=stored_partials(game.home_partials_json),
        away_partials=stored_partials(game.away_partials_json),
        ot_periods=game.ot_periods,
    )
    check = invariants.check_game(facts)
    digest = parse.box_digest(box)

    if check.hard:
        flipped = game.stats_status != "quarantined"
        game.stats_status = "quarantined"
        version = None
        if flipped:
            version = bump_sync_version(session, game.game_date, "live", moment)
        session.flush()
        return BoxResult(
            game_id,
            "quarantined",
            "quarantined",
            hard=check.hard,
            soft=check.soft,
            reason=invariants.describe(check.hard),
            version=version,
            digest=digest,
        )

    if game.stats_status == "ok" and game.raw_sha256 == digest:
        return BoxResult(game_id, "unchanged", "ok", soft=check.soft, digest=digest)

    # One game is one transaction: the job commits after each game, and a failure rolls this one
    # back whole, so the previous lines are never left half-replaced. (A savepoint is avoided on
    # purpose: the stdlib SQLite driver mishandles them without engine hooks this package does not
    # own.)
    session.execute(delete(ElPlayerGame).where(ElPlayerGame.game_id == game_id))
    session.execute(delete(ElTeamGame).where(ElTeamGame.game_id == game_id))
    clubs = {"home": game.home_club_code, "away": game.away_club_code}
    written = 0
    for side in box.sides():
        club = clubs[side.side]
        for line in side.players:
            _ensure_person(session, line.person)
            _ensure_registration(session, game.season_code, club, line)
            stats = {c: line.stats.get(c) for c in _LINE_COLUMNS}
            session.add(
                ElPlayerGame(
                    game_id=game_id,
                    person_code=line.person.code,
                    club_code=club,
                    participation=line.participation,
                    is_starter=line.is_starter,
                    position_code_at_game=line.position_code,
                    dorsal=line.dorsal,
                    **stats,
                )
            )
            written += 1
    for side in box.sides():
        is_home = side.side == "home"
        club = clubs[side.side]
        pts, opp_pts = (game.home_pts, game.away_pts) if is_home else (game.away_pts, game.home_pts)
        totals = {c: side.totals.stats.get(c) for c in _LINE_COLUMNS if c != "pts"}
        totals["seconds_played"] = invariants.players_seconds(side)
        session.add(
            ElTeamGame(
                game_id=game_id,
                club_code=club,
                is_home=is_home,
                opp_club_code=clubs["away" if is_home else "home"],
                won=pts > opp_pts,
                pts=pts,
                opp_pts=opp_pts,
                **totals,
            )
        )
    game.stats_status = "ok"
    game.raw_sha256 = digest
    game.data_source = DATA_SOURCE_LIVE
    game.ingested_at = moment
    if game.ot_periods is None and check.ot_periods is not None:
        game.ot_periods = check.ot_periods
    session.flush()
    version = bump_sync_version(session, game.game_date, "live", moment)
    return BoxResult(
        game_id,
        "written",
        "ok",
        soft=check.soft,
        lines=written,
        version=version,
        digest=digest,
    )


# ------------------------------------------------------------------------- raw payloads


def record_raw_payload(
    session: Session,
    fetched: Fetched,
    ref: RawRef | None,
    store: RawPayloadStore | None = None,
    *,
    keep: int = RAW_PAYLOADS_KEPT_PER_URL,
) -> ElRawPayload | None:
    """Note a stored raw body in ``el_raw_payload`` and keep only the last ``keep`` per URL.

    Only ``200`` bodies are recorded (``ref`` is ``None`` for anything else). A body identical to
    the newest one for the URL refreshes that row's time instead of adding a duplicate. Pruned
    rows delete their file too, unless a surviving row still points at it.
    """
    if ref is None:
        return None
    rows = list(
        session.execute(
            select(ElRawPayload)
            .where(ElRawPayload.url == fetched.url)
            .order_by(ElRawPayload.fetched_at.desc(), ElRawPayload.payload_id.desc())
        ).scalars()
    )
    stamp = (
        fetched.fetched_at.replace(tzinfo=None) if fetched.fetched_at.tzinfo else fetched.fetched_at
    )
    if rows and rows[0].sha256 == ref.sha256:
        rows[0].fetched_at = stamp
        rows[0].http_status = fetched.status
        session.flush()
        return rows[0]
    row = ElRawPayload(
        url=fetched.url,
        endpoint_key=fetched.endpoint_key,
        fetched_at=stamp,
        http_status=fetched.status,
        sha256=ref.sha256,
        bytes=ref.bytes,
        path=str(ref.path),
    )
    session.add(row)
    session.flush()
    survivors = [row, *rows][:keep]
    doomed = [r for r in [row, *rows][keep:]]
    kept_paths = {r.path for r in survivors}
    for old in doomed:
        if store is not None and old.path and old.path not in kept_paths:
            store.delete(old.path)
        session.delete(old)
    session.flush()
    return row


# ---------------------------------------------------------------------------- operations


def log_run(
    session: Session,
    *,
    job: str,
    started_at: datetime,
    status: str,
    games_written: int | None = None,
    rows_written: int | None = None,
    invariant_failed: str | None = None,
    error: str | None = None,
    source_sha256: str | None = None,
    detail: Mapping[str, Any] | None = None,
    finished_at: datetime | None = None,
) -> ElIngestLog:
    """Append one ``el_ingest_log`` row. ``status`` is ``ok``, ``partial``, ``quarantined`` or
    ``failed``; ``invariant_failed`` names the rule(s) that quarantined a game."""
    row = ElIngestLog(
        started_at=started_at,
        finished_at=finished_at or utcnow(),
        job=job,
        status=status,
        games_written=games_written,
        rows_written=rows_written,
        invariant_failed=(invariant_failed[:2000] if invariant_failed else None),
        error=(error[:2000] if error else None),
        source_sha256=source_sha256,
        detail_json=json.dumps(detail, sort_keys=True, default=str) if detail else None,
    )
    session.add(row)
    session.flush()
    return row


def read_cursor(session: Session, key: str) -> dict[str, Any]:
    """A job's own cursor (``el_job_state.cursor_json``), or ``{}``. The worker never touches it."""
    row = session.get(ElJobState, key)
    if row is None or not row.cursor_json:
        return {}
    try:
        value = json.loads(row.cursor_json)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def write_cursor(session: Session, key: str, cursor: Mapping[str, Any]) -> None:
    """Replace a job's cursor. Creates the row if the worker has not yet; never touches the
    worker's three columns (``last_started_at``, ``last_success_at``, ``last_error``)."""
    row = session.get(ElJobState, key)
    text = json.dumps(cursor, sort_keys=True, default=str)
    if row is None:
        session.add(ElJobState(job_key=key, cursor_json=text))
    else:
        row.cursor_json = text
    session.flush()


def get_source_state(session: Session, key: str) -> ElSourceState | None:
    return session.get(ElSourceState, key)


def mark_source_ok(
    session: Session,
    key: str,
    now: datetime,
    *,
    detail: Mapping[str, Any] | None = None,
    touch_sync: bool = True,
) -> ElSourceState:
    """The source answered and was understood: ``ok``, failures reset, any pause cleared.

    ``touch_sync`` also clears the store-wide ``el_sync_state.paused_until``; a headline feed
    passes ``False``, because a feed answering says nothing about the data service."""
    row = session.get(ElSourceState, key)
    if row is None:
        row = ElSourceState(source_key=key, state="ok", consecutive_failures=0)
        session.add(row)
    row.state = "ok"
    row.last_success_at = now
    row.last_error = None
    row.consecutive_failures = 0
    row.paused_until = None
    if detail is not None:
        row.detail_json = json.dumps(detail, sort_keys=True, default=str)
    if touch_sync:
        read_sync_state(session).paused_until = None
    session.flush()
    return row


def mark_source_failed(
    session: Session,
    key: str,
    now: datetime,
    *,
    state: str,
    error: str,
    paused_until: datetime | None = None,
    detail: Mapping[str, Any] | None = None,
    touch_sync: bool = True,
) -> ElSourceState:
    """Record a failure: ``blocked`` (with ``paused_until``), ``unreadable`` or ``error``.

    ``last_success_at`` is kept, so a source that worked yesterday still says so. ``touch_sync``
    mirrors the pause into ``el_sync_state`` (the data service); a headline feed passes ``False``.
    """
    row = session.get(ElSourceState, key)
    if row is None:
        row = ElSourceState(source_key=key, state=state, consecutive_failures=0)
        session.add(row)
    row.state = state
    row.last_error = error[:2000]
    row.consecutive_failures = (row.consecutive_failures or 0) + 1
    row.paused_until = paused_until
    if detail is not None:
        row.detail_json = json.dumps(detail, sort_keys=True, default=str)
    if touch_sync:
        read_sync_state(session).paused_until = paused_until
    session.flush()
    return row
