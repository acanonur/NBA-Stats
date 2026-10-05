/**
 * The TypeScript mirror of `POST /v1/dashboard/resolve`'s envelope and the twenty widget
 * payloads it carries — `contracts/CONTRACT.md` §2-§4, transcribed field-for-field from three
 * sources that must not drift from one another, in this order of authority:
 *
 *   1. `backend/nbastats/api/schemas.py` — the Pydantic models the server actually serialises
 *      from (`ContractModel`'s `alias_generator=to_camel` is *why* every field below is
 *      camelCase despite every Python name being snake_case: this file's `MetricValue`,
 *      `GameRef`, `ResolveResult` and friends are typed straight from that module's classes).
 *   2. `contracts/CONTRACT.md` §4, which fixes each widget's payload shape and the fields
 *      `schemas.py` leaves as a loose `dict[str, Any]` (every widget resolver in
 *      `backend/nbastats/widgets/*.py` builds its payload as a plain dict — there is no
 *      Pydantic model per widget kind — so the contract doc and the golden fixtures are that
 *      shape's only other written-down source).
 *   3. `contracts/fixtures/widget_<kind>.json` — the golden fixture per kind, read field by
 *      field (including which fields a fixture happens to show as non-null) rather than
 *      guessed from the prose.
 *
 * THE ONE RULE THAT MATTERS MOST: every field the server can send `null` for is typed
 * `T | null` here, never a bare `T`. `null` is not an absent value to default away — per
 * CONTRACT.md §6 it means "this statistic does not exist for this era or subject", and
 * rendering it as `0` (or `""`, or `false`) is the one mistake this whole product exists to
 * avoid. `eslint-local/rules/no-nullable-number-zero-fallback.js` polices the numeric half of
 * that rule mechanically; this file is what gives that rule something to type-check against.
 * A `?? 0`/`|| 0` on any `number | null` field declared below is a lint error by construction.
 *
 * SCOPE. WEB_DESIGN.md §9 gives this file exactly two jobs: the resolve envelope, and the
 * twenty widget payloads (sixteen NBA tiles, then the four league tiles at the end of the file,
 * which the web does not draw yet: their registry entries are `component: null`, so
 * `WidgetContainer` shows its pending tile, and these types are the contract a later web widget
 * decodes against). It deliberately does not cover the other `/v1/*` REST responses
 * (`/v1/players/*`, `/v1/teams/*`, `/v1/games/*`, `/v1/meta`, `/v1/presets`, `/v1/sync`) —
 * those belong to `api/client.ts` (WP4) alongside the fetch calls that return them, at a point
 * when at least one more resolver's worth of shapes is nailed down against a live server
 * rather than against fixtures a static-analysis pass can misread. Nothing here should be
 * read as a claim that those endpoints are out of scope for the app — only for this file.
 *
 * WIRING. `WidgetPayloadMap` keys are exactly `generated/contracts.ts`'s `WidgetKind` union
 * (imported, not re-declared, so the two can never drift): `ResolveResult<K>["payload"]` is
 * `WidgetPayloadMap[K] | null`, so narrowing on `result.status === "ok"` and `result.kind`
 * narrows `payload` to the right shape without a cast anywhere in `registry.ts`'s consumers.
 */
import type { WidgetKind } from "../generated/contracts";

// --------------------------------------------------------------------------------------------
// §2 Shared objects (CONTRACT.md §2; backend/nbastats/api/schemas.py)
// --------------------------------------------------------------------------------------------

/** CONTRACT.md §6 — how much the client may trust a number. */
export type Availability = "full" | "estimated" | "partial" | "unavailable";

export type GameStatus = "scheduled" | "live" | "final";

/** Per-widget outcome inside `POST /v1/dashboard/resolve`. */
export type ResolveStatus = "ok" | "unchanged" | "partial" | "error";

export type SubjectType = "player" | "team";

/**
 * A metric map, e.g. `{"pts": 32, "ts_pct": 0.641, "plus_minus": null}`. Keys are metric keys
 * from `contracts/metrics.json` (snake_case — they are data, not field names, so they are
 * exempt from the lowerCamelCase wire-key rule). A key present with a `null` value means the
 * metric does not exist for that row's era; a key simply absent means it was not requested.
 */
export type MetricMap = Readonly<Record<string, number | null>>;

export interface PlayerRef {
  readonly playerId: number;
  readonly name: string;
  readonly firstName: string | null;
  readonly lastName: string | null;
  readonly teamId: number | null;
  readonly teamAbbr: string | null;
  readonly position: string | null;
  readonly jersey: string | null;
  readonly headshotUrl: string | null;
  readonly isActive: boolean;
}

export interface TeamRef {
  readonly teamId: number;
  readonly abbr: string;
  readonly name: string;
  readonly city: string | null;
  readonly nickname: string | null;
  readonly conference: string | null;
  readonly division: string | null;
}

/**
 * The atom every widget renders. `value` is the raw number in the metric's native unit —
 * percentages are fractions in `[0, 1]`, never 0-100 — and is `null` exactly when the metric
 * does not exist for the subject's era, in which case `availability` is `"unavailable"` and
 * `displayValue` is the em dash `"—"`. `displayValue` itself is never null: the server
 * always has *something* to show, even when it is that dash.
 */
export interface MetricValue {
  readonly metric: string;
  readonly value: number | null;
  readonly displayValue: string;
  readonly rank: number | null;
  readonly percentile: number | null;
  readonly leagueAverage: number | null;
  readonly delta: number | null;
  readonly isEstimated: boolean;
  readonly availability: Availability;
}

export interface MetricDomain {
  readonly min: number;
  readonly max: number;
}

/** `contracts/metrics.json#/metrics/*\/availability` — the era rules for one metric. */
export interface MetricEraSpec {
  readonly seasonFrom: string;
  readonly perGameFrom: string;
  readonly seasonLevelOnly: boolean;
  readonly estimatedBefore: string | null;
}

/** Exactly one entry of `contracts/metrics.json#/metrics` — also `generated/contracts.ts`'s
 * `METRICS_DOCUMENT.metrics[number]`, typed here for use as a widget payload field
 * (`leaderboard.metric`, `game_log.columns[number]`, `career_arc.metric`, ...). */
