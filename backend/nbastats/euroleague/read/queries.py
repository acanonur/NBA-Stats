"""The EuroLeague read side's data access: one context, loaded once, that every builder shares.

Every EuroLeague payload (a matchup, a defence table, a round, a projection) needs the same
handful of things: which season, what the clock says, which clubs and which games exist, which
model settings are in force, and how fresh the store is. Rather than have a dozen builders each
re-read them (and quietly disagree about "now"), :func:`build_context` reads them once into a
:class:`ReadContext` and the builders take that. The context is also the one place a clock enters
the read side: builders never call ``datetime.now``, so a fixture, a test and the worker can all
ask "what would this have said at 09:00 on 12 October" and get a reproducible answer.

Why the data layer is not the model
-----------------------------------
This module only *reads*: games, lines, clubs, people, settings. It computes nothing that a
statistician would call a number except through the shared pure core (:mod:`nbastats.shared`).
The team-scoring scope in particular is :func:`load_team_games`, and its rule is the design's
(section 7.1): **only final games whose box score passed the invariants count** (``status =
'final'`` and ``stats_status = 'ok'``). A final score with no box score yet does not count
until its lines arrive, which is at most one ten-minute poll, and it means a club's points
allowed always equals the sum of what its opponents' players scored. A quarantined game
(``stats_status = 'quarantined'``) never counts: its score and its box disagreed, and which one
is wrong is not knowable here, so neither is used. This is the same fail-closed stance as the
parsers.

Games have two clocks, and this module keeps them apart
-------------------------------------------------------
``el_game.game_date`` is the day in Europe/Berlin, where the competition keeps its calendar;
``tipoff_utc`` is the instant, and may be NULL (a round-one workbook import does not know it).
:func:`game_start` is the tip-off, or midnight of the Berlin day when it is unknown; it is the
instant a game's *inputs* are cut off at ("strictly before tip-off"). :func:`game_expected_end`
is the later of the two things the design uses to decide a game has certainly been played:
tip-off plus three hours, or 23:59 Berlin plus three hours when the tip-off is unknown. A
return "expected in Rounds 2-3" has passed only once the last game of Round 3 has *finished*,
not when it tips off: using the tip-off would drop the entry for that very last game while it
was being projected.

Errors
------
Builders raise :class:`nbastats.api.errors.ApiError` (the same envelope the NBA routes use) so a
route needs no translation and a widget can attach the error body to one tile. The three codes
the design adds (``league_unavailable``, ``club_not_found``, ``invalid_status``) are built here,
because ``api/errors.py`` belongs to another package and ``ApiError`` accepts any code with an
explicit status.

What this module deliberately does not do
-----------------------------------------
It never writes, it never opens a session (the caller owns it), and it never reads the NBA
store. Nothing here knows about HTTP.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Any, Callable, Final, Iterable, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...api import errors as api_errors
from ...api.errors import ApiError
from ...shared import refs
from ...shared.league_profile import EUROLEAGUE_KEY
from ...shared.positions import euroleague_position_bucket
from ...shared.team_form import TeamGame
from ..db import read_identity, read_sync_state
from ..models import (
    ElClub,
    ElClubSeason,
    ElGame,
    ElIntelOverride,
    ElIntelStatus,
    ElPerson,
    ElProjectionLedger,
    ElRegistration,
    ElSeason,
    ElTeamGame,
)
from ..profile import (
    PHASES,
    PROFILE,
    fold_name,
    normalise_club_code,
    parse_season_ref,
)
from ..settings import EffectiveSetting, effective_settings, settings_digest

__all__ = [
    "BERLIN",
    "UTC",
    "league_unavailable",
    "club_not_found",
    "invalid_status",
    "bad_request",
    "game_not_found",
    "utc_now",
    "aware",
    "naive_utc",
    "game_start",
    "game_reference",
    "game_expected_end",
    "game_has_result",
    "counts_for_scoring",
    "parse_phases",
    "round_status",
    "ReadContext",
    "build_context",
    "context_for_game",
    "load_team_games",
    "memoise",
    "clear_memo",
    "digest",
    "MEMO_SIZE",
]

BERLIN: Final = ZoneInfo(PROFILE.schedule_tz)
UTC: Final = timezone.utc

#: The most computed tables kept between requests. A season's aggregates are cheap to rebuild
#: (about 9,000 player lines) but not free, and every key includes the sync version, so a stale
#: entry can never be served; this only bounds memory.
MEMO_SIZE: Final = 12


# --------------------------------------------------------------------------- errors


def league_unavailable(reason: str) -> ApiError:
    """503 ``league_unavailable``: the EuroLeague is off, misconfigured or holds no source."""
    return ApiError("league_unavailable", reason, http_status=503, recoverable=True)


def club_not_found(code: Any) -> ApiError:
    """404 ``club_not_found``."""
    return ApiError("club_not_found", f"No EuroLeague club with code {code}.", http_status=404)


def invalid_status(message: str, field_name: str | None = "status") -> ApiError:
    """400 ``invalid_status``: an availability status outside the five."""
    return ApiError("invalid_status", message, http_status=400, field=field_name)


def bad_request(message: str, field_name: str | None = None) -> ApiError:
    return api_errors.bad_request(message, field_name)


def game_not_found(game_id: Any) -> ApiError:
    return api_errors.game_not_found(game_id)


# --------------------------------------------------------------------------- time


def utc_now() -> datetime:
    """The clock: aware UTC. The only place the read side asks the system for the time."""
    return datetime.now(UTC)


def aware(value: datetime | None) -> datetime | None:
    """``value`` as aware UTC. A naive datetime is the store's convention and means UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def naive_utc(value: datetime) -> datetime:
    """The store's form of a moment: naive UTC."""
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def _berlin_midnight(day: date) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=BERLIN).astimezone(UTC)


