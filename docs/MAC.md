# Hardwood on your Mac — the native app

This is the document for the Mac client: how to build and run it, what you will see the first
time, what every screen shows today, what is deliberately missing, and what to paste back when
Xcode complains. The server it talks to is described in [RUNBOOK.md](RUNBOOK.md) §1c; the
EuroLeague method it shows is in [EUROLEAGUE.md](EUROLEAGUE.md); the Swift structure is in
[`ios/ARCHITECTURE.md`](../ios/ARCHITECTURE.md).

**One thing to know before anything else.** This app was written on a Linux machine that has no
Swift compiler and no Xcode. It has never been built. A script checks the mechanical mistakes
(`scripts/check_swift_portability.py`, §15), and it was written to a strict compile-safety rulebook
and re-read for compile errors, but the first real build, on your Mac, is where the compiler sees it
for the first time. Expect a few errors. §14 says exactly how to paste them back so they can be
fixed in one round.

---

## 1. What you are getting

* **A native macOS app** (SwiftUI, macOS 14 or later), in the same Xcode project and the same
  target as the iOS app. One target, two platforms: `Hardwood` builds for iOS 17 and macOS 14. The
  Mac gets its own shell (a sidebar, tables, an inspector, menus, a Settings window); the iOS
  app keeps its tab bar and only has to keep compiling.
* **Both leagues.** The NBA and the EuroLeague, switched with the control at the top of the
  sidebar (and in the toolbar), or ⌥⌘1 and ⌥⌘2. The screens are the same for both leagues, and
  where the NBA has no route that matches a EuroLeague one, the screen says so and uses the
  closest real route (§6).
* **A client, not a fetcher.** The app never goes to NBA.com, the EuroLeague or a news site. The
  Hardwood server on your Mac does that (RUNBOOK §1c: it runs under launchd, starts when you log
  in, and listens on `127.0.0.1` only). The app asks that server and shows the answer.
* **It computes nothing.** Every number on every screen is a field the server sent. The app
  chooses which request to make, formats the answer, and sorts by a served value when you click a
  column header. A number the server did not send is an em dash, never a zero. Percentages
  arrive as fractions and are shown as percentages.
* **No betting features.** Hardwood shows projected scores and recorded statistics. There is no
  line, no price, no probability of winning anywhere in it, and the server has nowhere to put
  one ([EUROLEAGUE.md](EUROLEAGUE.md) §7).

### What you asked for, and where it is

| You asked for | It is on |
| --- | --- |
| The EuroLeague, like your workbook | The league screens with the EuroLeague selected; §7 maps every workbook sheet to a screen |
| Both teams' latest score and points per game | **Matchup** (§6), and on the Matchups & Defence dashboard |
| How many points each team lets opponents score | **Matchup** ("Allows"), **Team Ratings** ("PA/G"), the **Defence** table ("PA/G") |
| Points allowed, broken down by the position of the opponent | **Defence by Position**, and its dashboard tile |
| Live injuries and news, with their sources | **Injuries & News** (NBA) / **Injury Report & News** (EuroLeague), **Sources & Freshness**, and the Availability Report tile |
| The NBA equivalent of all of it | The same screens with NBA selected |

---

## 2. Run it

You need a Mac with **macOS 14 or later** and **Xcode 16 or later** (Xcode 16 itself needs a
recent macOS, Sonoma 14.5 or later at the time of writing). The project file is hand-maintained
and needs nothing installed beyond Xcode.

### Step by step

1. **Install the server, once.** From the project folder in your home folder (the one `git clone` made, `NBA-Stats`):

   ```bash
   cd ~/NBA-Stats
   backend/scripts/macos/install.sh
   ```

   [RUNBOOK.md](RUNBOOK.md) §1c has everything about this step (Python 3.11 or newer, why the
   folder must not be in Documents, Desktop, Downloads or iCloud Drive, the API key the installer
   makes). Check that it is answering:

   ```bash
   curl -s http://127.0.0.1:8000/v1/health          # prints a line of JSON
   launchctl list | grep hardwood                   # four rows
   ```

   If you have the EuroLeague workbook, copy it into
   `~/Library/Application Support/Hardwood/inbox/` now; within a minute the server imports it
   (RUNBOOK §1c, "Importing your EuroLeague workbook"). Without it the EuroLeague lacks the
   workbook's starting ratings and its sourced injury statuses ([EUROLEAGUE.md](EUROLEAGUE.md) §3),
   and its screens will be thinner or empty until the live data service has supplied rounds. (A
   server started in demo mode, as `backend/scripts/serve_dev.sh --fresh` does, serves an
   **invented** EuroLeague instead, clubs ZZA to ZZT, and every EuroLeague screen says so in a
   yellow strip; that is for trying things out, not for reading results.)

2. **Open the project.**

   ```bash
   open ~/NBA-Stats/ios/NBAStats.xcodeproj
   ```

3. **Choose the destination.** In the Xcode toolbar the scheme should read **Hardwood**, and the
   run-destination menu next to it should read **My Mac**. (If it offers "My Mac (Designed for
   iPad)" or "Mac Catalyst", do not choose that one; the project turns both off, so you should
   not see them.)

