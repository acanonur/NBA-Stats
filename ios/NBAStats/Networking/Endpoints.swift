import Foundation

/// Typed URL construction for every route in `contracts/CONTRACT.md` §3.
///
/// Building URLs in one place keeps the percent-encoding rules in one place too: query values go
/// through `URLComponents`, and array parameters are rendered comma-separated exactly as the
/// service parses them.
public struct Endpoints: Hashable, Sendable {

    /// The `/v1` base every route hangs off.
    public let baseURL: URL

    public init(baseURL: URL) {
        self.baseURL = baseURL
    }

    public init(configuration: APIConfiguration) {
        self.init(baseURL: configuration.versionedBaseURL)
    }

    // MARK: - Service state

    public func health() -> URL { url(["health"]) }

    public func meta() -> URL { url(["meta"]) }

    public func presets() -> URL { url(["presets"]) }

    public func sync(since: Int? = nil) -> URL {
        url(["sync"], [item("since", since)])
    }

    public func syncStream() -> URL { url(["sync", "stream"]) }

    // MARK: - Players

    public func playerSearch(query: String,
                             limit: Int = 20,
                             activeOnly: Bool = false,
                             season: String? = nil) -> URL {
        url(["players", "search"], [
            item("q", query),
            item("limit", limit),
            activeOnly ? item("activeOnly", true) : nil,
            item("season", season)
        ])
    }

    public func player(_ playerID: PlayerID) -> URL {
        url(["players", String(playerID)])
    }

    public func gameLog(playerID: PlayerID,
                        season: String,
                        seasonType: String? = nil,
                        limit: Int? = nil,
                        cursor: String? = nil,
                        metrics: [String] = []) -> URL {
        url(["players", String(playerID), "gamelog"], [
            item("season", season),
            item("seasonType", seasonType),
            item("limit", limit),
            item("cursor", cursor),
            item("metrics", metrics)
        ])
    }

    // MARK: - Teams

    public func teams(includeHistorical: Bool = false) -> URL {
        url(["teams"], [includeHistorical ? item("includeHistorical", true) : nil])
    }

    public func team(_ teamID: TeamID, season: String? = nil, seasonType: String? = nil) -> URL {
        url(["teams", String(teamID)], [
            item("season", season),
            item("seasonType", seasonType)
        ])
    }

    // MARK: - Leaders

    /// Every filter `GET /v1/leaders` accepts. Defaults match the contract's own defaults, so a
    /// caller only names what it wants to change.
    public struct LeadersQuery: Hashable, Sendable {
        public var metric: String
        public var subjectType: String
        public var scope: String
        public var season: String?
        public var seasonType: String?
        public var perMode: String?
        public var limit: Int?
        public var minGames: Int?
        public var minMinutesPerGame: Double?
        public var positions: [String]
        public var teamIds: [TeamID]
        public var secondaryMetrics: [String]
        public var ascending: Bool?
        public var cursor: String?

        public init(metric: String,
                    subjectType: String = "player",
                    scope: String = "season",
                    season: String? = nil,
                    seasonType: String? = nil,
                    perMode: String? = nil,
                    limit: Int? = nil,
                    minGames: Int? = nil,
                    minMinutesPerGame: Double? = nil,
                    positions: [String] = [],
                    teamIds: [TeamID] = [],
                    secondaryMetrics: [String] = [],
                    ascending: Bool? = nil,
                    cursor: String? = nil) {
            self.metric = metric
            self.subjectType = subjectType
            self.scope = scope
            self.season = season
            self.seasonType = seasonType
            self.perMode = perMode
            self.limit = limit
            self.minGames = minGames
            self.minMinutesPerGame = minMinutesPerGame
            self.positions = positions
            self.teamIds = teamIds
            self.secondaryMetrics = secondaryMetrics
            self.ascending = ascending
            self.cursor = cursor
        }
    }

    public func leaders(_ query: LeadersQuery) -> URL {
        url(["leaders"], [
            item("metric", query.metric),
            item("subjectType", query.subjectType),
            item("scope", query.scope),
            item("season", query.season),
            item("seasonType", query.seasonType),
            item("perMode", query.perMode),
            item("limit", query.limit),
            item("minGames", query.minGames),
            item("minMinutesPerGame", query.minMinutesPerGame),
            item("positions", query.positions),
            item("teamIds", query.teamIds.map { String($0) }),
            item("secondaryMetrics", query.secondaryMetrics),
            item("ascending", query.ascending),
            item("cursor", query.cursor)
        ])
    }

    // MARK: - Games

    public func games(date: String? = nil,
                      season: String? = nil,
                      seasonType: String? = nil,
                      teamID: TeamID? = nil,
                      limit: Int? = nil,
                      cursor: String? = nil) -> URL {
        url(["games"], [
            item("date", date),
            item("season", season),
            item("seasonType", seasonType),
            item("teamId", teamID),
            item("limit", limit),
            item("cursor", cursor)
        ])
    }

    public func boxScore(gameID: GameID, view: String? = nil) -> URL {
        url(["games", gameID, "box"], [item("view", view)])
    }

    // MARK: - Dashboard

    public func resolve() -> URL { url(["dashboard", "resolve"]) }

    // MARK: - Construction

    /// Appends path components to the base and attaches the non-empty query items.
    ///
    /// `URLComponents` percent-encodes the query for us; the one thing it leaves alone is `+`,
    /// which many servers read as a space, so it is escaped explicitly here.
    private func url(_ pathComponents: [String], _ items: [URLQueryItem?] = []) -> URL {
        var result = baseURL
        for component in pathComponents where !component.isEmpty {
            result = result.appendingPathComponent(component)
        }
        let queryItems = items.compactMap { $0 }
        guard !queryItems.isEmpty else { return result }
        guard var components = URLComponents(url: result, resolvingAgainstBaseURL: false) else { return result }
        components.queryItems = queryItems
        if let encoded = components.percentEncodedQuery {
            components.percentEncodedQuery = encoded.replacingOccurrences(of: "+", with: "%2B")
        }
        return components.url ?? result
    }

    private func item(_ name: String, _ value: String?) -> URLQueryItem? {
        guard let value = value, !value.isEmpty else { return nil }
        return URLQueryItem(name: name, value: value)
    }

    private func item(_ name: String, _ value: Int?) -> URLQueryItem? {
        guard let value = value else { return nil }
        return URLQueryItem(name: name, value: String(value))
    }

    private func item(_ name: String, _ value: Double?) -> URLQueryItem? {
        guard let value = value, value.isFinite else { return nil }
        // A whole number renders without a trailing ".0", which keeps request URLs stable and
        // therefore keeps the client's request coalescing effective.
        if let whole = Int(exactly: value.rounded()), value == value.rounded() {
            return URLQueryItem(name: name, value: String(whole))
        }
        return URLQueryItem(name: name, value: String(value))
    }

    private func item(_ name: String, _ value: Bool?) -> URLQueryItem? {
        guard let value = value else { return nil }
        return URLQueryItem(name: name, value: value ? "true" : "false")
    }

    /// Array parameters are comma-separated, as `metrics`, `positions`, `teamIds` and
    /// `secondaryMetrics` are documented.
    private func item(_ name: String, _ values: [String]) -> URLQueryItem? {
        let cleaned = values.filter { !$0.isEmpty }
        guard !cleaned.isEmpty else { return nil }
        return URLQueryItem(name: name, value: cleaned.joined(separator: ","))
    }
}
