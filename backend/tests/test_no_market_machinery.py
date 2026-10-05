"""Betting machinery has nowhere to live: the structural guards, as tests.

Hardwood computes projected scores, margins and winners, and reports how those compared with what
happened. It does not compute, accept or return anything that exists only to be laid against a
price: no line, no probability of beating a number, no edge, no lean, no pick, no odds, and no
probability of winning (omitted in this version, which is a design choice and not a user
decision). A rule that lives only in a document erodes the first time someone adds a convenient
field, so each place such a thing could be written down is closed by a test, and this file is the
whole set. The vocabulary of the guard is :mod:`nbastats.shared.market_guard`; the lists it reads
are generated from ``contracts/tools/gen_leagues.py``.

The six closures (design section 8.7) and what each one reads
-------------------------------------------------------------
(a) **No payload key.** Every JSON object key, split into camelCase words, is checked against the
    forbidden words in three places: the contract files (``leagues.json``, the EuroLeague block of
    ``metrics.json``), every fixture under ``contracts/fixtures/leagues/`` and each league widget
    fixture, and **every response from every new GET route** in an application started over the
    seeded NBA store and the synthetic EuroLeague demo. The existing fixtures (a scoreboard
    ``line``, a draft ``pick``, a fantasy ``poolSpread``) are out of scope by construction: the new
    routes never reuse those payloads.
(b) **No parameter that could carry a price.** The OpenAPI document of the running application is
    walked for every new path, and every query, path and body field name must be on the allowed
    list, so no route accepts an external number to compare with a projection.
(c) **No setting that could hold one.** Both model-setting key sets (module constant, database
    CHECK constraint and generated schema file) equal the design's allowlists exactly, and none
    of the workbook's betting settings (``TotalSD``, ``PSDBase``, ``PSDSlope``, ``EdgeP``) has a
    key.
(d) **The importer never reads a betting cell.** Its read map contains none of the workbook's
    betting headers or settings; and a synthetic workbook that carries those columns and settings
    with sentinel values is imported and every table, cell and byte of the store is searched for
    them. The workbook is built by the EuroLeague tests' own builder (loaded from the file, because
    a fixture in ``tests/euroleague/conftest.py`` is not visible here), and this test refuses to
    pass if the builder ever stops writing the sentinels.
(e) **No half-point rounding.** An AST scan of the new packages for the spreadsheet's
    ``ROUND(x*2,0)/2`` in any of its Python spellings: a total rounded to .5 is the one number a
    bookmaker would quote, and this is how one would be made.
(f) **No probability of winning.** Not as a payload key, not as a fractional value under a key
    that says "win", not as a database column, not as a field of a shared dataclass.

What is deliberately not asserted
---------------------------------
That the allowed-parameter list is *sufficient* (a route that needs a new name must change the
list and the reasoning together), and that a free-text field contains no betting vocabulary
(a headline's text is a string, not a field the product defines; the web release's prose guard
reads the source text instead).
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import random
import re
import struct
import sys
import zipfile
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import CheckConstraint, select, text
from sqlalchemy.orm import Session

from nbastats import config
from nbastats.api import app as app_module
from nbastats.api import deps
from nbastats.models import Base, Game, Player, Team
from nbastats.shared import market_guard

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
PACKAGE = BACKEND / "nbastats"
CONTRACTS = REPO / "contracts"
FIXTURES = CONTRACTS / "fixtures"

# --------------------------------------------------------------------------- the design's lists
# Written out here on purpose, so the guard's own definition cannot be edited in one place and
# silently agree with itself. These are design section 8.7, section 8.6 and section 10.2.

DESIGN_FORBIDDEN_KEY_WORDS = frozenset(
    {
        "line", "lines", "odds", "moneyline", "spread", "over", "under", "lean", "edge", "pick",
        "picks", "push", "implied", "cover", "vig", "juice", "stake", "wager", "bookmaker",
        "market", "parlay", "handicap", "ats", "probability",
    }
)  # fmt: skip

STATUS_KEYS = ("out", "doubtful", "questionable", "probable", "available")

DESIGN_EL_SETTING_KEYS = frozenset(
    {
        "priorRegression", "homeAdvantagePoints", "teamSd", "marginSd", "replacementPer40",
        "absorbShare", "boostCap", "rotationShare", "formWeight", "capPolicyConsistent",
        "squadReconcile", "overtimeScaling", "positionCoverageCeiling",
        *(f"roundWeight.{n}" for n in range(1, 41)),
        *(f"statusChance.{status}" for status in STATUS_KEYS),
    }
)  # fmt: skip

DESIGN_NBA_SETTING_KEYS = frozenset(
    {
        "priorRegression", "priorWeightGames", "leagueLevelWeight", "homeAdvantagePoints",
        "teamSd", "marginSd", "replacementShare", "absorbShare", "boostCap",
        "capPolicyConsistent", "positionCoverageCeiling",
        *(f"statusChance.{status}" for status in STATUS_KEYS),
    }
)  # fmt: skip

#: The workbook's betting columns and the one it computes a probability of winning in.
DESIGN_IGNORED_HEADERS = frozenset(
    {"Model line", "Your line", "P(over)", "Lean", "Result v line", "Home win %"}
)
#: The workbook settings that exist only to feed a probability of beating a number, or a lean.
DESIGN_IGNORED_SETTINGS = frozenset({"TotalSD", "PSDBase", "PSDSlope", "EdgeP"})

#: Paths this guard calls "new": the league features of design section 9 (the EuroLeague's whole
#: router, and the NBA's matchup, defence, projection, availability, news, league and sources
#: routes). Existing routes that happen to share a word (``/v1/teams/{teamId}``) do not match.
NEW_PATH_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"^/v1/el(/|$)",
        r"^/v1/matchups(/|$)",
        r"^/v1/teams/\{[^}/]+\}/matchup$",
        r"/defense-by-position$",
        r"^/v1/availability(/|$)",
        r"^/v1/news(/|$)",
        r"^/v1/projections(/|$)",
        r"^/v1/games/\{[^}/]+\}/(projection|matchup)$",
        r"^/v1/leagues$",
        r"^/v1/sources$",
        r"^/v1/model-settings$",
    )
)

LEAGUE_WIDGET_FIXTURES = tuple(
    FIXTURES / f"widget_{kind}.json"
    for kind in ("team_matchup", "defense_by_position", "availability_report", "slate_projections")
)


def _is_new_path(path: str) -> bool:
    return any(pattern.search(path) for pattern in NEW_PATH_PATTERNS)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_by_path(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# =========================================================================== the guard itself


def test_the_guard_words_are_the_designed_words_in_every_place_they_are_kept() -> None:
    """One definition, four copies of it (the generator, ``leagues.json``, the generated Python
    constants and the guard's frozenset) and the design's own list, all the same."""
    generator = _load_by_path("hardwood_gen_leagues", CONTRACTS / "tools" / "gen_leagues.py")
    document = _load_json(CONTRACTS / "leagues.json")

    assert set(generator.FORBIDDEN_PAYLOAD_KEY_WORDS) == DESIGN_FORBIDDEN_KEY_WORDS
    assert set(document["forbiddenPayloadKeyWords"]) == DESIGN_FORBIDDEN_KEY_WORDS
    assert market_guard.FORBIDDEN_KEY_WORDS == DESIGN_FORBIDDEN_KEY_WORDS
    assert len(document["forbiddenPayloadKeyWords"]) == len(DESIGN_FORBIDDEN_KEY_WORDS)

    assert list(generator.ALLOWED_PARAMETERS) == document["allowedParameters"]
    assert market_guard.ALLOWED_PARAMETERS == frozenset(document["allowedParameters"])
    assert len(set(document["allowedParameters"])) == len(document["allowedParameters"])


def test_the_committed_contract_files_are_what_the_generator_prints() -> None:
    """The same guarantee ``scripts/check_contracts.py`` check (m) gives in CI, run in-process so
    a drift is found by the test suite as well."""
    generator = _load_by_path("hardwood_gen_leagues_drift", CONTRACTS / "tools" / "gen_leagues.py")
    committed = (CONTRACTS / "leagues.json").read_text(encoding="utf-8")
    assert json.dumps(generator.build_document(), indent=2) + "\n" == committed
    generated = PACKAGE / "shared" / "_generated_leagues.py"
    assert generator.build_python() == generated.read_text(encoding="utf-8")


def test_the_prose_guard_and_the_generator_agree_about_the_banned_words() -> None:
    """The generated constants split seven words in two so the web release's prose guard does not
    trip on its own list. That only works while the generator's copy of the prose guard's list is
    the prose guard's list, so read the real one out of its source."""
    generator = _load_by_path("hardwood_gen_leagues_prose", CONTRACTS / "tools" / "gen_leagues.py")
    source = (BACKEND / "tests" / "test_web_release_hardening.py").read_text(encoding="utf-8")
    banned: list[str] | None = None
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "banned" for t in node.targets)
            and isinstance(node.value, ast.Tuple)
        ):
            banned = [ast.literal_eval(element) for element in node.value.elts]
    assert banned, "could not find the prose guard's banned list in test_web_release_hardening.py"
    assert set(banned) == set(generator.PROSE_GUARD_PATTERNS)

    pattern = re.compile(r"\b(?:" + "|".join(banned) + r")\b")
    text_of_constants = (PACKAGE / "shared" / "_generated_leagues.py").read_text(encoding="utf-8")
    assert pattern.findall(text_of_constants.lower()) == []


