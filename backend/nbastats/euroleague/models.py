"""SQLAlchemy 2.0 declarative models for the EuroLeague store: ``ElBase`` and every ``el_*`` table.

``ElBase`` is a declarative base of its own, with its own ``MetaData``, in a database file of
its own. That is the whole point and not a tidiness preference: see the ``nbastats.euroleague``
package docstring for why nothing is shared with the NBA, and ``nbastats/accounts/models.py``
for the precedent (``AccountBase``) this follows. ``Base.metadata.sorted_tables`` never lists
these tables, so the NBA seeder's unconditional ``_clear()``, its ``_INSERT_ORDER`` and the
generated ``nbastats/schema.sql`` cannot see, wipe or describe them.

``nbastats/euroleague/schema.sql`` is the committed, generated DDL for exactly the tables
below. Regenerate it with ``python3 -m nbastats.euroleague.models > nbastats/euroleague/
schema.sql`` after any change here; ``tests/euroleague/test_schema.py`` fails the build if the
two drift, the same discipline the stats and account schemas have.

The null rule, in the schema
----------------------------
Every column is nullable unless the design says ``NN``, and NULL means "not recorded", which a
client renders as an em dash. It is never 0. The workbook's box scores carry no fouls drawn,
no blocks against, no plus/minus and no starter flag; those columns are NULL for such games,
and an average of one of them divides by the count of rows that actually carry it. A player
who did not play (``participation = 'dnp'``) has every stat column NULL, and a CHECK enforces
it, because a dozen zeros for a man who never stepped on the floor would drag every average
down. A percentage whose attempt count is zero is NULL (the workbook hard-codes 0 for a
zero-attempt shooter; the importer does not copy that).

Constraints that make the invariants structural
-----------------------------------------------
Statuses, phases, kinds and provenance labels are CHECK-constrained to their vocabularies; a
player's makes cannot exceed his attempts and no count is negative; a final game has both
scores; a scheduled game has no stats. A status entry must carry a link unless it is a manual
entry, or the link was withheld because it pointed at a gambling operator (the label then says
so). The model-setting key is constrained to the allowlist of ``settings.py``, which has no key
for a line, a threshold, a spread of a total or anything else that exists to be compared with a
projection. There is no terms column anywhere: the terms gate was removed (``LEGAL.md``).

Foreign keys are declared and **not enforced at run time** (``PRAGMA foreign_keys`` stays off,
as in the NBA store): reconciliation re-keys people and games in one transaction, and
declared-but-unenforced keys document the relations and order the DDL without making that
re-keying fight the engine. ``tests/euroleague`` checks integrity with ``PRAGMA
foreign_key_check``, which works whatever the pragma says.

Timestamps are naive UTC and are set by the caller (``db.utcnow()``), never by a column
default. ``game_date`` is the day in Europe/Berlin, where the competition keeps its schedule.
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
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..shared.availability import REASON_CATEGORIES, SOURCE_KINDS, STATUSES
from .profile import (
    GAME_STATUSES,
    PARTICIPATION,
    PHASES,
    POSITION5,
    STATS_STATUSES,
    STORE_KINDS,
    WITHHELD_SUFFIX,
)

__all__ = [
    "ElBase",
    "ElStoreIdentity",
    "ElSeason",
    "ElClub",
    "ElClubAlias",
    "ElClubSeason",
    "ElPerson",
    "ElPersonAlias",
    "ElRegistration",
    "ElGame",
    "ElPlayerGame",
    "ElTeamGame",
    "ElTeamRating",
    "ElPlayerRate",
    "ElModelSetting",
    "ElProjectionLedger",
    "ElIntelBatch",
    "ElIntelStatus",
    "ElIntelOverride",
    "ElIntelNewsFeed",
    "ElIntelNewsItem",
    "ElIntelNewsSubject",
    "ElRawPayload",
    "ElIngestLog",
    "ElJobState",
    "ElSourceState",
    "ElSyncState",
    "EL_TABLE_NAMES",
    "ALIAS_SYSTEMS",
    "CODE_SYSTEMS",
    "LEDGER_KINDS",
    "CAP_POLICIES",
    "RATING_SOURCES",
    "UPDATE_BASES",
    "RATE_BASES",
    "SETTING_PROVENANCES",
    "SYNC_MODES",
    "MODEL_SETTING_KEYS",
    "STAT_COLUMNS",
    "COUNT_COLUMNS",
    "WITHHELD_LABEL_SUFFIX",
    "render_schema_sql",
]

ALIAS_SYSTEMS = ("official", "tv", "workbook")
CODE_SYSTEMS = ("official", "workbook", "demo")
LEDGER_KINDS = ("latest", "locked", "reconstructed", "imported")
CAP_POLICIES = ("consistent", "workbook")
RATING_SOURCES = ("workbookImport", "roundUpdate", "manual", "syntheticDemo")
UPDATE_BASES = ("locked", "reconstructed")
RATE_BASES = (
    "workbookOfficial",
    "workbookEstimate",
    "positionPrior",
    "officialUpdate",
    "syntheticDemo",
)
SETTING_PROVENANCES = ("workbook", "workbookUnvalidated", "default", "fittedLedger", "manual")
SYNC_MODES = ("demo", "workbook", "live")

#: The suffix a status's label carries when its link was withheld (a gambling operator's site).
#: It is read from ``data/source_denylist.json``, beside the domains it is about.
WITHHELD_LABEL_SUFFIX = WITHHELD_SUFFIX

#: Every key ``el_model_setting`` may hold (design section 8.6). There is deliberately no key
#: for ``TotalSD``, ``PSDBase``, ``PSDSlope``, ``EdgeP``, a line, or a probability threshold.
MODEL_SETTING_KEYS: tuple[str, ...] = (
    "priorRegression",
    "homeAdvantagePoints",
    "teamSd",
    "marginSd",
    "replacementPer40",
    "absorbShare",
    "boostCap",
    "rotationShare",
    "formWeight",
    "capPolicyConsistent",
    "squadReconcile",
    "overtimeScaling",
    *(f"roundWeight.{n}" for n in range(1, 41)),
    *(f"statusChance.{status}" for status in STATUSES),
    "positionCoverageCeiling",
)

#: The box-score columns a player line and a team line share. All nullable; NULL is "not
#: recorded". ``seconds_played`` is whole seconds.
COUNT_COLUMNS: tuple[str, ...] = (
    "seconds_played",
    "pts",
    "fgm2",
    "fga2",
    "fgm3",
    "fga3",
    "ftm",
    "fta",
    "oreb",
    "dreb",
    "reb",
    "ast",
    "stl",
    "tov",
    "blk",
    "blk_against",
    "pf",
    "fouls_drawn",
)
STAT_COLUMNS: tuple[str, ...] = (*COUNT_COLUMNS, "plus_minus", "pir_official")


class ElBase(DeclarativeBase):
    """Declarative base for every EuroLeague table (``el_*``); its own ``MetaData``."""


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _num() -> Any:
    """A nullable float column."""
    return mapped_column(Float, nullable=True)


def _count() -> Any:
    """A nullable integer count: ``NULL`` when the source did not record it."""
    return mapped_column(Integer, nullable=True)


def _line_checks(table: str) -> list[CheckConstraint]:
    """Non-negative counts, and makes never above attempts, for a player or team line."""
    checks = [
        CheckConstraint(f"{c} IS NULL OR {c} >= 0", name=f"ck_{table}_{c}_nonneg")
        for c in COUNT_COLUMNS
    ]
    for made, attempted in (("fgm2", "fga2"), ("fgm3", "fga3"), ("ftm", "fta")):
        checks.append(
            CheckConstraint(
                f"{made} IS NULL OR {attempted} IS NULL OR {made} <= {attempted}",
                name=f"ck_{table}_{made}_le_{attempted}",
            )
        )
    return checks


# --------------------------------------------------------------------------- identity


class ElStoreIdentity(ElBase):
    """The store's stamp: which league it holds and whether the rows are real or invented.

    A singleton (``id = 1``). ``kind`` is set the day the file is created and decides what may
    be written: a ``synthetic`` store takes only demo seeding; ``workbook`` and ``live`` stores
    (both real) take workbook imports and live ingest. There are no terms columns: the terms
    gate was removed.
    """

    __tablename__ = "el_store_identity"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_el_store_identity_singleton"),
        CheckConstraint("league = 'euroleague'", name="ck_el_store_identity_league"),
        CheckConstraint(_in("kind", STORE_KINDS), name="ck_el_store_identity_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    league: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


# --------------------------------------------------------------------------- reference


class ElSeason(ElBase):
    __tablename__ = "el_season"

    season_code: Mapped[str] = mapped_column(String(8), primary_key=True)
    competition_code: Mapped[str] = mapped_column(String(4), nullable=False)
    label: Mapped[str] = mapped_column(String(8), nullable=False)
    start_year: Mapped[int] = mapped_column(Integer, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False)


class ElClub(ElBase):
    """A club, keyed by its official code (``PAN``)."""

    __tablename__ = "el_club"

    club_code: Mapped[str] = mapped_column(String(8), primary_key=True)
    tv_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    name: Mapped[str] = mapped_column(String(96), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(48), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(3), nullable=True)


class ElClubAlias(ElBase):
    """Another system's code for a club. Lookups always go by ``(system, code)``.

    The workbook's ``PAR`` is Paris while the official ``PAR`` is believed to be Partizan, so
    a lookup by the bare code would silently cross two clubs.
    """

    __tablename__ = "el_club_alias"
    __table_args__ = (
        CheckConstraint(_in("system", ALIAS_SYSTEMS), name="ck_el_club_alias_system"),
    )

    system: Mapped[str] = mapped_column(String(10), primary_key=True)
    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    club_code: Mapped[str] = mapped_column(ForeignKey("el_club.club_code"), nullable=False)


class ElClubSeason(ElBase):
    __tablename__ = "el_club_season"

    season_code: Mapped[str] = mapped_column(ForeignKey("el_season.season_code"), primary_key=True)
    club_code: Mapped[str] = mapped_column(ForeignKey("el_club.club_code"), primary_key=True)
    coach_name: Mapped[str | None] = mapped_column(String(96), nullable=True)
    home_venue_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    home_venue_tz: Mapped[str | None] = mapped_column(String(48), nullable=True)


class ElPerson(ElBase):
    """A player (staff are never stored).

    ``person_code`` is the official code, ``wb-<8hex>`` (minted by the workbook importer) or
    ``demo-<n>`` (the invented league).
    """

    __tablename__ = "el_person"
    __table_args__ = (
        CheckConstraint(_in("code_system", CODE_SYSTEMS), name="ck_el_person_system"),
    )

    person_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    code_system: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(96), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(96), nullable=True)
    jersey_name: Mapped[str | None] = mapped_column(String(48), nullable=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    height_cm: Mapped[int | None] = _count()
    weight_kg: Mapped[int | None] = _count()
    country_code: Mapped[str | None] = mapped_column(String(3), nullable=True)


class ElPersonAlias(ElBase):
    """A workbook-minted person mapped to the official code that replaced it."""

    __tablename__ = "el_person_alias"
    __table_args__ = (
        CheckConstraint("matched_by IN ('clubAndName', 'manual')", name="ck_el_person_alias_by"),
    )

    wb_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    official_code: Mapped[str] = mapped_column(String(16), nullable=False)
    matched_by: Mapped[str] = mapped_column(String(12), nullable=False)
    matched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ElRegistration(ElBase):
    """A player on a club's squad for a season. Staff rows (``type <> 'J'``) are never stored.

    ``position_code`` is the official three-way registration (1 guard, 2 forward, 3 center);
    ``position5_workbook`` is the workbook author's five-way label, an estimate. A workbook
    import knows only the second, so ``position_code`` stays NULL until the service says.
    """

    __tablename__ = "el_registration"
    __table_args__ = (
        CheckConstraint(
            "position_code IS NULL OR position_code IN (1, 2, 3)", name="ck_el_registration_pos"
        ),
        CheckConstraint(
            f"position5_workbook IS NULL OR {_in('position5_workbook', POSITION5)}",
            name="ck_el_registration_pos5",
        ),
        Index("ix_el_registration_person", "person_code"),
    )

    season_code: Mapped[str] = mapped_column(ForeignKey("el_season.season_code"), primary_key=True)
    club_code: Mapped[str] = mapped_column(ForeignKey("el_club.club_code"), primary_key=True)
    person_code: Mapped[str] = mapped_column(ForeignKey("el_person.person_code"), primary_key=True)
    dorsal: Mapped[str | None] = mapped_column(String(4), nullable=True)
    position_code: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    position_name: Mapped[str | None] = mapped_column(String(24), nullable=True)
    position5_workbook: Mapped[str | None] = mapped_column(String(2), nullable=True)
    role_workbook: Mapped[str | None] = mapped_column(String(24), nullable=True)
    age_workbook: Mapped[int | None] = _count()
    active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)


# --------------------------------------------------------------------------- games


class ElGame(ElBase):
    """A game, scheduled or final. ``game_date`` is the Europe/Berlin day.

    ``is_neutral`` is NULL when the venue is unknown and is never assumed False.
    ``stats_status`` says whether box-score lines exist and passed the hard invariants.
    ``data_source`` is ``euroleague-v2``, ``workbook:<sha8>`` or ``synthetic-demo``; a workbook
    never overwrites a ``euroleague-v2`` row.
    """

    __tablename__ = "el_game"
    __table_args__ = (
        UniqueConstraint("season_code", "game_code", name="uq_el_game_season_code"),
        CheckConstraint(_in("phase_code", PHASES), name="ck_el_game_phase"),
        CheckConstraint(_in("status", GAME_STATUSES), name="ck_el_game_status"),
        CheckConstraint(_in("stats_status", STATS_STATUSES), name="ck_el_game_stats_status"),
        CheckConstraint("round_number >= 1", name="ck_el_game_round"),
        CheckConstraint("home_club_code <> away_club_code", name="ck_el_game_distinct_clubs"),
        CheckConstraint(
            "status <> 'final' OR (home_pts IS NOT NULL AND away_pts IS NOT NULL)",
            name="ck_el_game_final_has_score",
        ),
        CheckConstraint(
            "status = 'final' OR stats_status = 'none'", name="ck_el_game_stats_need_final"
        ),
        CheckConstraint("ot_periods IS NULL OR ot_periods >= 0", name="ck_el_game_ot"),
        CheckConstraint("attendance IS NULL OR attendance >= 0", name="ck_el_game_attendance"),
        CheckConstraint(
            "data_source = 'euroleague-v2' OR data_source = 'synthetic-demo' "
            "OR data_source LIKE 'workbook:%'",
            name="ck_el_game_data_source",
        ),
        Index("ix_el_game_season_round", "season_code", "round_number"),
        Index("ix_el_game_season_date", "season_code", "game_date"),
        Index("ix_el_game_home", "home_club_code"),
        Index("ix_el_game_away", "away_club_code"),
    )

    game_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    season_code: Mapped[str] = mapped_column(ForeignKey("el_season.season_code"), nullable=False)
    game_code: Mapped[int | None] = _count()
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    phase_code: Mapped[str] = mapped_column(String(2), nullable=False)
    game_date: Mapped[date] = mapped_column(Date, nullable=False)
    tipoff_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    venue_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    venue_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_neutral: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    home_advantage_override: Mapped[float | None] = _num()
    home_club_code: Mapped[str] = mapped_column(ForeignKey("el_club.club_code"), nullable=False)
    away_club_code: Mapped[str] = mapped_column(ForeignKey("el_club.club_code"), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    home_pts: Mapped[int | None] = _count()
    away_pts: Mapped[int | None] = _count()
    home_partials_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    away_partials_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    ot_periods: Mapped[int | None] = _count()
    attendance: Mapped[int | None] = _count()
    stats_status: Mapped[str] = mapped_column(String(12), nullable=False)
    raw_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_source: Mapped[str] = mapped_column(String(24), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ElPlayerGame(ElBase):
    """One player's line in one game. A player not dressed has no row.

    For ``dnp`` every stat column is NULL (a CHECK enforces it). Workbook-imported games carry
    NULL ``fouls_drawn``, ``blk_against``, ``plus_minus`` and ``is_starter``; an average of
    one of those divides by the rows that carry it, so those games never pull it toward 0.
    """

    __tablename__ = "el_player_game"
    __table_args__ = (
        CheckConstraint(_in("participation", PARTICIPATION), name="ck_el_player_game_part"),
        CheckConstraint(
            "participation = 'played' OR ("
            + " AND ".join(f"{c} IS NULL" for c in STAT_COLUMNS)
            + ")",
            name="ck_el_player_game_dnp_null",
        ),
        *_line_checks("el_player_game"),
        Index("ix_el_player_game_person", "person_code"),
        Index("ix_el_player_game_club", "club_code"),
    )

    game_id: Mapped[str] = mapped_column(ForeignKey("el_game.game_id"), primary_key=True)
    person_code: Mapped[str] = mapped_column(ForeignKey("el_person.person_code"), primary_key=True)
    club_code: Mapped[str] = mapped_column(ForeignKey("el_club.club_code"), nullable=False)
    participation: Mapped[str] = mapped_column(String(8), nullable=False)
    is_starter: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    seconds_played: Mapped[int | None] = _count()
    pts: Mapped[int | None] = _count()
    fgm2: Mapped[int | None] = _count()
    fga2: Mapped[int | None] = _count()
    fgm3: Mapped[int | None] = _count()
    fga3: Mapped[int | None] = _count()
    ftm: Mapped[int | None] = _count()
    fta: Mapped[int | None] = _count()
    oreb: Mapped[int | None] = _count()
    dreb: Mapped[int | None] = _count()
    reb: Mapped[int | None] = _count()
    ast: Mapped[int | None] = _count()
    stl: Mapped[int | None] = _count()
    tov: Mapped[int | None] = _count()
    blk: Mapped[int | None] = _count()
    blk_against: Mapped[int | None] = _count()
    pf: Mapped[int | None] = _count()
    fouls_drawn: Mapped[int | None] = _count()
    plus_minus: Mapped[int | None] = _count()
    pir_official: Mapped[int | None] = _count()
    position_code_at_game: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    dorsal: Mapped[str | None] = mapped_column(String(4), nullable=True)


class ElTeamGame(ElBase):
    """One club's totals in one game. Team rebounds and turnovers are included, so a team's
    ``reb`` and ``tov`` can exceed the sum of its players'."""

    __tablename__ = "el_team_game"
    __table_args__ = (
        CheckConstraint("pts >= 0 AND opp_pts >= 0", name="ck_el_team_game_scores"),
        *_line_checks("el_team_game"),
        Index("ix_el_team_game_club", "club_code"),
    )

    game_id: Mapped[str] = mapped_column(ForeignKey("el_game.game_id"), primary_key=True)
    club_code: Mapped[str] = mapped_column(ForeignKey("el_club.club_code"), primary_key=True)
    is_home: Mapped[bool] = mapped_column(Boolean, nullable=False)
    opp_club_code: Mapped[str] = mapped_column(ForeignKey("el_club.club_code"), nullable=False)
    won: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    seconds_played: Mapped[int | None] = _count()
    pts: Mapped[int] = mapped_column(Integer, nullable=False)
    opp_pts: Mapped[int] = mapped_column(Integer, nullable=False)
    fgm2: Mapped[int | None] = _count()
    fga2: Mapped[int | None] = _count()
    fgm3: Mapped[int | None] = _count()
    fga3: Mapped[int | None] = _count()
    ftm: Mapped[int | None] = _count()
    fta: Mapped[int | None] = _count()
    oreb: Mapped[int | None] = _count()
    dreb: Mapped[int | None] = _count()
    reb: Mapped[int | None] = _count()
    ast: Mapped[int | None] = _count()
    stl: Mapped[int | None] = _count()
    tov: Mapped[int | None] = _count()
    blk: Mapped[int | None] = _count()
    blk_against: Mapped[int | None] = _count()
    pf: Mapped[int | None] = _count()
    fouls_drawn: Mapped[int | None] = _count()
    plus_minus: Mapped[int | None] = _count()
    pir_official: Mapped[int | None] = _count()


