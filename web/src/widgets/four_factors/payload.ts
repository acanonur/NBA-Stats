/**
 * Runtime decoder for the `four_factors` payload — `contracts/fixtures/widget_four_factors.json`,
 * `backend/nbastats/widgets/four_factors.py`.
 *
 * See `widgets/leaderboard/payload.ts`'s module docstring for why every object here is checked
 * against an explicit key allow-list rather than just cast: a field the server starts sending
 * that this function does not know about must fail `__tests__/payload.test.ts` against the real
 * fixture, not disappear silently.
 */
import type { FourFactorEntry, FourFactorsPayload, TeamRef } from "../../api/types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireRecord(value: unknown, where: string): Record<string, unknown> {
  if (!isRecord(value)) throw new Error(`four_factors payload: ${where} must be an object`);
  return value;
}

function requireArray(value: unknown, where: string): readonly unknown[] {
  if (!Array.isArray(value)) throw new Error(`four_factors payload: ${where} must be an array`);
  return value;
}

function requireString(value: unknown, where: string): string {
  if (typeof value !== "string") throw new Error(`four_factors payload: ${where} must be a string`);
  return value;
}

function requireNumber(value: unknown, where: string): number {
  if (typeof value !== "number") throw new Error(`four_factors payload: ${where} must be a number`);
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
        `four_factors payload: unknown field "${key}" in ${where} — teach payload.ts about it or ` +
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

const ENTRY_KEYS = ["key", "label", "weight", "value", "displayValue", "leagueAverage", "percentile", "rank"] as const;

function decodeEntry(value: unknown, where: string): FourFactorEntry {
  const record = requireRecord(value, where);
  assertKnownKeys(record, ENTRY_KEYS, where);
  return {
    key: requireString(record.key, `${where}.key`),
    label: requireString(record.label, `${where}.label`),
    weight: requireNumber(record.weight, `${where}.weight`),
    value: optionalNumber(record.value, `${where}.value`),
    displayValue: requireString(record.displayValue, `${where}.displayValue`),
    leagueAverage: optionalNumber(record.leagueAverage, `${where}.leagueAverage`),
    percentile: optionalNumber(record.percentile, `${where}.percentile`),
    rank: optionalNumber(record.rank, `${where}.rank`),
  };
}

const PAYLOAD_KEYS = ["team", "season", "seasonType", "offense", "defense"] as const;

/**
 * Decodes and fully validates a `four_factors` resolve payload. Throws on a missing required
 * field or on a field the server sends that this function does not yet know about — see the
 * module docstring.
 */
export function decodeFourFactorsPayload(raw: unknown): FourFactorsPayload {
  const record = requireRecord(raw, "payload");
  assertKnownKeys(record, PAYLOAD_KEYS, "payload");

  return {
    team: decodeTeamRef(record.team, "payload.team"),
    season: requireString(record.season, "payload.season"),
    seasonType: requireString(record.seasonType, "payload.seasonType"),
    offense: requireArray(record.offense, "payload.offense").map((entry, index) =>
      decodeEntry(entry, `payload.offense[${index}]`),
    ),
    defense: requireArray(record.defense, "payload.defense").map((entry, index) =>
      decodeEntry(entry, `payload.defense[${index}]`),
    ),
  };
}
