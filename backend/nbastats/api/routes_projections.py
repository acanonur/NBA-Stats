"""Projections and the model's settings: ``/v1/projections``, ``/v1/projections/review``,
``/v1/games/{gameId}/projection`` and ``/v1/model-settings``.

A projected score for each side of each game, with the margin, the projected winner (or a
toss-up), the combined points, what absences are worth and a range once a spread has been fitted.
**There is no probability of winning in any of these, and no line or total to compare with**
(``contracts/CONTRACT.md`` section 11). The builders are :mod:`nbastats.nba_matchup.slate`,
:mod:`nbastats.nba_matchup.projection` and :mod:`nbastats.nba_matchup.ledger`.

The reads
---------
``GET /projections``
    ``SlateProjections`` for a day. Query: ``date`` (an ISO date, ``next``, the default, or
    ``latest``) and ``teamIds`` (comma-separated). ``next`` is the next date with scheduled games,
    taken from the store, so a stale schedule never reads as "nothing is scheduled".
``GET /games/{gameId}/projection``
    ``GameProjectionDetail``: the current projection, the one frozen before tip-off, and the history
    of what the model said. A projection is never shown as a prediction after the lock deadline:
    from then on it is the frozen one, or one rebuilt from inputs recorded before it, and says so.
``GET /projections/review``
    ``ProjectionReview``: locked projections against what happened, with rebuilt ones reported
    apart. Query: ``date`` and ``season``.
``GET /model-settings``
    The sixteen allowlisted constants, each with its value, provenance and whether it is still the
    default.

The write
---------
``PATCH /model-settings`` takes ``{settings: [{key, value}]}`` and needs the API key or a signed-in
session with its CSRF token (the guard shared with the availability writes). The patch is validated
as a whole and applied atomically; a key outside the allowlist is ``400 bad_request`` naming it. The
body has no provenance field: a value set here is ``manual`` by definition.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query

from .. import nba_matchup
from ..nba_matchup import settings_view
from ..nba_matchup.queries import build_context, get_now
from ..nba_matchup.sources import freshness_for
from .deps import SessionDep
from .routes_availability import Body, commit_after, require_nba_write

__all__ = ["router", "ModelSettingsPatch", "SettingItem"]

router = APIRouter(tags=["projections"])

NowDep = Annotated[datetime, Depends(get_now)]
GamePath = Annotated[str, Path(alias="gameId")]


class SettingItem(Body):
    key: str
    value: float


class ModelSettingsPatch(Body):
    """``PATCH /v1/model-settings``: one or more allowlisted settings."""

    settings: list[SettingItem]


@router.get("/projections", summary="A day's projected scores")
def projections(
    session: SessionDep,
    now: NowDep,
    date: Annotated[str | None, Query(description="An ISO date, next or latest.")] = "next",
    team_ids: Annotated[str | None, Query(alias="teamIds", description="Comma-separated.")] = None,
) -> dict[str, Any]:
    ids = [part.strip() for part in (team_ids or "").split(",") if part.strip()]
    return nba_matchup.slate_projections(session, day=date, team_ids=ids, now=now)


@router.get("/projections/review", summary="Projections against results")
def projection_review(
    session: SessionDep,
    now: NowDep,
    date: Annotated[str | None, Query(description="An ISO date.")] = None,
    season: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    return nba_matchup.projection_review(session, day=date, season=season, now=now)


@router.get("/games/{gameId}/projection", summary="One game's projection, locked and history")
def game_projection(session: SessionDep, now: NowDep, game_id: GamePath) -> dict[str, Any]:
    return nba_matchup.game_projection(session, game_id, now=now)


@router.get("/model-settings", summary="The model's settings and where each came from")
def model_settings(session: SessionDep, now: NowDep) -> dict[str, Any]:
    ctx = build_context(session, None, now=now, allow_empty=True)
    return settings_view.model_settings_payload(ctx, freshness_for(ctx, ()))


@router.patch("/model-settings", summary="Change allowlisted model settings")
def patch_model_settings(
    session: SessionDep,
    now: NowDep,
    body: ModelSettingsPatch,
    _auth: Annotated[None, Depends(require_nba_write)],
) -> dict[str, Any]:
    ctx = build_context(session, None, now=now, allow_empty=True)
    settings_view.apply_settings_patch(ctx, [(i.key, i.value) for i in body.settings], now)
    commit_after(session)
    fresh = build_context(session, None, now=now, allow_empty=True)
    return settings_view.model_settings_payload(fresh, freshness_for(fresh, ()))
