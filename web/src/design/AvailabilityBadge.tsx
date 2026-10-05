/**
 * The era-honesty marker that sits beside a value —
 * `ios/NBAStats/DesignSystem/AvailabilityBadge.swift`'s `AvailabilityBadge`, ported.
 *
 * `"full"` renders **zero DOM nodes** — not an empty `<span>` — because this component sits
 * beside every number in a dense table, and an always-present container would change layout
 * weight and spacing for the overwhelming majority of values that need no marker at all. This is
 * asserted directly in `__tests__/AvailabilityBadge.test.tsx`.
 *
 * Everything else renders a marker that is a button (opening {@link AvailabilityExplainer}) when
 * `isInteractive`, or a plain, non-interactive glyph otherwise — a dense table cell wants the
 * marker to explain the whole column once, not to turn every cell into its own tap target.
 */
import { useState } from "react";
import type { Availability } from "../api/types";
import { badgeGlyph, shortLabel } from "./availability";
import { AvailabilityExplainer } from "./AvailabilityExplainer";
import styles from "./AvailabilityBadge.module.css";
import clsx from "clsx";

export interface AvailabilityBadgeProps {
  readonly availability: Availability;
  /** `false` renders a plain dot instead of a text capsule — for a compact call site (a dense
   * table column) where the value beside the marker already carries the em dash for
   * `"unavailable"`, so a second one here would be redundant. */
  readonly showsText?: boolean;
  /** `false` renders a non-interactive marker with no explainer attached — for a call site
   * (`StatValue` at `style="cell"`) too small or too repeated to want a tap target on every
   * instance. */
  readonly isInteractive?: boolean;
  readonly metricName?: string | null;
  readonly season?: string | null;
  readonly notes?: readonly string[];
}

export function AvailabilityBadge({
  availability,
  showsText = true,
  isInteractive = true,
  metricName,
  season,
  notes = [],
}: AvailabilityBadgeProps): JSX.Element | null {
  const [isExplaining, setIsExplaining] = useState(false);

  // The whole point of this component: a measured modern value gets no chrome and no explainer
  // attached, so this path costs nothing wherever it is used.
  if (availability === "full") return null;

  const accessibleLabel = metricName ? `${metricName}: ${shortLabel(availability)}` : shortLabel(availability);
  const marker = <Marker availability={availability} showsText={showsText} />;

  return (
    <>
      {isInteractive ? (
        <button
          type="button"
          className={styles.trigger}
          aria-label={accessibleLabel}
          title="Explains how this number was produced."
          onClick={() => setIsExplaining(true)}
        >
          {marker}
        </button>
      ) : (
        <span className={styles.marker} aria-label={accessibleLabel} role="img">
          {marker}
        </span>
      )}
      {isExplaining && (
        <AvailabilityExplainer
          availability={availability}
          metricName={metricName}
          season={season}
          notes={notes}
          onClose={() => setIsExplaining(false)}
        />
      )}
    </>
  );
}

function Marker({
  availability,
  showsText,
}: {
  readonly availability: Availability;
  readonly showsText: boolean;
}): JSX.Element | null {
  switch (availability) {
    case "full":
      return null;
    case "estimated":
      return showsText ? (
        <span className={clsx(styles.capsule, styles.capsuleEstimated)}>{badgeGlyph(availability)}</span>
      ) : (
        <span className={clsx(styles.dot, styles.dotEstimated)} />
      );
    case "partial":
      return showsText ? (
        <span className={clsx(styles.capsule, styles.capsulePartial)}>{badgeGlyph(availability)}</span>
      ) : (
        <span className={clsx(styles.dot, styles.dotPartial)} />
      );
    case "unavailable":
      // Compact call sites sit beside a value that is *already* the em dash, so the full glyph
      // would draw a second one right next to it — nothing to add there.
      return showsText ? <span className={styles.capsuleUnavailable}>{badgeGlyph(availability)}</span> : null;
  }
}
