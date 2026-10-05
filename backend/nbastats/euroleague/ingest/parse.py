"""Parsers for the five EuroLeague responses: tolerant of what is absent, closed to what is odd.

Written against documentation, not against a response
-----------------------------------------------------
No EuroLeague host could be reached from the container this was written in. The field names
below come from the fixtures and source of an MIT-licensed client of the same service (the
shape of a round, a game, a box score, a club and a registration), and **no payload has been seen
by the code that parses it**. The parsers are therefore built around one rule: *when the shape
is not what was documented, say so, by name, and write nothing*. The first real run on the Mac
(``probe``) is expected to find a difference or two, and what it prints for each one is the
path of the key it wanted and the keys it found instead.

Two kinds of strictness, chosen on purpose
------------------------------------------
**Absent is fine; wrong is not.** A field that is missing, ``null`` or an empty string is *not
recorded* and becomes ``None`` (a dash on screen), never ``0`` and never a guess. A field that is
there but is the wrong kind of thing (text where a count belongs, ``3.5`` points, a list where an
object belongs) raises :class:`Unreadable` carrying the JSON path, because a value that cannot
be read cannot be told apart from one that was misread.

**Lists reject items, not payloads, until everything is rejected.** One malformed game in a round
of ten is reported (:class:`Rejected`, with its path) and the other nine are returned; if *every*
item of a non-empty list is rejected the payload itself is :class:`Unreadable`, because that is
what a changed shape looks like. A box score is one game, so it is all or nothing.

What the box score taught the shape (all from the documented fixture)
---------------------------------------------------------------------
* ``local`` is the home side and ``road`` the away side.
* Each side has ``players`` (each with the player's *registration* and a ``stats`` object),
  ``team`` (the team-rebounds row: the rebounds and turnovers credited to the team, not to any
  player) and ``total`` (the whole team, which therefore includes ``team``).
* ``stats.timePlayed`` is **seconds**, a float: ``2412.0``. A player who did not play has ``0.0``
  and zero everywhere, and that is how a ``dnp`` is recognised (his stat columns are then NULL,
  never zero).
* ``total.timePlayed`` is **not** the sum of the players' time. It is the game clock (2400 seconds
  for forty minutes) while the players' seconds add up to 12000 (five men for forty minutes).
  This parser keeps it as ``clock_seconds`` and the team's seconds are always the players' sum.
* The person code is six digits in the documented fixture; the design strips a leading ``P``
  where the service sends one (:func:`nbastats.euroleague.profile.normalise_person_code`).
* Positions are ``1`` (guard), ``2`` (forward) and ``3`` (center); anything else is not recorded.

Counts are integers or nothing
------------------------------
The service sends counts as floats (``23.0``). :func:`as_count` accepts a float only when it is a
whole number, rejects a negative count and rejects a fraction, so ``points: 3.5`` is a refusal
and not a rounding. Seconds are the exception: they are durations, they are rounded to the nearest
whole second, and the ±6 second tolerance in the invariants is what absorbs a fractional one.

Time zones
----------
``utcDate`` is the instant (read as UTC, stored as naive UTC like every timestamp here).
``game_date`` is the day in Europe/Berlin of that instant, which is where the competition keeps
its calendar; when only the venue-local ``date`` is given the date part is used and the
tip-off stays unknown.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Final, Generic, Mapping, Sequence, TypeVar
from zoneinfo import ZoneInfo

from ..profile import PHASES, POSITION_CODES, fold_name, normalise_club_code, normalise_person_code

__all__ = [
    "BERLIN",
    "Unreadable",
    "Rejected",
    "Parsed",
    "RoundInfo",
    "ClubInfo",
    "SideInfo",
    "GameInfo",
    "PersonInfo",
    "RegistrationInfo",
    "PlayerLine",
    "TeamTotals",
    "BoxSide",
    "BoxScore",
    "NO_PLAYED_FLAG",
    "STAT_COLUMNS_BY_KEY",
    "REQUIRED_WHEN_PLAYED",
    "EXPECTED_PATHS",
    "decode_json",
    "as_str",
    "as_count",
    "as_seconds",
    "as_bool",
    "friendly_name",
    "person_variants",
    "stored_name_variants",
    "parse_rounds",
    "parse_clubs",
    "parse_games",
    "parse_people",
    "parse_box_score",
    "box_digest",
]

BERLIN: Final = ZoneInfo("Europe/Berlin")

T = TypeVar("T")


class Unreadable(Exception):
    """A response (or an item of one) is not the shape the parsers were written for.

    ``path`` is a JSON path such as ``data[3].local.score``; ``reason`` says what was wrong.
    The message is meant to be shown, unedited, to the person who owns the Mac.
    """

    def __init__(self, reason: str, *, path: str = "$") -> None:
        super().__init__(f"{path}: {reason}")
        self.reason = reason
        self.path = path


@dataclass(frozen=True, slots=True)
class Rejected:
    """One item of a list that was left out, and why."""

    path: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}: {self.reason}"


@dataclass(frozen=True, slots=True)
class Parsed(Generic[T]):
    """The result of parsing a list: what was kept, what was rejected, what the envelope claimed.

    ``total`` is the envelope's own count when it gave one. A list whose ``total`` is larger than
    what arrived is *incomplete* (a paged response the client did not follow, because the
    allowlist has no paging parameter); callers say so rather than treat it as the whole set.
    ``ignored`` counts items that were understood and deliberately not kept (staff in a roster).
    """

    items: tuple[T, ...]
    rejected: tuple[Rejected, ...] = ()
    total: int | None = None
    ignored: int = 0

    @property
    def complete(self) -> bool:
        return (
            self.total is None or self.total <= len(self.items) + len(self.rejected) + self.ignored
        )


# ----------------------------------------------------------------------------- primitives


def decode_json(body: bytes | str, *, what: str = "the response") -> Any:
    """Decode a response body as JSON, or raise :class:`Unreadable` saying what it looked like.

    An HTML page (a Cloudflare block, a maintenance notice) is by far the likeliest non-JSON
    answer, so the first characters are named in the reason.
    """
    text = body.decode("utf-8-sig", errors="replace") if isinstance(body, bytes) else body
    stripped = text.strip()
    if not stripped:
        raise Unreadable(f"{what} is empty")
    try:
        return json.loads(stripped)
    except ValueError as exc:
        head = " ".join(stripped[:60].split())
        raise Unreadable(f"{what} is not JSON (it starts {head!r}): {exc}") from exc


def _at(path: str, key: str | int) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    return key if path in ("", "$") else f"{path}.{key}"


def _keys(obj: Mapping[str, Any], limit: int = 14) -> str:
    names = sorted(str(k) for k in obj)
    more = f", and {len(names) - limit} more" if len(names) > limit else ""
    return "[" + ", ".join(names[:limit]) + more + "]"


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _obj(value: Any, path: str, *, required: bool = True) -> Mapping[str, Any] | None:
    if value is None:
        if required:
            raise Unreadable("expected an object, found nothing", path=path)
        return None
    if not isinstance(value, dict):
        raise Unreadable(f"expected an object, found {type(value).__name__}", path=path)
    return value


def _need(obj: Mapping[str, Any], key: str, path: str) -> Any:
    """``obj[key]``, or an :class:`Unreadable` that names the keys that *were* there."""
    if key not in obj or _blank(obj[key]):
        raise Unreadable(
            f"required key {key!r} is missing; found {_keys(obj)}", path=_at(path, key)
        )
    return obj[key]


def as_str(value: Any, path: str = "$") -> str | None:
    """Trimmed text, ``None`` for absent or blank. Whole numbers are accepted as their digits
    (a dorsal sent as ``10``); anything else that is not text is :class:`Unreadable`."""
    if _blank(value):
        return None
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        raise Unreadable("expected text, found a boolean", path=path)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    raise Unreadable(f"expected text, found {type(value).__name__}", path=path)


#: No count or duration in a box score is anywhere near this; a value past it is a unit or a
#: shape problem, and must not reach an INTEGER column.
_CEILING: Final = 1_000_000


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool):
        raise Unreadable("expected a number, found a boolean", path=path)
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            raise Unreadable(f"expected a number, found {value!r}", path=path) from None
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        raise Unreadable(f"expected a number, found {value!r}", path=path)
    if number != number or number in (float("inf"), float("-inf")):
        raise Unreadable(f"expected a number, found {value!r}", path=path)
    if abs(number) > _CEILING:
        raise Unreadable(f"{value!r} is too large to be a count or a duration", path=path)
    return number


def as_count(
    value: Any, path: str = "$", *, minimum: int | None = 0, maximum: int | None = None
) -> int | None:
    """A whole number, ``None`` when absent. A fraction, a boolean or text that is not a number
    is :class:`Unreadable`; so is a value below ``minimum`` (pass ``None`` to allow negatives,
    as plus/minus does)."""
    if _blank(value):
        return None
    number = _number(value, path)
    if abs(number - round(number)) > 1e-9:
        raise Unreadable(f"expected a whole number, found {value!r}", path=path)
    result = int(round(number))
    if minimum is not None and result < minimum:
        raise Unreadable(f"expected {minimum} or more, found {result}", path=path)
    if maximum is not None and result > maximum:
        raise Unreadable(f"expected {maximum} or fewer, found {result}", path=path)
    return result


_CLOCK = re.compile(r"^(\d{1,3}):([0-5]\d)$")


def as_seconds(value: Any, path: str = "$") -> int | None:
    """A duration in seconds, rounded to the nearest second; ``None`` when absent.

    ``"12:34"`` is read as minutes and seconds, because it cannot mean anything else. Negative
    durations are :class:`Unreadable`.
    """
    if _blank(value):
        return None
    if isinstance(value, str):
        match = _CLOCK.match(value.strip())
        if match:
            return int(match.group(1)) * 60 + int(match.group(2))
    number = _number(value, path)
    if number < 0:
        raise Unreadable(f"expected a duration of zero or more, found {value!r}", path=path)
    return int(round(number))


_TRUE = {"true", "t", "yes", "y", "1"}
_FALSE = {"false", "f", "no", "n", "0"}


def as_bool(value: Any, path: str = "$") -> bool | None:
    if _blank(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
    raise Unreadable(f"expected true or false, found {value!r}", path=path)


def _iso(value: str, path: str, *, earliest: int = 2000) -> datetime:
    text = value.strip()
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError as exc:
        raise Unreadable(f"expected an ISO date or time, found {value!r}", path=path) from exc
    if not earliest <= moment.year <= 2100:
        raise Unreadable(f"{value!r} is not a plausible date", path=path)
    return moment


def _as_utc(value: Any, path: str) -> datetime | None:
    """An instant as naive UTC. A string with no zone is taken to be UTC (the field is named so)."""
    if _blank(value):
        return None
    if not isinstance(value, str):
        raise Unreadable(f"expected an ISO time, found {type(value).__name__}", path=path)
    moment = _iso(value, path)
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def _as_wall(value: Any, path: str) -> datetime | None:
    """A wall-clock time exactly as written, with any zone dropped (a venue-local time)."""
    if _blank(value):
        return None
    if not isinstance(value, str):
        raise Unreadable(f"expected an ISO time, found {type(value).__name__}", path=path)
    return _iso(value, path).replace(tzinfo=None)


def _as_date(value: Any, path: str) -> date | None:
    moment = _as_wall(value, path)
    return moment.date() if moment is not None else None


def _as_birth_date(value: Any, path: str) -> date | None:
    """A birth date: 1930 to 2100. Anything outside it (a ``0001-01-01`` placeholder) raises, and
    the caller records "not recorded"."""
    if _blank(value):
        return None
    if not isinstance(value, str):
        raise Unreadable(f"expected an ISO date, found {type(value).__name__}", path=path)
    moment = _iso(value, path, earliest=1930)
    return moment.date()


def _tolerated(fn: Any, value: Any, path: str, *args: Any) -> Any:
    """``fn(value, path, *args)``, or ``None`` when it raises :class:`Unreadable`.

    Only for identity facts that no number depends on (a birth date, a height, a registration's
    start date). Real data is messy exactly there (``0001-01-01`` for "unknown", a height of 0),
    and a placeholder in a cosmetic field must not make a whole box score unreadable. The value
    is simply not recorded.
    """
    try:
        return fn(value, path, *args)
    except Unreadable:
        return None


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise Unreadable(f"expected a list, found {type(value).__name__}", path=path)
    return value


def _unwrap_list(payload: Any, what: str) -> tuple[list[Any], int | None]:
    """``(items, total)`` from a bare list or from ``{"data": [...], "total": n}``."""
    if isinstance(payload, list):
        return payload, None
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            total = payload.get("total")
            count = total if isinstance(total, int) and not isinstance(total, bool) else None
            return data, count
        raise Unreadable(
            f"expected {what} as a list or as an object whose 'data' is a list; "
            f"found keys {_keys(payload)}"
        )
    raise Unreadable(f"expected {what} as a list, found {type(payload).__name__}")


def _parse_list(
    payload: Any,
    what: str,
    one: Any,
    *,
    ignore: Any = None,
) -> Parsed[Any]:
    entries, total = _unwrap_list(payload, what)
    items: list[Any] = []
    rejected: list[Rejected] = []
    ignored = 0
    for index, entry in enumerate(entries):
        path = _at("data", index)
        try:
            if ignore is not None and ignore(entry):
                ignored += 1
                continue
            items.append(one(entry, path))
        except Unreadable as exc:
            rejected.append(Rejected(exc.path, exc.reason))
    candidates = len(entries) - ignored  # staff rows were understood and set aside, not rejected
    if candidates and not items:
        first = rejected[0]
        raise Unreadable(
            f"all {candidates} {what} were rejected, so the shape has probably changed; "
            f"first: {first.path}: {first.reason}",
            path="data",
        )
    return Parsed(tuple(items), tuple(rejected), total, ignored)


# ------------------------------------------------------------------------------ names


_NAME_SPLIT = re.compile(r"([\s'’\-]+)")


def _title(text: str) -> str:
    return "".join(
        part.capitalize() if part and not _NAME_SPLIT.fullmatch(part) else part
        for part in _NAME_SPLIT.split(text)
    )


def friendly_name(raw: str) -> str:
    """``"SURNAME, GIVEN"`` as ``"Given Surname"``, title-cased when the source shouted.

    The service writes a person as ``BEAUBOIS, RODRIGUE``. The rest of Hardwood (and the user's
    workbook) writes ``Rodrigue Beaubois``, and a screen should not shout, so the stored name is
    the second form. A name that already has lower-case letters is only re-ordered. Matching
    never depends on this: :func:`fold_name` of either order is what is compared.
    """
    text = " ".join(str(raw).split())
    if "," in text:
        surname, _, given = text.partition(",")
        text = f"{given.strip()} {surname.strip()}".strip()
    if text and text == text.upper():
        text = _title(text.lower())
    return text


def person_variants(person: "PersonInfo") -> frozenset[str]:
    """The folded spellings a workbook might use for this person, for matching.

    The official name in both orders, the passport name in both orders (given names often appear
    in full there), the service's own abbreviated name (``"Surname, G."``, which is how the
    workbook abbreviates a few players) and a two-word alias. A single-word alias is a surname,
    not an identity, and is left out. Every entry is :func:`fold_name` output, and every one is a
    spelling the *service itself publishes*, so a match on one is exact; whether it is also unique
    is for the caller to decide. Variants that fold to nothing are dropped.
    """
    spellings: list[str] = [person.official_name, friendly_name(person.official_name)]
    if person.passport_name and person.passport_surname:
        spellings.append(f"{person.passport_name} {person.passport_surname}")
        spellings.append(f"{person.passport_surname} {person.passport_name}")
    for spelled in (person.abbreviated_name, person.alias):
        if spelled and len(spelled.replace(",", " ").split()) >= 2:
            spellings.append(spelled)
            spellings.append(friendly_name(spelled))
    return frozenset(v for v in (fold_name(s) for s in spellings) if v)


def stored_name_variants(name: str | None, display_name: str | None = None) -> frozenset[str]:
    """Variants available from a *stored* person (the store keeps ``name`` and ``display_name``).

    ``name`` is already ``"Given Surname"``; ``display_name`` is the service's abbreviated form
    (``"Surname, G."``), taken in both orders when it has two or more words.
    """
    spellings: list[str] = []
    if name:
        spellings += [name, friendly_name(name)]
    if display_name and len(display_name.replace(",", " ").split()) >= 2:
        spellings += [display_name, friendly_name(display_name)]
    return frozenset(v for v in (fold_name(s) for s in spellings) if v)


# ------------------------------------------------------------------------------ values


@dataclass(frozen=True, slots=True)
class RoundInfo:
    """E1: one round of the calendar. Start times are as written (venue-local wall clock)."""

    round: int
    phase_code: str | None
    name: str | None
    first_start: datetime | None
    last_start: datetime | None
    season_code: str | None


@dataclass(frozen=True, slots=True)
class ClubInfo:
    """A club as E2, E3, E4 and E5 each embed it. ``code`` is the official code; ``tv_code`` is
    the broadcast code and is never used as a key."""

    code: str
    tv_code: str | None = None
    name: str | None = None
    short_name: str | None = None
    country_code: str | None = None
    venue_code: str | None = None
    city: str | None = None


@dataclass(frozen=True, slots=True)
class SideInfo:
    """One side of a game in E2."""

    club: ClubInfo
    score: int | None
    #: Quarter scores then overtime scores, or ``None`` when not recorded (or unreadable).
    partials: tuple[int, ...] | None


@dataclass(frozen=True, slots=True)
class GameInfo:
    """E2: one game, scheduled or played.

    ``status`` is ``final`` only for a game flagged ``played`` with two different, non-negative
    scores; ``postponed`` when the service's own status text says so; ``scheduled`` otherwise.
    ``issues`` are the things noticed and not acted on (a tied score, unreadable partials).
    """

    game_code: int
    season_code: str | None
    round: int
    phase_code: str
    played: bool | None
    status: str
    tipoff_utc: datetime | None
    game_date: date
    home: SideInfo
    away: SideInfo
    venue_name: str | None
    venue_code: str | None
    is_neutral: bool | None
    attendance: int | None
    service_status: str | None
    issues: tuple[str, ...] = ()

    @property
    def ot_periods(self) -> int | None:
        """Overtime periods, from the partials: ``len - 4``. ``None`` without usable partials."""
        parts = self.home.partials
        if parts is None or self.away.partials is None or len(parts) < 4:
            return None
        return len(parts) - 4


@dataclass(frozen=True, slots=True)
class PersonInfo:
    """The identity facts of a person, from a registration or a box score's player object."""

    code: str
    official_name: str
    name: str
    abbreviated_name: str | None = None
    jersey_name: str | None = None
    alias: str | None = None
    passport_name: str | None = None
    passport_surname: str | None = None
    birth_date: date | None = None
    height_cm: int | None = None
    weight_kg: int | None = None
    country_code: str | None = None


