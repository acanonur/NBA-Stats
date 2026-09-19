"""The golden fixtures are the contract, made executable.

``contracts/fixtures/*.json`` is the only artefact both halves of Hardwood read: the service
writes it from its real routes (:mod:`nbastats.fixtures_export`) and the iOS target decodes
it in its tests and replays it in demo mode. That makes two kinds of drift possible, and this
module closes both:

* **The committed bytes stop matching the service.** Somebody changes a serializer and forgets
  to regenerate. :func:`test_every_committed_fixture_is_reproducible` re-runs the whole export
  into a temporary directory and compares byte for byte, which is also the check
  ``.github/workflows/backend.yml`` performs with ``git diff --exit-code``.
* **The fixtures stop matching the written contract.** ``contracts/CONTRACT.md`` is prose with
  JSON examples in it, so nothing normally stops a payload from growing a key the document
  never mentions. :func:`test_fixture_keys_match_the_contract` parses those examples out of the
  markdown and compares the top-level key set of every fixture against them.

The second check reads the *real* document rather than a copy of its key lists: a contract
edit that renames a key fails here even if nobody touches this file.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pytest

from nbastats import catalog
from nbastats import fixtures_export

# --------------------------------------------------------------------------- locations


def repo_root() -> Path:
    """The repository root — the parent of the contracts directory the service reads."""
    return catalog.contracts_dir().parent


CONTRACT_PATH = repo_root() / "contracts" / "CONTRACT.md"
FIXTURES_DIR = repo_root() / "contracts" / "fixtures"

#: Cross-language parity fixtures ``contracts/tools/gen_parity_cases.py`` writes into this same
#: directory (WEB_DESIGN.md §8.3) — a fixed set of input/output cases asserted by Python,
#: TypeScript and Swift, not a snapshot of one API response. They share a directory with
#: :mod:`nbastats.fixtures_export`'s fixtures because that is where the design document and
#: ``scripts/sync_contracts.sh`` put them (the iOS test target needs them alongside the fixtures
#: it already copies from here), but they have their own generator, their own on-disk shape
#: (``sort_keys=True``, unlike the exporter's declaration-order dumps) and their own drift check
#: (``scripts/check_contracts.py`` check (i)). Every test below assumes ``committed`` is exactly
#: what :func:`nbastats.fixtures_export.fixture_names` would produce, so these are excluded at
#: the source rather than special-cased in each one.
PARITY_FIXTURE_NAMES = frozenset({"layout_migration_cases", "monogram_cases", "format_cases"})


# --------------------------------------------------------------------------- CONTRACT.md


def _sections(markdown: str) -> dict[str, str]:
    """``{heading: body}`` for every ``##``/``###`` section of the contract.

    Headings are normalised the way a reader would name them: no hashes, no backticks, no
    trailing italic aside — so ``### \\`GET /v1/sync/stream\\` *(optional…)*`` is keyed
    ``GET /v1/sync/stream``.
    """
    sections: dict[str, str] = {}
    heading: str | None = None
    body: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("## ") or line.startswith("### "):
            if heading is not None:
                sections[heading] = "\n".join(body)
            title = line.lstrip("#").strip()
            if "*(" in title:
                title = title.split("*(")[0].strip()
            heading = title.replace("`", "").strip()
            body = []
        elif heading is not None:
            body.append(line)
    if heading is not None:
        sections[heading] = "\n".join(body)
    return sections


def _json_blocks(section: str) -> list[str]:
    """Every JSON example in one section, in document order.

    Both forms count, because the contract uses both: a fenced ```` ```json ```` block, and an
    inline code span that is itself an object (``GET /v1/teams`` is documented that way).
    """
    blocks: list[str] = []
    lines = section.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.strip().startswith("```json"):
            index += 1
            fence: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                fence.append(lines[index])
                index += 1
            blocks.append("\n".join(fence))
        else:
            for span in _inline_spans(line):
                if span.startswith("{"):
                    blocks.append(span)
        index += 1
    return blocks


def _inline_spans(line: str) -> list[str]:
    """Single-backtick code spans on one line."""
    parts = line.split("`")
    return [part.strip() for part in parts[1::2]]


