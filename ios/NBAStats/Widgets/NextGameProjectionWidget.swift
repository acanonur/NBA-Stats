import Foundation
import SwiftUI

// MARK: - Interval bar

/// A low–high band with the mean marked inside it.
///
/// `FactorBar` draws a *fill* from the bottom of a domain to one value, which is the right shape
/// for a multiplier around 1.0 but the wrong one for an interval: filling 0 to 40.5 would say the
/// projection covers every value below the upper bound. An interval is a band, so it is drawn as
/// one, with the mean as a rule inside it rather than as the end of a bar.
///
/// File-private on purpose — nothing outside this widget has an interval to draw yet.
private struct ProjectionIntervalBar: View {
    let low: Double?
    let mean: Double?
    let high: Double?
    let domain: ClosedRange<Double>
    let tint: Color
    var barHeight: CGFloat = 10

    private var span: Double { domain.upperBound - domain.lowerBound }

    private func fraction(_ raw: Double?) -> Double? {
        guard let raw, raw.isFinite, span > 1e-12 else { return nil }
        return min(max((raw - domain.lowerBound) / span, 0), 1)
    }

    var body: some View {
        GeometryReader { proxy in
            let width = max(proxy.size.width, 1)
            ZStack(alignment: .leading) {
                Capsule(style: .continuous)
                    .fill(Palette.track)
                    .frame(height: barHeight)
                if let lowFraction = fraction(low), let highFraction = fraction(high), highFraction > lowFraction {
                    Capsule(style: .continuous)
                        .fill(tint.opacity(0.38))
                        .frame(width: max(width * CGFloat(highFraction - lowFraction), barHeight), height: barHeight)
                        .offset(x: min(width * CGFloat(lowFraction), max(width - barHeight, 0)))
                }
                if let meanFraction = fraction(mean) {
                    RoundedRectangle(cornerRadius: 1.5, style: .continuous)
                        .fill(tint)
                        .overlay(
                            RoundedRectangle(cornerRadius: 1.5, style: .continuous)
                                .strokeBorder(Palette.surface, lineWidth: 1)
                        )
                        .frame(width: 3, height: barHeight + 8)
                        .offset(x: min(max(width * CGFloat(meanFraction) - 1.5, 0), max(width - 3, 0)))
                }
            }
            .frame(height: barHeight + 8, alignment: .center)
        }
        .frame(height: barHeight + 8)
        // The numbers beside the bar carry the meaning; the drawing is decoration for a reader
        // who can see it, and a second unlabelled announcement for one who cannot.
        .accessibilityHidden(true)
    }
}

// MARK: - Widget

/// `next_game_projection` — a projected box score, rendered as an argument rather than an oracle.
///
/// The five honesty rules in `docs/PROJECTION.md` §7 are the whole design of this view:
///
/// 1. No mean is drawn without its interval. Every stat line carries its low–high beside the
///    mean, in the same weight, and a line that arrived *without* an interval says so on the line
///    instead of quietly showing a bare number.
/// 2. Projected minutes get their own block above the stat lines, with the interval and the
///    reason they matter: supplying true minutes cuts points error by 19.28% where the best model
///    gains 3.71%, so a reader who disagrees with the minutes has to meet that number first.
/// 3. The context factors are shown as multipliers around 1.0, each with its label and its
///    effect, so the projection can be argued with a factor at a time.
/// 4. A rate that leans on the league prior, or one with thin exposure behind it, is marked on
///    its own line; the exposure itself is stated under the lines at every size.
/// 5. Every projected value is `availability: .estimated` and gets the app's established
///    estimated treatment — the dashed underline and the `est.` marker.
///
/// There is deliberately nothing here about odds, implied probability, expected value or staking
/// (`docs/PROJECTION.md` §6), and no place to put it.
public struct NextGameProjectionWidget: View {

    /// One projected line paired with the descriptor that formats it.
    ///
    /// The payload usually carries its own descriptor; the catalog is the fallback so a line
    /// whose descriptor a build cannot read still gets its short name, format and direction.
    private struct LineReading: Identifiable {
        let line: ProjectedLine
        let descriptor: MetricDescriptor?

        var id: String { line.metric }
        var label: String { descriptor?.shortName ?? line.shortName }
        var name: String { descriptor?.name ?? line.name }
        var format: MetricFormat? { descriptor?.format }
        /// `nil` when the catalog does not know the metric: the direction is genuinely unknown
        /// then, and guessing "higher is better" paints a green chip on a worse turnover number.
        var higherIsBetter: Bool? { descriptor?.higherIsBetter }
    }

    private let payload: NextGameProjectionPayload
    private let size: WidgetSize

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @ScaledMetric(relativeTo: .caption) private var labelWidth: CGFloat = 50
    @ScaledMetric(relativeTo: .title3) private var valueWidth: CGFloat = 68
    @ScaledMetric(relativeTo: .footnote) private var rangeWidth: CGFloat = 76

    public init(payload: NextGameProjectionPayload, size: WidgetSize) {
        self.payload = payload
        self.size = size
    }

    // MARK: Density

