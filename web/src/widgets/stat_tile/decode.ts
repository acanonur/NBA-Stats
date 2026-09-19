/**
 * Runtime decoding for the `stat_tile` payload (`contracts/CONTRACT.md` §4;
 * `backend/nbastats/widgets/stat_tile.py`; `contracts/fixtures/widget_stat_tile.json`).
 *
 * See `widgets/scoreboard/decode.ts`'s module docstring for why this hand-rolled shape check
 * exists at all (no schema-validation dependency in this project) and what its two halves are
 * for: every required field must be present, and every field the fixture actually carries must
 * be known here — the second half is what turns a new server key into a red test in
 * `decode.test.ts` instead of a silently-dropped field.
 */
import type { Availability, PlayerRef, SparklinePoint, StatTilePayload, SubjectRef, SubjectType, TeamRef, MetricValue } from "../../api/types";

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
        `${context}: unrecognized field "${key}" — the stat_tile decoder does not know this ` +
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

const AVAILABILITIES: readonly Availability[] = ["full", "estimated", "partial", "unavailable"];

function availability(obj: Record<string, unknown>, key: string, context: string): Availability {
  const value = obj[key];
  if (typeof value !== "string" || !(AVAILABILITIES as readonly string[]).includes(value)) {
    throw new Error(`${context}.${key}: expected one of ${AVAILABILITIES.join(", ")}`);
  }
  return value as Availability;
}

const TEAM_REF_KEYS = ["teamId", "abbr", "name", "city", "nickname", "conference", "division"] as const;

function decodeTeamRef(raw: unknown, context: string): TeamRef {
  const obj = assertRecord(raw, context);
  checkShape(obj, TEAM_REF_KEYS, context);
  return {
    teamId: num(obj, "teamId", context),
    abbr: str(obj, "abbr", context),
    name: str(obj, "name", context),
    city: nullableStr(obj, "city", context),
    nickname: nullableStr(obj, "nickname", context),
    conference: nullableStr(obj, "conference", context),
    division: nullableStr(obj, "division", context),
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

const SUBJECT_TYPES: readonly SubjectType[] = ["player", "team"];

const SUBJECT_REF_KEYS = ["type", "player", "team"] as const;

function decodeSubjectRef(raw: unknown, context: string): SubjectRef {
  const obj = assertRecord(raw, context);
  checkShape(obj, SUBJECT_REF_KEYS, context);
  const type = obj.type;
  if (typeof type !== "string" || !(SUBJECT_TYPES as readonly string[]).includes(type)) {
    throw new Error(`${context}.type: expected one of ${SUBJECT_TYPES.join(", ")}`);
  }
  const playerRaw = obj.player;
  const teamRaw = obj.team;
  return {
    type: type as SubjectType,
    player: playerRaw === null ? null : decodePlayerRef(playerRaw, `${context}.player`),
    team: teamRaw === null ? null : decodeTeamRef(teamRaw, `${context}.team`),
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

const SPARKLINE_POINT_KEYS = ["x", "y", "gameId"] as const;

function decodeSparklinePoint(raw: unknown, context: string): SparklinePoint {
  const obj = assertRecord(raw, context);
  checkShape(obj, SPARKLINE_POINT_KEYS, context);
  return {
    x: str(obj, "x", context),
    y: nullableNum(obj, "y", context),
    gameId: str(obj, "gameId", context),
  };
}

const PAYLOAD_KEYS = ["subject", "context", "primary", "secondary", "sparkline", "sparklineMetric"] as const;

/** Decodes and validates a `stat_tile` resolve result's `payload`. Throws on any structural
 * mismatch rather than returning a best-effort partial object — see
 * `widgets/scoreboard/decode.ts`'s docstring for why that is the right failure mode here. */
export function decodeStatTilePayload(raw: unknown): StatTilePayload {
  const context = "stat_tile payload";
  const obj = assertRecord(raw, context);
  checkShape(obj, PAYLOAD_KEYS, context);
  const secondaryRaw = obj.secondary;
  if (!Array.isArray(secondaryRaw)) throw new Error(`${context}.secondary: expected an array`);
  const sparklineRaw = obj.sparkline;
  if (!Array.isArray(sparklineRaw)) throw new Error(`${context}.sparkline: expected an array`);
  return {
    subject: decodeSubjectRef(obj.subject, `${context}.subject`),
    context: str(obj, "context", context),
    primary: decodeMetricValue(obj.primary, `${context}.primary`),
    secondary: secondaryRaw.map((entry, index) => decodeMetricValue(entry, `${context}.secondary[${index}]`)),
    sparkline: sparklineRaw.map((entry, index) => decodeSparklinePoint(entry, `${context}.sparkline[${index}]`)),
    sparklineMetric: str(obj, "sparklineMetric", context),
  };
}
