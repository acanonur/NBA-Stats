/**
 * The anti-drift half of WP5's contract (WEB_DESIGN.md §9) for `daily_movers`: asserts against
 * the real fixture that no required field is missing and no fixture field is unknown to the
 * decoder — the second half is what turns a new server key into a red test here instead of a
 * silently-dropped field.
 */
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { decodeDailyMoversPayload } from "./decode";

const fixturePath = resolve(process.cwd(), "..", "contracts", "fixtures", "widget_daily_movers.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as Record<string, unknown>;
const fixtureRows = fixture.rows as readonly Record<string, unknown>[];

function omit(obj: Record<string, unknown>, key: string): Record<string, unknown> {
  const copy = { ...obj };
  delete copy[key];
  return copy;
}

describe("decodeDailyMoversPayload", () => {
  it("decodes the golden fixture without dropping or inventing a field", () => {
    const payload = decodeDailyMoversPayload(fixture);
    expect(payload.date).toBe("2026-01-02");
    expect(payload.direction).toBe("best");
    expect(payload.metric.key).toBe("game_score");
    expect(payload.rows).toHaveLength(8);
    expect(payload.rows[0]?.player.name).toBe("Austin Reaves");
    expect(payload.rows[0]?.delta).toBeCloseTo(14.5375, 3);
  });

  it("throws when the payload carries an unknown top-level key", () => {
    expect(() => decodeDailyMoversPayload({ ...fixture, asOf: "2026-01-02" })).toThrow(/unrecognized field "asOf"/);
  });

  it("throws when a row carries an unknown key", () => {
    const withExtra = { ...fixture, rows: [{ ...fixtureRows[0], minutes: 34.2 }] };
    expect(() => decodeDailyMoversPayload(withExtra)).toThrow(/unrecognized field "minutes"/);
  });

  it("throws when the metric descriptor carries an unknown key", () => {
    const metric = fixture.metric as Record<string, unknown>;
    const withExtra = { ...fixture, metric: { ...metric, isCounting: true } };
    expect(() => decodeDailyMoversPayload(withExtra)).toThrow(/unrecognized field "isCounting"/);
  });

  it("throws when a required field is missing from a row", () => {
    const withMissing = { ...fixture, rows: [omit(fixtureRows[0], "seasonAverage")] };
    expect(() => decodeDailyMoversPayload(withMissing)).toThrow(/missing required field "seasonAverage"/);
  });

  it("throws on an unrecognized direction", () => {
    expect(() => decodeDailyMoversPayload({ ...fixture, direction: "hottest" })).toThrow(/direction/);
  });

  it("accepts a row with no season baseline (a rookie's first game) — seasonAverage and delta null", () => {
    const noBaseline = { ...fixtureRows[0], seasonAverage: null, delta: null };
    const payload = decodeDailyMoversPayload({ ...fixture, rows: [noBaseline] });
    expect(payload.rows[0]?.seasonAverage).toBeNull();
    expect(payload.rows[0]?.delta).toBeNull();
  });

  it("accepts a null domain on the metric descriptor", () => {
    const metric = fixture.metric as Record<string, unknown>;
    expect(metric.domain).toBeNull();
    const payload = decodeDailyMoversPayload(fixture);
    expect(payload.metric.domain).toBeNull();
  });
});
