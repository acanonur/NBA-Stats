import Foundation
import SwiftUI

// MARK: - Shared tile chrome

/// The surface every state tile draws on. It honours the widget's size so a placeholder never
/// changes the height of the grid row it is standing in for.
private struct TileSurface<Content: View>: View {
    private let size: WidgetSize
    private let content: Content

    init(size: WidgetSize, @ViewBuilder content: () -> Content) {
        self.size = size
        self.content = content()
    }

    private var shape: RoundedRectangle {
        RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
    }

    var body: some View {
        content
            .padding(Spacing.md)
            .frame(maxWidth: .infinity, minHeight: size.estimatedHeight)
            .background(shape.fill(Palette.surface))
            .overlay(shape.strokeBorder(Palette.separator, lineWidth: 1))
            .clipShape(shape)
    }
}

// MARK: - Loading

/// One placeholder bar inside a loading tile, with a highlight that sweeps across it.
private struct SkeletonBar: View {
    let width: CGFloat?
    let height: CGFloat
    let phase: CGFloat

    private var shape: RoundedRectangle {
        RoundedRectangle(cornerRadius: min(height / 2, Radius.chip), style: .continuous)
    }

    var body: some View {
        shape
            .fill(Palette.skeleton)
            .frame(width: width, height: height)
            .overlay {
                GeometryReader { proxy in
                    let span = max(proxy.size.width, 1)
                    LinearGradient(gradient: Gradient(colors: [Color.clear,
                                                               Palette.skeletonHighlight,
                                                               Color.clear]),
                                   startPoint: .leading,
                                   endPoint: .trailing)
                        .frame(width: span * 0.55)
                        .offset(x: phase * span * 1.6)
                }
            }
            .clipShape(shape)
            .accessibilityHidden(true)
    }
}

/// The placeholder a widget shows while its payload is resolving.
public struct LoadingTile: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var phase: CGFloat = -0.8

    private let size: WidgetSize

    public init(size: WidgetSize) {
        self.size = size
    }

    public var body: some View {
        TileSurface(size: size) {
            skeleton
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Loading")
        .accessibilityAddTraits(.updatesFrequently)
        .onAppear {
            // A sweeping highlight is motion; readers who have asked for less of it get a
            // static placeholder instead.
            guard !reduceMotion else { return }
            withAnimation(.linear(duration: 1.4).repeatForever(autoreverses: false)) {
                phase = 1.0
            }
        }
    }

    @ViewBuilder private var skeleton: some View {
        switch size {
        case .small:
            VStack(alignment: .leading, spacing: Spacing.sm) {
                SkeletonBar(width: 62, height: 10, phase: phase)
                SkeletonBar(width: 104, height: 26, phase: phase)
                Spacer(minLength: 0)
                SkeletonBar(width: 84, height: 10, phase: phase)
            }
        case .medium:
            VStack(alignment: .leading, spacing: Spacing.sm) {
                SkeletonBar(width: 96, height: 12, phase: phase)
                SkeletonBar(width: 148, height: 30, phase: phase)
                SkeletonBar(width: nil, height: 10, phase: phase)
                SkeletonBar(width: nil, height: 10, phase: phase)
                Spacer(minLength: 0)
                SkeletonBar(width: 120, height: 10, phase: phase)
            }
        case .large:
            VStack(alignment: .leading, spacing: Spacing.sm) {
                SkeletonBar(width: 120, height: 12, phase: phase)
                SkeletonBar(width: 180, height: 30, phase: phase)
                ForEach(0..<5) { _ in
                    HStack(spacing: Spacing.sm) {
                        SkeletonBar(width: 26, height: 12, phase: phase)
                        SkeletonBar(width: nil, height: 12, phase: phase)
                        SkeletonBar(width: 44, height: 12, phase: phase)
                    }
                }
                Spacer(minLength: 0)
            }
        }
    }
}

// MARK: - Error

/// A single failed widget, contained inside its own tile so the rest of the dashboard is
/// untouched.
public struct ErrorTile: View {
    private let message: String
    private let isRetryable: Bool
    private let size: WidgetSize
    private let retry: () -> Void

    public init(message: String,
                isRetryable: Bool = true,
                size: WidgetSize = .small,
                retry: @escaping () -> Void) {
        self.message = message
        self.isRetryable = isRetryable
        self.size = size
        self.retry = retry
    }

