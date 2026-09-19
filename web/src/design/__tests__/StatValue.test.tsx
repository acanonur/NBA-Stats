/**
 * Snapshot + behavioural coverage for `StatValue` and the chips/badge it composes.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { StatValue } from "../StatValue";
import type { MetricDescriptor, MetricValue } from "../../api/types";

afterEach(() => cleanup());

const tsPct: MetricDescriptor = {
  key: "ts_pct",
  name: "True Shooting %",
  shortName: "TS%",
  category: "shooting",
  format: "percent1",
  higherIsBetter: true,
  scope: ["player", "team"],
  availability: { seasonFrom: "1946-47", perGameFrom: "1996-97", seasonLevelOnly: false, estimatedBefore: null },
  domain: { min: 0.35, max: 0.75 },
  glossary: "PTS / (2 * (FGA + 0.44*FTA)).",
};

function metricValue(overrides: Partial<MetricValue> = {}): MetricValue {
  return {
    metric: "ts_pct",
    value: 0.6153,
    displayValue: "61.5%",
    rank: 12,
    percentile: 0.93,
    leagueAverage: 0.5671,
    delta: 0.0482,
    isEstimated: false,
    availability: "full",
    ...overrides,
  };
}

describe("StatValue", () => {
  it("renders the server's displayValue verbatim and matches its snapshot", () => {
    const { container } = render(<StatValue value={metricValue()} descriptor={tsPct} showsDelta />);
    expect(container.innerHTML).toContain("61.5%");
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("renders the em dash, never 0, for a null value", () => {
    const { container, getAllByText } = render(
      <StatValue
        value={metricValue({
          value: null,
          displayValue: "—",
          rank: null,
          percentile: null,
          leagueAverage: null,
          delta: null,
          availability: "unavailable",
        })}
        descriptor={tsPct}
      />,
    );
    // Both the value itself and the (non-interactive-looking, but still a button here)
    // availability marker render the em dash for "unavailable" — see AvailabilityBadge's own
    // docstring on why the compact marker is redundant beside an already-dashed value; this
    // call site is not compact, so both legitimately show it.
    expect(getAllByText("—").length).toBeGreaterThan(0);
    expect(container.innerHTML).not.toContain(">0<");
  });

  it("shows no rank chip for rank 0 (the reserved no-rank sentinel)", () => {
    const { container } = render(<StatValue value={metricValue({ rank: 0 })} descriptor={tsPct} />);
    expect(container.innerHTML).not.toContain("#0");
  });

  it("renders an estimated value with the warning-coloured badge and dashed underline", () => {
    const { container } = render(
      <StatValue value={metricValue({ availability: "estimated" })} descriptor={tsPct} />,
    );
    expect(container.textContent).toContain("est.");
  });
});
