/**
 * A 0-100 percentile track with a marker — `ios/NBAStats/DesignSystem/PercentileBar.swift`'s
 * `PercentileBar`, ported.
 *
 * Meaning is carried by **position and label**, never by hue alone: the numeric label is on by
 * default, three quartile ticks give the eye a fixed reference, and the marker is a
 * high-contrast bar with a surface-coloured outline so it reads on any tint.
 */
import type { CSSProperties } from "react";
import { Text } from "./Text";
import { formatOrdinal } from "./format";
import styles from "./PercentileBar.module.css";

export interface PercentileBarProps {
  /** A fraction in `[0, 1]`, or an already-scaled `[0, 100]` value — both are accepted, matching
   * the Swift original, so a caller holding a pre-scaled number does not silently peg to the
   * top. `null` draws an empty track with no marker. */
  readonly percentile: number | null;
  /** A CSS colour value — typically `var(--hw-chart-N)` from {@link chartColorVar} or a
   * comparison colour from {@link comparisonColorVar}. */
  readonly tint: string;
  readonly showsLabel?: boolean;
  readonly trackHeight?: number;
}

function resolvedFraction(percentile: number | null): number | null {
  if (percentile === null || !Number.isFinite(percentile)) return null;
  const normalized = percentile > 1 ? percentile / 100 : percentile;
  return Math.min(Math.max(normalized, 0), 1);
}

const QUARTILES = [0.25, 0.5, 0.75];

export function PercentileBar({
  percentile,
  tint,
  showsLabel = true,
  trackHeight = 8,
}: PercentileBarProps): JSX.Element {
  const fraction = resolvedFraction(percentile);
  const rounded = fraction === null ? null : Math.round(fraction * 100);
  const labelText = rounded === null ? "—" : formatOrdinal(rounded);
  const accessibleLabel = rounded === null ? "Percentile not available" : `${formatOrdinal(rounded)} percentile`;
  const markerHeight = trackHeight + 8;

  return (
    <div className={styles.row} role="img" aria-label={accessibleLabel}>
      <div
        className={styles.track}
        style={{ height: markerHeight, "--hw-marker-color": tint } as CSSProperties}
      >
        <div className={styles.trackFill} style={{ height: trackHeight }} />
        {QUARTILES.map((stop) => (
          <div
            key={stop}
            className={styles.quartileTick}
            style={{ left: `${stop * 100}%`, height: trackHeight }}
          />
        ))}
        {fraction !== null && (
          <>
            <div
              className={styles.fill}
              style={{ width: `max(${fraction * 100}%, ${trackHeight}px)`, height: trackHeight }}
            />
            <div
              className={styles.marker}
              style={{ left: `${fraction * 100}%`, height: markerHeight }}
            />
          </>
        )}
      </div>
      {showsLabel && (
        <Text style="tableHeader" tabularNums color={fraction === null ? "tertiary" : "primary"} className={styles.label}>
          {labelText}
        </Text>
      )}
    </div>
  );
}
