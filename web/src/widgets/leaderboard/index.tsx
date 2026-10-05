/**
 * `leaderboard` — a ranked list of players or teams for one metric, for a season or across all
 * of league history. Ported from `ios/NBAStats/Widgets/LeaderboardWidget.swift`: the qualifier
 * line, the era treatment on every value, and — at `size === "large"` with room to spare — up to
 * three secondary metric columns that trade places with the row avatars rather than competing
 * with them for width (CONTRACT.md §7.7: "avatars and extra numeric columns never share space").
 */
import type { JSX } from "react";
import clsx from "clsx";
import type { WidgetViewProps } from "../../generated/registry";
import type { WidgetSizeKey } from "../../generated/tokens";
import type { MetricFormat } from "../../generated/contracts";
import type { LeaderboardPayload, LeaderboardRow, MetricDescriptor } from "../../api/types";
import { Text } from "../../design/Text";
import { StatValue } from "../../design/StatValue";
import { RankChip } from "../../design/RankChip";
import { PlayerAvatar } from "../../design/PlayerAvatar";
import { TeamBadge } from "../../design/TeamBadge";
import { ErrorTile } from "../../design/StateViews";
import { shortLabel } from "../../design/availability";
import { EM_DASH, formatValue, seasonContext } from "../../design/format";
import { decodeLeaderboardPayload } from "./payload";
import styles from "./leaderboard.module.css";

const ROW_LIMIT: Readonly<Record<WidgetSizeKey, number>> = { small: 3, medium: 6, large: 10 };

interface SecondaryColumn {
  readonly index: number;
  readonly descriptor: MetricDescriptor;
}

function secondaryColumns(payload: LeaderboardPayload, size: WidgetSizeKey): readonly SecondaryColumn[] {
  // Only the large tile has room for extra numeric columns (WEB_DESIGN.md §7.7).
  if (size !== "large") return [];
  return payload.secondaryMetrics.slice(0, 3).map((descriptor, index) => ({ index, descriptor }));
}

/** Matches a secondary value by metric key first, array position only as a fallback — an unkeyed
 * row from an older server shape still lines its columns up correctly. */
function secondaryValue(row: LeaderboardRow, column: SecondaryColumn) {
  return row.secondary.find((entry) => entry.metric === column.descriptor.key) ?? row.secondary[column.index] ?? null;
}

function secondaryText(row: LeaderboardRow, column: SecondaryColumn): string {
  const value = secondaryValue(row, column);
  if (!value) return EM_DASH;
  return value.displayValue.length > 0
    ? value.displayValue
    : formatValue(value.value, column.descriptor.format as MetricFormat);
}

function contextLine(payload: LeaderboardPayload): string {
  const parts: string[] = [];
  if (payload.scope === "all_time") {
    parts.push("All time");
  } else {
    const context = seasonContext(payload.season, payload.seasonType, payload.perMode);
    if (context) parts.push(context);
  }
  parts.push(payload.subjectType === "team" ? "Teams" : "Players");
  return parts.join(" · ");
}

/** A ten-row leaderboard gets 24px portraits or none, never both a portrait and a numeric
 * column competing for the same width (CONTRACT.md §7.7). */
function subjectShowsAvatar(size: WidgetSizeKey, columns: readonly SecondaryColumn[]): boolean {
  return size !== "small" && columns.length === 0;
}

function subjectKey(row: LeaderboardRow): string {
  const subject = row.player ? `p${row.player.playerId}` : row.team ? `t${row.team.teamId}` : "unknown";
  return `${row.season}-${subject}-${row.rank}`;
}

function accessibleRowLabel(payload: LeaderboardPayload, row: LeaderboardRow, columns: readonly SecondaryColumn[]): string {
  const parts: string[] = [];
  if (row.rank > 0) parts.push(`number ${row.rank}`);
  parts.push(row.player?.name ?? row.team?.name ?? "Unknown");
  if (payload.scope === "all_time" && row.season) parts.push(row.season);
  const valueText =
    row.value.availability === "unavailable" ? "not available" : row.value.displayValue.length > 0 ? row.value.displayValue : EM_DASH;
  parts.push(`${payload.metric.name} ${valueText}`);
  if (row.value.availability !== "full") parts.push(shortLabel(row.value.availability));
  for (const column of columns) {
    parts.push(`${column.descriptor.name} ${secondaryText(row, column)}`);
  }
  return parts.join(", ");
}

