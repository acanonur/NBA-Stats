/**
 * A multi-series line chart over a shared y-domain — built on {@link CartesianFrame} and
 * {@link RunPolyline}, for `trend_chart` and `career_arc`. Every series' nulls break its own
 * line independently; a gap in one series never affects another's.
 */
import type { JSX } from "react";
import { useId } from "react";
import { CartesianFrame, X_AXIS_LABEL_HEIGHT, normalizeInDomain } from "./CartesianFrame";
import type { CartesianTick } from "./CartesianFrame";
import { RunPolyline } from "./RunPolyline";

export interface LineChartPoint {
  /** A fraction in `[0, 1]` along the x-axis — the caller decides what that axis is (game
   * index, season index, age), so this component stays agnostic to it. */
  readonly x: number;
  /** The raw metric value; `null` breaks the line at this point. */
  readonly y: number | null;
}

export interface LineChartSeries {
  readonly id: string;
  readonly color: string;
  readonly points: readonly LineChartPoint[];
  readonly strokeWidth?: number;
  readonly opacity?: number;
  readonly showsDots?: boolean;
  readonly dashed?: boolean;
}

export interface LineChartProps {
  readonly width: number;
  readonly height: number;
  readonly series: readonly LineChartSeries[];
  readonly yDomain: readonly [number, number];
  readonly yTicks?: readonly CartesianTick[];
  readonly xTicks?: readonly CartesianTick[];
  readonly className?: string;
}

export function LineChart({
  width,
  height,
  series,
  yDomain,
  yTicks = [],
  xTicks = [],
  className,
}: LineChartProps): JSX.Element {
  const clipId = useId();
  const plotHeight = xTicks.length > 0 ? Math.max(height - X_AXIS_LABEL_HEIGHT, 1) : height;

  return (
    <CartesianFrame width={width} height={height} yTicks={yTicks} xTicks={xTicks} clipId={clipId} className={className}>
      {series.map((line) => {
        const points = line.points.map((point) => ({
          x: point.x * width,
          y: point.y === null || !Number.isFinite(point.y) ? null : plotHeight * (1 - normalizeInDomain(point.y, yDomain)),
        }));
        return (
          <g key={line.id} opacity={line.opacity} strokeDasharray={line.dashed ? "4 3" : undefined}>
            <RunPolyline
              points={points}
              stroke={line.color}
              strokeWidth={line.strokeWidth ?? 1.5}
              showsDots={line.showsDots}
              dotRadius={(line.strokeWidth ?? 1.5) * 1.4}
            />
          </g>
        );
      })}
    </CartesianFrame>
  );
}
