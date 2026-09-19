/**
 * `game_log` — a player's recent games as a real table, newest first. Ported from
 * `ios/NBAStats/Widgets/GameLogWidget.swift`: date, opponent and result are pinned on the
 * leading edge (`design/PinnedColumnTable.tsx`) while the metric columns scroll horizontally
 * under a header that stays put, a cell matching the season's whole-season best is marked, and a
 * cell with no number renders `unavailable`-styled regardless of what the row's own combined
 * availability says (CONTRACT.md §7.7).
 *
 * The Swift original hides its entire scrolling numeric grid from accessibility and puts the
 * full per-row summary — date, opponent, result, and every visible column's value — on the
 * pinned cell instead, so a screen-reader user gets one coherent announcement per row rather
 * than a table it has to swipe across. This component follows the same shape.
 */
import type { JSX } from "react";
import type { WidgetViewProps } from "../../generated/registry";
import type { WidgetSizeKey } from "../../generated/tokens";
import type { MetricFormat } from "../../generated/contracts";
import type { MetricDescriptor } from "../../api/types";
import { Text } from "../../design/Text";
import type { TextColorToken } from "../../design/Text";
import { TeamBadge } from "../../design/TeamBadge";
import { PinnedColumnTable } from "../../design/PinnedColumnTable";
import type { PinnedColumnDef } from "../../design/PinnedColumnTable";
import { ErrorTile } from "../../design/StateViews";
import { valueTreatmentClassName } from "../../design/availability";
import { EM_DASH, formatValue, mediumGameDate, seasonContext, shortGameDate } from "../../design/format";
import { decodeGameLogPayload, type DecodedGameLogPayload, type DecodedGameLogRow } from "./payload";
import styles from "./game_log.module.css";

const ROW_LIMIT: Readonly<Record<WidgetSizeKey, number>> = { small: 3, medium: 5, large: 8 };
const COLUMN_LIMIT: Readonly<Record<WidgetSizeKey, number>> = { small: 2, medium: 3, large: 6 };

/** The raw number for a column, falling back to the row's own `minutes` when the server did not
 * repeat it inside `values` — `GameLogWidget.swift`'s `rawValue(_:key:)`. */
function rawValue(row: DecodedGameLogRow, key: string): number | null {
  const fromValues = row.values[key];
  if (typeof fromValues === "number" && Number.isFinite(fromValues)) return fromValues;
  if (key === "min") return row.minutes;
  return null;
}

/** A cell with no number is `unavailable` whatever the row says — CONTRACT.md §7.7. */
function cellAvailability(row: DecodedGameLogRow, key: string): "full" | "estimated" | "partial" | "unavailable" {
  return rawValue(row, key) === null ? "unavailable" : row.availability;
}

function isSeasonBest(payload: DecodedGameLogPayload, row: DecodedGameLogRow, key: string): boolean {
  const best = payload.seasonBests[key];
  const value = rawValue(row, key);
  if (best === null || best === undefined || value === null) return false;
  if (!Number.isFinite(best) || !Number.isFinite(value)) return false;
  return Math.abs(best - value) <= 1e-9;
}

function matchupText(row: DecodedGameLogRow): string {
  const opponent = row.opponentAbbr ?? EM_DASH;
  if (row.isHome === null) return opponent;
  return `${row.isHome ? "vs" : "@"} ${opponent}`;
}

function resultLetter(result: string | null): string {
  if (!result) return "";
  return result.charAt(0).toUpperCase();
}

function resultColorToken(letter: string): TextColorToken {
  if (letter === "W") return "positive";
  if (letter === "L") return "negative";
  return "tertiary";
}

function accessibleRowText(
  payload: DecodedGameLogPayload,
  row: DecodedGameLogRow,
  columns: readonly MetricDescriptor[],
): string {
  const parts: string[] = [mediumGameDate(row.date), matchupText(row)];
  const letter = resultLetter(row.result);
  if (letter === "W") parts.push("win");
  else if (letter === "L") parts.push("loss");
  if (row.score) parts.push(row.score);
  for (const column of columns) {
    const value = rawValue(row, column.key);
    if (value === null) {
      parts.push(`${column.name} not available`);
    } else {
      let text = `${column.name} ${formatValue(value, column.format as MetricFormat)}`;
      if (isSeasonBest(payload, row, column.key)) text += ", season best";
      parts.push(text);
    }
  }
  return parts.join(", ");
}

