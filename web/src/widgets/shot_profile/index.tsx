/**
 * `shot_profile` — where the shots come from, and how they go in, against the league's own diet.
 *
 * Ported from `ios/NBAStats/Widgets/ShotProfileWidget.swift`; see that file's own docstring for
 * the decision reproduced here unchanged: **no court, no hex map.** A hex map of five aggregated
 * zones is a decorative lie — the payload has five numbers per zone, not a shot chart — so this
 * widget draws two horizontal grouped-bar charts (subject vs. a dimmed league bar) over the
 * contract's fixed 5-zone order instead.
 *
 * A zone the subject never shot from (or a season before 1996-97, where the league has no shot
 * locations at all — `backend/nbastats/widgets/shot_profile.py`'s `SHOT_CHARTS_FROM`) arrives
 * with every number `null`. This widget reads that as **"not tracked"**, never as a zero-shooting
 * player: a zone with no finite `shareOfFga`/`fgPct` for the subject is left out of both charts
 * entirely (`design/svg/GroupedBars.tsx`'s own rule — a `null` series value draws no bar, not a
 * zero-length one), and when *every* zone is like that the charts are replaced outright by a
 * plain explanation instead of two empty axes.
 */
import type { JSX } from "react";
import type { WidgetViewProps } from "../../generated/registry";
import type { ShotProfilePayload, ShotZone, SubjectRef } from "../../api/types";
import type { WidgetSizeKey } from "../../generated/tokens";
import { Text } from "../../design/Text";
import { PlayerAvatar } from "../../design/PlayerAvatar";
import { TeamBadge } from "../../design/TeamBadge";
import { AvailabilityBadge } from "../../design/AvailabilityBadge";
import { EM_DASH, formatValue, seasonContext } from "../../design/format";
import { chartColorVar } from "../../design/valueColor";
import { GroupedBars, type GroupedBarGroup } from "../../design/svg/GroupedBars";
import { decodeShotProfilePayload } from "./decode";
import { ErrorTile } from "../../design/StateViews";
import { useChartWidth } from "../../design/useChartWidth";
import styles from "./index.module.css";

const LEAGUE_COLOR = "color-mix(in srgb, var(--hw-neutral) 55%, transparent)";

const CHART_ROW_HEIGHT: Readonly<Record<WidgetSizeKey, number>> = { small: 16, medium: 22, large: 24 };
const CHART_BASE_HEIGHT: Readonly<Record<WidgetSizeKey, number>> = { small: 24, medium: 26, large: 28 };
const DEFAULT_CHART_WIDTH: Readonly<Record<WidgetSizeKey, number>> = { small: 220, medium: 280, large: 420 };

/** `"L. James"` when the name parts are known, otherwise the full name — the same formula as
 * `PlayerRef.shortName` (`ios/NBAStats/Core/Models.swift`); `api/types.ts` carries no computed
 * property for it, so each widget that needs one derives it itself (see `widgets/stat_tile`,
 * `widgets/scoreboard` for the same helper, kept local per WP5's "one self-contained directory
 * per kind" brief). */
function subjectDisplayName(subject: SubjectRef): string {
  if (subject.player) return subject.player.name;
  if (subject.team) return subject.team.name;
  return subject.type.charAt(0).toUpperCase() + subject.type.slice(1);
}

function SubjectBadge({ subject }: { readonly subject: SubjectRef }): JSX.Element | null {
  if (subject.player) return <PlayerAvatar player={subject.player} size="small" />;
  if (subject.team) return <TeamBadge abbreviation={subject.team.abbr} name={subject.team.name} size="small" />;
  return null;
}

function hasFinite(value: number | null): value is number {
  return value !== null && Number.isFinite(value);
}

/** `[0, max observed value * 1.15]`, floored so a chart with one tiny bar still has some visible
 * headroom and capped at 1 because every number these two charts draw is itself a fraction of
 * attempts or a shooting percentage, never something that can exceed 100%. */
function shareOfDomain(values: readonly number[]): readonly [number, number] {
  const max = values.length > 0 ? Math.max(...values) : 0.1;
  return [0, Math.min(Math.max(max * 1.15, 0.05), 1)];
}

