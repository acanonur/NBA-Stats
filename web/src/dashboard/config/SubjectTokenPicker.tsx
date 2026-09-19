/**
 * The well-known `$favorite_player` / `$featured_team` / … tokens `contracts/presets.json`
 * ships as `subjectTokens` — `catalog.py::_validate_field` accepts one of these anywhere a
 * `player`, `team` or `subject` config value is expected (not only on `subject` fields), so
 * every one of those field editors offers this picker alongside its own id lookup.
 */
import type { JSX } from "react";
import { PRESETS_DOCUMENT } from "../../generated/contracts";
import { Text } from "../../design/Text";
import styles from "./field.module.css";

const SUBJECT_TOKENS = PRESETS_DOCUMENT.subjectTokens as ReadonlyArray<{
  readonly token: string;
  readonly summary: string;
}>;

export interface SubjectTokenPickerProps {
  readonly value: string | number | null;
  readonly onPick: (token: string) => void;
  /** Restricts the offered tokens to ones that make sense for this field
   * (`$favorite_player`/`$featured_player`/`$league_leader` for a player field, the `_team`
   * pair for a team field; unfiltered for a genuine `subject` field, which can be either). */
  readonly kind: "player" | "team" | "any";
}

export function SubjectTokenPicker({ value, onPick, kind }: SubjectTokenPickerProps): JSX.Element {
  const tokens = SUBJECT_TOKENS.filter((entry) => {
    if (kind === "any") return true;
    return entry.token.includes(kind === "player" ? "player" : "team");
  });
  return (
    <div className={styles.chipList}>
      {tokens.map((entry) => (
        <button
          key={entry.token}
          type="button"
          className={styles.chip}
          title={entry.summary}
          aria-pressed={value === entry.token}
          onClick={() => onPick(entry.token)}
        >
          <Text style="caption" as="span">
            {entry.token}
          </Text>
        </button>
      ))}
    </div>
  );
}