    public var body: some View {
        TileSurface(size: size) {
            VStack(spacing: Spacing.sm) {
                Image(systemName: "exclamationmark.triangle")
                    .font(.title3)
                    .foregroundStyle(Palette.warning)
                    .accessibilityHidden(true)
                Text(message)
                    .hardwoodText(.tableCell, color: Palette.textSecondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                if isRetryable {
                    Button("Try again", action: retry)
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                        .tint(Palette.selection)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Could not load this widget. \(message)")
    }
}

// MARK: - Empty

/// A widget that resolved successfully but has nothing to show — no games on the slate, no
/// qualified players for the filter.
public struct EmptyTile: View {
    private let icon: String
    private let message: String
    private let size: WidgetSize

    public init(icon: String, message: String, size: WidgetSize = .small) {
        self.icon = icon
        self.message = message
        self.size = size
    }

    public var body: some View {
        TileSurface(size: size) {
            VStack(spacing: Spacing.sm) {
                Image(systemName: icon)
                    .font(.title3)
                    .foregroundStyle(Palette.textTertiary)
                    .accessibilityHidden(true)
                Text(message)
                    .hardwoodText(.tableCell, color: Palette.textSecondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(message)
    }
}

/// A widget whose metric simply did not exist for the requested era.
///
/// This tile is the whole reason for the em dash rule: rather than a zero, the reader gets a
/// dash and a tap target that explains the gap in the league's record.
public struct UnavailableTile: View {
    @State private var isExplaining = false

    private let reason: String
    private let metricName: String?
    private let season: String?
    private let size: WidgetSize

    public init(reason: String,
                metricName: String? = nil,
                season: String? = nil,
                size: WidgetSize = .small) {
        self.reason = reason
        self.metricName = metricName
        self.season = season
        self.size = size
    }

    public var body: some View {
        TileSurface(size: size) {
            VStack(spacing: Spacing.sm) {
                Text(verbatim: HardwoodNumberFormat.missing)
                    .font(Typography.displayValue)
                    .foregroundStyle(Palette.textTertiary)
                    .accessibilityHidden(true)
                Text(reason)
                    .hardwoodText(.tableCell, color: Palette.textSecondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                Button("Why?") { isExplaining = true }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .tint(Palette.selection)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .availabilityExplainer(isPresented: $isExplaining,
                               availability: .unavailable,
                               metricName: metricName,
                               season: season,
                               notes: [reason])
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Not available. \(reason)")
    }
}

// MARK: - Empty dashboard

/// The full-screen state for a layout with no widgets in it.
public struct EmptyDashboardView: View {
    private let addWidget: () -> Void
    private let browsePresets: () -> Void

    public init(addWidget: @escaping () -> Void, browsePresets: @escaping () -> Void) {
        self.addWidget = addWidget
        self.browsePresets = browsePresets
    }

    public var body: some View {
        VStack(spacing: Spacing.lg) {
            Image(systemName: "square.grid.2x2")
                .font(.system(size: 44, weight: .light))
                .foregroundStyle(Palette.textTertiary)
                .accessibilityHidden(true)
            VStack(spacing: Spacing.sm) {
                Text("Nothing on the board yet")
                    .hardwoodText(.sectionTitle)
                    .multilineTextAlignment(.center)
                Text("Add a widget to track a player, a team or last night's slate — or start from a preset and edit it however you like.")
                    .hardwoodText(.tableCell, color: Palette.textSecondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
            VStack(spacing: Spacing.sm) {
                Button("Add a widget", action: addWidget)
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .tint(Palette.selection)
                Button("Browse presets", action: browsePresets)
                    .buttonStyle(.bordered)
                    .controlSize(.large)
                    .tint(Palette.selection)
            }
        }
        .padding(Spacing.xl)
        .frame(maxWidth: 420)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .hardwoodBackground()
        .accessibilityElement(children: .contain)
    }
}

#if DEBUG
#Preview("Loading") {
    ScrollView {
        VStack(spacing: Spacing.md) {
            ForEach(WidgetSize.allCases, id: \.self) { size in
                LoadingTile(size: size)
            }
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}

#Preview("Error, empty, unavailable") {
    ScrollView {
        VStack(spacing: Spacing.md) {
            ErrorTile(message: "The ingest source is unreachable. Cached data may be stale.",
                      isRetryable: true,
                      size: .medium) { }
            ErrorTile(message: "No player with that id.", isRetryable: false, size: .small) { }
            EmptyTile(icon: "calendar",
                      message: "No games on this date.",
                      size: .small)
            UnavailableTile(reason: "Steals were not recorded before the 1973-74 season.",
                            metricName: "Steals",
                            season: "1965-66",
                            size: .medium)
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}

#Preview("Empty dashboard") {
    EmptyDashboardView(addWidget: { }, browsePresets: { })
}
#endif
