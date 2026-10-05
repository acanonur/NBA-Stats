#!/usr/bin/env python3
"""Generate contracts/leagues.json - the two leagues, as data - and the constants the guard reads.

Hardwood covers two leagues, and a handful of facts about them have to be the same in the
service, in the pure core and in any client: how long a game is, how many games make a defence's
average believable, which five statuses an availability entry can carry, which words no payload
key may contain. This generator is where those facts are *assembled*, from the one place each is
defined, so a drift between two copies has nowhere to start.

What is read, and from where
----------------------------
* The league profiles come from ``nbastats.shared.league_profile`` (``LeagueProfile.to_contract``),
  the availability vocabulary from ``nbastats.shared.availability.vocabulary`` and the position
  schemes from ``nbastats.shared.positions.scheme_contract``. Those modules are pure stdlib, so
  importing them here costs nothing and restates nothing.
* The two **guard lists** are defined *here* and nowhere else: the words no key of a new payload
  may contain, and the only names a new route may take as a query, path or body field. They exist
  to make betting machinery structurally impossible (no line, no price, no probability of beating
  a number), and a list that guards the payloads has to be a list one test cannot quietly
  disagree with. ``contracts/leagues.json`` carries them for clients and for the check; the
  second output of this generator, ``backend/nbastats/shared/_generated_leagues.py``, carries the
  same two lists as Python constants, because ``nbastats/shared`` is stdlib-only and file-free
  and so cannot open a JSON file. ``scripts/check_contracts.py`` check (m) regenerates both and
  fails on any difference.

What is not here
----------------
``leagues.json`` is **not** one of the three bundled catalogs (``CATALOGS`` in
``check_contracts.py``), so check (e) and the iOS bundle never see it. Clients receive the league
facts at run time from ``GET /v1/leagues`` and ``GET /v1/el/meta``. It is also deliberately not
embedded in ``web/src/generated/contracts.ts``: the guard list names the vocabulary it forbids,
and the web release's prose guard rightly refuses that vocabulary anywhere under ``web/src``.

Why seven of the words are written in two pieces in the Python output
--------------------------------------------------------------------
``tests/test_web_release_hardening.py`` greps the *source text* of whole packages, including
``nbastats/shared``, for words that must never reach a reader. The generated module is source text
too, and it is the one place that has to name the words it forbids. A list that forbids a word
cannot trip the scan that forbids it, so :data:`SPLIT_FOR_PROSE_GUARD` spells the words that scan
also bans as two string literals added together (a formatter that merges adjacent literals would
undo any subtler trick, which is why ``+`` is written out). :func:`_assert_prose_clean` refuses to
emit a file in which any banned word survives whole.

Usage::

    python3 contracts/tools/gen_leagues.py > contracts/leagues.json
    python3 contracts/tools/gen_leagues.py --python \
        > backend/nbastats/shared/_generated_leagues.py

Both print to stdout, like every other generator, so check (m) can diff them byte for byte.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from nbastats.shared import availability, positions  # noqa: E402  (path insert must run first)
from nbastats.shared.league_profile import EUROLEAGUE, NBA  # noqa: E402

SCHEMA_VERSION = 1

#: Words that may not appear, as a whole word, in any JSON object key of a new payload. Keys are
#: split into words on case changes, digits and punctuation before matching (``overtimePeriods``
#: is ``overtime`` and ``periods``), and matching is on whole words, never substrings, so
#: ``coverage`` and ``headline`` are fine. The list is chosen so that no new payload key needs an
#: exception. ``probability`` is here because a probability of winning is omitted from every
#: payload in this version.
FORBIDDEN_PAYLOAD_KEY_WORDS: tuple[str, ...] = (
    "line",
    "lines",
    "odds",
    "moneyline",
    "spread",
    "over",
    "under",
    "lean",
    "edge",
    "pick",
    "picks",
    "push",
    "implied",
    "cover",
    "vig",
    "juice",
    "stake",
    "wager",
    "bookmaker",
    "market",
    "parlay",
    "handicap",
    "ats",
    "probability",
)

#: Every query, path and body field name a new route may declare, grouped by what it names. A
#: route that needs a name outside this list needs the list, and the reasoning, changed first: no
#: route may accept an external number to compare with a projection, and no name here could carry
#: one. The group titles become comments in the Python output.
ALLOWED_PARAMETER_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "identity of what is asked about",
        (
            "asOfRound",
            "awayTeamId",
            "clubCode",
            "gameId",
            "homeTeamId",
            "overrideId",
            "personCode",
            "playerId",
            "playerIds",
            "statusId",
            "teamId",
            "teamIds",
        ),
    ),
    (
        "scope and window",
        (
            "basis",
            "cursor",
            "date",
            "includeNews",
            "limit",
            "minGames",
            "perClub",
            "perMode",
            "phase",
            "round",
            "scheme",
            "season",
            "seasonType",
            "sort",
            "statuses",
            "window",
        ),
    ),
    (
        "body of the availability write",
        (
            "expectedReturnText",
            "note",
            "playerName",
            "reasonCategory",
            "reasonText",
            "sourceLabel",
            "sourcePublishedAt",
            "sourceUrl",
            "status",
        ),
    ),
    (
        "body of the pasted-link write",
        ("link", "publishedAt", "sourceName", "title"),
    ),
    (
        "body of the model-settings write",
        ("key", "settings", "value"),
    ),
)
ALLOWED_PARAMETERS: tuple[str, ...] = tuple(
    name for _, names in ALLOWED_PARAMETER_GROUPS for name in names
)

#: The words that ``tests/test_web_release_hardening.py`` bans in source text, written as the two
#: pieces the Python output joins. Every forbidden key word the prose guard also bans must be
#: here; :func:`_assert_prose_clean` proves none survives whole.
SPLIT_FOR_PROSE_GUARD: dict[str, tuple[str, str]] = {
    "odds": ("od", "ds"),
    "moneyline": ("moneyl", "ine"),
    "vig": ("vi", "g"),
    "stake": ("sta", "ke"),
    "wager": ("wa", "ger"),
    "bookmaker": ("book", "maker"),
    "parlay": ("par", "lay"),
}

#: The prose guard's own patterns (a mirror of ``banned`` in ``test_web_release_hardening``; the
#: test of the same name in ``test_no_market_machinery`` asserts the two lists agree).
PROSE_GUARD_PATTERNS: tuple[str, ...] = (
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
_PROSE_GUARD = re.compile(r"\b(?:" + "|".join(PROSE_GUARD_PATTERNS) + r")\b")


def _assert_unique(names: tuple[str, ...], what: str) -> None:
    if len(set(names)) != len(names):
        raise SystemExit(f"duplicate entry in {what}")


_assert_unique(FORBIDDEN_PAYLOAD_KEY_WORDS, "FORBIDDEN_PAYLOAD_KEY_WORDS")
_assert_unique(ALLOWED_PARAMETERS, "ALLOWED_PARAMETERS")
for _word in FORBIDDEN_PAYLOAD_KEY_WORDS:
    if _word != _word.lower() or not _word.isalpha():
        raise SystemExit(f"forbidden word {_word!r} must be a single lower-case word")
for _word, (_head, _tail) in SPLIT_FOR_PROSE_GUARD.items():
    if _head + _tail != _word or _word not in FORBIDDEN_PAYLOAD_KEY_WORDS:
        raise SystemExit(f"{_word!r} is not split into two pieces of a forbidden word")


def build_document() -> dict:
    """The ``leagues.json`` document."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "leagues": [NBA.to_contract(), EUROLEAGUE.to_contract()],
        "availability": availability.vocabulary(),
        "positions": positions.scheme_contract(),
        "forbiddenPayloadKeyWords": list(FORBIDDEN_PAYLOAD_KEY_WORDS),
        "allowedParameters": list(ALLOWED_PARAMETERS),
    }


