import Foundation
import XCTest
@testable import Hardwood

/// The editing rules a reader actually notices: a preset is never edited in place, every change
/// is written immediately, one change can be taken back, and deleting the last dashboard leaves
/// them with a working one rather than an empty screen.
@MainActor
final class DashboardStoreTests: XCTestCase {

    private func makeStore(layouts: [DashboardLayout]) -> (store: DashboardStore,
                                                           persistence: InMemoryLayoutPersistence,
                                                           catalog: Catalog) {
        let catalog = makeTestCatalog()
        let persistence = InMemoryLayoutPersistence(layouts: layouts)
        let store = DashboardStore(persistence: persistence,
                                   catalog: catalog,
                                   defaults: makeTestDefaults("hardwood.store.tests"))
        return (store, persistence, catalog)
    }

    private func presetLayout() -> DashboardLayout {
        TestLayouts.layout(id: "preset.daily_recap",
                           name: "Daily Recap",
                           isPreset: true,
                           presetKey: "daily_recap",
                           widgets: [
                               TestLayouts.widget(id: "p1", kind: .scoreboard, size: .large),
                               TestLayouts.widget(id: "p2", kind: .statTile, size: .small)
                           ])
    }

    private func editableLayout(id: String = "mine", name: String = "Mine") -> DashboardLayout {
        TestLayouts.layout(id: id,
                           name: name,
                           isPreset: false,
                           widgets: [TestLayouts.widget(id: "w1", kind: .statTile, size: .small)])
    }

    // MARK: - Loading

    func testAnEmptyStoreSeedsItselfAndWritesTheSeedBack() throws {
        let catalog = makeTestCatalog()
        try XCTSkipIf(catalog.presets.isEmpty, "The bundled presets are not available")
        let persistence = InMemoryLayoutPersistence(layouts: [])
        let store = DashboardStore(persistence: persistence,
                                   catalog: catalog,
                                   defaults: makeTestDefaults("hardwood.store.seed"))

        XCTAssertEqual(store.layouts.count, 1)
        XCTAssertFalse(store.layouts[0].isPreset)
        XCTAssertEqual(store.selectedLayoutID, store.layouts[0].id)
        XCTAssertGreaterThan(persistence.saveCount, 0, "The seeded dashboard was not written back")
    }

    func testSelectionFallsBackToTheFirstLayout() {
        let (store, _, _) = makeStore(layouts: [editableLayout(id: "a", name: "A"),
                                                editableLayout(id: "b", name: "B")])
        XCTAssertEqual(store.selectedLayout?.id, "a")
        store.select("b")
        XCTAssertEqual(store.selectedLayout?.id, "b")
        // Selecting something that is not there changes nothing.
        store.select("nowhere")
        XCTAssertEqual(store.selectedLayout?.id, "b")
    }

    // MARK: - Adding from a preset

    func testAddingFromAPresetCreatesAnEditableCopy() throws {
        let catalog = makeTestCatalog()
        try XCTSkipIf(catalog.preset("daily_recap") == nil, "The daily_recap preset is not available")
        let (store, persistence, _) = makeStore(layouts: [editableLayout()])
        let savesBefore = persistence.saveCount

        let added = store.addLayout(fromPreset: "daily_recap")
        let preset = try XCTUnwrap(catalog.preset("daily_recap"))

        XCTAssertFalse(added.isPreset, "The reader was handed the preset itself")
        XCTAssertNotEqual(added.id, preset.id)
        XCTAssertEqual(added.presetKey, "daily_recap", "The copy forgot where it came from")
        XCTAssertEqual(added.widgets.count, preset.widgets.count)
        XCTAssertTrue(Set(added.widgets.map { $0.id }).isDisjoint(with: Set(preset.widgets.map { $0.id })),
                      "The copy shares widget ids with the preset")
        XCTAssertTrue(store.layouts.contains { $0.id == added.id })
        XCTAssertEqual(store.selectedLayoutID, added.id, "The new dashboard was not selected")
        XCTAssertGreaterThan(persistence.saveCount, savesBefore, "Adding a dashboard did not persist")
        // And the catalog's own copy is untouched.
        XCTAssertTrue(try XCTUnwrap(catalog.preset("daily_recap")).isPreset)
    }

