"""Pydantic v2 models for every object in ``contracts/CONTRACT.md`` §2 and §3.

Two rules run through this module, and both are decoding requirements for the Swift client
rather than stylistic choices:

1. **JSON keys are lowerCamelCase.** Fields are declared ``snake_case`` and aliased through
   :func:`pydantic.alias_generators.to_camel`; responses are serialised by alias (FastAPI's
   default). ``populate_by_name`` is on, so server-side construction uses the Python names.
2. **A nullable field is present and ``null``, never omitted.** Every optional field has an
   explicit ``None`` default and nothing here sets ``exclude_none``. ``ios/NBAStats/Core``
   decodes a missing key as a decoding failure for the shapes the contract marks non-null,
   so silence is worse than an explicit ``null``.

Timestamps are RFC-3339 UTC with a ``Z`` suffix (:data:`UtcTimestamp`); the database stores
naive UTC, so the serializer stamps the suffix rather than guessing a zone. Calendar dates
are plain ISO ``YYYY-MM-DD`` in US Eastern, the NBA's scheduling day.

The resolve envelope at the bottom (:class:`DashboardResolveRequest` and friends) is defined
here even though ``POST /v1/dashboard/resolve`` lives in ``routes_dashboard``: the request and
response shapes belong to the contract, not to one route module.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer
from pydantic.alias_generators import to_camel

__all__ = [
    "Availability",
    "GameStatus",
    "ResolveStatus",
    "SubjectType",
    "UtcTimestamp",
    "ContractModel",
    "PlayerRef",
    "TeamRef",
    "MetricValue",
    "MetricDomain",
    "MetricEraSpec",
    "MetricDescriptor",
    "GameRef",
    "DraftInfo",
    "PlayerBio",
    "SeasonRow",
    "PlayerDetail",
    "PlayerSearchResult",
    "PlayerSearchResponse",
    "GameLogRow",
    "GameLogResponse",
    "TeamRecord",
    "TeamRosterEntry",
    "TeamDetail",
    "TeamsResponse",
    "ScoreboardResponse",
    "BoxScorePlayer",
    "BoxScoreTeam",
    "BoxScoreResponse",
    "SeasonInfo",
    "Coverage",
    "MetaResponse",
    "SubjectTokenInfo",
    "PresetsResponse",
    "SyncResponse",
    "HealthResponse",
    "ErrorBody",
    "ErrorEnvelope",
    "ResolveContext",
    "ResolvedContext",
    "ResolveWidgetRequest",
    "DashboardResolveRequest",
    "ResolveResult",
    "DashboardResolveResponse",
]

#: ``contracts/CONTRACT.md`` §6 — how much the client may trust a number.
Availability = Literal["full", "estimated", "partial", "unavailable"]

GameStatus = Literal["scheduled", "live", "final"]

#: Per-widget outcome inside ``POST /v1/dashboard/resolve``.
ResolveStatus = Literal["ok", "unchanged", "partial", "error"]

SubjectType = Literal["player", "team"]

#: A metric map: ``{"pts": 32, "ts_pct": 0.641, "plus_minus": null}``. Keys are metric keys
#: from ``contracts/metrics.json`` and stay snake_case — they are data, not field names. The
#: int-or-float union keeps a counting stat an integer on the wire: the contract's examples
#: show ``"pts": 32``, not ``32.0``.
MetricMap = dict[str, Optional[Union[int, float]]]


def _rfc3339(value: datetime | None) -> str | None:
    """Render a timestamp the way the contract specifies: UTC, whole seconds, ``Z``."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(microsecond=0).isoformat() + "Z"


#: The NBA scheduling day, ``"2026-01-02"``. Aliased because a field *named* ``date`` would
#: otherwise shadow the type in its own class namespace when it carries a default.
CalendarDate = date

#: ``datetime`` that always serialises as ``"2026-01-03T07:12:44Z"``.
UtcTimestamp = Annotated[
    datetime, PlainSerializer(_rfc3339, return_type=str, when_used="always")
]


