"""Game projections, the slate, the per-game detail and the review: payloads built from the model.

A game's projection is shown in one of three forms and the payload always says which
(``model.kind``): ``latest`` (the model, now, for a game before its lock deadline), ``locked``
(frozen before it, read back from the ledger exactly as it was written) or ``reconstructed`` (the
model rebuilt from inputs strictly before the deadline, for a game that has no frozen row).
:func:`~nbastats.nba_matchup.projection.current_projection` decides which applies; this module
only describes it.

What a client is told, every time
---------------------------------
* ``availability: "estimated"``: a projection is an estimate, always. A slate or a game detail
  with nothing to show says ``unavailable`` instead (an era before 1996-97, a pre-season game, a
  store with nothing to rate the teams from) and its notes say which.
* ``intervalBasis``: ``null`` until a spread has been fitted (and the range is then ``null`` too,
  a dash on screen), ``fittedPrevSeason`` or ``fittedLedger`` once one has, ``assumed`` for a
  spread a person typed in. The EuroLeague's workbook spreads are never borrowed.
* ``assumptions.assumedAvailable``: how many players the model assumed would play only because it
  had no usable entry for them, and why (``notOnSubmittedReport``, ``teamReportPending`` or
  ``noReportPublished``). A projection that quietly treated silence as health would be telling a
  small lie; this is the correction. ``staleEntriesIgnored`` counts entries the model set aside
  because they no longer drive anything.
* ``venueAssumed``: always true for the NBA, which records no neutral sites.
* ``model.constants``: every constant used with its provenance, so a number somebody typed, a
  number Hardwood fitted and a number nobody has checked are not blurred.
* A frozen projection says that player detail was not frozen with it: its absences, cap flags and
  assumption counts are ``null``/empty, because they were not recorded.
* Playoff and play-in games say they are projected from the regular season's ratings.

The review
----------
``GET /v1/projections/review`` answers one question honestly: *when the model said a score before
the game, how far off was it?* ``locked`` rows are the only ones that measure the model as a
forecaster. A final game with no locked row gets the model's rebuilt projection, reported in a
**separate** block (``reconstructed``), because "the model called it" means something different when
the model was asked afterwards. A game with neither is not invented: the notes say how many were
never frozen. A projection with a margin under half a point is a toss-up: it names no winner, is
counted in ``tossUps`` and is excluded from ``winnersCalled``'s denominator (``decidedGames``).
``marginMiss`` is the actual margin less the projected one, and the means are the average size of
the miss in points. Nothing here compares a projection with an outside number.

There is no probability of winning anywhere in these payloads.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Final, Sequence

from sqlalchemy import select

from ..models import Game, TeamProjectionLedger
from ..shared import refs
from ..shared.team_projection import TOSS_UP_MARGIN, interval80, is_toss_up, summary_text
from .projection import (
    MODEL_KEY,
    MODEL_VERSION,
    ProjectionResult,
    ProjectionUnavailable,
    ProjectionView,
    SideResult,
    can_project,
    current_projection,
    get_model,
    interval_basis,
    ledger_rows_for,
    model_constants,
    side_absences,
)
from .queries import (
    PROJECTABLE_SEASON_TYPES,
    ReadContext,
    game_has_result,
)

__all__ = [
    "NOTE_NO_FROZEN",
    "model_block",
    "projection_payload",
    "game_projection_payload",
    "projection_detail",
    "build_slate",
    "ReviewRow",
    "metrics",
    "review_rows",
    "review_blocks",
    "build_review",
]

NOTE_NO_FROZEN: Final = "No projection was recorded before tip-off"


# --------------------------------------------------------------------------- the model block


def _cap_policy(ctx: ReadContext) -> str:
    return "consistent" if ctx.setting("capPolicyConsistent") >= 0.5 else "workbook"


def model_block(
    ctx: ReadContext,
    *,
    kind: str,
    computed_at: datetime | None,
    inputs_cutoff: datetime | None,
    version: str,
    league_level: float | None = None,
    include_constants: bool = True,
) -> dict[str, Any]:
    model = None
    try:
        model = get_model(ctx)
    except ProjectionUnavailable:
        pass
    return {
        "key": MODEL_KEY,
        "version": version,
        "kind": kind,
        "computedAt": refs.rfc3339(computed_at),
        "inputsCutoff": refs.rfc3339(inputs_cutoff),
        "capPolicy": _cap_policy(ctx),
        "constants": model_constants(ctx, model, league_level) if include_constants else [],
    }


# --------------------------------------------------------------------------- one projection


def _range(value: tuple[float, float] | None) -> dict[str, float] | None:
    return None if value is None else {"low": value[0], "high": value[1]}


def _result_block(game: Game, home: float, away: float) -> dict[str, Any] | None:
    if not game_has_result(game):
        return None
    projected = home - away
    called: bool | None
    if is_toss_up(projected):
        called = None
    else:
        called = (projected > 0) == (game.home_pts > game.away_pts)
    return {
        "homePts": game.home_pts,
        "awayPts": game.away_pts,
        "marginMiss": (game.home_pts - game.away_pts) - projected,
        "winnerCalled": called,
    }


def _side_from_result(
    ctx: ReadContext,
    side: SideResult,
    projected: float,
    full_strength: float,
    after_availability: float,
    range80: tuple[float, float] | None,
    as_of: datetime,
) -> dict[str, Any]:
    injury = side.injury
    return {
        "team": ctx.team_ref(side.team_id),
        "projectedPoints": projected,
        "range80": _range(range80),
        "fullStrengthPoints": full_strength,
        "availabilityEffect": projected - full_strength,
        "attackIndex": side.attack,
        "attackIndexAfterAvailability": after_availability,
        "defenceIndex": side.defence,
        "capBinding": injury.cap_binding if injury is not None else False,
        "unassignedPoints": injury.unassigned_points if injury is not None else 0.0,
        "replacementPoints": injury.replacement_credited if injury is not None else 0.0,
        "keyAbsences": side_absences(ctx, side, as_of),
    }


def _basis_of(result: ProjectionResult) -> tuple[str, str, str]:
    """``(basis, home basis, away basis)`` of the assumed-available count.

    The two sides can stand on different footings (one team has filed, the other has not), so each
    is stated, and the headline ``basis`` is the *least informed* of the two: the one that
    qualifies the model's assumption most strongly.
    """
    rank = {"notOnSubmittedReport": 0, "teamReportPending": 1, "noReportPublished": 2}
    home, away = result.home.assumed_basis, result.away.assumed_basis
    worst = max((home, away), key=lambda b: rank.get(b, 0))
    return worst, home, away


def projection_payload(
    ctx: ReadContext,
    game: Game,
    view: ProjectionView,
    freshness: dict[str, Any],
    league_level: float | None = None,
) -> dict[str, Any]:
    """``GameProjection`` for ``game`` in the form ``view`` describes."""
    home_ref, away_ref = ctx.team_ref(game.home_team_id), ctx.team_ref(game.away_team_id)
    home_abbr, away_abbr = ctx.team_abbr(game.home_team_id), ctx.team_abbr(game.away_team_id)
    notes: list[str]
    if view.result is not None:
        result = view.result
        match = result.match
        home_side = _side_from_result(
            ctx, result.home, match.home_points, match.home_full_strength,
            match.home_attack_after_availability, match.home_range80, result.as_of,
        )  # fmt: skip
        away_side = _side_from_result(
            ctx, result.away, match.away_points, match.away_full_strength,
            match.away_attack_after_availability, match.away_range80, result.as_of,
        )  # fmt: skip
        winner = match.projected_winner
        home_pts, away_pts = match.home_points, match.away_points
        margin_range = match.margin_range80
        combined, combined_effect = match.combined_points, match.combined_availability_effect
        advantage = result.home_advantage.points
        basis, home_basis, away_basis = _basis_of(result)
        assumptions: dict[str, Any] = {
            "assumedAvailable": {
                "home": result.home.assumed_available,
                "away": result.away.assumed_available,
                "basis": basis,
                "homeBasis": home_basis,
                "awayBasis": away_basis,
            },
            "staleEntriesIgnored": result.home.stale_ignored + result.away.stale_ignored,
        }
        model = model_block(
            ctx,
            kind=view.kind,
            computed_at=ctx.now,
            inputs_cutoff=result.as_of,
            version=MODEL_VERSION,
            league_level=result.league_level,
        )
        notes = [
            "Defence indices are not changed by absences: absences move the projected scores only.",
            "Players with no status in force are assumed to play; see "
            "assumptions.assumedAvailable.",
            "Absences are listed by season averages, not by the engine's own player projections.",
        ]
        if view.kind == "reconstructed":
            notes.append(
                "Rebuilt after the lock deadline from inputs recorded before it; no projection was "
                "frozen."
            )
        interval = interval_basis(ctx)
    else:
        row: TeamProjectionLedger = view.ledger  # type: ignore[assignment]
        home_pts, away_pts = row.home_pts, row.away_pts
        winner = (
            None if is_toss_up(home_pts - away_pts) else ("home" if home_pts > away_pts else "away")
        )
        home_range, away_range = interval80(home_pts, row.team_sd), interval80(
            away_pts, row.team_sd
        )
        margin_range = interval80(home_pts - away_pts, row.margin_sd)
        full_home, full_away = row.home_full_strength, row.away_full_strength

        def frozen_side(
            ref: dict[str, Any],
            projected: float,
            full: float,
            attack: float,
            factor: float,
            defence: float,
            rng: tuple[float, float] | None,
        ) -> dict[str, Any]:
            return {
                "team": ref,
                "projectedPoints": projected,
                "range80": _range(rng),
                "fullStrengthPoints": full,
                "availabilityEffect": projected - full,
                "attackIndex": attack,
                "attackIndexAfterAvailability": attack * factor,
                "defenceIndex": defence,
                "capBinding": None,
                "unassignedPoints": None,
                "replacementPoints": None,
                "keyAbsences": [],
            }

        home_side = frozen_side(
            home_ref, home_pts, full_home, row.home_attack_index,
            row.home_availability_factor, row.home_defence_index, home_range,
        )  # fmt: skip
        away_side = frozen_side(
            away_ref, away_pts, full_away, row.away_attack_index,
            row.away_availability_factor, row.away_defence_index, away_range,
        )  # fmt: skip
        combined = home_pts + away_pts
        combined_effect = combined - (full_home + full_away)
        advantage = row.home_advantage_points
        assumptions = {
            "assumedAvailable": {"home": None, "away": None, "basis": None},
            "staleEntriesIgnored": None,
        }
        model = model_block(
            ctx,
            kind=row.kind,
            computed_at=row.computed_at,
            inputs_cutoff=row.inputs_cutoff,
            version=row.model_version,
            include_constants=False,
        )
        notes = ["Player detail was not frozen with this projection, so absences are not listed."]
        interval = (
            None
            if row.team_sd is None or row.margin_sd is None
            else (interval_basis(ctx) or "assumed")
        )
    if game.season_type != "Regular Season":
        notes.append(f"{game.season_type} games are projected from the regular season's ratings.")
    if margin_range is None:
        notes.append("No spread has been fitted yet, so no range is shown.")
    margin = home_pts - away_pts
    return {
        "league": "nba",
        "game": ctx.game_ref(game),
        "freshness": freshness,
        "home": home_side,
        "away": away_side,
        "margin": margin,
        "marginRange80": _range(margin_range),
        "projectedWinner": None if winner is None else (home_ref if winner == "home" else away_ref),
        "isTossUp": is_toss_up(margin),
        "summary": summary_text(margin, home_abbr, away_abbr),
        "combinedPoints": combined,
        "combinedAvailabilityEffect": combined_effect,
        "homeAdvantagePoints": advantage,
        "venueAssumed": True,
        "intervalBasis": interval,
        "model": model,
        "assumptions": assumptions,
        "result": _result_block(game, home_pts, away_pts),
        "availability": "estimated",
        "notes": notes,
    }


def game_projection_payload(
    ctx: ReadContext, game: Game, freshness: dict[str, Any]
) -> dict[str, Any] | None:
    """The projection shown for ``game``, or ``None`` when there is no honest one."""
    try:
        model = get_model(ctx)
    except ProjectionUnavailable:
        return None
    view = current_projection(ctx, model, game)
    if view is None:
        return None
    level = view.result.league_level if view.result is not None else model.league_level(model.final)
    return projection_payload(ctx, game, view, freshness, level)


def projection_detail(ctx: ReadContext, game: Game, freshness: dict[str, Any]) -> dict[str, Any]:
    """``GameProjectionDetail``: the current projection, the frozen one and the history."""
    try:
        model = get_model(ctx)
    except ProjectionUnavailable:
        model = None
    rows = ledger_rows_for(ctx, game.game_id)
    current = locked = None
    level = model.league_level(model.final) if model is not None else None
    if model is not None:
        view = current_projection(ctx, model, game)
        if view is not None and view.kind == "locked":
            locked = projection_payload(ctx, game, view, freshness, level)
        elif view is not None:
            current = projection_payload(ctx, game, view, freshness, level)
            if view.result is not None:
                level = view.result.league_level
    if locked is None:
        frozen = [r for r in rows if r.kind == "locked"]
        if frozen:
            locked = projection_payload(
                ctx, game, ProjectionView("locked", ledger=frozen[-1]), freshness, level
            )
    notes: list[str] = []
    if current is None and locked is None:
        notes.append(
            can_project(ctx.season, game.season_type)
            or "This game has no honest projection: nothing to start the ratings from yet."
        )
    return {
        "league": "nba",
        "game": ctx.game_ref(game),
        "freshness": freshness,
        "current": current,
        "locked": locked,
        # ``estimated`` when there is a projection to show; ``unavailable`` when there cannot be
        # one (before 1996-97, a pre-season game, a store with nothing to rate teams from).
        "availability": "estimated" if (current or locked) else "unavailable",
        "history": [
            {
                "computedAt": refs.rfc3339(r.computed_at),
                "kind": r.kind,
                "homePts": r.home_pts,
                "awayPts": r.away_pts,
            }
            for r in rows
        ],
        "notes": notes,
    }


# --------------------------------------------------------------------------- the slate


def build_slate(
    ctx: ReadContext,
    day: date | None,
    team_ids: Sequence[int],
    freshness: dict[str, Any],
) -> dict[str, Any]:
    """``SlateProjections`` for the games on ``day``, optionally only those of ``team_ids``."""
    games = sorted(
        (
            g
            for g in ctx.games
            if day is not None
            and g.game_date == day
            and g.season_type in PROJECTABLE_SEASON_TYPES
            and g.status != "postponed"
            and (not team_ids or g.home_team_id in team_ids or g.away_team_id in team_ids)
        ),
        key=lambda g: (ctx.start_of(g), g.game_id),
    )
    projected: list[dict[str, Any]] = []
    missing = 0
    for game in games:
        payload = game_projection_payload(ctx, game, freshness)
        if payload is None:
            missing += 1
        else:
            projected.append(payload)
    level: float | None = None
    try:
        model = get_model(ctx)
        level = model.league_level(model.final)
    except ProjectionUnavailable:
        model = None
    blocks = review_blocks(review_rows(ctx))
    has_review = bool(blocks["byModel"]) or blocks["reconstructed"] is not None
    notes = ["Projections are estimates. Players with no status in force are assumed to play."]
    if day is None:
        notes.append("No game is scheduled, so there is nothing to project.")
    elif not games:
        notes.append(f"No game is scheduled on {day.isoformat()}.")
    if missing:
        reason = can_project(ctx.season, "Regular Season") or "a team has no rating yet"
        notes.append(f"{missing} game(s) have no projection: {reason}.")
    return {
        "league": "nba",
        "date": refs.iso_date(day),
        "round": None,
        "freshness": freshness,
        "model": model_block(
            ctx,
            kind="latest",
            computed_at=ctx.now,
            inputs_cutoff=ctx.now,
            version=MODEL_VERSION,
            league_level=level,
        ),
        "games": projected,
        "review": blocks if has_review else None,
        "availability": "estimated" if projected else "unavailable",
        "notes": notes,
    }


# --------------------------------------------------------------------------- the review


class ReviewRow:
    """One game's projected scores against its result."""

    __slots__ = ("game", "kind", "model_key", "home", "away")

    def __init__(self, game: Game, kind: str, home: float, away: float) -> None:
        self.game, self.kind, self.model_key, self.home, self.away = (
            game,
            kind,
            MODEL_KEY,
            home,
            away,
        )

    @property
    def margin_miss(self) -> float:
        return (self.game.home_pts - self.game.away_pts) - (self.home - self.away)

    @property
    def projected_winner(self) -> str | None:
        margin = self.home - self.away
        if abs(margin) < TOSS_UP_MARGIN:
            return None
        return "home" if margin >= 0 else "away"

    @property
    def winner_called(self) -> bool | None:
        winner = self.projected_winner
        if winner is None:
            return None
        return winner == ("home" if self.game.home_pts > self.game.away_pts else "away")


