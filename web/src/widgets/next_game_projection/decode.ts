/**
 * Runtime decoding for the `next_game_projection` payload (`contracts/CONTRACT.md` §4;
 * `backend/nbastats/widgets/next_game_projection.py`;
 * `contracts/fixtures/widget_next_game_projection.json`).
 *
 * See `widgets/stat_tile/decode.ts`'s module docstring for why this hand-rolled shape check
 * exists at all and what its two halves are for: every required field must be present, and
 * every field the fixture actually carries must be known here — the second half is what turns a
 * new server key into a red test in `decode.test.ts` instead of a silently-dropped field.
 */
import type {
  Availability,
  MetricDescriptor,
  MetricDomain,
  MetricEraSpec,
  MetricMap,
  NextGameProjectionCombo,
  NextGameProjectionFactor,
  NextGameProjectionGame,
  NextGameProjectionLine,
  NextGameProjectionMethod,
  NextGameProjectionMinutes,
  NextGameProjectionPayload,
  PlayerRef,
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

function checkShape(obj: Record<string, unknown>, keys: readonly string[], context: string): void {
  const known = new Set(keys);
  for (const key of Object.keys(obj)) {
    if (!known.has(key)) {
      throw new Error(
        `${context}: unrecognized field "${key}" — the next_game_projection decoder does not know ` +
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

function nullableBool(obj: Record<string, unknown>, key: string, context: string): boolean | null {
  const value = obj[key];
  if (value === null) return null;
  if (typeof value !== "boolean") throw new Error(`${context}.${key}: expected a boolean or null`);
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

function metricMap(obj: Record<string, unknown>, key: string, context: string): MetricMap {
  const raw = assertRecord(obj[key], `${context}.${key}`);
  const out: Record<string, number | null> = {};
  for (const [metricKey, value] of Object.entries(raw)) {
    if (value === null) {
      out[metricKey] = null;
      continue;
    }
    if (typeof value !== "number") {
      throw new Error(`${context}.${key}.${metricKey}: expected a number or null`);
    }
    out[metricKey] = value;
  }
  return out;
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

export function decodeMetricDescriptor(raw: unknown, context: string): MetricDescriptor {
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

const GAME_KEYS = [
  "gameId",
  "date",
  "opponentAbbr",
  "opponent",
  "isHome",
  "restDays",
  "isBackToBack",
  "opponentDefRtg",
  "expectedPace",
] as const;

function decodeGame(raw: unknown, context: string): NextGameProjectionGame {
  const obj = assertRecord(raw, context);
  checkShape(obj, GAME_KEYS, context);
  return {
    gameId: str(obj, "gameId", context),
    date: str(obj, "date", context),
    opponentAbbr: str(obj, "opponentAbbr", context),
    opponent: decodeTeamRef(obj.opponent, `${context}.opponent`),
    isHome: bool(obj, "isHome", context),
    restDays: nullableNum(obj, "restDays", context),
    isBackToBack: nullableBool(obj, "isBackToBack", context),
    opponentDefRtg: nullableNum(obj, "opponentDefRtg", context),
    expectedPace: nullableNum(obj, "expectedPace", context),
  };
}

const MINUTES_KEYS = ["value", "displayValue", "halfLifeGames", "seasonAverage", "low", "high"] as const;

function decodeMinutes(raw: unknown, context: string): NextGameProjectionMinutes {
  const obj = assertRecord(raw, context);
  checkShape(obj, MINUTES_KEYS, context);
  return {
    value: nullableNum(obj, "value", context),
    displayValue: str(obj, "displayValue", context),
    halfLifeGames: nullableNum(obj, "halfLifeGames", context),
    seasonAverage: nullableNum(obj, "seasonAverage", context),
    low: nullableNum(obj, "low", context),
    high: nullableNum(obj, "high", context),
  };
}

const LINE_KEYS = [
  "metric",
  "descriptor",
  "mean",
  "displayValue",
  "low",
  "high",
  "intervalLevel",
  "seasonAverage",
  "delta",
  "ratePerMinute",
  "shrinkageK",
  "exposureMinutes",
  "shrinkageWeight",
  "halfLifeGames",
  "dispersionAlpha",
  "dispersionMultiplier",
  "availability",
] as const;

function decodeLine(raw: unknown, context: string): NextGameProjectionLine {
  const obj = assertRecord(raw, context);
  checkShape(obj, LINE_KEYS, context);
  return {
    metric: str(obj, "metric", context),
    descriptor: decodeMetricDescriptor(obj.descriptor, `${context}.descriptor`),
    mean: num(obj, "mean", context),
    displayValue: str(obj, "displayValue", context),
    low: nullableNum(obj, "low", context),
    high: nullableNum(obj, "high", context),
    intervalLevel: nullableNum(obj, "intervalLevel", context),
    seasonAverage: nullableNum(obj, "seasonAverage", context),
    delta: nullableNum(obj, "delta", context),
    ratePerMinute: nullableNum(obj, "ratePerMinute", context),
    shrinkageK: nullableNum(obj, "shrinkageK", context),
    exposureMinutes: nullableNum(obj, "exposureMinutes", context),
    shrinkageWeight: nullableNum(obj, "shrinkageWeight", context),
    halfLifeGames: nullableNum(obj, "halfLifeGames", context),
    dispersionAlpha: nullableNum(obj, "dispersionAlpha", context),
    dispersionMultiplier: nullableNum(obj, "dispersionMultiplier", context),
    availability: availability(obj, "availability", context),
  };
}

const FACTOR_KEYS = ["key", "label", "value", "explanation", "contributions"] as const;

function decodeFactor(raw: unknown, context: string): NextGameProjectionFactor {
  const obj = assertRecord(raw, context);
  checkShape(obj, FACTOR_KEYS, context);
  return {
    key: str(obj, "key", context),
    label: str(obj, "label", context),
    value: num(obj, "value", context),
    explanation: str(obj, "explanation", context),
    contributions: metricMap(obj, "contributions", context),
  };
}

const COMBO_KEYS = ["label", "mean", "sd", "sdIfIndependent", "inflation", "low", "high", "correlation"] as const;

function decodeCorrelation(raw: unknown, context: string): readonly (readonly number[])[] {
  if (!Array.isArray(raw)) throw new Error(`${context}: expected an array`);
  return raw.map((row, rowIndex) => {
    if (!Array.isArray(row)) throw new Error(`${context}[${rowIndex}]: expected an array`);
    return row.map((entry, colIndex) => {
      if (typeof entry !== "number") throw new Error(`${context}[${rowIndex}][${colIndex}]: expected a number`);
      return entry;
    });
  });
}

function decodeCombo(raw: unknown, context: string): NextGameProjectionCombo {
  const obj = assertRecord(raw, context);
  checkShape(obj, COMBO_KEYS, context);
  return {
    label: str(obj, "label", context),
    mean: num(obj, "mean", context),
    sd: num(obj, "sd", context),
    sdIfIndependent: num(obj, "sdIfIndependent", context),
    inflation: num(obj, "inflation", context),
    low: nullableNum(obj, "low", context),
    high: nullableNum(obj, "high", context),
    correlation: decodeCorrelation(obj.correlation, `${context}.correlation`),
  };
}

const METHOD_KEYS = [
  "summary",
  "minutesHalfLifeGames",
  "correlationApplied",
  "dispersionShrinkageGames",
] as const;

function decodeMethod(raw: unknown, context: string): NextGameProjectionMethod {
  const obj = assertRecord(raw, context);
  checkShape(obj, METHOD_KEYS, context);
  return {
    summary: str(obj, "summary", context),
    minutesHalfLifeGames: num(obj, "minutesHalfLifeGames", context),
    correlationApplied: bool(obj, "correlationApplied", context),
    dispersionShrinkageGames: num(obj, "dispersionShrinkageGames", context),
  };
}

const PAYLOAD_KEYS = [
  "player",
  "game",
  "projectedMinutes",
  "lines",
  "factors",
  "combo",
  "method",
  "notes",
] as const;

function decodeLines(raw: unknown, context: string): readonly NextGameProjectionLine[] {
  if (!Array.isArray(raw)) throw new Error(`${context}: expected an array`);
  return raw.map((entry, index) => decodeLine(entry, `${context}[${index}]`));
}

function decodeFactors(raw: unknown, context: string): readonly NextGameProjectionFactor[] {
  if (!Array.isArray(raw)) throw new Error(`${context}: expected an array`);
  return raw.map((entry, index) => decodeFactor(entry, `${context}[${index}]`));
}

/** Decodes and validates a `next_game_projection` resolve result's `payload`. Throws on any
 * structural mismatch rather than returning a best-effort partial object. */
export function decodeNextGameProjectionPayload(raw: unknown): NextGameProjectionPayload {
  const context = "next_game_projection payload";
  const obj = assertRecord(raw, context);
  checkShape(obj, PAYLOAD_KEYS, context);
  const playerRaw = obj.player;
  const gameRaw = obj.game;
  const comboRaw = obj.combo;
  return {
    player: playerRaw === null ? null : decodePlayerRef(playerRaw, `${context}.player`),
    game: gameRaw === null ? null : decodeGame(gameRaw, `${context}.game`),
    projectedMinutes: decodeMinutes(obj.projectedMinutes, `${context}.projectedMinutes`),
    lines: decodeLines(obj.lines, `${context}.lines`),
    factors: decodeFactors(obj.factors, `${context}.factors`),
    combo: comboRaw === null ? null : decodeCombo(comboRaw, `${context}.combo`),
    method: decodeMethod(obj.method, `${context}.method`),
    notes: stringArray(obj, "notes", context),
  };
}
