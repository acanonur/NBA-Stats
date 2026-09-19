# Hardwood Web — frozen interface

**This is WP0's final act (WEB_DESIGN.md §9).** Five agents (WP1–WP5) build in parallel with
no shared review pass between them until integration. This document is what lets that work:
the exact exported signatures of the four files below, transcribed from what is actually on
disk (or, for `api/client.ts`, specified ahead of it existing — see that section). **Nothing
in WP1–WP5 may edit a WP0-owned file.** If a signature here is wrong for what you need, that
is a request back to WP0, not a local edit.

Two of these four files (`generated/tokens.ts`, `generated/contracts.ts`) are generated —
`GENERATED — DO NOT EDIT` is the first line of both — and will only ever change by re-running
`contracts/tools/gen_web_tokens.py` / `gen_web_contracts.py`, which is itself gated by
`scripts/check_contracts.py` checks (a) and (i). `api/types.ts` is hand-written but WP0-owned.

---

## 1. `src/generated/tokens.ts`

```ts
export const CHART_SERIES_LIGHT: readonly string[]; // length 8, hex strings
export const CHART_SERIES_DARK: readonly string[]; // length 8, hex strings

export interface MonogramSwatch { readonly light: string; readonly dark: string; }
export const MONOGRAM_PALETTE: readonly MonogramSwatch[]; // length 12

export interface AccentTokens {
  readonly light: string;
  readonly dark: string;
  readonly softLightAlpha: number;
  readonly softDarkAlpha: number;
}
export type AccentName =
  | "orange" | "indigo" | "teal" | "red" | "amber" | "green" | "blue" | "purple" | "graphite";
export const ACCENTS: Readonly<Record<AccentName, AccentTokens>>; // all 9 keys present

export const SPACING: { hairline: 1; xxs: 2; xs: 4; sm: 8; md: 12; lg: 16; xl: 24; xxl: 32 };
export const RADIUS: { chip: 6; control: 10; card: 16; sheet: 20; pill: 999 };

export interface TextStyleTokens {
  readonly px: number;
  readonly weight: number;
  readonly tracking: number;
  readonly numeric: boolean;
  readonly color: string; // a token NAME (e.g. "textPrimary"), not a CSS value — resolve it
                           // yourself against the --hw-* custom properties, this is not a hex.
}
export type HardwoodTextStyleName =
  | "displayValue" | "statValue" | "statLabel" | "tableHeader" | "tableCell" | "caption"
  | "sectionTitle" | "widgetTitle";
export const TEXT_STYLES: Readonly<Record<HardwoodTextStyleName, TextStyleTokens>>;

export type WidgetSizeKey = "small" | "medium" | "large";
export const ESTIMATED_HEIGHT: Readonly<Record<WidgetSizeKey, number>>; // px, for skeletons

export interface SizeSpan { readonly compact: number; readonly regular: number; }
export const SIZE_SPANS: Readonly<Record<WidgetSizeKey, SizeSpan>>;
// small {1,1} · medium {2,2} · large {2,4} — emit BOTH spans per tile and swap at the
// breakpoint (§7.5); never pick one and recompute the other.

export const GRID: {
  columns: { compact: 2; regular: 4 };
  regularBreakpointPx: 834;
  gap: { tiles: 12; broadsheet: 24 };
};
```

`tokens.css` (same generator) defines a `--hw-*` custom property for every value above that is
a *colour, length or type setting* — the palette, `--hw-chart-0…7`, `--hw-mono-0…11`, the nine
`--hw-accent-<name>` / `--hw-accent-<name>-soft` pairs, the broadsheet colours and metrics,
`--hw-space-*`, `--hw-radius-*`, the three shadows, and
`--hw-font-<slug>-{size,weight,tracking,numeric}` for each `HardwoodTextStyleName` (slug drops
a trailing `Value` then kebab-cases: `displayValue → display`, `tableHeader → table-header`).
`ESTIMATED_HEIGHT`, `SIZE_SPANS` and `GRID` deliberately have **no** CSS counterpart — they are
arithmetic inputs, and the grid reads them through the inline `--span-c`/`--span-r` a component
sets per tile (see `design/theme.css`). Use the `.ts` exports for arithmetic (grid spans, chart
series indexing); use the `.css` properties for anything that is just a style declaration.

---

