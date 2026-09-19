/**
 * `/v1/dashboards/*` — the user-scoped saved-dashboard CRUD `routes_me.py` serves. Shapes are
 * transcribed from that module, not frozen by CONTRACT-FOR-AGENTS.md §4 (see `session.ts`'s
 * docstring for why).
 *
 * This module also carries `searchPlayers` / `listTeams` (`GET /v1/players/search`,
 * `GET /v1/teams`): WP4's ownership list has no dedicated file for a widget-config picker's own
 * small lookups, and this is the closest fit of the four it does own — a player/team is looked
 * up here specifically so a dashboard's widget can be configured to point at one.
 *
 * Also re-exports {@link ApiError}: `eslint.config.js` restricts importing `api/client.ts` to
 * `src/api/**`, so a page or component that needs `instanceof ApiError` (every one of them
 * already imports something from this module) gets it from here rather than reaching past the
 * boundary itself.
 */
import { ApiError, fetchJson } from "./client";
import type { GameRef, PlayerRef, TeamRef } from "./types";

export { ApiError };

export interface DashboardSummary {
  readonly layoutId: string;
  readonly name: string;
  readonly icon: string | null;
  readonly accent: string;
  readonly presentation: "tiles" | "broadsheet";
  readonly presetKey: string | null;
  readonly isPreset: boolean;
  readonly widgetCount: number;
  readonly position: number;
  readonly updatedAt: string;
  readonly revision: number;
}

/** A dashboard's stored document. Deliberately typed as a loose record here rather than a fixed
 * interface: it is `DashboardLayout.swift`'s JSON shape, migrated server-side by
 * `accounts/layouts.py`, and the editor (`useLayoutStore.ts`) is what actually understands its
 * fields (`id`, `name`, `icon`, `accent`, `presentation`, `widgets[]`, …). */
export type LayoutDocument = Record<string, unknown>;

export interface DashboardDetail {
  readonly layout: LayoutDocument;
  readonly revision: number;
  readonly notes: readonly string[];
}

export async function listDashboards(): Promise<readonly DashboardSummary[]> {
  const result = await fetchJson<{ readonly dashboards: readonly DashboardSummary[] }>("/dashboards");
  return result.dashboards;
}

export async function getDashboard(layoutId: string): Promise<DashboardDetail> {
  return fetchJson<DashboardDetail>(`/dashboards/${encodeURIComponent(layoutId)}`);
}

export async function createDashboardFromPreset(presetKey: string): Promise<DashboardDetail> {
  return fetchJson<DashboardDetail>("/dashboards", { method: "POST", body: { presetKey } });
}

export async function createDashboardFromLayout(layout: LayoutDocument): Promise<DashboardDetail> {
  return fetchJson<DashboardDetail>("/dashboards", { method: "POST", body: { layout } });
}

/** Thrown when `PUT /v1/dashboards/{id}` answers `409 stale_write` — WEB_DESIGN.md §7.3's
 * non-destructive banner. Carries the server's own current copy so the caller can offer
 * "[Keep mine] [Use theirs]" without a second round trip. */
export class StaleWriteError extends Error {
  readonly layout: LayoutDocument;
  readonly revision: number;

  constructor(layout: LayoutDocument, revision: number) {
    super("This dashboard changed on another device.");
    this.name = "StaleWriteError";
    this.layout = layout;
    this.revision = revision;
  }
}

interface StaleWriteBody {
  readonly error: { readonly code: string };
  readonly layout: LayoutDocument;
  readonly revision: number;
}

function isStaleWriteBody(value: unknown): value is StaleWriteBody {
  return (
    !!value &&
    typeof value === "object" &&
    "error" in value &&
    "layout" in value &&
    "revision" in value
  );
}

/**
 * `PUT /v1/dashboards/{id}` with the `If-Match` revision the caller last read. `client.ts`'s
 * `headers` extension (deviation 3) carries `If-Match`; its `raw` extension is what lets a
 * `409`'s sibling `layout`/`revision` fields (beside `error`, which is all a plain `ApiError`
 * would otherwise expose) become a {@link StaleWriteError} here.
 */
export async function updateDashboard(
  layoutId: string,
  layout: LayoutDocument,
  expectedRevision: number,
): Promise<DashboardDetail> {
  try {
    return await fetchJson<DashboardDetail>(`/dashboards/${encodeURIComponent(layoutId)}`, {
      method: "PUT",
      body: { layout },
      headers: { "If-Match": `"${expectedRevision}"` },
    });
  } catch (error) {
    if (error instanceof ApiError && error.status === 409 && isStaleWriteBody(error.raw)) {
      throw new StaleWriteError(error.raw.layout, error.raw.revision);
    }
    throw error;
  }
}

export async function deleteDashboard(layoutId: string): Promise<void> {
  await fetchJson<void>(`/dashboards/${encodeURIComponent(layoutId)}`, { method: "DELETE" });
}

export async function restoreDashboard(layoutId: string): Promise<DashboardSummary> {
  return fetchJson<DashboardSummary>(`/dashboards/${encodeURIComponent(layoutId)}/restore`, {
    method: "POST",
  });
}

export async function reorderDashboards(layoutIds: readonly string[]): Promise<void> {
  await fetchJson<void>("/dashboards/order", { method: "PUT", body: { layoutIds } });
}

export interface ImportResult {
  readonly imported: readonly DashboardSummary[];
  readonly notes: readonly string[];
  readonly failures: readonly string[];
}

