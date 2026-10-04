"""Fixtures shared by the EuroLeague tests.

Fixtures other modules (WP-G's and WP-H's tests live beside these) may rely on, by name:

``el_engine``       an empty EuroLeague store: a fresh SQLite file with every ``el_*`` table and no
                    identity stamp (function scope)
``el_session``      a ``Session`` on ``el_engine``, rolled back after each test
``demo_engine``     a store seeded once with the invented league (session scope); treat it as
                    **read-only**, or use ``fresh_demo_engine`` for a copy you may write to
``demo_session``    a read session on ``demo_engine``, rolled back after each test
``fresh_demo_engine``  a new seeded store per test (function scope)
``xl``              a namespace holding ``make`` (the writer below) and the cell wrappers
                    ``Formula``, ``Err`` and ``Rich``
``make_xlsx``       ``make_xlsx(sheets, defined_names=..., ...) -> bytes``: write a tiny ``.xlsx``
                    with ``zipfile`` (shared or inline strings, dates, formulas with cached
                    values, error cells, a 1904 epoch, extra parts), so reader tests and importer
                    tests need no openpyxl and no real workbook
``mini_league``     a :class:`MiniLeague`: four invented clubs, their squads, and a builder for a
                    synthetic workbook shaped like the user's (every sheet the importer reads,
                    plus the betting columns, filled with sentinel values, that it must never
                    read), with a matching ``crosswalk``
``el_env``          builds an environment mapping and ``ElSettings`` for bootstrap tests

Nothing here is real. Clubs and players are invented (the demo's ``ZZ`` clubs); the workbook is
built in memory. Real data lives only under ``HARDWOOD_DATA_DIR`` or the git-ignored
``tests/local``.
"""

from __future__ import annotations

import io
import random
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator, Mapping, Sequence
from xml.sax.saxutils import escape

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from nbastats.config import Settings
from nbastats.euroleague import demo as demo_module
from nbastats.euroleague.config import ElSettings
from nbastats.euroleague.db import create_el_engine, dispose_el_engine, init_el_db
from nbastats.euroleague.importers.workbook import Line
from nbastats.euroleague.profile import ClubCodeRow, ClubCrosswalk

# --------------------------------------------------------------------------- a tiny xlsx writer


@dataclass(frozen=True)
class Formula:
    """A formula cell with its cached value: the reader must return the value, never the text."""

    value: Any
    text: str = "SUM(A1:A2)"


@dataclass(frozen=True)
class Err:
    """An error cell such as ``#DIV/0!``."""

    code: str = "#DIV/0!"


@dataclass(frozen=True)
class Rich:
    """A shared string made of runs, one of them phonetic (which must be ignored)."""

    parts: tuple[str, ...]
    phonetic: str = ""


# style index -> meaning, written into styles.xml
STYLE_GENERAL, STYLE_DATE_BUILTIN, STYLE_DATE_CUSTOM, STYLE_NUMBER_CUSTOM = 0, 1, 2, 3


def _col_letters(index: int) -> str:
    letters = ""
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _serial(value: date | datetime, date1904: bool) -> float:
    moment = value if isinstance(value, datetime) else datetime(value.year, value.month, value.day)
    if date1904:
        delta = moment - datetime(1904, 1, 1)
    else:
        delta = moment - datetime(1899, 12, 30)
        if moment < datetime(1900, 3, 1):
            delta = moment - datetime(1899, 12, 31)
    return delta.days + delta.seconds / 86400


