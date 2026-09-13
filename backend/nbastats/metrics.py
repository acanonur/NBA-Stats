"""Hardwood advanced-statistics engine: pure, dependency-free stat formulas.

Every function here is a *pure* function of explicit named arguments. There is no
database, no network and no framework import in this module - it is stdlib-only so
the formulas can be unit-tested without a schema and reused by the ingest worker,
the API layer and any offline script.

Three rules run through the whole module, and they are correctness requirements
rather than style preferences:

1. **Missing data is never zero.** Every function returns ``None`` when a required
   input is missing or a denominator is zero. A stat that did not exist in an era
   (steals before 1973-74, individual turnovers before 1977-78, threes before
   1979-80) arrives here as ``None`` and leaves as ``None``; the API layer turns
   that into ``availability: "unavailable"``.
2. **Nothing raises on data.** Bad or absent *data* yields ``None``. Only a bad
   *argument* (an unknown per-mode, a non-positive rolling window) raises
   ``ValueError``, because that is a programming error, not a gap in the record.
3. **Units are declared per function.** The named formula functions return the
   number exactly as the source defines it - so ``usage_pct`` returns ``30.6`` for
   30.6%, because Basketball-Reference's USG% formula carries the ``100 *``
   factor. :func:`compute_metric`, which is the layer that feeds ``MetricValue``,
   converts every percent-formatted metric to a fraction in ``[0, 1]`` as
   ``contracts/CONTRACT.md`` requires. The scaling is applied through one explicit
   table (:data:`PERCENT_METRICS`) so it can never happen twice or by accident.

Formula sources are quoted in each docstring: Basketball-Reference's glossary,
Dean Oliver's *Basketball on Paper* (individual ORtg/DRtg) and the NBA.com
glossary (PIE, assist ratio, fantasy points).

Column vocabulary
-----------------
:func:`compute_metric` reads plain ``dict`` rows keyed by snake_case column names.
It accepts both the short NBA vocabulary (``pts``, ``fga``, ``fg3m``, ``oreb``)
and the long vocabulary used by this repo's BigQuery-shaped SQL (``points``,
``field_goals_attempted``, ``three_pointers_made``, ``offensive_rebounds``); see
:data:`COLUMN_ALIASES`. Team and opponent context can be supplied either as
separate rows or as ``team_``/``opp_`` prefixed columns on the subject row.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Mapping, NamedTuple, Optional, Sequence

__all__ = [
    "FT_POSSESSION_WEIGHT",
    "FOUR_FACTOR_WEIGHTS",
    "PER_MODES",
    "PERCENT_METRICS",
    "SUPPORTED_METRIC_KEYS",
    "COLUMN_ALIASES",
    "FourFactor",
    "possessions",
    "effective_fg_pct",
    "true_shooting_pct",
    "three_point_attempt_rate",
    "free_throw_rate",
    "points_per_shot",
    "usage_pct",
    "assist_pct",
    "assist_ratio",
    "rebound_pct",
    "offensive_rebound_pct",
    "defensive_rebound_pct",
    "turnover_pct",
    "steal_pct",
    "block_pct",
    "pace",
    "game_score",
    "fantasy_points",
    "q_assist",
    "fg_part",
    "assist_part",
    "ft_part",
    "team_scoring_possessions",
    "team_oreb_pct",
    "team_play_pct",
    "team_oreb_weight",
    "oreb_part",
    "scoring_possessions",
    "missed_fg_possessions",
    "missed_ft_possessions",
    "total_possessions",
    "floor_pct",
    "points_produced",
    "offensive_rating",
    "defensive_fg_pct",
    "defensive_oreb_pct",
    "missed_fg_weight",
    "stops1",
    "stops2",
    "stops",
    "stop_pct",
    "d_pts_per_scoring_poss",
    "team_defensive_rating",
    "defensive_rating",
    "net_rating",
    "pie",
    "vorp",
    "four_factors",
    "per_mode_convert",
    "rolling_average",
    "compute_metric",
]

#: Dean Oliver's free-throw-trip coefficient: 0.44 of all FTA end a possession.
FT_POSSESSION_WEIGHT: float = 0.44

#: Oliver's four factors and the weight he assigns each in explaining wins.
FOUR_FACTOR_WEIGHTS: dict[str, float] = {
    "efg_pct": 0.40,
    "tov_pct": 0.25,
    "oreb_pct": 0.20,
    "ftr": 0.15,
}

#: Per-mode names accepted by :func:`per_mode_convert` (widget config enum).
PER_MODES: tuple[str, ...] = ("PerGame", "Totals", "Per36", "Per100")

#: First season with a three-point line; a NULL 3PM before this is a true zero.
THREE_POINT_ERA_START_YEAR: int = 1979


# --------------------------------------------------------------------------- #
# Numeric plumbing
# --------------------------------------------------------------------------- #


def _f(value: Any) -> Optional[float]:
    """Coerce a raw cell to a finite float, or ``None``.

    ``bool`` is rejected on purpose: a flag column such as ``started`` must never
    silently become ``1.0`` in a formula.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _div(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    """Divide, returning ``None`` for a missing operand or a zero denominator."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _all(*values: Any) -> Optional[tuple[float, ...]]:
    """Coerce every argument to float, or return ``None`` if any is missing."""
    out: list[float] = []
    for value in values:
        coerced = _f(value)
        if coerced is None:
            return None
        out.append(coerced)
    return tuple(out)


def _positive(value: Any) -> Optional[float]:
    """Coerce to float and require it to be strictly positive.

    Used for minutes and other on-floor denominators: a player with zero (or
    nonsensically negative) minutes has no rate stats, so the answer is ``None``
    rather than a division artefact.
    """
    out = _f(value)
    if out is None or out <= 0:
        return None
    return out


# --------------------------------------------------------------------------- #
# Possessions, shooting efficiency
# --------------------------------------------------------------------------- #


def possessions(
    fga: Optional[float] = None,
    fta: Optional[float] = None,
    oreb: Optional[float] = None,
    tov: Optional[float] = None,
) -> Optional[float]:
    """Possessions = FGA + 0.44 * FTA - ORB + TOV.

    The standard single-team box-score estimate (Basketball-Reference; Oliver's
    original coefficient 0.4 was later refined to 0.44). All four inputs are
    required - a pre-1977-78 row with no individual turnovers yields ``None``.
    """
    values = _all(fga, fta, oreb, tov)
    if values is None:
        return None
    field_goals, free_throws, offensive_rebounds, turnovers = values
    return field_goals + FT_POSSESSION_WEIGHT * free_throws - offensive_rebounds + turnovers


def effective_fg_pct(
    fgm: Optional[float] = None,
    fg3m: Optional[float] = None,
    fga: Optional[float] = None,
) -> Optional[float]:
    """eFG% = (FGM + 0.5 * 3PM) / FGA. Returns a fraction in ``[0, 1]``.

    3PM is required. Before 1979-80 there was no three-point line, so a row from
    that era carries a true zero rather than an unknown; :func:`compute_metric`
    applies that substitution when the row names its season, and never otherwise.
    """
    values = _all(fgm, fg3m, fga)
    if values is None:
        return None
    made, threes, attempts = values
    return _div(made + 0.5 * threes, attempts)


def true_shooting_pct(
    pts: Optional[float] = None,
    fga: Optional[float] = None,
    fta: Optional[float] = None,
) -> Optional[float]:
    """TS% = PTS / (2 * (FGA + 0.44 * FTA)). Returns a fraction in ``[0, 1]``."""
    values = _all(pts, fga, fta)
    if values is None:
        return None
    points, attempts, free_throws = values
    return _div(points, 2.0 * (attempts + FT_POSSESSION_WEIGHT * free_throws))


def three_point_attempt_rate(
    fg3a: Optional[float] = None, fga: Optional[float] = None
) -> Optional[float]:
    """3PAr = 3PA / FGA - the share of shots taken from three."""
    values = _all(fg3a, fga)
    if values is None:
        return None
    return _div(values[0], values[1])


def free_throw_rate(fta: Optional[float] = None, fga: Optional[float] = None) -> Optional[float]:
    """FTr = FTA / FGA - free throws drawn per field goal attempt."""
    values = _all(fta, fga)
    if values is None:
        return None
    return _div(values[0], values[1])


def points_per_shot(pts: Optional[float] = None, fga: Optional[float] = None) -> Optional[float]:
    """PPS = PTS / FGA."""
    values = _all(pts, fga)
    if values is None:
        return None
    return _div(values[0], values[1])


# --------------------------------------------------------------------------- #
# Rate stats (Basketball-Reference glossary)
# --------------------------------------------------------------------------- #


def usage_pct(
    fga: Optional[float] = None,
    fta: Optional[float] = None,
    tov: Optional[float] = None,
    minutes: Optional[float] = None,
    team_minutes: Optional[float] = None,
    team_fga: Optional[float] = None,
    team_fta: Optional[float] = None,
    team_tov: Optional[float] = None,
) -> Optional[float]:
    """USG% = 100 * ((FGA + 0.44*FTA + TOV) * (TmMP/5)) / (MP * (TmFGA + 0.44*TmFTA + TmTOV)).

    Returned on the 0-100 scale of the source formula; :func:`compute_metric`
    divides by 100 for the API's fractional convention.
    """
    values = _all(fga, fta, tov, team_fga, team_fta, team_tov)
    played = _positive(minutes)
    tm_minutes = _positive(team_minutes)
    if values is None or played is None or tm_minutes is None:
        return None
    attempts, free_throws, turnovers, tm_fga, tm_fta, tm_tov = values
    player_plays = attempts + FT_POSSESSION_WEIGHT * free_throws + turnovers
    team_plays = tm_fga + FT_POSSESSION_WEIGHT * tm_fta + tm_tov
    return _div(100.0 * player_plays * (tm_minutes / 5.0), played * team_plays)


def assist_pct(
    ast: Optional[float] = None,
    minutes: Optional[float] = None,
    team_minutes: Optional[float] = None,
    team_fgm: Optional[float] = None,
    fgm: Optional[float] = None,
) -> Optional[float]:
    """AST% = 100 * AST / (((MP / (TmMP/5)) * TmFGM) - FGM).

    The share of teammate field goals a player assisted while on the floor.
    Returned on the 0-100 scale.
    """
    values = _all(ast, team_fgm, fgm)
    played = _positive(minutes)
    tm_minutes = _positive(team_minutes)
    if values is None or played is None or tm_minutes is None:
        return None
    assists, tm_fgm, own_fgm = values
    # The denominator is an *estimate* of the field goals teammates made while this player was
    # on the floor, and the estimate goes to zero or below whenever a player made more field
    # goals than his minutes-share of the team's — an ordinary short high-usage stint. A
    # non-positive estimate carries no information about assist share, so the honest answer is
    # "unavailable", not the -600% or +14400% a bare zero-check lets through.
    teammate_fgm = _positive((played / (tm_minutes / 5.0)) * tm_fgm - own_fgm)
    return _div(100.0 * assists, teammate_fgm)


def assist_ratio(
    ast: Optional[float] = None,
    fga: Optional[float] = None,
    fta: Optional[float] = None,
    tov: Optional[float] = None,
) -> Optional[float]:
    """Assist Ratio = 100 * AST / (FGA + 0.44*FTA + AST + TOV)  (NBA.com glossary).

    Assists per 100 possessions *used* by the player. Not a percent-formatted
    metric in the catalog, so it stays on the 0-100 scale end to end.
    """
    values = _all(ast, fga, fta, tov)
    if values is None:
        return None
    assists, attempts, free_throws, turnovers = values
    used = attempts + FT_POSSESSION_WEIGHT * free_throws + assists + turnovers
    return _div(100.0 * assists, used)


def rebound_pct(
    reb: Optional[float] = None,
    minutes: Optional[float] = None,
    team_minutes: Optional[float] = None,
    team_reb: Optional[float] = None,
    opp_reb: Optional[float] = None,
) -> Optional[float]:
    """TRB% = 100 * (TRB * (TmMP/5)) / (MP * (TmTRB + OppTRB)). 0-100 scale."""
    values = _all(reb, team_reb, opp_reb)
    played = _positive(minutes)
    tm_minutes = _positive(team_minutes)
    if values is None or played is None or tm_minutes is None:
        return None
    rebounds, tm_reb, opponent_reb = values
    return _div(100.0 * rebounds * (tm_minutes / 5.0), played * (tm_reb + opponent_reb))


def offensive_rebound_pct(
    oreb: Optional[float] = None,
    minutes: Optional[float] = None,
    team_minutes: Optional[float] = None,
    team_oreb: Optional[float] = None,
    opp_dreb: Optional[float] = None,
) -> Optional[float]:
    """ORB% = 100 * (ORB * (TmMP/5)) / (MP * (TmORB + OppDRB)). 0-100 scale.

    The available offensive rebounds are the team's own offensive boards plus the
    ones the opponent claimed defensively.
    """
    return rebound_pct(oreb, minutes, team_minutes, team_oreb, opp_dreb)


def defensive_rebound_pct(
    dreb: Optional[float] = None,
    minutes: Optional[float] = None,
    team_minutes: Optional[float] = None,
    team_dreb: Optional[float] = None,
    opp_oreb: Optional[float] = None,
) -> Optional[float]:
    """DRB% = 100 * (DRB * (TmMP/5)) / (MP * (TmDRB + OppORB)). 0-100 scale."""
    return rebound_pct(dreb, minutes, team_minutes, team_dreb, opp_oreb)


def turnover_pct(
    tov: Optional[float] = None, fga: Optional[float] = None, fta: Optional[float] = None
) -> Optional[float]:
    """TOV% = 100 * TOV / (FGA + 0.44*FTA + TOV) - turnovers per 100 plays used."""
    values = _all(tov, fga, fta)
    if values is None:
        return None
    turnovers, attempts, free_throws = values
    used = attempts + FT_POSSESSION_WEIGHT * free_throws + turnovers
    return _div(100.0 * turnovers, used)


def steal_pct(
    stl: Optional[float] = None,
    minutes: Optional[float] = None,
    team_minutes: Optional[float] = None,
    opp_poss: Optional[float] = None,
) -> Optional[float]:
    """STL% = 100 * (STL * (TmMP/5)) / (MP * OppPoss). 0-100 scale.

    The share of opponent possessions that ended in a steal by this player while
    he was on the floor.
    """
    values = _all(stl, opp_poss)
    played = _positive(minutes)
    tm_minutes = _positive(team_minutes)
    if values is None or played is None or tm_minutes is None:
        return None
    steals, opponent_possessions = values
    return _div(100.0 * steals * (tm_minutes / 5.0), played * opponent_possessions)


def block_pct(
    blk: Optional[float] = None,
    minutes: Optional[float] = None,
    team_minutes: Optional[float] = None,
    opp_fga: Optional[float] = None,
    opp_fg3a: Optional[float] = None,
) -> Optional[float]:
    """BLK% = 100 * (BLK * (TmMP/5)) / (MP * (OppFGA - OppFG3A)). 0-100 scale.

    Only two-point attempts are blockable in the denominator, per the glossary.
    """
    values = _all(blk, opp_fga, opp_fg3a)
    played = _positive(minutes)
    tm_minutes = _positive(team_minutes)
    if values is None or played is None or tm_minutes is None:
        return None
    blocks, opponent_fga, opponent_fg3a = values
    # Two-point attempts are what can be blocked. A non-positive count is not "zero blocks
    # allowed", it is data that cannot support the rate at all.
    blockable = _positive(opponent_fga - opponent_fg3a)
    if blockable is None:
        return None
    return _div(100.0 * blocks * (tm_minutes / 5.0), played * blockable)


def pace(
    team_poss: Optional[float] = None,
    opp_poss: Optional[float] = None,
    team_minutes: Optional[float] = None,
) -> Optional[float]:
    """Pace = 48 * ((TmPoss + OppPoss) / (2 * (TmMP/5))).

    Possessions per 48 minutes. ``team_minutes`` is the team's total minutes
    played (240 per regulation game), so ``TmMP/5`` is the number of team-games
    worth of floor time and the average of the two teams' possession estimates
    smooths out box-score noise.
    """
    values = _all(team_poss, opp_poss)
    tm_minutes = _positive(team_minutes)
    if values is None or tm_minutes is None:
        return None
    return _div(48.0 * (values[0] + values[1]), 2.0 * (tm_minutes / 5.0))


def game_score(
    pts: Optional[float] = None,
    fgm: Optional[float] = None,
    fga: Optional[float] = None,
    fta: Optional[float] = None,
    ftm: Optional[float] = None,
    oreb: Optional[float] = None,
    dreb: Optional[float] = None,
    stl: Optional[float] = None,
    ast: Optional[float] = None,
    blk: Optional[float] = None,
    pf: Optional[float] = None,
    tov: Optional[float] = None,
) -> Optional[float]:
    """Game Score (Hollinger).

    GmSc = PTS + 0.4*FGM - 0.7*FGA - 0.4*(FTA-FTM) + 0.7*ORB + 0.3*DRB + STL
           + 0.7*AST + 0.7*BLK - 0.4*PF - TOV

    Scaled so that 10 is roughly an average starter's night and 40 is a monster
    game. Needs the full modern box score, hence 1973-74 at the earliest.
    """
    values = _all(pts, fgm, fga, fta, ftm, oreb, dreb, stl, ast, blk, pf, tov)
    if values is None:
        return None
    (
        points,
        made,
        attempts,
        ft_attempts,
        ft_made,
        off_reb,
        def_reb,
        steals,
        assists,
        blocks,
        fouls,
        turnovers,
    ) = values
    return (
        points
        + 0.4 * made
        - 0.7 * attempts
        - 0.4 * (ft_attempts - ft_made)
        + 0.7 * off_reb
        + 0.3 * def_reb
        + steals
        + 0.7 * assists
        + 0.7 * blocks
        - 0.4 * fouls
        - turnovers
    )


def fantasy_points(
    pts: Optional[float] = None,
    reb: Optional[float] = None,
    ast: Optional[float] = None,
    stl: Optional[float] = None,
    blk: Optional[float] = None,
    tov: Optional[float] = None,
) -> Optional[float]:
    """NBA fantasy points = PTS + 1.2*REB + 1.5*AST + 3*STL + 3*BLK - TOV.

    The scoring NBA.com publishes as ``NBA_FANTASY_PTS``.
    """
    values = _all(pts, reb, ast, stl, blk, tov)
    if values is None:
        return None
    points, rebounds, assists, steals, blocks, turnovers = values
    return points + 1.2 * rebounds + 1.5 * assists + 3.0 * steals + 3.0 * blocks - turnovers


# --------------------------------------------------------------------------- #
# Dean Oliver individual Offensive Rating
#
# Basketball on Paper, chapter 1; the transcription below follows the
# Basketball-Reference glossary step for step. Every intermediate is its own
# function so each one can be checked in isolation.
# --------------------------------------------------------------------------- #


def q_assist(
    ast: Optional[float] = None,
    fgm: Optional[float] = None,
    minutes: Optional[float] = None,
    team_ast: Optional[float] = None,
    team_fgm: Optional[float] = None,
    team_minutes: Optional[float] = None,
) -> Optional[float]:
    """qAST - the share of a player's own made field goals that were assisted.

    qAST = ((MP / (TmMP/5)) * (1.14 * ((TmAST - AST) / TmFGM)))
           + ((((TmAST / TmMP) * MP * 5 - AST)
               / ((TmFGM / TmMP) * MP * 5 - FGM)) * (1 - (MP / (TmMP/5))))

    A blend of a team-wide assist rate and the rate among the *other* four
    players on the floor, weighted by how much of the game the player played.
    """
    values = _all(ast, fgm, team_ast, team_fgm)
    played = _positive(minutes)
    tm_minutes = _positive(team_minutes)
    if values is None or played is None or tm_minutes is None:
        return None
    assists, made, tm_ast, tm_fgm = values
    if tm_fgm == 0:
        return None
    minute_share = played / (tm_minutes / 5.0)
    teammate_assists = (tm_ast / tm_minutes) * played * 5.0 - assists
    teammate_fgm = (tm_fgm / tm_minutes) * played * 5.0 - made
    second_term = _div(teammate_assists, teammate_fgm)
    if second_term is None:
        return None
    team_rate = 1.14 * ((tm_ast - assists) / tm_fgm)
    return minute_share * team_rate + second_term * (1.0 - minute_share)


def fg_part(
    fgm: Optional[float] = None,
    fga: Optional[float] = None,
    pts: Optional[float] = None,
    ftm: Optional[float] = None,
    qast: Optional[float] = None,
) -> Optional[float]:
    """FG_Part = FGM * (1 - 0.5 * ((PTS - FTM) / (2 * FGA)) * qAST).

    Made field goals, discounted for the share that a teammate created.
    """
    values = _all(fgm, fga, pts, ftm, qast)
    if values is None:
        return None
    made, attempts, points, ft_made, q = values
    if attempts == 0:
        return 0.0
    return made * (1.0 - 0.5 * ((points - ft_made) / (2.0 * attempts)) * q)


def assist_part(
    ast: Optional[float] = None,
    pts: Optional[float] = None,
    ftm: Optional[float] = None,
    fga: Optional[float] = None,
    team_pts: Optional[float] = None,
    team_ftm: Optional[float] = None,
    team_fga: Optional[float] = None,
) -> Optional[float]:
    """AST_Part = 0.5 * (((TmPTS - TmFTM) - (PTS - FTM)) / (2 * (TmFGA - FGA))) * AST.

    Credit for the scoring possessions a player's passes created, priced at the
    teammates' average points per field goal attempt.
    """
    values = _all(ast, pts, ftm, fga, team_pts, team_ftm, team_fga)
    if values is None:
        return None
    assists, points, ft_made, attempts, tm_pts, tm_ftm, tm_fga = values
    teammate_fga = tm_fga - attempts
    ratio = _div((tm_pts - tm_ftm) - (points - ft_made), 2.0 * teammate_fga)
    if ratio is None:
        return None
    return 0.5 * ratio * assists


def ft_part(ftm: Optional[float] = None, fta: Optional[float] = None) -> Optional[float]:
    """FT_Part = (1 - (1 - FT%)^2) * 0.4 * FTA, with FT% = FTM / FTA.

    The probability that a trip to the line produces at least one point, times
    the number of possession-ending trips.
    """
    values = _all(ftm, fta)
    if values is None:
        return None
    made, attempts = values
    if attempts == 0:
        return 0.0
    ft_pct = made / attempts
    return (1.0 - (1.0 - ft_pct) ** 2) * 0.4 * attempts


def team_scoring_possessions(
    team_fgm: Optional[float] = None,
    team_ftm: Optional[float] = None,
    team_fta: Optional[float] = None,
) -> Optional[float]:
    """Team_ScoringPoss = TmFGM + (1 - (1 - TmFT%)^2) * TmFTA * 0.4."""
    values = _all(team_fgm, team_ftm, team_fta)
    if values is None:
        return None
    made, ft_made, ft_attempts = values
    if ft_attempts == 0:
        return made
    return made + (1.0 - (1.0 - ft_made / ft_attempts) ** 2) * ft_attempts * 0.4


def team_oreb_pct(
    team_oreb: Optional[float] = None, opp_dreb: Optional[float] = None
) -> Optional[float]:
    """Team_ORB% = TmORB / (TmORB + OppDRB). Fraction in ``[0, 1]``."""
    values = _all(team_oreb, opp_dreb)
    if values is None:
        return None
    return _div(values[0], values[0] + values[1])


def team_play_pct(
    team_scoring_poss: Optional[float] = None,
    team_fga: Optional[float] = None,
    team_fta: Optional[float] = None,
    team_tov: Optional[float] = None,
) -> Optional[float]:
    """Team_Play% = Team_ScoringPoss / (TmFGA + TmFTA*0.4 + TmTOV).

    The share of the team's plays that ended in at least one point.
    """
    values = _all(team_scoring_poss, team_fga, team_fta, team_tov)
    if values is None:
        return None
    scoring_poss, attempts, ft_attempts, turnovers = values
    return _div(scoring_poss, attempts + ft_attempts * 0.4 + turnovers)


def team_oreb_weight(
    team_oreb_percentage: Optional[float] = None, play_pct: Optional[float] = None
) -> Optional[float]:
    """Team_ORB_Weight = ((1 - ORB%) * Play%) / ((1 - ORB%) * Play% + ORB% * (1 - Play%)).

    Oliver's credit split for an offensive rebound: how much of the resulting
    scoring possession belongs to the rebounder rather than to the shooter.
    """
    values = _all(team_oreb_percentage, play_pct)
    if values is None:
        return None
    orb_pct, play = values
    numerator = (1.0 - orb_pct) * play
    return _div(numerator, numerator + orb_pct * (1.0 - play))


def oreb_part(
    oreb: Optional[float] = None,
    orb_weight: Optional[float] = None,
    play_pct: Optional[float] = None,
) -> Optional[float]:
    """ORB_Part = ORB * Team_ORB_Weight * Team_Play%."""
    values = _all(oreb, orb_weight, play_pct)
    if values is None:
        return None
    return values[0] * values[1] * values[2]


def scoring_possessions(
    fg_part_value: Optional[float] = None,
    ast_part_value: Optional[float] = None,
    ft_part_value: Optional[float] = None,
    oreb_part_value: Optional[float] = None,
    team_oreb: Optional[float] = None,
    team_scoring_poss: Optional[float] = None,
    orb_weight: Optional[float] = None,
    play_pct: Optional[float] = None,
) -> Optional[float]:
    """ScPoss = (FG_Part + AST_Part + FT_Part)
                * (1 - (TmORB / Team_ScoringPoss) * Team_ORB_Weight * Team_Play%)
                + ORB_Part

    The bracketed factor removes the share of scoring possessions that were only
    salvaged by an offensive rebound, which is then credited back through
    ``ORB_Part`` to whoever grabbed the board.
    """
    values = _all(
        fg_part_value,
        ast_part_value,
        ft_part_value,
        oreb_part_value,
        team_oreb,
        team_scoring_poss,
        orb_weight,
        play_pct,
    )
    if values is None:
        return None
    fg_p, ast_p, ft_p, orb_p, tm_oreb, tm_scoring_poss, weight, play = values
    discount = _div(tm_oreb, tm_scoring_poss)
    if discount is None:
        return None
    return (fg_p + ast_p + ft_p) * (1.0 - discount * weight * play) + orb_p


def missed_fg_possessions(
    fga: Optional[float] = None,
    fgm: Optional[float] = None,
    team_oreb_percentage: Optional[float] = None,
) -> Optional[float]:
    """FGxPoss = (FGA - FGM) * (1 - 1.07 * Team_ORB%).

    Missed shots only cost a possession when nobody rebounds them; 1.07 is
    Oliver's adjustment for team offensive rebounding being better than average
    on one's own misses.
    """
    values = _all(fga, fgm, team_oreb_percentage)
    if values is None:
        return None
    attempts, made, orb_pct = values
    return (attempts - made) * (1.0 - 1.07 * orb_pct)


def missed_ft_possessions(
    ftm: Optional[float] = None, fta: Optional[float] = None
) -> Optional[float]:
    """FTxPoss = ((1 - FT%)^2) * 0.4 * FTA - trips to the line that scored nothing."""
    values = _all(ftm, fta)
    if values is None:
        return None
    made, attempts = values
    if attempts == 0:
        return 0.0
    return ((1.0 - made / attempts) ** 2) * 0.4 * attempts


def total_possessions(
    scoring_poss: Optional[float] = None,
    fg_missed_poss: Optional[float] = None,
    ft_missed_poss: Optional[float] = None,
    tov: Optional[float] = None,
) -> Optional[float]:
    """TotPoss = ScPoss + FGxPoss + FTxPoss + TOV - the player's individual possessions."""
    values = _all(scoring_poss, fg_missed_poss, ft_missed_poss, tov)
    if values is None:
        return None
    return values[0] + values[1] + values[2] + values[3]


def floor_pct(
    scoring_poss: Optional[float] = None, total_poss: Optional[float] = None
) -> Optional[float]:
    """Floor% = ScPoss / TotPoss - how often a player's possessions produce a score.

    Fraction in ``[0, 1]``. Oliver's companion to ORtg: ORtg says how *many*
    points, Floor% says how *often* any points at all.
    """
    values = _all(scoring_poss, total_poss)
    if values is None:
        return None
    return _div(values[0], values[1])


def points_produced(
    *,
    pts: Optional[float] = None,
    fgm: Optional[float] = None,
    fga: Optional[float] = None,
    fg3m: Optional[float] = None,
    ftm: Optional[float] = None,
    ast: Optional[float] = None,
    oreb: Optional[float] = None,
    qast: Optional[float] = None,
    team_pts: Optional[float] = None,
    team_fgm: Optional[float] = None,
    team_fga: Optional[float] = None,
    team_fg3m: Optional[float] = None,
    team_ftm: Optional[float] = None,
    team_oreb: Optional[float] = None,
    team_scoring_poss: Optional[float] = None,
    orb_weight: Optional[float] = None,
    play_pct: Optional[float] = None,
) -> Optional[float]:
    """PProd - points produced (points scored plus points created for others).

    PProd_FG_Part  = 2 * (FGM + 0.5*3PM) * (1 - 0.5*((PTS - FTM)/(2*FGA)) * qAST)
    PProd_AST_Part = 2 * ((TmFGM - FGM + 0.5*(Tm3PM - 3PM)) / (TmFGM - FGM)) * 0.5
                       * (((TmPTS - TmFTM) - (PTS - FTM)) / (2*(TmFGA - FGA))) * AST
    PProd_ORB_Part = ORB * Team_ORB_Weight * Team_Play%
                       * (TmPTS / Team_ScoringPoss)
    PProd = (PProd_FG_Part + PProd_AST_Part + FTM)
              * (1 - (TmORB / Team_ScoringPoss) * Team_ORB_Weight * Team_Play%)
              + PProd_ORB_Part
    """
    values = _all(
        pts,
        fgm,
        fga,
        fg3m,
        ftm,
        ast,
        oreb,
        qast,
        team_pts,
        team_fgm,
        team_fga,
        team_fg3m,
        team_ftm,
        team_oreb,
        team_scoring_poss,
        orb_weight,
        play_pct,
    )
    if values is None:
        return None
    (
        points,
        made,
        attempts,
        threes,
        ft_made,
        assists,
        off_reb,
        q,
        tm_pts,
        tm_fgm,
        tm_fga,
        tm_fg3m,
        tm_ftm,
        tm_oreb,
        tm_scoring_poss,
        weight,
        play,
    ) = values

    if attempts == 0:
        pprod_fg = 0.0
    else:
        pprod_fg = (
            2.0 * (made + 0.5 * threes) * (1.0 - 0.5 * ((points - ft_made) / (2.0 * attempts)) * q)
        )

    teammate_fgm = tm_fgm - made
    teammate_fga = tm_fga - attempts
    three_mix = _div(teammate_fgm + 0.5 * (tm_fg3m - threes), teammate_fgm)
    teammate_pps = _div((tm_pts - tm_ftm) - (points - ft_made), 2.0 * teammate_fga)
    if three_mix is None or teammate_pps is None:
        return None
    pprod_ast = 2.0 * three_mix * 0.5 * teammate_pps * assists

    points_per_scoring_poss = _div(tm_pts, tm_scoring_poss)
    oreb_discount = _div(tm_oreb, tm_scoring_poss)
    if points_per_scoring_poss is None or oreb_discount is None:
        return None
    pprod_orb = off_reb * weight * play * points_per_scoring_poss

    return (pprod_fg + pprod_ast + ft_made) * (
        1.0 - oreb_discount * weight * play
    ) + pprod_orb


def offensive_rating(
    *,
    pts: Optional[float] = None,
    fgm: Optional[float] = None,
    fga: Optional[float] = None,
    fg3m: Optional[float] = None,
    ftm: Optional[float] = None,
    fta: Optional[float] = None,
    ast: Optional[float] = None,
    oreb: Optional[float] = None,
    tov: Optional[float] = None,
    minutes: Optional[float] = None,
    team_pts: Optional[float] = None,
    team_fgm: Optional[float] = None,
    team_fga: Optional[float] = None,
    team_fg3m: Optional[float] = None,
    team_ftm: Optional[float] = None,
    team_fta: Optional[float] = None,
    team_ast: Optional[float] = None,
    team_oreb: Optional[float] = None,
    team_tov: Optional[float] = None,
    team_minutes: Optional[float] = None,
    opp_dreb: Optional[float] = None,
) -> Optional[float]:
    """Individual Offensive Rating: ORtg = 100 * (PProd / TotPoss).

    Dean Oliver, *Basketball on Paper*; transcription per the
    Basketball-Reference glossary. **Points produced per 100 individual
    possessions**, not per 100 team possessions - a player's own possessions are
    the ones he ends by shooting, turning it over, or drawing the final foul,
    plus the ones he creates by assisting or offensive rebounding.

    Required inputs - every one of them, or the result is ``None``:
      * player: PTS, FGM, FGA, 3PM, FTM, FTA, AST, ORB, TOV, MP
      * team:   PTS, FGM, FGA, 3PM, FTM, FTA, AST, ORB, TOV, MP
      * opponent: DRB (to price the value of an offensive rebound)

    That input list is why the catalog marks ORtg ``estimated`` before 1996-97
    and unavailable before individual turnovers were recorded in 1977-78.
    """
    q = q_assist(
        ast=ast,
        fgm=fgm,
        minutes=minutes,
        team_ast=team_ast,
        team_fgm=team_fgm,
        team_minutes=team_minutes,
    )
    tm_scoring_poss = team_scoring_possessions(team_fgm, team_ftm, team_fta)
    tm_orb_pct = team_oreb_pct(team_oreb, opp_dreb)
    play = team_play_pct(tm_scoring_poss, team_fga, team_fta, team_tov)
    weight = team_oreb_weight(tm_orb_pct, play)

    scoring = scoring_possessions(
        fg_part(fgm, fga, pts, ftm, q),
        assist_part(ast, pts, ftm, fga, team_pts, team_ftm, team_fga),
        ft_part(ftm, fta),
        oreb_part(oreb, weight, play),
        team_oreb,
        tm_scoring_poss,
        weight,
        play,
    )
    total = total_possessions(
        scoring,
        missed_fg_possessions(fga, fgm, tm_orb_pct),
        missed_ft_possessions(ftm, fta),
        tov,
    )
    produced = points_produced(
        pts=pts,
        fgm=fgm,
        fga=fga,
        fg3m=fg3m,
        ftm=ftm,
        ast=ast,
        oreb=oreb,
        qast=q,
        team_pts=team_pts,
        team_fgm=team_fgm,
        team_fga=team_fga,
        team_fg3m=team_fg3m,
        team_ftm=team_ftm,
        team_oreb=team_oreb,
        team_scoring_poss=tm_scoring_poss,
        orb_weight=weight,
        play_pct=play,
    )
    rating = _div(produced, total)
    return None if rating is None else 100.0 * rating


# --------------------------------------------------------------------------- #
# Dean Oliver individual Defensive Rating
# --------------------------------------------------------------------------- #


def defensive_fg_pct(
    opp_fgm: Optional[float] = None, opp_fga: Optional[float] = None
) -> Optional[float]:
    """DFG% = OppFGM / OppFGA - the field goal percentage the defense allowed."""
    values = _all(opp_fgm, opp_fga)
    if values is None:
        return None
    return _div(values[0], values[1])


def defensive_oreb_pct(
    opp_oreb: Optional[float] = None, team_dreb: Optional[float] = None
) -> Optional[float]:
    """DOR% = OppORB / (OppORB + TmDRB) - the offensive rebound rate allowed."""
    values = _all(opp_oreb, team_dreb)
    if values is None:
        return None
    return _div(values[0], values[0] + values[1])


def missed_fg_weight(
    dfg_pct: Optional[float] = None, dor_pct: Optional[float] = None
) -> Optional[float]:
    """FMwt = (DFG% * (1 - DOR%)) / (DFG% * (1 - DOR%) + (1 - DFG%) * DOR%).

    How much of a stop to credit to forcing a miss versus securing the rebound.
    When opponents shoot well but rarely rebound their misses, the rebound is
    worth less and the contest is worth more.
    """
    values = _all(dfg_pct, dor_pct)
    if values is None:
        return None
    fg_pct, orb_pct = values
    numerator = fg_pct * (1.0 - orb_pct)
    return _div(numerator, numerator + (1.0 - fg_pct) * orb_pct)


def stops1(
    stl: Optional[float] = None,
    blk: Optional[float] = None,
    dreb: Optional[float] = None,
    fm_weight: Optional[float] = None,
    dor_pct: Optional[float] = None,
) -> Optional[float]:
    """Stops1 = STL + BLK * FMwt * (1 - 1.07*DOR%) + DRB * (1 - FMwt).

    The stops a player is individually credited with in the box score.
    """
    values = _all(stl, blk, dreb, fm_weight, dor_pct)
    if values is None:
        return None
    steals, blocks, def_reb, weight, orb_pct = values
    return steals + blocks * weight * (1.0 - 1.07 * orb_pct) + def_reb * (1.0 - weight)


def stops2(
    minutes: Optional[float] = None,
    pf: Optional[float] = None,
    fm_weight: Optional[float] = None,
    dor_pct: Optional[float] = None,
    team_minutes: Optional[float] = None,
    team_blk: Optional[float] = None,
    team_stl: Optional[float] = None,
    team_pf: Optional[float] = None,
    opp_fga: Optional[float] = None,
    opp_fgm: Optional[float] = None,
    opp_ftm: Optional[float] = None,
    opp_fta: Optional[float] = None,
    opp_tov: Optional[float] = None,
) -> Optional[float]:
    """Stops2 - the team's uncredited stops, shared out by playing time and fouls.

    Stops2 = (((OppFGA - OppFGM - TmBLK) / TmMP) * FMwt * (1 - 1.07*DOR%)
              + ((OppTOV - TmSTL) / TmMP)) * MP
             + (PF / TmPF) * 0.4 * OppFTA * (1 - (OppFTM/OppFTA))^2

    The first bracket is forced misses and turnovers that no individual got
    credit for, prorated by minutes; the last term is missed opponent free
    throws, prorated by the player's share of the team's fouls.
    """
    values = _all(
        pf, fm_weight, dor_pct, team_blk, team_stl, team_pf,
        opp_fga, opp_fgm, opp_ftm, opp_fta, opp_tov,
    )
    played = _positive(minutes)
    tm_minutes = _positive(team_minutes)
    if values is None or played is None or tm_minutes is None:
        return None
    (
        fouls,
        weight,
        orb_pct,
        tm_blk,
        tm_stl,
        tm_pf,
        opponent_fga,
        opponent_fgm,
        opponent_ftm,
        opponent_fta,
        opponent_tov,
    ) = values

    uncredited_misses = ((opponent_fga - opponent_fgm - tm_blk) / tm_minutes) * weight * (
        1.0 - 1.07 * orb_pct
    )
    uncredited_tov = (opponent_tov - tm_stl) / tm_minutes
    share_of_fouls = _div(fouls, tm_pf)
    if share_of_fouls is None:
        return None
    if opponent_fta == 0:
        ft_stops = 0.0
    else:
        ft_stops = share_of_fouls * 0.4 * opponent_fta * (1.0 - opponent_ftm / opponent_fta) ** 2
    return (uncredited_misses + uncredited_tov) * played + ft_stops


def stops(
    stops1_value: Optional[float] = None, stops2_value: Optional[float] = None
) -> Optional[float]:
    """Stops = Stops1 + Stops2."""
    values = _all(stops1_value, stops2_value)
    if values is None:
        return None
    return values[0] + values[1]


def stop_pct(
    stops_value: Optional[float] = None,
    minutes: Optional[float] = None,
    team_possessions: Optional[float] = None,
    opp_minutes: Optional[float] = None,
) -> Optional[float]:
    """Stop% = (Stops * OppMP) / (TmPoss * MP).

    The share of the opponent possessions a player faced that ended in a stop.
    ``opp_minutes`` is the opponent's total minutes played, which equals the
    team's own total minutes (both teams play the same game).
    """
    values = _all(stops_value, team_possessions, opp_minutes)
    played = _positive(minutes)
    if values is None or played is None:
        return None
    total_stops, tm_poss, opponent_minutes = values
    return _div(total_stops * opponent_minutes, tm_poss * played)


def d_pts_per_scoring_poss(
    opp_pts: Optional[float] = None,
    opp_fgm: Optional[float] = None,
    opp_ftm: Optional[float] = None,
    opp_fta: Optional[float] = None,
) -> Optional[float]:
    """D_Pts_per_ScPoss = OppPTS / (OppFGM + (1 - (1 - OppFT%)^2) * OppFTA * 0.4).

    Points the opponent scored per scoring possession - what a *failure* to stop
    them costs.
    """
    values = _all(opp_pts, opp_fgm, opp_ftm, opp_fta)
    if values is None:
        return None
    points, made, ft_made, ft_attempts = values
    scoring_poss = made if ft_attempts == 0 else (
        made + (1.0 - (1.0 - ft_made / ft_attempts) ** 2) * ft_attempts * 0.4
    )
    return _div(points, scoring_poss)


def team_defensive_rating(
    opp_pts: Optional[float] = None, team_possessions: Optional[float] = None
) -> Optional[float]:
    """Team DRtg = 100 * (OppPTS / TmPoss) - points allowed per 100 possessions."""
    values = _all(opp_pts, team_possessions)
    if values is None:
        return None
    rating = _div(values[0], values[1])
    return None if rating is None else 100.0 * rating


def defensive_rating(
    *,
    minutes: Optional[float] = None,
    stl: Optional[float] = None,
    blk: Optional[float] = None,
    dreb: Optional[float] = None,
    pf: Optional[float] = None,
    team_minutes: Optional[float] = None,
    team_stl: Optional[float] = None,
    team_blk: Optional[float] = None,
    team_dreb: Optional[float] = None,
    team_pf: Optional[float] = None,
    team_possessions: Optional[float] = None,
    opp_pts: Optional[float] = None,
    opp_fgm: Optional[float] = None,
    opp_fga: Optional[float] = None,
    opp_ftm: Optional[float] = None,
    opp_fta: Optional[float] = None,
    opp_tov: Optional[float] = None,
    opp_oreb: Optional[float] = None,
    opp_minutes: Optional[float] = None,
) -> Optional[float]:
    """Individual Defensive Rating, **points allowed per 100 possessions**.

    DRtg = TeamDRtg + 0.2 * (100 * D_Pts_per_ScPoss * (1 - Stop%) - TeamDRtg)

    Dean Oliver, *Basketball on Paper*; transcription per the
    Basketball-Reference glossary. The individual estimate is deliberately
    shrunk 80% toward the team's rating, because the box score sees only a
    fraction of defense.

    Required inputs, or the result is ``None``:
      * player: MP, STL, BLK, DRB, PF
      * team:   MP, STL, BLK, DRB, PF, possessions
      * opponent: PTS, FGM, FGA, FTM, FTA, TOV, ORB (MP defaults to the team's)

    ``opp_minutes`` defaults to ``team_minutes`` - the two teams are on the floor
    for the same game.
    """
    if opp_minutes is None:
        opp_minutes = team_minutes

    dor = defensive_oreb_pct(opp_oreb, team_dreb)
    dfg = defensive_fg_pct(opp_fgm, opp_fga)
    weight = missed_fg_weight(dfg, dor)

    total_stops = stops(
        stops1(stl, blk, dreb, weight, dor),
        stops2(
            minutes=minutes,
            pf=pf,
            fm_weight=weight,
            dor_pct=dor,
            team_minutes=team_minutes,
            team_blk=team_blk,
            team_stl=team_stl,
            team_pf=team_pf,
            opp_fga=opp_fga,
            opp_fgm=opp_fgm,
            opp_ftm=opp_ftm,
            opp_fta=opp_fta,
            opp_tov=opp_tov,
        ),
    )
    stop_percentage = stop_pct(total_stops, minutes, team_possessions, opp_minutes)
    team_rating = team_defensive_rating(opp_pts, team_possessions)
    points_per_scoring_poss = d_pts_per_scoring_poss(opp_pts, opp_fgm, opp_ftm, opp_fta)
    if stop_percentage is None or team_rating is None or points_per_scoring_poss is None:
        return None
    return team_rating + 0.2 * (
        100.0 * points_per_scoring_poss * (1.0 - stop_percentage) - team_rating
    )


def net_rating(
    off_rtg: Optional[float] = None, def_rtg: Optional[float] = None
) -> Optional[float]:
    """NetRtg = ORtg - DRtg, in points per 100 possessions."""
    values = _all(off_rtg, def_rtg)
    if values is None:
        return None
    return values[0] - values[1]


# --------------------------------------------------------------------------- #
# PIE, VORP, four factors
# --------------------------------------------------------------------------- #


#: The box-score events PIE weighs, as (column, coefficient) pairs.
_PIE_TERMS: tuple[tuple[str, float], ...] = (
    ("pts", 1.0),
    ("fgm", 1.0),
    ("ftm", 1.0),
    ("fga", -1.0),
    ("fta", -1.0),
    ("dreb", 1.0),
    ("oreb", 0.5),
    ("ast", 1.0),
    ("stl", 1.0),
    ("blk", 0.5),
    ("pf", -1.0),
    ("tov", -1.0),
)


def _pie_sum(line: Mapping[str, Any]) -> Optional[float]:
    """Sum the PIE numerator terms over one box-score line, or ``None`` if incomplete."""
    total = 0.0
    for column, coefficient in _PIE_TERMS:
        value = _column(line, column)
        if value is None:
            return None
        total += coefficient * value
    return total


def pie(
    player_stat_line: Optional[Mapping[str, Any]] = None,
    game_totals: Optional[Mapping[str, Any]] = None,
) -> Optional[float]:
    """Player Impact Estimate (NBA.com), returned as a fraction in ``[0, 1]``.

    Numerator (the player's line) and denominator (**both teams'** totals for the
    same games) use exactly the same expression::

        PTS + FGM + FTM - FGA - FTA + DREB + 0.5*OREB + AST + STL + 0.5*BLK - PF - TOV

    PIE = numerator / denominator. A league-average player lands near 0.100 and a
    dominant season near 0.200. ``game_totals`` must cover *all ten players on
    the floor* - both teams - otherwise the share is inflated; the caller is
    responsible for summing the two team lines, which
    :func:`compute_metric` does when given both ``team_row`` and
    ``opponent_row``.

    Every one of the twelve components is required on both lines: PIE needs
    steals, blocks, the rebound split and turnovers, so it does not exist before
    1977-78 and the catalog only offers it from 1996-97.
    """
    if player_stat_line is None or game_totals is None:
        return None
    numerator = _pie_sum(player_stat_line)
    denominator = _pie_sum(game_totals)
    return _div(numerator, denominator)


def vorp(
    bpm: Optional[float] = None,
    minutes: Optional[float] = None,
    team_minutes: Optional[float] = None,
    team_games: Optional[float] = None,
) -> Optional[float]:
    """VORP = (BPM + 2.0) * (MP / TmMP) * (TmG / 82).

    Value Over Replacement Player: points per 100 team possessions above a
    replacement-level player (-2.0), scaled by the share of team minutes played
    and prorated to an 82-game season.

    ``team_minutes`` is the team's *total* minutes (5 * 48 * games, plus
    overtime), so ``MP / TmMP`` is the player's share of the whole team's floor
    time - about 0.15 for a 36-minutes-a-night starter on a healthy team.
    Basketball-Reference's worked example: BPM +7.6 on 70% of team minutes over
    a full 82-game season gives (7.6 + 2.0) * 0.70 * 1.0 = 6.7.
    """
    values = _all(bpm, team_games)
    minute_share = _div(_f(minutes), _f(team_minutes))
    if values is None or minute_share is None:
        return None
    box_plus_minus, games = values
    return (box_plus_minus + 2.0) * minute_share * (games / 82.0)


class FourFactor(NamedTuple):
    """One of Oliver's four factors, with the weight he assigns it.

    ``value`` is a fraction in ``[0, 1]`` (or ``None`` when the inputs are
    missing), matching the ``four_factors`` widget payload in the contract.
    """

    key: str
    label: str
    weight: float
    value: Optional[float]


def four_factors(
    *,
    fgm: Optional[float] = None,
    fg3m: Optional[float] = None,
    fga: Optional[float] = None,
    fta: Optional[float] = None,
    tov: Optional[float] = None,
    oreb: Optional[float] = None,
    opp_dreb: Optional[float] = None,
) -> list[FourFactor]:
    """Oliver's four factors of basketball success, in their fixed order.

    ==============  ======  =====================================
    Factor          Weight  Formula
    ==============  ======  =====================================
    ``efg_pct``     0.40    (FGM + 0.5*3PM) / FGA
    ``tov_pct``     0.25    TOV / (FGA + 0.44*FTA + TOV)
    ``oreb_pct``    0.20    ORB / (ORB + OppDRB)
    ``ftr``         0.15    FTA / FGA
    ==============  ======  =====================================

    Always returns four entries in that order; an entry whose inputs are missing
    carries ``value=None`` rather than being dropped, so the widget can render a
    row with an em dash. Every value is a fraction in ``[0, 1]`` - including
    ``tov_pct``, which :func:`turnover_pct` reports on the 0-100 scale.
    """
    turnover_fraction = turnover_pct(tov, fga, fta)
    return [
        FourFactor(
            "efg_pct",
            "eFG%",
            FOUR_FACTOR_WEIGHTS["efg_pct"],
            effective_fg_pct(fgm, fg3m, fga),
        ),
        FourFactor(
            "tov_pct",
            "TOV%",
            FOUR_FACTOR_WEIGHTS["tov_pct"],
            None if turnover_fraction is None else turnover_fraction / 100.0,
        ),
        FourFactor(
            "oreb_pct",
            "OREB%",
            FOUR_FACTOR_WEIGHTS["oreb_pct"],
            team_oreb_pct(oreb, opp_dreb),
        ),
        FourFactor("ftr", "FTr", FOUR_FACTOR_WEIGHTS["ftr"], free_throw_rate(fta, fga)),
    ]


# --------------------------------------------------------------------------- #
# Per-mode conversion and rolling windows
# --------------------------------------------------------------------------- #


def per_mode_convert(
    value: Optional[float],
    minutes: Optional[float],
    games: Optional[float],
    mode: str,
    *,
    possessions_played: Optional[float] = None,
    team_pace: Optional[float] = None,
) -> Optional[float]:
    """Convert a **season total** of a counting stat into the requested per-mode.

    =============  ==================================================
    Mode           Result
    =============  ==================================================
    ``Totals``     ``value``
    ``PerGame``    ``value / games``
    ``Per36``      ``value * 36 / minutes``
    ``Per100``     ``value * 100 / possessions``
    =============  ==================================================

    ``Per100`` needs possessions, which a box-score total does not contain. Pass
    ``possessions_played`` when it is known; otherwise pass ``team_pace`` and the
    possessions are estimated as ``pace * minutes / 48`` - the player's share of
    his team's possessions while he was on the floor. With neither, the answer is
    ``None``, never a fabricated number.

    Raises ``ValueError`` for an unknown mode: that is a caller bug, not missing
    data. Mode matching is case-insensitive so ``"per36"`` and ``"Per36"`` agree.
    """
    normalized = {m.lower(): m for m in PER_MODES}.get(str(mode).lower())
    if normalized is None:
        raise ValueError(f"Unknown per-mode {mode!r}; expected one of {', '.join(PER_MODES)}")

    amount = _f(value)
    if amount is None:
        return None
    if normalized == "Totals":
        return amount
    if normalized == "PerGame":
        played = _positive(games)
        return _div(amount, played)
    if normalized == "Per36":
        played = _positive(minutes)
        return _div(amount * 36.0, played)

    poss = _positive(possessions_played)
    if poss is None:
        pace_value = _positive(team_pace)
        played = _positive(minutes)
        if pace_value is None or played is None:
            return None
        poss = pace_value * played / 48.0
    return _div(amount * 100.0, poss)


def rolling_average(
    values: Sequence[Optional[float]], window: int
) -> list[Optional[float]]:
    """Trailing rolling mean, one output per input, for the trend widget.

    ``result[i]`` is the mean of the non-``None`` values in
    ``values[i - window + 1 : i + 1]``. It is ``None`` for the first
    ``window - 1`` positions (the line only starts once the window is full) and
    ``None`` whenever every value in a full window is missing - a DNP stretch
    must not silently pull the average toward zero.

    A ``window`` larger than the series therefore yields all ``None``. Raises
    ``ValueError`` for ``window < 1``.
    """
    if window < 1:
        raise ValueError(f"window must be >= 1, got {window}")

    series = list(values)
    out: list[Optional[float]] = []
    for index in range(len(series)):
        if index + 1 < window:
            out.append(None)
            continue
        chunk = [_f(v) for v in series[index - window + 1 : index + 1]]
        present = [v for v in chunk if v is not None]
        out.append(sum(present) / len(present) if present else None)
    return out


# --------------------------------------------------------------------------- #
# Column vocabulary
# --------------------------------------------------------------------------- #

#: Canonical column name -> every spelling accepted on an input row. The first
#: entry is the canonical one. Names absent from this table are looked up as-is,
#: which is how precomputed columns such as ``bpm`` or ``ts_pct`` are read.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "min": ("min", "minutes", "minutes_played", "mp"),
    "pts": ("pts", "points"),
    "reb": ("reb", "rebounds", "trb", "total_rebounds"),
    "oreb": ("oreb", "offensive_rebounds", "orb"),
    "dreb": ("dreb", "defensive_rebounds", "drb"),
    "ast": ("ast", "assists"),
    "stl": ("stl", "steals"),
    "blk": ("blk", "blocks", "blocked_shots"),
    "tov": ("tov", "turnovers", "to"),
    "pf": ("pf", "personal_fouls", "fouls"),
    "fgm": ("fgm", "field_goals_made"),
    "fga": ("fga", "field_goals_attempted"),
    "fg3m": ("fg3m", "three_pointers_made", "fg3_m", "tpm"),
    "fg3a": ("fg3a", "three_pointers_attempted", "fg3_a", "tpa"),
    "ftm": ("ftm", "free_throws_made"),
    "fta": ("fta", "free_throws_attempted"),
    "gp": ("gp", "games_played", "g"),
    "gs": ("gs", "games_started"),
    "wins": ("wins", "w"),
    "losses": ("losses", "l"),
    "fantasy_pts": ("fantasy_pts", "nba_fantasy_pts"),
    "game_score": ("game_score", "gmsc"),
    "plus_minus": ("plus_minus", "plusminus", "plus_minus_pts"),
    "poss": ("poss", "possessions"),
    "season": ("season", "season_id", "year"),
}


