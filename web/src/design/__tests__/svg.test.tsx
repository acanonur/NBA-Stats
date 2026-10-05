/**
 * Snapshot + behavioural coverage for the hand-written SVG chart primitives in `design/svg/**`.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { splitRuns } from "../svg/RunPolyline";
import { resolveYDomain, normalizeInDomain } from "../svg/CartesianFrame";
import { Sparkline } from "../svg/Sparkline";
import { LineChart } from "../svg/LineChart";
import { GroupedBars } from "../svg/GroupedBars";
import { ScatterPlot } from "../svg/ScatterPlot";
import { RadarPath } from "../svg/RadarPath";

afterEach(() => cleanup());

describe("splitRuns", () => {
  it("breaks a run at every null, keeping isolated points as their own run", () => {
    const points = [
      { x: 0, y: 0.48 },
      { x: 1, y: 0.51 },
      { x: 2, y: null },
      { x: 3, y: null },
      { x: 4, y: 0.58 },
      { x: 5, y: 0.57 },
      { x: 6, y: null },
      { x: 7, y: 0.64 },
    ];
    const runs = splitRuns(points);
    expect(runs.map((run) => run.length)).toEqual([2, 2, 1]);
  });

  it("returns one run for an all-equal series (no divide-by-zero special case here)", () => {
    const points = [0, 1, 2, 3, 4].map((x) => ({ x, y: 0.55 }));
    expect(splitRuns(points)).toEqual([points]);
  });

  it("returns no runs for an all-null series", () => {
    expect(splitRuns([{ x: 0, y: null }, { x: 1, y: null }])).toEqual([]);
  });
});

describe("resolveYDomain", () => {
  it("prefers a server-published domain outright", () => {
    expect(resolveYDomain({ serverDomain: { min: 0, max: 50 }, values: [10, 20] })).toEqual([0, 50]);
  });

  it("pads the plotted values by 8% when there is no server domain", () => {
    const [lower, upper] = resolveYDomain({ values: [10, 20] });
    expect(lower).toBeLessThan(10);
    expect(upper).toBeGreaterThan(20);
  });

  it("falls back to the catalog domain when every value is null", () => {
    expect(resolveYDomain({ values: [null, null], catalogDomain: { min: 1, max: 2 } })).toEqual([1, 2]);
  });

  it("falls back to [0, 1] when there is nothing at all to go on", () => {
    expect(resolveYDomain({ values: [] })).toEqual([0, 1]);
  });
});

describe("normalizeInDomain", () => {
  it("clamps outside the domain rather than extrapolating", () => {
    expect(normalizeInDomain(-5, [0, 10])).toBe(0);
    expect(normalizeInDomain(15, [0, 10])).toBe(1);
    expect(normalizeInDomain(5, [0, 10])).toBe(0.5);
  });
});

describe("Sparkline", () => {
  it("renders a rising series and matches its snapshot", () => {
    const { container } = render(
      <Sparkline points={[0.48, 0.51, 0.49, 0.55, 0.58, 0.57, 0.62, 0.64]} tint="var(--hw-chart-0)" />,
    );
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("breaks the line at gaps rather than interpolating", () => {
    // [0.48, 0.51, null, null, 0.58] is two runs: a two-point run (a polyline) and an isolated
    // single point (a dot, not a line with nothing to connect to) — never one line drawn
    // straight through the gap.
    const { container } = render(<Sparkline points={[0.48, 0.51, null, null, 0.58]} tint="var(--hw-chart-1)" />);
    expect(container.querySelectorAll("polyline").length).toBe(1);
    expect(container.querySelectorAll("circle").length).toBeGreaterThan(0);
  });

  it("draws a flat centre line when every value is equal, without throwing", () => {
    const { container } = render(<Sparkline points={[0.55, 0.55, 0.55, 0.55]} tint="var(--hw-chart-2)" />);
    expect(container.querySelector("polyline")).toBeTruthy();
  });

  it("reports no data accessibly when every point is null", () => {
    const { getByRole } = render(<Sparkline points={[null, null, null]} tint="var(--hw-chart-3)" />);
    expect(getByRole("img").getAttribute("aria-label")).toBe("Sparkline, no data");
  });
});

describe("LineChart", () => {
  it("renders two series and matches its snapshot", () => {
    const { container } = render(
      <LineChart
        width={200}
        height={100}
        yDomain={[0, 40]}
        series={[
          { id: "raw", color: "var(--hw-chart-0)", points: [{ x: 0, y: 20 }, { x: 0.5, y: null }, { x: 1, y: 30 }] },
          { id: "rolling", color: "var(--hw-chart-1)", strokeWidth: 2.5, points: [{ x: 0, y: 22 }, { x: 0.5, y: 25 }, { x: 1, y: 28 }] },
        ]}
      />,
    );
    expect(container.innerHTML).toMatchSnapshot();
  });
});

describe("GroupedBars", () => {
  it("draws no bar for a null value rather than a zero-length one", () => {
    const { container } = render(
      <GroupedBars
        groups={[{ label: "Rim", values: [0.65, null] }]}
        seriesColors={["var(--hw-chart-0)", "var(--hw-chart-1)"]}
        domain={[0, 1]}
        width={200}
        height={40}
      />,
    );
    expect(container.querySelectorAll("rect").length).toBe(1);
  });
});

describe("ScatterPlot", () => {
  it("renders points and crosshairs and matches its snapshot", () => {
    const { container } = render(
      <ScatterPlot
        points={[{ id: "LAL", x: 118, y: -112 }, { id: "BOS", x: 121, y: -108 }]}
        xDomain={[100, 125]}
        yDomain={[-125, -100]}
        crosshair={{ x: 114, y: -114 }}
        width={200}
        height={200}
      />,
    );
    expect(container.innerHTML).toMatchSnapshot();
  });
});

describe("RadarPath", () => {
  const axes = [
    { key: "ts", label: "TS%" },
    { key: "ast", label: "AST%" },
    { key: "reb", label: "REB%" },
    { key: "stl", label: "STL%" },
  ];

  it("closes a fully-populated series into one loop and matches its snapshot", () => {
    const { container } = render(
      <RadarPath
        axes={axes}
        series={[{ id: "a", color: "var(--hw-chart-0)", values: [0.9, 0.6, 0.5, 0.7] }]}
        size={160}
      />,
    );
    expect(container.querySelectorAll("polygon").length).toBe(1);
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("breaks the outline at a missing vertex instead of collapsing it to the centre", () => {
    const { container } = render(
      <RadarPath
        axes={axes}
        series={[{ id: "a", color: "var(--hw-chart-0)", values: [0.9, null, 0.5, 0.7] }]}
        size={160}
      />,
    );
    // No fully-closed loop, so no filled polygon — only open polylines/dots for the two runs a
    // single gap produces.
    expect(container.querySelectorAll("polygon").length).toBe(0);
    expect(container.querySelectorAll("polyline").length).toBeGreaterThan(0);
  });
});
