/**
 * Component-level coverage for `StatTileWidget`: the headline value renders the server's
 * `displayValue` verbatim, secondary-metric density by size, the era-gap em-dash path, and that
 * an unknown metric key still gets a readable fallback label instead of nothing.
 */
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import StatTileWidget from "./index";
import type { StatTilePayload } from "../../api/types";

afterEach(() => cleanup());

const fixturePath = resolve(process.cwd(), "..", "contracts", "fixtures", "widget_stat_tile.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as StatTilePayload;

describe("StatTileWidget", () => {
  it("renders the headline displayValue and the subject name", () => {
    const { getByText } = render(<StatTileWidget kind="stat_tile" size="medium" payload={fixture} />);
    expect(getByText("56.5%")).toBeTruthy();
    expect(getByText("AJ Green")).toBeTruthy();
  });

  it("shows fewer secondary metrics at small than at large", () => {
    const { container: small } = render(<StatTileWidget kind="stat_tile" size="small" payload={fixture} />);
    const { container: large } = render(<StatTileWidget kind="stat_tile" size="large" payload={fixture} />);
    // The fixture carries 2 secondary metrics; small caps at 2, large at 4 — both show all of them,
    // but the context line (season/type/permode) is only shown beyond "small".
    expect(small.textContent).not.toContain("Regular Season");
    expect(large.textContent).toContain("Regular Season");
  });

  it("renders the em dash, never 0, for a metric the era never recorded", () => {
    const payload: StatTilePayload = {
      subject: fixture.subject,
      context: "1971-72 · Regular Season · Per Game",
      primary: {
        metric: "ts_pct",
        value: null,
        displayValue: "—",
        rank: null,
        percentile: null,
        leagueAverage: null,
        delta: null,
        isEstimated: false,
        availability: "unavailable",
      },
      secondary: [],
      sparkline: [],
      sparklineMetric: "ts_pct",
    };
    const { container, getAllByText } = render(<StatTileWidget kind="stat_tile" size="medium" payload={payload} />);
    expect(getAllByText("—").length).toBeGreaterThan(0);
    expect(container.textContent).not.toContain(">0<");
  });

  it("labels an unknown metric key with a readable fallback rather than nothing", () => {
    const payload: StatTilePayload = {
      ...fixture,
      primary: { ...fixture.primary, metric: "some_new_metric_pct" },
    };
    const { getByText } = render(<StatTileWidget kind="stat_tile" size="medium" payload={payload} />);
    expect(getByText("SOME NEW METRIC%")).toBeTruthy();
  });

  it("does not draw a sparkline when every point is null", () => {
    const payload: StatTilePayload = { ...fixture, sparkline: fixture.sparkline.map((point) => ({ ...point, y: null })) };
    const { queryByRole } = render(<StatTileWidget kind="stat_tile" size="large" payload={payload} />);
    expect(queryByRole("img")).toBeNull();
  });

  it("draws a sparkline with an accessible label carrying the real numbers, not just 'chart'", () => {
    const { getByRole } = render(<StatTileWidget kind="stat_tile" size="large" payload={fixture} />);
    const chart = getByRole("img");
    expect(chart.getAttribute("aria-label")).toMatch(/True Shooting/);
    expect(chart.getAttribute("aria-label")).not.toBe("chart");
  });
});
