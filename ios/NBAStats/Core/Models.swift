import Foundation

// MARK: - Subjects

/// A player as every endpoint returns them (`contracts/CONTRACT.md` §2).
public struct PlayerRef: Codable, Hashable, Sendable, Identifiable {
    public let playerId: PlayerID
    public let name: String
    public let firstName: String?
    public let lastName: String?
    public let teamId: TeamID?
    public let teamAbbr: String?
    public let position: String?
    public let jersey: String?
    public let headshotUrl: String?
    public let isActive: Bool?

    public var id: PlayerID { playerId }

    public init(playerId: PlayerID,
                name: String,
                firstName: String? = nil,
                lastName: String? = nil,
                teamId: TeamID? = nil,
                teamAbbr: String? = nil,
                position: String? = nil,
                jersey: String? = nil,
                headshotUrl: String? = nil,
                isActive: Bool? = nil) {
        self.playerId = playerId
        self.name = name
        self.firstName = firstName
        self.lastName = lastName
        self.teamId = teamId
        self.teamAbbr = teamAbbr
        self.position = position
        self.jersey = jersey
        self.headshotUrl = headshotUrl
        self.isActive = isActive
    }

    /// `"L. James"` when the name parts are known, otherwise the full name.
    public var shortName: String {
        guard let lastName = lastName, let initial = firstName?.first else { return name }
        return "\(initial). \(lastName)"
    }

    /// The two-letter-ish initials used by the avatar placeholder.
    public var initials: String {
        let parts = name.split(separator: " ")
        let letters = parts.prefix(2).compactMap { $0.first }
        return letters.isEmpty ? "?" : String(letters)
    }

    public static let preview = PlayerRef(
        playerId: 2544,
        name: "LeBron James",
        firstName: "LeBron",
        lastName: "James",
        teamId: 1_610_612_747,
        teamAbbr: "LAL",
        position: "F",
        jersey: "23",
        headshotUrl: nil,
        isActive: true
    )

    public static let previewSecondary = PlayerRef(
        playerId: 1_629_029,
        name: "Luka Doncic",
        firstName: "Luka",
        lastName: "Doncic",
        teamId: 1_610_612_747,
        teamAbbr: "LAL",
        position: "G",
        jersey: "77",
        headshotUrl: nil,
        isActive: true
    )
}

/// A franchise as every endpoint returns them (`contracts/CONTRACT.md` §2).
public struct TeamRef: Codable, Hashable, Sendable, Identifiable {
    public let teamId: TeamID
    public let abbr: String
    public let name: String
    public let city: String?
    public let nickname: String?
    public let conference: String?
    public let division: String?

    public var id: TeamID { teamId }

    public init(teamId: TeamID,
                abbr: String,
                name: String,
                city: String? = nil,
                nickname: String? = nil,
                conference: String? = nil,
                division: String? = nil) {
        self.teamId = teamId
        self.abbr = abbr
        self.name = name
        self.city = city
        self.nickname = nickname
        self.conference = conference
        self.division = division
    }

    public static let preview = TeamRef(
        teamId: 1_610_612_747,
        abbr: "LAL",
        name: "Los Angeles Lakers",
        city: "Los Angeles",
        nickname: "Lakers",
        conference: "West",
        division: "Pacific"
    )

    public static let previewOpponent = TeamRef(
        teamId: 1_610_612_738,
        abbr: "BOS",
        name: "Boston Celtics",
        city: "Boston",
        nickname: "Celtics",
        conference: "East",
        division: "Atlantic"
    )
}

// MARK: - Metrics

/// How much a number can be trusted for the era it comes from (`contracts/CONTRACT.md` §6).
public enum MetricAvailability: String, Codable, Hashable, Sendable, CaseIterable {
    case full, estimated, partial, unavailable

    /// True when the value may be compared against modern numbers without a caveat.
    public var isTrustworthy: Bool { self == .full }

    /// The badge text a tile shows next to the value; `nil` when nothing needs saying.
    public var badgeText: String? {
        switch self {
        case .full: return nil
        case .estimated: return "est."
        case .partial: return "partial"
        case .unavailable: return nil
        }
    }

    /// The sentence shown when the reader taps the badge.
    public var explanation: String {
        switch self {
        case .full:
            return "Measured from official league records for this era."
        case .estimated:
            return "Derived from box-score formulas rather than possession data, which the league did not publish before 1996-97."
        case .partial:
            return "Some of the games behind this number are missing the inputs it needs."
        case .unavailable:
            return "The league did not record this stat in this era, so there is no number to show."
        }
    }
}

/// The atom every widget renders (`contracts/CONTRACT.md` §2).
///
/// `value` is the raw number in the metric's native unit — percentages are fractions in `[0, 1]`,
/// never 0-100 — and `displayValue` is the server-formatted string the client shows verbatim
/// unless it has a reason to reformat.
public struct MetricValue: Codable, Hashable, Sendable, Identifiable {
    public let metric: String
    public let value: Double?
    public let displayValue: String
    public let rank: Int?
    public let percentile: Double?
    public let leagueAverage: Double?
    public let delta: Double?
    public let isEstimated: Bool?
    public let availability: MetricAvailability

    public var id: String { metric }

    /// True when there is no number to render, so the view must show an em dash and not a zero.
    public var isMissing: Bool { value == nil || availability == .unavailable }

    public init(metric: String,
                value: Double?,
                displayValue: String,
                rank: Int? = nil,
                percentile: Double? = nil,
                leagueAverage: Double? = nil,
                delta: Double? = nil,
                isEstimated: Bool? = nil,
                availability: MetricAvailability = .full) {
        self.metric = metric
        self.value = value
        self.displayValue = displayValue
        self.rank = rank
        self.percentile = percentile
        self.leagueAverage = leagueAverage
        self.delta = delta
        self.isEstimated = isEstimated
        self.availability = availability
    }

