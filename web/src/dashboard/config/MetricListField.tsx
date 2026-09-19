import type { JSX } from "react";
import { Text } from "../../design/Text";
import { metricsInScope } from "./MetricField";
import type { FieldEditorProps } from "./types";
import styles from "./field.module.css";

export function MetricListField({
  spec,
  value,
  onChange,
}: FieldEditorProps<readonly string[] | null>): JSX.Element {
  const selected = value ?? [];
  const options = metricsInScope(spec.metricScope);
  const atMax = spec.maxItems !== undefined && selected.length >= spec.maxItems;

  function toggle(key: string): void {
    if (selected.includes(key)) {
      onChange(selected.filter((item) => item !== key));
    } else if (!atMax) {
      onChange([...selected, key]);
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
        {options.map((metric) => (
          <label key={metric.key} className={styles.checkboxRow}>
            <input
              type="checkbox"
              checked={selected.includes(metric.key)}
              disabled={!selected.includes(metric.key) && atMax}
              onChange={() => toggle(metric.key)}
            />
            <Text style="tableCell" as="span">
              {metric.name}
            </Text>
          </label>
        ))}
      </div>
      {spec.maxItems !== undefined && (
        <Text style="caption" color="tertiary">
          {selected.length} / {spec.maxItems}
        </Text>
      )}
      {spec.help && (
        <Text as="p" style="caption" color="secondary">
          {spec.help}
        </Text>
      )}
    </div>
  );
}
