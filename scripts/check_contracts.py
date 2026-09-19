#!/usr/bin/env python3
"""The contract drift guard. Run it from anywhere: ``python3 scripts/check_contracts.py``.

Hardwood is two codebases held together by one directory. ``contracts/`` is generated from
``contracts/tools/gen_*.py``, read at runtime by the service, and bundled verbatim into the
iOS app — three copies of the same truth, each of which can be edited independently and none
of which fails loudly when it stops agreeing with the others. This script is what makes that
failure loud, and it is the first job in ``.github/workflows/backend.yml``.

Twelve checks, one summary line each, exit 1 if any of them fails:

a. the three catalogs regenerate **byte-identically** from their generators, so nobody has
   hand-edited a generated file;
b. every preset widget validates against the widget catalog — using ``gen_presets.validate``
   itself, so the rule has exactly one definition;
c. every metric key a preset references exists in ``metrics.json``;
d. the widget kinds the service implements are exactly the kinds the catalog declares;
e. the copies bundled in the iOS app are byte-identical to ``contracts/``;
f. every golden fixture parses as JSON;
g. no two files bound for the app bundle share a basename;
h. the hand-written Xcode project still resolves, and nothing is produced twice;
i. the generated web artifacts (design tokens, the web's typed contracts, the cross-language
   parity fixtures) regenerate **byte-identically** from their generators, the same guarantee
   check (a) gives the three JSON catalogs;
j. the widget kinds ``widgets.json`` declares, the directories under ``web/src/widgets/``, and
   the ``*Widget.swift`` files under ``ios/NBAStats/Widgets/`` all name the same sixteen kinds,
   tolerating whatever ``web/src/widgets/PENDING.txt`` (or, before that file exists, simply "no
   directory yet") says the web has not ported;
k. every widget kind has a ``contracts/fixtures/widget_<kind>.json`` payload fixture, and — once
   the web test that enumerates them exists — that it lists every one;
l. each cross-language parity fixture (§8's ``gen_parity_cases.py`` output) is referenced by
   name from the test file(s) meant to assert it, once those files exist.

Checks (g) and (h) are here because the iOS half of this repository is written on a machine
with no Xcode. Both encode a build failure that otherwise only appears on someone's Mac, as a
DerivedData path with no indication of which two files are at fault.

Checks (i)-(l) police the web half the same way (a)-(h) police the iOS half, added alongside it
rather than folded into it: ``web/src/generated/**`` and ``web/src/widgets/**`` are built by
work packages that land after this script does (WP1-WP5, WEB_DESIGN.md §9), so unlike (a)-(h),
which have every input already in this checkout, (j)-(l) start in a state where most of what they
would police does not exist yet. Each is written to report that plainly and pass anyway — a
missing *file* here means "not built yet", the thing this whole contract-check exists to make
loud is a *disagreement* between two things that do exist.

Nothing here needs the network, and nothing writes to the repository.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
TOOLS = CONTRACTS / "tools"
FIXTURES = CONTRACTS / "fixtures"
IOS_CONTRACTS = ROOT / "ios" / "NBAStats" / "Resources" / "Contracts"
IOS_WIDGETS = ROOT / "ios" / "NBAStats" / "Widgets"
IOS_TESTS = ROOT / "ios" / "NBAStatsTests"
WEB = ROOT / "web"
WEB_WIDGETS = WEB / "src" / "widgets"
WEB_GENERATED = WEB / "src" / "generated"
BACKEND = ROOT / "backend"

#: The generated catalogs, and the generator that owns each.
CATALOGS: tuple[tuple[str, str], ...] = (
    ("metrics.json", "gen_metrics.py"),
    ("widgets.json", "gen_widgets.py"),
    ("presets.json", "gen_presets.py"),
)

#: Generated web artifacts, and the generator that owns each — a SEPARATE tuple from CATALOGS on
#: purpose (§0.1-B): check (e) builds its required iOS-bundle set straight from ``CATALOGS``, and
#: none of these ship inside the iOS app, so adding them to ``CATALOGS`` would make check (e)
#: demand an ``ios/.../Resources/Contracts/tokens.css`` that is never meant to exist.
WEB_ARTIFACTS: tuple[tuple[str, str], ...] = (
    ("contracts/theme.json", "gen_theme.py"),
    ("web/src/generated/tokens.css", "gen_web_tokens.py"),
    ("web/src/generated/tokens.ts", "gen_web_tokens.py"),
    ("web/src/generated/contracts.ts", "gen_web_contracts.py"),
    ("web/src/generated/registry.ts", "gen_web_contracts.py"),
    ("contracts/fixtures/layout_migration_cases.json", "gen_parity_cases.py"),
    ("contracts/fixtures/monogram_cases.json", "gen_parity_cases.py"),
    ("contracts/fixtures/format_cases.json", "gen_parity_cases.py"),
)

#: ``gen_theme.py`` prints one file to stdout, exactly like every ``CATALOGS`` generator, so
#: check (i) diffs its stdout the way check (a) already does. The other three each own more than
#: one output file living in the same directory, so they take ``--out DIR`` instead and check (i)
#: diffs a scratch directory against the committed files — see ``gen_web_tokens.py``'s docstring.
_WEB_ARTIFACT_DIR_GENERATORS = frozenset(
    {"gen_web_tokens.py", "gen_web_contracts.py", "gen_parity_cases.py"}
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


#: Keys Xcode injects for you when GENERATE_INFOPLIST_FILE is YES, and that a hand-written
#: plist must therefore carry itself. Each one is here because of the failure it causes:
#: without CFBundleIdentifier the simulator refuses to install ("Missing bundle ID"), and
#: without CFBundleExecutable the app installs and then fails to launch.
REQUIRED_INFO_PLIST_KEYS = (
    "CFBundleIdentifier",
    "CFBundleExecutable",
    "CFBundleName",
    "CFBundlePackageType",
    "CFBundleInfoDictionaryVersion",
    "CFBundleDevelopmentRegion",
    "CFBundleShortVersionString",
    "CFBundleVersion",
)


def _info_plist_problems(plist_path: Path, project_text: str) -> list[str]:
    """Check a hand-written Info.plist parses and carries the keys Xcode is not filling in."""
    import plistlib

    if "GENERATE_INFOPLIST_FILE = YES" in project_text and "GENERATE_INFOPLIST_FILE = NO" not in project_text:
        return []  # Xcode generates the file; nothing to police.
    try:
        with plist_path.open("rb") as handle:
            contents = plistlib.load(handle)
    except Exception as exc:  # noqa: BLE001 - an unparseable plist is the finding
        return [f"{plist_path.name} does not parse: {type(exc).__name__}: {exc}"]

    missing = [key for key in REQUIRED_INFO_PLIST_KEYS if key not in contents]
    if missing:
        return [
            f"{plist_path.name} is missing {', '.join(missing)}. GENERATE_INFOPLIST_FILE is NO, "
            "so Xcode injects nothing and every CFBundle key has to be in the file"
        ]

    # The identifier has to agree with the build setting, or the app installs under a name
    # nothing else in the project expects.
    identifier = str(contents.get("CFBundleIdentifier", ""))
    declared = set(re.findall(r"PRODUCT_BUNDLE_IDENTIFIER\s*=\s*([^;]+);", project_text))
    if not identifier.startswith("$(") and identifier and declared:
        if identifier not in {value.strip().strip('"') for value in declared}:
            return [
                f"CFBundleIdentifier is {identifier!r} but PRODUCT_BUNDLE_IDENTIFIER is "
                f"{sorted(declared)}"
            ]

    # Every background task the app registers must be declared, or BGTaskScheduler throws.
    declared_tasks = set(contents.get("BGTaskSchedulerPermittedIdentifiers", []) or [])
    if "UIBackgroundModes" in contents and not declared_tasks:
        return ["UIBackgroundModes is set but BGTaskSchedulerPermittedIdentifiers is empty"]
    return []


def check_xcode_project_is_sound() -> str:
    """(h) The hand-maintained ``project.pbxproj`` is structurally intact and has no file that
    two build commands would both produce.

    This project file is written by hand, on a machine with no Xcode, so nothing here can open
    it to find out whether it still makes sense. Two failure modes are worth catching cheaply:

    *Reference rot.* Every object is addressed by an opaque id. A hand edit that drops an
    object, or references one that was never defined, produces a project Xcode may refuse to
    open — a worse outcome than a build error, and one with no useful diagnostic.

    *The Info.plist trap.* A file-system synchronized group makes every file in its folder a
    target member automatically. A plist that lives in that folder is therefore copied into
    ``Hardwood.app/Info.plist`` as a resource, while ``INFOPLIST_FILE`` is separately producing
    that same path — "Multiple commands produce ..." and the build stops. A
    ``membershipExceptions`` entry is supposed to prevent this and, in practice, did not: the
    plist now lives at ``ios/Info.plist``, outside every synchronized folder, which removes the
    mechanism rather than trying to opt one file out of it. This check keeps it there.
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

    # Every folder mirrored into a target. Paths in build settings are relative to the folder
    # holding the .xcodeproj, which is ios/.
    synchronized = {
        name.strip().strip('"')
        for name in re.findall(
            r"isa = PBXFileSystemSynchronizedRootGroup;.*?path = ([^;]+);", text, re.S
        )
    }
    source_root = project.parent.parent
    # The lookbehind matters: GENERATE_INFOPLIST_FILE ends in the same 14 characters, and
    # matching it too made this check complain that "NO" was not a file.
    for setting in sorted(set(re.findall(r"(?<![A-Z_])INFOPLIST_FILE\s*=\s*([^;]+);", text))):
        plist = Path(setting.strip().strip('"'))
        if plist.parts and plist.parts[0] in synchronized:
            problems.append(
                f"INFOPLIST_FILE is {plist}, inside synchronized folder {plist.parts[0]}/. "
                "Everything in that folder is mirrored into the target, so the copy step and "
                "the plist step both produce Hardwood.app/Info.plist. Move it to "
                f"ios/{plist.name} and set INFOPLIST_FILE = {plist.name}"
            )
        if not (source_root / plist).is_file():
            problems.append(f"INFOPLIST_FILE points at {plist}, which does not exist")
            continue
        problems.extend(_info_plist_problems(source_root / plist, text))

    if problems:
        raise CheckFailure(*problems)
    return (
        f"{len(known)} objects, all references resolve, "
        f"{len(synchronized)} synchronized folder(s), Info.plist outside them"
    )


