/**
 * `team_efficiency` — the league table by offence, defence, net rating and pace, or (at
 * `style: "scatter"`) offensive rating plotted against **negated** defensive rating so "up and to
 * the right" always reads as a good team. Ported from
 * `ios/NBAStats/Widgets/TeamEfficiencyWidget.swift`.
 *
 * `rank` on every row is the league-wide rank even under a conference filter (CONTRACT.md §7.7),
 * so the visible table can legitimately read `1, 3, 4, 7…` — that is the honest answer to "how
 * good is this offence?", not a bug in the row order.
 */
import type { JSX } from "react";
import type { WidgetViewProps } from "../../generated/registry";
import type { WidgetSizeKey } from "../../generated/tokens";
import type { MetricFormat } from "../../generated/contracts";
import type { TeamEfficiencyPayload, TeamEfficiencyRow } from "../../api/types";
import { Text } from "../../design/Text";
import { TeamBadge } from "../../design/TeamBadge";
import { PinnedColumnTable } from "../../design/PinnedColumnTable";
import type { PinnedColumnDef } from "../../design/PinnedColumnTable";
import { ErrorTile } from "../../design/StateViews";
import { ScatterPlot } from "../../design/svg/ScatterPlot";
import { normalizeInDomain, resolveYDomain } from "../../design/svg/CartesianFrame";
import type { CartesianTick } from "../../design/svg/CartesianFrame";
import { comparisonColorVar, comparisonOutcomeAgainst, monogramColorVar } from "../../design/valueColor";
import { EM_DASH, formatInteger, formatValue, seasonContext } from "../../design/format";
import { decodeTeamEfficiencyPayload } from "./payload";
import { useChartWidth } from "../../design/useChartWidth";
import styles from "./index.module.css";

interface ColumnSpec {
  readonly id: string;
  readonly title: string;
  readonly format: MetricFormat;
  readonly higherIsBetter: boolean;
  readonly isDirectional: boolean;
}

const OFFENSE_COL: ColumnSpec = { id: "off_rtg", title: "ORtg", format: "rating1", higherIsBetter: true, isDirectional: true };
const DEFENSE_COL: ColumnSpec = { id: "def_rtg", title: "DRtg", format: "rating1", higherIsBetter: false, isDirectional: true };
const NET_COL: ColumnSpec = { id: "net_rtg", title: "NetRtg", format: "plusMinus1", higherIsBetter: true, isDirectional: true };
const PACE_COL: ColumnSpec = { id: "pace", title: "Pace", format: "decimal1", higherIsBetter: true, isDirectional: false };

const ROW_LIMIT: Readonly<Record<WidgetSizeKey, number>> = { small: 4, medium: 6, large: 10 };

function columnsForSize(size: WidgetSizeKey): readonly ColumnSpec[] {
  return size === "large" ? [OFFENSE_COL, DEFENSE_COL, NET_COL, PACE_COL] : [NET_COL, OFFENSE_COL, DEFENSE_COL];
}

function columnValue(row: TeamEfficiencyRow, key: string): number | null {
  const value = row.values[key];
  return typeof value === "number" ? value : null;
}

function columnRank(row: TeamEfficiencyRow, key: string): number | null {
  const rank = row.ranks[key];
  return typeof rank === "number" && rank > 0 ? rank : null;
}

function recordText(row: TeamEfficiencyRow): string {
  return row.wins !== null && row.losses !== null ? `${row.wins}-${row.losses}` : EM_DASH;
}

function accessibleRowText(row: TeamEfficiencyRow, columns: readonly ColumnSpec[], showsRecord: boolean): string {
  const parts: string[] = [row.rank > 0 ? `number ${row.rank}` : "unranked", row.team.name];
  if (showsRecord) parts.push(recordText(row));
  for (const column of columns) {
    const value = columnValue(row, column.id);
    const text = formatValue(value, column.format);
    const rank = columnRank(row, column.id);
    parts.push(rank !== null ? `${column.title} ${text}, ranked ${rank} in the league` : `${column.title} ${text}`);
  }
  return parts.join(", ");
}

