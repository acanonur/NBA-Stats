"""Availability and headlines: ``/v1/availability``, ``/v1/availability/review-queue``,
``/v1/news`` and the two writes that belong with them.

``AvailabilityReport`` says, for each team playing on a day (or for one team's next game), who the
NBA's official injury report lists, what a person has entered by hand, and where each statement came
from and how old it is. ``NewsLinks`` is a short list of headlines: title, link, date and the
outlet's name, never an excerpt. The builders and the rules are
:mod:`nbastats.nba_matchup.availability_view` and :mod:`nbastats.nba_matchup.news_view`.

The reads
---------
``GET /availability``
    Query: ``teamId``, ``date`` (an ISO date, ``next`` (the default) or ``latest``), ``statuses``
    (comma-separated, each one of the five), ``includeNews``. With a team and the default date the
    scope is that team's next game. Every unknown status is ``400 invalid_status``.
``GET /availability/review-queue``
    Report rows whose player name matched nobody uniquely. A name is never guessed.
``GET /news``
    Query: ``teamId``, ``playerId``, ``limit`` (1 to 50, default 10).

The writes
----------
Three routes write, and each needs **either** the API key on a request with no ``Origin`` (the
native Mac app sends it) **or** a signed-in browser session with its CSRF token and a same-origin
``Origin`` (:func:`require_nba_write`). With neither configured or presented the answer is ``401``:
a write endpoint with no credential is the one thing this service does not offer, and
``X-Hardwood-Client`` is not a credential. Each commits explicitly (:func:`commit_after`); no read
does.

``POST /availability``
    Enter an override: ``{playerId, teamId?, gameId?, status, note?, sourceUrl?,
    sourcePublishedAt?}``. The status must be one of the five (``400 invalid_status``); the demo
    league refuses it, because invented games are never placed beside real statuses.
``DELETE /availability/{overrideId}``
    Clear an override. The row stays, with the time it was cleared.
``POST /news/links``
    Paste a headline: ``{title, link, publishedAt, sourceName, teamIds, playerIds}``. A link to a
    gambling operator's site is refused.

Every query, path and body name is on the allowed-parameter list, and the bodies forbid extra
fields: a client that sends a field this service does not know about (a number to compare with, say)
gets a ``400`` naming it instead of having it silently dropped.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy.orm import Session

from .. import nba_matchup
from ..nba_matchup import availability_view, news_view
from ..nba_matchup.queries import (
    build_context,
    get_now,
    internal_error,
    parse_team_id,
    team_not_found,
)
from ..nba_matchup.sources import freshness_for, news_keys
from . import deps
from .deps import SessionDep

__all__ = [
    "router",
    "require_nba_write",
    "entered_by",
    "commit_after",
    "Body",
    "AvailabilityBody",
    "NewsLinkBody",
]

router = APIRouter(tags=["availability"])

NowDep = Annotated[datetime, Depends(get_now)]


# --------------------------------------------------------------------------- who may write


def require_nba_write(request: Request) -> None:
    """The native app's API key, or a browser session with its CSRF token; otherwise ``401``.

    One shared gate (:func:`nbastats.api.deps.require_key_or_session_write`) so the NBA's writes
    and the EuroLeague's can never differ. The key is compared in constant time, only when the
    service is configured with one, and only on a request that carries no ``Origin``: a web page
    holding the key is still a web page. A keyless server refuses every write that has no
    session. See ``nbastats/api/deps.py`` for why no other header is a credential.
    """
    deps.require_key_or_session_write(request)


def entered_by(request: Request) -> str | None:
    """The signed-in user's id for a write, or ``None`` when the API key authorised it."""
    user = getattr(request.state, "user", None)
    found = getattr(user, "user_id", None) if user is not None else None
    return str(found)[:32] if found is not None else None


def commit_after(session: Session) -> None:
    """Commit a write, turning a database failure into a clean 500 rather than a half-write."""
    try:
        session.commit()
    except Exception as exc:  # noqa: BLE001 - the envelope's own 500 says what to do
        session.rollback()
        raise internal_error("The change could not be saved.") from exc


