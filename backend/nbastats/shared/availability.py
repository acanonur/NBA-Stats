"""Availability vocabulary and the rules that decide which status counts.

A player's availability reaches Hardwood as sourced, dated entries: a row from the NBA's
official injury report, a line a user copied from a club statement, a workbook import. Three
different questions are asked of that pile, and this module answers each of them separately
because conflating them is how a product tells a small lie:

1. **What do we show?** Exactly what the source said, with its age and its link. "No report"
   is ``status: null``, an em dash on screen. It is never "available", because nobody said so.
2. **What does the model assume?** A player with no usable entry plays with probability one.
   That is a modelling convenience, not a claim, so it is stated beside every projection as
   an *assumption* with a count and a basis (:func:`assumed_available_basis`), never folded
   into the display.
3. **Does an old entry still count?** An entry stops driving a projection when it has been
   superseded by what happened on court or has simply outlived the evidence for it (the three
   "in force" rules below). It still appears in the list, marked out of force and stale.

The two vocabularies are therefore two functions: :func:`display_chance` returns ``None`` for
no report; :func:`model_chance` returns ``1.0``.

Statuses
--------
``out``, ``doubtful``, ``questionable``, ``probable``, ``available`` (lower case on the wire)
or ``None``. :func:`normalise_status` accepts any capitalisation, because a workbook says
``OUT`` and an API caller says ``out``, and rejects every other string with
:class:`InvalidStatusError`. The workbook's own ``IFERROR(...,1)`` turned an unrecognised
status into "certainly plays"; that behaviour is deliberately not ported, because a typo in a
status silently marking a star as healthy is exactly the failure this module exists to prevent.
A blank string is not a status either: ``None`` is how a caller says "none".

Which entry applies to a game
-----------------------------
:func:`select_effective_entry`, in priority order:

1. an *active override*: one the user entered, not cleared, and not made obsolete by a newer
   league report snapshot arriving after it was entered;
2. the newest sourced entry *for that game*, by ``source_published_at``;
3. the newest *player-level* entry (no game) still in force;
4. none.

Step 2 is deliberately not filtered by "in force": an entry written for this exact game is the
best evidence there is about it, and if it is out of force the verdict says so and the model
ignores it, rather than quietly falling back to an older, vaguer entry for the player.

The three "in force" rules
--------------------------
:func:`entry_in_force` (an entry stops driving the model when any of these holds):

(a) *Its expected return has passed.* A parsed return date has passed once the league's
    calendar day is later than it. A return expressed in rounds has passed once the **last**
    game of the last round in the range has been played. The design text says "the first game
    of its expected-return round range"; read literally that voids every ``Rounds 2-4`` entry
    on the day its second round starts, although that wording in the user's own workbook means
    the *span of the absence* ("out for Rounds 2 to 4"). Keeping such an entry until the span
    has ended can only err toward keeping an out status a little long, and rules (b) and (c)
    below end any entry that the facts contradict, so that reading is used and recorded as a
    deviation.
(b) *The box score supersedes it.* The player has a line in a game played after the entry's
    ``source_published_at``.
(c) *It is too old.* Its age passes 14 days, unless its ``expected_return_text`` says
    ``long-term``, ``indefinite``, ``season`` or ``surgery``, which stay in force. A long-term
    entry nonetheless carries ``isStale`` after 7 days (:func:`entry_is_stale`).

Staleness for display is separate from in-force: it depends on the league. The NBA's clock is
the snapshot (an hour inside a reporting window, a day outside); the EuroLeague's is the
entry (seven days, or the team has played since the source date). Every entry carries
``ageMinutes``, computed from ``source_published_at`` and never from the fetch date.

Pure and stdlib-only; ``datetime`` values may be naive (taken as UTC) or aware.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Callable, Final, Hashable, Iterable, Mapping, NamedTuple

from .league_profile import (
    EUROLEAGUE_KEY,
    NBA_KEY,
    STATUS_ORDER,
    LeagueProfile,
    get_profile,
)

__all__ = [
    "STATUSES",
    "STATUS_LABELS",
    "REASON_CATEGORIES",
    "SOURCE_KINDS",
    "TEAM_REPORT_STATES",
    "IN_FORCE_MAX_AGE_DAYS",
    "LONG_TERM_STALE_DAYS",
    "LONG_TERM_PATTERN",
    "ASSUMED_BASES",
    "InvalidStatusError",
    "InvalidReasonCategoryError",
    "InvalidSourceKindError",
    "normalise_status",
    "normalise_reason_category",
    "normalise_source_kind",
    "status_label",
    "validate_status_chance",
    "display_chance",
    "model_chance",
    "assumed_available_basis",
    "age_minutes",
    "is_long_term",
    "ExpectedReturn",
    "parse_expected_return",
    "StatusEntry",
    "ForceVerdict",
    "Effective",
    "entry_in_force",
    "entry_is_stale",
    "select_effective_entry",
    "vocabulary",
]

STATUSES: Final[tuple[str, ...]] = STATUS_ORDER

STATUS_LABELS: Final[Mapping[str, str]] = {
    "out": "Out",
    "doubtful": "Doubtful",
    "questionable": "Questionable",
    "probable": "Probable",
    "available": "Available",
}

REASON_CATEGORIES: Final[tuple[str, ...]] = (
    "injury",
    "illness",
    "rest",
    "coachDecision",
    "personal",
    "suspension",
    "gLeague",
    "notWithTeam",
    "notRegistered",
    "other",
)

SOURCE_KINDS: Final[tuple[str, ...]] = (
    "leagueReport",
    "clubStatement",
    "pressArticle",
    "boxScoreInference",
    "workbookImport",
    "manual",
)

#: ``reportState`` of a team in an availability report. ``noReport`` is the absence of any
#: snapshot for the team, which is not the same thing as a submitted report with nobody on it.
TEAM_REPORT_STATES: Final[tuple[str, ...]] = ("submitted", "notYetSubmitted", "noReport")

#: Rule (c): an entry older than this stops driving the model, unless long-term.
IN_FORCE_MAX_AGE_DAYS: Final = 14
#: A long-term entry stays in force but is flagged stale after this many days.
LONG_TERM_STALE_DAYS: Final = 7
LONG_TERM_PATTERN: Final = re.compile(r"long[- ]term|indefinite|season|surgery", re.IGNORECASE)

#: The ``basis`` of an ``assumedAvailable`` count: why an absent player is assumed to play.
ASSUMED_BASES: Final[tuple[str, ...]] = (
    "notOnSubmittedReport",
    "teamReportPending",
    "noReportPublished",
    "noEntry",
)


class InvalidStatusError(ValueError):
    """An availability status that is not one of the five (or ``None``)."""


class InvalidReasonCategoryError(ValueError):
    """A reason category outside :data:`REASON_CATEGORIES`."""


class InvalidSourceKindError(ValueError):
    """A source kind outside :data:`SOURCE_KINDS`."""


# --------------------------------------------------------------------------- vocabulary


def normalise_status(raw: Any) -> str | None:
    """The canonical lower-case status for ``raw``; ``None`` stays ``None``.

    Raises :class:`InvalidStatusError` for any string that is not one of the five statuses
    (after trimming and lower-casing), including the empty string, and for non-strings.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        candidate = raw.strip().lower()
        if candidate in STATUS_LABELS:
            return candidate
    raise InvalidStatusError(
        f"invalid availability status {raw!r}; expected one of {', '.join(STATUSES)} or null"
    )


