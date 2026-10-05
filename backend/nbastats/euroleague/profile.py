"""The EuroLeague's constants, identifiers and small pure helpers.

Everything that is a *fact about the competition* rather than behaviour lives here, so the
importer, the demo, the live ingest and the read side agree on spellings. The numbers that
differ between leagues (game length, sample thresholds, home advantage) are in
:data:`nbastats.shared.league_profile.EUROLEAGUE`; this module re-exports it as
:data:`PROFILE` and adds the vocabulary only the EuroLeague store needs.

Identifiers
-----------
Ids are **strings**, because the sources' own ids are strings and because a string id cannot
be mistaken for an NBA integer id anywhere downstream.

* A club is its official code (``PAN``). The workbook's abbreviations are a different system
  and live in ``el_club_alias`` keyed by ``(system, code)``: the workbook's ``PAR`` is Paris
  while the official ``PAR`` is believed to be Partizan, so a lookup by bare code is a bug.
* A person is the bare official code (a ``P`` prefix is stripped), or ``wb-<8hex>`` when the
  workbook minted one, or ``demo-<n>`` in the invented league. Minted codes are *deterministic*
  from ``(club, folded name)`` so re-importing the same squad finds the same person.
* A game is ``E2026-0012`` (season code, four-digit game code), or ``E2026-R03-01`` for a
  workbook fixture that has no official game code yet. Reconciliation re-keys the second form
  to the first once the service names the game.
* A season is ``E2026`` in the store and ``2026-27`` on screen. ``E``-codes exist only here and
  can never reach the NBA's season parsing.

Name folding
------------
:func:`fold_name` is the one definition of "the same name" for matching a workbook line to a
person, a status to a squad member, or a workbook person to an official one. It lower-cases,
strips accents, removes apostrophes and periods, turns hyphens into spaces and collapses
whitespace. It deliberately keeps generational suffixes (``Jr``): dropping them would merge a
father and son, and a failed match goes to the review queue, while a wrong match corrupts a
player's season.

The crosswalk and the denylist
------------------------------
``data/club_codes.json`` is committed (club names and codes are facts, not statistics) and
every row is ``pending``: none could be verified from the build environment. An unknown
workbook code aborts an import; it is never guessed. ``data/source_denylist.json`` lists
domains owned by gambling operators. :func:`screen_source_link` withholds such a link while
keeping the label and the date, so Hardwood never sends a reader to a gambling product. It
matches the *host of a link*, never text, so the club called "Partizan Mozzart Bet" is a club
name and is stored as data.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Iterable
from urllib.parse import urlsplit

from ..shared.league_profile import EUROLEAGUE, EUROLEAGUE_KEY

__all__ = [
    "LEAGUE_KEY",
    "PROFILE",
    "COMPETITION_CODE",
    "PHASES",
    "DEFAULT_PHASE",
    "STORE_KINDS",
    "KIND_SYNTHETIC",
    "KIND_WORKBOOK",
    "KIND_LIVE",
    "REAL_KINDS",
    "GAME_STATUSES",
    "STATS_STATUSES",
    "PARTICIPATION",
    "POSITION_CODES",
    "POSITION5",
    "POSITION5_TO_CODE",
    "ESTIMATE_BASIS_LABEL",
    "DATA_SOURCE_SYNTHETIC",
    "DATA_SOURCE_LIVE",
    "DATA_DIR",
    "SeasonRef",
    "season_code",
    "season_label",
    "season_ref_from_start_year",
    "parse_season_ref",
    "official_game_id",
    "provisional_game_id",
    "GameIdParts",
    "parse_game_id",
    "workbook_data_source",
    "is_workbook_data_source",
    "normalise_club_code",
    "normalise_person_code",
    "fold_name",
    "workbook_person_code",
    "ClubCodeRow",
    "ClubCrosswalk",
    "CrosswalkError",
    "load_club_codes",
    "load_source_denylist",
    "is_denied_url",
    "screen_source_link",
    "WITHHELD_SUFFIX",
]

LEAGUE_KEY: Final = EUROLEAGUE_KEY

#: The shared profile: game length, sample thresholds, home advantage, staleness.
PROFILE: Final = EUROLEAGUE

#: The competition code in the data service's URLs and in season codes.
COMPETITION_CODE: Final = "E"

#: Phases of a season. A game is in exactly one; the default for a workbook is the regular
#: season, because a workbook of rounds does not say otherwise.
PHASES: Final[tuple[str, ...]] = ("RS", "PI", "PO", "FF")
DEFAULT_PHASE: Final = "RS"

KIND_SYNTHETIC: Final = "synthetic"
KIND_WORKBOOK: Final = "workbook"
KIND_LIVE: Final = "live"
STORE_KINDS: Final[tuple[str, ...]] = (KIND_SYNTHETIC, KIND_WORKBOOK, KIND_LIVE)
#: Kinds that hold real data. They may coexist in one file because the workbook's box scores
#: came from the same data service live ingest reads; only ``synthetic`` is incompatible.
REAL_KINDS: Final[tuple[str, ...]] = (KIND_WORKBOOK, KIND_LIVE)

GAME_STATUSES: Final[tuple[str, ...]] = ("scheduled", "final", "postponed")
STATS_STATUSES: Final[tuple[str, ...]] = ("none", "ok", "quarantined")
PARTICIPATION: Final[tuple[str, ...]] = ("played", "dnp")

#: The registration's three-way position code and the bucket it means.
POSITION_CODES: Final[dict[int, str]] = {1: "G", 2: "F", 3: "C"}
#: The five labels the workbook's author assigned. Always an estimate, never official.
POSITION5: Final[tuple[str, ...]] = ("PG", "SG", "SF", "PF", "C")
#: The three-way code a five-way label folds into.
POSITION5_TO_CODE: Final[dict[str, int]] = {"PG": 1, "SG": 1, "SF": 2, "PF": 2, "C": 3}

#: What a screen says beside a per-40 line imported from a workbook row marked ``est.``: the
#: user typed or translated it, and where it came from was not recorded.
ESTIMATE_BASIS_LABEL: Final = "your estimate; source not recorded"

DATA_SOURCE_SYNTHETIC: Final = "synthetic-demo"
DATA_SOURCE_LIVE: Final = "euroleague-v2"

DATA_DIR: Final = Path(__file__).resolve().parent / "data"


# ------------------------------------------------------------------------------ seasons


@dataclass(frozen=True)
class SeasonRef:
    """One season in both of its spellings."""

    code: str
    label: str
    start_year: int

    @property
    def competition_code(self) -> str:
        return COMPETITION_CODE


def season_code(start_year: int) -> str:
    """``2026`` becomes ``E2026``."""
    return f"{COMPETITION_CODE}{int(start_year):04d}"


def season_label(start_year: int) -> str:
    """``2026`` becomes ``2026-27`` (the century digits of the second year are dropped)."""
    return f"{int(start_year):04d}-{(int(start_year) + 1) % 100:02d}"


def season_ref_from_start_year(start_year: int) -> SeasonRef:
    return SeasonRef(
        code=season_code(start_year), label=season_label(start_year), start_year=int(start_year)
    )


_SEASON_CODE = re.compile(r"^E(\d{4})$")
_SEASON_LABEL = re.compile(r"^(\d{4})-(\d{2})$")


def parse_season_ref(value: str | None) -> SeasonRef | None:
    """``E2026`` or ``2026-27`` as a :class:`SeasonRef`, or ``None`` for anything else.

    A label whose second half is not the year after the first (``2026-28``) is rejected, so a
    typo is not quietly read as a different season.
    """
    if value is None:
        return None
    text = value.strip()
    match = _SEASON_CODE.match(text)
    if match:
        return season_ref_from_start_year(int(match.group(1)))
    match = _SEASON_LABEL.match(text)
    if match:
        start = int(match.group(1))
        if (start + 1) % 100 == int(match.group(2)):
            return season_ref_from_start_year(start)
    return None


# ------------------------------------------------------------------------------ games


def official_game_id(season: str, game_code: int) -> str:
    """``("E2026", 12)`` becomes ``E2026-0012``."""
    return f"{season}-{int(game_code):04d}"


def provisional_game_id(season: str, round_number: int, number: int) -> str:
    """``("E2026", 3, 1)`` becomes ``E2026-R03-01``: a fixture the service has not named yet."""
    return f"{season}-R{int(round_number):02d}-{int(number):02d}"


@dataclass(frozen=True)
class GameIdParts:
    season_code: str
    game_code: int | None
    round_number: int | None
    number: int | None

    @property
    def is_provisional(self) -> bool:
        return self.game_code is None


_GAME_OFFICIAL = re.compile(r"^(E\d{4})-(\d{4})$")
_GAME_PROVISIONAL = re.compile(r"^(E\d{4})-R(\d{2})-(\d{2})$")


def parse_game_id(game_id: str) -> GameIdParts | None:
    """Split an id into its parts, or ``None`` when it is neither shape."""
    match = _GAME_OFFICIAL.match(game_id or "")
    if match:
        return GameIdParts(match.group(1), int(match.group(2)), None, None)
    match = _GAME_PROVISIONAL.match(game_id or "")
    if match:
        return GameIdParts(match.group(1), None, int(match.group(2)), int(match.group(3)))
    return None


def workbook_data_source(sha256_hex: str) -> str:
    """``workbook:<sha8>``: which file a row came from, short enough to read in a log."""
    return f"workbook:{sha256_hex[:8]}"


def is_workbook_data_source(value: str | None) -> bool:
    return bool(value) and value.startswith("workbook:")  # type: ignore[union-attr]


# ------------------------------------------------------------------------------ codes


def normalise_club_code(raw: Any) -> str | None:
    """Upper-case, trimmed club code; ``None`` for blank or non-text input."""
    if not isinstance(raw, str):
        return None
    text = raw.strip().upper()
    return text or None


def normalise_person_code(raw: Any) -> str | None:
    """The bare official person code: the service's ``P`` prefix is stripped.

    ``None`` for blank input. A code that is only ``P`` is left alone because stripping it
    would leave nothing.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if len(text) > 1 and text[0] in "Pp" and text[1:].isalnum():
        return text[1:]
    return text


