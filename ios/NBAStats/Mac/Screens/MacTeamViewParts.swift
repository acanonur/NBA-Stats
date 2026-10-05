#if os(macOS)
import SwiftUI

// The pieces of Team View that are not the squad table: the header of a EuroLeague club and of an
// NBA team, and the inspector card of a squad member or a roster player.
//
// WHAT THE HEADER ANSWERS
// "How is this team doing, what did it score last, what does it average, what does it let opponents
// score, who is missing, and when does it play next?" Every part is built from the shared league
// views (`ScoringBlock`, `LatestScoreCard`, `DefenseBucketBars`, `AvailabilityEntryRow`,
// `MatchupGameLine`), so a team's header and the Matchup screen draw a number the same way. Every
// number is a field the server sent; a team with no games yet shows em dashes, not zeros.
//
// ESTIMATES ARE LABELLED
// A EuroLeague club's rating is built from a prior (last season's scoring, regressed) and the round
// results since. When the prior is the workbook author's estimate and not an official figure, the
// header says "Prior is an estimate" next to the rating.

// MARK: - A small labelled figure

struct MacHeaderFigure: View {

    private let title: String
    private let value: String

    init(title: String, value: String) {
        self.title = title
        self.value = value
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(value)
                .hardwoodText(.widgetTitle)
            Text(title)
                .hardwoodText(.caption)
        }
        .accessibilityElement(children: .combine)
    }
}

// MARK: - EuroLeague club header

struct MacClubHeader: View {

    private let club: ElClubView
    private let onOpenDefense: () -> Void

    init(club: ElClubView, onOpenDefense: @escaping () -> Void) {
        self.club = club
        self.onOpenDefense = onOpenDefense
    }

    /// The scoring block, with the club's own team on it (the server's `scoring` has none).
    private var scoring: LeagueTeamForm? {
        guard var form = club.scoring else { return nil }
        form.team = club.team
        return form
    }

    private var recordText: String {
        var parts: [String] = [LeagueFormatting.record(club.record)]
        if let games = club.scoring?.games {
            parts.append(LeagueFormatting.integer(games) + (games == 1 ? " game" : " games"))
        }
        return parts.joined(separator: " · ")
    }

    private var coachText: String {
        guard let coach = club.coach, !coach.isEmpty else { return "" }
        return "Coach " + coach
    }

    /// `Projected 85.7 for, 85.3 against · attack 1.014 · defence 1.009 · as of round 4`.
    private var ratingText: String {
        guard let rating = club.rating else { return "" }
        let pointsFor: String = LeagueFormatting.number(rating.projectedPointsFor)
        let pointsAgainst: String = LeagueFormatting.number(rating.projectedPointsAgainst)
        let attack: String = LeagueFormatting.number(rating.attackIndex, places: 3)
        let defence: String = LeagueFormatting.number(rating.defenceIndex, places: 3)
        let asOf: String = LeagueFormatting.integer(rating.asOfRound)
        let projected: String = "Projected " + pointsFor + " for, " + pointsAgainst + " against"
        let attackText: String = "attack " + attack
        let defenceText: String = "defence " + defence
        let roundText: String = "as of round " + asOf
        return [projected, attackText, defenceText, roundText].joined(separator: " · ")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            identity
            ratingBlock
            HStack(alignment: .top, spacing: Spacing.xl) {
                scoringColumn
                nextColumn
            }
            absences
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var identity: some View {
        HStack(spacing: Spacing.md) {
            TeamBadge(abbreviation: club.team?.displayAbbr ?? Formatting.emDash,
                      name: club.team?.name,
                      size: .large)
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                Text(club.team?.displayName ?? Formatting.emDash)
                    .hardwoodText(.sectionTitle)
                Text(recordText)
                    .hardwoodText(.tableCell, monospacedDigits: false)
                if !coachText.isEmpty {
                    Text(coachText)
                        .hardwoodText(.caption)
                }
            }
            Spacer(minLength: 0)
        }
    }

    @ViewBuilder private var ratingBlock: some View {
        if !ratingText.isEmpty {
            HStack(spacing: Spacing.sm) {
                Text(ratingText)
                    .hardwoodText(.caption)
                    .fixedSize(horizontal: false, vertical: true)
                if club.rating?.priorIsEstimate == true {
                    EstimatedBadge(text: "Prior is an estimate")
                }
                Spacer(minLength: 0)
            }
        }
    }

