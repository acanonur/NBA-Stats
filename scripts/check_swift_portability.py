#!/usr/bin/env python3
"""Lint the Swift sources for the mistakes that cost a round trip through Xcode.

There is no Swift toolchain where this app is written. The only compiler is Xcode on the owner's
Mac, and every build error it finds comes back by copy and paste. So the cheapest defence is to
catch, on Linux and in seconds, the families of error that are mechanical enough for a script to
see: a Mac-only API compiled into the iOS build (or the reverse), a ``Table`` that mixes sortable
and unsortable columns, an exhaustive ``switch`` that has not heard about a new enum case, a
payload struct that cannot decode the fixture the backend actually serves.

This is NOT a type checker and never says a file compiles. Each rule is a narrow, named
promise from ``MAC_DESIGN`` section 6.9, plus one the design did not have (``P14``). A finding
prints as ``file:line: rule: message`` and the exit status is 1, like ``check_contracts.py``.

How it reads Swift. Every file is run through a small lexer first (``lex_swift``) that blanks
comments and string contents. Rules then match on the *code* only, so a doc comment that says
"UIColor" or a button titled "Table" is not a finding, and braces inside a string cannot unbalance
a block match. Separately the lexer keeps each string literal's static text, which is what the
banned-vocabulary rule (``P9``) reads. A second pass tracks ``#if`` / ``#elseif`` / ``#else`` so
a rule can ask "is this line inside a macOS-only (or iOS-only) region?".

The rules
  P1   every ``Mac/**/*.swift`` is wrapped whole in ``#if os(macOS)`` ... ``#endif``.
  P2   AppKit and Mac-only SwiftUI names appear only in Mac files or macOS regions.
  P3   iOS-only names appear only in iOS regions (the shims' iOS branches, ``#if os(iOS)``).
  P4   macOS 15 and otherwise forbidden APIs appear nowhere.
  P5   an exhaustive switch over ``WidgetKind``, ``ConfigFieldType`` or ``APIError.Code`` that has
       no ``default`` names every case (so adding a case breaks the lint, not just the build).
  P6   every ``Table`` has at most 10 columns, every ``TableColumn`` is sortable (``value:``),
       and no ``if``/``switch``/``ForEach`` sits in a Table's column builder.
  P7   every ``Widgets/*Widget.swift`` is the widget of a real ``WidgetKind``.
  P8   no non-Swift file is added under ``ios/NBAStats/`` outside ``Resources/``.
  P9   no betting vocabulary in the string literals of the Mac and league code.
  P10  ``Core/LeaguePayloads.swift`` is all ``public struct ... Codable, Hashable, Sendable`` with
       only ``public var`` Optional stored properties, synthesized Codable, no ``Date``; and every
       ``WidgetPayload`` associated type is ``public``.
  P11  every scene root in ``Mac/MacScenes.swift`` is handed its environment objects.
  P12  the macOS plist and the Xcode project's macOS settings are present and consistent.
  P13  statistic-looking payload properties are ``Double?``, never an integer.
  P14  every key in every league fixture, and in the four new widget fixtures, is a property of
       the Swift struct that decodes it, with a compatible type. This is the rule that would have
       caught the Mac design's guessed payload shapes before they reached Xcode.

Rules that guard a file which does not exist yet (``Mac/``, ``League/``,
``Core/LeaguePayloads.swift``) pass vacuously and say so; they start to bite the moment a package
adds the file. ``--payloads FILE`` points P10, P13 and P14 at a scratch file, for dry runs.

Run from anywhere: ``python3 scripts/check_swift_portability.py [--only P1,P5] [--root DIR]``.
"""

from __future__ import annotations

import argparse
import bisect
import functools
import json
import plistlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------- findings


