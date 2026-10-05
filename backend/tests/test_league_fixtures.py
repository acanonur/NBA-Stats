"""The committed NBA league payload fixtures are generated, invented and kept in step.

``contracts/fixtures/leagues/nba/*.json`` are one recorded response of every NBA league payload
family (design section 10.3), so a client, the Swift app later, can decode against a real body
instead of the design's prose. Four promises keep them honest, and each is a test here:

* **Reproducible.** A fresh build from the invented demo league equals what is committed, byte for
  byte, in a clock held still and a throwaway store. A change to a payload that forgets to
  regenerate them fails here, naming the file (``python -m nbastats.nba_matchup.fixtures_export
  --write`` fixes it).
* **Invented.** Every player is an invented one (the seeder's, with its identity file switched
  off, so no real person's name, number or headshot is in them), every link points at an
  ``example.org`` host, and every body says it is a demo. That is what lets them be committed to a
  public repository. The franchises are the thirty real ones: a team's name is a fact.
* **Clean.** No object key anywhere in them carries a word the product does not define
  (:mod:`nbastats.shared.market_guard`), no probability of winning and no rank appears, and the
  existing fixtures' checks, which glob ``contracts/fixtures/*.json`` without recursing, never see
  these files.
* **Useful.** Between them they show every state a client has to draw: a team report submitted,
  not yet submitted and never published; an entry from the league's report, one typed by hand and
  one whose player matched nobody; a projection that is the model's, one that was frozen and one
  that was rebuilt; a review with both a frozen block and a rebuilt one; a table with provisional
  teams and the sentence for a league with no positional signal; the demo league's refusal to show
  statuses at all.

The build is slow (it seeds a league), so it is built once for the module.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from nbastats import db as db_module
from nbastats import identities
from nbastats.nba_matchup import availability_view, fixtures_export
from nbastats.shared import league_registry, market_guard

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"
NBA_DIR = fixtures_export.FIXTURE_DIR
#: Player ids the demo seeder invents start here; real NBA person ids are far below.
INVENTED_PLAYER_IDS = range(9_000_000, 10_000_000)
URL = ("http://", "https://")


@pytest.fixture(scope="module")
def documents() -> dict[str, Any]:
    return fixtures_export.build_fixtures()


@pytest.fixture(scope="module")
def committed() -> dict[str, Any]:
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8")) for path in NBA_DIR.glob("*.json")
    }


def walk(value: Any, key: str | None = None):
    """Every ``(key, value)`` pair in a body, at any depth."""
    if isinstance(value, dict):
        for name, child in value.items():
            yield name, child
            yield from walk(child, name)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child, key)


def values(document: Any, name: str) -> list[Any]:
    return [value for key, value in walk(document) if key == name]


def test_the_committed_fixtures_are_what_a_fresh_build_produces(documents: dict[str, Any]) -> None:
    assert fixtures_export.check_fixtures(NBA_DIR, documents) == []


def test_the_files_are_exactly_the_families_the_design_names(documents: dict[str, Any]) -> None:
    on_disk = sorted(path.stem for path in NBA_DIR.glob("*.json"))
    assert on_disk == sorted(fixtures_export.FIXTURE_NAMES)
    assert list(documents) == list(fixtures_export.FIXTURE_NAMES)
    assert len(set(fixtures_export.FIXTURE_NAMES)) == len(fixtures_export.FIXTURE_NAMES)
    # One per payload family of section 9.4 (and the demo league's refusal, which is its own).
    assert set(on_disk) >= {
        "leagues", "team_matchup", "game_matchup", "defense_by_position",
        "defense_by_position_table", "slate_projections", "game_projection_detail",
        "projection_review", "availability_report", "availability_review_queue", "news",
        "sources", "model_settings",
    }  # fmt: skip


def test_every_committed_file_parses_and_is_in_the_canonical_form(
    committed: dict[str, Any],
) -> None:
    assert set(committed) == set(fixtures_export.FIXTURE_NAMES)
    for name, document in committed.items():
        text = (NBA_DIR / f"{name}.json").read_text(encoding="utf-8")
        assert text == fixtures_export.render(document), f"{name}.json is not in canonical form"
        assert text.endswith("\n") and not text.endswith("\n\n")


def test_every_fixture_says_which_league_it_is(committed: dict[str, Any]) -> None:
    for name, document in committed.items():
        if name == "leagues":  # a list of every league's row, each of which names itself
            assert [row["key"] for row in document] == ["nba", "euroleague"]
            continue
        assert isinstance(document, dict), name
        assert document.get("league") == "nba", name


def test_every_fixture_says_it_is_a_demo(committed: dict[str, Any]) -> None:
    for name, document in committed.items():
        if name in {"leagues", "availability_review_queue"}:  # no freshness block to carry it
            continue
        assert document["freshness"]["isDemo"] is True, name
    assert committed["leagues"][0]["isDemo"] is True
    for name, document in committed.items():
        if name == "leagues":  # the EuroLeague's row says it is not running, which is not a demo
            continue
        flags = values(document, "isDemo")
        assert all(flag is True for flag in flags), name


def test_no_fixture_key_carries_a_word_the_product_does_not_define(
    committed: dict[str, Any],
) -> None:
    for name, document in committed.items():
        violations = market_guard.scan_keys(document, root=name)
        assert violations == [], [str(v) for v in violations]
        for key, _ in walk(document):
            assert "rank" not in market_guard.split_words(key), (
                name,
                key,
            )  # no standings, no ranks


def test_nothing_in_a_fixture_is_real(committed: dict[str, Any]) -> None:
    players: set[int] = set()
    for name, document in committed.items():
        for key, value in walk(document):
            if isinstance(value, str) and value.startswith(URL):
                host = value.split("/")[2]
                assert host.endswith(".example.org") or host == "example.org", (name, value)
            if key == "playerId":
                assert value in INVENTED_PLAYER_IDS, (name, key, value)
                players.add(value)
            if key == "headshotUrl":
                assert value is None, (name, value)
            if key == "id" and isinstance(value, str) and value.isdigit() and len(value) > 7:
                # Team ids are the thirty real franchises' (10 digits); a seven-digit-plus
                # player id must be an invented one.
                assert len(value) == 10 or int(value) in INVENTED_PLAYER_IDS, (name, value)
    assert len(players) >= 5, "the fixtures should exercise more than one invented player"


def test_the_fixtures_between_them_show_every_state_a_client_must_draw(
    committed: dict[str, Any],
) -> None:
    report = committed["availability_report"]
    assert report["state"] == "fresh" and report["asOf"] is not None
    states = {team["reportState"] for team in report["teams"]}
    assert states == {"submitted", "notYetSubmitted", "noReport"}
    entries = [entry for team in report["teams"] for entry in team["entries"]]
    assert any(e["isOverride"] and e["source"]["kind"] == "manual" for e in entries)
    assert any(
        e["player"] is None for e in entries
    ), "a name that matched nobody is shown as printed"
    assert any(e["source"]["kind"] == "leagueReport" and e["inForce"] for e in entries)
    assert {e["status"] for e in entries} >= {"out", "doubtful", "questionable", "probable"}
    assert all(e["ageMinutes"] >= 0 and e["source"]["publishedAt"] for e in entries)
    assert report["news"], "includeNews attaches the headlines"
    assert committed["availability_review_queue"]["items"], "the queue shows the unmatched name"

    demo = committed["availability_report_demo"]
    assert demo["state"] == "disabled" and "Demo league" in demo["message"]
    assert all(
        team["entries"] == [] and team["reportState"] == "noReport" for team in demo["teams"]
    )

    detail = committed["game_projection_detail"]
    assert detail["current"]["model"]["kind"] == "latest"
    assert detail["locked"]["model"]["kind"] == "locked"
    assert [h["kind"] for h in detail["history"]] == ["latest", "locked"]
    assert detail["current"]["marginRange80"] is not None
    assert (
        detail["current"]["intervalBasis"] == "assumed"
    )  # the exporter types the spread in by hand
    assert detail["locked"]["assumptions"]["assumedAvailable"]["basis"] is None  # not frozen
    slate = committed["slate_projections"]
    with_absences = [
        g for g in slate["games"] if g["home"]["keyAbsences"] or g["away"]["keyAbsences"]
    ]
    assert with_absences and {
        g["assumptions"]["assumedAvailable"]["basis"] for g in slate["games"]
    } >= {
        "notOnSubmittedReport",
        "teamReportPending",
        "noReportPublished",
    }

    review = committed["projection_review"]
    assert review["byModel"] and review["reconstructed"] is not None
    assert {row["kind"] for row in review["games"]} == {"locked", "reconstructed"}
    assert committed["game_matchup"]["projection"]["model"]["kind"] == "reconstructed"

    table = committed["defense_by_position_table"]
    assert any(t["provisional"] for t in table["teams"])
    assert table["methodMessage"] and "No team's points allowed" in table["methodMessage"]
    papg = [t["pointsAllowedPerGame"] for t in table["teams"]]
    assert papg == sorted(papg)
    one = committed["defense_by_position"]
    assert round(sum(b["pointsAllowedPerGame"] for b in one["buckets"]), 9) == round(
        one["pointsAllowedPerGame"], 9
    )  # the buckets are the headline, in the committed body too
    assert one["reconciliation"]["unreconciledGames"] == 0

    leagues = committed["leagues"]
    assert leagues[0]["enabled"] is True and leagues[1]["enabled"] is False
    sources = {s["key"] for s in committed["sources"]["sources"]}
    assert {"nba.stats", "nba.rosters", "nba.injuryReport"} <= sources
    assert len(committed["model_settings"]["settings"]) == 16
    notice = committed["sources"]["dayOneNotice"]
    assert notice.startswith("Demo league") and "invented" in notice
    assert detail["availability"] == "estimated" == slate["availability"]


def test_the_existing_fixture_checks_never_see_these_files() -> None:
    """The original fixtures' checks glob ``contracts/fixtures/*.json`` without recursing."""
    assert NBA_DIR.is_relative_to(FIXTURES) and NBA_DIR != FIXTURES
    seen = set(FIXTURES.glob("*.json"))
    assert seen, "the original fixtures should be there to be checked"
    assert not [path for path in seen if path.is_relative_to(NBA_DIR)]
    assert not list(FIXTURES.glob("*/*.json")), "nothing sits one level down either"


