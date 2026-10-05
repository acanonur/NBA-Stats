/**
 * `projection_board` — tonight's projected ranges, each drawn against the player's own
 * season average. Ported from `ios/NBAStats/Widgets/ProjectionBoardWidget.swift`.
 *
 * Nothing here is a market quote of any kind: the tick on every bar is the row's own
 * `referenceValue` (the player's season average), labelled as such every time, never a market
 * line. Rows arrive from the server already sorted by `|deltaZ|` and are rendered in that order —
 * re-sorting by raw `|delta|` here would silently break the disagreement ranking a points row and
 * a rebounds row are only comparable through.
 *
 * The widget renders in the broadsheet language whenever its tile sits on a broadsheet-presented
 * dashboard (`WidgetFlowLayout` sets `[data-presentation="broadsheet"]` on the grid), and falls
 * back to the app's ordinary tile typography everywhere else — mirroring the Swift original's
 * `\.isBroadsheet` environment split. Detected once, from the DOM, because `WidgetViewProps`
 * carries no presentation flag (WEB_DESIGN.md §7.7: broadsheet is "a separate component family,
 * not a theme override").
 */
import { useLayoutEffect, useRef, useState, type JSX, type RefObject } from "react";
import type { WidgetViewProps } from "../../generated/registry";
import type { ProjectionBoardPayload, ProjectionBoardRow } from "../../api/types";
import type { MetricFormat } from "../../generated/contracts";
import type { WidgetSizeKey } from "../../generated/tokens";
import { Text } from "../../design/Text";
import { ProjectionIntervalBar } from "../../design/ProjectionIntervalBar";
import { BroadsheetRangeBar } from "../../design/broadsheet/BroadsheetRangeBar";
import { BroadsheetRule } from "../../design/broadsheet/BroadsheetRow";
// Loaded as a side effect so the `hw-bs-*` classes above are styled even when this tile's
// dashboard never mounted `BroadsheetPage` itself — `WidgetFlowLayout` can set
// `[data-presentation="broadsheet"]` on the grid without that page shell in the tree.
import "../../design/broadsheet/broadsheet.css";
import type { RangeBarModel } from "../../design/RangeBar";
import { ErrorTile } from "../../design/StateViews";
import { AvailabilityBadge } from "../../design/AvailabilityBadge";
import { shortLabel } from "../../design/availability";
import { deltaColorVar } from "../../design/valueColor";
import { EM_DASH, formatSigned, formatValue, mediumGameDate, shortGameDate } from "../../design/format";
import { decodeProjectionBoardPayload } from "./decode";
import styles from "./index.module.css";

const ROW_LIMIT: Readonly<Record<WidgetSizeKey, number>> = { small: 2, medium: 4, large: 8 };

function hasNumber(value: number | null | undefined): value is number {
  return value !== null && value !== undefined && Number.isFinite(value);
}

/** Detects, once, whether this tile's ancestor grid is presenting as a broadsheet — the only
 * signal `WidgetFlowLayout` gives (a DOM attribute on `.hw-grid`), since the frozen
 * `WidgetViewProps` contract carries no presentation flag. */
