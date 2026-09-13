import Combine
import Foundation
import OSLog

// MARK: - Catalog documents

/// A metric category, used to group the metric picker (`contracts/metrics.json#/categories`).
public struct MetricCategory: Codable, Hashable, Sendable, Identifiable {
    public let key: String
    public let name: String

    public var id: String { key }

    public init(key: String, name: String) {
        self.key = key
        self.name = name
    }
}

/// A season where the league's record changes (`contracts/metrics.json#/eraBoundaries`).
public struct EraBoundary: Codable, Hashable, Sendable, Identifiable {
    public let season: String
    public let label: String
    public let detail: String?

    public var id: String { season }

    public init(season: String, label: String, detail: String? = nil) {
        self.season = season
        self.label = label
        self.detail = detail
    }
}

/// One formatting rule (`contracts/metrics.json#/formats`). The client implements the rules in
/// `Formatting`; this type exists so the bundled file round-trips and so a server-side change is
/// at least visible.
public struct MetricFormatSpec: Codable, Hashable, Sendable, Identifiable {
    public let key: String
    public let decimals: Int
    public let suffix: String?
    public let multiplier: Double?
    public let signed: Bool?

    public var id: String { key }

    public init(key: String, decimals: Int, suffix: String? = nil, multiplier: Double? = nil, signed: Bool? = nil) {
        self.key = key
        self.decimals = decimals
        self.suffix = suffix
        self.multiplier = multiplier
        self.signed = signed
    }
}

/// `contracts/metrics.json` as a whole.
public struct MetricCatalogDocument: Codable, Hashable, Sendable {
    public let schemaVersion: Int?
    public let categories: [MetricCategory]
    public let formats: [MetricFormatSpec]
    public let eraBoundaries: [EraBoundary]
    public let metrics: [MetricDescriptor]

    public init(schemaVersion: Int? = nil,
                categories: [MetricCategory] = [],
                formats: [MetricFormatSpec] = [],
                eraBoundaries: [EraBoundary] = [],
                metrics: [MetricDescriptor] = []) {
        self.schemaVersion = schemaVersion
        self.categories = categories
        self.formats = formats
        self.eraBoundaries = eraBoundaries
        self.metrics = metrics
    }

    private enum CodingKeys: String, CodingKey {
        case schemaVersion, categories, formats, eraBoundaries, metrics
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decodeIfPresent(Int.self, forKey: .schemaVersion)
        categories = try container.decodeIfPresent([MetricCategory].self, forKey: .categories) ?? []
        formats = try container.decodeIfPresent([MetricFormatSpec].self, forKey: .formats) ?? []
        eraBoundaries = try container.decodeIfPresent([EraBoundary].self, forKey: .eraBoundaries) ?? []
        // A descriptor this build cannot read (an unknown format, say) is skipped rather than
        // costing the app every other metric.
        let raw = try container.decodeIfPresent([LenientEntry<MetricDescriptor>].self, forKey: .metrics) ?? []
        metrics = raw.compactMap { $0.value }
    }
}

/// One entry of `contracts/widgets.json#/sizes`.
public struct WidgetSizeSpec: Codable, Hashable, Sendable, Identifiable {
    public let key: WidgetSize
    public let columnsCompact: Int
    public let columnsRegular: Int
    public let height: Double
    public let name: String
    public let summary: String?

    public var id: WidgetSize { key }

    public init(key: WidgetSize, columnsCompact: Int, columnsRegular: Int, height: Double, name: String, summary: String? = nil) {
        self.key = key
        self.columnsCompact = columnsCompact
        self.columnsRegular = columnsRegular
        self.height = height
        self.name = name
        self.summary = summary
    }
}

/// `contracts/widgets.json#/gridColumns`.
public struct GridColumns: Codable, Hashable, Sendable {
    public let compact: Int
    public let regular: Int

    public init(compact: Int = 2, regular: Int = 4) {
        self.compact = compact
        self.regular = regular
    }
}

