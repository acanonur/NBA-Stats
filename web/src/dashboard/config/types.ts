/**
 * The shape of one entry in `contracts/widgets.json#/widgets/*\/config` — WP0's
 * `WIDGETS_DOCUMENT` carries these verbatim (`generated/contracts.ts`), but as loose `as const`
 * data with no field-level type of its own (CONTRACT-FOR-AGENTS.md scopes widget-config typing
 * to WP4/WP5). This is that type, transcribed field-by-field from `contracts/widgets.json`
 * itself rather than guessed from `ConfigFieldType`'s name list.
 */
import type { ConfigFieldType } from "../../generated/contracts";

export interface ConfigFieldSpec {
  readonly key: string;
  readonly type: ConfigFieldType;
  readonly label: string;
  readonly required: boolean;
  readonly default: unknown;
  /** `enum` / `enumList` only. */
  readonly options?: readonly string[];
  /** `int` / `double` only. */
  readonly min?: number;
  readonly max?: number;
  /** `metricList` / `playerList` / `teamList` / `subjectList` / `enumList`. */
  readonly minItems?: number;
  readonly maxItems?: number;
  /** `metric` / `metricList` only — which `MetricDescriptor.scope` values are eligible. */
  readonly metricScope?: "player" | "team" | "any";
  /** `subject` / `subjectList` only — the sibling field key that names `"player"` or `"team"`. */
  readonly dependsOn?: string;
  readonly help?: string;
}

export interface FieldEditorProps<T = unknown> {
  readonly spec: ConfigFieldSpec;
  readonly value: T;
  readonly onChange: (value: T) => void;
  /** The whole config object, for a `subject`/`subjectList` field reading its `dependsOn`
   * sibling. */
  readonly config: Readonly<Record<string, unknown>>;
}
