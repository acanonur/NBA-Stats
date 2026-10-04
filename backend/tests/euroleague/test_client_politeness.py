"""The EuroLeague client: five URLs, a polite way of asking, and nothing else.

Nothing here touches a network. Responses come from ``httpx.MockTransport`` and time comes from a
fake clock whose ``sleep`` only moves the clock, so a test of "one second between requests" takes
no seconds and a test of "wait out a ten-minute ``Retry-After``" takes none either.

What is pinned, because each is a promise the design makes to the host and to the person who owns
the Mac:

* only the five allowlisted URLs can be requested, and any other (a look-alike, a redirect target,
  the legacy host) is refused before a socket is considered;
* the User-Agent names Hardwood and says "private, personal, non-commercial", and cannot be
  replaced;
* a second between requests, at most two in flight, exponential backoff, ``Retry-After`` honoured,
  conditional requests, and a six-hour breaker on a 401, a 403, a Cloudflare 1015 or three 429s;
* a run has a request budget, and a block survives a restart (``restore_breaker``);
* raw bodies are gzipped, content-addressed, owner-only and confined to their folder;
* no module of the ingest package opens a socket any way but the polite client, and none mentions a
  host other than the one allowlisted.
"""

from __future__ import annotations

import ast
import gzip
import inspect
import os
import re
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from nbastats.euroleague.ingest import client as client_module
from nbastats.euroleague.ingest import endpoints
from nbastats.euroleague.ingest.client import (
    BREAKER_KEY,
    BudgetExhausted,
    EuroLeagueClient,
    RawPayloadStore,
    classify_failure,
)
from nbastats.euroleague.ingest.endpoints import EndpointNotAllowed
from nbastats.intel import http as intel_http
from nbastats.intel.http import (
    CircuitOpenError,
    PoliteClient,
    RefusedError,
    TooLargeError,
    TransportFailure,
)

UTC = timezone.utc
INGEST = Path(__file__).resolve().parents[2] / "nbastats" / "euroleague" / "ingest"
BASE = "https://api-live.euroleague.net/v2/competitions/E/seasons/E2031"


# ------------------------------------------------------------------------------ a fake net


