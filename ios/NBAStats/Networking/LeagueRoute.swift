import Foundation

// The league routes: which league, which path under the league's prefix, which query, and which
// bundled fixture answers it in demo mode.
//
// WHY ROUTES ARE VALUES
// A screen builds a `LeagueRoute` and uses it twice: as the argument to `LeagueClient.get`, and as
// half of the key of `.task(id:)`. Because the route is `Hashable`, "the same request" is exactly
// "an equal route", so changing a club, a round or a date restarts the load and an unrelated
// redraw does not. Nothing here touches the network; `LeagueClient` turns a route into a URL.
//
// EVERY PATH AND PARAMETER IN THIS FILE WAS READ FROM THE BACKEND, NOT FROM THE DESIGN
// (`backend/nbastats/api/routes_*.py` for the NBA, `backend/nbastats/euroleague/api/routes.py` for
// the EuroLeague). Where the two leagues spell a parameter differently, `LeagueRoutes` spells it
// each league's way and the screens never see the difference:
//
//   availability   NBA `teamId`, `date`            EuroLeague `clubCode`, `round`
//   projections    NBA `date` (next or ISO)        EuroLeague `round` (next or a number)
//   review         NBA `date`, `season`            EuroLeague `round`, `season`
//   news           `teamId`, `playerId`, `limit`   (the same for both: the design guessed clubCode)
//   defence        `scheme` is EuroLeague-only     (the NBA has one scheme, G, F, C)
//
// The league prefix is never written here. The NBA's routes hang off the base URL's `/v1`; the
// EuroLeague's off `/v1/el`; `LeagueClient` learns both from `GET /v1/leagues` and falls back to
// `LeagueKey.fallbackPrefix` before that has loaded.

// MARK: - League key

/// The two leagues. The raw values are the backend's own league keys (`GET /v1/leagues` and the
/// `league` member of every payload), so a key can be compared without a table.
enum LeagueKey: String, CaseIterable, Identifiable, Hashable, Sendable, Codable {
    case nba
    case euroleague

    var id: String { rawValue }

    var displayName: String { self == .nba ? "NBA" : "EuroLeague" }

    /// The route prefix, as path components after the base URL's `/v1`, until `/v1/leagues` has
    /// said what it really is.
    var fallbackPrefix: [String] { self == .nba ? [] : ["el"] }

    /// The prefix the bundled demo fixtures of this league carry in their file names.
    var fixturePrefix: String { self == .nba ? "league_nba_" : "league_el_" }

    /// Minutes in a regulation game: 48 for the NBA, 40 for the EuroLeague.
    var regulationMinutes: Int { self == .nba ? 48 : 40 }
}

// MARK: - Route

/// One name and value of a query string. Both are plain text; the value is percent-encoded when
/// the URL is built.
struct LeagueQueryItem: Hashable, Sendable {
    var name: String
    var value: String
}

/// A request to a league route, before it has a host.
struct LeagueRoute: Hashable, Sendable {
    var league: LeagueKey
    /// The path after the league's prefix, one component per element.
    var path: [String]
    var query: [LeagueQueryItem]
    /// The demo fixture that answers this route, without the league prefix or `.json`.
    var fixture: String
    /// False for the four NBA routes the existing flat fixtures (`teams`, `team_detail`,
    /// `games_scoreboard`, `game_box`) answer, which have no `league_nba_` copy.
    var usesLeagueFixturePrefix: Bool = true

    /// The bundle resource name of the demo fixture, without `.json`.
    var fixtureName: String {
        (usesLeagueFixturePrefix ? league.fixturePrefix : "") + fixture
    }