/// `contracts/widgets.json` as a whole.
public struct WidgetCatalogDocument: Codable, Hashable, Sendable {
    public let schemaVersion: Int?
    public let sizes: [WidgetSizeSpec]
    public let gridColumns: GridColumns?
    public let configFieldTypes: [String]
    public let widgets: [WidgetSpec]

    public init(schemaVersion: Int? = nil,
                sizes: [WidgetSizeSpec] = [],
                gridColumns: GridColumns? = nil,
                configFieldTypes: [String] = [],
                widgets: [WidgetSpec] = []) {
        self.schemaVersion = schemaVersion
        self.sizes = sizes
        self.gridColumns = gridColumns
        self.configFieldTypes = configFieldTypes
        self.widgets = widgets
    }

    private enum CodingKeys: String, CodingKey {
        case schemaVersion, sizes, gridColumns, configFieldTypes, widgets
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decodeIfPresent(Int.self, forKey: .schemaVersion)
        let rawSizes = try container.decodeIfPresent([LenientEntry<WidgetSizeSpec>].self, forKey: .sizes) ?? []
        sizes = rawSizes.compactMap { $0.value }
        gridColumns = try container.decodeIfPresent(GridColumns.self, forKey: .gridColumns)
        configFieldTypes = try container.decodeIfPresent([String].self, forKey: .configFieldTypes) ?? []
        // A widget kind a newer server knows and this build does not is skipped, not fatal.
        let rawWidgets = try container.decodeIfPresent([LenientEntry<WidgetSpec>].self, forKey: .widgets) ?? []
        widgets = rawWidgets.compactMap { $0.value }
    }
}

/// `contracts/presets.json` as a whole.
public struct PresetCatalogDocument: Codable, Hashable, Sendable {
    public let schemaVersion: Int?
    public let subjectTokens: [SubjectToken]
    public let presets: [DashboardLayout]

    public init(schemaVersion: Int? = nil, subjectTokens: [SubjectToken] = [], presets: [DashboardLayout] = []) {
        self.schemaVersion = schemaVersion
        self.subjectTokens = subjectTokens
        self.presets = presets
    }

    private enum CodingKeys: String, CodingKey {
        case schemaVersion, subjectTokens, presets
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decodeIfPresent(Int.self, forKey: .schemaVersion)
        subjectTokens = try container.decodeIfPresent([SubjectToken].self, forKey: .subjectTokens) ?? []
        let raw = try container.decodeIfPresent([LenientEntry<DashboardLayout>].self, forKey: .presets) ?? []
        presets = raw.compactMap { $0.value }
    }
}

/// Decodes one element of an array without ever throwing, so a single bad entry cannot cost the
/// app the whole catalog.
private struct LenientEntry<T: Decodable>: Decodable {
    let value: T?

    init(from decoder: Decoder) throws {
        value = try? T(from: decoder)
    }
}

// MARK: - Widget specs

/// The type of one configuration field (`contracts/widgets.json#/configFieldTypes`).
public enum ConfigFieldType: String, Codable, Hashable, Sendable, CaseIterable {
    case season, `enum`, enumList, metric, metricList, player, playerList
    case team, teamList, subject, subjectList, int, double, bool, date

    /// True for the field types whose editor picks more than one value.
    public var isMultiValue: Bool {
        switch self {
        case .enumList, .metricList, .playerList, .teamList, .subjectList:
            return true
        case .season, .`enum`, .metric, .player, .team, .subject, .int, .double, .bool, .date:
            return false
        }
    }
}

/// One widget kind's catalog entry (`contracts/widgets.json#/widgets`).
public struct WidgetSpec: Codable, Hashable, Sendable, Identifiable {

