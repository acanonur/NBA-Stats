/**
 * A render smoke test against the real fixture, at both sizes this kind ships
 * (`generated/registry.ts`'s `sizes: ["medium", "large"]`).
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import GameLogWidgetView from "../index";

const CURRENT_DIR = dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = resolve(CURRENT_DIR, "../../../../../contracts/fixtures/widget_game_log.json");
const FIXTURE = JSON.parse(readFileSync(FIXTURE_PATH, "utf8")) as unknown;

afterEach(() => cleanup());

describe("GameLogWidgetView", () => {
  for (const size of ["medium", "large"] as const) {
    it(`renders the fixture at size "${size}" with the player's name`, () => {
      render(<GameLogWidgetView kind="game_log" size={size} payload={FIXTURE} />);
      expect(screen.getByText("AJ Green")).toBeTruthy();
    });
  }

  it("shows more games and columns at large than at medium", () => {
    const { container: mediumContainer } = render(<GameLogWidgetView kind="game_log" size="medium" payload={FIXTURE} />);
    const mediumRows = mediumContainer.querySelectorAll('[role="group"]');
    cleanup();
    const { container: largeContainer } = render(<GameLogWidgetView kind="game_log" size="large" payload={FIXTURE} />);
    const largeRows = largeContainer.querySelectorAll('[role="group"]');
    expect(largeRows.length).toBeGreaterThan(mediumRows.length);
  });

  it("renders an empty-state note rather than an empty table when there are no games", () => {
    const empty = { ...(FIXTURE as Record<string, unknown>), rows: [] };
    render(<GameLogWidgetView kind="game_log" size="large" payload={empty} />);
    expect(screen.getByText("No games logged for this season yet.")).toBeTruthy();
  });

  it("falls back to a tile-level error rather than throwing when the payload is malformed", () => {
    render(<GameLogWidgetView kind="game_log" size="large" payload={{ bogus: true }} />);
    expect(screen.getByRole("alert")).toBeTruthy();
  });
});
