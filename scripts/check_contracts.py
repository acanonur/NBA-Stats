#!/usr/bin/env python3
"""The contract drift guard. Run it from anywhere: ``python3 scripts/check_contracts.py``.

Hardwood is two codebases held together by one directory. ``contracts/`` is generated from
``contracts/tools/gen_*.py``, read at runtime by the service, and bundled verbatim into the
iOS app — three copies of the same truth, each of which can be edited independently and none
of which fails loudly when it stops agreeing with the others. This script is what makes that
failure loud, and it is the first job in ``.github/workflows/backend.yml``.

Eight checks, one summary line each, exit 1 if any of them fails:

a. the three catalogs regenerate **byte-identically** from their generators, so nobody has
   hand-edited a generated file;
b. every preset widget validates against the widget catalog — using ``gen_presets.validate``
   itself, so the rule has exactly one definition;
c. every metric key a preset references exists in ``metrics.json``;
d. the widget kinds the service implements are exactly the kinds the catalog declares;
e. the copies bundled in the iOS app are byte-identical to ``contracts/``;
f. every golden fixture parses as JSON;
g. no two files bound for the app bundle share a basename;
h. the hand-written Xcode project still resolves, and nothing is produced twice.

Checks (g) and (h) are here because the iOS half of this repository is written on a machine
with no Xcode. Both encode a build failure that otherwise only appears on someone's Mac, as a
DerivedData path with no indication of which two files are at fault.

Nothing here needs the network, and nothing writes to the repository.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
TOOLS = CONTRACTS / "tools"
FIXTURES = CONTRACTS / "fixtures"
IOS_CONTRACTS = ROOT / "ios" / "NBAStats" / "Resources" / "Contracts"
BACKEND = ROOT / "backend"

#: The generated catalogs, and the generator that owns each.
CATALOGS: tuple[tuple[str, str], ...] = (
    ("metrics.json", "gen_metrics.py"),
    ("widgets.json", "gen_widgets.py"),
    ("presets.json", "gen_presets.py"),
)

#: Config field types whose value is one metric key, and those whose value is a list of them.
METRIC_FIELD_TYPES = frozenset({"metric"})
METRIC_LIST_FIELD_TYPES = frozenset({"metricList"})


class CheckFailure(Exception):
    """Raised by a check with one detail line per problem found."""

    def __init__(self, *details: str) -> None:
        super().__init__("; ".join(details))
        self.details = list(details)


def _load(name: str) -> Any:
    with (CONTRACTS / name).open(encoding="utf-8") as handle:
        return json.load(handle)


def _import_generator(filename: str) -> Any:
    """Import a generator module by path, without running its ``__main__`` body."""
    path = TOOLS / filename
    spec = importlib.util.spec_from_file_location(f"hardwood_contracts_{path.stem}", path)
    if spec is None or spec.loader is None:  # pragma: no cover - a deleted generator
        raise CheckFailure(f"{path} could not be imported")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- a. generators


def check_catalogs_regenerate() -> str:
    """(a) Each catalog must be exactly what its generator prints."""
    problems: list[str] = []
    for filename, generator in CATALOGS:
        target = CONTRACTS / filename
        if not target.is_file():
            problems.append(f"{filename} is missing")
            continue
        result = subprocess.run(
            [sys.executable, str(TOOLS / generator)],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        if result.returncode != 0:
            detail = result.stderr.strip().splitlines() or ["(no output)"]
            problems.append(
                f"{generator} exited {result.returncode}: " + " / ".join(detail[:6])
            )
            continue
        committed = target.read_text(encoding="utf-8")
        if result.stdout != committed:
            problems.append(
                f"{filename} differs from `python3 contracts/tools/{generator}` "
                f"({_first_difference(committed, result.stdout)})"
            )
    if problems:
        raise CheckFailure(*problems)
    return f"{len(CATALOGS)} catalogs regenerate byte-identically"


def _first_difference(left: str, right: str) -> str:
    """``line 41: "x" vs "y"`` — enough to find the edit without printing the file."""
    left_lines = left.splitlines()
    right_lines = right.splitlines()
    for index, (one, two) in enumerate(zip(left_lines, right_lines), start=1):
        if one != two:
            return f"first difference at line {index}: {one.strip()!r} vs {two.strip()!r}"
    if len(left_lines) != len(right_lines):
        return f"committed has {len(left_lines)} lines, generated has {len(right_lines)}"
    return "the files differ only in trailing whitespace"


# --------------------------------------------------------------------------- b. presets


def check_preset_widgets_validate() -> str:
    """(b) Every preset widget survives the generator's own validator."""
    generator = _import_generator("gen_presets.py")
    validate = getattr(generator, "validate", None)
    if not callable(validate):
        raise CheckFailure(
            "contracts/tools/gen_presets.py no longer exposes validate(); "
            "check_contracts.py imports it rather than keeping a second copy"
        )
    document = _load("presets.json")
    presets = document.get("presets", [])

    # Validate what is actually committed, not only what the generator holds in memory: a
    # hand-edited presets.json is exactly the drift this check exists for.
    problems = list(validate(presets))
    if problems:
        raise CheckFailure(*problems)
    widgets = sum(len(preset.get("widgets", [])) for preset in presets)
    return f"{widgets} preset widgets across {len(presets)} presets validate"


