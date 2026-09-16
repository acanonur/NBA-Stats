"""Tests for the real-identity index — and for the line it refuses to cross.

Two things are being checked here, and only one of them is ordinary. The ordinary one is the
API: lookup, search, headshots, caching, degradation. The other is that the bundled file is
*actually real*: the known-id assertions below (Luka Dončić is 1629029, LeBron James 2544)
fail loudly if someone ever regenerates ``data/nba_identities.json`` from invented data,
which would turn the app's one factual claim into a quiet fiction.

The file's own provenance block is tested too, because it is the contract: names, ids,
franchises and headshot URLs are stated; rosters, positions, measurements and statistics are
refused. :mod:`nbastats.seed` reads the first list and generates everything in the second.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from nbastats import identities

# The four ids the rest of the suite leans on. These are real NBA person ids; if any of them
# stops resolving to the right person, the identity file is no longer the NBA's.
LUKA_DONCIC = 1629029
LEBRON_JAMES = 2544
NIKOLA_JOKIC = 203999
VICTOR_WEMBANYAMA = 1641705
MICHAEL_JORDAN = 893


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch: pytest.MonkeyPatch):
    """Every test starts from the bundled file and an empty cache."""
    monkeypatch.delenv(identities.IDENTITIES_PATH_ENV, raising=False)
    identities.reset_cache()
    yield
    identities.reset_cache()


# --------------------------------------------------------------------------- the file


def test_the_bundled_file_loads_with_the_expected_shape() -> None:
    index = identities.index()
    assert not index.is_empty
    assert index.source_path is not None and index.source_path.is_file()

    # The 30 franchises, exactly — the NBA has 30 and the file states all of them.
    assert len(identities.teams()) == 30
    assert len({team.team_id for team in identities.teams()}) == 30
    assert all(1610612737 <= team.team_id <= 1610612766 for team in identities.teams())

    # Every person who has ever played: thousands, not hundreds. The bundled snapshot holds
    # 5,103 of them, so the band below is generous in both directions without being vacuous.
    people = identities.players()
    assert 5_000 <= len(people) <= 20_000
    assert len({person.player_id for person in people}) == len(people)


def test_the_active_and_historical_pools_partition_the_file() -> None:
    active = identities.active_players()
    historical = identities.historical_players()
    everyone = identities.players()

    assert len(active) + len(historical) == len(everyone)
    assert {p.player_id for p in active}.isdisjoint({p.player_id for p in historical})
    # A current league is a few hundred people; the historical list dwarfs it.
    assert 300 <= len(active) <= 900
    assert len(historical) > len(active) * 3

    assert identities.player(VICTOR_WEMBANYAMA) in active
    assert identities.player(MICHAEL_JORDAN) in historical


def test_the_provenance_block_states_what_the_file_will_not_claim() -> None:
    """The honesty boundary, read back out of the file that declares it."""
    provenance = identities.provenance()
    refused = " ".join(provenance["notProvided"]).lower()
    assert "team" in refused and "position" in refused and "statistic" in refused
    stated = " ".join(provenance["factual"]).lower()
    assert "person id" in stated and "name" in stated

    # No player row carries a team, a position or a measurement, whatever the block says.
    raw = json.loads(Path(identities.identities_path()).read_text(encoding="utf-8"))
    forbidden = {"teamId", "team", "teamAbbr", "position", "pos", "height", "weight", "pts"}
    for row in raw["players"][:200]:
        assert forbidden.isdisjoint(row.keys())


# --------------------------------------------------------------------------- lookup


def test_known_person_ids_resolve_to_the_right_people() -> None:
    """The check that the file is real. These four ids are NBA.com's own."""
    assert identities.player(LUKA_DONCIC).name == "Luka Dončić"
    assert identities.player(LEBRON_JAMES).name == "LeBron James"
    assert identities.player(NIKOLA_JOKIC).name == "Nikola Jokić"
    assert identities.player(VICTOR_WEMBANYAMA).name == "Victor Wembanyama"

    luka = identities.player(LUKA_DONCIC)
    assert luka.first_name == "Luka" and luka.last_name == "Dončić"
    assert luka.is_active
    assert identities.player(MICHAEL_JORDAN).name == "Michael Jordan"


