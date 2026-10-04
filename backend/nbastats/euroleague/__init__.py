"""The EuroLeague, as a sealed package: its own store, its own importers, its own demo.

Hardwood is an NBA product that grew a second league. The cheapest way to add one is to put
its rows in the NBA tables and add a ``league`` column, and the cheapest way is wrong: the
EuroLeague plays 40-minute games, names clubs with three-letter codes that collide with NBA
abbreviations (``MIL`` is Milwaukee and Milan), labels seasons ``E2026`` where the NBA says
``2026-27``, and arrives from a workbook and a rate-limited service rather than from the NBA's
own stats endpoints. A shared table would make every NBA query a EuroLeague query and every
EuroLeague bug an NBA bug. So nothing is shared.

What lives here
---------------
``profile``     the league's constants (competition code, phases, id formats, name folding,
                the committed club-code crosswalk and the gambling-operator denylist)
``config``      the environment switches, and the same-store guard that refuses to run the
                EuroLeague against the NBA's own file
``models``      ``ElBase`` and every ``el_*`` table, with the CHECK constraints that make the
                invariants structural rather than conventional (``schema.sql`` is generated
                from it, and a test fails if the two drift)
``db``          the EuroLeague engine, its session dependency, the store-identity stamp and
                the sync-state cursor
``settings``    the allowlisted model settings and their defaults (there is no key for a line,
                a threshold or anything to be compared with a projection)
``importers``   the stdlib-only xlsx reader and the workbook importer
``demo``        an invented league, shaped like the workbook, for offline use and CI
``bootstrap``   what happens at start-up: guard, stamp, create, demo seed, workbook import

How the EuroLeague cannot leak into the NBA views, and cannot be wiped by the seeder
------------------------------------------------------------------------------------
The tables are on ``ElBase.metadata``, not ``nbastats.models.Base.metadata``, so the seeder's
unconditional ``_clear()``, ``_INSERT_ORDER`` and the generated ``schema.sql`` never see them.
They are created only by :func:`nbastats.euroleague.db.init_el_db` in a file of their own
(``HARDWOOD_EL_DATABASE_URL``), and that function refuses a file that already holds a ``teams``
table. The package may import the shared pure core, the intel plumbing, ``api.errors``,
``api.deps`` and ``config``; it never imports ``nbastats.db``, ``nbastats.models`` or
``nbastats.seed``, and nothing outside it imports it (an AST test holds both lines).

Real data stays on the Mac
--------------------------
The user's workbook holds real data and the repository is public. The importer reads it only
into the local store under ``HARDWOOD_DATA_DIR``; committed fixtures and tests use invented
clubs and players. The store is stamped ``synthetic``, ``workbook`` or ``live`` the day it is
created, and a synthetic store refuses real writes and vice versa, so invented and real rows
can never meet in one file.

What is deliberately absent
---------------------------
No gambling machinery of any kind: no line, over-or-under, probability of beating a number,
edge, lean, pick, price or win probability, in a column, a setting key, a parameter or a
payload. The workbook has such columns; the importer never reads them, and a test imports a
workbook whose gambling cells hold sentinel values and asserts that no sentinel reaches the
store. There is
also no terms gate: the posture in ``docs/LEGAL.md`` (private, personal, non-commercial, never
redistributed, attributed) is applied to every source, and the importer simply runs.
"""

from __future__ import annotations

__all__ = ["LEAGUE_KEY", "PACKAGE_VERSION"]

#: The league key every payload and every store identity carries.
LEAGUE_KEY = "euroleague"

#: Bumped when the importer's reading of the workbook, or the store layout, changes in a way
#: a re-import should know about. Recorded in the ingest log's detail.
PACKAGE_VERSION = "1"
