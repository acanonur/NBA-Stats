"""How the rest of the application finds the EuroLeague without importing it.

The EuroLeague is sealed: nothing outside ``nbastats/euroleague/`` may import it (a test holds the
line), yet three places outside it legitimately need to *ask* it something. ``GET /v1/leagues``
has to list it, the dashboard's widget layer has to open a session on its store to resolve a tile,
and the widget-config validator has to check that a club code exists. The neutral seam is
:mod:`nbastats.shared.league_registry`, and this module is the EuroLeague's side of it: it
registers a :class:`~nbastats.shared.league_registry.LeagueProvider` with three callables.

``session_factory``
    Opens a session on the EuroLeague store, and **raises while the league is not ready**
    (disabled, misconfigured, nothing configured), so the registry's ``session_for`` turns that
    into the recoverable ``league_unavailable`` a widget shows on one tile, never a 500.
``describe``
    The row ``GET /v1/leagues`` shows: whether the league is enabled, its state and the reason,
    whether the data is invented, the current season, the sync cursor and what it can do. It
    never raises, and a store that cannot be read becomes ``state: "error"`` with the reason.
``team_exists``
    ``club_code -> bool`` against the live store, for widget validation. A club code the store has
    no row for is ``False``; the registry treats a *failure* to check as "cannot say", not "no".

Registration happens when the EuroLeague router is imported (see ``api/routes.py``), and that
happens only when the EuroLeague is enabled, so a disabled EuroLeague is simply never registered
and the registry reports it as unavailable. :func:`unregister` and the registry's own ``clear``
exist for tests.
"""

from __future__ import annotations

from typing import Any, Final

from sqlalchemy import select

from ..shared import league_registry
from ..shared.league_profile import EUROLEAGUE_KEY
from .bootstrap import get_state
from .db import get_el_sessionmaker, read_sync_state
from .models import ElClub, ElSeason
from .profile import normalise_club_code

__all__ = ["FEATURES", "session_factory", "describe", "team_exists", "register", "unregister"]

#: What the EuroLeague can do, by the names a client keys on.
FEATURES: Final[tuple[str, ...]] = (
    "matchup",
    "defenseByPosition",
    "projections",
    "availability",
    "news",
    "scorers",
    "ratings",
    "boxScores",
    "playerStats",
)


def session_factory() -> Any:
    """A new session on the EuroLeague store; raises while the league is not ready."""
    state = get_state()
    if not state.ready:
        raise RuntimeError(state.reason or f"the EuroLeague is {state.state}")
    return get_el_sessionmaker()()


def describe() -> dict[str, Any]:
    """The EuroLeague's row of ``GET /v1/leagues``."""
    state = get_state()
    row: dict[str, Any] = {
        "enabled": state.ready,
        "state": state.state,
        "reason": state.reason,
        "isDemo": state.is_demo,
        "currentSeason": None,
        "syncVersion": None,
        "dataThrough": None,
        "features": list(FEATURES) if state.ready else [],
    }
    if not state.ready:
        return row
    try:
        with get_el_sessionmaker()() as session:
            seasons = (
                session.execute(select(ElSeason).order_by(ElSeason.start_year.desc()))
                .scalars()
                .all()
            )
            current = next((s for s in seasons if s.is_current), seasons[0] if seasons else None)
            sync = read_sync_state(session)
            row["currentSeason"] = current.label if current is not None else None
            row["syncVersion"] = sync.sync_version or 0
            row["dataThrough"] = sync.data_through.isoformat() if sync.data_through else None
    except Exception as exc:  # noqa: BLE001 - describe must never raise
        row.update(
            enabled=False, state="error", reason=f"The store could not be read: {exc}", features=[]
        )
    return row


def team_exists(club_code: str) -> bool:
    """Whether the store has a club with this code (for validating a widget's ``club`` field)."""
    code = normalise_club_code(club_code)
    if code is None:
        return False
    with session_factory() as session:
        return session.get(ElClub, code) is not None


def register() -> None:
    """Register (or re-register) the EuroLeague with the league registry."""
    league_registry.register(
        league_registry.LeagueProvider(
            key=EUROLEAGUE_KEY,
            session_factory=session_factory,
            describe=describe,
            team_exists=team_exists,
        )
    )


def unregister() -> None:
    league_registry.unregister(EUROLEAGUE_KEY)
