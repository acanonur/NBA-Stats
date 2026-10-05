"""The shape of the shared package: pure, stdlib-only, free of gambling vocabulary, and wired right.

``nbastats/shared/`` is the one package both leagues import, which is exactly why it must be
the part that cannot reach a database, a web framework or the network: a calculation that
could would be a calculation whose answer depended on which league was loaded. These tests make
the purity a checked property rather than a habit.

* **Stdlib only, no I/O.** An AST walk over every module (not just top-level imports) allows
  only the standard library and the package itself, and refuses the standard-library modules
  that touch files, sockets, processes or the importer, so "file-free" is enforced too. No call
  to ``open`` either. (``zoneinfo`` is allowed: it is how a league's calendar day is computed.)
* **Nothing from the rest of ``nbastats``.** The core imports nothing outside itself, so it
  cannot grow a dependency on a database model or a route.
* **The prose scan.** ``test_web_release_hardening.py`` will grep this package's source for
  gambling vocabulary; the same pattern is applied here directly, so a docstring or a
  regenerated constants file that names the words fails in this suite and not only there.
* **No gambling identifiers.** Names in the code (functions, parameters, attributes) may not
  use the strongest of the words (a box-score "line" is legitimate vocabulary, so the weaker
  ones are exempt), and no half-point rounding of the form ``round(x * 2) / 2`` appears.

Then the two small modules that are about the package's wiring rather than its statistics:

* **League profiles**, against the design's table (section 2.2): the numbers that make the two
  leagues different, asserted as literals so that a quiet edit to one is a loud failure.
* **The league registry**, the late-bound seam by which the rest of the application asks the
  EuroLeague something without importing it: registration, replacement, lookups, the
  ``league_unavailable`` condition, and a provider that misbehaves.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import re
import sys
import threading
from pathlib import Path

import pytest

from nbastats.shared import league_profile as LP
from nbastats.shared import league_registry as LR
from nbastats.shared import market_guard

SHARED = Path(__file__).resolve().parents[2] / "nbastats" / "shared"
MODULES = sorted(SHARED.glob("*.py"))

DESIGNED_FILES = {
    "__init__",
    "league_profile",
    "positions",
    "availability",
    "team_form",
    "defense_position",
    "team_projection",
    "injury_layer",
    "refs",
    "market_guard",
    "league_registry",
    "_generated_leagues",
}

# standard-library modules that touch the file system, the network, processes or the importer
IO_MODULES = {
    "os",
    "pathlib",
    "io",
    "sqlite3",
    "socket",
    "ssl",
    "subprocess",
    "urllib",
    "http",
    "shutil",
    "tempfile",
    "glob",
    "asyncio",
    "importlib",
    "ctypes",
    "multiprocessing",
    "ftplib",
    "smtplib",
    "shelve",
    "pickle",
    "fileinput",
    "webbrowser",
    "xmlrpc",
    "selectors",
    "signal",
}


def top_level(name: str) -> str:
    return name.split(".")[0]


def test_every_designed_module_exists_and_nothing_else_does() -> None:
    assert {path.stem for path in MODULES} == DESIGNED_FILES


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_a_module_imports_only_the_standard_library_and_its_own_package(path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):  # every import, including ones inside functions
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = top_level(alias.name)
                if root not in sys.stdlib_module_names:
                    offenders.append(f"import {alias.name}")
                elif root in IO_MODULES:
                    offenders.append(f"import {alias.name} (touches I/O)")
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                module = node.module or ""
                root = top_level(module)
                if root == "nbastats":
                    if not module.startswith("nbastats.shared"):
                        offenders.append(f"from {module}")
                elif root not in sys.stdlib_module_names:
                    offenders.append(f"from {module} import ...")
                elif root in IO_MODULES:
                    offenders.append(f"from {module} import ... (touches I/O)")
            elif node.level == 1:
                if node.module and node.module not in DESIGNED_FILES:
                    offenders.append(f"from .{node.module} (not a shared module)")
            else:
                offenders.append(
                    f"relative import reaching outside the package (level {node.level})"
                )
    assert offenders == []


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_a_module_never_opens_a_file(path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            assert name not in {"open", "read_text", "write_text", "read_bytes", "write_bytes"}


def test_the_package_imports_cleanly_with_no_third_party_module_loaded() -> None:
    import subprocess

    code = (
        "import sys\n"
        "import nbastats.shared.availability, nbastats.shared.team_form, "
        "nbastats.shared.defense_position, nbastats.shared.team_projection, "
        "nbastats.shared.injury_layer, nbastats.shared.refs, nbastats.shared.market_guard, "
        "nbastats.shared.league_registry, nbastats.shared.positions\n"
        "banned = {'sqlalchemy', 'fastapi', 'pydantic', 'httpx', 'dateutil', 'numpy', 'scipy'}\n"
        "loaded = banned & set(sys.modules)\n"
        "assert not loaded, loaded\n"
        "foreign = [m for m in sys.modules\n"
        "           if m.startswith('nbastats.') and not m.startswith('nbastats.shared')]\n"
        "assert not foreign, foreign\n"
    )
    root = SHARED.parents[1]  # backend/
    done = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


# --------------------------------------------------------------------------- the prose scan

# The pattern of tests/test_web_release_hardening.py's vocabulary guard, plus the one word the
# design adds to it.
BANNED_PROSE = (
    r"odds",
    r"vig",
    r"kelly",
    r"wager\w*",
    r"sportsbook\w*",
    r"payout\w*",
    r"bett?ing",
    r"bookmaker\w*",
    r"parlay\w*",
    r"stake",
    r"staking",
    r"over/under",
    r"point spread",
    r"moneyline\w*",
)
PROSE = re.compile(r"\b(?:" + "|".join(BANNED_PROSE) + r")\b")


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_no_source_file_names_the_banned_vocabulary(path) -> None:
    found = sorted(set(PROSE.findall(path.read_text(encoding="utf-8").lower())))
    assert (
        found == []
    ), f"{path.name} mentions {found}; see _generated_leagues.py for how the list is spelled"


def test_the_scan_pattern_catches_what_it_is_meant_to() -> None:
    for word in (
        "odds",
        "vig",
        "wagering",
        "bookmakers",
        "parlays",
        "moneyline",
        "stake",
        "betting",
    ):
        assert PROSE.search(f"a note about {word} here"), word
    for fine in ("navigate", "mistake", "headline", "coverage", "market", "line", "spread of"):
        assert not PROSE.search(f"a note about {fine} here"), fine


# The strongest of the guarded words, which no identifier in the code may use. "line" (a
# player's box-score line), "spread" (a statistical spread), "over" and "under" are legitimate
# in code and are exempt here; the payload keys are what the market guard polices.
FORBIDDEN_IDENTIFIER_WORDS = market_guard.FORBIDDEN_KEY_WORDS - {
    "line",
    "lines",
    "spread",
    "over",
    "under",
    "push",
    "cover",
    "market",
}


def identifiers(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield node.name
        elif isinstance(node, ast.Name):
            yield node.id
        elif isinstance(node, ast.Attribute):
            yield node.attr
        elif isinstance(node, ast.arg):
            yield node.arg
        elif isinstance(node, ast.keyword) and node.arg:
            yield node.arg
        elif isinstance(node, ast.alias):
            yield node.asname or node.name.split(".")[-1]


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_no_identifier_uses_the_gambling_vocabulary(path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = sorted(
        {
            name
            for name in identifiers(tree)
            if set(market_guard.split_words(name)) & FORBIDDEN_IDENTIFIER_WORDS
        }
    )
    assert bad == []


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_no_half_point_rounding_anywhere(path) -> None:
    """``round(x * 2) / 2`` is how a total is snapped to a half point; it must not appear."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            right_two = isinstance(node.right, ast.Constant) and node.right.value == 2
            left = node.left
            if right_two and isinstance(left, ast.Call):
                func = left.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if name in {"round", "floor", "ceil"} and left.args:
                    arg = left.args[0]
                    assert not (
                        isinstance(arg, ast.BinOp)
                        and isinstance(arg.op, ast.Mult)
                        and any(
                            isinstance(s, ast.Constant) and s.value == 2
                            for s in (arg.left, arg.right)
                        )
                    ), f"{path.name}:{node.lineno} snaps a number to a half point"


