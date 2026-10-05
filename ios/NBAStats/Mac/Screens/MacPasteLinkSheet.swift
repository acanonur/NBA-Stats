#if os(macOS)
import SwiftUI

// The sheet that saves a headline link a person found: its title, its address, when it was
// published, which outlet published it, and which teams it is about.
//
// WHAT IS SAVED, AND WHAT IS NOT
// A headline is a title, a link, a date and the outlet's name, exactly like one the server reads
// from a feed, and it shows up in the Headlines tab beside them. Hardwood does not open the link
// or read the article; the person typed the title. The server refuses a link that is not a web
// address, and one that points to a betting operator's site, and the sheet shows its sentence.
//
// TEAMS
// A headline may name any number of teams (for the EuroLeague, clubs). They are optional and added
// from the league's own team list, so an id is never typed by hand.
//
// A write needs the server's API key, the same as recording a status; a refusal is explained by
// `MacWriteMessage`, shared with the Record Status sheet.

/// The one field of the server's answer the sheet reads: false when the link was already saved.
private struct MacLinkWriteResult: Decodable {
    var created: Bool?
}

struct MacPasteLinkSheet: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel
    @Environment(\.dismiss) private var dismiss

    private let league: LeagueKey

    @State private var title = ""
    @State private var link = ""
    @State private var sourceName = ""
    @State private var publishedDate = Date()
    @State private var teamKeys: [String] = []
    @State private var isSaving = false
    @State private var errorText = ""
    @State private var isDone = false

    init(league: LeagueKey) {
        self.league = league
    }

    // MARK: Body

    var body: some View {
        VStack(spacing: 0) {
            if isDone {
                doneView
            } else {
                form
            }
            Divider()
            buttonBar
        }
        .hardwoodSheetFrame(minWidth: 520, minHeight: 460)
    }

    // MARK: Form

    private var form: some View {
        Form {
            Section {
                TextField("Title", text: $title)
                TextField("Link", text: $link)
                TextField("Source name", text: $sourceName)
                DatePicker("Published", selection: $publishedDate, in: ...Date(), displayedComponents: .date)
            }
            Section("Teams (optional)") {
                teamFields
            }
            Section {
                Text(footerText)
                    .hardwoodText(.caption)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .formStyle(.grouped)
    }

    private var footerText: String {
        if environment.isDemoMode {
            return "Demo data is read-only. Turn off demo data in Settings to save to your server."
        }
        return "Saved on your server as a headline: title, link, date and source name only. "
            + "Hardwood does not open the link or read the article."
    }

    // MARK: Teams

    private var options: [MacTeamOption] {
        model.teamOptions(for: league)
    }

    private var addableTeams: [MacTeamOption] {
        options.filter { !teamKeys.contains($0.id) }
    }

    private func teamLabel(_ key: String) -> String {
        options.first(where: { $0.id == key })?.label ?? key
    }

    @ViewBuilder private var teamFields: some View {
        ForEach(teamKeys, id: \.self) { key in
            HStack(spacing: Spacing.sm) {
                Text(teamLabel(key))
                    .hardwoodText(.tableCell, monospacedDigits: false)
                Spacer(minLength: 0)
                Button {
                    teamKeys.removeAll(where: { $0 == key })
                } label: {
                    Image(systemName: "minus.circle")
                }
                .buttonStyle(.borderless)
                .help("Remove this team")
            }
        }
        Menu("Add a team") {
            ForEach(addableTeams) { option in
                Button(option.label) {
                    teamKeys.append(option.id)
                }
            }
        }
    }

    // MARK: Done and buttons

    private var doneView: some View {
        VStack(alignment: .leading, spacing: Spacing.md) {
            Label("Already saved", systemImage: "checkmark.circle")
                .hardwoodText(.sectionTitle)
            Text("That link was already on your server, so nothing was added.")
                .hardwoodText(.tableCell, monospacedDigits: false)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(Spacing.lg)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private var buttonBar: some View {
        HStack(spacing: Spacing.sm) {
            if isSaving {
                ProgressView()
                    .controlSize(.small)
            }
            errorLabel
            Spacer(minLength: 0)
            buttons
        }
        .padding(Spacing.md)
    }

    @ViewBuilder private var errorLabel: some View {
        if !errorText.isEmpty {
            Text(errorText)
                .hardwoodText(.caption, color: Palette.negative)
                .lineLimit(3)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: 360, alignment: .leading)
        }
    }

    @ViewBuilder private var buttons: some View {
        if isDone {
            Button("Done") {
                dismiss()
            }
            .keyboardShortcut(.defaultAction)
        } else {
            Button("Cancel") {
                dismiss()
            }
            .keyboardShortcut(.cancelAction)
            Button("Save") {
                save()
            }
            .keyboardShortcut(.defaultAction)
            .disabled(!canSave)
        }
    }

    // MARK: Saving

    private func trimmed(_ text: String) -> String {
        text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var canSave: Bool {
        if isSaving || environment.isDemoMode {
            return false
        }
        if trimmed(title).isEmpty || trimmed(sourceName).isEmpty {
            return false
        }
        return LeagueWebLink.url(link) != nil
    }

    private func save() {
        Task {
            await performSave()
        }
    }

    private func performSave() async {
        isSaving = true
        errorText = ""
        do {
            let reply: Data = try await environment.league.send(method: "POST",
                                                                route: LeagueRoutes.pasteLink(league),
                                                                json: linkBody())
            model.noteSuccessfulWrite(in: league)
            finish(with: reply)
        } catch {
            errorText = MacWriteMessage.text(for: APIError.from(error))
        }
        isSaving = false
    }

    private func linkBody() -> LeagueLinkWrite {
        var body = LeagueLinkWrite()
        body.title = trimmed(title)
        body.link = trimmed(link)
        body.publishedAt = MacWriteDate.text(for: publishedDate)
        body.sourceName = trimmed(sourceName)
        body.teamIds = teamKeys
        return body
    }

    /// Closes the sheet, unless the link was already there.
    private func finish(with reply: Data) {
        let result: MacLinkWriteResult? = try? JSONDecoder().decode(MacLinkWriteResult.self, from: reply)
        if result?.created == false {
            isDone = true
        } else {
            dismiss()
        }
    }
}
#endif
