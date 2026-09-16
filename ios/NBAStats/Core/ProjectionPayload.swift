import Foundation

// MARK: - next_game_projection

/// Decodes a games-count field that the contract writes as a whole number.
///
/// `decodeIfPresent(Int.self, …)` throws when the number arrived as `6.0` rather than `6`, and
/// that throw would propagate out of the enclosing `init(from:)` and cost the reader the entire
/// projection over a serialiser's choice of literal. Reading the number either way keeps the
/// failure local, and an absent or unusable value simply becomes `nil`.
private func decodeWholeNumber<K: CodingKey>(_ container: KeyedDecodingContainer<K>, forKey key: K) -> Int? {
    guard container.contains(key) else { return nil }
    if let value = try? container.decode(Int.self, forKey: key) { return value }
    // Clamp in `Double` space, *before* the conversion: `Int(_: Double)` traps on anything
    // outside `Int`'s range, and this number came straight off the wire.
    guard let value = try? container.decode(Double.self, forKey: key),
          value.isFinite,
          abs(value) < 1_000_000_000 else { return nil }
    return Int(value.rounded())
}

/// Renders one bound of a projected interval.
///
/// A counting stat's descriptor formats as `decimal1` because a *per-game average* wants a
/// decimal, but the bounds of a single game's negative-binomial interval are whole counts — the
/// contract writes them as `19` and `38`. Printing `19.0–38.0` would imply a precision the
/// interval does not have, so an integral bound prints as an integer.
private func projectionBoundText(_ value: Double, format: MetricFormat?) -> String {
    guard value.isFinite else { return Formatting.emDash }
    if let format = format, format.isPercentage {
        return Formatting.value(value, format: format)
    }
    if value.rounded() == value, abs(value) < 1_000_000_000 {
        return Formatting.integer(Int(value))
    }
    return Formatting.decimal(value, places: 1)
}

/// The game a projection is aimed at (`contracts/CONTRACT.md` §4, `next_game_projection.game`).
///
/// The whole object is `nil` on the payload when the player has no scheduled next game; the
/// payload still projects, against a league-average opponent, and says so in `notes`.
public struct ProjectionGame: Codable, Hashable, Sendable, Identifiable {
    public let gameId: GameID
    /// ISO-8601 calendar date in US Eastern, the league's scheduling day.
    public let date: String
    public let opponentAbbr: String?
    public let opponent: TeamRef?
    public let isHome: Bool?
    /// Days since the player's last game, clipped to 0–3 by the rest multiplier (PROJECTION.md §3).
    public let restDays: Int?
    public let isBackToBack: Bool?
    /// The opponent's defensive rating, the input to `f_opp`.
    public let opponentDefRtg: Double?
    /// `Pace_tm · Pace_opp / Pace_lg`, the tempo the two teams are expected to play at.
    public let expectedPace: Double?

    public var id: GameID { gameId }

    /// `"vs BOS"` or `"@ BOS"`.
    public var matchupText: String {
        guard let opponentAbbr = opponentAbbr ?? opponent?.abbr else { return Formatting.emDash }
        return (isHome ?? true) ? "vs \(opponentAbbr)" : "@ \(opponentAbbr)"
    }

    /// `"2 days rest"`, or `"Back-to-back"` when there were none.
    public var restText: String {
        if isBackToBack == true { return "Back-to-back" }
        guard let restDays = restDays else { return Formatting.emDash }
        return restDays == 1 ? "1 day rest" : "\(restDays) days rest"
    }

    public init(gameId: GameID,
                date: String = "",
                opponentAbbr: String? = nil,
                opponent: TeamRef? = nil,
                isHome: Bool? = nil,
                restDays: Int? = nil,
                isBackToBack: Bool? = nil,
                opponentDefRtg: Double? = nil,
                expectedPace: Double? = nil) {
        self.gameId = gameId
        self.date = date
        self.opponentAbbr = opponentAbbr
        self.opponent = opponent
        self.isHome = isHome
        self.restDays = restDays
        self.isBackToBack = isBackToBack
        self.opponentDefRtg = opponentDefRtg
        self.expectedPace = expectedPace
    }

    private enum CodingKeys: String, CodingKey {
        case gameId, date, opponentAbbr, opponent, isHome, restDays, isBackToBack
        case opponentDefRtg, expectedPace
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        gameId = try container.decode(GameID.self, forKey: .gameId)
        date = try container.decodeIfPresent(String.self, forKey: .date) ?? ""
        opponentAbbr = try container.decodeIfPresent(String.self, forKey: .opponentAbbr)
        opponent = try container.decodeIfPresent(TeamRef.self, forKey: .opponent)
        isHome = try container.decodeIfPresent(Bool.self, forKey: .isHome)
        restDays = decodeWholeNumber(container, forKey: .restDays)
        isBackToBack = try container.decodeIfPresent(Bool.self, forKey: .isBackToBack)
        opponentDefRtg = try container.decodeIfPresent(Double.self, forKey: .opponentDefRtg)
        expectedPace = try container.decodeIfPresent(Double.self, forKey: .expectedPace)
    }

    public static let preview = ProjectionGame(
        gameId: "0022500640",
        date: "2026-01-04",
        opponentAbbr: "BOS",
        opponent: TeamRef.previewOpponent,
        isHome: true,
        restDays: 2,
        isBackToBack: false,
        opponentDefRtg: 111.8,
        expectedPace: 99.1
    )
}

