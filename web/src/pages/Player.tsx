/**
 * `/player/:playerId` — **highlighted player stats**: snapshot, game log, career arc, shot
 * profile, next-game projection, and "Pin as my player". Every section hosts one of the sixteen
 * widget kinds through the same resolve pipeline a dashboard tile uses.
 *
 * Each section renders the widget's **own** component through `WidgetSection`, rather than a
 * thinner hand-rolled version of it. The hand-rolled versions dropped every honesty marker the
 * widgets carry: the career arc printed `season.displayValue` alone (no estimated marker, no
 * estimated-season count), the snapshot printed `metric.displayValue` and ignored both the
 * value's `availability` and the payload's `eraNote`, and "Next game" re-implemented the
 * projection view without the thin-rate caution, the minutes block, the intervals-missing line
 * or the estimated badge — the five rules `next_game_projection/index.tsx`'s docstring calls
 * "the whole design of this view". None of them rendered `result.notes`, which is the sentence
 * the server sends when a career crosses 1996-97 and the whole caveat when `status` is
 * `"partial"`. Same data, same session, two different honesty stories depending on whether the
 * widget was on a tile or on the page the nav links to.
 */
import { useState, type JSX } from "react";
import { useParams } from "react-router-dom";
import { DashboardResolveProvider } from "../dashboard/DashboardResolveContext";
import { useAuth } from "../auth/AuthProvider";
import { Text } from "../design/Text";
import { ErrorTile } from "../design/StateViews";
import type { ResolveWidgetRequest } from "../api/types";
import { WidgetSection } from "./WidgetSection";
import styles from "./Player.module.css";

const SNAPSHOT_METRICS = ["ts_pct", "usg_pct", "ast_pct", "reb_pct", "off_rtg", "def_rtg", "net_rtg", "pie"];
const GAME_LOG_COLUMNS = ["min", "pts", "reb", "ast", "ts_pct", "usg_pct", "plus_minus", "game_score"];
const PROJECTION_STATS = ["pts", "reb", "ast", "fg3m", "stl", "blk", "tov"];

/** One definition per section, used both to request the resolve and to read it back — the two
 * must agree exactly, because `widgetQueryKey` is derived from the id and the config. */
function playerWidgets(playerId: number): {
  readonly snapshot: ResolveWidgetRequest & { readonly kind: "player_snapshot" };
  readonly gameLog: ResolveWidgetRequest & { readonly kind: "game_log" };
  readonly careerArc: ResolveWidgetRequest & { readonly kind: "career_arc" };
  readonly shotProfile: ResolveWidgetRequest & { readonly kind: "shot_profile" };
  readonly nextGame: ResolveWidgetRequest & { readonly kind: "next_game_projection" };
} {
  return {
    snapshot: {
      id: "player.snapshot",
      kind: "player_snapshot",
      size: "large",
      config: {
        playerId,
        season: "latest",
        seasonType: "Regular Season",
        metrics: SNAPSHOT_METRICS,
        showPercentiles: true,
      },
    },
    gameLog: {
      id: "player.gameLog",
      kind: "game_log",
      size: "large",
      config: {
        playerId,
        season: "latest",
        seasonType: "Regular Season",
        columns: GAME_LOG_COLUMNS,
        limit: 10,
      },
    },
    careerArc: {
      id: "player.careerArc",
      kind: "career_arc",
      size: "large",
      config: {
        playerId,
        metric: "per",
        seasonType: "Regular Season",
        includePlayoffs: true,
        xAxis: "season",
      },
    },
    shotProfile: {
      id: "player.shotProfile",
      kind: "shot_profile",
      size: "large",
      config: {
        subjectType: "player",
        subjectId: playerId,
        season: "latest",
        seasonType: "Regular Season",
        compareToLeague: true,
      },
    },
    nextGame: {
      id: "player.nextGame",
      kind: "next_game_projection",
      size: "large",
      config: {
        playerId,
        stats: PROJECTION_STATS,
        season: "latest",
        seasonType: "Regular Season",
        showCombo: true,
        showFactors: true,
      },
    },
  };
}

export default function Player(): JSX.Element {
  const { playerId: playerIdParam } = useParams<{ playerId: string }>();
  const playerId = Number(playerIdParam);
  const { user, updateProfile } = useAuth();
  const [isPinning, setIsPinning] = useState(false);
  const [pinError, setPinError] = useState<string | null>(null);

  if (!Number.isFinite(playerId)) {
    return <ErrorTile message="No player was named." isRetryable={false} />;
  }

  const widgets = playerWidgets(playerId);
  const requested = [
    widgets.snapshot,
    widgets.gameLog,
    widgets.careerArc,
    widgets.shotProfile,
    widgets.nextGame,
  ];
  const isPinned = user?.favoritePlayerId === playerId;

  return (
    <DashboardResolveProvider widgets={requested} options={{ context: { favoritePlayerId: playerId } }}>
      <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: "var(--hw-space-sm)" }}>
        {pinError && (
          <span role="alert">
            <Text style="caption" color="negative">
              {pinError}
            </Text>
          </span>
        )}
        <button
          type="button"
          className={styles.pinButton}
          disabled={isPinning || isPinned}
          onClick={() => {
            setIsPinning(true);
            setPinError(null);
            void updateProfile({ favoritePlayerId: playerId })
              // `.finally` does not handle a rejection: a 400, a stale CSRF token after a
              // session rotation, or a dropped connection stopped the spinner, pinned nothing
              // and said nothing.
              .catch(() => setPinError("Could not pin this player. Try again."))
              .finally(() => setIsPinning(false));
          }}
        >
          {isPinned ? "Pinned as my player" : "Pin as my player"}
        </button>
      </div>
      <div className={styles.section}>
        <WidgetSection widget={widgets.snapshot} errorMessage="Could not load this player." />
      </div>
      <div className={styles.section}>
        <WidgetSection widget={widgets.gameLog} title="Game log" errorMessage="Could not load the game log." />
      </div>
      <div className={styles.section}>
        <WidgetSection widget={widgets.careerArc} title="Career arc" errorMessage="Could not load the career arc." />
      </div>
      <div className={styles.section}>
        <WidgetSection widget={widgets.shotProfile} title="Shot profile" errorMessage="Could not load the shot profile." />
      </div>
      <div className={styles.section}>
        <WidgetSection widget={widgets.nextGame} title="Next game" errorMessage="Could not load the projection." />
      </div>
    </DashboardResolveProvider>
  );
}
