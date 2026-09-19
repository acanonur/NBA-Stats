/**
 * `/fantasy` — **fantasy points**, kept apart per WEB_DESIGN.md §7.8: the NBA formula, ESPN
 * points and Yahoo points are three different, non-interchangeable scoring systems, and the
 * nine-category draft engine is a fourth thing entirely (season z-scores, not any of the three).
 * They are never added together or shown as one number anywhere on this page.
 */
import { useState, type JSX } from "react";
import { useQuery } from "@tanstack/react-query";
import { DashboardResolveProvider, useWidgetResult } from "../dashboard/DashboardResolveContext";
import { ApiError, getFantasyNight, type FantasyScoring } from "../api/dashboards";
import { Text } from "../design/Text";
import { PlayerAvatar } from "../design/PlayerAvatar";
import { RankChip } from "../design/RankChip";
import { PinnedColumnTable, type PinnedColumnDef } from "../design/PinnedColumnTable";
import { LoadingTile, ErrorTile, EmptyTile } from "../design/StateViews";
import { PlayerListField } from "../dashboard/config/PlayerListField";
import type { ConfigFieldSpec } from "../dashboard/config/types";
import { formatValue } from "../design/format";
import type { FantasyDraftBoardRow } from "../api/types";
import styles from "./Fantasy.module.css";

const TABS = ["tonight", "draft", "trade"] as const;
type Tab = (typeof TABS)[number];
const TAB_LABEL: Record<Tab, string> = { tonight: "Tonight", draft: "Draft Board", trade: "Trade" };

const SCORINGS: readonly { readonly key: FantasyScoring; readonly label: string }[] = [
  { key: "nba", label: "NBA formula" },
  { key: "espn_points", label: "ESPN points" },
  { key: "yahoo_points", label: "Yahoo points" },
];

function TonightTab(): JSX.Element {
  const [scoring, setScoring] = useState<FantasyScoring>("nba");
  const { data, isLoading, error } = useQuery({
    queryKey: ["fantasy", "night", scoring],
    queryFn: () => getFantasyNight(scoring),
  });

  return (
    <div>
      <div className={styles.groupTabs}>
        {SCORINGS.map((option) => (
          <button
            key={option.key}
            type="button"
            className={`${styles.tab} ${scoring === option.key ? styles.tabActive : ""}`}
            onClick={() => setScoring(option.key)}
          >
            {option.label}
          </button>
        ))}
      </div>
      {data && (
        <Text style="caption" color="secondary">
          {data.formulaLabel}
        </Text>
      )}
      {isLoading && <LoadingTile size="large" />}
      {error && <ErrorTile message={error instanceof ApiError ? error.message : "Could not load tonight's slate."} />}
      {data && data.rows.length === 0 && <EmptyTile message="No games tonight." />}
      {data?.rows.map((row) => (
        <div key={row.gameId + row.player.playerId} className={styles.row}>
          <RankChip rank={row.rank} />
          <PlayerAvatar player={row.player} size="small" />
          <div style={{ flex: 1, minWidth: 0 }}>
            <Text style="tableCell" truncate>
              {row.player.name}
            </Text>
            <Text style="caption" color="secondary" truncate>
              {row.line} {row.opponentAbbr ? `· ${row.isHome ? "vs" : "@"} ${row.opponentAbbr}` : ""}
            </Text>
          </div>
          <Text style="statValue" tabularNums>
            {row.displayValue}
          </Text>
        </div>
      ))}
    </div>
  );
}

const GROUPS = ["summary", "production", "impact"] as const;
type Group = (typeof GROUPS)[number];
const GROUP_LABEL: Record<Group, string> = { summary: "Summary", production: "Per game", impact: "Value" };

const DRAFT_BOARD_WIDGET = {
  id: "fantasy.draftBoard",
  kind: "fantasy_draft_board",
  size: "large",
  config: {
    season: "latest",
    scoring: "categories",
    puntCategories: [],
    teams: 12,
    rosterSpots: 13,
    poolSize: 150,
    limit: 40,
    seasonType: "Regular Season",
  },
} as const;

function DraftBoardTab(): JSX.Element {
  return (
    <DashboardResolveProvider widgets={[DRAFT_BOARD_WIDGET]} options={{}}>
      <DraftBoardBody />
    </DashboardResolveProvider>
  );
}

