#if os(macOS)
import Foundation

// The words of the Method screen that the app itself writes: the NBA's explanation of what its
// screens show, and the two sentences the EuroLeague page adds around the server's own text.
//
// WHY THE NBA HAS PROSE AND THE EUROLEAGUE HAS A ROUTE
// The EuroLeague's page is read from the server (`GET /v1/el/method`: the constants, the places
// Hardwood differs from the workbook, and the known limits), because that model came from a workbook
// and the differences are a fact about this server's data. The NBA has no such route, so its page is
// this bundled text plus the model's settings table, which the server does send.
//
// THE RULES THE WORDING FOLLOWS
// The app shows projected scores and recorded statistics and has no betting features, so the text
// never compares a projection with an outside number and never says how likely a team is to win.
// Every claim here is a description of what a screen does with the server's numbers, written in
// plain words; if the model changes, this text and the server's own notes have to change together.

/// One headed block of the page.
struct MacMethodSection: Identifiable, Hashable {
    let id: String
    let heading: String
    let paragraphs: [String]
}

enum MacMethodText {

    /// What the NBA screens show, and how the numbers behind them are made.
    static let nba: [MacMethodSection] = [
        MacMethodSection(
            id: "purpose",
            heading: "What these screens show",
            paragraphs: [
                "The NBA screens answer three questions. How has each team been scoring, and what has it allowed? How many points does each team give up to opponents at each listed position? What score is each team projected to post in its next game? Every number comes from your Hardwood server. The app formats and sorts them and works nothing out itself."
            ]
        ),
        MacMethodSection(
            id: "form",
            heading: "Team form",
            paragraphs: [
                "A team's form is built from its finished games this season: points scored per game, points allowed per game, the difference between them, its latest result, and its last five and last ten games. Home, away and neutral-floor games are split out.",
                "Points count for the whole game, overtime included. A second set of figures rescales each game to regulation length, 48 minutes, so a game that went to overtime does not read as an unusually high-scoring one."
            ]
        ),
        MacMethodSection(
            id: "adjusted",
            heading: "Compared with these opponents",
            paragraphs: [
                "Next to the plain averages, two figures compare a team with what its opponents usually do: how many points fewer or more it allows than those opponents usually score, and how many more or fewer it scores than those opponents usually allow. They need five qualifying games. Until then the screen says how many games the team has."
            ]
        ),
        MacMethodSection(
            id: "defence",
            heading: "Defence by position",
            paragraphs: [
                "Points allowed are split by the position of the player who scored them: guards, forwards and centres, as each roster lists a player for the season. The three positions add up to the team's points allowed per game, and the screen shows that check. This counts points by listed position. It does not measure who guarded whom.",
                "A team's figures are marked provisional while it has few games. They are withheld when there are too few games, or too few players placed at a position, for the comparison to mean anything. Better, typical or worse than the league at a position appears only when the numbers support it; otherwise the screen shows a dash, never the word typical."
            ]
        ),
        MacMethodSection(
            id: "projection",
            heading: "Projected scores",
            paragraphs: [
                "Each game gets a projected score for both teams. A team's rating starts from last season's scoring, pulled partway back toward the league average, and then moves after every game by how far the result was from the projection made before it. The projected margin is the difference between the two scores, and a margin smaller than half a point is called a toss-up.",
                "The 80% range shows where a score is expected to fall four times in five, once that range has been calibrated. Until then the screen says the range is assumed or not yet available. Home advantage is the average home margin of past games."
            ]
        ),
        MacMethodSection(
            id: "availability",
            heading: "Who is playing",
            paragraphs: [
                "Absences adjust a projection. A player's expected points and minutes are weighed against that player's chance of playing, taken from the latest report. Part of what a missing player would have scored is recovered by teammates and part is replaced. A player nobody has reported on is assumed available, and the projection says how many players it assumed.",
                "Statuses come from the NBA's official injury report once the app's parser has read a real report. Until then the screen says so, and a status you record yourself is labelled as yours."
            ]
        ),
        MacMethodSection(
            id: "review",
            heading: "Reviewing projections",
            paragraphs: [
                "After a game is played, the review sets the projection that was frozen before tip-off against the final score. Projections rebuilt after the game are counted separately and labelled, because they say what the model would have said, not what it did say."
            ]
        ),
        MacMethodSection(
            id: "limits",
            heading: "What is not here",
            paragraphs: [
                "Hardwood shows projected scores and recorded statistics. It does not compare a projection with any outside number, and it has no betting features. The constants behind the projections, and where each one came from, are on the Constants tab."
            ]
        )
    ]

    /// Said above the EuroLeague's own method text.
    static let euroleagueIntro = "This is the model behind the EuroLeague screens: how it differs from your workbook, what it cannot do, and the constants it uses, each with where it came from. The constants are on the Constants tab."

    /// The one sentence about the workbook's outside-number columns.
    static let outsideNumbersNote = "Workbook columns that compared projections with outside numbers are not reproduced."
}
#endif
