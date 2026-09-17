import Foundation

// MARK: - Shared payload pieces

/// The subject a widget is about: exactly one of `player` or `team` is filled in.
public struct SubjectRef: Codable, Hashable, Sendable {
    /// `"player"` or `"team"`.
    public let type: String
    public let player: PlayerRef?
    public let team: TeamRef?

    public var displayName: String {
        if let player = player { return player.name }
        if let team = team { return team.name }
        return type.capitalized
    }

    /// The short label a tile header uses: `"L. James"` or `"LAL"`.
    public var shortName: String {
        if let player = player { return player.shortName }
        if let team = team { return team.abbr }
        return displayName
    }

    public init(type: String, player: PlayerRef? = nil, team: TeamRef? = nil) {
        self.type = type
        self.player = player
        self.team = team
    }

    public init(player: PlayerRef) {
        self.init(type: "player", player: player, team: nil)
    }

    public init(team: TeamRef) {
        self.init(type: "team", player: nil, team: team)
    }

    public static let preview = SubjectRef(player: .preview)
    public static let previewTeam = SubjectRef(team: .preview)
}

/// One point of the sparkline a `stat_tile` can carry.
public struct SparklinePoint: Codable, Hashable, Sendable, Identifiable {
    /// The game's calendar date, `"2026-01-02"`.
    public let x: String
    /// `nil` where the game has no value for the metric; the line breaks rather than dropping to 0.
    public let y: Double?
    public let gameId: GameID?

    public var id: String { gameId ?? x }

    public init(x: String, y: Double?, gameId: GameID? = nil) {
        self.x = x
        self.y = y
        self.gameId = gameId
    }
}

// MARK: - stat_tile

/// `stat_tile` (`contracts/CONTRACT.md` §4).
public struct StatTilePayload: Codable, Hashable, Sendable {
    public let subject: SubjectRef
    /// `"2025-26 · Regular Season · Per Game"`.
    public let context: String?
    public let primary: MetricValue
    public let secondary: [MetricValue]
    public let sparkline: [SparklinePoint]
    public let sparklineMetric: String?

    public init(subject: SubjectRef,
                context: String? = nil,
                primary: MetricValue,
                secondary: [MetricValue] = [],
                sparkline: [SparklinePoint] = [],
                sparklineMetric: String? = nil) {
        self.subject = subject
        self.context = context
        self.primary = primary
        self.secondary = secondary
        self.sparkline = sparkline
        self.sparklineMetric = sparklineMetric
    }

    private enum CodingKeys: String, CodingKey {
        case subject, context, primary, secondary, sparkline, sparklineMetric
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        subject = try container.decode(SubjectRef.self, forKey: .subject)
        context = try container.decodeIfPresent(String.self, forKey: .context)
        primary = try container.decode(MetricValue.self, forKey: .primary)
        secondary = try container.decodeIfPresent([MetricValue].self, forKey: .secondary) ?? []
        sparkline = try container.decodeIfPresent([SparklinePoint].self, forKey: .sparkline) ?? []
        sparklineMetric = try container.decodeIfPresent(String.self, forKey: .sparklineMetric)
    }

    public static let preview = StatTilePayload(
        subject: .preview,
        context: "2025-26 · Regular Season · Per Game",
        primary: .preview,
        secondary: [
            MetricValue(metric: "pts", value: 24.1, displayValue: "24.1", rank: 18, percentile: 0.88, leagueAverage: 12.6, delta: 1.4),
            MetricValue(metric: "usg_pct", value: 0.281, displayValue: "28.1%", rank: 31, percentile: 0.79, leagueAverage: 0.2, delta: -0.004)
        ],
        sparkline: [
            SparklinePoint(x: "2025-12-26", y: 0.588, gameId: "0022500481"),
            SparklinePoint(x: "2025-12-28", y: 0.642, gameId: "0022500489"),
            SparklinePoint(x: "2025-12-30", y: 0.515, gameId: "0022500497"),
            SparklinePoint(x: "2026-01-01", y: 0.703, gameId: "0022500505"),
            SparklinePoint(x: "2026-01-02", y: 0.641, gameId: "0022500512")
        ],
        sparklineMetric: "ts_pct"
    )
}

// MARK: - player_snapshot

/// `player_snapshot` (`contracts/CONTRACT.md` §4).
public struct PlayerSnapshotPayload: Codable, Hashable, Sendable {
    public let player: PlayerRef
    public let season: String?
    public let seasonType: String?
    public let teamAbbr: String?
    public let gp: Int?
    public let gs: Int?
    public let minutesPerGame: Double?
    public let metrics: [MetricValue]
    /// Set when the season predates part of what the widget would normally show.
    public let eraNote: String?

    public init(player: PlayerRef,
                season: String? = nil,
                seasonType: String? = nil,
                teamAbbr: String? = nil,
                gp: Int? = nil,
                gs: Int? = nil,
                minutesPerGame: Double? = nil,
                metrics: [MetricValue] = [],
                eraNote: String? = nil) {
        self.player = player
        self.season = season
        self.seasonType = seasonType
        self.teamAbbr = teamAbbr
        self.gp = gp
        self.gs = gs
        self.minutesPerGame = minutesPerGame
        self.metrics = metrics
        self.eraNote = eraNote
    }

    private enum CodingKeys: String, CodingKey {
        case player, season, seasonType, teamAbbr, gp, gs, minutesPerGame, metrics, eraNote
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        player = try container.decode(PlayerRef.self, forKey: .player)
        season = try container.decodeIfPresent(String.self, forKey: .season)
        seasonType = try container.decodeIfPresent(String.self, forKey: .seasonType)
        teamAbbr = try container.decodeIfPresent(String.self, forKey: .teamAbbr)
        gp = try container.decodeIfPresent(Int.self, forKey: .gp)
        gs = try container.decodeIfPresent(Int.self, forKey: .gs)
        minutesPerGame = try container.decodeIfPresent(Double.self, forKey: .minutesPerGame)
        metrics = try container.decodeIfPresent([MetricValue].self, forKey: .metrics) ?? []
        eraNote = try container.decodeIfPresent(String.self, forKey: .eraNote)
    }

    public static let preview = PlayerSnapshotPayload(
        player: .preview,
        season: "2025-26",
        seasonType: "Regular Season",
        teamAbbr: "LAL",
        gp: 41,
        gs: 41,
        minutesPerGame: 34.5,
        metrics: [
            .preview,
            MetricValue(metric: "usg_pct", value: 0.281, displayValue: "28.1%", rank: 31, percentile: 0.79, leagueAverage: 0.2),
            MetricValue(metric: "ast_pct", value: 0.402, displayValue: "40.2%", rank: 6, percentile: 0.96, leagueAverage: 0.151),
            MetricValue(metric: "reb_pct", value: 0.112, displayValue: "11.2%", rank: 74, percentile: 0.55, leagueAverage: 0.1),
            MetricValue(metric: "off_rtg", value: 118.2, displayValue: "118.2", rank: 22, percentile: 0.84, leagueAverage: 114.1),
            MetricValue(metric: "def_rtg", value: 110.4, displayValue: "110.4", rank: 29, percentile: 0.81, leagueAverage: 114.1),
            MetricValue(metric: "net_rtg", value: 7.8, displayValue: "+7.8", rank: 12, percentile: 0.91, leagueAverage: 0),
            MetricValue(metric: "pie", value: 0.171, displayValue: "17.1%", rank: 9, percentile: 0.94, leagueAverage: 0.1)
        ],
        eraNote: nil
    )
}

// MARK: - leaderboard

/// One row of a `leaderboard` payload.
public struct LeaderboardRow: Codable, Hashable, Sendable, Identifiable {
    public let rank: Int
    public let player: PlayerRef?
    public let team: TeamRef?
    public let value: MetricValue
    public let secondary: [MetricValue]
    /// For an all-time leaderboard, the season being ranked.
    public let season: String?

    public var id: String {
        if let player = player { return "\(rank)-p\(player.playerId)-\(season ?? "")" }
        if let team = team { return "\(rank)-t\(team.teamId)-\(season ?? "")" }
        return "\(rank)"
    }

