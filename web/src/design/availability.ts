/**
 * The era-honesty vocabulary — the TypeScript counterpart of the `MetricAvailability` extension
 * in `ios/NBAStats/DesignSystem/AvailabilityBadge.swift`. Every string a reader sees about *why*
 * a number is missing, estimated, or partial comes from this file, so the wording used by the
 * badge, the explainer dialog, and any widget's own inline note can never drift from one another.
 *
 * This is deliberately just words and small pure lookups, with no JSX in it: `AvailabilityBadge`
 * and `AvailabilityExplainer` both import from here rather than each holding a private copy of
 * the copy, and a non-visual widget (a table cell, an accessible label) can use it too without
 * pulling in a component.
 */
import type { Availability } from "../api/types";
import treatmentStyles from "./availability.module.css";

/** The glyph a badge renders beside a value, or `null` when a measured value needs no marker at
 * all — `"full"` is the only case this happens for. */
export function badgeGlyph(availability: Availability): string | null {
  switch (availability) {
    case "full":
      return null;
    case "estimated":
      return "est.";
    case "partial":
      return "^";
    case "unavailable":
      return "—";
  }
}

/** A few words, for accessibility labels and inline footnotes. */
export function shortLabel(availability: Availability): string {
  switch (availability) {
    case "full":
      return "measured";
    case "estimated":
      return "estimated from the box score";
    case "partial":
      return "partial, some games are missing inputs";
    case "unavailable":
      return "not tracked in this era";
  }
}

/** The headline of the explainer dialog. */
export function headline(availability: Availability): string {
  switch (availability) {
    case "full":
      return "Measured from official records";
    case "estimated":
      return "Estimated, not measured";
    case "partial":
      return "Built from an incomplete set of games";
    case "unavailable":
      return "The league did not track this yet";
  }
}

/** Plain English, with the metric and season filled in when the caller knows them. */
export function explanation(
  availability: Availability,
  metricName?: string | null,
  season?: string | null,
): string {
  const metric = metricName ?? "This stat";
  const when = season ? `the ${season} season` : "this era";
  switch (availability) {
    case "full":
      return `${metric} is taken straight from the league's official record for ${when}. No estimation is involved.`;
    case "estimated":
      return (
        `The NBA did not publish possession data before 1996-97, so ${metric} for ${when} is ` +
        "derived from the box score with Basketball-Reference style formulas. It is a careful " +
        "estimate, not a measured number, and it should not be compared with a modern figure " +
        "without that caveat."
      );
    case "partial":
      return (
        `Some of the games behind ${metric} in ${when} are missing the inputs the formula ` +
        "needs, so this number covers only part of the span. The caret marks values built " +
        "from an incomplete set of games."
      );
    case "unavailable":
      return (
        `${metric} did not exist in the league's record for ${when}, so there is no number to ` +
        "show. Hardwood shows an em dash rather than a zero, because a zero here would be a lie."
      );
  }
}

/** The one footer disclaimer every explainer shows, regardless of which availability opened it. */
export const PRE_1997_DISCLAIMER =
  "Pre-1997 advanced season numbers are Basketball-Reference style estimates from the box " +
  "score, not measured possessions. Hardwood labels them rather than quietly mixing them with " +
  "modern figures.";

/**
 * The CSS class a *value's own text* takes for its availability — a muted colour for
 * `"unavailable"`, a dashed underline for `"estimated"` — `undefined` for `"full"` and
 * `"partial"`, which need no treatment on the value itself (`"partial"`'s caret already lives on
 * the marker beside it). Shared by `StatValue`, `PinnedColumnTable`, and any other primitive
 * that renders a raw `MetricValue.displayValue`, so the treatment cannot drift between them.
 */
export function valueTreatmentClassName(availability: Availability): string | undefined {
  switch (availability) {
    case "unavailable":
      return treatmentStyles.unavailableValue;
    case "estimated":
      return treatmentStyles.estimatedValue;
    case "full":
    case "partial":
      return undefined;
  }
}

/** The CSS custom property a marker or an underline is drawn in for a given availability. */
export function markerColorVar(availability: Availability): string {
  switch (availability) {
    case "full":
    case "unavailable":
      return "var(--hw-text-tertiary)";
    case "estimated":
    case "partial":
      return "var(--hw-warning)";
  }
}
