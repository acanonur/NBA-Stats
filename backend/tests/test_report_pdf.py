"""The NBA injury report: the PDF parser, how its rows are stored, and the job that fetches it.

No real report was available when this was written (every NBA host is denied in the development
environment, and the layout has changed three times since 2021-22), so the PDFs here are built by
the tests themselves, with invented teams and players and the documented geometry: seven columns,
a header repeated on every page, a title above, ``Page n of m`` below, date, time, matchup and team
printed once and then left blank, long reasons wrapped onto a second line, a row that breaks across
a page, and ``NOT YET SUBMITTED`` where a team has filed nothing. What these tests prove is the
parser's *behaviour*, above all that it fails closed:

* the header is the contract: a renamed column, or a page with text and no header, yields no
  entries and ``headerMismatch``, never a best guess;
* a line it cannot understand is dropped and counted, the snapshot is ``partial``, and the team the
  line belonged to is not marked ``submitted``;
* when most lines are dropped the whole parse is refused (layout drift);
* ``NOT YET SUBMITTED`` is kept distinct from an empty list;
* ``pypdf`` is imported lazily, and its absence is a stated condition, not a crash.

Then the rows' journey into the database (team, game and player matching that is unique or absent,
the review queue, append-only corrections, the shared selection rules) and the ``nba.injuries``
job (walk-back, unchanged files, cadence, the 403 quirk, the persisted breaker, the synthetic-store
refusal). If real PDFs are present in ``tests/local/nba_injury/`` (git-ignored) a final test parses
them; otherwise it skips.
"""

from __future__ import annotations

import ast
import io
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

import httpx
import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from nbastats import config
from nbastats import db as db_module
from nbastats.db import init_db
from nbastats.models import Game, Player, PlayerGameBasic, Team
from nbastats.nba_intel import jobs, report_fetch, report_pdf, status, store
from nbastats.nba_intel.models import (
    NbaIntelRawFetch,
    NbaIntelSnapshot,
    NbaIntelSourceState,
    NbaIntelStatus,
    NbaIntelTeamReport,
)
from nbastats.nba_intel.report_pdf import (
    EXPECTED_HEADER,
    ParsedReport,
    Run,
    parse_pages,
    parse_report,
    reason_category,
)
from nbastats.shared import availability as avail
from nbastats.intel.http import PoliteClient

pytest.importorskip("pypdf", reason="the injuries extra (pypdf) is needed to read PDFs")

COLS = (30, 100, 175, 235, 345, 465, 535)
PAGE_WIDTH = 792


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("these tests must never open a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


# ----------------------------------------------------------------------- building a PDF


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


@dataclass
class PageSpec:
    """One page of a synthetic report. ``rows`` are seven-cell lists, top to bottom."""

    rows: list[list[str]] = field(default_factory=list)
    title: str | None = "Injury Report: 10/22/25 05:30 PM"
    footer: str | None = "auto"
    dx: float = 0.0
    header: Sequence[str] = EXPECTED_HEADER
    show_header: bool = True
    single_block: bool = False
    rotate: int = 0
    split_header_words: bool = False
    kerned: bool = False
    leading: float = 11.0
    header_y: float = 545.0
    footer_shares_baseline: bool = False
    #: Move each header word this many points right of its column's data, to model a layout whose
    #: headers are not aligned with the cells beneath them.
    header_offsets: Sequence[float] = (0.0,) * 7


def build_report(pages: Sequence[PageSpec]) -> bytes:
    """Hand-write a PDF: a Helvetica content stream per page, positioned with ``Tm``."""
    streams: list[str] = []
    for number, spec in enumerate(pages, start=1):
        out: list[str] = []

        def place(x: float, y: float, value: str, _spec: PageSpec = spec) -> None:
            if _spec.rotate == 90:  # display (X, Y) lives at user space (W - Y, X), drawn rotated
                matrix = f"0 1 -1 0 {PAGE_WIDTH - y} {x}"
            else:
                matrix = f"1 0 0 1 {x} {y}"
            if _spec.kerned and len(value) > 3:
                half = len(value) // 2
                shown = f"[({_escape(value[:half])}) 15 ({_escape(value[half:])})] TJ"
            else:
                shown = f"({_escape(value)}) Tj"
            if _spec.single_block:
                out.append(f"{matrix} Tm {shown}")
            else:
                out.append(f"BT /F1 8 Tf {matrix} Tm {shown} ET")

        if spec.single_block:
            out.append("BT /F1 8 Tf")
        if spec.title:
            place(30, 575, spec.title)
        if spec.show_header:
            for index, name in enumerate(spec.header):
                words = name.split(" ")
                left = COLS[index] + spec.dx + spec.header_offsets[index]
                if spec.split_header_words and len(words) > 1:
                    place(left, spec.header_y, words[0])
                    place(left + 28, spec.header_y, " ".join(words[1:]))
                else:
                    place(left, spec.header_y, name)
        y = spec.header_y - 16
        for cells in spec.rows:
            for index, value in enumerate(cells):
                if value:
                    place(COLS[index] + spec.dx, y, value)
            y -= spec.leading
        footer = f"Page {number} of {len(pages)}" if spec.footer == "auto" else spec.footer
        if footer:
            if spec.footer_shares_baseline:
                place(30, 30, "NBA Official Injury Report")
            place(600 if spec.footer_shares_baseline else 380, 30, footer)
        if spec.single_block:
            out.append("ET")
        streams.append("\n".join(out))

    objects: list[bytes] = [b"", b""]
    font = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    objects.append(font)
    kids: list[int] = []
    for spec, content in zip(pages, streams):
        data = content.encode("latin-1")
        objects.append(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
        stream_id = len(objects)
        rotate = f" /Rotate {spec.rotate}" if spec.rotate else ""
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} 612]{rotate} "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {stream_id} 0 R >>"
            ).encode()
        )
        kids.append(len(objects))
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    ).encode()
    buffer = io.BytesIO()
    buffer.write(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(buffer.tell())
        buffer.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = buffer.tell()
    buffer.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        buffer.write(b"%010d 00000 n \n" % offset)
    buffer.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    )
    return buffer.getvalue()


# Two games on 22 Oct 2025: AAA@BBB and CCC@DDD. Every name is invented.
PAGE_ONE: list[list[str]] = [
    [
        "10/22/2025",
        "07:30 (ET)",
        "AAA@BBB",
        "Alpha City Aces",
        "Sample, Alex",
        "Out",
        "Injury/Illness - Left Knee;",
    ],
    ["", "", "", "", "", "", "Soreness"],
    ["", "", "", "", "Tester Jr., Bo", "Questionable", "Rest"],
    ["", "", "", "Bravo Town Bees", "NOT YET SUBMITTED", "", ""],
    [
        "",
        "10:00 (ET)",
        "CCC@DDD",
        "Charlie Bay Cats",
        "Mock, Casey",
        "Probable",
        "Injury/Illness - Illness",
    ],
    ["", "", "", "Delta Park Dogs", "Fake, Dana", "Doubtful", "Personal Reasons"],
]
PAGE_TWO: list[list[str]] = [
    ["", "", "", "", "Third, Eli", "Out", "G League - Two-Way"],
    ["", "", "", "", "Zed, Fay", "Available", "Injury/Illness - Right Ankle; Sprain"],
]


def standard_report(**overrides: Any) -> bytes:
    first = PageSpec(rows=[list(r) for r in PAGE_ONE], **overrides)
    second = PageSpec(rows=[list(r) for r in PAGE_TWO], dx=2.0, **overrides)
    return build_report([first, second])


# --------------------------------------------------------------------- the parser: happy


def test_a_two_page_report_is_read_row_by_row() -> None:
    report = parse_report(standard_report())
    assert report.status == "ok" and report.pages == 2 and report.dropped == 0
    assert report.errors == ()
    people = [(r.team_name, r.player_name_raw, r.status, r.reason_raw) for r in report.rows]
    assert people == [
        ("Alpha City Aces", "Sample, Alex", "out", "Injury/Illness - Left Knee; Soreness"),
        ("Alpha City Aces", "Tester Jr., Bo", "questionable", "Rest"),
        ("Charlie Bay Cats", "Mock, Casey", "probable", "Injury/Illness - Illness"),
        ("Delta Park Dogs", "Fake, Dana", "doubtful", "Personal Reasons"),
        ("Delta Park Dogs", "Third, Eli", "out", "G League - Two-Way"),
        ("Delta Park Dogs", "Zed, Fay", "available", "Injury/Illness - Right Ankle; Sprain"),
    ]


def test_date_time_matchup_and_team_are_forward_filled_across_the_page_break() -> None:
    report = parse_report(standard_report())
    third = next(r for r in report.rows if r.player_name_raw == "Third, Eli")
    assert (third.page, third.game_date, third.matchup) == (2, date(2025, 10, 22), "CCC@DDD")
    assert (third.away_abbr, third.home_abbr) == ("CCC", "DDD")
    assert third.game_time_raw == "10:00 (ET)" and third.team_name == "Delta Park Dogs"
    first = report.rows[0]
    assert first.game_time_raw == "07:30 (ET)" and first.matchup == "AAA@BBB"


def test_wrapped_reasons_are_joined_and_each_page_uses_its_own_header_edges() -> None:
    report = parse_report(standard_report())  # page two is shifted two points right
    assert report.rows[0].reason_raw == "Injury/Illness - Left Knee; Soreness"
    assert {r.page for r in report.rows} == {1, 2}


def test_not_yet_submitted_is_its_own_state_and_not_an_empty_list() -> None:
    report = parse_report(standard_report())
    states = {(m.team_name, m.state) for m in report.teams}
    assert ("Bravo Town Bees", "notYetSubmitted") in states
    assert ("Alpha City Aces", "submitted") in states
    assert all(r.team_name != "Bravo Town Bees" for r in report.rows)
    assert report.teams and all(m.clean for m in report.teams)


def test_the_title_band_gives_the_as_of_time_and_the_footer_is_dropped() -> None:
    report = parse_report(standard_report())
    assert report.as_of_et == datetime(2025, 10, 22, 17, 30)
    assert all("Page" not in (r.reason_raw or "") for r in report.rows)


def test_a_footer_that_shares_a_baseline_with_other_text_is_still_a_footer() -> None:
    report = parse_report(standard_report(footer_shares_baseline=True))
    assert report.status == "ok" and len(report.rows) == 6


