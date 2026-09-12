import Foundation
import SwiftUI

// MARK: - Column span

/// How many grid columns a tile occupies. Read by `WidgetFlowLayout` from each subview.
private struct WidgetColumnSpanKey: LayoutValueKey {
    static let defaultValue: Int = 1
}

extension View {
    /// Declares how many columns this tile spans inside `WidgetFlowLayout`.
    func widgetColumnSpan(_ span: Int) -> some View {
        layoutValue(key: WidgetColumnSpanKey.self, value: span)
    }
}

// MARK: - Flow layout

/// The dashboard's flowing grid.
///
/// `LazyVGrid` cannot span cells — `gridCellColumns(_:)` only applies inside `Grid`, and `Grid` is
/// not a flowing container — so the dashboard uses a custom `Layout` instead. It walks the widgets
/// in order, packing each one into the current row while it fits and starting a new row when it
/// does not: small tiles pair up, medium and large tiles take the full width they ask for, and the
/// reading order is exactly the order stored in the layout document.
struct WidgetFlowLayout: Layout {

    var columns: Int
    var spacing: CGFloat

    private struct Item {
        let index: Int
        let x: CGFloat
        let width: CGFloat
    }

    private struct Row {
        var items: [Item] = []
        var height: CGFloat = 0
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = resolvedWidth(proposal)
        let rows = layoutRows(subviews: subviews, width: width)
        guard !rows.isEmpty else { return CGSize(width: width, height: 0) }
        let contentHeight = rows.reduce(CGFloat(0)) { $0 + $1.height }
        let gaps = spacing * CGFloat(rows.count - 1)
        return CGSize(width: width, height: contentHeight + gaps)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let rows = layoutRows(subviews: subviews, width: bounds.width)
        var y = bounds.minY
        for row in rows {
            for item in row.items {
                subviews[item.index].place(
                    at: CGPoint(x: bounds.minX + item.x, y: y),
                    anchor: .topLeading,
                    proposal: ProposedViewSize(width: item.width, height: row.height)
                )
            }
            y += row.height + spacing
        }
    }

    /// A proposal can be unspecified or infinite; neither can be divided into columns, so a sane
    /// phone width stands in rather than producing a NaN.
    private func resolvedWidth(_ proposal: ProposedViewSize) -> CGFloat {
        let raw = proposal.replacingUnspecifiedDimensions().width
        return raw.isFinite && raw > 0 ? raw : 320
    }

    private func layoutRows(subviews: Subviews, width: CGFloat) -> [Row] {
        let columnCount = max(columns, 1)
        let totalSpacing = spacing * CGFloat(columnCount - 1)
        let columnWidth = max((width - totalSpacing) / CGFloat(columnCount), 1)

        var rows: [Row] = []
        var current = Row()
        var usedColumns = 0

        for index in subviews.indices {
            let requested = min(max(subviews[index][WidgetColumnSpanKey.self], 1), columnCount)
            if usedColumns > 0 && usedColumns + requested > columnCount {
                rows.append(current)
                current = Row()
                usedColumns = 0
            }

            let itemWidth = columnWidth * CGFloat(requested) + spacing * CGFloat(requested - 1)
            let x = (columnWidth + spacing) * CGFloat(usedColumns)
            let height = subviews[index].sizeThatFits(ProposedViewSize(width: itemWidth, height: nil)).height

            current.items.append(Item(index: index, x: x, width: itemWidth))
            current.height = max(current.height, height)
            usedColumns += requested

            if usedColumns >= columnCount {
                rows.append(current)
                current = Row()
                usedColumns = 0
            }
        }

        if !current.items.isEmpty {
            rows.append(current)
        }
        return rows
    }
}

// MARK: - Frame reporting

/// Every tile's frame in the grid's coordinate space, which is what turns a drag position into an
/// index in the layout document.
private struct WidgetFramePreferenceKey: PreferenceKey {
    static var defaultValue: [String: CGRect] { [:] }

    static func reduce(value: inout [String: CGRect], nextValue: () -> [String: CGRect]) {
        value.merge(nextValue()) { _, latest in latest }
    }
}

// MARK: - Grid

/// The dashboard's tiles, in order, with edit-mode reordering.
///
/// **Reordering approach.** The drag handle carries a `DragGesture` reported in the grid's own
/// named coordinate space. Each tile publishes its frame through a preference; those frames are
/// snapshotted when a drag begins (they do not move during the drag) and the finger position is
/// hit-tested against them to find the tile being dropped on. On release the grid calls
/// `DashboardStore.moveWidget(_:toIndex:in:)`, so the move is a real change to the stored document
/// that persists immediately — the tile does not merely slide around on screen. `.draggable` was
/// not used because the drop target has to be an index between tiles rather than another tile's
/// contents, and because a `Transferable` payload would round-trip the widget through an item
/// provider for no benefit.
public struct DashboardGrid: View {

    private struct DragState {
        let widgetID: String
        var translation: CGSize
        var targetIndex: Int?
    }

    private static let gridSpaceName = "hardwood.dashboard.grid"

    private let layout: DashboardLayout
    private let results: [String: WidgetState]
    private let isEditing: Bool
    private let catalog: Catalog
    private let onConfigure: (DashboardWidget) -> Void

    @ObservedObject private var store: DashboardStore

    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @State private var frames: [String: CGRect] = [:]
    @State private var dragFrames: [String: CGRect] = [:]
    @State private var drag: DragState?

    public init(layout: DashboardLayout,
                results: [String: WidgetState],
                isEditing: Bool,
                catalog: Catalog,
                store: DashboardStore,
                onConfigure: @escaping (DashboardWidget) -> Void) {
        self.layout = layout
        self.results = results
        self.isEditing = isEditing
        self.catalog = catalog
        _store = ObservedObject(wrappedValue: store)
        self.onConfigure = onConfigure
    }

