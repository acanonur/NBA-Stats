"""The team-score model: the user's workbook, as functions.

The EuroLeague workbook projects a game in four small steps, and this module is those steps and
nothing else. Each function carries the workbook cell it reproduces, because the standard this
code is held to is *formula for formula*: a golden test transcribes the cell formulas literally
and demands agreement to 1e-9.

The model, in four lines
------------------------
::

    rating(prior, L, r, adj)   = L + (prior - L) * (1 - r) + adj          Team Ratings K and L
    attack A = pf / L,  defence D = pa / L        (a higher D is a worse defence)
    home = L * A_home' * D_away + h / 2           Round 3 J
    away = L * A_away' * D_home - h / 2           Round 3 K
        where A' = A * af and af is the availability factor from the injury layer

``L`` is the league's average team score, ``r`` how far last season is pulled back toward it,
``adj`` the club's roster or in-season adjustment, ``h`` the home advantage in points, split
half to each side. Everything else is arithmetic on those: the margin, whether it is a
toss-up, who is projected to win, the combined points, and what absences cost (the projection
minus the same projection at full strength).

What is deliberately absent
---------------------------
There is no win probability, no total rounded to a half point and no comparison with any
external number. Win probability is omitted in v1 as a design choice by the lead; the margin
and its interval carry the same uncertainty without producing a figure one step from a price.
``combined_points`` is the plain sum of the two projected scores, to be shown to one decimal,
and it is never called a line or a total. The workbook cells that compute those things are
listed as excluded in the design (section 8.7) and not transcribed here.

Edge rules
----------
* A projected margin of less than half a point in size (``|margin| < 0.5``) is a **toss-up**
  and has no projected winner. Exactly half a point is not a toss-up, as in the workbook.
* ``h`` is zero at a neutral venue. A per-game override, when present, replaces the default.
  When neutrality is unknown the default applies and the projection says the venue was
  assumed (:func:`home_advantage_for_game`). A neutral flag takes precedence over an override,
  because it is a recorded fact about the venue and an override is an input someone typed.
* An 80% interval is the centre plus or minus 1.2816 standard deviations, or nothing when the
  spread is not known; it is never invented from another league's spread.

The in-season update
--------------------
After a game is final, a club's attack and defence adjustments move by a weight times the
miss: ``adj + w * (actual - projected)`` (R2 Review ``J``/``K``/``M``/``O``). The weights are
the workbook's for the rounds it has (0.10 then 0.09); beyond them the default is
``1 / (n + 9)``, flagged ``extrapolated`` so the payload can say it is a default, not the
user's number. The NBA model uses ``1 / (n + 10)``. Actual scores may be scaled to regulation
length first (:func:`regulation_scale`), so an overtime game does not read as a defensive
collapse; that is a documented deviation from the workbook, which did not scale.

Pure and stdlib-only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Mapping

__all__ = [
    "Z80",
    "TOSS_UP_MARGIN",
    "rating",
    "attack_index",
    "defence_index",
    "apply_availability",
    "match_scores",
    "margin_of",
    "is_toss_up",
    "projected_winner",
    "combined_points",
    "availability_effect",
    "interval80",
    "update_adjustment",
    "RoundWeight",
    "round_weight",
    "sequential_weight",
    "regulation_scale",
    "blend_league_level",
    "HomeAdvantage",
    "home_advantage_for_game",
    "round_half_up",
    "format_fixed",
    "summary_text",
    "TeamStrength",
    "MatchProjection",
    "project_match",
]

#: The 80% two-sided normal multiplier, as the workbook's design states it (not 1.28155...).
Z80: Final = 1.2816

#: ``|margin|`` strictly below this is a toss-up.
TOSS_UP_MARGIN: Final = 0.5


def _finite(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return float(value)


# -------------------------------------------------------------------------- the ratings


def rating(prior: float, league_level: float, regression: float, adjustment: float = 0.0) -> float:
    """``L + (prior - L) * (1 - r) + adj`` (Team Ratings ``K``, ``L``).

    ``regression`` is the share of last season's deviation from the league that is given
    back: 0 keeps last season as it was, 1 makes every club average.
    """
    _finite("prior", prior)
    _finite("league_level", league_level)
    _finite("adjustment", adjustment)
    if not 0.0 <= _finite("regression", regression) <= 1.0:
        raise ValueError(f"regression must be within [0, 1], got {regression!r}")
    return league_level + (prior - league_level) * (1 - regression) + adjustment


def _index(points: float, league_level: float) -> float:
    if not _finite("league_level", league_level) > 0:
        raise ValueError(f"league_level must be positive, got {league_level!r}")
    return _finite("points", points) / league_level


def attack_index(pf: float, league_level: float) -> float:
    """``pf / L`` (Team Ratings ``M``): above 1 scores more than the league average."""
    return _index(pf, league_level)


def defence_index(pa: float, league_level: float) -> float:
    """``pa / L`` (Team Ratings ``N``): above 1 *allows* more than average, a worse defence."""
    return _index(pa, league_level)


def apply_availability(attack: float, availability_factor: float) -> float:
    """``A' = A * af``: the attack index after absences (Team Ratings ``AC``)."""
    return _finite("attack", attack) * _finite("availability_factor", availability_factor)


