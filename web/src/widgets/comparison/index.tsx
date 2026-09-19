/**
 * `comparison` — two to four players side by side, in whichever of the three shapes the payload
 * asked for. Ported from `ios/NBAStats/Widgets/ComparisonWidget.swift`; see that file's own
 * docstring for the decisions reproduced here unchanged:
 *
 *   - `"bars"` — one row per metric, one bar per subject. Percentile-normalized when the payload
 *     says so and every displayed cell actually has a percentile (the **all-or-nothing** rule —
 *     one missing percentile anywhere demotes the whole chart to a row-relative raw scale);
 *     otherwise each row is scaled within itself, with the true value printed beside every bar
 *     either way, because a row-relative bar's *length* is not comparable across rows.
 *   - `"table"` — a compact matrix with the best value in each row emphasized, direction aware.
 *   - `"radar"` — drawn with {@link RadarPath}. Above eight metrics, or below three actually
 *     shown, it falls back to bars: nine spokes is a scribble, not a comparison.
 *   - `small` forces the table, regardless of the payload's requested `style` — there is no room
 *     for a legible chart in one grid column.
 *
 * A subject with no number for a metric gets no bar, no vertex, and no cell value beyond an em
 * dash — never a zero — and the gap is also named in a footnote rather than only silently absent.
 *
 * This widget hand-draws its grouped bars from plain CSS-percentage tracks
 * (`index.module.css`'s `.barFill`/`.barTrack`) rather than `design/svg/GroupedBars.tsx`: that
 * primitive's per-bar `valueLabel` callback receives only the already-scaled plotted value and a
 * series index, not which metric row produced it, so it cannot safely recover the *true* value a
 * row-relative scale needs to print next to a bar whose length alone is not meaningful (two
 * different metrics can easily land a subject at the same scaled position). Building the row
 * directly from the same value this component already holds keeps the true number attached to
 * the bar it describes, with no lookup to get wrong. (`widgets/shot_profile` does use
 * `GroupedBars`, because its bars are never rescaled — the value handed to `valueLabel` there
 * *is* the real percentage, so no such ambiguity exists.)
 */
import type { CSSProperties, JSX } from "react";
import type { WidgetViewProps } from "../../generated/registry";
import type { WidgetSizeKey } from "../../generated/tokens";
import type { MetricFormat } from "../../generated/contracts";
import type { ComparisonPayload, ComparisonSubject, MetricDescriptor, MetricValue, PlayerRef } from "../../api/types";
import { Text } from "../../design/Text";
import { PlayerAvatar } from "../../design/PlayerAvatar";
import { AvailabilityBadge } from "../../design/AvailabilityBadge";
import { PinnedColumnTable, type PinnedColumnDef } from "../../design/PinnedColumnTable";
import { RadarPath, type RadarAxis, type RadarSeries } from "../../design/svg/RadarPath";
import { EM_DASH, formatValue, seasonContext } from "../../design/format";
import { valueTreatmentClassName } from "../../design/availability";
import { chartColorVar } from "../../design/valueColor";
import { decodeComparisonPayload } from "./decode";
import styles from "./index.module.css";

type Presentation = "bars" | "table" | "radar";

interface SubjectInfo {
  readonly id: string;
  readonly subject: ComparisonSubject;
  readonly color: string;
}

const METRIC_LIMIT: Readonly<Record<WidgetSizeKey, number>> = { small: 4, medium: 6, large: 8 };
const RADAR_SIZE: Readonly<Record<WidgetSizeKey, number>> = { small: 150, medium: 190, large: 240 };

/** `"L. James"` when the name parts are known, otherwise the full name — the same formula every
 * other widget in this catalog derives locally (`api/types.ts` carries no computed property for
 * it); kept here rather than shared because WP5's brief is one self-contained directory per
 * kind. */
function subjectShortName(player: PlayerRef): string {
  const initial = player.firstName?.trim().charAt(0);
  if (!initial || !player.lastName) return player.name;
  return `${initial}. ${player.lastName}`;
}

function buildSubjectInfos(subjects: readonly ComparisonSubject[]): readonly SubjectInfo[] {
  return subjects.slice(0, 4).map((subject, index) => ({
    id: String(subject.player.playerId),
    subject,
    color: chartColorVar(subject.colorIndex >= 0 ? subject.colorIndex : index),
  }));
}

function valueFor(subject: ComparisonSubject, metricKey: string): MetricValue | undefined {
  return subject.values.find((value) => value.metric === metricKey);
}

/** The server formats every value; this only covers a cell the payload never sent. */
function displayText(measurement: MetricValue | undefined, metric: MetricDescriptor): string {
  if (!measurement) return EM_DASH;
  if (measurement.displayValue.length > 0) return measurement.displayValue;
  return formatValue(measurement.value, metric.format as MetricFormat);
}