# --------------------------------------------------------------------------- i. web artifacts


def check_web_artifacts_regenerate() -> str:
    """(i) Every ``WEB_ARTIFACTS`` entry is byte-identical to a fresh run of its generator.

    The multi-file generators run once each (not once per file they own) into a scratch
    directory, mirroring how ``sync_contracts.sh`` runs them for real; ``gen_theme.py`` runs the
    same stdout-diff way check (a) already diffs the three JSON catalogs.
    """
    problems: list[str] = []
    generators = sorted({generator for _, generator in WEB_ARTIFACTS})
    for generator in generators:
        owned = [ROOT / filename for filename, gen in WEB_ARTIFACTS if gen == generator]
        script = TOOLS / generator
        if not script.is_file():
            problems.append(f"{generator} does not exist")
            continue

        if generator in _WEB_ARTIFACT_DIR_GENERATORS:
            with tempfile.TemporaryDirectory(prefix="hardwood-contracts-") as scratch:
                result = subprocess.run(
                    [sys.executable, str(script), "--out", scratch],
                    capture_output=True, text=True, cwd=ROOT,
                )
                if result.returncode != 0:
                    detail = result.stderr.strip().splitlines() or ["(no output)"]
                    problems.append(
                        f"{generator} exited {result.returncode}: " + " / ".join(detail[:6])
                    )
                    continue
                for target in owned:
                    fresh = Path(scratch) / target.name
                    if not target.is_file():
                        problems.append(f"{target.relative_to(ROOT)} is missing")
                    elif not fresh.is_file():
                        problems.append(f"{generator} did not produce {target.name}")
                    else:
                        committed_text = target.read_text(encoding="utf-8")
                        fresh_text = fresh.read_text(encoding="utf-8")
                        if committed_text != fresh_text:
                            problems.append(
                                f"{target.relative_to(ROOT)} differs from a fresh "
                                f"`python3 contracts/tools/{generator} --out <dir>` "
                                f"({_first_difference(committed_text, fresh_text)})"
                            )
        else:
            result = subprocess.run(
                [sys.executable, str(script)], capture_output=True, text=True, cwd=ROOT,
            )
            if result.returncode != 0:
                detail = result.stderr.strip().splitlines() or ["(no output)"]
                problems.append(
                    f"{generator} exited {result.returncode}: " + " / ".join(detail[:6])
                )
                continue
            for target in owned:
                if not target.is_file():
                    problems.append(f"{target.relative_to(ROOT)} is missing")
                    continue
                committed_text = target.read_text(encoding="utf-8")
                if result.stdout != committed_text:
                    problems.append(
                        f"{target.relative_to(ROOT)} differs from "
                        f"`python3 contracts/tools/{generator}` "
                        f"({_first_difference(committed_text, result.stdout)})"
                    )
    if problems:
        raise CheckFailure(*problems)
    return f"{len(WEB_ARTIFACTS)} web artifacts regenerate byte-identically"