def _column(row: Optional[Mapping[str, Any]], name: str) -> Optional[float]:
    """Read one numeric column from a row, trying every accepted spelling."""
    if row is None:
        return None
    for candidate in COLUMN_ALIASES.get(name, (name,)):
        if candidate in row:
            value = _f(row[candidate])
            if value is not None:
                return value
    return None


def _prefixed(
    row: Optional[Mapping[str, Any]], prefixes: Sequence[str], name: str
) -> Optional[float]:
    """Read ``<prefix><column>`` from a flattened row, e.g. ``team_fga``."""
    if row is None:
        return None
    for prefix in prefixes:
        for candidate in COLUMN_ALIASES.get(name, (name,)):
            key = f"{prefix}{candidate}"
            if key in row:
                value = _f(row[key])
                if value is not None:
                    return value
    return None


def _season_start_year(row: Optional[Mapping[str, Any]]) -> Optional[int]:
    """Parse the leading year out of a season string such as ``"1979-80"``."""
    if row is None:
        return None
    for candidate in COLUMN_ALIASES["season"]:
        raw = row.get(candidate)
        if raw is None:
            continue
        text = str(raw).strip()
        head = text.split("-")[0]
        if head.isdigit() and len(head) == 4:
            return int(head)
    return None


class _Ctx(NamedTuple):
    """The four rows a metric may draw on, with vocabulary-aware accessors."""

    row: Mapping[str, Any]
    team_row: Optional[Mapping[str, Any]]
    opponent_row: Optional[Mapping[str, Any]]
    league_row: Optional[Mapping[str, Any]]

    def v(self, name: str) -> Optional[float]:
        """A column of the subject's own row."""
        return _column(self.row, name)

    def t(self, name: str) -> Optional[float]:
        """A team column: from ``team_row``, else a ``team_``/``tm_`` prefix on the row."""
        value = _column(self.team_row, name)
        if value is None:
            value = _prefixed(self.row, ("team_", "tm_"), name)
        return value

    def o(self, name: str) -> Optional[float]:
        """An opponent column: from ``opponent_row``, else an ``opp_`` prefix on the row."""
        value = _column(self.opponent_row, name)
        if value is None:
            value = _prefixed(self.row, ("opp_", "opponent_"), name)
        return value

    def total_reb(self, scope: str) -> Optional[float]:
        """Total rebounds for a scope, falling back to ORB + DRB when TRB is absent."""
        getter = {"self": self.v, "team": self.t, "opp": self.o}[scope]
        total = getter("reb")
        if total is not None:
            return total
        parts = _all(getter("oreb"), getter("dreb"))
        return None if parts is None else parts[0] + parts[1]

    def threes_made(self, scope: str) -> Optional[float]:
        """3PM for a scope, treating a missing value as a true zero before 1979-80.

        Era honesty cuts both ways: a NULL 3PM in 2019 is unknown and must stay
        unknown, but a NULL 3PM in 1968 is a certainty - the line did not exist -
        and blanking eFG% for every pre-1980 season would be its own distortion.
        The substitution happens only when the row states its season.
        """
        getter = {"self": self.v, "team": self.t, "opp": self.o}[scope]
        made = getter("fg3m")
        if made is not None:
            return made
        year = _season_start_year(self.row)
        if year is not None and year < THREE_POINT_ERA_START_YEAR:
            return 0.0
        return None

    def threes_attempted(self, scope: str) -> Optional[float]:
        """3PA for a scope, treating a missing value as a true zero before 1979-80.

        The mirror of :meth:`threes_made`, and needed for the same reason: BLK%'s denominator
        is ``OppFGA - OppFG3A`` because only two-pointers are blockable, and before the line
        existed a NULL 3PA is a certainty rather than an unknown. Without this the catalog
        advertises BLK% from 1973-74 as "estimated" while the engine can only ever return
        ``None`` for it.
        """
        getter = {"self": self.v, "team": self.t, "opp": self.o}[scope]
        attempted = getter("fg3a")
        if attempted is not None:
            return attempted
        year = _season_start_year(self.row)
        if year is not None and year < THREE_POINT_ERA_START_YEAR:
            return 0.0
        return None

    def team_possessions(self) -> Optional[float]:
        """The team's possessions: the stored column if present, else the estimate."""
        stored = self.t("poss")
        if stored is not None:
            return stored
        return possessions(self.t("fga"), self.t("fta"), self.t("oreb"), self.t("tov"))

    def opp_possessions(self) -> Optional[float]:
        """The opponent's possessions: the stored column if present, else the estimate."""
        stored = self.o("poss")
        if stored is not None:
            return stored
        return possessions(self.o("fga"), self.o("fta"), self.o("oreb"), self.o("tov"))


