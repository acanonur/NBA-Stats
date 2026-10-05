import Foundation

// The payloads of the league routes (`/v1/...` for the NBA, `/v1/el/...` for the EuroLeague) and
// of the four widget kinds that carry them (`team_matchup`, `defense_by_position`,
// `availability_report`, `slate_projections`).
//
// WHY ONE FILE, AND WHY EVERY PROPERTY IS OPTIONAL
// These types were written field for field against the recorded bodies in
// `contracts/fixtures/leagues/{nba,el}/*.json` and `contracts/fixtures/widget_<kind>.json`, not
// against the design's prose, which guessed several shapes the backend then settled differently.
// `scripts/check_swift_portability.py` rule P14 re-reads every one of those fixtures and fails if a
// key in it has no property here (or has a type that cannot hold it), so a backend change that
// adds a key is a red lint on Linux, not a silent gap found in Xcode.
//
// Every stored property is Optional, arrays included, because one key a server leaves out must
// never cost a whole screen: a payload that fails to decode shows an error where a payload with a
// missing number shows an em dash. The memberwise initialiser of an all-Optional struct gives every
// property a `nil` default, so `LeagueMatchup()` compiles and a preview is one line.
//
// RULES THE LINT ENFORCES (P10, P13)
// - Every type is `public struct X: Codable, Hashable, Sendable`. They have to be public because
//   `WidgetPayload` is public and its associated types must be at least as visible as it is.
// - Synthesized Codable only: no `init(from:)`, no `CodingKeys`. The JSON keys are the property
//   names, and the shared decoder sets no key strategy.
// - Enum-like values (status, state, band, kind, scheme) are `String?`, never a Swift enum, so a
//   value a newer server invents renders as its raw string instead of failing the decode.
//   `LeagueFormatting` turns the ones the backend defines into words.
// - Statistics, averages, indices, fractions, minutes, ages: `Double?`. `Int?` is only for whole
//   facts the backend writes as Python ints (ids, `syncVersion`, `round`, scores, counts), because
//   a JSON `6.0` into an `Int` throws (see Core/ProjectionPayload.swift).
// - Timestamps and calendar days are `String?`, never `Date`: the shared decoder's date strategy
//   throws on a literal it cannot parse, and that would cost the whole payload. They are parsed
//   only for display, by `LeagueFormatting`.
// - A shape the backend does not pin down is `JSONValue?` (Core/JSONValue.swift), which also
//   absorbs `null`.
// - A percentage is a fraction in [0, 1]; a missing number is an em dash, never 0. Neither rule is
//   applied here: these types hold what the server sent, and formatting is `LeagueFormatting`'s.
//
// NAMES
// `League...` is shared by both leagues, `El...` is EuroLeague-only, `Nba...` is NBA-only. The
// prefix keeps clear of the types that already exist (`TeamRef`, `PlayerRef`, `GameRef`,
// `GameStatus`, `Coverage`) and of SwiftUI's own (`Table`, `Section`, `Label`, `Group`).

// MARK: - Common objects (backend shared/refs.py)

/// A team in either league. `id` is the NBA numeric id as a string, or the EuroLeague club code;
/// clients key entities by (`league`, `id`). `teamId` is NBA-only, `clubCode` and `tvCode` are
/// EuroLeague-only.
public struct LeagueTeamRef: Codable, Hashable, Sendable {
    public var league: String?
    public var id: String?
    public var abbr: String?
    public var name: String?
    public var shortName: String?
    public var teamId: Int?
    public var clubCode: String?
    public var tvCode: String?
}

/// A player in either league. `position` is G, F or C or nil; `positionRaw` is the source's own
/// label. `playerId` is NBA-only and `personCode` EuroLeague-only.
public struct LeaguePlayerRef: Codable, Hashable, Sendable {
    public var league: String?
    public var id: String?
    public var name: String?
    public var position: String?
    public var positionRaw: String?
    public var jersey: String?
    public var headshotUrl: String?
    public var playerId: Int?
    public var personCode: String?
}

/// Where a status or a headline came from. `kind` is one of leagueReport, clubStatement,
/// pressArticle, boxScoreInference, workbookImport, manual. `publishedAt` is when the source said
/// it; `fetchedAt` is when Hardwood read it, and is never the age a reader is shown.
public struct LeagueSource: Codable, Hashable, Sendable {
    public var kind: String?
    public var label: String?
    public var url: String?
    public var publishedAt: String?
    public var asOf: String?
    public var fetchedAt: String?
    public var snapshotId: Int?
}

/// One element of `LeagueFreshness.sources`: a source's key, label, state and reason.
public struct LeagueSourceState: Codable, Hashable, Sendable {
    public var key: String?
    public var label: String?
    public var state: String?
    public var reason: String?
    public var lastSuccessAt: String?
}

/// The block every league payload carries: how current it is, whether it is invented, and the
/// state of every source behind it.
public struct LeagueFreshness: Codable, Hashable, Sendable {
    public var league: String?
    public var syncVersion: Int?
    public var dataThrough: String?
    public var generatedAt: String?
    public var isDemo: Bool?
    public var sources: [LeagueSourceState]?
}

/// A game. `date` is the league's own scheduling day (NBA: US Eastern, EuroLeague: Berlin) and is
/// never compared across leagues. `status` is scheduled, resultPending, final or postponed;
/// resultPending means the game should be over and no result is stored yet, which is not the same
/// as upcoming. The scores are nil until the game is final. `isNeutral` nil means unknown, not
/// false.
public struct LeagueGameRef: Codable, Hashable, Sendable {
    public var league: String?
    public var gameId: String?
    public var date: String?
    public var tipoffUtc: String?
    public var venue: String?
    public var isNeutral: Bool?
    public var round: Int?
    public var phase: String?
    public var status: String?
    public var home: LeagueTeamRef?
    public var away: LeagueTeamRef?
    public var homePts: Int?
    public var awayPts: Int?
    public var overtimePeriods: Int?
}