/// The projected minutes, `M̂` (`contracts/CONTRACT.md` §4, `docs/PROJECTION.md` §1).
///
/// This is the dominant error term in the whole formulation — supplying *true* minutes improves
/// points RMSE by 19.28% — which is why it is a first-class object rather than one more line, and
/// why §7 rule 2 requires the widget to show it separately and prominently.
public struct ProjectedMinutes: Codable, Hashable, Sendable {
    public let value: Double?
    /// The server's own rendering of `value`, shown verbatim when there is no reason to reformat.
    public let displayValue: String
    /// The EWMA half-life used for minutes, 2 games — far shorter than any production rate,
    /// because a rotation change is a discrete, persistent event.
    public let halfLifeGames: Int?
    public let seasonAverage: Double?
    public let low: Double?
    public let high: Double?

    /// `"27.0–40.5"`, or an em dash when the payload carries no interval.
    public var intervalText: String {
        guard let low = low, let high = high, low.isFinite, high.isFinite else { return Formatting.emDash }
        return "\(Formatting.decimal(low, places: 1))–\(Formatting.decimal(high, places: 1))"
    }

    /// How far the projection sits from the season average, in minutes.
    public var deltaFromSeasonAverage: Double? {
        guard let value = value, let seasonAverage = seasonAverage else { return nil }
        return value - seasonAverage
    }

    public init(value: Double? = nil,
                displayValue: String = Formatting.emDash,
                halfLifeGames: Int? = nil,
                seasonAverage: Double? = nil,
                low: Double? = nil,
                high: Double? = nil) {
        self.value = value
        self.displayValue = displayValue
        self.halfLifeGames = halfLifeGames
        self.seasonAverage = seasonAverage
        self.low = low
        self.high = high
    }

    private enum CodingKeys: String, CodingKey {
        case value, displayValue, halfLifeGames, seasonAverage, low, high
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        value = try container.decodeIfPresent(Double.self, forKey: .value)
        displayValue = try container.decodeIfPresent(String.self, forKey: .displayValue) ?? Formatting.emDash
        halfLifeGames = decodeWholeNumber(container, forKey: .halfLifeGames)
        seasonAverage = try container.decodeIfPresent(Double.self, forKey: .seasonAverage)
        low = try container.decodeIfPresent(Double.self, forKey: .low)
        high = try container.decodeIfPresent(Double.self, forKey: .high)
    }

    public static let preview = ProjectedMinutes(
        value: 34.2,
        displayValue: "34.2",
        halfLifeGames: 2,
        seasonAverage: 33.8,
        low: 27.0,
        high: 40.5
    )
}

/// One projected statistic, mean and interval together (`contracts/CONTRACT.md` §4).
///
/// `availability` is `.estimated` for every line the server sends, and defaults to `.estimated`
/// when the key is absent: a projection is never a record, so this value can never decode to
/// `.full`. `low`/`high` are the negative-binomial bounds at `intervalLevel`, **after** the
/// per-player dispersion multiplier (`docs/PROJECTION.md` §4).
public struct ProjectedLine: Codable, Hashable, Sendable, Identifiable {
    /// The metric key, `"pts"`.
    public let metric: String
    /// The metric's catalog entry. Optional so a descriptor this build cannot read costs the
    /// reader one line's formatting rather than the whole projection; `displayValue` still holds.
    public let descriptor: MetricDescriptor?
    public let mean: Double?
    /// The server's own rendering of `mean`.
    public let displayValue: String
    public let low: Double?
    public let high: Double?
    /// The nominal coverage of `low`/`high`, `0.8` for the paper's 80% interval.
    public let intervalLevel: Double?
    public let seasonAverage: Double?
    public let delta: Double?
    /// `r̂_reg`, the shrunken per-minute production rate.
    public let ratePerMinute: Double?
    /// The stabilisation constant `k = σ²_e / τ²`, in minutes of exposure.
    public let shrinkageK: Double?
    /// Minutes of the player's own history behind the rate.
    public let exposureMinutes: Double?
    /// `n / (n + k)` — how much of the rate is the player rather than the league prior. A low
    /// weight is the "degrade honestly" case of `docs/PROJECTION.md` §7 rule 4.
    public let shrinkageWeight: Double?
    /// The EWMA half-life for this statistic's recent-form term, in games.
    public let halfLifeGames: Int?
    /// The fitted negative-binomial dispersion, `Var = μ + α·μ²`.
    public let dispersionAlpha: Double?
    /// The shrunken per-player variance ratio `ĉ_i`; 1.0 means "no history, league prior".
    public let dispersionMultiplier: Double?
    /// Never `.full`. A projection is not a record, whatever the server called it
    /// (`docs/PROJECTION.md` §7 rule 5); `projectedAvailability(_:)` holds that invariant.
    public let availability: MetricAvailability

    public var id: String { metric }

    /// The short label the widget puts next to the number, `"PTS"`.
    public var shortName: String { descriptor?.shortName ?? metric.uppercased() }

    /// The full metric name, `"Points"`.
    public var name: String { descriptor?.name ?? metric }

    /// `"19–38"`, or an em dash when the payload carries no interval. Never let a mean be shown
    /// without this (`docs/PROJECTION.md` §7 rule 1).
    public var intervalText: String {
        guard let low = low, let high = high, low.isFinite, high.isFinite else { return Formatting.emDash }
        let format = descriptor?.format
        return "\(projectionBoundText(low, format: format))–\(projectionBoundText(high, format: format))"
    }