# --------------------------------------------------------------------------- #
# Metric dispatch
# --------------------------------------------------------------------------- #


def _pct(value: Optional[float]) -> Optional[float]:
    """Convert a 0-100 rate to the contract's fraction in ``[0, 1]``."""
    return None if value is None else value / 100.0


def _direct(name: str) -> Callable[[_Ctx], Optional[float]]:
    """A metric that is a plain column read (a stored or model-fitted value)."""

    def read(ctx: _Ctx) -> Optional[float]:
        return ctx.v(name)

    return read


def _m_reb(ctx: _Ctx) -> Optional[float]:
    return ctx.total_reb("self")


def _m_efg_pct(ctx: _Ctx) -> Optional[float]:
    return effective_fg_pct(ctx.v("fgm"), ctx.threes_made("self"), ctx.v("fga"))


def _m_ts_pct(ctx: _Ctx) -> Optional[float]:
    return true_shooting_pct(ctx.v("pts"), ctx.v("fga"), ctx.v("fta"))


def _m_usg_pct(ctx: _Ctx) -> Optional[float]:
    return _pct(
        usage_pct(
            ctx.v("fga"),
            ctx.v("fta"),
            ctx.v("tov"),
            ctx.v("min"),
            ctx.t("min"),
            ctx.t("fga"),
            ctx.t("fta"),
            ctx.t("tov"),
        )
    )


