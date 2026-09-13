import Foundation
import SwiftUI

/// The bar that sits at the bottom of the dashboard while edit mode is on.
///
/// Three jobs only: add a widget, take back the last change, and finish. Everything else an edit
/// session needs lives on the tiles themselves.
public struct EditingToolbar: View {

    private let canUndo: Bool
    private let widgetCount: Int
    private let accent: AccentName
    private let onAddWidget: () -> Void
    private let onUndo: () -> Void
    private let onDone: () -> Void

    public init(canUndo: Bool,
                widgetCount: Int,
                accent: AccentName = .orange,
                onAddWidget: @escaping () -> Void,
                onUndo: @escaping () -> Void,
                onDone: @escaping () -> Void) {
        self.canUndo = canUndo
        self.widgetCount = widgetCount
        self.accent = accent
        self.onAddWidget = onAddWidget
        self.onUndo = onUndo
        self.onDone = onDone
    }

    private var countText: String {
        widgetCount == 1 ? "1 widget" : "\(widgetCount) widgets"
    }

    public var body: some View {
        VStack(spacing: 0) {
            Rectangle()
                .fill(Palette.separator)
                .frame(height: 1)
                .accessibilityHidden(true)
            HStack(spacing: Spacing.md) {
                Button(action: onAddWidget) {
                    Label("Add Widget", systemImage: "plus.circle.fill")
                        .font(Typography.widgetTitle)
                }
                .buttonStyle(.plain)
                .foregroundStyle(accent.color)
                .accessibilityHint("Opens the widget catalog.")

                Spacer(minLength: Spacing.sm)

                Text(countText)
                    .hardwoodText(.caption)
                    .accessibilityHidden(true)

                Button(action: onUndo) {
                    Label("Undo", systemImage: "arrow.uturn.backward")
                        .labelStyle(.iconOnly)
                        .font(Typography.widgetTitle)
                        .frame(width: 34, height: 34)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .foregroundStyle(canUndo ? Palette.selection : Palette.textTertiary)
                .disabled(!canUndo)
                .accessibilityLabel("Undo the last change")

                Button("Done", action: onDone)
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                    .tint(accent.color)
            }
            .padding(.horizontal, Spacing.lg)
            .padding(.vertical, Spacing.sm)
        }
        .background(.ultraThinMaterial)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Editing")
    }
}

#if DEBUG
#Preview("Editing toolbar") {
    VStack {
        Spacer()
        EditingToolbar(canUndo: true,
                       widgetCount: 5,
                       accent: .orange,
                       onAddWidget: { },
                       onUndo: { },
                       onDone: { })
        EditingToolbar(canUndo: false,
                       widgetCount: 1,
                       accent: .indigo,
                       onAddWidget: { },
                       onUndo: { },
                       onDone: { })
    }
    .hardwoodBackground()
}
#endif