export async function importDashboards(raw: unknown): Promise<ImportResult> {
  return fetchJson<ImportResult>("/dashboards/import", { method: "POST", body: raw });
}

export function exportDashboardsUrl(): string {
  return "/v1/dashboards/export";
}

// --------------------------------------------------------------------- config-picker lookups

export interface PlayerSearchResult extends PlayerRef {
  readonly fromYear: number | null;
  readonly toYear: number | null;
  readonly matchScore: number | null;
}

/** `GET /v1/players/search` — needs at least two characters, per `routes_players.py`. */
export async function searchPlayers(
  query: string,
  options: { readonly activeOnly?: boolean; readonly limit?: number } = {},
): Promise<readonly PlayerSearchResult[]> {
  if (query.trim().length < 2) return [];
  const params = new URLSearchParams({ q: query, limit: String(options.limit ?? 20) });
  if (options.activeOnly) params.set("activeOnly", "true");
  const result = await fetchJson<{ readonly results: readonly PlayerSearchResult[] }>(
    `/players/search?${params.toString()}`,
  );
  return result.results;
}

/** `GET /v1/teams` — the 30 active franchises, for a team picker. */
export async function listTeams(includeHistorical = false): Promise<readonly TeamRef[]> {
  const query = includeHistorical ? "?includeHistorical=true" : "";
  const result = await fetchJson<{ readonly teams: readonly TeamRef[] }>(`/teams${query}`);
  return result.teams;
}

/**
 * `GET /v1/meta` — this build only reads `attribution`, for the footer's NBA.com credit line
 * (`routes_meta.py::ATTRIBUTION` / `DEMO_ATTRIBUTION`): the seeded demo league must not claim
 * NBA.com's real-data credit, so the exact string is read from the server rather than hard-coded
 * twice.
 */
export async function getAttribution(): Promise<string> {
  // `onUnauthorized: "ignore"`: the footer is on every page including `/`, `/sign-in` and the
  // legal pages. With `HARDWOOD_API_KEY` configured, anonymous `GET /v1/meta` answers 401, and
  // the default redirect would bounce every signed-out visitor to `/sign-in` the moment the
  // footer mounted.
  const meta = await fetchJson<{ readonly attribution: string }>("/meta", {
    onUnauthorized: "ignore",
  });
  return meta.attribution;
}

// ------------------------------------------------------------------------------- box scores

export interface BoxScorePlayerLine {
  readonly player: PlayerRef;
  readonly started: boolean | null;
  readonly minutes: number | null;
  readonly values: Readonly<Record<string, number | null>>;
  readonly availability: "full" | "estimated" | "partial" | "unavailable";
}

export interface BoxScoreTeamSide {
  readonly team: TeamRef;
  readonly values: Readonly<Record<string, number | null>>;
  readonly players: readonly BoxScorePlayerLine[];
}

export interface BoxScore {
  readonly game: GameRef;
  readonly teams: readonly BoxScoreTeamSide[];
}

/** `GET /v1/games/{gameId}/box` — `/tonight/:gameId`'s page, "now with `fantasy_pts`"
 * (`routes_games.py::BASIC_PLAYER_METRICS`). */
export async function getBoxScore(
  gameId: string,
  view: "basic" | "advanced" | "both" = "both",
): Promise<BoxScore> {
  return fetchJson<BoxScore>(`/games/${encodeURIComponent(gameId)}/box?view=${view}`);
}

// ------------------------------------------------------------------------------- team detail

export interface TeamRosterEntry extends PlayerRef {
  readonly values: Readonly<Record<string, number | null>>;
}

export interface TeamDetail {
  readonly team: TeamRef;
  readonly season: string | null;
  readonly seasonType: string | null;
  readonly record: { readonly wins: number | null; readonly losses: number | null } | null;
  readonly values: Readonly<Record<string, number | null>>;
  readonly roster: readonly TeamRosterEntry[];
}

/** `GET /v1/teams/{teamId}` — `/team/:teamId`'s roster (not one of the sixteen widget kinds). */
export async function getTeamDetail(teamId: number, season?: string): Promise<TeamDetail> {
  const query = season ? `?season=${encodeURIComponent(season)}` : "";
  return fetchJson<TeamDetail>(`/teams/${teamId}${query}`);
}

// ------------------------------------------------------------------------------- fantasy night

export type FantasyScoring = "nba" | "espn_points" | "yahoo_points";

export interface FantasyNightRow {
  readonly rank: number;
  readonly player: PlayerRef;
  readonly gameId: string;
  readonly opponentAbbr: string | null;
  readonly isHome: boolean;
  readonly points: number;
  readonly displayValue: string;
  readonly minutes: number | null;
  readonly line: string;
  readonly availability: "full" | "estimated";
}

export interface FantasyNight {
  readonly date: string | null;
  readonly scoring: FantasyScoring;
  readonly formulaLabel: string;
  readonly rows: readonly FantasyNightRow[];
}

/**
 * `GET /v1/fantasy/night` — §7.8's "Tonight" tab. `scoring: "nba"` reads the stored, recorded
 * `player_game_basic.fantasy_pts`; `espn_points`/`yahoo_points` are re-scored on demand from the
 * same box score and come back `availability: "estimated"` (`routes_fantasy.py`) — never
 * presented as the same number as the other two, per the house rule against folding the three
 * fantasy systems together.
 */
export async function getFantasyNight(scoring: FantasyScoring, date = "latest"): Promise<FantasyNight> {
  return fetchJson<FantasyNight>(`/fantasy/night?scoring=${scoring}&date=${encodeURIComponent(date)}`);
}
