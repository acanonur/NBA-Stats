import Foundation
import SwiftUI

/// Managing the dashboards themselves: rename, reorder, duplicate, delete, recolour, and put one
/// back to the preset it came from.
///
/// Switching between dashboards is the job of the menu in the navigation bar; this is where a
/// dashboard is changed rather than chosen, so every row leads to that dashboard's settings and
/// "Show" is one swipe away.
public struct LayoutSwitcher: View {

    @ObservedObject private var store: DashboardStore
    @ObservedObject private var catalog: Catalog
    private let onSelect: ((DashboardLayout) -> Void)?

    @Environment(\.dismiss) private var dismiss

    @State private var editMode: EditMode = .inactive
    @State private var layoutPendingDeletion: DashboardLayout?

    public init(store: DashboardStore,
                catalog: Catalog,
                onSelect: ((DashboardLayout) -> Void)? = nil) {
        _store = ObservedObject(wrappedValue: store)
        _catalog = ObservedObject(wrappedValue: catalog)
        self.onSelect = onSelect
    }

    // MARK: Derived

    private var isConfirmingDeletion: Binding<Bool> {
        Binding(
            get: { layoutPendingDeletion != nil },
            set: { presented in
                if !presented { layoutPendingDeletion = nil }
            }
        )
    }

    private func subtitle(for layout: DashboardLayout) -> String {
        var parts: [String] = [layout.widgets.count == 1 ? "1 widget" : "\(layout.widgets.count) widgets"]
        if let preset = store.basePreset(for: layout) {
            parts.append("based on \(preset.name)")
        }
        if let updated = layout.updatedAt {
            parts.append("edited \(Formatting.relative(updated))")
        }
        return parts.joined(separator: " · ")
    }

    // MARK: Body

    public var body: some View {
        NavigationStack {
            List {
                dashboardsSection
                createSection
            }
            .listStyle(.insetGrouped)
            .environment(\.editMode, $editMode)
            .navigationTitle("Dashboards")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { toolbarContent }
            .navigationDestination(for: String.self) { layoutID in
                destination(for: layoutID)
            }
            .confirmationDialog("Delete this dashboard?",
                                isPresented: isConfirmingDeletion,
                                titleVisibility: .visible,
                                presenting: layoutPendingDeletion) { doomed in
                Button("Delete “\(doomed.name)”", role: .destructive) {
                    store.delete(doomed.id)
                }
                Button("Keep it", role: .cancel) { }
            } message: { doomed in
                Text("“\(doomed.name)” and its widgets are removed from this device. Hardwood keeps one dashboard at all times, so deleting your last one starts you again from a preset.")
            }
        }
    }

    // MARK: Sections

    private var dashboardsSection: some View {
        Section {
            ForEach(store.layouts) { layout in
                row(for: layout)
            }
            .onMove { offsets, destination in
                store.moveLayouts(fromOffsets: offsets, toOffset: destination)
            }
        } header: {
            Text("Your dashboards")
        } footer: {
            Text("Tap a dashboard to rename it, recolour it or reset it. Swipe right to show it, left for duplicate and delete.")
        }
    }

    private var createSection: some View {
        Section {
            Button {
                let created = store.addBlankLayout(named: "New Dashboard")
                onSelect?(created)
                dismiss()
            } label: {
                Label("New Dashboard", systemImage: "plus")
            }
        } footer: {
            Text("A new dashboard starts empty. Add widgets to it from the Edit button on the board, or copy a preset from the gallery.")
        }
    }

