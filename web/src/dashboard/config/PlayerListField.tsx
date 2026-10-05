import { useState, type JSX } from "react";
import { Text } from "../../design/Text";
import { PlayerAvatar } from "../../design/PlayerAvatar";
import { usePlayerSearch } from "./usePlayerSearch";
import type { FieldEditorProps } from "./types";
import styles from "./field.module.css";

interface PickedPlayer {
  readonly playerId: number;
  readonly name: string;
}

export function PlayerListField({
  spec,
  value,
  onChange,
}: FieldEditorProps<readonly number[] | null>): JSX.Element {
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<readonly PickedPlayer[]>([]);
  const { results } = usePlayerSearch(query);
  const selected = value ?? [];
  const atMax = spec.maxItems !== undefined && selected.length >= spec.maxItems;

  function addPlayer(playerId: number, name: string): void {
    if (selected.includes(playerId) || atMax) return;
    onChange([...selected, playerId]);
    setPicked((prev) => [...prev.filter((p) => p.playerId !== playerId), { playerId, name }]);
    setQuery("");
  }

  function removePlayer(playerId: number): void {
    onChange(selected.filter((id) => id !== playerId));
  }

  return (
    <div className={styles.field}>
      <label>
        <Text style="tableHeader" as="span">
          {spec.label}
        </Text>
      </label>
      <div className={styles.chipList}>
        {selected.map((playerId) => (
          <span key={playerId} className={styles.chip}>
            <Text style="tableCell" as="span">
              {picked.find((p) => p.playerId === playerId)?.name ?? `#${playerId}`}
            </Text>
            <button
              type="button"
              className={styles.chipRemove}
              onClick={() => removePlayer(playerId)}
              aria-label="Remove"
            >
              ×
            </button>
          </span>
        ))}
      </div>
      {!atMax && (
        <input
          className={styles.control}
          type="text"
          placeholder="Search players…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      )}
      {results.length > 0 && (
        <div className={styles.optionList} role="listbox" aria-label={`${spec.label} results`}>
          {results.map((player) => (
            <button
              key={player.playerId}
              type="button"
              className={styles.chip}
              onClick={() => addPlayer(player.playerId, player.name)}
            >
              <PlayerAvatar player={player} size="small" />
              <Text style="tableCell" as="span">
                {player.name}
              </Text>
            </button>
          ))}
        </div>
      )}
      {(spec.maxItems !== undefined || spec.minItems !== undefined) && (
        <Text style="caption" color="tertiary">
          {selected.length}
          {spec.minItems !== undefined ? ` (min ${spec.minItems})` : ""}
          {spec.maxItems !== undefined ? ` / ${spec.maxItems}` : ""}
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
