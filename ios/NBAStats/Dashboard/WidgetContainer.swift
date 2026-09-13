import Foundation
import SwiftUI

/// The chrome around every tile on the dashboard.
///
/// It owns the title, the era badge, the staleness dot, the overflow menu and — in edit mode — the
/// delete affordance and the drag handle. The widget's own content is `WidgetHost`, which is what
/// decides how a payload, a failure or an era gap actually renders.
public struct WidgetContainer: View {

    /// Everything the chrome needs to know about a widget's state, read once so this file holds the
    /// module's only `switch` over `WidgetState`.
    private struct ChromeFacts {
        var availability: MetricAvailability = .full
        var notes: [String] = []
        var isStale = false
        var isLoading = false
        var failureMessage: String? = nil

        init(state: WidgetState) {
            switch state {
            case .loading:
                isLoading = true
            case .loaded(_, let resolvedAvailability, let resolvedNotes, let stale):
                availability = resolvedAvailability
                notes = resolvedNotes
                isStale = stale
            case .failed(let error):
                failureMessage = error.userMessage
                // A tile that could not refresh is showing yesterday's number at best.
                isStale = true
            case .unavailable(let reason):
                availability = .unavailable
                notes = [reason]
            }
        }
    }

    private let widget: DashboardWidget
    private let state: WidgetState
    private let catalog: Catalog
    private let accent: AccentName
    private let isEditing: Bool
    private let isDragging: Bool
    private let isDropTarget: Bool
    private let dragHandle: AnyView?
    private let onConfigure: () -> Void
    private let onResize: (WidgetSize) -> Void
    private let onDuplicate: () -> Void
    private let onRemove: () -> Void

    @Environment(\.displayScale) private var displayScale

    public init(widget: DashboardWidget,
                state: WidgetState,
                catalog: Catalog,
                accent: AccentName,
                isEditing: Bool,
                isDragging: Bool = false,
                isDropTarget: Bool = false,
                dragHandle: AnyView? = nil,
                onConfigure: @escaping () -> Void,
                onResize: @escaping (WidgetSize) -> Void,
                onDuplicate: @escaping () -> Void,
                onRemove: @escaping () -> Void) {
        self.widget = widget
        self.state = state
        self.catalog = catalog
        self.accent = accent
        self.isEditing = isEditing
        self.isDragging = isDragging
        self.isDropTarget = isDropTarget
        self.dragHandle = dragHandle
        self.onConfigure = onConfigure
        self.onResize = onResize
        self.onDuplicate = onDuplicate
        self.onRemove = onRemove
    }

    // MARK: Derived

    private var facts: ChromeFacts { ChromeFacts(state: state) }

    private var title: String {
        if let custom = widget.title, !custom.isEmpty { return custom }
        return catalog.defaultTitle(for: widget.kind)
    }

    private var sizes: [WidgetSize] {
        let available = catalog.widget(widget.kind)?.sizes ?? WidgetSize.allCases
        return available.isEmpty ? WidgetSize.allCases : available
    }

    private var season: String? {
        widget.configString("season")
    }

    private var accessibilitySummary: String {
        let chrome = facts
        var parts: [String] = [title]
        if chrome.isLoading { parts.append("loading") }
        if let failure = chrome.failureMessage { parts.append(failure) }
        if chrome.availability != .full { parts.append(chrome.availability.hardwoodShortLabel) }
        if chrome.isStale { parts.append("showing cached data") }
        return parts.joined(separator: ", ")
    }

    private var shape: RoundedRectangle {
        RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
    }

    // MARK: Body

