# Next-game projection — the formulation

Implemented from *"Forecasting the Next-Game Box Score of an NBA Player: A Mathematical and
Empirical Treatment of Opportunity, Rate, Shrinkage, Dynamics and Market Translation"* and its
accompanying `nbaproj` pipeline. Code lives in `backend/nbastats/projection.py`; the constants
are in `backend/nbastats/projection_constants.py`, each traceable to a table in the paper.

---

## 1. The master formula

Every counting statistic decomposes into **opportunity × rate**, adjusted by multiplicative
context factors (paper eq. 4.6):

```
Ŝ  =  M̂ · r̂_reg · f_pace · f_opp · f_home · f_rest
```

* `M̂` — projected minutes. **This is where the error lives.** The paper's headline finding is
  that supplying *true* minutes improves points RMSE by 19.28% and rebounds by 17.63%, whereas
  the best model over a naive last-ten baseline gains only 3.71% and 3.87%. Minutes projection
  dominates everything else, so it gets its own model and its own decay rate.
* `r̂_reg` — the shrunken per-minute production rate (§2).
* the `f` factors — context, each normalised so that 1.0 means neutral (§3).

## 2. The regressed rate

Three estimates are blended, all strictly pre-tip-off:

```
r_season = (Σ stat_season + k·μ) / (Σ min_season + k)      padding estimator, season
r_career = (Σ stat_career + k·μ) / (Σ min_career + k)      padding estimator, career
r_form   = EWMA_h(stat) / EWMA_h(minutes)                  recent form

w        = n / (n + 400)         n = cumulative season minutes
r        = w·r_season + (1-w)·r_career
r̂_reg    = 0.75·r + 0.25·r_form
```

`k` is the **stabilisation constant**: the exposure at which a player's own rate carries equal
weight to the league prior. It is `σ²_e / τ²` — within-player noise over between-player spread —
and it varies by a factor of 9.5 across statistics. That variation is the whole point: applying
one shrinkage to every stat is wrong in both directions at once.

### Constants (paper Table B.3 and B.4)

| Stat | league rate μ (per min) | k (minutes) | EWMA half-life (games) |
| --- | --- | --- | --- |
| Points | 0.4182 | 81 | 6 |
| Rebounds | 0.1762 | 34 | 8 |
| Assists | 0.0915 | 36 | 8 |
| 3PM | 0.0315 | 62 | 12 |
| Turnovers | 0.0570 | 209 | 15 |
| Blocks | 0.0202 | 69 | 20 |
| Steals | 0.0314 | 322 | 30 |
| **Minutes** | — | — | **2** |

Rebounds stabilise in 34 minutes because rebounding volume is a structural consequence of role
and size. Steals need 322 — roughly a full season — because they are rare events dominated by
opportunity. Shooting percentages stabilise at 275 attempts (3P%), 129 (FG%) and 25 (FT%).

Minutes needing a 2-game half-life while production rates need 6–30 is the quantitative
justification for keeping opportunity and rate separate: no single-series model can weight
history correctly for both. A rotation change is a discrete, persistent event; shooting ability
drifts slowly.

**Note on common practice:** a five-game average sits on the steep left flank of the loss curve
for every statistic examined. A one-game half-life costs 8.1% RMSE for points; a 120-game
half-life costs 8.0%. Both ends are wrong.

## 3. Context factors

```
f_pace = Pace_tm · Pace_opp / Pace_lg²          (eq. 4.7)
f_opp  = DRtg_opp / DRtg_lg
f_home, f_rest                                   empirically calibrated multipliers
```

The product-over-league pace form is preferred to an average because writing
`Pace_tm = Pace_lg(1+a)` and `Pace_opp = Pace_lg(1+b)` gives `f_pace ≈ 1 + a + b` — the two
teams' tempo deviations *add*, which is the behaviour you want.

`f_home` and `f_rest` are fitted as ratios of realised to raw-predicted totals within each
bucket (rest days clipped to 0–3), which absorbs a global bias correction at the same time.

## 4. The predictive distribution

A mean is not a forecast. The conditional distribution is negative binomial:

```
Var = μ + α·μ²        r = 1/α        p = r/(r+μ)
```

α is fitted by the method of moments: `E[(y-μ)² - μ] = α·μ²`.

**A pooled α is badly miscalibrated.** For points it understates realised variance by 28.9% and
covers only 69.9% of nominal 80% intervals. The fix is a per-player dispersion multiplier,
itself shrunk (paper eq. 10.5):