# =========================================================================== (a) payload keys


def _violations(label: str, document: Any) -> list[str]:
    return [f"{label}: {v}" for v in market_guard.scan_keys(document)]


def test_the_contract_files_carry_no_forbidden_key() -> None:
    """``leagues.json`` and the EuroLeague block of ``metrics.json`` are payload-shaped data the
    clients read; their keys are held to the same vocabulary. (The *values* of
    ``forbiddenPayloadKeyWords`` name the forbidden words, which is the one thing a value may
    do.)"""
    problems = _violations("leagues.json", _load_json(CONTRACTS / "leagues.json"))
    metrics = _load_json(CONTRACTS / "metrics.json")
    problems += _violations("metrics.json#/leagueMetrics", metrics["leagueMetrics"])
    problems += _violations(
        "metrics.json#/metrics/opp_pts", [m for m in metrics["metrics"] if m["key"] == "opp_pts"]
    )
    assert problems == [], "\n".join(problems)


LEAGUE_FIXTURE_FILES = sorted((FIXTURES / "leagues").rglob("*.json"))


def test_the_league_fixtures_were_found() -> None:
    """A guard that walks nothing proves nothing."""
    assert len(LEAGUE_FIXTURE_FILES) >= 10, [p.name for p in LEAGUE_FIXTURE_FILES]


