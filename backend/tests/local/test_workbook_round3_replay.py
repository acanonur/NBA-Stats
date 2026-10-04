"""Replay of the user's Round 3 workbook through the shared team-score model (verification G1).

This is the test the design calls the replay: the model, fed the workbook's own inputs, must
return the workbook's own outputs. It reads the user's real workbook, so it lives under
``tests/local/`` (git-ignored, like every other file that touches real data), and it **skips
unless** ``HARDWOOD_WORKBOOK_PATH`` names an existing ``.xlsx``. Nothing from the workbook is
copied into the repository; the file is read in place, its cached cell values only (formulas
are never evaluated by anyone but the test's own transcription of the model).

What is asserted, for all ten Round 3 games, to 1e-9:

* the projected home and away scores (``Round 3`` J and K), the margin (L), the combined points
  (O), the full-strength twins (Y and Z) and the combined availability effect (P);
* the printed summary (M), which also pins the sheet's rounding of the margin;
* underneath, every intermediate the sheet shows: the league level, each club's ratings, each
  club's injury-layer totals (``Team Ratings`` O, U to AC) and every rostered player's
  projected minutes and points (``Squads`` AK and AL).

The run uses ``capPolicy = workbook``, no squad reconciliation and no overtime scaling, the
settings under which the model is *meant* to reproduce the sheet. It includes the case the
design calls out by name, a club whose recovered scoring exceeds the 1.35 boost cap (the sheet
credits the team with points no player is given), and the neutral-venue game with a zero home
advantage. A pass on the user's machine is the record that the model reproduces the sheet; the
result is noted in ``docs/EUROLEAGUE.md`` before the EuroLeague projections are announced.

The reader below is a few lines of standard library (``zipfile`` and ``xml.etree``) rather than
a dependency, and refuses a part that declares a DTD, as the workbook importer does.
"""

from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from nbastats.shared import injury_layer as IL
from nbastats.shared import team_projection as TP

WORKBOOK = os.environ.get("HARDWOOD_WORKBOOK_PATH", "")

pytestmark = pytest.mark.skipif(
    not WORKBOOK or not Path(WORKBOOK).is_file(),
    reason="HARDWOOD_WORKBOOK_PATH does not name a workbook; the replay needs the real file",
)

NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
TOLERANCE = 1e-9
MAX_PART_BYTES = 50 * 1024 * 1024


