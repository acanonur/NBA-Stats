"""The only URLs the EuroLeague live ingest may request, and how it recognises them.

Written without being able to reach any EuroLeague host
-------------------------------------------------------
Every EuroLeague host returned a proxy refusal from the container this was written in, so
**none of the five endpoints below has been seen answering by the code that uses them**. They
come from the source and the test fixtures of an MIT-licensed npm client of the same service
(the facts: a host, a path template, a query name, and the JSON field names that parse.py
reads), and from the existence of the same paths in the documentation of a GPLv3 Python package
that was read as documentation only; no code from either was copied. Treat the table as the
design's best statement of a service nobody here has spoken to. The first real run on the Mac
is ``python -m nbastats.euroleague.ingest probe``, which makes each request once, records the
response under ``HARDWOOD_DATA_DIR/recordings/`` and says, per endpoint, whether the shape
matched what the parsers expect and which key it was looking for when it did not.

====  ==========================================================  ==========================
Key   Template                                                     Use
====  ==========================================================  ==========================
E1    ``/v2/competitions/E/seasons/{S}/rounds``                    the round calendar
E2    ``/v2/competitions/E/seasons/{S}/games?roundNumber={n}``     fixtures, results, game
                                                                   codes, partials, venue
E3    ``/v2/competitions/E/seasons/{S}/games/{gameCode}/stats``    one box score with each
                                                                   player's registration
E4    ``/v2/competitions/E/seasons/{S}/clubs``                     clubs (``code``, ``tvCode``)
E5    ``/v2/competitions/E/seasons/{S}/clubs/{clubCode}/people``   squads and positions
====  ==========================================================  ==========================

all on ``https://api-live.euroleague.net``.

Why a closed list, matched exactly
----------------------------------
The politeness budget in the design (about ninety requests a week) was worked out for these five
shapes. A sixth, however innocent it looks, is a request nobody budgeted, to a host whose terms
nobody here has read, so the client refuses any URL that is not one of the five *before* a
socket is considered. "Matched" means the whole URL: scheme ``https``, the exact host, no port,
no credentials, no fragment, a path made of the literal segments and one validated value per
placeholder, and no query other than E2's single ``roundNumber``. A look-alike (``http``, a
different host, an extra parameter, a path with ``..``) matches nothing.

What is deliberately not on the list
------------------------------------
* ``live.euroleague.net``: Cloudflare rate-limited, carries no positions, and the reason the
  design chose ``api-live`` in the first place.
* the v1 XML results feed: would need an XML dependency and adds nothing the v2 JSON lacks.
* the v3 statistics endpoints: their shape is unknown and everything wanted is in E3.
* EuroCup (competition code ``U``) and any season code that is not ``E`` plus four digits.
* anything that is not a ``GET``. (The client has no other verb.)

The season rule
---------------
:func:`current_season_code` follows the service's own convention: a season named for the year it
starts in, beginning in July. So October 2026 is ``E2026`` and May 2027 is still ``E2026``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Mapping
from urllib.parse import SplitResult, parse_qsl, urlsplit

__all__ = [
    "API_HOST",
    "API_ORIGIN",
    "COMPETITION",
    "BASE",
    "SEASON_PATTERN",
    "Endpoint",
    "ENDPOINTS",
    "EndpointNotAllowed",
    "MatchedEndpoint",
    "validate_season_code",
    "validate_game_code",
    "validate_round_number",
    "validate_club_code",
    "rounds_url",
    "games_url",
    "game_stats_url",
    "clubs_url",
    "club_people_url",
    "match_endpoint",
    "require_allowed",
    "is_allowed",
    "current_season_code",
]

API_HOST: Final = "api-live.euroleague.net"
API_ORIGIN: Final = f"https://{API_HOST}"
#: The competition code in the path. ``U`` (EuroCup) is not supported.
COMPETITION: Final = "E"
BASE: Final = f"{API_ORIGIN}/v2/competitions/{COMPETITION}"

SEASON_PATTERN: Final = re.compile(r"^E\d{4}$")
_CLUB_PATTERN: Final = re.compile(r"^[A-Z0-9]{2,4}$")
_MAX_ROUND: Final = 60
_MAX_GAME_CODE: Final = 9999


class EndpointNotAllowed(ValueError):
    """The URL (or a value destined for one) is not one of the five the ingest may use."""

    def __init__(self, message: str, *, url: str | None = None) -> None:
        super().__init__(message)
        self.url = url


@dataclass(frozen=True, slots=True)
class Endpoint:
    """One allowlisted request shape.

    ``verified`` is ``False`` for every endpoint in this build, and stays False until a recording
    made on the Mac has been parsed successfully. It is documentation, not behaviour: nothing
    branches on it, but the probe prints it so nobody mistakes the table for observed fact.
    """

    key: str
    name: str
    template: str
    use: str
    query: tuple[str, ...] = ()
    verified: bool = False


ENDPOINTS: Final[Mapping[str, Endpoint]] = {
    "E1": Endpoint("E1", "rounds", "/seasons/{season}/rounds", "the round calendar of a season"),
    "E2": Endpoint(
        "E2",
        "games",
        "/seasons/{season}/games?roundNumber={round}",
        "fixtures, results and game codes of one round",
        query=("roundNumber",),
    ),
    "E3": Endpoint(
        "E3",
        "stats",
        "/seasons/{season}/games/{gameCode}/stats",
        "one game's box score, with each player's registration",
    ),
    "E4": Endpoint("E4", "clubs", "/seasons/{season}/clubs", "the clubs of a season"),
    "E5": Endpoint(
        "E5",
        "people",
        "/seasons/{season}/clubs/{clubCode}/people",
        "a club's registered people (players and staff) for a season",
    ),
}


# ----------------------------------------------------------------------------- validators


def validate_season_code(season: object) -> str:
    """``E2026`` and nothing else: ``E`` followed by exactly four digits."""
    if not isinstance(season, str) or not SEASON_PATTERN.match(season):
        raise EndpointNotAllowed(f"{season!r} is not a EuroLeague season code such as 'E2026'")
    return season


def validate_game_code(code: object) -> int:
    """A positive integer no larger than 9999 (the id format has four digits)."""
    if isinstance(code, bool) or not isinstance(code, int) or not 1 <= code <= _MAX_GAME_CODE:
        raise EndpointNotAllowed(f"{code!r} is not a game code between 1 and {_MAX_GAME_CODE}")
    return code


def validate_round_number(number: object) -> int:
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= _MAX_ROUND:
        raise EndpointNotAllowed(f"{number!r} is not a round number between 1 and {_MAX_ROUND}")
    return number


def validate_club_code(code: object) -> str:
    """The official club code: two to four capital letters or digits."""
    if not isinstance(code, str) or not _CLUB_PATTERN.match(code):
        raise EndpointNotAllowed(f"{code!r} is not a club code such as 'PAN'")
    return code


# ------------------------------------------------------------------------------- builders


def rounds_url(season: str) -> str:
    """E1."""
    return f"{BASE}/seasons/{validate_season_code(season)}/rounds"


def games_url(season: str, round_number: int) -> str:
    """E2."""
    return (
        f"{BASE}/seasons/{validate_season_code(season)}/games"
        f"?roundNumber={validate_round_number(round_number)}"
    )


def game_stats_url(season: str, game_code: int) -> str:
    """E3."""
    return (
        f"{BASE}/seasons/{validate_season_code(season)}/games/"
        f"{validate_game_code(game_code)}/stats"
    )


def clubs_url(season: str) -> str:
    """E4."""
    return f"{BASE}/seasons/{validate_season_code(season)}/clubs"


def club_people_url(season: str, club_code: str) -> str:
    """E5."""
    return (
        f"{BASE}/seasons/{validate_season_code(season)}/clubs/"
        f"{validate_club_code(club_code)}/people"
    )


# -------------------------------------------------------------------------------- matching

_SEASONS: Final = r"/v2/competitions/E/seasons/(?P<season>E\d{4})"
_PATHS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("E1", re.compile(rf"^{_SEASONS}/rounds$")),
    ("E2", re.compile(rf"^{_SEASONS}/games$")),
    ("E3", re.compile(rf"^{_SEASONS}/games/(?P<game>[0-9]{{1,4}})/stats$")),
    ("E4", re.compile(rf"^{_SEASONS}/clubs$")),
    ("E5", re.compile(rf"^{_SEASONS}/clubs/(?P<club>[A-Z0-9]{{2,4}})/people$")),
)


@dataclass(frozen=True, slots=True)
class MatchedEndpoint:
    """An allowed URL, taken apart. Only the fields that endpoint has are set."""

    endpoint: Endpoint
    url: str
    season: str
    round_number: int | None = None
    game_code: int | None = None
    club_code: str | None = None

    @property
    def key(self) -> str:
        return self.endpoint.key


def _reject(reason: str, url: str) -> EndpointNotAllowed:
    return EndpointNotAllowed(
        f"{url!r} is not an allowed EuroLeague endpoint: {reason}. "
        "See nbastats/euroleague/ingest/endpoints.py for the five that are.",
        url=url,
    )


def _parts(url: str) -> SplitResult:
    try:
        return urlsplit(url)
    except ValueError as exc:
        raise _reject("it is not a valid URL", str(url)) from exc


def match_endpoint(url: object) -> MatchedEndpoint:
    """Take ``url`` apart, or raise :class:`EndpointNotAllowed` saying what is wrong with it.

    The match is on the whole URL; see the module docstring for what that rules out.
    """
    if not isinstance(url, str) or not url:
        raise EndpointNotAllowed("the URL is empty or not text", url=None)
    if any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in url):
        raise _reject("it contains whitespace or control characters", url)
    parts = _parts(url)
    if parts.scheme != "https":
        raise _reject(f"the scheme must be https, not {parts.scheme!r}", url)
    if parts.username is not None or parts.password is not None:
        raise _reject("it carries credentials", url)
    try:
        port = parts.port
    except ValueError as exc:
        raise _reject("its port is malformed", url) from exc
    if port is not None:
        raise _reject("an explicit port is not allowed", url)
    if (parts.hostname or "") != API_HOST:
        raise _reject(f"the host must be {API_HOST}", url)
    if parts.fragment:
        raise _reject("a fragment is not allowed", url)
    for key, pattern in _PATHS:
        match = pattern.match(parts.path)
        if match is None:
            continue
        endpoint = ENDPOINTS[key]
        try:
            queries = parse_qsl(
                parts.query, keep_blank_values=True, strict_parsing=bool(parts.query)
            )
        except ValueError as exc:
            raise _reject("its query string is malformed", url) from exc
        names = tuple(name for name, _ in queries)
        if names != endpoint.query:
            wanted = ", ".join(endpoint.query) or "no query"
            raise _reject(f"the query must be exactly: {wanted}", url)
        season = match.group("season")
        game = match.groupdict().get("game")
        club = match.groupdict().get("club")
        round_number: int | None = None
        if key == "E2":
            raw = queries[0][1]
            if not raw.isdigit():
                raise _reject("roundNumber must be a whole number", url)
            try:
                round_number = validate_round_number(int(raw))
            except EndpointNotAllowed as exc:
                raise _reject(str(exc), url) from exc
        game_code: int | None = None
        if game is not None:
            try:
                game_code = validate_game_code(int(game))
            except EndpointNotAllowed as exc:
                raise _reject(str(exc), url) from exc
        return MatchedEndpoint(
            endpoint=endpoint,
            url=url,
            season=season,
            round_number=round_number,
            game_code=game_code,
            club_code=club,
        )
    raise _reject("the path matches none of E1 to E5", url)


def require_allowed(url: object) -> MatchedEndpoint:
    """Alias of :func:`match_endpoint`, named for the call site that is enforcing the list."""
    return match_endpoint(url)


def is_allowed(url: object) -> bool:
    try:
        match_endpoint(url)
    except EndpointNotAllowed:
        return False
    return True


# ----------------------------------------------------------------------------------- season


def current_season_code(now: datetime) -> str:
    """The season in progress at ``now``: named for the year it starts in, from July."""
    year = now.year if now.month >= 7 else now.year - 1
    return f"E{year:04d}"