    /// The full URL: `base` plus the league's prefix, the path, and the query. Components that are
    /// empty are skipped and query items with an empty value are dropped. `+` is escaped as `%2B`
    /// exactly as `Endpoints` does, because a server reads a bare `+` as a space.
    func url(base: URL, prefix: [String]) -> URL {
        var result = base
        for component in prefix + path where !component.isEmpty {
            result = result.appendingPathComponent(component)
        }
        let items: [URLQueryItem] = query
            .filter { !$0.value.isEmpty }
            .map { URLQueryItem(name: $0.name, value: $0.value) }
        guard !items.isEmpty else { return result }
        guard var components = URLComponents(url: result, resolvingAgainstBaseURL: false) else {
            return result
        }
        components.queryItems = items
        if let encoded = components.percentEncodedQuery {
            components.percentEncodedQuery = encoded.replacingOccurrences(of: "+", with: "%2B")
        }
        return components.url ?? result
    }
}

// MARK: - Routes

/// One function per route. Every query value that is nil or empty is left out, so a screen can
/// pass its optional state straight through.
enum LeagueRoutes {

    // MARK: Builders

    static func item(_ name: String, _ value: String?) -> LeagueQueryItem? {
        guard let value = value, !value.isEmpty else { return nil }
        return LeagueQueryItem(name: name, value: value)
    }

    static func item(_ name: String, _ value: Int?) -> LeagueQueryItem? {
        guard let value = value else { return nil }
        return LeagueQueryItem(name: name, value: String(value))
    }

    static func query(_ items: [LeagueQueryItem?]) -> [LeagueQueryItem] {
        items.compactMap { $0 }
    }

    // MARK: League state

    /// `GET /v1/leagues`: a bare array of `LeagueInfo`. The NBA's route; there is no
    /// EuroLeague twin.
    static func leagues() -> LeagueRoute {
        LeagueRoute(league: .nba, path: ["leagues"], query: [], fixture: "leagues")
    }

    /// `GET /v1/el/meta`: `ElMeta`. Answers whatever the EuroLeague's state is.
    static func elMeta() -> LeagueRoute {
        LeagueRoute(league: .euroleague, path: ["meta"], query: [], fixture: "meta")
    }

    // MARK: Matchup

    /// `GET {prefix}/teams/{id}/matchup`: a team's next game. `window` is the games of recent form.
    static func teamMatchup(_ league: LeagueKey, team: String, window: Int? = nil) -> LeagueRoute {
        LeagueRoute(league: league,
                    path: ["teams", team, "matchup"],
                    query: query([item("window", window)]),
                    fixture: "team_matchup")
    }

    /// `GET {prefix}/matchups`: two teams side by side, home first.
    static func pairMatchup(_ league: LeagueKey,
                            home: String,
                            away: String,
                            window: Int? = nil,
                            season: String? = nil) -> LeagueRoute {
        LeagueRoute(league: league,
                    path: ["matchups"],
                    query: query([item("homeTeamId", home),
                                  item("awayTeamId", away),
                                  item("window", window),
                                  item("season", season)]),
                    fixture: "team_matchup")
    }

    /// `GET {prefix}/games/{id}/matchup`: a game's matchup with inputs cut off before it began.
    static func gameMatchup(_ league: LeagueKey, gameId: String, window: Int? = nil) -> LeagueRoute {
        LeagueRoute(league: league,
                    path: ["games", gameId, "matchup"],
                    query: query([item("window", window)]),
                    fixture: "game_matchup")
    }

    // MARK: Defence by position

    /// `GET {prefix}/teams/{id}/defense-by-position`. `window` is 0 for the season or the last N
    /// games; `basis` is perGame or perMinute; `scheme` (gfc or workbook5) is EuroLeague-only.
    static func teamDefense(_ league: LeagueKey,
                            team: String,
                            window: Int? = nil,
                            basis: String? = nil,
                            scheme: String? = nil,
                            season: String? = nil) -> LeagueRoute {
        LeagueRoute(league: league,
                    path: ["teams", team, "defense-by-position"],
                    query: defenseQuery(league, window: window, basis: basis, scheme: scheme, season: season),
                    fixture: "defense_by_position")
    }

