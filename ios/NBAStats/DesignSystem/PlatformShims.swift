import SwiftUI

// MARK: - Platform shims
//
// Hardwood is one multiplatform target: macOS 14 is the product, iOS 17 keeps compiling. A handful
// of SwiftUI modifiers exist on only one of them (`navigationBarTitleDisplayMode`,
// `textInputAutocapitalization`, `listStyle(.insetGrouped)`, ...), and an unavailable symbol is a
// compile error on the other platform, not a runtime no-op. Instead of scattering `#if os(iOS)`
// across two dozen call sites, each such modifier is wrapped exactly once here, in a method named
// for what the call site MEANS ("a compact title", "do not capitalise this field"). Call sites
// stay free of preprocessor noise, and `scripts/check_swift_portability.py` (rule P3) can then
// assert that the raw iOS-only spellings appear nowhere else.
//
// Every method is `@ViewBuilder ... -> some View` so the `#if` branches may return different
// concrete types. Only APIs available on BOTH iOS 17.0 and macOS 14.0 may be used outside an
// `#if os(iOS)` / `#if os(macOS)` branch.

public extension View {

    /// A small inline navigation title on iOS; macOS titles live in the window toolbar already.
    @ViewBuilder func hardwoodInlineTitle() -> some View {
        #if os(iOS)
        self.navigationBarTitleDisplayMode(.inline)
        #else
        self
        #endif
    }

    /// Capitalise each word as the reader types a name (a dashboard name, a player search).
    /// A hardware keyboard on the Mac needs no such hint.
    @ViewBuilder func hardwoodCapitalizeWords() -> some View {
        #if os(iOS)
        self.textInputAutocapitalization(.words)
        #else
        self
        #endif
    }

    /// Never capitalise (a URL or a key). No-op on macOS.
    @ViewBuilder func hardwoodNeverCapitalize() -> some View {
        #if os(iOS)
        self.textInputAutocapitalization(.never)
        #else
        self
        #endif
    }

    /// URL keyboard + content type on iOS; nothing on macOS (NSTextContentType.URL is not relied on).
    @ViewBuilder func hardwoodURLField() -> some View {
        #if os(iOS)
        self.textContentType(.URL).keyboardType(.URL)
        #else
        self
        #endif
    }

    /// Password autofill hint on iOS only (on macOS it would offer Keychain passwords for an API key).
    @ViewBuilder func hardwoodSecretField() -> some View {
        #if os(iOS)
        self.textContentType(.password)
        #else
        self
        #endif
    }

    /// The grouped, rounded list look on iOS; the plain inset list on macOS, which has no
    /// `insetGrouped` style.
    @ViewBuilder func hardwoodGroupedListStyle() -> some View {
        #if os(iOS)
        self.listStyle(.insetGrouped)
        #else
        self.listStyle(.inset)
        #endif
    }

    /// A push-to-choose picker on iOS; a pop-up menu on macOS, where `.navigationLink` does not exist.
    @ViewBuilder func hardwoodNavigationPickerStyle() -> some View {
        #if os(iOS)
        self.pickerStyle(.navigationLink)
        #else
        self.pickerStyle(.menu)
        #endif
    }

    /// Swipeable pages with a dot indicator on iOS. macOS never shows onboarding, but the file
    /// that uses this still has to compile there, and `.page` tab styles do not exist.
    @ViewBuilder func hardwoodPagedTabStyle() -> some View {
        #if os(iOS)
        self.tabViewStyle(.page(indexDisplayMode: .always))
            .indexViewStyle(.page(backgroundDisplayMode: .always))
        #else
        self
        #endif
    }

    /// Sheets on macOS size to their content's ideal size, and a List or Form has none.
    @ViewBuilder func hardwoodSheetFrame(minWidth: CGFloat = 520, minHeight: CGFloat = 480) -> some View {
        #if os(macOS)
        self.frame(minWidth: minWidth, idealWidth: minWidth + 40, minHeight: minHeight, idealHeight: minHeight + 100)
        #else
        self
        #endif
    }
}

/// Numbers that differ by platform and nothing else.
public enum PlatformMetrics {
    /// Mouse users cannot scroll sideways through a table whose indicators are hidden.
    public static var showsHorizontalIndicators: Bool {
        #if os(macOS)
        return true
        #else
        return false
        #endif
    }
}

/// Sentences that name a gesture or a place, which differ by platform. String literals only: no
/// view, no model, nothing that could change what a screen does.
public enum PlatformCopy {
    public static var refreshHint: String {
        #if os(macOS)
        return "Press ⌘R to refresh."
        #else
        return "Pull down to refresh."
        #endif
    }

    public static var editHint: String {
        #if os(macOS)
        return "Choose Dashboard ▸ Edit Dashboard (⇧⌘E) to add, move and remove widgets."
        #else
        return "Tap Edit to add, move and remove widgets."
        #endif
    }

    public static var searchPlace: String {
        #if os(macOS)
        return "Player Search in the sidebar"
        #else
        return "the Search tab"
        #endif
    }

    public static var dashboardPlace: String {
        #if os(macOS)
        return "a dashboard in the sidebar"
        #else
        return "the Dashboard tab"
        #endif
    }
}
