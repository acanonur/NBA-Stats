"""The EuroLeague matchup: two clubs side by side, as they stood going into a game.

``TeamMatchup`` answers the questions a person asks the day before a game: how many does each side
score and *allow*, what did their last games look like, how do they do at home and away, how do
those numbers compare with what their opponents usually do, who is missing, and where does each
defence give up its points. Every figure on the scoring side is the shared core's
(:mod:`nbastats.shared.team_form`), so the NBA and the EuroLeague cannot disagree about what
"points allowed" means.

Three ways in, one payload
--------------------------
``matchups?homeTeamId&awayTeamId``
    Two clubs by code. If a game is still to be played between them with those sides, the payload
    is about that game (and carries its projection); otherwise ``game`` is ``null`` and there is
    no projection, because a projection of a game that is not scheduled would be a number about
    nothing.
``teams/{clubCode}/matchup``
    The club's next scheduled game (``404 game_not_found`` when it has none).
``games/{gameId}/matchup``
    A particular game, **with inputs cut off before tip-off**: only games that had started before
    it count, the availability list is as it stood then, and the defence table is the one the
    clubs had going into it. Asking about a game last week must not tell you about the games that
    followed it.

What the payload deliberately leaves out
----------------------------------------
No rank of any team in any statistic (a rank after two games is a coin flip presented as a
standing), no opponent-adjusted value until five games qualify (the shared core enforces it, and
the payload carries ``games``, the number that did), and no probability of winning: the
projection's margin and its range say what the model thinks, and what it does not know.

The neutral split is ``null`` when no game in scope has a known neutral flag ("not tracked" and
"tracked, none played" are different statements), and a game whose neutrality is unknown is
classed by its home flag.

``availability`` at the top describes the *statistics*, not the projection (which carries its own):
``unavailable`` when neither club has played, ``estimated`` when the defence summary rests on the
workbook author's position labels, ``partial`` when a window is short or an opponent-adjusted value
is withheld, ``full`` otherwise.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...api.errors import ApiError
from ...shared.team_form import LeagueIndex, compute_team_form
from ..model.round_projection import (
    ProjectionUnavailable,
    current_projection,
    get_model,
    side_absences,
)
from ..models import ElGame
from ..profile import PROFILE
from .availability import summary_for_club
from .defense import (
    NOTE_SCOPE,
    build_table,
    defense_availability,
    summary_for_club as defense_summary,
)
from .queries import ReadContext, bad_request, game_start, load_team_games
from .round import projection_payload

__all__ = [
    "next_game_for_club",
    "game_between",
    "build_matchup",
]


def next_game_for_club(ctx: ReadContext, club: str) -> ElGame:
    """The club's next scheduled game, or ``404 game_not_found``."""
    game = ctx.next_game_of(club)
    if game is None:
        raise ApiError(
            "game_not_found", f"{club} has no scheduled game in this season.", http_status=404
        )
    return game


def game_between(ctx: ReadContext, home: str, away: str) -> ElGame | None:
    """The earliest game still to be played with ``home`` at home against ``away``, if any."""
    upcoming = [
        g
        for g in ctx.games
        if g.home_club_code == home
        and g.away_club_code == away
        and ctx.game_status(g) == "scheduled"
    ]
    return min(upcoming, key=lambda g: (game_start(g), g.game_id)) if upcoming else None


def _form_window(ctx_window: int | None) -> int:
    rules = PROFILE.matchup
    window = rules.form_window_default if ctx_window is None else ctx_window
    if not rules.form_window_min <= window <= rules.form_window_max:
        raise bad_request(
            f"window must be between {rules.form_window_min} and {rules.form_window_max}.", "window"
        )
    return window


def build_matchup(
    ctx: ReadContext,
    *,
    home: str,
    away: str,
    game: ElGame | None,
    window: int | None,
    freshness: dict[str, Any],
    cut_off: datetime | None = None,
) -> dict[str, Any]:
    """``TeamMatchup`` for two clubs (and the game they are about to play, if there is one).

    ``cut_off`` limits every input to games that started strictly before it (a game's matchup
    passes its tip-off); the availability list is read as of the same instant.
    """
    if home == away:
        raise bad_request("A club cannot play itself.", "awayTeamId")
    size = _form_window(window)
    scope = load_team_games(ctx, None, before=cut_off)
    index = LeagueIndex(scope)
    as_of = cut_off if cut_off is not None else ctx.now
    table = build_table(ctx, before=cut_off)
    game_id = game.game_id if game is not None else None

    projection: dict[str, Any] | None = None
    absences: dict[str, list[dict[str, Any]]] = {home: [], away: []}
    notes = [NOTE_SCOPE]
    if cut_off is not None:
        notes.append("Inputs are cut off before this game's tip-off.")
    if game is not None:
        try:
            model = get_model(ctx)
        except ProjectionUnavailable as exc:
            notes.append(exc.reason)
        else:
            view = current_projection(ctx, model, game)
            if view is not None:
                projection = projection_payload(
                    ctx, game, view, freshness, model.ratings.league_level
                )
                if view.result is not None:
                    absences[home] = side_absences(ctx, view.result.home, as_of)
                    absences[away] = side_absences(ctx, view.result.away, as_of)
            else:
                notes.append("No projection was recorded before tip-off for this game.")
    else:
        notes.append(
            "No game is scheduled between these clubs with these sides, so there is no projection."
        )

    teams: list[dict[str, Any]] = []
    games_played = []
    partial = False
    for side, club in (("home", home), ("away", away)):
        form = compute_team_form(index, club, profile=PROFILE, form_window=size)
        games_played.append(form.games)
        if form.games < size or form.adjusted_points_against.value is None:
            partial = True
        block = form.to_payload(ctx.team_ref)
        team_defense = defense_summary(table, club)
        teams.append(
            {
                "side": side,
                "team": ctx.team_ref(club),
                **block,
                "availability": summary_for_club(
                    ctx, club, as_of, game_id=game_id, key_absences=absences[club]
                ),
                "defenseSummary": team_defense,
            }
        )
    if any(t["adjustedPointsAgainst"]["value"] is None for t in teams):
        notes.append("Opponent-adjusted values need at least 5 qualifying games.")

    defence_state = defense_availability(ctx, "gfc", table, None)
    if not any(games_played):
        availability = "unavailable"
    elif defence_state == "estimated":
        availability = "estimated"
    elif partial:
        availability = "partial"
    else:
        availability = "full"
    return {
        "league": "euroleague",
        "season": ctx.season_label,
        "seasonCode": ctx.season_code,
        "seasonType": None,
        "phase": None,
        "freshness": freshness,
        "game": ctx.game_ref(game) if game is not None else None,
        "teams": teams,
        "leagueAverage": index.league_average().to_payload(),
        "projection": projection,
        "availability": availability,
        "notes": notes,
    }
