/**
 * A league rank, rendered as `#12` — `ios/NBAStats/DesignSystem/Components.swift`'s `RankChip`.
 *
 * Renders nothing at all — not an empty chip — for a rank that is absent or `<= 0`: `0` is the
 * reserved "no rank" sentinel throughout the contract (CONTRACT.md §6), never a real 0th place.
 */
import { Text } from "./Text";
import { formatOrdinal } from "./format";
import styles from "./RankChip.module.css";
import clsx from "clsx";

export interface RankChipProps {
  readonly rank: number | null | undefined;
  /** The field this rank is out of, for the accessible label only (`"ranked 12th of 482"`). */
  readonly outOf?: number | null;
}

export function RankChip({ rank, outOf }: RankChipProps): JSX.Element | null {
  if (rank === null || rank === undefined || rank <= 0) return null;
  const isTopTen = rank <= 10;
  const ordinal = formatOrdinal(rank);
  const label =
    outOf !== null && outOf !== undefined && outOf > 0
      ? `ranked ${ordinal} of ${outOf}`
      : `ranked ${ordinal}`;
  return (
    <span className={clsx(styles.chip, isTopTen ? styles.rankTopTen : styles.rank)} aria-label={label}>
      <Text style="tableHeader" tabularNums ariaHidden>{`#${rank}`}</Text>
    </span>
  );
}
