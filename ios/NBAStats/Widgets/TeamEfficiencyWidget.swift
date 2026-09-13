import Charts
import Foundation
import SwiftUI

/// `team_efficiency` — the league table, as either a ranked table or a scatter.
///
/// The scatter is the reason this widget exists: offensive rating on x, defensive rating on y
/// **inverted**, so the familiar reading holds — up and to the right is a good team. The
/// inversion is stated on the axis itself, not left for the reader to infer, and the league
/// average crosshairs split the plot into the four quadrants people actually talk about.
public struct TeamEfficiencyWidget: View {

    // MARK: Types

    private struct Column: Identifiable {
        let id: String
        let title: String
        let format: MetricFormat
        let higherIsBetter: Bool
        let isDirectional: Bool
    }

    private struct ScatterPoint: Identifiable {
        let id: TeamID
        let abbr: String
        let name: String
        let offense: Double
        /// The plotted y: the defensive rating negated, so a stingy defense sits high.
        let plottedDefense: Double
        let color: Color
    }

    private let payload: TeamEfficiencyPayload
    private let size: WidgetSize

    public init(payload: TeamEfficiencyPayload, size: WidgetSize) {
        self.payload = payload
        self.size = size
    }

    // MARK: Keys

    private static let offenseKey = "off_rtg"
    private static let defenseKey = "def_rtg"
    private static let netKey = "net_rtg"
    private static let paceKey = "pace"

    // MARK: Derived

    private var usesScatter: Bool {
        guard size != .small else { return false }
        return payload.style.lowercased() == "scatter"
    }

    private var rowLimit: Int {
        switch size {
        case .small: return 4
        case .medium: return 6
        case .large: return 10
        }
    }

    private var rows: [TeamEfficiencyRow] { Array(payload.rows.prefix(rowLimit)) }

    private var showsRecord: Bool {
        switch size {
        case .small, .medium: return false
        case .large: return true
        }
    }

    private var columns: [Column] {
        let offense = Column(id: Self.offenseKey, title: "ORtg", format: .rating1, higherIsBetter: true, isDirectional: true)
        let defense = Column(id: Self.defenseKey, title: "DRtg", format: .rating1, higherIsBetter: false, isDirectional: true)
        let net = Column(id: Self.netKey, title: "NetRtg", format: .plusMinus1, higherIsBetter: true, isDirectional: true)
        let pace = Column(id: Self.paceKey, title: "Pace", format: .decimal1, higherIsBetter: true, isDirectional: false)
        switch size {
        case .small: return [net]
        case .medium: return [net, offense, defense]
        case .large: return [offense, defense, net, pace]
        }
    }

    private var contextText: String {
        Formatting.seasonContext(season: payload.season, seasonType: payload.seasonType)
    }

    private var scatterPoints: [ScatterPoint] {
        payload.rows.compactMap { row -> ScatterPoint? in
            guard let offense = row.value(Self.offenseKey), offense.isFinite,
                  let defense = row.value(Self.defenseKey), defense.isFinite else { return nil }
            return ScatterPoint(id: row.team.teamId,
                                abbr: row.team.abbr,
                                name: row.team.name,
                                offense: offense,
                                plottedDefense: -defense,
                                color: Palette.monogramColor(for: row.team.abbr))
        }
    }

    private var scatterHeight: CGFloat {
        switch size {
        case .small: return 140
        case .medium: return 190
        case .large: return 250
        }
    }

    private var leagueOffense: Double? { payload.leagueAverageValue(Self.offenseKey) }
    private var leagueDefense: Double? { payload.leagueAverageValue(Self.defenseKey) }

    private var offenseDomain: ClosedRange<Double> {
        var values = scatterPoints.map(\.offense)
        if let leagueOffense { values.append(leagueOffense) }
        return HardwoodChart.paddedDomain(for: values, fallback: 100...125, padding: 0.12)
    }

    private var defenseDomain: ClosedRange<Double> {
        var values = scatterPoints.map(\.plottedDefense)
        if let leagueDefense { values.append(-leagueDefense) }
        return HardwoodChart.paddedDomain(for: values, fallback: (-125)...(-100), padding: 0.12)
    }

    private var accessibilityText: String {
        let leaders = rows.prefix(3).map { row in
            "\(row.team.abbr) \(HardwoodChart.valueLabel(row.value(Self.netKey), format: .plusMinus1))"
        }
        return "Team efficiency, sorted by \(payload.sortBy). Leaders: " + leaders.joined(separator: ", ")
    }

    // MARK: Body

