/**
 * `player_snapshot` — one player's season at a glance, with each rate stat measured against the
 * league. Ported from `ios/NBAStats/Widgets/PlayerSnapshotWidget.swift`: the percentile bar is
 * the point of the widget, so a metric with no percentile still shows its value and simply
 * leaves the bar empty rather than inventing a position for it, and a metric key the bundled
 * catalog does not know still gets a readable fallback label (`"off_rtg"` → `"OFF RTG"`).
 */
import type { JSX } from "react";
import type { WidgetViewProps } from "../../generated/registry";
import type { WidgetSizeKey } from "../../generated/tokens";
import { METRICS_DOCUMENT } from "../../generated/contracts";
import type { MetricDescriptor, MetricValue, PlayerSnapshotPayload } from "../../api/types";
import type { PlayerAvatarSize } from "../../design/PlayerAvatar";
import { Text } from "../../design/Text";
import { PlayerAvatar } from "../../design/PlayerAvatar";
import { AvailabilityBadge } from "../../design/AvailabilityBadge";
import { PercentileBar } from "../../design/PercentileBar";
import { ErrorTile } from "../../design/StateViews";
import { comparisonColorVar, comparisonOutcomeAgainst } from "../../design/valueColor";
import { shortLabel } from "../../design/availability";
import { EM_DASH, formatDecimal, formatInteger, formatOrdinal, formatPercentile, seasonContext } from "../../design/format";
import { decodePlayerSnapshotPayload } from "./payload";
import styles from "./player_snapshot.module.css";

const METRIC_DESCRIPTORS = new Map<string, MetricDescriptor>(
  METRICS_DOCUMENT.metrics.map((entry) => [entry.key, entry as unknown as MetricDescriptor]),
);

function findMetricDescriptor(key: string): MetricDescriptor | null {
  return METRIC_DESCRIPTORS.get(key) ?? null;
}

/** A readable label for a metric key the bundled catalog does not know: `"ts_pct"` becomes
 * `"TS%"`, `"off_rtg"` becomes `"OFF RTG"` — `Entry.fallbackShortName` in the Swift original. */
function fallbackShortName(key: string): string {
  const parts = key.split("_");
  let words = parts;
  let suffix = "";
  if (parts[parts.length - 1] === "pct") {
    words = parts.slice(0, -1);
    suffix = "%";
  }
  const body = words.map((word) => word.toUpperCase()).join(" ");
  return (body.length > 0 ? body : key.toUpperCase()) + suffix;
}

const METRIC_LIMIT: Readonly<Record<WidgetSizeKey, number | null>> = { small: 3, medium: 6, large: null };

interface Entry {
  readonly value: MetricValue;
  readonly descriptor: MetricDescriptor | null;
}

function buildEntries(payload: PlayerSnapshotPayload, size: WidgetSizeKey): readonly Entry[] {
  const limit = METRIC_LIMIT[size];
  const bounded = limit === null ? payload.metrics : payload.metrics.slice(0, Math.max(limit, 0));
  return bounded.map((value) => ({ value, descriptor: findMetricDescriptor(value.metric) }));
}

function entryLabel(entry: Entry): string {
  return entry.descriptor?.shortName ?? fallbackShortName(entry.value.metric);
}

function entryName(entry: Entry): string {
  return entry.descriptor?.name ?? entry.value.metric;
}

function avatarSize(size: WidgetSizeKey): PlayerAvatarSize {
  return size === "small" ? "small" : "medium";
}

function subtitle(payload: PlayerSnapshotPayload): string {
  const parts: string[] = [];
  const context = seasonContext(payload.season, payload.seasonType);
  if (context) parts.push(context);
  if (payload.teamAbbr && payload.teamAbbr.length > 0 && payload.teamAbbr !== payload.player.teamAbbr) {
    parts.push(payload.teamAbbr);
  }
  return parts.join(" · ");
}

function usageText(payload: PlayerSnapshotPayload): string {
  return [
    `${formatInteger(payload.gp)} GP`,
    `${formatInteger(payload.gs)} GS`,
    `${formatDecimal(payload.minutesPerGame, 1)} MPG`,
  ].join("  ·  ");
}

function usageAccessibleText(payload: PlayerSnapshotPayload): string {
  const games = payload.gp !== null ? `${payload.gp} games played` : "games played not recorded";
  const starts = payload.gs !== null ? `${payload.gs} started` : "games started not recorded";
  const minutes =
    payload.minutesPerGame !== null
      ? `${formatDecimal(payload.minutesPerGame, 1)} minutes per game`
      : "minutes not recorded";
  return `${games}, ${starts}, ${minutes}`;
}