export interface MetricDescriptor {
  readonly key: string;
  readonly name: string;
  readonly shortName: string;
  readonly category: string;
  readonly format: string;
  readonly higherIsBetter: boolean;
  readonly scope: readonly string[];
  readonly availability: MetricEraSpec;
  readonly domain: MetricDomain | null;
  readonly glossary: string;
}

/** One game. Score fields and `finalizedAt` are null until it is played. */
export interface GameRef {
  readonly gameId: string;
  readonly date: string;
  readonly season: string | null;
  readonly seasonType: string | null;
  readonly home: TeamRef;
  readonly away: TeamRef;
  readonly homePts: number | null;
  readonly awayPts: number | null;
  readonly status: GameStatus;
  readonly period: number | null;
  readonly clock: string | null;
  readonly finalizedAt: string | null;
}

/** The `{"type", "player", "team"}` wrapper `stat_tile` and `shot_profile` carry — exactly
 * one of `player`/`team` is non-null, matching `type`. */
export interface SubjectRef {
  readonly type: SubjectType;
  readonly player: PlayerRef | null;
  readonly team: TeamRef | null;
}

// --------------------------------------------------------------------------------------------
// §3 The resolve envelope (CONTRACT.md §3 "POST /v1/dashboard/resolve"; schemas.py)
// --------------------------------------------------------------------------------------------

/** Client context for a resolve: whose dashboard this is and when. */
export interface ResolveContext {
  readonly favoritePlayerId?: number | null;
  readonly favoriteTeamId?: number | null;
  readonly timeZone?: string | null;
  readonly asOf?: string | null;
  readonly season?: string | null;
}

/** What the server actually resolved the context to, echoed back to the client. */
export interface ResolvedContext {
  readonly favoritePlayerId: number | null;
  readonly favoriteTeamId: number | null;
  readonly season: string | null;
}

/** One widget to resolve. `config` is validated server-side against `contracts/widgets.json`;
 * this file does not re-type each kind's config shape (that is `dashboard/config/*` and the
 * per-widget `payload.ts` files' job, in WP4/WP5 — see the module docstring's SCOPE note). */
export interface ResolveWidgetRequest {
  readonly id: string;
  readonly kind: WidgetKind;
  readonly size?: "small" | "medium" | "large";
  readonly title?: string | null;
  readonly config?: Readonly<Record<string, unknown>>;
}

/** `POST /v1/dashboard/resolve` request body. At most 24 widgets — see resolve.ts's chunking
 * (CONTRACT.md §3: exceeding it is a whole-request `400 too_many_widgets`, never a per-widget
 * error). */
export interface DashboardResolveRequest {
  readonly layoutId?: string | null;
  readonly context?: ResolveContext;
  readonly knownSyncVersion?: number | null;
  readonly widgets: readonly ResolveWidgetRequest[];
}

/** The inner object of every error envelope (CONTRACT.md §7) and of a per-widget resolve
 * failure alike — the same shape either way. */
export interface ErrorBody {
  readonly code: string;
  readonly message: string;
  readonly recoverable: boolean;
  readonly field: string | null;
  readonly requestId: string | null;
}

/** Every non-2xx REST response body. */
export interface ErrorEnvelope {
  readonly error: ErrorBody;
}

/**
 * Every payload shape a resolve result can carry, keyed by `WidgetKind`. `WidgetKind` (from
 * `generated/contracts.ts`) is the single source for which twenty kinds exist; this map's
 * keys are checked against it structurally, so a kind added there and forgotten here is a
 * compile error, not a silent `unknown`.
 */
export interface WidgetPayloadMap {
  readonly stat_tile: StatTilePayload;
  readonly player_snapshot: PlayerSnapshotPayload;
  readonly leaderboard: LeaderboardPayload;
  readonly game_log: GameLogPayload;
  readonly trend_chart: TrendChartPayload;
  readonly four_factors: FourFactorsPayload;
  readonly shot_profile: ShotProfilePayload;
  readonly comparison: ComparisonPayload;
  readonly scoreboard: ScoreboardPayload;
  readonly daily_movers: DailyMoversPayload;
  readonly team_efficiency: TeamEfficiencyPayload;
  readonly next_game_projection: NextGameProjectionPayload;
  readonly projection_board: ProjectionBoardPayload;
  readonly fantasy_draft_board: FantasyDraftBoardPayload;
  readonly fantasy_trade: FantasyTradePayload;
  readonly career_arc: CareerArcPayload;
  readonly team_matchup: TeamMatchupPayload;
  readonly defense_by_position: DefenseByPositionPayload;
  readonly availability_report: AvailabilityReportPayload;
  readonly slate_projections: SlateProjectionsPayload;
}

// A compile-time proof that WidgetPayloadMap's keys are exactly WidgetKind's members — if a
// kind is ever added to or removed from the generated union without a matching edit here, one
// of these two exports stops compiling. Both are exported (rather than left as unreferenced
// locals) purely so the checker always evaluates them under `noUnusedLocals`; neither is meant
// to be imported for its value.
/** Indexing every `WidgetKind` into `WidgetPayloadMap` here forces a kind the map has dropped
 * to fail to compile at this line, rather than surfacing as `undefined` wherever it is used. */
export type WidgetPayloadMapCoversEveryKind = { readonly [K in WidgetKind]: WidgetPayloadMap[K] };
/** `true` iff `WidgetPayloadMap` names no kind `WidgetKind` does not — see the
 * `satisfiesNoExtraWidgetPayloadMapKeys` assignment below, which is what actually enforces it. */
export type NoExtraWidgetPayloadMapKeys =
  Exclude<keyof WidgetPayloadMap, WidgetKind> extends never
    ? true
    : [
        "WidgetPayloadMap has a key WidgetKind does not:",
        Exclude<keyof WidgetPayloadMap, WidgetKind>,
      ];
export const satisfiesNoExtraWidgetPayloadMapKeys: NoExtraWidgetPayloadMapKeys = true;