def game_start(game: ElGame) -> datetime:
    """The instant a game's inputs are cut off at: the tip-off, else midnight of its Berlin day.

    Midnight is the conservative reading of an unknown tip-off: only what was known before the
    game's *day* began is allowed to inform a projection of it.
    """
    return aware(game.tipoff_utc) or _berlin_midnight(game.game_date)


def game_reference(game: ElGame) -> datetime:
    """The moment ``resultPending`` is measured from: tip-off, else 23:59 on the Berlin day."""
    if game.tipoff_utc is not None:
        return aware(game.tipoff_utc)  # type: ignore[return-value]
    return datetime.combine(game.game_date, time(23, 59), tzinfo=BERLIN).astimezone(UTC)


def game_expected_end(game: ElGame) -> datetime:
    """When the game has certainly been played (see the module docstring)."""
    return game_reference(game) + refs.RESULT_PENDING_AFTER


# --------------------------------------------------------------------------- games


def game_has_result(game: ElGame) -> bool:
    return game.status == "final" and game.home_pts is not None and game.away_pts is not None


def counts_for_scoring(game: ElGame) -> bool:
    """The team-scoring scope: final, and the box score passed its invariants."""
    return game_has_result(game) and game.stats_status == "ok"


def parse_phases(raw: str | None) -> tuple[str, ...] | None:
    """``RS,PO`` as ``("RS", "PO")``; ``None`` or blank means every phase (so ``None``).

    An unknown phase is ``400 bad_request``, never ignored: a typo silently widening a scope
    would change every number it feeds.
    """
    if raw is None or not raw.strip():
        return None
    out: list[str] = []
    for part in raw.split(","):
        code = part.strip().upper()
        if not code:
            continue
        if code not in PHASES:
            raise bad_request(
                f"{part.strip()!r} is not a phase; expected one of {list(PHASES)}.", "phase"
            )
        if code not in out:
            out.append(code)
    return tuple(out) or None


def round_status(statuses: Iterable[str]) -> str:
    """``upcoming``, ``inProgress``, ``resultPending`` or ``complete`` from a round's game states.

    A postponed game is not waiting for anything this round can show, so it is ignored; a round
    of only postponed games is ``upcoming`` (it has not been played).
    """
    counts = {"scheduled": 0, "resultPending": 0, "final": 0}
    for status in statuses:
        if status in counts:
            counts[status] += 1
    scheduled, pending, final = counts["scheduled"], counts["resultPending"], counts["final"]
    if scheduled == 0 and pending == 0:
        return "complete" if final else "upcoming"
    if scheduled == 0:
        return "resultPending"
    if final == 0 and pending == 0:
        return "upcoming"
    return "inProgress"


# --------------------------------------------------------------------------- the context