function hasFiniteValue(measurement: MetricValue | undefined): measurement is MetricValue & { value: number } {
  return measurement !== undefined && measurement.value !== null && Number.isFinite(measurement.value);
}

function presentationFor(
  size: WidgetSizeKey,
  style: ComparisonPayload["style"],
  totalMetricCount: number,
  shownMetricCount: number,
): Presentation {
  if (size === "small") return "table";
  if (style === "table") return "table";
  if (style === "radar") return totalMetricCount > 8 || shownMetricCount < 3 ? "bars" : "radar";
  return "bars";
}

/** Percentiles only drive the axis when every displayed cell that has a value actually has a
 * percentile too — a half-filled percentile axis would silently compare two different scales
 * (WEB_DESIGN.md §7.7's "all-or-nothing" rule). */
function computeUsesPercentileAxis(
  normalization: ComparisonPayload["normalization"],
  metrics: readonly MetricDescriptor[],
  subjectInfos: readonly SubjectInfo[],
): boolean {
  if (normalization !== "percentile") return false;
  const measurements = metrics.flatMap((metric) => subjectInfos.map((info) => valueFor(info.subject, metric.key)));
  const present = measurements.filter(hasFiniteValue);
  return present.length > 0 && present.every((value) => value.percentile !== null && Number.isFinite(value.percentile));
}

function bestValueFor(metric: MetricDescriptor, subjectInfos: readonly SubjectInfo[]): number | null {
  const values = subjectInfos
    .map((info) => valueFor(info.subject, metric.key))
    .filter(hasFiniteValue)
    .map((value) => value.value);
  if (values.length === 0) return null;
  return metric.higherIsBetter ? Math.max(...values) : Math.min(...values);
}

/** A fraction in `[0, 1]` for a bar or a radar vertex, or `null` when the subject has no number
 * for this metric — a missing measurement draws no mark rather than a zero-length one. */
function plottedFraction(
  metric: MetricDescriptor,
  subject: ComparisonSubject,
  rowValues: readonly number[],
  usesPercentileAxis: boolean,
  orientForRadar: boolean,
): number | null {
  const measurement = valueFor(subject, metric.key);
  if (!hasFiniteValue(measurement)) return null;
  if (usesPercentileAxis && measurement.percentile !== null && Number.isFinite(measurement.percentile)) {
    const normalized = measurement.percentile > 1 ? measurement.percentile / 100 : measurement.percentile;
    return Math.min(Math.max(normalized, 0), 1);
  }
  if (rowValues.length === 0) return 0.6;
  const lowest = Math.min(...rowValues);
  const highest = Math.max(...rowValues);
  if (highest - lowest <= 1e-9) return 0.6;
  const fraction = (measurement.value - lowest) / (highest - lowest);
  if (!orientForRadar) return 0.12 + 0.88 * fraction;
  // The radar orients a row-relative fraction so "further from the centre" always means
  // "better", which the percentile scale already guarantees on its own; the bar chart has no
  // such orientation concept — a bar just gets longer toward the right regardless of direction.
  const oriented = metric.higherIsBetter ? fraction : 1 - fraction;
  return 0.2 + 0.8 * Math.min(Math.max(oriented, 0), 1);
}

function rowRawValues(metric: MetricDescriptor, subjectInfos: readonly SubjectInfo[]): readonly number[] {
  return subjectInfos.map((info) => valueFor(info.subject, metric.key)).filter(hasFiniteValue).map((value) => value.value);
}

function missingNotes(metrics: readonly MetricDescriptor[], subjectInfos: readonly SubjectInfo[]): readonly string[] {
  const notes: string[] = [];
  for (const metric of metrics) {
    const missing = subjectInfos.filter((info) => !hasFiniteValue(valueFor(info.subject, metric.key)));
    if (missing.length === 0) continue;
    const names = missing.map((info) => subjectShortName(info.subject.player)).join(", ");
    notes.push(`${metric.shortName} has no number for ${names}: ${metric.availability.seasonFrom} is as far back as it goes.`);
  }
  return notes.slice(0, 2);
}

function scaleNote(presentation: Presentation, usesPercentileAxis: boolean): string | null {
  if (presentation === "bars") {
    return usesPercentileAxis
      ? "Bars are league percentiles: 100 is the best mark in the league this season."
      : "Each row is scaled within itself — the labels carry the real numbers, not the bar lengths.";
  }
  if (presentation === "radar") {
    return usesPercentileAxis
      ? "Spokes are league percentiles; further from the centre is better."
      : "Spokes are scaled within each metric and oriented so further from the centre is better.";
  }
  return null;
}

