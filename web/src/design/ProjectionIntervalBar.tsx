/**
 * A `RangeBar` with the three labels the design puts beneath it: the low bound, the mark and the
 * projection in the middle, the high bound — `ios/NBAStats/DesignSystem/RangeBar.swift`'s
 * `LabelledRangeBar`, ported under the name WEB_DESIGN.md §7.4 gives it.
 *
 * Split out from `RangeBar` so the bar itself stays a pure drawing and this owns the wording,
 * which is where the honesty lives: the middle label **names** the mark (`season avg 27.1`)
 * rather than leaving a bare number a reader could mistake for a line.
 */
import type { CSSProperties } from "react";
import { Text } from "./Text";
import type { RangeBarModel, RangeBarVariant } from "./RangeBar";
import { RangeBar } from "./RangeBar";
import styles from "./RangeBar.module.css";

export interface ProjectionIntervalBarProps {
  readonly model: RangeBarModel;
  readonly accent: string;
  readonly lowText: string;
  readonly highText: string;
  readonly referenceLabel?: string | null;
  readonly referenceText?: string | null;
  readonly projectionText: string;
  readonly variant?: RangeBarVariant;
  readonly height?: number;
}

export function ProjectionIntervalBar({
  model,
  accent,
  lowText,
  highText,
  referenceLabel,
  referenceText,
  projectionText,
  variant = "tile",
  height = 26,
}: ProjectionIntervalBarProps): JSX.Element {
  const middleText =
    referenceLabel && referenceText
      ? `${referenceLabel} ${referenceText} · proj ${projectionText}`
      : `proj ${projectionText}`;

  const labelColorStyle: CSSProperties =
    variant === "broadsheet" ? { color: "var(--hw-bs-text-muted)" } : {};

  return (
    <div>
      <RangeBar model={model} accent={accent} height={height} variant={variant} />
      <div className={styles.labels}>
        <Text
          style="caption"
          tabularNums
          color={variant === "tile" ? "tertiary" : undefined}
          htmlStyle={labelColorStyle}
        >
          {lowText}
        </Text>
        <Text
          style="caption"
          tabularNums
          truncate
          color={variant === "tile" ? "tertiary" : undefined}
          className={styles.labelMiddle}
          htmlStyle={labelColorStyle}
        >
          {middleText}
        </Text>
        <Text
          style="caption"
          tabularNums
          color={variant === "tile" ? "tertiary" : undefined}
          htmlStyle={labelColorStyle}
        >
          {highText}
        </Text>
      </div>
    </div>
  );
}
