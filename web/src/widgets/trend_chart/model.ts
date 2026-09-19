/**
 * The chart model for `trend_chart` — ported from
 * `ios/NBAStats/Widgets/TrendChartWidget.swift`.
 *
 * Three decisions carried over verbatim from the Swift original (CONTRACT.md §7.7):
 *   - the **rolling average is the emphasized line** wherever a series has one at all — decided
 *     per series, not across all of them, so a subject with fewer games than the rolling window
 *     still draws its raw line rather than vanishing;
 *   - a game with no value **breaks the line** (`RunPolyline`'s null handling) instead of being
 *     interpolated through or plotted as zero;
 *   - a point whose `x` does not parse as a calendar date is **dropped and counted**, never
 *     plotted at "now".
 */
import type { MetricDomain, TrendChartPayload } from "../../api/types";
import { resolveYDomain } from "../../design/svg/CartesianFrame";
import { parseGameDate } from "../../design/format";

export const MAX_SERIES = 4;

export interface TrendPoint {
  readonly id: string;
  readonly epoch: number;
  readonly dateText: string;
  readonly raw: number | null;
  readonly rolling: number | null;
  readonly opponentAbbr: string | null;
}

export interface TrendSeriesModel {
  readonly id: string;
  readonly label: string;
  readonly colorIndex: number;
  readonly usesRolling: boolean;
  /** Every readable point, in order, `raw`/`rolling` possibly `null` — the shape `RunPolyline`
   * expects so a missing game breaks the drawn line rather than being interpolated through. */
  readonly points: readonly TrendPoint[];
  readonly latest: TrendPoint | null;
}

export interface TrendModel {
  readonly series: readonly TrendSeriesModel[];
  readonly xDomain: readonly [number, number];
  readonly yDomain: readonly [number, number];
  readonly droppedPoints: number;
  readonly usesRolling: boolean;
  readonly isEmpty: boolean;
}

function decodeDate(raw: string): number | null {
  const parsed = parseGameDate(raw);
  return parsed ? parsed.getTime() : null;
}

export function buildTrendModel(payload: TrendChartPayload): TrendModel {
  const seriesInput = payload.series.slice(0, MAX_SERIES);
  let droppedPoints = 0;
  const rawValues: number[] = [];
  const rollingValues: number[] = [];
  const epochs: number[] = [];

  const series: TrendSeriesModel[] = seriesInput.map((line, index) => {
    const usesRolling = line.points.some((point) => point.rolling !== null);
    const points: TrendPoint[] = [];
    line.points.forEach((point, pointIndex) => {
      const epoch = decodeDate(point.x);
      if (epoch === null) {
        droppedPoints += 1;
        return;
      }
      epochs.push(epoch);
      if (point.y !== null && Number.isFinite(point.y)) rawValues.push(point.y);
      if (point.rolling !== null && Number.isFinite(point.rolling)) rollingValues.push(point.rolling);
      points.push({
        id: `${line.id}-${pointIndex}-${point.gameId}`,
        epoch,
        dateText: point.x,
        raw: point.y,
        rolling: point.rolling,
        opponentAbbr: point.opponentAbbr,
      });
    });
    points.sort((a, b) => a.epoch - b.epoch);
    return {
      id: line.id,
      label: line.label.length > 0 ? line.label : `Series ${index + 1}`,
      colorIndex: line.colorIndex >= 0 ? line.colorIndex : index,
      usesRolling,
      points,
      latest: points.length > 0 ? points[points.length - 1] : null,
    };
  });

  const xDomain: readonly [number, number] = epochs.length > 0 ? [Math.min(...epochs), Math.max(...epochs)] : [0, 1];

  const mustInclude: number[] = [];
  if (payload.leagueAverage !== null && Number.isFinite(payload.leagueAverage)) mustInclude.push(payload.leagueAverage);
  const catalogDomain: MetricDomain | null = payload.metric.domain;
  const yDomain = resolveYDomain({
    serverDomain: payload.yDomain,
    values: [...rawValues, ...rollingValues, ...mustInclude],
    catalogDomain,
  });

  return {
    series,
    xDomain,
    yDomain,
    droppedPoints,
    usesRolling: series.some((line) => line.usesRolling),
    isEmpty: series.every((line) => line.points.length === 0),
  };
}

/** ~`count` evenly spaced tick indices into a sorted array of length `length`, always including
 * the first and last element when there is room. */
export function thinnedIndices(length: number, count: number): readonly number[] {
  if (length <= 0) return [];
  if (length <= count) return Array.from({ length }, (_, index) => index);
  const step = (length - 1) / (count - 1);
  const seen = new Set<number>();
  for (let index = 0; index < count; index += 1) seen.add(Math.round(index * step));
  return Array.from(seen).sort((a, b) => a - b);
}