@dataclass(frozen=True, slots=True)
class RegistrationInfo:
    """E5 (and the registration embedded in E3): a person's registration with a club."""

    person: PersonInfo
    club_code: str | None
    season_code: str | None
    dorsal: str | None
    position_code: int | None
    position_name: str | None
    active: bool | None
    start_date: date | None
    end_date: date | None


#: Service stat key -> store column, for a player line and for a team's totals alike.
STAT_COLUMNS_BY_KEY: Final[tuple[tuple[str, str], ...]] = (
    ("points", "pts"),
    ("fieldGoalsMade2", "fgm2"),
    ("fieldGoalsAttempted2", "fga2"),
    ("fieldGoalsMade3", "fgm3"),
    ("fieldGoalsAttempted3", "fga3"),
    ("freeThrowsMade", "ftm"),
    ("freeThrowsAttempted", "fta"),
    ("offensiveRebounds", "oreb"),
    ("defensiveRebounds", "dreb"),
    ("totalRebounds", "reb"),
    ("assistances", "ast"),
    ("steals", "stl"),
    ("turnovers", "tov"),
    ("blocksFavour", "blk"),
    ("blocksAgainst", "blk_against"),
    ("foulsCommited", "pf"),
    ("foulsReceived", "fouls_drawn"),
    ("plusMinus", "plus_minus"),
    ("valuation", "pir_official"),
)

