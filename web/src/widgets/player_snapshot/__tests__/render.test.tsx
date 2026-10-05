/**
 * A render smoke test against the real fixture, at both sizes this kind ships
 * (`generated/registry.ts`'s `sizes: ["medium", "large"]`).
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import PlayerSnapshotWidgetView from "../index";

const CURRENT_DIR = dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = resolve(CURRENT_DIR, "../../../../../contracts/fixtures/widget_player_snapshot.json");
const FIXTURE = JSON.parse(readFileSync(FIXTURE_PATH, "utf8")) as unknown;

afterEach(() => cleanup());

describe("PlayerSnapshotWidgetView", () => {
  for (const size of ["medium", "large"] as const) {
    it(`renders the fixture at size "${size}" with the player's name`, () => {
      render(<PlayerSnapshotWidgetView kind="player_snapshot" size={size} payload={FIXTURE} />);
      expect(screen.getByText("AJ Green")).toBeTruthy();
    });
  }

  it("shows more metrics at large than at medium", () => {
    const { container: mediumContainer } = render(
      <PlayerSnapshotWidgetView kind="player_snapshot" size="medium" payload={FIXTURE} />,
    );
    // FALLBACK_METRICS in the fixture has 8 entries; medium caps at 6, large shows all of them.
    expect(mediumContainer.textContent).not.toContain("PIE");
    cleanup();
    const { container: largeContainer } = render(
      <PlayerSnapshotWidgetView kind="player_snapshot" size="large" payload={FIXTURE} />,
    );
    expect(largeContainer.textContent).toContain("PIE");
  });

  it("renders no literal 0 for a null usage stat", () => {
    const withNullGames = { ...(FIXTURE as Record<string, unknown>), gp: null, gs: null, minutesPerGame: null };
    render(<PlayerSnapshotWidgetView kind="player_snapshot" size="large" payload={withNullGames} />);
    // formatInteger/formatDecimal render an em dash for null, never "0".
    expect(screen.queryByText("0 GP")).toBeNull();
    expect(screen.getAllByText(/—/).length).toBeGreaterThan(0);
  });

  it("shows the era note when present", () => {
    const withNote = { ...(FIXTURE as Record<string, unknown>), eraNote: "A test era note." };
    render(<PlayerSnapshotWidgetView kind="player_snapshot" size="large" payload={withNote} />);
    expect(screen.getByText("A test era note.")).toBeTruthy();
  });

  it("falls back to a tile-level error rather than throwing when the payload is malformed", () => {
    render(<PlayerSnapshotWidgetView kind="player_snapshot" size="large" payload={{ bogus: true }} />);
    expect(screen.getByRole("alert")).toBeTruthy();
  });
});
