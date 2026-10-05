"""The ingest client can reach only the endpoints it was built for, and never the odds feed.

Before the allowlist, ``StatsClient.call`` handed whatever endpoint name it was given straight to
``importlib``, so any module under ``nba_api.stats.endpoints`` was one typo from being fetched.
These tests make "this application only reads the endpoints it has a method for" something that
fails loudly when it stops being true, in both directions: a new method with no allowlist entry
cannot call anything, and an allowlist entry with no method behind it cannot linger.

The odds feed (``odds_todaysGames.json``) gets stronger treatment than "not on the list": Hardwood
has no betting machinery, so the refusal is by *name* as well as by omission, and it holds even if
somebody adds the pair to the allowlist by mistake. A scan of the whole ingest package's string
constants checks that nothing else spells the feed's address.

The tail of the file covers what rides on the same module: the roster endpoint's recorded payload
and parameters, and the scoreboard memo the schedule-detail writer reads instead of spending a
second request. Everything is local: a socket opened anywhere here is an immediate failure.
"""
from __future__ import annotations

import ast
import inspect
import socket
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from nbastats.ingest import client as client_module
from nbastats.ingest.client import (
    ENDPOINT_ALLOWLIST,
    FORBIDDEN_ENDPOINT_FRAGMENTS,
    EndpointNotAllowed,
    FixtureMissing,
    IngestError,
    RateLimiter,
    StatsClient,
    check_endpoint_allowed,
    polite_get,
)

FIXTURES = Path(__file__).parent / "fixtures" / "nba_api"
INGEST_PACKAGE = Path(client_module.__file__).parent

#: Invented ids of real franchises (a team id is a fact); the roster fixtures use these.
LAL = 1610612747
BOS = 1610612738

#: How to call every endpoint method, with the pair it must reach. The table is checked against
#: the class itself, so a new public method cannot be added without a line here.
ENDPOINT_METHODS: dict[str, tuple[tuple[Any, ...], dict[str, Any], tuple[str, str]]] = {
    "scoreboard": ((date(2026, 1, 2),), {}, ("scoreboardv2", "ScoreboardV2")),
    "league_game_log": (("2025-26",), {}, ("leaguegamelog", "LeagueGameLog")),
    "player_game_logs": (("2025-26",), {}, ("playergamelogs", "PlayerGameLogs")),
    "box_score_traditional": (
        ("0022500512",), {}, ("boxscoretraditionalv3", "BoxScoreTraditionalV3"),
    ),
    "box_score_advanced": (
        ("0022500512",), {}, ("boxscoreadvancedv3", "BoxScoreAdvancedV3"),
    ),
    "league_dash_player_stats": (
        ("2025-26",), {}, ("leaguedashplayerstats", "LeagueDashPlayerStats"),
    ),
    "common_all_players": (("2025-26",), {}, ("commonallplayers", "CommonAllPlayers")),
    "common_team_roster": ((LAL, "2025-26"), {}, ("commonteamroster", "CommonTeamRoster")),
}

#: Public members of ``StatsClient`` that are not endpoints.
NOT_ENDPOINTS = frozenset({"call", "describe", "fixture_mode", "last_scoreboard"})


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the allowlist tests must never open a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    # "Live mode" below means a client with no fixtures directory; an exported variable must
    # not quietly turn it into fixture mode.
    monkeypatch.delenv(client_module.FIXTURES_ENV_VAR, raising=False)


def _client(**kwargs: Any) -> StatsClient:
    return StatsClient(fixtures_dir=FIXTURES, min_delay=0.0, **kwargs)


# ------------------------------------------------------------------ the allowlist itself


def test_every_public_endpoint_method_is_in_the_table_below() -> None:
    """A new method must be added to ``ENDPOINT_METHODS`` (and so reviewed) to pass."""
    public = {
        name
        for name, member in inspect.getmembers(StatsClient)
        if not name.startswith("_") and (callable(member) or isinstance(member, property))
    }
    assert public - NOT_ENDPOINTS == set(ENDPOINT_METHODS)