@dataclass
class ReadContext:
    """Everything a builder needs about one season of the store, read once."""

    session: Session
    now: datetime
    season: ElSeason
    kind: str | None
    sync_version: int
    data_through: date | None
    mode: str
    clubs: dict[str, ElClub]
    club_seasons: dict[str, ElClubSeason]
    games: list[ElGame]
    settings: dict[str, EffectiveSetting]
    cache: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ identity

    @property
    def is_demo(self) -> bool:
        return self.kind == "synthetic"

    @property
    def season_code(self) -> str:
        return self.season.season_code

    @property
    def season_label(self) -> str:
        return self.season.label

    @property
    def games_by_id(self) -> dict[str, ElGame]:
        cached = self.cache.get("games_by_id")
        if cached is None:
            cached = self.cache["games_by_id"] = {g.game_id: g for g in self.games}
        return cached

    def setting(self, key: str) -> float:
        return self.settings[key].value

    def chance_table(self) -> dict[str, float]:
        return {s: self.settings[f"statusChance.{s}"].value for s in PROFILE.chance_table()}

    # ------------------------------------------------------------------ clubs

    def require_club(self, raw: Any) -> str:
        """The normalised club code, or ``404 club_not_found``."""
        code = normalise_club_code(raw)
        if code is None or code not in self.clubs:
            raise club_not_found(raw)
        return code

    def team_ref(self, code: str) -> dict[str, Any]:
        club = self.clubs.get(code)
        if club is None:  # a game that names a club the store has no row for: still say who
            return refs.el_team_ref(code, code)
        return refs.el_team_ref(code, club.name, club.short_name, club.tv_code)

    def club_name(self, code: str) -> str:
        club = self.clubs.get(code)
        return club.name if club is not None else code

    # ------------------------------------------------------------------ people

    @property
    def persons(self) -> dict[str, ElPerson]:
        cached = self.cache.get("persons")
        if cached is None:
            rows = self.session.execute(select(ElPerson)).scalars()
            cached = self.cache["persons"] = {p.person_code: p for p in rows}
        return cached

    @property
    def registrations(self) -> dict[tuple[str, str], ElRegistration]:
        """The season's registrations keyed by ``(club, person)``."""
        cached = self.cache.get("registrations")
        if cached is None:
            rows = self.session.execute(
                select(ElRegistration).where(ElRegistration.season_code == self.season_code)
            ).scalars()
            cached = self.cache["registrations"] = {(r.club_code, r.person_code): r for r in rows}
        return cached

    def registrations_for_club(self, club: str) -> list[ElRegistration]:
        by_club = self.cache.get("registrations_by_club")
        if by_club is None:
            by_club = {}
            for (code, _), row in self.registrations.items():
                by_club.setdefault(code, []).append(row)
            self.cache["registrations_by_club"] = by_club
        return list(by_club.get(club, ()))

    def registration_of(self, person: str, club: str | None = None) -> ElRegistration | None:
        """The registration for ``person`` at ``club``, else (club unknown) any of his."""
        if club is not None:
            return self.registrations.get((club, person))
        for (_, code), row in self.registrations.items():
            if code == person:
                return row
        return None

    @property
    def has_official_registrations(self) -> bool:
        """True when the store holds any registration with an official position code."""
        cached = self.cache.get("has_official")
        if cached is None:
            cached = self.cache["has_official"] = any(
                r.position_code is not None for r in self.registrations.values()
            )
        return cached

    def player_ref(self, person: str, club: str | None = None) -> dict[str, Any]:
        """A ``LeaguePlayerRef``. ``position`` is the *official* registration bucket only: a
        workbook author's label is an estimate and travels as ``positionRaw`` at most."""
        row = self.persons.get(person)
        name = row.name if row is not None else person
        reg = self.registration_of(person, club)
        bucket = euroleague_position_bucket(reg.position_code) if reg is not None else None
        raw = None
        if reg is not None:
            raw = reg.position_name or reg.position5_workbook
        return refs.el_player_ref(
            person,
            name,
            position=bucket,
            position_raw=raw,
            jersey=reg.dorsal if reg is not None else None,
        )

    def player_name(self, person: str) -> str:
        row = self.persons.get(person)
        return row.name if row is not None else person

    def folded_squad(self, club: str) -> dict[str, list[str]]:
        """``{folded name: [person codes]}`` for a club's season squad (for matching a name)."""
        out: dict[str, list[str]] = {}
        for reg in self.registrations_for_club(club):
            row = self.persons.get(reg.person_code)
            if row is not None:
                out.setdefault(fold_name(row.name), []).append(reg.person_code)
        return out

    # ------------------------------------------------------------------ games

    def game(self, game_id: str) -> ElGame:
        game = self.games_by_id.get(game_id)
        if game is None:
            raise game_not_found(game_id)
        return game

    def game_status(self, game: ElGame) -> str:
        return refs.game_status(
            league=EUROLEAGUE_KEY,
            has_result=game_has_result(game),
            now=self.now,
            tipoff_utc=game.tipoff_utc,
            game_date=game.game_date,
            postponed=game.status == "postponed",
        )

    def game_ref(self, game: ElGame) -> dict[str, Any]:
        status = self.game_status(game)
        return refs.game_ref(
            EUROLEAGUE_KEY,
            game_id=game.game_id,
            date=game.game_date,
            home=self.team_ref(game.home_club_code),
            away=self.team_ref(game.away_club_code),
            status=status,
            tipoff_utc=game.tipoff_utc,
            venue=game.venue_name,
            is_neutral=game.is_neutral,
            round_number=game.round_number,
            phase=game.phase_code,
            home_pts=game.home_pts,
            away_pts=game.away_pts,
            overtime_periods=game.ot_periods,
        )

    def games_of_round(self, round_number: int) -> list[ElGame]:
        return [g for g in self.games if g.round_number == round_number]

    def club_games(self, club: str) -> list[ElGame]:
        return [g for g in self.games if club in (g.home_club_code, g.away_club_code)]

    def rounds(self) -> list[int]:
        return sorted({g.round_number for g in self.games})

    def next_round(self) -> int | None:
        """The round ``round=next`` means: the first with a game still to be played; failing
        that, the first still waiting for results; failing that, the last round there is."""
        for wanted in ("scheduled", "resultPending"):
            candidates = sorted(
                {g.round_number for g in self.games if self.game_status(g) == wanted}
            )
            if candidates:
                return candidates[0]
        rounds = self.rounds()
        return rounds[-1] if rounds else None

    def next_game_of(self, club: str) -> ElGame | None:
        upcoming = [g for g in self.club_games(club) if self.game_status(g) == "scheduled"]
        if not upcoming:
            return None
        return min(upcoming, key=lambda g: (game_start(g), g.game_id))

    def round_end_at(self, round_number: int) -> datetime | None:
        """When the last game of ``round_number`` has certainly been played, or ``None`` when the
        store has no game in that round (the calendar is unknown, so the rule cannot fire)."""
        ends = [game_expected_end(g) for g in self.games if g.round_number == round_number]
        return max(ends) if ends else None

    # ------------------------------------------------------------------ keys

    def data_key(self) -> tuple[Any, ...]:
        """What a memoised computation must be keyed on so a stale result can never be served.

        The sync version moves with the data; the status, override and ledger high-water marks
        and the settings digest cover the writes that do not touch it.
        """
        cached = self.cache.get("data_key")
        if cached is None:
            session = self.session
            marks = tuple(
                session.execute(select(func.coalesce(func.max(column), 0))).scalar_one()
                for column in (
                    ElIntelStatus.status_id,
                    ElIntelOverride.override_id,
                    ElProjectionLedger.ledger_id,
                )
            )
            cached = self.cache["data_key"] = (
                str(session.get_bind().url),
                self.season_code,
                self.sync_version,
                marks,
                settings_digest(session),
            )
        return cached