    func testAddingTheSamePresetTwiceGivesTheSecondCopyItsOwnName() throws {
        let catalog = makeTestCatalog()
        try XCTSkipIf(catalog.preset("daily_recap") == nil, "The daily_recap preset is not available")
        let (store, _, _) = makeStore(layouts: [editableLayout()])
        let first = store.addLayout(fromPreset: "daily_recap")
        let second = store.addLayout(fromPreset: "daily_recap")
        XCTAssertNotEqual(first.id, second.id)
        XCTAssertNotEqual(first.name, second.name, "Two dashboards share a name")
    }

    func testAddingAnUnknownPresetStillGivesTheReaderADashboard() {
        let (store, _, _) = makeStore(layouts: [editableLayout()])
        let added = store.addLayout(fromPreset: "no_such_preset")
        XCTAssertFalse(added.isPreset)
        XCTAssertTrue(added.isEmpty)
        XCTAssertTrue(store.layouts.contains { $0.id == added.id })
    }

    func testAddBlankLayout() {
        let (store, _, _) = makeStore(layouts: [editableLayout()])
        let added = store.addBlankLayout(named: "Fresh")
        XCTAssertEqual(added.name, "Fresh")
        XCTAssertTrue(added.isEmpty)
        XCTAssertEqual(store.selectedLayoutID, added.id)
    }

    // MARK: - Editing a preset converts it

    /// The rule that keeps the nine shipped dashboards intact: the first edit to a preset
    /// silently makes an editable copy, keeping `presetKey` so "reset" stays available.
    func testEditingAPresetConvertsItToAnEditableCopy() throws {
        let preset = presetLayout()
        let (store, persistence, _) = makeStore(layouts: [preset])
        store.isEditing = true

        store.removeWidget("p1", from: preset.id)

        let converted = try XCTUnwrap(store.layouts.first)
        XCTAssertFalse(converted.isPreset, "The preset was edited in place")
        XCTAssertNotEqual(converted.id, preset.id, "The copy kept the preset's id")
        XCTAssertEqual(converted.presetKey, "daily_recap", "The copy forgot which preset it came from")
        XCTAssertEqual(converted.widgets.count, 1, "The widget the reader removed is still there")
        XCTAssertEqual(store.selectedLayoutID, converted.id, "The selection did not follow the copy")
        XCTAssertGreaterThan(persistence.saveCount, 0)
        XCTAssertNotNil(converted.updatedAt)
    }

    /// Removing by the id the reader can see has to work even though the conversion gave every
    /// widget a new id: the store maps the old ids onto the new ones for exactly this reason.
    func testEditingAPresetRemapsTheWidgetIDsTheCallerKnows() throws {
        let preset = presetLayout()
        let (store, _, _) = makeStore(layouts: [preset])
        store.isEditing = true
        store.resizeWidget("p2", to: .medium, in: preset.id)

        let converted = try XCTUnwrap(store.layouts.first)
        XCTAssertFalse(converted.isPreset)
        let statTile = try XCTUnwrap(converted.widgets.first { $0.kind == .statTile })
        XCTAssertEqual(statTile.size, .medium, "The resize landed on no widget at all")
        XCTAssertNotEqual(statTile.id, "p2", "The copy reused the preset's widget id")
    }

    func testASecondEditDoesNotConvertAgain() throws {
        let preset = presetLayout()
        let (store, _, _) = makeStore(layouts: [preset])
        store.isEditing = true
        store.rename(preset.id, to: "Mine")
        let converted = try XCTUnwrap(store.layouts.first)
        store.rename(converted.id, to: "Mine Again")
        XCTAssertEqual(store.layouts.count, 1, "A second edit made a second copy")
        XCTAssertEqual(store.layouts.first?.id, converted.id)
        XCTAssertEqual(store.layouts.first?.name, "Mine Again")
    }

    // MARK: - Widgets

