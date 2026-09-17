import Foundation
import SwiftUI

/// The fantasy draft board: who to take next, and what taking them gives you.
///
/// Every row leads with the pick slot and the player, then the one number that decides the
/// order, then a sentence a reader can disagree with. The nine category z-scores sit underneath
/// as a strip, because the whole point of a category league is that a player is a *shape*
/// rather than a single quantity — two players with the same total value can be opposite picks.
///
/// See `docs/FANTASY.md`. Turnovers are sign-flipped everywhere in this file: a positive z means
/// *few* turnovers, so the strip colours it like any other strength and the wording never calls
/// it a gain in turnovers.
public struct FantasyDraftBoardWidget: View {

    private let payload: FantasyDraftBoardPayload
    private let size: WidgetSize

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    public init(payload: FantasyDraftBoardPayload, size: WidgetSize) {
        self.payload = payload
        self.size = size
    }

    // MARK: Density

    private var rowLimit: Int {
        switch size {
        case .small:  return 3
        case .medium: return 6
        case .large:  return 12
        }
    }

    private var rows: [FantasyDraftPick] {
        Array(payload.picks.prefix(max(rowLimit, 0)))
    }

    /// The nine-column strip needs real width. At accessibility sizes it would be unreadable,
    /// so the row falls back to naming the two categories the pick is strongest in.
    private var showsStrip: Bool { size != .small && !dynamicTypeSize.isAccessibilitySize }

    private var contextLine: String {
        [Formatting.seasonDisplay(payload.season), payload.contextText]
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
    }

    // MARK: Body

