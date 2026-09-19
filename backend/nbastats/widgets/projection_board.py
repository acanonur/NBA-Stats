"""``projection_board`` — tonight's projected ranges, several players at a time.

Built from the ``Hardwood Predictions`` handoff, artboard 2b. See
:doc:`docs/BROADSHEET.md` for the design decisions, including the one that shaped this module
most: **the handoff drew each projection against a market quote, and this does not.** No price
and no implied probability appears anywhere here. NBA.com's terms forbid using their statistics
in connection with gambling, and the source monograph explicitly declines to claim anything
about market efficiency. The band's reference mark is the player's own season average instead,
which needs no third party and is the comparison the rest of the app already makes.

Every number on the board comes from :mod:`nbastats.widgets.next_game_projection`, by calling
its resolver rather than reimplementing the projection path. That is deliberate: a board and a
detail view that disagreed about the same player's projection would be worse than either alone.
"""
from __future__ import annotations

from datetime import date as date_type
from typing import Any, Optional, Sequence

from sqlalchemy import select

from .. import catalog
from ..models import PlayerGameBasic
from . import next_game_projection as single
from . import queries as q
from .base import ResolveContext, WidgetError, resolve_date

__all__ = ["resolve", "SCOPES", "REFERENCES", "DEFAULT_LIMIT", "DEFAULT_METRICS"]

SCOPES = ("favorites", "league", "team")
REFERENCES = ("season_average", "career_average", "none")
DEFAULT_LIMIT = 6
DEFAULT_METRICS: tuple[str, ...] = ("pts", "reb", "ast")

#: How many players to project before ranking. Each one runs a full single-player projection,
#: so this bounds the work; the board then keeps the `limit` most interesting rows.
CANDIDATE_CAP = 14

_REFERENCE_LABEL = {
    "season_average": "season avg",
    "career_average": "career avg",
    "none": None,
}


def resolve(config: dict[str, Any], ctx: ResolveContext) -> tuple[dict[str, Any], str, list[str]]:
    """Resolve one ``projection_board``."""
    notes: list[str] = []

    metrics = _requested_metrics(config.get("metrics"), notes)
    limit = max(1, int(config.get("limit") or DEFAULT_LIMIT))
    min_minutes = float(config.get("minMinutes") or 0.0)
    scope = config.get("scope") if config.get("scope") in SCOPES else "favorites"
    reference = config.get("reference") if config.get("reference") in REFERENCES else "season_average"

    day = resolve_date(ctx, config.get("date"), field="date")
    if day is None:
        day = ctx.as_of or date_type.today()
        notes.append("No completed game is loaded, so there is no slate to project against.")
        return _payload(day, reference, [], notes), "unavailable", notes

    # The era gate is the projection's, not the metric's. Points go back to 1946-47, so asking
    # whether ``pts`` is available would wave through a 1993 slate and then quietly build every
    # band with f_pace and f_opp missing. ``next_game_projection`` owns that rule, and the board
    # asks it rather than keeping a second copy that could drift.
    season = _slate_season(ctx, day)
    era_note = single.era_gap_note(season)
    if era_note:
        notes.append(era_note)
        return _payload(day, reference, [], notes), "unavailable", notes

    candidates = _candidates(config, ctx, day, scope, notes)
    if not candidates:
        notes.append("No player on this slate has enough history to project.")
        return _payload(day, reference, [], notes), "partial", notes

    rows: list[dict[str, Any]] = []
    for player_id in candidates[:CANDIDATE_CAP]:
        rows.extend(_rows_for_player(player_id, metrics, reference, min_minutes, ctx))

    # The reason to look at a board at all is disagreement with a player's own baseline, so
    # that is what ranks it — but in units of the projection's own spread, never raw. A board
    # ranked by raw delta is a board of points: a 1.3-point move outranks a 1.2-rebound one
    # purely because points are a bigger number, and a board asking for pts/reb/ast would
    # return six rows of pts every night. Standardising fixes that (see _rank_score).
    #
    # With no reference mark there is nothing to disagree with, so the signal-to-noise of the
    # projection itself is the order — which is scale-free for the same reason.
    rows.sort(key=lambda r: -_rank_score(r, reference))

    kept = rows[:limit]
    if not kept:
        notes.append("No projection cleared the minimum minutes filter.")
    return _payload(day, reference, kept, notes), "estimated", notes


