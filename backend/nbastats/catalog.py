"""Loader and typed accessors for the shared contract catalogs.

``contracts/metrics.json``, ``contracts/widgets.json`` and ``contracts/presets.json`` are the
binding contract between the iOS client and this service. This module owns every rule that
reads them: formatting, widget-config validation and era availability.

It is intentionally dependency-free (stdlib only, no fastapi and no sqlalchemy) so the ingest
worker, the API layer and the seeder can all import it.

Era availability, in one place
------------------------------
``availability.seasonFrom``      first season the metric exists at season level
``availability.perGameFrom``     first season it exists per game
``availability.seasonLevelOnly`` the metric has no per-game meaning at all (PER, WS, BPM…)
``availability.estimatedBefore`` before this season the value is derived from box-score
                                 formulas rather than possession data

``metric_availability()`` folds those into the four values the client renders:
``full`` / ``estimated`` / ``partial`` / ``unavailable``. ``partial`` is what you get when a
span of seasons straddles a boundary (pass ``"career"`` or a list of seasons).
"""
from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence, TypedDict

__all__ = [
    "EM_DASH",
    "Availability",
    "ConfigError",
    "MetricDescriptor",
    "WidgetDescriptor",
    "ConfigFieldSpec",
    "PresetLayout",
    "UnknownMetricError",
    "UnknownWidgetError",
    "UnknownPresetError",
    "contracts_dir",
    "metrics_document",
    "widgets_document",
    "presets_document",
    "metric",
    "all_metrics",
    "metric_keys",
    "metrics_for_scope",
    "has_metric",
    "format_metric",
    "format_value",
    "widget",
    "all_widgets",
    "widget_kinds",
    "widget_config_defaults",
    "validate_widget_config",
    "presets",
    "preset",
    "preset_keys",
    "subject_tokens",
    "era_boundaries",
    "coverage",
    "metric_availability",
    "combine_availability",
    "season_sort_key",
    "is_season_string",
    "reload_catalogs",
]

#: What the client renders for a value that does not exist in the subject's era.
EM_DASH = "—"

Availability = Literal["full", "estimated", "partial", "unavailable"]
Scope = Literal["player", "team"]
Granularity = Literal["season", "game", "per_game"]

_SEASON_RE = re.compile(r"^(\d{4})-(\d{2})$")
_CAREER_TOKENS = {"career", "all_time", "all-time", "alltime"}
_GAME_GRANULARITY = {"game", "per_game", "pergame"}


class AvailabilitySpec(TypedDict):
    seasonFrom: str
    perGameFrom: str
    seasonLevelOnly: bool
    estimatedBefore: str | None


class MetricDescriptor(TypedDict, total=False):
    """One entry of ``contracts/metrics.json#/metrics`` — serialised to the client verbatim."""

    key: str
    name: str
    shortName: str
    category: str
    format: str
    higherIsBetter: bool
    scope: list[str]
    availability: AvailabilitySpec
    domain: dict[str, float] | None
    glossary: str


class ConfigFieldSpec(TypedDict, total=False):
    key: str
    type: str
    label: str
    required: bool
    default: Any
    options: list[str]
    metricScope: str
    dependsOn: str
    minItems: int
    maxItems: int
    min: float
    max: float
    help: str


class WidgetDescriptor(TypedDict, total=False):
    kind: str
    name: str
    summary: str
    icon: str
    sizes: list[str]
    defaultSize: str
    minRefreshSeconds: int
    config: list[ConfigFieldSpec]
    availableFrom: str | None


class PresetLayout(TypedDict, total=False):
    id: str
    presetKey: str
    name: str
    icon: str
    tagline: str
    accent: str
    isPreset: bool
    schemaVersion: int
    widgets: list[dict[str, Any]]


class UnknownMetricError(KeyError):
    """Raised for a metric key that is not in the catalog."""


class UnknownWidgetError(KeyError):
    """Raised for a widget kind that is not in the catalog."""


class UnknownPresetError(KeyError):
    """Raised for a preset key that is not in the catalog."""