```
z²_i  = mean over games of  (S - μ)² / (μ + α·μ²)
ĉ_i   = (n_i·z̄²_i + k_c·1) / (n_i + k_c),      k_c = 60 games
Var_i = ĉ_i · (μ + α·μ²)
```

This is the padding estimator of §2 applied to a variance ratio, with prior mean 1. Coverage of
the nominal 80% interval rises from 69.9% to **79.1%**. A player in the top decile of volatility
carries more than twice the predictive variance of one in the bottom decile at the same mean;
players with little history shrink fully to 1.0, which is the intended behaviour.

## 5. Combination lines (PTS+REB+AST)

The residuals are **positively dependent**, so the sum is not the sum of independent parts:

| | PTS | REB | AST |
| --- | --- | --- | --- |
| **PTS** | 1.00 | 0.30 | 0.17 |
| **REB** | 0.30 | 1.00 | 0.20 |
| **AST** | 0.17 | 0.20 | 1.00 |

```
Var(ΣS) = σᵀ C σ            (not Σσ²)
```

Held-out: the independent calculation gives an SD of 6.70 against a realised 7.68 — ignoring
covariance **understates the spread by 12.8%**. The app therefore reports the correlated
interval and shows what the independent one would have been, because the gap is the interesting
part.

## 6. What this implementation deliberately omits

The source pipeline includes market translation — implied probabilities, vig removal, expected
value, Kelly staking, edge erosion. **None of it is implemented here.**

Two reasons, and the first is sufficient: NBA.com's Terms of Use forbid using their statistics
in connection with gambling, and this app is built on that data (see [LEGAL.md](LEGAL.md)).
Second, the paper is explicit that it makes no claim about market efficiency or profitability,
having had no access to historical lines — so a staking feature would be asserting something
its own source declines to assert.

What is implemented is the statistical object: a mean, an honest interval, and the inputs that
produced them.

## 7. Honesty requirements in the UI

These follow from the paper's own findings and are enforced in the widget:

1. **Never show a mean without its interval.** The whole contribution of §4 is that the mean
   alone is misleading, and by a quantified amount.
2. **Show the projected minutes separately and prominently.** It is the dominant error term; a
   reader who disagrees with the minutes should be able to see that immediately rather than
   discovering it buried in a point estimate.
3. **Show the context factors.** `f_pace`, `f_opp`, `f_home`, `f_rest` are multiplicative and
   each is one number; showing them turns the projection from an oracle into an argument.
4. **Degrade honestly.** A player with little history has a rate shrunk almost entirely to the
   league prior and a dispersion multiplier of 1.0. That is not a confident projection and must
   not be rendered as one — the widget reports the exposure behind the estimate.
5. **A projection is not a record.** It carries `availability: "estimated"` so it can never be
   confused with a stat that actually happened, which is the same rule the rest of the app
   applies to pre-1997 numbers.

---

## 8. The team-score model — a different object, shared by both leagues

Everything above is the player model: one player's next box score, from the paper. This section is
a separate, much smaller model that answers a different question: **what will each team score in
this game?** It is the EuroLeague workbook's mechanism written as pure functions
(`backend/nbastats/shared/team_projection.py` and `injury_layer.py`, standard library only), and
the NBA uses the same functions with its own inputs. It does not use the player model's numbers,
and the player model does not use it: the NBA slate shows a team's key absences by their season
averages, never as a sum that has to agree with a team projection from a different model.

The EuroLeague's constants, the workbook's own numbers, and every place the code deliberately
differs from the workbook are in [EUROLEAGUE.md](EUROLEAGUE.md); this section is the model and the
NBA's choices.

### 8.1 The formulas

```
rating(prior, L, r, adj)  =  L + (prior − L)(1 − r) + adj
attack index  A = pf / L        defence index  D = pa / L          (higher D is a worse defence)
home  =  L · A_home' · D_away + h/2
away  =  L · A_away' · D_home − h/2           A' = A · af  (af: availability factor, §8.2)
margin = home − away          combined points = home + away
after the game:  adj ← adj + w · (actual − projected)
```

`L` is the league's average team score, `r` how far last season is pulled back to it, `h` the home
advantage in points (0 at a neutral venue), and `w` the weight of one game's miss. A margin under
half a point in size is a **toss-up**, with no projected winner. The **availability effect** is
the projection minus the same projection at full strength, per team and combined. Combined points
are the plain sum, to one decimal, and are never rounded to a half point or compared with
anything.

An 80% interval is the centre ± 1.2816 standard deviations, and is **absent, not invented**, when
the spread is not known.

### 8.2 The injury layer

