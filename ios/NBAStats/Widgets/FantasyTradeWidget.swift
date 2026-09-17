import Foundation
import SwiftUI

/// The trade analyzer: what you gain, what you lose, and how much of that survives being wrong.
///
/// Three things here are load-bearing and easy to get wrong in a redesign.
///
/// **The roster-spot adjustment is shown as its own line.** Give two and get one and the freed
/// slot refills from waivers below pool average — routinely a bigger number than the difference
/// between the players. A reader shown only the net cannot tell a bad trade from slot arithmetic.
///
/// **The scenario range is never called an interval.** There is no probability in it. It is the
/// same trade recomputed under four named assumptions, and when those disagree about the sign
/// the widget leads with that rather than with the midpoint. `docs/FANTASY.md` §2d sets out why
/// a predictive interval is not on offer.
///
/// **Turnovers read in the direction they are scored.** A positive net there is an improvement,
/// so the wording says "better on turnovers", never "gains turnovers".
public struct FantasyTradeWidget: View {

    private let payload: FantasyTradePayload
    private let size: WidgetSize

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    public init(payload: FantasyTradePayload, size: WidgetSize) {
        self.payload = payload
        self.size = size
    }

    private var showsTable: Bool { size == .large && !dynamicTypeSize.isAccessibilitySize }

    // MARK: Body