def test_the_half_point_scan_catches_the_pattern() -> None:
    bad = ast.parse("y = round(x * 2) / 2")
    node = next(n for n in ast.walk(bad) if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div))
    assert isinstance(node.left, ast.Call)  # the shape the scan above looks for


# ------------------------------------------------------------------------- league profiles


def test_the_profiles_are_the_designed_table() -> None:
    nba, el = LP.NBA, LP.EUROLEAGUE
    assert (nba.key, el.key) == ("nba", "euroleague")
    assert (nba.name, el.name) == ("NBA", "EuroLeague")
    assert (nba.api_prefix, el.api_prefix) == ("/v1", "/v1/el")
    assert (nba.schedule_tz, el.schedule_tz) == ("America/New_York", "Europe/Berlin")
    assert (nba.regulation_minutes, el.regulation_minutes) == (48, 40)
    assert (nba.overtime_minutes, el.overtime_minutes) == (5, 5)
    assert (nba.team_regulation_seconds, el.team_regulation_seconds) == (14_400, 12_000)
    assert nba.per_modes == ("PerGame", "Totals", "Per36", "Per100")
    assert el.per_modes == ("PerGame", "Totals", "Per40")
    assert nba.position_buckets == el.position_buckets == ("G", "F", "C")
    assert nba.workbook_position_buckets == ()
    assert el.workbook_position_buckets == ("PG", "SG", "SF", "PF", "C")
    assert (nba.defense.min_games, nba.defense.provisional_below) == (10, 25)
    assert (el.defense.min_games, el.defense.provisional_below) == (6, 12)
    assert nba.defense.unknown_ceiling == el.defense.unknown_ceiling == 0.05
    assert nba.defense.league_sample_share == 0.8 and nba.defense.reliability_gate == 0.2
    assert nba.defense.familywise_alpha == 0.05
    assert nba.matchup.adjusted_min_games == el.matchup.adjusted_min_games == 5
    assert nba.matchup.adjusted_min_other_games == 2
    assert (
        nba.matchup.form_window_default,
        nba.matchup.form_window_min,
        nba.matchup.form_window_max,
    ) == (5, 3, 15)
    assert (nba.home_advantage_default, el.home_advantage_default) == (2.5, 3.5)
    assert (nba.home_advantage_basis, el.home_advantage_basis) == ("default", "workbook")
    assert nba.home_advantage_fit_min_games == 300 and el.home_advantage_fit_min_games is None
    assert (nba.tracks_neutral_sites, el.tracks_neutral_sites) == (False, True)
    expected_chance = {
        "out": 0.0,
        "doubtful": 0.25,
        "questionable": 0.5,
        "probable": 0.85,
        "available": 1.0,
    }
    assert nba.chance_table() == el.chance_table() == expected_chance
    assert list(nba.chance_table()) == list(LP.STATUS_ORDER)
    assert (nba.status_chance_basis, el.status_chance_basis) == ("default", "workbook")
    assert nba.stale_after == LP.StaleRules(in_window_minutes=60, outside_window_minutes=1440)
    assert el.stale_after == LP.StaleRules(max_age_days=7, stale_when_team_has_played=True)
    assert (
        nba.attribution == "Stats via NBA.com. Injury status from the NBA's official injury report."
    )
    assert el.attribution == (
        "EuroLeague statistics from the EuroLeague's data service. "
        "Availability researched from the linked sources."
    )


