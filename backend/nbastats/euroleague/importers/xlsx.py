"""A small, hardened ``.xlsx`` reader built on ``zipfile`` and ``xml.etree`` only.

Why not openpyxl
----------------
The workbook importer needs the *cached values* of a few sheets, in a file the user chose, on a
machine where the service runs unattended. openpyxl is a large dependency for that, and the
design forbids a new one at run time. An ``.xlsx`` is a zip of XML parts; reading values out
of it takes a few hundred lines, and writing them ourselves means every limit and refusal is
visible in one file rather than inherited.

What it reads
-------------
Cached ``<v>`` values only. A formula (``<f>``) is never evaluated or even looked at: a cell
whose formula has not been calculated has no value and reads as ``None``. Shared strings,
inline strings, formula string results, booleans and numbers are supported. A number whose
cell style is a date format becomes a ``datetime.date`` (or ``datetime`` when it has a time of
day), using the workbook's epoch (``date1904`` is honoured), because a date serial and an
ordinary number are indistinguishable without the style. An error cell (``#DIV/0!``) reads as
``None``, never as text a parser might mistake for a value. An empty or all-whitespace string
also reads as ``None``: a formula such as ``IFERROR(x/y,"")`` means "blank".

Defined names (``Regress`` pointing at ``Settings!$C$5``) are exposed, so a caller can read a
setting by the name the workbook's author gave it rather than by a cell address that moves
when a row is inserted.

Finding a table by its headers
------------------------------
Positions are the fragile part of a spreadsheet, so no importer here uses one. A
:class:`Column` names a header exactly (after trimming) and, where a header repeats within a
row, which occurrence; :meth:`Sheet.find_table` returns the first row that carries every
required header, and only those named columns are ever read. A column that is not named, such
as a gambling column sitting between two that are, is not read at all. If no row carries the
required headers the result is ``None`` and the importer reports the sheet as unreadable
rather than guessing a layout.

Refusals
--------
The file is refused (:class:`UnsafeWorkbook`) when it is larger than 25 MB, when its parts
would inflate to more than 200 MB in total, when it has more than 2,000 parts, when any
XML-like part contains ``<!DOCTYPE`` or ``<!ENTITY`` (no entity expansion is ever attempted,
because no entity is ever allowed), when a part contains a NUL byte or declares an encoding
other than UTF-8, US-ASCII or ISO-8859-1 (a multi-byte encoding could hide a ``DOCTYPE`` from
a byte scan), when any entry is encrypted, or when a sheet claims more rows, columns or cells
than a spreadsheet can hold. Anything that is not a readable workbook is
:class:`UnreadableWorkbook`. Both are :class:`XlsxError`.

The reader never writes, never follows an external link and never extracts a file to disk.
"""

from __future__ import annotations

import hashlib
import io
import math
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Iterable, Iterator, Mapping, Sequence

__all__ = [
    "MAX_FILE_BYTES",
    "MAX_INFLATED_BYTES",
    "MAX_PARTS",
    "XlsxError",
    "UnreadableWorkbook",
    "UnsafeWorkbook",
    "DefinedName",
    "Column",
    "Table",
    "Sheet",
    "Workbook",
    "excel_serial_to_datetime",
    "is_date_format",
    "col_to_index",
    "index_to_col",
    "cell_text",
    "cell_number",
    "cell_int",
    "cell_date",
]

#: A workbook larger than this is refused unread.
MAX_FILE_BYTES: int = 25 * 1024 * 1024
#: The total size every part may inflate to.
MAX_INFLATED_BYTES: int = 200 * 1024 * 1024
MAX_PARTS: int = 2000
MAX_ROW: Final = 1_048_576
MAX_COL: Final = 16_384
MAX_CELLS_PER_SHEET: Final = 3_000_000