#: What a played line must carry. A line that lacks these is not a box-score line the way the
#: parsers understand it, which is shape drift and not "not recorded".
REQUIRED_WHEN_PLAYED: Final[tuple[str, ...]] = (
    "pts",
    "fgm2",
    "fga2",
    "fgm3",
    "fga3",
    "ftm",
    "fta",
)

#: A player with zero seconds who nonetheless has one of these is not a "did not play": his
#: line contradicts itself. Fouls, fouls drawn, plus/minus, blocks against and PIR are left
#: out on purpose, because a bench technical foul is a real thing that happens at zero minutes.
_PRODUCTION: Final[tuple[str, ...]] = (
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
)

_NEGATIVE_OK: Final = frozenset({"plus_minus", "pir_official"})


@dataclass(frozen=True, slots=True)
class PlayerLine:
    """One player's line in an E3 box score. ``stats`` has every column; all ``None`` for a dnp."""

    person: PersonInfo
    club_code: str | None
    season_code: str | None
    participation: str
    is_starter: bool | None
    stats: Mapping[str, int | None]
    position_code: int | None
    position_name: str | None
    dorsal: str | None
    #: Production columns that were non-zero although ``timePlayed`` was zero.
    unexplained: tuple[str, ...] = ()
    active: bool | None = None

    @property
    def seconds(self) -> int | None:
        return self.stats.get("seconds_played")