def test_team_time_is_five_players_of_floor_time() -> None:
    for profile in (LP.NBA, LP.EUROLEAGUE):
        assert profile.team_regulation_seconds == 5 * profile.regulation_minutes * 60
        assert profile.regulation_seconds == profile.regulation_minutes * 60
    assert LP.EUROLEAGUE.team_overtime_seconds == 1_500 and LP.NBA.team_overtime_seconds == 1_500
    assert LP.EUROLEAGUE.team_time_seconds() == 12_000
    assert LP.EUROLEAGUE.team_time_seconds(1) == 13_500  # the EuroLeague's 200 + 25 minutes
    assert LP.EUROLEAGUE.team_time_seconds(2) == 15_000
    assert LP.NBA.team_time_seconds(1) == 15_900
    with pytest.raises(ValueError):
        LP.NBA.team_time_seconds(-1)


def test_profiles_are_immutable_and_hashable() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        LP.NBA.regulation_minutes = 40  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        LP.NBA.defense.min_games = 1  # type: ignore[misc]
    assert hash(LP.NBA) != hash(LP.EUROLEAGUE) or LP.NBA != LP.EUROLEAGUE
    assert {LP.NBA, LP.EUROLEAGUE, LP.NBA} == {LP.NBA, LP.EUROLEAGUE}
    with pytest.raises(TypeError):
        LP.PROFILES["nba"] = LP.EUROLEAGUE  # type: ignore[index]


