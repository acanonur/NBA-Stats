"""Position normalisers: turn whatever a source calls a position into bucket weights.

Defence by opponent position (:mod:`nbastats.shared.defense_position`) asks one question of
every opposing player: *which bucket do his points belong to?* This module answers it, from
the several vocabularies the sources actually use, and answers honestly when it cannot: a
position it does not recognise is :data:`UNKNOWN`, never a guess. Unknown points are kept,
counted and capped (a coverage gate withholds every index when too many points have no
bucket), which is the only way a coverage bias stays visible instead of silently moving the
numbers.

Weights
-------
A player's position is a mapping of bucket to weight summing to exactly one: ``{"G": 1.0}``
for a guard, ``{"G": 0.5, "F": 0.5}`` for a guard-forward. Weights are multiples of one half
on purpose. They are exact in binary floating point, so the per-game allocation of points
reconciles to the opponent's score with no rounding slack to hide a bug in. One basis per
player-season, never per game: a player does not change bucket from one night to the next.

The vocabularies
----------------
* **NBA** (``player_position_season``): ``G``, ``PG``, ``SG``, ``Guard`` are guards;
  ``F``, ``SF``, ``PF``, ``Forward`` are forwards; ``C``, ``Center``, ``Centre`` are centers;
  ``G-F`` and ``F-G`` (and the long spellings) split guard and forward evenly, ``F-C`` and
  ``C-F`` split forward and center evenly. Anything else is unknown. The table is deliberately
  closed: ``PG-SG`` or ``G-C`` are not on it, so they are unknown rather than interpreted. Case,
  surrounding whitespace, the dash character and ``/`` for ``-`` are tolerated, because those
  are formatting, not meaning.
* **EuroLeague official**: the registration's position code, ``1`` guard, ``2`` forward,
  ``3`` center, from ``el_registration`` or, failing that, the box score's own embedded
  registration.
* **EuroLeague workbook**: the five labels ``PG``, ``SG``, ``SF``, ``PF``, ``C`` the workbook's
  author assigned. These are an *estimate*, used for the three-way scheme only when the store
  holds no official registrations at all, and always for the opt-in ``workbook5`` scheme.
  The league itself registers only Guard, Forward and Center, so a five-way split is a
  presentation the data cannot back up, and every payload built on it says so.

Position basis
--------------
:class:`Resolution` carries the *basis* of the answer (``listed``, ``workbookListing`` or
``unknown``). Defence by position reports, as fractions of points allowed, how much of the
total rests on each basis; that is how a reader sees at a glance how much of a number is
official and how much is the workbook author's label.

Pure and stdlib-only. Unrecognised input is logged at DEBUG, once per call, and returned as
``None``; the caller decides whether to count it.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, Mapping

__all__ = [
    "UNKNOWN",
    "GFC",
    "WORKBOOK5",
    "SCHEME_GFC",
    "SCHEME_WORKBOOK5",
    "SCHEMES",
    "BUCKET_LABELS",
    "BASIS_LISTED",
    "BASIS_WORKBOOK_LISTING",
    "BASIS_UNKNOWN",
    "BASES",
    "Weights",
    "Resolution",
    "buckets_for_scheme",
    "all_buckets",
    "bucket_label",
    "normalise_nba_position",
    "weights_from_columns",
    "euroleague_position_bucket",
    "workbook5_label",
    "workbook5_to_gfc",
    "resolve_euroleague_position",
    "is_valid_weights",
    "scheme_contract",
]

_LOG = logging.getLogger(__name__)

#: The bucket for a player whose position is not known. Spelled as on the wire.
UNKNOWN: Final = "unknown"

GFC: Final[tuple[str, ...]] = ("G", "F", "C")
WORKBOOK5: Final[tuple[str, ...]] = ("PG", "SG", "SF", "PF", "C")

SCHEME_GFC: Final = "gfc"
SCHEME_WORKBOOK5: Final = "workbook5"

#: Scheme name to its known buckets, without :data:`UNKNOWN` (which every scheme adds).
SCHEMES: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {SCHEME_GFC: GFC, SCHEME_WORKBOOK5: WORKBOOK5}
)

BUCKET_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "G": "Guards",
        "F": "Forwards",
        "C": "Centers",
        "PG": "Point guards",
        "SG": "Shooting guards",
        "SF": "Small forwards",
        "PF": "Power forwards",
        UNKNOWN: "Position unknown",
    }
)

BASIS_LISTED: Final = "listed"
BASIS_WORKBOOK_LISTING: Final = "workbookListing"
BASIS_UNKNOWN: Final = "unknown"
BASES: Final[tuple[str, ...]] = (BASIS_LISTED, BASIS_WORKBOOK_LISTING, BASIS_UNKNOWN)

#: Bucket to weight, summing to one. ``None`` (not an empty mapping) means unknown.
Weights = Mapping[str, float]

_WEIGHT_TOLERANCE: Final = 1e-9


def buckets_for_scheme(scheme: str) -> tuple[str, ...]:
    """The known buckets of ``scheme`` in display order, without :data:`UNKNOWN`."""
    try:
        return SCHEMES[scheme]
    except (KeyError, TypeError):
        raise ValueError(
            f"unknown position scheme {scheme!r}; expected one of {', '.join(SCHEMES)}"
        ) from None


def all_buckets(scheme: str) -> tuple[str, ...]:
    """The buckets of ``scheme`` followed by :data:`UNKNOWN`: every place a point can land."""
    return (*buckets_for_scheme(scheme), UNKNOWN)


def bucket_label(bucket: str) -> str:
    """Human label for a bucket id (``G`` becomes ``Guards``)."""
    try:
        return BUCKET_LABELS[bucket]
    except KeyError:
        raise ValueError(f"unknown position bucket {bucket!r}") from None


def _frozen(weights: dict[str, float]) -> Weights:
    return MappingProxyType(weights)


# ------------------------------------------------------------------------------- NBA

_DASHES = re.compile(r"[‐‑‒–—−/]")
_SPACED_DASH = re.compile(r"\s*-\s*")

_GUARD: Final = _frozen({"G": 1.0})
_FORWARD: Final = _frozen({"F": 1.0})
_CENTER: Final = _frozen({"C": 1.0})
_GUARD_FORWARD: Final = _frozen({"G": 0.5, "F": 0.5})
_FORWARD_CENTER: Final = _frozen({"F": 0.5, "C": 0.5})

# The design's table, closed: every other string is unknown. "centre" is the same word as
# "center" and is accepted wherever "center" is.
_NBA_TABLE: Final[dict[str, Weights]] = {
    "g": _GUARD,
    "pg": _GUARD,
    "sg": _GUARD,
    "guard": _GUARD,
    "f": _FORWARD,
    "sf": _FORWARD,
    "pf": _FORWARD,
    "forward": _FORWARD,
    "c": _CENTER,
    "center": _CENTER,
    "centre": _CENTER,
    "g-f": _GUARD_FORWARD,
    "f-g": _GUARD_FORWARD,
    "guard-forward": _GUARD_FORWARD,
    "forward-guard": _GUARD_FORWARD,
    "f-c": _FORWARD_CENTER,
    "c-f": _FORWARD_CENTER,
    "forward-center": _FORWARD_CENTER,
    "center-forward": _FORWARD_CENTER,
    "forward-centre": _FORWARD_CENTER,
    "centre-forward": _FORWARD_CENTER,
}


def normalise_nba_position(raw: Any) -> Weights | None:
    """Bucket weights for an NBA position string, or ``None`` (unknown) for anything else.

    ``"G-F"`` gives ``{"G": 0.5, "F": 0.5}``; ``"Center"`` gives ``{"C": 1.0}``; ``"PG-SG"``,
    ``""``, ``None`` and ``42`` all give ``None``. The result is read-only and may be shared.
    """
    if not isinstance(raw, str):
        return None
    key = _SPACED_DASH.sub("-", _DASHES.sub("-", raw.strip().lower()))
    weights = _NBA_TABLE.get(key)
    if weights is None and key:
        _LOG.debug("unrecognised NBA position %r", raw)
    return weights


def is_valid_weights(weights: Any) -> bool:
    """True for a non-empty mapping of non-negative finite weights that sum to one."""
    if not isinstance(weights, Mapping) or not weights:
        return False
    total = 0.0
    for value in weights.values():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if not math.isfinite(value) or value < 0:
            return False
        total += value
    return abs(total - 1.0) <= _WEIGHT_TOLERANCE


def weights_from_columns(g_weight: Any, f_weight: Any, c_weight: Any) -> Weights | None:
    """Weights from the three ``player_position_season`` columns, or ``None``.

    The table's rule is that either all three are NULL or they sum to one. Anything that
    breaks the rule (a partial row, a negative, a sum of 0.9) is treated as unknown rather
    than repaired, so a corrupt row can only ever cost coverage, never move a number. Zero
    weights are dropped so the result compares equal to :func:`normalise_nba_position`.
    """
    raw = {"G": g_weight, "F": f_weight, "C": c_weight}
    if any(value is None for value in raw.values()):
        return None
    candidate = {bucket: value for bucket, value in raw.items()}
    if not is_valid_weights(candidate):
        return None
    return _frozen({bucket: float(value) for bucket, value in candidate.items() if value > 0})


# ------------------------------------------------------------------------ EuroLeague

_EL_CODE_BUCKET: Final[dict[int, str]] = {1: "G", 2: "F", 3: "C"}

_WORKBOOK_TO_GFC: Final[dict[str, str]] = {
    "PG": "G",
    "SG": "G",
    "SF": "F",
    "PF": "F",
    "C": "C",
}


def euroleague_position_bucket(code: Any) -> str | None:
    """``G``, ``F`` or ``C`` for the registration position code 1, 2 or 3; else ``None``.

    Accepts the integer or its decimal string, because the data service and the store do not
    agree on the type. Booleans are not codes.
    """
    if isinstance(code, bool):
        return None
    if isinstance(code, str):
        text = code.strip()
        if not text.isdigit():
            return None
        number = int(text)
    elif isinstance(code, int):
        number = code
    elif isinstance(code, float) and code.is_integer():
        number = int(code)
    else:
        return None
    return _EL_CODE_BUCKET.get(number)


def workbook5_label(raw: Any) -> str | None:
    """Normalise a workbook position label to ``PG``, ``SG``, ``SF``, ``PF`` or ``C``."""
    if not isinstance(raw, str):
        return None
    label = raw.strip().upper()
    return label if label in _WORKBOOK_TO_GFC else None


def workbook5_to_gfc(raw: Any) -> str | None:
    """Collapse a workbook label to the three-way bucket (``PG`` and ``SG`` to ``G``)."""
    label = workbook5_label(raw)
    return _WORKBOOK_TO_GFC[label] if label is not None else None


@dataclass(frozen=True)
class Resolution:
    """One player-season's position, with where it came from."""

    weights: Weights | None
    basis: str

    @property
    def bucket(self) -> str:
        """The single bucket when the weights name exactly one; :data:`UNKNOWN` otherwise."""
        if self.weights is not None and len(self.weights) == 1:
            return next(iter(self.weights))
        return UNKNOWN