class ContractModel(BaseModel):
    """Base for every contract object: camelCase aliases, populate by field name."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        ser_json_inf_nan="null",
    )


# --------------------------------------------------------------------------- §2 shared


class PlayerRef(ContractModel):
    """A player, as every payload refers to one.

    ``teamId``/``teamAbbr`` come from the player's most recent season row, not from the
    ``players`` table, which holds no team: a career spans several.
    """

    player_id: int
    name: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    team_id: Optional[int] = None
    team_abbr: Optional[str] = None
    position: Optional[str] = None
    jersey: Optional[str] = None
    headshot_url: Optional[str] = None
    is_active: bool = True


class TeamRef(ContractModel):
    """A franchise. ``conference``/``division`` are null for defunct franchises."""

    team_id: int
    abbr: str
    name: str
    city: Optional[str] = None
    nickname: Optional[str] = None
    conference: Optional[str] = None
    division: Optional[str] = None


class MetricValue(ContractModel):
    """One number the client renders, with everything it needs to render it honestly.

    ``value`` is the raw number in the metric's native unit — percentages are fractions in
    ``[0, 1]``, never 0-100. ``displayValue`` is the server-formatted string; for a metric the
    subject's era never recorded it is an em dash and ``value`` is ``null``.
    """

    metric: str
    value: Optional[float] = None
    display_value: str
    rank: Optional[int] = None
    percentile: Optional[float] = None
    league_average: Optional[float] = None
    delta: Optional[float] = None
    is_estimated: bool = False
    availability: Availability = "full"


class MetricDomain(ContractModel):
    """Suggested axis bounds for a metric, when the catalog gives them."""

    min: float
    max: float


class MetricEraSpec(ContractModel):
    """``contracts/metrics.json#/metrics/*/availability`` — the era rules for one metric."""

    season_from: str
    per_game_from: str
    season_level_only: bool = False
    estimated_before: Optional[str] = None


class MetricDescriptor(ContractModel):
    """Exactly one entry of ``contracts/metrics.json#/metrics``."""

    key: str
    name: str
    short_name: str
    category: str
    format: str
    higher_is_better: bool = True
    scope: list[str] = Field(default_factory=list)
    availability: MetricEraSpec
    domain: Optional[MetricDomain] = None
    glossary: str = ""


class GameRef(ContractModel):
    """One game. Points and ``finalizedAt`` are null until it is played."""

    game_id: str
    date: CalendarDate
    season: Optional[str] = None
    season_type: Optional[str] = None
    home: TeamRef
    away: TeamRef
    home_pts: Optional[int] = None
    away_pts: Optional[int] = None
    status: GameStatus = "scheduled"
    period: Optional[int] = None
    clock: Optional[str] = None
    finalized_at: Optional[UtcTimestamp] = None


# --------------------------------------------------------------------------- §3 players


class DraftInfo(ContractModel):
    """Draft position. Undrafted players carry nulls, not zeroes."""

    year: Optional[int] = None
    round: Optional[int] = None
    pick: Optional[int] = None


class PlayerBio(ContractModel):
    """The biography block of ``GET /v1/players/{playerId}``."""

    height: Optional[str] = None
    weight: Optional[int] = None
    birthdate: Optional[CalendarDate] = None
    country: Optional[str] = None
    draft: Optional[DraftInfo] = None
    school: Optional[str] = None
    from_year: Optional[int] = None
    to_year: Optional[int] = None
    bbref_slug: Optional[str] = None


class SeasonRow(ContractModel):
    """One season of a player's career — or the career total, whose ``season`` is ``"Career"``."""

    season: str
    season_type: Optional[str] = None
    team_abbr: Optional[str] = None
    age: Optional[int] = None
    gp: Optional[int] = None
    values: MetricMap = Field(default_factory=dict)
    availability: Availability = "full"


class PlayerDetail(ContractModel):
    """``GET /v1/players/{playerId}``."""

    player: PlayerRef
    bio: Optional[PlayerBio] = None
    career_totals: Optional[SeasonRow] = None
    seasons: list[SeasonRow] = Field(default_factory=list)


class PlayerSearchResult(PlayerRef):
    """A search hit: a flattened :class:`PlayerRef` plus its ranking fields."""

    from_year: Optional[int] = None
    to_year: Optional[int] = None
    match_score: Optional[float] = None


class PlayerSearchResponse(ContractModel):
    """``GET /v1/players/search``."""

    query: str
    results: list[PlayerSearchResult] = Field(default_factory=list)
    next_cursor: Optional[str] = None


