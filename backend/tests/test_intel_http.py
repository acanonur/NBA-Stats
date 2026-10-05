"""The polite HTTP client's policy, proved against a mock transport and fake clocks.

``nbastats/intel/http.py`` is the only place the new sources touch the network, so every rule it
promises is checked here, with no socket and no real second passing:

* the User-Agent names Hardwood and states personal use, and a caller cannot replace it;
* a one-second floor between request starts (across hosts), at most two requests in flight;
* conditional requests with ``ETag``/``Last-Modified``, and a 304 is an answer, not an error;
* up to three attempts on 429, 5xx and transport errors, honouring ``Retry-After`` (seconds or an
  HTTP date) capped at 600 seconds, otherwise exponential backoff with jitter;
* the circuit breaker: 401, 403, a Cloudflare 1015, or three 429s in a row open it for six hours;
  an open breaker makes no request; it can be persisted and restored;
* the size cap applies to decoded bytes (a gzip bomb), an error page never raises "too large",
  redirects are followed by hand and every hop passes the same gate (scheme, downgrade, ``allow``
  hook, breaker), and a hostile URL is refused before anything is sent.

Everything is invented; ``socket.socket`` is replaced so an accidental network call fails loudly.
"""

from __future__ import annotations

import gzip
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx
import pytest

from nbastats.intel import http as intel_http
from nbastats.intel.http import (
    CircuitOpenError,
    Conditional,
    PoliteClient,
    RefusedError,
    StopRequested,
    TooLargeError,
    TransportFailure,
)

START = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("these tests must never open a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


class Clock:
    """A fake monotonic clock whose ``sleep`` advances it, and a wall clock that follows it."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds

    def wall(self) -> datetime:
        return START + timedelta(seconds=self.t - 1000.0)

    def advance(self, seconds: float) -> None:
        self.t += seconds


def make_client(
    handler: Callable[[httpx.Request], httpx.Response], clock: Clock | None = None, **kwargs: Any
) -> tuple[PoliteClient, Clock]:
    clock = clock or Clock()
    options: dict[str, Any] = {
        "transport": httpx.MockTransport(handler),
        "clock": clock.now,
        "sleep": clock.sleep,
        "wall_clock": clock.wall,
        "jitter": lambda: 0.5,
    }
    options.update(kwargs)
    return PoliteClient(**options), clock


def ok(body: bytes = b"hello", **headers: str) -> httpx.Response:
    return httpx.Response(200, content=body, headers=headers)


URL = "https://feeds.example.org/feed"


# ------------------------------------------------------------------------- identity


def test_user_agent_names_hardwood_and_states_personal_use() -> None:
    assert intel_http.USER_AGENT.startswith("Hardwood/")
    assert "personal" in intel_http.USER_AGENT
    assert "single-user" in intel_http.USER_AGENT


def test_the_user_agent_cannot_be_replaced_by_a_caller() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return ok()

    client, _ = make_client(handler)
    client.get(URL, headers={"User-Agent": "definitely-not-hardwood", "Accept": "text/plain"})
    assert seen == [intel_http.USER_AGENT]


def test_a_caller_can_set_accept() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["accept"])
        return ok()

    client, _ = make_client(handler)
    client.get(URL, headers={"Accept": "application/pdf"})
    assert seen == ["application/pdf"]


# -------------------------------------------------------------------------- floor


def test_requests_start_at_least_one_second_apart() -> None:
    clock = Clock()
    starts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        starts.append(clock.t)
        return ok()

    client, _ = make_client(handler, clock)
    for _ in range(4):
        client.get(URL)
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert len(starts) == 4
    assert all(gap >= 1.0 for gap in gaps), gaps


def test_the_floor_is_global_across_hosts() -> None:
    clock = Clock()
    starts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        starts.append(clock.t)
        return ok()

    client, _ = make_client(handler, clock)
    client.get("https://one.example.org/a")
    client.get("https://two.example.org/b")
    assert starts[1] - starts[0] >= 1.0


def test_no_wait_when_a_second_has_already_passed() -> None:
    clock = Clock()
    client, _ = make_client(lambda request: ok(), clock)
    client.get(URL)
    clock.advance(5.0)
    before = list(clock.slept)
    client.get(URL)
    assert clock.slept == before  # nothing needed waiting


def test_at_most_two_requests_are_in_flight() -> None:
    lock = threading.Lock()
    state = {"now": 0, "max": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.05)
        with lock:
            state["now"] -= 1
        return ok()

    client = PoliteClient(transport=httpx.MockTransport(handler), min_interval=0.0)
    threads = [threading.Thread(target=client.get, args=(URL,)) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert state["max"] == 2


# ----------------------------------------------------------------------- conditional


def test_validators_are_sent_back_and_a_304_is_an_answer() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            {
                "inm": request.headers.get("if-none-match", ""),
                "ims": request.headers.get("if-modified-since", ""),
            }
        )
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return ok(b"body", etag='"v1"', **{"last-modified": "Sat, 03 Oct 2026 10:00:00 GMT"})

    client, _ = make_client(handler)
    first = client.get(URL)
    assert first.status == 200 and first.etag == '"v1"'
    assert first.last_modified == "Sat, 03 Oct 2026 10:00:00 GMT"
    second = client.get(URL, conditional=Conditional(first.etag, first.last_modified))
    assert second.not_modified and second.body == b""
    assert seen[1] == {"inm": '"v1"', "ims": "Sat, 03 Oct 2026 10:00:00 GMT"}
    assert seen[0] == {"inm": "", "ims": ""}


# ------------------------------------------------------------------------- retries


def test_a_503_is_retried_with_exponential_backoff_and_jitter() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else ok()

    client, clock = make_client(handler, min_interval=0.0)
    response = client.get(URL)
    assert response.status == 200 and response.attempts == 3
    # base 2 s: attempt 1 waits 2 + 0.5 * 2, attempt 2 waits 4 + 0.5 * 2
    assert clock.slept == [3.0, 5.0]


def test_three_attempts_at_most_then_the_last_response_is_returned() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500)

    client, clock = make_client(handler, min_interval=0.0)
    response = client.get(URL)
    assert response.status == 500 and response.attempts == 3 and calls["n"] == 3
    assert len(clock.slept) == 2


def test_retry_after_seconds_is_honoured() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, headers={"Retry-After": "7"}) if calls["n"] == 1 else ok()

    client, clock = make_client(handler, min_interval=0.0)
    client.get(URL)
    assert clock.slept == [7.0]


def test_retry_after_http_date_is_honoured() -> None:
    calls = {"n": 0}
    when = "Sun, 04 Oct 2026 12:00:30 GMT"  # 30 s after START

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, headers={"Retry-After": when}) if calls["n"] == 1 else ok()

    client, clock = make_client(handler, min_interval=0.0)
    client.get(URL)
    assert clock.slept == [30.0]


def test_retry_after_is_capped_at_600_seconds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, headers={"Retry-After": "99999"}) if calls["n"] == 1 else ok()

    client, clock = make_client(handler, min_interval=0.0)
    client.get(URL)
    assert clock.slept == [600.0]


def test_an_unusable_retry_after_falls_back_to_backoff() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, headers={"Retry-After": "soon"}) if calls["n"] == 1 else ok()

    client, clock = make_client(handler, min_interval=0.0)
    client.get(URL)
    assert clock.slept == [3.0]


def test_transport_errors_are_retried_then_raised() -> None:
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("reset", request=request)
        return ok()

    client, _ = make_client(flaky, min_interval=0.0)
    assert client.get(URL).attempts == 3

    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    client, _ = make_client(dead, min_interval=0.0)
    with pytest.raises(TransportFailure) as raised:
        client.get(URL)
    assert raised.value.attempts == 3


def test_a_404_is_returned_not_retried_and_not_raised() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, content=b"nope")

    client, clock = make_client(handler, min_interval=0.0)
    response = client.get(URL)
    assert response.status == 404 and calls["n"] == 1 and clock.slept == []


def test_the_in_flight_slot_is_released_before_a_backoff_wait() -> None:
    """A retry waiting out a long Retry-After must not hold one of the two slots."""
    clock = Clock()
    slot_free_during_wait: list[bool] = []
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, headers={"Retry-After": "30"}) if calls["n"] == 1 else ok()

    client, _ = make_client(handler, clock, min_interval=0.0, max_in_flight=1)

    original_sleep = clock.sleep

    def checking_sleep(seconds: float) -> None:
        acquired = client._slots.acquire(blocking=False)
        slot_free_during_wait.append(acquired)
        if acquired:
            client._slots.release()
        original_sleep(seconds)

    client.sleep = checking_sleep
    client.get(URL)
    assert slot_free_during_wait == [True]


# ------------------------------------------------------------------------- breaker


def test_three_429s_in_a_row_open_the_breaker_for_six_hours() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429)

    client, clock = make_client(handler, min_interval=0.0)
    with pytest.raises(CircuitOpenError) as raised:
        client.get(URL)
    assert calls["n"] == 3
    assert raised.value.status == 429
    assert raised.value.paused_until == clock.wall() + timedelta(hours=6)
    assert "429" in raised.value.reason

    with pytest.raises(CircuitOpenError):
        client.get(URL)
    assert calls["n"] == 3  # the open breaker made no request

    clock.advance(6 * 3600 + 1)
    with pytest.raises(CircuitOpenError):
        client.get(URL)  # the pause is over, so a request is made (and 429s again)
    assert calls["n"] == 6


def test_a_success_between_429s_resets_the_streak() -> None:
    sequence = iter([429, 429, 200, 429, 429, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(sequence)
        return ok() if status == 200 else httpx.Response(status)

    client, _ = make_client(handler, min_interval=0.0)
    assert client.get(URL).status == 200
    assert client.get(URL).status == 200  # never three in a row


@pytest.mark.parametrize("status", [401, 403])
def test_401_and_403_open_the_breaker_at_once(status: int) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(status)

    client, _ = make_client(handler, min_interval=0.0)
    with pytest.raises(CircuitOpenError) as raised:
        client.get(URL)
    assert raised.value.status == status and calls["n"] == 1
    with pytest.raises(CircuitOpenError):
        client.get(URL)
    assert calls["n"] == 1


def test_a_caller_can_say_which_statuses_block() -> None:
    client, _ = make_client(lambda request: httpx.Response(403), min_interval=0.0)
    assert client.get(URL, blocked_statuses={401}).status == 403
    client, _ = make_client(lambda request: httpx.Response(401), min_interval=0.0)
    with pytest.raises(CircuitOpenError):
        client.get(URL, blocked_statuses={401})


def test_a_cloudflare_1015_opens_the_breaker_without_retrying() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            429,
            content=b"<html><title>Access denied | Cloudflare</title>error code: 1015</html>",
            headers={"server": "cloudflare", "cf-ray": "abc"},
        )

    client, _ = make_client(handler, min_interval=0.0)
    with pytest.raises(CircuitOpenError) as raised:
        client.get(URL)
    assert calls["n"] == 1
    assert "1015" in raised.value.reason


def test_a_page_that_merely_contains_1015_is_not_a_cloudflare_block() -> None:
    assert not intel_http.is_cloudflare_rate_limit(429, {}, b"order 1015 shipped")
    assert intel_http.is_cloudflare_rate_limit(429, {"server": "cloudflare"}, b"error code: 1015")
    assert not intel_http.is_cloudflare_rate_limit(200, {"server": "cloudflare"}, b"1015")


def test_the_breaker_is_per_host_and_can_be_persisted_and_restored() -> None:
    client, clock = make_client(lambda request: httpx.Response(403), min_interval=0.0)
    with pytest.raises(CircuitOpenError):
        client.get("https://blocked.example.org/x")
    state = client.breaker_state("blocked.example.org")
    assert state.paused_until == clock.wall() + timedelta(hours=6)
    assert state.reason == "forbidden (403)"
    assert client.breaker_state("other.example.org").paused_until is None

    fresh, fresh_clock = make_client(lambda request: ok(), min_interval=0.0)
    fresh.restore_breaker("blocked.example.org", state.paused_until, state.reason)
    with pytest.raises(CircuitOpenError):
        fresh.get("https://blocked.example.org/x")
    assert fresh.get("https://other.example.org/x").status == 200
    fresh.reset_breaker("blocked.example.org")
    assert fresh.get("https://blocked.example.org/x").status == 200


def test_a_restored_naive_datetime_is_read_as_utc() -> None:
    client, clock = make_client(lambda request: ok(), min_interval=0.0)
    client.restore_breaker(
        "feeds.example.org", clock.wall().replace(tzinfo=None) + timedelta(hours=1), "x"
    )
    with pytest.raises(CircuitOpenError):
        client.get(URL)


# --------------------------------------------------------------------------- size


def test_the_body_cap_is_enforced_while_streaming() -> None:
    limit = intel_http.DEFAULT_MAX_BYTES
    client, _ = make_client(lambda request: ok(b"x" * limit), min_interval=0.0)
    assert len(client.get(URL).body) == limit
    client, _ = make_client(lambda request: ok(b"x" * (limit + 1)), min_interval=0.0)
    with pytest.raises(TooLargeError):
        client.get(URL)


def test_a_declared_content_length_over_the_cap_is_refused_early() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"small", headers={"content-length": "9999999"})

    client, _ = make_client(handler, min_interval=0.0)
    with pytest.raises(TooLargeError):
        client.get(URL, max_bytes=1000)


def test_the_cap_applies_to_decoded_bytes_so_a_gzip_bomb_is_stopped() -> None:
    bomb = gzip.compress(b"\0" * 5_000_000)
    assert len(bomb) < 20_000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=bomb, headers={"content-encoding": "gzip"})

    client, _ = make_client(handler, min_interval=0.0)
    with pytest.raises(TooLargeError):
        client.get(URL)


def test_an_error_page_never_raises_too_large_and_is_truncated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"y" * 3_000_000)

    client, _ = make_client(handler, min_interval=0.0)
    response = client.get(URL)
    assert response.status == 404 and response.truncated
    assert len(response.body) == intel_http.ERROR_BODY_LIMIT


# ----------------------------------------------------------------------- redirects


def test_redirects_are_followed_by_hand_up_to_three() -> None:
    hops = {
        "https://a.example.org/1": "https://a.example.org/2",
        "https://a.example.org/2": "/3",
        "https://a.example.org/3": "https://b.example.org/4",
    }
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        if url in hops:
            return httpx.Response(302, headers={"location": hops[url]})
        return ok(b"final")

    client, _ = make_client(handler, min_interval=0.0)
    response = client.get("https://a.example.org/1")
    assert response.body == b"final" and response.url == "https://b.example.org/4"
    assert seen[-1] == "https://b.example.org/4" and len(seen) == 4


def test_more_than_three_redirects_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/again"})

    client, _ = make_client(handler, min_interval=0.0)
    with pytest.raises(RefusedError) as raised:
        client.get(URL)
    assert "redirects" in raised.value.reason


def test_an_https_to_http_downgrade_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(301, headers={"location": "http://feeds.example.org/plain"})

    client, _ = make_client(handler, min_interval=0.0)
    with pytest.raises(RefusedError) as raised:
        client.get(URL)
    assert "downgrade" in raised.value.reason


def test_the_allow_hook_runs_on_every_hop() -> None:
    asked: list[str] = []

    def allow(url: str) -> str | None:
        asked.append(url)
        return "robots.txt disallows that" if url.endswith("/private") else None

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/private"})
        return ok()

    client, _ = make_client(handler, min_interval=0.0)
    with pytest.raises(RefusedError) as raised:
        client.get("https://feeds.example.org/start", allow=allow)
    assert raised.value.reason == "robots.txt disallows that"
    assert asked == ["https://feeds.example.org/start", "https://feeds.example.org/private"]


def test_a_redirect_to_another_host_consults_that_hosts_breaker() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://blocked.example.org/x"})

    client, clock = make_client(handler, min_interval=0.0)
    client.restore_breaker("blocked.example.org", clock.wall() + timedelta(hours=1), "earlier")
    with pytest.raises(CircuitOpenError) as raised:
        client.get(URL)
    assert raised.value.key == "blocked.example.org"


# -------------------------------------------------------------------------- refusal


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.org/x",
        "javascript:alert(1)",
        "https://user:secret@example.org/x",
        "https://example.org/a b",
        "https://example.org/\x00",
        "https:///nohost",
        "",
        "https://example.org/" + "a" * 3000,
        "https://example.org:notaport/",
    ],
)
def test_a_hostile_url_is_refused_before_anything_is_sent(url: str) -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        return ok()

    client, _ = make_client(handler)
    with pytest.raises(RefusedError):
        client.get(url)
    assert sent == []
    assert intel_http.check_url(url) is not None


def test_check_url_accepts_http_and_https() -> None:
    assert intel_http.check_url("https://example.org/feed?x=1") is None
    assert intel_http.check_url("http://example.org/feed") is None


# --------------------------------------------------------------------------- misc


def test_a_shutdown_request_ends_a_backoff_wait() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, headers={"Retry-After": "300"})

    client = PoliteClient(
        transport=httpx.MockTransport(handler), min_interval=0.0, stop_requested=lambda: True
    )
    started = time.monotonic()
    with pytest.raises(StopRequested):
        client.get(URL)
    assert time.monotonic() - started < 5


def test_parse_retry_after() -> None:
    now = START
    assert intel_http.parse_retry_after("120", now) == 120.0
    assert intel_http.parse_retry_after("1.5", now) == 1.5
    assert intel_http.parse_retry_after("Sun, 04 Oct 2026 12:01:00 GMT", now) == 60.0
    assert intel_http.parse_retry_after("Sun, 04 Oct 2026 11:00:00 GMT", now) == 0.0
    assert intel_http.parse_retry_after(None, now) is None
    assert intel_http.parse_retry_after("", now) is None
    assert intel_http.parse_retry_after("tomorrow-ish", now) is None


def test_the_response_hash_is_of_the_body() -> None:
    import hashlib

    client, _ = make_client(lambda request: ok(b"abc"), min_interval=0.0)
    assert client.get(URL).sha256 == hashlib.sha256(b"abc").hexdigest()


def test_the_client_validates_its_own_settings() -> None:
    with pytest.raises(ValueError):
        PoliteClient(max_in_flight=0)
    with pytest.raises(ValueError):
        PoliteClient(max_attempts=0)


# ================================================================== recordings and raw files

import json  # noqa: E402
import os  # noqa: E402
import stat  # noqa: E402
from pathlib import Path  # noqa: E402

from nbastats.intel import recordings  # noqa: E402


def test_the_data_directory_follows_the_environment_then_the_platform(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert recordings.data_dir({"HARDWOOD_DATA_DIR": str(tmp_path / "x")}) == tmp_path / "x"
    assert recordings.data_dir({"HARDWOOD_DATA_DIR": "  ~/hw  "}) == Path("~/hw").expanduser()
    monkeypatch.setattr("sys.platform", "darwin")
    mac = recordings.data_dir({})
    assert mac == Path.home() / "Library" / "Application Support" / "Hardwood"
    monkeypatch.setattr("sys.platform", "linux")
    assert recordings.data_dir({"XDG_DATA_HOME": str(tmp_path)}) == tmp_path / "hardwood"
    assert recordings.data_dir({}) == Path.home() / ".local" / "share" / "hardwood"


def test_the_subfolders_hang_off_the_data_directory(tmp_path: Path) -> None:
    assert recordings.recordings_dir(tmp_path) == tmp_path / "recordings"
    assert recordings.raw_dir(tmp_path) == tmp_path / "raw"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Injury-Report_2026-10-22_05_30PM.pdf", "Injury-Report_2026-10-22_05_30PM.pdf"),
        ("../../etc/passwd", "etc-passwd"),
        ("a/b\\c:d*e?f", "a-b-c-d-e-f"),
        (".hidden", "hidden"),
        ("..", "item"),
        ("", "item"),
        ("   ", "item"),
        ("x" * 500, "x" * 80),
    ],
)
def test_safe_names_cannot_escape_their_folder(raw: str, expected: str) -> None:
    assert recordings.safe_name(raw) == expected
    assert "/" not in recordings.safe_name(raw) and ".." not in recordings.safe_name(raw)


def test_a_recording_has_its_bytes_a_sidecar_and_owner_only_permissions(tmp_path: Path) -> None:
    when = datetime(2026, 10, 22, 21, 30, 5, tzinfo=timezone.utc)
    path = recordings.save_recording(
        "nba_injury",
        "Injury-Report_x.pdf",
        b"%PDF-bytes",
        meta={"url": "https://x.example.org/a"},
        now=when,
        base=tmp_path,
    )
    assert path == tmp_path / "recordings" / "nba_injury" / "20261022T213005Z_Injury-Report_x.pdf"
    assert path.read_bytes() == b"%PDF-bytes"
    sidecar = json.loads(path.with_name(path.name + ".json").read_text())
    assert sidecar["url"] == "https://x.example.org/a" and sidecar["bytes"] == 10
    assert len(sidecar["sha256"]) == 64 and sidecar["recordedAt"] == "20261022T213005Z"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not [p for p in path.parent.iterdir() if p.name.startswith(".tmp-")]  # atomic, no debris


def test_two_recordings_in_one_second_both_survive(tmp_path: Path) -> None:
    when = datetime(2026, 10, 22, 21, 30, 5, tzinfo=timezone.utc)
    one = recordings.save_recording("k", "same", b"1", now=when, base=tmp_path)
    two = recordings.save_recording("k", "same", b"2", now=when, base=tmp_path)
    assert one != two and one.read_bytes() == b"1" and two.read_bytes() == b"2"


def test_a_hostile_name_or_kind_stays_inside_the_recordings_folder(tmp_path: Path) -> None:
    path = recordings.save_recording("../../outside", "../../../evil.pdf", b"x", base=tmp_path)
    assert (tmp_path / "recordings").resolve() in path.resolve().parents
    assert not (tmp_path.parent / "evil.pdf").exists()


def test_raw_payloads_are_content_addressed_and_stored_once(tmp_path: Path) -> None:
    first = recordings.save_raw("nba_injury", b"%PDF-one", extension="pdf", base=tmp_path)
    again = recordings.save_raw("nba_injury", b"%PDF-one", extension="pdf", base=tmp_path)
    assert first.created and not again.created and first.path == again.path
    assert first.path == (
        tmp_path / "raw" / "nba_injury" / first.sha256[:2] / f"{first.sha256}.pdf"
    )
    assert first.bytes == 8 and first.path.read_bytes() == b"%PDF-one"
    other = recordings.save_raw("nba_injury", b"%PDF-two", extension="pdf", base=tmp_path)
    assert other.path != first.path
    elsewhere = recordings.save_raw("euroleague", b"%PDF-one", extension="pdf", base=tmp_path)
    assert elsewhere.path != first.path and elsewhere.sha256 == first.sha256
    odd = recordings.save_raw("s", b"x", extension="../../p/d/f", base=tmp_path)
    assert odd.path.parent.parent == tmp_path / "raw" / "s" and odd.path.suffix == ".p-d-f"
    assert stat.S_IMODE(first.path.stat().st_mode) == 0o600


def test_fetching_the_same_content_again_refreshes_its_age(tmp_path: Path) -> None:
    saved = recordings.save_raw("s", b"same body", extension="bin", base=tmp_path)
    long_ago = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(saved.path, (long_ago, long_ago))
    again = recordings.save_raw("s", b"same body", extension="bin", base=tmp_path)
    assert not again.created and again.path.stat().st_mtime > long_ago + 1000
    removed = recordings.prune_older_than(
        tmp_path / "raw", datetime(2026, 6, 1, tzinfo=timezone.utc)
    )
    assert removed == 0 and again.path.exists()


def test_a_truncated_raw_file_is_rewritten(tmp_path: Path) -> None:
    saved = recordings.save_raw("s", b"complete body", extension="bin", base=tmp_path)
    saved.path.write_bytes(b"comp")  # a crash left it short
    again = recordings.save_raw("s", b"complete body", extension="bin", base=tmp_path)
    assert again.created and again.path.read_bytes() == b"complete body"


def test_pruning_removes_only_old_regular_files(tmp_path: Path) -> None:
    folder = tmp_path / "raw" / "nba_injury"
    (folder / "ab").mkdir(parents=True)
    old, new = folder / "ab" / "old.pdf", folder / "ab" / "new.pdf"
    old.write_bytes(b"o")
    new.write_bytes(b"n")
    long_ago = datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp()
    os.utime(old, (long_ago, long_ago))
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"keep")
    os.utime(outside, (long_ago, long_ago))
    (folder / "link.pdf").symlink_to(outside)
    removed = recordings.prune_older_than(folder, datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert removed == 1 and not old.exists() and new.exists()
    assert outside.exists() and (folder / "link.pdf").is_symlink()  # a link is never followed
    assert folder.is_dir()  # the tree itself is never removed
    assert recordings.prune_older_than(tmp_path / "missing", datetime(2026, 10, 1)) == 0
