"""League-relative ranking: dense ranks, percentiles and distribution summaries.

Every ``MetricValue`` in ``contracts/CONTRACT.md`` can carry a ``rank`` and a
``percentile``; this module produces both from a set of ``(subject, value)``
pairs. It is stdlib-only and holds no opinion about *which* metric it is ranking
- direction comes in as ``higher_is_better``, which the caller reads from
``contracts/metrics.json`` (``tov`` and ``def_rtg`` are better when lower).

Conventions
-----------
* **Dense ranks.** Ties share a rank and the next distinct value takes the very
  next integer: values 30, 30, 28 rank 1, 1, 2. Rank 1 is always the best value
  in the requested direction.
* **Percentiles are fractions in [0, 1]**, like every other rate in the system,
  and use the mid-rank definition::

      percentile = (subjects strictly worse + 0.5 * subjects tied, self included) / n

  which puts a lone subject, and a field where everybody is equal, at exactly
  0.5 rather than at 0 or 1. Better values always get a percentile at least as
  high as worse ones.
* **Missing values are excluded, not zeroed.** A subject whose value is ``None``
  (era-unavailable, DNP, or not yet ingested) is left out of the ranking
  entirely: it does not appear in the result, and it does not drag the field's
  percentiles down. Ranking a player's missing 1962 steal rate as "worst in the
  league" would be exactly the lie the contract forbids.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from typing import Any, Hashable, Iterable, Mapping, NamedTuple, Optional, Sequence, TypeVar, Union

__all__ = ["Summary", "rank_and_percentile", "percentile_of", "summarize"]

SubjectT = TypeVar("SubjectT", bound=Hashable)

#: Either a mapping of subject -> value, or an iterable of ``(subject, value)``.
ValueInput = Union[Mapping[Any, Optional[float]], Iterable[tuple[Any, Optional[float]]]]


class Summary(NamedTuple):
    """Distribution summary of one metric across a field of subjects.

    Unpacks as ``mean, stddev, p10, p25, p50, p75, p90, count``. Every field
    except ``count`` is ``None`` when there is nothing to summarize. ``stddev``
    is the *population* standard deviation, so a single value gives ``0.0``
    rather than an error.
    """

    mean: Optional[float]
    stddev: Optional[float]
    p10: Optional[float]
    p25: Optional[float]
    p50: Optional[float]
    p75: Optional[float]
    p90: Optional[float]
    count: int


def _finite(value: Any) -> Optional[float]:
    """Coerce to a finite float, or ``None``. ``bool`` is not a stat value."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _pairs(values: ValueInput) -> list[tuple[Any, float]]:
    """Normalize the input to a list of ``(subject, finite value)`` pairs."""
    items: Iterable[tuple[Any, Optional[float]]]
    items = values.items() if isinstance(values, Mapping) else values
    out: list[tuple[Any, float]] = []
    for subject, raw in items:
        number = _finite(raw)
        if number is not None:
            out.append((subject, number))
    return out


def _clean(values: Iterable[Optional[float]]) -> list[float]:
    """Drop missing entries and return the remaining values sorted ascending."""
    return sorted(v for v in (_finite(value) for value in values) if v is not None)


def _mid_rank_percentile(
    value: float, ascending: list[float], higher_is_better: bool
) -> float:
    """Share of the field this value beats, counting ties as half.

    ``ascending`` must be sorted; ties are located with a pair of bisections, so
    this is O(log n) per subject.
    """
    total = len(ascending)
    if total == 0:
        return 0.5
    left = bisect_left(ascending, value)
    right = bisect_right(ascending, value)
    equal = right - left
    worse = left if higher_is_better else total - right
    return min(1.0, max(0.0, (worse + 0.5 * equal) / total))


def rank_and_percentile(
    values: ValueInput, higher_is_better: bool = True
) -> dict[Any, tuple[int, float]]:
    """Rank a field of subjects and place each one in the distribution.

    Returns ``{subject_id: (dense_rank, percentile)}``. Rank 1 is the best value
    in the requested direction, ties share a rank, and percentiles are fractions
    in ``[0, 1]`` using the mid-rank definition documented at module level.

    Subjects whose value is missing or non-finite are omitted from the result.
    An empty field returns an empty dict. If the same subject appears twice, the
    last occurrence wins.
    """
    pairs = _pairs(values)
    if not pairs:
        return {}

    ascending = sorted(value for _, value in pairs)
    ordered_distinct = sorted(set(ascending), reverse=higher_is_better)
    rank_by_value = {value: index + 1 for index, value in enumerate(ordered_distinct)}

    out: dict[Any, tuple[int, float]] = {}
    for subject, value in pairs:
        out[subject] = (
            rank_by_value[value],
            _mid_rank_percentile(value, ascending, higher_is_better),
        )
    return out


def percentile_of(
    value: Optional[float],
    sorted_values: Sequence[Optional[float]],
    higher_is_better: bool = True,
) -> Optional[float]:
    """Where one value falls in a reference distribution, as a fraction in ``[0, 1]``.

    ``sorted_values`` is expected ascending; it is filtered of missing entries
    and re-sorted defensively, so an unsorted or ``None``-laden list still gives
    the right answer.

    Returns ``None`` - rather than a made-up 0.0 - when ``value`` itself is
    missing or the reference distribution is empty. A value that is not present
    in the reference set is placed by comparison alone (no tie credit), which is
    what the league-average comparison in ``MetricValue.delta`` needs.
    """
    subject = _finite(value)
    if subject is None:
        return None
    ascending = _clean(sorted_values)
    if not ascending:
        return None
    return _mid_rank_percentile(subject, ascending, higher_is_better)


def _quantile(ascending: list[float], q: float) -> float:
    """Linear-interpolated quantile of a non-empty ascending list (numpy's default).

    Position ``q * (n - 1)`` in the sorted values, interpolating between the two
    neighbouring order statistics.
    """
    if len(ascending) == 1:
        return ascending[0]
    position = q * (len(ascending) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ascending[int(position)]
    fraction = position - lower
    return ascending[lower] + fraction * (ascending[upper] - ascending[lower])


def summarize(values: Iterable[Optional[float]]) -> Summary:
    """Summarize a field of values: mean, population stddev, deciles and count.

    Missing and non-finite entries are dropped; ``count`` reports how many real
    values were summarized, so the caller can decide whether a league average
    built on four rows is worth showing. An empty input gives every statistic as
    ``None`` with ``count = 0``.
    """
    ascending = _clean(values)
    count = len(ascending)
    if count == 0:
        return Summary(None, None, None, None, None, None, None, 0)

    mean = sum(ascending) / count
    variance = sum((value - mean) ** 2 for value in ascending) / count
    return Summary(
        mean=mean,
        stddev=math.sqrt(variance),
        p10=_quantile(ascending, 0.10),
        p25=_quantile(ascending, 0.25),
        p50=_quantile(ascending, 0.50),
        p75=_quantile(ascending, 0.75),
        p90=_quantile(ascending, 0.90),
        count=count,
    )
