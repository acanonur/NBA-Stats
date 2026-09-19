import type { JSX } from "react";
import { Text } from "../../design/Text";
import { useTeamList } from "./useTeamList";
import type { FieldEditorProps } from "./types";
import styles from "./field.module.css";

export function TeamListField({
  spec,
  value,
  onChange,
}: FieldEditorProps<readonly number[] | null>): JSX.Element {
  const teams = useTeamList();
  const selected = value ?? [];
  const atMax = spec.maxItems !== undefined && selected.length >= spec.maxItems;

  function toggle(teamId: number): void {
    if (selected.includes(teamId)) {
      onChange(selected.filter((id) => id !== teamId));
    } else if (!atMax) {
      onChange([...selected, teamId]);
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
        {teams.map((team) => (
          <label key={team.teamId} className={styles.checkboxRow}>
            <input
              type="checkbox"
              checked={selected.includes(team.teamId)}
              disabled={!selected.includes(team.teamId) && atMax}
              onChange={() => toggle(team.teamId)}
            />
            <Text style="tableCell" as="span">
              {team.abbr}
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
