#if os(macOS)
import SwiftUI

// The sheet that records an availability status a person found: which player, what status, who said
// it and when. It is the only way a status enters the app, because the app reads no article.
//
// WHY THE TWO LEAGUES SHOW DIFFERENT FIELDS
// The EuroLeague keeps a full provenance record for a hand-entered status: club, player, status,
// reason category, reason text, expected return, source link, source NAME (required) and source date
// (required). The NBA's write takes less: a player id, a status, an optional team, a note, and
// optionally a source link and date. Its server refuses any other field outright (a 400 naming it),
// so the NBA form shows only what it can send and never a label it would have to throw away.
//
// WHAT HAPPENS ON SAVE
// The sheet posts to the server, which appends a row. A status is never edited: to undo one, the
// person retracts it from the inspector, and the server appends a retraction (the NBA's clears the
// override and keeps the row). A write needs the server's API key; the app reads it from the
// installer's settings file when it starts. If the server refuses (401 or 403) the sheet says so in
// the words the design gives, and points at Settings. A server that holds only the invented demo
// league refuses every write, and the sheet shows the server's own sentence.
//
// WHAT THE SERVER MAY ANSWER BESIDES "SAVED"
// A EuroLeague status entered with a typed name that matches nobody on the squad is saved but
// waits under Needs review; a link to a betting operator is dropped and its label kept. The sheet
// tells the person either, in plain sentences, instead of closing as if all had gone well.
//
// THIS FILE ALSO HOLDS THE SMALL PIECES THE PASTE LINK SHEET SHARES
// The prefill value, the message for a refused write, the date text a write carries, and the
// player list entry.

// MARK: - Prefill

/// What a sheet opens with. Every field is optional; a screen fills in what it knows (the club of a
/// headline, the name of an unmatched row) and the person does the rest.
struct MacStatusPrefill: Hashable {
    /// A EuroLeague club code, or an NBA team id as text.
    var teamKey: String?
    var playerName: String?
    var sourceUrl: String?
    var sourceLabel: String?
    var sourcePublishedAt: String?
}

// MARK: - Shared write helpers

enum MacWriteMessage {

    /// The sentence for a refused or failed write. 401 and 403 both mean the server wants its key.
    static func text(for failure: APIError) -> String {
        if let status = failure.httpStatus, status == 401 || status == 403 {
            return "Your server needs its API key for changes. Open Settings ▸ Leagues ▸ Read API key from hardwood.env."
        }
        return failure.userMessage
    }
}

enum MacWriteDate {

    /// The date text a write carries for a day the person chose. Today is sent as the present
    /// instant, because a bare date is read as midnight UTC and a reader east of Greenwich who picks
    /// "today" would otherwise be sending a time that has not happened yet. Any earlier day is
    /// sent as `yyyy-MM-dd`.
    static func text(for day: Date, now: Date = Date(), calendar: Calendar = Calendar.current) -> String {
        if calendar.isDate(day, inSameDayAs: now) {
            return ISO8601DateFormatter().string(from: now)
        }
        return LeagueFormatting.isoDay(day, calendar: calendar)
    }
}

/// One player in the sheet's list: a EuroLeague squad member (identified by person code) or an NBA
/// roster player (identified by player id), as the text the write needs.
struct MacPlayerOption: Identifiable, Hashable {
    let id: String
    let label: String

    /// The list entry that means "someone not on the list: I will type the name".
    static let typedTag = "*typed"

    /// `Rurik Jessop · G`.
    static func describe(name: String, position: String?) -> String {
        if let place = position, !place.isEmpty {
            return name + " · " + place
        }
        return name
    }

    /// A club's squad by person code. Alphabetical, to find a name; the order says nothing else.
    static func squad(from club: ElClubView) -> [MacPlayerOption] {
        var options: [MacPlayerOption] = []
        for member in club.squad ?? [] {
            guard let player = member.player, let code = player.personCode, !code.isEmpty else { continue }
            let name: String = player.name ?? code
            options.append(MacPlayerOption(id: code, label: describe(name: name, position: player.position)))
        }
        return options.sorted { $0.label < $1.label }
    }

