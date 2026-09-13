import Foundation
import SwiftUI

// MARK: - Availability vocabulary

public extension MetricAvailability {

    /// The glyph the badge renders, or `nil` when a measured value needs no marker at all.
    var hardwoodBadgeText: String? {
        switch self {
        case .full:        return nil
        case .estimated:   return "est."
        case .partial:     return "^"
        case .unavailable: return "\u{2014}"
        }
    }

    /// A few words, for accessibility labels and inline footnotes.
    var hardwoodShortLabel: String {
        switch self {
        case .full:        return "measured"
        case .estimated:   return "estimated from the box score"
        case .partial:     return "partial, some games are missing inputs"
        case .unavailable: return "not tracked in this era"
        }
    }

    /// The headline of the explainer.
    var hardwoodHeadline: String {
        switch self {
        case .full:        return "Measured from official records"
        case .estimated:   return "Estimated, not measured"
        case .partial:     return "Built from an incomplete set of games"
        case .unavailable: return "The league did not track this yet"
        }
    }

    /// The color the marker is drawn in.
    var hardwoodMarkerColor: Color {
        switch self {
        case .full:        return Palette.textTertiary
        case .estimated:   return Palette.warning
        case .partial:     return Palette.warning
        case .unavailable: return Palette.textTertiary
        }
    }

    /// Plain English, with the metric and season filled in when the caller knows them.
    func hardwoodExplanation(metricName: String? = nil, season: String? = nil) -> String {
        let metric = metricName ?? "This stat"
        let when = season.map { "the \($0) season" } ?? "this era"
        switch self {
        case .full:
            return "\(metric) is taken straight from the league's official record for \(when). No estimation is involved."
        case .estimated:
            return "The NBA did not publish possession data before 1996-97, so \(metric) for \(when) is derived from the box score with Basketball-Reference style formulas. It is a careful estimate, not a measured number, and it should not be compared with a modern figure without that caveat."
        case .partial:
            return "Some of the games behind \(metric) in \(when) are missing the inputs the formula needs, so this number covers only part of the span. The caret marks values built from an incomplete set of games."
        case .unavailable:
            return "\(metric) did not exist in the league's record for \(when), so there is no number to show. Hardwood shows an em dash rather than a zero, because a zero here would be a lie."
        }
    }
}

// MARK: - Badge

/// The era-honesty marker that sits beside a value.
///
/// `.full` renders nothing at all — a measured modern number should carry no chrome. Everything
/// else renders a marker that is tappable and explains itself.
public struct AvailabilityBadge: View {
    private let availability: MetricAvailability
    private let showsText: Bool
    private let isInteractive: Bool
    private let metricName: String?
    private let season: String?
    private let notes: [String]

    @State private var isExplaining = false

    public init(availability: MetricAvailability,
                showsText: Bool = true,
                isInteractive: Bool = true,
                metricName: String? = nil,
                season: String? = nil,
                notes: [String] = []) {
        self.availability = availability
        self.showsText = showsText
        self.isInteractive = isInteractive
        self.metricName = metricName
        self.season = season
        self.notes = notes
    }

    private var accessibilityText: String {
        if let metricName {
            return "\(metricName): \(availability.hardwoodShortLabel)"
        }
        return availability.hardwoodShortLabel
    }

    public var body: some View {
        // A measured modern value gets no chrome and no sheet attached: this view sits beside
        // every number in a dense table, so the `.full` path has to cost nothing.
        if availability == .full {
            EmptyView()
        } else {
            markerControl
                .sheet(isPresented: $isExplaining) {
                    AvailabilityExplainer(availability: availability,
                                          metricName: metricName,
                                          season: season,
                                          notes: notes)
                        .presentationDetents([.medium, .large])
                        .presentationDragIndicator(.visible)
                }
        }
    }

    @ViewBuilder private var markerControl: some View {
        if isInteractive {
            Button {
                isExplaining = true
            } label: {
                marker
            }
            .buttonStyle(.plain)
            .accessibilityLabel(accessibilityText)
            .accessibilityHint("Explains how this number was produced.")
        } else {
            marker
                .accessibilityLabel(accessibilityText)
        }
    }