    private enum CodingKeys: String, CodingKey {
        case metric, value, displayValue, rank, percentile, leagueAverage, delta, isEstimated, availability
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        metric = try container.decode(String.self, forKey: .metric)
        value = try container.decodeIfPresent(Double.self, forKey: .value)
        displayValue = try container.decodeIfPresent(String.self, forKey: .displayValue) ?? Formatting.emDash
        rank = try container.decodeIfPresent(Int.self, forKey: .rank)
        percentile = try container.decodeIfPresent(Double.self, forKey: .percentile)
        leagueAverage = try container.decodeIfPresent(Double.self, forKey: .leagueAverage)
        delta = try container.decodeIfPresent(Double.self, forKey: .delta)
        isEstimated = try container.decodeIfPresent(Bool.self, forKey: .isEstimated)
        availability = try container.decodeIfPresent(MetricAvailability.self, forKey: .availability) ?? .full
    }

    public static let preview = MetricValue(
        metric: "ts_pct",
        value: 0.6153,
        displayValue: "61.5%",
        rank: 12,
        percentile: 0.93,
        leagueAverage: 0.5671,
        delta: 0.0482,
        isEstimated: false,
        availability: .full
    )

    /// A value the era never recorded, for previewing the em-dash treatment.
    public static let previewUnavailable = MetricValue(
        metric: "usg_pct",
        value: nil,
        displayValue: Formatting.emDash,
        rank: nil,
        percentile: nil,
        leagueAverage: nil,
        delta: nil,
        isEstimated: nil,
        availability: .unavailable
    )
}

/// How a metric is rendered (`contracts/metrics.json#/formats`).
public enum MetricFormat: String, Codable, Hashable, Sendable, CaseIterable {
    case integer, decimal1, decimal2, percent1, percent2, rating1, plusMinus1, minutes

    /// Number of digits after the decimal separator.
    public var decimals: Int {
        switch self {
        case .integer: return 0
        case .decimal1, .percent1, .rating1, .plusMinus1, .minutes: return 1
        case .decimal2, .percent2: return 2
        }
    }

    /// True for the two formats whose raw value is a fraction in `[0, 1]`.
    public var isPercentage: Bool { self == .percent1 || self == .percent2 }
}

/// One entry of `contracts/metrics.json#/metrics`.
public struct MetricDescriptor: Codable, Hashable, Sendable, Identifiable {

    /// The first season each form of the metric exists, per `contracts/CONTRACT.md` §6.
    public struct Availability: Codable, Hashable, Sendable {
        public let seasonFrom: String
        public let perGameFrom: String
        public let seasonLevelOnly: Bool
        public let estimatedBefore: String?

        public init(seasonFrom: String, perGameFrom: String, seasonLevelOnly: Bool, estimatedBefore: String? = nil) {
            self.seasonFrom = seasonFrom
            self.perGameFrom = perGameFrom
            self.seasonLevelOnly = seasonLevelOnly
            self.estimatedBefore = estimatedBefore
        }
    }

    /// The plausible range of the metric, used to scale bars and chart axes.
    public struct Domain: Codable, Hashable, Sendable {
        public let min: Double
        public let max: Double

        public init(min: Double, max: Double) {
            self.min = min
            self.max = max
        }

        public var span: Double { self.max - self.min }
    }

    public let key: String
    public let name: String
    public let shortName: String
    public let category: String
    public let format: MetricFormat
    public let higherIsBetter: Bool
    public let scope: [String]
    public let availability: Availability
    public let domain: Domain?
    public let glossary: String

    public var id: String { key }

    public init(key: String,
                name: String,
                shortName: String,
                category: String,
                format: MetricFormat,
                higherIsBetter: Bool,
                scope: [String],
                availability: Availability,
                domain: Domain? = nil,
                glossary: String) {
        self.key = key
        self.name = name
        self.shortName = shortName
        self.category = category
        self.format = format
        self.higherIsBetter = higherIsBetter
        self.scope = scope
        self.availability = availability
        self.domain = domain
        self.glossary = glossary
    }

    /// True when the metric can describe this kind of subject (`"player"` or `"team"`).
    public func appliesTo(_ subjectType: String) -> Bool { scope.contains(subjectType) }

    public static let preview = MetricDescriptor(
        key: "ts_pct",
        name: "True Shooting %",
        shortName: "TS%",
        category: "shooting",
        format: .percent1,
        higherIsBetter: true,
        scope: ["player", "team"],
        availability: Availability(seasonFrom: "1946-47", perGameFrom: "1996-97", seasonLevelOnly: false, estimatedBefore: nil),
        domain: Domain(min: 0.35, max: 0.75),
        glossary: "PTS / (2 * (FGA + 0.44*FTA)). Shooting efficiency including free throws."
    )

    public static let previewRating = MetricDescriptor(
        key: "net_rtg",
        name: "Net Rating",
        shortName: "NetRtg",
        category: "efficiency",
        format: .rating1,
        higherIsBetter: true,
        scope: ["player", "team"],
        availability: Availability(seasonFrom: "1996-97", perGameFrom: "1996-97", seasonLevelOnly: false, estimatedBefore: nil),
        domain: Domain(min: -25, max: 25),
        glossary: "Offensive Rating minus Defensive Rating."
    )

    public static let previewImpact = MetricDescriptor(
        key: "game_score",
        name: "Game Score",
        shortName: "GmSc",
        category: "impact",
        format: .decimal1,
        higherIsBetter: true,
        scope: ["player"],
        availability: Availability(seasonFrom: "1973-74", perGameFrom: "1973-74", seasonLevelOnly: false, estimatedBefore: nil),
        domain: nil,
        glossary: "Hollinger's single-game rating."
    )
}

// MARK: - Games

/// Where a game is in its life (`contracts/CONTRACT.md` §2).
public enum GameStatus: String, Codable, Hashable, Sendable, CaseIterable {
    case scheduled, live, final

    public var isFinal: Bool { self == .final }
}

/// One game as every endpoint returns it (`contracts/CONTRACT.md` §2).
public struct GameRef: Codable, Hashable, Sendable, Identifiable {
    public let gameId: GameID
    /// ISO-8601 calendar date in US Eastern, the league's scheduling day.
    public let date: String
    public let season: String?
    public let seasonType: String?
    public let home: TeamRef
    public let away: TeamRef
    public let homePts: Int?
    public let awayPts: Int?
    public let status: GameStatus
    public let period: Int?
    public let clock: String?
    public let finalizedAt: String?

    public var id: GameID { gameId }

