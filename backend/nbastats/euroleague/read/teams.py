"""Clubs: the directory, one club's page, and the ratings table.

``GET /v1/el/teams`` is the league at a glance (record, scoring, points allowed, rating).
``GET /v1/el/teams/{clubCode}`` is ``ClubView``: the workbook's "Team View" and "Squads" sheets as
one payload, with the club's scoring, its rating and how it got there, every squad member with
his projected minutes, per-40 rates and season line, who is missing, and the next game.
``GET /v1/el/ratings`` is the Team Ratings sheet with the round-by-round updates.

What a squad row shows, and what it will not
--------------------------------------------
* ``basis`` says whose numbers the rates are: ``workbookOfficial`` (from the league's own
  2025-26 statistics), ``workbookEstimate`` (the workbook author's translation from another
  league, labelled "your estimate; source not recorded" because Hardwood cannot tell where it came
  from), ``positionPrior`` (no line of his own: the pooled mean at his position) or
  ``officialUpdate``. An estimate is never shown as if it were official.
* ``seasonAverages`` divide each stat by the games that *recorded* it (``gamesWithStat`` on the
  stats table is the full story); a player with no game on record has ``seasonAverages: null``.
* ``status`` is the entry's own status, shown as sourced and dated (``availabilitySource``), with
  ``inForce`` and ``isStale``. A player with no entry has ``status: null`` (an em dash), not
  "available".

Ratings are served from the model (:class:`~nbastats.euroleague.model.round_projection.Model`),
not from stored rows, so a round whose results have just arrived is already in them.
"""

from __future__ import annotations

from typing import Any

from ...shared.availability import display_chance
from ...shared.team_form import LeagueIndex, compute_team_form
from ..model.player_rates import ESTIMATE_BASES
from ..model.round_projection import ProjectionUnavailable, get_model, side_absences
from ..profile import ESTIMATE_BASIS_LABEL, PROFILE
from .availability import get_book, source_of
from .defense import NOTE_SCOPE, build_table, summary_for_club
from .queries import ReadContext, bad_request, load_team_games
from .stats import aggregate_players, season_values

__all__ = ["build_teams", "build_club_view", "build_ratings"]


def _rating_block(ctx: ReadContext, club: str) -> dict[str, Any] | None:
    try:
        model = get_model(ctx)
    except ProjectionUnavailable:
        return None
    ratings = model.ratings.snapshots[model.last_round]
    row = ratings.get(club)
    if row is None:
        return None
    level, regression = model.ratings.league_level, model.ratings.regression
    return {
        "pfPrior": row.pf_prior,
        "paPrior": row.pa_prior,
        "priorIsEstimate": row.prior_is_estimate,
        "attackAdj": row.attack_adj,
        "defenceAdj": row.defence_adj,
        "projectedPointsFor": row.pf(level, regression),
        "projectedPointsAgainst": row.pa(level, regression),
        "attackIndex": row.attack(level, regression),
        "defenceIndex": row.defence(level, regression),
        "asOfRound": model.last_round,
        "source": row.source,
        "updateWeight": row.update_weight,
        "updateBasis": row.update_basis,
        "extrapolated": row.extrapolated,
    }


def build_teams(ctx: ReadContext, freshness: dict[str, Any]) -> dict[str, Any]:
    """The club directory: record, scoring and rating for every club, in club-code order."""
    index = LeagueIndex(load_team_games(ctx))
    rows: list[dict[str, Any]] = []
    for club in sorted(ctx.clubs):
        form = compute_team_form(index, club, profile=PROFILE)
        rating = _rating_block(ctx, club)
        rows.append(
            {
                "team": ctx.team_ref(club),
                "record": {"wins": form.wins, "losses": form.losses},
                "games": form.games,
                "pointsPerGame": form.points_per_game,
                "pointsAllowedPerGame": form.points_allowed_per_game,
                "rating": (
                    {
                        "attackIndex": rating["attackIndex"],
                        "defenceIndex": rating["defenceIndex"],
                        "asOfRound": rating["asOfRound"],
                    }
                    if rating is not None
                    else None
                ),
            }
        )
    return {
        "league": "euroleague",
        "season": ctx.season_label,
        "seasonCode": ctx.season_code,
        "freshness": freshness,
        "teams": rows,
        "notes": [NOTE_SCOPE],
    }


