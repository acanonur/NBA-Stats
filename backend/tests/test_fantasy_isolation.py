"""The fantasy toolkit and the injury data never meet.

``docs/LEGAL.md`` §2a is candid that the argument for the fantasy toolkit (it analyses one manager's
own roster decisions and does not operate a game) is the thinnest in the repository. Feeding the
NBA's official injury statuses into fantasy valuation would widen it: an injury report is NBA.com
content used under the private, non-commercial posture of §2, and "who is out tonight" is exactly
the input that turns a roster tool into something people would want to share. The decision is
therefore structural, not a promise: neither ``nbastats/fantasy.py``, nor its widgets, nor its
routes import anything from ``nbastats.nba_intel`` or ``nbastats.intel`` (or the EuroLeague and
matchup packages), and nothing in those packages imports the fantasy code either.

Four checks, from cheapest to strongest:

1. an AST scan of every fantasy module for static imports, in every spelling (absolute, relative,
   ``from .. import nba_intel``, ``import nbastats.nba_intel.status as s``);
2. a scan of the same files for a *string* naming those packages, which is how a dynamic
   ``importlib.import_module("...")`` would hide;
3. a fresh interpreter that imports ``nbastats.fantasy`` and asserts the intel packages were never
   loaded as a side effect;
4. the reverse direction, so the boundary cannot be crossed from the other side.

The glob is checked to be non-empty, so this test cannot pass by finding nothing to scan.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
PACKAGE = BACKEND / "nbastats"

#: Packages the fantasy code must never reach, by the first path component under ``nbastats``.
FORBIDDEN = ("nba_intel", "intel", "nba_matchup", "euroleague")

FANTASY_MODULES = [
    PACKAGE / "fantasy.py",
    *sorted((PACKAGE / "widgets").glob("fantasy_*.py")),
    PACKAGE / "api" / "routes_fantasy.py",
]


def _absolute_imports(path: Path) -> set[str]:
    package = ".".join(path.relative_to(BACKEND).with_suffix("").parts[:-1])
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                module = ".".join([*base, *([node.module] if node.module else [])])
            else:
                module = node.module or ""
            found.add(module)
            found.update(f"{module}.{alias.name}" for alias in node.names)  # `from .. import x`
    return found


def _reaches(module: str, packages: tuple[str, ...]) -> bool:
    parts = module.split(".")
    return len(parts) >= 2 and parts[0] == "nbastats" and parts[1] in packages


def test_the_scan_has_something_to_scan() -> None:
    names = {path.name for path in FANTASY_MODULES}
    assert {
        "fantasy.py",
        "fantasy_draft_board.py",
        "fantasy_trade.py",
        "routes_fantasy.py",
    } <= names
    assert all(path.is_file() for path in FANTASY_MODULES)


@pytest.mark.parametrize("path", FANTASY_MODULES, ids=lambda p: p.name)
def test_fantasy_code_imports_nothing_from_the_intel_packages(path: Path) -> None:
    offending = sorted(m for m in _absolute_imports(path) if _reaches(m, FORBIDDEN))
    assert offending == [], f"{path.name} imports {offending}"


@pytest.mark.parametrize("path", FANTASY_MODULES, ids=lambda p: p.name)
def test_fantasy_code_does_not_name_those_packages_in_a_string_either(path: Path) -> None:
    """A dynamic import hides the package name in a string literal; none may appear."""
    tree = ast.parse(path.read_text())
    strings = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    for value in strings:
        for package in FORBIDDEN:
            assert f"nbastats.{package}" not in value and f"..{package}" not in value, (
                path.name,
                value[:80],
            )
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert not {"NbaIntelStatus", "NbaIntelOverride", "NbaIntelSnapshot"} & names


def test_importing_the_fantasy_module_never_loads_the_intel_packages() -> None:
    code = (
        "import sys, nbastats.fantasy;"
        "loaded = sorted(m for m in sys.modules if m.startswith(("
        "'nbastats.nba_intel', 'nbastats.intel', 'nbastats.nba_matchup', 'nbastats.euroleague')));"
        "print(loaded); sys.exit(1 if loaded else 0)"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=BACKEND)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_intel_packages_never_import_the_fantasy_code() -> None:
    for base in (PACKAGE / "intel", PACKAGE / "nba_intel"):
        for path in sorted(base.rglob("*.py")):
            for module in _absolute_imports(path):
                parts = module.split(".")
                assert parts[:2] != ["nbastats", "fantasy"], f"{path.name} imports {module}"
                assert not (
                    len(parts) >= 3 and parts[1] == "widgets" and parts[2].startswith("fantasy")
                ), f"{path.name} imports {module}"
                assert parts[:3] != ["nbastats", "api", "routes_fantasy"], path.name
