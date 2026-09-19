/**
 * The TypeScript mirror of `POST /v1/dashboard/resolve`'s envelope and the sixteen widget
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
 * sixteen widget payloads. It deliberately does not cover the other `/v1/*` REST responses
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
 * `generated/contracts.ts`) is the single source for which sixteen kinds exist; this map's
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
