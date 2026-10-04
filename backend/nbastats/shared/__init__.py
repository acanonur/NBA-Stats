"""The pure core shared by every league: the statistics, with nothing else attached.

Hardwood covers two leagues, the NBA and the EuroLeague, and they must agree about what a
number means. "Points allowed" cannot be one thing on the NBA page and another on the
EuroLeague page; a defence-by-position table cannot reconcile to the score in one league and
approximately in the other. So the arithmetic that both leagues present lives here, once, as
functions over plain Python values. Each league's own package reads its own store, builds
the plain inputs, calls into this package, and serialises the answer; none of the
statistics are written twice.

What the package is made of
---------------------------
==================== =============================================================
``league_profile``    the constants that differ between leagues (game length,
                      sample-size thresholds, home advantage, staleness rules)
``positions``         position normalisers: any source's vocabulary to bucket weights
``availability``      the status vocabulary, the display/model split, and the
                      rules that decide which entry applies and whether it is in force
``team_form``         scoring, points allowed, form, splits, opponent-adjusted values
``defense_position``  points allowed by opponent position: exact allocation,
                      empirical-Bayes shrinkage, withholding, Bonferroni-gated bands
``team_projection``   the workbook's team-score model, formula for formula
``injury_layer``      the workbook's injury mechanism, with its one documented clamp
``refs``              builders for the team, player, source, freshness and game shapes
``market_guard``      the vocabulary no new payload key may use, as callable code
``league_registry``   the late-bound seam by which the rest of the application asks
                      the sealed EuroLeague package something without importing it
``_generated_leagues``the two guard lists, in the form the contract generator writes
==================== =============================================================

The rules this package is held to
---------------------------------
**Standard library only, and no I/O.** A test walks every module's syntax tree and refuses any
import that is not the standard library or this package, and refuses the standard-library
modules that touch files, sockets or processes. The reason is structural: a calculation that
could open a database would be a calculation whose answer depended on which league was loaded.
Callers hand in plain values; they get plain values back.

**A stat that was not recorded is ``None``, never zero.** Averages divide by the number of rows
that actually carry the stat, a percentage with no attempts is ``None``, a rate over zero
minutes is ``None``. Where a zero appears it is a recorded zero (a position nobody played at in
a game scored no points there).

**Fractions in [0, 1] for every percentage; wire keys in lowerCamelCase.** Dataclasses here use
snake_case for Python, and their ``to_payload`` methods write the wire form.

**Fail closed.** Anything unrecognised becomes an explicit "unknown" or "unavailable" state
(an unrecognised position is the ``unknown`` bucket; an unrecognised status raises; a game that
does not reconcile is excluded and counted), never a guess.

**No gambling machinery.** There is no line, no probability of beating a number, no edge, no
lean, no pick, and no probability of winning (omitted in v1 as a design choice). The projected
score, margin, winner and combined points remain, as analytics, and the guard in
``market_guard`` is how a test proves a payload contains nothing else.

Where to start
--------------
``defense_position`` and ``team_projection`` carry the statistics and have the longest
explanations of *why*; ``availability`` is the place to read how a status becomes a number the
model may use. Every module's docstring says what it deliberately does not do.
"""
