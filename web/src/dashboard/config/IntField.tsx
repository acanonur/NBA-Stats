import type { JSX } from "react";
import { Text } from "../../design/Text";
import type { FieldEditorProps } from "./types";
import styles from "./field.module.css";

export function IntField({ spec, value, onChange }: FieldEditorProps<number | null>): JSX.Element {
  return (
    <div className={styles.field}>
      <label>
        <Text style="tableHeader" as="span">
          {spec.label}
        </Text>
      </label>
      <input
        className={styles.control}
        type="number"
        step={1}
        min={spec.min}
        max={spec.max}
        value={value ?? ""}
        onChange={(event) => {
          const raw = event.target.value;
          onChange(raw === "" ? null : Math.round(Number(raw)));
        }}
      />
      {spec.help && (
        <Text as="p" style="caption" color="secondary">
          {spec.help}
        </Text>
      )}
    </div>
  );
}
