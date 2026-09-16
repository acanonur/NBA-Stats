import Foundation
import SwiftUI
import XCTest
@testable import Hardwood

/// The layout document is the one thing in Hardwood the reader owns. These tests cover the four
/// promises made about it: an edited preset becomes a copy rather than overwriting the original,
/// a widget keeps its id through every edit, what is written to disk reads back identically, and
/// a document from another build is repaired or refused rather than corrupted.
final class LayoutTests: XCTestCase {

    // MARK: - Fixtures

    private func makeWidgets() -> [DashboardWidget] {
        [
            TestLayouts.widget(id: "w1", kind: .scoreboard, size: .large, config: ["date": .string("latest")]),
            TestLayouts.widget(id: "w2", kind: .statTile, size: .small, config: ["metric": .string("ts_pct")]),
            TestLayouts.widget(id: "w3", kind: .leaderboard, size: .large, config: ["limit": .int(10)])
        ]
    }

    private func makePreset() -> DashboardLayout {
        TestLayouts.layout(id: "preset.daily_recap",
                           name: "Daily Recap",
                           isPreset: true,
                           presetKey: "daily_recap",
                           widgets: makeWidgets())
    }

    // MARK: - makeEditableCopy

    func testMakeEditableCopyTakesANewIdentityButKeepsItsOrigin() {
        let preset = makePreset()
        let copy = preset.makeEditableCopy()

        XCTAssertNotEqual(copy.id, preset.id, "The copy shares the preset's id")
        XCTAssertFalse(copy.isPreset, "The copy is still flagged as a preset")
        XCTAssertEqual(copy.presetKey, "daily_recap", "The copy forgot which preset it came from")
        XCTAssertEqual(copy.name, preset.name)
        XCTAssertEqual(copy.icon, preset.icon)
        XCTAssertEqual(copy.accent, preset.accent)
        XCTAssertEqual(copy.tagline, preset.tagline)
        XCTAssertEqual(copy.schemaVersion, DashboardLayout.currentSchemaVersion)
        XCTAssertNotNil(copy.createdAt)
        XCTAssertNotNil(copy.updatedAt)
        // The original is untouched.
        XCTAssertTrue(preset.isPreset)
    }

    func testMakeEditableCopyRenamesWhenAsked() {
        XCTAssertEqual(makePreset().makeEditableCopy(named: "My Recap").name, "My Recap")
    }

    /// Fresh widget ids matter: two copies of the same preset on one device must not share widget
    /// ids, or a resolve response could be applied to the wrong tile.
    func testMakeEditableCopyGivesEveryWidgetAFreshID() {
        let preset = makePreset()
        let first = preset.makeEditableCopy()
        let second = preset.makeEditableCopy()

        XCTAssertEqual(first.widgets.count, preset.widgets.count)
        XCTAssertTrue(Set(first.widgets.map { $0.id }).isDisjoint(with: Set(preset.widgets.map { $0.id })))
        XCTAssertTrue(Set(first.widgets.map { $0.id }).isDisjoint(with: Set(second.widgets.map { $0.id })))
        // Everything except the id is carried over, in order.
        for (source, copied) in zip(preset.widgets, first.widgets) {
            XCTAssertEqual(copied.kind, source.kind)
            XCTAssertEqual(copied.size, source.size)
            XCTAssertEqual(copied.title, source.title)
            XCTAssertEqual(copied.config, source.config)
        }
    }

    // MARK: - Widget editing

    func testAppendAddsToTheEnd() {
        var layout = TestLayouts.layout(widgets: makeWidgets())
        layout.append(TestLayouts.widget(id: "w4", kind: .careerArc))
        XCTAssertEqual(layout.widgets.map { $0.id }, ["w1", "w2", "w3", "w4"])
    }

    func testRemoveTakesOutExactlyOneWidget() {
        var layout = TestLayouts.layout(widgets: makeWidgets())
        layout.remove(widgetID: "w2")
        XCTAssertEqual(layout.widgets.map { $0.id }, ["w1", "w3"])
        layout.remove(widgetID: "not-in-this-layout")
        XCTAssertEqual(layout.widgets.map { $0.id }, ["w1", "w3"])
    }

