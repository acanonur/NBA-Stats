"""Availability in force: which status applies to a player in a game, and what the screen may say.

The NBA publishes its injury report as a PDF in Eastern-time quarter-hour slots, and the intel
package (:mod:`nbastats.nba_intel`) turns each slot into a snapshot of dated, sourced rows. A
person can add a status of their own (an *override*). This module is the read side of that pile.
Three different questions are asked of it, and the shared core
(:mod:`nbastats.shared.availability`) answers each separately because conflating them is how a
product tells a small lie:

1. **What do we show?** Exactly what the source said, with its age and its link. A player with
   no entry has ``status: null``, an em dash on screen, never "available".
2. **What does the model assume?** A player with no *usable* entry plays with probability one.
   That is a convenience, not a claim, so every projection lists those players under
   ``assumptions.assumedAvailable`` with a basis: ``notOnSubmittedReport`` (the team filed and he
   is not on it, which carries meaning because the league obliges teams to list anyone whose
   participation may be affected), ``teamReportPending`` (the team's report is NOT YET
   SUBMITTED) or ``noReportPublished`` (no report for this team and game exists).
3. **Does an old entry still count?** An entry stops driving a projection when its expected
   return has passed, when a box score after it shows the player on court, or when it is more
   than fourteen days old (long-term entries excepted). It still appears in the list, marked
   ``inForce: false`` and ``isStale: true``.

What this module adds to the shared rules
-----------------------------------------
:class:`AvailabilityBook` loads a team's rows and the overrides, builds the shared
:class:`~nbastats.shared.availability.StatusEntry` objects and answers
``resolve(player, game, as_of, before)``. Four NBA facts shape the answer:

* **A submitted list is authoritative for its game.** The report is cumulative per slot: each
  snapshot restates the team's list for the game. When a newer, fully parsed snapshot says the
  team has *submitted* for a game, a player an older snapshot listed for that game and the newer
  one does not is no longer listed, so the older row is dropped. ("Available" rows keep a cleared
  player on the list; absence from a submitted list is the league's own signal.) A snapshot that
  only partly parsed adds its rows but never removes anyone, because a dropped line must not
  clear a player.
* **Information is cut off at ``as_of``.** A row counts only when it was *recorded* by then and
  published before it, an override only when entered by then and not yet cleared, and a retraction
  only once made. That is what lets a projection be rebuilt "as of tip-off" from exactly what was
  knowable, and it is what the no-future-leakage test checks.
* **A league report outranks an older override.** An override entered before the newest snapshot
  arrived is obsolete (the league has spoken since); the shared rule applies it.
* **Staleness for display is the snapshot's clock.** An entry is stale after an hour inside a
  reporting window and a day outside one, and out-of-force entries are always stale. An entry from
  a report is also stale once a *later* slot has been fetched and could not be read: the league
  has published something newer that this installation could not make sense of, so the older list
  can no longer be vouched for (the whole report then says ``unreadable``).

The demo league
---------------
:func:`statuses_allowed` is the one question "may real injury statuses be shown here?": it is no
when the store holds the seeded demo league, judged by the store's own rows
(:func:`nbastats.nba_intel.store.store_is_synthetic`). A demo store answers ``state: "disabled"``
with the reason, lists nothing, and refuses to record an override: invented games are never
placed beside real statuses, and a manual re-seed of a live file cannot get round it. The
fixture exporter lifts the refusal for its build (it lists invented players with invented
statuses to show the entry shape), and that is the only place it does.

The two writes
--------------
``POST /v1/availability`` and ``DELETE /v1/availability/{overrideId}`` end here
(:func:`record_override`, :func:`clear_override`). An override is a status a person typed for a
player, in force until cleared or until a newer league report arrives. The status must be one of
the five (an unknown word is ``400 invalid_status``; the workbook's habit of reading a typo as
"certainly plays" is not ported), and a link to a gambling operator's site is dropped with the
notice kept, by :func:`nbastats.nba_intel.status.add_override`.

Nothing here reads article text, and nothing here fetches anything.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final, Iterable, Sequence

from sqlalchemy import func, select

from ..api.errors import ApiError
from ..models import Game, PlayerGameBasic
from ..nba_intel import status as intel_status
from ..nba_intel import store as intel_store
from ..nba_intel.models import (
    NbaIntelOverride,
    NbaIntelSnapshot,
    NbaIntelStatus,
    NbaIntelTeamReport,
)
from ..shared import refs
from ..shared.availability import (
    ForceVerdict,
    InvalidStatusError,
    StatusEntry,
    age_minutes,
    assumed_available_basis,
    display_chance,
    entry_in_force,
    entry_is_stale,
    normalise_status,
    select_effective_entry,
    status_label,
)
from .queries import (
    EASTERN,
    PROFILE,
    ReadContext,
    aware,
    bad_request,
    game_not_found,
    invalid_status,
    lock_deadline,
    naive_utc,
    season_year,
    team_not_found,
)

__all__ = [
    "DEMO_REFUSAL",
    "HISTORY",
    "Resolved",
    "Listed",
    "statuses_allowed",
    "injuries_switch_on",
    "AvailabilityBook",
    "get_book",
    "source_of",
    "latest_team_of",
    "entry_payload",
    "summary_for_team",
    "build_availability_report",
    "review_queue",
    "record_override",
    "clear_override",
]

#: What the demo league says in place of an injury report, and why a write is refused.
DEMO_REFUSAL: Final = intel_store.SYNTHETIC_REASON

#: How far back the book reads status rows. A season is long enough for every entry the in-force
#: rules could keep alive (a long-term absence is listed again at every report).
HISTORY: Final = timedelta(days=400)

_MAX_FUTURE: Final = timedelta(minutes=5)
_ON: Final = {"1", "true", "t", "yes", "y", "on"}
_OFF: Final = {"0", "false", "f", "no", "n", "off"}


def statuses_allowed(session: Any) -> bool:
    """May real injury statuses be shown, and written, in this store? No for the demo league.

    A single function so the rule is stated once; the fixture exporter replaces it for its own
    build. Judged by the store's rows, not by how the process was started.
    """
    return not intel_store.store_is_synthetic(session)


def injuries_switch_on(environ: Any = None) -> bool:
    """``HARDWOOD_NBA_INJURIES``: unset means on, a recognised value means itself, anything else
    means off (the safe reading, and the one the worker applies)."""
    raw = (os.environ if environ is None else environ).get("HARDWOOD_NBA_INJURIES")
    if raw is None or not raw.strip():
        return True
    lowered = raw.strip().lower()
    if lowered in _ON:
        return True
    return False if lowered in _OFF else False


# --------------------------------------------------------------------------- the book


@dataclass(frozen=True)
class Resolved:
    """The entry that applies to one player in one game, and whether it may drive the model."""

    player_id: int | None
    entry: StatusEntry | None
    row: NbaIntelStatus | NbaIntelOverride | None
    #: ``override``, ``gameEntry``, ``playerEntry``, ``outOfForce`` or ``none``.
    rule: str
    in_force: bool
    #: ``returnDatePassed``, ``returnRoundPassed``, ``playedSince`` or ``tooOld`` when out of force.
    reason: str | None

    @property
    def present(self) -> bool:
        return self.entry is not None

    @property
    def status(self) -> str | None:
        return self.row.status if self.row is not None else None

    @property
    def model_status(self) -> str | None:
        """The status the *model* uses: ``None`` when there is no entry in force."""
        if self.row is None or not self.in_force:
            return None
        return self.row.status


@dataclass(frozen=True)
class Listed:
    """One line of an availability list: a resolved entry plus what the screen needs."""

    team_id: int
    player_id: int | None
    name: str
    resolved: Resolved
    is_stale: bool
    age: int


@dataclass(frozen=True)
class _ReportRow:
    snapshot_id: int
    slot: datetime
    state: str
    parse_status: str


class AvailabilityBook:
    """Rows and overrides, loaded on demand, ready to answer "what applies, as of when".

    One book per request (:func:`get_book`). It holds ORM rows and so must never be memoised
    across sessions; the model keeps only the plain numbers it derives.
    """

    def __init__(self, ctx: ReadContext) -> None:
        self.ctx = ctx
        self.session = ctx.session
        self.allowed = statuses_allowed(ctx.session)
        self.rows: dict[Any, NbaIntelStatus | NbaIntelOverride] = {}
        self._by_player: dict[int, list[StatusEntry]] = {}
        self._unmatched: dict[int, list[StatusEntry]] = {}
        self._team_loaded: set[int] = set()
        self._snapshot_of: dict[Any, int] = {}
        self._snapshots: dict[int, NbaIntelSnapshot] = {}
        self._reports: dict[tuple[int, str], list[_ReportRow]] = {}
        self._played: dict[int, list[datetime]] = {}
        self._played_loaded: set[int] = set()
        self._overrides_loaded = False
        self._latest: list[NbaIntelSnapshot] | None = None
        self._unreadable: list[tuple[datetime, datetime]] | None = None
        self._window: bool | None = None

    # ------------------------------------------------------------------ loading

    def _slot_of(self, snapshot: NbaIntelSnapshot) -> datetime:
        return snapshot.slot_at_utc or snapshot.fetched_at

    def _horizon(self) -> datetime:
        return naive_utc(self.ctx.now) - HISTORY

    def _load_overrides(self) -> None:
        if self._overrides_loaded:
            return
        self._overrides_loaded = True
        if not self.allowed:
            return
        for over in self.session.execute(
            select(NbaIntelOverride).where(NbaIntelOverride.created_at >= self._horizon())
        ).scalars():
            entry = intel_status.override_entry(over)
            self.rows[entry.entry_id] = over
            self._by_player.setdefault(over.player_id, []).append(entry)

    def load_team(self, team_id: int) -> None:
        """Read the team's status rows and report markers once."""
        self._load_overrides()
        if team_id in self._team_loaded:
            return
        self._team_loaded.add(team_id)
        if not self.allowed:
            return
        horizon = self._horizon()
        snapshot_ids: set[int] = set()
        for row in self.session.execute(
            select(NbaIntelStatus).where(
                NbaIntelStatus.team_id == team_id, NbaIntelStatus.source_published_at >= horizon
            )
        ).scalars():
            entry = intel_status.status_entry(row)
            self.rows[entry.entry_id] = row
            if row.snapshot_id is not None:
                self._snapshot_of[entry.entry_id] = row.snapshot_id
                snapshot_ids.add(row.snapshot_id)
            if row.player_id is not None:
                self._by_player.setdefault(row.player_id, []).append(entry)
            else:
                self._unmatched.setdefault(team_id, []).append(entry)
        for marker, snapshot in self.session.execute(
            select(NbaIntelTeamReport, NbaIntelSnapshot)
            .join(NbaIntelSnapshot, NbaIntelSnapshot.snapshot_id == NbaIntelTeamReport.snapshot_id)
            .where(
                NbaIntelTeamReport.team_id == team_id,
                NbaIntelSnapshot.source_kind == "leagueReport",
                NbaIntelSnapshot.fetched_at >= horizon,
            )
        ):
            self._snapshots[snapshot.snapshot_id] = snapshot
            self._reports.setdefault((team_id, marker.game_id), []).append(
                _ReportRow(
                    snapshot.snapshot_id,
                    self._slot_of(snapshot),
                    marker.state,
                    snapshot.parse_status,
                )
            )
        missing = snapshot_ids - set(self._snapshots)
        if missing:
            for snapshot in self.session.execute(
                select(NbaIntelSnapshot).where(NbaIntelSnapshot.snapshot_id.in_(sorted(missing)))
            ).scalars():
                self._snapshots[snapshot.snapshot_id] = snapshot

    def _snapshots_known_by(self, as_of: datetime) -> list[NbaIntelSnapshot]:
        """Usable league snapshots fetched by ``as_of``, newest first."""
        if self._latest is None:
            self._latest = list(
                self.session.execute(
                    select(NbaIntelSnapshot)
                    .where(
                        NbaIntelSnapshot.source_kind == "leagueReport",
                        NbaIntelSnapshot.parse_status.in_(("ok", "partial", "empty")),
                        NbaIntelSnapshot.fetched_at >= self._horizon(),
                    )
                    .order_by(
                        func.coalesce(
                            NbaIntelSnapshot.slot_at_utc, NbaIntelSnapshot.fetched_at
                        ).desc(),
                        NbaIntelSnapshot.snapshot_id.desc(),
                    )
                ).scalars()
            )
        cutoff = naive_utc(as_of)
        return [s for s in self._latest if s.fetched_at <= cutoff]

    # ------------------------------------------------------------------ facts from the store

    def _load_played(self, player_ids: Iterable[int]) -> None:
        wanted = sorted({p for p in player_ids if p not in self._played_loaded})
        if not wanted:
            return
        self._played_loaded.update(wanted)
        year = season_year(self.ctx.season)
        seasons = [self.ctx.season, f"{year - 1}-{year % 100:02d}"]
        for start in range(0, len(wanted), 400):
            chunk = wanted[start : start + 400]
            rows = self.session.execute(
                select(PlayerGameBasic.player_id, Game)
                .join(Game, Game.game_id == PlayerGameBasic.game_id)
                .where(
                    PlayerGameBasic.player_id.in_(chunk),
                    PlayerGameBasic.minutes > 0,
                    Game.status == "final",
                    Game.season.in_(seasons),
                )
            )
            for player_id, game in rows:
                tip = self.ctx.tipoff_of(game) if game.season == self.ctx.season else None
                self._played.setdefault(player_id, []).append(lock_deadline(game, tip))
        for values in self._played.values():
            values.sort()

    def last_played_at(self, player_id: int, before: datetime) -> datetime | None:
        """When his most recent played game *tipped* (noon Eastern when the tip is unknown),
        strictly before ``before``."""
        self._load_played([player_id])
        cutoff = aware(before)
        latest: datetime | None = None
        for moment in self._played.get(player_id, ()):
            if moment < cutoff:  # type: ignore[operator]
                latest = moment
        return latest

    def in_reporting_window(self) -> bool | None:
        """Is a reporting window open now? ``None`` when that cannot be said (the entry is then
        judged by the longer, outside-window limit: it can only under-report staleness)."""
        if self._window is None:
            try:
                from ..nba_intel import report_fetch

                games = [
                    report_fetch.GameInfo(g.game_id, g.game_date, self.ctx.tipoff_of(g))
                    for g in self.ctx.scheduled_games()
                ]
                decision = report_fetch.decide_poll(self.ctx.now, games, None, force=True)
                self._window = bool(decision.in_window)
            except Exception:  # noqa: BLE001 - the clock rule is advisory; never fail a read for it
                self._window = False
        return self._window

    # ------------------------------------------------------------------ reports and freshness

    def report_for(
        self, team_id: int, game_id: str | None, as_of: datetime
    ) -> tuple[str, _ReportRow | None]:
        """``(reportState, the newest marker)`` of one team for one game as of ``as_of``.

        ``submitted`` and ``notYetSubmitted`` are the league's own words; ``noReport`` means no
        usable snapshot says anything about this team and game.
        """
        if not self.allowed or game_id is None:
            return "noReport", None
        self.load_team(team_id)
        cutoff = naive_utc(as_of)
        usable = [
            r
            for r in self._reports.get((team_id, game_id), ())
            if r.parse_status in ("ok", "partial", "empty")
            and self._snapshots[r.snapshot_id].fetched_at <= cutoff
        ]
        if not usable:
            return "noReport", None
        newest = max(usable, key=lambda r: (r.slot, r.snapshot_id))
        return newest.state, newest

    def assumed_basis(self, team_id: int, game_id: str | None, as_of: datetime) -> str:
        state, _ = self.report_for(team_id, game_id, as_of)
        return assumed_available_basis("nba", state)

    def _authority(self, team_id: int, game_id: str, as_of: datetime) -> _ReportRow | None:
        """The newest fully parsed snapshot in which the team *submitted* for this game."""
        cutoff = naive_utc(as_of)
        usable = [
            r
            for r in self._reports.get((team_id, game_id), ())
            if r.parse_status == "ok"
            and r.state == "submitted"
            and self._snapshots[r.snapshot_id].fetched_at <= cutoff
        ]
        return max(usable, key=lambda r: (r.slot, r.snapshot_id)) if usable else None

    def newest_report_arrival(self, as_of: datetime) -> datetime | None:
        """When the newest usable league snapshot arrived (an older override is obsolete)."""
        known = self._snapshots_known_by(as_of)
        return known[0].fetched_at if known else None

    def latest_snapshot(self, as_of: datetime | None = None) -> NbaIntelSnapshot | None:
        known = self._snapshots_known_by(as_of or self.ctx.now)
        return known[0] if known else None

    def overall(self) -> tuple[str, str, datetime | None]:
        """``(state, message, asOf)`` of the injury report as a whole.

        Precedence: ``disabled`` (the demo league, or the switch off, or the parser missing),
        ``unreadable`` (the newest slot could not be read), ``noReportYet``, ``stale``, ``fresh``.
        """
        if not self.allowed:
            return "disabled", DEMO_REFUSAL, None
        source = intel_store.get_source_state(self.session, intel_store.SOURCE_INJURY_REPORT)
        detail = intel_store.source_detail(source)
        snapshot = self.latest_snapshot()
        as_of = (snapshot.report_as_of_utc or snapshot.slot_at_utc) if snapshot else None
        if not injuries_switch_on():
            return "disabled", "The injury report is switched off (HARDWOOD_NBA_INJURIES).", as_of
        if source is not None and source.state == "notConfigured":
            return (
                "disabled",
                str(detail.get("reason") or "The report reader is not installed."),
                as_of,
            )
        if source is not None and source.state == "unreadable":
            reason = source.last_error or "its layout was not recognised"
            return (
                "unreadable",
                f"The newest injury report could not be read ({reason}); nothing was guessed.",
                as_of,
            )
        if snapshot is None:
            return "noReportYet", "No injury report has been read yet.", None
        limits = PROFILE.stale_after
        window = self.in_reporting_window()
        limit = limits.in_window_minutes if window else limits.outside_window_minutes
        age = age_minutes(self._slot_of(snapshot), self.ctx.now)
        if limit is not None and age > limit:
            return (
                "stale",
                f"The newest injury report is {age} minutes old, past the {limit} minutes it "
                "can vouch for.",
                as_of,
            )
        return (
            "fresh",
            "Statuses are from the league's official injury report and are dated.",
            as_of,
        )

    # ------------------------------------------------------------------ resolution

    def _pool(
        self,
        entries: Sequence[StatusEntry],
        team_id: int | None,
        game_id: str | None,
        as_of: datetime,
    ) -> list[StatusEntry]:
        """The entries as they were known at ``as_of``, with superseded list rows removed."""
        cutoff = naive_utc(as_of)
        authority = (
            self._authority(team_id, game_id, as_of)
            if team_id is not None and game_id is not None
            else None
        )
        out: list[StatusEntry] = []
        for entry in entries:
            if entry.is_override:
                out.append(entry)
                continue
            row = self.rows[entry.entry_id]
            if row.recorded_at > cutoff:  # type: ignore[union-attr]
                continue  # not yet known at ``as_of``
            snapshot_id = self._snapshot_of.get(entry.entry_id)
            if (
                authority is not None
                and entry.game_id == game_id
                and entry.retracts is None
                and snapshot_id is not None
                and snapshot_id != authority.snapshot_id
                and self._slot_of(self._snapshots[snapshot_id]) < authority.slot
                and row.source_kind == "leagueReport"  # type: ignore[union-attr]
            ):
                continue  # an older list for this game, restated since
            out.append(entry)
        return out

    def _verdict(
        self, player_id: int | None, entry: StatusEntry, as_of: datetime, before: datetime
    ) -> ForceVerdict:
        last = self.last_played_at(player_id, before) if player_id is not None else None
        return entry_in_force(entry, as_of=as_of, tz=EASTERN, last_played_at=last)

    def resolve(
        self,
        player_id: int,
        team_id: int,
        game_id: str | None,
        as_of: datetime,
        before: datetime | None = None,
    ) -> Resolved:
        """The effective entry for ``player_id`` in ``game_id`` as known at ``as_of``.

        ``before`` is the instant his last played game must precede (the game's start); it
        defaults to ``as_of``.
        """
        self.load_team(team_id)
        cutoff = before or as_of
        entries = self._pool(self._by_player.get(player_id, ()), team_id, game_id, as_of)
        if not entries:
            return Resolved(player_id, None, None, "none", False, None)
        effective = select_effective_entry(
            entries,
            game_id=game_id,
            as_of=as_of,
            newest_league_report_at=self.newest_report_arrival(as_of),
            tz=EASTERN,
            last_played_at=self.last_played_at(player_id, cutoff),
        )
        if effective.entry is not None:
            return Resolved(
                player_id,
                effective.entry,
                self.rows[effective.entry.entry_id],
                effective.rule,
                effective.verdict.in_force,
                effective.verdict.reason,
            )
        # Nothing in force. The newest sourced player-level entry is still worth *showing*,
        # marked out of force, so a reader can see why a name no longer drives anything.
        retracted = {e.retracts for e in entries if e.retracts is not None}
        known = [
            e
            for e in entries
            if e.retracts is None
            and not e.is_override
            and e.game_id is None
            and e.entry_id not in retracted
            and aware(e.source_published_at) < aware(as_of)  # type: ignore[operator]
        ]
        if not known:
            return Resolved(player_id, None, None, "none", False, None)
        newest = max(known, key=lambda e: (aware(e.source_published_at), str(e.entry_id)))
        verdict = self._verdict(player_id, newest, as_of, cutoff)
        return Resolved(
            player_id,
            newest,
            self.rows[newest.entry_id],
            "outOfForce",
            verdict.in_force,
            verdict.reason,
        )

    def _unreadable_slots(self, as_of: datetime) -> list[datetime]:
        """Slots of league reports that were fetched by ``as_of`` and could not be read."""
        if self._unreadable is None:
            rows = self.session.execute(
                select(NbaIntelSnapshot).where(
                    NbaIntelSnapshot.source_kind == "leagueReport",
                    NbaIntelSnapshot.parse_status == "headerMismatch",
                    NbaIntelSnapshot.fetched_at >= self._horizon(),
                )
            ).scalars()
            self._unreadable = [(self._slot_of(row), row.fetched_at) for row in rows]
        cutoff = naive_utc(as_of)
        return [slot for slot, fetched in self._unreadable if fetched <= cutoff]

    def is_stale(self, resolved: Resolved, as_of: datetime) -> bool:
        entry = resolved.entry
        if entry is None:
            return False
        if entry_is_stale(
            entry,
            ForceVerdict(resolved.in_force, resolved.reason),
            profile=PROFILE,
            as_of=as_of,
            in_reporting_window=self.in_reporting_window(),
        ):
            return True
        row = resolved.row
        if (
            isinstance(row, NbaIntelStatus)
            and row.source_kind == "leagueReport"
            and row.snapshot_id in self._snapshots
        ):
            own = self._slot_of(self._snapshots[row.snapshot_id])
            return any(slot > own for slot in self._unreadable_slots(as_of))
        return False

    def chance(self, resolved: Resolved, table: dict[str, float]) -> tuple[float, bool]:
        """``(chance the model uses, assumed)``: a player with nothing in force is assumed to
        play with probability one, and ``assumed`` says so."""
        status = resolved.model_status
        if status is None:
            return 1.0, True
        return float(table[status]), False

    # ------------------------------------------------------------------ listing

    def players_with_entries(self, team_id: int) -> list[int]:
        """Players with any entry for the team in the book's horizon (their latest may be old)."""
        self.load_team(team_id)
        found: list[int] = []
        for player_id, entries in self._by_player.items():
            for entry in entries:
                row = self.rows[entry.entry_id]
                owner = getattr(row, "team_id", None)
                if owner == team_id and entry.retracts is None:
                    found.append(player_id)
                    break
        return sorted(found)

    def listing(
        self, team_id: int, game_id: str | None, as_of: datetime, before: datetime | None = None
    ) -> list[Listed]:
        """One :class:`Listed` per player with an entry that applies, plus unmatched names."""
        self.load_team(team_id)
        ctx = self.ctx
        ctx.load_players(self.players_with_entries(team_id))
        out: list[Listed] = []
        for player_id in self.players_with_entries(team_id):
            resolved = self.resolve(player_id, team_id, game_id, as_of, before)
            if not resolved.present:
                continue
            published = resolved.entry.source_published_at  # type: ignore[union-attr]
            out.append(
                Listed(
                    team_id=team_id,
                    player_id=player_id,
                    name=ctx.player_name(player_id),
                    resolved=resolved,
                    is_stale=self.is_stale(resolved, as_of),
                    age=age_minutes(published, as_of),
                )
            )
        # Names the report printed that matched nobody: shown as printed, never guessed.
        authority = self._authority(team_id, game_id, as_of) if game_id is not None else None
        pool = list(self._unmatched.get(team_id, ()))
        retracted = {e.retracts for e in pool if e.retracts is not None}
        cutoff = naive_utc(as_of)
        for entry in pool:
            row = self.rows[entry.entry_id]
            if entry.retracts is not None or entry.entry_id in retracted:
                continue
            if row.recorded_at > cutoff or entry.game_id != game_id:  # type: ignore[union-attr]
                continue
            if (
                authority is not None
                and self._snapshot_of.get(entry.entry_id) != authority.snapshot_id
            ):
                continue
            verdict = self._verdict(None, entry, as_of, before or as_of)
            resolved = Resolved(None, entry, row, "gameEntry", verdict.in_force, verdict.reason)
            out.append(
                Listed(
                    team_id=team_id,
                    player_id=None,
                    name=row.player_name_raw,  # type: ignore[union-attr]
                    resolved=resolved,
                    is_stale=self.is_stale(resolved, as_of),
                    age=age_minutes(entry.source_published_at, as_of),
                )
            )
        out.sort(key=lambda item: (item.name.lower(), item.player_id or 0))
        return out


