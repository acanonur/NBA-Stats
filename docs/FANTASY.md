# The fantasy toolkit — nine-category valuation, drafting and trades

Built from the user's `Fantasy NBA 2026-27 Toolkit` workbook. Its Method sheet cites the same
monograph this project's projection engine does, and the stabilisation constants in both are the
same numbers — points 81 minutes, rebounds 34, assists 36, threes 62, turnovers 209, blocks 69,
steals 322. This document records what was implemented, and the four places the implementation
deliberately departs from the spreadsheet.

Code: `backend/nbastats/fantasy.py`, plus the `fantasy_draft_board` and `fantasy_trade` widgets.

---

## 1. The nine categories

Points, threes, rebounds, assists, steals, blocks, turnovers, FG%, FT%. Seven are standardised
straight from a per-game value against the pool — the top `poolSize` players, default 150:

```
z = (value − pool mean) / pool SD          population SD, not sample
z_tov = −z                                  fewer is better
```

The pool is the population of interest rather than a sample drawn from something larger, so the
SD is population. Turnovers are negated *after* standardising, which is arithmetically identical
to negating first but leaves the pool's mean and SD describing the statistic a reader recognises.

### The two percentages are not a z of the percentage

This is the one thing a naive implementation gets wrong, and it gets it wrong badly. A 90%
free-throw shooter taking 1.5 attempts a game is worth almost nothing in the category; a 78%
shooter taking nine is a real liability. Ranking on the percentage puts them the wrong way round.

```
p̂     = (makes + k·p_league) / (attempts + k)      k: FG% 129, FT% 25 attempts
impact = attempts × (p̂ − pool rate)
z      = (impact − mean) / SD
```

Two things to note. The shrinkage exposure is **attempts**, not minutes — a percentage is a
binomial ratio, so what stabilises it is the number of trials. That is paper Table B.5, and it was
already sitting in `projection_constants.PCT_STABILISATION` with no reader until now. And the pool
rate is the attempts-weighted aggregate of the pool's *shrunk* rates, not of its raw ones: mixing
the two makes the impacts sum to something other than zero over the pool, because shrinkage pulls
every player toward a league mean that sits below the pool's, so the entire pool reads as
below-average shooters. `value_pool` asserts the sum is zero.

### Totals

`totalZ` is the weighted **sum** of the nine; `score` is the weighted **mean**. Both are reported
because the workbook uses both, and they differ by a factor of the weight total — a threshold
written for one is wrong for the other by nine. A punted category has weight 0 and leaves every
`z` untouched, so a punt build and a balanced build stay directly comparable and a trade between
two managers with different punts is symmetric.

---

## 2. Four departures from the spreadsheet

### a. It reads season aggregates, not projections

A board ranks 150–460 players. Running the next-game projection per player costs about five
queries and four milliseconds each — over two thousand queries and three seconds for a full
board — and it structurally cannot produce FG% or FT%, because the engine projects no shot
attempts. Reading `player_season` costs a handful of queries flat. Measured rank agreement
between the two is Spearman 0.99, with 145 of the top 150 shared. For a ranking, that is the
right trade.

A consequence worth stating: **the board is a valuation of what a player did, not a forecast of
what they will do.** The workbook's own projections — with its hand-set role multipliers, its
age curve and its injury overrides — are better input than this, and `SeasonLine` is a plain
value object precisely so those numbers can be fed in instead.

### b. It adds no projection constants

The monograph gives a league rate and a stabilisation constant for seven counting stats and
nothing at all for FGA or FTA. Projecting attempt volume would have meant inventing two numbers
and placing them in a module whose docstring says every value carries the table it came from.
The percentages need attempts, not a model of attempts, and the season line already has them.

### c. The roster-spot adjustment is a separate number

Give two players and get one, and the freed slot is refilled from waivers — below pool average by
construction, so the term is negative. It is frequently larger than the difference between the
players themselves. The workbook computes it on its own row and so does this; folding it into the
total makes a reasonable 2-for-1 read as a blowout with no way to see why.

