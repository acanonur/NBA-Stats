import Foundation
import SwiftUI

/// A player's face — or, when there isn't one, something that looks like it was designed rather
/// than something that failed.
///
/// `PlayerRef.headshotUrl` points at the NBA's public CDN
/// (`.../headshots/nba/latest/1040x760/<personId>.png`). That host is reachable from a device and
/// not from every build or test environment, the asset is missing for a good share of historical
/// players, and even when it is present it arrives over the network at some unknown moment. So
/// this view is written around the assumption that **the photo is the exception, not the rule**:
///
/// * the circle is laid out at its final diameter before any request is made, so a photo landing
///   — or never landing — never moves the row it sits in;
/// * the loading and the failed state draw the *same* monogram on the *same* tinted circle, so a
///   slow CDN and a missing headshot degrade into the identical, deliberate-looking mark, and the
///   only thing that changes when a request fails is that the initials stop being dimmed;
/// * a `nil` URL skips the network entirely and goes straight to the monogram.
///
/// There is no broken-image glyph anywhere in this file, and no spinner: a spinner that never
/// resolves is worse than a monogram that was right all along.
///
/// The colour is derived from the player id with the same fixed fold `TeamBadge` uses for
/// abbreviations, so a given player is the same colour on every launch and on every device.
public struct PlayerAvatar: View {

    // MARK: - Size

    /// The three sizes the app actually has room for.
    ///
    /// The diameters are fixed points, deliberately *not* `@ScaledMetric`. Dynamic Type scales the
    /// initials inside the circle — see `monogramStyle` — but the circle itself must not move, or
    /// a column of ten leaderboard rows stops lining up the moment a reader bumps their text size.
    public enum Size: String, CaseIterable, Sendable {
        /// 24pt — inline in a dense table row, beside the name rather than instead of it.
        case small
        /// 44pt — a full-width list row, where the face is a real part of the row.
        case medium
        /// 88pt — a detail header, where the player is the subject of the whole screen.
        case large

        /// The fixed diameter, in points.
        public var diameter: CGFloat {
            switch self {
            case .small:  return 24
            case .medium: return 44
            case .large:  return 88
            }
        }

        /// The text style the monogram borrows. Each is a Dynamic Type style, so the glyphs grow
        /// with the reader's setting and shrink back to fit via `minimumScaleFactor`.
        var monogramStyle: HardwoodTextStyle {
            switch self {
            case .small:  return .tableHeader
            case .medium: return .statValue
            case .large:  return .displayValue
            }
        }

        /// Keeps a wide pair of glyphs off the rim at the largest accessibility sizes.
        var monogramInset: CGFloat {
            switch self {
            case .small:  return 1
            case .medium: return 4
            case .large:  return 8
            }
        }
    }

    // MARK: - Shared cache

    private static let memoryCapacityBytes = 32 * 1024 * 1024
    private static let diskCapacityBytes = 128 * 1024 * 1024

    /// Sizes the shared URL cache once, the first time any avatar is constructed.
    ///
    /// `AsyncImage` fetches through `URLSession.shared`, which reads `URLCache.shared`. The
    /// capacities are raised **in place** rather than by assigning a fresh `URLCache.shared`,
    /// because `URLSessionConfiguration.default` captures whichever cache object existed when the
    /// configuration was made: `APIClient` may already be holding a session by the time the first
    /// avatar appears, and swapping the object out would strand that session on the old one.
    /// Mutating the live object is order-independent.
    ///
    /// Only ever raised, never lowered, so this cannot shrink a cache the app has deliberately
    /// configured elsewhere. A `static let` runs exactly once and is thread-safe, which is why the
    /// work lives here rather than in `App/` — no file outside this one needs to know.
    private static let sharedCacheIsSized: Void = {
        let cache = URLCache.shared
        if cache.memoryCapacity < PlayerAvatar.memoryCapacityBytes {
            cache.memoryCapacity = PlayerAvatar.memoryCapacityBytes
        }
        if cache.diskCapacity < PlayerAvatar.diskCapacityBytes {
            cache.diskCapacity = PlayerAvatar.diskCapacityBytes
        }
    }()

    // MARK: - Stored

    private let player: PlayerRef
    private let size: Size

    @Environment(\.displayScale) private var displayScale

    public init(player: PlayerRef, size: PlayerAvatar.Size) {
        self.player = player
        self.size = size
        _ = PlayerAvatar.sharedCacheIsSized
    }

    // MARK: - Derived