    /// An NBA team's roster by player id.
    static func roster(from detail: TeamDetailResponse) -> [MacPlayerOption] {
        var options: [MacPlayerOption] = []
        for entry in detail.roster {
            let player = entry.player
            options.append(MacPlayerOption(id: String(player.playerId),
                                           label: describe(name: player.name, position: player.position)))
        }
        return options.sorted { $0.label < $1.label }
    }
}

/// The reason categories the server accepts, in the order the menu lists them.
enum MacRecordReasons {
    static let keys: [String] = [
        "injury", "illness", "rest", "coachDecision", "personal",
        "suspension", "gLeague", "notWithTeam", "notRegistered", "other"
    ]
}

/// The few fields of the server's answer the sheet reads.
private struct MacStatusWriteResult: Decodable {
    var matched: Bool?
    var linkWithheld: Bool?
}

// MARK: - The sheet

struct MacRecordStatusSheet: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel
    @Environment(\.dismiss) private var dismiss

    private let league: LeagueKey
    private let prefill: MacStatusPrefill

    @State private var teamChoice: String?
    @State private var playerChoice = ""
    @State private var typedName = ""
    @State private var status = ""
    @State private var reasonCategory = ""
    @State private var reasonText = ""
    @State private var expectedReturn = ""
    @State private var note = ""
    @State private var sourceUrl = ""
    @State private var sourceLabel = ""
    @State private var sourceDate = Date()
    @State private var squad: [MacPlayerOption] = []
    @State private var squadMessage = ""
    @State private var isSaving = false
    @State private var errorText = ""
    @State private var outcomeLines: [String] = []
    @State private var isDone = false

    init(league: LeagueKey, prefill: MacStatusPrefill) {
        self.league = league
        self.prefill = prefill
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
        .hardwoodSheetFrame(minWidth: 520, minHeight: 560)
        .onAppear {
            applyPrefill()
        }
        .task(id: teamChoice) {
            await loadSquad()
        }
    }

    // MARK: Form

    private var form: some View {
        Form {
            Section {
                teamPicker
                playerFields
                statusPicker
            }
            Section {
                reasonOrNote
            }
            Section {
                sourceFields
            }
            Section {
                Text(footerText)
                    .hardwoodText(.caption)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .formStyle(.grouped)
    }

    private var teamPicker: some View {
        MacTeamPicker(league: league,
                      title: league == .euroleague ? "Club" : "Team",
                      selection: $teamChoice,
                      noneLabel: league == .euroleague ? "Choose a club" : "Choose a team")
    }

    @ViewBuilder private var playerFields: some View {
        if squad.isEmpty {
            emptySquadFields
        } else {
            Picker("Player", selection: $playerChoice) {
                Text("Choose a player").tag("")
                ForEach(squad) { option in
                    Text(option.label).tag(option.id)
                }
                if league == .euroleague {
                    Text("Someone else (type a name)").tag(MacPlayerOption.typedTag)
                }
            }
            if playerChoice == MacPlayerOption.typedTag {
                TextField("Player name", text: $typedName)
            }
        }
        reportedAs
    }

    @ViewBuilder private var emptySquadFields: some View {
        if league == .euroleague {
            TextField("Player name", text: $typedName)
        } else {
            Text(nbaPlayerHint)
                .hardwoodText(.caption)
        }
        if !squadMessage.isEmpty {
            Text(squadMessage)
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// What stands where the NBA's player list will be.
    private var nbaPlayerHint: String {
        if teamChoice == nil {
            return "Choose a team to list its players."
        }
        if environment.isDemoMode {
            return "Players are not listed in demo mode."
        }
        return "Loading the team's players…"
    }

    /// For a row from Needs review: the name as the report spelled it.
    @ViewBuilder private var reportedAs: some View {
        if let reported = prefill.playerName, !reported.isEmpty {
            Text("Reported as " + reported + ". Choose the matching player.")
                .hardwoodText(.caption)
        }
    }

    private var statusChoices: [MacInjuryStatusChoice] {
        MacInjuryStatuses.choices.filter { $0.key != MacInjuryStatuses.noReportKey }
    }

    private var statusPicker: some View {
        Picker("Status", selection: $status) {
            Text("Choose a status").tag("")
            ForEach(statusChoices) { choice in
                Text(choice.label).tag(choice.key)
            }
        }
    }

    @ViewBuilder private var reasonOrNote: some View {
        if league == .euroleague {
            Picker("Reason", selection: $reasonCategory) {
                Text("Not stated").tag("")
                ForEach(MacRecordReasons.keys, id: \.self) { key in
                    Text(LeagueFormatting.reasonWord(key)).tag(key)
                }
            }
            TextField("Reason details", text: $reasonText)
            TextField("Expected return", text: $expectedReturn)
        } else {
            TextField("Note", text: $note)
        }
    }

    @ViewBuilder private var sourceFields: some View {
        TextField("Source link", text: $sourceUrl)
        if league == .euroleague {
            TextField("Source name (required)", text: $sourceLabel)
        }
        DatePicker("Source date", selection: $sourceDate, in: ...Date(), displayedComponents: .date)
    }

    private var footerText: String {
        if environment.isDemoMode {
            return "Demo data is read-only. Turn off demo data in Settings to save to your server."
        }
        switch league {
        case .euroleague:
            return "Saved on your server and used by projections. It is never edited; retract it to undo."
        case .nba:
            return "Saved on your server and used by projections. Clear it later to undo; the row stays in the history."
        }
    }

    // MARK: Done and buttons

    private var doneView: some View {
        VStack(alignment: .leading, spacing: Spacing.md) {
            Label("Saved", systemImage: "checkmark.circle")
                .hardwoodText(.sectionTitle)
            ForEach(Array(outcomeLines.enumerated()), id: \.offset) { pair in
                Text(pair.element)
                    .hardwoodText(.tableCell, monospacedDigits: false)
                    .fixedSize(horizontal: false, vertical: true)
            }
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

    // MARK: What can be saved

    private func trimmed(_ text: String) -> String {
        text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func nilIfEmpty(_ text: String) -> String? {
        let value = trimmed(text)
        return value.isEmpty ? nil : value
    }

    /// What identifies the player: a person code or player id from the list, or (EuroLeague only)
    /// a typed name. While a list is showing, nothing counts until a player is chosen from it (or
    /// "Someone else" is chosen and a name typed): a name left over from Needs review must not stand
    /// in for the choice the person has not made yet.
    private var playerReference: String {
        if playerChoice == MacPlayerOption.typedTag {
            return league == .euroleague ? trimmed(typedName) : ""
        }
        if playerChoice.isEmpty {
            if squad.isEmpty && league == .euroleague {
                return trimmed(typedName)
            }
            return ""
        }
        return playerChoice
    }

    private var canSave: Bool {
        if isSaving || environment.isDemoMode {
            return false
        }
        guard let team = teamChoice, !team.isEmpty else { return false }
        if status.isEmpty || playerReference.isEmpty {
            return false
        }
        if league == .euroleague && trimmed(sourceLabel).isEmpty {
            return false
        }
        let link = trimmed(sourceUrl)
        if !link.isEmpty && LeagueWebLink.url(link) == nil {
            return false
        }
        return true
    }

    // MARK: Prefill and the player list

    private func applyPrefill() {
        teamChoice = prefill.teamKey
        typedName = prefill.playerName ?? ""
        sourceUrl = prefill.sourceUrl ?? ""
        sourceLabel = prefill.sourceLabel ?? ""
        if let moment = Formatting.parseTimestamp(prefill.sourcePublishedAt) {
            sourceDate = moment
        }
    }

    private func loadSquad() async {
        squad = []
        squadMessage = ""
        guard let team = teamChoice, !team.isEmpty else { return }
        if environment.isDemoMode {
            return
        }
        do {
            switch league {
            case .euroleague:
                let club = try await environment.league.get(ElClubView.self, LeagueRoutes.elClub(team))
                if Task.isCancelled { return }
                squad = MacPlayerOption.squad(from: club)
            case .nba:
                guard let number = Int(team) else { return }
                let detail = try await environment.league.get(TeamDetailResponse.self,
                                                              LeagueRoutes.nbaTeam(number))
                if Task.isCancelled { return }
                squad = MacPlayerOption.roster(from: detail)
            }
            keepChoiceValid()
        } catch {
            if Task.isCancelled { return }
            squadMessage = "The player list could not be loaded: " + APIError.from(error).userMessage
        }
    }

    /// A choice made before the list loaded must still be on it, or it is cleared.
    private func keepChoiceValid() {
        if playerChoice.isEmpty || playerChoice == MacPlayerOption.typedTag {
            return
        }
        if !squad.contains(where: { $0.id == playerChoice }) {
            playerChoice = ""
        }
    }

    // MARK: Saving

    private func save() {
        Task {
            await performSave()
        }
    }

    private func performSave() async {
        isSaving = true
        errorText = ""
        do {
            let reply: Data
            switch league {
            case .euroleague:
                reply = try await environment.league.send(method: "POST",
                                                          route: LeagueRoutes.recordStatus(.euroleague),
                                                          json: elBody())
            case .nba:
                reply = try await environment.league.send(method: "POST",
                                                          route: LeagueRoutes.recordStatus(.nba),
                                                          json: nbaBody())
            }
            model.noteSuccessfulWrite(in: league)
            finish(with: reply)
        } catch {
            errorText = MacWriteMessage.text(for: APIError.from(error))
        }
        isSaving = false
    }

    private func elBody() -> ElStatusWrite {
        var body = ElStatusWrite()
        body.clubCode = teamChoice
        body.status = status
        body.sourceLabel = trimmed(sourceLabel)
        body.sourcePublishedAt = MacWriteDate.text(for: sourceDate)
        if playerChoice.isEmpty || playerChoice == MacPlayerOption.typedTag {
            body.playerName = nilIfEmpty(typedName)
        } else {
            body.personCode = playerChoice
        }
        body.reasonCategory = nilIfEmpty(reasonCategory)
        body.reasonText = nilIfEmpty(reasonText)
        body.expectedReturnText = nilIfEmpty(expectedReturn)
        body.sourceUrl = nilIfEmpty(sourceUrl)
        return body
    }

    private func nbaBody() -> NbaStatusWrite {
        var body = NbaStatusWrite()
        body.playerId = Int(playerChoice)
        body.status = status
        body.teamId = Int(teamChoice ?? "")
        body.note = nilIfEmpty(note)
        body.sourceUrl = nilIfEmpty(sourceUrl)
        body.sourcePublishedAt = MacWriteDate.text(for: sourceDate)
        return body
    }

    /// Closes the sheet, unless the server said something the person must read first.
    private func finish(with reply: Data) {
        let result: MacStatusWriteResult? = try? JSONDecoder().decode(MacStatusWriteResult.self, from: reply)
        var sentences: [String] = []
        if result?.matched == false {
            sentences.append("The name did not match anyone on the squad, so the entry is waiting in "
                         + "Needs review. Record it again and choose the player from the squad.")
        }
        if result?.linkWithheld == true {
            sentences.append("The link was not kept because it points to a betting operator's site. "
                         + "The rest of the entry was saved.")
        }
        if sentences.isEmpty {
            dismiss()
        } else {
            outcomeLines = sentences
            isDone = true
        }
    }
}
#endif
