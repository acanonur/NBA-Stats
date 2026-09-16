import Foundation
import SwiftUI
import XCTest
@testable import Hardwood

/// `PlayerAvatar` is written around the assumption that the photo is the exception: the NBA's
/// headshot CDN is unreachable from most build and test environments and has no asset at all for
/// a good share of historical players. What is left when the photo never arrives is the monogram,
/// and the monogram has to be *right* — the correct letters, in the correct order, in a colour
/// that is the same on every launch and every device.
///
/// None of that needs a rendered view, which is why it is all static and testable.
@MainActor
final class PlayerAvatarTests: XCTestCase {

    private func player(_ name: String,
                        first: String? = nil,
                        last: String? = nil,
                        id: PlayerID = 1) -> PlayerRef {
        PlayerRef(playerId: id, name: name, firstName: first, lastName: last)
    }

    // MARK: - Initials from the name parts

    /// The parts win when the server sent them: they are already split correctly for the names a
    /// space-separated parse gets wrong.
    func testInitialsComeFromTheNameParts() {
        XCTAssertEqual(PlayerAvatar.initials(for: player("LeBron James", first: "LeBron", last: "James")), "LJ")
        XCTAssertEqual(PlayerAvatar.initials(for: player("Shai Gilgeous-Alexander",
                                                         first: "Shai",
                                                         last: "Gilgeous-Alexander")), "SG")
        // The record is nothing but parts: an empty `name` is not a reason to show a question mark.
        XCTAssertEqual(PlayerAvatar.initials(for: player("", first: "Elgin", last: "Baylor")), "EB")
    }

    /// One part is not two. A record carrying only a first name falls through to the full-name
    /// parse rather than rendering a lone, misleading initial from the wrong field.
    func testASingleNamePartFallsThroughToTheFullName() {
        XCTAssertEqual(PlayerAvatar.initials(for: player("Gary Payton II", first: "Gary", last: nil)), "GP")
        XCTAssertEqual(PlayerAvatar.initials(for: player("Gary Payton II", first: nil, last: "Payton")), "GP")
        XCTAssertEqual(PlayerAvatar.initials(for: player("Nenê", first: "Nenê", last: nil)), "N")
    }

    // MARK: - Initials from a full name

    func testASingleWordNameGetsOneLetter() {
        XCTAssertEqual(PlayerAvatar.initials(for: player("Nenê")), "N")
        XCTAssertEqual(PlayerAvatar.initials(for: player("Pelé")), "P")
    }

    /// The first two *words*, never the last: "Gary Payton II" is GP, not GI.
    func testTheSecondWordIsTakenNotTheLast() {
        XCTAssertEqual(PlayerAvatar.initials(for: player("Gary Payton II")), "GP")
        XCTAssertEqual(PlayerAvatar.initials(for: player("Karl Anthony Towns")), "KA")
    }

    /// Punctuation is skipped rather than rendered: "J.R. Smith" is JS, and a name in quotes is
    /// still the person's name.
    func testLeadingPunctuationIsSkipped() {
        XCTAssertEqual(PlayerAvatar.initials(for: player("J.R. Smith")), "JS")
        XCTAssertEqual(PlayerAvatar.initials(for: player("\"Pistol\" Pete Maravich")), "PP")
    }

    func testTheInitialsAreAlwaysUppercased() {
        XCTAssertEqual(PlayerAvatar.initials(for: player("luka doncic")), "LD")
        XCTAssertEqual(PlayerAvatar.initials(for: player("de'aaron fox", first: "de'aaron", last: "fox")), "DF")
    }

    // MARK: - Diacritics

    /// `Character` is an extended grapheme cluster, so an accented letter is one element whether
    /// the server sent it precomposed or decomposed, and there is no index arithmetic to get wrong.
    func testANameWithDiacriticsKeepsItsAccents() {
        XCTAssertEqual(PlayerAvatar.initials(for: player("Luka Dončić")), "LD")
        XCTAssertEqual(PlayerAvatar.initials(for: player("Nikola Jokić", first: "Nikola", last: "Jokić")), "NJ")
        XCTAssertEqual(PlayerAvatar.initials(for: player("Álex Abrines")), "ÁA")
        XCTAssertEqual(PlayerAvatar.initials(for: player("Ömer Aşık", first: "Ömer", last: "Aşık")), "ÖA")
    }

    /// The same name written decomposed (`A` + combining acute) gives the same monogram as the
    /// precomposed spelling — which is what a grapheme-based parse buys.
    func testADecomposedNameGivesTheSameInitialsAsAPrecomposedOne() {
        let precomposed = PlayerAvatar.initials(for: player("Álex Abrines"))
        let decomposed = PlayerAvatar.initials(for: player("A\u{0301}lex Abrines"))
        XCTAssertEqual(decomposed, precomposed)

        let fromParts = PlayerAvatar.initials(for: player("", first: "A\u{0301}lex", last: "Abrines"))
        XCTAssertEqual(fromParts, precomposed)
    }

