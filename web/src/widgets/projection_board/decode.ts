/**
 * Runtime decoding for the `projection_board` payload (`contracts/CONTRACT.md` §4;
 * `backend/nbastats/widgets/projection_board.py`;
 * `contracts/fixtures/widget_projection_board.json`).
 *
 * See `widgets/stat_tile/decode.ts`'s module docstring for why this hand-rolled shape check
 * exists at all and what its two halves are for: every required field must be present, and
 * every field the fixture actually carries must be known here.
 */
import type {
  Availability,
  MetricDescriptor,
  MetricDomain,
  MetricEraSpec,
  PlayerRef,
  ProjectionBoardPayload,
  ProjectionBoardRow,
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
        `${context}: unrecognized field "${key}" — the projection_board decoder does not know ` +
          "this field yet. Teach decode.ts about it (and this widget) rather than ignoring it.",
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

function stringArray(obj: Record<string, unknown>, key: string, context: string): readonly string[] {
  const value = obj[key];
  if (!Array.isArray(value)) throw new Error(`${context}.${key}: expected an array`);
  return value.map((entry, index) => {
    if (typeof entry !== "string") throw new Error(`${context}.${key}[${index}]: expected a string`);
    return entry;
  });
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

const DESCRIPTOR_KEYS = [
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
  checkShape(obj, DESCRIPTOR_KEYS, context);
  const domainRaw = obj.domain;
  return {
    key: str(obj, "key", context),
    name: str(obj, "name", context),
    shortName: str(obj, "shortName", context),
    category: str(obj, "category", context),
    format: str(obj, "format", context),
    higherIsBetter: bool(obj, "higherIsBetter", context),
    scope: stringArray(obj, "scope", context),
    availability: decodeEraSpec(obj.availability, `${context}.availability`),
    domain: domainRaw === null ? null : decodeDomain(domainRaw, `${context}.domain`),
    glossary: str(obj, "glossary", context),
  };
}

const ROW_KEYS = [
  "player",
  "matchup",
  "opponentAbbr",
  "isHome",
  "gameId",
  "gameDate",
  "metric",
  "descriptor",
  "projection",
  "displayValue",
  "low",
  "high",
  "intervalLevel",
  "referenceValue",
  "delta",
  "deltaZ",
  "projectedMinutes",
  "availability",
] as const;

function decodeRow(raw: unknown, context: string): ProjectionBoardRow {
  const obj = assertRecord(raw, context);
  checkShape(obj, ROW_KEYS, context);
  return {
    player: decodePlayerRef(obj.player, `${context}.player`),
    matchup: str(obj, "matchup", context),
    opponentAbbr: str(obj, "opponentAbbr", context),
    isHome: bool(obj, "isHome", context),
    gameId: str(obj, "gameId", context),
    gameDate: str(obj, "gameDate", context),
    metric: str(obj, "metric", context),
    descriptor: decodeMetricDescriptor(obj.descriptor, `${context}.descriptor`),
    projection: num(obj, "projection", context),
    displayValue: str(obj, "displayValue", context),
    low: nullableNum(obj, "low", context),
    high: nullableNum(obj, "high", context),
    intervalLevel: nullableNum(obj, "intervalLevel", context),
    referenceValue: nullableNum(obj, "referenceValue", context),
    delta: nullableNum(obj, "delta", context),
    deltaZ: nullableNum(obj, "deltaZ", context),
    projectedMinutes: nullableNum(obj, "projectedMinutes", context),
    availability: availability(obj, "availability", context),
  };
}

const REFERENCES = ["season_average", "career_average", "none"] as const;

function reference(obj: Record<string, unknown>, key: string, context: string): ProjectionBoardPayload["reference"] {
  const value = obj[key];
  if (typeof value !== "string" || !(REFERENCES as readonly string[]).includes(value)) {
    throw new Error(`${context}.${key}: expected one of ${REFERENCES.join(", ")}`);
  }
  return value as ProjectionBoardPayload["reference"];
}

const PAYLOAD_KEYS = [
  "date",
  "throughDate",
  "selectionDate",
  "gameCount",
  "reference",
  "referenceLabel",
  "rows",
  "note",
] as const;

/** Decodes and validates a `projection_board` resolve result's `payload`. Throws on any
 * structural mismatch rather than returning a best-effort partial object. */
export function decodeProjectionBoardPayload(raw: unknown): ProjectionBoardPayload {
  const context = "projection_board payload";
  const obj = assertRecord(raw, context);
  checkShape(obj, PAYLOAD_KEYS, context);
  const rowsRaw = obj.rows;
  if (!Array.isArray(rowsRaw)) throw new Error(`${context}.rows: expected an array`);
  return {
    date: str(obj, "date", context),
    throughDate: nullableStr(obj, "throughDate", context),
    selectionDate: str(obj, "selectionDate", context),
    gameCount: num(obj, "gameCount", context),
    reference: reference(obj, "reference", context),
    referenceLabel: nullableStr(obj, "referenceLabel", context),
    rows: rowsRaw.map((entry, index) => decodeRow(entry, `${context}.rows[${index}]`)),
    note: nullableStr(obj, "note", context),
  };
}
