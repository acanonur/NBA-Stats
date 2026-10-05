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

/// One column of the draft-board table (`contracts/CONTRACT.md` §4).
///
/// Deliberately **not** a `MetricDescriptor`. Nine of these columns are pool-relative z-scores
/// and three are identity, none of which exist in `contracts/metrics.json` — and a payload
/// object carrying `key`/`name`/`shortName`/`category`/`format`/`availability` together is
/// asserted by the contract tests to be verbatim a catalog entry. This is a lighter thing that
/// says only what a table cell needs.
public struct FantasyColumn: Codable, Hashable, Sendable, Identifiable {
    public let key: String
    public let label: String
    /// `nil` for a text column such as the team abbreviation.
    public let format: MetricFormat?
    /// `"summary"`, `"production"` or `"impact"` — the strips a reader can page between.
    public let group: String
    /// Leading for text, trailing for every number, so the decimal points line up.
    public let align: String
    /// Whether a larger number is better, for the sign colouring. `nil` where it is neither —
    /// field-goal attempts are volume, not virtue.
    public let higherIsBetter: Bool?
    /// True for a z column whose category is punted. Raw production is never punted: a punt
    /// zeroes a category's weight, not a player's rebounds.
    public let punted: Bool
    /// True where the value reads better with an explicit `+`.
    public let signed: Bool

    public var id: String { key }
    public var isTrailing: Bool { align != "leading" }

    public init(key: String, label: String, format: MetricFormat? = nil,
                group: String = "production", align: String = "trailing",
                higherIsBetter: Bool? = nil, punted: Bool = false, signed: Bool = false) {
        self.key = key
        self.label = label
        self.format = format
        self.group = group
        self.align = align
        self.higherIsBetter = higherIsBetter
        self.punted = punted
        self.signed = signed
    }

    private enum CodingKeys: String, CodingKey {
        case key, label, format, group, align, higherIsBetter, punted, signed
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        key = try container.decode(String.self, forKey: .key)
        label = try container.decodeIfPresent(String.self, forKey: .label) ?? key.uppercased()
        // A format this build does not know must cost the column its formatting, never the
        // whole table — so it is read leniently and falls back to a plain decimal.
        format = (try? container.decodeIfPresent(MetricFormat.self, forKey: .format)) ?? nil
        group = try container.decodeIfPresent(String.self, forKey: .group) ?? "production"
        align = try container.decodeIfPresent(String.self, forKey: .align) ?? "trailing"
        higherIsBetter = try container.decodeIfPresent(Bool.self, forKey: .higherIsBetter)
        punted = try container.decodeIfPresent(Bool.self, forKey: .punted) ?? false
        signed = try container.decodeIfPresent(Bool.self, forKey: .signed) ?? false
    }

    /// One cell, formatted from the raw number the row carries.
    ///
    /// An absent key is an em dash and never a zero — `contracts/CONTRACT.md` §6's rule, which
    /// matters here because a missing value and a genuine 0.0 look identical once rendered.
    public func text(_ value: Double?) -> String {
        guard let value = value, value.isFinite else { return Formatting.emDash }
        if let format = format {
            if signed && !format.isPercentage {
                return Formatting.decimal(value, places: format.decimals, signed: true)
            }
            return Formatting.value(value, format: format)
        }
        return Formatting.decimal(value, places: 1, signed: signed)
    }
}

/// One row of the draft-board table.
public struct FantasyDraftRow: Codable, Hashable, Sendable, Identifiable {
    public let rank: Int
    public let round: Int
    public let pickInRound: Int
    public let player: PlayerRef?
    public let baselineRank: Int?
    public let totalZ: Double?
    public let valueOverReplacement: Double?
    public let suggestion: Double?
    public let espnPoints: Double?
    public let yahooPoints: Double?
    /// Every column's value by key. A dict rather than a parallel array, so a column this build
    /// does not know about is skipped instead of shifting every cell after it by one.
    public let values: [String: Double]
    /// The one non-numeric cell: the team abbreviation.
    public let team: String?
    public let fills: [String]
    public let reason: String?
    public let availability: MetricAvailability

    public var id: Int { player?.playerId ?? rank }
    public var pickText: String { "R\(round)·\(pickInRound)" }

    public func value(_ key: String) -> Double? { values[key] }