def normalise_reason_category(raw: Any) -> str | None:
    """A reason category exactly as stored (camelCase); ``None`` stays ``None``."""
    if raw is None:
        return None
    if isinstance(raw, str) and raw in REASON_CATEGORIES:
        return raw
    raise InvalidReasonCategoryError(
        f"invalid reason category {raw!r}; expected one of {', '.join(REASON_CATEGORIES)}"
    )


def normalise_source_kind(raw: Any) -> str:
    """A source kind exactly as stored; there is no null source kind."""
    if isinstance(raw, str) and raw in SOURCE_KINDS:
        return raw
    raise InvalidSourceKindError(
        f"invalid source kind {raw!r}; expected one of {', '.join(SOURCE_KINDS)}"
    )


def status_label(status: str | None) -> str | None:
    """Display label (``Out``) for a status; ``None`` for no status. Never a default."""
    if status is None:
        return None
    return STATUS_LABELS[normalise_status(status)]  # type: ignore[index]


def validate_status_chance(table: Mapping[str, Any]) -> dict[str, float]:
    """A copy of ``table`` with exactly the five statuses, each a finite chance in [0, 1].

    Used for the ``statusChance.*`` model settings, so a bad edit is refused at entry rather
    than producing a probability above one in a projection.
    """
    if set(table) != set(STATUSES):
        raise ValueError(
            f"status chance table must have exactly the keys {', '.join(STATUSES)}; "
            f"got {', '.join(sorted(map(str, table)))}"
        )
    out: dict[str, float] = {}
    for status in STATUSES:
        value = table[status]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"chance for {status!r} must be a number, got {value!r}")
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(f"chance for {status!r} must be within [0, 1], got {value!r}")
        out[status] = float(value)
    return out


