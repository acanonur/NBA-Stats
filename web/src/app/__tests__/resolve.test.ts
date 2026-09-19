/**
 * The four contract traps `resolve.ts` exists to hold in one place (WEB_DESIGN.md §7.6):
 * chunking at 24 widgets, the (id, config) cache key, the clock-dependent split, and the
 * unchanged-without-cache retry.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";
// This test mocks the client boundary itself to observe exactly what resolve.ts sends it;
// production code never imports api/client.ts outside src/api/*.
// eslint-disable-next-line no-restricted-imports
import { fetchJson } from "../../api/client";
import {
  CLOCK_DEPENDENT_KINDS,
  MAX_WIDGETS_PER_REQUEST,
  resolveDashboard,
  widgetQueryKey,
} from "../../api/resolve";
import type {
  DashboardResolveResponse,
  ResolveResult,
  ResolveWidgetRequest,
} from "../../api/types";

vi.mock("../../api/client", () => ({ fetchJson: vi.fn() }));

const mockedFetchJson = vi.mocked(fetchJson);

afterEach(() => {
  mockedFetchJson.mockReset();
});

function widget(id: string, overrides: Partial<ResolveWidgetRequest> = {}): ResolveWidgetRequest {
  return { id, kind: "stat_tile", size: "small", config: {}, ...overrides };
}

function okResult(widgetRequest: ResolveWidgetRequest): ResolveResult {
  return {
    widgetId: widgetRequest.id,
    kind: widgetRequest.kind,
    status: "ok",
    payload: { fixture: widgetRequest.id } as never,
    error: null,
    generatedAt: new Date().toISOString(),
    ttlSeconds: 300,
    availability: "full",
    notes: [],
  };
}

function response(results: readonly ResolveResult[]): DashboardResolveResponse {
  return {
    syncVersion: 5,
    dataThrough: null,
    generatedAt: new Date().toISOString(),
    resolvedContext: { favoritePlayerId: null, favoriteTeamId: null, season: null },
    results,
  };
}

describe("resolveDashboard: chunking", () => {
  it("splits a layout of 30 widgets into requests of at most 24", async () => {
    const widgets = Array.from({ length: 30 }, (_, index) => widget(`w${index}`));
    mockedFetchJson.mockImplementation((_path, options) => {
      const body = options?.body as { widgets: ResolveWidgetRequest[] };
      return Promise.resolve(response(body.widgets.map(okResult)));
    });

    const queryClient = new QueryClient();
    await resolveDashboard(queryClient, widgets);

    expect(mockedFetchJson).toHaveBeenCalledTimes(2);
    const sizes = mockedFetchJson.mock.calls
      .map((call) => (call[1]?.body as { widgets: unknown[] }).widgets.length)
      .sort((a, b) => b - a);
    expect(sizes).toEqual([24, 6]);
    expect(sizes.reduce((a, b) => a + b, 0)).toBe(30);
    expect(MAX_WIDGETS_PER_REQUEST).toBe(24);
  });
});

describe("resolveDashboard: cache keyed by (id, resolved config)", () => {
  it("does not reuse a cached payload when the same id is requested with a different config", async () => {
    mockedFetchJson.mockImplementation((_path, options) => {
      const body = options?.body as { widgets: ResolveWidgetRequest[] };
      return Promise.resolve(response(body.widgets.map(okResult)));
    });
    const queryClient = new QueryClient();

    const first = widget("tile-1", { config: { metric: "pts" } });
    await resolveDashboard(queryClient, [first]);
    expect(mockedFetchJson).toHaveBeenCalledTimes(1);

    const reconfigured = widget("tile-1", { config: { metric: "reb" } });
    await resolveDashboard(queryClient, [reconfigured]);

    // A second network call was made — the reconfigured tile was not served from the old cache
    // entry — and both entries still exist, keyed separately.
    expect(mockedFetchJson).toHaveBeenCalledTimes(2);
    expect(widgetQueryKey(first)).not.toEqual(widgetQueryKey(reconfigured));
    expect(queryClient.getQueryData(widgetQueryKey(first))).toBeDefined();
    expect(queryClient.getQueryData(widgetQueryKey(reconfigured))).toBeDefined();
  });

  it("builds the same key regardless of the config object's own key order", () => {
    const a = widget("tile-1", { config: { metric: "pts", season: "latest" } });
    const b = widget("tile-1", { config: { season: "latest", metric: "pts" } });
    expect(widgetQueryKey(a)).toEqual(widgetQueryKey(b));
  });
});

describe("resolveDashboard: the clock-dependent split", () => {
  it("never sends scoreboard or daily_movers alongside other widgets, and never with knownSyncVersion", async () => {
    expect(CLOCK_DEPENDENT_KINDS.has("scoreboard")).toBe(true);
    expect(CLOCK_DEPENDENT_KINDS.has("daily_movers")).toBe(true);

    const scoreboard = widget("scoreboard-1", { kind: "scoreboard" });
    const statTile = widget("tile-1");

    // Pre-cache the stat tile so its chunk is eligible to carry knownSyncVersion.
    const queryClient = new QueryClient();
    queryClient.setQueryData(widgetQueryKey(statTile), okResult(statTile));

    mockedFetchJson.mockImplementation((_path, options) => {
      const body = options?.body as { widgets: ResolveWidgetRequest[] };
      return Promise.resolve(response(body.widgets.map(okResult)));
    });

    await resolveDashboard(queryClient, [scoreboard, statTile], { knownSyncVersion: 42 });

    expect(mockedFetchJson).toHaveBeenCalledTimes(2);
    const bodies = mockedFetchJson.mock.calls.map(
      (call) => call[1]?.body as { widgets: ResolveWidgetRequest[]; knownSyncVersion?: number },
    );

    const scoreboardCall = bodies.find((b) => b.widgets.some((w) => w.kind === "scoreboard"));
    const statTileCall = bodies.find((b) => b.widgets.every((w) => w.kind !== "scoreboard"));

    expect(scoreboardCall?.widgets).toHaveLength(1);
    expect(scoreboardCall?.knownSyncVersion).toBeUndefined();
    expect(statTileCall?.widgets).toHaveLength(1);
    expect(statTileCall?.knownSyncVersion).toBe(42);
  });
});

describe("resolveDashboard: unchanged without a cache", () => {
  it("retries once, alone, with knownSyncVersion omitted, then keeps the payload on success", async () => {
    const target = widget("tile-1");
    const queryClient = new QueryClient();

    mockedFetchJson
      .mockResolvedValueOnce(
        response([
          {
            widgetId: target.id,
            kind: target.kind,
            status: "unchanged",
            payload: null,
            error: null,
            generatedAt: new Date().toISOString(),
            ttlSeconds: 300,
            availability: null,
            notes: [],
          },
        ]),
      )
      .mockResolvedValueOnce(response([okResult(target)]));

    await resolveDashboard(queryClient, [target], { knownSyncVersion: 7 });

    expect(mockedFetchJson).toHaveBeenCalledTimes(2);
    const retryBody = mockedFetchJson.mock.calls[1][1]?.body as {
      widgets: ResolveWidgetRequest[];
      knownSyncVersion?: number;
    };
    expect(retryBody.widgets).toEqual([target]);
    expect(retryBody.knownSyncVersion).toBeUndefined();

    const cached = queryClient.getQueryData<ResolveResult>(widgetQueryKey(target));
    expect(cached?.status).toBe("ok");
  });

  it("surfaces an error rather than an empty tile when the retry is also unchanged", async () => {
    const target = widget("tile-1");
    const queryClient = new QueryClient();
    const unchanged = (): DashboardResolveResponse =>
      response([
        {
          widgetId: target.id,
          kind: target.kind,
          status: "unchanged",
          payload: null,
          error: null,
          generatedAt: new Date().toISOString(),
          ttlSeconds: 300,
          availability: null,
          notes: [],
        },
      ]);
    mockedFetchJson.mockResolvedValueOnce(unchanged()).mockResolvedValueOnce(unchanged());

    await resolveDashboard(queryClient, [target]);

    expect(mockedFetchJson).toHaveBeenCalledTimes(2);
    const cached = queryClient.getQueryData<ResolveResult>(widgetQueryKey(target));
    expect(cached?.status).toBe("error");
    expect(cached?.error?.code).toBe("unchanged_without_cache");
  });
});