def metrics(rows: Sequence[ReviewRow]) -> dict[str, Any]:
    """The review numbers for a set of rows; the means are ``None`` for an empty set."""
    n = len(rows)
    decided = [r for r in rows if r.winner_called is not None]
    if n == 0:
        mean_margin = mean_score = mean_combined = None
    else:
        mean_margin = math.fsum(abs(r.margin_miss) for r in rows) / n
        mean_score = math.fsum(
            abs(r.game.home_pts - r.home) + abs(r.game.away_pts - r.away) for r in rows
        ) / (2 * n)
        mean_combined = (
            math.fsum(abs((r.game.home_pts + r.game.away_pts) - (r.home + r.away)) for r in rows)
            / n
        )
    return {
        "games": n,
        "decidedGames": len(decided),
        "winnersCalled": sum(1 for r in decided if r.winner_called),
        "tossUps": n - len(decided),
        "meanAbsMarginMiss": mean_margin,
        "meanAbsScoreMiss": mean_score,
        "meanAbsCombinedMiss": mean_combined,
    }


def review_rows(ctx: ReadContext, day: date | None = None) -> dict[str, list[ReviewRow]]:
    """``{"locked": [...], "reconstructed": [...]}`` for the final games in scope.

    A game with a locked row is reviewed on it and never also as a reconstruction. Games in a type
    or era the model does not project are left out.
    """
    games = [
        g
        for g in ctx.games
        if game_has_result(g)
        and (day is None or g.game_date == day)
        and can_project(g.season, g.season_type) is None
    ]
    out: dict[str, list[ReviewRow]] = {"locked": [], "reconstructed": []}
    if not games:
        return out
    locked: dict[str, TeamProjectionLedger] = {}
    for row in ctx.session.execute(
        select(TeamProjectionLedger)
        .where(
            TeamProjectionLedger.kind == "locked",
            TeamProjectionLedger.game_id.in_(select(Game.game_id).where(Game.season == ctx.season)),
        )
        .order_by(TeamProjectionLedger.ledger_id)
    ).scalars():
        locked[row.game_id] = row  # the newest locked row of a game wins
    try:
        model = get_model(ctx)
    except ProjectionUnavailable:
        model = None
    for game in games:
        frozen = locked.get(game.game_id)
        if frozen is not None:
            out["locked"].append(ReviewRow(game, "locked", frozen.home_pts, frozen.away_pts))
        elif model is not None:
            try:
                home, away = model.pre_scores(ctx, game)
            except ProjectionUnavailable:
                continue
            out["reconstructed"].append(ReviewRow(game, "reconstructed", home, away))
    return out