function buildGroups(
  zones: readonly ShotZone[],
  subjectValue: (zone: ShotZone) => number | null,
  leagueValue: (zone: ShotZone) => number | null,
): GroupedBarGroup[] {
  return zones.map((zone) => ({
    label: zone.label,
    values: [subjectValue(zone), leagueValue(zone)],
  }));
}

function shareAccessibilityLabel(subjectName: string, zones: readonly ShotZone[]): string {
  if (zones.length === 0) return `Share of attempts by zone for ${subjectName}: not tracked this season.`;
  const parts = zones.map((zone) => {
    const share = formatValue(zone.shareOfFga, "percent1");
    const accuracy = formatValue(zone.fgPct, "percent1");
    return `${zone.label} ${share} of attempts at ${accuracy}`;
  });
  return `Share of attempts by zone for ${subjectName}: ${parts.join(", ")}.`;
}

function accuracyAccessibilityLabel(subjectName: string, zones: readonly ShotZone[]): string {
  const parts = zones.map((zone) => {
    const own = formatValue(zone.fgPct, "percent1");
    const league = formatValue(zone.leagueFgPct, "percent1");
    return `${zone.label} ${own}, league ${league}`;
  });
  return `Field goal percentage by zone for ${subjectName}: ${parts.join(", ")}.`;
}

/** The compact text alternative to the accuracy chart, shown when the tile is not large enough
 * to draw a second chart (`ios/NBAStats/Widgets/ShotProfileWidget.swift`'s own `footnoteLines`) —
 * the accuracy numbers still reach the reader, just as words instead of bars. */
function accuracyFootnote(zones: readonly ShotZone[]): string | null {
  if (zones.length === 0) return null;
  const items = zones
    .slice(0, 3)
    .map((zone) => `${zone.label} ${formatValue(zone.fgPct, "percent1")} [${formatValue(zone.leagueFgPct, "percent1")}]`);
  return `Accuracy, league in brackets: ${items.join(" · ")}`;
}

function RateChip({ label, value }: { readonly label: string; readonly value: number | null }): JSX.Element {
  return (
    <div className={styles.rateChip}>
      <Text style="caption" color="secondary">
        {label}
      </Text>
      <Text style="tableHeader" tabularNums color={hasFinite(value) ? "primary" : "tertiary"}>
        {formatValue(value, "percent1")}
      </Text>
    </div>
  );
}

function LegendItem({ color, label }: { readonly color: string; readonly label: string }): JSX.Element {
  return (
    <span className={styles.legendItem}>
      <span className={styles.legendDot} style={{ backgroundColor: color }} aria-hidden />
      <Text style="caption" color="secondary">
        {label}
      </Text>
    </span>
  );
}

function Chart({
  title,
  groups,
  domain,
  width,
  height,
  ariaLabel,
}: {
  readonly title: string;
  readonly groups: readonly GroupedBarGroup[];
  readonly domain: readonly [number, number];
  readonly width: number;
  readonly height: number;
  readonly ariaLabel: string;
}): JSX.Element {
  return (
    <div className={styles.chartBlock}>
      <Text style="caption" color="secondary">
        {title}
      </Text>
      <div role="img" aria-label={ariaLabel}>
        <GroupedBars
          groups={groups}
          seriesColors={[chartColorVar(0), LEAGUE_COLOR]}
          domain={domain}
          width={width}
          height={height}
          valueLabel={(value) => formatValue(value, "percent1")}
        />
      </div>
    </div>
  );
}

/**
 * Decoded in a wrapper rather than in the view, because the view calls `useChartWidth` and a
 * hook may not sit after an early return. These decoders throw on any shape deviation, and a
 * bare call in render meant one off-contract field took the whole page down;
 * `WidgetContainer`'s `TileErrorBoundary` is the backstop, and this is the message worth
 * showing.
 */
