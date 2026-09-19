/**
 * A cheap line/area sparkline — `ios/NBAStats/DesignSystem/Sparkline.swift`, ported.
 *
 * No axes, no scales beyond a single internal min/max, no chart engine: a small tile may hold
 * one of these per row, so this stays deliberately simple. Three cases the league's data forces
 * on every implementation are handled explicitly, exactly as the Swift original documents them:
 *
 *   - fewer than two readable points: a single dot, or nothing at all;
 *   - every value identical: a flat line through the middle, never a divide by zero;
 *   - `null` gaps: the line **breaks** ({@link RunPolyline}) — a missed game is not a zero and
 *     is not interpolated through, because either would invent a performance that never
 *     happened.
 *
 * Sized by its container: the `<svg>` uses a fixed internal `viewBox` and stretches to fill
 * whatever CSS box the caller gives it (`preserveAspectRatio="none"`), the same effect the Swift
 * original gets from `GeometryReader`.
 */
import { useId, type JSX } from "react";
import { RunPolyline, splitRuns } from "./RunPolyline";

export interface SparklineProps {
  readonly points: readonly (number | null)[];
  readonly tint: string;
  /** A dashed reference line — typically the league average for the same metric. */
  readonly baseline?: number | null;
  readonly showsArea?: boolean;
  readonly showsLastPoint?: boolean;
  readonly lineWidth?: number;
  readonly ariaLabel?: string;
  readonly className?: string;
}

const VIEW_WIDTH = 160;
const VIEW_HEIGHT = 40;

function derivedAccessibilityLabel(points: readonly (number | null)[]): string {
  const values = points.filter((value): value is number => value !== null && Number.isFinite(value));
  if (values.length === 0) return "Sparkline, no data";
  const first = values[0];
  const last = values[values.length - 1];
  const missing = points.length - values.length;
  const direction = Math.abs(last - first) <= 1e-9 ? "flat" : last > first ? "trending up" : "trending down";
  let text = `Sparkline over ${points.length} games, ${direction}, from ${first.toFixed(2)} to ${last.toFixed(2)}`;
  if (missing > 0) text += `, ${missing} with no value`;
  return text;
}

export function Sparkline({
  points,
  tint,
  baseline,
  showsArea = true,
  showsLastPoint = true,
  lineWidth = 1.5,
  ariaLabel,
  className,
}: SparklineProps): JSX.Element {
  const gradientId = useId();
  const values = points.filter((value): value is number => value !== null && Number.isFinite(value));
  const hasBaseline = baseline !== null && baseline !== undefined && Number.isFinite(baseline);

  let lower = values.length > 0 ? Math.min(...values) : 0;
  let upper = values.length > 0 ? Math.max(...values) : 1;
  if (hasBaseline) {
    lower = Math.min(lower, baseline);
    upper = Math.max(upper, baseline);
  }
  const inset = lineWidth / 2 + 1;
  const usableHeight = Math.max(VIEW_HEIGHT - inset * 2, 1);
  const range = upper - lower;
  const isFlat = range <= 1e-12;

  function y(value: number): number {
    if (isFlat) return VIEW_HEIGHT / 2;
    const fraction = (value - lower) / range;
    return inset + usableHeight * (1 - fraction);
  }

  function x(index: number): number {
    return points.length > 1 ? (VIEW_WIDTH * index) / (points.length - 1) : VIEW_WIDTH / 2;
  }

  const runPoints = points.map((value, index) => ({
    x: x(index),
    y: value !== null && Number.isFinite(value) ? y(value) : null,
  }));
  const runs = splitRuns(runPoints);
  const lastPoint = runs.length > 0 ? runs[runs.length - 1][runs[runs.length - 1].length - 1] : null;
  const baselineY = hasBaseline ? y(baseline) : null;

  return (
    <svg
      viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
      preserveAspectRatio="none"
      className={className}
      style={{ width: "100%", height: "100%", minHeight: 18 }}
      role="img"
      aria-label={ariaLabel ?? derivedAccessibilityLabel(points)}
    >
      {showsArea && (
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={tint} stopOpacity={0.28} />
            <stop offset="100%" stopColor={tint} stopOpacity={0.02} />
          </linearGradient>
        </defs>
      )}
      {showsArea &&
        runs
          .filter((run) => run.length > 1)
          .map((run, index) => {
            const first = run[0];
            const last = run[run.length - 1];
            const areaPoints = [
              `${first.x},${VIEW_HEIGHT}`,
              ...run.map((point) => `${point.x},${point.y as number}`),
              `${last.x},${VIEW_HEIGHT}`,
            ].join(" ");
            return <polygon key={index} points={areaPoints} fill={`url(#${gradientId})`} />;
          })}
      {baselineY !== null && (
        <line
          x1={0}
          x2={VIEW_WIDTH}
          y1={baselineY}
          y2={baselineY}
          stroke="var(--hw-text-tertiary)"
          strokeWidth={1}
          strokeDasharray="3 3"
        />
      )}
      <RunPolyline points={runPoints} stroke={tint} strokeWidth={lineWidth} dotRadius={lineWidth} />
      {showsLastPoint && lastPoint && (
        <circle cx={lastPoint.x} cy={lastPoint.y as number} r={lineWidth * 1.2} fill={tint} />
      )}
    </svg>
  );
}
