import Foundation
import SwiftUI

/// The dynamic configuration form for one widget.
///
/// Nothing here is hard-coded per widget kind: the controls are built at runtime from
/// `WidgetSpec.config`, one editor per `ConfigFieldType`, and the same code therefore configures a
/// widget kind that only ever existed on the server.
public struct WidgetConfigSheet: View {

    private let widget: DashboardWidget
    private let layoutID: String
    private let catalog: Catalog

    @ObservedObject private var store: DashboardStore
    @StateObject private var directory: ConfigSubjectDirectory

    @Environment(\.dismiss) private var dismiss

    @State private var draft: [String: JSONValue]
    @State private var title: String
    @State private var size: WidgetSize
    @State private var isConfirmingReset = false

    private let seasons: [String]

    public init(widget: DashboardWidget,
                layoutID: String,
                store: DashboardStore,
                catalog: Catalog,
                client: (any APIClientProtocol)? = nil) {
        self.widget = widget
        self.layoutID = layoutID
        self.catalog = catalog
        _store = ObservedObject(wrappedValue: store)
        _directory = StateObject(wrappedValue: ConfigSubjectDirectory(client: client))
        _draft = State(initialValue: catalog.normalizedConfig(for: widget.kind, config: widget.config))
        _title = State(initialValue: widget.title ?? "")
        _size = State(initialValue: widget.size)
        self.seasons = SeasonOptions.allSeasons()
    }

    // MARK: Derived

    private var spec: WidgetSpec? { catalog.widget(widget.kind) }

    private var defaultTitle: String { catalog.defaultTitle(for: widget.kind) }

    private var availableSizes: [WidgetSize] {
        guard let spec = spec, !spec.sizes.isEmpty else { return WidgetSize.allCases }
        return spec.sizes.contains(size) ? spec.sizes : [size] + spec.sizes
    }

    private var issues: [String: String] {
        guard let spec = spec else { return [:] }
        return ConfigValidator.issues(for: spec, config: draft)
    }

    private var isValid: Bool { issues.isEmpty }

    /// Whether any field needs the player / team directory loaded up front.
    private var needsTeams: Bool {
        guard let spec = spec else { return false }
        return spec.config.contains { field in
            switch field.type {
            case .team, .teamList, .subject, .subjectList:
                return true
            default:
                return false
            }
        }
    }

    // MARK: Body

    public var body: some View {
        NavigationStack {
            Form {
                appearanceSection
                configurationSection
                aboutSection
            }
            .navigationTitle(spec?.name ?? widget.kind.fallbackName)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { save() }
                        .disabled(!isValid)
                        .fontWeight(.semibold)
                }
            }
            .task {
                if needsTeams {
                    await directory.loadTeamsIfNeeded()
                }
            }
        }
    }

    // MARK: Sections

    private var appearanceSection: some View {
        Section {
            TextField("Title", text: $title, prompt: Text(defaultTitle))
                .textInputAutocapitalization(.words)
            Picker("Size", selection: $size) {
                ForEach(availableSizes, id: \.self) { option in
                    Text(option.displayName).tag(option)
                }
            }
            .pickerStyle(.segmented)
        } header: {
            Text("Widget")
        } footer: {
            Text("Leave the title empty to use “\(defaultTitle)”.")
        }
    }

    @ViewBuilder private var configurationSection: some View {
        if let spec = spec, !spec.config.isEmpty {
            Section {
                ForEach(spec.config) { field in
                    fieldRow(field)
                }
            } header: {
                Text("Configuration")
            }
        } else if spec == nil {
            Section {
                Text("This widget came from a newer catalog, so Hardwood cannot offer its settings. Its title and size are still yours to change.")
                    .hardwoodText(.tableCell, color: Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var aboutSection: some View {
        Section {
            if let spec = spec {
                Text(spec.summary)
                    .hardwoodText(.tableCell, color: Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                if let availableFrom = spec.availableFrom {
                    Label("Only available from \(availableFrom) onward.", systemImage: "clock.arrow.circlepath")
                        .hardwoodText(.caption, color: Palette.warning)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Button("Reset to defaults", role: .destructive) {
                isConfirmingReset = true
            }
            .confirmationDialog("Reset this widget's settings?",
                                isPresented: $isConfirmingReset,
                                titleVisibility: .visible) {
                Button("Reset", role: .destructive) { resetToDefaults() }
                Button("Keep my settings", role: .cancel) { }
            }
        }
    }

    @ViewBuilder private func fieldRow(_ field: WidgetSpec.ConfigField) -> some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            ConfigFieldEditor(field: field,
                              value: binding(for: field),
                              catalog: catalog,
                              directory: directory,
                              seasons: seasons,
                              subjectKind: subjectKind(for: field))
            if let help = field.help, !help.isEmpty {
                Text(help)
                    .hardwoodText(.caption)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let issue = issues[field.key] {
                Label(issue, systemImage: "exclamationmark.circle")
                    .hardwoodText(.caption, color: Palette.negative)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.vertical, Spacing.xxs)
    }

    // MARK: Plumbing

    /// A binding into one key of the draft. Changing a field that others depend on (`subjectType`)
    /// clears those others, because a player id is meaningless once the widget is about a team.
    private func binding(for field: WidgetSpec.ConfigField) -> Binding<JSONValue?> {
        Binding(
            get: { draft[field.key] },
            set: { newValue in
                let changed = draft[field.key] != newValue
                draft[field.key] = newValue
                if changed {
                    clearDependents(of: field.key)
                }
            }
        )
    }

    private func clearDependents(of key: String) {
        guard let spec = spec else { return }
        for dependent in spec.config where dependent.dependsOn == key {
            draft[dependent.key] = dependent.type.isMultiValue ? JSONValue.array([]) : JSONValue.null
        }
    }

    private func subjectKind(for field: WidgetSpec.ConfigField) -> ConfigSubjectKind {
        guard let dependsOn = field.dependsOn, let raw = draft[dependsOn]?.stringValue else {
            return .player
        }
        return ConfigSubjectKind(rawValue: raw) ?? .player
    }

    private func resetToDefaults() {
        draft = catalog.defaultConfig(for: widget.kind)
        title = ""
        size = spec?.defaultSize ?? widget.size
    }

    private func save() {
        store.updateWidget(widget.id,
                           config: draft,
                           title: title,
                           size: size,
                           in: layoutID)
        dismiss()
    }
}

#if DEBUG
#Preview("Configure a widget") {
    let store = DashboardPreviewData.store()
    let widget = store.selectedLayout?.widgets.first
        ?? DashboardWidget(kind: .statTile, size: .small, config: [:])
    return WidgetConfigSheet(widget: widget,
                             layoutID: store.selectedLayout?.id ?? "",
                             store: store,
                             catalog: DashboardPreviewData.catalog,
                             client: nil)
}

#Preview("Configure a leaderboard") {
    let store = DashboardPreviewData.store()
    let widget = store.selectedLayout?.widgets.first(where: { $0.kind == .leaderboard })
        ?? DashboardWidget(kind: .leaderboard, size: .large, config: [:])
    return WidgetConfigSheet(widget: widget,
                             layoutID: store.selectedLayout?.id ?? "",
                             store: store,
                             catalog: DashboardPreviewData.catalog,
                             client: nil)
}
#endif
