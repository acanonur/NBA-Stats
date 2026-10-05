"""Importers: how outside files become EuroLeague rows.

``xlsx``       a stdlib-only reader for ``.xlsx`` (``zipfile`` and ``xml.etree``; no openpyxl
               at run time), hardened against the usual spreadsheet attacks
``workbook``   the importer for the user's EuroLeague workbook: it maps the sheets the design
               names onto the ``el_*`` tables, never reads a gambling column, applies the
               box-score invariants, and is idempotent by file hash

Why the reader is its own
-------------------------
The importer needs the cached values of a few sheets in a file the user chose, on a machine
where the service runs unattended. A general spreadsheet library is a large dependency for that
and brings its own opinions about formulas, styles and external links; the reader here does
one thing, returns what the file already says, and has every limit and refusal in one place.

Where real data may go
----------------------
Real data only ever flows through here into the local store under ``HARDWOOD_DATA_DIR``. The
committed tests build a small synthetic workbook of invented clubs and players instead, so no
real name, number or link is ever committed. Nothing an importer reads is served raw: what is
stored is the structured rows, never the file.
"""
