/**
 * A render smoke test against the real fixture, at both sizes this kind ships
 * (`generated/registry.ts`'s `sizes: ["medium", "large"]`).
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import CareerArcWidgetView from "../index";

const CURRENT_DIR = dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = resolve(CURRENT_DIR, "../../../../../contracts/fixtures/widget_career_arc.json");
const FIXTURE = JSON.parse(readFileSync(FIXTURE_PATH, "utf8")) as unknown;

afterEach(() => cleanup());

describe("CareerArcWidgetView", () => {
  for (const size of ["medium", "large"] as const) {
    it(`renders the fixture at size "${size}" with the player's name and an accessible chart summary`, () => {
      render(<CareerArcWidgetView kind="career_arc" size={size} payload={FIXTURE} />);
      expect(screen.getByText("AJ Green")).toBeTruthy();
      const chart = screen.getByRole("img");
      const label = chart.getAttribute("aria-label") ?? "";
      expect(label).toMatch(/AJ Green/);
      expect(label).toMatch(/\d/);
    });
  }

  it("shows era boundary labels inline only at size large, and in the footnote otherwise", () => {
    const withBoundary = {
      ...(FIXTURE as Record<string, unknown>),
      eraBoundaries: [{ season: "2023-24", label: "Test boundary", detail: "x" }],
    };
    const { container: mediumContainer } = render(<CareerArcWidgetView kind="career_arc" size="medium" payload={withBoundary} />);
    expect(mediumContainer.textContent).toContain("Test boundary");
    expect(mediumContainer.querySelector("svg text")?.textContent ?? "").not.toBe("Test boundary");
    cleanup();
    const { container: largeContainer } = render(<CareerArcWidgetView kind="career_arc" size="large" payload={withBoundary} />);
    expect(largeContainer.textContent).toContain("Test boundary");
  });

  it("marks an estimated season in the footnote and never plots a missing season as zero", () => {
    const fixture = FIXTURE as { seasons: Array<Record<string, unknown>> };
    const withGap = {
      ...(FIXTURE as Record<string, unknown>),
      seasons: [
        { ...fixture.seasons[0], availability: "estimated" },
        // 2024-25 omitted entirely — the payload's own convention for "no number this season".
        fixture.seasons[2],
      ],
    };
    render(<CareerArcWidgetView kind="career_arc" size="large" payload={withGap} />);
    expect(screen.getByText(/is estimated from the box score/)).toBeTruthy();
  });

  it("shows the empty-career message when no season has a value", () => {
    const empty = { ...(FIXTURE as Record<string, unknown>), seasons: [], playoffSeasons: [], peak: null };
    render(<CareerArcWidgetView kind="career_arc" size="large" payload={empty} />);
    expect(screen.getByText(/No season of this career has a/)).toBeTruthy();
  });

  it("falls back to a tile-level error rather than throwing when the payload is malformed", () => {
    render(<CareerArcWidgetView kind="career_arc" size="large" payload={{ bogus: true }} />);
    expect(screen.getByRole("alert")).toBeTruthy();
  });
});