/// One element of the bare array `GET /v1/leagues` answers: a league the server serves, where its
/// routes live and what state it is in. `apiPrefix` is "/v1" or "/v1/el"; a client never hard-codes
/// the second.
public struct LeagueInfo: Codable, Hashable, Sendable {
    public var key: String?
    public var name: String?
    public var apiPrefix: String?
    public var enabled: Bool?
    public var state: String?
    public var reason: String?
    public var isDemo: Bool?
    public var currentSeason: String?
    public var syncVersion: Int?
    public var dataThrough: String?
    public var regulationMinutes: Int?
    public var perModes: [String]?
    public var positionBuckets: [String]?
    public var features: [String]?
}

// MARK: - Matchup (`GET {prefix}/teams/{id}/matchup`, `/matchups`, `/games/{id}/matchup`)

/// Two teams side by side, with the game they are about to play, the league's average and,
/// when the server has one, a projection.
public struct LeagueMatchup: Codable, Hashable, Sendable {
    public var league: String?
    public var season: String?
    public var seasonCode: String?
    public var seasonType: String?
    public var phase: JSONValue?
    public var freshness: LeagueFreshness?
    public var game: LeagueGameRef?
    public var teams: [LeagueTeamForm]?
    public var leagueAverage: LeagueAverage?
    public var projection: LeagueGameProjection?
    public var availability: String?
    public var notes: [String]?
}

/// One side of a matchup, and also `ElClubView.scoring`: record, scoring and points allowed per
/// game, the latest score, recent form, venue splits, opponent-adjusted values, who is missing,
/// and a one-line defence summary.
public struct LeagueTeamForm: Codable, Hashable, Sendable {
    public var side: String?
    public var team: LeagueTeamRef?
    public var record: TeamRecord?
    public var games: Int?
    public var pointsPerGame: Double?
    public var pointsAllowedPerGame: Double?
    public var differentialPerGame: Double?
    public var pointsPerRegulation: Double?
    public var pointsAllowedPerRegulation: Double?
    public var latestGame: LeagueFormGame?
    public var form: [LeagueFormGame]?
    public var lastN: LeagueWindowAverage?
    public var last10: LeagueWindowAverage?
    public var venueSplits: LeagueVenueSplits?
    public var adjustedPointsAgainst: LeagueAdjusted?
    public var adjustedPointsFor: LeagueAdjusted?
    public var availability: LeagueAvailabilitySummary?
    public var defenseSummary: LeagueDefenseSummary?
}

/// A finished game from one team's point of view. `result` is "W" or "L".
public struct LeagueFormGame: Codable, Hashable, Sendable {
    public var gameId: String?
    public var date: String?
    public var opponent: LeagueTeamRef?
    public var isHome: Bool?
    public var isNeutral: Bool?
    public var teamScore: Int?
    public var opponentScore: Int?
    public var result: String?
    public var overtimePeriods: Int?
}

/// Scoring and points allowed over a window of recent games (`lastN`, `last10`).
public struct LeagueWindowAverage: Codable, Hashable, Sendable {
    public var window: Int?
    public var games: Int?
    public var pointsPerGame: Double?
    public var pointsAllowedPerGame: Double?
}

/// Scoring and points allowed in one venue kind.
public struct LeagueSplit: Codable, Hashable, Sendable {
    public var games: Int?
    public var pointsPerGame: Double?
    public var pointsAllowedPerGame: Double?
}

/// Home, away and neutral splits. A kind the team has not played is nil.
public struct LeagueVenueSplits: Codable, Hashable, Sendable {
    public var home: LeagueSplit?
    public var away: LeagueSplit?
    public var neutral: LeagueSplit?
}

/// An opponent-adjusted value. `value` is nil when too few games qualify.
public struct LeagueAdjusted: Codable, Hashable, Sendable {
    public var value: Double?
    public var games: Int?
}

/// The league's average points per game, over this many teams.
public struct LeagueAverage: Codable, Hashable, Sendable {
    public var pointsPerGame: Double?
    public var teams: Int?
}

/// Counts of who is out, doubtful, questionable or probable for a team, and its most costly
/// absences. The counts are whole numbers (an object here, unlike `LeagueMatchup.availability`).
public struct LeagueAvailabilitySummary: Codable, Hashable, Sendable {
    public var out: Int?
    public var doubtful: Int?
    public var questionable: Int?
    public var probable: Int?
    public var keyAbsences: [LeagueAbsence]?
    public var freshnessState: String?
    public var asOf: String?

    /// A count for display: the number itself, or nil (which renders as an em dash). Kept as a
    /// function so a caller written against the design, which allowed a list here, still compiles.
    public static func count(_ value: Int?) -> Int? {
        value
    }
}

/// A team's points allowed, with the per-position buckets only as a summary (the full breakdown is
/// `LeagueDefenseDocument`).
public struct LeagueDefenseSummary: Codable, Hashable, Sendable {
    public var pointsAllowedPerGame: Double?
    public var buckets: [LeagueDefenseBucket]?
    public var withheld: LeagueWithheld?
}

// MARK: - Defence by opponent position