def _m_ast_pct(ctx: _Ctx) -> Optional[float]:
    return _pct(
        assist_pct(ctx.v("ast"), ctx.v("min"), ctx.t("min"), ctx.t("fgm"), ctx.v("fgm"))
    )


def _m_reb_pct(ctx: _Ctx) -> Optional[float]:
    return _pct(
        rebound_pct(
            ctx.total_reb("self"),
            ctx.v("min"),
            ctx.t("min"),
            ctx.total_reb("team"),
            ctx.total_reb("opp"),
        )
    )


def _m_oreb_pct(ctx: _Ctx) -> Optional[float]:
    return _pct(
        offensive_rebound_pct(
            ctx.v("oreb"), ctx.v("min"), ctx.t("min"), ctx.t("oreb"), ctx.o("dreb")
        )
    )


def _m_dreb_pct(ctx: _Ctx) -> Optional[float]:
    return _pct(
        defensive_rebound_pct(
            ctx.v("dreb"), ctx.v("min"), ctx.t("min"), ctx.t("dreb"), ctx.o("oreb")
        )
    )


def _m_stl_pct(ctx: _Ctx) -> Optional[float]:
    return _pct(steal_pct(ctx.v("stl"), ctx.v("min"), ctx.t("min"), ctx.opp_possessions()))


