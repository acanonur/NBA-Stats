/**
 * The broadsheet's hairline rule and its dot-leader label/value row —
 * `ios/NBAStats/DesignSystem/BroadsheetTheme.swift`'s `BroadsheetRule` and
 * `BroadsheetLeaderRow`, ported.
 */
import type { JSX } from "react";
import clsx from "clsx";

export interface BroadsheetRuleProps {
  /** A heavier rule for a section boundary; the default (thin) rule is what separates ordinary
   * rows. */
  readonly heavy?: boolean;
  readonly className?: string;
}

export function BroadsheetRule({ heavy = false, className }: BroadsheetRuleProps): JSX.Element {
  return <hr className={clsx(heavy ? "hw-bs-rule-heavy" : "hw-bs-rule", className)} />;
}

export type BroadsheetRowValueColor = "text" | "above" | "below" | "muted";

const VALUE_COLOR_VAR: Readonly<Record<BroadsheetRowValueColor, string>> = {
  text: "var(--hw-bs-text)",
  above: "var(--hw-bs-accent-above)",
  below: "var(--hw-bs-accent-below)",
  muted: "var(--hw-bs-text-muted)",
};

export interface BroadsheetRowProps {
  readonly label: string;
  readonly value: string;
  readonly valueColor?: BroadsheetRowValueColor;
  readonly className?: string;
}

/** A label and a value separated by dot leaders, the way a broadsheet sets a table of contents.
 * The leader itself is drawn (a repeating background image), never typed as a run of periods —
 * a literal string of dots would have a screen reader read each one individually. */
export function BroadsheetRow({ label, value, valueColor = "text", className }: BroadsheetRowProps): JSX.Element {
  return (
    <div className={clsx("hw-bs-row", className)} aria-label={`${label}, ${value}`}>
      <span className="hw-bs-row-label" aria-hidden>
        {label}
      </span>
      <span className="hw-bs-row-leader" aria-hidden />
      <span className="hw-bs-row-value" style={{ color: VALUE_COLOR_VAR[valueColor] }} aria-hidden>
        {value}
      </span>
    </div>
  );
}