@pytest.mark.parametrize(
    "path", LEAGUE_FIXTURE_FILES, ids=[str(p.relative_to(FIXTURES)) for p in LEAGUE_FIXTURE_FILES]
)
def test_a_league_fixture_carries_no_forbidden_key(path: Path) -> None:
    assert _violations(str(path.relative_to(FIXTURES)), _load_json(path)) == []


@pytest.mark.parametrize("path", LEAGUE_WIDGET_FIXTURES, ids=lambda p: p.name)
def test_a_league_widget_fixture_carries_no_forbidden_key(path: Path) -> None:
    """The four widget kinds that carry league payloads. Their fixtures arrive with the widget
    kinds; until then there is nothing to read, and the test says so rather than failing."""
    if not path.is_file():
        pytest.skip(f"{path.name} does not exist yet (the widget kinds have not landed)")
    assert _violations(path.name, _load_json(path)) == []


# --------------------------------------------------------------------------- a running application


@contextmanager
def _client(**environment: str | None) -> Iterator[TestClient]:
    previous = {key: os.environ.get(key) for key in environment}
    for key, value in environment.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    config.reset_settings_cache()
    deps.reset_rate_limiter()
    try:
        with TestClient(app_module.create_app()) as client:
            yield client
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        config.reset_settings_cache()
        deps.reset_rate_limiter()