@dataclass(frozen=True, slots=True)
class ConfigError:
    """One widget-config validation failure. ``field`` is the offending config key."""

    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


# --------------------------------------------------------------------------- loading


def contracts_dir() -> Path:
    """Directory holding ``metrics.json`` / ``widgets.json`` / ``presets.json``.

    ``HARDWOOD_CONTRACTS_DIR`` wins; otherwise the repository copy two levels above this
    file, then a ``contracts`` directory beside the working directory.
    """
    override = os.environ.get("HARDWOOD_CONTRACTS_DIR")
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())
    here = Path(__file__).resolve()
    candidates.append(here.parents[2] / "contracts")  # <repo>/contracts
    candidates.append(here.parents[1] / "contracts")  # <repo>/backend/contracts
    candidates.append(Path.cwd() / "contracts")
    for candidate in candidates:
        if (candidate / "metrics.json").is_file():
            return candidate
    tried = ", ".join(str(c) for c in candidates)
    raise FileNotFoundError(f"Could not locate the contracts directory (tried: {tried})")


@lru_cache(maxsize=8)
def _load(name: str) -> dict[str, Any]:
    path = contracts_dir() / name
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=1)
def _metric_index() -> dict[str, MetricDescriptor]:
    return {m["key"]: m for m in metrics_document()["metrics"]}


@lru_cache(maxsize=1)
def _format_index() -> dict[str, dict[str, Any]]:
    return {f["key"]: f for f in metrics_document()["formats"]}


@lru_cache(maxsize=1)
def _widget_index() -> dict[str, WidgetDescriptor]:
    return {w["kind"]: w for w in widgets_document()["widgets"]}


@lru_cache(maxsize=1)
def _preset_index() -> dict[str, PresetLayout]:
    return {p["presetKey"]: p for p in presets_document()["presets"]}


@lru_cache(maxsize=1)
def _subject_tokens() -> frozenset[str]:
    return frozenset(t["token"] for t in presets_document().get("subjectTokens", []))


def reload_catalogs() -> None:
    """Drop every cached catalog; used by tests and by a hot contracts reload."""
    for cache in (
        _load,
        _metric_index,
        _format_index,
        _widget_index,
        _preset_index,
        _subject_tokens,
    ):
        cache.cache_clear()


def metrics_document() -> dict[str, Any]:
    """The whole of ``contracts/metrics.json`` (what ``/v1/meta`` echoes)."""
    return _load("metrics.json")


def widgets_document() -> dict[str, Any]:
    """The whole of ``contracts/widgets.json``."""
    return _load("widgets.json")


def presets_document() -> dict[str, Any]:
    """The whole of ``contracts/presets.json``."""
    return _load("presets.json")


# --------------------------------------------------------------------------- metrics


def metric(key: str) -> MetricDescriptor:
    """Descriptor for one metric key."""
    try:
        return _metric_index()[key]
    except KeyError as exc:
        raise UnknownMetricError(key) from exc


def has_metric(key: str) -> bool:
    return key in _metric_index()


def all_metrics() -> list[MetricDescriptor]:
    """Every metric descriptor, in catalog order."""
    return list(metrics_document()["metrics"])


def metric_keys() -> list[str]:
    return list(_metric_index())


def metrics_for_scope(scope: Scope | str) -> list[MetricDescriptor]:
    """Metrics that apply to ``"player"`` or ``"team"`` subjects."""
    return [m for m in all_metrics() if scope in m.get("scope", ())]


def era_boundaries() -> list[dict[str, Any]]:
    """The dated era boundaries the client draws on career charts."""
    return list(metrics_document()["eraBoundaries"])


def coverage() -> dict[str, str]:
    """The ``coverage`` block of ``GET /v1/meta``, derived from the era boundaries."""
    return {
        "seasonFrom": "1946-47",
        "advancedFrom": "1996-97",
        "shotChartsFrom": "1996-97",
        "trackingFrom": "2013-14",
        "hustleFrom": "2016-17",
    }


