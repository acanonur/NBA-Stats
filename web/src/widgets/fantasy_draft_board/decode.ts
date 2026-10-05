/**
 * Runtime decoding for the `fantasy_draft_board` payload (`contracts/CONTRACT.md` §4;
 * `backend/nbastats/widgets/fantasy_draft_board.py`;
 * `contracts/fixtures/widget_fantasy_draft_board.json`).
 *
 * See `widgets/stat_tile/decode.ts`'s module docstring for why this hand-rolled shape check
 * exists at all and what its two halves are for: every required field must be present, and
 * every field the fixture actually carries must be known here.
 *
 * One column-level wrinkle the fixture makes plain: `backend/nbastats/widgets/fantasy_draft_board.py`'s
 * `_COLUMNS` tuple is a set of Python dict *literals*, and only the `"team"` column literal
 * carries an `align` key, and only `"score"` and the nine `z_*` columns carry a `signed` key —
 * the rest simply omit them rather than sending `false`/`"trailing"` explicitly. `api/types.ts`'s
 * `FantasyDraftBoardColumn` declares both as always-present for every *caller* downstream of this
 * decoder, so this is exactly where that gap is closed: `align` and `signed` are decoded as
 * OPTIONAL on the wire (present or entirely absent, never `null`) and defaulted here
 * (`"trailing"` / `false`) — never treated as unknown fields when they are missing.
 */
