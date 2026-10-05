"""The walls between the leagues, proved from the source text and from the running application.

Hardwood serves the NBA and the EuroLeague from one process, and the whole design rests on one
promise: a EuroLeague row can never appear in an NBA view, an NBA reseed can never wipe a
EuroLeague row, and a broken EuroLeague can never stop the NBA from serving. A promise like that
cannot live in a document, because the first convenient shortcut (an NBA module importing a
EuroLeague helper, a table added to the wrong ``MetaData``, a start-up hook that lets an
exception through) breaks it silently. These tests are the shortcut detectors. They read the code
and the application; they never need a database full of data, so they stay cheap and run on every
commit.

What is asserted
----------------
**The import boundary (an AST walk, no module is executed).**

* No module under ``nbastats/`` outside ``euroleague/`` imports ``nbastats.euroleague``, with one
  named exception: ``api/routes_euroleague.py``, the three-line shim that exposes the EuroLeague's
  router to ``api/app.py``. ``app.py`` itself reaches the package only through
  :func:`importlib.import_module` by string name, inside a ``try``.
* ``nbastats.euroleague`` may import only the pure core (``nbastats.shared``), the shared ingest
  plumbing (``nbastats.intel``), ``nbastats.api.errors``, ``nbastats.api.deps`` for its guards,
  and ``nbastats.config``. Of ``deps`` it may take guards, never the helpers that open the NBA
  store (``get_db``, the season resolvers). It never imports ``nbastats.db``, ``nbastats.models``,
  ``nbastats.seed``, ``nbastats.nba_intel`` or anything else that knows the NBA.
* The pure core knows no league package and no database; the intel plumbing knows no league.

**Three stores, three ``MetaData`` objects, no shared table.** ``Base`` (the NBA stats store),
``NbaIntelBase`` (same file, never wiped by the seeder), ``ElBase`` (its own file) and
``AccountBase`` (accounts) are four different ``MetaData`` objects whose table names never
overlap; ``el_`` and ``nba_intel_`` tables exist only on their own base, so the seeder
(``seed._clear`` and ``_INSERT_ORDER`` walk ``Base.metadata``) and ``render_schema_sql`` never see
them. No foreign key leaves its own ``MetaData``. The three generated ``schema.sql`` files
contain only their own tables.

**The catalogs keep the leagues apart.** ``contracts/metrics.json`` has two lists on purpose. The
NBA catalog (``metrics``, now 62 entries with ``opp_pts``) holds only keys that resolve against NBA
rows and carry NBA-era availability; the EuroLeague's stat vocabulary (PIR among it) is a separate
top-level ``leagueMetrics.euroleague`` list, so a EuroLeague-only stat can never become a
permanently null NBA metric. The two lists agree on how any shared stat is named and formatted, and
the EuroLeague list covers every stat key the EuroLeague's own stats table emits.

**The wiring in ``api/app.py``.** The seven new optional router modules are listed;
``HARDWOOD_EL_ENABLED=0`` mounts no EuroLeague route and opens no EuroLeague store; a EuroLeague
bootstrap that raises is logged and the NBA still serves; ``/v1/el/health`` answers without an
API key while every other EuroLeague route is behind the key.

What is not here
----------------
The behavioural leak test (a EuroLeague store with deliberately colliding data, every NBA route
walked for a EuroLeague id) lives with the EuroLeague
(``tests/euroleague/test_no_leak.py``); the reseed-survival tests live with each store. This file
is the structure those behaviours depend on.
"""

from __future__ import annotations

import ast
import importlib
import logging
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, NamedTuple

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, inspect

from nbastats import config
from nbastats.api import app as app_module
from nbastats.api import deps
from nbastats.models import Base

BACKEND = Path(__file__).resolve().parents[1]
PACKAGE_DIR = BACKEND / "nbastats"

#: The one module outside ``euroleague/`` allowed to import it.
SHIM = "api/routes_euroleague.py"