/**
 * One widget's outcome inside a resolve response. `payload` is `WidgetPayloadMap[K] | null` —
 * non-null exactly when `status` is `"ok"` or `"partial"` (CONTRACT.md §3). `status ===
 * "unchanged"` and `status === "error"` both carry `payload: null`; see `resolve.ts` for what
 * each means for the client's cache.
 */
export interface ResolveResult<K extends WidgetKind = WidgetKind> {
  readonly widgetId: string;
  readonly kind: K;
  readonly status: ResolveStatus;
  readonly payload: WidgetPayloadMap[K] | null;
  readonly error: ErrorBody | null;
  readonly generatedAt: string | null;
  readonly ttlSeconds: number | null;
  readonly availability: Availability | null;
  readonly notes: readonly string[];
}

/** `POST /v1/dashboard/resolve` response — one round trip, one dashboard. */
export interface DashboardResolveResponse {
  readonly syncVersion: number;
  readonly dataThrough: string | null;
  readonly generatedAt: string;
  readonly resolvedContext: ResolvedContext;
  readonly results: readonly ResolveResult[];
}

// --------------------------------------------------------------------------------------------
// §4 Widget payloads (CONTRACT.md §4; contracts/fixtures/widget_<kind>.json)
// --------------------------------------------------------------------------------------------

export interface SparklinePoint {
  readonly x: string;
  /** `null` breaks the line at this point (design/svg/RunPolyline.tsx) rather than plotting a
   * zero — the metric did not exist, or the game did not, for this point on the axis. */
  readonly y: number | null;
  readonly gameId: string;
}

export interface StatTilePayload {
  readonly subject: SubjectRef;
  readonly context: string;
  readonly primary: MetricValue;
  readonly secondary: readonly MetricValue[];
  readonly sparkline: readonly SparklinePoint[];
  readonly sparklineMetric: string;
}

export interface PlayerSnapshotPayload {
  readonly player: PlayerRef;
  readonly season: string;
  readonly seasonType: string;
  readonly teamAbbr: string | null;
  readonly gp: number | null;
  readonly gs: number | null;
  readonly minutesPerGame: number | null;
  readonly metrics: readonly MetricValue[];
  readonly eraNote: string | null;
}

export interface LeaderboardRow {
  readonly rank: number;
  readonly player: PlayerRef | null;
  readonly team: TeamRef | null;
  readonly value: MetricValue;
  readonly secondary: readonly MetricValue[];
  /** For `scope: "all_time"`, the season this particular row's season-best is from — distinct
   * from the payload's own `season`, which is just the request's default. */
  readonly season: string;
}

export interface LeaderboardPayload {
  readonly metric: MetricDescriptor;
  readonly subjectType: SubjectType;
  readonly scope: "season" | "all_time";
  readonly season: string;
  readonly seasonType: string;
  readonly perMode: string;
  readonly qualifier: string | null;
  readonly secondaryMetrics: readonly MetricDescriptor[];
  readonly rows: readonly LeaderboardRow[];
}

export interface GameLogRow {
  readonly gameId: string;
  readonly date: string;
  readonly opponentAbbr: string | null;
  readonly isHome: boolean | null;
  readonly result: string | null;
  readonly score: string | null;
  readonly started: boolean | null;
  readonly values: MetricMap;
  readonly availability: Availability;
}

export interface GameLogPayload {
  readonly player: PlayerRef;
  readonly season: string;
  readonly seasonType: string;
  readonly columns: readonly MetricDescriptor[];
  /** The player's whole-season best for each requested metric — never recomputed from only
   * the visible page, per CONTRACT.md §4 `game_log`. A cell within `1e-9` of this value gets
   * the season-best treatment. */
  readonly seasonBests: MetricMap;
  readonly rows: readonly GameLogRow[];
}

export interface TrendChartPoint {
  readonly x: string;
  readonly y: number | null;
  readonly rolling: number | null;
  readonly gameId: string;
  readonly opponentAbbr: string | null;
}

export interface TrendChartSeries {
  readonly id: string;
  readonly label: string;
  readonly colorIndex: number;
  readonly points: readonly TrendChartPoint[];
}

export interface TrendChartPayload {
  readonly metric: MetricDescriptor;
  readonly rollingWindow: number;
  readonly leagueAverage: number | null;
  readonly yDomain: MetricDomain | null;
  readonly series: readonly TrendChartSeries[];
}

export interface FourFactorEntry {
  readonly key: string;
  readonly label: string;
  readonly weight: number;
  readonly value: number | null;
  readonly displayValue: string;
  readonly leagueAverage: number | null;
  readonly percentile: number | null;
  readonly rank: number | null;
}

export interface FourFactorsPayload {
  readonly team: TeamRef;
  readonly season: string;
  readonly seasonType: string;
  readonly offense: readonly FourFactorEntry[];
  readonly defense: readonly FourFactorEntry[];
}

/** Fixed order: `rim`, `paint_non_rim`, `mid_range`, `corner_three`, `above_break_three`. */
export type ShotZoneKey =
  | "rim"
  | "paint_non_rim"
  | "mid_range"
  | "corner_three"
  | "above_break_three";

export interface ShotZone {
  readonly zone: ShotZoneKey;
  readonly label: string;
  readonly fga: number | null;
  readonly fgPct: number | null;
  readonly shareOfFga: number | null;
  readonly pointsPerShot: number | null;
  readonly leagueFgPct: number | null;
  readonly leagueShareOfFga: number | null;
}

export interface ShotProfilePayload {
  readonly subject: SubjectRef;
  readonly season: string;
  readonly seasonType: string;
  /** Always all 5 zones, shaped-but-null pre-1996-97 (unlike `career_arc`, which omits a
   * valueless season instead — CONTRACT.md §7.7 calls this out explicitly as the opposite
   * convention on purpose). */
  readonly zones: readonly ShotZone[];
  readonly threePointRate: number | null;
  readonly freeThrowRate: number | null;
  readonly note: string | null;
}

export interface ComparisonSubject {
  readonly player: PlayerRef;
  readonly colorIndex: number;
  readonly values: readonly MetricValue[];
}

