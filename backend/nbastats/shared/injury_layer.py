"""The injury layer: what a squad's absences do to its scoring, the workbook's way.

The EuroLeague workbook turns a list of statuses into a team effect with a small, specific
mechanism, and this module is that mechanism as a pure function. It answers one question for
one squad: given who might not play, how many points does the team lose, and how many do the
players who are left pick up?

The mechanism
-------------
Each rostered player ``j`` has expected minutes ``m``, expected points ``p`` and a chance
``c`` of playing (from the status table in :mod:`nbastats.shared.availability`). Then::

    full  = sum p                 avail    = sum p * c      lost    = sum p * (1 - c)
    lostMin = sum m * (1 - c)     availMin = sum m * c

    repl     = lostMin * q                      # q: replacement points per minute
    absorbed = repl + a * (lost - repl)   if lost >= repl else lost
    boost    = 1 if avail == 0 else min(1 + absorbed / avail, kappa)

Missing minutes are first refilled at replacement level (``q`` points per minute, the
workbook's 9 per 40). Teammates then recover a share ``a`` (0.6) of the missing scoring
*above* replacement. The recovered points are shared among those who play in proportion to
their scoring, capped at a boost of ``kappa`` (1.35) to any one player.

The one deliberate departure: the clamp
---------------------------------------
The workbook computes ``repl + a * (lost - repl)`` unconditionally. When the missing players
score *less* than replacement level would (``lost < repl``), that expression is more than the
points that were lost, so an absence **raises** the team's scoring. An absence must never help,
so ``absorbed = lost`` in that case. For every squad where ``lost >= repl`` the two agree
exactly; the transcription test checks both the agreement and the clamp.

The cap policy
--------------
The workbook applies the cap to each player's boost but not to the *team* total, so when the cap
binds (a club with four scorers out, as in the workbook's Round 3) the team projection counts points
no player is credited with, and the squad sums to less than the team says. Two policies:

``consistent`` (the default)
    ``absorbedEff = min(absorbed, (kappa - 1) * avail)``. The team and its players agree: the
    team projection equals the sum of the players' projections. ``unassigned`` is 0.
``workbook`` (replay only)
    ``absorbedEff = absorbed``: reproduce the sheet exactly. The points no player can take are
    reported as ``unassigned``.

``capBinding`` is true when the cap limits the boost (``1 + absorbed / avail > kappa``),
whichever policy is in use, so a payload can say why its numbers differ from the sheet's.

The result
----------
``squadPts = avail + absorbedEff`` and the availability factor ``af = squadPts / full`` (1 when
``full`` is 0). The team model multiplies a club's attack index by ``af``
(:func:`nbastats.shared.team_projection.apply_availability`), and that is the only way absences
reach a projection. **Defence indices are never changed by absences**: this layer has no
opinion about who defends, and says so in the limitations a payload carries.

Each player's projected points are ``p * c * boost`` and, for display only, projected minutes
``m * c * min(1 + rotationShare * lostMin / max(availMin, 1), kappa)``, where ``rotationShare``
is the portion of lost minutes the listed rotation takes (the rest goes to players outside the
list).

Chances are validated: a chance outside [0, 1], or a negative minute or point, raises
``ValueError`` at entry rather than producing a nonsense team.

:func:`scale_to_total` is the workbook's squad reconciliation: it scales a squad's
full-strength points so they sum to the club's rated scoring, so the team and its players agree
before any absence is applied.

Pure and stdlib-only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Hashable, Sequence

__all__ = [
    "CAP_CONSISTENT",
    "CAP_WORKBOOK",
    "CAP_POLICIES",
    "InjurySettings",
    "PlayerInput",
    "PlayerOutcome",
    "InjuryResult",
    "apply_injury_layer",
    "scale_to_total",
]

CAP_CONSISTENT: Final = "consistent"
CAP_WORKBOOK: Final = "workbook"
CAP_POLICIES: Final[tuple[str, ...]] = (CAP_CONSISTENT, CAP_WORKBOOK)


@dataclass(frozen=True)
class InjurySettings:
    """The five levers of the layer."""

    #: ``q``: replacement-level points per minute (the workbook's 9 per 40 is 0.225).
    replacement_per_minute: float
    #: ``a``: share of missing scoring above replacement that teammates recover (0.6).
    absorb_share: float
    #: ``kappa``: the largest boost to one player (1.35).
    boost_cap: float
    cap_policy: str = CAP_CONSISTENT
    #: Share of lost minutes taken by the listed rotation (0.5); display minutes only.
    rotation_share: float = 0.5

    def __post_init__(self) -> None:
        for name in ("replacement_per_minute", "absorb_share", "boost_cap", "rotation_share"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a number, got {value!r}")
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite, got {value!r}")
        if self.replacement_per_minute < 0:
            raise ValueError("replacement_per_minute cannot be negative")
        if not 0.0 <= self.absorb_share <= 1.0:
            raise ValueError("absorb_share must be within [0, 1]")
        if self.boost_cap < 1.0:
            raise ValueError("boost_cap must be at least 1")
        if not 0.0 <= self.rotation_share <= 1.0:
            raise ValueError("rotation_share must be within [0, 1]")
        if self.cap_policy not in CAP_POLICIES:
            raise ValueError(f"cap_policy must be one of {', '.join(CAP_POLICIES)}")

    @classmethod
    def from_per40(
        cls,
        replacement_per_40: float,
        absorb_share: float,
        boost_cap: float,
        *,
        cap_policy: str = CAP_CONSISTENT,
        rotation_share: float = 0.5,
    ) -> InjurySettings:
        """Settings from the workbook's units: replacement points per 40 minutes."""
        return cls(
            replacement_per_minute=replacement_per_40 / 40,
            absorb_share=absorb_share,
            boost_cap=boost_cap,
            cap_policy=cap_policy,
            rotation_share=rotation_share,
        )


