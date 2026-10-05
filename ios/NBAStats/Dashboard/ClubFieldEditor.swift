import Foundation
import SwiftUI

// The configuration sheet's control for a `club` field (a EuroLeague club code such as `PAN`), and
// the one rule that decides which of a league widget's fields the sheet shows.
//
// WHY A FIELD TYPE OF ITS OWN
// A NBA team is an integer id; a EuroLeague club is a three-letter code. The widget catalog
// (`contracts/widgets.json`) therefore has a `club` field type beside `team`, and a league widget
// (`team_matchup`, `defense_by_position`, `availability_report`, `slate_projections`) carries both,
// plus a `league` field that says which of the two the tile reads.
//
// WHERE THE CLUBS COME FROM
// This file is shared by iOS and macOS and must not know where the club list comes from. The Mac
// root view reads it from `ElMeta.clubs` and puts it in the environment (`leagueClubs`), so the
// sheet opens with a picker of real clubs. Nothing on iOS writes that list: it stays empty, and the
// same editor falls back to a text box that upper-cases what is typed. A club code is never
// invented here; the picker offers only what the server listed.

// MARK: - Which fields to show

/// Hides the fields of the league a league widget is not pointed at.
///
/// A tile whose `league` is `nba` has no use for a club, a round or the five-position scheme, and
/// one whose `league` is `euroleague` has no use for an NBA team, an opponent team or a date. Asking
/// for them anyway invites a setting the tile will silently ignore. A hidden field keeps whatever it
/// holds: switching the league back and forth loses nothing.
///
/// The rule reads only the draft's own `league` value, so a widget that has no such value (every
/// kind built before the league widgets) is untouched, whatever its fields are called.
enum LeagueConfigVisibility {

    private static let hiddenForNba: Set<String> = ["club", "opponentClub", "round", "scheme"]
    private static let hiddenForEuroleague: Set<String> = ["team", "opponent", "date"]

    /// True when the sheet should draw `field` for a draft in this state.
    static func isVisible(_ field: WidgetSpec.ConfigField, config: [String: JSONValue]) -> Bool {
        guard let league = config["league"]?.stringValue else {
            return true
        }
        switch league {
        case "nba":
            return !hiddenForNba.contains(field.key)
        case "euroleague":
            return !hiddenForEuroleague.contains(field.key)
        default:
            return true
        }
    }
}

// MARK: - The editor

/// A club code: a picker over the clubs the server listed, or a text box when there are none.
///
/// An empty choice is stored as an explicit `null`, which the server treats as "not chosen" (the
/// matchup tile then asks the reader to choose; the others show every club). A stored code that is
/// not in the list (a club that has left the league, or a list that has not loaded) is still offered
/// as a choice, so opening the sheet never rewrites the configuration behind the reader's back.
struct ClubFieldEditor: View {
    private let field: WidgetSpec.ConfigField
    @Binding private var value: JSONValue?

    @Environment(\.leagueClubs) private var clubs

    init(field: WidgetSpec.ConfigField, value: Binding<JSONValue?>) {
        self.field = field
        _value = value
    }

    /// The stored code, trimmed and upper-cased; empty when there is none.
    private var code: String {
        guard let text = value?.stringValue else { return "" }
        return text.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
    }

    /// The code as a `String` for a control, with the empty string standing for "none".
    private var codeBinding: Binding<String> {
        Binding<String>(
            get: { code },
            set: { newValue in
                let cleaned = newValue.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
                if cleaned.isEmpty {
                    value = JSONValue.null
                } else {
                    value = JSONValue.string(cleaned)
                }
            }
        )
    }

    /// The clubs to offer: the server's list, plus the stored code when it is not on it.
    private var options: [LeaguePickerClub] {
        var list: [LeaguePickerClub] = clubs
        let current = code
        if !current.isEmpty && !list.contains(where: { $0.code == current }) {
            list.insert(LeaguePickerClub(code: current, name: current), at: 0)
        }
        return list
    }

    var body: some View {
        if clubs.isEmpty {
            textEntry
        } else {
            picker
        }
    }

    private var picker: some View {
        Picker(field.label, selection: codeBinding) {
            Text("No club").tag("")
            ForEach(options) { club in
                Text(optionTitle(club)).tag(club.code)
            }
        }
        .pickerStyle(.menu)
    }

    private func optionTitle(_ club: LeaguePickerClub) -> String {
        club.name == club.code ? club.code : club.name + " (" + club.code + ")"
    }

    private var textEntry: some View {
        TextField(field.label, text: codeBinding, prompt: Text("Three letters, such as PAN"))
            .hardwoodNeverCapitalize()
    }
}

#if DEBUG
#Preview("Club field") {
    let field = WidgetSpec.ConfigField(key: "club", type: .club, label: "Club")
    return Form {
        ClubFieldEditor(field: field, value: .constant(JSONValue.string("ZZA")))
            .environment(\.leagueClubs, [
                LeaguePickerClub(code: "ZZA", name: "Alderwick Herons"),
                LeaguePickerClub(code: "ZZB", name: "Brindlemoor Stags")
            ])
        ClubFieldEditor(field: field, value: .constant(nil))
    }
    .formStyle(.grouped)
    .frame(width: 420, height: 180)
}
#endif