    // MARK: - Nothing to go on

    func testAnEmptyNameGetsAQuestionMarkRatherThanAnEmptyCircle() {
        XCTAssertEqual(PlayerAvatar.initials(for: player("")), "?")
        XCTAssertEqual(PlayerAvatar.initials(for: player("   ")), "?")
        XCTAssertEqual(PlayerAvatar.initials(for: player("\u{00A0}\t")), "?")
        // Nothing but punctuation is nothing to go on either.
        XCTAssertEqual(PlayerAvatar.initials(for: player("--")), "?")
    }

    func testEveryMonogramIsOneOrTwoCharacters() {
        let names = ["LeBron James", "Nenê", "", "   ", "Gary Payton II", "J.R. Smith",
                     "Luka Dončić", "Álex Abrines", "Karl Anthony Towns", "X"]
        for name in names {
            let initials = PlayerAvatar.initials(for: player(name))
            XCTAssertFalse(initials.isEmpty, "\"\(name)\" produced no monogram at all")
            XCTAssertLessThanOrEqual(initials.count, 2, "\"\(name)\" produced \"\(initials)\", which will not fit")
        }
    }

    /// `PlayerRef.initials` is the model's own convenience and knows nothing about the name parts;
    /// the avatar's version is the one that does. Both must survive the same input.
    func testTheModelsOwnInitialsStillWorkForTheNameOnlyCase() {
        XCTAssertEqual(player("LeBron James").initials, "LJ")
        XCTAssertEqual(player("").initials, "?")
    }

    // MARK: - The monogram colour

    /// The documented fold, reimplemented here so the test is checking the *rule* rather than
    /// echoing the implementation: Swift's own `hashValue` is seeded per process and would give a
    /// player a different colour on every launch.
    private static func fnv1aIndex(_ text: String, modulo count: Int) -> Int {
        var hash: UInt32 = 2_166_136_261
        for scalar in text.uppercased().unicodeScalars {
            hash = (hash ^ (scalar.value & 0xFF)) &* 16_777_619
        }
        return Int(hash % UInt32(count))
    }

    /// The avatar folds `"player<id>"`, so a player's monogram and the team badge beside it in the
    /// same row come from different strings.
    private func monogramKey(for id: PlayerID) -> String { "player\(id)" }

    func testTheMonogramColourIsDeterministicForAPlayerID() {
        let key = monogramKey(for: 2544)
        let first = Palette.monogramColor(for: key)
        for _ in 0..<50 {
            XCTAssertEqual(Palette.monogramColor(for: key), first,
                           "The same player got two different colours in one process")
        }
        XCTAssertTrue(Palette.monogramPalette.contains(first), "The colour is not from the monogram palette")
    }

    func testTheMonogramColourFollowsTheDocumentedFoldRatherThanAProcessSeededHash() {
        XCTAssertFalse(Palette.monogramPalette.isEmpty)
        for id in [0, 1, 2544, 203_999, 1_629_029, 1_642_300] {
            let key = monogramKey(for: id)
            let expected = PlayerAvatarTests.fnv1aIndex(key, modulo: Palette.monogramPalette.count)
            XCTAssertEqual(Palette.monogramColor(for: key), Palette.monogramPalette[expected],
                           "\(key) did not land on the palette entry the FNV-1a fold names")
        }
    }

    func testTheFoldIsCaseInsensitive() {
        XCTAssertEqual(Palette.monogramColor(for: "player2544"), Palette.monogramColor(for: "PLAYER2544"))
        XCTAssertEqual(Palette.monogramColor(for: "lal"), Palette.monogramColor(for: "LAL"))
    }

    /// Ten players in a row should not all be the same colour; that would make the whole device
    /// look like one failed request.
    func testAColumnOfPlayersDoesNotCollapseToOneColour() {
        let colors = (0..<10).map { Palette.monogramColor(for: monogramKey(for: 1000 + $0)) }
        XCTAssertGreaterThan(Set(colors).count, 1, "Ten consecutive player ids all folded to one colour")
    }

    // MARK: - Geometry

    /// The diameters are fixed points on purpose: Dynamic Type scales the glyphs inside the
    /// circle, never the circle, or a column of leaderboard rows stops lining up.
    func testTheThreeSizesKeepTheirFixedDiameters() {
        XCTAssertEqual(PlayerAvatar.Size.allCases.count, 3)
        XCTAssertEqual(PlayerAvatar.Size.small.diameter, 24)
        XCTAssertEqual(PlayerAvatar.Size.medium.diameter, 44)
        XCTAssertEqual(PlayerAvatar.Size.large.diameter, 88)
        // Strictly increasing, so a caller can pick by "bigger" without consulting the table.
        let diameters = PlayerAvatar.Size.allCases.map { $0.diameter }
        XCTAssertEqual(diameters, diameters.sorted())
        XCTAssertEqual(Set(diameters).count, diameters.count)
    }
}
