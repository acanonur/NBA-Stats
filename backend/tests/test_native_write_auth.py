"""Who may write: the native Mac app's API key, a browser session, and nobody else (BD-1).

Why this file exists
--------------------
The Mac app is a native client, and four league routes change state when it uses them (enter an
injury status, retract one, paste a headline link, change a model setting). Its credential is the
API key in ``hardwood.env``. A key can be the whole of a native app's authorisation and still
leave a web page unable to write, but only if the server holds a few lines exactly, and each of
them is one of these tests:

* **The key authorises a write only when the request is not a browser's.** A browser adds an
  ``Origin`` (or ``Sec-Fetch-Site``) header to every request that changes state and page script
  cannot remove it; ``URLSession`` adds neither. So a valid key *with* an ``Origin`` is held to the
  browser path (session, CSRF token, same-origin ``Origin``) and, with no session, refused.
* **There is no keyless path.** The first design let a loopback caller send
  ``X-Hardwood-Client: mac`` instead of a key. That header is not a credential: ``api/app.py``
  answers every CORS preflight (``allow_headers=["*"]``), so any page open in the person's browser
  may send it to ``http://127.0.0.1:8000``, and the request really does come from loopback. Nothing
  here reads that header, and the tests below prove it.
* **A wrong key is refused without ever raising.** The comparison is ``hmac.compare_digest`` on
  bytes; a key header holding a non-ASCII byte used to be a 500 on the read gate (``TypeError``
  from comparing two ``str``) and is now just a wrong key.
* **The server really runs with the key the installer wrote.** The launch agent starts the API
  with ``HARDWOOD_ENV_FILE`` pointing at ``hardwood.env``; one test starts a fresh interpreter that
  way and reads the key back from the settings.

The probe app below mounts the three guards on routes that do nothing, so every row of the
matrix is asserted on the gate alone; a second group drives the real application (the CORS and
security middleware included) so the cross-origin cases are proved end to end.
"""

from __future__ import annotations

import importlib
import inspect
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi import Depends, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from nbastats import config as stats_config
from nbastats import db as db_module
from nbastats.accounts import config as auth_config
from nbastats.api import deps, errors as api_errors, routes_auth
from nbastats.api.app import create_app
from nbastats.api.routes_availability import require_nba_write
from nbastats.euroleague.api.deps import require_el_write

KEY = "k3y-for-the-native-app_0123456789abcdef"
SAME_ORIGIN = "http://127.0.0.1:8000"
HOSTILE_ORIGIN = "https://evil.example"
BACKEND = Path(__file__).resolve().parents[1]

#: The three names a write gate is reachable by. All of them are one function.
GATES = ("shared", "nba", "el")


@pytest.fixture(autouse=True)
def _environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A clean, keyed, quiet process: its own database, no rate limit, open sign-up."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'native-write.db'}")
    monkeypatch.setenv("HARDWOOD_API_KEY", KEY)
    monkeypatch.setenv("HARDWOOD_EL_ENABLED", "0")
    monkeypatch.setenv("HARDWOOD_RATE_LIMIT", "0")
    monkeypatch.setenv("HARDWOOD_SIGNUP_MODE", "open")
    monkeypatch.delenv("HARDWOOD_ENV_FILE", raising=False)
    monkeypatch.delenv("HARDWOOD_PUBLIC_BASE_URL", raising=False)
    stats_config.reset_settings_cache()
    auth_config.reset_auth_settings_cache()
    db_module.dispose_engine()
    deps.reset_rate_limiter()
    deps.reset_auth_limiter()
    routes_auth.reset_auth_route_limiters()
    yield
    db_module.dispose_engine()
    stats_config.reset_settings_cache()
    auth_config.reset_auth_settings_cache()
    deps.reset_rate_limiter()
    deps.reset_auth_limiter()


def _unset_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HARDWOOD_API_KEY", raising=False)
    stats_config.reset_settings_cache()


