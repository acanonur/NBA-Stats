/**
 * The anti-drift half of WP5's contract (WEB_DESIGN.md §9) for `fantasy_draft_board`: asserts
 * against the real fixture that no required field is missing and no fixture field is unknown to
 * the decoder — including the column-level wrinkle where `align` and `signed` are present on
 * only some columns (see `decode.ts`'s module docstring).
 */
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { decodeFantasyDraftBoardPayload } from "./decode";

const fixturePath = resolve(process.cwd(), "..", "contracts", "fixtures", "widget_fantasy_draft_board.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as Record<string, unknown>;

function omit(obj: Record<string, unknown>, key: string): Record<string, unknown> {
  const copy = { ...obj };
  delete copy[key];
  return copy;
}

describe("decodeFantasyDraftBoardPayload", () => {
  it("decodes the golden fixture without dropping or inventing a field", () => {
    const payload = decodeFantasyDraftBoardPayload(fixture);
    expect(payload.season).toBe("2025-26");
    expect(payload.categories).toHaveLength(9);
    expect(payload.columns).toHaveLength(24);
    expect(payload.rows).toHaveLength(12);
    expect(payload.rows[0]?.player.name).toBe("Davion Mitchell");
    expect(payload.nextPick).toEqual({ overall: 1, round: 1, pickInRound: 1 });
  });

  it("defaults a column's align to trailing when the wire omits it, and keeps it leading when sent", () => {
    const payload = decodeFantasyDraftBoardPayload(fixture);
    const score = payload.columns.find((c) => c.key === "score");
    const team = payload.columns.find((c) => c.key === "team");
    expect(score?.align).toBe("trailing");
    expect(team?.align).toBe("leading");
  });

  it("defaults a column's signed to false when the wire omits it, and keeps it true when sent", () => {
    const payload = decodeFantasyDraftBoardPayload(fixture);
    const gp = payload.columns.find((c) => c.key === "gp");
    const score = payload.columns.find((c) => c.key === "score");
    const zPts = payload.columns.find((c) => c.key === "z_pts");
    expect(gp?.signed).toBe(false);
    expect(score?.signed).toBe(true);
    expect(zPts?.signed).toBe(true);
  });

  it("renders columns in exactly the order the server sent, including the ast/reb swap point", () => {
    const payload = decodeFantasyDraftBoardPayload(fixture);
    const keys = payload.columns.map((c) => c.key);
    expect(keys.indexOf("reb")).toBeLessThan(keys.indexOf("ast"));
    // The impact block keeps the export's own ordering (zPTS zTPM zAST zREB…), which differs
    // from the raw block's (PTS TPM REB AST…): zAST comes before zREB.
    expect(keys.indexOf("z_ast")).toBeLessThan(keys.indexOf("z_reb"));
  });

  it("throws when the payload carries an unknown top-level key", () => {
    expect(() => decodeFantasyDraftBoardPayload({ ...fixture, draftedPlayerIds: [] })).toThrow(
      /unrecognized field "draftedPlayerIds"/,
    );
  });

  it("throws when a column carries an unknown key", () => {
    const columns = fixture.columns as readonly Record<string, unknown>[];
    const withExtra = { ...fixture, columns: [{ ...columns[0], sortable: true }] };
    expect(() => decodeFantasyDraftBoardPayload(withExtra)).toThrow(/unrecognized field "sortable"/);
  });

  it("throws when a row carries an unknown key", () => {
    const rows = fixture.rows as readonly Record<string, unknown>[];
    const withExtra = { ...fixture, rows: [{ ...rows[0], drafted: false }] };
    expect(() => decodeFantasyDraftBoardPayload(withExtra)).toThrow(/unrecognized field "drafted"/);
  });

  it("throws when a required column field is missing", () => {
    const columns = fixture.columns as readonly Record<string, unknown>[];
    const withMissing = { ...fixture, columns: [omit(columns[0], "label")] };
    expect(() => decodeFantasyDraftBoardPayload(withMissing)).toThrow(/missing required field "label"/);
  });

  it("skips a values key the decoder does not need to know about the columns for (a text value on team)", () => {
    const payload = decodeFantasyDraftBoardPayload(fixture);
    expect(payload.rows[0]?.values.team).toBe("PHI");
    expect(payload.rows[0]?.values.pts).toBe(21.5);
  });

  it("accepts a null nextPick and a null note", () => {
    const payload = decodeFantasyDraftBoardPayload({ ...fixture, nextPick: null, note: null });
    expect(payload.nextPick).toBeNull();
    expect(payload.note).toBeNull();
  });

  it("throws when a values entry is neither a number, a string, nor null", () => {
    const rows = fixture.rows as readonly Record<string, unknown>[];
    const values = rows[0]?.values as Record<string, unknown>;
    const withBad = { ...fixture, rows: [{ ...rows[0], values: { ...values, pts: true } }] };
    expect(() => decodeFantasyDraftBoardPayload(withBad)).toThrow(/expected a number, a string, or null/);
  });
});