# ---------------------------------------------------------------------------- the match


def match_scores(
    league_level: float,
    home_attack: float,
    away_defence: float,
    away_attack: float,
    home_defence: float,
    home_advantage: float,
) -> tuple[float, float]:
    """``(home, away)`` projected scores (Round 3 ``J`` and ``K``).

    ``home_attack`` and ``away_attack`` are the indices *after* availability. The home side
    gets half the advantage added and the away side half taken off, so the margin moves by
    the whole of it and the combined points do not move at all.
    """
    level = _finite("league_level", league_level)
    h = _finite("home_advantage", home_advantage)
    home = level * home_attack * away_defence + h / 2
    away = level * away_attack * home_defence - h / 2
    return home, away


def margin_of(home: float, away: float) -> float:
    """Home minus away (Round 3 ``L``)."""
    return home - away


def is_toss_up(margin: float) -> bool:
    """True when the projected margin is under half a point in size."""
    return abs(margin) < TOSS_UP_MARGIN


def projected_winner(margin: float) -> str | None:
    """``home`` or ``away``, or ``None`` for a toss-up (Round 3 ``M``)."""
    if is_toss_up(margin):
        return None
    return "home" if margin >= 0 else "away"


def combined_points(home: float, away: float) -> float:
    """The two projected scores added (Round 3 ``O``). Shown to one decimal; never rounded to
    a half point and never presented as a line or a total."""
    return home + away


def availability_effect(projected: float, full_strength: float) -> float:
    """What absences cost: the projection minus the same projection at full strength."""
    return projected - full_strength


def interval80(centre: float, sd: float | None) -> tuple[float, float] | None:
    """``centre +- 1.2816 * sd`` as ``(low, high)``; ``None`` when ``sd`` is ``None``."""
    if sd is None:
        return None
    if _finite("sd", sd) < 0:
        raise ValueError(f"sd cannot be negative, got {sd!r}")
    spread = Z80 * sd
    return centre - spread, centre + spread


# ------------------------------------------------------------------------ the updating


def update_adjustment(adjustment: float, actual: float, projected: float, weight: float) -> float:
    """``adj + w * (actual - projected)`` (R2 Review ``J``/``K`` and ``M``/``O``)."""
    if not 0.0 <= _finite("weight", weight) <= 1.0:
        raise ValueError(f"weight must be within [0, 1], got {weight!r}")
    return adjustment + weight * (actual - projected)


def sequential_weight(n: int, offset: float) -> float:
    """The default update weight for a club's ``n``-th game: ``1 / (n + offset)``."""
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"game number must be a positive integer, got {n!r}")
    if not _finite("offset", offset) >= 0:
        raise ValueError(f"offset cannot be negative, got {offset!r}")
    return 1.0 / (n + offset)


@dataclass(frozen=True)
class RoundWeight:
    weight: float
    #: True when ``n`` lies beyond the supplied table and the default formula was used.
    extrapolated: bool


def round_weight(n: int, table: Mapping[int, float], *, offset: float = 9) -> RoundWeight:
    """The weight for a club's ``n``-th game of the season: the table's, else ``1/(n+offset)``.

    The EuroLeague workbook's table has ``1 -> 0.10`` and ``2 -> 0.09``; the default offset of
    9 continues it smoothly (round 3 gives 0.0833) and is flagged ``extrapolated``.
    """
    if n in table:
        value = _finite(f"roundWeight.{n}", table[n])
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"roundWeight.{n} must be within [0, 1], got {value!r}")
        return RoundWeight(value, False)
    return RoundWeight(sequential_weight(n, offset), True)


def regulation_scale(points: float, team_seconds: float, regulation_seconds: float) -> float:
    """``points * R / t``: a score restated as if the game had ended after regulation time."""
    if not _finite("team_seconds", team_seconds) > 0:
        raise ValueError(f"team_seconds must be positive, got {team_seconds!r}")
    return _finite("points", points) * regulation_seconds / team_seconds


def blend_league_level(
    current_mean: float | None,
    team_games: int,
    previous_level: float | None,
    *,
    previous_weight: float = 150,
) -> float | None:
    """The league level ``(n * L_cur + w * L_prev) / (n + w)`` that fades last season out.

    Used by the NBA, where the level moves smoothly from last season's toward this season's
    as team-games accumulate, with no jump at some arbitrary game. ``None`` inputs degrade
    sensibly: no games means last season's level, no previous season means the current mean.
    """
    if current_mean is None or team_games <= 0:
        return previous_level
    if previous_level is None:
        return current_mean
    return (team_games * current_mean + previous_weight * previous_level) / (
        team_games + previous_weight
    )


# ----------------------------------------------------------------------- home advantage


@dataclass(frozen=True)
class HomeAdvantage:
    points: float
    #: True when the default was applied because the venue's neutrality is unknown.
    venue_assumed: bool
    #: ``neutral``, ``override`` or ``default``.
    basis: str


