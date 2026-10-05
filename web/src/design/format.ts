/**
 * Every number, date and season string the web app renders goes through here — the TypeScript
 * counterpart of `ios/NBAStats/Core/Formatting.swift`, kept deliberately parallel to it function
 * for function so a reviewer who knows one can find their way around the other.
 *
 * Two rules drive every function below, and neither is negotiable:
 *
 *   1. A missing value renders as {@link EM_DASH} and *never* as `0`, `""`, or `false`. This is
 *      the house rule ("era-unavailable stats are null and render as an em dash, never 0"), and
 *      it is why every formatter here takes `number | null | undefined` rather than `number`:
 *      the caller is never in a position to have already lost the distinction.
 *   2. A fraction in `[0, 1]` is a percentage — `format.ts` multiplies by 100 itself
 *      (`contracts/metrics.json#/formats` and CONTRACT.md §6 agree on this), so a caller must
 *      never pre-multiply before calling {@link formatValue}.
 *
 * `formatValue`'s eight branches are pinned byte-for-byte against
 * `contracts/fixtures/format_cases.json`, which is itself asserted from this file, from
 * `Formatting.swift`, and from `backend/nbastats/catalog.py::format_value` — three languages,
 * one table. **`en-US` is fixed rather than derived from the browser's locale on purpose**: the
 * fixture's expected strings are ASCII (`"61.5%"`, not `"61,5 %"`), and formatting them against
 * whatever locale a CI runner or a reader's browser happens to report would make this file pass
 * in one environment and fail in another for a reason that has nothing to do with a real bug.
 * `Formatting.swift` reads `Locale.current` because an iOS reader's own device locale is a
 * reasonable thing for a native app to honour; a web app that must match one pinned fixture
 * across every browser and CI runner cannot make the same trade.
 */
import type { MetricFormat } from "../generated/contracts";

/** What an absent value looks like everywhere in the app. Never `0`. */
export const EM_DASH = "—";

const LEAGUE_TIME_ZONE = "America/New_York";

// ------------------------------------------------------------------------------------------
// Numbers
// ------------------------------------------------------------------------------------------

type Finite = number;

function isUsableNumber(value: number | null | undefined): value is Finite {
  return value !== null && value !== undefined && Number.isFinite(value);
}

/** One `Intl.NumberFormat` per distinct option set, built once and reused — mirrors
 * `Formatting.swift`'s own `FormatterBox` cache, for the same reason: these are not free to
 * construct and every widget on a dashboard asks for one. */
const numberFormatterCache = new Map<string, Intl.NumberFormat>();

function numberFormatter(options: Intl.NumberFormatOptions): Intl.NumberFormat {
  const key = JSON.stringify(options);
  let formatter = numberFormatterCache.get(key);
  if (!formatter) {
    formatter = new Intl.NumberFormat("en-US", options);
    numberFormatterCache.set(key, formatter);
  }
  return formatter;
}

/**
 * Formats a raw value in the metric's own native unit, exactly as
 * `contracts/metrics.json#/formats` and `Formatting.value(_:format:)` define it. Percentages
 * arrive as fractions in `[0, 1]` and are multiplied by 100 here.
 *
 * `null`, `undefined`, and non-finite input (`NaN`, `Infinity`) all render {@link EM_DASH} —
 * never `0`.
 */
export function formatValue(value: number | null | undefined, format: MetricFormat): string {
  if (!isUsableNumber(value)) return EM_DASH;
  switch (format) {
    case "integer":
      return numberFormatter({
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
        useGrouping: true,
      }).format(value);
    case "decimal1":
      return numberFormatter({
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
        useGrouping: true,
      }).format(value);
    case "decimal2":
      return numberFormatter({
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
        useGrouping: true,
      }).format(value);
    case "percent1":
      return (
        numberFormatter({
          minimumFractionDigits: 1,
          maximumFractionDigits: 1,
          useGrouping: false,
        }).format(value * 100) + "%"
      );
    case "percent2":
      return (
        numberFormatter({
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
          useGrouping: false,
        }).format(value * 100) + "%"
      );
    case "rating1":
      return numberFormatter({
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
        useGrouping: false,
      }).format(value);
    case "plusMinus1":
      // Always signed, except at exactly zero (`-0` included: `signDisplay: "exceptZero"`
      // treats negative zero as zero, matching Swift's `value != 0`), where a sign reads as
      // noise rather than information.
      return numberFormatter({
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
        useGrouping: false,
        signDisplay: "exceptZero",
      }).format(value);
    case "minutes":
      return numberFormatter({
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
        useGrouping: false,
      }).format(value);
    default: {
      // Exhaustiveness guard: a MetricFormat branch added upstream without a matching case
      // here fails typecheck at this line rather than silently falling through.
      const neverFormat: never = format;
      throw new Error(`format.ts: unhandled MetricFormat ${String(neverFormat)}`);
    }
  }
}