    /// The name to print in the row.
    public var subjectName: String {
        player?.name ?? team?.name ?? Formatting.emDash
    }

    public init(rank: Int,
                player: PlayerRef? = nil,
                team: TeamRef? = nil,
                value: MetricValue,
                secondary: [MetricValue] = [],
                season: String? = nil) {
        self.rank = rank
        self.player = player
        self.team = team
        self.value = value
        self.secondary = secondary
        self.season = season
    }

    private enum CodingKeys: String, CodingKey {
        case rank, player, team, value, secondary, season
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        rank = try container.decodeIfPresent(Int.self, forKey: .rank) ?? 0
        player = try container.decodeIfPresent(PlayerRef.self, forKey: .player)
        team = try container.decodeIfPresent(TeamRef.self, forKey: .team)
        value = try container.decode(MetricValue.self, forKey: .value)
        secondary = try container.decodeIfPresent([MetricValue].self, forKey: .secondary) ?? []
        season = try container.decodeIfPresent(String.self, forKey: .season)
    }
}

/// `leaderboard` (`contracts/CONTRACT.md` §4).
public struct LeaderboardPayload: Codable, Hashable, Sendable {
    public let metric: MetricDescriptor
    public let subjectType: String
    /// `"season"` or `"all_time"`.
    public let scope: String
    public let season: String?
    public let seasonType: String?
    public let perMode: String?
    /// `"Minimum 15 games and 24.0 minutes per game"`.
    public let qualifier: String?
    public let secondaryMetrics: [MetricDescriptor]
    public let rows: [LeaderboardRow]
    public let nextCursor: String?

    public init(metric: MetricDescriptor,
                subjectType: String = "player",
                scope: String = "season",
                season: String? = nil,
                seasonType: String? = nil,
                perMode: String? = nil,
                qualifier: String? = nil,
                secondaryMetrics: [MetricDescriptor] = [],
                rows: [LeaderboardRow] = [],
                nextCursor: String? = nil) {
        self.metric = metric
        self.subjectType = subjectType
        self.scope = scope
        self.season = season
        self.seasonType = seasonType
        self.perMode = perMode
        self.qualifier = qualifier
        self.secondaryMetrics = secondaryMetrics
        self.rows = rows
        self.nextCursor = nextCursor
    }

    private enum CodingKeys: String, CodingKey {
        case metric, subjectType, scope, season, seasonType, perMode, qualifier
        case secondaryMetrics, rows, nextCursor
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        metric = try container.decode(MetricDescriptor.self, forKey: .metric)
        subjectType = try container.decodeIfPresent(String.self, forKey: .subjectType) ?? "player"
        scope = try container.decodeIfPresent(String.self, forKey: .scope) ?? "season"
        season = try container.decodeIfPresent(String.self, forKey: .season)
        seasonType = try container.decodeIfPresent(String.self, forKey: .seasonType)
        perMode = try container.decodeIfPresent(String.self, forKey: .perMode)
        qualifier = try container.decodeIfPresent(String.self, forKey: .qualifier)
        secondaryMetrics = try container.decodeIfPresent([MetricDescriptor].self, forKey: .secondaryMetrics) ?? []
        rows = try container.decodeIfPresent([LeaderboardRow].self, forKey: .rows) ?? []
        nextCursor = try container.decodeIfPresent(String.self, forKey: .nextCursor)
    }

    public static let preview = LeaderboardPayload(
        metric: .preview,
        subjectType: "player",
        scope: "season",
        season: "2025-26",
        seasonType: "Regular Season",
        perMode: "PerGame",
        qualifier: "Minimum 15 games and 20.0 minutes per game",
        secondaryMetrics: [],
        rows: [
            LeaderboardRow(rank: 1, player: .previewSecondary,
                           value: MetricValue(metric: "ts_pct", value: 0.6612, displayValue: "66.1%", rank: 1, percentile: 1.0, leagueAverage: 0.5671),
                           season: "2025-26"),
            LeaderboardRow(rank: 2, player: .preview,
                           value: MetricValue(metric: "ts_pct", value: 0.6153, displayValue: "61.5%", rank: 2, percentile: 0.97, leagueAverage: 0.5671),
                           season: "2025-26"),
            LeaderboardRow(rank: 3, player: PlayerRef(playerId: 203_507, name: "Giannis Antetokounmpo", firstName: "Giannis", lastName: "Antetokounmpo", teamId: 1_610_612_749, teamAbbr: "MIL", position: "F", jersey: "34", headshotUrl: nil, isActive: true),
                           value: MetricValue(metric: "ts_pct", value: 0.6098, displayValue: "61.0%", rank: 3, percentile: 0.95, leagueAverage: 0.5671),
                           season: "2025-26")
        ],
        nextCursor: nil
    )
}

// MARK: - game_log

/// One row of a `game_log` payload.
///
/// `values` is keyed by metric key; a metric the era never recorded is present with a JSON `null`
/// so the table can print an em dash instead of a zero.
public struct GameLogPayloadRow: Codable, Hashable, Sendable, Identifiable {
    public let gameId: GameID
    public let date: String
    public let opponentAbbr: String?
    public let isHome: Bool?
    public let result: String?
    public let score: String?
    public let started: Bool?
    public let minutes: Double?
    public let values: [String: JSONValue]
    public let availability: MetricAvailability

    public var id: GameID { gameId }

    /// `"vs BOS"` or `"@ BOS"`.
    public var matchupText: String {
        guard let opponentAbbr = opponentAbbr else { return Formatting.emDash }
        return (isHome ?? true) ? "vs \(opponentAbbr)" : "@ \(opponentAbbr)"
    }

    public func value(_ metric: String) -> Double? { values[metric]?.doubleValue }

    public init(gameId: GameID,
                date: String,
                opponentAbbr: String? = nil,
                isHome: Bool? = nil,
                result: String? = nil,
                score: String? = nil,
                started: Bool? = nil,
                minutes: Double? = nil,
                values: [String: JSONValue] = [:],
                availability: MetricAvailability = .full) {
        self.gameId = gameId
        self.date = date
        self.opponentAbbr = opponentAbbr
        self.isHome = isHome
        self.result = result
        self.score = score
        self.started = started
        self.minutes = minutes
        self.values = values
        self.availability = availability
    }

    private enum CodingKeys: String, CodingKey {
        case gameId, date, opponentAbbr, isHome, result, score, started, minutes, values, availability
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        gameId = try container.decode(GameID.self, forKey: .gameId)
        date = try container.decodeIfPresent(String.self, forKey: .date) ?? ""
        opponentAbbr = try container.decodeIfPresent(String.self, forKey: .opponentAbbr)
        isHome = try container.decodeIfPresent(Bool.self, forKey: .isHome)
        result = try container.decodeIfPresent(String.self, forKey: .result)
        score = try container.decodeIfPresent(String.self, forKey: .score)
        started = try container.decodeIfPresent(Bool.self, forKey: .started)
        minutes = try container.decodeIfPresent(Double.self, forKey: .minutes)
        values = try container.decodeIfPresent([String: JSONValue].self, forKey: .values) ?? [:]
        availability = MetricAvailability.decoded(from: container, forKey: .availability)
    }
}

/// `game_log` (`contracts/CONTRACT.md` §4).
public struct GameLogPayload: Codable, Hashable, Sendable {
    public let player: PlayerRef
    public let season: String?
    public let seasonType: String?
    public let columns: [MetricDescriptor]
    /// The season's best value per metric key, used to highlight a career-night cell.
    public let seasonBests: [String: JSONValue]
    public let rows: [GameLogPayloadRow]

    public init(player: PlayerRef,
                season: String? = nil,
                seasonType: String? = nil,
                columns: [MetricDescriptor] = [],
                seasonBests: [String: JSONValue] = [:],
                rows: [GameLogPayloadRow] = []) {
        self.player = player
        self.season = season
        self.seasonType = seasonType
        self.columns = columns
        self.seasonBests = seasonBests
        self.rows = rows
    }

