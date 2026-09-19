/**
 * `/today` — **daily stats**: three `daily_movers` panels (best / worst / surprise) sharing one
 * metric picker, per WEB_DESIGN.md §7.2.
 */
import { useState, type JSX } from "react";
import { Link } from "react-router-dom";
import { DashboardResolveProvider, useWidgetResult } from "../dashboard/DashboardResolveContext";
import { METRICS_DOCUMENT } from "../generated/contracts";
import { Text } from "../design/Text";
import { PlayerAvatar } from "../design/PlayerAvatar";
import { RankChip } from "../design/RankChip";
import { DeltaChip } from "../design/DeltaChip";
import { LoadingTile, ErrorTile, EmptyTile } from "../design/StateViews";
import { useAuth } from "../auth/AuthProvider";
import type { DailyMoversPayload, ResolveWidgetRequest } from "../api/types";
import type { MetricFormat } from "../generated/contracts";
import styles from "./Today.module.css";

const DIRECTIONS = ["best", "worst", "surprise"] as const;
type Direction = (typeof DIRECTIONS)[number];

const DIRECTION_LABEL: Record<Direction, string> = {
  best: "Best games",
  worst: "Worst games",
  surprise: "Biggest surprises",
};

const METRIC_OPTIONS = (
  METRICS_DOCUMENT.metrics as ReadonlyArray<{ key: string; name: string; scope: readonly string[] }>
).filter((metric) => metric.scope.includes("player"));

function Panel({ direction, metric }: { readonly direction: Direction; readonly metric: string }): JSX.Element {
  const widget: ResolveWidgetRequest & { readonly kind: "daily_movers" } = {
    id: `today.${direction}`,
    kind: "daily_movers",
    size: "large",
    config: { date: "latest", metric, direction, limit: 8, minMinutes: 12 },
  };
  const result = useWidgetResult(widget);

  return (
    <div>
      <Text style="widgetTitle">{DIRECTION_LABEL[direction]}</Text>
      {!result ? (
        <LoadingTile size="medium" />
      ) : result.status === "error" ? (
        <ErrorTile message={result.error?.message ?? "Could not load this panel."} />
      ) : (
        <PanelRows payload={result.payload} />
      )}
    </div>
  );
}

function PanelRows({ payload }: { readonly payload: DailyMoversPayload | null }): JSX.Element {
  if (!payload || payload.rows.length === 0) {
    return <EmptyTile message="No qualifying games yet today." />;
  }
  const format = (payload.metric.format ?? "decimal1") as MetricFormat;
  return (
    <div>
      {payload.rows.map((row) => (
        <Link key={row.gameId + row.player.playerId} to={`/player/${row.player.playerId}`} className={styles.row} style={{ textDecoration: "none", color: "inherit" }}>
          <RankChip rank={row.rank} />
          <PlayerAvatar player={row.player} size="small" />
          <div style={{ flex: 1, minWidth: 0 }}>
            <Text style="tableCell" truncate>
              {row.player.name}
            </Text>
            <Text style="caption" color="secondary" truncate>
              {row.line}
              {row.opponentAbbr ? ` vs ${row.opponentAbbr}` : ""}
            </Text>
          </div>
          <DeltaChip delta={row.delta} higherIsBetter={payload.metric.higherIsBetter} format={format} />
        </Link>
      ))}
    </div>
  );
}

export default function Today(): JSX.Element {
  const [metric, setMetric] = useState("game_score");
  const { user } = useAuth();

  const widgets: ResolveWidgetRequest[] = DIRECTIONS.map((direction) => ({
    id: `today.${direction}`,
    kind: "daily_movers",
    size: "large",
    config: { date: "latest", metric, direction, limit: 8, minMinutes: 12 },
  }));

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--hw-space-sm)", flexWrap: "wrap" }}>
        <Text as="p" style="sectionTitle">
          Today
        </Text>
        <label>
          <Text style="tableHeader" as="span">
            Metric
          </Text>
          <select value={metric} onChange={(event) => setMetric(event.target.value)}>
            {METRIC_OPTIONS.map((option) => (
              <option key={option.key} value={option.key}>
                {option.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <DashboardResolveProvider
        widgets={widgets}
        options={{ context: { favoritePlayerId: user?.favoritePlayerId, favoriteTeamId: user?.favoriteTeamId } }}
      >
        <div className={styles.panels}>
          {DIRECTIONS.map((direction) => (
            <Panel key={direction} direction={direction} metric={metric} />
          ))}
        </div>
      </DashboardResolveProvider>
    </div>
  );
}
