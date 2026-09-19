/**
 * `POST /v1/dashboard/resolve` — the only module allowed to name that endpoint
 * (`eslint.config.js`'s two directory-scoped rules enforce it: no other file may import
 * `api/client` at all, and no other file may write the literal `/dashboard/resolve`). Every
 * contract trap WEB_DESIGN.md §7.6 calls out lives here, once:
 *
 *  - requests are chunked at {@link MAX_WIDGETS_PER_REQUEST} widgets;
 *  - the TanStack Query cache key for one widget is **(id, resolved config)**
 *    (`widgetQueryKey`), never the id alone — a reconfigured tile must not keep showing the old
 *    metric's numbers under the old sync version;
 *  - `scoreboard` / `daily_movers` (`CLOCK_DEPENDENT_KINDS`) are always split into their own
 *    chunk and sent with `knownSyncVersion` omitted, and refetched on their own 60s cadence
 *    regardless of anything else — their payload can move without a sync-version bump, so
 *    treating them like every other widget would freeze a live scoreboard;
 *  - `knownSyncVersion` is sent for a chunk only when every widget in it already has a cached
 *    payload — a widget with nothing cached could never legally come back `"unchanged"`;
 *  - a `status: "unchanged"` result with nothing cached is retried once, alone, with
 *    `knownSyncVersion` omitted; a second `"unchanged"` becomes a synthesised `"error"` result
 *    (`unchanged_without_cache`) rather than an empty tile forever.
 */
import type { QueryClient } from "@tanstack/react-query";
import { fetchJson } from "./client";
import type {
  DashboardResolveRequest,
  DashboardResolveResponse,
  ResolveContext,
  ResolveResult,
  ResolveWidgetRequest,
} from "./types";
import type { WidgetKind } from "../generated/contracts";

/** CONTRACT.md §3: the whole-request failure mode, never a per-widget error. */
export const MAX_WIDGETS_PER_REQUEST = 24;

/** Widget kinds whose payload can move without a sync-version bump (WEB_DESIGN.md §7.6) —
 * always resolved in their own chunk, with `knownSyncVersion` omitted. */
export const CLOCK_DEPENDENT_KINDS: ReadonlySet<WidgetKind> = new Set(["scoreboard", "daily_movers"]);

/** How often a clock-dependent widget is refetched regardless of anything else — its own
 * `ttlSeconds` (WEB_DESIGN.md §7.6). */
export const CLOCK_DEPENDENT_REFRESH_MS = 60_000;

/** Deterministic JSON stringify — sorted object keys at every level, so the SAME config always
 * produces the SAME string regardless of the order its fields happened to be set in (a form, a
 * spread, whatever built it). Array order is preserved: it is meaningful there. */
export function stableStringify(value: unknown): string {
  return JSON.stringify(sortKeysDeep(value));
}

function sortKeysDeep(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeysDeep);
  if (value !== null && typeof value === "object") {
    const source = value as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(source).sort()) {
      out[key] = sortKeysDeep(source[key]);
    }
    return out;
  }
  return value;
}

/** The TanStack Query cache key for one widget — **(id, resolved config)**, never the id alone
 * (WEB_DESIGN.md §7.6). */
export function widgetQueryKey(
  widget: Pick<ResolveWidgetRequest, "id" | "config">,
): readonly unknown[] {
  return ["widget", widget.id, stableStringify(widget.config ?? {})];
}

export interface ResolveDashboardOptions {
  readonly layoutId?: string | null;
  readonly context?: ResolveContext;
  /** The last sync version this client observed (a previous resolve's `syncVersion`, or
   * `GET /v1/sync`). Sent to the server only for a chunk whose every widget is already cached —
   * see the module docstring. */
  readonly knownSyncVersion?: number | null;
}

const UNCHANGED_WITHOUT_CACHE_ERROR = {
  code: "unchanged_without_cache",
  message: "This widget could not be refreshed. Reload the page to try again.",
  recoverable: true,
  field: null,
  requestId: null,
} as const;

function chunk<T>(items: readonly T[], size: number): T[][] {
  const out: T[][] = [];
  for (let index = 0; index < items.length; index += size) {
    out.push(items.slice(index, index + size));
  }
  return out;
}

function partitionClockDependent(widgets: readonly ResolveWidgetRequest[]): {
  readonly clockDependent: readonly ResolveWidgetRequest[];
  readonly rest: readonly ResolveWidgetRequest[];
} {
  const clockDependent: ResolveWidgetRequest[] = [];
  const rest: ResolveWidgetRequest[] = [];
  for (const widget of widgets) {
    (CLOCK_DEPENDENT_KINDS.has(widget.kind) ? clockDependent : rest).push(widget);
  }
  return { clockDependent, rest };
}

function cachedResult(
  queryClient: QueryClient,
  widget: ResolveWidgetRequest,
): ResolveResult | undefined {
  return queryClient.getQueryData<ResolveResult>(widgetQueryKey(widget));
}

