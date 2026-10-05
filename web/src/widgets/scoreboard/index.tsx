/**
 * `scoreboard` — a whole slate, with the best line from each side.
 *
 * The front page of the product (WP5's own brief): last night's games, one row per matchup, with
 * both teams, the score, where the game is in its life, and — when the tile is large enough —
 * who carried it. Ported from `ios/NBAStats/Widgets/ScoreboardWidget.swift`; see that file's own
 * docstring for the two decisions that matter most and are reproduced here unchanged:
 *
 *   - a game that has not tipped shows an em dash rather than `0-0` (`design/format.ts`'s
 *     `formatInteger`, which every score in this widget goes through);
 *   - the winner is marked by weight, colour, AND a leading caret reserved on both lines, so the
 *     result survives a grayscale screenshot.
 */
import type { JSX } from "react";
import clsx from "clsx";
import type { WidgetViewProps } from "../../generated/registry";
import type { GameStatus, PlayerRef, ScoreboardGame, ScoreboardPayload, ScoreboardTopPerformer, TeamRef } from "../../api/types";
import { Text } from "../../design/Text";
import { AvailabilityBadge } from "../../design/AvailabilityBadge";
import { PlayerAvatar } from "../../design/PlayerAvatar";
import { TeamBadge } from "../../design/TeamBadge";
import { EM_DASH, formatInteger, mediumGameDate } from "../../design/format";
import { decodeScoreboardPayload } from "./decode";
import { ErrorTile } from "../../design/StateViews";
import styles from "./index.module.css";

const GAME_LIMIT: Readonly<Record<WidgetViewProps["size"], number>> = { small: 2, medium: 4, large: 8 };

/** `"L. James"` when the name parts are known, otherwise the full name — matches
 * `PlayerRef.shortName` (`ios/NBAStats/Core/Models.swift`); the web contract carries no such
 * computed property (`api/types.ts` is a plain data mirror), so this widget derives it itself. */
function shortPlayerName(player: PlayerRef): string {
  const initial = player.firstName?.trim().charAt(0);
  if (!initial || !player.lastName) return player.name;
  return `${initial}. ${player.lastName}`;
}

function winnerTeamId(game: ScoreboardGame): number | null {
  if (game.status !== "final") return null;
  if (game.homePts === null || game.awayPts === null) return null;
  if (game.homePts === game.awayPts) return null;
  return game.homePts > game.awayPts ? game.home.teamId : game.away.teamId;
}

function subheadText(payload: ScoreboardPayload, visibleCount: number): string {
  const parts: string[] = [];
  if (payload.allFinal) {
    parts.push("All final");
  } else if (payload.games.some((game) => game.status === "live")) {
    parts.push("In progress");
  } else if (payload.isLatestCompleted) {
    parts.push("Latest completed slate");
  }
  parts.push(payload.games.length === 1 ? "1 game" : `${payload.games.length} games`);
  if (payload.games.length - visibleCount > 0) parts.push(`showing ${visibleCount}`);
  return parts.join(" · ");
}

function liveText(game: ScoreboardGame): string {
  const parts: string[] = [];
  if (game.period !== null && game.period > 0) {
    parts.push(game.period <= 4 ? `Q${game.period}` : `OT${game.period - 4}`);
  } else {
    parts.push("Live");
  }
  if (game.clock) parts.push(game.clock);
  return parts.join(" ");
}

function statusWord(status: GameStatus, game: ScoreboardGame): string {
  switch (status) {
    case "final":
      return "final";
    case "live":
      return `in progress, ${liveText(game)}`;
    case "scheduled":
      return "not yet played";
  }
}

function gameAccessibilityLabel(game: ScoreboardGame, winner: number | null, showsPerformers: boolean): string {
  const parts: string[] = [];
  parts.push(game.awayPts !== null ? `${game.away.name} ${game.awayPts}` : `${game.away.name}, no score yet`);
  parts.push(game.homePts !== null ? `${game.home.name} ${game.homePts}` : `${game.home.name}, no score yet`);
  parts.push(statusWord(game.status, game));
  if (winner !== null) {
    const winnerName = winner === game.home.teamId ? game.home.name : game.away.name;
    parts.push(`${winnerName} won`);
  }
  if (showsPerformers) {
    for (const performer of game.topPerformers.slice(0, 2)) {
      let text = performer.player.name;
      if (performer.line) text += `, ${performer.line}`;
      if (performer.value.metric) text += `, ${performer.value.metric} ${performer.value.displayValue}`;
      parts.push(text);
    }
  }
  return parts.join(", ");
}

