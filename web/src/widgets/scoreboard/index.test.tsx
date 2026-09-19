/**
 * Component-level coverage for `ScoreboardWidget`: the em-dash rule for an unscored game, size
 * density (`small` shows 2 games and no performers, `large` shows performers), and the empty
 * slate state.
 */
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import ScoreboardWidget from "./index";
import type { ScoreboardPayload } from "../../api/types";

afterEach(() => cleanup());

const fixturePath = resolve(process.cwd(), "..", "contracts", "fixtures", "widget_scoreboard.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as ScoreboardPayload;

describe("ScoreboardWidget", () => {
  it("shows every game at large, including top performers", () => {
    const { container } = render(<ScoreboardWidget kind="scoreboard" size="large" payload={fixture} />);
    // The performer line is abbreviated to "F. Last" (no computed shortName in api/types.ts).
    expect(container.textContent).toContain("O. Toppin");
    expect(container.textContent).toContain("HOU");
    expect(container.textContent).toContain("Rockets");
    expect(container.textContent).toContain("Final");
  });

  it("truncates to 2 games and hides team names (but not badges) at small", () => {
    const { container } = render(<ScoreboardWidget kind="scoreboard" size="small" payload={fixture} />);
    // The performer block only ever shows at "large" — not present here regardless of truncation.
    expect(container.textContent).not.toContain("O. Toppin");
    // 5 games in the fixture, only 2 shown at small — "showing 2" appears in the subhead.
    expect(container.textContent).toContain("showing 2");
    // Team badges (bare abbreviations) always render; the longer nickname text does not at small.
    expect(container.textContent).toContain("HOU");
    expect(container.textContent).not.toContain("Rockets");
  });

  it("renders an em dash, never 0-0, for a scheduled game with no score yet", () => {
    const payload: ScoreboardPayload = {
      date: "2026-01-03",
      isLatestCompleted: false,
      allFinal: false,
      games: [
        {
          ...fixture.games[0],
          gameId: "0022500900",
          homePts: null,
          awayPts: null,
          status: "scheduled",
          period: null,
          clock: null,
          topPerformers: [],
        },
      ],
    };
    const { container } = render(<ScoreboardWidget kind="scoreboard" size="medium" payload={payload} />);
    expect(container.textContent).toContain("Scheduled");
    expect(container.textContent).not.toContain("0-0");
    const dashes = container.textContent?.match(/—/g) ?? [];
    expect(dashes.length).toBeGreaterThanOrEqual(2); // one per team line
  });

  it("shows a caption when the slate is empty", () => {
    const payload: ScoreboardPayload = { date: "2026-07-01", isLatestCompleted: false, allFinal: false, games: [] };
    const { getByText } = render(<ScoreboardWidget kind="scoreboard" size="medium" payload={payload} />);
    expect(getByText("No games on this date.")).toBeTruthy();
  });

  it("gives each game row an accessible label carrying both scores", () => {
    const { container } = render(<ScoreboardWidget kind="scoreboard" size="medium" payload={fixture} />);
    const row = container.querySelector("[aria-label*='Houston Rockets']");
    expect(row).toBeTruthy();
    expect(row?.getAttribute("aria-label")).toContain("Cleveland Cavaliers");
  });
});

describe("ScoreboardWidget availability", () => {
  // Regression: `performer.value.availability` was decoded and dropped, so a top-performer line
  // built from an estimated stat read as a recorded box score. A modern slate is all "full", and
  // `AvailabilityBadge` renders zero DOM nodes for that — so the common case must stay untouched.
  it("adds no chrome when every performer is measured", () => {
    const { container } = render(<ScoreboardWidget kind="scoreboard" size="large" payload={fixture} />);
    expect(container.querySelectorAll('[role="img"][aria-label*="estimated"]').length).toBe(0);
  });

  it("marks a performer whose stat is estimated", () => {
    const payload: ScoreboardPayload = {
      ...fixture,
      games: fixture.games.map((game) => ({
        ...game,
        topPerformers: game.topPerformers.map((performer) => ({
          ...performer,
          value: { ...performer.value, availability: "estimated" as const },
        })),
      })),
    };
    const { container } = render(<ScoreboardWidget kind="scoreboard" size="large" payload={payload} />);
    expect(container.querySelectorAll('[role="img"][aria-label*="estimated"]').length).toBeGreaterThan(0);
  });
});
