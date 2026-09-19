/**
 * Runtime decoding for the `fantasy_trade` payload (`contracts/CONTRACT.md` §4;
 * `backend/nbastats/widgets/fantasy_trade.py`; `contracts/fixtures/widget_fantasy_trade.json`).
 *
 * See `widgets/stat_tile/decode.ts`'s module docstring for why this hand-rolled shape check
 * exists at all and what its two halves are for: every required field must be present, and
 * every field the fixture actually carries must be known here.
 *
 * One field this decoder deliberately disagrees with `api/types.ts` about: `FantasyTradePayload
 * .sensitivity` is typed there as always-present, but `fantasy_trade.py::_payload` sends
 * `"sensitivity": null` outright whenever a tile is configured with `showSensitivity: false`
 * (`resolve()`'s `sweep` stays `None`) — a real, reachable server response, not a hypothetical
 * one. Rather than throw on that legitimate configuration, a null wire value decodes to the
 * empty sentinel below (`scenarios: []`, every number `null`), which still satisfies the
 * non-nullable field type for every caller and lets the widget simply not render a sensitivity
 * block when there is nothing in it.
 */
import type {
  Availability,
  FantasyCategory,
  FantasyTradeCategoryDelta,
  FantasyTradePayload,
  FantasyTradePlayerLine,
  FantasyTradePointsDelta,
  FantasyTradeSensitivity,
  FantasyTradeSensitivityScenario,
  FantasyTradeSide,
  MetricMap,
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
        `${context}: unrecognized field "${key}" — the fantasy_trade decoder does not know this ` +
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

function stringArray(obj: Record<string, unknown>, key: string, context: string): readonly FantasyCategory[] {
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

function metricMap(obj: Record<string, unknown>, key: string, context: string): MetricMap {
  const raw = assertRecord(obj[key], `${context}.${key}`);
  const out: Record<string, number | null> = {};
  for (const [metricKey, value] of Object.entries(raw)) {
    if (value === null) {
      out[metricKey] = null;
      continue;
    }
    if (typeof value !== "number") throw new Error(`${context}.${key}.${metricKey}: expected a number or null`);
    out[metricKey] = value;
  }
  return out;
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

const PLAYER_LINE_KEYS = ["player", "totalZ", "baselineRank", "gamesPlayed", "categories", "availability"] as const;

function decodePlayerLine(raw: unknown, context: string): FantasyTradePlayerLine {
  const obj = assertRecord(raw, context);
  checkShape(obj, PLAYER_LINE_KEYS, context);
  return {
    player: decodePlayerRef(obj.player, `${context}.player`),
    totalZ: nullableNum(obj, "totalZ", context),
    baselineRank: nullableNum(obj, "baselineRank", context),
    gamesPlayed: nullableNum(obj, "gamesPlayed", context),
    categories: metricMap(obj, "categories", context),
    availability: availability(obj, "availability", context),
  };
}

const SIDE_KEYS = ["label", "count", "totalZ", "espnPoints", "yahooPoints", "players", "missing"] as const;

function decodeSide(raw: unknown, context: string): FantasyTradeSide {
  const obj = assertRecord(raw, context);
  checkShape(obj, SIDE_KEYS, context);
  const playersRaw = obj.players;
  if (!Array.isArray(playersRaw)) throw new Error(`${context}.players: expected an array`);
  return {
    label: str(obj, "label", context),
    count: num(obj, "count", context),
    totalZ: nullableNum(obj, "totalZ", context),
    espnPoints: nullableNum(obj, "espnPoints", context),
    yahooPoints: nullableNum(obj, "yahooPoints", context),
    players: playersRaw.map((entry, index) => decodePlayerLine(entry, `${context}.players[${index}]`)),
    missing: stringArray(obj, "missing", context),
  };
}

const CATEGORY_DELTA_KEYS = ["category", "give", "get", "change", "net", "verdict", "punted"] as const;

function decodeCategoryDelta(raw: unknown, context: string): FantasyTradeCategoryDelta {
  const obj = assertRecord(raw, context);
  checkShape(obj, CATEGORY_DELTA_KEYS, context);
  return {
    category: str(obj, "category", context),
    give: nullableNum(obj, "give", context),
    get: nullableNum(obj, "get", context),
    change: nullableNum(obj, "change", context),
    net: nullableNum(obj, "net", context),
    verdict: str(obj, "verdict", context),
    punted: bool(obj, "punted", context),
  };
}

const POINTS_DELTA_KEYS = ["change", "net", "verdict"] as const;

function decodePointsDelta(raw: unknown, context: string): FantasyTradePointsDelta {
  const obj = assertRecord(raw, context);
  checkShape(obj, POINTS_DELTA_KEYS, context);
  return {
    change: nullableNum(obj, "change", context),
    net: nullableNum(obj, "net", context),
    verdict: str(obj, "verdict", context),
  };
}

function decodePoints(raw: unknown, context: string): Readonly<Record<string, FantasyTradePointsDelta>> {
  const obj = assertRecord(raw, context);
  const out: Record<string, FantasyTradePointsDelta> = {};
  for (const [key, value] of Object.entries(obj)) {
    out[key] = decodePointsDelta(value, `${context}.${key}`);
  }
  return out;
}

const SENSITIVITY_SCENARIO_KEYS = ["key", "label", "net"] as const;

function decodeScenario(raw: unknown, context: string): FantasyTradeSensitivityScenario {
  const obj = assertRecord(raw, context);
  checkShape(obj, SENSITIVITY_SCENARIO_KEYS, context);
  return { key: str(obj, "key", context), label: str(obj, "label", context), net: nullableNum(obj, "net", context) };
}

const SENSITIVITY_KEYS = ["base", "low", "high", "width", "lowScenario", "highScenario", "flips", "scenarios"] as const;

/** The empty sentinel a `null` wire `sensitivity` decodes to — see this module's docstring. */
const EMPTY_SENSITIVITY: FantasyTradeSensitivity = {
  base: null,
  low: null,
  high: null,
  width: null,
  lowScenario: "",
  highScenario: "",
  flips: false,
  scenarios: [],
};

function decodeSensitivity(raw: unknown, context: string): FantasyTradeSensitivity {
  const obj = assertRecord(raw, context);
  checkShape(obj, SENSITIVITY_KEYS, context);
  const scenariosRaw = obj.scenarios;
  if (!Array.isArray(scenariosRaw)) throw new Error(`${context}.scenarios: expected an array`);
  return {
    base: nullableNum(obj, "base", context),
    low: nullableNum(obj, "low", context),
    high: nullableNum(obj, "high", context),
    width: nullableNum(obj, "width", context),
    lowScenario: str(obj, "lowScenario", context),
    highScenario: str(obj, "highScenario", context),
    flips: bool(obj, "flips", context),
    scenarios: scenariosRaw.map((entry, index) => decodeScenario(entry, `${context}.scenarios[${index}]`)),
  };
}

const BANDS_KEYS = ["fair", "clear"] as const;

function decodeBands(raw: unknown, context: string): FantasyTradePayload["bands"] {
  const obj = assertRecord(raw, context);
  checkShape(obj, BANDS_KEYS, context);
  return { fair: num(obj, "fair", context), clear: num(obj, "clear", context) };
}

const PAYLOAD_KEYS = [
  "season",
  "seasonType",
  "puntCategories",
  "give",
  "get",
  "categories",
  "changeZ",
  "rosterAdjustment",
  "netZ",
  "replacementValue",
  "poolSpread",
  "verdict",
  "bands",
  "points",
  "sensitivity",
  "note",
] as const;

/** Decodes and validates a `fantasy_trade` resolve result's `payload`. Throws on any structural
 * mismatch rather than returning a best-effort partial object. */
export function decodeFantasyTradePayload(raw: unknown): FantasyTradePayload {
  const context = "fantasy_trade payload";
  const obj = assertRecord(raw, context);
  checkShape(obj, PAYLOAD_KEYS, context);
  const categoriesRaw = obj.categories;
  if (!Array.isArray(categoriesRaw)) throw new Error(`${context}.categories: expected an array`);
  const sensitivityRaw = obj.sensitivity;
  return {
    season: str(obj, "season", context),
    seasonType: str(obj, "seasonType", context),
    puntCategories: stringArray(obj, "puntCategories", context),
    give: decodeSide(obj.give, `${context}.give`),
    get: decodeSide(obj.get, `${context}.get`),
    categories: categoriesRaw.map((entry, index) => decodeCategoryDelta(entry, `${context}.categories[${index}]`)),
    changeZ: nullableNum(obj, "changeZ", context),
    rosterAdjustment: nullableNum(obj, "rosterAdjustment", context),
    netZ: nullableNum(obj, "netZ", context),
    replacementValue: nullableNum(obj, "replacementValue", context),
    poolSpread: nullableNum(obj, "poolSpread", context),
    verdict: str(obj, "verdict", context),
    bands: decodeBands(obj.bands, `${context}.bands`),
    points: decodePoints(obj.points, `${context}.points`),
    sensitivity: sensitivityRaw === null ? EMPTY_SENSITIVITY : decodeSensitivity(sensitivityRaw, `${context}.sensitivity`),
    note: nullableStr(obj, "note", context),
  };
}