@dataclass(frozen=True)
class Finding:
    """One problem: which rule, where, and what to do about it."""

    rule: str
    path: str
    line: int
    message: str

    def render(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: {self.rule}: {self.message}"


# --------------------------------------------------------------------------- the Swift lexer


@dataclass(frozen=True)
class Literal:
    """A string literal's static text. Interpolations are removed, not kept."""

    start: int
    end: int
    text: str


def lex_swift(text: str) -> tuple[list[str], list[Literal]]:
    """Return ``(code_lines, literals)`` for one Swift source.

    ``code_lines`` has exactly one entry per physical line. Comments become spaces; the contents
    of string literals (and of any ``\\( ... )`` interpolation inside them) become spaces too, the
    delimiting quotes stay. Columns and line numbers are preserved, which is what lets a rule
    report the real line. Handles ``//``, nested ``/* */``, ``"..."``, ``\"\"\"...\"\"\"`` and raw
    strings with any number of ``#``.
    """
    code: list[list[str]] = [[]]
    literals: list[Literal] = []
    n = len(text)
    i = 0
    line = 1
    # Stack of contexts. ["code", None] is the file; ["code", depth] is an interpolation whose
    # parentheses are being counted; ["str", hashes, multiline, buffer, start_line] is a literal.
    stack: list[list] = [["code", None]]

    def put(ch: str) -> None:
        nonlocal line
        if ch == "\n":
            code.append([])
            line += 1
        else:
            code[-1].append(ch)

    while i < n:
        ctx = stack[-1]
        ch = text[i]
        if ctx[0] == "code":
            quiet = ctx[1] is not None  # inside an interpolation: lex it, but do not emit it
            nxt = text[i + 1] if i + 1 < n else ""
            if ch == "/" and nxt == "/":
                while i < n and text[i] != "\n":
                    put(" ")
                    i += 1
                continue
            if ch == "/" and nxt == "*":
                depth = 0
                while i < n:
                    if text.startswith("/*", i):
                        depth += 1
                        put(" ")
                        put(" ")
                        i += 2
                    elif text.startswith("*/", i):
                        depth -= 1
                        put(" ")
                        put(" ")
                        i += 2
                        if depth == 0:
                            break
                    else:
                        put("\n" if text[i] == "\n" else " ")
                        i += 1
                continue
            if ch in ('"', "#"):
                j = i
                while j < n and text[j] == "#":
                    j += 1
                if j < n and text[j] == '"':
                    hashes = j - i
                    multiline = text.startswith('"""', j)
                    quote = '"""' if multiline else '"'
                    for c in "#" * hashes + quote:
                        put(" " if quiet else c)
                    i = j + len(quote)
                    stack.append(["str", hashes, multiline, [], line])
                    continue
            if quiet:
                if ch == "(":
                    ctx[1] += 1
                elif ch == ")":
                    ctx[1] -= 1
                    if ctx[1] == 0:
                        stack.pop()
                put("\n" if ch == "\n" else " ")
            else:
                put(ch)
            i += 1
            continue

        _, hashes, multiline, buf, start = ctx
        hs = "#" * hashes
        terminator = ('"""' if multiline else '"') + hs
        if text.startswith(terminator, i):
            outer_quiet = stack[-2][0] == "code" and stack[-2][1] is not None
            for c in terminator:
                put(" " if outer_quiet else c)
            i += len(terminator)
            stack.pop()
            literals.append(Literal(start, line, "".join(buf)))
            continue
        if ch == "\\" and text.startswith("\\" + hs, i):
            k = i + 1 + hashes
            if k < n and text[k] == "(":
                buf.append(" ")
                for _ in range(k + 1 - i):
                    put(" ")
                i = k + 1
                stack.append(["code", 1])
                continue
            for _ in range(k - i):
                put(" ")
            i = k
            if i < n:
                put("\n" if text[i] == "\n" else " ")
                buf.append(" " if text[i] in "nrt0\\\"'" else text[i])
                i += 1
            continue
        if ch == "\n" and not multiline:
            stack.pop()  # an unterminated literal: recover at the end of the line
            literals.append(Literal(start, line, "".join(buf)))
            continue
        buf.append(ch)
        put("\n" if ch == "\n" else " ")
        i += 1

    return ["".join(row) for row in code], literals


# --------------------------------------------------------------------------- #if regions

DIRECTIVE = re.compile(r"^\s*#(if|elseif|else|endif)\b(.*)$")
_MAC_COND = re.compile(r"\bos\(macOS\)|\bcanImport\(AppKit\)")
_IOS_COND = re.compile(r"\bos\(iOS\)|\bcanImport\(UIKit\)")


def classify_condition(expression: str) -> str | None:
    """``"mac"``, ``"ios"`` or ``None`` for the platform an ``#if`` expression selects."""
    expression = expression.strip()
    has_mac = bool(_MAC_COND.search(expression))
    has_ios = bool(_IOS_COND.search(expression))
    if has_mac == has_ios:
        return None
    platform = "mac" if has_mac else "ios"
    if expression.startswith("!"):
        platform = "ios" if platform == "mac" else "mac"
    return platform


@dataclass
class _Frame:
    covered: set[str] = field(default_factory=set)
    current: str | None = None


def preprocess(code: list[str]) -> tuple[list[str | None], list[int], list[tuple[int, str]]]:
    """Per line: the platform region it is in, the ``#if`` depth after it, and any imbalance.

    A ``#else`` takes the complement of the platforms its chain has covered, so
    ``#if canImport(UIKit) ... #else ...`` makes the ``#else`` a macOS region, and
    ``#if os(macOS) ... #else ...`` makes it an iOS one.
    """
    stack: list[_Frame] = []
    regions: list[str | None] = []
    depths: list[int] = []
    problems: list[tuple[int, str]] = []

    def region() -> str | None:
        for frame in reversed(stack):
            if frame.current:
                return frame.current
        return None

    for index, text in enumerate(code):
        regions.append(region())
        match = DIRECTIVE.match(text)
        if match:
            kind, rest = match.groups()
            if kind == "if":
                platform = classify_condition(rest)
                stack.append(_Frame({platform} if platform else set(), platform))
            elif not stack:
                problems.append((index + 1, f"#{kind} with no matching #if"))
            elif kind == "elseif":
                platform = classify_condition(rest)
                stack[-1].current = platform
                if platform:
                    stack[-1].covered.add(platform)
            elif kind == "else":
                covered = stack[-1].covered
                stack[-1].current = (
                    "ios" if covered == {"mac"} else "mac" if covered == {"ios"} else None
                )
            else:
                stack.pop()
        depths.append(len(stack))
    if stack:
        problems.append((len(code), f"{len(stack)} #if block(s) never closed"))
    return regions, depths, problems


# --------------------------------------------------------------------------- source files


@dataclass
class SwiftFile:
    """One Swift source: raw lines, code-only lines, literals and #if regions."""

    path: Path
    rel: str
    area: str
    raw: list[str]
    code: list[str]
    literals: list[Literal]
    region: list[str | None]
    depth: list[int]
    problems: list[tuple[int, str]]

    @functools.cached_property
    def code_text(self) -> str:
        return "\n".join(self.code)

    @functools.cached_property
    def newline_offsets(self) -> list[int]:
        return [m.start() for m in re.finditer("\n", self.code_text)]

    def line_of(self, offset: int) -> int:
        """1-based line number of a character offset into ``code_text``."""
        return bisect.bisect_left(self.newline_offsets, offset) + 1


def load_swift(path: Path, root: Path, base: Path) -> SwiftFile:
    raw_text = path.read_text(encoding="utf-8")
    code, literals = lex_swift(raw_text)
    raw = raw_text.split("\n")
    while len(code) < len(raw):
        code.append("")
    region, depth, problems = preprocess(code)
    try:
        area = path.relative_to(base).as_posix()
    except ValueError:
        area = path.name  # a scratch file passed with --payloads, outside the app folder
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = str(path)
    return SwiftFile(
        path=path, rel=rel, area=area, raw=raw, code=code,
        literals=literals, region=region, depth=depth, problems=problems,
    )


def match_close(text: str, open_index: int) -> int:
    """Index of the bracket closing the one at ``open_index`` (``-1`` when unbalanced)."""
    pairs = {"{": "}", "(": ")", "[": "]"}
    opener = text[open_index]
    closer = pairs[opener]
    depth = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return index
    return -1


def split_top_level(text: str, separator: str = ",") -> list[str]:
    """Split on ``separator`` outside any (), [] or {}."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == separator and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


class Project:
    """The tree being linted. Paths are resolved once so rules never build them by hand."""

    def __init__(self, root: Path, payloads: Path | None = None) -> None:
        self.root = root
        self.ios = root / "ios"
        self.app = self.ios / "NBAStats"
        self.tests = self.ios / "NBAStatsTests"
        self.payloads = payloads or self.app / "Core" / "LeaguePayloads.swift"
        self._cache: dict[Path, SwiftFile] = {}
        #: Rules that had nothing to look at (a file no package has written yet) say so here, so
        #: a green run is never mistaken for a rule that was exercised.
        self.notes: dict[str, str] = {}

    def file(self, path: Path) -> SwiftFile:
        if path not in self._cache:
            base = self.tests if self.tests in path.parents else self.app
            self._cache[path] = load_swift(path, self.root, base)
        return self._cache[path]

    def swift(self, *folders: Path) -> list[SwiftFile]:
        found: list[SwiftFile] = []
        for folder in folders:
            if folder.is_dir():
                found.extend(self.file(p) for p in sorted(folder.rglob("*.swift")))
        return found

    def all_swift(self) -> list[SwiftFile]:
        return self.swift(self.app, self.tests)

    def mac_files(self) -> list[SwiftFile]:
        return self.swift(self.app / "Mac")

    def is_new_code(self, f: SwiftFile) -> bool:
        """True for the files this feature adds: held to the stricter concurrency rules."""
        return (
            f.area.startswith(("Mac/", "League/"))
            or re.match(r"(Core|Networking)/League\w*\.swift$", f.area) is not None
            or f.area.startswith("DesignSystem/Platform")
            or f.area == "Dashboard/ClubFieldEditor.swift"
            or f.area in FOUR_WIDGET_FILES
        )


FOUR_WIDGET_FILES = frozenset(
    f"Widgets/{name}Widget.swift"
    for name in ("TeamMatchup", "DefenseByPosition", "AvailabilityReport", "SlateProjections")
)

# --------------------------------------------------------------------------- shared helpers


def scan_lines(
    files: Iterable[SwiftFile],
    rule: str,
    patterns: list[tuple[re.Pattern[str], str]],
    skip_region: str | None = None,
    skip: Callable[[SwiftFile, re.Match[str]], bool] | None = None,
) -> list[Finding]:
    """Match code-only lines against ``patterns``.

    ``skip_region`` ignores lines inside that region, so every line OUTSIDE it is checked: P2
    skips the macOS region (a Mac-only name anywhere else is a finding) and P3 skips the iOS one.
    """
    findings: list[Finding] = []
    for f in files:
        for index, text in enumerate(f.code):
            if not text.strip() or DIRECTIVE.match(text):
                continue
            region = f.region[index]
            if skip_region is not None and region == skip_region:
                continue
            for pattern, message in patterns:
                for match in pattern.finditer(text):
                    if skip and skip(f, match):
                        continue
                    shown = message.replace("{m}", match.group(0))
                    findings.append(Finding(rule, f.rel, index + 1, shown))
    return findings


def _shown(project: Project, path: Path) -> str:
    try:
        return path.relative_to(project.root).as_posix()
    except ValueError:
        return str(path)


def camel_tokens(name: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name)]


# --------------------------------------------------------------------------- P1


def rule_p1(project: Project) -> list[Finding]:
    """Mac files are wholly fenced: the synchronized folder compiles every file for iOS too."""
    findings: list[Finding] = []
    if not project.mac_files():
        project.notes["P1"] = "no files under ios/NBAStats/Mac/ yet (the fence check is idle)"
    for f in project.mac_files():
        non_blank = [i for i, text in enumerate(f.code) if text.strip()]
        if not non_blank:
            findings.append(Finding("P1", f.rel, 0, "file has no code; delete it or fence it"))
            continue
        first, last = non_blank[0], non_blank[-1]
        if not re.fullmatch(r"\s*#if\s+os\(macOS\)\s*", f.code[first]):
            findings.append(Finding(
                "P1", f.rel, first + 1,
                "the first non-comment line must be `#if os(macOS)` (every Mac file is fenced "
                "whole, or the iOS build compiles it)"))
        elif any(f.depth[i] < 1 for i in range(first, last)):
            findings.append(Finding(
                "P1", f.rel, first + 1,
                "the opening `#if os(macOS)` is closed before the end of the file; one #if must "
                "wrap the whole file"))
        if f.code[last].strip() != "#endif":
            findings.append(Finding(
                "P1", f.rel, last + 1, "the last non-comment line must be the closing `#endif`"))
    # Every other rule trusts the #if regions, so an unbalanced #if anywhere is a P1 finding.
    for f in project.all_swift():
        for line, message in f.problems:
            findings.append(Finding("P1", f.rel, line, message))
    return findings


# --------------------------------------------------------------------------- P2

#: Foundation types that begin with "NS". Everything else that does is AppKit (or unknown, which
#: is treated as AppKit until someone adds it here on purpose).
FOUNDATION_NS = frozenset("""
NSArray NSAttributedString NSBundle NSCache NSCalendar NSCoder NSCoding NSCopying NSData NSDate
NSDateComponents NSDateFormatter NSDecimalNumber NSDictionary NSError NSFileCoordinator
NSFileManager NSHomeDirectory NSIndexPath NSIndexSet NSItemProvider NSKeyedArchiver
NSKeyedUnarchiver NSLocale NSLocalizedString NSLock NSLog NSMutableArray NSMutableAttributedString
NSMutableData NSMutableDictionary NSMutableSet NSMutableString NSNotFound NSNotification
NSNotificationCenter NSNull NSNumber NSNumberFormatter NSObject NSObjectProtocol NSOperation
NSOperationQueue NSPredicate NSRange NSRecursiveLock NSRegularExpression NSSecureCoding NSSet
NSSortDescriptor NSString NSTemporaryDirectory NSThread NSTimeZone NSUbiquitousKeyValueStore
NSUndoManager NSURL NSURLComponents NSURLRequest NSURLSession NSUUID NSUserActivity NSUserDefaults
NSValue
""".split())

P2_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bimport\s+AppKit\b"), "`import AppKit` outside a macOS region"),
    (re.compile(r"(?<![\w.])Table\s*\("), "SwiftUI `Table(` is macOS-only here (iOS 17 compact "
                                           "tables are not used)"),
    (re.compile(r"(?<![\w.])TableColumn\s*\("), "`TableColumn(` is macOS-only here"),
    (re.compile(r"\.inspector\s*\("), "`.inspector(` is macOS-only here"),
    (re.compile(r"\bHSplitView\b"), "`HSplitView` does not exist on iOS"),
    (re.compile(r"(?<![\w.])Window\s*\("), "`Window(` scene does not exist on iOS"),
    (re.compile(r"(?<![\w.])Settings\s*\{"), "`Settings {` scene does not exist on iOS"),
    (re.compile(r"\bopenWindow\b"), "`openWindow` does not exist on iOS"),
    (re.compile(r"\.navigationSubtitle\b"), "`.navigationSubtitle` does not exist on iOS"),
    (re.compile(r"\.onDeleteCommand\b"), "`.onDeleteCommand` does not exist on iOS"),
    (re.compile(r"\.datePickerStyle\s*\(\s*\.field\s*\)"),
     "`.datePickerStyle(.field)` does not exist on iOS"),
]
_NS_NAME = re.compile(r"(?<![\w.])NS[A-Z]\w*")


def rule_p2(project: Project) -> list[Finding]:
    """AppKit and Mac-only SwiftUI belong in Mac files or macOS regions, nowhere else."""
    files = [f for f in project.all_swift() if not f.area.startswith("Mac/")]
    findings = scan_lines(files, "P2", P2_PATTERNS, skip_region="mac")
    findings += scan_lines(
        files, "P2", [(_NS_NAME, "`{m}` is an AppKit name outside a macOS region")],
        skip_region="mac", skip=lambda f, m: m.group(0) in FOUNDATION_NS)
    return findings


# --------------------------------------------------------------------------- P3

P3_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p), f"iOS-only `{label}` outside an iOS region: use the PlatformShims method or "
                    "wrap it in `#if os(iOS)`")
    for p, label in [
        (r"\.navigationBarTitleDisplayMode\b", ".navigationBarTitleDisplayMode"),
        (r"\.topBarLeading\b", ".topBarLeading"),
        (r"\.topBarTrailing\b", ".topBarTrailing"),
        (r"\.textInputAutocapitalization\b", ".textInputAutocapitalization"),
        (r"\.keyboardType\b", ".keyboardType"),
        (r"\.textContentType\b", ".textContentType"),
        (r"\.insetGrouped\b", ".insetGrouped"),
        (r"\bEditButton\b", "EditButton"),
        (r"\bEditMode\b", "EditMode"),
        (r"\\\.editMode\b", "\\.editMode"),
        (r"\.tabViewStyle\s*\(\s*\.page", ".tabViewStyle(.page"),
        (r"\.indexViewStyle\b", ".indexViewStyle"),
        (r"\.navigationLink\b", ".navigationLink picker style"),
        (r"\bhorizontalSizeClass\b", "horizontalSizeClass"),
        (r"\bUserInterfaceSizeClass\b", "UserInterfaceSizeClass"),
        (r"\bUI[A-Z]\w*", "a UIKit name"),
        (r"\bBG[A-Z]\w+", "a BackgroundTasks name"),
        (r"\bBackgroundTasks\b", "BackgroundTasks"),
        (r"\.fullScreenCover\b", ".fullScreenCover"),
    ]
]


def rule_p3(project: Project) -> list[Finding]:
    """iOS-only spellings stay inside iOS regions; the shims are where they live."""
    return scan_lines(project.all_swift(), "P3", P3_PATTERNS, skip_region="ios")


# --------------------------------------------------------------------------- P4

P4_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p), f"`{label}` is above the macOS 14 / Swift 5 floor or on the denylist (6.3)")
    for p, label in [
        (r"@Observable\b", "@Observable"), (r"@Bindable\b", "@Bindable"),
        (r"@Entry\b", "@Entry"), (r"@Previewable\b", "@Previewable"),
        (r"\bimport\s+Testing\b", "import Testing"),
        (r"\bimport\s+Observation\b", "import Observation"),
        (r"(?<![\w.])Tab\s*\(", "Tab("), (r"\.sidebarAdaptable\b", ".sidebarAdaptable"),
        (r"\.presentationSizing\b", ".presentationSizing"), (r"\.pointerStyle\b", ".pointerStyle"),
        (r"\.windowStyle\s*\(\s*\.plain", ".windowStyle(.plain)"),
        (r"\.containerBackground\s*\(\s*for:\s*\.window", ".containerBackground(for: .window)"),
        (r"\.onScrollGeometryChange\b", ".onScrollGeometryChange"),
        (r"\.defaultLaunchBehavior\b", ".defaultLaunchBehavior"),
        (r"\.searchFocused\b", ".searchFocused"), (r"\bMeshGradient\b", "MeshGradient"),
        (r"\bregisterUndo\b", "registerUndo"), (r"\bTableColumnForEach\b", "TableColumnForEach"),
        (r"\bTableColumnCustomization\b", "TableColumnCustomization"),
        (r"\.customizationID\b", ".customizationID"), (r"\bMenuBarExtra\b", "MenuBarExtra"),
        (r"\.scrollPosition\b", ".scrollPosition"), (r"\.chartXSelection\b", ".chartXSelection"),
        (r"\bonKeyPress\b", "onKeyPress"), (r"\.focusedValue\b", ".focusedValue"),
        (r"\.borderlessButton\b", ".menuStyle(.borderlessButton)"),
    ]
]
#: Denied in new code only: the existing app already uses ``Task.detached`` (DashboardService).
P4_NEW_CODE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bTask\.detached\b"), "`Task.detached` is on the denylist (6.3): use `.task`"),
    (re.compile(r"\.sink\s*[({]"), "Combine `.sink` into main-actor code is on the denylist "
                                    "(6.3): use `.onReceive`"),
]


def rule_p4(project: Project) -> list[Finding]:
    """Nothing above macOS 14, and none of the tempting-but-risky APIs, anywhere."""
    files = project.all_swift()
    findings = scan_lines(files, "P4", P4_PATTERNS)
    findings += scan_lines([f for f in files if project.is_new_code(f)], "P4", P4_NEW_CODE_PATTERNS)
    return findings


# --------------------------------------------------------------------------- P5

_CASE_NAME = re.compile(r"^(?:[A-Za-z_]\w*)?\.`?([A-Za-z_]\w*)`?")


def enum_cases(project: Project, relative: str, enum_name: str) -> list[str]:
    """Case names of ``enum <enum_name>`` as written in ``relative`` (under ios/NBAStats)."""
    f = project.file(project.app / relative)
    text = f.code_text
    match = re.search(r"\benum\s+" + re.escape(enum_name) + r"\b[^{]*\{", text)
    if not match:
        return []
    open_index = match.end() - 1
    close_index = match_close(text, open_index)
    body = text[open_index + 1:close_index]
    names: list[str] = []
    depth = 0
    for row in body.split("\n"):
        stripped = row.strip()
        if depth == 0 and stripped.startswith("case "):
            for item in split_top_level(stripped[len("case "):]):
                name = re.match(r"\s*`?([A-Za-z_]\w*)`?", item)
                if name:
                    names.append(name.group(1))
        depth += row.count("{") - row.count("}")
    return names


@dataclass
class SwitchBlock:
    line: int
    labels: set[str]
    has_default: bool


def parse_switches(f: SwiftFile) -> list[SwitchBlock]:
    """Every ``switch`` in a file with the case names its own top-level labels use."""
    text = f.code_text
    blocks: list[SwitchBlock] = []
    for match in re.finditer(r"(?<![\w.])switch\b", text):
        depth = 0
        index = match.end()
        while index < len(text):
            char = text[index]
            if char in "([":
                depth += 1
            elif char in ")]":
                depth -= 1
            elif char == "{" and depth == 0:
                break
            index += 1
        close = match_close(text, index) if index < len(text) else -1
        if close < 0:
            continue
        body = text[index + 1:close]
        labels: set[str] = set()
        has_default = False
        depth = 0
        line_start = 0
        for row in body.split("\n"):
            stripped = row.strip()
            if depth == 0 and re.match(r"default\s*:", stripped):
                has_default = True
            elif depth == 0 and re.match(r"case\b", stripped):
                start = line_start + row.index("case") + len("case")
                labels |= _label_names(body, start)
            line_start += len(row) + 1
            depth += row.count("{") - row.count("}")
        blocks.append(SwitchBlock(f.line_of(match.start()), labels, has_default))
    return blocks


def _label_names(body: str, start: int) -> set[str]:
    """Case names in one ``case`` label: from ``start`` to its top-level colon, possibly on
    later lines (``case .a,\n .b:``). A ``where`` clause is dropped."""
    end = start
    nest = 0
    while end < len(body):
        char = body[end]
        if char in "([{":
            nest += 1
        elif char in ")]}":
            nest -= 1
        elif char == ":" and nest == 0:
            break
        end += 1
    names: set[str] = set()
    for pattern in split_top_level(re.split(r"\bwhere\b", body[start:end])[0]):
        named = _CASE_NAME.match(re.sub(r"^\s*(let|var)\s+", "", pattern).strip())
        if named:
            names.add(named.group(1))
    return names


#: (enum, file holding it, anchor case that marks a switch as "over this enum").
P5_ENUMS = [
    ("WidgetKind", "Core/DashboardLayout.swift", "careerArc"),
    ("ConfigFieldType", "Core/Catalog.swift", "teamList"),
    ("Code", "Networking/APIError.swift", "upstreamUnavailable"),
]


def rule_p5(project: Project) -> list[Finding]:
    """An exhaustive switch must name every case, so a new case cannot be silently skipped."""
    findings: list[Finding] = []
    files = project.all_swift()
    for enum_name, relative, anchor in P5_ENUMS:
        if not (project.app / relative).is_file():
            findings.append(Finding("P5", f"ios/NBAStats/{relative}", 0, "enum file is missing"))
            continue
        cases = enum_cases(project, relative, enum_name)
        if anchor not in cases:
            findings.append(Finding(
                "P5", f"ios/NBAStats/{relative}", 0,
                f"could not read enum {enum_name}: anchor case `.{anchor}` not among {cases}"))
            continue
        for f in files:
            for block in parse_switches(f):
                if anchor not in block.labels or block.has_default:
                    continue
                missing = [c for c in cases if c not in block.labels]
                if missing:
                    findings.append(Finding(
                        "P5", f.rel, block.line,
                        f"switch names `.{anchor}` and has no `default`, but not these "
                        f"{enum_name} cases: {', '.join('.' + c for c in missing)}"))
    return findings


# --------------------------------------------------------------------------- P6

_TABLE_CALL = re.compile(r"(?<![\w.])Table\s*\(")
_COLUMN_CALL = re.compile(r"(?<![\w.])TableColumn\s*\(")
_BUILDER_CONDITIONAL = re.compile(
    r"(?<![\w.])(if|else|switch|for|guard|ForEach|Group|TableColumnForEach)\b|^\s*#if\b", re.M)


def rule_p6(project: Project) -> list[Finding]:
    """Tables: at most 10 columns, every column sortable, no conditionals in the builder."""
    findings: list[Finding] = []
    for f in project.all_swift():
        text = f.code_text
        for match in _COLUMN_CALL.finditer(text):
            close = match_close(text, match.end() - 1)
            arguments = text[match.end():close] if close > 0 else ""
            if not re.search(r"\bvalue\s*:", arguments):
                findings.append(Finding(
                    "P6", f.rel, f.line_of(match.start()),
                    "`TableColumn` without `value:`: a column that is not sortable cannot sit "
                    "beside sortable ones (6.3), so every column needs a sort key"))
        for match in _TABLE_CALL.finditer(text):
            paren_close = match_close(text, match.end() - 1)
            if paren_close < 0:
                continue
            brace = re.match(r"\s*\{", text[paren_close + 1:])
            if brace:
                open_index = paren_close + 1 + brace.end() - 1
            else:
                inner = text[match.end():paren_close]
                labelled = re.search(r"\bcolumns\s*:\s*\{", inner)
                if not labelled:
                    continue
                open_index = match.end() + labelled.end() - 1
            close_index = match_close(text, open_index)
            if close_index < 0:
                continue
            body = text[open_index + 1:close_index]
            line = f.line_of(match.start())
            count = len(_COLUMN_CALL.findall(body))
            if count > 10:
                findings.append(Finding(
                    "P6", f.rel, line,
                    f"`Table` has {count} columns; the builder limit is 10 (split it into two "
                    "Tables, one per column set)"))
            top_level = _top_level_text(body)
            for bad in _BUILDER_CONDITIONAL.finditer(top_level):
                findings.append(Finding(
                    "P6", f.rel, line,
                    f"`{bad.group(0).strip()}` inside a Table column builder: conditionals are "
                    "not available there at macOS 14.0; use one concrete Table per column set"))
                break
    return findings


def _top_level_text(body: str) -> str:
    """``body`` with every nested ``{ ... }`` removed, so only the builder's own lines remain."""
    out: list[str] = []
    depth = 0
    for char in body:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif depth == 0:
            out.append(char)
    return "".join(out)


# --------------------------------------------------------------------------- P7


def widget_kind_raw_values(project: Project) -> set[str]:
    """The ``"snake_case"`` raw values of ``enum WidgetKind`` in Core/DashboardLayout.swift."""
    f = project.file(project.app / "Core" / "DashboardLayout.swift")
    match = re.search(r"\benum\s+WidgetKind\b[^{]*\{", f.code_text)
    if not match:
        return set()
    close = match_close(f.code_text, match.end() - 1)
    # The raw values are string literals, which the lexer blanks in `code`: read the raw lines.
    lines = f.raw[f.line_of(match.start()) - 1:f.line_of(close)]
    return set(re.findall(r'^\s*case\s+\w+\s*=\s*"([a-z_]+)"', "\n".join(lines), re.M))


def rule_p7(project: Project) -> list[Finding]:
    """A file named ``XWidget.swift`` must be the widget of a kind the catalog knows."""
    folder = project.app / "Widgets"
    if not folder.is_dir():
        return []
    kinds = widget_kind_raw_values(project)
    findings: list[Finding] = []
    if not kinds:
        return [Finding("P7", "ios/NBAStats/Core/DashboardLayout.swift", 0,
                        "could not read the WidgetKind raw values")]
    for path in sorted(folder.glob("*Widget.swift")):
        stem = path.name[: -len("Widget.swift")]
        kind = re.sub(r"(?<!^)(?=[A-Z])", "_", stem).lower()
        if kind not in kinds:
            findings.append(Finding(
                "P7", path.relative_to(project.root).as_posix(), 0,
                f"`{path.name}` implies widget kind `{kind}`, which is not a WidgetKind raw "
                "value; only a widget file may end in `Widget.swift`"))
    return findings


# --------------------------------------------------------------------------- P8


def rule_p8(project: Project) -> list[Finding]:
    """Everything under ios/NBAStats/ outside Resources/ is compiled or bundled: Swift only."""
    if not project.app.is_dir():
        return []
    findings: list[Finding] = []
    for path in sorted(project.app.rglob("*")):
        if not path.is_file() or path.suffix == ".swift" or path.name == ".DS_Store":
            continue
        relative = path.relative_to(project.app).as_posix()
        if relative.startswith("Resources/") or relative == "Widgets/PENDING.txt":
            continue
        findings.append(Finding(
            "P8", path.relative_to(project.root).as_posix(), 0,
            "non-Swift file outside Resources/: the synchronized folder would bundle it into the "
            "app (put docs in docs/ or ios/, data in Resources/)"))
    return findings


# --------------------------------------------------------------------------- P9

#: Betting vocabulary. A product that "has no betting features" must not say these words in any
#: string a reader can see. Whole words, case-insensitive.
BANNED_WORDS = frozenset("""
line lines odds over under lean edge pick picks push implied cover vig juice stake wager
bookmaker market parlay handicap ats probability spread moneyline total
""".split())
ALLOW_PRAGMA = "// portability: allow-word"
_IDENTIFIER_LITERAL = re.compile(r"^[a-z0-9]+(\.[a-z0-9]+)+$")


def rule_p9(project: Project) -> list[Finding]:
    """No betting vocabulary in string literals of the Mac, league and four-widget files."""
    files = [
        f for f in project.all_swift()
        if f.area.startswith(("Mac/", "League/")) or f.area in FOUR_WIDGET_FILES
    ]
    findings: list[Finding] = []
    if not files:
        project.notes["P9"] = "no Mac/, League/ or league-widget files yet (nothing to read)"
    for f in files:
        for literal in f.literals:
            if _IDENTIFIER_LITERAL.match(literal.text.strip()):
                continue  # an SF Symbol name or a defaults key, not words a reader sees
            if f.raw[literal.end - 1].rstrip().endswith(ALLOW_PRAGMA):
                continue
            words = {w.lower() for w in re.findall(r"[A-Za-z]+", literal.text)}
            hit = sorted(words & BANNED_WORDS)
            if hit:
                findings.append(Finding(
                    "P9", f.rel, literal.start,
                    f"string literal contains {', '.join(hit)}: no betting vocabulary in the app "
                    f"(reword it; `{ALLOW_PRAGMA}` is the reviewed escape hatch)"))
    return findings


# --------------------------------------------------------------------------- Swift struct index

_TYPE_DECL = re.compile(
    r"(?<![\w.])(?P<mods>(?:(?:public|internal|private|fileprivate|open|final)\s+)*)"
    r"(?P<kind>struct|enum|class|actor|protocol)\s+(?P<name>[A-Za-z_]\w*)(?P<rest>[^{;]*)\{")
_PROPERTY = re.compile(
    r"^(?:@\w+(?:\([^)]*\))?\s+)*"
    r"(?P<access>(?:public|internal|private|fileprivate|open)(?:\(set\))?\s+)?"
    r"(?P<static>(?:static|class)\s+)?(?P<kw>var|let)\s+(?P<name>`?[A-Za-z_]\w*`?)\s*:\s*"
    r"(?P<type>[^={]+?)\s*(?:=\s*.+)?$")


@dataclass
class Property:
    name: str
    type: str
    access: str
    keyword: str
    line: int


@dataclass
class StructInfo:
    name: str
    file: SwiftFile
    line: int
    access: str
    kind: str
    conformances: list[str]
    properties: list[Property]
    custom_codable: bool


def parse_types(f: SwiftFile) -> list[StructInfo]:
    """Every type declaration in ``f`` with its direct, non-static stored properties."""
    text = f.code_text
    found: list[StructInfo] = []
    for match in _TYPE_DECL.finditer(text):
        open_index = match.end() - 1
        close_index = match_close(text, open_index)
        if close_index < 0:
            continue
        body = text[open_index + 1:close_index]
        first_line = f.line_of(open_index)
        properties: list[Property] = []
        depth = 0
        for offset, row in enumerate(body.split("\n")):
            stripped = row.strip()
            if depth == 0 and stripped and "{" not in stripped:
                prop = _PROPERTY.match(stripped)
                if prop and not prop.group("static"):
                    properties.append(Property(
                        name=prop.group("name").strip("`"),
                        type=prop.group("type").strip(),
                        access=(prop.group("access") or "").strip(),
                        keyword=prop.group("kw"),
                        line=first_line + offset,
                    ))
            depth += row.count("{") - row.count("}")
        rest = match.group("rest").strip()
        conformances: list[str] = []
        if rest.startswith(":"):
            clause = re.split(r"\bwhere\b", rest[1:])[0]
            conformances = [c.strip() for c in clause.split(",") if c.strip()]
        mods = match.group("mods")
        found.append(StructInfo(
            name=match.group("name"), file=f, line=f.line_of(match.start()),
            access="public" if "public" in mods else "open" if "open" in mods else "internal",
            kind=match.group("kind"), conformances=conformances, properties=properties,
            custom_codable=bool(re.search(r"\binit\s*\(\s*from\b|\benum\s+CodingKeys\b", body)),
        ))
    return found


def build_index(project: Project, extra: Path | None = None) -> dict[str, StructInfo]:
    """Type name -> declaration, across the app, plus ``extra`` (the payloads file)."""
    index: dict[str, StructInfo] = {}
    files = project.swift(project.app)
    if extra is not None and extra.is_file() and extra not in {f.path for f in files}:
        files.append(project.file(extra))
    for f in files:
        for info in parse_types(f):
            index.setdefault(info.name, info)
    return index


# --------------------------------------------------------------------------- P10 / P13

STAT_TOKENS = frozenset(
    "points pct index share rate average minutes chance age attendance".split())
#: Whole-number facts whose names look like statistics but which the backend writes as ints.
INTEGER_FACTS = frozenset({"regulationMinutes"})
_REQUIRED_CONFORMANCES = ("Codable", "Hashable", "Sendable")


def payload_structs(project: Project) -> list[StructInfo]:
    if not project.payloads.is_file():
        return []
    return parse_types(project.file(project.payloads))


def rule_p10(project: Project) -> list[Finding]:
    """The league payload types decode anything the server sends, and are visible to the app."""
    findings: list[Finding] = []
    if not project.payloads.is_file():
        project.notes["P10"] = f"{_shown(project, project.payloads)} does not exist yet"
    else:
        f = project.file(project.payloads)
        for info in parse_types(f):
            where = (f.rel, info.line)
            if info.kind != "struct":
                findings.append(Finding("P10", *where, f"`{info.name}` must be a struct"))
                continue
            if info.access != "public":
                findings.append(Finding("P10", *where, f"`{info.name}` must be `public`"))
            missing = [c for c in _REQUIRED_CONFORMANCES if c not in info.conformances]
            if missing:
                findings.append(Finding(
                    "P10", *where, f"`{info.name}` must conform to Codable, Hashable, Sendable "
                    f"(missing {', '.join(missing)})"))
            if info.custom_codable:
                findings.append(Finding(
                    "P10", *where, f"`{info.name}` hand-writes Codable (init(from:) or "
                    "CodingKeys): payloads use synthesized Codable only"))
            for prop in info.properties:
                at = (f.rel, prop.line)
                if prop.access != "public" or prop.keyword != "var":
                    findings.append(Finding(
                        "P10", *at, f"`{info.name}.{prop.name}` must be `public var`"))
                if not prop.type.endswith("?"):
                    findings.append(Finding(
                        "P10", *at, f"`{info.name}.{prop.name}: {prop.type}` must be Optional: "
                        "one missing key must not cost the whole payload"))
                if re.search(r"\bDate\b", prop.type):
                    findings.append(Finding(
                        "P10", *at, f"`{info.name}.{prop.name}` is a Date: timestamps are "
                        "`String?` (the shared decoder's date strategy throws)"))
    findings += _widget_payload_visibility(project)
    return findings


def _widget_payload_visibility(project: Project) -> list[Finding]:
    """Every ``WidgetPayload`` case's associated type is ``public`` (a public enum needs it)."""
    path = project.app / "Core" / "Payloads.swift"
    if not path.is_file():
        return []
    f = project.file(path)
    match = re.search(r"\benum\s+WidgetPayload\b[^{]*\{", f.code_text)
    if not match:
        return []
    body = f.code_text[match.end():match_close(f.code_text, match.end() - 1)]
    associated = set(re.findall(r"^\s*case\s+\w+\(\s*([A-Za-z_]\w*)\s*\)", body, re.M))
    index = build_index(project, project.payloads)
    findings: list[Finding] = []
    for name in sorted(associated):
        info = index.get(name)
        if info is not None and info.access not in ("public", "open"):
            findings.append(Finding(
                "P10", info.file.rel, info.line,
                f"`{name}` is the payload of a WidgetPayload case, so it must be `public`"))
    return findings


def rule_p13(project: Project) -> list[Finding]:
    """A statistic is a Double: a ``6.0`` where an Int is expected throws and costs the payload."""
    findings: list[Finding] = []
    if not project.payloads.is_file():
        project.notes["P13"] = f"{_shown(project, project.payloads)} does not exist yet"
    for info in payload_structs(project):
        for prop in info.properties:
            if not STAT_TOKENS & set(camel_tokens(prop.name)) or prop.name in INTEGER_FACTS:
                continue
            base = prop.type.rstrip("?").strip()
            if base in {"Int", "Int32", "Int64", "UInt", "Float", "CGFloat", "String", "Bool"}:
                findings.append(Finding(
                    "P13", info.file.rel, prop.line,
                    f"`{info.name}.{prop.name}: {prop.type}` looks like a statistic and must be "
                    "`Double?` (an Int throws on `6.0`; list genuine whole numbers in "
                    "INTEGER_FACTS)"))
    return findings


# --------------------------------------------------------------------------- P11

_SCENES = [
    ("Window", re.compile(r"(?<![\w.])Window\s*\("), True),
    ("WindowGroup", re.compile(r"(?<![\w.])WindowGroup\s*\("), False),
    ("Settings", re.compile(r"(?<![\w.])Settings\s*\{"), True),
]


def rule_p11(project: Project) -> list[Finding]:
    """Scenes do not inherit environment objects: a missing one is a crash at first render."""
    path = project.app / "Mac" / "MacScenes.swift"
    if not path.is_file():
        project.notes["P11"] = "ios/NBAStats/Mac/MacScenes.swift does not exist yet"
        return []
    f = project.file(path)
    text = f.code_text
    findings: list[Finding] = []
    for label, pattern, needs_store in _SCENES:
        for match in pattern.finditer(text):
            if label == "Settings":
                open_index = match.end() - 1
            else:
                close = match_close(text, match.end() - 1)
                brace = re.match(r"\s*\{", text[close + 1:]) if close > 0 else None
                if not brace:
                    continue
                open_index = close + 1 + brace.end() - 1
            body = text[open_index + 1:match_close(text, open_index)]
            required = ["environment", "environment.catalog", "model"]
            if needs_store:
                required.append("environment.store")
            for name in required:
                if f".environmentObject({name})" not in body.replace(" ", ""):
                    findings.append(Finding(
                        "P11", f.rel, f.line_of(match.start()),
                        f"`{label}` scene root has no `.environmentObject({name})`"))
    return findings


# --------------------------------------------------------------------------- P12

REQUIRED_PLIST_KEYS = (
    "CFBundleIdentifier", "CFBundleExecutable", "CFBundleName", "CFBundlePackageType",
    "CFBundleInfoDictionaryVersion", "CFBundleDevelopmentRegion", "CFBundleShortVersionString",
    "CFBundleVersion", "LSMinimumSystemVersion", "NSPrincipalClass", "HardwoodAPIBaseURL",
    "HardwoodDemoModeDefault",
)
IOS_ONLY_PLIST_KEYS = (
    "LSRequiresIPhoneOS", "UILaunchScreen", "UIApplicationSceneManifest",
    "UISupportedInterfaceOrientations", "UISupportedInterfaceOrientations~ipad",
    "UIBackgroundModes", "BGTaskSchedulerPermittedIdentifiers",
)
MAC_SDK_KEYS = (
    "INFOPLIST_FILE[sdk=macosx*]", "CODE_SIGN_IDENTITY[sdk=macosx*]",
    "ASSETCATALOG_COMPILER_APPICON_NAME[sdk=macosx*]", "LD_RUNPATH_SEARCH_PATHS[sdk=macosx*]",
)


def _build_settings(text: str) -> dict[str, dict[str, str]]:
    """XCBuildConfiguration id -> {setting key: raw value}, from the hand-kept project file."""
    configs: dict[str, dict[str, str]] = {}
    section = re.search(
        r"/\* Begin XCBuildConfiguration section \*/(.*?)/\* End XCBuildConfiguration section \*/",
        text, re.S)
    if not section:
        return configs
    block = re.compile(
        r"\t\t(?P<id>[A-Fa-f0-9]+) /\* \w+ \*/ = \{\n\t\t\tisa = XCBuildConfiguration;\n"
        r"\t\t\tbuildSettings = \{\n(?P<body>.*?)\n\t\t\t\};", re.S)
    setting = re.compile(
        r'^\t\t\t\t(?P<key>"[^"]+"|[A-Z0-9_]+) = (?P<value>\(.*?\n\t\t\t\t\)|[^;\n]+);$',
        re.S | re.M)
    for found in block.finditer(section.group(1)):
        configs[found.group("id")] = {
            s.group("key").strip('"'): s.group("value").strip().strip('"')
            for s in setting.finditer(found.group("body"))
        }
    return configs


def _config_lists(text: str) -> dict[str, list[str]]:
    """Configuration-list comment name -> its configuration ids (Debug, Release)."""
    lists: dict[str, list[str]] = {}
    for found in re.finditer(
        r'/\* (Build configuration list for [^*]+?) \*/ = \{\n\t\t\tisa = XCConfigurationList;\n'
        r"\t\t\tbuildConfigurations = \(\n(.*?)\t\t\t\);", text, re.S):
        lists[found.group(1)] = re.findall(r"([A-Fa-f0-9]{8,})", found.group(2))
    return lists


def rule_p12(project: Project) -> list[Finding]:
    """The Mac plist exists and parses; the project builds for macOS with the agreed settings."""
    findings: list[Finding] = []
    pbx = project.ios / "NBAStats.xcodeproj" / "project.pbxproj"
    plist_path = project.ios / "Info-macOS.plist"
    rel_plist = plist_path.relative_to(project.root).as_posix()
    if not plist_path.is_file():
        findings.append(Finding("P12", rel_plist, 0, "ios/Info-macOS.plist does not exist"))
    else:
        try:
            plist = plistlib.loads(plist_path.read_bytes())
        except Exception as error:  # noqa: BLE001 - an unreadable plist is the finding
            plist = {}
            findings.append(Finding("P12", rel_plist, 0, f"does not parse: {error}"))
        if plist:
            for key in REQUIRED_PLIST_KEYS:
                if key not in plist:
                    findings.append(Finding("P12", rel_plist, 0, f"missing key {key}"))
            for key in IOS_ONLY_PLIST_KEYS:
                if key in plist:
                    findings.append(Finding(
                        "P12", rel_plist, 0, f"iOS-only key {key} has no place in the Mac plist"))
            if plist.get("HardwoodDemoModeDefault") is not False:
                findings.append(Finding(
                    "P12", rel_plist, 0, "HardwoodDemoModeDefault must be false on the Mac"))
            if not str(plist.get("HardwoodAPIBaseURL", "")).startswith("http://127.0.0.1"):
                findings.append(Finding(
                    "P12", rel_plist, 0,
                    "HardwoodAPIBaseURL must be http://127.0.0.1... (the backend binds IPv4 "
                    "loopback only; `localhost` may resolve to ::1)"))
    if not pbx.is_file():
        return findings + [Finding("P12", "ios/NBAStats.xcodeproj/project.pbxproj", 0, "missing")]
    text = pbx.read_text(encoding="utf-8")
    rel_pbx = pbx.relative_to(project.root).as_posix()
    configs = _build_settings(text)
    lists = _config_lists(text)
    synchronized = {
        name.strip().strip('"')
        for name in re.findall(
            r"isa = PBXFileSystemSynchronizedRootGroup;.*?path = ([^;]+);", text, re.S)
    }

    def settings_for(fragment: str) -> list[dict[str, str]]:
        for name, ids in lists.items():
            if fragment in name:
                return [configs[i] for i in ids if i in configs]
        return []

    for label, fragment in (("project", 'PBXProject "NBAStats"'),
                            ("Hardwood target", 'PBXNativeTarget "Hardwood"'),
                            ("HardwoodTests target", 'PBXNativeTarget "HardwoodTests"')):
        if len(settings_for(fragment)) != 2:
            findings.append(Finding(
                "P12", rel_pbx, 0, f"could not find the {label}'s Debug and Release settings"))

    def require(label: str, fragment: str, key: str, expected: str | None = None) -> None:
        for settings in settings_for(fragment):
            value = settings.get(key)
            if value is None:
                findings.append(Finding("P12", rel_pbx, 0, f"{label}: {key} is not set"))
            elif expected is not None and expected not in value:
                findings.append(Finding(
                    "P12", rel_pbx, 0, f"{label}: {key} is `{value}`, expected `{expected}`"))

    project_list = 'PBXProject "NBAStats"'
    require("project", project_list, "SDKROOT", "auto")
    require("project", project_list, "MACOSX_DEPLOYMENT_TARGET", "14.0")
    require("project", project_list, "IPHONEOS_DEPLOYMENT_TARGET", "17.0")
    app_list = 'PBXNativeTarget "Hardwood"'
    for key in MAC_SDK_KEYS:
        require("Hardwood target", app_list, key)
    require("Hardwood target", app_list, "SUPPORTED_PLATFORMS", "macosx")
    require("Hardwood target", app_list, "SUPPORTED_PLATFORMS", "iphoneos")
    require("Hardwood target", app_list, "SUPPORTS_MAC_DESIGNED_FOR_IPHONE_IPAD", "NO")
    require("Hardwood target", app_list, "SUPPORTS_MACCATALYST", "NO")
    require("Hardwood target", app_list, "INFOPLIST_FILE", "Info.plist")
    require("Hardwood target", app_list, "CODE_SIGN_IDENTITY[sdk=macosx*]", "-")
    test_list = 'PBXNativeTarget "HardwoodTests"'
    require("HardwoodTests target", test_list, "SUPPORTED_PLATFORMS", "macosx")
    require("HardwoodTests target", test_list, "SUPPORTS_MAC_DESIGNED_FOR_IPHONE_IPAD", "NO")
    require("HardwoodTests target", test_list, "CODE_SIGN_IDENTITY[sdk=macosx*]", "-")
    for settings in settings_for(app_list):
        for forbidden in (
            "ENABLE_APP_SANDBOX", "ENABLE_HARDENED_RUNTIME", "CODE_SIGN_ENTITLEMENTS"
        ):
            if forbidden in settings:
                findings.append(Finding(
                    "P12", rel_pbx, 0,
                    f"{forbidden} must not be set: the unsandboxed app reads hardwood.env and "
                    "talks plain HTTP to 127.0.0.1"))
        mac_plist = settings.get("INFOPLIST_FILE[sdk=macosx*]")
        if mac_plist:
            if not (project.ios / mac_plist).is_file():
                findings.append(Finding(
                    "P12", rel_pbx, 0, f"INFOPLIST_FILE[sdk=macosx*] names {mac_plist}, which "
                    "does not exist beside the project"))
            first = Path(mac_plist).parts[0] if Path(mac_plist).parts else ""
            if first in synchronized:
                findings.append(Finding(
                    "P12", rel_pbx, 0, f"{mac_plist} is inside synchronized folder {first}/, "
                    "so it would also be bundled as a resource"))
    return findings


# --------------------------------------------------------------------------- P14

#: Fixture (relative to contracts/fixtures/) -> the Swift type that decodes it. A tuple lists
#: acceptable names for types the design left unnamed; the first one declared wins. After M0 this
#: table belongs to whoever owns Core/LeaguePayloads.swift: add an alias there, not a skip.
_ENVELOPES = {
    "availability_report": ("LeagueAvailabilityReport",),
    "box_score": ("ElBoxScore",),
    "club_view": ("ElClubView",),
    "defense_by_position": ("LeagueDefenseDocument",),
    "defense_by_position_table": ("LeagueDefenseDocument",),
    "game_matchup": ("LeagueMatchup",),
    "game_projection_detail": ("LeagueGameProjectionDetail",),
    "games": ("ElGamesList", "ElGamesTable", "ElGames"),
    "meta": ("ElMeta",),
    "method": ("ElMethod",),
    "model_settings": ("LeagueModelSettings",),
    "news": ("LeagueNewsList",),
    "player_detail": ("ElPlayerDetail",),
    "player_gamelog": ("ElPlayerGameLog",),
    "player_stats": ("ElPlayerStatsTable",),
    "projection_review": ("LeagueProjectionReview",),
    "ratings": ("ElRatingsTable", "RatingsTable", "ElRatings"),
    "review_queue": ("LeagueReviewQueue",),
    "round_scorers": ("ElRoundScorers",),
    "round_view": ("ElRoundView",),
    "round_view_complete": ("ElRoundView",),
    "slate_projections": ("LeagueSlateProjections",),
    "sources": ("LeagueSourceList",),
    "team_matchup": ("LeagueMatchup",),
    "teams": ("ElTeamsTable", "ElTeamsList", "ElTeams"),
}
P14_FIXTURES: dict[str, tuple[str, ...]] = {
    **{f"leagues/el/{name}.json": names for name, names in _ENVELOPES.items()},
    **{f"leagues/nba/{name}.json": names for name, names in _ENVELOPES.items()},
    "leagues/nba/availability_report_demo.json": ("LeagueAvailabilityReport",),
    "leagues/nba/availability_review_queue.json": ("LeagueReviewQueue",),
    "leagues/nba/leagues.json": ("[LeagueInfo]",),
    "leagues/el/widget_availability_report.json": ("LeagueAvailabilityReport",),
    "leagues/el/widget_defense_by_position.json": ("LeagueDefenseDocument",),
    "leagues/el/widget_slate_projections.json": ("LeagueSlateProjections",),
    "leagues/el/widget_team_matchup.json": ("LeagueMatchup",),
    "widget_availability_report.json": ("LeagueAvailabilityReport",),
    "widget_defense_by_position.json": ("LeagueDefenseDocument",),
    "widget_slate_projections.json": ("LeagueSlateProjections",),
    "widget_team_matchup.json": ("LeagueMatchup",),
}
_SCALARS = {"String", "Int", "Double", "Bool", "Float", "CGFloat", "Int32", "Int64", "UInt"}


def _fixture_paths(project: Project) -> list[Path]:
    base = project.root / "contracts" / "fixtures"
    paths = sorted((base / "leagues").glob("*/*.json"))
    paths += [base / f"widget_{kind}.json" for kind in (
        "team_matchup", "defense_by_position", "availability_report", "slate_projections")]
    return [p for p in paths if p.is_file()]


def _describe_json(value: object) -> str:
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, int):
        return "an integer"
    if isinstance(value, float):
        return f"a float ({value!r})"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "an array"
    if isinstance(value, dict):
        return "an object"
    return "null"


