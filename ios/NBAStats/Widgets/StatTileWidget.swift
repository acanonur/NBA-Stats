import Foundation
import SwiftUI

/// One metric, made as large as the tile allows.
///
/// The server has already formatted `displayValue`, including the em dash for a stat the era
/// never recorded, so this view never turns a missing number into a zero. Everything else on the
/// tile is density: how many supporting metrics fit, and whether the recent-form sparkline has
/// room to say anything.
public struct StatTileWidget: View {

    /// A value paired with the catalog descriptor that names and formats it.
    private struct Entry: Identifiable {
        let index: Int
        let value: MetricValue
        let descriptor: MetricDescriptor?
        var id: Int { index }
    }

    private struct Slate {
        var primary: MetricDescriptor?
        var secondary: [Entry]
        var sparkline: MetricDescriptor?
    }

    private let payload: StatTilePayload
    private let size: WidgetSize

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    public init(payload: StatTilePayload, size: WidgetSize) {
        self.payload = payload
        self.size = size
    }

    // MARK: Density

    private var secondaryLimit: Int {
        switch size {
        case .small:  return dynamicTypeSize.isAccessibilitySize ? 0 : 2
        case .medium: return dynamicTypeSize.isAccessibilitySize ? 2 : 3
        case .large:  return 4
        }
    }

    private var sparklineHeight: CGFloat {
        switch size {
        case .small:  return 24
        case .medium: return 38
        case .large:  return 54
        }
    }

    /// A sparkline with nothing but gaps in it is noise, so it is dropped rather than drawn flat.
    private var showsSparkline: Bool {
        guard payload.sparkline.contains(where: { $0.y != nil }) else { return false }
        if size == .small && dynamicTypeSize.isAccessibilitySize { return false }
        return true
    }

    private var subjectName: String {
        size == .small ? payload.subject.shortName : payload.subject.displayName
    }

    /// The season the era explainer should talk about, read off the server's context line
    /// (`"2025-26 · Regular Season · Per Game"`).
    private var seasonHint: String? {
        guard let context = payload.context else { return nil }
        let separator: Character = "\u{00B7}"
        let head = context.split(separator: separator).first.map {
            $0.trimmingCharacters(in: .whitespaces)
        }
        guard let head = head, !head.isEmpty else { return nil }
        return head
    }

    private var showsRankChip: Bool { (payload.primary.rank ?? 0) > 0 }

    private var showsDeltaChip: Bool {
        guard payload.primary.delta != nil else { return false }
        return size == .small ? !showsRankChip : true
    }

    /// The sparkline's own baseline, and only when it is measuring the headline metric: drawing
    /// the headline's league average under a different stat's line would be a lie.
    private var sparklineBaseline: Double? {
        let key = payload.sparklineMetric ?? payload.primary.metric
        guard key == payload.primary.metric else { return nil }
        return payload.primary.leagueAverage
    }

    // MARK: Catalog

    /// A readable label for a metric key the bundled catalog does not know, so the headline
    /// number is never left unlabelled: `"ts_pct"` becomes `"TS%"`, `"off_rtg"` becomes `"OFF RTG"`.
    private static func fallbackShortName(_ key: String) -> String {
        let separator: Character = "_"
        var parts = key.split(separator: separator).map(String.init)
        var suffix = ""
        if parts.last == "pct" {
            parts.removeLast()
            suffix = "%"
        }
        let body = parts.map { $0.uppercased() }.joined(separator: " ")
        return body.isEmpty ? key.uppercased() : body + suffix
    }

    /// Resolved once, on the main actor, and handed down to the plain view builders below.
    @MainActor private func resolveSlate() -> Slate {
        let catalog = Catalog.shared
        let entries: [Entry] = payload.secondary.prefix(max(secondaryLimit, 0)).enumerated().map { pair in
            Entry(index: pair.offset,
                  value: pair.element,
                  descriptor: catalog.metric(pair.element.metric))
        }
        return Slate(primary: catalog.metric(payload.primary.metric),
                     secondary: entries,
                     sparkline: catalog.metric(payload.sparklineMetric ?? payload.primary.metric))
    }

    // MARK: Body

