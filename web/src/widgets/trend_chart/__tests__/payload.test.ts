/**
 * `decodeTrendChartPayload` against the real, checked-in fixture — see
 * `widgets/leaderboard/__tests__/payload.test.ts`'s docstring for what this proves and why.
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { decodeTrendChartPayload } from "../payload";

const CURRENT_DIR = dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = resolve(CURRENT_DIR, "../../../../../contracts/fixtures/widget_trend_chart.json");

function loadFixture(): Record<string, unknown> {
  return JSON.parse(readFileSync(FIXTURE_PATH, "utf8")) as Record<string, unknown>;
}

describe("decodeTrendChartPayload", () => {
  it("decodes the real widget_trend_chart.json fixture with no data loss", () => {
    const raw = loadFixture();
    const decoded = decodeTrendChartPayload(raw);
    expect(decoded).toEqual(raw);
  });

  it("decodes two series with every point", () => {
    const decoded = decodeTrendChartPayload(loadFixture());
    expect(decoded.series).toHaveLength(2);
    expect(decoded.series[0]?.points.length).toBeGreaterThan(0);
    expect(decoded.series[0]?.label).toBe("AJ Green");
  });

  it("rejects a payload missing a required top-level field", () => {
    const raw = loadFixture();
    delete raw.series;
    expect(() => decodeTrendChartPayload(raw)).toThrow();
  });

  it("rejects a payload missing a required nested field on a point", () => {
    const raw = loadFixture();
    const series = raw.series as Array<Record<string, unknown>>;
    const points = series[0].points as Array<Record<string, unknown>>;
    delete points[0].gameId;
    expect(() => decodeTrendChartPayload(raw)).toThrow();
  });

  it("rejects an unrecognised top-level field — the anti-drift guarantee", () => {
    const raw = loadFixture();
    raw.newServerField = "surprise";
    expect(() => decodeTrendChartPayload(raw)).toThrow(/unknown field "newServerField"/);
  });

  it("rejects an unrecognised field on a point", () => {
    const raw = loadFixture();
    const series = raw.series as Array<Record<string, unknown>>;
    const points = series[0].points as Array<Record<string, unknown>>;
    points[0] = { ...points[0], seasonType: "Regular Season" };
    expect(() => decodeTrendChartPayload(raw)).toThrow(/unknown field "seasonType"/);
  });

  it("rejects an unrecognised field on the metric descriptor", () => {
    const raw = loadFixture();
    (raw.metric as Record<string, unknown>).isEstimated = false;
    expect(() => decodeTrendChartPayload(raw)).toThrow(/unknown field "isEstimated"/);
  });

  it("accepts a null y, rolling, opponentAbbr, leagueAverage and yDomain", () => {
    const raw = loadFixture();
    const series = raw.series as Array<Record<string, unknown>>;
    const points = series[0].points as Array<Record<string, unknown>>;
    points[0] = { ...points[0], y: null, rolling: null, opponentAbbr: null };
    const withNulls = { ...raw, leagueAverage: null, yDomain: null };
    const decoded = decodeTrendChartPayload(withNulls);
    expect(decoded.series[0]?.points[0]?.y).toBeNull();
    expect(decoded.series[0]?.points[0]?.rolling).toBeNull();
    expect(decoded.leagueAverage).toBeNull();
    expect(decoded.yDomain).toBeNull();
  });

  it("accepts an empty series list", () => {
    const raw = { ...loadFixture(), series: [] };
    const decoded = decodeTrendChartPayload(raw);
    expect(decoded.series).toEqual([]);
  });
});
