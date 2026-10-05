/**
 * Marks whether a tile is showing live data or a cached copy —
 * `ios/NBAStats/DesignSystem/Components.swift`'s `StalenessDot`.
 *
 * Stale is a hollow ring, fresh is a filled dot: the state is legible without colour, which
 * matters because both use warning/positive hues that a colourblind reader — or a greyscale
 * screenshot — cannot rely on alone.
 */
import { Text } from "./Text";
import { formatRelative } from "./format";
import styles from "./StalenessDot.module.css";

export interface StalenessDotProps {
  readonly isStale: boolean;
  /** When the payload behind this tile was generated. Accepts an ISO timestamp string (as the
   * wire sends `generatedAt`) or a `Date`. */
  readonly updatedAt?: string | Date | null;
  readonly showsText?: boolean;
}

function toDate(value: string | Date | null | undefined): Date | null {
  if (!value) return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function StalenessDot({
  isStale,
  updatedAt,
  showsText = false,
}: StalenessDotProps): JSX.Element {
  const date = toDate(updatedAt);
  const relativeText = date ? formatRelative(date) : null;
  const accessibilityText = relativeText
    ? isStale
      ? `Showing cached data, last updated ${relativeText}`
      : `Updated ${relativeText}`
    : isStale
      ? "Showing cached data"
      : "Up to date";

  return (
    <span className={styles.row} role="status" aria-label={accessibilityText}>
      <span className={isStale ? styles.stale : styles.fresh} aria-hidden />
      {showsText && relativeText && (
        <Text style="caption" color={isStale ? "warning" : "tertiary"} ariaHidden>
          {relativeText}
        </Text>
      )}
    </span>
  );
}
