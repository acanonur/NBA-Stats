import SwiftUI

// Where a status, an absence or a headline came from: its kind, its label (a link when there is
// one), and how long ago the SOURCE published it.
//
// THE PROVENANCE RULES (MAC_DESIGN 3.2)
// - The age is computed from `source.publishedAt`, the moment the source said it, never from
//   `fetchedAt`, the moment Hardwood read it. A fresh fetch of a two-week-old statement must not
//   make the statement look new. The exact local date and time is in the tooltip.
// - The label is a link that opens the default browser when the server sent a usable web address,
//   and plain text otherwise. That includes a label the server wrote for a withheld link (its own
//   words, shown as they arrive).
// - Only web addresses are ever opened: `http` and `https`. A source whose address is anything
//   else is shown as text, because the address came from a feed or from something the reader typed.
// - Hardwood never fetches an article. Only the title, link, date and source name are shown
//   (`LeagueNewsRow` is the one place a headline is drawn).

/// Turns text from a payload into an address that is safe to open in the browser.
enum LeagueWebLink {

    /// The address when it is a complete `http` or `https` URL with a host; otherwise nil.
    static func url(_ text: String?) -> URL? {
        guard let raw = text else { return nil }
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { return nil }
        guard let components = URLComponents(string: trimmed),
              let scheme = components.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              let host = components.host,
              !host.isEmpty,
              let url = components.url else {
            return nil
        }
        return url
    }
}

/// One source: kind chip, label, age, and (optionally) a Stale chip.
///
/// `compact` draws the label and age on one short line of plain text, for a dashboard tile where a
/// link would fight the tile's own gestures. `full` draws the kind chip, the label as a link when it
/// can be one, the age, and the Stale chip.
struct LeagueSourceLine: View {
    private let source: LeagueSource?
    private let density: LeagueDensity
    private let isStale: Bool

    init(source: LeagueSource?, density: LeagueDensity = .full, isStale: Bool = false) {
        self.source = source
        self.density = density
        self.isStale = isStale
    }

    private var labelText: String {
        let label = source?.label ?? ""
        return label.isEmpty ? "Unnamed source" : label
    }

    private var ageText: String {
        let published = source?.publishedAt ?? ""
        if published.isEmpty { return "date not given" }
        return LeagueFormatting.age(published)
    }

    private var absoluteText: String {
        LeagueFormatting.absoluteLocal(source?.publishedAt)
    }

    var body: some View {
        if source == nil {
            Text("Source not recorded")
                .hardwoodText(.caption)
        } else if density == .compact {
            compactLine
        } else {
            fullLine
        }
    }

    private var compactLine: some View {
        HStack(spacing: Spacing.xs) {
            Text(labelText + " · " + ageText)
                .hardwoodText(.caption)
                .lineLimit(1)
                .help(absoluteText)
            if isStale {
                LeagueChip(text: "Stale", tint: Palette.warning)
            }
        }
    }

    private var fullLine: some View {
        HStack(spacing: Spacing.xs) {
            SourceKindChip(kind: source?.kind)
            labelView
            Text("·")
                .hardwoodText(.caption)
                .accessibilityHidden(true)
            Text(ageText)
                .hardwoodText(.caption)
                .lineLimit(1)
                .help(absoluteText)
            if isStale {
                LeagueChip(text: "Stale", tint: Palette.warning)
            }
            Spacer(minLength: 0)
        }
    }

    @ViewBuilder private var labelView: some View {
        if let url = LeagueWebLink.url(source?.url) {
            Link(labelText, destination: url)
                .font(Typography.tableCell)
                .lineLimit(1)
                .help(url.absoluteString)
        } else {
            Text(labelText)
                .hardwoodText(.tableCell)
                .lineLimit(1)
        }
    }
}

// MARK: - Headlines

/// One headline: its title (a link on a screen), the source's name, how long ago the source
/// published it, and the teams it names. Nothing else: Hardwood does not read the article.
///
/// `compact` is a tile's version: the title in plain text (at most two lines) and the source and age
/// under it. `full` makes the title a link when the server sent a usable web address, and adds a
/// small chip for each team the headline names.
struct LeagueNewsRow: View {
    private let item: LeagueNewsLink
    private let density: LeagueDensity

    init(item: LeagueNewsLink, density: LeagueDensity = .full) {
        self.item = item
        self.density = density
    }

    private var titleText: String {
        let title = item.title ?? ""
        return title.isEmpty ? "Untitled headline" : title
    }

    private var titleLines: Int? {
        density == .compact ? 2 : nil
    }

    private var sourceText: String {
        let name = item.sourceName ?? ""
        let age = LeagueFormatting.age(item.publishedAt)
        if name.isEmpty { return age }
        return name + " · " + age
    }

    private var teams: [LeagueTeamRef] {
        density == .full ? (item.teams ?? []) : []
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Spacing.xxs) {
            titleView
            HStack(spacing: Spacing.xs) {
                Text(sourceText)
                    .hardwoodText(.caption)
                    .lineLimit(1)
                    .help(LeagueFormatting.absoluteLocal(item.publishedAt))
                ForEach(Array(teams.enumerated()), id: \.offset) { pair in
                    LeagueChip(text: pair.element.displayAbbr, tint: Palette.textSecondary)
                }
                Spacer(minLength: 0)
            }
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private var titleView: some View {
        if density == .full, let url = LeagueWebLink.url(item.link) {
            Link(titleText, destination: url)
                .font(Typography.tableCell)
                .fixedSize(horizontal: false, vertical: true)
                .help(url.absoluteString)
        } else {
            Text(titleText)
                .hardwoodText(.tableCell)
                .lineLimit(titleLines)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
