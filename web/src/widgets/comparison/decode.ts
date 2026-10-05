/**
 * Runtime decoding for the `comparison` payload (`contracts/CONTRACT.md` §4;
 * `backend/nbastats/widgets/comparison.py`; `contracts/fixtures/widget_comparison.json`).
 *
 * `WidgetContainer` hands every widget its payload as `unknown` (the frozen contract in
 * `web/CONTRACT-FOR-AGENTS.md` keeps `WidgetViewProps.payload` untyped on purpose), so this
 * module is the one place `comparison` turns that `unknown` into a `ComparisonPayload` it can
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
  ComparisonPayload,
  ComparisonSubject,
  MetricDescriptor,
  MetricDomain,
  MetricEraSpec,
  MetricValue,
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
        `${context}: unrecognized field "${key}" — the comparison decoder does not know this ` +
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

function strArray(obj: Record<string, unknown>, key: string, context: string): readonly string[] {
  const value = obj[key];
  if (!Array.isArray(value) || value.some((entry) => typeof entry !== "string")) {
    throw new Error(`${context}.${key}: expected an array of strings`);
  }
  return value as readonly string[];
}

const AVAILABILITIES: readonly Availability[] = ["full", "estimated", "partial", "unavailable"];

function availability(obj: Record<string, unknown>, key: string, context: string): Availability {
  const value = obj[key];
  if (typeof value !== "string" || !(AVAILABILITIES as readonly string[]).includes(value)) {
    throw new Error(`${context}.${key}: expected one of ${AVAILABILITIES.join(", ")}`);
  }
  return value as Availability;
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

function decodeMetricDescriptor(raw: unknown, context: string): MetricDescriptor {
  const obj = assertRecord(raw, context);
  checkShape(obj, METRIC_DESCRIPTOR_KEYS, context);
  const domainRaw = obj.domain;
  return {
    key: str(obj, "key", context),
    name: str(obj, "name", context),
    shortName: str(obj, "shortName", context),
    category: str(obj, "category", context),
    format: str(obj, "format", context),
    higherIsBetter: bool(obj, "higherIsBetter", context),
    scope: strArray(obj, "scope", context),
    availability: decodeEraSpec(obj.availability, `${context}.availability`),
    domain: domainRaw === null ? null : decodeDomain(domainRaw, `${context}.domain`),
    glossary: str(obj, "glossary", context),
  };
}

const SUBJECT_KEYS = ["player", "colorIndex", "values"] as const;

function decodeSubject(raw: unknown, context: string): ComparisonSubject {
  const obj = assertRecord(raw, context);
  checkShape(obj, SUBJECT_KEYS, context);
  const valuesRaw = obj.values;
  if (!Array.isArray(valuesRaw)) throw new Error(`${context}.values: expected an array`);
  return {
    player: decodePlayerRef(obj.player, `${context}.player`),
    colorIndex: num(obj, "colorIndex", context),
    values: valuesRaw.map((entry, index) => decodeMetricValue(entry, `${context}.values[${index}]`)),
  };
}

const NORMALIZATIONS = ["percentile", "raw"] as const;
type Normalization = (typeof NORMALIZATIONS)[number];

function normalization(obj: Record<string, unknown>, key: string, context: string): Normalization {
  const value = obj[key];
  if (typeof value !== "string" || !(NORMALIZATIONS as readonly string[]).includes(value)) {
    throw new Error(`${context}.${key}: expected one of ${NORMALIZATIONS.join(", ")}`);
  }
  return value as Normalization;
}

const STYLES = ["bars", "radar", "table"] as const;
type ComparisonStyle = (typeof STYLES)[number];

function style(obj: Record<string, unknown>, key: string, context: string): ComparisonStyle {
  const value = obj[key];
  if (typeof value !== "string" || !(STYLES as readonly string[]).includes(value)) {
    throw new Error(`${context}.${key}: expected one of ${STYLES.join(", ")}`);
  }
  return value as ComparisonStyle;
}

const PAYLOAD_KEYS = ["season", "seasonType", "normalization", "style", "metrics", "subjects"] as const;

/** Decodes and validates a `comparison` resolve result's `payload`. Throws (rather than
 * returning a best-effort partial object) on any structural mismatch — `WidgetContainer` only
 * ever calls a widget component when `result.status` is `"ok"` or `"partial"`, so a payload
 * that fails to decode there is a real contract break the widget should surface loudly during
 * development, not paper over. */
export function decodeComparisonPayload(raw: unknown): ComparisonPayload {
  const context = "comparison payload";
  const obj = assertRecord(raw, context);
  checkShape(obj, PAYLOAD_KEYS, context);
  const metricsRaw = obj.metrics;
  const subjectsRaw = obj.subjects;
  if (!Array.isArray(metricsRaw)) throw new Error(`${context}.metrics: expected an array`);
  if (!Array.isArray(subjectsRaw)) throw new Error(`${context}.subjects: expected an array`);
  return {
    season: str(obj, "season", context),
    seasonType: str(obj, "seasonType", context),
    normalization: normalization(obj, "normalization", context),
    style: style(obj, "style", context),
    metrics: metricsRaw.map((entry, index) => decodeMetricDescriptor(entry, `${context}.metrics[${index}]`)),
    subjects: subjectsRaw.map((entry, index) => decodeSubject(entry, `${context}.subjects[${index}]`)),
  };
}