export interface ComparisonPayload {
  readonly season: string;
  readonly seasonType: string;
  readonly normalization: "percentile" | "raw";
  readonly style: "bars" | "radar" | "table";
  readonly metrics: readonly MetricDescriptor[];
  /** Players only — comparison has no team subject type (CONTRACT.md §7.7). */
  readonly subjects: readonly ComparisonSubject[];
}

export interface ScoreboardTopPerformer {
  readonly player: PlayerRef;
  readonly teamAbbr: string;
  readonly line: string;
  readonly value: MetricValue;
}

/** `GameRef`'s keys flattened alongside `topPerformers` (CONTRACT.md §4 `scoreboard`). */
export interface ScoreboardGame extends GameRef {
  readonly topPerformers: readonly ScoreboardTopPerformer[];
}

export interface ScoreboardPayload {
  readonly date: string;
  readonly isLatestCompleted: boolean;
  readonly allFinal: boolean;
  readonly games: readonly ScoreboardGame[];
}

export interface DailyMoverRow {
  readonly rank: number;
  readonly player: PlayerRef;
  readonly gameId: string;
  readonly opponentAbbr: string | null;
  readonly isHome: boolean | null;
  readonly result: string | null;
  readonly line: string;
  /** `value.leagueAverage` / `value.delta` are null by contract here — the comparison lives
   * in this row's own `seasonAverage` / `delta`, never in the nested `MetricValue`. */
  readonly value: MetricValue;
  readonly seasonAverage: number | null;
  readonly delta: number | null;
}

export interface DailyMoversPayload {
  readonly date: string;
  readonly metric: MetricDescriptor;
  readonly direction: "best" | "worst" | "surprise";
  readonly rows: readonly DailyMoverRow[];
}

export interface TeamEfficiencyRow {
  readonly rank: number;
  readonly team: TeamRef;
  readonly wins: number | null;
  readonly losses: number | null;
  readonly values: MetricMap;
  /** Always the league-wide rank, even under a conference filter — `1, 3, 4, 7` is correct
   * (CONTRACT.md §7.7). `rank <= 0` renders an em dash. */
  readonly ranks: Readonly<Record<string, number>>;
}

export interface TeamEfficiencyPayload {
  readonly season: string;
  readonly seasonType: string;
  readonly sortBy: string;
  readonly style: "table" | "scatter";
  readonly leagueAverage: MetricMap;
  readonly rows: readonly TeamEfficiencyRow[];
}

export interface NextGameProjectionGame {
  readonly gameId: string;
  readonly date: string;
  readonly opponentAbbr: string;
  readonly opponent: TeamRef;
  readonly isHome: boolean;
  readonly restDays: number | null;
  readonly isBackToBack: boolean | null;
  readonly opponentDefRtg: number | null;
  readonly expectedPace: number | null;
}

export interface NextGameProjectionMinutes {
  readonly value: number | null;
  readonly displayValue: string;
  readonly halfLifeGames: number | null;
  readonly seasonAverage: number | null;
  readonly low: number | null;
  readonly high: number | null;
}

export interface NextGameProjectionLine {
  readonly metric: string;
  readonly descriptor: MetricDescriptor;
  readonly mean: number;
  readonly displayValue: string;
  readonly low: number | null;
  readonly high: number | null;
  readonly intervalLevel: number | null;
  readonly seasonAverage: number | null;
  readonly delta: number | null;
  readonly ratePerMinute: number | null;
  readonly shrinkageK: number | null;
  readonly exposureMinutes: number | null;
  readonly shrinkageWeight: number | null;
  readonly halfLifeGames: number | null;
  readonly dispersionAlpha: number | null;
  readonly dispersionMultiplier: number | null;
  /** Always `"estimated"` — a projection is never a record (CONTRACT.md §4). */
  readonly availability: Availability;
}

export interface NextGameProjectionFactor {
  readonly key: string;
  readonly label: string;
  readonly value: number;
  readonly explanation: string;
  /** Maps each `lines[].metric` to this factor's signed share of that statistic's projected
   * mean. First-order attribution, not a decomposition — the contributions do not sum to the
   * difference from a neutral-context projection (CONTRACT.md §4). */
  readonly contributions: MetricMap;
}

export interface NextGameProjectionCombo {
  readonly label: string;
  readonly mean: number;
  readonly sd: number;
  readonly sdIfIndependent: number;
  readonly inflation: number;
  readonly low: number | null;
  readonly high: number | null;
  readonly correlation: readonly (readonly number[])[];
}

export interface NextGameProjectionMethod {
  readonly summary: string;
  readonly minutesHalfLifeGames: number;
  readonly correlationApplied: boolean;
  readonly dispersionShrinkageGames: number;
}

export interface NextGameProjectionPayload {
  readonly player: PlayerRef | null;
  /** `null` when the player has no scheduled next game — the payload is still produced,
   * projected against a league-average opponent, and `notes` says so (CONTRACT.md §4). */
  readonly game: NextGameProjectionGame | null;
  readonly projectedMinutes: NextGameProjectionMinutes;
  readonly lines: readonly NextGameProjectionLine[];
  /** Always an array — empty when `showFactors` was configured off, never an absent key. */
  readonly factors: readonly NextGameProjectionFactor[];
  /** `null` when `showCombo` was off, or PTS/REB/AST were not all requested. */
  readonly combo: NextGameProjectionCombo | null;
  readonly method: NextGameProjectionMethod;
  readonly notes: readonly string[];
}

export interface ProjectionBoardRow {
  readonly player: PlayerRef;
  readonly matchup: string;
  readonly opponentAbbr: string;
  readonly isHome: boolean;
  readonly gameId: string;
  readonly gameDate: string;
  readonly metric: string;
  readonly descriptor: MetricDescriptor;
  readonly projection: number;
  readonly displayValue: string;
  readonly low: number | null;
  readonly high: number | null;
  readonly intervalLevel: number | null;
  /** The mark the band is read against — never a market line (CONTRACT.md §4). `null` when
   * `reference` is `"none"`. */
  readonly referenceValue: number | null;
  readonly delta: number | null;
  /** A ranking scale, not a significance claim — rows sort by `abs(deltaZ)` descending, never
   * by `abs(delta)`. `null` when `reference` is `"none"` or no interval was published. */
  readonly deltaZ: number | null;
  readonly projectedMinutes: number | null;
  readonly availability: Availability;
}