# --------------------------------------------------------------------------- c. metrics


def _metric_keys_in_presets(presets: Iterable[dict[str, Any]], fields: dict[str, str]):
    """Yield ``(preset, widget id, config key, metric key)`` for every metric reference."""
    for preset in presets:
        for widget in preset.get("widgets", []):
            for key, value in (widget.get("config") or {}).items():
                field_type = fields.get(f"{widget['kind']}.{key}")
                if field_type in METRIC_FIELD_TYPES and isinstance(value, str):
                    yield preset.get("presetKey"), widget.get("id"), key, value
                elif field_type in METRIC_LIST_FIELD_TYPES and isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            yield preset.get("presetKey"), widget.get("id"), key, item


def check_preset_metrics_exist() -> str:
    """(c) Every metric key a preset names is in the metric catalog."""
    metrics = {metric["key"] for metric in _load("metrics.json")["metrics"]}
    widgets = _load("widgets.json")["widgets"]
    field_types = {
        f"{widget['kind']}.{field['key']}": field["type"]
        for widget in widgets
        for field in widget["config"]
    }
    presets = _load("presets.json").get("presets", [])

    problems: list[str] = []
    seen: set[str] = set()
    for preset_key, widget_id, config_key, metric_key in _metric_keys_in_presets(
        presets, field_types
    ):
        seen.add(metric_key)
        if metric_key not in metrics:
            problems.append(
                f"{preset_key}/{widget_id}.{config_key}: {metric_key!r} is not in metrics.json"
            )
    if problems:
        raise CheckFailure(*problems)
    return f"{len(seen)} distinct metric keys referenced by presets all exist"


# --------------------------------------------------------------------------- d. resolvers