def _table(chance_table: Mapping[str, float] | None) -> Mapping[str, float]:
    return chance_table if chance_table is not None else dict(get_profile(NBA_KEY).status_chance)


def display_chance(
    status: str | None, chance_table: Mapping[str, float] | None = None
) -> float | None:
    """Chance of playing for *display*: ``None`` when there is no status ("no report").

    ``chance_table`` is the league's ``statusChance.*`` settings; the shared defaults (the
    same for both leagues) apply when it is omitted.
    """
    if status is None:
        return None
    return float(_table(chance_table)[normalise_status(status)])  # type: ignore[index]


def model_chance(status: str | None, chance_table: Mapping[str, float] | None = None) -> float:
    """Chance of playing for the *model*: ``1.0`` when there is no status.

    This is an assumption, not a fact. A projection that uses it must list the player under
    ``assumptions.assumedAvailable`` with the basis from :func:`assumed_available_basis`.
    """
    chance = display_chance(status, chance_table)
    return 1.0 if chance is None else chance


def assumed_available_basis(league: str, team_report_state: str | None = None) -> str:
    """Why a player with no entry is assumed to play, for ``assumptions.assumedAvailable``.

    EuroLeague: ``noEntry``. NBA: ``notOnSubmittedReport`` when the team submitted a report
    (the rules oblige teams to list anyone whose participation may be affected, so absence
    from a submitted list carries meaning), ``teamReportPending`` when it has not yet
    submitted, ``noReportPublished`` when no snapshot exists at all.
    """
    if league == EUROLEAGUE_KEY:
        return "noEntry"
    if league != NBA_KEY:
        raise ValueError(f"unknown league {league!r}")
    if team_report_state == "submitted":
        return "notOnSubmittedReport"
    if team_report_state == "notYetSubmitted":
        return "teamReportPending"
    if team_report_state in (None, "noReport"):
        return "noReportPublished"
    raise ValueError(f"unknown team report state {team_report_state!r}")


# ------------------------------------------------------------------------------ time


def _utc(value: datetime) -> datetime:
    """An aware UTC datetime; a naive one is taken to already be UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def age_minutes(published_at: datetime, as_of: datetime) -> int:
    """Whole minutes from ``published_at`` to ``as_of``, never negative.

    Computed from the *source's* publication time, never from when Hardwood fetched it, so a
    week-old statement fetched a minute ago still reads as a week old. A source dated in the
    future (a clock error) reads as zero rather than negative.
    """
    seconds = (_utc(as_of) - _utc(published_at)).total_seconds()
    return max(0, int(seconds // 60))


def is_long_term(expected_return_text: str | None) -> bool:
    """True when the entry's expected-return text marks a long absence (rule (c) exemption)."""
    if not expected_return_text:
        return False
    return LONG_TERM_PATTERN.search(expected_return_text) is not None


