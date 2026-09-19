/** A `subjectList` config field (`trend_chart.subjectIds`) — the list version of
 * {@link SubjectField}: player or team pickers depending on the sibling `dependsOn` field, plus
 * the same subject-token chips. */
import type { JSX } from "react";
import { PlayerListField } from "./PlayerListField";
import { TeamListField } from "./TeamListField";
import { SubjectTokenPicker } from "./SubjectTokenPicker";
import { Text } from "../../design/Text";
import type { FieldEditorProps } from "./types";
import styles from "./field.module.css";

export function SubjectListField({
  spec,
  value,
  onChange,
  config,
}: FieldEditorProps<readonly (number | string)[] | null>): JSX.Element {
  const subjectType = spec.dependsOn ? config[spec.dependsOn] : undefined;
  const numericValues = (value ?? []).filter((item): item is number => typeof item === "number");
  const tokenValues = (value ?? []).filter((item): item is string => typeof item === "string");

  function addToken(token: string): void {
    if (tokenValues.includes(token)) return;
    onChange([...(value ?? []), token]);
  }

  function setNumeric(next: readonly number[] | null): void {
    onChange([...tokenValues, ...(next ?? [])]);
  }

  return (
    <div className={styles.field}>
      <label>
        <Text style="tableHeader" as="span">
          {spec.label}
        </Text>
      </label>
      {tokenValues.length > 0 && (
        <div className={styles.chipList}>
          {tokenValues.map((token) => (
            <span key={token} className={styles.chip}>
              <Text style="caption" as="span">
                {token}
              </Text>
              <button
                type="button"
                className={styles.chipRemove}
                aria-label="Remove"
                onClick={() => onChange((value ?? []).filter((item) => item !== token))}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <SubjectTokenPicker value={null} onPick={addToken} kind={subjectType === "team" ? "team" : "player"} />
      {subjectType === "team" ? (
        <TeamListField spec={spec} value={numericValues} onChange={setNumeric} config={config} />
      ) : (
        <PlayerListField spec={spec} value={numericValues} onChange={setNumeric} config={config} />
      )}
    </div>
  );
}
