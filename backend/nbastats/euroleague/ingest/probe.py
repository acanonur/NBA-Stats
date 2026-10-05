"""The probe: the first real run against the EuroLeague service, made to be diagnosed.

Why this exists
---------------
The ingest was written without being able to reach any EuroLeague host (the container that built it
had every one refused at its proxy). Every URL, field name and shape in it is documentation, not
observation. The honest way to find out what is different is to ask the service five questions,
once, politely, and keep exactly what it said. That is this command, and it is meant to be the
**first thing run on the Mac**:

    python -m nbastats.euroleague.ingest probe

What it does
------------
It makes five requests, one second or more apart, under the same polite client and allowlist as the
worker (the descriptive User-Agent, the breaker, the size cap): the clubs (E4), the calendar
(E1), one round's games (E2), one played game's box score (E3) and one club's registrations (E5).
For each it

1. **records the response** (body and a sidecar with the URL, status, validators and hash) under
   ``HARDWOOD_DATA_DIR/recordings/euroleague/``, never inside the source tree;
2. **parses it** with the real parser and says ``ok``, ``notFound``, ``httpError``, ``blocked`` or
   ``unreadable``;
3. **for an unreadable shape, names the key it wanted and the keys it found**: the path and the
   reason come straight from the parser, and a *shape report* lists, for every key the parser looks
   for on an item, whether it was present, with its type, plus any keys the item has that the parser
   never reads;
4. **runs the invariants** over the box score (points formula, rebounds, the team's minutes, five
   starters, the PIR formula) against that round's final score and partials, and reports each
   failure by rule;
5. **previews the club reconciliation**: which of the crosswalk's official codes the service
   confirmed, which it has never heard of, and which of the service's clubs the crosswalk lacks.

It writes **no database row**. Running it cannot change what the app shows.

Re-diagnosing without the network
---------------------------------
``probe --offline DIR`` parses the newest recording of each endpoint found under ``DIR`` and prints
the same report with no request. After a parser has been patched this is the whole loop: edit,
re-run offline, read. ``tests/euroleague/test_parse.py`` also runs the parsers over
``backend/tests/local/`` recordings when any are there, and skips otherwise.

Nothing real is committed
-------------------------
``github.com/acanonur/NBA-Stats`` is public and a recording is somebody else's data. The output
folder defaults to the data directory; an ``--out`` inside the source checkout is refused unless
it is under ``backend/tests/local/`` (git-ignored). That is the one in-repo place real recordings
may go,
so a failing real payload can sit beside the parser tests that read it.

Exit code
---------
0 when all five answered and parsed; 1 when anything was unreadable, missing or an error; 2 when the
service refused (a breaker opened) or the output location was refused.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Final, Mapping, Sequence

from ...intel.http import USER_AGENT, BreakerState
from ...intel.recordings import data_dir as default_data_dir
from ...intel.recordings import recordings_dir, save_recording
from ..profile import ClubCrosswalk, load_club_codes
from . import endpoints, invariants, parse
from .client import EuroLeagueClient, Failure, Fetched, classify_failure
from .endpoints import ENDPOINTS, current_season_code

__all__ = [
    "RECORDING_KIND",
    "EndpointReport",
    "ProbeReport",
    "ShapeLine",
    "shape_report",
    "check_output_location",
    "find_recordings",
    "run_probe",
    "render",
]

logger = logging.getLogger("nbastats.euroleague.ingest.probe")

#: The folder under ``recordings/`` the probe writes to.
RECORDING_KIND: Final = "euroleague"

_HEADERS_KEPT: Final = (
    "content-type",
    "etag",
    "last-modified",
    "server",
    "cache-control",
    "retry-after",
    "x-ratelimit-remaining",
    "cf-ray",
)


# ------------------------------------------------------------------------------ shapes


@dataclass(frozen=True)
class ShapeLine:
    """One key the parser looks for on an item, and what was there.

    ``found`` is filled when the key is missing: the keys of the deepest object that *does* exist
    along its path (or a word for what was there instead), so a renamed field reads as "wanted
    ``local.score``; ``local`` has club, partials, points".
    """

    path: str
    required: bool
    present: bool
    kind: str | None = None
    found: tuple[str, ...] = ()

    def render(self) -> str:
        mark = "ok     " if self.present else ("MISSING" if self.required else "absent ")
        need = "required" if self.required else "optional"
        suffix = f"  ({self.kind})" if self.kind else ""
        if self.found and not self.present:
            suffix += "  -- found there instead: " + ", ".join(self.found)
        return f"    [{mark}] {self.path}  {need}{suffix}"


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    return type(value).__name__


def _lookup(root: Any, path: str) -> tuple[bool, str | None, tuple[str, ...]]:
    """Follow ``a.b[].c`` through ``root``; ``[]`` descends into the first list element.

    Returns ``(present, type name, found)``; ``found`` says what was at the point the path broke.
    """
    node = root
    for part in path.split("."):
        descend = part.endswith("[]")
        key = part[:-2] if descend else part
        if not isinstance(node, dict):
            what = f"a list of {len(node)}" if isinstance(node, list) else _kind(node)
            return False, None, (what,)
        if key not in node:
            return False, None, tuple(sorted(str(k) for k in node)[:24])
        node = node[key]
        if descend:
            if not isinstance(node, list) or not node:
                return False, None, ("an empty list" if isinstance(node, list) else _kind(node),)
            node = node[0]
    return True, _kind(node), ()


def _first_item(key: str, payload: Any) -> Any:
    if key == "E3":
        root = payload
        if isinstance(root, dict) and "local" not in root and isinstance(root.get("data"), dict):
            root = root["data"]
        return root
    entries = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(entries, list) and entries:
        return entries[0]
    return None


def shape_report(key: str, payload: Any) -> tuple[list[ShapeLine], list[str]]:
    """``(lines, unread_keys)`` for one endpoint's decoded payload.

    ``lines`` has one :class:`ShapeLine` per key the parser looks for (see
    :data:`~nbastats.euroleague.ingest.parse.EXPECTED_PATHS`), checked on the first item (the whole
    object for a box score). ``unread_keys`` are the item's top-level keys the parser never
    reads, so a renamed field shows up as one missing key and one new one side by side.
    """
    item = _first_item(key, payload)
    expected = parse.EXPECTED_PATHS.get(key, ())
    if item is None:
        return [ShapeLine(p, req, False) for p, req in expected], []
    lines = []
    for path, required in expected:
        present, kind, found = _lookup(item, path)
        lines.append(ShapeLine(path, required, present, kind, found))
    known = {p.split(".")[0].removesuffix("[]") for p, _ in expected}
    extra = sorted(k for k in item if k not in known) if isinstance(item, dict) else []
    return lines, extra


# ---------------------------------------------------------------------------------- report


@dataclass
class EndpointReport:
    """What happened for one of the five requests."""

    key: str
    url: str | None
    #: ``ok``, ``notFound``, ``httpError``, ``blocked``, ``unreadable``, ``error`` or ``notRun``.
    verdict: str
    status: int | None = None
    bytes: int | None = None
    saved: str | None = None
    reason: str | None = None
    items: int | None = None
    rejected: list[str] = field(default_factory=list)
    shape: list[ShapeLine] = field(default_factory=list)
    unread_keys: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def good(self) -> bool:
        return self.verdict == "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "url": self.url,
            "verdict": self.verdict,
            "status": self.status,
            "bytes": self.bytes,
            "saved": self.saved,
            "reason": self.reason,
            "items": self.items,
            "rejected": list(self.rejected),
            "missingRequired": [s.path for s in self.shape if s.required and not s.present],
            "unreadKeys": list(self.unread_keys),
            "notes": list(self.notes),
        }


@dataclass
class ProbeReport:
    season: str
    started_at: datetime
    folder: str | None
    offline: bool = False
    endpoints: list[EndpointReport] = field(default_factory=list)
    invariants: list[str] = field(default_factory=list)
    crosswalk: list[str] = field(default_factory=list)
    refused: str | None = None
    blocked: BreakerState | None = None

    @property
    def exit_code(self) -> int:
        if self.refused or (self.blocked is not None and self.blocked.paused_until):
            return 2
        return 0 if self.endpoints and all(e.good for e in self.endpoints) else 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "season": self.season,
            "startedAt": self.started_at.isoformat(),
            "offline": self.offline,
            "folder": self.folder,
            "userAgent": USER_AGENT,
            "endpoints": [e.to_dict() for e in self.endpoints],
            "invariants": list(self.invariants),
            "crosswalk": list(self.crosswalk),
            "refused": self.refused,
            "exitCode": self.exit_code,
        }


# --------------------------------------------------------------------------- locations


def _repo_root() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent
    return None


def check_output_location(base: Path | str) -> str | None:
    """``None`` when recordings may be written under ``base``, else the reason they may not.

    A folder inside the source checkout is refused unless it is under ``backend/tests/local/``
    (git-ignored). A folder outside the checkout is always fine.
    """
    root = _repo_root()
    if root is None:
        return None
    target = Path(base).expanduser().resolve()
    try:
        relative = target.relative_to(root)
    except ValueError:
        return None
    if relative.parts[:3] == ("backend", "tests", "local"):
        return None
    return (
        f"{target} is inside the Hardwood source checkout ({root}), and a recording is somebody "
        "else's data in a public repository. Use a folder outside the checkout, or "
        "backend/tests/local/ (which git ignores)."
    )


def find_recordings(folder: Path | str) -> dict[str, tuple[Path, dict[str, Any]]]:
    """The newest recording of each endpoint key found under ``folder`` (searched recursively).

    A recording is a body file with a ``.json`` sidecar that names an ``endpointKey``. Returns
    ``{key: (body path, sidecar)}``.
    """
    root = Path(folder).expanduser()
    found: dict[str, tuple[Path, dict[str, Any], float]] = {}
    if not root.is_dir():
        return {}
    for sidecar in root.rglob("*.json"):
        body = sidecar.with_name(sidecar.name[: -len(".json")])
        if not body.is_file():
            continue
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        key = meta.get("endpointKey") if isinstance(meta, dict) else None
        if key not in ENDPOINTS:
            continue
        stamp = body.stat().st_mtime
        if key not in found or stamp > found[key][2]:
            found[key] = (body, meta, stamp)
    return {k: (b, m) for k, (b, m, _) in found.items()}


# ----------------------------------------------------------------------------- the work


def _meta(fetched: Fetched, season: str, label: str) -> dict[str, Any]:
    return {
        "endpointKey": fetched.endpoint_key,
        "url": fetched.url,
        "season": season,
        "label": label,
        "status": fetched.status,
        "attempts": fetched.attempts,
        "fetchedAt": fetched.fetched_at.isoformat(),
        "headers": {k: v for k, v in fetched.headers.items() if k in _HEADERS_KEPT},
        "userAgent": USER_AGENT,
    }


def _judge(
    key: str,
    url: str | None,
    body: bytes | None,
    status: int | None,
    season: str,
    *,
    expect_round: int | None = None,
    expect_club: str | None = None,
) -> tuple[EndpointReport, Any]:
    """Parse one response and say what it was. Returns ``(report, parsed result or None)``."""
    report = EndpointReport(key, url, "ok", status=status, bytes=len(body) if body else 0)
    if status is not None and status in (404, 410):
        report.verdict, report.reason = "notFound", f"the service answered {status}"
        return report, None
    if status is not None and status != 200:
        report.verdict, report.reason = "httpError", f"the service answered {status}"
        return report, None
    try:
        payload = parse.decode_json(body or b"", what=f"{key} response")
    except parse.Unreadable as exc:
        report.verdict, report.reason = "unreadable", f"{exc.reason}"
        return report, None
    report.shape, report.unread_keys = shape_report(key, payload)
    try:
        if key == "E1":
            parsed: Any = parse.parse_rounds(payload, expect_season=season)
        elif key == "E2":
            parsed = parse.parse_games(payload, expect_season=season, expect_round=expect_round)
        elif key == "E3":
            parsed = parse.parse_box_score(payload)
        elif key == "E4":
            parsed = parse.parse_clubs(payload, expect_season=season)
        else:
            parsed = parse.parse_people(payload, expect_club=expect_club, expect_season=season)
    except parse.Unreadable as exc:
        report.verdict = "unreadable"
        report.reason = f"{exc.path}: {exc.reason}"
        return report, None
    if key == "E3":
        report.items = len(parsed.home.players) + len(parsed.away.players)
    else:
        report.items = len(parsed.items)
        if not parsed.items and not parsed.ignored:
            report.notes.append("the service sent an empty list, which is not what a season has")
        report.rejected = [str(r) for r in parsed.rejected[:8]]
        if parsed.rejected:
            report.notes.append(f"{len(parsed.rejected)} item(s) rejected")
        if not parsed.complete:
            report.notes.append(
                f"the service says {parsed.total} items but sent {len(parsed.items)}"
            )
        if getattr(parsed, "ignored", 0):
            report.notes.append(f"{parsed.ignored} staff row(s) ignored")
        if key == "E2":
            for game in parsed.items:
                report.notes.extend(f"game {game.game_code}: {i}" for i in game.issues[:2])
    return report, parsed


def _check_box(box: parse.BoxScore, game: parse.GameInfo | None) -> list[str]:
    final_home = (
        game.home.score if game and game.status == "final" else box.home.totals.stats.get("pts")
    )
    final_away = (
        game.away.score if game and game.status == "final" else box.away.totals.stats.get("pts")
    )
    facts = invariants.box_to_facts(
        box,
        final_home=final_home,
        final_away=final_away,
        home_club=game.home.club.code if game else None,
        away_club=game.away.club.code if game else None,
        home_partials=game.home.partials if game else None,
        away_partials=game.away.partials if game else None,
        ot_periods=game.ot_periods if game else None,
    )
    check = invariants.check_game(facts)
    source = "the schedule's final score" if game else "the box score's own totals (no schedule)"
    lines = [f"checked against {source}: {len(check.hard)} hard, {len(check.soft)} soft failure(s)"]
    lines += [f"  HARD {v.rule}: {v.message}" for v in check.hard[:12]]
    lines += [f"  soft {v.rule}: {v.message}" for v in check.soft[:12]]
    return lines


def _crosswalk_preview(clubs: Sequence[parse.ClubInfo], book: ClubCrosswalk) -> list[str]:
    live = {c.code: c for c in clubs}
    confirmed = sorted(c for c in live if (book.by_official(c) is not None))
    unknown = sorted(c for c in live if book.by_official(c) is None)
    missing = sorted(r.official_code for r in book.rows if r.official_code not in live)
    pending = sorted(
        c
        for c in confirmed
        if (row := book.by_official(c)) is not None and row.verified != "fixture"
    )
    lines = [
        f"the service lists {len(live)} clubs; the crosswalk confirms {len(confirmed)} of its "
        f"{len(book.rows)} official codes"
    ]
    if pending:
        lines.append(
            "  confirmed by this recording (change 'pending' to 'fixture' in "
            "data/club_codes.json): " + ", ".join(pending)
        )
    if unknown:
        lines.append(
            "  CLUBS THE CROSSWALK DOES NOT KNOW (their official codes): " + ", ".join(unknown)
        )
    if missing:
        lines.append(
            "  crosswalk codes the service did not list (a wrong code, or a club not in this "
            "season): " + ", ".join(missing)
        )
    return lines


def run_probe(
    *,
    season: str | None = None,
    out: Path | str | None = None,
    offline: Path | str | None = None,
    round_number: int | None = None,
    club: str | None = None,
    game_code: int | None = None,
    client: EuroLeagueClient | None = None,
    now: datetime | None = None,
    crosswalk: ClubCrosswalk | None = None,
    echo: Callable[[str], None] | None = None,
) -> ProbeReport:
    """Make (or, offline, replay) the five probe requests and report. See the module docstring.

    ``out`` is a data directory: recordings go under ``<out>/recordings/euroleague/`` (default
    ``HARDWOOD_DATA_DIR``). ``client`` is for tests. ``echo`` receives progress lines as they happen
    (the command line prints them; tests leave it ``None``).
    """
    say = echo or (lambda line: None)
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    code = season or current_season_code(moment)
    endpoints.validate_season_code(code)
    book = crosswalk or load_club_codes()
    if offline is not None:
        return _run_offline(Path(offline), code, moment, book, say)

    base = Path(out).expanduser() if out is not None else default_data_dir()
    problem = check_output_location(base)
    if problem:
        report = ProbeReport(code, moment, None, refused=problem)
        say(f"REFUSED: {problem}")
        return report
    folder = recordings_dir(base) / RECORDING_KIND
    report = ProbeReport(code, moment, str(folder))
    own = client is None
    api = client or EuroLeagueClient(max_requests=8)
    say(f"Hardwood EuroLeague probe, season {code}")
    say(f"  user agent: {USER_AGENT}")
    say("  host: api-live.euroleague.net, one request at a time, at least a second apart")
    say(f"  recordings: {folder}")
    try:
        _probe_all(api, report, code, base, moment, book, round_number, club, game_code, say)
    finally:
        if own:
            api.close()
    return report


def _record(
    report: ProbeReport,
    fetched: Fetched,
    season: str,
    label: str,
    base: Path,
    moment: datetime,
    *,
    expect_round: int | None = None,
    expect_club: str | None = None,
) -> tuple[EndpointReport, Any]:
    path = save_recording(
        RECORDING_KIND,
        f"{fetched.endpoint_key}_{label}",
        fetched.body,
        meta=_meta(fetched, season, label),
        now=moment,
        base=base,
    )
    endpoint, parsed = _judge(
        fetched.endpoint_key,
        fetched.url,
        fetched.body,
        fetched.status,
        season,
        expect_round=expect_round,
        expect_club=expect_club,
    )
    endpoint.saved = str(path)
    report.endpoints.append(endpoint)
    return endpoint, parsed


def _failed(
    report: ProbeReport, key: str, url: str, exc: BaseException, client: EuroLeagueClient
) -> Failure:
    failure = classify_failure(exc)
    verdict = "blocked" if failure.state == "blocked" else "error"
    report.endpoints.append(EndpointReport(key, url, verdict, reason=failure.reason))
    if failure.state == "blocked":
        report.blocked = client.breaker()
    return failure


def _probe_all(
    api: EuroLeagueClient,
    report: ProbeReport,
    season: str,
    base: Path,
    moment: datetime,
    book: ClubCrosswalk,
    round_number: int | None,
    club: str | None,
    game_code: int | None,
    say: Callable[[str], None],
) -> None:
    clubs: list[parse.ClubInfo] = []
    games: list[parse.GameInfo] = []

    def step(key: str, url: str, call: Callable[[], Fetched], label: str, **expect: Any) -> Any:
        say(f"  {key} {url.replace(endpoints.API_ORIGIN, '')}")
        try:
            fetched = call()
        except Exception as exc:  # noqa: BLE001 - every failure is a verdict, not a crash
            failure = _failed(report, key, url, exc, api)
            say(f"    -> {report.endpoints[-1].verdict}: {failure.reason}")
            if failure.stop:
                raise _Stop from exc
            return None
        endpoint, parsed = _record(report, fetched, season, label, base, moment, **expect)
        say(
            f"    -> {endpoint.verdict} (HTTP {endpoint.status}, {endpoint.bytes} bytes)"
            + (f": {endpoint.reason}" if endpoint.reason else "")
        )
        return parsed

    try:
        parsed = step("E4", endpoints.clubs_url(season), lambda: api.clubs(season), "clubs")
        if parsed is not None:
            clubs = list(parsed.items)
        step("E1", endpoints.rounds_url(season), lambda: api.rounds(season), "rounds")
        number = round_number or 1
        parsed = step(
            "E2",
            endpoints.games_url(season, number),
            lambda: api.games(season, number),
            f"games-round-{number}",
            expect_round=number,
        )
        if parsed is not None:
            games = list(parsed.items)
        target: parse.GameInfo | None = None
        if game_code is not None:
            target = next((g for g in games if g.game_code == game_code), None)
        else:
            target = next((g for g in games if g.status == "final"), None)
        code = game_code if game_code is not None else (target.game_code if target else None)
        if code is None:
            report.endpoints.append(
                EndpointReport(
                    "E3",
                    None,
                    "notRun",
                    reason=(
                        f"no played game in round {number}; "
                        "run again with --round or --game-code"
                    ),
                )
            )
        else:
            box = step(
                "E3",
                endpoints.game_stats_url(season, code),
                lambda: api.box_score(season, code),
                f"box-game-{code}",
            )
            if box is not None:
                report.invariants = _check_box(box, target)
        chosen = club or (target.home.club.code if target else (clubs[0].code if clubs else None))
        if chosen is None:
            report.endpoints.append(
                EndpointReport("E5", None, "notRun", reason="no club known; pass --club")
            )
        else:
            step(
                "E5",
                endpoints.club_people_url(season, chosen),
                lambda: api.people(season, chosen),
                f"people-{chosen}",
                expect_club=chosen,
            )
    except _Stop:
        for key in ("E4", "E1", "E2", "E3", "E5"):
            if not any(e.key == key for e in report.endpoints):
                report.endpoints.append(
                    EndpointReport(key, None, "notRun", reason="stopped after the service refused")
                )
    if clubs:
        report.crosswalk = _crosswalk_preview(clubs, book)
    _save_summary(report, base, moment)


class _Stop(Exception):
    """The service said no; do not ask the remaining questions."""


def _save_summary(report: ProbeReport, base: Path, moment: datetime) -> None:
    try:
        save_recording(
            RECORDING_KIND,
            "probe_summary",
            json.dumps(report.to_dict(), indent=2, sort_keys=True).encode("utf-8"),
            meta={"kind": "probeSummary"},
            now=moment,
            base=base,
        )
    except OSError:  # a summary that cannot be saved must not hide the report
        logger.warning("could not save the probe summary", exc_info=True)


def _run_offline(
    folder: Path, season: str, moment: datetime, book: ClubCrosswalk, say: Callable[[str], None]
) -> ProbeReport:
    report = ProbeReport(season, moment, str(folder), offline=True)
    found = find_recordings(folder)
    say(f"Hardwood EuroLeague probe (offline), reading {folder}")
    games: list[parse.GameInfo] = []
    clubs: list[parse.ClubInfo] = []
    for key in ("E4", "E1", "E2", "E3", "E5"):
        if key not in found:
            report.endpoints.append(
                EndpointReport(key, None, "notRun", reason="no recording of this endpoint found")
            )
            continue
        body_path, meta = found[key]
        recorded_season = meta.get("season") or season
        endpoint, parsed = _judge(
            key,
            meta.get("url"),
            body_path.read_bytes(),
            meta.get("status") if isinstance(meta.get("status"), int) else 200,
            recorded_season,
            expect_round=None,
            expect_club=None,
        )
        endpoint.saved = str(body_path)
        report.endpoints.append(endpoint)
        if key == "E2" and parsed is not None:
            games = list(parsed.items)
        if key == "E4" and parsed is not None:
            clubs = list(parsed.items)
        if key == "E3" and parsed is not None:
            code = None
            url = meta.get("url") or ""
            tail = url.rsplit("/stats", 1)[0].rsplit("/", 1)[-1]
            code = int(tail) if tail.isdigit() else None
            target = next((g for g in games if g.game_code == code), None)
            report.invariants = _check_box(parsed, target)
    if clubs:
        report.crosswalk = _crosswalk_preview(clubs, book)
    return report


# ------------------------------------------------------------------------------ rendering


def render(report: ProbeReport) -> str:
    """The report as the text the command prints at the end."""
    lines: list[str] = []
    if report.refused:
        return f"REFUSED: {report.refused}"
    lines.append("")
    lines.append("=" * 72)
    lines.append(
        f"EuroLeague probe: season {report.season}" + (" (offline)" if report.offline else "")
    )
    lines.append("=" * 72)
    for e in report.endpoints:
        spec = ENDPOINTS[e.key]
        tag = e.verdict.upper()
        lines.append(f"{e.key} {spec.name:<7} {tag:<10} " + (e.reason or ""))
        if e.url:
            lines.append(f"    {e.url}")
        if e.status is not None:
            lines.append(
                f"    HTTP {e.status}, {e.bytes} bytes, "
                f"{e.items if e.items is not None else '?'} item(s)"
            )
        if e.saved:
            lines.append(f"    recorded: {e.saved}")
        for note in e.notes:
            lines.append(f"    note: {note}")
        for item in e.rejected[:5]:
            lines.append(f"    rejected: {item}")
        if e.verdict in ("unreadable", "ok") and e.shape:
            problems = [s for s in e.shape if s.required and not s.present]
            if e.verdict == "unreadable" or problems:
                lines.append("    what the parser looks for on one item:")
                lines += [s.render() for s in e.shape]
                if e.unread_keys:
                    lines.append(
                        "    keys on the item the parser never reads: "
                        + ", ".join(e.unread_keys[:30])
                    )
    if report.invariants:
        lines.append("")
        lines.append("Box-score invariants")
        lines += [f"  {line}" for line in report.invariants]
    if report.crosswalk:
        lines.append("")
        lines.append("Club codes")
        lines += [f"  {line}" for line in report.crosswalk]
    lines.append("")
    if report.exit_code == 0:
        lines.append("All five endpoints answered and parsed. Nothing was written to the database.")
    elif report.exit_code == 2:
        if report.blocked is not None and report.blocked.paused_until:
            lines.append(
                f"The service refused ({report.blocked.reason}); do not retry before "
                f"{report.blocked.paused_until:%Y-%m-%d %H:%M} UTC."
            )
    else:
        lines.append("Something did not match what the parsers expect.")
        if report.folder:
            lines.append(f"  The responses are saved in {report.folder}")
            lines.append(
                "  They are private (do not commit them). After patching parse.py, re-check with:"
            )
            lines.append(
                f"    python -m nbastats.euroleague.ingest probe --offline {report.folder}"
            )
    return "\n".join(lines)