    public var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            header
            if rows.isEmpty {
                emptyNote
            } else {
                VStack(alignment: .leading, spacing: Spacing.sm) {
                    ForEach(rows) { pick in
                        row(pick)
                    }
                }
            }
            footer
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            HStack(alignment: .firstTextBaseline, spacing: Spacing.xs) {
                Text("On the clock")
                    .hardwoodText(.widgetTitle)
                Text(payload.nextPick.text)
                    .hardwoodText(.caption, monospacedDigits: true)
                Spacer(minLength: 0)
                if !payload.puntCategories.isEmpty {
                    Text("punting " + payload.puntCategories.map(FantasyCategory.shortLabel).joined(separator: ", "))
                        .hardwoodText(.caption, color: Palette.warning)
                        .lineLimit(1)
                }
            }
            Text(contextLine)
                .hardwoodText(.caption)
                .lineLimit(1)
        }
    }

    // MARK: Row

    private func row(_ pick: FantasyDraftPick) -> some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
                Text(pick.pickText)
                    .hardwoodText(.tableHeader, color: Palette.textTertiary, monospacedDigits: true)
                    .frame(minWidth: 38, alignment: .leading)
                Text(pick.player?.name ?? Formatting.emDash)
                    .hardwoodText(.tableCell)
                    .lineLimit(1)
                    .minimumScaleFactor(0.85)
                Spacer(minLength: Spacing.xs)
                valueColumn(pick)
            }
            if showsStrip {
                categoryStrip(pick)
            } else if let summary = strengthSummary(pick) {
                Text(summary)
                    .hardwoodText(.caption)
                    .lineLimit(1)
            }
            if size == .large, let reason = pick.reason, !reason.isEmpty {
                Text(reason)
                    .hardwoodText(.caption)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityLabel(pick))
    }

    @ViewBuilder private func valueColumn(_ pick: FantasyDraftPick) -> some View {
        if payload.isPointsLeague {
            let value = payload.scoring == "espn_points" ? pick.espnPoints : pick.yahooPoints
            Text(Formatting.decimal(value, places: 1))
                .hardwoodText(.statValue)
        } else {
            HStack(alignment: .firstTextBaseline, spacing: Spacing.xs) {
                Text(Formatting.decimal(pick.totalZ, places: 2, signed: true))
                    .hardwoodText(.statValue)
                if size == .large, let vor = pick.valueOverReplacement, vor.isFinite {
                    Text("VOR " + Formatting.decimal(vor, places: 1, signed: true))
                        .hardwoodText(.caption, monospacedDigits: true)
                }
            }
        }
    }

    /// The nine categories as a row of small signed numbers.
    ///
    /// Colour carries the sign and nothing else — there is no scale here, because a z of +3 and
    /// a z of +0.4 are both "helps" and the number is right there to be read.
    private func categoryStrip(_ pick: FantasyDraftPick) -> some View {
        HStack(spacing: 0) {
            ForEach(payload.categories, id: \.self) { key in
                let value = pick.category(key)
                let punted = payload.puntCategories.contains(key)
                VStack(spacing: 1) {
                    Text(FantasyCategory.shortLabel(key))
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(Palette.textTertiary)
                    Text(value?.zText ?? Formatting.emDash)
                        .font(.system(size: 11, weight: .regular).monospacedDigit())
                        .foregroundStyle(stripColor(value?.z, punted: punted))
                }
                .frame(maxWidth: .infinity)
                .opacity(punted ? 0.35 : 1)
            }
        }
        .accessibilityHidden(true)
    }

    private func stripColor(_ z: Double?, punted: Bool) -> Color {
        guard !punted else { return Palette.textTertiary }
        // Sign only: the z is already printed, and a gradient would imply a precision the
        // number does not have.
        return Palette.value(for: z, higherIsBetter: true)
    }

    /// The two categories a pick is strongest in, for the narrow layouts.
    private func strengthSummary(_ pick: FantasyDraftPick) -> String? {
        let best = pick.categories
            .filter { !payload.puntCategories.contains($0.category) }
            .sorted { ($0.z ?? 0) > ($1.z ?? 0) }
            .prefix(2)
            .filter { ($0.z ?? 0) > 0.5 }
        guard !best.isEmpty else { return nil }
        return best.map { FantasyCategory.label($0.category) }.joined(separator: " · ")
    }

    // MARK: Footer

    @ViewBuilder private var footer: some View {
        if size == .large {
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                if !payload.weakestCategories.isEmpty {
                    Text("Your roster is thinnest at "
                         + payload.weakestCategories.map(FantasyCategory.label).joined(separator: ", ")
                         + ".")
                        .hardwoodText(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let replacement = payload.replacementValue, replacement.isFinite {
                    Text("A freed roster spot refills at "
                         + Formatting.decimal(replacement, places: 2, signed: true)
                         + " total z.")
                        .hardwoodText(.caption, monospacedDigits: true)
                }
            }
        }
    }

    private var emptyNote: some View {
        Text("No players are valued for this season yet.")
            .hardwoodText(.caption)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Accessibility

    /// A sentence, because a nine-column strip of signed numbers is a picture to VoiceOver.
    private func accessibilityLabel(_ pick: FantasyDraftPick) -> String {
        var parts: [String] = [
            pick.player?.name ?? "Unknown player",
            "round \(pick.round) pick \(pick.pickInRound)",
        ]
        if payload.isPointsLeague {
            let value = payload.scoring == "espn_points" ? pick.espnPoints : pick.yahooPoints
            parts.append("\(Formatting.decimal(value, places: 1)) fantasy points per game")
        } else {
            parts.append("total z \(Formatting.decimal(pick.totalZ, places: 2, signed: true))")
        }
        let notable = pick.categories
            .filter { abs($0.z ?? 0) > 1.0 && !payload.puntCategories.contains($0.category) }
            .sorted { abs($0.z ?? 0) > abs($1.z ?? 0) }
            .prefix(3)
        for value in notable {
            parts.append(FantasyCategory.changePhrase(value.category, z: value.z ?? 0))
        }
        if let reason = pick.reason, !reason.isEmpty { parts.append(reason) }
        parts.append("fantasy value, not a record")
        return parts.joined(separator: ", ")
    }
}

#if DEBUG
#Preview("Draft board") {
    ScrollView {
        VStack(spacing: Spacing.md) {
            FantasyDraftBoardWidget(payload: .preview, size: .large)
                .padding(Spacing.md)
                .hardwoodCard()
            FantasyDraftBoardWidget(payload: .preview, size: .medium)
                .padding(Spacing.md)
                .hardwoodCard()
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}
#endif
