"""The shared machinery every widget resolver stands on.

A dashboard resolve is one HTTP request that fans out into up to 24 independent widget
resolutions. Three things have to be true for that to work, and all three live here:

1. **One request, one set of queries.** :class:`ResolveContext` carries a memo cache, so ten
   tiles that all want "the 2025-26 league TS% distribution" cause one query, not ten. Every
   helper in :mod:`nbastats.widgets.queries` goes through :meth:`ResolveContext.memo`.

2. **A failure is a value, not a crash.** :class:`WidgetError` carries one row of the
   contract's error table (``contracts/CONTRACT.md`` §7) and is what a resolver raises when a
   config cannot be honoured. The route turns it into ``status: "error"`` on that one result;
   the other 23 tiles still render.

3. **Era honesty travels with the number.** :func:`availability_note` turns the catalog's era
   rules into the sentence the client shows under a dash, so no resolver invents its own
   wording for "that stat did not exist yet" — and no resolver is tempted to serve a zero.

Subject tokens (``contracts/presets.json#/subjectTokens``) are resolved here too, with the
documented fallbacks::

    $favorite_player -> context.favoritePlayerId, else $featured_player
    $featured_player -> the season's PIE leader
    $favorite_team   -> context.favoriteTeamId,   else $featured_team
    $featured_team   -> the season's net-rating leader
    $league_leader   -> the season's scoring leader

A token is always resolved against the dashboard's own season (``ctx.season``), never against
an individual widget's season: "the featured player" is a property of the dashboard, and a
career-spanning tile asking about 1985-86 must not change who the dashboard is about.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Iterable, Mapping, Optional, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import catalog
from ..config import Settings, get_settings
from ..db import read_sync_state
from ..models import Game, Player, Team
from ..api import errors
from ..api.schemas import ErrorBody, ResolveContext as ResolveContextModel
from ..api.serializers import combined_availability

__all__ = [
    "SUBJECT_TOKENS",
    "FAVORITE_TOKENS",
    "FEATURED_TOKENS",
    "PER_MODE_LABELS",
    "WidgetError",
    "ResolveContext",
    "resolve_season",
    "resolve_date",
    "resolve_subject_token",
    "resolve_subject_list",
    "availability_note",
    "attach_availability",
    "combined_row_availability",
    "per_mode_label",
    "context_label",
    "stat_line",
]

T = TypeVar("T")

#: Every ``$``-token ``contracts/presets.json`` defines, and the subject type each names.
SUBJECT_TOKENS: dict[str, str] = {
    "$favorite_player": "player",
    "$featured_player": "player",
    "$league_leader": "player",
    "$favorite_team": "team",
    "$featured_team": "team",
}

#: The two tokens that read the caller's context before falling back to a featured subject.
FAVORITE_TOKENS: dict[str, str] = {
    "$favorite_player": "$featured_player",
    "$favorite_team": "$featured_team",
}

#: Metric each featured token ranks by, and the direction it ranks in.
FEATURED_TOKENS: dict[str, tuple[str, str]] = {
    "$featured_player": ("pie", "player"),
    "$league_leader": ("pts", "player"),
    "$featured_team": ("net_rtg", "team"),
}

#: How a ``perMode`` reads in a tile's caption.
PER_MODE_LABELS: dict[str, str] = {
    "PerGame": "Per Game",
    "Totals": "Totals",
    "Per36": "Per 36",
    "Per100": "Per 100 Poss",
}


class WidgetError(Exception):
    """One widget's failure, as one row of ``contracts/CONTRACT.md`` §7.

    Raising this from a resolver produces ``"status": "error"`` on that result and nothing
    else — the HTTP status of the resolve stays ``200`` and every other tile still renders.
    ``code`` must be a key of :data:`nbastats.api.errors.ERROR_STATUS`; ``recoverable``
    defaults to that code's own answer, and ``field`` names the offending config key so the
    client can open the configuration sheet on the right row.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        recoverable: bool | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.recoverable = (
            recoverable
            if recoverable is not None
            else errors.RECOVERABLE_BY_DEFAULT.get(code, True)
        )
        self.field = field

    def to_error_body(self, request_id: str | None = None) -> ErrorBody:
        """The ``error`` object this failure serialises as inside a resolve result."""
        return ErrorBody(
            code=self.code,
            message=self.message,
            recoverable=self.recoverable,
            field=self.field,
            request_id=request_id,
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"WidgetError({self.code!r}, {self.message!r}, field={self.field!r})"


def invalid_config(message: str, field: str | None = None) -> WidgetError:
    """The failure a bad or unresolvable config value produces."""
    return WidgetError("invalid_config", message, field=field)


# --------------------------------------------------------------------------- context


@dataclass
class ResolveContext:
    """Everything a resolver may read, plus the per-request memo cache.

    One instance is built per ``POST /v1/dashboard/resolve`` (and per ``GET /v1/leaders``)
    and handed to every resolver in the request. It is deliberately a plain dataclass rather
    than a pydantic model: it holds a live :class:`~sqlalchemy.orm.Session`, and it is never
    serialised.

    ``season`` is the dashboard's season — ``"latest"`` already resolved to the newest season
    the store actually holds. ``sync_version`` and ``data_through`` are read once, so every
    result in a response quotes the same freshness cursor even if an ingest lands mid-request.
    """

    session: Session
    settings: Settings
    season: str
    sync_version: int = 0
    data_through: Optional[date] = None
    favorite_player_id: Optional[int] = None
    favorite_team_id: Optional[int] = None
    time_zone: Optional[str] = None
    as_of: Optional[date] = None
    request_id: Optional[str] = None

    #: Subjects the resolvers actually used, echoed back as ``resolvedContext``.
    used_player_id: Optional[int] = None
    used_team_id: Optional[int] = None

    _memo: dict[Any, Any] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------ construction

    @classmethod
    def from_request(
        cls,
        session: Session,
        context: ResolveContextModel | None = None,
        *,
        request_id: str | None = None,
        settings: Settings | None = None,
    ) -> "ResolveContext":
        """Build the context for one resolve from the request body's ``context`` block."""
        resolved_settings = settings or get_settings()
        state = read_sync_state(session)
        supplied = context or ResolveContextModel()
        season = _default_season(session, supplied.season, resolved_settings)
        return cls(
            session=session,
            settings=resolved_settings,
            season=season,
            sync_version=int(state.sync_version or 0),
            data_through=state.data_through,
            favorite_player_id=supplied.favorite_player_id,
            favorite_team_id=supplied.favorite_team_id,
            time_zone=supplied.time_zone,
            as_of=supplied.as_of,
            request_id=request_id,
        )

    # ------------------------------------------------------------------ memoisation

    def memo(self, key: Any, factory: Callable[[], T]) -> T:
        """Return ``factory()``, computed at most once per request for this ``key``.

        This is what keeps a 24-widget dashboard from issuing 24 copies of the same league
        distribution query. ``None`` is cached like any other value, so a lookup that
        legitimately finds nothing is not retried by every tile after the first.
        """
        if key in self._memo:
            return self._memo[key]
        value = factory()
        self._memo[key] = value
        return value

    def cached(self, key: Any) -> Any:
        """The memoised value for ``key``, or ``None`` when nothing is cached yet."""
        return self._memo.get(key)

    # ------------------------------------------------------------------ subjects

    def note_player(self, player_id: int | None) -> int | None:
        """Record the first player a widget resolved, for ``resolvedContext``."""
        if player_id is not None and self.used_player_id is None:
            self.used_player_id = int(player_id)
        return player_id

    def note_team(self, team_id: int | None) -> int | None:
        """Record the first team a widget resolved, for ``resolvedContext``."""
        if team_id is not None and self.used_team_id is None:
            self.used_team_id = int(team_id)
        return team_id


def _default_season(session: Session, requested: str | None, settings: Settings) -> str:
    """The season a dashboard defaults to: the newest one loaded, else the configured one."""
    if requested and catalog.is_season_string(requested):
        return requested
    seasons = session.execute(select(Game.season).distinct()).scalars().all()
    known = sorted({s for s in seasons if s}, key=catalog.season_sort_key)
    return known[-1] if known else settings.current_season


# --------------------------------------------------------------------------- season / date


def resolve_season(ctx: ResolveContext, value: Any, *, field: str = "season") -> str:
    """Resolve a widget's ``season`` config value to a concrete season string.

    ``None`` and ``"latest"`` become the dashboard's season. A real season string is passed
    through **even when the store holds no games for it** — a 1985-86 tile is not an error,
    it is a tile full of honest em dashes (``contracts/CONTRACT.md`` §6). Anything that is
    not a season is :class:`WidgetError` ``invalid_config``.
    """
    if value is None:
        return ctx.season
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate or candidate.lower() == "latest":
            return ctx.season
        if catalog.is_season_string(candidate):
            return candidate
    raise invalid_config(
        f"{value!r} is not a season such as '2025-26' or 'latest'.", field
    )


def resolve_date(ctx: ResolveContext, value: Any, *, field: str = "date") -> Optional[date]:
    """Resolve a widget's ``date`` config value to a calendar day.

    ``"latest"`` (and ``None``) mean *the most recent day with at least one final game* —
    the same rule ``GET /v1/games?date=latest`` follows, so "last night" never points at an
    evening that has not been played. ``None`` comes back when the store holds no final game
    at all; the caller decides what an empty slate should say.
    """
    if value is None or (isinstance(value, str) and value.strip().lower() in ("", "latest")):
        return latest_final_date(ctx)
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError as exc:
            raise invalid_config(
                f"{value!r} is not an ISO date such as '2026-01-02'.", field
            ) from exc
    raise invalid_config(f"{value!r} is not an ISO date such as '2026-01-02'.", field)


def latest_final_date(ctx: ResolveContext) -> Optional[date]:
    """The most recent scheduling day with at least one final game (memoised)."""

    def _load() -> Optional[date]:
        from sqlalchemy import func

        return ctx.session.execute(
            select(func.max(Game.game_date)).where(Game.status == "final")
        ).scalar_one_or_none()

    return ctx.memo(("latest_final_date",), _load)


# --------------------------------------------------------------------------- subjects


def resolve_subject_token(
    token_or_id: Any,
    subject_type: str,
    ctx: ResolveContext,
    *,
    field: str = "subjectId",
) -> int:
    """Resolve a config subject — an id or a ``$`` token — to a concrete id.

    Integer ids are checked against the store, so a stale favourite from a device that has
    been restored from another account degrades to the featured subject instead of producing
    an empty tile. The token fallbacks are the ones ``contracts/presets.json`` documents::

        $favorite_player -> favoritePlayerId -> $featured_player (season PIE leader)
        $favorite_team   -> favoriteTeamId   -> $featured_team   (season net-rating leader)
        $league_leader   -> the season's scoring leader

    Anything that cannot be resolved — an unknown token, a token for the wrong subject type,
    or a league with no qualified subject to feature — raises :class:`WidgetError` with
    ``invalid_config``. It never crashes, and it never silently picks "some player".
    """
    wanted = "team" if subject_type == "team" else "player"

    if token_or_id is None:
        # The catalog's own default for a subject field is ``null`` — a widget the reader has
        # just dropped onto the grid has not been pointed at anybody yet. Treat that as the
        # favourite token, which then falls back to the featured subject, so a fresh tile
        # renders the league leader instead of an error.
        token_or_id = "$favorite_team" if wanted == "team" else "$favorite_player"

    if isinstance(token_or_id, bool):
        raise invalid_config(f"{token_or_id!r} is not a {wanted} id.", field)
    if isinstance(token_or_id, int):
        return _checked_id(int(token_or_id), wanted, ctx, field)
    if isinstance(token_or_id, float) and float(token_or_id).is_integer():
        return _checked_id(int(token_or_id), wanted, ctx, field)

    if not isinstance(token_or_id, str) or not token_or_id.startswith("$"):
        allowed = sorted(t for t, kind in SUBJECT_TOKENS.items() if kind == wanted)
        raise invalid_config(
            f"{token_or_id!r} is not a {wanted} id or one of {allowed}.", field
        )

    token = token_or_id
    if token not in SUBJECT_TOKENS:
        raise invalid_config(f"{token!r} is not a known subject token.", field)
    if SUBJECT_TOKENS[token] != wanted:
        raise invalid_config(
            f"{token!r} names a {SUBJECT_TOKENS[token]}, but this field wants a {wanted}.",
            field,
        )

    if token in FAVORITE_TOKENS:
        favourite = ctx.favorite_player_id if wanted == "player" else ctx.favorite_team_id
        if favourite is not None and _subject_exists(int(favourite), wanted, ctx):
            return int(favourite)
        token = FAVORITE_TOKENS[token]

    featured = _featured_subject(token, ctx)
    if featured is None:
        raise invalid_config(
            f"{token_or_id} could not be resolved: the {ctx.season} season has no "
            f"{wanted} to feature.",
            field,
        )
    return featured


def resolve_subject_list(
    values: Any,
    subject_type: str,
    ctx: ResolveContext,
    *,
    field: str = "subjectIds",
    maximum: int | None = None,
) -> list[int]:
    """Resolve a list of ids/tokens, dropping duplicates but keeping the caller's order."""
    if values is None:
        # Same rule as a single subject field: an unconfigured list means "the reader's
        # subject", which falls back to the league's featured one.
        return [resolve_subject_token(None, subject_type, ctx, field=field)]
    if not isinstance(values, (list, tuple)):
        raise invalid_config(f"expected a list of subjects, got {values!r}.", field)
    out: list[int] = []
    for item in values:
        resolved = resolve_subject_token(item, subject_type, ctx, field=field)
        if resolved not in out:
            out.append(resolved)
        if maximum is not None and len(out) >= maximum:
            break
    if not out:
        return [resolve_subject_token(None, subject_type, ctx, field=field)]
    return out


def _checked_id(subject_id: int, wanted: str, ctx: ResolveContext, field: str) -> int:
    if not _subject_exists(subject_id, wanted, ctx):
        raise WidgetError(
            "player_not_found" if wanted == "player" else "team_not_found",
            f"No {wanted} with id {subject_id}.",
            field=field,
        )
    return subject_id


def _subject_exists(subject_id: int, wanted: str, ctx: ResolveContext) -> bool:
    model = Player if wanted == "player" else Team
    key = ("subject_exists", wanted, subject_id)
    return ctx.memo(key, lambda: ctx.session.get(model, subject_id) is not None)


def _featured_subject(token: str, ctx: ResolveContext) -> Optional[int]:
    """The league-wide subject a featured token names, for the dashboard's season."""
    spec = FEATURED_TOKENS.get(token)
    if spec is None:
        return None
    metric_key, subject_type = spec

    def _load() -> Optional[int]:
        from . import queries

        return queries.featured_subject_id(ctx, metric_key, subject_type)

    return ctx.memo(("featured", token, ctx.season), _load)


# --------------------------------------------------------------------------- era rules


def availability_note(
    metric_key: str, season: str | None, granularity: str = "season"
) -> tuple[str, Optional[str]]:
    """``(availability, note)`` for one metric in one season.

    The note is the sentence the client shows when the reader taps an em dash or an "est."
    badge. It names the season the data actually begins in, because "not available" without
    a date reads as a bug rather than as history. ``None`` comes back for a measured value —
    there is nothing to explain.
    """
    availability = catalog.metric_availability(metric_key, season, granularity)
    if availability == "full":
        return availability, None

    descriptor = catalog.metric(metric_key)
    name = descriptor.get("name", metric_key)
    spec = descriptor["availability"]
    per_game = str(granularity).lower() in ("game", "per_game", "pergame")
    where = str(season) if season else "this span"

    if availability == "unavailable":
        if per_game and spec.get("seasonLevelOnly"):
            return availability, (
                f"{name} is a season rating with no single-game meaning, so there is no "
                f"per-game value for {where}."
            )
        first = spec["perGameFrom"] if per_game else spec["seasonFrom"]
        scope = "per game" if per_game else "at season level"
        return availability, (
            f"{name} {scope} begins in {first}; the {where} season predates it."
        )

    if availability == "estimated":
        boundary = spec.get("estimatedBefore") or "1996-97"
        return availability, (
            f"{name} for {where} is derived from box-score formulas rather than possession "
            f"data, which the league only recorded from {boundary}."
        )

    return availability, (
        f"{name} is only partly recorded across {where}; some seasons predate it."
    )


def attach_availability(
    notes: list[str], metric_keys: Iterable[str], season: str | None, granularity: str = "season"
) -> str:
    """Fold several metrics' era rules into one availability, appending each explanation.

    ``notes`` is mutated in place — resolvers collect their notes in one list and hand it
    back with the payload, which is what turns a result's status into ``"partial"``.
    """
    seen: list[str] = []
    for key in metric_keys:
        availability, note = availability_note(key, season, granularity)
        seen.append(availability)
        if note and note not in notes:
            notes.append(note)
    return catalog.combine_availability(seen) if seen else "full"


def combined_row_availability(
    metric_keys: Iterable[str], season: str | None, granularity: str = "season"
) -> str:
    """The availability one table row advertises, with no note collected.

    A game log has one of these per row and one note per *table*; repeating the same
    explanation eighty-two times would drown the one that matters.
    """
    return combined_availability(list(metric_keys), season, granularity)


# --------------------------------------------------------------------------- labels


def per_mode_label(per_mode: str | None) -> str:
    """``"PerGame"`` -> ``"Per Game"``, for a tile's caption."""
    return PER_MODE_LABELS.get(per_mode or "PerGame", str(per_mode or "Per Game"))


def context_label(season: str, season_type: str | None, per_mode: str | None = None) -> str:
    """The caption line a tile shows: ``"2025-26 · Regular Season · Per Game"``."""
    parts = [season]
    if season_type:
        parts.append(season_type)
    if per_mode:
        parts.append(per_mode_label(per_mode))
    return " · ".join(parts)


#: Box-score components a one-line summary mentions, and the floor each has to clear.
#: PTS is always shown; the rest earn their place, which is why ``"46 PTS · 9 AST"`` in the
#: contract has no rebound term.
_LINE_PARTS: tuple[tuple[str, str, float], ...] = (
    ("pts", "PTS", -1.0),
    ("reb", "REB", 5.0),
    ("ast", "AST", 5.0),
    ("stl", "STL", 3.0),
    ("blk", "BLK", 3.0),
)


def stat_line(values: Mapping[str, Optional[float]]) -> Optional[str]:
    """``"32 PTS · 8 REB · 11 AST"`` from a game's box-score values.

    Only components the era actually recorded appear, and only when they are worth
    mentioning; a line with no points at all comes back ``None`` rather than as ``"0 PTS"``.
    """
    parts: list[str] = []
    for key, label, floor in _LINE_PARTS:
        value = values.get(key)
        if value is None:
            continue
        number = float(value)
        if number < floor:
            continue
        parts.append(f"{int(round(number))} {label}")
    return " · ".join(parts) if parts else None

