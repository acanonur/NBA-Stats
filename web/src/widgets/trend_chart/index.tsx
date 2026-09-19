/**
 * `trend_chart` — up to four subjects' per-game values with their rolling average. Ported from
 * `ios/NBAStats/Widgets/TrendChartWidget.swift`: the rolling line is the emphasized one wherever
 * a series has one at all, raw per-game marks only draw in at `size === "large"`, and a persistent
 * readout row above the chart (always the latest game, since this build has no drag-to-inspect)
 * never changes the tile's height the way a hover-only tooltip would.
 */
import { useId, type JSX } from "react";
import type { WidgetViewProps } from "../../generated/registry";
import type { WidgetSizeKey } from "../../generated/tokens";
import type { MetricFormat } from "../../generated/contracts";
import type { TrendChartPayload } from "../../api/types";
import { Text } from "../../design/Text";
import { ErrorTile } from "../../design/StateViews";
import { CartesianFrame, X_AXIS_LABEL_HEIGHT, normalizeInDomain } from "../../design/svg/CartesianFrame";
import type { CartesianTick } from "../../design/svg/CartesianFrame";
import { RunPolyline } from "../../design/svg/RunPolyline";
import { chartColorVar } from "../../design/valueColor";
import { EM_DASH, formatValue, shortGameDate } from "../../design/format";
import { decodeTrendChartPayload } from "./payload";
import { buildTrendModel, thinnedIndices } from "./model";
import type { TrendModel, TrendSeriesModel } from "./model";
import { useChartWidth } from "../../design/useChartWidth";
import styles from "./index.module.css";

const RAW_MARK_OPACITY = 0.35;
const CHART_HEIGHT: Readonly<Record<WidgetSizeKey, number>> = { small: 96, medium: 142, large: 206 };

function evenTicks(domain: readonly [number, number], count: number): readonly number[] {
  const [low, high] = domain;
  if (!Number.isFinite(low) || !Number.isFinite(high) || high <= low || count < 2) return [low];
  const step = (high - low) / (count - 1);
  return Array.from({ length: count }, (_, index) => low + step * index);
}

function xAxisTicks(model: TrendModel, count: number): readonly CartesianTick[] {
  const byEpoch = new Map<number, string>();
  for (const line of model.series) {
    for (const point of line.points) byEpoch.set(point.epoch, point.dateText);
  }
  const sorted = Array.from(byEpoch.entries()).sort((a, b) => a[0] - b[0]);
  const indices = thinnedIndices(sorted.length, count);
  return indices.map((index) => {
    const [epoch, dateText] = sorted[index];
    return { position: normalizeInDomain(epoch, model.xDomain), label: shortGameDate(dateText) };
  });
}

function emphasizedValue(point: { readonly raw: number | null; readonly rolling: number | null }, usesRolling: boolean): number | null {
  return usesRolling ? point.rolling : point.raw;
}

function TrendLine({ model, format, width, height, showsRawPoints }: { readonly model: TrendModel; readonly format: MetricFormat; readonly width: number; readonly height: number; readonly showsRawPoints: boolean }): JSX.Element {
  const clipId = useId();
  // `showsRawPoints` is already `size === "large"` — reused here rather than threading `size`
  // through as a second prop, since the two questions ("show raw dots" / "how many x ticks") are
  // decided by the same size boundary in the Swift original.
  const xTicks = xAxisTicks(model, showsRawPoints ? 5 : 3);
  const plotHeight = Math.max(height - X_AXIS_LABEL_HEIGHT, 1);
  const yTicks: readonly CartesianTick[] = evenTicks(model.yDomain, 4).map((value) => ({
    position: normalizeInDomain(value, model.yDomain),
    label: formatValue(value, format),
  }));

  const toX = (epoch: number): number => normalizeInDomain(epoch, model.xDomain) * width;
  const toY = (value: number): number => plotHeight * (1 - normalizeInDomain(value, model.yDomain));

  return (
    <CartesianFrame width={width} height={height} xTicks={xTicks} yTicks={yTicks} clipId={clipId}>
      {model.series.map((line) => {
        const color = chartColorVar(line.colorIndex);
        const runPoints = line.points.map((point) => {
          const value = emphasizedValue(point, line.usesRolling);
          return { x: toX(point.epoch), y: value === null || !Number.isFinite(value) ? null : toY(value) };
        });
        return <RunPolyline key={line.id} points={runPoints} stroke={color} strokeWidth={2} />;
      })}
      {showsRawPoints &&
        model.series.flatMap((line) => {
          const color = chartColorVar(line.colorIndex);
          return line.points
            .filter((point) => point.raw !== null && Number.isFinite(point.raw))
            .map((point) => <circle key={`raw-${point.id}`} cx={toX(point.epoch)} cy={toY(point.raw as number)} r={2.4} fill={color} opacity={RAW_MARK_OPACITY} />);
        })}
    </CartesianFrame>
  );
}

function legendItems(model: TrendModel, hasLeagueAverage: boolean): readonly { readonly id: string; readonly label: string; readonly color: string; readonly dashed: boolean }[] {
  const items = model.series.map((line) => ({ id: line.id, label: line.label, color: chartColorVar(line.colorIndex), dashed: false }));
  if (hasLeagueAverage) items.push({ id: "league", label: "League average", color: "var(--hw-text-tertiary)", dashed: true });
  return items;
}