@dataclass(frozen=True, slots=True)
class TeamTotals:
    """A side's totals (``total``), the game clock it reported, and its team-rebounds row."""

    stats: Mapping[str, int | None]
    clock_seconds: int | None
    team_row: Mapping[str, int | None]


@dataclass(frozen=True, slots=True)
class BoxSide:
    side: str
    club_code: str | None
    players: tuple[PlayerLine, ...]
    totals: TeamTotals
    coach_name: str | None = None


@dataclass(frozen=True, slots=True)
class BoxScore:
    """E3: both sides of one game."""

    home: BoxSide
    away: BoxSide
    season_code: str | None = None
    issues: tuple[str, ...] = field(default_factory=tuple)

    def sides(self) -> tuple[BoxSide, BoxSide]:
        return (self.home, self.away)


# ------------------------------------------------------------------------------ clubs


def _club(obj: Any, path: str) -> ClubInfo:
    club = _obj(obj, path)
    assert club is not None
    code = normalise_club_code(as_str(_need(club, "code", path), _at(path, "code")))
    if code is None:
        raise Unreadable("the club code is blank", path=_at(path, "code"))
    country = _obj(club.get("country"), _at(path, "country"), required=False)
    return ClubInfo(
        code=code,
        tv_code=normalise_club_code(as_str(club.get("tvCode"), _at(path, "tvCode"))),
        name=as_str(club.get("name"), _at(path, "name")),
        short_name=(
            as_str(club.get("abbreviatedName"), _at(path, "abbreviatedName"))
            or as_str(club.get("editorialName"), _at(path, "editorialName"))
        ),
        country_code=(
            as_str(country.get("code"), _at(_at(path, "country"), "code")) if country else None
        ),
        venue_code=as_str(club.get("venueCode"), _at(path, "venueCode")),
        city=as_str(club.get("city"), _at(path, "city")),
    )