function SubjectBadge({ row, showsAvatar }: { readonly row: LeaderboardRow; readonly showsAvatar: boolean }): JSX.Element | null {
  if (row.team) {
    return <TeamBadge abbreviation={row.team.abbr} name={row.team.name} size="small" />;
  }
  if (row.player && showsAvatar) {
    return (
      <span className={styles.badge}>
        <PlayerAvatar player={row.player} size="small" />
        {row.player.teamAbbr && <TeamBadge abbreviation={row.player.teamAbbr} size="small" />}
      </span>
    );
  }
  if (row.player?.teamAbbr) {
    return <TeamBadge abbreviation={row.player.teamAbbr} size="small" />;
  }
  return null;
}

function LeaderboardRowView({
  payload,
  row,
  columns,
  showsAvatar,
}: {
  readonly payload: LeaderboardPayload;
  readonly row: LeaderboardRow;
  readonly columns: readonly SecondaryColumn[];
  readonly showsAvatar: boolean;
}): JSX.Element {
  return (
    <div className={styles.row} role="group" aria-label={accessibleRowLabel(payload, row, columns)}>
      <div className={styles.rank} aria-hidden="true">
        <RankChip rank={row.rank} />
      </div>
      <div className={styles.badge} aria-hidden="true">
        <SubjectBadge row={row} showsAvatar={showsAvatar} />
      </div>
      <div className={styles.subject} aria-hidden="true">
        <span className={styles.subjectName}>
          <Text style="tableCell" className={styles.subjectNameText} truncate>
            {row.player?.name ?? row.team?.name ?? EM_DASH}
          </Text>
        </span>
        {payload.scope === "all_time" && row.season && (
          <Text as="span" style="caption">
            {row.season}
          </Text>
        )}
      </div>
      <div className={clsx(styles.primaryValue, columns.length === 0 && styles.primaryValueWide)} aria-hidden="true">
        <StatValue
          value={row.value}
          descriptor={payload.metric}
          style="stat"
          showsLabel={false}
          showsRank={false}
          showsDelta={false}
          season={row.season}
          align="trailing"
        />
      </div>
      {columns.map((column) => (
        <div key={column.descriptor.key} className={styles.secondaryCell} aria-hidden="true">
          <Text style="tableCell" tabularNums>
            {secondaryText(row, column)}
          </Text>
        </div>
      ))}
    </div>
  );
}

function LeaderboardWidgetView({ size, payload: rawPayload }: WidgetViewProps): JSX.Element {
  let payload: LeaderboardPayload;
  try {
    payload = decodeLeaderboardPayload(rawPayload);
  } catch {
    return (
      <ErrorTile
        size={size}
        isRetryable={false}
        message="This leaderboard's data did not match what the app expected."
      />
    );
  }

  const columns = secondaryColumns(payload, size);
  const showsAvatar = subjectShowsAvatar(size, columns);
  const limit = ROW_LIMIT[size] ?? ROW_LIMIT.medium;
  const rows = payload.rows.slice(0, Math.max(limit, 0));

  return (
    <div className={styles.root}>
      <div>
        <span>
          <Text style="widgetTitle" truncate>
            {payload.metric.name}
          </Text>{" "}
          <Text style="tableHeader">{payload.metric.shortName}</Text>
        </span>
        <Text as="p" style="caption" className={styles.contextLine}>
          {contextLine(payload)}
        </Text>
      </div>
      {columns.length > 0 && (
        <div className={styles.columnHeader} aria-hidden="true">
          <div className={styles.columnHeaderSpacer} />
          <div className={styles.valueHeaderCell}>
            <Text style="tableHeader">{payload.metric.shortName}</Text>
          </div>
          {columns.map((column) => (
            <div key={column.descriptor.key} className={styles.columnHeaderCell}>
              <Text style="tableHeader">{column.descriptor.shortName}</Text>
            </div>
          ))}
        </div>
      )}
      {rows.length === 0 ? (
        <div className={styles.emptyNote} role="status">
          <Text as="p" style="caption">
            No one qualified for this leaderboard.
          </Text>
        </div>
      ) : (
        <div className={styles.rows}>
          {rows.map((row) => (
            <LeaderboardRowView
              key={subjectKey(row)}
              payload={payload}
              row={row}
              columns={columns}
              showsAvatar={showsAvatar}
            />
          ))}
        </div>
      )}
      {payload.qualifier && payload.qualifier.length > 0 && (
        <Text as="p" style="caption" className={styles.qualifier}>
          {payload.qualifier}
        </Text>
      )}
      <div className={styles.spacer} />
    </div>
  );
}

export default LeaderboardWidgetView;
