/**
 * A render smoke test against the real fixture, at both sizes this kind ships
 * (`generated/registry.ts`'s `sizes: ["medium", "large"]`).
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import TeamEfficiencyWidgetView from "../index";

const CURRENT_DIR = dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = resolve(CURRENT_DIR, "../../../../../contracts/fixtures/widget_team_efficiency.json");
const FIXTURE = JSON.parse(readFileSync(FIXTURE_PATH, "utf8")) as unknown;

afterEach(() => cleanup());

describe("TeamEfficiencyWidgetView", () => {
  for (const size of ["medium", "large"] as const) {
    it(`renders the fixture at size "${size}" as the league table`, () => {
      render(<TeamEfficiencyWidgetView kind="team_efficiency" size={size} payload={FIXTURE} />);
      expect(screen.getByText("League table")).toBeTruthy();
      expect(screen.getAllByText("HOU").length).toBeGreaterThan(0);
    });
  }

  it("shows more rows at large than at medium", () => {
    const { container: mediumContainer } = render(<TeamEfficiencyWidgetView kind="team_efficiency" size="medium" payload={FIXTURE} />);
    const mediumRows = mediumContainer.querySelectorAll('[role="group"]').length;
    cleanup();
    const { container: largeContainer } = render(<TeamEfficiencyWidgetView kind="team_efficiency" size="large" payload={FIXTURE} />);
    const largeRows = largeContainer.querySelectorAll('[role="group"]').length;
    expect(largeRows).toBeGreaterThan(mediumRows);
  });

  it("shows the win-loss record only at size large", () => {
    const { container: mediumContainer } = render(<TeamEfficiencyWidgetView kind="team_efficiency" size="medium" payload={FIXTURE} />);
    expect(mediumContainer.textContent).not.toContain("20-5");
    cleanup();
    const { container: largeContainer } = render(<TeamEfficiencyWidgetView kind="team_efficiency" size="large" payload={FIXTURE} />);
    expect(largeContainer.textContent).toContain("20-5");
  });

  it("renders the scatter style with the inversion caption and the accessible chart summary", () => {
    const scatterFixture = { ...(FIXTURE as Record<string, unknown>), style: "scatter" };
    render(<TeamEfficiencyWidgetView kind="team_efficiency" size="large" payload={scatterFixture} />);
    expect(screen.getByText(/Defensive rating is inverted/)).toBeTruthy();
    const chart = screen.getByRole("img");
    expect(chart.getAttribute("aria-label")).toMatch(/offensive rating/i);
    expect(chart.getAttribute("aria-label")).toMatch(/\d/);
  });

  it("renders an em dash rather than a literal 0 for an unranked row", () => {
    const fixture = FIXTURE as { rows: Array<Record<string, unknown>> };
    const withUnranked = { ...(FIXTURE as Record<string, unknown>), rows: [{ ...fixture.rows[0], rank: 0 }, ...fixture.rows.slice(1)] };
    render(<TeamEfficiencyWidgetView kind="team_efficiency" size="large" payload={withUnranked} />);
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("falls back to a tile-level error rather than throwing when the payload is malformed", () => {
    render(<TeamEfficiencyWidgetView kind="team_efficiency" size="large" payload={{ bogus: true }} />);
    expect(screen.getByRole("alert")).toBeTruthy();
  });
});