    public var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            header
            if payload.rows.isEmpty {
                HardwoodChartPlaceholder(icon: "tablecells",
                                         message: "No team ratings for this season yet.",
                                         detail: "Offensive and defensive ratings need measured possessions, which start in 1996-97.",
                                         availability: .unavailable,
                                         metricName: "Team efficiency",
                                         season: payload.season)
            } else if usesScatter {
                scatter
            } else {
                table
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.xs) {
            Text(usesScatter ? "Offense vs defense" : "League table")
                .hardwoodText(.statLabel, color: Palette.textPrimary)
                .lineLimit(1)
            Spacer(minLength: Spacing.xs)
            if !contextText.isEmpty {
                Text(contextText)
                    .hardwoodText(.caption)
                    .lineLimit(1)
            }
        }
        .accessibilityElement(children: .combine)
    }

    // MARK: Table

    private var table: some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            tableHeader
            Divider().overlay(Palette.separator)
            ForEach(rows) { row in
                tableRow(row)
            }
            if payload.rows.count > rows.count {
                Text("\(payload.rows.count - rows.count) more team\(payload.rows.count - rows.count == 1 ? "" : "s") at a larger size.")
                    .hardwoodText(.caption)
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(accessibilityText)
    }

    private var tableHeader: some View {
        HStack(spacing: Spacing.xs) {
            Text("#")
                .hardwoodText(.tableHeader)
                .frame(width: 18, alignment: .leading)
            Text("Team")
                .hardwoodText(.tableHeader)
                .frame(width: 46, alignment: .leading)
            if showsRecord {
                Text("W-L")
                    .hardwoodText(.tableHeader)
                    .frame(width: 44, alignment: .trailing)
            }
            ForEach(columns) { column in
                Text(column.title)
                    .hardwoodText(.tableHeader,
                                  color: column.id == payload.sortBy ? Palette.selection : Palette.textSecondary)
                    .lineLimit(1)
                    .frame(maxWidth: .infinity, alignment: .trailing)
            }
        }
        .accessibilityHidden(true)
    }

    private func tableRow(_ row: TeamEfficiencyRow) -> some View {
        HStack(spacing: Spacing.xs) {
            Text(row.rank > 0 ? "\(row.rank)" : HardwoodNumberFormat.missing)
                .font(Typography.tableCellMono)
                .foregroundStyle(Palette.textTertiary)
                .frame(width: 18, alignment: .leading)
            TeamBadge(team: row.team, size: .small)
                .frame(width: 46, alignment: .leading)
            if showsRecord {
                Text(row.recordText)
                    .font(Typography.tableCellMono)
                    .foregroundStyle(Palette.textSecondary)
                    .frame(width: 44, alignment: .trailing)
            }
            ForEach(columns) { column in
                valueCell(row: row, column: column)
            }
        }
        .padding(.vertical, 1)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(rowAccessibilityText(row))
    }

    private func valueCell(row: TeamEfficiencyRow, column: Column) -> some View {
        let value = row.value(column.id)
        let league = payload.leagueAverageValue(column.id)
        let tint: Color = column.isDirectional
            ? Palette.value(value, comparedTo: league, higherIsBetter: column.higherIsBetter)
            : Palette.textPrimary
        return HStack(spacing: 2) {
            Spacer(minLength: 0)
            Text(HardwoodChart.valueLabel(value, format: column.format))
                .font(Typography.tableCellMono)
                .foregroundStyle(value == nil ? Palette.textTertiary : tint)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
            if let rank = row.rankValue(column.id), rank > 0 {
                Text("\(rank)")
                    .font(Typography.tableHeaderMono)
                    .foregroundStyle(Palette.textTertiary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .trailing)
    }

    private func rowAccessibilityText(_ row: TeamEfficiencyRow) -> String {
        var parts: [String] = ["\(row.rank). \(row.team.name)"]
        if showsRecord { parts.append(row.recordText) }
        for column in columns {
            let text = HardwoodChart.valueLabel(row.value(column.id), format: column.format)
            if let rank = row.rankValue(column.id), rank > 0 {
                parts.append("\(column.title) \(text), \(HardwoodNumberFormat.ordinal(rank)) in the league")
            } else {
                parts.append("\(column.title) \(text)")
            }
        }
        return parts.joined(separator: ", ")
    }

    // MARK: Scatter

    private var scatter: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            Text("Better defense ↑  ·  Defensive rating is inverted, so up and right is a good team")
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
            if scatterPoints.isEmpty {
                HardwoodChartPlaceholder(icon: "chart.dots.scatter",
                                         message: "No team has both an offensive and a defensive rating for this season.",
                                         availability: .unavailable,
                                         metricName: "Team ratings",
                                         season: payload.season)
            } else {
                scatterChart
                HardwoodChartFootnote(lines: scatterFootnotes)
            }
        }
    }

    private var scatterFootnotes: [String] {
        var lines: [String] = ["Offensive rating →  ·  points scored per 100 possessions."]
        if let leagueOffense, let leagueDefense {
            lines.append("Dashed crosshairs: league average \(HardwoodChart.valueLabel(leagueOffense, format: .rating1)) offense, \(HardwoodChart.valueLabel(leagueDefense, format: .rating1)) defense.")
        }
        return lines
    }

    private var scatterChart: some View {
        Chart {
            if let leagueOffense, leagueOffense.isFinite {
                RuleMark(x: .value("League offense", leagueOffense))
                    .foregroundStyle(Palette.textTertiary)
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: HardwoodChart.ruleDash))
            }
            if let leagueDefense, leagueDefense.isFinite {
                RuleMark(y: .value("League defense", -leagueDefense))
                    .foregroundStyle(Palette.textTertiary)
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: HardwoodChart.ruleDash))
            }
            ForEach(scatterPoints) { point in
                PointMark(x: .value("Offensive rating", point.offense),
                          y: .value("Defensive rating (inverted)", point.plottedDefense))
                    .foregroundStyle(point.color)
                    .symbolSize(size == .large ? 44 : 30)
                    .annotation(position: .top, alignment: .center, spacing: 0) {
                        Text(point.abbr)
                            .font(Typography.tableHeaderMono)
                            .foregroundStyle(point.color)
                            .lineLimit(1)
                    }
            }
        }
        .chartXScale(domain: offenseDomain)
        .chartYScale(domain: defenseDomain)
        .chartXAxis { HardwoodChartAxis.metricValues(format: .rating1, position: .bottom, desiredCount: 4) }
        .chartYAxis { invertedDefenseAxis }
        .hardwoodChartStyle()
        .frame(height: scatterHeight)
        .accessibilityLabel(scatterAccessibilityText)
    }

    /// The y axis reads in real defensive ratings even though the plotted number is negated.
    @AxisContentBuilder private var invertedDefenseAxis: some AxisContent {
        AxisMarks(position: .leading, values: .automatic(desiredCount: 4)) { mark in
            AxisGridLine()
                .foregroundStyle(Palette.separator.opacity(HardwoodChart.gridOpacity))
            AxisTick()
                .foregroundStyle(Palette.separator)
            AxisValueLabel {
                Text(HardwoodChart.axisLabel(mark.as(Double.self).map { -$0 }, format: .rating1))
                    .hardwoodText(.tableHeader, color: Palette.textTertiary)
            }
        }
    }

    private var scatterAccessibilityText: String {
        let best = scatterPoints.max { first, second in
            (first.offense + first.plottedDefense) < (second.offense + second.plottedDefense)
        }
        var text = "Scatter of \(scatterPoints.count) teams, offensive rating on the horizontal axis and defensive rating inverted on the vertical axis so better defenses are higher"
        if let best {
            text += ". Best net position: \(best.name)"
        }
        return text
    }
}

