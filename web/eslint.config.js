/**
 * Flat ESLint config (ESLint 9 dropped `.eslintrc.*` support for new projects going forward
 * and ESLint 10 removed it entirely — WEB_DESIGN.md §7.9 — so this is the only form a config
 * for this project could take).
 *
 * Three layers, in order:
 *   1. `js.configs.recommended` / `tseslint.configs.recommendedTypeChecked` — general
 *      correctness, with the type-checked variant because `hardwood-local`'s own rule (see
 *      `eslint-local/`) needs a real type checker attached to every file it lints, and mixing
 *      "type-checked here, not there" is how a rule silently stops firing on half the app.
 *   2. React rules — hooks correctness and the Fast Refresh boundary check.
 *   3. `hardwood-local` — this project's own rule enforcing the era-honesty contract, plus
 *      `no-restricted-imports` locking `POST /v1/dashboard/resolve` to one call site (§7.6)
 *      and `react/no-danger`'s hand-rolled equivalent (`no-restricted-properties` on
 *      `dangerouslySetInnerHTML`, since this project does not depend on `eslint-plugin-react`
 *      for one rule — §7.9 says there is no `dangerouslySetInnerHTML` anywhere in `web/src`,
 *      and no component library and no SVG-from-a-string approach ever needs one).
 *
 * `languageOptions.parserOptions.projectService` (typescript-eslint ≥ 8) is what makes
 * `services.program` non-null inside `hardwood-local`'s rule — without it the rule degrades to
 * a silent no-op (documented in the rule itself) rather than an error, which is exactly the
 * failure mode a reviewer would not notice, so it is called out here too.
 */
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import hardwoodLocal from "./eslint-local/index.js";

/**
 * The three spellings of `dangerouslySetInnerHTML` that reach React, shared by the two
 * `no-restricted-syntax` configurations below (flat config replaces a rule's whole option list
 * rather than merging it, so the scoped block has to restate these — declaring them once here
 * is what stops the two copies drifting).
 */
const NO_DANGER_SELECTORS = [
  {
    selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
    message:
      "dangerouslySetInnerHTML is banned in this codebase (WEB_DESIGN.md §7.9). Render with " +
      "JSX; React escapes by default.",
  },
  {
    selector: "Property[key.name='dangerouslySetInnerHTML']",
    message:
      "dangerouslySetInnerHTML is banned in this codebase (WEB_DESIGN.md §7.9), including in a " +
      "props object spread into an element. Render with JSX; React escapes by default.",
  },
  {
    selector: "MemberExpression[property.name='dangerouslySetInnerHTML']",
    message:
      "dangerouslySetInnerHTML is banned in this codebase (WEB_DESIGN.md §7.9). Render with " +
      "JSX; React escapes by default.",
  },
];

export default tseslint.config(
  {
    ignores: ["dist/**", "node_modules/**", "coverage/**"],
  },
  js.configs.recommended,
  ...tseslint.configs.recommendedTypeChecked,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2023,
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
      "hardwood-local": hardwoodLocal,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],

      "hardwood-local/no-nullable-number-zero-fallback": "error",

      // The hand-rolled equivalent of `eslint-plugin-react/no-danger` (§7.9): this project
      // depends on no third-party React lint plugin for one rule, since React's own escaping
      // is the reason the SVG builders in design/svg/ have no XSS sink to begin with.
      //
      // This MUST be `no-restricted-syntax`, not `no-restricted-properties`. In JSX,
      // `dangerouslySetInnerHTML` is a `JSXAttribute`, never a `MemberExpression`, so
      // `no-restricted-properties` cannot see it at all — and its `object: "*"` is not a
      // wildcard either (the rule has no wildcard; `"*"` matches an object *named* `*`, i.e.
      // nothing). The earlier spelling of this rule was inert: a file containing
      // `<div dangerouslySetInnerHTML={{ __html: html }} />` linted clean. The three selectors
      // below cover the attribute, a props object built up before it is spread in, and a plain
      // property read, which is every spelling that reaches React.
      "no-restricted-syntax": [
        "error",
        ...NO_DANGER_SELECTORS,
      ],
    },
  },
  {
    // §7.6, enforced rather than described: `api/client.ts`'s `fetchJson` is the single door to
    // the service, and only the `api/` modules are allowed through it — a component or widget
    // that reaches for it directly bypasses the CSRF header, the `401` redirect and the
    // `ErrorEnvelope` -> `ApiError` translation that live in exactly one place.
    //
    // A literal `paths: [{ name: "../api/resolve" }]` (what this file said before) did not
    // express that: `no-restricted-imports` `paths` match the import specifier STRING, so the
    // entry fired only on a file exactly one directory below `api/` and never on
    // `../../api/resolve` from a widget — and it banned importing `api/resolve`, which is the
    // sanctioned way for a caller to reach the resolve helpers, rather than banning the raw
    // client. `patterns` + a directory-scoped config block is what actually holds.
    files: ["src/**/*.{ts,tsx}"],
    ignores: ["src/api/**"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["**/api/client", "**/api/client.ts", "**/api/client.js"],
              message:
                "Only src/api/* may import api/client.ts. Use the typed helper in " +
                "api/resolve.ts / api/session.ts / api/dashboards.ts / api/sync.ts instead " +
                "(WEB_DESIGN.md §7.6).",
            },
          ],
        },
      ],
    },
  },
  {
    // The other half of §7.6: `POST /v1/dashboard/resolve` is named in exactly one module. Even
    // with `api/client.ts` locked down above, a second caller could still assemble the request
    // through a sanctioned `fetchJson` from inside `api/`; this makes the endpoint path itself
    // the thing that is scoped, so the 24-widget chunking, the clock-dependent split and the
    // `unchanged`-without-cache retry cannot be reimplemented a second, subtly different way.
    files: ["src/**/*.{ts,tsx}"],
    ignores: ["src/api/resolve.ts"],
    rules: {
      "no-restricted-syntax": [
        "error",
        ...NO_DANGER_SELECTORS,
        {
          selector: "Literal[value=/\\/dashboard\\/resolve/]",
          message:
            "Only src/api/resolve.ts may name POST /v1/dashboard/resolve (WEB_DESIGN.md §7.6). " +
            "Import the typed helpers that module exports instead.",
        },
      ],
    },
  },
  {
    // Config files run under Node, not the browser, and are not part of the typed app
    // program — linting them with `recommendedTypeChecked` would need them inside
    // tsconfig.json's `include`, which they deliberately are not (tsconfig.node.json owns
    // them; see that file).
    files: ["*.config.{js,ts}", "eslint-local/**/*.js"],
    ...tseslint.configs.disableTypeChecked,
    languageOptions: {
      globals: globals.node,
    },
  },
);