    /// `GET {prefix}/defense-by-position`: every team's defence, one row each.
    static func defenseTable(_ league: LeagueKey,
                             window: Int? = nil,
                             basis: String? = nil,
                             scheme: String? = nil,
                             season: String? = nil) -> LeagueRoute {
        LeagueRoute(league: league,
                    path: ["defense-by-position"],
                    query: defenseQuery(league, window: window, basis: basis, scheme: scheme, season: season),
                    fixture: "defense_by_position_table")
    }

    private static func defenseQuery(_ league: LeagueKey,
                                     window: Int?,
                                     basis: String?,
                                     scheme: String?,
                                     season: String?) -> [LeagueQueryItem] {
        let schemeItem: LeagueQueryItem? = league == .euroleague ? item("scheme", scheme) : nil
        return query([item("window", window),
                      item("basis", basis),
                      schemeItem,
                      item("season", season)])
    }

    // MARK: Projections

    /// `GET /v1/projections` (NBA): a day's slate. `date` is `next`, `latest` or an ISO day.
    static func slate(date: String? = nil) -> LeagueRoute {
        LeagueRoute(league: .nba,
                    path: ["projections"],
                    query: query([item("date", date)]),
                    fixture: "slate_projections")
    }

    /// `GET /v1/el/projections`: a round's slate. `round` is `next` or a round number as text.
    static func elSlate(round: String? = nil) -> LeagueRoute {
        LeagueRoute(league: .euroleague,
                    path: ["projections"],
                    query: query([item("round", round)]),
                    fixture: "slate_projections")
    }

    /// `GET /v1/el/rounds/{n}`: every game of a round with its projection. In demo mode a round
    /// that is complete is answered by the completed-round fixture, so pass `complete: true` for
    /// one (the screen knows from `ElRoundSummary.status`).
    static func elRound(_ number: Int, complete: Bool = false) -> LeagueRoute {
        LeagueRoute(league: .euroleague,
                    path: ["rounds", String(number)],
                    query: [],
                    fixture: complete ? "round_view_complete" : "round_view")
    }

    /// `GET {prefix}/games/{id}/projection`: the current, locked and historical projections.
    static func gameProjection(_ league: LeagueKey, gameId: String) -> LeagueRoute {
        LeagueRoute(league: league,
                    path: ["games", gameId, "projection"],
                    query: [],
                    fixture: "game_projection_detail")
    }

    /// `GET {prefix}/projections/review`. The NBA reviews by `date`, the EuroLeague by `round`;
    /// the other league's parameter is ignored, so a screen can pass both.
    static func review(_ league: LeagueKey,
                       date: String? = nil,
                       round: Int? = nil,
                       season: String? = nil) -> LeagueRoute {
        let scopeItem: LeagueQueryItem? = league == .nba ? item("date", date) : item("round", round)
        return LeagueRoute(league: league,
                           path: ["projections", "review"],
                           query: query([scopeItem, item("season", season)]),
                           fixture: "projection_review")
    }

    // MARK: Availability and news

    /// `GET {prefix}/availability`. `team` is a team id (NBA `teamId`) or a club code
    /// (EuroLeague `clubCode`); the NBA reads `date`, the EuroLeague `round`.
    static func availability(_ league: LeagueKey,
                             team: String? = nil,
                             date: String? = nil,
                             round: Int? = nil) -> LeagueRoute {
        let items: [LeagueQueryItem?]
        switch league {
        case .nba:
            items = [item("teamId", team), item("date", date)]
        case .euroleague:
            items = [item("clubCode", team), item("round", round)]
        }
        return LeagueRoute(league: league,
                           path: ["availability"],
                           query: query(items),
                           fixture: "availability_report")
    }