function ValueCell({ row, column, leagueAverage }: { readonly row: TeamEfficiencyRow; readonly column: ColumnSpec; readonly leagueAverage: number | null }): JSX.Element {
  const value = columnValue(row, column.id);
  const rank = columnRank(row, column.id);
  const outcome = column.isDirectional ? comparisonOutcomeAgainst(value, leagueAverage, column.higherIsBetter) : "neutral";
  const colorVar = column.isDirectional ? comparisonColorVar(outcome) : undefined;
  return (
    <span className={styles.valueCell} aria-hidden="true">
      <Text style="tableCell" tabularNums color={value === null ? "tertiary" : undefined} htmlStyle={value !== null && colorVar ? { color: colorVar } : undefined}>
        {formatValue(value, column.format)}
      </Text>
      {rank !== null && (
        <Text style="tableHeader" tabularNums color="tertiary">
          {rank}
        </Text>
      )}
    </span>
  );
}

function buildColumns(columns: readonly ColumnSpec[], sortBy: string, showsRecord: boolean, leagueAverage: TeamEfficiencyPayload["leagueAverage"]): readonly PinnedColumnDef<TeamEfficiencyRow>[] {
  const defs: PinnedColumnDef<TeamEfficiencyRow>[] = [];
  if (showsRecord) {
    defs.push({
      key: "record",
      align: "trailing",
      header: (
        <Text style="tableHeader" ariaHidden>
          W-L
        </Text>
      ),
      render: (row) => (
        <Text style="tableCell" tabularNums color="secondary" ariaHidden>
          {recordText(row)}
        </Text>
      ),
    });
  }
  for (const column of columns) {
    defs.push({
      key: column.id,
      align: "trailing",
      header: (
        <Text style="tableHeader" color={column.id === sortBy ? "selection" : "secondary"} ariaHidden>
          {column.title}
        </Text>
      ),
      render: (row) => <ValueCell row={row} column={column} leagueAverage={leagueAverage[column.id] ?? null} />,
    });
  }
  return defs;
}

function PinnedIdentityCell({ row, columns, showsRecord }: { readonly row: TeamEfficiencyRow; readonly columns: readonly ColumnSpec[]; readonly showsRecord: boolean }): JSX.Element {
  return (
    <span className={styles.pinnedCell} role="group" aria-label={accessibleRowText(row, columns, showsRecord)}>
      <Text style="tableHeader" tabularNums color="tertiary" className={styles.pinnedRank} ariaHidden>
        {row.rank > 0 ? formatInteger(row.rank) : EM_DASH}
      </Text>
      <TeamBadge abbreviation={row.team.abbr} name={row.team.name} size="small" />
    </span>
  );
}

function EfficiencyTable({ payload, size }: { readonly payload: TeamEfficiencyPayload; readonly size: WidgetSizeKey }): JSX.Element {
  const showsRecord = size === "large";
  const columns = columnsForSize(size);
  const limit = ROW_LIMIT[size] ?? ROW_LIMIT.medium;
  const rows = payload.rows.slice(0, Math.max(limit, 0));
  const columnDefs = buildColumns(columns, payload.sortBy, showsRecord, payload.leagueAverage);
  const remaining = payload.rows.length - rows.length;

  return (
    <div className={styles.tableWrap}>
      <PinnedColumnTable
        rows={rows}
        rowKey={(row) => `${row.team.teamId}`}
        pinnedColumn={{
          key: "identity",
          header: (
            <span className={styles.pinnedHeader} aria-hidden="true">
              <Text style="tableHeader">#</Text>
              <Text style="tableHeader">Team</Text>
            </span>
          ),
          render: (row) => <PinnedIdentityCell row={row} columns={columns} showsRecord={showsRecord} />,
        }}
        columns={columnDefs}
        pinnedWidth={96}
        columnWidth={60}
      />
      {remaining > 0 && (
        <Text as="p" style="caption" color="tertiary" className={styles.moreNote}>
          {remaining} more team{remaining === 1 ? "" : "s"} at a larger size.
        </Text>
      )}
    </div>
  );
}

const SCATTER_HEIGHT: Readonly<Record<WidgetSizeKey, number>> = { small: 140, medium: 190, large: 250 };

function evenTicks(domain: readonly [number, number], count: number): readonly number[] {
  const [low, high] = domain;
  if (!Number.isFinite(low) || !Number.isFinite(high) || high <= low || count < 2) return [low];
  const step = (high - low) / (count - 1);
  return Array.from({ length: count }, (_, index) => low + step * index);
}

