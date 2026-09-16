import Foundation
import XCTest
@testable import Hardwood

/// The bundled contracts are the app's spine: every widget's controls, every metric's format and
/// era rules, and the ten dashboards a new install can start from are read from them at launch.
/// If they do not decode, nothing else in the app is trustworthy — so these tests read the real
/// bundled files rather than a fixture written for the occasion.
@MainActor
final class CatalogTests: XCTestCase {

    /// The counts the contract commits to (`contracts/CONTRACT.md`, the table in its header).
    private let expectedMetricCount = 61
    private let expectedWidgetCount = 13
    private let expectedPresetCount = 10

    /// The field types whose value is a subject the reader has to choose; the catalog gives them
    /// no default on purpose.
    private let subjectFieldTypes: Set<ConfigFieldType> = [
        .player, .playerList, .team, .teamList, .subject, .subjectList
    ]

    /// Built once per test method — XCTest makes a fresh instance for each — so a test that
    /// replaces the catalog's contents cannot leak into the next one.
    private lazy var loadedCatalog: Catalog = makeTestCatalog()

    /// Skips rather than fails when the bundled contracts are not in a test-visible bundle: a
    /// packaging problem is not a reason to report the catalog itself as broken.
    private func requireCatalog() throws -> Catalog {
        let catalog = loadedCatalog
        try XCTSkipIf(catalog.widgets.isEmpty && catalog.metrics.isEmpty,
                      "The bundled contracts (Resources/Contracts/*.json) are not in any test-visible bundle.")
        return catalog
    }

    // MARK: - The bundled documents decode

    func testBundledMetricDocumentDecodes() throws {
        let data = try XCTUnwrap(TestBundles.contractData(named: "metrics"), "metrics.json is not in the bundle")
        let document = try JSONDecoder().decode(MetricCatalogDocument.self, from: data)
        XCTAssertEqual(document.schemaVersion, 1)
        XCTAssertEqual(document.metrics.count, expectedMetricCount)
        XCTAssertFalse(document.categories.isEmpty)
        XCTAssertFalse(document.formats.isEmpty)
        XCTAssertFalse(document.eraBoundaries.isEmpty)
    }

    func testBundledWidgetDocumentDecodes() throws {
        let data = try XCTUnwrap(TestBundles.contractData(named: "widgets"), "widgets.json is not in the bundle")
        let document = try JSONDecoder().decode(WidgetCatalogDocument.self, from: data)
        XCTAssertEqual(document.schemaVersion, 1)
        XCTAssertEqual(document.widgets.count, expectedWidgetCount)
        XCTAssertEqual(document.sizes.count, WidgetSize.allCases.count)
        XCTAssertEqual(document.gridColumns?.compact, 2)
        XCTAssertEqual(document.gridColumns?.regular, 4)
        // Every field type the catalog names has a Swift case, or the config sheet would silently
        // drop a control.
        for raw in document.configFieldTypes {
            XCTAssertNotNil(ConfigFieldType(rawValue: raw), "Unknown config field type \"\(raw)\"")
        }
    }

    func testBundledPresetDocumentDecodes() throws {
        let data = try XCTUnwrap(TestBundles.contractData(named: "presets"), "presets.json is not in the bundle")
        let document = try JSONDecoder().decode(PresetCatalogDocument.self, from: data)
        XCTAssertEqual(document.schemaVersion, 1)
        XCTAssertEqual(document.presets.count, expectedPresetCount)
        XCTAssertFalse(document.subjectTokens.isEmpty)
        for token in document.subjectTokens {
            XCTAssertTrue(token.token.hasPrefix("$"), "Subject token \"\(token.token)\" is not $-prefixed")
        }
    }

    // MARK: - Counts

    func testCatalogLoadsEveryMetricWidgetAndPreset() throws {
        let catalog = try requireCatalog()
        XCTAssertEqual(catalog.metrics.count, expectedMetricCount)
        XCTAssertEqual(catalog.widgets.count, expectedWidgetCount)
        XCTAssertEqual(catalog.presets.count, expectedPresetCount)
    }