@pytest.fixture(scope="module")
def live_client(
    seeded_engine: Any, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[TestClient]:
    """The application over the seeded NBA store and the synthetic EuroLeague demo.

    Everything the EuroLeague would read from the user's machine is pointed at an empty
    temporary directory, and live fetching is off, so the demo is the only league data in play.
    """
    root = tmp_path_factory.mktemp("market-guard")
    with _client(
        HARDWOOD_DATA_DIR=str(root / "data"),
        HARDWOOD_EL_DATABASE_URL=f"sqlite:///{root / 'el.db'}",
        HARDWOOD_WORKBOOK_PATH=None,
        HARDWOOD_EL_DEMO="1",
        HARDWOOD_EL_MODE=None,
        HARDWOOD_EL_LIVE="0",
        HARDWOOD_EL_ENABLED=None,
        HARDWOOD_API_KEY=None,
    ) as client:
        yield client


class _Subjects:
    """Real ids to put into the new routes' path and query parameters."""

    def __init__(self, client: TestClient, session: Session) -> None:
        self.nba_teams = [int(t) for t in session.execute(select(Team.team_id)).scalars()][:4]
        scheduled = (
            session.execute(
                select(Game.game_id).where(Game.status == "scheduled").order_by(Game.game_date)
            )
            .scalars()
            .first()
        )
        final = (
            session.execute(
                select(Game.game_id).where(Game.status == "final").order_by(Game.game_date.desc())
            )
            .scalars()
            .first()
        )
        self.nba_games = [g for g in (scheduled, final) if g]
        self.nba_player = session.execute(select(Player.player_id)).scalars().first()

        meta = client.get("/v1/el/meta").json()
        self.el_clubs = [c["id"] for c in meta.get("clubs", [])]
        rounds = [r["round"] for r in meta.get("rounds", [])]
        self.el_rounds = [str(rounds[0]), str(rounds[-1])] if rounds else []
        games = client.get("/v1/el/games").json().get("games", [])
        final_games = [g["gameId"] for g in games if g.get("status") == "final"]
        open_games = [g["gameId"] for g in games if g.get("status") != "final"]
        self.el_games = [g for g in (final_games[:1] + open_games[:1]) if g]
        club = self.el_clubs[0] if self.el_clubs else None
        squad = client.get(f"/v1/el/teams/{club}").json().get("squad", []) if club else []
        self.el_person = next(
            (s["player"]["personCode"] for s in squad if s.get("player", {}).get("personCode")),
            None,
        )

    def candidates(self, path: str, name: str) -> list[str] | None:
        """Values for one parameter of one path, or ``None`` if nothing is known for it."""
        league = "el" if path.startswith("/v1/el") else "nba"
        table: dict[str, dict[str, list[Any]]] = {
            "el": {
                "clubCode": self.el_clubs,
                "teamId": self.el_clubs,
                "homeTeamId": self.el_clubs[:2],
                "awayTeamId": list(reversed(self.el_clubs[:2])),
                "gameId": self.el_games,
                "personCode": [self.el_person] if self.el_person else [],
                "round": self.el_rounds,
            },
            "nba": {
                "teamId": self.nba_teams,
                "homeTeamId": self.nba_teams[:2],
                "awayTeamId": list(reversed(self.nba_teams[:2])),
                "gameId": self.nba_games,
                "playerId": [self.nba_player] if self.nba_player else [],
            },
        }
        values = table[league].get(name)
        return None if not values else [str(v) for v in values]


def _walk_new_get_routes(client: TestClient, session: Session) -> dict[str, Any]:
    """Call every new GET route (twice where there are two real ids) and keep each response."""
    spec = client.app.openapi()
    subjects = _Subjects(client, session)
    responses: list[tuple[str, int, Any]] = []
    skipped: list[str] = []
    for path, operations in sorted(spec["paths"].items()):
        get = operations.get("get")
        if get is None or not _is_new_path(path):
            continue
        needed = [
            p["name"]
            for p in get.get("parameters", [])
            if p["in"] == "path" or (p["in"] == "query" and p.get("required"))
        ]
        pool = {name: subjects.candidates(path, name) for name in needed}
        missing = [name for name, values in pool.items() if not values]
        if missing:
            skipped.append(f"{path}: no sample value for {missing}")
            continue
        for variant in range(2):
            query: dict[str, str] = {}
            url = path
            for name in needed:
                values = pool[name] or []
                value = values[variant % len(values)]
                if f"{{{name}}}" in path:
                    url = url.replace(f"{{{name}}}", value)
                else:
                    query[name] = value
            response = client.get(url, params=query or None)
            body: Any = None
            if "json" in response.headers.get("content-type", ""):
                body = response.json()
            responses.append((f"GET {url}", response.status_code, body))
    return {"responses": responses, "skipped": skipped}


@pytest.fixture(scope="module")
def walked(live_client: TestClient, seeded_engine: Any) -> dict[str, Any]:
    with Session(seeded_engine, future=True) as session:
        return _walk_new_get_routes(live_client, session)


def test_the_walk_reached_the_routes_it_is_supposed_to_scan(walked: dict[str, Any]) -> None:
    ok = [label for label, status, _ in walked["responses"] if status == 200]
    el_ok = {label.split("?")[0] for label in ok if label.startswith("GET /v1/el")}
    assert len(el_ok) >= 15, sorted(el_ok)
    assert walked["skipped"] == [], "teach _Subjects a sample for:\n" + "\n".join(walked["skipped"])


def test_no_response_from_a_new_route_carries_a_forbidden_key(walked: dict[str, Any]) -> None:
    problems: list[str] = []
    for label, _status, body in walked["responses"]:
        if body is not None:
            problems += _violations(label, body)
    assert problems == [], "\n".join(problems)


# =========================================================================== (b) parameters


def _schema_property_names(schema: Any, components: Mapping[str, Any], seen: set[str]) -> set[str]:
    """Every property name reachable from one OpenAPI schema (``$ref``, arrays, unions)."""
    names: set[str] = set()
    if not isinstance(schema, Mapping):
        return names
    ref = schema.get("$ref")
    if isinstance(ref, str):
        target = ref.rsplit("/", 1)[-1]
        if target not in seen:
            seen.add(target)
            names |= _schema_property_names(components.get(target, {}), components, seen)
        return names
    for key in ("anyOf", "oneOf", "allOf"):
        for sub in schema.get(key, []):
            names |= _schema_property_names(sub, components, seen)
    names |= _schema_property_names(schema.get("items"), components, seen)
    additional = schema.get("additionalProperties")
    if isinstance(additional, Mapping):
        names |= _schema_property_names(additional, components, seen)
    for name, sub in (schema.get("properties") or {}).items():
        names.add(name)
        names |= _schema_property_names(sub, components, seen)
    return names


def _declared_names(operation: Mapping[str, Any], components: Mapping[str, Any]) -> set[str]:
    names = {p["name"] for p in operation.get("parameters", []) if p.get("in") in ("query", "path")}
    body = operation.get("requestBody") or {}
    for media in (body.get("content") or {}).values():
        names |= _schema_property_names(media.get("schema"), components, set())
    return names


def test_every_new_route_declares_only_allowed_parameter_names(live_client: TestClient) -> None:
    spec = live_client.app.openapi()
    components = spec.get("components", {}).get("schemas", {})
    new_paths = {path: ops for path, ops in spec["paths"].items() if _is_new_path(path)}
    assert len([p for p in new_paths if p.startswith("/v1/el")]) >= 25, sorted(new_paths)

    problems: list[str] = []
    for path, operations in sorted(new_paths.items()):
        for method, operation in operations.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            bad = market_guard.parameter_violations(_declared_names(operation, components))
            if bad:
                problems.append(f"{method.upper()} {path} declares {bad}")
    assert problems == [], (
        "a new route may take only the names in contracts/leagues.json#/allowedParameters:\n  "
        + "\n  ".join(problems)
    )


def test_the_parameter_walk_can_see_a_request_body(live_client: TestClient) -> None:
    """The check above is only as good as its reach: it must find the body fields of the three
    writes (``POST availability``, ``POST news/links``, ``PATCH model-settings``)."""
    spec = live_client.app.openapi()
    components = spec["components"]["schemas"]
    seen = _declared_names(spec["paths"]["/v1/el/availability"]["post"], components)
    assert {"status", "sourceUrl", "sourceLabel", "sourcePublishedAt"} <= seen
    links = _declared_names(spec["paths"]["/v1/el/news/links"]["post"], components)
    assert {"title", "link", "publishedAt", "sourceName"} <= links
    patch = _declared_names(spec["paths"]["/v1/el/model-settings"]["patch"], components)
    assert {"settings", "key", "value"} <= patch


def test_a_route_that_took_a_price_would_be_caught() -> None:
    names = {"homeTeamId", "line", "overUnder", "edge", "probability"}
    assert market_guard.parameter_violations(names) == ["edge", "line", "overUnder", "probability"]


# =========================================================================== (c) settings


def _check_keys(table: Any) -> set[str]:
    for constraint in table.constraints:
        if isinstance(constraint, CheckConstraint) and str(constraint.sqltext).startswith("key IN"):
            return set(re.findall(r"'([^']+)'", str(constraint.sqltext)))
    raise AssertionError(f"{table.name} has no CHECK on its key column")


def _schema_file_keys(path: Path, table: str) -> set[str]:
    sql = path.read_text(encoding="utf-8")
    block = sql[sql.index(f"CREATE TABLE {table} (") :]
    block = block[: block.index(");")]
    match = re.search(r"CHECK \(\"?key\"? IN \(([^)]*)\)\)", block)
    assert match, f"{path.name}: no key CHECK in {table}"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def test_the_euroleague_setting_keys_are_exactly_the_designed_allowlist() -> None:
    from nbastats.euroleague import models as el_models
    from nbastats.euroleague import settings as el_settings

    assert set(el_models.MODEL_SETTING_KEYS) == DESIGN_EL_SETTING_KEYS
    assert len(el_models.MODEL_SETTING_KEYS) == len(DESIGN_EL_SETTING_KEYS)
    assert set(el_settings.ALLOWED_KEYS) == DESIGN_EL_SETTING_KEYS
    assert _check_keys(el_models.ElModelSetting.__table__) == DESIGN_EL_SETTING_KEYS
    schema = PACKAGE / "euroleague" / "schema.sql"
    assert _schema_file_keys(schema, "el_model_setting") == DESIGN_EL_SETTING_KEYS


def test_the_nba_setting_keys_are_exactly_the_designed_allowlist() -> None:
    from nbastats.nba_intel import models as intel_models
    from nbastats.nba_intel import settings as intel_settings

    assert set(intel_models.MODEL_SETTING_KEYS) == DESIGN_NBA_SETTING_KEYS
    assert len(intel_models.MODEL_SETTING_KEYS) == len(DESIGN_NBA_SETTING_KEYS)
    assert set(intel_settings.ALLOWED_KEYS) == DESIGN_NBA_SETTING_KEYS
    assert _check_keys(intel_models.NbaIntelModelSetting.__table__) == DESIGN_NBA_SETTING_KEYS
    schema = PACKAGE / "nba_intel" / "schema.sql"
    assert _schema_file_keys(schema, "nba_intel_model_setting") == DESIGN_NBA_SETTING_KEYS


def test_no_setting_key_is_a_betting_word_or_a_betting_setting() -> None:
    keys = DESIGN_EL_SETTING_KEYS | DESIGN_NBA_SETTING_KEYS
    assert market_guard.scan_keys({key: 1 for key in keys}) == []
    lowered = {key.lower() for key in keys}
    for setting in DESIGN_IGNORED_SETTINGS:
        assert setting.lower() not in lowered, f"{setting} must have no model-setting key"
    # Whole words only: ``statusChance.probable`` is a status, not a probability.
    suspicious = {"prob", "probability", "threshold", "total", "edge", "line", "over", "under"}
    for key in keys:
        assert not set(market_guard.split_words(key)) & suspicious, key


# =========================================================================== (d) the importer


def test_the_importer_reads_none_of_the_betting_headers_or_settings() -> None:
    from nbastats.euroleague.importers import workbook

    read = {header for headers in workbook.READ_MAP.values() for header in headers}
    assert read, "the read map is empty"
    assert not read & DESIGN_IGNORED_HEADERS, sorted(read & DESIGN_IGNORED_HEADERS)
    assert not read & workbook.IGNORED_HEADERS, sorted(read & workbook.IGNORED_HEADERS)
    assert not read & DESIGN_IGNORED_SETTINGS, sorted(read & DESIGN_IGNORED_SETTINGS)
    assert not read & workbook.IGNORED_SETTINGS

    # Every one the design names is named by the importer as ignored (it may name more).
    assert DESIGN_IGNORED_HEADERS <= workbook.IGNORED_HEADERS
    assert workbook.IGNORED_SETTINGS == DESIGN_IGNORED_SETTINGS
    # A setting mapped into the store is never one of the betting ones.
    assert not set(workbook.SETTING_MAP) & DESIGN_IGNORED_SETTINGS


def _mini_league_workbook() -> tuple[bytes, Any, Any]:
    """``(xlsx bytes, the support module, the mini league)`` from the EuroLeague tests' builder.

    The builder is a dataclass in ``tests/euroleague/conftest.py``; its ``mini_league`` fixture is
    not visible from this directory, so the module is loaded from its file and the fixture's four
    lines are repeated. If the builder changes shape the failure message says where to look.
    """
    path = BACKEND / "tests" / "euroleague" / "conftest.py"
    try:
        support = _load_by_path("hardwood_el_test_support", path)
        from nbastats.euroleague import demo

        clubs = demo._build_clubs(random.Random(demo.DEMO_SEED + 1))[:4]
        rotation = {"ZZA": "ZZB", "ZZB": "ZZC", "ZZC": "ZZA", "ZZD": "ZZD"}
        league = support.MiniLeague(clubs=clubs, workbook_code=rotation)
        data = league.workbook(with_sentinels=True)
    except Exception as exc:  # noqa: BLE001 - a changed builder is this test's problem to name
        raise AssertionError(
            "the synthetic workbook builder in tests/euroleague/conftest.py (MiniLeague) changed "
            f"shape; update _mini_league_workbook in this file to match: {exc!r}"
        ) from exc
    return data, support, league


def _xml_parts(xlsx: bytes) -> str:
    import io

    with zipfile.ZipFile(io.BytesIO(xlsx)) as archive:
        return "\n".join(
            archive.read(name).decode("utf-8", "replace")
            for name in archive.namelist()
            if name.endswith(".xml")
        )


def test_a_workbook_full_of_betting_cells_leaves_no_trace_in_the_store(tmp_path: Path) -> None:
    from nbastats.euroleague.db import create_el_engine, dispose_el_engine, init_el_db
    from nbastats.euroleague.importers import workbook

    data, support, league = _mini_league_workbook()
    sentinel_text = tuple(support.SENTINEL_TEXT)
    sentinel_numbers = tuple(support.SENTINEL_NUMBERS)

    # The test is only a guard while the workbook really carries what the importer must ignore.
    xml = _xml_parts(data)
    carried = [word for word in sentinel_text if word in xml]
    assert len(carried) >= 2, f"the workbook carries only the text sentinels {carried}"
    for header in sorted(DESIGN_IGNORED_HEADERS):
        assert header in xml, f"the synthetic workbook has no {header!r} column"
    for setting in sorted(DESIGN_IGNORED_SETTINGS):
        assert setting in xml, f"the synthetic workbook has no {setting!r} setting"
    carried_numbers = [n for n in sentinel_numbers if str(n) in xml]
    assert len(carried_numbers) >= len(sentinel_numbers) // 2, carried_numbers

    path = tmp_path / "el.db"
    engine = create_el_engine(f"sqlite:///{path}")
    try:
        init_el_db(engine)
        report = workbook.import_workbook(data, engine, crosswalk=league.crosswalk)
        assert report.status == "ok", report.render()
        assert report.counts, "nothing was imported, so there is nothing to search"
        # The importer named what it declined to read, and never carried a value.
        assert set(report.ignored_settings) == DESIGN_IGNORED_SETTINGS
        assert set(report.ignored_headers) & DESIGN_IGNORED_HEADERS
        rendered = json.dumps(report.to_dict())
        assert not any(word in rendered for word in sentinel_text)
        assert not any(str(number) in rendered for number in sentinel_numbers)

        with engine.connect() as connection:
            tables = [
                row[0]
                for row in connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )
            ]
            cells = 0
            for table in tables:
                for row in connection.execute(text(f"SELECT * FROM {table}")):
                    for value in row:
                        cells += 1
                        if isinstance(value, str):
                            assert not any(word in value for word in sentinel_text), (table, value)
                        elif isinstance(value, (int, float)) and not isinstance(value, bool):
                            assert not any(abs(value - n) < 1e-9 for n in sentinel_numbers), (
                                table,
                                value,
                            )
        assert cells > 500, f"only {cells} cells were searched"
    finally:
        engine.dispose()
        dispose_el_engine()

    # And not in the bytes on disk either (text, or a number written as a binary double).
    blob = b"".join(p.read_bytes() for p in tmp_path.glob("el.db*") if p.is_file())
    assert blob, "the store file is empty"
    for word in sentinel_text:
        assert word.encode() not in blob
    for number in sentinel_numbers:
        assert struct.pack(">d", number) not in blob
        assert struct.pack("<d", number) not in blob