    /// At an accessibility size the columns stop being columns: a row that would truncate
    /// stacks instead, because a clipped interval is worse than no interval.
    private var isStacked: Bool { dynamicTypeSize.isAccessibilitySize }

    private var visibleLineCount: Int {
        switch size {
        case .small:  return 2
        case .medium: return 4
        case .large:  return payload.lines.count
        }
    }

    private var hiddenLineCount: Int {
        max(payload.lines.count - visibleLineCount, 0)
    }

    /// What the tile says instead of silently dropping the lines that did not fit.
    private var truncationText: String? {
        switch hiddenLineCount {
        case 0:
            return nil
        case 1:
            return "One more projected stat fits at the large size."
        default:
            return "\(hiddenLineCount) more projected stats fit at the large size."
        }
    }

    /// Per-line diagnostics — exposure, shrinkage, half-life, dispersion — only fit on `.large`.
    private var showsDiagnostics: Bool { size == .large }

    /// The factor bars need room for a label, a number and a track; `.small` gets the same four
    /// numbers as one line of text rather than losing them.
    private var showsFactorBars: Bool { size != .small }

    /// One player fills this whole tile, so the portrait earns its room at every size the catalog
    /// offers; `.small` only ever appears if a layout is hand-edited down to it.
    private var headerAvatarSize: PlayerAvatar.Size {
        size == .small ? .small : .medium
    }

    private var showsCombo: Bool { size != .small }

    // MARK: Derived text

    private var minutes: ProjectedMinutes { payload.projectedMinutes }

    private var minutesDisplay: String {
        if !minutes.displayValue.isEmpty, minutes.displayValue != Formatting.emDash {
            return minutes.displayValue
        }
        return Formatting.decimal(minutes.value, places: 1)
    }

    private var minutesHasInterval: Bool {
        guard let low = minutes.low, let high = minutes.high else { return false }
        return low.isFinite && high.isFinite
    }

    /// Minutes are plotted inside a whole game rather than inside their own interval, so the
    /// width of the band means something: 27–40 of a possible 48 is a wide night.
    private var minutesDomain: ClosedRange<Double> {
        let values = [minutes.low, minutes.high, minutes.value].compactMap { $0 }.filter { $0.isFinite }
        let upper = max(48, (values.max() ?? 48) + 1)
        return 0...upper
    }

    private var minutesComparisonText: String? {
        guard let seasonAverage = minutes.seasonAverage, seasonAverage.isFinite else { return nil }
        let average = Formatting.decimal(seasonAverage, places: 1)
        guard let delta = minutes.deltaFromSeasonAverage, delta.isFinite, abs(delta) >= 0.05 else {
            return "Season average \(average)"
        }
        return "\(Formatting.decimal(delta, places: 1, signed: true)) vs season average \(average)"
    }

    private static let minutesRationale = "Minutes carry most of the error. Knowing the true minutes would cut the points error by about 19%; a better production model gains under 4%. Disagree with this number and everything below moves with it."

    /// The interval level the lines were built at, for the column header: `"80% interval"`.
    private var intervalCaption: String {
        payload.lines.compactMap { $0.intervalLevelText }.first ?? "range"
    }

    /// True when *no* line arrived with bounds — the reader asked for means only, which §7 rule 1
    /// says is misleading, so it is said once at the top rather than on every line.
    private var intervalsMissing: Bool {
        !payload.lines.isEmpty && payload.lines.allSatisfy { $0.low == nil || $0.high == nil }
    }

    /// Exposure, surfaced at every size — a projection built on 40 minutes of history is a
    /// different object from one built on 1,240, and the difference has to be readable.
    private var exposureSummary: String? {
        let values = payload.lines.compactMap { $0.exposureMinutes }.filter { $0.isFinite && $0 >= 0 }
        guard let low = values.min(), let high = values.max() else { return nil }
        if abs(high - low) < 0.5 {
            return "\(Formatting.decimal(high, places: 0)) minutes of this player's own history sit behind these rates."
        }
        return "\(Formatting.decimal(low, places: 0))–\(Formatting.decimal(high, places: 0)) minutes of this player's own history sit behind these rates."
    }

    private var visibleNotes: [String] {
        payload.notes.filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    }

    private var methodLines: [String] {
        var lines: [String] = []
        if let summary = payload.method.summary, !summary.isEmpty {
            lines.append(summary)
        }
        var clauses: [String] = ["Projected minutes times a shrunken per-minute rate times the factors above"]
        if let halfLife = payload.method.minutesHalfLifeGames {
            clauses.append("minutes follow a \(halfLife)-game half-life of their own")
        }
        if let games = payload.method.dispersionShrinkageGames {
            clauses.append("the per-player spread multiplier is shrunk toward 1.0 over \(games) games")
        }
        lines.append(clauses.joined(separator: "; ") + ".")
        if let level = intervalLevelDescription {
            lines.append(level)
        }
        lines.append("The dashed underline and the est. mark mean the same thing here as everywhere else in Hardwood: this number was estimated, never recorded.")
        return lines
    }

