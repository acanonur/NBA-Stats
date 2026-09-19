/**
 * A radar/spider chart over a fixed set of axes — WEB_DESIGN.md §7.4 item 15, for `comparison`'s
 * `style: "radar"` (WEB_DESIGN.md §7.7: the percentile axis is all-or-nothing, so by the time a
 * series reaches this component either every axis has a value or the widget has already demoted
 * the whole chart to a row-relative bar/table view — but this primitive still handles a missing
 * vertex correctly on its own, rather than assuming a caller always gets that right).
 *
 * **A missing vertex breaks the outline rather than collapsing it to centre.** A radar chart's
 * usual convention for a missing value is to plot it at 0 (the centre), which silently makes an
 * absent statistic look like the worst possible one — precisely the failure mode the em-dash
 * rule exists to prevent everywhere else in this app. This component instead treats the ring of
 * vertices the same way {@link RunPolyline} treats a line: a `null` breaks the run, so a gap in
 * the data is a visible gap in the shape, never a point dragged to the centre.
 *
 * The closing seam between the last axis and the first is handled with the same trick
 * `career_arc`'s dash-style boundary uses (WEB_DESIGN.md §7.7): the first vertex is duplicated at
 * the end of the sequence before splitting into runs, so two fully-populated series still close
 * into a single seamless loop, while a series with any gap never draws a false seam across it.
 */
import type { JSX } from "react";
import { splitRuns, runToPolylinePoints } from "./RunPolyline";

export interface RadarAxis {
  readonly key: string;
  readonly label: string;
}

export interface RadarSeries {
  readonly id: string;
  readonly color: string;
  /** One entry per {@link RadarAxis}, already normalized to `[0, 1]` (a percentile, or a
   * row-relative raw scale the caller computed) — this component does no scaling of its own.
   * `null` at an axis means that value does not exist for this series. */
  readonly values: readonly (number | null)[];
  readonly fillOpacity?: number;
}

export interface RadarPathProps {
  readonly axes: readonly RadarAxis[];
  readonly series: readonly RadarSeries[];
  readonly size: number;
  readonly className?: string;
}

const RING_FRACTIONS = [0.25, 0.5, 0.75, 1];

function vertex(center: number, radius: number, angle: number, value: number): { x: number; y: number } {
  return {
    x: center + radius * value * Math.cos(angle),
    y: center + radius * value * Math.sin(angle),
  };
}

export function RadarPath({ axes, series, size, className }: RadarPathProps): JSX.Element {
  const center = size / 2;
  const labelMargin = 14;
  const maxRadius = Math.max(center - labelMargin, 1);
  const angleStep = axes.length > 0 ? (2 * Math.PI) / axes.length : 0;
  const angleAt = (index: number): number => -Math.PI / 2 + index * angleStep;

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className={className} role="presentation">
      <g className="hw-radar-grid">
        {RING_FRACTIONS.map((fraction) => (
          <circle
            key={fraction}
            cx={center}
            cy={center}
            r={maxRadius * fraction}
            fill="none"
            stroke="var(--hw-separator)"
            strokeWidth={1}
          />
        ))}
        {axes.map((axis, index) => {
          const angle = angleAt(index);
          const edge = vertex(center, maxRadius, angle, 1);
          const labelPoint = vertex(center, maxRadius + labelMargin - 4, angle, 1);
          return (
            <g key={axis.key}>
              <line x1={center} y1={center} x2={edge.x} y2={edge.y} stroke="var(--hw-separator)" strokeWidth={1} />
              <text
                x={labelPoint.x}
                y={labelPoint.y}
                fontSize={9}
                fill="var(--hw-text-tertiary)"
                textAnchor="middle"
                dominantBaseline="middle"
              >
                {axis.label}
              </text>
            </g>
          );
        })}
      </g>
      {series.map((line) => {
        // Duplicate the first axis's value at the end, so a fully-populated series closes into
        // one seamless loop and a series with a gap never draws a false seam across it.
        const extendedValues = [...line.values, line.values[0]];
        const points = extendedValues.map((value, index) => {
          const axisIndex = index % axes.length;
          if (value === null || !Number.isFinite(value)) return { x: axisIndex, y: null };
          const point = vertex(center, maxRadius, angleAt(axisIndex), value);
          return { x: point.x, y: point.y };
        });
        const runs = splitRuns(points);
        const isFullLoop = runs.length === 1 && runs[0].length === points.length;

        return (
          <g key={line.id}>
            {isFullLoop && (
              <polygon
                points={runToPolylinePoints(runs[0])}
                fill={line.color}
                fillOpacity={line.fillOpacity ?? 0.18}
                stroke="none"
              />
            )}
            {runs.map((run, runIndex) =>
              run.length > 1 ? (
                <polyline
                  key={runIndex}
                  points={runToPolylinePoints(run)}
                  fill="none"
                  stroke={line.color}
                  strokeWidth={1.75}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              ) : (
                <circle key={runIndex} cx={run[0].x} cy={run[0].y as number} r={2.5} fill={line.color} />
              ),
            )}
          </g>
        );
      })}
    </svg>
  );
}
