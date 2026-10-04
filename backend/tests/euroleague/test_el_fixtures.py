"""The committed EuroLeague payload fixtures are generated, invented, and kept in step.

``contracts/fixtures/leagues/el/*.json`` are one recorded response of every EuroLeague payload
family, so a client can decode against a real body instead of prose. Three promises keep them
honest, and each is a test here:

* **Reproducible.** A fresh build from the invented demo league equals what is committed, byte for
  byte (the same discipline the NBA's fixtures hold). A change to a payload that forgets to
  regenerate them fails here, naming the file.
* **Invented.** Nothing in them is real: every club is a ``ZZ`` club, every link points at an
  ``example.org`` host, and the store says it is a demo. That is what lets them be committed to a
  public repository.
* **Clean.** No object key anywhere in them carries a word the product does not define
  (:mod:`nbastats.shared.market_guard`), and the NBA's own fixture checks, which glob
  ``contracts/fixtures/*.json`` without recursing, never see these files.

The build is slow (it seeds a league), so it is built once for the module.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nbastats.euroleague import bootstrap, fixtures_export
from nbastats.shared import market_guard

FIXTURES = Path(__file__).resolve().parents[3] / "contracts" / "fixtures"
EL_DIR = fixtures_export.FIXTURE_DIR
CLUB_KEYS = {"clubCode", "homeClubCode", "awayClubCode", "oppClubCode"}


@pytest.fixture(scope="module")
def documents():
    return fixtures_export.build_fixtures()


@pytest.fixture(scope="module")
def committed():
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8")) for path in EL_DIR.glob("*.json")
    }


def walk(value, key=None):
    """Every ``(key, value)`` pair in a body, at any depth."""
    if isinstance(value, dict):
        for name, child in value.items():
            yield name, child
            yield from walk(child, name)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child, key)


def test_the_committed_fixtures_are_what_a_fresh_build_produces(documents) -> None:
    assert fixtures_export.check_fixtures(EL_DIR, documents) == []


def test_the_files_are_exactly_the_families_the_design_names(documents) -> None:
    on_disk = sorted(path.stem for path in EL_DIR.glob("*.json"))
    assert on_disk == sorted(fixtures_export.FIXTURE_NAMES)
    assert list(documents) == list(fixtures_export.FIXTURE_NAMES)
    assert len(set(fixtures_export.FIXTURE_NAMES)) == len(fixtures_export.FIXTURE_NAMES)


def test_every_committed_file_parses_and_is_in_the_canonical_form(committed) -> None:
    assert set(committed) == set(fixtures_export.FIXTURE_NAMES)
    for name, document in committed.items():
        text = (EL_DIR / f"{name}.json").read_text(encoding="utf-8")
        assert text == fixtures_export.render(document), f"{name}.json is not in canonical form"
        assert text.endswith("\n") and not text.endswith("\n\n")


def test_every_fixture_says_which_league_it_is(committed) -> None:
    for name, document in committed.items():
        assert isinstance(document, dict), name
        assert document.get("league") == "euroleague", name


def test_every_fixture_says_it_is_a_demo(committed) -> None:
    """Wherever a body carries freshness it says ``isDemo``; the meta says so at top level too."""
    assert committed["meta"]["isDemo"] is True
    for name, document in committed.items():
        if name == "review_queue":  # a bare list of items: no freshness block to carry the flag
            continue
        assert document["freshness"]["isDemo"] is True, name
        flags = [value for key, value in walk(document) if key == "isDemo"]
        assert flags and all(flag is True for flag in flags), name


def test_no_fixture_key_carries_a_word_the_product_does_not_define(committed) -> None:
    for name, document in committed.items():
        violations = market_guard.scan_keys(document, root=name)
        assert violations == [], [str(v) for v in violations]


def test_nothing_in_a_fixture_is_real(committed) -> None:
    clubs: set[str] = set()
    for name, document in committed.items():
        for key, value in walk(document):
            if key in CLUB_KEYS and isinstance(value, str):
                assert value.startswith("ZZ"), (name, key, value)
                clubs.add(value)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                host = value.split("/")[2]
                assert host.endswith(".example.org") or host == "example.org", (name, value)
    assert len(clubs) >= 10, "the fixtures should exercise the whole invented league"


def test_the_nba_fixture_checks_never_see_these_files() -> None:
    """The NBA's checks glob ``contracts/fixtures/*.json`` without recursing."""
    assert EL_DIR.is_relative_to(FIXTURES) and EL_DIR != FIXTURES
    seen = set(FIXTURES.glob("*.json"))
    assert seen, "the NBA fixtures should be there to be checked"
    assert not [path for path in seen if path.is_relative_to(EL_DIR)]
    assert not list(FIXTURES.glob("*/*.json")), "nothing sits one level down either"


def test_building_twice_gives_the_same_bytes(documents) -> None:
    again = fixtures_export.build_fixtures()
    assert {n: fixtures_export.render(d) for n, d in again.items()} == {
        n: fixtures_export.render(d) for n, d in documents.items()
    }


def test_a_build_restores_the_bootstrap_state_it_found() -> None:
    sentinel = bootstrap.BootstrapResult(state="unconfigured", kind=None, is_demo=False)
    bootstrap.set_state(sentinel)
    try:
        fixtures_export.build_fixtures()
        assert bootstrap.get_state() is sentinel
    finally:
        bootstrap.reset_state()


def test_check_mode_exits_zero_when_current_and_one_when_not(tmp_path, capsys) -> None:
    assert fixtures_export.main(["--check"]) == 0
    assert "up to date" in capsys.readouterr().out
    # An empty directory is every file missing; a directory with one stale file is a difference.
    assert fixtures_export.main(["--check", "--out", str(tmp_path)]) == 1
    capsys.readouterr()
    assert fixtures_export.main(["--write", "--out", str(tmp_path)]) == 0
    assert sorted(p.stem for p in tmp_path.glob("*.json")) == sorted(fixtures_export.FIXTURE_NAMES)
    assert fixtures_export.main(["--check", "--out", str(tmp_path)]) == 0
    (tmp_path / "meta.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "stray.json").write_text("{}\n", encoding="utf-8")
    assert fixtures_export.main(["--check", "--out", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "meta.json" in err and "stray.json" in err


def test_write_removes_a_file_the_exporter_no_longer_produces(tmp_path, documents) -> None:
    fixtures_export.write_fixtures(tmp_path, documents)
    (tmp_path / "retired.json").write_text("{}\n", encoding="utf-8")
    fixtures_export.write_fixtures(tmp_path, documents)
    assert not (tmp_path / "retired.json").exists()
