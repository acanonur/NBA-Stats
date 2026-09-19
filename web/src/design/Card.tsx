/**
 * The standard Hardwood card: surface fill, rounded corners, a hairline border, and — in light
 * mode only — a very small drop shadow (`ios/NBAStats/DesignSystem/Theme.swift`'s
 * `HardwoodCardModifier`, ported). Dark mode separates cards from the page by lightness instead
 * of a shadow, because a black shadow on a near-black page is invisible — `tokens.css` already
 * resolves `--hw-card-shadow(-raised)` to `none` under `prefers-color-scheme: dark`, so this
 * component does not need its own light/dark branch for that.
 *
 * See `Card.module.css`'s own docstring for why `box-shadow` is set here, inline, rather than as
 * a class in that stylesheet.
 */
import type { CSSProperties, ReactNode } from "react";
import clsx from "clsx";
import styles from "./Card.module.css";

export type CardPadding = "none" | "sm" | "md" | "lg";

const PADDING_CLASS: Readonly<Record<CardPadding, string>> = {
  none: styles.paddingNone,
  sm: styles.paddingSm,
  md: styles.paddingMd,
  lg: styles.paddingLg,
};

export interface CardProps {
  /** A raised card sits on `--hw-surface-raised` with the heavier of the two shadow tokens —
   * for a sheet, popover, or a chip that should read as sitting above the page. */
  readonly raised?: boolean;
  readonly padding?: CardPadding;
  readonly className?: string;
  readonly style?: CSSProperties;
  readonly children?: ReactNode;
}

/** Wraps `children` in the standard Hardwood card surface. */
export function Card({
  raised = false,
  padding = "md",
  className,
  style,
  children,
}: CardProps): JSX.Element {
  return (
    <div
      className={clsx(styles.card, raised && styles.raised, PADDING_CLASS[padding], className)}
      style={{ boxShadow: `var(${raised ? "--hw-card-shadow-raised" : "--hw-card-shadow"})`, ...style }}
    >
      {children}
    </div>
  );
}