def home_advantage_for_game(
    *, default: float, override: float | None = None, is_neutral: bool | None = None
) -> HomeAdvantage:
    """The ``h`` for one game, and whether the venue was assumed.

    Neutral venue: zero. Else a per-game override, when present. Else the league default, with
    ``venue_assumed`` set when ``is_neutral`` is unknown (never assumed false in the data, so
    it is flagged here instead).
    """
    if is_neutral is True:
        return HomeAdvantage(0.0, False, "neutral")
    if override is not None:
        return HomeAdvantage(_finite("override", override), False, "override")
    return HomeAdvantage(_finite("default", default), is_neutral is None, "default")


# ------------------------------------------------------------------------ presentation


def round_half_up(value: float, digits: int = 1) -> float:
    """Round half away from zero at ``digits`` decimals, on the value's 15-digit decimal form.

    Python's ``round`` and ``format`` round the exact binary value half to even, so
    ``round(2.25, 1)`` is 2.2 and ``round(0.15, 1)`` is 0.1. A spreadsheet's ``FIXED`` rounds
    the decimal a person sees (15 significant digits) half away from zero (2.3 and 0.2), and
    a summary that disagrees with the sheet on a tie would be a visible, needless difference.
    """
    quantum = Decimal(1).scaleb(-digits)
    exact = Decimal(format(value, ".15g"))
    return float(exact.quantize(quantum, rounding=ROUND_HALF_UP))


def format_fixed(value: float, digits: int = 1) -> str:
    """``value`` as fixed-point text with ``digits`` decimals, rounded as :func:`round_half_up`."""
    quantum = Decimal(1).scaleb(-digits)
    exact = Decimal(format(value, ".15g"))
    return f"{exact.quantize(quantum, rounding=ROUND_HALF_UP):.{digits}f}"


def summary_text(margin: float, home_abbr: str, away_abbr: str) -> str:
    """``"ZZA by 4.8"`` for the projected winner, or ``"Toss-up"`` (Round 3 ``M``)."""
    winner = projected_winner(margin)
    if winner is None:
        return "Toss-up"
    abbr = home_abbr if winner == "home" else away_abbr
    return f"{abbr} by {format_fixed(abs(margin), 1)}"


# ------------------------------------------------------------------------ the whole game


@dataclass(frozen=True)
class TeamStrength:
    """A side's ratings going into a game."""

    attack: float
    defence: float
    #: The injury layer's ``af``; 1 means full strength.
    availability_factor: float = 1.0


@dataclass(frozen=True)
class MatchProjection:
    """One game projected, with the full-strength twin that makes absences measurable."""

    league_level: float
    home_advantage_points: float
    home_points: float
    away_points: float
    home_full_strength: float
    away_full_strength: float
    margin: float
    is_toss_up: bool
    #: ``home``, ``away`` or ``None``.
    projected_winner: str | None
    combined_points: float
    combined_full_strength: float
    home_availability_effect: float
    away_availability_effect: float
    combined_availability_effect: float
    home_attack_after_availability: float
    away_attack_after_availability: float
    home_range80: tuple[float, float] | None
    away_range80: tuple[float, float] | None
    margin_range80: tuple[float, float] | None


def project_match(
    league_level: float,
    home: TeamStrength,
    away: TeamStrength,
    home_advantage_points: float,
    *,
    team_sd: float | None = None,
    margin_sd: float | None = None,
) -> MatchProjection:
    """Project one game from both sides' ratings (Round 3 ``J`` to ``P`` and ``Y``, ``Z``).

    ``team_sd`` is the spread of one team's score and ``margin_sd`` that of the margin; both
    are ``None`` until a spread is known or assumed, and then the intervals are ``None`` too.
    """
    home_attack = apply_availability(home.attack, home.availability_factor)
    away_attack = apply_availability(away.attack, away.availability_factor)
    home_pts, away_pts = match_scores(
        league_level, home_attack, away.defence, away_attack, home.defence, home_advantage_points
    )
    home_full, away_full = match_scores(
        league_level, home.attack, away.defence, away.attack, home.defence, home_advantage_points
    )
    margin = margin_of(home_pts, away_pts)
    combined = combined_points(home_pts, away_pts)
    combined_full = combined_points(home_full, away_full)
    return MatchProjection(
        league_level=league_level,
        home_advantage_points=home_advantage_points,
        home_points=home_pts,
        away_points=away_pts,
        home_full_strength=home_full,
        away_full_strength=away_full,
        margin=margin,
        is_toss_up=is_toss_up(margin),
        projected_winner=projected_winner(margin),
        combined_points=combined,
        combined_full_strength=combined_full,
        home_availability_effect=availability_effect(home_pts, home_full),
        away_availability_effect=availability_effect(away_pts, away_full),
        combined_availability_effect=availability_effect(combined, combined_full),
        home_attack_after_availability=home_attack,
        away_attack_after_availability=away_attack,
        home_range80=interval80(home_pts, team_sd),
        away_range80=interval80(away_pts, team_sd),
        margin_range80=interval80(margin, margin_sd),
    )