    public init(rank: Int = 0, round: Int = 1, pickInRound: Int = 1, player: PlayerRef? = nil,
                baselineRank: Int? = nil, totalZ: Double? = nil,
                valueOverReplacement: Double? = nil, suggestion: Double? = nil,
                espnPoints: Double? = nil, yahooPoints: Double? = nil,
                values: [String: Double] = [:], team: String? = nil,
                fills: [String] = [], reason: String? = nil,
                availability: MetricAvailability = .estimated) {
        self.rank = rank
        self.round = round
        self.pickInRound = pickInRound
        self.player = player
        self.baselineRank = baselineRank
        self.totalZ = totalZ
        self.valueOverReplacement = valueOverReplacement
        self.suggestion = suggestion
        self.espnPoints = espnPoints
        self.yahooPoints = yahooPoints
        self.values = values
        self.team = team
        self.fills = fills
        self.reason = reason
        self.availability = availability
    }

    private enum CodingKeys: String, CodingKey {
        case rank, round, pickInRound, player, baselineRank, totalZ, valueOverReplacement
        case suggestion, espnPoints, yahooPoints, values, fills, reason, availability
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        rank = try container.decodeIfPresent(Int.self, forKey: .rank) ?? 0
        round = try container.decodeIfPresent(Int.self, forKey: .round) ?? 1
        pickInRound = try container.decodeIfPresent(Int.self, forKey: .pickInRound) ?? 1
        player = try container.decodeIfPresent(PlayerRef.self, forKey: .player)
        baselineRank = try container.decodeIfPresent(Int.self, forKey: .baselineRank)
        totalZ = try container.decodeIfPresent(Double.self, forKey: .totalZ)
        valueOverReplacement = try container.decodeIfPresent(Double.self, forKey: .valueOverReplacement)
        suggestion = try container.decodeIfPresent(Double.self, forKey: .suggestion)
        espnPoints = try container.decodeIfPresent(Double.self, forKey: .espnPoints)
        yahooPoints = try container.decodeIfPresent(Double.self, forKey: .yahooPoints)
        fills = try container.decodeIfPresent([String].self, forKey: .fills) ?? []
        reason = try container.decodeIfPresent(String.self, forKey: .reason)
        let rawAvailability = (try? container.decodeIfPresent(MetricAvailability.self, forKey: .availability)) ?? nil
        availability = (rawAvailability == .full || rawAvailability == nil) ? .estimated : rawAvailability!
        // `values` mixes numbers with one string (the team), so it is decoded through JSONValue
        // and split rather than as [String: Double], which would throw on the team and cost the
        // reader the entire row.
        let raw = try container.decodeIfPresent([String: JSONValue].self, forKey: .values) ?? [:]
        var numbers: [String: Double] = [:]
        var abbreviation: String?
        for (key, entry) in raw {
            if let number = entry.doubleValue {
                numbers[key] = number
            } else if key == "team", let text = entry.stringValue {
                abbreviation = text
            }
        }
        values = numbers
        team = abbreviation
    }
}

/// The draft board (`contracts/CONTRACT.md` §4, `fantasy_draft_board`).
///
/// A table, not a list of cards: the columns ship as data so the server decides the layout, and
/// the client renders whatever arrives. The order follows a FanScout export — rank and player,
/// a value, the identity block, the raw per-game line, then the nine z-scores — because that is
/// the sheet a reader is most likely to be holding beside the app.
public struct FantasyDraftBoardPayload: Codable, Hashable, Sendable {
    public let season: String
    public let seasonType: String
    public let scoring: String
    public let categories: [String]
    public let puntCategories: [String]
    public let teams: Int
    public let rosterSpots: Int
    public let nextPick: FantasyNextPick
    public let poolSize: Int
    public let replacementValue: Double?
    public let weakestCategories: [String]
    public let rosterStrength: [String: Double]
    public let columns: [FantasyColumn]
    public let rows: [FantasyDraftRow]
    public let note: String?

    public var isPointsLeague: Bool { scoring != "categories" }

    public func columns(in group: String) -> [FantasyColumn] {
        columns.filter { $0.group == group }
    }

