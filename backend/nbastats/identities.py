"""Real NBA identities — the one place the demo league is allowed to be factual.

``nbastats/data/nba_identities.json`` is a bundled snapshot of the ``nba_api`` static tables.
Its provenance block draws the line this module enforces:

* **factual, and shippable** — NBA person ids, player names, the 30 franchises (id, name,
  abbreviation, city, year founded) and ``headshotUrl``, which is a pure function of the
  person id;
* **not in the file, and not to be invented** — player-to-team assignment, positions,
  physical attributes and every statistic. Rosters change constantly; asserting one from a
  bundled snapshot would be fabrication, so the file states none and neither does this module.

So the index answers "who is this person, and what is their headshot" and nothing else. The
demo league in :mod:`nbastats.seed` borrows the names and the photos, generates everything
else, and says so — see ``DEMO_ATTRIBUTION`` in :mod:`nbastats.api.routes_meta`.

Degradation is deliberate: a missing or malformed file logs one warning and yields an *empty*
index. Every accessor keeps working (empty tuples, ``None`` lookups) so a packaging mistake
costs the app its photos, never its availability.

Lookup path: ``HARDWOOD_IDENTITIES_PATH`` if set, otherwise ``data/nba_identities.json``
beside this module. The parsed index is cached; :func:`reset_cache` drops it, which is what a
test that moves the file needs.
"""
from __future__ import annotations

import json
import logging
import os
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

__all__ = [
    "PlayerIdentity",
    "TeamIdentity",
    "IdentityIndex",
    "HEADSHOT_URL_PATTERN",
    "IDENTITIES_PATH_ENV",
    "identities_path",
    "index",
    "teams",
    "TEAM_ALIGNMENT",
    "team_alignment",
    "team",
    "players",
    "active_players",
    "historical_players",
    "player",
    "find",
    "headshot_url",
    "provenance",
    "fold_name",
    "reset_cache",
]

logger = logging.getLogger("nbastats.identities")

#: Override for the bundled file. Set it to a path; an unreadable one degrades to empty.
IDENTITIES_PATH_ENV = "HARDWOOD_IDENTITIES_PATH"

#: The documented NBA.com CDN pattern. The file carries the rendered URL per player; this is
#: here so callers can recognise it (and so the test can assert the shape).
HEADSHOT_URL_PATTERN = "https://cdn.nba.com/headshots/nba/latest/1040x760/{player_id}.png"


# --------------------------------------------------------------------------- name folding

#: Letters that carry no combining mark to strip — NFKD leaves them exactly as they are, so
#: "Dončić" folds to "doncic" for free but "Luka Dončić"-style names with ``đ``/``ø``/``ł``
#: would not without this table.
_TRANSLITERATIONS = {
    "đ": "d", "Đ": "D", "ø": "o", "Ø": "O", "ł": "l", "Ł": "L",
    "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ß": "ss", "ı": "i",
    "ð": "d", "Ð": "D", "þ": "th", "Þ": "TH", "ŋ": "n", "Ŋ": "N",
}

#: Separators inside a name: they become spaces, so "Gilgeous-Alexander" is findable as two
#: words. Everything else non-alphanumeric is dropped, so "A.J." folds to "aj" and "O'Neal"
#: to "oneal" — which is how people type them.
_NAME_SEPARATORS = frozenset("-–—_/\\")


def fold_name(value: str) -> str:
    """Casefold, strip diacritics and normalise punctuation for comparison.

    ``fold_name("Luka Dončić") == "luka doncic"`` and ``fold_name("Nikola Jokić")
    == "nikola jokic"``: this is the whole reason a reader can type "Doncic" and find him.
    """
    if not value:
        return ""
    translated = "".join(_TRANSLITERATIONS.get(char, char) for char in value)
    decomposed = unicodedata.normalize("NFKD", translated)
    cleaned: list[str] = []
    for char in decomposed:
        if unicodedata.combining(char):
            continue
        if char.isalnum():
            cleaned.append(char)
        elif char.isspace() or char in _NAME_SEPARATORS:
            cleaned.append(" ")
    return " ".join("".join(cleaned).casefold().split())


