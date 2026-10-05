import { useState, type JSX } from "react";
import { Text } from "../../design/Text";
import { PlayerAvatar } from "../../design/PlayerAvatar";
import { usePlayerSearch } from "./usePlayerSearch";
import { SubjectTokenPicker } from "./SubjectTokenPicker";
import type { FieldEditorProps } from "./types";
import styles from "./field.module.css";

/**
 * `catalog.py::_validate_field` accepts a `$favorite_player`-style token on ANY `player` field,
 * not only a genuine `subject` field, so this editor offers both a numeric search and the token
 * picker (WEB_DESIGN.md §2 presets already ship `subjectId: "$favorite_player"` on fields typed
 * exactly this way).
 */
export function PlayerField({
  spec,
  value,
  onChange,
}: FieldEditorProps<number | string | null>): JSX.Element {
  const [query, setQuery] = useState("");
  const { results, isSearching } = usePlayerSearch(query);

  return (
    <div className={styles.field}>
      <label>
        <Text style="tableHeader" as="span">
          {spec.label}
        </Text>
      </label>
      <SubjectTokenPicker value={value} onPick={onChange} kind="player" />
      <input
        className={styles.control}
        type="text"
        placeholder="Search players…"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      {isSearching && (
        <Text style="caption" color="tertiary">
          Searching…
        </Text>
      )}
      {results.length > 0 && (
        <div className={styles.optionList} role="listbox" aria-label={`${spec.label} results`}>
          {results.map((player) => (
            <button
              key={player.playerId}
              type="button"
              className={styles.chip}
              onClick={() => {
                onChange(player.playerId);
                setQuery(player.name);
              }}
              aria-pressed={value === player.playerId}
            >
              <PlayerAvatar player={player} size="small" />
              <Text style="tableCell" as="span">
                {player.name}
              </Text>
            </button>
          ))}
        </div>
      )}
      {value !== null && value !== undefined && (
        <Text style="caption" color="secondary">
          Selected player id: {value}
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