/// One struct for the team route, the league table route and the polymorphic widget payload. The
/// team route fills `team` and `buckets`; the table route fills `teams`.
public struct LeagueDefenseDocument: Codable, Hashable, Sendable {
    public var league: String?
    public var team: LeagueTeamRef?
    public var teams: [LeagueDefenseTeamRow]?
    public var season: String?
    public var seasonCode: String?
    public var seasonType: String?
    public var phase: JSONValue?
    public var scheme: String?
    public var basis: String?
    public var regulationMinutes: Int?
    public var freshness: LeagueFreshness?
    public var window: LeagueDefenseWindow?
    public var pointsAllowedPerGame: Double?
    public var leaguePointsAllowedPerGame: Double?
    public var buckets: [LeagueDefenseBucket]?
    public var provisional: Bool?
    public var withheld: LeagueWithheld?
    public var coverage: LeagueDefenseCoverage?
    public var reconciliation: LeagueDefenseReconciliation?
    public var method: LeagueDefenseMethod?
    public var methodMessage: String?
    public var availability: String?
    public var caveat: String?
    public var notes: [String]?

    /// True for the all-teams table, false for one team's breakdown.
    public var isLeagueTable: Bool { team == nil && teams != nil }
}

/// Points allowed to opponents at one position. `band` (better, typical, worse) and the indices
/// are nil unless the table is not provisional and the position has enough signal; a nil band
/// renders as an em dash, never as "typical".
public struct LeagueDefenseBucket: Codable, Hashable, Sendable {
    public var position: String?
    public var label: String?
    public var pointsAllowedPerGame: Double?
    public var leagueAverage: Double?
    public var deltaPerGame: Double?
    public var opponentMinutesPerGame: Double?
    public var pointsPerRegulationMinutes: Double?
    public var leagueRate: Double?
    public var share: Double?
    public var leagueShare: Double?
    public var rawIndex: Double?
    public var index: Double?
    public var standardError: Double?
    public var band: String?
}

/// One team's row of the league defence table.
public struct LeagueDefenseTeamRow: Codable, Hashable, Sendable {
    public var team: LeagueTeamRef?
    public var games: Int?
    public var pointsAllowedPerGame: Double?
    public var buckets: [LeagueDefenseBucket]?
    public var provisional: Bool?
    public var withheld: LeagueWithheld?
}

/// The games the table covers: the whole season, or the last N.
public struct LeagueDefenseWindow: Codable, Hashable, Sendable {
    public var kind: String?
    public var games: Int?
    public var requested: Int?
}

/// Why a table or a team shows no per-position breakdown yet (minimumGames, positionCoverage,
/// leagueSample), in the server's own words.
public struct LeagueWithheld: Codable, Hashable, Sendable {
    public var reason: String?
    public var message: String?
}

/// How much of the points allowed could be placed at a listed position.
public struct LeagueDefenseCoverage: Codable, Hashable, Sendable {
    public var listed: Double?
    public var workbookListing: Double?
    public var unknown: Double?
}

/// The identity the buckets must satisfy: they sum to the points allowed per game.
public struct LeagueDefenseReconciliation: Codable, Hashable, Sendable {
    public var sumOfBuckets: Double?
    public var pointsAllowedPerGame: Double?
    public var unreconciledGames: Int?
    public var identity: String?
}

/// How the table was built: thresholds, the shrinkage rule, and the limitations to state.
public struct LeagueDefenseMethod: Codable, Hashable, Sendable {
    public var minimumGames: Int?
    public var provisionalBelowGames: Int?
    public var coverageCeiling: Double?
    public var shrinkage: String?
    public var leagueReliability: [String: JSONValue]?
    public var leagueSignal: String?
    public var bandRule: String?
    public var positionSource: String?
    public var taxonomy: String?
    public var limitations: [String]?
}

// MARK: - Projections (`GET {prefix}/projections`, `/games/{id}/projection`)

/// A game's projected score, margin and winner, with the model that made it and what it assumed.
/// `model.kind` says whether this is the latest run, one locked before tip-off, one rebuilt after
/// the fact or one imported from a workbook; `model.computedAfterTipoff` is true only when the
/// server is certain the run came after the game began.
public struct LeagueGameProjection: Codable, Hashable, Sendable {
    public var league: String?
    public var game: LeagueGameRef?
    public var freshness: LeagueFreshness?
    public var home: LeagueSideProjection?
    public var away: LeagueSideProjection?
    public var margin: Double?
    public var marginRange80: LeagueRange?
    public var projectedWinner: LeagueTeamRef?
    public var isTossUp: Bool?
    public var summary: String?
    public var combinedPoints: Double?
    public var combinedAvailabilityEffect: Double?
    public var homeAdvantagePoints: Double?
    public var venueAssumed: Bool?
    public var intervalBasis: String?
    public var model: LeagueProjectionModel?
    public var assumptions: LeagueAssumptions?
    public var result: LeagueProjectionResult?
    public var availability: String?
    public var notes: [String]?
}

/// One team's side of a projection. The detail fields are present on the single-game routes and
/// absent from the slate list.
public struct LeagueSideProjection: Codable, Hashable, Sendable {
    public var team: LeagueTeamRef?
    public var projectedPoints: Double?
    public var range80: LeagueRange?
    public var fullStrengthPoints: Double?
    public var availabilityEffect: Double?
    public var attackIndex: Double?
    public var attackIndexAfterAvailability: Double?
    public var defenceIndex: Double?
    public var capBinding: Bool?
    public var unassignedPoints: Double?
    public var replacementPoints: Double?
    public var keyAbsences: [LeagueAbsence]?
}

/// An 80 percent range for a projected quantity.
public struct LeagueRange: Codable, Hashable, Sendable {
    public var low: Double?
    public var high: Double?
}

/// The model behind a projection: its key and version, when it ran, what inputs it was allowed to
/// see, and the constants it used with where each came from.
public struct LeagueProjectionModel: Codable, Hashable, Sendable {
    public var key: String?
    public var version: String?
    public var kind: String?
    public var computedAt: String?
    public var inputsCutoff: String?
    public var capPolicy: String?
    public var constants: [LeagueModelConstant]?
    public var computedAfterTipoff: Bool?
}