    /// One configuration field, which the config sheet turns into a control.
    public struct ConfigField: Codable, Hashable, Sendable, Identifiable {
        public let key: String
        public let type: ConfigFieldType
        public let label: String
        public let required: Bool
        /// `default` is a Swift keyword, so the property is back-ticked and mapped explicitly.
        public let `default`: JSONValue?
        public let options: [String]?
        public let help: String?
        public let min: Double?
        public let max: Double?
        public let minItems: Int?
        public let maxItems: Int?
        /// `"player"`, `"team"` or `"any"`: which metrics the picker should offer.
        public let metricScope: String?
        /// The key of the field this one depends on, such as `subjectType`.
        public let dependsOn: String?

        public var id: String { key }

        public init(key: String,
                    type: ConfigFieldType,
                    label: String,
                    required: Bool = false,
                    `default` defaultValue: JSONValue? = nil,
                    options: [String]? = nil,
                    help: String? = nil,
                    min: Double? = nil,
                    max: Double? = nil,
                    minItems: Int? = nil,
                    maxItems: Int? = nil,
                    metricScope: String? = nil,
                    dependsOn: String? = nil) {
            self.key = key
            self.type = type
            self.label = label
            self.required = required
            self.`default` = defaultValue
            self.options = options
            self.help = help
            self.min = min
            self.max = max
            self.minItems = minItems
            self.maxItems = maxItems
            self.metricScope = metricScope
            self.dependsOn = dependsOn
        }

        public enum CodingKeys: String, CodingKey {
            case key, type, label, required
            case `default` = "default"
            case options, help, min, max, minItems, maxItems, metricScope, dependsOn
        }

        public init(from decoder: Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            key = try container.decode(String.self, forKey: .key)
            type = try container.decode(ConfigFieldType.self, forKey: .type)
            label = try container.decodeIfPresent(String.self, forKey: .label) ?? key
            required = try container.decodeIfPresent(Bool.self, forKey: .required) ?? false
            self.`default` = try container.decodeIfPresent(JSONValue.self, forKey: .`default`)
            options = try container.decodeIfPresent([String].self, forKey: .options)
            help = try container.decodeIfPresent(String.self, forKey: .help)
            self.min = try container.decodeIfPresent(Double.self, forKey: .min)
            self.max = try container.decodeIfPresent(Double.self, forKey: .max)
            minItems = try container.decodeIfPresent(Int.self, forKey: .minItems)
            maxItems = try container.decodeIfPresent(Int.self, forKey: .maxItems)
            metricScope = try container.decodeIfPresent(String.self, forKey: .metricScope)
            dependsOn = try container.decodeIfPresent(String.self, forKey: .dependsOn)
        }
    }

    public let kind: WidgetKind
    public let name: String
    public let summary: String
    /// An SF Symbol name.
    public let icon: String
    public let sizes: [WidgetSize]
    public let defaultSize: WidgetSize
    public let minRefreshSeconds: Int
    /// The first season this widget can say anything about, when it is era-limited.
    public let availableFrom: String?
    public let config: [ConfigField]

    public var id: WidgetKind { kind }

    public init(kind: WidgetKind,
                name: String,
                summary: String,
                icon: String,
                sizes: [WidgetSize],
                defaultSize: WidgetSize,
                minRefreshSeconds: Int,
                availableFrom: String? = nil,
                config: [ConfigField] = []) {
        self.kind = kind
        self.name = name
        self.summary = summary
        self.icon = icon
        self.sizes = sizes
        self.defaultSize = defaultSize
        self.minRefreshSeconds = minRefreshSeconds
        self.availableFrom = availableFrom
        self.config = config
    }

    /// The configuration field with this key, when the widget has one.
    public func field(_ key: String) -> ConfigField? {
        config.first { $0.key == key }
    }