_UNRESOLVED: Final = Resolution(weights=None, basis=BASIS_UNKNOWN)


def resolve_euroleague_position(
    *,
    registration_code: Any = None,
    box_score_code: Any = None,
    workbook_label: Any = None,
    scheme: str = SCHEME_GFC,
    has_official_registrations: bool = True,
) -> Resolution:
    """One EuroLeague player's bucket for ``scheme``, with its basis.

    Three-way scheme, in order: (a) the registration's position code for the season and the
    club he played for that night; (b) the code embedded in that game's box score; (c) the
    workbook's label, *only* when the store holds no official registrations at all
    (``has_official_registrations`` is false), with basis ``workbookListing``; otherwise (d)
    unknown. The workbook label is not a fallback for an individual player the registry
    omits: mixing the two inside one store would make the estimated share depend on which
    players happened to be missing.

    ``workbook5`` scheme: only the workbook label can name a five-way bucket, so the answer
    is that label with basis ``workbookListing``, or unknown.
    """
    if scheme == SCHEME_WORKBOOK5:
        label = workbook5_label(workbook_label)
        if label is None:
            return _UNRESOLVED
        return Resolution(weights=_frozen({label: 1.0}), basis=BASIS_WORKBOOK_LISTING)
    if scheme != SCHEME_GFC:
        raise ValueError(f"unknown position scheme {scheme!r}")
    for code in (registration_code, box_score_code):
        bucket = euroleague_position_bucket(code)
        if bucket is not None:
            return Resolution(weights=_frozen({bucket: 1.0}), basis=BASIS_LISTED)
    if not has_official_registrations:
        bucket = workbook5_to_gfc(workbook_label)
        if bucket is not None:
            return Resolution(weights=_frozen({bucket: 1.0}), basis=BASIS_WORKBOOK_LISTING)
    return _UNRESOLVED


def scheme_contract() -> dict[str, Any]:
    """The position schemes as ``contracts/leagues.json`` carries them."""
    return {
        "unknownBucket": UNKNOWN,
        "schemes": {name: list(buckets) for name, buckets in SCHEMES.items()},
        "labels": dict(BUCKET_LABELS),
        "bases": list(BASES),
        "workbookToGfc": dict(_WORKBOOK_TO_GFC),
        "registrationCodes": {str(code): bucket for code, bucket in _EL_CODE_BUCKET.items()},
    }
