/**
 * A render smoke test against the real fixture, at both sizes this kind ships
 * (`generated/registry.ts`'s `sizes: ["medium", "large"]`).
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import TrendChartWidgetView from "../index";

const CURRENT_DIR = dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = resolve(CURRENT_DIR, "../../../../../contracts/fixtures/widget_trend_chart.json");
const FIXTURE = JSON.parse(readFileSync(FIXTURE_PATH, "utf8")) as unknown;

afterEach(() => cleanup());

describe("TrendChartWidgetView", () => {
  for (const size of ["medium", "large"] as const) {
    it(`renders the fixture at size "${size}" with the metric name and every series' label`, () => {
      render(<TrendChartWidgetView kind="trend_chart" size={size} payload={FIXTURE} />);
      expect(screen.getByText("True Shooting %")).toBeTruthy();
      expect(screen.getAllByText("AJ Green").length).toBeGreaterThan(0);
      expect(screen.getAllByText("Kobe Sanders").length).toBeGreaterThan(0);
      const chart = screen.getByRole("img");
      const label = chart.getAttribute("aria-label") ?? "";
      expect(label).toMatch(/True Shooting %/);
      expect(label).toMatch(/\d/);
    });
  }

  it("shows a rolling-average caption when a series carries one", () => {
    render(<TrendChartWidgetView kind="trend_chart" size="large" payload={FIXTURE} />);
    expect(screen.getByText("5-game rolling average")).toBeTruthy();
  });

  it("drops a point with an unparseable date and counts it in the footnote", () => {
    const fixture = FIXTURE as { series: Array<{ points: Array<Record<string, unknown>> }> };
    const withBadDate = {
      ...(FIXTURE as Record<string, unknown>),
      series: [
        { ...fixture.series[0], points: [{ ...fixture.series[0].points[0], x: "not-a-date" }, ...fixture.series[0].points.slice(1)] },
        fixture.series[1],
      ],
    };
    render(<TrendChartWidgetView kind="trend_chart" size="large" payload={withBadDate} />);
    expect(screen.getByText(/had no readable game date and are not plotted/)).toBeTruthy();
  });

  it("shows the empty-chart message when no series has any point", () => {
    const empty = { ...(FIXTURE as Record<string, unknown>), series: [] };
    render(<TrendChartWidgetView kind="trend_chart" size="large" payload={empty} />);
    expect(screen.getByText(/No games with a/)).toBeTruthy();
  });

  it("renders an em dash rather than a literal 0 for a missing latest value", () => {
    const fixture = FIXTURE as { series: Array<{ points: Array<Record<string, unknown>> }> };
    const lastIndex = fixture.series[0].points.length - 1;
    const withMissingLatest = {
      ...(FIXTURE as Record<string, unknown>),
      series: [
        { ...fixture.series[0], points: fixture.series[0].points.map((point, index) => (index === lastIndex ? { ...point, y: null } : point)) },
        fixture.series[1],
      ],
    };
    render(<TrendChartWidgetView kind="trend_chart" size="large" payload={withMissingLatest} />);
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("falls back to a tile-level error rather than throwing when the payload is malformed", () => {
    render(<TrendChartWidgetView kind="trend_chart" size="large" payload={{ bogus: true }} />);
    expect(screen.getByRole("alert")).toBeTruthy();
  });
});