## 2. `src/generated/contracts.ts`

```ts
export type WidgetKind = // 16-member union, snake_case, one per widget directory
  | "stat_tile" | "player_snapshot" | "leaderboard" | "game_log" | "trend_chart"
  | "four_factors" | "shot_profile" | "comparison" | "scoreboard" | "daily_movers"
  | "team_efficiency" | "next_game_projection" | "projection_board" | "fantasy_draft_board"
  | "fantasy_trade" | "career_arc";
export const WIDGET_KINDS: readonly WidgetKind[]; // same 16, in catalog order

export type MetricKey = /* 61-member union of every contracts/metrics.json metric key */;
export type MetricFormat =
  | "integer" | "decimal1" | "decimal2" | "percent1" | "percent2" | "rating1" | "plusMinus1"
  | "minutes";
export type ConfigFieldType =
  | "season" | "enum" | "enumList" | "metric" | "metricList" | "player" | "playerList"
  | "team" | "teamList" | "subject" | "subjectList" | "int" | "double" | "bool" | "date";

// The three catalog documents, verbatim `as const` — i.e. exactly
// contracts/{metrics,widgets,presets}.json parsed and re-typed, never hand-transcribed.
// Read CONTRACT.md and the JSON files directly for their shape; do not guess field names from
// this doc. `MetricDescriptor`/`MetricEraSpec`/`MetricDomain` in api/types.ts (§4 below) type
// one entry of METRICS_DOCUMENT.metrics for use as a widget-payload field.
export const METRICS_DOCUMENT: { schemaVersion: 1; categories: [...]; formats: [...];
  eraBoundaries: [...]; metrics: [...] };
export const WIDGETS_DOCUMENT: { schemaVersion: 1; sizes: [...]; gridColumns: {...};
  configFieldTypes: [...]; widgets: [...] };
export const PRESETS_DOCUMENT: { schemaVersion: number; version: number; presets: [...];
  subjectTokens: [...] };

export interface EraBoundary {
  readonly season: string;
  readonly label: string;
  readonly detail: string;
}
export const ERA_BOUNDARIES: readonly EraBoundary[];
// = METRICS_DOCUMENT.eraBoundaries — use this alias, not the document field, in
// AvailabilityExplainer (WEB_DESIGN.md §0.1-F).
```

### `src/generated/registry.ts` (bonus — not in WP0's literal four, but small and load-bearing)

```ts
export interface WidgetViewProps {
  readonly kind: WidgetKind;
  readonly size: WidgetSizeKey; // from generated/tokens.ts
  readonly payload: unknown; // narrow it yourself: WidgetPayloadMap[kind] from api/types.ts
}
export type WidgetComponent = (props: WidgetViewProps) => JSX.Element | null;
export interface RegistryEntry {
  readonly component: WidgetComponent | null; // null until web/src/widgets/<kind>/index.tsx
                                               // exists ON DISK AT GENERATION TIME — re-run
                                               // scripts/sync_contracts.sh after adding a
                                               // widget directory; nothing here needs a hand edit.
  readonly sizes: readonly WidgetSizeKey[];
  readonly defaultSize: WidgetSizeKey;
}
export const REGISTRY: Readonly<Record<WidgetKind, RegistryEntry>>; // all 16 keys present
```

`Record<WidgetKind, RegistryEntry>` over the 16-member union is the whole reason a widget kind
forgotten anywhere in the pipeline is a compile error, not a blank tile (WEB_DESIGN.md §0
"Why React").

---

## 3. `src/api/types.ts`

The TypeScript mirror of the resolve envelope and the sixteen widget payloads
(`POST /v1/dashboard/resolve`, CONTRACT.md §3–§4), derived from
`backend/nbastats/api/schemas.py`, `CONTRACT.md`, and every `contracts/fixtures/widget_*.json`.
**Every field the server can send `null` for is `T | null` here, never a bare `T`.**
The ESLint rule `hardwood-local/no-nullable-number-zero-fallback`
(`eslint-local/rules/no-nullable-number-zero-fallback.js`) is a lint-time guarantee that a
`number | null` field declared here cannot legally fall back to `0` with `??`/`||` anywhere in
this codebase — see that rule's docstring for exactly what it does and does not catch.

### Shared objects

