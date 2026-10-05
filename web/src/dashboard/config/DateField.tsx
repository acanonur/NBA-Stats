import type { JSX } from "react";
import { Text } from "../../design/Text";
import type { FieldEditorProps } from "./types";
import styles from "./field.module.css";

/** An ISO date or the `"latest"` token (`scoreboard`/`daily_movers`/`projection_board`'s
 * `date` field). */
export function DateField({ spec, value, onChange }: FieldEditorProps<string | null>): JSX.Element {
  const isLatest = (value ?? spec.default) === "latest";
  return (
    <div className={styles.field}>
      <label>
        <Text style="tableHeader" as="span">
          {spec.label}
        </Text>
      </label>
      <label className={styles.checkboxRow}>
        <input type="checkbox" checked={isLatest} onChange={(event) => onChange(event.target.checked ? "latest" : "")} />
        <Text style="tableCell" as="span">
          Most recent completed slate
        </Text>
      </label>
      {!isLatest && (
        <input
          className={styles.control}
          type="date"
          value={value ?? ""}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
      {spec.help && (
        <Text as="p" style="caption" color="secondary">
          {spec.help}
        </Text>
      )}
    </div>
  );
}
