import SwiftUI

// The strips that sit above and below a league screen's content: the freshness bar (is this data
// real, how old is it, which sources stand behind it), the report-state banner, the withheld
// banner, and the footers that print the server's own notes, attribution and caveat.
//
// WHY THE SERVER'S WORDS ARE PRINTED AS THEY ARRIVE
// Every league payload carries `notes`, an `attribution`, a `caveat`, and for an injury report a
// `state` with a `message`. Those sentences are the backend's honest account of what the numbers
// are and are not ("EuroLeague games only", "Counts points scored by opposing players listed at
// each position. It does not measure who guarded whom."). The app shows them verbatim and never
// paraphrases, so the text a reader sees is the text the contract's tests pinned.
//
// A banner that has nothing to say draws nothing at all (not an empty padded box), so a screen
// whose data is fresh and real has no chrome above its content.

// MARK: - The strip

/// One coloured sentence with a symbol. The building block of the banners below.
struct LeagueStrip: View {
    private let symbol: String
    private let text: String
    private let tint: Color

    init(symbol: String, text: String, tint: Color) {
        self.symbol = symbol
        self.text = text
        self.tint = tint
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
            Image(systemName: symbol)
                .foregroundStyle(tint)
                .accessibilityHidden(true)
            Text(text)
                .hardwoodText(.caption, color: Palette.textPrimary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, Spacing.md)
        .padding(.vertical, Spacing.sm)
        .background(RoundedRectangle(cornerRadius: Radius.control, style: .continuous)
            .fill(tint.opacity(0.12)))
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Freshness

/// Whether the data is invented, how current it is, and the state of every source behind it.
///
/// - A yellow strip says so when the league is the server's invented demo league
///   (`freshness.isDemo`) or when the app itself is in demo mode (`isBundledDemo`). Neither is ever
///   mistaken for real games.
/// - A line then says "Data through ..." (the league's own day) and "Updated ..." (how long ago the
///   server built the payload), with the exact local time in the tooltip.
/// - One chip per source in `freshness.sources`, coloured by its state, with its reason in the
///   tooltip.
///
/// With no freshness block and no demo flag this view draws nothing.
struct FreshnessBar: View {
    private let freshness: LeagueFreshness?
    private let isBundledDemo: Bool

    init(freshness: LeagueFreshness?, isBundledDemo: Bool = false) {
        self.freshness = freshness
        self.isBundledDemo = isBundledDemo
    }

    private var demoText: String? {
        if isBundledDemo { return "Bundled demo data" }
        if freshness?.isDemo == true { return "Invented demo league — not real games" }
        return nil
    }

    private var statusText: String {
        guard let freshness = freshness else { return "" }
        var parts: [String] = []
        if let through = freshness.dataThrough, !through.isEmpty {
            parts.append("Data through " + LeagueFormatting.leagueDate(through))
        }
        if let generated = freshness.generatedAt, !generated.isEmpty {
            parts.append("Updated " + LeagueFormatting.age(generated))
        }
        return parts.joined(separator: " · ")
    }

    private var sources: [LeagueSourceState] {
        freshness?.sources ?? []
    }

    private var hasContent: Bool {
        demoText != nil || !statusText.isEmpty || !sources.isEmpty
    }

    var body: some View {
        if hasContent {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                if let demo = demoText {
                    LeagueStrip(symbol: "exclamationmark.triangle.fill", text: demo, tint: Palette.warning)
                }
                statusRow
            }
            .padding(.horizontal, Spacing.md)
            .padding(.vertical, Spacing.xs)
        }
    }

    @ViewBuilder private var statusRow: some View {
        if !statusText.isEmpty || !sources.isEmpty {
            HStack(spacing: Spacing.sm) {
                if !statusText.isEmpty {
                    Text(statusText)
                        .hardwoodText(.caption)
                        .lineLimit(1)
                        .help(LeagueFormatting.absoluteLocal(freshness?.generatedAt))
                }
                if !sources.isEmpty {
                    sourceChips
                }
                Spacer(minLength: 0)
            }
        }
    }

    private var sourceChips: some View {
        ScrollView(.horizontal, showsIndicators: PlatformMetrics.showsHorizontalIndicators) {
            HStack(spacing: Spacing.xs) {
                ForEach(Array(sources.enumerated()), id: \.offset) { pair in
                    SourceStateChip(source: pair.element)
                }
            }
        }
    }
}

/// One source of the freshness bar: a dot coloured by its state, its label, and (when it is not
/// simply fine) the state in words. The server's reason is the tooltip.
struct SourceStateChip: View {
    private let source: LeagueSourceState

    init(source: LeagueSourceState) {
        self.source = source
    }

    private var tint: Color {
        switch source.state ?? "" {
        case "ok":
            return Palette.positive
        case "stale":
            return Palette.warning
        case "error", "blocked", "unreadable":
            return Palette.negative
        case "disabled", "notConfigured":
            return Palette.textTertiary
        default:
            return Palette.neutral
        }
    }

