"""The NBA matchup: two teams side by side, as they stood going into a game.

``TeamMatchup`` answers the questions a person asks the day before a game: how many does each side
score and *allow*, what did their last games look like, how do they do at home and away, how do
those numbers compare with what their opponents usually do, who is missing, and where does each
defence give up its points. Every figure on the scoring side is the shared core's
(:mod:`nbastats.shared.team_form`), so the NBA and the EuroLeague cannot disagree about what
"points allowed" means.

Three ways in, one payload
--------------------------
``matchups?homeTeamId&awayTeamId``
    Two teams by id. If a game is still to be played between them with those sides, the payload is
    about that game (and carries its projection); otherwise ``game`` is ``null`` and there is no
    projection, because a projection of a game that is not scheduled would be a number about
    nothing.
``teams/{teamId}/matchup``
    The team's next scheduled game (``404 game_not_found`` when it has none).
``games/{gameId}/matchup``
    A particular game, **with inputs cut off before it started**: only games that had started
    before it count, the availability list is as it stood then, and the defence table is the one
    the teams had going into it. Asking about a game last week must not tell you about the games
    that followed it.

Scope
-----
The statistics are one season type: the one the route names (``seasonType``, the regular season
by default). A game-anchored matchup always uses the regular season, whatever kind of game it is,
so a playoff opener is not a matchup of two teams with no playoff games; a note says which scope
was used, and ``/matchups?seasonType=Playoffs`` asks for the postseason's own.

What the payload deliberately leaves out
----------------------------------------
No rank of any team in any statistic (a rank after two games is a coin flip presented as a
standing), no opponent-adjusted value until five games qualify (the shared core enforces it, and
the payload carries ``games``, the number that did), and no probability of winning: the
projection's margin and its range say what the model thinks, and what it does not know.

The NBA records no neutral-site games, so ``venueSplits.neutral`` is ``null`` and every venue is an
assumption, and ``isNeutral`` is ``null`` on every game.

``availability`` at the top describes the *statistics*, not the projection (which carries its own):
``unavailable`` when neither team has played, ``partial`` when a window is short, an
opponent-adjusted value is withheld or some points allowed went to unlisted positions, ``full``
otherwise.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from ..api.errors import ApiError
from ..models import Game
from ..shared.team_form import LeagueIndex, compute_team_form
from .availability_view import summary_for_team
from .defense import build_table, defense_availability, summary_for_team as defense_summary
from .projection import (
    ProjectionUnavailable,
    SideResult,
    can_project,
    current_projection,
    get_model,
    side_absences,
)
from .queries import (
    PROFILE,
    ReadContext,
    aware,
    bad_request,
    game_has_result,
    load_team_games,
)
from .slate import projection_payload

__all__ = [
    "NOTE_NEUTRAL",
    "next_game_for_team",
    "game_between",
    "build_matchup",
]

NOTE_NEUTRAL: Final = (
    "The NBA store does not record neutral-site games, so every venue is an assumption."
)


def next_game_for_team(ctx: ReadContext, team_id: int) -> Game:
    """The team's next scheduled game, or ``404 game_not_found``."""
    game = ctx.next_game_of(team_id)
    if game is None:
        raise ApiError(
            "game_not_found",
            f"{ctx.team_abbr(team_id)} has no scheduled game in {ctx.season}.",
            http_status=404,
        )
    return game


def game_between(ctx: ReadContext, home: int, away: int) -> Game | None:
    """The earliest game still to be played with ``home`` at home against ``away``, if any."""
    upcoming = [
        g
        for g in ctx.scheduled_games()
        if g.home_team_id == home and g.away_team_id == away and not game_has_result(g)
    ]
    return upcoming[0] if upcoming else None


def _form_window(requested: int | None) -> int:
    rules = PROFILE.matchup
    window = rules.form_window_default if requested is None else requested
    if not rules.form_window_min <= window <= rules.form_window_max:
        raise bad_request(
            f"window must be between {rules.form_window_min} and {rules.form_window_max}.",
            "window",
        )
    return window


