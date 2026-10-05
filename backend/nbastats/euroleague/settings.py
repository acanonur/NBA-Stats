"""EuroLeague model settings: the allowlisted keys, their defaults, and how they are changed.

The projection model is a set of constants (how much last season is pulled toward the mean,
what home court is worth, how much of a missing scorer's output teammates absorb). Each one is
a row in ``el_model_setting`` when it has been set, and a default here when it has not. Every
constant is shown by ``GET /v1/el/method`` and ``/v1/el/model-settings`` with its *provenance*,
because a number the user typed into a workbook, a number Hardwood fitted from results and a
number nobody has checked are three different kinds of number and the screen must not blur them.

Provenance
----------
``workbook``             taken from the user's workbook
``workbookUnvalidated``  taken from the workbook and never checked against results; the two
                         standard deviations carry this, and an interval built from them says
                         ``intervalBasis: "assumed"`` until enough locked games exist to fit them
``default``              Hardwood's own starting value, chosen to match the workbook where the
                         workbook has one
``fittedLedger``         fitted from the projection ledger's residuals
``manual``               entered by the user through ``PATCH /v1/el/model-settings``

A re-import never overwrites a ``manual`` or ``fittedLedger`` value: the user's edit and the
fitted value are worth more than a number copied from a spreadsheet a second time.

What is deliberately not here
-----------------------------
The key set is exactly the allowlist of design section 8.6, mirrored by a CHECK constraint in
``models.py``. There is no key for the workbook's ``TotalSD``, ``PSDBase``, ``PSDSlope`` or
``EdgeP``, nor for a line or a probability threshold: those exist only to feed a probability
probability and a lean, and Hardwood does not compute either. A key outside the allowlist is
rejected here with :class:`InvalidSettingError` before the database could reject it.

Validation
----------
Every value is checked against a range that makes sense for its meaning (a share is in [0, 1],
the cap on a teammate's boost is at least 1, a chance of playing is in [0, 1], a standard
deviation is positive), so a bad edit is refused at entry rather than producing a probability
above one or a negative spread in a projection. Booleans are 1 or 0.

The default round weight ``roundWeight.n = 1 / (n + 9)`` reproduces the workbook's first two
weights (0.10 and about 0.09) and continues past its table; a projection that uses a weight
beyond what the workbook states says it was extrapolated.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Final, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..shared.availability import STATUSES, validate_status_chance
from .db import utcnow
from .models import MODEL_SETTING_KEYS, SETTING_PROVENANCES, ElModelSetting
from .profile import PROFILE

__all__ = [
    "ALLOWED_KEYS",
    "ALLOWED_SETTING_KEYS",
    "PROVENANCES",
    "PROTECTED_PROVENANCES",
    "SettingDefault",
    "DEFAULTS",
    "EffectiveSetting",
    "InvalidSettingError",
    "is_allowed_key",
    "validate_setting",
    "effective_settings",
    "setting_value",
    "status_chance_table",
    "set_setting",
    "apply_imported_setting",
    "settings_digest",
]

ALLOWED_KEYS: Final[frozenset[str]] = frozenset(MODEL_SETTING_KEYS)
#: The same set under the name the design and the guard tests use.
ALLOWED_SETTING_KEYS: Final[frozenset[str]] = ALLOWED_KEYS
PROVENANCES: Final[tuple[str, ...]] = SETTING_PROVENANCES
#: Provenances a workbook re-import must not overwrite.
PROTECTED_PROVENANCES: Final[frozenset[str]] = frozenset({"manual", "fittedLedger"})


class InvalidSettingError(ValueError):
    """A key outside the allowlist, a provenance outside the vocabulary, or a bad value."""


@dataclass(frozen=True)
class SettingDefault:
    value: float
    provenance: str
    #: Inclusive bounds, ``None`` for unbounded on that side.
    low: float | None
    high: float | None
    #: ``True`` for a 1-or-0 switch.
    boolean: bool
    description: str


def _spec(
    value: float,
    description: str,
    *,
    low: float | None = None,
    high: float | None = None,
    provenance: str = "default",
    boolean: bool = False,
) -> SettingDefault:
    return SettingDefault(value, provenance, low, high, boolean, description)


def _build_defaults() -> dict[str, SettingDefault]:
    table: dict[str, SettingDefault] = {
        "priorRegression": _spec(
            0.3,
            "How far last season's scoring is pulled back toward the league mean "
            "(0 = as-is, 1 = every club average).",
            low=0.0,
            high=1.0,
        ),
        "homeAdvantagePoints": _spec(
            PROFILE.home_advantage_default,
            "Points of home-court advantage: half to the home side, half off the away side.",
            low=0.0,
            high=20.0,
        ),
        "teamSd": _spec(
            9.5,
            "Spread of one team's score in one game. Assumed by the workbook, not yet checked "
            "against results.",
            low=0.1,
            high=50.0,
            provenance="workbookUnvalidated",
        ),
        "marginSd": _spec(
            11.5,
            "Spread of the winning margin. Assumed by the workbook, not yet checked against "
            "results.",
            low=0.1,
            high=50.0,
            provenance="workbookUnvalidated",
        ),
        "replacementPer40": _spec(
            9.0,
            "Points per 40 minutes at replacement level; a missing player's minutes are "
            "first refilled at this rate.",
            low=0.0,
            high=40.0,
        ),
        "absorbShare": _spec(
            0.6,
            "Share of a missing player's scoring above replacement that teammates recover.",
            low=0.0,
            high=1.0,
        ),
        "boostCap": _spec(
            1.35,
            "Largest multiple of a player's output that teammates' absences can produce.",
            low=1.0,
            high=3.0,
        ),
        "rotationShare": _spec(
            0.5,
            "Share of lost minutes taken by the listed rotation (minutes display only).",
            low=0.0,
            high=1.0,
        ),
        "formWeight": _spec(
            0.25,
            "Weight of a scorer's recent average against the model in his round projection.",
            low=0.0,
            high=1.0,
        ),
        "capPolicyConsistent": _spec(
            1.0,
            "1: team and player projections agree (the boost cap binds the players; what they "
            "cannot take is credited at replacement level). "
            "0: the workbook's behaviour, which can leave points unassigned.",
            boolean=True,
        ),
        "squadReconcile": _spec(
            1.0,
            "1: scale each squad so its players' points sum to the club's projected score.",
            boolean=True,
        ),
        "overtimeScaling": _spec(
            1.0,
            "1: scale actual scores to regulation length before updating ratings (a "
            "documented deviation; the workbook did not).",
            boolean=True,
        ),
        "positionCoverageCeiling": _spec(
            PROFILE.defense.unknown_ceiling,
            "Most points a defence may allow to unlisted positions before every index is "
            "withheld.",
            low=0.0,
            high=1.0,
        ),
    }
    for n in range(1, 41):
        table[f"roundWeight.{n}"] = _spec(
            1.0 / (n + 9),
            f"Weight of one game's miss in the rating update for a club's game number {n}.",
            low=0.0,
            high=1.0,
        )
    chances = PROFILE.chance_table()
    for status in STATUSES:
        table[f"statusChance.{status}"] = _spec(
            chances[status],
            f"Chance a player with status '{status}' plays.",
            low=0.0,
            high=1.0,
        )
    missing = ALLOWED_KEYS - set(table)
    extra = set(table) - ALLOWED_KEYS
    if missing or extra:  # pragma: no cover - import-time sanity
        raise RuntimeError(f"settings defaults disagree with the allowlist: {missing} {extra}")
    return table


#: Default for every allowed key, in allowlist order.
DEFAULTS: Final[Mapping[str, SettingDefault]] = MappingProxyType(
    {key: spec for key, spec in _build_defaults().items()}
)


@dataclass(frozen=True)
class EffectiveSetting:
    key: str
    value: float
    provenance: str
    is_default: bool
    set_at: datetime | None
    description: str


def is_allowed_key(key: Any) -> bool:
    return isinstance(key, str) and key in ALLOWED_KEYS


def validate_setting(key: str, value: Any) -> float:
    """The value as a float, or :class:`InvalidSettingError` saying what is wrong."""
    if not is_allowed_key(key):
        raise InvalidSettingError(f"{key!r} is not a model setting")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidSettingError(f"{key} must be a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise InvalidSettingError(f"{key} must be finite, got {value!r}")
    spec = DEFAULTS[key]
    if spec.boolean:
        if number not in (0.0, 1.0):
            raise InvalidSettingError(f"{key} is a switch: 1 or 0, got {value!r}")
        return number
    if spec.low is not None and number < spec.low:
        raise InvalidSettingError(f"{key} must be at least {spec.low}, got {value!r}")
    if spec.high is not None and number > spec.high:
        raise InvalidSettingError(f"{key} must be at most {spec.high}, got {value!r}")
    return number


def _check_provenance(provenance: str) -> None:
    if provenance not in PROVENANCES:
        raise InvalidSettingError(f"provenance {provenance!r} is not one of {PROVENANCES}")


def effective_settings(session: Session) -> dict[str, EffectiveSetting]:
    """Every allowed key with the value in force: the stored row, else the default."""
    rows = {row.key: row for row in session.execute(select(ElModelSetting)).scalars()}
    out: dict[str, EffectiveSetting] = {}
    for key in MODEL_SETTING_KEYS:
        spec = DEFAULTS[key]
        row = rows.get(key)
        if row is None:
            out[key] = EffectiveSetting(
                key, spec.value, spec.provenance, True, None, spec.description
            )
        else:
            out[key] = EffectiveSetting(
                key,
                row.value,
                row.provenance,
                row.provenance == "default",
                row.set_at,
                spec.description,
            )
    return out


def setting_value(session: Session, key: str) -> float:
    """The value in force for one key."""
    if not is_allowed_key(key):
        raise InvalidSettingError(f"{key!r} is not a model setting")
    row = session.get(ElModelSetting, key)
    return row.value if row is not None else DEFAULTS[key].value


def status_chance_table(session: Session) -> dict[str, float]:
    """The ``statusChance.*`` settings as ``{status: chance}``, validated as a whole."""
    effective = effective_settings(session)
    return validate_status_chance(
        {status: effective[f"statusChance.{status}"].value for status in STATUSES}
    )


def set_setting(
    session: Session,
    key: str,
    value: Any,
    provenance: str = "manual",
    now: datetime | None = None,
) -> ElModelSetting:
    """Validate and store one setting, replacing any earlier row. Does not commit.

    The status chances are also checked as a table after the change, so editing one of the five
    cannot leave the set inconsistent.
    """
    _check_provenance(provenance)
    number = validate_setting(key, value)
    row = session.get(ElModelSetting, key)
    if row is None:
        row = ElModelSetting(key=key, value=number, provenance=provenance, set_at=now or utcnow())
        session.add(row)
    else:
        row.value = number
        row.provenance = provenance
        row.set_at = now or utcnow()
    session.flush()
    if key.startswith("statusChance."):
        status_chance_table(session)
    return row


def apply_imported_setting(
    session: Session,
    key: str,
    value: Any,
    provenance: str,
    now: datetime | None = None,
) -> bool:
    """Store a value read from a workbook unless a protected row already holds the key.

    Returns ``True`` when a row was written. A ``manual`` or ``fittedLedger`` row is kept, and a
    value identical to the stored one is not rewritten (so a re-import changes nothing).
    """
    _check_provenance(provenance)
    number = validate_setting(key, value)
    row = session.get(ElModelSetting, key)
    if row is not None:
        if row.provenance in PROTECTED_PROVENANCES:
            return False
        if row.value == number and row.provenance == provenance:
            return False
    set_setting(session, key, number, provenance, now)
    return True


def settings_digest(session: Session) -> str:
    """SHA-256 of the settings in force, stable across runs: stored on a ledger row so a
    projection can be tied to the exact constants that produced it."""
    effective = effective_settings(session)
    canonical = json.dumps(
        {key: [effective[key].value, effective[key].provenance] for key in sorted(effective)},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