    /// The season best for a metric key, when there is one.
    public func seasonBest(_ metric: String) -> Double? { seasonBests[metric]?.doubleValue }

    private enum CodingKeys: String, CodingKey {
        case player, season, seasonType, columns, seasonBests, rows
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        player = try container.decode(PlayerRef.self, forKey: .player)
        season = try container.decodeIfPresent(String.self, forKey: .season)
        seasonType = try container.decodeIfPresent(String.self, forKey: .seasonType)
        columns = try container.decodeIfPresent([MetricDescriptor].self, forKey: .columns) ?? []
        seasonBests = try container.decodeIfPresent([String: JSONValue].self, forKey: .seasonBests) ?? [:]
        rows = try container.decodeIfPresent([GameLogPayloadRow].self, forKey: .rows) ?? []
    }

    public static let preview = GameLogPayload(
        player: .preview,
        season: "2025-26",
        seasonType: "Regular Season",
        columns: [
            MetricDescriptor(key: "min", name: "Minutes", shortName: "MIN", category: "volume", format: .minutes,
                             higherIsBetter: true, scope: ["player", "team"],
                             availability: MetricDescriptor.Availability(seasonFrom: "1951-52", perGameFrom: "1951-52", seasonLevelOnly: false, estimatedBefore: nil),
                             domain: nil, glossary: "Minutes played."),
            MetricDescriptor(key: "pts", name: "Points", shortName: "PTS", category: "volume", format: .decimal1,
                             higherIsBetter: true, scope: ["player", "team"],
                             availability: MetricDescriptor.Availability(seasonFrom: "1946-47", perGameFrom: "1946-47", seasonLevelOnly: false, estimatedBefore: nil),
                             domain: nil, glossary: "Points scored."),
            .preview
        ],
        seasonBests: ["pts": .int(42), "ts_pct": .double(0.812)],
        rows: [
            GameLogPayloadRow(gameId: "0022500512", date: "2026-01-02", opponentAbbr: "BOS", isHome: true,
                              result: "W", score: "118-112", started: true, minutes: 34.5,
                              values: ["min": .double(34.5), "pts": .int(32), "ts_pct": .double(0.641)],
                              availability: .full),
            GameLogPayloadRow(gameId: "0022500505", date: "2026-01-01", opponentAbbr: "PHX", isHome: false,
                              result: "L", score: "104-111", started: true, minutes: 36.2,
                              values: ["min": .double(36.2), "pts": .int(27), "ts_pct": .double(0.703)],
                              availability: .full),
            GameLogPayloadRow(gameId: "0022500497", date: "2025-12-30", opponentAbbr: "GSW", isHome: true,
                              result: "W", score: "126-120", started: true, minutes: 31.8,
                              values: ["min": .double(31.8), "pts": .int(19), "ts_pct": .double(0.515)],
                              availability: .full)
        ]
    )
}

// MARK: - trend_chart

/// One point of a `trend_chart` series.
public struct TrendPoint: Codable, Hashable, Sendable, Identifiable {
    /// The game's calendar date, `"2026-01-02"`.
    public let x: String
    /// The game's own value; `nil` breaks the line rather than plotting a zero.
    public let y: Double?
    /// The rolling average over `rollingWindow` games.
    public let rolling: Double?
    public let gameId: GameID?
    public let opponentAbbr: String?

    public var id: String { gameId ?? x }

    public init(x: String, y: Double?, rolling: Double? = nil, gameId: GameID? = nil, opponentAbbr: String? = nil) {
        self.x = x
        self.y = y
        self.rolling = rolling
        self.gameId = gameId
        self.opponentAbbr = opponentAbbr
    }
}

/// One line of a `trend_chart`.
public struct TrendSeries: Codable, Hashable, Sendable, Identifiable {
    public let id: String
    public let label: String
    /// Index into the design system's categorical palette.
    public let colorIndex: Int
    public let points: [TrendPoint]

    public init(id: String, label: String, colorIndex: Int = 0, points: [TrendPoint] = []) {
        self.id = id
        self.label = label
        self.colorIndex = colorIndex
        self.points = points
    }

    private enum CodingKeys: String, CodingKey {
        case id, label, colorIndex, points
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decodeIfPresent(String.self, forKey: .id) ?? UUID().uuidString
        label = try container.decodeIfPresent(String.self, forKey: .label) ?? ""
        colorIndex = try container.decodeIfPresent(Int.self, forKey: .colorIndex) ?? 0
        points = try container.decodeIfPresent([TrendPoint].self, forKey: .points) ?? []
    }
}

/// `trend_chart` (`contracts/CONTRACT.md` §4).
public struct TrendChartPayload: Codable, Hashable, Sendable {
    public let metric: MetricDescriptor
    public let rollingWindow: Int?
    public let leagueAverage: Double?
    /// The y-axis range the server suggests; falls back to the metric's own domain.
    public let yDomain: MetricDescriptor.Domain?
    public let series: [TrendSeries]

    public init(metric: MetricDescriptor,
                rollingWindow: Int? = nil,
                leagueAverage: Double? = nil,
                yDomain: MetricDescriptor.Domain? = nil,
                series: [TrendSeries] = []) {
        self.metric = metric
        self.rollingWindow = rollingWindow
        self.leagueAverage = leagueAverage
        self.yDomain = yDomain
        self.series = series
    }

    private enum CodingKeys: String, CodingKey {
        case metric, rollingWindow, leagueAverage, yDomain, series
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        metric = try container.decode(MetricDescriptor.self, forKey: .metric)
        rollingWindow = try container.decodeIfPresent(Int.self, forKey: .rollingWindow)
        leagueAverage = try container.decodeIfPresent(Double.self, forKey: .leagueAverage)
        yDomain = try container.decodeIfPresent(MetricDescriptor.Domain.self, forKey: .yDomain)
        series = try container.decodeIfPresent([TrendSeries].self, forKey: .series) ?? []
    }

    public static let preview = TrendChartPayload(
        metric: .preview,
        rollingWindow: 5,
        leagueAverage: 0.5671,
        yDomain: MetricDescriptor.Domain(min: 0.38, max: 0.78),
        series: [
            TrendSeries(id: "2544", label: "LeBron James", colorIndex: 0, points: [
                TrendPoint(x: "2025-12-26", y: 0.588, rolling: 0.571, gameId: "0022500481", opponentAbbr: "SAC"),
                TrendPoint(x: "2025-12-28", y: 0.642, rolling: 0.589, gameId: "0022500489", opponentAbbr: "DEN"),
                TrendPoint(x: "2025-12-30", y: 0.515, rolling: 0.581, gameId: "0022500497", opponentAbbr: "GSW"),
                TrendPoint(x: "2026-01-01", y: 0.703, rolling: 0.604, gameId: "0022500505", opponentAbbr: "PHX"),
                TrendPoint(x: "2026-01-02", y: 0.641, rolling: 0.618, gameId: "0022500512", opponentAbbr: "BOS")
            ]),
            TrendSeries(id: "1629029", label: "Luka Doncic", colorIndex: 1, points: [
                TrendPoint(x: "2025-12-26", y: 0.661, rolling: 0.648, gameId: "0022500482", opponentAbbr: "MIN"),
                TrendPoint(x: "2025-12-28", y: 0.612, rolling: 0.639, gameId: "0022500490", opponentAbbr: "OKC"),
                TrendPoint(x: "2025-12-30", y: 0.702, rolling: 0.657, gameId: "0022500498", opponentAbbr: "HOU"),
                TrendPoint(x: "2026-01-01", y: 0.548, rolling: 0.641, gameId: "0022500506", opponentAbbr: "SAS"),
                TrendPoint(x: "2026-01-02", y: 0.688, rolling: 0.652, gameId: "0022500513", opponentAbbr: "MEM")
            ])
        ]
    )
}

// MARK: - four_factors

/// One of Dean Oliver's four factors, offensive or defensive.
public struct FourFactorEntry: Codable, Hashable, Sendable, Identifiable {
    public let key: String
    public let label: String
    /// 0.40 / 0.25 / 0.20 / 0.15 for eFG%, TOV%, OREB%, FTr.
    public let weight: Double
    public let value: Double?
    public let displayValue: String
    public let leagueAverage: Double?
    public let percentile: Double?
    public let rank: Int?