    /// `nil` for a missing, blank or unparseable URL — all three mean "no network attempt".
    private var headshotURL: URL? {
        guard let raw = player.headshotUrl?.trimmingCharacters(in: .whitespacesAndNewlines),
              !raw.isEmpty else {
            return nil
        }
        return URL(string: raw)
    }

    /// Deterministic from the player id.
    ///
    /// The `"player"` prefix means a player's monogram and the team badge sitting next to it in
    /// the same row are folded from different strings, so they collide on a palette entry only by
    /// chance rather than systematically.
    private var tint: Color {
        Palette.monogramColor(for: "player\(player.playerId)")
    }

    private var initials: String {
        PlayerAvatar.initials(for: player)
    }

    // MARK: - Body

    public var body: some View {
        ZStack {
            Circle()
                .fill(tint.opacity(0.16))
            content
        }
        .frame(width: size.diameter, height: size.diameter)
        .clipShape(Circle())
        .overlay(
            Circle()
                .strokeBorder(Palette.separator, lineWidth: 1 / max(displayScale, 1))
        )
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text(player.name))
    }

    @ViewBuilder private var content: some View {
        if let url = headshotURL {
            AsyncImage(url: url,
                       transaction: Transaction(animation: .easeOut(duration: 0.18))) { phase in
                switch phase {
                case .empty:
                    // Reserves the exact final size. Same circle, same initials, only dimmer, so
                    // the arrival of the photo is a cross-fade and not a reflow.
                    monogram(isSettled: false)
                case .success(let image):
                    image
                        .resizable()
                        .aspectRatio(contentMode: .fill)
                        .frame(width: size.diameter, height: size.diameter)
                        .clipped()
                        .accessibilityHidden(true)
                case .failure:
                    // 404, DNS failure, ATS, timeout — every one of them lands here and looks
                    // identical to a player who simply has no headshot on file.
                    monogram(isSettled: true)
                @unknown default:
                    monogram(isSettled: true)
                }
            }
        } else {
            monogram(isSettled: true)
        }
    }

    /// The fallback mark: the player's initials on their own colour.
    ///
    /// - Parameter isSettled: `false` while a request is still in flight, which only dims the
    ///   glyphs. Nothing about the geometry differs between the two.
    private func monogram(isSettled: Bool) -> some View {
        Text(initials)
            .font(size.monogramStyle.font)
            .fontWeight(.semibold)
            .foregroundStyle(isSettled ? tint : tint.opacity(0.45))
            .lineLimit(1)
            .minimumScaleFactor(0.4)
            .allowsTightening(true)
            .padding(size.monogramInset)
            .frame(width: size.diameter, height: size.diameter)
            .accessibilityHidden(true)
    }

    // MARK: - Initials

    /// One or two letters standing in for a player.
    ///
    /// `firstName` / `lastName` win when the server sent them, because they are already split
    /// correctly for names the space-separated parse gets wrong. Otherwise `name` is parsed.
    ///
    /// `PlayerRef.initials` in `Core` does the `name`-only half of this; it is left alone because
    /// it is the model's own convenience and does not know about the name parts.
    public static func initials(for player: PlayerRef) -> String {
        if let fromParts = initials(first: player.firstName, last: player.lastName) {
            return fromParts
        }
        return initials(fromFullName: player.name)
    }

    /// `nil` unless *both* parts yield a letter, so a record carrying only a first name falls
    /// through to the full-name parse rather than rendering a lone initial.
    private static func initials(first: String?, last: String?) -> String? {
        guard let firstLetter = leadingLetter(of: first),
              let lastLetter = leadingLetter(of: last) else {
            return nil
        }
        return String([firstLetter, lastLetter]).uppercased()
    }

    /// Takes the first *word* and the second, never the last: "Gary Payton II" is `GP`, not `GI`.
    /// A single-word name ("Nenê") gets the one letter, the way Contacts does.
    private static func initials(fromFullName name: String) -> String {
        let letters = name
            .split(whereSeparator: { $0 == " " || $0 == "\u{00A0}" || $0.isNewline || $0 == "\t" })
            .prefix(2)
            .compactMap { leadingLetter(of: String($0)) }
        guard let firstLetter = letters.first else { return "?" }
        guard letters.count > 1 else { return String(firstLetter).uppercased() }
        return String(letters).uppercased()
    }

    /// The first letter or digit, skipping punctuation such as a leading quote in `"J.R."`.
    ///
    /// Grapheme-based throughout: `Character` is an extended grapheme cluster, so `Č` in `Dončić`
    /// is one element whether the server sent it precomposed or decomposed, and `.isLetter` is
    /// true for it. There is no index arithmetic here to get wrong.
    private static func leadingLetter(of text: String?) -> Character? {
        guard let text else { return nil }
        for character in text where character.isLetter || character.isNumber {
            return character
        }
        return nil
    }
}

