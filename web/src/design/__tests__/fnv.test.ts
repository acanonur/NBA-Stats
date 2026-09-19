/**
 * Parity test: `fnv1a` must reproduce every case in `contracts/fixtures/monogram_cases.json` —
 * the same fixture Swift and Python assert against, so a team badge or a player avatar lands on
 * the same palette entry on every platform.
 *
 * The fixture is read straight off disk with `node:fs` rather than imported as a JSON module:
 * this keeps the shared contract fixture as the single source of truth without asking the app's
 * own `tsconfig.json` to add `resolveJsonModule` for a file only tests ever touch, and it means a
 * regenerated fixture is picked up with no rebuild step.
 */
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { fnv1a } from "../fnv";

interface MonogramCase {
  readonly index: number;
  readonly key: string;
  readonly kind: "player" | "team";
}

interface MonogramFixture {
  readonly algorithm: string;
  readonly modulo: number;
  readonly schemaVersion: number;
  readonly cases: readonly MonogramCase[];
}

// Resolved against `process.cwd()` (Vitest's working directory is `web/`, whichever way the
// suite was invoked — `npm test`, `npx vitest run`, or a single-file run) rather than
// `import.meta.url`: Vitest's collection phase does not always evaluate top-level module code
// with a `file:` URL behind `import.meta.url`, which makes `fileURLToPath` throw before a single
// test even registers. `process.cwd()` has no such wrinkle.
const fixturePath = resolve(process.cwd(), "..", "contracts", "fixtures", "monogram_cases.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as MonogramFixture;

describe("fnv1a", () => {
  it("has a modulo matching the twelve-entry monogram palette this fixture was generated for", () => {
    expect(fixture.modulo).toBe(12);
  });

  it("covers both team abbreviations and player keys", () => {
    const kinds = new Set(fixture.cases.map((c) => c.kind));
    expect(kinds).toEqual(new Set(["player", "team"]));
    expect(fixture.cases.length).toBeGreaterThan(0);
  });

  for (const testCase of fixture.cases) {
    it(`folds ${testCase.kind} key ${JSON.stringify(testCase.key)} to index ${testCase.index}`, () => {
      expect(fnv1a(testCase.key, fixture.modulo)).toBe(testCase.index);
    });
  }

  it("is stable across repeated calls (no per-process seed, unlike Swift's String.hashValue)", () => {
    const first = fnv1a("LAL", 12);
    const second = fnv1a("LAL", 12);
    expect(first).toBe(second);
  });

  it("uppercases before folding, so a mixed-case key matches its canonical form", () => {
    expect(fnv1a("player2544", 12)).toBe(fnv1a("PLAYER2544", 12));
    expect(fnv1a("player2544", 12)).toBe(fnv1a("Player2544", 12));
  });
});