/// A model constant or setting: also the row type of `{prefix}/model-settings` and of the
/// EuroLeague method page. `value` is a number or, for a policy such as capPolicy, a string.
public struct LeagueModelConstant: Codable, Hashable, Sendable {
    public var key: String?
    public var value: JSONValue?
    public var provenance: String?
    public var isDefault: Bool?
    public var description: String?
    public var setAt: String?
}

/// What a projection assumed about who plays: how many players were assumed available on each
/// side, why, and how many out-of-date entries it ignored.
public struct LeagueAssumptions: Codable, Hashable, Sendable {
    public var assumedAvailable: LeagueAssumedAvailable?
    public var staleEntriesIgnored: Int?
}

/// `basis` is notOnSubmittedReport, teamReportPending, noReportPublished or noEntry; the NBA also
/// sends one per side (`homeBasis`, `awayBasis`).
public struct LeagueAssumedAvailable: Codable, Hashable, Sendable {
    public var home: Int?
    public var away: Int?
    public var basis: String?
    public var homeBasis: String?
    public var awayBasis: String?
}

/// The real result, next to its projection, once the game is final.
public struct LeagueProjectionResult: Codable, Hashable, Sendable {
    public var homePts: Int?
    public var awayPts: Int?
    public var marginMiss: Double?
    public var winnerCalled: Bool?
}

/// A player missing from a team and what that is estimated to cost it.
public struct LeagueAbsence: Codable, Hashable, Sendable {
    public var player: LeaguePlayerRef?
    public var status: String?
    public var chanceOfPlaying: Double?
    public var expectedPointsLost: Double?
    public var expectedMinutesLost: Double?
    public var inForce: Bool?
    public var isStale: Bool?
    public var source: LeagueSource?
}

/// `GET /v1/projections` (NBA, by day) and `GET /v1/el/projections` (EuroLeague, by round). The
/// slate-level `model` block describes no single game, and its `computedAfterTipoff` is nil.
public struct LeagueSlateProjections: Codable, Hashable, Sendable {
    public var league: String?
    public var date: String?
    public var round: Int?
    public var freshness: LeagueFreshness?
    public var model: LeagueProjectionModel?
    public var games: [LeagueGameProjection]?
    public var review: LeagueReviewSummary?
    public var availability: String?
    public var notes: [String]?
}

/// `GET {prefix}/games/{id}/projection`: the current projection, the one locked before tip-off
/// (nil until one exists) and every run so far.
public struct LeagueGameProjectionDetail: Codable, Hashable, Sendable {
    public var league: String?
    public var game: LeagueGameRef?
    public var freshness: LeagueFreshness?
    public var current: LeagueGameProjection?
    public var locked: LeagueGameProjection?
    public var history: [LeagueProjectionHistoryEntry]?
    public var availability: String?
    public var notes: [String]?
}

/// One run of the model for a game.
public struct LeagueProjectionHistoryEntry: Codable, Hashable, Sendable {
    public var computedAt: String?
    public var kind: String?
    public var homePts: Double?
    public var awayPts: Double?
}

// MARK: - Review (`GET {prefix}/projections/review`)

/// Projections against what happened.
public struct LeagueProjectionReview: Codable, Hashable, Sendable {
    public var league: String?
    public var scope: LeagueReviewScope?
    public var freshness: LeagueFreshness?
    public var byModel: [LeagueReviewModelRow]?
    public var reconstructed: LeagueReviewModelRow?
    public var games: [LeagueReviewGame]?
    public var notes: [String]?
}

/// What a review covers: a season and, for the NBA, a day, or for the EuroLeague, a round.
public struct LeagueReviewScope: Codable, Hashable, Sendable {
    public var season: String?
    public var seasonCode: String?
    public var date: String?
    public var round: Int?
}

/// The review block a slate carries: the same two model rows, without the games.
public struct LeagueReviewSummary: Codable, Hashable, Sendable {
    public var byModel: [LeagueReviewModelRow]?
    public var reconstructed: LeagueReviewModelRow?
}

/// How one model did over the games it was reviewed on. The misses are mean absolute errors in
/// points; `winnersCalled` counts decided games only (a toss-up calls no winner).
public struct LeagueReviewModelRow: Codable, Hashable, Sendable {
    public var modelKey: String?
    public var games: Int?
    public var decidedGames: Int?
    public var winnersCalled: Int?
    public var tossUps: Int?
    public var meanAbsMarginMiss: Double?
    public var meanAbsScoreMiss: Double?
    public var meanAbsCombinedMiss: Double?
}

/// One reviewed game: the projection used, the result, and how far off it was. `locked` is the
/// NBA's frozen pre-tip-off row and is nil when none was frozen.
public struct LeagueReviewGame: Codable, Hashable, Sendable {
    public var game: LeagueGameRef?
    public var kind: String?
    public var modelKey: String?
    public var projected: LeagueScorePair?
    public var locked: LeagueScorePair?
    public var result: LeagueScorePair?
    public var marginMiss: Double?
    public var combinedMiss: Double?
    public var winnerCalled: Bool?
}

/// A home and away score, projected or real.
public struct LeagueScorePair: Codable, Hashable, Sendable {
    public var homePts: Double?
    public var awayPts: Double?
}

// MARK: - Availability, news, sources

/// `GET {prefix}/availability`. `state` is fresh, stale, noReportYet, unreadable, disabled (or a
/// legacy value); `message` explains any state that is not fresh. Every entry carries its source.
public struct LeagueAvailabilityReport: Codable, Hashable, Sendable {
    public var league: String?
    public var asOf: String?
    public var freshness: LeagueFreshness?
    public var state: String?
    public var message: String?
    public var teams: [LeagueAvailabilityTeam]?
    public var news: [LeagueNewsLink]?
    public var attribution: String?
}