def test_the_allowlist_is_exactly_what_the_methods_reach(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each method reaches its own allowlisted pair, and no pair is left without a method."""
    reached: dict[str, tuple[str, str]] = {}
    current: list[str] = []

    def spy(self: StatsClient, endpoint: str, class_name: str, *args: Any, **kwargs: Any) -> dict:
        reached[current[0]] = (endpoint, class_name)
        return {}

    monkeypatch.setattr(StatsClient, "call", spy)
    client = _client()
    for name, (args, kwargs, expected) in ENDPOINT_METHODS.items():
        current[:] = [name]
        getattr(client, name)(*args, **kwargs)
        assert reached[name] == expected, name
        assert expected in ENDPOINT_ALLOWLIST, name
    assert set(reached.values()) == set(ENDPOINT_ALLOWLIST), "an allowlist entry has no method"


def test_the_roster_endpoint_is_on_the_list_by_name() -> None:
    assert ("commonteamroster", "CommonTeamRoster") in ENDPOINT_ALLOWLIST


# ------------------------------------------------------------------ refusal comes first


@pytest.mark.parametrize("live", [False, True], ids=["fixture-mode", "live-mode"])
@pytest.mark.parametrize(
    ("endpoint", "class_name"),
    [
        ("playerindex", "PlayerIndex"),  # a real nba_api endpoint, simply not ours
        ("scoreboardv2", "ScoreboardV3"),  # right module, wrong class
        ("ScoreboardV2", "ScoreboardV2"),  # the pair is exact, not case-folded
        ("", ""),
    ],
)
def test_an_unlisted_endpoint_is_refused_before_anything_happens(
    monkeypatch: pytest.MonkeyPatch, live: bool, endpoint: str, class_name: str
) -> None:
    """No import, no fixture read, no request: the refusal is the first thing ``call`` does."""
    touched: list[str] = []
    monkeypatch.setattr(
        StatsClient, "_endpoint_class", lambda *a, **k: touched.append("import") or None
    )
    monkeypatch.setattr(StatsClient, "_fixture", lambda *a, **k: touched.append("fixture") or {})
    client = StatsClient(min_delay=0.0) if live else _client()
    with pytest.raises(EndpointNotAllowed):
        client.call(endpoint, class_name)
    assert touched == []
    assert client.stats.calls == 0


def test_a_refusal_is_an_ingest_error_and_is_never_retried() -> None:
    assert issubclass(EndpointNotAllowed, IngestError)
    attempts: list[int] = []

    def refused() -> None:
        attempts.append(1)
        check_endpoint_allowed("playerindex", "PlayerIndex")

    limiter = RateLimiter(0.0)
    with pytest.raises(EndpointNotAllowed):
        polite_get(refused, retries=5, limiter=limiter)
    assert attempts == [1]
    assert limiter.stats.retries == 0 and limiter.stats.failures == 0


# ------------------------------------------------------------------ the odds feed


@pytest.mark.parametrize("live", [False, True], ids=["fixture-mode", "live-mode"])
@pytest.mark.parametrize(
    ("endpoint", "class_name"),
    [
        ("odds", "Odds"),
        ("odds_todaysGames", "OddsTodaysGames"),
        ("ODDS", "ODDS"),
        ("scoreboardv2", "OddsOverlay"),  # an allowlisted module with an odds-ish class
        ("liveData/odds/odds_todaysGames.json", "Odds"),
    ],
)
def test_the_odds_feed_cannot_be_reached_by_any_spelling(
    monkeypatch: pytest.MonkeyPatch, live: bool, endpoint: str, class_name: str
) -> None:
    monkeypatch.setattr(StatsClient, "_endpoint_class", lambda *a, **k: pytest.fail("imported"))
    monkeypatch.setattr(StatsClient, "_fixture", lambda *a, **k: pytest.fail("read a fixture"))
    client = StatsClient(min_delay=0.0) if live else _client()
    with pytest.raises(EndpointNotAllowed, match="betting machinery"):
        client.call(endpoint, class_name, fixture_parts=("odds_todaysGames",))


def test_the_odds_refusal_does_not_depend_on_the_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adding the pair to the list by mistake must not open the door."""
    monkeypatch.setattr(
        client_module, "ENDPOINT_ALLOWLIST", ENDPOINT_ALLOWLIST | {("odds", "Odds")}
    )
    with pytest.raises(EndpointNotAllowed, match="betting machinery"):
        check_endpoint_allowed("odds", "Odds")
    with pytest.raises(EndpointNotAllowed, match="betting machinery"):
        _client().call("odds", "Odds")


def test_nothing_the_client_offers_is_named_for_the_odds_feed() -> None:
    fragments = FORBIDDEN_ENDPOINT_FRAGMENTS
    assert fragments, "the forbidden fragments must not be emptied"
    for name, _member in inspect.getmembers(StatsClient):
        assert not any(fragment in name.lower() for fragment in fragments), name
    for endpoint, class_name in ENDPOINT_ALLOWLIST:
        assert not any(
            fragment in f"{endpoint}/{class_name}".lower() for fragment in fragments
        ), (endpoint, class_name)


def _string_constants_outside_docstrings(path: Path) -> list[tuple[int, str]]:
    """Every string literal in a module that is not a docstring, with its line number."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                found.append((node.lineno, node.value))
    return found


def test_no_ingest_module_spells_the_odds_feed_address() -> None:
    """The feed's file name and path appear nowhere in code, only (if at all) in prose."""
    needles = ("odds_todaysgames", "livedata/odds", "liveData/odds".lower())
    offences = [
        f"{path.name}:{line}: {value!r}"
        for path in sorted(INGEST_PACKAGE.glob("*.py"))
        for line, value in _string_constants_outside_docstrings(path)
        if any(needle in value.lower() for needle in needles)
    ]
    assert offences == []


def test_the_forbidden_fragment_is_defined_once_and_nowhere_else_in_code() -> None:
    """Only ``client.py``'s own definition and its refusal message carry the fragment."""
    carriers = {
        path.name
        for path in sorted(INGEST_PACKAGE.glob("*.py"))
        for _line, value in _string_constants_outside_docstrings(path)
        if "odds" in value.lower()
    }
    assert carriers <= {"client.py"}


# ------------------------------------------------------------------ the roster endpoint


def test_the_roster_endpoint_reads_its_recorded_payload() -> None:
    client = _client()
    payload = client.common_team_roster(LAL, "2025-26")
    names = [item["name"] for item in payload["resultSets"]]
    assert names == ["CommonTeamRoster", "Coaches"]
    assert client.stats.by_endpoint == {"commonteamroster": 1}
    assert client.stats.fixture_calls == 1 and client.stats.live_calls == 0


def test_the_roster_fixture_name_is_endpoint_season_team() -> None:
    assert client_module.fixture_name("commonteamroster", ("2025-26", LAL)) == (
        f"commonteamroster__2025-26__{LAL}.json"
    )
    assert (FIXTURES / f"commonteamroster__2025-26__{LAL}.json").is_file()


def test_a_team_with_no_recorded_roster_is_a_missing_fixture_not_a_fallback() -> None:
    with pytest.raises(FixtureMissing):
        _client().common_team_roster(1610612760, "2025-26")


def test_the_roster_call_passes_team_and_season_to_nba_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    class FakeRoster:
        def __init__(self, **kwargs: Any) -> None:
            seen.update(kwargs)

        def get_dict(self) -> dict[str, Any]:
            return {"resultSets": []}

    requested: list[tuple[str, str]] = []

    def fake_class(self: StatsClient, module_name: str, class_name: str) -> Any:
        requested.append((module_name, class_name))
        return FakeRoster

    monkeypatch.setattr(StatsClient, "_endpoint_class", fake_class)
    client = StatsClient(min_delay=0.0, proxy="http://proxy.invalid:8080")
    assert client.common_team_roster(BOS, "2025-26") == {"resultSets": []}
    assert requested == [("commonteamroster", "CommonTeamRoster")]
    assert seen["team_id"] == BOS and seen["season"] == "2025-26"
    assert seen["proxy"] == "http://proxy.invalid:8080"
    assert seen["headers"]["Referer"] == "https://www.nba.com/", "browser headers are mandatory"
    assert client.stats.live_calls == 1


# ------------------------------------------------------------------ the scoreboard memo


def test_the_scoreboard_response_is_remembered_without_another_request() -> None:
    client = _client()
    day = date(2026, 1, 2)
    assert client.last_scoreboard(day) is None, "nothing fetched yet"
    payload = client.scoreboard(day)
    assert client.last_scoreboard(day) is payload
    assert client.last_scoreboard(date(2026, 1, 3)) is None
    assert client.stats.by_endpoint == {"scoreboardv2": 1}, "remembering must not re-request"


def test_the_memo_keeps_only_the_newest_few_dates() -> None:
    client = _client()
    days = [date(2026, 2, index) for index in range(1, 8)]
    for day in days:  # the corpus falls back to its endpoint-wide (empty) slate for these dates
        client.scoreboard(day)
    assert [day for day in days if client.last_scoreboard(day) is not None] == days[-4:]


def test_refetching_a_date_makes_it_the_newest_again() -> None:
    client = _client()
    for index in range(1, 5):
        client.scoreboard(date(2026, 3, index))
    client.scoreboard(date(2026, 3, 1))  # the oldest, now the newest
    client.scoreboard(date(2026, 3, 5))  # evicts the oldest remaining: the 2nd
    assert client.last_scoreboard(date(2026, 3, 1)) is not None
    assert client.last_scoreboard(date(2026, 3, 2)) is None
    assert [d.day for d in (date(2026, 3, n) for n in (3, 4, 5))
            if client.last_scoreboard(d) is not None] == [3, 4, 5]
