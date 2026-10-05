/**
 * `fantasy_draft_board` — who to take next, and what taking them gives you, as a dense
 * spreadsheet table. Ported from `ios/NBAStats/Widgets/FantasyDraftBoardWidget.swift`, whose
 * shape this follows closely: a pinned rank+player column beside `PinnedColumnTable`'s scrolling
 * numeric grid, with a three-way group picker over `column.group` (Summary / Per game / Value)
 * standing in for the twenty-four columns that do not fit a phone at once.
 *
 * Columns render in exactly the order the server sends them (including the intentional
 * ast/reb ↔ zAST/zREB ordering swap between the production and impact blocks — CONTRACT.md
 * §7.7), never re-sorted or grouped by this component. `punted: true` only ever appears on a
 * `z_` column (the raw per-game production column beside it is never punted), and
 * `higherIsBetter: null` (`fga`, `fta`) gets no direction colour — guessing one would be worse
 * than showing none.
 */
import { useState, type JSX } from "react";
import type { WidgetViewProps } from "../../generated/registry";
import type { FantasyDraftBoardColumn, FantasyDraftBoardPayload, FantasyDraftBoardRow } from "../../api/types";
import type { MetricFormat } from "../../generated/contracts";
import type { WidgetSizeKey } from "../../generated/tokens";
import { Text } from "../../design/Text";
import { AvailabilityBadge } from "../../design/AvailabilityBadge";
import { PinnedColumnTable } from "../../design/PinnedColumnTable";
import type { PinnedColumnDef } from "../../design/PinnedColumnTable";
import { ErrorTile } from "../../design/StateViews";
import { comparisonOutcome, comparisonColorVar } from "../../design/valueColor";
import { EM_DASH, formatInteger, formatSigned, formatValue, seasonDisplay } from "../../design/format";
import { decodeFantasyDraftBoardPayload } from "./decode";
import styles from "./index.module.css";

const ROW_LIMIT: Readonly<Record<WidgetSizeKey, number>> = { small: 3, medium: 4, large: 8 };

const CATEGORY_LABEL: Readonly<Record<string, string>> = {
  pts: "points",
  fg3m: "threes",
  reb: "rebounds",
  ast: "assists",
  stl: "steals",
  blk: "blocks",
  tov: "turnovers",
  fg_pct: "FG%",
  ft_pct: "FT%",
};

const SCORING_LABEL: Readonly<Record<string, string>> = {
  categories: "Categories",
  espn_points: "ESPN Points",
  yahoo_points: "Yahoo Points",
};

const GROUP_LABEL: Readonly<Record<string, string>> = {
  summary: "Summary",
  production: "Per game",
  impact: "Value",
};

function categoryLabel(category: string): string {
  return CATEGORY_LABEL[category] ?? category;
}

function orderedGroups(columns: readonly FantasyDraftBoardColumn[]): readonly string[] {
  const seen: string[] = [];
  for (const column of columns) {
    if (!seen.includes(column.group)) seen.push(column.group);
  }
  return seen;
}

function contextLine(payload: FantasyDraftBoardPayload): string {
  const parts = [
    seasonDisplay(payload.season),
    SCORING_LABEL[payload.scoring] ?? payload.scoring,
    `${payload.teams} teams`,
    `${payload.rosterSpots} roster spots`,
  ];
  if (payload.puntCategories.length > 0) {
    parts.push(`punting ${payload.puntCategories.map(categoryLabel).join(", ")}`);
  }
  return parts.join(" · ");
}

function nextPickText(payload: FantasyDraftBoardPayload): string | null {
  const pick = payload.nextPick;
  if (!pick) return null;
  return `Round ${pick.round}, Pick ${pick.pickInRound} (#${pick.overall} overall)`;
}

function cellText(column: FantasyDraftBoardColumn, row: FantasyDraftBoardRow): string {
  const value = row.values[column.key];
  if (value === undefined || value === null) return EM_DASH;
  if (typeof value === "string") return value;
  if (!column.format) return String(value);
  const format = column.format as MetricFormat;
  return column.signed ? formatSigned(value, format, true) : formatValue(value, format);
}

function cellColorVar(column: FantasyDraftBoardColumn, row: FantasyDraftBoardRow): string | undefined {
  if (column.punted) return "var(--hw-text-tertiary)";
  if (column.group !== "impact" || column.higherIsBetter === null) return undefined;
  const value = row.values[column.key];
  if (typeof value !== "number") return undefined;
  return comparisonColorVar(comparisonOutcome(value, column.higherIsBetter));
}

function rowAccessibleLabel(row: FantasyDraftBoardRow, columns: readonly FantasyDraftBoardColumn[]): string {
  const parts: string[] = [`Rank ${row.rank}`, row.player.name];
  const team = row.values.team;
  if (typeof team === "string" && team.length > 0) parts.push(team);
  const score = row.values.score;
  if (typeof score === "number") parts.push(`value ${formatSigned(score, "decimal2", true)}`);
  const notable = columns
    .filter((column) => column.group === "impact" && !column.punted)
    .map((column) => ({ column, value: row.values[column.key] }))
    .filter((entry): entry is { column: FantasyDraftBoardColumn; value: number } => typeof entry.value === "number" && Math.abs(entry.value) > 1)
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
    .slice(0, 3);
  for (const { column, value } of notable) {
    const category = column.key.startsWith("z_") ? column.key.slice(2) : column.key;
    parts.push(`${categoryLabel(category)} ${formatSigned(value, "decimal2", true)}`);
  }
  if (row.reason) parts.push(row.reason);
  parts.push("fantasy value, not a record");
  return parts.join(", ");
}