def get_book(ctx: ReadContext) -> AvailabilityBook:
    """The context's availability book, built once per request."""
    book = ctx.cache.get("availability_book")
    if book is None:
        book = ctx.cache["availability_book"] = AvailabilityBook(ctx)
    return book


# --------------------------------------------------------------------------- payloads


def source_of(row: NbaIntelStatus | NbaIntelOverride) -> dict[str, Any]:
    """The ``Source`` of an entry: who said it, when, and where to read it."""
    if isinstance(row, NbaIntelOverride):
        return refs.source_ref(
            "manual",
            "Entered by hand",
            published_at=row.source_published_at or row.created_at,
            url=row.source_url,
            as_of=row.created_at,
            fetched_at=row.created_at,
        )
    return refs.source_ref(
        row.source_kind,
        row.source_label,
        published_at=row.source_published_at,
        url=row.source_url,
        as_of=row.as_of,
        fetched_at=row.recorded_at,
        snapshot_id=row.snapshot_id,
    )


def entry_payload(ctx: ReadContext, listed: Listed) -> dict[str, Any]:
    """One entry of ``AvailabilityReport.teams[].entries`` (design section 9.4)."""
    resolved, row = listed.resolved, listed.resolved.row
    assert row is not None
    is_override = isinstance(row, NbaIntelOverride)
    table = ctx.chance_table()
    expected: dict[str, Any] | None = None
    if isinstance(row, NbaIntelStatus) and any(
        v is not None
        for v in (
            row.expected_return_round_from,
            row.expected_return_round_to,
            row.expected_return_date,
        )
    ):
        expected = {
            "roundFrom": row.expected_return_round_from,
            "roundTo": row.expected_return_round_to,
            "date": refs.iso_date(row.expected_return_date),
        }
    game = ctx.games_by_id.get(row.game_id) if row.game_id is not None else None
    status = row.status
    return {
        "statusId": None if is_override else row.status_id,
        "overrideId": row.override_id if is_override else None,
        "player": ctx.player_ref(listed.player_id, listed.team_id) if listed.player_id else None,
        "playerName": listed.name,
        "status": status,
        "statusLabel": status_label(status),
        "chanceOfPlaying": display_chance(status, table),
        "modelStatus": None if is_override else row.model_status,
        "reasonCategory": None if is_override else row.reason_category,
        "reasonText": row.note if is_override else row.reason_text,
        "expectedReturnText": None if is_override else row.expected_return_text,
        "expectedReturn": expected,
        "game": ctx.game_ref(game) if game is not None else None,
        "isOverride": is_override,
        "inForce": resolved.in_force,
        "outOfForceReason": resolved.reason,
        "isStale": listed.is_stale,
        "ageMinutes": listed.age,
        "source": source_of(row),
    }


