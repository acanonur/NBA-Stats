"""The model's settings: every allowlisted constant, where its value came from, how to change one.

``GET /v1/model-settings`` lists the sixteen constants the NBA team model and its availability
layer lean on, each with the value in force, its provenance and whether it is still the default,
so a screen can show which numbers are Hardwood's defaults, which were fitted to games and which
the user has changed. ``PATCH /v1/model-settings`` changes some of them.

The allowlist is the point
--------------------------
The keys are exactly the design's section 8.6 list and nothing else
(:data:`nbastats.nba_intel.models.MODEL_SETTING_KEYS`, enforced again by a database CHECK). There
is no key for any number that would exist only to be compared with an outside figure, so there is
nowhere to put one: a ``PATCH`` naming any other key is ``400 bad_request`` with that key in
``field``. The write has no provenance field either, because a value written here is ``manual`` by
definition; ``fittedPrevSeason`` and ``fittedLedger`` are written only by the calibration job.

Validation and atomicity
------------------------
Every value must be a finite number inside the range that makes sense for its constant, and the
five ``statusChance`` values must stay non-decreasing from ``out`` to ``available`` (checked against
the table that would result). The patch is validated as a whole and applied atomically: either every
key in it lands or none does, and the route commits only on success.

Two constants, ``teamSd`` and ``marginSd``, have **no default** until a calibration has run: their
value is ``null`` (a dash) and the projection shows no range.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Sequence

from ..nba_intel import settings as intel_settings
from ..shared import refs
from .queries import ReadContext, bad_request, naive_utc

__all__ = ["DESCRIPTIONS", "model_settings_payload", "apply_settings_patch"]

DESCRIPTIONS: Final[dict[str, str]] = {
    "priorRegression": (
        "How far last season's scoring is pulled back toward the league average: 0 keeps it as it "
        "was, 1 makes every team average. A default, not fitted."
    ),
    "priorWeightGames": (
        "How slowly a team's in-season adjustment moves: each game moves it by 1 divided by (games "
        "played plus this number). Also the weight, in games, last season's per-game values carry "
        "in a player's blend. A default."
    ),
    "leagueLevelWeight": (
        "How many team-games of weight last season's league scoring level carries when blended "
        "with this season's. A default."
    ),
    "homeAdvantagePoints": (
        "Points a home team is worth. Replaced by the mean home margin once 300 games are final, "
        "unless set by hand. A default until then."
    ),
    "teamSd": (
        "The spread of one team's score around its projection, in points. Fitted from results; it "
        "has no default, and no range is shown until it exists."
    ),
    "marginSd": (
        "The spread of the projected margin around the result, in points. Fitted from results; it "
        "has no default, and no range is shown until it exists."
    ),
    "replacementShare": (
        "A replacement player's scoring as a share of the league's points per minute. A default."
    ),
    "absorbShare": (
        "The share of the scoring missing above replacement level that teammates recover. A "
        "default."
    ),
    "boostCap": (
        "The most a teammate's scoring is raised when others are out (1.35 is 35% more). A default."
    ),
    "capPolicyConsistent": (
        "1 keeps a team's projected score equal to the sum of its players' (plus a "
        "replacement-level line for minutes the capped players cannot fill) when the cap binds; "
        "0 reproduces the workbook's own arithmetic, where the two can differ."
    ),
    "positionCoverageCeiling": (
        "The largest share of a team's (or the league's) points allowed that may go to players "
        "with no listed position before defence-by-position indices are withheld."
    ),
}
for _status in ("out", "doubtful", "questionable", "probable", "available"):
    DESCRIPTIONS[f"statusChance.{_status}"] = (
        f"The chance of playing counted for a player listed as {_status}. A default."
    )


def model_settings_payload(ctx: ReadContext, freshness: dict[str, Any]) -> dict[str, Any]:
    """Every allowlisted setting with its value in force and where that value came from."""
    return {
        "league": "nba",
        "freshness": freshness,
        "settings": [
            {
                "key": s.key,
                "value": s.value,
                "provenance": s.provenance,
                "isDefault": s.is_default,
                "setAt": refs.rfc3339(s.set_at),
                "description": DESCRIPTIONS.get(s.key, ""),
            }
            for s in intel_settings.get_all(ctx.session)
        ],
    }


def apply_settings_patch(
    ctx: ReadContext, items: Sequence[tuple[str, Any]], now: datetime
) -> list[intel_settings.SettingValue]:
    """Validate ``(key, value)`` pairs as a whole, then apply them all or none. Does not commit.

    ``400 bad_request`` names the offending ``key`` or ``value``; nothing is changed when any pair
    is refused, including a status-chance table that would no longer be non-decreasing.
    """
    if not items:
        raise bad_request("Send at least one setting.", "settings")
    patch: dict[str, Any] = {}
    for key, value in items:
        if key in patch:
            raise bad_request(f"{key!r} appears twice in the patch.", "key")
        try:
            intel_settings.validate_setting(key, value)
        except intel_settings.InvalidSettingError as exc:
            raise bad_request(
                str(exc), "key" if "is not a model setting" in str(exc) else "value"
            ) from exc
        patch[key] = value
    try:
        return intel_settings.apply_patch(
            ctx.session, patch, provenance="manual", now=naive_utc(now)
        )
    except intel_settings.InvalidSettingError as exc:  # e.g. an inconsistent set of status chances
        raise bad_request(str(exc), "value") from exc
