# Data sources and acquisition plan

This is the operational version of the source research document
("All-Time NBA Advanced-Stats App: Data Sources, Acquisition Plan & Architecture"). It says
where every number in Hardwood comes from, what it costs, and what it is legally allowed to do.

**The one-line strategy:** backfill history from bulk open datasets, keep current with a small
`nba_api` job. Never try to pull ~64,000 games one at a time from stats.nba.com — that is tens
of thousands of calls and days of runtime at safe rate limits.

---

## 1. The hybrid pipeline

```
   ┌─ ONE TIME ────────────────────────────────┐     ┌─ CONTINUOUS ──────────────────────┐
   │ Kaggle NBA Database (SQLite, 1946-)       │     │ nba_api, residential IP           │
   │ Sumitro Datta BBRef season CSVs (1947-)   │     │  · poll scoreboard during games   │
   │ shufinskiy nba_data PBP/shots (1996-)     │     │  · each game -> Final -> ingest    │
   │ hoopR release parquet (2002-)             │     │  · re-pull last 3 days nightly    │
   └───────────────┬───────────────────────────┘     └─────────────┬─────────────────────┘
                   │                                               │
                   └───────────────► serving database ◄────────────┘
                        (SQLite / Postgres — see docs/ARCHITECTURE.md)
                                        │
                          Hardwood API  ──►  iOS app + web app
```

Implemented in `backend/nbastats/ingest/`: `backfill.py` for the left arm, `daily.py` +
`runner.py` for the right arm, `aggregate.py` for everything derived.

---

## 2. Source catalogue

### A. NBA.com Stats via `nba_api` — free, unofficial, official data

`https://github.com/swar/nba_api` · MIT · Python 3.10+. This is the engine for incremental
updates. The endpoints that matter:

| Endpoint | Use |
| --- | --- |
| `LeagueGameLog` / `LeagueGameFinder` | Enumerate every `game_id` for a season and season type |
| `PlayerGameLogs` (plural) with `MeasureType='Advanced'` | **The efficient one** — one call per season returns every player's per-game advanced rows (ORtg, DRtg, NetRtg, AST%, TOV%, eFG%, TS%, USG%, Pace, PIE) |
| `BoxScoreAdvancedV3` | Per-game advanced box for a single `game_id` — used only for a just-finalized game |
| `BoxScoreTraditionalV3` | The basic box for that same game |
| `LeagueDashPlayerStats` | Season aggregates |
| `CommonAllPlayers` / `PlayerCareerStats` | Roster crosswalk and career totals |
| `PlayByPlayV3` / `ShotChartDetail` | Possession and shot granularity (optional, large) |

**Two operational facts that shape the whole deployment:**

1. **Datacenter IPs are silently blocked.** stats.nba.com sits behind Akamai bot protection and
   drops requests from AWS, GCP and Azure ranges — they hang rather than erroring. The ingest
   worker must run from a residential IP, a home box, or through a residential proxy
   (`NBA_API_PROXY`). GitHub Actions runners are usually on cloud ranges too. Only the database
   write step is safe to run in a cloud region.
2. **Browser headers are mandatory:** `User-Agent`, `Referer: https://www.nba.com/`,
   `x-nba-stats-origin: stats`, `x-nba-stats-token: true`, `Accept-Language`. `nba_api` bundles
   them; `backend/nbastats/ingest/client.py` lets you override them.

Rate limits are undocumented. Community consensus is roughly one request per 0.6–1.5s; the
client defaults to 1.0s with exponential backoff on timeouts.

### B. Basketball-Reference — free to read, **do not build on**

The richest historical source and the origin of PER, WS, BPM and VORP. But:

* 20 requests/minute, enforced, with about an hour's block for bots.
* The data-use policy explicitly says not to create websites or tools based on scraped data.
* Custom data requests start at $5,000.

Hardwood therefore **never scrapes Basketball-Reference**. Pre-1997 season advanced numbers
come from the Sumitro Datta Kaggle dump (a redistributed compilation) and are marked
`is_estimated`, with Basketball-Reference credited as the methodology source.

### C. Bulk open datasets — the backfill