# --------------------------------------------------------------------------- model inputs


class ElTeamRating(ElBase):
    """A club's prior and its attack/defence adjustments, as of a round.

    ``prior_is_estimate`` is set for a club with no record in the competition (a promoted side
    whose last-season line is an estimate). ``update_weight`` and ``update_basis`` are filled
    by the round update; an imported rating has neither.
    """

    __tablename__ = "el_team_rating"
    __table_args__ = (
        CheckConstraint(_in("source", RATING_SOURCES), name="ck_el_team_rating_source"),
        CheckConstraint(
            f"update_basis IS NULL OR {_in('update_basis', UPDATE_BASES)}",
            name="ck_el_team_rating_basis",
        ),
        CheckConstraint("as_of_round >= 0", name="ck_el_team_rating_round"),
    )

    season_code: Mapped[str] = mapped_column(ForeignKey("el_season.season_code"), primary_key=True)
    club_code: Mapped[str] = mapped_column(ForeignKey("el_club.club_code"), primary_key=True)
    as_of_round: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    pf_prior: Mapped[float] = mapped_column(Float, nullable=False)
    pa_prior: Mapped[float] = mapped_column(Float, nullable=False)
    prior_is_estimate: Mapped[bool] = mapped_column(Boolean, nullable=False)
    attack_adj: Mapped[float] = mapped_column(Float, nullable=False)
    defence_adj: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    update_weight: Mapped[float | None] = _num()
    update_basis: Mapped[str | None] = mapped_column(String(14), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ElPlayerRate(ElBase):
    """A player's per-40 rates as of a round. A percentage whose attempt rate is 0 is NULL.

    ``prior_minutes`` is the weight the prior carries when box-score evidence is added: 400 for
    an official basis, 150 otherwise. A ``positionPrior`` row has NULL rates; the model fills
    them from the pooled mean of the official players at his position.
    """

    __tablename__ = "el_player_rate"
    __table_args__ = (
        CheckConstraint(_in("basis", RATE_BASES), name="ck_el_player_rate_basis"),
        CheckConstraint("as_of_round >= 0", name="ck_el_player_rate_round"),
        CheckConstraint("prior_minutes > 0", name="ck_el_player_rate_prior"),
        CheckConstraint(
            "(fg2_pct IS NULL OR (fg2_pct >= 0 AND fg2_pct <= 1)) AND "
            "(fg3_pct IS NULL OR (fg3_pct >= 0 AND fg3_pct <= 1)) AND "
            "(ft_pct IS NULL OR (ft_pct >= 0 AND ft_pct <= 1))",
            name="ck_el_player_rate_fractions",
        ),
        Index("ix_el_player_rate_club", "season_code", "club_code", "as_of_round"),
    )

    season_code: Mapped[str] = mapped_column(ForeignKey("el_season.season_code"), primary_key=True)
    person_code: Mapped[str] = mapped_column(ForeignKey("el_person.person_code"), primary_key=True)
    as_of_round: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    club_code: Mapped[str] = mapped_column(ForeignKey("el_club.club_code"), nullable=False)
    proj_minutes: Mapped[float | None] = _num()
    pts40: Mapped[float | None] = _num()
    reb40: Mapped[float | None] = _num()
    ast40: Mapped[float | None] = _num()
    fg3m40: Mapped[float | None] = _num()
    stl40: Mapped[float | None] = _num()
    blk40: Mapped[float | None] = _num()
    tov40: Mapped[float | None] = _num()
    fga2_40: Mapped[float | None] = _num()
    fg2_pct: Mapped[float | None] = _num()
    fga3_40: Mapped[float | None] = _num()
    fg3_pct: Mapped[float | None] = _num()
    fta40: Mapped[float | None] = _num()
    ft_pct: Mapped[float | None] = _num()
    basis: Mapped[str] = mapped_column(String(16), nullable=False)
    prior_minutes: Mapped[float] = mapped_column(Float, nullable=False)


class ElModelSetting(ElBase):
    """One model constant and where it came from. The key is constrained to the allowlist."""

    __tablename__ = "el_model_setting"
    __table_args__ = (
        CheckConstraint(_in("key", MODEL_SETTING_KEYS), name="ck_el_model_setting_key"),
        CheckConstraint(_in("provenance", SETTING_PROVENANCES), name="ck_el_model_setting_prov"),
    )

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    provenance: Mapped[str] = mapped_column(String(20), nullable=False)
    set_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ElProjectionLedger(ElBase):
    """A projection as it was computed. There is no line, price or over-or-under column.

    A ``locked`` row requires ``computed_at < tipoff_utc`` (enforced in ``ledger.lock()``,
    because the tip-off is in another table); a ``reconstructed`` row requires
    ``inputs_cutoff < tipoff_utc``. An ``imported`` row is the workbook's published projection
    and holds scores only: the columns the workbook did not publish are NULL (not recorded),
    which a CHECK permits for that kind alone. Every other kind must be complete.
    """

    __tablename__ = "el_projection_ledger"
    __table_args__ = (
        CheckConstraint(_in("kind", LEDGER_KINDS), name="ck_el_ledger_kind"),
        CheckConstraint(_in("cap_policy", CAP_POLICIES), name="ck_el_ledger_cap_policy"),
        CheckConstraint(
            "kind = 'imported' OR ("
            "computed_at IS NOT NULL AND inputs_cutoff IS NOT NULL "
            "AND home_full_strength IS NOT NULL AND away_full_strength IS NOT NULL "
            "AND home_attack_index_after_availability IS NOT NULL "
            "AND away_attack_index_after_availability IS NOT NULL "
            "AND home_defence_index IS NOT NULL AND away_defence_index IS NOT NULL "
            "AND home_advantage_points IS NOT NULL)",
            name="ck_el_ledger_complete",
        ),
        Index("ix_el_ledger_game", "game_id", "kind"),
    )

    ledger_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("el_game.game_id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(14), nullable=False)
    model_version: Mapped[str] = mapped_column(String(24), nullable=False)
    computed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    inputs_cutoff: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    home_pts: Mapped[float] = mapped_column(Float, nullable=False)
    away_pts: Mapped[float] = mapped_column(Float, nullable=False)
    home_full_strength: Mapped[float | None] = _num()
    away_full_strength: Mapped[float | None] = _num()
    home_attack_index_after_availability: Mapped[float | None] = _num()
    away_attack_index_after_availability: Mapped[float | None] = _num()
    home_defence_index: Mapped[float | None] = _num()
    away_defence_index: Mapped[float | None] = _num()
    home_advantage_points: Mapped[float | None] = _num()
    cap_policy: Mapped[str] = mapped_column(String(12), nullable=False)
    settings_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    inputs_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    availability_as_of: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# --------------------------------------------------------------------------- availability


class ElIntelBatch(ElBase):
    """One import or entry session that produced status rows, for provenance and idempotency."""

    __tablename__ = "el_intel_batch"

    batch_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    label: Mapped[str] = mapped_column(String(96), nullable=False)
    file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)


_STATUS_LINK_RULE = (
    "source_url IS NOT NULL OR source_kind = 'manual' "
    f"OR source_label LIKE '%{WITHHELD_LABEL_SUFFIX}'"
)


class ElIntelStatus(ElBase):
    """An availability fact about one player, append-only.

    ``person_code`` NULL means the name was not matched to a squad member; the row goes to the
    review queue and the player is never guessed. ``status`` NULL is "no status given" and is
    rendered as an em dash, never as "available"; an unknown input string is rejected at entry
    (the workbook's ``IFERROR(...,1)``, which turned a typo into "certainly plays", is not
    ported). ``model_status`` keeps the workbook's separate "In model" column.
    ``source_published_at`` is when the *source* said it, never when Hardwood fetched it, and
    every age shown is computed from it.
    """

    __tablename__ = "el_intel_status"
    __table_args__ = (
        CheckConstraint(
            f"status IS NULL OR {_in('status', STATUSES)}", name="ck_el_intel_status_status"
        ),
        CheckConstraint(
            f"model_status IS NULL OR {_in('model_status', STATUSES)}",
            name="ck_el_intel_status_model",
        ),
        CheckConstraint(
            f"reason_category IS NULL OR {_in('reason_category', REASON_CATEGORIES)}",
            name="ck_el_intel_status_reason",
        ),
        CheckConstraint(_in("source_kind", SOURCE_KINDS), name="ck_el_intel_status_kind"),
        CheckConstraint(_STATUS_LINK_RULE, name="ck_el_intel_status_link"),
        CheckConstraint(
            "expected_return_round_from IS NULL OR expected_return_round_to IS NULL "
            "OR expected_return_round_to >= expected_return_round_from",
            name="ck_el_intel_status_rounds",
        ),
        Index("ix_el_intel_status_club", "club_code", "person_code"),
        Index("ix_el_intel_status_published", "source_published_at"),
    )

    status_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    club_code: Mapped[str] = mapped_column(ForeignKey("el_club.club_code"), nullable=False)
    person_code: Mapped[str | None] = mapped_column(
        ForeignKey("el_person.person_code"), nullable=True
    )
    player_name_raw: Mapped[str] = mapped_column(String(96), nullable=False)
    game_id: Mapped[str | None] = mapped_column(ForeignKey("el_game.game_id"), nullable=True)
    game_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str | None] = mapped_column(String(12), nullable=True)
    status_raw: Mapped[str | None] = mapped_column(String(48), nullable=True)
    model_status: Mapped[str | None] = mapped_column(String(12), nullable=True)
    reason_category: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    expected_return_text: Mapped[str | None] = mapped_column(String(120), nullable=True)
    expected_return_round_from: Mapped[int | None] = _count()
    expected_return_round_to: Mapped[int | None] = _count()
    expected_return_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    source_label: Mapped[str] = mapped_column(String(160), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("el_intel_batch.batch_id"), nullable=True
    )
    entered_by_user_id: Mapped[int | None] = _count()
    retracts_status_id: Mapped[int | None] = _count()
    provenance_note: Mapped[str | None] = mapped_column(String(200), nullable=True)