    private enum CodingKeys: String, CodingKey {
        case kind, name, summary, icon, sizes, defaultSize, minRefreshSeconds, availableFrom, config
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        kind = try container.decode(WidgetKind.self, forKey: .kind)
        name = try container.decodeIfPresent(String.self, forKey: .name) ?? kind.fallbackName
        summary = try container.decodeIfPresent(String.self, forKey: .summary) ?? ""
        icon = try container.decodeIfPresent(String.self, forKey: .icon) ?? kind.fallbackIcon
        let decodedSizes = try container.decodeIfPresent([WidgetSize].self, forKey: .sizes) ?? []
        sizes = decodedSizes.isEmpty ? [.medium, .large] : decodedSizes
        let decodedDefault = try container.decodeIfPresent(WidgetSize.self, forKey: .defaultSize)
        defaultSize = decodedDefault ?? sizes.first ?? .medium
        minRefreshSeconds = try container.decodeIfPresent(Int.self, forKey: .minRefreshSeconds) ?? 300
        availableFrom = try container.decodeIfPresent(String.self, forKey: .availableFrom)
        let rawFields = try container.decodeIfPresent([LenientEntry<ConfigField>].self, forKey: .config) ?? []
        config = rawFields.compactMap { $0.value }
    }
}

// MARK: - The catalog

/// The bundled contracts, loaded once and replaceable at runtime from `/v1/meta`.
///
/// A malformed bundle is never fatal: a decode failure is logged and leaves the previous (or
/// empty) catalog in place.
@MainActor
public final class Catalog: ObservableObject {

    /// The catalog the app uses.
    public static let shared = Catalog()

    @Published public private(set) var metrics: [MetricDescriptor]
    @Published public private(set) var widgets: [WidgetSpec]
    @Published public private(set) var presets: [DashboardLayout]
    public private(set) var eraBoundaries: [EraBoundary]
    public private(set) var categories: [MetricCategory]
    public private(set) var formats: [MetricFormatSpec]
    public private(set) var subjectTokens: [SubjectToken]
    public private(set) var gridColumns: GridColumns

    private var metricsByKey: [String: MetricDescriptor]
    private var widgetsByKind: [WidgetKind: WidgetSpec]
    private var presetsByKey: [String: DashboardLayout]

    private static let logger = Logger(subsystem: "com.hardwood.nbastats", category: "catalog")

    /// Loads `Resources/Contracts/*.json` from a bundle. Tests pass their own bundle.
    public init(bundle: Bundle = .main) {
        let metricDocument = Catalog.loadDocument(MetricCatalogDocument.self, named: "metrics", bundle: bundle)
        let widgetDocument = Catalog.loadDocument(WidgetCatalogDocument.self, named: "widgets", bundle: bundle)
        let presetDocument = Catalog.loadDocument(PresetCatalogDocument.self, named: "presets", bundle: bundle)

        metrics = metricDocument?.metrics ?? []
        widgets = widgetDocument?.widgets ?? []
        presets = presetDocument?.presets ?? []
        eraBoundaries = metricDocument?.eraBoundaries ?? []
        categories = metricDocument?.categories ?? []
        formats = metricDocument?.formats ?? []
        subjectTokens = presetDocument?.subjectTokens ?? []
        gridColumns = widgetDocument?.gridColumns ?? GridColumns()
        metricsByKey = [:]
        widgetsByKind = [:]
        presetsByKey = [:]

        rebuildIndexes()
    }

    // MARK: Lookups

    public func metric(_ key: String) -> MetricDescriptor? {
        metricsByKey[key]
    }

    /// Every metric that can describe this kind of subject: `"player"`, `"team"`, or `"any"`.
    public func metrics(inScope scope: String) -> [MetricDescriptor] {
        guard scope != "any" else { return metrics }
        return metrics.filter { $0.scope.contains(scope) }
    }

    /// Every metric in a category, in catalog order.
    public func metrics(inCategory category: String) -> [MetricDescriptor] {
        metrics.filter { $0.category == category }
    }

    /// The display name of a metric category, falling back to the key itself.
    public func categoryName(_ key: String) -> String {
        categories.first { $0.key == key }?.name ?? key.capitalized
    }