# --------------------------------------------------------------------- expected return


class ExpectedReturn(NamedTuple):
    """What an expected-return string says, in the only three forms parsed."""

    round_from: int | None
    round_to: int | None
    date: date | None


_ROUNDS = re.compile(r"^\s*rounds?\s+(\d{1,3})(?:\s*[-–—]\s*(\d{1,3}))?\s*$", re.IGNORECASE)
_AROUND = re.compile(r"^\s*around\s+(\d{1,2})\s+([a-z]{3,9})\.?\s*$", re.IGNORECASE)
_MONTHS: Final[dict[str, int]] = {
    name: number
    for number, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}


def parse_expected_return(text: str | None, *, source_date: date | None = None) -> ExpectedReturn:
    """Parse ``Rounds a-b``, ``Round n`` or ``Around D Mon``; everything else is all-``None``.

    The match is anchored on the whole string, on purpose: ``Likely Round 4``, ``After Round
    3`` and ``Around Round 4-5`` carry a hedge the structured columns cannot, so they are left
    for the raw ``expected_return_text`` to show and parse to nothing. A range whose first
    round exceeds its last, or a round of zero, is unreadable and parses to nothing.

    ``Around 8 Oct`` has no year. It takes ``source_date``'s year, or the following year when
    that would put the date more than a month before the source date. Without a
    ``source_date`` the date is left ``None``, because guessing a year would be guessing a
    date. The fields hold the *span the source names*; rule (a) of the in-force test decides
    what that span means for the model.
    """
    if not text:
        return ExpectedReturn(None, None, None)
    match = _ROUNDS.match(text)
    if match:
        first = int(match.group(1))
        last = int(match.group(2)) if match.group(2) else first
        if first < 1 or last < first:
            return ExpectedReturn(None, None, None)
        return ExpectedReturn(first, last, None)
    match = _AROUND.match(text)
    if match and source_date is not None:
        month = _MONTHS.get(match.group(2).lower())
        day = int(match.group(1))
        if month is None:
            return ExpectedReturn(None, None, None)
        for year in (source_date.year, source_date.year + 1):
            try:
                candidate = date(year, month, day)
            except ValueError:
                return ExpectedReturn(None, None, None)
            if candidate >= source_date - timedelta(days=31):
                return ExpectedReturn(None, None, candidate)
    return ExpectedReturn(None, None, None)


# ---------------------------------------------------------------------------- entries


@dataclass(frozen=True)
class StatusEntry:
    """One availability fact about one player, in the league-neutral shape the rules need.

    The read sides build these from their own rows (``nba_intel_status`` and
    ``el_intel_status`` rows, overrides) and group them by player before asking for the
    effective entry. Keys are hashable and otherwise opaque, so integer NBA ids and string
    EuroLeague codes both fit.
    """

    entry_id: Hashable
    status: str | None
    source_kind: str
    source_published_at: datetime
    player_key: Hashable | None = None
    #: The game the entry is about, or ``None`` for a player-level entry.
    game_id: Hashable | None = None
    is_override: bool = False
    recorded_at: datetime | None = None
    #: Overrides only: when the user cleared it.
    cleared_at: datetime | None = None
    #: The id of the entry this row retracts. Retraction rows are never candidates themselves.
    retracts: Hashable | None = None
    expected_return_text: str | None = None
    expected_return_round_from: int | None = None
    expected_return_round_to: int | None = None
    expected_return_date: date | None = None

    @property
    def recorded(self) -> datetime:
        return self.recorded_at if self.recorded_at is not None else self.source_published_at


class ForceVerdict(NamedTuple):
    """Whether an entry may drive a projection, and the first rule that says it may not."""

    in_force: bool
    #: ``returnDatePassed``, ``returnRoundPassed``, ``playedSince`` or ``tooOld``; else ``None``.
    reason: str | None


