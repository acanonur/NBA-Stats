"""Import the user's EuroLeague workbook into the EuroLeague store.

The workbook is real data the user collected: results and box scores from the EuroLeague's data
service, squads, team ratings, a dated and sourced injury list, and a model's projections. It
is also half a gambling tool: it has a "Model line" column, a place to type an outside price
for a game, a probability of going over it and a lean. This importer takes what Hardwood is for
and leaves the rest where it is. It reads the sheets the design names, only the columns the
design names, and never touches a gambling cell, so nothing an outside number could be compared
with ever enters the store.

What goes where
---------------
==================== =====================================================================
Sheet                 Target
==================== =====================================================================
Settings              ``el_model_setting``, by the workbook's own defined names. Only the
                      allowlisted names map (``Regress``, ``HCA``, ``TeamSD``, ``MarginSD``,
                      ``ReplRate``, ``Absorb``, ``BoostCap``, ``RotShare``, ``FormW``,
                      ``KTeam_n``, and the status table). ``TotalSD``, ``PSDBase``,
                      ``PSDSlope`` and ``EdgeP`` are in :data:`IGNORED_SETTINGS` and are only
                      *named* in the report. ``LgPts`` is recomputed from the clubs and
                      ``TeamMinutes`` is the profile constant. Any other name is reported,
                      never stored.
Team Ratings          ``el_club``, ``el_club_alias``, ``el_club_season`` (coach, venue),
                      ``el_team_rating``. Columns A-J only. A club with no record gets
                      ``prior_is_estimate``.
Squads                ``el_person`` (minted ``wb-`` codes), ``el_registration``,
                      ``el_player_rate``. Columns A-T only. A zero-attempt percentage is NULL
                      (the workbook hard-codes 0). ``est.`` lines are imported as estimates
                      unless ``include_estimates`` is off, when only minutes and identity are.
``R<n> Box Scores``   ``el_game`` (final), ``el_player_game``, ``el_team_game``. ``DNP`` is a
                      ``dnp`` line with every stat NULL. Decimal minutes become whole seconds.
                      Fouls drawn, blocks against, plus/minus and the starter flag are not in
                      the sheet and stay NULL.
``R<n> Review``       partials, venue, attendance and neutrality of the game it reviews, and
                      the published projection as an ``imported`` ledger row (scores only).
``Round <n>``         ``el_game`` scheduled (``E2026-R03-NN``), with the tip-off taken from the
                      CEST component of the tip cell, combined with the date in Europe/Berlin
                      and converted to UTC. Neutral only when the home advantage cell is 0 and
                      the venue says so.
Injury Report         ``el_intel_batch`` and append-only ``el_intel_status`` rows with their
                      own source link and date. Only rows whose status is one of the five
                      are taken; free-form sections are skipped.
Not imported          Latest Games, Game Logs, Season Stats, R3 Scorers, Team View, Start Here
                      and Method are counted in the report and nothing else (see below).
==================== =====================================================================

Why some sheets are not imported
--------------------------------
Latest Games and Game Logs mix in friendlies, national-team games and domestic cups from
sources whose terms nobody has reviewed, and the official rows duplicate the box scores;
Season Stats is derived from the box scores; R3 Scorers is picks and lines. Nothing is lost:
what is derivable is derived, and what is a gambling aid is not wanted.

What is never read
------------------
:data:`IGNORED_HEADERS` (Model line, Your line, P(over), Lean, Result v line, Home win %, Win
%) are never in a read map; :data:`READ_MAP` lists every header that *is* read, and a test
asserts the two sets do not meet. Columns are found by exact header text and only the named
ones are read, so a gambling column sitting between two read columns is as invisible as one at
the far edge of the sheet.

Fail closed
-----------
An unknown club code aborts the import and writes nothing: a code is never guessed. A sheet
whose headers cannot be found is reported ``unreadable`` and skipped, and the others proceed.
A cell that is not what its column says becomes a violation that quarantines its game, never a
zero. A status that is not one of the five is rejected and listed, not defaulted to "available".

Invariants and quarantine
-------------------------
Every imported game passes through :func:`check_game`, the rules of design section 4.5 (the
live ingest and the demo use the same ones): points equal ``2*2PM + 3*3PM + FTM`` on every
line, rebounds equal offensive plus defensive, makes never exceed attempts, the players'
points equal the team's equal the final score, team time is 200 minutes (plus 25 per overtime;
the tolerance is 0.05 minute because the sheet rounds to two decimals), and the partials sum to
the final. A hard failure *quarantines* the game: it is stored final with ``stats_status =
'quarantined'`` and its lines are not written (lines from an earlier good import are kept).
Soft failures (a team's rebounds below its players', a side without five starters, a PIR that
does not match its formula) are counted and the row is stored.

Idempotency
-----------
The file's SHA-256 is recorded in ``el_ingest_log``, and a file already imported is a no-op.
A newer workbook upserts facts by natural key, adds its later rounds, appends status rows that
are new and never rewrites history. A workbook never overwrites a ``euroleague-v2`` row, and
never overwrites a model setting the user set by hand or the ledger fitted. Re-importing after
the service has named a fixture's game re-keys the provisional ``E2026-R03-NN`` to the official
id by (round, home, away).

A dry run executes everything against the real store and rolls the transaction back, so its
counts are exactly what a real run would write.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final, Mapping, Sequence
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from ...shared.availability import InvalidStatusError, normalise_status, parse_expected_return
from .. import PACKAGE_VERSION
from ..db import ElStoreError, StoreKindMismatch, bump_sync_version, ensure_identity, utcnow
from ..models import (
    ElClub,
    ElClubAlias,
    ElClubSeason,
    ElGame,
    ElIngestLog,
    ElIntelBatch,
    ElIntelOverride,
    ElIntelStatus,
    ElPerson,
    ElPersonAlias,
    ElPlayerGame,
    ElPlayerRate,
    ElProjectionLedger,
    ElRegistration,
    ElSeason,
    ElTeamGame,
    ElTeamRating,
)
from ..profile import (
    DEFAULT_PHASE,
    KIND_WORKBOOK,
    POSITION5,
    PROFILE,
    ClubCrosswalk,
    SeasonRef,
    fold_name,
    load_club_codes,
    normalise_club_code,
    official_game_id,
    provisional_game_id,
    screen_source_link,
    season_ref_from_start_year,
    workbook_data_source,
    workbook_person_code,
)
from ..settings import DEFAULTS, InvalidSettingError, apply_imported_setting
from . import xlsx as _xlsx
from .xlsx import (
    Column,
    Sheet,
    Table,
    UnreadableWorkbook,
    UnsafeWorkbook,
    Workbook,
    XlsxError,
    cell_date,
    cell_int,
    cell_number,
    cell_text,
)

__all__ = [
    "IGNORED_HEADERS",
    "IGNORED_SETTINGS",
    "READ_MAP",
    "SETTING_MAP",
    "ImportAborted",
    "Line",
    "Violation",
    "GameFacts",
    "GameCheck",
    "check_game",
    "classify_reason",
    "parse_tipoff",
    "SheetOutcome",
    "WorkbookReport",
    "import_workbook",
    "workbook_imported",
    "last_import_failed",
]

_LOG = logging.getLogger(__name__)

#: Headers the importer must never read: gambling machinery and win probability. A test asserts
#: none of them is in :data:`READ_MAP`, and a synthetic workbook that carries them with
#: sentinel values must leave no sentinel anywhere in the store.
IGNORED_HEADERS: Final[frozenset[str]] = frozenset(
    {"Model line", "Your line", "P(over)", "Lean", "Result v line", "Home win %", "Win %"}
)

#: Workbook settings that exist only to feed a probability of going over a number, or a lean.
IGNORED_SETTINGS: Final[frozenset[str]] = frozenset({"TotalSD", "PSDBase", "PSDSlope", "EdgeP"})

#: Defined name -> (model-setting key, provenance).
SETTING_MAP: Final[dict[str, tuple[str, str]]] = {
    "Regress": ("priorRegression", "workbook"),
    "HCA": ("homeAdvantagePoints", "workbook"),
    "TeamSD": ("teamSd", "workbookUnvalidated"),
    "MarginSD": ("marginSd", "workbookUnvalidated"),
    "ReplRate": ("replacementPer40", "workbook"),
    "Absorb": ("absorbShare", "workbook"),
    "BoostCap": ("boostCap", "workbook"),
    "RotShare": ("rotationShare", "workbook"),
    "FormW": ("formWeight", "workbook"),
}
#: Names that are understood and deliberately not stored.
_RECOMPUTED_SETTINGS: Final[dict[str, str]] = {
    "LgPts": "recomputed from the clubs' priors",
    "TeamMinutes": "a constant of the league profile",
}
_STATUS_TABLE_NAMES: Final = ("StatusList", "StatusProb")
_KTEAM = re.compile(r"^KTeam_(\d{1,2})$")

_SETTINGS_SHEET = "Settings"

# ------------------------------------------------------------------------------ columns

_RATING_COLUMNS: Final[tuple[Column, ...]] = (
    Column("code", "Code"),
    Column("name", "Club"),
    Column("country", "Country", required=False),
    Column("coach", "Head coach", required=False),
    Column("wins", "W", required=False),
    Column("losses", "L", required=False),
    Column("pf", "PF/g <season>", required=True),
    Column("pa", "PA/g <season>", required=True),
    Column("attack", "Attack adj"),
    Column("defence", "Defence adj"),
)
_SQUAD_COLUMNS: Final[tuple[Column, ...]] = (
    Column("club", "Team"),
    Column("name", "Player"),
    Column("pos", "Pos", required=False),
    Column("age", "Age", required=False),
    Column("role", "Role", required=False),
    Column("basis", "Basis"),
    Column("minutes", "MIN"),
    Column("pts40", "PTS/40"),
    Column("reb40", "REB/40"),
    Column("ast40", "AST/40"),
    Column("fg3m40", "3PM/40"),
    Column("stl40", "STL/40"),
    Column("blk40", "BLK/40"),
    Column("tov40", "TOV/40"),
    Column("fga2_40", "2PA/40"),
    Column("fga3_40", "3PA/40"),
    Column("fta40", "FTA/40"),
    Column("fg2_pct", "2P%"),
    Column("fg3_pct", "3P%"),
    Column("ft_pct", "FT%"),
)
_BOX_COMMON: Final[tuple[Column, ...]] = (
    Column("game", "Game"),
    Column("date", "Date"),
    Column("club", "Club"),
    Column("opp", "Opp"),
    Column("ha", "H/A"),
    Column("result", "Result"),
)
_BOX_STATS: Final[tuple[Column, ...]] = (
    Column("min", "MIN"),
    Column("pts", "PTS"),
    Column("fgm2", "2PM"),
    Column("fga2", "2PA"),
    Column("fgm3", "3PM"),
    Column("fga3", "3PA"),
    Column("ftm", "FTM"),
    Column("fta", "FTA"),
    Column("oreb", "OREB"),
    Column("dreb", "DREB"),
    Column("reb", "REB"),
    Column("ast", "AST"),
    Column("stl", "STL"),
    Column("tov", "TOV"),
    Column("blk", "BLK"),
    Column("pf", "PF"),
    Column("pir", "PIR"),
)
_PLAYER_COLUMNS: Final[tuple[Column, ...]] = (
    *_BOX_COMMON,
    Column("who", "Player"),
    *_BOX_STATS,
)
_TEAM_COLUMNS: Final[tuple[Column, ...]] = (
    *_BOX_COMMON,
    Column("who", "Team"),
    *_BOX_STATS,
)
_REVIEW_COLUMNS: Final[tuple[Column, ...]] = (
    Column("num", "#"),
    Column("date", "Date", required=False),
    Column("home", "Home", occurrence=1),
    Column("away", "Away", occurrence=1),
    Column("proj_home", "Proj home"),
    Column("proj_away", "Proj away"),
    Column("res_home", "Home", occurrence=2, required=False),
    Column("res_away", "Away", occurrence=2, required=False),
    Column("quarters", "Quarters", required=False),
    Column("venue", "Venue", required=False),
    Column("attendance", "Attendance", required=False),
)
_FIXTURE_COLUMNS: Final[tuple[Column, ...]] = (
    Column("num", "#"),
    Column("date", "Date"),
    Column("tip", "Tip local / CEST / TR", required=False),
    Column("home", "Home"),
    Column("away", "Away"),
    Column("venue", "Venue", required=False),
    Column("home_adv", "Home adv (pts)", required=False),
)
_INJURY_COLUMNS: Final[tuple[Column, ...]] = (
    Column("club", "Club"),
    Column("name", "Player"),
    Column("problem", "Problem", required=False),
    Column("status", "Status (research)"),
    Column("model", "In model", required=False),
    Column("expected", "Expected return", required=False),
    Column("source", "Source", required=False),
    Column("published", "Source date"),
)

#: Every header (and settings name) the importer reads, by sheet. A test asserts that none of
#: :data:`IGNORED_HEADERS` or :data:`IGNORED_SETTINGS` appears here.
READ_MAP: Final[dict[str, tuple[str, ...]]] = {
    "Team Ratings": tuple(c.header for c in _RATING_COLUMNS),
    "Squads": tuple(c.header for c in _SQUAD_COLUMNS),
    "R<n> Box Scores (players)": tuple(c.header for c in _PLAYER_COLUMNS),
    "R<n> Box Scores (teams)": tuple(c.header for c in _TEAM_COLUMNS),
    "R<n> Review": tuple(c.header for c in _REVIEW_COLUMNS),
    "Round <n>": tuple(c.header for c in _FIXTURE_COLUMNS),
    "Injury Report": tuple(c.header for c in _INJURY_COLUMNS),
    "Settings (defined names)": (*SETTING_MAP, "KTeam_<n>", *_STATUS_TABLE_NAMES),
}

_COUNT_KEYS: Final[tuple[str, ...]] = (
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
    "pf",
)
_BOX_HEADERS: Final[dict[str, str]] = {c.key: c.header for c in _BOX_STATS}

_SHEET_BOX = re.compile(r"^R(\d{1,2}) Box Scores$")
_SHEET_REVIEW = re.compile(r"^R(\d{1,2}) Review$")
_SHEET_ROUND = re.compile(r"^Round (\d{1,2})$")
_SHEET_SCORERS = re.compile(r"^R\d{1,2} Scorers$")

_NOT_IMPORTED: Final[dict[str, str]] = {
    "Latest Games": "mixes competitions from sources whose terms are unreviewed; official rows "
    "duplicate the box scores",
    "Game Logs": "built to give form for lines; mixes competitions and sources",
    "Season Stats": "derived from the box scores",
    "Team View": "a derived view of one club at a time",
    "Start Here": "prose",
    "Method": "prose",
}

_PERIOD_RE = re.compile(r"^\s*(\d{1,3})\s*-\s*(\d{1,3})\s*$")
_RESULT_RE = re.compile(r"^\s*([WL])\s+(\d{1,3})\s*-\s*(\d{1,3})\s*$")
_TIP_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*/\s*(\d{1,2}):(\d{2})\s*/\s*(\d{1,2}):(\d{2})\s*$")
_NEUTRAL_RE = re.compile(r"\s*\(\s*neutral\s*\)\s*$", re.IGNORECASE)
_AS_OF_RE = re.compile(r"\bas of\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", re.IGNORECASE)
_SEASON_TITLE_RE = re.compile(r"\b(\d{4})-(\d{2})\b")
_MONTHS: Final = {
    name: number
    for number, name in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        start=1,
    )
}

#: Country names the workbook uses, as ISO 3166-1 alpha-2 codes. An unlisted country is NULL.
_COUNTRY_CODES: Final[dict[str, str]] = {
    "greece": "GR",
    "spain": "ES",
    "turkey": "TR",
    "serbia": "RS",
    "lithuania": "LT",
    "israel": "IL",
    "italy": "IT",
    "germany": "DE",
    "france": "FR",
    "monaco": "MC",
    "united arab emirates": "AE",
    "uae": "AE",
    "bulgaria": "BG",
    "croatia": "HR",
    "slovenia": "SI",
    "poland": "PL",
    "latvia": "LV",
    "hungary": "HU",
    "montenegro": "ME",
}

_BERLIN = ZoneInfo(PROFILE.schedule_tz)

_MAX_WARNINGS = 200
_ESTIMATE_BASIS = "est."
_OFFICIAL_BASIS = "EuroLeague"


class ImportAborted(ValueError):
    """The import cannot proceed and writes nothing (an unknown club code, an unknowable
    season, a file that is not a toolkit workbook)."""


# ------------------------------------------------------------------------------ lines and rules


@dataclass
class Line:
    """One player's or one team's box-score line, in the shape the invariants need.

    ``side`` is ``"home"`` or ``"away"``. ``None`` is "not recorded". A ``dnp`` line has every
    stat ``None``.
    """

    side: str
    name: str | None = None
    participation: str = "played"
    is_starter: bool | None = None
    seconds: int | None = None
    pts: int | None = None
    fgm2: int | None = None
    fga2: int | None = None
    fgm3: int | None = None
    fga3: int | None = None
    ftm: int | None = None
    fta: int | None = None
    oreb: int | None = None
    dreb: int | None = None
    reb: int | None = None
    ast: int | None = None
    stl: int | None = None
    tov: int | None = None
    blk: int | None = None
    blk_against: int | None = None
    pf: int | None = None
    fouls_drawn: int | None = None
    plus_minus: int | None = None
    pir_official: int | None = None


@dataclass(frozen=True)
class Violation:
    """A failed invariant. ``kind`` is ``hard`` (quarantines the game) or ``soft`` (warns)."""

    rule: str
    kind: str
    message: str


@dataclass
class GameFacts:
    """What :func:`check_game` needs to know about one game."""

    final_home: int | None
    final_away: int | None
    players: Sequence[Line]
    #: Team totals by side; a side may be missing.
    teams: Mapping[str, Line]
    home_partials: Sequence[int] | None = None
    away_partials: Sequence[int] | None = None
    #: Tolerance on team time, in seconds: 3 for the workbook's two-decimal minutes, 6 for the
    #: service's whole seconds.
    seconds_tolerance: int = 3


@dataclass(frozen=True)
class GameCheck:
    violations: tuple[Violation, ...]
    ot_periods: int | None

    @property
    def hard(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.kind == "hard")

    @property
    def soft(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.kind == "soft")


_SIDES: Final = ("home", "away")


def _all_known(line: Line, *names: str) -> bool:
    return all(getattr(line, name) is not None for name in names)


def check_game(facts: GameFacts) -> GameCheck:
    """Apply the design's invariants (section 4.5) to one game.

    Hard rules, whose failure quarantines the game: ``pts_formula``, ``reb_sum``,
    ``makes_le_attempts``, ``team_points`` (players = team = final), ``final_score``,
    ``team_time`` (200 minutes, plus 25 per overtime), ``partials``. Soft rules, which warn:
    ``reb_vs_team``, ``tov_vs_team``, ``starters``, ``pir_formula``. A rule that needs a value
    the source did not record is skipped, never failed: absence is not a violation.
    """
    violations: list[Violation] = []

    def hard(rule: str, message: str) -> None:
        violations.append(Violation(rule, "hard", message))

    def soft(rule: str, message: str) -> None:
        violations.append(Violation(rule, "soft", message))

    played = [p for p in facts.players if p.participation == "played"]
    for line in [*played, *facts.teams.values()]:
        who = f"{line.side} {line.name or 'team'}"
        if _all_known(line, "pts", "fgm2", "fgm3", "ftm"):
            expected = 2 * line.fgm2 + 3 * line.fgm3 + line.ftm  # type: ignore[operator]
            if line.pts != expected:
                hard("pts_formula", f"{who}: pts {line.pts} != 2*fgm2 + 3*fgm3 + ftm = {expected}")
        if _all_known(line, "reb", "oreb", "dreb"):
            if line.reb != line.oreb + line.dreb:  # type: ignore[operator]
                hard("reb_sum", f"{who}: reb {line.reb} != oreb + dreb")
        for made, tried in (("fgm2", "fga2"), ("fgm3", "fga3"), ("ftm", "fta")):
            m, t = getattr(line, made), getattr(line, tried)
            if m is not None and t is not None and m > t:
                hard("makes_le_attempts", f"{who}: {made} {m} > {tried} {t}")

    finals = {"home": facts.final_home, "away": facts.final_away}
    for side in _SIDES:
        team = facts.teams.get(side)
        side_players = [p for p in played if p.side == side]
        pts = [p.pts for p in side_players]
        if side_players and all(v is not None for v in pts):
            total = sum(pts)  # type: ignore[arg-type]
            if team is not None and team.pts is not None and total != team.pts:
                hard("team_points", f"{side}: players sum to {total}, team line says {team.pts}")
            if finals[side] is not None and total != finals[side]:
                hard(
                    "team_points", f"{side}: players sum to {total}, final score is {finals[side]}"
                )
        if team is not None and team.pts is not None and finals[side] is not None:
            if team.pts != finals[side]:
                hard(
                    "final_score",
                    f"{side}: team line says {team.pts}, final score is {finals[side]}",
                )

    # Partials sum to the final, and tell us how many periods were played.
    ot_from_partials: int | None = None
    hp, ap = facts.home_partials, facts.away_partials
    if hp is not None and ap is not None:
        if len(hp) != len(ap) or len(hp) < 4:
            hard("partials", f"partials have {len(hp)} and {len(ap)} periods; at least 4 expected")
        else:
            ot_from_partials = len(hp) - 4
            if facts.final_home is not None and sum(hp) != facts.final_home:
                hard("partials", f"home partials sum to {sum(hp)}, final is {facts.final_home}")
            if facts.final_away is not None and sum(ap) != facts.final_away:
                hard("partials", f"away partials sum to {sum(ap)}, final is {facts.final_away}")

    # Team time: 200 minutes of player time, plus 25 per overtime.
    regulation = PROFILE.team_regulation_seconds
    overtime = PROFILE.team_overtime_seconds
    observed: list[int] = []
    for side in _SIDES:
        team = facts.teams.get(side)
        if team is not None and team.seconds is not None:
            observed.append(team.seconds)
    for side in _SIDES:
        side_players = [p for p in played if p.side == side]
        if side_players and all(p.seconds is not None for p in side_players):
            observed.append(sum(p.seconds for p in side_players))  # type: ignore[misc]
    if ot_from_partials is not None:
        ot: int | None = ot_from_partials
    elif observed:
        ot = max(0, round((observed[0] - regulation) / overtime))
    else:
        ot = None
    if ot is not None:
        expected_seconds = regulation + ot * overtime
        for side in _SIDES:
            team = facts.teams.get(side)
            if team is not None and team.seconds is not None:
                if abs(team.seconds - expected_seconds) > facts.seconds_tolerance:
                    hard(
                        "team_time",
                        f"{side}: team time {team.seconds}s, expected {expected_seconds}s",
                    )
            side_players = [p for p in played if p.side == side]
            if side_players and all(p.seconds is not None for p in side_players):
                total_seconds = sum(p.seconds for p in side_players)  # type: ignore[misc]
                if abs(total_seconds - expected_seconds) > facts.seconds_tolerance:
                    hard(
                        "team_time",
                        f"{side}: players' time {total_seconds}s, expected {expected_seconds}s",
                    )

    # Soft rules.
    for side in _SIDES:
        team = facts.teams.get(side)
        side_players = [p for p in facts.players if p.side == side]
        side_played = [p for p in side_players if p.participation == "played"]
        if team is not None:
            for field_name, rule in (("reb", "reb_vs_team"), ("tov", "tov_vs_team")):
                values = [getattr(p, field_name) for p in side_played]
                team_value = getattr(team, field_name)
                if values and all(v is not None for v in values) and team_value is not None:
                    if sum(values) > team_value:
                        soft(rule, f"{side}: players' {field_name} exceed the team's {team_value}")
        if any(p.is_starter is not None for p in side_players):
            starters = sum(1 for p in side_players if p.is_starter)
            if starters != 5:
                soft("starters", f"{side}: {starters} starters, expected 5")
    for p in played:
        if p.fouls_drawn is not None and p.blk_against is not None and p.pir_official is not None:
            parts = (
                "pts",
                "reb",
                "ast",
                "stl",
                "blk",
                "tov",
                "pf",
                "fgm2",
                "fga2",
                "fgm3",
                "fga3",
                "ftm",
                "fta",
            )
            if _all_known(p, *parts):
                formula = (
                    p.pts
                    + p.reb
                    + p.ast
                    + p.stl
                    + p.blk
                    + p.fouls_drawn  # type: ignore[operator]
                    - (p.fga2 - p.fgm2)
                    - (p.fga3 - p.fgm3)
                    - (p.fta - p.ftm)  # type: ignore[operator]
                    - p.tov
                    - p.blk_against
                    - p.pf  # type: ignore[operator]
                )
                if formula != p.pir_official:
                    soft("pir_formula", f"{p.side} {p.name}: pir {p.pir_official} != {formula}")

    if any(v.kind == "hard" and v.rule in ("team_time", "partials") for v in violations):
        ot = None
    return GameCheck(tuple(violations), ot)


# ------------------------------------------------------------------------------ parsed records


@dataclass
class _Rating:
    code: str
    name: str
    country: str | None
    coach: str | None
    pf: float
    pa: float
    attack: float
    defence: float
    is_estimate: bool


@dataclass
class _Squad:
    code: str
    name: str
    pos5: str | None
    age: int | None
    role: str | None
    basis: str
    minutes: float | None
    rates: dict[str, float | None]


@dataclass
class _ParsedGame:
    round_number: int
    game_code: int | None
    number: int | None
    date: date
    home: str
    away: str
    final_home: int | None = None
    final_away: int | None = None
    players: list[Line] = field(default_factory=list)
    player_names: list[str] = field(default_factory=list)
    teams: dict[str, Line] = field(default_factory=dict)
    home_partials: list[int] | None = None
    away_partials: list[int] | None = None
    venue: str | None = None
    attendance: int | None = None
    is_neutral: bool | None = None
    proj_home: float | None = None
    proj_away: float | None = None
    issues: list[str] = field(default_factory=list)
    check: GameCheck | None = None
    has_lines: bool = False


@dataclass
class _Fixture:
    round_number: int
    number: int
    date: date
    home: str
    away: str
    tipoff: datetime | None
    venue: str | None
    is_neutral: bool | None
    home_adv: float | None


@dataclass
class _Status:
    row: int
    code: str
    name: str
    status: str
    status_raw: str
    model_status: str | None
    problem: str | None
    expected: str | None
    url: str | None
    published: date


@dataclass
class SheetOutcome:
    sheet: str
    role: str
    status: str
    rows_read: int = 0
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sheet": self.sheet,
            "role": self.role,
            "status": self.status,
            "rowsRead": self.rows_read,
            "note": self.note,
        }


@dataclass
class WorkbookReport:
    """What an import did (or, for a dry run, would do). Safe to print and to store: it names
    ignored headers and settings but never carries their values."""

    file_name: str = ""
    sha256: str = ""
    size: int = 0
    dry_run: bool = False
    #: ``ok``, ``alreadyImported``, ``aborted`` (nothing written) or ``failed``.
    status: str = "ok"
    season: str | None = None
    as_of_round: int | None = None
    sheets: list[SheetOutcome] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    ignored_headers: list[str] = field(default_factory=list)
    ignored_settings: list[str] = field(default_factory=list)
    recomputed_settings: list[str] = field(default_factory=list)
    unknown_settings: list[str] = field(default_factory=list)
    quarantined: list[dict[str, Any]] = field(default_factory=list)
    soft_violations: int = 0
    rejected_statuses: list[str] = field(default_factory=list)
    unmatched_players: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def count(self, name: str, n: int = 1) -> None:
        self.counts[name] = self.counts.get(name, 0) + n

    def warn(self, message: str) -> None:
        if len(self.warnings) < _MAX_WARNINGS:
            self.warnings.append(message)
        elif len(self.warnings) == _MAX_WARNINGS:
            self.warnings.append("further warnings omitted")

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "alreadyImported")

    def to_dict(self) -> dict[str, Any]:
        return {
            "fileName": self.file_name,
            "sha256": self.sha256,
            "bytes": self.size,
            "dryRun": self.dry_run,
            "status": self.status,
            "season": self.season,
            "asOfRound": self.as_of_round,
            "sheets": [s.to_dict() for s in self.sheets],
            "counts": dict(sorted(self.counts.items())),
            "ignoredHeaders": sorted(set(self.ignored_headers)),
            "ignoredSettings": sorted(set(self.ignored_settings)),
            "recomputedSettings": sorted(set(self.recomputed_settings)),
            "unknownSettings": sorted(set(self.unknown_settings)),
            "quarantinedGames": list(self.quarantined),
            "softViolations": self.soft_violations,
            "rejectedStatuses": list(self.rejected_statuses),
            "unmatchedPlayers": list(self.unmatched_players),
            "warnings": list(self.warnings),
            "error": self.error,
            "importerVersion": PACKAGE_VERSION,
        }

    def render(self) -> str:
        """A short human-readable summary for the command line."""
        lines = [
            f"{'DRY RUN: ' if self.dry_run else ''}workbook {self.file_name or '?'} "
            f"(sha256 {self.sha256[:12]}) -> {self.status}",
        ]
        if self.error:
            lines.append(f"  error: {self.error}")
        if self.season:
            lines.append(f"  season {self.season}, as of round {self.as_of_round}")
        for outcome in self.sheets:
            note = f" ({outcome.note})" if outcome.note else ""
            lines.append(
                f"  [{outcome.status:<11}] {outcome.sheet}: {outcome.rows_read} rows{note}"
            )
        if self.counts:
            lines.append("  " + ", ".join(f"{k}={v}" for k, v in sorted(self.counts.items())))
        if self.ignored_headers or self.ignored_settings:
            lines.append(
                "  never read: "
                + ", ".join(sorted(set(self.ignored_headers) | set(self.ignored_settings)))
            )
        if self.unknown_settings:
            lines.append(
                "  unknown settings (not stored): " + ", ".join(sorted(self.unknown_settings))
            )
        for item in self.quarantined:
            lines.append(f"  QUARANTINED {item['gameId']}: {', '.join(item['rules'])}")
        if self.soft_violations:
            lines.append(f"  soft invariant warnings: {self.soft_violations}")
        for message in self.warnings:
            lines.append(f"  warning: {message}")
        return "\n".join(lines)


# ------------------------------------------------------------------------------ small parsers


def _as_date(value: Any) -> date | None:
    parsed = cell_date(value)
    if parsed is not None:
        return parsed
    text = cell_text(value)
    if text:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    return None


def parse_tipoff(day: date, cell: Any) -> datetime | None:
    """UTC tip-off from a ``local / CEST / TR`` cell and the game's date, or ``None``.

    The middle component is the time in central Europe; it is combined with ``day`` in
    Europe/Berlin and converted to UTC, so a tip-off after the clocks change is still right.
    Any other shape is ``None``: a tip-off is never guessed.
    """
    text = cell_text(cell)
    if text is None:
        return None
    match = _TIP_RE.match(text)
    if not match:
        return None
    hour, minute = int(match.group(3)), int(match.group(4))
    if hour > 23 or minute > 59:
        return None
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=_BERLIN)
    return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _clean_venue(text: str | None) -> tuple[str | None, bool]:
    """``(venue without the neutral marker, marked neutral)``."""
    if text is None:
        return None, False
    stripped = _NEUTRAL_RE.sub("", text).strip()
    return (stripped[:128] or None), stripped != text.strip()


def _unit(value: Any) -> float | None:
    """A fraction in [0, 1], else ``None`` (a percentage typed as 56 is refused, not scaled)."""
    number = cell_number(value)
    if number is None or not 0.0 <= number <= 1.0:
        return None
    return number


def _nonneg(value: Any) -> float | None:
    number = cell_number(value)
    return number if number is not None and number >= 0 else None


# ------------------------------------------------------------------------------ reason categories

_REASON_RULES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = tuple(
    (category, re.compile(pattern, re.IGNORECASE))
    for category, pattern in (
        ("suspension", r"suspen"),
        ("gLeague", r"g[ -]?league"),
        ("coachDecision", r"coach'?s? decision|squad choice|left (?:out|off)|not selected"),
        (
            "notRegistered",
            r"not in the euroleague game roster|not (?:yet )?registered|not on the registered",
        ),
        ("personal", r"\bfamily\b|\bwedding\b|\bpersonal\b|\bbirth\b|\bbereavement\b"),
        ("notWithTeam", r"did not travel|not with the team|stayed (?:in|at)"),
        ("rest", r"\brest(?:ed)?\b"),
        ("illness", r"\billness\b|\bsick\b|\bflu\b|\bcovid\b|\bvirus\b|\bfever\b"),
        (
            "injury",
            r"injur|surgery|\btorn?\b|\btear\b|\bacl\b|achilles|hamstring|\bcalf\b|\bknee\b|ankle"
            r"|\bback\b|groin|thigh|\bquad(?:s|riceps)?\b|adductor|meniscus|fractur|metacarpal"
            r"|strain|sprain|tendin|wrist|shoulder|\bleg\b|\bknock\b|\bblow\b|muscle|rehab"
            r"|discomfort|\bhand\b",
        ),
    )
)
_NO_REASON = re.compile(r"no reason", re.IGNORECASE)
_COACH_EXPECTED = frozenset({"squad choice", "home game"})


def classify_reason(problem: str | None, expected: str | None, status: str | None) -> str | None:
    """A reason category for a workbook line, or ``None`` when none can honestly be given.

    The workbook's "Squad choice" and "Home game" return notes mean the player was left out by
    the coach, whatever the problem column says. Otherwise the first matching phrase of the
    problem text decides. A line that gives no reason says so with ``None``; an *available*
    player with no matching phrase has no absence to classify, so also ``None``; any other
    unmatched line is ``other``.
    """
    if expected and expected.strip().lower() in _COACH_EXPECTED:
        return "coachDecision"
    text = (problem or "").strip()
    if not text or _NO_REASON.search(text):
        return None
    for category, pattern in _REASON_RULES:
        if pattern.search(text):
            return category
    return None if status == "available" else "other"


# ------------------------------------------------------------------------------ parse stage


@dataclass
class _Parsed:
    settings: list[tuple[str, float, str]] = field(default_factory=list)
    ratings: list[_Rating] = field(default_factory=list)
    squads: list[_Squad] = field(default_factory=list)
    games: list[_ParsedGame] = field(default_factory=list)
    fixtures: list[_Fixture] = field(default_factory=list)
    statuses: list[_Status] = field(default_factory=list)
    status_as_of: date | None = None
    as_of_round: int = 0
    season_start_year: int | None = None
    hca: float = PROFILE.home_advantage_default
    club_codes: dict[str, str] = field(default_factory=dict)


class _Parser:
    """Turns a :class:`Workbook` into plain records. Pure: no database, no clock."""

    def __init__(
        self,
        workbook: Workbook,
        crosswalk: ClubCrosswalk,
        include_estimates: bool,
        report: WorkbookReport,
    ) -> None:
        self.wb = workbook
        self.crosswalk = crosswalk
        self.include_estimates = include_estimates
        self.report = report
        self.out = _Parsed()

    # -------------------------------------------------------------------- helpers

    def club(self, raw: Any, where: str) -> str | None:
        """The official club code for a workbook code.

        ``None`` for a blank cell (an opponent not yet known, a row half filled in): the caller
        skips the row and says so. A code that is *there* but not in the crosswalk aborts the
        whole import, because a code is never guessed.
        """
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return None
        code = normalise_club_code(raw)
        row = self.crosswalk.by_workbook(code)
        if code is None or row is None:
            raise ImportAborted(f"unknown club code {raw!r} ({where}); nothing was imported")
        self.out.club_codes[code] = row.official_code
        return row.official_code

    def _outcome(
        self, sheet: str, role: str, status: str, rows: int = 0, note: str | None = None
    ) -> None:
        self.report.sheets.append(SheetOutcome(sheet, role, status, rows, note))

    def _note_ignored(self, table: Table) -> None:
        for value in table.sheet.row(table.header_row).values():
            text = cell_text(value)
            if text in IGNORED_HEADERS:
                self.report.ignored_headers.append(text)  # type: ignore[arg-type]

    def _table(self, sheet: Sheet, columns: Sequence[Column], role: str, **kw: Any) -> Table | None:
        table = _find_table(sheet, columns, **kw)
        if table is None:
            self._outcome(
                sheet.name, role, "unreadable", sheet.row_count, "required headers not found"
            )
            self.report.warn(f"{sheet.name}: required headers not found; sheet skipped")
        return table

    # -------------------------------------------------------------------- driver

    def run(self) -> _Parsed:
        names = self.wb.sheet_names
        roles_done = 0
        if self.wb.has_sheet(_SETTINGS_SHEET) or any(
            _KTEAM.match(n) for n in self.wb.defined_names
        ):
            self._settings()
            roles_done += 1
        if "Team Ratings" in names:
            roles_done += self._ratings()
        if "Squads" in names:
            roles_done += self._squads()
        box_rounds: dict[int, list[_ParsedGame]] = {}
        for name in names:
            match = _SHEET_BOX.match(name)
            if match:
                games = self._box_scores(self.wb.sheet(name), int(match.group(1)))
                if games is not None:
                    box_rounds[int(match.group(1))] = games
                    roles_done += 1
        reviews = []
        for name in names:
            match = _SHEET_REVIEW.match(name)
            if match:
                rows = self._review(self.wb.sheet(name), int(match.group(1)))
                if rows is not None:
                    reviews.append((int(match.group(1)), rows))
                    roles_done += 1
        for name in names:
            match = _SHEET_ROUND.match(name)
            if match:
                roles_done += self._fixtures(self.wb.sheet(name), int(match.group(1)))
        if "Injury Report" in names:
            roles_done += self._injuries(self.wb.sheet("Injury Report"))
        for name in names:
            if name in _NOT_IMPORTED or _SHEET_SCORERS.match(name):
                reason = _NOT_IMPORTED.get(name, "picks and lines; nothing here is wanted")
                self._outcome(
                    name, "notImported", "notImported", self.wb.sheet(name).row_count, reason
                )
            elif not (
                name in ("Settings", "Team Ratings", "Squads", "Injury Report")
                or _SHEET_BOX.match(name)
                or _SHEET_REVIEW.match(name)
                or _SHEET_ROUND.match(name)
            ):
                self._outcome(
                    name, "unknown", "notImported", self.wb.sheet(name).row_count, "not recognised"
                )

        games: list[_ParsedGame] = []
        for round_number in sorted(box_rounds):
            games.extend(box_rounds[round_number])
        self._merge_reviews(games, reviews)
        for game in games:
            self._finalise(game)
        self.out.games = [g for g in games if g.final_home is not None and g.final_away is not None]
        for g in games:
            if g not in self.out.games:
                self.report.warn(
                    f"round {g.round_number} game {g.game_code or g.number}: no final score "
                    "could be read; the game was skipped"
                )
        self.out.as_of_round = max(
            (r for r, gs in box_rounds.items() if any(g.has_lines for g in gs)), default=0
        )
        if roles_done == 0:
            raise ImportAborted("no recognised sheet was readable: this is not a toolkit workbook")
        self._season()
        return self.out

    # -------------------------------------------------------------------- settings

    def _settings(self) -> None:
        wb, found = self.wb, False
        for name, target in sorted(wb.defined_names.items()):
            kteam = _KTEAM.match(name)
            if kteam:
                found = True
                number = int(kteam.group(1))
                value = cell_number(wb.defined_value(name))
                key = f"roundWeight.{number}"
                if value is None or key not in DEFAULTS:
                    self.report.unknown_settings.append(name)
                else:
                    self.out.settings.append((key, value, "workbook"))
                continue
            if target.sheet != _SETTINGS_SHEET:
                continue
            found = True
            if name in SETTING_MAP:
                key, provenance = SETTING_MAP[name]
                value = cell_number(wb.defined_value(name))
                if value is None:
                    self.report.warn(f"setting {name} has no numeric value; not stored")
                    continue
                self.out.settings.append((key, value, provenance))
                if name == "HCA":
                    self.out.hca = value
            elif name in IGNORED_SETTINGS:
                self.report.ignored_settings.append(name)
            elif name in _RECOMPUTED_SETTINGS:
                self.report.recomputed_settings.append(name)
            elif name in _STATUS_TABLE_NAMES:
                continue
            else:
                self.report.unknown_settings.append(name)
        labels = wb.defined_values("StatusList")
        chances = wb.defined_values("StatusProb")
        if labels and len(labels) == len(chances):
            table: dict[str, float] = {}
            for label, chance in zip(labels, chances):
                try:
                    status = normalise_status(cell_text(label))
                except InvalidStatusError:
                    status = None
                number = cell_number(chance)
                if status is not None and number is not None:
                    table[status] = number
            if len(table) == 5:
                for status, number in table.items():
                    self.out.settings.append((f"statusChance.{status}", number, "workbook"))
            else:
                self.report.warn(
                    "the status table is not five known statuses with numbers; not stored"
                )
        self._outcome(
            _SETTINGS_SHEET,
            "settings",
            "imported" if found else "absent",
            len(self.out.settings),
            "read by defined name",
        )

    # -------------------------------------------------------------------- team ratings

    def _ratings(self) -> int:
        sheet = self.wb.sheet("Team Ratings")
        table = self._table(sheet, _RATING_COLUMNS, "teamRatings")
        if table is None:
            return 0
        self._note_ignored(table)
        rows = 0
        for number, row in table.rows(stop_when_blank="code"):
            rows += 1
            code = self.club(row["code"], f"Team Ratings row {number}")
            if code is None:
                continue
            values = [cell_number(row[k]) for k in ("pf", "pa", "attack", "defence")]
            if any(v is None for v in values):
                self.report.warn(f"Team Ratings row {number}: an incomplete rating; not stored")
                continue
            country = cell_text(row.get("country"))
            self.out.ratings.append(
                _Rating(
                    code=code,
                    name=cell_text(row["name"]) or code,
                    country=_COUNTRY_CODES.get(country.lower()) if country else None,
                    coach=cell_text(row.get("coach")),
                    pf=values[0],  # type: ignore[arg-type]
                    pa=values[1],  # type: ignore[arg-type]
                    attack=values[2],  # type: ignore[arg-type]
                    defence=values[3],  # type: ignore[arg-type]
                    is_estimate=cell_number(row.get("wins")) is None
                    and cell_number(row.get("losses")) is None,
                )
            )
        self._outcome("Team Ratings", "teamRatings", "imported", rows, "columns A-J only")
        return 1

    # -------------------------------------------------------------------- squads

    def _squads(self) -> int:
        sheet = self.wb.sheet("Squads")
        table = self._table(sheet, _SQUAD_COLUMNS, "squads")
        if table is None:
            return 0
        self._note_ignored(table)
        seen: set[tuple[str, str]] = set()
        rows = 0
        for number, row in table.rows(stop_when_blank="club"):
            rows += 1
            code = self.club(row["club"], f"Squads row {number}")
            if code is None:
                continue
            name = cell_text(row["name"])
            if name is None:
                self.report.warn(f"Squads row {number}: a row with no player name; skipped")
                continue
            key = (code, fold_name(name))
            if key in seen:
                self.report.warn(
                    f"Squads row {number}: {name!r} appears twice for {code}; the first is kept"
                )
                continue
            seen.add(key)
            pos = cell_text(row.get("pos"))
            if pos is not None and pos not in POSITION5:
                self.report.warn(f"Squads row {number}: position {pos!r} is not one of {POSITION5}")
                pos = None
            basis = cell_text(row["basis"]) or ""
            if basis not in (_OFFICIAL_BASIS, _ESTIMATE_BASIS):
                self.report.warn(f"Squads row {number}: basis {basis!r} treated as an estimate")
                basis = _ESTIMATE_BASIS
            rates: dict[str, float | None] = {
                k: _nonneg(row[k])
                for k in (
                    "pts40",
                    "reb40",
                    "ast40",
                    "fg3m40",
                    "stl40",
                    "blk40",
                    "tov40",
                    "fga2_40",
                    "fga3_40",
                    "fta40",
                )
            }
            for pct, attempts in (
                ("fg2_pct", "fga2_40"),
                ("fg3_pct", "fga3_40"),
                ("ft_pct", "fta40"),
            ):
                value = _unit(row[pct])
                # The workbook writes 0% for a player who never attempts the shot. That is
                # "not applicable", so it is NULL, never a recorded zero.
                rates[pct] = None if rates[attempts] == 0 else value
            self.out.squads.append(
                _Squad(
                    code=code,
                    name=name[:96],
                    pos5=pos,
                    age=cell_int(row.get("age")),
                    role=(cell_text(row.get("role")) or "")[:24] or None,
                    basis=basis,
                    minutes=_nonneg(row["minutes"]),
                    rates=rates,
                )
            )
        self._outcome("Squads", "squads", "imported", rows, "columns A-T only")
        return 1

    # -------------------------------------------------------------------- box scores

    def _box_scores(self, sheet: Sheet, round_number: int) -> list[_ParsedGame] | None:
        role = "boxScores"
        players = self._table(sheet, _PLAYER_COLUMNS, role)
        if players is None:
            return None
        games: dict[int, _ParsedGame] = {}
        rows = 0
        for number, row in players.rows(stop_when_blank="game"):
            if cell_number(row["game"]) is None:
                break  # a footnote under the table, not a line
            rows += 1
            game = self._game_for_row(games, row, round_number, sheet.name, number)
            if game is None:
                continue
            line = self._player_line(row, game, sheet.name, number)
            if line is not None:
                game.players.append(line)
                game.player_names.append(cell_text(row["who"]) or "")
                game.has_lines = True
        teams = _find_table(sheet, _TEAM_COLUMNS, first_row=players.header_row + 1)
        if teams is not None:
            for number, row in teams.rows(stop_when_blank="game"):
                if cell_number(row["game"]) is None:
                    break  # the note beneath the totals
                rows += 1
                game = self._game_for_row(games, row, round_number, sheet.name, number)
                if game is None:
                    continue
                self._team_line(row, game, sheet.name, number)
        else:
            self.report.warn(f"{sheet.name}: no team totals table; team-level checks are skipped")
        self._outcome(sheet.name, role, "imported", rows, f"{len(games)} games")
        return list(games.values())

    def _game_for_row(
        self,
        games: dict[int, _ParsedGame],
        row: dict[str, Any],
        round_number: int,
        sheet: str,
        n: int,
    ) -> _ParsedGame | None:
        code = cell_int(row["game"])
        when = _as_date(row["date"])
        where = f"{sheet} row {n}"
        if code is None or code < 1 or when is None:
            self.report.warn(f"{where}: no readable game code and date; the row was skipped")
            return None
        club = self.club(row["club"], where)
        opp = self.club(row["opp"], where)
        if club is None or opp is None:
            self.report.warn(f"{where}: no club or opponent; the row was skipped")
            return None
        side = cell_text(row["ha"])
        if side not in ("H", "A"):
            self.report.warn(f"{where}: H/A is {side!r}; the row was skipped")
            return None
        home, away = (club, opp) if side == "H" else (opp, club)
        game = games.get(code)
        if game is None:
            game = games[code] = _ParsedGame(round_number, code, None, when, home, away)
        elif (game.home, game.away, game.date) != (home, away, when):
            game.issues.append(f"{where}: disagrees with earlier rows about the matchup or date")
        result = cell_text(row["result"])
        match = _RESULT_RE.match(result) if result else None
        if match:
            mine, theirs = int(match.group(2)), int(match.group(3))
            if (match.group(1) == "W") != (mine > theirs):
                game.issues.append(f"{where}: result {result!r} contradicts its own score")
            finals = (mine, theirs) if side == "H" else (theirs, mine)
            if game.final_home is None:
                game.final_home, game.final_away = finals
            elif (game.final_home, game.final_away) != finals:
                game.issues.append(f"{where}: result {result!r} disagrees with the other side's")
        elif result:
            game.issues.append(f"{where}: result {result!r} is not 'W 83-77'")
        return game

    def _stats(self, row: dict[str, Any], where: str, game: _ParsedGame) -> dict[str, int | None]:
        out: dict[str, int | None] = {}
        for key in _COUNT_KEYS:
            value = row[key]
            if value is None:
                out[key] = None
                continue
            parsed = cell_int(value)
            if parsed is None or parsed < 0:
                game.issues.append(f"{where}: {_BOX_HEADERS[key]} is not a whole number")
                parsed = None
            out[key] = parsed
        pir = row["pir"]
        parsed_pir = None if pir is None else cell_int(pir)
        if pir is not None and parsed_pir is None:
            game.issues.append(f"{where}: PIR is not a whole number")
        out["pir_official"] = parsed_pir
        return out

    def _side(self, game: _ParsedGame, row: dict[str, Any], where: str) -> str:
        # _game_for_row has already resolved this row's club, so it is never blank here
        return "home" if self.club(row["club"], where) == game.home else "away"

    def _player_line(
        self, row: dict[str, Any], game: _ParsedGame, sheet: str, n: int
    ) -> Line | None:
        where = f"{sheet} row {n}"
        name = cell_text(row["who"])
        if name is None:
            game.issues.append(f"{where}: a line with no player name")
            return None
        side = self._side(game, row, where)
        minutes = row["min"]
        text = cell_text(minutes)
        if text is not None and text.upper() == "DNP":
            # The sheet writes zeros for a player who did not play; they are not stats, so
            # the line is a ``dnp`` with every stat NULL and the zeros are never even read.
            return Line(side=side, name=name, participation="dnp")
        stats = self._stats(row, where, game)
        number = cell_number(minutes)
        if number is None:
            if all(v is None for v in stats.values()):
                return Line(side=side, name=name, participation="dnp")
            game.issues.append(f"{where}: MIN is not a number or DNP")
            return Line(side=side, name=name, **stats)  # seconds unknown; checks will skip it
        if number < 0 or number > 70:
            game.issues.append(f"{where}: MIN {number} is outside a game")
            return Line(side=side, name=name, **stats)
        seconds = round(number * 60)
        if seconds == 0:
            if any(v for v in stats.values() if v is not None):
                game.issues.append(f"{where}: no minutes but a stat line")
            return Line(side=side, name=name, participation="dnp")
        return Line(side=side, name=name, seconds=seconds, **stats)

    def _team_line(self, row: dict[str, Any], game: _ParsedGame, sheet: str, n: int) -> None:
        where = f"{sheet} row {n}"
        side = self._side(game, row, where)
        stats = self._stats(row, where, game)
        minutes = cell_number(row["min"])
        game.teams[side] = Line(
            side=side,
            name=cell_text(row["who"]),
            seconds=round(minutes * 60) if minutes is not None and minutes >= 0 else None,
            **stats,
        )

    # -------------------------------------------------------------------- reviews

    def _review(self, sheet: Sheet, round_number: int) -> list[dict[str, Any]] | None:
        table = self._table(sheet, _REVIEW_COLUMNS, "review")
        if table is None:
            return None
        self._note_ignored(table)
        out: list[dict[str, Any]] = []
        rows = 0
        for number, row in table.rows(stop_when_blank="num"):
            rows += 1
            where = f"{sheet.name} row {number}"
            home = self.club(row["home"], where)
            away = self.club(row["away"], where)
            if home is None or away is None:
                self.report.warn(f"{where}: a club is not named yet; the row was skipped")
                continue
            quarters = cell_text(row.get("quarters"))
            home_partials = away_partials = None
            if quarters:
                pieces = [_PERIOD_RE.match(piece) for piece in quarters.split(",")]
                if all(pieces):
                    home_partials = [int(m.group(1)) for m in pieces if m]
                    away_partials = [int(m.group(2)) for m in pieces if m]
                else:
                    self.report.warn(
                        f"{where}: quarters {quarters!r} are not 'a-b, c-d, ...'; ignored"
                    )
            venue, marked_neutral = _clean_venue(cell_text(row.get("venue")))
            out.append(
                {
                    "round": round_number,
                    "number": cell_int(row["num"]),
                    "home": home,
                    "away": away,
                    "date": _as_date(row.get("date")),
                    "proj_home": _nonneg(row["proj_home"]),
                    "proj_away": _nonneg(row["proj_away"]),
                    "res_home": cell_int(row.get("res_home")),
                    "res_away": cell_int(row.get("res_away")),
                    "home_partials": home_partials,
                    "away_partials": away_partials,
                    "venue": venue,
                    "is_neutral": marked_neutral if venue is not None else None,
                    "attendance": cell_int(row.get("attendance")),
                }
            )
        self._outcome(sheet.name, "review", "imported", rows, "projections kept as scores only")
        return out

    def _merge_reviews(
        self, games: list[_ParsedGame], reviews: list[tuple[int, list[dict[str, Any]]]]
    ) -> None:
        index = {(g.round_number, g.home, g.away): g for g in games}
        for round_number, rows in reviews:
            for item in rows:
                game = index.get((round_number, item["home"], item["away"]))
                if game is None:
                    if (
                        item["date"] is None
                        or item["res_home"] is None
                        or item["res_away"] is None
                        or item["number"] is None
                    ):
                        self.report.warn(
                            f"round {round_number} review of {item['home']} v {item['away']}: "
                            "no box score and no readable result; nothing stored for it"
                        )
                        continue
                    game = _ParsedGame(
                        round_number,
                        None,
                        item["number"],
                        item["date"],
                        item["home"],
                        item["away"],
                        final_home=item["res_home"],
                        final_away=item["res_away"],
                    )
                    games.append(game)
                    index[(round_number, item["home"], item["away"])] = game
                elif (
                    item["res_home"] is not None
                    and item["res_away"] is not None
                    and (item["res_home"], item["res_away"]) != (game.final_home, game.final_away)
                ):
                    self.report.warn(
                        f"round {round_number} {item['home']} v {item['away']}: the review's "
                        "result differs from the box score's; the box score's is used"
                    )
                game.home_partials = item["home_partials"]
                game.away_partials = item["away_partials"]
                game.venue = item["venue"]
                game.is_neutral = item["is_neutral"]
                game.attendance = item["attendance"]
                game.proj_home, game.proj_away = item["proj_home"], item["proj_away"]

    def _finalise(self, game: _ParsedGame) -> None:
        facts = GameFacts(
            final_home=game.final_home,
            final_away=game.final_away,
            players=game.players,
            teams=game.teams,
            home_partials=game.home_partials,
            away_partials=game.away_partials,
            seconds_tolerance=3,
        )
        game.check = check_game(facts)

    # -------------------------------------------------------------------- fixtures

    def _fixtures(self, sheet: Sheet, round_number: int) -> int:
        table = self._table(sheet, _FIXTURE_COLUMNS, "fixtures")
        if table is None:
            return 0
        self._note_ignored(table)
        rows = 0
        for number, row in table.rows(stop_when_blank="num"):
            rows += 1
            where = f"{sheet.name} row {number}"
            home = self.club(row["home"], where)
            away = self.club(row["away"], where)
            if home is None or away is None:
                self.report.warn(f"{where}: a club is not named yet; the fixture was skipped")
                continue
            when = _as_date(row["date"])
            ordinal = cell_int(row["num"])
            if when is None or ordinal is None or ordinal < 1:
                self.report.warn(f"{where}: no readable date and number; skipped")
                continue
            tip = parse_tipoff(when, row.get("tip"))
            if tip is None:
                self.report.warn(
                    f"{where}: tip-off {cell_text(row.get('tip'))!r} not readable; left unknown"
                )
            venue, marked_neutral = _clean_venue(cell_text(row.get("venue")))
            advantage = cell_number(row.get("home_adv"))
            if venue is None and advantage is None:
                neutral: bool | None = None
            else:
                neutral = bool(marked_neutral and advantage == 0)
                if marked_neutral and advantage != 0:
                    self.report.warn(
                        f"{where}: venue says neutral but home advantage is {advantage}"
                    )
            self.out.fixtures.append(
                _Fixture(round_number, ordinal, when, home, away, tip, venue, neutral, advantage)
            )
        self._outcome(
            sheet.name, "fixtures", "imported", rows, "columns A-I only; no projections read"
        )
        return 1

    # -------------------------------------------------------------------- injuries

    def _injuries(self, sheet: Sheet) -> int:
        table = self._table(sheet, _INJURY_COLUMNS, "injuryReport")
        if table is None:
            return 0
        for r in range(1, min(table.header_row, 6)):
            for value in sheet.row(r).values():
                text = cell_text(value)
                match = _AS_OF_RE.search(text) if text else None
                month = _MONTHS.get(match.group(2).lower()) if match else None
                if match and month:
                    try:
                        self.out.status_as_of = date(
                            int(match.group(3)), month, int(match.group(1))
                        )
                    except ValueError:
                        pass
        rows = 0
        for number, row in table.rows(stop_when_blank="club"):
            rows += 1
            code = self.club(row["club"], f"Injury Report row {number}")
            if code is None:
                continue
            name = cell_text(row["name"])
            raw_status = cell_text(row["status"])
            try:
                status = normalise_status(raw_status) if raw_status else None
            except InvalidStatusError:
                status = None
            published = _as_date(row["published"])
            if name is None or status is None or published is None:
                reason = (
                    "no player name"
                    if name is None
                    else (
                        f"status {raw_status!r} is not one of the five"
                        if status is None
                        else "no readable source date"
                    )
                )
                self.report.rejected_statuses.append(f"row {number} ({name or '?'}): {reason}")
                continue
            model_raw = cell_text(row.get("model"))
            try:
                model_status = normalise_status(model_raw) if model_raw else None
            except InvalidStatusError:
                model_status = None
                self.report.warn(
                    f"Injury Report row {number}: 'In model' {model_raw!r} is not a status"
                )
            self.out.statuses.append(
                _Status(
                    row=number,
                    code=code,
                    name=name,
                    status=status,
                    status_raw=raw_status or "",
                    model_status=model_status,
                    problem=cell_text(row.get("problem")),
                    expected=cell_text(row.get("expected")),
                    url=cell_text(row.get("source")),
                    published=published,
                )
            )
        self._outcome(
            "Injury Report", "injuryReport", "imported", rows, "free-form sections skipped"
        )
        return 1

    # -------------------------------------------------------------------- season

    def _season(self) -> None:
        dates = [g.date for g in self.out.games] + [f.date for f in self.out.fixtures]
        from_dates = None
        if dates:
            first = min(dates)
            from_dates = first.year if first.month >= 7 else first.year - 1
        from_title = None
        if self.wb.has_sheet("Start Here"):
            sheet = self.wb.sheet("Start Here")
            for r in range(1, min(sheet.max_row, 8) + 1):
                for value in sheet.row(r).values():
                    text = cell_text(value)
                    match = _SEASON_TITLE_RE.search(text) if text else None
                    if match and (int(match.group(1)) + 1) % 100 == int(match.group(2)):
                        from_title = int(match.group(1))
                        break
                if from_title:
                    break
        if from_dates and from_title and from_dates != from_title:
            raise ImportAborted(
                f"the game dates say the season starts in {from_dates} but the title says "
                f"{from_title}; nothing was imported"
            )
        year = from_dates or from_title
        if year is None:
            raise ImportAborted(
                "cannot tell which season this workbook is for; nothing was imported"
            )
        self.out.season_start_year = year


def _find_table(
    sheet: Sheet, columns: Sequence[Column], *, first_row: int = 1, last_row: int | None = None
) -> Table | None:
    """:meth:`Sheet.find_table`, tolerant of the season-stamped ratings headers.

    The ratings sheet names its prior columns after the season they summarise (``PF/g
    2025-26``), which changes every year, so those two headers match by pattern.
    """
    patterns = {"PF/g <season>": r"PF/g \d{4}-\d{2}", "PA/g <season>": r"PA/g \d{4}-\d{2}"}
    if not any(c.header in patterns for c in columns):
        return sheet.find_table(columns, first_row=first_row, last_row=last_row)
    end = last_row if last_row is not None else sheet.max_row
    for number in range(first_row, end + 1):
        cells = sheet.cells.get(number)
        if not cells:
            continue
        resolved: list[Column] = []
        for column in columns:
            if column.header in patterns:
                regex = re.compile(patterns[column.header])
                text = next(
                    (t for v in cells.values() if (t := cell_text(v)) and regex.fullmatch(t)), None
                )
                if text is None:
                    break
                resolved.append(Column(column.key, text, column.occurrence, column.required))
            else:
                resolved.append(column)
        else:
            table = sheet.find_table(resolved, first_row=number, last_row=number)
            if table is not None:
                return table
    return None


# ------------------------------------------------------------------------------ apply stage


def _set(obj: Any, values: Mapping[str, Any], *, fill_only: bool = False) -> bool:
    """Assign ``values`` to ``obj``; report whether anything changed. With ``fill_only`` an
    attribute is written only where it is currently ``None``."""
    changed = False
    for key, value in values.items():
        current = getattr(obj, key)
        if fill_only:
            if current is None and value is not None:
                setattr(obj, key, value)
                changed = True
        elif current != value:
            setattr(obj, key, value)
            changed = True
    return changed


@dataclass
class _Ctx:
    session: Session
    now: datetime
    season: SeasonRef
    sha: str
    crosswalk: ClubCrosswalk
    report: WorkbookReport
    as_of_round: int
    include_estimates: bool
    hca: float
    dirty: bool = False
    rows: int = 0
    person_index: dict[tuple[str, str], str] = field(default_factory=dict)
    alias_map: dict[str, str] = field(default_factory=dict)
    game_ids: dict[tuple[int, str, str], str] = field(default_factory=dict)

    @property
    def data_source(self) -> str:
        return workbook_data_source(self.sha)

    def wrote(self, name: str | None, n: int = 1) -> None:
        """Note that ``n`` rows were written (and, with a ``name``, count them in the report)."""
        self.dirty = True
        self.rows += n
        if name:
            self.report.count(name, n)


def _apply_season(ctx: _Ctx) -> None:
    session, ref = ctx.session, ctx.season
    seasons = {s.season_code: s for s in session.execute(select(ElSeason)).scalars()}
    row = seasons.get(ref.code)
    newest = max([s.start_year for s in seasons.values()] + [ref.start_year])
    if row is None:
        session.add(
            ElSeason(
                season_code=ref.code,
                competition_code=ref.competition_code,
                label=ref.label,
                start_year=ref.start_year,
                is_current=ref.start_year == newest,
            )
        )
        ctx.wrote("seasons")
    for other in seasons.values():
        current = other.start_year == newest
        if other.is_current != current:
            other.is_current = current
            ctx.dirty = True
    if row is not None and row.is_current != (ref.start_year == newest):
        row.is_current = ref.start_year == newest
        ctx.dirty = True
    session.flush()


def _apply_clubs(ctx: _Ctx, parsed: _Parsed) -> None:
    session = ctx.session
    ratings = {r.code: r for r in parsed.ratings}
    venues: dict[str, str] = {}
    for fixture in parsed.fixtures:
        if fixture.venue and fixture.is_neutral is False:
            venues.setdefault(fixture.home, fixture.venue)
    for game in parsed.games:
        if game.venue and game.is_neutral is False:
            venues.setdefault(game.home, game.venue)
    for workbook_code, official in sorted(parsed.club_codes.items()):
        row = ctx.crosswalk.by_workbook(workbook_code)
        assert row is not None
        rating = ratings.get(official)
        values = {
            "tv_code": row.tv_code,
            "short_name": row.short_name,
            "country_code": rating.country if rating else None,
        }
        club = session.get(ElClub, official)
        if club is None:
            club = ElClub(club_code=official, name=row.name, **values)
            session.add(club)
            ctx.wrote("clubs")
        elif _set(club, values, fill_only=True):
            ctx.wrote("clubsUpdated")
        for system, code in (
            ("workbook", workbook_code),
            ("official", official),
            ("tv", row.tv_code),
        ):
            if code is None:
                continue
            alias = session.get(ElClubAlias, (system, code))
            if alias is None:
                session.add(ElClubAlias(system=system, code=code, club_code=official))
                ctx.wrote("aliases")
            elif alias.club_code != official:
                raise ImportAborted(
                    f"club alias ({system}, {code}) already points at {alias.club_code}, not "
                    f"{official}; nothing was imported"
                )
        season_row = session.get(ElClubSeason, (ctx.season.code, official))
        coach = rating.coach if rating else None
        venue = venues.get(official)
        if season_row is None:
            session.add(
                ElClubSeason(
                    season_code=ctx.season.code,
                    club_code=official,
                    coach_name=coach,
                    home_venue_name=venue,
                    home_venue_tz=None,
                )
            )
            ctx.wrote("clubSeasons")
        elif _set(season_row, {"coach_name": coach, "home_venue_name": venue}, fill_only=True):
            ctx.wrote("clubSeasonsUpdated")
    session.flush()


def _load_person_index(ctx: _Ctx) -> None:
    session = ctx.session
    for alias in session.execute(select(ElPersonAlias)).scalars():
        ctx.alias_map[alias.wb_code] = alias.official_code
    rows = session.execute(
        select(ElRegistration.club_code, ElRegistration.person_code, ElPerson.name)
        .join(ElPerson, ElPerson.person_code == ElRegistration.person_code)
        .where(ElRegistration.season_code == ctx.season.code)
    )
    for club, person, name in rows:
        ctx.person_index.setdefault((club, fold_name(name)), person)


def _person_for(ctx: _Ctx, club: str, name: str, create: bool = True) -> str | None:
    """The person code for a squad member, minting a workbook one when there is none."""
    folded = fold_name(name)
    key = (club, folded)
    code = ctx.person_index.get(key)
    if code is not None:
        return ctx.alias_map.get(code, code)
    if not create:
        return None
    attempt = 0
    while True:
        minted = workbook_person_code(club, folded, attempt)
        if minted in ctx.alias_map:
            # Reconciliation already replaced this minted person with an official code: use
            # that, and do not mint the old one again.
            ctx.person_index[key] = minted
            return ctx.alias_map[minted]
        existing = ctx.session.get(ElPerson, minted)
        if existing is None:
            ctx.session.add(ElPerson(person_code=minted, code_system="workbook", name=name[:96]))
            ctx.wrote("persons")
            break
        if fold_name(existing.name) == folded:
            break
        attempt += 1
    ctx.person_index[key] = minted
    return ctx.alias_map.get(minted, minted)


def _apply_squads(ctx: _Ctx, parsed: _Parsed) -> None:
    session = ctx.session
    for squad in parsed.squads:
        club = squad.code  # the parser already resolved it to the official code
        person = _person_for(ctx, club, squad.name)
        assert person is not None
        reg = session.get(ElRegistration, (ctx.season.code, club, person))
        values = {
            "position5_workbook": squad.pos5,
            "role_workbook": squad.role,
            "age_workbook": squad.age,
            "active": True,
        }
        if reg is None:
            session.add(
                ElRegistration(
                    season_code=ctx.season.code,
                    club_code=club,
                    person_code=person,
                    source="workbook",
                    **values,
                )
            )
            ctx.wrote("registrations")
        elif reg.source == "workbook" and _set(reg, values):
            ctx.wrote("registrationsUpdated")
        official_basis = squad.basis == _OFFICIAL_BASIS
        if official_basis:
            basis, prior = "workbookOfficial", 400.0
        elif ctx.include_estimates:
            basis, prior = "workbookEstimate", 150.0
        else:
            basis, prior = "positionPrior", 150.0
        rates = dict(squad.rates)
        if basis == "positionPrior":
            rates = {k: None for k in rates}
        rate_values = {
            "club_code": club,
            "proj_minutes": squad.minutes,
            "basis": basis,
            "prior_minutes": prior,
            **rates,
        }
        rate = session.get(ElPlayerRate, (ctx.season.code, person, ctx.as_of_round))
        if rate is None:
            session.add(
                ElPlayerRate(
                    season_code=ctx.season.code,
                    person_code=person,
                    as_of_round=ctx.as_of_round,
                    **rate_values,
                )
            )
            ctx.wrote("rates")
        elif _set(rate, rate_values):
            ctx.wrote("ratesUpdated")
    session.flush()


def _apply_ratings(ctx: _Ctx, parsed: _Parsed) -> None:
    session = ctx.session
    for rating in parsed.ratings:
        values = {
            "pf_prior": rating.pf,
            "pa_prior": rating.pa,
            "prior_is_estimate": rating.is_estimate,
            "attack_adj": rating.attack,
            "defence_adj": rating.defence,
            "source": "workbookImport",
        }
        key = (ctx.season.code, rating.code, ctx.as_of_round)
        row = session.get(ElTeamRating, key)
        if row is None:
            session.add(
                ElTeamRating(
                    season_code=key[0],
                    club_code=key[1],
                    as_of_round=key[2],
                    computed_at=ctx.now,
                    **values,
                )
            )
            ctx.wrote("ratings")
        elif row.source == "workbookImport" and _set(row, values):
            row.computed_at = ctx.now
            ctx.wrote("ratingsUpdated")
    session.flush()


def _find_by_matchup(ctx: _Ctx, round_number: int, home: str, away: str) -> ElGame | None:
    return (
        ctx.session.execute(
            select(ElGame).where(
                ElGame.season_code == ctx.season.code,
                ElGame.round_number == round_number,
                ElGame.home_club_code == home,
                ElGame.away_club_code == away,
            )
        )
        .scalars()
        .first()
    )


def _rekey_game(session: Session, old: str, new: str) -> None:
    """Move every reference to a provisional game id to the official one (no row is lost)."""
    session.flush()
    for model in (ElPlayerGame, ElTeamGame, ElProjectionLedger, ElIntelStatus, ElIntelOverride):
        session.execute(update(model).where(model.game_id == old).values(game_id=new))
    session.execute(update(ElGame).where(ElGame.game_id == old).values(game_id=new))
    session.expire_all()


def _digest(game: _ParsedGame, home_code: str, away_code: str) -> str:
    def line(entry: Line) -> dict[str, Any]:
        return {k: v for k, v in sorted(vars(entry).items())}

    payload = {
        "home": home_code,
        "away": away_code,
        "final": [game.final_home, game.final_away],
        "partials": [game.home_partials, game.away_partials],
        "players": sorted(
            (line(p) for p in game.players), key=lambda d: (d["side"], d["name"] or "")
        ),
        "teams": {side: line(entry) for side, entry in sorted(game.teams.items())},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


_LINE_FIELDS: Final = (
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
    "plus_minus",
    "pir_official",
)


def _line_values(line: Line) -> dict[str, Any]:
    out = {
        name: getattr(line, "seconds" if name == "seconds_played" else name)
        for name in _LINE_FIELDS
    }
    return out


def _apply_games(ctx: _Ctx, parsed: _Parsed) -> None:
    session, report = ctx.session, ctx.report
    for game in parsed.games:
        home, away = game.home, game.away  # official codes, resolved by the parser
        check = game.check
        assert check is not None
        for violation in check.soft:
            report.soft_violations += 1
            report.warn(f"round {game.round_number} {game.home} v {game.away}: {violation.message}")
        gid = (
            official_game_id(ctx.season.code, game.game_code)
            if game.game_code is not None
            else provisional_game_id(ctx.season.code, game.round_number, game.number or 0)
        )
        existing = session.get(ElGame, gid)
        if existing is None:
            same = _find_by_matchup(ctx, game.round_number, home, away)
            if same is not None:
                if same.data_source == "euroleague-v2":
                    report.count("skippedOfficial")
                    continue
                if game.game_code is not None and same.game_code is None:
                    _rekey_game(session, same.game_id, gid)
                    existing = session.get(ElGame, gid)
                    report.count("gamesRekeyed")
                    ctx.dirty = True
                elif same.game_id != gid:
                    report.warn(f"{gid}: matches {same.game_id} by round and clubs; left alone")
                    continue
        if existing is not None and existing.data_source == "euroleague-v2":
            report.count("skippedOfficial")
            continue
        ctx.game_ids[(game.round_number, home, away)] = gid

        hard = check.hard
        quarantined = bool(hard or game.issues)
        if quarantined:
            rules = sorted({v.rule for v in hard} | ({"unreadable"} if game.issues else set()))
            report.quarantined.append({"gameId": gid, "rules": rules})
            for violation in hard:
                report.warn(f"{gid}: {violation.rule}: {violation.message}")
            for issue in game.issues:
                report.warn(f"{gid}: {issue}")
        stats_status = "quarantined" if quarantined else ("ok" if game.has_lines else "none")
        if quarantined and existing is not None and existing.stats_status == "ok":
            # A file that contradicts itself must not rewrite what an earlier, consistent file
            # established: the game is only flagged, and its score, partials and lines stay as
            # they were until a consistent file arrives.
            existing.stats_status = "quarantined"
            report.count("gamesQuarantined")
            ctx.wrote(None)
            continue
        digest = _digest(game, home, away)
        values: dict[str, Any] = {
            "season_code": ctx.season.code,
            "game_code": game.game_code,
            "round_number": game.round_number,
            "phase_code": DEFAULT_PHASE,
            "game_date": game.date,
            "home_club_code": home,
            "away_club_code": away,
            "status": "final",
            "home_pts": game.final_home,
            "away_pts": game.final_away,
            "stats_status": stats_status,
            "raw_sha256": digest,
        }
        if game.home_partials is not None and not any(v.rule == "partials" for v in hard):
            values["home_partials_json"] = json.dumps(game.home_partials)
            values["away_partials_json"] = json.dumps(game.away_partials)
        if check.ot_periods is not None:
            values["ot_periods"] = check.ot_periods
        if game.attendance is not None:
            values["attendance"] = game.attendance
        if game.venue is not None:
            values["venue_name"] = game.venue
        if game.is_neutral is not None:
            values["is_neutral"] = game.is_neutral
        if existing is None:
            session.add(
                ElGame(game_id=gid, data_source=ctx.data_source, ingested_at=ctx.now, **values)
            )
            report.count("gamesFinal")
            ctx.wrote(None)
            changed = True
        else:
            # A newer workbook carries the same rounds again. Only a change of *content*
            # counts as a write; the file that first introduced identical content stays the
            # row's data_source, so re-importing a longer workbook rewrites nothing old.
            changed = _set(existing, values)
            if changed:
                existing.data_source = ctx.data_source
                existing.ingested_at = ctx.now
                report.count("gamesUpdated")
                ctx.wrote(None)
        if quarantined:
            report.count("gamesQuarantined")
            continue  # previous good rows (if any) are kept; nothing is written from this file
        if game.has_lines and changed:
            _write_lines(ctx, gid, game, home, away)
    session.flush()


def _write_lines(ctx: _Ctx, gid: str, game: _ParsedGame, home: str, away: str) -> None:
    session = ctx.session
    session.execute(delete(ElPlayerGame).where(ElPlayerGame.game_id == gid))
    session.execute(delete(ElTeamGame).where(ElTeamGame.game_id == gid))
    clubs = {"home": home, "away": away}
    seen: set[str] = set()
    for line, name in zip(game.players, game.player_names):
        club = clubs[line.side]
        person = _person_for(ctx, club, name)
        assert person is not None
        if person in seen:
            ctx.report.warn(
                f"{gid}: {name!r} appears twice on one side; the second line is dropped"
            )
            continue
        seen.add(person)
        stats = (
            _line_values(line)
            if line.participation == "played"
            else {f: None for f in _LINE_FIELDS}
        )
        session.add(
            ElPlayerGame(
                game_id=gid,
                person_code=person,
                club_code=club,
                participation=line.participation,
                is_starter=line.is_starter,
                **stats,
            )
        )
        ctx.wrote("dnpLines" if line.participation == "dnp" else "playerLines")
    for side in ("home", "away"):
        team = game.teams.get(side)
        if team is None:
            continue
        scored = team.pts
        other = game.teams.get("away" if side == "home" else "home")
        final = (
            (game.final_home, game.final_away)
            if side == "home"
            else (game.final_away, game.final_home)
        )
        pts = scored if scored is not None else final[0]
        opp_pts = final[1] if final[1] is not None else (other.pts if other else None)
        if pts is None or opp_pts is None:
            ctx.report.warn(f"{gid}: {side} team line has no points; not stored")
            continue
        stats = _line_values(team)
        stats.pop("pts")
        session.add(
            ElTeamGame(
                game_id=gid,
                club_code=clubs[side],
                is_home=side == "home",
                opp_club_code=clubs["away" if side == "home" else "home"],
                won=pts > opp_pts,
                pts=pts,
                opp_pts=opp_pts,
                **stats,
            )
        )
        ctx.wrote("teamLines")


def _apply_imported_ledger(ctx: _Ctx, parsed: _Parsed) -> None:
    session = ctx.session
    for game in parsed.games:
        if game.proj_home is None or game.proj_away is None:
            continue
        gid = ctx.game_ids.get((game.round_number, game.home, game.away))
        if gid is None:
            continue
        row = (
            session.execute(
                select(ElProjectionLedger).where(
                    ElProjectionLedger.game_id == gid, ElProjectionLedger.kind == "imported"
                )
            )
            .scalars()
            .first()
        )
        values = {"home_pts": game.proj_home, "away_pts": game.proj_away}
        if row is None:
            session.add(
                ElProjectionLedger(
                    game_id=gid,
                    kind="imported",
                    model_version="workbook",
                    cap_policy="workbook",
                    **values,
                )
            )
            ctx.wrote("ledgerImported")
        elif _set(row, values):
            ctx.wrote("ledgerImportedUpdated")
    session.flush()


def _apply_fixtures(ctx: _Ctx, parsed: _Parsed) -> None:
    session, report = ctx.session, ctx.report
    for fixture in parsed.fixtures:
        home, away = fixture.home, fixture.away
        existing = _find_by_matchup(ctx, fixture.round_number, home, away)
        override = None
        if fixture.is_neutral is not True and fixture.home_adv is not None:
            if abs(fixture.home_adv - ctx.hca) > 1e-9:
                override = fixture.home_adv
        facts = {
            "tipoff_utc": fixture.tipoff,
            "venue_name": fixture.venue,
            "is_neutral": fixture.is_neutral,
            "home_advantage_override": override,
        }
        if existing is not None:
            if existing.data_source == "euroleague-v2":
                report.count("skippedOfficial")
                continue
            if existing.status == "final":
                changed = _set(existing, facts, fill_only=True)
            else:
                # A scheduled game's facts can legitimately change (a moved tip-off), so a
                # newer workbook overwrites them; a value it does not state is left alone.
                newer = {k: v for k, v in facts.items() if v is not None}
                if fixture.home_adv is not None:
                    newer["home_advantage_override"] = override
                changed = _set(existing, {**newer, "game_date": fixture.date})
                if changed:
                    existing.data_source = ctx.data_source
            if changed:
                ctx.wrote("fixturesUpdated")
            continue
        gid = provisional_game_id(ctx.season.code, fixture.round_number, fixture.number)
        if session.get(ElGame, gid) is not None:
            report.warn(
                f"{gid}: that fixture id is taken by another matchup; the fixture was skipped"
            )
            continue
        session.add(
            ElGame(
                game_id=gid,
                season_code=ctx.season.code,
                game_code=None,
                round_number=fixture.round_number,
                phase_code=DEFAULT_PHASE,
                game_date=fixture.date,
                home_club_code=home,
                away_club_code=away,
                status="scheduled",
                stats_status="none",
                data_source=ctx.data_source,
                ingested_at=ctx.now,
                **facts,
            )
        )
        report.count("gamesScheduled")
        ctx.wrote(None)
    session.flush()


def _host_label(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        host = ""
    host = host.lower()
    return (host[4:] if host.startswith("www.") else host) or "workbook"


def _apply_statuses(ctx: _Ctx, parsed: _Parsed) -> None:
    session, report = ctx.session, ctx.report
    if not parsed.statuses:
        return
    known: set[tuple[Any, ...]] = set()
    for row in session.execute(
        select(ElIntelStatus).where(
            ElIntelStatus.source_kind.in_(("workbookImport", "boxScoreInference"))
        )
    ).scalars():
        known.add(
            _status_key(
                row.club_code,
                row.player_name_raw,
                row.status,
                row.model_status,
                row.source_url,
                row.source_published_at,
                row.expected_return_text,
                row.reason_text,
            )
        )
    batch: ElIntelBatch | None = None
    added = 0
    for item in parsed.statuses:
        club = item.code
        published = datetime(item.published.year, item.published.month, item.published.day)
        as_of = parsed.status_as_of or item.published
        url = (
            item.url if item.url and item.url.lower().startswith(("http://", "https://")) else None
        )
        if item.url and url is None:
            report.warn(
                f"Injury Report row {item.row}: the source is not a web link; kept as no link"
            )
        if url and "live.euroleague.net/api/boxscore" in url.lower():
            kind, label = "boxScoreInference", "EuroLeague box score"
        elif url:
            kind, label = "workbookImport", _host_label(url)
        else:
            kind, label = "manual", "Workbook (no source link)"
        url, label = screen_source_link(url, label)
        expected = item.expected[:120] if item.expected else None
        reason = item.problem[:200] if item.problem else None
        key = _status_key(
            club, item.name, item.status, item.model_status, url, published, expected, reason
        )
        if key in known:
            report.count("statusesUnchanged")
            continue
        known.add(key)
        person = _person_for(ctx, club, item.name, create=False)
        if person is None:
            report.count("statusesUnmatched")
            report.unmatched_players.append(f"{item.code}: {item.name}")
        parsed_return = parse_expected_return(expected, source_date=item.published)
        if batch is None:
            batch = ElIntelBatch(
                source_kind="workbookImport",
                label=f"Workbook injury report {ctx.sha[:8]}",
                file_sha256=ctx.sha,
                imported_at=ctx.now,
                row_count=0,
            )
            session.add(batch)
            session.flush()
        session.add(
            ElIntelStatus(
                club_code=club,
                person_code=person,
                player_name_raw=item.name[:96],
                status=item.status,
                status_raw=item.status_raw[:48],
                model_status=item.model_status,
                reason_category=classify_reason(item.problem, item.expected, item.status),
                reason_text=reason,
                expected_return_text=expected,
                expected_return_round_from=parsed_return.round_from,
                expected_return_round_to=parsed_return.round_to,
                expected_return_date=parsed_return.date,
                source_kind=kind,
                source_label=label[:160],
                source_url=url,
                source_published_at=published,
                as_of=datetime(as_of.year, as_of.month, as_of.day),
                recorded_at=ctx.now,
                batch_id=batch.batch_id,
                provenance_note=f"Injury Report row {item.row}",
            )
        )
        added += 1
    if batch is not None:
        batch.row_count = added
    if added:
        ctx.wrote("statuses", added)
    session.flush()


def _status_key(
    club: str,
    name: str,
    status: Any,
    model: Any,
    url: Any,
    published: Any,
    expected: Any,
    reason: Any,
) -> tuple[Any, ...]:
    return (club, fold_name(name), status, model, url, published, expected, reason)


def _apply_settings(ctx: _Ctx, parsed: _Parsed) -> None:
    for key, value, provenance in parsed.settings:
        try:
            if apply_imported_setting(ctx.session, key, value, provenance, ctx.now):
                ctx.wrote("settings")
        except InvalidSettingError as exc:
            ctx.report.warn(f"setting {key} not stored: {exc}")


def _data_through(parsed: _Parsed) -> date | None:
    dates = [g.date for g in parsed.games]
    return max(dates) if dates else None


# ------------------------------------------------------------------------------ the entry point


def workbook_imported(session: Session, sha256: str) -> bool:
    """True when a file with this hash has already been imported successfully."""
    return (
        session.execute(
            select(ElIngestLog.id)
            .where(
                ElIngestLog.job == "workbook",
                ElIngestLog.source_sha256 == sha256,
                ElIngestLog.status == "ok",
            )
            .limit(1)
        ).first()
        is not None
    )


def last_import_failed(session: Session, sha256: str) -> str | None:
    """The error of the latest failed attempt on this file, or ``None`` (never failed, or
    since succeeded). The inbox scan uses it to avoid retrying a broken file every tick."""
    if workbook_imported(session, sha256):
        return None
    row = session.execute(
        select(ElIngestLog.error)
        .where(
            ElIngestLog.job == "workbook",
            ElIngestLog.source_sha256 == sha256,
            ElIngestLog.status == "failed",
        )
        .order_by(ElIngestLog.id.desc())
        .limit(1)
    ).first()
    return None if row is None else (row[0] or "failed")


def import_workbook(
    source: str | Path | bytes,
    engine: Engine,
    *,
    dry_run: bool = False,
    include_estimates: bool = True,
    crosswalk: ClubCrosswalk | None = None,
    now: datetime | None = None,
    file_name: str | None = None,
) -> WorkbookReport:
    """Import one workbook into the store behind ``engine`` and return what happened.

    Nothing is raised for a bad file: the report's ``status`` is ``failed`` or ``aborted`` and
    its ``error`` says why. ``dry_run`` runs the whole import and rolls it back. The store
    must already have its tables (``init_el_db``); it is stamped ``workbook`` if it has no
    identity, and a synthetic store refuses.
    """
    report = WorkbookReport(dry_run=dry_run)
    report.file_name = file_name or (Path(source).name if not isinstance(source, bytes) else "")
    moment = now or utcnow()
    try:
        if isinstance(source, bytes):
            data = source
        else:
            size = Path(source).stat().st_size
            if size > _xlsx.MAX_FILE_BYTES:
                report.size = size
                report.status = "failed"
                report.error = f"the file is {size} bytes; the limit is {_xlsx.MAX_FILE_BYTES}"
                return report
            data = Path(source).read_bytes()
    except OSError as exc:
        report.status, report.error = "failed", f"cannot read the file: {exc}"
        return report
    report.size = len(data)
    report.sha256 = hashlib.sha256(data).hexdigest()
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    with factory() as session:
        try:
            if workbook_imported(session, report.sha256):
                report.status = "alreadyImported"
                return report
        except Exception as exc:  # noqa: BLE001 - e.g. the tables do not exist yet
            report.status, report.error = "failed", f"the store is not ready: {exc}"
            return report

    try:
        workbook = Workbook.open(data)
        parsed = _Parser(workbook, crosswalk or load_club_codes(), include_estimates, report).run()
    except (UnsafeWorkbook, UnreadableWorkbook, XlsxError) as exc:
        report.status, report.error = "failed", str(exc)
        _log_failure(factory, report, moment)
        return report
    except ImportAborted as exc:
        report.status, report.error = "aborted", str(exc)
        _log_failure(factory, report, moment)
        return report

    assert parsed.season_start_year is not None
    season = season_ref_from_start_year(parsed.season_start_year)
    report.season, report.as_of_round = season.code, parsed.as_of_round
    session = factory()
    try:
        ensure_identity(session, KIND_WORKBOOK, moment)
        ctx = _Ctx(
            session=session,
            now=moment,
            season=season,
            sha=report.sha256,
            crosswalk=crosswalk or load_club_codes(),
            report=report,
            as_of_round=parsed.as_of_round,
            include_estimates=include_estimates,
            hca=parsed.hca,
        )
        _load_person_index(ctx)
        _apply_settings(ctx, parsed)
        _apply_season(ctx)
        _apply_clubs(ctx, parsed)
        _apply_squads(ctx, parsed)
        _apply_ratings(ctx, parsed)
        _apply_games(ctx, parsed)
        _apply_imported_ledger(ctx, parsed)
        _apply_fixtures(ctx, parsed)
        _apply_statuses(ctx, parsed)
        report.count("rowsWritten", ctx.rows)
        if not dry_run:
            if ctx.dirty:
                bump_sync_version(session, _data_through(parsed), None, moment)
            session.add(
                ElIngestLog(
                    started_at=moment,
                    finished_at=utcnow(),
                    job="workbook",
                    status="ok",
                    games_written=sum(
                        report.counts.get(k, 0) for k in ("gamesFinal", "gamesUpdated")
                    ),
                    rows_written=ctx.rows,
                    invariant_failed="; ".join(
                        f"{q['gameId']}: {','.join(q['rules'])}" for q in report.quarantined
                    )
                    or None,
                    source_sha256=report.sha256,
                    detail_json=json.dumps(report.to_dict(), sort_keys=True),
                )
            )
            session.commit()
        else:
            session.rollback()
    except StoreKindMismatch as exc:
        # The refusal is about the store (it holds the invented league), not about the file,
        # so nothing is logged against the file's hash: pointing at a new store retries it.
        session.rollback()
        report.status, report.error = "failed", str(exc)
    except ImportAborted as exc:
        session.rollback()
        report.status, report.error = "aborted", str(exc)
        _log_failure(factory, report, moment)
    except ElStoreError as exc:
        session.rollback()
        report.status, report.error = "failed", str(exc)
        _log_failure(factory, report, moment)
    except Exception as exc:  # noqa: BLE001 - nothing may escape half-written
        session.rollback()
        _LOG.exception("workbook import failed")
        report.status, report.error = "failed", f"{type(exc).__name__}: {exc}"
        _log_failure(factory, report, moment)
    finally:
        session.close()
    return report


def _log_failure(
    factory: "sessionmaker[Session]", report: WorkbookReport, moment: datetime
) -> None:
    """Record a failed attempt so the inbox scan does not retry the same broken file forever.

    A dry run records nothing.
    """
    if report.dry_run:
        return
    with factory() as session:
        try:
            session.add(
                ElIngestLog(
                    started_at=moment,
                    finished_at=utcnow(),
                    job="workbook",
                    status="failed",
                    error=(report.error or "failed")[:2000],
                    source_sha256=report.sha256,
                    detail_json=json.dumps(report.to_dict(), sort_keys=True),
                )
            )
            session.commit()
        except Exception:  # noqa: BLE001 - failing to log must not hide the real failure
            session.rollback()
            _LOG.exception("could not record the failed import")