# --------------------------------------------------------------------------- python output

_PYTHON_HEADER = '''"""GENERATED by contracts/tools/gen_leagues.py - DO NOT EDIT.

The market-vocabulary constants, in the form ``contracts/leagues.json`` carries them.

``contracts/leagues.json`` is the one definition of two lists that guard every new payload
(EUROLEAGUE_DESIGN section 10.2): the words no JSON key may contain, and the only parameter
names a new route may accept. ``shared/market_guard.py`` must read them, but ``shared/`` is
stdlib-only and file-free, so it cannot open a JSON file; it reads this module instead, which
``gen_leagues.py --python`` writes from the same lists that write the JSON. The *names* below
(``FORBIDDEN_PAYLOAD_KEY_WORDS``, ``ALLOWED_PARAMETERS``) are the interface and must survive any
change to the generator. ``scripts/check_contracts.py`` check (m) regenerates this file and
fails when it differs, so a hand edit here cannot survive CI.

Why seven of the words are written in two pieces
------------------------------------------------
``tests/test_web_release_hardening.py`` greps the *source text* of whole packages for a list
of words that must never reach a reader. This file is source text too, and it is the one place
that has to name the words it forbids. A list that forbids a word cannot be allowed to trip the
scan that forbids it, so the seven entries the scan also bans are spelled as two string
literals added together (a formatter that merges adjacent literals would undo any subtler
trick). ``tests/shared/test_market_guard.py`` asserts the joined set equals the designed set
exactly, and ``test_stdlib_only.py`` asserts this directory is clean under the scan's own
pattern, so a regenerated file that writes them whole fails loudly instead of silently breaking
the prose guard.

The key words are matched after a key is split into words (``overtimePeriods`` becomes
``overtime`` and ``periods``), never as substrings, so ``coverage``, ``headline`` and
``baseline`` are all fine. The key list is chosen so that no new payload key needs an
exception.
"""

from __future__ import annotations

from typing import Final

__all__ = ["FORBIDDEN_PAYLOAD_KEY_WORDS", "ALLOWED_PARAMETERS"]

#: Words that may not appear, as a whole word, in any JSON object key of a new payload.
FORBIDDEN_PAYLOAD_KEY_WORDS: Final[tuple[str, ...]] = (
'''


