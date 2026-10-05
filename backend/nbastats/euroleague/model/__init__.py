"""The EuroLeague projection model: ratings, player rates, round projections, scorers, the ledger.

The workbook's method, made to keep running after the workbook stops. Each module owns one part,
and each opens with *why* it does what it does, because most of the decisions here fix something
the workbook could not (a game counted twice, a projection made after the result, a team total
that disagrees with its players).

``ratings``           club ratings and the in-season update, one game at a time
``player_rates``      per-40 rates and projected minutes, carried forward by box scores
``round_projection``  the whole model: ratings + squads + availability -> a game's projection
``scorers``           the round's top scorers per club, with no spread and no picks
``ledger``            what was projected and when: latest, locked, reconstructed, imported

The model never touches the NBA store, never reads the network and has no notion of a number to
compare a projection with. :func:`nbastats.euroleague.model.round_projection.get_model` is the
entry point; the worker reaches ``ledger.run_refresh``, ``ledger.run_lock``,
``ledger.run_calibrate`` and ``ratings.run_ratings`` by string name.
"""
