#if os(macOS)
import SwiftUI

// The Method screen's constants table: every number the model uses, with where each came from.
//
// WHY PROVENANCE IS ON EVERY ROW
// A constant is only as good as its source. A number the workbook's author typed and nobody has
// validated is not the same kind of thing as a default Hardwood chose, a figure derived from the
// data, or a value the reader changed, and the table says which in words. "Default" says whether the
// value is still the one Hardwood shipped with. The description is the server's own sentence about
// what the constant does.
//
// VALUES ARE SHOWN, NOT WORKED OUT
// A value is the number (or, for a policy, the word) the server sent, with trailing zeros trimmed so
// 0.300 reads as 0.3. It sorts numerically when it is a number. The app computes nothing here and
// changes nothing: the table is read-only.

// MARK: - Words

enum MacConstantWords {

    /// Where a constant came from, in words.
    static func provenance(_ provenance: String?) -> String {
        guard let provenance = provenance, !provenance.isEmpty else { return Formatting.emDash }
        switch provenance {
        case "default":
            return "Hardwood default"
        case "workbook":
            return "Workbook"
        case "workbookUnvalidated":
            return "Workbook (not validated)"
        case "derived":
            return "Derived from the data"
        case "manual":
            return "Set by you"
        default:
            return provenance
        }
    }

    /// `Default` for a value that is still the shipped one, `Changed` for one that is not, an em
    /// dash when the server did not say.
    static func defaultWord(_ isDefault: Bool?) -> String {
        guard let isDefault = isDefault else { return Formatting.emDash }
        return isDefault ? "Default" : "Changed"
    }

    /// A constant's number for display, shared by the Method screen and Settings > Model so one
    /// value never reads two ways: grouped and in the reader's locale like every other number in
    /// the app, at most six decimals, trailing zeros trimmed (`0.3`, `10`, `0.02439`).
    static func numberText(_ value: Double) -> String {
        trimmed(Formatting.decimal(value, places: 6))
    }

    /// A localized number with its trailing fraction zeros trimmed: `0.300` is `0.3`, `10.0` is
    /// `10`, and in a German locale `0,5000` is `0,5` and `1.500,0` is `1.500`.
    ///
    /// The separator is the locale's, the same `Locale.current` the app's number formatter uses.
    /// Looking for a literal "." would leave `0,5000` untouched in a comma locale and, worse,
    /// strip `1.500` (one thousand five hundred, "." being the grouping mark) to `1.5`.
    static func trimmed(_ text: String) -> String {
        let separator: String = Locale.current.decimalSeparator ?? "."
        guard !separator.isEmpty, text.contains(separator) else { return text }
        var result = text
        while result.hasSuffix("0") {
            result.removeLast()
        }
        if result.hasSuffix(separator) {
            result.removeLast(separator.count)
        }
        return result
    }
}

// MARK: - Rows

struct MacConstantRow: Identifiable, Hashable {
    let id: String
    let order: Int
    let key: String
    let value: String
    let valueSort: Double
    let provenance: String
    let defaultText: String
    let defaultSort: Int
    let details: String

    static let copyHeader: [String] = ["Key", "Value", "Provenance", "Default", "Description"]

    var copyCells: [String] {
        [key, value, provenance, defaultText, details]
    }
}

enum MacConstantRows {

    /// One row per constant, in the order the server sent them.
    static func rows(from constants: [LeagueModelConstant]?) -> [MacConstantRow] {
        guard let source = constants else { return [] }
        var result: [MacConstantRow] = []
        var index = 0
        for constant in source {
            result.append(make(constant, index: index))
            index += 1
        }
        return result
    }

    private static func make(_ constant: LeagueModelConstant, index: Int) -> MacConstantRow {
        let key: String = constant.key ?? ("row-" + String(index))
        let number: Double? = constant.value?.doubleValue
        return MacConstantRow(
            id: key + "-" + String(index),
            order: index,
            key: key,
            value: valueText(constant.value),
            valueSort: LeagueFormatting.sortKey(number),
            provenance: MacConstantWords.provenance(constant.provenance),
            defaultText: MacConstantWords.defaultWord(constant.isDefault),
            defaultSort: defaultOrder(constant.isDefault),
            details: constant.description ?? ""
        )
    }

    /// The value as text: a number trimmed, a word as it is, an em dash for nothing.
    private static func valueText(_ value: JSONValue?) -> String {
        guard let value = value else { return Formatting.emDash }
        if let number = value.doubleValue {
            return MacConstantWords.numberText(number)
        }
        if let words = value.stringValue, !words.isEmpty {
            return words
        }
        return Formatting.emDash
    }

    /// Changed values first: they are the ones a reader came to find.
    private static func defaultOrder(_ isDefault: Bool?) -> Int {
        guard let isDefault = isDefault else { return 2 }
        return isDefault ? 1 : 0
    }
}

// MARK: - Table

/// Constants (5 columns).
struct MacConstantsTable: View {

    private let rows: [MacConstantRow]
    @Binding private var selection: MacConstantRow.ID?
    @Binding private var sortOrder: [KeyPathComparator<MacConstantRow>]

    init(rows: [MacConstantRow],
         selection: Binding<MacConstantRow.ID?>,
         sortOrder: Binding<[KeyPathComparator<MacConstantRow>]>) {
        self.rows = rows
        _selection = selection
        _sortOrder = sortOrder
    }

    private var sortedRows: [MacConstantRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sortedRows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Key", value: \MacConstantRow.key) { row in
                TextCell(text: row.key)
            }
            .width(min: 140, ideal: 190, max: 260)
            TableColumn("Value", value: \MacConstantRow.valueSort) { row in
                NumCell(text: row.value)
            }
            .width(min: 60, ideal: 80, max: 120)
            TableColumn("Provenance", value: \MacConstantRow.provenance) { row in
                TextCell(text: row.provenance)
            }
            .width(min: 120, ideal: 160, max: 220)
            TableColumn("Default", value: \MacConstantRow.defaultSort) { row in
                TextCell(text: row.defaultText)
            }
            .width(min: 64, ideal: 80, max: 110)
            TableColumn("Description", value: \MacConstantRow.details) { row in
                TextCell(text: row.details)
            }
            .width(min: 240, ideal: 520)
        }
        .tableStyle(.inset(alternatesRowBackgrounds: true))
        .contextMenu(forSelectionType: MacConstantRow.ID.self) { ids in
            menuItems(for: ids)
        }
    }

    @ViewBuilder private func menuItems(for ids: Set<MacConstantRow.ID>) -> some View {
        if let id = ids.first, let row = rows.first(where: { $0.id == id }) {
            Button("Copy Row") {
                MacTableExport.copy(header: MacConstantRow.copyHeader, rows: [row.copyCells])
            }
        }
    }
}
#endif