    /// `"80% interval"`, or `nil` when the payload did not name a level.
    public var intervalLevelText: String? {
        guard let intervalLevel = intervalLevel, intervalLevel.isFinite, intervalLevel > 0 else { return nil }
        return "\(Formatting.percent(intervalLevel, places: 0)) interval"
    }

    /// True when the rate leans mostly on the league prior rather than on this player, which is
    /// the case the widget has to say out loud rather than dress up as a confident number.
    public var leansOnLeaguePrior: Bool {
        guard let shrinkageWeight = shrinkageWeight else { return false }
        return shrinkageWeight < 0.5
    }

    /// Holds the one invariant this type exists to protect: a projected value is never `.full`.
    ///
    /// `MetricAvailability.decoded(…)` is lenient about *unknown* strings but faithful about known
    /// ones, so a server that spelled a projected line `"availability": "full"` — a bug, or an
    /// older build of the service — would decode to `.full`, and `.full` is precisely the value
    /// that renders with no dashed underline and no `est.` marker: a projection dressed as a
    /// record. Clamping it here rather than in the view means every reader of the payload gets the
    /// same answer, including the accessibility labels and anything built on it later.
    private static func projectedAvailability(_ raw: MetricAvailability) -> MetricAvailability {
        raw == .full ? .estimated : raw
    }

    public init(metric: String,
                descriptor: MetricDescriptor? = nil,
                mean: Double? = nil,
                displayValue: String = Formatting.emDash,
                low: Double? = nil,
                high: Double? = nil,
                intervalLevel: Double? = nil,
                seasonAverage: Double? = nil,
                delta: Double? = nil,
                ratePerMinute: Double? = nil,
                shrinkageK: Double? = nil,
                exposureMinutes: Double? = nil,
                shrinkageWeight: Double? = nil,
                halfLifeGames: Int? = nil,
                dispersionAlpha: Double? = nil,
                dispersionMultiplier: Double? = nil,
                availability: MetricAvailability = .estimated) {
        self.metric = metric
        self.descriptor = descriptor
        self.mean = mean
        self.displayValue = displayValue
        self.low = low
        self.high = high
        self.intervalLevel = intervalLevel
        self.seasonAverage = seasonAverage
        self.delta = delta
        self.ratePerMinute = ratePerMinute
        self.shrinkageK = shrinkageK
        self.exposureMinutes = exposureMinutes
        self.shrinkageWeight = shrinkageWeight
        self.halfLifeGames = halfLifeGames
        self.dispersionAlpha = dispersionAlpha
        self.dispersionMultiplier = dispersionMultiplier
        self.availability = ProjectedLine.projectedAvailability(availability)
    }

    private enum CodingKeys: String, CodingKey {
        case metric, descriptor, mean, displayValue, low, high, intervalLevel
        case seasonAverage, delta, ratePerMinute, shrinkageK, exposureMinutes
        case shrinkageWeight, halfLifeGames, dispersionAlpha, dispersionMultiplier, availability
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        metric = try container.decode(String.self, forKey: .metric)
        // A descriptor this build cannot read costs one line its formatting, not the projection.
        if container.contains(.descriptor) {
            descriptor = try? container.decode(MetricDescriptor.self, forKey: .descriptor)
        } else {
            descriptor = nil
        }
        mean = try container.decodeIfPresent(Double.self, forKey: .mean)
        displayValue = try container.decodeIfPresent(String.self, forKey: .displayValue) ?? Formatting.emDash
        low = try container.decodeIfPresent(Double.self, forKey: .low)
        high = try container.decodeIfPresent(Double.self, forKey: .high)
        intervalLevel = try container.decodeIfPresent(Double.self, forKey: .intervalLevel)
        seasonAverage = try container.decodeIfPresent(Double.self, forKey: .seasonAverage)
        delta = try container.decodeIfPresent(Double.self, forKey: .delta)
        ratePerMinute = try container.decodeIfPresent(Double.self, forKey: .ratePerMinute)
        shrinkageK = try container.decodeIfPresent(Double.self, forKey: .shrinkageK)
        exposureMinutes = try container.decodeIfPresent(Double.self, forKey: .exposureMinutes)
        shrinkageWeight = try container.decodeIfPresent(Double.self, forKey: .shrinkageWeight)
        halfLifeGames = decodeWholeNumber(container, forKey: .halfLifeGames)
        dispersionAlpha = try container.decodeIfPresent(Double.self, forKey: .dispersionAlpha)
        dispersionMultiplier = try container.decodeIfPresent(Double.self, forKey: .dispersionMultiplier)
        // A projection is never a record: an absent or unreadable qualifier stays `.estimated`,
        // and an explicit `"full"` is clamped back down rather than rendered as a measured number.
        availability = ProjectedLine.projectedAvailability(
            MetricAvailability.decoded(from: container, forKey: .availability, default: .estimated)
        )
    }
}

/// One multiplicative context factor, normalised so 1.0 means neutral
/// (`docs/PROJECTION.md` §3). Showing these is what turns the projection from an oracle into an
/// argument (§7 rule 3).
public struct ProjectionFactor: Codable, Hashable, Sendable, Identifiable {
    /// `"pace"`, `"opponent"`, `"venue"`, `"rest"` — the keys the resolver emits.
    public let key: String
    public let label: String
    public let value: Double?
    public let explanation: String?
    /// This factor's signed share of each projected statistic, keyed by metric, in that
    /// statistic's own units. Empty when the server did not send it.
    ///
    /// A multiplier of `1.021` means nothing to a reader until it is half a point. The server
    /// derives this as `mean × (value − 1)` and the contract is explicit that it is a
    /// **first-order attribution, not a decomposition** — the factors multiply, so the column
    /// does not add up. `contributionsCaption` is written to say so wherever these are shown.
    public let contributions: [String: Double]

