/**
 * Runtime decoder for the `game_log` payload — `contracts/fixtures/widget_game_log.json`,
 * `backend/nbastats/widgets/game_log.py`.
 *
 * See `widgets/leaderboard/payload.ts`'s module docstring for why every object here is checked
 * against an explicit key allow-list rather than just cast.
 *
 * **A deliberate, documented divergence from `src/api/types.ts`'s `GameLogRow`:** the real
 * fixture and `game_log.py`'s row dict both carry a top-level `minutes` field (`line.basic.minutes`)
 * alongside `values` — this is exactly the anti-drift signal the decoder test below is for. WP5
 * does not own `api/types.ts` (WEB_DESIGN.md §9 / CONTRACT-FOR-AGENTS.md — a signature there is a
 * request back to WP0, not a local edit), so this module declares its own {@link DecodedGameLogRow}
 * that is `GameLogRow` plus that one field, and the widget renders against it. `minutes` is the
 * fallback the Swift original reads when a column's key is `"min"` but `values` happens not to
 * carry it (`GameLogWidget.swift`'s `rawValue(_:key:)`), so dropping it would silently break that
 * one column rather than merely losing an unused field.
 */
import type { Availability, GameLogPayload, MetricDescriptor, MetricDomain, MetricEraSpec, MetricMap, PlayerRef } from "../../api/types";

export interface DecodedGameLogRow {
  readonly gameId: string;
  readonly date: string;
  readonly opponentAbbr: string | null;
  readonly isHome: boolean | null;
  readonly result: string | null;
  readonly score: string | null;
  readonly started: boolean | null;
  /** Not on `api/types.ts`'s `GameLogRow` — see the module docstring. */
  readonly minutes: number | null;
  readonly values: MetricMap;
  readonly availability: Availability;
}

export interface DecodedGameLogPayload extends Omit<GameLogPayload, "rows"> {
  readonly rows: readonly DecodedGameLogRow[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireRecord(value: unknown, where: string): Record<string, unknown> {
  if (!isRecord(value)) throw new Error(`game_log payload: ${where} must be an object`);
  return value;
}

function requireArray(value: unknown, where: string): readonly unknown[] {
  if (!Array.isArray(value)) throw new Error(`game_log payload: ${where} must be an array`);
  return value;
}

function requireString(value: unknown, where: string): string {
  if (typeof value !== "string") throw new Error(`game_log payload: ${where} must be a string`);
  return value;
}

function requireNumber(value: unknown, where: string): number {
  if (typeof value !== "number") throw new Error(`game_log payload: ${where} must be a number`);
  return value;
}

function requireBoolean(value: unknown, where: string): boolean {
  if (typeof value !== "boolean") throw new Error(`game_log payload: ${where} must be a boolean`);
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

function optionalBoolean(value: unknown, where: string): boolean | null {
  if (value === null || value === undefined) return null;
  return requireBoolean(value, where);
}

function assertKnownKeys(record: Record<string, unknown>, known: readonly string[], where: string): void {
  const allowed = new Set(known);
  for (const key of Object.keys(record)) {
    if (!allowed.has(key)) {
      throw new Error(
        `game_log payload: unknown field "${key}" in ${where} — teach payload.ts about it or it ` +
          `will be silently dropped`,
      );
    }
  }
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

function decodePlayerRef(value: unknown, where: string): PlayerRef {
  const record = requireRecord(value, where);
  assertKnownKeys(record, PLAYER_REF_KEYS, where);
  return {
    playerId: requireNumber(record.playerId, `${where}.playerId`),
    name: requireString(record.name, `${where}.name`),
    firstName: optionalString(record.firstName, `${where}.firstName`),
    lastName: optionalString(record.lastName, `${where}.lastName`),
    teamId: optionalNumber(record.teamId, `${where}.teamId`),
    teamAbbr: optionalString(record.teamAbbr, `${where}.teamAbbr`),
    position: optionalString(record.position, `${where}.position`),
    jersey: optionalString(record.jersey, `${where}.jersey`),
    headshotUrl: optionalString(record.headshotUrl, `${where}.headshotUrl`),
    isActive: requireBoolean(record.isActive, `${where}.isActive`),
  };
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
  return {
    min: requireNumber(record.min, `${where}.min`),
    max: requireNumber(record.max, `${where}.max`),
  };
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
    scope: requireArray(record.scope, `${where}.scope`).map((entry, index) =>
      requireString(entry, `${where}.scope[${index}]`),
    ),
    availability: decodeEraSpec(record.availability, `${where}.availability`),
    domain: decodeDomain(record.domain, `${where}.domain`),
    glossary: requireString(record.glossary, `${where}.glossary`),
  };
}

function decodeMetricMap(value: unknown, where: string): MetricMap {
  const record = requireRecord(value, where);
  const out: Record<string, number | null> = {};
  for (const [key, entry] of Object.entries(record)) {
    out[key] = optionalNumber(entry, `${where}.${key}`);
  }
  return out;
}

const AVAILABILITY_VALUES = new Set(["full", "estimated", "partial", "unavailable"]);

function decodeAvailability(value: unknown, where: string): Availability {
  const text = requireString(value, where);
  if (!AVAILABILITY_VALUES.has(text)) {
    throw new Error(`game_log payload: ${where} has unknown availability "${text}"`);
  }
  return text as Availability;
}

const ROW_KEYS = [
  "gameId",
  "date",
  "opponentAbbr",
  "isHome",
  "result",
  "score",
  "started",
  "minutes",
  "values",
  "availability",
] as const;

function decodeRow(value: unknown, where: string): DecodedGameLogRow {
  const record = requireRecord(value, where);
  assertKnownKeys(record, ROW_KEYS, where);
  return {
    gameId: requireString(record.gameId, `${where}.gameId`),
    date: requireString(record.date, `${where}.date`),
    opponentAbbr: optionalString(record.opponentAbbr, `${where}.opponentAbbr`),
    isHome: optionalBoolean(record.isHome, `${where}.isHome`),
    result: optionalString(record.result, `${where}.result`),
    score: optionalString(record.score, `${where}.score`),
    started: optionalBoolean(record.started, `${where}.started`),
    minutes: optionalNumber(record.minutes, `${where}.minutes`),
    values: decodeMetricMap(record.values, `${where}.values`),
    availability: decodeAvailability(record.availability, `${where}.availability`),
  };
}

const PAYLOAD_KEYS = ["player", "season", "seasonType", "columns", "seasonBests", "rows"] as const;

/**
 * Decodes and fully validates a `game_log` resolve payload. Throws on a missing required field
 * or on a field the server sends that this function does not yet know about — see the module
 * docstring.
 */
export function decodeGameLogPayload(raw: unknown): DecodedGameLogPayload {
  const record = requireRecord(raw, "payload");
  assertKnownKeys(record, PAYLOAD_KEYS, "payload");

  return {
    player: decodePlayerRef(record.player, "payload.player"),
    season: requireString(record.season, "payload.season"),
    seasonType: requireString(record.seasonType, "payload.seasonType"),
    columns: requireArray(record.columns, "payload.columns").map((entry, index) =>
      decodeMetricDescriptor(entry, `payload.columns[${index}]`),
    ),
    seasonBests: decodeMetricMap(record.seasonBests, "payload.seasonBests"),
    rows: requireArray(record.rows, "payload.rows").map((entry, index) => decodeRow(entry, `payload.rows[${index}]`)),
  };
}