    func testMoveReordersWithoutChangingIdentity() {
        var layout = TestLayouts.layout(widgets: makeWidgets())
        layout.move(fromOffsets: IndexSet(integer: 0), toOffset: 3)
        XCTAssertEqual(layout.widgets.map { $0.id }, ["w2", "w3", "w1"])
        XCTAssertEqual(Set(layout.widgets.map { $0.id }).count, 3)
    }

    func testReplaceSwapsTheWidgetWithTheSameID() throws {
        var layout = TestLayouts.layout(widgets: makeWidgets())
        var updated = try XCTUnwrap(layout.widget(id: "w2"))
        updated.size = .large
        updated.config = ["metric": .string("net_rtg")]
        layout.replace(updated)

        XCTAssertEqual(layout.widget(id: "w2")?.size, .large)
        XCTAssertEqual(layout.widget(id: "w2")?.config["metric"], .string("net_rtg"))
        XCTAssertEqual(layout.widgets.map { $0.id }, ["w1", "w2", "w3"], "Replacing moved a widget")
    }

    func testReplaceIgnoresAWidgetThatIsNotInTheLayout() {
        var layout = TestLayouts.layout(widgets: makeWidgets())
        layout.replace(TestLayouts.widget(id: "stranger", kind: .comparison))
        XCTAssertEqual(layout.widgets.count, 3)
        XCTAssertNil(layout.widget(id: "stranger"))
    }

    func testIsEmptyAndWidgetLookup() {
        XCTAssertTrue(TestLayouts.layout(widgets: []).isEmpty)
        let layout = TestLayouts.layout(widgets: makeWidgets())
        XCTAssertFalse(layout.isEmpty)
        XCTAssertEqual(layout.widget(id: "w3")?.kind, .leaderboard)
        XCTAssertNil(layout.widget(id: "nope"))
    }

    func testWidgetConfigAccessors() {
        let widget = TestLayouts.widget(id: "w",
                                        config: ["metric": .string("ts_pct"),
                                                 "limit": .int(10),
                                                 "showSparkline": .bool(true)])
        XCTAssertEqual(widget.configString("metric"), "ts_pct")
        XCTAssertEqual(widget.configInt("limit"), 10)
        XCTAssertEqual(widget.configBool("showSparkline"), true)
        XCTAssertNil(widget.configString("limit"))
        XCTAssertNil(widget.configInt("missing"))
    }

    func testCopyWithNewIDKeepsEverythingButTheID() {
        let widget = TestLayouts.widget(id: "w1", kind: .gameLog, size: .large, config: ["season": .string("2025-26")])
        let copy = widget.copyWithNewID()
        XCTAssertNotEqual(copy.id, widget.id)
        XCTAssertEqual(copy.kind, widget.kind)
        XCTAssertEqual(copy.size, widget.size)
        XCTAssertEqual(copy.config, widget.config)
    }

    // MARK: - Sizes

    func testWidgetSizeSpansAndHeights() {
        XCTAssertEqual(WidgetSize.small.columnSpan(horizontalSizeClass: .compact), 1)
        XCTAssertEqual(WidgetSize.medium.columnSpan(horizontalSizeClass: .compact), 2)
        XCTAssertEqual(WidgetSize.large.columnSpan(horizontalSizeClass: .compact), 2)
        XCTAssertEqual(WidgetSize.large.columnSpan(horizontalSizeClass: .regular), 4)
        // An unknown size class is treated as compact, so a tile is never wider than the grid.
        XCTAssertEqual(WidgetSize.large.columnSpan(horizontalSizeClass: nil), 2)

        XCTAssertEqual(WidgetSize.small.estimatedHeight, 148)
        XCTAssertEqual(WidgetSize.medium.estimatedHeight, 232)
        XCTAssertEqual(WidgetSize.large.estimatedHeight, 360)

        XCTAssertEqual(WidgetSize.small.next, .medium)
        XCTAssertEqual(WidgetSize.medium.next, .large)
        XCTAssertEqual(WidgetSize.large.next, .small)
    }