    public var id: String { key }

    /// How far the factor moves the projection, as a signed percentage: `"+2.1%"`.
    public var effectText: String {
        guard let value = value, value.isFinite else { return Formatting.emDash }
        return Formatting.decimal((value - 1) * 100, places: 1, signed: true) + "%"
    }

    /// True for a factor that neither helps nor hurts, within a tenth of a percent.
    public var isNeutral: Bool {
        guard let value = value, value.isFinite else { return true }
        return abs(value - 1) < 0.001
    }

    /// This factor's contribution to one statistic, as a signed number in that statistic's
    /// units: `"+1.6"`. `nil` when the server sent no contribution for that metric, which is
    /// not the same as zero and must not be drawn as one.
    public func contributionText(for metric: String, format: MetricFormat? = nil) -> String? {
        guard let contribution = contributions[metric], contribution.isFinite else { return nil }
        if let format = format, format.isPercentage {
            return Formatting.value(contribution, format: format)
        }
        return Formatting.decimal(contribution, places: 1, signed: true)
    }

    public init(key: String,
                label: String,
                value: Double? = nil,
                explanation: String? = nil,
                contributions: [String: Double] = [:]) {
        self.key = key
        self.label = label
        self.value = value
        self.explanation = explanation
        self.contributions = contributions
    }

    private enum CodingKeys: String, CodingKey {
        case key, label, value, explanation, contributions
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        key = try container.decode(String.self, forKey: .key)
        label = try container.decodeIfPresent(String.self, forKey: .label) ?? key
        value = try container.decodeIfPresent(Double.self, forKey: .value)
        explanation = try container.decodeIfPresent(String.self, forKey: .explanation)
        // A server old enough not to send these, or one that sent something unreadable, costs
        // the reader the "Why" numbers — never the projection they sit under.
        let rawContributions = (try? container.decodeIfPresent([String: Double].self, forKey: .contributions)) ?? nil
        contributions = rawContributions ?? [:]
    }
}

public extension Array where Element == ProjectionFactor {
    /// The factors that actually moved this statistic, largest first.
    ///
    /// "Largest movers", not "the breakdown". The factors are multiplicative, so these do not
    /// sum to the difference between the projection and a neutral-context one, and presenting
    /// them as a total would be arithmetic the reader could check and find wrong.
    func largestMovers(for metric: String, limit: Int = 3) -> [ProjectionFactor] {
        filter { factor in
            guard let contribution = factor.contributions[metric] else { return false }
            return contribution.isFinite && abs(contribution) >= 0.05
        }
        .sorted { abs($0.contributions[metric] ?? 0) > abs($1.contributions[metric] ?? 0) }
        .prefix(limit)
        .map { $0 }
    }
}

/// The PTS+REB+AST combination line (`contracts/CONTRACT.md` §4, `docs/PROJECTION.md` §5).
///
/// The residuals are positively dependent, so `Var(ΣS) = σᵀCσ` rather than `Σσ²`.
/// `sdIfIndependent` is carried so the client can show what ignoring that would have claimed —
/// the gap is the interesting part, and on held-out data it understates the spread by 12.8%.
public struct ProjectionCombo: Codable, Hashable, Sendable {
    public let label: String
    public let mean: Double?
    /// The correlated standard deviation, `√(σᵀCσ)`.
    public let sd: Double?
    /// `√(Σσ²)` — what a naive sum of independent parts would have claimed.
    public let sdIfIndependent: Double?
    /// `sd / sdIfIndependent - 1`, the fraction by which correlation widens the spread.
    public let inflation: Double?
    public let low: Double?
    public let high: Double?
    /// The residual correlation matrix, row-major, in the order of the combination's parts.
    public let correlation: [[Double]]

    /// `"33–57"`, or an em dash when the payload carries no interval.
    public var intervalText: String {
        guard let low = low, let high = high, low.isFinite, high.isFinite else { return Formatting.emDash }
        return "\(projectionBoundText(low, format: nil))–\(projectionBoundText(high, format: nil))"
    }

    /// `"+12.7%"` — how much wider the honest interval is than the independent one.
    public var inflationText: String {
        guard let inflation = inflation, inflation.isFinite else { return Formatting.emDash }
        return Formatting.decimal(inflation * 100, places: 1, signed: true) + "%"
    }

    public init(label: String,
                mean: Double? = nil,
                sd: Double? = nil,
                sdIfIndependent: Double? = nil,
                inflation: Double? = nil,
                low: Double? = nil,
                high: Double? = nil,
                correlation: [[Double]] = []) {
        self.label = label
        self.mean = mean
        self.sd = sd
        self.sdIfIndependent = sdIfIndependent
        self.inflation = inflation
        self.low = low
        self.high = high
        self.correlation = correlation
    }

