/**
 * The one hash Hardwood uses to turn a short, stable string — a team abbreviation, or
 * `"player" + playerId` — into a deterministic index into a fixed-size palette.
 *
 * This has to be a hash we write ourselves rather than a language builtin: `Array.prototype`'s
 * nothing helps here, and JavaScript's own `String`/`Object` hashing is not specified at all,
 * let alone stable across engines. Worse, Swift's own `String.hashValue` is seeded per process
 * (`ios/NBAStats/DesignSystem/Theme.swift`'s `monogramColor(for:)` comment says exactly why it
 * cannot be used there either) — a per-process seed would mean a team's badge colour changes
 * every time the page reloads, which is the one thing a "deterministic" mark must never do.
 *
 * The algorithm is FNV-1a-32, folded over the **uppercased** text's UTF-16 code points, matching
 * `contracts/tools/gen_parity_cases.py`'s reference implementation byte for byte (see
 * `contracts/fixtures/monogram_cases.json`'s own `algorithm` field). It is pinned by that fixture
 * and asserted from this file's own Vitest suite, from a Swift test, and from a Python test — the
 * same fold, in three languages, so a team badge and a player avatar are the same colour on every
 * platform.
 *
 * `Math.imul` is not a style preference: `h ^ ... ) * 0x01000193` computed with plain `*` would
 * happily promote the product to a JavaScript `number` (an IEEE-754 double) and start losing the
 * low bits of a genuine 32-bit multiply somewhere past 2^53 of accumulated shifting — which for
 * FNV's constant occurs almost immediately, and would silently diverge from every other
 * implementation after only a few characters. `Math.imul` is the one operator that reproduces C's
 * (and Swift's) wrapping 32-bit multiply, and the trailing `>>> 0` reinterprets the signed 32-bit
 * result as the unsigned integer FNV is defined over.
 *
 * `for...of` over a `string` iterates Unicode code points (surrogate pairs combine into one
 * step), the same granularity as Swift's `String.unicodeScalars` — so a supplementary-plane
 * character folds the same number of times on both platforms. Every key this hash is actually
 * given (team abbreviations, `"player" + integer id`) is ASCII, so this only matters for staying
 * honest about what the function does with input it will never actually see.
 */

/**
 * FNV-1a-32 over `text.toUpperCase()`, reduced into `[0, modulo)`.
 *
 * @param text - Any string. Only its low byte per code point is folded in (`& 0xFF`), matching
 *   the Swift and Python references — this hash is never asked to distinguish two strings that
 *   differ only above U+00FF.
 * @param modulo - The palette size to index into. Must be a positive integer; a non-positive
 *   value makes the result meaningless (`% 0` is `NaN`) rather than throwing, so a caller who
 *   passes an empty palette's `.length` gets a value it can still branch on defensively.
 */
export function fnv1a(text: string, modulo: number): number {
  let h = 0x811c9dc5;
  for (const ch of text.toUpperCase()) {
    // `ch` is one code point yielded by `for...of` over a string, so `codePointAt(0)` is never
    // `undefined` here — the non-null assertion documents that rather than papering over a real
    // nullable server value the way a `?? 0` would (hardwood-local/no-nullable-number-zero-
    // fallback exists precisely to catch that shape, and would flag a `?? 0` here too).
    const codePoint = ch.codePointAt(0)!;
    h = Math.imul(h ^ (codePoint & 0xff), 0x01000193) >>> 0;
  }
  return h % modulo;
}
