/**
 * A dense table with a non-scrolling identity column beside an `overflow-x: auto` body —
 * WEB_DESIGN.md §7.4 item 16, generic over any row shape, for `game_log` and
 * `fantasy_draft_board`.
 *
 * This component owns only the grid mechanics — the pinned column, the scrolling body, and the
 * hairline sizing trick documented in `PinnedColumnTable.module.css`. What each cell renders
 * (a `StatValue`, a plain string, a pill) is entirely up to the caller's `render` functions, so
 * this stays one definition of "a table with a pinned column" rather than a second one per
 * widget that happens to need it.
 */
import type { ReactNode, JSX } from "react";
import type { CSSProperties } from "react";
import clsx from "clsx";
import styles from "./PinnedColumnTable.module.css";

export interface PinnedColumnDef<Row> {
  readonly key: string;
  readonly header: ReactNode;
  readonly align?: "leading" | "trailing";
  readonly render: (row: Row) => ReactNode;
  /** The season-best (or otherwise notable) treatment — WEB_DESIGN.md §7.7's `game_log` note:
   * "a cell equal to `seasonBests[key]` within `1e-9` gets the selection pill". Evaluated per
   * row so the caller's own equality rule (whatever it is for that column) decides. */
  readonly isHighlighted?: (row: Row) => boolean;
}

export interface PinnedColumnTableProps<Row> {
  readonly rows: readonly Row[];
  readonly rowKey: (row: Row) => string;
  /** The non-scrolling leading column — a player name, a date, whatever identifies the row. */
  readonly pinnedColumn: PinnedColumnDef<Row>;
  /** The scrolling columns, rendered in exactly the order given — CONTRACT.md's "render columns
   * in served order" rule applies at the call site, not here. */
  readonly columns: readonly PinnedColumnDef<Row>[];
  /** The identity column's width, in px. Fixed rather than content-measured, so every row lines
   * up without a layout pass — 112px is WEB_DESIGN.md §7.4 item 16's own figure. */
  readonly pinnedWidth?: number;
  /** One shared width for every scrolling column, in px — what lets the hairline rule below the
   * grid be sized as `calc(var(--col-w) * var(--col-n))` (see the stylesheet's docstring). */
  readonly columnWidth?: number;
  readonly className?: string;
}

export function PinnedColumnTable<Row>({
  rows,
  rowKey,
  pinnedColumn,
  columns,
  pinnedWidth = 112,
  columnWidth = 68,
  className,
}: PinnedColumnTableProps<Row>): JSX.Element {
  const gridStyle = { "--col-w": `${columnWidth}px`, "--col-n": columns.length } as CSSProperties;

  return (
    <div className={clsx(styles.root, className)}>
      <div className={styles.pinnedColumn} style={{ width: pinnedWidth }}>
        <div className={styles.pinnedCell}>{pinnedColumn.header}</div>
        <div className={styles.pinnedRule} />
        {rows.map((row) => (
          <div key={rowKey(row)}>
            <div
              className={clsx(
                styles.pinnedCell,
                pinnedColumn.align === "trailing" && styles.alignTrailing,
              )}
            >
              {pinnedColumn.render(row)}
            </div>
            <div className={styles.pinnedRule} />
          </div>
        ))}
      </div>
      <div className={styles.scrollArea}>
        <div className={styles.grid} style={gridStyle}>
          {columns.map((column) => (
            <div
              key={column.key}
              className={clsx(styles.headerCell, column.align === "trailing" && styles.alignTrailing)}
            >
              {column.header}
            </div>
          ))}
        </div>
        <div className={styles.rule} style={gridStyle} />
        {rows.map((row) => (
          <div key={rowKey(row)}>
            <div className={styles.grid} style={gridStyle}>
              {columns.map((column) => (
                <div
                  key={column.key}
                  className={clsx(
                    styles.cell,
                    column.align === "trailing" && styles.alignTrailing,
                    column.isHighlighted?.(row) && styles.selectionPill,
                  )}
                >
                  {column.render(row)}
                </div>
              ))}
            </div>
            <div className={styles.rule} style={gridStyle} />
          </div>
        ))}
      </div>
    </div>
  );
}