def parse_clubs(payload: Any, *, expect_season: str | None = None) -> Parsed[ClubInfo]:
    """E4: the clubs of a season, keyed by ``code``, never by ``tvCode``.

    A club object that lacks ``code`` is rejected; a payload where every club does is unreadable.
    """

    def one(entry: Any, path: str) -> ClubInfo:
        return _club(entry, path)

    return _parse_list(payload, "clubs", one)


# ------------------------------------------------------------------------------ rounds


def parse_rounds(payload: Any, *, expect_season: str | None = None) -> Parsed[RoundInfo]:
    """E1: the rounds of a season. ``round`` is required; the rest is kept when present."""

    def one(entry: Any, path: str) -> RoundInfo:
        item = _obj(entry, path)
        assert item is not None
        number = as_count(_need(item, "round", path), _at(path, "round"), minimum=1, maximum=99)
        assert number is not None
        season = as_str(item.get("seasonCode"), _at(path, "seasonCode"))
        if expect_season is not None and season is not None and season != expect_season:
            raise Unreadable(
                f"belongs to season {season}, not the requested {expect_season}", path=path
            )
        phase = as_str(item.get("phaseTypeCode"), _at(path, "phaseTypeCode"))
        return RoundInfo(
            round=number,
            phase_code=phase.upper() if phase else None,
            name=as_str(item.get("name"), _at(path, "name")),
            first_start=_as_wall(item.get("minGameStartDate"), _at(path, "minGameStartDate")),
            last_start=_as_wall(item.get("maxGameStartDate"), _at(path, "maxGameStartDate")),
            season_code=season,
        )

    return _parse_list(payload, "rounds", one)


# ------------------------------------------------------------------------------ games


def _partials(side: Mapping[str, Any], path: str) -> tuple[tuple[int, ...] | None, str | None]:
    """``(partials, problem)``: quarter scores then overtime scores, or ``None`` with a reason.

    Partials only feed one invariant and the overtime count, both of which fall back to other
    evidence, so an odd shape here is noted and not fatal.
    """
    raw = side.get("partials")
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        return None, f"{_at(path, 'partials')}: expected an object, found {type(raw).__name__}"
    try:
        quarters = [
            as_count(raw.get(f"partials{n}"), _at(path, f"partials.partials{n}"))
            for n in (1, 2, 3, 4)
        ]
        extra_raw = raw.get("extraPeriods")
        extras: list[int] = []
        if isinstance(extra_raw, dict):
            keys = sorted(
                extra_raw,
                key=lambda k: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(k))],
            )
            for key in keys:
                value = as_count(extra_raw[key], _at(path, f"partials.extraPeriods.{key}"))
                if value is None:
                    return None, f"{_at(path, 'partials.extraPeriods.' + str(key))}: blank"
                extras.append(value)
        elif extra_raw is not None and extra_raw != []:
            return None, f"{_at(path, 'partials.extraPeriods')}: expected an object"
    except Unreadable as exc:
        return None, f"{exc.path}: {exc.reason}"
    if any(q is None for q in quarters):
        return None, None
    return tuple(int(q) for q in quarters if q is not None) + tuple(extras), None


def _side(obj: Any, path: str, issues: list[str]) -> SideInfo:
    side = _obj(obj, path)
    assert side is not None
    club = _club(_need(side, "club", path), _at(path, "club"))
    score = as_count(side.get("score"), _at(path, "score"))
    partials, problem = _partials(side, path)
    if problem:
        issues.append(f"partials unreadable ({problem})")
    return SideInfo(club=club, score=score, partials=partials)


#: The issue text for a game with no ``played`` key at all (as opposed to ``played: null``).
NO_PLAYED_FLAG: Final = "no 'played' flag"

_POSTPONED = re.compile(r"postpon|cancel|suspend|abandon", re.IGNORECASE)