# --------------------------------------------------------------------------- record types


@dataclass(frozen=True, slots=True)
class PlayerIdentity:
    """One real person: id, name, whether the snapshot had them active, and their photo.

    There is no team, no position and no measurement here, and that absence is the point.
    """

    player_id: int
    name: str
    first_name: str
    last_name: str
    is_active: bool
    headshot_url: str

    @property
    def folded_name(self) -> str:
        return fold_name(self.name)

    @property
    def folded_last_name(self) -> str:
        return fold_name(self.last_name)


#: Conference and division — the two franchise facts ``data/nba_identities.json`` does not
#: carry, because nba_api's static team table does not carry them either. Everything else
#: about a franchise comes from the file and is reconciled with this map by abbreviation.
#:
#: This lives here rather than in :mod:`nbastats.seed` because it is a fact about the league,
#: not about the demo generator: the live ingest path needs it too, and an ingest importing
#: from the seeder to learn which conference Denver plays in would have the dependency
#: backwards.
TEAM_ALIGNMENT: dict[str, tuple[str, str]] = {
    "ATL": ("East", "Southeast"), "BOS": ("East", "Atlantic"), "BKN": ("East", "Atlantic"),
    "CHA": ("East", "Southeast"), "CHI": ("East", "Central"), "CLE": ("East", "Central"),
    "DET": ("East", "Central"), "IND": ("East", "Central"), "MIA": ("East", "Southeast"),
    "MIL": ("East", "Central"), "NYK": ("East", "Atlantic"), "ORL": ("East", "Southeast"),
    "PHI": ("East", "Atlantic"), "TOR": ("East", "Atlantic"), "WAS": ("East", "Southeast"),
    "DAL": ("West", "Southwest"), "DEN": ("West", "Northwest"), "GSW": ("West", "Pacific"),
    "HOU": ("West", "Southwest"), "LAC": ("West", "Pacific"), "LAL": ("West", "Pacific"),
    "MEM": ("West", "Southwest"), "MIN": ("West", "Northwest"), "NOP": ("West", "Southwest"),
    "OKC": ("West", "Northwest"), "PHX": ("West", "Pacific"), "POR": ("West", "Northwest"),
    "SAC": ("West", "Pacific"), "SAS": ("West", "Southwest"), "UTA": ("West", "Northwest"),
}


def team_alignment(abbr: str | None) -> tuple[str | None, str | None]:
    """``(conference, division)`` for a franchise, or ``(None, None)`` for an unknown one.

    Never guesses. A relocated or defunct abbreviation the map does not hold comes back empty
    rather than being assigned a plausible conference, which is the same rule the rest of the
    project applies to facts it does not have.
    """
    if not abbr:
        return None, None
    found = TEAM_ALIGNMENT.get(abbr.strip().upper())
    return found if found is not None else (None, None)


@dataclass(frozen=True, slots=True)
class TeamIdentity:
    """One of the 30 franchises. Conference and division are *not* in the file.

    :data:`TEAM_ALIGNMENT` above supplies those two, keyed by :attr:`abbr`.
    """

    team_id: int
    abbr: str
    name: str
    city: str
    nickname: str
    state: str
    year_founded: int | None


@dataclass(frozen=True, slots=True)
class _SearchKey:
    """Pre-folded name forms, computed once at load rather than per query."""

    folded_name: str
    folded_last: str
    identity: PlayerIdentity


