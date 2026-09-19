/**
 * The chart model for `career_arc` — ported from
 * `ios/NBAStats/Widgets/CareerArcWidget.swift`'s `Model`/`makeModel`.
 *
 * A season whose value the league never recorded is **omitted** from the payload's `seasons[]`
 * entirely (the opposite convention from `shot_profile`'s always-5 zones — CONTRACT.md §7.7), so
 * a "missing" season here means a gap in the *x* positions, not a `null` in an evenly-spaced
 * array. That is why this model splits the drawn line into runs by **x-distance** (a gap over 1.5
 * x-units) rather than by `RunPolyline`'s usual null-based splitting — there are no nulls to
 * split on. A run also restarts, without a gap, at the exact point an `estimated` season becomes
 * `full` or vice versa, with the shared boundary point duplicated in both runs (once solid, once
 * dashed) so the two strokes visually meet.
 */
import type { Availability } from "../../api/types";
import { resolveYDomain } from "../../design/svg/CartesianFrame";
import type { DomainLike } from "../../design/svg/CartesianFrame";
import { seasonStartYear } from "../../design/format";
import type { DecodedCareerArcPayload } from "./payload";

export type ArcMarkerShape = "circle" | "diamond" | "triangle";

export interface ArcPoint {
  readonly id: string;
  readonly x: number;
  readonly value: number;
  readonly seasonLabel: string;
  readonly startYear: number | null;
  readonly age: number | null;
  readonly displayValue: string;
  readonly availability: Availability;
  readonly isPlayoffs: boolean;
}

export interface ArcRun {
  readonly id: string;
  readonly points: readonly ArcPoint[];
  readonly isEstimated: boolean;
  readonly isPlayoffs: boolean;
}

export interface EraMark {
  readonly season: string;
  readonly label: string;
  readonly detail: string | null;
  readonly x: number;
}

export interface PeakMark {
  readonly x: number;
  readonly value: number;
  readonly label: string;
}

export interface ArcModel {
  readonly regular: readonly ArcPoint[];
  readonly playoffs: readonly ArcPoint[];
  readonly allPoints: readonly ArcPoint[];
  readonly runs: readonly ArcRun[];
  readonly eraMarks: readonly EraMark[];
  readonly peak: PeakMark | null;
  readonly xDomain: readonly [number, number];
  readonly yDomain: readonly [number, number];
  readonly usesAgeAxis: boolean;
  readonly estimatedCount: number;
  readonly partialCount: number;
  readonly missingCount: number;
  readonly isEmpty: boolean;
}

export function markerShapeFor(availability: Availability): ArcMarkerShape {
  switch (availability) {
    case "estimated":
      return "diamond";
    case "partial":
      return "triangle";
    case "full":
    case "unavailable":
      return "circle";
  }
}

interface Anchor {
  readonly year: number;
  readonly age: number;
}

function findAnchor(points: readonly ArcPoint[]): Anchor | null {
  for (const point of points) {
    if (point.startYear !== null && point.age !== null) return { year: point.startYear, age: point.age };
  }
  return null;
}

function positionForSeason(season: string, usesAgeAxis: boolean, anchor: Anchor | null): number | null {
  const year = seasonStartYear(season);
  if (year === null) return null;
  if (!usesAgeAxis) return year;
  if (!anchor) return null;
  return anchor.age + (year - anchor.year);
}

/** `1996` becomes `"1996-97"`; `1999` becomes `"1999-00"`. */
export function seasonLabelForStartYear(year: number): string {
  const next = ((year + 1) % 100 + 100) % 100;
  return `${year}-${String(next).padStart(2, "0")}`;
}

function buildPoints(
  seasons: DecodedCareerArcPayload["seasons"],
  isPlayoffs: boolean,
  usesAgeAxis: boolean,
  countMissing: (n: number) => void,
): readonly ArcPoint[] {
  const out: ArcPoint[] = [];
  seasons.forEach((season, index) => {
    if (season.value === null || !Number.isFinite(season.value) || season.availability === "unavailable") {
      if (!isPlayoffs) countMissing(1);
      return;
    }
    const startYear = seasonStartYear(season.season);
    const x = usesAgeAxis ? season.age : startYear;
    if (x === null) {
      if (!isPlayoffs) countMissing(1);
      return;
    }
    out.push({
      id: `${isPlayoffs ? "po" : "rs"}-${season.season}-${season.seasonType}-${index}`,
      x,
      value: season.value,
      seasonLabel: season.season,
      startYear,
      age: season.age,
      displayValue: season.displayValue,
      availability: season.availability,
      isPlayoffs,
    });
  });
  return [...out].sort((a, b) => a.x - b.x);
}

const GAP_THRESHOLD = 1.5;