    /// The queue of report rows whose player matched nobody: the NBA's
    /// `GET /v1/availability/review-queue`, and the EuroLeague's `GET /v1/el/review-queue`, which
    /// also lists people the workbook minted a code for.
    static func reviewQueue(_ league: LeagueKey) -> LeagueRoute {
        switch league {
        case .nba:
            return LeagueRoute(league: .nba,
                               path: ["availability", "review-queue"],
                               query: [],
                               fixture: "availability_review_queue")
        case .euroleague:
            return LeagueRoute(league: .euroleague,
                               path: ["review-queue"],
                               query: [],
                               fixture: "review_queue")
        }
    }

    /// `GET {prefix}/news`: headlines. `team` is `teamId` in both leagues.
    static func news(_ league: LeagueKey,
                     team: String? = nil,
                     limit: Int? = nil,
                     player: String? = nil) -> LeagueRoute {
        LeagueRoute(league: league,
                    path: ["news"],
                    query: query([item("teamId", team), item("playerId", player), item("limit", limit)]),
                    fixture: "news")
    }

    /// `GET {prefix}/sources`: where every number came from.
    static func sources(_ league: LeagueKey) -> LeagueRoute {
        LeagueRoute(league: league, path: ["sources"], query: [], fixture: "sources")
    }

    /// `GET {prefix}/model-settings`.
    static func modelSettings(_ league: LeagueKey) -> LeagueRoute {
        LeagueRoute(league: league, path: ["model-settings"], query: [], fixture: "model_settings")
    }

    // MARK: EuroLeague only

    /// `GET /v1/el/teams`: every club.
    static func elTeams() -> LeagueRoute {
        LeagueRoute(league: .euroleague, path: ["teams"], query: [], fixture: "teams")
    }

    /// `GET /v1/el/teams/{clubCode}`: one club.
    static func elClub(_ code: String) -> LeagueRoute {
        LeagueRoute(league: .euroleague, path: ["teams", code], query: [], fixture: "club_view")
    }

    /// `GET /v1/el/rounds/{n}/scorers`.
    static func elScorers(round: Int, perClub: Int? = nil) -> LeagueRoute {
        LeagueRoute(league: .euroleague,
                    path: ["rounds", String(round), "scorers"],
                    query: query([item("perClub", perClub)]),
                    fixture: "round_scorers")
    }

    /// `GET /v1/el/games`: the schedule and results.
    static func elGames(round: Int? = nil, club: String? = nil) -> LeagueRoute {
        LeagueRoute(league: .euroleague,
                    path: ["games"],
                    query: query([item("round", round), item("clubCode", club)]),
                    fixture: "games")
    }

    /// `GET /v1/el/games/{id}`: a box score.
    static func elBox(_ gameId: String) -> LeagueRoute {
        LeagueRoute(league: .euroleague, path: ["games", gameId], query: [], fixture: "box_score")
    }

    /// `GET /v1/el/stats/players`. `perMode` is PerGame, Totals or Per40; `sort` is a stat key.
    static func elPlayerStats(perMode: String? = nil,
                              club: String? = nil,
                              minGames: Int? = nil,
                              sort: String? = nil,
                              limit: Int? = nil) -> LeagueRoute {
        LeagueRoute(league: .euroleague,
                    path: ["stats", "players"],
                    query: query([item("perMode", perMode),
                                  item("sort", sort),
                                  item("clubCode", club),
                                  item("minGames", minGames),
                                  item("limit", limit)]),
                    fixture: "player_stats")
    }

    /// `GET /v1/el/players/{personCode}`: one player.
    static func elPlayer(_ personCode: String) -> LeagueRoute {
        LeagueRoute(league: .euroleague,
                    path: ["players", personCode],
                    query: [],
                    fixture: "player_detail")
    }

    /// `GET /v1/el/players/{personCode}/gamelog`.
    static func elGameLog(_ personCode: String) -> LeagueRoute {
        LeagueRoute(league: .euroleague,
                    path: ["players", personCode, "gamelog"],
                    query: [],
                    fixture: "player_gamelog")
    }