def _top_level_keys(block: str) -> list[str]:
    """The keys at depth 1 of a JSON-ish example.

    The contract's examples are illustrative rather than parseable — ``{ "...TeamRef" }`` and
    ``"…": "§4"`` both appear — so this walks the text instead of calling :func:`json.loads`.
    A string is a key only when it sits at depth 1 and is followed by a colon, which is
    exactly what leaves the ``"...TeamRef"`` placeholders out.
    """
    keys: list[str] = []
    depth = 0
    index = 0
    length = len(block)
    while index < length:
        char = block[index]
        if char == '"':
            end = index + 1
            while end < length:
                if block[end] == "\\":
                    end += 2
                    continue
                if block[end] == '"':
                    break
                end += 1
            token = block[index + 1 : end]
            if depth == 1 and block[end + 1 :].lstrip().startswith(":"):
                keys.append(token)
            index = end + 1
            continue
        if char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
        index += 1
    return keys


#: ``fixture name -> (contract section, which JSON example in it)``.
CONTRACT_SOURCE: dict[str, tuple[str, int]] = {
    "health": ("GET /v1/health", 0),
    "meta": ("GET /v1/meta", 0),
    "presets": ("GET /v1/presets", 0),
    "sync": ("GET /v1/sync", 0),
    "player_search": ("GET /v1/players/search", 0),
    "player_detail": ("GET /v1/players/{playerId}", 0),
    "player_gamelog": ("GET /v1/players/{playerId}/gamelog", 0),
    # This section documents both team routes: the collection inline, then the detail in a
    # fenced block.
    "teams": ("GET /v1/teams · GET /v1/teams/{teamId}", 0),
    "team_detail": ("GET /v1/teams · GET /v1/teams/{teamId}", 1),
    "games_scoreboard": ("GET /v1/games", 0),
    "game_box": ("GET /v1/games/{gameId}/box", 0),
    # §3: "Response is the `leaderboard` widget payload (§4) plus `nextCursor`".
    "leaders": ("leaderboard", 0),
    # The first example in the resolve section is the *request*; the second is the response.
    "dashboard_resolve": ("POST /v1/dashboard/resolve", 1),
    "error_not_found": ("7. Errors", 0),
    "error_invalid_config": ("7. Errors", 0),
}

#: Keys a fixture carries that its section's example does not spell out, each because another
#: part of the contract requires it. Nothing else may be extra.
DOCUMENTED_EXTRA_KEYS: dict[str, set[str]] = {
    # §1 "Pagination": every collection response carries `nextCursor`. The search example
    # predates that paragraph and omits it; the client decodes it.
    "player_search": {"nextCursor"},
    # §3 states this one in prose rather than in the example: "the `leaderboard` widget
    # payload (§4) plus `"nextCursor"`".
    "leaders": {"nextCursor"},
    # `GET /v1/teams/{teamId}` takes `?season=&seasonType=`, and every other endpoint that
    # takes a season type echoes it back (see the gamelog example). The team example is
    # abbreviated; `ios/NBAStats/Core/Models.swift` decodes the field.
    "team_detail": {"seasonType"},
}


def contract_key_sets() -> dict[str, set[str]]:
    """``{fixture name: the keys CONTRACT.md documents for it}``."""
    sections = _sections(CONTRACT_PATH.read_text(encoding="utf-8"))
    expected: dict[str, set[str]] = {}
    for name, (heading, block_index) in CONTRACT_SOURCE.items():
        assert heading in sections, f"CONTRACT.md has no section {heading!r}"
        blocks = _json_blocks(sections[heading])
        assert len(blocks) > block_index, (
            f"CONTRACT.md section {heading!r} has {len(blocks)} JSON examples, "
            f"so there is no example {block_index}"
        )
        expected[name] = set(_top_level_keys(blocks[block_index]))
    for kind in catalog.widget_kinds():
        assert kind in sections, f"CONTRACT.md §4 has no section for the {kind!r} widget"
        blocks = _json_blocks(sections[kind])
        assert blocks, f"CONTRACT.md §4 {kind!r} has no JSON example"
        expected[f"widget_{kind}"] = set(_top_level_keys(blocks[0]))
    return expected


# --------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def committed() -> dict[str, str]:
    """Every committed *exporter* fixture, as raw text keyed by fixture name.

    Excludes ``PARITY_FIXTURE_NAMES`` — see the module-level comment by that name.
    """
    assert FIXTURES_DIR.is_dir(), (
        f"{FIXTURES_DIR} does not exist; run "
        "`python3 -m nbastats.fixtures_export --out contracts/fixtures`"
    )
    return {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted(FIXTURES_DIR.glob("*.json"))
        if path.stem not in PARITY_FIXTURE_NAMES
    }


