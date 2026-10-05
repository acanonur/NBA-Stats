#if os(macOS)
import SwiftUI

// The sidebar: a league switch at the top, then the user's dashboards, then the screens in five
// groups.
//
// WHY THE LEAGUE SWITCH IS HERE AS WELL AS IN THE TOOLBAR
// The two leagues name several screens differently ("Slate" and "Round", "Injuries & News" and
// "Injury Report & News"), and the rows below change when the league does. Putting the control
// directly above the rows it renames makes the cause and the effect one glance. Both controls bind
// to the same `model.league`, so they can never disagree.
//
// WHAT A ROW IS
// Every row is a `Label` tagged with a `MacSelection`; the `List(selection:)` writes the tag into
// `model.selection`, and `MacAppModel` does the rest (it selects the dashboard in the store, and
// remembers the choice). The EuroLeague has no player search, so that row is not drawn there.
//
// DASHBOARD ROWS
// A context menu offers Rename, Duplicate, Accent and Delete, each calling the one store method
// that does it. Delete always asks first, whether from the menu or from the Delete key. Rows can
// be dragged to reorder (`onMove`), which the store persists.
struct MacSidebar: View {

    @EnvironmentObject private var model: MacAppModel
    @EnvironmentObject private var store: DashboardStore

    @State private var isRenaming = false
    @State private var renamingID: String?
    @State private var renameText = ""
    @State private var isConfirmingDelete = false
    @State private var deletingID: String?

    init() { }

    var body: some View {
        List(selection: $model.selection) {
            dashboardsSection
            ForEach(MacSidebarSection.allCases, id: \.self) { section in
                screensSection(section)
            }
        }
        .listStyle(.sidebar)
        .navigationSplitViewColumnWidth(min: 200, ideal: 230, max: 300)
        .safeAreaInset(edge: .top, spacing: 0) {
            leaguePicker
        }
        .onDeleteCommand {
            askToDeleteSelectedDashboard()
        }
        .alert("Rename Dashboard", isPresented: $isRenaming) {
            TextField("Name", text: $renameText)
            Button("Rename") {
                commitRename()
            }
            Button("Cancel", role: .cancel) { }
        }
        .confirmationDialog("Delete this dashboard?",
                            isPresented: $isConfirmingDelete,
                            titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                commitDelete()
            }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text("The dashboard and its widgets are removed. This cannot be undone.")
        }
    }

    // MARK: League switch

    private var leaguePicker: some View {
        Picker("League", selection: $model.league) {
            ForEach(LeagueKey.allCases) { option in
                Text(option.displayName).tag(option)
            }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .padding(.horizontal, Spacing.md)
        .padding(.vertical, Spacing.sm)
    }

    // MARK: Dashboards

    private var dashboardsSection: some View {
        Section("Dashboards") {
            ForEach(store.layouts) { layout in
                dashboardRow(layout)
            }
            .onMove { source, destination in
                store.moveLayouts(fromOffsets: source, toOffset: destination)
            }
            Button {
                model.newDashboard()
            } label: {
                Label("New Dashboard", systemImage: "plus")
            }
            .buttonStyle(.plain)
            .foregroundStyle(Palette.textSecondary)
        }
    }

    private func dashboardRow(_ layout: DashboardLayout) -> some View {
        Label(layout.name, systemImage: layout.icon)
            .contextMenu {
                Button("Rename…") {
                    beginRename(layout)
                }
                Button("Duplicate") {
                    store.duplicate(layout)
                }
                Menu("Accent") {
                    ForEach(AccentName.allCases, id: \.self) { accent in
                        Button(accent.displayName) {
                            store.setAccent(accent, for: layout.id)
                        }
                    }
                }
                Divider()
                Button("Delete…", role: .destructive) {
                    deletingID = layout.id
                    isConfirmingDelete = true
                }
            }
            .tag(MacSelection.dashboard(layout.id))
    }

    // MARK: Screens

    @ViewBuilder private func screensSection(_ section: MacSidebarSection) -> some View {
        let screens: [MacScreen] = section.screens.filter { $0.isAvailable(in: model.league) }
        Section(section.title) {
            ForEach(screens, id: \.self) { screen in
                Label(screen.title(for: model.league), systemImage: screen.icon)
                    .tag(MacSelection.screen(screen))
            }
        }
    }

    // MARK: Actions

    private func beginRename(_ layout: DashboardLayout) {
        renamingID = layout.id
        renameText = layout.name
        isRenaming = true
    }

    private func commitRename() {
        if let id = renamingID {
            store.rename(id, to: renameText)
        }
        renamingID = nil
    }

    private func askToDeleteSelectedDashboard() {
        guard let current = model.selection else { return }
        switch current {
        case .dashboard(let id):
            deletingID = id
            isConfirmingDelete = true
        case .screen:
            break
        }
    }

    private func commitDelete() {
        if let id = deletingID {
            store.delete(id)
        }
        deletingID = nil
    }
}
#endif