    private enum CodingKeys: String, CodingKey {
        case label, mean, sd, sdIfIndependent, inflation, low, high, correlation
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        label = try container.decodeIfPresent(String.self, forKey: .label) ?? Formatting.emDash
        mean = try container.decodeIfPresent(Double.self, forKey: .mean)
        sd = try container.decodeIfPresent(Double.self, forKey: .sd)
        sdIfIndependent = try container.decodeIfPresent(Double.self, forKey: .sdIfIndependent)
        inflation = try container.decodeIfPresent(Double.self, forKey: .inflation)
        low = try container.decodeIfPresent(Double.self, forKey: .low)
        high = try container.decodeIfPresent(Double.self, forKey: .high)
        correlation = try container.decodeIfPresent([[Double]].self, forKey: .correlation) ?? []
    }

    public static let preview = ProjectionCombo(
        label: "PTS+REB+AST",
        mean: 45.1,
        sd: 9.21,
        sdIfIndependent: 8.17,
        inflation: 0.127,
        low: 33,
        high: 57,
        correlation: [[1.0, 0.3, 0.17], [0.3, 1.0, 0.2], [0.17, 0.2, 1.0]]
    )
}

/// What produced the numbers, in the payload's own words (`contracts/CONTRACT.md` §4).
public struct ProjectionMethod: Codable, Hashable, Sendable {
    public let summary: String?
    /// 2 games — the minutes model's own decay, far faster than any production rate.
    public let minutesHalfLifeGames: Int?
    /// Whether the combination interval used the residual correlation matrix. Defaults to
    /// `false`, which is the cautious direction: the widget then says the combined spread may be
    /// understated rather than silently claiming an interval it cannot vouch for.
    public let correlationApplied: Bool
    /// `k_c`, the prior weight on the per-player dispersion multiplier, in games.
    public let dispersionShrinkageGames: Int?

    public init(summary: String? = nil,
                minutesHalfLifeGames: Int? = nil,
                correlationApplied: Bool = false,
                dispersionShrinkageGames: Int? = nil) {
        self.summary = summary
        self.minutesHalfLifeGames = minutesHalfLifeGames
        self.correlationApplied = correlationApplied
        self.dispersionShrinkageGames = dispersionShrinkageGames
    }

    private enum CodingKeys: String, CodingKey {
        case summary, minutesHalfLifeGames, correlationApplied, dispersionShrinkageGames
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        summary = try container.decodeIfPresent(String.self, forKey: .summary)
        minutesHalfLifeGames = decodeWholeNumber(container, forKey: .minutesHalfLifeGames)
        correlationApplied = try container.decodeIfPresent(Bool.self, forKey: .correlationApplied) ?? false
        dispersionShrinkageGames = decodeWholeNumber(container, forKey: .dispersionShrinkageGames)
    }

    public static let preview = ProjectionMethod(
        summary: "Opportunity x rate, shrunk per statistic.",
        minutesHalfLifeGames: 2,
        correlationApplied: true,
        dispersionShrinkageGames: 60
    )
}

/// `next_game_projection` (`contracts/CONTRACT.md` §4).
///
/// A projected box score: `Ŝ = M̂ · r̂_reg · f_pace · f_opp · f_home · f_rest`. Nothing here is a
/// record — every line carries `availability: "estimated"` and renders with the same treatment as
/// a pre-1997 derived stat. There is deliberately no market translation: no implied probability,
/// no expected value, no staking (`docs/PROJECTION.md` §6).
public struct NextGameProjectionPayload: Codable, Hashable, Sendable {
    public let player: PlayerRef
    /// `nil` when the player has no scheduled next game. The payload still projects, against a
    /// league-average opponent, and says so in `notes`.
    public let game: ProjectionGame?
    public let projectedMinutes: ProjectedMinutes
    public let lines: [ProjectedLine]
    public let factors: [ProjectionFactor]
    /// `nil` when the reader turned the combination line off, or when the payload has fewer than
    /// two of its parts.
    public let combo: ProjectionCombo?
    public let method: ProjectionMethod
    public let notes: [String]

    /// The projected line for a metric key, when the payload carries one.
    public func line(_ metric: String) -> ProjectedLine? {
        lines.first { $0.metric == metric }
    }

    /// The context factor for a key such as `"pace"`, when the payload carries one.
    public func factor(_ key: String) -> ProjectionFactor? {
        factors.first { $0.key == key }
    }

    /// True when at least one line is thin enough on the player's own history that the widget
    /// has to say so (`docs/PROJECTION.md` §7 rule 4).
    public var hasThinExposure: Bool {
        lines.contains { $0.leansOnLeaguePrior }
    }

    public init(player: PlayerRef,
                game: ProjectionGame? = nil,
                projectedMinutes: ProjectedMinutes = ProjectedMinutes(),
                lines: [ProjectedLine] = [],
                factors: [ProjectionFactor] = [],
                combo: ProjectionCombo? = nil,
                method: ProjectionMethod = ProjectionMethod(),
                notes: [String] = []) {
        self.player = player
        self.game = game
        self.projectedMinutes = projectedMinutes
        self.lines = lines
        self.factors = factors
        self.combo = combo
        self.method = method
        self.notes = notes
    }

    private enum CodingKeys: String, CodingKey {
        case player, game, projectedMinutes, lines, factors, combo, method, notes
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        player = try container.decode(PlayerRef.self, forKey: .player)
        game = try container.decodeIfPresent(ProjectionGame.self, forKey: .game)
        projectedMinutes = try container.decodeIfPresent(ProjectedMinutes.self, forKey: .projectedMinutes) ?? ProjectedMinutes()
        lines = try container.decodeIfPresent([ProjectedLine].self, forKey: .lines) ?? []
        factors = try container.decodeIfPresent([ProjectionFactor].self, forKey: .factors) ?? []
        combo = try container.decodeIfPresent(ProjectionCombo.self, forKey: .combo)
        method = try container.decodeIfPresent(ProjectionMethod.self, forKey: .method) ?? ProjectionMethod()
        notes = try container.decodeIfPresent([String].self, forKey: .notes) ?? []
    }

