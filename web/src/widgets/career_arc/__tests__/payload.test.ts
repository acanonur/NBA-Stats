/**
 * `decodeCareerArcPayload` against the real, checked-in fixture — see
 * `widgets/leaderboard/__tests__/payload.test.ts`'s docstring for what this proves and why.
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { decodeCareerArcPayload } from "../payload";

const CURRENT_DIR = dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = resolve(CURRENT_DIR, "../../../../../contracts/fixtures/widget_career_arc.json");

function loadFixture(): Record<string, unknown> {
  return JSON.parse(readFileSync(FIXTURE_PATH, "utf8")) as Record<string, unknown>;
}

describe("decodeCareerArcPayload", () => {
  it("decodes the real widget_career_arc.json fixture with no data loss", () => {
    const raw = loadFixture();
    const decoded = decodeCareerArcPayload(raw);
    expect(decoded).toEqual(raw);
  });

  it("decodes three regular seasons and one playoff season", () => {
    const decoded = decodeCareerArcPayload(loadFixture());
    expect(decoded.seasons).toHaveLength(3);
    expect(decoded.playoffSeasons).toHaveLength(1);
    expect(decoded.peak?.season).toBe("2025-26");
  });

  it("keeps the fixture's era boundary 'detail' field even though api/types.ts's CareerArcPayload omits it", () => {
    const decoded = decodeCareerArcPayload(loadFixture());
    expect(decoded.eraBoundaries[0]?.detail).toContain("per-minute metrics");
  });

  it("rejects a payload missing a required top-level field", () => {
    const raw = loadFixture();
    delete raw.player;
    expect(() => decodeCareerArcPayload(raw)).toThrow();
  });

  it("rejects a payload missing a required nested field on a season", () => {
    const raw = loadFixture();
    const seasons = raw.seasons as Array<Record<string, unknown>>;
    delete seasons[0].availability;
    expect(() => decodeCareerArcPayload(raw)).toThrow();
  });

  it("rejects an unrecognised top-level field — the anti-drift guarantee", () => {
    const raw = loadFixture();
    raw.newServerField = "surprise";
    expect(() => decodeCareerArcPayload(raw)).toThrow(/unknown field "newServerField"/);
  });

  it("rejects an unrecognised field on a season", () => {
    const raw = loadFixture();
    const seasons = raw.seasons as Array<Record<string, unknown>>;
    seasons[0] = { ...seasons[0], playerId: 1 };
    expect(() => decodeCareerArcPayload(raw)).toThrow(/unknown field "playerId"/);
  });

  it("rejects an unrecognised field on the metric descriptor", () => {
    const raw = loadFixture();
    (raw.metric as Record<string, unknown>).isEstimated = true;
    expect(() => decodeCareerArcPayload(raw)).toThrow(/unknown field "isEstimated"/);
  });

  it("rejects an unrecognised xAxis value", () => {
    const raw = { ...loadFixture(), xAxis: "elevation" };
    expect(() => decodeCareerArcPayload(raw)).toThrow(/unknown axis "elevation"/);
  });

  it("accepts a null peak", () => {
    const raw = { ...loadFixture(), peak: null };
    const decoded = decodeCareerArcPayload(raw);
    expect(decoded.peak).toBeNull();
  });

  it("accepts a season with a null age (an age-axis fallback case)", () => {
    const raw = loadFixture();
    const seasons = raw.seasons as Array<Record<string, unknown>>;
    seasons[0] = { ...seasons[0], age: null };
    const decoded = decodeCareerArcPayload(raw);
    expect(decoded.seasons[0]?.age).toBeNull();
  });
});
