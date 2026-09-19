/**
 * A coloured monogram capsule standing in for a team mark —
 * `ios/NBAStats/DesignSystem/Components.swift`'s `TeamBadge`, ported.
 *
 * The colour is derived from the bare abbreviation with the same fixed fold {@link PlayerAvatar}
 * uses for a player, so it is identical on every launch. Hardwood ships no league logos.
 * `TeamBadge` folds the bare abbreviation while `PlayerAvatar` folds `"player" + playerId` — two
 * different keys on purpose, so a player and the team badge sitting beside it in the same row
 * cannot collide on the palette by construction.
 */
import type { JSX } from "react";
import clsx from "clsx";
import { Text } from "./Text";
import type { HardwoodTextStyleName } from "../generated/tokens";
import { monogramColorVar } from "./valueColor";
import styles from "./TeamBadge.module.css";

export type TeamBadgeSize = "small" | "medium" | "large";

const SIZE_CLASS: Readonly<Record<TeamBadgeSize, string>> = {
  small: styles.small,
  medium: styles.medium,
  large: styles.large,
};

const TEXT_STYLE: Readonly<Record<TeamBadgeSize, HardwoodTextStyleName>> = {
  small: "tableHeader",
  medium: "statLabel",
  large: "widgetTitle",
};

export interface TeamBadgeProps {
  readonly abbreviation: string;
  /** The team's full name, for the accessible label — the badge itself always shows the bare
   * abbreviation. */
  readonly name?: string | null;
  readonly size?: TeamBadgeSize;
  readonly className?: string;
}

export function TeamBadge({ abbreviation, name, size = "medium", className }: TeamBadgeProps): JSX.Element {
  const color = monogramColorVar(abbreviation);
  return (
    <span
      className={clsx(styles.badge, SIZE_CLASS[size], className)}
      style={{ color, backgroundColor: `color-mix(in sRGB, ${color} 16%, transparent)` }}
      aria-label={name ?? abbreviation}
    >
      <Text as="span" style={TEXT_STYLE[size]} ariaHidden htmlStyle={{ color: "inherit" }}>
        {abbreviation}
      </Text>
    </span>
  );
}