```ts
export type Availability = "full" | "estimated" | "partial" | "unavailable";
export type GameStatus = "scheduled" | "live" | "final";
export type ResolveStatus = "ok" | "unchanged" | "partial" | "error";
export type SubjectType = "player" | "team";
export type MetricMap = Readonly<Record<string, number | null>>; // snake_case metric keys

export interface PlayerRef {
  readonly playerId: number; readonly name: string;
  readonly firstName: string | null; readonly lastName: string | null;
  readonly teamId: number | null; readonly teamAbbr: string | null;
  readonly position: string | null; readonly jersey: string | null;
  readonly headshotUrl: string | null; readonly isActive: boolean;
}
export interface TeamRef {
  readonly teamId: number; readonly abbr: string; readonly name: string;
  readonly city: string | null; readonly nickname: string | null;
  readonly conference: string | null; readonly division: string | null;
}
export interface MetricValue {
  readonly metric: string; readonly value: number | null; readonly displayValue: string;
  readonly rank: number | null; readonly percentile: number | null;
  readonly leagueAverage: number | null; readonly delta: number | null;
  readonly isEstimated: boolean; readonly availability: Availability;
}
export interface MetricDomain { readonly min: number; readonly max: number; }
export interface MetricEraSpec {
  readonly seasonFrom: string; readonly perGameFrom: string;
  readonly seasonLevelOnly: boolean; readonly estimatedBefore: string | null;
}
export interface MetricDescriptor {
  readonly key: string; readonly name: string; readonly shortName: string;
  readonly category: string; readonly format: string; readonly higherIsBetter: boolean;
  readonly scope: readonly string[]; readonly availability: MetricEraSpec;
  readonly domain: MetricDomain | null; readonly glossary: string;
}
export interface GameRef {
  readonly gameId: string; readonly date: string;
  readonly season: string | null; readonly seasonType: string | null;
  readonly home: TeamRef; readonly away: TeamRef;
  readonly homePts: number | null; readonly awayPts: number | null;
  readonly status: GameStatus; readonly period: number | null;
  readonly clock: string | null; readonly finalizedAt: string | null;
}
export interface SubjectRef {
  readonly type: SubjectType; readonly player: PlayerRef | null; readonly team: TeamRef | null;
}
```

### The resolve envelope

```ts
export interface ResolveContext {
  readonly favoritePlayerId?: number | null; readonly favoriteTeamId?: number | null;
  readonly timeZone?: string | null; readonly asOf?: string | null; readonly season?: string | null;
}
export interface ResolvedContext {
  readonly favoritePlayerId: number | null; readonly favoriteTeamId: number | null;
  readonly season: string | null;
}
export interface ResolveWidgetRequest {
  readonly id: string; readonly kind: WidgetKind;
  readonly size?: "small" | "medium" | "large"; readonly title?: string | null;
  readonly config?: Readonly<Record<string, unknown>>;
}
export interface DashboardResolveRequest {
  readonly layoutId?: string | null; readonly context?: ResolveContext;
  readonly knownSyncVersion?: number | null; readonly widgets: readonly ResolveWidgetRequest[];
}
export interface ErrorBody {
  readonly code: string; readonly message: string; readonly recoverable: boolean;
  readonly field: string | null; readonly requestId: string | null;
}
export interface ErrorEnvelope { readonly error: ErrorBody; }

// Keyed exactly by WidgetKind — see WidgetPayloadMapCoversEveryKind /
// NoExtraWidgetPayloadMapKeys in the file itself: a kind added to or dropped from WidgetKind
// without a matching edit here fails `npm run typecheck`, not silently.
export interface WidgetPayloadMap {
  stat_tile: StatTilePayload; player_snapshot: PlayerSnapshotPayload;
  leaderboard: LeaderboardPayload; game_log: GameLogPayload; trend_chart: TrendChartPayload;
  four_factors: FourFactorsPayload; shot_profile: ShotProfilePayload;
  comparison: ComparisonPayload; scoreboard: ScoreboardPayload;
  daily_movers: DailyMoversPayload; team_efficiency: TeamEfficiencyPayload;
  next_game_projection: NextGameProjectionPayload; projection_board: ProjectionBoardPayload;
  fantasy_draft_board: FantasyDraftBoardPayload; fantasy_trade: FantasyTradePayload;
  career_arc: CareerArcPayload;
}

export interface ResolveResult<K extends WidgetKind = WidgetKind> {
  readonly widgetId: string; readonly kind: K; readonly status: ResolveStatus;
  readonly payload: WidgetPayloadMap[K] | null; // non-null iff status is "ok" | "partial"
  readonly error: ErrorBody | null; readonly generatedAt: string | null;
  readonly ttlSeconds: number | null; readonly availability: Availability | null;
  readonly notes: readonly string[];
}
export interface DashboardResolveResponse {
  readonly syncVersion: number; readonly dataThrough: string | null;
  readonly generatedAt: string; readonly resolvedContext: ResolvedContext;
  readonly results: readonly ResolveResult[];
}
```

