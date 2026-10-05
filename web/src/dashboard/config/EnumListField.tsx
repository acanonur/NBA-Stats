import type { JSX } from "react";
import { Text } from "../../design/Text";
import type { FieldEditorProps } from "./types";
import styles from "./field.module.css";

export function EnumListField({
  spec,
  value,
  onChange,
}: FieldEditorProps<readonly string[] | null>): JSX.Element {
  const selected = value ?? [];
  const atMax = spec.maxItems !== undefined && selected.length >= spec.maxItems;

  function toggle(option: string): void {
    if (selected.includes(option)) {
      onChange(selected.filter((item) => item !== option));
    } else if (!atMax) {
      onChange([...selected, option]);
    }
  }

  return (
    <div className={styles.field}>
      <label>
        <Text style="tableHeader" as="span">
          {spec.label}
        </Text>
      </label>
      <div className={styles.optionList} role="group" aria-label={spec.label}>
        {(spec.options ?? []).map((option) => (
          <label key={option} className={styles.checkboxRow}>
            <input
              type="checkbox"
              checked={selected.includes(option)}
              disabled={!selected.includes(option) && atMax}
              onChange={() => toggle(option)}
            />
            <Text style="tableCell" as="span">
              {option}
            </Text>
          </label>
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