function buildRuns(points: readonly ArcPoint[], prefix: string): readonly ArcRun[] {
  if (points.length === 0) return [];
  const runs: ArcRun[] = [];
  let current: ArcPoint[] = [points[0]];
  let currentIsEstimated = points[0].availability === "estimated";
  let runIndex = 0;

  const flush = (): void => {
    runs.push({ id: `${prefix}#${runIndex}`, points: current, isEstimated: currentIsEstimated, isPlayoffs: current[0].isPlayoffs });
    runIndex += 1;
  };

  for (let index = 1; index < points.length; index += 1) {
    const previous = points[index - 1];
    const point = points[index];
    const pointIsEstimated = point.availability === "estimated";
    if (point.x - previous.x > GAP_THRESHOLD) {
      flush();
      current = [point];
      currentIsEstimated = pointIsEstimated;
    } else if (pointIsEstimated !== currentIsEstimated) {
      flush();
      // The joining point is repeated at the head of the new run, in the new run's style, so the
      // two strokes meet instead of leaving a hole (Swift's `lineMarks`, ported verbatim).
      current = [previous, point];
      currentIsEstimated = pointIsEstimated;
    } else {
      current.push(point);
    }
  }
  flush();
  return runs;
}

export function buildArcModel(payload: DecodedCareerArcPayload): ArcModel {
  const wantsAge = payload.xAxis === "age";
  const hasAges = [...payload.seasons, ...payload.playoffSeasons].some((season) => season.age !== null);
  const usesAgeAxis = wantsAge && hasAges;

  let missingCount = 0;
  const regular = buildPoints(payload.seasons, false, usesAgeAxis, (n) => {
    missingCount += n;
  });
  const playoffs = buildPoints(payload.playoffSeasons, true, usesAgeAxis, () => {
    /* playoff misses are not counted — CONTRACT.md/Swift only count the primary arc */
  });
  const allPoints = [...regular, ...playoffs];

  const estimatedCount = regular.filter((point) => point.availability === "estimated").length;
  const partialCount = regular.filter((point) => point.availability === "partial").length;

  const runs = [...buildRuns(regular, "rs"), ...buildRuns(playoffs, "po")];

  const xs = allPoints.map((point) => point.x);
  const xDomain: readonly [number, number] = xs.length > 0 ? [Math.min(...xs), Math.max(...xs)] : [0, 1];

  const values = allPoints.map((point) => point.value);
  const mustInclude: number[] = [];

  let peak: PeakMark | null = null;
  if (payload.peak) {
    const matched = regular.find((point) => point.seasonLabel === payload.peak?.season);
    if (matched) {
      peak = { x: matched.x, value: matched.value, label: `Peak ${payload.peak.displayValue}` };
      mustInclude.push(matched.value);
    } else {
      const anchor = findAnchor(regular);
      const x = positionForSeason(payload.peak.season, usesAgeAxis, anchor);
      if (x !== null && Number.isFinite(payload.peak.value)) {
        peak = { x, value: payload.peak.value, label: `Peak ${payload.peak.displayValue}` };
        mustInclude.push(payload.peak.value);
      }
    }
  }

  const catalogDomain: DomainLike | null = payload.metric.domain;
  const yDomain = resolveYDomain({ values: [...values, ...mustInclude], catalogDomain });

  const eraMarks: EraMark[] = [];
  if (xs.length > 0) {
    const anchor = findAnchor(regular);
    const [lowest, highest] = xDomain;
    for (const boundary of payload.eraBoundaries) {
      const x = positionForSeason(boundary.season, usesAgeAxis, anchor);
      if (x === null || x < lowest - 0.5 || x > highest + 0.5) continue;
      eraMarks.push({ season: boundary.season, label: boundary.label, detail: boundary.detail, x });
    }
  }

  return {
    regular,
    playoffs,
    allPoints,
    runs,
    eraMarks,
    peak,
    xDomain,
    yDomain,
    usesAgeAxis,
    estimatedCount,
    partialCount,
    missingCount,
    isEmpty: regular.length === 0 && playoffs.length === 0,
  };
}

/** ~`count` evenly spaced x-axis ticks across the domain, rounded to whole seasons/ages. */
export function thinnedXTicks(domain: readonly [number, number], count: number): readonly number[] {
  const [low, high] = domain;
  if (!Number.isFinite(low) || !Number.isFinite(high) || high <= low || count < 2) return [Math.round(low)];
  const step = (high - low) / (count - 1);
  const seen = new Set<number>();
  const out: number[] = [];
  for (let index = 0; index < count; index += 1) {
    const value = Math.round(low + step * index);
    if (!seen.has(value)) {
      seen.add(value);
      out.push(value);
    }
  }
  return out;
}
