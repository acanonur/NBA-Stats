"""Availability in force: which status applies to a player in a game, and what the screen may say.

The EuroLeague publishes no injury list. What Hardwood has is sourced, dated entries: the rows of
the user's workbook, and lines a person typed in after reading a club statement. Three different
questions are asked of that pile (the shared core, :mod:`nbastats.shared.availability`, answers
each separately, and this module is where the EuroLeague's store is fed into it):

1. **What do we show?** Exactly what the source said, with its age and its link. A player with
   no entry has ``status: null``, an em dash on screen, never "available".
2. **What does the model assume?** A player with no *usable* entry plays with probability one.
   That is a convenience, not a claim, so every projection lists those players under
   ``assumptions.assumedAvailable`` with the basis ``noEntry``.
3. **Does an old entry still count?** An entry stops driving a projection when its expected
   return has passed, when a box score after it shows the player on court, or when it is more
   than fourteen days old (long-term entries excepted). It still appears in the list, marked
   ``inForce: false`` and ``isStale: true``.

What this module adds to the shared rules
-----------------------------------------
:class:`AvailabilityBook` loads every status and override once, builds the shared
:class:`~nbastats.shared.availability.StatusEntry` objects, and answers
``resolve(person, game, as_of)``. It supplies the three facts the shared rules need from the
store: the calendar (:meth:`ReadContext.round_end_at`, the moment a round's last game has
certainly been played), each player's last played game before ``as_of`` (rule b), and whether a
club has played since an entry's source date (the EuroLeague's staleness test).

Two decisions are worth stating, because both are visible in what a reader sees:

* **The workbook's "In model" column drives the model, "Status (research)" is what is shown.**
  The workbook says its model column "can be stricter than the research" (a player not yet on the
  registered roster is OUT for the round). Both travel in the payload (``status`` and
  ``modelStatus``); the model uses ``model_status`` when there is one, otherwise ``status``.
  A hand-entered entry has no model column, so its status is both.
* **Information is cut off at ``as_of``.** A retraction recorded after ``as_of`` does not hide
  an entry as it was known then, and an entry published at or after ``as_of`` does not exist yet.
  That is what lets a projection be rebuilt "as of tip-off" from exactly what was knowable.

The two writes
--------------
``POST /v1/el/availability`` and ``DELETE /v1/el/availability/{statusId}`` end here
(:func:`record_status`, :func:`retract_status`). The status table is append-only: a correction
is a new row and a retraction is a row that names the one it retracts, so the history of what
Hardwood showed and when is never rewritten. A status is validated at entry (an unknown word is
``400 invalid_status``; the workbook's ``IFERROR(...,1)``, which read a typo as "certainly
plays", is not ported), a name that matches nobody on the squad is stored *unmatched* and goes to
the review queue rather than being guessed onto a player, and a link whose host is a gambling
operator is stored with the label and the date and no link.

Nothing here reads article text, and nothing here fetches anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final, Iterable, Sequence
from urllib.parse import urlsplit

from sqlalchemy import select

from ...shared import refs
from ...shared.availability import (
    ForceVerdict,
    InvalidReasonCategoryError,
    InvalidStatusError,
    StatusEntry,
    age_minutes,
    display_chance,
    entry_in_force,
    entry_is_stale,
    normalise_reason_category,
    normalise_status,
    parse_expected_return,
    select_effective_entry,
    status_label,
)
from ..db import bump_sync_version
from ..models import ElIntelOverride, ElIntelStatus, ElPersonAlias, ElPlayerGame
from ..profile import PROFILE, fold_name, screen_source_link
from .queries import (
    BERLIN,
    ReadContext,
    aware,
    bad_request,
    game_has_result,
    game_start,
    invalid_status,
    naive_utc,
)

__all__ = [
    "EXCUSING_STATUSES",
    "NOT_EXCUSING_REASONS",
    "Resolved",
    "Listed",
    "AvailabilityBook",
    "get_book",
    "model_status_of",
    "source_of",
    "entry_payload",
    "summary_for_club",
    "build_availability_report",
    "review_queue",
    "build_review_queue",
    "record_status",
    "retract_status",
]

#: A player who does not play while listed at one of these did not have his minutes judged by
#: the coach, so the round is no evidence about his role (the player-rate update leaves it out).
EXCUSING_STATUSES: Final[tuple[str, ...]] = ("out", "doubtful", "questionable")
#: ...unless the reason is a choice about minutes: then the round *is* evidence about his role.
NOT_EXCUSING_REASONS: Final[tuple[str, ...]] = ("coachDecision", "rest")

_ONE_SECOND: Final = timedelta(seconds=1)
_MAX_FUTURE: Final = timedelta(minutes=5)


# --------------------------------------------------------------------------- the book


@dataclass(frozen=True)
class Resolved:
    """The entry that applies to one player in one game, and whether it may drive the model."""

    person: str | None
    entry: StatusEntry | None
    row: ElIntelStatus | ElIntelOverride | None
    #: ``override``, ``gameEntry``, ``playerEntry``, ``outOfForce`` or ``none``.
    rule: str
    in_force: bool
    #: ``returnDatePassed``, ``returnRoundPassed``, ``playedSince`` or ``tooOld`` when out of force.
    reason: str | None

    @property
    def present(self) -> bool:
        return self.entry is not None

    @property
    def is_override(self) -> bool:
        return self.entry is not None and self.entry.is_override

    @property
    def status(self) -> str | None:
        return self.row.status if self.row is not None else None

    @property
    def model_status(self) -> str | None:
        """The status the *model* uses: ``None`` when there is no entry in force."""
        return model_status_of(self)


def model_status_of(resolved: Resolved) -> str | None:
    if resolved.row is None or not resolved.in_force:
        return None
    row = resolved.row
    if isinstance(row, ElIntelStatus):
        return row.model_status or row.status
    return row.status


@dataclass(frozen=True)
class Listed:
    """One line of an availability list: a resolved entry plus what the screen needs."""

    club: str
    person: str | None
    name: str
    resolved: Resolved
    is_stale: bool
    age: int


def _status_entry(row: ElIntelStatus) -> StatusEntry:
    return StatusEntry(
        entry_id=row.status_id,
        status=row.status,
        source_kind=row.source_kind,
        source_published_at=row.source_published_at,
        player_key=row.person_code,
        game_id=row.game_id,
        is_override=False,
        recorded_at=row.recorded_at,
        retracts=row.retracts_status_id,
        expected_return_text=row.expected_return_text,
        expected_return_round_from=row.expected_return_round_from,
        expected_return_round_to=row.expected_return_round_to,
        expected_return_date=row.expected_return_date,
    )


def _override_entry(row: ElIntelOverride) -> StatusEntry:
    return StatusEntry(
        entry_id=f"o{row.override_id}",
        status=row.status,
        source_kind="manual",
        source_published_at=row.source_published_at or row.created_at,
        player_key=row.person_code,
        game_id=row.game_id,
        is_override=True,
        recorded_at=row.created_at,
        cleared_at=row.cleared_at,
    )


class AvailabilityBook:
    """Every status and override in the store, ready to answer "what applies, as of when"."""

    def __init__(self, ctx: ReadContext) -> None:
        self.ctx = ctx
        session = ctx.session
        self.rows: dict[Any, ElIntelStatus | ElIntelOverride] = {}
        self._by_person: dict[str, list[StatusEntry]] = {}
        self._unmatched: dict[tuple[str, str], list[StatusEntry]] = {}
        self._retractions: dict[int, ElIntelStatus] = {}
        for row in session.execute(select(ElIntelStatus)).scalars():
            entry = _status_entry(row)
            self.rows[entry.entry_id] = row
            if row.retracts_status_id is not None:
                self._retractions.setdefault(row.retracts_status_id, row)
            if row.person_code is not None:
                self._by_person.setdefault(row.person_code, []).append(entry)
            else:
                key = (row.club_code, fold_name(row.player_name_raw))
                self._unmatched.setdefault(key, []).append(entry)
        for over in session.execute(select(ElIntelOverride)).scalars():
            entry = _override_entry(over)
            self.rows[entry.entry_id] = over
            self._by_person.setdefault(over.person_code, []).append(entry)
        self._played: dict[str, list[datetime]] | None = None

    # ------------------------------------------------------------------ facts from the store

    def _played_starts(self) -> dict[str, list[datetime]]:
        """Each player's played games' start instants (those with a stored result)."""
        if self._played is None:
            starts = {g.game_id: game_start(g) for g in self.ctx.games if game_has_result(g)}
            played: dict[str, list[datetime]] = {}
            if starts:
                rows = self.ctx.session.execute(
                    select(ElPlayerGame.person_code, ElPlayerGame.game_id).where(
                        ElPlayerGame.participation == "played",
                        ElPlayerGame.game_id.in_(list(starts)),
                    )
                )
                for person, game_id in rows:
                    played.setdefault(person, []).append(starts[game_id])
            for values in played.values():
                values.sort()
            self._played = played
        return self._played

    def last_played_at(self, person: str, before: datetime) -> datetime | None:
        """The start of his most recent played game strictly before ``before``."""
        cutoff = aware(before)
        latest: datetime | None = None
        for start in self._played_starts().get(person, ()):
            if start < cutoff:  # type: ignore[operator]
                latest = start
        return latest

    def team_played_since(self, club: str, published: datetime, as_of: datetime) -> bool:
        """Has ``club`` played a game (with a result) after ``published`` and before ``as_of``?"""
        since, until = aware(published), aware(as_of)
        for game in self.ctx.club_games(club):
            if game_has_result(game) and since < game_start(game) < until:  # type: ignore[operator]
                return True
        return False

    # ------------------------------------------------------------------ resolution

    def _pool(self, entries: Sequence[StatusEntry], as_of: datetime) -> list[StatusEntry]:
        """The entries as they were known at ``as_of``: a retraction made later is not yet true."""
        cutoff = aware(as_of)
        # A retraction made at or before ``as_of`` has happened; an entry must be strictly before.
        return [
            e
            for e in entries
            if e.retracts is None or aware(e.source_published_at) <= cutoff  # type: ignore[operator]
        ]

    def _verdict(self, person: str | None, entry: StatusEntry, as_of: datetime) -> ForceVerdict:
        last = self.last_played_at(person, as_of) if person is not None else None
        return entry_in_force(
            entry,
            as_of=as_of,
            tz=BERLIN,
            round_end_at=self.ctx.round_end_at,
            last_played_at=last,
        )

    def _supersede_game_entries(
        self, person: str, game_id: str | None, as_of: datetime, entries: list[StatusEntry]
    ) -> list[StatusEntry]:
        """Drop this game's own entries that a *newer* in-force general entry has overtaken.

        The shared rule prefers an entry written for this exact game over any general one, and
        deliberately does not fall back when that entry is out of force (so a vague old note does
        not replace a specific one). It does not say what happens when the general entry is the
        *newer* of the two, and the answer matters: a person who types "out" for a player (no game
        named, which is the usual way) must not be silently overruled by a game-specific note
        written a week earlier. So a game-specific entry yields to a general sourced entry that is
        newer than it and still in force. A tie keeps the game-specific entry: it is the more
        specific statement. Overrides are untouched (they outrank everything already).
        """
        if game_id is None:
            return entries
        general = [
            e
            for e in entries
            if e.game_id is None
            and e.retracts is None
            and not e.is_override
            and aware(e.source_published_at) < aware(as_of)  # type: ignore[operator]
            and self._verdict(person, e, as_of).in_force
        ]
        if not general:
            return entries
        newest = max(aware(e.source_published_at) for e in general)  # type: ignore[type-var]
        return [
            e
            for e in entries
            if not (
                e.game_id == game_id
                and not e.is_override
                and e.retracts is None
                and aware(e.source_published_at) < newest  # type: ignore[operator]
            )
        ]

    def resolve(self, person: str, game_id: str | None, as_of: datetime) -> Resolved:
        """The effective entry for ``person`` in ``game_id`` as known at ``as_of``."""
        entries = self._supersede_game_entries(
            person, game_id, as_of, self._pool(self._by_person.get(person, ()), as_of)
        )
        if not entries:
            return Resolved(person, None, None, "none", False, None)
        effective = select_effective_entry(
            entries,
            game_id=game_id,
            as_of=as_of,
            tz=BERLIN,
            round_end_at=self.ctx.round_end_at,
            last_played_at=self.last_played_at(person, as_of),
        )
        if effective.entry is not None:
            return Resolved(
                person,
                effective.entry,
                self.rows[effective.entry.entry_id],
                effective.rule,
                effective.verdict.in_force,
                effective.verdict.reason,
            )
        # Nothing in force. The newest sourced player-level entry is still worth *showing*,
        # marked out of force, so a reader can see why a name is no longer driving anything.
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
            return Resolved(person, None, None, "none", False, None)
        newest = max(known, key=lambda e: (aware(e.source_published_at), str(e.entry_id)))
        verdict = self._verdict(person, newest, as_of)
        return Resolved(
            person,
            newest,
            self.rows[newest.entry_id],
            "outOfForce",
            verdict.in_force,
            verdict.reason,
        )

    def is_stale(self, resolved: Resolved, club: str, as_of: datetime) -> bool:
        entry = resolved.entry
        if entry is None:
            return False
        verdict = ForceVerdict(resolved.in_force, resolved.reason)
        return entry_is_stale(
            entry,
            verdict,
            profile=PROFILE,
            as_of=as_of,
            team_played_since_source=self.team_played_since(club, entry.source_published_at, as_of),
        )

    def chance(self, resolved: Resolved, table: dict[str, float]) -> tuple[float, bool]:
        """``(chance the model uses, assumed)``: a player with nothing in force is assumed to
        play with probability one, and ``assumed`` says so."""
        status = resolved.model_status
        if status is None:
            return 1.0, True
        return float(table[status]), False

    def excused(self, person: str, game_id: str, as_of: datetime) -> bool:
        """True when a missed game gives no evidence about his role (see the module constants)."""
        resolved = self.resolve(person, game_id, as_of)
        if resolved.row is None or not resolved.in_force:
            return False
        if resolved.model_status not in EXCUSING_STATUSES:
            return False
        reason = getattr(resolved.row, "reason_category", None)
        return reason not in NOT_EXCUSING_REASONS

    # ------------------------------------------------------------------ listing

    def persons_with_entries(self) -> list[str]:
        return sorted(self._by_person)

    def listing(
        self,
        *,
        club: str | None,
        game_id_for: dict[str, str | None] | None,
        as_of: datetime,
    ) -> list[Listed]:
        """One :class:`Listed` per player (and per unmatched name) with an entry.

        ``game_id_for`` maps a club to the game the list is about (or ``None`` for "in general");
        a club absent from it is listed in general. ``club`` restricts the list to one club.
        """
        ctx = self.ctx
        out: list[Listed] = []
        for person in self.persons_with_entries():
            entries = self._by_person[person]
            statuses = [e for e in entries if e.retracts is None]
            if not statuses:
                continue
            newest = max(statuses, key=lambda e: (aware(e.source_published_at), str(e.entry_id)))
            row = self.rows[newest.entry_id]
            owner = getattr(row, "club_code", None)
            if owner is None:
                reg = ctx.registration_of(person)
                owner = reg.club_code if reg is not None else None
            if owner is None or (club is not None and owner != club):
                continue
            game_id = (game_id_for or {}).get(owner)
            resolved = self.resolve(person, game_id, as_of)
            if not resolved.present:
                continue
            out.append(
                Listed(
                    club=owner,
                    person=person,
                    name=ctx.player_name(person),
                    resolved=resolved,
                    is_stale=self.is_stale(resolved, owner, as_of),
                    age=age_minutes(resolved.entry.source_published_at, as_of),  # type: ignore[union-attr]
                )
            )
        for (owner, _), entries in sorted(self._unmatched.items()):
            if club is not None and owner != club:
                continue
            retracted = {e.retracts for e in entries if e.retracts is not None}
            live = [
                e
                for e in entries
                if e.retracts is None
                and e.entry_id not in retracted
                and aware(e.source_published_at) < aware(as_of)  # type: ignore[operator]
            ]
            if not live:
                continue
            newest = max(live, key=lambda e: (aware(e.source_published_at), str(e.entry_id)))
            verdict = self._verdict(None, newest, as_of)
            resolved = Resolved(
                None,
                newest,
                self.rows[newest.entry_id],
                "playerEntry",
                verdict.in_force,
                verdict.reason,
            )
            row = self.rows[newest.entry_id]
            out.append(
                Listed(
                    club=owner,
                    person=None,
                    name=row.player_name_raw,  # type: ignore[union-attr]
                    resolved=resolved,
                    is_stale=self.is_stale(resolved, owner, as_of),
                    age=age_minutes(newest.source_published_at, as_of),
                )
            )
        out.sort(key=lambda item: (item.club, item.name.lower(), item.person or ""))
        return out


def get_book(ctx: ReadContext) -> AvailabilityBook:
    """The context's availability book, built once per request."""
    book = ctx.cache.get("availability_book")
    if book is None:
        book = ctx.cache["availability_book"] = AvailabilityBook(ctx)
    return book


