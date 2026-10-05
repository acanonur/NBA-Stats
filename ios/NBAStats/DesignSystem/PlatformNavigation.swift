import SwiftUI

// MARK: - Navigation that has to differ on the Mac
//
// `PlatformShims.swift` wraps single modifiers that exist on one platform only. This file holds the
// pieces that are bigger than one modifier: how a configuration picker opens, how a search field
// is shown, how wide a dashboard may grow, and the macOS-only pointer affordances (right-click
// menu, hover). Every call site stays free of `#if`, and `scripts/check_swift_portability.py`
// still sees the platform seams in exactly two files.
//
// Only APIs that exist on BOTH iOS 17.0 and macOS 14.0 appear outside an `#if os(...)` branch.

// MARK: - Pickers inside a sheet

/// A row that opens a picker: a `NavigationLink` push on iOS, a sheet on macOS.
///
/// **Why a sheet on the Mac.** The widget configuration form is itself a sheet there, and a sheet
/// has no title bar, so a page pushed inside it has no Back control: the reader would be stranded
/// on the picker. A nested sheet has its own Done button instead, and it closes when the picker
/// calls `dismiss()` after a choice, exactly as a pushed page pops on iOS.
///
/// The generic parameters are `Destination` and `RowLabel`, not `Label`, so SwiftUI's own `Label`
/// view stays usable inside this type.
///
/// `title` is the same string the destination gives `.navigationTitle`. iOS ignores it (the
/// navigation bar draws the destination's own title); the Mac sheet draws it as a heading, because
/// a sheet has no title bar and `.navigationTitle` there is shown nowhere.
struct HardwoodPushLink<Destination: View, RowLabel: View>: View {
    private let title: String?
    private let destination: () -> Destination
    private let label: () -> RowLabel
    @State private var isPresented = false

    init(title: String? = nil,
         @ViewBuilder destination: @escaping () -> Destination,
         @ViewBuilder label: @escaping () -> RowLabel) {
        self.title = title
        self.destination = destination
        self.label = label
    }

    var body: some View {
        #if os(macOS)
        Button {
            isPresented = true
        } label: {
            // A plain button has no disclosure arrow and only the drawn text is clickable, so the
            // chevron and the content shape give the row back what `NavigationLink` supplies.
            HStack(spacing: Spacing.sm) {
                label()
                Image(systemName: "chevron.right")
                    .imageScale(.small)
                    .foregroundStyle(Palette.textTertiary)
                    .accessibilityHidden(true)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .sheet(isPresented: $isPresented) {
            HardwoodPushedSheet(title: title, content: destination)
        }
        #else
        NavigationLink {
            destination()
        } label: {
            label()
        }
        #endif
    }
}

#if os(macOS)
/// The sheet a `HardwoodPushLink` presents on macOS: a heading, the picker, a rule and a Done
/// button.
///
/// The picker views dismiss themselves after a choice through `@Environment(\.dismiss)`, which
/// resolves to this sheet because it is the nearest presentation.
///
/// The heading is drawn here because a macOS sheet has no title bar: the `.navigationTitle` each
/// picker sets ("Add Metric", the field's label) is shown nowhere, and a second-level picker would
/// otherwise be an untitled list.
struct HardwoodPushedSheet<Content: View>: View {
    private let title: String?
    private let content: () -> Content
    @Environment(\.dismiss) private var dismiss

    init(title: String? = nil, content: @escaping () -> Content) {
        self.title = title
        self.content = content
    }

    var body: some View {
        VStack(spacing: 0) {
            if let heading = title, !heading.isEmpty {
                Text(heading)
                    .hardwoodText(.sectionTitle)
                    .lineLimit(1)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 16)
                    .padding(.top, 14)
                    .padding(.bottom, 8)
                    .accessibilityAddTraits(.isHeader)
                Divider()
            }
            content()
            Divider()
            HStack {
                Spacer()
                Button("Done") { dismiss() }
                    .keyboardShortcut(.defaultAction)
            }
            .padding(12)
        }
        .frame(minWidth: 460, idealWidth: 520, minHeight: 520, idealHeight: 600)
    }
}
#endif

/// The content of a sheet that hosts a picker: a navigation stack on iOS (title, toolbar, swipe
/// down to dismiss) and the pushed-sheet frame with a Done button on macOS.
///
/// The ordered pickers present their "add one more" chooser through this, so the chooser is never
/// a sheet the Mac reader cannot close with the pointer.
struct HardwoodPickerSheet<Content: View>: View {
    private let title: String?
    private let content: () -> Content

    /// `title` is the heading the Mac sheet draws (iOS shows the content's own navigation title).
    init(title: String? = nil, @ViewBuilder content: @escaping () -> Content) {
        self.title = title
        self.content = content
    }

    var body: some View {
        #if os(macOS)
        HardwoodPushedSheet(title: title, content: content)
        #else
        NavigationStack {
            content()
        }
        #endif
    }
}

// MARK: - View helpers

public extension View {