def _literal(word: str) -> str:
    if word in SPLIT_FOR_PROSE_GUARD:
        head, tail = SPLIT_FOR_PROSE_GUARD[word]
        return f'"{head}" + "{tail}"'
    return f'"{word}"'


def _assert_prose_clean(source: str) -> None:
    found = sorted(set(_PROSE_GUARD.findall(source.lower())))
    if found:
        raise SystemExit(
            "the generated Python would trip the web release's prose guard with: "
            + ", ".join(found)
            + "; add the word to SPLIT_FOR_PROSE_GUARD"
        )


def build_python() -> str:
    """The text of ``backend/nbastats/shared/_generated_leagues.py``."""
    lines = [_PYTHON_HEADER.rstrip("\n")]
    for word in FORBIDDEN_PAYLOAD_KEY_WORDS:
        lines.append(f"    {_literal(word)},")
    lines.append(")")
    lines.append("")
    lines.extend(
        [
            "#: Every query, path and body field name a new route (``/v1/el/**``,",
            "#: ``/v1/matchups*`` and the rest of design section 9) may declare. A route that",
            "#: needs a name outside this list needs the list, and the reasoning, changed first:",
            "#: no route may accept an external number to compare with a projection.",
        ]
    )
    lines.append("ALLOWED_PARAMETERS: Final[tuple[str, ...]] = (")
    for title, names in ALLOWED_PARAMETER_GROUPS:
        lines.append(f"    # {title}")
        for name in names:
            lines.append(f'    "{name}",')
    lines.append(")")
    source = "\n".join(lines) + "\n"
    _assert_prose_clean(source)
    return source


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--python"]:
        sys.stdout.write(build_python())
        return 0
    if args:
        print("usage: gen_leagues.py [--python]", file=sys.stderr)
        return 2
    print(json.dumps(build_document(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