def test_building_twice_gives_the_same_bytes(documents: dict[str, Any]) -> None:
    again = fixtures_export.build_fixtures()
    assert {n: fixtures_export.render(d) for n, d in again.items()} == {
        n: fixtures_export.render(d) for n, d in documents.items()
    }


def test_a_build_leaves_the_process_as_it_found_it(documents: dict[str, Any]) -> None:
    """The exporter lifts the demo refusal, freezes the clock and switches the identity file off
    for its own build; none of that may outlive it, and a running EuroLeague is put back."""
    sentinel = league_registry.LeagueProvider("euroleague", lambda: None, lambda: {})
    original = league_registry.get("euroleague")
    league_registry.register(sentinel)
    allowed = availability_view.statuses_allowed
    utcnow = db_module.utcnow
    identities_env = os.environ.get(identities.IDENTITIES_PATH_ENV)
    try:
        fixtures_export.build_fixtures()
        assert league_registry.get("euroleague") is sentinel
        assert availability_view.statuses_allowed is allowed
        assert db_module.utcnow is utcnow
        assert abs((datetime.utcnow() - db_module.utcnow()).total_seconds()) < 5
        assert os.environ.get(identities.IDENTITIES_PATH_ENV) == identities_env
    finally:
        league_registry.unregister("euroleague")
        if original is not None:
            league_registry.register(original)


