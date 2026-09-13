import Foundation
import SwiftUI

// MARK: - Banner

/// A dismissible strip above the grid: games that finished, a refresh that failed, a layout that
/// had to be repaired on the way in.
///
/// It is deliberately not a tile. Anything that belongs to one widget is rendered inside that
/// widget; this is only for things that are true of the whole dashboard.
struct DashboardBanner: View {

    let icon: String
    let title: String
    let message: String?
    let tint: Color
    let actionTitle: String?
    let action: (() -> Void)?
    let onDismiss: (() -> Void)?

    init(icon: String,
         title: String,
         message: String? = nil,
         tint: Color,
         actionTitle: String? = nil,
         action: (() -> Void)? = nil,
         onDismiss: (() -> Void)? = nil) {
        self.icon = icon
        self.title = title
        self.message = message
        self.tint = tint
        self.actionTitle = actionTitle
        self.action = action
        self.onDismiss = onDismiss
    }

    var body: some View {
        HStack(alignment: .top, spacing: Spacing.sm) {
            Image(systemName: icon)
                .foregroundStyle(tint)
                .accessibilityHidden(true)
            text
            Spacer(minLength: Spacing.xs)
            controls
        }
        .padding(Spacing.md)
        .background(
            RoundedRectangle(cornerRadius: Radius.control, style: .continuous)
                .fill(tint.opacity(0.12))
        )
        .overlay(
            RoundedRectangle(cornerRadius: Radius.control, style: .continuous)
                .strokeBorder(tint.opacity(0.30), lineWidth: 1)
        )
        .accessibilityElement(children: .contain)
        .accessibilityLabel(message.map { "\(title). \($0)" } ?? title)
    }

