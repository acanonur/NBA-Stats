/**
 * The anti-drift half of WP5's contract (WEB_DESIGN.md §9): asserts against the real fixture,
 * field for field, that (1) every field the decoder requires is present, and (2) every field
 * the fixture actually carries is known to the decoder. #2 is the one that matters most — it is
 * what turns a new `backend/nbastats/widgets/comparison.py` field into a red test here instead
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
import { decodeComparisonPayload } from "./decode";

const fixturePath = resolve(process.cwd(), "..", "contracts", "fixtures", "widget_comparison.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as Record<string, unknown>;
const fixtureMetrics = fixture.metrics as readonly Record<string, unknown>[];
const fixtureSubjects = fixture.subjects as readonly Record<string, unknown>[];

/** A shallow copy of `obj` without `key` — avoids the unused-binding lint noise of
 * `const { key: _key, ...rest } = obj`. */
function omit(obj: Record<string, unknown>, key: string): Record<string, unknown> {
  const copy = { ...obj };
  delete copy[key];
  return copy;
}

describe("decodeComparisonPayload", () => {
  it("decodes the golden fixture without dropping or inventing a field", () => {
    const payload = decodeComparisonPayload(fixture);
    expect(payload.season).toBe("2025-26");
    expect(payload.normalization).toBe("percentile");
    expect(payload.style).toBe("bars");
    expect(payload.metrics).toHaveLength(6);
    expect(payload.metrics[0]?.key).toBe("pts");
    expect(payload.subjects).toHaveLength(3);
    expect(payload.subjects[0]?.player.name).toBe("AJ Green");
    expect(payload.subjects[0]?.values).toHaveLength(6);
    expect(payload.subjects[1]?.colorIndex).toBe(1);
  });

  it("throws — not a silently-dropped field — when the payload carries an unknown top-level key", () => {
    expect(() => decodeComparisonPayload({ ...fixture, gameCount: 12 })).toThrow(/unrecognized field "gameCount"/);
  });

  it("throws when a metric descriptor carries an unknown key", () => {
    const withExtra = { ...fixture, metrics: [{ ...fixtureMetrics[0], unit: "pts" }, ...fixtureMetrics.slice(1)] };
    expect(() => decodeComparisonPayload(withExtra)).toThrow(/unrecognized field "unit"/);
  });

  it("throws when a subject's value object carries an unknown key", () => {
    const subject = fixtureSubjects[0];
    const values = subject.values as readonly Record<string, unknown>[];
    const withExtra = {
      ...fixture,
      subjects: [{ ...subject, values: [{ ...values[0], gameId: "x" }, ...values.slice(1)] }, ...fixtureSubjects.slice(1)],
    };
    expect(() => decodeComparisonPayload(withExtra)).toThrow(/unrecognized field "gameId"/);
  });

  it("throws when a metric descriptor's nested availability carries an unknown key", () => {
    const metric = fixtureMetrics[0];
    const eraSpec = metric.availability as Record<string, unknown>;
    const withExtra = {
      ...fixture,
      metrics: [{ ...metric, availability: { ...eraSpec, notes: "x" } }, ...fixtureMetrics.slice(1)],
    };
    expect(() => decodeComparisonPayload(withExtra)).toThrow(/unrecognized field "notes"/);
  });

  it("throws when a required top-level field is missing", () => {
    expect(() => decodeComparisonPayload(omit(fixture, "subjects"))).toThrow(/missing required field "subjects"/);
  });

  it("throws when a required nested field (player.name) is missing", () => {
    const subject = fixtureSubjects[0];
    const player = subject.player as Record<string, unknown>;
    const withBadPlayer = { ...fixture, subjects: [{ ...subject, player: omit(player, "name") }, ...fixtureSubjects.slice(1)] };
    expect(() => decodeComparisonPayload(withBadPlayer)).toThrow(/missing required field "name"/);
  });

  it("accepts a null domain (a metric with no catalog domain, e.g. pts)", () => {
    expect(fixtureMetrics[0]?.domain).toBeNull();
    const payload = decodeComparisonPayload(fixture);
    expect(payload.metrics[0]?.domain).toBeNull();
  });

  it("accepts a null value/rank/percentile/delta on a MetricValue (a subject missing that metric)", () => {
    const subject = fixtureSubjects[0];
    const values = subject.values as readonly Record<string, unknown>[];
    const missing = {
      ...values[0],
      value: null,
      displayValue: "—",
      rank: null,
      percentile: null,
      delta: null,
      availability: "unavailable",
    };
    const withMissing = { ...fixture, subjects: [{ ...subject, values: [missing, ...values.slice(1)] }, ...fixtureSubjects.slice(1)] };
    const payload = decodeComparisonPayload(withMissing);
    expect(payload.subjects[0]?.values[0]?.value).toBeNull();
    expect(payload.subjects[0]?.values[0]?.availability).toBe("unavailable");
  });

  it("rejects a field carrying the wrong type", () => {
    expect(() => decodeComparisonPayload({ ...fixture, normalization: 1 })).toThrow(/normalization/);
  });

  it("rejects an unrecognized normalization or style value", () => {
    expect(() => decodeComparisonPayload({ ...fixture, normalization: "zscore" })).toThrow(/normalization/);
    expect(() => decodeComparisonPayload({ ...fixture, style: "pie" })).toThrow(/style/);
  });
});