@dataclass(frozen=True, slots=True)
class IdentityIndex:
    """The loaded file, indexed. Empty when the file is missing or malformed."""

    teams: tuple[TeamIdentity, ...]
    players: tuple[PlayerIdentity, ...]
    source_path: Path | None
    provenance: Mapping[str, Any]
    _by_player_id: Mapping[int, PlayerIdentity]
    _by_abbr: Mapping[str, TeamIdentity]
    _search: tuple[_SearchKey, ...]

    @property
    def is_empty(self) -> bool:
        return not self.players and not self.teams

    def player(self, player_id: int) -> PlayerIdentity | None:
        try:
            key = int(player_id)
        except (TypeError, ValueError):
            return None
        return self._by_player_id.get(key)

    def team(self, abbr: str) -> TeamIdentity | None:
        if not isinstance(abbr, str):
            return None
        return self._by_abbr.get(abbr.strip().upper())

    def active_players(self) -> tuple[PlayerIdentity, ...]:
        return tuple(p for p in self.players if p.is_active)

    def historical_players(self) -> tuple[PlayerIdentity, ...]:
        """Everyone the snapshot marks inactive — the retired-and-historical pool."""
        return tuple(p for p in self.players if not p.is_active)

    def find(
        self, query: str, *, limit: int = 25, active_only: bool = False
    ) -> tuple[PlayerIdentity, ...]:
        """Diacritic-insensitive name search, best match first.

        Ranking, in order: the whole name, the surname, a name prefix, a surname prefix, then
        anything containing the query. Ties break towards active players and then by name, so
        the result is stable for a given file.
        """
        folded = fold_name(query)
        if not folded or limit <= 0:
            return ()
        hits: list[tuple[int, int, str, int, PlayerIdentity]] = []
        for key in self._search:
            if active_only and not key.identity.is_active:
                continue
            if folded == key.folded_name:
                rank = 0
            elif folded == key.folded_last:
                rank = 1
            elif key.folded_name.startswith(folded):
                rank = 2
            elif key.folded_last.startswith(folded):
                rank = 3
            elif folded in key.folded_name:
                rank = 4
            else:
                continue
            hits.append(
                (rank, 0 if key.identity.is_active else 1, key.folded_name,
                 key.identity.player_id, key.identity)
            )
        hits.sort(key=lambda row: row[:4])
        return tuple(row[4] for row in hits[:limit])


_EMPTY_INDEX = IdentityIndex(
    teams=(), players=(), source_path=None, provenance={},
    _by_player_id={}, _by_abbr={}, _search=(),
)


# --------------------------------------------------------------------------- loading


def identities_path() -> Path:
    """Where the identity file is expected: the env override, else beside this module."""
    override = os.environ.get(IDENTITIES_PATH_ENV)
    if override and override.strip():
        return Path(override.strip()).expanduser()
    return Path(__file__).resolve().parent / "data" / "nba_identities.json"


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _player_from(raw: Any) -> PlayerIdentity | None:
    if not isinstance(raw, dict):
        return None
    player_id = _int_or_none(raw.get("playerId"))
    name = raw.get("name")
    if player_id is None or not isinstance(name, str) or not name.strip():
        return None
    first = raw.get("firstName")
    last = raw.get("lastName")
    if not isinstance(first, str):
        first = name.split(" ", 1)[0]
    if not isinstance(last, str):
        last = name.split(" ", 1)[-1]
    headshot = raw.get("headshotUrl")
    if not isinstance(headshot, str) or not headshot.strip():
        headshot = HEADSHOT_URL_PATTERN.format(player_id=player_id)
    return PlayerIdentity(
        player_id=player_id,
        name=name.strip(),
        first_name=first.strip(),
        last_name=last.strip(),
        is_active=bool(raw.get("isActive")),
        headshot_url=headshot.strip(),
    )


def _team_from(raw: Any) -> TeamIdentity | None:
    if not isinstance(raw, dict):
        return None
    team_id = _int_or_none(raw.get("teamId"))
    abbr = raw.get("abbr")
    if team_id is None or not isinstance(abbr, str) or not abbr.strip():
        return None
    name = raw.get("name") if isinstance(raw.get("name"), str) else ""
    city = raw.get("city") if isinstance(raw.get("city"), str) else ""
    nickname = raw.get("nickname") if isinstance(raw.get("nickname"), str) else ""
    state = raw.get("state") if isinstance(raw.get("state"), str) else ""
    return TeamIdentity(
        team_id=team_id,
        abbr=abbr.strip().upper(),
        name=name.strip() or f"{city} {nickname}".strip(),
        city=city.strip(),
        nickname=nickname.strip(),
        state=state.strip(),
        year_founded=_int_or_none(raw.get("yearFounded")),
    )