    /// `.searchable` on iOS; an in-content field on macOS.
    ///
    /// A sheet has no toolbar to hold the system search field on the Mac, so the field is a plain
    /// text field pinned above the content. It sits on a material so rows scrolling under it stay
    /// legible.
    @ViewBuilder func hardwoodSearchField(text: Binding<String>, prompt: String) -> some View {
        #if os(macOS)
        self.safeAreaInset(edge: .top, spacing: 0) {
            TextField(prompt, text: text)
                .textFieldStyle(.roundedBorder)
                .padding(12)
                .background(.regularMaterial)
        }
        #else
        self.searchable(text: text, prompt: Text(prompt))
        #endif
    }

    /// Caps the content at a readable width on a wide Mac window and centres it. A no-op on iOS,
    /// where the screen is never wider than the layout was designed for.
    @ViewBuilder func hardwoodReadableWidth() -> some View {
        #if os(macOS)
        self.frame(maxWidth: 1600).frame(maxWidth: .infinity)
        #else
        self
        #endif
    }

    /// A right-click menu on macOS. iOS rows and tiles keep their swipe actions, overflow menus
    /// and drag handles, and a second long-press menu would fight the drag gesture.
    @ViewBuilder func hardwoodContextMenu<MenuItems: View>(@ViewBuilder _ menuItems: @escaping () -> MenuItems) -> some View {
        #if os(macOS)
        self.contextMenu {
            menuItems()
        }
        #else
        self
        #endif
    }

    /// Reports whether the pointer is over this view, on macOS. Touch has no hover, so iOS is a
    /// no-op and the binding is never written.
    @ViewBuilder func hardwoodHover(isHovering: Binding<Bool>) -> some View {
        #if os(macOS)
        self.onHover { hovering in
            isHovering.wrappedValue = hovering
        }
        #else
        self
        #endif
    }

    /// A menu that reads as a small icon button on macOS: borderless, with no pop-up arrow. iOS
    /// keeps the default menu presentation.
    @ViewBuilder func hardwoodIconMenuStyle() -> some View {
        #if os(macOS)
        self
            .menuStyle(.button)
            .buttonStyle(.borderless)
            .menuIndicator(.hidden)
        #else
        self
        #endif
    }
}

// MARK: - Copy that names a gesture

/// More platform-dependent sentences, kept beside the shims' own `PlatformCopy` strings. String
/// literals only: nothing here can change what a screen does.
///
/// The iOS wording of every string below is byte-identical to the sentence it replaced, so the
/// iOS build reads exactly as it did before the Mac existed.
public extension PlatformCopy {

    /// `APIError.recoveryHint` while offline.
    static var offlineRecoveryHint: String {
        #if os(macOS)
        return "Reconnect and press ⌘R to refresh."
        #else
        return "Reconnect and pull down to refresh."
        #endif
    }

    /// `APIError.recoveryHint` after a timeout.
    static var timeoutRecoveryHint: String {
        #if os(macOS)
        return "Press ⌘R to try again."
        #else
        return "Pull down to try again."
        #endif
    }

    /// The footer under the list of dashboards in the Manage Dashboards sheet.
    static var layoutListHint: String {
        #if os(macOS)
        return "Click a dashboard to rename it, recolour it or reset it. Right-click one to show, duplicate or delete it."
        #else
        return "Tap a dashboard to rename it, recolour it or reset it. Swipe right to show it, left for duplicate and delete."
        #endif
    }

    /// The title of the onboarding point about refreshing.
    static var onboardingRefreshTitle: String {
        #if os(macOS)
        return "Press ⌘R to refresh"
        #else
        return "Pull down to refresh"
        #endif
    }

    /// The detail under the onboarding refresh title.
    static var onboardingRefreshDetail: String {
        #if os(macOS)
        return "Hardwood checks for finished games when you open it, when you press ⌘R, and about once a minute while it stays open."
        #else
        return "Hardwood checks for finished games when you open it, when you pull the board down, and — if iOS allows it — quietly in the background."
        #endif
    }

    /// The detail under the onboarding point about editing a dashboard.
    static var onboardingEditDetail: String {
        #if os(macOS)
        return "Choose Dashboard ▸ Edit Dashboard (⇧⌘E) to add, resize, reorder or remove widgets. Nothing is saved to a server — your dashboards live on this Mac."
        #else
        return "Tap Edit on the board to add, resize, reorder or remove widgets. Nothing is saved to a server — your dashboards live on this device."
        #endif
    }

    /// The footer under the Refreshing section of Settings.
    static var settingsRefreshFooter: String {
        #if os(macOS)
        return "While Hardwood is open it checks your server every minute; the server keeps collecting through launchd when the app is closed."
        #else
        let minutes = Int(BackgroundRefresh.defaultRefreshInterval / 60)
        return "Hardwood asks iOS for a background refresh about every \(minutes) minutes and again overnight, after the league publishes its stat corrections. iOS decides whether those ever run, so every screen also refreshes when you open it and when you pull down."
        #endif
    }
}