function buildColumns(visible: readonly FantasyDraftBoardColumn[]): readonly PinnedColumnDef<FantasyDraftBoardRow>[] {
  return visible.map((column) => ({
    key: column.key,
    align: column.align,
    header: (
      <Text style="tableHeader" truncate ariaHidden color={column.punted ? "tertiary" : undefined}>
        {column.label}
      </Text>
    ),
    render: (row: FantasyDraftBoardRow) => (
      <Text style="tableCell" tabularNums ariaHidden htmlStyle={{ color: cellColorVar(column, row) }}>
        {cellText(column, row)}
      </Text>
    ),
  }));
}

function GroupPicker({
  groups,
  active,
  onChange,
}: {
  readonly groups: readonly string[];
  readonly active: string;
  readonly onChange: (group: string) => void;
}): JSX.Element | null {
  if (groups.length <= 1) return null;
  return (
    <div className={styles.groupPicker} role="tablist" aria-label="Column group">
      {groups.map((group) => (
        <button
          key={group}
          type="button"
          role="tab"
          aria-selected={group === active}
          className={active === group ? styles.groupButtonActive : styles.groupButton}
          onClick={() => onChange(group)}
        >
          {GROUP_LABEL[group] ?? group}
        </button>
      ))}
    </div>
  );
}

export default function FantasyDraftBoardWidget({ size, payload }: WidgetViewProps): JSX.Element {
  const [group, setGroup] = useState<string | null>(null);

  let data: FantasyDraftBoardPayload;
  try {
    data = decodeFantasyDraftBoardPayload(payload);
  } catch {
    return <ErrorTile size={size} isRetryable={false} message="This draft board's data did not match what the app expected." />;
  }

  const groups = orderedGroups(data.columns);
  const activeGroup = group && groups.includes(group) ? group : (groups[0] ?? "production");
  const visibleColumns = data.columns.filter((column) => column.group === activeGroup);
  const rowLimit = ROW_LIMIT[size] ?? ROW_LIMIT.medium;
  const rows = data.rows.slice(0, Math.max(rowLimit, 0));
  const columnDefs = buildColumns(visibleColumns.length > 0 ? visibleColumns : data.columns);

  const pinnedColumn: PinnedColumnDef<FantasyDraftBoardRow> = {
    key: "identity",
    header: (
      <span className={styles.pinnedHeader} aria-hidden="true">
        <Text style="tableHeader">#</Text>
        <Text style="tableHeader">Player</Text>
      </span>
    ),
    render: (row) => (
      <span className={styles.pinnedCell} role="group" aria-label={rowAccessibleLabel(row, data.columns)}>
        <Text style="tableCell" color="tertiary" tabularNums className={styles.pinnedRank} ariaHidden>
          {formatInteger(row.rank)}
        </Text>
        <Text style="tableCell" truncate ariaHidden>
          {row.player.name}
        </Text>
        {/* `row.availability` was decoded and then dropped: a board of z-scores derived from
            season aggregates is `"estimated"`, and nothing on screen said so. The dot variant
            (`showsText={false}`) is the one `AvailabilityBadge` documents for a dense table
            column, and `full` renders zero DOM nodes, so a row that really is measured keeps its
            spacing exactly as it was. The row's accessible label is on the wrapping group. */}
        <AvailabilityBadge
          availability={row.availability}
          showsText={false}
          isInteractive={false}
          metricName={row.player.name}
        />
      </span>
    ),
  };

  const topRow = rows[0];
  const showsFooter = size === "large" && data.rows.length > 0;

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        <div className={styles.headerRow}>
          <Text style="widgetTitle">On the clock</Text>
          {nextPickText(data) && (
            <Text style="caption" tabularNums color="secondary">
              {nextPickText(data)}
            </Text>
          )}
        </div>
        <Text as="p" style="caption" color="secondary">
          {contextLine(data)}
        </Text>
      </div>

      {data.rows.length === 0 ? (
        <Text as="p" style="caption" color="secondary">
          No players are valued for this season yet.
        </Text>
      ) : (
        <>
          <GroupPicker groups={groups} active={activeGroup} onChange={setGroup} />
          <div className={styles.tableWrap}>
            <PinnedColumnTable
              rows={rows}
              rowKey={(row) => String(row.rank)}
              pinnedColumn={pinnedColumn}
              columns={columnDefs}
              pinnedWidth={132}
              columnWidth={56}
            />
          </div>
        </>
      )}

      {showsFooter && (
        <div className={styles.footer}>
          {topRow?.reason && (
            <Text as="p" style="caption" color="secondary">
              {`${topRow.player.name}: ${topRow.reason}.`}
            </Text>
          )}
          {data.weakestCategories.length > 0 && (
            <Text as="p" style="caption" color="secondary">
              {`Your roster is thinnest at ${data.weakestCategories.map(categoryLabel).join(", ")}.`}
            </Text>
          )}
          {data.note && (
            <Text as="p" style="caption" color="tertiary">
              {data.note}
            </Text>
          )}
        </div>
      )}
    </div>
  );
}
