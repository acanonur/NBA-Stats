/**
 * Snapshot coverage for the remaining primitives not already exercised end-to-end by another
 * component's test file: `Card`, `StalenessDot`, and `AvailabilityExplainer`'s own rendered
 * content (as opposed to `AvailabilityBadge.test.tsx`, which only asserts that *some* dialog
 * opens).
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { Card } from "../Card";
import { StalenessDot } from "../StalenessDot";
import { AvailabilityExplainer } from "../AvailabilityExplainer";

afterEach(() => cleanup());

describe("Card", () => {
  it("renders its children inside the standard surface and matches its snapshot", () => {
    const { container, getByText } = render(<Card>Net Rating</Card>);
    expect(getByText("Net Rating")).toBeTruthy();
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("resolves the raised shadow token when raised", () => {
    const { container } = render(<Card raised>Raised</Card>);
    const card = container.firstElementChild as HTMLElement;
    expect(card.style.boxShadow).toBe("var(--hw-card-shadow-raised)");
  });

  it("resolves the ordinary shadow token by default", () => {
    const { container } = render(<Card>Plain</Card>);
    const card = container.firstElementChild as HTMLElement;
    expect(card.style.boxShadow).toBe("var(--hw-card-shadow)");
  });
});

describe("StalenessDot", () => {
  it("renders fresh (filled dot) and matches its snapshot", () => {
    const { container } = render(<StalenessDot isStale={false} updatedAt={new Date()} showsText />);
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("renders stale (hollow ring) with a warning-coloured label", () => {
    const { getByRole } = render(
      <StalenessDot isStale updatedAt={new Date(Date.now() - 2 * 60 * 60 * 1000)} showsText />,
    );
    expect(getByRole("status").getAttribute("aria-label")).toMatch(/^Showing cached data, last updated/);
  });

  it("degrades to a plain up-to-date/cached label with no timestamp", () => {
    const { getByRole } = render(<StalenessDot isStale={false} />);
    expect(getByRole("status").getAttribute("aria-label")).toBe("Up to date");
  });
});

describe("AvailabilityExplainer", () => {
  it("renders the headline, explanation, notes, and era timeline, and matches its snapshot", () => {
    const { container, getByText } = render(
      <AvailabilityExplainer
        availability="estimated"
        metricName="Usage %"
        season="1971-72"
        notes={["Individual turnovers were not recorded, so possessions ended are inferred."]}
        onClose={() => {}}
      />,
    );
    expect(getByText("Estimated, not measured")).toBeTruthy();
    expect(getByText("When the record changed")).toBeTruthy();
    expect(getByText("1996-97")).toBeTruthy();
    expect(container.innerHTML).toMatchSnapshot();
  });
});