def _round_end(round_end_at: Any, number: int) -> datetime | None:
    if round_end_at is None:
        return None
    if callable(round_end_at):
        return round_end_at(number)
    return round_end_at.get(number)


def entry_in_force(
    entry: StatusEntry,
    *,
    as_of: datetime,
    tz: tzinfo | None = None,
    round_end_at: Callable[[int], datetime | None] | Mapping[int, datetime] | None = None,
    last_played_at: datetime | None = None,
) -> ForceVerdict:
    """Apply the three in-force rules to ``entry`` at ``as_of``.

    ``tz`` is the league's scheduling zone, used to turn ``as_of`` into the calendar day a
    parsed return date is compared with (UTC when omitted). ``round_end_at`` gives, for a
    round number, the time by which that round's last game had been played (callers pass
    its tip-off), as a callable or a mapping;
    when it is absent or returns ``None`` the round clause cannot fire, and the other rules
    still apply. ``last_played_at`` is when the player's most recent *played* line took place
    (the game's tip-off), or ``None``.

    Overrides are not tested here: an override lives until it is cleared or superseded, which
    :func:`select_effective_entry` decides.
    """
    now = _utc(as_of)
    published = _utc(entry.source_published_at)

    # (a) the expected return has passed
    if entry.expected_return_date is not None:
        local_day = now.astimezone(tz).date() if tz is not None else now.date()
        if local_day > entry.expected_return_date:
            return ForceVerdict(False, "returnDatePassed")
    last_round = entry.expected_return_round_to or entry.expected_return_round_from
    if last_round is not None:
        ended = _round_end(round_end_at, last_round)
        if ended is not None and _utc(ended) <= now:
            return ForceVerdict(False, "returnRoundPassed")

    # (b) a box score after the entry supersedes it
    if last_played_at is not None and _utc(last_played_at) > published:
        return ForceVerdict(False, "playedSince")

    # (c) too old, unless the source calls it a long absence
    if now - published > timedelta(days=IN_FORCE_MAX_AGE_DAYS) and not is_long_term(
        entry.expected_return_text
    ):
        return ForceVerdict(False, "tooOld")

    return ForceVerdict(True, None)


def entry_is_stale(
    entry: StatusEntry,
    verdict: ForceVerdict,
    *,
    profile: LeagueProfile,
    as_of: datetime,
    in_reporting_window: bool | None = None,
    team_played_since_source: bool = False,
) -> bool:
    """The ``isStale`` flag shown beside an entry.

    An entry out of force is always stale. A long-term entry in force turns stale after
    seven days. Otherwise the league decides: the EuroLeague profile makes an entry stale
    after its ``max_age_days`` or once its team has played since the source date
    (``team_played_since_source``); the NBA profile uses the snapshot clock, an hour inside a
    reporting window (``in_reporting_window`` true) and a day outside it. When the caller
    cannot say whether a reporting window is open, the longer outside-window limit applies,
    which can only under-report staleness by hours, never invent it.
    """
    if not verdict.in_force:
        return True
    minutes = age_minutes(entry.source_published_at, as_of)
    if is_long_term(entry.expected_return_text):
        return minutes > LONG_TERM_STALE_DAYS * 24 * 60
    rules = profile.stale_after
    if rules.max_age_days is not None:
        if minutes > rules.max_age_days * 24 * 60:
            return True
        return rules.stale_when_team_has_played and team_played_since_source
    if rules.in_window_minutes is not None and rules.outside_window_minutes is not None:
        limit = rules.in_window_minutes if in_reporting_window else rules.outside_window_minutes
        return minutes > limit
    return False


@dataclass(frozen=True)
class Effective:
    """The entry that applies to one player in one game, and why."""

    entry: StatusEntry | None
    #: ``override``, ``gameEntry``, ``playerEntry`` or ``none``.
    rule: str
    verdict: ForceVerdict


def _newest(
    entries: Iterable[StatusEntry], key: Callable[[StatusEntry], Any]
) -> StatusEntry | None:
    best: StatusEntry | None = None
    for candidate in entries:
        if best is None or key(candidate) > key(best):
            best = candidate
    return best