_TRANSLATE = str.maketrans(
    {
        "ı": "i",  # dotless i
        "ø": "o",
        "Ø": "o",
        "đ": "d",
        "Đ": "d",
        "ł": "l",
        "Ł": "l",
        "ß": "ss",
        "æ": "ae",
        "Æ": "ae",
        "œ": "oe",
        "Œ": "oe",
        "ð": "d",
        "Ð": "d",
        "Þ": "th",
        "þ": "th",
        "’": "",
        "‘": "",
        "'": "",
        ".": "",
    }
)
_NON_WORD = re.compile(r"[^a-z0-9]+")


def fold_name(name: Any) -> str:
    """The comparison form of a person's name; ``""`` when there is nothing to compare."""
    if not isinstance(name, str):
        return ""
    decomposed = unicodedata.normalize("NFKD", name.translate(_TRANSLATE))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_WORD.sub(" ", stripped.lower()).strip()


def workbook_person_code(club_code: str, folded_name: str, attempt: int = 0) -> str:
    """``wb-<8hex>``: a deterministic code for a person the workbook names.

    Derived from the club and the folded name only, never the season or the run, so the same
    squad member re-imported next round is the same person. ``attempt`` exists so a caller
    can step past the astronomically unlikely collision of two different people.
    """
    seed = f"wb|{club_code}|{folded_name}" + (f"|{attempt}" if attempt else "")
    return "wb-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]