def _m_blk_pct(ctx: _Ctx) -> Optional[float]:
    return _pct(
        block_pct(
            ctx.v("blk"),
            ctx.v("min"),
            ctx.t("min"),
            ctx.o("fga"),
            ctx.threes_attempted("opp"),
        )
    )


def _m_pace(ctx: _Ctx) -> Optional[float]:
    team_minutes = ctx.t("min")
    if team_minutes is None and ctx.team_row is None:
        team_minutes = ctx.v("min")
    return pace(ctx.team_possessions(), ctx.opp_possessions(), team_minutes)


def _m_poss(ctx: _Ctx) -> Optional[float]:
    """Possessions played.

    For a team subject (no separate ``team_row``) this is the team's own
    possession estimate. For a player inside a team it is the team's possessions
    scaled by the player's share of the floor, ``MP / (TmMP/5)``, which is the
    standard way NBA.com reports a player's possessions.
    """
    stored = ctx.v("poss")
    if stored is not None and ctx.team_row is None:
        return stored
    team_poss = ctx.team_possessions()
    if team_poss is None:
        return possessions(ctx.v("fga"), ctx.v("fta"), ctx.v("oreb"), ctx.v("tov"))
    raw_minutes = _f(ctx.v("min"))
    if raw_minutes is not None and raw_minutes <= 0:
        # A subject that did not take the floor used no possessions. That is a measured zero,
        # and the team's whole possession count is the one answer it cannot be. Without this
        # the `_positive` below folds "played 0:00" into "minutes unknown" and falls through
        # to `team_poss`, which is how a DNP leads the league in possessions.
        return 0.0
    minutes = _positive(raw_minutes)
    team_minutes = _positive(ctx.t("min"))
    if minutes is None or team_minutes is None:
        return team_poss
    return team_poss * minutes / (team_minutes / 5.0)


