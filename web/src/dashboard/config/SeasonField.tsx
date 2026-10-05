/** A season string (`"2025-26"`) or one of the request tokens `season.ts`'s `seasonDisplay`
 * already knows how to show — `"latest"`, `"career"`, `"all_time"`. No network call: unlike
 * `player`/`team`, the season axis has no lookup endpoint this build needs, and a free-text
 * field with the token buttons covers every real use (`contracts/widgets.json` never restricts
 * `season` to an enum). */
import type { JSX } from "react";
import { Text } from "../../design/Text";
import type { FieldEditorProps } from "./types";
import styles from "./field.module.css";

const TOKENS = ["latest", "career", "all_time"] as const;

export function SeasonField({ spec, value, onChange }: FieldEditorProps<string | null>): JSX.Element {
  return (
    <div className={styles.field}>
      <label>
        <Text style="tableHeader" as="span">
          {spec.label}
        </Text>
      </label>
      <input
        className={styles.control}
        type="text"
        placeholder="e.g. 2025-26"
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value)}
      />
      <div className={styles.chipList}>
        {TOKENS.map((token) => (
          <button
            key={token}
            type="button"
            className={styles.chip}
            onClick={() => onChange(token)}
            aria-pressed={value === token}
          >
            {token}
          </button>
        ))}
      </div>
      {spec.help && (
        <Text as="p" style="caption" color="secondary">
          {spec.help}
        </Text>
      )}
    </div>
  );
}
