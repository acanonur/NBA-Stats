/**
 * `/tonight` — **daily match results**. Hosts the `scoreboard` widget kind (so it goes through
 * the same `POST /v1/dashboard/resolve` pipeline as a dashboard tile, never a bespoke `/games`
 * call) and renders its payload directly rather than through `registry.ts`, since this is a full
 * page with its own date stepper and per-game links to `/tonight/:gameId` — the same "host a
 * widget kind as a full page with its own chrome" pattern `/leaders` uses.
 */
import { useEffect, useState, type JSX } from "react";
import { Link } from "react-router-dom";
import { DashboardResolveProvider, useWidgetResult } from "../dashboard/DashboardResolveContext";
import { Text } from "../design/Text";
import { TeamBadge } from "../design/TeamBadge";
import { PlayerAvatar } from "../design/PlayerAvatar";
import { LoadingTile, ErrorTile, EmptyTile } from "../design/StateViews";
import { formatValue, mediumGameDate } from "../design/format";
import { useAuth } from "../auth/AuthProvider";
import type { ResolveWidgetRequest } from "../api/types";
import styles from "./Tonight.module.css";

const WIDGET_ID = "tonight.scoreboard";

function shiftDate(iso: string, days: number): string {
  const date = new Date(`${iso}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function ScoreboardBody({
  date,
  onResolvedDate,
}: {
  readonly date: string;
  readonly onResolvedDate: (resolved: string) => void;
}): JSX.Element {
  const widget: ResolveWidgetRequest & { readonly kind: "scoreboard" } = {
    id: WIDGET_ID,
    kind: "scoreboard",
    size: "large",
    config: { date, showTopPerformers: true, teamIds: [] },
  };
  const result = useWidgetResult(widget);

  useEffect(() => {
    if (result?.status === "ok" && result.payload) onResolvedDate(result.payload.date);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when this widget's own payload changes
  }, [result]);

  if (!result) return <LoadingTile size="large" />;
  if (result.status === "error") {
    return <ErrorTile message={result.error?.message ?? "Could not load the scoreboard."} size="large" />;
  }
  const payload = result.payload;
  if (!payload || payload.games.length === 0) {
    return <EmptyTile message="No games on this date." size="large" />;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--hw-space-md)" }}>
      {payload.games.map((game) => {
        const homeWon = game.homePts !== null && game.awayPts !== null && game.homePts > game.awayPts;
        const awayWon = game.homePts !== null && game.awayPts !== null && game.awayPts > game.homePts;
        return (
          <Link
            key={game.gameId}
            to={`/tonight/${encodeURIComponent(game.gameId)}`}
            style={{ textDecoration: "none", color: "inherit" }}
          >
            <div
              style={{
                border: "1px solid var(--hw-separator)",
                borderRadius: "var(--hw-radius-card)",
                padding: "var(--hw-space-md)",
                display: "flex",
                flexDirection: "column",
                gap: "var(--hw-space-xs)",
              }}
            >
              {([
                [game.away, game.awayPts, awayWon],
                [game.home, game.homePts, homeWon],
              ] as const).map(([team, pts, won], index) => (
                <div key={index} style={{ display: "flex", alignItems: "center", gap: "var(--hw-space-sm)" }}>
                  <Text style="tableCell" color={won ? "primary" : "secondary"}>
                    {won ? "▸" : " "}
                  </Text>
                  <TeamBadge abbreviation={team.abbr} name={team.name} size="small" />
                  <Text style="tableCell" color={won ? "primary" : "secondary"}>
                    {team.abbr}
                  </Text>
                  <div style={{ flex: 1 }} />
                  <Text style="statValue" tabularNums color={won ? "primary" : "secondary"}>
                    {formatValue(pts, "integer")}
                  </Text>
                </div>
              ))}
              <Text style="caption" color="tertiary">
                {game.status === "final" ? "Final" : game.status}
              </Text>
              {game.topPerformers.length > 0 && (
                <div style={{ display: "flex", gap: "var(--hw-space-md)", flexWrap: "wrap" }}>
                  {game.topPerformers.map((performer) => (
                    <div key={performer.player.playerId} style={{ display: "flex", alignItems: "center", gap: "var(--hw-space-xxs)" }}>
                      <PlayerAvatar player={performer.player} size="small" />
                      <Text style="caption">{performer.line}</Text>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </Link>
        );
      })}
    </div>
  );
}

export default function Tonight(): JSX.Element {
  const [explicitDate, setExplicitDate] = useState<string | null>(null);
  const [resolvedDate, setResolvedDate] = useState<string | null>(null);
  const { user } = useAuth();
  const date = explicitDate ?? "latest";

  return (
    <div>
      <div className={styles.header}>
        <Text as="p" style="sectionTitle">
          Last night
        </Text>
        <button
          type="button"
          className={styles.button}
          onClick={() =>
            setExplicitDate(shiftDate(explicitDate ?? resolvedDate ?? new Date().toISOString().slice(0, 10), -1))
          }
        >
          ← Earlier
        </button>
        {explicitDate && (
          <button type="button" className={styles.button} onClick={() => setExplicitDate(shiftDate(explicitDate, 1))}>
            Later →
          </button>
        )}
        {explicitDate && (
          <Text style="caption" color="secondary">
            {mediumGameDate(explicitDate)}
          </Text>
        )}
      </div>
      <DashboardResolveProvider
        widgets={[{ id: WIDGET_ID, kind: "scoreboard", size: "large", config: { date } }]}
        options={{ context: { favoritePlayerId: user?.favoritePlayerId, favoriteTeamId: user?.favoriteTeamId } }}
      >
        <ScoreboardBody date={date} onResolvedDate={setResolvedDate} />
      </DashboardResolveProvider>
    </div>
  );
}