    func testWidgetKindRawValuesMatchTheContract() {
        XCTAssertEqual(WidgetKind.allCases.count, 13)
        XCTAssertEqual(WidgetKind.statTile.rawValue, "stat_tile")
        XCTAssertEqual(WidgetKind.playerSnapshot.rawValue, "player_snapshot")
        XCTAssertEqual(WidgetKind.gameLog.rawValue, "game_log")
        XCTAssertEqual(WidgetKind.trendChart.rawValue, "trend_chart")
        XCTAssertEqual(WidgetKind.fourFactors.rawValue, "four_factors")
        XCTAssertEqual(WidgetKind.shotProfile.rawValue, "shot_profile")
        XCTAssertEqual(WidgetKind.dailyMovers.rawValue, "daily_movers")
        XCTAssertEqual(WidgetKind.teamEfficiency.rawValue, "team_efficiency")
        XCTAssertEqual(WidgetKind.nextGameProjection.rawValue, "next_game_projection")
        XCTAssertEqual(WidgetKind.careerArc.rawValue, "career_arc")
        for kind in WidgetKind.allCases {
            XCTAssertFalse(kind.fallbackName.isEmpty)
            XCTAssertFalse(kind.fallbackIcon.isEmpty)
            XCTAssertGreaterThan(kind.defaultCacheTTLSeconds, 0)
        }
    }

    func testAccentNamesMatchTheContract() {
        XCTAssertEqual(Set(AccentName.allCases.map { $0.rawValue }),
                       ["orange", "indigo", "teal", "red", "amber", "green", "blue", "purple", "graphite"])
        for accent in AccentName.allCases {
            XCTAssertFalse(accent.displayName.isEmpty)
        }
    }

    // MARK: - Codable

    func testLayoutRoundTripsThroughJSON() throws {
        var layout = TestLayouts.layout(widgets: makeWidgets())
        layout.accent = .indigo
        layout.tagline = "Last night's slate, decoded."
        layout.presetKey = "daily_recap"
        layout.createdAt = Date(timeIntervalSince1970: 1_767_312_000)
        layout.updatedAt = Date(timeIntervalSince1970: 1_767_315_600)

        let data = try JSONEncoder().encode(layout)
        let decoded = try JSONDecoder().decode(DashboardLayout.self, from: data)

        XCTAssertEqual(decoded.id, layout.id)
        XCTAssertEqual(decoded.name, layout.name)
        XCTAssertEqual(decoded.accent, .indigo)
        XCTAssertEqual(decoded.tagline, layout.tagline)
        XCTAssertEqual(decoded.presetKey, "daily_recap")
        XCTAssertEqual(decoded.widgets, layout.widgets)
        XCTAssertEqual(decoded.createdAt, layout.createdAt)
        XCTAssertEqual(decoded.updatedAt, layout.updatedAt)
    }

