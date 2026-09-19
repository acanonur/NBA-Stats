/**
 * `ProjectionIntervalBar` dressed for the broadsheet — the design's colour logic (cyan above the
 * reference mark, magenta below, neutral with no mark) carried over without its betting meaning
 * (`ios/NBAStats/DesignSystem/RangeBar.swift`'s own docstring, ported).
 */
import type { JSX } from "react";
import type { RangeBarModel } from "../RangeBar";
import { ProjectionIntervalBar } from "../ProjectionIntervalBar";

export type BroadsheetRangeBarAccent = "above" | "below" | "muted";

const ACCENT_VAR: Readonly<Record<BroadsheetRangeBarAccent, string>> = {
  above: "var(--hw-bs-accent-above)",
  below: "var(--hw-bs-accent-below)",
  muted: "var(--hw-bs-text-muted)",
};

export interface BroadsheetRangeBarProps {
  readonly model: RangeBarModel;
  /** `"none"` when `model.reference` is absent — never guess a direction for a comparison this
   * component cannot make. */
  readonly accent: BroadsheetRangeBarAccent;
  readonly lowText: string;
  readonly highText: string;
  readonly referenceLabel?: string | null;
  readonly referenceText?: string | null;
  readonly projectionText: string;
}

export function BroadsheetRangeBar({
  model,
  accent,
  lowText,
  highText,
  referenceLabel,
  referenceText,
  projectionText,
}: BroadsheetRangeBarProps): JSX.Element {
  return (
    <ProjectionIntervalBar
      model={model}
      accent={ACCENT_VAR[accent]}
      variant="broadsheet"
      lowText={lowText}
      highText={highText}
      referenceLabel={referenceLabel}
      referenceText={referenceText}
      projectionText={projectionText}
    />
  );
}