    func testEveryWidgetKindHasACatalogEntry() throws {
        let catalog = try requireCatalog()
        for kind in WidgetKind.allCases {
            XCTAssertNotNil(catalog.widget(kind), "No catalog entry for \(kind.rawValue)")
        }
        XCTAssertEqual(Set(catalog.widgets.map { $0.kind }).count, expectedWidgetCount)
    }

    func testEveryWidgetSpecIsInternallyConsistent() throws {
        let catalog = try requireCatalog()
        XCTAssertFalse(catalog.widgets.isEmpty)
        for spec in catalog.widgets {
            XCTAssertFalse(spec.name.isEmpty, "\(spec.kind.rawValue) has no name")
            XCTAssertFalse(spec.icon.isEmpty, "\(spec.kind.rawValue) has no icon")
            XCTAssertFalse(spec.sizes.isEmpty, "\(spec.kind.rawValue) offers no sizes")
            XCTAssertTrue(spec.sizes.contains(spec.defaultSize),
                          "\(spec.kind.rawValue) defaults to a size it does not offer")
            XCTAssertGreaterThan(spec.minRefreshSeconds, 0)
            let keys = spec.config.map { $0.key }
            XCTAssertEqual(Set(keys).count, keys.count, "\(spec.kind.rawValue) has two fields sharing a key")
            for field in spec.config {
                XCTAssertNotNil(spec.field(field.key))
                if let dependsOn = field.dependsOn {
                    XCTAssertTrue(keys.contains(dependsOn),
                                  "\(spec.kind.rawValue).\(field.key) depends on \"\(dependsOn)\", which it does not have")
                }
            }
        }
    }

    func testEveryMetricKeyIsUniqueAndLookupable() throws {
        let catalog = try requireCatalog()
        let keys = catalog.metrics.map { $0.key }
        XCTAssertFalse(keys.isEmpty)
        XCTAssertEqual(Set(keys).count, keys.count, "Two metrics share a key")
        for key in keys {
            XCTAssertEqual(catalog.metric(key)?.key, key)
        }
        XCTAssertNil(catalog.metric("no_such_metric"))
    }

    func testEveryMetricDeclaresAScopeAndAnEra() throws {
        let catalog = try requireCatalog()
        XCTAssertFalse(catalog.metrics.isEmpty)
        for metric in catalog.metrics {
            XCTAssertFalse(metric.scope.isEmpty, "\(metric.key) has no scope")
            XCTAssertFalse(metric.availability.seasonFrom.isEmpty, "\(metric.key) has no seasonFrom")
            XCTAssertFalse(metric.availability.perGameFrom.isEmpty, "\(metric.key) has no perGameFrom")
            XCTAssertFalse(metric.glossary.isEmpty, "\(metric.key) has no glossary entry")
            for scope in metric.scope {
                XCTAssertTrue(scope == "player" || scope == "team",
                              "\(metric.key) has an unexpected scope \"\(scope)\"")
            }
        }
    }

    func testMetricsInScopeFilterMatchesTheCatalog() throws {
        let catalog = try requireCatalog()
        let players = catalog.metrics(inScope: "player")
        let teams = catalog.metrics(inScope: "team")
        XCTAssertFalse(players.isEmpty)
        XCTAssertFalse(teams.isEmpty)
        XCTAssertTrue(players.allSatisfy { $0.scope.contains("player") })
        XCTAssertTrue(teams.allSatisfy { $0.scope.contains("team") })
        XCTAssertEqual(catalog.metrics(inScope: "any").count, catalog.metrics.count)
    }

    // MARK: - Presets

    func testEveryPresetDecodesIntoALayout() throws {
        let catalog = try requireCatalog()
        XCTAssertEqual(catalog.presets.count, expectedPresetCount)
        for preset in catalog.presets {
            XCTAssertTrue(preset.isPreset, "\(preset.name) is not flagged as a preset")
            XCTAssertEqual(preset.schemaVersion, DashboardLayout.currentSchemaVersion)
            XCTAssertFalse(preset.name.isEmpty)
            XCTAssertFalse(preset.icon.isEmpty)
            XCTAssertNotNil(preset.presetKey, "\(preset.name) has no presetKey")
        }
        XCTAssertEqual(Set(catalog.presets.compactMap { $0.presetKey }).count, expectedPresetCount)
    }