def _m_off_rtg(ctx: _Ctx) -> Optional[float]:
    computed = offensive_rating(
        pts=ctx.v("pts"),
        fgm=ctx.v("fgm"),
        fga=ctx.v("fga"),
        fg3m=ctx.threes_made("self"),
        ftm=ctx.v("ftm"),
        fta=ctx.v("fta"),
        ast=ctx.v("ast"),
        oreb=ctx.v("oreb"),
        tov=ctx.v("tov"),
        minutes=ctx.v("min"),
        team_pts=ctx.t("pts"),
        team_fgm=ctx.t("fgm"),
        team_fga=ctx.t("fga"),
        team_fg3m=ctx.threes_made("team"),
        team_ftm=ctx.t("ftm"),
        team_fta=ctx.t("fta"),
        team_ast=ctx.t("ast"),
        team_oreb=ctx.t("oreb"),
        team_tov=ctx.t("tov"),
        team_minutes=ctx.t("min"),
        opp_dreb=ctx.o("dreb"),
    )
    if computed is not None:
        return computed
    # A team subject has no individual ORtg: its offensive rating is simply
    # points per 100 of its own possessions.
    team_points = ctx.v("pts") if ctx.team_row is None else None
    own_poss = possessions(ctx.v("fga"), ctx.v("fta"), ctx.v("oreb"), ctx.v("tov"))
    if team_points is not None and own_poss is not None:
        rating = _div(team_points, own_poss)
        if rating is not None:
            return 100.0 * rating
    return ctx.v("off_rtg")


