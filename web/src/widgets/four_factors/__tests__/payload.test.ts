/**
 * `decodeFourFactorsPayload` against the real, checked-in fixture — see
 * `widgets/leaderboard/__tests__/payload.test.ts`'s docstring for what this proves and why.
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { decodeFourFactorsPayload } from "../payload";

const CURRENT_DIR = dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = resolve(CURRENT_DIR, "../../../../../contracts/fixtures/widget_four_factors.json");

function loadFixture(): Record<string, unknown> {
  return JSON.parse(readFileSync(FIXTURE_PATH, "utf8")) as Record<string, unknown>;
}

describe("decodeFourFactorsPayload", () => {
  it("decodes the real widget_four_factors.json fixture with no data loss", () => {
    const raw = loadFixture();
    const decoded = decodeFourFactorsPayload(raw);
    expect(decoded).toEqual(raw);
  });

  it("decodes the fixed offensive order and the four defensive counterparts", () => {
    const decoded = decodeFourFactorsPayload(loadFixture());
    expect(decoded.offense.map((entry) => entry.key)).toEqual(["efg_pct", "tov_pct", "oreb_pct", "ftr"]);
    expect(decoded.defense.map((entry) => entry.key)).toEqual(["opp_efg_pct", "opp_tov_pct", "opp_oreb_pct", "opp_ftr"]);
  });

  it("rejects a payload missing a required top-level field", () => {
    const raw = loadFixture();
    delete raw.team;
    expect(() => decodeFourFactorsPayload(raw)).toThrow();
  });

  it("rejects a payload missing a required nested field", () => {
    const raw = loadFixture();
    const offense = raw.offense as Array<Record<string, unknown>>;
    delete offense[0].displayValue;
    expect(() => decodeFourFactorsPayload(raw)).toThrow(/missing|must be a string/);
  });

  it("rejects an unrecognised top-level field — the anti-drift guarantee", () => {
    const raw = loadFixture();
    raw.newServerField = "surprise";
    expect(() => decodeFourFactorsPayload(raw)).toThrow(/unknown field "newServerField"/);
  });

  it("rejects an unrecognised field on a factor entry", () => {
    const raw = loadFixture();
    const offense = raw.offense as Array<Record<string, unknown>>;
    offense[0].higherIsBetter = true;
    expect(() => decodeFourFactorsPayload(raw)).toThrow(/unknown field "higherIsBetter"/);
  });

  it("rejects an unrecognised field on the team reference", () => {
    const raw = loadFixture();
    (raw.team as Record<string, unknown>).logoUrl = "x";
    expect(() => decodeFourFactorsPayload(raw)).toThrow(/unknown field "logoUrl"/);
  });

  it("accepts a null value, leagueAverage, percentile and rank on a factor entry", () => {
    const raw = loadFixture();
    const offense = raw.offense as Array<Record<string, unknown>>;
    offense[0] = { ...offense[0], value: null, leagueAverage: null, percentile: null, rank: null, displayValue: "—" };
    const decoded = decodeFourFactorsPayload(raw);
    expect(decoded.offense[0]?.value).toBeNull();
    expect(decoded.offense[0]?.leagueAverage).toBeNull();
    expect(decoded.offense[0]?.percentile).toBeNull();
    expect(decoded.offense[0]?.rank).toBeNull();
  });

  it("accepts an empty defense array (showOpponent: false)", () => {
    const raw = { ...loadFixture(), defense: [] };
    const decoded = decodeFourFactorsPayload(raw);
    expect(decoded.defense).toEqual([]);
  });
});
