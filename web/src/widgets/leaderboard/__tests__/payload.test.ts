/**
 * `decodeLeaderboardPayload` against the real, checked-in fixture — WEB_DESIGN.md §9 WP5 "done
 * when": a decoder test that asserts both that no required field is missing and that no key the
 * fixture actually carries is unknown to the decoder. The second half is what turns a new server
 * field into a red test here instead of a silently ignored one — see the assertions that inject
 * an unrecognised key into a clone of the real fixture and expect a throw.
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { decodeLeaderboardPayload } from "../payload";

// `fileURLToPath(import.meta.url)` (a single argument) rather than
// `new URL("...", import.meta.url)`: the two-argument form is the exact syntax Vite's asset
// plugin pattern-matches and rewrites into a dev-server asset URL (WHATWG `file:` scheme
// replaced with an `http:`/`/@fs/` one), which then makes `fileURLToPath` throw. Resolving the
// path by hand with `node:path` avoids that rewrite entirely.
const CURRENT_DIR = dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = resolve(CURRENT_DIR, "../../../../../contracts/fixtures/widget_leaderboard.json");

function loadFixture(): Record<string, unknown> {
  return JSON.parse(readFileSync(FIXTURE_PATH, "utf8")) as Record<string, unknown>;
}

describe("decodeLeaderboardPayload", () => {
  it("decodes the real widget_leaderboard.json fixture with no data loss", () => {
    const raw = loadFixture();
    const decoded = decodeLeaderboardPayload(raw);
    // Every decode function in payload.ts is a 1:1 field mapping — nothing renamed, nothing
    // dropped — so a full deep-equal against the raw fixture is exactly the "no required field
    // is missing" half of the anti-drift property: if it drifted from a 1:1 mapping the values
    // would differ, not just the shape.
    expect(decoded).toEqual(raw);
  });

  it("decodes every row's player, value and secondary metrics", () => {
    const decoded = decodeLeaderboardPayload(loadFixture());
    expect(decoded.rows.length).toBeGreaterThan(0);
    for (const row of decoded.rows) {
      expect(row.player).not.toBeNull();
      expect(row.value.displayValue.length).toBeGreaterThan(0);
      expect(row.secondary.length).toBe(decoded.secondaryMetrics.length);
    }
  });

  it("rejects a payload missing a required top-level field", () => {
    const raw = loadFixture();
    delete raw.rows;
    expect(() => decodeLeaderboardPayload(raw)).toThrow();
  });

  it("rejects a payload missing a required nested field", () => {
    const raw = loadFixture();
    const rows = raw.rows as Array<Record<string, unknown>>;
    delete rows[0].rank;
    expect(() => decodeLeaderboardPayload(raw)).toThrow();
  });

  it("rejects an unrecognised top-level field — the anti-drift guarantee", () => {
    const raw = loadFixture();
    raw.newServerField = "surprise";
    expect(() => decodeLeaderboardPayload(raw)).toThrow(/unknown field "newServerField"/);
  });

  it("rejects an unrecognised field on a row", () => {
    const raw = loadFixture();
    const rows = raw.rows as Array<Record<string, unknown>>;
    rows[0].newRowField = 1;
    expect(() => decodeLeaderboardPayload(raw)).toThrow(/unknown field "newRowField"/);
  });

  it("rejects an unrecognised field on the metric descriptor", () => {
    const raw = loadFixture();
    (raw.metric as Record<string, unknown>).newDescriptorField = true;
    expect(() => decodeLeaderboardPayload(raw)).toThrow(/unknown field "newDescriptorField"/);
  });

  it("rejects an unrecognised field on a player reference", () => {
    const raw = loadFixture();
    const rows = raw.rows as Array<Record<string, unknown>>;
    (rows[0].player as Record<string, unknown>).newPlayerField = "x";
    expect(() => decodeLeaderboardPayload(raw)).toThrow(/unknown field "newPlayerField"/);
  });

  it("rejects an unrecognised field on a metric value", () => {
    const raw = loadFixture();
    const rows = raw.rows as Array<Record<string, unknown>>;
    (rows[0].value as Record<string, unknown>).newValueField = 1;
    expect(() => decodeLeaderboardPayload(raw)).toThrow(/unknown field "newValueField"/);
  });

  it("rejects an unknown subjectType", () => {
    const raw = loadFixture();
    raw.subjectType = "franchise";
    expect(() => decodeLeaderboardPayload(raw)).toThrow(/subjectType/);
  });
});
