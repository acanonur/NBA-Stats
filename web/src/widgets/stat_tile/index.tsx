/**
 * `stat_tile` — one headline number, its supporting cast, and a sparkline.
 *
 * Ported from `ios/NBAStats/Widgets/StatTileWidget.swift`. The smallest widget in the catalog,
 * and the one most likely to be wrong in a way nobody notices, because a tile that shows `0.0`
 * looks like a bad night rather than like a stat that did not exist — so every number here goes
 * through `StatValue`/`design/format.ts`, never a hand-rolled `.toFixed()`.
 */
import type { JSX } from "react";
import type { WidgetViewProps } from "../../generated/registry";
import type { MetricDescriptor, MetricValue, SparklinePoint, StatTilePayload, SubjectRef } from "../../api/types";
import { METRICS_DOCUMENT } from "../../generated/contracts";
import { Text } from "../../design/Text";
import { StatValue } from "../../design/StatValue";
import { TeamBadge } from "../../design/TeamBadge";
import { Sparkline } from "../../design/svg/Sparkline";
import { formatValue } from "../../design/format";
import { chartColorVar } from "../../design/valueColor";
import { decodeStatTilePayload } from "./decode";
import styles from "./index.module.css";

const ALL_METRICS = METRICS_DOCUMENT.metrics as readonly MetricDescriptor[];
const METRIC_BY_KEY: ReadonlyMap<string, MetricDescriptor> = new Map(ALL_METRICS.map((metric) => [metric.key, metric]));

const SECONDARY_LIMIT: Readonly<Record<WidgetViewProps["size"], number>> = { small: 2, medium: 3, large: 4 };
const SPARKLINE_HEIGHT: Readonly<Record<WidgetViewProps["size"], number>> = { small: 24, medium: 38, large: 54 };

/** `"L. James"` when the name parts are known, otherwise the full name — the same formula as
 * `PlayerRef.shortName` (`ios/NBAStats/Core/Models.swift`); `api/types.ts` carries no computed
 * property for it, so each widget that needs one derives it itself. */
function subjectShortName(subject: SubjectRef): string {
  if (subject.player) {
    const initial = subject.player.firstName?.trim().charAt(0);
    if (initial && subject.player.lastName) return `${initial}. ${subject.player.lastName}`;
    return subject.player.name;
  }
  if (subject.team) return subject.team.abbr;
  return subject.type.charAt(0).toUpperCase() + subject.type.slice(1);
}

function subjectDisplayName(subject: SubjectRef): string {
  if (subject.player) return subject.player.name;
  if (subject.team) return subject.team.name;
  return subject.type.charAt(0).toUpperCase() + subject.type.slice(1);
}

/** A readable label for a metric key the bundled catalog does not know, so the headline number
 * is never left unlabelled: `"ts_pct"` becomes `"TS%"`, `"off_rtg"` becomes `"OFF RTG"` — matches
 * `StatTileWidget.fallbackShortName`. */
function fallbackShortName(key: string): string {
  const parts = key.split("_");
  let suffix = "";
  if (parts[parts.length - 1] === "pct") {
    parts.pop();
    suffix = "%";
  }
  const body = parts.map((part) => part.toUpperCase()).join(" ");
  return body.length === 0 ? key.toUpperCase() : body + suffix;
}

/** The season the era explainer should talk about, read off the server's `"2025-26 · Regular
 * Season · Per Game"` context line — the `·` split CONTRACT.md §7.7 calls out by name. */
function seasonHint(context: string): string | null {
  const head = context.split("·")[0]?.trim();
  return head ? head : null;
}

function hasPositiveRank(value: MetricValue): boolean {
  return value.rank !== null && value.rank !== undefined && value.rank > 0;
}

function hasDelta(value: MetricValue): boolean {
  return value.delta !== null && value.delta !== undefined;
}

function sparklineCaption(descriptor: MetricDescriptor | undefined, sparklineMetric: string, points: readonly SparklinePoint[], hasBaseline: boolean): string {
  const name = descriptor?.shortName ?? sparklineMetric;
  const played = points.filter((point) => point.y !== null).length;
  const baseline = hasBaseline ? " · dashed rule is the league average" : "";
  return `${name}, last ${played} games${baseline}`;
}