    func testEveryPresetIsReachableByItsKey() throws {
        let catalog = try requireCatalog()
        for preset in catalog.presets {
            guard let key = preset.presetKey else { continue }
            XCTAssertEqual(catalog.preset(key)?.id, preset.id)
        }
        XCTAssertNil(catalog.preset("no_such_preset"))
    }

    func testEveryPresetWidgetKindIsKnown() throws {
        let catalog = try requireCatalog()
        var widgetCount = 0
        for preset in catalog.presets {
            for widget in preset.widgets {
                widgetCount += 1
                XCTAssertNotNil(WidgetKind(rawValue: widget.kind.rawValue))
                XCTAssertNotNil(catalog.widget(widget.kind),
                                "\(preset.name) uses \(widget.kind.rawValue), which the widget catalog does not list")
            }
        }
        XCTAssertGreaterThan(widgetCount, 0, "The presets carry no widgets at all")
    }

    func testEveryPresetWidgetIDIsUniqueWithinItsLayout() throws {
        let catalog = try requireCatalog()
        for preset in catalog.presets {
            let ids = preset.widgets.map { $0.id }
            XCTAssertEqual(Set(ids).count, ids.count, "\(preset.name) has two widgets sharing an id")
        }
    }

    func testEveryPresetSizeIsOneTheWidgetSupports() throws {
        let catalog = try requireCatalog()
        for preset in catalog.presets {
            for widget in preset.widgets {
                guard let spec = catalog.widget(widget.kind) else { continue }
                XCTAssertTrue(spec.sizes.contains(widget.size),
                              "\(preset.name): \(widget.kind.rawValue) cannot be \(widget.size.rawValue)")
            }
        }
    }

    /// Every metric key a preset names has to exist, or the tile resolves to an error on a fresh
    /// install — the one failure a new reader would blame on the app rather than on the data.
    func testEveryMetricKeyAPresetReferencesExists() throws {
        let catalog = try requireCatalog()
        var checked = 0
        for preset in catalog.presets {
            for widget in preset.widgets {
                guard let spec = catalog.widget(widget.kind) else { continue }
                for field in spec.config {
                    guard let value = widget.config[field.key] else { continue }
                    let label = "\(preset.name) · \(widget.kind.rawValue) · \(field.key)"
                    switch field.type {
                    case .metric:
                        guard let key = value.stringValue, !key.isEmpty else { continue }
                        checked += 1
                        XCTAssertNotNil(catalog.metric(key), "\(label): unknown metric \"\(key)\"")
                    case .metricList:
                        for element in value.arrayValue ?? [] {
                            guard let key = element.stringValue, !key.isEmpty else { continue }
                            checked += 1
                            XCTAssertNotNil(catalog.metric(key), "\(label): unknown metric \"\(key)\"")
                        }
                    default:
                        continue
                    }
                }
            }
        }
        XCTAssertGreaterThan(checked, 0, "No preset named a metric, so this test proved nothing")
    }

    /// A preset must not carry a configuration key its widget no longer has: the migrator would
    /// strip it on load and show the reader a "your dashboards were updated" banner for a
    /// dashboard they never edited.
    func testNoPresetCarriesAnUnknownConfigKey() throws {
        let catalog = try requireCatalog()
        for preset in catalog.presets {
            for widget in preset.widgets {
                guard let spec = catalog.widget(widget.kind) else { continue }
                let known = Set(spec.config.map { $0.key })
                for key in widget.config.keys {
                    XCTAssertTrue(known.contains(key),
                                  "\(preset.name) · \(widget.kind.rawValue): unknown config key \"\(key)\"")
                }
            }
        }
    }

    /// Every enum-valued preset setting is one of the choices the catalog offers, so the config
    /// sheet never opens on a value it cannot show.
    func testEveryPresetEnumValueIsOneTheCatalogOffers() throws {
        let catalog = try requireCatalog()
        for preset in catalog.presets {
            for widget in preset.widgets {
                guard let spec = catalog.widget(widget.kind) else { continue }
                for field in spec.config where field.type == .`enum` {
                    guard let options = field.options,
                          let value = widget.config[field.key]?.stringValue else { continue }
                    XCTAssertTrue(options.contains(value),
                                  "\(preset.name) · \(widget.kind.rawValue).\(field.key): \"\(value)\" is not offered")
                }
            }
        }
    }