class GameLogRow(ContractModel):
    """One line of ``GET /v1/players/{playerId}/gamelog``.

    ``values`` holds exactly the requested metric keys; a key whose metric did not exist in
    that game's era is present with a ``null`` value.
    """

    game_id: str
    date: CalendarDate
    opponent_abbr: Optional[str] = None
    is_home: Optional[bool] = None
    result: Optional[str] = None
    score: Optional[str] = None
    started: Optional[bool] = None
    minutes: Optional[float] = None
    values: MetricMap = Field(default_factory=dict)
    availability: Availability = "full"


class GameLogResponse(ContractModel):
    """``GET /v1/players/{playerId}/gamelog`` — newest first, cursor paginated."""

    player: PlayerRef
    season: Optional[str] = None
    season_type: Optional[str] = None
    rows: list[GameLogRow] = Field(default_factory=list)
    next_cursor: Optional[str] = None


# --------------------------------------------------------------------------- §3 teams


class TeamRecord(ContractModel):
    """A win-loss record."""

    wins: Optional[int] = None
    losses: Optional[int] = None


class TeamRosterEntry(PlayerRef):
    """A roster line: a flattened :class:`PlayerRef` plus that player's season values."""

    values: MetricMap = Field(default_factory=dict)


class TeamDetail(ContractModel):
    """``GET /v1/teams/{teamId}``."""

    team: TeamRef
    season: Optional[str] = None
    season_type: Optional[str] = None
    record: Optional[TeamRecord] = None
    values: MetricMap = Field(default_factory=dict)
    roster: list[TeamRosterEntry] = Field(default_factory=list)


class TeamsResponse(ContractModel):
    """``GET /v1/teams``."""

    teams: list[TeamRef] = Field(default_factory=list)


# --------------------------------------------------------------------------- §3 games


class ScoreboardResponse(ContractModel):
    """``GET /v1/games``. ``date`` is null when the query was by season rather than by day."""

    date: Optional[CalendarDate] = None
    is_latest_completed: bool = False
    games: list[GameRef] = Field(default_factory=list)
    next_cursor: Optional[str] = None


class BoxScorePlayer(ContractModel):
    """One player's line in a box score."""

    player: PlayerRef
    started: Optional[bool] = None
    minutes: Optional[float] = None
    values: MetricMap = Field(default_factory=dict)
    availability: Availability = "full"


class BoxScoreTeam(ContractModel):
    """One team's side of a box score."""

    team: TeamRef
    values: MetricMap = Field(default_factory=dict)
    players: list[BoxScorePlayer] = Field(default_factory=list)


class BoxScoreResponse(ContractModel):
    """``GET /v1/games/{gameId}/box``."""

    game: GameRef
    teams: list[BoxScoreTeam] = Field(default_factory=list)


# --------------------------------------------------------------------------- §3 meta


class SeasonInfo(ContractModel):
    """One season the store actually holds, as ``/v1/meta`` advertises it."""

    season: str
    season_types: list[str] = Field(default_factory=list)
    is_current: bool = False
    has_advanced: bool = False
    game_count: int = 0


class Coverage(ContractModel):
    """First season of each class of data (``contracts/CONTRACT.md`` §6)."""

    season_from: str
    advanced_from: str
    shot_charts_from: str
    tracking_from: str
    hustle_from: str


class MetaResponse(ContractModel):
    """``GET /v1/meta`` — league state plus the three catalogs, verbatim.

    ``metrics`` and ``widgets`` are the whole contract documents, passed through untouched so
    a catalog change reaches clients without an App Store release.
    """

    sync_version: int = 0
    data_through: Optional[CalendarDate] = None
    current_season: str
    seasons: list[SeasonInfo] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    widgets: dict[str, Any] = Field(default_factory=dict)
    teams: list[TeamRef] = Field(default_factory=list)
    coverage: Coverage
    attribution: str


class SubjectTokenInfo(ContractModel):
    """One ``$``-prefixed subject token a preset may use in place of an id."""

    token: str
    summary: Optional[str] = None


class PresetsResponse(ContractModel):
    """``GET /v1/presets``."""

    schema_version: int = 1
    version: int = 1
    presets: list[dict[str, Any]] = Field(default_factory=list)
    subject_tokens: list[SubjectTokenInfo] = Field(default_factory=list)


