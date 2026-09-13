import Foundation
import SwiftUI

/// One player: who they are, what every season of theirs looks like, and the shape of the whole
/// career — plus the one action that makes the dashboard feel alive, "put this on my board".
///
/// The season table is where the era rules earn their keep. A number the league never recorded is
/// an em dash, never a zero; a pre-1997 advanced number carries the dashed "estimated" underline;
/// and every cell can explain itself.
public struct PlayerDetailScreen: View {

    @EnvironmentObject private var environment: AppEnvironment

    private let playerID: PlayerID
    private let seed: PlayerRef?

    public init(player: PlayerRef) {
        self.playerID = player.playerId
        self.seed = player
    }

    public init(playerID: PlayerID) {
        self.playerID = playerID
        self.seed = nil
    }

    public var body: some View {
        PlayerDetailScreenContent(playerID: playerID,
                                  seed: seed,
                                  environment: environment,
                                  store: environment.store,
                                  catalog: environment.catalog,
                                  client: environment.client)
    }
}

struct PlayerDetailScreenContent: View {

    /// The columns the season table prefers, in this order, when the data carries them.
    static let preferredColumnOrder = [
        "pts", "reb", "ast", "ts_pct", "usg_pct", "per", "net_rtg", "efg_pct", "min"
    ]

    /// The metric the career arc opens on, first match wins.
    static let preferredArcMetrics = ["per", "pts", "ts_pct", "win_shares", "net_rtg"]

    /// How many metric columns fit before the table is more spreadsheet than summary.
    static let maximumColumns = 4

    private let playerID: PlayerID
    private let seed: PlayerRef?

    @ObservedObject private var environment: AppEnvironment
    @ObservedObject private var store: DashboardStore
    private let catalog: Catalog
    private let client: any APIClientProtocol

    init(playerID: PlayerID,
         seed: PlayerRef?,
         environment: AppEnvironment,
         store: DashboardStore,
         catalog: Catalog,
         client: any APIClientProtocol) {
        self.playerID = playerID
        self.seed = seed
        _environment = ObservedObject(wrappedValue: environment)
        _store = ObservedObject(wrappedValue: store)
        self.catalog = catalog
        self.client = client
    }

    @State private var detail: PlayerDetailResponse?
    @State private var errorMessage: String?
    @State private var isLoading = false
    @State private var arcMetricKey: String?
    @State private var confirmation: String?

    // MARK: Derived

    private var player: PlayerRef? { detail?.player ?? seed }

    private var displayName: String { player?.name ?? "Player" }

    private var accent: AccentName { store.selectedLayout?.accent ?? .orange }

    /// Regular-season rows, oldest first, which is how a career reads.
    private var seasonRows: [SeasonRow] {
        guard let detail = detail else { return [] }
        return detail.seasons
            .filter { ($0.seasonType ?? "Regular Season") == "Regular Season" }
            .sorted { $0.season < $1.season }
    }

    /// Every metric key the season rows actually carry, in the table's preferred order.
    private var columns: [MetricDescriptor] {
        var keys: [String] = []
        for row in seasonRows {
            for key in row.values.keys where !keys.contains(key) {
                keys.append(key)
            }
        }
        let preferred = PlayerDetailScreenContent.preferredColumnOrder
        let ordered = preferred.filter { keys.contains($0) } + keys.filter { !preferred.contains($0) }.sorted()
        let descriptors = ordered.compactMap { catalog.metric($0) }
        return Array(descriptors.prefix(PlayerDetailScreenContent.maximumColumns))
    }

    /// The metrics the career arc can be drawn for: player metrics this player has numbers for.
    private var arcMetrics: [MetricDescriptor] {
        var keys: Set<String> = []
        for row in seasonRows {
            for key in row.values.keys { keys.insert(key) }
        }
        return catalog.metrics(inScope: "player").filter { keys.contains($0.key) }
    }

    private var arcMetric: MetricDescriptor? {
        if let key = arcMetricKey, let descriptor = catalog.metric(key) {
            return descriptor
        }
        let available = arcMetrics
        for key in PlayerDetailScreenContent.preferredArcMetrics {
            if let match = available.first(where: { $0.key == key }) { return match }
        }
        return available.first
    }

    private var isFavorite: Bool {
        guard let player = player else { return false }
        return environment.isFavorite(player: player)
    }

