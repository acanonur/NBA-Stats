import Foundation

// MARK: - Shared

/// One of the nine categories, in the order every screen shows them.
///
/// A raw string rather than an enum on the payload: a category the server adds later must not
/// throw away the row it appears in. `FantasyCategory.known` is the display vocabulary.
public enum FantasyCategory {
    public static let order: [String] = [
        "pts", "fg3m", "reb", "ast", "stl", "blk", "tov", "fg_pct", "ft_pct",
    ]

    /// Short labels for a nine-column header, where "3PM" has to fit beside "FG%".
    private static let short: [String: String] = [
        "pts": "PTS", "fg3m": "3PM", "reb": "REB", "ast": "AST", "stl": "STL",
        "blk": "BLK", "tov": "TO", "fg_pct": "FG%", "ft_pct": "FT%",
    ]

    private static let long: [String: String] = [
        "pts": "Points", "fg3m": "Threes", "reb": "Rebounds", "ast": "Assists",
        "stl": "Steals", "blk": "Blocks", "tov": "Turnovers",
        "fg_pct": "Field goal %", "ft_pct": "Free throw %",
    ]

    public static func shortLabel(_ key: String) -> String {
        short[key] ?? key.uppercased()
    }

    public static func label(_ key: String) -> String {
        long[key] ?? key
    }

    /// True where fewer is better, so a positive z means *few* turnovers.
    ///
    /// Every piece of wording that describes a category has to consult this. "Gains turnovers"
    /// is praise for the thing the category penalises, and the sign alone cannot tell you.
    public static func isNegative(_ key: String) -> Bool { key == "tov" }

    /// Percentage categories carry a volume-weighted impact rather than a plain value.
    public static func isPercentage(_ key: String) -> Bool {
        key == "fg_pct" || key == "ft_pct"
    }

    /// `"+1.2 rebounds"` / `"1.2 fewer turnovers"` — direction-aware.
    public static func changePhrase(_ key: String, z: Double) -> String {
        let magnitude = Formatting.decimal(abs(z), places: 2)
        if isNegative(key) {
            return z >= 0 ? "\(magnitude) better on turnovers" : "\(magnitude) worse on turnovers"
        }
        return "\(z >= 0 ? "+" : "−")\(magnitude) \(label(key).lowercased())"
    }
}

/// One category's number for one player (`contracts/CONTRACT.md` §4).
public struct FantasyCategoryValue: Codable, Hashable, Sendable, Identifiable {
    public let category: String
    /// The statistic as a reader knows it: 26.8 points, or 0.571 for FG%.
    public let value: Double?
    public let z: Double?
    /// Percentage categories only: attempts per game, the volume the impact is weighted by.
    public let attempts: Double?
    /// Percentage categories only: `attempts × (rate − pool rate)` — the quantity actually
    /// standardised. Never show this as though it were a shooting percentage.
    public let impact: Double?
    /// Percentage categories only: how much of the rate is the player rather than the league.
    public let shrinkageWeight: Double?

    public var id: String { category }

    public var label: String { FantasyCategory.shortLabel(category) }

    /// `"26.8"` or `"57.1%"`, chosen from the category rather than the number.
    public var displayValue: String {
        guard let value = value, value.isFinite else { return Formatting.emDash }
        return FantasyCategory.isPercentage(category)
            ? Formatting.percent(value, places: 1)
            : Formatting.decimal(value, places: 1)
    }

    public var zText: String {
        guard let z = z, z.isFinite else { return Formatting.emDash }
        return Formatting.decimal(z, places: 2, signed: true)
    }

    public init(category: String, value: Double? = nil, z: Double? = nil,
                attempts: Double? = nil, impact: Double? = nil,
                shrinkageWeight: Double? = nil) {
        self.category = category
        self.value = value
        self.z = z
        self.attempts = attempts
        self.impact = impact
        self.shrinkageWeight = shrinkageWeight
    }
}

// MARK: - fantasy_draft_board