    private var text: String {
        let name = source.label ?? source.key ?? Formatting.emDash
        let state = source.state ?? ""
        if state.isEmpty || state == "ok" { return name }
        return name + ": " + LeagueFormatting.stateWord(state)
    }

    var body: some View {
        HStack(spacing: Spacing.xs) {
            Circle()
                .fill(tint)
                .frame(width: 7, height: 7)
                .accessibilityHidden(true)
            Text(text)
                .hardwoodText(.caption, color: Palette.textSecondary)
                .lineLimit(1)
        }
        .padding(.horizontal, Spacing.sm)
        .padding(.vertical, Spacing.xxs)
        .background(Capsule(style: .continuous).fill(Palette.surfaceSunken))
        .help(source.reason ?? "")
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Report state

/// The injury report's own state, when it is not fresh: stale, no report yet, unreadable, off, or
/// a value this build does not know. The server's message is printed after the state word. A
/// `fresh` report, or one with no state and no message, draws nothing.
struct LeagueStateBanner: View {
    private let state: String?
    private let message: String?

    init(state: String?, message: String?) {
        self.state = state
        self.message = message
    }

    private var isQuiet: Bool {
        if state == "fresh" { return true }
        return (state ?? "").isEmpty && (message ?? "").isEmpty
    }

    private var text: String {
        let words = (state ?? "").isEmpty ? "" : LeagueFormatting.stateWord(state)
        let detail = message ?? ""
        if words.isEmpty { return detail }
        if detail.isEmpty { return words }
        return words + " — " + detail
    }

    var body: some View {
        if !isQuiet {
            LeagueStrip(symbol: "info.circle", text: text, tint: Palette.neutral)
        }
    }
}

// MARK: - Withheld

/// Why a defence breakdown, or one team's row of it, is withheld, in the server's own words. Draws
/// nothing when nothing is withheld.
struct WithheldBanner: View {
    private let withheld: LeagueWithheld?

    init(withheld: LeagueWithheld?) {
        self.withheld = withheld
    }

    var body: some View {
        if let withheld = withheld {
            LeagueStrip(symbol: "exclamationmark.triangle",
                        text: "Withheld: " + (withheld.message ?? LeagueFormatting.withheldReasonWord(withheld.reason)),
                        tint: Palette.warning)
        }
    }
}

// MARK: - Footers

/// The server's notes, one per paragraph, and the attribution line, printed as they arrive. Draws
/// nothing when there are none.
struct NotesFooter: View {
    private let notes: [String]
    private let attribution: String?

    init(notes: [String]?, attribution: String? = nil) {
        self.notes = (notes ?? []).filter { !$0.isEmpty }
        self.attribution = attribution
    }

    private var attributionText: String {
        attribution ?? ""
    }

    var body: some View {
        if !notes.isEmpty || !attributionText.isEmpty {
            VStack(alignment: .leading, spacing: Spacing.xs) {
                Divider()
                ForEach(Array(notes.enumerated()), id: \.offset) { pair in
                    Text(pair.element)
                        .hardwoodText(.caption)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !attributionText.isEmpty {
                    Text(attributionText)
                        .hardwoodText(.caption)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.horizontal, Spacing.md)
            .padding(.vertical, Spacing.sm)
            .textSelection(.enabled)
        }
    }
}

/// The caveat a defence breakdown must always show: what the numbers count and what they do not.
/// `extra` is an additional sentence (the NBA's three-position note).
struct CaveatFooter: View {
    private let text: String
    private let extra: String

    init(text: String?, extra: String? = nil) {
        self.text = text ?? ""
        self.extra = extra ?? ""
    }

    var body: some View {
        if !text.isEmpty || !extra.isEmpty {
            HStack(alignment: .firstTextBaseline, spacing: Spacing.sm) {
                Image(systemName: "info.circle")
                    .foregroundStyle(Palette.textTertiary)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: Spacing.xxs) {
                    if !text.isEmpty {
                        Text(text)
                            .hardwoodText(.caption)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if !extra.isEmpty {
                        Text(extra)
                            .hardwoodText(.caption)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Spacer(minLength: 0)
            }
            .textSelection(.enabled)
        }
    }
}

#if DEBUG
#Preview("League banners") {
    VStack(alignment: .leading, spacing: Spacing.sm) {
        FreshnessBar(freshness: LeagueMatchup.preview.freshness)
        LeagueStateBanner(state: "noReportYet", message: "No injury report has been published for this slate.")
        WithheldBanner(withheld: LeagueWithheld(reason: "minimumGames", message: "Only 4 games, too few to judge a defence"))
        CaveatFooter(text: "Counts points scored by opposing players listed at each position.")
        NotesFooter(notes: ["EuroLeague games only."], attribution: "Statistics via the league's data service.")
    }
    .padding(Spacing.lg)
    .hardwoodBackground()
}
#endif
