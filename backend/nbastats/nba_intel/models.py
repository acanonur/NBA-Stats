"""SQLAlchemy models for NBA availability and headlines, on their **own declarative base and
their own ``MetaData``**, in the same SQLite file as the NBA stats store and never on
:class:`nbastats.models.Base`.

Why a second base is the whole point
------------------------------------
This is the AccountBase precedent (``nbastats/accounts/models.py``), applied to a second kind of
data that must outlive the demo league. ``nbastats/seed.py::_clear()`` deletes every row of every
table on ``Base.metadata``, unconditionally, every time the demo league is seeded, and
``api/app.py::_prepare_database`` seeds whenever ``HARDWOOD_DEMO_MODE`` is on and ``teams`` is
empty. An injury snapshot, a pasted headline link, a model setting the user changed or the
scheduler's job state living on ``Base`` would be erased by a re-seed with no log line anyone
would think to read. On ``NbaIntelBase`` they are invisible to ``_clear()``, to
``_INSERT_ORDER`` and to ``render_schema_sql()``, so none of that can happen.
``tests/test_nba_intel_schema.py`` proves it empirically; it is the most important test in this
package.

What belongs here, and what does not
------------------------------------
These tables hold facts about *availability*, sources and the user's own entries: what the
league's injury report said and when, what a person overrode, which headlines were seen, which
model constants were set. They are **not** stats. Stats-derived and schedule facts (positions,
tip-off times, the projection ledger) are rows about games, belong with the games, and are rightly
wiped and rebuilt with them; those live on ``Base`` (``nbastats/models.py``, another package's
file).

The same separation has a second use: the synthetic demo league must never sit next to real
injury statuses. ``store.store_is_synthetic`` asks the *games* table, by content, whether the
store holds the demo league, and the injury job refuses to write if it does. Because the intel
tables survive a re-seed, that guard cannot be skipped by re-seeding a live file.

Consequences this module is built around
----------------------------------------
* **No foreign key crosses to the stats tables.** A string FK cannot resolve across two
  ``MetaData`` objects, and SQLite here does not enforce foreign keys anyway (see ``db.py``).
  ``team_id``, ``player_id`` and ``game_id`` are plain columns that name rows in the stats store;
  the code that writes them checks them against it and the code that reads them tolerates a
  stale one. Keys *within* this schema (a status row's snapshot, a headline's feed) are declared
  normally.
* **No ``ALTER`` and no migration tool.** ``create_all`` adds missing tables and never changes
  one. Every column that could plausibly move house is nullable, and ``detail_json`` and
  ``cursor_json`` are text escape hatches so most future fields need no DDL.
* **NULL means not recorded.** A status that was not stated is ``NULL`` (an em dash on screen),
  never ``'available'``; ``expected_return_*`` is ``NULL`` unless the source said it. The
  workbook's ``IFERROR(...,1)`` behaviour (an unknown status silently counting as healthy) is not
  ported: the ``status`` column is a CHECK-constrained vocabulary and an unknown string is refused
  by the database as well as by :mod:`nbastats.shared.availability`.
* **Status rows are append-only.** A correction is a new row, a retraction is a new row that names
  the one it retracts, and history is never rewritten; an ORM-level guard refuses an ``UPDATE``
  of a status row (a bulk ``DELETE`` can still bypass it, which is why nothing in the codebase
  issues one). Every status row carries its source and the source's own publication time, and
  "how old is this?" is always measured from that time, never from the fetch.
* **Dates and times are naive UTC**, like every other timestamp column in Hardwood, and a
  ``Date`` column is a calendar day in US Eastern where it names an NBA game day.
* **Terms columns are gone.** The first design carried ``terms_reviewed_on`` and ``terms_url`` on
  the feed table and a CHECK that no feed could be enabled without them. The lead removed the
  terms gate. What remains is the automatic ``robots.txt`` check, whose last verdict is recorded
  on the feed row so ``/v1/sources`` can show why a feed is off.
* **No gambling vocabulary anywhere**: no column or constraint name here is a line, a price, an
  edge or a probability of beating a number, and the model-setting allowlist has no key for any
  of them (``tests/test_nba_intel_schema.py`` scans the names).

The CHECK allowlists below are written out as literals, not imported from
``nbastats.shared.availability``, so that the generated DDL cannot change because another
package edited a tuple; a test asserts the two agree and fails loudly if they ever drift.

``nbastats/nba_intel/schema.sql`` is the committed, generated DDL. Regenerate it with
``python3 -m nbastats.nba_intel.models > nbastats/nba_intel/schema.sql``; the drift test fails the
build if it is stale, as it does for the stats and account schemas.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

__all__ = [
    "NbaIntelBase",
    "NbaIntelSnapshot",
    "NbaIntelTeamReport",
    "NbaIntelStatus",
    "NbaIntelOverride",
    "NbaIntelNewsFeed",
    "NbaIntelNewsItem",
    "NbaIntelNewsSubject",
    "NbaIntelSourceState",
    "NbaIntelJobState",
    "NbaIntelModelSetting",
    "NbaIntelRawFetch",
    "AppendOnlyError",
    "NBA_INTEL_TABLE_NAMES",
    "NO_GAME",
    "LINK_WITHHELD_MARKER",
    "SNAPSHOT_SOURCE_KINDS",
    "PARSE_STATUSES",
    "TEAM_REPORT_STATES",
    "STATUS_VALUES",
    "REASON_CATEGORY_VALUES",
    "SOURCE_KIND_VALUES",
    "SOURCE_STATE_VALUES",
    "ROBOTS_STATES",
    "MODEL_SETTING_KEYS",
    "SETTING_PROVENANCES",
    "render_schema_sql",
]


class NbaIntelBase(DeclarativeBase):
    """Declarative base for every NBA intel table.

    A distinct class, and therefore a distinct ``MetaData``, from :class:`nbastats.models.Base`;
    see the module docstring for why that separation is load-bearing.
    """


# --------------------------------------------------------------------------- vocabularies

#: ``nba_intel_snapshot.source_kind``: where a whole snapshot came from.
SNAPSHOT_SOURCE_KINDS = ("leagueReport", "workbookImport", "manual")

#: ``nba_intel_snapshot.parse_status``. ``ok``: every line understood. ``partial``: some lines
#: dropped (listed in ``parse_error``). ``headerMismatch``: the table's header was not the seven
#: expected names (or the file could not be read as a report), so there are no entries.
#: ``empty``: understood, nothing in it. ``fetchFailed``: no usable bytes. ``notFound``: the
#: slot does not exist (normal, and rarely stored).
PARSE_STATUSES = ("ok", "partial", "headerMismatch", "empty", "fetchFailed", "notFound")

TEAM_REPORT_STATES = ("submitted", "notYetSubmitted")

STATUS_VALUES = ("out", "doubtful", "questionable", "probable", "available")

REASON_CATEGORY_VALUES = (
    "injury",
    "illness",
    "rest",
    "coachDecision",
    "personal",
    "suspension",
    "gLeague",
    "notWithTeam",
    "notRegistered",
    "other",
)

SOURCE_KIND_VALUES = (
    "leagueReport",
    "clubStatement",
    "pressArticle",
    "boxScoreInference",
    "workbookImport",
    "manual",
)

#: ``nba_intel_source_state.state``, the ``/v1/sources`` vocabulary after the terms gate was
#: removed. ``stale`` and ``noReportYet`` are computed by the read side as well as stored.
SOURCE_STATE_VALUES = (
    "ok",
    "stale",
    "disabled",
    "notConfigured",
    "noReportYet",
    "blocked",
    "unreadable",
    "error",
)

#: ``nba_intel_news_feed.robots_state``: the last ``robots.txt`` verdict for the feed's host.
ROBOTS_STATES = ("allowed", "disallowed", "unavailable")

#: ``nba_intel_model_setting.key``: the design's §8.6 allowlist for the NBA model, and nothing
#: else. There is deliberately no key for a total spread, a player spread, a threshold on a
#: probability, or any number that exists to be compared with an outside price.
MODEL_SETTING_KEYS = (
    "priorRegression",
    "priorWeightGames",
    "leagueLevelWeight",
    "homeAdvantagePoints",
    "teamSd",
    "marginSd",
    "replacementShare",
    "absorbShare",
    "boostCap",
    "capPolicyConsistent",
    "statusChance.out",
    "statusChance.doubtful",
    "statusChance.questionable",
    "statusChance.probable",
    "statusChance.available",
    "positionCoverageCeiling",
)

SETTING_PROVENANCES = ("default", "fittedPrevSeason", "fittedLedger", "manual")

#: ``nba_intel_team_report.game_id`` when the report names a game the stats store does not hold.
#: A primary-key column cannot be NULL, so "no game" is the empty string, never a guess.
NO_GAME = ""

#: What a ``source_label`` contains when the source's link was withheld because the host is on the
#: source denylist (design §4.6). The full suffix text lives in ``data/source_denylist.json`` and
#: is applied by :mod:`nbastats.nba_intel.status`; the ``source_url`` CHECK needs only this
#: prefix to recognise it. The design's two statements ("NULL only for a manual entry" and "a
#: denylisted link is stored NULL with a suffixed label") only agree if the second is a stated
#: exception to the first, so the CHECK states it.
LINK_WITHHELD_MARKER = "(link withheld:"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _nullable_in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IS NULL OR {_in(column, values)}"


class AppendOnlyError(RuntimeError):
    """An attempt to update a row of an append-only table."""


# ------------------------------------------------------------------------------- snapshot


class NbaIntelSnapshot(NbaIntelBase):
    """One fetch of the league's injury report (or one import, or one manual batch).

    ``slot_at_utc`` is the quarter-hour slot the URL named; ``report_as_of_utc`` is the time the
    report says it is current to (the same, unless the document's own header disagrees and is
    readable). ``sha256`` is the file's hash, so an unchanged re-fetch is skipped. A failed parse
    still gets a row, with ``parse_status`` and ``parse_error`` saying why, so "unreadable" is a
    recorded fact with a date rather than a silence.
    """

    __tablename__ = "nba_intel_snapshot"
    __table_args__ = (
        CheckConstraint(
            _in("source_kind", SNAPSHOT_SOURCE_KINDS), name="ck_nba_intel_snapshot_kind"
        ),
        CheckConstraint(_in("parse_status", PARSE_STATUSES), name="ck_nba_intel_snapshot_parse"),
        Index("ix_nba_intel_snapshot_slot", "slot_at_utc"),
        Index("ix_nba_intel_snapshot_sha", "sha256"),
        Index("ix_nba_intel_snapshot_url", "url"),
    )

    snapshot_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    slot_at_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    report_as_of_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    parse_status: Mapped[str] = mapped_column(String(16), nullable=False)
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class NbaIntelTeamReport(NbaIntelBase):
    """Whether a team had filed its report in a snapshot, for one game.

    ``notYetSubmitted`` is the league's own "NOT YET SUBMITTED" marker and is kept distinct from
    ``submitted``: the first means "we do not know who is out", the second means "the team has
    said who is out, and a player not listed is a player the team did not list". A team with no
    row at all is ``noReport``, which is a third thing and is computed, not stored.
    """

    __tablename__ = "nba_intel_team_report"
    __table_args__ = (
        CheckConstraint(_in("state", TEAM_REPORT_STATES), name="ck_nba_intel_team_report_state"),
        Index("ix_nba_intel_team_report_team", "team_id"),
    )

    snapshot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("nba_intel_snapshot.snapshot_id"), primary_key=True
    )
    team_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[str] = mapped_column(String(16), primary_key=True, default=NO_GAME)
    state: Mapped[str] = mapped_column(String(16), nullable=False)


# --------------------------------------------------------------------------------- status


class NbaIntelStatus(NbaIntelBase):
    """One sourced, dated statement about one player's availability. Append-only.

    ``player_id`` is NULL when the report's name could not be matched to exactly one player; the
    row then waits in the review queue and the player is never guessed. ``status`` is NULL when
    the source gave none. ``model_status`` is the workbook's separate "In model" column, unused by
    the NBA report. ``reason_text`` is the league report's own field, never article text.
    ``source_published_at`` is when the *source* said this, and every age shown to a person is
    measured from it.

    ``source_url`` is NULL only for a manual entry, or for a link withheld because it points at a
    host on the source denylist (the label then carries :data:`LINK_WITHHELD_MARKER`).
    """

    __tablename__ = "nba_intel_status"
    __table_args__ = (
        CheckConstraint(_nullable_in("status", STATUS_VALUES), name="ck_nba_intel_status_status"),
        CheckConstraint(
            _nullable_in("model_status", STATUS_VALUES), name="ck_nba_intel_status_model_status"
        ),
        CheckConstraint(
            _nullable_in("reason_category", REASON_CATEGORY_VALUES),
            name="ck_nba_intel_status_reason_category",
        ),
        CheckConstraint(_in("source_kind", SOURCE_KIND_VALUES), name="ck_nba_intel_status_kind"),
        CheckConstraint(
            "source_url IS NOT NULL OR source_kind = 'manual' "
            f"OR source_label LIKE '%{LINK_WITHHELD_MARKER}%'",
            name="ck_nba_intel_status_source_url",
        ),
        Index("ix_nba_intel_status_team_date", "team_id", "game_date"),
        Index("ix_nba_intel_status_player", "player_id"),
        Index("ix_nba_intel_status_snapshot", "snapshot_id"),
        Index("ix_nba_intel_status_published", "source_published_at"),
    )

    status_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    player_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    player_name_raw: Mapped[str] = mapped_column(String(96), nullable=False)
    game_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    game_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str | None] = mapped_column(String(12), nullable=True)
    status_raw: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model_status: Mapped[str | None] = mapped_column(String(12), nullable=True)
    reason_category: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    expected_return_text: Mapped[str | None] = mapped_column(String(120), nullable=True)
    expected_return_round_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_return_round_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_return_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    source_label: Mapped[str] = mapped_column(String(120), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    snapshot_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("nba_intel_snapshot.snapshot_id"), nullable=True
    )
    #: Account ids are strings (``users.user_id``), so this is not the design's integer.
    entered_by_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    retracts_status_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provenance_note: Mapped[str | None] = mapped_column(String(200), nullable=True)


@event.listens_for(NbaIntelStatus, "before_update")
def _status_rows_are_append_only(mapper: Any, connection: Any, target: NbaIntelStatus) -> None:
    raise AppendOnlyError(
        "nba_intel_status is append-only: add a new row (or a retraction), never update one"
    )


class NbaIntelOverride(NbaIntelBase):
    """A status a person typed in for a player, in force until they clear it or a newer league
    report arrives (the selection rule lives in :mod:`nbastats.shared.availability`).

    Unlike a status row an override is edited in exactly one way, by setting ``cleared_at``.
    """

    __tablename__ = "nba_intel_override"
    __table_args__ = (
        CheckConstraint(_in("status", STATUS_VALUES), name="ck_nba_intel_override_status"),
        Index("ix_nba_intel_override_player", "player_id"),
    )

    override_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(Integer, nullable=False)
    team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    game_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    entered_by_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


# ----------------------------------------------------------------------------------- news


class NbaIntelNewsFeed(NbaIntelBase):
    """A configured headline feed. ``enabled`` is the person's switch (default on).

    ``robots_state``, ``robots_checked_on`` and ``robots_reason`` record the last ``robots.txt``
    verdict. A disallow switches the feed off (``enabled`` is cleared and ``robots_state`` is
    ``disallowed``) and the reason is shown in ``/v1/sources``; the daily re-check turns a feed
    back on *only* if it was switched off by that disallow, never one the person switched off.
    ``etag`` and ``last_modified`` are the validators for conditional requests.
    """

    __tablename__ = "nba_intel_news_feed"
    __table_args__ = (
        CheckConstraint(
            _nullable_in("robots_state", ROBOTS_STATES), name="ck_nba_intel_news_feed_robots"
        ),
    )

    feed_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    robots_checked_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    robots_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    robots_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(40), nullable=True)


class NbaIntelNewsItem(NbaIntelBase):
    """One headline: a title, a link, a publication date and the source's name. That is all.

    There is no description, summary or body column, on purpose. ``feed_id`` is NULL for a link a
    person pasted. Feed items are kept 30 days; pasted links are kept.
    """

    __tablename__ = "nba_intel_news_item"
    __table_args__ = (
        UniqueConstraint("feed_id", "guid", name="uq_nba_intel_news_item_feed_guid"),
        Index("ix_nba_intel_news_item_published", "published_at"),
        Index("ix_nba_intel_news_item_link", "link"),
    )

    item_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feed_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("nba_intel_news_feed.feed_id"), nullable=True
    )
    guid: Mapped[str | None] = mapped_column(String(512), nullable=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    link: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    source_name: Mapped[str] = mapped_column(String(80), nullable=False)


class NbaIntelNewsSubject(NbaIntelBase):
    """A team (and optionally a player) a headline is about. ``player_id`` is 0 for a team-only
    link, because it is part of the primary key. A name that is ambiguous within the NBA store
    produces no row at all."""

    __tablename__ = "nba_intel_news_subject"
    __table_args__ = (
        Index("ix_nba_intel_news_subject_team", "team_id"),
        Index("ix_nba_intel_news_subject_player", "player_id"),
    )

    item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("nba_intel_news_item.item_id"), primary_key=True
    )
    team_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(Integer, primary_key=True, default=0)


# ------------------------------------------------------------------------- operations


class NbaIntelSourceState(NbaIntelBase):
    """The standing of one source, for ``/v1/sources``: ``nba.injuryReport``,
    ``nba.news.<feedId>`` and so on. ``paused_until`` persists a circuit breaker across restarts.
    ``detail_json`` holds source-specific facts, such as the injury parser's confirmation counter
    (how many real reports it has parsed successfully on this machine)."""

    __tablename__ = "nba_intel_source_state"
    __table_args__ = (
        CheckConstraint(_in("state", SOURCE_STATE_VALUES), name="ck_nba_intel_source_state_state"),
    )

    source_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    paused_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class NbaIntelJobState(NbaIntelBase):
    """The scheduler's memory of one job. The worker (``nbastats/worker.py``) writes
    ``last_started_at``, ``last_success_at`` and ``last_error`` and never touches
    ``cursor_json``; the job owns that column (the newest injury slot it fetched, say)."""

    __tablename__ = "nba_intel_job_state"

    job_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class NbaIntelModelSetting(NbaIntelBase):
    """A model constant the user or a fit has set. Absent means "the default applies".

    The ``key`` CHECK is the allowlist in :data:`MODEL_SETTING_KEYS`; a key not on it cannot be
    stored by any code path, because the database refuses it.
    """

    __tablename__ = "nba_intel_model_setting"
    __table_args__ = (
        CheckConstraint(_in("key", MODEL_SETTING_KEYS), name="ck_nba_intel_model_setting_key"),
        CheckConstraint(
            _in("provenance", SETTING_PROVENANCES), name="ck_nba_intel_model_setting_provenance"
        ),
    )

    key: Mapped[str] = mapped_column(String(48), primary_key=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    provenance: Mapped[str] = mapped_column(String(20), nullable=False)
    set_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class NbaIntelRawFetch(NbaIntelBase):
    """A raw payload kept for provenance: ``path`` is under ``HARDWOOD_DATA_DIR/raw`` and is never
    served by any route. Lets a parser fix re-read exactly what the server sent."""

    __tablename__ = "nba_intel_raw_fetch"
    __table_args__ = (Index("ix_nba_intel_raw_fetch_sha", "sha256"),)

    fetch_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    path: Mapped[str | None] = mapped_column(Text, nullable=True)


#: Every table name on :data:`NbaIntelBase.metadata`. Tests assert it is disjoint from the stats
#: metadata, that each name starts with ``nba_intel_``, and that ``init_db()`` creates them all.
NBA_INTEL_TABLE_NAMES = frozenset(table.name for table in NbaIntelBase.metadata.sorted_tables)


def render_schema_sql() -> str:
    """Emit this schema as SQLite DDL, the source of ``nbastats/nba_intel/schema.sql``.

    Mirrors :func:`nbastats.accounts.models.render_schema_sql`, against
    ``NbaIntelBase.metadata``, so the three generated files stay in one shape.
    """
    from sqlalchemy.dialects import sqlite
    from sqlalchemy.schema import CreateIndex, CreateTable

    dialect = sqlite.dialect()
    lines: list[str] = [
        "-- Hardwood NBA intel schema, generated by nbastats.nba_intel.models.render_schema_sql().",
        "-- Do not edit by hand: run",
        "-- `python3 -m nbastats.nba_intel.models > nbastats/nba_intel/schema.sql`.",
        "-- Dialect: SQLite. These tables live on their own MetaData (NbaIntelBase), in the same",
        "-- file as the stats store but never wiped by the seeder; see nba_intel/models.py.",
        "",
    ]
    for table in NbaIntelBase.metadata.sorted_tables:
        lines.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
        lines.append("")
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            lines.append(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")
        if table.indexes:
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":  # pragma: no cover - developer utility
    print(render_schema_sql(), end="")