### d. The trade carries a range, not an interval

This is the biggest departure and the one worth arguing about.

The projection engine produces a genuine, calibrated negative-binomial interval — 79.1% coverage
at a nominal 80%. It is tempting to carry that through to a trade delta, and it would be wrong.
That interval is a **single-game count, for one player, in one category**. A trade delta is a
signed sum over up to eight players and nine *standardised* categories. An honest variance for it
needs a 72×72 covariance matrix; this repo has a 3×3, for points/rebounds/assists, within one
player. The missing pieces are not small and they do not point the same way:

* within-player, cross-category dependence for the other six — all riding the same minutes
  channel, so positive;
* across players — shared pace and blowout shocks (positive) against competition for usage
  (negative), so the sign is not even known;
* across the two sides — both evaluated over the same weeks, so `Var(get − give)` has a real
  positive cross term that independence would drop;
* the pool moments themselves, shared by every z and estimated from the same 150 players.

Assuming independence is therefore not conservative. It understates the band in some places and
overstates it in others, with no way to say which dominates.

So instead the widget recomputes the whole trade under **four named scenarios** and reports the
spread with the scenario that produced each end:

| scenario | what it changes |
| --- | --- |
| The player you get misses 22 games | availability on the get side |
| The player you get loses 15% of his minutes | per-game line on the get side |
| The player you give up misses 22 games | availability on the give side |
| The player you give up gains 15% of his minutes | per-game line on the give side |

Nothing distributional is claimed. `flips` is true when the scenarios disagree about the *sign*,
which is the single most useful thing the block can say: a trade that wins under every assumption
is a different proposition from one that only wins if everybody stays healthy.

Two notes on getting this right, both learned by getting it wrong first:

* **The scenarios must be asymmetric.** The first version scaled every player's minutes by the
  same factor. A z-score is standardised against the pool, so a uniform shift cancels exactly —
  five scenarios produced a range of 0.02.
* **Availability is a weight on what you receive, not a change to the line.** A per-game line is
  identical whether a player appears 60 times or 82. Scaling the line by games only perturbed the
  percentage shrinkage exposure, and came out backwards: "the player you get misses 22 games"
  made the trade look *better*.

---

## 3. Verdict bands

The workbook's: fair inside ±0.75 net z-sum per game, clear win or loss beyond 2.0; for points
leagues, ±3 and 8 fantasy points per game. Those are the defaults and they are **configurable**,
because they are a property of a league rather than of the sport — and the payload reports
`poolSpread`, the pool's own SD of `totalZ`, beside them. A fair band of 0.75 means one thing
against a spread of 0.9 and something quite different against 2.8. A threshold quoted without the
scale it lives on is a number pretending to be a judgement.

---

## 4. Draft suggestions

The board sorts on `totalZ` adjusted by **at most a quarter of itself** for the categories the
manager's roster is weakest in, and by exactly nothing until that roster holds three players —
earlier than that, a category shape is noise.

Three things were left out on purpose:

* **Positional scarcity as a multiplier.** Category leagues are won on categories, and position
  eligibility is loose enough in most formats that a scarcity term largely reproduces the
  category term with more noise. Position is shown, not scored.
* **Average draft position.** Drafting toward consensus is how a projection's edge is thrown
  away. The board's job is to disagree with ADP when the numbers do.
* **Tier breaks.** A presentation device. Inventing them here would hide the continuous quantity
  the reader should be looking at.

Every row carries a one-sentence `reason` a reader can disagree with. It follows the *direction*
of each category: a high turnover z means few turnovers, so the phrase is "protects the ball" —
"carries turnovers" reads as praise for the thing the category penalises.

---

## 5. What this is not

No odds, no price, no edge, no stake, and no contest of any kind — the same boundary
[`PROJECTION.md`](PROJECTION.md) §6 draws, enforced here by a test that greps this module for the
vocabulary. See [`LEGAL.md`](LEGAL.md) §2a for why season-long draft and trade analysis sits on a
different footing from operating a fantasy game, and what would change that.