    func testAddingAndRemovingWidgetsPersists() throws {
        let (store, persistence, catalog) = makeStore(layouts: [editableLayout()])
        try XCTSkipIf(catalog.widget(.leaderboard) == nil, "The bundled widget catalog is not available")

        let added = try XCTUnwrap(store.addWidget(kind: .leaderboard, to: "mine"))
        XCTAssertEqual(store.layout(id: "mine")?.widgets.count, 2)
        XCTAssertEqual(store.layout(id: "mine")?.widgets.last?.id, added.id)
        // The new widget arrives configured from the catalog rather than empty.
        XCTAssertEqual(added.config, catalog.defaultConfig(for: .leaderboard))

        store.removeWidget(added.id, from: "mine")
        XCTAssertEqual(store.layout(id: "mine")?.widgets.count, 1)
        XCTAssertEqual(try persistence.loadAll().first?.widgets.count, 1, "The removal was not written")
    }

    func testMovingWidgetsReorders() {
        var layout = editableLayout()
        layout.widgets = [
            TestLayouts.widget(id: "w1", kind: .statTile),
            TestLayouts.widget(id: "w2", kind: .leaderboard, size: .large),
            TestLayouts.widget(id: "w3", kind: .scoreboard, size: .large)
        ]
        let (store, _, _) = makeStore(layouts: [layout])
        store.moveWidgets(in: "mine", fromOffsets: IndexSet(integer: 0), toOffset: 3)
        XCTAssertEqual(store.layout(id: "mine")?.widgets.map { $0.id }, ["w2", "w3", "w1"])

        store.moveWidget("w1", toIndex: 0, in: "mine")
        XCTAssertEqual(store.layout(id: "mine")?.widgets.map { $0.id }, ["w1", "w2", "w3"])
    }

    func testResizingRespectsTheSizesAWidgetOffers() throws {
        let (store, _, catalog) = makeStore(layouts: [
            TestLayouts.layout(id: "mine", widgets: [TestLayouts.widget(id: "w1", kind: .leaderboard, size: .large)])
        ])
        let spec = try XCTUnwrap(catalog.widget(.leaderboard))
        try XCTSkipIf(spec.sizes.contains(.small), "The leaderboard now offers the small size")

        store.resizeWidget("w1", to: .small, in: "mine")
        XCTAssertEqual(store.layout(id: "mine")?.widgets.first?.size, spec.defaultSize,
                       "A widget was resized to something it cannot be")

        store.resizeWidget("w1", to: .medium, in: "mine")
        XCTAssertEqual(store.layout(id: "mine")?.widgets.first?.size, .medium)
    }

    func testCycleWidgetSizeWalksOnlyTheSupportedSizes() throws {
        let (store, _, catalog) = makeStore(layouts: [
            TestLayouts.layout(id: "mine", widgets: [TestLayouts.widget(id: "w1", kind: .leaderboard, size: .medium)])
        ])
        let spec = try XCTUnwrap(catalog.widget(.leaderboard))
        try XCTSkipIf(spec.sizes.count < 2, "The leaderboard offers fewer than two sizes")

        var seen: [WidgetSize] = []
        for _ in 0..<(spec.sizes.count + 1) {
            store.cycleWidgetSize("w1", in: "mine")
            if let size = store.layout(id: "mine")?.widgets.first?.size { seen.append(size) }
        }
        XCTAssertTrue(seen.allSatisfy { spec.sizes.contains($0) }, "Cycling produced an unsupported size")
        XCTAssertGreaterThan(Set(seen).count, 1, "Cycling never changed the size")
    }

    func testUpdatingAWidgetConfigNormalizesAndSanitizesTheTitle() throws {
        let (store, _, catalog) = makeStore(layouts: [
            TestLayouts.layout(id: "mine", widgets: [TestLayouts.widget(id: "w1", kind: .leaderboard, size: .large)])
        ])
        try XCTSkipIf(catalog.widget(.leaderboard) == nil, "The bundled widget catalog is not available")

        store.updateWidgetConfig("w1",
                                 config: ["metric": .string("pts"), "nonsense": .string("drop me")],
                                 title: "   ",
                                 in: "mine")
        let widget = try XCTUnwrap(store.layout(id: "mine")?.widgets.first)
        XCTAssertEqual(widget.config["metric"], .string("pts"))
        XCTAssertNil(widget.config["nonsense"], "An unknown key was written into the layout")
        XCTAssertNil(widget.title, "A whitespace-only title should fall back to the catalog's")

        store.setWidgetTitle("  Scoring  ", for: "w1", in: "mine")
        XCTAssertEqual(store.layout(id: "mine")?.widgets.first?.title, "Scoring")
    }

