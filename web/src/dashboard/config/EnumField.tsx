import type { JSX } from "react";
import { Text } from "../../design/Text";
import type { FieldEditorProps } from "./types";
import styles from "./field.module.css";

export function EnumField({ spec, value, onChange }: FieldEditorProps<string | null>): JSX.Element {
  return (
    <div className={styles.field}>
      <label>
        <Text style="tableHeader" as="span">
          {spec.label}
        </Text>
      </label>
      <select
        className={styles.control}
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value)}
      >
        {!spec.required && <option value="">—</option>}
        {(spec.options ?? []).map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
      {spec.help && (
        <Text as="p" style="caption" color="secondary">
          {spec.help}
        </Text>
      )}
    </div>
  );
}