def make_xlsx(
    sheets: Sequence[tuple[str, Mapping[int, Mapping[int, Any]] | Sequence[Sequence[Any]]]],
    *,
    defined_names: Mapping[str, str] | None = None,
    date1904: bool = False,
    inline_strings: bool = False,
    extra_parts: Mapping[str, bytes] | None = None,
    workbook_extra: str = "",
    styles: bool = True,
) -> bytes:
    """Write a minimal ``.xlsx`` and return its bytes.

    ``sheets`` is ``[(name, rows)]`` where ``rows`` is either a list of lists (row 1 first;
    ``None`` leaves the cell empty) or ``{row: {col: value}}`` with 1-based indexes. Values:
    ``str``, ``bool``, ``int``, ``float``, ``date``/``datetime`` (written as a serial with a date
    style), :class:`Formula`, :class:`Err`, :class:`Rich`. ``defined_names`` maps a name to its
    reference text, for example ``{"Regress": "Settings!$C$5"}``.
    """
    shared: list[Any] = []
    shared_index: dict[Any, int] = {}

    def share(value: Any) -> int:
        key = value if not isinstance(value, Rich) else ("rich", value.parts, value.phonetic)
        if key not in shared_index:
            shared_index[key] = len(shared)
            shared.append(value)
        return shared_index[key]

    def cell_xml(ref: str, value: Any) -> str:
        formula = ""
        if isinstance(value, Formula):
            formula = f"<f>{escape(value.text)}</f>"
            value = value.value
        if value is None:
            return f'<c r="{ref}">{formula}</c>' if formula else ""
        if isinstance(value, Err):
            return f'<c r="{ref}" t="e">{formula}<v>{escape(value.code)}</v></c>'
        if isinstance(value, bool):
            return f'<c r="{ref}" t="b">{formula}<v>{int(value)}</v></c>'
        if isinstance(value, (int, float)):
            return f'<c r="{ref}">{formula}<v>{value!r}</v></c>'
        if isinstance(value, (date, datetime)):
            style = STYLE_DATE_CUSTOM if isinstance(value, datetime) else STYLE_DATE_BUILTIN
            return f'<c r="{ref}" s="{style}">{formula}<v>{_serial(value, date1904)!r}</v></c>'
        if isinstance(value, Rich):
            return f'<c r="{ref}" t="s">{formula}<v>{share(value)}</v></c>'
        text = str(value)
        if inline_strings and not formula:
            return f'<c r="{ref}" t="inlineStr"><is><t>{escape(text)}</t></is></c>'
        if formula:  # a formula's string result is stored inline as t="str"
            return f'<c r="{ref}" t="str">{formula}<v>{escape(text)}</v></c>'
        return f'<c r="{ref}" t="s"><v>{share(text)}</v></c>'

    sheet_parts: list[tuple[str, str]] = []
    for name, rows in sheets:
        if not isinstance(rows, Mapping):
            rows = {r: {c: v for c, v in enumerate(row, start=1)} for r, row in enumerate(rows, 1)}
        body = []
        for r in sorted(rows):
            cells = "".join(
                cell_xml(f"{_col_letters(c)}{r}", v) for c, v in sorted(rows[r].items())
            )
            if cells:
                body.append(f'<row r="{r}">{cells}</row>')
        sheet_parts.append(
            (
                name,
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f"<sheetData>{''.join(body)}</sheetData></worksheet>",
            )
        )

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<workbookPr date1904="{"true" if date1904 else "false"}"/><sheets>'
        + "".join(
            f'<sheet name="{escape(name, {chr(34): "&quot;"})}" sheetId="{i}" r:id="rId{i}"/>'
            for i, (name, _) in enumerate(sheet_parts, start=1)
        )
        + "</sheets>"
        + (
            "<definedNames>"
            + "".join(
                f'<definedName name="{escape(n)}">{escape(ref)}</definedName>'
                for n, ref in (defined_names or {}).items()
            )
            + "</definedNames>"
            if defined_names
            else ""
        )
        + workbook_extra
        + "</workbook>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/'
            f'2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(sheet_parts) + 1)
        )
        + "</Relationships>"
    )
    shared_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + "".join(_si(item) for item in shared)
        + "</sst>"
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts count="2"><numFmt numFmtId="164" formatCode="d\\ mmm\\ yyyy"/>'
        '<numFmt numFmtId="165" formatCode="0.00&quot;m&quot;"/></numFmts>'
        '<cellXfs count="4"><xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="164"/>'
        '<xf numFmtId="165"/></cellXfs></styleSheet>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        if shared:
            archive.writestr("xl/sharedStrings.xml", shared_xml)
        if styles:
            archive.writestr("xl/styles.xml", styles_xml)
        for i, (_, xml) in enumerate(sheet_parts, start=1):
            archive.writestr(f"xl/worksheets/sheet{i}.xml", xml)
        for name, data in (extra_parts or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _si(item: Any) -> str:
    if isinstance(item, Rich):
        runs = "".join(f"<r><t>{escape(p)}</t></r>" for p in item.parts)
        phon = f'<rPh sb="0" eb="1"><t>{escape(item.phonetic)}</t></rPh>' if item.phonetic else ""
        return f"<si>{runs}{phon}</si>"
    return f'<si><t xml:space="preserve">{escape(str(item))}</t></si>'


@pytest.fixture(name="make_xlsx")
def _make_xlsx() -> Callable[..., bytes]:
    return make_xlsx


@pytest.fixture()
def xl() -> SimpleNamespace:
    """The writer and its cell wrappers in one namespace: ``xl.make``, ``xl.Formula``,
    ``xl.Err``, ``xl.Rich`` (a conftest cannot be imported, so tests take them as a fixture)."""
    return SimpleNamespace(make=make_xlsx, Formula=Formula, Err=Err, Rich=Rich)


# --------------------------------------------------------------------------- stores


@pytest.fixture()
def el_engine(tmp_path: Path) -> Iterator[Engine]:
    """An empty EuroLeague store: every table, no identity."""
    engine = create_el_engine(f"sqlite:///{tmp_path / 'el.db'}")
    init_el_db(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def el_session(el_engine: Engine) -> Iterator[Session]:
    session = Session(el_engine, future=True)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _seeded_engine(directory: Path) -> Engine:
    engine = create_el_engine(f"sqlite:///{directory / 'el_demo.db'}")
    init_el_db(engine)
    with Session(engine, future=True) as session:
        demo_module.seed_demo(session)
        session.commit()
    return engine


@pytest.fixture(scope="session")
def demo_engine(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Engine]:
    """The invented league, seeded once. Read-only by convention."""
    engine = _seeded_engine(tmp_path_factory.mktemp("el-demo"))
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def demo_session(demo_engine: Engine) -> Iterator[Session]:
    session = Session(demo_engine, future=True)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def fresh_demo_engine(tmp_path: Path) -> Iterator[Engine]:
    engine = _seeded_engine(tmp_path)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _dispose_cached_engines() -> Iterator[None]:
    """The EuroLeague engine cache is per URL; drop it so no test inherits another's file."""
    yield
    dispose_el_engine()


# --------------------------------------------------------------------------- environment


@pytest.fixture()
def el_env(tmp_path: Path) -> Callable[..., tuple[dict[str, str], ElSettings]]:
    """``el_env(**overrides) -> (environ, ElSettings)`` for a self-contained environment.

    The NBA store is ``tmp_path/nba.db`` and the EuroLeague store defaults to
    ``tmp_path/data/hardwood_el.db``; keys in ``overrides`` replace or (``None``) remove entries.
    """

    def build(**overrides: str | None) -> tuple[dict[str, str], ElSettings]:
        environ: dict[str, str] = {
            "DATABASE_URL": f"sqlite:///{tmp_path / 'nba.db'}",
            "HARDWOOD_DATA_DIR": str(tmp_path / "data"),
        }
        for key, value in overrides.items():
            if value is None:
                environ.pop(key, None)
            else:
                environ[key] = value
        return environ, ElSettings.from_env(environ, nba_settings=Settings.from_env(environ))

    return build


# --------------------------------------------------------------------------- the mini league

#: Values that sit in cells the importer must never read. After an import none of them may be
#: found anywhere in the store, as text or as a number.
SENTINEL_TEXT = ("SENTINEL-OVER", "SENTINEL-LEAN", "SENTINEL-YOURLINE", "SENTINEL-WIN")
SENTINEL_NUMBERS = (
    8888.5,
    8887.25,
    0.987654321,
    0.123456789,
    9191.5,
    7777.7,
    6666.6,
    5555.5,
    4444.4,
)

_COUNTRY = "Zedland"


@dataclass
class _MiniGame:
    round_number: int
    code: int
    day: date
    home: str  # official code
    away: str
    home_pts: int
    away_pts: int
    ot: int
    home_lines: list[tuple[Any, Line]]
    away_lines: list[tuple[Any, Line]]
    home_team: Line
    away_team: Line
    partials: tuple[list[int], list[int]]
    venue: str
    attendance: int
    proj: tuple[float, float]


@dataclass
class MiniLeague:
    """Four invented clubs and a builder for a workbook shaped like the user's.

    Workbook codes are a rotation of the official ones (workbook ``ZZB`` is official ``ZZA``,
    and so on), so a bare-code lookup would cross two clubs, the way the real ``PAR`` does.
    """

    clubs: list[Any]
    workbook_code: dict[str, str]
    games: dict[int, _MiniGame] = field(default_factory=dict)

    @property
    def crosswalk(self) -> ClubCrosswalk:
        rows = tuple(
            ClubCodeRow(
                workbook_code=self.workbook_code[club.spec.code],
                official_code=club.spec.code,
                tv_code=None,
                name=club.spec.name,
                short_name=club.spec.short_name,
                verified="pending",
            )
            for club in self.clubs
        )
        return ClubCrosswalk(rows)

    def wb(self, official: str) -> str:
        return self.workbook_code[official]

    def club(self, official: str) -> Any:
        return next(c for c in self.clubs if c.spec.code == official)

    # ------------------------------------------------------------------ games

    def _play(
        self,
        rng: random.Random,
        rnd: int,
        code: int,
        day: date,
        home: str,
        away: str,
        ot: int,
        h: int,
        a: int,
    ) -> _MiniGame:
        hc, ac = self.club(home), self.club(away)
        home_lines, home_team = demo_module._team_lines(rng, hc, h, a, ot, False)
        away_lines, away_team = demo_module._team_lines(rng, ac, a, h, ot, False)
        if ot:
            regulation = 80
            ph = demo_module._split(rng, regulation, 4) + [h - regulation]
            pa = demo_module._split(rng, regulation, 4) + [a - regulation]
        else:
            ph, pa = demo_module._split(rng, h, 4), demo_module._split(rng, a, 4)
        return _MiniGame(
            rnd,
            code,
            day,
            home,
            away,
            h,
            a,
            ot,
            home_lines,
            away_lines,
            home_team,
            away_team,
            (ph, pa),
            f"{hc.spec.venue}",
            4000 + code * 111,
            (h - 2.25, a + 1.5),
        )

    # ------------------------------------------------------------------ the workbook

    def workbook(
        self,
        *,
        box_rounds: Sequence[int] = (1, 2),
        fixture_round: int | None = 3,
        break_game: int | None = None,
        bad_partials_game: int | None = None,
        wrong_result_game: int | None = None,
        unknown_club: bool = False,
        include_review: bool = True,
        with_sentinels: bool = True,
        as_of: str = "30 September 2026",
        extra_status_rows: Sequence[Sequence[Any]] = (),
        title: str = "Synthetic League 2026-27 Toolkit",
        extra_sheets: Sequence[tuple[str, Any]] = (),
        defined_extra: Mapping[str, str] | None = None,
        fixture_rows: Sequence[tuple[str | None, float | None, str | None]] | None = None,
    ) -> bytes:
        """Build the workbook. ``break_game`` makes one player's points disagree with his
        makes in that game code (a hard invariant failure); ``bad_partials_game`` makes that
        game's quarters not sum to its final score."""
        self.wrong_result_game = wrong_result_game
        rng = random.Random(5)
        a, b, c, d = (club.spec.code for club in self.clubs)
        schedule = {
            1: [(1, date(2026, 9, 24), a, b, 0, 80, 77), (2, date(2026, 9, 25), c, d, 0, 91, 88)],
            2: [(3, date(2026, 9, 29), b, c, 1, 92, 95), (4, date(2026, 9, 30), d, a, 0, 84, 90)],
            3: [(5, date(2026, 10, 1), a, c, 0, 83, 79), (6, date(2026, 10, 2), b, d, 0, 87, 81)],
        }
        self.games = {}
        for rnd, rows in schedule.items():
            for code, day, home, away, ot, h, away_pts in rows:
                self.games[code] = self._play(rng, rnd, code, day, home, away, ot, h, away_pts)

        sheets: list[tuple[str, Any]] = []
        names: dict[str, str] = {}

        # Start Here
        sheets.append(("Start Here", {2: {2: title}, 3: {2: "Prose that is never imported."}}))

        # Settings
        settings: dict[int, dict[int, Any]] = {
            1: {1: "Settings"},
            5: {2: "Regress", 3: 0.3},
            6: {2: "HCA", 3: 3.5},
            7: {2: "TeamSD", 3: 9.5},
            8: {2: "MarginSD", 3: 11.5},
            9: {2: "TotalSD", 3: 7777.7 if with_sentinels else 13.5},
            10: {2: "PSDBase", 3: 6666.6 if with_sentinels else 1.8},
            11: {2: "PSDSlope", 3: 5555.5 if with_sentinels else 0.28},
            12: {2: "EdgeP", 3: 4444.4 if with_sentinels else 0.55},
            13: {2: "TeamMinutes", 3: 200},
            17: {2: "LgPts", 3: 85.12},
            24: {2: "Status", 3: "Chance he plays"},
            25: {2: "OUT", 3: 0},
            26: {2: "DOUBTFUL", 3: 0.25},
            27: {2: "QUESTIONABLE", 3: 0.5},
            28: {2: "PROBABLE", 3: 0.85},
            29: {2: "AVAILABLE", 3: 1},
            31: {2: "Absorb", 3: 0.6},
            32: {2: "ReplRate", 3: 9},
            33: {2: "BoostCap", 3: 1.35},
            34: {2: "RotShare", 3: 0.5},
            35: {2: "FormW", 3: 0.25},
            40: {2: "Mystery", 3: 3.14},
        }
        sheets.append(("Settings", settings))
        names.update(
            {
                "Regress": "Settings!$C$5",
                "HCA": "Settings!$C$6",
                "TeamSD": "Settings!$C$7",
                "MarginSD": "Settings!$C$8",
                "TotalSD": "Settings!$C$9",
                "PSDBase": "Settings!$C$10",
                "PSDSlope": "Settings!$C$11",
                "EdgeP": "Settings!$C$12",
                "TeamMinutes": "Settings!$C$13",
                "LgPts": "Settings!$C$17",
                "StatusList": "Settings!$B$25:$B$29",
                "StatusProb": "Settings!$C$25:$C$29",
                "Absorb": "Settings!$C$31",
                "ReplRate": "Settings!$C$32",
                "BoostCap": "Settings!$C$33",
                "RotShare": "Settings!$C$34",
                "FormW": "Settings!$C$35",
                "Mystery": "Settings!$C$40",
                "KTeam_2": "'R2 Review'!$M$30",
                "Sq_Name": "Squads!$B$6:$B$40",  # a range name on another sheet: ignored quietly
            }
        )
        names.update(defined_extra or {})

        # Team Ratings
        ratings: dict[int, dict[int, Any]] = {
            1: {1: "Team Ratings"},
            5: dict(
                enumerate(
                    [
                        "Code",
                        "Club",
                        "Country",
                        "Head coach",
                        "W",
                        "L",
                        "PF/g 2025-26",
                        "PA/g 2025-26",
                        "Attack adj",
                        "Defence adj",
                        "Proj PF/g",
                        "Proj PA/g",
                    ],
                    start=1,
                )
            ),
        }
        for i, club in enumerate(self.clubs):
            wins, losses = (None, None) if i == len(self.clubs) - 1 else (20 + i, 18 - i)
            code = self.wb(club.spec.code) if not (unknown_club and i == 0) else "QQQ"
            ratings[6 + i] = {
                1: code,
                2: club.spec.name,
                3: _COUNTRY,
                4: club.spec.coach,
                5: wins,
                6: losses,
                7: 84.5 + i,
                8: 83.0 - i,
                9: 1.25 - i * 0.5,
                10: -0.75 + i * 0.4,
                11: 91.0,
                12: 80.0,
            }
        ratings[6 + len(self.clubs) + 1] = {
            2: "A note below the table; the walk has already stopped."
        }
        sheets.append(("Team Ratings", ratings))

        # Squads
        squad_headers = [
            "Team",
            "Player",
            "Pos",
            "Age",
            "Role",
            "Basis",
            "MIN",
            "PTS/40",
            "REB/40",
            "AST/40",
            "3PM/40",
            "STL/40",
            "BLK/40",
            "TOV/40",
            "2PA/40",
            "3PA/40",
            "FTA/40",
            "2P%",
            "3P%",
            "FT%",
            "PTS",
            "R3 status",
        ]
        squads: dict[int, dict[int, Any]] = {5: dict(enumerate(squad_headers, start=1))}
        row = 6
        for club in self.clubs:
            for player in club.players:
                r = player.rates
                squads[row] = {
                    1: self.wb(club.spec.code),
                    2: player.name,
                    3: player.pos5,
                    4: player.age,
                    5: player.role,
                    6: "EuroLeague" if player.established else "est.",
                    7: player.proj_minutes,
                    8: r["pts40"],
                    9: r["reb40"],
                    10: r["ast40"],
                    11: r["fg3m40"],
                    12: r["stl40"],
                    13: r["blk40"],
                    14: r["tov40"],
                    15: r["fga2_40"],
                    16: r["fga3_40"],
                    17: r["fta40"],
                    18: r["fg2_pct"] if r["fg2_pct"] is not None else 0,
                    19: r["fg3_pct"] if r["fg3_pct"] is not None else 0,
                    20: r["ft_pct"] if r["ft_pct"] is not None else 0,
                    21: 12.5,
                    22: "OUT",
                }
                row += 1
        sheets.append(("Squads", squads))

        # Box scores
        for rnd in box_rounds:
            sheets.append((f"R{rnd} Box Scores", self._box_sheet(rnd, break_game, unknown_club)))

        # R2 Review (betting columns carry sentinels)
        if include_review:
            sheets.append(("R2 Review", self._review_sheet(with_sentinels, bad_partials_game)))

        # Round n fixtures (betting columns carry sentinels)
        if fixture_round is not None:
            sheets.append(
                (
                    f"Round {fixture_round}",
                    self._fixture_sheet(fixture_round, with_sentinels, fixture_rows),
                )
            )

        # Injury Report
        sheets.append(("Injury Report", self._injury_sheet(as_of, extra_status_rows)))

        # Sheets that are never imported (and carry betting cells)
        sheets.append(
            (
                "R3 Scorers",
                {
                    6: {
                        1: "#",
                        2: "Player",
                        17: "Model line",
                        18: "Your line",
                        19: "P(over)",
                        20: "Lean",
                    },
                    7: {
                        1: 1,
                        2: "Nobody Real",
                        17: 8888.5,
                        18: 8887.25,
                        19: 0.987654321,
                        20: "SENTINEL-LEAN",
                    },
                },
            )
        )
        sheets.append(("Game Logs", {5: {1: "Player", 2: "Club"}, 6: {1: "Nobody Real", 2: "ZZZ"}}))
        sheets.append(
            ("Latest Games", {5: {1: "Club", 2: "Date"}, 6: {1: "ZZZ", 2: date(2026, 9, 4)}})
        )
        sheets.extend(extra_sheets)
        return make_xlsx(sheets, defined_names=names)

    # ------------------------------------------------------------------ sheets

    wrong_result_game: int | None = None

    def _result(self, game: _MiniGame, official: str) -> str:
        home_pts = game.home_pts + (1 if game.code == self.wrong_result_game else 0)
        mine, theirs = (
            (home_pts, game.away_pts) if official == game.home else (game.away_pts, home_pts)
        )
        return f"{'W' if mine > theirs else 'L'} {mine}-{theirs}"

    def _box_sheet(
        self, rnd: int, break_game: int | None, unknown_club: bool
    ) -> dict[int, dict[int, Any]]:
        headers = [
            "Game",
            "Date",
            "Club",
            "Opp",
            "H/A",
            "Result",
            "Player",
            "MIN",
            "PTS",
            "2PM",
            "2PA",
            "3PM",
            "3PA",
            "FTM",
            "FTA",
            "FG%",
            "3P%",
            "FT%",
            "OREB",
            "DREB",
            "REB",
            "AST",
            "STL",
            "TOV",
            "BLK",
            "PF",
            "PIR",
            "kp",
        ]
        sheet: dict[int, dict[int, Any]] = {
            1: {1: f"Round {rnd} box scores"},
            5: dict(enumerate(headers, start=1)),
        }
        row = 6
        games = [g for g in self.games.values() if g.round_number == rnd]
        for game in games:
            for official, lines, opp in (
                (game.home, game.home_lines, game.away),
                (game.away, game.away_lines, game.home),
            ):
                for player, line in lines:
                    played = line.participation == "played"
                    pts = line.pts
                    if break_game == game.code and played and pts and official == game.home:
                        pts += 1
                        break_game = None
                    cells: dict[int, Any] = {
                        1: game.code,
                        2: game.day,
                        3: self.wb(official),
                        4: self.wb(opp),
                        5: "H" if official == game.home else "A",
                        6: self._result(game, official),
                        7: player.name,
                    }
                    if played:
                        cells.update(
                            {
                                8: round(line.seconds / 60, 2),
                                9: pts,
                                10: line.fgm2,
                                11: line.fga2,
                                12: line.fgm3,
                                13: line.fga3,
                                14: line.ftm,
                                15: line.fta,
                                19: line.oreb,
                                20: line.dreb,
                                21: line.reb,
                                22: line.ast,
                                23: line.stl,
                                24: line.tov,
                                25: line.blk,
                                26: line.pf,
                                27: line.pir_official,
                                28: Formula(1.5, "I6-ROW()/1000000"),
                                16: Formula(0.5, 'IFERROR(x,"")'),
                                17: Err("#DIV/0!"),
                            }
                        )
                    else:  # the sheet writes zeros for a DNP
                        cells.update(
                            {
                                8: "DNP",
                                **{
                                    k: 0
                                    for k in (
                                        9,
                                        10,
                                        11,
                                        12,
                                        13,
                                        14,
                                        15,
                                        19,
                                        20,
                                        21,
                                        22,
                                        23,
                                        24,
                                        25,
                                        26,
                                        27,
                                    )
                                },
                            }
                        )
                    sheet[row] = cells
                    row += 1
        row += 2
        sheet[row] = {1: "Team totals"}
        row += 1
        team_headers = list(headers)
        team_headers[6] = "Team"
        sheet[row] = dict(enumerate(team_headers, start=1))
        row += 1
        for game in games:
            for official, team, opp in (
                (game.home, game.home_team, game.away),
                (game.away, game.away_team, game.home),
            ):
                sheet[row] = {
                    1: game.code,
                    2: game.day,
                    3: self.wb(official),
                    4: self.wb(opp),
                    5: "H" if official == game.home else "A",
                    6: self._result(game, official),
                    7: self.club(official).spec.name,
                    8: round(team.seconds / 60, 2),
                    9: team.pts,
                    10: team.fgm2,
                    11: team.fga2,
                    12: team.fgm3,
                    13: team.fga3,
                    14: team.ftm,
                    15: team.fta,
                    19: team.oreb,
                    20: team.dreb,
                    21: team.reb,
                    22: team.ast,
                    23: team.stl,
                    24: team.tov,
                    25: team.blk,
                    26: team.pf,
                    27: team.pir_official,
                }
                row += 1
        sheet[row] = {1: "A footnote under the totals, in the Game column."}
        row += 2
        sheet[row] = {1: f"Round {rnd} leaders"}
        sheet[row + 1] = {
            1: "#",
            2: "Date",
            3: "Club",
            4: "Opp",
            5: "H/A",
            6: "Result",
            7: "Player",
            8: "MIN",
            9: "PTS",
        }
        sheet[row + 2] = {1: 1, 2: games[0].day, 3: "ZZZ", 7: "Nobody Real", 9: 99}
        return sheet

    def _review_sheet(
        self, sentinels: bool, bad_partials_game: int | None
    ) -> dict[int, dict[int, Any]]:
        headers = [
            "#",
            "Date",
            "Home",
            "Away",
            "Proj home",
            "Proj away",
            "Home",
            "Away",
            "Proj total",
            "Actual total",
            "Total miss",
            "Model line",
            "Result v line",
            "Projected winner",
            "Winner",
            "Right?",
            "Margin miss",
            "Quarters",
            "Venue",
            "Attendance",
        ]
        sheet: dict[int, dict[int, Any]] = {
            5: {5: "Projected", 7: "Result"},
            6: dict(enumerate(headers, start=1)),
        }
        row = 7
        for n, game in enumerate([g for g in self.games.values() if g.round_number == 2], start=1):
            ph, pa = game.partials
            if bad_partials_game == game.code:
                ph = [*ph[:-1], ph[-1] + 1]
            quarters = ", ".join(f"{x}-{y}" for x, y in zip(ph, pa))
            venue = game.venue + (" (neutral)" if game.code == 4 else "")
            sheet[row] = {
                1: n,
                2: game.day,
                3: self.wb(game.home),
                4: self.wb(game.away),
                5: game.proj[0],
                6: game.proj[1],
                7: game.home_pts,
                8: game.away_pts,
                9: sum(game.proj),
                10: game.home_pts + game.away_pts,
                11: 1.0,
                12: 9191.5 if sentinels else 160.5,
                13: "SENTINEL-OVER" if sentinels else "OVER",
                14: "x",
                15: "y",
                16: "Yes",
                17: 2.0,
                18: quarters,
                19: venue,
                20: game.attendance,
            }
            row += 1
        sheet[row + 1] = {1: "How Round 2 went", 7: "9 over, 1 under"}
        sheet[30] = {10: "Weight of one game", 13: 0.09}
        sheet[31] = {
            2: "Club",
            3: "Opp",
            4: "Proj scored",
            5: "Scored",
            7: "Proj allowed",
            8: "Allowed",
        }
        return sheet

    def _fixture_sheet(
        self,
        rnd: int,
        sentinels: bool,
        rows: Sequence[tuple[str | None, float | None, str | None]] | None = None,
    ) -> dict[int, dict[int, Any]]:
        """``rows`` overrides, per game, ``(venue text, home advantage, tip text)``."""
        headers = [
            "#",
            "Date",
            "Tip local / CEST / TR",
            "Home",
            "Home club",
            "Away",
            "Away club",
            "Venue",
            "Home adv (pts)",
            "Proj home",
            "Proj away",
            "Margin",
            "Projected winner",
            "Home win %",
            "Proj total",
            "Injury effect on total",
            "Model line",
            "Your line",
            "P(over)",
            "Lean",
            "What to watch",
        ]
        sheet: dict[int, dict[int, Any]] = {
            3: {1: "Type a bookmaker line to see the edge."},
            6: dict(enumerate(headers, start=1)),
        }
        for n, game in enumerate(
            [g for g in self.games.values() if g.round_number == rnd], start=1
        ):
            neutral = n == 1
            venue = game.venue + (" (neutral)" if neutral else "")
            advantage: float | None = 0 if neutral else 3.5
            tip: str | None = "19:00 / 18:00 / 19:00" if n == 1 else "20:30 / 20:30 / 21:30"
            if rows is not None and n <= len(rows):
                venue, advantage, tip = rows[n - 1]
            sheet[6 + n] = {
                1: n,
                2: game.day,
                3: tip,
                4: self.wb(game.home),
                5: self.club(game.home).spec.name,
                6: self.wb(game.away),
                7: self.club(game.away).spec.name,
                8: venue,
                9: advantage,
                10: 83.9,
                11: 84.9,
                12: -1.0,
                13: "x",
                14: 0.123456789 if sentinels else 0.5,
                15: 168.8,
                16: -3.0,
                17: 8888.5 if sentinels else 168.5,
                18: 8887.25 if sentinels else 168.5,
                19: 0.987654321 if sentinels else 0.5,
                20: "SENTINEL-LEAN" if sentinels else "no edge",
                21: "prose",
            }
        sheet[20] = {1: "#", 2: "Date", 4: "H/A", 5: "Club", 10: "Win %"}
        sheet[21] = {1: 1, 10: 0.5, 5: "SENTINEL-WIN" if sentinels else "x"}
        return sheet

    def _injury_sheet(
        self, as_of: str, extra: Sequence[Sequence[Any]]
    ) -> dict[int, dict[int, Any]]:
        a, b, c, d = (club for club in self.clubs)
        headers = [
            "Club",
            "Player",
            "Problem",
            "Status (research)",
            "In model",
            "Expected return",
            "Source",
            "Source date",
        ]
        published = date(2026, 9, 29)

        def name(club: Any, i: int) -> str:
            return club.players[i].name

        rows: list[list[Any]] = [
            [
                self.wb(a.spec.code),
                name(a, 0),
                "Tendinitis in the heel",
                "OUT",
                "OUT",
                "Rounds 2-3",
                "https://news.example.org/a",
                published,
            ],
            [
                self.wb(a.spec.code),
                name(a, 1),
                "Left out of the squad",
                "AVAILABLE",
                "AVAILABLE",
                "Squad choice",
                "https://www.example.net/b",
                published,
            ],
            [
                self.wb(b.spec.code),
                name(b, 0),
                "Box score shows no minutes",
                "QUESTIONABLE",
                "QUESTIONABLE",
                "No update",
                "https://live.euroleague.net/api/Boxscore?gamecode=3&seasoncode=E2026",
                published,
            ],
            [
                self.wb(b.spec.code),
                name(b, 1),
                "Hamstring",
                "DOUBTFUL",
                "DOUBTFUL",
                "Around 8 Oct",
                "https://www.mozzartsport.com/kosarka/vesti/invented-story/1",
                published,
            ],
            [
                self.wb(c.spec.code),
                name(c, 0),
                "Torn ligament",
                "OUT",
                "OUT",
                "Long-term",
                "https://news.example.org/c",
                published,
            ],
            [
                self.wb(c.spec.code),
                "Nobody On The Squad",
                "Knee",
                "PROBABLE",
                "PROBABLE",
                "Playing",
                "https://news.example.org/d",
                published,
            ],
            [
                self.wb(d.spec.code),
                name(d, 0),
                "Back problem",
                "DOUBTFULL",
                "OUT",
                "n/a",
                "https://news.example.org/e",
                published,
            ],
            [
                self.wb(d.spec.code),
                name(d, 1),
                "Family matter",
                "OUT",
                "OUT",
                "Round 4",
                "https://news.example.org/f",
                published,
            ],
            *[list(r) for r in extra],
        ]
        sheet: dict[int, dict[int, Any]] = {
            1: {1: f"Injury Report - availability as of {as_of}"},
            5: dict(enumerate(headers, start=1)),
        }
        for i, values in enumerate(rows):
            sheet[6 + i] = dict(enumerate(values, start=1))
        last = 6 + len(rows)
        sheet[last + 1] = {1: "Clubs with no injury news found"}
        sheet[last + 2] = {1: self.wb(a.spec.code), 2: "No injuries; the coach rested two."}
        return sheet


@pytest.fixture()
def mini_league() -> MiniLeague:
    """Four invented clubs with squads, and a builder for a synthetic workbook."""
    clubs = demo_module._build_clubs(random.Random(demo_module.DEMO_SEED + 1))[:4]
    # a rotation: workbook ZZB is official ZZA, workbook ZZC is official ZZB, workbook ZZA is ZZC
    rotation = {"ZZA": "ZZB", "ZZB": "ZZC", "ZZC": "ZZA", "ZZD": "ZZD"}
    return MiniLeague(clubs=clubs, workbook_code=rotation)