    /// The groups present, in the order they first appear, so the strip toggle follows the
    /// server's ordering rather than an alphabetical one.
    public var groups: [String] {
        var seen: [String] = []
        for column in columns where !seen.contains(column.group) {
            seen.append(column.group)
        }
        return seen
    }

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
                rosterStrength: [String: Double] = [:], columns: [FantasyColumn] = [],
                rows: [FantasyDraftRow] = [], note: String? = nil) {
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
        self.columns = columns
        self.rows = rows
        self.note = note
    }

    private enum CodingKeys: String, CodingKey {
        case season, seasonType, scoring, categories, puntCategories, teams, rosterSpots
        case nextPick, poolSize, replacementValue, weakestCategories, rosterStrength
        case columns, rows, note
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
        columns = try container.decodeIfPresent([FantasyColumn].self, forKey: .columns) ?? []
        rows = try container.decodeIfPresent([FantasyDraftRow].self, forKey: .rows) ?? []
        note = try container.decodeIfPresent(String.self, forKey: .note)
    }

    public static let preview: FantasyDraftBoardPayload = {
        let columns: [FantasyColumn] = [
            FantasyColumn(key: "score", label: "Value", format: .decimal2, group: "summary",
                          higherIsBetter: true, signed: true),
            FantasyColumn(key: "team", label: "Team", format: nil, group: "summary",
                          align: "leading"),
            FantasyColumn(key: "gp", label: "GP", format: .integer, group: "summary",
                          higherIsBetter: true),
            FantasyColumn(key: "mpg", label: "MPG", format: .decimal1, group: "summary",
                          higherIsBetter: true),
            FantasyColumn(key: "pts", label: "PTS", format: .decimal1, higherIsBetter: true),
            FantasyColumn(key: "fg3m", label: "TPM", format: .decimal1, higherIsBetter: true),
            FantasyColumn(key: "reb", label: "REB", format: .decimal1, higherIsBetter: true),
            FantasyColumn(key: "ast", label: "AST", format: .decimal1, higherIsBetter: true),
            FantasyColumn(key: "stl", label: "STL", format: .decimal1, higherIsBetter: true),
            FantasyColumn(key: "blk", label: "BLK", format: .decimal1, higherIsBetter: true),
            FantasyColumn(key: "tov", label: "TOV", format: .decimal1, higherIsBetter: false),
            FantasyColumn(key: "fg_pct", label: "FG%", format: .percent1, higherIsBetter: true),
            FantasyColumn(key: "fga", label: "FGA", format: .decimal1),
            FantasyColumn(key: "ft_pct", label: "FT%", format: .percent1, higherIsBetter: true),
            FantasyColumn(key: "fta", label: "FTA", format: .decimal1),
        ] + ["pts", "fg3m", "ast", "reb", "stl", "blk", "tov", "fg_pct", "ft_pct"].map { key in
            FantasyColumn(key: "z_\(key)",
                          label: "z" + FantasyCategory.shortLabel(key),
                          format: .decimal2, group: "impact",
                          higherIsBetter: true, signed: true)
        }
        let rows: [FantasyDraftRow] = [
            FantasyDraftRow(
                rank: 1, round: 1, pickInRound: 1, player: .preview, baselineRank: 1,
                totalZ: 16.94, valueOverReplacement: 20.1, suggestion: 1.88,
                espnPoints: 52.4, yahooPoints: 45.1,
                values: ["score": 1.88, "gp": 68, "mpg": 31, "pts": 28.3, "fg3m": 1.96,
                         "reb": 12.7, "ast": 3.9, "stl": 1.18, "blk": 3.54, "tov": 2.06,
                         "fg_pct": 0.5355, "fga": 18.8, "ft_pct": 0.8428, "fta": 7.4,
                         "z_pts": 2.29, "z_fg3m": -0.43, "z_ast": -0.37, "z_reb": 3.20,
                         "z_stl": -0.19, "z_blk": 6.64, "z_tov": 0.20, "z_fg_pct": 2.82,
                         "z_ft_pct": 2.77],
                team: "SAS", fills: [],
                reason: "Best available; carries blocks and rebounds"),
            FantasyDraftRow(
                rank: 2, round: 1, pickInRound: 2, player: .previewSecondary, baselineRank: 2,
                totalZ: 9.17, valueOverReplacement: 12.4, suggestion: 1.02,
                espnPoints: 49.8, yahooPoints: 44.0,
                values: ["score": 1.02, "gp": 68, "mpg": 36, "pts": 34.6, "fg3m": 4.27,
                         "reb": 7.8, "ast": 8.8, "stl": 1.31, "blk": 0.51, "tov": 4.03,
                         "fg_pct": 0.4773, "fga": 23.5, "ft_pct": 0.78, "fta": 10.1,
                         "z_pts": 3.64, "z_fg3m": 3.72, "z_ast": 2.68, "z_reb": 0.61,
                         "z_stl": 0.26, "z_blk": -0.96, "z_tov": -2.96, "z_fg_pct": 0.73,
                         "z_ft_pct": 1.45],
                team: "LAL", fills: ["ast"],
                reason: "carries points and threes; turns it over"),
        ]
        return FantasyDraftBoardPayload(
            season: "2025-26", puntCategories: [], teams: 12, rosterSpots: 13,
            nextPick: FantasyNextPick(overall: 1, round: 1, pickInRound: 1),
            poolSize: 150, replacementValue: -3.2,
            weakestCategories: ["ast", "fg3m", "stl"],
            rosterStrength: ["pts": 2.1, "reb": 4.4, "ast": -1.8],
            columns: columns, rows: rows,
            note: "Values are z-scores against the top 150 players of 2025-26."
        )
    }()
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
