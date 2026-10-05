/**
 * The anti-drift half of WP5's contract (WEB_DESIGN.md §9) for `fantasy_trade`: asserts against
 * the real fixture that no required field is missing and no fixture field is unknown to the
 * decoder — plus the one legitimate `sensitivity: null` case `decode.ts`'s docstring explains.
 */
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { decodeFantasyTradePayload } from "./decode";

const fixturePath = resolve(process.cwd(), "..", "contracts", "fixtures", "widget_fantasy_trade.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as Record<string, unknown>;

function omit(obj: Record<string, unknown>, key: string): Record<string, unknown> {
  const copy = { ...obj };
  delete copy[key];
  return copy;
}

describe("decodeFantasyTradePayload", () => {
  it("decodes the golden fixture without dropping or inventing a field", () => {
    const payload = decodeFantasyTradePayload(fixture);
    expect(payload.give.players).toHaveLength(1);
    expect(payload.give.players[0]?.player.name).toBe("AJ Green");
    expect(payload.get.players[0]?.player.name).toBe("Kobe Sanders");
    expect(payload.categories).toHaveLength(9);
    expect(payload.points.espn_points?.net).toBe(14.09);
    expect(payload.points.yahoo_points?.net).toBe(12.44);
    expect(payload.sensitivity.scenarios).toHaveLength(5);
    expect(payload.sensitivity.flips).toBe(false);
    expect(payload.verdict).toBe("clear win");
  });

  it("decodes a null sensitivity (showSensitivity: false) to the empty sentinel rather than throwing", () => {
    const payload = decodeFantasyTradePayload({ ...fixture, sensitivity: null });
    expect(payload.sensitivity.scenarios).toHaveLength(0);
    expect(payload.sensitivity.base).toBeNull();
    expect(payload.sensitivity.flips).toBe(false);
  });

  it("throws when the payload carries an unknown top-level key", () => {
    expect(() => decodeFantasyTradePayload({ ...fixture, oddsBoost: 1 })).toThrow(/unrecognized field "oddsBoost"/);
  });

  it("throws when a side carries an unknown key", () => {
    const give = fixture.give as Record<string, unknown>;
    expect(() => decodeFantasyTradePayload({ ...fixture, give: { ...give, teamName: "x" } })).toThrow(
      /unrecognized field "teamName"/,
    );
  });

  it("throws when a category delta carries an unknown key", () => {
    const categories = fixture.categories as readonly Record<string, unknown>[];
    const withExtra = { ...fixture, categories: [{ ...categories[0], weight: 1 }] };
    expect(() => decodeFantasyTradePayload(withExtra)).toThrow(/unrecognized field "weight"/);
  });

  it("throws when a required field is missing from a player line", () => {
    const give = fixture.give as Record<string, unknown>;
    const players = give.players as readonly Record<string, unknown>[];
    const withMissing = { ...fixture, give: { ...give, players: [omit(players[0], "totalZ")] } };
    expect(() => decodeFantasyTradePayload(withMissing)).toThrow(/missing required field "totalZ"/);
  });

  it("accepts an empty side (no players added yet)", () => {
    const payload = decodeFantasyTradePayload({
      ...fixture,
      give: { label: "give", count: 0, totalZ: null, espnPoints: null, yahooPoints: null, players: [], missing: [] },
    });
    expect(payload.give.players).toHaveLength(0);
    expect(payload.give.totalZ).toBeNull();
  });

  it("accepts a null note, changeZ, and replacementValue", () => {
    const payload = decodeFantasyTradePayload({ ...fixture, note: null, changeZ: null, replacementValue: null });
    expect(payload.note).toBeNull();
    expect(payload.changeZ).toBeNull();
  });
});
