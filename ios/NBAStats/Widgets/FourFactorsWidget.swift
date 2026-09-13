import Foundation
import SwiftUI

// MARK: - Factor direction

/// The rules that make the four factors readable rather than merely drawn.
///
/// Dean Oliver's four factors do **not** all point the same way. Shooting, offensive rebounding
/// and free throw rate are better when high; **turnover rate is better when low**. On the
/// defensive side every direction flips again — forcing turnovers is good, conceding offensive
/// rebounds is not. Coloring a bar by "bigger is greener" would tell the reader the opposite of
/// the truth on two of the eight bars, so direction is decided here, per factor, per side.
public enum FourFactorRules {

    /// The factor a payload key belongs to, whichever spelling the server used.
    public enum Family: String, Hashable, CaseIterable {
        case shooting      // eFG%
        case turnovers     // TOV%
        case rebounding    // OREB%
        case freeThrows    // FTr
        case unknown

        /// The direction on the offensive side, before any defensive flip.
        var offensiveHigherIsBetter: Bool {
            switch self {
            case .shooting:   return true
            case .turnovers:  return false
            case .rebounding: return true
            case .freeThrows: return true
            case .unknown:    return true
            }
        }

        /// The plausible league-wide span, used to place a bar inside a stable track so two
        /// teams' tiles can be compared side by side.
        var baseDomain: ClosedRange<Double> {
            switch self {
            case .shooting:   return 0.44...0.62
            case .turnovers:  return 0.08...0.20
            case .rebounding: return 0.14...0.40
            case .freeThrows: return 0.10...0.36
            case .unknown:    return 0...1
            }
        }
    }

    /// Matches the contract's keys (`efg_pct`, `tov_pct`, `oreb_pct`, `ftr`) and their `opp_`
    /// counterparts, plus the obvious near-spellings, without ever trapping on a new one.
    public static func family(forKey key: String) -> Family {
        let normalized = key.lowercased()
        if normalized.contains("efg") { return .shooting }
        if normalized.contains("tov") || normalized.contains("turnover") { return .turnovers }
        if normalized.contains("oreb") || normalized.contains("off_reb") { return .rebounding }
        if normalized.contains("ftr") || normalized.contains("ft_rate") || normalized.contains("ft_rt") {
            return .freeThrows
        }
        return .unknown
    }

    /// Whether a bigger number is a better number for this factor on this side of the ball.
    ///
    /// A defensive entry is the mirror of its offensive twin: holding opponents to a low eFG% is
    /// good, forcing a high opponent turnover rate is good, conceding offensive rebounds or free
    /// throws is not.
    public static func higherIsBetter(forKey key: String, isDefense: Bool) -> Bool {
        let base = family(forKey: key).offensiveHigherIsBetter
        let normalized = key.lowercased()
        let isOpponentFacing = isDefense || normalized.hasPrefix("opp_") || normalized.contains("opp")
        return isOpponentFacing ? !base : base
    }

    /// The track a factor's bar is drawn inside, widened when the real numbers fall outside it.
    public static func domain(forKey key: String, value: Double?, leagueAverage: Double?) -> ClosedRange<Double> {
        let base = family(forKey: key).baseDomain
        let extras = [value, leagueAverage].compactMap { $0 }.filter { $0.isFinite }
        let low = min(base.lowerBound, (extras.min() ?? base.lowerBound) - 0.01)
        let high = max(base.upperBound, (extras.max() ?? base.upperBound) + 0.01)
        guard high > low else { return base }
        return low...high
    }

    /// The short caption that carries the direction without relying on hue.
    public static func directionHint(higherIsBetter: Bool) -> String {
        higherIsBetter ? "higher is better" : "lower is better"
    }
}

// MARK: - Widget

/// `four_factors` — the four things that decide a basketball game, drawn as horizontal bars
/// against the league average.
///
/// On `.large` the defensive mirror is shown underneath, which is the only place in the app
/// where the same four labels appear twice with opposite directions; each bar carries its own
/// "higher/lower is better" caption so the colors are never the only signal.
public struct FourFactorsWidget: View {

