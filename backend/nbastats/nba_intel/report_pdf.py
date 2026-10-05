"""The NBA injury report parser: a PDF table read by position, header-checked, failing closed.

What the file is, and what is and is not known about it
--------------------------------------------------------
The NBA publishes its official injury report as a PDF in Eastern-time quarter-hour slots
(``.../referee/injury/Injury-Report_2026-10-22_05_30PM.pdf``). It is a borderless table with seven
columns, ``Game Date``, ``Game Time``, ``Matchup``, ``Team``, ``Player Name``, ``Current Status``
and ``Reason``, repeated at the top of every page, with a title above it and ``Page n of m`` below.
Date, time, matchup and team are printed once and left blank on the rows that follow, long reasons
wrap onto a second line, a row can break across a page, and a team that has not filed shows the
words ``NOT YET SUBMITTED`` where its players would be.

**Nothing about that layout was verified against a real file.** Every sports host is denied in the
development environment, so this parser was written against the documented shape and tested with
PDFs the tests build themselves (invented players, the column geometry above). The layout has
changed three times since 2021-22. The design therefore treats a wrong guess as the worst outcome
and an honest "unreadable" as an acceptable one, and every decision below follows from that.

Fail closed, in four places
---------------------------
1. **The header is the contract.** On *every* page the seven header names must be found, in order,
   on one line. A page with body text and no header, or a header with a name changed, produces
   status ``headerMismatch``, **zero entries**, and the caller shows the source as "unreadable".
   No page's column edges are borrowed from another.
2. **Every row is validated, not just positioned.** A status must be one of the five. A player
   name must be ``Last, First``. The date, the matchup (``AAA@BBB``) and the team must be known by
   the time a row is finished. A line that cannot be understood is *dropped and counted*, never
   patched up, and the snapshot becomes ``partial`` with the reasons in the error text.
3. **Dropped lines poison their team.** A team whose block contained a dropped line is not marked
   ``submitted``, because "a player not listed is a player the team did not list" is only true if
   we read the whole list. That team simply has no report state (``noReport``).
4. **Layout drift trips the whole parse.** If more than half the body lines were dropped, the
   columns are almost certainly misaligned, and the result is ``headerMismatch`` with no entries
   rather than a thin, plausible-looking remainder.

``NOT YET SUBMITTED`` is its own state (:attr:`TeamMarker.state`), never an empty list: it means
"we do not know who is out", which is different from "nobody is out".

How positions are obtained
--------------------------
``pypdf`` (the optional ``injuries`` extra) is imported lazily, inside the function that needs it,
so importing this module, running the rest of Hardwood and running every test that does not read a
PDF need no PDF library; without it :class:`ReportParserUnavailable` says what to install.

Text runs with coordinates come from ``PageObject.extract_text``, with two corrections, both of
which were found by running the parser against the whole range of ``pypdf`` releases rather than
only the one installed here.

*Isolating fragments.* ``pypdf`` merges consecutive text operations on one baseline into a single
run, so a producer that writes a whole row inside one ``BT``/``ET`` block would collapse seven cells
into one run and destroy the geometry the parser depends on. It flushes its buffer whenever the
graphics matrix changes, so :func:`_isolate_text` inserts an identity ``cm`` after every
text-showing operator in an in-memory copy of the page's content stream. Each fragment then arrives
as its own run, whatever the producer's block structure. Nothing is written back to any file.

*Positions from the operator, not the buffer.* The position ``visitor_text`` reports comes from an
internal buffer and is wrong in most releases (every fragment after the first read ``(0, 0)`` in
4.0 through 6.4). The matrices passed to ``visitor_operand_before`` at each operator are right in
every release, so :class:`_PageRuns` reads the position there and pairs it with the text that the
injected ``cm`` flushes. The parser is tested on 4.0.1, 4.3.1, 5.0.0, 5.9.0, 6.0.0, 6.4.0 and
6.18.1; a fragment whose position cannot be established makes the page unreadable, not guessed.

Geometry
--------
Runs are grouped into visual lines by baseline (tolerance 40% of the median font size), the header
line is found by matching the seven names across one or more runs, and each column spans from its
header word's left edge (less a small slack) to the next one's. Lines above the header are the title
band (the report's own "as of" time is read from it); lines at or below the ``Page n of m`` baseline
are the footer band and are dropped. Pages with a ``/Rotate`` entry are rotated into display
orientation first. The assumption that data is aligned with the left edge of its header word is
exactly the kind of thing a real file may contradict; the row validation above is what turns a
contradiction into "unreadable" instead of wrong data.

Mapping the reason
------------------
:func:`reason_category` maps the report's free text to the shared vocabulary: ``Injury/Illness -
...`` is ``injury`` (``illness`` when the detail says so), ``Rest`` is ``rest``, ``Personal
Reasons`` is ``personal``, ``League Suspension`` is ``suspension``, ``G League - ...`` is
``gLeague``, ``Not with Team`` is ``notWithTeam`` and anything else is ``other``.

This module imports no database and no HTTP client; :mod:`nbastats.nba_intel.status` turns a
:class:`ParsedReport` into rows.
"""

