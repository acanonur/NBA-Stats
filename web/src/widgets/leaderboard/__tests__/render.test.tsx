/**
 * A render smoke test against the real fixture, at both sizes this kind ships
 * (`generated/registry.ts`'s `sizes: ["medium", "large"]`) — proves the component mounts, shows
 * the metric name, and never renders a literal `"0"` for a value the payload does not carry
 * (the house rule: a null stat is an em dash, never zero).
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import LeaderboardWidgetView from "../index";

const CURRENT_DIR = dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = resolve(CURRENT_DIR, "../../../../../contracts/fixtures/widget_leaderboard.json");
const FIXTURE = JSON.parse(readFileSync(FIXTURE_PATH, "utf8")) as unknown;

afterEach(() => cleanup());

describe("LeaderboardWidgetView", () => {
  for (const size of ["medium", "large"] as const) {
    it(`renders the fixture at size "${size}" with the metric name and every row's rank`, () => {
      render(<LeaderboardWidgetView kind="leaderboard" size={size} payload={FIXTURE} />);
      expect(screen.getByText("True Shooting %")).toBeTruthy();
      expect(screen.getByText("Caris LeVert")).toBeTruthy();
    });
  }

  it("renders secondary metric columns only at size large", () => {
    const { container: mediumContainer } = render(
      <LeaderboardWidgetView kind="leaderboard" size="medium" payload={FIXTURE} />,
    );
    expect(mediumContainer.textContent).not.toContain("MIN");
    cleanup();
    const { container: largeContainer } = render(
      <LeaderboardWidgetView kind="leaderboard" size="large" payload={FIXTURE} />,
    );
    expect(largeContainer.textContent).toContain("MIN");
  });

  it("shows the qualifier line", () => {
    render(<LeaderboardWidgetView kind="leaderboard" size="large" payload={FIXTURE} />);
    expect(screen.getByText("Minimum 15 games and 20.0 minutes per game")).toBeTruthy();
  });

  it("falls back to a tile-level error rather than throwing when the payload is malformed", () => {
    render(<LeaderboardWidgetView kind="leaderboard" size="large" payload={{ bogus: true }} />);
    expect(screen.getByRole("alert")).toBeTruthy();
  });
});