    public func widget(_ kind: WidgetKind) -> WidgetSpec? {
        widgetsByKind[kind]
    }

    /// A preset by its `presetKey`, or failing that by its layout id.
    public func preset(_ key: String) -> DashboardLayout? {
        presetsByKey[key] ?? presets.first { $0.id == key }
    }

    /// The title a widget shows when its own `title` is `nil`.
    public func defaultTitle(for kind: WidgetKind) -> String {
        widgetsByKind[kind]?.name ?? kind.fallbackName
    }

    /// The SF Symbol for a widget kind.
    public func icon(for kind: WidgetKind) -> String {
        widgetsByKind[kind]?.icon ?? kind.fallbackIcon
    }

    // MARK: Configuration

    /// Every catalog default for a kind. Fields whose default is `null` — the subject pickers —
    /// are left out, because the editor has to fill them in.
    public func defaultConfig(for kind: WidgetKind) -> [String: JSONValue] {
        guard let spec = widgetsByKind[kind] else { return [:] }
        var config: [String: JSONValue] = [:]
        for field in spec.config {
            if let defaultValue = field.`default`, !defaultValue.isNull {
                config[field.key] = defaultValue
            }
        }
        return config
    }

    /// Fills in catalog defaults for missing keys and strips unknown ones.
    public func normalizedConfig(for kind: WidgetKind, config: [String: JSONValue]) -> [String: JSONValue] {
        // With no spec to check against, the configuration is passed through untouched: dropping
        // every key would be worse than keeping one the server may still understand.
        guard let spec = widgetsByKind[kind] else { return config }
        var normalized: [String: JSONValue] = [:]
        for field in spec.config {
            let provided = config[field.key]
            if let provided = provided, !provided.isNull {
                normalized[field.key] = provided
            } else if let defaultValue = field.`default`, !defaultValue.isNull {
                normalized[field.key] = defaultValue
            } else if provided != nil {
                // An explicit null with no default (an unset subject) is kept, so the server can
                // resolve it from context rather than guessing that the key was forgotten.
                normalized[field.key] = .null
            }
        }
        return normalized
    }

    /// The required fields a widget is still missing, for the "finish setting this up" state.
    public func missingRequiredFields(for kind: WidgetKind, config: [String: JSONValue]) -> [WidgetSpec.ConfigField] {
        guard let spec = widgetsByKind[kind] else { return [] }
        return spec.config.filter { field in
            guard field.required else { return false }
            guard let value = config[field.key], !value.isNull else { return true }
            if let array = value.arrayValue, array.isEmpty { return true }
            if let string = value.stringValue, string.isEmpty { return true }
            return false
        }
    }

    /// A new widget of this kind, sized and configured from the catalog.
    public func makeWidget(kind: WidgetKind, size: WidgetSize? = nil, title: String? = nil) -> DashboardWidget {
        let spec = widgetsByKind[kind]
        return DashboardWidget(
            id: UUID().uuidString,
            kind: kind,
            title: title,
            size: size ?? spec?.defaultSize ?? .medium,
            config: defaultConfig(for: kind)
        )
    }

    // MARK: Formatting and era rules

    /// Formats a raw value using the metric's format; `nil` renders as an em dash.
    public func format(_ key: String, _ value: Double?) -> String {
        guard let value = value else { return Formatting.emDash }
        let format = metricsByKey[key]?.format ?? .decimal1
        return Formatting.value(value, format: format)
    }

