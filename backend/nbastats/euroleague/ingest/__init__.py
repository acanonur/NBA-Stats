"""EuroLeague live ingest: five polite requests, parsed closed, checked, reconciled, written.

This package brings EuroLeague results, box scores, clubs and squads into the EuroLeague store
(``hardwood_el.db``) from the league's data service, politely, for one person's private use. It is
the live counterpart of the workbook importer (``importers/workbook.py``): the workbook is how the
season's first rounds arrive, this is how the rest do, and both write the same tables under the
same invariants.

**Nothing in this package has ever spoken to the real service.** It was written in a container
whose proxy refused every EuroLeague host, from documentation of the service rather than from a
recording of it. Every parser therefore fails closed (an unexpected shape becomes an explicit
"unreadable" state, with the key it wanted and the keys it found) and the first real run is a
command built for diagnosis:

    python -m nbastats.euroleague.ingest probe

Modules, in the order a response travels
----------------------------------------
``endpoints``   the five allowlisted URL templates and the matcher that refuses every other URL
``client``      the polite HTTP client narrowed to those five, with a request budget, one breaker
                for the whole service and gzipped raw bodies kept on disk (never in git)
``parse``       tolerant, fail-closed parsers for the five responses (rounds, games, box score,
                clubs, registrations) and the digest that decides whether a box score changed
``invariants``  the rules a box score must obey (points formula, rebounds, five men for forty
                minutes, players add up to the team and the final score) and what failing one
                does: quarantine
``reconcile``   workbook identities to official ones: people by club and folded name, provisional
                game ids to official, clubs verified or reported as mismatched, never guessed
``write``       the transactional writer: schedule upsert, box-score write-or-quarantine, people,
                raw-payload rows, ingest log, source state and job cursors
``jobs``        the worker's five entry points and their cadence, each of which skips before it
                touches the network when nothing is due
``probe``       the first-run diagnostic: five requests, recorded locally, parsed, explained
``__main__``    ``probe``, ``import-workbook``, ``backfill``, ``run``, ``reconcile``, ``status``

What it does not do
-------------------
No gambling vocabulary of any kind, no win probability, no terms gate (the posture in
``docs/LEGAL.md`` is applied: private, personal, non-commercial, never redistributed, attributed),
no ``live.euroleague.net`` legacy feeds, no v1 XML, no v3 statistics, and no scraping of anything
but the five documented JSON endpoints and the headline feeds' own RSS.

This module is deliberately import-light: the worker checks that the package exists before it
schedules the inbox job, and must not pay for an HTTP stack to find out.
"""

from __future__ import annotations

__all__: list[str] = []
