/**
 * Runtime decoding for the `daily_movers` payload (`contracts/CONTRACT.md` §4;
 * `backend/nbastats/widgets/daily_movers.py`; `contracts/fixtures/widget_daily_movers.json`).
 *
 * See `widgets/scoreboard/decode.ts`'s module docstring for why this hand-rolled shape check
 * exists at all (no schema-validation dependency in this project) and what its two halves are
 * for: every required field must be present, and every field the fixture actually carries must
 * be known here — the second half is what turns a new server key into a red test in
 * `decode.test.ts` instead of a silently-dropped field.
 */
import type {
  Availability,
  DailyMoverRow,
  DailyMoversPayload,
  MetricDescriptor,
  MetricDomain,
  MetricEraSpec,
  MetricValue,
  PlayerRef,
} from "../../api/types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function assertRecord(value: unknown, context: string): Record<string, unknown> {
  if (!isRecord(value)) {
    throw new Error(`${context}: expected an object, received ${value === null ? "null" : typeof value}`);
  }
  return value;
}

function checkShape(obj: Record<string, unknown>, keys: readonly string[], context: string): void {
  const known = new Set(keys);
  for (const key of Object.keys(obj)) {
    if (!known.has(key)) {
      throw new Error(
        `${context}: unrecognized field "${key}" — the daily_movers decoder does not know this ` +
          "field yet. Teach decode.ts about it (and this widget) rather than ignoring it.",
      );
    }
  }
  for (const key of keys) {
    if (!(key in obj)) throw new Error(`${context}: missing required field "${key}"`);
  }
}

function str(obj: Record<string, unknown>, key: string, context: string): string {
  const value = obj[key];
  if (typeof value !== "string") throw new Error(`${context}.${key}: expected a string`);
  return value;
}

function nullableStr(obj: Record<string, unknown>, key: string, context: string): string | null {
  const value = obj[key];
  if (value === null) return null;
  if (typeof value !== "string") throw new Error(`${context}.${key}: expected a string or null`);
  return value;
}

function num(obj: Record<string, unknown>, key: string, context: string): number {
  const value = obj[key];
  if (typeof value !== "number") throw new Error(`${context}.${key}: expected a number`);
  return value;
}

function nullableNum(obj: Record<string, unknown>, key: string, context: string): number | null {
  const value = obj[key];
  if (value === null) return null;
  if (typeof value !== "number") throw new Error(`${context}.${key}: expected a number or null`);
  return value;
}

function bool(obj: Record<string, unknown>, key: string, context: string): boolean {
  const value = obj[key];
  if (typeof value !== "boolean") throw new Error(`${context}.${key}: expected a boolean`);
  return value;
}

function nullableBool(obj: Record<string, unknown>, key: string, context: string): boolean | null {
  const value = obj[key];
  if (value === null) return null;
  if (typeof value !== "boolean") throw new Error(`${context}.${key}: expected a boolean or null`);
  return value;
}

const AVAILABILITIES: readonly Availability[] = ["full", "estimated", "partial", "unavailable"];

function availability(obj: Record<string, unknown>, key: string, context: string): Availability {
  const value = obj[key];
  if (typeof value !== "string" || !(AVAILABILITIES as readonly string[]).includes(value)) {
    throw new Error(`${context}.${key}: expected one of ${AVAILABILITIES.join(", ")}`);
  }
  return value as Availability;
}

const PLAYER_REF_KEYS = [
  "playerId",
  "name",
  "firstName",
  "lastName",
  "teamId",
  "teamAbbr",
  "position",
  "jersey",
  "headshotUrl",
  "isActive",
] as const;

function decodePlayerRef(raw: unknown, context: string): PlayerRef {
  const obj = assertRecord(raw, context);
  checkShape(obj, PLAYER_REF_KEYS, context);
  return {
    playerId: num(obj, "playerId", context),
    name: str(obj, "name", context),
    firstName: nullableStr(obj, "firstName", context),
    lastName: nullableStr(obj, "lastName", context),
    teamId: nullableNum(obj, "teamId", context),
    teamAbbr: nullableStr(obj, "teamAbbr", context),
    position: nullableStr(obj, "position", context),
    jersey: nullableStr(obj, "jersey", context),
    headshotUrl: nullableStr(obj, "headshotUrl", context),
    isActive: bool(obj, "isActive", context),
  };
}

const METRIC_VALUE_KEYS = [
  "metric",
  "value",
  "displayValue",
  "rank",
  "percentile",
  "leagueAverage",
  "delta",
  "isEstimated",
  "availability",
] as const;