    public var body: some View {
        let slate = resolveSlate()
        VStack(alignment: .leading, spacing: Spacing.sm) {
            header
            StatValueView(value: payload.primary,
                          descriptor: slate.primary,
                          style: .display,
                          showsLabel: true,
                          showsRank: showsRankChip,
                          showsDelta: showsDeltaChip,
                          season: seasonHint,
                          alignment: .leading)
            if slate.primary == nil {
                // `StatValueView` prints the descriptor's short name; with no descriptor to
                // print, the tile names the metric itself rather than showing a bare number.
                Text(StatTileWidget.fallbackShortName(payload.primary.metric))
                    .hardwoodText(.statLabel)
                    .lineLimit(1)
            }
            if !slate.secondary.isEmpty {
                secondaryRow(slate.secondary)
            }
            if showsSparkline {
                sparklineBlock(descriptor: slate.sparkline)
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Pieces

    private var header: some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            HStack(spacing: Spacing.xs) {
                subjectBadge
                Text(subjectName)
                    .hardwoodText(.widgetTitle)
                    .lineLimit(1)
                    .minimumScaleFactor(0.75)
                Spacer(minLength: 0)
            }
            if size != .small, let context = payload.context, !context.isEmpty {
                Text(context)
                    .hardwoodText(.caption)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private var subjectBadge: some View {
        if let team = payload.subject.team {
            TeamBadge(team: team, size: .small)
        } else if let abbreviation = payload.subject.player?.teamAbbr, !abbreviation.isEmpty {
            TeamBadge(abbreviation: abbreviation, size: .small)
        }
    }

    @ViewBuilder private func secondaryRow(_ entries: [Entry]) -> some View {
        if dynamicTypeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                ForEach(entries) { entry in
                    secondaryValue(entry)
                }
            }
        } else {
            HStack(alignment: .top, spacing: Spacing.md) {
                ForEach(entries) { entry in
                    secondaryValue(entry)
                }
                Spacer(minLength: 0)
            }
        }
    }

    private func secondaryValue(_ entry: Entry) -> some View {
        StatValueView(value: entry.value,
                      descriptor: entry.descriptor,
                      style: .cell,
                      showsLabel: true,
                      showsRank: false,
                      showsDelta: false,
                      season: seasonHint,
                      alignment: .leading)
    }

    private func sparklineBlock(descriptor: MetricDescriptor?) -> some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            Sparkline(points: payload.sparkline.map { $0.y },
                      tint: Palette.chartColor(at: 0),
                      baseline: sparklineBaseline,
                      showsArea: size != .small,
                      accessibilityDescription: sparklineDescription(descriptor: descriptor))
                .frame(height: sparklineHeight)
            if size == .large {
                Text(sparklineCaption(descriptor: descriptor))
                    .hardwoodText(.caption)
                    .lineLimit(1)
            }
        }
    }

    private func sparklineCaption(descriptor: MetricDescriptor?) -> String {
        let name = descriptor?.shortName ?? payload.sparklineMetric ?? payload.primary.metric
        let played = payload.sparkline.filter { $0.y != nil }.count
        let baseline = sparklineBaseline == nil ? "" : " · dashed rule is the league average"
        return "\(name), last \(played) games\(baseline)"
    }

    private func sparklineDescription(descriptor: MetricDescriptor?) -> String {
        let name = descriptor?.name ?? payload.sparklineMetric ?? payload.primary.metric
        let values = payload.sparkline.compactMap { $0.y }
        guard let first = values.first, let last = values.last else {
            return "\(name) over the recent games: no values recorded."
        }
        let format = descriptor?.format ?? .decimal1
        let direction: String
        if abs(last - first) <= 1e-9 {
            direction = "flat"
        } else {
            direction = last > first ? "rising" : "falling"
        }
        let missing = payload.sparkline.count - values.count
        var text = "\(name) over the last \(payload.sparkline.count) games, \(direction), "
        text += "from \(Formatting.value(first, format: format)) to \(Formatting.value(last, format: format))"
        if missing > 0 {
            text += ", \(missing) with no value"
        }
        return text
    }
}

#if DEBUG
#Preview("Stat tile, three sizes") {
    ScrollView {
        VStack(spacing: Spacing.md) {
            ForEach(WidgetSize.allCases, id: \.self) { size in
                StatTileWidget(payload: .preview, size: size)
                    .hardwoodCard()
            }
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}

#Preview("Stat tile, era gap") {
    let payload = StatTilePayload(
        subject: .preview,
        context: "1971-72 · Regular Season · Per Game",
        primary: .previewUnavailable,
        secondary: [MetricValue(metric: "pts", value: 21.2, displayValue: "21.2", rank: 9, availability: .estimated)],
        sparkline: [],
        sparklineMetric: nil
    )
    return VStack(spacing: Spacing.md) {
        StatTileWidget(payload: payload, size: .small)
            .hardwoodCard()
        StatTileWidget(payload: payload, size: .medium)
            .hardwoodCard()
    }
    .padding(Spacing.lg)
    .hardwoodBackground()
}
#endif