# --------------------------------------------------------------------------- the probe app


@pytest.fixture()
def probe() -> Iterator[TestClient]:
    """Three do-nothing write routes behind the three guards, plus the real sign-in routes so a
    test can obtain a genuine session and CSRF token. ``TestClient`` is told it is on loopback."""
    db_module.init_db()
    app = FastAPI()
    api_errors.install_error_handlers(app)
    app.include_router(routes_auth.router, prefix="/v1")

    @app.post("/probe/shared", dependencies=[Depends(deps.require_key_or_session_write)])
    def _shared() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/probe/nba", dependencies=[Depends(require_nba_write)])
    def _nba() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/probe/el", dependencies=[Depends(require_el_write)])
    def _el() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app, client=("127.0.0.1", 50123)) as client:
        yield client


def write(client: TestClient, gate: str, **headers: str) -> Any:
    return client.post(f"/probe/{gate}", headers=headers)


def refused(response: Any, status: int = 401) -> str:
    assert response.status_code == status, (response.status_code, response.text[:200])
    assert response.json()["error"]["code"] in {"unauthorized", "csrf_failed"}
    return response.json()["error"]["code"]


def sign_in(client: TestClient, email: str = "ada@example.com") -> str:
    """Sign up and log in through the real routes; returns the CSRF token. The session cookie is
    kept by ``client``."""
    password = "a reasonable password"
    origin = {"Origin": SAME_ORIGIN}
    signup = client.post(
        "/v1/auth/signup", json={"email": email, "password": password}, headers=origin
    )
    assert signup.status_code == 202, signup.text
    login = client.post(
        "/v1/auth/login", json={"email": email, "password": password}, headers=origin
    )
    assert login.status_code == 200, login.text
    return login.json()["csrfToken"]


# --------------------------------------------------------------------------- the native app


@pytest.mark.parametrize("gate", GATES)
def test_the_key_with_no_origin_is_a_native_write_and_is_allowed(
    probe: TestClient, gate: str
) -> None:
    response = write(probe, gate, **{"X-API-Key": KEY})
    assert response.status_code == 200 and response.json() == {"ok": True}


@pytest.mark.parametrize("gate", GATES)
def test_the_native_app_may_also_send_its_client_header_but_it_changes_nothing(
    probe: TestClient, gate: str
) -> None:
    """The Mac app sends ``X-Hardwood-Client: mac``. It is welcome and means nothing."""
    assert write(probe, gate, **{"X-API-Key": KEY, "X-Hardwood-Client": "mac"}).status_code == 200
    refused(write(probe, gate, **{"X-Hardwood-Client": "mac"}))


# ---------------------------------------------------------------- a key is not enough in a browser


@pytest.mark.parametrize("origin", [SAME_ORIGIN, "http://localhost:8000", HOSTILE_ORIGIN, "null"])
@pytest.mark.parametrize("gate", GATES)
def test_a_valid_key_with_an_origin_is_refused(
    probe: TestClient, gate: str, origin: str
) -> None:
    """Even the app's own origin: the key is not what authorises a browser."""
    assert refused(write(probe, gate, **{"X-API-Key": KEY, "Origin": origin})) == "unauthorized"


@pytest.mark.parametrize("gate", GATES)
def test_an_empty_origin_header_still_counts_as_a_browser(probe: TestClient, gate: str) -> None:
    """Presence is what marks a browser, not a truthy value."""
    refused(write(probe, gate, **{"X-API-Key": KEY, "Origin": ""}))


@pytest.mark.parametrize("site", ["same-origin", "same-site", "cross-site", "none"])
@pytest.mark.parametrize("gate", GATES)
def test_a_valid_key_with_sec_fetch_site_is_refused(
    probe: TestClient, gate: str, site: str
) -> None:
    """``Sec-Fetch-Site`` is a header a page cannot set either. A native client sends none."""
    refused(write(probe, gate, **{"X-API-Key": KEY, "Sec-Fetch-Site": site}))


