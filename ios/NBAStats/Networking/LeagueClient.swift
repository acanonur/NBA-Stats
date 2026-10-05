import Foundation
import OSLog

/// Fetches the league routes: live from the user's server, or from the bundled demo fixtures.
///
/// WHY A SECOND CLIENT
/// `APIClient` and `HardwoodAPIClient` implement `APIClientProtocol`, whose surface the dashboard,
/// the sync service and the test stub all depend on. The league screens need one generic
/// "fetch this route as this type" call and a write call; adding them to the protocol would force
/// a change on every conformer, including the test stub. So the league routes get their own small
/// actor, reached as `AppEnvironment.league`, and the protocol is untouched.
///
/// WHAT IT DOES
/// - Builds the URL from a `LeagueRoute`, the configured base URL and the league's prefix (learned
///   from `GET /v1/leagues` through `updatePrefixes`, with a fallback until then).
/// - Sends the API key, when the configuration has one, on EVERY request: reads need it too
///   whenever the server has a key set (the installer sets one), and writes are authorised by it
///   alone. It never sets an `Origin` header (a native request has none, and the server holds any
///   request that does to the browser rules of a session and a CSRF token), and it sends no
///   custom "I am the Mac app" header: a header any web page can also send is not a credential.
/// - Decodes with the app's shared decoder, so the date strategy and every other rule is the one
///   the rest of the app uses.
/// - In demo mode, answers from the fixtures bundled as `league_<nba|el>_<name>.json` and never
///   touches the network; a fixture that is not bundled is `demoModeMissingFixture`, which a
///   screen renders as "Demo data for this screen isn't bundled yet", not as an error tile.
///   A write in demo mode is refused (`demo_read_only`).
///
/// WHAT IT DOES NOT DO
/// It does not retry a GET (a screen has a Retry button, and a league that is off answers a stable
/// 503 that retrying only delays), does not coalesce requests (`.task(id:)` cancels the superseded
/// load, and cancelling a `URLSession` request cancels it on the wire), and never retries or
/// coalesces a write: an append-only status posted twice would be recorded twice.
actor LeagueClient {

    private var configuration: APIConfiguration
    private var usesDemoData: Bool
    private var prefixes: [LeagueKey: [String]] = [:]
    private let bundle: Bundle
    private let session: URLSession
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder
    private let userAgent: String

    private static let logger = Logger(subsystem: "com.hardwood.nbastats", category: "league")

    /// `session` is for tests, which hand in one whose `protocolClasses` answer from a script; the
    /// app passes nothing and gets an ephemeral session (no cookies, no cache on disk).
    init(configuration: APIConfiguration,
         isDemoMode: Bool,
         bundle: Bundle = .main,
         session: URLSession? = nil) {
        self.configuration = configuration
        self.usesDemoData = isDemoMode
        self.bundle = bundle
        let sessionConfiguration = URLSessionConfiguration.ephemeral
        sessionConfiguration.timeoutIntervalForRequest = configuration.timeout
        self.session = session ?? URLSession(configuration: sessionConfiguration)
        self.decoder = APIClient.makeDecoder()
        self.encoder = APIClient.makeEncoder()
        self.userAgent = APIClient.makeUserAgent(bundle: bundle)
    }

    // MARK: - State

    /// True while the bundled fixtures are being served.
    var isDemoMode: Bool { usesDemoData }

    /// True when the configuration carries an API key, so a screen can say "your server needs its
    /// API key for changes" before a write is refused rather than after.
    var hasAPIKey: Bool {
        guard let key = configuration.apiKey else { return false }
        return !key.isEmpty
    }

    func setDemoMode(_ enabled: Bool) {
        usesDemoData = enabled
    }

    /// Points the client at a different server or key. Learned prefixes are kept: they describe
    /// the app's routes, not the host.
    func updateConfiguration(_ configuration: APIConfiguration) {
        self.configuration = configuration
    }

    /// Learns each league's route prefix from `GET /v1/leagues`. A league whose key this build
    /// does not know, or whose entry has no prefix, is ignored.
    func updatePrefixes(_ leagues: [LeagueInfo]) {
        for info in leagues {
            guard let keyText = info.key,
                  let key = LeagueKey(rawValue: keyText),
                  let prefix = info.apiPrefix else { continue }
            prefixes[key] = LeagueClient.prefixComponents(prefix)
        }
    }

    /// `"/v1/el"` becomes `["el"]`: the leading `v1` and any empty parts are dropped, because the
    /// base URL already ends in `/v1`.
    static func prefixComponents(_ apiPrefix: String) -> [String] {
        var parts: [String] = apiPrefix.split(separator: "/").map { String($0) }
        if let first = parts.first, first == "v1" {
            parts.removeFirst()
        }
        return parts
    }

    /// The URL a route resolves to right now.
    func url(for route: LeagueRoute) -> URL {
        let prefix: [String] = prefixes[route.league] ?? route.league.fallbackPrefix
        return route.url(base: configuration.versionedBaseURL, prefix: prefix)
    }

    // MARK: - Reads

    /// The one generic in the data layer. Callers always pass the type explicitly:
    /// `try await client.get(LeagueMatchup.self, route)`.
    func get<T: Decodable & Sendable>(_ type: T.Type, _ route: LeagueRoute) async throws -> T {
        if usesDemoData {
            return try demoPayload(type, route)
        }
        let request = makeRequest(url: url(for: route), method: "GET", body: nil)
        let data = try await perform(request)
        return try decode(type, from: data)
    }

    // MARK: - Writes

    /// Sends a write with an already-encoded body and returns the server's answer. Never
    /// retried, never coalesced. In demo mode it throws `demo_read_only` and sends nothing.
    @discardableResult
    func send(method: String, route: LeagueRoute, body: Data?) async throws -> Data {
        if usesDemoData {
            throw LeagueClient.demoReadOnly
        }
        let request = makeRequest(url: url(for: route), method: method, body: body)
        return try await perform(request)
    }

    /// Sends a write, encoding `json` with the app's shared encoder. Nil properties are omitted.
    @discardableResult
    func send<Body: Encodable & Sendable>(method: String, route: LeagueRoute, json: Body) async throws -> Data {
        if usesDemoData {
            throw LeagueClient.demoReadOnly
        }
        let payload: Data
        do {
            payload = try encoder.encode(json)
        } catch {
            throw APIError.decoding("Hardwood could not encode its own request: \(error.localizedDescription)")
        }
        let request = makeRequest(url: url(for: route), method: method, body: payload)
        return try await perform(request)
    }

    private static var demoReadOnly: APIError {
        APIError.server(code: "demo_read_only",
                        message: "Demo data is read-only. Turn off demo data in Settings to save to your server.",
                        status: 0,
                        recoverable: false)
    }

    // MARK: - Transport

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

    /// One request, no retry. A transport failure becomes an `APIError`; a non-2xx answer becomes
    /// the server's own error envelope (`league_unavailable`, `club_not_found`, `invalid_status`
    /// and the rest arrive as codes this build does not list, and show the server's message).
    private func perform(_ request: URLRequest) async throws -> Data {
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

    private func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        do {
            return try decoder.decode(type, from: data)
        } catch let error as DecodingError {
            LeagueClient.logger.error("Decoding \(String(describing: type), privacy: .public) failed: \(APIError.describe(error), privacy: .public)")
            throw APIError.decoding(APIError.describe(error))
        } catch {
            throw APIError.decoding(error.localizedDescription)
        }
    }

    // MARK: - Demo data

    private func demoPayload<T: Decodable>(_ type: T.Type, _ route: LeagueRoute) throws -> T {
        let name = route.fixtureName
        guard let data = fixtureData(named: name) else {
            throw APIError.demoModeMissingFixture(name + ".json")
        }
        return try decode(type, from: data)
    }

    /// The fixture folder first, then the bundle root: Xcode's synchronized groups may flatten
    /// `Resources/Fixtures` into the root, as `DemoAPIClient` also allows for.
    private func fixtureData(named name: String) -> Data? {
        let url = bundle.url(forResource: name, withExtension: "json", subdirectory: "Fixtures")
            ?? bundle.url(forResource: name, withExtension: "json")
        guard let url = url else { return nil }
        return try? Data(contentsOf: url)
    }
}
