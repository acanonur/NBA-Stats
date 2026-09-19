/**
 * The eight Hardwood text styles, applied to any run of text — the TypeScript counterpart of
 * `hardwoodText(_:color:monospacedDigits:)` in `ios/NBAStats/DesignSystem/Typography.swift`.
 *
 * There is exactly one way to get Hardwood typography onto the page: `<Text style="...">`.
 * Nothing outside `generated/tokens.css` may declare a `font-size`, `font-weight`, or
 * `letter-spacing` (WEB_DESIGN.md §7.9's stylelint rule polices colour and shadow the same way;
 * this component is what makes the equivalent true for type by convention, since there is no
 * mechanical check for "used the token" the way there is for "used a raw hex").
 */
import type { CSSProperties, ReactNode } from "react";
import clsx from "clsx";
import type { HardwoodTextStyleName } from "../generated/tokens";
import styles from "./Text.module.css";

/** A semantic colour token a caller can apply over a style's own default — never a raw colour. */
export type TextColorToken =
  | "primary"
  | "secondary"
  | "tertiary"
  | "positive"
  | "negative"
  | "neutral"
  | "warning"
  | "selection";

const COLOR_CLASS: Readonly<Record<TextColorToken, string>> = {
  primary: styles.colorPrimary,
  secondary: styles.colorSecondary,
  tertiary: styles.colorTertiary,
  positive: styles.colorPositive,
  negative: styles.colorNegative,
  neutral: styles.colorNeutral,
  warning: styles.colorWarning,
  selection: styles.colorSelection,
};

/** The handful of elements a run of Hardwood text is ever wrapped in. Kept small deliberately —
 * this is a typography primitive, not a general-purpose polymorphic-component escape hatch. */
export type TextElement = "span" | "div" | "p" | "dt" | "dd" | "label";

export interface TextProps {
  /** One of the eight Hardwood text styles (`generated/tokens.ts`'s `HardwoodTextStyleName`). */
  readonly style: HardwoodTextStyleName;
  /** Overrides the style's own default colour token. */
  readonly color?: TextColorToken;
  /** Forces `font-variant-numeric: tabular-nums` on a style that does not carry it by default
   * (`statLabel`, `tableHeader`, `caption`, `sectionTitle`, `widgetTitle`) — the `.hw-tnum`
   * opt-in WEB_DESIGN.md §7.4 describes, for a non-numeric style that still has to line up in a
   * numeric column. A style that is already numeric (`displayValue`, `statValue`, `tableCell`)
   * ignores this prop; it cannot be turned *off* per instance, because no call site should ever
   * want a jittering column of stat values. */
  readonly tabularNums?: boolean;
  /** Single-line with an ellipsis, for a name or label that must not wrap a tile's height. */
  readonly truncate?: boolean;
  /** The DOM element this renders as. Defaults to `"span"` so `<Text>` composes inline with
   * sibling content by default, matching how most call sites use it (a value beside a badge, a
   * label beside a chip). */
  readonly as?: TextElement;
  readonly className?: string;
  readonly title?: string;
  readonly id?: string;
  /** An escape hatch for the rare style a token/class cannot express — e.g. a broadsheet colour
   * this package's semantic `color` tokens do not name. Named `htmlStyle` rather than `style`
   * because `style` is already this component's own text-style-name prop. */
  readonly htmlStyle?: CSSProperties;
  /** Hides this run from assistive technology — for a value repeated inside a parent that
   * already carries its own `aria-label` (every chip in this package follows that pattern). */
  readonly ariaHidden?: boolean;
  readonly children?: ReactNode;
}

/** Renders one run of text in a Hardwood type style. See the module docstring for why this is
 * the only sanctioned way to set type in this codebase. */
export function Text({
  style,
  color,
  tabularNums = false,
  truncate = false,
  as = "span",
  className,
  title,
  id,
  ariaHidden,
  htmlStyle,
  children,
}: TextProps): JSX.Element {
  const Component = as;
  return (
    <Component
      id={id}
      title={title}
      aria-hidden={ariaHidden}
      style={htmlStyle}
      className={clsx(
        styles.text,
        styles[style],
        color && COLOR_CLASS[color],
        tabularNums && styles.tabularNums,
        truncate && styles.truncate,
        className,
      )}
    >
      {children}
    </Component>
  );
}
