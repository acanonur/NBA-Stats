#if os(macOS)
import AppKit
import SwiftUI

// "Copy Table": the rows of a table on screen, as tab-separated text on the pasteboard, so they
// paste into Excel or Numbers as cells.
//
// WHY THIS EXISTS
// The workbook this app replaces is a spreadsheet, and the person who uses it compares numbers
// side by side. A native table cannot show every column the workbook had (a table is capped at ten
// columns here), so the way to keep the whole row is to hand it to a spreadsheet. What is copied is
// exactly what is displayed: the already-formatted strings, em dashes included. Nothing is
// recomputed on the way out, and a missing value stays an em dash rather than becoming 0.
//
// A cell never contains a tab or a line break after cleaning (they would break the row apart), so
// one table row is always one pasted row.

enum MacTableExport {

    /// The table as text: a header row, then one line per row, separated by tabs.
    static func tsv(header: [String], rows: [[String]]) -> String {
        var output: [String] = []
        output.append(header.map { clean($0) }.joined(separator: "\t"))
        for row in rows {
            output.append(row.map { clean($0) }.joined(separator: "\t"))
        }
        return output.joined(separator: "\n") + "\n"
    }

    /// A cell's text with anything that would split a row replaced by a space.
    static func clean(_ text: String) -> String {
        var result = text.replacingOccurrences(of: "\t", with: " ")
        result = result.replacingOccurrences(of: "\r", with: " ")
        result = result.replacingOccurrences(of: "\n", with: " ")
        return result
    }

    /// Puts the table on the general pasteboard, replacing what was there.
    static func copy(header: [String], rows: [[String]]) {
        let text = tsv(header: header, rows: rows)
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(text, forType: .string)
    }
}

/// The toolbar button every table screen carries. It is disabled while there is nothing to copy.
struct MacCopyTableButton: View {
    private let header: [String]
    private let rows: [[String]]

    init(header: [String], rows: [[String]]) {
        self.header = header
        self.rows = rows
    }

    var body: some View {
        Button {
            MacTableExport.copy(header: header, rows: rows)
        } label: {
            Label("Copy Table", systemImage: "doc.on.doc")
        }
        .help("Copy the table as tab-separated text, for Excel or Numbers")
        .disabled(rows.isEmpty)
    }
}
#endif