def test_check_mode_exits_zero_when_current_and_one_when_not(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    documents: dict[str, Any],
) -> None:
    # The command line is tested against the build the module already made, not a fresh one each
    # time; that the build equals the committed files is its own test above.
    monkeypatch.setattr(fixtures_export, "build_fixtures", lambda *args, **kwargs: documents)
    assert fixtures_export.main(["--check"]) == 0
    assert "up to date" in capsys.readouterr().out
    # An empty directory is every file missing; a directory with one stale file is a difference.
    assert fixtures_export.main(["--check", "--out", str(tmp_path)]) == 1
    capsys.readouterr()
    assert fixtures_export.main(["--write", "--out", str(tmp_path)]) == 0
    assert sorted(p.stem for p in tmp_path.glob("*.json")) == sorted(fixtures_export.FIXTURE_NAMES)
    assert fixtures_export.main(["--check", "--out", str(tmp_path)]) == 0
    (tmp_path / "sources.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "stray.json").write_text("{}\n", encoding="utf-8")
    assert fixtures_export.main(["--check", "--out", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "sources.json" in err and "stray.json" in err


def test_write_removes_a_file_the_exporter_no_longer_produces(
    tmp_path: Path, documents: dict[str, Any]
) -> None:
    fixtures_export.write_fixtures(tmp_path, documents)
    (tmp_path / "retired.json").write_text("{}\n", encoding="utf-8")
    fixtures_export.write_fixtures(tmp_path, documents)
    assert not (tmp_path / "retired.json").exists()
