"""Fetching the NBA injury report: which file to ask for, how far back to look, and when to ask.

Where the files are
-------------------
``https://ak-static.cms.nba.com/referee/injury/Injury-Report_<YYYY-MM-DD>_<hh>_<mm><AM|PM>.pdf``,
one file per Eastern-time quarter-hour slot, for example ``..._2026-10-22_05_30PM.pdf``. Not every
slot has a file. The URL template is a setting (``HARDWOOD_NBA_INJURY_URL_TEMPLATE``) because this
is the part of the design most likely to need changing: the path has moved before. It must be an
``https`` URL with the four placeholders ``{date}``, ``{hh}``, ``{mm}`` and ``{ampm}``.

The time in the file name is US Eastern wall-clock time, so the arithmetic here is done in
``America/New_York`` and converted to UTC only for storage. Clock changes are handled by the time
zone database rather than by hand (the 1:30 AM slot on the night clocks go back is ambiguous, and
no game is played then).

How far back a run looks
------------------------
The report is a complete snapshot, not a delta: the newest file is the whole story. So a run asks
for the newest slot that can exist (the current time floored to a quarter hour), and if that answers
404 it steps back one slot at a time, **stopping as soon as it reaches a slot it already has** and
never more than eight steps (two hours). On a quiet day that is one request; when a new report has
just appeared it is usually two. A 404 is the normal answer for a slot that was not published and is
not a failure. An unchanged file (same hash as one already stored) is not parsed or stored again,
but the slot is remembered as seen, so the next run does not ask for it again.

One host quirk is handled on purpose. Object stores commonly answer **403** for a key that does not
exist, and the polite client's default is to open its circuit breaker on any 403 (right for an API,
wrong here: the first probe of an unpublished slot would then silence the job for six hours). So
this fetcher passes ``blocked_statuses={401}`` and treats a 403 as "not there", with its own rule
for telling a quirk from a block: after :data:`FORBIDDEN_STREAK_LIMIT` consecutive 403s with nothing
fetched in between, the job opens the breaker itself. Whether the real host does this is unverified.

When to ask
-----------
The scheduler calls the job every 15 minutes and :func:`decide_poll` decides, from the games in the
store, whether to do anything. The job only runs when a game is scheduled within 36 hours. It polls
every 15 minutes inside a *reporting window*, which runs from 17:00 ET on the day before game day to
the last tip-off of game day, and hourly otherwise. All of that is pure arithmetic on a clock and a
list of games, so it is tested without a database.

This module imports no database; :mod:`nbastats.nba_intel.jobs` supplies the games and persists the
outcome.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from ..intel.http import (
    CircuitOpenError,
    FetchError,
    PoliteClient,
    TooLargeError,
    TransportFailure,
    check_url,
)

__all__ = [
    "DEFAULT_URL_TEMPLATE",
    "URL_TEMPLATE_ENV",
    "EASTERN",
    "SLOT_MINUTES",
    "MAX_STEPS",
    "PDF_MAX_BYTES",
    "FORBIDDEN_STREAK_LIMIT",
    "GAME_HORIZON",
    "POLL_IN_WINDOW",
    "POLL_OUTSIDE_WINDOW",
    "InvalidTemplateError",
    "GameInfo",
    "PollDecision",
    "SlotFetch",
    "url_template",
    "floor_slot",
    "slot_url",
    "slot_to_utc",
    "slot_from_utc",
    "slot_from_url",
    "candidate_slots",
    "decide_poll",
    "fetch_slot",
]

DEFAULT_URL_TEMPLATE = (
    "https://ak-static.cms.nba.com/referee/injury/Injury-Report_{date}_{hh}_{mm}{ampm}.pdf"
)
URL_TEMPLATE_ENV = "HARDWOOD_NBA_INJURY_URL_TEMPLATE"
EASTERN = ZoneInfo("America/New_York")
SLOT_MINUTES = 15
#: At most this many slots are asked for in one run.
MAX_STEPS = 8
#: A real report is a few hundred kilobytes; anything near this is not one.
PDF_MAX_BYTES = 8 * 1024 * 1024
#: Consecutive 403s, with no success between, before the job treats them as a block.
FORBIDDEN_STREAK_LIMIT = 12
#: The job is idle unless a game is scheduled within this long.
GAME_HORIZON = timedelta(hours=36)
POLL_IN_WINDOW = timedelta(minutes=15)
POLL_OUTSIDE_WINDOW = timedelta(hours=1)
#: A tick may land a little before the interval has fully elapsed; this much early is accepted.
POLL_TOLERANCE = timedelta(minutes=2)
#: When a game's tip-off time is unknown, the window is assumed to end at this Eastern time.
ASSUMED_LAST_TIP = time(22, 30)
WINDOW_OPENS = time(17, 0)

_PLACEHOLDERS = ("{date}", "{hh}", "{mm}", "{ampm}")
_FILENAME = re.compile(r"Injury-Report_(\d{4})-(\d{2})-(\d{2})_(\d{2})_(\d{2})(AM|PM)\.pdf$")


class InvalidTemplateError(ValueError):
    """The URL template setting is not a usable template."""


@dataclass(frozen=True, slots=True)
class GameInfo:
    """A not-yet-final game, as far as the poll decision needs to know it."""

    game_id: str
    game_date: date  # the NBA scheduling day, US Eastern
    tipoff_utc: datetime | None = None  # naive UTC, when the store knows it


@dataclass(frozen=True, slots=True)
class PollDecision:
    poll: bool
    reason: str
    in_window: bool = False
    interval: timedelta | None = None


@dataclass(frozen=True, slots=True)
class SlotFetch:
    """One slot's outcome. ``state`` is ``ok``, ``notFound`` (404/410), ``forbidden`` (403),
    ``blocked`` (breaker open, 401, or a Cloudflare page) or ``error``."""

    state: str
    url: str
    http_status: int | None
    fetched_at: datetime
    body: bytes | None = None
    sha256: str | None = None
    reason: str | None = None
    paused_until: datetime | None = None


# --------------------------------------------------------------------------- URLs and slots


def url_template(env: Mapping[str, str] | None = None) -> str:
    """The URL template: the environment's override if set, else the default. Validated."""
    source = os.environ if env is None else env
    template = (source.get(URL_TEMPLATE_ENV) or "").strip() or DEFAULT_URL_TEMPLATE
    missing = [p for p in _PLACEHOLDERS if p not in template]
    if missing:
        raise InvalidTemplateError(f"{URL_TEMPLATE_ENV} is missing {', '.join(missing)}")
    if not template.startswith("https://"):
        raise InvalidTemplateError(f"{URL_TEMPLATE_ENV} must be an https URL")
    return template