# --------------------------------------------------------------------------- formatting


def format_value(format_key: str, value: float | int | None) -> str:
    """Render ``value`` with one of the catalog's format rules.

    Percent formats multiply by 100 (values are fractions everywhere in the data layer),
    ``plusMinus1`` is signed, and ``None`` renders as an em dash.
    """
    if value is None:
        return EM_DASH
    spec = _format_index().get(format_key)
    if spec is None:
        raise KeyError(f"unknown format {format_key!r}")
    try:
        number = Decimal(str(float(value)))
    except (TypeError, ValueError, ArithmeticError):
        return EM_DASH
    if not number.is_finite():
        return EM_DASH
    number *= Decimal(str(spec.get("multiplier", 1)))
    decimals = int(spec.get("decimals", 1))
    quantum = Decimal(1).scaleb(-decimals)
    number = number.quantize(quantum, rounding=ROUND_HALF_UP)
    signed = bool(spec.get("signed"))
    if number == 0:  # a value that rounds to zero must not render as "-0.0"
        number = abs(number)
    text = f"{number:+.{decimals}f}" if signed else f"{number:.{decimals}f}"
    suffix = spec.get("suffix")
    return f"{text}{suffix}" if suffix else text


def format_metric(key: str, value: float | int | None) -> str:
    """``displayValue`` for a ``MetricValue``: the string the client shows verbatim."""
    return format_value(metric(key)["format"], value)


# --------------------------------------------------------------------------- widgets


def widget(kind: str) -> WidgetDescriptor:
    """Descriptor for one widget kind."""
    try:
        return _widget_index()[kind]
    except KeyError as exc:
        raise UnknownWidgetError(kind) from exc


def all_widgets() -> list[WidgetDescriptor]:
    return list(widgets_document()["widgets"])


def widget_kinds() -> list[str]:
    return list(_widget_index())


def widget_config_defaults(kind: str) -> dict[str, Any]:
    """Default config for a widget kind — a fresh copy the caller may mutate."""
    return {field["key"]: deepcopy(field.get("default")) for field in widget(kind)["config"]}


def subject_tokens() -> frozenset[str]:
    """The ``$``-prefixed subject tokens presets may use in place of an id."""
    return _subject_tokens()


def _is_int(value: Any) -> bool:
    # bool is a subclass of int; a checkbox value is never an id or a count.
    return isinstance(value, int) and not isinstance(value, bool)


