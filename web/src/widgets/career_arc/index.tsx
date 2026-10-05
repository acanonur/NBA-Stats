/**
 * `career_arc` — one metric across a whole career, by season or by age. Ported from
 * `ios/NBAStats/Widgets/CareerArcWidget.swift`: an `estimated` season is drawn dashed with a
 * diamond marker, `full` solid with a circle, `partial` a triangle — never silently blended into
 * a smooth line that would hide exactly the seams the era model exists to show — and a season the
 * league has no number for is left out of the arc entirely and only ever explained in the
 * footnote, never plotted as zero.
 */
import { useId, type JSX } from "react";
import type { WidgetViewProps } from "../../generated/registry";
import type { WidgetSizeKey } from "../../generated/tokens";
import type { MetricFormat } from "../../generated/contracts";
import { Text } from "../../design/Text";
import { PlayerAvatar } from "../../design/PlayerAvatar";
import { ErrorTile } from "../../design/StateViews";
import { CartesianFrame, X_AXIS_LABEL_HEIGHT, normalizeInDomain } from "../../design/svg/CartesianFrame";
import type { CartesianTick } from "../../design/svg/CartesianFrame";
import { RunPolyline } from "../../design/svg/RunPolyline";
import { chartColorVar } from "../../design/valueColor";
import { formatValue } from "../../design/format";
import { decodeCareerArcPayload } from "./payload";
import type { DecodedCareerArcPayload } from "./payload";
import { buildArcModel, markerShapeFor, seasonLabelForStartYear, thinnedXTicks } from "./model";
import type { ArcMarkerShape, ArcModel } from "./model";
import { useChartWidth } from "../../design/useChartWidth";
import styles from "./index.module.css";

const REGULAR_COLOR_INDEX = 0;
const PLAYOFF_COLOR_INDEX = 3;

const CHART_HEIGHT: Readonly<Record<WidgetSizeKey, number>> = { small: 112, medium: 158, large: 224 };

function evenTicks(domain: readonly [number, number], count: number): readonly number[] {
  const [low, high] = domain;
  if (!Number.isFinite(low) || !Number.isFinite(high) || high <= low || count < 2) return [low];
  const step = (high - low) / (count - 1);
  return Array.from({ length: count }, (_, index) => low + step * index);
}

function runColor(isPlayoffs: boolean, isEstimated: boolean): string {
  if (isEstimated) return "var(--hw-warning)";
  return chartColorVar(isPlayoffs ? PLAYOFF_COLOR_INDEX : REGULAR_COLOR_INDEX);
}

function Marker({ shape, cx, cy, r, fill }: { readonly shape: ArcMarkerShape; readonly cx: number; readonly cy: number; readonly r: number; readonly fill: string }): JSX.Element {
  if (shape === "diamond") {
    return <polygon points={`${cx},${cy - r} ${cx + r},${cy} ${cx},${cy + r} ${cx - r},${cy}`} fill={fill} />;
  }
  if (shape === "triangle") {
    return <polygon points={`${cx},${cy - r} ${cx + r * 1.1},${cy + r} ${cx - r * 1.1},${cy + r}`} fill={fill} />;
  }
  return <circle cx={cx} cy={cy} r={r} fill={fill} />;
}

function ArcChart({ model, format, width, height, showsEraLabels }: { readonly model: ArcModel; readonly format: MetricFormat; readonly width: number; readonly height: number; readonly showsEraLabels: boolean }): JSX.Element {
  const clipId = useId();
  const xTicksRaw = thinnedXTicks(model.xDomain, 5);
  const plotHeight = Math.max(height - X_AXIS_LABEL_HEIGHT, 1);
  const xTickLabel = (value: number): string => (model.usesAgeAxis ? String(value) : seasonLabelForStartYear(value));
  const xTicks: readonly CartesianTick[] = xTicksRaw.map((value) => ({
    position: normalizeInDomain(value, model.xDomain),
    label: xTickLabel(value),
  }));
  const yTicks: readonly CartesianTick[] = evenTicks(model.yDomain, 4).map((value) => ({
    position: normalizeInDomain(value, model.yDomain),
    label: formatValue(value, format),
  }));

  const toX = (x: number): number => normalizeInDomain(x, model.xDomain) * width;
  const toY = (value: number): number => plotHeight * (1 - normalizeInDomain(value, model.yDomain));

  return (
    <CartesianFrame width={width} height={height} xTicks={xTicks} yTicks={yTicks} clipId={clipId}>
      {model.eraMarks.map((mark) => {
        const x = toX(mark.x);
        return (
          <g key={mark.season}>
            <line x1={x} x2={x} y1={0} y2={plotHeight} stroke="var(--hw-text-tertiary)" strokeWidth={1} strokeDasharray="3 3" opacity={0.8} />
            {showsEraLabels && (
              <text x={x + 3} y={10} fontSize={9} fill="var(--hw-text-tertiary)">
                {mark.label}
              </text>
            )}
          </g>
        );
      })}
      {model.runs.map((run) => {
        const points = run.points.map((point) => ({ x: toX(point.x), y: toY(point.value) }));
        const color = runColor(run.isPlayoffs, run.isEstimated);
        return (
          <g key={run.id} strokeDasharray={run.isEstimated ? "5 4" : undefined}>
            <RunPolyline points={points} stroke={color} strokeWidth={run.isPlayoffs ? 1.6 : 2.2} />
          </g>
        );
      })}
      {model.allPoints.map((point) => (
        <Marker
          key={point.id}
          shape={markerShapeFor(point.availability)}
          cx={toX(point.x)}
          cy={toY(point.value)}
          r={point.isPlayoffs ? 2.6 : 3.2}
          fill={runColor(point.isPlayoffs, point.availability === "estimated")}
        />
      ))}
      {model.peak && (
        <g>
          <circle cx={toX(model.peak.x)} cy={toY(model.peak.value)} r={4} fill="var(--hw-selection)" />
          <text x={toX(model.peak.x)} y={Math.max(toY(model.peak.value) - 8, 10)} fontSize={10} fill="var(--hw-selection)" textAnchor="middle">
            {model.peak.label}
          </text>
        </g>
      )}
    </CartesianFrame>
  );
}