class Workbook:
    """Cached cell values of a ``.xlsx``, by sheet name and A1 coordinate."""

    def __init__(self, path: str) -> None:
        self._zip = zipfile.ZipFile(path)
        self._strings = self._shared_strings()
        self._sheet_parts = self._sheet_paths()
        self._cache: dict[str, dict[str, object]] = {}

    def _parse(self, name: str) -> ET.Element:
        info = self._zip.getinfo(name)
        assert info.file_size < MAX_PART_BYTES, f"{name} is implausibly large"
        raw = self._zip.read(name)
        assert b"<!DOCTYPE" not in raw and b"<!ENTITY" not in raw, f"{name} declares a DTD"
        return ET.fromstring(raw)

    def _shared_strings(self) -> list[str]:
        if "xl/sharedStrings.xml" not in self._zip.namelist():
            return []
        root = self._parse("xl/sharedStrings.xml")
        return ["".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")) for si in root]

    def _sheet_paths(self) -> dict[str, str]:
        book = self._parse("xl/workbook.xml")
        rels = self._parse("xl/_rels/workbook.xml.rels")
        targets = {rel.get("Id"): rel.get("Target") for rel in rels}
        paths = {}
        for sheet in book.find("m:sheets", NS):
            target = targets[sheet.get(f"{{{NS['r']}}}id")].lstrip("/")
            paths[sheet.get("name")] = target if target.startswith("xl/") else f"xl/{target}"
        return paths

    def sheet(self, name: str) -> dict[str, object]:
        if name not in self._cache:
            root = self._parse(self._sheet_parts[name])
            cells: dict[str, object] = {}
            for cell in root.iter(f"{{{NS['m']}}}c"):
                value = cell.find("m:v", NS)
                kind = cell.get("t")
                if kind == "inlineStr":
                    text = cell.find("m:is", NS)
                    cells[cell.get("r")] = "".join(
                        t.text or "" for t in text.iter(f"{{{NS['m']}}}t")
                    )
                elif value is None or value.text is None:
                    continue
                elif kind == "s":
                    cells[cell.get("r")] = self._strings[int(value.text)]
                elif kind in ("str", "e"):
                    cells[cell.get("r")] = value.text
                elif kind == "b":
                    cells[cell.get("r")] = value.text == "1"
                else:
                    cells[cell.get("r")] = float(value.text)
            self._cache[name] = cells
        return self._cache[name]


@pytest.fixture(scope="module")
def book() -> Workbook:
    return Workbook(WORKBOOK)


def number(cells, ref):
    value = cells.get(ref)
    assert isinstance(value, float), f"{ref} is {value!r}, expected a number"
    return value


@pytest.fixture(scope="module")
def replay(book):
    """The model run on the workbook's inputs, with the sheet's cached outputs beside it."""
    settings_sheet = book.sheet("Settings")
    ratings = book.sheet("Team Ratings")
    squads = book.sheet("Squads")
    round3 = book.sheet("Round 3")

    regression = number(settings_sheet, "C5")
    home_advantage = number(settings_sheet, "C6")
    absorb = number(settings_sheet, "C31")
    replacement_per_40 = number(settings_sheet, "C32")
    boost_cap = number(settings_sheet, "C33")
    rotation_share = number(settings_sheet, "C34")
    chance = {
        str(settings_sheet[f"B{row}"]): number(settings_sheet, f"C{row}") for row in range(25, 30)
    }
    assert set(chance) == {"OUT", "DOUBTFUL", "QUESTIONABLE", "PROBABLE", "AVAILABLE"}

    clubs = []
    for row in range(6, 26):
        code = ratings.get(f"A{row}")
        assert isinstance(code, str) and re.fullmatch(r"[A-Z]{3}", code), f"row {row}: {code!r}"
        clubs.append(
            dict(
                row=row,
                code=code,
                pf=number(ratings, f"G{row}"),
                pa=number(ratings, f"H{row}"),
                attack_adj=number(ratings, f"I{row}"),
                defence_adj=number(ratings, f"J{row}"),
            )
        )
    assert len(clubs) == 20
    level = sum(c["pf"] for c in clubs) / len(clubs)  # Settings C17 =AVERAGE(T_PF)

    injury = IL.InjurySettings.from_per40(
        replacement_per_40,
        absorb,
        boost_cap,
        cap_policy=IL.CAP_WORKBOOK,
        rotation_share=rotation_share,
    )

    roster: dict[str, list[tuple[int, IL.PlayerInput]]] = {c["code"]: [] for c in clubs}
    for row in range(6, 336):
        team = squads.get(f"A{row}")
        if team is None:
            continue
        status = squads.get(f"AI{row}")
        # a status outside the table is read as "plays" by the sheet's IFERROR; the workbook
        # has none, and this asserts it so the replay never depends on that behaviour
        assert status in chance, f"Squads row {row}: status {status!r}"
        minutes = number(squads, f"G{row}")
        points = number(squads, f"H{row}") * minutes / 40  # Squads U =H*G/40
        roster[team].append(
            (row, IL.PlayerInput(key=row, minutes=minutes, points=points, chance=chance[status]))
        )

    layer = {
        code: IL.apply_injury_layer([p for _, p in players], injury)
        for code, players in roster.items()
    }
    strengths = {}
    for club in clubs:
        pf = TP.rating(club["pf"], level, regression, club["attack_adj"])
        pa = TP.rating(club["pa"], level, regression, club["defence_adj"])
        strengths[club["code"]] = TP.TeamStrength(
            attack=TP.attack_index(pf, level),
            defence=TP.defence_index(pa, level),
            availability_factor=layer[club["code"]].availability_factor,
        )
    return dict(
        level=level,
        clubs=clubs,
        roster=roster,
        layer=layer,
        strengths=strengths,
        regression=regression,
        home_advantage=home_advantage,
        ratings=ratings,
        squads=squads,
        round3=round3,
        settings=settings_sheet,
        levers=(replacement_per_40, absorb, boost_cap, rotation_share),
    )


def close(got, expected, label):
    assert abs(got - expected) < TOLERANCE, f"{label}: model {got!r}, workbook {expected!r}"


def test_the_league_level_matches_the_workbook(replay) -> None:
    close(replay["level"], number(replay["settings"], "C17"), "Settings C17")


def test_every_clubs_ratings_match(replay) -> None:
    ratings, level = replay["ratings"], replay["level"]
    for club in replay["clubs"]:
        row, code = club["row"], club["code"]
        pf = TP.rating(club["pf"], level, replay["regression"], club["attack_adj"])
        pa = TP.rating(club["pa"], level, replay["regression"], club["defence_adj"])
        close(pf, number(ratings, f"K{row}"), f"{code} Team Ratings K")
        close(pa, number(ratings, f"L{row}"), f"{code} Team Ratings L")
        close(replay["strengths"][code].attack, number(ratings, f"M{row}"), f"{code} M")
        close(replay["strengths"][code].defence, number(ratings, f"N{row}"), f"{code} N")


def test_every_clubs_injury_layer_totals_match(replay) -> None:
    ratings = replay["ratings"]
    for club in replay["clubs"]:
        row, code = club["row"], club["code"]
        r = replay["layer"][code]
        close(r.full_strength_points, number(ratings, f"O{row}"), f"{code} O")
        close(r.lost_minutes, number(ratings, f"U{row}"), f"{code} U lost MIN")
        close(r.lost_points, number(ratings, f"V{row}"), f"{code} V lost PTS")
        close(r.available_minutes, number(ratings, f"W{row}"), f"{code} W avail MIN")
        close(r.available_points, number(ratings, f"X{row}"), f"{code} X avail PTS")
        close(r.replacement_points, number(ratings, f"Y{row}"), f"{code} Y repl PTS")
        close(r.absorbed_points, number(ratings, f"Z{row}"), f"{code} Z absorbed")
        close(r.boost, number(ratings, f"AA{row}"), f"{code} AA boost")
        close(r.squad_points, number(ratings, f"AB{row}"), f"{code} AB squad PTS")
        after = TP.apply_availability(
            replay["strengths"][code].attack, replay["strengths"][code].availability_factor
        )
        close(after, number(ratings, f"AC{row}"), f"{code} AC R3 attack index")


def test_every_players_round_3_minutes_and_points_match(replay) -> None:
    squads = replay["squads"]
    checked = 0
    for code, players in replay["roster"].items():
        outcomes = replay["layer"][code].players
        for (row, _), outcome in zip(players, outcomes):
            close(outcome.projected_minutes, number(squads, f"AK{row}"), f"Squads AK{row}")
            close(outcome.projected_points, number(squads, f"AL{row}"), f"Squads AL{row}")
            checked += 1
    assert checked == sum(len(p) for p in replay["roster"].values()) > 250


def test_all_ten_round_3_games_match(replay) -> None:
    round3 = replay["round3"]
    for row in range(7, 17):
        home, away = round3[f"D{row}"], round3[f"F{row}"]
        hadv = number(round3, f"I{row}")
        match = TP.project_match(
            replay["level"], replay["strengths"][home], replay["strengths"][away], hadv
        )
        label = f"Round 3 row {row} ({home} v {away})"
        close(match.home_points, number(round3, f"J{row}"), f"{label} J")
        close(match.away_points, number(round3, f"K{row}"), f"{label} K")
        close(match.margin, number(round3, f"L{row}"), f"{label} L margin")
        close(match.combined_points, number(round3, f"O{row}"), f"{label} O")
        close(match.combined_availability_effect, number(round3, f"P{row}"), f"{label} P")
        close(match.home_full_strength, number(round3, f"Y{row}"), f"{label} Y full home")
        close(match.away_full_strength, number(round3, f"Z{row}"), f"{label} Z full away")
        # the printed summary, which uses the club names in E and G and pins the rounding
        summary = TP.summary_text(match.margin, round3[f"E{row}"], round3[f"G{row}"])
        assert summary == round3[f"M{row}"], f"{label} M: {summary!r} vs {round3[f'M{row}']!r}"


def test_the_replay_includes_the_neutral_game_and_the_capped_club(replay) -> None:
    round3 = replay["round3"]
    home_advantages = [number(round3, f"I{row}") for row in range(7, 17)]
    assert 0.0 in home_advantages  # the neutral-venue game is in the set
    assert home_advantages.count(replay["home_advantage"]) == 9
    binding = [code for code, r in replay["layer"].items() if r.cap_binding]
    assert binding, "no club in the workbook has its recovered scoring capped"
    for code in binding:
        r = replay["layer"][code]
        # the sheet credits the team with points the capped boost cannot give any player
        assert r.unassigned_points > 0
        credited = sum(p.projected_points for p in r.players)
        assert abs((r.squad_points - credited) - r.unassigned_points) < TOLERANCE


def test_the_default_policy_makes_the_capped_team_agree_with_its_players(replay) -> None:
    """Under ``consistent`` the capped club's projection is lower than the sheet's and equals
    the sum of its players: the documented difference from the workbook."""
    per40, absorb, cap, rotation = replay["levers"]  # the workbook's own levers
    consistent = IL.InjurySettings.from_per40(per40, absorb, cap, rotation_share=rotation)
    for code, r in replay["layer"].items():
        if not r.cap_binding:
            continue
        players = [p for _, p in replay["roster"][code]]
        ours = IL.apply_injury_layer(players, consistent)
        assert ours.squad_points < r.squad_points
        assert abs(sum(p.projected_points for p in ours.players) - ours.squad_points) < TOLERANCE