    public var id: String { key }

    public init(key: String,
                label: String,
                weight: Double,
                value: Double?,
                displayValue: String,
                leagueAverage: Double? = nil,
                percentile: Double? = nil,
                rank: Int? = nil) {
        self.key = key
        self.label = label
        self.weight = weight
        self.value = value
        self.displayValue = displayValue
        self.leagueAverage = leagueAverage
        self.percentile = percentile
        self.rank = rank
    }

    private enum CodingKeys: String, CodingKey {
        case key, label, weight, value, displayValue, leagueAverage, percentile, rank
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        key = try container.decode(String.self, forKey: .key)
        label = try container.decodeIfPresent(String.self, forKey: .label) ?? key
        weight = try container.decodeIfPresent(Double.self, forKey: .weight) ?? 0
        value = try container.decodeIfPresent(Double.self, forKey: .value)
        displayValue = try container.decodeIfPresent(String.self, forKey: .displayValue) ?? Formatting.emDash
        leagueAverage = try container.decodeIfPresent(Double.self, forKey: .leagueAverage)
        percentile = try container.decodeIfPresent(Double.self, forKey: .percentile)
        rank = try container.decodeIfPresent(Int.self, forKey: .rank)
    }
}

/// `four_factors` (`contracts/CONTRACT.md` §4). Factor order is fixed: eFG%, TOV%, OREB%, FTr.
public struct FourFactorsPayload: Codable, Hashable, Sendable {
    public let team: TeamRef
    public let season: String?
    public let seasonType: String?
    public let offense: [FourFactorEntry]
    public let defense: [FourFactorEntry]

    public init(team: TeamRef,
                season: String? = nil,
                seasonType: String? = nil,
                offense: [FourFactorEntry] = [],
                defense: [FourFactorEntry] = []) {
        self.team = team
        self.season = season
        self.seasonType = seasonType
        self.offense = offense
        self.defense = defense
    }

    private enum CodingKeys: String, CodingKey {
        case team, season, seasonType, offense, defense
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        team = try container.decode(TeamRef.self, forKey: .team)
        season = try container.decodeIfPresent(String.self, forKey: .season)
        seasonType = try container.decodeIfPresent(String.self, forKey: .seasonType)
        offense = try container.decodeIfPresent([FourFactorEntry].self, forKey: .offense) ?? []
        defense = try container.decodeIfPresent([FourFactorEntry].self, forKey: .defense) ?? []
    }

    public static let preview = FourFactorsPayload(
        team: .preview,
        season: "2025-26",
        seasonType: "Regular Season",
        offense: [
            FourFactorEntry(key: "efg_pct", label: "eFG%", weight: 0.40, value: 0.556, displayValue: "55.6%", leagueAverage: 0.538, percentile: 0.72, rank: 8),
            FourFactorEntry(key: "tov_pct", label: "TOV%", weight: 0.25, value: 0.128, displayValue: "12.8%", leagueAverage: 0.134, percentile: 0.64, rank: 11),
            FourFactorEntry(key: "oreb_pct", label: "OREB%", weight: 0.20, value: 0.291, displayValue: "29.1%", leagueAverage: 0.272, percentile: 0.70, rank: 9),
            FourFactorEntry(key: "ftr", label: "FTr", weight: 0.15, value: 0.244, displayValue: "24.4%", leagueAverage: 0.231, percentile: 0.61, rank: 12)
        ],
        defense: [
            FourFactorEntry(key: "opp_efg_pct", label: "Opp eFG%", weight: 0.40, value: 0.521, displayValue: "52.1%", leagueAverage: 0.538, percentile: 0.78, rank: 6),
            FourFactorEntry(key: "opp_tov_pct", label: "Opp TOV%", weight: 0.25, value: 0.141, displayValue: "14.1%", leagueAverage: 0.134, percentile: 0.69, rank: 10),
            FourFactorEntry(key: "opp_oreb_pct", label: "Opp OREB%", weight: 0.20, value: 0.258, displayValue: "25.8%", leagueAverage: 0.272, percentile: 0.66, rank: 10),
            FourFactorEntry(key: "opp_ftr", label: "Opp FTr", weight: 0.15, value: 0.219, displayValue: "21.9%", leagueAverage: 0.231, percentile: 0.58, rank: 13)
        ]
    )
}

// MARK: - shot_profile

/// One zone of a `shot_profile`. Zone order is fixed: rim, paint_non_rim, mid_range,
/// corner_three, above_break_three.
public struct ShotZone: Codable, Hashable, Sendable, Identifiable {
    public let zone: String
    public let label: String
    public let fga: Double?
    public let fgPct: Double?
    public let shareOfFga: Double?
    public let pointsPerShot: Double?
    public let leagueFgPct: Double?
    public let leagueShareOfFga: Double?

    public var id: String { zone }

    public init(zone: String,
                label: String,
                fga: Double? = nil,
                fgPct: Double? = nil,
                shareOfFga: Double? = nil,
                pointsPerShot: Double? = nil,
                leagueFgPct: Double? = nil,
                leagueShareOfFga: Double? = nil) {
        self.zone = zone
        self.label = label
        self.fga = fga
        self.fgPct = fgPct
        self.shareOfFga = shareOfFga
        self.pointsPerShot = pointsPerShot
        self.leagueFgPct = leagueFgPct
        self.leagueShareOfFga = leagueShareOfFga
    }

    private enum CodingKeys: String, CodingKey {
        case zone, label, fga, fgPct, shareOfFga, pointsPerShot, leagueFgPct, leagueShareOfFga
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        zone = try container.decode(String.self, forKey: .zone)
        label = try container.decodeIfPresent(String.self, forKey: .label) ?? zone
        fga = try container.decodeIfPresent(Double.self, forKey: .fga)
        fgPct = try container.decodeIfPresent(Double.self, forKey: .fgPct)
        shareOfFga = try container.decodeIfPresent(Double.self, forKey: .shareOfFga)
        pointsPerShot = try container.decodeIfPresent(Double.self, forKey: .pointsPerShot)
        leagueFgPct = try container.decodeIfPresent(Double.self, forKey: .leagueFgPct)
        leagueShareOfFga = try container.decodeIfPresent(Double.self, forKey: .leagueShareOfFga)
    }
}

/// `shot_profile` (`contracts/CONTRACT.md` §4). Only available from 1996-97.
public struct ShotProfilePayload: Codable, Hashable, Sendable {
    public let subject: SubjectRef
    public let season: String?
    public let seasonType: String?
    public let zones: [ShotZone]
    public let threePointRate: Double?
    public let freeThrowRate: Double?
    public let note: String?

    public init(subject: SubjectRef,
                season: String? = nil,
                seasonType: String? = nil,
                zones: [ShotZone] = [],
                threePointRate: Double? = nil,
                freeThrowRate: Double? = nil,
                note: String? = nil) {
        self.subject = subject
        self.season = season
        self.seasonType = seasonType
        self.zones = zones
        self.threePointRate = threePointRate
        self.freeThrowRate = freeThrowRate
        self.note = note
    }

    private enum CodingKeys: String, CodingKey {
        case subject, season, seasonType, zones, threePointRate, freeThrowRate, note
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        subject = try container.decode(SubjectRef.self, forKey: .subject)
        season = try container.decodeIfPresent(String.self, forKey: .season)
        seasonType = try container.decodeIfPresent(String.self, forKey: .seasonType)
        zones = try container.decodeIfPresent([ShotZone].self, forKey: .zones) ?? []
        threePointRate = try container.decodeIfPresent(Double.self, forKey: .threePointRate)
        freeThrowRate = try container.decodeIfPresent(Double.self, forKey: .freeThrowRate)
        note = try container.decodeIfPresent(String.self, forKey: .note)
    }