from __future__ import annotations

import importlib
import importlib.util
import io
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Sequence

from ..shared.availability import InvalidStatusError, normalise_status

__all__ = [
    "EXPECTED_HEADER",
    "NOT_YET_SUBMITTED",
    "MAX_PAGES",
    "MAX_DROPPED_SHARE",
    "ReportParserUnavailable",
    "ReportUnreadable",
    "Run",
    "ReportRow",
    "TeamMarker",
    "ParsedReport",
    "pypdf_available",
    "extract_runs",
    "parse_pages",
    "parse_report",
    "reason_category",
    "split_matchup",
]

#: The seven column names, in order. Matching is case-insensitive and whitespace-insensitive.
EXPECTED_HEADER: tuple[str, ...] = (
    "Game Date",
    "Game Time",
    "Matchup",
    "Team",
    "Player Name",
    "Current Status",
    "Reason",
)

NOT_YET_SUBMITTED = "NOT YET SUBMITTED"

#: A report over this many pages is not an injury report.
MAX_PAGES = 40
MAX_RUNS_PER_PAGE = 6000
MAX_ROWS = 2000
#: More than this share of body lines dropped means the layout was not understood.
MAX_DROPPED_SHARE = 0.5
MAX_REASON_CHARS = 400
MAX_ERRORS_KEPT = 20
#: Left slack, in points, applied to every column edge.
EDGE_SLACK = 3.0

_COL_DATE, _COL_TIME, _COL_MATCHUP, _COL_TEAM, _COL_PLAYER, _COL_STATUS, _COL_REASON = range(7)
_LEAD_COLUMNS = (_COL_DATE, _COL_TIME, _COL_MATCHUP, _COL_TEAM)

_SPACE = re.compile(r"\s+")
_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$")
_MATCHUP = re.compile(r"^([A-Z]{2,4})\s*@\s*([A-Z]{2,4})$")
_FOOTER = re.compile(r"^page\s+\d+\s+of\s+\d+$", re.IGNORECASE)
_FOOTER_TAIL = re.compile(r"\bpage\s+\d+\s+of\s+\d+\s*$", re.IGNORECASE)
_NYS = re.compile(r"^not\s+yet\s+submitted$", re.IGNORECASE)
_AS_OF = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\s+(\d{1,2}):(\d{2})\s*([AaPp])\.?[Mm]\.?")

#: Content-stream operators that put text on the page.
_SHOW_OPERATORS = (b"Tj", b"TJ", b"'", b'"')


# ----------------------------------------------------------------------------- errors


class ReportParserUnavailable(RuntimeError):
    """``pypdf`` is not installed. Install the ``injuries`` extra."""


class ReportUnreadable(RuntimeError):
    """The bytes are not a PDF this parser can open (corrupt, encrypted, wrong size)."""


# ----------------------------------------------------------------------------- values


@dataclass(frozen=True, slots=True)
class Run:
    """One text fragment and where it starts, in display space (x right, y up, points)."""

    text: str
    x: float
    y: float
    size: float
    seq: int


@dataclass(frozen=True, slots=True)
class ReportRow:
    """One player's entry, exactly as the report printed it (``status`` is normalised)."""

    page: int
    line: int
    game_date: date
    game_time_raw: str | None
    matchup: str
    away_abbr: str
    home_abbr: str
    team_name: str
    player_name_raw: str
    status_raw: str
    status: str
    reason_raw: str | None


