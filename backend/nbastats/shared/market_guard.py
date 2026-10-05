"""The guard that keeps gambling machinery out of the payloads, as code a test can call.

Hardwood computes projected scores, margins and winners, and it reports how those compared
with what happened. It does not compute, accept or return anything that exists only to be
laid against a price: no line, no probability of beating a number, no edge, no lean, no pick.
That is a product decision, and a decision that lives only in a design document erodes the
first time someone adds a convenient field. So the decision is enforced structurally, and this
module is the vocabulary of the enforcement.

Two questions, answered by pure functions
-----------------------------------------
**Does a payload contain a forbidden word?** :func:`scan_keys` walks any JSON-shaped value and
reports every object key that, once split into words, contains a word from
:data:`FORBIDDEN_KEY_WORDS`. Keys are split on case changes, digits and punctuation, so
``overtimePeriods`` is ``overtime`` and ``periods`` and is fine, while ``projectedLine`` is
``projected`` and ``line`` and is not. Matching is on whole words, never substrings: a key
called ``coverage`` or ``headline`` is not a violation, which is what lets the list be strict
without ever needing an exception.

**Does a route accept a number it should not?** :func:`parameter_violations` checks the names
of a route's query, path and body fields against :data:`ALLOWED_PARAMETERS`. A route whose
parameters are all on that list cannot take an external number to compare a projection with,
because no name on the list could carry one.

Where the lists come from
-------------------------
Both lists are defined once, in ``contracts/leagues.json``, and reach this module through the
generated constants in :mod:`nbastats.shared._generated_leagues`, so that this package stays
file-free and clients can read the same lists at runtime. They are re-exported here as
frozensets. Nothing in this module reads a file or imports anything outside the standard
library.

What this module does not do
----------------------------
It is not a runtime filter. It never removes or rewrites a key; a payload is clean or a test
fails. The tests that call it (``tests/test_no_market_machinery.py``) walk fixtures, real
responses from a seeded app, the OpenAPI document and the importers' read maps. Model-setting
keys, the importer's ignored headers and the half-point-rounding scan are checked there,
against the same vocabulary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final, Iterable, Iterator, Mapping, Sequence

from ._generated_leagues import ALLOWED_PARAMETERS as _ALLOWED
from ._generated_leagues import FORBIDDEN_PAYLOAD_KEY_WORDS as _FORBIDDEN

__all__ = [
    "FORBIDDEN_KEY_WORDS",
    "ALLOWED_PARAMETERS",
    "KeyViolation",
    "split_words",
    "forbidden_words_in",
    "scan_keys",
    "assert_clean_keys",
    "parameter_violations",
    "is_allowed_parameter",
]

#: Words no object key of a new payload may contain, as a whole word.
FORBIDDEN_KEY_WORDS: Final[frozenset[str]] = frozenset(_FORBIDDEN)

#: Names a new route may declare as a query, path or body field.
ALLOWED_PARAMETERS: Final[frozenset[str]] = frozenset(_ALLOWED)

# A word is a run of capitals not followed by a lower-case letter (an acronym), a capital
# followed by lower-case letters, a run of lower-case letters, or a run of digits.
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def split_words(key: str) -> tuple[str, ...]:
    """The lower-case words of a key: ``overtimePeriods`` gives ``("overtime", "periods")``.

    Splits on case changes (``HTTPStatus`` gives ``http``, ``status``), on letter-digit
    boundaries (``line2`` gives ``line``, ``2``) and on any non-alphanumeric character
    (``snake_case`` and ``kebab-case`` work).
    """
    return tuple(match.group(0).lower() for match in _WORD.finditer(key))


def forbidden_words_in(key: str) -> tuple[str, ...]:
    """The forbidden words ``key`` contains, in order, each at most once."""
    found: list[str] = []
    for word in split_words(key):
        if word in FORBIDDEN_KEY_WORDS and word not in found:
            found.append(word)
    return tuple(found)


@dataclass(frozen=True)
class KeyViolation:
    """One forbidden key: where it is and which words made it so."""

    path: str
    key: str
    words: tuple[str, ...]

    def __str__(self) -> str:
        return f"{self.path}: key {self.key!r} contains forbidden word(s) {', '.join(self.words)}"


def _walk(value: Any, path: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            where = f"{path}.{key}"
            yield where, str(key)
            yield from _walk(child, where)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def scan_keys(payload: Any, *, root: str = "$") -> list[KeyViolation]:
    """Every object key anywhere in ``payload`` that contains a forbidden word.

    Only keys are examined, never values: a headline whose text happens to include a
    forbidden word is a string, not a field the product defines. Lists and nested objects are
    walked to any depth. An empty list means the payload is clean.
    """
    violations: list[KeyViolation] = []
    for where, key in _walk(payload, root):
        words = forbidden_words_in(key)
        if words:
            violations.append(KeyViolation(path=where, key=key, words=words))
    return violations


def assert_clean_keys(payload: Any, *, root: str = "$") -> None:
    """Raise ``AssertionError`` listing every violation; a no-op for a clean payload."""
    violations = scan_keys(payload, root=root)
    if violations:
        raise AssertionError("forbidden payload keys:\n  " + "\n  ".join(map(str, violations)))


def is_allowed_parameter(name: str) -> bool:
    """True when ``name`` is on the allowed-parameter list (exact, case-sensitive)."""
    return name in ALLOWED_PARAMETERS


def parameter_violations(names: Iterable[str]) -> list[str]:
    """The names in ``names`` that a new route may not declare, sorted and de-duplicated."""
    return sorted({name for name in names if name not in ALLOWED_PARAMETERS})


def _self_check(words: Sequence[str]) -> None:  # pragma: no cover - import-time sanity
    for word in words:
        if split_words(word) != (word,):
            raise RuntimeError(f"forbidden word {word!r} is not a single lower-case word")


_self_check(tuple(FORBIDDEN_KEY_WORDS))
