/**
 * Parity test: `formatValue` must reproduce every case in
 * `contracts/fixtures/format_cases.json` — including the `null -> "—"` rule and both
 * signed-zero `plusMinus1` cases (`0.0` and `-0.0` must both render `"0.0"`, matching Swift's
 * `value != 0` guard, which is `true` for neither).
 */
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { EM_DASH, formatValue } from "../format";
import type { MetricFormat } from "../../generated/contracts";

interface FormatCase {
  readonly format: MetricFormat;
  readonly value: number | null;
  readonly expected: string;
}

interface FormatFixture {
  readonly emDash: string;
  readonly schemaVersion: number;
  readonly cases: readonly FormatCase[];
}

// See fnv.test.ts for why this is resolved against `process.cwd()` rather than
// `import.meta.url`.
const fixturePath = resolve(process.cwd(), "..", "contracts", "fixtures", "format_cases.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as FormatFixture;

describe("formatValue", () => {
  it("agrees with the fixture's own em dash character", () => {
    expect(EM_DASH).toBe(fixture.emDash);
  });

  it("covers every MetricFormat branch", () => {
    const formats = new Set(fixture.cases.map((c) => c.format));
    expect(formats).toEqual(
      new Set([
        "integer",
        "decimal1",
        "decimal2",
        "percent1",
        "percent2",
        "rating1",
        "plusMinus1",
        "minutes",
      ]),
    );
  });

  for (const testCase of fixture.cases) {
    it(`renders ${testCase.format}(${JSON.stringify(testCase.value)}) as ${JSON.stringify(testCase.expected)}`, () => {
      expect(formatValue(testCase.value, testCase.format)).toBe(testCase.expected);
    });
  }

  it("treats -0.0 identically to 0.0 for plusMinus1 — both render unsigned", () => {
    expect(formatValue(-0.0, "plusMinus1")).toBe("0.0");
    expect(formatValue(0.0, "plusMinus1")).toBe(formatValue(-0.0, "plusMinus1"));
  });

  it("never renders 0 for null, across every format", () => {
    for (const format of [
      "integer",
      "decimal1",
      "decimal2",
      "percent1",
      "percent2",
      "rating1",
      "plusMinus1",
      "minutes",
    ] as const) {
      expect(formatValue(null, format)).toBe(EM_DASH);
      expect(formatValue(undefined, format)).toBe(EM_DASH);
      expect(formatValue(Number.NaN, format)).toBe(EM_DASH);
    }
  });
});