    private struct FactorRow: Identifiable {
        let id: String
        let entry: FourFactorEntry
        let higherIsBetter: Bool
        let domain: ClosedRange<Double>
        let tint: Color
        let isDefense: Bool
    }

    private let payload: FourFactorsPayload
    private let size: WidgetSize

    public init(payload: FourFactorsPayload, size: WidgetSize) {
        self.payload = payload
        self.size = size
    }

    // MARK: Derived

    private var offenseRows: [FactorRow] { Self.rows(from: payload.offense, isDefense: false) }
    private var defenseRows: [FactorRow] { Self.rows(from: payload.defense, isDefense: true) }

    private var showsDefense: Bool {
        switch size {
        case .small, .medium: return false
        case .large: return !defenseRows.isEmpty
        }
    }

    private var barHeight: CGFloat {
        switch size {
        case .small: return 7
        case .medium: return 9
        case .large: return 9
        }
    }

    private var contextText: String {
        Formatting.seasonContext(season: payload.season, seasonType: payload.seasonType)
    }

    private var hasLeagueComparison: Bool {
        (payload.offense + payload.defense).contains { $0.leagueAverage != nil }
    }

    private var footnoteLines: [String] {
        var lines: [String] = []
        if hasLeagueComparison {
            lines.append("The vertical rule on each bar is the league average; the percentage after a factor is Oliver's weight.")
        } else {
            lines.append("The percentage after a factor is Oliver's weight for it.")
        }
        if !showsDefense, !defenseRows.isEmpty {
            lines.append("Resize to large to see the defensive side.")
        }
        return lines
    }

    private func accessibilityText(for row: FactorRow) -> String {
        var parts: [String] = [row.isDefense ? "Defense" : "Offense", row.entry.label, row.entry.displayValue]
        if row.entry.value == nil {
            parts.append("not available for this era")
        }
        if let league = row.entry.leagueAverage {
            let text = HardwoodChart.valueLabel(league, format: .percent1)
            parts.append("league average \(text)")
        }
        if let rank = row.entry.rank, rank > 0 {
            parts.append("ranked \(HardwoodNumberFormat.ordinal(rank))")
        }
        parts.append(FourFactorRules.directionHint(higherIsBetter: row.higherIsBetter))
        return parts.joined(separator: ", ")
    }

    // MARK: Body