    public init(gameId: GameID,
                date: String,
                season: String? = nil,
                seasonType: String? = nil,
                home: TeamRef,
                away: TeamRef,
                homePts: Int? = nil,
                awayPts: Int? = nil,
                status: GameStatus,
                period: Int? = nil,
                clock: String? = nil,
                finalizedAt: String? = nil) {
        self.gameId = gameId
        self.date = date
        self.season = season
        self.seasonType = seasonType
        self.home = home
        self.away = away
        self.homePts = homePts
        self.awayPts = awayPts
        self.status = status
        self.period = period
        self.clock = clock
        self.finalizedAt = finalizedAt
    }

    /// `"118-112"` once there is a score, otherwise `nil`.
    public var scoreLine: String? {
        guard let homePts = homePts, let awayPts = awayPts else { return nil }
        return "\(awayPts)-\(homePts)"
    }

    /// The winning team once the game is final.
    public var winner: TeamRef? {
        guard status == .final, let homePts = homePts, let awayPts = awayPts else { return nil }
        if homePts == awayPts { return nil }
        return homePts > awayPts ? home : away
    }

    public static let preview = GameRef(
        gameId: "0022500512",
        date: "2026-01-02",
        season: "2025-26",
        seasonType: "Regular Season",
        home: .preview,
        away: .previewOpponent,
        homePts: 118,
        awayPts: 112,
        status: .final,
        period: 4,
        clock: nil,
        finalizedAt: "2026-01-03T02:41:07Z"
    )
}

// MARK: - GET /v1/health

/// `GET /v1/health` (`contracts/CONTRACT.md` §3).
public struct HealthResponse: Codable, Hashable, Sendable {
    public let status: String
    public let version: String?
    public let syncVersion: Int?
    public let dataThrough: String?
    public let databaseReady: Bool?
    public let seededDemoData: Bool?

    public init(status: String,
                version: String? = nil,
                syncVersion: Int? = nil,
                dataThrough: String? = nil,
                databaseReady: Bool? = nil,
                seededDemoData: Bool? = nil) {
        self.status = status
        self.version = version
        self.syncVersion = syncVersion
        self.dataThrough = dataThrough
        self.databaseReady = databaseReady
        self.seededDemoData = seededDemoData
    }

    public var isHealthy: Bool { status == "ok" && databaseReady != false }
}

// MARK: - GET /v1/meta

/// One season the service can answer questions about.
public struct SeasonInfo: Codable, Hashable, Sendable, Identifiable {
    public let season: String
    public let seasonTypes: [String]
    public let isCurrent: Bool?
    public let hasAdvanced: Bool?
    public let gameCount: Int?

    public var id: String { season }

    public init(season: String,
                seasonTypes: [String] = [],
                isCurrent: Bool? = nil,
                hasAdvanced: Bool? = nil,
                gameCount: Int? = nil) {
        self.season = season
        self.seasonTypes = seasonTypes
        self.isCurrent = isCurrent
        self.hasAdvanced = hasAdvanced
        self.gameCount = gameCount
    }

    private enum CodingKeys: String, CodingKey {
        case season, seasonTypes, isCurrent, hasAdvanced, gameCount
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        season = try container.decode(String.self, forKey: .season)
        seasonTypes = try container.decodeIfPresent([String].self, forKey: .seasonTypes) ?? []
        isCurrent = try container.decodeIfPresent(Bool.self, forKey: .isCurrent)
        hasAdvanced = try container.decodeIfPresent(Bool.self, forKey: .hasAdvanced)
        gameCount = try container.decodeIfPresent(Int.self, forKey: .gameCount)
    }
}

/// The first season each family of stats exists for, echoed from `metrics.json#/eraBoundaries`.
public struct Coverage: Codable, Hashable, Sendable {
    public let advancedFrom: String?
    public let trackingFrom: String?
    public let hustleFrom: String?
    public let shotChartsFrom: String?
    public let seasonFrom: String?

    public init(advancedFrom: String? = nil,
                trackingFrom: String? = nil,
                hustleFrom: String? = nil,
                shotChartsFrom: String? = nil,
                seasonFrom: String? = nil) {
        self.advancedFrom = advancedFrom
        self.trackingFrom = trackingFrom
        self.hustleFrom = hustleFrom
        self.shotChartsFrom = shotChartsFrom
        self.seasonFrom = seasonFrom
    }
}

/// `GET /v1/meta` (`contracts/CONTRACT.md` §3): league state plus the three catalogs, so a
/// server-side catalog change reaches clients without an App Store release.
public struct MetaResponse: Codable, Hashable, Sendable {
    public let syncVersion: Int?
    public let dataThrough: String?
    public let currentSeason: String?
    public let seasons: [SeasonInfo]
    public let metrics: MetricCatalogDocument?
    public let widgets: WidgetCatalogDocument?
    public let teams: [TeamRef]
    public let coverage: Coverage?
    public let attribution: String?

    public init(syncVersion: Int? = nil,
                dataThrough: String? = nil,
                currentSeason: String? = nil,
                seasons: [SeasonInfo] = [],
                metrics: MetricCatalogDocument? = nil,
                widgets: WidgetCatalogDocument? = nil,
                teams: [TeamRef] = [],
                coverage: Coverage? = nil,
                attribution: String? = nil) {
        self.syncVersion = syncVersion
        self.dataThrough = dataThrough
        self.currentSeason = currentSeason
        self.seasons = seasons
        self.metrics = metrics
        self.widgets = widgets
        self.teams = teams
        self.coverage = coverage
        self.attribution = attribution
    }

    private enum CodingKeys: String, CodingKey {
        case syncVersion, dataThrough, currentSeason, seasons, metrics, widgets, teams, coverage, attribution
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        syncVersion = try container.decodeIfPresent(Int.self, forKey: .syncVersion)
        dataThrough = try container.decodeIfPresent(String.self, forKey: .dataThrough)
        currentSeason = try container.decodeIfPresent(String.self, forKey: .currentSeason)
        seasons = try container.decodeIfPresent([SeasonInfo].self, forKey: .seasons) ?? []
        // A catalog the app cannot read must not cost it the rest of the response.
        metrics = try? container.decodeIfPresent(MetricCatalogDocument.self, forKey: .metrics)
        widgets = try? container.decodeIfPresent(WidgetCatalogDocument.self, forKey: .widgets)
        teams = try container.decodeIfPresent([TeamRef].self, forKey: .teams) ?? []
        coverage = try container.decodeIfPresent(Coverage.self, forKey: .coverage)
        attribution = try container.decodeIfPresent(String.self, forKey: .attribution)
    }
}