@dataclass(frozen=True, slots=True)
class TeamMarker:
    """A team's report state for one game: ``submitted`` (it listed at least one player) or
    ``notYetSubmitted``. ``clean`` is False when a line in the team's block was dropped, in which
    case the marker must not be stored."""

    game_date: date
    matchup: str
    team_name: str
    state: str
    clean: bool = True


@dataclass(frozen=True, slots=True)
class ParsedReport:
    """What a report said. ``status`` is one of ``ok``, ``partial``, ``headerMismatch`` or
    ``empty`` (the snapshot ``parse_status`` vocabulary)."""

    status: str
    rows: tuple[ReportRow, ...] = ()
    teams: tuple[TeamMarker, ...] = ()
    #: The "as of" time printed in the title band, a naive US Eastern wall-clock time.
    as_of_et: datetime | None = None
    pages: int = 0
    body_lines: int = 0
    dropped: int = 0
    errors: tuple[str, ...] = ()

    @property
    def parse_error(self) -> str | None:
        if not self.errors:
            return None
        text = "; ".join(self.errors)
        extra = self.dropped - len(self.errors)
        return text + (
            f"; and {extra} more" if extra > 0 and self.status != "headerMismatch" else ""
        )

    @property
    def entry_count(self) -> int:
        return len(self.rows)


# ------------------------------------------------------------------------ small helpers


def pypdf_available() -> bool:
    """True when ``pypdf`` can be imported (without importing it)."""
    try:
        return importlib.util.find_spec("pypdf") is not None
    except (ImportError, ValueError):
        return False


def _import_pypdf() -> Any:
    try:
        return importlib.import_module("pypdf")
    except ImportError as exc:
        raise ReportParserUnavailable(
            "pypdf is not installed. Install the 'injuries' extra: "
            "pip install 'hardwood-backend[injuries]'"
        ) from exc


