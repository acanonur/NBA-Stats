/**
 * Renders one `MetricValue` with the right typography, the era treatment, and optional rank and
 * delta chips — `ios/NBAStats/DesignSystem/Components.swift`'s `StatValueView`, ported.
 *
 * The server's `displayValue` is shown verbatim — it already encodes the metric's format and the
 * em dash for a stat that never existed — so this component never turns a missing number into a
 * zero. This is deliberately **the** place a `MetricValue` becomes pixels: every one of the
 * sixteen widgets that shows a headline number, a supporting stat, or a table cell should reach
 * for this component rather than hand-rolling `<Text>{value.displayValue}</Text>` beside its own
 * copy of the availability badge, rank chip and delta chip wiring (WEB_DESIGN.md's "this is the
 * correctness surface for all sixteen widgets" — there must be exactly one interpretation of
 * "render a MetricValue").
 */
import clsx from "clsx";
import type { MetricDescriptor, MetricValue } from "../api/types";
import { Text } from "./Text";
import type { HardwoodTextStyleName } from "../generated/tokens";
import type { MetricFormat } from "../generated/contracts";
import { formatValue } from "./format";
import { valueTreatmentClassName } from "./availability";
import { AvailabilityBadge } from "./AvailabilityBadge";
import { RankChip } from "./RankChip";
import { DeltaChip } from "./DeltaChip";
import styles from "./StatValue.module.css";

export type StatValueStyle = "display" | "stat" | "cell";

const VALUE_TEXT_STYLE: Readonly<Record<StatValueStyle, HardwoodTextStyleName>> = {
  display: "displayValue",
  stat: "statValue",
  cell: "tableCell",
};

const LABEL_TEXT_STYLE: Readonly<Record<StatValueStyle, HardwoodTextStyleName>> = {
  display: "statLabel",
  stat: "statLabel",
  cell: "tableHeader",
};

const SPACING_CLASS: Readonly<Record<StatValueStyle, string>> = {
  display: styles.spacingDisplay,
  stat: styles.spacingStat,
  cell: styles.spacingCell,
};

export interface StatValueProps {
  readonly value: MetricValue;
  /** The metric's catalog entry, when the caller has one. Supplies the label text, the fallback
   * format for an (unexpected) empty `displayValue`, and `higherIsBetter` for the delta chip. */
  readonly descriptor?: MetricDescriptor | null;
  readonly style?: StatValueStyle;
  readonly showsLabel?: boolean;
  readonly showsRank?: boolean;
  readonly showsDelta?: boolean;
  /** The season this value is from, threaded through to the availability explainer so its copy
   * can say "the 1971-72 season" rather than "this era". */
  readonly season?: string | null;
  readonly align?: "leading" | "trailing";
  readonly className?: string;
}

export function StatValue({
  value,
  descriptor,
  style = "display",
  showsLabel = true,
  showsRank = true,
  showsDelta = false,
  season,
  align = "leading",
  className,
}: StatValueProps): JSX.Element {
  // `MetricDescriptor.format` is typed as a bare `string` (api/types.ts: it is transcribed
  // verbatim from `contracts/metrics.json`, which this file does not re-validate against the
  // `MetricFormat` union at the type level). Every descriptor the server actually sends carries
  // one of the eight `MetricFormat` values; this cast documents that assumption at the one
  // place it is needed rather than threading a validated type through every caller.
  const format = (descriptor?.format as MetricFormat | undefined) ?? "decimal1";
  const displayText = value.displayValue.length > 0 ? value.displayValue : formatValue(value.value, format);

  const higherIsBetter = descriptor?.higherIsBetter ?? null;
  // Written as an explicit null/undefined check rather than `(value.rank ?? 0) > 0`: the lint
  // rule that keeps a nullable statistic from silently becoming a rendered zero cannot tell a
  // *comparison* apart from a *render*, so it flags any `?? 0` on a nullable number on sight —
  // rightly, since the shape is identical even though nothing here is displayed.
  const rankVisible = showsRank && value.rank !== null && value.rank !== undefined && value.rank > 0;
  const deltaVisible = showsDelta && value.delta !== null && value.delta !== undefined;
  const hasChips = rankVisible || deltaVisible;

  return (
    <div
      className={clsx(
        styles.root,
        align === "leading" ? styles.alignLeading : styles.alignTrailing,
        SPACING_CLASS[style],
        className,
      )}
    >
      <span className={styles.valueRow}>
        <Text
          style={VALUE_TEXT_STYLE[style]}
          tabularNums
          className={clsx(styles.value, valueTreatmentClassName(value.availability))}
        >
          {displayText}
        </Text>
        <AvailabilityBadge
          availability={value.availability}
          showsText={style !== "cell"}
          isInteractive={style !== "cell"}
          metricName={descriptor?.name}
          season={season}
        />
      </span>
      {showsLabel && descriptor && (
        <Text style={LABEL_TEXT_STYLE[style]} className={styles.label}>
          {descriptor.shortName}
        </Text>
      )}
      {hasChips && (
        <span className={styles.chipRow}>
          {showsRank && <RankChip rank={value.rank} />}
          {showsDelta && (
            <DeltaChip delta={value.delta} higherIsBetter={higherIsBetter} format={format} />
          )}
        </span>
      )}
    </div>
  );
}
