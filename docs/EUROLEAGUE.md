# EuroLeague — method, constants, deviations and the replay record

Hardwood's EuroLeague side is built around the workbook you supplied (the *Round 3 toolkit*):
its team ratings, its injury layer, its match projections and its scorer projections, written as
code and checked against it. This document says what is computed and how, which numbers are the
workbook's and which are defaults Hardwood chose, every place Hardwood **deliberately differs**
from the workbook and why, and the record of the one check that matters most: whether the code
reproduces the workbook's own Round 3 numbers.

How to run it is in [RUNBOOK.md](RUNBOOK.md) §1c. Where each number comes from, and the posture
it is used under, is in [DATA_SOURCES.md](DATA_SOURCES.md) §7.

---

## 1. What this is, and what it is not

* **Sealed and separate.** The EuroLeague has its own database file (`hardwood_el.db`), its own
  code package and its own URL space (`/v1/el/...`). It cannot appear in an NBA screen, and the
  NBA demo league's re-seed cannot touch it. Club ids are the official three-letter codes and game
  ids look like `E2026-0012`, so they can never be mistaken for an NBA id.
* **Forty-minute games, twenty clubs.** The regulation team-time is 200 player-minutes
  (12,000 seconds) plus 25 minutes per overtime period. The registration lists three positions,
  Guard, Forward and Center; the workbook's five (PG, SG, SF, PF, C) are kept as labels and can be
  chosen as an opt-in view, always marked estimated.
* **EuroLeague games only.** Friendlies, domestic leagues and national-team games are not
  tracked, and every EuroLeague payload says so. The workbook's *Latest Games* and *Game Logs*
  sheets mix those competitions and are not imported ([DATA_SOURCES.md](DATA_SOURCES.md) §7B).
* **Projections are estimates, labelled as such.** Every projected number carries
  `availability: "estimated"`, and the screen shows it.

## 2. What you get on day one — said plainly

* Your workbook, once you drop it in the `inbox/` folder: Rounds 1 and 2 (results and box
  scores), the Round 3 fixtures, and the 56 sourced availability statuses. **Round 3 was played on
  1–2 October, so it shows as "result pending", not as upcoming**, until results arrive.