def _build(document: Any, path: Path) -> IdentityIndex:
    if not isinstance(document, dict):
        raise ValueError("the identity file is not a JSON object")
    raw_teams = document.get("teams")
    raw_players = document.get("players")
    if not isinstance(raw_teams, list) or not isinstance(raw_players, list):
        raise ValueError("the identity file has no 'teams'/'players' arrays")

    teams_out: list[TeamIdentity] = []
    by_abbr: dict[str, TeamIdentity] = {}
    for raw in raw_teams:
        parsed = _team_from(raw)
        if parsed is None or parsed.abbr in by_abbr:
            continue
        by_abbr[parsed.abbr] = parsed
        teams_out.append(parsed)

    players_out: list[PlayerIdentity] = []
    by_id: dict[int, PlayerIdentity] = {}
    for raw in raw_players:
        parsed = _player_from(raw)
        if parsed is None or parsed.player_id in by_id:
            continue
        by_id[parsed.player_id] = parsed
        players_out.append(parsed)

    skipped = (len(raw_teams) - len(teams_out)) + (len(raw_players) - len(players_out))
    if skipped:
        logger.warning("identity file %s: skipped %d unusable row(s)", path, skipped)

    provenance = document.get("provenance")
    search = tuple(
        _SearchKey(folded_name=p.folded_name, folded_last=p.folded_last_name, identity=p)
        for p in players_out
    )
    return IdentityIndex(
        teams=tuple(teams_out),
        players=tuple(players_out),
        source_path=path,
        provenance=dict(provenance) if isinstance(provenance, dict) else {},
        _by_player_id=by_id,
        _by_abbr=by_abbr,
        _search=search,
    )


@lru_cache(maxsize=1)
def _load(path: str) -> IdentityIndex:
    target = Path(path)
    try:
        with target.open(encoding="utf-8") as handle:
            document = json.load(handle)
        return _build(document, target)
    except FileNotFoundError:
        logger.warning(
            "NBA identity file not found at %s; the demo falls back to generated names "
            "and no headshots.", target,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        logger.warning(
            "NBA identity file at %s could not be read (%s); the demo falls back to "
            "generated names and no headshots.", target, error,
        )
    return _EMPTY_INDEX


def index() -> IdentityIndex:
    """The cached index for the currently configured path."""
    return _load(str(identities_path()))


def reset_cache() -> None:
    """Drop the cached index — for tests that move or rewrite the file."""
    _load.cache_clear()


# --------------------------------------------------------------------------- accessors


def teams() -> tuple[TeamIdentity, ...]:
    """The 30 franchises, in file order. No conference or division: not in the file."""
    return index().teams


def team(abbr: str) -> TeamIdentity | None:
    """One franchise by abbreviation, case-insensitively."""
    return index().team(abbr)


def players() -> tuple[PlayerIdentity, ...]:
    """Every person in the snapshot, active and historical."""
    return index().players


def active_players() -> tuple[PlayerIdentity, ...]:
    """Those the snapshot marks active. Which team they play for is *not* stated."""
    return index().active_players()


def historical_players() -> tuple[PlayerIdentity, ...]:
    """Those the snapshot marks inactive — the pool a pre-modern demo season draws on."""
    return index().historical_players()


def player(player_id: int) -> PlayerIdentity | None:
    """One person by NBA person id, or ``None`` if the snapshot does not have them."""
    return index().player(player_id)


def find(query: str, *, limit: int = 25, active_only: bool = False) -> tuple[PlayerIdentity, ...]:
    """Search by name, ignoring diacritics: "Doncic" finds "Luka Dončić"."""
    return index().find(query, limit=limit, active_only=active_only)


def headshot_url(player_id: int) -> str | None:
    """The NBA.com CDN headshot for a known person id, else ``None``.

    Unknown ids get ``None`` rather than a rendered pattern: a URL for an id the snapshot
    does not carry would assert a person we cannot vouch for. Clients render their monogram
    fallback for ``None`` — and also for a real id whose photo 404s.
    """
    found = player(player_id)
    return found.headshot_url if found is not None else None


def provenance() -> Mapping[str, Any]:
    """The file's own provenance block: what it claims, and what it refuses to claim."""
    return index().provenance
