# Hardwood — API & Data Contract v1

This file is the **single source of truth** shared by the iOS client (`ios/`) and the
service (`backend/`). Both sides are implemented against it, and
`scripts/check_contracts.py` fails CI when they drift apart.

Machine-readable companions, loaded by both sides at build time:

| File | Contents |
| --- | --- |
| `contracts/metrics.json` | 62 metric descriptors (`metrics`: name, format, direction, era availability, glossary) and, beside them, the EuroLeague's stat vocabulary (`leagueMetrics.euroleague`, 23 descriptors; §9) |
| `contracts/widgets.json` | 20 widget kinds (16 NBA tiles and the 4 league tiles of §4), their sizes and their configuration field schema |
| `contracts/presets.json` | 12 preset dashboards, pre-validated against the widget catalog |
| `contracts/leagues.json` | The two league profiles, the availability vocabulary, the position schemes, and the two guard lists (§9, §10, §11). **Not** bundled in the app: clients read the live equivalents from `GET /v1/leagues` and `GET /v1/el/meta` |
| `contracts/fixtures/*.json` | Golden response payloads, decoded by both the backend tests and the iOS tests |
| `contracts/fixtures/leagues/{nba,el}/*.json` | Golden payloads of the league routes (§3, §4), from the seeded NBA demo and the synthetic EuroLeague. A sub-directory on purpose: the iOS bundle and its decoding tests never read it |

Regenerate the catalogs with `python3 contracts/tools/gen_metrics.py > contracts/metrics.json`
(and the `gen_widgets` / `gen_presets` equivalents). `gen_presets.py` validates every preset
widget against the widget catalog and the metric catalog, and exits non-zero on any mismatch.
`gen_leagues.py` writes `contracts/leagues.json`; with `--python` it writes
`backend/nbastats/shared/_generated_leagues.py`, the same two guard lists as Python constants for
the stdlib-only pure core. `scripts/check_contracts.py` check (m) regenerates both.

---

## 1. Conventions

* Base path: `/v1`. Every response is JSON, UTF-8, `Content-Type: application/json`.
* **JSON keys are `lowerCamelCase`.** The database uses `snake_case`; the API layer translates.
  Swift types therefore decode with the default key strategy (no `convertFromSnakeCase`).
* Dates are ISO-8601 calendar dates in **US Eastern**, the NBA's scheduling day: `"2026-01-02"`.
  Timestamps are RFC-3339 UTC with a `Z` suffix: `"2026-01-03T07:12:44Z"`.
* Seasons are NBA-style strings: `"2025-26"`. The literal `"latest"` is accepted in requests
  and resolves to the season currently in progress.
* Season types: `"Regular Season"`, `"Playoffs"`, `"Play In"`, `"All Star"`, `"Pre Season"`.
* `null` means *not available for this era or subject*, never zero. Every payload carrying an
  era-limited metric also carries the flags in §6.
* Money-free, no auth on the public read surface by default; when `HARDWOOD_API_KEY` is set the
  service requires `X-API-Key` on every `/v1` route except `/v1/health`.
* Errors use the envelope in §7. HTTP status mirrors the error class.
* **Leagues.** `/v1` is the NBA. The EuroLeague is `/v1/el`, with the same route suffixes (§9). The
  league is chosen by the URL, never by a header or a query parameter, and every league-aware
  payload says which one it is with `"league": "nba" | "euroleague"`. The calendar-day, season and
  id rules above are the NBA's; the EuroLeague's differ where §9 says so.

### Pagination

Collection endpoints that can exceed 200 rows take `limit` (default 50, max 200) and an opaque
`cursor`. Responses carry `"nextCursor": string | null`. Endpoints whose rows are a widget
payload bound their `limit` to that widget's config field instead, and each states its own
range below; an out-of-range `limit` is a `bad_request`, never a silent clamp.

### Leagues and prefixes

The league-aware routes (§3) exist under two prefixes with identical suffixes: `/v1` for the NBA
and `/v1/el` for the EuroLeague. A client builds its league switcher from `GET /v1/leagues`
(§9.2), which names each league's `apiPrefix`, and never hard-codes `/v1/el`. The two prefixes
cannot be mixed: a EuroLeague id sent to an NBA route is an unknown id, and the reverse.

Teams and players in league-aware payloads are the league-neutral references below, never
`TeamRef` and `PlayerRef` (which stay NBA-only and unchanged). `id` is always a **string**: the NBA
numeric id as a string, or the EuroLeague's official club or person code. Clients key entities by
`(league, id)`.

```json
{ "league": "euroleague", "id": "ZZA", "abbr": "ZZA", "name": "Alderwick Herons",
  "shortName": "Alderwick", "clubCode": "ZZA", "tvCode": null }
```

`LeagueTeamRef` is `{league, id, abbr, name, shortName, teamId?, clubCode?, tvCode?}`. `teamId`
(a number) is present for the NBA only; `clubCode` and `tvCode` for the EuroLeague only. `shortName`
and `tvCode` are nullable.

```json
{ "league": "euroleague", "id": "demo-9", "name": "Aldous Oldacre", "position": "F",
  "positionRaw": "Forward", "jersey": "75", "headshotUrl": null, "personCode": "demo-9" }
```

`LeaguePlayerRef` is `{league, id, name, position, positionRaw, jersey, headshotUrl, playerId?,
personCode?}`. `position` is `"G"`, `"F"`, `"C"` or null (the three buckets both leagues publish;
null when the listing is missing or not understood), and `positionRaw` is the source's own label.
`playerId` is NBA-only and `personCode` EuroLeague-only. `headshotUrl` is null for every EuroLeague player (no photographs are stored) and, for the NBA, is whatever `PlayerRef.headshotUrl` would be for the same player.

---

## 2. Shared objects

### `PlayerRef`

```json
{
  "playerId": 2544,
  "name": "LeBron James",
  "firstName": "LeBron",
  "lastName": "James",
  "teamId": 1610612747,
  "teamAbbr": "LAL",
  "position": "F",
  "jersey": "23",
  "headshotUrl": null,
  "isActive": true
}
```

`teamId`, `teamAbbr`, `position`, `jersey`, `headshotUrl` are nullable.

### `TeamRef`

```json
{
  "teamId": 1610612747,
  "abbr": "LAL",
  "name": "Los Angeles Lakers",
  "city": "Los Angeles",
  "nickname": "Lakers",
  "conference": "West",
  "division": "Pacific"
}
```

### `MetricValue`

The atom every widget renders. `value` is always the raw number in the metric's native unit
(percentages are fractions in `[0,1]`, never 0-100); `displayValue` is the server-formatted
string that the client shows verbatim when it has no reason to reformat.

```json
{
  "metric": "ts_pct",
  "value": 0.6153,
  "displayValue": "61.5%",
  "rank": 12,
  "percentile": 0.93,
  "leagueAverage": 0.5671,
  "delta": 0.0482,
  "isEstimated": false,
  "availability": "full"
}
```

`rank`, `percentile`, `leagueAverage`, `delta` are nullable. `value` is nullable when the
metric does not exist for the subject's era — in that case `availability` is `"unavailable"`
and `displayValue` is `"—"`.

`availability` ∈ `"full"` | `"estimated"` | `"partial"` | `"unavailable"` (see §6).

### `MetricDescriptor`

Exactly the shape of one entry in `contracts/metrics.json#/metrics`. The client bundles that
file and refreshes it from `/v1/meta`, so it never hard-codes formatting rules.

### `GameRef`

```json
{
  "gameId": "0022500512",
  "date": "2026-01-02",
  "season": "2025-26",
  "seasonType": "Regular Season",
  "home": { "...TeamRef" },
  "away": { "...TeamRef" },
  "homePts": 118,
  "awayPts": 112,
  "status": "final",
  "period": 4,
  "clock": null,
  "finalizedAt": "2026-01-03T02:41:07Z"
}
```

`status` ∈ `"scheduled"` | `"live"` | `"final"`. `homePts`/`awayPts`/`finalizedAt` are null
before the game starts. `clock` is a display string such as `"4:21"` while live.

### `GameRefL`

The league-neutral game, used by every payload in §4's league section. `GameRef` is unchanged.

```json
{ "league": "euroleague", "gameId": "E2026-R05-01", "date": "2026-10-15",
  "tipoffUtc": "2026-10-15T17:30:00Z", "venue": "Alderwick Hall", "isNeutral": false,
  "round": 5, "phase": "RS", "status": "scheduled",
  "home": { "...LeagueTeamRef" }, "away": { "...LeagueTeamRef" },
  "homePts": null, "awayPts": null, "overtimePeriods": null }
```

`status` ∈ `"scheduled"` | `"resultPending"` | `"final"` | `"postponed"`. **`resultPending` is
derived when read, and it is not "upcoming":** the tip-off (or 23:59 local on the game date when
the tip-off is unknown) is more than three hours past and no result is stored. `isNeutral` is
`null` when unknown, never assumed false. `round` and `phase` are null for the NBA, which has no
rounds. `date` is the league's own calendar day (US Eastern for the NBA, Europe/Berlin for the
EuroLeague). `tipoffUtc` is null when the source did not say.

### `Source`

Where one fact came from, carried by every availability entry (§10).

```json
{ "kind": "pressArticle", "label": "example.org news", "url": "https://news.example.org/injuries/0",
  "publishedAt": "2026-10-02T09:00:00Z", "asOf": "2026-10-12T09:00:00Z",
  "fetchedAt": "2026-10-12T09:00:00Z", "snapshotId": null }
```

`kind` ∈ `leagueReport` | `clubStatement` | `pressArticle` | `boxScoreInference` |
`workbookImport` | `manual`. `publishedAt` is the date the *source* carries and is what every age
is computed from, never `fetchedAt`. `url` is null for a hand-typed entry and for a link withheld
because its host belongs to a betting operator (the `label` then ends
`(link withheld: betting operator)`). `snapshotId` is the NBA injury-report snapshot the entry came
from, or null.

### `Freshness`

How current a payload is, and which sources fed it. In every new payload.

```json
{ "league": "euroleague", "syncVersion": 7, "dataThrough": "2026-10-09",
  "generatedAt": "2026-10-12T09:00:00Z", "isDemo": true,
  "sources": [ { "key": "el.manual", "label": "Entered by hand", "state": "ok",
                 "reason": null, "lastSuccessAt": "2026-10-12T09:00:00Z" } ] }
```

`syncVersion` is the league's own cursor (the EuroLeague's is independent of the NBA's, §9.6).
`isDemo` is true when the store holds invented games: **show a banner**. `sources` lists only the
sources that fed *this* payload; the full registry is `GET {prefix}/sources` (§10.5).

---

## 3. Endpoints

### `GET /v1/health`

```json
{ "status": "ok", "version": "1.0.0", "syncVersion": 412, "dataThrough": "2026-01-02",
  "databaseReady": true, "seededDemoData": false, "authReady": true, "authWarnings": [] }
```

`authReady` / `authWarnings` describe the Hardwood Web accounts feature and are computed with
no database read (see `nbastats/api/routes_meta.py`): `authReady` is true once `/v1/auth/*` is
actually mounted (false — with an empty `authWarnings` — on a stats-only deployment that never
installed the accounts feature at all, which is not itself a degraded state), and
`authWarnings` are the non-fatal configuration notes from `nbastats.accounts.config.
startup_warnings` (an `HARDWOOD_APPLE_*` set with an http base URL, a `HARDWOOD_GOOGLE_CLIENT_ID`
with no matching secret, and so on). Neither field ever changes `status` or the HTTP status
code — a misconfigured or absent accounts feature is not a database being down.

Never requires an API key. `200` when healthy, `503` with the same body when `databaseReady`
is false.

### `GET /v1/meta`

The client calls this on launch and caches it for 24 hours. It returns the three catalogs plus
league state, so a server-side catalog change reaches clients without an App Store release.

```json
{
  "syncVersion": 412,
  "dataThrough": "2026-01-02",
  "currentSeason": "2025-26",
  "seasons": [ { "season": "2025-26", "seasonTypes": ["Regular Season"], "isCurrent": true,
                 "hasAdvanced": true, "gameCount": 612 } ],
  "metrics": { "...contracts/metrics.json" },
  "widgets": { "...contracts/widgets.json" },
  "teams": [ { "...TeamRef" } ],
  "coverage": { "advancedFrom": "1996-97", "trackingFrom": "2013-14",
                "hustleFrom": "2016-17", "shotChartsFrom": "1996-97",
                "seasonFrom": "1946-47" },
  "attribution": "Stats via NBA.com. Not endorsed by or affiliated with the NBA."
}
```