def build_club_view(ctx: ReadContext, club: str, freshness: dict[str, Any]) -> dict[str, Any]:
    """``ClubView``."""
    index = LeagueIndex(load_team_games(ctx))
    form = compute_team_form(index, club, profile=PROFILE)
    nxt = ctx.next_game_of(club)
    game_id = nxt.game_id if nxt is not None else None
    notes = [NOTE_SCOPE]
    states: dict[str, Any] = {}
    absences: list[dict[str, Any]] = []
    try:
        model = get_model(ctx)
    except ProjectionUnavailable as exc:
        notes.append(exc.reason)
    else:
        states = model.players.get(model.last_round, {})
        side = model.side(ctx, club, as_of=ctx.now, game_id=game_id, state_round=model.last_round)
        absences = side_absences(ctx, side, ctx.now, limit=None)
    book = get_book(ctx)
    aggregates = aggregate_players(ctx)
    table = build_table(ctx)
    squad: list[dict[str, Any]] = []
    for reg in sorted(
        ctx.registrations_for_club(club),
        key=lambda r: (ctx.player_name(r.person_code).lower(), r.person_code),
    ):
        person = reg.person_code
        state = states.get(person)
        agg = aggregates.get(person)
        averages = None
        if agg is not None:
            values, _ = season_values(agg, "PerGame")
            averages = {
                "games": agg.games,
                "min": values["min"],
                "pts": values["pts"],
                "reb": values["reb"],
                "ast": values["ast"],
                "pir": values["pir"],
                "fg2Pct": values["fg2_pct"],
                "fg3Pct": values["fg3_pct"],
                "ftPct": values["ft_pct"],
            }
        resolved = book.resolve(person, game_id, ctx.now)
        status = resolved.status if resolved.present else None
        squad.append(
            {
                "player": ctx.player_ref(person, club),
                "positionWorkbook5": reg.position5_workbook,
                "age": reg.age_workbook,
                "role": reg.role_workbook,
                "basis": state.basis if state is not None else None,
                "basisNote": (
                    ESTIMATE_BASIS_LABEL
                    if state is not None and state.basis == "workbookEstimate"
                    else None
                ),
                "isEstimate": state is not None and state.basis in ESTIMATE_BASES,
                "projectedMinutes": state.minutes if state is not None else None,
                "per40": (
                    {
                        k: state.rates.get(f"{k}40")
                        for k in ("pts", "reb", "ast", "fg3m", "stl", "blk", "tov")
                    }
                    if state is not None
                    else None
                ),
                "seasonAverages": averages,
                "status": status,
                "chanceOfPlaying": display_chance(status, ctx.chance_table()),
                "inForce": resolved.in_force if resolved.present else None,
                "isStale": book.is_stale(resolved, club, ctx.now) if resolved.present else None,
                "availabilitySource": (
                    source_of(resolved.row)
                    if resolved.present and resolved.row is not None
                    else None
                ),
            }
        )
    season_row = ctx.club_seasons.get(club)
    return {
        "league": "euroleague",
        "team": ctx.team_ref(club),
        "season": ctx.season_label,
        "seasonCode": ctx.season_code,
        "freshness": freshness,
        "coach": season_row.coach_name if season_row is not None else None,
        "record": {"wins": form.wins, "losses": form.losses},
        "scoring": form.to_payload(ctx.team_ref),
        "rating": _rating_block(ctx, club),
        "squad": squad,
        "absences": absences,
        "nextGame": ctx.game_ref(nxt) if nxt is not None else None,
        "defenseSummary": summary_for_club(table, club),
        "notes": notes,
    }


def build_ratings(
    ctx: ReadContext, as_of_round: int | None, freshness: dict[str, Any]
) -> dict[str, Any]:
    """``RatingsTable``: every club's rating as of a round (the latest by default)."""
    try:
        model = get_model(ctx)
    except ProjectionUnavailable as exc:
        raise bad_request(exc.reason, "asOfRound") from exc
    book = model.ratings
    wanted = model.last_round if as_of_round is None else as_of_round
    if wanted < book.base_round:
        raise bad_request(
            f"Ratings before round {book.base_round} are not in this store (it starts from an "
            f"import as of round {book.base_round}).",
            "asOfRound",
        )
    snapshot = book.at(wanted)
    served = max(r for r in book.snapshots if r <= wanted)
    level, regression = book.league_level, book.regression
    rows = [
        {
            "team": ctx.team_ref(club),
            "pfPrior": row.pf_prior,
            "paPrior": row.pa_prior,
            "priorIsEstimate": row.prior_is_estimate,
            "attackAdj": row.attack_adj,
            "defenceAdj": row.defence_adj,
            "projectedPointsFor": row.pf(level, regression),
            "projectedPointsAgainst": row.pa(level, regression),
            "attackIndex": row.attack(level, regression),
            "defenceIndex": row.defence(level, regression),
            "source": row.source,
            "updateWeight": row.update_weight,
            "updateBasis": row.update_basis,
            "extrapolated": row.extrapolated,
            "gamesCounted": row.games_counted,
        }
        for club, row in sorted(snapshot.items())
    ]
    updates = [
        {
            "round": u.round,
            "team": ctx.team_ref(u.club_code),
            "opponent": ctx.team_ref(u.opponent),
            "gameId": u.game_id,
            "gameNumber": u.n,
            "weight": u.weight,
            "extrapolated": u.extrapolated,
            "projectedScored": u.projected_scored,
            "scored": u.scored,
            "projectedAllowed": u.projected_allowed,
            "allowed": u.allowed,
            "basis": u.basis,
        }
        for u in book.updates
        if u.round <= served
    ]
    return {
        "league": "euroleague",
        "season": ctx.season_label,
        "seasonCode": ctx.season_code,
        "asOfRound": served,
        "baseRound": book.base_round,
        "freshness": freshness,
        "leagueAveragePoints": level,
        "regression": regression,
        "rows": rows,
        "updates": updates,
        "notes": [
            "Rounds up to the base round come from the import; later rounds move each club's "
            "attack and defence by a weight times the miss against the projection made before the game.",
        ],
    }
