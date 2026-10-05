"""Tests for Sign in with Apple: the ES256 client-secret JWT, the ``form_post`` body parser,
the once-only ``user`` name field, the ``response_mode`` misconfiguration guard, private-relay
handling, and a full callback round trip — all against a local EC keypair, never the network.
"""
from __future__ import annotations

import json
import time
from typing import Any, Iterator
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jwt.algorithms import ECAlgorithm
from sqlalchemy import select

from nbastats import config as stats_config
from nbastats import db as db_module
from nbastats.accounts import config as auth_config
from nbastats.accounts.models import User, UserIdentity
from nbastats.accounts.providers import apple as apple_provider
from nbastats.accounts.providers import oidc
from nbastats.api import errors as api_errors
from nbastats.api import routes_auth

SERVICES_ID = "com.hardwood.web"
TEAM_ID = "TEAMID1234"
KEY_ID = "KEYID6789"
KID = "apple-test-key-1"


@pytest.fixture(scope="module")
def ec_keypair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key, private_key.public_key()


@pytest.fixture()
def apple_key_file(tmp_path, ec_keypair) -> str:
    private_key, _public = ec_keypair
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = tmp_path / "AuthKey_test.p8"
    path.write_bytes(pem)
    path.chmod(0o600)
    return str(path)


@pytest.fixture(autouse=True)
def _clear_apple_and_oidc_caches() -> Iterator[None]:
    apple_provider._cache.clear()
    oidc._discovery_cache.clear()
    oidc._jwks_cache.clear()
    oidc._last_forced_jwks_refresh.clear()
    yield
    apple_provider._cache.clear()
    oidc._discovery_cache.clear()
    oidc._jwks_cache.clear()
    oidc._last_forced_jwks_refresh.clear()


def _settings_with_apple(apple_key_file: str) -> auth_config.AuthSettings:
    return auth_config.AuthSettings(
        public_base_url="https://hardwood.example",
        apple_services_id=SERVICES_ID,
        apple_team_id=TEAM_ID,
        apple_key_id=KEY_ID,
        apple_key_file=apple_key_file,
    )


# --------------------------------------------------------------------------- client_secret


def test_client_secret_is_a_well_formed_five_minute_es256_jwt(
    apple_key_file: str,
    ec_keypair,
) -> None:
    _private, public_key = ec_keypair
    settings = _settings_with_apple(apple_key_file)

    token = apple_provider.client_secret(settings)
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "ES256"
    assert header["kid"] == KEY_ID

    claims = jwt.decode(
        token, key=public_key, algorithms=["ES256"], audience=apple_provider.APPLE_ISSUER,
        options={"require": ["exp", "iat", "aud", "iss", "sub"]},
    )
    assert claims["iss"] == TEAM_ID
    assert claims["sub"] == SERVICES_ID
    assert claims["aud"] == apple_provider.APPLE_ISSUER
    assert claims["exp"] - claims["iat"] == 300


def test_client_secret_is_cached_across_calls(apple_key_file: str) -> None:
    settings = _settings_with_apple(apple_key_file)
    first = apple_provider.client_secret(settings)
    second = apple_provider.client_secret(settings)
    assert first == second  # same process, well within the 5-minute lifetime -> reused


def test_client_secret_never_reads_a_stale_or_missing_key_silently(tmp_path) -> None:
    settings = _settings_with_apple(str(tmp_path / "does-not-exist.p8"))
    with pytest.raises(OSError):
        apple_provider.client_secret(settings)


# --------------------------------------------------------------------------- parse_callback_body


def test_parse_callback_body_reads_form_post_fields() -> None:
    encoded_user = "%7B%22name%22%3A%7B%22firstName%22%3A%22Ada%22%7D%7D"
    body = f"code=abc123&state=xyz&id_token=eyJhbGci&user={encoded_user}".encode()
    fields = apple_provider.parse_callback_body(body)
    assert fields["code"] == "abc123"
    assert fields["state"] == "xyz"
    assert json.loads(fields["user"]) == {"name": {"firstName": "Ada"}}


def test_parse_callback_body_handles_an_empty_body() -> None:
    assert apple_provider.parse_callback_body(b"") == {}


def test_parse_callback_body_handles_the_cancellation_error() -> None:
    fields = apple_provider.parse_callback_body(b"error=user_cancelled_authorize&state=xyz")
    assert fields["error"] == "user_cancelled_authorize"


# --------------------------------------------------------------------------- first name parsing