def _m_def_rtg(ctx: _Ctx) -> Optional[float]:
    computed = defensive_rating(
        minutes=ctx.v("min"),
        stl=ctx.v("stl"),
        blk=ctx.v("blk"),
        dreb=ctx.v("dreb"),
        pf=ctx.v("pf"),
        team_minutes=ctx.t("min"),
        team_stl=ctx.t("stl"),
        team_blk=ctx.t("blk"),
        team_dreb=ctx.t("dreb"),
        team_pf=ctx.t("pf"),
        team_possessions=ctx.team_possessions(),
        opp_pts=ctx.o("pts"),
        opp_fgm=ctx.o("fgm"),
        opp_fga=ctx.o("fga"),
        opp_ftm=ctx.o("ftm"),
        opp_fta=ctx.o("fta"),
        opp_tov=ctx.o("tov"),
        opp_oreb=ctx.o("oreb"),
    )
    if computed is not None:
        return computed
    if ctx.team_row is None:
        own_poss = possessions(ctx.v("fga"), ctx.v("fta"), ctx.v("oreb"), ctx.v("tov"))
        rating = team_defensive_rating(ctx.o("pts"), own_poss)
        if rating is not None:
            return rating
    return ctx.v("def_rtg")


def _m_net_rtg(ctx: _Ctx) -> Optional[float]:
    return net_rating(_m_off_rtg(ctx), _m_def_rtg(ctx))


def _m_pie(ctx: _Ctx) -> Optional[float]:
    """PIE against the totals of every player in the game.

    ``league_row`` is used verbatim when supplied (it is then the game or season
    total both teams generated); otherwise the two team rows are summed, which is
    exactly the denominator NBA.com uses.
    """
    totals: Optional[Mapping[str, Any]] = ctx.league_row
    if totals is None and ctx.team_row is not None and ctx.opponent_row is not None:
        combined: dict[str, float] = {}
        for column, _ in _PIE_TERMS:
            team_value = ctx.t(column)
            opp_value = ctx.o(column)
            if team_value is None or opp_value is None:
                return ctx.v("pie")
            combined[column] = team_value + opp_value
        totals = combined
    if totals is None:
        return ctx.v("pie")
    computed = pie(ctx.row, totals)
    return computed if computed is not None else ctx.v("pie")


