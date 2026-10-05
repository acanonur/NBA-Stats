"""Rounds, game projections and scorers: the payloads built from the model.

A game's projection is shown in one of four forms and the payload always says which
(``model.kind``): ``latest`` (the model, now, for a game still to be played), ``locked`` (frozen
before tip-off), ``reconstructed`` (rebuilt after the game from inputs before tip-off) or
``imported`` (the workbook's own, scores only). :func:`~nbastats.euroleague.model.round_projection.
current_projection` decides which applies; this module only describes it.

What a client is told, every time
---------------------------------
* ``availability: "estimated"``: a projection is an estimate, always.
* ``intervalBasis: "assumed"`` until the spreads have been fitted from results: the 80% range is
  the workbook's assumed spread, "not yet checked against results".
* ``assumptions.assumedAvailable``: how many players the model assumed would play only because it
  had no usable entry for them (and why: ``noEntry``), and how many entries it ignored because they
  were out of force. A projection that quietly treated silence as health would be telling a small
  lie; this is the correction.
* ``venueAssumed``: the home advantage was applied because the venue's neutrality is unknown.
* ``model.constants``: every constant used with its provenance, so a number the workbook typed, a
  number Hardwood fitted and a number nobody has checked are not blurred.
* A frozen projection (``locked`` or ``imported``) says that player detail was not frozen with it,
  and its absences, attack index and cap flags are ``null``: they were not recorded.

A round that was played before its results were loaded is ``resultPending``, not ``upcoming``, and
carries the note that says how to fix it. That is the honest day-one state of a league whose last
round predates the workbook export.

There is no probability of winning anywhere in these payloads, and nothing to compare a projected
score with: the margin, its range, the projected winner and the projected combined points are the
whole of it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Final, Sequence

from ...shared import refs
from ...shared.team_projection import interval80, is_toss_up, summary_text
from ..models import ElGame, ElProjectionLedger
from ..model.round_projection import (
    MODEL_KEY,
    MODEL_VERSION,
    ProjectionUnavailable,
    ProjectionView,
    SideResult,
    current_projection,
    get_model,
    interval_basis,
    ledger_rows_for,
    side_absences,
)
from ..model.scorers import game_scorers, recent_points
from .queries import (
    ReadContext,
    bad_request,
    computed_after_tipoff,
    game_has_result,
    game_start,
    round_status,
)
from .review import review_blocks, review_rows

__all__ = [
    "NOTE_RESULTS_NOT_LOADED",
    "CONSTANT_KEYS",
    "model_constants",
    "model_block",
    "projection_payload",
    "game_projection_payload",
    "projection_detail",
    "resolve_round",
    "build_round_view",
    "build_slate",
    "build_round_scorers",
]

NOTE_RESULTS_NOT_LOADED: Final = (
    "Round {round} results are not loaded: enable live ingest or import an updated workbook."
)

#: The settings a projection depends on (the status chances are listed by the method page).
CONSTANT_KEYS: Final[tuple[str, ...]] = (
    "priorRegression",
    "homeAdvantagePoints",
    "teamSd",
    "marginSd",
    "replacementPer40",
    "absorbShare",
    "boostCap",
    "rotationShare",
    "capPolicyConsistent",
    "squadReconcile",
    "overtimeScaling",
)


# --------------------------------------------------------------------------- constants


def model_constants(ctx: ReadContext, league_level: float | None = None) -> list[dict[str, Any]]:
    """The constants behind a projection, each with where it came from."""
    rows = [
        {
            "key": key,
            "value": ctx.settings[key].value,
            "provenance": ctx.settings[key].provenance,
            "isDefault": ctx.settings[key].is_default,
        }
        for key in CONSTANT_KEYS
    ]
    if league_level is not None:
        rows.append(
            {
                "key": "leagueAveragePoints",
                "value": league_level,
                "provenance": "derived",
                "isDefault": False,
            }
        )
    return rows


def model_block(
    ctx: ReadContext,
    *,
    kind: str,
    computed_at: datetime | None,
    inputs_cutoff: datetime | None,
    cap_policy: str,
    model_key: str = MODEL_KEY,
    version: str = MODEL_VERSION,
    league_level: float | None = None,
    include_constants: bool = True,
    after_tipoff: bool | None = None,
) -> dict[str, Any]:
    """The ``model`` object of a projection.

    ``computedAfterTipoff`` is :func:`~nbastats.euroleague.read.queries.computed_after_tipoff` for
    the game the projection is about, passed in as ``after_tipoff`` by the one caller that knows
    the game. A block that is not about one game (the round's own ``model``) leaves it ``None``.
    """
    return {
        "key": model_key,
        "version": version,
        "kind": kind,
        "computedAt": refs.rfc3339(computed_at),
        "inputsCutoff": refs.rfc3339(inputs_cutoff),
        "computedAfterTipoff": after_tipoff,
        "capPolicy": cap_policy,
        "constants": model_constants(ctx, league_level) if include_constants else [],
    }


# --------------------------------------------------------------------------- one projection


def _range(value: tuple[float, float] | None) -> dict[str, float] | None:
    return None if value is None else {"low": value[0], "high": value[1]}


def _result_block(game: ElGame, home: float, away: float) -> dict[str, Any] | None:
    if not game_has_result(game):
        return None
    margin_miss = (game.home_pts - game.away_pts) - (home - away)
    projected = home - away
    called: bool | None
    if is_toss_up(projected):
        called = None
    else:
        called = (projected > 0) == (game.home_pts > game.away_pts)
    return {
        "homePts": game.home_pts,
        "awayPts": game.away_pts,
        "marginMiss": margin_miss,
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
    return {
        "team": ctx.team_ref(side.club_code),
        "projectedPoints": projected,
        "range80": _range(range80),
        "fullStrengthPoints": full_strength,
        "availabilityEffect": projected - full_strength,
        "attackIndex": side.attack,
        "attackIndexAfterAvailability": after_availability,
        "defenceIndex": side.defence,
        "capBinding": side.injury.cap_binding,
        "unassignedPoints": side.injury.unassigned_points,
        "replacementPoints": side.injury.replacement_credited,
        "keyAbsences": side_absences(ctx, side, as_of),
    }


def projection_payload(
    ctx: ReadContext,
    game: ElGame,
    view: ProjectionView,
    freshness: dict[str, Any],
    league_level: float | None = None,
) -> dict[str, Any]:
    """``GameProjection`` for ``game`` in the form ``view`` describes."""
    home_ref, away_ref = ctx.team_ref(game.home_club_code), ctx.team_ref(game.away_club_code)
    if view.result is not None:
        result = view.result
        match = result.match
        home_side = _side_from_result(
            ctx,
            result.home,
            match.home_points,
            match.home_full_strength,
            match.home_attack_after_availability,
            match.home_range80,
            result.as_of,
        )
        away_side = _side_from_result(
            ctx,
            result.away,
            match.away_points,
            match.away_full_strength,
            match.away_attack_after_availability,
            match.away_range80,
            result.as_of,
        )
        winner = match.projected_winner
        home_pts, away_pts = match.home_points, match.away_points
        margin_range = match.margin_range80
        combined, combined_effect = match.combined_points, match.combined_availability_effect
        advantage = result.home_advantage.points
        venue_assumed: bool | None = result.home_advantage.venue_assumed
        assumptions: dict[str, Any] = {
            "assumedAvailable": {
                "home": result.home.assumed_available,
                "away": result.away.assumed_available,
                "basis": "noEntry",
            },
            "staleEntriesIgnored": result.home.stale_ignored + result.away.stale_ignored,
        }
        model = model_block(
            ctx,
            kind=view.kind,
            computed_at=ctx.now,
            inputs_cutoff=result.as_of,
            cap_policy=result.cap_policy,
            league_level=result.league_level,
            after_tipoff=computed_after_tipoff(game, ctx.now),
        )
        notes = [
            "Defence indices are not changed by absences: absences move the projected scores only.",
            "Players with no status in force are assumed to play; see assumptions.assumedAvailable.",
        ]
        if view.kind == "reconstructed":
            notes.append(
                "Rebuilt after tip-off from inputs published before it; no projection was frozen."
            )
    else:
        row: ElProjectionLedger = view.ledger  # type: ignore[assignment]
        home_pts, away_pts = row.home_pts, row.away_pts
        winner = (
            None if is_toss_up(home_pts - away_pts) else ("home" if home_pts > away_pts else "away")
        )
        team_sd, margin_sd = ctx.setting("teamSd"), ctx.setting("marginSd")
        home_range, away_range = interval80(home_pts, team_sd), interval80(away_pts, team_sd)
        margin_range = interval80(home_pts - away_pts, margin_sd)
        full_home, full_away = row.home_full_strength, row.away_full_strength

        def frozen_side(
            ref: dict[str, Any],
            projected: float,
            full: float | None,
            attack_after: float | None,
            defence: float | None,
            rng: tuple[float, float] | None,
        ) -> dict[str, Any]:
            return {
                "team": ref,
                "projectedPoints": projected,
                "range80": _range(rng),
                "fullStrengthPoints": full,
                "availabilityEffect": None if full is None else projected - full,
                "attackIndex": None,
                "attackIndexAfterAvailability": attack_after,
                "defenceIndex": defence,
                "capBinding": None,
                "unassignedPoints": None,
                "replacementPoints": None,
                "keyAbsences": [],
            }

        home_side = frozen_side(
            home_ref,
            home_pts,
            full_home,
            row.home_attack_index_after_availability,
            row.home_defence_index,
            home_range,
        )
        away_side = frozen_side(
            away_ref,
            away_pts,
            full_away,
            row.away_attack_index_after_availability,
            row.away_defence_index,
            away_range,
        )
        combined = home_pts + away_pts
        combined_effect = (
            None if full_home is None or full_away is None else combined - (full_home + full_away)
        )
        advantage = row.home_advantage_points  # type: ignore[assignment]
        venue_assumed = game.is_neutral is None
        assumptions = {
            "assumedAvailable": {"home": None, "away": None, "basis": None},
            "staleEntriesIgnored": None,
        }
        model = model_block(
            ctx,
            kind=row.kind,
            computed_at=row.computed_at,
            inputs_cutoff=row.inputs_cutoff,
            cap_policy=row.cap_policy,
            model_key="workbook" if row.kind == "imported" else MODEL_KEY,
            version=row.model_version,
            include_constants=False,
            after_tipoff=computed_after_tipoff(game, row.computed_at),
        )
        notes = [
            "Player detail was not frozen with this projection, so absences are not listed.",
        ]
        if row.kind == "imported":
            notes.append("The workbook published scores only; everything else is not recorded.")
    margin = home_pts - away_pts
    return {
        "league": "euroleague",
        "game": ctx.game_ref(game),
        "freshness": freshness,
        "home": home_side,
        "away": away_side,
        "margin": margin,
        "marginRange80": _range(margin_range),
        "projectedWinner": None if winner is None else (home_ref if winner == "home" else away_ref),
        "isTossUp": is_toss_up(margin),
        "summary": summary_text(margin, game.home_club_code, game.away_club_code),
        "combinedPoints": combined,
        "combinedAvailabilityEffect": combined_effect,
        "homeAdvantagePoints": advantage,
        "venueAssumed": venue_assumed,
        "intervalBasis": interval_basis(ctx),
        "model": model,
        "assumptions": assumptions,
        "result": _result_block(game, home_pts, away_pts),
        "availability": "estimated",
        "notes": notes,
    }


def game_projection_payload(
    ctx: ReadContext, game: ElGame, freshness: dict[str, Any]
) -> dict[str, Any] | None:
    """The projection shown for ``game``, or ``None`` when there is no honest one."""
    try:
        model = get_model(ctx)
    except ProjectionUnavailable:
        return None
    view = current_projection(ctx, model, game)
    if view is None:
        return None
    return projection_payload(ctx, game, view, freshness, model.ratings.league_level)


def projection_detail(ctx: ReadContext, game: ElGame, freshness: dict[str, Any]) -> dict[str, Any]:
    """``GameProjectionDetail``: the current projection, the frozen one and the history."""
    try:
        model = get_model(ctx)
    except ProjectionUnavailable:
        model = None
    rows = ledger_rows_for(ctx, game.game_id)
    current = locked = None
    league_level = model.ratings.league_level if model is not None else None
    if model is not None:
        view = current_projection(ctx, model, game)
        if view is not None and view.kind == "locked":
            locked = projection_payload(ctx, game, view, freshness, league_level)
        elif view is not None:
            current = projection_payload(ctx, game, view, freshness, league_level)
    if locked is None:
        frozen = [r for r in rows if r.kind == "locked"]
        if frozen:
            locked = projection_payload(
                ctx, game, ProjectionView("locked", ledger=frozen[-1]), freshness, league_level
            )
    return {
        "league": "euroleague",
        "game": ctx.game_ref(game),
        "freshness": freshness,
        "current": current,
        "locked": locked,
        "history": [
            {
                "computedAt": refs.rfc3339(r.computed_at),
                "kind": r.kind,
                "homePts": r.home_pts,
                "awayPts": r.away_pts,
            }
            for r in rows
        ],
    }


# --------------------------------------------------------------------------- rounds


def resolve_round(ctx: ReadContext, value: str | int | None) -> int:
    """``next`` (or nothing) or a round number that exists in the season; else ``400``."""
    if value is None or (isinstance(value, str) and value.strip().lower() in ("", "next")):
        found = ctx.next_round()
        if found is None:
            raise bad_request("This season has no rounds yet.", "round")
        return found
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise bad_request(f"{value!r} is not a round number or 'next'.", "round") from exc
    if number not in ctx.rounds():
        raise bad_request(f"There is no round {number} in this season.", "round")
    return number


def _round_games(ctx: ReadContext, number: int) -> list[ElGame]:
    return sorted(ctx.games_of_round(number), key=lambda g: (game_start(g), g.game_id))


def _projections(
    ctx: ReadContext, games: Sequence[ElGame], freshness: dict[str, Any]
) -> tuple[list[dict[str, Any]], int]:
    out: list[dict[str, Any]] = []
    missing = 0
    for game in games:
        payload = game_projection_payload(ctx, game, freshness)
        if payload is None:
            missing += 1
        else:
            out.append(payload)
    return out, missing


def build_round_view(ctx: ReadContext, number: int, freshness: dict[str, Any]) -> dict[str, Any]:
    """``RoundView``."""
    games = _round_games(ctx, number)
    statuses = [ctx.game_status(g) for g in games]
    status = round_status(statuses)
    projected, missing = _projections(ctx, games, freshness)
    notes = [
        "EuroLeague games only: friendlies, domestic leagues and national-team games are not tracked."
    ]
    if status in ("resultPending", "inProgress") and "resultPending" in statuses:
        notes.append(NOTE_RESULTS_NOT_LOADED.format(round=number))
    if missing:
        notes.append(f"{missing} game(s) have no projection: a club has no rating in this store.")
    closest = None
    if projected:
        nearest = min(projected, key=lambda p: (abs(p["margin"]), p["game"]["gameId"]))
        closest = nearest["game"]
    decided = [p for p in projected if not p["isTossUp"]]
    return {
        "league": "euroleague",
        "season": ctx.season_label,
        "seasonCode": ctx.season_code,
        "round": number,
        "phase": games[0].phase_code if games else None,
        "freshness": freshness,
        "status": status,
        "games": projected,
        "summary": {
            "games": len(games),
            "tossUps": sum(1 for p in projected if p["isTossUp"]),
            "closestGame": closest,
            "averageCombinedPoints": (
                sum(p["combinedPoints"] for p in projected) / len(projected) if projected else None
            ),
            "homeWinnersProjected": sum(1 for p in decided if p["margin"] > 0),
            "awayWinnersProjected": sum(1 for p in decided if p["margin"] < 0),
        },
        "notes": notes,
    }


def build_slate(ctx: ReadContext, number: int, freshness: dict[str, Any]) -> dict[str, Any]:
    """``SlateProjections`` for one round."""
    games = _round_games(ctx, number)
    projected, missing = _projections(ctx, games, freshness)
    try:
        level = get_model(ctx).ratings.league_level
    except ProjectionUnavailable:
        level = None
    blocks = review_blocks(review_rows(ctx))
    has_review = bool(blocks["byModel"]) or blocks["reconstructed"] is not None
    notes = ["Projections are estimates. Players with no status in force are assumed to play."]
    if missing:
        notes.append(f"{missing} game(s) have no projection: a club has no rating in this store.")
    statuses = [ctx.game_status(g) for g in games]
    if "resultPending" in statuses:
        notes.append(NOTE_RESULTS_NOT_LOADED.format(round=number))
    return {
        "league": "euroleague",
        "date": None,
        "round": number,
        "freshness": freshness,
        "model": model_block(
            ctx,
            kind="latest",
            computed_at=ctx.now,
            inputs_cutoff=ctx.now,
            cap_policy="consistent" if ctx.setting("capPolicyConsistent") >= 0.5 else "workbook",
            league_level=level,
        ),
        "games": projected,
        "review": blocks if has_review else None,
        "notes": notes,
    }


# --------------------------------------------------------------------------- scorers


def build_round_scorers(
    ctx: ReadContext, number: int, per_club: int, freshness: dict[str, Any]
) -> dict[str, Any]:
    """``RoundScorers``: the top ``per_club`` scorers of each club in the round, no spread, no picks."""
    if not 1 <= per_club <= 15:
        raise bad_request("perClub must be between 1 and 15.", "perClub")
    notes = [
        "Scorers are projected from rates and minutes; form uses EuroLeague games in the store only.",
    ]
    clubs: list[dict[str, Any]] = []
    try:
        model = get_model(ctx)
    except ProjectionUnavailable:
        notes.append("This store has no club ratings yet, so no scorers are projected.")
        return {
            "league": "euroleague",
            "round": number,
            "freshness": freshness,
            "clubs": clubs,
            "notes": notes,
        }
    skipped = 0
    for game in _round_games(ctx, number):
        status = ctx.game_status(game)
        if game.round_number <= model.base_round and status != "scheduled":
            skipped += 1  # no pre-game state exists for a game the base already contains
            continue
        if status == "postponed":
            continue
        try:
            if status == "scheduled":
                result = model.project(ctx, game, as_of=ctx.now, kind="latest")
            else:
                result = model.reconstructed.get(game.game_id) or model.project(
                    ctx, game, as_of=game_start(game) - timedelta(seconds=1), kind="reconstructed"
                )
        except ProjectionUnavailable:
            skipped += 1
            continue
        home, away = game_scorers(ctx, result, per_club=per_club)
        for club, lines, side in (
            (game.home_club_code, home, result.home),
            (game.away_club_code, away, result.away),
        ):
            by_person = {entry.person_code: entry for entry in side.squad}
            players = []
            for line in lines:
                entry = by_person[line.person_code]
                players.append(
                    {
                        "player": ctx.player_ref(line.person_code, club),
                        "status": line.status,
                        "chanceOfPlaying": line.chance,
                        "modelPoints": line.model_points,
                        "formAverage": line.form_average,
                        "formGames": line.form_games,
                        "recentPoints": recent_points(ctx, line.person_code, club, result.as_of),
                        "projectedPoints": line.projected_points,
                        "projectedMinutes": entry.outcome.projected_minutes,
                    }
                )
            clubs.append(
                {"team": ctx.team_ref(club), "game": ctx.game_ref(game), "players": players}
            )
    if skipped:
        notes.append(
            f"{skipped} game(s) have no scorers: their inputs predate this store's ratings."
        )
    return {
        "league": "euroleague",
        "season": ctx.season_label,
        "seasonCode": ctx.season_code,
        "round": number,
        "freshness": freshness,
        "clubs": clubs,
        "notes": notes,
    }
