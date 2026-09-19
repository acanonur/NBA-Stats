/**
 * The mechanical enforcement of "a statistic that did not exist in an era is null and renders
 * as an em dash, never 0" (WEB_DESIGN.md, house rules; CONTRACT.md §6).
 *
 * `MetricValue.value` — and every other field this API can send `null` for in place of a
 * number, per `api/types.ts`'s own rule that a nullable server number is typed
 * `number | null`, never `number` — means exactly one thing when it is `null`: the era did
 * not record this statistic, or it genuinely could not be computed. `someValue ?? 0` or
 * `someValue || 0` turns that "we don't know" into a confident, wrong "zero", indistinguishable
 * on screen from a real zero (a player who really did grab 0 rebounds). Once a component reads
 * `.value ?? 0` the type checker is satisfied — the result really is a `number` — so nothing
 * else in the toolchain can catch this. A lint rule reading type information is the only place
 * left to stop it before it reaches a chart or a table cell.
 *
 * WHAT THIS RULE CATCHES: a `LogicalExpression` (`??` or `||`) whose right-hand side is the
 * literal `0` (or `-0`) and whose left-hand side's static type is a union that includes
 * `null` and/or `undefined` alongside a numeric type (`number`, a numeric literal type, or
 * `bigint`). That is precisely the shape of every nullable numeric field this contract
 * defines — `MetricValue.value`, `MetricValue.rank`, `GameLogRow.minutes`, and so on through
 * `api/types.ts` — decoded, destructured, or accessed through an optional chain.
 *
 * WHAT THIS RULE DOES NOT CATCH, on purpose or by necessity — state these precisely rather
 * than implying broader coverage than exists:
 *   - A fallback to any literal other than `0` (`?? "—"`, `?? null`, `?? undefined`) is exactly
 *     the correct pattern for rendering the em dash and is never flagged.
 *   - A fallback guarding a value the type checker does not know is nullable — an `any`-typed
 *     value (a `JSON.parse` result, an untyped third-party payload, a `@ts-ignore`d access) —
 *     is invisible to this rule, because there is no union to inspect. The fix for that gap is
 *     typing the payload, not widening this rule.
 *   - `someValue == null ? 0 : someValue` and other non-`??`/`||` spellings of the same bug are
 *     not covered; `no-restricted-syntax` cannot express an arbitrary conditional's semantics
 *     as cheaply as this dedicated rule expresses the two logical operators, and in this
 *     codebase the ternary spelling does not appear (every existing null guard already prefers
 *     `??`).
 *   - This rule needs type-aware linting (`parserOptions.project` / `projectService`); running
 *     it without a type checker attached (for example `eslint --no-config-lookup` against one
 *     file with no tsconfig in scope) makes it a silent no-op, not a false positive. `npm run
 *     lint` always runs with the project's own `tsconfig.json` attached (see `eslint.config.js`),
 *     so this only matters for someone invoking the ESLint API directly.
 *   - Non-zero falsy fallbacks (`?? NaN`, `|| ""`) are out of scope: the one failure mode this
 *     rule exists for is a *rendered zero*, because that is the one value indistinguishable
 *     from a real, present statistic.
 */

/** @typedef {import("@typescript-eslint/utils").TSESTree.Node} EstreeNode */
/** @typedef {import("typescript").Type} TsType */

/** @type {import('@typescript-eslint/utils').TSESLint.RuleModule<'noZeroFallback', []>} */
const rule = {
  meta: {
    type: "problem",
    docs: {
      description:
        "Disallow `?? 0` / `|| 0` on a value whose type includes null or undefined alongside " +
        "a numeric type — an era-unavailable statistic must render as an em dash, never 0.",
    },
    schema: [],
    messages: {
      noZeroFallback:
        "Do not fall back a nullable numeric value to 0 with '{{operator}}'. A null value " +
        "here means the statistic does not exist for this era or subject, and must render as " +
        "an em dash (see design/format.ts), never a confident zero. Use '{{operator}} null' " +
        "(or leave the null and format it downstream) instead.",
    },
  },
  create(context) {
    const services = context.sourceCode.parserServices;
    if (!services || !services.program || !services.esTreeNodeToTSNodeMap) {
      // No type information available (see the module docstring): degrade to a no-op rather
      // than throwing, so a misconfigured one-off `eslint` invocation fails open, not with a
      // stack trace that looks like a bug in this rule.
      return {};
    }
    const program = services.program;
    const nodeMap = services.esTreeNodeToTSNodeMap;
    const checker = program.getTypeChecker();

    /**
     * Is `node` the literal `0`, written as `0` or as unary `-0`?
     * @param {EstreeNode} node
     * @returns {boolean}
     */
    function isZeroLiteral(node) {
      if (node.type === "Literal" && typeof node.value === "number" && node.value === 0) {
        return true;
      }
      if (
        node.type === "UnaryExpression" &&
        node.operator === "-" &&
        node.argument.type === "Literal" &&
        typeof node.argument.value === "number" &&
        node.argument.value === 0
      ) {
        return true;
      }
      return false;
    }

    /**
     * True when `type` is a numeric type: `number`, a numeric literal, or `bigint`.
     * @param {TsType} type
     * @returns {boolean}
     */
    function isNumericConstituent(type) {
      const NUMBER_LIKE =
        // ts.TypeFlags.NumberLike | ts.TypeFlags.BigIntLike, inlined so this file needs no
        // direct `typescript` import beyond what typescript-eslint already re-exports.
        /* Number */ 8 | /* NumberLiteral */ 256 | /* BigInt */ 64 | /* BigIntLiteral */ 2048;
      return (type.flags & NUMBER_LIKE) !== 0;
    }

    /**
     * True when `type` is exactly `null` or `undefined`.
     * @param {TsType} type
     * @returns {boolean}
     */
    function isNullish(type) {
      // ts.TypeFlags.Undefined (1 << 15) | .Null (1 << 16) | .Void (1 << 14).
      const NULLISH = /* Void */ 16384 | /* Undefined */ 32768 | /* Null */ 65536;
      return (type.flags & NULLISH) !== 0;
    }

    /**
     * True when `type` is a union of one-or-more numeric constituents and one-or-more nullish
     * constituents, and nothing else — exactly the shape of a nullable server number.
     * @param {TsType} type
     * @returns {boolean}
     */
    function isNullableNumeric(type) {
      const parts = type.isUnion() ? type.types : [type];
      let sawNumeric = false;
      let sawNullish = false;
      for (const part of parts) {
        if (isNullish(part)) {
          sawNullish = true;
        } else if (isNumericConstituent(part)) {
          sawNumeric = true;
        } else {
          return false;
        }
      }
      return sawNumeric && sawNullish;
    }

    return {
      LogicalExpression(node) {
        if (node.operator !== "??" && node.operator !== "||") {
          return;
        }
        if (!isZeroLiteral(node.right)) {
          return;
        }
        const tsLeft = nodeMap.get(node.left);
        const leftType = checker.getTypeAtLocation(tsLeft);
        if (!isNullableNumeric(leftType)) {
          return;
        }
        context.report({
          node,
          messageId: "noZeroFallback",
          data: { operator: node.operator },
        });
      },
    };
  },
};

export default rule;
