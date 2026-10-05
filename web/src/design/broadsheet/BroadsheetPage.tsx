/**
 * The broadsheet page shell and its uppercase section kicker —
 * `ios/NBAStats/DesignSystem/BroadsheetTheme.swift`'s page background and `BroadsheetKicker`,
 * ported. Imports `broadsheet.css` once, so any page that renders a `BroadsheetPage` gets the
 * whole family's furniture without every call site remembering to import the stylesheet itself.
 */
import type { JSX, ReactNode } from "react";
import clsx from "clsx";
import "./broadsheet.css";

export interface BroadsheetPageProps {
  /** Sets `[data-presentation="broadsheet"]` on the page root — this is what `theme.css`'s
   * `.hw-grid` rule (WEB_DESIGN.md §7.5) reads to collapse a dashboard's grid to one column with
   * the broadsheet's wider gap, so a `projection_board` rendered inside this page automatically
   * lays out as a single editorial column rather than the tile grid. */
  readonly children?: ReactNode;
  readonly className?: string;
}

export function BroadsheetPage({ children, className }: BroadsheetPageProps): JSX.Element {
  return (
    <div className={clsx("hw-bs-page", className)} data-presentation="broadsheet">
      <div className="hw-bs-column">{children}</div>
    </div>
  );
}

export interface BroadsheetKickerProps {
  readonly children: ReactNode;
  readonly className?: string;
}

/** The uppercase, letter-spaced label above a section or a column. */
export function BroadsheetKicker({ children, className }: BroadsheetKickerProps): JSX.Element {
  return <p className={clsx("hw-bs-kicker", className)}>{children}</p>;
}
