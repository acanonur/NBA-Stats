/**
 * A render smoke test against the real fixture, at both sizes this kind ships
 * (`generated/registry.ts`'s `sizes: ["medium", "large"]`).
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import FourFactorsWidgetView from "../index";

const CURRENT_DIR = dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = resolve(CURRENT_DIR, "../../../../../contracts/fixtures/widget_four_factors.json");
const FIXTURE = JSON.parse(readFileSync(FIXTURE_PATH, "utf8")) as unknown;

afterEach(() => cleanup());

describe("FourFactorsWidgetView", () => {
  for (const size of ["medium", "large"] as const) {
    it(`renders the fixture at size "${size}" with the team name`, () => {
      render(<FourFactorsWidgetView kind="four_factors" size={size} payload={FIXTURE} />);
      expect(screen.getByText("Denver Nuggets")).toBeTruthy();
      expect(screen.getByText("eFG%")).toBeTruthy();
    });
  }

  it("shows the defensive mirror only at size large", () => {
    const { container: mediumContainer } = render(<FourFactorsWidgetView kind="four_factors" size="medium" payload={FIXTURE} />);
    expect(mediumContainer.textContent).not.toContain("Opp eFG%");
    cleanup();
    const { container: largeContainer } = render(<FourFactorsWidgetView kind="four_factors" size="large" payload={FIXTURE} />);
    expect(largeContainer.textContent).toContain("Opp eFG%");
  });

  it("marks turnover rate as lower-is-better on offence", () => {
    render(<FourFactorsWidgetView kind="four_factors" size="large" payload={FIXTURE} />);
    expect(screen.getAllByText("lower is better").length).toBeGreaterThan(0);
  });

  it("renders no literal 0 for a null factor value", () => {
    const fixture = FIXTURE as { offense: Array<Record<string, unknown>> };
    const withNull = {
      ...(FIXTURE as Record<string, unknown>),
      offense: [
        { ...fixture.offense[0], value: null, leagueAverage: null, percentile: null, rank: null, displayValue: "—" },
        ...fixture.offense.slice(1),
      ],
    };
    render(<FourFactorsWidgetView kind="four_factors" size="large" payload={withNull} />);
    expect(screen.queryByText("0")).toBeNull();
    expect(screen.getAllByText(/—/).length).toBeGreaterThan(0);
  });

  it("falls back to a tile-level error rather than throwing when the payload is malformed", () => {
    render(<FourFactorsWidgetView kind="four_factors" size="large" payload={{ bogus: true }} />);
    expect(screen.getByRole("alert")).toBeTruthy();
  });
});