// MARK: - GET /v1/presets

/// A `$`-prefixed token a preset uses in place of a concrete subject id.
public struct SubjectToken: Codable, Hashable, Sendable, Identifiable {
    public let token: String
    public let summary: String?

    public var id: String { token }

    public init(token: String, summary: String? = nil) {
        self.token = token
        self.summary = summary
    }
}

/// `GET /v1/presets` (`contracts/CONTRACT.md` §3).
public struct PresetsResponse: Codable, Hashable, Sendable {
    public let schemaVersion: Int?
    public let version: Int?
    public let presets: [DashboardLayout]
    public let subjectTokens: [SubjectToken]

    public init(schemaVersion: Int? = nil,
                version: Int? = nil,
                presets: [DashboardLayout] = [],
                subjectTokens: [SubjectToken] = []) {
        self.schemaVersion = schemaVersion
        self.version = version
        self.presets = presets
        self.subjectTokens = subjectTokens
    }

    private enum CodingKeys: String, CodingKey {
        case schemaVersion, version, presets, subjectTokens
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decodeIfPresent(Int.self, forKey: .schemaVersion)
        version = try container.decodeIfPresent(Int.self, forKey: .version)
        presets = try container.decodeIfPresent([DashboardLayout].self, forKey: .presets) ?? []
        subjectTokens = try container.decodeIfPresent([SubjectToken].self, forKey: .subjectTokens) ?? []
    }
}

// MARK: - GET /v1/sync

/// `GET /v1/sync` (`contracts/CONTRACT.md` §3). When nothing changed the body is small, so every
/// collection here tolerates being absent.
public struct SyncResponse: Codable, Hashable, Sendable {
    public let syncVersion: Int
    public let previousVersion: Int?
    public let serverTime: String?
    public let dataThrough: String?
    public let hasChanges: Bool
    public let changedDates: [String]
    public let finalizedGames: [GameRef]
    public let affectedPlayerIds: [PlayerID]
    /// Widget **kinds** whose cached payloads must be dropped. Kept as raw strings so a kind this
    /// build does not know about cannot fail the whole response.
    public let invalidate: [String]
    public let nextPollAfterSeconds: Int?

    public init(syncVersion: Int,
                previousVersion: Int? = nil,
                serverTime: String? = nil,
                dataThrough: String? = nil,
                hasChanges: Bool = false,
                changedDates: [String] = [],
                finalizedGames: [GameRef] = [],
                affectedPlayerIds: [PlayerID] = [],
                invalidate: [String] = [],
                nextPollAfterSeconds: Int? = nil) {
        self.syncVersion = syncVersion
        self.previousVersion = previousVersion
        self.serverTime = serverTime
        self.dataThrough = dataThrough
        self.hasChanges = hasChanges
        self.changedDates = changedDates
        self.finalizedGames = finalizedGames
        self.affectedPlayerIds = affectedPlayerIds
        self.invalidate = invalidate
        self.nextPollAfterSeconds = nextPollAfterSeconds
    }

    /// The subset of `invalidate` this build understands.
    public var invalidatedKinds: [WidgetKind] {
        invalidate.compactMap { WidgetKind(rawValue: $0) }
    }

    private enum CodingKeys: String, CodingKey {
        case syncVersion, previousVersion, serverTime, dataThrough, hasChanges
        case changedDates, finalizedGames, affectedPlayerIds, invalidate, nextPollAfterSeconds
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        syncVersion = try container.decodeIfPresent(Int.self, forKey: .syncVersion) ?? 0
        previousVersion = try container.decodeIfPresent(Int.self, forKey: .previousVersion)
        serverTime = try container.decodeIfPresent(String.self, forKey: .serverTime)
        dataThrough = try container.decodeIfPresent(String.self, forKey: .dataThrough)
        hasChanges = try container.decodeIfPresent(Bool.self, forKey: .hasChanges) ?? false
        changedDates = try container.decodeIfPresent([String].self, forKey: .changedDates) ?? []
        finalizedGames = try container.decodeIfPresent([GameRef].self, forKey: .finalizedGames) ?? []
        affectedPlayerIds = try container.decodeIfPresent([PlayerID].self, forKey: .affectedPlayerIds) ?? []
        invalidate = try container.decodeIfPresent([String].self, forKey: .invalidate) ?? []
        nextPollAfterSeconds = try container.decodeIfPresent(Int.self, forKey: .nextPollAfterSeconds)
    }
}

// MARK: - GET /v1/players/search

/// One search hit: a `PlayerRef` flattened together with its ranking fields.
public struct PlayerSearchResult: Codable, Hashable, Sendable, Identifiable {
    public let player: PlayerRef
    public let fromYear: Int?
    public let toYear: Int?
    public let matchScore: Double?

    public var id: PlayerID { player.playerId }

    public init(player: PlayerRef, fromYear: Int? = nil, toYear: Int? = nil, matchScore: Double? = nil) {
        self.player = player
        self.fromYear = fromYear
        self.toYear = toYear
        self.matchScore = matchScore
    }

    /// `"2003 – 2026"`, the career span shown under the name.
    public var careerSpan: String? {
        guard let fromYear = fromYear else { return nil }
        guard let toYear = toYear else { return String(fromYear) }
        return "\(fromYear) – \(toYear)"
    }

    private enum CodingKeys: String, CodingKey {
        case fromYear, toYear, matchScore
    }

    public init(from decoder: Decoder) throws {
        player = try PlayerRef(from: decoder)
        let container = try decoder.container(keyedBy: CodingKeys.self)
        fromYear = try container.decodeIfPresent(Int.self, forKey: .fromYear)
        toYear = try container.decodeIfPresent(Int.self, forKey: .toYear)
        matchScore = try container.decodeIfPresent(Double.self, forKey: .matchScore)
    }

    public func encode(to encoder: Encoder) throws {
        try player.encode(to: encoder)
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encodeIfPresent(fromYear, forKey: .fromYear)
        try container.encodeIfPresent(toYear, forKey: .toYear)
        try container.encodeIfPresent(matchScore, forKey: .matchScore)
    }
}