def test_an_unknown_id_is_none_rather_than_an_invention() -> None:
    assert identities.player(9_000_001) is None  # a seeded fallback id
    assert identities.player(-1) is None
    assert identities.player("not-an-id") is None
    assert identities.headshot_url(9_000_001) is None


def test_teams_are_indexed_by_abbreviation_case_insensitively() -> None:
    lakers = identities.team("lal")
    assert lakers is not None
    assert lakers.team_id == 1610612747
    assert lakers.name == "Los Angeles Lakers"
    assert identities.team("LAL") == lakers
    assert identities.team("XXX") is None
    # Conference and division are not in the file, and the record does not pretend they are.
    assert not hasattr(lakers, "conference")
    assert not hasattr(lakers, "division")


# --------------------------------------------------------------------------- search


@pytest.mark.parametrize(
    "query, expected",
    [
        ("Doncic", LUKA_DONCIC),
        ("doncic", LUKA_DONCIC),
        ("Luka Doncic", LUKA_DONCIC),
        ("luka doncic", LUKA_DONCIC),
        ("Dončić", LUKA_DONCIC),
        ("Jokic", NIKOLA_JOKIC),
        ("nikola jokic", NIKOLA_JOKIC),
        ("Jokić", NIKOLA_JOKIC),
        ("LeBron James", LEBRON_JAMES),
        ("lebron", LEBRON_JAMES),
        ("Wembanyama", VICTOR_WEMBANYAMA),
    ],
)
def test_search_is_diacritic_insensitive(query: str, expected: int) -> None:
    """Typing "Doncic" has to find "Dončić" — nobody types the háčeks."""
    hits = identities.find(query)
    assert hits, f"{query!r} found nobody"
    assert hits[0].player_id == expected


def test_folding_handles_the_marks_and_punctuation_names_actually_carry() -> None:
    assert identities.fold_name("Luka Dončić") == "luka doncic"
    assert identities.fold_name("Nikola Jokić") == "nikola jokic"
    assert identities.fold_name("Jusuf Nurkić") == "jusuf nurkic"
    assert identities.fold_name("A.J. Lawson") == "aj lawson"
    assert identities.fold_name("Shaquille O'Neal") == "shaquille oneal"
    assert identities.fold_name("Shai Gilgeous-Alexander") == "shai gilgeous alexander"
    assert identities.fold_name("  MIKE   BIBBY ") == "mike bibby"
    assert identities.fold_name("") == ""


def test_search_ranks_the_exact_person_first_and_respects_its_limits() -> None:
    # A surname shared by many: the full name still wins outright.
    hits = identities.find("LeBron James")
    assert hits[0].player_id == LEBRON_JAMES

    james = identities.find("James", limit=5)
    assert len(james) == 5
    assert all("james" in identities.fold_name(p.name) for p in james)

    assert identities.find("", limit=5) == ()
    assert identities.find("Doncic", limit=0) == ()
    assert identities.find("zzzzzznobody") == ()

    active_only = identities.find("James", limit=8, active_only=True)
    assert active_only and all(p.is_active for p in active_only)

    # Stable for a given file: the same query twice is the same answer.
    assert identities.find("Smith", limit=6) == identities.find("Smith", limit=6)


# --------------------------------------------------------------------------- headshots


def test_headshot_url_is_the_documented_cdn_pattern() -> None:
    pattern = identities.provenance()["headshotUrlPattern"]
    assert pattern == "https://cdn.nba.com/headshots/nba/latest/1040x760/{id}.png"

    assert identities.headshot_url(LEBRON_JAMES) == (
        "https://cdn.nba.com/headshots/nba/latest/1040x760/2544.png"
    )
    assert identities.headshot_url(LUKA_DONCIC) == pattern.replace("{id}", str(LUKA_DONCIC))
    assert identities.headshot_url(VICTOR_WEMBANYAMA) == (
        identities.HEADSHOT_URL_PATTERN.format(player_id=VICTOR_WEMBANYAMA)
    )

    # It is a pure function of the person id for everyone in the file, which is why the URL
    # can be shipped as factual at all.
    for person in identities.players()[:300]:
        assert person.headshot_url.endswith(f"/{person.player_id}.png")