def _as_int(value: Any) -> int | None:
    if _is_int(value):
        return int(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _clamp(value: float, field: ConfigFieldSpec) -> float:
    low, high = field.get("min"), field.get("max")
    if low is not None:
        value = max(value, low)
    if high is not None:
        value = min(value, high)
    return value


def _check_subject(value: Any) -> bool:
    return _is_int(value) or (isinstance(value, str) and value in _subject_tokens())


def _metric_ok(key: Any, field: ConfigFieldSpec) -> str | None:
    """Return an error message, or None when ``key`` is a usable metric for this field."""
    if not isinstance(key, str) or not has_metric(key):
        return f"unknown metric {key!r}"
    scope = field.get("metricScope")
    if scope in (None, "any"):
        return None
    if scope not in metric(key).get("scope", ()):
        return f"metric {key!r} is not a {scope} metric"
    return None


def _validate_field(field: ConfigFieldSpec, value: Any) -> tuple[Any, list[str]]:
    """Validate and coerce one config value. Returns ``(cleaned, messages)``."""
    kind = field["type"]
    messages: list[str] = []

    if kind == "enum":
        options = field.get("options", [])
        if value not in options:
            return deepcopy(field.get("default")), [f"{value!r} is not one of {options}"]
        return value, messages

    if kind == "enumList":
        options = field.get("options", [])
        if not isinstance(value, (list, tuple)):
            return deepcopy(field.get("default")) or [], ["expected a list"]
        cleaned = []
        for item in value:
            if item in options:
                cleaned.append(item)
            else:
                messages.append(f"{item!r} is not one of {options}")
        return cleaned, messages

    if kind == "metric":
        problem = _metric_ok(value, field)
        if problem:
            return deepcopy(field.get("default")), [problem]
        return value, messages

    if kind == "metricList":
        if not isinstance(value, (list, tuple)):
            return deepcopy(field.get("default")) or [], ["expected a list"]
        cleaned = []
        for item in value:
            problem = _metric_ok(item, field)
            if problem:
                messages.append(problem)
            else:
                cleaned.append(item)
        cleaned, extra = _apply_item_bounds(cleaned, field)
        return cleaned, messages + extra

    if kind in ("player", "team", "subject"):
        if value is None and not field.get("required"):
            # An optional subject field written out as an explicit ``null`` means the same
            # thing as one left out entirely: nothing is chosen. ``next_game_projection``'s
            # ``opponentTeamId`` is exactly that — "leave empty to use the player's actual
            # next scheduled opponent" — and ``contracts/presets.json`` ships it as ``null``,
            # so rejecting it would make a preset in the contract invalid against the
            # catalog in the same contract.
            return None, messages
        if not _check_subject(value):
            return deepcopy(field.get("default")), [
                f"expected an id or one of {sorted(_subject_tokens())}, got {value!r}"
            ]
        return _as_int(value) if _as_int(value) is not None else value, messages

    if kind in ("playerList", "teamList", "subjectList"):
        if not isinstance(value, (list, tuple)):
            return deepcopy(field.get("default")) or [], ["expected a list"]
        cleaned = []
        for item in value:
            if _check_subject(item):
                coerced = _as_int(item)
                cleaned.append(coerced if coerced is not None else item)
            else:
                messages.append(f"{item!r} is not an id or a subject token")
        cleaned, extra = _apply_item_bounds(cleaned, field)
        return cleaned, messages + extra

    if kind == "int":
        coerced = _as_int(value)
        if coerced is None:
            return deepcopy(field.get("default")), [f"expected an integer, got {value!r}"]
        return int(_clamp(coerced, field)), messages

    if kind == "double":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return deepcopy(field.get("default")), [f"expected a number, got {value!r}"]
        return float(_clamp(float(value), field)), messages

    if kind == "bool":
        if not isinstance(value, bool):
            return deepcopy(field.get("default")), [f"expected a boolean, got {value!r}"]
        return value, messages

    if kind == "season":
        if isinstance(value, str) and (value == "latest" or is_season_string(value)):
            return value, messages
        return deepcopy(field.get("default")), [
            f"expected a season such as '2025-26' or 'latest', got {value!r}"
        ]

    if kind == "date":
        if isinstance(value, str) and (value == "latest" or _is_iso_date(value)):
            return value, messages
        return deepcopy(field.get("default")), [
            f"expected an ISO date or 'latest', got {value!r}"
        ]

    # A field type the catalog grew after this build: keep the value, flag nothing.
    return value, messages


def _apply_item_bounds(items: list[Any], field: ConfigFieldSpec) -> tuple[list[Any], list[str]]:
    messages: list[str] = []
    max_items = field.get("maxItems")
    min_items = field.get("minItems")
    if max_items is not None and len(items) > max_items:
        messages.append(f"{len(items)} items exceeds the maximum of {max_items}")
        items = items[:max_items]
    if min_items is not None and len(items) < min_items:
        messages.append(f"{len(items)} items is below the minimum of {min_items}")
    return items, messages


def _is_iso_date(value: str) -> bool:
    from datetime import date as _date

    try:
        _date.fromisoformat(value)
    except ValueError:
        return False
    return True


def validate_widget_config(
    kind: str, config: Mapping[str, Any] | None
) -> tuple[dict[str, Any], list[ConfigError]]:
    """Clean one widget's config against the catalog's field schema.

    Applies the contract's rules: unknown keys are dropped, missing keys take the catalog
    default, enums must be members, ints/doubles are typed then clamped to ``min``/``max``,
    lists honour ``minItems``/``maxItems``, and subject fields accept an integer id or one of
    the ``$`` tokens from ``presets.json``.

    The returned config is always usable — a value that failed validation falls back to the
    catalog default — so a caller may either reject the widget on ``errors`` (the ``/v1``
    ``invalid_config`` path) or render with the defaults.
    """
    spec = widget(kind)
    supplied: Mapping[str, Any] = config or {}
    cleaned: dict[str, Any] = {}
    errors: list[ConfigError] = []

    for field in spec["config"]:
        key = field["key"]
        if key not in supplied:
            default = deepcopy(field.get("default"))
            if default is None and field.get("required"):
                errors.append(ConfigError(key, "is required"))
            cleaned[key] = default
            continue
        value, messages = _validate_field(field, supplied[key])
        cleaned[key] = value
        errors.extend(ConfigError(key, message) for message in messages)

    return cleaned, errors


# --------------------------------------------------------------------------- presets


def presets() -> list[PresetLayout]:
    """Every preset dashboard, in catalog order."""
    return list(presets_document()["presets"])


def preset(key: str) -> PresetLayout:
    """One preset dashboard by its ``presetKey``."""
    try:
        return _preset_index()[key]
    except KeyError as exc:
        raise UnknownPresetError(key) from exc


def preset_keys() -> list[str]:
    return list(_preset_index())


# --------------------------------------------------------------------------- era rules


def is_season_string(value: Any) -> bool:
    """True for an NBA-style season string such as ``"1999-00"``."""
    return isinstance(value, str) and _SEASON_RE.match(value) is not None


def season_sort_key(season: str) -> int:
    """Sortable key for a season string: ``"2025-26"`` → ``2025``."""
    match = _SEASON_RE.match(season or "")
    if match is None:
        raise ValueError(f"{season!r} is not a season string such as '2025-26'")
    return int(match.group(1))


def _availability_for_season(
    spec: AvailabilitySpec, season: str, granularity: Granularity | str
) -> Availability:
    year = season_sort_key(season)
    per_game = str(granularity).lower() in _GAME_GRANULARITY
    if per_game:
        if spec.get("seasonLevelOnly"):
            return "unavailable"
        first = spec["perGameFrom"]
    else:
        first = spec["seasonFrom"]
    if year < season_sort_key(first):
        return "unavailable"
    estimated_before = spec.get("estimatedBefore")
    if estimated_before and year < season_sort_key(estimated_before):
        return "estimated"
    return "full"


def combine_availability(values: Iterable[Availability]) -> Availability:
    """Fold per-season availabilities into the one the client should render.

    Any mixture — some seasons measured, some missing — is ``partial``; that is the caret
    and footnote treatment in the contract.
    """
    seen = set(values)
    if not seen:
        return "unavailable"
    if len(seen) == 1:
        return seen.pop()
    if seen == {"full", "estimated"}:
        return "estimated"
    return "partial"


def metric_availability(
    metric_key: str,
    season: str | Sequence[str] | None = None,
    granularity: Granularity | str = "season",
) -> Availability:
    """Era availability of a metric.

    ``season`` may be a season string, a sequence of season strings, or ``None`` /
    ``"career"`` / ``"all_time"`` to ask about the whole of league history — a span that
    straddles a boundary comes back ``"partial"``.

    ``granularity`` is ``"season"`` for season aggregates or ``"game"`` (``"per_game"``) for
    a single game, which is the distinction between ``seasonFrom`` and ``perGameFrom``.
    """
    spec = metric(metric_key)["availability"]

    if season is None or (isinstance(season, str) and season.lower() in _CAREER_TOKENS):
        # Compare against the whole of league history: 1946-47 to the present.
        history = [b["season"] for b in era_boundaries()]
        history.append("2025-26")
        return combine_availability(
            _availability_for_season(spec, s, granularity) for s in history
        )

    if isinstance(season, str):
        return _availability_for_season(spec, season, granularity)

    return combine_availability(
        _availability_for_season(spec, s, granularity) for s in season
    )