#if DEBUG
private enum TeamEfficiencyPreviewData {
    static let scatter = TeamEfficiencyPayload(
        season: "2025-26",
        seasonType: "Regular Season",
        sortBy: "net_rtg",
        style: "scatter",
        leagueAverage: ["off_rtg": .double(114.1), "def_rtg": .double(114.1), "pace": .double(98.6)],
        rows: [
            TeamEfficiencyRow(rank: 1, team: .preview, wins: 27, losses: 14,
                              values: ["off_rtg": .double(118.2), "def_rtg": .double(110.4), "net_rtg": .double(7.8), "pace": .double(99.4)],
                              ranks: ["off_rtg": .int(3), "def_rtg": .int(5), "net_rtg": .int(1)]),
            TeamEfficiencyRow(rank: 2, team: .previewOpponent, wins: 26, losses: 15,
                              values: ["off_rtg": .double(119.6), "def_rtg": .double(112.5), "net_rtg": .double(7.1), "pace": .double(97.2)],
                              ranks: ["off_rtg": .int(1), "def_rtg": .int(9), "net_rtg": .int(2)]),
            TeamEfficiencyRow(rank: 3, team: TeamRef(teamId: 1_610_612_760, abbr: "OKC", name: "Oklahoma City Thunder",
                                                     city: "Oklahoma City", nickname: "Thunder", conference: "West", division: "Northwest"),
                              wins: 25, losses: 16,
                              values: ["off_rtg": .double(115.4), "def_rtg": .double(108.9), "net_rtg": .double(6.5), "pace": .double(100.8)],
                              ranks: ["off_rtg": .int(8), "def_rtg": .int(1), "net_rtg": .int(3)]),
            TeamEfficiencyRow(rank: 4, team: TeamRef(teamId: 1_610_612_755, abbr: "PHI", name: "Philadelphia 76ers",
                                                     city: "Philadelphia", nickname: "76ers", conference: "East", division: "Atlantic"),
                              wins: 21, losses: 20,
                              values: ["off_rtg": .double(112.3), "def_rtg": .double(116.8), "net_rtg": .double(-4.5), "pace": .double(96.4)],
                              ranks: ["off_rtg": .int(19), "def_rtg": .int(26), "net_rtg": .int(22)])
        ]
    )
}

#Preview("Team efficiency") {
    ScrollView {
        VStack(alignment: .leading, spacing: Spacing.lg) {
            Text("Table — large").hardwoodText(.caption)
            TeamEfficiencyWidget(payload: .preview, size: .large)
                .hardwoodCard()
            Text("Table — medium").hardwoodText(.caption)
            TeamEfficiencyWidget(payload: .preview, size: .medium)
                .hardwoodCard()
            Text("Scatter — defensive rating inverted").hardwoodText(.caption)
            TeamEfficiencyWidget(payload: TeamEfficiencyPreviewData.scatter, size: .large)
                .hardwoodCard()
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}
#endif
