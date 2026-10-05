import type { JSX } from "react";
import { Text } from "../../design/Text";
import { useTeamList } from "./useTeamList";
import { SubjectTokenPicker } from "./SubjectTokenPicker";
import type { FieldEditorProps } from "./types";
import styles from "./field.module.css";

export function TeamField({
  spec,
  value,
  onChange,
}: FieldEditorProps<number | string | null>): JSX.Element {
  const teams = useTeamList();
  const isToken = typeof value === "string";
  return (
    <div className={styles.field}>
      <label>
        <Text style="tableHeader" as="span">
          {spec.label}
        </Text>
      </label>
      <SubjectTokenPicker value={value} onPick={onChange} kind="team" />
      <select
        className={styles.control}
        value={isToken ? "" : (value ?? "")}
        onChange={(event) => onChange(event.target.value === "" ? null : Number(event.target.value))}
      >
        {!spec.required && <option value="">—</option>}
        {teams.map((team) => (
          <option key={team.teamId} value={team.teamId}>
            {team.city ? `${team.city} ${team.nickname ?? team.name}` : team.name}
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
