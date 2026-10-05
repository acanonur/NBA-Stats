"""``team_matchup``: two teams side by side going into a game.

A tile and a route answer the same question with the same object. For the NBA this module calls
:func:`nbastats.nba_matchup.team_matchup`, the builder behind ``GET /v1/matchups`` and
``GET /v1/teams/{teamId}/matchup``; for the EuroLeague it calls the EuroLeague's builder of the same
name (``GET /v1/el/matchups`` and ``/teams/{clubCode}/matchup``). **Nothing in this module computes
a statistic**: it picks the two teams and the season, passes them on, and returns the payload. The
payload (``TeamMatchup``, ``contracts/CONTRACT.md`` §4) carries both sides' points scored and
allowed, recent form with scores, home and away splits, opponent-adjusted values, who is missing,
where each defence gives up its points, and the projected score when a game is scheduled. It has no
rank of any team in any statistic and no probability of winning.

Which two teams
---------------
``team`` (NBA; default ``$favorite_team``) or ``club`` (EuroLeague) is the tile's subject.
``opponent`` or ``opponentClub`` is optional, and null means *the next opponent*: the subject's next
scheduled game decides who it is, and the payload then carries that game and its projection.

* **No opponent chosen, a game is scheduled:** that game.
* **An opponent chosen:** the pair's scheduled game if there is one, tried with the subject at home
  first and then away, because the catalog cannot know who hosts. If the two are not scheduled to
  meet, the payload is the two teams' current statistics with ``game: null`` and no projection (a
  projection of a game that is not scheduled would be a number about nothing).
* **No opponent chosen, and nothing is scheduled** (the off-season, or a calendar not loaded yet):
  the two sides of the subject's most recent game, as they stand now, with ``game: null`` and a
  note that says so. The alternative was an error tile for the whole summer; this is the closest
  honest answer, and it is labelled as such. With no game played either there is nothing to
  compare, and the tile says ``game_not_found`` on the ``opponent`` field.

A club is required for the EuroLeague: there is no "favourite club" token (``$favorite_team`` is an
NBA id and is honoured only for the NBA), so an unchosen club is ``invalid_config`` on ``club``,
which is the cue for a client to open the configuration sheet on that row.

Availability
------------
The payload's own ``availability`` (``full``, or ``partial`` while a window is short or an
opponent-adjusted value is withheld) is the result's. When it is ``partial`` the payload's notes say
what is missing and become the result's notes; when it is ``full`` the result carries none (see
:mod:`nbastats.widgets.league_common`). The one exception is the "most recent game" substitute
above: the tile is not showing what it was asked for, so it is ``partial`` whatever the
statistics' own state, and its notes (the builder's and the one added here) say why.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from .. import nba_matchup
from ..api.errors import ApiError
from ..models import Game
from . import league_common as common
from .base import ResolveContext, WidgetError, invalid_config, resolve_season, resolve_subject_token

__all__ = ["resolve"]

#: Builder parameter names -> the tile's config keys, for the error's ``field``.
_NBA_FIELDS = {"homeTeamId": "team", "awayTeamId": "opponent", "teamId": "team"}
_EL_FIELDS = {"homeTeamId": "club", "awayTeamId": "opponentClub", "clubCode": "club"}

_NOTE_LAST_MET = (
    "No game is scheduled for {subject}, so this compares the two sides of its most recent game "
    "as they stand now."
)


def resolve(config: dict[str, Any], ctx: ResolveContext) -> tuple[dict[str, Any], str, list[str]]:
    """Resolve one ``team_matchup``."""
    league = common.league_of(config)
    window = int(config.get("window") or 5)
    if league == common.EUROLEAGUE_KEY:
        payload, fell_back = _euroleague(config, window)
    else:
        payload, fell_back = _nba(config, ctx, window)
    if fell_back:
        # The tile is not showing what it was asked for (the next game), so it is a degraded
        # result whatever the statistics' own state: ``partial``, with the payload's sentences.
        availability = payload.get("availability")
        return common.outcome(
            payload, availability="partial" if availability in (None, "full") else availability
        )
    return common.outcome(payload)


# --------------------------------------------------------------------------- the NBA


def _nba(config: dict[str, Any], ctx: ResolveContext, window: int) -> tuple[dict[str, Any], bool]:
    """``(payload, fell_back)``: ``fell_back`` is true for the "most recent game" substitute."""
    season = resolve_season(ctx, config.get("season"))
    team_id = resolve_subject_token(config.get("team"), "team", ctx, field="team")
    ctx.note_team(team_id)
    opponent_id: int | None = None
    if config.get("opponent") is not None:
        opponent_id = resolve_subject_token(config.get("opponent"), "team", ctx, field="opponent")
        if opponent_id == team_id:
            raise invalid_config("A team cannot be its own opponent.", "opponent")
    stamp = common.now()

    def matchup(**who: Any) -> dict[str, Any]:
        try:
            return nba_matchup.team_matchup(
                ctx.session, season=season, window=window, now=stamp, **who
            )
        except ApiError as exc:
            raise common.translate(exc, _NBA_FIELDS) from exc

    if opponent_id is not None:
        first = matchup(home=team_id, away=opponent_id)
        if first["game"] is None:
            second = matchup(home=opponent_id, away=team_id)
            if second["game"] is not None:
                return second, False
        return first, False

    try:
        return (
            nba_matchup.team_matchup(
                ctx.session, team=team_id, season=season, window=window, now=stamp
            ),
            False,
        )
    except ApiError as exc:
        if exc.code != "game_not_found":
            raise common.translate(exc, _NBA_FIELDS) from exc
    pair = _nba_last_pair(ctx, team_id, season)
    if pair is None:
        raise WidgetError(
            "game_not_found",
            f"No game is scheduled or played in {season} for this team. Choose an opponent.",
            field="opponent",
        )
    payload = matchup(home=pair[0], away=pair[1])
    payload["notes"] = [*payload["notes"], _NOTE_LAST_MET.format(subject="this team")]
    return payload, True


def _nba_last_pair(ctx: ResolveContext, team_id: int, season: str) -> tuple[int, int] | None:
    """``(home, away)`` of the team's most recent final game in ``season``, or ``None``."""
    row = ctx.session.execute(
        select(Game.home_team_id, Game.away_team_id)
        .where(Game.season == season, Game.status == "final")
        .where((Game.home_team_id == team_id) | (Game.away_team_id == team_id))
        .order_by(Game.game_date.desc(), Game.game_id.desc())
        .limit(1)
    ).first()
    return (int(row[0]), int(row[1])) if row is not None else None


