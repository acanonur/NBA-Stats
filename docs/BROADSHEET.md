# The Fantasy Board — implementing the `Hardwood Predictions` design

Built from the Claude Design handoff `Hardwood Stats Prediction App`, artboard **2b (Range)**.
The design itself was reverse-engineered from this repository, so most of its tokens are the
ones already in `DesignSystem/Theme.swift`; what follows is what actually changed.

---

## 1. What was built, and what was not

The design's prediction layer is drawn against **sportsbook lines**: a book line per stat,
odds (`O −112 · DK`, `U −105 · FD`), an edge column, and Over/Under probabilities. None of that
is implemented, and it should not be added later without the licensing conversation first:

* **NBA.com's Terms of Use forbid using their statistics in connection with gambling**, and
  every number in this app traces to NBA.com. See [LEGAL.md](LEGAL.md).
* **The source monograph declines the claim.** It states plainly that with no access to
  historical lines it establishes nothing about market efficiency or profitability. Rendering
  `Edge +2.7` as actionable would assert what the research explicitly withholds.
* There is no odds feed in this project, and adding one would not resolve the first point.

Everything else in 2b is implemented. The substitution is small and, I would argue, an
improvement: **the band's tick becomes the player's own season average** instead of the book
line. The question "is tonight's projection above or below what this player normally does"
needs no third party to answer, and it is the comparison the rest of the app already makes.

## 2. The range bar

The design's anatomy, translated directly (dimensions in points):

```
  ├─────────────────────────────────────────────┤   baseline rule, 1pt, neutral-300
            ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓                 band, 5pt, neutral-400   ← p10…p90
                    ●                                dot, 11pt, textPrimary  ← projection
                 │                                   tick, 2×20pt, accent    ← season average
  p10 25            season avg 31.5 · proj 34.2            p90 43
```

The band is the 80% predictive interval the projection engine already produces, which *is* the
10th-to-90th percentile — so the design's own caption is accurate for our data with one
correction: ours is analytic (negative binomial), not "10,000 simulated games". The caption
says so.

The tick takes the cyan accent when the projection sits above the reference and magenta when
below, preserving the design's colour logic (cyan for the model's side) without its betting
meaning.

## 3. Presentation is a property of the layout

`DashboardLayout.presentation` is `"tiles"` or `"broadsheet"`. It governs the page, not a
widget, because the change is structural: broadsheet removes the card chrome entirely — no
fills, no borders, no radius — and replaces the two-column grid with a single editorial column
of hairline rules, uppercase kickers at `.08em` tracking, and dot leaders.

A layout that omits the key is `tiles`, so every document written before this field still
loads. The nine existing presets are untouched.

## 4. Broadsheet tokens

From the handoff's `_ds/broadsheet-…/styles.css`. Light values are the design's; dark values
are derived, since the design only specifies light.

| Token | Light | Dark |
| --- | --- | --- |
| background | `#F3F2F2` | `#161514` |
| text | `#201E1D` | `#EDEBEA` |
| rule (neutral-300) | `#D7D3D3` | `#3A3736` |
| band (neutral-400) | `#BAB6B6` | `#514D4C` |
| accent (above) | `#006786` | `#4FC3E8` |
| accent-2 (below) | `#AA0B56` | `#FF7FB0` |

Type is **serif** — `Source Serif 4` in the design, `.system(design: .serif)` on device, which
is the largest single departure from the current sans-serif app. Numerals use tabular figures
throughout, as the design's `font-feature-settings:'tnum' 1` specifies.

## 5. "Why" — factor contributions

The design's "Why" section lists signed point contributions (`+1.6 Denver plays the 4th-fastest
pace…`). The engine's factors are *multipliers* (1.021), so the contribution is derived:

```
contribution ≈ projected_mean × (factor − 1)
```

This is a first-order attribution, not an exact decomposition — the factors are multiplicative,
so they do not sum to the total difference. The payload reports it as `contribution` and the
widget labels the section as the largest movers rather than a complete accounting, because
claiming an exact decomposition of a product would be wrong.

This section is the best idea in the design. `PROJECTION.md` §7 already required showing the
factors, on the grounds that doing so "turns the projection from an oracle into an argument" —
the design found the right form for it.

## 6. Ranking the board

Six rows fit. Which six is the only editorial decision the backend makes, and the obvious
answer is wrong: **ranking by `|delta|` ranks by unit size.** A 1.3-point move beats a
1.2-rebound one because a point is a smaller thing than a rebound, so a board asked for
`pts, reb, ast` comes back as six rows of `pts`, every night, forever. The first fixture
generated did exactly that.

The board therefore ranks on `deltaZ` — the delta divided by the projection's own predictive
spread, recovered from the published interval as `(high − low) / 2·z₀.₉`. That is scale-free
and it is the quantity a reader actually means by "surprising". `tests/test_projection_board.py`
keeps both halves honest: one test asserts the board does not collapse to one metric, and its
power check re-ranks the same rows by raw delta and asserts that it *would* have.

`deltaZ` is a ranking scale, not a significance claim. Values well under 1 are normal, because
one game's noise is large — which is §4 of `PROJECTION.md` restating itself.

## 7. Three dates, because there are three dates

The board picks its players off the **last completed slate** and projects each of them forward
to whatever **they** play next. Those are different days, and players' next games need not
share one night either. The payload names all three rather than picking one and being wrong
two-thirds of the time:

| Key | Meaning |
| --- | --- |
| `selectionDate` | the completed slate the candidate list came from |
| `date` | the earliest game being projected — the board's "tonight" |
| `throughDate` | the latest, or null when every row is on `date` |

A client heading the board with `selectionDate` would print last night's date above tomorrow
night's numbers. The first draft did that too.

## 8. Where the broadsheet stops

`LayoutPresentation` governs the page, the grid, the widget chrome and any widget that has a
variant. Today exactly one does: `ProjectionBoardWidget`. Everything else renders unchanged, and
three pieces of shared chrome stay in the app's tile treatment even on a broadsheet page:

* `LoadingTile`, `ErrorTile` and `UnavailableTile`
* `AvailabilityBadge` and `StalenessDot`
* the configuration and catalog sheets

This is a stop, not an oversight. Reskinning the state views means touching every widget's
failure path to serve one preset, and a sans-serif "Try again" on a serif page is a smaller
problem than a regression in the ten presets that are not broadsheets. The loaded-state
footnote *is* restyled, because it is the only one of these a reader sees when nothing is wrong.

Two consequences worth stating rather than discovering:

* A widget dragged onto a broadsheet layout renders in its tile typography inside broadsheet
  chrome. It is legible and it is honest about being a tile; it is not designed.
* A `projection_board` dragged onto an ordinary dashboard renders as a tile, deliberately — the
  widget reads `\.isBroadsheet` rather than assuming its own presentation, so it never becomes a
  serif island in a grid of cards.