/// One team's block of the report. `reportState` is submitted, notYetSubmitted or noReport; the
/// NBA also names the game the report is for.
public struct LeagueAvailabilityTeam: Codable, Hashable, Sendable {
    public var team: LeagueTeamRef?
    public var reportState: String?
    public var game: LeagueGameRef?
    public var entries: [LeagueAvailabilityEntry]?
}

/// A player's status. `status` is out, doubtful, questionable, probable, available, or nil for
/// "no report" (never rendered as available). `modelStatus` is what the projection used when that
/// differs from `status`. `statusId` identifies an EuroLeague entry and `overrideId` an NBA one a
/// person typed; either is what a retraction names. `outOfForceReason` is a code (returnDatePassed,
/// returnRoundPassed, playedSince, tooOld) saying why an entry no longer drives the projection;
/// `LeagueFormatting.outOfForceWord` words it.
public struct LeagueAvailabilityEntry: Codable, Hashable, Sendable {
    public var statusId: Int?
    public var overrideId: Int?
    public var player: LeaguePlayerRef?
    public var playerName: String?
    public var status: String?
    public var statusLabel: String?
    public var chanceOfPlaying: Double?
    public var modelStatus: String?
    public var reasonCategory: String?
    public var reasonText: String?
    public var expectedReturnText: String?
    public var expectedReturn: LeagueExpectedReturn?
    public var game: LeagueGameRef?
    public var isOverride: Bool?
    public var inForce: Bool?
    public var outOfForceReason: String?
    public var isStale: Bool?
    public var ageMinutes: Double?
    public var source: LeagueSource?
}

/// When a player is expected back: a range of rounds, or a day.
public struct LeagueExpectedReturn: Codable, Hashable, Sendable {
    public var roundFrom: Int?
    public var roundTo: Int?
    public var date: String?
}

/// A headline: title, link, date and source name only. Hardwood never reads an article body.
public struct LeagueNewsLink: Codable, Hashable, Sendable {
    public var itemId: Int?
    public var title: String?
    public var link: String?
    public var publishedAt: String?
    public var sourceName: String?
    public var teams: [LeagueTeamRef]?
    public var players: [LeaguePlayerRef]?
}

/// `GET {prefix}/news`.
public struct LeagueNewsList: Codable, Hashable, Sendable {
    public var league: String?
    public var freshness: LeagueFreshness?
    public var items: [LeagueNewsLink]?
    public var notes: [String]?

    /// The headlines, empty when the server sent none.
    public var allItems: [LeagueNewsLink] { items ?? [] }
}

/// `GET {prefix}/sources`: where every number came from and how current each source is.
public struct LeagueSourceList: Codable, Hashable, Sendable {
    public var league: String?
    public var freshness: LeagueFreshness?
    public var sources: [LeagueSourceEntry]?
    public var store: LeagueSourceStore?
    public var dayOneNotice: String?
    public var attribution: String?
}

/// One source. `robotsCheckedOn` is a calendar day (a feed's robots.txt check), not a flag.
public struct LeagueSourceEntry: Codable, Hashable, Sendable {
    public var key: String?
    public var label: String?
    public var kind: String?
    public var enabled: Bool?
    public var state: String?
    public var reason: String?
    public var lastSuccessAt: String?
    public var lastError: String?
    public var robotsCheckedOn: String?
    public var attribution: String?
}

/// The state of the store itself (the EuroLeague's: ready, demo, off, misconfigured).
public struct LeagueSourceStore: Codable, Hashable, Sendable {
    public var state: String?
    public var reason: String?
    public var kind: String?
}

/// `GET {prefix}/availability/review-queue` (NBA) and `GET /v1/el/review-queue` (EuroLeague):
/// report rows whose player matched nobody. `kind` (status or person), `name` and `personCode`
/// are only present on the EuroLeague's queue, which also lists unmatched people.
public struct LeagueReviewQueue: Codable, Hashable, Sendable {
    public var league: String?
    public var items: [LeagueReviewQueueItem]?
}

/// One waiting row.
public struct LeagueReviewQueueItem: Codable, Hashable, Sendable {
    public var kind: String?
    public var statusId: Int?
    public var playerName: String?
    public var name: String?
    public var personCode: String?
    public var team: LeagueTeamRef?
    public var source: LeagueSource?
}

/// `GET {prefix}/model-settings`.
public struct LeagueModelSettings: Codable, Hashable, Sendable {
    public var league: String?
    public var freshness: LeagueFreshness?
    public var settings: [LeagueModelConstant]?
}

// MARK: - EuroLeague only: meta, clubs, ratings

/// `GET /v1/el/meta`: what the EuroLeague is, what state it is in and what to expect on day one.
public struct ElMeta: Codable, Hashable, Sendable {
    public var league: String?
    public var state: String?
    public var reason: String?
    public var isDemo: Bool?
    public var mode: String?
    public var seasons: [ElSeasonInfo]?
    public var currentSeason: String?
    public var currentSeasonCode: String?
    public var rounds: [ElRoundSummary]?
    public var clubs: [LeagueTeamRef]?
    public var regulationMinutes: Int?
    public var perModes: [String]?
    public var positionBuckets: [String]?
    public var unverifiedClubCodes: [String]?
    public var attribution: String?
    public var dataThrough: String?
    public var dayOneNotice: String?
    public var freshness: LeagueFreshness?
}

/// A season the store holds: its code (E2026), its label (2026-27) and whether it is current.
public struct ElSeasonInfo: Codable, Hashable, Sendable {
    public var code: String?
    public var label: String?
    public var isCurrent: Bool?
}

/// One round in `ElMeta.rounds`. `phase` is RS, PI, PO or FF; `status` is upcoming, inProgress,
/// resultPending or complete.
public struct ElRoundSummary: Codable, Hashable, Sendable {
    public var round: Int?
    public var phase: String?
    public var firstTipoffUtc: String?
    public var games: Int?
    public var status: String?
}

