#if os(macOS)
import SwiftUI

// The strip above the screen that says when the server is not answering, when it refused the API
// key, or when the app is showing bundled demo data, and offers the one-click ways out of each.
//
// WHY IT SITS ABOVE EVERY SCREEN
// A dead server makes every screen fail in its own way, each with its own retry button. One
// banner that says "the server is not answering" and offers the repair (restart it, read the log,
// use demo data) is clearer than fifteen copies of the same error. It draws nothing at all while
// the server is fine, so a working install has no chrome here.
//
// WHAT EACH STATE OFFERS
// - Not answering: Try Again, Use Demo Data, Copy Restart Command (the `launchctl` line that
//   restarts the launchd job), Open Logs Folder.
// - Key refused: Read Key from hardwood.env (the installer's file), and Settings.
// - Demo data: Use My Server.
// The server's own sentence is shown as the second line; it is never replaced by a guess.

struct MacBackendBanner: View {

    @EnvironmentObject private var environment: AppEnvironment
    @EnvironmentObject private var model: MacAppModel

    init() { }

    /// `http://127.0.0.1:8000`: the configured address without its `/v1` path.
    private var serverText: String {
        let url = environment.configuration.baseURL
        let scheme = url.scheme ?? "http"
        return scheme + "://" + environment.configuration.displayHost
    }

    var body: some View {
        content
    }

    @ViewBuilder private var content: some View {
        if environment.isDemoMode {
            demoStrip
        } else {
            liveStrip
        }
    }

    @ViewBuilder private var liveStrip: some View {
        switch model.server {
        case .unreachable(let message):
            unreachableStrip(message)
        case .keyRejected(let message):
            keyRejectedStrip(message)
        case .unknown, .reachable, .demo:
            EmptyView()
        }
    }

    // MARK: Demo data

    private var demoStrip: some View {
        HStack(spacing: Spacing.sm) {
            LeagueStrip(symbol: "shippingbox",
                        text: "Showing bundled demo data, not your server's.",
                        tint: Palette.warning)
            Button("Use My Server") {
                model.useLiveServer()
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
        }
        .padding(.horizontal, Spacing.md)
        .padding(.vertical, Spacing.xs)
    }

    // MARK: Not answering

    private func unreachableStrip(_ message: String) -> some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            LeagueStrip(symbol: "wifi.exclamationmark",
                        text: "Hardwood's server isn't answering at " + serverText + ". " + message,
                        tint: Palette.warning)
            HStack(spacing: Spacing.sm) {
                Button("Try Again") {
                    retry()
                }
                Button("Use Demo Data") {
                    model.useDemoData()
                }
                Button("Copy Restart Command") {
                    MacHardwoodEnv.copyRestartCommand()
                }
                Button("Open Logs Folder") {
                    MacHardwoodEnv.openLogsFolder()
                }
                Spacer(minLength: 0)
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
        }
        .padding(.horizontal, Spacing.md)
        .padding(.vertical, Spacing.xs)
    }

    // MARK: Key refused

    private func keyRejectedStrip(_ message: String) -> some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            LeagueStrip(symbol: "key",
                        text: message + " The installer keeps the key in hardwood.env.",
                        tint: Palette.warning)
            HStack(spacing: Spacing.sm) {
                Button("Read Key from hardwood.env") {
                    readKeyFromSettingsFile()
                }
                Button("Try Again") {
                    retry()
                }
                Spacer(minLength: 0)
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
        }
        .padding(.horizontal, Spacing.md)
        .padding(.vertical, Spacing.xs)
    }

    // MARK: Actions

    private func retry() {
        Task {
            await model.reconnect()
        }
    }

    /// Replaces the stored key with the installer's, then asks again.
    private func readKeyFromSettingsFile() {
        guard let key = MacHardwoodEnv.readAPIKey() else { return }
        let override = UserDefaults.standard.string(forKey: APIConfiguration.DefaultsKey.baseURL)
        Task {
            await environment.applyServerSettingsAndWait(baseURL: override, apiKey: key)
            await model.reconnect()
        }
    }
}
#endif
