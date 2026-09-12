import Foundation
import OSLog

/// The surface the app talks to, so the live client and the demo client are interchangeable.
///
/// Everything the dashboard, search and settings need is here; the concrete clients add the
/// narrower routes (`gamelog`, `leaders`, `box`) on top.
public protocol APIClientProtocol: Sendable {
    func meta() async throws -> MetaResponse
    func presets() async throws -> PresetsResponse
    func health() async throws -> HealthResponse
    func sync(since: Int?) async throws -> SyncResponse
    func searchPlayers(query: String, limit: Int) async throws -> PlayerSearchResponse
    func player(_ id: PlayerID) async throws -> PlayerDetailResponse
    func teams() async throws -> TeamsResponse
    func resolve(_ request: DashboardResolveRequest) async throws -> DashboardResolveResponse
}

/// The live client: one `URLSession`, one decoder, one retry policy.
///
/// Being an actor buys two things beyond safety — the in-flight table below can be mutated
/// without a lock, and every request shares one configured decoder instead of building one per
/// call.
public actor APIClient: APIClientProtocol {

    /// At most two retries, so a dead server costs three timeouts and not thirty.
    public static let maxRetries = 2

    private let configuration: APIConfiguration
    private let session: URLSession
    private let endpoints: Endpoints
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder
    private let userAgent: String

    /// Identical GETs that are already on the wire share one task instead of racing each other.
    private var inFlight: [String: Task<Data, Error>] = [:]

    private static let logger = Logger(subsystem: "com.hardwood.nbastats", category: "api")

    public init(configuration: APIConfiguration, session: URLSession = .shared) {
        self.configuration = configuration
        self.session = session
        self.endpoints = Endpoints(configuration: configuration)
        self.decoder = APIClient.makeDecoder()
        self.encoder = APIClient.makeEncoder()
        self.userAgent = APIClient.makeUserAgent()
    }

    // MARK: - Shared coders

    /// The one decoder every response goes through.
    ///
    /// No `keyDecodingStrategy` is set: the API is already lowerCamelCase
    /// (`contracts/CONTRACT.md` §1) and converting would break every key the models spell out.
    /// ISO calendar dates such as `"2026-01-02"` stay `String`, as the models declare them; only
    /// genuine `Date` properties use the RFC-3339 strategy below.
    public static func makeDecoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let raw = try container.decode(String.self)
            guard let date = Formatting.parseTimestamp(raw) else {
                throw DecodingError.dataCorruptedError(
                    in: container,
                    debugDescription: "Expected an RFC-3339 timestamp such as \"2026-01-03T07:12:44Z\", got \"\(raw)\"."
                )
            }
            return date
        }
        return decoder
    }

    /// The encoder for request bodies; dates go out in the same RFC-3339 form they arrive in.
    public static func makeEncoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .custom { date, encoder in
            var container = encoder.singleValueContainer()
            try container.encode(Formatting.timestampString(date))
        }
        return encoder
    }

    /// `Hardwood/1.0 (12; iOS 17.4)` — enough for a server log to tell builds apart.
    public static func makeUserAgent(bundle: Bundle = .main) -> String {
        let version = (bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "1.0"
        let build = (bundle.object(forInfoDictionaryKey: "CFBundleVersion") as? String) ?? "1"
        let system = ProcessInfo.processInfo.operatingSystemVersion
        return "Hardwood/\(version) (\(build); iOS \(system.majorVersion).\(system.minorVersion))"
    }

    // MARK: - Routes

    public func meta() async throws -> MetaResponse {
        try await get(MetaResponse.self, url: endpoints.meta())
    }

    public func presets() async throws -> PresetsResponse {
        try await get(PresetsResponse.self, url: endpoints.presets())
    }

    /// `/v1/health` answers 503 with the *same* body when the database is not ready, so the body
    /// is read before the status is judged: "server up, data not loaded" is a useful answer.
    public func health() async throws -> HealthResponse {
        let outcome = try await rawData(url: endpoints.health())
        if let health = try? decoder.decode(HealthResponse.self, from: outcome.data) {
            return health
        }
        if (200..<300).contains(outcome.status) {
            throw APIError.decoding("The health response could not be read.")
        }
        throw APIClient.error(status: outcome.status, data: outcome.data)
    }

    public func sync(since: Int?) async throws -> SyncResponse {
        try await get(SyncResponse.self, url: endpoints.sync(since: since))
    }

    public func searchPlayers(query: String, limit: Int) async throws -> PlayerSearchResponse {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        // The contract requires two characters; answering locally saves a guaranteed 400.
        guard trimmed.count >= 2 else {
            return PlayerSearchResponse(query: trimmed, results: [], nextCursor: nil)
        }
        let url = endpoints.playerSearch(query: trimmed, limit: max(1, min(limit, 50)))
        return try await get(PlayerSearchResponse.self, url: url)
    }

    public func player(_ id: PlayerID) async throws -> PlayerDetailResponse {
        try await get(PlayerDetailResponse.self, url: endpoints.player(id))
    }

    public func teams() async throws -> TeamsResponse {
        try await get(TeamsResponse.self, url: endpoints.teams())
    }

    public func resolve(_ request: DashboardResolveRequest) async throws -> DashboardResolveResponse {
        guard request.widgets.count <= DashboardResolveRequest.maxWidgets else {
            // The caller is expected to split; failing here names the contract rule instead of
            // spending a round trip to be told the same thing.
            throw APIError.server(
                code: APIError.Code.tooManyWidgets.rawValue,
                message: "A resolve request carries at most \(DashboardResolveRequest.maxWidgets) widgets.",
                status: 400,
                recoverable: false
            )
        }
        return try await post(DashboardResolveResponse.self, url: endpoints.resolve(), body: request)
    }

    // MARK: - Routes outside the shared protocol

    public func gameLog(playerID: PlayerID,
                        season: String,
                        seasonType: String? = nil,
                        limit: Int? = nil,
                        cursor: String? = nil,
                        metrics: [String] = []) async throws -> GameLogResponse {
        let url = endpoints.gameLog(playerID: playerID,
                                    season: season,
                                    seasonType: seasonType,
                                    limit: limit,
                                    cursor: cursor,
                                    metrics: metrics)
        return try await get(GameLogResponse.self, url: url)
    }

    public func team(_ teamID: TeamID, season: String? = nil, seasonType: String? = nil) async throws -> TeamDetailResponse {
        try await get(TeamDetailResponse.self, url: endpoints.team(teamID, season: season, seasonType: seasonType))
    }

    public func games(date: String? = nil,
                      season: String? = nil,
                      seasonType: String? = nil,
                      teamID: TeamID? = nil,
                      limit: Int? = nil,
                      cursor: String? = nil) async throws -> ScoreboardResponse {
        let url = endpoints.games(date: date,
                                  season: season,
                                  seasonType: seasonType,
                                  teamID: teamID,
                                  limit: limit,
                                  cursor: cursor)
        return try await get(ScoreboardResponse.self, url: url)
    }

    public func boxScore(gameID: GameID, view: String? = nil) async throws -> BoxScoreResponse {
        try await get(BoxScoreResponse.self, url: endpoints.boxScore(gameID: gameID, view: view))
    }

    public func leaders(_ query: Endpoints.LeadersQuery) async throws -> LeaderboardPayload {
        try await get(LeaderboardPayload.self, url: endpoints.leaders(query))
    }

    // MARK: - Plumbing

    private func get<T: Decodable>(_ type: T.Type, url: URL) async throws -> T {
        let request = makeRequest(url: url, method: "GET", body: nil)
        let data = try await coalescedData(for: request, key: url.absoluteString)
        return try decode(type, from: data)
    }

    private func post<T: Decodable, Body: Encodable>(_ type: T.Type, url: URL, body: Body) async throws -> T {
        let payload: Data
        do {
            payload = try encoder.encode(body)
        } catch {
            throw APIError.decoding("Hardwood could not encode its own request: \(error.localizedDescription)")
        }
        let request = makeRequest(url: url, method: "POST", body: payload)
        // Writes are never coalesced: two identical POSTs are two intentional requests.
        let data = try await APIClient.perform(request: request, session: session)
        return try decode(type, from: data)
    }

    private func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        do {
            return try decoder.decode(type, from: data)
        } catch let error as DecodingError {
            APIClient.logger.error("Decoding \(String(describing: type), privacy: .public) failed: \(APIError.describe(error), privacy: .public)")
            throw APIError.decoding(APIError.describe(error))
        } catch {
            throw APIError.decoding(error.localizedDescription)
        }
    }

    private func makeRequest(url: URL, method: String, body: Data?) -> URLRequest {
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = configuration.timeout
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(userAgent, forHTTPHeaderField: "User-Agent")
        if let key = configuration.apiKey, !key.isEmpty {
            request.setValue(key, forHTTPHeaderField: "X-API-Key")
        }
        if let body = body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return request
    }

    /// Two identical GETs in flight at once — the same widget on two dashboards, a screen that
    /// appears twice in a tab stack — share a single task and a single answer.
    private func coalescedData(for request: URLRequest, key: String) async throws -> Data {
        if let existing = inFlight[key] {
            return try await existing.value
        }
        let currentSession = session
        let task = Task<Data, Error> {
            try await APIClient.perform(request: request, session: currentSession)
        }
        inFlight[key] = task
        do {
            let data = try await task.value
            if inFlight[key] == task { inFlight[key] = nil }
            return data
        } catch {
            if inFlight[key] == task { inFlight[key] = nil }
            throw error
        }
    }

    /// A GET that hands the body back whatever the status, for `/v1/health`.
    private func rawData(url: URL) async throws -> (data: Data, status: Int) {
        let request = makeRequest(url: url, method: "GET", body: nil)
        let result: (Data, URLResponse)
        do {
            result = try await session.data(for: request)
        } catch let urlError as URLError {
            throw APIError.from(urlError: urlError)
        }
        let status = (result.1 as? HTTPURLResponse)?.statusCode ?? 0
        return (result.0, status)
    }

    // MARK: - Transport

    /// One request, with the retry policy applied: a timeout or a 5xx is tried again after a
    /// short exponential backoff, at most `maxRetries` times. A 4xx is never retried — the
    /// request itself is what the server objected to, and repeating it changes nothing.
    private static func perform(request: URLRequest, session: URLSession) async throws -> Data {
        var attempt = 0
        while true {
            do {
                return try await sendOnce(request: request, session: session)
            } catch {
                if error is CancellationError { throw error }
                let apiError = APIError.from(error)
                guard apiError.isAutomaticallyRetryable, attempt < maxRetries else { throw apiError }
                attempt += 1
                logger.notice("Retrying \(request.url?.absoluteString ?? "request", privacy: .public) (attempt \(attempt, privacy: .public)) after \(apiError.title, privacy: .public)")
                try await Task.sleep(nanoseconds: backoffNanoseconds(attempt: attempt))
            }
        }
    }

    private static func sendOnce(request: URLRequest, session: URLSession) async throws -> Data {
        let result: (Data, URLResponse)
        do {
            result = try await session.data(for: request)
        } catch let urlError as URLError {
            throw APIError.from(urlError: urlError)
        }
        guard let http = result.1 as? HTTPURLResponse else {
            throw APIError.decoding("The server's answer was not an HTTP response.")
        }
        guard (200..<300).contains(http.statusCode) else {
            throw APIClient.error(status: http.statusCode, data: result.0)
        }
        return result.0
    }

    /// 0.6s then 1.8s — long enough for a restarting server, short enough that a reader waiting
    /// on a pull-to-refresh does not give up first.
    private static func backoffNanoseconds(attempt: Int) -> UInt64 {
        let seconds = 0.6 * pow(3.0, Double(max(0, attempt - 1)))
        return UInt64(seconds * 1_000_000_000)
    }

    /// Turns a non-2xx response into an `APIError`, preferring the documented envelope
    /// (`contracts/CONTRACT.md` §7) and falling back to the status when a proxy answers with
    /// something else entirely.
    static func error(status: Int, data: Data) -> APIError {
        let envelopeDecoder = JSONDecoder()
        if let envelope = try? envelopeDecoder.decode(ErrorEnvelope.self, from: data) {
            return APIError(body: envelope.error, status: status)
        }
        let code: APIError.Code
        switch status {
        case 401: code = .unauthorized
        case 404: code = .playerNotFound
        case 422: code = .metricUnavailable
        case 429: code = .rateLimited
        case 503: code = .upstreamUnavailable
        case 500..<600: code = .internalError
        default: code = .badRequest
        }
        let text = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        // A 500 page of HTML is not a message; only a short plain answer is worth showing.
        let message = (text.isEmpty || text.count > 240 || text.hasPrefix("<")) ? "" : text
        return .server(code: code.rawValue, message: message, status: status, recoverable: status >= 500)
    }
}
