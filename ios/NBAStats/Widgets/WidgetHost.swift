import Foundation
import SwiftUI

// MARK: - Host environment

/// The action a tile calls when the reader taps "Try again" on a failed widget.
///
/// `WidgetHost`'s initializer is fixed by `WidgetContainer`, so the retry handler reaches it
/// through the environment instead. The dashboard screen installs one with
/// `.widgetRetryAction { id in ... }`; anything that renders a host without installing one — a
/// preview, a screenshot harness, a test — gets a button that does nothing rather than a crash.
public struct WidgetRetryAction {
    private let handler: (String) -> Void

    public init(handler: @escaping (String) -> Void) {
        self.handler = handler
    }

    /// Asks for the widget with this id to be resolved again.
    public func callAsFunction(_ widgetID: String) {
        handler(widgetID)
    }
}

private struct WidgetRetryActionKey: EnvironmentKey {
    /// Computed rather than stored so the default never becomes shared mutable state.
    static var defaultValue: WidgetRetryAction { WidgetRetryAction { _ in } }
}

/// The reader's favourite player and team, so a widget can pick them out of a list.
///
/// Both are optional and default to `nil`: with no favourite set, nothing is highlighted, which
/// is exactly the right behaviour rather than a fallback.
public struct WidgetFavorites: Hashable, Sendable {
    public var playerID: PlayerID?
    public var teamID: TeamID?

    public init(playerID: PlayerID? = nil, teamID: TeamID? = nil) {
        self.playerID = playerID
        self.teamID = teamID
    }

    public func matches(player: PlayerRef?) -> Bool {
        guard let playerID = playerID, let player = player else { return false }
        return player.playerId == playerID
    }

    public func matches(team: TeamRef?) -> Bool {
        guard let teamID = teamID, let team = team else { return false }
        return team.teamId == teamID
    }
}

private struct WidgetFavoritesKey: EnvironmentKey {
    static var defaultValue: WidgetFavorites { WidgetFavorites() }
}

public extension EnvironmentValues {
    /// What a failed tile calls when the reader asks for another attempt.
    var widgetRetryAction: WidgetRetryAction {
        get { self[WidgetRetryActionKey.self] }
        set { self[WidgetRetryActionKey.self] = newValue }
    }

    /// The subjects the reader has starred, highlighted wherever they appear in a list.
    var widgetFavorites: WidgetFavorites {
        get { self[WidgetFavoritesKey.self] }
        set { self[WidgetFavoritesKey.self] = newValue }
    }
}

public extension View {
    /// Installs the handler every failed tile below this view calls to retry, by widget id.
    func widgetRetryAction(_ handler: @escaping (String) -> Void) -> some View {
        environment(\.widgetRetryAction, WidgetRetryAction(handler: handler))
    }

    /// Tells every widget below this view which subjects are the reader's favourites.
    func widgetFavorites(playerID: PlayerID?, teamID: TeamID?) -> some View {
        environment(\.widgetFavorites, WidgetFavorites(playerID: playerID, teamID: teamID))
    }
}

// MARK: - Host

/// Turns one `WidgetState` into the view that belongs to it.
///
/// This is the single place where a payload meets a view: `WidgetContainer` owns the chrome —
/// title, era badge, overflow menu — and hands the interior to this type. Four states and
/// sixteen payloads are all handled here, so adding a widget kind is a change in exactly two
/// files: the payload in `Core`, and the one `case` below.
public struct WidgetHost: View {
    private let widget: DashboardWidget
    private let state: WidgetState
    private let isEditing: Bool

    @Environment(\.widgetRetryAction) private var retryAction
    @Environment(\.isBroadsheet) private var isBroadsheet

    public init(widget: DashboardWidget, state: WidgetState, isEditing: Bool) {
        self.widget = widget
        self.state = state
        self.isEditing = isEditing
    }

    public var body: some View {
        content
            .frame(maxWidth: .infinity, alignment: .leading)
            // While the dashboard is being edited, a tap anywhere on the tile belongs to the
            // chrome, which opens the configuration sheet. The interior stops taking hits so a
            // button inside a widget cannot swallow that gesture.
            .allowsHitTesting(!isEditing)
            .accessibilityElement(children: .contain)
    }

    // MARK: State

    @ViewBuilder private var content: some View {
        switch state {
        case .loading:
            LoadingTile(size: widget.size)
        case .loaded(let payload, let availability, let notes, let stale):
            loaded(payload: payload, availability: availability, notes: notes, stale: stale)
        case .failed(let error):
            ErrorTile(message: error.userMessage,
                      isRetryable: error.isRetryable,
                      size: widget.size) {
                retryAction(widget.id)
            }
        case .unavailable(let reason):
            UnavailableTile(reason: reason,
                            metricName: widget.title,
                            season: widget.configString("season"),
                            size: widget.size)
        }
    }

    private func loaded(payload: WidgetPayload,
                        availability: MetricAvailability,
                        notes: [String],
                        stale: Bool) -> some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            view(for: payload)
            footer(lines: footnotes(kind: payload.kind, availability: availability, notes: notes),
                   stale: stale)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Payload dispatch

