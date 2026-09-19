/**
 * `daily_movers` — last night's biggest games, measured against each player's own baseline.
 *
 * Ported from `ios/NBAStats/Widgets/DailyMoversWidget.swift`. The one decision that matters most,
 * carried over unchanged: a raw Game Score means little on its own, so the delta against the
 * player's own season average is the dominant, boldest number in the row, and the absolute value
 * sits beside it as supporting detail — never the other way around (WEB_DESIGN.md §7.7).
 */
import type { JSX } from "react";
import type { WidgetViewProps } from "../../generated/registry";
import type { DailyMoverRow, DailyMoversPayload } from "../../api/types";
import type { MetricFormat } from "../../generated/contracts";
import { Text } from "../../design/Text";
import { PlayerAvatar } from "../../design/PlayerAvatar";
import { TeamBadge } from "../../design/TeamBadge";
import { StatValue } from "../../design/StatValue";
import { DeltaChip } from "../../design/DeltaChip";
import { EM_DASH, formatValue, mediumGameDate } from "../../design/format";
import { decodeDailyMoversPayload } from "./decode";
import { ErrorTile } from "../../design/StateViews";
import styles from "./index.module.css";

const ROW_LIMIT: Readonly<Record<WidgetViewProps["size"], number>> = { small: 2, medium: 3, large: 5 };

function directionLabel(direction: DailyMoversPayload["direction"]): string {
  switch (direction) {
    case "worst":
      return "Toughest nights";
    case "surprise":
      return "Biggest surprises";
    case "best":
      return "Biggest nights";
  }
}

/** `"vs BOS"` / `"@ BOS"` — the same formula as `GameLogRow.matchupText`
 * (`ios/NBAStats/Core/Payloads.swift`); the web contract carries no computed property for it. */
function matchupText(row: DailyMoverRow): string {
  if (!row.opponentAbbr) return EM_DASH;
  return (row.isHome ?? true) ? `vs ${row.opponentAbbr}` : `@ ${row.opponentAbbr}`;
}

function subtitle(row: DailyMoverRow): string {
  const parts = [matchupText(row)];
  if (row.result) parts.push(row.result.slice(0, 1).toUpperCase());
  if (row.line) parts.push(row.line);
  return parts.join(" · ");
}

/** The server already computes `row.delta` against the player's own season average
 * (`backend/nbastats/widgets/daily_movers.py`); this defensive recomputation only matters if a
 * future caller ever constructs a row without going through that resolver — matches
 * `DailyMoversWidget.resolvedDelta`. */
function effectiveDelta(row: DailyMoverRow): number | null {
  if (row.delta !== null && Number.isFinite(row.delta)) return row.delta;
  if (row.value.value !== null && row.seasonAverage !== null && Number.isFinite(row.value.value) && Number.isFinite(row.seasonAverage)) {
    return row.value.value - row.seasonAverage;
  }
  return null;
}

function rowAccessibilityLabel(row: DailyMoverRow, payload: DailyMoversPayload): string {
  const parts: string[] = [];
  if (row.rank > 0) parts.push(`number ${row.rank}`);
  parts.push(row.player.name);
  parts.push(matchupText(row));
  if (row.line) parts.push(row.line);
  const valueText = row.value.availability === "unavailable" ? "not available" : row.value.displayValue || EM_DASH;
  parts.push(`${payload.metric.name} ${valueText}`);
  const delta = effectiveDelta(row);
  if (delta !== null) {
    const direction = delta > 0 ? "above" : "below";
    const magnitude = formatValue(Math.abs(delta), payload.metric.format as MetricFormat);
    if (row.seasonAverage !== null) {
      parts.push(`${magnitude} ${direction} their season average of ${formatValue(row.seasonAverage, payload.metric.format as MetricFormat)}`);
    } else {
      parts.push(`${magnitude} ${direction} their season average`);
    }
  }
  return parts.join(", ");
}

function MoverRow({ row, payload, showsSeasonAverage }: { readonly row: DailyMoverRow; readonly payload: DailyMoversPayload; readonly showsSeasonAverage: boolean }): JSX.Element {
  const format = payload.metric.format as MetricFormat;
  return (
    <div className={styles.row} aria-label={rowAccessibilityLabel(row, payload)}>
      <Text as="span" style="tableCell" color="tertiary" tabularNums className={styles.rank}>
        {row.rank > 0 ? String(row.rank) : EM_DASH}
      </Text>
      <div className={styles.identity}>
        <PlayerAvatar player={row.player} size="medium" />
        <div className={styles.identityText}>
          <div className={styles.nameRow}>
            {row.player.teamAbbr && <TeamBadge abbreviation={row.player.teamAbbr} size="small" />}
            <Text style="widgetTitle" truncate>
              {row.player.name}
            </Text>
          </div>
          <Text style="caption" tabularNums truncate>
            {subtitle(row)}
          </Text>
        </div>
      </div>
      <div className={styles.valueBlock}>
        <StatValue value={row.value} descriptor={payload.metric} style="stat" showsLabel showsRank={false} showsDelta={false} align="trailing" />
        {showsSeasonAverage && (
          <Text style="caption" tabularNums color="secondary">
            avg {formatValue(row.seasonAverage, format)}
          </Text>
        )}
      </div>
      <div className={styles.deltaBlock}>
        <DeltaChip delta={effectiveDelta(row)} higherIsBetter={payload.metric.higherIsBetter} format={format} caption="vs avg" />
      </div>
    </div>
  );
}

export default function DailyMoversWidget({ size, payload }: WidgetViewProps): JSX.Element {
  // Decoded inside a try/catch, like the other widgets: these decoders throw on any
  // shape deviation, and a bare call here meant one off-contract field took the whole
  // page down. `WidgetContainer`'s `TileErrorBoundary` is the backstop; this is the
  // message worth showing.
  let data: DailyMoversPayload;
  try {
    data = decodeDailyMoversPayload(payload);
  } catch {
    return (
      <ErrorTile
        size={size}
        isRetryable={false}
        message="The data behind these daily movers did not match what the app expected."
      />
    );
  }
  const limit = ROW_LIMIT[size];
  const rows = data.rows.slice(0, Math.max(limit, 0));
  const showsSeasonAverage = size === "large";
  const contextLine = [mediumGameDate(data.date), data.metric.name].filter((part) => part.length > 0).join(" · ");

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        <Text as="div" style="widgetTitle">
          {directionLabel(data.direction)}
        </Text>
        <Text as="div" style="caption" color="secondary">
          {contextLine}
        </Text>
      </div>
      {rows.length === 0 ? (
        <Text as="p" style="tableCell" color="secondary">
          Nobody played on this date.
        </Text>
      ) : (
        <div className={styles.rows}>
          {rows.map((row) => (
            <MoverRow key={`${row.gameId}-${row.player.playerId}`} row={row} payload={data} showsSeasonAverage={showsSeasonAverage} />
          ))}
        </div>
      )}
    </div>
  );
}
