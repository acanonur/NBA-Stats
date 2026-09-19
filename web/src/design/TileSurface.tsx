/**
 * The surface every widget-size placeholder draws on
 * (`ios/NBAStats/DesignSystem/StateViews.swift`'s private `TileSurface`, made public here
 * because — unlike in the SwiftUI file, where every state view lives beside it — this design
 * package's `StateViews.tsx` is one of several call sites, and `PENDING.txt`'s eventual
 * `PendingTile` usage is a widget-level concern too).
 *
 * Unlike `Card`, this never carries a shadow — the Swift original applies no `.shadow()`
 * modifier to its `TileSurface` either; a shadow belongs to a card floating over the page, and a
 * placeholder standing in for a grid tile that will have no shadow of its own should not gain
 * one just because it is temporarily empty.
 *
 * `minHeight` comes from `generated/tokens.ts`'s `ESTIMATED_HEIGHT`, keyed by the widget's own
 * size — never a literal — so a loading skeleton never changes the height of the grid row it is
 * standing in for and a real payload never "pops" the tile taller when it arrives.
 */
import type { ReactNode } from "react";
import clsx from "clsx";
import type { WidgetSizeKey } from "../generated/tokens";
import { ESTIMATED_HEIGHT } from "../generated/tokens";
import cardStyles from "./Card.module.css";
import styles from "./TileSurface.module.css";

export interface TileSurfaceProps {
  readonly size: WidgetSizeKey;
  readonly className?: string;
  readonly children?: ReactNode;
}

export function TileSurface({ size, className, children }: TileSurfaceProps): JSX.Element {
  return (
    <div
      className={clsx(cardStyles.card, cardStyles.paddingMd, styles.tile, className)}
      style={{ minHeight: ESTIMATED_HEIGHT[size] }}
    >
      {children}
    </div>
  );
}
