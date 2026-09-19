#!/usr/bin/env python3
"""Scrape ``Theme.swift`` (and its three siblings) into ``contracts/theme.json``.

Hardwood Web's whole design system is generated from the SwiftUI design system, in one
direction, because the alternative — hand-authoring a second ``theme.json`` next to a Swift
source that already defines every color — is strictly worse: two hand-edited sources of the
same fact can drift from each other with nothing to notice, while one hand-edited source
scraped by a strict parser can only drift from itself, which is a merge conflict, not a bug.
Swift is not regenerated: it ships in a reviewed app and its comments carry rationale ("a black
shadow on a near-black page is invisible") that no JSON field would preserve.

``Theme.swift`` does not have one declaration shape, it has four (see ``WEB_DESIGN.md`` §0.1-D,
verified against this exact checkout):

  1. a one-line ``public static let name = adaptive(light: 0x..., dark: 0x...)``;
  2. the same shape wrapped across two physical lines, alphas on the continuation line
     (``skeletonHighlight``, ``Theme.swift:77-78``);
  3. bare, index-significant array members inside ``chartSeries`` / ``monogramPalette``, with an
     optional ``// name`` trailing comment;
  4. ``case .name: return adaptive(...)`` inside a ``switch`` in an ``AccentName`` extension.

A parser that only recognises shape 1 silently drops shape 2 (one color) and would have shipped
a theme with fifteen entries instead of sixteen and no error anywhere. So every declaration this
script finds inside a block it is scanning either matches one of the four shapes or the run
fails, naming the file and line — a generator that produces *something* on a color it could not
parse is worse than one that refuses to run at all.

Two small tables have no file to read them from and are declared here, by hand, clearly marked:
Apple's Dynamic Type point sizes (they are frame sizes of a *dynamic* type system, not constants
in this repository) and the CSS breakpoint standing in for ``horizontalSizeClass == .regular``
(iOS has no pixel value for that at all). Everything else in the output is derived.

Usage: ``python3 contracts/tools/gen_theme.py`` prints the JSON document to stdout. Run from
anywhere; paths are resolved from this file's location, matching every other generator here.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
IOS = ROOT / "ios" / "NBAStats"
THEME_SWIFT = IOS / "DesignSystem" / "Theme.swift"
BROADSHEET_SWIFT = IOS / "DesignSystem" / "BroadsheetTheme.swift"
TYPOGRAPHY_SWIFT = IOS / "DesignSystem" / "Typography.swift"
LAYOUT_SWIFT = IOS / "Core" / "DashboardLayout.swift"
WIDGETS_JSON = ROOT / "contracts" / "widgets.json"


class ThemeParseError(SystemExit):
    """Raised with ``file:line: reason`` and an exit code of 1.

    Every raise site names the exact source line so a broken parse is a one-line fix, never a
    diff of the whole generated file to figure out which token went missing.
    """

    def __init__(self, path: Path, line_no: int | None, reason: str) -> None:
        where = f"{path.relative_to(ROOT)}:{line_no}" if line_no else f"{path.relative_to(ROOT)}"
        super().__init__(f"{where}: {reason}")


# --------------------------------------------------------------------------- shared regexes

#: Shapes 1, 2 and 4 all end in a call to ``adaptive(light: 0x…, dark: 0x…[, lightAlpha:
#: …][, darkAlpha: …])``, optionally qualified as ``Palette.adaptive`` — BroadsheetTheme.swift
#: qualifies every call, Theme.swift never does because it IS ``Palette``.
#: Named groups throughout, deliberately: NAMED, MEMBER and CASE each wrap this fragment with a
#: different number of *their own* capture groups ahead of it (a declaration name, nothing, or a
#: case name), and a positional offset that happened to work for one shape and not the others is
#: exactly the kind of silent-drop bug this generator exists to make impossible.
_ADAPTIVE = (
    r"(?:Palette\.)?adaptive\(\s*light:\s*0x(?P<light>[0-9A-Fa-f]{6})\s*,\s*"
    r"dark:\s*0x(?P<dark>[0-9A-Fa-f]{6})"
    r"(?:\s*,\s*lightAlpha:\s*(?P<lightAlpha>[\d.]+))?"
    r"(?:\s*,\s*darkAlpha:\s*(?P<darkAlpha>[\d.]+))?\s*\)"
)
#: Used only to flag an ``adaptive(`` call none of the four shapes matched. The negative
#: lookbehind excludes the ``adaptive(...)`` *constructor's own definition* (``private static
#: func solid`` calls it, and it is declared as ``public static func adaptive(light: UInt32,
#: ...)`` right at the top of ``Palette`` — the only place the literal text "adaptive(" appears
#: without being a color declaration).
_ADAPTIVE_CALL = re.compile(r"(?<!func )(?:Palette\.)?adaptive\(")
NAMED = re.compile(r"^public static let (?P<name>\w+)\s*=\s*" + _ADAPTIVE + r"\s*$")
MEMBER = re.compile(r"^" + _ADAPTIVE + r"\s*,?\s*(?://\s*(?P<comment>\S+)\s*)?$")
CASE = re.compile(r"^case \.(?P<case>\w+):\s*return\s*" + _ADAPTIVE + r"\s*$")
SCALAR = re.compile(r"^public static let (\w+):\s*CGFloat\s*=\s*([\d.]+)\s*$")


def _hex(match: "re.Match[str]", group: str) -> str:
    return "#" + match.group(group).upper()


def _alpha(match: "re.Match[str]", group: str) -> float:
    raw = match.group(group)
    return 1.0 if raw is None else float(raw)


def _color_from_adaptive(match: "re.Match[str]") -> dict[str, Any]:
    entry: dict[str, Any] = {
        "light": _hex(match, "light"),
        "dark": _hex(match, "dark"),
        "lightAlpha": _alpha(match, "lightAlpha"),
        "darkAlpha": _alpha(match, "darkAlpha"),
    }
    if entry["lightAlpha"] == 1.0 and entry["darkAlpha"] == 1.0:
        del entry["lightAlpha"]
        del entry["darkAlpha"]
    return entry


# --------------------------------------------------------------------------- source plumbing


def _read(path: Path) -> str:
    if not path.is_file():
        raise ThemeParseError(path, None, "file does not exist")
    return path.read_text(encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _logical_lines(path: Path, raw_lines: list[str]) -> list[tuple[int, str]]:
    """Join a physical line to the next while its parenthesis count is unbalanced.

    Only ``skeletonHighlight`` needs this in the files this script reads (§0.1-D: its alphas sit
    on ``Theme.swift:77-78``, on their own), but the join is written generally — against
    whichever physical lines are handed in, usually one already-isolated block — so no other
    multi-line declaration becomes a silent gap if one is ever added.
    """
    joined: list[tuple[int, str]] = []
    index = 0
    total = len(raw_lines)
    while index < total:
        start = index
        text = raw_lines[index].strip()
        depth = text.count("(") - text.count(")")
        while depth > 0:
            index += 1
            if index >= total:
                raise ThemeParseError(path, start + 1, "unbalanced '(' with no closing line")
            cont = raw_lines[index].strip()
            text += " " + cont
            depth += cont.count("(") - cont.count(")")
        joined.append((start + 1, text))
        index += 1
    return joined


def _brace_block(path: Path, lines: list[str], header: re.Pattern[str]) -> list[tuple[int, str]]:
    """Return the ``(line_no, text)`` pairs strictly between the ``{`` and its matching ``}``.

    Line numbers are 1-based and absolute in ``path``, so an error raised from inside the block
    still names a real, findable line.
    """
    for i, line in enumerate(lines):
        if header.search(line):
            depth = line.count("{") - line.count("}")
            body_start = i + 1
            j = i
            while depth > 0:
                j += 1
                if j >= len(lines):
                    raise ThemeParseError(path, i + 1, "unmatched '{' for this block header")
                depth += lines[j].count("{") - lines[j].count("}")
            return [(k + 1, lines[k]) for k in range(body_start, j)]
    raise ThemeParseError(path, None, f"no block found matching {header.pattern!r}")


def _assert_no_orphan_adaptive(
    path: Path, lines: list[tuple[int, str]], consumed: set[int]
) -> None:
    """Fail loudly if an ``adaptive(`` call in a scanned block matched none of the four shapes.

    This is the actual point of the exercise: a refactor that turns a ``static let`` into a
    computed property, or adds a fifth case to a switch, must break the build instead of quietly
    shipping fifteen colors where there were sixteen.
    """
    for line_no, text in lines:
        if line_no in consumed:
            continue
        if _ADAPTIVE_CALL.search(text):
            raise ThemeParseError(
                path, line_no, f"unparseable adaptive(...) declaration: {text.strip()!r}"
            )


# --------------------------------------------------------------------------- Theme.swift: palette

#: The sixteen semantic colors, in the order Theme.swift declares them. Named explicitly (rather
#: than "whatever we happened to match") so a renamed or removed `static let` is a KeyError
#: naming the missing color, not a silently short palette.
PALETTE_KEYS = (
    "background", "surface", "surfaceRaised", "surfaceSunken", "separator",
    "textPrimary", "textSecondary", "textTertiary",
    "positive", "negative", "neutral", "warning", "selection",
    "track", "skeleton", "skeletonHighlight",
)


def _consumed_physical_spans(block: list[tuple[int, str]], consumed_starts: set[int]) -> set[int]:
    """Expand a set of *logical*-line start numbers back into every physical line they cover.

    A logical line built by `_logical_lines` may span several physical lines (only
    `skeletonHighlight` does today). The orphan check below walks physical lines, so a matched
    logical line's continuation line has to be marked consumed too, or the check re-flags the
    second half of a declaration it already accepted.
    """
    raw_texts = [t for _, t in block]
    starts = [n for n, _ in block]
    consumed_physical: set[int] = set()
    i = 0
    while i < len(raw_texts):
        depth = raw_texts[i].count("(") - raw_texts[i].count(")")
        span = [starts[i]]
        j = i
        while depth > 0 and j + 1 < len(raw_texts):
            j += 1
            span.append(starts[j])
            depth += raw_texts[j].count("(") - raw_texts[j].count(")")
        if starts[i] in consumed_starts:
            consumed_physical.update(span)
        i = j + 1
    return consumed_physical


def parse_palette(
    path: Path, text: str, extra_consumed: set[int] = frozenset()
) -> dict[str, dict[str, Any]]:
    lines = text.splitlines()
    block = _brace_block(path, lines, re.compile(r"public enum Palette\b"))
    logical = _logical_lines(path, [t for _, t in block])
    # Re-attach absolute line numbers: _logical_lines renumbers from 0 against the slice it was
    # given, so translate back using the block's own starting line.
    offset = block[0][0] - 1 if block else 0
    logical = [(line_no + offset, t) for line_no, t in logical]

    found: dict[str, dict[str, Any]] = {}
    consumed: set[int] = set()
    for line_no, text_line in logical:
        match = NAMED.match(text_line)
        if match and match.group("name") in PALETTE_KEYS:
            found[match.group("name")] = _color_from_adaptive(match)
            consumed.add(line_no)

    missing = [key for key in PALETTE_KEYS if key not in found]
    if missing:
        raise ThemeParseError(path, block[0][0], f"palette is missing {missing}")
    if len(found) != 16:
        raise ThemeParseError(path, block[0][0], f"expected 16 palette colors, found {len(found)}")

    # `chartSeries` and `monogramPalette` are two more array literals inside this same enum body
    # (parsed separately by `parse_array`, called first in `build_theme`); their member lines are
    # legitimate `adaptive(` calls too, so the caller passes their line numbers in here rather
    # than this function re-deriving them, which would mean two independent array scanners.
    consumed_physical = _consumed_physical_spans(block, consumed) | extra_consumed
    _assert_no_orphan_adaptive(path, block, consumed_physical)
    return {key: found[key] for key in PALETTE_KEYS}


# --------------------------------------------------------------------------- Theme.swift: arrays


def parse_array(
    path: Path, text: str, member_name: str, expected_len: int
) -> tuple[list[dict[str, Any]], set[int]]:
    """Read the positional members of ``static let <member_name>: [Color] = [ ... ]``.

    Returns the members and the set of physical line numbers they occupy — `parse_palette`
    folds that set into its own orphan check, since both arrays live inside the same `Palette`
    enum body it scans.
    """
    lines = text.splitlines()
    header = re.compile(
        r"public static let " + re.escape(member_name) + r"\s*:\s*\[Color\]\s*=\s*\["
    )
    for i, line in enumerate(lines):
        if header.search(line):
            depth = 1  # the opening '[' the header itself consumed
            j = i
            body: list[tuple[int, str]] = []
            while depth > 0:
                j += 1
                if j >= len(lines):
                    raise ThemeParseError(path, i + 1, f"unterminated {member_name} array")
                depth += lines[j].count("[") - lines[j].count("]")
                if depth > 0:
                    body.append((j + 1, lines[j]))
                else:
                    # The closing ']' line may still hold a trailing member before it.
                    tail = lines[j].rsplit("]", 1)[0]
                    if tail.strip():
                        body.append((j + 1, tail))
            members: list[dict[str, Any]] = []
            consumed: set[int] = set()
            for line_no, member_line in body:
                stripped = member_line.strip()
                if not stripped:
                    continue
                match = MEMBER.match(stripped)
                if not match:
                    raise ThemeParseError(
                        path, line_no, f"unparseable {member_name} member: {stripped!r}"
                    )
                entry = _color_from_adaptive(match)
                if match.group("comment"):
                    entry = {"name": match.group("comment"), **entry}
                members.append(entry)
                consumed.add(line_no)
            if len(members) != expected_len:
                raise ThemeParseError(
                    path, i + 1,
                    f"{member_name} has {len(members)} members, expected {expected_len}",
                )
            return members, consumed
    raise ThemeParseError(path, None, f"no array literal found for {member_name}")


# --------------------------------------------------------------------------- Theme.swift: accents


def _accent_names(path: Path, text: str) -> list[str]:
    """The ``AccentName`` case list and its order, from ``Core/DashboardLayout.swift``.

    Order matters: it is the order the web's accent picker lists them in, and it is asserted
    against ``Theme.swift``'s two switches below so the three never silently diverge.
    """
    lines = text.splitlines()
    block = _brace_block(path, lines, re.compile(r"public enum AccentName\b"))
    for _, line in block:
        match = re.match(r"case ([a-zA-Z, ]+)$", line.strip())
        if match:
            return [name.strip() for name in match.group(1).split(",")]
    raise ThemeParseError(path, block[0][0] if block else None, "no 'case a, b, c' line found")


def _parse_accent_switch(
    path: Path, text: str, var_name: str, names: list[str]
) -> dict[str, dict[str, Any]]:
    lines = text.splitlines()
    header = re.compile(r"var " + re.escape(var_name) + r"\s*:\s*Color\s*\{")
    block = _brace_block(path, lines, header)
    logical = _logical_lines(path, [t for _, t in block])
    offset = block[0][0] - 1 if block else 0
    logical = [(line_no + offset, t) for line_no, t in logical]

    found: dict[str, dict[str, Any]] = {}
    consumed_starts: set[int] = set()
    for line_no, text_line in logical:
        match = CASE.match(text_line)
        if match:
            found[match.group("case")] = _color_from_adaptive(match)
            consumed_starts.add(line_no)

    consumed_physical = _consumed_physical_spans(block, consumed_starts)
    _assert_no_orphan_adaptive(path, block, consumed_physical)

    missing = [name for name in names if name not in found]
    if missing:
        raise ThemeParseError(path, block[0][0], f"var {var_name} is missing cases {missing}")
    extra = sorted(set(found) - set(names))
    if extra:
        raise ThemeParseError(
            path, block[0][0], f"var {var_name} has cases AccentName does not: {extra}"
        )
    return found


# --------------------------------------------------------------------------- Theme.swift: shadow


def parse_card_shadow(path: Path, text: str) -> dict[str, Any]:
    """The card shadow lives as ternaries inside ``HardwoodCardModifier.body`` (§0.1-D), not as
    ``static let``s, so it needs its own three targeted patterns rather than the four shapes
    above. Each pattern failing to match is its own named failure: a modifier rewritten to use a
    different literal should not silently ship a wrong or missing shadow on the web.
    """
    lines = text.splitlines()
    block = _brace_block(path, lines, re.compile(r"struct HardwoodCardModifier\b"))
    body_text = " ".join(t.strip() for _, t in block)

    color_match = re.search(r"Color\.black\.opacity\(([\d.]+)\)", body_text)
    radius_match = re.search(r"radius:\s*isRaised\s*\?\s*(\d+)\s*:\s*(\d+)", body_text)
    y_match = re.search(r"\by:\s*isRaised\s*\?\s*(\d+)\s*:\s*(\d+)", body_text)
    if not color_match:
        raise ThemeParseError(path, block[0][0], "no Color.black.opacity(...) shadow literal found")
    if not radius_match:
        raise ThemeParseError(path, block[0][0], "no 'radius: isRaised ? N : M' literal found")
    if not y_match:
        raise ThemeParseError(path, block[0][0], "no 'y: isRaised ? N : M' literal found")

    opacity = color_match.group(1)
    color = f"rgba(0,0,0,{opacity})"
    raised_radius, base_radius = int(radius_match.group(1)), int(radius_match.group(2))
    raised_y, base_y = int(y_match.group(1)), int(y_match.group(2))
    return {
        "card": {"y": base_y, "blur": base_radius, "color": color},
        "raised": {"y": raised_y, "blur": raised_radius, "color": color},
        # No Swift source defines a drag shadow — SwiftUI has no drag-reorder concept for a
        # dashboard grid. This one value is hand-authored for the web's dnd-kit interaction
        # (WEB_DESIGN.md §7.5) and is the one entry in this whole file that is NOT derived.
        "drag": {"y": 8, "blur": 16, "color": "rgba(0,0,0,0.18)"},
        "dark": "none",
    }


# --------------------------------------------------------------------------- Spacing / Radius


def parse_scalars(path: Path, text: str, enum_name: str, keys: tuple[str, ...]) -> dict[str, float]:
    lines = text.splitlines()
    block = _brace_block(path, lines, re.compile(r"public enum " + re.escape(enum_name) + r"\b"))
    found: dict[str, float] = {}
    for line_no, line in block:
        match = SCALAR.match(line.strip())
        if match:
            found[match.group(1)] = _num(match.group(2))
    missing = [key for key in keys if key not in found]
    if missing:
        raise ThemeParseError(path, block[0][0], f"{enum_name} is missing {missing}")
    if len(found) != len(keys):
        extra = sorted(set(found) - set(keys))
        raise ThemeParseError(path, block[0][0], f"{enum_name} has unexpected members: {extra}")
    return {key: found[key] for key in keys}


def _num(text: str) -> float:
    value = float(text)
    return int(value) if value.is_integer() else value


SPACING_KEYS = ("hairline", "xxs", "xs", "sm", "md", "lg", "xl", "xxl")
RADIUS_KEYS = ("chip", "control", "card", "sheet", "pill")


# --------------------------------------------------------------------------- BroadsheetTheme.swift

BROADSHEET_COLOR_KEYS = (
    "background", "text", "textMuted", "rule", "band", "accentAbove", "accentBelow",
)
BROADSHEET_METRIC_KEYS = ("bandHeight", "dotSize", "tickWidth", "tickHeight", "kickerTracking")


def parse_broadsheet(path: Path, text: str) -> dict[str, Any]:
    lines = text.splitlines()
    block = _brace_block(path, lines, re.compile(r"public enum Broadsheet\b"))
    colors: dict[str, dict[str, Any]] = {}
    metrics: dict[str, float] = {}
    consumed: set[int] = set()
    for line_no, line in block:
        stripped = line.strip()
        named = NAMED.match(stripped)
        if named and named.group("name") in BROADSHEET_COLOR_KEYS:
            colors[named.group("name")] = _color_from_adaptive(named)
            consumed.add(line_no)
            continue
        scalar = SCALAR.match(stripped)
        if scalar and scalar.group(1) in BROADSHEET_METRIC_KEYS:
            metrics[scalar.group(1)] = _num(scalar.group(2))
            consumed.add(line_no)
    _assert_no_orphan_adaptive(path, block, consumed)

    missing_colors = [key for key in BROADSHEET_COLOR_KEYS if key not in colors]
    if missing_colors:
        raise ThemeParseError(path, block[0][0], f"Broadsheet is missing colors {missing_colors}")
    if len(colors) != 7:
        raise ThemeParseError(
            path, block[0][0], f"expected 7 broadsheet colors, found {len(colors)}"
        )
    missing_metrics = [key for key in BROADSHEET_METRIC_KEYS if key not in metrics]
    if missing_metrics:
        raise ThemeParseError(path, block[0][0], f"Broadsheet is missing metrics {missing_metrics}")
    if len(metrics) != 5:
        raise ThemeParseError(
            path, block[0][0], f"expected 5 broadsheet metrics, found {len(metrics)}"
        )

    result = {key: colors[key] for key in BROADSHEET_COLOR_KEYS}
    result["metrics"] = {key: metrics[key] for key in BROADSHEET_METRIC_KEYS}
    return result


# --------------------------------------------------------------------------- Typography.swift

#: Apple's Dynamic Type point sizes at the default content size category. These exist in no file
#: in this repository — Dynamic Type is computed by UIKit/SwiftUI at runtime from the *category*
#: (`.title`, `.title3`, ...), never a literal point size — so unlike everything else in this
#: generator, this table is hand-authored. It is the sole source for the pixel column of
#: `theme.json#/textStyles`; `weight`, `tracking`, `isNumeric` and `defaultColor` all come from
#: parsing `Typography.swift`'s switches below, and the case set is asserted against them.
TEXT_STYLE_PX = {
    "displayValue": 28,
    "statValue": 20,
    "statLabel": 12,
    "tableHeader": 11,
    "tableCell": 13,
    "caption": 12,
    "sectionTitle": 17,
    "widgetTitle": 15,
}

#: SwiftUI's `Font.Weight` has no numeric `rawValue`; this is the standard, widely documented
#: mapping onto the CSS `font-weight` numbers those same names mean everywhere else on the web.
FONT_WEIGHT_CSS = {
    "ultraLight": 100, "thin": 200, "light": 300, "regular": 400,
    "medium": 500, "semibold": 600, "bold": 700, "heavy": 800, "black": 900,
}


def _text_style_cases(path: Path, text: str) -> list[str]:
    lines = text.splitlines()
    block = _brace_block(path, lines, re.compile(r"enum HardwoodTextStyle\b"))
    cases: list[str] = []
    for _, line in block:
        match = re.match(r"case (\w+)$", line.strip())
        if match:
            cases.append(match.group(1))
    if not cases:
        raise ThemeParseError(path, block[0][0] if block else None, "no 'case name' lines found")
    return cases


def parse_text_styles(path: Path, text: str) -> dict[str, dict[str, Any]]:
    case_names = _text_style_cases(path, text)
    missing_px = [name for name in case_names if name not in TEXT_STYLE_PX]
    if missing_px:
        raise ThemeParseError(
            path, None,
            "HardwoodTextStyle gained case(s) with no hand-authored px size in "
            f"gen_theme.py: {missing_px}",
        )
    if set(case_names) != set(TEXT_STYLE_PX):
        extra = sorted(set(TEXT_STYLE_PX) - set(case_names))
        raise ThemeParseError(
            path, None, f"gen_theme.py's TEXT_STYLE_PX has stale case(s): {extra}"
        )

    is_numeric = _parse_case_group_switch(
        path, text, r"var isNumeric: Bool \{", {"true": True, "false": False}
    )
    weight = _parse_case_return_switch(
        path, text, r"private var weight: Font\.Weight \{", r"\.(\w+)"
    )
    tracking = _parse_case_return_switch(path, text, r"var tracking: CGFloat \{", r"(-?[\d.]+)")
    color = _parse_case_return_switch(
        path, text, r"var defaultColor: Color \{", r"Palette\.(\w+)"
    )

    styles: dict[str, dict[str, Any]] = {}
    for name in case_names:
        for table, label in (
            (is_numeric, "isNumeric"),
            (weight, "weight"),
            (tracking, "tracking"),
            (color, "defaultColor"),
        ):
            if name not in table:
                raise ThemeParseError(path, None, f"{label} switch has no case for {name!r}")
        styles[name] = {
            "px": TEXT_STYLE_PX[name],
            "weight": FONT_WEIGHT_CSS[weight[name]],
            "tracking": _num(tracking[name]),
            "numeric": is_numeric[name],
            "color": color[name],
        }
    return styles


def _parse_case_group_switch(
    path: Path, text: str, header_pattern: str, value_map: dict[str, Any]
) -> dict[str, Any]:
    """A switch whose ``case a, b, c:`` groups share one ``return <literal>`` line."""
    lines = text.splitlines()
    block = _brace_block(path, lines, re.compile(header_pattern))
    result: dict[str, Any] = {}
    pending: list[str] = []
    for _, line in block:
        stripped = line.strip()
        case_match = re.match(r"case ([.\w, ]+):$", stripped)
        if case_match:
            pending.extend(name.strip().lstrip(".") for name in case_match.group(1).split(","))
            continue
        return_match = re.match(r"return (\w+)$", stripped)
        if return_match and pending:
            literal = return_match.group(1)
            if literal not in value_map:
                raise ThemeParseError(
                    path, None, f"unrecognised literal {literal!r} in {header_pattern}"
                )
            for name in pending:
                result[name] = value_map[literal]
            pending = []
    return result


def _parse_case_return_switch(
    path: Path, text: str, header_pattern: str, value_pattern: str
) -> dict[str, str]:
    """A switch whose ``case a, b, c:`` groups share one ``return`` line matched by
    ``value_pattern`` (a regex with exactly one capture group)."""
    lines = text.splitlines()
    block = _brace_block(path, lines, re.compile(header_pattern))
    logical = _logical_lines(path, [t for _, t in block])
    result: dict[str, str] = {}
    pending: list[str] = []
    value_re = re.compile(r"return\s+.*?" + value_pattern)
    for _, line in logical:
        stripped = line.strip()
        case_match = re.match(r"case ([.\w, ]+):\s*(.*)$", stripped)
        if case_match:
            pending.extend(name.strip().lstrip(".") for name in case_match.group(1).split(","))
            remainder = case_match.group(2)
            value_match = value_re.match("return " + remainder) if remainder else None
            if value_match:
                for name in pending:
                    result[name] = value_match.group(1)
                pending = []
            continue
        value_match = value_re.match(stripped)
        if value_match and pending:
            for name in pending:
                result[name] = value_match.group(1)
            pending = []
    return result


# --------------------------------------------------------------------------- grid (widgets.json)


def load_grid() -> dict[str, Any]:
    """The grid geometry is NOT re-stated here: it is copied from ``widgets.json``, the one
    source WP0's widget catalog generator already owns, so there is exactly one place that
    knows a `large` tile spans 2 columns on compact and 4 on regular (§0.1-E)."""
    if not WIDGETS_JSON.is_file():
        raise ThemeParseError(WIDGETS_JSON, None, "run gen_widgets.py before gen_theme.py")
    document = json.loads(WIDGETS_JSON.read_text(encoding="utf-8"))
    columns = document["gridColumns"]
    sizes = {size["key"]: size for size in document["sizes"]}
    order = ("small", "medium", "large")
    missing = [key for key in order if key not in sizes]
    if missing:
        raise ThemeParseError(WIDGETS_JSON, None, f"widgets.json sizes[] is missing {missing}")
    return {
        "columns": {"compact": columns["compact"], "regular": columns["regular"]},
        "regularBreakpointPx": 834,  # hand-authored: iPad-portrait width; iOS has no pixel
                                     # equivalent for `.regular` (see module docstring).
        "estimatedHeight": {key: sizes[key]["height"] for key in order},
        "spanCompact": {key: sizes[key]["columnsCompact"] for key in order},
        "spanRegular": {key: sizes[key]["columnsRegular"] for key in order},
        "gap": {},  # filled in by build_theme() once `spacing` is known.
    }


# --------------------------------------------------------------------------- assembly


def build_theme() -> dict[str, Any]:
    theme_text = _read(THEME_SWIFT)
    broadsheet_text = _read(BROADSHEET_SWIFT)
    typography_text = _read(TYPOGRAPHY_SWIFT)
    layout_text = _read(LAYOUT_SWIFT)

    chart_series, chart_consumed = parse_array(THEME_SWIFT, theme_text, "chartSeries", 8)
    monogram_palette, monogram_consumed = parse_array(
        THEME_SWIFT, theme_text, "monogramPalette", 12
    )
    palette = parse_palette(THEME_SWIFT, theme_text, chart_consumed | monogram_consumed)

    accent_names = _accent_names(LAYOUT_SWIFT, layout_text)
    if len(accent_names) != 9:
        raise ThemeParseError(
            LAYOUT_SWIFT, None, f"expected 9 AccentName cases, found {len(accent_names)}"
        )
    accent_colors = _parse_accent_switch(THEME_SWIFT, theme_text, "color", accent_names)
    accent_soft = _parse_accent_switch(THEME_SWIFT, theme_text, "softTint", accent_names)
    accents: dict[str, Any] = {}
    for name in accent_names:
        base, soft = accent_colors[name], accent_soft[name]
        accents[name] = {
            "light": base["light"],
            "dark": base["dark"],
            "softLightAlpha": soft.get("lightAlpha", 1.0),
            "softDarkAlpha": soft.get("darkAlpha", 1.0),
        }

    shadow = parse_card_shadow(THEME_SWIFT, theme_text)
    spacing = parse_scalars(THEME_SWIFT, theme_text, "Spacing", SPACING_KEYS)
    radius = parse_scalars(THEME_SWIFT, theme_text, "Radius", RADIUS_KEYS)
    broadsheet = parse_broadsheet(BROADSHEET_SWIFT, broadsheet_text)
    text_styles = parse_text_styles(TYPOGRAPHY_SWIFT, typography_text)

    grid = load_grid()
    grid["gap"] = {"tiles": spacing["md"], "broadsheet": spacing["xl"]}

    document = {
        "schemaVersion": 1,
        "source": {
            "theme": _sha256(THEME_SWIFT),
            "broadsheet": _sha256(BROADSHEET_SWIFT),
            "typography": _sha256(TYPOGRAPHY_SWIFT),
            "layout": _sha256(LAYOUT_SWIFT),
        },
        "palette": palette,
        "chartSeries": chart_series,
        "monogramPalette": monogram_palette,
        "accents": accents,
        "broadsheet": broadsheet,
        "spacing": spacing,
        "radius": radius,
        "shadow": shadow,
        "textStyles": text_styles,
        "grid": grid,
        "card": {"padding": spacing["md"], "radius": radius["card"]},
    }
    return document


def main() -> int:
    try:
        document = build_theme()
    except ThemeParseError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
