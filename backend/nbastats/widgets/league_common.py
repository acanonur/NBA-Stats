"""What the four league widgets share: the league choice, the clock, the EuroLeague seam, results.

``team_matchup``, ``defense_by_position``, ``availability_report`` and ``slate_projections`` are
thin resolvers. Each one reads a config, picks a league, calls the *same* builder the matching
``/v1`` or ``/v1/el`` route calls, and hands back what the builder returned, so a tile's payload
is the route's payload and the two cannot drift. Everything the four have in common lives here so
each resolver stays a page of "which arguments does this tile pass".

The league is a config value, never a header
--------------------------------------------
Every one of the four kinds declares a ``league`` field (``nba`` or ``euroleague``). It is part of
the widget's own config, not of the layout document, because the layout migrators rebuild a layout
from a fixed key list and would silently drop a layout-level ``league``; a declared config key is
kept by :func:`nbastats.catalog.validate_widget_config`. :func:`league_of` reads it.

The NBA path uses the resolve context's session. The EuroLeague path cannot: the context holds a
session on the NBA store, and a EuroLeague row must never reach an NBA view (nor an NBA session
touch a EuroLeague table). So the EuroLeague path opens a session on its own store through
:func:`nbastats.shared.league_registry.session_for`, inside the resolver, and closes it before the
resolver returns. :func:`euroleague` is that, as a context manager.

The one place this layer reaches the EuroLeague's builders
----------------------------------------------------------
A tile needs the EuroLeague's builders (``team_matchup``, ``defense_by_position`` and the rest),
which live in the sealed ``nbastats.euroleague`` package that nothing outside it may import. The
EuroLeague therefore hands them out itself: its registry provider carries a ``read_side``
callable beside ``session_factory``, and :func:`_read_side` asks the registry for it
(:func:`nbastats.shared.league_registry.read_side`). This module imports nothing of the
EuroLeague, by name or by string. An unregistered league, a provider with no read side, or one
that cannot load it is the recoverable per-widget ``league_unavailable``, never a 500 and never a
half-built tile.

One clock
---------
A dashboard response stamps ``generatedAt`` from :func:`nbastats.db.utcnow`. The tiles read the
same function (imported here by name, so the fixtures exporter's clock freeze reaches it) and pass
it to the builders as ``now``, so one resolve is one instant: the ages in a tile's availability
entries, its ``resultPending`` games and its "next" slate are all computed at the moment the
response says it was generated, and a test or a fixture holds time still in one place.

What a tile says about itself
-----------------------------
The resolver contract is ``(payload, availability, notes)``. ``notes`` is what turns a result into
``"partial"``, so it carries only what is genuinely missing or withheld. The builders' own
``notes`` are mostly method commentary ("the NBA store does not record neutral-site games"), true
on every call and not a defect, so they are returned as the result's notes only when the payload's
availability is ``partial`` or ``unavailable``; a projection is always ``estimated``, which is a
label, not a gap, and carries no result notes. :func:`outcome` is that rule in one place.

Nothing a tile returns is compared with a market number. A projected score, margin and winner are
analytics; there is no external figure to measure them against, no probability of winning, and no
config field or builder argument that could carry either (``tests/test_widgets_league.py`` walks the
four kinds' config keys, and their payloads, against the market guard's vocabulary to hold it).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import ModuleType
from typing import Any, Iterator, Mapping, Sequence

from sqlalchemy.orm import Session

from ..api.errors import ApiError
from ..db import utcnow
from ..shared import league_registry
from ..shared.league_profile import EUROLEAGUE_KEY, LEAGUE_KEYS, NBA_KEY
from .base import WidgetError, invalid_config

__all__ = [
    "NBA_KEY",
    "EUROLEAGUE_KEY",
    "AVAILABILITIES",
    "league_of",
    "now",
    "league_unavailable",
    "euroleague",
    "read_module",
    "translate",
    "outcome",
    "euroleague_season",
    "state_availability",
]

#: The four values a result's ``availability`` may take (``contracts/CONTRACT.md`` §6).
AVAILABILITIES: tuple[str, ...] = ("full", "estimated", "partial", "unavailable")

#: What a result says when a payload is ``partial`` or ``unavailable`` but carries no sentence of
#: its own, so a degraded tile is never silent about it.
_FALLBACK_NOTES: Mapping[str, str] = {
    "partial": "Some of this tile's figures are withheld or incomplete.",
    "unavailable": "There is nothing to show for this tile yet.",
}

#: A availability report's ``state`` as the availability a result advertises. A report that is
#: current is ``full``; a stale one is shown but marked ``partial``; every other state (no report
#: yet, unreadable, switched off) has no statuses to trust, so it is ``unavailable``.
_STATE_AVAILABILITY: Mapping[str, str] = {"fresh": "full", "stale": "partial"}


def league_of(config: Mapping[str, Any]) -> str:
    """The tile's league, from its ``league`` config value.

    The catalog has already checked the value is a member of the field's options, so reaching the
    ``raise`` means a caller bypassed validation. It is still a clear per-widget error, never a
    ``KeyError``.
    """
    value = config.get("league")
    if value not in LEAGUE_KEYS:
        raise invalid_config(
            f"{value!r} is not a league; expected one of {list(LEAGUE_KEYS)}.", "league"
        )
    return str(value)


def now() -> datetime:
    """The instant this resolve is at: aware UTC, from the same clock as ``generatedAt``."""
    return utcnow().replace(tzinfo=timezone.utc)


def league_unavailable(reason: str) -> WidgetError:
    """The recoverable per-widget error for a league that is off or cannot be read.

    Recoverable because the reader can switch the league on, or change the tile's league; the
    ``league`` field is named so a client can open the configuration sheet on it.
    """
    return WidgetError("league_unavailable", reason, recoverable=True, field="league")


def _read_side(league: str, module: str = "read") -> ModuleType:
    """The league's read-side module, as its registry provider hands it out.

    An unregistered league, a provider with no read side, or one that cannot supply ``module`` is
    the recoverable ``league_unavailable``.
    """
    try:
        return league_registry.read_side(league, module)  # type: ignore[no-any-return]
    except league_registry.LeagueUnavailableError as exc:
        raise league_unavailable(exc.reason) from exc


def read_module(league: str, module: str = "read") -> ModuleType:
    """A module of ``league``'s read side (default: its builders); registered leagues only."""
    return _read_side(league, module)