# --------------------------------------------------------------------------- candidates


def _candidates(
    config: dict[str, Any], ctx: ResolveContext, day: date_type, scope: str, notes: list[str]
) -> list[int]:
    """Player ids worth projecting, in a deterministic order."""
    explicit = [int(v) for v in (config.get("playerIds") or []) if isinstance(v, (int, float))]
    if explicit:
        return explicit

    if scope == "team":
        team_id = config.get("teamId")
        if team_id is None:
            notes.append("No team is set, so the board fell back to the whole slate.")
        else:
            return _slate_players(ctx, day, team_id=int(team_id))

    if scope == "favorites":
        favourite = ctx.favorite_player_id
        if favourite is not None:
            return [int(favourite)]
        notes.append("No favourite player is set, so the board shows the slate's busiest players.")

    return _slate_players(ctx, day)


def _slate_players(
    ctx: ResolveContext, day: date_type, team_id: Optional[int] = None
) -> list[int]:
    """Players who appeared on the most recent slate, busiest first.

    Minutes are the ranking because minutes are what the projection is most sensitive to: a
    board of deep-bench players would be a board of very wide, very uninteresting bands.
    """
    games = [g for g in q.slate_games(ctx, day)]
    if not games:
        return []
    game_ids = [g.game_id for g in games]
    stmt = (
        select(PlayerGameBasic.player_id, PlayerGameBasic.minutes)
        .where(PlayerGameBasic.game_id.in_(game_ids))
        .order_by(PlayerGameBasic.minutes.desc())
    )
    if team_id is not None:
        stmt = stmt.where(PlayerGameBasic.team_id == team_id)
    seen: list[int] = []
    for player_id, _minutes in ctx.session.execute(stmt).all():
        if player_id not in seen:
            seen.append(int(player_id))
        if len(seen) >= CANDIDATE_CAP:
            break
    return seen


# --------------------------------------------------------------------------- rows


def _rows_for_player(
    player_id: int,
    metrics: Sequence[str],
    reference: str,
    min_minutes: float,
    ctx: ResolveContext,
) -> list[dict[str, Any]]:
    """One row per requested metric, by running the single-player projection."""
    try:
        payload, availability, _notes = single.resolve(
            {
                "playerId": player_id,
                "stats": list(metrics),
                "interval": "80",
                "showCombo": False,
                "showFactors": False,
            },
            ctx,
        )
    except WidgetError:
        return []
    except Exception:  # noqa: BLE001 - one unprojectable player must not empty the board
        return []

    if availability == "unavailable":
        return []

    minutes = ((payload.get("projectedMinutes") or {}).get("value")) or 0.0
    if minutes < min_minutes:
        return []

    player = payload.get("player") or {}
    game = payload.get("game") or {}
    matchup = _matchup(player, game)

    rows: list[dict[str, Any]] = []
    for line in payload.get("lines") or []:
        mean = line.get("mean")
        if mean is None:
            continue
        ref_value = _reference_value(line, reference)
        rows.append(
            {
                "player": player,
                "matchup": matchup,
                "opponentAbbr": game.get("opponentAbbr"),
                "isHome": game.get("isHome"),
                "gameId": game.get("gameId"),
                "gameDate": game.get("date"),
                "metric": line.get("metric"),
                "descriptor": line.get("descriptor"),
                "projection": mean,
                "displayValue": line.get("displayValue"),
                "low": line.get("low"),
                "high": line.get("high"),
                "intervalLevel": line.get("intervalLevel"),
                "referenceValue": ref_value,
                "delta": None if ref_value is None else round(mean - ref_value, 3),
                "deltaZ": _delta_z(mean, ref_value, line),
                "projectedMinutes": round(minutes, 1),
                "availability": line.get("availability", "estimated"),
            }
        )
    return rows


#: Half-width of an 80% normal interval in standard deviations — ``z(0.9)``. The engine's
#: interval is a discrete negative-binomial one, so dividing its width by this recovers the
#: spread only approximately; approximately is all a *ranking* needs.
_Z90 = 1.2816


