"""``POST /v1/dashboard/resolve`` and ``GET /v1/dashboard/resolve-preset/{presetKey}``.

The endpoint the whole app renders from. One round trip carries a dashboard, and the
contract's central promise (``contracts/CONTRACT.md`` §3) is that **one bad tile degrades one
tile**:

* an invalid config is that widget's ``status: "error"`` with ``invalid_config`` — never a
  4xx for the request, because the other 23 widgets were fine;
* an exception inside a resolver is caught, logged with the request id, and becomes
  ``internal_error`` on that result alone;
* only the 24-widget cap, which is a property of the request rather than of any widget, is an
  HTTP ``400`` (``too_many_widgets``).

``knownSyncVersion`` is the cheap path. The service's sync version increments once per
finalized game (§8), so a client whose version already matches the server's is holding
payloads that cannot have changed: those widgets come back ``"unchanged"`` with a ``null``
payload and the client keeps what it has.

``resolvedContext`` echoes the subjects that were actually used, so a client that sent no
favourite learns who the dashboard ended up being about and can offer "pin this player".

The one exception to "one bad tile degrades one tile" is identity, not data
--------------------------------------------------------------------------------
Hardwood Web signs in over a session, and a signed-in user's own stored
``favoritePlayerId`` / ``favoriteTeamId`` (``accounts.models.User``) fill in for a request's
``context.favoritePlayerId`` / ``context.favoriteTeamId`` whenever the client left them
``null`` — a client-sent value always wins, so an iOS request (which always sends its own
favourites) is byte-identical to today's, and every existing widget-layer test is untouched.
That substitution happens once, at the single :meth:`~nbastats.widgets.base.ResolveContext.
from_request` call site below, by reading ``request.state.user`` (populated by
``deps.require_api_key_or_session``, which every router carrying this route already depends
on) — nowhere inside :mod:`nbastats.widgets` needs to know a user exists at all.

A **401** on this route (no key, no session, and the service requires one) is a real
request-level failure, never a per-widget ``status: "error"``: the "one bad tile" rule governs
*data* a resolver could not produce, not *who is asking*, and the correct client action for an
expired session is to route to sign-in, not to retry twenty-four grey tiles. See
``contracts/CONTRACT.md`` §3 for the same note written for API consumers.
"""
from __future__ import annotations

import logging
from datetime import date as date_type
from typing import Optional

from fastapi import APIRouter, Query, Request

from .. import catalog
from ..db import utcnow
from ..widgets import MAX_WIDGETS_PER_REQUEST, ResolveContext, WidgetError, resolver_for, ttl_for
from . import errors
from .deps import SessionDep
from .schemas import (
    DashboardResolveRequest,
    DashboardResolveResponse,
    ResolveContext as ResolveContextModel,
    ResolvedContext,
    ResolveResult,
    ResolveWidgetRequest,
)

__all__ = ["router", "resolve_one", "MAX_WIDGETS_PER_REQUEST"]

logger = logging.getLogger("nbastats.api")

router = APIRouter(tags=["dashboard"])


@router.post(
    "/dashboard/resolve",
    response_model=DashboardResolveResponse,
    summary="Resolve a whole dashboard in one round trip",
)
def resolve_dashboard(
    session: SessionDep, request: Request, body: DashboardResolveRequest
) -> DashboardResolveResponse:
    """Resolve up to 24 widgets independently and answer with one envelope."""
    if len(body.widgets) > MAX_WIDGETS_PER_REQUEST:
        raise errors.too_many_widgets(len(body.widgets), MAX_WIDGETS_PER_REQUEST)

    request_id = errors.request_id_of(request)
    ctx = ResolveContext.from_request(session, body.context, request_id=request_id)

    user = getattr(request.state, "user", None)
    if user is not None:
        if ctx.favorite_player_id is None:
            ctx.favorite_player_id = user.favorite_player_id
        if ctx.favorite_team_id is None:
            ctx.favorite_team_id = user.favorite_team_id

    results = [
        resolve_one(widget, ctx, known_sync_version=body.known_sync_version)
        for widget in body.widgets
    ]

    return DashboardResolveResponse(
        sync_version=ctx.sync_version,
        data_through=ctx.data_through,
        generated_at=utcnow(),
        resolved_context=_resolved_context(ctx),
        results=results,
    )


@router.get(
    "/dashboard/resolve-preset/{preset_key}",
    response_model=DashboardResolveResponse,
    summary="Resolve one of the stored preset dashboards",
)
def resolve_preset(
    session: SessionDep,
    request: Request,
    preset_key: str,
    favorite_player_id: Optional[int] = Query(None, alias="favoritePlayerId"),
    favorite_team_id: Optional[int] = Query(None, alias="favoriteTeamId"),
    time_zone: Optional[str] = Query(None, alias="timeZone"),
    as_of: Optional[date_type] = Query(None, alias="asOf"),
    season: Optional[str] = Query(None),
    known_sync_version: Optional[int] = Query(None, alias="knownSyncVersion"),
) -> DashboardResolveResponse:
    """Resolve a preset from ``contracts/presets.json`` with the caller's own context.

    The same work ``POST /v1/dashboard/resolve`` does, for a layout the client has not copied
    yet — this is what the preset gallery previews with, so ``$favorite_player`` resolves
    against the reader rather than showing a stranger.
    """
    try:
        layout = catalog.preset(preset_key)
    except catalog.UnknownPresetError as exc:
        raise errors.ApiError(
            "not_found", f"No preset with key {preset_key!r}.", field="presetKey"
        ) from exc

    body = DashboardResolveRequest(
        layout_id=layout.get("id"),
        context=ResolveContextModel(
            favorite_player_id=favorite_player_id,
            favorite_team_id=favorite_team_id,
            time_zone=time_zone,
            as_of=as_of,
            season=season,
        ),
        known_sync_version=known_sync_version,
        widgets=[
            ResolveWidgetRequest(
                id=str(widget.get("id") or f"{preset_key}.{index}"),
                kind=str(widget.get("kind")),
                size=str(widget.get("size") or "medium"),
                title=widget.get("title"),
                config=dict(widget.get("config") or {}),
            )
            for index, widget in enumerate(layout.get("widgets") or [])
        ],
    )
    return resolve_dashboard(session, request, body)


