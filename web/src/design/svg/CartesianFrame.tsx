/**
 * Ticks, gridlines, and the clip rect a chart's data marks are drawn inside —
 * WEB_DESIGN.md §7.4 item 11. Every chart primitive in this package (`LineChart`, `GroupedBars`,
 * `ScatterPlot`) is built on top of this rather than each drawing its own axis furniture, so a
 * gridline's colour, weight, and label typography have exactly one definition.
 */
import type { JSX, ReactNode } from "react";

export interface CartesianTick {
  /** A fraction in `[0, 1]` along the axis — the caller has already resolved a data value into
   * plot-space, matching how {@link RunPoint} coordinates are pre-resolved. */
  readonly position: number;
  readonly label: string;
}

export interface CartesianFrameProps {
  readonly width: number;
  readonly height: number;
  /** Horizontal gridlines, each with a left-edge label. Omit for a chart with no meaningful
   * y-axis rule (a sparkline, which draws its own single dashed baseline instead). */
  readonly yTicks?: readonly CartesianTick[];
  /** Bottom-edge labels; ticks are not usually a chart's whole x-axis is what the caller's data
   * marks (season labels under a line chart's own points) — this only draws the labels. */
  readonly xTicks?: readonly CartesianTick[];
  /** A caller-unique id for this frame's `<clipPath>` — required because SVG ids are
   * document-global, and two frames on the same dashboard must not clip each other. */
  readonly clipId: string;
  readonly className?: string;
  /** The data marks (a `RunPolyline`, bars, points, …), rendered inside the clip. */
  readonly children?: ReactNode;
}

/** The plot area's usable height, after leaving room for the x-axis labels below it — a small,
 * fixed reservation so every chart in this package agrees on where the axis line sits. */
export const X_AXIS_LABEL_HEIGHT = 16;

export function CartesianFrame({
  width,
  height,
  yTicks = [],
  xTicks = [],
  clipId,
  className,
  children,
}: CartesianFrameProps): JSX.Element {
  const plotHeight = xTicks.length > 0 ? Math.max(height - X_AXIS_LABEL_HEIGHT, 1) : height;

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className={className} role="presentation">
      <defs>
        <clipPath id={clipId}>
          <rect x={0} y={0} width={width} height={plotHeight} />
        </clipPath>
      </defs>
      <g className="hw-cartesian-grid">
        {yTicks.map((tick) => {
          const y = plotHeight * (1 - tick.position);
          return (
            <g key={tick.label + tick.position}>
              <line
                x1={0}
                x2={width}
                y1={y}
                y2={y}
                stroke="var(--hw-separator)"
                strokeWidth={1}
                shapeRendering="crispEdges"
              />
              <text x={2} y={y - 2} fontSize={10} fill="var(--hw-text-tertiary)">
                {tick.label}
              </text>
            </g>
          );
        })}
      </g>
      <g clipPath={`url(#${clipId})`}>{children}</g>
      {xTicks.length > 0 && (
        <g className="hw-cartesian-x-labels">
          {xTicks.map((tick) => (
            <text
              key={tick.label + tick.position}
              x={width * tick.position}
              y={height - 4}
              fontSize={10}
              fill="var(--hw-text-tertiary)"
              textAnchor="middle"
            >
              {tick.label}
            </text>
          ))}
        </g>
      )}
    </svg>
  );
}

export interface DomainLike {
  readonly min: number;
  readonly max: number;
}

/**
 * The documented y-domain ladder (WEB_DESIGN.md §7.4 item 11): a server-published `yDomain` wins
 * outright; otherwise the plotted values themselves, padded 8% on each side; otherwise the
 * metric's catalog `domain`; otherwise `[0, 1]` so a chart with genuinely nothing to go on still
 * renders a sane axis rather than a divide-by-zero.
 */
export function resolveYDomain(options: {
  readonly serverDomain?: DomainLike | null;
  readonly values: readonly (number | null | undefined)[];
  readonly catalogDomain?: DomainLike | null;
}): readonly [number, number] {
  const { serverDomain, values, catalogDomain } = options;
  if (serverDomain) return [serverDomain.min, serverDomain.max];

  const finite = values.filter(
    (value): value is number => value !== null && value !== undefined && Number.isFinite(value),
  );
  if (finite.length > 0) {
    const min = Math.min(...finite);
    const max = Math.max(...finite);
    const span = max - min;
    const padding = span > 1e-9 ? span * 0.08 : Math.max(Math.abs(max) * 0.08, 0.5);
    return [min - padding, max + padding];
  }

  if (catalogDomain) return [catalogDomain.min, catalogDomain.max];
  return [0, 1];
}

/** Maps a value onto `[0, 1]` within `[domain[0], domain[1]]`, clamped — the one place every
 * chart in this package turns a data value into plot-space. */
export function normalizeInDomain(value: number, domain: readonly [number, number]): number {
  const span = domain[1] - domain[0];
  if (span <= 1e-12 || !Number.isFinite(value)) return 0.5;
  return Math.min(Math.max((value - domain[0]) / span, 0), 1);
}