/// Where the next pick falls in a snake draft.
public struct FantasyNextPick: Codable, Hashable, Sendable {
    public let overall: Int
    public let round: Int
    public let pickInRound: Int

    /// `"Round 2, pick 12"`.
    public var text: String { "Round \(round), pick \(pickInRound)" }

    public init(overall: Int = 1, round: Int = 1, pickInRound: Int = 1) {
        self.overall = overall
        self.round = round
        self.pickInRound = pickInRound
    }
}

/// One row of the draft board.
public struct FantasyDraftPick: Codable, Hashable, Sendable, Identifiable {
    public let player: PlayerRef?
    public let overall: Int
    public let round: Int
    public let pickInRound: Int
    public let baselineRank: Int?
    /// Weighted **sum** of the nine z. Not `score`, which is the mean — they differ by the
    /// weight total, and a threshold written for one is wrong for the other by that factor.
    public let totalZ: Double?
    public let score: Double?
    /// Total z minus the replacement level: value over a waiver body, which is the unit a
    /// roster spot is actually spent in.
    public let valueOverReplacement: Double?
    /// What the board sorted on — total z, tilted for roster need.
    public let suggestion: Double?
    public let espnPoints: Double?
    public let yahooPoints: Double?
    public let gamesPlayed: Int?
    public let minutesPerGame: Double?
    public let categories: [FantasyCategoryValue]
    /// Categories this pick would shore up on the manager's own roster.
    public let fills: [String]
    /// One sentence a reader can disagree with.
    public let reason: String?
    public let availability: MetricAvailability

    public var id: Int { player?.playerId ?? overall }

    public var pickText: String { "R\(round)·\(pickInRound)" }

    public func category(_ key: String) -> FantasyCategoryValue? {
        categories.first { $0.category == key }
    }

    public init(player: PlayerRef? = nil, overall: Int = 0, round: Int = 1,
                pickInRound: Int = 1, baselineRank: Int? = nil, totalZ: Double? = nil,
                score: Double? = nil, valueOverReplacement: Double? = nil,
                suggestion: Double? = nil, espnPoints: Double? = nil,
                yahooPoints: Double? = nil, gamesPlayed: Int? = nil,
                minutesPerGame: Double? = nil, categories: [FantasyCategoryValue] = [],
                fills: [String] = [], reason: String? = nil,
                availability: MetricAvailability = .estimated) {
        self.player = player
        self.overall = overall
        self.round = round
        self.pickInRound = pickInRound
        self.baselineRank = baselineRank
        self.totalZ = totalZ
        self.score = score
        self.valueOverReplacement = valueOverReplacement
        self.suggestion = suggestion
        self.espnPoints = espnPoints
        self.yahooPoints = yahooPoints
        self.gamesPlayed = gamesPlayed
        self.minutesPerGame = minutesPerGame
        self.categories = categories
        self.fills = fills
        self.reason = reason
        self.availability = availability
    }

    private enum CodingKeys: String, CodingKey {
        case player, overall, round, pickInRound, baselineRank, totalZ, score
        case valueOverReplacement, suggestion, espnPoints, yahooPoints
        case gamesPlayed, minutesPerGame, categories, fills, reason, availability
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        player = try container.decodeIfPresent(PlayerRef.self, forKey: .player)
        overall = try container.decodeIfPresent(Int.self, forKey: .overall) ?? 0
        round = try container.decodeIfPresent(Int.self, forKey: .round) ?? 1
        pickInRound = try container.decodeIfPresent(Int.self, forKey: .pickInRound) ?? 1
        baselineRank = try container.decodeIfPresent(Int.self, forKey: .baselineRank)
        totalZ = try container.decodeIfPresent(Double.self, forKey: .totalZ)
        score = try container.decodeIfPresent(Double.self, forKey: .score)
        valueOverReplacement = try container.decodeIfPresent(Double.self, forKey: .valueOverReplacement)
        suggestion = try container.decodeIfPresent(Double.self, forKey: .suggestion)
        espnPoints = try container.decodeIfPresent(Double.self, forKey: .espnPoints)
        yahooPoints = try container.decodeIfPresent(Double.self, forKey: .yahooPoints)
        gamesPlayed = try container.decodeIfPresent(Int.self, forKey: .gamesPlayed)
        minutesPerGame = try container.decodeIfPresent(Double.self, forKey: .minutesPerGame)
        categories = try container.decodeIfPresent([FantasyCategoryValue].self, forKey: .categories) ?? []
        fills = try container.decodeIfPresent([String].self, forKey: .fills) ?? []
        reason = try container.decodeIfPresent(String.self, forKey: .reason)
        // A valuation is not a record, so an absent or unreadable marker is `estimated`.
        let raw = (try? container.decodeIfPresent(MetricAvailability.self, forKey: .availability)) ?? nil
        availability = (raw == .full || raw == nil) ? .estimated : raw!
    }
}