def summary_for_team(
    ctx: ReadContext,
    team_id: int,
    as_of: datetime,
    game_id: str | None = None,
    before: datetime | None = None,
    key_absences: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """``AvailabilitySummary``: counts by status for the entries in force, the key absences the
    caller supplies (it holds the projection that ranks them) and how fresh the source is.

    The counts are counts of recorded entries, so a team with none has zeros, not nulls, and
    ``freshnessState`` says ``noReportYet`` (or ``disabled``) so a reader can tell "nobody is
    listed" from "nothing has been recorded".
    """
    book = get_book(ctx)
    listed = book.listing(team_id, game_id, as_of, before)
    counts = {"out": 0, "doubtful": 0, "questionable": 0, "probable": 0}
    for item in listed:
        status = item.resolved.model_status
        if status in counts:
            counts[status] += 1
    state, _, report_as_of = book.overall()
    return {
        **counts,
        "keyAbsences": list(key_absences),
        "freshnessState": state,
        "asOf": refs.rfc3339(report_as_of),
    }


def _games_in_scope(ctx: ReadContext, team_id: int | None, day: date | None) -> list[Game]:
    if day is not None:
        games = [g for g in ctx.games if g.game_date == day and g.status != "postponed"]
    elif team_id is not None:
        game = ctx.next_game_of(team_id)
        games = [game] if game is not None else []
    else:
        return []
    if team_id is not None:
        games = [g for g in games if team_id in (g.home_team_id, g.away_team_id)]
    return sorted(games, key=lambda g: (ctx.start_of(g), g.game_id))


def build_availability_report(
    ctx: ReadContext,
    *,
    team_id: int | None = None,
    day: date | None = None,
    statuses: Sequence[str] | None = None,
    news: list[dict[str, Any]] | None = None,
    include_news: bool = False,
    freshness: dict[str, Any],
) -> dict[str, Any]:
    """``AvailabilityReport``: each team's entries for the games in scope, with their provenance.

    The scope is the games on ``day`` (the next slate when ``day`` and ``team_id`` are both
    omitted, which the caller resolves), or the team's next game. ``statuses`` keeps only entries
    with one of those statuses.
    """
    book = get_book(ctx)
    state, message, as_of = book.overall()
    games = _games_in_scope(ctx, team_id, day)
    wanted = set(statuses) if statuses else None
    teams: list[dict[str, Any]] = []
    scope: list[tuple[int, Game | None]] = []
    for game in games:
        for side_team in (game.home_team_id, game.away_team_id):
            if team_id is None or side_team == team_id:
                scope.append((side_team, game))
    if not scope and team_id is not None:
        scope.append((team_id, None))
    for side_team, game in scope:
        game_id = game.game_id if game is not None else None
        start = ctx.start_of(game) if game is not None else ctx.now
        items = book.listing(side_team, game_id, ctx.now, start) if book.allowed else []
        if wanted is not None:
            items = [i for i in items if i.resolved.status in wanted]
        report_state, _ = book.report_for(side_team, game_id, ctx.now)
        teams.append(
            {
                "team": ctx.team_ref(side_team),
                "reportState": report_state,
                "game": ctx.game_ref(game) if game is not None else None,
                "entries": [entry_payload(ctx, item) for item in items],
            }
        )
    return {
        "league": "nba",
        "asOf": refs.rfc3339(as_of),
        "freshness": freshness,
        "state": state,
        "message": message,
        "teams": teams,
        "news": news if include_news else None,
        "attribution": PROFILE.attribution,
    }


def review_queue(ctx: ReadContext) -> dict[str, Any]:
    """Rows of the newest report whose player name matched nobody uniquely: ``{items: [...]}``.

    A name is never guessed onto a player; it waits here. (Linking one is a person's decision and
    has no route in v1; the intel package keeps the append-only way to do it.)
    """
    items: list[dict[str, Any]] = []
    if statuses_allowed(ctx.session):
        for item in intel_status.review_queue(ctx.session):
            items.append(
                {
                    "statusId": item.status_id,
                    "playerName": item.player_name,
                    "team": ctx.team_ref(item.team_id),
                    "source": refs.source_ref(
                        "leagueReport",
                        item.source_label,
                        published_at=item.source_published_at,
                        url=item.source_url,
                    ),
                }
            )
    return {"league": "nba", "items": items}


# --------------------------------------------------------------------------- the two writes


def _published(value: datetime | date | str | None, now: datetime) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            value = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise bad_request(
                f"{value!r} is not an ISO date or time such as '2026-01-02'.", "sourcePublishedAt"
            ) from exc
    if isinstance(value, datetime):
        moment = aware(value)
    else:
        moment = aware(datetime(value.year, value.month, value.day))
    if moment > aware(now) + _MAX_FUTURE:  # type: ignore[operator]
        raise bad_request("sourcePublishedAt is in the future.", "sourcePublishedAt")
    return naive_utc(moment)  # type: ignore[arg-type]


def latest_team_of(ctx: ReadContext, player_id: int) -> int | None:
    """The team a player most recently played for this season, else the one his listing names.

    An override with no team is placed with this team so that it shows in that team's report.
    ``None`` when the store knows nothing about him this season.
    """
    row = ctx.session.execute(
        select(PlayerGameBasic.team_id)
        .join(Game, Game.game_id == PlayerGameBasic.game_id)
        .where(PlayerGameBasic.player_id == player_id, Game.season == ctx.season)
        .order_by(Game.game_date.desc(), Game.game_id.desc())
        .limit(1)
    ).first()
    if row is not None:
        return int(row[0])
    listing = ctx.positions.get(player_id)
    return int(listing.team_id) if listing is not None and listing.team_id is not None else None


def record_override(
    ctx: ReadContext,
    *,
    player_id: int,
    status: str,
    team_id: int | None = None,
    game_id: str | None = None,
    note: str | None = None,
    source_url: str | None = None,
    source_published_at: datetime | date | str | None = None,
    entered_by_user_id: str | None = None,
) -> dict[str, Any]:
    """Record a status a person typed (``POST /v1/availability``). Does not commit.

    Refused in the demo league. The status must be one of the five; a link must be http(s); one
    to a gambling operator is dropped and the notice kept (the intel layer does that).
    """
    if not statuses_allowed(ctx.session):
        raise bad_request(DEMO_REFUSAL)
    try:
        normalised = normalise_status(status)
    except InvalidStatusError as exc:
        raise invalid_status(str(exc)) from exc
    if normalised is None:
        raise invalid_status(
            "A status is required: one of out, doubtful, questionable, probable, available."
        )
    if team_id is not None and team_id not in ctx.teams:
        raise team_not_found(team_id)
    if game_id is not None:
        game = ctx.session.get(Game, game_id)
        if game is None:
            raise game_not_found(game_id)
        if team_id is not None and team_id not in (game.home_team_id, game.away_team_id):
            raise bad_request(f"{game_id!r} is not a game of team {team_id}.", "gameId")
    published = _published(source_published_at, ctx.now)
    if team_id is None:
        team_id = latest_team_of(ctx, player_id)
    try:
        row = intel_status.add_override(
            ctx.session,
            player_id=player_id,
            status=normalised,
            user_id=entered_by_user_id,
            team_id=team_id,
            game_id=game_id,
            note=note,
            source_url=source_url,
            source_published_at=published,
            now=naive_utc(ctx.now),
        )
    except intel_status.UnknownPlayerError as exc:
        raise ApiError("player_not_found", f"No player with id {player_id}.") from exc
    except intel_status.InvalidOverrideError as exc:
        raise bad_request(str(exc), "sourceUrl") from exc
    return {
        "league": "nba",
        "overrideId": row.override_id,
        "player": ctx.player_ref(player_id, team_id),
        "team": ctx.team_ref(team_id) if team_id is not None else None,
        "status": normalised,
        "gameId": game_id,
        "linkWithheld": bool(source_url) and row.source_url is None,
    }


def clear_override(ctx: ReadContext, override_id: int) -> dict[str, Any]:
    """Clear an override (``DELETE /v1/availability/{overrideId}``). Does not commit.

    Idempotent: clearing one that is already cleared says so. The row stays, with its clear time.
    """
    existing = ctx.session.get(NbaIntelOverride, override_id)
    if existing is None:
        raise ApiError("not_found", f"No override with id {override_id}.", http_status=404)
    already = existing.cleared_at is not None
    row = intel_status.clear_override(ctx.session, override_id, now=naive_utc(ctx.now))
    assert row is not None
    return {
        "league": "nba",
        "overrideId": override_id,
        "clearedAt": refs.rfc3339(row.cleared_at),
        "alreadyCleared": already,
    }