    @ViewBuilder private var marker: some View {
        switch availability {
        case .full:
            EmptyView()
        case .estimated:
            if showsText {
                capsuleMarker(text: "est.", color: Palette.warning)
            } else {
                Circle()
                    .strokeBorder(Palette.warning, lineWidth: 1.5)
                    .frame(width: 7, height: 7)
            }
        case .partial:
            if showsText {
                Text(verbatim: "^")
                    .font(Typography.tableHeader)
                    .foregroundStyle(Palette.warning)
                    .padding(.horizontal, Spacing.xxs)
                    .background(
                        RoundedRectangle(cornerRadius: Radius.chip, style: .continuous)
                            .fill(Palette.warning.opacity(0.15))
                    )
            } else {
                Circle()
                    .strokeBorder(Palette.warning, lineWidth: 1.5)
                    .frame(width: 6, height: 6)
            }
        case .unavailable:
            // Compact call sites sit beside a value that is *already* the em dash, so the full
            // glyph would draw a second one right next to it. Nothing to add here.
            if showsText {
                Text(verbatim: "\u{2014}")
                    .font(Typography.tableHeader)
                    .foregroundStyle(Palette.textTertiary)
                    .padding(.horizontal, Spacing.xs)
                    .padding(.vertical, 1)
                    .overlay(
                        Capsule(style: .continuous)
                            .strokeBorder(Palette.separator,
                                          style: StrokeStyle(lineWidth: 1, dash: [2, 2]))
                    )
            } else {
                EmptyView()
            }
        }
    }

    private func capsuleMarker(text: String, color: Color) -> some View {
        Text(text)
            .font(Typography.tableHeader)
            .foregroundStyle(color)
            .padding(.horizontal, Spacing.xs)
            .padding(.vertical, 1)
            .background(Capsule(style: .continuous).fill(color.opacity(0.15)))
            .overlay(Capsule(style: .continuous).strokeBorder(color.opacity(0.35), lineWidth: 1))
    }
}

// MARK: - Text and view treatments

/// A single rule along the bottom edge, stroked with a dash pattern for estimated values.
private struct BaselineRule: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY))
        return path
    }
}

public extension Text {
    /// Applies the era treatment that belongs to a value's availability: a dashed underline for
    /// an estimate, a muted color for a stat that never existed.
    func availabilityStyled(_ availability: MetricAvailability) -> Text {
        switch availability {
        case .full:
            return self
        case .estimated:
            return self.underline(true, pattern: .dash, color: Palette.warning)
        case .partial:
            return self
        case .unavailable:
            return self.foregroundStyle(Palette.textTertiary)
        }
    }
}

/// The view-level equivalent of `Text.availabilityStyled(_:)`, for values that are not a single
/// `Text` run (a number with a unit beside it, for example).
public struct AvailabilityTreatment: ViewModifier {
    private let availability: MetricAvailability

    public init(availability: MetricAvailability) {
        self.availability = availability
    }

    public func body(content: Content) -> some View {
        content
            .foregroundStyle(availability == .unavailable ? Palette.textTertiary : Palette.textPrimary)
            .overlay(alignment: .bottom) {
                if availability == .estimated {
                    BaselineRule()
                        .stroke(Palette.warning, style: StrokeStyle(lineWidth: 1, dash: [2, 2]))
                        .frame(height: 2)
                }
            }
    }
}

public extension View {
    /// Applies the era treatment for an availability value.
    func availabilityTreatment(_ availability: MetricAvailability) -> some View {
        modifier(AvailabilityTreatment(availability: availability))
    }

    /// Presents the explainer as a half-height sheet.
    func availabilityExplainer(isPresented: Binding<Bool>,
                               availability: MetricAvailability,
                               metricName: String? = nil,
                               season: String? = nil,
                               notes: [String] = []) -> some View {
        sheet(isPresented: isPresented) {
            AvailabilityExplainer(availability: availability,
                                  metricName: metricName,
                                  season: season,
                                  notes: notes)
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
        }
    }
}

// MARK: - Explainer

/// One line of the league's record-keeping history.
public struct EraFact: Identifiable, Hashable, Sendable {
    public let season: String
    public let headline: String
    public let detail: String

    public init(season: String, headline: String, detail: String) {
        self.season = season
        self.headline = headline
        self.detail = detail
    }

    public var id: String { season }
}

