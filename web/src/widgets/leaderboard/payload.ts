/**
 * Runtime decoder for the `leaderboard` payload — `contracts/fixtures/widget_leaderboard.json`,
 * `backend/nbastats/widgets/leaderboard.py`.
 *
 * This is the one place a raw `unknown` resolve payload becomes a typed `LeaderboardPayload`
 * (WEB_DESIGN.md §9 WP5 "done when": a decoder test asserts both that no required field is
 * missing and that no key the server actually sends is silently ignored). Every object shape is
 * checked against an explicit allow-list of keys — `assertKnownKeys` — so a field added to the
 * server's response without a matching edit here fails loudly in `__tests__/payload.test.ts`
 * against the real fixture, rather than being read once and forgotten.
 */
import type {
  LeaderboardPayload,
  LeaderboardRow,
  MetricDescriptor,
  MetricDomain,
  MetricEraSpec,
  MetricValue,
  PlayerRef,
  TeamRef,
} from "../../api/types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireRecord(value: unknown, where: string): Record<string, unknown> {
  if (!isRecord(value)) throw new Error(`leaderboard payload: ${where} must be an object`);
  return value;
}

function requireArray(value: unknown, where: string): readonly unknown[] {
  if (!Array.isArray(value)) throw new Error(`leaderboard payload: ${where} must be an array`);
  return value;
}

function requireString(value: unknown, where: string): string {
  if (typeof value !== "string") throw new Error(`leaderboard payload: ${where} must be a string`);
  return value;
}

function requireNumber(value: unknown, where: string): number {
  if (typeof value !== "number") throw new Error(`leaderboard payload: ${where} must be a number`);
  return value;
}

function requireBoolean(value: unknown, where: string): boolean {
  if (typeof value !== "boolean") throw new Error(`leaderboard payload: ${where} must be a boolean`);
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

/** Throws when `record` carries any key outside `known` — the anti-drift half of this file. */
function assertKnownKeys(record: Record<string, unknown>, known: readonly string[], where: string): void {
  const allowed = new Set(known);
  for (const key of Object.keys(record)) {
    if (!allowed.has(key)) {
      throw new Error(
        `leaderboard payload: unknown field "${key}" in ${where} — teach payload.ts about it or ` +
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

export function decodeMetricDescriptor(value: unknown, where: string): MetricDescriptor {
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

const AVAILABILITY_VALUES = new Set(["full", "estimated", "partial", "unavailable"]);

function decodeAvailability(value: unknown, where: string): MetricValue["availability"] {
  const text = requireString(value, where);
  if (!AVAILABILITY_VALUES.has(text)) {
    throw new Error(`leaderboard payload: ${where} has unknown availability "${text}"`);
  }
  return text as MetricValue["availability"];
}

export function decodeMetricValue(value: unknown, where: string): MetricValue {
  const record = requireRecord(value, where);
  assertKnownKeys(record, METRIC_VALUE_KEYS, where);
  return {
    metric: requireString(record.metric, `${where}.metric`),
    value: optionalNumber(record.value, `${where}.value`),
    displayValue: requireString(record.displayValue, `${where}.displayValue`),
    rank: optionalNumber(record.rank, `${where}.rank`),
    percentile: optionalNumber(record.percentile, `${where}.percentile`),
    leagueAverage: optionalNumber(record.leagueAverage, `${where}.leagueAverage`),
    delta: optionalNumber(record.delta, `${where}.delta`),
    isEstimated: requireBoolean(record.isEstimated, `${where}.isEstimated`),
    availability: decodeAvailability(record.availability, `${where}.availability`),
  };
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

export function decodePlayerRef(value: unknown, where: string): PlayerRef {
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

const TEAM_REF_KEYS = ["teamId", "abbr", "name", "city", "nickname", "conference", "division"] as const;

export function decodeTeamRef(value: unknown, where: string): TeamRef {
  const record = requireRecord(value, where);
  assertKnownKeys(record, TEAM_REF_KEYS, where);
  return {
    teamId: requireNumber(record.teamId, `${where}.teamId`),
    abbr: requireString(record.abbr, `${where}.abbr`),
    name: requireString(record.name, `${where}.name`),
    city: optionalString(record.city, `${where}.city`),
    nickname: optionalString(record.nickname, `${where}.nickname`),
    conference: optionalString(record.conference, `${where}.conference`),
    division: optionalString(record.division, `${where}.division`),
  };
}

const ROW_KEYS = ["rank", "player", "team", "value", "secondary", "season"] as const;

function decodeRow(value: unknown, where: string): LeaderboardRow {
  const record = requireRecord(value, where);
  assertKnownKeys(record, ROW_KEYS, where);
  return {
    rank: requireNumber(record.rank, `${where}.rank`),
    player: record.player === null || record.player === undefined
      ? null
      : decodePlayerRef(record.player, `${where}.player`),
    team: record.team === null || record.team === undefined
      ? null
      : decodeTeamRef(record.team, `${where}.team`),
    value: decodeMetricValue(record.value, `${where}.value`),
    secondary: requireArray(record.secondary, `${where}.secondary`).map((entry, index) =>
      decodeMetricValue(entry, `${where}.secondary[${index}]`),
    ),
    season: requireString(record.season, `${where}.season`),
  };
}

const PAYLOAD_KEYS = [
  "metric",
  "subjectType",
  "scope",
  "season",
  "seasonType",
  "perMode",
  "qualifier",
  "secondaryMetrics",
  "rows",
] as const;

const SUBJECT_TYPES = new Set(["player", "team"]);
const SCOPES = new Set(["season", "all_time"]);

/**
 * Decodes and fully validates a `leaderboard` resolve payload. Throws on a missing required
 * field or on a field the server sends that this function does not yet know about — see the
 * module docstring.
 */
export function decodeLeaderboardPayload(raw: unknown): LeaderboardPayload {
  const record = requireRecord(raw, "payload");
  assertKnownKeys(record, PAYLOAD_KEYS, "payload");

  const subjectType = requireString(record.subjectType, "payload.subjectType");
  if (!SUBJECT_TYPES.has(subjectType)) {
    throw new Error(`leaderboard payload: unknown subjectType "${subjectType}"`);
  }
  const scope = requireString(record.scope, "payload.scope");
  if (!SCOPES.has(scope)) {
    throw new Error(`leaderboard payload: unknown scope "${scope}"`);
  }

  return {
    metric: decodeMetricDescriptor(record.metric, "payload.metric"),
    subjectType: subjectType as LeaderboardPayload["subjectType"],
    scope: scope as LeaderboardPayload["scope"],
    season: requireString(record.season, "payload.season"),
    seasonType: requireString(record.seasonType, "payload.seasonType"),
    perMode: requireString(record.perMode, "payload.perMode"),
    qualifier: optionalString(record.qualifier, "payload.qualifier"),
    secondaryMetrics: requireArray(record.secondaryMetrics, "payload.secondaryMetrics").map((entry, index) =>
      decodeMetricDescriptor(entry, `payload.secondaryMetrics[${index}]`),
    ),
    rows: requireArray(record.rows, "payload.rows").map((entry, index) => decodeRow(entry, `payload.rows[${index}]`)),
  };
}