    // MARK: Preview

    private static let pointsDescriptor = MetricDescriptor(
        key: "pts",
        name: "Points",
        shortName: "PTS",
        category: "volume",
        format: .decimal1,
        higherIsBetter: true,
        scope: ["player", "team"],
        availability: MetricDescriptor.Availability(seasonFrom: "1946-47", perGameFrom: "1946-47", seasonLevelOnly: false, estimatedBefore: nil),
        domain: nil,
        glossary: "Points scored."
    )

    private static let reboundsDescriptor = MetricDescriptor(
        key: "reb",
        name: "Rebounds",
        shortName: "REB",
        category: "volume",
        format: .decimal1,
        higherIsBetter: true,
        scope: ["player", "team"],
        availability: MetricDescriptor.Availability(seasonFrom: "1950-51", perGameFrom: "1950-51", seasonLevelOnly: false, estimatedBefore: nil),
        domain: nil,
        glossary: "Total rebounds. Offensive/defensive split only from 1973-74."
    )

    private static let assistsDescriptor = MetricDescriptor(
        key: "ast",
        name: "Assists",
        shortName: "AST",
        category: "volume",
        format: .decimal1,
        higherIsBetter: true,
        scope: ["player", "team"],
        availability: MetricDescriptor.Availability(seasonFrom: "1946-47", perGameFrom: "1946-47", seasonLevelOnly: false, estimatedBefore: nil),
        domain: nil,
        glossary: "Assists."
    )

    public static let preview = NextGameProjectionPayload(
        player: .preview,
        game: ProjectionGame.preview,
        projectedMinutes: ProjectedMinutes.preview,
        lines: [
            ProjectedLine(metric: "pts",
                          descriptor: NextGameProjectionPayload.pointsDescriptor,
                          mean: 28.4, displayValue: "28.4",
                          low: 19, high: 38, intervalLevel: 0.8,
                          seasonAverage: 27.1, delta: 1.3,
                          ratePerMinute: 0.831, shrinkageK: 81, exposureMinutes: 1240.0,
                          shrinkageWeight: 0.94, halfLifeGames: 6,
                          dispersionAlpha: 0.061, dispersionMultiplier: 1.34,
                          availability: .estimated),
            ProjectedLine(metric: "reb",
                          descriptor: NextGameProjectionPayload.reboundsDescriptor,
                          mean: 7.8, displayValue: "7.8",
                          low: 4, high: 12, intervalLevel: 0.8,
                          seasonAverage: 7.4, delta: 0.4,
                          ratePerMinute: 0.228, shrinkageK: 34, exposureMinutes: 1240.0,
                          shrinkageWeight: 0.97, halfLifeGames: 8,
                          dispersionAlpha: 0.094, dispersionMultiplier: 1.08,
                          availability: .estimated),
            ProjectedLine(metric: "ast",
                          descriptor: NextGameProjectionPayload.assistsDescriptor,
                          mean: 8.9, displayValue: "8.9",
                          low: 5, high: 13, intervalLevel: 0.8,
                          seasonAverage: 8.6, delta: 0.3,
                          ratePerMinute: 0.260, shrinkageK: 36, exposureMinutes: 1240.0,
                          shrinkageWeight: 0.97, halfLifeGames: 8,
                          dispersionAlpha: 0.118, dispersionMultiplier: 1.12,
                          availability: .estimated)
        ],
        factors: [
            ProjectionFactor(key: "pace", label: "Pace", value: 1.021,
                             explanation: "Both teams play slightly faster than league average.",
                             contributions: ["pts": 0.60, "reb": 0.16, "ast": 0.19]),
            ProjectionFactor(key: "opponent", label: "Opponent", value: 0.981,
                             explanation: "Boston's defence is better than league average, which costs about 2%.",
                             contributions: ["pts": -0.54, "reb": -0.15, "ast": -0.17]),
            ProjectionFactor(key: "venue", label: "Venue", value: 1.014,
                             explanation: "Home games run a little higher than road games.",
                             contributions: ["pts": 0.40, "reb": 0.11, "ast": 0.12]),
            ProjectionFactor(key: "rest", label: "Rest", value: 1.003,
                             explanation: "Two days of rest, which is close to neutral.",
                             contributions: ["pts": 0.09, "reb": 0.02, "ast": 0.03])
        ],
        combo: ProjectionCombo.preview,
        method: ProjectionMethod.preview,
        notes: [
            "Projected minutes carry most of the error. Disagree with 34.2 and the rest moves with it.",
            "Every number here is an estimate, not a record."
        ]
    )
}

// MARK: - projection_board