async function postResolve(
  widgets: readonly ResolveWidgetRequest[],
  options: ResolveDashboardOptions,
  knownSyncVersion: number | null,
): Promise<DashboardResolveResponse> {
  const body: DashboardResolveRequest = {
    layoutId: options.layoutId ?? undefined,
    context: options.context,
    knownSyncVersion: knownSyncVersion ?? undefined,
    widgets,
  };
  return fetchJson<DashboardResolveResponse>("/dashboard/resolve", { method: "POST", body });
}

/**
 * Resolve every widget in `widgets`, writing each one's {@link ResolveResult} into
 * `queryClient`'s cache at {@link widgetQueryKey}. This is the only function in the app that may
 * call `POST /v1/dashboard/resolve` — see the module docstring for the traps it handles.
 */
export async function resolveDashboard(
  queryClient: QueryClient,
  widgets: readonly ResolveWidgetRequest[],
  options: ResolveDashboardOptions = {},
): Promise<void> {
  if (widgets.length === 0) return;
  const { clockDependent, rest } = partitionClockDependent(widgets);

  const chunks: (readonly ResolveWidgetRequest[])[] = [
    ...chunk(clockDependent, MAX_WIDGETS_PER_REQUEST),
    ...chunk(rest, MAX_WIDGETS_PER_REQUEST),
  ];

  await Promise.all(chunks.map((oneChunk) => resolveChunk(queryClient, oneChunk, options)));
}

async function resolveChunk(
  queryClient: QueryClient,
  widgets: readonly ResolveWidgetRequest[],
  options: ResolveDashboardOptions,
): Promise<void> {
  if (widgets.length === 0) return;

  // A chunk is homogeneous by construction (partitionClockDependent never mixes the two), so
  // checking the first widget is equivalent to checking every widget.
  const isClockDependent = CLOCK_DEPENDENT_KINDS.has(widgets[0].kind);
  const everyWidgetCached = widgets.every((widget) => cachedResult(queryClient, widget) !== undefined);
  const knownSyncVersion =
    !isClockDependent && everyWidgetCached ? (options.knownSyncVersion ?? null) : null;

  const response = await postResolve(widgets, options, knownSyncVersion);
  await applyResults(queryClient, widgets, response.results, options);
}

async function applyResults(
  queryClient: QueryClient,
  requested: readonly ResolveWidgetRequest[],
  results: readonly ResolveResult[],
  options: ResolveDashboardOptions,
): Promise<void> {
  const byId = new Map(requested.map((widget) => [widget.id, widget] as const));
  const retryNeeded: ResolveWidgetRequest[] = [];

  for (const result of results) {
    const widget = byId.get(result.widgetId);
    if (!widget) continue;

    if (result.status === "unchanged") {
      if (cachedResult(queryClient, widget) !== undefined) {
        // The client already holds this widget's payload and nothing has moved — keep it.
        continue;
      }
      retryNeeded.push(widget);
      continue;
    }
    queryClient.setQueryData(widgetQueryKey(widget), result);
  }

  if (retryNeeded.length > 0) {
    await retryUnchangedWithoutCache(queryClient, retryNeeded, options);
  }
}

/** One retry, alone, with `knownSyncVersion` omitted — never looping. A second `"unchanged"`
 * becomes an `"error"` result rather than an empty tile forever (WEB_DESIGN.md §7.6). */
async function retryUnchangedWithoutCache(
  queryClient: QueryClient,
  widgets: readonly ResolveWidgetRequest[],
  options: ResolveDashboardOptions,
): Promise<void> {
  const response = await postResolve(widgets, options, null);
  const byId = new Map(widgets.map((widget) => [widget.id, widget] as const));

  for (const result of response.results) {
    const widget = byId.get(result.widgetId);
    if (!widget) continue;

    if (result.status === "unchanged") {
      const errorResult: ResolveResult = {
        widgetId: widget.id,
        kind: widget.kind,
        status: "error",
        payload: null,
        error: UNCHANGED_WITHOUT_CACHE_ERROR,
        generatedAt: result.generatedAt,
        ttlSeconds: result.ttlSeconds,
        availability: null,
        notes: [],
      };
      queryClient.setQueryData(widgetQueryKey(widget), errorResult);
      continue;
    }
    queryClient.setQueryData(widgetQueryKey(widget), result);
  }
}

/** True once a cached result is old enough that `StalenessDot` should go hollow-warning and a
 * background refetch should run while the number keeps rendering (WEB_DESIGN.md §7.6). */
export function isResultStale(result: ResolveResult, now: Date = new Date()): boolean {
  if (!result.generatedAt || result.ttlSeconds === null) return false;
  const generatedAt = Date.parse(result.generatedAt);
  if (Number.isNaN(generatedAt)) return false;
  return now.getTime() > generatedAt + result.ttlSeconds * 1000;
}