    /// How far a metric can be trusted for a season (`contracts/CONTRACT.md` §6).
    ///
    /// `perGame` asks for the per-game form of the metric, which for many advanced stats only
    /// exists from 1996-97 even though the season-level number goes back further.
    public func availability(for key: String, season: String, perGame: Bool) -> MetricAvailability {
        guard let descriptor = metricsByKey[key] else { return .unavailable }
        // "latest", "career" and anything else unparseable is treated as the modern era, which is
        // what those tokens mean in a request.
        guard let year = Formatting.seasonStartYear(season) else { return .full }

        if perGame && descriptor.availability.seasonLevelOnly {
            // A season-level-only rating has no per-game form at all; showing one would be a lie.
            return .unavailable
        }
        let fromSeason = perGame ? descriptor.availability.perGameFrom : descriptor.availability.seasonFrom
        if let fromYear = Formatting.seasonStartYear(fromSeason), year < fromYear {
            return .unavailable
        }
        if let estimatedBefore = descriptor.availability.estimatedBefore,
           let estimatedYear = Formatting.seasonStartYear(estimatedBefore),
           year < estimatedYear {
            return .estimated
        }
        return .full
    }

    /// The era boundary a season falls on, for the badge on a career chart.
    public func eraBoundary(at season: String) -> EraBoundary? {
        eraBoundaries.first { $0.season == season }
    }

    // MARK: Refresh

    /// Replaces catalogs the server has newer copies of. A `nil` argument leaves that catalog
    /// alone, and an empty array is ignored for the same reason: never trade a working catalog
    /// for an empty one.
    public func update(metrics: [MetricDescriptor]?, widgets: [WidgetSpec]?, presets: [DashboardLayout]?) {
        if let metrics = metrics, !metrics.isEmpty {
            self.metrics = metrics
        }
        if let widgets = widgets, !widgets.isEmpty {
            self.widgets = widgets
        }
        if let presets = presets, !presets.isEmpty {
            self.presets = presets
        }
        rebuildIndexes()
    }

    /// Applies the catalogs that came back with `/v1/meta`.
    public func update(from meta: MetaResponse) {
        if let document = meta.metrics {
            if !document.eraBoundaries.isEmpty { eraBoundaries = document.eraBoundaries }
            if !document.categories.isEmpty { categories = document.categories }
            if !document.formats.isEmpty { formats = document.formats }
        }
        if let document = meta.widgets, let columns = document.gridColumns {
            gridColumns = columns
        }
        update(metrics: meta.metrics?.metrics, widgets: meta.widgets?.widgets, presets: nil)
    }

    /// Applies the presets that came back with `/v1/presets`.
    public func update(from response: PresetsResponse) {
        if !response.subjectTokens.isEmpty { subjectTokens = response.subjectTokens }
        update(metrics: nil, widgets: nil, presets: response.presets)
    }

    private func rebuildIndexes() {
        var byKey: [String: MetricDescriptor] = [:]
        for descriptor in metrics { byKey[descriptor.key] = descriptor }
        metricsByKey = byKey

        var byKind: [WidgetKind: WidgetSpec] = [:]
        for spec in widgets { byKind[spec.kind] = spec }
        widgetsByKind = byKind

        var byPresetKey: [String: DashboardLayout] = [:]
        for layout in presets {
            if let key = layout.presetKey { byPresetKey[key] = layout }
        }
        presetsByKey = byPresetKey

        if metrics.isEmpty || widgets.isEmpty {
            Catalog.logger.error("Catalog loaded with \(self.metrics.count, privacy: .public) metrics and \(self.widgets.count, privacy: .public) widgets; the bundled contracts may be missing.")
        }
    }

    private static func loadDocument<T: Decodable>(_ type: T.Type, named name: String, bundle: Bundle) -> T? {
        // The contracts are copied into the bundle by scripts/sync_contracts.sh; depending on how
        // the resource is added they land in a Contracts folder or at the bundle root.
        let url = bundle.url(forResource: name, withExtension: "json", subdirectory: "Contracts")
            ?? bundle.url(forResource: name, withExtension: "json")
        guard let url = url else {
            logger.error("Catalog: \(name, privacy: .public).json is not in the bundle.")
            return nil
        }
        do {
            let data = try Data(contentsOf: url)
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            logger.error("Catalog: \(name, privacy: .public).json could not be decoded: \(error.localizedDescription, privacy: .public)")
            return nil
        }
    }
}
