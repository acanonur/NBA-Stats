/**
 * A scatter plot over two independent domains — WEB_DESIGN.md §7.4 item 15, built for
 * `team_efficiency`'s ORtg-vs-DRtg chart. `team_efficiency` plots ORtg against **negated** DRtg
 * so "up and to the right" always means "better", captioning the inversion and handing this
 * component already-negated `y` values with tick *labels* it negates back — this component does
 * not know or care that any negation happened; it only draws points against the domains and
 * ticks it is given, which is what keeps that axis-inversion trick entirely in the widget layer.
 */
import type { JSX } from "react";
import { useId } from "react";
import { CartesianFrame, normalizeInDomain } from "./CartesianFrame";
import type { CartesianTick } from "./CartesianFrame";

export interface ScatterPoint {
  readonly id: string;
  readonly x: number;
  readonly y: number;
  readonly label?: string;
  readonly color?: string;
  readonly radius?: number;
}

export interface ScatterCrosshair {
  readonly x?: number;
  readonly y?: number;
}

export interface ScatterPlotProps {
  readonly points: readonly ScatterPoint[];
  readonly xDomain: readonly [number, number];
  readonly yDomain: readonly [number, number];
  readonly width: number;
  readonly height: number;
  /** Dashed reference lines — typically the league averages on each axis. */
  readonly crosshair?: ScatterCrosshair;
  readonly xTicks?: readonly CartesianTick[];
  readonly yTicks?: readonly CartesianTick[];
  readonly className?: string;
}

export function ScatterPlot({
  points,
  xDomain,
  yDomain,
  width,
  height,
  crosshair,
  xTicks = [],
  yTicks = [],
  className,
}: ScatterPlotProps): JSX.Element {
  const clipId = useId();
  const crosshairX =
    crosshair?.x !== undefined && Number.isFinite(crosshair.x) ? normalizeInDomain(crosshair.x, xDomain) * width : null;
  const crosshairY =
    crosshair?.y !== undefined && Number.isFinite(crosshair.y)
      ? height * (1 - normalizeInDomain(crosshair.y, yDomain))
      : null;

  return (
    <CartesianFrame width={width} height={height} xTicks={xTicks} yTicks={yTicks} clipId={clipId} className={className}>
      {crosshairX !== null && (
        <line
          x1={crosshairX}
          x2={crosshairX}
          y1={0}
          y2={height}
          stroke="var(--hw-text-tertiary)"
          strokeWidth={1}
          strokeDasharray="3 3"
        />
      )}
      {crosshairY !== null && (
        <line
          x1={0}
          x2={width}
          y1={crosshairY}
          y2={crosshairY}
          stroke="var(--hw-text-tertiary)"
          strokeWidth={1}
          strokeDasharray="3 3"
        />
      )}
      {points.map((point) => {
        const cx = normalizeInDomain(point.x, xDomain) * width;
        const cy = height * (1 - normalizeInDomain(point.y, yDomain));
        return (
          <g key={point.id}>
            <circle cx={cx} cy={cy} r={point.radius ?? 4} fill={point.color ?? "var(--hw-selection)"} />
            {point.label && (
              <text x={cx + 6} y={cy - 6} fontSize={10} fill="var(--hw-text-secondary)">
                {point.label}
              </text>
            )}
          </g>
        );
      })}
    </CartesianFrame>
  );
}