class _Walker:
    """Compare JSON documents against the Swift types that are supposed to decode them.

    Problems are de-duplicated: a property missing from a struct is reported once, for the first
    fixture and path where it was seen, however many fixtures and array elements repeat it.
    """

    def __init__(self, index: dict[str, StructInfo]) -> None:
        self.index = index
        self.problems: dict[tuple[str, str, str], tuple[str, str, str]] = {}
        self.unchecked: set[str] = set()

    def note(self, key: tuple[str, str, str], fixture: str, path: str, message: str) -> None:
        self.problems.setdefault(key, (fixture, path, message))

    def walk(self, value: object, swift: str, fixture: str, path: str, owner: str) -> None:
        """``owner`` names what is being decoded (``Type.property``) for the de-duplication."""
        swift = swift.strip()
        optional = swift.endswith("?")
        base = swift.rstrip("?").strip()
        if base == "JSONValue":
            return  # represents any JSON value, null included
        if value is None:
            if not optional:
                self.note(("null", owner, swift), fixture, path, f"null for non-Optional `{swift}`")
            return
        if base.startswith("[") and base.endswith("]"):
            self._collection(value, base, swift, fixture, path, owner)
            return
        if base in _SCALARS:
            self._scalar(value, base, swift, fixture, path, owner)
            return
        info = self.index.get(base)
        if info is None:
            self.note(("undeclared", owner, base), fixture, path,
                      f"type `{base}` is not declared anywhere")
            return
        if info.kind != "struct":
            return  # a raw-value enum decodes from a string: nothing to descend into
        if not isinstance(value, dict):
            self.note(("shape", owner, swift), fixture, path,
                      f"`{swift}` needs an object, the fixture has {_describe_json(value)}")
            return
        if info.custom_codable:
            self.unchecked.add(info.name)
            return
        properties = {prop.name: prop for prop in info.properties}
        for key, item in value.items():
            prop = properties.get(key)
            if prop is None:
                self.note(("missing", info.name, key), fixture, f"{path}.{key}",
                          f"no property `{key}` in `{info.name}`")
            else:
                self.walk(item, prop.type, fixture, f"{path}.{key}", f"{info.name}.{key}")

    def _collection(self, value: object, base: str, swift: str, fixture: str, path: str,
                    owner: str) -> None:
        inner = base[1:-1]
        parts = split_top_level(inner, ":")
        if len(parts) == 2:
            if not isinstance(value, dict):
                self.note(("shape", owner, swift), fixture, path,
                          f"`{swift}` needs an object, the fixture has {_describe_json(value)}")
                return
            for key, item in value.items():
                self.walk(item, parts[1], fixture, f"{path}.{key}", owner)
            return
        if not isinstance(value, list):
            self.note(("shape", owner, swift), fixture, path,
                      f"`{swift}` needs an array, the fixture has {_describe_json(value)}")
            return
        for item in value:
            self.walk(item, inner, fixture, f"{path}[*]", owner)

    def _scalar(self, value: object, base: str, swift: str, fixture: str, path: str,
                owner: str) -> None:
        whole = {"Int", "Int32", "Int64", "UInt"}
        if isinstance(value, bool):
            ok = base == "Bool"
        elif isinstance(value, int):
            ok = base in whole | {"Double", "Float", "CGFloat"}
        elif isinstance(value, float):
            ok = base in {"Double", "Float", "CGFloat"}
        else:
            ok = isinstance(value, str) and base == "String"
        if not ok:
            hint = (" (an Int throws on `6.0`: a statistic must be `Double?`)"
                    if isinstance(value, float) and base in whole else "")
            self.note(("type", owner, swift), fixture, path,
                      f"`{swift}` cannot decode {_describe_json(value)}{hint}")