@pytest.fixture(scope="module")
def regenerated(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    """A complete fresh export, written to a temporary directory."""
    out_dir = tmp_path_factory.mktemp("fixtures-regenerated")
    fixtures_export.export_fixtures(out_dir)
    yield {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted(out_dir.glob("*.json"))
    }


def _metric_values(node: Any) -> Iterator[dict[str, Any]]:
    """Every ``MetricValue``-shaped object anywhere in a document."""
    if isinstance(node, dict):
        if {"metric", "displayValue", "availability"} <= set(node):
            yield node
        for value in node.values():
            yield from _metric_values(value)
    elif isinstance(node, list):
        for item in node:
            yield from _metric_values(item)


def _metric_maps(node: Any) -> Iterator[dict[str, Any]]:
    """Every ``values`` map (``{"pts": 32, "ts_pct": 0.641}``) anywhere in a document."""
    if isinstance(node, dict):
        values = node.get("values")
        if isinstance(values, dict):
            yield values
        for value in node.values():
            yield from _metric_maps(value)
    elif isinstance(node, list):
        for item in node:
            yield from _metric_maps(item)


# --------------------------------------------------------------------------- tests


def test_the_committed_set_is_exactly_what_the_exporter_writes(
    committed: dict[str, str]
) -> None:
    """No stale file, no missing one — a renamed fixture has to be deleted by hand."""
    assert sorted(committed) == sorted(fixtures_export.fixture_names())


def test_every_committed_fixture_is_reproducible(
    committed: dict[str, str], regenerated: dict[str, str]
) -> None:
    """Regenerating must produce the committed bytes, not merely equivalent JSON.

    Byte equality is the point: CI runs the exporter and then ``git diff --exit-code``, so a
    field order or a float that moved is a failure here rather than a silent change in a file
    the iOS tests decode.
    """
    assert sorted(regenerated) == sorted(fixtures_export.fixture_names())
    differing = [name for name in sorted(committed) if committed[name] != regenerated[name]]
    assert not differing, (
        "these fixtures no longer match the service: "
        f"{differing}. Run `python3 -m nbastats.fixtures_export --out contracts/fixtures` "
        "and `scripts/sync_contracts.sh`."
    )


def test_fixture_keys_match_the_contract(committed: dict[str, str]) -> None:
    """Every fixture's top-level keys are the ones ``contracts/CONTRACT.md`` documents."""
    expected = contract_key_sets()
    assert sorted(expected) == sorted(fixtures_export.fixture_names()), (
        "every fixture needs a section of CONTRACT.md to be checked against"
    )
    for name, keys in expected.items():
        document = json.loads(committed[name])
        assert isinstance(document, dict), f"{name}.json is not a JSON object"
        allowed = keys | DOCUMENTED_EXTRA_KEYS.get(name, set())
        assert set(document) == allowed, (
            f"{name}.json keys {sorted(document)} do not match CONTRACT.md's "
            f"{sorted(allowed)}"
        )


def test_every_fixture_is_indented_json_in_declaration_order(
    committed: dict[str, str]
) -> None:
    """Two-space indent, a trailing newline, and *not* sorted by key."""
    for name, text in committed.items():
        assert text.endswith("\n"), f"{name}.json has no trailing newline"
        assert text == fixtures_export.dumps(json.loads(text)), (
            f"{name}.json is not in the canonical on-disk form"
        )
    # The contract documents `syncVersion` before `dataThrough`; sorting would swap them.
    assert list(json.loads(committed["health"])) == [
        "status",
        "version",
        "syncVersion",
        "dataThrough",
        "databaseReady",
        "seededDemoData",
        "authReady",
        "authWarnings",
    ]


def test_the_fixtures_never_serve_a_zero_for_a_stat_that_did_not_exist(
    committed: dict[str, str]
) -> None:
    """``contracts/CONTRACT.md`` §6: an era-unavailable stat is ``null``, never ``0``."""
    for name, text in committed.items():
        for value in _metric_values(json.loads(text)):
            if value["availability"] == "unavailable":
                assert value["value"] is None, f"{name}.json: {value['metric']} is not null"
                assert value["displayValue"] == "—", (
                    f"{name}.json: {value['metric']} should display an em dash"
                )


def test_the_fixtures_carry_percentages_as_fractions(committed: dict[str, str]) -> None:
    """§2: a percentage crosses the wire in ``[0, 1]``, never as 0-100."""
    percent_metrics = {
        descriptor["key"]
        for descriptor in catalog.all_metrics()
        if str(descriptor["format"]).startswith("percent")
    }
    for name, text in committed.items():
        if name == "meta":  # the metric catalog itself, not measurements
            continue
        document = json.loads(text)
        for entry in _metric_values(document):
            if entry["metric"] in percent_metrics and entry["value"] is not None:
                assert 0.0 <= entry["value"] <= 1.0, (
                    f"{name}.json: {entry['metric']} = {entry['value']} is not a fraction"
                )
        for values in _metric_maps(document):
            for key, value in values.items():
                if key in percent_metrics and value is not None:
                    assert 0.0 <= value <= 1.0, (
                        f"{name}.json: {key} = {value} is not a fraction"
                    )


def test_the_resolve_fixture_covers_every_widget_kind_and_every_availability(
    committed: dict[str, str]
) -> None:
    """The resolve fixture is what the client's decoder is exercised against.

    It has to carry one result per catalog kind, and — because §6's badges are the easiest
    thing to get wrong on a device — at least one result of every availability the contract
    defines other than the uninteresting ``"full"``.
    """
    resolved = json.loads(committed["dashboard_resolve"])
    results = resolved["results"]
    kinds = [result["kind"] for result in results]
    assert set(kinds) == set(catalog.widget_kinds())
    assert kinds[: len(catalog.widget_kinds())] == catalog.widget_kinds(), (
        "the first block of results must be one tile per kind, in catalog order — that is "
        "where the widget_<kind>.json fixtures are cut from"
    )

    # Every tile resolves, except the one the export *declares* should not. Checked per widget
    # id rather than as a set of statuses, so an accidental failure anywhere else still fails
    # this test — and so the declared failure is pinned to the shape §3 documents, which is the
    # only fixture coverage the client's payload / error / decodeError branch gets.
    expected_failures = {
        widget_id for widget_id, _, _ in fixtures_export.ERROR_WIDGET_CONFIGS
    }
    assert expected_failures, "the resolve fixture must carry a declared per-widget failure"
    seen_failures: set[str] = set()
    statuses = set()
    for result in results:
        if result["widgetId"] in expected_failures:
            seen_failures.add(result["widgetId"])
            assert result["status"] == "error", result
            assert result["payload"] is None, "an error result must not carry a payload"
            error = result["error"]
            assert error and error["code"] == "metric_unavailable", error
            assert error["message"], "an error body must say something a reader can act on"
            assert "recoverable" in error and "field" in error
            continue
        statuses.add(result["status"])
        assert result["status"] in {"ok", "partial"}, (
            f"{result['widgetId']} failed to resolve: {result['status']}"
        )
    assert seen_failures == expected_failures
    assert "partial" in statuses, "no widget in the fixture exercises a partial result"

    availabilities = {result["availability"] for result in results}
    for expected in ("full", "estimated", "partial", "unavailable"):
        assert expected in availabilities, (
            f"no widget in the resolve fixture comes back {expected!r}"
        )

    for result in results:
        if result["status"] == "partial":
            assert result["notes"], f"{result['widgetId']} is partial with nothing to say"


def test_each_widget_fixture_is_the_payload_of_its_result(committed: dict[str, str]) -> None:
    """``widget_<kind>.json`` is literally the first payload of that kind in the resolve."""
    resolved = json.loads(committed["dashboard_resolve"])
    first: dict[str, Any] = {}
    for result in resolved["results"]:
        first.setdefault(result["kind"], result["payload"])
    for kind in catalog.widget_kinds():
        assert json.loads(committed[f"widget_{kind}"]) == first[kind]


#: ``contracts/CONTRACT.md`` §2, key for key. A payload that embeds one of these embeds
#: *exactly* these keys: the Swift types decode by key name, so an extra one is a silent
#: omission on a device and a missing one is a decoding failure.
SHARED_OBJECTS: dict[str, tuple[frozenset[str], frozenset[str], frozenset[str]]] = {
    # name: (keys that identify the shape, the full key set, the flattened extensions §3/§4
    #        explicitly documents with a "...Ref" spread)
    "PlayerRef": (
        frozenset({"playerId", "isActive"}),
        frozenset(
            {
                "playerId",
                "name",
                "firstName",
                "lastName",
                "teamId",
                "teamAbbr",
                "position",
                "jersey",
                "headshotUrl",
                "isActive",
            }
        ),
        # §3 search results add the ranking fields; §3 roster rows add that season's values.
        frozenset({"fromYear", "toYear", "matchScore", "values"}),
    ),
    "TeamRef": (
        frozenset({"teamId", "abbr"}),
        frozenset({"teamId", "abbr", "name", "city", "nickname", "conference", "division"}),
        frozenset(),
    ),
    "MetricValue": (
        # ``isEstimated`` is in the marker because it is the one key no other §4 object has.
        # A ``next_game_projection`` line also carries ``metric`` and ``displayValue`` beside
        # its own mean and interval, and it is a different documented shape, not a
        # ``MetricValue`` missing half its keys — the marker has to tell them apart.
        frozenset({"metric", "displayValue", "isEstimated"}),
        frozenset(
            {
                "metric",
                "value",
                "displayValue",
                "rank",
                "percentile",
                "leagueAverage",
                "delta",
                "isEstimated",
                "availability",
            }
        ),
        frozenset(),
    ),
    "GameRef": (
        frozenset({"gameId", "home"}),
        frozenset(
            {
                "gameId",
                "date",
                "season",
                "seasonType",
                "home",
                "away",
                "homePts",
                "awayPts",
                "status",
                "period",
                "clock",
                "finalizedAt",
            }
        ),
        # §4 scoreboard spreads a GameRef and adds the night's leaders.
        frozenset({"topPerformers"}),
    ),
}


def _objects(node: Any) -> Iterator[dict[str, Any]]:
    """Every JSON object anywhere in a document."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _objects(value)
    elif isinstance(node, list):
        for item in node:
            yield from _objects(item)


def test_every_shared_object_matches_its_contract_shape(committed: dict[str, str]) -> None:
    """§2's four shared objects have the same keys everywhere they appear.

    The top-level check above cannot see these: a ``PlayerRef`` nested eight levels down in a
    leaderboard row is the shape most likely to drift and the one a client notices last.
    """
    checked = 0
    for name, text in committed.items():
        if name == "meta":  # the catalogs, not measurements
            continue
        for obj in _objects(json.loads(text)):
            keys = set(obj)
            for shape, (marker, expected, extensions) in SHARED_OBJECTS.items():
                if not marker <= keys:
                    continue
                checked += 1
                assert expected <= keys, (
                    f"{name}.json: a {shape} is missing {sorted(expected - keys)}"
                )
                assert keys - expected <= extensions, (
                    f"{name}.json: a {shape} carries undocumented keys "
                    f"{sorted(keys - expected - extensions)}"
                )
    assert checked > 100, f"only {checked} shared objects found; the walk is not working"


def test_embedded_metric_descriptors_are_the_catalog_entry(committed: dict[str, str]) -> None:
    """§2: a ``MetricDescriptor`` is *exactly* one entry of ``contracts/metrics.json``."""
    index = {metric["key"]: metric for metric in catalog.metrics_document()["metrics"]}
    marker = {"key", "name", "shortName", "category", "format", "availability"}
    checked = 0
    for name, text in committed.items():
        if name == "meta":  # this one *is* the catalog
            continue
        for obj in _objects(json.loads(text)):
            if not marker <= set(obj):
                continue
            checked += 1
            expected = index.get(obj["key"])
            assert expected is not None, f"{name}.json describes unknown metric {obj['key']!r}"
            assert obj == expected, (
                f"{name}.json: the {obj['key']!r} descriptor is not the catalog's entry"
            )
    assert checked, "no metric descriptors were found in the fixtures"


def test_meta_echoes_the_catalogs_verbatim(committed: dict[str, str]) -> None:
    """§3: ``/v1/meta`` passes the contract documents through untouched."""
    meta = json.loads(committed["meta"])
    assert meta["metrics"] == catalog.metrics_document()
    assert meta["widgets"] == catalog.widgets_document()
    presets = json.loads(committed["presets"])
    assert presets["presets"] == catalog.presets_document()["presets"]
    assert [token["token"] for token in presets["subjectTokens"]] == [
        token["token"] for token in catalog.presets_document()["subjectTokens"]
    ]


def test_the_error_fixtures_use_the_contract_envelope(committed: dict[str, str]) -> None:
    """§7: both error fixtures carry a code from the table and the request id."""
    from nbastats.api.errors import ERROR_STATUS

    for name, code in (
        ("error_not_found", "player_not_found"),
        ("error_invalid_config", "invalid_config"),
    ):
        body = json.loads(committed[name])["error"]
        assert body["code"] == code
        assert code in ERROR_STATUS
        assert body["message"]
        assert body["requestId"] == fixtures_export.FIXTURE_REQUEST_ID
        assert set(body) == {"code", "message", "recoverable", "field", "requestId"}
    assert json.loads(committed["error_invalid_config"])["error"]["field"] == "seasonType"