def parse_games(
    payload: Any,
    *,
    expect_season: str | None = None,
    expect_round: int | None = None,
    phase_by_round: Mapping[int, str] | None = None,
) -> Parsed[GameInfo]:
    """E2: the games of one round.

    A game is rejected (with its path) when its code, round, clubs or date cannot be read, when it
    belongs to a different season or round than was asked for, or when its phase is not one of
    :data:`nbastats.euroleague.profile.PHASES`. ``phase_by_round`` (from E1) fills a phase the
    game itself omits; without either the game is rejected, because the store's phase column
    does not accept a guess.
    """

    def one(entry: Any, path: str) -> GameInfo:
        game = _obj(entry, path)
        assert game is not None
        issues: list[str] = []
        code = as_count(
            _need(game, "gameCode", path), _at(path, "gameCode"), minimum=1, maximum=9999
        )
        assert code is not None
        round_number = as_count(
            _need(game, "round", path), _at(path, "round"), minimum=1, maximum=99
        )
        assert round_number is not None
        if expect_round is not None and round_number != expect_round:
            raise Unreadable(
                f"says round {round_number}, but round {expect_round} was requested", path=path
            )
        season_obj = _obj(game.get("season"), _at(path, "season"), required=False)
        season = (
            as_str(season_obj.get("code"), _at(_at(path, "season"), "code")) if season_obj else None
        )
        if expect_season is not None and season is not None and season != expect_season:
            raise Unreadable(
                f"belongs to season {season}, not the requested {expect_season}", path=path
            )
        phase_obj = _obj(game.get("phaseType"), _at(path, "phaseType"), required=False)
        phase = (
            as_str(phase_obj.get("code"), _at(_at(path, "phaseType"), "code"))
            if phase_obj
            else None
        )
        phase = phase.upper() if phase else (phase_by_round or {}).get(round_number)
        if phase is None:
            raise Unreadable(
                "has no phaseType.code and the round calendar does not give one", path=path
            )
        if phase not in PHASES:
            raise Unreadable(
                f"phase {phase!r} is not one of {', '.join(PHASES)}",
                path=_at(path, "phaseType.code"),
            )
        home = _side(_need(game, "local", path), _at(path, "local"), issues)
        away = _side(_need(game, "road", path), _at(path, "road"), issues)
        if home.club.code == away.club.code:
            raise Unreadable(f"both sides are the club {home.club.code}", path=path)

        played = as_bool(game.get("played"), _at(path, "played"))
        if "played" not in game:
            issues.append(NO_PLAYED_FLAG)
        service_status = as_str(game.get("gameStatus"), _at(path, "gameStatus"))
        tipoff = _as_utc(game.get("utcDate"), _at(path, "utcDate"))
        local_wall = _as_wall(game.get("localDate") or game.get("date"), _at(path, "date"))
        if tipoff is not None:
            game_date = tipoff.replace(tzinfo=timezone.utc).astimezone(BERLIN).date()
        elif local_wall is not None:
            game_date = local_wall.date()
        else:
            raise Unreadable("has neither utcDate nor date", path=path)

        status = "scheduled"
        if played is True:
            if home.score is None or away.score is None:
                issues.append("flagged played but a score is missing; not treated as final")
            elif home.score == away.score:
                issues.append(
                    f"flagged played with a tied score {home.score}-{away.score}; "
                    "not treated as final"
                )
            else:
                status = "final"
        elif service_status and _POSTPONED.search(service_status):
            status = "postponed"

        venue = _obj(game.get("venue"), _at(path, "venue"), required=False)
        return GameInfo(
            game_code=code,
            season_code=season,
            round=round_number,
            phase_code=phase,
            played=played,
            status=status,
            tipoff_utc=tipoff,
            game_date=game_date,
            home=home,
            away=away,
            venue_name=(
                as_str(venue.get("name"), _at(_at(path, "venue"), "name")) if venue else None
            ),
            venue_code=(
                as_str(venue.get("code"), _at(_at(path, "venue"), "code")) if venue else None
            ),
            is_neutral=as_bool(game.get("isNeutralVenue"), _at(path, "isNeutralVenue")),
            attendance=as_count(game.get("audience"), _at(path, "audience")),
            service_status=service_status,
            issues=tuple(issues),
        )

    parsed = _parse_list(payload, "games", one)
    # A renamed "played" key would make every game read as scheduled for ever, and nothing would
    # ever become a result. One game without the key is a note; all of them is a changed shape.
    if parsed.items and all(NO_PLAYED_FLAG in g.issues for g in parsed.items):
        raise Unreadable(
            f"none of the {len(parsed.items)} games has a 'played' key, so no result could ever "
            "be recognised; the shape has probably changed",
            path="data[].played",
        )
    return parsed


# ----------------------------------------------------------------------- registrations


def _person(obj: Any, path: str) -> PersonInfo:
    person = _obj(obj, path)
    assert person is not None
    code = normalise_person_code(as_str(_need(person, "code", path), _at(path, "code")))
    if code is None:
        raise Unreadable("the person code is blank", path=_at(path, "code"))
    official = as_str(_need(person, "name", path), _at(path, "name"))
    assert official is not None
    country = _obj(person.get("country"), _at(path, "country"), required=False)
    return PersonInfo(
        code=code,
        official_name=official,
        name=friendly_name(official),
        abbreviated_name=as_str(person.get("abbreviatedName"), _at(path, "abbreviatedName")),
        jersey_name=as_str(person.get("jerseyName"), _at(path, "jerseyName")),
        alias=as_str(person.get("alias"), _at(path, "alias")),
        passport_name=as_str(person.get("passportName"), _at(path, "passportName")),
        passport_surname=as_str(person.get("passportSurname"), _at(path, "passportSurname")),
        birth_date=_tolerated(_as_birth_date, person.get("birthDate"), _at(path, "birthDate")),
        height_cm=_tolerated(
            lambda v, p: as_count(v, p, minimum=1, maximum=300),
            person.get("height"),
            _at(path, "height"),
        ),
        weight_kg=_tolerated(
            lambda v, p: as_count(v, p, minimum=1, maximum=400),
            person.get("weight"),
            _at(path, "weight"),
        ),
        country_code=(
            as_str(country.get("code"), _at(_at(path, "country"), "code")) if country else None
        ),
    )


