import type { JSX } from "react";
import { Text } from "../../design/Text";
import type { FieldEditorProps } from "./types";
import styles from "./field.module.css";

export function BoolField({ spec, value, onChange }: FieldEditorProps<boolean>): JSX.Element {
  return (
    <div className={styles.field}>
      <label className={styles.checkboxRow}>
        <input
          type="checkbox"
          checked={value ?? Boolean(spec.default)}
          onChange={(event) => onChange(event.target.checked)}
        />
        <Text style="tableHeader" as="span">
          {spec.label}
        </Text>
      </label>
      {spec.help && (
        <Text as="p" style="caption" color="secondary">
          {spec.help}
        </Text>
      )}
    </div>
  );
}
