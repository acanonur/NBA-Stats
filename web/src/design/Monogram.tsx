/**
 * The player-portrait fallback mark: initials on a coloured circle —
 * `ios/NBAStats/DesignSystem/PlayerAvatar.swift`'s `initials(for:)` and its inline monogram
 * view, split out here so `PlayerAvatar` owns only the network/crossfade concern and this owns
 * the initials rule and the circle itself.
 */
import type { JSX } from "react";
import styles from "./Monogram.module.css";

export interface MonogramNameParts {
  readonly firstName?: string | null;
  readonly lastName?: string | null;
  readonly fullName?: string | null;
}

/** Grapheme clusters, not UTF-16 code units or bare code points: "Dončić" must not split a
 * precomposed accented letter, and `Intl.Segmenter` is the one JS primitive that agrees with
 * Swift's `Character` on where a grapheme boundary falls. */
const GRAPHEME_SEGMENTER = new Intl.Segmenter(undefined, { granularity: "grapheme" });

function graphemes(text: string): string[] {
  return Array.from(GRAPHEME_SEGMENTER.segment(text), (entry) => entry.segment);
}

/** The first letter or digit in `text`, skipping punctuation such as a leading quote in
 * `"J.R."`. `null` for an absent or all-punctuation string. */
function leadingLetter(text: string | null | undefined): string | null {
  if (!text) return null;
  for (const grapheme of graphemes(text)) {
    if (/\p{L}|\p{N}/u.test(grapheme)) return grapheme;
  }
  return null;
}

// JS's `\s` already matches U+00A0 (non-breaking space) per the ECMAScript WhiteSpace
// production, alongside space/tab/CR/LF — a superset of the separator set
// `PlayerAvatar.swift`'s name split names explicitly, chosen here instead of spelling out a
// non-breaking space as a literal character in source (which a mis-set editor encoding can
// silently turn into the wrong byte sequence).
const WORD_SEPARATOR = /\s+/u;

/** Takes the first *word* and the second, never the last: "Gary Payton II" is "GP", not "GI". A
 * single-word name ("Nenê") gets the one letter. */
function initialsFromFullName(name: string): string {
  const words = name.split(WORD_SEPARATOR).filter((word) => word.length > 0).slice(0, 2);
  const letters = words.map((word) => leadingLetter(word)).filter((letter): letter is string => letter !== null);
  if (letters.length === 0) return "?";
  if (letters.length === 1) return letters[0].toUpperCase();
  return letters.join("").toUpperCase();
}

/**
 * One or two letters standing in for a player. `firstName`/`lastName` win when the server sent
 * them, because they are already split correctly for names the space-separated parse gets wrong;
 * only when at least one is missing does this fall back to parsing `fullName`.
 */
export function initialsFor(parts: MonogramNameParts): string {
  const first = leadingLetter(parts.firstName);
  const last = leadingLetter(parts.lastName);
  if (first !== null && last !== null) {
    return (first + last).toUpperCase();
  }
  return initialsFromFullName(parts.fullName ?? "");
}

export interface MonogramProps {
  readonly initials: string;
  /** The CSS colour (typically {@link monogramColorVar}'s result) this player or team folds to. */
  readonly color: string;
  readonly diameter: number;
  /** `true` while a headshot request for the same subject is still in flight — dims the glyphs
   * without changing any geometry, so a photo's arrival is a cross-fade rather than a reflow. */
  readonly dimmed?: boolean;
  readonly className?: string;
}

export function Monogram({ initials, color, diameter, dimmed = false, className }: MonogramProps): JSX.Element {
  return (
    <div
      className={`${styles.circle}${className ? ` ${className}` : ""}`}
      style={{ width: diameter, height: diameter, backgroundColor: `color-mix(in sRGB, ${color} 16%, transparent)` }}
    >
      <span
        className={styles.initials}
        style={{
          color,
          opacity: dimmed ? 0.45 : 1,
          fontSize: diameter * 0.4,
        }}
      >
        {initials}
      </span>
    </div>
  );
}