/// `GET /v1/players/search` (`contracts/CONTRACT.md` §3).
public struct PlayerSearchResponse: Codable, Hashable, Sendable {
    public let query: String
    public let results: [PlayerSearchResult]
    public let nextCursor: String?

    public init(query: String, results: [PlayerSearchResult] = [], nextCursor: String? = nil) {
        self.query = query
        self.results = results
        self.nextCursor = nextCursor
    }

    private enum CodingKeys: String, CodingKey {
        case query, results, nextCursor
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        query = try container.decodeIfPresent(String.self, forKey: .query) ?? ""
        results = try container.decodeIfPresent([PlayerSearchResult].self, forKey: .results) ?? []
        nextCursor = try container.decodeIfPresent(String.self, forKey: .nextCursor)
    }
}

// MARK: - GET /v1/players/{playerId}

/// Where and when a player was drafted; every field is null for an undrafted player.
public struct DraftInfo: Codable, Hashable, Sendable {
    public let year: Int?
    public let round: Int?
    public let pick: Int?

    public init(year: Int? = nil, round: Int? = nil, pick: Int? = nil) {
        self.year = year
        self.round = round
        self.pick = pick
    }

    /// `"2003 · Round 1, Pick 1"`, or `"Undrafted"`.
    public var displayText: String {
        guard let year = year else { return "Undrafted" }
        guard let round = round, let pick = pick else { return String(year) }
        return "\(year) · Round \(round), Pick \(pick)"
    }
}

/// The biographical block of `GET /v1/players/{playerId}`.
public struct PlayerBio: Codable, Hashable, Sendable {
    public let height: String?
    public let weight: Int?
    public let birthdate: String?
    public let country: String?
    public let draft: DraftInfo?
    public let school: String?
    public let fromYear: Int?
    public let toYear: Int?
    public let bbrefSlug: String?

    public init(height: String? = nil,
                weight: Int? = nil,
                birthdate: String? = nil,
                country: String? = nil,
                draft: DraftInfo? = nil,
                school: String? = nil,
                fromYear: Int? = nil,
                toYear: Int? = nil,
                bbrefSlug: String? = nil) {
        self.height = height
        self.weight = weight
        self.birthdate = birthdate
        self.country = country
        self.draft = draft
        self.school = school
        self.fromYear = fromYear
        self.toYear = toYear
        self.bbrefSlug = bbrefSlug
    }
}

/// One season (or the career total, whose `season` is `"Career"`) of a player's record.
///
/// `values` is keyed by metric key. A key whose metric did not exist in that era is present with
/// a JSON `null`, which arrives here as `JSONValue.null` and must render as an em dash.
public struct SeasonRow: Codable, Hashable, Sendable, Identifiable {
    public let season: String
    public let seasonType: String?
    public let teamAbbr: String?
    public let age: Int?
    public let gp: Int?
    public let values: [String: JSONValue]
    public let availability: MetricAvailability

    public var id: String { "\(season)|\(seasonType ?? "")|\(teamAbbr ?? "")" }

    public init(season: String,
                seasonType: String? = nil,
                teamAbbr: String? = nil,
                age: Int? = nil,
                gp: Int? = nil,
                values: [String: JSONValue] = [:],
                availability: MetricAvailability = .full) {
        self.season = season
        self.seasonType = seasonType
        self.teamAbbr = teamAbbr
        self.age = age
        self.gp = gp
        self.values = values
        self.availability = availability
    }

    /// The raw number for a metric key, or `nil` when the era did not record it.
    public func value(_ metric: String) -> Double? { values[metric]?.doubleValue }

    private enum CodingKeys: String, CodingKey {
        case season, seasonType, teamAbbr, age, gp, values, availability
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        season = try container.decode(String.self, forKey: .season)
        seasonType = try container.decodeIfPresent(String.self, forKey: .seasonType)
        teamAbbr = try container.decodeIfPresent(String.self, forKey: .teamAbbr)
        age = try container.decodeIfPresent(Int.self, forKey: .age)
        gp = try container.decodeIfPresent(Int.self, forKey: .gp)
        values = try container.decodeIfPresent([String: JSONValue].self, forKey: .values) ?? [:]
        availability = try container.decodeIfPresent(MetricAvailability.self, forKey: .availability) ?? .full
    }
}

/// `GET /v1/players/{playerId}` (`contracts/CONTRACT.md` §3).
public struct PlayerDetailResponse: Codable, Hashable, Sendable {
    public let player: PlayerRef
    public let bio: PlayerBio?
    public let careerTotals: SeasonRow?
    public let seasons: [SeasonRow]

    public init(player: PlayerRef, bio: PlayerBio? = nil, careerTotals: SeasonRow? = nil, seasons: [SeasonRow] = []) {
        self.player = player
        self.bio = bio
        self.careerTotals = careerTotals
        self.seasons = seasons
    }

    private enum CodingKeys: String, CodingKey {
        case player, bio, careerTotals, seasons
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        player = try container.decode(PlayerRef.self, forKey: .player)
        bio = try container.decodeIfPresent(PlayerBio.self, forKey: .bio)
        careerTotals = try container.decodeIfPresent(SeasonRow.self, forKey: .careerTotals)
        seasons = try container.decodeIfPresent([SeasonRow].self, forKey: .seasons) ?? []
    }
}

// MARK: - GET /v1/players/{playerId}/gamelog

/// One game of a player's log, newest first in the response.
public struct GameLogRow: Codable, Hashable, Sendable, Identifiable {
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

    /// `"vs BOS"` or `"@ BOS"`.
    public var matchupText: String {
        guard let opponentAbbr = opponentAbbr else { return Formatting.emDash }
        return (isHome ?? true) ? "vs \(opponentAbbr)" : "@ \(opponentAbbr)"
    }

    public func value(_ metric: String) -> Double? { values[metric]?.doubleValue }

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
        availability = try container.decodeIfPresent(MetricAvailability.self, forKey: .availability) ?? .full
    }
}

/// `GET /v1/players/{playerId}/gamelog` (`contracts/CONTRACT.md` §3).
public struct GameLogResponse: Codable, Hashable, Sendable {
    public let player: PlayerRef
    public let season: String?
    public let seasonType: String?
    public let rows: [GameLogRow]
    public let nextCursor: String?

