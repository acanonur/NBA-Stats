/**
 * Colour *decisions* — as opposed to colour *values*, which live entirely in
 * `generated/tokens.css`/`tokens.ts`. Every function here answers "which token applies", never
 * "what hex is that token"; nothing in this file contains a colour literal; and the CSS
 * custom-property strings it returns can be plugged straight into an inline `style` or an SVG
 * `fill`/`stroke` attribute without either kind of caller re-deriving the same decision.
 *
 * Ports `Palette.value(for:higherIsBetter:)`, `Palette.chartColor(at:)`, and
 * `Palette.monogramColor(for:)` from `ios/NBAStats/DesignSystem/Theme.swift`.
 */
import { fnv1a } from "./fnv";
import { CHART_SERIES_LIGHT, MONOGRAM_PALETTE } from "../generated/tokens";

/** The three outcomes a comparison against a baseline can resolve to. Never guessed: a `null`
 * delta or a `higherIsBetter` the caller does not know is always `"neutral"`, matching
 * `Palette.value(for:higherIsBetter:)`'s own refusal to colour a comparison it cannot make. */
export type ComparisonOutcome = "positive" | "negative" | "neutral";

/** Deltas smaller than this are treated as indistinguishable from zero — the same epsilon
 * `Components.swift`'s `DeltaChip` and `Palette.value(for:higherIsBetter:)` use. */
const DELTA_EPSILON = 1e-9;

/**
 * The comparison outcome for a signed delta against a direction of merit.
 *
 * `higherIsBetter: null` — used wherever a shape's `higherIsBetter` is unknown, such as a
 * `fantasy_draft_board` volume column (`fga`, `fta`) or a metric with no catalog direction —
 * is `"neutral"` **by construction**, not a fallback to `true`: guessing a direction and
 * colouring by it would be worse than colouring nothing.
 */
export function comparisonOutcome(
  delta: number | null | undefined,
  higherIsBetter: boolean | null | undefined,
): ComparisonOutcome {
  if (delta === null || delta === undefined || !Number.isFinite(delta)) return "neutral";
  if (Math.abs(delta) <= DELTA_EPSILON) return "neutral";
  if (higherIsBetter === null || higherIsBetter === undefined) return "neutral";
  const isImprovement = higherIsBetter ? delta > 0 : delta < 0;
  return isImprovement ? "positive" : "negative";
}

/** The same decision expressed as a comparison between a value and a league average. */
export function comparisonOutcomeAgainst(
  value: number | null | undefined,
  baseline: number | null | undefined,
  higherIsBetter: boolean | null | undefined,
): ComparisonOutcome {
  if (value === null || value === undefined || baseline === null || baseline === undefined) {
    return "neutral";
  }
  return comparisonOutcome(value - baseline, higherIsBetter);
}

/** The CSS custom property for a {@link ComparisonOutcome}. */
export function comparisonColorVar(outcome: ComparisonOutcome): string {
  switch (outcome) {
    case "positive":
      return "var(--hw-positive)";
    case "negative":
      return "var(--hw-negative)";
    case "neutral":
      return "var(--hw-neutral)";
  }
}

/** Resolves a delta straight to its CSS custom property — the common case, when the caller has
 * no other use for the intermediate {@link ComparisonOutcome}. */
export function deltaColorVar(
  delta: number | null | undefined,
  higherIsBetter: boolean | null | undefined,
): string {
  return comparisonColorVar(comparisonOutcome(delta, higherIsBetter));
}

/**
 * One of the eight chart series colours, by index, wrapping around so a series index the API
 * sends never traps. The light-mode hex array is used only to size the wraparound — the actual
 * colour is the CSS custom property, which already resolves per colour scheme.
 */
export function chartColorVar(index: number): string {
  const count = CHART_SERIES_LIGHT.length;
  const wrapped = ((index % count) + count) % count;
  return `var(--hw-chart-${wrapped})`;
}

/**
 * A stable colour for a short key such as a team abbreviation or `"player" + playerId`, folded
 * with {@link fnv1a}. Deliberately a different palette from {@link chartColorVar} — a team badge
 * must never be visually confused with a chart series swatch sitting beside it in the same tile.
 *
 * `PlayerAvatar` and `TeamBadge` each fold a differently-prefixed key (`"player" + id` vs. the
 * bare abbreviation) so a player and their team's badge in the same row do not collide on the
 * palette by construction — see each component's own docstring for why.
 */
export function monogramColorVar(key: string): string {
  const index = fnv1a(key, MONOGRAM_PALETTE.length);
  return `var(--hw-mono-${index})`;
}
