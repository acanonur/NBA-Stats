"""NBA availability and headlines: the official injury report, the user's own entries, news links
and the model constants, kept apart from the stats on purpose.

This package answers one question for the rest of Hardwood: *who is, or may be, unavailable, and
how do we know?* It fetches and parses the NBA's official injury report (a PDF the league
publishes in Eastern-time quarter-hour slots), stores what each report said with its source and
its own publication time, accepts hand-entered overrides, collects headline links, and holds the
NBA model's settings and the scheduler's job state. It computes no projection and shows nothing to
a person; ``nbastats/nba_matchup`` reads these tables and builds the payloads.

Modules
-------
``models``        the tables, on their own ``MetaData`` (``NbaIntelBase``) so a re-seed of the demo
                  league cannot erase them; ``schema.sql`` is the generated DDL
``store``         persistence helpers: the synthetic-store guard, source and job state, snapshots,
                  and the injury parser's persisted "confirmed against a real report" counter
``status``        the availability vocabulary applied to rows: ingesting a parsed report, matching
                  names to players, overrides, the review queue, the source denylist
``report_fetch``  the injury report's URL slots, the polite fetch of one slot and the rule for how
                  far back a run steps
``report_pdf``    the PDF parser: ``pypdf`` imported lazily from the optional ``injuries`` extra,
                  header-checked on every page, failing closed
``news``          headline feeds: configured feeds, persistence, subject matching, pasted links
``settings``      the allowlisted model constants and their defaults
``jobs``          the two entry points the scheduler calls, ``run_injuries`` and ``run_news``

Import discipline
-----------------
``nbastats.db.init_db`` imports :mod:`nbastats.nba_intel.models` (inside the function, so importing
``db`` loads no feature package) to create the tables, so this package's ``__init__`` imports
nothing. The modules above may import ``nbastats.db``, ``nbastats.config``, ``nbastats.shared`` and
``nbastats.intel``; they read the stats tables (teams, games, players) by name with plain SQL
rather than by importing ``nbastats.models``, so neither side can break the other with a rename
that a type checker would not see.

The fantasy toolkit imports nothing from here and never will (``tests/test_fantasy_isolation.py``):
feeding injury statuses into fantasy valuation would widen an argument ``docs/LEGAL.md`` §2a
already calls thin.

Posture
-------
Private, personal, single-user. There is no terms gate; the NBA injury report is on by default
(``HARDWOOD_NBA_INJURIES``) and refused with a clear message when the store holds the synthetic
demo league, judged by the store's own rows. Headlines are on by default (``HARDWOOD_NEWS``), with
``robots.txt`` checked automatically. See ``docs/LEGAL.md`` §2e.
"""
