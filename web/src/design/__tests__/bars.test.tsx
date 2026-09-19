/**
 * Snapshot coverage for the bar primitives: `PercentileBar`, `FactorBar`, `RangeBar`, and
 * `ProjectionIntervalBar`.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { PercentileBar } from "../PercentileBar";
import { FactorBar } from "../FactorBar";
import { RangeBar, isRangeBarDrawable, rangeBarAxis, rangeBarPosition } from "../RangeBar";
import { ProjectionIntervalBar } from "../ProjectionIntervalBar";

afterEach(() => cleanup());

describe("PercentileBar", () => {
  it("renders a percentile and matches its snapshot", () => {
    const { container } = render(<PercentileBar percentile={0.93} tint="var(--hw-chart-0)" />);
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("accepts an already-scaled 0-100 value without pegging to the top", () => {
    const { getByRole } = render(<PercentileBar percentile={72} tint="var(--hw-chart-2)" />);
    expect(getByRole("img").getAttribute("aria-label")).toBe("72nd percentile");
  });

  it("draws an empty marker-less track for null", () => {
    const { getByRole } = render(<PercentileBar percentile={null} tint="var(--hw-chart-2)" />);
    expect(getByRole("img").getAttribute("aria-label")).toBe("Percentile not available");
  });
});

describe("FactorBar", () => {
  it("renders a value inside its domain and matches its snapshot", () => {
    const { container } = render(
      <FactorBar
        value={0.556}
        leagueAverage={0.538}
        domain={[0.45, 0.62]}
        tint="var(--hw-chart-0)"
        label="eFG%"
        valueText="55.6%"
      />,
    );
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("draws a dashed empty track for a null value, never a zero-length bar", () => {
    const { container } = render(
      <FactorBar value={null} domain={[0, 1]} tint="var(--hw-chart-0)" label="FT Rate" />,
    );
    expect(container.querySelector('[class*="trackEmpty"]')).toBeTruthy();
    expect(container.querySelector('[class*="fill"]')).toBeNull();
  });
});

describe("RangeBar geometry", () => {
  const model = { low: 19, high: 38, projection: 28.4, reference: 27.1 };

  it("is drawable when every value is finite and high > low", () => {
    expect(isRangeBarDrawable(model)).toBe(true);
    expect(isRangeBarDrawable({ low: 5, high: 5, projection: 5 })).toBe(false);
  });

  it("widens the axis to include a reference mark that falls outside the band", () => {
    const widened = { low: 19, high: 38, projection: 28.4, reference: 14.0 };
    const [lower] = rangeBarAxis(widened);
    expect(lower).toBeLessThan(14.0);
  });

  it("positions low/high/projection within [0, 1]", () => {
    for (const value of [model.low, model.high, model.projection]) {
      const position = rangeBarPosition(model, value);
      expect(position).toBeGreaterThanOrEqual(0);
      expect(position).toBeLessThanOrEqual(1);
    }
  });

  it("renders and matches its snapshot", () => {
    const { container } = render(<RangeBar model={model} accent="var(--hw-bs-accent-above)" />);
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("draws only the baseline when not drawable", () => {
    const { container } = render(
      <RangeBar model={{ low: 5, high: 5, projection: 5 }} accent="var(--hw-bs-accent-above)" />,
    );
    expect(container.querySelector('[class*="band"]')).toBeNull();
    expect(container.querySelector('[class*="baseline"]')).toBeTruthy();
  });
});

describe("ProjectionIntervalBar", () => {
  it("names the reference mark in the middle label rather than leaving a bare number", () => {
    const { getByText } = render(
      <ProjectionIntervalBar
        model={{ low: 19, high: 38, projection: 28.4, reference: 27.1 }}
        accent="var(--hw-bs-accent-above)"
        lowText="19"
        highText="38"
        referenceLabel="season avg"
        referenceText="27.1"
        projectionText="28.4"
      />,
    );
    expect(getByText("season avg 27.1 · proj 28.4")).toBeTruthy();
  });

  it("falls back to a bare projection label when there is no reference", () => {
    const { getByText } = render(
      <ProjectionIntervalBar
        model={{ low: 5, high: 13, projection: 8.9 }}
        accent="var(--hw-text-tertiary)"
        lowText="5"
        highText="13"
        projectionText="8.9"
      />,
    );
    expect(getByText("proj 8.9")).toBeTruthy();
  });
});