/// One row of the Fantasy Board: a player, a statistic, and the range it is projected into
/// (`contracts/CONTRACT.md` §4, `projection_board.rows[]`).
///
/// **There is no line, price or edge here, and there must never be one.** The design this was
/// built from drew each band against a sportsbook line; `referenceValue` is the player's own
/// season average instead. `docs/BROADSHEET.md` §1 has the reasoning, `docs/PROJECTION.md` §6 the
/// licensing that settles it.
public struct ProjectionBoardRow: Codable, Hashable, Sendable, Identifiable {
    public let player: PlayerRef
    /// `"LAL @ DEN"` or `"BOS vs NYK"`, as the server composed it.
    public let matchup: String?
    public let opponentAbbr: String?
    public let isHome: Bool?
    public let gameId: GameID?
    /// The scheduling day of the game being projected, which is *not* the slate the players were
    /// chosen from. See `ProjectionBoardPayload`.
    public let gameDate: String?
    public let metric: String
    public let descriptor: MetricDescriptor?
    public let projection: Double?
    public let displayValue: String
    /// The 10th and 90th percentile of the predictive distribution — the band.
    public let low: Double?
    public let high: Double?
    public let intervalLevel: Double?
    /// The mark the band is read against: the player's own average. Never a market line.
    public let referenceValue: Double?
    public let delta: Double?
    /// `delta` in units of the projection's own spread. This is the board's sort key, because a
    /// raw delta is not comparable across statistics — see `docs/BROADSHEET.md` §6.
    public let deltaZ: Double?
    public let projectedMinutes: Double?
    public let availability: MetricAvailability

    /// Unique within one board: a player can appear once per statistic.
    public var id: String { "\(player.playerId).\(metric)" }

    public var metricLabel: String {
        descriptor?.shortName ?? descriptor?.name ?? metric.uppercased()
    }

    /// `"31.5"` — the reference mark, formatted the way the metric formats.
    public var referenceText: String? {
        guard let referenceValue = referenceValue, referenceValue.isFinite else { return nil }
        return Formatting.value(referenceValue, format: descriptor?.format ?? .decimal1)
    }

    /// `"+2.7"`, or `nil` when there is no mark to compare against.
    public var deltaText: String? {
        guard let delta = delta, delta.isFinite else { return nil }
        return Formatting.decimal(delta, places: 1, signed: true)
    }

    /// The bounds as whole counts, because a single game's interval is a count and `19.0–38.0`
    /// would claim a precision it does not have.
    public var lowText: String { low.map { projectionBoundText($0, format: descriptor?.format) } ?? Formatting.emDash }
    public var highText: String { high.map { projectionBoundText($0, format: descriptor?.format) } ?? Formatting.emDash }

    /// True when the projection sits above the player's own average. `nil` with no reference, and
    /// `nil` is drawn as neither colour rather than as "below".
    public var isAboveReference: Bool? {
        guard let delta = delta, delta.isFinite, abs(delta) > 1e-9 else { return nil }
        return delta > 0
    }

    public init(player: PlayerRef,
                matchup: String? = nil,
                opponentAbbr: String? = nil,
                isHome: Bool? = nil,
                gameId: GameID? = nil,
                gameDate: String? = nil,
                metric: String,
                descriptor: MetricDescriptor? = nil,
                projection: Double? = nil,
                displayValue: String = Formatting.emDash,
                low: Double? = nil,
                high: Double? = nil,
                intervalLevel: Double? = nil,
                referenceValue: Double? = nil,
                delta: Double? = nil,
                deltaZ: Double? = nil,
                projectedMinutes: Double? = nil,
                availability: MetricAvailability = .estimated) {
        self.player = player
        self.matchup = matchup
        self.opponentAbbr = opponentAbbr
        self.isHome = isHome
        self.gameId = gameId
        self.gameDate = gameDate
        self.metric = metric
        self.descriptor = descriptor
        self.projection = projection
        self.displayValue = displayValue
        self.low = low
        self.high = high
        self.intervalLevel = intervalLevel
        self.referenceValue = referenceValue
        self.delta = delta
        self.deltaZ = deltaZ
        self.projectedMinutes = projectedMinutes
        self.availability = ProjectionBoardRow.projectedAvailability(availability)
    }

    /// A projection is never a record (`docs/PROJECTION.md` §7 rule 5), so `.full` is not a value
    /// this type can hold — however the server spelled it, and however a caller constructs one.
    /// Same rule and same implementation as `ProjectedLine`; a board row is a projected line.
    private static func projectedAvailability(_ raw: MetricAvailability) -> MetricAvailability {
        raw == .full ? .estimated : raw
    }

    private enum CodingKeys: String, CodingKey {
        case player, matchup, opponentAbbr, isHome, gameId, gameDate, metric, descriptor
        case projection, displayValue, low, high, intervalLevel
        case referenceValue, delta, deltaZ, projectedMinutes, availability
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        player = try container.decode(PlayerRef.self, forKey: .player)
        matchup = try container.decodeIfPresent(String.self, forKey: .matchup)
        opponentAbbr = try container.decodeIfPresent(String.self, forKey: .opponentAbbr)
        isHome = try container.decodeIfPresent(Bool.self, forKey: .isHome)
        gameId = try container.decodeIfPresent(GameID.self, forKey: .gameId)
        gameDate = try container.decodeIfPresent(String.self, forKey: .gameDate)
        metric = try container.decode(String.self, forKey: .metric)
        descriptor = try container.decodeIfPresent(MetricDescriptor.self, forKey: .descriptor)
        projection = try container.decodeIfPresent(Double.self, forKey: .projection)
        displayValue = try container.decodeIfPresent(String.self, forKey: .displayValue) ?? Formatting.emDash
        low = try container.decodeIfPresent(Double.self, forKey: .low)
        high = try container.decodeIfPresent(Double.self, forKey: .high)
        intervalLevel = try container.decodeIfPresent(Double.self, forKey: .intervalLevel)
        referenceValue = try container.decodeIfPresent(Double.self, forKey: .referenceValue)
        delta = try container.decodeIfPresent(Double.self, forKey: .delta)
        deltaZ = try container.decodeIfPresent(Double.self, forKey: .deltaZ)
        projectedMinutes = try container.decodeIfPresent(Double.self, forKey: .projectedMinutes)
        // A projection is never a record, so an absent *or* unreadable marker is `estimated`,
        // and a server that said `full` is overruled rather than believed.
        let rawAvailability = (try? container.decodeIfPresent(MetricAvailability.self, forKey: .availability)) ?? nil
        availability = ProjectionBoardRow.projectedAvailability(rawAvailability ?? .estimated)
    }
}