class ElIntelOverride(ElBase):
    """A status the user entered by hand; it outranks a sourced entry until cleared."""

    __tablename__ = "el_intel_override"
    __table_args__ = (
        CheckConstraint(_in("status", STATUSES), name="ck_el_intel_override_status"),
        Index("ix_el_intel_override_person", "person_code"),
    )

    override_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_code: Mapped[str] = mapped_column(ForeignKey("el_person.person_code"), nullable=False)
    club_code: Mapped[str | None] = mapped_column(ForeignKey("el_club.club_code"), nullable=True)
    game_id: Mapped[str | None] = mapped_column(ForeignKey("el_game.game_id"), nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    entered_by_user_id: Mapped[int | None] = _count()


# --------------------------------------------------------------------------- headlines


class ElIntelNewsFeed(ElBase):
    """A headline feed. ``robots.txt`` is checked automatically before every fetch; a
    disallow sets ``enabled`` to 0 and records the reason, which ``/v1/el/sources`` shows.

    There is no terms column: the terms gate was removed. Only title, link, date and source
    name are ever stored from a feed.
    """

    __tablename__ = "el_intel_news_feed"

    feed_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    robots_checked_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    disabled_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ElIntelNewsItem(ElBase):
    """A headline: title, link, date and source name only. There is no body column."""

    __tablename__ = "el_intel_news_item"
    __table_args__ = (
        UniqueConstraint("feed_id", "guid", name="uq_el_intel_news_item_guid"),
        Index("ix_el_intel_news_item_published", "published_at"),
    )

    item_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feed_id: Mapped[int | None] = mapped_column(
        ForeignKey("el_intel_news_feed.feed_id"), nullable=True
    )
    guid: Mapped[str | None] = mapped_column(String(300), nullable=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    link: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    source_name: Mapped[str] = mapped_column(String(80), nullable=False)


class ElIntelNewsSubject(ElBase):
    """Which club or player a headline is about. ``person_code`` is ``''`` for a club-only link."""

    __tablename__ = "el_intel_news_subject"

    item_id: Mapped[int] = mapped_column(ForeignKey("el_intel_news_item.item_id"), primary_key=True)
    club_code: Mapped[str] = mapped_column(ForeignKey("el_club.club_code"), primary_key=True)
    person_code: Mapped[str] = mapped_column(String(16), primary_key=True, default="")


# --------------------------------------------------------------------------- operations


class ElRawPayload(ElBase):
    """A recorded response: the gzip file on disk (never served) and what it was."""

    __tablename__ = "el_raw_payload"

    payload_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint_key: Mapped[str | None] = mapped_column(String(16), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    http_status: Mapped[int | None] = _count()
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bytes: Mapped[int | None] = _count()
    path: Mapped[str | None] = mapped_column(Text, nullable=True)


class ElIngestLog(ElBase):
    """One import or ingest run. ``source_sha256`` makes a workbook import idempotent: a file
    whose hash already has an ``ok`` row is a no-op. ``detail_json`` holds the run's report.

    ``source_sha256`` and ``detail_json`` are additions to the column list in the design,
    which asks for the hash to be recorded here but names no column for it.
    """

    __tablename__ = "el_ingest_log"
    __table_args__ = (Index("ix_el_ingest_log_sha", "source_sha256"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    job: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    games_written: Mapped[int | None] = _count()
    rows_written: Mapped[int | None] = _count()
    invariant_failed: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class ElJobState(ElBase):
    __tablename__ = "el_job_state"

    job_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class ElSourceState(ElBase):
    __tablename__ = "el_source_state"

    source_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    paused_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class ElSyncState(ElBase):
    """The EuroLeague's freshness cursor, independent of the NBA's (``/v1/el/sync``)."""

    __tablename__ = "el_sync_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_el_sync_state_singleton"),
        CheckConstraint(_in("mode", SYNC_MODES), name="ck_el_sync_state_mode"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    sync_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    data_through: Mapped[date | None] = mapped_column(Date, nullable=True)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    paused_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


EL_TABLE_NAMES: tuple[str, ...] = tuple(sorted(ElBase.metadata.tables))


# --------------------------------------------------------------------------- DDL


def render_schema_sql() -> str:
    """Emit the EuroLeague schema as SQLite DDL: the source of ``euroleague/schema.sql``.

    Mirrors :func:`nbastats.accounts.models.render_schema_sql` against ``ElBase.metadata``.
    """
    from sqlalchemy.dialects import sqlite
    from sqlalchemy.schema import CreateIndex, CreateTable

    dialect = sqlite.dialect()
    lines: list[Any] = [
        "-- Hardwood EuroLeague schema, generated by",
        "-- nbastats.euroleague.models.render_schema_sql().",
        "-- Do not edit by hand: run",
        "-- `python3 -m nbastats.euroleague.models > nbastats/euroleague/schema.sql`.",
        "-- Dialect: SQLite. These tables live on their own MetaData (ElBase) in their own file,",
        "-- separate from nbastats/schema.sql -- see nbastats/euroleague/models.py for why.",
        "",
    ]
    for table in ElBase.metadata.sorted_tables:
        lines.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
        lines.append("")
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            lines.append(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")
        if table.indexes:
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":  # pragma: no cover - developer utility
    print(render_schema_sql(), end="")