4. **Run** (⌘R). The first build compiles the whole app (about 140 Swift files, more than a third
   of them the Mac's own) and takes a few minutes. When it works, Hardwood opens a window on
   **Start Here**.

If you would rather see errors without launching, press **Build** (⌘B) first.

### The first session in Xcode, in order

The project has never been built, so go in this order and paste errors from each step (§14)
before moving to the next:

| Step | Do | What it proves |
| --- | --- | --- |
| 1 | Destination **My Mac**, ⌘B | The Mac app compiles |
| 2 | ⌘R | It launches and reaches the server |
| 3 | Destination **Any iOS Simulator Device**, ⌘B | The iOS app still compiles (the shared files were edited) |
| 4 | Destination **My Mac**, ⌘U | The unit tests pass on the Mac |

### Keeping the app

⌘R runs a Debug build from Xcode's own build folder. To keep a copy: **Product ▸ Show Build
Folder in Finder**, open `Products/Debug`, and drag `Hardwood.app` into Applications. It is signed
only to run on the Mac that built it, which is all this app is for (§3). Building again in Xcode
does not update the copy in Applications.

---

### A disk image instead of Xcode

Once the server is installed (step 1), you never have to open Xcode to get the app. In Terminal:

```bash
cd ~/NBA-Stats
scripts/make_dmg.sh --open
```

It builds the app and opens `dist/Hardwood.dmg`: drag **Hardwood** onto **Applications** and open
it from there. A disk image built on your own Mac opens with a plain double-click.

Or skip building altogether: every push that touches the app runs the **Mac app** workflow on
GitHub, and its run page has a **Hardwood-mac** download under *Artifacts* (GitHub zips it; unzip
to get `Hardwood.dmg`). A downloaded copy is quarantined, so the first time macOS says it cannot
verify the developer: click **Done**, then **System Settings ▸ Privacy & Security ▸ Open Anyway**
(or run `xattr -dr com.apple.quarantine /Applications/Hardwood.app`). The app is signed to run
locally, not by a registered Apple developer; removing that step would take a paid Developer ID and
notarization, which only matters if the app were shared — see [LEGAL.md](LEGAL.md) before doing
that.

The disk image holds the app only. The server still comes from `install.sh`; the image's
*Read Me First* says so.

## 3. If Xcode asks about signing

The project signs Mac builds "to run locally": no Apple developer account, no team
(`CODE_SIGN_IDENTITY[sdk=macosx*] = "-"`). Most of the time Xcode accepts that and you will not
see anything. If it still stops with **"Signing for 'Hardwood' requires a development team"**:

1. Click the blue **Hardwood** project at the top of the left sidebar, choose the **Hardwood**
   target (not HardwoodTests), and open the **Signing & Capabilities** tab.
2. Under **Team**, choose your name, shown as "(Personal Team)". If the list is empty or only
   says "None", choose **Add an Account…**, sign in with your Apple ID (free; no paid programme is
   needed), and then choose the Personal Team that appears.
3. If a **Signing Certificate** menu is shown, choose **Sign to Run Locally**. If it is not
   offered while "Automatically manage signing" is ticked, untick that box, choose Sign to Run
   Locally, and run again.
4. Press **Run** again.

Two things that can happen next, and what to do:

* **"Failed to register bundle identifier"** or "The app identifier cannot be registered":
  someone else's account already uses `com.hardwood.nbastats`. In the same tab change **Bundle
  Identifier** to something with your name in it, for example `com.yourname.hardwood`. Consequence:
  macOS treats it as a new app, so the app's preferences (selected screen, favourites, a key
  typed into Settings) start empty. The dashboards folder is named by a constant, not by the
  bundle identifier, so your dashboards are unaffected (§12).
* **A Personal Team build stops launching after about seven days.** That is the free account's
  rule. Run from Xcode again; nothing is lost.