def _position(value: Any, path: str) -> int | None:
    """1, 2 or 3 (guard, forward, center); anything else is not recorded."""
    number = as_count(value, path, minimum=None)
    return number if number in POSITION_CODES else None


def _registration(entry: Any, path: str) -> RegistrationInfo:
    reg = _obj(entry, path)
    assert reg is not None
    person = _person(_need(reg, "person", path), _at(path, "person"))
    club = _obj(reg.get("club"), _at(path, "club"), required=False)
    club_code = (
        normalise_club_code(as_str(club.get("code"), _at(_at(path, "club"), "code")))
        if club
        else None
    )
    season = _obj(reg.get("season"), _at(path, "season"), required=False)
    return RegistrationInfo(
        person=person,
        club_code=club_code,
        season_code=(
            as_str(season.get("code"), _at(_at(path, "season"), "code")) if season else None
        ),
        dorsal=as_str(reg.get("dorsal"), _at(path, "dorsal")),
        position_code=_position(reg.get("position"), _at(path, "position")),
        position_name=as_str(reg.get("positionName"), _at(path, "positionName")),
        active=as_bool(reg.get("active"), _at(path, "active")),
        start_date=_tolerated(_as_date, reg.get("startDate"), _at(path, "startDate")),
        end_date=_tolerated(_as_date, reg.get("endDate"), _at(path, "endDate")),
    )


def parse_people(
    payload: Any, *, expect_club: str | None = None, expect_season: str | None = None
) -> Parsed[RegistrationInfo]:
    """E5: a club's registered people. Only ``type == "J"`` (a player) is kept.

    Staff are counted in ``ignored`` and nothing else about them is read. A registration with no
    ``type`` cannot be classified, so it is rejected rather than assumed to be a player.
    ``expect_club`` and ``expect_season`` reject a registration that belongs elsewhere.
    """

    def is_staff(entry: Any) -> bool:
        if not isinstance(entry, dict):
            return False
        kind = entry.get("type")
        return isinstance(kind, str) and kind.strip() != "" and kind.strip().upper() != "J"

    def one(entry: Any, path: str) -> RegistrationInfo:
        reg = _obj(entry, path)
        assert reg is not None
        if _blank(reg.get("type")):
            raise Unreadable(
                f"has no 'type', so it cannot be told from a coach; found {_keys(reg)}", path=path
            )
        info = _registration(reg, path)
        if expect_club is not None and info.club_code not in (None, expect_club):
            raise Unreadable(f"belongs to club {info.club_code}, not {expect_club}", path=path)
        if expect_season is not None and info.season_code not in (None, expect_season):
            raise Unreadable(
                f"belongs to season {info.season_code}, not {expect_season}", path=path
            )
        return info

    return _parse_list(payload, "registrations", one, ignore=is_staff)


# -------------------------------------------------------------------------- box score


def _stat_columns(stats: Mapping[str, Any], path: str) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    for key, column in STAT_COLUMNS_BY_KEY:
        out[column] = as_count(
            stats.get(key),
            _at(path, key),
            minimum=None if column in _NEGATIVE_OK else 0,
        )
    return out


def _player_line(entry: Any, path: str) -> PlayerLine:
    item = _obj(entry, path)
    assert item is not None
    reg = _obj(_need(item, "player", path), _at(path, "player"))
    assert reg is not None
    stats = _obj(_need(item, "stats", path), _at(path, "stats"))
    assert stats is not None
    kind = as_str(reg.get("type"), _at(_at(path, "player"), "type"))
    if kind is not None and kind.upper() != "J":
        raise Unreadable(
            f"is a registration of type {kind!r}, not a player, inside a box score's players",
            path=_at(path, "player"),
        )
    registration = _registration(reg, _at(path, "player"))
    seconds = as_seconds(
        _need(stats, "timePlayed", _at(path, "stats")), _at(_at(path, "stats"), "timePlayed")
    )
    assert seconds is not None
    columns = _stat_columns(stats, _at(path, "stats"))
    started = as_bool(stats.get("startFive"), _at(_at(path, "stats"), "startFive"))
    dorsal = registration.dorsal or as_str(stats.get("dorsal"), _at(_at(path, "stats"), "dorsal"))
    if seconds == 0:
        unexplained = tuple(c for c in _PRODUCTION if (columns.get(c) or 0) != 0)
        blank: dict[str, int | None] = {c: None for _, c in STAT_COLUMNS_BY_KEY}
        blank["seconds_played"] = None
        return PlayerLine(
            person=registration.person,
            club_code=registration.club_code,
            season_code=registration.season_code,
            participation="dnp",
            is_starter=started,
            stats=blank,
            position_code=registration.position_code,
            position_name=registration.position_name,
            dorsal=dorsal,
            unexplained=unexplained,
            active=registration.active,
        )
    missing = [
        key
        for key, column in STAT_COLUMNS_BY_KEY
        if column in REQUIRED_WHEN_PLAYED and columns[column] is None
    ]
    if missing:
        raise Unreadable(
            f"a player with {seconds}s on court has no value for {', '.join(missing)}; "
            f"found {_keys(stats)}",
            path=_at(path, "stats"),
        )
    columns["seconds_played"] = seconds
    return PlayerLine(
        person=registration.person,
        club_code=registration.club_code,
        season_code=registration.season_code,
        participation="played",
        is_starter=started,
        stats=columns,
        position_code=registration.position_code,
        position_name=registration.position_name,
        dorsal=dorsal,
        active=registration.active,
    )


def _totals(side: Mapping[str, Any], path: str) -> TeamTotals:
    total = _obj(_need(side, "total", path), _at(path, "total"))
    assert total is not None
    team_row = _obj(side.get("team"), _at(path, "team"), required=False)
    columns = _stat_columns(total, _at(path, "total"))
    return TeamTotals(
        stats=columns,
        clock_seconds=as_seconds(total.get("timePlayed"), _at(_at(path, "total"), "timePlayed")),
        team_row=_stat_columns(team_row, _at(path, "team")) if team_row else {},
    )