# =========================================================================== (e) rounding

_ROUNDERS = frozenset({"round", "floor", "ceil", "rint", "trunc"})


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
    return ""


def _is_number(node: ast.AST, *values: float) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
        and node.value in values
    )


def _doubles(node: ast.AST) -> bool:
    """``x * 2``, ``2 * x`` or ``x / 0.5``: a value moved to the half-point scale."""
    if not isinstance(node, ast.BinOp):
        return False
    if isinstance(node.op, ast.Mult):
        return _is_number(node.left, 2) or _is_number(node.right, 2)
    if isinstance(node.op, ast.Div):
        return _is_number(node.right, 0.5)
    return False


def _rounds_a_double(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and _call_name(node) in _ROUNDERS
        and bool(node.args)
        and _doubles(node.args[0])
    )


def half_point_roundings(tree: ast.AST) -> list[int]:
    """Line numbers of ``round(x * 2) / 2`` and its spellings: floor or ceil in place of round,
    ``x / 0.5`` for ``x * 2``, ``* 0.5`` for ``/ 2``, and the operands either way round."""
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp):
            continue
        if (
            isinstance(node.op, ast.Div)
            and _rounds_a_double(node.left)
            and _is_number(node.right, 2)
        ):
            hits.append(node.lineno)
        elif isinstance(node.op, ast.Mult) and (
            (_rounds_a_double(node.left) and _is_number(node.right, 0.5))
            or (_rounds_a_double(node.right) and _is_number(node.left, 0.5))
        ):
            hits.append(node.lineno)
    return hits