#: What ``nbastats.euroleague`` may import from the rest of the application, as module prefixes.
EUROLEAGUE_MAY_IMPORT = (
    "nbastats.shared",
    "nbastats.intel",
    "nbastats.api.errors",
    "nbastats.api.deps",
    "nbastats.config",
)

#: Helpers in ``api/deps.py`` that open a session on the NBA store or resolve an NBA season. The
#: EuroLeague has its own session dependency and its own season codes and takes neither.
NBA_STORE_HELPERS_IN_DEPS = frozenset(
    {
        "get_db",
        "SessionDep",
        "loaded_seasons",
        "current_season",
        "resolve_season",
        "ensure_season_loaded",
    }
)

NEW_ROUTER_MODULES = (
    "routes_leagues",
    "routes_matchups",
    "routes_defense",
    "routes_projections",
    "routes_availability",
    "routes_sources",
    "routes_euroleague",
)

# --------------------------------------------------------------------------- import resolution


class Edge(NamedTuple):
    """One import: the module that wrote it, what it resolves to, and where."""

    source: str  # path relative to nbastats/, e.g. "euroleague/read/stats.py"
    target: str  # absolute dotted name, e.g. "nbastats.shared.refs"
    line: int
    module: str  # the module a ``from`` statement names ("" for a plain ``import``)
    names: tuple[str, ...]  # the names a ``from`` statement takes


def _dotted(path: Path) -> tuple[str, bool]:
    """``(module name, is a package __init__)`` for a file under the backend directory."""
    parts = list(path.relative_to(BACKEND).with_suffix("").parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts = parts[:-1]
    return ".".join(parts), is_package


def _is_module(dotted: str) -> bool:
    base = BACKEND.joinpath(*dotted.split("."))
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def _edges(path: Path) -> list[Edge]:
    """Every import in ``path`` that resolves to a ``nbastats`` module.

    A relative import is resolved against the file's own package. ``from a import b`` resolves to
    ``a.b`` when that is a module (so ``from ...api import errors`` is ``nbastats.api.errors``)
    and to ``a`` when ``b`` is only a name defined in ``a``.
    """
    name, is_package = _dotted(path)
    package = name if is_package else name.rpartition(".")[0]
    source = str(path.relative_to(PACKAGE_DIR))
    found: list[Edge] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "nbastats":
                    found.append(Edge(source, alias.name, node.lineno, "", ()))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                parts = parts[: len(parts) - (node.level - 1)]
                base = ".".join(parts + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            if base.split(".")[0] != "nbastats":
                continue
            names = tuple(alias.name for alias in node.names)
            for alias in node.names:
                candidate = f"{base}.{alias.name}"
                target = candidate if alias.name != "*" and _is_module(candidate) else base
                found.append(Edge(source, target, node.lineno, base, names))
    return found


def _python_files(*subpaths: str) -> list[Path]:
    """The ``.py`` files under ``nbastats/<subpath>`` (a directory) or that file itself."""
    files: list[Path] = []
    for sub in subpaths:
        target = PACKAGE_DIR / sub
        if target.is_dir():
            files.extend(sorted(target.rglob("*.py")))
        elif target.is_file():
            files.append(target)
    return files


def _all_modules_except(*skip_dirs: str) -> list[Path]:
    skipped = tuple(PACKAGE_DIR / d for d in skip_dirs)
    return [
        path
        for path in sorted(PACKAGE_DIR.rglob("*.py"))
        if not any(path.is_relative_to(s) for s in skipped)
    ]


def _under(target: str, prefix: str) -> bool:
    return target == prefix or target.startswith(prefix + ".")


# --------------------------------------------------------------------------- the boundary


def test_the_walk_sees_the_packages_it_is_supposed_to_guard() -> None:
    """A boundary test that walks nothing proves nothing: fail if a package is not found."""
    assert len(_python_files("euroleague")) >= 20
    assert len(_python_files("shared")) >= 8
    assert len(_python_files("intel")) >= 4
    assert len(_python_files("nba_intel")) >= 5
    assert _edges(PACKAGE_DIR / "api" / "routes_euroleague.py"), "the shim imports the router"


def test_nothing_outside_the_euroleague_package_imports_it() -> None:
    offenders = [
        f"{edge.source}:{edge.line} imports {edge.target}"
        for path in _all_modules_except("euroleague")
        for edge in _edges(path)
        if _under(edge.target, "nbastats.euroleague") and edge.source != SHIM
    ]
    assert offenders == [], (
        "only api/routes_euroleague.py may import the sealed EuroLeague package:\n  "
        + "\n  ".join(offenders)
    )


def test_the_shim_imports_only_the_euroleague_router() -> None:
    """The exception is a shim, not a door: it takes the router and nothing else."""
    edges = [e for e in _edges(PACKAGE_DIR / "api" / "routes_euroleague.py")]
    reached = {e.target for e in edges if _under(e.target, "nbastats.euroleague")}
    assert reached == {"nbastats.euroleague.api.routes"}, reached


def test_the_application_reaches_the_euroleague_only_by_string_name() -> None:
    """``api/app.py`` starts the EuroLeague through ``importlib``, never a static import."""
    path = PACKAGE_DIR / "api" / "app.py"
    assert not [e for e in _edges(path) if "euroleague" in e.target]
    calls = [
        ast.unparse(node)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Name, ast.Attribute))
        and getattr(node.func, "attr", getattr(node.func, "id", "")) == "import_module"
        and "euroleague" in ast.unparse(node)
    ]
    assert any("euroleague.bootstrap" in call for call in calls), calls