/// The draft board (`contracts/CONTRACT.md` §4, `fantasy_draft_board`).
public struct FantasyDraftBoardPayload: Codable, Hashable, Sendable {
    public let season: String
    public let seasonType: String
    /// `"categories"`, `"espn_points"` or `"yahoo_points"`.
    public let scoring: String
    public let categories: [String]
    public let puntCategories: [String]
    public let teams: Int
    public let rosterSpots: Int
    public let nextPick: FantasyNextPick
    public let poolSize: Int
    /// What a freed roster spot refills at. Negative in a normal league, by construction.
    public let replacementValue: Double?
    public let weakestCategories: [String]
    public let rosterStrength: [String: Double]
    public let picks: [FantasyDraftPick]
    public let note: String?

    public var isPointsLeague: Bool { scoring != "categories" }

    /// `"12 teams · 13 spots · top 150"`.
    public var contextText: String {
        var parts: [String] = []
        if teams > 0 { parts.append("\(teams) teams") }
        if rosterSpots > 0 { parts.append("\(rosterSpots) spots") }
        if poolSize > 0 { parts.append("top \(poolSize)") }
        return parts.joined(separator: " · ")
    }

    public init(season: String = "", seasonType: String = "Regular Season",
                scoring: String = "categories", categories: [String] = FantasyCategory.order,
                puntCategories: [String] = [], teams: Int = 12, rosterSpots: Int = 13,
                nextPick: FantasyNextPick = FantasyNextPick(), poolSize: Int = 0,
                replacementValue: Double? = nil, weakestCategories: [String] = [],
                rosterStrength: [String: Double] = [:], picks: [FantasyDraftPick] = [],
                note: String? = nil) {
        self.season = season
        self.seasonType = seasonType
        self.scoring = scoring
        self.categories = categories
        self.puntCategories = puntCategories
        self.teams = teams
        self.rosterSpots = rosterSpots
        self.nextPick = nextPick
        self.poolSize = poolSize
        self.replacementValue = replacementValue
        self.weakestCategories = weakestCategories
        self.rosterStrength = rosterStrength
        self.picks = picks
        self.note = note
    }

    private enum CodingKeys: String, CodingKey {
        case season, seasonType, scoring, categories, puntCategories, teams, rosterSpots
        case nextPick, poolSize, replacementValue, weakestCategories, rosterStrength
        case picks, note
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        season = try container.decodeIfPresent(String.self, forKey: .season) ?? ""
        seasonType = try container.decodeIfPresent(String.self, forKey: .seasonType) ?? "Regular Season"
        scoring = try container.decodeIfPresent(String.self, forKey: .scoring) ?? "categories"
        categories = try container.decodeIfPresent([String].self, forKey: .categories) ?? FantasyCategory.order
        puntCategories = try container.decodeIfPresent([String].self, forKey: .puntCategories) ?? []
        teams = try container.decodeIfPresent(Int.self, forKey: .teams) ?? 0
        rosterSpots = try container.decodeIfPresent(Int.self, forKey: .rosterSpots) ?? 0
        nextPick = try container.decodeIfPresent(FantasyNextPick.self, forKey: .nextPick) ?? FantasyNextPick()
        poolSize = try container.decodeIfPresent(Int.self, forKey: .poolSize) ?? 0
        replacementValue = try container.decodeIfPresent(Double.self, forKey: .replacementValue)
        weakestCategories = try container.decodeIfPresent([String].self, forKey: .weakestCategories) ?? []
        rosterStrength = try container.decodeIfPresent([String: Double].self, forKey: .rosterStrength) ?? [:]
        picks = try container.decodeIfPresent([FantasyDraftPick].self, forKey: .picks) ?? []
        note = try container.decodeIfPresent(String.self, forKey: .note)
    }