def _m_game_score(ctx: _Ctx) -> Optional[float]:
    computed = game_score(
        ctx.v("pts"),
        ctx.v("fgm"),
        ctx.v("fga"),
        ctx.v("fta"),
        ctx.v("ftm"),
        ctx.v("oreb"),
        ctx.v("dreb"),
        ctx.v("stl"),
        ctx.v("ast"),
        ctx.v("blk"),
        ctx.v("pf"),
        ctx.v("tov"),
    )
    return computed if computed is not None else ctx.v("game_score")


def _m_fantasy_pts(ctx: _Ctx) -> Optional[float]:
    computed = fantasy_points(
        ctx.v("pts"),
        ctx.total_reb("self"),
        ctx.v("ast"),
        ctx.v("stl"),
        ctx.v("blk"),
        ctx.v("tov"),
    )
    return computed if computed is not None else ctx.v("fantasy_pts")


def _m_vorp(ctx: _Ctx) -> Optional[float]:
    computed = vorp(ctx.v("bpm"), ctx.v("min"), ctx.t("min"), ctx.t("gp"))
    return computed if computed is not None else ctx.v("vorp")


def _m_opp_efg_pct(ctx: _Ctx) -> Optional[float]:
    computed = effective_fg_pct(ctx.o("fgm"), ctx.threes_made("opp"), ctx.o("fga"))
    return computed if computed is not None else ctx.v("opp_efg_pct")


def _m_opp_tov_pct(ctx: _Ctx) -> Optional[float]:
    computed = _pct(turnover_pct(ctx.o("tov"), ctx.o("fga"), ctx.o("fta")))
    return computed if computed is not None else ctx.v("opp_tov_pct")


def _m_opp_oreb_pct(ctx: _Ctx) -> Optional[float]:
    team_dreb = ctx.t("dreb")
    if team_dreb is None and ctx.team_row is None:
        team_dreb = ctx.v("dreb")
    computed = team_oreb_pct(ctx.o("oreb"), team_dreb)
    return computed if computed is not None else ctx.v("opp_oreb_pct")


def _m_opp_ftr(ctx: _Ctx) -> Optional[float]:
    computed = free_throw_rate(ctx.o("fta"), ctx.o("fga"))
    return computed if computed is not None else ctx.v("opp_ftr")


def _m_win_pct(ctx: _Ctx) -> Optional[float]:
    values = _all(ctx.v("wins"), ctx.v("losses"))
    if values is None:
        return ctx.v("win_pct")
    return _div(values[0], values[0] + values[1])


def _stored_or(
    name: str, compute: Callable[[_Ctx], Optional[float]]
) -> Callable[[_Ctx], Optional[float]]:
    """Compute from components, falling back to a stored column of the same key.

    Ingested rows may already carry a precomputed advanced column (the NBA's own
    number). Components win when they are present, because they are internally
    consistent with everything else on the row; the stored value is the fallback.
    Stored percent columns are expected to be fractions, per the contract.
    """

    def resolve(ctx: _Ctx) -> Optional[float]:
        value = compute(ctx)
        return value if value is not None else ctx.v(name)

    return resolve


_METRIC_COMPUTERS: dict[str, Callable[[_Ctx], Optional[float]]] = {
    # Volume - direct box-score columns.
    "min": _direct("min"),
    "pts": _direct("pts"),
    "reb": _m_reb,
    "oreb": _direct("oreb"),
    "dreb": _direct("dreb"),
    "ast": _direct("ast"),
    "stl": _direct("stl"),
    "blk": _direct("blk"),
    "tov": _direct("tov"),
    "pf": _direct("pf"),
    "fgm": _direct("fgm"),
    "fga": _direct("fga"),
    "fg3m": _direct("fg3m"),
    "fg3a": _direct("fg3a"),
    "ftm": _direct("ftm"),
    "fta": _direct("fta"),
    "plus_minus": _direct("plus_minus"),
    "gp": _direct("gp"),
    "gs": _direct("gs"),
    "wins": _direct("wins"),
    "losses": _direct("losses"),
    "fantasy_pts": _m_fantasy_pts,
    # Shooting.
    "fg_pct": _stored_or("fg_pct", lambda c: _div(c.v("fgm"), c.v("fga"))),
    "fg3_pct": _stored_or("fg3_pct", lambda c: _div(c.v("fg3m"), c.v("fg3a"))),
    "ft_pct": _stored_or("ft_pct", lambda c: _div(c.v("ftm"), c.v("fta"))),
    "efg_pct": _stored_or("efg_pct", _m_efg_pct),
    "ts_pct": _stored_or("ts_pct", _m_ts_pct),
    "fg3a_rate": _stored_or(
        "fg3a_rate", lambda c: three_point_attempt_rate(c.v("fg3a"), c.v("fga"))
    ),
    "ftr": _stored_or("ftr", lambda c: free_throw_rate(c.v("fta"), c.v("fga"))),
    "pps": _stored_or("pps", lambda c: points_per_shot(c.v("pts"), c.v("fga"))),
    # Efficiency and usage.
    "off_rtg": _m_off_rtg,
    "def_rtg": _m_def_rtg,
    "net_rtg": _stored_or("net_rtg", _m_net_rtg),
    "usg_pct": _stored_or("usg_pct", _m_usg_pct),
    "ast_pct": _stored_or("ast_pct", _m_ast_pct),
    "ast_tov": _stored_or("ast_tov", lambda c: _div(c.v("ast"), c.v("tov"))),
    "ast_ratio": _stored_or(
        "ast_ratio", lambda c: assist_ratio(c.v("ast"), c.v("fga"), c.v("fta"), c.v("tov"))
    ),
    "oreb_pct": _stored_or("oreb_pct", _m_oreb_pct),
    "dreb_pct": _stored_or("dreb_pct", _m_dreb_pct),
    "reb_pct": _stored_or("reb_pct", _m_reb_pct),
    "tov_pct": _stored_or(
        "tov_pct", lambda c: _pct(turnover_pct(c.v("tov"), c.v("fga"), c.v("fta")))
    ),
    "stl_pct": _stored_or("stl_pct", _m_stl_pct),
    "blk_pct": _stored_or("blk_pct", _m_blk_pct),
    "pace": _stored_or("pace", _m_pace),
    "poss": _m_poss,
    # Impact.
    "pie": _m_pie,
    "game_score": _m_game_score,
    "per": _direct("per"),
    "ws": _direct("ws"),
    "ows": _direct("ows"),
    "dws": _direct("dws"),
    "ws48": _direct("ws48"),
    "bpm": _direct("bpm"),
    "obpm": _direct("obpm"),
    "dbpm": _direct("dbpm"),
    "vorp": _m_vorp,
    # Team defense / record.
    "opp_efg_pct": _m_opp_efg_pct,
    "opp_tov_pct": _m_opp_tov_pct,
    "opp_oreb_pct": _m_opp_oreb_pct,
    "opp_ftr": _m_opp_ftr,
    "win_pct": _m_win_pct,
}

#: Every metric key :func:`compute_metric` knows how to resolve. The test suite
#: asserts this matches ``contracts/metrics.json`` exactly, in both directions,
#: so the catalog and the engine cannot drift apart unnoticed.
SUPPORTED_METRIC_KEYS: frozenset[str] = frozenset(_METRIC_COMPUTERS)

#: Metric keys whose value must come from the row because no box score can
#: produce them: they are fitted against league-wide data by the ingest step
#: (PER is pace- and league-adjusted; Win Shares and BPM need league averages).
MODEL_FITTED_METRIC_KEYS: frozenset[str] = frozenset(
    {"per", "ws", "ows", "dws", "ws48", "bpm", "obpm", "dbpm", "plus_minus"}
)

#: Percent-formatted metrics, whose :func:`compute_metric` result is a fraction
#: in ``[0, 1]`` even where the underlying formula is on a 0-100 scale.
PERCENT_METRICS: frozenset[str] = frozenset(
    {
        "fg_pct",
        "fg3_pct",
        "ft_pct",
        "efg_pct",
        "ts_pct",
        "fg3a_rate",
        "ftr",
        "usg_pct",
        "ast_pct",
        "oreb_pct",
        "dreb_pct",
        "reb_pct",
        "tov_pct",
        "stl_pct",
        "blk_pct",
        "pie",
        "opp_efg_pct",
        "opp_tov_pct",
        "opp_oreb_pct",
        "opp_ftr",
        "win_pct",
    }
)


def compute_metric(
    key: str,
    *,
    row: Mapping[str, Any],
    team_row: Optional[Mapping[str, Any]] = None,
    opponent_row: Optional[Mapping[str, Any]] = None,
    league_row: Optional[Mapping[str, Any]] = None,
) -> Optional[float]:
    """Resolve one catalog metric key against a set of snake_case rows.

    This is the only entry point the API layer needs: no route, serializer or
    widget resolver should ever contain a formula.

    * ``row`` - the subject's box-score line (a player game, a player season, or
      a team season). Required.
    * ``team_row`` - the subject's team totals over the same games. Needed by
      every on-floor rate (USG%, AST%, rebound percentages, ORtg, DRtg, VORP).
      May be omitted when the subject *is* the team, or when the row already
      carries ``team_`` prefixed columns.
    * ``opponent_row`` - the opposing team's totals. Needed by rebound
      percentages, STL%, BLK%, pace, DRtg and every ``opp_*`` metric.
    * ``league_row`` - league or game totals; used as the PIE denominator when
      supplied.

    Returns the value in the unit the contract specifies: fractions in ``[0, 1]``
    for every percent-formatted metric, points per 100 possessions for ratings,
    and the raw count for volume stats. Returns ``None`` - never ``0`` - for an
    unknown key, a missing input or a zero denominator.
    """
    computer = _METRIC_COMPUTERS.get(key)
    if computer is None:
        return None
    return computer(
        _Ctx(row=row, team_row=team_row, opponent_row=opponent_row, league_row=league_row)
    )
