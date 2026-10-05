/**
 * Runtime decoding for the `scoreboard` payload (`contracts/CONTRACT.md` §4;
 * `backend/nbastats/widgets/scoreboard.py`; `contracts/fixtures/widget_scoreboard.json`).
 *
 * `WidgetContainer` hands every widget its payload as `unknown` (the frozen contract in
 * `web/CONTRACT-FOR-AGENTS.md` keeps `WidgetViewProps.payload` untyped on purpose), so this
 * module is the one place `scoreboard` turns that `unknown` into a `ScoreboardPayload` it can
 * actually render. It does real, if shallow, structural checks rather than a bare `as` cast:
 * every object shape below is checked against its own exact key set, so a field the backend
 * adds or removes shows up as a thrown error in `decode.test.ts` — a red test — instead of a
 * silently-ignored (or silently-`undefined`) field reaching the view. See that test file for
 * the fixture-driven half of this guarantee.
 *
 * Deliberately NOT a general-purpose schema library: there is no such dependency in this
 * project (WEB_DESIGN.md's "no new npm package the design does not name"), and a hand-rolled
 * decoder that mirrors `api/types.ts` field-for-field is a few dozen lines, not a framework.
 */
import type {
  Availability,
  GameStatus,
  MetricValue,
  PlayerRef,
  ScoreboardGame,
  ScoreboardPayload,
  ScoreboardTopPerformer,
  TeamRef,
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

/**
 * Throws when `obj` carries a key outside `keys` (a new server field this decoder has not been
 * taught about) or is missing one of `keys` (a field the decoder needs that the server stopped
 * sending). Both halves matter equally — see the module docstring.
 */
function checkShape(obj: Record<string, unknown>, keys: readonly string[], context: string): void {
  const known = new Set(keys);
  for (const key of Object.keys(obj)) {
    if (!known.has(key)) {
      throw new Error(
        `${context}: unrecognized field "${key}" — the scoreboard decoder does not know this ` +
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

const GAME_STATUSES: readonly GameStatus[] = ["scheduled", "live", "final"];

function gameStatus(obj: Record<string, unknown>, key: string, context: string): GameStatus {
  const value = obj[key];
  if (typeof value !== "string" || !(GAME_STATUSES as readonly string[]).includes(value)) {
    throw new Error(`${context}.${key}: expected one of ${GAME_STATUSES.join(", ")}`);
  }
  return value as GameStatus;
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

const TOP_PERFORMER_KEYS = ["player", "teamAbbr", "line", "value"] as const;

function decodeTopPerformer(raw: unknown, context: string): ScoreboardTopPerformer {
  const obj = assertRecord(raw, context);
  checkShape(obj, TOP_PERFORMER_KEYS, context);
  return {
    player: decodePlayerRef(obj.player, `${context}.player`),
    teamAbbr: str(obj, "teamAbbr", context),
    line: str(obj, "line", context),
    value: decodeMetricValue(obj.value, `${context}.value`),
  };
}

const GAME_KEYS = [
  "gameId",
  "date",
  "season",
  "seasonType",
  "home",
  "away",
  "homePts",
  "awayPts",
  "status",
  "period",
  "clock",
  "finalizedAt",
  "topPerformers",
] as const;

function decodeScoreboardGame(raw: unknown, context: string): ScoreboardGame {
  const obj = assertRecord(raw, context);
  checkShape(obj, GAME_KEYS, context);
  const performersRaw = obj.topPerformers;
  if (!Array.isArray(performersRaw)) {
    throw new Error(`${context}.topPerformers: expected an array`);
  }
  return {
    gameId: str(obj, "gameId", context),
    date: str(obj, "date", context),
    season: nullableStr(obj, "season", context),
    seasonType: nullableStr(obj, "seasonType", context),
    home: decodeTeamRef(obj.home, `${context}.home`),
    away: decodeTeamRef(obj.away, `${context}.away`),
    homePts: nullableNum(obj, "homePts", context),
    awayPts: nullableNum(obj, "awayPts", context),
    status: gameStatus(obj, "status", context),
    period: nullableNum(obj, "period", context),
    clock: nullableStr(obj, "clock", context),
    finalizedAt: nullableStr(obj, "finalizedAt", context),
    topPerformers: performersRaw.map((entry, index) =>
      decodeTopPerformer(entry, `${context}.topPerformers[${index}]`),
    ),
  };
}

const PAYLOAD_KEYS = ["date", "isLatestCompleted", "allFinal", "games"] as const;

/** Decodes and validates a `scoreboard` resolve result's `payload`. Throws (rather than
 * returning a best-effort partial object) on any structural mismatch — `WidgetContainer` only
 * ever calls a widget component when `result.status` is `"ok"` or `"partial"`, so a payload
 * that fails to decode there is a real contract break the widget should surface loudly during
 * development, not paper over. */
export function decodeScoreboardPayload(raw: unknown): ScoreboardPayload {
  const context = "scoreboard payload";
  const obj = assertRecord(raw, context);
  checkShape(obj, PAYLOAD_KEYS, context);
  const gamesRaw = obj.games;
  if (!Array.isArray(gamesRaw)) throw new Error(`${context}.games: expected an array`);
  return {
    date: str(obj, "date", context),
    isLatestCompleted: bool(obj, "isLatestCompleted", context),
    allFinal: bool(obj, "allFinal", context),
    games: gamesRaw.map((entry, index) => decodeScoreboardGame(entry, `${context}.games[${index}]`)),
  };
}