    public static let preview = FantasyDraftBoardPayload(
        season: "2025-26",
        scoring: "categories",
        puntCategories: ["ft_pct"],
        teams: 12, rosterSpots: 13,
        nextPick: FantasyNextPick(overall: 5, round: 1, pickInRound: 5),
        poolSize: 150,
        replacementValue: -3.2,
        weakestCategories: ["ast", "fg3m", "stl"],
        rosterStrength: ["pts": 2.1, "reb": 4.4, "ast": -1.8],
        picks: [
            FantasyDraftPick(
                player: .preview, overall: 5, round: 1, pickInRound: 5, baselineRank: 4,
                totalZ: 6.82, score: 0.76, valueOverReplacement: 10.02, suggestion: 7.01,
                espnPoints: 48.2, yahooPoints: 41.6, gamesPlayed: 72, minutesPerGame: 34.1,
                categories: [
                    FantasyCategoryValue(category: "pts", value: 26.8, z: 1.94),
                    FantasyCategoryValue(category: "fg3m", value: 1.6, z: -0.31),
                    FantasyCategoryValue(category: "reb", value: 12.2, z: 2.44),
                    FantasyCategoryValue(category: "ast", value: 9.7, z: 2.81),
                    FantasyCategoryValue(category: "stl", value: 1.5, z: 0.62),
                    FantasyCategoryValue(category: "blk", value: 0.8, z: 0.11),
                    FantasyCategoryValue(category: "tov", value: 3.2, z: -1.42),
                    FantasyCategoryValue(category: "fg_pct", value: 0.571, z: 1.12,
                                         attempts: 17.5, impact: 1.74, shrinkageWeight: 0.91),
                    FantasyCategoryValue(category: "ft_pct", value: 0.817, z: 0.09,
                                         attempts: 6.3, impact: 0.13, shrinkageWeight: 0.95),
                ],
                fills: ["ast"],
                reason: "Best available; carries assists and rebounds; turns it over"
            ),
            FantasyDraftPick(
                player: .previewSecondary, overall: 6, round: 1, pickInRound: 6,
                baselineRank: 7, totalZ: 5.44, score: 0.60, valueOverReplacement: 8.64,
                suggestion: 5.51, espnPoints: 44.0, yahooPoints: 38.2,
                gamesPlayed: 66, minutesPerGame: 30.9,
                categories: [
                    FantasyCategoryValue(category: "pts", value: 25.9, z: 1.81),
                    FantasyCategoryValue(category: "fg3m", value: 2.3, z: 0.54),
                    FantasyCategoryValue(category: "reb", value: 12.1, z: 2.40),
                    FantasyCategoryValue(category: "ast", value: 3.6, z: 0.12),
                    FantasyCategoryValue(category: "stl", value: 1.1, z: 0.08),
                    FantasyCategoryValue(category: "blk", value: 3.4, z: 3.02),
                    FantasyCategoryValue(category: "tov", value: 2.9, z: -1.05),
                    FantasyCategoryValue(category: "fg_pct", value: 0.495, z: -0.42,
                                         attempts: 18.4, impact: -0.68, shrinkageWeight: 0.92),
                    FantasyCategoryValue(category: "ft_pct", value: 0.823, z: 0.14,
                                         attempts: 6.5, impact: 0.19, shrinkageWeight: 0.95),
                ],
                fills: [],
                reason: "carries blocks and rebounds"
            ),
        ],
        note: "Values are z-scores against the top 150 players of 2025-26; a projection is not involved."
    )
}