# --------------------------------------------------------------------------- degradation


def test_a_missing_file_degrades_to_an_empty_index_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A packaging mistake costs the app its photos, never its availability."""
    monkeypatch.setenv(identities.IDENTITIES_PATH_ENV, str(tmp_path / "gone.json"))
    identities.reset_cache()

    with caplog.at_level(logging.WARNING, logger="nbastats.identities"):
        index = identities.index()

    assert index.is_empty
    assert "not found" in caplog.text.lower()
    assert identities.teams() == ()
    assert identities.players() == ()
    assert identities.active_players() == ()
    assert identities.historical_players() == ()
    assert identities.player(LEBRON_JAMES) is None
    assert identities.headshot_url(LEBRON_JAMES) is None
    assert identities.find("Doncic") == ()
    assert identities.team("LAL") is None
    assert identities.provenance() == {}


@pytest.mark.parametrize(
    "body",
    [
        "{not json at all",
        "[]",
        '{"schemaVersion": 1}',
        '{"teams": "thirty", "players": []}',
        '{"teams": [], "players": {"1": "Luka"}}',
    ],
)
def test_a_malformed_file_degrades_the_same_way(
    body: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "broken.json"
    path.write_text(body, encoding="utf-8")
    monkeypatch.setenv(identities.IDENTITIES_PATH_ENV, str(path))
    identities.reset_cache()

    with caplog.at_level(logging.WARNING, logger="nbastats.identities"):
        index = identities.index()

    assert index.is_empty or index.players == ()
    assert caplog.records, "a file that cannot be used has to say so"
    assert identities.find("Doncic") == ()


def test_unusable_rows_are_skipped_rather_than_failing_the_load(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "partial.json"
    path.write_text(
        json.dumps(
            {
                "provenance": {"factual": ["names"]},
                "teams": [
                    {"teamId": 1610612747, "abbr": "LAL", "name": "Los Angeles Lakers",
                     "city": "Los Angeles", "nickname": "Lakers", "state": "California",
                     "yearFounded": 1948},
                    {"abbr": "???"},
                    "not a team",
                ],
                "players": [
                    {"playerId": 2544, "name": "LeBron James", "firstName": "LeBron",
                     "lastName": "James", "isActive": True},
                    {"playerId": None, "name": "Nobody"},
                    {"name": "No Id"},
                    42,
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(identities.IDENTITIES_PATH_ENV, str(path))
    identities.reset_cache()

    assert len(identities.teams()) == 1
    assert len(identities.players()) == 1
    # The headshot is derivable from the id, so a row missing it still gets the right URL.
    assert identities.headshot_url(2544) == (
        "https://cdn.nba.com/headshots/nba/latest/1040x760/2544.png"
    )


# --------------------------------------------------------------------------- caching


def test_the_index_is_cached_until_it_is_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    first = identities.index()
    assert identities.index() is first

    identities.reset_cache()
    assert identities.index() is not first
    assert identities.index().players[:5] == first.players[:5]


def test_the_demo_attribution_states_both_halves_of_the_boundary() -> None:
    """A reader of the footer has to learn which half of the demo is real."""
    from nbastats.api.routes_meta import ATTRIBUTION, DEMO_ATTRIBUTION

    lowered = DEMO_ATTRIBUTION.lower()
    # What is real.
    assert "name" in lowered and "id" in lowered and "headshot" in lowered
    assert "real nba identities" in lowered
    # What is not.
    assert "generated" in lowered
    assert "team assignments" in lowered and "statistic" in lowered
    # And the standing disclaimer, which the demo still owes the NBA.
    assert "not endorsed by or affiliated with the nba" in lowered
    assert DEMO_ATTRIBUTION != ATTRIBUTION


def test_the_env_override_selects_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    default = identities.identities_path()
    assert default.name == "nba_identities.json"
    assert default.parent.name == "data"

    monkeypatch.setenv(identities.IDENTITIES_PATH_ENV, str(tmp_path / "elsewhere.json"))
    assert identities.identities_path() == tmp_path / "elsewhere.json"

    monkeypatch.setenv(identities.IDENTITIES_PATH_ENV, "   ")
    assert identities.identities_path() == default