def test_the_chance_table_is_a_fresh_dict_each_time() -> None:
    table = LP.NBA.chance_table()
    table["out"] = 1.0
    assert LP.NBA.chance_table()["out"] == 0.0


def test_profile_lookup() -> None:
    assert LP.get_profile("nba") is LP.NBA and LP.get_profile("euroleague") is LP.EUROLEAGUE
    assert LP.LEAGUE_KEYS == ("nba", "euroleague")
    for bad in ("NBA", "el", "", None, 3):
        assert not LP.is_league_key(bad)
        with pytest.raises(LP.UnknownLeagueError):
            LP.get_profile(bad)
    assert issubclass(LP.UnknownLeagueError, ValueError)
    assert LP.is_league_key("nba") and LP.is_league_key("euroleague")


def test_the_contract_form_is_json_ready_and_camel_cased() -> None:
    for profile in (LP.NBA, LP.EUROLEAGUE):
        contract = profile.to_contract()
        assert json.loads(json.dumps(contract)) == contract
        assert contract["teamRegulationSeconds"] == profile.team_regulation_seconds
        assert contract["defense"]["minGames"] == profile.defense.min_games
        assert contract["defense"]["provisionalBelowGames"] == profile.defense.provisional_below
        assert contract["statusChance"]["probable"] == 0.85
        assert market_guard.scan_keys(contract) == []

        def keys(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield key
                    yield from keys(child)

        assert all(re.fullmatch(r"[a-z][A-Za-z0-9]*|[a-z]+", k) for k in keys(contract)), contract
    assert LP.NBA.to_contract()["homeAdvantageFitMinGames"] == 300
    assert LP.EUROLEAGUE.to_contract()["homeAdvantageFitMinGames"] is None
    assert LP.EUROLEAGUE.to_contract()["staleAfter"]["maxAgeDays"] == 7


# ---------------------------------------------------------------------- the league registry


@pytest.fixture(autouse=True)
def clean_registry():
    LR.clear()
    yield
    LR.clear()


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.closed = True


def provider(key="euroleague", *, describe=None, team_exists=None, factory=None):
    return LR.LeagueProvider(
        key=key,
        session_factory=factory or FakeSession,
        describe=describe
        or (
            lambda: {
                "enabled": True,
                "state": "ok",
                "isDemo": True,
                "currentSeason": "E2026",
                "syncVersion": 4,
            }
        ),
        team_exists=team_exists,
    )


def test_nothing_is_registered_to_begin_with() -> None:
    assert LR.registered_keys() == ()
    assert LR.get("euroleague") is None and not LR.is_registered("euroleague")


def test_register_get_and_unregister() -> None:
    p = provider()
    LR.register(p)
    assert LR.get("euroleague") is p and LR.is_registered("euroleague")
    assert LR.registered_keys() == ("euroleague",)
    LR.register(provider("nba"))
    assert LR.registered_keys() == ("nba", "euroleague")  # canonical order, not insertion order
    LR.unregister("euroleague")
    assert LR.registered_keys() == ("nba",)
    LR.unregister("euroleague")  # unregistering twice is fine
    LR.unregister("never-heard-of-it")


def test_registering_again_replaces_the_provider() -> None:
    first, second = provider(), provider()
    LR.register(first)
    LR.register(second)
    assert LR.get("euroleague") is second


def test_an_unknown_league_cannot_register() -> None:
    with pytest.raises(ValueError, match="unknown league"):
        LR.register(provider("wnba"))
    assert LR.registered_keys() == ()


def test_session_for_opens_a_new_session_the_caller_closes() -> None:
    LR.register(provider())
    with LR.session_for("euroleague") as session:
        assert isinstance(session, FakeSession) and not session.closed
    assert session.closed
    assert LR.session_for("euroleague") is not LR.session_for("euroleague")


def test_session_for_an_unregistered_league_is_league_unavailable() -> None:
    with pytest.raises(LR.LeagueUnavailableError) as caught:
        LR.session_for("euroleague")
    assert caught.value.code == "league_unavailable"
    assert caught.value.key == "euroleague"
    assert "not available" in caught.value.reason
    assert isinstance(caught.value, LookupError)


def test_a_store_that_cannot_be_opened_is_league_unavailable_too() -> None:
    def broken():
        raise RuntimeError("database is locked")

    LR.register(provider(factory=broken))
    with pytest.raises(LR.LeagueUnavailableError, match="database is locked") as caught:
        LR.session_for("euroleague")
    assert caught.value.code == "league_unavailable"
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_league_entry_for_an_unregistered_league_is_a_complete_disabled_row() -> None:
    entry = LR.league_entry("euroleague")
    assert entry["key"] == "euroleague" and entry["enabled"] is False
    assert entry["state"] == "unavailable" and entry["reason"]
    assert entry["apiPrefix"] == "/v1/el" and entry["name"] == "EuroLeague"
    assert entry["regulationMinutes"] == 40 and entry["perModes"] == ["PerGame", "Totals", "Per40"]
    assert entry["positionBuckets"] == ["G", "F", "C"]
    assert entry["isDemo"] is False and entry["currentSeason"] is None
    assert entry["syncVersion"] is None and entry["dataThrough"] is None and entry["features"] == []
    assert set(entry) == {
        "key",
        "name",
        "apiPrefix",
        "enabled",
        "state",
        "reason",
        "isDemo",
        "currentSeason",
        "syncVersion",
        "dataThrough",
        "regulationMinutes",
        "perModes",
        "positionBuckets",
        "features",
    }
    assert json.loads(json.dumps(entry)) == entry


def test_league_entry_for_a_registered_league_uses_its_own_description() -> None:
    LR.register(
        provider(
            describe=lambda: {
                "enabled": True,
                "state": "ok",
                "reason": None,
                "isDemo": True,
                "currentSeason": "E2026",
                "syncVersion": 4,
                "dataThrough": "2026-09-29",
                "features": ["matchup", "defense"],
            }
        )
    )
    entry = LR.league_entry("euroleague")
    assert entry["enabled"] is True and entry["state"] == "ok" and entry["isDemo"] is True
    assert entry["currentSeason"] == "E2026" and entry["syncVersion"] == 4
    assert entry["features"] == ["matchup", "defense"]
    assert entry["regulationMinutes"] == 40  # still filled from the profile


def test_a_provider_cannot_rename_or_reroute_itself() -> None:
    LR.register(provider(describe=lambda: {"key": "nba", "apiPrefix": "/v1", "enabled": True}))
    entry = LR.league_entry("euroleague")
    assert entry["key"] == "euroleague" and entry["apiPrefix"] == "/v1/el"


def test_a_describe_that_raises_yields_an_error_row_not_an_exception() -> None:
    def explode():
        raise ValueError("store is on fire")

    LR.register(provider(describe=explode))
    entry = LR.league_entry("euroleague")
    assert entry["enabled"] is False and entry["state"] == "error"
    assert "store is on fire" in entry["reason"]


def test_league_entry_rejects_an_unknown_league() -> None:
    with pytest.raises(LP.UnknownLeagueError):
        LR.league_entry("wnba")


def test_team_exists_says_when_it_cannot_say() -> None:
    assert LR.team_exists("euroleague", "ZZA") is None  # not registered
    LR.register(provider())
    assert LR.team_exists("euroleague", "ZZA") is None  # no checker
    LR.register(provider(team_exists=lambda code: code in {"ZZA", "ZZB"}))
    assert LR.team_exists("euroleague", "ZZA") is True
    assert LR.team_exists("euroleague", "ZZZ") is False

    def explode(code):
        raise RuntimeError("no store")

    LR.register(provider(team_exists=explode))
    assert LR.team_exists("euroleague", "ZZA") is None  # a failed check is not "does not exist"


def test_the_registry_survives_concurrent_registration() -> None:
    errors: list[BaseException] = []

    def hammer(i: int) -> None:
        try:
            for _ in range(200):
                LR.register(provider())
                LR.get("euroleague")
                LR.registered_keys()
                if i % 2:
                    LR.unregister("euroleague")
        except BaseException as exc:  # pragma: no cover - only on a real race
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