def _box_side(obj: Any, name: str, side: str) -> BoxSide:
    root = _obj(obj, name)
    assert root is not None
    players_raw = _list(_need(root, "players", name), _at(name, "players"))
    lines = tuple(_player_line(p, _at(_at(name, "players"), i)) for i, p in enumerate(players_raw))
    clubs = sorted({ln.club_code for ln in lines if ln.club_code})
    if len(clubs) > 1:
        raise Unreadable(
            f"its players belong to different clubs ({', '.join(clubs)})", path=_at(name, "players")
        )
    coach = _obj(root.get("coach"), _at(name, "coach"), required=False)
    return BoxSide(
        side=side,
        club_code=clubs[0] if clubs else None,
        players=lines,
        totals=_totals(root, name),
        coach_name=as_str(coach.get("name"), _at(_at(name, "coach"), "name")) if coach else None,
    )


def parse_box_score(payload: Any) -> BoxScore:
    """E3: a box score. All or nothing: one unreadable line makes the game unreadable.

    ``local`` becomes the home side and ``road`` the away side. See the module docstring for the
    shape. The player lines are not checked against the totals here; that is
    :mod:`nbastats.euroleague.ingest.invariants`, which has the final score and the partials to
    check them against.
    """
    root = payload
    if isinstance(root, dict) and "local" not in root and isinstance(root.get("data"), dict):
        root = root["data"]
    if not isinstance(root, dict):
        raise Unreadable(f"expected an object, found {type(root).__name__}")
    for key in ("local", "road"):
        if key not in root:
            raise Unreadable(f"required key {key!r} is missing; found {_keys(root)}", path=key)
    home = _box_side(root["local"], "local", "home")
    away = _box_side(root["road"], "road", "away")
    seasons = sorted(
        {ln.season_code for side in (home, away) for ln in side.players if ln.season_code}
    )
    if len(seasons) > 1:
        raise Unreadable(f"the players belong to different seasons ({', '.join(seasons)})")
    if home.club_code and home.club_code == away.club_code:
        raise Unreadable(f"both sides are the club {home.club_code}")
    return BoxScore(home=home, away=away, season_code=seasons[0] if seasons else None)


def box_digest(box: BoxScore) -> str:
    """A SHA-256 of what a box score *says*: clubs, lines and totals, not its envelope.

    Hashing the response bytes would change when an unrelated field (an image address, a
    timestamp) did, and a change of hash is what triggers a write and a version bump. This hash
    moves only when a number does, so a re-fetch of an unchanged game writes nothing.
    """

    def side(entry: BoxSide) -> dict[str, Any]:
        return {
            "club": entry.club_code,
            "players": sorted(
                (
                    {
                        "code": p.person.code,
                        "part": p.participation,
                        "start": p.is_starter,
                        "pos": p.position_code,
                        "dorsal": p.dorsal,
                        "stats": dict(sorted(p.stats.items())),
                    }
                    for p in entry.players
                ),
                key=lambda d: d["code"],
            ),
            "totals": dict(sorted(entry.totals.stats.items())),
        }

    payload = {"home": side(box.home), "away": side(box.away)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


# -------------------------------------------------------------------- expected shapes

#: The dotted paths each parser looks for on one item of its response, as ``(path, required)``.
#: The probe prints, for a recorded response, which of these were found and which were not,
#: which is what makes a shape difference a one-line diagnosis instead of a guess.
EXPECTED_PATHS: Final[Mapping[str, tuple[tuple[str, bool], ...]]] = {
    "E1": (
        ("round", True),
        ("phaseTypeCode", False),
        ("name", False),
        ("minGameStartDate", False),
        ("maxGameStartDate", False),
        ("seasonCode", False),
    ),
    "E2": (
        ("gameCode", True),
        ("round", True),
        ("played", True),
        ("phaseType.code", True),
        ("utcDate", False),
        ("date", False),
        ("local.club.code", True),
        ("local.club.tvCode", False),
        ("local.score", True),
        ("local.partials.partials1", False),
        ("local.partials.extraPeriods", False),
        ("road.club.code", True),
        ("road.score", True),
        ("road.partials.partials1", False),
        ("venue.name", False),
        ("venue.code", False),
        ("audience", False),
        ("isNeutralVenue", False),
        ("gameStatus", False),
        ("season.code", False),
    ),
    "E3": (
        ("local.players", True),
        ("local.players[].player.person.code", True),
        ("local.players[].player.person.name", True),
        ("local.players[].player.club.code", False),
        ("local.players[].player.position", False),
        ("local.players[].player.type", False),
        ("local.players[].stats.timePlayed", True),
        ("local.players[].stats.points", True),
        ("local.players[].stats.fieldGoalsMade2", True),
        ("local.players[].stats.startFive", False),
        ("local.players[].stats.foulsReceived", False),
        ("local.players[].stats.valuation", False),
        ("local.total.points", True),
        ("local.total.timePlayed", False),
        ("local.team.totalRebounds", False),
        ("road.players", True),
        ("road.total.points", True),
    ),
    "E4": (
        ("code", True),
        ("tvCode", False),
        ("name", False),
        ("abbreviatedName", False),
        ("country.code", False),
        ("venueCode", False),
    ),
    "E5": (
        ("type", True),
        ("person.code", True),
        ("person.name", True),
        ("club.code", False),
        ("season.code", False),
        ("position", False),
        ("positionName", False),
        ("dorsal", False),
        ("active", False),
    ),
}