Each player has expected minutes *m*, expected points *p* and a chance of playing *c* from his
status. Missing minutes are refilled at a replacement rate *q*; teammates then recover a share
*a* of the missing scoring above that; the recovery is shared in proportion to scoring and capped
at a boost κ to any one player:

```
repl = lostMin · q        absorbed = repl + a·(lost − repl)   if lost ≥ repl, else lost
af = (avail + absorbed) / full
```

Two things differ from the workbook and are on by default: an absence can never *raise* a
team's scoring (the clamp in the second line), and under the default `consistent` cap policy a
team's projection equals the sum of its players' (the workbook's formula can credit a team with
points no player is given when the cap binds). `workbook` stays selectable and is what the replay
test uses. **Defence is never changed by an absence.**

### 8.3 The NBA's instance

| Piece | NBA choice | Label |
| --- | --- | --- |
| League level `L` | `(n·L_now + 150·L_last) / (n + 150)`, with *n* the season's team-games so far, regulation-scaled, so it blends smoothly with no jump partway through the year | `leagueLevelWeight` = 150, DEFAULT |
| Priors | Last season's regulation-scaled points and points allowed, regressed 30% towards the league. A team with no previous season starts at `L` | `priorRegression` = 0.3, DEFAULT (not the workbook's) |
| In-season update | The shared update with `w = 1/(n+10)`, over the season's games in date order. "Projected" is the model's own walk-forward projection at that time, with availability as it was known then | `priorWeightGames` = 10, DEFAULT |
| Home advantage | The mean of (home − away points) over this and last season once there are 300 or more final games; otherwise 2.5 | 2.5 DEFAULT until fitted |
| Neutral venues | Not tracked for the NBA, so every NBA projection says the venue was assumed | — |
| Injury inputs | A player's points and minutes are blended `(n·current + 10·last season)/(n + 10)` over players whose latest team this season is the team; `q` is 0.52 × the league's points per minute this season; `a` = 0.6, κ = 1.35, `consistent` | `replacementShare` = 0.52, DEFAULT |
| Chances of playing | out 0, doubtful 0.25, questionable 0.5, probable 0.85, available 1 | DEFAULT (the workbook's table, borrowed) |
| Intervals | The spreads are fitted nightly by `projections.calibrate` from last season's walk-forward misses; with no previous season in the store they, and the interval, are absent and shown as an em dash. After 300 locked games they are fitted from the ledger instead | `fittedPrevSeason`, then `fittedLedger` |
| Seasons | 1996-97 onwards, the same era boundary as the player projection. Earlier seasons answer `unavailable` | — |

The EuroLeague's 9 points per 40 minutes is a EuroLeague number and is **not** used for the NBA:
the NBA's replacement rate is derived from NBA rates. None of the NBA's numbers is the
workbook's, and the payload says which are defaults. The workbook's spreads (9.5 and 11.5) are
EuroLeague numbers too, and are never used here.

### 8.4 Recording, locking and review

A projection is only worth reviewing if it is the projection that was actually made. Every game
tipping within 48 hours gets a `latest` row whenever its inputs change (`projections.refresh`,
every 30 minutes), and in the hour before tip-off the newest `latest` row is copied to a `locked`
row (`projections.lock`, every 5 minutes). **A locked row is never created at or after tip-off**;
if nothing was recorded in time the review says "No projection was recorded before tip-off". The
review reports, for locked rows only: winners called (toss-ups counted separately), the mean
absolute miss in the margin, in each team's score and in the combined points. The NBA's ledger
lives in the stats store and is wiped with the games when the demo league is re-seeded; a ledger
must never outlive the games it is graded against.

### 8.5 What the team model deliberately omits

There is **no win probability** in this version (a design choice made when it was built, which
can be revisited), no total rounded to a half point, and no way to give it a number to compare a
projection with: no route takes one, no setting holds one, and a structural test fails the build
if one appears. The paper's market-translation machinery (§6) stays out for the same reason.

### 8.6 Honesty requirements in the UI

1. **Say it is an estimate.** Every team projection carries `availability: "estimated"`.
2. **Show the interval, or the dash.** A projection with an interval shows it; one without shows
   an em dash, never a made-up spread. An *assumed* spread (a workbook number not yet checked
   against results) is labelled "assumed spread".
3. **Show what absences cost.** The availability effect is part of the payload, per team, so the
   reader can see how much of a projection is a status.
4. **Show how old the status is.** A status that has gone out of date stops moving the number,
   and the list says so.
5. **Show what was assumed.** A player nobody reported on is assumed available *by the model*; the
   payload says how many were assumed and why, and the display never labels them "available".
6. **Say when the venue was assumed**, which for the NBA is always.

