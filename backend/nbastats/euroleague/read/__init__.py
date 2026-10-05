"""The EuroLeague read side: every payload, built from the store and the shared core.

This package turns the EuroLeague store (and the model in :mod:`nbastats.euroleague.model`) into
the payloads of design section 9.4. It is a *library* first and an HTTP surface second: the routes
in :mod:`nbastats.euroleague.api.routes` are thin, and the functions below are what both the
routes and the dashboard's widget layer call, so a tile's payload and the REST payload are the
same object built by the same code.

The functions at the bottom of this module are that shared entry. Each takes an open session on
the EuroLeague store (the caller owns it), an optional ``now`` (aware UTC; a fixture or a test
passes a fixed clock, the routes pass the system's) and the parameters of the matching route, and
returns a JSON-ready ``dict``. Failures are :class:`nbastats.api.errors.ApiError` with the
contract's codes (``club_not_found``, ``game_not_found``, ``season_not_loaded``, ``bad_request``),
so a route needs no translation and a widget can attach the error body to one tile.

Module map
----------
``queries``       the context (season, clock, clubs, games, settings), the scoring scope, the memo
``availability``  statuses in force, the report, and the two writes (a status, a retraction)
``defense``       defence by opponent position (registration positions; opt-in workbook five-way)
``matchup``       two clubs side by side as they stood going into a game
``round``         rounds, the projection of a game, the slate and the round's scorers
``review``        how locked, reconstructed and imported projections compared with results
``teams``         the club directory, ``ClubView`` and the ratings table
``games``         the schedule and box scores
``stats``         player statistics (computed on read, nulls honoured column by column)
``news``          headlines (title, link, date, source only) and the pasted link
``sources``       the sources panel and every payload's ``freshness``
``method``        the constants with their provenance, the deviations and the limitations

Nothing here imports the NBA's modules, and nothing outside the EuroLeague package imports this
one by name (the isolation test holds both lines); the widget layer reaches it through the
late-bound registry or by string name.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from sqlalchemy.orm import Session

__all__ = [
    "KEYS_MATCHUP",
    "team_matchup",
    "defense_by_position",
    "availability_report",
    "slate_projections",
]

#: The sources behind a matchup (results and ratings, and who is missing).
KEYS_MATCHUP: Final[tuple[str, ...]] = ("el.workbook", "el.dataService", "el.manual")


def team_matchup(
    session: Session,
    *,
    club: str | None = None,
    home: str | None = None,
    away: str | None = None,
    game_id: str | None = None,
    season: str | None = None,
    window: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """``TeamMatchup``. Give ``game_id``; or ``club`` (its next scheduled game); or ``home`` and
    ``away`` (two clubs, and their game if one is still to be played)."""
    from .matchup import build_matchup, game_between, next_game_for_club
    from .queries import bad_request, build_context, context_for_game, game_start
    from .sources import freshness_for

    if game_id is not None:
        ctx = context_for_game(session, game_id, now=now)
        game = ctx.game(game_id)
        return build_matchup(
            ctx,
            home=game.home_club_code,
            away=game.away_club_code,
            game=game,
            window=window,
            freshness=freshness_for(ctx, KEYS_MATCHUP),
            cut_off=game_start(game),
        )
    ctx = build_context(session, season, now=now)
    if club is not None:
        game = next_game_for_club(ctx, ctx.require_club(club))
        return build_matchup(
            ctx,
            home=game.home_club_code,
            away=game.away_club_code,
            game=game,
            window=window,
            freshness=freshness_for(ctx, KEYS_MATCHUP),
        )
    if home is None or away is None:
        raise bad_request("Name a game, a club, or both clubs.", "homeTeamId")
    first, second = ctx.require_club(home), ctx.require_club(away)
    return build_matchup(
        ctx,
        home=first,
        away=second,
        game=game_between(ctx, first, second),
        window=window,
        freshness=freshness_for(ctx, KEYS_MATCHUP),
    )


def defense_by_position(
    session: Session,
    *,
    club: str | None = None,
    season: str | None = None,
    phase: str | None = None,
    window: int | None = 0,
    basis: str | None = None,
    scheme: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """``DefenseByPosition`` for ``club``, or ``DefenseByPositionTable`` when no club is named."""
    from .defense import (
        defense_payload,
        defense_table_payload,
        validate_basis,
        validate_scheme,
        validate_window,
    )
    from .queries import build_context, parse_phases
    from .sources import KEYS_STATS, freshness_for

    ctx = build_context(session, season, now=now)
    phases = parse_phases(phase)
    options = dict(
        scheme=validate_scheme(scheme),
        basis=validate_basis(basis),
        window=validate_window(window),
        phases=phases,
        freshness=freshness_for(ctx, KEYS_STATS),
    )
    if club is None:
        return defense_table_payload(ctx, **options)
    return defense_payload(ctx, ctx.require_club(club), **options)


def availability_report(
    session: Session,
    *,
    club: str | None = None,
    round_number: int | None = None,
    statuses: str | None = None,
    include_news: bool = False,
    season: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """``AvailabilityReport`` for a club, a round, or every club."""
    from ...shared.availability import InvalidStatusError, normalise_status
    from .availability import build_availability_report
    from .news import build_news
    from .queries import build_context, invalid_status
    from .sources import KEYS_AVAILABILITY, freshness_for, news_keys

    ctx = build_context(session, season, now=now)
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
    keys = (*KEYS_AVAILABILITY, *(news_keys(ctx) if include_news else ()))
    freshness = freshness_for(ctx, keys)
    news = None
    if include_news:
        club_code = ctx.require_club(club) if club else None
        news = build_news(ctx, team_id=club_code, player_id=None, limit=5, freshness=freshness)[
            "items"
        ]
    return build_availability_report(
        ctx,
        club_code=club,
        round_number=round_number,
        statuses=wanted,
        include_news=include_news,
        news=news,
        freshness=freshness,
    )


def slate_projections(
    session: Session,
    *,
    round_number: str | int | None = "next",
    season: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """``SlateProjections`` for a round (``next`` by default)."""
    from .queries import build_context
    from .round import build_slate, resolve_round
    from .sources import freshness_for

    ctx = build_context(session, season, now=now)
    number = resolve_round(ctx, round_number)
    return build_slate(ctx, number, freshness_for(ctx, KEYS_MATCHUP))