@dataclass(frozen=True)
class PlayerInput:
    """One rostered player going into the layer."""

    key: Hashable
    #: ``m``: expected minutes in a game.
    minutes: float
    #: ``p``: expected points in a game.
    points: float
    #: ``c``: chance of playing, in [0, 1].
    chance: float

    def __post_init__(self) -> None:
        for name in ("minutes", "points", "chance"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"player {self.key!r}: {name} must be a number, got {value!r}")
            if not math.isfinite(value):
                raise ValueError(f"player {self.key!r}: {name} must be finite, got {value!r}")
        if self.minutes < 0 or self.points < 0:
            raise ValueError(f"player {self.key!r}: minutes and points cannot be negative")
        if not 0.0 <= self.chance <= 1.0:
            raise ValueError(f"player {self.key!r}: chance must be within [0, 1]")


@dataclass(frozen=True)
class PlayerOutcome:
    """One player after the layer."""

    key: Hashable
    chance: float
    #: ``p * (1 - c)``
    expected_points_lost: float
    #: ``m * (1 - c)``
    expected_minutes_lost: float
    #: ``p * c * boost``
    projected_points: float
    #: ``m * c * min(1 + rotationShare * lostMin / max(availMin, 1), kappa)``; display only.
    projected_minutes: float


@dataclass(frozen=True)
class InjuryResult:
    """A squad's totals and its players' outcomes."""

    full_strength_points: float
    available_points: float
    lost_points: float
    lost_minutes: float
    available_minutes: float
    replacement_points: float
    absorbed_points: float
    #: Points actually credited to the squad after the cap policy.
    absorbed_effective: float
    #: Recovered points no player can take under the cap (``workbook`` policy only).
    unassigned_points: float
    boost: float
    squad_points: float
    availability_factor: float
    cap_binding: bool
    cap_policy: str
    players: tuple[PlayerOutcome, ...]

    @property
    def points_lost_to_absences(self) -> float:
        """``squad_points - full_strength_points``: what the absences cost the squad."""
        return self.squad_points - self.full_strength_points


def apply_injury_layer(players: Sequence[PlayerInput], settings: InjurySettings) -> InjuryResult:
    """Run the workbook's injury layer over one squad.

    ``players`` is the squad in any order; ``players`` in the result are in the same order.
    An empty squad is a squad with nothing to lose: every total is zero and ``af`` is 1.
    """
    q = settings.replacement_per_minute
    a = settings.absorb_share
    kappa = settings.boost_cap

    full = math.fsum(p.points for p in players)
    avail = math.fsum(p.points * p.chance for p in players)
    lost = math.fsum(p.points * (1 - p.chance) for p in players)
    lost_min = math.fsum(p.minutes * (1 - p.chance) for p in players)
    avail_min = math.fsum(p.minutes * p.chance for p in players)

    repl = lost_min * q
    absorbed = repl + a * (lost - repl) if lost >= repl else lost

    if avail == 0:
        boost = 1.0
        cap_binding = absorbed > 0
        capacity = 0.0
    else:
        boost = min(1 + absorbed / avail, kappa)
        cap_binding = (1 + absorbed / avail) > kappa
        capacity = (kappa - 1) * avail

    if settings.cap_policy == CAP_CONSISTENT:
        effective = min(absorbed, capacity)
        unassigned = 0.0
    else:
        effective = absorbed
        unassigned = absorbed - (boost - 1) * avail

    squad = avail + effective
    factor = squad / full if full != 0 else 1.0

    rotation = min(1 + settings.rotation_share * lost_min / max(avail_min, 1), kappa)
    outcomes = tuple(
        PlayerOutcome(
            key=p.key,
            chance=p.chance,
            expected_points_lost=p.points * (1 - p.chance),
            expected_minutes_lost=p.minutes * (1 - p.chance),
            projected_points=p.points * p.chance * boost,
            projected_minutes=p.minutes * p.chance * rotation,
        )
        for p in players
    )
    return InjuryResult(
        full_strength_points=full,
        available_points=avail,
        lost_points=lost,
        lost_minutes=lost_min,
        available_minutes=avail_min,
        replacement_points=repl,
        absorbed_points=absorbed,
        absorbed_effective=effective,
        unassigned_points=unassigned,
        boost=boost,
        squad_points=squad,
        availability_factor=factor,
        cap_binding=cap_binding,
        cap_policy=settings.cap_policy,
        players=outcomes,
    )


def scale_to_total(values: Sequence[float], target: float) -> list[float]:
    """Scale ``values`` proportionally so they sum to ``target`` (squad reconciliation).

    The workbook scales each squad's full-strength points to the club's rated scoring so the
    players add up to the team. Values summing to zero cannot be scaled and are returned
    unchanged rather than divided by zero; the caller decides what that squad means.
    """
    if not math.isfinite(target) or target < 0:
        raise ValueError(f"target must be a finite non-negative number, got {target!r}")
    total = math.fsum(values)
    if total == 0:
        return list(values)
    factor = target / total
    return [v * factor for v in values]
