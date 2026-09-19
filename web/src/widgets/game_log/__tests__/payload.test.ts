/**
 * `decodeGameLogPayload` against the real, checked-in fixture — see
 * `widgets/leaderboard/__tests__/payload.test.ts`'s docstring for what this proves and why.
 *
 * The fixture carries a `minutes` field on every row that `src/api/types.ts`'s `GameLogRow`
 * does not declare (see `payload.ts`'s module docstring) — the first test below is what would
 * have caught that as a red test had the decoder's key list not already accounted for it.
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { decodeGameLogPayload } from "../payload";

// `fileURLToPath(import.meta.url)` (a single argument) rather than
// `new URL("...", import.meta.url)`: the two-argument form is the exact syntax Vite's asset
// plugin pattern-matches and rewrites into a dev-server asset URL, which then makes
// `fileURLToPath` throw under vitest. Resolving the path by hand avoids that rewrite.
const CURRENT_DIR = dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = resolve(CURRENT_DIR, "../../../../../contracts/fixtures/widget_game_log.json");

function loadFixture(): Record<string, unknown> {
  return JSON.parse(readFileSync(FIXTURE_PATH, "utf8")) as Record<string, unknown>;
}

describe("decodeGameLogPayload", () => {
  it("decodes the real widget_game_log.json fixture with no data loss, including the row-level minutes field", () => {
    const raw = loadFixture();
    const decoded = decodeGameLogPayload(raw);
    expect(decoded).toEqual(raw);
    const rows = raw.rows as Array<Record<string, unknown>>;
    expect(decoded.rows[0].minutes).toBe(rows[0].minutes);
  });

  it("decodes every column and every row's values map", () => {
    const decoded = decodeGameLogPayload(loadFixture());
    expect(decoded.columns.length).toBeGreaterThan(0);
    expect(decoded.rows.length).toBeGreaterThan(0);
    for (const row of decoded.rows) {
      for (const column of decoded.columns) {
        // Every column requested is present in every row's values map for this fixture.
        expect(Object.prototype.hasOwnProperty.call(row.values, column.key)).toBe(true);
      }
    }
  });

  it("rejects a payload missing a required top-level field", () => {
    const raw = loadFixture();
    delete raw.columns;
    expect(() => decodeGameLogPayload(raw)).toThrow();
  });

  it("rejects a payload missing a required nested field", () => {
    const raw = loadFixture();
    const rows = raw.rows as Array<Record<string, unknown>>;
    delete rows[0].gameId;
    expect(() => decodeGameLogPayload(raw)).toThrow();
  });

  it("rejects an unrecognised top-level field — the anti-drift guarantee", () => {
    const raw = loadFixture();
    raw.newServerField = "surprise";
    expect(() => decodeGameLogPayload(raw)).toThrow(/unknown field "newServerField"/);
  });

  it("rejects an unrecognised field on a row", () => {
    const raw = loadFixture();
    const rows = raw.rows as Array<Record<string, unknown>>;
    rows[0].newRowField = 1;
    expect(() => decodeGameLogPayload(raw)).toThrow(/unknown field "newRowField"/);
  });

  it("rejects an unrecognised field on a column descriptor", () => {
    const raw = loadFixture();
    const columns = raw.columns as Array<Record<string, unknown>>;
    columns[0].newColumnField = true;
    expect(() => decodeGameLogPayload(raw)).toThrow(/unknown field "newColumnField"/);
  });

  it("rejects an unrecognised field on the player reference", () => {
    const raw = loadFixture();
    (raw.player as Record<string, unknown>).newPlayerField = "x";
    expect(() => decodeGameLogPayload(raw)).toThrow(/unknown field "newPlayerField"/);
  });
});