def _interval_sd(line: dict[str, Any]) -> Optional[float]:
    """The projection's spread, read back out of the interval the engine already published."""
    low, high = line.get("low"), line.get("high")
    if low is None or high is None:
        return None
    width = float(high) - float(low)
    if width <= 0:
        return None
    return width / (2.0 * _Z90)


def _delta_z(mean: float, ref_value: Optional[float], line: dict[str, Any]) -> Optional[float]:
    """``delta`` in units of a single game's predictive spread.

    This is what makes a points row and a rebounds row comparable. It is *not* a claim that
    the shift is significant: a 0.3 here means tonight's projection sits three tenths of one
    game's worth of noise from the player's own average, which is a small move, and the board
    saying so beats the board implying otherwise by ranking raw points first.
    """
    if ref_value is None:
        return None
    sd = _interval_sd(line)
    if not sd:
        return None
    return round((mean - ref_value) / sd, 3)


def _rank_score(row: dict[str, Any], reference: str) -> float:
    """How interesting a row is, on a scale that means the same thing for every statistic.

    With a reference mark that is ``|deltaZ|``; without one it is the projection over its own
    spread. A row the engine could not put an interval on scores 0 and sinks — it is the row
    the board knows least about, so it is the row to drop when only six fit.
    """
    if reference != "none":
        return abs(row.get("deltaZ") or 0.0)
    sd = _interval_sd(row)
    if not sd:
        return 0.0
    return (row.get("projection") or 0.0) / sd


def _reference_value(line: dict[str, Any], reference: str) -> Optional[float]:
    if reference == "none":
        return None
    if reference == "career_average":
        # The single-player payload carries the season baseline; a career one would need a
        # second query per player per metric, which is not worth it for a six-row board.
        # Falling back is honest and the label says which mark is drawn.
        return line.get("seasonAverage")
    return line.get("seasonAverage")


def _matchup(player: dict[str, Any], game: dict[str, Any]) -> Optional[str]:
    """``LAL @ DEN`` or ``BOS vs NYK``, the way the design writes it."""
    team = player.get("teamAbbr")
    opponent = game.get("opponentAbbr")
    if not team or not opponent:
        return None
    return f"{team} {'vs' if game.get('isHome') else '@'} {opponent}"


# --------------------------------------------------------------------------- helpers


def _requested_metrics(requested: Any, notes: list[str]) -> list[str]:
    keys = [k for k in (requested or []) if isinstance(k, str)]
    known = [k for k in keys if catalog.has_metric(k)]
    dropped = [k for k in keys if k not in known]
    if dropped:
        notes.append(f"Unknown metric dropped: {', '.join(sorted(dropped))}.")
    return known or list(DEFAULT_METRICS)


def _slate_season(ctx: ResolveContext, day: date_type) -> str:
    games = q.slate_games(ctx, day)
    for game in games:
        if game.season:
            return str(game.season)
    return ctx.season


def _payload(
    selection_day: date_type,
    reference: str,
    rows: list[dict[str, Any]],
    notes: list[str],
) -> dict[str, Any]:
    """The board's envelope.

    ``date`` is the day of the games being **projected**, which is not the day the candidate
    list came from: players are chosen off the last completed slate and then projected forward
    to whatever each one plays next. Reporting the selection day as ``date`` would put last
    night's date above tomorrow night's numbers, so both are named. Players' next games do not
    all fall on the same night either, hence ``throughDate``, which is null when they do.
    """
    dates = sorted({row["gameDate"] for row in rows if row.get("gameDate")})
    first = dates[0] if dates else selection_day.isoformat()
    last = dates[-1] if dates else None
    return {
        "date": first,
        "throughDate": last if last and last != first else None,
        "selectionDate": selection_day.isoformat(),
        "gameCount": len({row["gameId"] for row in rows if row.get("gameId")}),
        "reference": reference,
        "referenceLabel": _REFERENCE_LABEL.get(reference),
        "rows": rows,
        # The design's caption said "10,000 simulated games". Ours is analytic, and saying so
        # costs nothing while claiming a simulation we did not run would be a small lie.
        "note": (
            "The band is the model's 10th to 90th percentile from a negative binomial fit; "
            "the dot is the projection."
        ),
    }