    public var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            if payload.isEmpty {
                emptyState
            } else {
                verdictBlock
                sides
                if showsTable {
                    categoryTable
                } else {
                    categorySummary
                }
                sensitivityBlock
                rosterNote
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Verdict

    private var verdictBlock: some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
                Text(payload.verdict.capitalized)
                    .hardwoodText(.widgetTitle, color: verdictColor)
                Text(Formatting.decimal(payload.netZ, places: 2, signed: true))
                    .hardwoodText(.statValue, color: verdictColor)
                Text("net z")
                    .hardwoodText(.caption)
                Spacer(minLength: 0)
            }
            if let spread = payload.poolSpread, spread.isFinite, spread > 0 {
                // The band without the scale it lives on is a number pretending to be a
                // judgement, so the two are always shown together.
                Text("Fair inside ±\(Formatting.decimal(payload.bands.fair, places: 2)) · "
                     + "league spread \(Formatting.decimal(spread, places: 2))")
                    .hardwoodText(.caption, monospacedDigits: true)
                    .lineLimit(1)
            }
        }
    }

    private var verdictColor: Color {
        guard let net = payload.netZ else { return Palette.neutral }
        if abs(net) < payload.bands.fair { return Palette.neutral }
        return net > 0 ? Palette.positive : Palette.negative
    }

    // MARK: Sides

    private var sides: some View {
        HStack(alignment: .top, spacing: Spacing.md) {
            sideColumn("You give", payload.give)
            Divider()
            sideColumn("You get", payload.get)
        }
    }

    private func sideColumn(_ title: String, _ side: FantasyTradeSide) -> some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            Text(title)
                .hardwoodText(.tableHeader)
            ForEach(side.players) { entry in
                HStack(alignment: .firstTextBaseline, spacing: Spacing.xs) {
                    Text(entry.player?.shortName ?? Formatting.emDash)
                        .hardwoodText(.tableCell)
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                    Spacer(minLength: Spacing.xs)
                    Text(Formatting.decimal(entry.totalZ, places: 1, signed: true))
                        .hardwoodText(.caption, monospacedDigits: true)
                }
            }
            if side.players.isEmpty {
                Text("—").hardwoodText(.caption)
            }
            Text("total " + Formatting.decimal(side.totalZ, places: 2, signed: true))
                .hardwoodText(.caption, monospacedDigits: true)
                .padding(.top, 1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    // MARK: Categories

    private var categoryTable: some View {
        VStack(spacing: 0) {
            HStack(spacing: 0) {
                ForEach(payload.categories) { entry in
                    VStack(spacing: 1) {
                        Text(entry.label)
                            .font(.system(size: 9, weight: .semibold))
                            .foregroundStyle(Palette.textTertiary)
                        Text(Formatting.decimal(entry.net, places: 2, signed: true))
                            .font(.system(size: 11, weight: .regular).monospacedDigit())
                            .foregroundStyle(color(for: entry))
                    }
                    .frame(maxWidth: .infinity)
                    .opacity(entry.punted ? 0.35 : 1)
                }
            }
            .padding(.vertical, Spacing.xs)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(categoryAccessibility)
    }

    /// The narrow fallback: name what moves rather than tabulating all nine.
    private var categorySummary: some View {
        let sorted = payload.categories
            .filter { !$0.punted }
            .sorted { abs($0.net ?? 0) > abs($1.net ?? 0) }
        let gains = sorted.filter { ($0.net ?? 0) > 0.25 }.prefix(2)
        let losses = sorted.filter { ($0.net ?? 0) < -0.25 }.prefix(2)
        return VStack(alignment: .leading, spacing: Spacing.xxs) {
            if !gains.isEmpty {
                Text("Gains " + gains.map { FantasyCategory.label($0.category) }.joined(separator: ", "))
                    .hardwoodText(.caption, color: Palette.positive)
            }
            if !losses.isEmpty {
                Text("Costs " + losses.map { FantasyCategory.label($0.category) }.joined(separator: ", "))
                    .hardwoodText(.caption, color: Palette.negative)
            }
            if gains.isEmpty && losses.isEmpty {
                Text("No category moves by much.").hardwoodText(.caption)
            }
        }
    }

    private func color(for entry: FantasyTradeCategory) -> Color {
        guard !entry.punted else { return Palette.textTertiary }
        // `net` is already direction-corrected by the server — turnovers are sign-flipped
        // upstream — so positive is good for every one of the nine.
        return Palette.value(for: entry.net, higherIsBetter: true)
    }

    private var categoryAccessibility: String {
        let parts = payload.categories
            .filter { !$0.punted && abs($0.net ?? 0) > 0.25 }
            .map { $0.phrase }
        return parts.isEmpty ? "No category moves by much." : parts.joined(separator: ", ")
    }

    // MARK: Sensitivity

    @ViewBuilder private var sensitivityBlock: some View {
        if let sweep = payload.sensitivity {
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                HStack(alignment: .firstTextBaseline, spacing: Spacing.xs) {
                    Text(sweep.flips ? "Depends on who holds up" : "Holds up across scenarios")
                        .hardwoodText(.statLabel,
                                      color: sweep.flips ? Palette.warning : Palette.textSecondary)
                    Text(sweep.rangeText)
                        .hardwoodText(.caption, monospacedDigits: true)
                    Spacer(minLength: 0)
                }
                if sweep.flips {
                    Text("It wins under some assumptions and loses under others, so the midpoint is not the answer.")
                        .hardwoodText(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if size == .large, let low = sweep.lowScenario, let high = sweep.highScenario {
                    Text("Worst: \(low). Best: \(high).")
                        .hardwoodText(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }
                // Said plainly, every time. A range that gets read as a confidence interval is
                // worse than no range, because it claims a probability nothing here computed.
                Text("A sweep over four named assumptions, not a probability.")
                    .hardwoodText(.caption, color: Palette.textTertiary)
            }
            .padding(.top, Spacing.xxs)
        }
    }

    @ViewBuilder private var rosterNote: some View {
        if let adjustment = payload.rosterAdjustment, abs(adjustment) > 0.001 {
            Text("Includes \(Formatting.decimal(adjustment, places: 2, signed: true)) for the roster "
                 + "\(abs(payload.give.count - payload.get.count) == 1 ? "spot" : "spots") this moves"
                 + (payload.rosterDominates ? " — more than the players themselves." : "."))
                .hardwoodText(.caption, monospacedDigits: true)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            Text("Add players to both sides")
                .hardwoodText(.widgetTitle)
            Text("Pick up to four each way. You will get the change per category, what the roster spots are worth, and how much of it survives a player losing his role.")
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

#if DEBUG
#Preview("Trade analyzer") {
    ScrollView {
        VStack(spacing: Spacing.md) {
            FantasyTradeWidget(payload: .preview, size: .large)
                .padding(Spacing.md)
                .hardwoodCard()
            FantasyTradeWidget(payload: FantasyTradePayload(), size: .large)
                .padding(Spacing.md)
                .hardwoodCard()
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}
#endif