# --------------------------------------------------------------------------- request bodies


class Body(BaseModel):
    """Base of every request body: camelCase on the wire, no unknown fields."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class AvailabilityBody(Body):
    """``POST /v1/availability``: a status a person typed, and where it came from.

    The status is a plain string on purpose: a ``Literal`` would turn a typo into a generic
    ``400 bad_request``, and the contract has a code for it (``invalid_status``).
    """

    player_id: int
    status: str
    team_id: int | str | None = None
    game_id: str | None = None
    note: str | None = None
    source_url: str | None = None
    source_published_at: datetime | str | None = None


class NewsLinkBody(Body):
    """``POST /v1/news/links``: a headline a person pasted."""

    title: str
    link: str
    published_at: datetime | str
    source_name: str
    team_ids: list[int | str] = []
    player_ids: list[int | str] = []


# --------------------------------------------------------------------------- availability


@router.get("/availability", summary="Who is available, with the source and age of every status")
def availability(
    session: SessionDep,
    now: NowDep,
    team_id: Annotated[str | None, Query(alias="teamId")] = None,
    date: Annotated[str | None, Query(description="An ISO date, next or latest.")] = "next",
    statuses: Annotated[str | None, Query()] = None,
    include_news: Annotated[bool, Query(alias="includeNews")] = False,
) -> dict[str, Any]:
    return nba_matchup.availability_report(
        session,
        team=team_id,
        day=date,
        statuses=statuses,
        include_news=include_news,
        now=now,
    )


@router.get("/availability/review-queue", summary="Report rows whose player matched nobody")
def availability_review_queue(session: SessionDep, now: NowDep) -> dict[str, Any]:
    return availability_view.review_queue(build_context(session, None, now=now))


@router.post("/availability", status_code=201, summary="Enter a status for a player")
def post_availability(
    request: Request,
    session: SessionDep,
    now: NowDep,
    body: AvailabilityBody,
    _auth: Annotated[None, Depends(require_nba_write)],
) -> dict[str, Any]:
    ctx = build_context(session, None, now=now)
    team = None
    if body.team_id is not None:
        team = parse_team_id(body.team_id)
        if team is None:
            raise team_not_found(body.team_id)
    result = availability_view.record_override(
        ctx,
        player_id=body.player_id,
        status=body.status,
        team_id=team,
        game_id=body.game_id,
        note=body.note,
        source_url=body.source_url,
        source_published_at=body.source_published_at,
        entered_by_user_id=entered_by(request),
    )
    commit_after(session)
    return result


@router.delete("/availability/{overrideId}", summary="Clear an override (the row stays)")
def delete_availability(
    session: SessionDep,
    now: NowDep,
    override_id: Annotated[int, Path(alias="overrideId")],
    _auth: Annotated[None, Depends(require_nba_write)],
) -> dict[str, Any]:
    ctx = build_context(session, None, now=now, allow_empty=True)
    result = availability_view.clear_override(ctx, override_id)
    commit_after(session)
    return result


# --------------------------------------------------------------------------- news


@router.get("/news", summary="Headlines about a team or player (title, link, date, source)")
def news(
    session: SessionDep,
    now: NowDep,
    team_id: Annotated[str | None, Query(alias="teamId")] = None,
    player_id: Annotated[int | None, Query(alias="playerId")] = None,
    limit: Annotated[int | None, Query()] = None,
) -> dict[str, Any]:
    ctx = build_context(session, None, now=now)
    team = ctx.require_team(team_id) if team_id is not None else None
    return news_view.build_news(
        ctx,
        team_id=team,
        player_id=player_id,
        limit=limit,
        freshness=freshness_for(ctx, news_keys(ctx)),
    )


@router.post("/news/links", summary="Paste a headline link")
def post_news_link(
    session: SessionDep,
    now: NowDep,
    body: NewsLinkBody,
    response: Response,
    _auth: Annotated[None, Depends(require_nba_write)],
) -> dict[str, Any]:
    ctx = build_context(session, None, now=now)
    result = news_view.record_link(
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