function TeamLine({
  team,
  points,
  isWinner,
  showsName,
}: {
  readonly team: TeamRef;
  readonly points: number | null;
  readonly isWinner: boolean;
  readonly showsName: boolean;
}): JSX.Element {
  return (
    <div className={styles.teamLine}>
      <Text as="span" style="tableHeader" className={clsx(styles.winnerMark, isWinner && styles.winnerMarkActive)} ariaHidden>
        &#9656;
      </Text>
      <TeamBadge abbreviation={team.abbr} name={team.name} size="small" />
      {showsName && (
        <Text
          as="span"
          style="tableCell"
          color={isWinner ? "primary" : "secondary"}
          truncate
          className={clsx(styles.teamName, isWinner && styles.winner)}
        >
          {team.nickname ?? team.name}
        </Text>
      )}
      <Text
        as="span"
        style="statValue"
        color={points === null ? "tertiary" : "primary"}
        className={clsx(styles.points, isWinner && styles.winner)}
      >
        {formatInteger(points)}
      </Text>
    </div>
  );
}

function StatusLabel({ game }: { readonly game: ScoreboardGame }): JSX.Element {
  switch (game.status) {
    case "final":
      return (
        <Text style="tableHeader" color="secondary">
          Final
        </Text>
      );
    case "live":
      return (
        <span className={styles.liveStatus}>
          <span className={styles.liveDot} aria-hidden />
          <Text style="tableHeader" color="negative" tabularNums>
            {liveText(game)}
          </Text>
        </span>
      );
    case "scheduled":
      return (
        <Text style="tableHeader" color="tertiary">
          Scheduled
        </Text>
      );
  }
}

function Performers({ performers }: { readonly performers: readonly ScoreboardTopPerformer[] }): JSX.Element {
  return (
    <div className={styles.performers} aria-hidden>
      {performers.slice(0, 2).map((performer) => (
        <div className={styles.performerRow} key={performer.player.playerId}>
          <PlayerAvatar player={performer.player} size="small" />
          <Text style="caption" color="secondary" truncate>
            {shortPlayerName(performer.player)}
          </Text>
          {performer.teamAbbr && <Text style="caption">{performer.teamAbbr}</Text>}
          <Text style="caption" color="primary" tabularNums truncate className={styles.performerLine}>
            {performer.line || EM_DASH}
          </Text>
          {/* The decoder has always read `performer.value.availability`; nothing rendered it, so a
              top-performer line from an era that only estimates one of its components read as a
              recorded box score. `full` — every performer in a modern slate — renders zero DOM
              nodes, so this costs the common case nothing. Non-interactive: this row is inside an
              `aria-hidden` block whose game already carries the accessible label. */}
          <AvailabilityBadge
            availability={performer.value.availability}
            showsText={false}
            isInteractive={false}
            metricName={performer.value.metric}
          />
        </div>
      ))}
    </div>
  );
}

export default function ScoreboardWidget({ size, payload }: WidgetViewProps): JSX.Element {
  // Decoded inside a try/catch, like the other widgets: these decoders throw on any
  // shape deviation, and a bare call here meant one off-contract field took the whole
  // page down. `WidgetContainer`'s `TileErrorBoundary` is the backstop; this is the
  // message worth showing.
  let data: ScoreboardPayload;
  try {
    data = decodeScoreboardPayload(payload);
  } catch {
    return (
      <ErrorTile
        size={size}
        isRetryable={false}
        message="The data behind this scoreboard did not match what the app expected."
      />
    );
  }
  const limit = GAME_LIMIT[size];
  const games = data.games.slice(0, Math.max(limit, 0));
  const showsPerformers = size === "large";
  const showsTeamNames = size !== "small";

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        <Text as="div" style="widgetTitle">
          {mediumGameDate(data.date)}
        </Text>
        <Text as="div" style="caption" color="secondary">
          {subheadText(data, games.length)}
        </Text>
      </div>
      {games.length === 0 ? (
        <Text as="p" style="tableCell" color="secondary">
          No games on this date.
        </Text>
      ) : (
        <div className={styles.rows}>
          {games.map((game) => {
            const winner = winnerTeamId(game);
            return (
              <div className={styles.row} key={game.gameId} aria-label={gameAccessibilityLabel(game, winner, showsPerformers)}>
                <div className={styles.matchup}>
                  <div className={styles.teamLines}>
                    <TeamLine team={game.away} points={game.awayPts} isWinner={winner === game.away.teamId} showsName={showsTeamNames} />
                    <TeamLine team={game.home} points={game.homePts} isWinner={winner === game.home.teamId} showsName={showsTeamNames} />
                  </div>
                  <div className={styles.status}>
                    <StatusLabel game={game} />
                  </div>
                </div>
                {showsPerformers && game.topPerformers.length > 0 && <Performers performers={game.topPerformers} />}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