    private func row(for layout: DashboardLayout) -> some View {
        NavigationLink(value: layout.id) {
            HStack(spacing: Spacing.md) {
                Image(systemName: layout.icon)
                    .font(.body)
                    .foregroundStyle(layout.accent.color)
                    .frame(width: 34, height: 34)
                    .background(Circle().fill(layout.accent.softTint))
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: Spacing.xxs) {
                    Text(layout.name)
                        .hardwoodText(.widgetTitle)
                        .lineLimit(1)
                    Text(subtitle(for: layout))
                        .hardwoodText(.caption)
                        .lineLimit(1)
                }
                Spacer(minLength: Spacing.sm)
                selectionMark(for: layout)
            }
            .padding(.vertical, Spacing.xxs)
            .accessibilityElement(children: .combine)
        }
        .swipeActions(edge: .leading, allowsFullSwipe: true) {
            Button {
                show(layout)
            } label: {
                Label("Show", systemImage: "eye")
            }
            .tint(layout.accent.color)
        }
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            Button(role: .destructive) {
                layoutPendingDeletion = layout
            } label: {
                Label("Delete", systemImage: "trash")
            }
            Button {
                store.duplicate(layout)
            } label: {
                Label("Duplicate", systemImage: "plus.square.on.square")
            }
            .tint(Palette.selection)
        }
    }

    @ViewBuilder private func selectionMark(for layout: DashboardLayout) -> some View {
        if layout.id == store.selectedLayoutID {
            Image(systemName: "checkmark.circle.fill")
                .foregroundStyle(layout.accent.color)
                .accessibilityLabel("Currently showing")
        }
    }

    @ViewBuilder private func destination(for layoutID: String) -> some View {
        if let layout = store.layout(id: layoutID) {
            LayoutSettingsView(layout: layout,
                               store: store,
                               catalog: catalog,
                               onShow: { shown in show(shown) })
        } else {
            ContentUnavailableView("That dashboard is gone",
                                   systemImage: "square.slash",
                                   description: Text("It was deleted while this screen was open."))
        }
    }

    @ToolbarContentBuilder private var toolbarContent: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            EditButton()
                .disabled(store.layouts.count < 2)
        }
        ToolbarItem(placement: .confirmationAction) {
            Button("Done") { dismiss() }
        }
    }

    // MARK: Actions

    private func show(_ layout: DashboardLayout) {
        store.select(layout.id)
        onSelect?(layout)
        dismiss()
    }
}

// MARK: - One dashboard's settings

/// Name, symbol, accent — and the three things that change a whole dashboard at once: duplicate,
/// reset to its preset, delete.
struct LayoutSettingsView: View {

    /// The symbols a dashboard can wear. Every one of them ships with the system.
    static let iconChoices: [String] = [
        "square.grid.2x2", "sun.horizon", "flame", "bolt", "chart.xyaxis.line",
        "chart.bar.xaxis", "person.crop.square", "person.2", "sportscourt", "list.number",
        "trophy", "scope", "waveform.path.ecg", "calendar", "star", "binoculars"
    ]

    @ObservedObject private var store: DashboardStore
    @ObservedObject private var catalog: Catalog
    private let layoutID: String
    private let onShow: (DashboardLayout) -> Void

    @Environment(\.dismiss) private var dismiss

    @State private var name: String
    @State private var isConfirmingReset = false
    @State private var isConfirmingDelete = false

    init(layout: DashboardLayout,
         store: DashboardStore,
         catalog: Catalog,
         onShow: @escaping (DashboardLayout) -> Void) {
        _store = ObservedObject(wrappedValue: store)
        _catalog = ObservedObject(wrappedValue: catalog)
        self.layoutID = layout.id
        self.onShow = onShow
        _name = State(initialValue: layout.name)
    }

    // MARK: Derived

    private var layout: DashboardLayout? { store.layout(id: layoutID) }

    private var basePreset: DashboardLayout? {
        guard let layout = layout else { return nil }
        return store.basePreset(for: layout)
    }

    // MARK: Body

    var body: some View {
        Form {
            if let layout = layout {
                nameSection
                iconSection(current: layout.icon)
                accentSection(current: layout.accent)
                actionsSection(layout: layout)
            } else {
                Section {
                    Text("This dashboard was deleted.")
                        .hardwoodText(.tableCell, color: Palette.textSecondary)
                }
            }
        }
        .navigationTitle(layout?.name ?? "Dashboard")
        .navigationBarTitleDisplayMode(.inline)
        .onDisappear(perform: commitName)
    }

    // MARK: Sections

    private var nameSection: some View {
        Section {
            TextField("Name", text: $name)
                .textInputAutocapitalization(.words)
                .submitLabel(.done)
                .onSubmit(commitName)
        } header: {
            Text("Name")
        } footer: {
            Text("The name shows in the dashboard menu at the top of the board.")
        }
    }