def test_only_the_application_and_the_worker_name_the_euroleague_to_importlib() -> None:
    """A string-named import is the sanctioned way round the static rule, so it is itself
    confined to the two places the design names: the start-up hook in ``api/app.py`` and the
    worker, whose jobs are imported by name at the moment they are due."""
    files: set[str] = set()
    for path in _all_modules_except("euroleague"):
        text = path.read_text(encoding="utf-8")
        if "import_module" not in text:
            continue
        for node in ast.walk(ast.parse(text)):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "attr", getattr(node.func, "id", "")) == "import_module"
                and "euroleague" in ast.unparse(node)
            ):
                files.add(str(path.relative_to(PACKAGE_DIR)))
    assert files <= {"api/app.py", "worker.py"}, sorted(files)


#: Modules allowed to import a module whose name is computed at run time, and why each is safe.
#: Anything else that builds an import name from a value could assemble "nbastats.euroleague"
#: without spelling it, which the literal checks above cannot see.
_COMPUTED_IMPORTS_ALLOWED: dict[str, str] = {
    "api/app.py": "the optional routers, by a fixed list of names in this package",
    "worker.py": "job targets named in the worker's own job table",
    "api/__init__.py": "its own submodule, api.app, imported lazily",
    "accounts/__init__.py": "its own submodules, for lazy attribute access",
    "ingest/client.py": "an nba_api endpoint module (nba_api.stats.endpoints.*)",
}


def test_no_module_imports_a_computed_name_outside_the_allowlist() -> None:
    """``importlib.import_module`` (or ``__import__``) with a non-literal name is confined to
    the modules above. The widget layer reaches the EuroLeague's builders through the league
    registry's ``read_side``, not through a module name built from a league key."""
    offenders: list[str] = []
    for path in _all_modules_except("euroleague"):
        rel = str(path.relative_to(PACKAGE_DIR))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name not in ("import_module", "__import__") or not node.args:
                continue
            first = node.args[0]
            literal = isinstance(first, ast.Constant) and isinstance(first.value, str)
            if not literal and rel not in _COMPUTED_IMPORTS_ALLOWED:
                offenders.append(f"{rel}:{node.lineno}: {ast.unparse(node)}")
    assert offenders == [], "computed import names outside the allowlist:\n  " + "\n  ".join(
        offenders
    )