| Dataset | Contents | License |
| --- | --- | --- |
| [Wyatt Walsh "NBA Database"](https://www.kaggle.com/datasets/wyattowalsh/basketball) | SQLite: 64,000+ games, 4,800+ players, box scores and play-by-play from 1946, daily-updated | CC BY-SA 4.0 (compilation) |
| [eoinamoore "Historical NBA Data"](https://www.kaggle.com/datasets/eoinamoore/historical-nba-data-and-player-box-scores) | Box scores from 1947, advanced from 1997, nightly | Kaggle terms |
| [Sumitro Datta "NBA Stats (1947-present)"](https://www.kaggle.com/datasets/sumitrodatta/nba-aba-baa-stats) | Basketball-Reference season dumps keyed on the BBRef slug — **the only practical pre-1997 advanced source** | Kaggle terms |
| [shufinskiy/nba_data](https://github.com/shufinskiy/nba_data) | Play-by-play + shot detail 1996-, downloadable files; `nba_on_court` pulls a season in seconds | Repo terms |
| [hoopR / sportsdataverse](https://github.com/sportsdataverse/hoopR-nba-data) | Release parquet, ESPN-sourced box/PBP 2002- | Repo terms |

The CC license on a compilation does **not** override the NBA's rights over the underlying
data for commercial redistribution. Fine for personal and research use; see §4.

### D. Licensed feeds — required if this ships commercially

| Provider | Shape | Cost |
| --- | --- | --- |
| **Sportradar** | Official NBA data partner; live PBP, tracking-derived metrics, an SLA | Enterprise, reported ~$10,000+/month |
| **SportsDataIO** | US-focused, DFS salaries built in | Tiered commercial |
| **API-NBA** (api-sports.io) | Free 100 req/day; Pro $15/mo; Ultra $25/mo; Mega $35/mo | Budget-friendly; verify advanced depth |
| **balldontlie** | Free 5 req/min; ALL-STAR $9.99/mo; GOAT $39.99/mo unlocks box scores and PBP | Cheap and clean, but advanced stats only from 2015 and PBP only from 2025 — **not** a pre-2015 advanced source |
| **BigDataBall** | Cleaned CSV play-by-play with all 10 on-court players, 2002-03 onward | Per season |

`backend/nbastats/ingest/` is written so the upstream client is swappable: moving to a licensed
feed means writing one new module against `normalize.py`'s column maps, not rewriting the app.

### E. Impact metrics that are not on NBA.com

EPM (Dunks & Threes), DARKO, LEBRON (BBall-Index) and Cleaning the Glass are view-only or
subscription products with no bulk export — Hardwood does not redistribute them. RAPTOR is
frozen at its final 2023-era archive after FiveThirtyEight was shut down in March 2025.
BPM 2.0 and VORP are computed from the box score using the published formulas
(`backend/nbastats/metrics.py`), not copied from anyone's table.

---

## 3. Era coverage — what can and cannot exist

| Stat | First season | Note |
| --- | --- | --- |
| Basic box (FG, FT, PTS, PF, AST) | 1946-47 | BAA |
| Total rebounds | 1950-51 | No offensive/defensive split |
| Minutes played | 1951-52 | Enables per-minute metrics |
| ORB/DRB split, steals, blocks | 1973-74 | First season BPM/VORP are computable |
| Individual turnovers | 1977-78 | Enables USG% and TOV% |
| Three-point line | 1979-80 | |
| **Per-game advanced box, +/-, play-by-play, shot charts** | **1996-97** | The hard boundary |
| Player tracking (SportVU → Hawk-Eye 2023-24) | 2013-14 | Licensing-encumbered |
| Hustle stats | 2016-17 | Box outs added ~2019-20 |

Shot-location data from 1996-97 to 1999-00 is unreliable: Sports Reference flags 194,239 field
goal attempts in those years with no shot distance or coordinates.

**Consequence for the product:** for 1946-47 → 1995-96 Hardwood shows basic box scores plus
box-derived season estimates (PER, WS, and BPM/VORP from 1973-74). True per-game advanced
ratings *cannot* exist for those years — the possession data was never recorded. The schema
allows NULL for every era-unavailable column, stamps `data_source` and `is_estimated`, and the
API returns an `availability` of `unavailable` or `estimated` rather than a zero. The iOS app
renders an em dash and explains why. This is a correctness requirement, not a polish item.

---

## 4. Legal position

*Not legal advice.* See [LEGAL.md](LEGAL.md) for the full treatment.

* Raw statistics are facts and generally not copyrightable (US: *Feist*), but compilations,
  presentation and **terms of use** are enforceable.
* **NBA.com Terms of Use** permit NBA statistics only for "legitimate news reporting or private,
  non-commercial purposes", require prominent NBA.com attribution, and forbid use with
  sponsorship or commercial identification, gambling, fantasy games, real-time play-by-play
  depiction, or a database product.
* **Sports Reference** forbids building tools or websites on scraped data.

So: **this repository in its default configuration is a private, non-commercial project.**
Running it for yourself is fine.

The trigger is not a store submission. It is **leaving the private configuration** — a
deployment people you did not invite can reach or sign up for. A store submission is one shape
of that; putting the web app on a public DNS name is another, and since the web release it is
two environment variables away. Monetizing in any form — ads, a subscription, an in-app
purchase — crosses the same line for a different reason. At any of those points you license a
feed (Sportradar for real scale, API-NBA or balldontlie GOAT for hobby scale) and stop relying
on scraped stats.nba.com data in production. [LEGAL.md](LEGAL.md) §2b draws the line properly.

Every surface carries the attribution string returned by `/v1/meta` — the iOS Settings screen,
and the footer of every page of the web app:
*"Stats via NBA.com. Not endorsed by or affiliated with the NBA."*

### One category this document does not cover

Everything above is about data Hardwood takes *in*, and what its providers allow. Since the web
release the project also collects data from its own users — email addresses, OAuth subject
identifiers, a truncated IP prefix and a user-agent string per session. That is a different
kind of obligation, running the other way, and it is written up in [LEGAL.md](LEGAL.md) §2c
along with what is kept, for how long, and the export and deletion endpoints that exist for it.

---

## 5. Volume and cost

* ~64,000+ games all-time (a rolling regular-season + playoff figure from the Kaggle DB; there
  is no single official lifetime count). Modern seasons are 1,230 regular-season games.
* ~1.3–1.7 million player-game rows all-time; the advanced subset (1996-97+) is ~35,000 games.
* Full per-game backfill via `BoxScoreAdvancedV3` would be ~35,000 calls → 12+ hours at best,
  realistically days with backoff. **This is why the backfill reads bulk files** and why
  `PlayerGameLogs` (about 30 calls for every advanced log from 1996 to today) is the incremental
  fallback.
* Storage: the full player-game box (basic + advanced) is comfortably ~1–2 GB. Play-by-play is
  tens of GB — keep it optional, and Hardwood does.

## 6. Known pitfalls

* Cloud IP blocks (the number-one practical blocker).
* Missing required headers → silent failure.
* V2 (`UPPER_SNAKE_CASE`) vs V3 (`camelCase`) schema drift — handled in `normalize.py`.
* Endpoints deprecated without notice; pin `nba_api` and watch its releases.
* Post-hoc stat corrections — hence the three-day re-pull window.
* `PlayerGameLogs` `MeasureType` coverage varies by era.

---

## 7. Sources added for the EuroLeague, injuries and headlines

Everything above is about the NBA stats store. This section covers what the EuroLeague,
availability and headline work adds, source by source: what Hardwood takes, where it goes, how
politely, and the posture it is used under. [LEGAL.md](LEGAL.md) §2d and §2e state the same
posture; this is the operational version. [RUNBOOK.md](RUNBOOK.md) §1c is how to run it.

### What was and was not verified

The terms and the live behaviour of every source in this section **could not be checked from
the development environment**: every NBA, ESPN, EuroLeague and news host was refused at the
build sandbox's network proxy, so no terms page was read and no live response was fetched. What
the code is built on instead is each source's documented shape, an MIT-licensed SDK's source
and fixtures for the EuroLeague endpoints, and the content of the EuroLeague workbook you
supplied. Three consequences, all deliberate:

* every parser fails closed: an unexpected shape becomes an explicit "unreadable" or
  "unavailable" state, never a guess;
* the endpoint shapes are marked unverified until a recording made on your Mac says otherwise
  (the probe commands, below);
* the posture below is the conservative one, not a reading of anyone's terms.

### The posture applied to all of them

Private, personal and non-commercial use on one machine; nothing redistributed; nothing used for
gambling or to operate a fantasy game; every screen carries attribution. That is the posture
[LEGAL.md](LEGAL.md) §2 already established for NBA.com data, extended to these sources.
**Sharing or publishing Hardwood changes every one of these answers**, and the worker enforces
the private half: it refuses to fetch EuroLeague data, injury reports or headlines when
`HARDWOOD_PUBLIC_BASE_URL` is not a loopback address.

There is no review date, terms URL or "outcome" to record before a source runs. Each has an
on/off switch in `hardwood.env` instead, and every source reports its state, its last success
and the reason it is off or blocked in `GET /v1/sources` (`/v1/el/sources` for the EuroLeague).

Attribution strings every client shows:

* NBA: *"Stats via NBA.com. Injury status from the NBA's official injury report."*
* EuroLeague: *"EuroLeague statistics from the EuroLeague's data service. Availability
  researched from the linked sources."*

### The sources

| # | Source | What Hardwood takes | Switch | `/v1/sources` key |
| --- | --- | --- | --- | --- |
| A | EuroLeague data service, `api-live.euroleague.net/v2` | Round calendar, fixtures, results, box scores, clubs, rosters and listed positions | `HARDWOOD_EL_LIVE` | `el.dataService` |
| B | Your EuroLeague workbook (a file you supply) | Rounds' results and box scores, fixtures, club ratings inputs, squads, sourced availability statuses | none: a file you drop in the inbox | `el.workbook` |
| C | The NBA's official injury report, `ak-static.cms.nba.com` | Per-player status, reason and team report state | `HARDWOOD_NBA_INJURIES` | `nba.injuryReport` |
| D | NBA `CommonTeamRoster` (through `nba_api`) | The position each rostered player is listed at | none | `nba.rosters` |
| E | NBA scoreboard tip-off and arena fields | Tip-off time, arena name and city, where present | none | `nba.stats` |
| F | Headline feeds (`eurohoops.net/feed`, `talkbasket.net/feed` as shipped candidates) | Title, link, date and the source's name | `HARDWOOD_NEWS` | `nba.news.<feed>`, `el.news.<feed>` |
| G | Hand-entered availability (a pasted link, label and date) | What you type | none | `el.manual` |

### A. The EuroLeague data service

`GET https://api-live.euroleague.net/v2/competitions/E/seasons/{season}/...` for five fixed
endpoints: the round calendar, a round's games (fixtures, results, game codes, partials, tip-off,
venue, audience), one game's box score with the embedded registration (position and starting
five), the season's clubs, and a club's people. **Only these URL templates can be requested; any
other URL raises before a request is made.** None has been verified against the live service.

Not used, and why: `live.euroleague.net` (Cloudflare rate-limited and carries no positions), the
v1 XML feeds (they would add a dependency), and v3 statistics (shape unknown).

How it behaves:

* at least one second between requests on every host, at most two in flight;
* a User-Agent that names Hardwood and says it is for personal use: `Hardwood/<version>
  (private single-user analytics)`;
* a 20-second timeout, up to three attempts on 429 or 5xx honouring `Retry-After` (capped at 600
  seconds) and otherwise backing off exponentially, and conditional requests whenever an ETag or
  Last-Modified is offered;
* a 401, a 403, a Cloudflare 1015 or three 429s in a row opens a **circuit breaker**: that
  source is paused for six hours and `/v1/sources` says `blocked`, with the time it will retry;
* raw responses are kept gzipped under `el-raw/` (the last three per URL) for diagnosis and are
  never served.

Expected volume is about 90 requests a week, and about 150 in a week with two rounds. A full
season backfill is deliberately a command you run on purpose
(`backfill --season E2025 --yes`), and it prints the request count before it starts.

`python -m nbastats.euroleague.ingest probe` records real responses to `recordings/` in your
data folder, so the endpoint shapes can be checked against reality. Recordings never enter git.

### B. Your workbook

The workbook is yours and its contents never enter the repository: it is imported into the
EuroLeague store on your Mac and nowhere else. The importer reads only named columns of named
sheets (Settings, Team Ratings, Squads, the round box scores, R2 Review, the round fixture
sheets, Injury Report) and reads cached cell values only, never formulas. It refuses a file
containing a document-type declaration, caps the file at 25 MB and the unpacked size at 200 MB,
and rejects any club code it does not recognise rather than guessing.

* **Not imported:** Latest Games and Game Logs (those rows come from sources whose terms nobody
  has read, and the official rows duplicate the box scores), Season Stats (derived), R3 Scorers,
  Start Here and Method. The report at the end of an import counts what was skipped.
* **The workbook's betting columns and settings are not read.** The importer carries an explicit
  list of those headers and settings and a test proves a workbook full of sentinel values in
  them leaves nothing in the store ([EUROLEAGUE.md](EUROLEAGUE.md) §7).
* **"est." lines are imported** and marked estimated everywhere they are shown: their origin is
  not recorded in the workbook, so they are labelled "your estimate; source not recorded".
* A newer workbook adds its later rounds and appends new statuses; history is never rewritten,
  and a workbook never overwrites a row that came from the data service.

### C. The NBA injury report

`https://ak-static.cms.nba.com/referee/injury/Injury-Report_<YYYY-MM-DD>_<hh>_<mm><AM|PM>.pdf`,
published in Eastern-time quarter-hour slots. It is NBA.com content, used under §2 of
[LEGAL.md](LEGAL.md): fetched by the worker only, never by a browser.

* **When:** only when a game is scheduled within 36 hours; every 15 minutes inside the reporting
  windows (the day before after 17:00 ET, and game day from 08:00 ET to the last tip-off), and
  hourly otherwise. Each run tries the newest quarter-hour slot and steps back only until the
  newest slot it already has, at most eight steps, so in practice one or two requests. A 404 is
  normal (not every slot is published) and an unchanged file is skipped.
* **Parsing:** text runs with coordinates are read from the PDF with `pypdf` (the optional
  `injuries` extra; `install.sh` installs it), columns are found from the header on every page,
  and wrapped reasons and rows split across a page are rejoined. `NOT YET SUBMITTED` is recorded
  as exactly that. **A header that does not match the seven expected names produces no
  entries and the state "unreadable"**: the layout has changed three times since 2021-22, and
  wrong data is worse than none.
* **Matching:** "Last, First" is matched against players with games for that team this season. A
  name that matches more than one player, or none, is left unmatched and goes to a review queue;
  a player is never guessed.
* **Not shown next to a demo league:** the job refuses a store that holds the seeded league.
* **Verification:** `/v1/sources` reports this parser as *not yet confirmed against a real
  report* until it has parsed one successfully on your Mac. There are no 2026-27 reports before
  about 19 October. `backend/scripts/probe_nba_injury_report.py` records real PDFs to
  `recordings/`.
* The report is not linked to the fantasy toolkit and never will be by default: that would widen
  the argument [LEGAL.md](LEGAL.md) §2a already calls thin.

### D and E. NBA rosters and the scoreboard's extra fields

`CommonTeamRoster` is called once per team (30 calls) weekly, to learn the position each player
is listed at; defence by position uses it as its one position basis. Its `POSITION` field is
unverified: if it is absent the job logs that, writes nothing, and the source shows
`unreadable`, which makes defence by position withhold itself league-wide with the reason, not
guess. Current-season positions from a Kaggle backfill are used only as a fallback. The
scoreboard's tip-off and arena fields are written only when the response actually contains them.
The NBA odds endpoint (`odds_todaysGames.json`) is not callable from the client at all, and a
test proves it.

### F. Headlines

Off the shelf, two candidate feeds are configured and **both are unverified**. For each enabled
feed Hardwood:

* reads `robots.txt` with Python's `urllib.robotparser` before fetching, caches the answer for
  24 hours, and on a disallow switches that feed off, with the reason shown in `/v1/sources`;
* fetches at most hourly, honours `Retry-After`, caps the response at 2 MB, and refuses an XML
  document with a document-type or entity declaration;
* parses RSS 2.0 or Atom and stores **a title, a link, a date and the source's name** and nothing
  else. `description` and `content:encoded` are never read into storage and **article bodies are
  never fetched**;
* keeps feed items 30 days (a link you pasted yourself is kept), and links a headline to a team
  or player only when the name is unambiguous within that league's own store.

A headline never becomes an availability status on its own: "Set from headline" pre-fills the
link, label and date and a person confirms the status.

### G. Hand-entered availability

The EuroLeague publishes no injury list through its data service, so the statuses in the
workbook were researched by a person, each with its own link and date, and new ones are entered
the same way: a status, a link, a label and the source's publication date. Rows are append-only
(a correction is a retraction plus a new row), every status shows how old its source is, and a
status stops driving projections once it is out of date. Links to betting-operator sites are
withheld and only the label and date are kept; a club whose name contains one is never affected.

### Deliberately not used

* **ESPN's unofficial API**, because of the automation clause in its terms, whatever the use.
* **Basketball-Reference**, as in §2B above, and **RotoWire, CBS, NBC and balldontlie**.
* **Scraping BasketNews**, which sits behind a paywall.
* **Article bodies** from any source.
* **The `euroleague-api` Python package** is GPLv3: its endpoint list was read as documentation,
  and none of its code is used.

### Verification records

Two checks are recorded rather than enforced, because they need your Mac's real data and
network. Hardwood runs without them; they say what has and has not been confirmed.

* **G1, the Round 3 replay:** the ten Round 3 projections recomputed from the imported inputs
  against the workbook's own cells (to nine decimals). Result recorded in
  [EUROLEAGUE.md](EUROLEAGUE.md) §9.
* **G2, the injury report parser:** `/v1/sources` shows it as unconfirmed until it has read one
  real report. Steps in [RUNBOOK.md](RUNBOOK.md) §1c.

