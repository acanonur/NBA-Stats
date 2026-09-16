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