// MARK: - fantasy_trade

/// One player on one side of a trade.
public struct FantasyTradePlayer: Codable, Hashable, Sendable, Identifiable {
    public let player: PlayerRef?
    public let totalZ: Double?
    public let baselineRank: Int?
    public let gamesPlayed: Int?
    /// `category -> z`, so a side can be read category by category.
    public let categories: [String: Double]
    public let availability: MetricAvailability

    public var id: Int { player?.playerId ?? baselineRank ?? 0 }

    public init(player: PlayerRef? = nil, totalZ: Double? = nil, baselineRank: Int? = nil,
                gamesPlayed: Int? = nil, categories: [String: Double] = [:],
                availability: MetricAvailability = .estimated) {
        self.player = player
        self.totalZ = totalZ
        self.baselineRank = baselineRank
        self.gamesPlayed = gamesPlayed
        self.categories = categories
        self.availability = availability
    }
}

/// One side of a trade, with its totals.
public struct FantasyTradeSide: Codable, Hashable, Sendable {
    public let label: String
    public let count: Int
    public let totalZ: Double?
    public let espnPoints: Double?
    public let yahooPoints: Double?
    public let players: [FantasyTradePlayer]
    /// Ids the server could not value. Left out of every total rather than counted as zero.
    public let missing: [String]

    public init(label: String = "", count: Int = 0, totalZ: Double? = nil,
                espnPoints: Double? = nil, yahooPoints: Double? = nil,
                players: [FantasyTradePlayer] = [], missing: [String] = []) {
        self.label = label
        self.count = count
        self.totalZ = totalZ
        self.espnPoints = espnPoints
        self.yahooPoints = yahooPoints
        self.players = players
        self.missing = missing
    }

    private enum CodingKeys: String, CodingKey {
        case label, count, totalZ, espnPoints, yahooPoints, players, missing
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        label = try container.decodeIfPresent(String.self, forKey: .label) ?? ""
        count = try container.decodeIfPresent(Int.self, forKey: .count) ?? 0
        totalZ = try container.decodeIfPresent(Double.self, forKey: .totalZ)
        espnPoints = try container.decodeIfPresent(Double.self, forKey: .espnPoints)
        yahooPoints = try container.decodeIfPresent(Double.self, forKey: .yahooPoints)
        players = try container.decodeIfPresent([FantasyTradePlayer].self, forKey: .players) ?? []
        missing = try container.decodeIfPresent([String].self, forKey: .missing) ?? []
    }
}

/// One category's line in the trade table.
public struct FantasyTradeCategory: Codable, Hashable, Sendable, Identifiable {
    public let category: String
    public let give: Double?
    public let get: Double?
    /// `get − give`, before the roster-spot adjustment.
    public let change: Double?
    /// The change plus this category's share of the roster adjustment.
    public let net: Double?
    /// `"gain"`, `"loss"` or `"level"`.
    public let verdict: String
    public let punted: Bool

    public var id: String { category }
    public var label: String { FantasyCategory.shortLabel(category) }

    /// A phrase that respects the category's direction — a positive turnover net is an
    /// improvement, and calling it "gains turnovers" would read as praise for a flaw.
    public var phrase: String {
        FantasyCategory.changePhrase(category, z: net ?? 0)
    }

    public init(category: String, give: Double? = nil, get: Double? = nil,
                change: Double? = nil, net: Double? = nil, verdict: String = "level",
                punted: Bool = false) {
        self.category = category
        self.give = give
        self.get = get
        self.change = change
        self.net = net
        self.verdict = verdict
        self.punted = punted
    }
}