    private var intervalLevelDescription: String? {
        guard let level = payload.lines.compactMap({ $0.intervalLevel }).first,
              level.isFinite, level > 0 else { return nil }
        return "Ranges are the \(Formatting.percent(level, places: 0)) negative-binomial interval, after the per-player spread multiplier."
    }

    // MARK: Catalog

    @MainActor private func resolveLines() -> [LineReading] {
        let catalog = Catalog.shared
        return payload.lines.prefix(max(visibleLineCount, 0)).map { line in
            LineReading(line: line, descriptor: line.descriptor ?? catalog.metric(line.metric))
        }
    }

    // MARK: Body

    public var body: some View {
        let readings = resolveLines()
        VStack(alignment: .leading, spacing: Spacing.md) {
            header
            minutesBlock
            statLinesSection(readings)
            comboBlock
            whyBlock
            factorsBlock
            HardwoodChartFootnote(lines: methodLines)
            notesBlock
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Header

    private var header: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            PlayerRow(player: payload.player, avatar: headerAvatarSize)
            gameLine
        }
    }

    @ViewBuilder private var gameLine: some View {
        if let game = payload.game {
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
                    if let abbr = game.opponentAbbr ?? game.opponent?.abbr, !abbr.isEmpty {
                        TeamBadge(abbreviation: abbr, name: game.opponent?.name, size: .small)
                            .accessibilityHidden(true)
                    }
                    Text(matchupText(game))
                        .hardwoodText(.statLabel, monospacedDigits: true)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
                if showsDiagnostics, let inputs = opponentInputsText(game) {
                    Text(inputs)
                        .hardwoodText(.caption, monospacedDigits: true)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(gameAccessibilityText(game))
        } else {
            // §7 rule 4 in its bluntest form: with no opponent there is no `f_opp`, no venue and
            // no rest, and the reader is told that before reading a single number.
            HStack(alignment: .top, spacing: Spacing.xs) {
                Image(systemName: "calendar")
                    .imageScale(.small)
                    .foregroundStyle(Palette.warning)
                    .accessibilityHidden(true)
                Text("No scheduled next game. These numbers are projected against a league-average opponent, at a neutral venue, on normal rest.")
                    .hardwoodText(.caption, color: Palette.warning)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .accessibilityElement(children: .combine)
        }
    }

    private func matchupText(_ game: ProjectionGame) -> String {
        var parts: [String] = [game.matchupText]
        let date = Formatting.mediumGameDate(game.date)
        if date != Formatting.emDash { parts.append(date) }
        let rest = game.restText
        if rest != Formatting.emDash { parts.append(rest) }
        return parts.joined(separator: " · ")
    }

    /// The two raw numbers behind `f_opp` and `f_pace`, so the factors below can be checked.
    private func opponentInputsText(_ game: ProjectionGame) -> String? {
        var parts: [String] = []
        if let defRtg = game.opponentDefRtg, defRtg.isFinite {
            let opponent = game.opponentAbbr ?? game.opponent?.abbr ?? "Opponent"
            parts.append("\(opponent) defensive rating \(Formatting.decimal(defRtg, places: 1))")
        }
        if let pace = game.expectedPace, pace.isFinite {
            parts.append("expected pace \(Formatting.decimal(pace, places: 1))")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    private func gameAccessibilityText(_ game: ProjectionGame) -> String {
        var parts: [String] = []
        let opponent = game.opponent?.name ?? game.opponentAbbr ?? "an unnamed opponent"
        switch game.isHome {
        case .some(true):  parts.append("At home against \(opponent)")
        case .some(false): parts.append("Away at \(opponent)")
        case .none:        parts.append("Against \(opponent)")
        }
        let date = Formatting.mediumGameDate(game.date)
        if date != Formatting.emDash { parts.append(date) }
        let rest = game.restText
        if rest != Formatting.emDash { parts.append(rest) }
        if showsDiagnostics, let inputs = opponentInputsText(game) { parts.append(inputs) }
        return parts.joined(separator: ", ")
    }

    // MARK: Projected minutes

    /// §7 rule 2. The minutes sit above the stat lines, in the display type, in their own
    /// surface, with their interval and the reason they dominate.
    private var minutesBlock: some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            minutesHeader
            minutesValueRow
            if minutesHasInterval || minutes.value != nil {
                ProjectionIntervalBar(low: minutes.low,
                                      mean: minutes.value,
                                      high: minutes.high,
                                      domain: minutesDomain,
                                      tint: Palette.selection)
            }
            Text(Self.minutesRationale)
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(Spacing.sm)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: Radius.control, style: .continuous)
                .fill(Palette.surfaceSunken)
        )
        .overlay(
            RoundedRectangle(cornerRadius: Radius.control, style: .continuous)
                .strokeBorder(Palette.separator, lineWidth: 1)
        )
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(minutesAccessibilityText)
    }

    private var minutesHeader: some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
            Text("Projected minutes")
                .hardwoodText(.statLabel)
            Spacer(minLength: Spacing.xs)
            if let comparison = minutesComparisonText {
                // Deliberately not a `DeltaChip`: more minutes than usual is not "better", and
                // a green arrow would say it was.
                Text(comparison)
                    .hardwoodText(.caption, monospacedDigits: true)
                    .lineLimit(2)
                    .multilineTextAlignment(.trailing)
            }
        }
    }

    @ViewBuilder private var minutesValueRow: some View {
        if isStacked {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                minutesHero
                minutesRange
            }
        } else {
            HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
                minutesHero
                Spacer(minLength: Spacing.xs)
                minutesRange
            }
        }
    }

    private var minutesHero: some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.xs) {
            Text(minutesDisplay)
                .availabilityStyled(.estimated)
                .hardwoodText(.displayValue, color: Palette.textPrimary, monospacedDigits: true)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            // `isInteractive: false` throughout this widget: the shared explainer tells the
            // pre-1997 possession-data story, which is true of a derived season stat and false
            // of a projection. The footnotes below say what "estimated" means here instead.
            AvailabilityBadge(availability: .estimated,
                              showsText: true,
                              isInteractive: false,
                              metricName: "Projected minutes")
        }
    }

    private var minutesRange: some View {
        VStack(alignment: isStacked ? .leading : .trailing, spacing: 0) {
            Text(minutes.intervalText)
                .hardwoodText(.tableCell,
                              color: minutesHasInterval ? Palette.textPrimary : Palette.textTertiary,
                              monospacedDigits: true)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            Text(minutesHasInterval ? "likely range" : "no range given")
                .hardwoodText(.tableHeader, color: minutesHasInterval ? Palette.textSecondary : Palette.warning)
        }
    }

    private var minutesAccessibilityText: String {
        var parts: [String] = ["Projected minutes", minutesDisplay]
        if let low = minutes.low, let high = minutes.high, low.isFinite, high.isFinite {
            parts.append("likely range \(Formatting.decimal(low, places: 1)) to \(Formatting.decimal(high, places: 1))")
        } else {
            parts.append("no range given for the minutes")
        }
        if let comparison = minutesComparisonText { parts.append(comparison) }
        if let halfLife = minutes.halfLifeGames {
            parts.append("fitted on a \(halfLife)-game half-life")
        }
        parts.append("estimated, not a record")
        parts.append(Self.minutesRationale)
        return parts.joined(separator: ", ")
    }

    // MARK: Stat lines

    @ViewBuilder private func statLinesSection(_ readings: [LineReading]) -> some View {
        if readings.isEmpty {
            Text("This projection carries no statistic lines.")
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            VStack(alignment: .leading, spacing: isStacked ? Spacing.md : Spacing.sm) {
                if intervalsMissing { intervalWarning }
                if !isStacked { columnHeader }
                ForEach(readings) { reading in
                    statLine(reading)
                }
                if let truncation = truncationText {
                    Text(truncation)
                        .hardwoodText(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let exposure = exposureSummary {
                    Text(exposure)
                        .hardwoodText(.caption, monospacedDigits: true)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private var intervalWarning: some View {
        HStack(alignment: .top, spacing: Spacing.xs) {
            Image(systemName: "exclamationmark.triangle")
                .imageScale(.small)
                .foregroundStyle(Palette.warning)
                .accessibilityHidden(true)
            Text("This projection arrived without intervals. A mean on its own is the one number the model is least willing to stand behind.")
                .hardwoodText(.caption, color: Palette.warning)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }

    private var columnHeader: some View {
        HStack(spacing: Spacing.sm) {
            Text("Stat")
                .frame(width: labelWidth, alignment: .leading)
            Text("Projected")
                .frame(width: valueWidth, alignment: .trailing)
            Text(intervalCaption)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
                .frame(width: rangeWidth, alignment: .trailing)
            Spacer(minLength: 0)
            Text("vs season")
        }
        .hardwoodText(.tableHeader)
        .accessibilityHidden(true)
    }

    @ViewBuilder private func statLine(_ reading: LineReading) -> some View {
        Group {
            if isStacked {
                stackedLine(reading)
            } else {
                compactLine(reading)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText(for: reading))
    }

    private func compactLine(_ reading: LineReading) -> some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
                Text(reading.label)
                    .hardwoodText(.statLabel)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                    .frame(width: labelWidth, alignment: .leading)
                meanText(reading)
                    .frame(width: valueWidth, alignment: .trailing)
                rangeText(reading)
                    .frame(width: rangeWidth, alignment: .trailing)
                Spacer(minLength: Spacing.xs)
                deltaView(reading)
            }
            lineAnnotations(reading)
        }
    }

    private func stackedLine(_ reading: LineReading) -> some View {
        VStack(alignment: .leading, spacing: Spacing.xs) {
            HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
                Text(reading.name)
                    .hardwoodText(.statLabel)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: Spacing.xs)
                deltaView(reading)
            }
            meanText(reading)
            HStack(alignment: .firstTextBaseline, spacing: Spacing.xs) {
                Text(intervalCaption)
                    .hardwoodText(.tableHeader)
                rangeText(reading)
            }
            lineAnnotations(reading)
        }
    }

    @ViewBuilder private func lineAnnotations(_ reading: LineReading) -> some View {
        if let caution = cautionText(reading) {
            cautionLabel(caution)
        }
        if showsDiagnostics, let diagnostics = diagnosticsText(reading) {
            Text(diagnostics)
                .hardwoodText(.caption, monospacedDigits: true)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func meanText(_ reading: LineReading) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.xxs) {
            Text(reading.line.displayValue.isEmpty ? Formatting.emDash : reading.line.displayValue)
                .availabilityStyled(reading.line.availability)
                .hardwoodText(.statValue,
                              color: reading.line.availability == .unavailable
                                  ? Palette.textTertiary
                                  : Palette.textPrimary,
                              monospacedDigits: true)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            AvailabilityBadge(availability: reading.line.availability,
                              showsText: false,
                              isInteractive: false,
                              metricName: reading.name)
        }
    }

    private func rangeText(_ reading: LineReading) -> some View {
        let hasInterval = reading.line.low != nil && reading.line.high != nil
        return Text(reading.line.intervalText)
            .hardwoodText(.tableCell,
                          color: hasInterval ? Palette.textPrimary : Palette.textTertiary,
                          monospacedDigits: true)
            .lineLimit(1)
            .minimumScaleFactor(0.7)
    }

    @ViewBuilder private func deltaView(_ reading: LineReading) -> some View {
        if let higherIsBetter = reading.higherIsBetter {
            DeltaChip(delta: reading.line.delta,
                      higherIsBetter: higherIsBetter,
                      format: reading.format ?? .decimal1)
        } else if let delta = reading.line.delta, delta.isFinite, abs(delta) > 1e-9 {
            // No descriptor, so no direction: the number is shown without a verdict on it.
            Text(Formatting.decimal(delta, places: 1, signed: true))
                .hardwoodText(.tableHeader, color: Palette.textSecondary, monospacedDigits: true)
        } else {
            EmptyView()
        }
    }

    private func cautionLabel(_ text: String) -> some View {
        HStack(alignment: .top, spacing: Spacing.xs) {
            Image(systemName: "exclamationmark.triangle")
                .imageScale(.small)
                .foregroundStyle(Palette.warning)
                .accessibilityHidden(true)
            Text(text)
                .hardwoodText(.caption, color: Palette.warning)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: Line honesty

    /// §7 rule 4. A rate that is mostly the league prior, or one with less exposure behind it
    /// than its own stabilisation constant, is not a confident number and does not get to look
    /// like one.
    private static func isThin(_ line: ProjectedLine) -> Bool {
        if line.leansOnLeaguePrior { return true }
        guard let exposure = line.exposureMinutes, exposure.isFinite else { return false }
        if let k = line.shrinkageK, k.isFinite, k > 0 { return exposure < k }
        return exposure < 200
    }

    private func cautionText(_ reading: LineReading) -> String? {
        let line = reading.line
        var parts: [String] = []
        if line.low == nil || line.high == nil, !intervalsMissing {
            parts.append("No interval on this line, so the mean alone overstates what is known")
        }
        if Self.isThin(line) {
            if let weight = line.shrinkageWeight, weight.isFinite {
                let share = Formatting.percent(min(max(weight, 0), 1), places: 0)
                parts.append("Only \(share) of this rate is the player; the rest is the league average")
            } else {
                parts.append("Thin history behind this rate, so it leans on the league average")
            }
            if let exposure = line.exposureMinutes, exposure.isFinite {
                parts.append("\(Formatting.decimal(exposure, places: 0)) min of exposure")
            }
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    private func diagnosticsText(_ reading: LineReading) -> String? {
        let line = reading.line
        var parts: [String] = []
        if let rate = line.ratePerMinute, rate.isFinite {
            parts.append("\(Formatting.decimal(rate, places: 3)) per minute")
        }
        if let weight = line.shrinkageWeight, weight.isFinite, !Self.isThin(line) {
            parts.append("\(Formatting.percent(min(max(weight, 0), 1), places: 0)) player, rest league prior")
        }
        if let k = line.shrinkageK, k.isFinite {
            parts.append("stabilises at \(Formatting.decimal(k, places: 0)) min")
        }
        if let exposure = line.exposureMinutes, exposure.isFinite, !Self.isThin(line) {
            parts.append("\(Formatting.decimal(exposure, places: 0)) min of exposure")
        }
        if let halfLife = line.halfLifeGames {
            parts.append("\(halfLife)-game half-life")
        }
        if let multiplier = line.dispersionMultiplier, multiplier.isFinite {
            parts.append("spread multiplier \(Formatting.decimal(multiplier, places: 2))")
        }
        if let alpha = line.dispersionAlpha, alpha.isFinite {
            parts.append("dispersion \(Formatting.decimal(alpha, places: 3))")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// One projected line, spoken. A range read as two bare numbers is useless, so the bounds
    /// are read as "from low to high" with the level that produced them.
    private func accessibilityText(for reading: LineReading) -> String {
        var parts: [String] = [reading.name]
        let value = reading.line.displayValue.isEmpty ? Formatting.emDash : reading.line.displayValue
        parts.append("projected \(value)")
        if let range = spokenRange(reading) {
            parts.append(range)
        } else {
            parts.append("no interval given")
        }
        if let delta = reading.line.delta, delta.isFinite, abs(delta) > 1e-9 {
            let magnitude = HardwoodNumberFormat.magnitude(delta, format: reading.format ?? .decimal1)
            let direction = delta > 0 ? "up" : "down"
            if let average = reading.line.seasonAverage, average.isFinite {
                let averageText = HardwoodNumberFormat.string(average, format: reading.format ?? .decimal1)
                parts.append("\(direction) \(magnitude) against a season average of \(averageText)")
            } else {
                parts.append("\(direction) \(magnitude) against the season average")
            }
        }
        // Not `hardwoodShortLabel`: that one explains the pre-1997 box-score estimate, which is
        // the wrong sentence for a projection even though the availability value is the same.
        parts.append("estimated, not a record")
        if let caution = cautionText(reading) { parts.append(caution) }
        if showsDiagnostics, let diagnostics = diagnosticsText(reading) { parts.append(diagnostics) }
        return parts.joined(separator: ", ")
    }

    private func spokenRange(_ reading: LineReading) -> String? {
        guard let low = reading.line.low, let high = reading.line.high,
              low.isFinite, high.isFinite else { return nil }
        let lowText = Self.boundText(low, format: reading.format)
        let highText = Self.boundText(high, format: reading.format)
        if let level = reading.line.intervalLevelText {
            return "\(level) from \(lowText) to \(highText)"
        }
        return "range from \(lowText) to \(highText)"
    }

    /// One bound, spoken. The bounds of a single game's interval are whole counts, so an
    /// integral bound is read as an integer rather than as "nineteen point zero".
    private static func boundText(_ value: Double, format: MetricFormat?) -> String {
        guard value.isFinite else { return Formatting.emDash }
        if let format = format, format.isPercentage {
            return Formatting.value(value, format: format)
        }
        if value.rounded() == value, abs(value) < 1_000_000_000 {
            return Formatting.integer(Int(value))
        }
        return Formatting.decimal(value, places: 1)
    }

    // MARK: Combination line

    /// §5 of the derivation: the residuals are positively dependent, so the correlated spread is
    /// the honest one and the independent spread is shown beside it. The gap is the point.
    @ViewBuilder private var comboBlock: some View {
        if showsCombo, let combo = payload.combo {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                comboValueRow(combo)
                if let spread = comboSpreadText(combo) {
                    Text(spread)
                        .hardwoodText(.caption, monospacedDigits: true)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let gap = comboGapText(combo) {
                    Text(gap)
                        .hardwoodText(.caption, color: Palette.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !payload.method.correlationApplied {
                    cautionLabel("This combined range was built without the residual correlation, so the spread may be understated.")
                }
            }
            .padding(Spacing.sm)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: Radius.control, style: .continuous)
                    .fill(Palette.surfaceSunken)
            )
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(comboAccessibilityText(combo))
        }
    }

    private func comboValueRow(_ combo: ProjectionCombo) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
            Text(combo.label.isEmpty ? "Combined" : combo.label)
                .hardwoodText(.statLabel)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            Spacer(minLength: Spacing.xs)
            Text(Formatting.decimal(combo.mean, places: 1))
                .availabilityStyled(.estimated)
                .hardwoodText(.statValue, color: Palette.textPrimary, monospacedDigits: true)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            AvailabilityBadge(availability: .estimated,
                              showsText: false,
                              isInteractive: false,
                              metricName: combo.label)
            Text(combo.intervalText)
                .hardwoodText(.tableCell, color: Palette.textPrimary, monospacedDigits: true)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
    }

    private func comboSpreadText(_ combo: ProjectionCombo) -> String? {
        guard let sd = combo.sd, sd.isFinite else { return nil }
        let correlated = Formatting.decimal(sd, places: 2)
        guard let independent = combo.sdIfIndependent, independent.isFinite else {
            return "Spread (SD) \(correlated), using the residual correlation."
        }
        return "Spread (SD) \(correlated) with the residual correlation · \(Formatting.decimal(independent, places: 2)) if the parts were independent."
    }

    /// The sentence a reader can act on, rather than two standard deviations side by side.
    private func comboGapText(_ combo: ProjectionCombo) -> String? {
        guard let inflation = resolvedInflation(combo), inflation.isFinite else { return nil }
        guard inflation > 0.0005 else {
            return "Here the correlated spread is no wider than the independent one."
        }
        return "Ignoring how these move together would understate the spread by \(Formatting.percent(inflation, places: 0))."
    }

    private func resolvedInflation(_ combo: ProjectionCombo) -> Double? {
        if let inflation = combo.inflation, inflation.isFinite { return inflation }
        guard let sd = combo.sd, let independent = combo.sdIfIndependent,
              sd.isFinite, independent.isFinite, independent > 1e-9 else { return nil }
        return sd / independent - 1
    }

    private func comboAccessibilityText(_ combo: ProjectionCombo) -> String {
        var parts: [String] = [combo.label.isEmpty ? "Combined line" : combo.label]
        parts.append("projected \(Formatting.decimal(combo.mean, places: 1))")
        if let low = combo.low, let high = combo.high, low.isFinite, high.isFinite {
            parts.append("range from \(Self.boundText(low, format: nil)) to \(Self.boundText(high, format: nil))")
        } else {
            parts.append("no interval given")
        }
        if let spread = comboSpreadText(combo) { parts.append(spread) }
        if let gap = comboGapText(combo) { parts.append(gap) }
        if !payload.method.correlationApplied {
            parts.append("This combined range was built without the residual correlation, so the spread may be understated")
        }
        parts.append("estimated, not a record")
        return parts.joined(separator: ", ")
    }

    // MARK: Why

    /// The design's "Why" section: the context factors turned into the statistic's own units.
    ///
    /// A reader can be told that `f_pace` is 1.021 and still not know whether to care. Half a
    /// point, they know. The arithmetic is the server's (`mean × (factor − 1)`) and the caption
    /// is the honest part: these are the **largest movers**, not a breakdown. The factors
    /// multiply, so the numbers below do not sum to anything, and the caption says as much
    /// rather than inviting a reader to add them up and find the total wrong.
    ///
    /// Headline metric only. Repeating the block for all seven lines would turn an argument into
    /// a spreadsheet, and the first line is the one the widget already leads with.
    @ViewBuilder private var whyBlock: some View {
        if let metric = whyMetric {
            let movers = payload.factors.largestMovers(for: metric.key)
            if !movers.isEmpty {
                VStack(alignment: .leading, spacing: Spacing.xs) {
                    Text("Why \(metric.label)")
                        .hardwoodText(.statLabel)
                    ForEach(movers) { factor in
                        whyRow(factor, metric: metric)
                    }
                    Text("The largest movers, not a breakdown: the factors multiply, so these do not add up to the projection.")
                        .hardwoodText(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    /// `(key, label, format)` for the line the "Why" block explains — the first one shown.
    private var whyMetric: (key: String, label: String, format: MetricFormat?)? {
        guard let line = payload.lines.first else { return nil }
        return (line.metric, line.shortName, line.descriptor?.format)
    }

    private func whyRow(_ factor: ProjectionFactor, metric: (key: String, label: String, format: MetricFormat?)) -> some View {
        let contribution = factor.contributions[metric.key] ?? 0
        let text = factor.contributionText(for: metric.key, format: metric.format) ?? Formatting.emDash
        return HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
            Text(text)
                .hardwoodText(.tableCell,
                              color: Palette.value(for: contribution, higherIsBetter: true),
                              monospacedDigits: true)
                .frame(minWidth: 44, alignment: .trailing)
            Text(factor.explanation ?? factor.label)
                .hardwoodText(.caption)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(text) \(metric.label) from \(factor.label). \(factor.explanation ?? "")")
    }

    // MARK: Context factors

    /// §7 rule 3. Four multiplicative numbers, each against a rule at 1.00, which is what lets a
    /// reader disagree with the projection one factor at a time.
    @ViewBuilder private var factorsBlock: some View {
        if !payload.factors.isEmpty {
            VStack(alignment: .leading, spacing: Spacing.sm) {
                Text("Context factors")
                    .hardwoodText(.statLabel)
                if showsFactorBars {
                    ForEach(payload.factors) { factor in
                        factorRow(factor)
                    }
                    Text("Each factor multiplies the projection; the vertical rule is 1.00, which is no effect either way.")
                        .hardwoodText(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityHidden(true)
                } else {
                    Text(compactFactorText)
                        .hardwoodText(.caption, monospacedDigits: true)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    @ViewBuilder private func factorRow(_ factor: ProjectionFactor) -> some View {
        Group {
            if let value = factor.value, value.isFinite {
                VStack(alignment: .leading, spacing: Spacing.xxs) {
                    FactorBar(value: value,
                              leagueAverage: 1,
                              domain: Self.factorDomain(value),
                              tint: Palette.selection,
                              label: factor.label,
                              valueText: "\(Formatting.decimal(value, places: 3)) · \(factor.effectText)",
                              showsLegend: false,
                              barHeight: 8)
                    if showsDiagnostics, let explanation = factor.explanation, !explanation.isEmpty {
                        Text(explanation)
                            .hardwoodText(.caption)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            } else {
                HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
                    Text(factor.label)
                        .hardwoodText(.statLabel)
                    Spacer(minLength: Spacing.xs)
                    Text(Formatting.emDash)
                        .hardwoodText(.tableCell, color: Palette.textTertiary, monospacedDigits: true)
                }
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(factorAccessibilityText(factor))
    }

    /// A multiplier lives near 1.0, so the track is a tight window around it, widened only when a
    /// real factor falls outside — never so wide that a 2% swing looks like nothing.
    private static func factorDomain(_ value: Double) -> ClosedRange<Double> {
        let low = min(0.90, value - 0.02)
        let high = max(1.10, value + 0.02)
        guard high > low else { return 0.90...1.10 }
        return low...high
    }

    private var compactFactorText: String {
        payload.factors
            .map { "\($0.label) \($0.effectText)" }
            .joined(separator: " · ")
    }

    private func factorAccessibilityText(_ factor: ProjectionFactor) -> String {
        var parts: [String] = [factor.label]
        guard let value = factor.value, value.isFinite else {
            parts.append("not available")
            return parts.joined(separator: ", ")
        }
        parts.append("multiplier \(Formatting.decimal(value, places: 3))")
        if factor.isNeutral {
            parts.append("no effect either way")
        } else {
            let direction = value > 1 ? "raises" : "lowers"
            parts.append("\(direction) the projection by \(Formatting.percent(abs(value - 1), places: 1))")
        }
        if let explanation = factor.explanation, !explanation.isEmpty {
            parts.append(explanation)
        }
        return parts.joined(separator: ", ")
    }

    // MARK: Notes

    @ViewBuilder private var notesBlock: some View {
        if !visibleNotes.isEmpty {
            VStack(alignment: .leading, spacing: Spacing.xxs) {
                ForEach(visibleNotes.indices, id: \.self) { index in
                    HStack(alignment: .top, spacing: Spacing.sm) {
                        Text(verbatim: "\u{2022}")
                            .hardwoodText(.caption, color: Palette.warning)
                        Text(visibleNotes[index])
                            .hardwoodText(.caption)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .accessibilityElement(children: .combine)
                }
            }
        }
    }
}

#if DEBUG
#Preview("Next game projection") {
    ScrollView {
        VStack(spacing: Spacing.md) {
            NextGameProjectionWidget(payload: .preview, size: .medium)
                .hardwoodCard()
            NextGameProjectionWidget(payload: .preview, size: .large)
                .hardwoodCard()
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}

#Preview("Thin history, no scheduled game") {
    // The degrade-honestly case of §7 rule 4: a rookie with 63 minutes of history, a rate shrunk
    // almost entirely to the league prior, a dispersion multiplier of 1.0, and no opponent.
    let payload = NextGameProjectionPayload(
        player: PlayerRef(playerId: 1_642_300,
                          name: "Rookie Guard",
                          firstName: "Rookie",
                          lastName: "Guard",
                          teamId: 1_610_612_747,
                          teamAbbr: "LAL",
                          position: "G",
                          jersey: "0",
                          headshotUrl: nil,
                          isActive: true),
        game: nil,
        projectedMinutes: ProjectedMinutes(value: 11.4,
                                           displayValue: "11.4",
                                           halfLifeGames: 2,
                                           seasonAverage: 9.8,
                                           low: 0,
                                           high: 24.6),
        lines: [
            ProjectedLine(metric: "pts",
                          mean: 4.6, displayValue: "4.6",
                          low: 0, high: 11, intervalLevel: 0.8,
                          seasonAverage: 4.1, delta: 0.5,
                          ratePerMinute: 0.404, shrinkageK: 81, exposureMinutes: 63,
                          shrinkageWeight: 0.44, halfLifeGames: 6,
                          dispersionAlpha: 0.061, dispersionMultiplier: 1.0,
                          availability: .estimated),
            ProjectedLine(metric: "reb",
                          mean: 1.9, displayValue: "1.9",
                          low: 0, high: 5, intervalLevel: 0.8,
                          seasonAverage: 1.8, delta: 0.1,
                          ratePerMinute: 0.167, shrinkageK: 34, exposureMinutes: 63,
                          shrinkageWeight: 0.65, halfLifeGames: 8,
                          dispersionAlpha: 0.094, dispersionMultiplier: 1.0,
                          availability: .estimated),
            ProjectedLine(metric: "stl",
                          mean: 0.4, displayValue: "0.4",
                          seasonAverage: 0.3, delta: 0.1,
                          ratePerMinute: 0.031, shrinkageK: 322, exposureMinutes: 63,
                          shrinkageWeight: 0.16, halfLifeGames: 30,
                          dispersionAlpha: 0.22, dispersionMultiplier: 1.0,
                          availability: .estimated)
        ],
        factors: [
            ProjectionFactor(key: "pace", label: "Pace", value: 1.0,
                             explanation: "No opponent, so the league-average tempo is assumed."),
            ProjectionFactor(key: "opponent", label: "Opponent", value: 1.0,
                             explanation: "Projected against a league-average defence."),
            ProjectionFactor(key: "home", label: "Venue", value: 1.0,
                             explanation: "Neutral venue."),
            ProjectionFactor(key: "rest", label: "Rest", value: nil, explanation: nil)
        ],
        combo: nil,
        method: ProjectionMethod(summary: "Opportunity x rate, shrunk per statistic.",
                                 minutesHalfLifeGames: 2,
                                 correlationApplied: false,
                                 dispersionShrinkageGames: 60),
        notes: [
            "This player has 63 minutes of history. The rates are mostly the league prior, and the intervals are wide because of it.",
            "Every number here is an estimate, not a record."
        ]
    )
    return ScrollView {
        NextGameProjectionWidget(payload: payload, size: .large)
            .hardwoodCard()
            .padding(Spacing.lg)
    }
    .hardwoodBackground()
}

#Preview("Accessibility size") {
    ScrollView {
        NextGameProjectionWidget(payload: .preview, size: .large)
            .hardwoodCard()
            .padding(Spacing.lg)
    }
    .hardwoodBackground()
    .environment(\.dynamicTypeSize, .accessibility2)
}
#endif