function decodeMetricValue(raw: unknown, context: string): MetricValue {
  const obj = assertRecord(raw, context);
  checkShape(obj, METRIC_VALUE_KEYS, context);
  return {
    metric: str(obj, "metric", context),
    value: nullableNum(obj, "value", context),
    displayValue: str(obj, "displayValue", context),
    rank: nullableNum(obj, "rank", context),
    percentile: nullableNum(obj, "percentile", context),
    leagueAverage: nullableNum(obj, "leagueAverage", context),
    delta: nullableNum(obj, "delta", context),
    isEstimated: bool(obj, "isEstimated", context),
    availability: availability(obj, "availability", context),
  };
}

const ERA_SPEC_KEYS = ["seasonFrom", "perGameFrom", "seasonLevelOnly", "estimatedBefore"] as const;

function decodeEraSpec(raw: unknown, context: string): MetricEraSpec {
  const obj = assertRecord(raw, context);
  checkShape(obj, ERA_SPEC_KEYS, context);
  return {
    seasonFrom: str(obj, "seasonFrom", context),
    perGameFrom: str(obj, "perGameFrom", context),
    seasonLevelOnly: bool(obj, "seasonLevelOnly", context),
    estimatedBefore: nullableStr(obj, "estimatedBefore", context),
  };
}

const DOMAIN_KEYS = ["min", "max"] as const;

function decodeDomain(raw: unknown, context: string): MetricDomain {
  const obj = assertRecord(raw, context);
  checkShape(obj, DOMAIN_KEYS, context);
  return { min: num(obj, "min", context), max: num(obj, "max", context) };
}

const METRIC_DESCRIPTOR_KEYS = [
  "key",
  "name",
  "shortName",
  "category",
  "format",
  "higherIsBetter",
  "scope",
  "availability",
  "domain",
  "glossary",
] as const;

function decodeMetricDescriptor(raw: unknown, context: string): MetricDescriptor {
  const obj = assertRecord(raw, context);
  checkShape(obj, METRIC_DESCRIPTOR_KEYS, context);
  const scopeRaw = obj.scope;
  if (!Array.isArray(scopeRaw) || scopeRaw.some((entry) => typeof entry !== "string")) {
    throw new Error(`${context}.scope: expected an array of strings`);
  }
  const domainRaw = obj.domain;
  return {
    key: str(obj, "key", context),
    name: str(obj, "name", context),
    shortName: str(obj, "shortName", context),
    category: str(obj, "category", context),
    format: str(obj, "format", context),
    higherIsBetter: bool(obj, "higherIsBetter", context),
    scope: scopeRaw as readonly string[],
    availability: decodeEraSpec(obj.availability, `${context}.availability`),
    domain: domainRaw === null ? null : decodeDomain(domainRaw, `${context}.domain`),
    glossary: str(obj, "glossary", context),
  };
}

const ROW_KEYS = [
  "rank",
  "player",
  "gameId",
  "opponentAbbr",
  "isHome",
  "result",
  "line",
  "value",
  "seasonAverage",
  "delta",
] as const;

function decodeRow(raw: unknown, context: string): DailyMoverRow {
  const obj = assertRecord(raw, context);
  checkShape(obj, ROW_KEYS, context);
  return {
    rank: num(obj, "rank", context),
    player: decodePlayerRef(obj.player, `${context}.player`),
    gameId: str(obj, "gameId", context),
    opponentAbbr: nullableStr(obj, "opponentAbbr", context),
    isHome: nullableBool(obj, "isHome", context),
    result: nullableStr(obj, "result", context),
    line: str(obj, "line", context),
    value: decodeMetricValue(obj.value, `${context}.value`),
    seasonAverage: nullableNum(obj, "seasonAverage", context),
    delta: nullableNum(obj, "delta", context),
  };
}

const DIRECTIONS = new Set(["best", "worst", "surprise"]);

const PAYLOAD_KEYS = ["date", "metric", "direction", "rows"] as const;

/** Decodes and validates a `daily_movers` resolve result's `payload`. Throws on any structural
 * mismatch rather than returning a best-effort partial object — see
 * `widgets/scoreboard/decode.ts`'s docstring for why that is the right failure mode here. */
export function decodeDailyMoversPayload(raw: unknown): DailyMoversPayload {
  const context = "daily_movers payload";
  const obj = assertRecord(raw, context);
  checkShape(obj, PAYLOAD_KEYS, context);
  const rowsRaw = obj.rows;
  if (!Array.isArray(rowsRaw)) throw new Error(`${context}.rows: expected an array`);
  const direction = obj.direction;
  if (typeof direction !== "string" || !DIRECTIONS.has(direction)) {
    throw new Error(`${context}.direction: expected one of best, worst, surprise`);
  }
  return {
    date: str(obj, "date", context),
    metric: decodeMetricDescriptor(obj.metric, `${context}.metric`),
    direction: direction as DailyMoversPayload["direction"],
    rows: rowsRaw.map((entry, index) => decodeRow(entry, `${context}.rows[${index}]`)),
  };
}