@contextmanager
def euroleague() -> Iterator[tuple[Session, ModuleType]]:
    """``(session, builders)`` for the EuroLeague; the session is closed on the way out.

    ``session_for`` raises :class:`~nbastats.shared.league_registry.LeagueUnavailableError` when
    the EuroLeague is not registered or its store cannot be opened (the provider's own session
    factory raises while the league is misconfigured), and both become the recoverable
    ``league_unavailable`` on this one tile. The NBA is untouched either way.
    """
    try:
        session = league_registry.session_for(EUROLEAGUE_KEY)
    except league_registry.LeagueUnavailableError as exc:
        raise league_unavailable(exc.reason) from exc
    try:
        yield session, _read_side(EUROLEAGUE_KEY)
    finally:
        session.close()


def euroleague_season(value: Any) -> str | None:
    """A tile's ``season`` config as the EuroLeague's builders take it.

    ``latest`` (and nothing) mean the current season, which the builders resolve themselves. The
    dashboard's own season is an NBA season string and is deliberately never used here.
    """
    if value is None:
        return None
    text = str(value).strip()
    return None if not text or text.lower() == "latest" else text


def translate(exc: ApiError, fields: Mapping[str, str] | None = None) -> WidgetError:
    """A builder's :class:`ApiError` as the tile's :class:`WidgetError`, with the field renamed.

    The builders name their *route* parameters (``homeTeamId``, ``awayTeamId``); a tile has
    *config* keys (``team``, ``opponent``). Renaming lets a client open the configuration sheet on
    the row that is wrong. Code, message and recoverability are the builder's own.
    """
    field = (fields or {}).get(exc.field or "", exc.field)
    return WidgetError(exc.code, exc.message, recoverable=exc.recoverable, field=field)


def state_availability(state: Any) -> str:
    """The availability a report in ``state`` advertises (see :data:`_STATE_AVAILABILITY`)."""
    return _STATE_AVAILABILITY.get(str(state), "unavailable")


def outcome(
    payload: dict[str, Any],
    *,
    availability: str | None = None,
    notes: Sequence[str] | None = None,
) -> tuple[dict[str, Any], str, list[str]]:
    """``(payload, availability, notes)`` for a finished tile.

    ``availability`` defaults to the payload's own ``availability`` key and, failing that, ``full``.
    ``notes`` defaults to the payload's own ``notes``. Whichever is used, the *result's* notes
    are empty unless the availability is ``partial`` or ``unavailable`` (see the module docstring),
    and a degraded result with no sentence at all gets a generic one.
    """
    chosen = availability if availability is not None else payload.get("availability")
    if chosen not in AVAILABILITIES:
        chosen = "full"
    result_notes: list[str] = []
    if chosen in ("partial", "unavailable"):
        source = notes if notes is not None else payload.get("notes") or []
        result_notes = [str(note) for note in source if isinstance(note, str) and note]
        if not result_notes:
            result_notes = [_FALLBACK_NOTES[chosen]]
    return payload, str(chosen), result_notes