/// The Fantasy Board (`contracts/CONTRACT.md` §4, `projection_board`).
///
/// **Three dates, because there are three.** The server picks its players off the last completed
/// slate (`selectionDate`) and projects each of them forward to whatever *they* play next, which
/// need not be one shared night — hence `date` and `throughDate`. Heading the board with
/// `selectionDate` would put last night's date above tomorrow night's numbers, so this type
/// exposes `dateHeadline`, which is built from the right two.
public struct ProjectionBoardPayload: Codable, Hashable, Sendable {
    /// The earliest game being projected: the board's "tonight".
    public let date: String
    /// The latest, when the rows span more than one night. `nil` when they do not.
    public let throughDate: String?
    /// The completed slate the candidate players were taken from.
    public let selectionDate: String?
    /// Distinct games among `rows`, not games on the slate.
    public let gameCount: Int
    /// `"season_average"`, `"career_average"` or `"none"`.
    public let reference: String
    /// What to call the mark on screen: `"season avg"`. `nil` when there is no mark.
    public let referenceLabel: String?
    public let rows: [ProjectionBoardRow]
    /// The server's sentence about what the band is. Rendered verbatim; it is careful about
    /// saying the interval is analytic rather than simulated.
    public let note: String?

    /// `"Sun, Jan 4"`, or `"Sun, Jan 4 – Mon, Jan 5"` when the rows span two nights.
    public var dateHeadline: String {
        let first = Formatting.mediumGameDate(date)
        guard let throughDate = throughDate, throughDate != date, !throughDate.isEmpty else {
            return first
        }
        return "\(first) – \(Formatting.mediumGameDate(throughDate))"
    }

    /// `"4 games"`. Kept separate from the date so a caller can put them on different lines.
    public var gameCountText: String {
        gameCount == 1 ? "1 game" : "\(gameCount) games"
    }

    /// True when the board has a mark to draw the tick at.
    public var hasReference: Bool { reference != "none" && referenceLabel != nil }

    public init(date: String,
                throughDate: String? = nil,
                selectionDate: String? = nil,
                gameCount: Int = 0,
                reference: String = "season_average",
                referenceLabel: String? = "season avg",
                rows: [ProjectionBoardRow] = [],
                note: String? = nil) {
        self.date = date
        self.throughDate = throughDate
        self.selectionDate = selectionDate
        self.gameCount = gameCount
        self.reference = reference
        self.referenceLabel = referenceLabel
        self.rows = rows
        self.note = note
    }

    private enum CodingKeys: String, CodingKey {
        case date, throughDate, selectionDate, gameCount, reference, referenceLabel, rows, note
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        date = try container.decodeIfPresent(String.self, forKey: .date) ?? ""
        throughDate = try container.decodeIfPresent(String.self, forKey: .throughDate)
        selectionDate = try container.decodeIfPresent(String.self, forKey: .selectionDate)
        gameCount = decodeWholeNumber(container, forKey: .gameCount) ?? 0
        reference = try container.decodeIfPresent(String.self, forKey: .reference) ?? "season_average"
        referenceLabel = try container.decodeIfPresent(String.self, forKey: .referenceLabel)
        rows = try container.decodeIfPresent([ProjectionBoardRow].self, forKey: .rows) ?? []
        note = try container.decodeIfPresent(String.self, forKey: .note)
    }

    // MARK: Preview

    public static let preview: ProjectionBoardPayload = {
        let lines = NextGameProjectionPayload.preview.lines
        let rows: [ProjectionBoardRow] = lines.map { line in
            // The same arithmetic the server does: an 80% interval is 2·z(0.9) wide, so its
            // width divided by 2.5632 recovers the spread the delta is standardised against.
            let spread = ((line.high ?? 0) - (line.low ?? 0)) / (2 * 1.2816)
            let standardised: Double? = spread > 0 ? (line.delta ?? 0) / spread : nil
            return ProjectionBoardRow(
                player: .preview,
                matchup: "LAL @ DEN",
                opponentAbbr: "DEN",
                isHome: false,
                gameId: "0022500640",
                gameDate: "2026-01-04",
                metric: line.metric,
                descriptor: line.descriptor,
                projection: line.mean,
                displayValue: line.displayValue,
                low: line.low,
                high: line.high,
                intervalLevel: 0.8,
                referenceValue: line.seasonAverage,
                delta: line.delta,
                deltaZ: standardised,
                projectedMinutes: 34.2,
                availability: .estimated
            )
        }
        return ProjectionBoardPayload(
            date: "2026-01-04",
            throughDate: nil,
            selectionDate: "2026-01-02",
            gameCount: 1,
            reference: "season_average",
            referenceLabel: "season avg",
            rows: rows,
            note: "The band is the model's 10th to 90th percentile from a negative binomial fit; the dot is the projection."
        )
    }()
}