/// Explains, in plain English, why a stat is missing or estimated for an era.
///
/// Suitable as a sheet body or a popover body; it brings no navigation chrome of its own.
public struct AvailabilityExplainer: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.isPresented) private var isPresented

    private let availability: MetricAvailability
    private let metricName: String?
    private let season: String?
    private let notes: [String]

    public init(availability: MetricAvailability,
                metricName: String? = nil,
                season: String? = nil,
                notes: [String] = []) {
        self.availability = availability
        self.metricName = metricName
        self.season = season
        self.notes = notes
    }

    /// The dates the league's record actually changes. These are the boundaries every era
    /// caveat in the app traces back to.
    public static let eraTimeline: [EraFact] = [
        EraFact(season: "1946-47",
                headline: "The basic box score",
                detail: "Field goals, free throws, points, personal fouls and assists. Nothing else was written down."),
        EraFact(season: "1950-51",
                headline: "Rebounds",
                detail: "Totals only. There is no offensive/defensive split for another 23 seasons."),
        EraFact(season: "1951-52",
                headline: "Minutes played",
                detail: "The first season any per-minute rate — PER, WS/48 — can be computed at all."),
        EraFact(season: "1973-74",
                headline: "Steals, blocks, and the OREB/DREB split",
                detail: "The first season box plus/minus and VORP can be estimated."),
        EraFact(season: "1977-78",
                headline: "Individual turnovers",
                detail: "Usage rate and turnover rate begin here; before this they cannot be computed for a player."),
        EraFact(season: "1979-80",
                headline: "The three point line",
                detail: "Three pointers made and attempted, and therefore 3PAr and effective FG%, start with this season."),
        EraFact(season: "1996-97",
                headline: "Advanced box scores and play-by-play",
                detail: "The first season with measured possessions: per-game offensive and defensive ratings, plus/minus, play-by-play and shot charts."),
        EraFact(season: "2013-14",
                headline: "Player tracking",
                detail: "SportVU cameras league-wide, replaced by Hawk-Eye optical tracking in 2023-24."),
        EraFact(season: "2016-17",
                headline: "Hustle stats",
                detail: "Deflections, contested shots, screen assists and loose balls; box outs were added around 2019-20.")
    ]

    public var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.lg) {
                header
                Text(availability.hardwoodExplanation(metricName: metricName, season: season))
                    .hardwoodText(.tableCell, color: Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                if !notes.isEmpty {
                    notesSection
                }
                timelineSection
                Text("Pre-1997 advanced season numbers are Basketball-Reference style estimates from the box score, not measured possessions. Hardwood labels them rather than quietly mixing them with modern figures.")
                    .hardwoodText(.caption)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(Spacing.lg)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(Palette.background.ignoresSafeArea())
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                Text(availability.hardwoodHeadline)
                    .hardwoodText(.sectionTitle)
                    .fixedSize(horizontal: false, vertical: true)
                if let metricName {
                    Text(season.map { "\(metricName) · \($0)" } ?? metricName)
                        .hardwoodText(.caption)
                }
            }
            Spacer(minLength: Spacing.sm)
            if isPresented {
                Button("Done") { dismiss() }
                    .buttonStyle(.plain)
                    .font(Typography.widgetTitle)
                    .foregroundStyle(Palette.selection)
            }
        }
    }

    private var notesSection: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            Text("What is missing here")
                .hardwoodText(.statLabel)
            ForEach(notes, id: \.self) { note in
                HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
                    Text(verbatim: "\u{2022}")
                        .hardwoodText(.caption, color: Palette.warning)
                    Text(note)
                        .hardwoodText(.tableCell, color: Palette.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Spacing.md)
        .background(
            RoundedRectangle(cornerRadius: Radius.control, style: .continuous)
                .fill(Palette.warning.opacity(0.10))
        )
    }

    private var timelineSection: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            Text("When the record changed")
                .hardwoodText(.statLabel)
            ForEach(Self.eraTimeline) { fact in
                HStack(alignment: .top, spacing: Spacing.md) {
                    Text(fact.season)
                        .hardwoodText(.tableHeader, color: Palette.textPrimary, monospacedDigits: true)
                        .frame(minWidth: 68, alignment: .leading)
                        .fixedSize(horizontal: true, vertical: false)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(fact.headline)
                            .hardwoodText(.tableCell)
                            .fixedSize(horizontal: false, vertical: true)
                        Text(fact.detail)
                            .hardwoodText(.caption)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .accessibilityElement(children: .combine)
            }
        }
    }
}

#if DEBUG
#Preview("Badges") {
    VStack(alignment: .leading, spacing: Spacing.lg) {
        ForEach([MetricAvailability.full, .estimated, .partial, .unavailable], id: \.self) { availability in
            HStack(spacing: Spacing.sm) {
                Text(availability.rawValue)
                    .hardwoodText(.tableCell)
                    .frame(width: 110, alignment: .leading)
                AvailabilityBadge(availability: availability,
                                  metricName: "Usage %",
                                  season: "1971-72")
                Spacer(minLength: 0)
                Text(verbatim: "24.8%")
                    .font(Typography.statValue)
                    .availabilityStyled(availability)
            }
        }
    }
    .padding(Spacing.lg)
    .hardwoodCard()
    .padding(Spacing.lg)
    .hardwoodBackground()
}

#Preview("Explainer — unavailable") {
    AvailabilityExplainer(availability: .unavailable, metricName: "Steals", season: "1965-66")
}

#Preview("Explainer — estimated") {
    AvailabilityExplainer(availability: .estimated,
                          metricName: "Usage %",
                          season: "1971-72",
                          notes: ["Individual turnovers were not recorded, so possessions ended are inferred."])
}
#endif