# ------------------------------------------------------------------------------ crosswalk


class CrosswalkError(ValueError):
    """The committed club-code crosswalk is malformed."""


@dataclass(frozen=True)
class ClubCodeRow:
    workbook_code: str
    official_code: str
    tv_code: str | None
    name: str
    short_name: str | None
    #: ``fixture`` once a recorded response confirms the row, ``pending`` until then.
    verified: str


@dataclass(frozen=True)
class ClubCrosswalk:
    rows: tuple[ClubCodeRow, ...]

    def by_workbook(self, code: Any) -> ClubCodeRow | None:
        key = normalise_club_code(code)
        for row in self.rows:
            if row.workbook_code == key:
                return row
        return None

    def by_official(self, code: Any) -> ClubCodeRow | None:
        key = normalise_club_code(code)
        for row in self.rows:
            if row.official_code == key:
                return row
        return None

    @property
    def unverified_official_codes(self) -> tuple[str, ...]:
        """Official codes whose row is still ``pending``, for ``/v1/el/meta``."""
        return tuple(row.official_code for row in self.rows if row.verified != "fixture")


_CODE = re.compile(r"^[A-Z0-9]{2,4}$")


@lru_cache(maxsize=4)
def _load_club_codes(path: str) -> ClubCrosswalk:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        raw_rows = document["clubs"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CrosswalkError(f"cannot read the club-code crosswalk {path}: {exc}") from exc
    rows: list[ClubCodeRow] = []
    for index, raw in enumerate(raw_rows):
        try:
            row = ClubCodeRow(
                workbook_code=str(raw["workbookCode"]),
                official_code=str(raw["officialCode"]),
                tv_code=(str(raw["tvCode"]) if raw.get("tvCode") else None),
                name=str(raw["name"]),
                short_name=(str(raw["shortName"]) if raw.get("shortName") else None),
                verified=str(raw["verified"]),
            )
        except (KeyError, TypeError) as exc:
            raise CrosswalkError(f"club row {index} is malformed: {exc}") from exc
        if not _CODE.match(row.workbook_code) or not _CODE.match(row.official_code):
            raise CrosswalkError(f"club row {index} has a malformed code: {raw!r}")
        if row.verified not in ("fixture", "pending"):
            raise CrosswalkError(f"club row {index} has verified={row.verified!r}")
        rows.append(row)
    for label, values in (
        ("workbook", [r.workbook_code for r in rows]),
        ("official", [r.official_code for r in rows]),
    ):
        if len(set(values)) != len(values):
            raise CrosswalkError(f"the crosswalk repeats a {label} code")
    return ClubCrosswalk(tuple(rows))


def load_club_codes(path: str | Path | None = None) -> ClubCrosswalk:
    """The committed crosswalk (or the one at ``path``), validated and cached."""
    return _load_club_codes(str(path if path is not None else DATA_DIR / "club_codes.json"))


# ------------------------------------------------------------------------------ denylist


@lru_cache(maxsize=4)
def _denylist_document(path: str) -> dict[str, Any]:
    """The denylist file, parsed. An unreadable or malformed file raises: that is a start-up
    problem (the committed file is parsed by the tests), never a reason to link out."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document.get("domains"), list) or not isinstance(
        document.get("withheldSuffix"), str
    ):
        raise ValueError(f"{path} must hold a 'domains' list and a 'withheldSuffix' string")
    return document


def load_source_denylist(path: str | Path | None = None) -> frozenset[str]:
    """The gambling-operator domains whose links are never stored."""
    document = _denylist_document(
        str(path if path is not None else DATA_DIR / "source_denylist.json")
    )
    return frozenset(
        str(d).strip().lower().rstrip(".") for d in document["domains"] if str(d).strip()
    )


#: What a status's label ends with when its link was withheld. The words live in the data file
#: beside the domains they are about, so the source files that handle them stay free of the
#: vocabulary the prose guard keeps out of them.
WITHHELD_SUFFIX: Final[str] = _denylist_document(str(DATA_DIR / "source_denylist.json"))[
    "withheldSuffix"
]


def _host(url: str) -> str | None:
    try:
        host = urlsplit(url.strip()).hostname
    except ValueError:
        return None
    return host.lower().rstrip(".") if host else None


def is_denied_url(url: Any, denylist: Iterable[str] | None = None) -> bool:
    """True when the link's host is a denylisted domain or a subdomain of one.

    A link with a backslash is denied outright: a browser reads ``\\`` as ``/`` in an http(s)
    URL, so ``https://operator.example\\@example.org/`` opens the operator's site while
    ``urlsplit`` reports ``example.org``. Nobody can vouch for such a link, so it is withheld."""
    if not isinstance(url, str) or not url.strip():
        return False
    if "\\" in url:
        return True
    host = _host(url)
    if host is None:
        return False
    domains = load_source_denylist() if denylist is None else frozenset(denylist)
    return any(host == d or host.endswith("." + d) for d in domains)


def screen_source_link(
    url: str | None, label: str, denylist: Iterable[str] | None = None
) -> tuple[str | None, str]:
    """``(url, label)`` as they may be stored: a denied link becomes ``(None, label + note)``.

    The label and the caller's ``source_kind`` and date are kept, so the entry still says who
    reported what and when; only the link is withheld.
    """
    if is_denied_url(url, denylist):
        return None, f"{label} {WITHHELD_SUFFIX}"
    return url, label
