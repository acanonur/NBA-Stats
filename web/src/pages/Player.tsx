/**
 * `/player/:playerId` — **highlighted player stats**: snapshot, game log, career arc, shot
 * profile, next-game projection, and "Pin as my player". Every section hosts one of the sixteen
 * widget kinds through the same resolve pipeline a dashboard tile uses, rendered with this
 * page's own full-width layout rather than a tile's.
 */
import { useState, type JSX } from "react";
import { useParams } from "react-router-dom";
import { DashboardResolveProvider, useWidgetResult } from "../dashboard/DashboardResolveContext";
import { useAuth } from "../auth/AuthProvider";
import { Text } from "../design/Text";
import { PlayerAvatar } from "../design/PlayerAvatar";
import { PercentileBar } from "../design/PercentileBar";
import { ProjectionIntervalBar } from "../design/ProjectionIntervalBar";
import { PinnedColumnTable, type PinnedColumnDef } from "../design/PinnedColumnTable";
import { LoadingTile, ErrorTile, EmptyTile } from "../design/StateViews";
import { formatValue } from "../design/format";
import type { GameLogRow, ResolveWidgetRequest } from "../api/types";
import type { MetricFormat } from "../generated/contracts";
import styles from "./Player.module.css";

const SNAPSHOT_METRICS = ["ts_pct", "usg_pct", "ast_pct", "reb_pct", "off_rtg", "def_rtg", "net_rtg", "pie"];
const GAME_LOG_COLUMNS = ["min", "pts", "reb", "ast", "ts_pct", "usg_pct", "plus_minus", "game_score"];
const PROJECTION_STATS = ["pts", "reb", "ast", "fg3m", "stl", "blk", "tov"];

function usePlayerWidgets(playerId: number): readonly ResolveWidgetRequest[] {
  return [
    {
      id: "player.snapshot",
      kind: "player_snapshot",
      size: "large",
      config: { playerId, season: "latest", seasonType: "Regular Season", metrics: SNAPSHOT_METRICS, showPercentiles: true },
    },
    {
      id: "player.gameLog",
      kind: "game_log",
      size: "large",
      config: { playerId, season: "latest", seasonType: "Regular Season", columns: GAME_LOG_COLUMNS, limit: 10 },
    },
    {
      id: "player.careerArc",
      kind: "career_arc",
      size: "large",
      config: { playerId, metric: "per", seasonType: "Regular Season", includePlayoffs: true, xAxis: "season" },
    },
    {
      id: "player.shotProfile",
      kind: "shot_profile",
      size: "large",
      config: { subjectType: "player", subjectId: playerId, season: "latest", seasonType: "Regular Season", compareToLeague: true },
    },
    {
      id: "player.nextGame",
      kind: "next_game_projection",
      size: "large",
      config: { playerId, stats: PROJECTION_STATS, season: "latest", seasonType: "Regular Season", showCombo: true, showFactors: true },
    },
  ];
}