# --------------------------------------------------------------------------- payloads


def source_of(row: ElIntelStatus | ElIntelOverride) -> dict[str, Any]:
    """The ``Source`` of an entry: who said it, when, and where to read it."""
    if isinstance(row, ElIntelOverride):
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
    )


def entry_payload(ctx: ReadContext, listed: Listed) -> dict[str, Any]:
    """One entry of ``AvailabilityReport.teams[].entries`` (design section 9.4)."""
    resolved, row = listed.resolved, listed.resolved.row
    assert row is not None
    is_override = isinstance(row, ElIntelOverride)
    status = row.status
    table = ctx.chance_table()
    expected: dict[str, Any] | None = None
    game = None
    if isinstance(row, ElIntelStatus):
        if any(
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
        if row.game_id is not None and row.game_id in ctx.games_by_id:
            game = ctx.game_ref(ctx.games_by_id[row.game_id])
    elif row.game_id is not None and row.game_id in ctx.games_by_id:
        game = ctx.game_ref(ctx.games_by_id[row.game_id])
    return {
        "statusId": row.status_id if isinstance(row, ElIntelStatus) else None,
        "overrideId": row.override_id if is_override else None,
        "player": ctx.player_ref(listed.person, listed.club) if listed.person else None,
        "playerName": listed.name,
        "status": status,
        "statusLabel": status_label(status),
        "chanceOfPlaying": display_chance(status, table),
        "modelStatus": row.model_status if isinstance(row, ElIntelStatus) else None,
        "reasonCategory": row.reason_category if isinstance(row, ElIntelStatus) else None,
        "reasonText": (row.reason_text if isinstance(row, ElIntelStatus) else row.note),
        "expectedReturnText": row.expected_return_text if isinstance(row, ElIntelStatus) else None,
        "expectedReturn": expected,
        "game": game,
        "isOverride": is_override,
        "inForce": resolved.in_force,
        "outOfForceReason": resolved.reason,
        "isStale": listed.is_stale,
        "ageMinutes": listed.age,
        "source": source_of(row),
    }


def summary_for_club(
    ctx: ReadContext,
    club: str,
    as_of: datetime,
    game_id: str | None = None,
    key_absences: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """``AvailabilitySummary``: counts by status for the club's entries in force, its key
    absences (supplied by the caller, who has the projection that ranks them) and whether what
    is known is fresh.

    The counts are counts of recorded entries, so a club with none has zeros, not nulls; the
    ``freshnessState`` says ``noReportYet`` so a reader can tell "nobody is listed" from
    "nothing has been recorded".
    """
    book = get_book(ctx)
    listed = book.listing(club=club, game_id_for={club: game_id}, as_of=as_of)
    counts = {"out": 0, "doubtful": 0, "questionable": 0, "probable": 0}
    for item in listed:
        status = item.resolved.model_status
        if status in counts:
            counts[status] += 1
    newest = max(
        (i.resolved.row.as_of for i in listed if isinstance(i.resolved.row, ElIntelStatus)),
        default=None,
    )
    return {
        **counts,
        "keyAbsences": list(key_absences),
        "freshnessState": _report_state(listed),
        "asOf": refs.rfc3339(newest) if newest is not None else None,
    }


def _report_state(items: Iterable[Listed]) -> str:
    listed = list(items)
    if not listed:
        return "noReportYet"
    if all(item.is_stale for item in listed):
        return "stale"
    return "fresh"


def build_availability_report(
    ctx: ReadContext,
    *,
    club_code: str | None = None,
    round_number: int | None = None,
    statuses: Sequence[str] | None = None,
    include_news: bool = False,
    news: list[dict[str, Any]] | None = None,
    freshness: dict[str, Any],
) -> dict[str, Any]:
    """``AvailabilityReport``: every club's entries as of now, with their provenance.

    ``round_number`` scopes the report to the clubs playing that round and lets a game-specific
    entry (one written for that exact game) show for its game; without it a club's next
    scheduled game is the one asked about. ``statuses`` keeps only entries with one of those
    statuses.
    """
    book = get_book(ctx)
    clubs: list[str]
    game_for: dict[str, str | None] = {}
    if round_number is not None:
        games = ctx.games_of_round(round_number)
        if not games:
            raise bad_request(f"There is no round {round_number} in this season.", "round")
        for game in games:
            game_for[game.home_club_code] = game.game_id
            game_for[game.away_club_code] = game.game_id
        clubs = sorted(game_for)
    else:
        clubs = sorted(ctx.clubs)
        for code in clubs:
            nxt = ctx.next_game_of(code)
            game_for[code] = nxt.game_id if nxt is not None else None
    if club_code is not None:
        clubs = [ctx.require_club(club_code)]
    wanted = set(statuses) if statuses else None
    listing = book.listing(club=None, game_id_for=game_for, as_of=ctx.now)
    by_club: dict[str, list[Listed]] = {}
    for item in listing:
        by_club.setdefault(item.club, []).append(item)
    teams: list[dict[str, Any]] = []
    shown: list[Listed] = []
    for code in clubs:
        items = by_club.get(code, [])
        if wanted is not None:
            items = [i for i in items if i.resolved.status in wanted]
        shown.extend(items)
        teams.append(
            {
                "team": ctx.team_ref(code),
                "reportState": "submitted" if items else "noReport",
                "entries": [entry_payload(ctx, item) for item in items],
            }
        )
    state = _report_state(shown)
    newest = max(
        (i.resolved.row.as_of for i in shown if isinstance(i.resolved.row, ElIntelStatus)),
        default=None,
    )
    messages = {
        "noReportYet": "No availability has been recorded for this scope yet.",
        "stale": "Every status shown is older than its source can vouch for.",
        "fresh": (
            "Statuses are researched from the linked sources and dated; none of them is a live feed."
        ),
    }
    return {
        "league": "euroleague",
        "asOf": refs.rfc3339(newest) if newest is not None else None,
        "freshness": freshness,
        "state": state,
        "message": messages[state],
        "teams": teams,
        "news": news if include_news else None,
        "attribution": PROFILE.attribution,
    }


def review_queue(ctx: ReadContext) -> dict[str, Any]:
    """Statuses whose player name matched nobody on the squad: ``{items: [...]}``.

    A name is never guessed onto a player; it waits here until someone enters the status again
    with the person's code.
    """
    book = get_book(ctx)
    items: list[dict[str, Any]] = []
    for (club, _), entries in sorted(book._unmatched.items()):
        retracted = {e.retracts for e in entries if e.retracts is not None}
        for entry in sorted(entries, key=lambda e: e.entry_id):
            if entry.retracts is not None or entry.entry_id in retracted:
                continue
            row = book.rows[entry.entry_id]
            assert isinstance(row, ElIntelStatus)
            items.append(
                {
                    "statusId": row.status_id,
                    "playerName": row.player_name_raw,
                    "team": ctx.team_ref(club),
                    "source": source_of(row),
                }
            )
    return {"league": "euroleague", "items": items}


def build_review_queue(ctx: ReadContext) -> dict[str, Any]:
    """``GET /v1/el/review-queue``: everything that waits for a person to match it.

    Two kinds of item. A *status* whose player name matched nobody on the squad. A *person* the
    workbook minted a code for (``wb-...``) that the live service's registration has not yet
    been matched to (no ``el_person_alias`` row): listed only once official people exist in the
    store, because before the first sweep every workbook person is in that state and the list
    would be the whole league. Neither is ever guessed onto anyone.
    """
    items: list[dict[str, Any]] = [
        {"kind": "status", **item} for item in review_queue(ctx)["items"]
    ]
    persons = ctx.persons.values()
    if any(p.code_system == "official" for p in persons):
        aliased = {code for (code,) in ctx.session.execute(select(ElPersonAlias.wb_code))}
        for person in sorted(persons, key=lambda p: p.person_code):
            if person.code_system != "workbook" or person.person_code in aliased:
                continue
            reg = ctx.registration_of(person.person_code)
            items.append(
                {
                    "kind": "person",
                    "personCode": person.person_code,
                    "name": person.name,
                    "team": ctx.team_ref(reg.club_code) if reg is not None else None,
                }
            )
    return {"league": "euroleague", "items": items}


# --------------------------------------------------------------------------- the writes

_MAX_TEXT = {
    "reason_text": 200,
    "expected_return_text": 120,
    "source_label": 160,
    "player_name": 96,
}


def _check_length(value: str | None, name: str, field_name: str) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if len(text) > _MAX_TEXT[name]:
        raise bad_request(f"{field_name} is longer than {_MAX_TEXT[name]} characters.", field_name)
    return text or None


def _published(value: datetime | date | str, now: datetime) -> datetime:
    if isinstance(value, str):
        text = value.strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise bad_request(
                f"{value!r} is not an ISO date or time such as '2026-09-30'.", "sourcePublishedAt"
            ) from exc
        value = parsed
    if isinstance(value, datetime):
        moment = aware(value)
    else:
        moment = aware(datetime(value.year, value.month, value.day))
    if moment > aware(now) + _MAX_FUTURE:  # type: ignore[operator]
        raise bad_request("sourcePublishedAt is in the future.", "sourcePublishedAt")
    return naive_utc(moment)  # type: ignore[arg-type]


def record_status(
    ctx: ReadContext,
    *,
    club_code: str,
    status: str,
    source_label: str,
    source_published_at: datetime | date | str,
    person_code: str | None = None,
    player_name: str | None = None,
    game_id: str | None = None,
    reason_category: str | None = None,
    reason_text: str | None = None,
    expected_return_text: str | None = None,
    source_url: str | None = None,
    entered_by_user_id: int | None = None,
) -> dict[str, Any]:
    """Append one hand-entered status (``POST /v1/el/availability``). Does not commit.

    The source kind is ``manual`` when there is no link; with a link it is ``clubStatement`` when
    the label names the club and ``pressArticle`` otherwise. The link is screened against the
    gambling-operator denylist; a withheld link keeps its label (suffixed) and its date.
    """
    club = ctx.require_club(club_code)
    try:
        normalised = normalise_status(status)
    except InvalidStatusError as exc:
        raise invalid_status(str(exc)) from exc
    if normalised is None:
        raise invalid_status(
            "A status is required: one of out, doubtful, questionable, probable, available."
        )
    try:
        reason = normalise_reason_category(reason_category)
    except InvalidReasonCategoryError as exc:
        raise bad_request(str(exc), "reasonCategory") from exc
    label = _check_length(source_label, "source_label", "sourceLabel")
    if not label:
        raise bad_request("sourceLabel is required: who said it.", "sourceLabel")
    text = _check_length(reason_text, "reason_text", "reasonText")
    expected_text = _check_length(
        expected_return_text, "expected_return_text", "expectedReturnText"
    )
    published = _published(source_published_at, ctx.now)

    person: str | None = None
    raw_name = _check_length(player_name, "player_name", "playerName")
    if person_code:
        code = person_code.strip()
        if (club, code) not in ctx.registrations:
            raise bad_request(f"{code!r} is not registered with {club} this season.", "personCode")
        person, raw_name = code, ctx.player_name(code)
    elif raw_name:
        matches = ctx.folded_squad(club).get(fold_name(raw_name), [])
        person = matches[0] if len(matches) == 1 else None
    else:
        raise bad_request("Name the player: personCode or playerName.", "personCode")

    if game_id is not None:
        game = ctx.games_by_id.get(game_id)
        if game is None or club not in (game.home_club_code, game.away_club_code):
            raise bad_request(f"{game_id!r} is not a game of {club}.", "gameId")

    url: str | None = None
    if source_url is not None and source_url.strip():
        candidate = source_url.strip()
        parts = urlsplit(candidate)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            raise bad_request("sourceUrl must be an http or https link.", "sourceUrl")
        url = candidate
    if url is None:
        kind = "manual"
    else:
        club_row = ctx.clubs[club]
        names = {club_row.name.lower(), (club_row.short_name or club_row.name).lower()}
        kind = "clubStatement" if any(n and n in label.lower() for n in names) else "pressArticle"
    url, label = screen_source_link(url, label)

    parsed = parse_expected_return(expected_text, source_date=published.date())
    moment = naive_utc(ctx.now)
    row = ElIntelStatus(
        club_code=club,
        person_code=person,
        player_name_raw=(raw_name or "")[:96],
        game_id=game_id,
        game_date=ctx.games_by_id[game_id].game_date if game_id else None,
        status=normalised,
        status_raw=normalised.upper(),
        model_status=None,
        reason_category=reason,
        reason_text=text,
        expected_return_text=expected_text,
        expected_return_round_from=parsed.round_from,
        expected_return_round_to=parsed.round_to,
        expected_return_date=parsed.date,
        source_kind=kind,
        source_label=label,
        source_url=url,
        source_published_at=published,
        as_of=published,
        recorded_at=moment,
        entered_by_user_id=entered_by_user_id,
        provenance_note="Entered through the API",
    )
    ctx.session.add(row)
    ctx.session.flush()
    bump_sync_version(ctx.session, None, None, moment)
    return {
        "league": "euroleague",
        "statusId": row.status_id,
        "matched": person is not None,
        "person": ctx.player_ref(person, club) if person else None,
        "team": ctx.team_ref(club),
        "status": normalised,
        "sourceKind": kind,
        "linkWithheld": source_url is not None and url is None,
        "reviewQueue": person is None,
    }


def retract_status(
    ctx: ReadContext, status_id: int, *, entered_by_user_id: int | None = None
) -> dict[str, Any]:
    """Append a retraction of one status (``DELETE /v1/el/availability/{statusId}``).

    The retracted row stays in the table, and a projection rebuilt for a moment before the
    retraction still sees it. A second retraction of the same row is a no-op that says so.
    """
    from ...api.errors import ApiError

    session = ctx.session
    row = session.get(ElIntelStatus, status_id)
    if row is None:
        raise ApiError("not_found", f"No availability entry with id {status_id}.", http_status=404)
    if row.retracts_status_id is not None:
        raise bad_request("That row is itself a retraction.", "statusId")
    existing = (
        session.execute(select(ElIntelStatus).where(ElIntelStatus.retracts_status_id == status_id))
        .scalars()
        .first()
    )
    if existing is not None:
        return {
            "league": "euroleague",
            "statusId": status_id,
            "retractedBy": existing.status_id,
            "alreadyRetracted": True,
        }
    moment = naive_utc(ctx.now)
    retraction = ElIntelStatus(
        club_code=row.club_code,
        person_code=row.person_code,
        player_name_raw=row.player_name_raw,
        game_id=row.game_id,
        status=None,
        source_kind="manual",
        source_label="Retracted by hand",
        source_url=None,
        source_published_at=moment,
        as_of=moment,
        recorded_at=moment,
        retracts_status_id=row.status_id,
        entered_by_user_id=entered_by_user_id,
        provenance_note=f"Retracts entry {row.status_id}",
    )
    session.add(retraction)
    session.flush()
    bump_sync_version(session, None, None, moment)
    return {
        "league": "euroleague",
        "statusId": status_id,
        "retractedBy": retraction.status_id,
        "alreadyRetracted": False,
    }