**Do not turn on App Sandbox or Hardened Runtime**, and do not add an entitlements file. The app
has to read `~/Library/Application Support/Hardwood/hardwood.env` (where the installer keeps the
server's API key), talk plain HTTP to `127.0.0.1`, and open `~/Library/Logs/Hardwood`. A sandbox
blocks the first and last of those, and it also moves where the app keeps its files.

I could not run Xcode where this was written, so the wording of its menus may differ a little
from the steps above. If a step does not match what you see, paste what you see (§14).

---

## 4. First launch: live data, demo data, or no server

The Mac build **starts on live data**, pointed at `http://127.0.0.1:8000/v1` (its
`Info-macOS.plist`; the iOS build starts in demo mode instead). It uses `127.0.0.1` rather than
`localhost` because the server listens on IPv4 loopback only.

### With the server running

On launch, in order, the app:

1. **Reads the server's API key** from `~/Library/Application Support/Hardwood/hardwood.env`, but
   only when no key is stored yet. It uses the first `HARDWOOD_API_KEY=` line exactly as the server
   does, never shows the key and never writes the file. A key you typed into Settings is never
   overwritten. (Why a key at all: with one set, the server asks for it on every read except
   `/v1/health`, and it is the only credential a change from the app is accepted with. RUNBOOK §1c,
   "The API key".)
2. Refreshes the widget catalog from the server and asks what changed since last time (the
   dashboard's own sync check).
3. Asks `/v1/health` whether anything is answering.
4. Asks `/v1/leagues` for each league's state, and learns each league's route prefix from it; then
   the EuroLeague's `/v1/el/meta` (rounds and clubs) and the NBA's team list.
5. **Makes your first Mac dashboard,** *Matchups & Defence*, once (§10).
6. Starts checking the server **once a minute**, and again at once when the Mac wakes and when you
   switch back to the app. When a league's cursor on `/v1/leagues` moves, every open screen of
   that league reloads. ⌘R reloads everything now.

You land on **Start Here**, in the **EuroLeague** the first time (it is in season; the NBA's
regular season has not started). The app remembers the league and the screen you leave it on.

Start Here opens with a **Welcome** card: choose your NBA team and your EuroLeague club, then
**Save** (or **Skip**). The NBA team is the one the NBA matchup tile follows and the Matchup and
Team View screens open on; the club is the one the EuroLeague matchup tile follows and the
Matchup, Team View and Box Scores screens open on. You can change both later: the team in
Settings ▸ General ▸ Favourites, the club in Settings ▸ Leagues.

### With the server not running

The app does not fail; it says what it found. A strip appears above the screen:

| What the strip says | What it means | What it offers |
| --- | --- | --- |
| **Hardwood's server isn't answering at http://127.0.0.1:8000** | Nothing is listening: the agent is stopped or crashed | **Try Again**; **Use Demo Data**; **Copy Restart Command** (puts `launchctl kickstart -k gui/$(id -u)/com.hardwood.api` on the clipboard for Terminal); **Open Logs Folder** (`api.log` is the one to read) |
| A key icon: the server **refused the API key** | The key the app holds is not the one in `hardwood.env` (rotated, or the file changed) | **Read Key from hardwood.env**; **Try Again** |
| *Showing bundled demo data, not your server's.* (yellow) | Demo mode is on | **Use My Server** |

Nothing is shown at all while the server is fine. The strip uses the server's own sentence as its
second line and never replaces it with a guess.

### Demo data

Demo mode serves the golden fixtures bundled in the app, so every screen works with no server and
no network. Turn it on from the banner (**Use Demo Data**) or **Settings ▸ General ▸ Use bundled
demo data**; turn it off from the banner (**Use My Server**) or the same toggle. Switching clears
what was loaded from the other source, so the two can never be mixed on screen.

What demo mode is and is not:

* It is **invented** data in the real shapes: the clubs are ZZA to ZZT, the players are made up,
  and the dates are made up too. It is for seeing what a screen looks like, not for reading
  results. The strip says "Bundled demo data".
* League screens read bundled copies of the league routes (`league_nba_*` and `league_el_*`
  fixtures, copied in by `scripts/sync_contracts.sh`). The four league dashboard tiles read the
  bundled `league_el_widget_<kind>` fixture when a tile is set to the EuroLeague, and **never** the
  NBA fixture of the same kind: a tile that cannot find its fixture says *Demo mode has no bundled
  EuroLeague data for this widget* rather than put NBA numbers under a EuroLeague title.
* A screen with no bundled fixture says *Demo data for this screen isn't bundled yet* and offers
  **Use My Server**. It is a state, not an error.
* Demo mode is read-only. Recording a status or pasting a link says "Demo data is read-only. Turn
  off demo data in Settings to save to your server."

A server that holds the invented league (one started in demo mode, as `serve_dev.sh --fresh`
does) is a different thing: that is *live* data from your server, and the screens show
**Invented demo league — not real games** in a yellow strip so it can never be mistaken for real
results. An installed server does not do this by default: its EuroLeague store is created for real
data (your workbook, or live ingest) and keeps that kind.

---

## 5. The window, the menus and the keys

### The window

* **Sidebar.** The league switch at the top; your **Dashboards**; then the screens in five groups:
  **Game Day** (Start Here, Round or Slate, Matchup, Defence by Position, Injuries & News),
  **Players** (Round Scorers or Projected Scorers, Season Stats, Player Search on the NBA only),
  **Results** (Box Scores, Review), **Teams** (Team View, Team Ratings), **Reference** (Method,
  Sources & Freshness). The two leagues name some rows differently ("Round" and "Slate");
  changing the league renames them.
* **Toolbar.** The league switch (hidden on a dashboard, where each tile picks its own league),
  **Refresh**, and on a table screen **Copy Table**, an inspector toggle, and any action the
  screen has (Record Status…, Paste Link…).
* **Filter bar.** The pickers for the screen (club, round, date, column set, minimum games, a
  name filter) are in a row at the top of the screen's own content, not in the toolbar.
* **Tables.** Every column sorts when you click its header (by the value, not the text). Rows
  alternate shading. Right-click a row for **Show Details** and **Copy Row**, and, where they make
  sense for that table, **Open Matchup**, **Open Defence** and **Open in New Window** (games and
  clubs). Return or a double-click is Show Details, which opens the **inspector**. A table shows at
  most ten columns, so some screens have a column-set choice (Box / Shooting, Squad / Season /
  Per 40 / Projected).
* **Copy Table** puts every row on the clipboard as tab-separated text that pastes into Excel or
  Numbers as cells. It copies what the table shows, em dashes included, and on Season Stats it
  copies every column, not only the ten on screen. This is how to get the workbook's side-by-side
  view back.
* **Inspector** (⌃⌘I to show or hide): the detail of the selected row. Start Here, Method and
  Sources & Freshness have none; Games & Box Scores shows the box score beside the list instead;
  Team View's player card is its own inspector, opened by selecting a player (it works the same in
  the Club window, which has no sidebar).
* **Windows.** Double-clicking a game opens its **Box Score** window, and a club opens a **Club**
  window; both are read-only and you can have several. Closing the main window leaves the app
  running; **Window ▸ Hardwood** brings it back.
* **Subtitle.** Under each title: league and "data through" day, in the league's own calendar.

### Settings (⌘,)

| Tab | What is there |
| --- | --- |
| **General** | Use bundled demo data; the server address and API key with **Test Connection**; favourites; refresh notes; storage; what the record covers |
| **Leagues** | The league the app opens on; your favourite EuroLeague club; **Read API key from hardwood.env** (says only "Set" or "Not set"; never prints the key); **Copy Restart Command**, **Open Logs Folder**, **Open Data Folder** |
| **Model** | A read-only table of the model's constants for either league: setting, value, where it came from (Default, Fitted from results, Workbook assumption not checked, Set by you), and whether it is the default |
| **About** | Version, who the numbers come from, the personal-use posture, and "Hardwood shows projected scores and recorded statistics. It has no betting features." |

### Keys

| Keys | Does |
| --- | --- |
| ⌘0 | The dashboard you were on |
| ⌘1 to ⌘9 | Start Here, Round/Slate, Matchup, Defence, Injuries & News, Scorers, Season Stats, Games & Box Scores, Team View |
| (no key) | Review, Team Ratings, Method, Sources & Freshness, Player Search: use the sidebar or the **Go** menu |
| ⌥⌘1 / ⌥⌘2 | NBA / EuroLeague |
| ⌘[ / ⌘] | Previous / next round (EuroLeague) or day (NBA), on the screens that step |
| ⌘R | Refresh everything now |
| ⌃⌘I / ⌃⌘S | Show or hide the inspector / the sidebar |
| ⌘N | New dashboard |
| ⇧⌘E | Edit the dashboard (it goes to your dashboard first); again to finish |
| ⌥⌘Z | **Dashboard ▸ Revert Last Change.** One level, a revert and not an undo stack; ⌘Z stays the ordinary text undo |
| ⇧⌘R | Record Availability Status… (goes to Injuries & News first) |
| ⇧⌘L | Paste Headline Link… |
| Delete | On a dashboard row in the sidebar: asks, then deletes |
| ⌘, | Settings |

**Help ▸ Open Server Logs** and **Help ▸ Copy Server Restart Command** are the same helpers the
banner offers.

---

## 6. What each screen shows today

Titles are given as NBA / EuroLeague where they differ. "Route" is the server route behind it
(`{prefix}` is `/v1` for the NBA and `/v1/el` for the EuroLeague). The numbers of columns are what
is on screen now.

### Start Here (⌘1)
What is loaded, how fresh it is, what is invented, and where to go. A **League status** card per
league (state, the server's reason when it is not simply ready, a badge when the games are
invented, season, data through, attribution); the EuroLeague's own **day-one notice**, verbatim; a
**Rounds** strip with one chip per round ("R3 · RS · Result pending", first tip-off in your time)
that opens that round; the **next NBA slate**; EuroLeague club codes the server could not verify,
when there are any; what the data covers; four quick links. A league card that failed to load
shows its own error line and the other card still draws. Routes: `/v1/leagues`, `/v1/el/meta`,
the NBA meta and `/v1/projections?date=next`.

### Slate / Round (⌘2)
Every game of a day (NBA) or round (EuroLeague) with its projected score. **Ten columns:** Tip (your
time; the EuroLeague hover adds Berlin time), Home, Away, Proj home, Proj away, Margin (home minus
away), Projected winner ("CZV by 2.4", or "Toss-up"), Combined pts, Injury effect, Status (tip time,
"Result pending", the final score, "Postponed"). The EuroLeague has a **round picker** with previous
and next arrows (⌘[ ⌘]) and a summary strip (games, toss-ups, closest game, average combined points,
projected winners at home and away: counts and averages, not likelihoods); the NBA has a **date
field** with previous and next arrows and **Next slate**. The **inspector** is the whole projection
for the selected game: both sides, the 80% range (or "Range not yet calibrated"), full-strength
points, the effect of absences, who the model assumed was available and on what basis, the model's
constants with where each came from, the result once final, the history of earlier runs, and buttons
for **Open Matchup** and **Open Box Score**. A projection made after tip-off is captioned
**"Computed after tip-off — a reconstruction, not a prediction"**; the app does no timestamp
arithmetic for that, the server says so. Routes: `/v1/el/rounds/{n}` and `/v1/projections?date=`,
then `{prefix}/games/{id}/projection`.

### Matchup (⌘3) — your first question
Two teams side by side: what each **scored last**, what each **averages**, and what each **lets
opponents score**. Three ways in: **Next game** (a team's next scheduled game; with none you get
"No scheduled game" and **Choose two teams**), **Two teams** (any pair, plus **Show last season**
for the NBA), or **Open Matchup** from a game on the Slate/Round screen (every input cut off
before that game started). Filter bar: mode, the team or two teams, a **Last** stepper (3 to 15
games, default 5), the NBA season, and a **Per 48 min / Per 40 min** toggle (it scales each game to
regulation length, so overtime does not inflate points). For each side (home left, away right):

* the **latest score** with its opponent, the venue marker (H, A, or N for a neutral court), the
  date, and "OT" or "2OT";
* **Scores** (points per game) and **Allows** (points opponents score per game), each with its
  game count and a tick for the league average, plus the **margin** per game;
* a **chart** of scored against allowed over the last games (a game with no score is left out,
  never drawn as zero) and the list of those games;
* the **last-N and last-10** averages ("only 3 games" when there are fewer than asked for);
* **home / away / neutral** splits;
* the **opponent-adjusted** sentences ("Allows 2.1 fewer than these opponents usually score (7
  games)", or "Not enough qualifying games yet (has 3)" when the server could not produce the
  figure);