/**
 * The client-side equivalent of `HardwoodNumberFormat.string` (`Components.swift`): forwards to
 * {@link formatValue} — never a second, independently-written number path — and optionally
 * forces a leading `+` on a positive value that `formatValue` would not otherwise sign (every
 * format except `plusMinus1`, which already signs itself). Zero is never signed.
 */
export function formatSigned(
  value: number | null | undefined,
  format: MetricFormat,
  signed = false,
): string {
  const text = formatValue(value, format);
  if (!signed || !isUsableNumber(value) || value <= 0 || text.startsWith("+")) return text;
  return "+" + text;
}

/**
 * A delta's *magnitude*, for a chip that already carries the direction in an arrow and a tint.
 * `plusMinus1` signs every non-zero value, so a bad night's magnitude is rendered as a plain
 * decimal (`decimal1`) rather than double-signing a value already headed for a red down arrow.
 */
export function formatMagnitude(value: number, format: MetricFormat): string {
  return formatValue(Math.abs(value), format === "plusMinus1" ? "decimal1" : format);
}

/** A decimal with an explicit number of places, for values with no metric descriptor. */
export function formatDecimal(
  value: number | null | undefined,
  places = 1,
  signed = false,
): string {
  if (!isUsableNumber(value)) return EM_DASH;
  return numberFormatter({
    minimumFractionDigits: Math.max(0, places),
    maximumFractionDigits: Math.max(0, places),
    useGrouping: true,
    signDisplay: signed ? "exceptZero" : "auto",
  }).format(value);
}

/** A whole number with grouping separators, for counts and totals. */
export function formatInteger(value: number | null | undefined): string {
  if (!isUsableNumber(value)) return EM_DASH;
  return numberFormatter({
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
    useGrouping: true,
  }).format(value);
}

/** A fraction in `[0, 1]` rendered as a percentage. */
export function formatPercent(value: number | null | undefined, places = 1): string {
  if (!isUsableNumber(value)) return EM_DASH;
  return (
    numberFormatter({
      minimumFractionDigits: Math.max(0, places),
      maximumFractionDigits: Math.max(0, places),
      useGrouping: false,
    }).format(value * 100) + "%"
  );
}

const ORDINAL_RULES = new Intl.PluralRules("en-US", { type: "ordinal" });
const ORDINAL_SUFFIX: Record<string, string> = { one: "st", two: "nd", few: "rd", other: "th" };

/** `12` becomes `"12th"`, for ranks. */
export function formatOrdinal(rank: number | null | undefined): string {
  if (!isUsableNumber(rank)) return EM_DASH;
  const whole = Math.trunc(rank);
  return `${whole}${ORDINAL_SUFFIX[ORDINAL_RULES.select(whole)] ?? "th"}`;
}

/**
 * A percentile in `[0, 1]` rendered as a rank-like string: `0.93` becomes `"93rd"`. Clamped in
 * floating-point space *before* rounding to an integer, mirroring
 * `Formatting.percentile(_:)`'s own comment: an out-of-range value must never reach a step that
 * could misbehave on it.
 */
export function formatPercentile(value: number | null | undefined): string {
  if (!isUsableNumber(value)) return EM_DASH;
  const scaled = Math.min(Math.max(value * 100, 0), 100);
  return formatOrdinal(Math.round(scaled));
}

// ------------------------------------------------------------------------------------------
// Dates
// ------------------------------------------------------------------------------------------

/** Parses an RFC-3339 UTC timestamp such as `"2026-01-03T07:12:44Z"`, with or without
 * fractional seconds. `null` for anything empty or unparseable. */
export function parseTimestamp(value: string | null | undefined): Date | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** Renders a date as the RFC-3339 UTC timestamp the API and the layout document use — no
 * fractional seconds, matching `ISO8601DateFormatter.withInternetDateTime`. */
export function timestampString(date: Date): string {
  return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}

const CALENDAR_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/**
 * Parses an ISO-8601 calendar date such as `"2026-01-02"`, interpreted in US Eastern — the
 * league's scheduling day — falling back to a full timestamp. Anchored at 12:00 UTC rather than
 * midnight: US Eastern is UTC-4 or UTC-5 depending on daylight saving, so noon UTC always falls
 * on the same Eastern calendar day the string named, with no per-date DST branch to get wrong.
 */