# --------------------------------------------------------------------------- j. widget kinds (web)

#: The escape hatch (§8.4) cannot become permanent: once more than this many kinds are still
#: unported, the check itself starts failing so the tolerance list has to shrink, not just grow.
MAX_TOLERATED_PENDING_WIDGETS = 12


def _kind_from_swift_widget_filename(filename: str) -> str:
    """``StatTileWidget.swift`` -> ``stat_tile``: strip ``Widget.swift``, then PascalCase to
    snake_case — the same spelling ``contracts/widgets.json`` already uses for ``kind``."""
    stem = filename[: -len("Widget.swift")]
    return re.sub(r"(?<!^)(?=[A-Z])", "_", stem).lower()


def check_widget_kinds_agree() -> str:
    """(j) ``widgets.json``, ``web/src/widgets/``, and ``ios/.../Widgets/*Widget.swift`` name the
    same kinds, tolerating whatever is still pending on the web."""
    catalog_kinds = {widget["kind"] for widget in _load("widgets.json")["widgets"]}

    if not IOS_WIDGETS.is_dir():
        raise CheckFailure(f"{IOS_WIDGETS.relative_to(ROOT)} does not exist")
    ios_kinds = {
        _kind_from_swift_widget_filename(path.name) for path in IOS_WIDGETS.glob("*Widget.swift")
    }

    pending_path = WEB_WIDGETS / "PENDING.txt"
    existing_web_dirs = (
        {p.name for p in WEB_WIDGETS.iterdir() if p.is_dir() and p.name != "__tests__"}
        if WEB_WIDGETS.is_dir() else set()
    )
    if pending_path.is_file():
        pending = {
            line.strip() for line in pending_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        pending_source = "web/src/widgets/PENDING.txt"
        unknown_pending = sorted(pending - catalog_kinds)
        if unknown_pending:
            raise CheckFailure(
                f"{pending_source} names kinds widgets.json does not: {unknown_pending}"
            )
        # The ceiling only binds once there is a file that is actually supposed to shrink: WP5
        # empties it one widget at a time (WEB_DESIGN.md §9), and this is what stops "12" from
        # becoming a number nobody ever has to make true.
        if len(pending) > MAX_TOLERATED_PENDING_WIDGETS:
            raise CheckFailure(
                f"{pending_source} tolerates {len(pending)} kinds, more than the "
                f"{MAX_TOLERATED_PENDING_WIDGETS} the escape hatch allows: {sorted(pending)}"
            )
    else:
        # No PENDING.txt: every kind with no web/src/widgets/<kind>/ directory is implicitly
        # pending, and the filesystem is the list. This is deliberately not the same as having
        # no ceiling.
        #
        # A written PENDING.txt naming all sixteen kinds would fail the ceiling on the very
        # commit that created it, so the gate cannot ship one. But dropping the ceiling whenever
        # the file is absent would mean a web client with four widgets passes this check
        # forever, which is the exact outcome the ceiling exists to prevent. The ratchet
        # therefore binds on the first widget instead: while no web widget exists at all, the
        # web client is "not started" and sixteen pending kinds is the honest reading; the
        # moment one lands, the work is underway and at most MAX_TOLERATED_PENDING_WIDGETS may
        # still be missing. Nothing has to be hand-maintained, and the number has to be made
        # true by the same commit that first claims progress.
        pending = catalog_kinds - existing_web_dirs
        pending_source = "implicit (no web/src/widgets/PENDING.txt; the filesystem is the list)"
        if existing_web_dirs and len(pending) > MAX_TOLERATED_PENDING_WIDGETS:
            raise CheckFailure(
                f"{len(existing_web_dirs)} web widget(s) exist, so the web client has started, "
                f"but {len(pending)} kinds are still missing — more than the "
                f"{MAX_TOLERATED_PENDING_WIDGETS} the escape hatch allows: {sorted(pending)}"
            )

    expected_web = catalog_kinds - pending
    missing_web = sorted(expected_web - existing_web_dirs)
    extra_web = sorted(existing_web_dirs - catalog_kinds)
    missing_ios = sorted(catalog_kinds - ios_kinds)
    extra_ios = sorted(ios_kinds - catalog_kinds)

    problems: list[str] = []
    if missing_web:
        problems.append(
            f"no web/src/widgets/ directory, and not tolerated as pending: {missing_web}"
        )
    if extra_web:
        problems.append(f"web/src/widgets/ has directories widgets.json does not know: {extra_web}")
    if missing_ios:
        problems.append(f"no ios/.../Widgets/*Widget.swift for: {missing_ios}")
    if extra_ios:
        problems.append(
            f"ios/.../Widgets/ implements kinds widgets.json does not know: {extra_ios}"
        )
    if problems:
        raise CheckFailure(*problems)
    pending_list = ", ".join(sorted(pending)) if pending else "none"
    return (
        f"{len(catalog_kinds)} widget kinds agree across widgets.json, web and iOS "
        f"(pending on the web: {pending_list})"
    )


# --------------------------------------------------------------------------- k. widget fixtures


def check_widget_fixtures_exist() -> str:
    """(k) A ``contracts/fixtures/widget_<kind>.json`` exists for every kind, and — once
    ``web/src/widgets/__tests__/fixtures.test.ts`` exists — that it enumerates every one."""
    catalog_kinds = sorted(widget["kind"] for widget in _load("widgets.json")["widgets"])
    missing = [kind for kind in catalog_kinds if not (FIXTURES / f"widget_{kind}.json").is_file()]
    if missing:
        raise CheckFailure(f"contracts/fixtures/widget_<kind>.json missing for: {missing}")

    fixtures_test = WEB_WIDGETS / "__tests__" / "fixtures.test.ts"
    if not fixtures_test.is_file():
        return (
            f"{len(catalog_kinds)} widget payload fixtures exist "
            "(web/src/widgets/__tests__/fixtures.test.ts not written yet)"
        )
    text = fixtures_test.read_text(encoding="utf-8")
    not_enumerated = [kind for kind in catalog_kinds if f"widget_{kind}" not in text]
    if not_enumerated:
        raise CheckFailure(
            f"web/src/widgets/__tests__/fixtures.test.ts does not enumerate: {not_enumerated}"
        )
    return f"{len(catalog_kinds)} widget payload fixtures exist and are enumerated by the web test"


# --------------------------------------------------------------------------- l. parity wiring

#: For each parity fixture: exact test files the design names by path (checked directly, and
#: required to reference the fixture once they exist), and test DIRECTORIES to scan for any file
#: that mentions it (used where no exact filename is specified — the web test tree, whose files
#: are WP3's to name).
PARITY_WIRING: tuple[tuple[str, tuple[Path, ...], tuple[Path, ...]], ...] = (
    (
        "layout_migration_cases.json",
        (BACKEND / "tests" / "test_layout_parity.py", IOS_TESTS / "LayoutParityTests.swift"),
        (),
    ),
    (
        "monogram_cases.json",
        (IOS_TESTS / "MonogramParityTests.swift",),
        (WEB / "src" / "design" / "__tests__",),
    ),
    (
        "format_cases.json",
        (BACKEND / "tests" / "test_format_parity.py", IOS_TESTS / "FormatParityTests.swift"),
        (WEB / "src" / "design" / "__tests__",),
    ),
)


def check_parity_cases_are_wired() -> str:
    """(l) Each parity fixture is referenced by name from the test(s) meant to assert it.

    A test file or directory that does not exist yet is reported in the summary, not failed on:
    ``backend/tests/test_layout_parity.py``, the two other backend parity tests, and every Swift
    and web parity test belong to work packages that build after this one (WEB_DESIGN.md §9). A
    file or directory that DOES exist and fails to mention the fixture is a real finding — that
    is the drift this check exists to catch once there is anything to drift.
    """
    problems: list[str] = []
    wired = 0
    not_yet: list[str] = []
    for fixture_name, exact_paths, scan_dirs in PARITY_WIRING:
        if not (FIXTURES / fixture_name).is_file():
            problems.append(f"{fixture_name} does not exist")
            continue
        for test_path in exact_paths:
            if not test_path.is_file():
                not_yet.append(str(test_path.relative_to(ROOT)))
                continue
            if fixture_name not in test_path.read_text(encoding="utf-8"):
                problems.append(
                    f"{test_path.relative_to(ROOT)} does not reference {fixture_name!r}"
                )
                continue
            wired += 1
        for directory in scan_dirs:
            if not directory.is_dir():
                not_yet.append(str(directory.relative_to(ROOT)) + "/*")
                continue
            hits = [
                path for path in directory.rglob("*")
                if path.is_file()
                and fixture_name in path.read_text(encoding="utf-8", errors="ignore")
            ]
            if not hits:
                problems.append(
                    f"nothing under {directory.relative_to(ROOT)}/ references {fixture_name!r}"
                )
                continue
            wired += 1
    if problems:
        raise CheckFailure(*problems)
    detail = f", not yet written: {', '.join(not_yet)}" if not_yet else ""
    return f"{wired} parity-fixture/test pair(s) wired{detail}"


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
    ("i", "web artifacts regenerate from contracts/tools", check_web_artifacts_regenerate),
    ("j", "widget kinds agree across widgets.json, web and iOS", check_widget_kinds_agree),
    ("k", "a payload fixture exists per widget kind", check_widget_fixtures_exist),
    ("l", "parity fixtures are wired into their tests", check_parity_cases_are_wired),
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