function EfficiencyScatter({ payload, size }: { readonly payload: TeamEfficiencyPayload; readonly size: WidgetSizeKey }): JSX.Element {
  const [containerRef, measuredWidth] = useChartWidth(320);
  const height = SCATTER_HEIGHT[size] ?? SCATTER_HEIGHT.medium;

  const points = payload.rows
    .map((row) => {
      const offense = columnValue(row, "off_rtg");
      const defense = columnValue(row, "def_rtg");
      if (offense === null || !Number.isFinite(offense) || defense === null || !Number.isFinite(defense)) return null;
      return {
        id: `${row.team.teamId}`,
        x: offense,
        y: -defense,
        label: row.team.abbr,
        name: row.team.name,
        color: monogramColorVar(row.team.abbr),
      };
    })
    .filter((point): point is NonNullable<typeof point> => point !== null);

  const leagueOffense = payload.leagueAverage.off_rtg ?? null;
  const leagueDefense = payload.leagueAverage.def_rtg ?? null;

  if (points.length === 0) {
    return (
      <Text as="p" style="caption" className={styles.emptyNote}>
        No team has both an offensive and a defensive rating for this season.
      </Text>
    );
  }

  const xValues = points.map((point) => point.x).concat(leagueOffense !== null ? [leagueOffense] : []);
  const yValues = points.map((point) => point.y).concat(leagueDefense !== null ? [-leagueDefense] : []);
  const xDomain = resolveYDomain({ values: xValues });
  const yDomain = resolveYDomain({ values: yValues });

  const xTicks: readonly CartesianTick[] = evenTicks(xDomain, 4).map((value) => ({
    position: normalizeInDomain(value, xDomain),
    label: formatValue(value, "rating1"),
  }));
  const yTicks: readonly CartesianTick[] = evenTicks(yDomain, 4).map((value) => ({
    position: normalizeInDomain(value, yDomain),
    label: formatValue(-value, "rating1"),
  }));

  const leaders = [...points]
    .sort((a, b) => a.x + a.y - (b.x + b.y))
    .reverse()
    .slice(0, 3)
    .map((point) => `${point.label} net position ${formatValue(point.x + point.y, "decimal1")}`);
  const accessibleLabel = [
    `Scatter of ${points.length} teams: offensive rating on the horizontal axis, defensive rating inverted on the vertical axis so a higher point is a better defense.`,
    leagueOffense !== null && leagueDefense !== null
      ? `Dashed crosshairs mark the league average, ${formatValue(leagueOffense, "rating1")} offense and ${formatValue(leagueDefense, "rating1")} defense.`
      : null,
    leaders.length > 0 ? `Leaders: ${leaders.join(", ")}.` : null,
  ]
    .filter((line): line is string => line !== null)
    .join(" ");

  return (
    <div className={styles.scatterColumn}>
      <Text as="p" style="caption" className={styles.scatterCaption}>
        Better defense ↑ · Defensive rating is inverted, so up and right is a good team.
      </Text>
      <div ref={containerRef} className={styles.scatterWrap} role="img" aria-label={accessibleLabel}>
        <ScatterPlot
          points={points}
          xDomain={xDomain}
          yDomain={yDomain}
          width={measuredWidth}
          height={height}
          crosshair={{ x: leagueOffense ?? undefined, y: leagueDefense !== null ? -leagueDefense : undefined }}
          xTicks={xTicks}
          yTicks={yTicks}
        />
      </div>
      <Text as="p" style="caption" color="tertiary">
        Offensive rating → · points scored per 100 possessions.
      </Text>
    </div>
  );
}

function TeamEfficiencyWidgetView({ size, payload: rawPayload }: WidgetViewProps): JSX.Element {
  let payload: TeamEfficiencyPayload;
  try {
    payload = decodeTeamEfficiencyPayload(rawPayload);
  } catch {
    return <ErrorTile size={size} isRetryable={false} message="This team efficiency table did not match what the app expected." />;
  }

  const usesScatter = payload.style === "scatter";
  const contextText = seasonContext(payload.season, payload.seasonType);

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        <Text style="statLabel" truncate>
          {usesScatter ? "Offense vs defense" : "League table"}
        </Text>
        {contextText.length > 0 && (
          <Text as="span" style="caption">
            {contextText}
          </Text>
        )}
      </div>
      {payload.rows.length === 0 ? (
        <Text as="p" style="caption" className={styles.emptyNote}>
          No team ratings for this season yet.
        </Text>
      ) : usesScatter ? (
        <EfficiencyScatter payload={payload} size={size} />
      ) : (
        <EfficiencyTable payload={payload} size={size} />
      )}
      <div className={styles.spacer} />
    </div>
  );
}

export default TeamEfficiencyWidgetView;