/// One scenario in the sensitivity sweep.
public struct FantasyScenario: Codable, Hashable, Sendable, Identifiable {
    public let key: String
    public let label: String
    public let net: Double?

    public var id: String { key }

    public init(key: String, label: String, net: Double? = nil) {
        self.key = key
        self.label = label
        self.net = net
    }
}

/// The spread of a trade's net across named scenarios.
///
/// **Not an interval and never to be labelled as one.** There is no probability anywhere in it.
/// It is the same trade recomputed under a handful of enumerable assumptions, with the scenario
/// that produced each end. `docs/FANTASY.md` §2d sets out why a predictive interval is not on
/// offer: the engine's negative-binomial band is a single-game count for one player in one
/// category, and a signed sum over eight players and nine standardised categories would need a
/// covariance matrix this project does not have.
public struct FantasySensitivity: Codable, Hashable, Sendable {
    public let base: Double?
    public let low: Double?
    public let high: Double?
    public let width: Double?
    public let lowScenario: String?
    public let highScenario: String?
    /// True when the scenarios disagree about whether the trade helps at all. The single most
    /// useful thing this block says, and the reason it exists.
    public let flips: Bool
    public let scenarios: [FantasyScenario]

    public var rangeText: String {
        guard let low = low, let high = high, low.isFinite, high.isFinite else {
            return Formatting.emDash
        }
        return "\(Formatting.decimal(low, places: 2, signed: true)) to \(Formatting.decimal(high, places: 2, signed: true))"
    }

    public init(base: Double? = nil, low: Double? = nil, high: Double? = nil,
                width: Double? = nil, lowScenario: String? = nil, highScenario: String? = nil,
                flips: Bool = false, scenarios: [FantasyScenario] = []) {
        self.base = base
        self.low = low
        self.high = high
        self.width = width
        self.lowScenario = lowScenario
        self.highScenario = highScenario
        self.flips = flips
        self.scenarios = scenarios
    }

    private enum CodingKeys: String, CodingKey {
        case base, low, high, width, lowScenario, highScenario, flips, scenarios
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        base = try container.decodeIfPresent(Double.self, forKey: .base)
        low = try container.decodeIfPresent(Double.self, forKey: .low)
        high = try container.decodeIfPresent(Double.self, forKey: .high)
        width = try container.decodeIfPresent(Double.self, forKey: .width)
        lowScenario = try container.decodeIfPresent(String.self, forKey: .lowScenario)
        highScenario = try container.decodeIfPresent(String.self, forKey: .highScenario)
        flips = try container.decodeIfPresent(Bool.self, forKey: .flips) ?? false
        scenarios = try container.decodeIfPresent([FantasyScenario].self, forKey: .scenarios) ?? []
    }
}

/// A points-league summary for one scoring system.
public struct FantasyPointsOutcome: Codable, Hashable, Sendable {
    public let change: Double?
    public let net: Double?
    public let verdict: String

    public init(change: Double? = nil, net: Double? = nil, verdict: String = "fair") {
        self.change = change
        self.net = net
        self.verdict = verdict
    }
}

/// The trade analyzer (`contracts/CONTRACT.md` §4, `fantasy_trade`).
public struct FantasyTradePayload: Codable, Hashable, Sendable {
    public let season: String
    public let seasonType: String
    public let puntCategories: [String]
    public let give: FantasyTradeSide
    public let get: FantasyTradeSide
    public let categories: [FantasyTradeCategory]
    /// `get − give`, before the roster adjustment.
    public let changeZ: Double?
    /// `(n_give − n_get) × replacement`. **Its own number on purpose**: a freed roster spot
    /// refills from waivers below pool average and routinely outweighs the players themselves,
    /// so a reader who only sees the net cannot tell a bad trade from slot arithmetic.
    public let rosterAdjustment: Double?
    public let netZ: Double?
    public let replacementValue: Double?
    /// The pool's own SD of total z. The bands have to be read against this, not taken as
    /// universal: 0.75 means one thing against a spread of 0.9 and another against 2.8.
    public let poolSpread: Double?
    public let verdict: String
    public let bands: FantasyBands
    public let points: [String: FantasyPointsOutcome]
    public let sensitivity: FantasySensitivity?
    public let note: String?

