"""``GET /v1/leaders`` — the leaderboard widget, addressed by query string.

The contract says this route "returns the ``leaderboard`` widget payload (§4) plus
``nextCursor``", and it means it literally: this module maps query parameters onto a
``leaderboard`` config, hands it to the same
:func:`nbastats.widgets.leaderboard.resolve_leaderboard` the dashboard uses, and adds the
cursor. There is exactly one implementation of "who leads the league in TS%", so the screen
that drills into a tile cannot disagree with the tile it came from.

The one real difference is where a bad parameter goes. Inside a resolve, a bad config is that
widget's problem and the HTTP status stays 200; here the request *is* the widget, so an
unusable parameter is a ``400`` naming the field.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query, Request

from .. import catalog
from ..widgets import ResolveContext, WidgetError
from ..widgets.leaderboard import resolve_leaderboard
from . import errors
from .deps import SessionDep, decode_cursor, encode_cursor

__all__ = ["router", "DEFAULT_LIMIT", "MIN_LIMIT", "MAX_LIMIT"]

router = APIRouter(tags=["leaders"])

DEFAULT_LIMIT = 10

#: The widget catalog's own floor and ceiling on ``limit``. This route builds a ``leaderboard``
#: config out of the query string, so the catalog's bounds are the real bounds — declaring wider
#: ones and then silently clamping would answer a ``limit=200`` ask with 50 rows and no note.
#: ``contracts/CONTRACT.md`` states these per-endpoint limits alongside the route.
MIN_LIMIT = 3
MAX_LIMIT = 50


@router.get("/leaders", summary="League leaders in any metric")
def leaders(
    session: SessionDep,
    request: Request,
    metric: str = Query(..., description="A metric key from contracts/metrics.json."),
    subject_type: str = Query("player", alias="subjectType"),
    scope: str = Query("season", description="'season' or 'all_time'."),
    season: Optional[str] = Query(None),
    season_type: Optional[str] = Query(None, alias="seasonType"),
    per_mode: Optional[str] = Query(None, alias="perMode"),
    limit: Optional[int] = Query(None, ge=MIN_LIMIT, le=MAX_LIMIT),
    min_games: Optional[int] = Query(None, alias="minGames", ge=0),
    min_minutes_per_game: Optional[float] = Query(
        None, alias="minMinutesPerGame", ge=0.0, le=48.0
    ),
    positions: Optional[str] = Query(None, description="Comma-separated: G, F, C."),
    team_ids: Optional[str] = Query(None, alias="teamIds", description="Comma-separated ids."),
    secondary_metrics: Optional[str] = Query(
        None, alias="secondaryMetrics", description="Comma-separated metric keys."
    ),
    ascending: Optional[bool] = Query(None),
    cursor: Optional[str] = Query(None),
) -> dict[str, Any]:
    """One page of league leaders, as the ``leaderboard`` payload plus ``nextCursor``."""
    if not catalog.has_metric(metric):
        raise errors.bad_request(f"Unknown metric {metric!r}.", "metric")

    offset = _offset(cursor)
    config = _config(
        metric=metric,
        subject_type=subject_type,
        scope=scope,
        season=season,
        season_type=season_type,
        per_mode=per_mode,
        limit=limit,
        min_games=min_games,
        min_minutes_per_game=min_minutes_per_game,
        positions=positions,
        team_ids=team_ids,
        secondary_metrics=secondary_metrics,
        ascending=ascending,
    )

    cleaned, config_errors = catalog.validate_widget_config("leaderboard", config)
    if config_errors:
        # The contract reserves ``invalid_config`` (§7) for "a widget config failed
        # validation", and that is literally what just happened: this route builds a
        # ``leaderboard`` config out of the query string and runs the widget validator over
        # it. ``bad_request`` would lose the distinction the client branches on — a config
        # problem offers "edit the widget", a malformed query does not.
        first = config_errors[0]
        raise errors.invalid_config(
            "; ".join(str(problem) for problem in config_errors), first.field
        )

    ctx = ResolveContext.from_request(
        session, None, request_id=errors.request_id_of(request)
    )
    try:
        payload, _availability, _notes, has_more = resolve_leaderboard(
            cleaned, ctx, offset=offset
        )
    except WidgetError as exc:
        raise errors.ApiError(
            exc.code, exc.message, recoverable=exc.recoverable, field=exc.field
        ) from exc

    page_size = int(cleaned.get("limit") or DEFAULT_LIMIT)
    payload["nextCursor"] = (
        encode_cursor({"o": offset + page_size}) if has_more else None
    )
    return payload


def _offset(cursor: Optional[str]) -> int:
    """Decode the opaque page cursor. A cursor is server-minted, so a broken one is a 400."""
    decoded = decode_cursor(cursor)
    if decoded is None:
        return 0
    raw = decoded.get("o")
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        raise errors.bad_request("The cursor is not valid.", "cursor")
    return raw


def _config(
    *,
    metric: str,
    subject_type: str,
    scope: str,
    season: Optional[str],
    season_type: Optional[str],
    per_mode: Optional[str],
    limit: Optional[int],
    min_games: Optional[int],
    min_minutes_per_game: Optional[float],
    positions: Optional[str],
    team_ids: Optional[str],
    secondary_metrics: Optional[str],
    ascending: Optional[bool],
) -> dict[str, Any]:
    """Query parameters as a ``leaderboard`` widget config.

    Only the keys the caller actually supplied are set; everything else is left out so
    :func:`nbastats.catalog.validate_widget_config` fills it from the catalog default. That
    keeps one definition of "the default leaderboard" instead of two that drift.
    """
    config: dict[str, Any] = {
        "metric": metric,
        "subjectType": subject_type,
        "scope": scope,
        "season": season or "latest",
    }
    if season_type is not None:
        config["seasonType"] = season_type
    if per_mode is not None:
        config["perMode"] = per_mode
    if limit is not None:
        config["limit"] = int(limit)
    if min_games is not None:
        config["minGames"] = int(min_games)
    if min_minutes_per_game is not None:
        config["minMinutesPerGame"] = float(min_minutes_per_game)
    if positions is not None:
        config["positions"] = _split(positions, upper=True)
    if team_ids is not None:
        config["teamIds"] = _team_id_list(team_ids)
    if secondary_metrics is not None:
        config["secondaryMetrics"] = _split(secondary_metrics)
    if ascending is not None:
        config["ascending"] = bool(ascending)
    return config


def _split(raw: str, *, upper: bool = False) -> list[str]:
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    return [part.upper() for part in parts] if upper else parts


def _team_id_list(raw: str) -> list[int]:
    out: list[int] = []
    for part in _split(raw):
        try:
            out.append(int(part))
        except ValueError as exc:
            raise errors.bad_request(f"{part!r} is not a team id.", "teamIds") from exc
    return out