    public static let preview = ShotProfilePayload(
        subject: .preview,
        season: "2025-26",
        seasonType: "Regular Season",
        zones: [
            ShotZone(zone: "rim", label: "At Rim", fga: 6.2, fgPct: 0.684, shareOfFga: 0.31, pointsPerShot: 1.368, leagueFgPct: 0.652, leagueShareOfFga: 0.28),
            ShotZone(zone: "paint_non_rim", label: "Paint", fga: 3.1, fgPct: 0.442, shareOfFga: 0.15, pointsPerShot: 0.884, leagueFgPct: 0.431, leagueShareOfFga: 0.14),
            ShotZone(zone: "mid_range", label: "Mid-Range", fga: 2.4, fgPct: 0.418, shareOfFga: 0.12, pointsPerShot: 0.836, leagueFgPct: 0.408, leagueShareOfFga: 0.13),
            ShotZone(zone: "corner_three", label: "Corner 3", fga: 2.0, fgPct: 0.401, shareOfFga: 0.10, pointsPerShot: 1.203, leagueFgPct: 0.388, leagueShareOfFga: 0.11),
            ShotZone(zone: "above_break_three", label: "Above the Break 3", fga: 6.4, fgPct: 0.361, shareOfFga: 0.32, pointsPerShot: 1.083, leagueFgPct: 0.355, leagueShareOfFga: 0.34)
        ],
        threePointRate: 0.42,
        freeThrowRate: 0.28,
        note: nil
    )
}

// MARK: - comparison

/// One subject of a `comparison`, with its value for each compared metric in order.
public struct ComparisonSubject: Codable, Hashable, Sendable, Identifiable {
    public let player: PlayerRef?
    public let team: TeamRef?
    public let colorIndex: Int
    public let values: [MetricValue]

    public var id: String {
        if let player = player { return "p\(player.playerId)" }
        if let team = team { return "t\(team.teamId)" }
        return "subject-\(colorIndex)"
    }

    public var displayName: String {
        player?.name ?? team?.name ?? Formatting.emDash
    }

    public init(player: PlayerRef? = nil, team: TeamRef? = nil, colorIndex: Int = 0, values: [MetricValue] = []) {
        self.player = player
        self.team = team
        self.colorIndex = colorIndex
        self.values = values
    }

    /// The value for a metric key, when this subject has one.
    public func value(_ metric: String) -> MetricValue? {
        values.first { $0.metric == metric }
    }

    private enum CodingKeys: String, CodingKey {
        case player, team, colorIndex, values
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        player = try container.decodeIfPresent(PlayerRef.self, forKey: .player)
        team = try container.decodeIfPresent(TeamRef.self, forKey: .team)
        colorIndex = try container.decodeIfPresent(Int.self, forKey: .colorIndex) ?? 0
        values = try container.decodeIfPresent([MetricValue].self, forKey: .values) ?? []
    }
}

/// `comparison` (`contracts/CONTRACT.md` §4).
public struct ComparisonPayload: Codable, Hashable, Sendable {
    public let season: String?
    public let seasonType: String?
    /// `"percentile"` or `"raw"`.
    public let normalization: String
    /// `"bars"`, `"radar"` or `"table"`.
    public let style: String
    public let metrics: [MetricDescriptor]
    public let subjects: [ComparisonSubject]

    public init(season: String? = nil,
                seasonType: String? = nil,
                normalization: String = "percentile",
                style: String = "bars",
                metrics: [MetricDescriptor] = [],
                subjects: [ComparisonSubject] = []) {
        self.season = season
        self.seasonType = seasonType
        self.normalization = normalization
        self.style = style
        self.metrics = metrics
        self.subjects = subjects
    }

    private enum CodingKeys: String, CodingKey {
        case season, seasonType, normalization, style, metrics, subjects
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        season = try container.decodeIfPresent(String.self, forKey: .season)
        seasonType = try container.decodeIfPresent(String.self, forKey: .seasonType)
        normalization = try container.decodeIfPresent(String.self, forKey: .normalization) ?? "percentile"
        style = try container.decodeIfPresent(String.self, forKey: .style) ?? "bars"
        metrics = try container.decodeIfPresent([MetricDescriptor].self, forKey: .metrics) ?? []
        subjects = try container.decodeIfPresent([ComparisonSubject].self, forKey: .subjects) ?? []
    }

    public static let preview = ComparisonPayload(
        season: "2025-26",
        seasonType: "Regular Season",
        normalization: "percentile",
        style: "bars",
        metrics: [.preview, .previewRating],
        subjects: [
            ComparisonSubject(player: .preview, colorIndex: 0, values: [
                MetricValue(metric: "ts_pct", value: 0.6153, displayValue: "61.5%", rank: 12, percentile: 0.93, leagueAverage: 0.5671),
                MetricValue(metric: "net_rtg", value: 7.8, displayValue: "+7.8", rank: 12, percentile: 0.91, leagueAverage: 0)
            ]),
            ComparisonSubject(player: .previewSecondary, colorIndex: 1, values: [
                MetricValue(metric: "ts_pct", value: 0.6612, displayValue: "66.1%", rank: 1, percentile: 1.0, leagueAverage: 0.5671),
                MetricValue(metric: "net_rtg", value: 9.4, displayValue: "+9.4", rank: 4, percentile: 0.97, leagueAverage: 0)
            ])
        ]
    )
}

// MARK: - scoreboard

/// A standout line from one game.
public struct TopPerformer: Codable, Hashable, Sendable, Identifiable {
    public let player: PlayerRef
    public let teamAbbr: String?
    /// `"32 PTS · 8 REB · 11 AST"`.
    public let line: String?
    public let value: MetricValue?

    public var id: PlayerID { player.playerId }

    public init(player: PlayerRef, teamAbbr: String? = nil, line: String? = nil, value: MetricValue? = nil) {
        self.player = player
        self.teamAbbr = teamAbbr
        self.line = line
        self.value = value
    }
}

/// One game on the scoreboard: a `GameRef` flattened together with its top performers.
public struct ScoreboardGame: Codable, Hashable, Sendable, Identifiable {
    public let game: GameRef
    public let topPerformers: [TopPerformer]

    public var id: GameID { game.gameId }

    public init(game: GameRef, topPerformers: [TopPerformer] = []) {
        self.game = game
        self.topPerformers = topPerformers
    }

    private enum CodingKeys: String, CodingKey {
        case topPerformers
    }

    public init(from decoder: Decoder) throws {
        game = try GameRef(from: decoder)
        let container = try decoder.container(keyedBy: CodingKeys.self)
        topPerformers = try container.decodeIfPresent([TopPerformer].self, forKey: .topPerformers) ?? []
    }

    public func encode(to encoder: Encoder) throws {
        try game.encode(to: encoder)
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(topPerformers, forKey: .topPerformers)
    }
}

/// `scoreboard` (`contracts/CONTRACT.md` §4).
public struct ScoreboardPayload: Codable, Hashable, Sendable {
    public let date: String
    public let isLatestCompleted: Bool?
    public let allFinal: Bool?
    public let games: [ScoreboardGame]

    public init(date: String, isLatestCompleted: Bool? = nil, allFinal: Bool? = nil, games: [ScoreboardGame] = []) {
        self.date = date
        self.isLatestCompleted = isLatestCompleted
        self.allFinal = allFinal
        self.games = games
    }

    private enum CodingKeys: String, CodingKey {
        case date, isLatestCompleted, allFinal, games
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        date = try container.decodeIfPresent(String.self, forKey: .date) ?? ""
        isLatestCompleted = try container.decodeIfPresent(Bool.self, forKey: .isLatestCompleted)
        allFinal = try container.decodeIfPresent(Bool.self, forKey: .allFinal)
        games = try container.decodeIfPresent([ScoreboardGame].self, forKey: .games) ?? []
    }