@pytest.mark.parametrize("gate", GATES)
def test_a_wrong_key_with_an_origin_is_refused_too(probe: TestClient, gate: str) -> None:
    refused(write(probe, gate, **{"X-API-Key": "not-the-key", "Origin": SAME_ORIGIN}))


# --------------------------------------------------------------------- there is no keyless path


@pytest.mark.parametrize("gate", GATES)
def test_no_credential_at_all_is_refused_even_from_loopback(probe: TestClient, gate: str) -> None:
    assert refused(write(probe, gate)) == "unauthorized"


@pytest.mark.parametrize("gate", GATES)
def test_the_client_header_is_not_a_credential(probe: TestClient, gate: str) -> None:
    """The loopback shortcut this design rejected: ``X-Hardwood-Client: mac`` from 127.0.0.1."""
    refused(write(probe, gate, **{"X-Hardwood-Client": "mac"}))
    refused(write(probe, gate, **{"X-Hardwood-Client": "mac", "Origin": SAME_ORIGIN}))
    refused(write(probe, gate, **{"X-Hardwood-Client": "mac", "X-API-Key": "wrong"}))


@pytest.mark.parametrize("gate", GATES)
def test_a_server_with_no_key_configured_refuses_every_sessionless_write(
    probe: TestClient, monkeypatch: pytest.MonkeyPatch, gate: str
) -> None:
    """With no ``HARDWOOD_API_KEY`` there is no key to present: nothing a caller sends counts."""
    _unset_key(monkeypatch)
    refused(write(probe, gate))
    refused(write(probe, gate, **{"X-Hardwood-Client": "mac"}))
    refused(write(probe, gate, **{"X-API-Key": KEY}))
    refused(write(probe, gate, **{"X-API-Key": ""}))
    refused(write(probe, gate, **{"X-API-Key": "anything", "X-Hardwood-Client": "mac"}))


@pytest.mark.parametrize("gate", GATES)
def test_a_blank_configured_key_is_no_key(
    probe: TestClient, monkeypatch: pytest.MonkeyPatch, gate: str
) -> None:
    """``HARDWOOD_API_KEY=`` (an empty line in the settings file) must not make "" a password."""
    monkeypatch.setenv("HARDWOOD_API_KEY", "   ")
    stats_config.reset_settings_cache()
    refused(write(probe, gate, **{"X-API-Key": ""}))
    refused(write(probe, gate, **{"X-API-Key": "   "}))


# --------------------------------------------------------------------------- a wrong key


@pytest.mark.parametrize("gate", GATES)
@pytest.mark.parametrize(
    "supplied",
    [b"", b"wrong", KEY.encode()[:-1], KEY.encode() + b"x", KEY.upper().encode(), b"k\xc3\xa9y"],
)
def test_a_wrong_key_is_refused_and_never_raises(
    probe: TestClient, gate: str, supplied: bytes
) -> None:
    """A truncated, extended, re-cased or non-ASCII key is simply wrong (no 500)."""
    response = probe.post(f"/probe/{gate}", headers=[(b"X-API-Key", supplied)])
    refused(response)


