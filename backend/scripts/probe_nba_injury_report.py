#!/usr/bin/env python3
"""Fetch (or read) a real NBA injury report and show what the parser makes of it.

Run this on the Mac, once, when the first real report of the season is out (there are none for
2026-27 before about 19 October). It exists because the PDF parser in
``nbastats/nba_intel/report_pdf.py`` was written from the documented layout and has never seen a
real file: the development environment cannot reach the NBA's servers. This script is how that gets
checked, and how a failure gets diagnosed.

What it does
------------
* Without arguments it asks for the newest quarter-hour slot, stepping back up to eight slots until
  a report exists (the same walk the worker does), politely: one request a second, a User-Agent
  naming Hardwood and stating personal use.
* It **records** the PDF it finds, with the response headers, under
  ``$HARDWOOD_DATA_DIR/recordings/nba_injury/`` (on a Mac,
  ``~/Library/Application Support/Hardwood/recordings/nba_injury/``). Recordings are for
  diagnosis, are never read back by the service, and never go into git. Copy one to
  ``backend/tests/local/nba_injury/`` (git-ignored) and ``tests/test_report_pdf.py`` will run the
  parser over it as a test.
* With ``--parse`` it runs the parser and prints a summary: the status, how many entries and which
  teams, what the parser could not read and why.
* With ``--dump-runs N`` it prints the first N text fragments with their positions. That is the one
  thing needed to fix the parser if the layout differs from the documented one.
* With ``--file PATH`` it reads a PDF you already have and touches no network at all.

There is no gate, no review date and no terms setting: the lead removed that step. The posture is in
``docs/LEGAL.md`` §2 and §2e (private, personal, never redistributed). The one thing that is still
enforced is politeness (``nbastats/intel/http.py``).

Examples
--------
::

    python3 backend/scripts/probe_nba_injury_report.py --parse
    python3 backend/scripts/probe_nba_injury_report.py --slot "2026-10-22 17:30" --parse
    python3 backend/scripts/probe_nba_injury_report.py --file ~/Desktop/r.pdf --parse --dump-runs 60
    python3 backend/scripts/probe_nba_injury_report.py --dry-run

Exit status: 0 when a report was obtained or read, 1 when none was found or it could not be
read at all, 2 for a usage error.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nbastats.intel import recordings  # noqa: E402
from nbastats.intel.http import PoliteClient  # noqa: E402
from nbastats.nba_intel import report_fetch, report_pdf  # noqa: E402


def _parse_slot(text: str) -> datetime:
    """``YYYY-MM-DD HH:MM`` in US Eastern, floored to the quarter hour."""
    try:
        naive = datetime.strptime(text.strip(), "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not 'YYYY-MM-DD HH:MM' (US Eastern time)"
        ) from exc
    return report_fetch.floor_slot(naive.replace(tzinfo=report_fetch.EASTERN))


def _summarise(report: report_pdf.ParsedReport, *, show: int = 12) -> None:
    as_of = (
        report.as_of_et.isoformat(sep=" ") + " ET" if report.as_of_et is not None else "not found"
    )
    print(f"  parse status : {report.status}")
    print(f"  pages        : {report.pages}")
    print(f"  as of        : {as_of}")
    print(
        f"  entries      : {len(report.rows)}"
        f"  (dropped lines: {report.dropped} of {report.body_lines})"
    )
    for name, state, clean in sorted({(m.team_name, m.state, m.clean) for m in report.teams}):
        note = "" if clean else "  (NOT stored: a line was dropped)"
        print(f"  team         : {name}: {state}{note}")
    for row in report.rows[:show]:
        reason = f"  [{row.reason_raw}]" if row.reason_raw else ""
        who = f"{row.matchup} {row.team_name}: {row.player_name_raw}"
        print(f"  entry        : {who} = {row.status}{reason}")
    if len(report.rows) > show:
        print(f"  ... and {len(report.rows) - show} more")
    for error in report.errors:
        print(f"  could not read: {error}")


def _dump_runs(pdf: bytes, count: int) -> None:
    try:
        pages = report_pdf.extract_runs(pdf)
    except (report_pdf.ReportUnreadable, report_pdf.ReportParserUnavailable) as exc:
        print(f"  cannot extract text: {exc}")
        return
    shown = 0
    for number, runs in enumerate(pages, start=1):
        print(f"  --- page {number}: {len(runs)} text fragments (x, y, size, text) ---")
        for run in sorted(runs, key=lambda r: (-r.y, r.x))[: max(0, count - shown)]:
            print(f"  {run.x:8.1f} {run.y:8.1f} {run.size:5.1f}  {run.text}")
            shown += 1
        if shown >= count:
            break


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch a real NBA injury report, record it, and show how the parser reads it."
    )
    parser.add_argument("--slot", type=_parse_slot, help="one slot, 'YYYY-MM-DD HH:MM' US Eastern")
    parser.add_argument(
        "--back",
        type=int,
        default=report_fetch.MAX_STEPS,
        help="how many slots to step back from the newest (default %(default)s)",
    )
    parser.add_argument("--file", type=Path, help="read this PDF instead of fetching (no network)")
    parser.add_argument("--parse", action="store_true", help="run the parser and print a summary")
    parser.add_argument(
        "--dump-runs",
        type=int,
        default=0,
        metavar="N",
        help="print the first N text fragments with their positions",
    )
    parser.add_argument("--no-record", action="store_true", help="do not save the PDF")
    parser.add_argument("--dry-run", action="store_true", help="print the URLs, request nothing")
    args = parser.parse_args(argv)

    if args.back < 1 or args.back > 48:
        parser.error("--back must be between 1 and 48")
    if args.dump_runs < 0:
        parser.error("--dump-runs cannot be negative")

    if args.file is not None:
        try:
            pdf = args.file.expanduser().read_bytes()
        except OSError as exc:
            print(f"Cannot read {args.file}: {exc}", file=sys.stderr)
            return 1
        print(f"Read {args.file} ({len(pdf)} bytes). No network was used.")
        return _inspect(pdf, args)

    try:
        template = report_fetch.url_template()
    except report_fetch.InvalidTemplateError as exc:
        print(f"Cannot use the URL template: {exc}", file=sys.stderr)
        return 2
    now = datetime.now(timezone.utc)
    if args.slot is not None:
        slots = [args.slot]
    else:
        slots = report_fetch.candidate_slots(now, None, max_steps=args.back)

    if args.dry_run:
        print("Would request, newest first:")
        for slot in slots:
            print(f"  {report_fetch.slot_url(slot, template)}")
        return 0

    if args.parse and not report_pdf.pypdf_available():
        print(
            "pypdf is not installed. Install it with: pip install 'hardwood-backend[injuries]'",
            file=sys.stderr,
        )
        return 2

    found: bytes | None = None
    with PoliteClient() as client:
        for slot in slots:
            url = report_fetch.slot_url(slot, template)
            print(f"Asking for {slot.strftime('%Y-%m-%d %H:%M ET')} ...")
            result = report_fetch.fetch_slot(client, url)
            print(f"  {result.state}" + (f" ({result.reason})" if result.reason else ""))
            if result.state == "ok" and result.body is not None:
                found = result.body
                if not args.no_record:
                    path = recordings.save_recording(
                        "nba_injury",
                        url.rsplit("/", 1)[-1],
                        result.body,
                        meta={
                            "url": url,
                            "httpStatus": result.http_status,
                            "slotEastern": slot.isoformat(),
                            "fetchedAt": result.fetched_at.isoformat(),
                        },
                    )
                    print(f"  recorded to {path}")
                break
            if result.state in ("blocked", "error"):
                break
    if found is None:
        print("No report was found. That is normal outside a reporting window.")
        return 1
    return _inspect(found, args)


def _inspect(pdf: bytes, args: argparse.Namespace) -> int:
    if args.dump_runs:
        _dump_runs(pdf, args.dump_runs)
    if args.parse:
        try:
            report = report_pdf.parse_report(pdf)
        except report_pdf.ReportParserUnavailable as exc:
            print(str(exc), file=sys.stderr)
            return 2
        _summarise(report)
        return 0 if report.status in ("ok", "partial", "empty") else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