def _resolve_season(session: Session, raw: str | None) -> ElSeason:
    text = (raw or "").strip()
    if not text or text.lower() == "latest":
        rows = (
            session.execute(select(ElSeason).order_by(ElSeason.start_year.desc())).scalars().all()
        )
        if not rows:
            raise api_errors.ApiError(
                "season_not_loaded",
                "No EuroLeague season is loaded yet.",
                http_status=422,
                recoverable=True,
                field="season",
            )
        for row in rows:
            if row.is_current:
                return row
        return rows[0]
    ref = parse_season_ref(text)
    if ref is None:
        raise bad_request(
            f"{text!r} is not a season such as 'E2026', '2026-27' or 'latest'.", "season"
        )
    row = session.get(ElSeason, ref.code)
    if row is None:
        raise api_errors.season_not_loaded(ref.code)
    return row


def build_context(
    session: Session, season: str | None = None, *, now: datetime | None = None
) -> ReadContext:
    """Read a season's shared facts once. ``season`` is ``E2026``, ``2026-27``, ``latest`` or None."""
    row = _resolve_season(session, season)
    return _context(session, row, now)


def context_for_game(session: Session, game_id: str, *, now: datetime | None = None) -> ReadContext:
    """The context of the season the named game belongs to, or ``404 game_not_found``."""
    game = session.get(ElGame, game_id)
    if game is None:
        raise game_not_found(game_id)
    season = session.get(ElSeason, game.season_code)
    if season is None:
        raise game_not_found(game_id)
    return _context(session, season, now)


