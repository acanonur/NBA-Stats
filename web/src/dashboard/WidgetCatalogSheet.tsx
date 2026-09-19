/** "Add a widget" — every kind in `WIDGETS_DOCUMENT`, name and summary straight from the
 * catalog. `REGISTRY[kind].component === null` kinds (WP5's territory, still unshipped) are
 * listed too rather than hidden — the widget will render `PendingTile`/its own placeholder once
 * added, which is more honest than pretending it does not exist yet. */
import type { JSX } from "react";
import { WIDGETS_DOCUMENT, type WidgetKind } from "../generated/contracts";
import { Text } from "../design/Text";
import styles from "./WidgetCatalogSheet.module.css";

export interface WidgetCatalogSheetProps {
  readonly onSelect: (kind: WidgetKind) => void;
  readonly onClose: () => void;
}

export function WidgetCatalogSheet({ onSelect, onClose }: WidgetCatalogSheetProps): JSX.Element {
  const widgets = WIDGETS_DOCUMENT.widgets as ReadonlyArray<{
    readonly kind: string;
    readonly name: string;
    readonly summary: string;
  }>;

  return (
    <div className={styles.backdrop} role="presentation" onClick={onClose}>
      <div className={styles.scrim} aria-hidden />
      <div
        className={styles.sheet}
        style={{ boxShadow: "var(--hw-card-shadow-raised)" }}
        role="dialog"
        aria-modal="true"
        aria-label="Add a widget"
        onClick={(event) => event.stopPropagation()}
      >
        <Text style="sectionTitle" as="p">
          Add a widget
        </Text>
        <div className={styles.list}>
          {widgets.map((widget) => (
            <button
              key={widget.kind}
              type="button"
              className={styles.item}
              onClick={() => onSelect(widget.kind as WidgetKind)}
            >
              <Text style="widgetTitle">{widget.name}</Text>
              <Text style="caption" color="secondary">
                {widget.summary}
              </Text>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