    // MARK: - Default configuration

    /// `defaultConfig` fills in every required field the catalog gives a default for. The subject
    /// pickers deliberately have none — a widget cannot guess which player the reader meant — so
    /// those, and only those, are left for the editor to fill in.
    func testDefaultConfigFillsEveryRequiredFieldThatHasADefault() throws {
        let catalog = try requireCatalog()
        for kind in WidgetKind.allCases {
            guard let spec = catalog.widget(kind) else { continue }
            let config = catalog.defaultConfig(for: kind)
            for field in spec.config where field.required {
                if config[field.key] != nil { continue }
                XCTAssertTrue(subjectFieldTypes.contains(field.type),
                              "\(kind.rawValue).\(field.key) is required, has no default, and is not a subject picker")
            }
            let known = Set(spec.config.map { $0.key })
            for key in config.keys {
                XCTAssertTrue(known.contains(key), "defaultConfig invented \"\(key)\" for \(kind.rawValue)")
            }
        }
    }

    func testMissingRequiredFieldsNamesExactlyTheSubjectPickers() throws {
        let catalog = try requireCatalog()
        for kind in WidgetKind.allCases {
            guard let spec = catalog.widget(kind) else { continue }
            let defaults = catalog.defaultConfig(for: kind)
            let missing = catalog.missingRequiredFields(for: kind, config: defaults)
            for field in missing {
                XCTAssertTrue(field.required)
                XCTAssertTrue(subjectFieldTypes.contains(field.type),
                              "\(kind.rawValue).\(field.key) is missing from the defaults but is not a subject picker")
            }
            let expected = spec.config.filter { $0.required && defaults[$0.key] == nil }
            XCTAssertEqual(Set(missing.map { $0.key }), Set(expected.map { $0.key }))
        }
    }

    func testNormalizedConfigFillsDefaultsAndStripsUnknownKeys() throws {
        let catalog = try requireCatalog()
        let spec = try XCTUnwrap(catalog.widget(.leaderboard))
        let normalized = catalog.normalizedConfig(for: .leaderboard, config: ["nonsense": .string("drop me")])
        XCTAssertNil(normalized["nonsense"], "An unknown key survived normalization")
        var checked = 0
        for field in spec.config {
            guard let defaultValue = field.`default`, !defaultValue.isNull else { continue }
            checked += 1
            XCTAssertEqual(normalized[field.key], defaultValue, "\(field.key) did not take its catalog default")
        }
        XCTAssertGreaterThan(checked, 0, "The leaderboard spec declares no defaults to fill in")
    }

    func testNormalizedConfigKeepsAValueTheReaderChose() throws {
        let catalog = try requireCatalog()
        let normalized = catalog.normalizedConfig(for: .leaderboard,
                                                  config: ["metric": .string("pts"), "limit": .int(25)])
        XCTAssertEqual(normalized["metric"], .string("pts"))
        XCTAssertEqual(normalized["limit"], .int(25))
    }

    func testMakeWidgetUsesTheCatalogDefaultSizeAndConfig() throws {
        let catalog = try requireCatalog()
        for kind in WidgetKind.allCases {
            guard let spec = catalog.widget(kind) else { continue }
            let widget = catalog.makeWidget(kind: kind)
            XCTAssertEqual(widget.kind, kind)
            XCTAssertEqual(widget.size, spec.defaultSize)
            XCTAssertTrue(spec.sizes.contains(widget.size))
            XCTAssertEqual(widget.config, catalog.defaultConfig(for: kind))
            XCTAssertFalse(widget.id.isEmpty)
        }
    }

    // MARK: - Formatting and era rules

    func testFormatUsesTheMetricsOwnFormat() throws {
        let catalog = try requireCatalog()
        try XCTSkipIf(catalog.metric("ts_pct") == nil, "ts_pct is not in this catalog")
        XCTAssertFormatted(catalog.format("ts_pct", 0.6153), "61.5%")
        XCTAssertEqual(catalog.format("ts_pct", nil), Formatting.emDash)
    }

    func testFormatOfAnUnknownMetricStillNeverShowsZeroForNil() throws {
        let catalog = try requireCatalog()
        XCTAssertEqual(catalog.format("no_such_metric", nil), Formatting.emDash)
    }

