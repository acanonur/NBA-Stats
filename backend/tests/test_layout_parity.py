"""Every case in ``contracts/fixtures/layout_migration_cases.json``, replayed against the real
``nbastats.accounts.layouts`` module.

The fixture is generated (``contracts/tools/gen_parity_cases.py``) from a small *reference* port
of ``LayoutMigrator.swift`` kept in that generator, independently of this module — the same
independence ``ios/NBAStatsTests/LayoutParityTests.swift`` has from the real Swift migrator. That
is deliberate: the fixture pins the *rules*, and this test is what proves ``accounts/layouts.py``
implements them, rather than proving the generator agrees with itself.

Freshly generated ids (a fresh ``uuid4().hex`` where the fixture prints the sentinel from
``generatedIdSentinel``, e.g. ``"<generated>-0"``) can never compare equal byte-for-byte to a
real implementation's output — the whole point of a fresh id is that it was not supplied — so
:func:`_assert_widget_ids_match` treats any expected id starting with that sentinel as "must be a
non-empty string this case's input never supplied", and compares everything else exactly.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nbastats.accounts import layouts

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "layout_migration_cases.json"
)

ERROR_TYPES = {
    "LayoutTooNew": layouts.LayoutTooNew,
    "NotALayout": layouts.NotALayout,
    "UnreadableLayout": layouts.UnreadableLayout,
}


@pytest.fixture(scope="module")
def fixture() -> dict[str, Any]:
    assert FIXTURE_PATH.is_file(), f"{FIXTURE_PATH} is missing; run scripts/sync_contracts.sh"
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _input_widget_ids(raw_input: Any) -> set[str]:
    ids: set[str] = set()
    if isinstance(raw_input, dict):
        for widget in raw_input.get("widgets") or []:
            if isinstance(widget, dict) and isinstance(widget.get("id"), str):
                ids.add(widget["id"])
    return ids


def _assert_widget_ids_match(
    sentinel: str, expected_widgets: list[dict[str, Any]], actual_widgets: list[dict[str, Any]],
    supplied_ids: set[str],
) -> None:
    assert len(expected_widgets) == len(actual_widgets)
    for expected, actual in zip(expected_widgets, actual_widgets):
        assert actual["kind"] == expected["kind"]
        assert actual["size"] == expected["size"]
        if expected["id"].startswith(sentinel):
            assert isinstance(actual["id"], str) and actual["id"]
            assert actual["id"] not in supplied_ids, (
                f"widget id {actual['id']!r} was supposed to be freshly generated, but it "
                "matches an id the input already used"
            )
        else:
            assert actual["id"] == expected["id"]


def test_tier1_current_schema_version_matches_the_fixture(fixture: dict[str, Any]) -> None:
    assert layouts.CURRENT_SCHEMA_VERSION == fixture["currentSchemaVersion"]


def test_every_tier1_case(fixture: dict[str, Any]) -> None:
    sentinel = fixture["generatedIdSentinel"]
    cases = fixture["tier1"]
    assert cases, "the fixture carries no tier-1 cases"

    for case in cases:
        raw_input = case["input"]
        if case["outcome"] == "error":
            with pytest.raises(ERROR_TYPES[case["error"]["type"]]) as excinfo:
                layouts.migrate_document(raw_input)
            assert str(excinfo.value) == case["error"]["message"], case["name"]
            continue

        result = layouts.migrate_document(raw_input)
        assert result.notes == case["notes"], case["name"]

        expect = case["expect"]
        assert result.layout["schemaVersion"] == expect["schemaVersion"], case["name"]
        assert result.layout["accent"] == expect["accent"], case["name"]
        # `createdAt`/`updatedAt` are carried through, never invented: `LayoutMigrator.swift`
        # passes both straight into the layout it builds and `DashboardLayout.encode` writes
        # them back out, so a round trip iOS -> web -> export -> iOS must not lose them.
        assert result.layout.get("createdAt") == expect["createdAt"], case["name"]
        assert result.layout.get("updatedAt") == expect["updatedAt"], case["name"]
        assert len(result.layout["widgets"]) == expect["widgetCount"], case["name"]
        _assert_widget_ids_match(
            sentinel,
            expect["widgets"],
            [
                {"id": w["id"], "kind": w["kind"], "size": w["size"]}
                for w in result.layout["widgets"]
            ],
            _input_widget_ids(raw_input),
        )


def test_every_tier2_case(fixture: dict[str, Any]) -> None:
    cases = fixture["tier2"]
    assert cases, "the fixture carries no tier-2 cases"

    for case in cases:
        layout = {
            "id": "layout-under-test",
            "name": "Board",
            "icon": layouts.DEFAULT_ICON,
            "accent": layouts.DEFAULT_ACCENT,
            "presentation": layouts.DEFAULT_PRESENTATION,
            "schemaVersion": layouts.CURRENT_SCHEMA_VERSION,
            "isPreset": False,
            "presetKey": None,
            "tagline": None,
            "widgets": case["input"],
        }
        available_kinds = case.get("catalogKinds")
        result = layouts.normalize(layout, available_kinds=available_kinds)
        assert result.notes == case["notes"], case["name"]

        expect = case["expect"]
        if "widgetCount" in expect:
            assert len(result.layout["widgets"]) == expect["widgetCount"], case["name"]
        if "config" in expect:
            assert result.layout["widgets"][0]["config"] == expect["config"], case["name"]
        if "size" in expect:
            assert result.layout["widgets"][0]["size"] == expect["size"], case["name"]


def test_migrate_and_normalize_runs_both_tiers_in_order() -> None:
    raw = {
        "id": "b1",
        "name": "Board",
        "widgets": [
            {
                "id": "w1",
                "kind": "four_factors",
                "title": "Hawks Defense",
                "size": "small",
                "config": {"teamId": 1610612737, "season": "latest", "extraJunk": True},
            }
        ],
    }
    result = layouts.migrate_and_normalize(raw)
    # Tier 2 resized it (small isn't offered) and dropped/filled its config; tier 1 contributed
    # no notes of its own here, so every note present came from tier 2, in tier-2's order.
    assert any("resized to Medium" in note for note in result.notes)
    assert any("extraJunk" in note for note in result.notes)
    widget = result.layout["widgets"][0]
    assert widget["size"] == "medium"
    assert "extraJunk" not in widget["config"]


def test_migrate_collection_accepts_the_three_ios_shapes() -> None:
    single = {"id": "b1", "name": "Solo Board", "widgets": []}
    bare_array = [
        {"id": "b1", "name": "First", "widgets": []},
        {"id": "b2", "name": "Second", "widgets": []},
    ]
    envelope = {"schemaVersion": 1, "updatedAt": "2026-01-01T00:00:00Z", "layouts": bare_array}

    results, failures = layouts.migrate_collection(single)
    assert len(results) == 1 and not failures

    results, failures = layouts.migrate_collection(bare_array)
    assert len(results) == 2 and not failures

    results, failures = layouts.migrate_collection(envelope)
    assert len(results) == 2 and not failures


def test_migrate_collection_keeps_the_rest_when_one_layout_is_corrupt() -> None:
    collection = [
        {"id": "b1", "name": "Good Board", "widgets": []},
        {"schemaVersion": 99, "id": "b2", "name": "Too New", "widgets": []},
        {"tagline": "not a layout at all"},
        "{not even json",
    ]
    results, failures = layouts.migrate_collection(collection)
    assert len(results) == 1
    assert len(failures) == 3


def test_canonical_json_matches_swifts_sorted_keys_output() -> None:
    layout = {"b": 1, "a": [3, 2, 1], "c": {"y": 1, "x": 2}}
    assert layouts.canonical_json(layout) == '{"a":[3,2,1],"b":1,"c":{"x":2,"y":1}}'


def test_fork_preset_mints_fresh_ids_and_retains_the_preset_key() -> None:
    from nbastats import catalog

    preset = catalog.preset("daily_recap")
    original_widget_ids = {w["id"] for w in preset["widgets"]}

    layout, id_map = layouts.fork_preset(preset)

    assert layout["id"] not in (preset.get("id"),)
    assert layout["isPreset"] is False
    assert layout["presetKey"] == "daily_recap"
    assert len(layout["widgets"]) == len(preset["widgets"])
    assert set(id_map) == original_widget_ids
    new_ids = {w["id"] for w in layout["widgets"]}
    assert new_ids == set(id_map.values())
    assert new_ids.isdisjoint(original_widget_ids)
    # Every widget's own contents (kind, config, size) survive the fork untouched.
    for old, new in zip(preset["widgets"], layout["widgets"]):
        assert old["kind"] == new["kind"]
        assert old.get("config") == new.get("config")


def test_seed_layout_for_new_user_is_a_preset_shaped_fork() -> None:
    layout = layouts.seed_layout_for_new_user()
    assert layout["isPreset"] is True
    assert layout["presetKey"] == "daily_recap"
    assert layout["widgets"], "a new user's seed dashboard should not be empty"


def test_a_document_with_no_recognisable_dashboard_field_is_not_a_layout() -> None:
    with pytest.raises(layouts.NotALayout):
        layouts.migrate_document([1, 2, 3])
    with pytest.raises(layouts.NotALayout):
        layouts.migrate_document(None)


def test_unreadable_bytes_raise_before_not_a_layout() -> None:
    with pytest.raises(layouts.UnreadableLayout):
        layouts.migrate_document("{not valid json")


def test_fallback_names_match_the_catalog() -> None:
    """``layouts.FALLBACK_WIDGET_NAMES`` is a transcription of Swift's compile-time
    ``WidgetKind.fallbackName`` table, which tier 1 needs precisely because tier 1 is
    catalog-free. It must nevertheless agree with ``contracts/widgets.json`` for every kind the
    catalog ships, or the same widget would read one way in a tier-1 note and another way in a
    tier-2 note — the bug this table was added to fix."""
    from nbastats import catalog

    for kind in catalog.widget_kinds():
        assert layouts.FALLBACK_WIDGET_NAMES.get(kind) == catalog.widget(kind)["name"], kind
    assert set(layouts.FALLBACK_WIDGET_NAMES) == set(catalog.widget_kinds())


def test_a_non_integer_schema_version_cannot_defeat_the_ratchet() -> None:
    """``2.0`` is a perfectly legal JSON number and ``"2"`` is what a re-serialiser can emit;
    neither may be treated as "no version at all" and stamped down to the current one, which is
    exactly the silent corruption of a newer build's fields the ratchet exists to prevent."""
    with pytest.raises(layouts.LayoutTooNew):
        layouts.migrate_document({"id": "b", "name": "N", "schemaVersion": 2.0, "widgets": []})
    # Swift's `RawLayout.schemaVersion` is an `Int?`, so a string or a fractional number is a
    # `JSONDecoder` failure, which `migrateResult(_:)` reports as `.unreadable`.
    with pytest.raises(layouts.UnreadableLayout):
        layouts.migrate_document({"id": "b", "name": "N", "schemaVersion": "2", "widgets": []})
    with pytest.raises(layouts.UnreadableLayout):
        layouts.migrate_document({"id": "b", "name": "N", "schemaVersion": 1.5, "widgets": []})
    # A boolean is an `int` subclass in Python and must not sneak past as version 1.
    with pytest.raises(layouts.UnreadableLayout):
        layouts.migrate_document({"id": "b", "name": "N", "schemaVersion": True, "widgets": []})


def test_the_ratchet_is_checked_before_the_is_this_a_layout_test() -> None:
    """``LayoutMigrator.swift:168-173`` applies ``tooNew`` first and ``notALayout`` second. A
    document that is both has to report the same error on both clients."""
    with pytest.raises(layouts.LayoutTooNew):
        layouts.migrate_document({"schemaVersion": 2, "tagline": "nothing layout-shaped here"})
