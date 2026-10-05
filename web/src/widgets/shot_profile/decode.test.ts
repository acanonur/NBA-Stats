/**
 * The anti-drift half of WP5's contract (WEB_DESIGN.md §9): asserts against the real fixture,
 * field for field, that (1) every field the decoder requires is present, and (2) every field
 * the fixture actually carries is known to the decoder. #2 is the one that matters most — it is
 * what turns a new `backend/nbastats/widgets/shot_profile.py` field into a red test here instead
 * of a silently-dropped key.
 *
 * The fixture is read straight off disk with `node:fs` rather than imported as a JSON module —
 * `widgets/scoreboard/decode.test.ts` does the same and explains why (no `resolveJsonModule`
 * needed for a file only tests touch, and a regenerated fixture needs no rebuild).
 */
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { decodeShotProfilePayload } from "./decode";

const fixturePath = resolve(process.cwd(), "..", "contracts", "fixtures", "widget_shot_profile.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as Record<string, unknown>;
const fixtureZones = fixture.zones as readonly Record<string, unknown>[];

/** A shallow copy of `obj` without `key` — avoids the unused-binding lint noise of
 * `const { key: _key, ...rest } = obj`. */
function omit(obj: Record<string, unknown>, key: string): Record<string, unknown> {
  const copy = { ...obj };
  delete copy[key];
  return copy;
}

describe("decodeShotProfilePayload", () => {
  it("decodes the golden fixture without dropping or inventing a field", () => {
    const payload = decodeShotProfilePayload(fixture);
    expect(payload.season).toBe("2025-26");
    expect(payload.subject.type).toBe("player");
    expect(payload.subject.player?.name).toBe("AJ Green");
    expect(payload.subject.team).toBeNull();
    expect(payload.zones).toHaveLength(5);
    expect(payload.zones.map((zone) => zone.zone)).toEqual([
      "rim",
      "paint_non_rim",
      "mid_range",
      "corner_three",
      "above_break_three",
    ]);
    expect(payload.zones[0]?.fgPct).toBeCloseTo(0.6470588235294118);
    expect(payload.threePointRate).toBeCloseTo(0.3172043010752688);
    expect(payload.note).toBeNull();
  });

  it("throws — not a silently-dropped field — when the payload carries an unknown top-level key", () => {
    expect(() => decodeShotProfilePayload({ ...fixture, gameCount: 12 })).toThrow(/unrecognized field "gameCount"/);
  });

  it("throws when a zone object carries an unknown key", () => {
    const withExtra = { ...fixture, zones: [{ ...fixtureZones[0], attempts: 4 }, ...fixtureZones.slice(1)] };
    expect(() => decodeShotProfilePayload(withExtra)).toThrow(/unrecognized field "attempts"/);
  });

  it("throws when the subject object carries an unknown key", () => {
    const subject = fixture.subject as Record<string, unknown>;
    const withExtra = { ...fixture, subject: { ...subject, favoriteTeamId: 1610612743 } };
    expect(() => decodeShotProfilePayload(withExtra)).toThrow(/unrecognized field "favoriteTeamId"/);
  });

  it("throws when a required top-level field is missing", () => {
    expect(() => decodeShotProfilePayload(omit(fixture, "season"))).toThrow(/missing required field "season"/);
  });

  it("throws when a required nested field (zone.label) is missing", () => {
    const withBadZone = { ...fixture, zones: [omit(fixtureZones[0], "label"), ...fixtureZones.slice(1)] };
    expect(() => decodeShotProfilePayload(withBadZone)).toThrow(/missing required field "label"/);
  });

  it("decodes a pre-1996-97 era payload — every zone shaped but null, never a fabricated zero", () => {
    const eraPayload = {
      subject: fixture.subject,
      season: "1961-62",
      seasonType: "Regular Season",
      zones: fixtureZones.map((zone) => ({
        zone: zone.zone,
        label: zone.label,
        fga: null,
        fgPct: null,
        shareOfFga: null,
        pointsPerShot: null,
        leagueFgPct: null,
        leagueShareOfFga: null,
      })),
      threePointRate: null,
      freeThrowRate: null,
      note: "Shot locations begin in 1996-97, with league-wide play-by-play; the 1961-62 season has box scores but no shot chart.",
    };
    const payload = decodeShotProfilePayload(eraPayload);
    expect(payload.zones).toHaveLength(5);
    expect(payload.zones.every((zone) => zone.fga === null && zone.fgPct === null)).toBe(true);
    expect(payload.note).toContain("1996-97");
  });

  it("rejects a field carrying the wrong type", () => {
    expect(() => decodeShotProfilePayload({ ...fixture, season: 2025 })).toThrow(/season/);
  });
});