_XML_SUFFIXES: Final = (".xml", ".rels", ".vml")
_DANGEROUS = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_DECLARED_ENCODING = re.compile(
    rb"^\s*<\?xml[^>]*?encoding\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE
)
_SAFE_ENCODINGS: Final = {b"utf-8", b"us-ascii", b"ascii", b"iso-8859-1", b"latin1", b"latin-1"}
_BOMS_REFUSED: Final = (b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff", b"\xff\xfe\x00\x00")


class XlsxError(ValueError):
    """Base class for every refusal of this reader."""


class UnreadableWorkbook(XlsxError):
    """The bytes are not a workbook this reader can read."""


class UnsafeWorkbook(XlsxError):
    """The workbook is refused for a safety reason (size, entities, encoding, encryption)."""


# ------------------------------------------------------------------------------ helpers


def col_to_index(letters: str) -> int:
    """``A`` is 1, ``Z`` is 26, ``AA`` is 27."""
    total = 0
    for ch in letters.upper():
        if not "A" <= ch <= "Z":
            raise ValueError(f"not a column: {letters!r}")
        total = total * 26 + (ord(ch) - 64)
    return total


def index_to_col(index: int) -> str:
    """The inverse of :func:`col_to_index`."""
    if index < 1:
        raise ValueError("column index starts at 1")
    letters = ""
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


_CELL_REF = re.compile(r"^([A-Za-z]{1,3})(\d+)$")


def _split_ref(ref: str) -> tuple[int, int]:
    match = _CELL_REF.match(ref)
    if not match:
        raise ValueError(f"not a cell reference: {ref!r}")
    return int(match.group(2)), col_to_index(match.group(1))


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


_ESCAPED = re.compile(r"_x([0-9A-Fa-f]{4})_")


def _unescape(text: str) -> str:
    """Excel writes some control characters as ``_x000D_``; ``_x005F_`` is a literal ``_``."""

    def repl(match: re.Match[str]) -> str:
        code = int(match.group(1), 16)
        return "_" if code == 0x5F else chr(code)

    return _ESCAPED.sub(repl, text) if "_x" in text else text


def excel_serial_to_datetime(serial: float, date1904: bool = False) -> datetime:
    """A spreadsheet date serial as a ``datetime``.

    The 1900 system counts a day (29 February 1900) that never existed, so serials 1 to 59 are
    one day earlier than the arithmetic from the later epoch would say and serial 60 is not a
    date. Raises ``ValueError`` for a negative, non-finite, out-of-range or impossible serial.
    """
    if not math.isfinite(serial) or serial < 0:
        raise ValueError(f"not a date serial: {serial!r}")
    days = math.floor(serial)
    fraction = serial - days
    if date1904:
        base = datetime(1904, 1, 1) + timedelta(days=days)
    else:
        if days == 60:
            raise ValueError("serial 60 is the non-existent 29 February 1900")
        if days == 0:
            raise ValueError("serial 0 is not a date")
        epoch = datetime(1899, 12, 31) if days < 60 else datetime(1899, 12, 30)
        base = epoch + timedelta(days=days)
    seconds = round(fraction * 86400)
    return base + timedelta(seconds=seconds)


_BUILTIN_DATE_IDS: Final = frozenset(
    set(range(14, 23)) | set(range(27, 37)) | set(range(45, 48)) | set(range(50, 59))
)


def is_date_format(code: str) -> bool:
    """True when a number-format code displays a date or a time."""
    text = re.sub(r'"[^"]*"', "", code)
    text = re.sub(r"\\.", "", text)
    text = re.sub(r"_.", "", text)
    text = re.sub(r"\*.", "", text)
    text = re.sub(r"\[(?![hms]+\])[^\]]*\]", "", text, flags=re.IGNORECASE)
    if text.strip().lower() in ("", "general", "@"):
        return False
    return re.search(r"[dmyhs]", text, re.IGNORECASE) is not None


# ------------------------------------------------------------------------------ cell coercion


def cell_text(value: Any) -> str | None:
    """A trimmed string, or ``None`` for blank or non-text."""
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def cell_number(value: Any) -> float | None:
    """A finite number, or ``None``. Booleans, text and dates are not numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def cell_int(value: Any) -> int | None:
    """A whole number as ``int`` (``14.0`` is 14), or ``None``."""
    number = cell_number(value)
    if number is None or number != math.floor(number):
        return None
    return int(number)


def cell_date(value: Any) -> date | None:
    """A ``date`` from a date cell; a ``datetime`` is reduced to its date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


# ------------------------------------------------------------------------------ model


@dataclass(frozen=True)
class DefinedName:
    """A workbook-level name for a cell or a rectangular range on one sheet."""

    name: str
    sheet: str
    first: tuple[int, int]
    last: tuple[int, int]
    hidden: bool = False

    @property
    def is_single_cell(self) -> bool:
        return self.first == self.last


@dataclass(frozen=True)
class Column:
    """A header to find: its exact text, and which occurrence when the row repeats it."""

    key: str
    header: str
    occurrence: int = 1
    required: bool = True


@dataclass(frozen=True)
class Table:
    """A located table: the header row and the column of each named key."""

    sheet: "Sheet"
    header_row: int
    columns: Mapping[str, int]

    def rows(
        self,
        *,
        stop_when_blank: str | None = None,
        first_row: int | None = None,
        last_row: int | None = None,
    ) -> Iterator[tuple[int, dict[str, Any]]]:
        """``(row number, {key: value})`` for each row below the header.

        Only the named columns appear. With ``stop_when_blank`` the walk ends at the first row
        whose value for that key is blank (a wholly empty row included), which is how a table
        is told from whatever follows it on the same sheet. Without it, empty rows are skipped
        and the walk runs to the end of the sheet.
        """
        start = first_row if first_row is not None else self.header_row + 1
        end = last_row if last_row is not None else self.sheet.max_row
        for number in range(start, end + 1):
            cells = self.sheet.cells.get(number)
            if not cells:
                if stop_when_blank is None:
                    continue
                return
            row = {key: cells.get(col) for key, col in self.columns.items()}
            if stop_when_blank is not None and row.get(stop_when_blank) is None:
                return
            yield number, row


@dataclass
class Sheet:
    """One worksheet's cached values. Blank cells are absent from ``cells``."""

    name: str
    cells: dict[int, dict[int, Any]] = field(default_factory=dict)
    max_row: int = 0
    max_col: int = 0
    error_cells: int = 0

    def value(self, row: int, col: int) -> Any:
        return self.cells.get(row, {}).get(col)

    def row(self, row: int) -> dict[int, Any]:
        return dict(self.cells.get(row, {}))

    @property
    def row_count(self) -> int:
        """Rows that hold at least one value."""
        return len(self.cells)

    def find_table(
        self,
        columns: Sequence[Column],
        *,
        first_row: int = 1,
        last_row: int | None = None,
    ) -> Table | None:
        """The first row carrying every required header, with the column of each header found.

        Optional headers (``required=False``) are included when present and simply absent from
        the table's ``columns`` otherwise. ``None`` when no row has all the required headers.
        """
        end = last_row if last_row is not None else self.max_row
        for number in range(first_row, end + 1):
            cells = self.cells.get(number)
            if not cells or len(cells) < sum(1 for c in columns if c.required):
                continue
            by_text: dict[str, list[int]] = {}
            for col in sorted(cells):
                text = cell_text(cells[col])
                if text is not None:
                    by_text.setdefault(text, []).append(col)
            found: dict[str, int] = {}
            complete = True
            for column in columns:
                positions = by_text.get(column.header, [])
                if len(positions) >= column.occurrence:
                    found[column.key] = positions[column.occurrence - 1]
                elif column.required:
                    complete = False
                    break
            if complete:
                return Table(self, number, found)
        return None


# ------------------------------------------------------------------------------ the workbook


def _check_xml_part(name: str, data: bytes) -> None:
    """Refuse an XML-like part that could carry an entity, hide one, or is not text."""
    if any(data.startswith(bom) for bom in _BOMS_REFUSED):
        raise UnsafeWorkbook(f"{name}: a UTF-16 or UTF-32 part is not accepted")
    if b"\x00" in data:
        raise UnsafeWorkbook(f"{name}: contains a NUL byte")
    match = _DECLARED_ENCODING.match(data[:300].lstrip(b"\xef\xbb\xbf"))
    if match and match.group(1).lower() not in _SAFE_ENCODINGS:
        raise UnsafeWorkbook(f"{name}: declares the encoding {match.group(1).decode('latin-1')!r}")
    if _DANGEROUS.search(data):
        raise UnsafeWorkbook(f"{name}: contains a DOCTYPE or ENTITY declaration")


def _is_kept(name: str) -> bool:
    if name in (
        "xl/workbook.xml",
        "xl/_rels/workbook.xml.rels",
        "xl/sharedStrings.xml",
        "xl/styles.xml",
    ):
        return True
    return name.startswith("xl/worksheets/") and name.endswith(".xml") and "/_rels/" not in name


class Workbook:
    """A read-only view of an ``.xlsx`` file. Use :meth:`open`."""

    def __init__(
        self,
        *,
        sha256: str,
        size: int,
        parts: dict[str, bytes],
        sheet_parts: Mapping[str, str],
        sheet_order: Sequence[str],
        date1904: bool,
        defined_names: Mapping[str, DefinedName],
        shared_strings: Sequence[str],
        date_styles: frozenset[int],
    ) -> None:
        self.sha256 = sha256
        self.size = size
        self._parts = parts
        self._sheet_parts = dict(sheet_parts)
        self.sheet_names: tuple[str, ...] = tuple(sheet_order)
        self.date1904 = date1904
        self.defined_names: dict[str, DefinedName] = dict(defined_names)
        self._shared = list(shared_strings)
        self._date_styles = date_styles
        self._sheets: dict[str, Sheet] = {}

    # ------------------------------------------------------------------ opening

    @classmethod
    def open(cls, source: str | Path | bytes) -> "Workbook":
        """Open a workbook from a path or from bytes, applying every refusal in the module
        docstring before any value is read."""
        if isinstance(source, (bytes, bytearray)):
            data = bytes(source)
        else:
            path = Path(source)
            try:
                size = path.stat().st_size
            except OSError as exc:
                raise UnreadableWorkbook(f"cannot read {path}: {exc}") from exc
            if size > MAX_FILE_BYTES:
                raise UnsafeWorkbook(f"{path.name} is {size} bytes; the limit is {MAX_FILE_BYTES}")
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise UnreadableWorkbook(f"cannot read {path}: {exc}") from exc
        if len(data) > MAX_FILE_BYTES:
            raise UnsafeWorkbook(
                f"the workbook is {len(data)} bytes; the limit is {MAX_FILE_BYTES}"
            )
        sha256 = hashlib.sha256(data).hexdigest()
        parts = cls._read_parts(data)
        return cls._build(sha256, len(data), parts)

    @staticmethod
    def _read_parts(data: bytes) -> dict[str, bytes]:
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise UnreadableWorkbook("the file is not a zip archive, so not an .xlsx") from exc
        with archive:
            infos = archive.infolist()
            if len(infos) > MAX_PARTS:
                raise UnsafeWorkbook(
                    f"the archive has {len(infos)} parts; the limit is {MAX_PARTS}"
                )
            declared = sum(info.file_size for info in infos)
            if declared > MAX_INFLATED_BYTES:
                raise UnsafeWorkbook(
                    f"the archive would inflate to {declared} bytes; the limit is "
                    f"{MAX_INFLATED_BYTES}"
                )
            budget = MAX_INFLATED_BYTES
            kept: dict[str, bytes] = {}
            for info in infos:
                if info.is_dir():
                    continue
                if info.flag_bits & 0x1:
                    raise UnsafeWorkbook(f"{info.filename}: encrypted entries are not accepted")
                name = info.filename.lstrip("/")
                xmlish = name.lower().endswith(_XML_SUFFIXES)
                if not xmlish and not _is_kept(name):
                    # Binary parts (images, printer settings) are never read; their declared
                    # size already counted against the cap above.
                    continue
                try:
                    with archive.open(info) as handle:
                        chunks: list[bytes] = []
                        while True:
                            chunk = handle.read(1 << 20)
                            if not chunk:
                                break
                            budget -= len(chunk)
                            if budget < 0:
                                raise UnsafeWorkbook("the archive inflates past the size limit")
                            chunks.append(chunk)
                except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as exc:
                    raise UnreadableWorkbook(f"{info.filename}: cannot be read ({exc})") from exc
                body = b"".join(chunks)
                if xmlish:
                    _check_xml_part(name, body)
                if _is_kept(name):
                    kept[name] = body
            return kept

    @classmethod
    def _build(cls, sha256: str, size: int, parts: dict[str, bytes]) -> "Workbook":
        if "xl/workbook.xml" not in parts:
            raise UnreadableWorkbook("the archive has no xl/workbook.xml, so it is not an .xlsx")
        try:
            root = ET.fromstring(parts["xl/workbook.xml"])
        except ET.ParseError as exc:
            raise UnreadableWorkbook(f"xl/workbook.xml is not valid XML: {exc}") from exc

        date1904 = False
        sheets_decl: list[tuple[str, str]] = []
        names_raw: list[ET.Element] = []
        for element in root.iter():
            tag = _local(element.tag)
            if tag == "workbookPr":
                date1904 = element.get("date1904", "").lower() in ("1", "true")
            elif tag == "sheet":
                rid = next((v for k, v in element.attrib.items() if _local(k) == "id"), None)
                name = element.get("name")
                if name and rid:
                    sheets_decl.append((name, rid))
            elif tag == "definedName":
                names_raw.append(element)

        relationships: dict[str, str] = {}
        rels = parts.get("xl/_rels/workbook.xml.rels")
        if rels is not None:
            try:
                for rel in ET.fromstring(rels).iter():
                    if _local(rel.tag) == "Relationship" and rel.get("Id") and rel.get("Target"):
                        relationships[rel.get("Id")] = rel.get("Target")  # type: ignore[index]
            except ET.ParseError as exc:
                raise UnreadableWorkbook(
                    f"the workbook relationships are not valid XML: {exc}"
                ) from exc

        sheet_parts: dict[str, str] = {}
        order: list[str] = []
        for name, rid in sheets_decl:
            target = relationships.get(rid)
            if target is None:
                continue
            path = (
                target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            )
            if path in parts and name not in sheet_parts:
                sheet_parts[name] = path
                order.append(name)
        if not order:
            raise UnreadableWorkbook("the workbook has no readable worksheets")

        defined = cls._parse_defined_names(names_raw, set(order))
        shared = cls._parse_shared_strings(parts.get("xl/sharedStrings.xml"))
        date_styles = cls._parse_date_styles(parts.get("xl/styles.xml"))
        return cls(
            sha256=sha256,
            size=size,
            parts=parts,
            sheet_parts=sheet_parts,
            sheet_order=order,
            date1904=date1904,
            defined_names=defined,
            shared_strings=shared,
            date_styles=date_styles,
        )

    # ------------------------------------------------------------------ parts

    _NAME_REF = re.compile(
        r"^(?:'((?:[^']|'')+)'|([^'!]+))!"
        r"\$?([A-Za-z]{1,3})\$?(\d+)(?::\$?([A-Za-z]{1,3})\$?(\d+))?$"
    )

    @classmethod
    def _parse_defined_names(
        cls, elements: Iterable[ET.Element], sheet_names: set[str]
    ) -> dict[str, DefinedName]:
        out: dict[str, DefinedName] = {}
        for element in elements:
            name = element.get("name")
            if not name or name.startswith("_xlnm.") or element.get("localSheetId") is not None:
                continue
            match = cls._NAME_REF.match((element.text or "").strip())
            if not match:
                continue
            sheet = (match.group(1) or match.group(2)).replace("''", "'")
            if sheet not in sheet_names:
                continue
            first = (int(match.group(4)), col_to_index(match.group(3)))
            if match.group(5):
                last = (int(match.group(6)), col_to_index(match.group(5)))
            else:
                last = first
            if last[0] < first[0] or last[1] < first[1]:
                continue
            out.setdefault(
                name,
                DefinedName(
                    name, sheet, first, last, element.get("hidden", "").lower() in ("1", "true")
                ),
            )
        return out

    @staticmethod
    def _parse_shared_strings(data: bytes | None) -> list[str]:
        if data is None:
            return []
        strings: list[str] = []
        try:
            stack_phonetic = 0
            current: list[str] = []
            for event, element in ET.iterparse(io.BytesIO(data), events=("start", "end")):
                tag = _local(element.tag)
                if event == "start":
                    if tag == "rPh":
                        stack_phonetic += 1
                    elif tag == "si":
                        current = []
                    continue
                if tag == "rPh":
                    stack_phonetic -= 1
                elif tag == "t" and stack_phonetic == 0:
                    current.append(element.text or "")
                elif tag == "si":
                    strings.append(_unescape("".join(current)))
                    element.clear()
        except ET.ParseError as exc:
            raise UnreadableWorkbook(f"xl/sharedStrings.xml is not valid XML: {exc}") from exc
        return strings

    @staticmethod
    def _parse_date_styles(data: bytes | None) -> frozenset[int]:
        if data is None:
            return frozenset()
        try:
            root = ET.fromstring(data)
        except ET.ParseError as exc:
            raise UnreadableWorkbook(f"xl/styles.xml is not valid XML: {exc}") from exc
        custom: dict[int, str] = {}
        xf_ids: list[int] = []
        for element in root.iter():
            tag = _local(element.tag)
            if tag == "numFmt":
                try:
                    custom[int(element.get("numFmtId", ""))] = element.get("formatCode", "")
                except ValueError:
                    continue
            elif tag == "cellXfs":
                for xf in element:
                    if _local(xf.tag) == "xf":
                        try:
                            xf_ids.append(int(xf.get("numFmtId", "0")))
                        except ValueError:
                            xf_ids.append(0)
        dates: set[int] = set()
        for style, fmt_id in enumerate(xf_ids):
            if fmt_id in custom:
                if is_date_format(custom[fmt_id]):
                    dates.add(style)
            elif fmt_id in _BUILTIN_DATE_IDS:
                dates.add(style)
        return frozenset(dates)

    # ------------------------------------------------------------------ public API

    def has_sheet(self, name: str) -> bool:
        return name in self._sheet_parts

    def sheet(self, name: str) -> Sheet:
        """The parsed sheet called ``name`` (cached). ``KeyError`` when there is none."""
        cached = self._sheets.get(name)
        if cached is not None:
            return cached
        if name not in self._sheet_parts:
            raise KeyError(name)
        parsed = self._parse_sheet(name, self._parts[self._sheet_parts[name]])
        self._sheets[name] = parsed
        return parsed

    def defined_values(self, name: str) -> list[Any]:
        """The cached values a defined name points at, row by row. ``[]`` for an unknown name."""
        target = self.defined_names.get(name)
        if target is None:
            return []
        sheet = self.sheet(target.sheet)
        return [
            sheet.value(row, col)
            for row in range(target.first[0], target.last[0] + 1)
            for col in range(target.first[1], target.last[1] + 1)
        ]

    def defined_value(self, name: str) -> Any:
        """The cached value of a single-cell defined name, or ``None``."""
        target = self.defined_names.get(name)
        if target is None or not target.is_single_cell:
            return None
        return self.sheet(target.sheet).value(*target.first)

    # ------------------------------------------------------------------ sheet parsing

    def _parse_sheet(self, name: str, data: bytes) -> Sheet:
        sheet = Sheet(name=name)
        row_number = 0
        next_col = 0
        total = 0
        try:
            for event, element in ET.iterparse(io.BytesIO(data), events=("start", "end")):
                tag = _local(element.tag)
                if event == "start":
                    if tag == "row":
                        raw = element.get("r")
                        row_number = int(raw) if raw and raw.isdigit() else row_number + 1
                        next_col = 0
                        if row_number > MAX_ROW:
                            raise UnsafeWorkbook(f"{name}: a row number past {MAX_ROW}")
                    continue
                if tag == "row":
                    element.clear()
                    continue
                if tag != "c":
                    continue
                ref = element.get("r")
                if ref:
                    try:
                        cell_row, col = _split_ref(ref)
                    except ValueError:
                        element.clear()
                        continue
                    row_number = cell_row if cell_row else row_number
                else:
                    col = next_col + 1
                next_col = col
                if col > MAX_COL or row_number > MAX_ROW:
                    raise UnsafeWorkbook(f"{name}: a cell outside the spreadsheet grid")
                value, is_error = self._cell_value(element)
                element.clear()
                if is_error:
                    sheet.error_cells += 1
                if value is None:
                    continue
                total += 1
                if total > MAX_CELLS_PER_SHEET:
                    raise UnsafeWorkbook(f"{name}: more than {MAX_CELLS_PER_SHEET} cells")
                sheet.cells.setdefault(row_number, {})[col] = value
                if row_number > sheet.max_row:
                    sheet.max_row = row_number
                if col > sheet.max_col:
                    sheet.max_col = col
        except ET.ParseError as exc:
            raise UnreadableWorkbook(f"sheet {name!r} is not valid XML: {exc}") from exc
        return sheet

    def _cell_value(self, element: ET.Element) -> tuple[Any, bool]:
        """``(value, is_error)`` for one ``<c>`` element."""
        kind = element.get("t", "n")
        v_text: str | None = None
        inline: list[str] = []
        for child in element:
            tag = _local(child.tag)
            if tag == "v":
                v_text = child.text
            elif tag == "is":
                inline = [t.text or "" for t in child.iter() if _local(t.tag) == "t"]
        if kind == "inlineStr":
            return _blank_to_none(_unescape("".join(inline))), False
        if kind == "e":
            return None, True
        if v_text is None:
            return None, False
        if kind == "s":
            try:
                return _blank_to_none(self._shared[int(v_text)]), False
            except (ValueError, IndexError):
                return None, True
        if kind == "str":
            return _blank_to_none(_unescape(v_text)), False
        if kind == "b":
            return v_text.strip() in ("1", "true"), False
        if kind == "d":
            try:
                return datetime.fromisoformat(v_text.strip().replace("Z", "+00:00")), False
            except ValueError:
                return None, True
        # numeric
        text = v_text.strip()
        if not text:
            return None, False
        try:
            number: Any = int(text) if re.fullmatch(r"-?\d+", text) else float(text)
        except ValueError:
            return None, True
        if isinstance(number, float) and not math.isfinite(number):
            return None, True
        style = element.get("s")
        if style is not None and style.isdigit() and int(style) in self._date_styles:
            try:
                moment = excel_serial_to_datetime(float(number), self.date1904)
            except ValueError:
                return None, True
            return (moment.date() if moment.time() == datetime.min.time() else moment), False
        return number, False


def _blank_to_none(text: str) -> str | None:
    return text if text.strip() else None
