"""The NBA model's constants: an allowlist, their defaults, and what makes a value acceptable.

Why this is a table of named constants and not a free-form settings bag
-----------------------------------------------------------------------
Every number the team-score model leans on is either a measured quantity or a default somebody
chose, and the difference has to stay visible: a user reading "home advantage 2.5" should be able
to see that it is a *default* and not something fitted to games. Two rules keep that true.

1. **Only the allowlisted keys exist** (:data:`~nbastats.nba_intel.models.MODEL_SETTING_KEYS`,
   the design's §8.6). The database's CHECK refuses any other key, and :func:`validate_setting`
   refuses it first, with a message. The list has no key for a total spread, a player spread, a
   threshold on a probability, or any number whose only use would be to compare a projection with
   an outside price, so there is nowhere to put one.
2. **A row means "set", its absence means "default"** (:func:`get_all`). The defaults are
   not written into the table, so a changed default in a later release reaches everyone who never
   changed it, and ``is_default`` on the wire is true exactly when nothing has been set.
   ``provenance`` records how a stored value got there: ``manual`` (a person set it),
   ``fittedPrevSeason`` and ``fittedLedger`` (a calibration job did).

Two keys, ``teamSd`` and ``marginSd``, have **no default at all**. They are the spread of a team's
score and of a margin, fitted from walk-forward residuals by ``projections.calibrate``; until that
has run there is nothing honest to put there, so :func:`get_all` returns ``None`` and the
projection shows an em dash. The EuroLeague's 9.5 and 11.5 are *its* workbook's numbers and are
never borrowed for the NBA.

Where the defaults come from
----------------------------
The status-to-chance table and the home-advantage default are read from the shared league profile
(:mod:`nbastats.shared.league_profile`), so the numbers a profile states and the numbers this table
falls back to cannot disagree. The rest are the design's stated defaults (§8.4), each labelled a
default in the payloads that carry them: the NBA team model was not fitted to NBA data when it was
written.

Validation
----------
Values must be finite numbers (a ``bool`` is not a number here) inside the range that makes sense
for the constant. The five ``statusChance`` values must also be non-decreasing in the order out,
doubtful, questionable, probable, available, checked against the table that would result from the
change, so one edit cannot make "questionable" likelier than "probable". A patch is validated as
a whole and applied atomically: either every key in it lands or none does.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Final, Mapping

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..shared.league_profile import NBA, STATUS_ORDER
from .models import MODEL_SETTING_KEYS, SETTING_PROVENANCES, NbaIntelModelSetting

__all__ = [
    "ALLOWED_KEYS",
    "DEFAULTS",
    "NO_DEFAULT_KEYS",
    "InvalidSettingError",
    "SettingValue",
    "validate_setting",
    "get_all",
    "get_value",
    "chance_table",
    "set_setting",
    "apply_patch",
    "reset_setting",
]

ALLOWED_KEYS: Final[tuple[str, ...]] = MODEL_SETTING_KEYS

_DEFAULTS: dict[str, float] = {
    "priorRegression": 0.3,
    "priorWeightGames": 10.0,
    "leagueLevelWeight": 150.0,
    "homeAdvantagePoints": NBA.home_advantage_default,
    "replacementShare": 0.52,
    "absorbShare": 0.6,
    "boostCap": 1.35,
    "capPolicyConsistent": 1.0,
    "positionCoverageCeiling": NBA.defense.unknown_ceiling,
}
for _status, _chance in NBA.status_chance:
    _DEFAULTS[f"statusChance.{_status}"] = _chance

#: Key to default value. ``teamSd`` and ``marginSd`` are deliberately absent: see the docstring.
DEFAULTS: Final[Mapping[str, float]] = MappingProxyType(_DEFAULTS)
NO_DEFAULT_KEYS: Final[frozenset[str]] = frozenset(set(ALLOWED_KEYS) - set(_DEFAULTS))

# key -> (lowest, highest, lowest_inclusive)
_RANGES: Final[dict[str, tuple[float, float, bool]]] = {
    "priorRegression": (0.0, 1.0, True),
    "priorWeightGames": (0.0, 1000.0, False),
    "leagueLevelWeight": (0.0, 10_000.0, True),
    "homeAdvantagePoints": (0.0, 10.0, True),
    "teamSd": (0.0, 40.0, False),
    "marginSd": (0.0, 40.0, False),
    "replacementShare": (0.0, 1.0, True),
    "absorbShare": (0.0, 1.0, True),
    "boostCap": (1.0, 3.0, True),
    "positionCoverageCeiling": (0.0, 1.0, True),
}
for _status in STATUS_ORDER:
    _RANGES[f"statusChance.{_status}"] = (0.0, 1.0, True)

_BINARY_KEYS: Final[frozenset[str]] = frozenset({"capPolicyConsistent"})


class InvalidSettingError(ValueError):
    """A key that is not allowlisted, or a value that is not acceptable for its key."""


@dataclass(frozen=True, slots=True)
class SettingValue:
    """One setting as the API reports it. ``value`` is ``None`` only for a key with no default
    that has not been set (``teamSd`` and ``marginSd`` before calibration)."""

    key: str
    value: float | None
    provenance: str
    is_default: bool
    set_at: datetime | None


def validate_setting(key: str, value: Any) -> float:
    """The value as a float, or :class:`InvalidSettingError` saying what is wrong with it."""
    if key not in ALLOWED_KEYS:
        raise InvalidSettingError(f"{key!r} is not a model setting")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidSettingError(f"{key} must be a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise InvalidSettingError(f"{key} must be a finite number, got {value!r}")
    if key in _BINARY_KEYS:
        if number not in (0.0, 1.0):
            raise InvalidSettingError(f"{key} must be 0 or 1, got {value!r}")
        return number
    low, high, low_inclusive = _RANGES[key]
    below = number < low or (number == low and not low_inclusive)
    if below or number > high:
        bracket = "[" if low_inclusive else "("
        raise InvalidSettingError(
            f"{key} must be within {bracket}{low:g}, {high:g}], got {value!r}"
        )
    return number


def _check_chance_order(table: Mapping[str, float]) -> None:
    previous: tuple[str, float] | None = None
    for status in STATUS_ORDER:
        chance = table[f"statusChance.{status}"]
        if previous is not None and chance < previous[1]:
            raise InvalidSettingError(
                f"statusChance.{status} ({chance:g}) cannot be lower than "
                f"statusChance.{previous[0]} ({previous[1]:g})"
            )
        previous = (status, chance)


def _stored(session: Session) -> dict[str, NbaIntelModelSetting]:
    rows = session.execute(select(NbaIntelModelSetting)).scalars().all()
    return {row.key: row for row in rows}


def get_all(session: Session) -> list[SettingValue]:
    """Every allowlisted key in allowlist order, stored values merged over the defaults."""
    stored = _stored(session)
    out: list[SettingValue] = []
    for key in ALLOWED_KEYS:
        row = stored.get(key)
        if row is not None:
            out.append(SettingValue(key, row.value, row.provenance, False, row.set_at))
        else:
            out.append(SettingValue(key, DEFAULTS.get(key), "default", True, None))
    return out


def get_value(session: Session, key: str) -> float | None:
    """The effective value of one key: the stored one, else the default, else ``None``."""
    if key not in ALLOWED_KEYS:
        raise InvalidSettingError(f"{key!r} is not a model setting")
    row = session.get(NbaIntelModelSetting, key)
    return row.value if row is not None else DEFAULTS.get(key)


def chance_table(session: Session) -> dict[str, float]:
    """The five-status chance table in the shape :mod:`nbastats.shared.availability` takes."""
    stored = _stored(session)
    out: dict[str, float] = {}
    for status in STATUS_ORDER:
        key = f"statusChance.{status}"
        out[status] = stored[key].value if key in stored else DEFAULTS[key]
    return out


def apply_patch(
    session: Session,
    patch: Mapping[str, Any],
    *,
    provenance: str = "manual",
    now: datetime | None = None,
) -> list[SettingValue]:
    """Validate every change, then apply them all or none. Returns the changed settings.

    The caller commits. Raises :class:`InvalidSettingError` for an unknown key, a bad value, a
    bad provenance, or a status-chance table that would no longer be non-decreasing.
    """
    if provenance not in SETTING_PROVENANCES or provenance == "default":
        raise InvalidSettingError(f"{provenance!r} is not a provenance a setting can be set with")
    if not patch:
        return []
    cleaned = {key: validate_setting(key, value) for key, value in patch.items()}

    merged = {s.key: s.value for s in get_all(session)}
    merged.update(cleaned)
    if any(key.startswith("statusChance.") for key in cleaned):
        chances = {k: v for k, v in merged.items() if k.startswith("statusChance.")}
        _check_chance_order(chances)  # type: ignore[arg-type]

    stamp = now or utcnow()
    out: list[SettingValue] = []
    for key, number in cleaned.items():
        row = session.get(NbaIntelModelSetting, key)
        if row is None:
            row = NbaIntelModelSetting(key=key, value=number, provenance=provenance, set_at=stamp)
            session.add(row)
        else:
            row.value, row.provenance, row.set_at = number, provenance, stamp
        out.append(SettingValue(key, number, provenance, False, stamp))
    session.flush()
    return out


def set_setting(
    session: Session,
    key: str,
    value: Any,
    *,
    provenance: str = "manual",
    now: datetime | None = None,
) -> SettingValue:
    """Set one key; a thin wrapper over :func:`apply_patch`."""
    return apply_patch(session, {key: value}, provenance=provenance, now=now)[0]


def reset_setting(session: Session, key: str) -> bool:
    """Forget a stored value so the default applies again. True if a row was removed."""
    if key not in ALLOWED_KEYS:
        raise InvalidSettingError(f"{key!r} is not a model setting")
    removed = session.execute(
        delete(NbaIntelModelSetting).where(NbaIntelModelSetting.key == key)
    ).rowcount
    return bool(removed)
