/**
 * `/team/:teamId` — four factors, team efficiency, roster. `four_factors` and `team_efficiency`
 * are widget kinds, resolved through the normal pipeline; the roster comes from
 * `GET /v1/teams/{teamId}`, which is outside the sixteen widget payloads.
 */
import type { JSX } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { DashboardResolveProvider, useWidgetResult } from "../dashboard/DashboardResolveContext";
import { ApiError, getTeamDetail } from "../api/dashboards";
import { Text } from "../design/Text";
import { TeamBadge } from "../design/TeamBadge";
import { PlayerAvatar } from "../design/PlayerAvatar";
import { PercentileBar } from "../design/PercentileBar";
import { RankChip } from "../design/RankChip";
import { LoadingTile, ErrorTile, EmptyTile } from "../design/StateViews";
import { formatValue } from "../design/format";
import type { FourFactorEntry } from "../api/types";
import styles from "./Team.module.css";

/**
 * `four_factors` carries no `higherIsBetter` (CONTRACT.md §7.7): infer the family by substring
 * and flip it for anything `opp_`-prefixed — turnover rate is better low on offence and better
 * high for the opponent.
 */
function higherIsBetterFor(key: string): boolean {
  const isOpponent = key.startsWith("opp_");
  if (key.includes("tov")) return isOpponent;
  return !isOpponent;
}

function FactorRow({ entry }: { readonly entry: FourFactorEntry }): JSX.Element {
  const better = higherIsBetterFor(entry.key);
  return (
    <div className={styles.factorRow}>
      <div>
        <Text style="tableCell">{entry.label}</Text>
        <Text style="caption" color="tertiary">
          {better ? "higher is better" : "lower is better"}
        </Text>
      </div>
      <PercentileBar percentile={entry.percentile} tint="var(--hw-accent-orange)" />
      <Text style="statValue" tabularNums>
        {entry.displayValue}
      </Text>
    </div>
  );
}

function FourFactorsSection({ teamId }: { readonly teamId: number }): JSX.Element {
  const result = useWidgetResult({
    id: "team.fourFactors",
    kind: "four_factors",
    size: "large",
    config: { teamId, season: "latest", seasonType: "Regular Season", showOpponent: true, comparison: "league" },
  });
  if (!result) return <LoadingTile size="large" />;
  if (result.status === "error") return <ErrorTile message={result.error?.message ?? "Could not load four factors."} />;
  const payload = result.payload;
  if (!payload) return <EmptyTile message="No four-factors data." />;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--hw-space-lg)" }}>
      <div>
        <Text style="widgetTitle">Offense</Text>
        {payload.offense.map((entry) => (
          <FactorRow key={entry.key} entry={entry} />
        ))}
      </div>
      <div>
        <Text style="widgetTitle">Defense</Text>
        {payload.defense.map((entry) => (
          <FactorRow key={entry.key} entry={entry} />
        ))}
      </div>
    </div>
  );
}

function TeamEfficiencySection({ teamId }: { readonly teamId: number }): JSX.Element {
  const result = useWidgetResult({
    id: "team.efficiency",
    kind: "team_efficiency",
    size: "large",
    config: { season: "latest", seasonType: "Regular Season", sortBy: "net_rtg", conference: "all", limit: 30, style: "table" },
  });
  if (!result) return <LoadingTile size="large" />;
  if (result.status === "error") return <ErrorTile message={result.error?.message ?? "Could not load team efficiency."} />;
  const payload = result.payload;
  if (!payload) return <EmptyTile message="No team-efficiency data." />;
  const row = payload.rows.find((candidate) => candidate.team.teamId === teamId);
  if (!row) return <EmptyTile message="This team is not in the current efficiency table." />;

  return (
    <div style={{ display: "flex", gap: "var(--hw-space-lg)", flexWrap: "wrap" }}>
      <div>
        <Text style="caption" color="secondary">
          League rank
        </Text>
        <RankChip rank={row.rank <= 0 ? null : row.rank} />
      </div>
      {Object.entries(row.values).map(([key, value]) => (
        <div key={key}>
          <Text style="caption" color="secondary">
            {key}
          </Text>
          <Text style="statValue" tabularNums>
            {formatValue(value, "rating1")}
          </Text>
        </div>
      ))}
    </div>
  );
}

function RosterSection({ teamId }: { readonly teamId: number }): JSX.Element {
  const { data, isLoading, error } = useQuery({
    queryKey: ["team", teamId, "roster"],
    queryFn: () => getTeamDetail(teamId),
  });
  if (isLoading) return <LoadingTile size="large" />;
  if (error) return <ErrorTile message={error instanceof ApiError ? error.message : "Could not load the roster."} />;
  if (!data || data.roster.length === 0) return <EmptyTile message="No roster on file for this season." />;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--hw-space-xs)" }}>
      {data.roster.map((player) => (
        <Link
          key={player.playerId}
          to={`/player/${player.playerId}`}
          style={{ display: "flex", alignItems: "center", gap: "var(--hw-space-sm)", textDecoration: "none", color: "inherit", padding: "var(--hw-space-xxs) 0" }}
        >
          <PlayerAvatar player={player} size="small" />
          <Text style="tableCell">{player.name}</Text>
          <Text style="caption" color="secondary">
            {player.position ?? "—"}
          </Text>
          <div style={{ flex: 1 }} />
          <Text style="tableCell" tabularNums>
            {formatValue(player.values.pts, "decimal1")} PTS
          </Text>
        </Link>
      ))}
    </div>
  );
}

export default function Team(): JSX.Element {
  const { teamId: teamIdParam } = useParams<{ teamId: string }>();
  const teamId = Number(teamIdParam);
  const { data: detail } = useQuery({
    queryKey: ["team", teamId, "detail"],
    queryFn: () => getTeamDetail(teamId),
    enabled: Number.isFinite(teamId),
  });

  if (!Number.isFinite(teamId)) return <ErrorTile message="No team was named." isRetryable={false} />;

  return (
    <DashboardResolveProvider
      widgets={[
        {
          id: "team.fourFactors",
          kind: "four_factors",
          size: "large",
          config: { teamId, season: "latest", seasonType: "Regular Season", showOpponent: true, comparison: "league" },
        },
        {
          id: "team.efficiency",
          kind: "team_efficiency",
          size: "large",
          config: { season: "latest", seasonType: "Regular Season", sortBy: "net_rtg", conference: "all", limit: 30, style: "table" },
        },
      ]}
      options={{}}
    >
      <div className={styles.header}>
        {detail && <TeamBadge abbreviation={detail.team.abbr} name={detail.team.name} size="large" />}
        <div>
          <Text as="p" style="displayValue">
            {detail?.team.name ?? "Loading…"}
          </Text>
          {detail?.record && (
            <Text style="caption" color="secondary">
              {detail.record.wins ?? "—"}-{detail.record.losses ?? "—"}
            </Text>
          )}
        </div>
      </div>
      <div className={styles.section}>
        <Text as="p" style="sectionTitle">
          Four factors
        </Text>
        <FourFactorsSection teamId={teamId} />
      </div>
      <div className={styles.section}>
        <Text as="p" style="sectionTitle">
          Team efficiency
        </Text>
        <TeamEfficiencySection teamId={teamId} />
      </div>
      <div className={styles.section}>
        <Text as="p" style="sectionTitle">
          Roster
        </Text>
        <RosterSection teamId={teamId} />
      </div>
    </DashboardResolveProvider>
  );
}