    /// The era rule the whole app rests on: a stat that did not exist reports `unavailable`, so no
    /// view is ever handed a number it could round down to zero.
    func testAvailabilityRespectsTheEraBoundaries() throws {
        let catalog = try requireCatalog()
        // Any metric whose record starts after the league's first seasons will do.
        let candidate = catalog.metrics.first { metric in
            guard let year = Formatting.seasonStartYear(metric.availability.seasonFrom) else { return false }
            return year > 1950
        }
        let metric = try XCTUnwrap(candidate, "No era-limited metric in the catalog")
        let firstYear = try XCTUnwrap(Formatting.seasonStartYear(metric.availability.seasonFrom))
        let before = CatalogTests.seasonString(startingIn: firstYear - 5)
        XCTAssertEqual(catalog.availability(for: metric.key, season: before, perGame: false), .unavailable,
                       "\(metric.key) should not exist in \(before)")
        XCTAssertNotEqual(catalog.availability(for: metric.key, season: "2025-26", perGame: false), .unavailable)
    }

    func testAvailabilityOfAnUnknownMetricIsUnavailable() throws {
        let catalog = try requireCatalog()
        XCTAssertEqual(catalog.availability(for: "no_such_metric", season: "2025-26", perGame: true), .unavailable)
    }

    func testAvailabilityOfATokenSeasonIsTreatedAsModern() throws {
        let catalog = try requireCatalog()
        let metric = try XCTUnwrap(catalog.metrics.first)
        XCTAssertEqual(catalog.availability(for: metric.key, season: "latest", perGame: false), .full)
    }

    func testEveryEraBoundaryNamesARealSeason() throws {
        let catalog = try requireCatalog()
        XCTAssertFalse(catalog.eraBoundaries.isEmpty)
        for boundary in catalog.eraBoundaries {
            XCTAssertNotNil(Formatting.seasonStartYear(boundary.season),
                            "Era boundary \"\(boundary.season)\" is not a season string")
            XCTAssertFalse(boundary.label.isEmpty)
        }
    }

    // MARK: - Runtime replacement

    func testUpdateNeverTradesAWorkingCatalogForAnEmptyOne() throws {
        let catalog = try requireCatalog()
        let before = catalog.metrics.count
        catalog.update(metrics: [], widgets: [], presets: [])
        XCTAssertEqual(catalog.metrics.count, before)
        catalog.update(metrics: nil, widgets: nil, presets: nil)
        XCTAssertEqual(catalog.metrics.count, before)
    }

    func testUpdateReplacesTheMetricsItIsGiven() throws {
        let catalog = try requireCatalog()
        let replacement = MetricDescriptor.preview
        catalog.update(metrics: [replacement], widgets: nil, presets: nil)
        XCTAssertEqual(catalog.metrics.count, 1)
        XCTAssertEqual(catalog.metric(replacement.key), replacement)
        XCTAssertNil(catalog.metric("no_such_metric"))
    }

    // MARK: - The kinds and the catalog agree

    /// Both directions, against the *raw* document rather than the decoded one.
    ///
    /// `WidgetCatalogDocument` decodes its entries leniently, so a catalog kind this build does
    /// not know is dropped silently and would never show up in `catalog.widgets`. Reading the
    /// kind strings straight out of the JSON is what makes "and vice versa" mean anything.
    func testEveryWidgetKindHasACatalogEntryAndEveryCatalogEntryHasAKind() throws {
        let catalog = try requireCatalog()
        let data = try XCTUnwrap(TestBundles.contractData(named: "widgets"), "widgets.json is not in the bundle")
        let document = try JSONDecoder().decode(JSONValue.self, from: data)
        let rawKinds = (document["widgets"]?.arrayValue ?? []).compactMap { $0["kind"]?.stringValue }

        XCTAssertEqual(rawKinds.count, expectedWidgetCount, "The widget catalog is not the size the contract says")
        XCTAssertEqual(Set(rawKinds).count, rawKinds.count, "Two catalog entries share a kind")
        XCTAssertEqual(Set(rawKinds), Set(WidgetKind.allCases.map { $0.rawValue }), """
            The bundled catalog and WidgetKind disagree. In the catalog only: \
            \(Set(rawKinds).subtracting(WidgetKind.allCases.map { $0.rawValue }).sorted()); \
            in WidgetKind only: \
            \(Set(WidgetKind.allCases.map { $0.rawValue }).subtracting(rawKinds).sorted()).
            """)

        for kind in WidgetKind.allCases {
            XCTAssertNotNil(catalog.widget(kind), "No catalog entry for \(kind.rawValue)")
        }
        for raw in rawKinds {
            XCTAssertNotNil(WidgetKind(rawValue: raw), "The catalog offers \"\(raw)\", which this build cannot render")
        }
    }

