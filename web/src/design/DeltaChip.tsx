/**
 * A signed change against a baseline, coloured by whether the change is an improvement —
 * `ios/NBAStats/DesignSystem/Components.swift`'s `DeltaChip`.
 *
 * The arrow carries the same meaning as the colour, so the chip still reads correctly without
 * hue perception. `higherIsBetter: null` (unknown direction — a metric with no catalog
 * direction, or a shape such as `fantasy_draft_board`'s volume columns where neither direction is
 * a virtue) renders **neutral grey**, never a guessed direction: {@link comparisonOutcome}
 * refuses to colour a comparison it cannot actually make.
 */
import type { MetricFormat } from "../generated/contracts";
import { Text } from "./Text";
import { formatMagnitude } from "./format";
import { comparisonOutcome, comparisonColorVar } from "./valueColor";
import styles from "./DeltaChip.module.css";
import clsx from "clsx";

export interface DeltaChipProps {
  readonly delta: number | null | undefined;
  readonly higherIsBetter: boolean | null | undefined;
  readonly format: MetricFormat;
  /** A trailing word or two of context, dimmed — e.g. "this season". */
  readonly caption?: string | null;
}

const DELTA_EPSILON = 1e-9;

function ArrowGlyph({ direction }: { readonly direction: "up" | "down" }): JSX.Element {
  // A minimal stand-in for SF Symbols' `arrow.up.right` / `arrow.down.right` — this app ships no
  // icon font or icon library (WEB_DESIGN.md §7.9), so every glyph in this design package is a
  // few lines of inline SVG.
  const rotation = direction === "up" ? 0 : 90;
  return (
    <svg width="9" height="9" viewBox="0 0 10 10" aria-hidden focusable="false">
      <g transform={`rotate(${rotation} 5 5)`}>
        <path
          d="M2.4 7.6 7.6 2.4M4.2 2.4H7.6V5.8"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.3"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </g>
    </svg>
  );
}

export function DeltaChip({
  delta,
  higherIsBetter,
  format,
  caption,
}: DeltaChipProps): JSX.Element | null {
  if (delta === null || delta === undefined || !Number.isFinite(delta)) return null;
  if (Math.abs(delta) <= DELTA_EPSILON) return null;

  const outcome = comparisonOutcome(delta, higherIsBetter);
  const colorVar = comparisonColorVar(outcome);
  const magnitude = formatMagnitude(delta, format);
  const direction = delta > 0 ? "up" : "down";
  const label = `${direction} ${magnitude}${caption ? ` ${caption}` : ""}`;

  return (
    <span
      className={clsx(styles.chip)}
      style={{
        color: colorVar,
        backgroundColor: `color-mix(in sRGB, ${colorVar} 12%, transparent)`,
      }}
      aria-label={label}
    >
      <ArrowGlyph direction={direction} />
      <Text style="tableHeader" tabularNums ariaHidden>
        {magnitude}
      </Text>
      {caption && (
        <Text style="caption" color="tertiary" ariaHidden>
          {caption}
        </Text>
      )}
    </span>
  );
}
