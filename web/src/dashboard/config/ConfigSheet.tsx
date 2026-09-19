/**
 * The widget configuration sheet — one editor per `ConfigFieldType`, dispatched by `type`. Reads
 * a widget kind's field specs straight from `WIDGETS_DOCUMENT` (WP0-generated, verbatim from
 * `contracts/widgets.json`), so a catalog change reaches this sheet with no code edit.
 */
import { useState, type JSX } from "react";
import { WIDGETS_DOCUMENT, type WidgetKind } from "../../generated/contracts";
import { Text } from "../../design/Text";
import type { ConfigFieldSpec } from "./types";
import { SeasonField } from "./SeasonField";
import { EnumField } from "./EnumField";
import { EnumListField } from "./EnumListField";
import { MetricField } from "./MetricField";
import { MetricListField } from "./MetricListField";
import { PlayerField } from "./PlayerField";
import { PlayerListField } from "./PlayerListField";
import { TeamField } from "./TeamField";
import { TeamListField } from "./TeamListField";
import { SubjectField } from "./SubjectField";
import { SubjectListField } from "./SubjectListField";
import { IntField } from "./IntField";
import { DoubleField } from "./DoubleField";
import { BoolField } from "./BoolField";
import { DateField } from "./DateField";
import formStyles from "../../auth/forms.module.css";
import styles from "./ConfigSheet.module.css";

/**
 * Dispatches one config value to its editor. A `switch` rather than a lookup table on purpose:
 * each editor's `value`/`onChange` types are genuinely different (a `bool` field is not a
 * `metricList` field), and a value read back from a saved widget's `config` is `unknown` at
 * compile time no matter how it is stored — so narrowing it per case with an explicit assertion
 * to that one case's own declared type is what lets this file have no `any` anywhere in it.
 */
function renderField(
  fieldSpec: ConfigFieldSpec,
  value: unknown,
  onChange: (value: unknown) => void,
  config: Readonly<Record<string, unknown>>,
): JSX.Element {
  switch (fieldSpec.type) {
    case "season":
      return <SeasonField spec={fieldSpec} value={value as string | null} onChange={onChange} config={config} />;
    case "enum":
      return <EnumField spec={fieldSpec} value={value as string | null} onChange={onChange} config={config} />;
    case "enumList":
      return (
        <EnumListField spec={fieldSpec} value={value as readonly string[] | null} onChange={onChange} config={config} />
      );
    case "metric":
      return <MetricField spec={fieldSpec} value={value as string | null} onChange={onChange} config={config} />;
    case "metricList":
      return (
        <MetricListField spec={fieldSpec} value={value as readonly string[] | null} onChange={onChange} config={config} />
      );
    case "player":
      return (
        <PlayerField spec={fieldSpec} value={value as number | string | null} onChange={onChange} config={config} />
      );
    case "playerList":
      return (
        <PlayerListField spec={fieldSpec} value={value as readonly number[] | null} onChange={onChange} config={config} />
      );
    case "team":
      return <TeamField spec={fieldSpec} value={value as number | string | null} onChange={onChange} config={config} />;
    case "teamList":
      return (
        <TeamListField spec={fieldSpec} value={value as readonly number[] | null} onChange={onChange} config={config} />
      );
    case "subject":
      return (
        <SubjectField spec={fieldSpec} value={value as number | string | null} onChange={onChange} config={config} />
      );
    case "subjectList":
      return (
        <SubjectListField
          spec={fieldSpec}
          value={value as readonly (number | string)[] | null}
          onChange={onChange}
          config={config}
        />
      );
    case "int":
      return <IntField spec={fieldSpec} value={value as number | null} onChange={onChange} config={config} />;
    case "double":
      return <DoubleField spec={fieldSpec} value={value as number | null} onChange={onChange} config={config} />;
    case "bool":
      return <BoolField spec={fieldSpec} value={value as boolean} onChange={onChange} config={config} />;
    case "date":
      return <DateField spec={fieldSpec} value={value as string | null} onChange={onChange} config={config} />;
  }
}

function widgetSpec(kind: WidgetKind): {
  readonly name: string;
  readonly config: readonly ConfigFieldSpec[];
} | null {
  const found = (
    WIDGETS_DOCUMENT.widgets as ReadonlyArray<{
      readonly kind: string;
      readonly name: string;
      readonly config: readonly ConfigFieldSpec[];
    }>
  ).find((widget) => widget.kind === kind);
  return found ? { name: found.name, config: found.config } : null;
}

export interface ConfigSheetProps {
  readonly kind: WidgetKind;
  readonly title: string | null;
  readonly config: Readonly<Record<string, unknown>>;
  readonly onSave: (config: Readonly<Record<string, unknown>>, title: string | null) => void;
  readonly onClose: () => void;
}

export function ConfigSheet({ kind, title, config, onSave, onClose }: ConfigSheetProps): JSX.Element {
  const spec = widgetSpec(kind);
  const [pending, setPending] = useState<Record<string, unknown>>({ ...config });
  const [pendingTitle, setPendingTitle] = useState(title ?? "");

  function setField(key: string, value: unknown): void {
    setPending((prev) => ({ ...prev, [key]: value }));
  }

  return (
    <div className={styles.backdrop} role="presentation" onClick={onClose}>
      <div className={styles.scrim} aria-hidden />
      <div
        className={styles.sheet}
        style={{ boxShadow: "var(--hw-card-shadow-raised)" }}
        role="dialog"
        aria-modal="true"
        aria-label={`Configure ${spec?.name ?? kind}`}
        onClick={(event) => event.stopPropagation()}
      >
        <Text style="sectionTitle" as="p">
          Configure {spec?.name ?? kind}
        </Text>
        <label className={formStyles.field}>
          <Text style="tableHeader" as="span">
            Custom title (optional)
          </Text>
          <input
            className={formStyles.input}
            type="text"
            value={pendingTitle}
            onChange={(event) => setPendingTitle(event.target.value)}
          />
        </label>
        {spec?.config.map((fieldSpec) => (
          <div key={fieldSpec.key}>
            {renderField(
              fieldSpec,
              pending[fieldSpec.key] ?? fieldSpec.default,
              (value) => setField(fieldSpec.key, value),
              pending,
            )}
          </div>
        ))}
        <div className={styles.actions}>
          <button type="button" className={formStyles.submit} onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className={formStyles.submit}
            onClick={() => onSave(pending, pendingTitle.trim() || null)}
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
