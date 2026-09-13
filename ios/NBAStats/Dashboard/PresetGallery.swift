import Foundation
import SwiftUI

/// The nine dashboards Hardwood ships with.
///
/// A preset is never edited in place: "Use this" copies it into an editable layout that keeps its
/// `presetKey`, so the copy can still be reset back to the original later.
public struct PresetGallery: View {

    @ObservedObject private var catalog: Catalog
    private let onUse: ((DashboardLayout) -> Void)?

    @ObservedObject private var store: DashboardStore
    @Environment(\.dismiss) private var dismiss

    public init(catalog: Catalog,
                store: DashboardStore,
                onUse: ((DashboardLayout) -> Void)? = nil) {
        _catalog = ObservedObject(wrappedValue: catalog)
        _store = ObservedObject(wrappedValue: store)
        self.onUse = onUse
    }

    // MARK: Derived

    private var presets: [DashboardLayout] { catalog.presets }

    /// The preset keys the reader already has a copy of.
    private var usedKeys: Set<String> {
        Set(store.layouts.compactMap { $0.presetKey })
    }

    private func presetKey(for preset: DashboardLayout) -> String {
        preset.presetKey ?? preset.id
    }

    /// "Scoreboard · 2 × Daily Movers · Stat Tile", in the preset's own order.
    private func contents(of preset: DashboardLayout) -> String {
        guard !preset.widgets.isEmpty else {
            return "Empty, so you can build it yourself."
        }
        var order: [WidgetKind] = []
        var counts: [WidgetKind: Int] = [:]
        for widget in preset.widgets {
            if counts[widget.kind] == nil {
                order.append(widget.kind)
            }
            counts[widget.kind] = (counts[widget.kind] ?? 0) + 1
        }
        let names = order.map { kind -> String in
            let name = catalog.defaultTitle(for: kind)
            let count = counts[kind] ?? 1
            return count > 1 ? "\(count) × \(name)" : name
        }
        return names.joined(separator: " · ")
    }

    private func widgetCountText(_ preset: DashboardLayout) -> String {
        preset.widgets.count == 1 ? "1 widget" : "\(preset.widgets.count) widgets"
    }

    // MARK: Body

    public var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(spacing: Spacing.md) {
                    if presets.isEmpty {
                        Text("No presets shipped with this build.")
                            .hardwoodText(.tableCell, color: Palette.textSecondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(Spacing.lg)
                    }
                    ForEach(presets) { preset in
                        card(for: preset)
                    }
                }
                .padding(Spacing.lg)
            }
            .hardwoodBackground()
            .navigationTitle("Presets")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    private func card(for preset: DashboardLayout) -> some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            header(for: preset)
            Text(contents(of: preset))
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
            footer(for: preset)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .hardwoodCard(padding: Spacing.lg)
        .accessibilityElement(children: .contain)
    }

    private func header(for preset: DashboardLayout) -> some View {
        HStack(alignment: .top, spacing: Spacing.md) {
            Image(systemName: preset.icon)
                .font(.title3)
                .foregroundStyle(preset.accent.color)
                .frame(width: 40, height: 40)
                .background(Circle().fill(preset.accent.softTint))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                Text(preset.name)
                    .hardwoodText(.sectionTitle)
                if let tagline = preset.tagline, !tagline.isEmpty {
                    Text(tagline)
                        .hardwoodText(.tableCell, color: Palette.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
        }
    }

    private func footer(for preset: DashboardLayout) -> some View {
        HStack(spacing: Spacing.sm) {
            Text(widgetCountText(preset))
                .hardwoodText(.caption)
            if usedKeys.contains(presetKey(for: preset)) {
                Label("In your dashboards", systemImage: "checkmark.circle")
                    .hardwoodText(.caption, color: Palette.positive)
            }
            Spacer(minLength: Spacing.sm)
            Button("Use this") {
                use(preset)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.small)
            .tint(preset.accent.color)
            .accessibilityHint("Copies “\(preset.name)” into a dashboard you can edit.")
        }
    }

    private func use(_ preset: DashboardLayout) {
        let created = store.addLayout(fromPreset: presetKey(for: preset))
        onUse?(created)
        dismiss()
    }
}

#if DEBUG
#Preview("Preset gallery") {
    PresetGallery(catalog: DashboardPreviewData.catalog, store: DashboardPreviewData.store())
}
#endif