@pytest.mark.parametrize(
    "overrides",
    [
        {"single_block": True},
        {"kerned": True},
        {"split_header_words": True},
        {"single_block": True, "kerned": True, "split_header_words": True},
    ],
    ids=["one-block-per-page", "kerned-text-arrays", "split-header-words", "all-three"],
)
def test_the_producers_block_structure_does_not_change_the_result(
    overrides: dict[str, Any],
) -> None:
    assert parse_report(standard_report(**overrides)).rows == parse_report(standard_report()).rows


def test_a_rotated_page_is_read_in_display_orientation() -> None:
    report = parse_report(standard_report(rotate=90))
    assert report.status == "ok"
    assert [r.player_name_raw for r in report.rows] == [
        r.player_name_raw for r in parse_report(standard_report()).rows
    ]


def test_a_wrapped_team_name_and_a_wrapped_time_are_rejoined() -> None:
    rows = [
        ["10/22/2025", "07:30", "AAA@BBB", "Alpha City", "Sample, Alex", "Out", "Rest"],
        ["", "(ET)", "", "Aces", "", "", ""],
        ["", "", "", "", "Tester, Bo", "Probable", "Rest"],
    ]
    report = parse_report(build_report([PageSpec(rows=rows)]))
    assert report.status == "ok"
    assert {r.team_name for r in report.rows} == {"Alpha City Aces"}
    assert report.rows[0].game_time_raw == "07:30 (ET)"


def test_a_player_name_wrapped_after_its_comma_is_rejoined() -> None:
    rows = [
        [
            "10/22/2025",
            "07:30 (ET)",
            "AAA@BBB",
            "Alpha City Aces",
            "Longsurname-Hyphen,",
            "Out",
            "Rest",
        ],
        ["", "", "", "", "Given", "", ""],
    ]
    report = parse_report(build_report([PageSpec(rows=rows)]))
    assert report.status == "ok" and report.rows[0].player_name_raw == "Longsurname-Hyphen, Given"


def test_a_header_only_report_is_empty_not_a_failure() -> None:
    report = parse_report(build_report([PageSpec(rows=[]), PageSpec(rows=[])]))
    assert report.status == "empty" and report.rows == () and report.teams == ()


def test_a_blank_trailing_page_is_ignored() -> None:
    blank = PageSpec(rows=[], title=None, show_header=False, footer=None)
    report = parse_report(build_report([PageSpec(rows=[list(r) for r in PAGE_ONE]), blank]))
    assert report.status == "ok" and len(report.rows) == 4


# ------------------------------------------------------------ the parser: fails closed


def test_a_renamed_header_column_yields_no_entries() -> None:
    renamed = ["Game Date", "Game Time", "Matchup", "Team", "Player Name", "Status", "Reason"]
    report = parse_report(standard_report(header=renamed))
    assert report.status == "headerMismatch"
    assert report.rows == () and report.teams == ()
    assert report.errors and "header" in report.errors[0]


def test_the_header_names_are_matched_case_and_space_insensitively() -> None:
    shouted = [name.upper() for name in EXPECTED_HEADER]
    assert parse_report(standard_report(header=shouted)).status == "ok"


def test_a_page_with_text_but_no_header_fails_the_whole_report() -> None:
    good = PageSpec(rows=[list(r) for r in PAGE_ONE])
    headless = PageSpec(rows=[list(r) for r in PAGE_TWO], show_header=False)
    report = parse_report(build_report([good, headless]))
    assert report.status == "headerMismatch" and report.rows == ()
    assert "page 2" in report.errors[0]


def test_headers_out_of_order_are_not_accepted() -> None:
    swapped = list(EXPECTED_HEADER)
    swapped[3], swapped[4] = swapped[4], swapped[3]
    assert parse_report(standard_report(header=swapped)).status == "headerMismatch"


def test_an_unknown_status_drops_the_line_and_poisons_only_its_team() -> None:
    rows = [list(r) for r in PAGE_ONE]
    rows[5][5] = "Maybe"  # Fake, Dana: "Doubtful" -> "Maybe"
    pages = [PageSpec(rows=rows), PageSpec(rows=[list(r) for r in PAGE_TWO], dx=2.0)]
    report = parse_report(build_report(pages))
    assert report.status == "partial" and report.dropped == 1
    assert "'Maybe' is not a status" in report.parse_error  # type: ignore[operator]
    assert all(r.player_name_raw != "Fake, Dana" for r in report.rows)
    clean = {m.team_name: m.clean for m in report.teams}
    assert clean["Delta Park Dogs"] is False  # a line of its block was lost
    assert clean["Alpha City Aces"] is True and clean["Charlie Bay Cats"] is True


def test_a_team_whose_only_line_was_dropped_has_no_state_at_all() -> None:
    rows = [list(r) for r in PAGE_ONE]
    rows[5][5] = "Maybe"
    report = parse_report(build_report([PageSpec(rows=rows), PageSpec(rows=[])]))
    assert report.status == "partial"
    assert "Delta Park Dogs" not in {m.team_name for m in report.teams}


def test_a_name_without_a_comma_is_dropped() -> None:
    rows = [["10/22/2025", "07:30 (ET)", "AAA@BBB", "Alpha City Aces", "Sample Alex", "Out", ""]]
    rows.append(["", "", "", "", "Tester, Bo", "Probable", ""])
    report = parse_report(build_report([PageSpec(rows=rows)]))
    assert report.status == "partial" and [r.player_name_raw for r in report.rows] == ["Tester, Bo"]


def test_a_new_matchup_without_a_team_is_not_guessed() -> None:
    rows = [
        ["10/22/2025", "07:30 (ET)", "AAA@BBB", "Alpha City Aces", "Sample, Alex", "Out", ""],
        ["", "10:00 (ET)", "CCC@DDD", "", "Mock, Casey", "Out", ""],
        ["", "", "", "", "Fake, Dana", "Out", ""],
    ]
    report = parse_report(build_report([PageSpec(rows=rows)]))
    assert report.status == "partial"
    assert [r.player_name_raw for r in report.rows] == ["Sample, Alex", "Fake, Dana"]
    # The surviving Dana row must not be attributed to the *previous* game's team silently:
    assert report.rows[1].matchup == "AAA@BBB"


def test_when_most_lines_are_unreadable_the_layout_is_not_trusted() -> None:
    rows = [["10/22/2025", "07:30 (ET)", "AAA@BBB", "Alpha City Aces", "Sample, Alex", "Out", ""]]
    rows += [["", "", "", "", f"Broken{n}", "Out", ""] for n in range(5)]  # no comma: dropped
    report = parse_report(build_report([PageSpec(rows=rows)]))
    assert report.status == "headerMismatch" and report.rows == ()
    assert "layout is not understood" in report.errors[0]


def test_misaligned_columns_read_as_unreadable_not_as_wrong_data() -> None:
    """If data sat well left of its header, cells land in the wrong columns. The row checks turn
    that into no entries (or entries that are exactly right), never wrong ones."""
    offsets = (0.0, 0.0, 0.0, 0.0, 60.0, 60.0, 0.0)  # Player Name and Current Status headers
    report = parse_report(
        build_report([PageSpec(rows=[list(r) for r in PAGE_ONE], header_offsets=offsets)])
    )
    assert report.status == "headerMismatch" and report.rows == () and report.teams == ()
    assert report.errors


def test_a_small_misalignment_inside_the_slack_is_tolerated() -> None:
    offsets = (0.0, 0.0, 0.0, 0.0, 2.5, 2.5, 2.5)  # within the 3 point slack
    report = parse_report(
        build_report([PageSpec(rows=[list(r) for r in PAGE_ONE], header_offsets=offsets)])
    )
    assert report.status == "ok" and len(report.rows) == 4


@pytest.mark.parametrize(
    "data",
    [b"", b"hello", b"%PDF-1.4\nnot really a pdf", b"%PDF-1.4\n" + b"\x00" * 200],
)
def test_bytes_that_are_not_a_readable_pdf_fail_closed(data: bytes) -> None:
    report = parse_report(data)
    assert report.status == "headerMismatch" and report.rows == () and report.errors


def test_an_encrypted_pdf_is_unreadable_not_a_crash() -> None:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(standard_report())))
    writer.encrypt("a-password-nobody-has")
    buffer = io.BytesIO()
    writer.write(buffer)
    report = parse_report(buffer.getvalue())
    assert report.status == "headerMismatch" and report.errors


def test_a_pdf_with_too_many_pages_is_refused() -> None:
    pages = [PageSpec(rows=[]) for _ in range(report_pdf.MAX_PAGES + 1)]
    report = parse_report(build_report(pages))
    assert report.status == "headerMismatch" and "pages" in report.errors[0]


# -------------------------------------------------------------- the parser: pure geometry


def run(text_: str, x: float, y: float, seq: int = 0) -> Run:
    return Run(text_, x, y, 8.0, seq)


def test_parse_pages_works_on_plain_runs_with_no_pdf_at_all() -> None:
    header = [run(name, COLS[i], 545) for i, name in enumerate(EXPECTED_HEADER)]
    body = [
        run("10/22/2025", COLS[0], 529),
        run("AAA@BBB", COLS[2], 529),
        run("Alpha City Aces", COLS[3], 529),
        run("Sample, Alex", COLS[4], 529),
        run("Out", COLS[5], 529),
        run("Rest", COLS[6], 529),
    ]
    report = parse_pages([header + body])
    assert report.status == "ok" and report.rows[0].player_name_raw == "Sample, Alex"
    assert parse_pages([]).status == "headerMismatch"
    assert parse_pages([[run("only text", 10, 10)]]).status == "headerMismatch"


def test_runs_on_slightly_different_baselines_belong_to_one_line() -> None:
    header = [run(name, COLS[i], 545) for i, name in enumerate(EXPECTED_HEADER)]
    body = [
        run("10/22/2025", COLS[0], 529.0),
        run("AAA@BBB", COLS[2], 528.2),
        run("Alpha City Aces", COLS[3], 529.4),
        run("Sample, Alex", COLS[4], 529.0),
        run("Out", COLS[5], 528.8),
    ]
    assert parse_pages([header + body]).rows[0].status == "out"


def test_a_header_name_split_over_runs_is_found_and_stray_runs_before_it_are_skipped() -> None:
    header = [run("Injury Report", 5, 545)]
    for i, name in enumerate(EXPECTED_HEADER):
        for j, word in enumerate(name.split(" ")):
            header.append(run(word, COLS[i] + 28 * j, 545))
    assert report_pdf._match_header(report_pdf._group_lines(header)[0]) is not None  # noqa: SLF001
    # ... but a stray run in the middle of the header disqualifies the line.
    header.insert(4, run("INTRUDER", COLS[1] + 10, 545))
    assert report_pdf._match_header(report_pdf._group_lines(header)[0]) is None  # noqa: SLF001


