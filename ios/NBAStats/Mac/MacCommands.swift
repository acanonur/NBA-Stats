#if os(macOS)
import SwiftUI

// The Mac menus and their shortcuts.
//
// WHAT IS HERE AND WHAT THE SYSTEM GIVES FOR FREE
// `SidebarCommands` (View > Show/Hide Sidebar) and `InspectorCommands` (View > Show/Hide Inspector)
// are the system's own. File gets "New Dashboard" in the place of "New Window" (the main window is
// a single-instance scene), plus the two writes, which first go to the Injuries screen. League has
// the two leagues and round/day stepping. Go has one item per screen, built from
// `MacScreen.allCases`, so a screen added to the enum appears here with its shortcut. Dashboard has
// edit mode and the one-level revert. Help gains the two server helpers.
//
// WHY COMMANDS OBSERVE BOTH OBJECTS
// A menu item's title and enabled state are read from the model and the store, so this struct
// observes both (`@ObservedObject`, set in an explicit init as the rulebook requires). Every action
// also guards itself, so an item that is momentarily stale can never do harm.
//
// WHY "REVERT LAST CHANGE" AND NOT UNDO
// Command-Z stays the system text undo. The dashboard store keeps exactly one snapshot, which is a
// revert, not an undo stack, so the menu calls it that.
struct MacCommands: Commands {

    @ObservedObject private var model: MacAppModel
    @ObservedObject private var store: DashboardStore

    init(model: MacAppModel, store: DashboardStore) {
        _model = ObservedObject(wrappedValue: model)
        _store = ObservedObject(wrappedValue: store)
    }

    var body: some Commands {
        SidebarCommands()
        InspectorCommands()
        fileCommands
        refreshCommands
        leagueMenu
        goMenu
        dashboardMenu
        helpCommands
    }

    // MARK: File

    private var fileCommands: some Commands {
        CommandGroup(replacing: .newItem) {
            Button("New Dashboard") {
                model.newDashboard()
            }
            .keyboardShortcut("n", modifiers: .command)
            Divider()
            Button("Record Availability Status…") {
                model.requestAction(.recordStatus)
            }
            .keyboardShortcut("r", modifiers: [.command, .shift])
            Button("Paste Headline Link…") {
                model.requestAction(.pasteLink)
            }
            .keyboardShortcut("l", modifiers: [.command, .shift])
        }
    }

    // MARK: View

    private var refreshCommands: some Commands {
        CommandGroup(after: .toolbar) {
            Button("Refresh") {
                model.refreshNow()
            }
            .keyboardShortcut("r", modifiers: .command)
        }
    }

    // MARK: League

    private var previousTitle: String {
        model.league == .nba ? "Previous Day" : "Previous Round"
    }

    private var nextTitle: String {
        model.league == .nba ? "Next Day" : "Next Round"
    }

    private var leagueMenu: some Commands {
        CommandMenu("League") {
            Button("NBA") {
                model.league = .nba
            }
            .keyboardShortcut("1", modifiers: [.command, .option])
            Button("EuroLeague") {
                model.league = .euroleague
            }
            .keyboardShortcut("2", modifiers: [.command, .option])
            Divider()
            Button(previousTitle) {
                model.requestStep(-1)
            }
            .keyboardShortcut("[", modifiers: .command)
            Button(nextTitle) {
                model.requestStep(1)
            }
            .keyboardShortcut("]", modifiers: .command)
        }
    }

    // MARK: Go

    private var goMenu: some Commands {
        CommandMenu("Go") {
            Button("Dashboard") {
                model.showDashboard()
            }
            .keyboardShortcut("0", modifiers: .command)
            Divider()
            ForEach(MacScreen.allCases, id: \.self) { screen in
                if screen.isAvailable(in: model.league) {
                    Button(screen.title(for: model.league)) {
                        model.show(screen)
                    }
                    .keyboardShortcut(screen.shortcut)
                }
            }
        }
    }

    // MARK: Dashboard

    private var dashboardMenu: some Commands {
        CommandMenu("Dashboard") {
            Button(store.isEditing ? "Done Editing" : "Edit Dashboard") {
                toggleEditing()
            }
            .keyboardShortcut("e", modifiers: [.command, .shift])
            Button("Revert Last Change") {
                store.undoLastEdit()
            }
            .keyboardShortcut("z", modifiers: [.command, .option])
            .disabled(!store.canUndo)
        }
    }

    /// Editing only makes sense on a dashboard, so the command goes there first.
    private func toggleEditing() {
        model.showDashboard()
        store.isEditing.toggle()
    }

    // MARK: Help

    private var helpCommands: some Commands {
        CommandGroup(after: .help) {
            Button("Open Server Logs") {
                MacHardwoodEnv.openLogsFolder()
            }
            Button("Copy Server Restart Command") {
                MacHardwoodEnv.copyRestartCommand()
            }
        }
    }
}
#endif
