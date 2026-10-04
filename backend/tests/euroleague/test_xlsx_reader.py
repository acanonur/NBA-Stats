"""The stdlib ``.xlsx`` reader: values, dates, defined names, header tables, and its refusals.

Every workbook here is written in memory by ``make_xlsx`` (zipfile and strings, no openpyxl),
so the reader is tested against exactly the shapes it claims to support and against hostile
files built to break each of its limits.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from nbastats.euroleague.importers import xlsx
from nbastats.euroleague.importers.xlsx import (
    Column,
    UnreadableWorkbook,
    UnsafeWorkbook,
    Workbook,
    cell_date,
    cell_int,
    cell_number,
    cell_text,
    col_to_index,
    excel_serial_to_datetime,
    index_to_col,
    is_date_format,
)

# --------------------------------------------------------------------------- values


def test_strings_numbers_booleans_and_blanks(xl: SimpleNamespace) -> None:
    data = xl.make([("S", [["text", 1, 2.5, True, None, "  ", ""], [False, -3, 1e-06]])])
    sheet = Workbook.open(data).sheet("S")
    assert sheet.cells[1] == {1: "text", 2: 1, 3: 2.5, 4: True}
    assert isinstance(sheet.cells[1][2], int) and isinstance(sheet.cells[1][3], float)
    assert sheet.cells[2] == {1: False, 2: -3, 3: 1e-06}
    assert sheet.max_row == 2 and sheet.max_col == 4  # blanks and whitespace are not cells


def test_inline_strings_read_like_shared_ones(xl: SimpleNamespace) -> None:
    data = xl.make([("S", [["alpha", "beta"]])], inline_strings=True)
    assert Workbook.open(data).sheet("S").cells[1] == {1: "alpha", 2: "beta"}


def test_rich_text_joins_runs_and_ignores_the_phonetic_run(xl: SimpleNamespace) -> None:
    data = xl.make([("S", [[xl.Rich(("Hel", "lo"), phonetic="PHONETIC")]])])
    assert Workbook.open(data).sheet("S").value(1, 1) == "Hello"


def test_escaped_control_characters_are_decoded(xl: SimpleNamespace) -> None:
    data = xl.make([("S", [["a_x000D_b", "keep_x005F_this", "no_xZZZZ_escape"]])])
    row = Workbook.open(data).sheet("S").row(1)
    assert row == {1: "a\rb", 2: "keep_this", 3: "no_xZZZZ_escape"}


def test_a_formula_reads_as_its_cached_value_and_never_runs(xl: SimpleNamespace) -> None:
    data = xl.make(
        [
            (
                "S",
                [
                    [
                        xl.Formula(41.5, "1+1"),
                        xl.Formula("label", 'IF(1,"label")'),
                        xl.Formula(None, "A1"),
                    ]
                ],
            )
        ]
    )
    sheet = Workbook.open(data).sheet("S")
    assert sheet.value(1, 1) == 41.5  # not 2: the formula text is never evaluated
    assert sheet.value(1, 2) == "label"
    assert sheet.value(1, 3) is None  # never calculated: no value, not zero


def test_an_error_cell_is_none_and_counted(xl: SimpleNamespace) -> None:
    data = xl.make([("S", [[xl.Err("#DIV/0!"), 1, xl.Err("#N/A")]])])
    sheet = Workbook.open(data).sheet("S")
    assert sheet.cells[1] == {2: 1}
    assert sheet.error_cells == 2


def test_cells_without_references_take_their_position(xl: SimpleNamespace) -> None:
    sheet_xml = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row><c><v>1</v></c><c><v>2</v></c></row><row r="5"><c><v>3</v></c><c r="D5"><v>4</v></c>'
        "<c><v>5</v></c></row></sheetData></worksheet>"
    )
    data = xl.make([("S", [["x"]])])
    patched = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(patched, "w") as target:
        for item in source.infolist():
            body = sheet_xml.encode() if item.filename.endswith("sheet1.xml") else source.read(item)
            target.writestr(item.filename, body)
    sheet = Workbook.open(patched.getvalue()).sheet("S")
    assert sheet.cells == {1: {1: 1, 2: 2}, 5: {1: 3, 4: 4, 5: 5}}


def test_a_strict_namespace_workbook_is_readable() -> None:
    main = "http://purl.oclc.org/ooxml/spreadsheetml/main"
    rel = "http://purl.oclc.org/ooxml/officeDocument/relationships"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets><sheet name="S" sheetId="1" '
            f'r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="/xl/worksheets/a.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/worksheets/a.xml",
            f'<worksheet xmlns="{main}"><sheetData><row r="1"><c r="A1" t="inlineStr"><is>'
            "<t>strict</t></is></c></row></sheetData></worksheet>",
        )
    assert Workbook.open(buffer.getvalue()).sheet("S").value(1, 1) == "strict"


def test_sheet_order_names_and_lookup(xl: SimpleNamespace) -> None:
    workbook = Workbook.open(xl.make([("B", [[1]]), ("A b", [[2]]), ("It's", [[3]])]))
    assert workbook.sheet_names == ("B", "A b", "It's")
    assert workbook.has_sheet("A b") and not workbook.has_sheet("nope")
    with pytest.raises(KeyError):
        workbook.sheet("nope")
    assert workbook.sheet("B") is workbook.sheet("B")  # cached


def test_sha256_is_of_the_file(xl: SimpleNamespace) -> None:
    data = xl.make([("S", [[1]])])
    workbook = Workbook.open(data)
    assert workbook.sha256 == hashlib.sha256(data).hexdigest()
    assert workbook.size == len(data)


def test_a_path_can_be_opened(xl: SimpleNamespace, tmp_path: Path) -> None:
    path = tmp_path / "book.xlsx"
    path.write_bytes(xl.make([("S", [[7]])]))
    assert Workbook.open(path).sheet("S").value(1, 1) == 7
    assert Workbook.open(str(path)).sheet("S").value(1, 1) == 7


# --------------------------------------------------------------------------- dates


def test_serial_to_datetime_known_values() -> None:
    assert excel_serial_to_datetime(1) == datetime(1900, 1, 1)
    assert excel_serial_to_datetime(59) == datetime(1900, 2, 28)
    assert excel_serial_to_datetime(61) == datetime(1900, 3, 1)  # the leap day that never was
    assert excel_serial_to_datetime(46289) == datetime(2026, 9, 24)
    assert excel_serial_to_datetime(45000.5) == datetime(2023, 3, 15, 12, 0)
    assert excel_serial_to_datetime(0, date1904=True) == datetime(1904, 1, 1)
    assert excel_serial_to_datetime(1462, date1904=True) == datetime(1908, 1, 2)


@pytest.mark.parametrize("serial", [60, 0, -1, float("nan"), float("inf")])
def test_impossible_serials_are_refused(serial: float) -> None:
    with pytest.raises(ValueError):
        excel_serial_to_datetime(serial)


def test_date_cells_become_dates_by_their_style(xl: SimpleNamespace) -> None:
    data = xl.make([("S", [[date(2026, 9, 24), datetime(2026, 10, 1, 18, 30), 46289, "46289"]])])
    row = Workbook.open(data).sheet("S").row(1)
    assert row[1] == date(2026, 9, 24)  # a built-in date style
    assert row[2] == datetime(2026, 10, 1, 18, 30)  # a custom style with a time of day
    assert row[3] == 46289 and isinstance(row[3], int)  # an unstyled number stays a number
    assert row[4] == "46289"


def test_the_1904_epoch_is_honoured(xl: SimpleNamespace) -> None:
    data = xl.make([("S", [[date(2026, 9, 24)]])], date1904=True)
    workbook = Workbook.open(data)
    assert workbook.date1904 is True
    assert workbook.sheet("S").value(1, 1) == date(2026, 9, 24)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("General", False),
        ("0.00", False),
        ("0%", False),
        ("#,##0", False),
        ("@", False),
        ("0.00E+00", False),
        ('0.00"m"', False),
        ('#,##0 "days"', False),
        ("d\\ mmm\\ yyyy", True),
        ("yyyy-mm-dd", True),
        ("dd/mm/yy hh:mm", True),
        ("[$-409]d-mmm", True),
        ("[h]:mm:ss", True),
        ("hh:mm", True),
        ("ddd\\ d\\ mmm", True),
        ('"Day" d', True),
        ("[Red]0.00", False),
    ],
)
def test_is_date_format(code: str, expected: bool) -> None:
    assert is_date_format(code) is expected


def test_column_letters_round_trip() -> None:
    assert col_to_index("A") == 1 and col_to_index("Z") == 26 and col_to_index("AA") == 27
    assert col_to_index("aF") == 32 and col_to_index("XFD") == 16384
    for index in (1, 26, 27, 52, 53, 702, 703, 16384):
        assert col_to_index(index_to_col(index)) == index
    with pytest.raises(ValueError):
        col_to_index("A1")
    with pytest.raises(ValueError):
        index_to_col(0)


def test_cell_coercions() -> None:
    assert cell_text("  x ") == "x" and cell_text("  ") is None and cell_text(3) is None
    assert cell_number(3) == 3.0 and cell_number(True) is None and cell_number("3") is None
    assert cell_number(float("nan")) is None
    assert cell_int(14.0) == 14 and cell_int(14.5) is None and cell_int(-2) == -2
    assert cell_int(True) is None
    assert cell_date(datetime(2026, 1, 2, 3)) == date(2026, 1, 2)
    assert cell_date(date(2026, 1, 2)) == date(2026, 1, 2) and cell_date("2026-01-02") is None


# --------------------------------------------------------------------------- defined names


def test_defined_names_resolve_to_cached_values(xl: SimpleNamespace) -> None:
    data = xl.make(
        [
            ("Settings", {5: {3: 0.3}, 25: {2: "OUT", 3: 0}, 26: {2: "DOUBTFUL", 3: 0.25}}),
            ("R2 Review", {30: {13: 0.09}}),
            ("It's here", {1: {1: "apostrophe"}}),
        ],
        defined_names={
            "Regress": "Settings!$C$5",
            "StatusList": "Settings!$B$25:$B$26",
            "StatusProb": "Settings!$C$25:$C$26",
            "KTeam_2": "'R2 Review'!$M$30",
            "Quoted": "'It''s here'!$A$1",
            "Elsewhere": "Nowhere!$A$1",
            "Formula": "SUM(Settings!A1:A2)",
            "_xlnm._FilterDatabase": "Settings!$A$1:$B$2",
        },
    )
    workbook = Workbook.open(data)
    assert workbook.defined_value("Regress") == 0.3
    assert workbook.defined_values("StatusList") == ["OUT", "DOUBTFUL"]
    assert workbook.defined_values("StatusProb") == [0, 0.25]
    assert workbook.defined_value("KTeam_2") == 0.09
    assert workbook.defined_value("Quoted") == "apostrophe"
    assert workbook.defined_value("StatusList") is None  # a range is not a single cell
    assert workbook.defined_values("missing") == [] and workbook.defined_value("missing") is None
    assert {"Elsewhere", "Formula", "_xlnm._FilterDatabase"}.isdisjoint(workbook.defined_names)
    assert workbook.defined_names["Regress"].sheet == "Settings"


def test_a_sheet_local_name_is_not_global(xl: SimpleNamespace) -> None:
    data = xl.make(
        [("S", [[1]])],
        defined_names={"Global": "S!$A$1"},
        workbook_extra='<definedNames><definedName name="Local" localSheetId="0">S!$A$1'
        "</definedName></definedNames>",
    )
    assert "Local" not in Workbook.open(data).defined_names


# --------------------------------------------------------------------------- tables by header


def _table_workbook(xl: SimpleNamespace) -> Workbook:
    return Workbook.open(
        xl.make(
            [
                (
                    "T",
                    {
                        1: {1: "A title"},
                        3: {1: "Note"},
                        5: {
                            1: "Club",
                            2: " Player ",
                            3: "PTS",
                            4: "Model line",
                            5: "REB",
                            6: "Home",
                            7: "Home",
                        },
                        6: {1: "AAA", 2: "One", 3: 10, 4: "SECRET", 5: 4, 6: "x", 7: "y"},
                        7: {1: "BBB", 2: "Two", 3: 12, 4: "SECRET", 5: 6, 6: "z", 7: "w"},
                        8: {},
                        9: {1: "CCC", 2: "Three", 3: 1},
                        12: {1: "Club", 2: "Other table"},
                    },
                )
            ]
        )
    )


def test_a_table_is_found_by_exact_header_and_only_named_columns_are_read(
    xl: SimpleNamespace,
) -> None:
    sheet = _table_workbook(xl).sheet("T")
    table = sheet.find_table(
        [
            Column("club", "Club"),
            Column("name", "Player"),
            Column("pts", "PTS"),
            Column("reb", "REB"),
        ]
    )
    assert table is not None and table.header_row == 5
    assert table.columns == {"club": 1, "name": 2, "pts": 3, "reb": 5}
    rows = list(table.rows(stop_when_blank="club"))
    assert rows == [
        (6, {"club": "AAA", "name": "One", "pts": 10, "reb": 4}),
        (7, {"club": "BBB", "name": "Two", "pts": 12, "reb": 6}),
    ]  # the unnamed "Model line" column never appears, and the walk stops at the blank row
    assert all("SECRET" not in repr(row) for _, row in rows)


def test_without_a_stop_key_the_walk_skips_empty_rows(xl: SimpleNamespace) -> None:
    sheet = _table_workbook(xl).sheet("T")
    table = sheet.find_table([Column("club", "Club"), Column("pts", "PTS")])
    assert table is not None
    assert [n for n, _ in table.rows()] == [6, 7, 9, 12]
    assert [n for n, _ in table.rows(first_row=7, last_row=9)] == [7, 9]


def test_a_repeated_header_is_found_by_occurrence(xl: SimpleNamespace) -> None:
    sheet = _table_workbook(xl).sheet("T")
    table = sheet.find_table(
        [Column("club", "Club"), Column("first", "Home", 1), Column("second", "Home", 2)]
    )
    assert table is not None
    assert table.columns["first"] == 6 and table.columns["second"] == 7
    assert sheet.find_table([Column("third", "Home", 3)]) is None


def test_a_missing_required_header_means_no_table_and_an_optional_one_is_skipped(
    xl: SimpleNamespace,
) -> None:
    sheet = _table_workbook(xl).sheet("T")
    assert sheet.find_table([Column("club", "Club"), Column("nope", "Not a header")]) is None
    table = sheet.find_table(
        [Column("club", "Club"), Column("nope", "Not a header", required=False)]
    )
    assert table is not None and "nope" not in table.columns


def test_header_matching_is_exact(xl: SimpleNamespace) -> None:
    sheet = _table_workbook(xl).sheet("T")
    assert sheet.find_table([Column("p", "pts")]) is None  # case matters
    assert sheet.find_table([Column("p", "PT")]) is None  # no substring matching
    assert sheet.find_table([Column("p", "Player")]) is not None  # surrounding space is trimmed


def test_the_search_can_start_below_a_row(xl: SimpleNamespace) -> None:
    sheet = _table_workbook(xl).sheet("T")
    second = sheet.find_table([Column("club", "Club"), Column("other", "Other table")], first_row=6)
    assert second is not None and second.header_row == 12


# --------------------------------------------------------------------------- refusals


def _rewrite(data: bytes, **replacements: bytes) -> bytes:
    """The archive with some parts replaced (by name) or added."""
    out = io.BytesIO()
    seen = set()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for item in source.infolist():
            seen.add(item.filename)
            target.writestr(item.filename, replacements.get(item.filename, source.read(item)))
        for name, body in replacements.items():
            if name not in seen:
                target.writestr(name, body)
    return out.getvalue()


def _book(xl: SimpleNamespace) -> bytes:
    return xl.make([("S", [["a", 1]])])


@pytest.mark.parametrize(
    "declaration",
    [
        b"<!DOCTYPE foo>",
        b"<!doctype foo>",
        b"<!ENTITY x 'y'>",
        b'<!DOCTYPE x [<!ENTITY a "b">]>',
        b"<!  DOCTYPE foo>",
    ],
)
@pytest.mark.parametrize(
    "part",
    [
        "xl/worksheets/sheet1.xml",
        "xl/sharedStrings.xml",
        "xl/workbook.xml",
        "xl/styles.xml",
        "xl/drawings/drawing1.xml",
        "docProps/core.xml",
    ],
)
def test_a_doctype_or_entity_in_any_xml_part_is_refused(
    xl: SimpleNamespace, part: str, declaration: bytes
) -> None:
    original = (
        zipfile.ZipFile(io.BytesIO(_book(xl))).read(part)
        if part in zipfile.ZipFile(io.BytesIO(_book(xl))).namelist()
        else b"<root/>"
    )
    body = (
        original.replace(b"?>", b"?>" + declaration, 1)
        if b"?>" in original
        else declaration + original
    )
    with pytest.raises(UnsafeWorkbook, match="DOCTYPE or ENTITY"):
        Workbook.open(_rewrite(_book(xl), **{part: body}))


def test_a_nul_byte_in_xml_is_refused(xl: SimpleNamespace) -> None:
    body = b'<?xml version="1.0"?><worksheet>\x00</worksheet>'
    with pytest.raises(UnsafeWorkbook, match="NUL"):
        Workbook.open(_rewrite(_book(xl), **{"xl/worksheets/sheet1.xml": body}))


def test_utf16_xml_cannot_hide_an_entity_from_the_byte_scan(xl: SimpleNamespace) -> None:
    hidden = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY a "b">]><x/>'
    for body in (
        b"\xff\xfe" + hidden.encode("utf-16-le"),
        b"\xfe\xff" + hidden.encode("utf-16-be"),
    ):
        with pytest.raises(UnsafeWorkbook):
            Workbook.open(_rewrite(_book(xl), **{"xl/worksheets/sheet1.xml": body}))


@pytest.mark.parametrize("encoding", ["UTF-16", "cp037", "utf-32", "shift_jis"])
def test_an_unusual_declared_encoding_is_refused(xl: SimpleNamespace, encoding: str) -> None:
    body = f'<?xml version="1.0" encoding="{encoding}"?><worksheet/>'.encode()
    with pytest.raises(UnsafeWorkbook, match="encoding"):
        Workbook.open(_rewrite(_book(xl), **{"xl/worksheets/sheet1.xml": body}))


@pytest.mark.parametrize("encoding", ["UTF-8", "utf-8", "US-ASCII", "ISO-8859-1"])
def test_the_safe_encodings_are_accepted(xl: SimpleNamespace, encoding: str) -> None:
    body = (
        f'<?xml version="1.0" encoding="{encoding}"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData><row r="1"><c r="A1"><v>5</v></c></row></sheetData></worksheet>'
    ).encode()
    data = _rewrite(_book(xl), **{"xl/worksheets/sheet1.xml": body})
    assert Workbook.open(data).sheet("S").value(1, 1) == 5


def test_a_file_over_the_size_cap_is_refused(
    xl: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data = _book(xl)
    monkeypatch.setattr(xlsx, "MAX_FILE_BYTES", len(data) - 1)
    with pytest.raises(UnsafeWorkbook, match="limit"):
        Workbook.open(data)
    path = tmp_path / "big.xlsx"
    path.write_bytes(data)
    with pytest.raises(UnsafeWorkbook, match="limit"):  # refused from its size, before reading
        Workbook.open(path)
    monkeypatch.setattr(xlsx, "MAX_FILE_BYTES", len(data))
    assert Workbook.open(data).sheet("S").value(1, 2) == 1


def test_the_default_caps_are_the_designs() -> None:
    assert xlsx.MAX_FILE_BYTES == 25 * 1024 * 1024
    assert xlsx.MAX_INFLATED_BYTES == 200 * 1024 * 1024


def test_a_zip_bomb_is_refused_by_its_inflated_size(
    xl: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    bomb = _rewrite(_book(xl), **{"xl/worksheets/padding.xml": b"<x>" + b"a" * 50_000 + b"</x>"})
    monkeypatch.setattr(xlsx, "MAX_INFLATED_BYTES", 10_000)
    with pytest.raises(UnsafeWorkbook, match="inflate"):
        Workbook.open(bomb)


def test_too_many_parts_are_refused(xl: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    many = _rewrite(_book(xl), **{f"xl/media/{i}.bin": b"x" for i in range(5)})
    monkeypatch.setattr(xlsx, "MAX_PARTS", 4)
    with pytest.raises(UnsafeWorkbook, match="parts"):
        Workbook.open(many)


def _mark_encrypted(data: bytes, filename: str) -> bytes:
    """Set the 'encrypted' flag in the central-directory entry of ``filename`` (the standard
    library will not write it, so the bytes are patched)."""
    patched = bytearray(data)
    position = 0
    while True:
        position = patched.find(b"PK\x01\x02", position)
        if position < 0:
            raise AssertionError(f"{filename} not in the central directory")
        name_length = int.from_bytes(patched[position + 28 : position + 30], "little")
        name = bytes(patched[position + 46 : position + 46 + name_length]).decode()
        if name == filename:
            patched[position + 8] |= 0x1
            return bytes(patched)
        position += 46


def test_an_encrypted_entry_is_refused(xl: SimpleNamespace) -> None:
    data = _mark_encrypted(_book(xl), "xl/worksheets/sheet1.xml")
    with pytest.raises(UnsafeWorkbook, match="encrypted"):
        Workbook.open(data)


def test_a_cell_outside_the_grid_is_refused(xl: SimpleNamespace) -> None:
    body = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1"><c r="A2000000"><v>1</v></c></row></sheetData></worksheet>'
    ).encode()
    workbook = Workbook.open(_rewrite(_book(xl), **{"xl/worksheets/sheet1.xml": body}))
    with pytest.raises(UnsafeWorkbook, match="grid"):
        workbook.sheet("S")
    body = body.replace(b"A2000000", b"XFE1")
    workbook = Workbook.open(_rewrite(_book(xl), **{"xl/worksheets/sheet1.xml": body}))
    with pytest.raises(UnsafeWorkbook, match="grid"):
        workbook.sheet("S")


def test_too_many_cells_are_refused(xl: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xlsx, "MAX_CELLS_PER_SHEET", 3)
    workbook = Workbook.open(xl.make([("S", [[1, 2, 3, 4]])]))
    with pytest.raises(UnsafeWorkbook, match="cells"):
        workbook.sheet("S")


def test_what_is_not_a_workbook_is_unreadable(xl: SimpleNamespace) -> None:
    for junk in (b"", b"not a zip", b"PK\x03\x04garbage"):
        with pytest.raises(UnreadableWorkbook):
            Workbook.open(junk)
    no_workbook = io.BytesIO()
    with zipfile.ZipFile(no_workbook, "w") as archive:
        archive.writestr("hello.txt", "hi")
    with pytest.raises(UnreadableWorkbook, match="workbook.xml"):
        Workbook.open(no_workbook.getvalue())
    with pytest.raises(UnreadableWorkbook, match="valid XML"):
        Workbook.open(_rewrite(_book(xl), **{"xl/workbook.xml": b"<workbook><sheets>"}))
    with pytest.raises(UnreadableWorkbook, match="no readable worksheets"):
        Workbook.open(
            _rewrite(_book(xl), **{"xl/workbook.xml": b'<workbook xmlns="x"><sheets/></workbook>'})
        )


def test_a_missing_file_is_unreadable(tmp_path: Path) -> None:
    with pytest.raises(UnreadableWorkbook):
        Workbook.open(tmp_path / "missing.xlsx")


def test_a_broken_sheet_is_unreadable_when_it_is_read_not_before(xl: SimpleNamespace) -> None:
    workbook = Workbook.open(
        _rewrite(_book(xl), **{"xl/worksheets/sheet1.xml": b"<worksheet><sheetData>"})
    )
    with pytest.raises(UnreadableWorkbook):
        workbook.sheet("S")


def test_every_refusal_is_an_xlsx_error() -> None:
    assert issubclass(UnsafeWorkbook, xlsx.XlsxError) and issubclass(
        UnreadableWorkbook, xlsx.XlsxError
    )
    assert issubclass(xlsx.XlsxError, ValueError)