import type {
  Availability,
  FantasyDraftBoardColumn,
  FantasyDraftBoardPayload,
  FantasyDraftBoardRow,
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
        `${context}: unrecognized field "${key}" — the fantasy_draft_board decoder does not know ` +
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

const COLUMN_REQUIRED_KEYS = ["key", "label", "format", "group", "higherIsBetter", "punted"] as const;
const COLUMN_OPTIONAL_KEYS = ["align", "signed"] as const;
const COLUMN_ALL_KEYS: readonly string[] = [...COLUMN_REQUIRED_KEYS, ...COLUMN_OPTIONAL_KEYS];
const GROUPS = ["summary", "production", "impact"] as const;
const ALIGNS = ["leading", "trailing"] as const;

function decodeColumn(raw: unknown, context: string): FantasyDraftBoardColumn {
  const obj = assertRecord(raw, context);
  for (const key of Object.keys(obj)) {
    if (!COLUMN_ALL_KEYS.includes(key)) {
      throw new Error(
        `${context}: unrecognized field "${key}" — the fantasy_draft_board decoder does not know ` +
          "this field yet. Teach decode.ts about it (and this widget) rather than ignoring it.",
      );
    }
  }
  for (const key of COLUMN_REQUIRED_KEYS) {
    if (!(key in obj)) throw new Error(`${context}: missing required field "${key}"`);
  }
  const group = obj.group;
  if (typeof group !== "string" || !(GROUPS as readonly string[]).includes(group)) {
    throw new Error(`${context}.group: expected one of ${GROUPS.join(", ")}`);
  }
  const alignRaw = obj.align;
  let align: "leading" | "trailing";
  if (alignRaw === undefined) {
    align = "trailing";
  } else if (typeof alignRaw === "string" && (ALIGNS as readonly string[]).includes(alignRaw)) {
    align = alignRaw as "leading" | "trailing";
  } else {
    throw new Error(`${context}.align: expected one of ${ALIGNS.join(", ")}`);
  }
  const signed = "signed" in obj ? bool(obj, "signed", context) : false;
  return {
    key: str(obj, "key", context),
    label: str(obj, "label", context),
    format: nullableStr(obj, "format", context),
    group: group as FantasyDraftBoardColumn["group"],
    align,
    higherIsBetter: nullableBool(obj, "higherIsBetter", context),
    signed,
    punted: bool(obj, "punted", context),
  };
}

const ROW_KEYS = [
  "rank",
  "round",
  "pickInRound",
  "player",
  "baselineRank",
  "totalZ",
  "valueOverReplacement",
  "suggestion",
  "espnPoints",
  "yahooPoints",
  "values",
  "fills",
  "reason",
  "availability",
] as const;

function decodeValues(raw: unknown, context: string): Readonly<Record<string, number | string | null>> {
  const obj = assertRecord(raw, context);
  const out: Record<string, number | string | null> = {};
  for (const [key, value] of Object.entries(obj)) {
    if (value === null || typeof value === "number" || typeof value === "string") {
      out[key] = value;
      continue;
    }
    throw new Error(`${context}.${key}: expected a number, a string, or null`);
  }
  return out;
}

function decodeRow(raw: unknown, context: string): FantasyDraftBoardRow {
  const obj = assertRecord(raw, context);
  checkShape(obj, ROW_KEYS, context);
  return {
    rank: num(obj, "rank", context),
    round: num(obj, "round", context),
    pickInRound: num(obj, "pickInRound", context),
    player: decodePlayerRef(obj.player, `${context}.player`),
    baselineRank: nullableNum(obj, "baselineRank", context),
    totalZ: nullableNum(obj, "totalZ", context),
    valueOverReplacement: nullableNum(obj, "valueOverReplacement", context),
    suggestion: nullableNum(obj, "suggestion", context),
    espnPoints: nullableNum(obj, "espnPoints", context),
    yahooPoints: nullableNum(obj, "yahooPoints", context),
    values: decodeValues(obj.values, `${context}.values`),
    fills: stringArray(obj, "fills", context),
    reason: nullableStr(obj, "reason", context),
    availability: availability(obj, "availability", context),
  };
}

const NEXT_PICK_KEYS = ["overall", "round", "pickInRound"] as const;

function decodeNextPick(raw: unknown, context: string): FantasyDraftBoardPayload["nextPick"] {
  const obj = assertRecord(raw, context);
  checkShape(obj, NEXT_PICK_KEYS, context);
  return {
    overall: num(obj, "overall", context),
    round: num(obj, "round", context),
    pickInRound: num(obj, "pickInRound", context),
  };
}

const PAYLOAD_KEYS = [
  "season",
  "seasonType",
  "scoring",
  "categories",
  "puntCategories",
  "teams",
  "rosterSpots",
  "nextPick",
  "poolSize",
  "replacementValue",
  "weakestCategories",
  "rosterStrength",
  "columns",
  "rows",
  "note",
] as const;

/** Decodes and validates a `fantasy_draft_board` resolve result's `payload`. Throws on any
 * structural mismatch rather than returning a best-effort partial object. */
export function decodeFantasyDraftBoardPayload(raw: unknown): FantasyDraftBoardPayload {
  const context = "fantasy_draft_board payload";
  const obj = assertRecord(raw, context);
  checkShape(obj, PAYLOAD_KEYS, context);
  const columnsRaw = obj.columns;
  const rowsRaw = obj.rows;
  if (!Array.isArray(columnsRaw)) throw new Error(`${context}.columns: expected an array`);
  if (!Array.isArray(rowsRaw)) throw new Error(`${context}.rows: expected an array`);
  const nextPickRaw = obj.nextPick;
  return {
    season: str(obj, "season", context),
    seasonType: str(obj, "seasonType", context),
    scoring: str(obj, "scoring", context),
    categories: stringArray(obj, "categories", context),
    puntCategories: stringArray(obj, "puntCategories", context),
    teams: num(obj, "teams", context),
    rosterSpots: num(obj, "rosterSpots", context),
    nextPick: nextPickRaw === null ? null : decodeNextPick(nextPickRaw, `${context}.nextPick`),
    poolSize: num(obj, "poolSize", context),
    replacementValue: nullableNum(obj, "replacementValue", context),
    weakestCategories: stringArray(obj, "weakestCategories", context),
    rosterStrength: metricMap(obj, "rosterStrength", context),
    columns: columnsRaw.map((entry, index) => decodeColumn(entry, `${context}.columns[${index}]`)),
    rows: rowsRaw.map((entry, index) => decodeRow(entry, `${context}.rows[${index}]`)),
    note: nullableStr(obj, "note", context),
  };
}
