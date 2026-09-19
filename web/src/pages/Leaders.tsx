/** `/leaders` — `leaderboard` hosted as a full page, per the routing table. */
import { useState, type JSX } from "react";
import { Link } from "react-router-dom";
import { DashboardResolveProvider, useWidgetResult } from "../dashboard/DashboardResolveContext";
import { METRICS_DOCUMENT } from "../generated/contracts";
import { Text } from "../design/Text";
import { PlayerAvatar } from "../design/PlayerAvatar";
import { TeamBadge } from "../design/TeamBadge";
import { RankChip } from "../design/RankChip";
import { LoadingTile, ErrorTile, EmptyTile } from "../design/StateViews";
import type { ResolveWidgetRequest, SubjectType } from "../api/types";
import styles from "./Leaders.module.css";

const METRIC_OPTIONS = METRICS_DOCUMENT.metrics as ReadonlyArray<{
  readonly key: string;
  readonly name: string;
  readonly scope: readonly string[];
}>;

function LeaderboardBody({ widget }: { readonly widget: ResolveWidgetRequest & { readonly kind: "leaderboard" } }): JSX.Element {
  const result = useWidgetResult(widget);
  if (!result) return <LoadingTile size="large" />;
  if (result.status === "error") return <ErrorTile message={result.error?.message ?? "Could not load the leaderboard."} />;
  const payload = result.payload;
  if (!payload || payload.rows.length === 0) return <EmptyTile message="No qualifying rows." />;

  return (
    <div>
      {payload.rows.map((row) => (
        <div key={(row.player?.playerId ?? row.team?.teamId ?? row.season) + String(row.rank)} className={styles.row}>
          <RankChip rank={row.rank} />
          {row.player && <PlayerAvatar player={row.player} size="small" />}
          {row.team && <TeamBadge abbreviation={row.team.abbr} name={row.team.name} size="small" />}
          <Link
            to={row.player ? `/player/${row.player.playerId}` : `/team/${row.team?.teamId}`}
            style={{ flex: 1, minWidth: 0, textDecoration: "none", color: "inherit" }}
          >
            <Text style="tableCell" truncate>
              {row.player?.name ?? row.team?.name}
            </Text>
          </Link>
          <Text style="statValue" tabularNums>
            {row.value.displayValue}
          </Text>
        </div>
      ))}
    </div>
  );
}

export default function Leaders(): JSX.Element {
  const [metric, setMetric] = useState("pie");
  const [subjectType, setSubjectType] = useState<SubjectType>("player");

  const widget: ResolveWidgetRequest & { readonly kind: "leaderboard" } = {
    id: "leaders.page",
    kind: "leaderboard",
    size: "large",
    config: {
      subjectType,
      metric,
      scope: "season",
      season: "latest",
      seasonType: "Regular Season",
      perMode: "PerGame",
      limit: 25,
      minGames: 15,
    },
  };

  return (
    <div>
      <Text as="p" style="sectionTitle">
        Leaders
      </Text>
      <div className={styles.controls}>
        <select value={subjectType} onChange={(event) => setSubjectType(event.target.value as SubjectType)}>
          <option value="player">Players</option>
          <option value="team">Teams</option>
        </select>
        <select value={metric} onChange={(event) => setMetric(event.target.value)}>
          {METRIC_OPTIONS.filter((option) => option.scope.includes(subjectType)).map((option) => (
            <option key={option.key} value={option.key}>
              {option.name}
            </option>
          ))}
        </select>
      </div>
      <DashboardResolveProvider widgets={[widget]} options={{}}>
        <LeaderboardBody widget={widget} />
      </DashboardResolveProvider>
    </div>
  );
}
