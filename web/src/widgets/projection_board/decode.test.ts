/**
 * The anti-drift half of WP5's contract (WEB_DESIGN.md §9) for `projection_board`: asserts
 * against the real fixture that no required field is missing and no fixture field is unknown to
 * the decoder.
 */
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { decodeProjectionBoardPayload } from "./decode";

const fixturePath = resolve(process.cwd(), "..", "contracts", "fixtures", "widget_projection_board.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as Record<string, unknown>;

function omit(obj: Record<string, unknown>, key: string): Record<string, unknown> {
  const copy = { ...obj };
  delete copy[key];
  return copy;
}

describe("decodeProjectionBoardPayload", () => {
  it("decodes the golden fixture without dropping or inventing a field", () => {
    const payload = decodeProjectionBoardPayload(fixture);
    expect(payload.date).toBe("2026-01-05");
    expect(payload.throughDate).toBeNull();
    expect(payload.selectionDate).toBe("2026-01-02");
    expect(payload.gameCount).toBe(4);
    expect(payload.reference).toBe("season_average");
    expect(payload.rows).toHaveLength(6);
    expect(payload.rows[0]?.player.name).toBe("KJ Simpson");
    expect(payload.rows[0]?.descriptor.shortName).toBe("REB");
  });

  it("throws when the payload carries an unknown top-level key", () => {
    expect(() => decodeProjectionBoardPayload({ ...fixture, teamId: 1 })).toThrow(/unrecognized field "teamId"/);
  });

  it("throws when a row carries an unknown key", () => {
    const rows = fixture.rows as readonly Record<string, unknown>[];
    const withExtra = { ...fixture, rows: [{ ...rows[0], rank: 1 }] };
    expect(() => decodeProjectionBoardPayload(withExtra)).toThrow(/unrecognized field "rank"/);
  });

  it("throws when a required field is missing from a row", () => {
    const rows = fixture.rows as readonly Record<string, unknown>[];
    const withMissing = { ...fixture, rows: [omit(rows[0], "projection")] };
    expect(() => decodeProjectionBoardPayload(withMissing)).toThrow(/missing required field "projection"/);
  });

  it("accepts a null low/high/referenceValue/delta/deltaZ", () => {
    const rows = fixture.rows as readonly Record<string, unknown>[];
    const withNulls = {
      ...fixture,
      rows: [{ ...rows[0], low: null, high: null, referenceValue: null, delta: null, deltaZ: null }],
    };
    const payload = decodeProjectionBoardPayload(withNulls);
    expect(payload.rows[0]?.low).toBeNull();
    expect(payload.rows[0]?.deltaZ).toBeNull();
  });

  it("accepts a non-null throughDate", () => {
    const payload = decodeProjectionBoardPayload({ ...fixture, throughDate: "2026-01-06" });
    expect(payload.throughDate).toBe("2026-01-06");
  });

  it("throws on an unrecognized reference value", () => {
    expect(() => decodeProjectionBoardPayload({ ...fixture, reference: "market_line" })).toThrow(
      /reference: expected one of/,
    );
  });

  it("accepts an empty rows array with a null note", () => {
    const payload = decodeProjectionBoardPayload({ ...fixture, rows: [], note: null });
    expect(payload.rows).toHaveLength(0);
    expect(payload.note).toBeNull();
  });
});