    public init(player: PlayerRef,
                season: String? = nil,
                seasonType: String? = nil,
                rows: [GameLogRow] = [],
                nextCursor: String? = nil) {
        self.player = player
        self.season = season
        self.seasonType = seasonType
        self.rows = rows
        self.nextCursor = nextCursor
    }

    private enum CodingKeys: String, CodingKey {
        case player, season, seasonType, rows, nextCursor
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        player = try container.decode(PlayerRef.self, forKey: .player)
        season = try container.decodeIfPresent(String.self, forKey: .season)
        seasonType = try container.decodeIfPresent(String.self, forKey: .seasonType)
        rows = try container.decodeIfPresent([GameLogRow].self, forKey: .rows) ?? []
        nextCursor = try container.decodeIfPresent(String.self, forKey: .nextCursor)
    }
}

// MARK: - GET /v1/teams

/// `GET /v1/teams` (`contracts/CONTRACT.md` §3).
public struct TeamsResponse: Codable, Hashable, Sendable {
    public let teams: [TeamRef]

    public init(teams: [TeamRef] = []) {
        self.teams = teams
    }

    private enum CodingKeys: String, CodingKey {
        case teams
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        teams = try container.decodeIfPresent([TeamRef].self, forKey: .teams) ?? []
    }
}

/// A win-loss record.
public struct TeamRecord: Codable, Hashable, Sendable {
    public let wins: Int?
    public let losses: Int?

    public init(wins: Int? = nil, losses: Int? = nil) {
        self.wins = wins
        self.losses = losses
    }

    /// `"27-14"`.
    public var displayText: String {
        guard let wins = wins, let losses = losses else { return Formatting.emDash }
        return "\(wins)-\(losses)"
    }
}

/// One roster line: a `PlayerRef` flattened together with that player's values.
public struct TeamRosterEntry: Codable, Hashable, Sendable, Identifiable {
    public let player: PlayerRef
    public let values: [String: JSONValue]

    public var id: PlayerID { player.playerId }

    public init(player: PlayerRef, values: [String: JSONValue] = [:]) {
        self.player = player
        self.values = values
    }

    public func value(_ metric: String) -> Double? { values[metric]?.doubleValue }

    private enum CodingKeys: String, CodingKey {
        case values
    }

    public init(from decoder: Decoder) throws {
        player = try PlayerRef(from: decoder)
        let container = try decoder.container(keyedBy: CodingKeys.self)
        values = try container.decodeIfPresent([String: JSONValue].self, forKey: .values) ?? [:]
    }

    public func encode(to encoder: Encoder) throws {
        try player.encode(to: encoder)
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(values, forKey: .values)
    }
}

/// `GET /v1/teams/{teamId}` (`contracts/CONTRACT.md` §3).
public struct TeamDetailResponse: Codable, Hashable, Sendable {
    public let team: TeamRef
    public let season: String?
    public let seasonType: String?
    public let record: TeamRecord?
    public let values: [String: JSONValue]
    public let roster: [TeamRosterEntry]

    public init(team: TeamRef,
                season: String? = nil,
                seasonType: String? = nil,
                record: TeamRecord? = nil,
                values: [String: JSONValue] = [:],
                roster: [TeamRosterEntry] = []) {
        self.team = team
        self.season = season
        self.seasonType = seasonType
        self.record = record
        self.values = values
        self.roster = roster
    }

    public func value(_ metric: String) -> Double? { values[metric]?.doubleValue }

    private enum CodingKeys: String, CodingKey {
        case team, season, seasonType, record, values, roster
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        team = try container.decode(TeamRef.self, forKey: .team)
        season = try container.decodeIfPresent(String.self, forKey: .season)
        seasonType = try container.decodeIfPresent(String.self, forKey: .seasonType)
        record = try container.decodeIfPresent(TeamRecord.self, forKey: .record)
        values = try container.decodeIfPresent([String: JSONValue].self, forKey: .values) ?? [:]
        roster = try container.decodeIfPresent([TeamRosterEntry].self, forKey: .roster) ?? []
    }
}

// MARK: - GET /v1/games and /v1/games/{gameId}/box

/// `GET /v1/games` (`contracts/CONTRACT.md` §3).
public struct ScoreboardResponse: Codable, Hashable, Sendable {
    public let date: String?
    public let isLatestCompleted: Bool?
    public let games: [GameRef]
    public let nextCursor: String?

    public init(date: String? = nil,
                isLatestCompleted: Bool? = nil,
                games: [GameRef] = [],
                nextCursor: String? = nil) {
        self.date = date
        self.isLatestCompleted = isLatestCompleted
        self.games = games
        self.nextCursor = nextCursor
    }

    private enum CodingKeys: String, CodingKey {
        case date, isLatestCompleted, games, nextCursor
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        date = try container.decodeIfPresent(String.self, forKey: .date)
        isLatestCompleted = try container.decodeIfPresent(Bool.self, forKey: .isLatestCompleted)
        games = try container.decodeIfPresent([GameRef].self, forKey: .games) ?? []
        nextCursor = try container.decodeIfPresent(String.self, forKey: .nextCursor)
    }
}

/// One player's line in a box score.
public struct BoxScorePlayer: Codable, Hashable, Sendable, Identifiable {
    public let player: PlayerRef
    public let started: Bool?
    public let minutes: Double?
    public let values: [String: JSONValue]
    public let availability: MetricAvailability

    public var id: PlayerID { player.playerId }

    public init(player: PlayerRef,
                started: Bool? = nil,
                minutes: Double? = nil,
                values: [String: JSONValue] = [:],
                availability: MetricAvailability = .full) {
        self.player = player
        self.started = started
        self.minutes = minutes
        self.values = values
        self.availability = availability
    }

    public func value(_ metric: String) -> Double? { values[metric]?.doubleValue }

    private enum CodingKeys: String, CodingKey {
        case player, started, minutes, values, availability
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        player = try container.decode(PlayerRef.self, forKey: .player)
        started = try container.decodeIfPresent(Bool.self, forKey: .started)
        minutes = try container.decodeIfPresent(Double.self, forKey: .minutes)
        values = try container.decodeIfPresent([String: JSONValue].self, forKey: .values) ?? [:]
        availability = try container.decodeIfPresent(MetricAvailability.self, forKey: .availability) ?? .full
    }
}