def _aware_utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def floor_slot(now: datetime) -> datetime:
    """The newest quarter-hour slot at or before ``now``, as an aware US Eastern datetime."""
    local = _aware_utc(now).astimezone(EASTERN)
    minute = local.minute - local.minute % SLOT_MINUTES
    return local.replace(minute=minute, second=0, microsecond=0)


def slot_url(slot: datetime, template: str | None = None) -> str:
    """The URL of ``slot`` (an Eastern datetime). The clock is spelled out by hand because
    ``%p`` depends on the process locale and the file names are always upper-case English."""
    local = slot.astimezone(EASTERN) if slot.tzinfo else slot.replace(tzinfo=EASTERN)
    hour12 = local.hour % 12 or 12
    ampm = "AM" if local.hour < 12 else "PM"
    return (template or DEFAULT_URL_TEMPLATE).format(
        date=local.strftime("%Y-%m-%d"), hh=f"{hour12:02d}", mm=f"{local.minute:02d}", ampm=ampm
    )


def slot_to_utc(slot: datetime) -> datetime:
    """A slot as naive UTC, the form every timestamp column stores."""
    local = slot.astimezone(EASTERN) if slot.tzinfo else slot.replace(tzinfo=EASTERN)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def slot_from_utc(value: datetime) -> datetime:
    """The inverse of :func:`slot_to_utc`: an aware Eastern datetime."""
    return _aware_utc(value).astimezone(EASTERN)


def slot_from_url(url: str) -> datetime | None:
    """The slot a report URL names (aware Eastern), or ``None`` if it is not a report URL."""
    found = _FILENAME.search(url)
    if not found:
        return None
    year, month, day, hour, minute, ampm = found.groups()
    h = int(hour) % 12 + (12 if ampm == "PM" else 0)
    try:
        return datetime(int(year), int(month), int(day), h, int(minute), tzinfo=EASTERN)
    except ValueError:
        return None


def candidate_slots(
    now: datetime, newest_fetched_utc: datetime | None, *, max_steps: int = MAX_STEPS
) -> list[datetime]:
    """The slots a run should ask for, newest first: from the current floor slot back, stopping at
    the first slot that is not newer than ``newest_fetched_utc``, and at ``max_steps``."""
    slots: list[datetime] = []
    current = floor_slot(now)
    newest = slot_from_utc(newest_fetched_utc) if newest_fetched_utc is not None else None
    for step in range(max_steps):
        candidate = (
            current.astimezone(timezone.utc) - timedelta(minutes=SLOT_MINUTES * step)
        ).astimezone(EASTERN)
        if newest is not None and candidate <= newest:
            break
        slots.append(candidate)
    return slots