### The sixteen widget payloads

One interface per `WidgetKind`, named `WidgetPayloadMap`'s value type for that key
(`stat_tile → StatTilePayload`, `player_snapshot → PlayerSnapshotPayload`, and so on — see the
map above for the full kind → type-name table). **Open `src/api/types.ts` directly for every
field of every payload** — reproducing all sixteen shapes field-by-field here would stop being
a one-page summary and start being a second copy of the file that can drift from the first.
What is safe to rely on without opening the file:

* Every payload interface name is `Pascal-cased kind + "Payload"` (`fantasy_draft_board` →
  `FantasyDraftBoardPayload`), with the sole exception of a few small nested shapes each
  payload also exports (e.g. `LeaderboardRow`, `ShotZone`, `NextGameProjectionLine`,
  `FantasyTradeSide`) — grep `src/api/types.ts` for the kind's name to find them all together.
* `SparklinePoint`, `TrendChartPoint`/`TrendChartSeries`, `ShotZoneKey`/`ShotZone`, and the
  `fantasy_*` category/column shapes are exported standalone because more than one payload (or
  more than one design primitive) needs them.
* `game`, `combo`, `throughDate`, `player` on a couple of payloads, and `referenceValue`/
  `deltaZ` are the fields most likely to surprise you by being nullable — each has a one-line
  comment in the file explaining exactly when it is null and why; read those before writing a
  `!` non-null assertion on any of them.

---

## 4. `src/api/client.ts` — **frozen ahead of being written (WP4 builds it)**

Nothing under this heading exists on disk yet. It is specified here, not transcribed, because
WP3 and WP5 need something stable to import from on day one rather than waiting on WP4. If
WP4's real implementation needs to diverge from this, that is a request back to WP0 like any
other change to this document — not a silent local decision, because WP3/WP5 may already be
compiled against the shape below.

```ts
/** Every non-2xx response, and every ApiError thrown by fetchJson, carries this shape. */
export class ApiError extends Error {
  readonly code: string;           // ErrorBody.code (api/types.ts) — e.g. "metric_unavailable"
  readonly status: number;         // HTTP status
  readonly recoverable: boolean;
  readonly field: string | null;
  readonly requestId: string | null;
  constructor(status: number, body: ErrorBody);
}

export interface FetchJsonOptions {
  readonly method?: "GET" | "POST" | "PUT" | "DELETE";
  readonly body?: unknown;               // JSON-serialised; omit for GET/DELETE
  readonly signal?: AbortSignal;
}

/**
 * The one function every other api/*.ts module (resolve.ts, session.ts, dashboards.ts,
 * sync.ts) calls to reach the service. Never called directly from a component or a widget —
 * that indirection is what makes the single-origin, cookie-based session model
 * (WEB_DESIGN.md §0) enforceable in one place:
 *   - `credentials: "same-origin"` always (there is no cross-origin case in this app).
 *   - Path is joined to `/v1` — callers pass `"/auth/session"`, never `"/v1/auth/session"`.
 *   - A mutating method (`POST`/`PUT`/`DELETE`) always sends `X-Hardwood-CSRF`
 *     (`accounts/csrf.py`'s `CSRF_HEADER`) from the in-memory token AuthProvider holds — never
 *     from a cookie or localStorage (WEB_DESIGN.md §7.3: the CSRF token lives in memory only).
 *   - A non-2xx response is parsed as `ErrorEnvelope` (api/types.ts) and thrown as `ApiError`;
 *     a body that fails to parse as that shape throws `ApiError` with `code: "bad_request"`
 *     and `status` from the response, never a raw `SyntaxError`.
 *   - A `401` clears the in-memory auth context and navigates to `/sign-in?next=<path>` —
 *     this is the ONE place that redirect happens, so every caller gets it for free
 *     (WEB_DESIGN.md §7.6: "auth failure is a page-level outcome, never a grey tile").
 */
export function fetchJson<T>(path: string, options?: FetchJsonOptions): Promise<T>;

/** The origin this app is served from — always same-origin per WEB_DESIGN.md §0, exposed only
 * so a component can build an absolute URL (e.g. for a copy-link button) without hard-coding
 * `location.origin` in six different files. */
export function apiOrigin(): string;
```