export interface ProjectionBoardPayload {
  readonly date: string;
  /** `null` when every row falls on `date` — three genuinely different dates, per
   * CONTRACT.md §4: `date`/`throughDate` bracket the games being projected, `selectionDate`
   * is only how the candidates were chosen. Never head this board with `selectionDate`. */
  readonly throughDate: string | null;
  readonly selectionDate: string;
  readonly gameCount: number;
  readonly reference: "season_average" | "career_average" | "none";
  readonly referenceLabel: string | null;
  readonly rows: readonly ProjectionBoardRow[];
  readonly note: string | null;
}

/** A `fantasy_draft_board` / `fantasy_trade` category key — snake_case, matching the nine
 * scoring categories (`pts`, `fg3m`, `reb`, `ast`, `stl`, `blk`, `tov`, `fg_pct`, `ft_pct`). */
export type FantasyCategory = string;

/** A `columns[]` entry is deliberately *not* a `MetricDescriptor` (CONTRACT.md §4): nine are
 * pool-relative z-scores and three are identity, none of which exist in `metrics.json`. */
export interface FantasyDraftBoardColumn {
  readonly key: string;
  readonly label: string;
  /** `null` for a text column (e.g. `team`). */
  readonly format: string | null;
  readonly group: "summary" | "production" | "impact";
  readonly align: "leading" | "trailing";
  /** `null` where neither direction is a virtue — field-goal attempts are volume, not merit. */
  readonly higherIsBetter: boolean | null;
  readonly signed: boolean;
  /** Only ever true on a `z_` column: this category is weighted 0 by the reader's punts. */
  readonly punted: boolean;
}

export interface FantasyDraftBoardRow {
  readonly rank: number;
  readonly round: number;
  readonly pickInRound: number;
  readonly player: PlayerRef;
  readonly baselineRank: number | null;
  readonly totalZ: number | null;
  readonly valueOverReplacement: number | null;
  readonly suggestion: number | null;
  readonly espnPoints: number | null;
  readonly yahooPoints: number | null;
  /** Keyed exactly by `columns[].key`; render only the keys present, in `columns` order — an
   * unknown column is skipped, never shifted. Every value is a number except `team`. */
  readonly values: Readonly<Record<string, number | string | null>>;
  readonly fills: readonly string[];
  readonly reason: string | null;
  readonly availability: Availability;
}

export interface FantasyDraftBoardPayload {
  readonly season: string;
  readonly seasonType: string;
  readonly scoring: string;
  readonly categories: readonly FantasyCategory[];
  readonly puntCategories: readonly FantasyCategory[];
  readonly teams: number;
  readonly rosterSpots: number;
  readonly nextPick: {
    readonly overall: number;
    readonly round: number;
    readonly pickInRound: number;
  } | null;
  readonly poolSize: number;
  readonly replacementValue: number | null;
  readonly weakestCategories: readonly FantasyCategory[];
  readonly rosterStrength: MetricMap;
  readonly columns: readonly FantasyDraftBoardColumn[];
  readonly rows: readonly FantasyDraftBoardRow[];
  readonly note: string | null;
}

export interface FantasyTradePlayerLine {
  readonly player: PlayerRef;
  readonly totalZ: number | null;
  readonly baselineRank: number | null;
  readonly gamesPlayed: number | null;
  readonly categories: MetricMap;
  readonly availability: Availability;
}

export interface FantasyTradeSide {
  readonly label: string;
  readonly count: number;
  readonly totalZ: number | null;
  readonly espnPoints: number | null;
  readonly yahooPoints: number | null;
  readonly players: readonly FantasyTradePlayerLine[];
  readonly missing: readonly string[];
}

export interface FantasyTradeCategoryDelta {
  readonly category: FantasyCategory;
  readonly give: number | null;
  readonly get: number | null;
  readonly change: number | null;
  readonly net: number | null;
  readonly verdict: string;
  readonly punted: boolean;
}

export interface FantasyTradePointsDelta {
  readonly change: number | null;
  readonly net: number | null;
  readonly verdict: string;
}

export interface FantasyTradeSensitivityScenario {
  readonly key: string;
  readonly label: string;
  readonly net: number | null;
}

export interface FantasyTradeSensitivity {
  readonly base: number | null;
  readonly low: number | null;
  readonly high: number | null;
  readonly width: number | null;
  readonly lowScenario: string;
  readonly highScenario: string;
  /** True when the low/high scenarios disagree about the trade's sign — the most useful thing
   * this block can say (CONTRACT.md §4). Never a probability. */
  readonly flips: boolean;
  readonly scenarios: readonly FantasyTradeSensitivityScenario[];
}

export interface FantasyTradePayload {
  readonly season: string;
  readonly seasonType: string;
  readonly puntCategories: readonly FantasyCategory[];
  readonly give: FantasyTradeSide;
  readonly get: FantasyTradeSide;
  readonly categories: readonly FantasyTradeCategoryDelta[];
  readonly changeZ: number | null;
  /** A separate key on purpose — never fold into `netZ` without showing this too
   * (CONTRACT.md §4): a freed roster slot is refilled at `replacementValue`, below pool
   * average by construction. */
  readonly rosterAdjustment: number | null;
  readonly netZ: number | null;
  readonly replacementValue: number | null;
  readonly poolSpread: number | null;
  readonly verdict: string;
  readonly bands: { readonly fair: number; readonly clear: number };
  readonly points: Readonly<Record<string, FantasyTradePointsDelta>>;
  /** A range over four named assumptions, never an interval or a probability. */
  readonly sensitivity: FantasyTradeSensitivity;
  readonly note: string | null;
}

export interface CareerArcSeason {
  readonly season: string;
  readonly seasonType: string;
  readonly age: number | null;
  readonly teamAbbr: string | null;
  readonly gp: number | null;
  readonly value: number | null;
  readonly displayValue: string;
  readonly availability: Availability;
}

export interface CareerArcPeak {
  readonly season: string;
  readonly value: number;
  readonly displayValue: string;
}