def test_first_authorization_name_parses_the_normal_shape() -> None:
    raw = json.dumps({"name": {"firstName": "Ada", "lastName": "Lovelace"}, "email": "a@b.com"})
    assert apple_provider.first_authorization_name(raw) == ("Ada", "Lovelace")


@pytest.mark.parametrize(
    "raw",
    [
        None, "", "not json at all", "42",
        json.dumps("a string, not an object"),
        json.dumps({"name": "not a dict"}),
    ],
)
def test_first_authorization_name_is_defensive_about_malformed_input(raw: str | None) -> None:
    assert apple_provider.first_authorization_name(raw) == (None, None)


def test_first_authorization_name_absent_on_a_later_callback_means_absent() -> None:
    """A later callback with no ``user`` field must never be mistaken for "the user cleared
    their name" — callers rely on ``(None, None)`` meaning "say nothing", not "erase"."""
    assert apple_provider.first_authorization_name(None) == (None, None)


def test_first_authorization_name_strips_and_drops_blank_parts() -> None:
    raw = json.dumps({"name": {"firstName": "  ", "lastName": "Lovelace  "}})
    assert apple_provider.first_authorization_name(raw) == (None, "Lovelace")


# --------------------------------------------------------------------------- response_mode guard


@pytest.fixture()
def bare_client() -> TestClient:
    app = FastAPI()
    api_errors.install_error_handlers(app)
    app.include_router(routes_auth.router, prefix="/v1")
    return TestClient(app)


def test_get_on_apple_callback_names_the_fix(bare_client: TestClient) -> None:
    response = bare_client.get("/v1/auth/apple/callback")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "apple_response_mode"
    assert "form_post" in response.json()["error"]["message"]


# --------------------------------------------------------------------------- end-to-end callback