# ------------------------------------------------------------------------------ cadence


def decide_poll(
    now: datetime,
    games: Sequence[GameInfo],
    last_probe_at: datetime | None,
    *,
    force: bool = False,
) -> PollDecision:
    """Should this tick fetch? A pure function of the clock, the games and the last probe time.

    ``games`` are the not-yet-final games the store knows; only those within :data:`GAME_HORIZON`
    count. ``last_probe_at`` is naive or aware UTC. ``force`` skips the interval but not the
    "is there a game at all" question: with no game in range there is no report to wait for.
    """
    moment = _aware_utc(now)
    local = moment.astimezone(EASTERN)
    horizon = moment + GAME_HORIZON

    relevant: list[GameInfo] = []
    for game in games:
        if game.tipoff_utc is not None:
            tip = _aware_utc(game.tipoff_utc)
            starts, ends = tip, tip + timedelta(hours=3)
        else:
            noon = datetime.combine(game.game_date, time(12, 0), tzinfo=EASTERN)
            starts = noon.astimezone(timezone.utc)
            ends = datetime.combine(game.game_date, time(23, 59), tzinfo=EASTERN).astimezone(
                timezone.utc
            )
        if starts <= horizon and ends >= moment:
            relevant.append(game)
    if not relevant:
        return PollDecision(False, "no game is scheduled within 36 hours")

    in_window = False
    for day in sorted({g.game_date for g in relevant}):
        opens = datetime.combine(day - timedelta(days=1), WINDOW_OPENS, tzinfo=EASTERN)
        tips = [g.tipoff_utc for g in games if g.game_date == day]
        if tips and all(t is not None for t in tips):
            closes = _aware_utc(max(tips)).astimezone(EASTERN)  # type: ignore[type-var]
        else:
            closes = datetime.combine(day, ASSUMED_LAST_TIP, tzinfo=EASTERN)
        if opens <= local <= closes:
            in_window = True
            break

    interval = POLL_IN_WINDOW if in_window else POLL_OUTSIDE_WINDOW
    if force:
        return PollDecision(True, "forced", in_window, interval)
    if last_probe_at is not None:
        elapsed = moment - _aware_utc(last_probe_at)
        if elapsed < interval - POLL_TOLERANCE:
            minutes = int((interval - elapsed).total_seconds() // 60) + 1
            return PollDecision(
                False,
                f"the last check was {int(elapsed.total_seconds() // 60)} minutes ago; "
                f"next in about {minutes}",
                in_window,
                interval,
            )
    where = "inside a reporting window" if in_window else "outside a reporting window"
    return PollDecision(True, f"a game is scheduled and it is {where}", in_window, interval)


# ------------------------------------------------------------------------------- fetching


def fetch_slot(client: PoliteClient, url: str) -> SlotFetch:
    """Request one slot's PDF politely and classify the answer, never raising for a normal miss.

    A 200 must be a PDF (``%PDF-`` magic); an HTML error page that answers 200 is an ``error``, not
    a report. See the module docstring for why 403 is ``forbidden`` rather than ``blocked`` here.
    """
    problem = check_url(url)
    if problem:
        return SlotFetch("error", url, None, datetime.now(timezone.utc), reason=problem)
    try:
        response = client.get(
            url,
            headers={"Accept": "application/pdf,*/*;q=0.5"},
            max_bytes=PDF_MAX_BYTES,
            blocked_statuses={401},
        )
    except CircuitOpenError as exc:
        return SlotFetch(
            "blocked",
            url,
            exc.status,
            datetime.now(timezone.utc),
            reason=exc.reason,
            paused_until=exc.paused_until,
        )
    except TooLargeError:
        return SlotFetch(
            "error",
            url,
            None,
            datetime.now(timezone.utc),
            reason=f"the file is larger than {PDF_MAX_BYTES // (1024 * 1024)} MB",
        )
    except (TransportFailure, FetchError) as exc:
        return SlotFetch("error", url, None, datetime.now(timezone.utc), reason=str(exc))

    status = response.status
    if status in (404, 410):
        return SlotFetch("notFound", url, status, response.fetched_at, reason="not published")
    if status == 403:
        return SlotFetch(
            "forbidden",
            url,
            status,
            response.fetched_at,
            reason="the host answered 403 (also how it may say a file does not exist)",
        )
    if status != 200:
        return SlotFetch(
            "error", url, status, response.fetched_at, reason=f"the host answered {status}"
        )
    if not response.body.startswith(b"%PDF-"):
        return SlotFetch(
            "error", url, status, response.fetched_at, reason="the response is not a PDF"
        )
    return SlotFetch(
        "ok", url, status, response.fetched_at, body=response.body, sha256=response.sha256
    )