### `GET /v1/presets`

```json
{ "schemaVersion": 1, "version": 7, "presets": [ "...contracts/presets.json#/presets" ],
  "subjectTokens": [ "...contracts/presets.json#/subjectTokens" ] }
```

`version` increments whenever the preset content changes, so the client can offer "this preset
was updated" without diffing.

### `GET /v1/players/search`

Query: `q` (min 2 chars, required), `limit` (default 20, max 50), `activeOnly` (bool, default
false), `season` (optional filter).

```json
{ "query": "lebr", "results": [ { "...PlayerRef", "fromYear": 2003, "toYear": 2026,
                                  "matchScore": 0.98 } ] }
```

Search is diacritic- and case-insensitive, matches on any name part, and ranks exact prefix
matches first, then active players, then career minutes.

### `GET /v1/players/{playerId}`

```json
{ "player": { "...PlayerRef" },
  "bio": { "height": "6-9", "weight": 250, "birthdate": "1984-12-30", "country": "USA",
           "draft": { "year": 2003, "round": 1, "pick": 1 }, "school": "St. Vincent-St. Mary HS",
           "fromYear": 2003, "toYear": 2026, "bbrefSlug": "jamesle01" },
  "careerTotals": { "...season row, season = \"Career\"" },
  "seasons": [ { "season": "2025-26", "seasonType": "Regular Season", "teamAbbr": "LAL",
                 "age": 41, "gp": 41, "values": { "pts": 24.1, "ts_pct": 0.601 },
                 "availability": "full" } ] }
```

### `GET /v1/players/{playerId}/gamelog`

Query: `season` (required, or `"career"`), `seasonType`, `limit`, `cursor`, `metrics`
(comma-separated metric keys; defaults to the game-log preset column set).

```json
{ "player": { "...PlayerRef" }, "season": "2025-26", "seasonType": "Regular Season",
  "rows": [ { "gameId": "0022500512", "date": "2026-01-02", "opponentAbbr": "BOS",
              "isHome": true, "result": "W", "score": "118-112", "started": true,
              "minutes": 34.5, "values": { "pts": 32, "ts_pct": 0.641 },
              "availability": "full" } ],
  "nextCursor": null }
```

Rows are newest-first. `values` contains only the requested metric keys; a key whose metric is
unavailable for that game's era is present with a `null` value.

### `GET /v1/teams` · `GET /v1/teams/{teamId}`

`GET /v1/teams` → `{ "teams": [ TeamRef ] }` (all 30 current franchises plus historical ones
when `includeHistorical=true`).

`GET /v1/teams/{teamId}?season=&seasonType=` →

```json
{ "team": { "...TeamRef" }, "season": "2025-26", "record": { "wins": 27, "losses": 14 },
  "values": { "off_rtg": 118.2, "def_rtg": 110.4, "net_rtg": 7.8, "pace": 99.4 },
  "roster": [ { "...PlayerRef", "values": { "min": 34.2, "pts": 24.1 } } ] }
```

### `GET /v1/leaders`

Query: `metric` (required), `subjectType` (`player`|`team`, default `player`), `scope`
(`season`|`all_time`), `season`, `seasonType`, `perMode`, `limit` (default 10, min 3, max 50 —
the `leaderboard` widget's own range), `minGames`, `minMinutesPerGame`, `positions`, `teamIds`,
`secondaryMetrics`, `ascending`.

Response is the `leaderboard` widget payload (§4) plus `"nextCursor"`.

### `GET /v1/games`

Query: `date` (ISO or `"latest"`), or `season` + `seasonType` + `teamId`, `limit`, `cursor`.

```json
{ "date": "2026-01-02", "isLatestCompleted": true, "games": [ { "...GameRef" } ],
  "nextCursor": null }
```

`"latest"` resolves to the most recent date with at least one `final` game.

### `GET /v1/games/{gameId}/box`

Query: `view` ∈ `basic` | `advanced` | `both` (default `both`).

```json
{ "game": { "...GameRef" },
  "teams": [ { "team": { "...TeamRef" }, "values": { "off_rtg": 118.2 },
               "players": [ { "player": { "...PlayerRef" }, "started": true, "minutes": 34.5,
                              "values": { "pts": 32, "ts_pct": 0.641, "fantasy_pts": 47.4 },
                              "availability": "full" } ] } ] }
```

`fantasy_pts` (NBA's own `PTS + 1.2·REB + 1.5·AST + 3·STL + 3·BLK − TOV` formula, recorded on
the box score at ingest) is included in `values` alongside every other basic metric; it is
`null`, like any other era-limited column, on a game whose steals/blocks/turnovers were never
recorded (before 1977-78).

### `GET /v1/fantasy/night`

Per-game fantasy points for one night's slate, under a scoring system of the caller's choosing —
a page, not a dashboard widget (there is no 17th widget kind for this; see §10 of
`WEB_DESIGN.md`). API-key-or-session, same as the five original routers.

Query: `date` (ISO or `"latest"`, default `"latest"`), `scoring` (`"nba"` | `"espn_points"` |
`"yahoo_points"`, default `"nba"`), `limit` (3-50, default 25), `minMinutes` (0-48, default 12).

```json
{ "date": "2026-03-14", "scoring": "espn_points",
  "formulaLabel": "ESPN points scoring, applied to this game's box score.",
  "rows": [ { "rank": 1, "player": { "...PlayerRef" }, "gameId": "0022500512",
              "opponentAbbr": "BOS", "isHome": true, "points": 58.5, "displayValue": "58.5",
              "minutes": 36.2, "line": "32 PTS · 8 REB · 11 AST", "availability": "full" } ] }
```

`availability` is `"full"` for `scoring: "nba"` (a recorded box-score value) and `"estimated"`
for `espn_points` / `yahoo_points` (a re-scoring of the box score, not a record). A player whose
game is missing a component the chosen scoring system weights (steals/blocks before 1973-74,
three-pointers before 1979-80, individual turnovers before 1977-78) is **omitted** from `rows`
entirely, never scored as though the missing value were zero.

### `POST /v1/dashboard/resolve`

**The endpoint the dashboard is built on.** One round trip renders a whole dashboard; each
widget resolves independently so a single failure degrades one tile instead of the screen.

Request:

```json
{
  "layoutId": "5C6F…",
  "context": { "favoritePlayerId": 2544, "favoriteTeamId": 1610612747,
               "timeZone": "America/New_York", "asOf": "2026-01-02" },
  "knownSyncVersion": 411,
  "widgets": [
    { "id": "w1", "kind": "scoreboard", "size": "large", "config": { "date": "latest" } },
    { "id": "w2", "kind": "leaderboard", "size": "large",
      "config": { "metric": "ts_pct", "season": "latest", "limit": 10 } }
  ]
}
```

* At most **24 widgets** per request; over that is `400 too_many_widgets`.
* Unknown config keys are ignored; missing optional keys take the catalog default.
* `$`-prefixed subject tokens (see `contracts/presets.json#/subjectTokens`) are resolved
  server-side from `context`, falling back to the league-wide featured subject.
* `knownSyncVersion` is advisory: when it equals the server's, widgets whose data has not
  changed may come back with `"status": "unchanged"` and no payload, and the client keeps its
  cached copy.

Response:

```json
{
  "syncVersion": 412,
  "dataThrough": "2026-01-02",
  "generatedAt": "2026-01-03T07:12:44Z",
  "resolvedContext": { "favoritePlayerId": 2544, "favoriteTeamId": 1610612747,
                       "season": "2025-26" },
  "results": [
    { "widgetId": "w1", "kind": "scoreboard", "status": "ok",
      "payload": { "…": "§4" }, "generatedAt": "2026-01-03T07:12:44Z",
      "ttlSeconds": 60, "availability": "full", "notes": [] },
    { "widgetId": "w2", "kind": "leaderboard", "status": "error", "payload": null,
      "error": { "code": "metric_unavailable",
                 "message": "TS% is not available for the 1958-59 season.",
                 "recoverable": true, "field": "metric" } }
  ]
}
```

`status` ∈ `"ok"` | `"unchanged"` | `"partial"` | `"error"`. `"partial"` carries a payload
**and** `notes` explaining what is missing (for example a pre-1997 season with no per-game
ratings).

**Signed-in favourites (Hardwood Web).** When the caller holds a live session and
`context.favoritePlayerId` / `context.favoriteTeamId` are omitted or `null`, the server fills
them in from that account's own stored favourites (`GET /v1/me`'s same fields) before resolving
any widget. A value the client *did* send always wins, so an iOS request — which always sends
its own favourites — is unaffected. This is also the one place a `401` on this endpoint is a
real request-level failure rather than a per-widget `status: "error"`: an expired or missing
session is a question about *who is asking*, which "one bad tile degrades one tile" was never
meant to paper over, and the right client response is to route to sign-in, not to retry every
tile.

### `GET /v1/sync`

Query: `since` (integer sync version, optional).

```json
{ "syncVersion": 412, "previousVersion": 411, "serverTime": "2026-01-03T07:12:44Z",
  "dataThrough": "2026-01-02", "hasChanges": true,
  "changedDates": ["2026-01-02", "2026-01-01"],
  "finalizedGames": [ { "...GameRef" } ],
  "affectedPlayerIds": [2544, 1629029],
  "invalidate": ["scoreboard", "daily_movers", "leaderboard", "game_log"],
  "nextPollAfterSeconds": 900 }
```

The client polls this on foreground, on pull-to-refresh, and from a background refresh task.
When `hasChanges` is false the body is small and the client does nothing. `invalidate` lists
widget **kinds** whose cached payloads must be dropped.

### `GET /v1/sync/stream` *(optional, server-sent events)*

Emits `event: sync` with the `/v1/sync` body each time a game finalizes. The client uses it
only while the dashboard is foregrounded; everything still works without it.

### `/v1/me/*` — account settings, sessions and identities (Hardwood Web)

Session required (`401` with no live `hw_session` cookie); every unsafe method additionally
needs `X-Hardwood-CSRF` (`403 csrf_failed` without it — see §5 below) and the four rows marked
**fresh** need a session less than ten minutes past sign-in (`403 reauthentication_required`
otherwise). `GET /v1/me`, `GET /v1/me/email/confirm` and `GET /v1/me/sessions` responses carry
`Cache-Control: no-store` and `Vary: Cookie`, as does every `/v1/dashboards/*` response
(including the `Layouts.json` export) — see `nbastats/api/security.py`.

| Method | Path | Extra auth | Success |
| --- | --- | --- | --- |
| GET | `/v1/me` | — | `200 User` |
| PATCH | `/v1/me` | CSRF | `{"displayName","favoritePlayerId","favoriteTeamId","theme","selectedDashboardId"}` (an explicit allowlist; `email` is not settable here) → `200 User` |
| POST | `/v1/me/password` | CSRF + fresh | `{"currentPassword?","newPassword"}` → `200 {"csrfToken":"…"}`; rotates this session and revokes every other one |
| POST | `/v1/me/email` | CSRF + fresh | `{"newEmail","currentPassword?"}` → `202 {"status":"checkYourEmail"}`, identical whether or not the address is already in use |
| GET | `/v1/me/email/confirm` | token | `?token=` → `303` to `/settings?emailChanged=1`; sets `email`/`emailVerified`, rotates the caller's session and revokes every other one. `303` to `/settings?emailTaken=1` if the address was claimed by somebody else in the meantime |
| GET | `/v1/me/sessions` | — | `200 {"sessions":[{"sessionId","createdAt","lastSeenAt","userAgent","ipPrefix","authMethod","current"}]}` |
| DELETE | `/v1/me/sessions/{sessionId}` | CSRF | `204` |
| POST | `/v1/me/identities/{provider}/start` | CSRF + fresh | `200 {"redirectUrl":"…"}` + `Set-Cookie: hw_oauth` (`intent=link`); `404 provider_not_configured` |
| DELETE | `/v1/me/identities/{provider}` | CSRF + fresh | `200 {"csrfToken":"…"}`; rotates this session and revokes every other one (unlinking is a privilege change); `409 last_credential` if it would leave the account with no way to sign in |
| GET | `/v1/me/export` | — | `200` the account plus every dashboard (GDPR access); its own limiter, 5/hour |
| DELETE | `/v1/me` | CSRF + fresh | `{"confirm":"DELETE"}` → `204`; soft-deletes and revokes every session |

`User` on the wire:

```json
{ "userId": "…", "email": "a@b.c", "emailVerified": true, "displayName": "Ada",
  "isPrivateRelay": false, "hasPassword": true, "identities": ["google"],
  "favoritePlayerId": 2544, "favoriteTeamId": 1610612747, "selectedDashboardId": "…",
  "theme": "system", "createdAt": "2026-09-19T00:00:00Z" }
```