    // MARK: Derived

    private var gridSpace: CoordinateSpace { .named(DashboardGrid.gridSpaceName) }

    private var columnCount: Int {
        let columns = catalog.gridColumns
        let count = horizontalSizeClass == .regular ? columns.regular : columns.compact
        return max(count, 1)
    }

    private var orderFingerprint: [String] { layout.widgets.map { $0.id } }

    private var moveAnimation: Animation? {
        reduceMotion ? nil : .snappy(duration: 0.26)
    }

    // MARK: Body

    public var body: some View {
        WidgetFlowLayout(columns: columnCount, spacing: Spacing.md) {
            ForEach(layout.widgets) { widget in
                tile(for: widget)
                    .widgetColumnSpan(widget.size.columnSpan(horizontalSizeClass: horizontalSizeClass))
            }
        }
        .coordinateSpace(.named(DashboardGrid.gridSpaceName))
        .onPreferenceChange(WidgetFramePreferenceKey.self) { value in
            frames = value
        }
        .animation(moveAnimation, value: orderFingerprint)
    }

    @ViewBuilder private func tile(for widget: DashboardWidget) -> some View {
        let isDragging = drag?.widgetID == widget.id
        WidgetContainer(
            widget: widget,
            state: results[widget.id] ?? .loading,
            catalog: catalog,
            accent: layout.accent,
            isEditing: isEditing,
            isDragging: isDragging,
            isDropTarget: isDropTarget(widget),
            dragHandle: isEditing ? AnyView(handle(for: widget, isDragging: isDragging)) : nil,
            onConfigure: { onConfigure(widget) },
            onResize: { newSize in
                store.resizeWidget(widget.id, to: newSize, in: layout.id)
            },
            onDuplicate: {
                store.duplicateWidget(widget.id, in: layout.id)
            },
            onRemove: {
                store.removeWidget(widget.id, from: layout.id)
            }
        )
        .offset(isDragging ? (drag?.translation ?? .zero) : .zero)
        .zIndex(isDragging ? 1 : 0)
        .background(frameReader(for: widget))
        .transition(.scale(scale: 0.94).combined(with: .opacity))
    }

    private func handle(for widget: DashboardWidget, isDragging: Bool) -> some View {
        WidgetDragHandle(isActive: isDragging)
            .gesture(dragGesture(for: widget))
    }

    private func frameReader(for widget: DashboardWidget) -> some View {
        GeometryReader { proxy in
            Color.clear
                .preference(key: WidgetFramePreferenceKey.self,
                            value: [widget.id: proxy.frame(in: gridSpace)])
        }
    }

    // MARK: Dragging

    private func dragGesture(for widget: DashboardWidget) -> some Gesture {
        DragGesture(minimumDistance: 4, coordinateSpace: gridSpace)
            .onChanged { value in
                if drag?.widgetID == widget.id {
                    drag?.translation = value.translation
                    drag?.targetIndex = targetIndex(for: value.location, excluding: widget.id)
                } else {
                    // Snapshot the frames once: they are stable for the rest of the drag, and the
                    // dragged tile's own frame is about to start moving with the finger.
                    dragFrames = frames
                    drag = DragState(widgetID: widget.id,
                                     translation: value.translation,
                                     targetIndex: nil)
                }
            }
            .onEnded { value in
                let destination = targetIndex(for: value.location, excluding: widget.id)
                let movedID = widget.id
                drag = nil
                dragFrames = [:]
                guard let destination = destination else { return }
                withAnimation(moveAnimation) {
                    store.moveWidget(movedID, toIndex: destination, in: layout.id)
                }
            }
    }

    /// The index of the tile the finger is currently over, or `nil` when it is over a gap or over
    /// the tile being dragged.
    private func targetIndex(for point: CGPoint, excluding widgetID: String) -> Int? {
        var best: (index: Int, distance: CGFloat)?
        for (index, widget) in layout.widgets.enumerated() {
            guard widget.id != widgetID, let frame = dragFrames[widget.id] else { continue }
            let dx = frame.midX - point.x
            let dy = frame.midY - point.y
            let distance = dx * dx + dy * dy
            if let current = best {
                if distance < current.distance {
                    best = (index: index, distance: distance)
                }
            } else {
                best = (index: index, distance: distance)
            }
        }
        guard let best = best, best.index < layout.widgets.count else { return nil }
        let candidate = layout.widgets[best.index]
        guard let frame = dragFrames[candidate.id],
              frame.insetBy(dx: -Spacing.sm, dy: -Spacing.sm).contains(point) else {
            return nil
        }
        return best.index
    }

    private func isDropTarget(_ widget: DashboardWidget) -> Bool {
        guard let drag = drag,
              let target = drag.targetIndex,
              widget.id != drag.widgetID,
              let index = layout.widgets.firstIndex(where: { $0.id == widget.id }) else {
            return false
        }
        return index == target
    }
}

#if DEBUG
#Preview("Dashboard grid") {
    let store = DashboardPreviewData.store()
    let layout = store.selectedLayout ?? DashboardPreviewData.layout
    var states: [String: WidgetState] = [:]
    for (index, widget) in layout.widgets.enumerated() {
        switch index % 3 {
        case 0:
            states[widget.id] = .loading
        case 1:
            states[widget.id] = .unavailable(reason: "Shot charts start with the 1996-97 season.")
        default:
            states[widget.id] = .failed(.offline)
        }
    }
    return ScrollView {
        DashboardGrid(layout: layout,
                      results: states,
                      isEditing: true,
                      catalog: DashboardPreviewData.catalog,
                      store: store,
                      onConfigure: { _ in })
            .padding(Spacing.md)
    }
    .hardwoodBackground()
}
#endif