function buildColumns(
  payload: DecodedGameLogPayload,
  visibleColumns: readonly MetricDescriptor[],
): readonly PinnedColumnDef<DecodedGameLogRow>[] {
  return visibleColumns.map((descriptor) => ({
    key: descriptor.key,
    align: "trailing" as const,
    header: (
      <Text style="tableHeader" truncate ariaHidden>
        {descriptor.shortName}
      </Text>
    ),
    isHighlighted: (row: DecodedGameLogRow) => isSeasonBest(payload, row, descriptor.key),
    render: (row: DecodedGameLogRow) => {
      const value = rawValue(row, descriptor.key);
      const availability = cellAvailability(row, descriptor.key);
      const isBest = isSeasonBest(payload, row, descriptor.key);
      return (
        <Text
          style="tableCell"
          tabularNums
          ariaHidden
          color={isBest ? "selection" : availability === "unavailable" ? "tertiary" : "primary"}
          className={valueTreatmentClassName(availability)}
        >
          {formatValue(value, descriptor.format as MetricFormat)}
        </Text>
      );
    },
  }));
}

function PinnedIdentityCell({
  payload,
  row,
  columns,
}: {
  readonly payload: DecodedGameLogPayload;
  readonly row: DecodedGameLogRow;
  readonly columns: readonly MetricDescriptor[];
}): JSX.Element {
  const letter = resultLetter(row.result);
  return (
    <span className={styles.pinnedCell} role="group" aria-label={accessibleRowText(payload, row, columns)}>
      <Text style="tableCell" color="secondary" tabularNums className={styles.pinnedDate} ariaHidden>
        {shortGameDate(row.date)}
      </Text>
      <Text style="tableCell" className={styles.pinnedMatchup} truncate ariaHidden>
        {matchupText(row)}
      </Text>
      <Text style="tableHeader" tabularNums color={resultColorToken(letter)} className={styles.pinnedResult} ariaHidden>
        {letter.length > 0 ? letter : EM_DASH}
      </Text>
    </span>
  );
}

function GameLogWidgetView({ size, payload: rawPayload }: WidgetViewProps): JSX.Element {
  let payload: DecodedGameLogPayload;
  try {
    payload = decodeGameLogPayload(rawPayload);
  } catch {
    return (
      <ErrorTile size={size} isRetryable={false} message="This game log's data did not match what the app expected." />
    );
  }

  const rowLimit = ROW_LIMIT[size] ?? ROW_LIMIT.medium;
  const columnLimit = COLUMN_LIMIT[size] ?? COLUMN_LIMIT.medium;
  const rows = payload.rows.slice(0, Math.max(rowLimit, 0));
  const visibleColumns = payload.columns.slice(0, Math.max(columnLimit, 0));
  const columns = buildColumns(payload, visibleColumns);

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        {payload.player.teamAbbr && payload.player.teamAbbr.length > 0 && (
          <TeamBadge abbreviation={payload.player.teamAbbr} size="small" />
        )}
        <div className={styles.headerText}>
          <Text style="widgetTitle" className={styles.playerName} truncate>
            {payload.player.name}
          </Text>
          <Text as="span" style="caption">
            {seasonContext(payload.season, payload.seasonType)}
          </Text>
        </div>
      </div>
      {rows.length === 0 ? (
        <div className={styles.emptyNote} role="status">
          <Text as="p" style="caption">
            No games logged for this season yet.
          </Text>
        </div>
      ) : (
        <div className={styles.tableWrap}>
          <PinnedColumnTable
            rows={rows}
            rowKey={(row) => row.gameId}
            pinnedColumn={{
              key: "identity",
              header: (
                <span className={styles.pinnedHeader} aria-hidden="true">
                  <Text style="tableHeader">Date</Text>
                  <Text style="tableHeader">Opp</Text>
                </span>
              ),
              render: (row) => <PinnedIdentityCell payload={payload} row={row} columns={visibleColumns} />,
            }}
            columns={columns}
            pinnedWidth={112}
            columnWidth={58}
          />
        </div>
      )}
      <div className={styles.spacer} />
    </div>
  );
}

export default GameLogWidgetView;
