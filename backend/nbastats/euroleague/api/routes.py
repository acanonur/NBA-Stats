"""``/v1/el``: the EuroLeague's routes (design section 9.3).

The router's prefix is ``/el``; ``api/app.py`` includes it under ``/v1`` with the same guards as
every other router (an API key or a session, and the rate limiter), and ``api/routes_euroleague.py``
is a three-line shim that exposes ``router`` from here. The league is chosen by the URL, never by
a header, which is half of why a EuroLeague row cannot reach an NBA view.

The shape of this module
------------------------
A route does three things: take the EuroLeague session and the clock, build one
:class:`~nbastats.euroleague.read.queries.ReadContext`, and hand it to a builder in
:mod:`nbastats.euroleague.read`. No route holds logic of its own, so the dashboard's widgets, the
fixtures and these routes cannot drift apart.

Suffixes are identical across leagues: ``matchups``, ``teams/{id}/matchup``, ``games/{id}/matchup``,
``teams/{id}/defense-by-position``, ``defense-by-position``, ``projections``,
``games/{id}/projection``, ``projections/review``, ``availability``, ``availability/review-queue``,
``news``, ``news/links``, ``sources`` and ``model-settings``. Under ``/v1/el`` a team id is a club
code. The EuroLeague-only routes are the workbook's other sheets: ``meta``, ``health``, ``sync``,
``teams``, ``teams/{clubCode}``, ``rounds/{round}``, ``rounds/{round}/scorers``, ``games``,
``games/{gameId}``, ``players/{personCode}``, ``players/{personCode}/gamelog``,
``stats/players``, ``ratings``, ``method`` and ``review-queue``.

Parameters
----------
Every query, path and body field is on the allowed-parameter list
(:data:`nbastats.shared.market_guard.ALLOWED_PARAMETERS`); path parameters are declared with an
alias so OpenAPI shows ``clubCode``, not ``club_code``. ``season`` accepts ``E2026``, ``2026-27``
or ``latest``. ``round`` is a number or, for projections, ``next``.

States
------
Every route but ``meta`` and ``health`` answers ``503 league_unavailable`` with the reason while
the EuroLeague is off, misconfigured or holds no source. ``meta`` and ``health`` always answer:
the state *is* their payload.

Writes
------
``POST /availability``, ``DELETE /availability/{statusId}``, ``POST /news/links`` and
``PATCH /model-settings`` need the API key or a session with its CSRF token
(:func:`~nbastats.euroleague.api.deps.require_el_write`) and commit explicitly; every read leaves
the session untouched.

Registering with the league registry
------------------------------------
Importing this module registers the EuroLeague with :mod:`nbastats.shared.league_registry`, so
``GET /v1/leagues`` and the widget layer can find it without importing it. The router is only
imported when the EuroLeague is enabled, so a disabled EuroLeague is simply never registered.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from .. import read
from ..read import availability as availability_module
from ..read import games as games_module
from ..read import method as method_module
from ..read import news as news_module
from ..read import review as review_module
from ..read import round as round_module
from ..read import sources as sources_module
from ..read import stats as stats_module
from ..read import teams as teams_module
from ..db import bump_sync_version
from ..read.queries import ReadContext, bad_request, build_context, context_for_game, naive_utc
from ..settings import InvalidSettingError, set_setting, validate_setting
from .deps import (
    ElSession,
    MaybeSession,
    NowDep,
    commit_after,
    entered_by,
    require_el_write,
)
from .serializers import (
    AvailabilityBody,
    ModelSettingsPatch,
    NewsLinkBody,
    model_settings_payload,
)

__all__ = ["router"]

_LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/el", tags=["euroleague"])

Season = Annotated[str | None, Query(description="E2026, 2026-27 or latest.")]
ClubPath = Annotated[str, Path(alias="clubCode")]
GamePath = Annotated[str, Path(alias="gameId")]
PersonPath = Annotated[str, Path(alias="personCode")]


def _ctx(session: ElSession, season: str | None, now: Any) -> ReadContext:
    return build_context(session, season, now=now)


# --------------------------------------------------------------------------- state


@router.get("/meta", summary="What the EuroLeague is, its state, and what to expect")
def meta(session: MaybeSession, now: NowDep) -> dict[str, Any]:
    return sources_module.build_meta(session, now=now)


@router.get("/health", summary="The EuroLeague's state and cursor (never needs a key)")
def health(session: MaybeSession, now: NowDep) -> dict[str, Any]:
    return sources_module.build_health(session, now=now)


@router.get("/sync", summary="The EuroLeague's freshness cursor")
def sync(session: ElSession, now: NowDep) -> dict[str, Any]:
    return sources_module.build_sync(session, now=now)


# --------------------------------------------------------------------------- clubs and rounds


@router.get("/teams", summary="Every club: record, scoring, points allowed, rating")
def teams(session: ElSession, now: NowDep, season: Season = None) -> dict[str, Any]:
    ctx = _ctx(session, season, now)
    return teams_module.build_teams(
        ctx, sources_module.freshness_for(ctx, sources_module.KEYS_STATS)
    )


@router.get("/teams/{clubCode}", summary="One club: scoring, rating, squad, absences, next game")
def club_view(
    session: ElSession, now: NowDep, club_code: ClubPath, season: Season = None
) -> dict[str, Any]:
    ctx = _ctx(session, season, now)
    club = ctx.require_club(club_code)
    return teams_module.build_club_view(
        ctx, club, sources_module.freshness_for(ctx, read.KEYS_MATCHUP)
    )


@router.get("/rounds/{round}", summary="One round: every game with its projection")
def round_view(
    session: ElSession,
    now: NowDep,
    round_: Annotated[int, Path(alias="round")],
    season: Season = None,
) -> dict[str, Any]:
    ctx = _ctx(session, season, now)
    number = round_module.resolve_round(ctx, round_)
    return round_module.build_round_view(
        ctx, number, sources_module.freshness_for(ctx, read.KEYS_MATCHUP)
    )


@router.get("/rounds/{round}/scorers", summary="A round's top scorers per club, no lines")
def round_scorers(
    session: ElSession,
    now: NowDep,
    round_: Annotated[int, Path(alias="round")],
    per_club: Annotated[int, Query(alias="perClub", ge=1, le=15)] = 3,
    season: Season = None,
) -> dict[str, Any]:
    ctx = _ctx(session, season, now)
    number = round_module.resolve_round(ctx, round_)
    return round_module.build_round_scorers(
        ctx, number, per_club, sources_module.freshness_for(ctx, read.KEYS_MATCHUP)
    )


@router.get("/ratings", summary="Club ratings as of a round")
def ratings(
    session: ElSession,
    now: NowDep,
    as_of_round: Annotated[int | None, Query(alias="asOfRound", ge=0)] = None,
    season: Season = None,
) -> dict[str, Any]:
    ctx = _ctx(session, season, now)
    return teams_module.build_ratings(
        ctx, as_of_round, sources_module.freshness_for(ctx, sources_module.KEYS_STATS)
    )


# --------------------------------------------------------------------------- games and players


@router.get("/games", summary="The schedule and results (EuroLeague games only)")
def games(
    session: ElSession,
    now: NowDep,
    round_: Annotated[int | None, Query(alias="round", ge=1)] = None,
    club_code: Annotated[str | None, Query(alias="clubCode")] = None,
    phase: Annotated[str | None, Query()] = None,
    season: Season = None,
) -> dict[str, Any]:
    ctx = _ctx(session, season, now)
    return games_module.build_games(
        ctx,
        round_number=round_,
        club_code=club_code,
        phase=phase,
        freshness=sources_module.freshness_for(ctx, sources_module.KEYS_STATS),
    )


@router.get("/games/{gameId}", summary="A box score")
def box_score(session: ElSession, now: NowDep, game_id: GamePath) -> dict[str, Any]:
    ctx = context_for_game(session, game_id, now=now)
    return games_module.build_box_score(
        ctx, ctx.game(game_id), sources_module.freshness_for(ctx, sources_module.KEYS_STATS)
    )


@router.get("/players/{personCode}", summary="One player: season line, rates, availability")
def player_detail(
    session: ElSession, now: NowDep, person_code: PersonPath, season: Season = None
) -> dict[str, Any]:
    ctx = _ctx(session, season, now)
    return stats_module.build_player_detail(
        ctx, person_code, sources_module.freshness_for(ctx, read.KEYS_MATCHUP)
    )


@router.get("/players/{personCode}/gamelog", summary="One player's official games")
def player_gamelog(
    session: ElSession,
    now: NowDep,
    person_code: PersonPath,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
    season: Season = None,
) -> dict[str, Any]:
    ctx = _ctx(session, season, now)
    return stats_module.build_player_gamelog(
        ctx, person_code, limit, sources_module.freshness_for(ctx, sources_module.KEYS_STATS)
    )


@router.get("/stats/players", summary="Player statistics (per game, totals or per 40)")
def player_stats(
    session: ElSession,
    now: NowDep,
    per_mode: Annotated[str | None, Query(alias="perMode")] = None,
    sort: Annotated[str | None, Query()] = None,
    club_code: Annotated[str | None, Query(alias="clubCode")] = None,
    min_games: Annotated[int | None, Query(alias="minGames", ge=0)] = None,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
    season: Season = None,
) -> dict[str, Any]:
    ctx = _ctx(session, season, now)
    return stats_module.build_stats_table(
        ctx,
        per_mode=per_mode,
        sort=sort,
        club_code=club_code,
        min_games=min_games,
        limit=limit,
        freshness=sources_module.freshness_for(ctx, sources_module.KEYS_STATS),
    )


# --------------------------------------------------------------------------- matchups and defence


@router.get("/matchups", summary="Two clubs side by side")
def matchups(
    session: ElSession,
    now: NowDep,
    home_team_id: Annotated[str, Query(alias="homeTeamId")],
    away_team_id: Annotated[str, Query(alias="awayTeamId")],
    season: Season = None,
    window: Annotated[int | None, Query()] = None,
) -> dict[str, Any]:
    return read.team_matchup(
        session, home=home_team_id, away=away_team_id, season=season, window=window, now=now
    )


@router.get("/teams/{clubCode}/matchup", summary="A club's next game")
def club_matchup(
    session: ElSession,
    now: NowDep,
    club_code: ClubPath,
    season: Season = None,
    window: Annotated[int | None, Query()] = None,
) -> dict[str, Any]:
    return read.team_matchup(session, club=club_code, season=season, window=window, now=now)


@router.get("/games/{gameId}/matchup", summary="A game's matchup, inputs cut off before tip-off")
def game_matchup(
    session: ElSession,
    now: NowDep,
    game_id: GamePath,
    window: Annotated[int | None, Query()] = None,
) -> dict[str, Any]:
    return read.team_matchup(session, game_id=game_id, window=window, now=now)


@router.get(
    "/teams/{clubCode}/defense-by-position", summary="Points a club allows, by opponent position"
)
def club_defense(
    session: ElSession,
    now: NowDep,
    club_code: ClubPath,
    season: Season = None,
    phase: Annotated[str | None, Query()] = None,
    window: Annotated[int | None, Query()] = 0,
    basis: Annotated[str | None, Query()] = None,
    scheme: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    return read.defense_by_position(
        session,
        club=club_code,
        season=season,
        phase=phase,
        window=window,
        basis=basis,
        scheme=scheme,
        now=now,
    )


@router.get("/defense-by-position", summary="Every club's defence by position")
def league_defense(
    session: ElSession,
    now: NowDep,
    season: Season = None,
    phase: Annotated[str | None, Query()] = None,
    window: Annotated[int | None, Query()] = 0,
    basis: Annotated[str | None, Query()] = None,
    scheme: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    return read.defense_by_position(
        session,
        club=None,
        season=season,
        phase=phase,
        window=window,
        basis=basis,
        scheme=scheme,
        now=now,
    )


# --------------------------------------------------------------------------- projections


@router.get("/projections", summary="A round's projections")
def projections(
    session: ElSession,
    now: NowDep,
    round_: Annotated[
        str | None, Query(alias="round", description="next or a round number.")
    ] = "next",
    season: Season = None,
) -> dict[str, Any]:
    return read.slate_projections(session, round_number=round_, season=season, now=now)


@router.get("/projections/review", summary="Projections against results")
def projection_review(
    session: ElSession,
    now: NowDep,
    round_: Annotated[int | None, Query(alias="round", ge=1)] = None,
    season: Season = None,
) -> dict[str, Any]:
    ctx = _ctx(session, season, now)
    if round_ is not None and round_ not in ctx.rounds():
        raise bad_request(f"There is no round {round_} in this season.", "round")
    return review_module.build_review(
        ctx, round_number=round_, freshness=sources_module.freshness_for(ctx, read.KEYS_MATCHUP)
    )


@router.get("/games/{gameId}/projection", summary="One game's projection, locked and history")
def game_projection(session: ElSession, now: NowDep, game_id: GamePath) -> dict[str, Any]:
    ctx = context_for_game(session, game_id, now=now)
    return round_module.projection_detail(
        ctx, ctx.game(game_id), sources_module.freshness_for(ctx, read.KEYS_MATCHUP)
    )


# --------------------------------------------------------------------------- availability


@router.get("/availability", summary="Who is available, with the source and age of every status")
def availability(
    session: ElSession,
    now: NowDep,
    club_code: Annotated[str | None, Query(alias="clubCode")] = None,
    team_id: Annotated[str | None, Query(alias="teamId")] = None,
    round_: Annotated[int | None, Query(alias="round", ge=1)] = None,
    statuses: Annotated[str | None, Query()] = None,
    include_news: Annotated[bool, Query(alias="includeNews")] = False,
    season: Season = None,
) -> dict[str, Any]:
    return read.availability_report(
        session,
        club=club_code or team_id,
        round_number=round_,
        statuses=statuses,
        include_news=include_news,
        season=season,
        now=now,
    )


@router.get("/availability/review-queue", summary="Statuses whose player matched nobody")
def availability_review_queue(session: ElSession, now: NowDep) -> dict[str, Any]:
    return availability_module.review_queue(_ctx(session, None, now))


@router.get("/review-queue", summary="Unmatched people and statuses from import or reconcile")
def review_queue(session: ElSession, now: NowDep) -> dict[str, Any]:
    return availability_module.build_review_queue(_ctx(session, None, now))


@router.post("/availability", status_code=201, summary="Enter a status with its source")
def post_availability(
    request: Request,
    session: ElSession,
    now: NowDep,
    body: AvailabilityBody,
    _auth: Annotated[None, Depends(require_el_write)],
) -> dict[str, Any]:
    ctx = _ctx(session, None, now)
    result = availability_module.record_status(
        ctx,
        club_code=body.club_code,
        status=body.status,
        source_label=body.source_label,
        source_published_at=body.source_published_at,
        person_code=body.person_code,
        player_name=body.player_name,
        game_id=body.game_id,
        reason_category=body.reason_category,
        reason_text=body.reason_text,
        expected_return_text=body.expected_return_text,
        source_url=body.source_url,
        entered_by_user_id=entered_by(request),
    )
    commit_after(session)
    return result


@router.delete("/availability/{statusId}", summary="Retract a status (appends a retraction)")
def delete_availability(
    request: Request,
    session: ElSession,
    now: NowDep,
    status_id: Annotated[int, Path(alias="statusId")],
    _auth: Annotated[None, Depends(require_el_write)],
) -> dict[str, Any]:
    ctx = _ctx(session, None, now)
    result = availability_module.retract_status(
        ctx, status_id, entered_by_user_id=entered_by(request)
    )
    commit_after(session)
    return result


# --------------------------------------------------------------------------- news


@router.get("/news", summary="Headlines about a club or player (title, link, date, source)")
def news(
    session: ElSession,
    now: NowDep,
    team_id: Annotated[str | None, Query(alias="teamId")] = None,
    player_id: Annotated[str | None, Query(alias="playerId")] = None,
    limit: Annotated[int | None, Query(ge=1, le=50)] = None,
) -> dict[str, Any]:
    ctx = _ctx(session, None, now)
    keys = sources_module.news_keys(ctx)
    return news_module.build_news(
        ctx,
        team_id=team_id,
        player_id=player_id,
        limit=limit,
        freshness=sources_module.freshness_for(ctx, keys),
    )


@router.post("/news/links", summary="Paste a headline link")
def post_news_link(
    session: ElSession,
    now: NowDep,
    body: NewsLinkBody,
    response: Response,
    _auth: Annotated[None, Depends(require_el_write)],
) -> dict[str, Any]:
    ctx = _ctx(session, None, now)
    result = news_module.record_link(
        ctx,
        title=body.title,
        link=body.link,
        published_at=body.published_at,
        source_name=body.source_name,
        team_ids=body.team_ids,
        player_ids=body.player_ids,
    )
    commit_after(session)
    response.status_code = 201 if result["created"] else 200
    return result


# --------------------------------------------------------------------------- sources, method, settings


@router.get("/sources", summary="Where every number came from, and how current it is")
def sources(session: ElSession, now: NowDep) -> dict[str, Any]:
    ctx = _ctx(session, None, now)
    return sources_module.build_source_list(ctx, sources_module.freshness_for(ctx, ()))


@router.get("/method", summary="Constants with provenance, deviations and limitations")
def method(session: ElSession, now: NowDep) -> dict[str, Any]:
    ctx = _ctx(session, None, now)
    return method_module.build_method(ctx, sources_module.freshness_for(ctx, ()))


@router.get("/model-settings", summary="The model's settings and where each came from")
def model_settings(session: ElSession, now: NowDep) -> dict[str, Any]:
    ctx = _ctx(session, None, now)
    return model_settings_payload(ctx, sources_module.freshness_for(ctx, ()))


@router.patch("/model-settings", summary="Change allowlisted model settings")
def patch_model_settings(
    session: ElSession,
    now: NowDep,
    body: ModelSettingsPatch,
    _auth: Annotated[None, Depends(require_el_write)],
) -> dict[str, Any]:
    if not body.settings:
        raise bad_request("Send at least one setting.", "settings")
    checked: list[tuple[str, float]] = []
    for item in body.settings:  # validate all first: a bad value changes nothing
        try:
            checked.append((item.key, validate_setting(item.key, item.value)))
        except InvalidSettingError as exc:
            raise bad_request(
                str(exc), "key" if "not a model setting" in str(exc) else "value"
            ) from exc
    moment = naive_utc(now)
    try:
        for key, value in checked:
            set_setting(session, key, value, "manual", moment)
    except InvalidSettingError as exc:  # e.g. an inconsistent set of status chances
        session.rollback()
        raise bad_request(str(exc), "value") from exc
    bump_sync_version(session, None, None, moment)
    commit_after(session)
    ctx = _ctx(session, None, now)
    return model_settings_payload(ctx, sources_module.freshness_for(ctx, ()))


def _register_with_the_league_registry() -> None:
    """Make ``GET /v1/leagues`` and the widget layer able to find the EuroLeague (see the
    module docstring). A failure here must never stop the routes from loading."""
    try:
        from ..registry_provider import register

        register()
    except Exception:  # noqa: BLE001 - the registry is a convenience, never a dependency
        _LOG.exception("the EuroLeague could not register with the league registry")


_register_with_the_league_registry()