/// One team's half of a box score.
public struct BoxScoreTeam: Codable, Hashable, Sendable, Identifiable {
    public let team: TeamRef
    public let values: [String: JSONValue]
    public let players: [BoxScorePlayer]

    public var id: TeamID { team.teamId }

    public init(team: TeamRef, values: [String: JSONValue] = [:], players: [BoxScorePlayer] = []) {
        self.team = team
        self.values = values
        self.players = players
    }

    public func value(_ metric: String) -> Double? { values[metric]?.doubleValue }

    private enum CodingKeys: String, CodingKey {
        case team, values, players
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        team = try container.decode(TeamRef.self, forKey: .team)
        values = try container.decodeIfPresent([String: JSONValue].self, forKey: .values) ?? [:]
        players = try container.decodeIfPresent([BoxScorePlayer].self, forKey: .players) ?? []
    }
}

/// `GET /v1/games/{gameId}/box` (`contracts/CONTRACT.md` §3).
public struct BoxScoreResponse: Codable, Hashable, Sendable {
    public let game: GameRef
    public let teams: [BoxScoreTeam]

    public init(game: GameRef, teams: [BoxScoreTeam] = []) {
        self.game = game
        self.teams = teams
    }

    private enum CodingKeys: String, CodingKey {
        case game, teams
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        game = try container.decode(GameRef.self, forKey: .game)
        teams = try container.decodeIfPresent([BoxScoreTeam].self, forKey: .teams) ?? []
    }
}

// MARK: - Errors

/// The body of an error envelope (`contracts/CONTRACT.md` §7).
public struct APIErrorBody: Codable, Hashable, Sendable {
    public let code: String
    public let message: String
    public let recoverable: Bool
    public let field: String?
    public let requestId: String?

    public init(code: String,
                message: String,
                recoverable: Bool = false,
                field: String? = nil,
                requestId: String? = nil) {
        self.code = code
        self.message = message
        self.recoverable = recoverable
        self.field = field
        self.requestId = requestId
    }

    private enum CodingKeys: String, CodingKey {
        case code, message, recoverable, field, requestId
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        code = try container.decodeIfPresent(String.self, forKey: .code) ?? "internal_error"
        message = try container.decodeIfPresent(String.self, forKey: .message) ?? "Something went wrong."
        recoverable = try container.decodeIfPresent(Bool.self, forKey: .recoverable) ?? false
        field = try container.decodeIfPresent(String.self, forKey: .field)
        requestId = try container.decodeIfPresent(String.self, forKey: .requestId)
    }
}

/// Every failing request answers with this envelope (`contracts/CONTRACT.md` §7).
public struct ErrorEnvelope: Codable, Hashable, Sendable {
    public let error: APIErrorBody

    public init(error: APIErrorBody) {
        self.error = error
    }
}

// MARK: - POST /v1/dashboard/resolve

/// The context sent with a resolve request, and the resolved context sent back with the response.
/// Requests fill in the favorites and the clock; responses fill in `season` as well.
public struct ResolveContextPayload: Codable, Hashable, Sendable {
    public let favoritePlayerId: PlayerID?
    public let favoriteTeamId: TeamID?
    public let timeZone: String?
    public let asOf: String?
    public let season: String?

    public init(favoritePlayerId: PlayerID? = nil,
                favoriteTeamId: TeamID? = nil,
                timeZone: String? = nil,
                asOf: String? = nil,
                season: String? = nil) {
        self.favoritePlayerId = favoritePlayerId
        self.favoriteTeamId = favoriteTeamId
        self.timeZone = timeZone
        self.asOf = asOf
        self.season = season
    }
}

/// One widget of a resolve request.
public struct ResolveWidgetRequest: Codable, Hashable, Sendable, Identifiable {
    public let id: String
    public let kind: WidgetKind
    public let size: WidgetSize
    public let config: [String: JSONValue]

    public init(id: String, kind: WidgetKind, size: WidgetSize, config: [String: JSONValue]) {
        self.id = id
        self.kind = kind
        self.size = size
        self.config = config
    }

    public init(widget: DashboardWidget) {
        self.init(id: widget.id, kind: widget.kind, size: widget.size, config: widget.config)
    }
}

/// `POST /v1/dashboard/resolve` request body (`contracts/CONTRACT.md` §3).
public struct DashboardResolveRequest: Codable, Hashable, Sendable {
    /// At most 24 widgets per request; more is `400 too_many_widgets`.
    public static let maxWidgets = 24

    public let layoutId: String
    public let context: ResolveContextPayload
    public let knownSyncVersion: Int?
    public let widgets: [ResolveWidgetRequest]

    public init(layoutId: String,
                context: ResolveContextPayload,
                knownSyncVersion: Int? = nil,
                widgets: [ResolveWidgetRequest]) {
        self.layoutId = layoutId
        self.context = context
        self.knownSyncVersion = knownSyncVersion
        self.widgets = widgets
    }
}

/// Per-widget outcome of a resolve (`contracts/CONTRACT.md` §3).
public enum ResolveStatus: String, Codable, Hashable, Sendable, CaseIterable {
    case ok, unchanged, partial, error
}

/// One widget's result inside a resolve response.
///
/// The `payload` object is untyped in JSON and is decoded here according to this result's own
/// `kind`. A kind this build does not know, or a payload it cannot read, degrades this one result:
/// `decodeError` is filled in and `payload` stays `nil`, so the rest of the dashboard still renders.
public struct ResolveResult: Codable, Hashable, Sendable, Identifiable {
    public let widgetId: String
    /// The raw `kind` string, kept verbatim so an unknown kind survives a round trip.
    public let kindRaw: String
    public let status: ResolveStatus
    public let payload: WidgetPayload?
    public let error: APIErrorBody?
    public let generatedAt: String?
    public let ttlSeconds: Int?
    public let availability: MetricAvailability?
    public let notes: [String]
    /// Why the payload could not be decoded, when that is what happened.
    public let decodeError: String?

    public var id: String { widgetId }

    /// The widget kind, when this build knows it.
    public var kind: WidgetKind? { WidgetKind(rawValue: kindRaw) }