    public var isEmpty: Bool { give.count == 0 || get.count == 0 }

    /// True when the roster adjustment is doing more work than the players are.
    public var rosterDominates: Bool {
        guard let adjustment = rosterAdjustment, let change = changeZ else { return false }
        return abs(adjustment) > abs(change)
    }

    public init(season: String = "", seasonType: String = "Regular Season",
                puntCategories: [String] = [], give: FantasyTradeSide = FantasyTradeSide(),
                get: FantasyTradeSide = FantasyTradeSide(),
                categories: [FantasyTradeCategory] = [], changeZ: Double? = nil,
                rosterAdjustment: Double? = nil, netZ: Double? = nil,
                replacementValue: Double? = nil, poolSpread: Double? = nil,
                verdict: String = "fair", bands: FantasyBands = FantasyBands(),
                points: [String: FantasyPointsOutcome] = [:],
                sensitivity: FantasySensitivity? = nil, note: String? = nil) {
        self.season = season
        self.seasonType = seasonType
        self.puntCategories = puntCategories
        self.give = give
        self.get = get
        self.categories = categories
        self.changeZ = changeZ
        self.rosterAdjustment = rosterAdjustment
        self.netZ = netZ
        self.replacementValue = replacementValue
        self.poolSpread = poolSpread
        self.verdict = verdict
        self.bands = bands
        self.points = points
        self.sensitivity = sensitivity
        self.note = note
    }

    private enum CodingKeys: String, CodingKey {
        case season, seasonType, puntCategories, give, get, categories, changeZ
        case rosterAdjustment, netZ, replacementValue, poolSpread, verdict, bands
        case points, sensitivity, note
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        season = try container.decodeIfPresent(String.self, forKey: .season) ?? ""
        seasonType = try container.decodeIfPresent(String.self, forKey: .seasonType) ?? "Regular Season"
        puntCategories = try container.decodeIfPresent([String].self, forKey: .puntCategories) ?? []
        give = try container.decodeIfPresent(FantasyTradeSide.self, forKey: .give) ?? FantasyTradeSide()
        get = try container.decodeIfPresent(FantasyTradeSide.self, forKey: .get) ?? FantasyTradeSide()
        categories = try container.decodeIfPresent([FantasyTradeCategory].self, forKey: .categories) ?? []
        changeZ = try container.decodeIfPresent(Double.self, forKey: .changeZ)
        rosterAdjustment = try container.decodeIfPresent(Double.self, forKey: .rosterAdjustment)
        netZ = try container.decodeIfPresent(Double.self, forKey: .netZ)
        replacementValue = try container.decodeIfPresent(Double.self, forKey: .replacementValue)
        poolSpread = try container.decodeIfPresent(Double.self, forKey: .poolSpread)
        verdict = try container.decodeIfPresent(String.self, forKey: .verdict) ?? "fair"
        bands = try container.decodeIfPresent(FantasyBands.self, forKey: .bands) ?? FantasyBands()
        points = try container.decodeIfPresent([String: FantasyPointsOutcome].self, forKey: .points) ?? [:]
        sensitivity = try container.decodeIfPresent(FantasySensitivity.self, forKey: .sensitivity)
        note = try container.decodeIfPresent(String.self, forKey: .note)
    }