#if DEBUG
/// A labelled fallback case. A struct rather than a tuple because Swift has no key path to a
/// tuple element, so `ForEach(_:id: \.0)` over `[(String, PlayerRef)]` would not compile.
private struct PlayerAvatarFallbackCase: Identifiable {
    let id: String
    let player: PlayerRef
}

#Preview("Sizes") {
    let player = PlayerRef(playerId: 2544,
                           name: "LeBron James",
                           firstName: "LeBron",
                           lastName: "James",
                           teamId: 1_610_612_747,
                           teamAbbr: "LAL",
                           position: "F",
                           jersey: "23",
                           headshotUrl: "https://cdn.nba.com/headshots/nba/latest/1040x760/2544.png",
                           isActive: true)
    return VStack(alignment: .leading, spacing: Spacing.lg) {
        Text("Live CDN URL — a photo here, a monogram wherever the network cannot reach it.")
            .hardwoodText(.caption)
            .fixedSize(horizontal: false, vertical: true)
        HStack(alignment: .bottom, spacing: Spacing.lg) {
            ForEach(PlayerAvatar.Size.allCases, id: \.self) { size in
                VStack(spacing: Spacing.xs) {
                    PlayerAvatar(player: player, size: size)
                    Text("\(Int(size.diameter))pt").hardwoodText(.caption)
                }
            }
        }
    }
    .padding(Spacing.lg)
    .frame(maxWidth: .infinity, alignment: .leading)
    .hardwoodCard()
    .padding(Spacing.lg)
    .hardwoodBackground()
}

#Preview("Fallbacks") {
    let cases: [PlayerAvatarFallbackCase] = [
        PlayerAvatarFallbackCase(
            id: "No URL at all — no request is made",
            player: PlayerRef(playerId: 1_629_029, name: "Luka Dončić",
                              firstName: "Luka", lastName: "Dončić",
                              teamAbbr: "LAL", headshotUrl: nil)),
        PlayerAvatarFallbackCase(
            id: "URL that 404s — settles to the same mark",
            player: PlayerRef(playerId: 203_999, name: "Nikola Jokić",
                              firstName: "Nikola", lastName: "Jokić",
                              teamAbbr: "DEN",
                              headshotUrl: "https://cdn.nba.com/headshots/nba/latest/1040x760/0.png")),
        PlayerAvatarFallbackCase(
            id: "Single-word name",
            player: PlayerRef(playerId: 2403, name: "Nenê", headshotUrl: nil)),
        PlayerAvatarFallbackCase(
            id: "Name parts only, empty name",
            player: PlayerRef(playerId: 76375, name: "", firstName: "Elgin",
                              lastName: "Baylor", headshotUrl: nil)),
        PlayerAvatarFallbackCase(
            id: "Nothing to go on",
            player: PlayerRef(playerId: 0, name: "", headshotUrl: nil))
    ]
    return VStack(alignment: .leading, spacing: Spacing.md) {
        ForEach(cases) { entry in
            HStack(spacing: Spacing.md) {
                PlayerAvatar(player: entry.player, size: .medium)
                VStack(alignment: .leading, spacing: 1) {
                    Text(entry.id).hardwoodText(.widgetTitle)
                    Text(entry.player.name.isEmpty ? Formatting.emDash : entry.player.name)
                        .hardwoodText(.caption)
                }
                Spacer(minLength: 0)
            }
        }
    }
    .padding(Spacing.lg)
    .frame(maxWidth: .infinity, alignment: .leading)
    .hardwoodCard()
    .padding(Spacing.lg)
    .hardwoodBackground()
}

#Preview("Dark, and a row of ten") {
    let players: [PlayerRef] = (0..<10).map { index in
        PlayerRef(playerId: 1000 + index,
                  name: "Player \(index) Example",
                  firstName: "Player",
                  lastName: "Example\(index)",
                  headshotUrl: nil)
    }
    return VStack(alignment: .leading, spacing: Spacing.xs) {
        Text("Ten monograms, ten colours, one baseline").hardwoodText(.caption)
        ForEach(players) { player in
            HStack(spacing: Spacing.sm) {
                PlayerAvatar(player: player, size: .small)
                Text(player.name).hardwoodText(.tableCell)
                Spacer(minLength: 0)
            }
        }
    }
    .padding(Spacing.lg)
    .frame(maxWidth: .infinity, alignment: .leading)
    .hardwoodCard()
    .padding(Spacing.lg)
    .hardwoodBackground()
    .preferredColorScheme(.dark)
}
#endif