def _context(session: Session, season: ElSeason, now: datetime | None) -> ReadContext:
    identity = read_identity(session)
    state = read_sync_state(session)
    clubs = {c.club_code: c for c in session.execute(select(ElClub)).scalars()}
    club_seasons = {
        cs.club_code: cs
        for cs in session.execute(
            select(ElClubSeason).where(ElClubSeason.season_code == season.season_code)
        ).scalars()
    }
    games = list(
        session.execute(
            select(ElGame)
            .where(ElGame.season_code == season.season_code)
            .order_by(ElGame.round_number, ElGame.game_date, ElGame.tipoff_utc, ElGame.game_id)
        ).scalars()
    )
    return ReadContext(
        session=session,
        now=aware(now) if now is not None else utc_now(),  # type: ignore[arg-type]
        season=season,
        kind=identity.kind if identity is not None else None,
        sync_version=state.sync_version or 0,
        data_through=state.data_through,
        mode=state.mode,
        clubs=clubs,
        club_seasons=club_seasons,
        games=games,
        settings=effective_settings(session),
    )


# --------------------------------------------------------------------------- team games


def load_team_games(
    ctx: ReadContext,
    phases: Sequence[str] | None = None,
    before: datetime | None = None,
) -> list[TeamGame]:
    """One :class:`TeamGame` per side of every game in the scoring scope.

    ``phases`` narrows to those phase codes (``None`` is all of them); ``before`` keeps only
    games that *started* strictly before that instant, which is how a game's matchup is cut off
    at its own tip-off.
    """
    wanted = {g.game_id: g for g in ctx.games if counts_for_scoring(g)}
    if phases:
        wanted = {k: g for k, g in wanted.items() if g.phase_code in phases}
    if before is not None:
        cutoff = aware(before)
        wanted = {k: g for k, g in wanted.items() if game_start(g) < cutoff}  # type: ignore[operator]
    if not wanted:
        return []
    rows = ctx.session.execute(
        select(ElTeamGame).where(ElTeamGame.game_id.in_(list(wanted)))
    ).scalars()
    out: list[TeamGame] = []
    for row in rows:
        game = wanted[row.game_id]
        out.append(
            TeamGame(
                game_id=game.game_id,
                date=game.game_date,
                team=row.club_code,
                opponent=row.opp_club_code,
                is_home=bool(row.is_home),
                pts=row.pts,
                opp_pts=row.opp_pts,
                is_neutral=game.is_neutral,
                team_seconds=row.seconds_played,
                overtime_periods=game.ot_periods,
                tipoff_utc=game.tipoff_utc,
            )
        )
    out.sort(key=lambda g: (g.date, g.game_id, str(g.team)))
    return out


# --------------------------------------------------------------------------- memo

_MEMO: "OrderedDict[tuple[Any, ...], Any]" = OrderedDict()
_MEMO_LOCK = threading.Lock()


def memoise(ctx: ReadContext, name: str, extra: Any, build: Callable[[], Any]) -> Any:
    """Return ``build()`` for ``(ctx.data_key(), name, extra)``, computing it once.

    The key carries the sync version, the status/override/ledger high-water marks and the
    settings digest, so any change that could alter the answer makes a new key; the table
    only ever serves an answer computed from exactly the data now in the store. Bounded to
    :data:`MEMO_SIZE` entries, oldest evicted.
    """
    key = (*ctx.data_key(), name, json.dumps(extra, sort_keys=True, default=str))
    with _MEMO_LOCK:
        if key in _MEMO:
            _MEMO.move_to_end(key)
            return _MEMO[key]
    value = build()
    with _MEMO_LOCK:
        _MEMO[key] = value
        _MEMO.move_to_end(key)
        while len(_MEMO) > MEMO_SIZE:
            _MEMO.popitem(last=False)
    return value


def clear_memo() -> None:
    """Forget every memoised table (tests)."""
    with _MEMO_LOCK:
        _MEMO.clear()


def digest(*parts: Any) -> str:
    """A stable SHA-256 of JSON-able parts, for the ledger's input fingerprints."""
    canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