function footnoteLines(model: TrendModel, showsRawPoints: boolean): readonly string[] {
  const lines: string[] = [];
  const hasRaw = model.series.some((line) => line.points.some((point) => point.raw !== null));
  if (!showsRawPoints && hasRaw && model.usesRolling) lines.push("Per-game values are folded into the rolling line at this size.");
  if (model.droppedPoints > 0) lines.push(`${model.droppedPoints} point${model.droppedPoints === 1 ? "" : "s"} had no readable game date and are not plotted.`);
  return lines;
}

function accessibleChartLabel(payload: TrendChartPayload, model: TrendModel): string {
  const names = model.series.map((line) => line.label).join(", ");
  const count = model.series.reduce((total, line) => total + line.points.length, 0);
  let text = `${payload.metric.name} over ${count} games for ${names}`;
  if (model.usesRolling && payload.rollingWindow > 1) text += `, drawn as a ${payload.rollingWindow}-game rolling average`;
  if (payload.leagueAverage !== null) text += `, league average ${formatValue(payload.leagueAverage, payload.metric.format as MetricFormat)}`;
  return text;
}

function ReadoutRow({ line, format }: { readonly line: TrendSeriesModel; readonly format: MetricFormat }): JSX.Element {
  const latest = line.latest;
  const valueText = latest && latest.raw !== null ? formatValue(latest.raw, format) : EM_DASH;
  const rollingText = latest && line.usesRolling && latest.rolling !== null ? `avg ${formatValue(latest.rolling, format)}` : null;
  return (
    <div className={styles.readoutLine}>
      <span className={styles.readoutSwatch} style={{ backgroundColor: chartColorVar(line.colorIndex) }} aria-hidden="true" />
      <Text style="caption" color="secondary" truncate className={styles.readoutLabel}>
        {line.label}
      </Text>
      {latest?.opponentAbbr && (
        <Text style="caption" color="tertiary">
          vs {latest.opponentAbbr}
        </Text>
      )}
      <span className={styles.readoutSpacer} />
      <Text style="statValue" tabularNums color={latest?.raw === null || latest === null ? "tertiary" : undefined}>
        {valueText}
      </Text>
      {rollingText && (
        <Text style="tableHeader" tabularNums color="tertiary">
          {rollingText}
        </Text>
      )}
    </div>
  );
}

function TrendChartWidgetView({ size, payload: rawPayload }: WidgetViewProps): JSX.Element {
  // Called unconditionally, before the decode's early return — react-hooks/rules-of-hooks
  // requires the same hook order on every render even on the decode-failure path.
  const [containerRef, width] = useChartWidth(320);

  let payload: TrendChartPayload;
  try {
    payload = decodeTrendChartPayload(rawPayload);
  } catch {
    return <ErrorTile size={size} isRetryable={false} message="This trend chart's data did not match what the app expected." />;
  }

  const model = buildTrendModel(payload);
  const height = CHART_HEIGHT[size] ?? CHART_HEIGHT.medium;
  const showsRawPoints = size === "large";
  // `MetricDescriptor.format` is a bare `string` on `api/types.ts` — see `StatValue.tsx`'s own
  // comment on this same cast.
  const format = payload.metric.format as MetricFormat;
  const latestDate = model.series.reduce<string | null>((best, line) => (line.latest ? line.latest.dateText : best), null);

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        <Text style="statLabel" truncate>
          {payload.metric.name}
        </Text>
        {model.usesRolling && payload.rollingWindow > 1 && (
          <Text style="caption" truncate>
            {payload.rollingWindow}-game rolling average
          </Text>
        )}
        <span className={styles.headerSpacer} />
        {payload.leagueAverage !== null && (
          <Text style="tableHeader" tabularNums color="tertiary">
            Lg {formatValue(payload.leagueAverage, format)}
          </Text>
        )}
      </div>
      {model.isEmpty ? (
        <Text as="p" style="caption" className={styles.emptyNote}>
          No games with a {payload.metric.name} value yet.
        </Text>
      ) : (
        <>
          <div className={styles.readout}>
            <Text as="p" style="tableHeader" tabularNums color="secondary">
              {latestDate ? shortGameDate(latestDate) : EM_DASH} · latest
            </Text>
            {model.series.map((line) => (
              <ReadoutRow key={line.id} line={line} format={format} />
            ))}
          </div>
          <div ref={containerRef} className={styles.chartWrap} role="img" aria-label={accessibleChartLabel(payload, model)}>
            <TrendLine model={model} format={format} width={width} height={height} showsRawPoints={showsRawPoints} />
          </div>
          {legendItems(model, payload.leagueAverage !== null).length > 1 && (
            <div className={styles.legend} aria-hidden="true">
              {legendItems(model, payload.leagueAverage !== null).map((item) => (
                <span key={item.id} className={styles.legendItem}>
                  <span className={item.dashed ? styles.legendDashSwatch : styles.legendSwatch} style={{ backgroundColor: item.dashed ? undefined : item.color, borderColor: item.color }} />
                  <Text style="caption" color="tertiary">
                    {item.label}
                  </Text>
                </span>
              ))}
            </div>
          )}
          {footnoteLines(model, showsRawPoints).map((line) => (
            <Text key={line} as="p" style="caption" color="tertiary">
              {line}
            </Text>
          ))}
        </>
      )}
      <div className={styles.spacer} />
    </div>
  );
}

export default TrendChartWidgetView;
