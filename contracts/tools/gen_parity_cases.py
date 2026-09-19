#!/usr/bin/env python3
"""Emit the three fixture files that pin cross-language behaviour byte-for-byte.

Three small pieces of logic exist in more than one language on purpose (WEB_DESIGN.md §0.1,
§7.4, §8.3), and each duplication is a place a rewrite in one language can silently stop agreeing
with the others:

* the FNV-1a monogram fold (`Palette.monogramColor(for:)` in `Theme.swift`, ported to TypeScript
  as `fnv.ts` and to nothing on the backend — it never leaves the client);
* the eight `MetricFormat` rendering rules (`Formatting.swift` on iOS, `format.ts` on the web,
  `nbastats.catalog.format_value` on the backend);
* the dashboard-layout migration rules (`LayoutMigrator.swift` on iOS, `nbastats.accounts.layouts`
  on the backend once WP2 lands it, a TypeScript port only if the web ever edits a raw layout
  document directly rather than through the API).

A fixture file that a human transcribes from the design doc is a fourth place these numbers can
drift, so this generator computes every case's expected output from a real implementation
instead of typing it out by hand:

* monogram cases run the *same* FNV-1a fold this file defines (a straight, byte-for-byte port of
  the four-line Swift function — there is no faster-varying "real" implementation to import for
  a pure function this small, so the port itself is the thing pinned, and it is transcribed once,
  here, rather than copied a second time into a test);
* format cases call `nbastats.catalog.format_value` directly (`backend/nbastats/catalog.py`,
  already shipped and unrelated to the accounts work) — so whatever that function actually does
  today is what gets pinned, not what this script's author remembered it doing;
* layout-migration cases run a small reference port of `LayoutMigrator.swift`'s *schema-level*
  rules (Tier 1 — `nbastats.accounts.layouts` does not exist yet; WP2 builds it against this same
  fixture) and the real `nbastats.catalog` widget specs for the *catalog-level* rules (Tier 2 —
  `widget_config_defaults`, `widget`, both already shipped), so Tier 2's dropped/filled-key notes
  come from the actual field lists in `contracts/widgets.json`, not a second copy of them.

Usage: ``python3 contracts/tools/gen_parity_cases.py`` writes the three files straight into
``contracts/fixtures/``. ``--out DIR`` writes them into ``DIR`` instead, for
``scripts/check_contracts.py`` check (i) — see ``gen_web_tokens.py``'s docstring for why.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
CONTRACTS = ROOT / "contracts"
DEFAULT_OUT_DIR = CONTRACTS / "fixtures"
EM_DASH = "—"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from nbastats import catalog  # noqa: E402  (path insert above must run first)


# =========================================================================== format_cases.json

#: One representative value per branch of `Formatting.value(_:format:)` (and its exact Python
#: analogue, `nbastats.catalog.format_value`): every format at least once, plus `null`. Values are
#: chosen with an exact decimal representation at the format's own precision (`7.8` for a
#: one-decimal format, `61.55` for two decimals, and so on) specifically so no rounding mode is
#: ever exercised — Python's `Decimal` and Swift's `NumberFormatter` are not guaranteed to round
#: `x.xx5` the same way, and that disagreement is not what this fixture is for.
#:
#: `plusMinus1` at exactly `0` is pinned deliberately, and it is the one case here that changed an
#: implementation rather than describing it. Swift has always suppressed the sign at zero
#: (`Formatting.swift`'s `signed: value != 0`, "a sign reads as noise"), while
#: `nbastats.catalog.format_value` signed every value the spec marks `signed` — so a plus/minus of
#: exactly zero reached the reader as `"+0.0"` from the server and `"0.0"` from the iOS formatter,
#: in the same app. Swift was right: zero is neither positive nor negative, and `"+0.0"` asserts a
#: direction the number does not have. Python now matches, and this case is what keeps them
#: matching. Both signs of zero are covered, because `-0.0` must not survive as `"-0.0"` either.
FORMAT_VALUE_CASES: tuple[tuple[str, float | None], ...] = (
    ("integer", 82),
    ("integer", 0),
    ("integer", None),
    ("decimal1", 27.8),
    ("decimal1", 0.0),
    ("decimal1", None),
    ("decimal2", 61.55),
    ("decimal2", None),
    ("percent1", 0.615),
    ("percent1", 0.0),
    ("percent1", None),
    ("percent2", 0.6155),
    ("percent2", None),
    ("rating1", 118.5),
    ("rating1", None),
    ("plusMinus1", 7.8),
    ("plusMinus1", -7.8),
    ("plusMinus1", 0.0),
    ("plusMinus1", -0.0),
    ("plusMinus1", None),
    ("minutes", 36.2),
    ("minutes", None),
)


def build_format_cases() -> dict[str, Any]:
    cases = []
    for format_key, value in FORMAT_VALUE_CASES:
        cases.append(
            {
                "format": format_key,
                "value": value,
                "expected": catalog.format_value(format_key, value),
            }
        )
    return {
        "schemaVersion": 1,
        "emDash": EM_DASH,
        "source": "backend/nbastats/catalog.py::format_value (values chosen to match "
        "ios/NBAStats/Core/Formatting.swift's eight MetricFormat branches)",
        "cases": cases,
    }


# ========================================================================== monogram_cases.json


def fnv1a(text: str, modulo: int) -> int:
    """A direct, byte-for-byte port of `Palette.monogramColor(for:)` (`Theme.swift`):

    ```swift
    var hash: UInt32 = 2_166_136_261                 // 0x811C9DC5, the FNV-1a 32-bit offset basis
    for scalar in text.uppercased().unicodeScalars {
        hash = (hash ^ (scalar.value & 0xFF)) &* 16_777_619   // 0x01000193, the FNV-1a 32-bit prime
    }
    return Int(hash % UInt32(monogramPalette.count))
    ```

    Python's `for ch in text` already iterates Unicode code points, exactly like Swift's
    `.unicodeScalars` (neither ever yields a UTF-16 surrogate half), so `ord(ch)` is `scalar.value`
    with no further translation needed; `& 0xFFFFFFFF` after each multiply is the same wraparound
    Swift's `&*` performs explicitly.
    """
    hash_value = 0x811C9DC5
    for ch in text.upper():
        hash_value = ((hash_value ^ (ord(ch) & 0xFF)) * 0x01000193) & 0xFFFFFFFF
    return hash_value % modulo


#: `contracts/fixtures/teams.json` is generated from the same 30-team catalog the app ships with;
#: reading it here (rather than retyping 30 abbreviations) means a future expansion team is
#: covered the moment its fixture lands, with no edit to this file.
def _team_abbreviations() -> list[str]:
    document = json.loads((CONTRACTS / "fixtures" / "teams.json").read_text(encoding="utf-8"))
    return [team["abbr"] for team in document["teams"]]


#: `PlayerAvatar` folds `"player" + playerId`, never the player's name (WEB_DESIGN.md §7.4.17), so
#: the *shape* of a player key is a fixed prefix plus digits, not free text. Real player ids are a
#: sparse, non-sequential range assigned by the league; this fixture does not depend on any one of
#: them existing in this checkout's data, so it covers the shape with a plain, clearly-synthetic
#: sequence instead. What is under test is the fold, which only ever sees bytes — a real id would
#: exercise nothing this sequence does not.
_SYNTHETIC_PLAYER_IDS = tuple(range(1, 41))


def build_monogram_cases() -> dict[str, Any]:
    modulo = 12  # len(Palette.monogramPalette) — asserted at 12 by gen_theme.py's own checks.
    team_cases = [
        {"key": abbr, "kind": "team", "index": fnv1a(abbr, modulo)}
        for abbr in _team_abbreviations()
    ]
    player_cases = [
        {"key": f"player{pid}", "kind": "player", "index": fnv1a(f"player{pid}", modulo)}
        for pid in _SYNTHETIC_PLAYER_IDS
    ]
    return {
        "schemaVersion": 1,
        "modulo": modulo,
        "algorithm": "FNV-1a-32 (offset 0x811C9DC5, prime 0x01000193) folded over "
        "text.uppercased() bytes, then hash % modulo",
        "source": "ios/NBAStats/DesignSystem/Theme.swift::Palette.monogramColor(for:)",
        "cases": team_cases + player_cases,
    }


# =================================================================== layout_migration_cases.json

CURRENT_SCHEMA_VERSION = 1
KNOWN_WIDGET_KINDS = tuple(catalog.widget_kinds())
KNOWN_SIZES = ("small", "medium", "large")
KNOWN_ACCENTS = (
    "orange", "indigo", "teal", "red", "amber", "green", "blue", "purple", "graphite",
)
DEFAULT_ACCENT = "orange"
DEFAULT_SIZE = "medium"
#: Printed in place of a freshly minted uuid. Both tiers of the reference port below mint a real
#: one internally (so two colliding ids really do compare unequal, the way `UUID().uuidString`
#: does), then this sentinel is substituted into the JSON that ships — a literal random string
#: would just be one more thing a future diff has to explain.
GENERATED = "<generated>"


def _list_phrase(items: list[str]) -> str:
    """`listPhrase` (`LayoutMigrator.swift`): `["a"]` -> `"a"`, `["a","b","c"]` -> `"a", "b" and
    "c"` — curly-quoted per item, no Oxford comma. Matches `LayoutMigrator.swift`'s private
    `listPhrase`, including the quote character (U+201C/U+201D, not ASCII `"`)."""
    quoted = [f"“{item}”" for item in items]
    if not quoted:
        return ""
    if len(quoted) == 1:
        return quoted[0]
    return ", ".join(quoted[:-1]) + " and " + quoted[-1]


class Tier1Error(Exception):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def tier1_migrate(
    raw: dict[str, Any] | None, *, uuid_source: list[str]
) -> tuple[dict[str, Any], list[str]]:
    """A line-for-line port of `LayoutMigrator.migrate(_ document:)` (`LayoutMigrator.swift`
    lines 166-240) — the schema-level tier, which needs no widget catalog. ``raw`` is the already
    -decoded JSON object (``None``/non-dict inputs are handled by the caller: that is the
    ``UnreadableLayout`` / ``NotALayout`` split, which happens before this function is reached in
    the real ``migrateResult(_:)``). ``uuid_source`` supplies fresh ids in call order, standing in
    for `UUID().uuidString` so a fixture case is reproducible.
    """
    found_version = raw.get("schemaVersion", CURRENT_SCHEMA_VERSION)
    if found_version is None:
        found_version = CURRENT_SCHEMA_VERSION
    if found_version > CURRENT_SCHEMA_VERSION:
        raise Tier1Error(
            "LayoutTooNew",
            f"This dashboard was made with a newer version of Hardwood (format {found_version}; "
            f"this build reads {CURRENT_SCHEMA_VERSION}). Update the app to edit it.",
        )
    if raw.get("name") is None and raw.get("widgets") is None and raw.get("id") is None:
        raise Tier1Error("NotALayout", "This file does not contain a dashboard.")

    notes: list[str] = []
    widgets: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    next_uuid = iter(uuid_source)

    for raw_widget in raw.get("widgets") or []:
        kind_raw = raw_widget.get("kind")
        if kind_raw is None:
            notes.append("Removed a widget that had no type.")
            continue
        if kind_raw not in KNOWN_WIDGET_KINDS:
            notes.append(
                f"Removed a “{kind_raw}” widget: this version of Hardwood does not "
                "have that widget."
            )
            continue

        size_raw = raw_widget.get("size")
        if size_raw is not None:
            if size_raw in KNOWN_SIZES:
                size = size_raw
            else:
                size = DEFAULT_SIZE
                title_or_fallback = raw_widget.get("title") or kind_raw
                notes.append(
                    f"“{title_or_fallback}”: the size “{size_raw}” is unknown, "
                    "so it is medium now."
                )
        else:
            size = DEFAULT_SIZE

        widget_id = raw_widget.get("id") or next(next_uuid)
        if widget_id in used_ids:
            widget_id = next(next_uuid)
            notes.append("Two widgets shared an id; one of them was given a new one.")
        used_ids.add(widget_id)

        widgets.append(
            {
                "id": widget_id,
                "kind": kind_raw,
                "title": raw_widget.get("title"),
                "size": size,
                "config": raw_widget.get("config") or {},
            }
        )

    accent_raw = raw.get("accent")
    accent = accent_raw if accent_raw in KNOWN_ACCENTS else DEFAULT_ACCENT

    layout = {
        "id": raw.get("id") or next(next_uuid),
        "name": raw.get("name") or "Dashboard",
        "icon": raw.get("icon") or "square.grid.2x2",
        "accent": accent,
        "schemaVersion": CURRENT_SCHEMA_VERSION,
        "isPreset": raw.get("isPreset") or False,
        "presetKey": raw.get("presetKey"),
        "tagline": raw.get("tagline"),
        "widgets": widgets,
    }
    if found_version < CURRENT_SCHEMA_VERSION:
        notes.append(
            f"Updated this dashboard from format {found_version} to {CURRENT_SCHEMA_VERSION}."
        )
    return layout, notes


def tier2_normalize(
    widgets: list[dict[str, Any]], *, catalog_kinds: tuple[str, ...] | None = None
) -> tuple[list[dict[str, Any]], list[str]]:
    """A port of `LayoutMigrator.normalize(_:catalog:)` (`LayoutMigrator.swift` lines 125-162),
    using the real `nbastats.catalog` widget specs (`widget_config_defaults`, `widget`) for the
    field lists a Swift `Catalog` object would otherwise supply. ``catalog_kinds`` narrows which
    kinds are considered known, for the one test case that needs a kind the real catalog still
    has but a *hypothetically smaller* one no longer does — see the case below named
    ``kind_removed_from_catalog``.
    """
    available = set(catalog_kinds) if catalog_kinds is not None else set(KNOWN_WIDGET_KINDS)
    notes: list[str] = []
    kept: list[dict[str, Any]] = []

    for widget in widgets:
        kind = widget["kind"]
        title = widget.get("title")
        if kind not in available:
            notes.append(
                f"Removed “{title or kind}”: this version of Hardwood no longer has "
                "that widget."
            )
            continue

        spec = catalog.widget(kind)
        field_keys = {field["key"] for field in spec["config"]}
        defaults = catalog.widget_config_defaults(kind)
        raw_config = widget.get("config") or {}

        normalized: dict[str, Any] = {}
        for key in field_keys:
            if key in raw_config and raw_config[key] is not None:
                normalized[key] = raw_config[key]
            elif defaults.get(key) is not None:
                normalized[key] = defaults[key]
            elif key in raw_config:
                normalized[key] = None  # explicit null, no default: kept, not filled.

        dropped = sorted(key for key in raw_config if key not in normalized)
        if dropped:
            notes.append(
                f"“{title or spec['name']}”: removed {_list_phrase(dropped)}, which "
                "this widget no longer uses."
            )
        filled = sorted(key for key in normalized if key not in raw_config)
        if filled:
            notes.append(
                f"“{title or spec['name']}”: filled in {_list_phrase(filled)} from the "
                "defaults."
            )

        updated = dict(widget, config=normalized)
        sizes = spec["sizes"]
        if widget["size"] not in sizes:
            default_size = spec["defaultSize"]
            notes.append(
                f"“{title or spec['name']}”: resized to {default_size.capitalize()}, "
                "the only size it supports now."
            )
            updated["size"] = default_size
        kept.append(updated)

    return kept, notes


def _tier1_case(
    name: str, source_line: str, raw: dict[str, Any], *, uuid_count: int = 4
) -> dict[str, Any]:
    uuid_source = [f"{GENERATED}-{index}" for index in range(uuid_count)]
    try:
        layout, notes = tier1_migrate(raw, uuid_source=uuid_source)
    except Tier1Error as error:
        return {
            "name": name,
            "sourceLine": source_line,
            "input": raw,
            "outcome": "error",
            "error": {"type": error.error_type, "message": error.message},
            "notes": [],
            "expect": None,
        }
    return {
        "name": name,
        "sourceLine": source_line,
        "input": raw,
        "outcome": "migrated",
        "error": None,
        "notes": notes,
        "expect": {
            "schemaVersion": layout["schemaVersion"],
            "accent": layout["accent"],
            "widgetCount": len(layout["widgets"]),
            "widgets": [
                {"id": w["id"], "kind": w["kind"], "size": w["size"]} for w in layout["widgets"]
            ],
        },
    }


def build_tier1_cases() -> list[dict[str, Any]]:
    return [
        _tier1_case(
            "schema_version_too_new",
            "LayoutMigrator.swift:168-170",
            {"schemaVersion": 2, "id": "b1", "name": "Future Board", "widgets": []},
        ),
        _tier1_case(
            "schema_version_upgraded_silently_with_a_note",
            "LayoutMigrator.swift:236-238",
            {"schemaVersion": 0, "id": "b1", "name": "Old Board", "widgets": []},
        ),
        _tier1_case(
            "not_a_layout_when_name_widgets_and_id_are_all_absent",
            "LayoutMigrator.swift:171-173",
            {"tagline": "just a stray tagline, nothing else"},
        ),
        {
            "name": "unreadable_bytes_are_not_json_at_all",
            "sourceLine": "LayoutMigrator.swift:79-86",
            "input": "{not valid json",
            "inputIsRawText": True,
            "outcome": "error",
            "error": {
                "type": "UnreadableLayout",
                "message": "This dashboard file could not be read.",
            },
            "notes": [],
            "expect": None,
        },
        _tier1_case(
            "widget_with_no_kind_is_dropped",
            "LayoutMigrator.swift:180-183",
            {
                "id": "b1", "name": "Board", "widgets": [
                    {"id": "w1", "title": "Mystery Tile"},
                ],
            },
        ),
        _tier1_case(
            "widget_with_an_unknown_kind_is_dropped",
            "LayoutMigrator.swift:184-187",
            {
                "id": "b1", "name": "Board", "widgets": [
                    {"id": "w1", "kind": "holo_projector", "title": "Holo"},
                ],
            },
        ),
        _tier1_case(
            "widget_with_an_unrecognised_size_falls_back_to_medium_with_a_note",
            "LayoutMigrator.swift:189-199",
            {
                "id": "b1", "name": "Board", "widgets": [
                    {"id": "w1", "kind": "stat_tile", "title": "Points", "size": "jumbo"},
                ],
            },
        ),
        _tier1_case(
            "duplicate_widget_ids_the_second_gets_a_fresh_one",
            "LayoutMigrator.swift:201-206",
            {
                "id": "b1", "name": "Board", "widgets": [
                    {"id": "dup", "kind": "stat_tile", "title": "First"},
                    {"id": "dup", "kind": "leaderboard", "title": "Second"},
                ],
            },
        ),
        _tier1_case(
            "missing_widget_id_gets_a_fresh_one_silently",
            "LayoutMigrator.swift:201",
            {
                "id": "b1", "name": "Board", "widgets": [
                    {"kind": "stat_tile", "title": "No Id"},
                ],
            },
        ),
        _tier1_case(
            "unrecognised_accent_falls_back_to_orange_silently",
            "LayoutMigrator.swift:215-220",
            {"id": "b1", "name": "Board", "accent": "neon_pink", "widgets": []},
        ),
        _tier1_case(
            "a_well_formed_layout_migrates_with_no_notes_at_all",
            "LayoutMigrator.swift:166-240",
            {
                "id": "b1", "name": "Board", "accent": "teal", "schemaVersion": 1,
                "widgets": [{"id": "w1", "kind": "stat_tile", "title": "Points", "size": "small"}],
            },
        ),
    ]


def build_tier2_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    legacy_widget = {
        "kind": "shot_chart_legacy", "title": "Old Shot Chart", "size": "medium", "config": {},
    }
    kept, notes = tier2_normalize(
        [legacy_widget],
        catalog_kinds=("stat_tile", "leaderboard"),  # a hypothetically smaller live catalog
    )
    cases.append(
        {
            "name": "kind_removed_from_catalog",
            "sourceLine": "LayoutMigrator.swift:132-136",
            "note": "Uses a narrowed catalogKinds (not the real 16) to exercise a kind the "
            "schema still allows but a live catalog has stopped serving.",
            "catalogKinds": ["stat_tile", "leaderboard"],
            "input": [legacy_widget],
            "notes": notes,
            "expect": {"widgetCount": len(kept)},
        }
    )

    raw_widget = {
        "id": "w1", "kind": "four_factors", "title": "Hawks Defense", "size": "medium",
        "config": {"teamId": 1610612737, "season": "latest", "extraJunk": True},
    }
    kept, notes = tier2_normalize([raw_widget])
    cases.append(
        {
            "name": "unknown_config_key_dropped_and_missing_keys_filled_from_defaults",
            "sourceLine": "LayoutMigrator.swift:139-149",
            "input": [raw_widget],
            "notes": notes,
            "expect": {"config": kept[0]["config"]},
        }
    )

    raw_widget = {
        "id": "w1", "kind": "four_factors", "title": "Hawks Defense", "size": "small",
        "config": {"teamId": 1610612737, "season": "latest", "seasonType": "Regular Season",
                   "showOpponent": True, "comparison": "league"},
    }
    kept, notes = tier2_normalize([raw_widget])
    cases.append(
        {
            "name": "size_not_offered_by_the_kind_resizes_to_the_default_with_a_note",
            "sourceLine": "LayoutMigrator.swift:151-154",
            "input": [raw_widget],
            "notes": notes,
            "expect": {"size": kept[0]["size"]},
        }
    )

    raw_widget = {
        "id": "w1", "kind": "four_factors", "title": "Hawks Defense", "size": "medium",
        "config": {"teamId": 1610612737, "season": "latest", "seasonType": "Regular Season",
                   "showOpponent": True, "comparison": "league"},
    }
    kept, notes = tier2_normalize([raw_widget])
    cases.append(
        {
            "name": "a_widget_that_already_matches_the_catalog_gets_no_notes",
            "sourceLine": "LayoutMigrator.swift:128-162",
            "input": [raw_widget],
            "notes": notes,
            "expect": {"config": kept[0]["config"], "size": kept[0]["size"]},
        }
    )

    return cases


def build_layout_migration_cases() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "currentSchemaVersion": CURRENT_SCHEMA_VERSION,
        "generatedIdSentinel": GENERATED,
        "source": "ios/NBAStats/Core/LayoutMigrator.swift",
        "tier1": build_tier1_cases(),
        "tier2": build_tier2_cases(),
    }


# --------------------------------------------------------------------------- entry point


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    out_dir = DEFAULT_OUT_DIR
    if args and args[0] == "--out":
        out_dir = Path(args[1])
    out_dir.mkdir(parents=True, exist_ok=True)

    documents = {
        "format_cases.json": build_format_cases(),
        "monogram_cases.json": build_monogram_cases(),
        "layout_migration_cases.json": build_layout_migration_cases(),
    }
    for filename, document in documents.items():
        text = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        (out_dir / filename).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
