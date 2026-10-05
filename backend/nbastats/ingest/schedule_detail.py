"""Schedule detail: tip-off and arena, written when the scoreboard says them and not otherwise.

Why this table exists
---------------------
``games.game_date`` is the NBA scheduling day in US Eastern. It says nothing about the hour. Two
things need the hour: the projection ledger, whose whole point is that a ``locked`` projection
was computed *before* tip-off (``ledger.lock()`` refuses a row computed at or after it), and the
injury-report job, which decides how often to poll from how soon the next game starts. Without
a tip-off both fall back to the earliest an NBA game can start (12:00 Eastern on the game date),
which is safe but coarse. ``game_schedule_detail`` is where the real hour lives when it is known.

Written only from what the scoreboard actually said
---------------------------------------------------
The scoreboard's tip-off and arena fields are **unverified**: no response could be fetched from
the development environment, so :func:`nbastats.ingest.normalize.normalize_scoreboard_schedule`
reads them from the documented ``ScoreboardV2`` shape and from a handful of other spellings, and
returns ``None`` for anything it cannot interpret exactly. This module's rule follows from that:

* a game gets a row **only if at least one of tip-off, arena name or arena city is present**.
  There is no row whose every column is ``NULL``, so the table is never a place that merely
  records "we looked";
* a field is **never overwritten with absence**. A live or final game's status text is a clock or
  a score, not a tip-off, so the poll that sees ``Q3 4:21`` has no tip-off to offer and leaves the
  one a previous poll stored. A field that *changes* (a game moved) is updated;
* a ``manual`` row (``source = 'manual'``, typed in by a person to correct the scoreboard) is
  **never touched** by a scoreboard poll;
* a game the store does not have (a scoreboard row whose game was skipped for lacking team ids)
  is counted and skipped: the table's key is a foreign key to ``games``.

One request, not two
--------------------
The watch loop already fetches the scoreboard to learn which games went final. This module reads
the *same* response (``StatsClient.last_scoreboard``) after the games have been upserted, so
schedule detail costs no extra request. :func:`record_from_client` is the best-effort wrapper the
runner calls: a failure here is logged and rolled back and never fails the ingest it rode along
with, because tip-off is a convenience for readers and a box score is the product.

Nothing is seeded, and there is no ``data_source`` column: the table holds scheduling facts
about games, and the demo league's games have no real arena or tip-off to record.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping

from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import Game, GameScheduleDetail
from . import normalize
from .normalize import ScheduleDetailRow

__all__ = [
    "SOURCE_SCOREBOARD",
    "SOURCE_MANUAL",
    "ScheduleDetailReport",
    "write_schedule_detail",
    "record_scoreboard",
    "record_from_client",
]

logger = logging.getLogger("nbastats.ingest.schedule_detail")

SOURCE_SCOREBOARD = "scoreboard"
SOURCE_MANUAL = "manual"

_FIELDS = ("tipoff_utc", "arena_name", "arena_city")


@dataclass
class ScheduleDetailReport:
    """What one write did, so a test (or a log line) can say exactly what happened."""

    rows_seen: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    #: Scoreboard rows with no tip-off and no arena: nothing to record, so nothing was written.
    no_detail: int = 0
    #: Rows for a game the store does not hold (the key is a foreign key to ``games``).
    unknown_game: int = 0
    #: Rows left alone because a person's ``manual`` entry stands.
    manual_kept: int = 0

    @property
    def written(self) -> int:
        """Rows inserted or changed."""
        return self.inserted + self.updated

    def as_dict(self) -> dict[str, int]:
        return {
            "rowsSeen": self.rows_seen,
            "inserted": self.inserted,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "noDetail": self.no_detail,
            "unknownGame": self.unknown_game,
            "manualKept": self.manual_kept,
        }


def write_schedule_detail(
    session: Session,
    rows: Iterable[ScheduleDetailRow],
    *,
    now: datetime | None = None,
) -> ScheduleDetailReport:
    """Write the rows that carry something. Flushes, never commits.

    See the module docstring for the four rules (only present fields, never overwrite with
    absence, never touch ``manual``, skip unknown games). ``ingested_at`` is the time of the
    last *write*: it moves only when a field actually changed, so an unchanged slate polled
    every few minutes leaves the table byte-for-byte as it was.
    """
    report = ScheduleDetailReport()
    stamp = now or utcnow()
    for row in rows:
        report.rows_seen += 1
        if not row.has_detail:
            report.no_detail += 1
            continue
        if session.get(Game, row.game_id) is None:
            report.unknown_game += 1
            logger.info("event=schedule_detail_unknown_game game_id=%s", row.game_id)
            continue

        existing = session.get(GameScheduleDetail, row.game_id)
        if existing is None:
            session.add(
                GameScheduleDetail(
                    game_id=row.game_id,
                    tipoff_utc=row.tipoff_utc,
                    arena_name=row.arena_name,
                    arena_city=row.arena_city,
                    source=SOURCE_SCOREBOARD,
                    ingested_at=stamp,
                )
            )
            report.inserted += 1
            continue
        if existing.source == SOURCE_MANUAL:
            report.manual_kept += 1
            continue

        changed = False
        for field in _FIELDS:
            new_value = getattr(row, field)
            if new_value is not None and new_value != getattr(existing, field):
                setattr(existing, field, new_value)
                changed = True
        if changed:
            existing.ingested_at = stamp
            report.updated += 1
        else:
            report.unchanged += 1
    session.flush()
    return report


def record_scoreboard(
    session: Session,
    payload: Mapping[str, Any],
    *,
    game_date: date | None = None,
    now: datetime | None = None,
    commit: bool = True,
) -> ScheduleDetailReport:
    """Read a ``ScoreboardV2`` payload and write what it says about when and where.

    The games must already be in ``games`` (the scoreboard poll upserts them first). With
    ``commit=False`` the caller owns the transaction.
    """
    rows = normalize.normalize_scoreboard_schedule(payload, game_date=game_date)
    report = write_schedule_detail(session, rows, now=now)
    if commit:
        session.commit()
    if report.rows_seen:
        # A poll every minute during games should not write a log line a minute to say nothing
        # changed; a write, or a row for a game the store lacks, is worth an INFO line.
        level = logging.INFO if report.written or report.unknown_game else logging.DEBUG
        logger.log(
            level,
            "event=schedule_detail date=%s %s",
            game_date.isoformat() if game_date else "-",
            " ".join(f"{key}={value}" for key, value in report.as_dict().items()),
        )
    return report


def record_from_client(
    session: Session,
    client: Any,
    game_date: date,
    *,
    now: datetime | None = None,
) -> ScheduleDetailReport | None:
    """Record schedule detail from the scoreboard ``client`` fetched for ``game_date``.

    Makes no request: it reads the payload the client remembered
    (:meth:`~nbastats.ingest.client.StatsClient.last_scoreboard`). Returns ``None`` when there
    is nothing to read (a client that does not remember, or has not fetched that date) and when
    the write failed. **Best effort by design**: any exception is logged, the transaction rolled
    back, and swallowed, so schedule detail can never fail the box-score ingest it rode along
    with. Call it *after* the poll has committed the games, so the rollback has nothing of the
    poll's to undo.
    """
    recall = getattr(client, "last_scoreboard", None)
    payload = recall(game_date) if callable(recall) else None
    if not isinstance(payload, Mapping):
        return None
    try:
        return record_scoreboard(session, payload, game_date=game_date, now=now, commit=True)
    except Exception as exc:  # noqa: BLE001 - never let a convenience table fail an ingest
        session.rollback()
        logger.warning(
            "event=schedule_detail_failed date=%s error=%s", game_date.isoformat(), exc
        )
        return None