export interface CareerArcPayload {
  readonly player: PlayerRef;
  readonly metric: MetricDescriptor;
  /** Reverts to `"season"` client-side unless some row has a non-null `age` — see
   * `design/svg/RunPolyline.tsx`'s caller, not this type, for that fallback. */
  readonly xAxis: "season" | "age";
  /** Unlike `shot_profile`'s always-5 zones, a valueless season is omitted here entirely
   * rather than nulled (CONTRACT.md §7.7 calls this out as the opposite convention). */
  readonly seasons: readonly CareerArcSeason[];
  readonly playoffSeasons: readonly CareerArcSeason[];
  readonly eraBoundaries: readonly { readonly season: string; readonly label: string }[];
  readonly peak: CareerArcPeak | null;
}

// --------------------------------------------------------------------------------------------
// §4 League widget payloads (CONTRACT.md §4 `team_matchup`, `defense_by_position`,
// `availability_report`, `slate_projections`; contracts/fixtures/widget_<kind>.json)
//
// The four league tiles carry exactly what the `/v1` and `/v1/el` routes return, so these types
// describe both leagues. A key the NBA never sends (`seasonCode`, `clubCode`) is optional; a value
// the server can send as `null` is `T | null`, never a bare `T`: `null` is "not recorded" and
// renders as an em dash, and an unmeasured figure is never a zero (CONTRACT.md §6). No payload
// here has a probability of winning, a line, or a rank of any team in any statistic.
// --------------------------------------------------------------------------------------------

export type LeagueKey = "nba" | "euroleague";

/** `LeagueTeamRef`: a team in either league. `id` is a string in both (the NBA's numeric id as
 * text, or the EuroLeague's club code); clients key entities by `(league, id)`. */
export interface LeagueTeamRef {
  readonly league: LeagueKey;
  readonly id: string;
  readonly abbr: string;
  readonly name: string;
  readonly shortName: string | null;
  /** NBA only. */
  readonly teamId?: number;
  /** EuroLeague only. */
  readonly clubCode?: string;
  readonly tvCode?: string | null;
}

/** `LeaguePlayerRef`. `position` is one of three buckets; the raw listing is in `positionRaw`. */
export interface LeaguePlayerRef {
  readonly league: LeagueKey;
  readonly id: string;
  readonly name: string;
  readonly position: "G" | "F" | "C" | null;
  readonly positionRaw: string | null;
  readonly jersey: string | null;
  readonly headshotUrl: string | null;
  /** NBA only. */
  readonly playerId?: number;
  /** EuroLeague only. */
  readonly personCode?: string;
}

/** Where a status came from. `publishedAt` is the source's own date, and ages are counted from it. */
export interface LeagueSource {
  readonly kind: string;
  readonly label: string;
  readonly url: string | null;
  readonly publishedAt: string;
  readonly asOf: string | null;
  readonly fetchedAt: string | null;
  readonly snapshotId: number | null;
}

export interface LeagueSourceState {
  readonly key: string;
  readonly label: string;
  readonly state: string;
  readonly reason: string | null;
  readonly lastSuccessAt: string | null;
}

/** A league payload's freshness. `isDemo` must be shown as a banner: the games are invented. */
export interface LeagueFreshness {
  readonly league: LeagueKey;
  readonly syncVersion: number;
  readonly dataThrough: string | null;
  readonly generatedAt: string;
  readonly isDemo: boolean;
  readonly sources: readonly LeagueSourceState[];
}

/** `GameRefL`. `"resultPending"` is a game whose tip-off is long past with no result stored: it
 * is not "upcoming". `isNeutral` is `null` when unknown, never assumed false. */
export interface LeagueGameRef {
  readonly league: LeagueKey;
  readonly gameId: string;
  readonly date: string;
  readonly tipoffUtc: string | null;
  readonly venue: string | null;
  readonly isNeutral: boolean | null;
  readonly round: number | null;
  readonly phase: string | null;
  readonly status: "scheduled" | "resultPending" | "final" | "postponed";
  readonly home: LeagueTeamRef;
  readonly away: LeagueTeamRef;
  readonly homePts: number | null;
  readonly awayPts: number | null;
  readonly overtimePeriods: number | null;
}

export type LeagueAvailabilityStatus = "out" | "doubtful" | "questionable" | "probable" | "available";

/** One status in force (or not) for a player. A status is never shown without its `source`. */
export interface LeagueAbsence {
  readonly player: LeaguePlayerRef;
  readonly status: LeagueAvailabilityStatus | null;
  readonly chanceOfPlaying: number | null;
  readonly expectedPointsLost: number | null;
  readonly expectedMinutesLost: number | null;
  readonly inForce: boolean;
  readonly isStale: boolean;
  readonly source: LeagueSource;
}

export interface LeagueFormGame {
  readonly gameId: string;
  readonly date: string;
  readonly opponent: LeagueTeamRef;
  readonly isHome: boolean;
  readonly isNeutral: boolean | null;
  readonly teamScore: number;
  readonly opponentScore: number;
  readonly result: "W" | "L";
  readonly overtimePeriods: number | null;
}

export interface LeagueSplit {
  readonly games: number;
  readonly pointsPerGame: number | null;
  readonly pointsAllowedPerGame: number | null;
}

export interface LeagueWindowSplit extends LeagueSplit {
  readonly window: number;
}

export interface LeagueAdjustedPoints {
  /** `null` until at least five games qualify; `games` says how many did. */
  readonly value: number | null;
  readonly games: number;
}

export interface LeagueAvailabilitySummary {
  readonly out: number;
  readonly doubtful: number;
  readonly questionable: number;
  readonly probable: number;
  readonly keyAbsences: readonly LeagueAbsence[];
  readonly freshnessState: string;
  readonly asOf: string | null;
}

export type DefenseBand = "better" | "typical" | "worse";

export type DefenseWithheldReason = "minimumGames" | "positionCoverage" | "leagueSample";

/** `withheld` replaces every index, standard error and band with `null`; its `message` goes on
 * screen. The raw buckets still show. */
export interface DefenseWithheld {
  readonly reason: DefenseWithheldReason;
  readonly message: string;
}

