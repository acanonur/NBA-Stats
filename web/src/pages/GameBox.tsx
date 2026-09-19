/**
 * `/tonight/:gameId` — one game's box score, Basic / Advanced / Both, "now with `fantasy_pts`"
 * (`routes_games.py::BASIC_PLAYER_METRICS`). A REST page, not a widget: `GET
 * /v1/games/{gameId}/box` is outside the sixteen dashboard-widget payloads.
 */
import { useState, type JSX } from "react";
import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ApiError, getBoxScore, type BoxScorePlayerLine } from "../api/dashboards";
import { METRICS_DOCUMENT } from "../generated/contracts";
import type { MetricFormat } from "../generated/contracts";
import { Text } from "../design/Text";
import { PlayerAvatar } from "../design/PlayerAvatar";
import { TeamBadge } from "../design/TeamBadge";
import { PinnedColumnTable, type PinnedColumnDef } from "../design/PinnedColumnTable";
import { AvailabilityBadge } from "../design/AvailabilityBadge";
import { formatValue, mediumGameDate } from "../design/format";
import { LoadingTile, ErrorTile } from "../design/StateViews";
import styles from "./GameBox.module.css";

const METRIC_LOOKUP = new Map(
  (METRICS_DOCUMENT.metrics as ReadonlyArray<{ key: string; shortName: string; format: string }>).map(
    (metric) => [metric.key, metric],
  ),
);

function columnsFor(players: readonly BoxScorePlayerLine[]): readonly string[] {
  const seen = new Set<string>();
  const ordered: string[] = [];
  for (const player of players) {
    for (const key of Object.keys(player.values)) {
      if (!seen.has(key)) {
        seen.add(key);
        ordered.push(key);
      }
    }
  }
  return ordered;
}

export default function GameBox(): JSX.Element {
  const { gameId } = useParams<{ gameId: string }>();
  const [view, setView] = useState<"basic" | "advanced" | "both">("both");
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["boxScore", gameId, view],
    queryFn: () => getBoxScore(gameId!, view),
    enabled: !!gameId,
  });

  if (!gameId) return <ErrorTile message="No game was named." isRetryable={false} />;
  if (isLoading) return <LoadingTile size="large" />;
  if (error) {
    return (
      <ErrorTile
        message={error instanceof ApiError ? error.message : "This box score could not be loaded."}
        onRetry={() => void refetch()}
      />
    );
  }
  if (!data) return <ErrorTile message="This box score could not be loaded." isRetryable={false} />;

  return (
    <div>
      <Text as="p" style="sectionTitle">
        {data.game.away.abbr} @ {data.game.home.abbr}
      </Text>
      <Text style="caption" color="secondary">
        {mediumGameDate(data.game.date)}
        {data.game.status === "final" && data.game.homePts !== null && data.game.awayPts !== null && (
          <> · Final {data.game.awayPts}–{data.game.homePts}</>
        )}
      </Text>
      <div className={styles.tabs} role="tablist" aria-label="Box score view">
        {(["basic", "advanced", "both"] as const).map((option) => (
          <button
            key={option}
            type="button"
            role="tab"
            aria-selected={view === option}
            className={`${styles.tab} ${view === option ? styles.tabActive : ""}`}
            onClick={() => setView(option)}
          >
            {option[0].toUpperCase() + option.slice(1)}
          </button>
        ))}
      </div>
      {data.teams.map((side) => {
        const columns = columnsFor(side.players);
        const columnDefs: PinnedColumnDef<BoxScorePlayerLine>[] = columns.map((key) => ({
          key,
          header: (
            <Text style="tableHeader" tabularNums>
              {METRIC_LOOKUP.get(key)?.shortName ?? key}
            </Text>
          ),
          align: "trailing",
          render: (row) => {
            const value = row.values[key];
            const format = (METRIC_LOOKUP.get(key)?.format ?? "decimal1") as MetricFormat;
            return (
              <Text style="tableCell" tabularNums>
                {formatValue(value, format)}
              </Text>
            );
          },
        }));

        return (
          <div key={side.team.teamId} className={styles.side}>
            <div style={{ display: "flex", alignItems: "center", gap: "var(--hw-space-sm)", marginBottom: "var(--hw-space-sm)" }}>
              <TeamBadge abbreviation={side.team.abbr} name={side.team.name} size="medium" />
              <Text style="widgetTitle">{side.team.name}</Text>
              <AvailabilityBadge availability="full" showsText={false} isInteractive={false} />
            </div>
            <PinnedColumnTable
              rows={side.players}
              rowKey={(row) => String(row.player.playerId)}
              pinnedColumn={{
                key: "player",
                header: <Text style="tableHeader">Player</Text>,
                render: (row) => (
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--hw-space-xs)" }}>
                    <PlayerAvatar player={row.player} size="small" />
                    <Text style="tableCell" truncate>
                      {row.player.name}
                    </Text>
                  </div>
                ),
              }}
              columns={columnDefs}
              pinnedWidth={160}
            />
          </div>
        );
      })}
    </div>
  );
}