def check_widget_kinds_match() -> str:
    """(d) The kinds the service implements are the kinds the catalog declares."""
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    try:
        from nbastats import widgets as widget_layer  # noqa: PLC0415 - imported on demand
    except Exception as exc:  # noqa: BLE001 - the import itself is the assertion
        raise CheckFailure(
            f"backend/nbastats/widgets/__init__.py could not be imported: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    catalog_kinds = [widget["kind"] for widget in _load("widgets.json")["widgets"]]
    implemented = set(widget_layer.RESOLVERS)
    missing = sorted(set(catalog_kinds) - implemented)
    extra = sorted(implemented - set(catalog_kinds))
    problems = []
    if missing:
        problems.append(f"widgets.json declares kinds with no resolver: {missing}")
    if extra:
        problems.append(f"nbastats.widgets implements kinds the catalog does not know: {extra}")
    no_ttl = sorted(set(catalog_kinds) - set(widget_layer.TTL_SECONDS))
    if no_ttl:
        problems.append(f"nbastats.widgets.TTL_SECONDS has no entry for {no_ttl}")
    if problems:
        raise CheckFailure(*problems)
    return f"{len(catalog_kinds)} widget kinds implemented, one resolver each"


# --------------------------------------------------------------------------- e. iOS copies


def check_ios_bundle_matches() -> str:
    """(e) The catalogs bundled in the app are byte-identical to ``contracts/``."""
    if not IOS_CONTRACTS.is_dir():
        raise CheckFailure(f"{IOS_CONTRACTS.relative_to(ROOT)} does not exist")
    problems: list[str] = []
    expected = {filename for filename, _ in CATALOGS}
    for filename in sorted(expected):
        bundled = IOS_CONTRACTS / filename
        if not bundled.is_file():
            problems.append(f"{filename} is not bundled in the app")
            continue
        if bundled.read_bytes() != (CONTRACTS / filename).read_bytes():
            problems.append(
                f"ios/.../Contracts/{filename} differs from contracts/{filename} "
                "(run scripts/sync_contracts.sh)"
            )
    stray = sorted(path.name for path in IOS_CONTRACTS.glob("*.json") if path.name not in expected)
    if stray:
        problems.append(f"the app bundles catalogs contracts/ does not have: {stray}")
    if problems:
        raise CheckFailure(*problems)
    return f"{len(expected)} bundled catalogs match contracts/"


# --------------------------------------------------------------------------- f. fixtures


def check_fixtures_parse() -> str:
    """(f) Every golden fixture is readable JSON."""
    if not FIXTURES.is_dir():
        raise CheckFailure(
            f"{FIXTURES.relative_to(ROOT)} does not exist; run "
            "`python3 -m nbastats.fixtures_export --out contracts/fixtures` from backend/"
        )
    paths = sorted(FIXTURES.glob("*.json"))
    if not paths:
        raise CheckFailure(f"{FIXTURES.relative_to(ROOT)} holds no fixtures")
    problems: list[str] = []
    for path in paths:
        try:
            with path.open(encoding="utf-8") as handle:
                json.load(handle)
        except (OSError, ValueError) as exc:
            problems.append(f"fixtures/{path.name}: {exc}")
    if problems:
        raise CheckFailure(*problems)
    return f"{len(paths)} golden fixtures parse as JSON"


def check_app_bundle_has_no_name_collision() -> str:
    """(g) No two app resources share a basename.

    Xcode 16 file-system synchronized groups flatten resource subdirectories into the bundle
    root, so ``Resources/Contracts/presets.json`` and ``Resources/Fixtures/presets.json`` both
    resolve to ``Hardwood.app/presets.json`` and the build fails with "Multiple commands
    produce ...". That is a linker-stage failure with a DerivedData path in the message and no
    hint about which two files are at fault, which makes it a miserable thing to debug on the
    machine that happens to own the only Swift compiler.

    Checking it here costs nothing and names both paths. ``scripts/sync_contracts.sh`` keeps
    the excluded set; this check is what proves the exclusion is still doing its job.

    Asset catalogs are skipped: ``actool`` compiles them into a single ``Assets.car``, so the
    many ``Contents.json`` files inside them never reach the bundle root.
    """
    app = ROOT / "ios" / "NBAStats"
    if not app.is_dir():
        return "the iOS app is not in this checkout; skipped"

    seen: dict[str, list[Path]] = {}
    for path in sorted(app.rglob("*")):
        if not path.is_file():
            continue
        if any(part.endswith(".xcassets") for part in path.parts) or path.name == "Info.plist":
            continue
        if path.suffix.lower() not in {".json", ".plist", ".strings", ".txt", ".md"}:
            continue
        seen.setdefault(path.name, []).append(path.relative_to(ROOT))

    problems = [
        f"{name} is bundled from {len(paths)} places and would collide at the bundle root: "
        + ", ".join(str(p) for p in paths)
        for name, paths in sorted(seen.items())
        if len(paths) > 1
    ]
    if problems:
        raise CheckFailure(*problems)
    return f"{len(seen)} bundled resources, no basename collision"


def check_xcode_project_is_sound() -> str:
    """(h) The hand-maintained ``project.pbxproj`` is structurally intact and has no file that
    two build commands would both produce.

    This project file is written by hand, on a machine with no Xcode, so nothing here can open
    it to find out whether it still makes sense. Two failure modes are worth catching cheaply:

    *Reference rot.* Every object is addressed by an opaque id. A hand edit that drops an
    object, or references one that was never defined, produces a project Xcode may refuse to
    open — a worse outcome than a build error, and one with no useful diagnostic.

    *The Info.plist trap.* A file-system synchronized group makes every file in its folder a
    target member automatically. ``Info.plist`` lives in that folder and is also processed via
    ``INFOPLIST_FILE``, so without a ``membershipExceptions`` entry both the copy step and the
    plist step produce ``Hardwood.app/Info.plist`` and the build stops with "Multiple commands
    produce ...". Xcode writes that exception itself when you add the file through the UI; a
    hand-written project has to remember.
    """
    project = ROOT / "ios" / "NBAStats.xcodeproj" / "project.pbxproj"
    if not project.is_file():
        return "no Xcode project in this checkout; skipped"

    text = project.read_text(encoding="utf-8")
    problems: list[str] = []

    defined = re.findall(r"^\t\t([A-Fa-f0-9]{8,32})\s*(?:/\*.*?\*/)?\s*=\s*\{", text, re.M)
    if not defined:
        raise CheckFailure("no objects found; the project file is not in the expected format")
    width = len(defined[0])
    known = set(defined)
    referenced = set(re.findall(r"\b([A-Fa-f0-9]{%d})\b" % width, text))
    for missing in sorted(referenced - known):
        problems.append(f"object id {missing} is referenced but never defined")

    root_object = re.search(r"rootObject\s*=\s*([A-Fa-f0-9]+)", text)
    if not root_object or root_object.group(1) not in known:
        problems.append("rootObject does not resolve to a defined object")

    # Every folder mirrored into a target, and the files each target excludes from it.
    synchronized = set(re.findall(r"isa = PBXFileSystemSynchronizedRootGroup;.*?path = ([^;]+);",
                                  text, re.S))
    excepted = {
        name.strip().strip('",')
        for block in re.findall(r"membershipExceptions\s*=\s*\(([^)]*)\)", text, re.S)
        for name in block.split()
        if name.strip(" ,")
    }
    for setting in sorted(set(re.findall(r"INFOPLIST_FILE\s*=\s*([^;]+);", text))):
        plist = Path(setting.strip().strip('"'))
        if plist.parts and plist.parts[0] in synchronized and plist.name not in excepted:
            problems.append(
                f"INFOPLIST_FILE is {plist}, inside synchronized folder {plist.parts[0]}/, "
                f"but {plist.name} is not in any membershipExceptions — the copy step and the "
                "plist step would both produce it"
            )

    if problems:
        raise CheckFailure(*problems)
    return (
        f"{len(known)} objects, all references resolve, "
        f"{len(synchronized)} synchronized folder(s), Info.plist excluded"
    )


# --------------------------------------------------------------------------- runner

CHECKS: tuple[tuple[str, str, Callable[[], str]], ...] = (
    ("a", "catalogs regenerate from contracts/tools", check_catalogs_regenerate),
    ("b", "preset widgets validate against the catalog", check_preset_widgets_validate),
    ("c", "preset metric keys exist", check_preset_metrics_exist),
    ("d", "widget kinds match the service", check_widget_kinds_match),
    ("e", "iOS bundle matches contracts/", check_ios_bundle_matches),
    ("f", "golden fixtures parse", check_fixtures_parse),
    ("g", "app bundle has no resource name collision", check_app_bundle_has_no_name_collision),
    ("h", "Xcode project is structurally sound", check_xcode_project_is_sound),
)


def main() -> int:
    failures = 0
    for label, title, check in CHECKS:
        try:
            summary = check()
        except CheckFailure as failure:
            failures += 1
            print(f"FAIL  {label}. {title}")
            for detail in failure.details:
                print(f"          {detail}")
        except Exception as exc:  # noqa: BLE001 - a broken check is a failed check
            failures += 1
            print(f"FAIL  {label}. {title}")
            print(f"          unexpected {type(exc).__name__}: {exc}")
        else:
            print(f"ok    {label}. {title} — {summary}")

    if failures:
        print(f"\n{failures} of {len(CHECKS)} contract checks failed.")
        return 1
    print(f"\nAll {len(CHECKS)} contract checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