def _norm(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


def _key(text: str) -> str:
    return _norm(text).casefold()


def split_matchup(text: str) -> tuple[str, str, str] | None:
    """``"BOS@NYK"`` gives ``("BOS@NYK", "BOS", "NYK")`` (away, then home); ``None`` otherwise."""
    found = _MATCHUP.match(_norm(text).upper())
    if not found:
        return None
    away, home = found.group(1), found.group(2)
    return f"{away}@{home}", away, home


def _parse_date(text: str) -> date | None:
    found = _DATE.match(_norm(text))
    if not found:
        return None
    month, day, year = int(found.group(1)), int(found.group(2)), found.group(3)
    full = int(year) + 2000 if len(year) == 2 else int(year)
    try:
        return date(full, month, day)
    except ValueError:
        return None


def _parse_as_of(text: str) -> datetime | None:
    found = _AS_OF.search(text)
    if not found:
        return None
    month, day, year = int(found.group(1)), int(found.group(2)), found.group(3)
    full = int(year) + 2000 if len(year) == 2 else int(year)
    hour, minute = int(found.group(4)), int(found.group(5))
    if not (1 <= hour <= 12 and 0 <= minute <= 59):
        return None
    hour24 = hour % 12 + (12 if found.group(6).upper() == "P" else 0)
    try:
        return datetime(full, month, day, hour24, minute)
    except ValueError:
        return None


# ------------------------------------------------------------------ reason -> category


_INJURY_DETAIL_ILLNESS = re.compile(r"\billness\b|\bflu\b|\bcovid\b|\bsick\b", re.IGNORECASE)


def reason_category(reason: str | None) -> str | None:
    """Map the report's reason text to the shared reason vocabulary; ``None`` for no reason.

    The order matters: ``Not With Team - Personal Reasons`` is *not with team*, not personal, so
    the more specific prefixes are tried first. ``Injury/Illness - Illness`` is ``illness``; the
    word "illness" in the ``Injury/Illness`` prefix itself does not count, only the detail after it.
    """
    if reason is None:
        return None
    text = _norm(reason)
    if not text:
        return None
    lowered = text.casefold()
    if lowered.startswith("not with team"):
        return "notWithTeam"
    if lowered.startswith("g league") or lowered.startswith("g-league"):
        return "gLeague"
    if lowered.startswith("league suspension") or lowered.startswith("suspension"):
        return "suspension"
    if (
        lowered == "rest"
        or lowered.startswith("rest ")
        or lowered.startswith("rest-")
        or (lowered.startswith("rest;"))
    ):
        return "rest"
    if lowered.startswith("personal"):
        return "personal"
    if (
        lowered.startswith("injury/illness")
        or lowered.startswith("injury")
        or lowered.startswith("illness")
    ):
        detail = re.split(r"\s[-–—]\s|:", text, maxsplit=1)
        remainder = detail[1] if len(detail) > 1 else ""
        if lowered.startswith("illness") or _INJURY_DETAIL_ILLNESS.search(remainder):
            return "illness"
        return "injury"
    return "other"


# ------------------------------------------------------------------ pypdf: runs


def _display_point(
    x: float, y: float, rotation: int, left: float, bottom: float, width: float, height: float
) -> tuple[float, float]:
    """Map a point in the page's user space to display space for a ``/Rotate`` of 0/90/180/270."""
    x -= left
    y -= bottom
    if rotation == 90:
        return y, width - x
    if rotation == 180:
        return width - x, height - y
    if rotation == 270:
        return height - y, x
    return x, y


def _isolate_text(page: Any, reader: Any) -> None:
    """Make ``pypdf`` report every text-showing operation as its own run. See the docstring."""
    from pypdf.generic import ContentStream, FloatObject, NameObject  # lazy, with pypdf

    contents = page.get_contents()
    if contents is None:
        return
    stream = ContentStream(contents, reader)
    operations: list[tuple[list[Any], bytes]] = []
    for operands, operator in stream.operations:
        operations.append((operands, operator))
        if operator in _SHOW_OPERATORS:
            identity = [FloatObject(v) for v in (1, 0, 0, 1, 0, 0)]
            operations.append((identity, b"cm"))
    stream.operations = operations
    page[NameObject("/Contents")] = stream


def extract_runs(pdf: bytes) -> list[list[Run]]:
    """Read a PDF into one list of :class:`Run` per page. Imports ``pypdf`` lazily.

    Raises :class:`ReportParserUnavailable` without ``pypdf`` and :class:`ReportUnreadable` for a
    file it cannot open, an encrypted file, or one with no pages or too many.
    """
    pypdf = _import_pypdf()
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf), strict=False)
        if getattr(reader, "is_encrypted", False):
            try:
                if not reader.decrypt(""):
                    raise ReportUnreadable("the PDF is encrypted")
            except ReportUnreadable:
                raise
            except Exception as exc:  # noqa: BLE001 - any failure to decrypt is "unreadable"
                raise ReportUnreadable(f"the PDF is encrypted ({type(exc).__name__})") from exc
        pages = list(reader.pages)
    except ReportUnreadable:
        raise
    except Exception as exc:  # noqa: BLE001 - pypdf raises many types for a bad file
        raise ReportUnreadable(
            f"the PDF could not be opened ({type(exc).__name__}: {exc})"
        ) from exc
    if not pages:
        raise ReportUnreadable("the PDF has no pages")
    if len(pages) > MAX_PAGES:
        raise ReportUnreadable(f"the PDF has {len(pages)} pages; an injury report has fewer")

    out: list[list[Run]] = []
    for page in pages:
        try:
            collector = _PageRuns(page)
            _isolate_text(page, reader)
            page.extract_text(
                visitor_operand_before=collector.before,
                visitor_operand_after=collector.after,
                visitor_text=collector.visit,
            )
        except ReportUnreadable:
            raise
        except Exception as exc:  # noqa: BLE001 - a page whose text cannot be read is unreadable
            raise ReportUnreadable(
                f"a page's text could not be read ({type(exc).__name__}: {exc})"
            ) from exc
        if collector.unplaced:
            raise ReportUnreadable(
                f"{collector.unplaced} text fragments on a page could not be positioned, "
                "so the columns cannot be trusted (an old pypdf, or text in an unsupported place)"
            )
        out.append(collector.runs)
    return out