/// `GET /v1/el/teams`: every club's record, scoring, points allowed and rating.
public struct ElTeamsTable: Codable, Hashable, Sendable {
    public var league: String?
    public var season: String?
    public var seasonCode: String?
    public var freshness: LeagueFreshness?
    public var teams: [ElTeamsRow]?
    public var notes: [String]?
}

/// One club of the teams table.
public struct ElTeamsRow: Codable, Hashable, Sendable {
    public var team: LeagueTeamRef?
    public var record: TeamRecord?
    public var games: Int?
    public var pointsPerGame: Double?
    public var pointsAllowedPerGame: Double?
    public var rating: ElRatingBrief?
}

/// The two rating indices on a club's teams-table row.
public struct ElRatingBrief: Codable, Hashable, Sendable {
    public var attackIndex: Double?
    public var defenceIndex: Double?
    public var asOfRound: Int?
}

/// `GET /v1/el/teams/{clubCode}`: one club's scoring, rating, squad, absences, next game and
/// defence summary.
public struct ElClubView: Codable, Hashable, Sendable {
    public var league: String?
    public var team: LeagueTeamRef?
    public var season: String?
    public var seasonCode: String?
    public var freshness: LeagueFreshness?
    public var coach: String?
    public var record: TeamRecord?
    public var scoring: LeagueTeamForm?
    public var rating: ElClubRating?
    public var squad: [ElSquadMember]?
    public var absences: [LeagueAbsence]?
    public var nextGame: LeagueGameRef?
    public var defenseSummary: LeagueDefenseSummary?
    public var notes: [String]?
}

/// A club's rating as the workbook computes it: priors, adjustments and the projected points that
/// follow from them. `source`, `updateWeight` and `updateBasis` say how the latest update was made.
public struct ElClubRating: Codable, Hashable, Sendable {
    public var pfPrior: Double?
    public var paPrior: Double?
    public var priorIsEstimate: Bool?
    public var attackAdj: Double?
    public var defenceAdj: Double?
    public var projectedPointsFor: Double?
    public var projectedPointsAgainst: Double?
    public var attackIndex: Double?
    public var defenceIndex: Double?
    public var asOfRound: Int?
    public var source: String?
    public var updateWeight: Double?
    public var updateBasis: String?
    public var extrapolated: Bool?
}

/// A squad member. `basis` says whose numbers the rates are: workbookOfficial, workbookEstimate,
/// positionPrior, officialUpdate or syntheticDemo. An estimate is never shown as official.
public struct ElSquadMember: Codable, Hashable, Sendable {
    public var player: LeaguePlayerRef?
    public var positionWorkbook5: String?
    public var age: Double?
    public var role: String?
    public var basis: String?
    public var basisNote: String?
    public var isEstimate: Bool?
    public var projectedMinutes: Double?
    public var per40: ElPer40?
    public var perGame: ElPerGameProjection?
    public var seasonAverages: ElSeasonAverages?
    public var status: String?
    public var chanceOfPlaying: Double?
    public var inForce: Bool?
    public var isStale: Bool?
    public var availabilitySource: LeagueSource?
}

/// Per-40-minute rates.
public struct ElPer40: Codable, Hashable, Sendable {
    public var pts: Double?
    public var reb: Double?
    public var ast: Double?
    public var fg3m: Double?
    public var stl: Double?
    public var blk: Double?
    public var tov: Double?
}

/// A squad member's official season averages. Shooting percentages are fractions.
public struct ElSeasonAverages: Codable, Hashable, Sendable {
    public var games: Int?
    public var min: Double?
    public var pts: Double?
    public var reb: Double?
    public var ast: Double?
    public var pir: Double?
    public var fg2Pct: Double?
    public var fg3Pct: Double?
    public var ftPct: Double?
}

/// The squad's projected per-game line. `pir` is always nil: a projected PIR needs fouls drawn,
/// blocks against and fouls committed, which the rates do not carry.
public struct ElPerGameProjection: Codable, Hashable, Sendable {
    public var min: Double?
    public var pts: Double?
    public var reb: Double?
    public var ast: Double?
    public var fg3m: Double?
    public var stl: Double?
    public var blk: Double?
    public var tov: Double?
    public var pir: Double?
}

/// `GET /v1/el/ratings`: every club's rating as of a round, and the updates that moved it.
public struct ElRatingsTable: Codable, Hashable, Sendable {
    public var league: String?
    public var season: String?
    public var seasonCode: String?
    public var asOfRound: Int?
    public var baseRound: Int?
    public var freshness: LeagueFreshness?
    public var leagueAveragePoints: Double?
    public var regression: Double?
    public var rows: [ElRatingRow]?
    public var updates: [ElRatingUpdate]?
    public var notes: [String]?
}

/// One club of the ratings table.
public struct ElRatingRow: Codable, Hashable, Sendable {
    public var team: LeagueTeamRef?
    public var pfPrior: Double?
    public var paPrior: Double?
    public var priorIsEstimate: Bool?
    public var attackAdj: Double?
    public var defenceAdj: Double?
    public var projectedPointsFor: Double?
    public var projectedPointsAgainst: Double?
    public var attackIndex: Double?
    public var defenceIndex: Double?
    public var source: String?
    public var updateWeight: Double?
    public var updateBasis: String?
    public var extrapolated: Bool?
    public var gamesCounted: Int?
}

/// One game's contribution to a club's rating: what the model projected, what happened, and the
/// weight it was given.
public struct ElRatingUpdate: Codable, Hashable, Sendable {
    public var round: Int?
    public var team: LeagueTeamRef?
    public var opponent: LeagueTeamRef?
    public var gameId: String?
    public var gameNumber: Int?
    public var weight: Double?
    public var extrapolated: Bool?
    public var projectedScored: Double?
    public var scored: Double?
    public var projectedAllowed: Double?
    public var allowed: Double?
    public var basis: String?
}