function sparklineDescription(descriptor: MetricDescriptor | undefined, sparklineMetric: string, points: readonly SparklinePoint[]): string {
  const name = descriptor?.name ?? sparklineMetric;
  const values = points.map((point) => point.y).filter((value): value is number => value !== null);
  if (values.length === 0) return `${name} over the recent games: no values recorded.`;
  const first = values[0];
  const last = values[values.length - 1];
  const format = (descriptor?.format ?? "decimal1") as Parameters<typeof formatValue>[1];
  const direction = Math.abs(last - first) <= 1e-9 ? "flat" : last > first ? "rising" : "falling";
  const missing = points.length - values.length;
  let text = `${name} over the last ${points.length} games, ${direction}, from ${formatValue(first, format)} to ${formatValue(last, format)}`;
  if (missing > 0) text += `, ${missing} with no value`;
  return text;
}

function SubjectBadge({ subject }: { readonly subject: SubjectRef }): JSX.Element | null {
  if (subject.team) return <TeamBadge abbreviation={subject.team.abbr} name={subject.team.name} size="small" />;
  if (subject.player?.teamAbbr) return <TeamBadge abbreviation={subject.player.teamAbbr} size="small" />;
  return null;
}

export default function StatTileWidget({ size, payload }: WidgetViewProps): JSX.Element {
  const data: StatTilePayload = decodeStatTilePayload(payload);
  const primaryDescriptor = METRIC_BY_KEY.get(data.primary.metric);
  const sparklineDescriptor = METRIC_BY_KEY.get(data.sparklineMetric);
  const season = seasonHint(data.context);

  const secondaryLimit = SECONDARY_LIMIT[size];
  const secondary = data.secondary.slice(0, Math.max(secondaryLimit, 0));

  const showsRankChip = hasPositiveRank(data.primary);
  const showsDeltaChip = size === "small" ? !showsRankChip : hasDelta(data.primary);

  const subjectName = size === "small" ? subjectShortName(data.subject) : subjectDisplayName(data.subject);

  const showsSparkline = data.sparkline.some((point) => point.y !== null);
  // The sparkline's own baseline is drawn only when it is measuring the headline metric —
  // drawing the headline's league average under a different stat's line would be a lie.
  const sparklineBaseline = data.sparklineMetric === data.primary.metric ? data.primary.leagueAverage : null;

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        <div className={styles.subjectRow}>
          <SubjectBadge subject={data.subject} />
          <Text style="widgetTitle" truncate className={styles.subjectName}>
            {subjectName}
          </Text>
        </div>
        {size !== "small" && data.context.length > 0 && (
          <Text as="p" style="caption" color="secondary">
            {data.context}
          </Text>
        )}
      </div>

      <StatValue
        value={data.primary}
        descriptor={primaryDescriptor}
        style="display"
        showsLabel
        showsRank={showsRankChip}
        showsDelta={showsDeltaChip}
        season={season}
        align="leading"
      />
      {!primaryDescriptor && (
        <Text style="statLabel" className={styles.fallbackLabel}>
          {fallbackShortName(data.primary.metric)}
        </Text>
      )}

      {secondary.length > 0 && (
        <div className={styles.secondaryRow}>
          {secondary.map((entry, index) => (
            <StatValue
              key={`${entry.metric}-${index}`}
              value={entry}
              descriptor={METRIC_BY_KEY.get(entry.metric)}
              style="cell"
              showsLabel
              showsRank={false}
              showsDelta={false}
              season={season}
              align="leading"
            />
          ))}
        </div>
      )}

      {showsSparkline && (
        <div className={styles.sparklineBlock}>
          <div className={styles.sparklineWrap} style={{ height: SPARKLINE_HEIGHT[size] }}>
            <Sparkline
              points={data.sparkline.map((point) => point.y)}
              tint={chartColorVar(0)}
              baseline={sparklineBaseline}
              showsArea={size !== "small"}
              ariaLabel={sparklineDescription(sparklineDescriptor, data.sparklineMetric, data.sparkline)}
            />
          </div>
          {size === "large" && (
            <Text style="caption" color="secondary">
              {sparklineCaption(sparklineDescriptor, data.sparklineMetric, data.sparkline, sparklineBaseline !== null)}
            </Text>
          )}
        </div>
      )}
    </div>
  );
}
