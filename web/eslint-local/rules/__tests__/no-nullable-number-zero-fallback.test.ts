/**
 * Regression test for `hardwood-local/no-nullable-number-zero-fallback` — the mechanical half
 * of "never render 0 for a missing stat" (see the rule's own docstring for the full case).
 *
 * This is a type-aware rule, so `RuleTester` needs a real TypeScript program behind each case
 * — a plain string of `code` with no project attached would leave
 * `context.sourceCode.parserServices` empty, which is exactly the "no type information"
 * branch the rule treats as a silent no-op (documented in the rule itself, and covered
 * separately below). Every case's `code` is a **complete, self-contained file**: it declares
 * its own `Sample` type and never imports across files, because `filename` below points every
 * case at the same virtual path (`fixtures/sample.ts`, chosen only so `tsconfigRootDir`'s
 * `include` covers it) and `RuleTester` compiles the `code` string in place of whatever that
 * path holds on disk — an inter-file `import` from that path back to itself is not what "one
 * project, several cases" means here.
 *
 * `RuleTester.describe`/`.it`/`.afterAll` are pointed at Vitest's own functions because
 * `@typescript-eslint/rule-tester` defaults to Mocha's globals, which this project does not
 * depend on (WEB_DESIGN.md §7.9: no test runner beyond Vitest).
 */
import { afterAll, describe, it } from "vitest";
import { RuleTester } from "@typescript-eslint/rule-tester";
import { Linter } from "eslint";
import tsParser from "@typescript-eslint/parser";
import rule from "../no-nullable-number-zero-fallback.js";

RuleTester.describe = describe;
RuleTester.it = it;
RuleTester.itOnly = it.only;
RuleTester.afterAll = afterAll;

const SAMPLE = `
interface Sample {
  readonly value: number | null;
  readonly plain: number;
  readonly label: string | null;
}
declare const s: Sample;
`;

const ruleTester = new RuleTester({
  languageOptions: {
    parserOptions: {
      project: "./fixtures/tsconfig.json",
      tsconfigRootDir: __dirname,
    },
  },
});

ruleTester.run("no-nullable-number-zero-fallback", rule, {
  valid: [
    {
      // A non-nullable field falling back to 0 is not this rule's concern (there is nothing
      // to fall back from).
      code: `${SAMPLE}\nconst a = s.plain ?? 0;`,
      filename: "fixtures/sample.ts",
    },
    {
      // The correct pattern: fall back to null (or leave it null), never to 0.
      code: `${SAMPLE}\nconst a = s.value ?? null;`,
      filename: "fixtures/sample.ts",
    },
    {
      // A nullable field that is not numeric at all — out of scope by the rule's own stated
      // limits (it exists for a *rendered zero*, not any falsy fallback).
      code: `${SAMPLE}\nconst a = s.label ?? "—";`,
      filename: "fixtures/sample.ts",
    },
  ],
  invalid: [
    {
      code: `${SAMPLE}\nconst a = s.value ?? 0;`,
      filename: "fixtures/sample.ts",
      errors: [{ messageId: "noZeroFallback", data: { operator: "??" } }],
    },
    {
      code: `${SAMPLE}\nconst a = s.value || 0;`,
      filename: "fixtures/sample.ts",
      errors: [{ messageId: "noZeroFallback", data: { operator: "||" } }],
    },
    {
      // -0 is exactly as wrong as 0: still a confident, wrong reading for "we don't know".
      code: `${SAMPLE}\nconst a = s.value ?? -0;`,
      filename: "fixtures/sample.ts",
      errors: [{ messageId: "noZeroFallback", data: { operator: "??" } }],
    },
  ],
});

describe("no-nullable-number-zero-fallback (untyped)", () => {
  it("is a documented, silent no-op with no type information attached", () => {
    // The rule's docstring promises this degrades to a no-op — never a thrown error — when
    // no parser services are available. A plain Linter with no `parserOptions.project`
    // reproduces exactly that: the one situation `npm run lint` never puts the rule in
    // (`eslint.config.js` always attaches `projectService: true`), but a caller of the
    // ESLint API directly could.
    const linter = new Linter();
    const messages = linter.verify(
      "const x = (v: number | null) => v ?? 0;",
      {
        files: ["**/*.ts"],
        languageOptions: {
          ecmaVersion: 2023,
          sourceType: "module",
          parser: tsParser,
          // Deliberately no `parserOptions.project` / `projectService` — this is the "no type
          // checker attached" case the rule's docstring documents as a silent no-op.
        },
        // `rule` is typed against `@typescript-eslint/utils`'s `RuleModule`, which ESLint's
        // own core `Linter.Config` type does not structurally accept (a known friction point
        // between the two type packages, not a real incompatibility — the rule runs under
        // this exact `Linter` in `npm run lint` via `eslint.config.js` every day).
        plugins: { local: { rules: { "no-zero": rule } } } as unknown as Linter.Config["plugins"],
        rules: { "local/no-zero": "error" },
      },
      { filename: "untyped.ts" },
    );
    if (messages.length !== 0) {
      throw new Error(`expected no lint messages without type info, got: ${JSON.stringify(messages)}`);
    }
  });
});