    /// The sixteen payload cases, each rendered by the view that owns it. Every widget view takes
    /// the same `(payload:size:)` shape, so this stays a flat mapping with nothing to decide.
    @ViewBuilder private func view(for payload: WidgetPayload) -> some View {
        switch payload {
        case .statTile(let value):
            StatTileWidget(payload: value, size: widget.size)
        case .playerSnapshot(let value):
            PlayerSnapshotWidget(payload: value, size: widget.size)
        case .leaderboard(let value):
            LeaderboardWidget(payload: value, size: widget.size)
        case .gameLog(let value):
            GameLogWidget(payload: value, size: widget.size)
        case .trendChart(let value):
            TrendChartWidget(payload: value, size: widget.size)
        case .fourFactors(let value):
            FourFactorsWidget(payload: value, size: widget.size)
        case .shotProfile(let value):
            ShotProfileWidget(payload: value, size: widget.size)
        case .comparison(let value):
            ComparisonWidget(payload: value, size: widget.size)
        case .scoreboard(let value):
            ScoreboardWidget(payload: value, size: widget.size)
        case .dailyMovers(let value):
            DailyMoversWidget(payload: value, size: widget.size)
        case .teamEfficiency(let value):
            TeamEfficiencyWidget(payload: value, size: widget.size)
        case .nextGameProjection(let value):
            NextGameProjectionWidget(payload: value, size: widget.size)
        case .projectionBoard(let value):
            ProjectionBoardWidget(payload: value, size: widget.size)
        case .fantasyDraftBoard(let value):
            FantasyDraftBoardWidget(payload: value, size: widget.size)
        case .fantasyTrade(let value):
            FantasyTradeWidget(payload: value, size: widget.size)
        case .careerArc(let value):
            CareerArcWidget(payload: value, size: widget.size)
        }
    }

    // MARK: Footnotes

    /// The server's own notes, or — when it sent none — the sentence the era treatment implies.
    ///
    /// `kind` is here for one reason. `.estimated` means two different things in this app: a
    /// season number derived from the box score because the league published no possession data
    /// before 1996-97, and a *projection*, which is an estimate of a game that has not been
    /// played. The pre-1997 sentence under a next-game projection would be simply false, so the
    /// projection gets the sentence that is true of it (`docs/PROJECTION.md` §7 rule 5).
    private func footnotes(kind: WidgetKind, availability: MetricAvailability, notes: [String]) -> [String] {
        let cleaned = notes.filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        guard cleaned.isEmpty else { return cleaned }
        switch availability {
        case .full:
            return []
        case .estimated where kind == .fantasyDraftBoard || kind == .fantasyTrade:
            return ["Fantasy value, not a record. These are z-scores against this season's player pool, and FG% and FT% are weighted by how often a player shoots."]
        case .estimated where kind == .nextGameProjection || kind == .projectionBoard:
            return ["Projected, not recorded. These are estimates of a game that has not been played, and each carries its own range."]
        case .estimated:
            return ["Estimated from the box score. The league did not publish possession data before 1996-97."]
        case .partial:
            return ["Some of the games behind these numbers are missing the inputs they need."]
        case .unavailable:
            return []
        }
    }

    /// One footnote line, in the page's own voice.
    ///
    /// The footnote is the only piece of chrome that shows in the ordinary loaded state, so it is
    /// worth following the broadsheet's typography. The loading, failure and era-gap tiles still
    /// render in the app's tile treatment on a broadsheet page — a deliberate stop, recorded in
    /// docs/BROADSHEET.md §8, rather than an oversight.
    ///
    /// Written as two branches instead of a conditional font: `hardwoodText` sets the font *and*
    /// the colour, so applying it after a broadsheet font would silently undo it.
    @ViewBuilder private func footnote(_ line: String) -> some View {
        if isBroadsheet {
            Text(line)
                .font(Broadsheet.serif(11))
                .foregroundStyle(Broadsheet.textMuted)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            Text(line)
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder private func footer(lines: [String], stale: Bool) -> some View {
        if stale || !lines.isEmpty {
            HStack(alignment: .top, spacing: Spacing.xs) {
                if stale {
                    StalenessDot(isStale: true)
                        .padding(.top, 3)
                }
                if !lines.isEmpty {
                    VStack(alignment: .leading, spacing: Spacing.xxs) {
                        ForEach(lines.indices, id: \.self) { index in
                            footnote(lines[index])
                        }
                    }
                }
                Spacer(minLength: 0)
            }
        }
    }
}

#if DEBUG
#Preview("Every state") {
    let widget = DashboardWidget(kind: .statTile,
                                 title: "True Shooting",
                                 size: .small,
                                 config: ["metric": .string("ts_pct"), "season": .string("1971-72")])
    return ScrollView {
        VStack(spacing: Spacing.md) {
            WidgetHost(widget: widget, state: .loading, isEditing: false)
            WidgetHost(widget: widget,
                       state: .loaded(.statTile(.preview), availability: .estimated, notes: [], stale: true),
                       isEditing: false)
                .hardwoodCard()
            WidgetHost(widget: widget,
                       state: .failed(.offline),
                       isEditing: false)
            WidgetHost(widget: widget,
                       state: .unavailable(reason: "Usage rate needs individual turnovers, which the league did not record until 1977-78."),
                       isEditing: false)
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
    .widgetRetryAction { _ in }
}

#Preview("Loaded payloads") {
    ScrollView {
        VStack(spacing: Spacing.md) {
            ForEach(WidgetKind.allCases, id: \.self) { kind in
                WidgetHost(widget: DashboardWidget(kind: kind, size: .medium, config: [:]),
                           state: .loaded(WidgetPayload.preview(for: kind),
                                          availability: .full,
                                          notes: [],
                                          stale: false),
                           isEditing: false)
                    .hardwoodCard()
            }
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}
#endif