def test_the_euroleague_imports_only_what_it_is_allowed_to() -> None:
    offenders = [
        f"{edge.source}:{edge.line} imports {edge.target}"
        for path in _python_files("euroleague")
        for edge in _edges(path)
        if not (
            _under(edge.target, "nbastats.euroleague")
            or edge.target == "nbastats"
            or any(_under(edge.target, allowed) for allowed in EUROLEAGUE_MAY_IMPORT)
        )
    ]
    assert offenders == [], (
        "nbastats.euroleague may import only "
        + ", ".join(EUROLEAGUE_MAY_IMPORT)
        + ":\n  "
        + "\n  ".join(offenders)
    )


def test_the_euroleague_takes_guards_from_deps_and_never_the_nba_session() -> None:
    taken: list[str] = []
    for path in _python_files("euroleague"):
        for edge in _edges(path):
            if edge.module == "nbastats.api.deps" or edge.target == "nbastats.api.deps":
                taken.extend(
                    f"{edge.source}:{edge.line} takes {name}"
                    for name in edge.names
                    if name in NBA_STORE_HELPERS_IN_DEPS
                )
                if not edge.names:  # a plain ``import nbastats.api.deps`` takes the lot
                    taken.append(f"{edge.source}:{edge.line} imports the whole module")
    assert taken == [], "the EuroLeague has its own session dependency:\n  " + "\n  ".join(taken)


def test_the_euroleague_never_touches_an_nba_module() -> None:
    """The allowlist above already implies this; the names make the failure message obvious."""
    never = (
        "nbastats.db",
        "nbastats.models",
        "nbastats.seed",
        "nbastats.catalog",
        "nbastats.fantasy",
        "nbastats.nba_intel",
        "nbastats.nba_matchup",
        "nbastats.widgets",
        "nbastats.ingest",
        "nbastats.accounts",
    )
    offenders = [
        f"{edge.source}:{edge.line} imports {edge.target}"
        for path in _python_files("euroleague")
        for edge in _edges(path)
        if any(_under(edge.target, forbidden) for forbidden in never)
    ]
    assert offenders == [], "\n  ".join(offenders)


def test_the_pure_core_knows_no_other_part_of_the_application() -> None:
    offenders = [
        f"{edge.source}:{edge.line} imports {edge.target}"
        for path in _python_files("shared")
        for edge in _edges(path)
        if not _under(edge.target, "nbastats.shared")
    ]
    assert offenders == [], "nbastats.shared must be self-contained:\n  " + "\n  ".join(offenders)