    // MARK: Body

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.lg) {
                confirmationBanner
                content
            }
            .padding(.horizontal, Spacing.md)
            .padding(.vertical, Spacing.md)
        }
        .hardwoodBackground()
        .navigationTitle(displayName)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { toolbarContent }
        .task(id: playerID) {
            await load()
        }
        .refreshable {
            await load()
        }
    }

    @ViewBuilder private var content: some View {
        if let detail = detail {
            loaded(detail)
        } else if let errorMessage = errorMessage {
            ErrorTile(message: errorMessage, isRetryable: true, size: .medium) {
                Task { await load() }
            }
        } else {
            LoadingTile(size: .medium)
        }
    }

    private func loaded(_ detail: PlayerDetailResponse) -> some View {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            hero(detail)
            careerArcCard
            seasonsCard(detail)
            bioCard(detail)
            attributionNote
        }
    }

    // MARK: Hero

    private func hero(_ detail: PlayerDetailResponse) -> some View {
        VStack(alignment: .leading, spacing: Spacing.md) {
            HStack(alignment: .top, spacing: Spacing.md) {
                if let abbr = detail.player.teamAbbr, !abbr.isEmpty {
                    TeamBadge(abbreviation: abbr, size: .large)
                }
                VStack(alignment: .leading, spacing: Spacing.xxs) {
                    Text(detail.player.name)
                        .hardwoodText(.sectionTitle)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(heroSubtitle(detail))
                        .hardwoodText(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
            addToDashboardMenu
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .hardwoodCard(padding: Spacing.lg)
    }

    private func heroSubtitle(_ detail: PlayerDetailResponse) -> String {
        var parts: [String] = []
        if let position = detail.player.position, !position.isEmpty { parts.append(position) }
        if let jersey = detail.player.jersey, !jersey.isEmpty { parts.append("#\(jersey)") }
        if let bio = detail.bio {
            if let height = bio.height, !height.isEmpty { parts.append(height) }
            if let weight = bio.weight { parts.append("\(weight) lb") }
            if let from = bio.fromYear {
                let to = bio.toYear.map { String($0) } ?? "present"
                parts.append("\(from)–\(to)")
            }
        }
        if parts.isEmpty { parts.append("No biography on file") }
        return parts.joined(separator: " · ")
    }

    // MARK: Add to dashboard

    private var addToDashboardMenu: some View {
        Menu {
            Button {
                add(kind: .playerSnapshot, extra: ["playerId": .int(playerID)], title: displayName)
            } label: {
                Label("Player snapshot", systemImage: "person.crop.square")
            }
            Button {
                add(kind: .careerArc,
                    extra: careerArcConfig(),
                    title: "\(displayName) career")
            } label: {
                Label("Career arc", systemImage: "waveform.path.ecg")
            }
            Button {
                add(kind: .gameLog, extra: ["playerId": .int(playerID)], title: "\(displayName) game log")
            } label: {
                Label("Game log", systemImage: "tablecells")
            }
            Button {
                add(kind: .statTile, extra: statTileConfig(), title: displayName)
            } label: {
                Label("Stat tile", systemImage: "number.square")
            }
            Button {
                add(kind: .trendChart, extra: trendConfig(), title: "\(displayName) trend")
            } label: {
                Label("Trend", systemImage: "chart.xyaxis.line")
            }
            Button {
                add(kind: .shotProfile,
                    extra: ["subjectType": .string("player"), "subjectId": .int(playerID)],
                    title: "\(displayName) shots")
            } label: {
                Label("Shot profile", systemImage: "scope")
            }
        } label: {
            Label("Add to \(store.selectedLayout?.name ?? "dashboard")", systemImage: "plus.rectangle.on.rectangle")
                .font(Typography.widgetTitle)
                .frame(maxWidth: .infinity)
                .padding(.vertical, Spacing.sm)
                .background(
                    RoundedRectangle(cornerRadius: Radius.control, style: .continuous)
                        .fill(accent.softTint)
                )
                .foregroundStyle(accent.color)
        }
        .accessibilityLabel("Add a widget about \(displayName) to your dashboard")
    }

    private func careerArcConfig() -> [String: JSONValue] {
        var config: [String: JSONValue] = ["playerId": .int(playerID)]
        if let metric = arcMetric {
            config["metric"] = .string(metric.key)
        }
        return config
    }

    private func statTileConfig() -> [String: JSONValue] {
        var config: [String: JSONValue] = [
            "subjectType": .string("player"),
            "subjectId": .int(playerID)
        ]
        if let metric = arcMetric {
            config["metric"] = .string(metric.key)
        }
        return config
    }

    private func trendConfig() -> [String: JSONValue] {
        var config: [String: JSONValue] = [
            "subjectType": .string("player"),
            "subjectIds": .array([.int(playerID)])
        ]
        if let metric = arcMetric {
            config["metric"] = .string(metric.key)
        }
        return config
    }

    /// Appends a preconfigured widget to whichever dashboard is on screen.
    private func add(kind: WidgetKind, extra: [String: JSONValue], title: String) {
        guard let layoutID = store.selectedLayout?.id else {
            confirmation = "There is no dashboard to add this to yet."
            return
        }
        var config = catalog.defaultConfig(for: kind)
        for (key, value) in extra {
            config[key] = value
        }
        let widget = DashboardWidget(kind: kind,
                                     title: title,
                                     size: catalog.widget(kind)?.defaultSize ?? .medium,
                                     config: config)
        store.addWidget(widget, to: layoutID)
        let layoutName = store.layout(id: layoutID)?.name ?? "your dashboard"
        confirmation = "Added “\(title)” to \(layoutName)."
    }

    @ViewBuilder private var confirmationBanner: some View {
        if let confirmation = confirmation {
            DashboardBanner(icon: "checkmark.circle.fill",
                            title: confirmation,
                            message: "Open the Dashboard tab to arrange it.",
                            tint: Palette.positive,
                            onDismiss: { self.confirmation = nil })
        }
    }

    // MARK: Career arc

    @ViewBuilder private var careerArcCard: some View {
        if let metric = arcMetric, let payload = careerArcPayload(metric: metric) {
            VStack(alignment: .leading, spacing: Spacing.sm) {
                HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
                    Text("Career arc")
                        .hardwoodText(.sectionTitle)
                    Spacer(minLength: Spacing.sm)
                    metricMenu(current: metric)
                }
                CareerArcWidget(payload: payload, size: .large)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .hardwoodCard(padding: Spacing.md)
        }
    }

    private func metricMenu(current: MetricDescriptor) -> some View {
        Menu {
            ForEach(arcMetrics) { descriptor in
                Button {
                    arcMetricKey = descriptor.key
                } label: {
                    if descriptor.key == current.key {
                        Label(descriptor.name, systemImage: "checkmark")
                    } else {
                        Text(descriptor.name)
                    }
                }
            }
        } label: {
            HStack(spacing: Spacing.xs) {
                Text(current.shortName)
                    .hardwoodText(.widgetTitle, color: accent.color)
                Image(systemName: "chevron.up.chevron.down")
                    .imageScale(.small)
                    .foregroundStyle(accent.color)
            }
        }
        .accessibilityLabel("Career arc metric: \(current.name)")
    }

    /// Builds the same payload the `career_arc` widget renders on the dashboard, from the season
    /// table this screen already has, so the two always agree.
    private func careerArcPayload(metric: MetricDescriptor) -> CareerArcPayload? {
        guard let player = player else { return nil }
        let seasons = seasonRows.map { row -> CareerSeason in
            let value = row.value(metric.key)
            return CareerSeason(season: row.season,
                                seasonType: row.seasonType,
                                age: row.age,
                                teamAbbr: row.teamAbbr,
                                gp: row.gp,
                                value: value,
                                displayValue: displayValue(value, metric: metric),
                                availability: availability(for: metric, row: row, raw: value))
        }
        guard seasons.contains(where: { $0.value != nil }) else { return nil }

        return CareerArcPayload(player: player,
                                metric: metric,
                                xAxis: "season",
                                seasons: seasons,
                                playoffSeasons: playoffSeasons(metric: metric),
                                eraBoundaries: boundaries(within: seasons),
                                peak: peak(of: seasons))
    }

    private func playoffSeasons(metric: MetricDescriptor) -> [CareerSeason] {
        guard let detail = detail else { return [] }
        return detail.seasons
            .filter { $0.seasonType == "Playoffs" }
            .sorted { $0.season < $1.season }
            .map { row -> CareerSeason in
                let value = row.value(metric.key)
                return CareerSeason(season: row.season,
                                    seasonType: row.seasonType,
                                    age: row.age,
                                    teamAbbr: row.teamAbbr,
                                    gp: row.gp,
                                    value: value,
                                    displayValue: displayValue(value, metric: metric),
                                    availability: availability(for: metric, row: row, raw: value))
            }
    }

    /// The boundaries the player's own career actually crosses; the rest would be noise.
    private func boundaries(within seasons: [CareerSeason]) -> [EraBoundary] {
        guard let first = seasons.first?.season, let last = seasons.last?.season else { return [] }
        return catalog.eraBoundaries.filter { $0.season > first && $0.season <= last }
    }

    private func peak(of seasons: [CareerSeason]) -> CareerPeak? {
        var best: CareerSeason?
        for season in seasons {
            guard let value = season.value, value.isFinite else { continue }
            guard let incumbent = best?.value else {
                best = season
                continue
            }
            let isBetter = arcMetricHigherIsBetter ? (value > incumbent) : (value < incumbent)
            if isBetter { best = season }
        }
        guard let best = best, let value = best.value else { return nil }
        return CareerPeak(season: best.season, value: value, displayValue: best.displayValue)
    }

    private var arcMetricHigherIsBetter: Bool {
        arcMetric?.higherIsBetter ?? true
    }

    // MARK: Seasons table

    private func seasonsCard(_ detail: PlayerDetailResponse) -> some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            SectionHeader(title: "Seasons",
                          subtitle: seasonsSubtitle(detail))
            if seasonRows.isEmpty {
                Text("No season history is loaded for \(displayName) yet.")
                    .hardwoodText(.tableCell, color: Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                seasonsTable(detail)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .hardwoodCard(padding: Spacing.md)
    }

    private func seasonsSubtitle(_ detail: PlayerDetailResponse) -> String {
        let count = seasonRows.count
        let seasons = count == 1 ? "1 regular season" : "\(count) regular seasons"
        guard seasonRows.contains(where: { $0.availability != .full }) else { return seasons }
        return "\(seasons) · some numbers are estimated for their era"
    }

    private func seasonsTable(_ detail: PlayerDetailResponse) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            Grid(alignment: .trailing, horizontalSpacing: Spacing.md, verticalSpacing: Spacing.sm) {
                headerRow
                ForEach(seasonRows) { row in
                    dataRow(row, isCareer: false)
                }
                if let career = detail.careerTotals {
                    Divider()
                        .gridCellUnsizedAxes(.horizontal)
                    dataRow(career, isCareer: true)
                }
            }
            .padding(.vertical, Spacing.xxs)
        }
    }

    private var headerRow: some View {
        GridRow {
            Text("Season")
                .hardwoodText(.tableHeader)
                .gridColumnAlignment(.leading)
            Text("Team")
                .hardwoodText(.tableHeader)
                .gridColumnAlignment(.leading)
            Text("GP")
                .hardwoodText(.tableHeader)
            ForEach(columns) { metric in
                Text(metric.shortName)
                    .hardwoodText(.tableHeader)
            }
        }
    }

    private func dataRow(_ row: SeasonRow, isCareer: Bool) -> some View {
        GridRow {
            Text(isCareer ? "Career" : row.season)
                .hardwoodText(.tableCell, color: isCareer ? Palette.textPrimary : Palette.textSecondary)
                .fontWeight(isCareer ? .semibold : .regular)
            Text(row.teamAbbr ?? Formatting.emDash)
                .hardwoodText(.tableCell, color: Palette.textSecondary)
            Text(Formatting.integer(row.gp))
                .hardwoodText(.tableCell, color: Palette.textSecondary)
            ForEach(columns) { metric in
                cell(row: row, metric: metric)
            }
        }
    }

    /// One number, with the era treatment its availability calls for. A value the league never
    /// recorded renders as an em dash — never as a zero.
    private func cell(row: SeasonRow, metric: MetricDescriptor) -> some View {
        let raw = row.value(metric.key)
        let treatment = availability(for: metric, row: row, raw: raw)
        let color = treatment == .unavailable ? Palette.textTertiary : Palette.textPrimary
        return Text(displayValue(raw, metric: metric))
            .foregroundStyle(color)
            .availabilityStyled(treatment)
            .font(Typography.tableCellMono)
            .accessibilityLabel(cellAccessibilityLabel(row: row, metric: metric, raw: raw, treatment: treatment))
    }

    private func cellAccessibilityLabel(row: SeasonRow,
                                        metric: MetricDescriptor,
                                        raw: Double?,
                                        treatment: MetricAvailability) -> String {
        let season = row.season
        guard raw != nil else {
            return "\(metric.name), \(season): not recorded in that era"
        }
        let value = displayValue(raw, metric: metric)
        guard treatment != .full else { return "\(metric.name), \(season): \(value)" }
        return "\(metric.name), \(season): \(value), \(treatment.hardwoodShortLabel)"
    }

    private func displayValue(_ raw: Double?, metric: MetricDescriptor) -> String {
        guard let raw = raw else { return Formatting.emDash }
        return Formatting.value(raw, format: metric.format)
    }

    /// The honest availability of one cell: the row's own verdict, tightened by what the catalog
    /// knows about when the league started recording this metric.
    private func availability(for metric: MetricDescriptor,
                              row: SeasonRow,
                              raw: Double?) -> MetricAvailability {
        guard raw != nil else { return .unavailable }
        if row.availability == .unavailable { return .unavailable }
        let era = catalog.availability(for: metric.key, season: row.season, perGame: true)
        if era == .unavailable { return .unavailable }
        if era == .estimated || row.availability == .estimated { return .estimated }
        if row.availability == .partial { return .partial }
        return .full
    }

    // MARK: Biography

    private func bioCard(_ detail: PlayerDetailResponse) -> some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            SectionHeader(title: "Biography")
            if let bio = detail.bio {
                VStack(alignment: .leading, spacing: Spacing.xs) {
                    ForEach(bioFacts(bio), id: \.label) { fact in
                        bioRow(fact)
                    }
                }
            } else {
                Text("No biography is loaded for \(displayName).")
                    .hardwoodText(.tableCell, color: Palette.textSecondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .hardwoodCard(padding: Spacing.md)
    }

    private struct BioFact {
        let label: String
        let value: String
    }

    private func bioFacts(_ bio: PlayerBio) -> [BioFact] {
        var facts: [BioFact] = []
        if let height = bio.height, !height.isEmpty {
            facts.append(BioFact(label: "Height", value: height))
        }
        if let weight = bio.weight {
            facts.append(BioFact(label: "Weight", value: "\(weight) lb"))
        }
        if let birthdate = bio.birthdate, !birthdate.isEmpty {
            facts.append(BioFact(label: "Born", value: Formatting.mediumGameDate(birthdate)))
        }
        if let country = bio.country, !country.isEmpty {
            facts.append(BioFact(label: "Country", value: country))
        }
        if let draft = bio.draft {
            facts.append(BioFact(label: "Draft", value: draft.displayText))
        }
        if let school = bio.school, !school.isEmpty {
            facts.append(BioFact(label: "From", value: school))
        }
        return facts
    }

    private func bioRow(_ fact: BioFact) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.md) {
            Text(fact.label)
                .hardwoodText(.statLabel)
                .frame(width: 78, alignment: .leading)
            Text(fact.value)
                .hardwoodText(.tableCell)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
    }

    private var attributionNote: some View {
        Text(environment.attribution)
            .hardwoodText(.caption)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Toolbar

    @ToolbarContentBuilder private var toolbarContent: some ToolbarContent {
        ToolbarItem(placement: .topBarTrailing) {
            Button {
                guard let player = player else { return }
                environment.toggleFavorite(player: player)
            } label: {
                Image(systemName: isFavorite ? "star.fill" : "star")
                    .foregroundStyle(isFavorite ? Palette.warning : Palette.textSecondary)
            }
            .disabled(player == nil)
            .accessibilityLabel(isFavorite
                                ? "Remove \(displayName) as your favourite player"
                                : "Make \(displayName) your favourite player")
        }
    }

    // MARK: Loading

    private func load() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            let response = try await client.player(playerID)
            detail = response
            errorMessage = nil
        } catch {
            guard !APIError.isCancellation(error) else { return }
            // A player that is already on screen stays there: a failed refresh must not blank it.
            if detail == nil {
                errorMessage = APIError.from(error).userMessage
            }
        }
    }
}

#if DEBUG
/// The player `Resources/Fixtures/player_detail.json` carries, so the preview shows a real season
/// table rather than the "not in demo data" tile. If the fixture is regenerated with a different
/// subject the preview degrades to that tile — it never fails to build.
private let previewDemoPlayerID: PlayerID = 1_500_819

#Preview("Player detail") {
    NavigationStack {
        PlayerDetailScreen(playerID: previewDemoPlayerID)
            .environmentObject(AppEnvironment.demo())
    }
}

#Preview("Player detail, not in the demo data") {
    NavigationStack {
        PlayerDetailScreen(player: .preview)
            .environmentObject(AppEnvironment.demo())
    }
}
#endif