class SyncResponse(ContractModel):
    """``GET /v1/sync`` and each ``event: sync`` frame of ``GET /v1/sync/stream``.

    ``previousVersion`` echoes the client's ``since`` and is null when it sent none.
    ``invalidate`` names widget **kinds** whose cached payloads must be dropped.
    """

    sync_version: int = 0
    previous_version: Optional[int] = None
    server_time: UtcTimestamp
    data_through: Optional[CalendarDate] = None
    has_changes: bool = False
    changed_dates: list[CalendarDate] = Field(default_factory=list)
    finalized_games: list[GameRef] = Field(default_factory=list)
    affected_player_ids: list[int] = Field(default_factory=list)
    invalidate: list[str] = Field(default_factory=list)
    next_poll_after_seconds: int = 900


class HealthResponse(ContractModel):
    """``GET /v1/health``. Served with ``503`` when ``databaseReady`` is false.

    ``authReady`` / ``authWarnings`` (WEB_DESIGN.md §4.5) are computed with no database read —
    they describe whether the web accounts feature is actually mounted and what, if anything,
    is misconfigured about it, following the route's existing "answer with a body even when
    degraded" pattern rather than raising. A stats-only deployment with no accounts feature at
    all reports ``authReady: false`` and an empty ``authWarnings``, which is simply true rather
    than an error.
    """

    status: str = "ok"
    version: str
    sync_version: int = 0
    data_through: Optional[CalendarDate] = None
    database_ready: bool = True
    seeded_demo_data: bool = False
    auth_ready: bool = False
    auth_warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- §7 errors


class ErrorBody(ContractModel):
    """The inner object of the error envelope."""

    code: str
    message: str
    recoverable: bool = False
    field: Optional[str] = None
    request_id: Optional[str] = None


class ErrorEnvelope(ContractModel):
    """Every non-2xx body: ``{"error": {...}}``."""

    error: ErrorBody


# --------------------------------------------------------------------------- §3 resolve


class ResolveContext(ContractModel):
    """Client context for a resolve: whose dashboard this is and when.

    Unknown keys are ignored rather than rejected, so an older service still serves a newer
    client.
    """

    favorite_player_id: Optional[int] = None
    favorite_team_id: Optional[int] = None
    time_zone: Optional[str] = None
    as_of: Optional[CalendarDate] = None
    season: Optional[str] = None


class ResolvedContext(ContractModel):
    """What the server actually resolved the context to, echoed back to the client.

    The keys here are exactly the three ``contracts/CONTRACT.md`` §3 defines for
    ``resolvedContext``; the client has no home for anything else.
    """

    favorite_player_id: Optional[int] = None
    favorite_team_id: Optional[int] = None
    season: Optional[str] = None


class ResolveWidgetRequest(ContractModel):
    """One widget to resolve. ``config`` is validated against ``contracts/widgets.json``."""

    id: str
    kind: str
    size: str = "medium"
    title: Optional[str] = None
    config: dict[str, Any] = Field(default_factory=dict)


class DashboardResolveRequest(ContractModel):
    """``POST /v1/dashboard/resolve``.

    The 24-widget cap is enforced by the route rather than by this model: exceeding it is
    ``400 too_many_widgets`` from the contract's error table, not a schema validation error.
    """

    layout_id: Optional[str] = None
    context: ResolveContext = Field(default_factory=ResolveContext)
    known_sync_version: Optional[int] = None
    widgets: list[ResolveWidgetRequest] = Field(default_factory=list)


class ResolveResult(ContractModel):
    """One widget's outcome. A failure here never changes the HTTP status."""

    widget_id: str
    kind: str
    status: ResolveStatus = "ok"
    payload: Optional[dict[str, Any]] = None
    error: Optional[ErrorBody] = None
    generated_at: Optional[UtcTimestamp] = None
    ttl_seconds: Optional[int] = None
    availability: Optional[Availability] = None
    notes: list[str] = Field(default_factory=list)


class DashboardResolveResponse(ContractModel):
    """``POST /v1/dashboard/resolve`` — one round trip, one dashboard."""

    sync_version: int = 0
    data_through: Optional[CalendarDate] = None
    generated_at: UtcTimestamp
    resolved_context: ResolvedContext = Field(default_factory=ResolvedContext)
    results: list[ResolveResult] = Field(default_factory=list)
