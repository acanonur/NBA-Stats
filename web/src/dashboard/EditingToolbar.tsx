/**
 * The edit-mode bar above a dashboard's grid: toggle editing, add a widget, undo the last edit
 * (one level, WEB_DESIGN.md §7.3), and the save-state readout. The `stale_write` banner
 * ("[Keep mine] [Use theirs]") lives here too, since it is edit-session chrome, not a widget's
 * concern.
 */
import { useState, type JSX } from "react";
import type { WidgetKind } from "../generated/contracts";
import type { UseLayoutStoreResult } from "./useLayoutStore";
import { Text } from "../design/Text";
import { WidgetCatalogSheet } from "./WidgetCatalogSheet";
import styles from "./EditingToolbar.module.css";

export interface EditingToolbarProps {
  readonly store: UseLayoutStoreResult;
}

export function EditingToolbar({ store }: EditingToolbarProps): JSX.Element {
  const [isAddingWidget, setIsAddingWidget] = useState(false);

  function handleSelectWidget(kind: WidgetKind): void {
    store.addWidget(kind);
    setIsAddingWidget(false);
  }

  return (
    <div>
      {store.staleConflict && (
        <div className={styles.banner} role="alert">
          <Text style="tableCell">This dashboard changed on another device.</Text>
          <button type="button" className={styles.button} onClick={store.keepMine}>
            Keep mine
          </button>
          <button type="button" className={styles.button} onClick={store.useTheirs}>
            Use theirs
          </button>
        </div>
      )}
      <div className={styles.bar}>
        <button
          type="button"
          className={`${styles.button} ${store.isEditing ? styles.primary : ""}`}
          onClick={() => store.setEditing(!store.isEditing)}
        >
          {store.isEditing ? "Done editing" : "Edit"}
        </button>
        {store.isEditing && (
          <>
            <button type="button" className={styles.button} onClick={() => setIsAddingWidget(true)}>
              Add widget
            </button>
            <button type="button" className={styles.button} onClick={store.undoLastEdit} disabled={!store.canUndo}>
              Undo
            </button>
          </>
        )}
        <div className={styles.spacer} />
        <Text style="caption" color="secondary">
          {store.isSaving ? "Saving…" : store.saveError ? store.saveError : ""}
        </Text>
      </div>
      {isAddingWidget && (
        <WidgetCatalogSheet onSelect={handleSelectWidget} onClose={() => setIsAddingWidget(false)} />
      )}
    </div>
  );
}