def _absences(
    ctx: ReadContext,
    sides: dict[int, SideResult],
    team_id: int,
    game: Game | None,
    as_of: datetime,
) -> list[dict[str, Any]]:
    """The team's key absences: from the projection's own squad when there is one, else from a
    squad built just for this team and its game."""
    if game is None or can_project(game.season, game.season_type) is not None:
        return []
    side = sides.get(team_id)
    if side is None:
        try:
            model = get_model(ctx)
            snap = model.snapshot_for(ctx.start_of(game))
            level = model.league_level(snap)
            if level is None:
                return []
            side = model.side(ctx, team_id, game, snap, level, as_of=as_of, detail=True)
        except ProjectionUnavailable:
            return []
    return side_absences(ctx, side, as_of)


def build_matchup(
    ctx: ReadContext,
    *,
    home: int,
    away: int,
    game: Game | None,
    window: int | None,
    freshness: dict[str, Any],
    cut_off: datetime | None = None,
) -> dict[str, Any]:
    """``TeamMatchup`` for two teams (and the game they are about to play, if there is one).

    ``cut_off`` limits every input to games that started strictly before it (a game's matchup
    passes its start); the availability list is read as of the same moment.
    """
    if home == away:
        raise bad_request("A team cannot play itself.", "awayTeamId")
    size = _form_window(window)
    scope = load_team_games(ctx, before=cut_off)
    index = LeagueIndex(scope)
    as_of = aware(cut_off) if cut_off is not None else ctx.now
    table = build_table(ctx, before=cut_off)

    projection: dict[str, Any] | None = None
    sides: dict[int, SideResult] = {}
    notes: list[str] = []
    if cut_off is not None:
        notes.append("Inputs are cut off before this game started.")
    if game is not None:
        if game.season_type != ctx.season_type:
            notes.append(
                f"Statistics are {ctx.season_type} only; this {game.season_type} game's own "
                "results are not in them."
            )
        try:
            model = get_model(ctx)
        except ProjectionUnavailable as exc:
            notes.append(exc.reason)
        else:
            view = current_projection(ctx, model, game)
            if view is None:
                notes.append(
                    can_project(game.season, game.season_type)
                    or "This game has no honest projection."
                )
            else:
                level = (
                    view.result.league_level
                    if view.result is not None
                    else model.league_level(model.final)
                )
                projection = projection_payload(ctx, game, view, freshness, level)
                if view.result is not None:
                    sides = {s.team_id: s for s in view.result.sides}
                    as_of = view.result.as_of
                elif view.kind == "locked":
                    notes.append("The projection shown was frozen before tip-off.")
    else:
        notes.append(
            "No game is scheduled between these teams with these sides, so there is no projection."
        )

    teams: list[dict[str, Any]] = []
    games_played: list[int] = []
    partial = False
    for side_name, team_id in (("home", home), ("away", away)):
        form = compute_team_form(index, team_id, profile=PROFILE, form_window=size)
        games_played.append(form.games)
        if form.games < size or form.adjusted_points_against.value is None:
            partial = True
        target = game if game is not None else ctx.next_game_of(team_id)
        target_start = ctx.start_of(target) if target is not None else None
        teams.append(
            {
                "side": side_name,
                "team": ctx.team_ref(team_id),
                **form.to_payload(ctx.team_ref),
                "availability": summary_for_team(
                    ctx,
                    team_id,
                    as_of,
                    game_id=target.game_id if target is not None else None,
                    before=target_start,
                    key_absences=_absences(ctx, sides, team_id, target, as_of),
                ),
                "defenseSummary": defense_summary(table, team_id),
            }
        )
    if any(t["adjustedPointsAgainst"]["value"] is None for t in teams):
        notes.append("Opponent-adjusted values need at least 5 qualifying games.")
    notes.append(NOTE_NEUTRAL)

    defence_state = defense_availability(table, None)
    if not any(games_played):
        availability = "unavailable"
    elif partial or defence_state == "partial":
        availability = "partial"
    else:
        availability = "full"
    return {
        "league": "nba",
        "season": ctx.season,
        "seasonType": ctx.season_type,
        "phase": None,
        "freshness": freshness,
        "game": ctx.game_ref(game) if game is not None else None,
        "teams": teams,
        "leagueAverage": index.league_average().to_payload(),
        "projection": projection,
        "availability": availability,
        "notes": notes,
    }