class Net:
    """A scripted service and a clock that only moves when somebody sleeps."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.sleeps: list[float] = []
        self.monotonic = 0.0
        self.now = datetime(2031, 10, 4, 9, 0, tzinfo=UTC)
        self.script: list[Any] = []
        self.routes: dict[str, httpx.Response] = {}

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.monotonic += seconds
        self.now += timedelta(seconds=seconds)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return self.routes.get(str(request.url)) or httpx.Response(404, text="not found")

    def polite(self, **options: Any) -> PoliteClient:
        return PoliteClient(
            transport=httpx.MockTransport(self.handler),
            clock=lambda: self.monotonic,
            wall_clock=lambda: self.now,
            sleep=self.sleep,
            jitter=lambda: 0.0,
            **options,
        )

    def client(self, *, budget: int | None = 40, **options: Any) -> EuroLeagueClient:
        return EuroLeagueClient(self.polite(**options), max_requests=budget)


def ok(body: bytes = b"[]", **headers: str) -> httpx.Response:
    return httpx.Response(
        200, content=body, headers={"content-type": "application/json", **headers}
    )


@pytest.fixture()
def net() -> Net:
    return Net()


# ---------------------------------------------------------------------------- endpoints


def test_the_builders_make_exactly_the_five_documented_urls() -> None:
    assert endpoints.rounds_url("E2031") == f"{BASE}/rounds"
    assert endpoints.games_url("E2031", 3) == f"{BASE}/games?roundNumber=3"
    assert endpoints.game_stats_url("E2031", 12) == f"{BASE}/games/12/stats"
    assert endpoints.clubs_url("E2031") == f"{BASE}/clubs"
    assert endpoints.club_people_url("E2031", "ZZA") == f"{BASE}/clubs/ZZA/people"


def test_each_url_is_recognised_as_its_endpoint_with_its_parameters() -> None:
    cases = [
        (endpoints.rounds_url("E2031"), "E1", {}),
        (endpoints.games_url("E2031", 3), "E2", {"round_number": 3}),
        (endpoints.game_stats_url("E2031", 12), "E3", {"game_code": 12}),
        (endpoints.clubs_url("E2031"), "E4", {}),
        (endpoints.club_people_url("E2031", "ZZA"), "E5", {"club_code": "ZZA"}),
    ]
    for url, key, params in cases:
        matched = endpoints.match_endpoint(url)
        assert matched.key == key and matched.season == "E2031" and matched.url == url
        for name, value in params.items():
            assert getattr(matched, name) == value
    assert set(endpoints.ENDPOINTS) == {"E1", "E2", "E3", "E4", "E5"}


def test_no_endpoint_claims_to_have_been_seen_live() -> None:
    """Honesty: ``verified`` flips only when a recording made on the Mac has parsed."""
    assert not any(e.verified for e in endpoints.ENDPOINTS.values())


@pytest.mark.parametrize(
    "season", ["E26", "e2031", "U2031", "2031", "E20311", "E2031 ", "", None, 2031, "E2031/../x"]
)
def test_bad_season_codes_are_refused(season: Any) -> None:
    with pytest.raises(EndpointNotAllowed):
        endpoints.validate_season_code(season)


@pytest.mark.parametrize("code", [0, -1, 10000, True, "12", 1.5, None])
def test_bad_game_codes_are_refused(code: Any) -> None:
    with pytest.raises(EndpointNotAllowed):
        endpoints.validate_game_code(code)


@pytest.mark.parametrize("number", [0, -1, 61, True, "3", None])
def test_bad_round_numbers_are_refused(number: Any) -> None:
    with pytest.raises(EndpointNotAllowed):
        endpoints.validate_round_number(number)


@pytest.mark.parametrize("club", ["zza", "Z", "ZZZZZ", "ZZ A", "ZZ/", "", None, 5, "ZZA?x=1"])
def test_bad_club_codes_are_refused(club: Any) -> None:
    with pytest.raises(EndpointNotAllowed):
        endpoints.validate_club_code(club)


@pytest.mark.parametrize(
    "url",
    [
        f"{BASE}/rounds".replace("https", "http"),  # not https
        "https://live.euroleague.net/api/Boxscore?gamecode=1&seasoncode=E2031",  # the legacy host
        "https://api-live.euroleague.net/v1/results/E2031",  # v1
        "https://api-live.euroleague.net/v3/competitions/E/seasons/E2031/games/1/stats",  # v3
        f"{BASE}/rounds?x=1",  # an extra query
        f"{BASE}/games",  # E2 needs its round
        f"{BASE}/games?roundNumber=3&extra=1",
        f"{BASE}/games?roundNumber=abc",
        f"{BASE}/games?roundNumber=0",
        f"{BASE}/games?roundNumber=3#frag",
        f"{BASE}/games?roundNumber",
        f"{BASE}/games/12/stats?x=1",
        f"{BASE}/rounds/",  # trailing slash
        f"{BASE}/standings",  # an endpoint nobody budgeted
        f"{BASE}/clubs/zza/people",
        f"{BASE}/clubs/ZZA/people/extra",
        f"{BASE}/games/0/stats",
        f"{BASE}/games/10000/stats",
        "https://api-live.euroleague.net:8443/v2/competitions/E/seasons/E2031/rounds",
        "https://user:pw@api-live.euroleague.net/v2/competitions/E/seasons/E2031/rounds",
        "https://api-live.euroleague.net.evil.test/v2/competitions/E/seasons/E2031/rounds",
        "https://evil.test/https://api-live.euroleague.net/v2/competitions/E/seasons/E2031/rounds",
        "https://api-live.euroleague.net/v2/competitions/U/seasons/E2031/rounds",  # EuroCup
        "https://api-live.euroleague.net/v2/competitions/E/seasons/U2031/rounds",
        "https://api-live.euroleague.net/v2/competitions/E/seasons/E2031/../E2030/rounds",
        f"{BASE}/rounds\n",
        f"{BASE}/ rounds",
        "file:///etc/passwd",
        "",
    ],
)
def test_every_look_alike_is_refused(url: str) -> None:
    assert not endpoints.is_allowed(url)
    with pytest.raises(EndpointNotAllowed):
        endpoints.require_allowed(url)


def test_a_non_string_is_refused() -> None:
    for value in (None, 5, b"https://api-live.euroleague.net/", ["x"]):
        with pytest.raises(EndpointNotAllowed):
            endpoints.match_endpoint(value)


@pytest.mark.parametrize(
    ("moment", "season"),
    [
        (datetime(2026, 10, 4), "E2026"),
        (datetime(2027, 5, 31), "E2026"),
        (datetime(2027, 6, 30, 23, 59), "E2026"),
        (datetime(2027, 7, 1), "E2027"),
        (datetime(2027, 1, 1), "E2026"),
    ],
)
def test_the_season_is_named_for_the_year_it_starts_in_from_july(
    moment: datetime, season: str
) -> None:
    assert endpoints.current_season_code(moment) == season


# ----------------------------------------------------------------------------- the client


def test_an_unlisted_url_is_refused_before_any_request(net: Net) -> None:
    client = net.client()
    with pytest.raises(EndpointNotAllowed):
        client.get("https://live.euroleague.net/api/Boxscore?gamecode=1")
    with pytest.raises(EndpointNotAllowed):
        client.get("https://example.org/")
    assert net.calls == [] and client.requests_made == 0


def test_the_five_typed_methods_request_exactly_their_urls(net: Net) -> None:
    client = net.client()
    client.rounds("E2031")
    client.games("E2031", 2)
    client.box_score("E2031", 7)
    client.clubs("E2031")
    client.people("E2031", "ZZB")
    assert [str(r.url) for r in net.calls] == [
        f"{BASE}/rounds",
        f"{BASE}/games?roundNumber=2",
        f"{BASE}/games/7/stats",
        f"{BASE}/clubs",
        f"{BASE}/clubs/ZZB/people",
    ]
    assert client.requests_made == 5


def test_the_user_agent_names_hardwood_and_says_what_the_traffic_is(net: Net) -> None:
    net.routes[f"{BASE}/rounds"] = ok()
    net.client().rounds("E2031")
    agent = net.calls[0].headers["user-agent"]
    assert agent == intel_http.USER_AGENT
    assert agent.startswith("Hardwood/")
    assert "private single-user analytics" in agent and "personal, non-commercial" in agent
    assert net.calls[0].headers["accept"] == "application/json"


def test_a_caller_cannot_choose_the_user_agent_or_the_headers() -> None:
    names = set(inspect.signature(EuroLeagueClient.get).parameters)
    assert names == {"self", "url", "validators"}


def test_a_second_passes_between_request_starts(net: Net) -> None:
    client = net.client()
    for _ in range(3):
        client.rounds("E2031")
    assert net.sleeps == [1.0, 1.0]


def test_at_most_two_requests_may_be_in_flight_and_the_floor_is_a_second(net: Net) -> None:
    polite = client_module.EuroLeagueClient(net.polite())._polite
    assert polite.max_in_flight == 2 == intel_http.MAX_IN_FLIGHT
    assert polite.min_interval == 1.0 == intel_http.MIN_INTERVAL_SECONDS
    assert polite.max_attempts == 3 and polite.timeout == 20.0


def test_a_429_with_retry_after_is_waited_out_then_retried(net: Net) -> None:
    net.script = [httpx.Response(429, headers={"retry-after": "30"}), ok(b'{"data": []}')]
    fetched = net.client().rounds("E2031")
    assert fetched.status == 200 and fetched.attempts == 2
    assert 30.0 in net.sleeps


def test_retry_after_is_capped_at_ten_minutes(net: Net) -> None:
    net.script = [httpx.Response(429, headers={"retry-after": "86400"}), ok()]
    net.client().rounds("E2031")
    assert 600.0 in net.sleeps and 86400.0 not in net.sleeps


def test_a_5xx_is_retried_with_exponential_backoff_then_returned_as_itself(net: Net) -> None:
    net.script = [httpx.Response(503)] * 3
    fetched = net.client().rounds("E2031")
    assert fetched.status == 503 and fetched.attempts == 3 and not fetched.ok
    waits = [s for s in net.sleeps if s != 1.0]
    assert waits == [2.0, 4.0]  # base 2 s, doubled, no jitter in the test


def test_a_transport_error_is_retried_then_raised(net: Net) -> None:
    net.script = [httpx.ConnectError("no route")] * 3
    client = net.client()
    with pytest.raises(TransportFailure) as caught:
        client.rounds("E2031")
    assert caught.value.attempts == 3
    failure = classify_failure(caught.value)
    assert failure.state == "error" and "no response from the service" in failure.reason
    assert not failure.stop and failure.paused_until is None


def test_three_429s_in_a_row_open_the_breaker_for_six_hours(net: Net) -> None:
    net.script = [httpx.Response(429)] * 3
    client = net.client()
    with pytest.raises(CircuitOpenError) as caught:
        client.rounds("E2031")
    assert "3 429 responses in a row" in caught.value.reason
    until = client.breaker().paused_until
    assert until is not None and until - net.now == timedelta(hours=6)
    before = len(net.calls)
    with pytest.raises(CircuitOpenError):
        client.clubs("E2031")
    assert len(net.calls) == before  # no request left the machine


@pytest.mark.parametrize(
    ("status", "label"), [(401, "unauthorized (401)"), (403, "forbidden (403)")]
)
def test_a_401_or_403_opens_the_breaker_at_once(net: Net, status: int, label: str) -> None:
    net.script = [httpx.Response(status)]
    client = net.client()
    with pytest.raises(CircuitOpenError) as caught:
        client.box_score("E2031", 1)
    assert caught.value.reason == label and caught.value.status == status
    failure = classify_failure(caught.value)
    assert failure.state == "blocked" and failure.stop
    assert failure.paused_until == client.breaker().paused_until


def test_cloudflare_error_1015_opens_the_breaker(net: Net) -> None:
    page = b"<html><title>Error 1015</title>You are being rate limited. Cloudflare</html>"
    net.script = [httpx.Response(429, content=page, headers={"server": "cloudflare"})]
    client = net.client()
    with pytest.raises(CircuitOpenError) as caught:
        client.rounds("E2031")
    assert "Cloudflare 1015" in caught.value.reason
    assert client.breaker().reason == "Cloudflare 1015 rate limit"


def test_a_persisted_block_is_honoured_after_a_restart(net: Net) -> None:
    client = net.client()
    client.restore_breaker(net.now + timedelta(hours=2), "forbidden (403)")
    with pytest.raises(CircuitOpenError) as caught:
        client.rounds("E2031")
    assert "forbidden (403)" in caught.value.reason and net.calls == []
    expired = net.client()
    expired.restore_breaker(net.now - timedelta(minutes=1), "forbidden (403)")
    assert expired.rounds("E2031").status == 404  # the pause has run out: the request is made
    assert len(net.calls) == 1


def test_one_breaker_covers_every_endpoint(net: Net) -> None:
    net.script = [httpx.Response(403)]
    client = net.client()
    with pytest.raises(CircuitOpenError):
        client.rounds("E2031")
    for call in (
        lambda: client.games("E2031", 1),
        lambda: client.people("E2031", "ZZA"),
        lambda: client.box_score("E2031", 1),
    ):
        with pytest.raises(CircuitOpenError):
            call()
    assert len(net.calls) == 1 and client._polite.breaker_state(BREAKER_KEY).paused_until


def test_the_breaker_can_be_reset_by_a_person(net: Net) -> None:
    client = net.client()
    client.restore_breaker(net.now + timedelta(hours=2), "x")
    client.reset_breaker()
    assert client.breaker().paused_until is None


def test_validators_make_the_request_conditional_and_a_304_is_an_answer(net: Net) -> None:
    net.routes[f"{BASE}/rounds"] = ok(
        b'{"data": []}', etag='"v1"', **{"last-modified": "Sat, 04 Oct 2031 08:00:00 GMT"}
    )
    client = net.client()
    first = client.rounds("E2031")
    assert first.etag == '"v1"' and first.validators() is not None
    net.routes[f"{BASE}/rounds"] = httpx.Response(304)
    second = client.rounds("E2031", validators=first.validators())
    sent = net.calls[1].headers
    assert sent["if-none-match"] == '"v1"'
    assert sent["if-modified-since"] == "Sat, 04 Oct 2031 08:00:00 GMT"
    assert second.not_modified and second.body == b"" and not second.ok


def test_a_response_with_no_validators_offers_none(net: Net) -> None:
    net.routes[f"{BASE}/rounds"] = ok()
    assert net.client().rounds("E2031").validators() is None


def test_a_404_is_returned_not_raised(net: Net) -> None:
    fetched = net.client().box_score("E2031", 9)
    assert fetched.status == 404 and fetched.missing and not fetched.ok
    assert fetched.endpoint_key == "E3"


def test_a_response_over_the_cap_is_refused_without_being_read(net: Net) -> None:
    net.routes[f"{BASE}/rounds"] = httpx.Response(
        200, content=b"x" * 10, headers={"content-length": str(client_module.MAX_BODY_BYTES + 1)}
    )
    with pytest.raises(TooLargeError) as caught:
        net.client().rounds("E2031")
    failure = classify_failure(caught.value)
    assert failure.state == "error" and "larger than" in failure.reason and not failure.stop


def test_a_redirect_off_the_allowlist_is_refused_but_one_within_it_is_followed(net: Net) -> None:
    net.script = [httpx.Response(302, headers={"location": "https://example.org/elsewhere"})]
    with pytest.raises(RefusedError):
        net.client().rounds("E2031")
    assert len(net.calls) == 1  # the second hop was never made
    net.calls.clear()
    net.script = [
        httpx.Response(301, headers={"location": f"{BASE}/clubs"}),
        ok(b'{"data": []}'),
    ]
    fetched = net.client().rounds("E2031")
    assert fetched.ok and [str(r.url) for r in net.calls] == [f"{BASE}/rounds", f"{BASE}/clubs"]


def test_a_run_has_a_request_budget(net: Net) -> None:
    client = net.client(budget=2)
    assert client.remaining == 2
    client.rounds("E2031")
    client.clubs("E2031")
    assert client.remaining == 0
    with pytest.raises(BudgetExhausted) as caught:
        client.people("E2031", "ZZA")
    assert len(net.calls) == 2
    failure = classify_failure(caught.value)
    assert failure.state == "error" and failure.stop
    assert net.client(budget=None).remaining is None


def test_a_polite_client_and_options_for_one_are_mutually_exclusive(net: Net) -> None:
    with pytest.raises(ValueError):
        EuroLeagueClient(net.polite(), stop_requested=lambda: False)
    with pytest.raises(ValueError):
        EuroLeagueClient(net.polite(), min_interval=0.0)


def test_a_stop_request_interrupts_the_wait_between_requests() -> None:
    stop = {"now": False}
    client = EuroLeagueClient(
        transport=httpx.MockTransport(lambda r: ok()),
        stop_requested=lambda: stop["now"],
        min_interval=30.0,
    )
    client.rounds("E2031")
    stop["now"] = True
    with pytest.raises(intel_http.StopRequested):
        client.clubs("E2031")
    assert classify_failure(intel_http.StopRequested("x")).stop


def test_classify_failure_covers_every_way_a_request_can_fail() -> None:
    until = datetime(2031, 10, 4, 15, tzinfo=UTC)
    cases: list[tuple[BaseException, str, bool]] = [
        (CircuitOpenError("h", until, "forbidden (403)", status=403), "blocked", True),
        (EndpointNotAllowed("no"), "error", True),
        (RefusedError("because"), "error", True),
        (TooLargeError(10), "error", False),
        (TransportFailure(OSError("x"), 3), "error", False),
        (intel_http.FetchError("odd"), "error", False),
        (RuntimeError("boom"), "error", False),
    ]
    for exc, state, stop in cases:
        failure = classify_failure(exc)
        assert (failure.state, failure.stop) == (state, stop), exc
        assert failure.reason


# --------------------------------------------------------------------------- raw bodies


def fetched_200(net: Net, body: bytes = b'{"data": [1]}') -> client_module.Fetched:
    net.routes[f"{BASE}/rounds"] = ok(body)
    return net.client().rounds("E2031")


def test_a_raw_body_is_gzipped_content_addressed_and_owner_only(net: Net, tmp_path: Path) -> None:
    store = RawPayloadStore(tmp_path / "el-raw")
    fetched = fetched_200(net)
    ref = store.save(fetched)
    assert ref is not None and ref.created and ref.bytes == len(fetched.body)
    assert ref.path == tmp_path / "el-raw" / "E1" / ref.sha256[:2] / f"{ref.sha256}.json.gz"
    assert gzip.decompress(ref.path.read_bytes()) == fetched.body
    assert stat.S_IMODE(os.stat(ref.path).st_mode) == 0o600
    assert store.read(ref.path) == fetched.body


def test_saving_the_same_body_twice_stores_it_once_and_reproducibly(
    net: Net, tmp_path: Path
) -> None:
    store = RawPayloadStore(tmp_path)
    fetched = fetched_200(net)
    first = store.save(fetched)
    bytes_before = first.path.read_bytes()
    second = store.save(fetched)
    assert second is not None and not second.created and second.path == first.path
    assert first.path.read_bytes() == bytes_before
    assert len([p for p in tmp_path.rglob("*") if p.is_file()]) == 1  # no stray temporary file


def test_only_a_200_body_is_kept(net: Net, tmp_path: Path) -> None:
    store = RawPayloadStore(tmp_path)
    missing = net.client().box_score("E2031", 1)
    assert store.save(missing) is None
    net.routes[f"{BASE}/rounds"] = httpx.Response(304)
    assert store.save(net.client().rounds("E2031")) is None
    assert not list(tmp_path.rglob("*.gz"))


def test_the_store_only_deletes_and_reads_inside_its_own_folder(net: Net, tmp_path: Path) -> None:
    store = RawPayloadStore(tmp_path / "el-raw")
    ref = store.save(fetched_200(net))
    outside = tmp_path / "keep.txt"
    outside.write_text("precious")
    assert store.delete(outside) is False and outside.exists()
    assert store.delete(tmp_path / "el-raw" / ".." / "keep.txt") is False
    with pytest.raises(ValueError):
        store.read(outside)
    assert store.delete(ref.path) is True and not ref.path.exists()
    assert store.delete(ref.path) is False  # already gone


# ------------------------------------------------------------ structural: nobody else asks


def _modules() -> list[Path]:
    return sorted(p for p in INGEST.glob("*.py"))


def _imports(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def test_no_ingest_module_opens_a_socket_except_through_the_polite_client() -> None:
    forbidden = {"httpx", "requests", "urllib3", "aiohttp", "socket", "http", "ftplib", "smtplib"}
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not (_imports(tree) & forbidden), f"{path.name} imports {_imports(tree) & forbidden}"
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                assert node.module != "urllib.request", path.name
            if isinstance(node, ast.Import):
                assert all(a.name != "urllib.request" for a in node.names), path.name


def test_the_only_http_host_literal_in_the_ingest_code_is_the_allowlisted_one() -> None:
    pattern = re.compile(r"https?://([A-Za-z0-9.\-]+)")
    allowed = {"api-live.euroleague.net"}
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            id(n.body[0].value)
            for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))
            and n.body
            and isinstance(n.body[0], ast.Expr)
            and isinstance(getattr(n.body[0], "value", None), ast.Constant)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ):
                for host in pattern.findall(node.value):
                    assert host in allowed, f"{path.name} has a literal URL on {host}"


def test_requests_go_through_the_polite_client_everywhere() -> None:
    for name in ("client.py", "jobs.py"):
        text = (INGEST / name).read_text(encoding="utf-8")
        assert "PoliteClient" in text, name
    assert "from ...intel.http import" in (INGEST / "client.py").read_text(encoding="utf-8")


_BANNED = (
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


def test_the_package_reads_clean_under_the_prose_guard() -> None:
    """The extended prose guard scans ``nbastats/euroleague``; this is the same list, here, so the
    failure is found by the package's own tests and not by the release check."""
    pattern = re.compile(r"\b(?:" + "|".join(_BANNED) + r")\b")
    for path in _modules():
        text = path.read_text(encoding="utf-8").lower()
        assert not pattern.findall(text), (path.name, sorted(set(pattern.findall(text))))


def test_there_is_no_terms_gate_anywhere_in_the_package() -> None:
    for path in _modules():
        text = path.read_text(encoding="utf-8")
        assert "TERMS_REVIEWED" not in text and "TERMS_OUTCOME" not in text, path.name
        assert "termsNotReviewed" not in text, path.name


def test_no_probability_of_winning_and_no_market_words_in_the_code_identifiers() -> None:
    words = {"win_prob", "winprob", "p_over", "implied", "edge", "lean", "spread", "pick"}
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
            n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
        }
        for name in names:
            parts = set(name.lower().split("_"))
            assert not (parts & words), f"{path.name} uses the name {name}"


def test_the_client_module_exposes_its_contract() -> None:
    assert client_module.SOURCE_KEY == client_module.BREAKER_KEY == "el.dataService"
    assert client_module.DEFAULT_REQUEST_BUDGET >= 21  # a twenty-club roster sweep must fit
    assert callable(client_module.el_raw_dir)
    assert client_module.el_raw_dir("/x") == Path("/x/el-raw")