function DraftBoardBody(): JSX.Element {
  const [group, setGroup] = useState<Group>("summary");
  const result = useWidgetResult(DRAFT_BOARD_WIDGET);

  if (!result) return <LoadingTile size="large" />;
  if (result.status === "error") return <ErrorTile message={result.error?.message ?? "Could not load the draft board."} />;
  const payload = result.payload;
  if (!payload || payload.rows.length === 0) return <EmptyTile message="No draft board data yet." />;

  const visibleColumns = payload.columns.filter((column) => column.group === group);
  const columnDefs: PinnedColumnDef<FantasyDraftBoardRow>[] = visibleColumns.map((column) => ({
    key: column.key,
    header: (
      <Text style="tableHeader" tabularNums>
        {column.label}
      </Text>
    ),
    align: column.align,
    render: (row) => {
      const value = row.values[column.key];
      if (value === undefined) return null;
      if (typeof value === "string") return <Text style="tableCell">{value}</Text>;
      return (
        <Text
          style="tableCell"
          tabularNums
          color={column.punted ? "tertiary" : undefined}
        >
          {formatValue(value, (column.format as never) ?? "decimal1")}
        </Text>
      );
    },
  }));

  return (
    <div>
      <Text as="p" style="caption" color="secondary">
        Season averages, minimum 10 games.
      </Text>
      <div className={styles.groupTabs}>
        {GROUPS.map((option) => (
          <button
            key={option}
            type="button"
            className={`${styles.tab} ${group === option ? styles.tabActive : ""}`}
            onClick={() => setGroup(option)}
          >
            {GROUP_LABEL[option]}
          </button>
        ))}
      </div>
      <PinnedColumnTable
        rows={payload.rows}
        rowKey={(row) => String(row.player.playerId)}
        pinnedColumn={{
          key: "player",
          header: <Text style="tableHeader">Player</Text>,
          render: (row) => (
            <div style={{ display: "flex", alignItems: "center", gap: "var(--hw-space-xs)" }}>
              <RankChip rank={row.rank} />
              <PlayerAvatar player={row.player} size="small" />
              <Text style="tableCell" truncate>
                {row.player.name}
              </Text>
            </div>
          ),
        }}
        columns={columnDefs}
        pinnedWidth={180}
      />
    </div>
  );
}

const PLAYER_LIST_SPEC = (label: string): ConfigFieldSpec => ({
  key: "players",
  type: "playerList",
  label,
  required: false,
  default: [],
  maxItems: 6,
});

function tradeWidget(givePlayerIds: readonly number[], getPlayerIds: readonly number[]) {
  return {
    id: "fantasy.trade",
    kind: "fantasy_trade",
    size: "large",
    config: {
      season: "latest",
      givePlayerIds,
      getPlayerIds,
      puntCategories: [],
      poolSize: 150,
      fairBand: 0.75,
      clearBand: 2.0,
      showSensitivity: true,
      seasonType: "Regular Season",
    },
  } as const;
}

function TradeTab(): JSX.Element {
  const [givePlayerIds, setGivePlayerIds] = useState<readonly number[]>([]);
  const [getPlayerIds, setGetPlayerIds] = useState<readonly number[]>([]);
  const hasBothSides = givePlayerIds.length > 0 && getPlayerIds.length > 0;
  const widget = tradeWidget(givePlayerIds, getPlayerIds);
  const result = useWidgetResult(widget);

  return (
    <DashboardResolveProvider widgets={hasBothSides ? [widget] : []} options={{}}>
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--hw-space-lg)" }}>
        <PlayerListField
          spec={PLAYER_LIST_SPEC("You give")}
          value={givePlayerIds}
          onChange={(value) => setGivePlayerIds(value ?? [])}
          config={{}}
        />
        <PlayerListField
          spec={PLAYER_LIST_SPEC("You get")}
          value={getPlayerIds}
          onChange={(value) => setGetPlayerIds(value ?? [])}
          config={{}}
        />
      </div>
      {givePlayerIds.length === 0 || getPlayerIds.length === 0 ? (
        <EmptyTile message="Pick at least one player on each side to analyse a trade." />
      ) : !result ? (
        <LoadingTile size="large" />
      ) : result.status === "error" ? (
        <ErrorTile message={result.error?.message ?? "Could not evaluate this trade."} />
      ) : !result.payload ? (
        <EmptyTile message="No trade to evaluate yet." />
      ) : (
        <div>
          <Text as="p" style="displayValue">
            {result.payload.verdict}
          </Text>
          <Text as="p" style="tableCell">
            Net z-score change: {formatValue(result.payload.netZ, "decimal2")} (roster-slot
            adjustment {formatValue(result.payload.rosterAdjustment, "decimal2")} kept separate)
          </Text>
          <Text as="p" style="caption" color="secondary">
            A sweep over four named assumptions, not a probability. Pool spread:{" "}
            {formatValue(result.payload.poolSpread, "decimal2")}
          </Text>
          {Object.entries(result.payload.points).map(([system, delta]) => (
            <Text as="p" key={system} style="tableCell">
              {system}: {delta.verdict}
            </Text>
          ))}
        </div>
      )}
    </div>
    </DashboardResolveProvider>
  );
}

export default function Fantasy(): JSX.Element {
  const [tab, setTab] = useState<Tab>("tonight");

  return (
    <div>
      <Text as="p" style="sectionTitle">
        Fantasy
      </Text>
      <div className={styles.banner}>
        <Text as="p" style="tableCell">
          Hardwood does not run a fantasy league, keep scores, or publish odds. These are your
          own roster's numbers. Tonight's points, the nine-category draft board, and a trade
          verdict are three unrelated scoring systems — never added together, never shown as one
          number.
        </Text>
      </div>
      <div className={styles.tabs} role="tablist">
        {TABS.map((option) => (
          <button
            key={option}
            type="button"
            role="tab"
            aria-selected={tab === option}
            className={`${styles.tab} ${tab === option ? styles.tabActive : ""}`}
            onClick={() => setTab(option)}
          >
            {TAB_LABEL[option]}
          </button>
        ))}
      </div>
      {tab === "tonight" && <TonightTab />}
      {tab === "draft" && <DraftBoardTab />}
      {tab === "trade" && <TradeTab />}
    </div>
  );
}