def test_the_intel_plumbing_knows_no_league() -> None:
    offenders = [
        f"{edge.source}:{edge.line} imports {edge.target}"
        for path in _python_files("intel")
        for edge in _edges(path)
        if not (_under(edge.target, "nbastats.intel") or _under(edge.target, "nbastats.shared"))
    ]
    assert offenders == [], (
        "nbastats.intel is shared plumbing and has no models: it may import itself and the "
        "pure core, nothing else:\n  " + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------- the metadata


def _metadatas() -> dict[str, MetaData]:
    from nbastats.accounts.models import AccountBase
    from nbastats.euroleague.models import ElBase
    from nbastats.nba_intel.models import NbaIntelBase

    return {
        "stats": Base.metadata,
        "nba_intel": NbaIntelBase.metadata,
        "euroleague": ElBase.metadata,
        "accounts": AccountBase.metadata,
    }


def test_four_stores_are_four_metadata_objects() -> None:
    metadatas = _metadatas()
    assert len({id(m) for m in metadatas.values()}) == len(metadatas)
    for name, metadata in metadatas.items():
        assert metadata.tables, f"{name} has no tables"


def test_no_table_name_is_shared_between_stores() -> None:
    owners: dict[str, list[str]] = {}
    for store, metadata in _metadatas().items():
        for table in metadata.tables:
            owners.setdefault(table, []).append(store)
    shared = {table: stores for table, stores in owners.items() if len(stores) > 1}
    assert shared == {}, f"tables on more than one MetaData: {shared}"


def test_league_tables_exist_only_on_their_own_base() -> None:
    metadatas = _metadatas()
    for store, prefix in (("euroleague", "el_"), ("nba_intel", "nba_intel_")):
        wrong = [t for t in metadatas[store].tables if not t.startswith(prefix)]
        assert wrong == [], f"{store} tables must all start with {prefix!r}: {wrong}"
    for store, metadata in metadatas.items():
        if store in ("euroleague", "nba_intel"):
            continue
        stray = [t for t in metadata.tables if t.startswith(("el_", "nba_intel_"))]
        assert stray == [], f"{store} must not hold league-intel tables: {stray}"


def test_the_seeder_and_the_schema_renderer_never_see_a_league_table() -> None:
    from nbastats import models, seed

    league = {
        name
        for store, metadata in _metadatas().items()
        if store in ("euroleague", "nba_intel")
        for name in metadata.tables
    }
    assert league, "the league tables were not found"
    assert not league & set(seed._INSERT_ORDER), "the seeder would wipe a league table"
    assert not league & set(Base.metadata.tables)
    ddl = models.render_schema_sql()
    assert not re.search(r"CREATE TABLE (el_|nba_intel_)", ddl)


def test_no_foreign_key_leaves_its_own_metadata() -> None:
    crossing: list[str] = []
    for store, metadata in _metadatas().items():
        for table in metadata.tables.values():
            for column in table.columns:
                for fk in column.foreign_keys:
                    target_table = fk.target_fullname.rpartition(".")[0].rpartition(".")[-1]
                    if target_table not in metadata.tables:
                        crossing.append(
                            f"{store}: {table.name}.{column.name} -> {fk.target_fullname}"
                        )
    assert crossing == [], "\n".join(crossing)


@pytest.mark.parametrize(
    ("relative", "own_prefix", "other_prefixes"),
    [
        ("schema.sql", "", ("el_", "nba_intel_")),
        ("nba_intel/schema.sql", "nba_intel_", ("el_",)),
        ("euroleague/schema.sql", "el_", ("nba_intel_",)),
    ],
)
def test_each_generated_schema_file_holds_only_its_own_tables(
    relative: str, own_prefix: str, other_prefixes: tuple[str, ...]
) -> None:
    text = (PACKAGE_DIR / relative).read_text(encoding="utf-8")
    tables = re.findall(r"CREATE TABLE (\w+)", text)
    assert tables, f"{relative} creates no tables"
    for table in tables:
        assert table.startswith(own_prefix), f"{relative} creates {table}"
        assert not table.startswith(other_prefixes), f"{relative} creates {table}"


def test_the_stats_store_has_no_prefix_collision_with_the_league_stores() -> None:
    names = set(Base.metadata.tables)
    assert not [n for n in names if n.startswith(("el_", "nba_intel_"))], names


def test_the_euroleague_store_refuses_a_file_that_holds_an_nba_store(tmp_path: Path) -> None:
    """The structural half of the same-store guard: ``init_el_db`` independently refuses any
    file that already contains a ``teams`` table (the NBA's), so a bypassed configuration guard
    still cannot put EuroLeague tables beside NBA ones."""
    from nbastats.db import create_db_engine, init_db
    from nbastats.euroleague import db as el_db

    path = tmp_path / "one-file.db"
    engine = create_db_engine(f"sqlite:///{path}")
    try:
        init_db(engine)
        with pytest.raises(el_db.ElStoreError):
            el_db.init_el_db(engine)
        created = [t for t in inspect(engine).get_table_names() if t.startswith("el_")]
        assert created == [], f"a refused file must be left alone, but now holds {created}"
    finally:
        engine.dispose()


# --------------------------------------------------------------------------- the catalogs

CONTRACTS = BACKEND.parent / "contracts"

#: Stats only the EuroLeague has. None of them may be an NBA metric.
EUROLEAGUE_ONLY_KEYS = frozenset({"pir", "fgm2", "fga2", "fg2_pct", "blk_against", "fouls_drawn"})

#: The keys design section 10.1 names for the EuroLeague block, with ``fgm3`` and ``fga3`` under
#: the catalog's own names (``fg3m`` and ``fg3a``).
DESIGNED_EUROLEAGUE_KEYS = frozenset(
    {
        "pir", "fgm2", "fga2", "fg2_pct", "blk_against", "fouls_drawn",
        "pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf",
        "fg3m", "fg3a", "fg3_pct", "ftm", "fta", "ft_pct", "min", "plus_minus",
    }
)  # fmt: skip


def _metrics_document() -> dict[str, Any]:
    import json

    return json.loads((CONTRACTS / "metrics.json").read_text(encoding="utf-8"))


def test_points_allowed_is_a_team_metric_of_the_nba_catalog() -> None:
    document = _metrics_document()
    metrics = {m["key"]: m for m in document["metrics"]}
    assert len(metrics) == 62
    assert metrics["opp_pts"] == {
        "key": "opp_pts",
        "name": "Points Allowed",
        "shortName": "OPP PTS",
        "category": "scoring",
        "format": "decimal1",
        "higherIsBetter": False,
        "scope": ["team"],
        "availability": {
            "seasonFrom": "1946-47",
            "perGameFrom": "1946-47",
            "seasonLevelOnly": False,
            "estimatedBefore": None,
        },
        "domain": None,
        "glossary": "Points the team's opponents scored per game.",
    }
    assert "scoring" in {c["key"] for c in document["categories"]}


def test_no_euroleague_only_stat_is_in_the_nba_catalog() -> None:
    document = _metrics_document()
    assert not EUROLEAGUE_ONLY_KEYS & {m["key"] for m in document["metrics"]}
    # and the NBA entries carry no league marker: ``leagues`` belongs to the other list
    assert not [m["key"] for m in document["metrics"] if "leagues" in m or "perModes" in m]


def test_the_euroleague_block_is_the_designed_list_in_the_nba_entry_shape() -> None:
    document = _metrics_document()
    entries = document["leagueMetrics"]["euroleague"]
    assert list(document["leagueMetrics"]) == ["euroleague"]
    keys = [e["key"] for e in entries]
    assert len(keys) == len(set(keys))
    assert set(keys) == DESIGNED_EUROLEAGUE_KEYS

    nba_shape = set(document["metrics"][0])
    formats = {f["key"] for f in document["formats"]}
    categories = {c["key"] for c in document["categories"]}
    for entry in entries:
        assert set(entry) == nba_shape | {"leagues", "perModes"}, entry["key"]
        assert entry["leagues"] == ["euroleague"]
        assert entry["format"] in formats and entry["category"] in categories
        assert set(entry["perModes"]) <= {"PerGame", "Totals", "Per40"}
        assert "PerGame" in entry["perModes"] and "Totals" in entry["perModes"]
        assert entry["glossary"]
    by_key = {e["key"]: e for e in entries}
    pir = by_key["pir"]
    assert (pir["shortName"], pir["format"]) == ("PIR", "decimal1")
    assert "not recomputed by Hardwood" in pir["glossary"]
    assert by_key["blk_against"]["name"] == "Blocks against"
    assert by_key["fouls_drawn"]["name"] == "Fouls drawn"
    # a per-40 rate of minutes is not a statistic
    assert "Per40" not in by_key["min"]["perModes"]


def test_a_stat_shared_by_both_leagues_is_formatted_the_same_way_in_both() -> None:
    document = _metrics_document()
    nba = {m["key"]: m for m in document["metrics"]}
    shared = [e for e in document["leagueMetrics"]["euroleague"] if e["key"] in nba]
    assert len(shared) >= 15, [e["key"] for e in shared]
    for entry in shared:
        twin = nba[entry["key"]]
        for field in ("name", "shortName", "format", "higherIsBetter", "category"):
            assert entry[field] == twin[field], (entry["key"], field)


def test_the_euroleague_block_covers_every_stat_the_euroleague_stats_table_emits() -> None:
    """A client formats a ``/v1/el/stats/players`` value by looking its key up here, so a key the
    table emits and this list lacks would render unformatted."""
    from nbastats.euroleague.read import stats

    emitted = {"min", *stats.METRIC_COLUMNS, *stats.PERCENTAGES}
    described = {e["key"] for e in _metrics_document()["leagueMetrics"]["euroleague"]}
    assert emitted <= described, sorted(emitted - described)


def test_the_web_contracts_carry_both_new_things() -> None:
    """``gen_web_contracts.py`` embeds ``metrics.json`` verbatim, so ``leagueMetrics`` and the new
    metric key reach the web with no generator change; this proves the regeneration happened."""
    text = (BACKEND.parent / "web" / "src" / "generated" / "contracts.ts").read_text("utf-8")
    assert '| "opp_pts"' in text
    assert '"leagueMetrics"' in text
    assert '"blk_against"' in text


# --------------------------------------------------------------------------- the wiring


@contextmanager
def _client(**environment: str | None) -> Iterator[TestClient]:
    """A client over the seeded NBA database for an app built with ``environment`` applied."""
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


@pytest.fixture()
def quiet_euroleague(tmp_path: Path, seeded_engine: Any) -> dict[str, str | None]:
    """Environment that keeps the EuroLeague from reading or writing anything real."""
    return {
        "HARDWOOD_DATA_DIR": str(tmp_path / "data"),
        "HARDWOOD_EL_DATABASE_URL": f"sqlite:///{tmp_path / 'el.db'}",
        "HARDWOOD_WORKBOOK_PATH": None,
        "HARDWOOD_EL_DEMO": "0",
        "HARDWOOD_EL_MODE": None,
        "HARDWOOD_EL_LIVE": "0",
        "HARDWOOD_EL_ENABLED": None,
        "HARDWOOD_API_KEY": None,
    }


def test_the_new_routers_are_on_the_optional_path() -> None:
    for name in NEW_ROUTER_MODULES:
        assert name in app_module.OPTIONAL_ROUTE_MODULES, name
    assert len(set(app_module.OPTIONAL_ROUTE_MODULES)) == len(app_module.OPTIONAL_ROUTE_MODULES)
    # None of them takes a bespoke guard profile: the standard API-key-or-session gate applies.
    assert not set(NEW_ROUTER_MODULES) & set(app_module.OPTIONAL_ROUTE_GUARDS)
    assert app_module.ROUTE_SWITCHES == {"routes_euroleague": "HARDWOOD_EL_ENABLED"}


def test_the_euroleague_router_is_mounted_at_v1_el(quiet_euroleague: dict) -> None:
    with _client(**quiet_euroleague) as client:
        paths = client.app.openapi()["paths"]
        assert "routes_euroleague" in client.app.state.mounted_route_modules
        assert "/v1/el/meta" in paths and "/v1/el/health" in paths
        assert not [p for p in paths if p.startswith("/el")], "the prefix must be /v1/el"
        assert client.get("/v1/health").status_code == 200
        assert client.get("/v1/el/health").status_code == 200


def test_the_switch_skips_the_euroleague_entirely(
    quiet_euroleague: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bootstrap = importlib.import_module("nbastats.euroleague.bootstrap")
    bootstrap.reset_state()
    environment = {**quiet_euroleague, "HARDWOOD_EL_ENABLED": "0"}
    with _client(**environment) as client:
        paths = client.app.openapi()["paths"]
        assert not [p for p in paths if p.startswith("/v1/el")]
        assert "routes_euroleague" not in client.app.state.mounted_route_modules
        assert client.get("/v1/health").status_code == 200
        assert client.get("/v1/el/health").status_code == 404
    assert bootstrap.get_state().state == bootstrap.STATE_NOT_PREPARED, "bootstrap must not run"
    assert not (tmp_path / "el.db").exists(), "a switched-off EuroLeague opens no store"


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF", " 0 "])
def test_every_plainly_false_spelling_switches_the_euroleague_off(value: str) -> None:
    previous = os.environ.get("HARDWOOD_EL_ENABLED")
    os.environ["HARDWOOD_EL_ENABLED"] = value
    try:
        assert app_module._switched_off("routes_euroleague") is True
        assert app_module._switched_off("routes_dashboard") is False
    finally:
        if previous is None:
            os.environ.pop("HARDWOOD_EL_ENABLED", None)
        else:
            os.environ["HARDWOOD_EL_ENABLED"] = previous


@pytest.mark.parametrize("value", [None, "", "1", "true", "on", "yes", "maybe"])
def test_anything_else_leaves_the_euroleague_on(value: str | None) -> None:
    previous = os.environ.get("HARDWOOD_EL_ENABLED")
    if value is None:
        os.environ.pop("HARDWOOD_EL_ENABLED", None)
    else:
        os.environ["HARDWOOD_EL_ENABLED"] = value
    try:
        assert app_module._switched_off("routes_euroleague") is False
    finally:
        if previous is None:
            os.environ.pop("HARDWOOD_EL_ENABLED", None)
        else:
            os.environ["HARDWOOD_EL_ENABLED"] = previous


def test_a_euroleague_that_cannot_start_never_stops_the_nba(
    quiet_euroleague: dict, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    bootstrap = importlib.import_module("nbastats.euroleague.bootstrap")

    def explode(*_: Any, **__: Any) -> None:
        raise RuntimeError("the EuroLeague store is on fire")

    monkeypatch.setattr(bootstrap, "prepare", explode)
    with caplog.at_level(logging.ERROR, logger="nbastats.api"):
        with _client(**quiet_euroleague) as client:
            assert client.get("/v1/health").status_code == 200
            assert client.get("/v1/teams").status_code == 200
    assert "the EuroLeague could not start" in caplog.text


def test_a_missing_euroleague_package_is_not_an_error(
    quiet_euroleague: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = importlib.import_module

    def without_the_euroleague(name: str, package: str | None = None) -> Any:
        if "euroleague" in name:
            raise ImportError(f"No module named {name!r}")
        return real(name, package)

    monkeypatch.setattr(app_module.importlib, "import_module", without_the_euroleague)
    with _client(**quiet_euroleague) as client:
        assert client.get("/v1/health").status_code == 200
        assert "routes_euroleague" not in client.app.state.mounted_route_modules
        assert client.get("/v1/teams").status_code == 200


def test_the_euroleague_health_path_is_the_only_new_key_exemption() -> None:
    assert deps.API_KEY_EXEMPT_PATHS >= {"/v1/health", "/v1/health/"}
    assert {"/v1/el/health", "/v1/el/health/"} <= deps.API_KEY_EXEMPT_PATHS
    extra = deps.API_KEY_EXEMPT_PATHS - {
        "/v1/health",
        "/v1/health/",
        "/v1/el/health",
        "/v1/el/health/",
    }
    assert extra == frozenset(), f"unexpected paths exempt from the API key: {sorted(extra)}"


def test_with_a_key_set_only_euroleague_health_answers_without_it(quiet_euroleague: dict) -> None:
    environment = {**quiet_euroleague, "HARDWOOD_API_KEY": "isolation-test-key"}
    with _client(**environment) as client:
        assert client.get("/v1/health").status_code == 200
        assert client.get("/v1/el/health").status_code == 200
        assert client.get("/v1/el/meta").status_code == 401
        assert client.get("/v1/el/teams").status_code == 401
        keyed = {"X-API-Key": "isolation-test-key"}
        assert client.get("/v1/el/meta", headers=keyed).status_code == 200