export interface MatchupDefenseSummary {
  readonly pointsAllowedPerGame: number | null;
  readonly buckets: readonly {
    readonly position: string;
    readonly pointsAllowedPerGame: number | null;
    readonly deltaPerGame: number | null;
    readonly band: DefenseBand | null;
  }[];
  readonly withheld: DefenseWithheld | null;
}

export interface TeamMatchupSide {
  readonly side: "home" | "away" | null;
  readonly team: LeagueTeamRef;
  readonly record: { readonly wins: number; readonly losses: number };
  readonly games: number;
  readonly pointsPerGame: number | null;
  /** How many the team lets opponents score: the mean of the opponents' points. */
  readonly pointsAllowedPerGame: number | null;
  readonly differentialPerGame: number | null;
  readonly pointsPerRegulation: number | null;
  readonly pointsAllowedPerRegulation: number | null;
  readonly latestGame: LeagueFormGame | null;
  readonly form: readonly LeagueFormGame[];
  readonly lastN: LeagueWindowSplit;
  readonly last10: LeagueWindowSplit;
  readonly venueSplits: {
    readonly home: LeagueSplit;
    readonly away: LeagueSplit;
    /** EuroLeague only; the NBA records no neutral-site games. */
    readonly neutral: LeagueSplit | null;
  };
  readonly adjustedPointsAgainst: LeagueAdjustedPoints;
  readonly adjustedPointsFor: LeagueAdjustedPoints;
  readonly availability: LeagueAvailabilitySummary | null;
  readonly defenseSummary: MatchupDefenseSummary | null;
}

export interface ProjectionRange {
  readonly low: number;
  readonly high: number;
}

export interface LeagueSideProjection {
  readonly team: LeagueTeamRef;
  readonly projectedPoints: number;
  readonly range80: ProjectionRange | null;
  readonly fullStrengthPoints: number;
  readonly availabilityEffect: number;
  readonly attackIndex: number;
  readonly attackIndexAfterAvailability: number;
  readonly defenceIndex: number;
  readonly capBinding: boolean;
  readonly unassignedPoints: number | null;
  readonly replacementPoints: number | null;
  readonly keyAbsences: readonly LeagueAbsence[];
}

export interface ProjectionConstant {
  readonly key: string;
  /** `null` for a spread the model has not calibrated yet (see `intervalBasis`). */
  readonly value: number | null;
  readonly provenance: string;
  readonly isDefault: boolean;
}

export interface ProjectionModel {
  readonly key: string;
  readonly version: string;
  readonly kind: "latest" | "locked" | "reconstructed" | "imported";
  readonly computedAt: string;
  readonly inputsCutoff: string;
  readonly capPolicy: string;
  readonly constants: readonly ProjectionConstant[];
}

/** A game's projected score. There is no probability of winning in it, and nothing to compare it
 * with but what happened. `intervalBasis: "assumed"` must be shown as an "assumed spread". */
export interface LeagueGameProjection {
  readonly league: LeagueKey;
  readonly game: LeagueGameRef;
  readonly freshness: LeagueFreshness;
  readonly home: LeagueSideProjection;
  readonly away: LeagueSideProjection;
  readonly margin: number;
  readonly marginRange80: ProjectionRange | null;
  /** `null` for a toss-up. */
  readonly projectedWinner: LeagueTeamRef | null;
  readonly isTossUp: boolean;
  readonly summary: string;
  readonly combinedPoints: number;
  readonly combinedAvailabilityEffect: number;
  readonly homeAdvantagePoints: number;
  readonly venueAssumed: boolean;
  readonly intervalBasis: "assumed" | "fittedPrevSeason" | "fittedLedger" | null;
  readonly model: ProjectionModel;
  readonly assumptions: {
    readonly assumedAvailable: {
      readonly home: number;
      readonly away: number;
      readonly basis: string;
      readonly homeBasis?: string;
      readonly awayBasis?: string;
    };
    readonly staleEntriesIgnored: number;
  };
  readonly result: {
    readonly homePts: number;
    readonly awayPts: number;
    readonly marginMiss: number;
    readonly winnerCalled: boolean | null;
  } | null;
  readonly availability: Availability;
  readonly notes: readonly string[];
}

/** `team_matchup`. NBA payloads carry `seasonType`; EuroLeague payloads carry `seasonCode` and
 * `phase`. `game` and `projection` are `null` when the two teams are not scheduled to meet. */
export interface TeamMatchupPayload {
  readonly league: LeagueKey;
  readonly season: string;
  readonly seasonCode?: string;
  readonly seasonType: string | null;
  readonly phase: readonly string[] | null;
  readonly freshness: LeagueFreshness;
  readonly game: LeagueGameRef | null;
  readonly teams: readonly TeamMatchupSide[];
  readonly leagueAverage: { readonly pointsPerGame: number | null; readonly teams: number };
  readonly projection: LeagueGameProjection | null;
  readonly availability: Availability;
  readonly notes: readonly string[];
}

export interface DefenseBucket {
  readonly position: "G" | "F" | "C" | "PG" | "SG" | "SF" | "PF" | "unknown";
  readonly label: string;
  readonly pointsAllowedPerGame: number | null;
  readonly leagueAverage: number | null;
  readonly deltaPerGame: number | null;
  readonly opponentMinutesPerGame: number | null;
  readonly pointsPerRegulationMinutes: number | null;
  readonly leagueRate: number | null;
  readonly share: number | null;
  readonly leagueShare: number | null;
  /** Never shown as a ranking. */
  readonly rawIndex: number | null;
  readonly index: number | null;
  readonly standardError: number | null;
  readonly band: DefenseBand | null;
}

export interface DefenseMethod {
  readonly minimumGames: number;
  readonly provisionalBelowGames: number;
  readonly coverageCeiling: number;
  readonly shrinkage: string;
  readonly leagueReliability: {
    readonly G: number | null;
    readonly F: number | null;
    readonly C: number | null;
  };
  readonly leagueSignal: "detected" | "none detected" | null;
  readonly bandRule: string;
  readonly positionSource: string | null;
  readonly taxonomy: string;
  readonly limitations: readonly string[];
}