@pytest.fixture()
def apple_client(
    tmp_path, apple_key_file: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    db_path = tmp_path / "apple.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("HARDWOOD_PUBLIC_BASE_URL", "https://hardwood.example")
    monkeypatch.setenv("HARDWOOD_APPLE_SERVICES_ID", SERVICES_ID)
    monkeypatch.setenv("HARDWOOD_APPLE_TEAM_ID", TEAM_ID)
    monkeypatch.setenv("HARDWOOD_APPLE_KEY_ID", KEY_ID)
    monkeypatch.setenv("HARDWOOD_APPLE_KEY_FILE", apple_key_file)
    stats_config.reset_settings_cache()
    auth_config.reset_auth_settings_cache()
    db_module.dispose_engine()
    db_module.init_db()
    routes_auth.reset_auth_route_limiters()

    app = FastAPI()
    api_errors.install_error_handlers(app)
    app.include_router(routes_auth.router, prefix="/v1")
    client = TestClient(app, base_url="https://hardwood.example")
    try:
        yield client
    finally:
        db_module.dispose_engine()
        auth_config.reset_auth_settings_cache()
        stats_config.reset_settings_cache()


def _mint_apple_id_token(
    ec_keypair, *, nonce: str, email: str, email_verified: Any = True, extra: dict | None = None
) -> str:
    private_key, _public = ec_keypair
    now = int(time.time())
    claims = {
        "iss": apple_provider.APPLE_ISSUER,
        "aud": SERVICES_ID,
        "sub": "apple-subject-1",
        "iat": now,
        "exp": now + 3600,
        "nonce": nonce,
        "email": email,
        "email_verified": email_verified,
    }
    if extra:
        claims.update(extra)
    return jwt.encode(claims, private_key, algorithm="ES256", headers={"kid": KID})


def _apple_jwks(ec_keypair) -> dict[str, Any]:
    _private, public = ec_keypair
    jwk = json.loads(ECAlgorithm.to_jwk(public))
    jwk["kid"] = KID
    return {"keys": [jwk]}


def test_apple_start_uses_form_post_and_the_https_only_samesite_none_cookie(
    apple_client: TestClient,
) -> None:
    response = apple_client.get("/v1/auth/apple/start", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    query = parse_qs(urlsplit(location).query)
    assert query["response_type"] == ["code id_token"]
    assert query["response_mode"] == ["form_post"]
    assert query["client_id"] == [SERVICES_ID]

    set_cookie = response.headers.get("set-cookie", "")
    assert "hw_oauth" in set_cookie or "__Host-hw_oauth" in set_cookie
    assert "samesite=none" in set_cookie.lower()
    assert "secure" in set_cookie.lower()


def test_apple_callback_completes_sign_in_with_first_time_name_and_relay_email(
    apple_client: TestClient, ec_keypair, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oidc, "_get_json", lambda url: _apple_jwks(ec_keypair))

    start = apple_client.get("/v1/auth/apple/start", follow_redirects=False)
    location = start.headers["location"]
    query = parse_qs(urlsplit(location).query)
    state = query["state"][0]
    nonce = query["nonce"][0]
    handle_cookie = start.cookies.get("__Host-hw_oauth") or start.cookies.get("hw_oauth")

    id_token = _mint_apple_id_token(
        ec_keypair, nonce=nonce, email="abc123@privaterelay.appleid.com",
        extra={"is_private_email": "true"},
    )

    def fake_post(url, data=None, timeout=None, **kwargs):
        assert data["client_id"] == SERVICES_ID
        assert "client_secret" in data

        class _Resp:
            def raise_for_status(self_inner):
                return None

            def json(self_inner):
                return {"id_token": id_token}

        return _Resp()

    monkeypatch.setattr(routes_auth.httpx, "post", fake_post)

    user_field = json.dumps({"name": {"firstName": "Ada", "lastName": "Lovelace"}})
    apple_client.cookies.set("__Host-hw_oauth", handle_cookie)
    response = apple_client.post(
        "/v1/auth/apple/callback",
        data={"code": "fake-code", "state": state, "id_token": id_token, "user": user_field},
        follow_redirects=False,
    )
    assert response.status_code == 303

    factory = db_module.get_sessionmaker()
    with factory() as db:
        user = db.execute(select(User)).scalar_one()
        assert user.is_private_relay is True
        assert user.email_verified_at is None  # never laundered from an unverified/relay claim
        assert user.given_name == "Ada"
        assert user.family_name == "Lovelace"
        identity = db.execute(select(UserIdentity)).scalar_one()
        assert identity.provider == "apple"


def test_apple_callback_does_not_overwrite_a_stored_name_on_a_later_callback(
    apple_client: TestClient, ec_keypair, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Apple sends the name once. A later sign-in with no ``user`` field must not erase the
    name captured the first time."""
    monkeypatch.setattr(oidc, "_get_json", lambda url: _apple_jwks(ec_keypair))

    def do_callback(user_field: str | None):
        start = apple_client.get("/v1/auth/apple/start", follow_redirects=False)
        query = parse_qs(urlsplit(start.headers["location"]).query)
        state, nonce = query["state"][0], query["nonce"][0]
        handle_cookie = start.cookies.get("__Host-hw_oauth") or start.cookies.get("hw_oauth")
        id_token = _mint_apple_id_token(ec_keypair, nonce=nonce, email="ada@example.com")

        def fake_post(url, data=None, timeout=None, **kwargs):
            class _Resp:
                def raise_for_status(self_inner):
                    return None

                def json(self_inner):
                    return {"id_token": id_token}

            return _Resp()

        monkeypatch.setattr(routes_auth.httpx, "post", fake_post)
        apple_client.cookies.set("__Host-hw_oauth", handle_cookie)
        form = {"code": "fake-code", "state": state, "id_token": id_token}
        if user_field is not None:
            form["user"] = user_field
        return apple_client.post("/v1/auth/apple/callback", data=form, follow_redirects=False)

    first = do_callback(json.dumps({"name": {"firstName": "Ada", "lastName": "Lovelace"}}))
    assert first.status_code == 303
    second = do_callback(None)
    assert second.status_code == 303

    factory = db_module.get_sessionmaker()
    with factory() as db:
        user = db.execute(select(User)).scalar_one()
        assert user.given_name == "Ada"
        assert user.family_name == "Lovelace"


def test_apple_callback_handles_user_cancellation_quietly(apple_client: TestClient) -> None:
    response = apple_client.post(
        "/v1/auth/apple/callback",
        data={"error": "user_cancelled_authorize", "state": "whatever"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/sign-in"


def test_apple_callback_rejects_a_state_mismatch(apple_client: TestClient) -> None:
    start = apple_client.get("/v1/auth/apple/start", follow_redirects=False)
    handle_cookie = start.cookies.get("__Host-hw_oauth") or start.cookies.get("hw_oauth")
    apple_client.cookies.set("__Host-hw_oauth", handle_cookie)

    response = apple_client.post(
        "/v1/auth/apple/callback", data={"code": "x", "state": "not-the-real-state"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "oauth_state_mismatch"