    func testDuplicatingAWidgetInsertsItRightAfterTheOriginal() throws {
        var layout = editableLayout()
        layout.widgets = [
            TestLayouts.widget(id: "w1", kind: .statTile),
            TestLayouts.widget(id: "w2", kind: .leaderboard, size: .large)
        ]
        let (store, _, _) = makeStore(layouts: [layout])
        store.duplicateWidget("w1", in: "mine")

        let widgets = try XCTUnwrap(store.layout(id: "mine")?.widgets)
        XCTAssertEqual(widgets.count, 3)
        XCTAssertEqual(widgets[0].id, "w1")
        XCTAssertEqual(widgets[1].kind, .statTile)
        XCTAssertNotEqual(widgets[1].id, "w1", "The duplicate reused the original's id")
        XCTAssertEqual(widgets[2].id, "w2")
    }

    // MARK: - Undo

    func testUndoRestoresTheLayoutTheEditChanged() throws {
        let (store, _, catalog) = makeStore(layouts: [editableLayout()])
        try XCTSkipIf(catalog.widget(.leaderboard) == nil, "The bundled widget catalog is not available")

        store.isEditing = true
        XCTAssertFalse(store.canUndo, "A fresh edit session has something to undo")

        store.addWidget(kind: .leaderboard, to: "mine")
        XCTAssertEqual(store.layout(id: "mine")?.widgets.count, 2)
        XCTAssertTrue(store.canUndo)

        store.undoLastEdit()
        XCTAssertEqual(store.layout(id: "mine")?.widgets.count, 1, "Undo did not put the layout back")
        XCTAssertEqual(store.layout(id: "mine")?.widgets.first?.id, "w1")
        XCTAssertFalse(store.canUndo, "Undo is one level deep, so it is spent")
    }

    func testUndoOfAPresetConversionPutsThePresetBack() throws {
        let preset = presetLayout()
        let (store, _, _) = makeStore(layouts: [preset])
        store.isEditing = true
        store.removeWidget("p1", from: preset.id)
        XCTAssertEqual(store.layouts.first?.widgets.count, 1)

        store.undoLastEdit()
        let restored = try XCTUnwrap(store.layouts.first)
        XCTAssertEqual(restored.id, preset.id)
        XCTAssertTrue(restored.isPreset)
        XCTAssertEqual(restored.widgets.count, 2)
        XCTAssertEqual(store.selectedLayoutID, preset.id)
    }

    func testNothingIsRecordedForUndoOutsideEditMode() {
        let (store, _, _) = makeStore(layouts: [editableLayout()])
        XCTAssertFalse(store.isEditing)
        store.rename("mine", to: "Renamed")
        XCTAssertFalse(store.canUndo, "An edit outside edit mode was put on the undo stack")
        XCTAssertEqual(store.layout(id: "mine")?.name, "Renamed")
    }

    func testLeavingEditModeClearsTheUndoStack() {
        let (store, _, _) = makeStore(layouts: [editableLayout()])
        store.isEditing = true
        store.rename("mine", to: "Renamed")
        XCTAssertTrue(store.canUndo)
        store.isEditing = false
        XCTAssertFalse(store.canUndo, "Leaving edit mode should commit")
    }

    // MARK: - Deleting

    /// The reader is never left staring at nothing: deleting the last dashboard re-seeds.
    func testDeletingTheLastLayoutReSeeds() throws {
        let catalog = makeTestCatalog()
        try XCTSkipIf(catalog.presets.isEmpty, "The bundled presets are not available")
        let only = editableLayout(id: "only", name: "Only")
        let persistence = InMemoryLayoutPersistence(layouts: [only])
        let store = DashboardStore(persistence: persistence,
                                   catalog: catalog,
                                   defaults: makeTestDefaults("hardwood.store.delete"))
        XCTAssertEqual(store.layouts.count, 1)

        store.delete("only")

        XCTAssertFalse(store.layouts.isEmpty, "The reader was left with no dashboard at all")
        XCTAssertFalse(store.layouts.contains { $0.id == "only" })
        XCTAssertEqual(store.selectedLayoutID, store.layouts.first?.id)
        XCTAssertFalse(try XCTUnwrap(store.layouts.first).isPreset)
        XCTAssertEqual(try persistence.loadAll().count, store.layouts.count)
    }

