/**
 * The single shared "split a series into unbroken runs at nulls" helper — used by
 * {@link ../Sparkline | Sparkline}, `trend_chart`, and `career_arc` (WEB_DESIGN.md §7.4 item 10).
 *
 * A `null` in a series **breaks the line**; it is never interpolated through, and it is never
 * plotted as zero. This is the one behaviour every general-purpose chart library gets wrong by
 * default — Recharts, visx, nivo, d3's own line generator all draw straight through a gap unless
 * told otherwise, and this codebase ships no chart library at all (WEB_DESIGN.md §7.9) precisely
 * because that default is incompatible with a data model where a missing game must never look
 * like a measured one.
 *
 * An isolated point — a single non-null value between two gaps — is a run of length one, drawn
 * as a dot rather than a line with nothing to connect to. A series where every value is equal is
 * *not* a special case here: it is the caller's y-scale that collapses to a flat line (see
 * `CartesianFrame.ts`'s `resolveYDomain`), not this module's job to detect.
 */
import type { JSX } from "react";

export interface RunPoint {
  readonly x: number;
  /** `null` breaks the run at this point. */
  readonly y: number | null;
}

/**
 * Splits `points` into arrays of consecutive elements with a non-null `y`. A run of length one
 * means "one measured point with a gap on both sides" — a legitimate, meaningful shape, not an
 * edge case to special-case away.
 */
export function splitRuns<T extends RunPoint>(points: readonly T[]): (readonly T[])[] {
  const runs: T[][] = [];
  let current: T[] = [];
  for (const point of points) {
    if (point.y !== null && Number.isFinite(point.y)) {
      current.push(point);
    } else if (current.length > 0) {
      runs.push(current);
      current = [];
    }
  }
  if (current.length > 0) runs.push(current);
  return runs;
}

/** Builds an SVG polyline `points` attribute value from one run — every element must already
 * have a non-null `y` (the shape {@link splitRuns} guarantees for anything it returns). */
export function runToPolylinePoints(run: readonly RunPoint[]): string {
  return run.map((point) => `${point.x},${point.y as number}`).join(" ");
}

export interface RunPolylineProps {
  /** Already resolved into SVG coordinate space (pixels within the enclosing `<svg>`'s
   * `viewBox`) — this component does no scaling of its own; `CartesianFrame`/`Sparkline` own
   * that so the same points feed both the line and any gridlines drawn against them. */
  readonly points: readonly RunPoint[];
  readonly stroke: string;
  readonly strokeWidth?: number;
  /** The radius of the dot drawn for an isolated (run-of-one) point, and for every point when
   * `showsDots` is set. */
  readonly dotRadius?: number;
  readonly showsDots?: boolean;
  readonly className?: string;
}

/** Renders the runs `splitRuns` computes: a stroked polyline per run of two-or-more points, a
 * dot per run of exactly one. Nothing is drawn for an empty series. */
export function RunPolyline({
  points,
  stroke,
  strokeWidth = 1.5,
  dotRadius,
  showsDots = false,
  className,
}: RunPolylineProps): JSX.Element {
  const runs = splitRuns(points);
  const resolvedDotRadius = dotRadius ?? strokeWidth;
  return (
    <g className={className}>
      {runs.map((run, index) =>
        run.length > 1 ? (
          <polyline
            key={index}
            points={runToPolylinePoints(run)}
            fill="none"
            stroke={stroke}
            strokeWidth={strokeWidth}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        ) : (
          <circle key={index} cx={run[0].x} cy={run[0].y as number} r={resolvedDotRadius} fill={stroke} />
        ),
      )}
      {showsDots &&
        runs.flatMap((run) =>
          run.length > 1
            ? run.map((point, pointIndex) => (
                <circle
                  key={`dot-${point.x}-${pointIndex}`}
                  cx={point.x}
                  cy={point.y as number}
                  r={resolvedDotRadius}
                  fill={stroke}
                />
              ))
            : [],
        )}
    </g>
  );
}
