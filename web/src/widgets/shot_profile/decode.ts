/**
 * Runtime decoding for the `shot_profile` payload (`contracts/CONTRACT.md` §4;
 * `backend/nbastats/widgets/shot_profile.py`; `contracts/fixtures/widget_shot_profile.json`).
 *
 * `WidgetContainer` hands every widget its payload as `unknown` (the frozen contract in
 * `web/CONTRACT-FOR-AGENTS.md` keeps `WidgetViewProps.payload` untyped on purpose), so this
 * module is the one place `shot_profile` turns that `unknown` into a `ShotProfilePayload` it can
 * actually render. It does real, if shallow, structural checks rather than a bare `as` cast:
 * every object shape below is checked against its own exact key set, so a field the backend
 * adds or removes shows up as a thrown error in `decode.test.ts` — a red test — instead of a
 * silently-ignored (or silently-`undefined`) field reaching the view. See that test file for
 * the fixture-driven half of this guarantee.
 *
 * Deliberately NOT a general-purpose schema library: there is no such dependency in this
 * project (WEB_DESIGN.md's "no new npm package the design does not name"), and a hand-rolled
 * decoder that mirrors `api/types.ts` field-for-field is a few dozen lines, not a framework.
 *
 * A pre-1996-97 season (`backend/nbastats/widgets/shot_profile.py`'s `SHOT_CHARTS_FROM`) comes
 * back with all five zones present but every number `null` — this decoder does not special-case
 * that shape at all, which is the point: a zone with every field `null` decodes exactly like any
 * other zone, and it is the *view* layer's job to read "no finite number anywhere" as "not
 * tracked", never as a zero-shooting player (see this directory's `index.tsx`).
 */
import type { PlayerRef, ShotProfilePayload, ShotZone, ShotZoneKey, SubjectRef, SubjectType, TeamRef } from "../../api/types";

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
        `${context}: unrecognized field "${key}" — the shot_profile decoder does not know this ` +
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

const TEAM_REF_KEYS = ["teamId", "abbr", "name", "city", "nickname", "conference", "division"] as const;

function decodeTeamRef(raw: unknown, context: string): TeamRef {
  const obj = assertRecord(raw, context);
  checkShape(obj, TEAM_REF_KEYS, context);
  const teamId = obj.teamId;
  if (typeof teamId !== "number") throw new Error(`${context}.teamId: expected a number`);
  return {
    teamId,
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
  const playerId = obj.playerId;
  if (typeof playerId !== "number") throw new Error(`${context}.playerId: expected a number`);
  return {
    playerId,
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

function subjectType(obj: Record<string, unknown>, key: string, context: string): SubjectType {
  const value = obj[key];
  if (typeof value !== "string" || !(SUBJECT_TYPES as readonly string[]).includes(value)) {
    throw new Error(`${context}.${key}: expected one of ${SUBJECT_TYPES.join(", ")}`);
  }
  return value as SubjectType;
}

const SUBJECT_REF_KEYS = ["type", "player", "team"] as const;

function decodeSubjectRef(raw: unknown, context: string): SubjectRef {
  const obj = assertRecord(raw, context);
  checkShape(obj, SUBJECT_REF_KEYS, context);
  const playerRaw = obj.player;
  const teamRaw = obj.team;
  return {
    type: subjectType(obj, "type", context),
    player: playerRaw === null ? null : decodePlayerRef(playerRaw, `${context}.player`),
    team: teamRaw === null ? null : decodeTeamRef(teamRaw, `${context}.team`),
  };
}

const SHOT_ZONE_KEYS: readonly ShotZoneKey[] = [
  "rim",
  "paint_non_rim",
  "mid_range",
  "corner_three",
  "above_break_three",
];

function shotZoneKey(obj: Record<string, unknown>, key: string, context: string): ShotZoneKey {
  const value = obj[key];
  if (typeof value !== "string" || !(SHOT_ZONE_KEYS as readonly string[]).includes(value)) {
    throw new Error(`${context}.${key}: expected one of ${SHOT_ZONE_KEYS.join(", ")}`);
  }
  return value as ShotZoneKey;
}

const ZONE_KEYS = [
  "zone",
  "label",
  "fga",
  "fgPct",
  "shareOfFga",
  "pointsPerShot",
  "leagueFgPct",
  "leagueShareOfFga",
] as const;

function decodeShotZone(raw: unknown, context: string): ShotZone {
  const obj = assertRecord(raw, context);
  checkShape(obj, ZONE_KEYS, context);
  return {
    zone: shotZoneKey(obj, "zone", context),
    label: str(obj, "label", context),
    fga: nullableNum(obj, "fga", context),
    fgPct: nullableNum(obj, "fgPct", context),
    shareOfFga: nullableNum(obj, "shareOfFga", context),
    pointsPerShot: nullableNum(obj, "pointsPerShot", context),
    leagueFgPct: nullableNum(obj, "leagueFgPct", context),
    leagueShareOfFga: nullableNum(obj, "leagueShareOfFga", context),
  };
}

const PAYLOAD_KEYS = [
  "subject",
  "season",
  "seasonType",
  "zones",
  "threePointRate",
  "freeThrowRate",
  "note",
] as const;

/** Decodes and validates a `shot_profile` resolve result's `payload`. Throws (rather than
 * returning a best-effort partial object) on any structural mismatch — `WidgetContainer` only
 * ever calls a widget component when `result.status` is `"ok"` or `"partial"`, so a payload
 * that fails to decode there is a real contract break the widget should surface loudly during
 * development, not paper over. */
export function decodeShotProfilePayload(raw: unknown): ShotProfilePayload {
  const context = "shot_profile payload";
  const obj = assertRecord(raw, context);
  checkShape(obj, PAYLOAD_KEYS, context);
  const zonesRaw = obj.zones;
  if (!Array.isArray(zonesRaw)) throw new Error(`${context}.zones: expected an array`);
  return {
    subject: decodeSubjectRef(obj.subject, `${context}.subject`),
    season: str(obj, "season", context),
    seasonType: str(obj, "seasonType", context),
    zones: zonesRaw.map((entry, index) => decodeShotZone(entry, `${context}.zones[${index}]`)),
    threePointRate: nullableNum(obj, "threePointRate", context),
    freeThrowRate: nullableNum(obj, "freeThrowRate", context),
    note: nullableStr(obj, "note", context),
  };
}
