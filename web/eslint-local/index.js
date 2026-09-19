/**
 * `hardwood-local` — this app's own ESLint rules, for checks the generated catalogs and
 * `typescript-eslint`'s bundled rulesets cannot express because they are specific to this
 * contract rather than to TypeScript in general. There is exactly one rule today; see its
 * own docstring in `rules/no-nullable-number-zero-fallback.js` for what it enforces and,
 * as importantly, what it deliberately does not catch.
 *
 * Registered as a flat-config plugin object directly from `eslint.config.js` (a relative
 * import, not a published package), so this directory needs no `package.json` of its own.
 */
import noNullableNumberZeroFallback from "./rules/no-nullable-number-zero-fallback.js";

export default {
  rules: {
    "no-nullable-number-zero-fallback": noNullableNumberZeroFallback,
  },
};