class _PageRuns:
    """Collect one page's text fragments, each bound to the position of its own operator.

    ``pypdf`` offers two ways to learn where text is. ``visitor_text`` hands over the text with a
    position taken from an internal buffer, and **that position is not reliable across versions**:
    every release from 4.0 through 6.4 reported ``(0, 0)`` for every fragment after the first in
    this very layout, and only recent ones get it right. ``visitor_operand_before`` is called for
    every content-stream operator with the *current* graphics and text matrices, which is the basic
    state every version tracks and which agrees across 4.0.1 to 6.18.

    So the position is read there. When a text-showing operator arrives its matrices are held as
    "pending"; the identity ``cm`` that :func:`_isolate_text` put straight after it makes ``pypdf``
    flush that one fragment, and :meth:`visit` pairs the flushed text with the pending position.
    Any other operator in between clears the pending position, so text that was *not* isolated (for
    instance inside a form XObject) is never paired with the wrong fragment's coordinates: it falls
    back to the position ``pypdf`` itself reports, and if that is the degenerate ``(0, 0)`` the
    fragment is counted in :attr:`unplaced`, which makes the whole page unreadable rather than
    guessed.
    """

    def __init__(self, page: Any) -> None:
        self.runs: list[Run] = []
        self.unplaced = 0
        self._pending: tuple[Any, Any] | None = None
        self._rotation = int(getattr(page, "rotation", 0) or 0) % 360
        box = page.mediabox
        self._left, self._bottom = float(box.left), float(box.bottom)
        self._width, self._height = float(box.width), float(box.height)

    def before(self, operator: Any, operands: Any, cm: Any, tm: Any) -> None:
        if operator in _SHOW_OPERATORS:
            self._pending = (list(cm), list(tm))
        elif operator != b"cm":
            self._pending = None

    def after(self, operator: Any, operands: Any, cm: Any, tm: Any) -> None:
        if operator == b"cm":
            self._pending = None  # the flush has happened; a fragment that had no text has no run

    def visit(self, text: Any, cm: Any, tm: Any, _font: Any, font_size: Any) -> None:
        value = _norm(text) if isinstance(text, str) else ""
        if not value or len(self.runs) >= MAX_RUNS_PER_PAGE:
            return
        bound = self._pending is not None
        cm_matrix, tm_matrix = self._pending if self._pending is not None else (cm, tm)
        self._pending = None
        try:
            _a, _b, c, d, e, f = (float(v) for v in tm_matrix[:6])
            ca, cb, cc, cd, ce, cf = (float(v) for v in cm_matrix[:6])
            size = float(font_size or 0.0)
        except (TypeError, ValueError, IndexError):
            self.unplaced += 1
            return
        x = e * ca + f * cc + ce
        y = e * cb + f * cd + cf
        if not (math.isfinite(x) and math.isfinite(y)) or (not bound and x == 0.0 and y == 0.0):
            self.unplaced += 1
            return
        scale = math.hypot(c * ca + d * cc, c * cb + d * cd)
        dx, dy = _display_point(
            x, y, self._rotation, self._left, self._bottom, self._width, self._height
        )
        self.runs.append(Run(value, dx, dy, abs(size * scale) or abs(size), len(self.runs)))


# --------------------------------------------------------------------- pure: geometry


@dataclass(slots=True)
class _Line:
    y: float
    runs: list[Run]

    @property
    def text(self) -> str:
        return _norm(" ".join(run.text for run in self.runs))

    @property
    def is_footer(self) -> bool:
        """A ``Page n of m`` line, whether it stands alone or shares a baseline with other text."""
        return any(_FOOTER.match(run.text) for run in self.runs) or bool(
            _FOOTER_TAIL.search(self.text)
        )