export function parseGameDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const match = CALENDAR_DATE.exec(value);
  if (match) {
    const anchored = new Date(`${match[1]}-${match[2]}-${match[3]}T12:00:00Z`);
    return Number.isNaN(anchored.getTime()) ? null : anchored;
  }
  return parseTimestamp(value);
}

const dateTimeFormatterCache = new Map<string, Intl.DateTimeFormat>();

function dateTimeFormatter(options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = JSON.stringify(options);
  let formatter = dateTimeFormatterCache.get(key);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("en-US", { timeZone: LEAGUE_TIME_ZONE, ...options });
    dateTimeFormatterCache.set(key, formatter);
  }
  return formatter;
}

/** `"2026-01-02"` becomes `"Jan 2"`. Unparseable input is echoed back rather than blanked —
 * matching `Formatting.shortGameDate(_:)`, which prefers a strange-looking string to a silent
 * dash when the input was not the calendar date it expected. */
export function shortGameDate(value: string | null | undefined): string {
  if (!value) return EM_DASH;
  const date = parseGameDate(value);
  if (!date) return value;
  return dateTimeFormatter({ month: "short", day: "numeric" }).format(date);
}

/** `"2026-01-02"` becomes `"Fri, Jan 2"`. */
export function mediumGameDate(value: string | null | undefined): string {
  if (!value) return EM_DASH;
  const date = parseGameDate(value);
  if (!date) return value;
  return dateTimeFormatter({ weekday: "short", month: "short", day: "numeric" }).format(date);
}

const RELATIVE_UNITS: ReadonlyArray<readonly [Intl.RelativeTimeFormatUnit, number]> = [
  ["year", 31_536_000],
  ["month", 2_592_000],
  ["week", 604_800],
  ["day", 86_400],
  ["hour", 3_600],
  ["minute", 60],
];

const RELATIVE_FORMATTER = new Intl.RelativeTimeFormat("en-US", {
  numeric: "always",
  style: "long",
});

/** A short relative description of a past instant: `"4 minutes ago"`. */
export function formatRelative(date: Date, now: Date = new Date()): string {
  const seconds = (now.getTime() - date.getTime()) / 1000;
  if (Math.abs(seconds) < 60) return "just now";
  for (const [unit, secondsInUnit] of RELATIVE_UNITS) {
    if (Math.abs(seconds) >= secondsInUnit || unit === "minute") {
      return RELATIVE_FORMATTER.format(Math.round(-seconds / secondsInUnit), unit);
    }
  }
  return "just now";
}

/** The freshness line under a dashboard: `"Updated 4 minutes ago"`. */
export function formatUpdatedAt(date: Date | null | undefined, now: Date = new Date()): string {
  if (!date) return "Not updated yet";
  return `Updated ${formatRelative(date, now)}`;
}

// ------------------------------------------------------------------------------------------
// Seasons
// ------------------------------------------------------------------------------------------

/** `"2025-26"` stays itself; the request tokens get readable names. */
export function seasonDisplay(season: string | null | undefined): string {
  if (!season) return EM_DASH;
  switch (season.toLowerCase()) {
    case "latest":
    case "current":
      return "Current Season";
    case "career":
      return "Career";
    case "all_time":
    case "alltime":
      return "All Time";
    default:
      return season;
  }
}

/** The first calendar year of an NBA season string: `"1996-97"` is `1996`. `null` for tokens
 * such as `"latest"` or `"career"`. */
export function seasonStartYear(season: string | null | undefined): number | null {
  if (!season) return null;
  const head = season.slice(0, 4);
  if (head.length !== 4 || !/^\d{4}$/.test(head)) return null;
  return Number.parseInt(head, 10);
}

/** `"PerGame"` becomes `"Per Game"`. */
export function perModeDisplay(perMode: string): string {
  switch (perMode) {
    case "PerGame":
      return "Per Game";
    case "Totals":
      return "Totals";
    case "Per36":
      return "Per 36";
    case "Per100":
      return "Per 100";
    default:
      return perMode;
  }
}

/** `"2025-26"` and `"Regular Season"` become `"2025-26 · Regular Season"`. The
 * `"·"` separator is the one `stat_tile`'s context string is split back apart on
 * (CONTRACT.md §7.7), so any caller building this string must use this function rather than
 * hand-joining with a different character. */
export function seasonContext(
  season: string | null | undefined,
  seasonType: string | null | undefined,
  perMode?: string | null,
): string {
  const parts: string[] = [];
  if (season) parts.push(seasonDisplay(season));
  if (seasonType) parts.push(seasonType);
  if (perMode) parts.push(perModeDisplay(perMode));
  return parts.join(" · ");
}