    public static let preview = FantasyTradePayload(
        season: "2025-26",
        give: FantasyTradeSide(
            label: "give", count: 2, totalZ: 8.14, espnPoints: 71.2, yahooPoints: 60.4,
            players: [
                FantasyTradePlayer(player: .preview, totalZ: 4.90, baselineRank: 11,
                                   gamesPlayed: 68,
                                   categories: ["pts": 1.2, "reb": 2.1, "ast": 0.4]),
                FantasyTradePlayer(player: .previewSecondary, totalZ: 3.24, baselineRank: 34,
                                   gamesPlayed: 71,
                                   categories: ["pts": 0.6, "reb": 0.2, "ast": 1.9]),
            ]),
        get: FantasyTradeSide(
            label: "get", count: 1, totalZ: 9.35, espnPoints: 77.6, yahooPoints: 66.0,
            players: [
                FantasyTradePlayer(player: .preview, totalZ: 9.35, baselineRank: 2,
                                   gamesPlayed: 74,
                                   categories: ["pts": 2.6, "reb": 1.9, "ast": 2.2]),
            ]),
        categories: [
            FantasyTradeCategory(category: "pts", give: 1.8, get: 2.6, change: 0.8, net: 0.44, verdict: "gain"),
            FantasyTradeCategory(category: "fg3m", give: 0.9, get: 1.8, change: 0.9, net: 0.54, verdict: "gain"),
            FantasyTradeCategory(category: "reb", give: 2.3, get: 1.9, change: -0.4, net: -0.76, verdict: "loss"),
            FantasyTradeCategory(category: "ast", give: 2.3, get: 2.2, change: -0.1, net: -0.46, verdict: "loss"),
            FantasyTradeCategory(category: "stl", give: 0.5, get: 0.8, change: 0.3, net: -0.06, verdict: "level"),
            FantasyTradeCategory(category: "blk", give: 0.4, get: 0.1, change: -0.3, net: -0.66, verdict: "loss"),
            FantasyTradeCategory(category: "tov", give: -0.8, get: -1.4, change: -0.6, net: -0.96, verdict: "loss"),
            FantasyTradeCategory(category: "fg_pct", give: 0.5, get: 1.1, change: 0.6, net: 0.24, verdict: "level"),
            FantasyTradeCategory(category: "ft_pct", give: 0.2, get: 0.2, change: 0.0, net: -0.36, verdict: "loss"),
        ],
        changeZ: 1.21, rosterAdjustment: -3.2, netZ: -1.99,
        replacementValue: -3.2, poolSpread: 2.06,
        verdict: "slight loss",
        bands: FantasyBands(fair: 0.75, clear: 2.0),
        points: [
            "espn_points": FantasyPointsOutcome(change: 6.4, net: -8.1, verdict: "clear loss"),
            "yahoo_points": FantasyPointsOutcome(change: 5.6, net: -6.9, verdict: "slight loss"),
        ],
        sensitivity: FantasySensitivity(
            base: -1.99, low: -4.40, high: 0.31, width: 4.71,
            lowScenario: "The player you give up gains 15% of his minutes",
            highScenario: "The player you get misses 22 games",
            flips: true,
            scenarios: [
                FantasyScenario(key: "base", label: "As projected", net: -1.99),
                FantasyScenario(key: "get_injured", label: "The player you get misses 22 games", net: 0.31),
                FantasyScenario(key: "get_role_loss", label: "The player you get loses 15% of his minutes", net: -0.74),
                FantasyScenario(key: "give_injured", label: "The player you give up misses 22 games", net: -3.10),
                FantasyScenario(key: "give_breakout", label: "The player you give up gains 15% of his minutes", net: -4.40),
            ]),
        note: "The range is a sweep over named assumptions, not a confidence interval."
    )
}

/// The thresholds a verdict is read against. Editable, because they are a property of a league.
public struct FantasyBands: Codable, Hashable, Sendable {
    public let fair: Double
    public let clear: Double

    public init(fair: Double = 0.75, clear: Double = 2.0) {
        self.fair = fair
        self.clear = clear
    }

    private enum CodingKeys: String, CodingKey { case fair, clear }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        fair = try container.decodeIfPresent(Double.self, forKey: .fair) ?? 0.75
        clear = try container.decodeIfPresent(Double.self, forKey: .clear) ?? 2.0
    }
}