    func testDeletingOneOfSeveralKeepsTheRest() {
        let (store, _, _) = makeStore(layouts: [editableLayout(id: "a", name: "A"),
                                                editableLayout(id: "b", name: "B")])
        store.select("a")
        store.delete("a")
        XCTAssertEqual(store.layouts.map { $0.id }, ["b"])
        XCTAssertEqual(store.selectedLayoutID, "b", "The selection did not move off the deleted dashboard")
    }

    func testDuplicatingALayoutInsertsItAfterTheOriginal() {
        let first = editableLayout(id: "a", name: "A")
        let (store, _, _) = makeStore(layouts: [first, editableLayout(id: "b", name: "B")])
        store.duplicate(first)
        XCTAssertEqual(store.layouts.count, 3)
        XCTAssertEqual(store.layouts[0].id, "a")
        XCTAssertEqual(store.layouts[2].id, "b")
        XCTAssertNotEqual(store.layouts[1].id, "a")
        XCTAssertFalse(store.layouts[1].isPreset)
    }

    // MARK: - Reset

    func testResetToPresetPutsTheWidgetsBackButKeepsTheIdentity() throws {
        let catalog = makeTestCatalog()
        try XCTSkipIf(catalog.preset("daily_recap") == nil, "The daily_recap preset is not available")
        let (store, _, _) = makeStore(layouts: [editableLayout()])
        let added = store.addLayout(fromPreset: "daily_recap")
        store.removeWidget(try XCTUnwrap(added.widgets.first).id, from: added.id)
        let trimmed = try XCTUnwrap(store.layout(id: added.id))
        XCTAssertEqual(trimmed.widgets.count, added.widgets.count - 1)

        store.resetToPreset(added.id)

        let reset = try XCTUnwrap(store.layout(id: added.id))
        XCTAssertEqual(reset.id, added.id, "Resetting changed the dashboard's identity")
        XCTAssertEqual(reset.name, trimmed.name)
        XCTAssertEqual(reset.widgets.count, added.widgets.count, "Resetting did not restore every widget")
        XCTAssertFalse(reset.isPreset)
        XCTAssertEqual(reset.presetKey, "daily_recap")
    }

    func testResetDoesNothingForALayoutWithNoPreset() {
        let (store, _, _) = makeStore(layouts: [editableLayout()])
        store.resetToPreset("mine")
        XCTAssertEqual(store.layout(id: "mine")?.widgets.count, 1)
    }

    // MARK: - Appearance

    func testAccentAndIconAreStoredAndPersisted() throws {
        let (store, persistence, _) = makeStore(layouts: [editableLayout()])
        store.setAccent(.teal, for: "mine")
        store.setIcon("flame", for: "mine")
        XCTAssertEqual(store.layout(id: "mine")?.accent, .teal)
        XCTAssertEqual(store.layout(id: "mine")?.icon, "flame")
        let saved = try XCTUnwrap(try persistence.loadAll().first)
        XCTAssertEqual(saved.accent, .teal)
        XCTAssertEqual(saved.icon, "flame")
    }

    func testRenameRefusesAnEmptyName() {
        let (store, _, _) = makeStore(layouts: [editableLayout()])
        store.rename("mine", to: "   ")
        XCTAssertEqual(store.layout(id: "mine")?.name, "Mine")
    }

    func testBasePresetNamesWhereALayoutCameFrom() throws {
        let catalog = makeTestCatalog()
        try XCTSkipIf(catalog.preset("daily_recap") == nil, "The daily_recap preset is not available")
        let (store, _, _) = makeStore(layouts: [editableLayout()])
        let added = store.addLayout(fromPreset: "daily_recap")
        XCTAssertEqual(store.basePreset(for: added)?.presetKey, "daily_recap")
        XCTAssertNil(store.basePreset(for: try XCTUnwrap(store.layout(id: "mine"))))
    }
}
