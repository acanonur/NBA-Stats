/**
 * The anti-drift half of WP5's contract (WEB_DESIGN.md §9) for `next_game_projection`: asserts
 * against the real fixture that no required field is missing and no fixture field is unknown to
 * the decoder — the second half is what turns a new server key into a red test here instead of a
 * silently-dropped field.
 */
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { decodeNextGameProjectionPayload } from "./decode";

const fixturePath = resolve(process.cwd(), "..", "contracts", "fixtures", "widget_next_game_projection.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as Record<string, unknown>;

function omit(obj: Record<string, unknown>, key: string): Record<string, unknown> {
  const copy = { ...obj };
  delete copy[key];
  return copy;
}

describe("decodeNextGameProjectionPayload", () => {
  it("decodes the golden fixture without dropping or inventing a field", () => {
    const payload = decodeNextGameProjectionPayload(fixture);
    expect(payload.player?.name).toBe("AJ Green");
    expect(payload.game?.opponentAbbr).toBe("MIN");
    expect(payload.projectedMinutes.displayValue).toBe("38.6");
    expect(payload.lines).toHaveLength(7);
    expect(payload.lines[0]?.metric).toBe("pts");
    expect(payload.factors).toHaveLength(4);
    expect(payload.combo?.label).toBe("PTS+REB+AST");
    expect(payload.combo?.correlation).toHaveLength(3);
    expect(payload.method.summary).toBe("Opportunity x rate, shrunk per statistic.");
    expect(payload.notes).toHaveLength(3);
  });

  it("throws when the payload carries an unknown top-level key", () => {
    expect(() => decodeNextGameProjectionPayload({ ...fixture, extra: 1 })).toThrow(/unrecognized field "extra"/);
  });

  it("throws when a line carries an unknown key", () => {
    const lines = fixture.lines as readonly Record<string, unknown>[];
    const withExtra = { ...fixture, lines: [{ ...lines[0], units: "per game" }] };
    expect(() => decodeNextGameProjectionPayload(withExtra)).toThrow(/unrecognized field "units"/);
  });

  it("throws when a required field is missing from a line", () => {
    const lines = fixture.lines as readonly Record<string, unknown>[];
    const withMissing = { ...fixture, lines: [omit(lines[0], "mean")] };
    expect(() => decodeNextGameProjectionPayload(withMissing)).toThrow(/missing required field "mean"/);
  });

  it("throws when the descriptor carries an unknown key", () => {
    const lines = fixture.lines as readonly Record<string, unknown>[];
    const descriptor = lines[0]?.descriptor as Record<string, unknown>;
    const withExtra = { ...fixture, lines: [{ ...lines[0], descriptor: { ...descriptor, aliases: [] } }] };
    expect(() => decodeNextGameProjectionPayload(withExtra)).toThrow(/unrecognized field "aliases"/);
  });

  it("accepts a null player, game, and combo", () => {
    const payload = decodeNextGameProjectionPayload({ ...fixture, player: null, game: null, combo: null });
    expect(payload.player).toBeNull();
    expect(payload.game).toBeNull();
    expect(payload.combo).toBeNull();
  });

  it("accepts a null low/high on a line (no interval published)", () => {
    const lines = fixture.lines as readonly Record<string, unknown>[];
    const withNulls = { ...fixture, lines: [{ ...lines[0], low: null, high: null, intervalLevel: null }] };
    const payload = decodeNextGameProjectionPayload(withNulls);
    expect(payload.lines[0]?.low).toBeNull();
    expect(payload.lines[0]?.high).toBeNull();
  });

  it("accepts an empty factors array and empty notes", () => {
    const payload = decodeNextGameProjectionPayload({ ...fixture, factors: [], notes: [] });
    expect(payload.factors).toHaveLength(0);
    expect(payload.notes).toHaveLength(0);
  });

  it("throws on an unrecognized availability value", () => {
    const lines = fixture.lines as readonly Record<string, unknown>[];
    const withBad = { ...fixture, lines: [{ ...lines[0], availability: "confirmed" }] };
    expect(() => decodeNextGameProjectionPayload(withBad)).toThrow(/availability: expected one of/);
  });
});
