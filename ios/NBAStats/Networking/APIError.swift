import Foundation

/// Everything that can go wrong between the app and its stats server.
///
/// The cases are deliberately few: the UI only ever has to decide between "you are offline",
/// "the server said no", and "Hardwood could not read the answer". The contract's error codes
/// (`contracts/CONTRACT.md` §7) are carried inside `.server` rather than exploded into cases, so
/// a code a future server invents still arrives intact.
public enum APIError: Error, Hashable, Sendable {
    /// No route to the server at all.
    case offline
    /// The server was reachable but did not answer in time.
    case timeout
    /// The server answered with an error envelope (`contracts/CONTRACT.md` §7).
    case server(code: String, message: String, status: Int, recoverable: Bool)
    /// The answer arrived but could not be read into the app's types.
    case decoding(String)
    /// Demo mode was asked for a payload that is not in the bundle.
    case demoModeMissingFixture(String)

    // MARK: - Contract error codes

    /// The error codes the service is documented to return (`contracts/CONTRACT.md` §7).
    ///
    /// Mapping a code here is what turns `{"code": "season_not_loaded"}` into a sentence a reader
    /// can act on. An unknown code is not an error: the server's own message is shown instead.
    public enum Code: String, Hashable, Sendable, CaseIterable {
        case badRequest = "bad_request"
        case invalidConfig = "invalid_config"
        case tooManyWidgets = "too_many_widgets"
        case unauthorized = "unauthorized"
        case playerNotFound = "player_not_found"
        case teamNotFound = "team_not_found"
        case gameNotFound = "game_not_found"
        case metricUnavailable = "metric_unavailable"
        case seasonNotLoaded = "season_not_loaded"
        case rateLimited = "rate_limited"
        case upstreamUnavailable = "upstream_unavailable"
        case internalError = "internal_error"

        /// The HTTP status the contract pairs with this code.
        public var httpStatus: Int {
            switch self {
            case .badRequest, .invalidConfig, .tooManyWidgets: return 400
            case .unauthorized: return 401
            case .playerNotFound, .teamNotFound, .gameNotFound: return 404
            case .metricUnavailable, .seasonNotLoaded: return 422
            case .rateLimited: return 429
            case .upstreamUnavailable: return 503
            case .internalError: return 500
            }
        }

        /// True when the same request, sent again later, could succeed.
        public var isRetryable: Bool {
            switch self {
            case .rateLimited, .upstreamUnavailable, .internalError:
                return true
            case .badRequest, .invalidConfig, .tooManyWidgets, .unauthorized,
                 .playerNotFound, .teamNotFound, .gameNotFound,
                 .metricUnavailable, .seasonNotLoaded:
                return false
            }
        }

        /// True when the failure is about the widget's configuration rather than the server, so
        /// the tile should offer "Edit widget" instead of "Try again".
        public var isConfigurationProblem: Bool {
            switch self {
            case .invalidConfig, .metricUnavailable, .playerNotFound, .teamNotFound, .gameNotFound:
                return true
            case .badRequest, .tooManyWidgets, .unauthorized, .seasonNotLoaded,
                 .rateLimited, .upstreamUnavailable, .internalError:
                return false
            }
        }

        /// The sentence to show a reader. Codes whose detail lives in the server's own message —
        /// which era a metric is missing from, which field failed validation — pass that message
        /// through rather than replacing it with something vaguer.
        public func userMessage(serverMessage: String) -> String {
            let detail = serverMessage.trimmingCharacters(in: .whitespacesAndNewlines)
            switch self {
            case .badRequest:
                return detail.isEmpty ? "Hardwood sent a request your stats server could not read." : detail
            case .invalidConfig:
                return detail.isEmpty ? "This widget's settings are not valid. Open its settings to fix them." : detail
            case .tooManyWidgets:
                return "This dashboard asked for too many widgets at once."
            case .unauthorized:
                return "Your stats server rejected Hardwood's API key. Check the key in Settings."
            case .playerNotFound:
                return detail.isEmpty ? "That player is not in your stats server's database." : detail
            case .teamNotFound:
                return detail.isEmpty ? "That team is not in your stats server's database." : detail
            case .gameNotFound:
                return detail.isEmpty ? "That game is not in your stats server's database." : detail
            case .metricUnavailable:
                return detail.isEmpty ? "This stat did not exist in the era you asked about." : detail
            case .seasonNotLoaded:
                return detail.isEmpty ? "That season has not been loaded into your stats server yet." : detail
            case .rateLimited:
                return "Your stats server asked Hardwood to slow down. Try again in a moment."
            case .upstreamUnavailable:
                return "Your stats server cannot reach the league's data right now, so these numbers may be behind."
            case .internalError:
                return "Your stats server ran into a problem loading this. Try again in a moment."
            }
        }
    }

    /// Builds the error for a non-2xx response out of its envelope.
    public init(body: APIErrorBody, status: Int) {
        self = .server(code: body.code, message: body.message, status: status, recoverable: body.recoverable)
    }

    /// The contract code, when this is a server error whose code this build knows.
    public var contractCode: Code? {
        guard case .server(let code, _, _, _) = self else { return nil }
        return Code(rawValue: code)
    }

    /// The raw code string, whether or not this build knows it.
    public var rawCode: String? {
        guard case .server(let code, _, _, _) = self else { return nil }
        return code
    }

    /// The HTTP status, for the server cases.
    public var httpStatus: Int? {
        guard case .server(_, _, let status, _) = self else { return nil }
        return status
    }

    // MARK: - Presentation