function useIsBroadsheet(ref: RefObject<HTMLElement | null>): boolean {
  const [isBroadsheet, setIsBroadsheet] = useState(false);
  useLayoutEffect(() => {
    try {
      setIsBroadsheet(ref.current?.closest('[data-presentation="broadsheet"]') != null);
    } catch {
      setIsBroadsheet(false);
    }
    // Presentation is a dashboard-level setting that does not change without remounting this
    // tile (`DashboardEditor` remounts by `layoutId`), so this runs once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return isBroadsheet;
}

function dateHeadline(payload: ProjectionBoardPayload): string {
  const primary = mediumGameDate(payload.date);
  if (payload.throughDate && payload.throughDate !== payload.date) {
    return `${primary} – ${mediumGameDate(payload.throughDate)}`;
  }
  return primary;
}

function gameCountText(payload: ProjectionBoardPayload): string {
  return payload.gameCount === 1 ? "1 game" : `${payload.gameCount} games`;
}

function contextLine(payload: ProjectionBoardPayload): string {
  return [dateHeadline(payload), gameCountText(payload)].join(" · ");
}

function selectionNote(payload: ProjectionBoardPayload): string | null {
  if (!payload.selectionDate || payload.selectionDate === payload.date) return null;
  return `Players chosen from the ${shortGameDate(payload.selectionDate)} slate.`;
}

function rowModel(row: ProjectionBoardRow, hasReference: boolean): RangeBarModel {
  return {
    low: row.low ?? NaN,
    high: row.high ?? NaN,
    projection: row.projection,
    reference: hasReference ? row.referenceValue : null,
  };
}

function deltaText(row: ProjectionBoardRow): string | null {
  if (!hasNumber(row.delta) || Math.abs(row.delta) < 1e-9) return null;
  return formatSigned(row.delta, row.descriptor.format as MetricFormat, true);
}

function boundText(value: number | null, format: MetricFormat): string {
  return value === null ? EM_DASH : formatValue(value, format);
}

function tileAccent(row: ProjectionBoardRow): string {
  return deltaColorVar(row.delta, true);
}

function broadsheetAccent(row: ProjectionBoardRow, hasReference: boolean): "above" | "below" | "muted" {
  if (!hasReference || !hasNumber(row.delta) || Math.abs(row.delta) < 1e-9) return "muted";
  return row.delta > 0 ? "above" : "below";
}

function rowAccessibleLabel(row: ProjectionBoardRow, payload: ProjectionBoardPayload, hasReference: boolean): string {
  const format = row.descriptor.format as MetricFormat;
  const parts: string[] = [row.player.name, row.matchup, `${row.descriptor.name} projected ${row.displayValue}`];
  if (row.low !== null || row.high !== null) {
    parts.push(`likely between ${boundText(row.low, format)} and ${boundText(row.high, format)}`);
  }
  if (hasReference && payload.referenceLabel && row.referenceValue !== null) {
    parts.push(`${payload.referenceLabel} ${formatValue(row.referenceValue, format)}`);
  }
  const delta = deltaText(row);
  if (delta) parts.push(`difference ${delta}`);
  parts.push("estimated, not recorded");
  return parts.join(", ");
}

/** One line naming every non-`full` availability present on the board. The tile presentation
 * also badges each row individually; the broadsheet deliberately carries no badge chrome (it is a
 * separate component family — WEB_DESIGN.md §7.7 — with no capsules, tints or radii), so this
 * sentence is the only place it can say so, and it is what makes the two presentations tell the
 * reader the same thing. */
function availabilityNote(rows: readonly ProjectionBoardRow[]): string | null {
  const kinds = [...new Set(rows.map((row) => row.availability))].filter((kind) => kind !== "full");
  if (kinds.length === 0) return null;
  const capitalised = kinds.map((kind) => {
    const label = shortLabel(kind);
    return label.charAt(0).toUpperCase() + label.slice(1);
  });
  return `${capitalised.join("; ")}.`;
}

function TileRow({ row, payload, hasReference }: { readonly row: ProjectionBoardRow; readonly payload: ProjectionBoardPayload; readonly hasReference: boolean }): JSX.Element {
  const format = row.descriptor.format as MetricFormat;
  const model = rowModel(row, hasReference);
  const delta = deltaText(row);
  return (
    <div className={styles.row} role="group" aria-label={rowAccessibleLabel(row, payload, hasReference)}>
      <div className={styles.rowHead}>
        <Text style="tableCell" truncate className={styles.rowName} ariaHidden>
          {row.player.name}
        </Text>
        <Text as="span" style="caption" tabularNums color="secondary" ariaHidden>
          {row.matchup}
        </Text>
        <span className={styles.rowSpacer} />
        <span className={styles.rowMetric} aria-hidden>
          <Text style="tableHeader" color="tertiary">
            {row.descriptor.shortName}
          </Text>
          <Text style="statValue" tabularNums>
            {row.displayValue}
          </Text>
          {/* Every row on this board carries its own `availability`, and the decoder has always
              read it — but until now nothing rendered it, so a projection (`"estimated"` for the
              whole board, by construction) looked exactly like a recorded number. `next_game_projection`
              marks the identical rows with this same badge; the two widgets now agree.
              `isInteractive={false}` because a board is a dense repeated call site — the footnote
              below explains the column once, rather than every row becoming a tap target. */}
          <AvailabilityBadge
            availability={row.availability}
            isInteractive={false}
            metricName={row.descriptor.name}
          />
          {delta && (
            <Text style="caption" tabularNums htmlStyle={{ color: tileAccent(row) }}>
              {delta}
            </Text>
          )}
        </span>
      </div>
      {row.low !== null && row.high !== null ? (
        <ProjectionIntervalBar
          model={model}
          accent={tileAccent(row)}
          lowText={boundText(row.low, format)}
          highText={boundText(row.high, format)}
          referenceLabel={hasReference ? payload.referenceLabel : null}
          referenceText={hasReference && row.referenceValue !== null ? formatValue(row.referenceValue, format) : null}
          projectionText={row.displayValue}
        />
      ) : (
        <Text as="p" style="caption" color="secondary" ariaHidden>
          No interval for this projection.
        </Text>
      )}
    </div>
  );
}

function BroadsheetRowView({ row, payload, hasReference, isLast }: { readonly row: ProjectionBoardRow; readonly payload: ProjectionBoardPayload; readonly hasReference: boolean; readonly isLast: boolean }): JSX.Element {
  const format = row.descriptor.format as MetricFormat;
  const model = rowModel(row, hasReference);
  const delta = deltaText(row);
  const accent = broadsheetAccent(row, hasReference);
  return (
    <div className={styles.bsRowWrap}>
      <div className={styles.bsRow} role="group" aria-label={rowAccessibleLabel(row, payload, hasReference)}>
        <div className={styles.bsRowHead}>
          <Text as="span" style="tableCell" truncate htmlStyle={{ fontWeight: 600, color: "var(--hw-bs-text)" }} ariaHidden>
            {row.player.name}
          </Text>
          <Text as="span" style="caption" tabularNums htmlStyle={{ color: "var(--hw-bs-text-muted)" }} ariaHidden>
            {row.matchup}
          </Text>
          <span className={styles.rowSpacer} />
          <span className={styles.rowMetric} aria-hidden>
            <Text style="tableHeader" htmlStyle={{ color: "var(--hw-bs-text-muted)" }}>
              {row.descriptor.shortName}
            </Text>
            <Text style="statValue" tabularNums htmlStyle={{ color: "var(--hw-bs-text)" }}>
              {row.displayValue}
            </Text>
            {delta && (
              <Text
                style="caption"
                tabularNums
                htmlStyle={{ color: accent === "muted" ? "var(--hw-bs-text-muted)" : `var(--hw-bs-accent-${accent})` }}
              >
                {delta}
              </Text>
            )}
          </span>
        </div>
        {row.low !== null && row.high !== null ? (
          <BroadsheetRangeBar
            model={model}
            accent={accent}
            lowText={boundText(row.low, format)}
            highText={boundText(row.high, format)}
            referenceLabel={hasReference ? payload.referenceLabel : null}
            referenceText={hasReference && row.referenceValue !== null ? formatValue(row.referenceValue, format) : null}
            projectionText={row.displayValue}
          />
        ) : (
          <Text as="p" style="caption" htmlStyle={{ color: "var(--hw-bs-text-muted)" }} ariaHidden>
            No interval for this projection.
          </Text>
        )}
      </div>
      {!isLast && <BroadsheetRule />}
    </div>
  );
}

export default function ProjectionBoardWidget({ size, payload }: WidgetViewProps): JSX.Element {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const isBroadsheet = useIsBroadsheet(rootRef);

  let data: ProjectionBoardPayload;
  try {
    data = decodeProjectionBoardPayload(payload);
  } catch {
    return <ErrorTile size={size} isRetryable={false} message="This board's data did not match what the app expected." />;
  }

  const limit = ROW_LIMIT[size] ?? ROW_LIMIT.large;
  const rows = data.rows.slice(0, Math.max(limit, 0));
  const hasReference = data.reference !== "none";
  const footnotes = [data.note, availabilityNote(rows), selectionNote(data)].filter(
    (line): line is string => !!line,
  );

  return (
    <div ref={rootRef} className={styles.root}>
      {isBroadsheet ? (
        <Text as="p" style="caption" tabularNums htmlStyle={{ color: "var(--hw-bs-text)", fontFamily: "Georgia, 'Iowan Old Style', 'Times New Roman', serif" }}>
          {contextLine(data)}
        </Text>
      ) : (
        <Text as="p" style="caption" color="secondary">
          {contextLine(data)}
        </Text>
      )}
      {isBroadsheet && <BroadsheetRule heavy />}

      {rows.length === 0 ? (
        <Text
          as="p"
          style="caption"
          color={isBroadsheet ? undefined : "secondary"}
          htmlStyle={isBroadsheet ? { color: "var(--hw-bs-text-muted)" } : undefined}
        >
          No projection cleared tonight&rsquo;s filters.
        </Text>
      ) : isBroadsheet ? (
        <div className={styles.bsBoard}>
          {rows.map((row, index) => (
            <BroadsheetRowView
              key={`${row.gameId}-${row.metric}-${row.player.playerId}`}
              row={row}
              payload={data}
              hasReference={hasReference}
              isLast={index === rows.length - 1}
            />
          ))}
        </div>
      ) : (
        <div className={styles.board}>
          {rows.map((row) => (
            <TileRow key={`${row.gameId}-${row.metric}-${row.player.playerId}`} row={row} payload={data} hasReference={hasReference} />
          ))}
        </div>
      )}

      {footnotes.length > 0 && (
        <div className={styles.footnotes}>
          {isBroadsheet && <BroadsheetRule heavy />}
          {footnotes.map((line) => (
            <Text
              as="p"
              key={line}
              style="caption"
              color={isBroadsheet ? undefined : "secondary"}
              htmlStyle={isBroadsheet ? { color: "var(--hw-bs-text-muted)" } : undefined}
            >
              {line}
            </Text>
          ))}
        </div>
      )}
    </div>
  );
}