@pytest.mark.parametrize(
    "source",
    [
        "round(x * 2) / 2",
        "round(x * 2, 0) / 2",
        "round(2 * x) / 2.0",
        "math.floor(x * 2) / 2",
        "math.ceil(combined * 2) / 2",
        "round(x / 0.5) * 0.5",
        "0.5 * round(x / 0.5)",
        "round(x * 2) * 0.5",
        "round(x / 0.5) / 2",
    ],
)
def test_the_rounding_detector_catches_every_spelling_of_a_half_point_total(source: str) -> None:
    assert half_point_roundings(ast.parse(source)) == [1]


@pytest.mark.parametrize(
    "source",
    ["round(x, 1)", "round(x * 100) / 100", "round(x * 2.5) / 2.5", "x * 2 / 2", "round(x) / 2"],
)
def test_the_rounding_detector_leaves_ordinary_arithmetic_alone(source: str) -> None:
    assert half_point_roundings(ast.parse(source)) == []


NEW_PACKAGE_DIRS = ("shared", "intel", "nba_intel", "nba_matchup", "euroleague")
NEW_MODULE_FILES = (
    "api/routes_leagues.py",
    "api/routes_matchups.py",
    "api/routes_defense.py",
    "api/routes_projections.py",
    "api/routes_availability.py",
    "api/routes_sources.py",
    "api/routes_euroleague.py",
    "worker.py",
)