    /// The status to act on: a payload that failed to decode counts as an error whatever the
    /// server said.
    public var effectiveStatus: ResolveStatus {
        decodeError == nil ? status : .error
    }

    /// The message to show in a failed tile.
    public var failureMessage: String? {
        if let decodeError = decodeError { return decodeError }
        if let error = error { return error.message }
        return status == .error ? "This widget could not be loaded." : nil
    }

    public init(widgetId: String,
                kindRaw: String,
                status: ResolveStatus,
                payload: WidgetPayload? = nil,
                error: APIErrorBody? = nil,
                generatedAt: String? = nil,
                ttlSeconds: Int? = nil,
                availability: MetricAvailability? = nil,
                notes: [String] = [],
                decodeError: String? = nil) {
        self.widgetId = widgetId
        self.kindRaw = kindRaw
        self.status = status
        self.payload = payload
        self.error = error
        self.generatedAt = generatedAt
        self.ttlSeconds = ttlSeconds
        self.availability = availability
        self.notes = notes
        self.decodeError = decodeError
    }

    private enum CodingKeys: String, CodingKey {
        case widgetId
        case kindRaw = "kind"
        case status, payload, error, generatedAt, ttlSeconds, availability, notes, decodeError
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        widgetId = try container.decode(String.self, forKey: .widgetId)
        let rawKind = try container.decodeIfPresent(String.self, forKey: .kindRaw) ?? ""
        kindRaw = rawKind
        let rawStatus = try container.decodeIfPresent(String.self, forKey: .status) ?? ResolveStatus.ok.rawValue
        let decodedStatus = ResolveStatus(rawValue: rawStatus) ?? .error
        error = try? container.decodeIfPresent(APIErrorBody.self, forKey: .error)
        generatedAt = try container.decodeIfPresent(String.self, forKey: .generatedAt)
        ttlSeconds = try container.decodeIfPresent(Int.self, forKey: .ttlSeconds)
        availability = try? container.decodeIfPresent(MetricAvailability.self, forKey: .availability)
        notes = try container.decodeIfPresent([String].self, forKey: .notes) ?? []

        var decodedPayload: WidgetPayload?
        var failure: String?
        let hasPayload: Bool
        if container.contains(.payload) {
            hasPayload = !((try? container.decodeNil(forKey: .payload)) ?? true)
        } else {
            hasPayload = false
        }
        if hasPayload {
            if let kind = WidgetKind(rawValue: rawKind) {
                do {
                    decodedPayload = try WidgetPayload.decode(kind: kind, from: decoder, key: CodingKeys.payload)
                } catch {
                    failure = "This widget's data could not be read (\(kind.rawValue))."
                }
            } else {
                failure = "This version of Hardwood does not know the widget type \"\(rawKind)\"."
            }
        } else if decodedStatus == .ok || decodedStatus == .partial {
            // A success with no payload is still a failure from the reader's point of view.
            if rawKind.isEmpty || WidgetKind(rawValue: rawKind) == nil {
                failure = "This version of Hardwood does not know the widget type \"\(rawKind)\"."
            }
        }
        payload = decodedPayload
        decodeError = try container.decodeIfPresent(String.self, forKey: .decodeError) ?? failure
        status = decodedStatus
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(widgetId, forKey: .widgetId)
        try container.encode(kindRaw, forKey: .kindRaw)
        try container.encode(status.rawValue, forKey: .status)
        try container.encodeIfPresent(payload, forKey: .payload)
        try container.encodeIfPresent(error, forKey: .error)
        try container.encodeIfPresent(generatedAt, forKey: .generatedAt)
        try container.encodeIfPresent(ttlSeconds, forKey: .ttlSeconds)
        try container.encodeIfPresent(availability, forKey: .availability)
        try container.encode(notes, forKey: .notes)
        try container.encodeIfPresent(decodeError, forKey: .decodeError)
    }
}

/// `POST /v1/dashboard/resolve` response body (`contracts/CONTRACT.md` §3).
public struct DashboardResolveResponse: Codable, Hashable, Sendable {
    public let syncVersion: Int?
    public let dataThrough: String?
    public let generatedAt: String?
    public let resolvedContext: ResolveContextPayload?
    public let results: [ResolveResult]

    public init(syncVersion: Int? = nil,
                dataThrough: String? = nil,
                generatedAt: String? = nil,
                resolvedContext: ResolveContextPayload? = nil,
                results: [ResolveResult] = []) {
        self.syncVersion = syncVersion
        self.dataThrough = dataThrough
        self.generatedAt = generatedAt
        self.resolvedContext = resolvedContext
        self.results = results
    }

    /// The results keyed by widget id, which is how the dashboard consumes them.
    public var resultsByWidgetID: [String: ResolveResult] {
        var out: [String: ResolveResult] = [:]
        for result in results { out[result.widgetId] = result }
        return out
    }

    private enum CodingKeys: String, CodingKey {
        case syncVersion, dataThrough, generatedAt, resolvedContext, results
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        syncVersion = try container.decodeIfPresent(Int.self, forKey: .syncVersion)
        dataThrough = try container.decodeIfPresent(String.self, forKey: .dataThrough)
        generatedAt = try container.decodeIfPresent(String.self, forKey: .generatedAt)
        resolvedContext = try container.decodeIfPresent(ResolveContextPayload.self, forKey: .resolvedContext)
        results = try container.decodeIfPresent([ResolveResult].self, forKey: .results) ?? []
    }
}

extension DashboardResolveRequest {
    /// Builds a request straight from a layout. The caller should hand in configuration that has
    /// already been through `Catalog.normalizedConfig(for:config:)`.
    public init(layout: DashboardLayout,
                context: ResolveContextPayload,
                knownSyncVersion: Int? = nil,
                configuredBy transform: ((DashboardWidget) -> [String: JSONValue])? = nil) {
        let requests = layout.widgets.prefix(DashboardResolveRequest.maxWidgets).map { widget -> ResolveWidgetRequest in
            let config = transform?(widget) ?? widget.config
            return ResolveWidgetRequest(id: widget.id, kind: widget.kind, size: widget.size, config: config)
        }
        self.init(layoutId: layout.id,
                  context: context,
                  knownSyncVersion: knownSyncVersion,
                  widgets: Array(requests))
    }
}
