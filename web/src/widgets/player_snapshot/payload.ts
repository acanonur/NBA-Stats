/**
 * Runtime decoder for the `player_snapshot` payload — `contracts/fixtures/widget_player_snapshot.json`,
 * `backend/nbastats/widgets/player_snapshot.py`.
 *
 * See `widgets/leaderboard/payload.ts`'s module docstring for why every object here is checked
 * against an explicit key allow-list rather than just cast: a field the server starts sending
 * that this function does not know about must fail `__tests__/payload.test.ts` against the real
 * fixture, not disappear silently.
 */
import type { MetricValue, PlayerRef, PlayerSnapshotPayload } from "../../api/types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireRecord(value: unknown, where: string): Record<string, unknown> {
  if (!isRecord(value)) throw new Error(`player_snapshot payload: ${where} must be an object`);
  return value;
}

function requireArray(value: unknown, where: string): readonly unknown[] {
  if (!Array.isArray(value)) throw new Error(`player_snapshot payload: ${where} must be an array`);
  return value;
}

function requireString(value: unknown, where: string): string {
  if (typeof value !== "string") throw new Error(`player_snapshot payload: ${where} must be a string`);
  return value;
}

function requireNumber(value: unknown, where: string): number {
  if (typeof value !== "number") throw new Error(`player_snapshot payload: ${where} must be a number`);
  return value;
}

function requireBoolean(value: unknown, where: string): boolean {
  if (typeof value !== "boolean") throw new Error(`player_snapshot payload: ${where} must be a boolean`);
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
        `player_snapshot payload: unknown field "${key}" in ${where} — teach payload.ts about it or ` +
          `it will be silently dropped`,
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
    throw new Error(`player_snapshot payload: ${where} has unknown availability "${text}"`);
  }
  return text as MetricValue["availability"];
}

function decodeMetricValue(value: unknown, where: string): MetricValue {
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

const PAYLOAD_KEYS = [
  "player",
  "season",
  "seasonType",
  "teamAbbr",
  "gp",
  "gs",
  "minutesPerGame",
  "metrics",
  "eraNote",
] as const;

/**
 * Decodes and fully validates a `player_snapshot` resolve payload. Throws on a missing required
 * field or on a field the server sends that this function does not yet know about — see the
 * module docstring.
 */
export function decodePlayerSnapshotPayload(raw: unknown): PlayerSnapshotPayload {
  const record = requireRecord(raw, "payload");
  assertKnownKeys(record, PAYLOAD_KEYS, "payload");

  return {
    player: decodePlayerRef(record.player, "payload.player"),
    season: requireString(record.season, "payload.season"),
    seasonType: requireString(record.seasonType, "payload.seasonType"),
    teamAbbr: optionalString(record.teamAbbr, "payload.teamAbbr"),
    gp: optionalNumber(record.gp, "payload.gp"),
    gs: optionalNumber(record.gs, "payload.gs"),
    minutesPerGame: optionalNumber(record.minutesPerGame, "payload.minutesPerGame"),
    metrics: requireArray(record.metrics, "payload.metrics").map((entry, index) =>
      decodeMetricValue(entry, `payload.metrics[${index}]`),
    ),
    eraNote: optionalString(record.eraNote, "payload.eraNote"),
  };
}
