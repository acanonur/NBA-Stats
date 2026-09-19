/**
 * The dashboard grid — WEB_DESIGN.md §7.5. `design/theme.css` already defines `.hw-grid` and
 * `.hw-grid > .hw-tile`'s span arithmetic (compact 2 columns, regular 4, swapped at 834px); this
 * component's whole job is to walk `widgets` in **document order** and render one
 * `WidgetContainer` per widget, wrapped in `.hw-grid`. It never reorders to fill a gap —
 * `flex-wrap: wrap` with an explicit per-tile width is greedy, order-preserving row packing, and
 * `WidgetContainer` sets the two spans (`--span-c` / `--span-r`) `.hw-tile` reads.
 */
import type { JSX } from "react";
import clsx from "clsx";
import type { WidgetSizeKey } from "../generated/tokens";
import type { ResolveWidgetRequest } from "../api/types";
import { WidgetContainer } from "./WidgetContainer";

export interface WidgetFlowLayoutProps {
  readonly widgets: readonly ResolveWidgetRequest[];
  readonly presentation?: "tiles" | "broadsheet";
  readonly isEditing?: boolean;
  readonly onConfigure?: (widgetId: string) => void;
  readonly onDuplicate?: (widgetId: string) => void;
  readonly onRemove?: (widgetId: string) => void;
  readonly onMoveUp?: (widgetId: string) => void;
  readonly onMoveDown?: (widgetId: string) => void;
  readonly onResize?: (widgetId: string, size: WidgetSizeKey) => void;
  readonly className?: string;
}

export function WidgetFlowLayout({
  widgets,
  presentation = "tiles",
  isEditing = false,
  onConfigure,
  onDuplicate,
  onRemove,
  onMoveUp,
  onMoveDown,
  onResize,
  className,
}: WidgetFlowLayoutProps): JSX.Element {
  return (
    <div
      className={clsx("hw-grid", className)}
      data-presentation={presentation === "broadsheet" ? "broadsheet" : undefined}
    >
      {widgets.map((widget, index) => (
        <WidgetContainer
          key={widget.id}
          widget={widget}
          isEditing={isEditing}
          isFirst={index === 0}
          isLast={index === widgets.length - 1}
          onConfigure={onConfigure}
          onDuplicate={onDuplicate}
          onRemove={onRemove}
          onMoveUp={onMoveUp}
          onMoveDown={onMoveDown}
          onResize={onResize}
        />
      ))}
    </div>
  );
}