# --------------------------------------------------------------------------- one widget


def resolve_one(
    widget: ResolveWidgetRequest,
    ctx: ResolveContext,
    *,
    known_sync_version: int | None = None,
) -> ResolveResult:
    """Resolve one widget, converting every possible failure into a result.

    Nothing raised inside a resolver escapes this function: a :class:`WidgetError` carries its
    own contract code, an :class:`~nbastats.api.errors.ApiError` is honoured as-is, and
    anything else is logged against the request id and served as ``internal_error``. That is
    the difference between a dashboard with one grey tile and a dashboard with none.
    """
    kind = widget.kind
    ttl = ttl_for(kind)

    if kind not in catalog.widget_kinds():
        return _error(
            widget,
            ttl,
            WidgetError(
                "invalid_config",
                f"{kind!r} is not a widget kind this server knows about.",
                field="kind",
            ),
            ctx.request_id,
        )

    cleaned, config_errors = catalog.validate_widget_config(kind, widget.config)
    if config_errors:
        first = config_errors[0]
        return _error(
            widget,
            ttl,
            WidgetError(
                "invalid_config",
                "; ".join(str(problem) for problem in config_errors),
                field=first.field,
            ),
            ctx.request_id,
        )

    if (
        known_sync_version is not None
        and known_sync_version == ctx.sync_version
        and kind not in _CLOCK_DEPENDENT_KINDS
    ):
        # Nothing has been ingested since the client's copy was built, so nothing this
        # widget could show has moved. §3: the client keeps what it has.
        return ResolveResult(
            widget_id=widget.id,
            kind=kind,
            status="unchanged",
            payload=None,
            generated_at=utcnow(),
            ttl_seconds=ttl,
            availability=None,
            notes=[],
        )

    try:
        payload, availability, notes = resolver_for(kind)(cleaned, ctx)
    except WidgetError as exc:
        return _error(widget, ttl, exc, ctx.request_id)
    except errors.ApiError as exc:
        return _error(
            widget,
            ttl,
            WidgetError(
                exc.code, exc.message, recoverable=exc.recoverable, field=exc.field
            ),
            ctx.request_id,
        )
    except Exception:  # noqa: BLE001 - one tile must never take the dashboard down
        logger.exception(
            "request %s: widget %s (%s) failed to resolve", ctx.request_id, widget.id, kind
        )
        return _error(
            widget,
            ttl,
            WidgetError(
                "internal_error",
                "This widget could not be built. Quote the request id when reporting it.",
            ),
            ctx.request_id,
        )

    return ResolveResult(
        widget_id=widget.id,
        kind=kind,
        status="partial" if notes else "ok",
        payload=payload,
        generated_at=utcnow(),
        ttl_seconds=ttl,
        availability=availability,
        notes=notes,
    )


#: Kinds whose payload moves without an ingest, so "the sync version has not changed" is not
#: the same claim as "this widget's data has not changed".
#:
#: ``contracts/CONTRACT.md`` §8 increments ``sync_version`` once per *finalized* game, and §3
#: only licenses ``"unchanged"`` for a widget "whose data has not changed". A scoreboard's
#: scores move all through a live game and no game has finalized; ``date: "latest"`` re-reads
#: the wall clock. Answering ``unchanged`` for these would freeze a live tile until the final
#: buzzer, so they are always resolved.
_CLOCK_DEPENDENT_KINDS = frozenset({"scoreboard", "daily_movers"})


def _error(
    widget: ResolveWidgetRequest, ttl: int, failure: WidgetError, request_id: str | None
) -> ResolveResult:
    """One failed widget, as the contract's per-widget error result."""
    return ResolveResult(
        widget_id=widget.id,
        kind=widget.kind,
        status="error",
        payload=None,
        error=failure.to_error_body(request_id),
        generated_at=utcnow(),
        ttl_seconds=ttl,
        availability=None,
        notes=[],
    )


def _resolved_context(ctx: ResolveContext) -> ResolvedContext:
    """What the server actually resolved the caller's context to.

    The favourites echoed back are the subjects the widgets *used*: with no favourite in the
    request these are the featured player and team the tokens fell back to, which is exactly
    what a client needs to caption "showing the league leader" or to offer a pin.
    """
    return ResolvedContext(
        favorite_player_id=ctx.used_player_id
        if ctx.used_player_id is not None
        else ctx.favorite_player_id,
        favorite_team_id=ctx.used_team_id
        if ctx.used_team_id is not None
        else ctx.favorite_team_id,
        season=ctx.season,
    )
