"""Hardwood ingest: how numbers get into the store, and how they stay correct.

The pipeline is deliberately two-armed, because the two problems are different
(see ``docs/DATA_SOURCES.md``):

``backfill``  history, loaded once from **already downloaded bulk files** — the Kaggle
              NBA Database, the Basketball-Reference season dumps, hoopR/shufinskiy
              parquet. Pulling ~64,000 games one at a time from stats.nba.com would be
              tens of thousands of calls and days of runtime, so we never do it.
``daily``     the present, kept current by a small ``nba_api`` job that polls the
              scoreboard and ingests each game **the moment it goes Final**.

Module map
----------
``client``     polite stats.nba.com client; optional ``nba_api``, recorded-fixture mode
``normalize``  the V2 (``UPPER_SNAKE_CASE``) / V3 (``camelCase``) translation layer
``daily``      poll → ingest one game → bump ``sync_version`` → re-aggregate
``backfill``   bulk-file loaders and the id crosswalk
``aggregate``  everything derived: season rows, team seasons, league distributions
``runner``     the scheduler entry point and CLI

Invariants every module here upholds
------------------------------------
* **Writes are upserts.** The league issues post-hoc stat corrections, so an ingest
  run must be able to overwrite yesterday without duplicating it. Running any loader
  twice produces exactly the same rows.
* **``sync_version`` increments once per finalized game**, per ``CONTRACT.md`` §8 —
  not once per row and not once per night.
* **Era honesty.** A stat the era did not record is written ``NULL`` with no advanced
  row at all before 1996-97; it is never written as ``0``.
* **Percentages are fractions in ``[0, 1]``** from the moment a row is normalized.
"""
from __future__ import annotations

import pkgutil

#: The submodules this package actually ships, discovered rather than hard-coded.
#: ``runner`` is the scheduler entry point and is built separately from the library,
#: and a name listed here with no module behind it makes ``from nbastats.ingest
#: import *`` fail with an ``AttributeError`` instead of simply offering one name
#: fewer — the same tolerance ``nbastats.api.app`` gives its optional routers.
__all__ = sorted(module.name for module in pkgutil.iter_modules(__path__))