def test_the_comparison_is_constant_time_on_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every gate decides through ``hmac.compare_digest`` over bytes, never ``==``."""
    seen: list[tuple[type, type]] = []
    real = deps.hmac.compare_digest

    def spy(left: Any, right: Any) -> bool:
        seen.append((type(left), type(right)))
        return real(left, right)

    monkeypatch.setattr(deps.hmac, "compare_digest", spy)
    assert deps.api_key_matches(KEY, KEY) is True
    assert deps.api_key_matches("nope", KEY) is False
    assert deps.api_key_matches("kéy", KEY) is False  # the old code raised TypeError here
    assert seen == [(bytes, bytes)] * 3


def test_a_missing_or_empty_key_never_matches() -> None:
    for supplied in (None, ""):
        assert deps.api_key_matches(supplied, KEY) is False
    for expected in (None, ""):
        assert deps.api_key_matches(KEY, expected) is False
        assert deps.api_key_matches("", expected) is False
        assert deps.api_key_matches(None, expected) is False


def test_the_read_gate_no_longer_turns_a_non_ascii_key_into_a_500() -> None:
    """The same helper now guards reads: this header used to raise inside the dependency."""
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        bad = client.get("/v1/teams", headers=[(b"X-API-Key", b"k\xc3\xa9y")])
        assert bad.status_code == 401 and bad.json()["error"]["code"] == "unauthorized"
        assert client.get("/v1/teams", headers={"X-API-Key": KEY}).status_code == 200


# --------------------------------------------------------------------------- the browser path


@pytest.mark.parametrize("gate", GATES)
def test_a_signed_in_browser_writes_with_session_csrf_and_origin(
    probe: TestClient, gate: str
) -> None:
    csrf = sign_in(probe)
    headers = {"Origin": SAME_ORIGIN, "X-Hardwood-CSRF": csrf}
    assert write(probe, gate, **headers).status_code == 200


@pytest.mark.parametrize("gate", GATES)
def test_a_signed_in_browser_is_refused_without_the_csrf_token_or_from_another_origin(
    probe: TestClient, gate: str
) -> None:
    csrf = sign_in(probe)
    assert refused(write(probe, gate, Origin=SAME_ORIGIN), 403) == "csrf_failed"
    assert (
        refused(write(probe, gate, **{"Origin": HOSTILE_ORIGIN, "X-Hardwood-CSRF": csrf}), 403)
        == "csrf_failed"
    )
    assert (
        refused(write(probe, gate, **{"Origin": SAME_ORIGIN, "X-Hardwood-CSRF": "forged"}), 403)
        == "csrf_failed"
    )


@pytest.mark.parametrize("gate", GATES)
def test_the_key_does_not_rescue_a_browser_that_fails_the_browser_path(
    probe: TestClient, gate: str
) -> None:
    """A signed-in browser that also holds the key is still judged by the CSRF check."""
    sign_in(probe)
    assert (
        refused(write(probe, gate, **{"X-API-Key": KEY, "Origin": SAME_ORIGIN}), 403)
        == "csrf_failed"
    )


@pytest.mark.parametrize("gate", GATES)
def test_the_key_and_a_good_browser_session_together_are_fine(
    probe: TestClient, gate: str
) -> None:
    csrf = sign_in(probe)
    headers = {"X-API-Key": KEY, "Origin": SAME_ORIGIN, "X-Hardwood-CSRF": csrf}
    assert write(probe, gate, **headers).status_code == 200


# --------------------------------------------------------------------------- the real application


@pytest.fixture()
def real() -> Iterator[TestClient]:
    """The whole application (CORS, security headers, routers), on loopback, EuroLeague off."""
    with TestClient(create_app(), client=("127.0.0.1", 50124)) as client:
        yield client


PATCH_BODY = {"settings": [{"key": "boostCap", "value": 1.3}]}


def _stored_setting(client: TestClient) -> float | None:
    response = client.get("/v1/model-settings", headers={"X-API-Key": KEY})
    assert response.status_code == 200, response.text
    return next(s["value"] for s in response.json()["settings"] if s["key"] == "boostCap")


def test_the_real_route_takes_the_native_write_and_stores_it(real: TestClient) -> None:
    before = _stored_setting(real)
    native = {"X-API-Key": KEY, "X-Hardwood-Client": "mac"}
    response = real.patch("/v1/model-settings", json=PATCH_BODY, headers=native)
    assert response.status_code == 200, response.text
    assert _stored_setting(real) == 1.3 != before


def test_a_cross_origin_page_cannot_write_even_with_the_key_and_the_client_header(
    real: TestClient,
) -> None:
    """The attack the amendment closes, end to end through the real CORS middleware.

    Step one is the browser's preflight. The service ANSWERS it, for any header, which is exactly
    why the client header proves nothing (this assertion documents that the wall is not CORS).
    Step two is the request the browser then sends, with its own ``Origin``: refused.
    """
    before = _stored_setting(real)
    preflight = real.options(
        "/v1/model-settings",
        headers={
            "Origin": HOSTILE_ORIGIN,
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type,x-api-key,x-hardwood-client",
        },
    )
    assert preflight.status_code == 200
    allowed = preflight.headers.get("access-control-allow-headers", "").lower()
    assert "x-hardwood-client" in allowed and "x-api-key" in allowed
    for headers in (
        {"Origin": HOSTILE_ORIGIN, "X-Hardwood-Client": "mac"},
        {"Origin": HOSTILE_ORIGIN, "X-Hardwood-Client": "mac", "X-API-Key": KEY},
        {"Origin": HOSTILE_ORIGIN, "X-API-Key": KEY},
        {"Origin": SAME_ORIGIN, "X-API-Key": KEY},
        {"Origin": HOSTILE_ORIGIN, "Sec-Fetch-Site": "cross-site", "X-API-Key": KEY},
    ):
        response = real.patch("/v1/model-settings", json=PATCH_BODY, headers=headers)
        assert response.status_code == 401, (headers, response.status_code, response.text[:160])
        assert response.json()["error"]["code"] == "unauthorized"
    assert _stored_setting(real) == before


def test_no_key_and_the_client_header_from_loopback_cannot_write_on_the_real_app(
    real: TestClient,
) -> None:
    before = _stored_setting(real)
    for headers in ({}, {"X-Hardwood-Client": "mac"}, {"X-API-Key": "wrong"}):
        response = real.patch("/v1/model-settings", json=PATCH_BODY, headers=headers)
        assert response.status_code == 401, headers
    assert _stored_setting(real) == before


def test_a_keyless_real_app_refuses_writes_and_a_session_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _unset_key(monkeypatch)
    with TestClient(create_app(), client=("127.0.0.1", 50125)) as client:
        for headers in ({}, {"X-Hardwood-Client": "mac"}, {"X-API-Key": KEY}):
            response = client.patch("/v1/model-settings", json=PATCH_BODY, headers=headers)
            assert response.status_code == 401, headers
        csrf = sign_in(client)
        signed_in = client.patch(
            "/v1/model-settings",
            json=PATCH_BODY,
            headers={"Origin": SAME_ORIGIN, "X-Hardwood-CSRF": csrf},
        )
        assert signed_in.status_code == 200, signed_in.text


# ---------------------------------------------------------------------- the key reaches the server


def test_the_server_reads_its_key_from_the_settings_file_the_launch_agent_names(
    tmp_path: Path,
) -> None:
    """``com.hardwood.api`` sets ``HARDWOOD_ENV_FILE`` and nothing else about the key. A fresh
    interpreter started that way must come up requiring exactly the key in that file."""
    env_file = tmp_path / "hardwood.env"
    env_file.write_text(f"# settings\nHARDWOOD_NEWS=on\nHARDWOOD_API_KEY={KEY}\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "HARDWOOD_API_KEY"}
    env.update(
        HARDWOOD_ENV_FILE=str(env_file),
        DATABASE_URL=f"sqlite:///{tmp_path / 'agent.db'}",
        HARDWOOD_EL_ENABLED="0",
    )
    code = (
        "import nbastats.api.app\n"
        "from nbastats.config import get_settings\n"
        "s = get_settings()\n"
        "print(s.requires_api_key, s.api_key)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=BACKEND,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == f"True {KEY}"


# --------------------------------------------------------------------------- no route can forget the gate

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}

#: Router modules whose unsafe routes are *not* league writes, and why each is out of this rule:
#: sign-in is how a session is obtained at all (so cannot need one), the account and dashboard
#: routes carry their own per-route session dependencies (``require_user``/``require_write``), and
#: the dashboard resolver is a POST only because its body is large; it reads.
OTHER_GATES = {"routes_auth", "routes_me", "routes_dashboard"}

#: Every optional router module and the mandatory ones (``api/app.py`` includes them all).
ROUTER_MODULES = (
    "routes_meta", "routes_players", "routes_teams", "routes_games", "routes_sync",
    "routes_fantasy", "routes_leagues", "routes_matchups", "routes_defense", "routes_projections",
    "routes_availability", "routes_sources", "routes_euroleague",
)


def _calls(dependant: Dependant) -> Iterator[Any]:
    for child in dependant.dependencies:
        if child.call is not None:
            yield child.call
        yield from _calls(child)


def _unsafe_routes() -> list[tuple[str, str, str, set[Any]]]:
    """``(module, method, path, dependency callables)`` for every unsafe route in the league and
    stats routers, read from the router objects themselves."""
    found: list[tuple[str, str, str, set[Any]]] = []
    for name in ROUTER_MODULES:
        module = importlib.import_module(f"nbastats.api.{name}")
        for route in module.router.routes:  # routes_euroleague is a shim over the EL's router
            if isinstance(route, APIRoute) and route.methods & UNSAFE:
                for method in sorted(route.methods & UNSAFE):
                    found.append((name, method, route.path, set(_calls(route.dependant))))
    return found


def test_the_walk_finds_the_eight_league_writes() -> None:
    writes = sorted((method, path) for _, method, path, _ in _unsafe_routes())
    assert writes == sorted(
        [
            ("POST", "/availability"),
            ("DELETE", "/availability/{overrideId}"),
            ("POST", "/news/links"),
            ("PATCH", "/model-settings"),
            ("POST", "/el/availability"),
            ("DELETE", "/el/availability/{statusId}"),
            ("POST", "/el/news/links"),
            ("PATCH", "/el/model-settings"),
        ]
    )


def test_every_unsafe_route_outside_accounts_depends_on_the_one_write_gate() -> None:
    """A new write route that forgets its gate would be open to anyone who passes the read gate.
    The read gate accepts the key from a browser; the write gate does not."""
    gates = {require_nba_write, require_el_write, deps.require_key_or_session_write}
    unguarded = [
        f"{method} {path} ({name})"
        for name, method, path, calls in _unsafe_routes()
        if not calls & gates
    ]
    assert unguarded == []
    assert OTHER_GATES.isdisjoint(ROUTER_MODULES)


def test_the_two_league_guards_are_thin_wrappers_with_no_comparison_of_their_own() -> None:
    """One implementation of the rule, so the NBA's writes and the EuroLeague's cannot differ."""
    import ast
    import textwrap

    for guard in (require_nba_write, require_el_write):
        tree = ast.parse(textwrap.dedent(inspect.getsource(guard)))
        function = tree.body[0]
        assert isinstance(function, ast.FunctionDef)
        body = function.body[1:]  # everything after the docstring
        calls = [ast.unparse(node) for node in body]
        assert calls == ["require_key_or_session_write(request)"] or calls == [
            "deps.require_key_or_session_write(request)"
        ], f"{guard.__name__} must only hand the request to the shared gate: {calls}"


def test_no_module_other_than_deps_compares_the_api_key() -> None:
    """The only place the key is compared is ``api_key_matches``: grep the package for a second."""
    offenders: list[str] = []
    root = BACKEND / "nbastats"
    for path in sorted(root.rglob("*.py")):
        if path.name == "deps.py" and path.parent.name == "api":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if "compare_digest(" in code and ("api_key" in code.lower() or "API_KEY" in code):
                offenders.append(f"{path.relative_to(root)}:{number}")
    assert offenders == []