    /// The preset count, from the raw document for the same reason.
    func testThePresetDocumentCarriesEveryPresetTheCatalogLoaded() throws {
        let catalog = try requireCatalog()
        let data = try XCTUnwrap(TestBundles.contractData(named: "presets"), "presets.json is not in the bundle")
        let document = try JSONDecoder().decode(JSONValue.self, from: data)
        let rawKeys = (document["presets"]?.arrayValue ?? []).compactMap { $0["presetKey"]?.stringValue }

        XCTAssertEqual(rawKeys.count, expectedPresetCount)
        XCTAssertEqual(Set(rawKeys), Set(catalog.presets.compactMap { $0.presetKey }),
                       "A preset in the document did not survive decoding into a layout")
    }

    // MARK: - The Next Game preset

    func testTheNextGamePresetIsShippedAndItsProjectionWidgetValidates() throws {
        let catalog = try requireCatalog()
        let preset = try XCTUnwrap(catalog.preset("next_game"),
                                   "The Next Game preset is not in the bundled catalog")
        XCTAssertTrue(preset.isPreset)
        XCTAssertEqual(preset.schemaVersion, DashboardLayout.currentSchemaVersion)
        XCTAssertFalse(preset.name.isEmpty)
        XCTAssertFalse(preset.widgets.isEmpty)

        let widget = try XCTUnwrap(preset.widgets.first { $0.kind == .nextGameProjection },
                                   "The Next Game preset does not contain a next_game_projection widget")
        let spec = try XCTUnwrap(catalog.widget(.nextGameProjection),
                                 "next_game_projection is not in the widget catalog")

        XCTAssertTrue(spec.sizes.contains(widget.size),
                      "The preset asks for a size the widget does not offer")
        XCTAssertEqual(ConfigValidator.issues(for: spec, config: widget.config), [:],
                       "The shipped Next Game configuration does not validate")
        XCTAssertTrue(catalog.missingRequiredFields(for: .nextGameProjection, config: widget.config).isEmpty,
                      "The shipped Next Game configuration is missing a required field")

        let known = Set(spec.config.map { $0.key })
        for key in widget.config.keys {
            XCTAssertTrue(known.contains(key), "The preset carries an unknown config key \"\(key)\"")
        }

        // The subject is a token rather than a hard-coded player: a shipped dashboard cannot know
        // whose next game the reader cares about.
        let playerId = try XCTUnwrap(widget.config["playerId"]?.stringValue,
                                     "The preset pins a concrete player instead of a subject token")
        XCTAssertTrue(playerId.hasPrefix("$"), "\"\(playerId)\" is not a subject token")

        // An explicitly unset optional subject survives normalization as an explicit null, so the
        // server resolves the opponent from the schedule rather than guessing the key was dropped.
        let normalized = catalog.normalizedConfig(for: .nextGameProjection, config: widget.config)
        XCTAssertEqual(normalized["playerId"], widget.config["playerId"])
        XCTAssertEqual(normalized["interval"], JSONValue.string("80"))
        XCTAssertEqual(normalized["opponentTeamId"], JSONValue.null)
    }