def test_the_parsed_report_is_a_plain_value() -> None:
    report = parse_report(standard_report())
    assert isinstance(report, ParsedReport) and report.entry_count == 6
    assert report.parse_error is None
    partial = ParsedReport("partial", errors=("a",), dropped=3)
    assert partial.parse_error == "a; and 2 more"


# ---------------------------------------------------------------- the reason mapping


@pytest.mark.parametrize(
    "reason, category",
    [
        ("Injury/Illness - Left Knee; Soreness", "injury"),
        ("Injury/Illness - Right Ankle; Sprain", "injury"),
        ("Injury/Illness - Illness", "illness"),
        ("Injury/Illness - Illness (flu-like symptoms)", "illness"),
        ("Injury/Illness - COVID-19", "illness"),
        ("Injury/Illness", "injury"),
        ("Rest", "rest"),
        ("Rest - Back-to-Back", "rest"),
        ("Personal Reasons", "personal"),
        ("League Suspension", "suspension"),
        ("G League - Two-Way", "gLeague"),
        ("G League - On Assignment", "gLeague"),
        ("Not With Team - Personal Reasons", "notWithTeam"),
        ("Not with Team", "notWithTeam"),
        ("Return to Competition Reconditioning", "other"),
        ("Something nobody has seen", "other"),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_reason_mapping(reason: str | None, category: str | None) -> None:
    assert reason_category(reason) == category
    if category is not None:
        assert avail.normalise_reason_category(category) == category


# ----------------------------------------------------------- pypdf is lazy and optional


def test_pypdf_is_never_imported_at_module_level() -> None:
    source = Path(report_pdf.__file__).read_text()
    for node in ast.parse(source).body:
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] != "pypdf" for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "pypdf"


def test_importing_the_package_does_not_load_pypdf() -> None:
    code = (
        "import sys, nbastats.nba_intel.report_pdf, nbastats.nba_intel.jobs, nbastats.db;"
        "sys.exit(1 if 'pypdf' in sys.modules else 0)"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_a_missing_pypdf_is_a_stated_condition(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pypdf", None)  # makes `import pypdf` raise ImportError
    with pytest.raises(report_pdf.ReportParserUnavailable) as raised:
        parse_report(standard_report())
    assert "injuries" in str(raised.value)
    assert report_pdf.pypdf_available() in (True, False)  # does not raise


def test_the_extra_is_declared() -> None:
    import tomllib

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    extras = tomllib.loads(pyproject.read_text())["project"]["optional-dependencies"]
    assert extras["injuries"] == ["pypdf>=4"]


# ================================================================= into the database


@pytest.fixture()
def job_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'intel.db'}")
    monkeypatch.setenv("HARDWOOD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("HARDWOOD_NBA_INJURIES", raising=False)
    monkeypatch.delenv(report_fetch.URL_TEMPLATE_ENV, raising=False)
    config.reset_settings_cache()
    db_module.dispose_engine()
    engine = db_module.get_engine()
    init_db(engine)
    with Session(engine) as session:
        add_league(session)
        session.commit()
    try:
        yield engine
    finally:
        db_module.dispose_engine()
        config.reset_settings_cache()


def add_league(session: Session) -> None:
    """Four invented teams, four games on 22 Oct 2025 (two of them scheduled), and players."""
    teams = [
        (9101, "AAA", "Alpha City Aces", "Alpha City", "Aces"),
        (9102, "BBB", "Bravo Town Bees", "Bravo Town", "Bees"),
        (9103, "CCC", "Charlie Bay Cats", "Charlie Bay", "Cats"),
        (9104, "DDD", "Delta Park Dogs", "Delta Park", "Dogs"),
    ]
    for team_id, abbr, name, city, nickname in teams:
        session.add(Team(team_id=team_id, abbr=abbr, name=name, city=city, nickname=nickname))

    def game(game_id: str, day: date, season: str, home: int, away: int, status_: str) -> None:
        session.add(
            Game(
                game_id=game_id,
                game_date=day,
                season=season,
                season_type="Regular Season",
                home_team_id=home,
                away_team_id=away,
                status=status_,
                data_source="nba_api",
            )
        )

    game("T-0001", date(2025, 10, 22), "2025-26", 9102, 9101, "scheduled")  # AAA@BBB
    game("T-0002", date(2025, 10, 22), "2025-26", 9104, 9103, "scheduled")  # CCC@DDD
    game("T-0003", date(2025, 10, 20), "2025-26", 9101, 9103, "final")
    game("T-0004", date(2025, 10, 21), "2025-26", 9104, 9102, "final")
    game("T-0005", date(2025, 4, 10), "2024-25", 9104, 9101, "final")
    players = {
        8001: "Alex Sample",
        8002: "Bo Tester Jr.",
        8003: "Casey Mock",
        8004: "Casey Mock",
        8005: "Dana Fake",
        8006: "Eli Third",
        8007: "Gina Other",
    }
    for player_id, name in players.items():
        session.add(Player(player_id=player_id, full_name=name, is_active=True))
    session.flush()

    def line(game_id: str, player_id: int, team_id: int) -> None:
        session.add(PlayerGameBasic(game_id=game_id, player_id=player_id, team_id=team_id))

    line("T-0003", 8001, 9101)
    line("T-0003", 8002, 9101)
    line("T-0003", 8003, 9103)  # two Casey Mocks on one roster: ambiguous
    line("T-0003", 8004, 9103)
    line("T-0004", 8005, 9104)
    line("T-0005", 8006, 9104)  # Eli Third: only last season's line for his team
    line("T-0004", 8007, 9102)


def ingest(engine: Engine, pdf: bytes, *, slot: datetime | None = None) -> status.IngestResult:
    parsed = parse_report(pdf)
    with Session(engine) as session:
        result = status.ingest_report(
            session,
            parsed,
            url="https://static.example.org/Injury-Report_x.pdf",
            slot_at_utc=slot or datetime(2025, 10, 22, 21, 30),
            sha256="a" * 64,
            fetched_at=datetime(2025, 10, 22, 21, 35),
        )
        session.commit()
    return result


def test_rows_are_stored_with_their_source_and_the_sources_own_time(job_db: Engine) -> None:
    result = ingest(job_db, standard_report())
    assert result.parse_status == "ok" and result.stored == 6 and result.teams_marked == 4
    with Session(job_db) as session:
        rows = (
            session.execute(select(NbaIntelStatus).order_by(NbaIntelStatus.status_id))
            .scalars()
            .all()
        )
        first = rows[0]
        assert first.team_id == 9101 and first.player_id == 8001 and first.game_id == "T-0001"
        assert first.status == "out" and first.status_raw == "Out"
        assert first.reason_category == "injury"
        assert first.reason_text == "Injury/Illness - Left Knee; Soreness"
        assert first.game_date == date(2025, 10, 22)
        assert (
            first.source_kind == "leagueReport" and first.source_label == status.REPORT_SOURCE_LABEL
        )
        assert first.source_url == "https://static.example.org/Injury-Report_x.pdf"
        # 5:30 PM Eastern on 22 Oct 2025 (EDT, UTC-4) is 21:30 UTC, and it is the *report's* time
        assert first.source_published_at == datetime(2025, 10, 22, 21, 30)
        assert first.as_of == first.source_published_at
        assert first.recorded_at != first.source_published_at
        assert first.status_id and first.snapshot_id == result.snapshot_id
        assert first.expected_return_text is None and first.expected_return_date is None
        snapshot = session.get(NbaIntelSnapshot, result.snapshot_id)
        assert snapshot is not None and snapshot.row_count == 6 and snapshot.parse_status == "ok"
        assert snapshot.report_as_of_utc == datetime(2025, 10, 22, 21, 30)
        reports = {
            (r.team_id, r.game_id): r.state
            for r in session.execute(select(NbaIntelTeamReport)).scalars()
        }
    assert reports == {
        (9101, "T-0001"): "submitted",
        (9102, "T-0001"): "notYetSubmitted",
        (9103, "T-0002"): "submitted",
        (9104, "T-0002"): "submitted",
    }


def test_players_are_matched_only_when_the_match_is_unique(job_db: Engine) -> None:
    ingest(job_db, standard_report())
    with Session(job_db) as session:
        matched = {
            r.player_name_raw: r.player_id
            for r in session.execute(select(NbaIntelStatus)).scalars()
        }
        notes = {
            r.player_name_raw: r.provenance_note
            for r in session.execute(select(NbaIntelStatus)).scalars()
        }
    assert matched["Sample, Alex"] == 8001
    assert matched["Tester Jr., Bo"] == 8002  # "Jr." in the report's surname position
    assert matched["Fake, Dana"] == 8005
    assert matched["Third, Eli"] == 8006  # only last season's line for his team
    assert "priorLines" in notes["Third, Eli"] and "lines" in notes["Sample, Alex"]
    assert matched["Mock, Casey"] is None  # two Casey Mocks on that roster: never guessed
    assert matched["Zed, Fay"] is None  # nobody by that name


def test_the_review_queue_lists_unmatched_rows_and_a_link_appends_not_edits(
    job_db: Engine,
) -> None:
    ingest(job_db, standard_report())
    as_of = datetime(2025, 10, 23, 0, 0)
    with Session(job_db) as session:
        queue = status.review_queue(session)
        assert sorted(item.player_name for item in queue) == ["Mock, Casey", "Zed, Fay"]
        target = next(item for item in queue if item.player_name == "Zed, Fay")
        mock_item = next(item for item in queue if item.player_name == "Mock, Casey")
        before = session.execute(select(func.count()).select_from(NbaIntelStatus)).scalar()

        replacement = status.resolve_review_item(
            session, target.status_id, 8007, user_id="user-1", now=datetime(2025, 10, 22, 22, 0)
        )
        session.commit()
        total = session.execute(select(func.count()).select_from(NbaIntelStatus)).scalar()
        assert total == before + 2  # a retraction and a replacement, nothing edited
        original = session.get(NbaIntelStatus, target.status_id)
        assert original is not None and original.player_id is None  # never rewritten
        assert [i.player_name for i in status.review_queue(session)] == ["Mock, Casey"]

        # The shared selection rules understand the pair. The linked player gets the entry ...
        rows = (
            session.execute(
                select(NbaIntelStatus).where(NbaIntelStatus.player_name_raw == "Zed, Fay")
            )
            .scalars()
            .all()
        )
        entries = [status.status_entry(r) for r in rows]
        linked = [e for e in entries if e.player_key == 8007]
        assert [e.entry_id for e in linked] == [replacement] and linked[0].status == "available"
        chosen = avail.select_effective_entry(linked, game_id="T-0002", as_of=as_of)
        assert chosen.rule == "gameEntry" and chosen.entry is not None
        assert chosen.entry.entry_id == replacement
        # ... and the unmatched original is retracted, so it is nobody's entry any more.
        unmatched = [e for e in entries if e.player_key is None]
        assert any(e.retracts == target.status_id for e in unmatched)
        assert avail.select_effective_entry(unmatched, game_id="T-0002", as_of=as_of).rule == "none"

        retraction_id = next(e.entry_id for e in unmatched if e.retracts is not None)
        with pytest.raises(ValueError):
            status.resolve_review_item(session, replacement, 8007)  # it has a player now
        with pytest.raises(ValueError):
            status.resolve_review_item(session, target.status_id, 8007)  # already linked once
        with pytest.raises(ValueError):
            status.resolve_review_item(session, retraction_id, 8007)  # a retraction is not a row
        with pytest.raises(ValueError):
            status.resolve_review_item(session, 999_999, 8007)
        with pytest.raises(status.UnknownPlayerError):
            status.resolve_review_item(session, mock_item.status_id, 999_999)
        assert session.execute(select(func.count()).select_from(NbaIntelStatus)).scalar() == total


def test_status_rows_cannot_be_updated(job_db: Engine) -> None:
    from nbastats.nba_intel.models import AppendOnlyError

    ingest(job_db, standard_report())
    with Session(job_db) as session:
        row = session.execute(select(NbaIntelStatus)).scalars().first()
        assert row is not None
        row.status = "available"
        with pytest.raises(AppendOnlyError):
            session.flush()


def test_a_team_that_cannot_be_placed_is_not_stored_and_the_snapshot_is_partial(
    job_db: Engine,
) -> None:
    rows = [list(r) for r in PAGE_ONE]
    rows[0][3] = "Nowhere Nobodies"
    result = ingest(job_db, build_report([PageSpec(rows=rows), PageSpec(rows=[])]))
    assert result.parse_status == "partial" and result.unmatched_teams == 2
    with Session(job_db) as session:
        names = {r.player_name_raw for r in session.execute(select(NbaIntelStatus)).scalars()}
        snapshot = session.get(NbaIntelSnapshot, result.snapshot_id)
    assert "Sample, Alex" not in names
    assert snapshot is not None and "not a known team" in (snapshot.parse_error or "")


def test_an_unreadable_report_is_still_a_recorded_snapshot_with_no_entries(job_db: Engine) -> None:
    renamed = ["Game Date", "Game Time", "Matchup", "Team", "Player Name", "Status", "Reason"]
    result = ingest(job_db, standard_report(header=renamed))
    assert result.parse_status == "headerMismatch" and result.stored == 0
    with Session(job_db) as session:
        assert session.execute(select(func.count()).select_from(NbaIntelStatus)).scalar() == 0
        snapshot = session.get(NbaIntelSnapshot, result.snapshot_id)
        assert snapshot is not None and snapshot.parse_status == "headerMismatch"
        assert "header" in (snapshot.parse_error or "")


def test_the_titles_time_is_trusted_only_when_it_agrees_with_the_slot(job_db: Engine) -> None:
    pdf = build_report(
        [PageSpec(rows=[list(r) for r in PAGE_ONE], title="Injury Report: 10/22/25 05:30 PM")]
    )
    ingest(
        job_db, pdf, slot=datetime(2025, 10, 22, 21, 15)
    )  # a 5:15 PM file: title is 15 min later
    ingest(job_db, pdf, slot=datetime(2025, 10, 25, 21, 15))  # days away: the title is not trusted
    with Session(job_db) as session:
        snapshots = (
            session.execute(select(NbaIntelSnapshot).order_by(NbaIntelSnapshot.snapshot_id))
            .scalars()
            .all()
        )
    assert snapshots[0].report_as_of_utc == datetime(2025, 10, 22, 21, 30)
    assert snapshots[1].report_as_of_utc == datetime(2025, 10, 25, 21, 15)


# --------------------------------------------------------------- name and team matching


def test_name_folding_and_the_reports_name_order() -> None:
    assert status.fold_name("Dončić, Luka") == "doncic luka"
    assert status.fold_name("  O'Neal-Smith  Jr. ") == "o neal smith jr"
    assert status.fold_name("Ðorđe Łukasz Øyvind") == "dorde lukasz oyvind"
    assert status.report_name_to_display("Porter Jr., Michael") == "Michael Porter Jr."
    assert status.report_name_to_display("Nene") == "Nene"
    assert status.report_name_to_display("  Sample ,  Alex ") == "Alex Sample"


def test_player_pools_are_tried_in_order_and_ambiguity_stops_the_search(
    job_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    with Session(job_db) as session:
        session.execute(text("CREATE TABLE tmp_roster (player_id INT, season TEXT, team_id INT)"))
        session.execute(text("INSERT INTO tmp_roster VALUES (8007, '2025-26', 9104)"))
        session.commit()
        monkeypatch.setattr(
            status,
            "_ROSTER_SQL",
            "SELECT DISTINCT p.player_id, p.full_name FROM tmp_roster s "
            "JOIN players p ON p.player_id = s.player_id "
            "WHERE s.team_id = :team AND s.season = :season",
        )
        directory = status.PlayerDirectory(session)
        directory._has_roster = True  # noqa: SLF001
        # a roster listing finds a player with no line yet this season
        assert directory.resolve(9104, "2025-26", "Other, Gina") == (8007, "roster")
        # a line this season is found first
        assert directory.resolve(9104, "2025-26", "Fake, Dana") == (8005, "lines")
        # last season's line is the last resort
        assert directory.resolve(9104, "2025-26", "Third, Eli") == (8006, "priorLines")
        # the wrong team never matches
        assert directory.resolve(9101, "2025-26", "Fake, Dana") == (None, None)
        # two of the same name in the pool that knows him: no match, and no looking further
        assert directory.resolve(9103, "2025-26", "Mock, Casey") == (None, None)
        # a generational suffix is tried both ways
        assert directory.resolve(9101, "2025-26", "Tester, Bo") == (8002, "lines")
        assert directory.resolve(9101, "2025-26", "Tester Jr., Bo") == (8002, "lines")


def test_team_names_match_uniquely_or_not_at_all(job_db: Engine) -> None:
    with Session(job_db) as session:
        session.add_all(
            [
                Team(
                    team_id=9201,
                    abbr="EEE",
                    name="Echo Falls Sparks",
                    city="Echo Falls",
                    nickname="Sparks",
                ),
                Team(
                    team_id=9202,
                    abbr="FFF",
                    name="Foxtrot Rock Sparks",
                    city="Foxtrot Rock",
                    nickname="Sparks",
                ),
            ]
        )
        session.flush()
        teams = status.TeamDirectory(session)
        assert teams.match("Alpha City Aces") == 9101
        assert teams.match("alpha city  ACES") == 9101
        assert teams.match("Los Alpha City Aces") == 9101  # ends with a unique nickname
        assert teams.match("Sparks") is None  # two teams share the nickname
        assert teams.match("Echo Falls Sparks") == 9201
        assert teams.match("Sparks", candidates=[9201]) == 9201  # a game narrows it
        assert teams.match("Nowhere Nobodies") is None
        assert teams.match("") is None
        assert teams.abbr(9101) == "AAA" and teams.abbr(1) is None


# ---------------------------------------------------------------------------- overrides


def test_an_override_needs_a_real_status_and_a_real_player(job_db: Engine) -> None:
    with Session(job_db) as session:
        row = status.add_override(
            session,
            player_id=8001,
            status="Questionable",
            user_id="u1",
            note="  heard at shootaround ",
            source_url="https://news.example.org/x",
            now=datetime(2025, 10, 22, 12, 0),
        )
        assert row.status == "questionable" and row.note == "heard at shootaround"
        assert row.cleared_at is None and row.entered_by_user_id == "u1"
        with pytest.raises(avail.InvalidStatusError):
            status.add_override(session, player_id=8001, status="healthy")
        with pytest.raises(avail.InvalidStatusError):
            status.add_override(session, player_id=8001, status="")
        with pytest.raises(status.InvalidOverrideError):
            status.add_override(session, player_id=8001, status=None)  # type: ignore[arg-type]
        with pytest.raises(status.UnknownPlayerError):
            status.add_override(session, player_id=424242, status="out")
        with pytest.raises(status.InvalidOverrideError):
            status.add_override(session, player_id=8001, status="out", source_url="javascript:1")


def test_a_denylisted_link_is_withheld_but_the_override_keeps_its_words(job_db: Engine) -> None:
    with Session(job_db) as session:
        row = status.add_override(
            session,
            player_id=8001,
            status="out",
            note="ruled out per a preview",
            source_url="https://www.mozzartsport.com/preview/1",
        )
        assert row.source_url is None
        assert row.note is not None and row.note.startswith("ruled out per a preview")
        assert "link withheld" in row.note


def test_overrides_can_be_cleared_once_and_listed(job_db: Engine) -> None:
    with Session(job_db) as session:
        one = status.add_override(session, player_id=8001, status="out", now=datetime(2025, 10, 1))
        two = status.add_override(
            session, player_id=8002, status="doubtful", now=datetime(2025, 10, 2)
        )
        assert [o.override_id for o in status.active_overrides(session)] == [
            two.override_id,
            one.override_id,
        ]
        assert [o.override_id for o in status.active_overrides(session, player_ids=[8001])] == [
            one.override_id
        ]
        cleared = status.clear_override(session, one.override_id, now=datetime(2025, 10, 3))
        assert cleared is not None and cleared.cleared_at == datetime(2025, 10, 3)
        again = status.clear_override(session, one.override_id, now=datetime(2025, 10, 9))
        assert again is not None and again.cleared_at == datetime(2025, 10, 3)  # not re-stamped
        assert status.clear_override(session, 999_999) is None
        assert [o.override_id for o in status.active_overrides(session)] == [two.override_id]


def test_the_shared_rules_pick_an_override_over_an_older_report_row(job_db: Engine) -> None:
    ingest(job_db, standard_report())
    report_at = datetime(2025, 10, 22, 21, 30)
    with Session(job_db) as session:
        report_row = (
            session.execute(select(NbaIntelStatus).where(NbaIntelStatus.player_id == 8001))
            .scalars()
            .one()
        )
        override = status.add_override(
            session, player_id=8001, status="probable", now=datetime(2025, 10, 22, 23, 0)
        )

        def pick(as_of: datetime, newest_report: datetime | None) -> avail.Effective:
            entries = [status.status_entry(report_row), status.override_entry(override)]
            return avail.select_effective_entry(
                entries, game_id="T-0001", as_of=as_of, newest_league_report_at=newest_report
            )

        night = datetime(2025, 10, 23, 0, 0)
        chosen = pick(night, report_at)
        assert chosen.rule == "override" and chosen.entry is not None
        assert chosen.entry.is_override and chosen.entry.status == "probable"
        # a league report that arrives after the override is entered supersedes it
        newer = pick(night, datetime(2025, 10, 22, 23, 30))
        assert newer.rule == "gameEntry" and newer.entry is not None and newer.entry.status == "out"
        # cleared, the report row applies again
        status.clear_override(session, override.override_id, now=datetime(2025, 10, 22, 23, 15))
        cleared = pick(night, report_at)
        assert cleared.rule == "gameEntry" and cleared.entry is not None
        assert cleared.entry.status == "out"
        assert status.override_entry(override).entry_id == f"override:{override.override_id}"


# ---------------------------------------------------------------- the fetch: slots and URLs


def test_slot_urls_use_the_easterns_twelve_hour_clock() -> None:
    template = report_fetch.DEFAULT_URL_TEMPLATE
    eastern = report_fetch.EASTERN
    assert report_fetch.slot_url(datetime(2026, 10, 22, 17, 30, tzinfo=eastern), template).endswith(
        "/referee/injury/Injury-Report_2026-10-22_05_30PM.pdf"
    )
    assert report_fetch.slot_url(datetime(2026, 10, 22, 0, 0, tzinfo=eastern)).endswith(
        "2026-10-22_12_00AM.pdf"
    )
    assert report_fetch.slot_url(datetime(2026, 10, 22, 12, 15, tzinfo=eastern)).endswith(
        "2026-10-22_12_15PM.pdf"
    )
    assert report_fetch.slot_url(datetime(2026, 10, 22, 9, 45, tzinfo=eastern)).endswith(
        "2026-10-22_09_45AM.pdf"
    )
    assert report_fetch.DEFAULT_URL_TEMPLATE.startswith(
        "https://ak-static.cms.nba.com/referee/injury/"
    )


def test_the_slot_round_trips_through_utc_and_the_url() -> None:
    slot = datetime(2026, 10, 22, 17, 30, tzinfo=report_fetch.EASTERN)
    utc = report_fetch.slot_to_utc(slot)
    assert utc == datetime(2026, 10, 22, 21, 30)  # EDT
    assert report_fetch.slot_from_utc(utc) == slot
    assert report_fetch.slot_from_url(report_fetch.slot_url(slot)) == slot
    winter = datetime(2026, 12, 1, 17, 30, tzinfo=report_fetch.EASTERN)
    assert report_fetch.slot_to_utc(winter) == datetime(2026, 12, 1, 22, 30)  # EST
    assert report_fetch.slot_from_url("https://example.org/not-a-report.pdf") is None


def test_the_floor_slot_is_the_current_quarter_hour_in_eastern() -> None:
    now = datetime(2026, 10, 22, 21, 44, 59, tzinfo=timezone.utc)  # 5:44:59 PM EDT
    assert report_fetch.floor_slot(now) == datetime(
        2026, 10, 22, 17, 30, tzinfo=report_fetch.EASTERN
    )
    assert report_fetch.floor_slot(datetime(2026, 10, 22, 21, 45)) == datetime(
        2026, 10, 22, 17, 45, tzinfo=report_fetch.EASTERN
    )


def test_a_run_steps_back_only_until_the_newest_slot_it_already_has() -> None:
    now = datetime(2026, 10, 22, 21, 40, tzinfo=timezone.utc)  # floor: 5:30 PM ET
    eastern = report_fetch.EASTERN
    fresh = report_fetch.candidate_slots(now, None)
    assert len(fresh) == report_fetch.MAX_STEPS == 8
    assert fresh[0] == datetime(2026, 10, 22, 17, 30, tzinfo=eastern)
    assert fresh[-1] == datetime(2026, 10, 22, 15, 45, tzinfo=eastern)
    have_5_15 = report_fetch.slot_to_utc(datetime(2026, 10, 22, 17, 15, tzinfo=eastern))
    assert report_fetch.candidate_slots(now, have_5_15) == [fresh[0]]
    have_5_30 = report_fetch.slot_to_utc(fresh[0])
    assert report_fetch.candidate_slots(now, have_5_30) == []
    have_4_00 = report_fetch.slot_to_utc(datetime(2026, 10, 22, 16, 0, tzinfo=eastern))
    assert len(report_fetch.candidate_slots(now, have_4_00)) == 6  # 5:30 back to 4:15, not 4:00


def test_the_url_template_setting_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    assert report_fetch.url_template({}) == report_fetch.DEFAULT_URL_TEMPLATE
    good = "https://reports.example.org/{date}/{hh}{mm}{ampm}.pdf"
    assert report_fetch.url_template({report_fetch.URL_TEMPLATE_ENV: good}) == good
    for bad in (
        "https://reports.example.org/{date}.pdf",
        "http://reports.example.org/{date}/{hh}{mm}{ampm}.pdf",
    ):
        with pytest.raises(report_fetch.InvalidTemplateError):
            report_fetch.url_template({report_fetch.URL_TEMPLATE_ENV: bad})


# -------------------------------------------------------------------- the fetch: cadence

GAME_DAY = date(2026, 10, 22)


def games_on(
    day: date, tipoffs: Sequence[datetime | None] = (None,)
) -> list[report_fetch.GameInfo]:
    return [report_fetch.GameInfo(f"g{n}", day, t) for n, t in enumerate(tipoffs)]


def et(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=report_fetch.EASTERN)


def test_nothing_is_polled_unless_a_game_is_within_36_hours() -> None:
    far = games_on(GAME_DAY + timedelta(days=3))
    decision = report_fetch.decide_poll(et(GAME_DAY, 12), far, None)
    assert not decision.poll and "36 hours" in decision.reason
    assert not report_fetch.decide_poll(et(GAME_DAY, 12), [], None).poll
    # a game that is already over (date passed) is not "within 36 hours" either
    past = games_on(GAME_DAY - timedelta(days=2))
    assert not report_fetch.decide_poll(et(GAME_DAY, 12), past, None).poll


def test_the_window_opens_at_5pm_the_day_before_and_closes_at_the_last_tip() -> None:
    games = games_on(GAME_DAY)
    before = report_fetch.decide_poll(et(GAME_DAY - timedelta(days=1), 16, 59), games, None)
    assert before.poll and not before.in_window and before.interval == timedelta(hours=1)
    opens = report_fetch.decide_poll(et(GAME_DAY - timedelta(days=1), 17, 0), games, None)
    assert opens.in_window and opens.interval == timedelta(minutes=15)
    assert report_fetch.decide_poll(et(GAME_DAY, 8, 0), games, None).in_window
    assert report_fetch.decide_poll(et(GAME_DAY, 22, 29), games, None).in_window  # assumed 22:30
    closed = report_fetch.decide_poll(et(GAME_DAY, 22, 31), games, None)
    assert not closed.in_window


def test_with_known_tip_offs_the_window_closes_at_the_last_one() -> None:
    tips = [
        et(GAME_DAY, 19, 30).astimezone(timezone.utc).replace(tzinfo=None),
        et(GAME_DAY, 21, 0).astimezone(timezone.utc).replace(tzinfo=None),
    ]
    games = games_on(GAME_DAY, tips)
    assert report_fetch.decide_poll(et(GAME_DAY, 20, 59), games, None).in_window
    assert not report_fetch.decide_poll(et(GAME_DAY, 21, 1), games, None).in_window


def test_the_interval_is_15_minutes_in_a_window_and_hourly_outside_it() -> None:
    games = games_on(GAME_DAY)
    inside = et(GAME_DAY, 15, 0)
    last = inside.astimezone(timezone.utc) - timedelta(minutes=10)
    assert not report_fetch.decide_poll(inside, games, last).poll
    assert report_fetch.decide_poll(inside, games, last - timedelta(minutes=4)).poll
    # a tick that lands a minute early is accepted (the worker's ticks are not exact)
    assert report_fetch.decide_poll(
        inside, games, inside.astimezone(timezone.utc) - timedelta(minutes=14)
    ).poll

    outside = et(GAME_DAY - timedelta(days=1), 10, 0)
    outside_utc = outside.astimezone(timezone.utc)
    hourly = report_fetch.decide_poll(outside, games, outside_utc - timedelta(minutes=30))
    assert not hourly.poll and "next in about" in hourly.reason
    assert report_fetch.decide_poll(outside, games, outside_utc - timedelta(minutes=59)).poll
    assert report_fetch.decide_poll(outside, games, outside_utc - timedelta(hours=2)).poll


def test_force_skips_the_interval_but_never_the_question_of_whether_there_is_a_game() -> None:
    games = games_on(GAME_DAY)
    now = et(GAME_DAY, 15, 0)
    just_now = now.astimezone(timezone.utc) - timedelta(minutes=1)
    assert report_fetch.decide_poll(now, games, just_now, force=True).poll
    assert not report_fetch.decide_poll(now, [], just_now, force=True).poll


def test_a_naive_last_probe_time_is_read_as_utc() -> None:
    games = games_on(GAME_DAY)
    now = et(GAME_DAY, 15, 0)
    naive = (now.astimezone(timezone.utc) - timedelta(minutes=20)).replace(tzinfo=None)
    assert report_fetch.decide_poll(now, games, naive).poll


# ====================================================================== the job, end to end

EASTERN = report_fetch.EASTERN


class Wire:
    """The NBA's static host, as a mock: a file per slot URL, and a record of every request."""

    def __init__(self) -> None:
        self.files: dict[str, httpx.Response | Callable[[], httpx.Response]] = {}
        self.default: Callable[[httpx.Request], httpx.Response] = lambda request: httpx.Response(
            404
        )
        self.requests: list[str] = []
        self.now = datetime(2025, 10, 22, 21, 40, tzinfo=timezone.utc)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        self.requests.append(name)
        if name in self.files:
            entry = self.files[name]
            return entry() if callable(entry) else entry
        return self.default(request)

    def serve(self, slot_label: str, pdf: bytes) -> None:
        self.files[f"Injury-Report_{slot_label}.pdf"] = httpx.Response(
            200, content=pdf, headers={"content-type": "application/pdf"}
        )


@pytest.fixture()
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    site = Wire()

    def factory(**kwargs: Any) -> PoliteClient:
        return PoliteClient(
            transport=httpx.MockTransport(site),
            min_interval=0.0,
            sleep=lambda seconds: None,
            jitter=lambda: 0.0,
            wall_clock=lambda: site.now,
            **kwargs,
        )

    monkeypatch.setattr(jobs, "PoliteClient", factory)
    return site


def report_pdf_for(slot_title: str = "10/22/25 05:15 PM") -> bytes:
    first = PageSpec(rows=[list(r) for r in PAGE_ONE], title=f"Injury Report: {slot_title}")
    second = PageSpec(
        rows=[list(r) for r in PAGE_TWO], dx=2.0, title=f"Injury Report: {slot_title}"
    )
    return build_report([first, second])


def counts(engine: Engine) -> dict[str, int]:
    with Session(engine) as session:
        return {
            "snapshots": session.execute(
                select(func.count()).select_from(NbaIntelSnapshot)
            ).scalar()
            or 0,
            "statuses": session.execute(select(func.count()).select_from(NbaIntelStatus)).scalar()
            or 0,
            "raw": session.execute(select(func.count()).select_from(NbaIntelRawFetch)).scalar()
            or 0,
        }


def source_state(engine: Engine) -> NbaIntelSourceState:
    with Session(engine) as session:
        row = session.get(NbaIntelSourceState, store.SOURCE_INJURY_REPORT)
        assert row is not None
        session.expunge(row)
        return row


def test_the_job_finds_the_newest_report_stores_it_and_remembers_the_slot(
    job_db: Engine, wire: Wire, tmp_path: Path
) -> None:
    wire.serve("2025-10-22_05_15PM", report_pdf_for())
    result = jobs.run_injuries(now=wire.now)
    assert result["status"] == "ok", result
    # newest slot first (5:30 PM), then one step back (5:15 PM): the file is there
    assert wire.requests == [
        "Injury-Report_2025-10-22_05_30PM.pdf",
        "Injury-Report_2025-10-22_05_15PM.pdf",
    ]
    assert "6 entries (ok)" in result["detail"]
    assert counts(job_db) == {"snapshots": 1, "statuses": 6, "raw": 1}

    with Session(job_db) as session:
        snapshot = session.execute(select(NbaIntelSnapshot)).scalars().one()
        assert snapshot.slot_at_utc == datetime(2025, 10, 22, 21, 15)  # 5:15 PM EDT
        assert snapshot.url is not None and snapshot.url.endswith("2025-10-22_05_15PM.pdf")
        assert (
            snapshot.parse_status == "ok"
            and snapshot.row_count == 6
            and len(snapshot.sha256 or "") == 64
        )
        raw = session.execute(select(NbaIntelRawFetch)).scalars().one()
        assert raw.sha256 == snapshot.sha256 and raw.http_status == 200
        assert raw.path is not None and Path(raw.path).is_file()
        assert Path(raw.path).is_relative_to(tmp_path / "data" / "raw")
        cursor = store.read_cursor(session, store.JOB_INJURIES)
    assert cursor["newestSlot"] == "2025-10-22T21:15:00" and "lastProbeAt" in cursor
    state = source_state(job_db)
    assert (
        state.state == "ok"
        and state.last_success_at is not None
        and state.consecutive_failures == 0
    )
    with Session(job_db) as session:
        confirmation = store.injury_parser_confirmation(session)
    assert confirmation.count == 1 and confirmation.confirmed


def test_the_next_tick_asks_only_for_slots_it_does_not_have(job_db: Engine, wire: Wire) -> None:
    wire.serve("2025-10-22_05_15PM", report_pdf_for())
    jobs.run_injuries(now=wire.now)
    wire.requests.clear()
    wire.now += timedelta(minutes=15)  # 5:55 PM: floor slot 5:45, newest held 5:15
    result = jobs.run_injuries(now=wire.now)
    assert result["status"] == "ok" and "none published" in result["detail"]
    assert wire.requests == [
        "Injury-Report_2025-10-22_05_45PM.pdf",
        "Injury-Report_2025-10-22_05_30PM.pdf",
    ]  # and not 5:15 again
    assert counts(job_db)["snapshots"] == 1


def test_an_unchanged_file_is_not_stored_again_but_its_slot_is_remembered(
    job_db: Engine, wire: Wire
) -> None:
    pdf = report_pdf_for()
    wire.serve("2025-10-22_05_15PM", pdf)
    jobs.run_injuries(now=wire.now)
    wire.now += timedelta(minutes=30)  # 6:10 PM: floor slot 6:00
    wire.serve("2025-10-22_06_00PM", pdf)  # identical bytes under a newer slot
    wire.requests.clear()
    result = jobs.run_injuries(now=wire.now)
    assert "unchanged" in result["detail"]
    assert wire.requests == ["Injury-Report_2025-10-22_06_00PM.pdf"]  # newest slot answered at once
    assert counts(job_db) == {"snapshots": 1, "statuses": 6, "raw": 2}
    with Session(job_db) as session:
        assert store.read_cursor(session, store.JOB_INJURIES)["newestSlot"] == "2025-10-22T22:00:00"
    # and the slot just seen is not asked for again
    wire.requests.clear()
    wire.now += timedelta(minutes=15)  # 6:25 PM: floor 6:15, newest held 6:00
    jobs.run_injuries(now=wire.now)
    assert wire.requests == ["Injury-Report_2025-10-22_06_15PM.pdf"]


def test_a_second_run_inside_the_interval_does_nothing_and_asks_nothing(
    job_db: Engine, wire: Wire
) -> None:
    wire.serve("2025-10-22_05_15PM", report_pdf_for())
    jobs.run_injuries(now=wire.now)
    wire.requests.clear()
    result = jobs.run_injuries(now=wire.now + timedelta(minutes=3))
    assert result["status"] == "skipped" and "next in about" in result["detail"]
    assert wire.requests == []
    forced = jobs.run_injuries(now=wire.now + timedelta(minutes=3), force=True)
    assert forced["status"] == "ok" and wire.requests  # force skips the interval


def test_it_is_idle_when_no_game_is_within_36_hours(job_db: Engine, wire: Wire) -> None:
    wire.now = datetime(2025, 10, 18, 15, 0, tzinfo=timezone.utc)  # games are on the 22nd
    result = jobs.run_injuries(now=wire.now)
    assert result["status"] == "skipped" and "36 hours" in result["detail"]
    assert wire.requests == [] and counts(job_db)["snapshots"] == 0


def test_outside_a_reporting_window_it_polls_hourly(job_db: Engine, wire: Wire) -> None:
    wire.now = datetime(2025, 10, 21, 14, 0, tzinfo=timezone.utc)  # 10 AM ET the day before
    assert jobs.run_injuries(now=wire.now)["status"] == "ok"
    wire.requests.clear()
    skipped = jobs.run_injuries(now=wire.now + timedelta(minutes=20))
    assert skipped["status"] == "skipped" and wire.requests == []
    assert jobs.run_injuries(now=wire.now + timedelta(minutes=61))["status"] == "ok"


def test_nothing_published_yet_is_the_state_no_report_yet(job_db: Engine, wire: Wire) -> None:
    result = jobs.run_injuries(now=wire.now)
    assert result["status"] == "ok" and "none published" in result["detail"]
    assert len(wire.requests) == report_fetch.MAX_STEPS
    state = source_state(job_db)
    assert state.state == "noReportYet"
    assert "No injury report" in store.source_detail(state)["reason"]
    assert counts(job_db)["snapshots"] == 0


def test_an_unreadable_report_is_recorded_as_unreadable_and_never_counts_as_confirmed(
    job_db: Engine, wire: Wire
) -> None:
    renamed = ["Game Date", "Game Time", "Matchup", "Team", "Player Name", "Status", "Reason"]
    wire.serve("2025-10-22_05_15PM", standard_report(header=renamed))
    result = jobs.run_injuries(now=wire.now)
    assert result["status"] == "ok" and "unreadable" in result["detail"]
    assert counts(job_db) == {"snapshots": 1, "statuses": 0, "raw": 1}
    state = source_state(job_db)
    assert state.state == "unreadable" and "header" in (state.last_error or "")
    assert state.consecutive_failures == 1
    with Session(job_db) as session:
        assert store.injury_parser_confirmation(session).count == 0
        assert (
            session.execute(select(NbaIntelSnapshot)).scalars().one().parse_status
            == "headerMismatch"
        )


def test_a_partial_parse_is_stored_but_does_not_confirm_the_parser(
    job_db: Engine, wire: Wire
) -> None:
    rows = [list(r) for r in PAGE_ONE]
    rows[5][5] = "Maybe"
    wire.serve("2025-10-22_05_15PM", build_report([PageSpec(rows=rows), PageSpec(rows=[])]))
    result = jobs.run_injuries(now=wire.now)
    assert "(partial)" in result["detail"]
    with Session(job_db) as session:
        assert store.injury_parser_confirmation(session).count == 0
    assert source_state(job_db).state == "ok"


def test_confirmations_accumulate_across_reports(job_db: Engine, wire: Wire) -> None:
    wire.serve("2025-10-22_05_15PM", report_pdf_for())
    jobs.run_injuries(now=wire.now)
    wire.now += timedelta(minutes=30)
    changed = [list(r) for r in PAGE_ONE]
    changed[0][5] = "Questionable"
    wire.serve(
        "2025-10-22_06_00PM",
        build_report(
            [PageSpec(rows=changed, title="Injury Report: 10/22/25 06:00 PM"), PageSpec(rows=[])]
        ),
    )
    jobs.run_injuries(now=wire.now)
    with Session(job_db) as session:
        confirmation = store.injury_parser_confirmation(session)
        assert confirmation.count == 2
        assert confirmation.first_at is not None and confirmation.last_at is not None
        assert confirmation.first_at <= confirmation.last_at
    assert counts(job_db)["snapshots"] == 2


def test_a_403_is_tolerated_as_not_there_until_it_looks_like_a_block(
    job_db: Engine, wire: Wire
) -> None:
    wire.default = lambda request: httpx.Response(403)
    first = jobs.run_injuries(now=wire.now)
    assert first["status"] == "ok"
    assert len(wire.requests) == report_fetch.MAX_STEPS  # a 403 did not stop the walk back
    assert source_state(job_db).state != "blocked"
    wire.now += timedelta(minutes=15)
    second = jobs.run_injuries(now=wire.now)
    assert second["status"] == "skipped" and "403" in second["detail"]
    state = source_state(job_db)
    assert state.state == "blocked" and state.paused_until is not None
    assert state.paused_until == (wire.now + timedelta(hours=6)).replace(tzinfo=None)

    # the pause is honoured on the next tick, with nothing sent, even across a "restart"
    wire.requests.clear()
    wire.now += timedelta(minutes=15)
    third = jobs.run_injuries(now=wire.now)
    assert wire.requests == []
    assert third["status"] == "skipped" and "blocked" in third["detail"]
    assert source_state(job_db).state == "blocked"


def test_a_403_streak_is_reset_by_a_success(job_db: Engine, wire: Wire) -> None:
    wire.default = lambda request: httpx.Response(403)
    jobs.run_injuries(now=wire.now)
    wire.now += timedelta(minutes=15)
    wire.serve("2025-10-22_05_45PM", report_pdf_for())
    assert jobs.run_injuries(now=wire.now)["status"] == "ok"
    with Session(job_db) as session:
        assert store.read_cursor(session, store.JOB_INJURIES)["forbiddenStreak"] == 0


def test_repeated_429s_open_the_breaker_and_the_pause_is_persisted(
    job_db: Engine, wire: Wire
) -> None:
    wire.default = lambda request: httpx.Response(429)
    result = jobs.run_injuries(now=wire.now)
    assert result["status"] == "skipped" and "blocked" in result["detail"]
    assert len(wire.requests) == 3  # three attempts, then the breaker opened
    state = source_state(job_db)
    assert state.state == "blocked" and state.paused_until == (
        wire.now + timedelta(hours=6)
    ).replace(tzinfo=None)
    assert "429" in (state.last_error or "")

    wire.requests.clear()
    wire.now += timedelta(minutes=20)
    jobs.run_injuries(now=wire.now)
    assert wire.requests == []  # the persisted pause was restored into the new client


def test_a_401_blocks_immediately(job_db: Engine, wire: Wire) -> None:
    wire.default = lambda request: httpx.Response(401)
    jobs.run_injuries(now=wire.now)
    assert len(wire.requests) == 1 and source_state(job_db).state == "blocked"


def test_a_transport_failure_is_a_job_error_and_a_source_error(job_db: Engine, wire: Wire) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    wire.default = down
    result = jobs.run_injuries(now=wire.now)
    assert result["status"] == "error"
    assert source_state(job_db).state == "error"


def test_an_html_error_page_with_status_200_is_not_a_report(job_db: Engine, wire: Wire) -> None:
    wire.default = lambda request: httpx.Response(200, content=b"<html>Service unavailable</html>")
    result = jobs.run_injuries(now=wire.now)
    assert result["status"] == "error" and "not a PDF" in result["detail"]
    assert counts(job_db)["snapshots"] == 0


# ------------------------------------------------------------------ the job: refusals


def test_the_job_refuses_the_synthetic_demo_store_judged_by_its_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wire: Wire
) -> None:
    from nbastats.seed import seed_database

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'demo.db'}")
    monkeypatch.setenv("HARDWOOD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("HARDWOOD_DEMO_MODE", raising=False)  # the environment says nothing
    config.reset_settings_cache()
    db_module.dispose_engine()
    try:
        engine = db_module.get_engine()
        init_db(engine)
        with Session(engine) as session:
            seed_database(
                session,
                seasons=["2025-26"],
                games_per_team=2,
                players_per_team=2,
                include_playoffs=False,
            )
            session.commit()
            assert store.store_is_synthetic(session)
        result = jobs.run_injuries(now=wire.now, force=True)
        assert result["status"] == "skipped" and result["detail"] == store.SYNTHETIC_REASON
        assert wire.requests == []
        with Session(engine) as session:
            assert session.execute(select(func.count()).select_from(NbaIntelSnapshot)).scalar() == 0
            assert session.execute(select(func.count()).select_from(NbaIntelStatus)).scalar() == 0
            state = session.get(NbaIntelSourceState, store.SOURCE_INJURY_REPORT)
            assert state is not None and state.state == "disabled"
            assert store.source_detail(state)["reason"] == store.SYNTHETIC_REASON
    finally:
        db_module.dispose_engine()
        config.reset_settings_cache()


def test_the_switch_turns_the_job_off(
    job_db: Engine, wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    for value in ("off", "0", "false", "no", "nonsense"):
        monkeypatch.setenv("HARDWOOD_NBA_INJURIES", value)
        assert jobs.run_injuries(now=wire.now)["status"] == "skipped"
    assert wire.requests == []
    monkeypatch.setenv("HARDWOOD_NBA_INJURIES", "on")
    assert jobs.run_injuries(now=wire.now)["status"] == "ok"


def test_without_pypdf_the_source_is_not_configured_and_says_how_to_fix_it(
    job_db: Engine, wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report_pdf, "pypdf_available", lambda: False)
    result = jobs.run_injuries(now=wire.now)
    assert result["status"] == "skipped" and "injuries" in result["detail"]
    assert wire.requests == []
    state = source_state(job_db)
    assert state.state == "notConfigured" and "injuries" in store.source_detail(state)["reason"]


def test_a_bad_url_template_is_a_job_error(
    job_db: Engine, wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(report_fetch.URL_TEMPLATE_ENV, "http://insecure.example.org/{date}")
    result = jobs.run_injuries(now=wire.now)
    assert result["status"] == "error" and wire.requests == []


def test_the_worker_can_find_both_jobs_by_name() -> None:
    assert set(jobs.JOBS) == {"nba.injuries", "nba.news"}
    assert jobs.JOBS["nba.injuries"] is jobs.run_injuries and jobs.JOBS["nba.news"] is jobs.run_news
    import inspect

    for fn in (jobs.run_injuries, jobs.run_news):
        names = set(inspect.signature(fn).parameters)
        assert {"now", "shutdown", "force", "data_dir"} <= names


def test_old_raw_reports_are_pruned_once_a_day(job_db: Engine, wire: Wire, tmp_path: Path) -> None:
    raw_dir = tmp_path / "data" / "raw" / "nba_injury" / "ab"
    raw_dir.mkdir(parents=True)
    old = raw_dir / ("0" * 64 + ".pdf")
    old.write_bytes(b"%PDF-old")
    stale = (wire.now - timedelta(days=45)).timestamp()
    import os

    os.utime(old, (stale, stale))
    fresh = raw_dir / ("1" * 64 + ".pdf")
    fresh.write_bytes(b"%PDF-fresh")
    jobs.run_injuries(now=wire.now, data_dir=tmp_path / "data")
    assert not old.exists() and fresh.exists()


# ------------------------------------------------------------- real reports, if present

LOCAL = Path(__file__).resolve().parent / "local" / "nba_injury"


def test_real_reports_recorded_on_the_mac_parse() -> None:
    """Runs over ``tests/local/nba_injury/*.pdf`` (git-ignored) and skips when there are none.

    This is the check the design asks for before the parser is called confirmed: at least two real
    reports, each parsing ``ok`` or at worst ``partial`` with entries.
    """
    reports = sorted(LOCAL.glob("*.pdf")) if LOCAL.is_dir() else []
    if not reports:
        pytest.skip("no real injury report PDFs in backend/tests/local/nba_injury/")
    for path in reports:
        parsed = parse_report(path.read_bytes())
        assert parsed.status in ("ok", "partial", "empty"), (path.name, parsed.errors)
        if parsed.status != "empty":
            assert parsed.rows or parsed.teams, path.name
        for row in parsed.rows:
            assert row.status in avail.STATUSES and "," in row.player_name_raw, path.name


# ============================================================================ the probe script


def load_probe() -> Any:
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "probe_nba_injury_report.py"
    spec = importlib.util.spec_from_file_location("probe_nba_injury_report", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_probe_dry_run_lists_urls_and_requests_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    probe = load_probe()
    assert probe.main(["--dry-run", "--back", "3"]) == 0
    lines = [
        ln.strip() for ln in capsys.readouterr().out.splitlines() if ln.strip().startswith("https")
    ]
    assert len(lines) == 3 and all("/referee/injury/Injury-Report_" in ln for ln in lines)


def test_the_probe_reads_a_local_file_with_no_network_and_summarises_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(standard_report())
    probe = load_probe()
    assert probe.main(["--file", str(pdf), "--parse", "--dump-runs", "6"]) == 0
    out = capsys.readouterr().out
    assert "No network was used" in out and "parse status : ok" in out
    assert "entries      : 6" in out and "Bravo Town Bees: notYetSubmitted" in out
    assert "page 1:" in out and "(x, y, size, text)" in out
    assert "Sample, Alex" in out  # a real report's names go to the person's terminal, not git


def test_the_probe_exits_1_for_a_report_it_cannot_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    renamed = ["Game Date", "Game Time", "Matchup", "Team", "Player Name", "Status", "Reason"]
    pdf = tmp_path / "bad.pdf"
    pdf.write_bytes(standard_report(header=renamed))
    assert load_probe().main(["--file", str(pdf), "--parse"]) == 1
    assert "headerMismatch" in capsys.readouterr().out
    assert load_probe().main(["--file", str(tmp_path / "missing.pdf")]) == 1


def test_the_probe_fetches_records_and_parses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HARDWOOD_DATA_DIR", str(tmp_path / "data"))
    probe = load_probe()
    asked: list[str] = []
    pdf = standard_report()

    def fake_fetch(client: Any, url: str) -> report_fetch.SlotFetch:
        asked.append(url)
        now = datetime.now(timezone.utc)
        if len(asked) < 3:
            return report_fetch.SlotFetch("notFound", url, 404, now, reason="not published")
        return report_fetch.SlotFetch("ok", url, 200, now, body=pdf, sha256="0" * 64)

    monkeypatch.setattr(probe.report_fetch, "fetch_slot", fake_fetch)
    assert probe.main(["--parse"]) == 0
    out = capsys.readouterr().out
    assert len(asked) == 3 and "notFound" in out and "recorded to" in out
    recorded = sorted((tmp_path / "data" / "recordings" / "nba_injury").glob("*.pdf"))
    assert len(recorded) == 1 and recorded[0].read_bytes() == pdf
    assert recorded[0].with_name(recorded[0].name + ".json").is_file()
    assert "parse status : ok" in out


def test_the_probe_can_skip_recording_and_reports_when_nothing_was_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HARDWOOD_DATA_DIR", str(tmp_path / "data"))
    probe = load_probe()

    def nothing(client: Any, url: str) -> report_fetch.SlotFetch:
        return report_fetch.SlotFetch("notFound", url, 404, datetime.now(timezone.utc))

    monkeypatch.setattr(probe.report_fetch, "fetch_slot", nothing)
    assert probe.main(["--back", "2"]) == 1
    assert "No report was found" in capsys.readouterr().out
    assert not (tmp_path / "data").exists()

    def found(client: Any, url: str) -> report_fetch.SlotFetch:
        return report_fetch.SlotFetch(
            "ok", url, 200, datetime.now(timezone.utc), body=standard_report(), sha256="0" * 64
        )

    monkeypatch.setattr(probe.report_fetch, "fetch_slot", found)
    assert probe.main(["--no-record", "--slot", "2025-10-22 17:30"]) == 0
    assert not (tmp_path / "data").exists()


def test_the_probe_stops_at_a_blocked_host_and_rejects_bad_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = load_probe()
    calls: list[str] = []

    def blocked(client: Any, url: str) -> report_fetch.SlotFetch:
        calls.append(url)
        return report_fetch.SlotFetch("blocked", url, 429, datetime.now(timezone.utc), reason="x")

    monkeypatch.setattr(probe.report_fetch, "fetch_slot", blocked)
    assert probe.main(["--back", "5"]) == 1 and len(calls) == 1
    for bad in (["--back", "0"], ["--slot", "yesterday-ish"], ["--dump-runs", "-1"]):
        with pytest.raises(SystemExit) as raised:
            probe.main(bad)
        assert raised.value.code == 2


def test_the_probe_says_so_when_pypdf_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    probe = load_probe()
    monkeypatch.setattr(probe.report_pdf, "pypdf_available", lambda: False)
    assert probe.main(["--parse"]) == 2
    assert "injuries" in capsys.readouterr().err


# ================================================================ where text is: the binding


class FakeBox:
    left, bottom, width, height = 0.0, 0.0, 792.0, 612.0


class FakePage:
    rotation = 0
    mediabox = FakeBox()


IDENTITY = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]


def test_a_fragments_position_comes_from_its_operator_not_from_the_visitors_buffer() -> None:
    """Most pypdf releases report (0, 0) to ``visitor_text`` for every fragment after the first.
    The position must therefore be taken from the matrices at the operator itself."""
    runs = report_pdf._PageRuns(FakePage())  # noqa: SLF001
    runs.before(b"Tj", [], IDENTITY, [1.0, 0.0, 0.0, 1.0, 345.0, 500.0])
    runs.visit("Sample, Alex", IDENTITY, IDENTITY, None, 8.0)  # the visitor says (0, 0)
    runs.after(b"cm", [], IDENTITY, IDENTITY)
    assert [(r.text, r.x, r.y) for r in runs.runs] == [("Sample, Alex", 345.0, 500.0)]
    assert runs.unplaced == 0


def test_the_graphics_matrix_at_the_operator_is_applied() -> None:
    runs = report_pdf._PageRuns(FakePage())  # noqa: SLF001
    shifted = [1.0, 0.0, 0.0, 1.0, 10.0, 20.0]
    runs.before(b"TJ", [], shifted, [1.0, 0.0, 0.0, 1.0, 40.0, 500.0])
    runs.visit("Out", IDENTITY, IDENTITY, None, 8.0)
    assert (runs.runs[0].x, runs.runs[0].y) == (50.0, 520.0)
    flipped = [1.0, 0.0, 0.0, -1.0, 0.0, 612.0]  # a producer that flips the page
    runs.before(b"Tj", [], flipped, [1.0, 0.0, 0.0, 1.0, 40.0, 100.0])
    runs.visit("Rest", IDENTITY, IDENTITY, None, 8.0)
    assert (runs.runs[1].x, runs.runs[1].y) == (40.0, 512.0)


def test_a_position_is_used_once_and_never_by_the_next_fragment() -> None:
    runs = report_pdf._PageRuns(FakePage())  # noqa: SLF001
    runs.before(b"Tj", [], IDENTITY, [1.0, 0.0, 0.0, 1.0, 100.0, 200.0])
    runs.visit("first", IDENTITY, IDENTITY, None, 8.0)
    runs.visit("second", IDENTITY, [1.0, 0.0, 0.0, 1.0, 5.0, 6.0], None, 8.0)  # no operator for it
    assert [(r.text, r.x, r.y) for r in runs.runs] == [
        ("first", 100.0, 200.0),
        ("second", 5.0, 6.0),
    ]


def test_a_show_operator_with_no_text_leaves_nothing_pending() -> None:
    runs = report_pdf._PageRuns(FakePage())  # noqa: SLF001
    runs.before(b"Tj", [], IDENTITY, [1.0, 0.0, 0.0, 1.0, 100.0, 200.0])  # shows nothing
    runs.after(b"cm", [], IDENTITY, IDENTITY)  # the flush came and went with no text
    runs.visit("later", IDENTITY, [1.0, 0.0, 0.0, 1.0, 7.0, 8.0], None, 8.0)
    assert (runs.runs[0].x, runs.runs[0].y) == (7.0, 8.0)  # not (100, 200)


def test_an_operator_in_between_breaks_the_pairing() -> None:
    """Text that was not isolated is not given the coordinates of a different fragment."""
    runs = report_pdf._PageRuns(FakePage())  # noqa: SLF001
    runs.before(b"Tj", [], IDENTITY, [1.0, 0.0, 0.0, 1.0, 100.0, 200.0])
    runs.before(b"Td", [], IDENTITY, IDENTITY)  # something other than the injected cm
    runs.visit("merged", IDENTITY, [1.0, 0.0, 0.0, 1.0, 30.0, 40.0], None, 8.0)
    assert (runs.runs[0].x, runs.runs[0].y) == (30.0, 40.0)


def test_a_fragment_that_cannot_be_positioned_is_counted_not_guessed() -> None:
    runs = report_pdf._PageRuns(FakePage())  # noqa: SLF001
    runs.visit("nowhere", IDENTITY, IDENTITY, None, 8.0)  # no operator, and (0, 0)
    runs.visit("also bad", IDENTITY, ["x"], None, 8.0)
    runs.visit("   ", IDENTITY, IDENTITY, None, 8.0)  # whitespace is not text
    assert runs.runs == [] and runs.unplaced == 2


def test_a_page_with_unpositioned_text_is_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    """If pypdf cannot place a fragment the page is refused, which parse_report reports as
    ``headerMismatch`` with the reason, rather than parsing a table with a missing cell."""

    def broken_visit(self: Any, text: Any, cm: Any, tm: Any, font: Any, size: Any) -> None:
        self.unplaced += 1

    monkeypatch.setattr(report_pdf._PageRuns, "visit", broken_visit)  # noqa: SLF001
    report = parse_report(standard_report())
    assert report.status == "headerMismatch" and "could not be positioned" in report.errors[0]


def test_the_extraction_survives_a_visitor_that_reports_degenerate_positions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-6.18 behaviour, simulated on any pypdf: every ``visitor_text`` call is given the
    identity matrices (a position of (0, 0)). The parse must be unaffected."""
    from pypdf import PageObject

    real = PageObject.extract_text

    def degenerate(self: Any, *args: Any, **kwargs: Any) -> str:
        visitor = kwargs.get("visitor_text")
        if visitor is not None:
            kwargs["visitor_text"] = lambda text, cm, tm, font, size: visitor(
                text, IDENTITY, IDENTITY, font, size
            )
        return real(self, *args, **kwargs)

    monkeypatch.setattr(PageObject, "extract_text", degenerate)
    report = parse_report(standard_report())
    assert report.status == "ok" and len(report.rows) == 6
    assert report.rows == parse_report(standard_report(single_block=True)).rows


# ================================================================== recovery and re-reading


def test_a_blocked_source_recovers_when_the_host_answers_normally_again(
    job_db: Engine, wire: Wire
) -> None:
    with Session(job_db) as session:  # a game the next day, so the job is still in business
        session.add(
            Game(
                game_id="T-0006",
                game_date=date(2025, 10, 23),
                season="2025-26",
                season_type="Regular Season",
                home_team_id=9102,
                away_team_id=9103,
                status="scheduled",
                data_source="nba_api",
            )
        )
        session.commit()
    wire.serve("2025-10-22_05_15PM", report_pdf_for())
    jobs.run_injuries(now=wire.now)
    delivered = source_state(job_db).last_success_at
    assert delivered is not None

    wire.default = lambda request: httpx.Response(429)
    wire.now += timedelta(minutes=15)
    jobs.run_injuries(now=wire.now)
    blocked = source_state(job_db)
    assert blocked.state == "blocked" and blocked.consecutive_failures == 1
    assert blocked.paused_until is not None

    wire.default = lambda request: httpx.Response(404)  # not published: the normal answer
    wire.now += timedelta(hours=6, minutes=5)  # the pause is over
    wire.requests.clear()
    result = jobs.run_injuries(now=wire.now)
    assert result["status"] == "ok" and wire.requests  # it asked again
    recovered = source_state(job_db)
    assert recovered.state == "ok" and recovered.paused_until is None
    assert recovered.consecutive_failures == 0 and recovered.last_error is None
    assert recovered.last_success_at == delivered  # nothing new was delivered


def test_an_unreadable_file_is_read_again_so_a_parser_fix_takes_effect(
    job_db: Engine, wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    renamed = ["Game Date", "Game Time", "Matchup", "Team", "Player Name", "Status", "Reason"]
    unreadable = standard_report(header=renamed)
    wire.serve("2025-10-22_05_15PM", unreadable)
    jobs.run_injuries(now=wire.now)
    assert counts(job_db)["snapshots"] == 1 and source_state(job_db).state == "unreadable"

    # Later the same bytes arrive under a newer slot, and by then the parser has been fixed.
    fixed = parse_report(standard_report())
    monkeypatch.setattr(report_pdf, "parse_report", lambda body: fixed)
    wire.now += timedelta(minutes=30)
    wire.serve("2025-10-22_06_00PM", unreadable)
    result = jobs.run_injuries(now=wire.now)
    assert "unchanged" not in result["detail"] and "6 entries (ok)" in result["detail"]
    assert counts(job_db)["snapshots"] == 2 and counts(job_db)["statuses"] == 6
    assert source_state(job_db).state == "ok"
    with Session(job_db) as session:
        assert store.injury_parser_confirmation(session).count == 1