    /// `GET /v1/el/ratings`: every club's rating, optionally as of an earlier round.
    static func elRatings(asOfRound: Int? = nil) -> LeagueRoute {
        LeagueRoute(league: .euroleague,
                    path: ["ratings"],
                    query: query([item("asOfRound", asOfRound)]),
                    fixture: "ratings")
    }

    /// `GET /v1/el/method`: constants with provenance, deviations and limitations.
    static func elMethod() -> LeagueRoute {
        LeagueRoute(league: .euroleague, path: ["method"], query: [], fixture: "method")
    }

    // MARK: NBA routes that already have payload types (answered by the flat fixtures)

    /// `GET /v1/teams`: `TeamsResponse`.
    static func nbaTeams() -> LeagueRoute {
        LeagueRoute(league: .nba,
                    path: ["teams"],
                    query: [],
                    fixture: "teams",
                    usesLeagueFixturePrefix: false)
    }

    /// `GET /v1/teams/{id}?rosterMetrics=a,b`: `TeamDetailResponse`, with the roster's chosen
    /// player metrics. `rosterMetrics` are metric keys from the catalog.
    static func nbaTeam(_ teamId: Int, rosterMetrics: [String] = []) -> LeagueRoute {
        let keys = rosterMetrics.filter { !$0.isEmpty }.joined(separator: ",")
        return LeagueRoute(league: .nba,
                           path: ["teams", String(teamId)],
                           query: query([item("rosterMetrics", keys)]),
                           fixture: "team_detail",
                           usesLeagueFixturePrefix: false)
    }

    /// The same route with the metric keys already joined as the wire wants them: `"pts_pg,reb_pg"`.
    static func nbaTeam(_ teamId: Int, rosterMetrics: String) -> LeagueRoute {
        LeagueRoute(league: .nba,
                    path: ["teams", String(teamId)],
                    query: query([item("rosterMetrics", rosterMetrics)]),
                    fixture: "team_detail",
                    usesLeagueFixturePrefix: false)
    }

    /// `GET /v1/games?date=`: `ScoreboardResponse`.
    static func nbaGames(date: String? = nil) -> LeagueRoute {
        LeagueRoute(league: .nba,
                    path: ["games"],
                    query: query([item("date", date)]),
                    fixture: "games_scoreboard",
                    usesLeagueFixturePrefix: false)
    }

    /// `GET /v1/games/{id}/box?view=both`: `BoxScoreResponse`.
    static func nbaBox(_ gameId: String) -> LeagueRoute {
        LeagueRoute(league: .nba,
                    path: ["games", gameId, "box"],
                    query: [LeagueQueryItem(name: "view", value: "both")],
                    fixture: "game_box",
                    usesLeagueFixturePrefix: false)
    }

    // MARK: Writes

    /// `POST {prefix}/availability`: record a status. Body: `ElStatusWrite` or `NbaStatusWrite`.
    static func recordStatus(_ league: LeagueKey) -> LeagueRoute {
        LeagueRoute(league: league, path: ["availability"], query: [], fixture: "")
    }

    /// `DELETE {prefix}/availability/{id}`: retract a status. `id` is the entry's `statusId`
    /// (EuroLeague) or `overrideId` (NBA).
    static func retractStatus(_ league: LeagueKey, id: Int) -> LeagueRoute {
        LeagueRoute(league: league, path: ["availability", String(id)], query: [], fixture: "")
    }

    /// `POST {prefix}/news/links`: paste a headline link. Body: `LeagueLinkWrite`.
    static func pasteLink(_ league: LeagueKey) -> LeagueRoute {
        LeagueRoute(league: league, path: ["news", "links"], query: [], fixture: "")
    }

    /// `PATCH {prefix}/model-settings`. Body: `LeagueSettingsPatch`.
    static func patchModelSettings(_ league: LeagueKey) -> LeagueRoute {
        LeagueRoute(league: league, path: ["model-settings"], query: [], fixture: "")
    }
}