def rule_p14(project: Project) -> list[Finding]:
    """Every fixture key must be a property of its Swift struct (with a type that can hold it)."""
    findings: list[Finding] = []
    fixtures = _fixture_paths(project)
    base = project.root / "contracts" / "fixtures"
    for path in fixtures:
        relative = path.relative_to(base).as_posix()
        if relative not in P14_FIXTURES:
            findings.append(Finding(
                "P14", path.relative_to(project.root).as_posix(), 0,
                "no Swift type is mapped to this fixture: add it to P14_FIXTURES in "
                "scripts/check_swift_portability.py (a new backend payload needs a struct)"))
    if not project.payloads.is_file():
        project.notes["P14"] = (
            f"{_shown(project, project.payloads)} does not exist yet: only the fixture-to-type "
            f"table was checked ({len(fixtures)} fixtures mapped), no key was compared")
        return findings
    walker = _Walker(build_index(project, project.payloads))
    for path in fixtures:
        relative = path.relative_to(base).as_posix()
        names = P14_FIXTURES.get(relative)
        if not names:
            continue
        chosen = next((n for n in names if n.strip("[]") in walker.index), None)
        if chosen is None:
            findings.append(Finding(
                "P14", path.relative_to(project.root).as_posix(), 0,
                f"none of the types {list(names)} is declared in Swift"))
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        root_type = chosen if chosen.startswith("[") else chosen + "?"
        walker.walk(document, root_type, relative, "$", chosen)
    for _, (fixture, path, message) in sorted(walker.problems.items()):
        findings.append(Finding(
            "P14", f"contracts/fixtures/{fixture}", 0, f"{path}: {message}"))
    return findings