// MARK: - EuroLeague only: rounds and scorers

/// `GET /v1/el/rounds/{n}`: every game of a round with its projection. `status` is upcoming,
/// inProgress, resultPending or complete.
public struct ElRoundView: Codable, Hashable, Sendable {
    public var league: String?
    public var season: String?
    public var seasonCode: String?
    public var round: Int?
    public var phase: String?
    public var freshness: LeagueFreshness?
    public var status: String?
    public var games: [LeagueGameProjection]?
    public var summary: ElRoundStats?
    public var notes: [String]?
}

/// The counts and averages a round summary strip shows.
public struct ElRoundStats: Codable, Hashable, Sendable {
    public var games: Int?
    public var tossUps: Int?
    public var closestGame: LeagueGameRef?
    public var averageCombinedPoints: Double?
    public var homeWinnersProjected: Int?
    public var awayWinnersProjected: Int?
}

/// `GET /v1/el/rounds/{n}/scorers`: each club's top scorers for a round.
public struct ElRoundScorers: Codable, Hashable, Sendable {
    public var league: String?
    public var season: String?
    public var seasonCode: String?
    public var round: Int?
    public var freshness: LeagueFreshness?
    public var clubs: [ElScorerClub]?
    public var notes: [String]?
}

/// One club's scorers for a round, with the game they are playing.
public struct ElScorerClub: Codable, Hashable, Sendable {
    public var team: LeagueTeamRef?
    public var game: LeagueGameRef?
    public var players: [ElScorer]?
}

/// A projected scorer. `recentPoints` is the last five official games, newest first, with nil for
/// a game the player did not play.
public struct ElScorer: Codable, Hashable, Sendable {
    public var player: LeaguePlayerRef?
    public var status: String?
    public var chanceOfPlaying: Double?
    public var modelPoints: Double?
    public var formAverage: Double?
    public var formGames: Int?
    public var projectedPoints: Double?
    public var recentPoints: [Double?]?
    public var projectedMinutes: Double?
}

// MARK: - EuroLeague only: games, box scores, players

/// `GET /v1/el/games`: the schedule and results (EuroLeague games only).
public struct ElGamesList: Codable, Hashable, Sendable {
    public var league: String?
    public var season: String?
    public var seasonCode: String?
    public var freshness: LeagueFreshness?
    public var games: [LeagueGameRef]?
    public var notes: [String]?
}

/// `GET /v1/el/games/{id}`: a box score. `statsStatus` says whether the player lines are final.
public struct ElBoxScore: Codable, Hashable, Sendable {
    public var league: String?
    public var game: LeagueGameRef?
    public var partials: ElPartials?
    public var attendance: Double?
    public var statsStatus: String?
    public var teams: [ElBoxTeam]?
    public var freshness: LeagueFreshness?
    public var sourceRef: ElSourceRef?
    public var notes: [String]?
}

/// Points per period, home and away. Overtime periods are appended.
public struct ElPartials: Codable, Hashable, Sendable {
    public var home: [Int]?
    public var away: [Int]?
}

/// Where a box score was read from and when.
public struct ElSourceRef: Codable, Hashable, Sendable {
    public var kind: String?
    public var label: String?
    public var ingestedAt: String?
}

/// One team's side of a box score.
public struct ElBoxTeam: Codable, Hashable, Sendable {
    public var team: LeagueTeamRef?
    public var totals: ElStatLine?
    public var players: [ElBoxPlayer]?
}

/// One player's box-score row. `participation` is played or dnp; a dnp row has no `stats`.
public struct ElBoxPlayer: Codable, Hashable, Sendable {
    public var player: LeaguePlayerRef?
    public var participation: String?
    public var isStarter: Bool?
    public var stats: ElStatLine?

    /// The row's line. The design allowed two other key names for it; the server sends only
    /// `stats`, and this stays so a caller written against the design still compiles.
    public var resolvedLine: ElStatLine? { stats }
}

/// A box-score line. `minutes` is rendered with one decimal under the header MIN; the
/// percentages are fractions, and nil when there were no attempts.
public struct ElStatLine: Codable, Hashable, Sendable {
    public var minutes: Double?
    public var pts: Double?
    public var fgm2: Double?
    public var fga2: Double?
    public var fg2Pct: Double?
    public var fgm3: Double?
    public var fga3: Double?
    public var fg3Pct: Double?
    public var ftm: Double?
    public var fta: Double?
    public var ftPct: Double?
    public var oreb: Double?
    public var dreb: Double?
    public var reb: Double?
    public var ast: Double?
    public var stl: Double?
    public var tov: Double?
    public var blk: Double?
    public var blkAgainst: Double?
    public var pf: Double?
    public var foulsDrawn: Double?
    public var plusMinus: Double?
    public var pir: Double?
}

/// `GET /v1/el/stats/players`. `perMode` is PerGame, Totals or Per40; `sort` is the key the
/// server ordered the rows by.
public struct ElPlayerStatsTable: Codable, Hashable, Sendable {
    public var league: String?
    public var season: String?
    public var seasonCode: String?
    public var perMode: String?
    public var sort: String?
    public var freshness: LeagueFreshness?
    public var rows: [ElStatsRow]?
    public var notes: [String]?
}

/// One player of the stats table. `values` is keyed by stat (min, pts, reb, fg2_pct, plus_minus,
/// pir ...) and a null value stays an em dash; `gamesWithStat` says how many games each stat
/// was recorded in.
public struct ElStatsRow: Codable, Hashable, Sendable {
    public var player: LeaguePlayerRef?
    public var team: LeagueTeamRef?
    public var games: Int?
    public var values: [String: JSONValue]?
    public var gamesWithStat: [String: JSONValue]?

