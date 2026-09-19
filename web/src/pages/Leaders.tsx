/**
 * `/leaders` — `leaderboard` hosted as a full page, per the routing table.
 *
 * The rows are drawn here rather than by the widget component because the page is a picker
 * with its own two selects and full-width layout. The *value* still goes through `StatValue`,
 * which is what puts the availability marker and the era treatment on an estimated pre-1997
 * number; printing `row.value.displayValue` bare showed it in exactly the same weight as a
 * measured one. `WidgetSection` owns the notes line above the list.
 */
import { useState, type JSX } from "react";
import { Link } from "react-router-dom";
import { DashboardResolveProvider } from "../dashboard/DashboardResolveContext";
import { METRICS_DOCUMENT } from "../generated/contracts";
import { Text } from "../design/Text";
import { PlayerAvatar } from "../design/PlayerAvatar";
import { TeamBadge } from "../design/TeamBadge";
import { RankChip } from "../design/RankChip";
import { StatValue } from "../design/StatValue";
import { EmptyTile } from "../design/StateViews";
import type { ResolveResult, ResolveWidgetRequest, SubjectType } from "../api/types";
import { WidgetSection } from "./WidgetSection";
import styles from "./Leaders.module.css";

const METRIC_OPTIONS = METRICS_DOCUMENT.metrics as ReadonlyArray<{
  readonly key: string;
  readonly name: string;
  readonly scope: readonly string[];
}>;

function LeaderboardRows({ result }: { readonly result: ResolveResult<"leaderboard"> }): JSX.Element {
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
          <StatValue
            value={row.value}
            descriptor={payload.metric}
            style="stat"
            showsLabel={false}
            season={row.season}
            align="trailing"
          />
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
        <WidgetSection widget={widget} errorMessage="Could not load the leaderboard.">
          {(result) => <LeaderboardRows result={result} />}
        </WidgetSection>
      </DashboardResolveProvider>
    </div>
  );
}
