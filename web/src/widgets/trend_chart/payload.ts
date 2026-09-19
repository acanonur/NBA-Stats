/**
 * Runtime decoder for the `trend_chart` payload — `contracts/fixtures/widget_trend_chart.json`,
 * `backend/nbastats/widgets/trend_chart.py`.
 *
 * See `widgets/leaderboard/payload.ts`'s module docstring for why every object here is checked
 * against an explicit key allow-list rather than just cast: a field the server starts sending
 * that this function does not know about must fail `__tests__/payload.test.ts` against the real
 * fixture, not disappear silently.
 */
import type { MetricDescriptor, MetricDomain, MetricEraSpec, TrendChartPayload, TrendChartPoint, TrendChartSeries } from "../../api/types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireRecord(value: unknown, where: string): Record<string, unknown> {
  if (!isRecord(value)) throw new Error(`trend_chart payload: ${where} must be an object`);
  return value;
}

function requireArray(value: unknown, where: string): readonly unknown[] {
  if (!Array.isArray(value)) throw new Error(`trend_chart payload: ${where} must be an array`);
  return value;
}

function requireString(value: unknown, where: string): string {
  if (typeof value !== "string") throw new Error(`trend_chart payload: ${where} must be a string`);
  return value;
}

function requireNumber(value: unknown, where: string): number {
  if (typeof value !== "number") throw new Error(`trend_chart payload: ${where} must be a number`);
  return value;
}

function requireBoolean(value: unknown, where: string): boolean {
  if (typeof value !== "boolean") throw new Error(`trend_chart payload: ${where} must be a boolean`);
  return value;
}

function optionalString(value: unknown, where: string): string | null {
  if (value === null || value === undefined) return null;
  return requireString(value, where);
}

function optionalNumber(value: unknown, where: string): number | null {
  if (value === null || value === undefined) return null;
  return requireNumber(value, where);
}

function assertKnownKeys(record: Record<string, unknown>, known: readonly string[], where: string): void {
  const allowed = new Set(known);
  for (const key of Object.keys(record)) {
    if (!allowed.has(key)) {
      throw new Error(
        `trend_chart payload: unknown field "${key}" in ${where} — teach payload.ts about it or ` +
          `it will be silently dropped`,
      );
    }
  }
}

const ERA_SPEC_KEYS = ["seasonFrom", "perGameFrom", "seasonLevelOnly", "estimatedBefore"] as const;

function decodeEraSpec(value: unknown, where: string): MetricEraSpec {
  const record = requireRecord(value, where);
  assertKnownKeys(record, ERA_SPEC_KEYS, where);
  return {
    seasonFrom: requireString(record.seasonFrom, `${where}.seasonFrom`),
    perGameFrom: requireString(record.perGameFrom, `${where}.perGameFrom`),
    seasonLevelOnly: requireBoolean(record.seasonLevelOnly, `${where}.seasonLevelOnly`),
    estimatedBefore: optionalString(record.estimatedBefore, `${where}.estimatedBefore`),
  };
}

const DOMAIN_KEYS = ["min", "max"] as const;

function decodeDomain(value: unknown, where: string): MetricDomain | null {
  if (value === null || value === undefined) return null;
  const record = requireRecord(value, where);
  assertKnownKeys(record, DOMAIN_KEYS, where);
  return { min: requireNumber(record.min, `${where}.min`), max: requireNumber(record.max, `${where}.max`) };
}

const METRIC_DESCRIPTOR_KEYS = ["key", "name", "shortName", "category", "format", "higherIsBetter", "scope", "availability", "domain", "glossary"] as const;

function decodeMetricDescriptor(value: unknown, where: string): MetricDescriptor {
  const record = requireRecord(value, where);
  assertKnownKeys(record, METRIC_DESCRIPTOR_KEYS, where);
  return {
    key: requireString(record.key, `${where}.key`),
    name: requireString(record.name, `${where}.name`),
    shortName: requireString(record.shortName, `${where}.shortName`),
    category: requireString(record.category, `${where}.category`),
    format: requireString(record.format, `${where}.format`),
    higherIsBetter: requireBoolean(record.higherIsBetter, `${where}.higherIsBetter`),
    scope: requireArray(record.scope, `${where}.scope`).map((entry, index) => requireString(entry, `${where}.scope[${index}]`)),
    availability: decodeEraSpec(record.availability, `${where}.availability`),
    domain: decodeDomain(record.domain, `${where}.domain`),
    glossary: requireString(record.glossary, `${where}.glossary`),
  };
}

const POINT_KEYS = ["x", "y", "rolling", "gameId", "opponentAbbr"] as const;

function decodePoint(value: unknown, where: string): TrendChartPoint {
  const record = requireRecord(value, where);
  assertKnownKeys(record, POINT_KEYS, where);
  return {
    x: requireString(record.x, `${where}.x`),
    y: optionalNumber(record.y, `${where}.y`),
    rolling: optionalNumber(record.rolling, `${where}.rolling`),
    gameId: requireString(record.gameId, `${where}.gameId`),
    opponentAbbr: optionalString(record.opponentAbbr, `${where}.opponentAbbr`),
  };
}

const SERIES_KEYS = ["id", "label", "colorIndex", "points"] as const;

function decodeSeries(value: unknown, where: string): TrendChartSeries {
  const record = requireRecord(value, where);
  assertKnownKeys(record, SERIES_KEYS, where);
  return {
    id: requireString(record.id, `${where}.id`),
    label: requireString(record.label, `${where}.label`),
    colorIndex: requireNumber(record.colorIndex, `${where}.colorIndex`),
    points: requireArray(record.points, `${where}.points`).map((entry, index) => decodePoint(entry, `${where}.points[${index}]`)),
  };
}

const PAYLOAD_KEYS = ["metric", "rollingWindow", "leagueAverage", "yDomain", "series"] as const;

/**
 * Decodes and fully validates a `trend_chart` resolve payload. Throws on a missing required
 * field or on a field the server sends that this function does not yet know about — see the
 * module docstring.
 */
export function decodeTrendChartPayload(raw: unknown): TrendChartPayload {
  const record = requireRecord(raw, "payload");
  assertKnownKeys(record, PAYLOAD_KEYS, "payload");

  return {
    metric: decodeMetricDescriptor(record.metric, "payload.metric"),
    rollingWindow: requireNumber(record.rollingWindow, "payload.rollingWindow"),
    leagueAverage: optionalNumber(record.leagueAverage, "payload.leagueAverage"),
    yDomain: decodeDomain(record.yDomain, "payload.yDomain"),
    series: requireArray(record.series, "payload.series").map((entry, index) => decodeSeries(entry, `payload.series[${index}]`)),
  };
}