def _group_lines(runs: Sequence[Run]) -> list[_Line]:
    """Group runs into visual lines, top to bottom, each left to right."""
    if not runs:
        return []
    sizes = sorted(run.size for run in runs if run.size > 0)
    median = sizes[len(sizes) // 2] if sizes else 9.0
    tolerance = max(1.0, 0.4 * median)
    ordered = sorted(runs, key=lambda r: (-r.y, r.x, r.seq))
    lines: list[_Line] = []
    current: list[Run] = [ordered[0]]
    reference = ordered[0].y
    for run in ordered[1:]:
        if reference - run.y <= tolerance:
            current.append(run)
        else:
            lines.append(_Line(reference, sorted(current, key=lambda r: (r.x, r.seq))))
            current, reference = [run], run.y
    lines.append(_Line(reference, sorted(current, key=lambda r: (r.x, r.seq))))
    return lines


def _match_header(line: _Line) -> list[float] | None:
    """The left edges of the seven header names if ``line`` is the header, else ``None``.

    A name may be one run or up to four consecutive runs ("Game" and "Date" separately). Runs
    before the first name are skipped; once matching has begun, any run that does not continue the
    header disqualifies the line.
    """
    runs = line.runs
    position = 0
    edges: list[float] = []
    wanted = [_key(name) for name in EXPECTED_HEADER]
    while position < len(runs) and len(edges) < len(wanted):
        matched = False
        for width in range(1, 5):
            chunk = runs[position : position + width]
            if len(chunk) < width:
                break
            if _key(" ".join(run.text for run in chunk)) == wanted[len(edges)]:
                edges.append(chunk[0].x)
                position += width
                matched = True
                break
        if not matched:
            if edges:
                return None
            position += 1
    if len(edges) != len(wanted) or edges != sorted(edges):
        return None
    return edges


def _cells(line: _Line, edges: Sequence[float]) -> list[str]:
    """The line's text split into the seven columns by the header's edges."""
    boundaries = [edge - EDGE_SLACK for edge in edges]
    columns: list[list[str]] = [[] for _ in edges]
    for run in line.runs:
        index = 0
        for i, boundary in enumerate(boundaries):
            if run.x >= boundary:
                index = i
        columns[index].append(run.text)
    return [_norm(" ".join(parts)) for parts in columns]


# --------------------------------------------------------------------- pure: parsing


class _Block:
    """The running state of the table across lines and pages."""

    def __init__(self) -> None:
        self.date: date | None = None
        self.time: str | None = None
        self.matchup: tuple[str, str, str] | None = None
        self.team: str | None = None
        self.row: dict[str, Any] | None = None
        self.prev_cells: list[str] | None = None
        self.rows: list[ReportRow] = []
        self.team_rows: dict[tuple[date, str, str], int] = {}
        self.nys: set[tuple[date, str, str]] = set()
        self.unclean: set[tuple[date, str, str]] = set()
        self.global_unclean = False
        self.errors: list[str] = []
        self.dropped = 0
        self.body_lines = 0

    def context_key(self) -> tuple[date, str, str] | None:
        if self.date is None or self.matchup is None or not self.team:
            return None
        return (self.date, self.matchup[0], self.team)

    def drop(self, page: int, line: int, why: str) -> None:
        self.dropped += 1
        if len(self.errors) < MAX_ERRORS_KEPT:
            self.errors.append(f"page {page} line {line}: {why}")
        key = self.context_key()
        if key is None:
            self.global_unclean = True
        else:
            self.unclean.add(key)

    def finish_row(self) -> None:
        """Validate and store the row in progress, or drop it."""
        row, self.row = self.row, None
        if row is None:
            return
        name = _norm(row["player"])
        status_raw = row["status_raw"]
        reason = _norm(row["reason"])[:MAX_REASON_CHARS] if row["reason"] else None
        # The team and the time are read now, not when the row began: a long team name can wrap
        # onto a second line, and "07:30" can be followed by "(ET)" on the next, and each
        # continuation updates the running value before the row is finished.
        matchup, game_date, team = row["matchup"], row["date"], _norm(self.team or "")
        if (
            name.count(",") != 1
            or not all(part.strip() for part in name.split(","))
            or len(name) > 96
        ):
            self.drop(row["page"], row["line"], f"{name!r} is not a 'Last, First' name")
            return
        if game_date is None or matchup is None or not team:
            self.drop(row["page"], row["line"], "the row has no date, matchup or team")
            return
        try:
            status = normalise_status(status_raw)
        except InvalidStatusError:
            self.drop(row["page"], row["line"], f"{status_raw!r} is not a status")
            return
        assert status is not None
        if len(self.rows) >= MAX_ROWS:
            self.drop(row["page"], row["line"], "too many rows")
            return
        self.rows.append(
            ReportRow(
                page=row["page"],
                line=row["line"],
                game_date=game_date,
                game_time_raw=self.time,
                matchup=matchup[0],
                away_abbr=matchup[1],
                home_abbr=matchup[2],
                team_name=team,
                player_name_raw=name,
                status_raw=_norm(status_raw),
                status=status,
                reason_raw=reason or None,
            )
        )
        key = (game_date, matchup[0], team)
        self.team_rows[key] = self.team_rows.get(key, 0) + 1


def _update_context(block: _Block, cells: list[str], page: int, line: int) -> bool:
    """Apply the lead cells (date, time, matchup, team) to the running context.

    Returns False when a lead cell was present but unusable (the line is then dropped by the
    caller). A new matchup must come with a team on the same line: a forward-filled team would
    belong to the previous game.
    """
    date_cell, time_cell, matchup_cell, team_cell = (cells[i] for i in _LEAD_COLUMNS)
    if date_cell:
        parsed = _parse_date(date_cell)
        if parsed is None:
            block.drop(page, line, f"{date_cell!r} is not a date")
            return False
        block.date = parsed
    if time_cell:
        block.time = time_cell
    if matchup_cell:
        parsed_matchup = split_matchup(matchup_cell)
        if parsed_matchup is None:
            block.drop(page, line, f"{matchup_cell!r} is not a matchup")
            return False
        if not team_cell:
            block.drop(page, line, "a new matchup has no team")
            return False
        block.matchup = parsed_matchup
    if team_cell:
        block.team = team_cell
    return True


def _parse_body_line(block: _Block, cells: list[str], page: int, number: int) -> None:
    lead = cells[:_COL_PLAYER]
    player, status, reason = cells[_COL_PLAYER], cells[_COL_STATUS], cells[_COL_REASON]
    has_lead = any(lead)

    if any(_NYS.match(cell) for cell in cells[_COL_TEAM:]):
        block.finish_row()
        if _NYS.match(cells[_COL_TEAM]) or not (block.team or cells[_COL_TEAM]):
            block.drop(page, number, "NOT YET SUBMITTED without a team")
            return
        if not _update_context(block, cells, page, number):
            return
        key = block.context_key()
        if key is None:
            block.drop(page, number, "NOT YET SUBMITTED with no game")
            return
        block.nys.add(key)
        return

    if player and status:
        block.finish_row()
        if not _update_context(block, cells, page, number):
            return
        block.row = {
            "page": page,
            "line": number,
            "player": player,
            "status_raw": status,
            "reason": reason,
            "date": block.date,
            "matchup": block.matchup,
        }
        # Validate the status now so a bad one is dropped at its own line.
        try:
            normalise_status(status)
        except InvalidStatusError:
            block.row = None
            block.drop(page, number, f"{status!r} is not a status")
        return

    if player and not status:
        row = block.row
        if row is not None and not has_lead and row["player"].rstrip().endswith(","):
            row["player"] = f"{row['player']} {player}"
            if reason:
                row["reason"] = f"{row['reason']} {reason}".strip() if row["reason"] else reason
            return
        block.drop(page, number, "a player name with no status")
        return

    if status and not player:
        block.drop(page, number, "a status with no player")
        return

    if reason and not has_lead:
        row = block.row
        if row is None:
            block.drop(page, number, "a reason with no row to continue")
            return
        row["reason"] = f"{row['reason']} {reason}".strip() if row["reason"] else reason
        return

    if has_lead:
        # A line of lead cells only: a wrapped lead cell, or a team (or game) heading.
        previous = block.prev_cells or [""] * 7
        present = [column for column in _LEAD_COLUMNS if cells[column]]
        wrapped = [c for c in present if c in (_COL_TIME, _COL_TEAM) and previous[c]]
        if len(wrapped) == len(present) and not reason:
            # Every cell on this line continues a cell on the line above it (a long team name, or
            # "07:30" then "(ET)"). The row in progress is not finished: it is still the same row.
            for column in wrapped:
                if column == _COL_TEAM:
                    block.team = f"{block.team or ''} {cells[column]}".strip()
                else:
                    block.time = f"{block.time or ''} {cells[column]}".strip()
            return
        block.finish_row()
        _update_context(block, cells, page, number)
        # A reason on a heading line with no player is not understood.
        if reason:
            block.drop(page, number, "a reason on a line with no player")
        return

    if reason:
        block.drop(page, number, "a reason with no row to continue")


def parse_pages(pages: Sequence[Sequence[Run]]) -> ParsedReport:
    """Parse the text runs of every page. Pure: no PDF, no I/O. See the module docstring."""
    if not pages:
        return ParsedReport("headerMismatch", errors=("the document has no pages",))

    block = _Block()
    as_of: datetime | None = None
    seen_header = False

    for page_number, runs in enumerate(pages, start=1):
        lines = _group_lines(runs)
        # Page furniture first: the footer baseline, if any.
        footer_y: float | None = None
        for line in lines:
            if line.is_footer:
                footer_y = line.y
        header_index: int | None = None
        edges: list[float] | None = None
        for index, line in enumerate(lines):
            candidate = _match_header(line)
            if candidate is not None:
                header_index, edges = index, candidate
                break

        content = [
            line for line in lines if not line.is_footer and (footer_y is None or line.y > footer_y)
        ]
        if header_index is None or edges is None:
            if content:
                return ParsedReport(
                    "headerMismatch",
                    pages=len(pages),
                    errors=(
                        f"page {page_number} has text but no header row with the seven "
                        f"expected column names ({', '.join(EXPECTED_HEADER)})",
                    ),
                )
            continue  # a blank (or footer-only) page
        seen_header = True
        header_y = lines[header_index].y

        for line in lines[:header_index]:
            if as_of is None:
                as_of = _parse_as_of(line.text)

        body = [line for line in content if line.y < header_y and line is not lines[header_index]]
        for number, line in enumerate(body, start=1):
            cells = _cells(line, edges)
            if not any(cells):
                continue
            block.body_lines += 1
            _parse_body_line(block, cells, page_number, number)
            block.prev_cells = cells
        # A row can continue on the next page, so it is not finished at a page boundary.

    block.finish_row()
    if not seen_header:
        return ParsedReport(
            "headerMismatch", pages=len(pages), errors=("no page has the table header",)
        )

    understood = len(block.rows) + len(block.nys)
    total = block.body_lines
    if total == 0:
        return ParsedReport("empty", pages=len(pages), as_of_et=as_of)
    if understood == 0 or (block.dropped and block.dropped / total > MAX_DROPPED_SHARE):
        reason = (
            "the table's columns could not be read"
            if understood == 0
            else f"{block.dropped} of {total} lines could not be read; the layout is not understood"
        )
        return ParsedReport(
            "headerMismatch",
            pages=len(pages),
            as_of_et=as_of,
            body_lines=total,
            dropped=block.dropped,
            errors=(reason, *block.errors[:5]),
        )

    markers: list[TeamMarker] = []
    for key in sorted(set(block.team_rows) | block.nys):
        game_date, matchup, team = key
        has_rows, is_nys = key in block.team_rows, key in block.nys
        clean = key not in block.unclean and not block.global_unclean and not (has_rows and is_nys)
        markers.append(
            TeamMarker(
                game_date, matchup, team, "notYetSubmitted" if is_nys else "submitted", clean
            )
        )

    status = "partial" if block.dropped else "ok"
    return ParsedReport(
        status,
        rows=tuple(block.rows),
        teams=tuple(markers),
        as_of_et=as_of,
        pages=len(pages),
        body_lines=total,
        dropped=block.dropped,
        errors=tuple(block.errors),
    )


def parse_report(pdf: bytes) -> ParsedReport:
    """Parse the bytes of an injury-report PDF. Never guesses.

    A file that cannot be opened, or whose pages carry no recognisable header, is
    ``headerMismatch`` with no entries and the reason in ``errors``. Raises
    :class:`ReportParserUnavailable` (not a parse result) when ``pypdf`` is missing, because that is
    a fact about this machine, not about the report.
    """
    if not pdf.startswith(b"%PDF-"):
        return ParsedReport("headerMismatch", errors=("the file is not a PDF",))
    try:
        pages = extract_runs(pdf)
    except ReportUnreadable as exc:
        return ParsedReport("headerMismatch", errors=(str(exc),))
    return parse_pages(pages)