function accessibilityOverview(subjectInfos: readonly SubjectInfo[], metrics: readonly MetricDescriptor[]): string {
  const names = subjectInfos.map((info) => info.subject.player.name).join(" versus ");
  const metricNames = metrics.map((metric) => metric.shortName).join(", ");
  return `Comparison of ${names} across ${metricNames}`;
}

function radarAccessibilityLabel(subjectInfos: readonly SubjectInfo[], metrics: readonly MetricDescriptor[]): string {
  const rows = subjectInfos.map((info) => {
    const cells = metrics
      .map((metric) => `${metric.shortName} ${displayText(valueFor(info.subject, metric.key), metric)}`)
      .join(", ");
    return `${info.subject.player.name}: ${cells}`;
  });
  return `${accessibilityOverview(subjectInfos, metrics)}. ${rows.join(". ")}.`;
}

function DirectionArrow(): JSX.Element {
  return (
    <svg width="8" height="8" viewBox="0 0 10 10" aria-hidden focusable="false" className={styles.directionArrow}>
      <path d="M2 4 5 8 8 4" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function SubjectChip({ info }: { readonly info: SubjectInfo }): JSX.Element {
  return (
    <div className={styles.subjectChip}>
      <span className={styles.avatarRing} style={{ "--ring-color": info.color } as CSSProperties}>
        <PlayerAvatar player={info.subject.player} size="small" />
      </span>
      <Text style="statLabel" truncate className={styles.subjectChipName}>
        {subjectShortName(info.subject.player)}
      </Text>
    </div>
  );
}

function Legend({ subjectInfos }: { readonly subjectInfos: readonly SubjectInfo[] }): JSX.Element | null {
  if (subjectInfos.length <= 1) return null;
  return (
    <div className={styles.legend}>
      {subjectInfos.map((info) => (
        <span className={styles.legendItem} key={info.id}>
          <span className={styles.legendDot} style={{ backgroundColor: info.color }} aria-hidden />
          <Text style="caption" color="secondary">
            {subjectShortName(info.subject.player)}
          </Text>
        </span>
      ))}
    </div>
  );
}

function BarsChart({
  metrics,
  subjectInfos,
  usesPercentileAxis,
}: {
  readonly metrics: readonly MetricDescriptor[];
  readonly subjectInfos: readonly SubjectInfo[];
  readonly usesPercentileAxis: boolean;
}): JSX.Element {
  return (
    <div className={styles.bars} role="group" aria-label={accessibilityOverview(subjectInfos, metrics)}>
      {metrics.map((metric) => {
        const rowValues = rowRawValues(metric, subjectInfos);
        return (
          <div className={styles.barsRow} key={metric.key}>
            <Text style="tableHeader" truncate className={styles.barsRowLabel}>
              {metric.shortName}
            </Text>
            <div className={styles.barsRowTracks}>
              {subjectInfos.map((info) => {
                const measurement = valueFor(info.subject, metric.key);
                const fraction = plottedFraction(metric, info.subject, rowValues, usesPercentileAxis, false);
                return (
                  <div className={styles.barTrack} key={info.id}>
                    <div className={styles.barTrackFill}>
                      {fraction !== null && (
                        <div className={styles.barFill} style={{ width: `${fraction * 100}%`, backgroundColor: info.color }} />
                      )}
                    </div>
                    <Text
                      style="tableHeader"
                      tabularNums
                      color={fraction === null ? "tertiary" : "primary"}
                      className={styles.barValue}
                    >
                      {displayText(measurement, metric)}
                    </Text>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function RadarChart({
  metrics,
  subjectInfos,
  usesPercentileAxis,
  size,
}: {
  readonly metrics: readonly MetricDescriptor[];
  readonly subjectInfos: readonly SubjectInfo[];
  readonly usesPercentileAxis: boolean;
  readonly size: WidgetSizeKey;
}): JSX.Element {
  const axes: readonly RadarAxis[] = metrics.map((metric) => ({ key: metric.key, label: metric.shortName }));
  const series: readonly RadarSeries[] = subjectInfos.map((info) => ({
    id: info.id,
    color: info.color,
    values: metrics.map((metric) => {
      const rowValues = rowRawValues(metric, subjectInfos);
      return plottedFraction(metric, info.subject, rowValues, usesPercentileAxis, true);
    }),
  }));
  return (
    <div className={styles.radarWrap} role="img" aria-label={radarAccessibilityLabel(subjectInfos, metrics)}>
      <RadarPath axes={axes} series={series} size={RADAR_SIZE[size]} />
    </div>
  );
}

function ComparisonTable({
  metrics,
  subjectInfos,
  season,
}: {
  readonly metrics: readonly MetricDescriptor[];
  readonly subjectInfos: readonly SubjectInfo[];
  readonly season: string;
}): JSX.Element {
  const columns: readonly PinnedColumnDef<MetricDescriptor>[] = subjectInfos.map((info) => ({
    key: info.id,
    align: "trailing",
    header: (
      <span className={styles.subjectHeaderCell}>
        <span className={styles.legendDot} style={{ backgroundColor: info.color }} aria-hidden />
        <Text style="tableHeader" truncate>
          {subjectShortName(info.subject.player)}
        </Text>
      </span>
    ),
    render: (metric) => {
      const measurement = valueFor(info.subject, metric.key);
      const availability = measurement?.availability ?? "unavailable";
      return (
        <span className={styles.tableCell}>
          <Text style="tableCell" tabularNums className={valueTreatmentClassName(availability)}>
            {displayText(measurement, metric)}
          </Text>
          <AvailabilityBadge
            availability={availability}
            showsText={false}
            isInteractive={false}
            metricName={metric.name}
            season={season}
          />
        </span>
      );
    },
    isHighlighted: (metric) => {
      const best = bestValueFor(metric, subjectInfos);
      const measurement = valueFor(info.subject, metric.key);
      if (best === null || !hasFiniteValue(measurement)) return false;
      return Math.abs(measurement.value - best) < 1e-9;
    },
  }));

  return (
    <PinnedColumnTable
      rows={metrics}
      rowKey={(metric) => metric.key}
      pinnedColumn={{
        key: "metric",
        header: <Text style="tableHeader">Metric</Text>,
        align: "leading",
        render: (metric) => (
          <span className={styles.metricLabelCell}>
            <Text style="tableCell" color="secondary" truncate>
              {metric.shortName}
            </Text>
            {!metric.higherIsBetter && <DirectionArrow />}
          </span>
        ),
      }}
      columns={columns}
      pinnedWidth={72}
      columnWidth={62}
    />
  );
}

export default function ComparisonWidget({ size, payload }: WidgetViewProps): JSX.Element {
  const data: ComparisonPayload = decodeComparisonPayload(payload);
  const subjectInfos = buildSubjectInfos(data.subjects);
  const metricLimit = METRIC_LIMIT[size];
  const metrics = data.metrics.slice(0, Math.max(metricLimit, 0));
  const context = seasonContext(data.season, data.seasonType);

  if (subjectInfos.length === 0 || metrics.length === 0) {
    return (
      <div className={styles.root}>
        <Text as="p" style="tableCell" color="secondary">
          Pick at least two subjects and one metric to compare.
        </Text>
        {context.length > 0 && (
          <Text as="p" style="caption" color="secondary">
            {context}
          </Text>
        )}
      </div>
    );
  }

  const usesPercentileAxis = computeUsesPercentileAxis(data.normalization, metrics, subjectInfos);
  const presentation = presentationFor(size, data.style, data.metrics.length, metrics.length);
  const showsAvatars = size !== "small" && subjectInfos.length <= 3;
  const footnoteLines = [scaleNote(presentation, usesPercentileAxis), ...missingNotes(metrics, subjectInfos)].filter(
    (line): line is string => line !== null,
  );

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        {showsAvatars ? (
          <div className={styles.subjectChips}>
            {subjectInfos.map((info) => (
              <SubjectChip info={info} key={info.id} />
            ))}
          </div>
        ) : (
          <Text style="statLabel" truncate>
            {subjectInfos.map((info) => subjectShortName(info.subject.player)).join(" · ")}
          </Text>
        )}
        {context.length > 0 && (
          <Text style="caption" color="secondary" className={styles.headerContext}>
            {context}
          </Text>
        )}
      </div>

      {presentation === "bars" && (
        <BarsChart metrics={metrics} subjectInfos={subjectInfos} usesPercentileAxis={usesPercentileAxis} />
      )}
      {presentation === "radar" && (
        <RadarChart metrics={metrics} subjectInfos={subjectInfos} usesPercentileAxis={usesPercentileAxis} size={size} />
      )}
      {presentation === "table" && <ComparisonTable metrics={metrics} subjectInfos={subjectInfos} season={data.season} />}

      {presentation !== "table" && <Legend subjectInfos={subjectInfos} />}

      {footnoteLines.length > 0 && (
        <div className={styles.footnote}>
          {footnoteLines.map((line) => (
            <Text as="p" style="caption" color="secondary" key={line}>
              {line}
            </Text>
          ))}
        </div>
      )}
    </div>
  );
}
