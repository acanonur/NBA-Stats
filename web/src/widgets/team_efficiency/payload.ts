/**
 * Runtime decoder for the `team_efficiency` payload — `contracts/fixtures/widget_team_efficiency.json`,
 * `backend/nbastats/widgets/team_efficiency.py`.
 *
 * See `widgets/leaderboard/payload.ts`'s module docstring for why every object here is checked
 * against an explicit key allow-list rather than just cast. `values`/`leagueAverage` are metric
 * maps keyed by whatever metric keys the request asked for (`off_rtg`, `def_rtg`, `net_rtg`,
 * `pace`, plus `sortBy` when it is outside that standard four — CONTRACT.md §7.7), so those two
 * are decoded as open maps rather than against a fixed key list; `ranks` is the same shape but
 * **not nullable** — a rank is always an integer, `<= 0` being the reserved "no rank" sentinel
 * the widget itself renders as an em dash, never the decoder.
 */
import type { MetricMap, TeamEfficiencyPayload, TeamEfficiencyRow, TeamRef } from "../../api/types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireRecord(value: unknown, where: string): Record<string, unknown> {
  if (!isRecord(value)) throw new Error(`team_efficiency payload: ${where} must be an object`);
  return value;
}

function requireArray(value: unknown, where: string): readonly unknown[] {
  if (!Array.isArray(value)) throw new Error(`team_efficiency payload: ${where} must be an array`);
  return value;
}

function requireString(value: unknown, where: string): string {
  if (typeof value !== "string") throw new Error(`team_efficiency payload: ${where} must be a string`);
  return value;
}

function requireNumber(value: unknown, where: string): number {
  if (typeof value !== "number") throw new Error(`team_efficiency payload: ${where} must be a number`);
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
        `team_efficiency payload: unknown field "${key}" in ${where} — teach payload.ts about it or ` +
          `it will be silently dropped`,
      );
    }
  }
}

const TEAM_REF_KEYS = ["teamId", "abbr", "name", "city", "nickname", "conference", "division"] as const;

function decodeTeamRef(value: unknown, where: string): TeamRef {
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

function decodeMetricMap(value: unknown, where: string): MetricMap {
  const record = requireRecord(value, where);
  const out: Record<string, number | null> = {};
  for (const [key, entry] of Object.entries(record)) {
    out[key] = optionalNumber(entry, `${where}.${key}`);
  }
  return out;
}

function decodeRankMap(value: unknown, where: string): Readonly<Record<string, number>> {
  const record = requireRecord(value, where);
  const out: Record<string, number> = {};
  for (const [key, entry] of Object.entries(record)) {
    out[key] = requireNumber(entry, `${where}.${key}`);
  }
  return out;
}

const ROW_KEYS = ["rank", "team", "wins", "losses", "values", "ranks"] as const;

function decodeRow(value: unknown, where: string): TeamEfficiencyRow {
  const record = requireRecord(value, where);
  assertKnownKeys(record, ROW_KEYS, where);
  return {
    rank: requireNumber(record.rank, `${where}.rank`),
    team: decodeTeamRef(record.team, `${where}.team`),
    wins: optionalNumber(record.wins, `${where}.wins`),
    losses: optionalNumber(record.losses, `${where}.losses`),
    values: decodeMetricMap(record.values, `${where}.values`),
    ranks: decodeRankMap(record.ranks, `${where}.ranks`),
  };
}

const STYLES = new Set(["table", "scatter"]);

function decodeStyle(value: unknown, where: string): "table" | "scatter" {
  const text = requireString(value, where);
  if (!STYLES.has(text)) throw new Error(`team_efficiency payload: ${where} has unknown style "${text}"`);
  return text as "table" | "scatter";
}

const PAYLOAD_KEYS = ["season", "seasonType", "sortBy", "style", "leagueAverage", "rows"] as const;

/**
 * Decodes and fully validates a `team_efficiency` resolve payload. Throws on a missing required
 * field or on a field the server sends that this function does not yet know about — see the
 * module docstring.
 */
export function decodeTeamEfficiencyPayload(raw: unknown): TeamEfficiencyPayload {
  const record = requireRecord(raw, "payload");
  assertKnownKeys(record, PAYLOAD_KEYS, "payload");

  return {
    season: requireString(record.season, "payload.season"),
    seasonType: requireString(record.seasonType, "payload.seasonType"),
    sortBy: requireString(record.sortBy, "payload.sortBy"),
    style: decodeStyle(record.style, "payload.style"),
    leagueAverage: decodeMetricMap(record.leagueAverage, "payload.leagueAverage"),
    rows: requireArray(record.rows, "payload.rows").map((entry, index) => decodeRow(entry, `payload.rows[${index}]`)),
  };
}