    private var text: some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            Text(title)
                .hardwoodText(.widgetTitle)
                .fixedSize(horizontal: false, vertical: true)
            if let message = message, !message.isEmpty {
                Text(message)
                    .hardwoodText(.caption)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    @ViewBuilder private var controls: some View {
        VStack(alignment: .trailing, spacing: Spacing.xs) {
            if let actionTitle = actionTitle, let action = action {
                Button(actionTitle, action: action)
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .tint(tint)
            }
            if let onDismiss = onDismiss {
                Button(action: onDismiss) {
                    Image(systemName: "xmark")
                        .imageScale(.small)
                        .frame(width: 24, height: 24)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .foregroundStyle(Palette.textTertiary)
                .accessibilityLabel("Dismiss")
            }
        }
    }
}

// MARK: - Screen

/// The dashboard: the screen Hardwood is really about.
///
/// It reads its dependencies out of `AppEnvironment` and hands them to `DashboardScreenContent`,
/// which observes each one individually — `AppEnvironment` publishes nothing when a widget
/// resolves or a layout is edited, because that state belongs to `DashboardService` and
/// `DashboardStore`.
public struct DashboardScreen: View {

    @EnvironmentObject private var environment: AppEnvironment

    public init() { }

    public var body: some View {
        DashboardScreenContent(environment: environment,
                               store: environment.store,
                               service: environment.dashboard,
                               sync: environment.sync,
                               catalog: environment.catalog)
    }
}

/// What the dashboard screen actually is.
struct DashboardScreenContent: View {

    /// The sheets the dashboard can put up. One `@State` rather than four booleans, so two sheets
    /// can never race each other.
    private enum DashboardSheet: Identifiable {
        case catalog
        case presets
        case layouts
        case configure(DashboardWidget)

        var id: String {
            switch self {
            case .catalog:                return "catalog"
            case .presets:                return "presets"
            case .layouts:                return "layouts"
            case .configure(let widget):  return "configure." + widget.id
            }
        }
    }

    @ObservedObject private var environment: AppEnvironment
    @ObservedObject private var store: DashboardStore
    @ObservedObject private var service: DashboardService
    @ObservedObject private var sync: SyncService
    @ObservedObject private var catalog: Catalog

    init(environment: AppEnvironment,
         store: DashboardStore,
         service: DashboardService,
         sync: SyncService,
         catalog: Catalog) {
        _environment = ObservedObject(wrappedValue: environment)
        _store = ObservedObject(wrappedValue: store)
        _service = ObservedObject(wrappedValue: service)
        _sync = ObservedObject(wrappedValue: sync)
        _catalog = ObservedObject(wrappedValue: catalog)
    }

    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @State private var sheet: DashboardSheet?
    /// A widget just added from the catalog, whose settings open as soon as the catalog closes.
    @State private var pendingConfiguration: DashboardWidget?

    // MARK: Derived

    private var layout: DashboardLayout? { store.selectedLayout }

    private var accent: AccentName { layout?.accent ?? .orange }

    private var editAnimation: Animation? {
        reduceMotion ? nil : .snappy(duration: 0.24)
    }

    /// `"Updated 4 minutes ago · data through Jan 2"`.
    private var freshnessText: String {
        var parts = [Formatting.updatedString(service.lastUpdated)]
        if let through = environment.dataThroughText {
            parts.append(through)
        }
        return parts.joined(separator: " · ")
    }

    /// The second line: what this dashboard is, and whether the numbers are the real ones.
    private var contextText: String? {
        var parts: [String] = []
        if environment.isDemoMode {
            parts.append("Demo data")
        }
        if let tagline = layout?.tagline, !tagline.isEmpty {
            parts.append(tagline)
        } else if let count = layout?.widgets.count {
            parts.append(count == 1 ? "1 widget" : "\(count) widgets")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// True while anything on screen came from the cache rather than from this refresh.
    private var isStale: Bool {
        if service.lastError != nil { return true }
        return service.results.values.contains { $0.isStale }
    }

    private var finalizedDetail: String? {
        let games = Array(sync.newlyFinalizedGames.prefix(3))
        guard !games.isEmpty else { return nil }
        let lines = games.map { game -> String in
            let score = game.scoreLine.map { " \($0)" } ?? ""
            return "\(game.away.abbr) at \(game.home.abbr)\(score)"
        }
        var text = lines.joined(separator: " · ")
        let remainder = sync.newlyFinalizedGames.count - lines.count
        if remainder > 0 {
            text += " · \(remainder) more"
        }
        return text
    }

    // MARK: Body

    var body: some View {
        NavigationStack {
            ZStack {
                Palette.background.ignoresSafeArea()
                mainContent
            }
            .navigationTitle(layout?.name ?? "Dashboard")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { toolbarContent }
            .safeAreaInset(edge: .bottom, spacing: 0) { editingBar }
            .sheet(item: $sheet, onDismiss: presentPendingConfiguration) { presented in
                sheetContent(presented)
            }
        }
        .tint(accent.color)
        .widgetRetryAction { widgetID in
            Task { await service.retry(widgetID: widgetID) }
        }
        .widgetFavorites(playerID: environment.favoritePlayerID, teamID: environment.favoriteTeamID)
        .task(id: store.selectedLayoutID) {
            await environment.refreshDashboard(force: false)
        }
        .onChange(of: scenePhase) { _, phase in
            guard phase == .active else { return }
            Task { await environment.checkForUpdates(force: false) }
        }
    }

    // MARK: Content

    @ViewBuilder private var mainContent: some View {
        if let layout = layout, !layout.widgets.isEmpty {
            grid(for: layout)
        } else {
            emptyState
        }
    }

    private var emptyState: some View {
        ScrollView {
            VStack(spacing: Spacing.md) {
                banners
                EmptyDashboardView(addWidget: { openCatalog() },
                                   browsePresets: { sheet = .presets })
                    .frame(minHeight: 420)
            }
            .padding(.horizontal, Spacing.md)
            .padding(.vertical, Spacing.md)
        }
        .refreshable {
            await environment.refreshEverything()
        }
    }

    private func grid(for layout: DashboardLayout) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.md) {
                header
                banners
                DashboardGrid(layout: layout,
                              results: service.results,
                              isEditing: store.isEditing,
                              catalog: catalog,
                              store: store,
                              onConfigure: { widget in sheet = .configure(widget) })
            }
            .padding(.horizontal, Spacing.md)
            .padding(.top, Spacing.sm)
            .padding(.bottom, Spacing.xxl)
        }
        .refreshable {
            await environment.refreshEverything()
        }
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                Text(freshnessText)
                    .hardwoodText(.widgetTitle, color: Palette.textSecondary)
                    .lineLimit(2)
                if let contextText = contextText {
                    Text(contextText)
                        .hardwoodText(.caption)
                        .lineLimit(1)
                }
            }
            Spacer(minLength: Spacing.sm)
            freshnessIndicator
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private var freshnessIndicator: some View {
        if service.isRefreshing {
            ProgressView()
                .controlSize(.small)
                .accessibilityLabel("Refreshing")
        } else {
            StalenessDot(isStale: isStale, updatedAt: service.lastUpdated, showsText: false)
        }
    }

    // MARK: Banners

    @ViewBuilder private var banners: some View {
        VStack(spacing: Spacing.sm) {
            finalizedBanner
            refreshErrorBanner
            migrationBanner
            storeErrorBanner
        }
    }

    @ViewBuilder private var finalizedBanner: some View {
        if let summary = sync.finalizedSummary {
            DashboardBanner(icon: "sportscourt.fill",
                            title: summary,
                            message: finalizedDetail,
                            tint: accent.color,
                            actionTitle: "Refresh",
                            action: {
                                sync.acknowledgeFinalizedGames()
                                Task { await environment.refreshDashboard(force: true) }
                            },
                            onDismiss: { sync.acknowledgeFinalizedGames() })
        }
    }

    @ViewBuilder private var refreshErrorBanner: some View {
        if let error = service.lastError {
            DashboardBanner(icon: "wifi.exclamationmark",
                            title: error.title,
                            message: error.userMessage,
                            tint: Palette.warning,
                            actionTitle: "Try again",
                            action: { Task { await environment.refreshEverything() } })
        }
    }

    @ViewBuilder private var migrationBanner: some View {
        if !store.migrationNotes.isEmpty {
            DashboardBanner(icon: "wrench.and.screwdriver",
                            title: "Your dashboards were updated",
                            message: store.migrationNotes.joined(separator: " "),
                            tint: Palette.selection,
                            onDismiss: { store.acknowledgeMigrationNotes() })
        }
    }

    @ViewBuilder private var storeErrorBanner: some View {
        if let message = store.lastErrorMessage {
            DashboardBanner(icon: "exclamationmark.triangle",
                            title: "Saving went wrong",
                            message: message,
                            tint: Palette.negative,
                            onDismiss: { store.clearError() })
        }
    }

    // MARK: Toolbar

    @ToolbarContentBuilder private var toolbarContent: some ToolbarContent {
        ToolbarItem(placement: .principal) {
            layoutMenu
        }
        ToolbarItem(placement: .topBarTrailing) {
            editButton
        }
    }

    private var layoutMenu: some View {
        Menu {
            Picker("Dashboard", selection: layoutSelection) {
                ForEach(store.layouts) { option in
                    Label(option.name, systemImage: option.icon).tag(option.id)
                }
            }
            Divider()
            Button {
                newDashboard()
            } label: {
                Label("New Dashboard", systemImage: "plus")
            }
            Button {
                sheet = .presets
            } label: {
                Label("Browse Presets", systemImage: "rectangle.3.group")
            }
            Button {
                sheet = .layouts
            } label: {
                Label("Manage Dashboards", systemImage: "list.bullet.indent")
            }
        } label: {
            layoutMenuLabel
        }
        .accessibilityLabel("Dashboard: \(layout?.name ?? "none"). Switch dashboards.")
    }

    private var layoutMenuLabel: some View {
        HStack(spacing: Spacing.xs) {
            Image(systemName: layout?.icon ?? "square.grid.2x2")
                .imageScale(.small)
            Text(layout?.name ?? "Dashboard")
                .hardwoodText(.widgetTitle, color: Palette.textPrimary)
                .lineLimit(1)
            Image(systemName: "chevron.down")
                .imageScale(.small)
                .foregroundStyle(Palette.textTertiary)
        }
        .contentShape(Rectangle())
    }

    private var layoutSelection: Binding<String> {
        Binding(
            get: { store.selectedLayoutID ?? store.layouts.first?.id ?? "" },
            set: { newValue in store.select(newValue) }
        )
    }

    private var editButton: some View {
        Button(store.isEditing ? "Done" : "Edit") {
            withAnimation(editAnimation) {
                store.isEditing.toggle()
            }
        }
        .fontWeight(store.isEditing ? .semibold : .regular)
        .accessibilityHint(store.isEditing
                           ? "Finishes editing this dashboard."
                           : "Rearrange, resize and remove widgets.")
    }

    @ViewBuilder private var editingBar: some View {
        if store.isEditing {
            EditingToolbar(canUndo: store.canUndo,
                           widgetCount: layout?.widgets.count ?? 0,
                           accent: accent,
                           onAddWidget: { openCatalog() },
                           onUndo: { withAnimation(editAnimation) { store.undoLastEdit() } },
                           onDone: { withAnimation(editAnimation) { store.isEditing = false } })
                .transition(.move(edge: .bottom).combined(with: .opacity))
        }
    }

    // MARK: Sheets

    @ViewBuilder private func sheetContent(_ sheet: DashboardSheet) -> some View {
        switch sheet {
        case .catalog:
            WidgetCatalogSheet(catalog: catalog, accent: accent) { kind in
                addWidget(kind)
            }
        case .presets:
            PresetGallery(catalog: catalog, store: store) { _ in
                store.isEditing = false
            }
        case .layouts:
            LayoutSwitcher(store: store, catalog: catalog)
        case .configure(let widget):
            WidgetConfigSheet(widget: widget,
                              layoutID: store.selectedLayout?.id ?? "",
                              store: store,
                              catalog: catalog,
                              client: environment.client)
        }
    }

    // MARK: Actions

    private func openCatalog() {
        if !store.isEditing {
            withAnimation(editAnimation) { store.isEditing = true }
        }
        sheet = .catalog
    }

    private func newDashboard() {
        store.addBlankLayout(named: "New Dashboard")
        withAnimation(editAnimation) { store.isEditing = true }
    }

    /// Adds a widget of this kind and queues its settings sheet for when the catalog closes.
    private func addWidget(_ kind: WidgetKind) {
        guard let layoutID = store.selectedLayout?.id else { return }
        if !store.isEditing {
            store.isEditing = true
        }
        guard let widget = store.addWidget(kind: kind, to: layoutID) else { return }
        pendingConfiguration = widget
    }

    /// Two sheets cannot be presented in the same run loop turn, so the settings sheet for a newly
    /// added widget waits for the catalog's dismissal to finish.
    private func presentPendingConfiguration() {
        guard let widget = pendingConfiguration else { return }
        pendingConfiguration = nil
        Task {
            try? await Task.sleep(nanoseconds: 150_000_000)
            sheet = .configure(widget)
        }
    }
}

#if DEBUG
#Preview("Dashboard") {
    DashboardScreen()
        .environmentObject(AppEnvironment.demo())
}

#Preview("Banners") {
    ScrollView {
        VStack(spacing: Spacing.md) {
            DashboardBanner(icon: "sportscourt.fill",
                            title: "3 games finished since you last looked",
                            message: "BOS at LAL 112-118 · MIA at NYK 99-104",
                            tint: AccentName.orange.color,
                            actionTitle: "Refresh",
                            action: { },
                            onDismiss: { })
            DashboardBanner(icon: "wifi.exclamationmark",
                            title: "Offline",
                            message: "Hardwood could not reach your stats server. You are seeing the most recent numbers saved on this device.",
                            tint: Palette.warning,
                            actionTitle: "Try again",
                            action: { })
            DashboardBanner(icon: "wrench.and.screwdriver",
                            title: "Your dashboards were updated",
                            message: "One widget type this version does not know was removed from “Advanced Scout”.",
                            tint: Palette.selection,
                            onDismiss: { })
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}
#endif