    public var body: some View {
        VStack(alignment: .leading, spacing: Spacing.md) {
            teamHeader
            if offenseRows.isEmpty && defenseRows.isEmpty {
                HardwoodChartPlaceholder(icon: "square.grid.2x2",
                                         message: "The four factors need possession data, which the league did not record before 1996-97.",
                                         detail: contextText.isEmpty ? nil : contextText,
                                         availability: .unavailable,
                                         metricName: "Four factors",
                                         season: payload.season)
            } else {
                section(title: "Offense", rows: offenseRows)
                if showsDefense {
                    Divider().overlay(Palette.separator)
                    section(title: "Defense", rows: defenseRows)
                }
                HardwoodChartFootnote(lines: footnoteLines)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var teamHeader: some View {
        HStack(spacing: Spacing.sm) {
            TeamBadge(team: payload.team, size: .small)
            VStack(alignment: .leading, spacing: 0) {
                Text(payload.team.name)
                    .hardwoodText(.statLabel, color: Palette.textPrimary)
                    .lineLimit(1)
                if !contextText.isEmpty {
                    Text(contextText)
                        .hardwoodText(.caption)
                        .lineLimit(1)
                }
            }
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
    }

    private func section(title: String, rows: [FactorRow]) -> some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            HStack(spacing: Spacing.xs) {
                Text(title)
                    .hardwoodText(.statLabel, color: Palette.textSecondary)
                Spacer(minLength: 0)
                if title == "Defense" {
                    Text("opponent's numbers")
                        .hardwoodText(.caption)
                }
            }
            ForEach(rows) { row in
                factorRow(row)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func factorRow(_ row: FactorRow) -> some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            HStack(alignment: .firstTextBaseline, spacing: Spacing.xs) {
                Text(row.entry.label)
                    .hardwoodText(.statLabel, color: Palette.textPrimary)
                    .lineLimit(1)
                if row.entry.weight > 0 {
                    Text(Formatting.percent(row.entry.weight, places: 0))
                        .hardwoodText(.caption)
                }
                if !row.higherIsBetter {
                    Text("lower is better")
                        .hardwoodText(.caption, color: Palette.warning)
                        .lineLimit(1)
                }
                Spacer(minLength: Spacing.xs)
                if size != .small {
                    RankChip(rank: row.entry.rank)
                }
                Text(row.entry.displayValue)
                    .font(Typography.statValueMono)
                    .foregroundStyle(row.entry.value == nil ? Palette.textTertiary : row.tint)
                    .lineLimit(1)
                AvailabilityBadge(availability: row.entry.value == nil ? .unavailable : .full,
                                  showsText: false,
                                  isInteractive: false,
                                  metricName: row.entry.label,
                                  season: payload.season)
            }
            factorTrack(row)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText(for: row))
    }

    /// A factor with no number gets an empty dashed track rather than a bar of length zero: a
    /// zero-length bar reads as "they were terrible at this", which is not what a missing number
    /// means.
    @ViewBuilder private func factorTrack(_ row: FactorRow) -> some View {
        if let value = row.entry.value, value.isFinite {
            FactorBar(value: value,
                      leagueAverage: row.entry.leagueAverage,
                      domain: row.domain,
                      tint: row.tint,
                      label: nil,
                      valueText: nil,
                      showsLegend: false,
                      barHeight: barHeight)
        } else {
            Capsule(style: .continuous)
                .strokeBorder(Palette.separator, style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
                .frame(height: barHeight + 6)
                .accessibilityHidden(true)
        }
    }

    // MARK: Rows

    private static func rows(from entries: [FourFactorEntry], isDefense: Bool) -> [FactorRow] {
        entries.map { entry -> FactorRow in
            let higherIsBetter = FourFactorRules.higherIsBetter(forKey: entry.key, isDefense: isDefense)
            let tint: Color
            if entry.leagueAverage == nil {
                tint = HardwoodChart.seriesColor(at: isDefense ? 2 : 0)
            } else {
                tint = Palette.value(entry.value, comparedTo: entry.leagueAverage, higherIsBetter: higherIsBetter)
            }
            return FactorRow(id: (isDefense ? "d|" : "o|") + entry.key,
                             entry: entry,
                             higherIsBetter: higherIsBetter,
                             domain: FourFactorRules.domain(forKey: entry.key,
                                                            value: entry.value,
                                                            leagueAverage: entry.leagueAverage),
                             tint: tint,
                             isDefense: isDefense)
        }
    }
}

#if DEBUG
#Preview("Four factors") {
    ScrollView {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            Text("Large — offense and the defensive mirror").hardwoodText(.caption)
            FourFactorsWidget(payload: .preview, size: .large)
                .hardwoodCard()
            Text("Medium — offense only").hardwoodText(.caption)
            FourFactorsWidget(payload: .preview, size: .medium)
                .hardwoodCard()
            Text("A team turning it over more than the league does").hardwoodText(.caption)
            FourFactorsWidget(payload: FourFactorsPayload(
                team: .previewOpponent,
                season: "2025-26",
                seasonType: "Regular Season",
                offense: [
                    FourFactorEntry(key: "efg_pct", label: "eFG%", weight: 0.40, value: 0.511,
                                    displayValue: "51.1%", leagueAverage: 0.538, percentile: 0.21, rank: 24),
                    FourFactorEntry(key: "tov_pct", label: "TOV%", weight: 0.25, value: 0.158,
                                    displayValue: "15.8%", leagueAverage: 0.134, percentile: 0.08, rank: 29),
                    FourFactorEntry(key: "oreb_pct", label: "OREB%", weight: 0.20, value: 0.312,
                                    displayValue: "31.2%", leagueAverage: 0.272, percentile: 0.88, rank: 3),
                    FourFactorEntry(key: "ftr", label: "FTr", weight: 0.15, value: nil,
                                    displayValue: Formatting.emDash, leagueAverage: nil, percentile: nil, rank: nil)
                ],
                defense: []), size: .large)
                .hardwoodCard()
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}
#endif