Linking a *new* provider from inside a signed-in session is a **POST** that returns a
`redirectUrl`, never a bare `GET /start`: that is what makes it CSRF-protected and
freshness-gated, which is how §6.4's link-flow account-capture attack is closed.

### `/v1/dashboards/*` — user-scoped saved dashboards (Hardwood Web)

Session required throughout; every unsafe method needs `X-Hardwood-CSRF`. Every write goes
through the same layout migrator (`nbastats.accounts.layouts`) `POST /v1/dashboards/import`
does, so the store can never hold a dashboard the resolver cannot resolve. Whole-document `PUT`,
never per-widget `PATCH` — every mutation re-encodes the whole layout, matching iOS.

| Method | Path | Body | Success | Errors |
| --- | --- | --- | --- | --- |
| GET | `/v1/dashboards` | — | `200 {"dashboards":[{"layoutId","name","icon","accent","presentation","presetKey","isPreset","widgetCount","position","updatedAt","revision"}]}` | — |
| POST | `/v1/dashboards` | `{"layout":{…}}` **or** `{"presetKey":"daily_recap"}` | `201 {"layout":{…},"revision":1,"notes":[]}` | `409 too_many_dashboards`, `409 layout_too_new`, `400 not_a_layout` |
| GET | `/v1/dashboards/{layoutId}` | — | `200 {"layout":{…},"revision":N,"notes":[]}`, `ETag: "N"` | `404` (another user's id is a 404, never a 403 — a 403 would confirm the id exists) |
| PUT | `/v1/dashboards/{layoutId}` | `{"layout":{…}}` + header `If-Match: N` | `200 {"layout":{…},"revision":N+1,"notes":[…]}` | `428 precondition_required` (no `If-Match`), `409 stale_write` **with the server's current document in the body**, `409 layout_too_new` |
| DELETE | `/v1/dashboards/{layoutId}` | — | `204` | `404` |
| POST | `/v1/dashboards/{layoutId}/restore` | — | `200` the restored summary | `404` |
| PUT | `/v1/dashboards/order` | `{"layoutIds":["…"]}` | `204` | — |
| POST | `/v1/dashboards/import` | the iOS envelope, a bare array, or one bare layout | `200 {"imported":[…],"notes":[…],"failures":[…]}` | `413 payload_too_large` |
| GET | `/v1/dashboards/export` | — | `200` the exact iOS `{"schemaVersion":1,"updatedAt":"…","layouts":[…]}` envelope, `Content-Disposition: attachment; filename="Layouts.json"` | — |

A `409 stale_write` body:

```json
{ "error": { "code": "stale_write", "message": "This dashboard changed on another device.",
             "recoverable": true, "field": null, "requestId": "…" },
  "layout": { "...the server's current document" }, "revision": 3 }
```

### League routes: the NBA, under `/v1`

Team matchup, defence by opponent position, team-score projections, availability, headlines and
the source registry. The EuroLeague serves the same suffixes under `/v1/el` (next section). All of
them sit behind the same API-key-or-session gate and rate limiter as every other `/v1` router; the
payload shapes are in §4's league section and the rules behind them in §9–§11. A **write** needs a
signed-in session with its CSRF token (the browser) or the API key (the native app on loopback).

| Route | What it returns |
| --- | --- |
| `GET /v1/leagues` | The leagues this process serves, each with its `apiPrefix`, state, units and position buckets (§9.2) |
| `GET /v1/matchups` | `TeamMatchup` for two teams. Query: `homeTeamId`, `awayTeamId` (both required), `season` (default `latest`), `seasonType`, `window` (3-15, default 5) |
| `GET /v1/teams/{teamId}/matchup` | `TeamMatchup` for the team's next scheduled game (`404 game_not_found` if there is none). Query: `window` |
| `GET /v1/games/{gameId}/matchup` | `TeamMatchup` for one game, with every input cut off before tip-off |
| `GET /v1/teams/{teamId}/defense-by-position` | `DefenseByPosition`. Query: `season`, `seasonType`, `window` (`0` = the whole season, default), `basis` (`perGame` or `perMinute`, default `perGame`) |
| `GET /v1/defense-by-position` | `DefenseByPositionTable`, every team. Same query |
| `GET /v1/projections` | `SlateProjections`. Query: `date` (an ISO date or `next`, default `next`), `teamIds` |
| `GET /v1/games/{gameId}/projection` | `GameProjectionDetail`: the current projection, the one locked before tip-off, and the history |
| `GET /v1/projections/review` | `ProjectionReview`: locked projections against what happened. Query: `date`, `season` |
| `GET /v1/availability` | `AvailabilityReport`. Query: `teamId`, `date` (`next` by default), `statuses` |
| `GET /v1/availability/review-queue` | `{items: [{statusId, playerName, team, source}]}`: statuses whose player matched nobody |
| `POST /v1/availability` | Write: enter an override. Body: `{playerId, teamId?, gameId?, status, note?, sourceUrl?, sourcePublishedAt?}` |
| `DELETE /v1/availability/{overrideId}` | Write: clear an override |
| `GET /v1/news` | `NewsLinks`: headlines (title, link, date, source) about a team or player. Query: `teamId`, `playerId`, `limit` (default 10) |
| `POST /v1/news/links` | Write: paste a link. Body: `{title, link, publishedAt, sourceName, teamIds, playerIds}` |
| `GET /v1/sources` | `SourceList`: where every number came from and how current it is (§10.5) |
| `GET /v1/model-settings` | `{league, freshness, settings: [{key, value, provenance, isDefault, setAt}]}` |
| `PATCH /v1/model-settings` | Write: `{settings: [{key, value}]}`. Allowlisted keys only; the patch is validated whole and applied atomically |

Every query, path and body field name on these routes is on one list
(`contracts/leagues.json#/allowedParameters`), and a test walks the OpenAPI document to prove it:
no route takes an external number to compare with a projection (§11).

### League routes: the EuroLeague, under `/v1/el`

`season` accepts `E2026`, `2026-27` or `latest` and defaults to the current season. A team id is a
club code. Every route except `meta` and `health` answers `503 league_unavailable` while the
EuroLeague is off, misconfigured or holds no data (§9.4); **`GET /v1/el/health` is the only
EuroLeague route that needs no API key**, because its state is its payload.

**The same suffixes as the NBA routes above:**

| Route | Notes |
| --- | --- |
| `GET /v1/el/matchups` | `homeTeamId` and `awayTeamId` are club codes |
| `GET /v1/el/teams/{clubCode}/matchup` | |
| `GET /v1/el/games/{gameId}/matchup` | |
| `GET /v1/el/teams/{clubCode}/defense-by-position` | `phase` in place of `seasonType`; adds `scheme` (`gfc`, the default, or `workbook5`; §9.4) |
| `GET /v1/el/defense-by-position` | `phase`, `scheme` |
| `GET /v1/el/projections` | `round` (a number or `next`, the default) in place of `date` |
| `GET /v1/el/games/{gameId}/projection` | |
| `GET /v1/el/projections/review` | `round` |
| `GET /v1/el/availability` | `clubCode`, `round`, `statuses`, `includeNews` |
| `GET /v1/el/availability/review-queue` | |
| `POST /v1/el/availability` | Write. Body: `{clubCode, personCode or playerName, gameId?, status, reasonCategory?, reasonText?, expectedReturnText?, sourceUrl?, sourceLabel, sourcePublishedAt}`. `kind` of the stored source is `pressArticle` or `clubStatement` when a URL is given, `manual` otherwise. A status outside the five is `400 invalid_status` |
| `DELETE /v1/el/availability/{statusId}` | Write. Appends a retraction; history is never rewritten |
| `GET /v1/el/news`, `POST /v1/el/news/links` | as the NBA's, club codes for team ids |
| `GET /v1/el/sources` | |
| `GET /v1/el/model-settings`, `PATCH /v1/el/model-settings` | |

**EuroLeague only** (the pages of the user's workbook, made live):

| Route | Payload |
| --- | --- |
| `GET /v1/el/meta` | State, demo flag, seasons, rounds, clubs, units and the plain statement of what is and is not loaded (§9.4) |
| `GET /v1/el/health`, `GET /v1/el/sync` | The EuroLeague's state and freshness cursor only |
| `GET /v1/el/teams` | Every club: record, points scored and allowed, rating |
| `GET /v1/el/teams/{clubCode}` | `ClubView`: scoring, rating, squad, absences, next game |
| `GET /v1/el/rounds/{round}` | `RoundView` |
| `GET /v1/el/rounds/{round}/scorers` | `RoundScorers`. Query: `perClub` (default 3) |
| `GET /v1/el/games` | The schedule and results (EuroLeague games only). Query: `round`, `clubCode`, `phase` |
| `GET /v1/el/games/{gameId}` | `ElBoxScore` |
| `GET /v1/el/players/{personCode}`, `.../gamelog` | A player's season line, rates and availability; his official games |
| `GET /v1/el/stats/players` | `ElPlayerStatsTable`. Query: `perMode` (`PerGame`, `Totals`, `Per40`), `sort`, `clubCode`, `minGames`, `limit` |
| `GET /v1/el/ratings` | Club ratings. Query: `asOfRound` |
| `GET /v1/el/method` | `{constants: [{key, value, provenance, isDefault, description}], deviations: [string], limitations: [string]}`: every constant the model uses and where it came from |
| `GET /v1/el/review-queue` | Unmatched people, clubs and statuses from import or reconciliation |

---

## 4. Widget payloads

One payload type per `kind`. All of them are also written out as golden fixtures in
`contracts/fixtures/widget_<kind>.json`.

### `stat_tile`

```json
{ "subject": { "type": "player", "player": { "...PlayerRef" }, "team": null },
  "context": "2025-26 · Regular Season · Per Game",
  "primary": { "...MetricValue" },
  "secondary": [ { "...MetricValue" } ],
  "sparkline": [ { "x": "2026-01-02", "y": 0.641, "gameId": "0022500512" } ],
  "sparklineMetric": "ts_pct" }
```

### `player_snapshot`

```json
{ "player": { "...PlayerRef" }, "season": "2025-26", "seasonType": "Regular Season",
  "teamAbbr": "LAL", "gp": 41, "gs": 41, "minutesPerGame": 34.5,
  "metrics": [ { "...MetricValue" } ], "eraNote": null }
```

### `leaderboard`

```json
{ "metric": { "...MetricDescriptor" }, "subjectType": "player", "scope": "season",
  "season": "2025-26", "seasonType": "Regular Season", "perMode": "PerGame",
  "qualifier": "Minimum 15 games and 24.0 minutes per game",
  "secondaryMetrics": [ { "...MetricDescriptor" } ],
  "rows": [ { "rank": 1, "player": { "...PlayerRef" }, "team": null,
              "value": { "...MetricValue" }, "secondary": [ { "...MetricValue" } ],
              "season": "2025-26" } ] }
```

For `scope: "all_time"` each row's `season` names the season being ranked.

### `game_log`

```json
{ "player": { "...PlayerRef" }, "season": "2025-26", "seasonType": "Regular Season",
  "columns": [ { "...MetricDescriptor" } ],
  "seasonBests": { "pts": 38, "ts_pct": 0.812 },
  "rows": [ { "gameId": "0022500512", "date": "2026-01-02", "opponentAbbr": "BOS",
              "isHome": true, "result": "W", "score": "118-112", "started": true,
              "values": { "pts": 32 }, "availability": "full" } ] }
```

### `trend_chart`

```json
{ "metric": { "...MetricDescriptor" }, "rollingWindow": 5,
  "leagueAverage": 0.5671, "yDomain": { "min": 0.38, "max": 0.78 },
  "series": [ { "id": "2544", "label": "LeBron James", "colorIndex": 0,
                "points": [ { "x": "2026-01-02", "y": 0.641, "rolling": 0.598,
                              "gameId": "0022500512", "opponentAbbr": "BOS" } ] } ] }
```

### `four_factors`

```json
{ "team": { "...TeamRef" }, "season": "2025-26", "seasonType": "Regular Season",
  "offense": [ { "key": "efg_pct", "label": "eFG%", "weight": 0.4, "value": 0.556,
                 "displayValue": "55.6%", "leagueAverage": 0.538, "percentile": 0.72,
                 "rank": 8 } ],
  "defense": [ { "key": "opp_efg_pct", "…": "same shape" } ] }
```

Factor order is fixed: `efg_pct`, `tov_pct`, `oreb_pct`, `ftr` (weights 0.40 / 0.25 / 0.20 / 0.15).

### `shot_profile`

```json
{ "subject": { "...subject wrapper" }, "season": "2025-26", "seasonType": "Regular Season",
  "zones": [ { "zone": "rim", "label": "At Rim", "fga": 6.2, "fgPct": 0.684,
               "shareOfFga": 0.31, "pointsPerShot": 1.368, "leagueFgPct": 0.652,
               "leagueShareOfFga": 0.28 } ],
  "threePointRate": 0.42, "freeThrowRate": 0.28, "note": null }
```

Zones, in order: `rim`, `paint_non_rim`, `mid_range`, `corner_three`, `above_break_three`.

### `comparison`

```json
{ "season": "2025-26", "seasonType": "Regular Season", "normalization": "percentile",
  "style": "bars", "metrics": [ { "...MetricDescriptor" } ],
  "subjects": [ { "player": { "...PlayerRef" }, "colorIndex": 0,
                  "values": [ { "...MetricValue" } ] } ] }
```

### `scoreboard`

```json
{ "date": "2026-01-02", "isLatestCompleted": true, "allFinal": true,
  "games": [ { "...GameRef",
               "topPerformers": [ { "player": { "...PlayerRef" }, "teamAbbr": "LAL",
                                    "line": "32 PTS · 8 REB · 11 AST",
                                    "value": { "...MetricValue" } } ] } ] }
```

### `daily_movers`

```json
{ "date": "2026-01-02", "metric": { "...MetricDescriptor" }, "direction": "best",
  "rows": [ { "rank": 1, "player": { "...PlayerRef" }, "gameId": "0022500512",
              "opponentAbbr": "BOS", "isHome": true, "result": "W",
              "line": "46 PTS · 9 AST", "value": { "...MetricValue" },
              "seasonAverage": 24.1, "delta": 21.9 } ] }
```

### `team_efficiency`

```json
{ "season": "2025-26", "seasonType": "Regular Season", "sortBy": "net_rtg", "style": "table",
  "leagueAverage": { "off_rtg": 114.1, "def_rtg": 114.1, "pace": 98.6 },
  "rows": [ { "rank": 1, "team": { "...TeamRef" }, "wins": 27, "losses": 14,
              "values": { "off_rtg": 118.2, "def_rtg": 110.4, "net_rtg": 7.8, "pace": 99.4 },
              "ranks": { "off_rtg": 3, "def_rtg": 5, "net_rtg": 1 } } ] }
```

### `next_game_projection`

A projected box score. See [`docs/PROJECTION.md`](../docs/PROJECTION.md) for the derivation;
the short version is `Ŝ = M̂ · r̂_reg · f_pace · f_opp · f_home · f_rest`.

```json
{ "player": { "...PlayerRef" },
  "game": { "gameId": "0022500640", "date": "2026-01-04", "opponentAbbr": "BOS",
            "opponent": { "...TeamRef" }, "isHome": true, "restDays": 2, "isBackToBack": false,
            "opponentDefRtg": 111.8, "expectedPace": 99.1 },
  "projectedMinutes": { "value": 34.2, "displayValue": "34.2", "halfLifeGames": 2,
                        "seasonAverage": 33.8, "low": 27.0, "high": 40.5 },
  "lines": [
    { "metric": "pts", "descriptor": { "...MetricDescriptor" },
      "mean": 28.4, "displayValue": "28.4",
      "low": 19, "high": 38, "intervalLevel": 0.8,
      "seasonAverage": 27.1, "delta": 1.3,
      "ratePerMinute": 0.831, "shrinkageK": 81, "exposureMinutes": 1240.0,
      "shrinkageWeight": 0.94, "halfLifeGames": 6,
      "dispersionAlpha": 0.061, "dispersionMultiplier": 1.34,
      "availability": "estimated" } ],
  "factors": [ { "key": "pace", "label": "Pace", "value": 1.021,
                 "explanation": "Both teams play slightly faster than league average.",
                 "contributions": { "pts": 0.58, "reb": 0.16, "ast": 0.12 } } ],
  "combo": { "label": "PTS+REB+AST", "mean": 45.1, "sd": 9.21, "sdIfIndependent": 8.17,
             "inflation": 0.127, "low": 33, "high": 57,
             "correlation": [[1.0, 0.3, 0.17], [0.3, 1.0, 0.2], [0.17, 0.2, 1.0]] },
  "method": { "summary": "Opportunity x rate, shrunk per statistic.",
              "minutesHalfLifeGames": 2, "correlationApplied": true,
              "dispersionShrinkageGames": 60 },
  "notes": [ "Projected minutes carry most of the error." ] }
```

Every `line` carries `availability: "estimated"` — a projection is never a record, and the
client renders it with the same treatment as a pre-1997 derived stat. `game` is null when the
player has no scheduled next game, in which case the payload still projects against a
league-average opponent and says so in `notes`.

`low`/`high` are the bounds of the negative-binomial interval at `intervalLevel`, **after** the
per-player dispersion multiplier. `sdIfIndependent` exists so the client can show what ignoring
residual correlation would have claimed; the gap is the point.

`factors[].contributions` maps each `lines[].metric` to that factor's signed share of the
statistic's projected mean, in the statistic's own units: `mean × (value − 1)`. It is a
**first-order attribution, not a decomposition** — the factors multiply, so the contributions do
not sum to the difference from a neutral-context projection. Clients must present them as the
largest movers, never as a column that adds up. The key is absent when `showFactors` is false,
along with the rest of `factors`.

### `projection_board`

Tonight's projected lines for several players, each drawn as a range. Designed from the
`Hardwood Predictions` handoff, option 2b — see [`docs/BROADSHEET.md`](../docs/BROADSHEET.md).

```json
{ "date": "2026-01-04", "throughDate": "2026-01-05", "selectionDate": "2026-01-02",
  "gameCount": 7,
  "reference": "season_average", "referenceLabel": "season avg",
  "rows": [
    { "player": { "...PlayerRef" }, "matchup": "LAL @ DEN", "opponentAbbr": "DEN",
      "isHome": false, "gameId": "0022500640", "gameDate": "2026-01-04",
      "metric": "pts", "descriptor": { "...MetricDescriptor" },
      "projection": 34.2, "displayValue": "34.2",
      "low": 26, "high": 43, "intervalLevel": 0.8,
      "referenceValue": 31.5, "delta": 2.7, "deltaZ": 0.38,
      "projectedMinutes": 34.1, "availability": "estimated" } ],
  "note": "The band is the model's 10th to 90th percentile; the dot is the projection." }
```

**Three dates, because they are genuinely three different things.** `selectionDate` is the last
completed slate, which is only how the candidate players were chosen; `date` and `throughDate`
bracket the games actually being projected, since each player is projected to whatever *they*
play next and those need not fall on one night. `throughDate` is null when every row is on
`date`. A client heading the board with `selectionDate` would put last night's date above
tomorrow night's numbers. `gameCount` counts the distinct games in `rows`, not the slate.

`referenceValue` is the mark the band is read against — the player's season or career average
for that metric, or null when `reference` is `"none"`. **It is never a market line:** no odds,
price, edge or implied probability appears anywhere in this payload, for the reasons in
[`docs/PROJECTION.md`](../docs/PROJECTION.md) §6.

`deltaZ` is `delta` divided by the projection's own predictive spread, recovered from the
published interval. **Rows are sorted by `abs(deltaZ)` descending, not by `abs(delta)`** — that
disagreement with a player's baseline is the only reason to look at the board, but a raw delta
is not comparable across statistics, so ranking on it returns a board of nothing but points
every night. `deltaZ` is a ranking scale, not a significance claim: values well under 1 are
normal, because one game's noise is large. It is null when `reference` is `"none"` or the
engine published no interval; with no reference, rows sort by `projection` over that same
spread.

### `fantasy_draft_board`

A **table**. Nine-category fantasy value for a whole season, ranked. See
[`docs/FANTASY.md`](../docs/FANTASY.md).

```json
{ "season": "2025-26", "seasonType": "Regular Season", "scoring": "categories",
  "categories": ["pts","fg3m","reb","ast","stl","blk","tov","fg_pct","ft_pct"],
  "puntCategories": ["ft_pct"],
  "teams": 12, "rosterSpots": 13,
  "nextPick": { "overall": 5, "round": 1, "pickInRound": 5 },
  "poolSize": 150, "replacementValue": -3.2,
  "weakestCategories": ["ast", "fg3m", "stl"],
  "rosterStrength": { "pts": 2.1, "reb": 4.4, "…": 0.0 },
  "columns": [
    { "key": "score", "label": "Value", "format": "decimal2", "group": "summary",
      "align": "trailing", "higherIsBetter": true, "signed": true, "punted": false },
    { "key": "team", "label": "Team", "format": null, "group": "summary",
      "align": "leading", "higherIsBetter": null, "signed": false, "punted": false },
    { "key": "z_ft_pct", "label": "zFT%", "format": "decimal2", "group": "impact",
      "align": "trailing", "higherIsBetter": true, "signed": true, "punted": true } ],
  "rows": [
    { "rank": 1, "round": 1, "pickInRound": 1, "player": { "...PlayerRef" },
      "baselineRank": 1, "totalZ": 16.94, "valueOverReplacement": 20.1,
      "suggestion": 1.88, "espnPoints": 52.4, "yahooPoints": 45.1,
      "values": { "score": 1.88, "team": "SAS", "gp": 68, "mpg": 31.0,
                  "pts": 28.3, "fg_pct": 0.5355, "fga": 18.8, "z_pts": 2.29 },
      "fills": ["ast"], "reason": "Best available; carries blocks and rebounds",
      "availability": "estimated" } ],
  "note": "Values are z-scores against the top 150 players of 2025-26…" }
```

**Columns ship as data.** The server owns the layout; a client renders whatever arrives and adding
a column needs no app release. A `columns[]` entry is deliberately **not** a `MetricDescriptor` —
nine of them are pool-relative z-scores and three are identity, none of which exist in
`metrics.json`, and §2's rule is that anything carrying `key`/`name`/`shortName`/`category`/
`format`/`availability` together *is* verbatim a catalog entry.

| key | meaning |
| --- | --- |
| `format` | a `MetricFormat`, or `null` for a text column |
| `group` | `summary`, `production` or `impact` — the strips a reader pages between |
| `align` | `leading` for text, `trailing` for numbers so the decimals line up |
| `higherIsBetter` | `null` where neither — field-goal attempts are volume, not virtue |
| `signed` | render with an explicit `+` |
| `punted` | this category is weighted 0; **only ever true on a `z_` column** |

`rows[].values` is keyed, not a parallel array, so a column a client does not know is skipped
rather than shifting every cell after it. An absent key is an em dash and **never** a zero (§6).
Every value is a number except `team`.

**The column order is a FanScout export's**, because that is the sheet a reader is most likely to
have beside the app: rank and player, a value, the identity block, the raw per-game line, then the
nine z-scores. The z block keeps that export's ordering — `zPTS zTPM zAST zREB…` — which differs
from the raw block's `PTS TPM REB AST…`. That is the export's quirk, reproduced so the two diff
column by column.

Two columns such an export has are **not** served, and their absence is deliberate:

* **Contract status.** Nothing in this project knows it. It is not in a box score, not in the
  identity snapshot, and not on any NBA.com endpoint the ingest touches.
* **A proprietary "Value".** `score` occupies that slot instead — the weighted mean this engine
  computes and `docs/FANTASY.md` derives. A third party's value is not reproducible from the nine
  z-scores printed beside it, so keeping the header and changing the number underneath would be
  the worst of both.

`score` and `totalZ` are computed with the **punt weights in force**, so the value column is
monotonic with `rank`. `z` values are never reweighted: a punt zeroes a category's contribution to
a total, it does not change what a player did.

### `fantasy_trade`

```json
{ "season": "2025-26", "seasonType": "Regular Season", "puntCategories": [],
  "give": { "label": "give", "count": 2, "totalZ": 8.1, "espnPoints": 71.2,
            "yahooPoints": 60.4, "players": [ { "player": { "...PlayerRef" },
            "totalZ": 4.9, "baselineRank": 11, "gamesPlayed": 68,
            "categories": { "pts": 1.2, "…": 0.0 }, "availability": "estimated" } ],
            "missing": [] },
  "get":  { "…": "same shape" },
  "categories": [
    { "category": "pts", "give": 2.4, "get": 3.1, "change": 0.7, "net": 0.34,
      "verdict": "gain", "punted": false } ],
  "changeZ": 1.21, "rosterAdjustment": -3.2, "netZ": -1.99,
  "replacementValue": -3.2, "poolSpread": 2.06,
  "verdict": "slight loss", "bands": { "fair": 0.75, "clear": 2.0 },
  "points": { "espn_points": { "change": 6.4, "net": -8.1, "verdict": "clear loss" },
              "yahoo_points": { "…": "same shape" } },
  "sensitivity": { "base": -1.99, "low": -4.4, "high": 0.3, "width": 4.7,
                   "lowScenario": "The player you give up gains 15% of his minutes",
                   "highScenario": "The player you get misses 22 games",
                   "flips": true,
                   "scenarios": [ { "key": "base", "label": "As projected", "net": -1.99 } ] },
  "note": "The range is a sweep over named assumptions, not a confidence interval…" }
```

**`rosterAdjustment` is a separate key on purpose.** Give two and get one and the freed slot is
refilled from waivers at `replacementValue`, which is below pool average by construction — often
a larger number than the difference between the players themselves. `netZ` is
`changeZ + rosterAdjustment`, and a client that shows only the net leaves a reader unable to tell
a bad trade from slot arithmetic.

**`sensitivity` is a range, not an interval, and must never be labelled as one.** There is no
probability anywhere in it. It is the same trade recomputed under four named scenarios — the
player you get misses 22 games or loses 15% of his minutes, and the same two for the player you
give up — with the scenario that produced each end. `flips` is true when those scenarios disagree
about the sign, which is the most useful thing the block can say. A predictive interval is
deliberately *not* offered: the engine's negative-binomial band is a single-game count for one
player in one category, and an honest variance for a signed sum over eight players and nine
standardised categories needs a covariance matrix this project does not have.

`poolSpread` is the population SD of `totalZ` across the pool, reported so `bands` can be read
against the league they are being applied to rather than taken as universal thresholds.

### `career_arc`

```json
{ "player": { "...PlayerRef" }, "metric": { "...MetricDescriptor" }, "xAxis": "season",
  "seasons": [ { "season": "2003-04", "seasonType": "Regular Season", "age": 19,
                 "teamAbbr": "CLE", "gp": 79, "value": 18.3, "displayValue": "18.3",
                 "availability": "estimated" } ],
  "playoffSeasons": [ { "…": "same shape" } ],
  "eraBoundaries": [ { "season": "1996-97", "label": "Advanced box scores begin" } ],
  "peak": { "season": "2008-09", "value": 31.7 } }
```

### `team_matchup`

```json
{ "league": "nba", "season": "2025-26", "seasonType": "Regular Season", "phase": null,
  "freshness": { "...Freshness" }, "game": { "...GameRefL" },
  "teams": [ { "side": "home", "team": { "...LeagueTeamRef" }, "…": "TeamMatchup §4" } ],
  "leagueAverage": { "pointsPerGame": 113.4, "teams": 30 },
  "projection": { "...GameProjection" }, "availability": "full", "notes": [ "…" ] }
```

A league widget: the payload is `TeamMatchup` (League payloads, below), exactly what
`GET /v1/matchups` and `GET /v1/teams/{teamId}/matchup` (or the `/v1/el` pair) return. Config:
`league` (`nba` | `euroleague`, default `nba`), `team` (default `$favorite_team`; NBA) or `club`
(a three-letter club code; EuroLeague), `opponent` / `opponentClub` (optional; empty means the next
opponent), `season`, `window` (3–15, default 5). A EuroLeague payload adds `seasonCode` beside
`season`. With no opponent chosen and no game scheduled, the payload is the two sides of the
subject's most recent game as they stand now, with `game: null` and a note.

### `defense_by_position`

```json
{ "league": "nba", "season": "2025-26", "seasonType": "Regular Season", "scheme": "gfc",
  "basis": "perGame", "regulationMinutes": 48, "freshness": { "...Freshness" },
  "team": { "...LeagueTeamRef" }, "window": { "kind": "season", "games": 41, "requested": null },
  "pointsAllowedPerGame": 112.3, "leaguePointsAllowedPerGame": 113.4,
  "buckets": [ { "position": "G", "…": "DefenseByPosition §4" } ],
  "provisional": false, "withheld": null, "coverage": { "listed": 1.0, "workbookListing": 0.0,
  "unknown": 0.0 }, "reconciliation": { "…": "…" }, "method": { "…": "…" }, "methodMessage": null,
  "availability": "full", "caveat": "…", "notes": [ "…" ] }
```

A league widget: `DefenseByPosition` for the configured `team` (NBA) or `club` (EuroLeague), or
`DefenseByPositionTable` (no `team` key; a `teams` list) when none is configured. Config: `league`,
`team` / `club` (optional), `season`, `window` (0 is the season), `basis` (`perGame` |
`perMinute`), `scheme` (`gfc` | `workbook5`; the five-position scheme is EuroLeague only, and asking
the NBA for it shows the three positions with a note). A EuroLeague payload carries `seasonCode` and
`phase` where an NBA one carries `seasonType`.

### `availability_report`

```json
{ "league": "nba", "asOf": "2026-01-03T11:00:00Z", "freshness": { "...Freshness" },
  "state": "fresh", "message": "…",
  "teams": [ { "team": { "...LeagueTeamRef" }, "reportState": "submitted", "entries": [ { "…": "§10" } ] } ],
  "news": null, "attribution": "…" }
```

A league widget: `AvailabilityReport` (§10), exactly what `GET /v1/availability` or
`GET /v1/el/availability` returns. Config: `league`, `team` / `club` (optional; none is the whole
slate), `includeNews` (headline links only when a feed is switched on). The result's `availability`
is derived from the payload's `state`: `fresh` is `full`, `stale` is `partial`, anything else
(no report yet, unreadable, disabled) is `unavailable`, and the payload's `message` is the note.

### `slate_projections`

```json
{ "league": "nba", "date": "2026-01-03", "round": null, "freshness": { "...Freshness" },
  "model": { "key": "hardwood", "…": "…" }, "games": [ { "...GameProjection" } ],
  "review": null, "availability": "estimated", "notes": [ "…" ] }
```

A league widget: `SlateProjections`, exactly what `GET /v1/projections` or `GET /v1/el/projections`
returns. Config: `league`, `date` (`next`, `latest` or an ISO date; NBA), `round` (0 is the next
round; EuroLeague). A projection is an estimate, so a slate with games is `estimated`; a slate with
none is `unavailable`. There is no probability of winning and no line to compare with (§11). The
EuroLeague payload carries no `availability` key; the resolver derives it.

The four league widgets are never answered `unchanged` by `POST /v1/dashboard/resolve`: their
freshness is the payload's own `freshness` block, not the NBA's `syncVersion`.

### League payloads

The payloads of the league routes in §3, and of the four league widgets above
(`team_matchup`, `defense_by_position`, `availability_report`, `slate_projections`), which are thin
resolvers over the same builders, so a tile's payload is exactly the route's. Every payload carries
`league` and `freshness` (§2). Shapes are written in TypeScript
notation; `T | null` means the key is always present and may be null, `k?` means the key may be
absent. A client ignores keys it does not know. Percentages are fractions in [0, 1]; anything not
recorded is `null`, never `0`.

#### `TeamMatchup`

Two teams side by side, or one team for its next game.

```ts
TeamMatchup = {
  league, season, seasonType: string | null, phase: string[] | null, freshness,
  game: GameRefL | null,
  teams: [{
    side: "home" | "away" | null, team: LeagueTeamRef, record: {wins, losses}, games,
    pointsPerGame, pointsAllowedPerGame, differentialPerGame,
    pointsPerRegulation: number | null, pointsAllowedPerRegulation: number | null,
    latestGame: FormGame | null, form: FormGame[],
    lastN:  {window, games, pointsPerGame, pointsAllowedPerGame},
    last10: {window: 10, games, pointsPerGame, pointsAllowedPerGame},
    venueSplits: {home: Split, away: Split, neutral: Split | null},
    adjustedPointsAgainst: {value: number | null, games},
    adjustedPointsFor:     {value: number | null, games},
    availability: AvailabilitySummary | null,
    defenseSummary: {pointsAllowedPerGame, buckets: [{position, pointsAllowedPerGame,
                     deltaPerGame, band}], withheld} | null
  }, ...],
  leagueAverage: {pointsPerGame, teams},
  projection: GameProjection | null,
  availability: "full" | "partial" | "estimated" | "unavailable", notes: string[]
}
FormGame = {gameId, date, opponent: LeagueTeamRef, isHome, isNeutral: boolean | null,
            teamScore, opponentScore, result: "W" | "L", overtimePeriods: number | null}
Split = {games, pointsPerGame: number | null, pointsAllowedPerGame: number | null}
AvailabilitySummary = {out, doubtful, questionable, probable, keyAbsences: AbsenceEntry[],
                       freshnessState, asOf}
```

Only final games count, newest first. `pointsAllowedPerGame` is the mean of the opponents' points
and answers "how many do they let opponents score". `pointsPerRegulation` and
`pointsAllowedPerRegulation` rescale each game to regulation time and are `null` when the team's
playing time is unknown for any game. `adjustedPointsAgainst` is the mean, over games, of the
opponent's points minus that opponent's own average in its other games (negative: the team holds
opponents below their usual output); `value` is `null` until at least five games qualify, and
`games` says how many did. There is **no rank anywhere** in this payload. The EuroLeague's
neutral-site games appear only under `venueSplits.neutral`; the NBA never has one (it does not
record neutral sites, so `isNeutral` is `null` and every NBA venue is an assumption). A
EuroLeague payload says in `notes` that only EuroLeague games are tracked, not friendlies or
domestic games.

#### `DefenseByPosition` and `DefenseByPositionTable`

Points allowed by the position of the opposing players, **not** who guarded whom: it counts the
points scored by opponents *listed at* a position. Every payload carries a `caveat` saying so.

```ts
DefenseByPosition = {
  league, team: LeagueTeamRef, season, seasonType | phase, scheme: "gfc" | "workbook5",
  basis: "perGame" | "perMinute", regulationMinutes, freshness,
  window: {kind: "season" | "lastGames", games, requested: number | null},
  pointsAllowedPerGame, leaguePointsAllowedPerGame,
  buckets: [{ position: "G" | "F" | "C" | "PG" | "SG" | "SF" | "PF" | "unknown", label,
              pointsAllowedPerGame, leagueAverage, deltaPerGame,
              opponentMinutesPerGame, pointsPerRegulationMinutes, leagueRate,
              share, leagueShare,
              rawIndex: number | null, index: number | null, standardError: number | null,
              band: "better" | "typical" | "worse" | null }],
  provisional: boolean,
  withheld: {reason: "minimumGames" | "positionCoverage" | "leagueSample", message} | null,
  coverage: {listed, workbookListing, unknown},
  reconciliation: {sumOfBuckets, pointsAllowedPerGame, unreconciledGames,
                   identity: "sum(deltaPerGame) = pointsAllowedPerGame - leaguePointsAllowedPerGame"},
  method: {minimumGames, provisionalBelowGames, coverageCeiling, shrinkage: "empiricalBayes",
           leagueReliability: {G, F, C}, leagueSignal: "detected" | "none detected" | null,
           bandRule, positionSource, taxonomy, limitations: string[]},
  methodMessage: string | null, availability, caveat, notes
}
DefenseByPositionTable = { league, season, scheme, basis, freshness, leaguePointsAllowedPerGame,
  teams: [{team, games, pointsAllowedPerGame, buckets: [...same bucket shape...], provisional,
           withheld}],   /* sorted by pointsAllowedPerGame ascending; no rank field */
  method, methodMessage: string | null, caveat }
```

`methodMessage` is the sentence to show when `method.leagueSignal` is `"none detected"` ("No team's
points allowed at this position differ from the league by more than chance this season") and is
`null` otherwise. A EuroLeague payload also carries `seasonCode` (beside `season`) and `phase`
(`null` for all phases) where an NBA one carries `seasonType`.

How to read it. The buckets **sum to the headline**: `sum(deltaPerGame)` equals
`pointsAllowedPerGame - leaguePointsAllowedPerGame` over the window's reconciled games, and the
payload shows that identity, so the user can check it. A game whose positions do not reconcile to
the final score is excluded and counted in `unreconciledGames`. Each player counts at exactly one
position per season (a hybrid splits half and half), taken from the league's own listing, never
from where he lined up in a game; a player with no listing falls in `unknown`, which is shown and
never redistributed.

`index` is a team's points allowed at that position over the league's, shrunk toward 1 by an
empirical-Bayes estimate of how much teams really differ (a lower index is a better defence); it
is `rawIndex` pulled toward 1 by how noisy the team's sample is. `standardError` is the
raw index's. A `band` is present only when the table is not `provisional` and the league's
reliability at that position reaches 0.2, and it uses a family-wise (Bonferroni) cut-off over every
displayed cell, so **a league with no positional signal shows essentially no `better` or `worse`**
and `method.leagueSignal` says `"none detected"`. That is correct behaviour, not a bug. **Never show
`rawIndex` as a ranking.**

`withheld` replaces every index, standard error and band with `null` and its `message` goes on
screen. `minimumGames`: fewer games than the league's minimum (NBA 10, EuroLeague 6).
`positionCoverage`: more than 5% of the team's, or the league's, points allowed went to players
with no listed position. `leagueSample`: fewer than 80% of teams have enough games to estimate
the spread between teams. The raw buckets, `unknown` included, still show because they are
recorded facts. `provisional` (NBA under 25 games, EuroLeague under 12) must be visibly marked and
shows no band. The injury layer never changes a defence, and `method.limitations` says so.
`scheme: "workbook5"` (EuroLeague only, opt-in) uses PG, SG, SF, PF and C as the workbook's author
assigned them; it is always `availability: "estimated"` and never the default.

#### `GameProjection`, `SlateProjections`, `GameProjectionDetail`, `ProjectionReview`

A projected score for each side. **There is no probability of winning in any of these**, and no
line or total to compare with (§11).

```ts
GameProjection = {
  league, game: GameRefL, freshness,
  home: SideProjection, away: SideProjection,
  margin, marginRange80: {low, high} | null,
  projectedWinner: LeagueTeamRef | null, isTossUp, summary,
  combinedPoints, combinedAvailabilityEffect, homeAdvantagePoints, venueAssumed,
  intervalBasis: "assumed" | "fittedPrevSeason" | "fittedLedger" | null,
  model: {key, version, kind: "latest" | "locked" | "reconstructed" | "imported",
          computedAt, inputsCutoff, capPolicy,
          constants: [{key, value, provenance, isDefault}]},
  assumptions: {assumedAvailable: {home: number, away: number, basis}, staleEntriesIgnored: number},
  result: {homePts, awayPts, marginMiss, winnerCalled: boolean | null} | null,
  availability: "estimated", notes
}
SideProjection = { team: LeagueTeamRef, projectedPoints, range80: {low, high} | null,
  fullStrengthPoints, availabilityEffect, attackIndex, attackIndexAfterAvailability,
  defenceIndex, capBinding, unassignedPoints, replacementPoints,
  keyAbsences: AbsenceEntry[] }
SlateProjections = { league, date | null, round | null, freshness, model, games: GameProjection[],
  review: ProjectionReviewSummary | null, notes }
GameProjectionDetail = { current: GameProjection | null, locked: GameProjection | null,
  history: [{computedAt, kind, homePts, awayPts}] }
ProjectionReview = { league, scope: {season, seasonCode?, round?, date?}, freshness,
  byModel: [{modelKey, games, decidedGames, winnersCalled, tossUps, meanAbsMarginMiss,
             meanAbsScoreMiss, meanAbsCombinedMiss}],
  reconstructed: {...same as a byModel row} | null,
  games: [{game, locked: {homePts, awayPts}, result, marginMiss, winnerCalled}], notes }
```

`decidedGames` is `games` less the toss-ups: a toss-up names no winner, so it can be neither called
nor missed, and `winnersCalled` is out of `decidedGames`.

`margin` is home minus away; `isTossUp` is true under half a point, and `projectedWinner` is then
`null` and `summary` is `"Toss-up"` (otherwise `"ZZA by 8.7"`). `combinedPoints` is the sum of the
two projections to one decimal, **never rounded to a half point and never called a total to beat**.
`homeAdvantagePoints` is zero for a neutral game; `venueAssumed` is true when the venue was not
known and the league default was applied (always, for the NBA). `intervalBasis: "assumed"` is
shown as an "assumed spread": it is the workbook's own, not yet checked against results. A
`range80` or `marginRange80` is `null` until a spread exists.

`availability` is always `"estimated"`: a projection is never a record. `assumptions` states how
many players with no entry the model assumed to play, and why (§10.2). `capBinding` is true when
teammates' absorption of a missing player's scoring hit its cap. Under the default policy the
team figure then equals the sum of the players' plus `replacementPoints`, the points credited at
replacement level to whoever fills the minutes the capped players cannot (at most the
replacement-level refill, so the cap never removes the replacement floor; 0 when the cap does not
bind); under the workbook's own policy `replacementPoints` is 0 and `unassignedPoints` reports the
gap. All three are null on a frozen (`locked`) or `imported` row, which stores scores and indices only.
A `locked` projection was frozen before tip-off and is the
only kind the review judges; a `reconstructed` one was rebuilt afterwards from inputs dated before
tip-off and is reported separately; an `imported` one is a score the user's workbook published.
The review reports calls and misses of the model's own numbers; it compares nothing with an outside
price.

#### `AvailabilityReport` and `NewsLink`

```ts
AvailabilityReport = { league, asOf: string | null, freshness,
  state: "fresh" | "stale" | "noReportYet" | "unreadable" | "disabled",
  message,
  teams: [{ team: LeagueTeamRef, reportState: "submitted" | "notYetSubmitted" | "noReport",
    entries: [{ statusId, overrideId?, player: LeaguePlayerRef | null, playerName,
      status: string | null, statusLabel, chanceOfPlaying: number | null,
      modelStatus: string | null, reasonCategory: string | null, reasonText: string | null,
      expectedReturnText: string | null,
      expectedReturn: {roundFrom, roundTo, date} | null,
      game: GameRefL | null, isOverride, inForce, outOfForceReason?, isStale, ageMinutes,
      source: Source }] }],
  news: NewsLink[] | null, attribution }
NewsLink = { itemId, title, link, publishedAt, sourceName,
             teams: LeagueTeamRef[], players: LeaguePlayerRef[] }
NewsLinks = { league, freshness, items: NewsLink[], notes }
AbsenceEntry = { player: LeaguePlayerRef, status: string | null, chanceOfPlaying: number,
                 expectedPointsLost: number, expectedMinutesLost: number,
                 inForce: boolean, isStale: boolean, source: Source }
```

`AbsenceEntry` is what `keyAbsences` and `absences` hold wherever a payload names the players a
team is missing (a matchup's `availability`, a projection's `home`/`away`, a `ClubView`).
`expectedPointsLost` and `expectedMinutesLost` are what the absence is worth to the team in the
model: the player's projected points and minutes times his chance of *not* playing. A list of
absences is ordered by `expectedPointsLost`, largest first, and holds only players the model
expects to miss time (a player assumed to play is not an absence, §10.2). `inForce: false` means
the entry no longer drives a projection (§10.3) and `isStale` that it is old enough to be
doubted; both are shown.

See §10 for what every field means. `state: "disabled"` carries the reason in `message`, for
instance that the NBA store holds the invented demo league and a real injury status is not shown
next to invented games. A headline is a title, a link, a date and a source name and nothing more:
no excerpt, no body.

#### `RoundView`, `ClubView`, `RoundScorers` (EuroLeague)

```ts
RoundView = { league, season, round, phase, freshness,
  status: "upcoming" | "inProgress" | "resultPending" | "complete",
  games: GameProjection[],
  summary: {games, tossUps, closestGame: GameRefL | null, averageCombinedPoints,
            homeWinnersProjected, awayWinnersProjected},
  notes }
ClubView = { league, team, season, freshness, coach, record, scoring: /* TeamMatchup's team block */,
  rating: {pfPrior, paPrior, priorIsEstimate, attackAdj, defenceAdj, projectedPointsFor,
           projectedPointsAgainst, attackIndex, defenceIndex, asOfRound,
           source, updateWeight: number | null, updateBasis: "locked" | "reconstructed" | null,
           extrapolated: boolean},
  squad: [{ player: LeaguePlayerRef, positionWorkbook5: string | null, age: number | null,
            role: string | null, basis, basisNote: string | null, isEstimate: boolean,
            projectedMinutes: number | null,
            per40: {pts, reb, ast, fg3m, stl, blk, tov} | null,
            seasonAverages: {games, min, pts, reb, ast, pir, fg2Pct, fg3Pct, ftPct} | null,
            status: string | null, chanceOfPlaying: number | null,
            availabilitySource: Source | null }],
  absences: AbsenceEntry[], nextGame: GameRefL | null, defenseSummary, notes }
RoundScorers = { league, round, freshness,
  clubs: [{team, game: GameRefL,
           players: [{player, status: string | null, chanceOfPlaying, modelPoints,
                      formAverage: number | null, formGames, projectedPoints}]}] }
```

`RoundView.status` of `"resultPending"` means the round's games were played and their results are
not loaded: its `notes` then say so and how to load them (enable live ingest, or import an updated
workbook). A round that was played before the data arrived is **never** shown as `"upcoming"`.
`RoundScorers` ranks each club's players by projected points and has no player interval and no
line of any kind. `squad[].basis` says where a player's per-40 rates came from: the league's
published numbers, an estimate typed into the workbook (shown as "your estimate; source not
recorded"), or a position prior; `isEstimate` is true for the last two and `basisNote` is the
sentence to show beside them. `rating.updateWeight` is the weight the club's last round update
carried, `updateBasis` says whether that update was measured against a projection frozen before
tip-off (`locked`) or one rebuilt afterwards (`reconstructed`), and `extrapolated` is true when
the weight came from the rule that continues the workbook's table rather than from the table.

#### `ElBoxScore`, `ElLine`, `ElPlayerStatsTable` (EuroLeague)

```ts
ElBoxScore = { league, game: GameRefL, partials: {home: number[], away: number[]} | null,
  attendance: number | null, statsStatus, notes,
  teams: [{team, totals: ElLine,
           players: [{player, participation: "played" | "dnp", isStarter: boolean | null,
                      line: ElLine | null}]}],
  freshness, sourceRef: {kind, label, ingestedAt} }
ElLine = {minutes, pts, fgm2, fga2, fg2Pct, fgm3, fga3, fg3Pct, ftm, fta, ftPct, oreb, dreb, reb,
          ast, stl, tov, blk, blkAgainst, pf, foulsDrawn, plusMinus, pir}
ElPlayerStatsTable = { league, season, perMode, sort, freshness,
  rows: [{player, team, games, values: {<metricKey>: number | null},
          gamesWithStat: {<metricKey>: number}}], notes }
```

A player who was listed and did not play is `participation: "dnp"` with `line: null`, never a row of
zeros; a player who was not dressed has no row. Every statistic that was not recorded is `null`:
the workbook's box scores carry no fouls drawn, blocks against, plus/minus or starter flag, so
those are `null` in those games. **A per-game average divides by the games that carry the stat**,
not by games played, and `gamesWithStat` is exposed beside each value so the divisor is visible;
`Per40` divides by the minutes of those same games; a percentage over no attempts is `null`, not
`0%`. `pir` is the EuroLeague's published Performance Index Rating, stored as published and never
recomputed. The keys of `values` are the metric keys of `contracts/metrics.json#/leagueMetrics/euroleague`
(§9.5), which is where a client finds each one's name, format and direction.

#### The rest of the EuroLeague's pages

The remaining `/v1/el` payloads, each the equivalent of one page of the user's workbook. Every one
carries `league`, `freshness`, and, where it is about a season, both `season` (`"2026-27"`) and
`seasonCode` (`"E2026"`).

```ts
ElMeta = { league, state: "ready" | "disabled" | "misconfigured" | "notConfigured" | "error",
  reason: string | null, isDemo, mode: string | null,
  seasons: [{code, label, isCurrent}], currentSeason, currentSeasonCode,
  rounds: [{round, phase, firstTipoffUtc: string | null, games,
            status: "upcoming" | "inProgress" | "resultPending" | "complete"}],
  clubs: LeagueTeamRef[], regulationMinutes: 40, perModes: string[], positionBuckets: string[],
  unverifiedClubCodes: string[], attribution, dataThrough: string | null, dayOneNotice, freshness }
ElTeams = { league, season, seasonCode, freshness,
  teams: [{team: LeagueTeamRef, record: {wins, losses}, games, pointsPerGame,
           pointsAllowedPerGame, rating: {attackIndex, defenceIndex, asOfRound}}], notes }
ElGames = { league, season, seasonCode, freshness, games: GameRefL[], notes }
ElPlayerDetail = { league, season, seasonCode, freshness, player: LeaguePlayerRef,
  club: LeagueTeamRef,
  registration: {dorsal, positionCode: 1 | 2 | 3 | null, positionName, positionWorkbook5,
                 role, age, active},
  birthDate, heightCm, weightKg, countryCode,       /* each null when the league did not say */
  seasonAverages: {games, values: {<metricKey>: number | null},
                   gamesWithStat: {<metricKey>: number}},
  seasonPer40: {<metricKey>: number | null},
  rate: {basis, basisNote: string | null, asOfRound, projectedMinutes,
         per40: {pts, reb, ast, fg3m, stl, blk, tov}} | null,
  availability: {status, chanceOfPlaying, inForce, isStale, source: Source} | null, notes }
ElPlayerGameLog = { league, season, seasonCode, freshness, player: LeaguePlayerRef,
  games: [{game: GameRefL, club: LeagueTeamRef, participation: "played" | "dnp",
           isStarter: boolean | null, stats: ElLine | null}], notes }
RatingsTable = { league, season, seasonCode, asOfRound, baseRound, freshness,
  leagueAveragePoints, regression,
  rows: [{team: LeagueTeamRef, pfPrior, paPrior, priorIsEstimate, attackAdj, defenceAdj,
          projectedPointsFor, projectedPointsAgainst, attackIndex, defenceIndex, source,
          updateWeight: number | null, updateBasis: "locked" | "reconstructed" | null,
          extrapolated, gamesCounted}],
  updates: [{round, team, opponent, gameId, gameNumber, weight, extrapolated, projectedScored,
             scored, projectedAllowed, allowed, basis: "locked" | "reconstructed"}], notes }
ElMethod = { league, freshness,
  constants: [{key, value, provenance, isDefault, description}],
  deviations: string[], limitations: string[] }
ModelSettings = { league, freshness,
  settings: [{key, value, provenance, isDefault, setAt: string | null, description}] }
ReviewQueue = { league,
  items: [ {kind: "status", statusId, playerName, team, source: Source}
         | {kind: "person", personCode, name, team: LeagueTeamRef | null} ] }
ElHealth = { league, status: "ok" | "unavailable", state, reason: string | null, isDemo,
             syncVersion: number | null, dataThrough: string | null, generatedAt }
ElSync = { league, syncVersion, dataThrough: string | null, mode: string | null, isDemo,
           lastSuccessAt: string | null, pausedUntil: string | null, generatedAt }
```

`ElMeta.state` and `ElHealth.state` are the §9.4 states; `ElHealth` is the one payload that needs no
key and never answers 503, and its `syncVersion` and `dataThrough` are `null` unless the
EuroLeague is `ready`. `ElMeta.rounds[].status` follows `RoundView.status`. `ElPlayerDetail`'s
`seasonAverages.values` and `seasonPer40` are keyed by the metric keys of
`contracts/metrics.json#/leagueMetrics/euroleague` (§9.5) and divide by the games that carry each
stat, exactly as `ElPlayerStatsTable` does; `seasonPer40.min` is `null` (minutes per forty minutes
is not a statistic). `RatingsTable` is the club-rating arithmetic made visible: each `updates` row
is one finished game's correction to one club, its `weight`, and the projection it was measured
against (`basis`: frozen before tip-off or rebuilt afterwards). `ElMethod.constants` lists every
number the model uses with its `provenance` (the workbook's, a default, fitted from results, or set
by the user), and `ModelSettings.settings` the same keys in their editable form. `ReviewQueue`
holds what waits for a person: a status whose player matched nobody, and a workbook-minted person
not yet matched to the league's official code; neither is ever guessed onto anyone.

#### `SourceList`

```ts
SourceList = { league, freshness,
  sources: [{ key, label, kind, enabled, state, reason: string | null,
              lastSuccessAt: string | null, lastError: string | null,
              robotsCheckedOn: string | null, attribution }],
  store?: {state, reason, kind}, attribution }
```

See §10.5. It renders as a "Sources and freshness" panel.

---

## 5. Dashboard layout document

The client owns layouts; the server only ships presets. A layout is stored on device and is
the exact JSON below (also what `GET /v1/presets` returns per preset).

```json
{
  "id": "8B1F…",
  "name": "My Dashboard",
  "icon": "square.grid.2x2",
  "accent": "orange",
  "schemaVersion": 1,
  "isPreset": false,
  "presetKey": "daily_recap",
  "createdAt": "2026-01-02T18:00:00Z",
  "updatedAt": "2026-01-02T18:30:00Z",
  "widgets": [
    { "id": "w1", "kind": "stat_tile", "title": "Net Rating", "size": "small",
      "config": { "subjectType": "team", "subjectId": 1610612747, "metric": "net_rtg" } }
  ]
}
```

* `presetKey` is retained after a user edits a preset copy, so the app can show "based on
  Daily Recap" and offer a reset.
* `title` is nullable; when null the client renders the widget catalog's default title.
* Widget `id` is unique within a layout and stable across edits (drag, resize, reconfigure).
* `accent` ∈ `orange`, `indigo`, `teal`, `red`, `amber`, `green`, `blue`, `purple`, `graphite`.
* `presentation` ∈ `tiles` (default) | `broadsheet`. It governs the whole page, not one widget:
  `tiles` is the card grid; `broadsheet` drops the card chrome for a single editorial column
  with hairline rules, uppercase kickers and serif numerals. A layout that omits the key is
  `tiles`, so every document written before this field remains valid.
* Order in `widgets` is the render order. There are no explicit grid coordinates: the layout
  flows into a 2-column (compact) or 4-column (regular) grid using each widget's `size`.

**Migration rule.** A layout whose `schemaVersion` is lower than the app's is upgraded on load:
unknown widget kinds are dropped with a user-visible note, unknown config keys are stripped,
and missing required keys take the catalog default. A layout whose `schemaVersion` is *higher*
is shown read-only with an "update the app" banner rather than being corrupted.

---

## 6. Era availability

Per the source research, the league's record is not uniform. Every payload that could mislead
carries an `availability` value:

| Value | Meaning | Client treatment |
| --- | --- | --- |
| `full` | Measured from official records for this era | Normal |
| `estimated` | Derived from box-score formulas, not possession data (pre-1996-97) | Dashed underline + "est." badge |
| `partial` | Some component games lack the inputs | Value shown with a caret and a footnote |
| `unavailable` | The stat did not exist in this era | `—`, tappable to explain why |

Boundaries live in `contracts/metrics.json#/eraBoundaries` and are echoed in `/v1/meta.coverage`:
basic box from 1946-47; rebounds 1950-51; minutes 1951-52; steals, blocks and the OREB/DREB
split 1973-74; individual turnovers 1977-78; the three-point line 1979-80; **per-game advanced
box scores, plus/minus, play-by-play and shot charts 1996-97**; player tracking 2013-14;
hustle stats 2016-17.

The client must never render `0` for an era-unavailable stat, and never compares a pre-1997
per-game rating against a modern one without the badge.

---

## 7. Errors

```json
{ "error": { "code": "player_not_found", "message": "No player with id 99999999.",
             "recoverable": false, "field": null, "requestId": "0f1c…" } }
```

| Code | HTTP | Meaning |
| --- | --- | --- |
| `bad_request` | 400 | Malformed query or body |
| `invalid_config` | 400 | A widget config failed validation (`field` names the key) |
| `too_many_widgets` | 400 | More than 24 widgets in one resolve |
| `unauthorized` | 401 | Missing or wrong `X-API-Key` |
| `player_not_found` / `team_not_found` / `game_not_found` | 404 | Unknown id |
| `club_not_found` | 404 | An unknown EuroLeague club code (`/v1/el`) |
| `invalid_status` | 400 | An availability status that is not one of the five (§10) |
| `league_unavailable` | 503 | The EuroLeague is off, misconfigured, or holds no data source; `message` says which (§9.4). `recoverable` is true |
| `metric_unavailable` | 422 | The metric does not exist for the requested era or subject |
| `season_not_loaded` | 422 | The season is valid but not yet ingested |
| `rate_limited` | 429 | Client exceeded the service's own limiter; `Retry-After` is set |
| `upstream_unavailable` | 503 | The ingest source is unreachable; cached data may be stale |
| `internal_error` | 500 | Anything else; `requestId` is in the server log |

Inside `POST /v1/dashboard/resolve`, per-widget failures never change the HTTP status — the
response is `200` with `status: "error"` on the individual result.

Accounts and dashboards (Hardwood Web) add these codes to the same table and the same envelope:

| Code | HTTP | Meaning |
| --- | --- | --- |
| `csrf_failed` | 403 | The synchroniser token or the `Origin` check failed on an unsafe method |
| `reauthentication_required` | 403 | The action needs a session less than ten minutes old |
| `invalid_credentials` | 401 | A login attempt, or a `currentPassword` check, did not match |
| `invalid_token` | 400 | A verify/reset/email-change token was unknown, expired or already used |
| `last_credential` | 409 | Unlinking this identity would leave the account with no way to sign in |
| `not_a_layout` | 400 | The document has none of `name`, `widgets` or `id`, or is not valid JSON |
| `layout_too_new` | 409 | The layout's `schemaVersion` is newer than this build reads |
| `too_many_dashboards` | 409 | The account already holds the maximum number of dashboards |
| `precondition_required` | 428 | `PUT /v1/dashboards/{layoutId}` with no `If-Match` header |
| `stale_write` | 409 | `If-Match` did not match the dashboard's current revision (§3's own body shape) |
| `payload_too_large` | 413 | A dashboard document, or an import payload, exceeds its byte cap |

---

## 8. Freshness model — "stats arrive as each game finishes"

1. The ingest worker polls the league scoreboard on a short cycle during the game window.
2. When a game flips to `final`, that single `gameId` is pulled (traditional + advanced box),
   written, and the affected season aggregates are recomputed.
3. `sync_state.sync_version` increments **once per finalized game**, and `data_through` moves
   to that game's date once every game on the slate is final.
4. The last three days are re-pulled on every nightly run, because the league issues
   post-hoc stat corrections.
5. Clients see the change through `GET /v1/sync`, driven by foreground, pull-to-refresh, a
   `BGAppRefreshTask`, and optionally the SSE stream.

`ttlSeconds` on each resolve result tells the client how long the payload is good for:
60s for `scoreboard` and `daily_movers`, 300s for player-level widgets and `availability_report`,
600s for leaderboards, team tables, `team_matchup`, `defense_by_position` and `slate_projections`,
3600s for `career_arc`.

---

## 9. Leagues

Hardwood serves two leagues from one process, the NBA and the EuroLeague, and holds them
apart on purpose: separate stores, separate id spaces, separate URL prefixes, one shared payload
format. A EuroLeague row can never appear in an NBA view, an NBA reseed can never wipe a
EuroLeague row, and a EuroLeague that fails to start never stops the NBA from serving.

### 9.1 One format, two prefixes

* **Prefixes.** `/v1` is the NBA and `/v1/el` is the EuroLeague. The suffixes of the shared
  routes are identical (`matchups`, `teams/{id}/matchup`, `games/{id}/matchup`,
  `teams/{id}/defense-by-position`, `defense-by-position`, `projections`, `games/{id}/projection`,
  `projections/review`, `availability`, `news`, `sources`, `model-settings`), so a client that can
  render one league renders the other by changing the prefix.
* **Every new payload carries `league` and `freshness`** (§2). Teams and players are
  `LeagueTeamRef` and `LeaguePlayerRef` with a string `id`; games are `GameRefL`. Clients key
  entities by `(league, id)`.
* **No table, id or season code is shared.** NBA ids are the NBA's ten-digit game ids and numeric
  team and player ids; the EuroLeague's are strings (below). A EuroLeague season code such as
  `E2026` exists only in the EuroLeague's store and is never accepted by an NBA route.

### 9.2 Discovery: `GET /v1/leagues`

An array with one row per league, so a client builds league switching from it and never
hard-codes a prefix:

```ts
{ key: "nba" | "euroleague", name, apiPrefix, enabled, state, reason: string | null, isDemo,
  currentSeason, syncVersion, dataThrough: string | null, regulationMinutes,
  perModes: string[], positionBuckets: string[], features }
```

The EuroLeague's row is filled in by the EuroLeague's own package through a registry, so the NBA
side never imports it; when the EuroLeague is switched off (`HARDWOOD_EL_ENABLED=0`) or did not
start, its row says `enabled: false` with a `state` and a `reason`. `isDemo` is true when a league
holds invented games.

### 9.3 What differs between the leagues

The constants a calculation needs are data, in `contracts/leagues.json`, and are the same ones the
service computes with:

| | NBA | EuroLeague |
| --- | --- | --- |
| Route prefix | `/v1` | `/v1/el` |
| Calendar day | US Eastern | Europe/Berlin |
| Regulation, overtime (minutes) | 48, 5 | 40, 5 |
| Per modes | `PerGame`, `Totals`, `Per36`, `Per100` | `PerGame`, `Totals`, `Per40` |
| Position buckets | G, F, C | G, F, C (opt-in `workbook5`: PG, SG, SF, PF, C, always estimated) |
| Defence: minimum games, provisional below | 10, 25 | 6, 12 |
| Defence: unlisted-position ceiling (team, league) | 5% | 5% |
| Opponent-adjusted points: qualifying games needed | 5 | 5 |
| Home advantage (points) | 2.5, a default; replaced by the fitted mean margin from 300 final games | 3.5, the user's workbook setting |
| Neutral sites | not recorded (every venue is an assumption) | recorded; `isNeutral` null means unknown |
| Chance of playing, by status | out 0, doubtful .25, questionable .5, probable .85, available 1 (defaults) | the same values, from the workbook |
| An availability entry is stale after | 60 minutes inside a reporting window, 24 hours outside one | 7 days, or once the team has played since the source's date |
| Attribution | *"Stats via NBA.com. Injury status from the NBA's official injury report."* | *"EuroLeague statistics from the EuroLeague's data service. Availability researched from the linked sources."* |

### 9.4 The EuroLeague's own rules

* **Season.** `E2026` or `2026-27`, defaulting to the current one. Payloads carry both the label
  (`season`) and the code (`seasonCode`). Phases are `RS`, `PI`, `PO` and `FF`; `phase` filters a
  query, and a payload that spans several says which.
* **Ids.** Clubs use the official club code (`PAN`). People use the official person code with any
  `P` prefix removed, or a minted `wb-<8 hex>` (from a workbook) or `demo-<n>` (the demo). Games
  use `E2026-0012` (season code and four-digit game code), or `E2026-R03-01` for a fixture a
  workbook listed before the league assigned it a game code; the id then changes to the official
  one when the data service names the game, and nothing else about the game does.
* **Club codes are looked up by system.** The workbook's `PAR` (Paris) and the league's `PAR`
  (believed to be Partizan) are different clubs, so `meta.unverifiedClubCodes` names any code the
  crosswalk has not yet confirmed against the data service, and a name that matches two people
  goes to `GET /v1/el/review-queue` and is never guessed.
* **State.** `meta.state` is `ready`, `disabled` (switched off), `misconfigured` (its store file is
  the NBA's), `notConfigured` (nothing to hold yet) or `error`; `reason` says why. Every route but
  `meta` and `health` answers `503 league_unavailable` with that reason in any state but `ready`.
* **Demo.** A synthetic EuroLeague of invented clubs (`ZZA` to `ZZT`) and players exists for
  offline use and for the tests. `isDemo` is true throughout it, and a store is either real or
  synthetic and never both.
* **What it knows on day one.** `meta.dayOneNotice` says plainly what is loaded and what is not,
  in the user's words: which rounds have results, which are fixtures only, and that availability
  is sourced and dated, never live. A round whose games were played before their results arrived
  is `resultPending`.
* **Scope.** EuroLeague games only. Friendlies, domestic leagues and national-team games are not
  tracked, and every EuroLeague payload that averages says so in `notes`.
* **Computed on read.** Season aggregates are summed from the box-score lines when asked, with the
  per-column divisor rule of §4.

### 9.5 Stat vocabulary

`contracts/metrics.json` has two lists on purpose. `metrics` is the NBA catalog (62 entries,
including `opp_pts`, "Points Allowed", a team metric available from 1946-47): every key in it
resolves against NBA rows and carries NBA-era availability. **`leagueMetrics.euroleague` is the
EuroLeague's** (23 entries): the stats a EuroLeague box score has, with the same entry shape plus
`leagues` and `perModes`. PIR is only in the second list, because in the first it would be a
permanently null NBA metric that lied about its era. Where both leagues have a stat (`pts`, `reb`,
`fg3m` and the rest) both lists name and format it identically, and a test proves it. The store's
`fgm3`, `fga3` and `pir_official` are `fg3m`, `fg3a` and `pir` on the wire. A client formats a
EuroLeague stat from `leagueMetrics.euroleague` and an NBA one from `metrics`. Their `availability`
describes what Hardwood holds (the first EuroLeague season it stores is 2026-27), not when the
league began publishing a stat, and three of them (`fouls_drawn`, `blk_against`, `plus_minus`) are
`null` in games imported from a workbook.

### 9.6 Refreshing

The NBA uses `GET /v1/sync` and `/v1/sync/stream` (§8). The EuroLeague has its own cursor at
`GET /v1/el/sync`, independent of the NBA's; its `syncVersion` moves when a game is written or a
status is entered. A league's `syncVersion` only means anything beside that league's own
payloads, which is why a dashboard tile of the EuroLeague is never answered "unchanged" by the
NBA's cursor.

### 9.7 What every client must do with these payloads

1. `null` renders as an em dash, never `0`.
2. Percentages are fractions.
3. `withheld` replaces indices with its `message`. `provisional` is visibly marked.
4. `rawIndex` is never shown as a ranking, and there are no rank fields to show.
5. `freshness.isDemo` shows a banner.
6. `availability: "estimated"` is labelled "estimated".
7. Every status shows the age of its `source.publishedAt`.
8. `intervalBasis: "assumed"` shows as an "assumed spread".
9. `status: "resultPending"` is not "upcoming".
10. A write needs a session with its CSRF token, or the API key.

---

## 10. Availability and provenance

A player's availability reaches Hardwood as sourced, dated entries: a row of the NBA's official
injury report, a line a person copied from a club statement or an article, a row of the user's
workbook. Three different questions are asked of that pile and they are answered separately,
because conflating them is how a product tells a small lie.

### 10.1 What is shown

**Exactly what the source said, with its age and its link.** The vocabulary is `out`, `doubtful`,
`questionable`, `probable`, `available`, lower case, or `null`. **"No report" is `status: null`**,
an em dash on screen, and it is never rendered "available", because nobody said so. A string that
is not one of the five is rejected with `400 invalid_status` (a typo never silently marks a star
healthy). `reasonCategory` is `injury`, `illness`, `rest`, `coachDecision`, `personal`,
`suspension`, `gLeague`, `notWithTeam`, `notRegistered`, `other` or null. `reasonText` is the
league report's own field or text a person typed (200 characters at most), never article text.
`expectedReturn` is parsed only from `Rounds a-b`, `Round n` and `Around D Mon`; anything else is
kept as `expectedReturnText` and left unparsed.

Every entry carries `ageMinutes`, computed from `source.publishedAt` and **never** from the fetch
time, and `isStale`. `chanceOfPlaying` is derived from the league's status table (§9.3) at read
time and never stored; it is `null` for a `null` status.

### 10.2 What the model assumes

A player with no usable entry is assumed to play with probability one. That is a modelling
convenience and not a claim, so it is stated beside every projection, as a count and a basis, in
`assumptions.assumedAvailable`, never folded into the display:

* `notOnSubmittedReport` (NBA): the player's team submitted a report and he is not on it. The
  NBA's rules oblige teams to list anyone whose participation may be affected, so his absence
  from the list carries meaning.
* `teamReportPending` (NBA): the team's report is `notYetSubmitted`.
* `noReportPublished` (NBA): no snapshot exists at all.
* `noEntry` (EuroLeague): there is no entry.

### 10.3 Which entry applies to a game, and whether it still counts

For one player and one game, in priority order: an active **override** (one a person entered, not
cleared, and not made obsolete by a newer league report arriving after it); then the newest sourced
entry **for that game**, by `source.publishedAt`; then the newest player-level entry still *in
force*; then none. An entry written for this exact game is the best evidence about it, so it is
never skipped for an older, vaguer one; if it is out of force the model ignores it.

An entry stops driving a projection (`inForce: false`, `isStale: true`, still listed) when any of
these holds:

1. **Its expected return has passed.** A parsed date once the league's day is past it; a round
   range once the last game of the last round in it has been played.
2. **The box score supersedes it.** The player has a line in a game played after the entry's
   `source.publishedAt`.
3. **It is too old.** It is more than 14 days old, unless its expected-return text says
   long-term, indefinite, season or surgery. A long-term entry stays in force but is flagged
   stale after 7 days.

`outOfForceReason` names which. Staleness for display is separate and depends on the league (§9.3):
the NBA's clock is the snapshot, the EuroLeague's is the entry.

### 10.4 How entries get in

* **NBA: automated from the official injury report.** The scheduled worker fetches the report's
  PDFs from NBA.com (never a browser), parses them and stores a snapshot per slot. The parser fails
  closed: a layout it does not recognise yields `parse_status` `headerMismatch`, no rows, and the
  state `unreadable`; it never guesses. A team's `NOT YET SUBMITTED` is stored as exactly that. A
  player whose name matches two people goes to `GET /v1/availability/review-queue` and is never
  guessed. Until the parser has read one real report successfully on the user's machine,
  `GET /v1/sources` says so. It is off when the NBA store holds the invented demo league, and the
  reason is shown.
* **EuroLeague: imported and entered by hand.** The league publishes no injury feed. A workbook
  import brings its dated, sourced rows; a person pastes the rest through `POST /v1/el/availability`
  with a source label, an optional link and a date. A headline can prefill the link, label and date
  but **a person always confirms the status**: a headline is never turned into a status
  automatically. `DELETE` appends a retraction and history is never rewritten.
* **Links to betting operators are withheld.** A source whose host belongs to one keeps its
  label and date but is stored and served with `url: null` (§2, `Source`).

### 10.5 Where every number came from: `GET {prefix}/sources`

```ts
{ key, label, kind, enabled, state, reason: string | null, lastSuccessAt, lastError,
  robotsCheckedOn, attribution }
```

`state` is one of `ok`, `stale`, `disabled`, `notConfigured`, `noReportYet`, `blocked`
(a host refused us and requests are paused), `unreadable` or `error`; `reason` is the sentence
a person can read. NBA keys: `nba.stats`, `nba.rosters`, `nba.injuryReport`, `nba.news.<feedId>`;
EuroLeague keys: `el.workbook`, `el.dataService`, `el.news.<feedId>`, `el.manual`. A headline feed
whose `robots.txt` disallows the fetch is `disabled` with that reason. A client shows this as a
"Sources and freshness" panel.

---

## 11. Things this service will not compute

Hardwood projects scores. It does not price anything. The list below is not a roadmap; each item
is excluded by a structural guard (`tests/test_no_market_machinery.py`, built on
`backend/nbastats/shared/market_guard.py` and `contracts/leagues.json`), so adding one means
removing the guard first, in a change nobody can miss.

**Never computed, stored, accepted or returned, in any league:**

* a **line**, a total to beat, an over/under, a spread used as a price, a handicap;
* a **probability** of going over a number, an **edge**, a **lean**, a **pick** against a line;
* **odds**, an implied probability, a stake, a payout, a parlay, a bookmaker's anything;
* a **probability of winning**. It is omitted from this version as a design choice. A projected
  margin, with its interval, carries the same uncertainty without a number one step from a price.
  A later version may choose differently; this one does not.

**What remains, and is analytics:** projected scores, projected margin, projected winner, the
toss-up flag, the sum of the two projected scores (`combinedPoints`, never rounded to a half point
and never called a total), the effect of absences on a projection, and the review of the model's
own misses against what happened.

**How it is enforced rather than promised:**

1. No object key of a league payload contains a forbidden word. Keys are split into camelCase
   words and matched as whole words (`overtimePeriods` is fine, `projectedLine` is not). The words
   are `contracts/leagues.json#/forbiddenPayloadKeyWords`. The check covers the league fixtures,
   the league widget fixtures and every response of every new route.
2. No route accepts a number it could compare with a projection: every query, path and body field
   name is on `contracts/leagues.json#/allowedParameters`.
3. No database column and no model-setting key could hold one. The settings are an allowlist, and
   the workbook's own `TotalSD`, `PSDBase`, `PSDSlope` and `EdgeP` have no key.
4. The workbook importer never reads a betting column (`Model line`, `Your line`, `P(over)`,
   `Lean`, `Result v line`, `Home win %`); a workbook full of them, with sentinel values, imports
   and leaves no trace.
5. No code rounds a total to the half point (`round(x * 2) / 2` and its spellings).
6. No payload field holds a probability of winning.
7. The NBA client cannot reach NBA.com's odds endpoint, and no source whose purpose is betting is
   fetched; links to betting operators are withheld (§10.4).

The reasons are in [`docs/PROJECTION.md`](../docs/PROJECTION.md) §6 and §8 and the legal posture in
[`docs/LEGAL.md`](../docs/LEGAL.md): this tool is private, personal analytics, and the fastest way
to stop being that is to start looking like something else.