def review_blocks(rows: dict[str, list[ReviewRow]]) -> dict[str, Any]:
    """``byModel`` and ``reconstructed``: the summary every review and the slate carry."""
    by_model = [{"modelKey": MODEL_KEY, **metrics(rows["locked"])}] if rows["locked"] else []
    reconstructed = (
        {"modelKey": MODEL_KEY, **metrics(rows["reconstructed"])} if rows["reconstructed"] else None
    )
    return {"byModel": by_model, "reconstructed": reconstructed}


def build_review(
    ctx: ReadContext, *, day: date | None, freshness: dict[str, Any]
) -> dict[str, Any]:
    """``ProjectionReview``."""
    rows = review_rows(ctx, day)
    blocks = review_blocks(rows)
    listed: list[dict[str, Any]] = []
    for row in (*rows["locked"], *rows["reconstructed"]):
        figures = {"homePts": row.home, "awayPts": row.away}
        listed.append(
            {
                "game": ctx.game_ref(row.game),
                "kind": row.kind,
                "modelKey": row.model_key,
                "projected": figures,
                "locked": figures if row.kind == "locked" else None,
                "result": {"homePts": row.game.home_pts, "awayPts": row.game.away_pts},
                "marginMiss": row.margin_miss,
                "winnerCalled": row.winner_called,
            }
        )
    listed.sort(key=lambda item: (item["game"]["date"], item["game"]["gameId"], item["kind"]))
    finals = [g for g in ctx.games if game_has_result(g) and (day is None or g.game_date == day)]
    unfrozen = len(finals) - len(rows["locked"])
    notes = [
        "Reconstructed projections were made after the game from inputs recorded before it; they "
        "are reported apart from locked ones, which are the only ones that test the model."
    ]
    if finals and unfrozen:
        notes.append(f"{NOTE_NO_FROZEN} for {unfrozen} of {len(finals)} final games.")
    if not finals:
        notes.append("No final game is in this scope yet.")
    return {
        "league": "nba",
        "scope": {"season": ctx.season, "date": refs.iso_date(day)},
        "freshness": freshness,
        **blocks,
        "games": listed,
        "notes": notes,
    }