    public var body: some View {
        VStack(alignment: .leading, spacing: Spacing.sm) {
            header
            WidgetHost(widget: widget, state: state, isEditing: isEditing)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(Spacing.md)
        .frame(maxWidth: .infinity, minHeight: widget.size.estimatedHeight, alignment: .topLeading)
        .background(shape.fill(Palette.surface))
        .overlay { border }
        .clipShape(shape)
        .overlay(alignment: .topLeading) { deleteAffordance }
        .shadow(color: isDragging ? Color.black.opacity(0.18) : Color.clear,
                radius: isDragging ? 16 : 0,
                x: 0,
                y: isDragging ? 8 : 0)
        .scaleEffect(isDragging ? 1.03 : 1)
        .opacity(isDragging ? 0.95 : 1)
        .contentShape(Rectangle())
        .onTapGesture {
            // Outside edit mode a tap belongs to the widget's own content.
            if isEditing { onConfigure() }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(accessibilitySummary)
    }

    private var border: some View {
        shape.strokeBorder(
            isDropTarget ? accent.color : Palette.separator,
            style: StrokeStyle(lineWidth: isDropTarget ? 2 : 1 / max(displayScale, 1),
                               dash: isEditing && !isDropTarget ? [4, 3] : [])
        )
    }

    private var header: some View {
        HStack(spacing: Spacing.xs) {
            Text(title)
                .hardwoodText(.widgetTitle)
                .lineLimit(1)
                .accessibilityHidden(true)
            AvailabilityBadge(availability: facts.availability,
                              showsText: true,
                              isInteractive: true,
                              metricName: title,
                              season: season,
                              notes: facts.notes)
            Spacer(minLength: Spacing.xs)
            if facts.isStale {
                StalenessDot(isStale: true)
            }
            if isEditing, let dragHandle = dragHandle {
                dragHandle
            }
            overflowMenu
        }
    }

    private var overflowMenu: some View {
        Menu {
            Button {
                onConfigure()
            } label: {
                Label("Configure", systemImage: "slider.horizontal.3")
            }
            Menu {
                ForEach(sizes, id: \.self) { option in
                    Button {
                        onResize(option)
                    } label: {
                        if option == widget.size {
                            Label(option.displayName, systemImage: "checkmark")
                        } else {
                            Text(option.displayName)
                        }
                    }
                }
            } label: {
                Label("Resize", systemImage: "arrow.up.left.and.arrow.down.right")
            }
            Button {
                onDuplicate()
            } label: {
                Label("Duplicate", systemImage: "plus.square.on.square")
            }
            Divider()
            Button(role: .destructive) {
                onRemove()
            } label: {
                Label("Remove", systemImage: "trash")
            }
        } label: {
            Image(systemName: "ellipsis.circle")
                .imageScale(.medium)
                .foregroundStyle(Palette.textTertiary)
                .frame(width: 28, height: 28)
                .contentShape(Rectangle())
        }
        .accessibilityLabel("\(title) options")
    }

    @ViewBuilder private var deleteAffordance: some View {
        if isEditing {
            Button {
                onRemove()
            } label: {
                Image(systemName: "minus.circle.fill")
                    .symbolRenderingMode(.palette)
                    .foregroundStyle(Palette.surface, Palette.negative)
                    .font(.title3)
            }
            .buttonStyle(.plain)
            .offset(x: -6, y: -6)
            .accessibilityLabel("Remove \(title)")
        }
    }
}

/// The grab handle the grid hands to the container in edit mode.
///
/// It carries no gesture of its own: `DashboardGrid` attaches one, because only the grid knows
/// where the other tiles are.
public struct WidgetDragHandle: View {
    private let isActive: Bool

    public init(isActive: Bool = false) {
        self.isActive = isActive
    }

    public var body: some View {
        Image(systemName: "line.3.horizontal")
            .imageScale(.small)
            .foregroundStyle(isActive ? Palette.selection : Palette.textTertiary)
            .frame(width: 30, height: 30)
            .background(
                Circle().fill(isActive ? Palette.selection.opacity(0.14) : Palette.surfaceSunken)
            )
            .contentShape(Circle())
            .accessibilityLabel("Drag to reorder")
    }
}

#if DEBUG
#Preview("Widget chrome") {
    let catalog = DashboardPreviewData.catalog
    let widget = DashboardWidget(kind: .statTile,
                                 title: "True Shooting",
                                 size: .small,
                                 config: ["metric": .string("ts_pct"), "season": .string("1971-72")])
    return ScrollView {
        VStack(spacing: Spacing.md) {
            WidgetContainer(widget: widget,
                            state: .loading,
                            catalog: catalog,
                            accent: .orange,
                            isEditing: false,
                            onConfigure: { },
                            onResize: { _ in },
                            onDuplicate: { },
                            onRemove: { })
            WidgetContainer(widget: widget,
                            state: .unavailable(reason: "Usage rate needs individual turnovers, which were not recorded until 1977-78."),
                            catalog: catalog,
                            accent: .orange,
                            isEditing: true,
                            dragHandle: AnyView(WidgetDragHandle()),
                            onConfigure: { },
                            onResize: { _ in },
                            onDuplicate: { },
                            onRemove: { })
        }
        .padding(Spacing.lg)
    }
    .hardwoodBackground()
}
#endif