    /// A short headline for an error tile: `"Offline"`, `"Server error"`.
    public var title: String {
        switch self {
        case .offline: return "Offline"
        case .timeout: return "Timed out"
        case .decoding: return "Unexpected answer"
        case .demoModeMissingFixture: return "Not in demo data"
        case .server:
            if let code = contractCode, code.isConfigurationProblem { return "Check this widget" }
            return "Server error"
        }
    }

    /// A sentence a reader can act on. Never a bare code, never a stack trace.
    public var userMessage: String {
        switch self {
        case .offline:
            return "Hardwood could not reach your stats server. You are seeing the most recent numbers saved on this device."
        case .timeout:
            return "Your stats server took too long to answer. Hardwood is still showing the last numbers it has."
        case .server(let code, let message, let status, _):
            if let known = Code(rawValue: code) {
                return known.userMessage(serverMessage: message)
            }
            let detail = message.trimmingCharacters(in: .whitespacesAndNewlines)
            if !detail.isEmpty { return detail }
            return "Your stats server answered with an error (HTTP \(status))."
        case .decoding:
            return "Hardwood could not read your stats server's answer. The server may be running a newer version of the API than this app."
        case .demoModeMissingFixture(let name):
            return "Demo mode has no bundled data for this widget (\(name))."
        }
    }

    /// Extra guidance under the message, where there is something concrete to suggest.
    public var recoveryHint: String? {
        switch self {
        case .offline:
            return "Reconnect and pull down to refresh."
        case .timeout:
            return "Pull down to try again."
        case .server(let code, _, _, _):
            switch Code(rawValue: code) {
            case .some(.unauthorized):
                return "Settings › Stats server › API key."
            case .some(.invalidConfig), .some(.metricUnavailable):
                return "Open this widget's settings to pick something else."
            case .some(.seasonNotLoaded):
                return "Try the current season, or load that season on the server."
            default:
                return nil
            }
        case .decoding:
            return "Updating Hardwood usually fixes this."
        case .demoModeMissingFixture:
            return "Turn off demo mode in Settings to load live data."
        }
    }

    /// True when trying the exact same request again could plausibly succeed — which is what the
    /// "Try again" button in a failed tile means. Broader than `isAutomaticallyRetryable`: being
    /// offline is worth a manual retry once the reader is back on a network, but not worth the
    /// client hammering the socket two seconds later.
    public var isRetryable: Bool {
        switch self {
        case .offline, .timeout:
            return true
        case .server(let code, _, let status, let recoverable):
            if let known = Code(rawValue: code) { return known.isRetryable }
            return status >= 500 || recoverable
        case .decoding, .demoModeMissingFixture:
            return false
        }
    }

    /// The narrow subset `APIClient` retries on its own: a timeout, or a server that fell over.
    /// A 4xx is never retried — the request itself is what the server objected to.
    public var isAutomaticallyRetryable: Bool {
        switch self {
        case .timeout:
            return true
        case .server(_, _, let status, _):
            return status >= 500
        case .offline, .decoding, .demoModeMissingFixture:
            return false
        }
    }

    /// True when the server refused the API key, which Settings surfaces specially.
    public var isUnauthorized: Bool {
        contractCode == .unauthorized
    }

    // MARK: - Mapping

    /// Maps `URLSession`'s errors onto this enum.
    public static func from(urlError: URLError) -> APIError {
        switch urlError.code {
        case .notConnectedToInternet, .networkConnectionLost:
            return .offline
        case .timedOut:
            return .timeout
        case .cannotFindHost, .cannotConnectToHost, .dnsLookupFailed,
             .internationalRoamingOff, .dataNotAllowed, .callIsActive:
            // From the reader's point of view these are all "the server is not there".
            return .offline
        default:
            return .server(code: "network_error",
                           message: urlError.localizedDescription,
                           status: 0,
                           recoverable: true)
        }
    }

    /// True when this error is only "the work was called off" — a screen that went away, a
    /// superseded refresh. Cancellation is never shown to a reader as a failure.
    public static func isCancellation(_ error: Error) -> Bool {
        if error is CancellationError { return true }
        if let urlError = error as? URLError, urlError.code == .cancelled { return true }
        return false
    }

    /// Maps any error thrown inside the networking stack onto this enum.
    public static func from(_ error: Error) -> APIError {
        if let apiError = error as? APIError { return apiError }
        if let urlError = error as? URLError { return .from(urlError: urlError) }
        if let decodingError = error as? DecodingError { return .decoding(describe(decodingError)) }
        return .server(code: "network_error",
                       message: error.localizedDescription,
                       status: 0,
                       recoverable: true)
    }

    /// Turns a `DecodingError` into one line naming the key path that failed, which is the only
    /// part of it worth logging.
    public static func describe(_ error: DecodingError) -> String {
        func path(_ context: DecodingError.Context) -> String {
            let parts = context.codingPath.map { key -> String in
                if let index = key.intValue { return "[\(index)]" }
                return key.stringValue
            }
            return parts.isEmpty ? "the response body" : parts.joined(separator: ".")
        }
        switch error {
        case .keyNotFound(let key, let context):
            return "missing \"\(key.stringValue)\" at \(path(context))"
        case .typeMismatch(let type, let context):
            return "expected \(type) at \(path(context))"
        case .valueNotFound(let type, let context):
            return "no value for \(type) at \(path(context))"
        case .dataCorrupted(let context):
            return "malformed JSON at \(path(context))"
        @unknown default:
            return "the response could not be decoded"
        }
    }
}

extension APIError: LocalizedError {
    public var errorDescription: String? { userMessage }
    public var recoverySuggestion: String? { recoveryHint }
}