def _new_source_files() -> list[Path]:
    files: list[Path] = []
    for directory in NEW_PACKAGE_DIRS:
        files.extend(sorted((PACKAGE / directory).rglob("*.py")))
    files.extend(PACKAGE / name for name in NEW_MODULE_FILES if (PACKAGE / name).is_file())
    return files


def test_no_new_code_rounds_a_total_to_the_half_point() -> None:
    files = _new_source_files()
    assert len(files) >= 60, f"the scan reached only {len(files)} files"
    hits = [
        f"{path.relative_to(PACKAGE)}:{line}"
        for path in files
        for line in half_point_roundings(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert hits == [], (
        "a total rounded to .5 is a quotable number; do not make one:\n  " + "\n  ".join(hits)
    )


# =========================================================================== (f) win probability

_WIN_WORDS = frozenset({"win", "wins", "winner", "winners", "winning", "victory"})
_LIKELIHOOD_WORDS = frozenset(
    {"prob", "probability", "probabilities", "pct", "percent", "percentage", "chance", "chances",
     "likelihood", "odds", "pwin"}
)  # fmt: skip


def win_probability_fields(payload: Any, root: str = "$") -> list[str]:
    """Paths of keys that look like a chance of winning.

    Two tests, because a name can be innocent and a value can say what the name does not: a key
    whose words include both a win word and a likelihood word (``homeWinPct``, ``winProb``,
    ``chanceToWin`` is caught by ``chance`` and ``win``), and any key that mentions winning whose
    value is a fraction strictly between 0 and 1 (``homeWin: 0.62``). Counts such as
    ``winnersCalled`` are integers and pass; so does ``projectedWinner`` (an object).
    """
    found: list[str] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            where = f"{root}.{key}"
            words = set(market_guard.split_words(str(key)))
            if words & _WIN_WORDS and words & _LIKELIHOOD_WORDS:
                found.append(where)
            elif (
                words & _WIN_WORDS
                and isinstance(value, float)
                and not isinstance(value, bool)
                and 0.0 < value < 1.0
            ):
                found.append(where)
            found.extend(win_probability_fields(value, where))
    elif isinstance(payload, (list, tuple)):
        for index, child in enumerate(payload):
            found.extend(win_probability_fields(child, f"{root}[{index}]"))
    return found


def test_the_win_probability_detector_catches_the_shapes_it_is_for() -> None:
    assert win_probability_fields({"homeWinPct": 0.6}) == ["$.homeWinPct"]
    assert win_probability_fields({"winProb": 0.6}) == ["$.winProb"]
    assert win_probability_fields({"chanceToWin": 0.4}) == ["$.chanceToWin"]
    assert win_probability_fields({"home": {"win": 0.62}}) == ["$.home.win"]
    assert win_probability_fields({"games": [{"winProbability": 0.5}]}) == [
        "$.games[0].winProbability"
    ]
    assert win_probability_fields({"winnersCalled": 7, "projectedWinner": {"id": "ZZA"}}) == []
    assert win_probability_fields({"record": {"wins": 3, "losses": 1}, "winnerCalled": True}) == []


def test_no_new_payload_has_a_field_holding_a_probability_of_winning(
    walked: dict[str, Any],
) -> None:
    problems: list[str] = []
    for label, _status, body in walked["responses"]:
        problems += [f"{label}: {path}" for path in win_probability_fields(body)]
    for path in [*LEAGUE_FIXTURE_FILES, *(p for p in LEAGUE_WIDGET_FIXTURES if p.is_file())]:
        problems += [
            f"{path.relative_to(FIXTURES)}: {found}"
            for found in win_probability_fields(_load_json(path))
        ]
    problems += [
        f"leagues.json: {p}" for p in win_probability_fields(_load_json(CONTRACTS / "leagues.json"))
    ]
    assert problems == [], "\n".join(problems)


def _new_tables() -> list[Any]:
    from nbastats.euroleague.models import ElBase
    from nbastats.nba_intel.models import NbaIntelBase

    new_stats_tables = ("player_position_season", "game_schedule_detail", "team_projection_ledger")
    tables = [Base.metadata.tables[name] for name in new_stats_tables]
    tables += list(ElBase.metadata.tables.values())
    tables += list(NbaIntelBase.metadata.tables.values())
    return tables


def test_no_new_column_is_named_for_a_price_or_a_chance_of_winning() -> None:
    """The ledgers record what the model said, the settings record what it assumed, and nothing
    has a column for a line or a probability."""
    tables = _new_tables()
    assert len(tables) >= 40, [t.name for t in tables]
    problems: list[str] = []
    for table in tables:
        for column in table.columns:
            words = set(market_guard.split_words(column.name))
            banned = sorted(words & market_guard.FORBIDDEN_KEY_WORDS)
            if banned:
                problems.append(f"{table.name}.{column.name}: {banned}")
            if words & _WIN_WORDS and words & _LIKELIHOOD_WORDS:
                problems.append(f"{table.name}.{column.name}: looks like a chance of winning")
    assert problems == [], "\n".join(problems)


def _shared_dataclasses() -> Iterator[tuple[str, type]]:
    import importlib
    import pkgutil

    import nbastats.shared as shared

    for info in pkgutil.iter_modules(shared.__path__):
        module = importlib.import_module(f"nbastats.shared.{info.name}")
        for name, obj in vars(module).items():
            if isinstance(obj, type) and is_dataclass(obj) and obj.__module__ == module.__name__:
                yield f"{info.name}.{name}", obj


def test_no_shared_result_type_has_a_field_for_a_price_or_a_chance_of_winning() -> None:
    seen = list(_shared_dataclasses())
    assert len(seen) >= 15, [name for name, _ in seen]
    problems: list[str] = []
    for name, cls in seen:
        for field in fields(cls):
            words = set(market_guard.split_words(field.name))
            if words & market_guard.FORBIDDEN_KEY_WORDS:
                problems.append(f"{name}.{field.name}")
            if words & _WIN_WORDS and words & _LIKELIHOOD_WORDS:
                problems.append(f"{name}.{field.name}: looks like a chance of winning")
    assert problems == [], "\n".join(problems)
