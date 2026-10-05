/**
 * The anti-drift half of WP5's contract (WEB_DESIGN.md §9) for `stat_tile`: asserts against the
 * real fixture that no required field is missing and no fixture field is unknown to the decoder
 * — the second half is what turns a new server key into a red test here instead of a
 * silently-dropped field.
 */
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { decodeStatTilePayload } from "./decode";

const fixturePath = resolve(process.cwd(), "..", "contracts", "fixtures", "widget_stat_tile.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as Record<string, unknown>;

function omit(obj: Record<string, unknown>, key: string): Record<string, unknown> {
  const copy = { ...obj };
  delete copy[key];
  return copy;
}

describe("decodeStatTilePayload", () => {
  it("decodes the golden fixture without dropping or inventing a field", () => {
    const payload = decodeStatTilePayload(fixture);
    expect(payload.subject.type).toBe("player");
    expect(payload.subject.player?.name).toBe("AJ Green");
    expect(payload.subject.team).toBeNull();
    expect(payload.primary.metric).toBe("ts_pct");
    expect(payload.secondary).toHaveLength(2);
    expect(payload.sparkline).toHaveLength(15);
    expect(payload.sparklineMetric).toBe("ts_pct");
  });

  it("throws when the payload carries an unknown top-level key", () => {
    expect(() => decodeStatTilePayload({ ...fixture, season: "2025-26" })).toThrow(/unrecognized field "season"/);
  });

  it("throws when the subject carries an unknown key", () => {
    const subject = fixture.subject as Record<string, unknown>;
    expect(() => decodeStatTilePayload({ ...fixture, subject: { ...subject, subjectId: 1 } })).toThrow(
      /unrecognized field "subjectId"/,
    );
  });

  it("throws when a sparkline point carries an unknown key", () => {
    const sparkline = fixture.sparkline as readonly Record<string, unknown>[];
    const withExtra = { ...fixture, sparkline: [{ ...sparkline[0], metric: "ts_pct" }] };
    expect(() => decodeStatTilePayload(withExtra)).toThrow(/unrecognized field "metric"/);
  });

  it("throws when a required field is missing from the primary MetricValue", () => {
    const primary = fixture.primary as Record<string, unknown>;
    expect(() => decodeStatTilePayload({ ...fixture, primary: omit(primary, "displayValue") })).toThrow(
      /missing required field "displayValue"/,
    );
  });

  it("accepts a null sparkline point value (a game the metric does not exist for)", () => {
    const sparkline = fixture.sparkline as readonly Record<string, unknown>[];
    const withNull = { ...fixture, sparkline: [{ ...sparkline[0], y: null }] };
    const payload = decodeStatTilePayload(withNull);
    expect(payload.sparkline[0]?.y).toBeNull();
  });

  it("accepts a team subject with a null player", () => {
    const teamSubject = {
      ...fixture,
      subject: { type: "team", player: null, team: { teamId: 1610612747, abbr: "LAL", name: "Los Angeles Lakers", city: "Los Angeles", nickname: "Lakers", conference: "West", division: "Pacific" } },
    };
    const payload = decodeStatTilePayload(teamSubject);
    expect(payload.subject.player).toBeNull();
    expect(payload.subject.team?.abbr).toBe("LAL");
  });

  it("throws on an unrecognized subject type", () => {
    expect(() => decodeStatTilePayload({ ...fixture, subject: { type: "franchise", player: null, team: null } })).toThrow(
      /type: expected one of player, team/,
    );
  });
});