    /// Timestamps are written by the type itself rather than by a decoder strategy, so a layout
    /// reads back the same whichever `JSONDecoder` opens it.
    func testTimestampsSurviveAPlainDecoder() throws {
        var layout = TestLayouts.layout(widgets: [])
        layout.updatedAt = Date(timeIntervalSince1970: 1_767_315_600)
        let data = try JSONEncoder().encode(layout)
        let text = try XCTUnwrap(String(data: data, encoding: .utf8))
        XCTAssertTrue(text.contains(#""updatedAt":""#),
                      "updatedAt was not written as an RFC-3339 string: \(text)")
        XCTAssertEqual(try JSONDecoder().decode(DashboardLayout.self, from: data).updatedAt, layout.updatedAt)
    }

    func testDecodingToleratesAMinimalDocument() throws {
        let json = #"{"name": "Minimal", "widgets": []}"#
        let data = try XCTUnwrap(json.data(using: .utf8))
        let layout = try JSONDecoder().decode(DashboardLayout.self, from: data)
        XCTAssertEqual(layout.name, "Minimal")
        XCTAssertFalse(layout.id.isEmpty, "A layout with no id should be given one")
        XCTAssertEqual(layout.accent, .orange)
        XCTAssertEqual(layout.schemaVersion, DashboardLayout.currentSchemaVersion)
        XCTAssertTrue(layout.widgets.isEmpty)
    }

    // MARK: - FileLayoutPersistence

    func testFilePersistenceRoundTripsThroughATemporaryDirectory() throws {
        let directory = TemporaryDirectory("layout-persistence")
        let persistence = FileLayoutPersistence(directoryURL: directory.url, fileName: "Layouts.json")

        // Nothing on disk yet: a first launch, not an error.
        XCTAssertTrue(try persistence.loadAll().isEmpty)

        let first = TestLayouts.layout(id: "one", name: "One", widgets: makeWidgets())
        let second = TestLayouts.layout(id: "two", name: "Two", presetKey: "efficiency_hunt", widgets: [])
        try persistence.save([first, second])

        XCTAssertTrue(FileManager.default.fileExists(atPath: persistence.fileURL.path))

        let loaded = try persistence.loadAll()
        XCTAssertEqual(loaded.count, 2)
        XCTAssertEqual(loaded.map { $0.id }, ["one", "two"], "Layout order was not preserved")
        XCTAssertEqual(loaded.first?.widgets.map { $0.id }, ["w1", "w2", "w3"])
        XCTAssertEqual(loaded.first?.widgets.first?.config["date"], .string("latest"))
        XCTAssertEqual(loaded.last?.presetKey, "efficiency_hunt")
    }

    func testFilePersistenceOverwritesRatherThanAppends() throws {
        let directory = TemporaryDirectory("layout-overwrite")
        let persistence = FileLayoutPersistence(directoryURL: directory.url)
        try persistence.save([TestLayouts.layout(id: "one", name: "One", widgets: [])])
        try persistence.save([TestLayouts.layout(id: "two", name: "Two", widgets: [])])
        let loaded = try persistence.loadAll()
        XCTAssertEqual(loaded.map { $0.id }, ["two"])
    }

    func testFilePersistenceReportsTheNotesFromAMigration() throws {
        let directory = TemporaryDirectory("layout-notes")
        let persistence = FileLayoutPersistence(directoryURL: directory.url)
        let document = """
        {"schemaVersion": 1, "updatedAt": "2026-01-02T18:00:00Z", "layouts": [
          {"id": "one", "name": "One", "schemaVersion": 1, "widgets": [
            {"id": "w1", "kind": "teleporter", "size": "small", "config": {}},
            {"id": "w2", "kind": "scoreboard", "size": "large", "config": {"date": "latest"}}
          ]}
        ]}
        """
        try XCTUnwrap(document.data(using: .utf8)).write(to: persistence.fileURL)

        let result = try persistence.loadAllWithNotes()
        XCTAssertEqual(result.layouts.count, 1)
        XCTAssertEqual(result.layouts.first?.widgets.map { $0.id }, ["w2"])
        XCTAssertTrue(result.hasIssues)
        XCTAssertFalse(result.messages.isEmpty)
        XCTAssertTrue(result.messages.contains { $0.contains("teleporter") },
                      "The reader is not told which widget went missing: \(result.messages)")
    }

    func testInMemoryPersistenceCountsItsSaves() throws {
        let persistence = InMemoryLayoutPersistence(layouts: [TestLayouts.layout(widgets: [])])
        XCTAssertEqual(try persistence.loadAll().count, 1)
        XCTAssertEqual(persistence.saveCount, 0)
        try persistence.save([])
        XCTAssertEqual(persistence.saveCount, 1)
        XCTAssertTrue(try persistence.loadAll().isEmpty)
    }

    // MARK: - LayoutMigrator

    private func data(_ json: String) throws -> Data {
        try XCTUnwrap(json.data(using: .utf8))
    }

    func testMigratorDropsAnUnknownWidgetKindAndSaysSo() throws {
        let raw = try data("""
        {"id": "one", "name": "One", "schemaVersion": 1, "widgets": [
          {"id": "w1", "kind": "scoreboard", "size": "large", "config": {"date": "latest"}},
          {"id": "w2", "kind": "hologram", "size": "large", "config": {}},
          {"id": "w3", "kind": "stat_tile", "size": "small", "config": {}}
        ]}
        """)
        let result = try LayoutMigrator.migrateResult(raw)
        XCTAssertEqual(result.layout.widgets.map { $0.id }, ["w1", "w3"])
        XCTAssertTrue(result.didChange)
        XCTAssertEqual(result.notes, result.migrationNotes)
        XCTAssertTrue(result.notes.contains { $0.contains("hologram") }, "\(result.notes)")
    }

    func testMigratorReplacesAnUnknownSizeRatherThanDroppingTheWidget() throws {
        let raw = try data("""
        {"id": "one", "name": "One", "widgets": [
          {"id": "w1", "kind": "stat_tile", "size": "gigantic", "config": {}}
        ]}
        """)
        let result = try LayoutMigrator.migrateResult(raw)
        XCTAssertEqual(result.layout.widgets.count, 1)
        XCTAssertEqual(result.layout.widgets.first?.size, .medium)
        XCTAssertTrue(result.notes.contains { $0.contains("gigantic") }, "\(result.notes)")
    }

    func testMigratorGivesADuplicateWidgetIDANewOne() throws {
        let raw = try data("""
        {"id": "one", "name": "One", "widgets": [
          {"id": "w1", "kind": "stat_tile", "size": "small", "config": {}},
          {"id": "w1", "kind": "leaderboard", "size": "large", "config": {}}
        ]}
        """)
        let result = try LayoutMigrator.migrateResult(raw)
        let ids = result.layout.widgets.map { $0.id }
        XCTAssertEqual(ids.count, 2)
        XCTAssertEqual(Set(ids).count, 2, "Two widgets still share an id")
        XCTAssertFalse(result.notes.isEmpty)
    }

    func testMigratorRefusesALayoutFromANewerBuild() throws {
        let raw = try data("""
        {"id": "one", "name": "From the future", "schemaVersion": 99, "widgets": []}
        """)
        XCTAssertThrowsError(try LayoutMigrator.migrate(raw)) { error in
            guard let migrationError = error as? LayoutMigrationError else {
                return XCTFail("Expected a LayoutMigrationError, got \(error)")
            }
            XCTAssertEqual(migrationError, .tooNew(found: 99, supported: DashboardLayout.currentSchemaVersion))
            XCTAssertTrue(migrationError.userMessage.contains("99"))
        }
    }

    func testMigratorRefusesSomethingThatIsNotALayout() throws {
        XCTAssertThrowsError(try LayoutMigrator.migrate(try data(#"{"unrelated": true}"#))) { error in
            XCTAssertEqual(error as? LayoutMigrationError, .notALayout)
        }
        XCTAssertThrowsError(try LayoutMigrator.migrate(try data("not json at all"))) { error in
            guard case .unreadable = (error as? LayoutMigrationError) else {
                return XCTFail("Expected .unreadable, got \(error)")
            }
        }
    }

    func testMigrateCollectionKeepsWhatItCanAndReportsTheRest() throws {
        let raw = try data("""
        [
          {"id": "ok", "name": "Fine", "schemaVersion": 1, "widgets": []},
          {"id": "future", "name": "Newer", "schemaVersion": 42, "widgets": []}
        ]
        """)
        let result = LayoutMigrator.migrateCollection(raw)
        XCTAssertEqual(result.layouts.map { $0.id }, ["ok"])
        XCTAssertEqual(result.failures.count, 1)
        XCTAssertEqual(result.failures.first, .tooNew(found: 42, supported: DashboardLayout.currentSchemaVersion))
    }

    func testMigrateCollectionAlsoAcceptsASingleDocument() throws {
        let raw = try data(#"{"id": "solo", "name": "Solo", "widgets": []}"#)
        XCTAssertEqual(LayoutMigrator.migrateCollection(raw).layouts.map { $0.id }, ["solo"])
    }
}

/// The half of the layout story that needs the catalog: which configuration keys a widget still
/// has, which sizes it offers, and what a fresh install starts from. These live in their own
/// `@MainActor` case because `Catalog` is main-actor isolated.
@MainActor
final class LayoutCatalogTests: XCTestCase {

    private func data(_ json: String) throws -> Data {
        try XCTUnwrap(json.data(using: .utf8))
    }

    /// Stripping an unknown configuration key needs the catalog, because the catalog is what says
    /// which keys a widget still has.
    func testMigratorStripsAnUnknownConfigKeyAndFillsTheDefaults() throws {
        let catalog = makeTestCatalog()
        try XCTSkipIf(catalog.widget(.leaderboard) == nil, "The bundled widget catalog is not available")

        let raw = try data("""
        {"id": "one", "name": "One", "schemaVersion": 1, "widgets": [
          {"id": "w1", "kind": "leaderboard", "size": "large",
           "config": {"metric": "ts_pct", "abolishedSetting": "goodbye"}}
        ]}
        """)
        let result = try LayoutMigrator.migrate(raw, catalog: catalog)
        let widget = try XCTUnwrap(result.layout.widgets.first)

        XCTAssertNil(widget.config["abolishedSetting"], "An unknown config key survived the migration")
        XCTAssertEqual(widget.config["metric"], .string("ts_pct"), "The reader's own choice was lost")
        XCTAssertTrue(result.notes.contains { $0.contains("abolishedSetting") },
                      "The reader is not told what was removed: \(result.notes)")
        // And the catalog's defaults filled in the rest.
        let spec = try XCTUnwrap(catalog.widget(.leaderboard))
        for field in spec.config {
            guard let defaultValue = field.`default`, !defaultValue.isNull, field.key != "metric" else { continue }
            XCTAssertEqual(widget.config[field.key], defaultValue, "\(field.key) was not filled in")
        }
    }

    func testNormalizeResizesAWidgetToASizeItsCatalogEntryOffers() throws {
        let catalog = makeTestCatalog()
        let spec = try XCTUnwrap(catalog.widget(.leaderboard))
        try XCTSkipIf(spec.sizes.contains(.small), "The leaderboard now offers the small size")

        let layout = TestLayouts.layout(widgets: [TestLayouts.widget(id: "w1", kind: .leaderboard, size: .small)])
        let result = LayoutMigrator.normalize(layout, catalog: catalog)
        XCTAssertEqual(result.layout.widgets.first?.size, spec.defaultSize)
        XCTAssertTrue(result.notes.contains { $0.contains("resized") }, "\(result.notes)")
    }

    func testNormalizeDropsAWidgetTheCatalogNoLongerLists() throws {
        // A catalog with only one widget kind in it stands in for a build that dropped the rest.
        let catalog = makeTestCatalog()
        let spec = try XCTUnwrap(catalog.widget(.scoreboard))
        catalog.update(metrics: nil, widgets: [spec], presets: nil)

        let layout = TestLayouts.layout(widgets: [
            TestLayouts.widget(id: "w1", kind: .scoreboard, size: spec.defaultSize),
            TestLayouts.widget(id: "w2", kind: .careerArc, size: .large)
        ])
        let result = LayoutMigrator.normalize(layout, catalog: catalog)
        XCTAssertEqual(result.layout.widgets.map { $0.id }, ["w1"])
        XCTAssertFalse(result.notes.isEmpty)
    }

    // MARK: - Seeding

    func testSeedingProducesOneEditableDashboard() throws {
        let catalog = makeTestCatalog()
        try XCTSkipIf(catalog.presets.isEmpty, "The bundled presets are not available")
        let seeded = LayoutSeeder.seedLayouts(catalog: catalog)
        XCTAssertEqual(seeded.count, 1, "A first launch should be one working dashboard, not ten")
        let layout = try XCTUnwrap(seeded.first)
        XCTAssertFalse(layout.isPreset)
        XCTAssertEqual(layout.presetKey, LayoutSeeder.defaultPresetKey)
        XCTAssertFalse(layout.widgets.isEmpty)
    }

    func testBlankLayoutIsEmptyButUsable() {
        let layout = LayoutSeeder.blankLayout(named: "Mine", accent: .teal)
        XCTAssertEqual(layout.name, "Mine")
        XCTAssertEqual(layout.accent, .teal)
        XCTAssertFalse(layout.isPreset)
        XCTAssertTrue(layout.isEmpty)
        XCTAssertNotNil(layout.createdAt)
    }
}
