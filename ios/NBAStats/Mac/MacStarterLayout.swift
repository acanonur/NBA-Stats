#if os(macOS)
import Foundation
import SwiftUI

// The Mac app's own first dashboard, "Matchups & Defence": the four league widgets, pointed at both
// leagues, so the first thing the Mac shows is the answer to what the reader asked for.
//
// WHAT IT IS, AND WHY IT IS BUILT IN CODE
// The dashboard presets (`contracts/presets.json`) are the backend's, and a count of twelve is
// pinned by tests on both sides of the wire. The Mac needs a dashboard the iOS app does not, so it
// makes one locally on its first launch and leaves the presets alone. It is an ordinary layout once
// made: the reader can rename it, rearrange it, reconfigure every tile and delete it.
//
// WHAT IT HOLDS
// Seven tiles, with the two availability tiles side by side at the end of the NBA's half and the
// EuroLeague's half following:
//   NBA          matchup (the favourite team's next game), defence by position (every team),
//                availability report
//   EuroLeague   availability report (with headlines), projected round, defence by position (every
//                club), matchup (the favourite club's next game)
// The NBA matchup follows the favourite team the reader has set (the catalog's `$favorite_team`
// default), and says so on the tile when none is set. The EuroLeague has no such token, so its
// matchup tile asks for a club until one is chosen; the tile is still seeded, and it offers
// Configure rather than an error nobody can act on.
//
// WHEN IT RUNS
// Once. `hardwood.mac.starterSeeded` is set the first time the layout is made, so deleting the
// dashboard is respected and it never comes back. The layout's id is kept in
// `hardwood.mac.starterLayoutID` so the favourite club can be written into the EuroLeague matchup
// tile later. That is all `applyFavorites` does, and only into an EMPTY club: it never overwrites a
// club the reader chose on the tile. `seedIfNeeded` calls it on every launch after the first, so a
// favourite chosen on Start Here takes effect at the latest the next time the app opens, and at
// once when Start Here calls `applyFavorites` itself.
//
// WHEN IT DOES NOT RUN
// While the widget catalog lacks any of the four kinds (a server whose catalog predates them
// replaced the bundled one), because a tile of a kind with no catalog entry has no settings to edit.
// The flag stays unset, so the next launch tries again.

@MainActor
enum MacStarterLayout {

    /// UserDefaults keys. All carry the `hardwood.` prefix (MAC_DESIGN 6.3).
    enum DefaultsKey {
        static let seeded = "hardwood.mac.starterSeeded"
        static let layoutID = "hardwood.mac.starterLayoutID"
    }

    static let layoutName = "Matchups & Defence"

    private static let layoutIcon = "shield.lefthalf.filled"

    // MARK: Seeding

    /// Makes the starter dashboard the first time it is called with a catalog that knows the four
    /// league widgets; afterwards only keeps the EuroLeague matchup's club in step with the
    /// favourite (see the file comment).
    static func seedIfNeeded(store: DashboardStore,
                             catalog: Catalog,
                             clubCode: String?,
                             defaults: UserDefaults = UserDefaults.standard) {
        if defaults.bool(forKey: DefaultsKey.seeded) {
            applyFavorites(store: store, catalog: catalog, clubCode: clubCode, defaults: defaults)
            return
        }
        guard catalogKnowsTheLeagueWidgets(catalog) else {
            return
        }

        let layout = store.addBlankLayout(named: layoutName)
        store.setIcon(layoutIcon, for: layout.id)
        for widget in starterWidgets(catalog: catalog, clubCode: clubCode) {
            store.addWidget(widget, to: layout.id)
        }
        defaults.set(layout.id, forKey: DefaultsKey.layoutID)
        defaults.set(true, forKey: DefaultsKey.seeded)
        store.select(layout.id)
    }

    /// Writes the favourite club into the starter's EuroLeague matchup tile when that tile has no
    /// club yet. A tile the reader pointed at a club is never touched, and neither is a dashboard
    /// that was deleted or never made.
    static func applyFavorites(store: DashboardStore,
                               catalog: Catalog,
                               clubCode: String?,
                               defaults: UserDefaults = UserDefaults.standard) {
        guard let code = clubCode, !code.isEmpty else {
            return
        }
        guard let layoutID = defaults.string(forKey: DefaultsKey.layoutID),
              let layout = store.layout(id: layoutID) else {
            return
        }
        for widget in layout.widgets where widget.kind == .teamMatchup {
            if widget.configString("league") != "euroleague" {
                continue
            }
            if let current = widget.configString("club"), !current.isEmpty {
                continue
            }
            var config: [String: JSONValue] = widget.config
            config["club"] = JSONValue.string(code)
            store.updateWidgetConfig(widget.id, config: config, title: widget.title, in: layout.id)
        }
    }

    // MARK: The tiles

    private static func catalogKnowsTheLeagueWidgets(_ catalog: Catalog) -> Bool {
        catalog.widget(.teamMatchup) != nil
            && catalog.widget(.defenseByPosition) != nil
            && catalog.widget(.availabilityReport) != nil
            && catalog.widget(.slateProjections) != nil
    }

    /// The seven tiles, in reading order.
    private static func starterWidgets(catalog: Catalog, clubCode: String?) -> [DashboardWidget] {
        var club: JSONValue = JSONValue.null
        if let code = clubCode, !code.isEmpty {
            club = JSONValue.string(code)
        }
        var widgets: [DashboardWidget] = []
        widgets.append(tile(.teamMatchup, .large, league: "nba", catalog: catalog))
        widgets.append(tile(.defenseByPosition, .large, league: "nba", catalog: catalog))
        widgets.append(tile(.availabilityReport, .medium, league: "nba", catalog: catalog))
        widgets.append(tile(.availabilityReport,
                            .medium,
                            league: "euroleague",
                            extra: ["includeNews": JSONValue.bool(true)],
                            catalog: catalog))
        widgets.append(tile(.slateProjections,
                            .large,
                            league: "euroleague",
                            extra: ["round": JSONValue.int(0)],
                            catalog: catalog))
        widgets.append(tile(.defenseByPosition,
                            .large,
                            league: "euroleague",
                            extra: ["scheme": JSONValue.string("gfc")],
                            catalog: catalog))
        widgets.append(tile(.teamMatchup,
                            .large,
                            league: "euroleague",
                            extra: ["club": club],
                            catalog: catalog))
        return widgets
    }

    /// One tile: its kind and size, its league, any other settings, and the catalog's defaults for
    /// everything else.
    private static func tile(_ kind: WidgetKind,
                             _ size: WidgetSize,
                             league: String,
                             extra: [String: JSONValue] = [:],
                             catalog: Catalog) -> DashboardWidget {
        var values: [String: JSONValue] = extra
        values["league"] = JSONValue.string(league)
        let config = catalog.normalizedConfig(for: kind, config: values)
        return DashboardWidget(kind: kind, size: size, config: config)
    }
}
#endif