function legendItems(model: ArcModel): readonly { readonly id: string; readonly label: string; readonly color: string; readonly dashed: boolean }[] {
  const items = [{ id: "regular", label: "Regular season", color: chartColorVar(REGULAR_COLOR_INDEX), dashed: false }];
  if (model.playoffs.length > 0) items.push({ id: "playoffs", label: "Playoffs", color: chartColorVar(PLAYOFF_COLOR_INDEX), dashed: false });
  if (model.estimatedCount > 0) items.push({ id: "estimated", label: "Estimated", color: "var(--hw-warning)", dashed: true });
  if (model.eraMarks.length > 0) items.push({ id: "era", label: "Era boundary", color: "var(--hw-text-tertiary)", dashed: true });
  return items;
}

function footnoteLines(model: ArcModel, metricName: string, showsEraLabels: boolean): readonly string[] {
  const lines: string[] = [];
  if (model.estimatedCount > 0) {
    lines.push(`${model.estimatedCount} season${model.estimatedCount === 1 ? " is" : "s are"} estimated from the box score rather than measured — dashed line, diamond markers.`);
  }
  if (model.partialCount > 0) {
    lines.push(`${model.partialCount} season${model.partialCount === 1 ? " is" : "s are"} built from an incomplete set of games — triangle markers.`);
  }
  if (model.missingCount > 0) {
    lines.push(`${model.missingCount} season${model.missingCount === 1 ? " has" : "s have"} no ${metricName} in the league's record and ${model.missingCount === 1 ? "is" : "are"} left out rather than drawn as zero.`);
  }
  if (!showsEraLabels && model.eraMarks.length > 0) {
    lines.push(`Dashed rules: ${model.eraMarks.map((mark) => `${mark.season} ${mark.label}`).join(" · ")}`);
  }
  return lines;
}

function accessibleChartLabel(payload: DecodedCareerArcPayload, model: ArcModel): string {
  let text = `${payload.metric.name} by ${model.usesAgeAxis ? "age" : "season"} for ${payload.player.name}`;
  const first = model.regular[0];
  const last = model.regular[model.regular.length - 1];
  if (first && last) text += `, from ${first.displayValue} in ${first.seasonLabel} to ${last.displayValue} in ${last.seasonLabel}`;
  if (model.peak) text += `, peaking at ${model.peak.label}`;
  if (model.estimatedCount > 0) text += `. ${model.estimatedCount} season${model.estimatedCount === 1 ? " is" : "s are"} estimated, not measured`;
  return text;
}

function CareerArcWidgetView({ size, payload: rawPayload }: WidgetViewProps): JSX.Element {
  // Called unconditionally, before the decode's early return, so this hook runs on every render
  // in the same order (react-hooks/rules-of-hooks) — a decode failure still needs a stable hook
  // count across renders even though its result goes unused on that path.
  const [containerRef, width] = useChartWidth(320);

  let payload: DecodedCareerArcPayload;
  try {
    payload = decodeCareerArcPayload(rawPayload);
  } catch {
    return <ErrorTile size={size} isRetryable={false} message="This career arc's data did not match what the app expected." />;
  }

  const model = buildArcModel(payload);
  const height = CHART_HEIGHT[size] ?? CHART_HEIGHT.medium;
  const showsEraLabels = size === "large";
  // `MetricDescriptor.format` is a bare `string` on `api/types.ts` (transcribed verbatim from
  // `contracts/metrics.json`, not re-validated at the type level) — see `StatValue.tsx`'s own
  // comment on this same cast.
  const format = payload.metric.format as MetricFormat;

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        <PlayerAvatar player={payload.player} size="small" />
        <Text style="statLabel" truncate>
          {payload.player.name}
        </Text>
        <Text style="caption">{payload.metric.shortName}</Text>
        <span className={styles.headerSpacer} />
        <Text style="caption">by {model.usesAgeAxis ? "age" : "season"}</Text>
      </div>
      {model.isEmpty ? (
        <Text as="p" style="caption" className={styles.emptyNote}>
          No season of this career has a {payload.metric.name}. {payload.metric.name} goes back to {payload.metric.availability.seasonFrom}.
        </Text>
      ) : (
        <>
          <div ref={containerRef} className={styles.chartWrap} role="img" aria-label={accessibleChartLabel(payload, model)}>
            <ArcChart model={model} format={format} width={width} height={height} showsEraLabels={showsEraLabels} />
          </div>
          <div className={styles.legend} aria-hidden="true">
            {legendItems(model).map((item) => (
              <span key={item.id} className={styles.legendItem}>
                <span className={item.dashed ? styles.legendDashSwatch : styles.legendSwatch} style={{ backgroundColor: item.dashed ? undefined : item.color, borderColor: item.color }} />
                <Text style="caption" color="tertiary">
                  {item.label}
                </Text>
              </span>
            ))}
          </div>
          {footnoteLines(model, payload.metric.name, showsEraLabels).map((line) => (
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

export default CareerArcWidgetView;