    public static let preview = ScoreboardPayload(
        date: "2026-01-02",
        isLatestCompleted: true,
        allFinal: true,
        games: [
            ScoreboardGame(game: .preview, topPerformers: [
                TopPerformer(player: .preview, teamAbbr: "LAL", line: "32 PTS · 8 REB · 11 AST",
                             value: MetricValue(metric: "game_score", value: 28.4, displayValue: "28.4", availability: .full)),
                TopPerformer(player: PlayerRef(playerId: 1_628_369, name: "Jayson Tatum", firstName: "Jayson", lastName: "Tatum", teamId: 1_610_612_738, teamAbbr: "BOS", position: "F", jersey: "0", headshotUrl: nil, isActive: true),
                             teamAbbr: "BOS", line: "29 PTS · 10 REB",
                             value: MetricValue(metric: "game_score", value: 24.1, displayValue: "24.1", availability: .full))
            ]),
            ScoreboardGame(game: GameRef(gameId: "0022500513", date: "2026-01-02",
                                         season: "2025-26", seasonType: "Regular Season",
                                         home: TeamRef(teamId: 1_610_612_763, abbr: "MEM", name: "Memphis Grizzlies", city: "Memphis", nickname: "Grizzlies", conference: "West", division: "Southwest"),
                                         away: TeamRef(teamId: 1_610_612_742, abbr: "DAL", name: "Dallas Mavericks", city: "Dallas", nickname: "Mavericks", conference: "West", division: "Southwest"),
                                         homePts: 109, awayPts: 121, status: .final, period: 4, clock: nil,
                                         finalizedAt: "2026-01-03T03:12:55Z"),
                           topPerformers: [])
        ]
    )
}

// MARK: - daily_movers

/// One row of `daily_movers`.
public struct DailyMoverRow: Codable, Hashable, Sendable, Identifiable {
    public let rank: Int
    public let player: PlayerRef
    public let gameId: GameID?
    public let opponentAbbr: String?
    public let isHome: Bool?
    public let result: String?
    /// `"46 PTS · 9 AST"`.
    public let line: String?
    public let value: MetricValue
    public let seasonAverage: Double?
    public let delta: Double?

    public var id: String { "\(rank)-\(player.playerId)" }

    /// `"vs BOS"` or `"@ BOS"`.
    public var matchupText: String {
        guard let opponentAbbr = opponentAbbr else { return Formatting.emDash }
        return (isHome ?? true) ? "vs \(opponentAbbr)" : "@ \(opponentAbbr)"
    }

    public init(rank: Int,
                player: PlayerRef,
                gameId: GameID? = nil,
                opponentAbbr: String? = nil,
                isHome: Bool? = nil,
                result: String? = nil,
                line: String? = nil,
                value: MetricValue,
                seasonAverage: Double? = nil,
                delta: Double? = nil) {
        self.rank = rank
        self.player = player
        self.gameId = gameId
        self.opponentAbbr = opponentAbbr
        self.isHome = isHome
        self.result = result
        self.line = line
        self.value = value
        self.seasonAverage = seasonAverage
        self.delta = delta
    }

    private enum CodingKeys: String, CodingKey {
        case rank, player, gameId, opponentAbbr, isHome, result, line, value, seasonAverage, delta
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        rank = try container.decodeIfPresent(Int.self, forKey: .rank) ?? 0
        player = try container.decode(PlayerRef.self, forKey: .player)
        gameId = try container.decodeIfPresent(GameID.self, forKey: .gameId)
        opponentAbbr = try container.decodeIfPresent(String.self, forKey: .opponentAbbr)
        isHome = try container.decodeIfPresent(Bool.self, forKey: .isHome)
        result = try container.decodeIfPresent(String.self, forKey: .result)
        line = try container.decodeIfPresent(String.self, forKey: .line)
        value = try container.decode(MetricValue.self, forKey: .value)
        seasonAverage = try container.decodeIfPresent(Double.self, forKey: .seasonAverage)
        delta = try container.decodeIfPresent(Double.self, forKey: .delta)
    }
}

/// `daily_movers` (`contracts/CONTRACT.md` §4).
public struct DailyMoversPayload: Codable, Hashable, Sendable {
    public let date: String
    public let metric: MetricDescriptor
    /// `"best"`, `"worst"` or `"surprise"`.
    public let direction: String
    public let rows: [DailyMoverRow]

    public init(date: String, metric: MetricDescriptor, direction: String = "best", rows: [DailyMoverRow] = []) {
        self.date = date
        self.metric = metric
        self.direction = direction
        self.rows = rows
    }

    private enum CodingKeys: String, CodingKey {
        case date, metric, direction, rows
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        date = try container.decodeIfPresent(String.self, forKey: .date) ?? ""
        metric = try container.decode(MetricDescriptor.self, forKey: .metric)
        direction = try container.decodeIfPresent(String.self, forKey: .direction) ?? "best"
        rows = try container.decodeIfPresent([DailyMoverRow].self, forKey: .rows) ?? []
    }

    public static let preview = DailyMoversPayload(
        date: "2026-01-02",
        metric: .previewImpact,
        direction: "best",
        rows: [
            DailyMoverRow(rank: 1, player: .previewSecondary, gameId: "0022500513", opponentAbbr: "MEM", isHome: false,
                          result: "W", line: "46 PTS · 9 AST",
                          value: MetricValue(metric: "game_score", value: 38.2, displayValue: "38.2", rank: 1, percentile: 1.0, leagueAverage: 9.8),
                          seasonAverage: 24.1, delta: 14.1),
            DailyMoverRow(rank: 2, player: .preview, gameId: "0022500512", opponentAbbr: "BOS", isHome: true,
                          result: "W", line: "32 PTS · 11 AST",
                          value: MetricValue(metric: "game_score", value: 28.4, displayValue: "28.4", rank: 2, percentile: 0.99, leagueAverage: 9.8),
                          seasonAverage: 21.7, delta: 6.7)
        ]
    )
}

// MARK: - team_efficiency

/// One team's row in the efficiency table.
///
/// `values` and `ranks` are keyed by metric key, and both use `JSONValue` so a metric the era
/// never recorded arrives as a JSON `null` rather than as a zero.
public struct TeamEfficiencyRow: Codable, Hashable, Sendable, Identifiable {
    public let rank: Int
    public let team: TeamRef
    public let wins: Int?
    public let losses: Int?
    public let values: [String: JSONValue]
    public let ranks: [String: JSONValue]

    public var id: TeamID { team.teamId }

    /// `"27-14"`.
    public var recordText: String {
        guard let wins = wins, let losses = losses else { return Formatting.emDash }
        return "\(wins)-\(losses)"
    }

    /// The raw value for a metric key, or `nil` when the era did not record it.
    public func value(_ metric: String) -> Double? { values[metric]?.doubleValue }
    /// This team's league rank in a metric, when the payload carries one.
    public func rankValue(_ metric: String) -> Int? { ranks[metric]?.intValue }

    public init(rank: Int,
                team: TeamRef,
                wins: Int? = nil,
                losses: Int? = nil,
                values: [String: JSONValue] = [:],
                ranks: [String: JSONValue] = [:]) {
        self.rank = rank
        self.team = team
        self.wins = wins
        self.losses = losses
        self.values = values
        self.ranks = ranks
    }

    private enum CodingKeys: String, CodingKey {
        case rank, team, wins, losses, values, ranks
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        rank = try container.decodeIfPresent(Int.self, forKey: .rank) ?? 0
        team = try container.decode(TeamRef.self, forKey: .team)
        wins = try container.decodeIfPresent(Int.self, forKey: .wins)
        losses = try container.decodeIfPresent(Int.self, forKey: .losses)
        values = try container.decodeIfPresent([String: JSONValue].self, forKey: .values) ?? [:]
        ranks = try container.decodeIfPresent([String: JSONValue].self, forKey: .ranks) ?? [:]
    }
}

/// `team_efficiency` (`contracts/CONTRACT.md` §4).
public struct TeamEfficiencyPayload: Codable, Hashable, Sendable {
    public let season: String?
    public let seasonType: String?
    /// The metric key the table is sorted by.
    public let sortBy: String
    /// `"table"` or `"scatter"`.
    public let style: String
    public let leagueAverage: [String: JSONValue]
    public let rows: [TeamEfficiencyRow]

