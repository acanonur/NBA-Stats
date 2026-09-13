"""The widget layer: one resolver per kind in ``contracts/widgets.json``.

Every resolver has the same signature::

    resolve(config: dict, ctx: ResolveContext) -> tuple[payload, availability, notes]

``config`` has already been cleaned by :func:`nbastats.catalog.validate_widget_config`, so a
resolver may read a key without checking it exists. ``payload`` is a plain JSON-ready dict
matching ``contracts/CONTRACT.md`` §4 exactly — the iOS client decodes each one into a fixed
Swift struct, so a renamed key is a crash on a device, not a cosmetic difference.
``availability`` is one of §6's four values, and ``notes`` is what turns a result's status
into ``"partial"``.

:data:`RESOLVERS` is asserted against the catalog at **import time**. A kind added to
``contracts/widgets.json`` without a resolver — or a resolver without a catalog entry — stops
the process from starting rather than producing a 500 the first time somebody's dashboard
happens to contain that tile. Same for :data:`TTL_SECONDS`, which has to agree with the
catalog's ``minRefreshSeconds`` and with ``contracts/CONTRACT.md`` §8.
"""
from __future__ import annotations

from typing import Any, Protocol

from .. import catalog
from . import (
    career_arc,
    comparison,
    daily_movers,
    four_factors,
    game_log,
    leaderboard,
    player_snapshot,
    scoreboard,
    shot_profile,
    stat_tile,
    team_efficiency,
    trend_chart,
)
from .base import ResolveContext, WidgetError

__all__ = [
    "RESOLVERS",
    "TTL_SECONDS",
    "MAX_WIDGETS_PER_REQUEST",
    "Resolver",
    "ResolveContext",
    "WidgetError",
    "resolver_for",
    "ttl_for",
]


class Resolver(Protocol):
    """What every widget module's ``resolve`` looks like."""

    def __call__(
        self, config: dict[str, Any], ctx: ResolveContext
    ) -> tuple[dict[str, Any], str, list[str]]:  # pragma: no cover - typing only
        ...


#: The contract's cap on one ``POST /v1/dashboard/resolve`` (§3).
MAX_WIDGETS_PER_REQUEST = 24

#: ``kind`` -> the function that turns a config into a payload.
RESOLVERS: dict[str, Resolver] = {
    "stat_tile": stat_tile.resolve,
    "player_snapshot": player_snapshot.resolve,
    "leaderboard": leaderboard.resolve,
    "game_log": game_log.resolve,
    "trend_chart": trend_chart.resolve,
    "four_factors": four_factors.resolve,
    "shot_profile": shot_profile.resolve,
    "comparison": comparison.resolve,
    "scoreboard": scoreboard.resolve,
    "daily_movers": daily_movers.resolve,
    "team_efficiency": team_efficiency.resolve,
    "career_arc": career_arc.resolve,
}

#: How long a payload of each kind stays good for (``contracts/CONTRACT.md`` §8):
#: 60s for the live slate, 300s for player-level tiles, 600s for leaderboards and team
#: tables, 3600s for a career that only changes once a season.
TTL_SECONDS: dict[str, int] = {
    "scoreboard": 60,
    "daily_movers": 60,
    "stat_tile": 300,
    "player_snapshot": 300,
    "game_log": 300,
    "trend_chart": 300,
    "leaderboard": 600,
    "four_factors": 600,
    "shot_profile": 600,
    "comparison": 600,
    "team_efficiency": 600,
    "career_arc": 3600,
}


def _assert_registry_matches_catalog() -> None:
    """Fail at import, not at request time, if the registry and the catalog disagree."""
    catalog_kinds = set(catalog.widget_kinds())
    registered = set(RESOLVERS)
    missing = sorted(catalog_kinds - registered)
    extra = sorted(registered - catalog_kinds)
    if missing or extra:
        raise RuntimeError(
            "nbastats.widgets.RESOLVERS does not match contracts/widgets.json: "
            f"missing resolvers for {missing}, unknown kinds {extra}"
        )

    ttl_gaps = sorted(catalog_kinds - set(TTL_SECONDS))
    if ttl_gaps:
        raise RuntimeError(f"nbastats.widgets.TTL_SECONDS has no entry for {ttl_gaps}")

    disagreements = {
        kind: (TTL_SECONDS[kind], catalog.widget(kind).get("minRefreshSeconds"))
        for kind in sorted(catalog_kinds)
        if catalog.widget(kind).get("minRefreshSeconds") not in (None, TTL_SECONDS[kind])
    }
    if disagreements:
        raise RuntimeError(
            "nbastats.widgets.TTL_SECONDS disagrees with the widget catalog's "
            f"minRefreshSeconds: {disagreements}"
        )


_assert_registry_matches_catalog()


def resolver_for(kind: str) -> Resolver:
    """The resolver for one widget kind.

    Raises :class:`WidgetError` ``invalid_config`` for a kind the catalog does not know —
    which is what a layout written by a newer client looks like, and is one tile's problem
    rather than the request's.
    """
    try:
        return RESOLVERS[kind]
    except KeyError as exc:
        raise WidgetError(
            "invalid_config",
            f"{kind!r} is not a widget kind this server knows about.",
            field="kind",
        ) from exc


def ttl_for(kind: str) -> int:
    """``ttlSeconds`` for one kind, defaulting to the player-level 300s."""
    return TTL_SECONDS.get(kind, 300)
