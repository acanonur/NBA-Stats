import Foundation

/// Where the app talks to, and how patiently it waits.
///
/// The shipped defaults live in `Info.plist`; a value the user sets in Settings is written to
/// `UserDefaults` and wins over the bundled one, which is what makes "point this build at my
/// laptop" possible without a rebuild.
public struct APIConfiguration: Hashable, Sendable {
    public var baseURL: URL
    public var apiKey: String?
    public var timeout: TimeInterval

    /// The contract's own patience budget: long enough for a cold resolve, short enough that a
    /// dead server does not hold the dashboard hostage.
    public static let defaultTimeout: TimeInterval = 20

    /// Keys read from `Info.plist`.
    public enum InfoPlistKey {
        public static let baseURL = "HardwoodAPIBaseURL"
        public static let demoModeDefault = "HardwoodDemoModeDefault"
        /// Optional: builds that talk to a key-protected deployment can bake one in.
        public static let apiKey = "HardwoodAPIKey"
    }

    /// Keys read from `UserDefaults`. A value here overrides the bundled default.
    public enum DefaultsKey {
        public static let baseURL = "hardwood.api.baseURL"
        public static let apiKey = "hardwood.api.apiKey"
        public static let demoMode = "hardwood.demoMode"
    }

    /// The last-resort base URL, used only when neither the plist nor the user override parses.
    ///
    /// `URL(string:)` is optional and force unwrapping is banned, so the unreachable branch falls
    /// back to a file URL: every request against it fails visibly instead of crashing at launch.
    public static let defaultBaseURL: URL =
        URL(string: "http://localhost:8000/v1") ?? URL(fileURLWithPath: "/")

    public init(baseURL: URL, apiKey: String? = nil, timeout: TimeInterval = APIConfiguration.defaultTimeout) {
        self.baseURL = baseURL
        self.apiKey = apiKey
        self.timeout = timeout > 0 ? timeout : APIConfiguration.defaultTimeout
    }

    /// The configuration the app runs with: `Info.plist`, with any user override applied.
    public static var fromInfoPlist: APIConfiguration {
        resolved()
    }

    /// `fromInfoPlist` with the bundle and defaults injected, so tests can drive it.
    public static func resolved(bundle: Bundle = .main, defaults: UserDefaults = .standard) -> APIConfiguration {
        let plistValue = (bundle.object(forInfoDictionaryKey: InfoPlistKey.baseURL) as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let overrideValue = defaults.string(forKey: DefaultsKey.baseURL)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        // The override is consulted first; an empty string counts as "not set" so that clearing
        // the field in Settings falls back to the bundled default rather than to nothing.
        let candidates = [overrideValue, plistValue].compactMap { $0 }.filter { !$0.isEmpty }
        let url = candidates.compactMap { URL(string: $0) }.first ?? defaultBaseURL

        let plistKey = bundle.object(forInfoDictionaryKey: InfoPlistKey.apiKey) as? String
        let overrideKey = defaults.string(forKey: DefaultsKey.apiKey)
        let key = [overrideKey, plistKey].compactMap { $0 }.first { !$0.isEmpty }

        return APIConfiguration(baseURL: url, apiKey: key, timeout: defaultTimeout)
    }

    /// Whether the app should serve bundled fixtures instead of talking to a server.
    ///
    /// A choice the user has made is stored in `UserDefaults` and wins; otherwise the bundled
    /// `HardwoodDemoModeDefault` decides, so a fresh install of a build with no backend still
    /// shows a full dashboard.
    public static func isDemoModeEnabled(bundle: Bundle = .main, defaults: UserDefaults = .standard) -> Bool {
        if defaults.object(forKey: DefaultsKey.demoMode) != nil {
            return defaults.bool(forKey: DefaultsKey.demoMode)
        }
        if let flag = bundle.object(forInfoDictionaryKey: InfoPlistKey.demoModeDefault) as? Bool {
            return flag
        }
        if let number = bundle.object(forInfoDictionaryKey: InfoPlistKey.demoModeDefault) as? NSNumber {
            return number.boolValue
        }
        return false
    }

    /// Records the user's demo-mode choice.
    public static func setDemoModeEnabled(_ enabled: Bool, defaults: UserDefaults = .standard) {
        defaults.set(enabled, forKey: DefaultsKey.demoMode)
    }

    /// Records (or with `nil`, clears) the user's base-URL override. Returns false when the text
    /// is not a URL at all, so Settings can refuse it instead of silently breaking the app.
    @discardableResult
    public static func setBaseURLOverride(_ text: String?, defaults: UserDefaults = .standard) -> Bool {
        guard let text = text?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else {
            defaults.removeObject(forKey: DefaultsKey.baseURL)
            return true
        }
        guard let url = URL(string: text), url.scheme != nil else { return false }
        defaults.set(text, forKey: DefaultsKey.baseURL)
        return true
    }

    /// Records (or with `nil`, clears) the user's API key.
    public static func setAPIKeyOverride(_ key: String?, defaults: UserDefaults = .standard) {
        guard let key = key?.trimmingCharacters(in: .whitespacesAndNewlines), !key.isEmpty else {
            defaults.removeObject(forKey: DefaultsKey.apiKey)
            return
        }
        defaults.set(key, forKey: DefaultsKey.apiKey)
    }

    /// The base every route hangs off: the configured URL with `/v1` appended when the configured
    /// value stops at the host, so both `http://host:8000` and `http://host:8000/v1` work.
    public var versionedBaseURL: URL {
        var trimmed = baseURL.absoluteString
        while trimmed.hasSuffix("/") { trimmed.removeLast() }
        guard !trimmed.isEmpty else { return baseURL }
        if trimmed.hasSuffix("/v1") {
            return URL(string: trimmed) ?? baseURL
        }
        return URL(string: trimmed + "/v1") ?? baseURL
    }

    /// A short description for the Settings screen: `"localhost:8000"`.
    public var displayHost: String {
        guard let host = baseURL.host else { return baseURL.absoluteString }
        if let port = baseURL.port { return "\(host):\(port)" }
        return host
    }
}
