// Exists only so `../no-nullable-number-zero-fallback.test.ts`'s `filename: "fixtures/sample.ts"`
// resolves against a real path this directory's `tsconfig.json` includes — `RuleTester`
// compiles each case's own `code` string in its place, so this file's actual content is never
// read. Kept here, and kept in sync with the test's inline `SAMPLE` type, purely so a reader
// opening this path sees what the tests are really exercising instead of an empty file.
export interface Sample {
  readonly value: number | null;
  readonly plain: number;
  readonly label: string | null;
}
