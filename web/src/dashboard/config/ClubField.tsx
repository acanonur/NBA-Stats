import type { JSX } from "react";
import { Text } from "../../design/Text";
import type { FieldEditorProps } from "./types";
import styles from "./field.module.css";

/** A EuroLeague club code: three capital letters (the `club` fields of the league widgets). An
 * empty box is `null`, which means "none chosen", never an empty string. */
export function ClubField({ spec, value, onChange }: FieldEditorProps<string | null>): JSX.Element {
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
        maxLength={3}
        autoCapitalize="characters"
        spellCheck={false}
        value={value ?? ""}
        onChange={(event) => {
          const raw = event.target.value.trim().toUpperCase();
          onChange(raw === "" ? null : raw);
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