function SnapshotSection({ playerId }: { readonly playerId: number }): JSX.Element {
  const result = useWidgetResult({
    id: "player.snapshot",
    kind: "player_snapshot",
    size: "large",
    config: { playerId, season: "latest", seasonType: "Regular Season", metrics: SNAPSHOT_METRICS, showPercentiles: true },
  });
  if (!result) return <LoadingTile size="large" />;
  if (result.status === "error") return <ErrorTile message={result.error?.message ?? "Could not load this player."} />;
  const payload = result.payload;
  if (!payload) return <EmptyTile message="No data for this player." />;

  return (
    <div className={styles.header}>
      <PlayerAvatar player={payload.player} size="large" />
      <div>
        <Text as="p" style="displayValue">
          {payload.player.name}
        </Text>
        <Text style="caption" color="secondary">
          {payload.teamAbbr ?? "Free agent"} · {payload.season} · GP {payload.gp ?? "—"} · MPG{" "}
          {formatValue(payload.minutesPerGame, "decimal1")}
        </Text>
        <div style={{ marginTop: "var(--hw-space-sm)", display: "flex", flexDirection: "column", gap: "var(--hw-space-xs)" }}>
          {payload.metrics.map((metric) => (
            <div key={metric.metric} className={styles.metricRow}>
              <Text style="statLabel" as="span" htmlStyle={{ minWidth: 90 }}>
                {metric.metric}
              </Text>
              <PercentileBar percentile={metric.percentile} tint="var(--hw-accent-orange)" />
              <Text style="statValue" tabularNums>
                {metric.displayValue}
              </Text>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function GameLogSection({ playerId }: { readonly playerId: number }): JSX.Element {
  const result = useWidgetResult({
    id: "player.gameLog",
    kind: "game_log",
    size: "large",
    config: { playerId, season: "latest", seasonType: "Regular Season", columns: GAME_LOG_COLUMNS, limit: 10 },
  });
  if (!result) return <LoadingTile size="large" />;
  if (result.status === "error") return <ErrorTile message={result.error?.message ?? "Could not load the game log."} />;
  const payload = result.payload;
  if (!payload || payload.rows.length === 0) return <EmptyTile message="No games recorded yet this season." />;

  const columns: PinnedColumnDef<GameLogRow>[] = payload.columns.map((descriptor) => ({
    key: descriptor.key,
    header: (
      <Text style="tableHeader" tabularNums>
        {descriptor.shortName}
      </Text>
    ),
    align: "trailing",
    isHighlighted: (row) => {
      const value = row.values[descriptor.key];
      const best = payload.seasonBests[descriptor.key];
      return value !== null && best !== null && value !== undefined && best !== undefined && Math.abs(value - best) < 1e-9;
    },
    render: (row) => {
      const value = row.values[descriptor.key];
      return (
        <Text style="tableCell" tabularNums>
          {value === undefined ? "—" : formatValue(value, descriptor.format as MetricFormat)}
        </Text>
      );
    },
  }));

  return (
    <PinnedColumnTable
      rows={payload.rows}
      rowKey={(row) => row.gameId}
      pinnedColumn={{
        key: "date",
        header: <Text style="tableHeader">Date</Text>,
        render: (row) => (
          <div>
            <Text style="tableCell">{row.date.slice(5)}</Text>
            <Text style="caption" color="secondary">
              {row.isHome ? "vs" : "@"} {row.opponentAbbr ?? "—"}
            </Text>
          </div>
        ),
      }}
      columns={columns}
    />
  );
}

function ShotProfileSection({ playerId }: { readonly playerId: number }): JSX.Element {
  const result = useWidgetResult({
    id: "player.shotProfile",
    kind: "shot_profile",
    size: "large",
    config: { subjectType: "player", subjectId: playerId, season: "latest", seasonType: "Regular Season", compareToLeague: true },
  });
  if (!result) return <LoadingTile size="large" />;
  if (result.status === "error") return <ErrorTile message={result.error?.message ?? "Could not load the shot profile."} />;
  const payload = result.payload;
  if (!payload) return <EmptyTile message="No shot data for this player." />;

  return (
    <div>
      {payload.note && (
        <Text as="p" style="caption" color="secondary">
          {payload.note}
        </Text>
      )}
      <div className={styles.zoneRow}>
        <Text style="tableHeader">Zone</Text>
        <Text style="tableHeader" tabularNums>
          FGA
        </Text>
        <Text style="tableHeader" tabularNums>
          FG%
        </Text>
        <Text style="tableHeader" tabularNums>
          PPS
        </Text>
      </div>
      {payload.zones.map((zone) => (
        <div key={zone.zone} className={styles.zoneRow}>
          <Text style="tableCell">{zone.label}</Text>
          <Text style="tableCell" tabularNums>
            {formatValue(zone.fga, "integer")}
          </Text>
          <Text style="tableCell" tabularNums>
            {formatValue(zone.fgPct, "percent1")}
          </Text>
          <Text style="tableCell" tabularNums>
            {formatValue(zone.pointsPerShot, "decimal2")}
          </Text>
        </div>
      ))}
    </div>
  );
}

function CareerArcSection({ playerId }: { readonly playerId: number }): JSX.Element {
  const result = useWidgetResult({
    id: "player.careerArc",
    kind: "career_arc",
    size: "large",
    config: { playerId, metric: "per", seasonType: "Regular Season", includePlayoffs: true, xAxis: "season" },
  });
  if (!result) return <LoadingTile size="large" />;
  if (result.status === "error") return <ErrorTile message={result.error?.message ?? "Could not load the career arc."} />;
  const payload = result.payload;
  if (!payload || payload.seasons.length === 0) return <EmptyTile message="No career data yet." />;

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--hw-space-md)" }}>
      {payload.seasons.map((season) => (
        <div key={season.season} style={{ minWidth: 72 }}>
          <Text style="caption" color="secondary">
            {season.season}
          </Text>
          <Text style="statValue" tabularNums>
            {season.displayValue}
          </Text>
        </div>
      ))}
    </div>
  );
}

function NextGameSection({ playerId }: { readonly playerId: number }): JSX.Element {
  const result = useWidgetResult({
    id: "player.nextGame",
    kind: "next_game_projection",
    size: "large",
    config: { playerId, stats: PROJECTION_STATS, season: "latest", seasonType: "Regular Season", showCombo: true, showFactors: true },
  });
  if (!result) return <LoadingTile size="large" />;
  if (result.status === "error") return <ErrorTile message={result.error?.message ?? "Could not load the projection."} />;
  const payload = result.payload;
  if (!payload) return <EmptyTile message="No projection available." />;
  if (!payload.game) {
    return (
      <EmptyTile message={payload.notes[0] ?? "No scheduled game — projected against a league-average opponent."} />
    );
  }

  return (
    <div>
      <Text style="caption" color="secondary">
        Next: {payload.game.isHome ? "vs" : "@"} {payload.game.opponentAbbr}
      </Text>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--hw-space-md)", marginTop: "var(--hw-space-sm)" }}>
        {payload.lines.map((line) =>
          line.low === null || line.high === null ? (
            <div key={line.metric}>
              <Text style="statLabel">{line.metric}</Text>
              <Text as="p" style="caption" color="warning">
                No published interval for this stat yet.
              </Text>
            </div>
          ) : (
            <div key={line.metric}>
              <Text style="statLabel">{line.metric}</Text>
              <ProjectionIntervalBar
                model={{ low: line.low, high: line.high, projection: line.mean, reference: line.seasonAverage }}
                accent="var(--hw-accent-orange)"
                lowText={formatValue(line.low, "decimal1")}
                highText={formatValue(line.high, "decimal1")}
                referenceLabel={line.seasonAverage !== null ? "season avg" : null}
                referenceText={line.seasonAverage !== null ? formatValue(line.seasonAverage, "decimal1") : null}
                projectionText={line.displayValue}
              />
            </div>
          ),
        )}
      </div>
    </div>
  );
}

export default function Player(): JSX.Element {
  const { playerId: playerIdParam } = useParams<{ playerId: string }>();
  const playerId = Number(playerIdParam);
  const { user, updateProfile } = useAuth();
  const [isPinning, setIsPinning] = useState(false);
  const widgets = usePlayerWidgets(playerId);

  if (!Number.isFinite(playerId)) {
    return <ErrorTile message="No player was named." isRetryable={false} />;
  }

  const isPinned = user?.favoritePlayerId === playerId;

  return (
    <DashboardResolveProvider widgets={widgets} options={{ context: { favoritePlayerId: playerId } }}>
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <button
          type="button"
          className={styles.pinButton}
          disabled={isPinning || isPinned}
          onClick={() => {
            setIsPinning(true);
            void updateProfile({ favoritePlayerId: playerId }).finally(() => setIsPinning(false));
          }}
        >
          {isPinned ? "Pinned as my player" : "Pin as my player"}
        </button>
      </div>
      <div className={styles.section}>
        <SnapshotSection playerId={playerId} />
      </div>
      <div className={styles.section}>
        <Text as="p" style="sectionTitle">
          Game log
        </Text>
        <GameLogSection playerId={playerId} />
      </div>
      <div className={styles.section}>
        <Text as="p" style="sectionTitle">
          Career arc
        </Text>
        <CareerArcSection playerId={playerId} />
      </div>
      <div className={styles.section}>
        <Text as="p" style="sectionTitle">
          Shot profile
        </Text>
        <ShotProfileSection playerId={playerId} />
      </div>
      <div className={styles.section}>
        <Text as="p" style="sectionTitle">
          Next game
        </Text>
        <NextGameSection playerId={playerId} />
      </div>
    </DashboardResolveProvider>
  );
}