/** One team's defence by the position of the players who scored against it (`team` is present). */
export interface DefenseByPositionTeamPayload {
  readonly league: LeagueKey;
  readonly season: string;
  readonly seasonCode?: string;
  readonly seasonType?: string | null;
  readonly phase?: readonly string[] | null;
  readonly scheme: "gfc" | "workbook5";
  readonly basis: "perGame" | "perMinute";
  readonly regulationMinutes: number;
  readonly freshness: LeagueFreshness;
  readonly team: LeagueTeamRef;
  readonly window: {
    readonly kind: "season" | "lastGames";
    readonly games: number;
    readonly requested: number | null;
  };
  readonly pointsAllowedPerGame: number | null;
  readonly leaguePointsAllowedPerGame: number | null;
  readonly buckets: readonly DefenseBucket[];
  /** Visibly marked when true; a provisional table shows no band. */
  readonly provisional: boolean;
  readonly withheld: DefenseWithheld | null;
  readonly coverage: {
    readonly listed: number | null;
    readonly workbookListing: number | null;
    readonly unknown: number | null;
  };
  readonly reconciliation: {
    readonly sumOfBuckets: number | null;
    readonly pointsAllowedPerGame: number | null;
    readonly unreconciledGames: number;
    readonly identity: string;
  };
  readonly method: DefenseMethod;
  readonly methodMessage: string | null;
  readonly availability: Availability;
  /** "Points by the position of who scored them, not who guarded whom": always shown. */
  readonly caveat: string;
  readonly notes: readonly string[];
}

/** The whole league's defences, sorted by points allowed (no rank field; `teams` is the key). */
export interface DefenseByPositionTablePayload {
  readonly league: LeagueKey;
  readonly season: string;
  readonly seasonCode?: string;
  readonly seasonType?: string | null;
  readonly phase?: readonly string[] | null;
  readonly scheme: "gfc" | "workbook5";
  readonly basis: "perGame" | "perMinute";
  readonly regulationMinutes: number;
  readonly freshness: LeagueFreshness;
  readonly leaguePointsAllowedPerGame: number | null;
  readonly window: {
    readonly kind: "season" | "lastGames";
    readonly requested: number | null;
  };
  readonly teams: readonly {
    readonly team: LeagueTeamRef;
    readonly games: number;
    readonly pointsAllowedPerGame: number | null;
    readonly buckets: readonly DefenseBucket[];
    readonly provisional: boolean;
    readonly withheld: DefenseWithheld | null;
  }[];
  readonly method: DefenseMethod;
  readonly methodMessage: string | null;
  readonly availability: Availability;
  readonly caveat: string;
  readonly notes: readonly string[];
}

/** `defense_by_position` is a team's payload when a team is configured and the league's table
 * when none is; the two differ by the `team` key (and the `teams` list). */
export type DefenseByPositionPayload = DefenseByPositionTeamPayload | DefenseByPositionTablePayload;

export interface AvailabilityEntry {
  /** `null` for a team whose report has not been submitted: there is no row to point at. */
  readonly statusId: number | null;
  readonly overrideId: number | null;
  /** `null` when the name could not be matched to one player; it is never guessed. */
  readonly player: LeaguePlayerRef | null;
  readonly playerName: string;
  /** `null` is "no report", an em dash: never "available". */
  readonly status: LeagueAvailabilityStatus | null;
  readonly statusLabel: string;
  readonly chanceOfPlaying: number | null;
  readonly modelStatus: LeagueAvailabilityStatus | null;
  readonly reasonCategory: string | null;
  readonly reasonText: string | null;
  readonly expectedReturnText: string | null;
  readonly expectedReturn: {
    readonly roundFrom: number | null;
    readonly roundTo: number | null;
    readonly date: string | null;
  } | null;
  readonly game: LeagueGameRef | null;
  readonly isOverride: boolean;
  readonly inForce: boolean;
  readonly outOfForceReason: string | null;
  readonly isStale: boolean;
  /** Minutes since the source published it, never since it was fetched. */
  readonly ageMinutes: number | null;
  readonly source: LeagueSource;
}

export interface AvailabilityTeam {
  readonly team: LeagueTeamRef;
  readonly reportState: "submitted" | "notYetSubmitted" | "noReport";
  readonly game?: LeagueGameRef | null;
  readonly entries: readonly AvailabilityEntry[];
}

/** Headline links: title, link, date and source only, never an article body. */
export interface LeagueNewsLink {
  readonly itemId: number;
  readonly title: string;
  readonly link: string;
  readonly publishedAt: string;
  readonly sourceName: string;
  readonly teams: readonly LeagueTeamRef[];
  readonly players: readonly LeaguePlayerRef[];
}

/** `availability_report`. It has no `availability` key: how far to trust it is its `state`. */
export interface AvailabilityReportPayload {
  readonly league: LeagueKey;
  readonly asOf: string | null;
  readonly freshness: LeagueFreshness;
  readonly state: "fresh" | "stale" | "noReportYet" | "unreadable" | "disabled";
  readonly message: string;
  readonly teams: readonly AvailabilityTeam[];
  readonly news: readonly LeagueNewsLink[] | null;
  readonly attribution: string;
}

export interface ProjectionReviewRow {
  readonly modelKey: string;
  readonly games: number;
  readonly decidedGames?: number;
  readonly winnersCalled: number;
  readonly tossUps: number;
  readonly meanAbsMarginMiss: number | null;
  readonly meanAbsScoreMiss: number | null;
  readonly meanAbsCombinedMiss: number | null;
}

/** `slate_projections`. NBA payloads carry `date` and `availability`; EuroLeague payloads carry
 * `round` and no `availability` (a slate with games is an estimate). */
export interface SlateProjectionsPayload {
  readonly league: LeagueKey;
  readonly date: string | null;
  readonly round: number | null;
  readonly freshness: LeagueFreshness;
  readonly model: ProjectionModel;
  readonly games: readonly LeagueGameProjection[];
  readonly review: {
    readonly byModel: readonly ProjectionReviewRow[];
    readonly reconstructed: ProjectionReviewRow | null;
  } | null;
  readonly availability?: Availability;
  readonly notes: readonly string[];
}
