"""Builders for the league-neutral objects every new payload is made of.

Every payload that talks about the NBA or the EuroLeague refers to a team, a player, a game,
a source and a freshness in the same shape, so a client can render an NBA club and a
EuroLeague club with one code path and key them by ``(league, id)``. This module is
the one place those shapes are written down (design section 9.1); both leagues' read sides
call it, and a payload that builds the shape by hand is a payload that will drift.

Wire rules, applied here so no caller has to remember them
----------------------------------------------------------
* Keys are lowerCamelCase.
* ``id`` is always a **string**: the NBA's numeric team id as text (``"1610612738"``), the
  EuroLeague's club code. The numeric ``teamId`` / ``playerId`` ride beside it for the NBA
  only, and ``clubCode`` / ``personCode`` for the EuroLeague only; the other league's key is
  *absent*, not null, so a leaked identifier cannot hide behind a null.
* Timestamps are RFC-3339 UTC with a ``Z`` and no fractional seconds (``contracts/CONTRACT.md``
  section 1); calendar dates are ISO ``YYYY-MM-DD`` in the league's own scheduling zone (US
  Eastern for the NBA, Europe/Berlin for the EuroLeague). A naive ``datetime`` is taken to be
  UTC, because the stores keep naive UTC.
* Anything not recorded is ``None`` and stays ``None``. A jersey that is unknown is not ``""``.
* A player's ``position`` is one of ``G``, ``F``, ``C`` or ``None``; the raw string the source
  used travels beside it as ``positionRaw``.

``resultPending``
-----------------
A game whose tip-off has passed by three hours and which has no stored result is neither
upcoming nor final: it is waiting for data. :func:`game_status` derives that at read time,
never stored, so the day a result lands the status flips on its own. This is the honest day-one
state of a league whose last round was played before the workbook was exported: it says
"result pending", not "upcoming".

Pure and stdlib-only.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Final, Iterable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .availability import normalise_source_kind
from .league_profile import EUROLEAGUE_KEY, LEAGUE_KEYS, NBA_KEY, get_profile

__all__ = [
    "RESULT_PENDING_AFTER",
    "GAME_STATUSES",
    "rfc3339",
    "iso_date",
    "team_ref",
    "nba_team_ref",
    "el_team_ref",
    "player_ref",
    "nba_player_ref",
    "el_player_ref",
    "source_ref",
    "freshness_block",
    "game_status",
    "game_ref",
]

#: How long after tip-off a game with no stored result becomes ``resultPending``.
RESULT_PENDING_AFTER: Final = timedelta(hours=3)

GAME_STATUSES: Final[tuple[str, ...]] = ("scheduled", "resultPending", "final", "postponed")

_POSITIONS: Final = ("G", "F", "C")


def _check_league(league: str) -> str:
    if league not in LEAGUE_KEYS:
        raise ValueError(f"unknown league {league!r}; expected one of {', '.join(LEAGUE_KEYS)}")
    return league


def rfc3339(value: datetime | None) -> str | None:
    """``value`` as ``2026-10-01T18:00:00Z`` (UTC, seconds); ``None`` stays ``None``.

    Sub-second precision is dropped by truncation, not rounding, so a timestamp never reads
    as later than it was.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_date(value: date | datetime | str | None) -> str | None:
    """A calendar date as ``YYYY-MM-DD``. A ``datetime`` loses its time; a string is checked."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return date.fromisoformat(value.strip()).isoformat()
    raise TypeError(f"cannot render {type(value).__name__} as a date")


def _timestamp(value: datetime | str | None) -> str | None:
    """A timestamp field: a ``datetime`` is rendered, a string is trusted as already wire."""
    if value is None or isinstance(value, str):
        return value
    return rfc3339(value)


# ------------------------------------------------------------------------------ teams


def team_ref(
    league: str,
    *,
    id: str,  # noqa: A002 - the wire key is ``id``
    abbr: str,
    name: str,
    short_name: str | None = None,
    team_id: int | None = None,
    club_code: str | None = None,
    tv_code: str | None = None,
) -> dict[str, Any]:
    """A ``LeagueTeamRef``.

    ``team_id`` is accepted for the NBA only and ``club_code`` / ``tv_code`` for the
    EuroLeague only; passing the other league's field raises, because a payload carrying a
    EuroLeague club code on an NBA team is the leak the sealed design exists to prevent.
    """
    _check_league(league)
    if not isinstance(id, str) or not id:
        raise ValueError("a team ref needs a non-empty string id")
    if league == NBA_KEY and (club_code is not None or tv_code is not None):
        raise ValueError("clubCode and tvCode belong to the EuroLeague, not the NBA")
    if league == EUROLEAGUE_KEY and team_id is not None:
        raise ValueError("teamId belongs to the NBA, not the EuroLeague")
    ref: dict[str, Any] = {
        "league": league,
        "id": id,
        "abbr": abbr,
        "name": name,
        "shortName": short_name,
    }
    if league == NBA_KEY and team_id is not None:
        ref["teamId"] = int(team_id)
    if league == EUROLEAGUE_KEY:
        if club_code is not None:
            ref["clubCode"] = club_code
        ref["tvCode"] = tv_code
    return ref


def nba_team_ref(
    team_id: int, abbr: str, name: str, short_name: str | None = None
) -> dict[str, Any]:
    """An NBA ``LeagueTeamRef``: ``id`` is the numeric team id as a string."""
    return team_ref(
        NBA_KEY, id=str(int(team_id)), abbr=abbr, name=name, short_name=short_name, team_id=team_id
    )


def el_team_ref(
    club_code: str,
    name: str,
    short_name: str | None = None,
    tv_code: str | None = None,
) -> dict[str, Any]:
    """A EuroLeague ``LeagueTeamRef``: ``id`` and ``abbr`` are both the club code."""
    return team_ref(
        EUROLEAGUE_KEY,
        id=club_code,
        abbr=club_code,
        name=name,
        short_name=short_name,
        club_code=club_code,
        tv_code=tv_code,
    )


# ---------------------------------------------------------------------------- players


def player_ref(
    league: str,
    *,
    id: str,  # noqa: A002 - the wire key is ``id``
    name: str,
    position: str | None = None,
    position_raw: str | None = None,
    jersey: str | None = None,
    headshot_url: str | None = None,
    player_id: int | None = None,
    person_code: str | None = None,
) -> dict[str, Any]:
    """A ``LeaguePlayerRef`` (same league-only identifier rule as :func:`team_ref`)."""
    _check_league(league)
    if not isinstance(id, str) or not id:
        raise ValueError("a player ref needs a non-empty string id")
    if position is not None and position not in _POSITIONS:
        raise ValueError(
            f"position must be one of {', '.join(_POSITIONS)} or None, got {position!r}"
        )
    if league == NBA_KEY and person_code is not None:
        raise ValueError("personCode belongs to the EuroLeague, not the NBA")
    if league == EUROLEAGUE_KEY and player_id is not None:
        raise ValueError("playerId belongs to the NBA, not the EuroLeague")
    ref: dict[str, Any] = {
        "league": league,
        "id": id,
        "name": name,
        "position": position,
        "positionRaw": position_raw,
        "jersey": jersey,
        "headshotUrl": headshot_url,
    }
    if league == NBA_KEY and player_id is not None:
        ref["playerId"] = int(player_id)
    if league == EUROLEAGUE_KEY and person_code is not None:
        ref["personCode"] = person_code
    return ref


def nba_player_ref(player_id: int, name: str, **fields: Any) -> dict[str, Any]:
    """An NBA ``LeaguePlayerRef``: ``id`` is the numeric player id as a string."""
    return player_ref(NBA_KEY, id=str(int(player_id)), name=name, player_id=player_id, **fields)


def el_player_ref(person_code: str, name: str, **fields: Any) -> dict[str, Any]:
    """A EuroLeague ``LeaguePlayerRef``: ``id`` is the bare official or minted person code."""
    return player_ref(EUROLEAGUE_KEY, id=person_code, name=name, person_code=person_code, **fields)


# ---------------------------------------------------------------- sources and freshness


def source_ref(
    kind: str,
    label: str,
    *,
    published_at: datetime | str,
    url: str | None = None,
    as_of: datetime | str | None = None,
    fetched_at: datetime | str | None = None,
    snapshot_id: int | None = None,
) -> dict[str, Any]:
    """A ``Source``: where one availability fact came from.

    ``published_at`` is required (it is what ages the fact); ``url`` is ``None`` only for a
    manual entry or a link withheld by the source denylist, and the label says which.
    """
    normalise_source_kind(kind)
    if published_at is None:
        raise ValueError("a source needs the time its source was published")
    return {
        "kind": kind,
        "label": label,
        "url": url,
        "publishedAt": _timestamp(published_at),
        "asOf": _timestamp(as_of),
        "fetchedAt": _timestamp(fetched_at),
        "snapshotId": snapshot_id,
    }


def freshness_block(
    league: str,
    *,
    sync_version: int,
    data_through: date | datetime | str | None,
    generated_at: datetime | str,
    is_demo: bool,
    sources: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """A ``Freshness``: how current a payload is and how much of it is invented.

    ``sources`` are the summaries of only those sources that fed *this* payload, so a payload
    about injuries does not carry the box-score ingest's health. ``is_demo`` is always shown
    by a client as a banner.
    """
    _check_league(league)
    if isinstance(data_through, datetime):
        through: str | None = rfc3339(data_through)
    else:
        through = iso_date(data_through)
    return {
        "league": league,
        "syncVersion": int(sync_version),
        "dataThrough": through,
        "generatedAt": _timestamp(generated_at),
        "isDemo": bool(is_demo),
        "sources": [dict(summary) for summary in sources],
    }


# ------------------------------------------------------------------------------ games


def _local_end_of_day_utc(day: date, tz_name: str) -> datetime:
    """23:59 on ``day`` in ``tz_name``, as aware UTC."""
    try:
        zone = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:  # pragma: no cover - needs a stripped-down OS image
        zone = timezone.utc  # type: ignore[assignment]
    return datetime.combine(day, time(23, 59), tzinfo=zone).astimezone(timezone.utc)


def game_status(
    *,
    league: str,
    has_result: bool,
    now: datetime,
    tipoff_utc: datetime | None = None,
    game_date: date | None = None,
    postponed: bool = False,
) -> str:
    """``scheduled``, ``resultPending``, ``final`` or ``postponed`` for one game.

    A stored result makes a game ``final`` and a postponement makes it ``postponed``, both
    regardless of the clock. Otherwise a game is ``resultPending`` once three hours have
    passed since tip-off, or since 23:59 on the game date in the league's scheduling zone
    when the tip-off is unknown; before that it is ``scheduled``. A game with neither a
    tip-off nor a date cannot be placed in time and stays ``scheduled``.
    """
    profile = get_profile(league)
    if postponed:
        return "postponed"
    if has_result:
        return "final"
    if tipoff_utc is not None:
        reference = tipoff_utc if tipoff_utc.tzinfo else tipoff_utc.replace(tzinfo=timezone.utc)
    elif game_date is not None:
        reference = _local_end_of_day_utc(game_date, profile.schedule_tz)
    else:
        return "scheduled"
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return "resultPending" if reference + RESULT_PENDING_AFTER < current else "scheduled"


def game_ref(
    league: str,
    *,
    game_id: str,
    date: date | str,  # noqa: A002 - the wire key is ``date``
    home: Mapping[str, Any],
    away: Mapping[str, Any],
    status: str,
    tipoff_utc: datetime | str | None = None,
    venue: str | None = None,
    is_neutral: bool | None = None,
    round_number: int | None = None,
    phase: str | None = None,
    home_pts: int | None = None,
    away_pts: int | None = None,
    overtime_periods: int | None = None,
) -> dict[str, Any]:
    """A ``GameRefL``.

    A game that is not ``final`` carries no score, whatever the caller passes: a half-written
    result must not look like one. ``is_neutral`` stays ``None`` when unknown and is never
    assumed false.
    """
    _check_league(league)
    if status not in GAME_STATUSES:
        raise ValueError(f"status must be one of {', '.join(GAME_STATUSES)}, got {status!r}")
    final = status == "final"
    return {
        "league": league,
        "gameId": game_id,
        "date": iso_date(date),
        "tipoffUtc": _timestamp(tipoff_utc),
        "venue": venue,
        "isNeutral": is_neutral,
        "round": round_number,
        "phase": phase,
        "status": status,
        "home": dict(home),
        "away": dict(away),
        "homePts": home_pts if final else None,
        "awayPts": away_pts if final else None,
        "overtimePeriods": overtime_periods if final else None,
    }