function accessibleMetricText(entry: Entry): string {
  const parts: string[] = [entryName(entry)];
  if (entry.value.availability === "unavailable") {
    parts.push("not available in this era");
  } else {
    parts.push(entry.value.displayValue.length > 0 ? entry.value.displayValue : EM_DASH);
  }
  if (entry.value.percentile !== null && Number.isFinite(entry.value.percentile)) {
    parts.push(`${formatPercentile(entry.value.percentile)} percentile`);
  }
  if (entry.value.rank !== null && entry.value.rank > 0) {
    parts.push(`ranked ${formatOrdinal(entry.value.rank)}`);
  }
  if (entry.value.availability === "estimated" || entry.value.availability === "partial") {
    parts.push(shortLabel(entry.value.availability));
  }
  return parts.join(", ");
}

function WarningGlyph({ className }: { readonly className?: string }): JSX.Element {
  return (
    <svg width="14" height="14" viewBox="0 0 20 20" className={className} aria-hidden focusable="false">
      <path d="M10 2.5 18 17H2Z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      <line x1="10" y1="8" x2="10" y2="12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="10" cy="14.5" r="1" fill="currentColor" />
    </svg>
  );
}

function MetricRow({ entry, showsBar, size }: { readonly entry: Entry; readonly showsBar: boolean; readonly size: WidgetSizeKey }): JSX.Element {
  const tint = comparisonColorVar(
    comparisonOutcomeAgainst(entry.value.value, entry.value.leagueAverage, entry.descriptor?.higherIsBetter ?? null),
  );
  return (
    <div className={styles.metricRow} role="group" aria-label={accessibleMetricText(entry)}>
      <Text style="statLabel" className={styles.metricLabel} truncate ariaHidden>
        {entryLabel(entry)}
      </Text>
      <span className={styles.metricValue} aria-hidden="true">
        <Text style="tableCell" tabularNums color={entry.value.availability === "unavailable" ? "tertiary" : "primary"}>
          {entry.value.displayValue.length > 0 ? entry.value.displayValue : EM_DASH}
        </Text>
        <AvailabilityBadge availability={entry.value.availability} showsText={false} isInteractive={false} metricName={entryName(entry)} />
      </span>
      {showsBar ? (
        <span className={styles.metricBar} aria-hidden="true">
          <PercentileBar percentile={entry.value.percentile} tint={tint} showsLabel={size === "large"} trackHeight={7} />
        </span>
      ) : (
        <span className={styles.metricSpacer} aria-hidden="true" />
      )}
    </div>
  );
}

function PlayerSnapshotWidgetView({ size, payload: rawPayload }: WidgetViewProps): JSX.Element {
  let payload: PlayerSnapshotPayload;
  try {
    payload = decodePlayerSnapshotPayload(rawPayload);
  } catch {
    return (
      <ErrorTile
        size={size}
        isRetryable={false}
        message="This player snapshot's data did not match what the app expected."
      />
    );
  }

  const entries = buildEntries(payload, size);
  const showsBars = entries.some((entry) => entry.value.percentile !== null);
  const subtitleText = subtitle(payload);

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        <PlayerAvatar player={payload.player} size={avatarSize(size)} />
        <div className={styles.headerText}>
          <Text style="widgetTitle" className={styles.playerName} truncate>
            {payload.player.name}
          </Text>
          {subtitleText.length > 0 && (
            <Text as="span" style="caption">
              {subtitleText}
            </Text>
          )}
        </div>
      </div>
      <div aria-label={usageAccessibleText(payload)}>
        <Text as="p" style="statLabel" tabularNums className={styles.usageLine} ariaHidden>
          {usageText(payload)}
        </Text>
      </div>
      <div className={styles.rule} aria-hidden="true" />
      {entries.length === 0 ? (
        <Text as="p" style="caption" className={styles.emptyNote}>
          No metrics are available for this season.
        </Text>
      ) : (
        <div className={styles.slate}>
          {entries.map((entry) => (
            <MetricRow key={entry.value.metric} entry={entry} showsBar={showsBars} size={size} />
          ))}
        </div>
      )}
      {payload.eraNote && payload.eraNote.length > 0 && (
        <div className={styles.eraNote}>
          <WarningGlyph className={styles.eraNoteIcon} />
          <Text as="p" style="caption" color="warning">
            {payload.eraNote}
          </Text>
        </div>
      )}
      <div className={styles.spacer} />
    </div>
  );
}

export default PlayerSnapshotWidgetView;