`resolve.ts` is the **only** module allowed to call `/dashboard/resolve`, and only `src/api/*`
may import `client.ts` at all. Both halves are enforced in `eslint.config.js` (WEB_DESIGN.md
§7.6), and both are directory-scoped rather than path-literal, so they hold at any nesting
depth:

* `no-restricted-imports` with `patterns: ["**/api/client", …]`, applied to `src/**/*.{ts,tsx}`
  with `src/api/**` excluded — a component or widget importing `fetchJson` is a lint error.
* `no-restricted-syntax` with `Literal[value=/\/dashboard\/resolve/]`, applied to
  `src/**/*.{ts,tsx}` with `src/api/resolve.ts` excluded — naming the endpoint anywhere else is
  a lint error, so the 24-widget chunking, the clock-dependent chunk split and the
  `unchanged`-without-cache retry cannot be reimplemented a second, subtly different way.

Every other endpoint goes through `session.ts` / `dashboards.ts` / `sync.ts`, which this
document does not freeze a shape for: they are ordinary CRUD wrappers
around endpoints `CONTRACT.md` §3 already fixes, and inventing their exact TypeScript
signatures ahead of a live server to test them against would cost more in guessed-wrong
surface area than it saves. `api/types.ts` (§3 above) is what those two files should return.

---

## Amendment log

A change to any signature above is a change to this file, timestamped and reasoned, not a
silent edit — see the top of this document for who is allowed to make one.

**2026-09-19 — WP0 verification pass.** No exported *signature* changed; two descriptions in
this document were wrong about the code and were corrected against it.

1. §1's "one `--hw-*` custom property per leaf value above" was false for `ESTIMATED_HEIGHT`,
   `SIZE_SPANS` and `GRID`, which `tokens.css` does not emit and never did. An agent that took
   the sentence literally would have written `var(--hw-estimated-height-small)` and got an
   invalid declaration with no build error. Rewritten to list what `tokens.css` actually
   defines and to say plainly that those three are `.ts`-only.
2. §4's claim that a `no-restricted-imports` rule made `resolve.ts` the only caller of
   `/dashboard/resolve` described a rule that did not do that. The rule in `eslint.config.js`
   was `paths: [{ name: "../api/resolve" }]`: it matched the import *string*, so it fired only
   on a file exactly one directory below `api/` and never on `../../api/resolve` from a widget;
   and what it banned was importing `api/resolve`, which is the sanctioned way to reach the
   resolve helpers, not the raw client. It has been replaced with the two directory-scoped
   rules now described in §4, each verified to fire on a violating file and to stay silent on
   `src/api/resolve.ts` and `src/api/session.ts`.

Two further `eslint.config.js` fixes in the same pass affect no signature in this document, but
WP3-WP5 should know the guard rails are real now and will fail their builds:

* The `dangerouslySetInnerHTML` ban was written as `no-restricted-properties` with
  `object: "*"`. That rule has no wildcard (`"*"` matches an object literally named `*`), and
  in JSX `dangerouslySetInnerHTML` is a `JSXAttribute`, not a `MemberExpression`, so the rule
  was inert — `<div dangerouslySetInnerHTML={{ __html: html }} />` linted clean. It is now
  three `no-restricted-syntax` selectors (attribute, object property, member read).
* `.stylelintrc.json` exempted `src/design/theme.css` from the no-raw-colour rule. WEB_DESIGN.md
  §7.9 exempts only `src/generated/tokens.css`, and with both hand-written and generated CSS
  exempted the stylelint run was linting zero files. The exemption is now just
  `src/generated/tokens.css`; `theme.css` had no raw colour in it, but it did have four
  `stylelint-config-standard` violations that nothing had ever reported.
