/**
 * `decodePlayerSnapshotPayload` against the real, checked-in fixture — see
 * `widgets/leaderboard/__tests__/payload.test.ts`'s docstring for what this proves and why.
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { decodePlayerSnapshotPayload } from "../payload";

// `fileURLToPath(import.meta.url)` (a single argument) rather than
// `new URL("...", import.meta.url)`: the two-argument form is the exact syntax Vite's asset
// plugin pattern-matches and rewrites into a dev-server asset URL, which then makes
// `fileURLToPath` throw under vitest. Resolving the path by hand avoids that rewrite.
const CURRENT_DIR = dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = resolve(CURRENT_DIR, "../../../../../contracts/fixtures/widget_player_snapshot.json");

function loadFixture(): Record<string, unknown> {
  return JSON.parse(readFileSync(FIXTURE_PATH, "utf8")) as Record<string, unknown>;
}

describe("decodePlayerSnapshotPayload", () => {
  it("decodes the real widget_player_snapshot.json fixture with no data loss", () => {
    const raw = loadFixture();
    const decoded = decodePlayerSnapshotPayload(raw);
    expect(decoded).toEqual(raw);
  });

  it("decodes every metric value", () => {
    const decoded = decodePlayerSnapshotPayload(loadFixture());
    expect(decoded.metrics.length).toBeGreaterThan(0);
    for (const metric of decoded.metrics) {
      expect(metric.displayValue.length).toBeGreaterThan(0);
    }
  });

  it("rejects a payload missing a required top-level field", () => {
    const raw = loadFixture();
    delete raw.player;
    expect(() => decodePlayerSnapshotPayload(raw)).toThrow();
  });

  it("rejects a payload missing a required nested field", () => {
    const raw = loadFixture();
    const metrics = raw.metrics as Array<Record<string, unknown>>;
    delete metrics[0].displayValue;
    expect(() => decodePlayerSnapshotPayload(raw)).toThrow();
  });

  it("rejects an unrecognised top-level field — the anti-drift guarantee", () => {
    const raw = loadFixture();
    raw.newServerField = "surprise";
    expect(() => decodePlayerSnapshotPayload(raw)).toThrow(/unknown field "newServerField"/);
  });

  it("rejects an unrecognised field on the player reference", () => {
    const raw = loadFixture();
    (raw.player as Record<string, unknown>).newPlayerField = "x";
    expect(() => decodePlayerSnapshotPayload(raw)).toThrow(/unknown field "newPlayerField"/);
  });

  it("rejects an unrecognised field on a metric value", () => {
    const raw = loadFixture();
    const metrics = raw.metrics as Array<Record<string, unknown>>;
    metrics[0].newMetricField = 1;
    expect(() => decodePlayerSnapshotPayload(raw)).toThrow(/unknown field "newMetricField"/);
  });

  it("accepts a null eraNote and null gp/gs/minutesPerGame", () => {
    const raw = loadFixture();
    raw.eraNote = null;
    raw.gp = null;
    raw.gs = null;
    raw.minutesPerGame = null;
    const decoded = decodePlayerSnapshotPayload(raw);
    expect(decoded.eraNote).toBeNull();
    expect(decoded.gp).toBeNull();
    expect(decoded.gs).toBeNull();
    expect(decoded.minutesPerGame).toBeNull();
  });
});
