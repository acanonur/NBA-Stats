/**
 * A player's face — or, when there is not one, something that looks like it was designed rather
 * than something that failed. `ios/NBAStats/DesignSystem/PlayerAvatar.swift`, ported.
 *
 * `PlayerRef.headshotUrl` points at the NBA's public CDN, which is not reachable from every
 * environment, is missing for a good share of historical players, and even when present arrives
 * at some unknown moment. So this component is written around the same assumption the Swift
 * original states outright: **the photo is the exception, not the rule.**
 *
 *   - the circle is laid out at its final diameter before any request is made, so a photo
 *     landing — or never landing — never moves the row it sits in;
 *   - the loading and the failed state draw the *same* monogram on the *same* tinted circle, so
 *     a slow CDN and a missing headshot degrade into the identical, deliberate-looking mark, and
 *     the only thing that changes when a request fails is that the initials stop being dimmed;
 *   - a missing or blank `headshotUrl` skips the network entirely and goes straight to the
 *     monogram.
 *
 * There is no broken-image glyph and no spinner anywhere in this component: a spinner that never
 * resolves is worse than a monogram that was right all along.
 *
 * The colour is derived by folding `"player" + playerId` (see `valueColor.ts`'s
 * `monogramColorVar` and its own docstring on why this is a *different* key from
 * {@link TeamBadge}'s bare abbreviation) so a given player is the same colour on every launch and
 * every device.
 */
import { useState, type JSX } from "react";
import clsx from "clsx";
import { Monogram, initialsFor } from "./Monogram";
import { monogramColorVar } from "./valueColor";
import styles from "./PlayerAvatar.module.css";

export type PlayerAvatarSize = "small" | "medium" | "large";

const DIAMETER: Readonly<Record<PlayerAvatarSize, number>> = {
  small: 24,
  medium: 44,
  large: 88,
};

export interface PlayerAvatarPlayer {
  readonly playerId: number;
  readonly name: string;
  readonly firstName?: string | null;
  readonly lastName?: string | null;
  readonly headshotUrl?: string | null;
}

export interface PlayerAvatarProps {
  readonly player: PlayerAvatarPlayer;
  /** `"small"` (24px, inline in a dense row) · `"medium"` (44px, a full list row) · `"large"`
   * (88px, a detail header). Fixed points, deliberately **not** scaled by font size — a column of
   * leaderboard rows must stay aligned even as a reader's text size grows (unlike {@link
   * TeamBadge}, which does scale; see that component's own docstring for why the two disagree). */
  readonly size?: PlayerAvatarSize;
  readonly className?: string;
}

type PhotoState = "none" | "loading" | "loaded" | "failed";

function resolvedUrl(raw: string | null | undefined): string | null {
  const trimmed = raw?.trim();
  return trimmed ? trimmed : null;
}

export function PlayerAvatar({ player, size = "medium", className }: PlayerAvatarProps): JSX.Element {
  const url = resolvedUrl(player.headshotUrl);
  const [state, setState] = useState<PhotoState>(url ? "loading" : "none");
  // Resets when the URL itself changes — a list virtualising rows can reuse one component
  // instance across different players without remounting it. Adjusted during render (React's
  // documented pattern for "reset state when a prop changes") rather than in a `useEffect`: an
  // effect would let the previous player's photo (or its dimmed monogram) paint for one extra
  // frame before the reset runs, which is exactly the reflow-on-arrival this component exists to
  // avoid.
  const [urlAtLastRender, setUrlAtLastRender] = useState(url);
  if (url !== urlAtLastRender) {
    setUrlAtLastRender(url);
    setState(url ? "loading" : "none");
  }

  const diameter = DIAMETER[size];
  const initials = initialsFor({ firstName: player.firstName, lastName: player.lastName, fullName: player.name });
  const color = monogramColorVar(`player${player.playerId}`);
  const showsPhoto = state === "loading" || state === "loaded";

  return (
    <span
      className={clsx(styles.wrapper, className)}
      style={{ width: diameter, height: diameter }}
      aria-label={player.name}
    >
      <Monogram initials={initials} color={color} diameter={diameter} dimmed={state === "loading"} />
      {showsPhoto && url && (
        <img
          src={url}
          alt=""
          loading="lazy"
          className={styles.photo}
          style={{ opacity: state === "loaded" ? 1 : 0 }}
          onLoad={() => setState("loaded")}
          onError={() => setState("failed")}
        />
      )}
    </span>
  );
}
