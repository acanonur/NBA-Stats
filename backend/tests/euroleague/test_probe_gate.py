"""The probe: the first real run, and whether it tells the truth about what it found.

The file is called ``test_probe_gate`` because the design's table names it so; with the terms gate
removed (lead amendment A2) there is no gate left to test, and what is left is the probe's other
half, which matters more: it must be **polite** (the allowlist, a second between requests, the
descriptive User-Agent, a breaker that stops it), **private** (recordings under the data directory
or the git-ignored ``tests/local``, mode 0600, never inside the source tree), **harmless** (it
writes no database row), and above all **diagnostic**: when the service's shape is not the
documented one, what it prints must name the key it wanted and the keys it found.

The service is scripted; nothing here reaches a network.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from nbastats.euroleague.ingest import __main__ as cli
from nbastats.euroleague.ingest import endpoints, probe
from nbastats.euroleague.ingest.client import EuroLeagueClient
from nbastats.intel import http as intel_http
from nbastats.intel.http import PoliteClient

UTC = timezone.utc
NOW = datetime(2031, 10, 4, 9, 0, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "euroleague" / "authored"
BASE = "https://api-live.euroleague.net/v2/competitions/E/seasons/E2031"
ROUTES = {
    f"{BASE}/clubs": "clubs.json",
    f"{BASE}/rounds": "rounds.json",
    f"{BASE}/games?roundNumber=1": "games_round_1.json",
    f"{BASE}/games/1/stats": "box_game_1.json",
    f"{BASE}/clubs/ZZA/people": "people_ZZA.json",
}
EXPECTED_ORDER = [
    f"{BASE}/clubs",
    f"{BASE}/rounds",
    f"{BASE}/games?roundNumber=1",
    f"{BASE}/games/1/stats",
    f"{BASE}/clubs/ZZA/people",
]


def jload(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class Service:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.sleeps: list[float] = []
        self.monotonic = 0.0
        self.bodies: dict[str, bytes] = {
            url: (FIXTURES / n).read_bytes() for url, n in ROUTES.items()
        }
        self.status: dict[str, int] = {}
        self.agents: list[str] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.monotonic += seconds

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        self.agents.append(request.headers.get("user-agent", ""))
        if url in self.status:
            return httpx.Response(self.status[url], content=b"refused")
        if url in self.bodies:
            return httpx.Response(
                200,
                content=self.bodies[url],
                headers={"content-type": "application/json", "server": "t"},
            )
        return httpx.Response(404, content=b"not found")

    def client(self) -> EuroLeagueClient:
        polite = PoliteClient(
            transport=httpx.MockTransport(self.handler),
            clock=lambda: self.monotonic,
            wall_clock=lambda: NOW + timedelta(seconds=self.monotonic),
            sleep=self.sleep,
            jitter=lambda: 0.0,
        )
        return EuroLeagueClient(polite, max_requests=8)


@pytest.fixture()
def service() -> Service:
    return Service()


def go(service: Service, out: Path, **kw: Any) -> probe.ProbeReport:
    return probe.run_probe(season="E2031", out=out, client=service.client(), now=NOW, **kw)


def verdicts(report: probe.ProbeReport) -> dict[str, str]:
    return {e.key: e.verdict for e in report.endpoints}


def recordings(out: Path) -> list[Path]:
    folder = out / "recordings" / "euroleague"
    return sorted(p for p in folder.glob("*") if not p.name.endswith(".json"))


# ================================================================================ a clean run


def test_a_clean_probe_asks_exactly_the_five_questions_in_order(
    service: Service, tmp_path: Path
) -> None:
    report = go(service, tmp_path)
    assert service.calls == EXPECTED_ORDER
    assert all(endpoints.is_allowed(u) for u in service.calls)
    assert verdicts(report) == {k: "ok" for k in ("E4", "E1", "E2", "E3", "E5")}
    assert report.exit_code == 0 and report.refused is None and report.blocked is None


def test_the_probe_is_polite_a_second_apart_with_the_descriptive_user_agent(
    service: Service, tmp_path: Path
) -> None:
    go(service, tmp_path)
    assert service.sleeps == [1.0] * 4  # five requests, four waits of the one-second floor
    assert set(service.agents) == {intel_http.USER_AGENT}
    assert "private single-user analytics" in service.agents[0]


def test_it_needs_no_terms_variable_and_ignores_any_that_are_set(
    service: Service, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in list(os.environ):
        if "TERMS" in name:
            monkeypatch.delenv(name)
    assert go(service, tmp_path / "a").exit_code == 0
    monkeypatch.setenv(
        "HARDWOOD_EL_TERMS_OUTCOME", "denied"
    )  # the gate was removed: nothing reads it
    assert go(Service(), tmp_path / "b").exit_code == 0
    assert "TERMS" not in Path(probe.__file__).read_text(encoding="utf-8").replace("no terms", "")


def test_the_defaults_for_what_to_probe_are_round_one_the_first_played_game_and_its_home_club(
    service: Service, tmp_path: Path
) -> None:
    report = go(service, tmp_path)
    e3 = next(e for e in report.endpoints if e.key == "E3")
    assert e3.url == f"{BASE}/games/1/stats" and e3.items == 24


def test_explicit_round_game_and_club_are_honoured(service: Service, tmp_path: Path) -> None:
    service.bodies[f"{BASE}/games?roundNumber=2"] = (FIXTURES / "games_round_2.json").read_bytes()
    service.bodies[f"{BASE}/games/3/stats"] = (FIXTURES / "box_game_3_overtime.json").read_bytes()
    people = json.loads((FIXTURES / "people_ZZA.json").read_text())
    for entry in people["data"]:
        entry["club"] = {**entry["club"], "code": "ZZB"}
    service.bodies[f"{BASE}/clubs/ZZB/people"] = json.dumps(people).encode()
    report = go(service, tmp_path, round_number=2, game_code=3, club="ZZB")
    assert service.calls == [
        f"{BASE}/clubs",
        f"{BASE}/rounds",
        f"{BASE}/games?roundNumber=2",
        f"{BASE}/games/3/stats",
        f"{BASE}/clubs/ZZB/people",
    ]
    assert report.exit_code == 0 and any(
        "checked against the schedule" in l for l in report.invariants
    )


# =================================================================================== recording


def test_every_response_is_recorded_byte_for_byte_and_privately(
    service: Service, tmp_path: Path
) -> None:
    report = go(service, tmp_path)
    bodies = recordings(tmp_path)
    assert len([b for b in bodies if "probe_summary" not in b.name]) == 5
    served = {url: service.bodies[url] for url in ROUTES}
    for endpoint in report.endpoints:
        path = Path(endpoint.saved)
        assert path.read_bytes() == served[endpoint.url]
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        meta = json.loads(path.with_name(path.name + ".json").read_text())
        assert meta["endpointKey"] == endpoint.key and meta["url"] == endpoint.url
        assert meta["status"] == 200 and meta["season"] == "E2031"
        assert (
            meta["userAgent"] == intel_http.USER_AGENT
            and meta["sha256"]
            and meta["bytes"] == len(served[endpoint.url])
        )
        assert meta["headers"]["content-type"] == "application/json"
        assert stat.S_IMODE(os.stat(path.with_name(path.name + ".json")).st_mode) == 0o600
    summary = [p for p in (tmp_path / "recordings" / "euroleague").glob("*probe_summary")]
    assert len(summary) == 1
    saved = json.loads(summary[0].read_text())
    assert saved["exitCode"] == 0 and saved["userAgent"] == intel_http.USER_AGENT


def test_recordings_go_under_the_data_directory_by_default(
    service: Service, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HARDWOOD_DATA_DIR", str(tmp_path / "hardwood"))
    report = probe.run_probe(season="E2031", client=service.client(), now=NOW)
    assert report.folder == str(tmp_path / "hardwood" / "recordings" / "euroleague")
    assert len(recordings(tmp_path / "hardwood")) == 6  # five endpoints and the summary


def test_the_probe_writes_no_database_row_and_opens_no_store(
    service: Service, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "hardwood_el.db"
    monkeypatch.setenv("HARDWOOD_EL_DATABASE_URL", f"sqlite:///{store}")
    opened: list[str] = []
    from nbastats.euroleague import db

    monkeypatch.setattr(db, "get_el_engine", lambda *a, **k: opened.append("engine") or None)
    monkeypatch.setattr(db, "create_el_engine", lambda *a, **k: opened.append("engine") or None)
    go(service, tmp_path / "rec")
    assert opened == [] and not store.exists() and not list(tmp_path.glob("*.db*"))


# =========================================================================== the repo guard


def _repo() -> Path:
    root = probe._repo_root()
    if root is None:
        pytest.skip("not running inside a git checkout")
    return root


def test_a_location_inside_the_source_checkout_is_refused_except_the_ignored_local_folder() -> None:
    root = _repo()
    for refused in (
        root / "backend" / "nbastats",
        root / "backend" / "tests" / "fixtures",
        root / "docs",
        root,
        root / "backend" / "tests",
    ):
        reason = probe.check_output_location(refused)
        assert reason and "source checkout" in reason and "public repository" in reason, refused
    for allowed in (
        root / "backend" / "tests" / "local",
        root / "backend" / "tests" / "local" / "euroleague",
        Path("/tmp/hardwood-recordings"),
        Path.home() / "Library" / "Application Support" / "Hardwood",
    ):
        assert probe.check_output_location(allowed) is None, allowed


def test_the_probe_refuses_to_record_into_the_checkout_and_makes_no_request(
    service: Service,
) -> None:
    root = _repo()
    report = go(service, root / "backend" / "nbastats")
    assert report.refused and report.exit_code == 2 and report.endpoints == []
    assert service.calls == []
    assert "REFUSED" in probe.render(report)
    assert not (root / "backend" / "nbastats" / "recordings").exists()


# ================================================================================== diagnosis


def test_a_renamed_nested_key_is_named_with_what_was_found_in_its_place(
    service: Service, tmp_path: Path
) -> None:
    payload = jload("games_round_1.json")
    for game in payload["data"]:
        game["local"]["points"] = game["local"].pop("score")
    service.bodies[f"{BASE}/games?roundNumber=1"] = json.dumps(payload).encode()
    report = go(service, tmp_path)
    text = probe.render(report)
    assert "[MISSING] local.score" in text
    line = next(l for l in text.splitlines() if "[MISSING] local.score" in l)
    assert (
        "found there instead:" in line
        and "points" in line
        and "club" in line
        and "partials" in line
    )
    game = next(e for e in report.endpoints if e.key == "E2")
    assert game.verdict == "ok" and any("a score is missing" in n for n in game.notes)
    # with no game recognisable as played there is no box score to ask for
    assert verdicts(report)["E3"] == "notRun" and report.exit_code == 1


def test_a_renamed_players_key_makes_the_box_score_unreadable_and_says_so(
    service: Service, tmp_path: Path
) -> None:
    payload = jload("box_game_1.json")
    payload["local"]["roster"] = payload["local"].pop("players")
    service.bodies[f"{BASE}/games/1/stats"] = json.dumps(payload).encode()
    report = go(service, tmp_path)
    box = next(e for e in report.endpoints if e.key == "E3")
    assert box.verdict == "unreadable" and box.reason.startswith("local.players:")
    assert "roster" in box.reason  # the key it found instead
    text = probe.render(report)
    assert "UNREADABLE" in text and "what the parser looks for on one item" in text
    assert "[MISSING] local.players" in text and "Something did not match" in text
    assert report.exit_code == 1 and "probe --offline" in text  # the next step is spelled out
    assert report.invariants == []  # nothing to check when the box could not be read


def test_an_html_page_where_json_was_expected_is_named_for_what_it_is(
    service: Service, tmp_path: Path
) -> None:
    service.bodies[f"{BASE}/clubs"] = (
        b"<html><title>Attention Required! | Cloudflare</title></html>"
    )
    report = go(service, tmp_path)
    clubs = next(e for e in report.endpoints if e.key == "E4")
    assert clubs.verdict == "unreadable" and "not JSON" in clubs.reason and "<html>" in clubs.reason
    assert report.crosswalk == []  # no clubs: no crosswalk preview, and no crash


def test_an_empty_list_is_flagged_as_suspicious(service: Service, tmp_path: Path) -> None:
    service.bodies[f"{BASE}/clubs"] = b'{"data": [], "total": 0}'
    report = go(service, tmp_path)
    clubs = next(e for e in report.endpoints if e.key == "E4")
    assert clubs.verdict == "ok" and clubs.items == 0
    assert any("empty list" in n for n in clubs.notes)


def test_a_box_score_that_is_not_published_is_not_found_not_an_error(
    service: Service, tmp_path: Path
) -> None:
    del service.bodies[f"{BASE}/games/1/stats"]
    report = go(service, tmp_path)
    assert verdicts(report)["E3"] == "notFound" and report.exit_code == 1
    assert "E3 stats   NOTFOUND" in probe.render(report)
    assert next(e for e in report.endpoints if e.key == "E5").verdict == "ok"  # the rest still ran


def test_a_round_with_no_played_game_skips_the_box_score_and_says_what_to_do(
    service: Service, tmp_path: Path
) -> None:
    payload = jload("games_round_2.json")
    service.bodies[f"{BASE}/games?roundNumber=1"] = json.dumps(
        {"data": [payload["data"][1]]}
    ).encode()
    report = go(service, tmp_path)
    box = next(e for e in report.endpoints if e.key == "E3")
    assert box.verdict == "notRun" and "--round or --game-code" in box.reason
    assert [u for u in service.calls if u.endswith("/stats")] == []


def test_a_refusal_stops_the_probe_at_once_and_reports_when_it_may_try_again(
    service: Service, tmp_path: Path
) -> None:
    service.status[f"{BASE}/clubs"] = 403
    report = go(service, tmp_path)
    assert service.calls == [f"{BASE}/clubs"]  # the first question was the last
    assert verdicts(report) == {
        "E4": "blocked",
        "E1": "notRun",
        "E2": "notRun",
        "E3": "notRun",
        "E5": "notRun",
    }
    assert report.exit_code == 2 and report.blocked is not None and report.blocked.paused_until
    text = probe.render(report)
    assert "do not retry before" in text and "forbidden (403)" in text


def test_a_failure_to_connect_is_an_error_verdict_not_a_crash(tmp_path: Path) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    polite = PoliteClient(
        transport=httpx.MockTransport(refuse), sleep=lambda s: None, jitter=lambda: 0.0
    )
    report = probe.run_probe(season="E2031", out=tmp_path, client=EuroLeagueClient(polite), now=NOW)
    first = report.endpoints[0]
    assert first.verdict == "error" and "no response from the service" in first.reason
    assert len(report.endpoints) >= 2 and report.exit_code == 1


def test_the_shape_report_marks_required_optional_and_unread_keys() -> None:
    lines, unread = probe.shape_report("E2", jload("games_round_1.json"))
    by_path = {l.path: l for l in lines}
    assert by_path["local.score"].present and by_path["local.score"].kind == "int"
    assert by_path["venue.name"].required is False and by_path["venue.name"].present
    assert "referee1" in unread and "gameCode" not in unread  # keys the parser never reads
    payload = jload("games_round_1.json")
    del payload["data"][0]["venue"]
    lines, _ = probe.shape_report("E2", payload)
    line = next(l for l in lines if l.path == "venue.name")
    assert not line.present and not line.required and "absent" in line.render()
    empty_lines, _ = probe.shape_report("E2", {"data": []})
    assert all(not l.present for l in empty_lines)  # nothing to look at: nothing is claimed present


def test_a_path_through_a_list_reports_an_empty_list_and_a_non_object() -> None:
    payload = jload("box_game_1.json")
    payload["local"]["players"] = []
    lines, _ = probe.shape_report("E3", payload)
    line = next(l for l in lines if l.path == "local.players[].player.person.code")
    assert not line.present and line.found == ("an empty list",)
    payload["local"]["players"] = {"not": "a list"}
    lines, _ = probe.shape_report("E3", payload)
    assert next(l for l in lines if l.path == "local.players[].player.person.code").found == (
        "dict",
    )


# ============================================================================ crosswalk preview


def test_the_club_preview_lists_confirmed_unknown_and_unlisted_codes(
    service: Service, tmp_path: Path
) -> None:
    from nbastats.euroleague.profile import ClubCodeRow, ClubCrosswalk

    book = ClubCrosswalk(
        (
            ClubCodeRow("WZA", "ZZA", "ZHB", "Zenith Harbour BC", "Harbour", "pending"),
            ClubCodeRow("WZB", "ZZB", "BMF", "Brightmoor Falcons", "Brightmoor", "fixture"),
            ClubCodeRow("WZQ", "ZZQ", "QQQ", "Quillon BC", None, "pending"),
        )
    )
    report = probe.run_probe(
        season="E2031", out=tmp_path, client=service.client(), now=NOW, crosswalk=book
    )
    text = "\n".join(report.crosswalk)
    assert "the service lists 4 clubs; the crosswalk confirms 2 of its 3 official codes" in text
    assert (
        "change 'pending' to 'fixture'" in text
        and "ZZA" in text.split("fixture'")[1].splitlines()[0]
    )
    assert "THE CROSSWALK DOES NOT KNOW" in text and "ZZC, ZZD" in text
    assert "did not list" in text and "ZZQ" in text


# ================================================================================== offline


def test_offline_mode_replays_recordings_with_no_client_and_the_same_verdicts(
    service: Service, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    online = go(service, tmp_path)
    monkeypatch.setattr(
        probe, "EuroLeagueClient", lambda *a, **k: pytest.fail("a client was built offline")
    )
    offline = probe.run_probe(
        offline=tmp_path / "recordings" / "euroleague", season="E2031", now=NOW
    )
    assert offline.offline and verdicts(offline) == verdicts(online)
    assert offline.invariants == online.invariants
    assert offline.exit_code == 0 and "(offline)" in probe.render(offline)
    assert [e.items for e in offline.endpoints] == [e.items for e in online.endpoints]


def test_after_a_parser_is_patched_the_same_recordings_are_judged_again(
    service: Service, tmp_path: Path
) -> None:
    payload = jload("box_game_1.json")
    payload["road"]["squad"] = payload["road"].pop("players")
    service.bodies[f"{BASE}/games/1/stats"] = json.dumps(payload).encode()
    broken = go(service, tmp_path)
    assert verdicts(broken)["E3"] == "unreadable"
    recorded = next(Path(e.saved) for e in broken.endpoints if e.key == "E3")
    recorded.write_bytes(
        (FIXTURES / "box_game_1.json").read_bytes()
    )  # "the service was right all along"
    again = probe.run_probe(offline=tmp_path / "recordings" / "euroleague", season="E2031", now=NOW)
    assert verdicts(again)["E3"] == "ok" and again.exit_code == 0


def test_an_offline_folder_with_nothing_in_it_reports_every_endpoint_as_not_run(
    tmp_path: Path,
) -> None:
    report = probe.run_probe(offline=tmp_path, season="E2031", now=NOW)
    assert set(verdicts(report).values()) == {"notRun"} and report.exit_code == 1
    assert probe.find_recordings(tmp_path / "missing") == {}


def test_the_newest_recording_of_each_endpoint_is_the_one_replayed(
    service: Service, tmp_path: Path
) -> None:
    go(service, tmp_path)
    folder = tmp_path / "recordings" / "euroleague"
    old = next(p for p in folder.glob("*_E4_clubs"))
    newer = old.with_name("20311105T000000Z_E4_clubs")
    newer.write_bytes(b'{"data": []}')
    newer.with_name(newer.name + ".json").write_text(
        json.dumps({"endpointKey": "E4", "url": f"{BASE}/clubs", "status": 200, "season": "E2031"})
    )
    os.utime(old, (1, 1))
    found = probe.find_recordings(folder)
    assert found["E4"][0] == newer and set(found) == {"E1", "E2", "E3", "E4", "E5"}


def test_a_json_file_that_is_not_a_recording_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "x.json").write_text("{}")
    (tmp_path / "y").write_text("body")
    (tmp_path / "y.json").write_text('{"endpointKey": "E9"}')
    (tmp_path / "z").write_text("body")
    (tmp_path / "z.json").write_text("not json at all")
    assert probe.find_recordings(tmp_path) == {}


# ====================================================================================== the CLI


def test_the_probe_command_prints_progress_the_report_and_exits_with_its_code(
    service: Service, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(probe, "EuroLeagueClient", lambda **k: service.client())
    lines: list[str] = []
    code = cli.main(["probe", "--season", "E2031", "--out", str(tmp_path)], out=lines.append)
    text = "\n".join(lines)
    assert code == 0 and "Hardwood EuroLeague probe, season E2031" in text
    assert "user agent: Hardwood/" in text and "All five endpoints answered and parsed" in text
    assert "Nothing was written to the database" in text


def test_the_probe_command_can_replay_offline_and_refuses_the_checkout(
    service: Service, tmp_path: Path
) -> None:
    go(service, tmp_path)
    lines: list[str] = []
    assert (
        cli.main(
            [
                "probe",
                "--season",
                "E2031",
                "--offline",
                str(tmp_path / "recordings" / "euroleague"),
            ],
            out=lines.append,
        )
        == 0
    )
    root = _repo()
    refused: list[str] = []
    assert cli.main(["probe", "--out", str(root / "docs")], out=refused.append) == 2
    assert any("REFUSED" in l for l in refused)