# --------------------------------------------------------------------------- the EuroLeague


def _euroleague(config: dict[str, Any], window: int) -> tuple[dict[str, Any], bool]:
    """``(payload, fell_back)``, as for the NBA."""
    club = config.get("club")
    opponent = config.get("opponentClub")
    season = common.euroleague_season(config.get("season"))
    stamp = common.now()

    # The league first: with the EuroLeague off, "choose a club" would send the reader to pick from
    # a list that is not there. Once it is open, the club is the tile's own config to get right.
    with common.euroleague() as (session, read):
        if club is None:
            raise invalid_config("Choose a club for this tile.", "club")
        if opponent is not None and opponent == club:
            raise invalid_config("A club cannot be its own opponent.", "opponentClub")

        def matchup(**who: Any) -> dict[str, Any]:
            try:
                return read.team_matchup(session, season=season, window=window, now=stamp, **who)
            except ApiError as exc:
                raise common.translate(exc, _EL_FIELDS) from exc

        if opponent is not None:
            first = matchup(home=club, away=opponent)
            if first["game"] is None:
                second = matchup(home=opponent, away=club)
                if second["game"] is not None:
                    return second, False
            return first, False

        try:
            return (
                read.team_matchup(session, club=club, season=season, window=window, now=stamp),
                False,
            )
        except ApiError as exc:
            if exc.code != "game_not_found":
                raise common.translate(exc, _EL_FIELDS) from exc
        pair = _el_last_pair(session, club, season, stamp)
        if pair is None:
            raise WidgetError(
                "game_not_found",
                "No game is scheduled or played for this club in this season. Choose an opponent.",
                field="opponentClub",
            )
        payload = matchup(home=pair[0], away=pair[1])
        payload["notes"] = [*payload["notes"], _NOTE_LAST_MET.format(subject="this club")]
        return payload, True


def _el_last_pair(
    session: Any, club: str, season: str | None, stamp: Any
) -> tuple[str, str] | None:
    """``(home, away)`` of the club's most recent final game, or ``None``.

    Read through the EuroLeague's own context builder, so this module never names a EuroLeague
    table: ``club_games`` and ``game_has_result`` are its public read-side vocabulary.
    """
    queries = common.read_module(common.EUROLEAGUE_KEY, "read.queries")
    try:
        ctx = queries.build_context(session, season, now=stamp)
    except ApiError as exc:
        raise common.translate(exc) from exc
    played = [g for g in ctx.club_games(club) if queries.game_has_result(g)]
    if not played:
        return None
    last = max(played, key=lambda g: (g.game_date, g.game_id))
    return last.home_club_code, last.away_club_code
