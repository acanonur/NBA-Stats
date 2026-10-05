"""The NBA read side: matchup, defence by position, projections, availability, news and sources.

This package turns the NBA stats store (and the intel tables that sit beside it in the same file)
into the payloads of design section 9.4. It is a *library* first and an HTTP surface second: the
routes in ``nbastats/api/routes_matchups.py`` and its siblings are thin, and the functions below are
what both the routes and the dashboard's widget layer call, so a tile's payload and the REST payload
are the same object built by the same code.

The functions at the bottom of this module are that shared entry. Each takes an open session on the
stats store (the caller owns it), an optional ``now`` (aware UTC; a fixture or a test passes a fixed
clock, the routes pass the system's) and the parameters of the matching route, and returns a
JSON-ready ``dict``. Failures are :class:`nbastats.api.errors.ApiError` with the contract's codes
(``team_not_found``, ``game_not_found``, ``season_not_loaded``, ``bad_request``,
``invalid_status``), so a route needs no translation and a widget can attach the error body to one
tile.

Module map
----------
``queries``            the context (season, clock, teams, games, settings), the scoring scope, the
                       ``date=next`` token, the memo
``matchup``            two teams side by side as they stood going into a game
``defense``            defence by opponent position (listed positions, one basis per player-season)
``projection``         the team-score model: league level, ratings, the walk-forward fold, the
                       injury layer's inputs, the spreads and their calibration
``ledger``             the frozen projections: refresh, lock (refused after tip-off), residuals
``slate``              the ``GameProjection`` payload, the day's slate, the detail and the review
``availability_view``  statuses in force, the report, and the two writes (an override, its clear)
``news_view``          headlines (title, link, date, source only) and the pasted link
``sources``            the sources panel, every payload's ``freshness`` and the NBA's league row
``settings_view``      the model's allowlisted constants with their provenance, and the patch
``fixtures_export``    the committed payload fixtures, built from the invented demo league

What this package does not do
-----------------------------
It fetches nothing and parses no sports payload: the ingest, the injury report reader and the
headline feeds live in ``nbastats/ingest`` and ``nbastats/nba_intel``. It writes only the three
things a person asks it to (an availability override, a pasted headline link, a model setting) and
the projection ledger the scheduler asks for, and every other function leaves the session
untouched. It never imports the EuroLeague (``nbastats/euroleague`` is sealed; the league registry
in :mod:`nbastats.shared.league_registry` is how ``/v1/leagues`` describes it), and the fantasy
toolkit never imports it.

There is no market vocabulary here and none to add: no line, no probability of beating one, no
edge, no pick, and no probability of winning. A projected score, margin, winner and combined
points are analytics; what they are compared with is what happened.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Final, Sequence

from sqlalchemy.orm import Session

__all__ = [
    "team_matchup",
    "defense_by_position",
    "availability_report",
    "slate_projections",
    "projection_review",
    "game_projection",
]

_TOKENS: Final = ("next", "latest")


def _day(session: Session, value: str | date | None, now: datetime | None, season: str | None):
    """Resolve a ``date`` parameter: an ISO date, ``next`` (the next slate) or ``latest`` (the
    newest day with a final game). ``None`` means ``next``. Returns ``(date | None, season)``."""
    from .queries import (
        bad_request,
        latest_final_date,
        next_slate_date,
        resolve_season,
        season_of_date,
        utc_now,
    )

    moment = now if now is not None else utc_now()
    token = value.strip().lower() if isinstance(value, str) else value
    if token in (None, "", "next"):
        target = resolve_season(session, season)
        return next_slate_date(session, moment, target), target
    if token == "latest":
        target = resolve_season(session, season)
        return latest_final_date(session, target), target
    if isinstance(value, date):
        day = value
    else:
        try:
            day = date.fromisoformat(str(value).strip())
        except ValueError as exc:
            raise bad_request(
                f"{value!r} is not an ISO date such as '2026-01-02', or one of {list(_TOKENS)}.",
                "date",
            ) from exc
    found = season_of_date(session, day)
    return day, (
        resolve_season(session, season) if season else found or resolve_season(session, None)
    )


def team_matchup(
    session: Session,
    *,
    team: str | int | None = None,
    home: str | int | None = None,
    away: str | int | None = None,
    game_id: str | None = None,
    season: str | None = None,
    season_type: str | None = None,
    window: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """``TeamMatchup``. Give ``game_id``; or ``team`` (its next scheduled game); or ``home`` and
    ``away`` (two teams, and their game if one is still to be played)."""
    from .matchup import build_matchup, game_between, next_game_for_team
    from .queries import bad_request, build_context, context_for_game
    from .sources import KEYS_MATCHUP, freshness_for

    if game_id is not None:
        ctx = context_for_game(session, game_id, now=now)
        game = ctx.game(game_id)
        return build_matchup(
            ctx,
            home=game.home_team_id,
            away=game.away_team_id,
            game=game,
            window=window,
            freshness=freshness_for(ctx, KEYS_MATCHUP),
            cut_off=ctx.start_of(game),
        )
    ctx = build_context(session, season, season_type=season_type, now=now)
    freshness = freshness_for(ctx, KEYS_MATCHUP)
    if team is not None:
        team_id = ctx.require_team(team)
        game = next_game_for_team(ctx, team_id)
        return build_matchup(
            ctx,
            home=game.home_team_id,
            away=game.away_team_id,
            game=game,
            window=window,
            freshness=freshness,
        )
    if home is None or away is None:
        raise bad_request("Name a game, a team, or both teams.", "homeTeamId")
    first, second = ctx.require_team(home, field_name="homeTeamId"), ctx.require_team(
        away, field_name="awayTeamId"
    )
    return build_matchup(
        ctx,
        home=first,
        away=second,
        game=game_between(ctx, first, second),
        window=window,
        freshness=freshness,
    )


def defense_by_position(
    session: Session,
    *,
    team: str | int | None = None,
    season: str | None = None,
    season_type: str | None = None,
    window: int | None = 0,
    basis: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """``DefenseByPosition`` for ``team``, or ``DefenseByPositionTable`` when no team is named."""
    from .defense import (
        defense_payload,
        defense_table_payload,
        validate_basis,
        validate_window,
    )
    from .queries import build_context
    from .sources import KEYS_STATS, freshness_for

    ctx = build_context(session, season, season_type=season_type, now=now)
    options = dict(
        basis=validate_basis(basis),
        window=validate_window(window),
        freshness=freshness_for(ctx, KEYS_STATS),
    )
    if team is None:
        return defense_table_payload(ctx, **options)
    return defense_payload(ctx, ctx.require_team(team), **options)


def availability_report(
    session: Session,
    *,
    team: str | int | None = None,
    day: str | date | None = "next",
    statuses: str | None = None,
    include_news: bool = False,
    season: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """``AvailabilityReport`` for a day's slate, or for one team's next game.

    With a ``team`` and the default ``day`` the scope is that team's next scheduled game, which
    need not be on the next league-wide slate date.
    """
    from ..shared.availability import InvalidStatusError, normalise_status
    from .availability_view import build_availability_report
    from .news_view import build_news
    from .queries import build_context, invalid_status
    from .sources import KEYS_AVAILABILITY, freshness_for, news_keys

    wanted: list[str] | None = None
    if statuses is not None and statuses.strip():
        wanted = []
        for part in statuses.split(","):
            try:
                value = normalise_status(part)
            except InvalidStatusError as exc:
                raise invalid_status(str(exc), "statuses") from exc
            if value is not None and value not in wanted:
                wanted.append(value)
    token = day.strip().lower() if isinstance(day, str) else day
    if team is not None and token in (None, "", "next"):
        resolved_day, target = None, season
    else:
        resolved_day, target = _day(session, day, now, season)
    ctx = build_context(session, target, now=now)
    team_id = ctx.require_team(team) if team is not None else None
    keys = (*KEYS_AVAILABILITY, *(news_keys(ctx) if include_news else ()))
    freshness = freshness_for(ctx, keys)
    news = None
    if include_news:
        news = build_news(ctx, team_id=team_id, player_id=None, limit=5, freshness=freshness)[
            "items"
        ]
    return build_availability_report(
        ctx,
        team_id=team_id,
        day=resolved_day,
        statuses=wanted,
        news=news,
        include_news=include_news,
        freshness=freshness,
    )


def slate_projections(
    session: Session,
    *,
    day: str | date | None = "next",
    team_ids: Sequence[str | int] = (),
    season: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """``SlateProjections`` for a day (``next`` by default) and, optionally, some teams."""
    from .queries import build_context
    from .slate import build_slate
    from .sources import KEYS_MATCHUP, freshness_for

    resolved_day, target = _day(session, day, now, season)
    ctx = build_context(session, target, now=now)
    teams = [ctx.require_team(t, field_name="teamIds") for t in team_ids]
    return build_slate(ctx, resolved_day, teams, freshness_for(ctx, KEYS_MATCHUP))


def projection_review(
    session: Session,
    *,
    day: str | date | None = None,
    season: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """``ProjectionReview``: locked projections against what happened, for a day or a season."""
    from .queries import build_context
    from .slate import build_review
    from .sources import KEYS_MATCHUP, freshness_for

    resolved_day: date | None = None
    target = season
    if day is not None and not (isinstance(day, str) and not day.strip()):
        resolved_day, target = _day(session, day, now, season)
    ctx = build_context(session, target, now=now)
    return build_review(ctx, day=resolved_day, freshness=freshness_for(ctx, KEYS_MATCHUP))


def game_projection(
    session: Session, game_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """``GameProjectionDetail``: the current projection, the frozen one, the history."""
    from .queries import context_for_game
    from .slate import projection_detail
    from .sources import KEYS_MATCHUP, freshness_for

    ctx = context_for_game(session, game_id, now=now)
    return projection_detail(ctx, ctx.game(game_id), freshness_for(ctx, KEYS_MATCHUP))