    public init(season: String? = nil,
                seasonType: String? = nil,
                sortBy: String = "net_rtg",
                style: String = "table",
                leagueAverage: [String: JSONValue] = [:],
                rows: [TeamEfficiencyRow] = []) {
        self.season = season
        self.seasonType = seasonType
        self.sortBy = sortBy
        self.style = style
        self.leagueAverage = leagueAverage
        self.rows = rows
    }

    /// The league average for a metric key, when the payload carries one.
    public func leagueAverageValue(_ metric: String) -> Double? { leagueAverage[metric]?.doubleValue }

    private enum CodingKeys: String, CodingKey {
        case season, seasonType, sortBy, style, leagueAverage, rows
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        season = try container.decodeIfPresent(String.self, forKey: .season)
        seasonType = try container.decodeIfPresent(String.self, forKey: .seasonType)
        sortBy = try container.decodeIfPresent(String.self, forKey: .sortBy) ?? "net_rtg"
        style = try container.decodeIfPresent(String.self, forKey: .style) ?? "table"
        leagueAverage = try container.decodeIfPresent([String: JSONValue].self, forKey: .leagueAverage) ?? [:]
        rows = try container.decodeIfPresent([TeamEfficiencyRow].self, forKey: .rows) ?? []
    }

    public static let preview = TeamEfficiencyPayload(
        season: "2025-26",
        seasonType: "Regular Season",
        sortBy: "net_rtg",
        style: "table",
        leagueAverage: ["off_rtg": .double(114.1), "def_rtg": .double(114.1), "pace": .double(98.6)],
        rows: [
            TeamEfficiencyRow(rank: 1, team: .preview, wins: 27, losses: 14,
                              values: ["off_rtg": .double(118.2), "def_rtg": .double(110.4), "net_rtg": .double(7.8), "pace": .double(99.4)],
                              ranks: ["off_rtg": .int(3), "def_rtg": .int(5), "net_rtg": .int(1)]),
            TeamEfficiencyRow(rank: 2, team: .previewOpponent, wins: 26, losses: 15,
                              values: ["off_rtg": .double(119.6), "def_rtg": .double(112.5), "net_rtg": .double(7.1), "pace": .double(97.2)],
                              ranks: ["off_rtg": .int(1), "def_rtg": .int(9), "net_rtg": .int(2)])
        ]
    )
}

// MARK: - career_arc

/// One season of a `career_arc`.
public struct CareerSeason: Codable, Hashable, Sendable, Identifiable {
    public let season: String
    public let seasonType: String?
    public let age: Int?
    public let teamAbbr: String?
    public let gp: Int?
    public let value: Double?
    public let displayValue: String?
    public let availability: MetricAvailability

    public var id: String { "\(season)|\(seasonType ?? "")|\(teamAbbr ?? "")" }

    public init(season: String,
                seasonType: String? = nil,
                age: Int? = nil,
                teamAbbr: String? = nil,
                gp: Int? = nil,
                value: Double? = nil,
                displayValue: String? = nil,
                availability: MetricAvailability = .full) {
        self.season = season
        self.seasonType = seasonType
        self.age = age
        self.teamAbbr = teamAbbr
        self.gp = gp
        self.value = value
        self.displayValue = displayValue
        self.availability = availability
    }

    private enum CodingKeys: String, CodingKey {
        case season, seasonType, age, teamAbbr, gp, value, displayValue, availability
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        season = try container.decode(String.self, forKey: .season)
        seasonType = try container.decodeIfPresent(String.self, forKey: .seasonType)
        age = try container.decodeIfPresent(Int.self, forKey: .age)
        teamAbbr = try container.decodeIfPresent(String.self, forKey: .teamAbbr)
        gp = try container.decodeIfPresent(Int.self, forKey: .gp)
        value = try container.decodeIfPresent(Double.self, forKey: .value)
        displayValue = try container.decodeIfPresent(String.self, forKey: .displayValue)
        availability = MetricAvailability.decoded(from: container, forKey: .availability)
    }
}

/// The best season of a career arc.
public struct CareerPeak: Codable, Hashable, Sendable {
    public let season: String
    public let value: Double?
    public let displayValue: String?

    public init(season: String, value: Double? = nil, displayValue: String? = nil) {
        self.season = season
        self.value = value
        self.displayValue = displayValue
    }
}

/// `career_arc` (`contracts/CONTRACT.md` §4).
public struct CareerArcPayload: Codable, Hashable, Sendable {
    public let player: PlayerRef
    public let metric: MetricDescriptor
    /// `"season"` or `"age"`.
    public let xAxis: String
    public let seasons: [CareerSeason]
    public let playoffSeasons: [CareerSeason]
    public let eraBoundaries: [EraBoundary]
    public let peak: CareerPeak?

    public init(player: PlayerRef,
                metric: MetricDescriptor,
                xAxis: String = "season",
                seasons: [CareerSeason] = [],
                playoffSeasons: [CareerSeason] = [],
                eraBoundaries: [EraBoundary] = [],
                peak: CareerPeak? = nil) {
        self.player = player
        self.metric = metric
        self.xAxis = xAxis
        self.seasons = seasons
        self.playoffSeasons = playoffSeasons
        self.eraBoundaries = eraBoundaries
        self.peak = peak
    }

    private enum CodingKeys: String, CodingKey {
        case player, metric, xAxis, seasons, playoffSeasons, eraBoundaries, peak
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        player = try container.decode(PlayerRef.self, forKey: .player)
        metric = try container.decode(MetricDescriptor.self, forKey: .metric)
        xAxis = try container.decodeIfPresent(String.self, forKey: .xAxis) ?? "season"
        seasons = try container.decodeIfPresent([CareerSeason].self, forKey: .seasons) ?? []
        playoffSeasons = try container.decodeIfPresent([CareerSeason].self, forKey: .playoffSeasons) ?? []
        eraBoundaries = try container.decodeIfPresent([EraBoundary].self, forKey: .eraBoundaries) ?? []
        peak = try container.decodeIfPresent(CareerPeak.self, forKey: .peak)
    }

    public static let preview = CareerArcPayload(
        player: .preview,
        metric: MetricDescriptor(key: "per", name: "Player Efficiency Rating", shortName: "PER",
                                 category: "impact", format: .decimal1, higherIsBetter: true, scope: ["player"],
                                 availability: MetricDescriptor.Availability(seasonFrom: "1951-52", perGameFrom: "1951-52", seasonLevelOnly: true, estimatedBefore: "1996-97"),
                                 domain: MetricDescriptor.Domain(min: 0, max: 35),
                                 glossary: "Hollinger's per-minute rating, pace and league adjusted so that the league average is 15.00 every season."),
        xAxis: "season",
        seasons: [
            CareerSeason(season: "2003-04", seasonType: "Regular Season", age: 19, teamAbbr: "CLE", gp: 79, value: 18.3, displayValue: "18.3", availability: .full),
            CareerSeason(season: "2008-09", seasonType: "Regular Season", age: 24, teamAbbr: "CLE", gp: 81, value: 31.7, displayValue: "31.7", availability: .full),
            CareerSeason(season: "2012-13", seasonType: "Regular Season", age: 28, teamAbbr: "MIA", gp: 76, value: 31.6, displayValue: "31.6", availability: .full),
            CareerSeason(season: "2019-20", seasonType: "Regular Season", age: 35, teamAbbr: "LAL", gp: 67, value: 25.4, displayValue: "25.4", availability: .full),
            CareerSeason(season: "2025-26", seasonType: "Regular Season", age: 41, teamAbbr: "LAL", gp: 41, value: 21.2, displayValue: "21.2", availability: .full)
        ],
        playoffSeasons: [],
        eraBoundaries: [EraBoundary(season: "1996-97", label: "Advanced box scores and play-by-play", detail: nil)],
        peak: CareerPeak(season: "2008-09", value: 31.7, displayValue: "31.7")
    )
}

// MARK: - The umbrella