    /// One stat of the row, or nil when it is absent or null.
    public func value(_ key: String) -> Double? {
        values?[key]?.doubleValue
    }
}

/// `GET /v1/el/players/{personCode}`: one player's registration, season line, rates and status.
public struct ElPlayerDetail: Codable, Hashable, Sendable {
    public var league: String?
    public var season: String?
    public var seasonCode: String?
    public var freshness: LeagueFreshness?
    public var player: LeaguePlayerRef?
    public var club: LeagueTeamRef?
    public var registration: ElPlayerRegistration?
    public var birthDate: String?
    public var heightCm: Double?
    public var weightKg: Double?
    public var countryCode: String?
    public var seasonAverages: ElPlayerSeasonLine?
    public var seasonPer40: [String: JSONValue]?
    public var rate: ElPlayerRate?
    public var availability: ElPlayerAvailability?
    public var notes: [String]?
}

/// What the league's registration says about a player.
public struct ElPlayerRegistration: Codable, Hashable, Sendable {
    public var dorsal: String?
    public var positionCode: Int?
    public var positionName: String?
    public var positionWorkbook5: String?
    public var role: String?
    public var age: Double?
    public var active: Bool?
}

/// A player's season line as a keyed table, like `ElStatsRow`.
public struct ElPlayerSeasonLine: Codable, Hashable, Sendable {
    public var games: Int?
    public var values: [String: JSONValue]?
    public var gamesWithStat: [String: JSONValue]?

    /// One stat of the line, or nil when it is absent or null.
    public func value(_ key: String) -> Double? {
        values?[key]?.doubleValue
    }
}

/// The rate the projection uses for a player and whose numbers it is (see `ElSquadMember.basis`).
public struct ElPlayerRate: Codable, Hashable, Sendable {
    public var basis: String?
    public var basisNote: String?
    public var asOfRound: Int?
    public var projectedMinutes: Double?
    public var per40: ElPer40?
}

/// A player's current availability with its source.
public struct ElPlayerAvailability: Codable, Hashable, Sendable {
    public var status: String?
    public var chanceOfPlaying: Double?
    public var inForce: Bool?
    public var isStale: Bool?
    public var source: LeagueSource?
}

/// `GET /v1/el/players/{personCode}/gamelog`: one player's official games, newest first.
public struct ElPlayerGameLog: Codable, Hashable, Sendable {
    public var league: String?
    public var season: String?
    public var seasonCode: String?
    public var freshness: LeagueFreshness?
    public var player: LeaguePlayerRef?
    public var games: [ElGameLogRow]?
    public var notes: [String]?
}

/// One game of a player's log. `club` is the player's own club and `result` is "W" or "L" from its
/// side.
public struct ElGameLogRow: Codable, Hashable, Sendable {
    public var game: LeagueGameRef?
    public var club: LeagueTeamRef?
    public var opponent: LeagueTeamRef?
    public var isHome: Bool?
    public var isNeutral: Bool?
    public var teamScore: Int?
    public var opponentScore: Int?
    public var result: String?
    public var participation: String?
    public var isStarter: Bool?
    public var stats: ElStatLine?
}

/// `GET /v1/el/method`: the model's constants with where each came from, and the deviations and
/// limitations to state.
public struct ElMethod: Codable, Hashable, Sendable {
    public var league: String?
    public var freshness: LeagueFreshness?
    public var constants: [LeagueModelConstant]?
    public var deviations: [String]?
    public var limitations: [String]?
}

// MARK: - Write bodies
//
// Encodable bodies for the three writes. Synthesized encoding uses `encodeIfPresent`, so a nil
// property is simply left out of the JSON, which is what the server wants: every body is
// `extra="forbid"`, so a key the route does not know is a 400 naming it. That is why the two
// leagues have two status bodies: the NBA body has no club, reason or label fields, and sending
// one (say `sourceLabel`) would be refused. Nothing here can carry a number to compare a
// projection with.

/// `POST /v1/el/availability`. `clubCode`, `status`, `sourceLabel` and `sourcePublishedAt` are
/// required by the server; the rest are optional.
public struct ElStatusWrite: Codable, Hashable, Sendable {
    public var clubCode: String?
    public var status: String?
    public var sourceLabel: String?
    public var sourcePublishedAt: String?
    public var personCode: String?
    public var playerName: String?
    public var gameId: String?
    public var reasonCategory: String?
    public var reasonText: String?
    public var expectedReturnText: String?
    public var sourceUrl: String?
}

/// `POST /v1/availability` (NBA). `playerId` and `status` are required by the server.
public struct NbaStatusWrite: Codable, Hashable, Sendable {
    public var playerId: Int?
    public var status: String?
    public var teamId: Int?
    public var gameId: String?
    public var note: String?
    public var sourceUrl: String?
    public var sourcePublishedAt: String?
}

/// `POST {prefix}/news/links`. Ids are the strings of `LeagueTeamRef.id` and `LeaguePlayerRef.id`.
public struct LeagueLinkWrite: Codable, Hashable, Sendable {
    public var title: String?
    public var link: String?
    public var publishedAt: String?
    public var sourceName: String?
    public var teamIds: [String]?
    public var playerIds: [String]?
}

/// One setting of a `PATCH {prefix}/model-settings` body.
public struct LeagueSettingWrite: Codable, Hashable, Sendable {
    public var key: String?
    public var value: Double?
}

/// `PATCH {prefix}/model-settings`: one or more allowlisted settings, applied as a whole.
public struct LeagueSettingsPatch: Codable, Hashable, Sendable {
    public var settings: [LeagueSettingWrite]?
}