* who is **missing** (Out / Doubtful / Questionable / Probable counts and the top three absences,
  each with its source);
* its **defence by position** in miniature, with **Open Defence**.

The inspector holds the projection for the game, or "No projection for this pairing". A team with
no games this season shows "No games yet this season" instead of numbers. Routes:
`{prefix}/teams/{id}/matchup`, `{prefix}/matchups`, `{prefix}/games/{id}/matchup`.

### Defence by Position (⌘4) — your second question
How many points a team allows to opponents **listed at each position**, against the league.
**All teams** (default) is the league table in the server's order (points allowed per game, lowest
first); choose a team to see its own breakdown. Filter bar: team scope, **window** (Season, Last 5,
10, 15), **basis** (per game, or per 48/40 minutes), the EuroLeague's **positions** (G · F · C, or
"5 positions (estimated)"), and the NBA season. Tables: three positions are **eight columns**
(Team, GP, PA/G, G, F, C, Unassigned, Status); five positions are **ten** (Team, GP, PA/G, PG, SG,
SF, PF, C, Unassigned, Status). A position cell shows points allowed over the difference from the
league, coloured by the server's band only (better, typical, worse). There is **no rank column and
no ordering by a score** anywhere; a header click re-sorts by a served value. The inspector (and
the one-team view) shows a bar per position against the league tick, the shares, a checksum line
("Positions add up to 83.2; points allowed per game is 83.2.", plus "n games excluded: their
position split did not add up to the final score" when some were), a coverage bar, and a
collapsed **Method details**.

It is honest about thin evidence, and every word of it comes from the payload:

| The server says | You see |
| --- | --- |
| Fewer games than the league's minimum (6) | **Withheld**, the server's message, the raw points per position dimmed, no verdict at all |
| Fewer than the comfortable number (12) | **Provisional**: no bands |
| Too few players with a listed position, or a thin league sample | Withheld, with that message |
| No position differs from the league by more than chance | "No team's points allowed at this position differ from the league by more than chance this season." |
| The five-position view | "Estimated", and that the labels come from your workbook's listings |

The caveat is always on screen: these are points scored by players **listed** at a position, not
by whoever guarded them. The NBA lists players as guards, forwards or centres, so three positions
are shown. Routes: `{prefix}/defense-by-position` and `{prefix}/teams/{id}/defense-by-position`.

### Injuries & News / Injury Report & News (⌘5)
"Latest information from internet sources." Three tabs: **Injuries**, **Headlines**, **Needs
review**.

* **Injuries** (eight columns): Club, Player, Status, In model, Problem, Expected return, Source,
  Reported. Every row shows **who said it** (a kind chip: League report, Club statement, Press,
  Box score, Your workbook, Entered by you; the label as a link when there is one) and **how long
  ago they said it** (the age of the source's date, never of Hardwood's fetch; the exact time is
  in the hover). Chips mark **Stale**, **Superseded** (dimmed, still listed) and **Override**. A
  player nobody has reported on is **"No report"**, never "Available". The server's own state for
  the report (stale, no report yet, unreadable, switched off) is a banner with its message. Filters:
  club, status, show superseded, a name. Re-read every five minutes while the screen is open, and
  on ⌘R. The inspector has every field and a **Retract** button for a status a person typed.
* **Headlines**: title (a link), outlet, age, teams. Only title, link, date and outlet are ever
  shown; the app never opens an article. **Record status from this headline** pre-fills the sheet.
* **Needs review**: report rows whose player matched nobody (a name is never guessed); each has
  **Record status** to resolve it by choosing the player.

Routes: `{prefix}/availability`, `{prefix}/news`, `{prefix}/availability/review-queue`. The
sheets are §11.

### Projected Scorers / Round Scorers (⌘6)
* **EuroLeague, Round Scorers** (nine columns): Club, Player, Game, Status, Chance, Model pts,
  Form avg, Form games, Projection. Round picker, a **Players per club** stepper (1 to 5, default
  2 as in the workbook), and a club filter. The inspector shows the player's recent points, newest
  first, "DNP" where he did not play, and Open Matchup. Route: `/v1/el/rounds/{n}/scorers`.
* **NBA, Projected Scorers, next game** (nine columns): Player, Team, Matchup, Projection, Range,
  Season avg, Change vs season, Proj min, Status. The NBA has no round and no scorers route, so
  this asks the dashboard resolver once, exactly as a dashboard tile would, for the projection
  board (League or Favourites). The table opens in the server's order.

### Season Stats (⌘7)
* **EuroLeague** (up to 500 rows in one answer): per game, totals or per 40. **Box** (10 columns:
  Club, Player, GP, MIN, PTS, REB, AST, STL, BLK, TOV) or **Shooting** (8: Club, Player, GP, 3PM,
  2P%, 3P%, FT%, PIR). Club, minimum-games stepper and a name filter. Hovering a number says how
  many games carried that statistic. The inspector is the player card with the player's **game
  log** (date, opponent, result, minutes, points, rebounds, assists, PIR) and a points bar chart;
  a game he did not play is "DNP" with no bar. Route: `/v1/el/stats/players`, then
  `/v1/el/players/{personCode}/gamelog`.
* **NBA**: the server has no table route, so the screen builds the same table from the thirty
  teams' rosters (`/v1/teams/{id}?rosterMetrics=…`), four requests at a time, rows appearing as
  each lands, with progress shown. A team whose request fails is left out and named, never filled
  with zeros. **Box** (10) and **Shooting** (9: Player, Team, GP, FG%, 3P%, FT%, TS%, USG%, Net
  rtg). The inspector has **Open player**, the existing player screen, in a sheet.

### Player Search (NBA only)
The existing search screen, with its own navigation inside the detail column. The EuroLeague has
no player search; asking for it lands on Season Stats.

### Games & Box Scores / Box Scores & Latest Games (⌘8)
A game list on the left, the selected game's box score on the right (a splitter between them).
Double-click a game for its own window.
* **EuroLeague:** **By round** (every game of a round, played or not: the workbook's round sheets)
  or **By club** (that club's games: Latest Games), seven columns: Date, Round, Home, Away, Score,
  Venue, Status. By club also shows the club's record under one sentence that is always there: it
  counts **EuroLeague games only**, so it differs from a record that also counts friendlies,
  domestic and national-team games.
* **NBA:** one day's scoreboard (date field with previous and next arrows, and **Latest**).
* **The box score:** the final score and how it was reached (quarters, overtime), attendance and
  venue when served; a team switch; **Box** (10 columns, EuroLeague: Player, MIN, PTS, REB, AST,
  STL, TOV, BLK, PF, PIR; NBA: Player, MIN, PTS, REB, AST, STL, BLK, TOV, +/-, Game score) and
  **Shooting** (10). A player who did not play shows "DNP" and em dashes. Team totals sit under
  the table and are never sorted. A game whose box failed the server's own checks shows its score
  and no lines, with the server's sentence. A game not yet played has no lines, and the view says
  so. Routes: `/v1/el/games`, `/v1/el/games/{id}`; `/v1/games`, `/v1/games/{id}/box`.

### Slate Review / Round Review
What the model said before the games against what happened. Cards for each model: winners called
("6 of 8, +2 toss-ups"), the mean miss in margin, in each team's score, and in combined points;
rows rebuilt after the fact are counted **apart**, in their own card, never blended in. Ten
columns: Date, Home, Away, Proj home, Proj away, Result, Margin miss, Combined miss, Winner, Basis
(Locked before tip-off, Imported from the workbook, Reconstructed after the fact, or Latest).
EuroLeague by round (or all rounds), NBA by day (or the whole season). The inspector has the
projection frozen at tip-off and its history, partials, venue and attendance, and (EuroLeague) the
rating changes the round caused. A game with no projection recorded before tip-off is simply not in
the table. Route: `{prefix}/projections/review`.

### Team View / Team View & Squads (⌘9)
Choose a team (alphabetical: an order for finding, not a ranking; it opens on your favourite, else
the first). A header with record, **Scores / Allows / Margin**, the next game, who is missing and
the defence in miniature, then the players.
* **EuroLeague** (`/v1/el/teams/{clubCode}`): four column sets, ten columns each. **Squad**
  (Player, Pos, Age, Role, Basis, Proj MIN, PTS/40, REB/40, AST/40, Status), **Season** (Player,
  GP, MIN, PTS, REB, AST, PIR, 2P%, 3P%, FT%), **Per 40** and **Projected** (Player, MIN, PTS, REB,
  AST, 3PM, STL, BLK, TOV, PIR; offered only when the server sends the squad's projected line).
  The **Basis** chip says where a player's rates came from: "Official (workbook)", "Estimate
  (workbook)" (a figure the workbook's author estimated and did not source), "Position average",
  "Official update", or "Invented demo". Position is the workbook's five-position label when there
  is one. A player with no reported status shows an em dash, with one sentence
  saying the workbook marked unlisted players AVAILABLE and Hardwood shows a status only when a
  source reported one. The rating card says when the club's prior is an estimate.
* **NBA** (`/v1/teams/{id}` with roster metrics, the team's matchup for the scoring header, and
  the availability report for statuses): Player, Pos, GP, MIN, PTS, REB, AST, TS%, USG%, Net rtg.

**Open in New Window** makes a Club window.

### Team Ratings
How good each team is by the numbers the server holds, in one sortable table.
* **EuroLeague** (seven columns): Club, Record, PPG, PA/G, Attack idx, Defence idx, As of round.
  "Defence index: higher means more points allowed", so a higher number is a *worse* defence and
  sorting descending does not rank the best first. The inspector has the club's full rating
  (priors, adjustments, projected points, the games that moved it). Routes: `/v1/el/teams`,
  `/v1/el/ratings`.
* **NBA, Team Ratings, efficiency** (seven columns): Team, W–L, ORtg, DRtg, Net, Pace, PA/G. Built
  from the efficiency widget plus points allowed from the defence table.

### Method
How the numbers are made and every constant behind them. **About the model** (prose) and
**Constants** (key, value, provenance, default, description; read-only). For the EuroLeague the
prose comes from the server (`/v1/el/method`: where Hardwood differs from your workbook, and the
known limits); for the NBA, which has no such route, it is text bundled in the app and needs no
network. It says plainly that the workbook's columns that compared a projection with an outside
number are not reproduced.

### Sources & Freshness
Where every number comes from and whether that source is working. Eight columns: Source, Kind,
State, Reason, Last success, Last error, robots.txt checked, Enabled. A state this build does not
know is shown as the server wrote it, in a neutral chip. The server's own notice about what to
expect on day one, the attribution lines, each reason and last error are printed verbatim. Problems
sort first, because that is the useful order for finding what is broken; nothing is ranked.
Route: `{prefix}/sources`.

---

## 7. Your workbook, sheet by sheet

| Workbook sheet | Screen (EuroLeague / NBA twin) |
| --- | --- |
| Start Here | **Start Here** |
| Settings | **Settings ▸ Model** and **Method ▸ Constants** (read-only) |
| Round 3 | **Round** / **Slate** |
| R3 Scorers | **Round Scorers** / **Projected Scorers** |
| Injury Report | **Injury Report & News** / **Injuries & News** |
| R2 Review | **Round Review** / **Slate Review** |
| Season Stats | **Season Stats** (NBA: built from the thirty rosters) |
| R1 and R2 Box Scores, Latest Games | **Box Scores & Latest Games** / **Games & Box Scores** |
| Game Logs | The **game log in the Season Stats inspector**, and the **Season** column set of Team View |
| Squads, Team View | **Team View & Squads** / **Team View** |
| Team Ratings | **Team Ratings** |
| Method | **Method** |
| *(new)* | **Matchup**, **Defence by Position**, **Sources & Freshness** |

---

## 8. Day one — said plainly

What the screens honestly look like in the first weeks of October 2026. Every empty state below is
explained on screen by the server's own words; none is a bug. The EuroLeague column assumes your
workbook has been imported ([RUNBOOK.md](RUNBOOK.md) §1c); without it that side is thinner.

| Screen | NBA | EuroLeague |
| --- | --- | --- |
| **Matchup** | The 2026-27 regular season has not started: every season number is an em dash with "No games yet this season". **Show last season** works only if last season is in your server's store, and a new install is empty until you load one (RUNBOOK §2b). | Rounds 1 and 2 are final: latest scores, averages and "Allows" are real. Round 3 was played 1–2 October and shows "Result pending" until results load. |
| **Defence** | Withheld ("Only n games, too few to judge a defence") for the new season. | Withheld for every club while it has only a few games (it needs six, and a table is provisional until twelve). The message is shown and the raw points per position are dimmed. |
| **Round / Slate** | The next date with scheduled games the server has seen (the watcher records each day's scoreboard as it polls), or "No projected games on this date". | Round 3 is "Played; results not loaded yet", and its projections are captioned "Computed after tip-off". |
| **Injuries** | "No report yet" until the NBA's first reports, about **19 October**. The report reader has not yet been confirmed against a real report (RUNBOOK §1c); if the layout is not recognised the state is "unreadable", never a wrong status. Or "disabled" if the NBA store is the invented league. | The 56 sourced statuses in your workbook, dated 28–30 September, many shown **Superseded** or **Stale** once Round 3 loads. Statuses are research with a link and a date, not a live feed. |
| **Headlines** | From the feeds the server has enabled. "No NBA headline feed is configured on your server yet" if there are none (for example `HARDWOOD_NEWS=off`, or every feed's robots.txt refuses). | The same, for the EuroLeague feeds. |
| **Review** | Hardwood's model only, once games are played. | Only the workbook's imported Round 2 projections. |
| **Season Stats / Team Ratings** | Fill in as games are ingested. | Real, from the imported rounds. |

To have real NBA numbers now, load a past season (RUNBOOK §2b: `--nightly --days 200 --date
2026-04-15`, then `--refetch-games` over the same range for who started). The app shows whatever
the server's store holds and nothing else.

**Without the workbook,** the EuroLeague has no starting ratings and no sourced injury statuses
([EUROLEAGUE.md](EUROLEAGUE.md) §3), so projections and the Injury Report stay thin or empty until
live data supplies what it can. **If the server is in demo mode** (`serve_dev.sh --fresh`, or
`HARDWOOD_DEMO_MODE=1`), both leagues are invented and every screen carries the yellow
"Invented demo league — not real games" strip. An installed server does not do that unless you
point it at a demo store.

---

## 9. Not shown, and why

### Left out on purpose

The workbook has columns and settings that compare a projection with an outside number: a typed
number to beat, a probability against it, a lean, an edge, a result against it, a home-win
percentage, a "why picked" column, and the settings that tune them. None is imported, stored,
served or shown, and the server has no field or route parameter that could carry one
([EUROLEAGUE.md](EUROLEAGUE.md) §6 and §7). The Method screen says so in one sentence. There is no
win probability in this version; the margin and its 80% range carry the same uncertainty.

### Different from the workbook, by design

* **EuroLeague games only.** Friendlies, domestic and national-team games are not tracked, so a
  club's record here can differ from the workbook's. Box Scores says so under a club's record.
* **Positions.** The EuroLeague registers Guard, Forward and Center. The workbook's five are an
  opt-in, always labelled estimated. The NBA lists three.
* **Times** are your Mac's local time, with Berlin time in the hover for the EuroLeague. The
  workbook's separate CEST and TR columns are not reproduced.
* **A status is shown only when a source reported one.** The workbook counted unlisted players as
  available.

### The server has it; the app does not show it yet

* **EuroLeague player details** (country, birth date, height, weight, registration):
  `/v1/el/players/{personCode}` is served, and no screen reads it.
* **Editing the model's constants.** The server accepts a change to a few allowed constants
  (`PATCH {prefix}/model-settings`), but Settings ▸ Model is read-only until that has a design of
  its own (which constants, with what limits, how to put one back).
* **Projected PIR.** The squad's projected line is shown, but its PIR column is an em dash: the
  server does not have the inputs to project it.

### The server does not have it

* The workbook's previous-season W–L for each club.
* Season Stats' round-1 and round-2 points, and its combined field-goal percentage, as columns.
* Latest Games' friendlies, domestic games and leading-scorer columns (the games list carries no
  leaders; open the box score).

---

## 10. Dashboards and the four league widgets

The dashboards are the existing editable grid, now in the sidebar. A new install has **Daily
Recap** (seeded as on iOS) and, from the first Mac launch, **Matchups & Defence**: seven tiles,
made once and then yours to rename, rearrange, reconfigure or delete. If you delete it, it does
not come back.

| Tile | League | Size | Notes |
| --- | --- | --- | --- |
| Matchup | NBA | large | Follows your favourite NBA team; with none set the tile shows the server's message, and **Configure…** in the tile's menu lets you choose one |
| Defence by Position | NBA | large | Every team (the league table) |
| Availability Report | NBA | medium | |
| Availability Report | EuroLeague | medium | With headlines |
| Slate Projections | EuroLeague | large | The next round (the tile's round setting is 0, which means "next") |
| Defence by Position | EuroLeague | large | Three positions, every club |
| Matchup | EuroLeague | large | Your favourite club, once you choose one (Start Here or Settings ▸ Leagues); until then the tile says "Choose a club for this tile." |

The four new kinds are in the widget catalog like any other: **Matchup**, **Defence by Position**,
**Availability Report**, **Slate Projections**. Each has a **League** setting (NBA or EuroLeague)
and shows only the other settings that apply to that league (a team for the NBA, a club for the
EuroLeague, a date for the NBA, a round for the EuroLeague). A tile and the screen it summarises
draw the same numbers the same way, from the same payload. Tiles are marked estimated where they
carry modelled or workbook-estimated numbers. When there is nothing yet (no games this season, no
injury report published, a feed switched off) a league tile draws its own empty state with the
server's message, never the "not tracked in this era" badge the older stat tiles use. A Defence
tile set to **Per minute** shows points per 48 (NBA) or 40 (EuroLeague) opponent minutes, with the
league's rate beneath, because that is the unit its colours were judged in.

Editing: **⇧⌘E** (or the dashboard's Edit button), then **Add Widget**, the button that appears
while you are editing. Right-click a tile, or use its ⋯ menu, for **Configure…**, **Resize**,
**Duplicate** and **Remove**; drag the handle to reorder. The grid is two columns below 760 points
of width and four above. Tiles refresh with ⌘R, every minute while
the app is open, and when the Mac wakes; the server keeps collecting through launchd while the
app is closed, which is why a closed app needs nothing like iOS background refresh.

---

## 11. Changing things: record a status, paste a link, retract

Two sheets, both on the Injuries & News screen (⇧⌘R, ⇧⌘L, or the toolbar buttons).

* **Record Status…** records an availability status a person found. EuroLeague form: club,
  player (from the club's squad when it is loaded), status (you always choose it: out, doubtful,
  questionable, probable, available), reason category and text, expected return, a source link,
  a **source name (required)** and a **source date (required)**. Copy on the sheet: *Saved on your
  server and used by projections. It is never edited; retract it to undo.* Statuses are append-only;
  **Retract** (in the inspector, for a status a person typed) appends a retraction after asking.
* **The NBA form is shorter**, on purpose. The server takes a player, a status, an optional team,
  a note, and optionally a link and its date, and it refuses any other field outright. The sheet
  shows only what it can send, so it never has a label it would have to throw away. An NBA entry
  is an override: retracting it clears the override and the row stays in the history.
* **Paste Link…** saves a headline: title, link, published date, outlet, and the teams it is about
  (chosen from the league's team list). It is stored exactly like a headline from a feed. The app
  does not open the link or read the article.
* A link to a betting operator is dropped by the server and its label kept; a typed name that
  matches nobody on the squad is saved and waits under **Needs review**. The sheet says either
  in plain words. A successful write reloads every screen of that league.

**A change needs the server's API key.** The app reads it from `hardwood.env` at launch and sends
it with every request, and never sends an `Origin` header (a browser request always carries one,
which is why the server judges one as a browser's and a native one as yours). If a write is
refused with 401 or 403 the sheet says: *Your server needs its API key for changes. Open Settings ▸
Leagues ▸ Read API key from hardwood.env.* If `hardwood.env` has no key (installed with
`--no-api-key`, or the line was removed), run `install.sh` again: it adds one and never replaces
yours (RUNBOOK §1c, "The API key"). A server holding the invented demo league refuses every write
and the sheet shows its own sentence.

---

## 12. Where the app keeps things

| What | Where |
| --- | --- |
| The server's settings, including the API key the app reads | `~/Library/Application Support/Hardwood/hardwood.env` (the server's file; the app only reads it) |
| **Your dashboards** | `~/Library/Application Support/com.hardwood.nbastats/Layouts.json` |
| The widget cache (always safe to delete) | `~/Library/Caches/Hardwood/Widgets` |
| Preferences: the selected screen and league, favourites, a server address or key you set | The app's `UserDefaults`, every key starting `hardwood.` (see below) |
| The server's databases, inbox and logs | `~/Library/Application Support/Hardwood/` and `~/Library/Logs/Hardwood/` (RUNBOOK §1c) |

Your dashboards are **not** in the server's data folder, on purpose: `uninstall.sh --purge-data`
deletes that folder, and your dashboards must not go with the server. (Back them up by copying
`Layouts.json`.)

The API key is kept in the app's preferences like the server address, not in the Keychain. It is
a key to a server that listens on this Mac only; moving it to the Keychain is a possible later
change. The app never displays it or writes it anywhere else.

The Mac's own preferences, if you ever need to reset one (`defaults delete com.hardwood.nbastats
<key>`, or delete them all with `defaults delete com.hardwood.nbastats`):

| Key | Holds |
| --- | --- |
| `hardwood.mac.selection`, `hardwood.mac.league` | Where the app opens |
| `hardwood.favorite.clubCode`, `hardwood.favorite.teamID` | Your favourite club and NBA team |
| `hardwood.mac.welcomeDone` | The Welcome card has been finished or skipped |
| `hardwood.mac.starterSeeded`, `hardwood.mac.starterLayoutID` | The Matchups & Defence dashboard has been made, and which one it is |
| `hardwood.api.baseURL`, `hardwood.api.apiKey` | A server address and key set in Settings or read from `hardwood.env` |

---

## 13. When something looks wrong

| Symptom | Cause | Fix |
| --- | --- | --- |
| Banner: "Hardwood's server isn't answering at http://127.0.0.1:8000" | The server is not running | `launchctl list \| grep hardwood`. Click **Copy Restart Command** and paste it into Terminal, or run `install.sh` again. **Open Logs Folder** and read `api.log`. **Use Demo Data** lets you browse meanwhile |
| Banner with a key: the key was refused | The app holds an old key, or `hardwood.env` changed | **Read Key from hardwood.env** (banner, or Settings ▸ Leagues). If the file has no key, run `install.sh` again, then restart the server |
| "Your server needs its API key for changes" | A write with no key or the wrong one | The same; §11 |
| Every screen is empty, no banner | The server answers but the store is empty | RUNBOOK §2b for the NBA; the workbook in `inbox/` for the EuroLeague |
| Yellow "Invented demo league — not real games" | The server is in demo mode, or its store was created in demo mode (a store keeps the kind it was created with and never takes real data) | An installed server should not do this. If yours does, paste the lines of `~/Library/Logs/Hardwood/api.log` that mention the EuroLeague store: they name the file and what to replace. For the NBA, use a fresh database (RUNBOOK §2b) and leave `HARDWOOD_DEMO_MODE` unset |
| "EuroLeague is turned off on your server" | `HARDWOOD_EL_ENABLED=off` (or the older `HARDWOOD_EL_MODE=off`) in `hardwood.env` | Remove the line, restart the server (RUNBOOK §1c) |
| "Demo data for this screen isn't bundled yet" | Demo mode, and that screen has no bundled fixture | **Use My Server** |
| Injuries say "no report yet" (NBA) | The NBA publishes none before about 19 October | Nothing to do; this is true |
| Defence says "Withheld" | Too few games for that team (6 are needed) | Nothing to do; the message says how many there are. Choose the **Season** window, or last season (NBA) |
| A tile says "Demo mode has no bundled EuroLeague data for this widget" | Demo mode and the fixture is missing from the app bundle | `scripts/sync_contracts.sh`, rebuild; or **Use My Server** |
| Xcode: "Signing for 'Hardwood' requires a development team" | §3 | |
| Xcode offers no **My Mac** | The scheme is HardwoodTests, or Xcode is older than 16 | Select the **Hardwood** scheme; check Xcode ▸ About Xcode |
| The window is gone | You closed it; the app is still running | **Window ▸ Hardwood** |

### The first things to suspect on a first run

These macOS behaviours could not be checked without a Mac, and each is isolated to one file. If one
misbehaves, say which, and the fallback is already worked out:

| If this misbehaves | Look at | Fallback |
| --- | --- | --- |
| The toolbar items from the dashboard or Player Search appear doubled or in the wrong place (those two screens have their own navigation stack inside the detail column) | `Mac/MacDetailRouter.swift`, `Mac/MacRootView.swift` | Move the root toolbar onto each screen |
| The inspector does not open, or opens on the wrong screen | `Mac/MacScreenChrome.swift` and each screen | A plain toolbar toggle |
| View ▸ Show Inspector (⌃⌘I) does not open a screen's inspector | `Mac/MacCommands.swift` | The toolbar toggle does the same |
| A menu item looks enabled or disabled wrongly (the menus observe the model and the dashboard store) | `Mac/MacCommands.swift` | Every action guards itself, so a stale state is cosmetic |
| Renaming a dashboard from the sidebar does nothing (the rename alert has a text field) | `Mac/MacSidebar.swift` | A sheet instead of an alert |
| Warnings about `Sendable` or main-actor annotations under a newer Xcode | the file named | The project builds in Swift 5 mode, where these are warnings; send them anyway |

---

## 14. Pasting build errors back

Paste the **text** the compiler printed, not a description of it and not a screenshot alone: the
file name and line number are what let the fix be exact.

**From Xcode.** After **Product ▸ Build** (⌘B), open the **Issue navigator** (⌘5). Errors are the
red rows; yellow warnings can wait. Click an error, press ⌘A to select every row, then ⌘C, and
paste. If what you copied has no file and line, use the **Report navigator** (⌘9) instead: click
the latest Build, and select and copy the lines that say `error:` from the log.

**From Terminal**, which is the easiest to paste and gives every file and line (the `awk` only
drops repeated lines and keeps the order):

```bash
cd ~/NBA-Stats/ios
xcodebuild build -scheme Hardwood -destination 'platform=macOS' -quiet 2>&1 | grep 'error:' | awk '!seen[$0]++'
```

For the iOS check, use `-destination 'generic/platform=iOS Simulator'`. To get past a signing
complaint and see the compile errors behind it, put `CODE_SIGNING_ALLOWED=NO` after `-quiet`. For
tests, replace `build` with `test` (§15). If an error line says "see note", also run the command
without the `grep` and paste the lines around it. `xcodebuild` needs Xcode selected as the active
developer directory (`xcode-select -p` should print a path inside Xcode.app).

**Also say:**
* which destination (My Mac, or an iOS simulator) and which step of §2 you were on;
* your Xcode version (Xcode ▸ About Xcode) and macOS version;
* for a launch or runtime problem rather than a build error: what you did, what you expected, and
  the text from Xcode's debug console (⇧⌘Y shows it). A crash also leaves a report in
  Console.app ▸ Crash Reports.

**What to expect.** A first build may show dozens of errors. Many are one mistake repeated, or
follow from one earlier error, so paste all of them once, in the order Xcode lists them, and do not
fix by hand in between. Errors from the Mac build first, then the iOS build, then tests.

---

## 15. Tests, and what was and was not checked

**On your Mac** (⌘U with the destination set to My Mac), or:

```bash
cd ~/NBA-Stats/ios
xcodebuild test -scheme Hardwood -destination 'platform=macOS'
```

The suite is pure unit tests and needs no server. Besides the existing ones it has tests that
decode every bundled league fixture into its Swift type, pin the URL of every league route under
both prefixes (and that the key is sent on every request and no `Origin` header ever is), pin the
number formatting (a missing value is an em dash), and decode the four league widget fixtures. A
test that cannot find its fixture reports a skip naming it, not a failure.

**On Linux, before anything reaches you** (`python3 scripts/check_swift_portability.py`, with
`python3 scripts/check_contracts.py` beside it). These are not a compiler and never say a file
compiles. What they do catch, in seconds:

* a Mac-only API compiled into the iOS build, or the reverse, and every Mac file fenced whole in
  `#if os(macOS)`;
* a table with more than ten columns, or a column that is not sortable, or an `if` inside a table;
* an exhaustive `switch` that has not heard about a new widget kind;
* a payload struct that is not all-optional, or whose properties do not match the fixture keys the
  server actually sends (the rule that catches a guessed payload shape);
* betting vocabulary in a string the app displays; a scene root missing an environment object;
  the Mac plist and project settings drifting.

**What CI checks now:** the Mac app compiles, links, signs and packs into `Hardwood.dmg`, and the
unit tests pass on macOS (`.github/workflows/mac.yml`); the iOS destination builds and its tests pass
on an iPhone simulator (`.github/workflows/ios.yml`). Both use the newest Xcode on GitHub's runners.

**What nothing here could check:** that every screen draws as intended, and anything about the live
EuroLeague service (RUNBOOK §1c and [EUROLEAGUE.md](EUROLEAGUE.md) §8 say what is unconfirmed).

---

## 16. What the app will not do

* **Compute a statistic.** No average, rank, total, miss or leader is calculated in the app.
* **Anything about betting.** No line, odds, edge, lean, pick or win probability: not in the code,
  not in the payloads, not on screen.
* **Fetch from the internet.** The server does that, slowly, for you alone, from your own Mac
  ([LEGAL.md](LEGAL.md) §2d and §2e). Do not put the server on a public address.
* **Read an article.** A headline is a title, a link, a date and an outlet's name.
* **Guess a person.** A name that matches two players waits in Needs review.
* **Show 0 for something unknown.** An em dash, with the reason where there is one.

Not built, and noted so they are not mistaken for bugs: a Mac app icon (the Dock shows the generic
one), notarisation or the App Store, the sandbox, Keychain storage for the key, column show and hide, the server's live event
stream (the app checks once a minute instead), and a menu-bar item.