export default function ShotProfileWidget({ size, payload }: WidgetViewProps): JSX.Element {
  let data: ShotProfilePayload;
  try {
    data = decodeShotProfilePayload(payload);
  } catch {
    return (
      <ErrorTile
        size={size}
        isRetryable={false}
        message="The data behind this shot profile did not match what the app expected."
      />
    );
  }
  return <ShotProfileView size={size} data={data} />;
}

function ShotProfileView({
  size,
  data,
}: {
  readonly size: WidgetSizeKey;
  readonly data: ShotProfilePayload;
}): JSX.Element {
  const subjectName = subjectDisplayName(data.subject);
  const context = seasonContext(data.season, data.seasonType);

  const shareZones = data.zones.filter((zone) => hasFinite(zone.shareOfFga));
  const accuracyZones = data.zones.filter((zone) => hasFinite(zone.fgPct));
  const hasNothingToPlot = shareZones.length === 0 && accuracyZones.length === 0;
  const showsAccuracyChart = size === "large" && accuracyZones.length > 0;

  const [chartRef, measuredWidth] = useChartWidth(DEFAULT_CHART_WIDTH[size]);

  const rowHeight = CHART_ROW_HEIGHT[size];
  const baseHeight = CHART_BASE_HEIGHT[size];

  const shareGroups = buildGroups(
    shareZones,
    (zone) => zone.shareOfFga,
    (zone) => zone.leagueShareOfFga,
  );
  const shareDomain = shareOfDomain([
    ...shareZones.flatMap((zone) => [zone.shareOfFga, zone.leagueShareOfFga].filter(hasFinite)),
  ]);
  const shareHeight = Math.max(shareZones.length, 1) * rowHeight + baseHeight;

  const accuracyGroups = buildGroups(
    accuracyZones,
    (zone) => zone.fgPct,
    (zone) => zone.leagueFgPct,
  );
  const accuracyDomain = shareOfDomain([
    ...accuracyZones.flatMap((zone) => [zone.fgPct, zone.leagueFgPct].filter(hasFinite)),
  ]);
  const accuracyHeight = Math.max(accuracyZones.length, 1) * rowHeight + baseHeight;

  const footnote = !showsAccuracyChart ? accuracyFootnote(accuracyZones) : null;

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        <div className={styles.subjectRow}>
          <SubjectBadge subject={data.subject} />
          <Text style="statLabel" truncate className={styles.subjectName}>
            {subjectName}
          </Text>
        </div>
        {context.length > 0 && (
          <Text as="p" style="caption" color="secondary">
            {context}
          </Text>
        )}
      </div>

      {hasNothingToPlot ? (
        <div className={styles.unavailable} role="status">
          <Text style="displayValue" color="tertiary" ariaHidden>
            {EM_DASH}
          </Text>
          <Text as="p" style="tableCell" color="secondary" className={styles.unavailableText}>
            {data.note ?? "Shot locations are not tracked for this season."}
          </Text>
          <AvailabilityBadge
            availability="unavailable"
            metricName="Shot profile"
            season={data.season}
            notes={data.note ? [data.note] : []}
          />
        </div>
      ) : (
        <>
          <div className={styles.rateRow}>
            <RateChip label="3PA rate" value={data.threePointRate} />
            <RateChip label="FT rate" value={data.freeThrowRate} />
          </div>

          <div className={styles.charts} ref={chartRef}>
            <Chart
              title="Share of attempts"
              groups={shareGroups}
              domain={shareDomain}
              width={measuredWidth}
              height={shareHeight}
              ariaLabel={shareAccessibilityLabel(subjectName, shareZones)}
            />
            {showsAccuracyChart && (
              <Chart
                title="Field goal percentage"
                groups={accuracyGroups}
                domain={accuracyDomain}
                width={measuredWidth}
                height={accuracyHeight}
                ariaLabel={accuracyAccessibilityLabel(subjectName, accuracyZones)}
              />
            )}
          </div>

          <div className={styles.legend}>
            <LegendItem color={chartColorVar(0)} label={subjectName} />
            <LegendItem color={LEAGUE_COLOR} label="League" />
          </div>

          {footnote && (
            <Text as="p" style="caption" color="secondary">
              {footnote}
            </Text>
          )}
        </>
      )}
    </div>
  );
}