* Fresh data needs one of two things: live ingest (on by default, a polite trickle from the
  EuroLeague's own data service) or importing an updated workbook.
* Statuses are sourced and dated. They are never presented as live, and a status that is out of
  date stops affecting projections (§4.5).
* Until the replay in §9 has been run on your Mac the projections are labelled as not yet
  replayed against the workbook.

## 3. Where each piece of data comes from

| Data | From | Notes |
| --- | --- | --- |
| Clubs, rosters, positions, fixtures, results, box scores | The EuroLeague data service, or your workbook | The service wins: a workbook never overwrites a service row |
| Team ratings, player rates, model settings | Your workbook (Settings, Team Ratings, Squads) | Labelled with where they came from (§5) |
| Availability | Your workbook's Injury Report, or entered by hand | A link, a label and a date on every row; no copied text |
| Headlines | Configured feeds, title and link only | Never turned into a status automatically |
| Round weights beyond the workbook's | Hardwood's default | Labelled `DEFAULT` (§5) |

A stat that was not recorded is **null and shown as an em dash, never as zero**. Per-game
averages divide by the number of games that actually carry the statistic, and the count is shown
beside the value. Workbook box scores carry no "fouls drawn", "blocks against" or "plus/minus",
so those columns are null for those games and never pull an average towards zero. A shooting
percentage with no attempts is null (the workbook hard-coded 0 for 39 players).

## 4. The method

### 4.1 Team ratings

A club's **prior** is last season's points scored (`pf`) and allowed (`pa`) per game. The league
average **L** is the mean of the twenty clubs' scoring priors and is fixed for the season. A
club's rating after round *k* is last season pulled back towards the league and then adjusted:

```
rating(prior, L, r, adj) = L + (prior - L) * (1 - r) + adj
attack index  A = pf / L        defence index  D = pa / L      (a higher D is a worse defence)
```

`r` is the regression (`priorRegression`, 0.3: rosters changed a lot, so last season is pulled
30% of the way back to average) and `adj` is the club's roster or in-season adjustment.

### 4.2 The match

```
home = L * A_home' * D_away + h / 2          away = L * A_away' * D_home - h / 2
    where A' = A * af, and af is the availability factor from the injury layer (§4.4)
margin = home - away        combined = home + away
```

`h` is the home advantage in points (3.5), split half to each side. It is **0 at a neutral
venue**; a per-game override replaces the default, and when the venue's neutrality is unknown the
default is used and the payload says `venueAssumed`. A margin smaller than half a point in size
is a **toss-up** and has no projected winner. The combined points are the plain sum, shown to one
decimal and never rounded to a half point or compared with anything. The **availability effect**
is the projection minus the same projection at full strength.

### 4.3 After each game

Once a game is final, each club's adjustments move by a weight times the miss:

```
attack_adj  += w_n * (scored  - projected scored)
defence_adj += w_n * (allowed - projected allowed)
```

`n` counts the club's games **from the start of the season**, not from when you imported the
workbook. The workbook's weights are 0.10 for the first game and 0.09 for the second; beyond them
Hardwood uses `1 / (n + 9)` and labels it **extrapolated** (the third game's weight is 0.0833).
The projection that is compared with is the one frozen before tip-off (§4.8). When the Mac was
asleep and none was frozen, Hardwood **reconstructs** it from inputs dated strictly before the
tip-off, records it as reconstructed, and the review keeps those separate.

The workbook's ratings already include rounds 1 and 2, so the update applies only to games after
the workbook's round; nothing is counted twice.

### 4.4 The injury layer

Each rostered player has expected minutes *m*, expected points *p* and a **chance of playing**
*c* from his status (`out` 0, `doubtful` 0.25, `questionable` 0.5, `probable` 0.85,
`available` 1). Then:

```
full = Σp    avail = Σ p·c    lost = Σ p·(1-c)    lostMin = Σ m·(1-c)
repl     = lostMin * q                        (q = 9 points per 40 minutes)
absorbed = repl + 0.6 * (lost - repl)         if lost >= repl, else lost
boost    = min(1 + absorbed / avail, 1.35)
af       = (avail + absorbed) / full
player projected points = p * c * boost
```

Missing minutes are first refilled at replacement level; teammates then recover 60% of the
missing scoring above it; the recovered points are shared in proportion to scoring, capped at a
35% boost to any one player. **Defence indices are never changed by absences**, and the payload's
limitations say so.

A player with no status at all is treated as available *for the model*, and the model says how
many such players it assumed (`assumptions.assumedAvailable`, with the basis). The **display**
never says "available" for someone nobody reported on: it shows an em dash.

### 4.5 When a status stops counting

A status drives a projection only while it is *in force*. It stops when its expected return has
passed, when the player has played a game after the status was published (the box score
supersedes it), or when it is more than 14 days old, unless it is long-term (it mentions
"long-term", "indefinite", "season" or "surgery"), in which case it stays in force and is marked
stale after 7 days. Out-of-force statuses still appear in the list, marked stale; they just stop
moving the numbers. An active manual override wins until cleared.

### 4.6 The player layer

* **Rates.** A player's per-40 rates start from the workbook. After each game, rates move by
  adding that game's points and minutes to the prior, which counts as 400 minutes for a player
  with EuroLeague history and 150 for an estimated line:
  `rate40 = (rate40_prior · P + 40 · Σpoints_after) / (P + Σminutes_after)`.
* **Minutes.** `m = ((k + 3) · m_import + Σminutes_after) / (k + 3 + games_after)`, where *k* is
  the workbook's round. This is the workbook's own `rounds / (rounds + 3)` blend, continued. A
  healthy player who did not play counts as 0 minutes in a round; one missing through injury
  gives no minutes evidence.
* **Squads.** Minutes are normalised so every squad totals 200, no one over 32. Full-strength
  squad points are then **scaled to sum to the club's projected scoring**, so the team number and
  its players agree before any absence is applied.
* **Estimated lines.** Players with no EuroLeague history carry the workbook's lines, marked
  estimated. Where only minutes are known the rates start from the pooled Guard/Forward/Center
  average of the players who have them, with the lighter 150-minute weight.

### 4.7 Scorers

For each club's players in a round: `context X = D_opponent + side · h / (2 · pf_self)`
(`side` is +1 at home, −1 away), `model = projected points × X`, and
`form` = the mean of his last ten EuroLeague games with minutes. The projection is `0` if he is
out, the model alone if he has no recent games, and otherwise
`(1 − 0.25) · model + 0.25 · form · c`. The screen lists the top few per club **by projection**.
There is no player interval.

### 4.8 The ledger and the review

Each projection is written down as it was. `projections.refresh` records a `latest` row when the
inputs change; `projections.lock` copies the newest one to a `locked` row in the hour before
tip-off, and **a locked row is never created after tip-off**. If nothing was recorded in time the
review says "No projection was recorded before tip-off". The review compares locked projections
with results: winners called (toss-ups counted separately), the mean absolute miss in margin, in
each score and in the combined points. The workbook's own published Round 2 projections are kept
as `imported` rows and reviewed under the model name "workbook". Reconstructed rows are reported
apart from locked ones.

### 4.9 Intervals

The workbook's spreads (a team score ±9.5, a margin ±11.5) are used to draw an 80% interval, the
centre ± 1.2816 standard deviations, but they were **never checked against results**, so the
payload says `intervalBasis: "assumed"` and the screen says "assumed spread". Once 100 or more
locked EuroLeague games exist the spreads are fitted from the ledger's own misses and the label
changes. An interval is never invented from the NBA's spread.

### 4.10 Defence by position

What it measures: **points scored by opposing players who are listed at a position**. It does not
measure who guarded whom, and every payload says so. Positions come from the official
registration (Guard, Forward, Center) for the player's club that season, then from the box
score's own listing; a player with neither is "unknown", never guessed. The workbook's five
labels are used only when the store has no registrations, or when you ask for them, and then
always marked estimated.

For each defending club the points allowed to each position are averaged over its games and
compared with the league's. The three positions plus "unknown" add up exactly to the club's
points allowed per game, and the payload shows that identity. A club's index is **shrunk towards
the league** (an empirical-Bayes estimate), so two games of data cannot make a defence look
extreme; bands (better, typical, worse) appear only for a club with enough games and only when
the league shows real position-to-position differences, with a stricter threshold because every
cell is a separate comparison. There are **no ranks**: the table is sorted by points allowed, a
recorded fact. Everything is withheld, with the reason, when fewer than 6 games are in, when
more than 5% of allowed points belong to unlisted players, or when too few clubs have enough
games to estimate the league spread; below 12 games it is marked provisional. Opponent strength
is not adjusted for in this version.

## 5. The constants, and where each one comes from

Every constant carries a provenance label in `/v1/el/method` and in each projection's
`model.constants`. The labels: **workbook** (your number, taken as given), **workbookUnvalidated**
(your number, but never checked against results), **DEFAULT** (Hardwood's choice because the
workbook has none), **fittedLedger** (fitted from locked projections and results) and **manual**
(set by you).

| Constant | Value | Label | What it does |
| --- | --- | --- | --- |
| `priorRegression` | 0.3 | workbook | Share of last season pulled back to the league average |
| `homeAdvantagePoints` | 3.5 | workbook | Home advantage in points; 0 at a neutral venue |
| `teamSd` | 9.5 | workbookUnvalidated | Spread of one team score (drives the 80% interval) |
| `marginSd` | 11.5 | workbookUnvalidated | Spread of the margin (drives the 80% interval) |
| `replacementPer40` | 9 | workbook | Points per 40 minutes at replacement level |
| `absorbShare` | 0.6 | workbook | Share of missing excess scoring teammates recover |
| `boostCap` | 1.35 | workbook | Largest boost to one player from teammates' absences |
| `rotationShare` | 0.5 | workbook | Share of lost minutes the listed rotation takes (minutes display only) |
| `formWeight` | 0.25 | workbook | Weight of the last-ten average in a scorer projection |
| `roundWeight.1`, `.2` | 0.10, 0.09 | workbook | Rating update weight for a club's first and second game |
| `roundWeight.n` for n ≥ 3 | `1/(n+9)` | **DEFAULT**, extrapolated | The same, past the workbook's table |
| `statusChance.out` … `.available` | 0, 0.25, 0.5, 0.85, 1 | workbook | Chance a player with that status plays |
| player minutes per squad | 200, none over 32 | workbook | The squad is normalised to this |
| `prior_minutes` | 400 official, 150 estimated | workbook | Weight of a player's prior rate |
| `capPolicyConsistent` | on | **DEFAULT** | Team and player projections agree (§6) |
| `squadReconcile` | on | workbook | Squad points scaled to the club's rated scoring |
| `overtimeScaling` | on | **DEFAULT** | Scores scaled to regulation length before a rating update (§6) |
| `positionCoverageCeiling` | 0.05 | **DEFAULT** | Most points allowed to unlisted players before defence is withheld |
| defence sample gates | 6 games withheld, 12 provisional | **DEFAULT** | A judgement about sample size, not a fitted number |
| opponent-adjusted minimum | 5 qualifying games | **DEFAULT** | Before an adjusted points figure is shown |
| statuses in force | 14 days; long-term stale after 7 | **DEFAULT** | §4.5 |
| EuroLeague status "stale" | 7 days, or the club has played since | **DEFAULT** | When a status is flagged old |

The model settings are an allowlist, both in code and in the database. There is **no setting for
any number that would be compared with a projection**, and nothing to type one into.

## 6. Where Hardwood differs from the workbook, and why

| # | The workbook | Hardwood | Why |
| --- | --- | --- | --- |
| 1 | Updates ratings with weights for two rounds only | Continues with `1 / (n + 9)` from the club's first game of the season, labelled extrapolated | The workbook's table stops. Counting from the season's start, not the import, keeps the weights continuing rather than restarting |
| 2 | One round-2 update applied to the imported ratings | Updates apply only to games after the workbook's round | Counting rounds 1 and 2 again would double their effect |
| 3 | Compares raw scores | Scales an overtime game's score to regulation length first | An overtime game would otherwise read as a collapse in defence. The workbook did not scale |
| 4 | The boost cap limits each player but not the team | Default `consistent`: the team total equals the sum of its players. `workbook` stays selectable and is what the replay uses | When the cap binds the workbook counts points no player is credited with. The payload says when it binds |
| 5 | `repl + 0.6 · (lost − repl)` always | The same, but never more than was lost | When missing players score less than replacement level the sheet's formula makes an absence raise scoring |
| 6 | Form from the last ten games in any competition | The last ten EuroLeague games with minutes | The any-competition logs are excluded ([DATA_SOURCES.md](DATA_SOURCES.md) §7B) |
| 7 | "Picks": two per club, with a manual overrule | The top few per club by projection | A pick is curation against a number Hardwood does not hold |
| 8 | A home-win percentage per game (win probability) | **Not computed in this version** | A design choice made when Hardwood was built; it was never a request or a decision of yours. The margin and its 80% interval carry the same uncertainty. Adding it back is one field if you want it |
| 9 | A zero for a shooting percentage with no attempts | Null | Zero is a claim about shooting; null is "no attempts" |
| 10 | Per-game averages over every listed game | Over the games that carry the statistic, with the count shown | A game without "fouls drawn" must not read as zero fouls |
| 11 | Free-text venue and local/CEST/TR times | Tip-off from the CEST time in Europe/Berlin converted to UTC; neutral only when the home advantage is 0 and the venue says "(neutral)" | A timezone and a neutral flag should be a recorded fact, not a guess |
| 12 | Five positions | Three official ones, five as an opt-in labelled estimated | The EuroLeague registers Guard, Forward and Center |
| 13 | A player's PIR recomputed | PIR stored as the EuroLeague publishes it | Hardwood does not restate an official number |

Not deviations, but worth saying: the "est." lines are **imported and marked estimated**, as in
the workbook; and an unknown status string is rejected where the workbook silently counted it as
available.

## 7. What is deliberately not computed

The workbook has columns and settings for comparing a projection with an outside number: a typed
line, an over/under probability, a lean, a result against a line, and a home-win percentage.
None is imported, computed or stored. The importer carries an explicit list of those headers and
settings and **a test imports a workbook full of sentinel values in them and proves nothing
reaches the store**. There is no field in any payload and no parameter on any route that takes
an outside number to compare with a projection. [LEGAL.md](LEGAL.md) and
[CONTRACT.md](../contracts/CONTRACT.md) (§11) say why, in full.

## 8. Limitations

* **Two rounds are thin evidence.** After a couple of rounds every defence index is withheld and
  that is correct; they are not hidden by a bug. Bands may show nothing all season if the league
  has no real position-to-position differences, and that is also correct.
* **The ratings move only part of the way** towards a trend. If scoring stays unusually high or
  low the totals lag it.
* **Statuses are a snapshot, not a feed.** The data service has no injury list; every status is a
  person's research with a link and a date, and the screen shows its age.
* **The roster list lags club news in both directions.** A player can be playing before he is
  registered, or registered after he has left.
* **Listed position is not who guarded whom.** Defence by position needs tracking data to say
  that, which Hardwood does not have.
* **Nothing about the live service is verified.** Endpoint shapes, position codes, Round 3 game
  codes and rate limits are known only from documentation until a probe recording on your Mac
  says otherwise. A shape that does not match produces an "unreadable" state, not a guess.
* **Three players' names are abbreviated** in the workbook's roster and may need linking by hand
  to the official ones; they go to a review queue and are never guessed.

## 9. The replay record

The workbook computed ten Round 3 games. The replay recomputes all ten from the workbook's own
inputs and compares the home score, away score, margin, combined points, full-strength points and
availability effect with the workbook's own cells **to 1e-9**. It runs the model in the
workbook's own mode (`capPolicy = workbook`, no squad reconciliation, no overtime scaling), and
includes the club where the boost cap binds and the neutral-venue game. During development the
same arithmetic was reproduced outside Excel to about 5e-13.

It reads your real workbook, so it lives in `backend/tests/local/` (which git ignores) and is
skipped on any machine without the file. Steps: [RUNBOOK.md](RUNBOOK.md) §1c, "Two checks to run
once, and record".

| Date | Code version | Workbook as of | Result | Run by |
| --- | --- | --- | --- | --- |
| — | — | 30 September 2026 (Round 3) | **Not yet run on the Mac** | — |

Until a row here says it passed, EuroLeague projections are labelled as not yet replayed. When
you have run it, replace the dash row with the date, the output of `git rev-parse --short HEAD`,
and the test's summary line (for example `12 passed`). A pass means every comparison was within
1e-9. If it fails, do not edit the test to make it pass: the failure message names the quantity
and gives both the model's value and the workbook's, which is the thing to look at.