/// Every widget payload, tagged by the kind that produced it.
///
/// The resolve response carries `payload` as an untyped object, so it is decoded through
/// `decode(kind:from:key:)` using the result's own `kind`.
public enum WidgetPayload: Hashable, Sendable {
    case statTile(StatTilePayload)
    case playerSnapshot(PlayerSnapshotPayload)
    case leaderboard(LeaderboardPayload)
    case gameLog(GameLogPayload)
    case trendChart(TrendChartPayload)
    case fourFactors(FourFactorsPayload)
    case shotProfile(ShotProfilePayload)
    case comparison(ComparisonPayload)
    case scoreboard(ScoreboardPayload)
    case dailyMovers(DailyMoversPayload)
    case teamEfficiency(TeamEfficiencyPayload)
    case nextGameProjection(NextGameProjectionPayload)
    case projectionBoard(ProjectionBoardPayload)
    case fantasyDraftBoard(FantasyDraftBoardPayload)
    case fantasyTrade(FantasyTradePayload)
    case careerArc(CareerArcPayload)

    public var kind: WidgetKind {
        switch self {
        case .statTile: return .statTile
        case .playerSnapshot: return .playerSnapshot
        case .leaderboard: return .leaderboard
        case .gameLog: return .gameLog
        case .trendChart: return .trendChart
        case .fourFactors: return .fourFactors
        case .shotProfile: return .shotProfile
        case .comparison: return .comparison
        case .scoreboard: return .scoreboard
        case .dailyMovers: return .dailyMovers
        case .teamEfficiency: return .teamEfficiency
        case .nextGameProjection: return .nextGameProjection
        case .projectionBoard: return .projectionBoard
        case .fantasyDraftBoard: return .fantasyDraftBoard
        case .fantasyTrade: return .fantasyTrade
        case .careerArc: return .careerArc
        }
    }

    /// Decodes the untyped `payload` object from a resolve result into the right case.
    public static func decode(kind: WidgetKind, from decoder: Decoder, key: CodingKey) throws -> WidgetPayload {
        let container = try decoder.container(keyedBy: AnyCodingKey.self)
        let payloadKey = AnyCodingKey(key)
        switch kind {
        case .statTile:
            return .statTile(try container.decode(StatTilePayload.self, forKey: payloadKey))
        case .playerSnapshot:
            return .playerSnapshot(try container.decode(PlayerSnapshotPayload.self, forKey: payloadKey))
        case .leaderboard:
            return .leaderboard(try container.decode(LeaderboardPayload.self, forKey: payloadKey))
        case .gameLog:
            return .gameLog(try container.decode(GameLogPayload.self, forKey: payloadKey))
        case .trendChart:
            return .trendChart(try container.decode(TrendChartPayload.self, forKey: payloadKey))
        case .fourFactors:
            return .fourFactors(try container.decode(FourFactorsPayload.self, forKey: payloadKey))
        case .shotProfile:
            return .shotProfile(try container.decode(ShotProfilePayload.self, forKey: payloadKey))
        case .comparison:
            return .comparison(try container.decode(ComparisonPayload.self, forKey: payloadKey))
        case .scoreboard:
            return .scoreboard(try container.decode(ScoreboardPayload.self, forKey: payloadKey))
        case .dailyMovers:
            return .dailyMovers(try container.decode(DailyMoversPayload.self, forKey: payloadKey))
        case .teamEfficiency:
            return .teamEfficiency(try container.decode(TeamEfficiencyPayload.self, forKey: payloadKey))
        case .nextGameProjection:
            return .nextGameProjection(try container.decode(NextGameProjectionPayload.self, forKey: payloadKey))
        case .projectionBoard:
            return .projectionBoard(try container.decode(ProjectionBoardPayload.self, forKey: payloadKey))
        case .fantasyDraftBoard:
            return .fantasyDraftBoard(try container.decode(FantasyDraftBoardPayload.self, forKey: payloadKey))
        case .fantasyTrade:
            return .fantasyTrade(try container.decode(FantasyTradePayload.self, forKey: payloadKey))
        case .careerArc:
            return .careerArc(try container.decode(CareerArcPayload.self, forKey: payloadKey))
        }
    }

    /// Decodes a bare payload document — a golden fixture file, for instance — with no envelope.
    public static func decode(kind: WidgetKind, from data: Data, using decoder: JSONDecoder = JSONDecoder()) throws -> WidgetPayload {
        switch kind {
        case .statTile:
            return .statTile(try decoder.decode(StatTilePayload.self, from: data))
        case .playerSnapshot:
            return .playerSnapshot(try decoder.decode(PlayerSnapshotPayload.self, from: data))
        case .leaderboard:
            return .leaderboard(try decoder.decode(LeaderboardPayload.self, from: data))
        case .gameLog:
            return .gameLog(try decoder.decode(GameLogPayload.self, from: data))
        case .trendChart:
            return .trendChart(try decoder.decode(TrendChartPayload.self, from: data))
        case .fourFactors:
            return .fourFactors(try decoder.decode(FourFactorsPayload.self, from: data))
        case .shotProfile:
            return .shotProfile(try decoder.decode(ShotProfilePayload.self, from: data))
        case .comparison:
            return .comparison(try decoder.decode(ComparisonPayload.self, from: data))
        case .scoreboard:
            return .scoreboard(try decoder.decode(ScoreboardPayload.self, from: data))
        case .dailyMovers:
            return .dailyMovers(try decoder.decode(DailyMoversPayload.self, from: data))
        case .teamEfficiency:
            return .teamEfficiency(try decoder.decode(TeamEfficiencyPayload.self, from: data))
        case .nextGameProjection:
            return .nextGameProjection(try decoder.decode(NextGameProjectionPayload.self, from: data))
        case .projectionBoard:
            return .projectionBoard(try decoder.decode(ProjectionBoardPayload.self, from: data))
        case .fantasyDraftBoard:
            return .fantasyDraftBoard(try decoder.decode(FantasyDraftBoardPayload.self, from: data))
        case .fantasyTrade:
            return .fantasyTrade(try decoder.decode(FantasyTradePayload.self, from: data))
        case .careerArc:
            return .careerArc(try decoder.decode(CareerArcPayload.self, from: data))
        }
    }

    /// A plausible payload for previews and for tests that do not want a fixture file.
    public static func preview(for kind: WidgetKind) -> WidgetPayload {
        switch kind {
        case .statTile: return .statTile(.preview)
        case .playerSnapshot: return .playerSnapshot(.preview)
        case .leaderboard: return .leaderboard(.preview)
        case .gameLog: return .gameLog(.preview)
        case .trendChart: return .trendChart(.preview)
        case .fourFactors: return .fourFactors(.preview)
        case .shotProfile: return .shotProfile(.preview)
        case .comparison: return .comparison(.preview)
        case .scoreboard: return .scoreboard(.preview)
        case .dailyMovers: return .dailyMovers(.preview)
        case .teamEfficiency: return .teamEfficiency(.preview)
        case .nextGameProjection: return .nextGameProjection(.preview)
        case .projectionBoard: return .projectionBoard(.preview)
        case .fantasyDraftBoard: return .fantasyDraftBoard(.preview)
        case .fantasyTrade: return .fantasyTrade(.preview)
        case .careerArc: return .careerArc(.preview)
        }
    }
}

extension WidgetPayload: Encodable {
    /// Encodes the wrapped payload unwrapped, which is the shape the API uses and the shape the
    /// disk cache stores.
    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .statTile(let payload): try container.encode(payload)
        case .playerSnapshot(let payload): try container.encode(payload)
        case .leaderboard(let payload): try container.encode(payload)
        case .gameLog(let payload): try container.encode(payload)
        case .trendChart(let payload): try container.encode(payload)
        case .fourFactors(let payload): try container.encode(payload)
        case .shotProfile(let payload): try container.encode(payload)
        case .comparison(let payload): try container.encode(payload)
        case .scoreboard(let payload): try container.encode(payload)
        case .dailyMovers(let payload): try container.encode(payload)
        case .teamEfficiency(let payload): try container.encode(payload)
        case .nextGameProjection(let payload): try container.encode(payload)
        case .projectionBoard(let payload): try container.encode(payload)
        case .fantasyDraftBoard(let payload): try container.encode(payload)
        case .fantasyTrade(let payload): try container.encode(payload)
        case .careerArc(let payload): try container.encode(payload)
        }
    }
}
