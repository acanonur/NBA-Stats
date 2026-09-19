/**
 * `decodeTeamEfficiencyPayload` against the real, checked-in fixture — see
 * `widgets/leaderboard/__tests__/payload.test.ts`'s docstring for what this proves and why.
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { decodeTeamEfficiencyPayload } from "../payload";

const CURRENT_DIR = dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = resolve(CURRENT_DIR, "../../../../../contracts/fixtures/widget_team_efficiency.json");

function loadFixture(): Record<string, unknown> {
  return JSON.parse(readFileSync(FIXTURE_PATH, "utf8")) as Record<string, unknown>;
}

describe("decodeTeamEfficiencyPayload", () => {
  it("decodes the real widget_team_efficiency.json fixture with no data loss", () => {
    const raw = loadFixture();
    const decoded = decodeTeamEfficiencyPayload(raw);
    expect(decoded).toEqual(raw);
  });

  it("decodes all 30 rows, ranked by the sort metric", () => {
    const decoded = decodeTeamEfficiencyPayload(loadFixture());
    expect(decoded.rows).toHaveLength(30);
    expect(decoded.rows[0]?.team.abbr).toBe("HOU");
    expect(decoded.rows[0]?.rank).toBe(1);
  });

  it("rejects a payload missing a required top-level field", () => {
    const raw = loadFixture();
    delete raw.rows;
    expect(() => decodeTeamEfficiencyPayload(raw)).toThrow();
  });

  it("rejects a payload missing a required nested field on a row", () => {
    const raw = loadFixture();
    const rows = raw.rows as Array<Record<string, unknown>>;
    delete rows[0].team;
    expect(() => decodeTeamEfficiencyPayload(raw)).toThrow();
  });

  it("rejects an unrecognised top-level field — the anti-drift guarantee", () => {
    const raw = loadFixture();
    raw.newServerField = "surprise";
    expect(() => decodeTeamEfficiencyPayload(raw)).toThrow(/unknown field "newServerField"/);
  });

  it("rejects an unrecognised field on a row", () => {
    const raw = loadFixture();
    const rows = raw.rows as Array<Record<string, unknown>>;
    rows[0] = { ...rows[0], gamesBack: 2.5 };
    expect(() => decodeTeamEfficiencyPayload(raw)).toThrow(/unknown field "gamesBack"/);
  });

  it("rejects an unrecognised field on the team reference", () => {
    const raw = loadFixture();
    const rows = raw.rows as Array<Record<string, unknown>>;
    (rows[0].team as Record<string, unknown>).logoUrl = "x";
    expect(() => decodeTeamEfficiencyPayload(raw)).toThrow(/unknown field "logoUrl"/);
  });

  it("rejects an unrecognised style", () => {
    const raw = { ...loadFixture(), style: "pie" };
    expect(() => decodeTeamEfficiencyPayload(raw)).toThrow(/unknown style "pie"/);
  });

  it("accepts a value map carrying null entries and null wins/losses", () => {
    const raw = loadFixture();
    const rows = raw.rows as Array<Record<string, unknown>>;
    rows[0] = { ...rows[0], wins: null, losses: null, values: { ...(rows[0].values as object), pace: null } };
    const decoded = decodeTeamEfficiencyPayload(raw);
    expect(decoded.rows[0]?.wins).toBeNull();
    expect(decoded.rows[0]?.losses).toBeNull();
    expect(decoded.rows[0]?.values.pace).toBeNull();
  });

  it("accepts an unranked (rank <= 0) row", () => {
    const raw = loadFixture();
    const rows = raw.rows as Array<Record<string, unknown>>;
    rows[0] = { ...rows[0], rank: 0 };
    const decoded = decodeTeamEfficiencyPayload(raw);
    expect(decoded.rows[0]?.rank).toBe(0);
  });
});
