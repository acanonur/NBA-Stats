/**
 * Runtime decoder for the `career_arc` payload — `contracts/fixtures/widget_career_arc.json`,
 * `backend/nbastats/widgets/career_arc.py`.
 *
 * See `widgets/leaderboard/payload.ts`'s module docstring for why every object here is checked
 * against an explicit key allow-list rather than just cast.
 *
 * **A deliberate, documented divergence from `src/api/types.ts`'s `CareerArcPayload`:** the real
 * fixture and `career_arc.py`'s `_era_boundaries` both carry a `detail` string alongside `season`
 * and `label` on every era boundary, but `CareerArcPayload.eraBoundaries` (WP0-owned,
 * CONTRACT-FOR-AGENTS.md — a signature there is a request back to WP0, not a local edit)
 * intentionally narrows that to `{season, label}` because the widget only ever draws the label on
 * its vertical rule. Dropping `detail` silently would be exactly the drift this decoder's
 * allow-list is meant to catch, so it is decoded into {@link DecodedEraBoundary} instead and the
 * widget renders against {@link DecodedCareerArcPayload} — see `widgets/game_log/payload.ts`'s
 * `DecodedGameLogRow` for the same pattern.
 */
import type { Availability, CareerArcPayload, CareerArcPeak, CareerArcSeason, MetricDescriptor, MetricDomain, MetricEraSpec, PlayerRef } from "../../api/types";

export interface DecodedEraBoundary {
  readonly season: string;
  readonly label: string;
  readonly detail: string | null;
}

export interface DecodedCareerArcPayload extends Omit<CareerArcPayload, "eraBoundaries"> {
  readonly eraBoundaries: readonly DecodedEraBoundary[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireRecord(value: unknown, where: string): Record<string, unknown> {
  if (!isRecord(value)) throw new Error(`career_arc payload: ${where} must be an object`);
  return value;
}

function requireArray(value: unknown, where: string): readonly unknown[] {
  if (!Array.isArray(value)) throw new Error(`career_arc payload: ${where} must be an array`);
  return value;
}

function requireString(value: unknown, where: string): string {
  if (typeof value !== "string") throw new Error(`career_arc payload: ${where} must be a string`);
  return value;
}

function requireNumber(value: unknown, where: string): number {
  if (typeof value !== "number") throw new Error(`career_arc payload: ${where} must be a number`);
  return value;
}

function requireBoolean(value: unknown, where: string): boolean {
  if (typeof value !== "boolean") throw new Error(`career_arc payload: ${where} must be a boolean`);
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
        `career_arc payload: unknown field "${key}" in ${where} — teach payload.ts about it or ` +
          `it will be silently dropped`,
      );
    }
  }
}

const PLAYER_REF_KEYS = ["playerId", "name", "firstName", "lastName", "teamId", "teamAbbr", "position", "jersey", "headshotUrl", "isActive"] as const;

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

const AVAILABILITY_VALUES = new Set(["full", "estimated", "partial", "unavailable"]);

function decodeAvailability(value: unknown, where: string): Availability {
  const text = requireString(value, where);
  if (!AVAILABILITY_VALUES.has(text)) throw new Error(`career_arc payload: ${where} has unknown availability "${text}"`);
  return text as Availability;
}

const SEASON_KEYS = ["season", "seasonType", "age", "teamAbbr", "gp", "value", "displayValue", "availability"] as const;

function decodeSeason(value: unknown, where: string): CareerArcSeason {
  const record = requireRecord(value, where);
  assertKnownKeys(record, SEASON_KEYS, where);
  return {
    season: requireString(record.season, `${where}.season`),
    seasonType: requireString(record.seasonType, `${where}.seasonType`),
    age: optionalNumber(record.age, `${where}.age`),
    teamAbbr: optionalString(record.teamAbbr, `${where}.teamAbbr`),
    gp: optionalNumber(record.gp, `${where}.gp`),
    value: optionalNumber(record.value, `${where}.value`),
    displayValue: requireString(record.displayValue, `${where}.displayValue`),
    availability: decodeAvailability(record.availability, `${where}.availability`),
  };
}

const ERA_BOUNDARY_KEYS = ["season", "label", "detail"] as const;

function decodeEraBoundary(value: unknown, where: string): DecodedEraBoundary {
  const record = requireRecord(value, where);
  assertKnownKeys(record, ERA_BOUNDARY_KEYS, where);
  return {
    season: requireString(record.season, `${where}.season`),
    label: requireString(record.label, `${where}.label`),
    detail: optionalString(record.detail, `${where}.detail`),
  };
}

const PEAK_KEYS = ["season", "value", "displayValue"] as const;

function decodePeak(value: unknown, where: string): CareerArcPeak | null {
  if (value === null || value === undefined) return null;
  const record = requireRecord(value, where);
  assertKnownKeys(record, PEAK_KEYS, where);
  return {
    season: requireString(record.season, `${where}.season`),
    value: requireNumber(record.value, `${where}.value`),
    displayValue: requireString(record.displayValue, `${where}.displayValue`),
  };
}

const XAXIS_VALUES = new Set(["season", "age"]);

function decodeXAxis(value: unknown, where: string): "season" | "age" {
  const text = requireString(value, where);
  if (!XAXIS_VALUES.has(text)) throw new Error(`career_arc payload: ${where} has unknown axis "${text}"`);
  return text as "season" | "age";
}

const PAYLOAD_KEYS = ["player", "metric", "xAxis", "seasons", "playoffSeasons", "eraBoundaries", "peak"] as const;

/**
 * Decodes and fully validates a `career_arc` resolve payload. Throws on a missing required field
 * or on a field the server sends that this function does not yet know about — see the module
 * docstring.
 */
export function decodeCareerArcPayload(raw: unknown): DecodedCareerArcPayload {
  const record = requireRecord(raw, "payload");
  assertKnownKeys(record, PAYLOAD_KEYS, "payload");

  return {
    player: decodePlayerRef(record.player, "payload.player"),
    metric: decodeMetricDescriptor(record.metric, "payload.metric"),
    xAxis: decodeXAxis(record.xAxis, "payload.xAxis"),
    seasons: requireArray(record.seasons, "payload.seasons").map((entry, index) => decodeSeason(entry, `payload.seasons[${index}]`)),
    playoffSeasons: requireArray(record.playoffSeasons, "payload.playoffSeasons").map((entry, index) => decodeSeason(entry, `payload.playoffSeasons[${index}]`)),
    eraBoundaries: requireArray(record.eraBoundaries, "payload.eraBoundaries").map((entry, index) => decodeEraBoundary(entry, `payload.eraBoundaries[${index}]`)),
    peak: decodePeak(record.peak, "payload.peak"),
  };
}