    func testTheNextGameProjectionSpecOffersTheControlsTheWidgetReads() throws {
        let catalog = try requireCatalog()
        let spec = try XCTUnwrap(catalog.widget(.nextGameProjection))

        XCTAssertEqual(spec.defaultSize, .large)
        XCTAssertFalse(spec.sizes.contains(.small),
                       "A projection cannot show a mean, an interval and the factors in one grid column")
        XCTAssertEqual(spec.availableFrom, "1996-97")
        XCTAssertGreaterThanOrEqual(spec.minRefreshSeconds, 60)

        let keys = Set(spec.config.map { $0.key })
        for key in ["playerId", "stats", "interval", "showCombo", "showFactors", "season", "seasonType"] {
            XCTAssertTrue(keys.contains(key), "next_game_projection has no \"\(key)\" field")
        }

        let playerField = try XCTUnwrap(spec.field("playerId"))
        XCTAssertEqual(playerField.type, .player)
        XCTAssertTrue(playerField.required, "A projection with no subject is not a projection")

        let interval = try XCTUnwrap(spec.field("interval"))
        XCTAssertEqual(interval.type, .`enum`)
        XCTAssertEqual(interval.`default`?.stringValue, "80",
                       "The paper's nominal level is 80%, and that is what a fresh widget should use")
        XCTAssertEqual(Set(interval.options ?? []), ["80", "50", "none"])

        let stats = try XCTUnwrap(spec.field("stats"))
        XCTAssertEqual(stats.type, .metricList)
        let defaults = stats.`default`?.arrayValue ?? []
        XCTAssertFalse(defaults.isEmpty, "A projection with no statistics projects nothing")
        if let maxItems = stats.maxItems {
            XCTAssertLessThanOrEqual(defaults.count, maxItems, "The default stat list is longer than maxItems")
        }
        for element in defaults {
            let key = try XCTUnwrap(element.stringValue)
            XCTAssertNotNil(catalog.metric(key), "The default stat \"\(key)\" is not in the metric catalog")
        }

        let opponent = try XCTUnwrap(spec.field("opponentTeamId"))
        XCTAssertEqual(opponent.type, .team)
        XCTAssertFalse(opponent.required, "The opponent comes from the schedule unless the reader overrides it")
    }

    // MARK: - No market translation, anywhere in the catalog

    /// `docs/PROJECTION.md` §6: the source pipeline's market translation is deliberately absent,
    /// first because NBA.com's terms forbid using their statistics in connection with gambling.
    /// A control the catalog offers is a control the app has to build, so the rule is enforced on
    /// the catalog and not only on the views.
    func testNothingInTheCatalogOffersAMarketOrABettingControl() throws {
        let catalog = try requireCatalog()
        let forbidden = ["odds", "betting", "parlay", "wager", "moneyline", "payout",
                         "kelly", "staking", "vigorish", "implied probability", "expected value"]

        var inspected: [(String, String)] = []
        for spec in catalog.widgets {
            inspected.append((spec.name, "\(spec.kind.rawValue).name"))
            inspected.append((spec.summary, "\(spec.kind.rawValue).summary"))
            for field in spec.config {
                inspected.append((field.key, "\(spec.kind.rawValue).\(field.key)"))
                inspected.append((field.label, "\(spec.kind.rawValue).\(field.key).label"))
                inspected.append((field.help ?? "", "\(spec.kind.rawValue).\(field.key).help"))
                for option in field.options ?? [] {
                    inspected.append((option, "\(spec.kind.rawValue).\(field.key) option"))
                }
            }
        }
        for preset in catalog.presets {
            inspected.append((preset.name, "preset \(preset.presetKey ?? preset.id).name"))
            inspected.append((preset.tagline ?? "", "preset \(preset.presetKey ?? preset.id).tagline"))
            for widget in preset.widgets {
                inspected.append((widget.title ?? "", "preset \(preset.presetKey ?? preset.id) widget title"))
            }
        }

        XCTAssertGreaterThan(inspected.count, 0, "Nothing was inspected, so this test proved nothing")
        for (text, location) in inspected {
            let lowered = text.lowercased()
            for term in forbidden {
                XCTAssertFalse(lowered.contains(term),
                               "\(location) mentions \"\(term)\", which docs/PROJECTION.md §6 rules out")
            }
        }
    }

    // MARK: - Helpers

    /// `1996` becomes `"1996-97"`, `1999` becomes `"1999-00"`.
    private static func seasonString(startingIn year: Int) -> String {
        let tail = (((year + 1) % 100) + 100) % 100
        return tail < 10 ? "\(year)-0\(tail)" : "\(year)-\(tail)"
    }
}
