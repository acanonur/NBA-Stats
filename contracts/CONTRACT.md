# Hardwood — API & Data Contract v1

This file is the **single source of truth** shared by the iOS client (`ios/`) and the
service (`backend/`). Both sides are implemented against it, and
`scripts/check_contracts.py` fails CI when they drift apart.

Machine-readable companions, loaded by both sides at build time:

| File | Contents |
| --- | --- |
| `contracts/metrics.json` | 61 metric descriptors: name, format, direction, era availability, glossary |
| `contracts/widgets.json` | 12 widget kinds, their sizes and their configuration field schema |
| `contracts/presets.json` | 9 preset dashboards, pre-validated against the widget catalog |
| `contracts/fixtures/*.json` | Golden response payloads, decoded by both the backend tests and the iOS tests |

Regenerate the catalogs with `python3 contracts/tools/gen_metrics.py > contracts/metrics.json`
(and the `gen_widgets` / `gen_presets` equivalents). `gen_presets.py` validates every preset
widget against the widget catalog and the metric catalog, and exits non-zero on any mismatch.

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

### Pagination

Collection endpoints that can exceed 200 rows take `limit` (default 50, max 200) and an opaque
`cursor`. Responses carry `"nextCursor": string | null`. Endpoints whose rows are a widget
payload bound their `limit` to that widget's config field instead, and each states its own
range below; an out-of-range `limit` is a `bad_request`, never a silent clamp.

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

---

## 3. Endpoints

### `GET /v1/health`

```json
{ "status": "ok", "version": "1.0.0", "syncVersion": 412, "dataThrough": "2026-01-02",
  "databaseReady": true, "seededDemoData": false }
```

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
                              "values": { "pts": 32, "ts_pct": 0.641 },
                              "availability": "full" } ] } ] }
```

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
                 "explanation": "Both teams play slightly faster than league average." } ],
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
| `metric_unavailable` | 422 | The metric does not exist for the requested era or subject |
| `season_not_loaded` | 422 | The season is valid but not yet ingested |
| `rate_limited` | 429 | Client exceeded the service's own limiter; `Retry-After` is set |
| `upstream_unavailable` | 503 | The ingest source is unreachable; cached data may be stale |
| `internal_error` | 500 | Anything else; `requestId` is in the server log |

Inside `POST /v1/dashboard/resolve`, per-widget failures never change the HTTP status — the
response is `200` with `status: "error"` on the individual result.

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
60s for `scoreboard` and `daily_movers`, 300s for player-level widgets, 600s for leaderboards
and team tables, 3600s for `career_arc`.