def _published_key(entry: StatusEntry) -> tuple[datetime, datetime, str]:
    return (_utc(entry.source_published_at), _utc(entry.recorded), str(entry.entry_id))


def select_effective_entry(
    entries: Iterable[StatusEntry],
    *,
    game_id: Hashable | None,
    as_of: datetime,
    newest_league_report_at: datetime | None = None,
    tz: tzinfo | None = None,
    round_end_at: Callable[[int], datetime | None] | Mapping[int, datetime] | None = None,
    last_played_at: datetime | None = None,
) -> Effective:
    """The effective entry for one player in ``game_id`` (see the module docstring).

    ``entries`` are all the entries about *one* player. Retraction rows, and the rows they
    retract, are discarded first. ``newest_league_report_at`` is the as-of time of the newest
    ``leagueReport`` snapshot that exists; an override entered before it is obsolete, because
    the league has spoken since. Pass ``game_id=None`` to ask about the player in general
    (the game-specific step is then skipped). Information dated at or after ``as_of`` is not
    yet known and is ignored, which is what makes a projection rebuilt "as of tip-off" use
    only inputs from strictly before it.
    """
    now = _utc(as_of)
    pool = list(entries)
    retracted = {entry.retracts for entry in pool if entry.retracts is not None}
    live = [e for e in pool if e.retracts is None and e.entry_id not in retracted]

    def override_applies(entry: StatusEntry) -> bool:
        if not entry.is_override:
            return False
        if entry.game_id is not None and entry.game_id != game_id:
            return False
        if entry.cleared_at is not None and _utc(entry.cleared_at) <= now:
            return False
        if newest_league_report_at is not None and _utc(newest_league_report_at) > _utc(
            entry.recorded
        ):
            return False
        return _utc(entry.recorded) < now

    override = _newest(
        (e for e in live if override_applies(e)),
        lambda e: (_utc(e.recorded), str(e.entry_id)),
    )
    if override is not None:
        return Effective(override, "override", ForceVerdict(True, None))

    sourced = [e for e in live if not e.is_override and _utc(e.source_published_at) < now]

    def verdict_for(entry: StatusEntry) -> ForceVerdict:
        return entry_in_force(
            entry, as_of=as_of, tz=tz, round_end_at=round_end_at, last_played_at=last_played_at
        )

    if game_id is not None:
        for_game = _newest((e for e in sourced if e.game_id == game_id), _published_key)
        if for_game is not None:
            return Effective(for_game, "gameEntry", verdict_for(for_game))

    in_force = [(e, verdict_for(e)) for e in sourced if e.game_id is None]
    candidates = [e for e, verdict in in_force if verdict.in_force]
    player_entry = _newest(candidates, _published_key)
    if player_entry is not None:
        return Effective(player_entry, "playerEntry", ForceVerdict(True, None))
    return Effective(None, "none", ForceVerdict(False, None))


# --------------------------------------------------------------------------- contract


def vocabulary() -> dict[str, Any]:
    """The availability vocabulary as ``contracts/leagues.json`` carries it."""
    return {
        "statuses": [{"key": key, "label": STATUS_LABELS[key]} for key in STATUSES],
        "reasonCategories": list(REASON_CATEGORIES),
        "sourceKinds": list(SOURCE_KINDS),
        "teamReportStates": list(TEAM_REPORT_STATES),
        "assumedAvailableBases": list(ASSUMED_BASES),
        "statusChance": dict(get_profile(NBA_KEY).status_chance),
        "inForce": {
            "maxAgeDays": IN_FORCE_MAX_AGE_DAYS,
            "longTermStaleDays": LONG_TERM_STALE_DAYS,
            "longTermPattern": LONG_TERM_PATTERN.pattern,
        },
        "staleAfter": {
            key: get_profile(key).to_contract()["staleAfter"] for key in (NBA_KEY, EUROLEAGUE_KEY)
        },
    }