    @ViewBuilder private var scoringColumn: some View {
        if let form = scoring {
            VStack(alignment: .leading, spacing: Spacing.md) {
                ScoringBlock(team: form, leagueAverage: nil, density: .compact)
                LatestScoreCard(game: form.latestGame, density: .compact)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            Text("No scoring figures yet.")
                .hardwoodText(.caption)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var nextColumn: some View {
        VStack(alignment: .leading, spacing: Spacing.md) {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                Text("Next game")
                    .hardwoodText(.statLabel)
                MatchupGameLine(game: club.nextGame, league: .euroleague)
            }
            defence
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var defence: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            Text("Defence by position")
                .hardwoodText(.statLabel)
            if let summary = club.defenseSummary {
                WithheldBanner(withheld: summary.withheld)
                DefenseBucketBars(buckets: summary.buckets,
                                  withheld: summary.withheld,
                                  provisional: false,
                                  density: .compact)
            } else {
                Text(Formatting.emDash)
                    .hardwoodText(.caption)
            }
            Button("Open Defence") {
                onOpenDefense()
            }
        }
    }

    @ViewBuilder private var absences: some View {
        let list: [LeagueAbsence] = club.absences ?? []
        VStack(alignment: .leading, spacing: Spacing.xs) {
            Text("Absences")
                .hardwoodText(.statLabel)
            if list.isEmpty {
                Text("No absences reported.")
                    .hardwoodText(.caption)
            } else {
                ForEach(Array(list.enumerated()), id: \.offset) { pair in
                    AvailabilityEntryRow(absence: pair.element,
                                         density: .compact,
                                         teamAbbr: club.team?.displayAbbr)
                }
            }
        }
    }
}

// MARK: - NBA team header

struct MacNbaTeamHeader: View {

    private let detail: TeamDetailResponse
    private let form: LeagueTeamForm?
    private let leagueAverage: Double?
    private let game: LeagueGameRef?
    private let onOpenDefense: () -> Void

    init(detail: TeamDetailResponse,
         form: LeagueTeamForm?,
         leagueAverage: Double?,
         game: LeagueGameRef?,
         onOpenDefense: @escaping () -> Void) {
        self.detail = detail
        self.form = form
        self.leagueAverage = leagueAverage
        self.game = game
        self.onOpenDefense = onOpenDefense
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            identity
            figures
            HStack(alignment: .top, spacing: Spacing.xl) {
                scoringColumn
                nextColumn
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var identity: some View {
        HStack(spacing: Spacing.md) {
            TeamBadge(team: detail.team, size: .large)
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                Text(detail.team.name)
                    .hardwoodText(.sectionTitle)
                Text(LeagueFormatting.record(detail.record))
                    .hardwoodText(.tableCell, monospacedDigits: false)
            }
            Spacer(minLength: 0)
        }
    }

    private var figures: some View {
        HStack(spacing: Spacing.xl) {
            MacHeaderFigure(title: "Offensive rating", value: LeagueFormatting.number(detail.value("off_rtg")))
            MacHeaderFigure(title: "Defensive rating", value: LeagueFormatting.number(detail.value("def_rtg")))
            MacHeaderFigure(title: "Net rating", value: LeagueFormatting.signed(detail.value("net_rtg")))
            MacHeaderFigure(title: "Pace", value: LeagueFormatting.number(detail.value("pace")))
            Spacer(minLength: 0)
        }
    }

    @ViewBuilder private var scoringColumn: some View {
        if let team = form {
            VStack(alignment: .leading, spacing: Spacing.md) {
                ScoringBlock(team: team, leagueAverage: leagueAverage, density: .compact)
                LatestScoreCard(game: team.latestGame, density: .compact)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            Text("Scoring and the next game load from this team's scheduled matchup; none is available yet.")
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var nextColumn: some View {
        VStack(alignment: .leading, spacing: Spacing.md) {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                Text("Next game")
                    .hardwoodText(.statLabel)
                MatchupGameLine(game: game, league: .nba)
            }
            AvailabilitySummaryBlock(summary: form?.availability, teamAbbr: detail.team.abbr)
            defence
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder private var defence: some View {
        if let summary = form?.defenseSummary {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                Text("Defence by position")
                    .hardwoodText(.statLabel)
                WithheldBanner(withheld: summary.withheld)
                DefenseBucketBars(buckets: summary.buckets,
                                  withheld: summary.withheld,
                                  provisional: false,
                                  density: .compact)
                Button("Open Defence") {
                    onOpenDefense()
                }
            }
        }
    }
}

// MARK: - Squad member card

/// A EuroLeague squad member in the inspector: who, whose numbers the rates are, the status and where
/// it came from.
struct MacSquadMemberPanel: View {

    private let member: ElSquadMember
    private let clubName: String
    private let clubAbbr: String

    init(member: ElSquadMember, clubName: String, clubAbbr: String) {
        self.member = member
        self.clubName = clubName
        self.clubAbbr = clubAbbr
    }

    private var statusKey: String {
        MacWorkbookStatus.key(member.status)
    }

    private var statusLabel: String {
        LeagueFormatting.statusWord(member.status)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            MacPlayerHeader(name: member.player?.name ?? Formatting.emDash,
                            detail: MacPlayerDetailText.line(position: member.player?.positionRaw ?? member.player?.position ?? "",
                                                             jersey: member.player?.jersey ?? "",
                                                             team: clubName),
                            teamAbbr: clubAbbr,
                            teamName: clubName)
            statusBlock
            basisBlock
            facts
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var statusBlock: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            Text("Availability")
                .hardwoodText(.statLabel)
            HStack(spacing: Spacing.sm) {
                MacOptionalStatusCell(status: statusKey, label: statusLabel)
                if member.inForce == false {
                    LeagueChip(text: "Superseded", tint: Palette.neutral)
                }
                Spacer(minLength: 0)
            }
            if statusKey.isEmpty {
                Text("No source has reported a status for this player.")
                    .hardwoodText(.caption)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                DetailLine(label: "Chance of playing", value: LeagueFormatting.percent(member.chanceOfPlaying, places: 0))
                LeagueSourceLine(source: member.availabilitySource, density: .full, isStale: member.isStale == true)
            }
        }
    }

    private var basisBlock: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            Text("Whose numbers")
                .hardwoodText(.statLabel)
            MacBasisCell(basis: member.basis ?? "", label: LeagueFormatting.basisWord(member.basis))
            if let note = member.basisNote, !note.isEmpty {
                Text(note)
                    .hardwoodText(.caption)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var facts: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            DetailLine(label: "Role", value: member.role ?? Formatting.emDash)
            DetailLine(label: "Age", value: LeagueFormatting.number(member.age, places: 0))
            DetailLine(label: "Projected minutes", value: LeagueFormatting.number(member.projectedMinutes))
            DetailLine(label: "Points per 40", value: LeagueFormatting.number(member.per40?.pts))
            DetailLine(label: "Rebounds per 40", value: LeagueFormatting.number(member.per40?.reb))
            DetailLine(label: "Assists per 40", value: LeagueFormatting.number(member.per40?.ast))
            DetailLine(label: "Season games", value: LeagueFormatting.integer(member.seasonAverages?.games))
            DetailLine(label: "Season points", value: LeagueFormatting.number(member.seasonAverages?.pts))
        }
    }
}

// MARK: - Roster player card

/// An NBA roster player in the inspector: the line the table carries, the player's report entry when
/// there is one, and a button that opens the full player screen.
struct MacRosterMemberPanel: View {

    private let row: MacRosterRow
    private let entry: LeagueAvailabilityEntry?
    private let teamAbbr: String
    private let teamName: String
    private let onOpenPlayer: () -> Void

    init(row: MacRosterRow,
         entry: LeagueAvailabilityEntry?,
         teamAbbr: String,
         teamName: String,
         onOpenPlayer: @escaping () -> Void) {
        self.row = row
        self.entry = entry
        self.teamAbbr = teamAbbr
        self.teamName = teamName
        self.onOpenPlayer = onOpenPlayer
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            MacPlayerHeader(name: row.player,
                            detail: MacPlayerDetailText.line(position: row.position,
                                                             jersey: row.playerRef.jersey ?? "",
                                                             team: teamName),
                            teamAbbr: teamAbbr,
                            teamName: teamName)
            availability
            facts
            Button("Open player") {
                onOpenPlayer()
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder private var availability: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            Text("Availability")
                .hardwoodText(.statLabel)
            if let found = entry {
                AvailabilityEntryRow(entry: found, density: .full, teamAbbr: teamAbbr)
            } else {
                Text("No report names this player.")
                    .hardwoodText(.caption)
            }
        }
    }

    private var facts: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            Text("Season · per game")
                .hardwoodText(.statLabel)
            DetailLine(label: "Games", value: row.games.text)
            DetailLine(label: "Minutes", value: row.minutes.text)
            DetailLine(label: "Points", value: row.points.text)
            DetailLine(label: "Rebounds", value: row.rebounds.text)
            DetailLine(label: "Assists", value: row.assists.text)
            DetailLine(label: "True shooting %", value: row.trueShootingPct.text)
            DetailLine(label: "Usage %", value: row.usagePct.text)
            DetailLine(label: "Net rating", value: row.netRating.text)
        }
    }
}
#endif
