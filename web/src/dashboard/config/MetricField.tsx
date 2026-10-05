import type { JSX } from "react";
import { METRICS_DOCUMENT } from "../../generated/contracts";
import { Text } from "../../design/Text";
import type { FieldEditorProps } from "./types";
import styles from "./field.module.css";

const ALL_METRICS = METRICS_DOCUMENT.metrics as ReadonlyArray<{
  readonly key: string;
  readonly name: string;
  readonly category: string;
  readonly scope: readonly string[];
}>;

export function metricsInScope(scope: "player" | "team" | "any" | undefined): readonly {
  readonly key: string;
  readonly name: string;
  readonly category: string;
}[] {
  if (!scope || scope === "any") return ALL_METRICS;
  return ALL_METRICS.filter((metric) => metric.scope.includes(scope));
}

export function MetricField({ spec, value, onChange }: FieldEditorProps<string | null>): JSX.Element {
  const options = metricsInScope(spec.metricScope);
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
        {options.map((metric) => (
          <option key={metric.key} value={metric.key}>
            {metric.name}
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