    private func iconSection(current: String) -> some View {
        Section {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 54), spacing: Spacing.sm)],
                      spacing: Spacing.sm) {
                ForEach(LayoutSettingsView.iconChoices, id: \.self) { symbol in
                    iconButton(symbol: symbol, isSelected: symbol == current)
                }
            }
            .padding(.vertical, Spacing.xs)
        } header: {
            Text("Symbol")
        }
    }

    private func iconButton(symbol: String, isSelected: Bool) -> some View {
        let tint = layout?.accent.color ?? Palette.selection
        return Button {
            store.setIcon(symbol, for: layoutID)
        } label: {
            Image(systemName: symbol)
                .font(.body)
                .foregroundStyle(isSelected ? tint : Palette.textSecondary)
                .frame(width: 44, height: 44)
                .background(
                    RoundedRectangle(cornerRadius: Radius.control, style: .continuous)
                        .fill(isSelected ? tint.opacity(0.16) : Palette.surfaceSunken)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: Radius.control, style: .continuous)
                        .strokeBorder(isSelected ? tint.opacity(0.6) : Color.clear, lineWidth: 1.5)
                )
        }
        .buttonStyle(.plain)
        .accessibilityLabel(LayoutSettingsView.spokenName(for: symbol))
        .accessibilityAddTraits(isSelected ? AccessibilityTraits.isSelected : [])
    }

    /// `"chart.xyaxis.line"` reads as `"chart xyaxis line"` rather than as one unpronounceable run.
    static func spokenName(for symbol: String) -> String {
        symbol.replacingOccurrences(of: ".", with: " ")
    }

    private func accentSection(current: AccentName) -> some View {
        Section {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 54), spacing: Spacing.sm)],
                      spacing: Spacing.sm) {
                ForEach(AccentName.allCases, id: \.self) { accent in
                    accentButton(accent: accent, isSelected: accent == current)
                }
            }
            .padding(.vertical, Spacing.xs)
        } header: {
            Text("Accent")
        } footer: {
            Text("The accent tints this dashboard's chrome, its buttons and its charts' emphasis.")
        }
    }

    private func accentButton(accent: AccentName, isSelected: Bool) -> some View {
        Button {
            store.setAccent(accent, for: layoutID)
        } label: {
            Circle()
                .fill(accent.color)
                .frame(width: 30, height: 30)
                .overlay(
                    Circle()
                        .strokeBorder(Palette.textPrimary.opacity(isSelected ? 0.85 : 0), lineWidth: 2)
                        .padding(-3)
                )
                .frame(width: 44, height: 44)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(accent.displayName)
        .accessibilityAddTraits(isSelected ? AccessibilityTraits.isSelected : [])
    }

    private func actionsSection(layout: DashboardLayout) -> some View {
        Section {
            Button {
                commitName()
                onShow(layout)
            } label: {
                Label("Show this dashboard", systemImage: "eye")
            }
            Button {
                store.duplicate(layout)
                dismiss()
            } label: {
                Label("Duplicate", systemImage: "plus.square.on.square")
            }
            resetButton
            deleteButton(layout: layout)
        } footer: {
            Text(footerText(for: layout))
        }
    }

    private func deleteButton(layout: DashboardLayout) -> some View {
        Button(role: .destructive) {
            isConfirmingDelete = true
        } label: {
            Label("Delete dashboard", systemImage: "trash")
        }
        .confirmationDialog("Delete “\(layout.name)”?",
                            isPresented: $isConfirmingDelete,
                            titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                store.delete(layoutID)
                dismiss()
            }
            Button("Keep it", role: .cancel) { }
        }
    }

    @ViewBuilder private var resetButton: some View {
        if let preset = basePreset {
            Button {
                isConfirmingReset = true
            } label: {
                Label("Reset to \(preset.name)", systemImage: "arrow.counterclockwise")
            }
            .confirmationDialog("Reset to \(preset.name)?",
                                isPresented: $isConfirmingReset,
                                titleVisibility: .visible) {
                Button("Reset", role: .destructive) {
                    store.resetToPreset(layoutID)
                }
                Button("Keep my changes", role: .cancel) { }
            } message: {
                Text("Every widget goes back to the way “\(preset.name)” ships. Your other dashboards are untouched.")
            }
        }
    }

    private func footerText(for layout: DashboardLayout) -> String {
        let widgets = layout.widgets.count == 1 ? "1 widget" : "\(layout.widgets.count) widgets"
        guard let preset = basePreset else {
            return "\(widgets). Built from scratch."
        }
        return "\(widgets). Based on the \(preset.name) preset, so it can always be reset back to it."
    }

    // MARK: Actions

    private func commitName() {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, trimmed != layout?.name else { return }
        store.rename(layoutID, to: trimmed)
    }
}

#if DEBUG
#Preview("Layout switcher") {
    LayoutSwitcher(store: DashboardPreviewData.store(),
                   catalog: DashboardPreviewData.catalog)
}

#Preview("One dashboard's settings") {
    let store = DashboardPreviewData.store()
    let layout = store.selectedLayout ?? DashboardPreviewData.layout
    return NavigationStack {
        LayoutSettingsView(layout: layout,
                           store: store,
                           catalog: DashboardPreviewData.catalog,
                           onShow: { _ in })
    }
}
#endif
