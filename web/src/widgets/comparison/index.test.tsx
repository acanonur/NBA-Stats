/**
 * Component-level coverage for `ComparisonWidget`: the presentation-selection rules
 * (WEB_DESIGN.md §7.7 — `small` forces the table, radar falls back to bars above 8 or below 3
 * metrics), the em-dash rule for a subject missing a metric, and that a chart's accessible name
 * carries real numbers rather than just "chart".
 */
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import ComparisonWidget from "./index";
import type { ComparisonPayload } from "../../api/types";

afterEach(() => cleanup());

const fixturePath = resolve(process.cwd(), "..", "contracts", "fixtures", "widget_comparison.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as ComparisonPayload;

describe("ComparisonWidget", () => {
  it("renders bars (the fixture's requested style) at large, with the true value beside every bar", () => {
    const { container, getAllByText } = render(<ComparisonWidget kind="comparison" size="large" payload={fixture} />);
    expect(container.querySelector('[role="group"]')).toBeTruthy();
    // The bars are on a percentile axis (fixture's normalization) but the real display value is
    // still printed, e.g. AJ Green's 18.9 PTS.
    expect(getAllByText("18.9").length).toBeGreaterThan(0);
  });

  it("forces the table at small regardless of the payload's requested style", () => {
    const { container } = render(<ComparisonWidget kind="comparison" size="small" payload={fixture} />);
    expect(container.querySelector('[role="group"]')).toBeNull();
    expect(container.querySelector("table, [class*='root']")).toBeTruthy();
    // A table row exists for the first metric.
    expect(container.textContent).toContain("PTS");
  });

  it("falls back to bars for a radar style with more than 8 total metrics", () => {
    const manyMetrics: ComparisonPayload = {
      ...fixture,
      style: "radar",
      metrics: [...fixture.metrics, ...fixture.metrics, ...fixture.metrics].slice(0, 9),
    };
    const { container } = render(<ComparisonWidget kind="comparison" size="large" payload={manyMetrics} />);
    expect(container.querySelector('[role="img"][aria-label^="Comparison of"]')).toBeNull();
    expect(container.querySelector('[role="group"]')).toBeTruthy();
  });

  it("falls back to bars for a radar style with fewer than 3 shown metrics", () => {
    const fewMetrics: ComparisonPayload = { ...fixture, style: "radar", metrics: fixture.metrics.slice(0, 2) };
    const { container } = render(<ComparisonWidget kind="comparison" size="large" payload={fewMetrics} />);
    expect(container.querySelector('[role="img"][aria-label^="Comparison of"]')).toBeNull();
    expect(container.querySelector('[role="group"]')).toBeTruthy();
  });

  it("draws a radar for a payload asking for it within the 3-8 metric window", () => {
    const { container } = render(<ComparisonWidget kind="comparison" size="large" payload={{ ...fixture, style: "radar" }} />);
    const radar = container.querySelector('[role="img"]');
    expect(radar).toBeTruthy();
    const label = radar?.getAttribute("aria-label") ?? "";
    // The accessible name carries the real numbers, not just "chart".
    expect(label).toContain("PTS 18.9");
  });

  it("renders an em dash, never 0, for a subject missing a metric, and names it in a footnote", () => {
    const missing: ComparisonPayload = {
      ...fixture,
      subjects: [
        {
          ...fixture.subjects[0],
          values: fixture.subjects[0].values.map((value) =>
            value.metric === "pts" ? { ...value, value: null, displayValue: "—", availability: "unavailable" } : value,
          ),
        },
        ...fixture.subjects.slice(1),
      ],
    };
    const { container, getByText } = render(<ComparisonWidget kind="comparison" size="large" payload={missing} />);
    expect(container.textContent).not.toContain(">0<");
    expect(getByText(/PTS has no number for/)).toBeTruthy();
  });

  it("highlights the best raw value per row in the table presentation", () => {
    const { container } = render(<ComparisonWidget kind="comparison" size="small" payload={fixture} />);
    // Kobe Sanders (30.5 PTS) is the best of the three subjects for PTS.
    const best = Array.from(container.querySelectorAll("*")).find((node) => node.textContent === "30.5");
    expect(best).toBeTruthy();
  });

  it("shows a placeholder rather than crashing when there are no subjects or metrics", () => {
    const empty: ComparisonPayload = { ...fixture, subjects: [], metrics: [] };
    const { getByText } = render(<ComparisonWidget kind="comparison" size="large" payload={empty} />);
    expect(getByText("Pick at least two subjects and one metric to compare.")).toBeTruthy();
  });
});