# --------------------------------------------------------------------------- driver

RULES: dict[str, tuple[str, Callable[[Project], list[Finding]]]] = {
    "P1": ("Mac files are fenced whole in #if os(macOS)", rule_p1),
    "P2": ("AppKit / Mac-only SwiftUI only in Mac regions", rule_p2),
    "P3": ("iOS-only names only in iOS regions", rule_p3),
    "P4": ("macOS 15 and denylisted APIs nowhere", rule_p4),
    "P5": ("exhaustive switches name every case", rule_p5),
    "P6": ("Table columns: <= 10, all sortable, no conditionals", rule_p6),
    "P7": ("*Widget.swift files map to a WidgetKind", rule_p7),
    "P8": ("no non-Swift file in the synchronized folder", rule_p8),
    "P9": ("no betting vocabulary in string literals", rule_p9),
    "P10": ("league payload types are public, all-Optional, synthesized", rule_p10),
    "P11": ("scene roots get their environment objects", rule_p11),
    "P12": ("macOS plist and Xcode settings", rule_p12),
    "P13": ("statistic-looking payload properties are Double?", rule_p13),
    "P14": ("every fixture key is a property of its Swift struct", rule_p14),
}


def run(project: Project, only: list[str] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for name, (_, rule) in RULES.items():
        if only and name not in only:
            continue
        findings.extend(rule(project))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root (default: here)")
    parser.add_argument("--only", help="comma-separated rule ids, e.g. P1,P5")
    parser.add_argument("--payloads", type=Path,
                        help="check this file as Core/LeaguePayloads.swift (P10, P13, P14)")
    parser.add_argument("--list", action="store_true", help="list the rules and exit")
    args = parser.parse_args(argv)
    if args.list:
        for name, (summary, _) in RULES.items():
            print(f"{name:<4} {summary}")
        return 0
    only = [r.strip().upper() for r in args.only.split(",")] if args.only else None
    unknown = [r for r in (only or []) if r not in RULES]
    if unknown:
        print(f"unknown rule(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    project = Project(args.root.resolve(), args.payloads.resolve() if args.payloads else None)
    if not project.app.is_dir():
        print(f"no iOS app at {project.app}", file=sys.stderr)
        return 2
    findings = run(project, only)
    for finding in findings:
        print(finding.render())
    ran = only or list(RULES)
    swift_count = len(project.all_swift())
    if findings:
        print(f"\n{len(findings)} finding(s) across {len(ran)} rule(s), {swift_count} Swift files.")
    else:
        print(f"All {len(ran)} portability rules passed over {swift_count} Swift files.")
    for name, note in sorted(project.notes.items(), key=lambda item: int(item[0][1:])):
        print(f"  note {name}: idle, {note}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
