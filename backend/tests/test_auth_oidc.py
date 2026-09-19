"""Tests for the OIDC verification core (``accounts/providers/oidc.py``), the six account-
linking rules (``accounts/linking.py``), and the Google end-to-end callback in
``routes_auth.py`` — all against a local, in-process fake issuer (a real RSA keypair, real
JWTs, a fake JWKS document held in a Python dict) and never the network, per
``WEB_DESIGN.md``'s testing instructions.

Also includes ``test_only_two_writers_of_email_verified_at``, the invariant §6.5 pins.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Iterator

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from sqlalchemy import select

from nbastats import config as stats_config
from nbastats import db as db_module
from nbastats.accounts import config as auth_config
from nbastats.accounts.linking import LinkConflict, link_to, resolve_login
from nbastats.accounts.models import OAuthTransaction, User, UserIdentity
from nbastats.accounts.providers import google as google_provider
from nbastats.accounts.providers import oidc
from nbastats.accounts.providers.oidc import Claims, OIDCError, verify_id_token
from nbastats.api import errors as api_errors
from nbastats.api import routes_auth


# --------------------------------------------------------------------------- a local fake issuer


ISSUER = "https://issuer.example"
AUDIENCE = "test-client-id"
KID = "test-signing-key-1"


@pytest.fixture(scope="module")
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture()
def jwks_doc(rsa_keypair) -> dict[str, Any]:
    _private, public = rsa_keypair
    jwk = json.loads(RSAAlgorithm.to_jwk(public))
    jwk["kid"] = KID
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    return {"keys": [jwk]}


@pytest.fixture(autouse=True)
def _clear_oidc_caches() -> Iterator[None]:
    """``oidc.py`` caches discovery/JWKS documents in module-level dicts, keyed by URL — clear
    them before and after every test so one test's fake issuer never leaks into another's."""
    oidc._discovery_cache.clear()
    oidc._jwks_cache.clear()
    oidc._last_forced_jwks_refresh.clear()
    yield
    oidc._discovery_cache.clear()
    oidc._jwks_cache.clear()
    oidc._last_forced_jwks_refresh.clear()


def _mint(
    rsa_keypair,
    *,
    kid: str = KID,
    alg: str = "RS256",
    issuer: str = ISSUER,
    audience: Any = AUDIENCE,
    nonce: str = "the-nonce",
    email: str | None = "ada@example.com",
    email_verified: Any = True,
    extra: dict[str, Any] | None = None,
) -> str:
    private_key, _public = rsa_keypair
    now = int(time.time())
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": "subject-123",
        "iat": now,
        "exp": now + 3600,
        "nonce": nonce,
    }
    if email is not None:
        claims["email"] = email
        claims["email_verified"] = email_verified
    if extra:
        claims.update(extra)
    return jwt.encode(claims, private_key, algorithm=alg, headers={"kid": kid})


def _patch_jwks(
    monkeypatch: pytest.MonkeyPatch, jwks_doc: dict[str, Any], jwks_uri: str = f"{ISSUER}/keys"
) -> None:
    monkeypatch.setattr(oidc, "_get_json", lambda url: jwks_doc if url == jwks_uri else {})


# --------------------------------------------------------------------------- verify_id_token


def test_verify_id_token_accepts_a_well_formed_token(rsa_keypair, jwks_doc, monkeypatch) -> None:
    _patch_jwks(monkeypatch, jwks_doc)
    token = _mint(rsa_keypair)
    claims = verify_id_token(
        token, issuers={ISSUER}, audience=AUDIENCE, algorithms=["RS256"], nonce="the-nonce",
        jwks_uri=f"{ISSUER}/keys",
    )
    assert claims.subject == "subject-123"
    assert claims.email == "ada@example.com"
    assert claims.email_verified is True


def test_verify_id_token_normalises_apples_stringly_typed_email_verified(
    rsa_keypair,
    jwks_doc,
    monkeypatch,
) -> None:
    _patch_jwks(monkeypatch, jwks_doc)
    token = _mint(rsa_keypair, email_verified="true")
    claims = verify_id_token(
        token, issuers={ISSUER}, audience=AUDIENCE, algorithms=["RS256"], nonce="the-nonce",
        jwks_uri=f"{ISSUER}/keys",
    )
    assert claims.email_verified is True

    token_false = _mint(rsa_keypair, email_verified="false")
    claims_false = verify_id_token(
        token_false, issuers={ISSUER}, audience=AUDIENCE, algorithms=["RS256"], nonce="the-nonce",
        jwks_uri=f"{ISSUER}/keys",
    )
    assert claims_false.email_verified is False


def test_verify_id_token_rejects_hs256_in_the_allowlist(rsa_keypair, jwks_doc, monkeypatch) -> None:
    _patch_jwks(monkeypatch, jwks_doc)
    token = _mint(rsa_keypair)
    with pytest.raises(OIDCError, match="HS"):
        verify_id_token(
            token, issuers={ISSUER}, audience=AUDIENCE, algorithms=["RS256", "HS256"],
            nonce="the-nonce", jwks_uri=f"{ISSUER}/keys",
        )


def test_verify_id_token_rejects_wrong_issuer(rsa_keypair, jwks_doc, monkeypatch) -> None:
    _patch_jwks(monkeypatch, jwks_doc)
    token = _mint(rsa_keypair, issuer="https://not-the-issuer.example")
    with pytest.raises(OIDCError):
        verify_id_token(
            token, issuers={ISSUER}, audience=AUDIENCE, algorithms=["RS256"], nonce="the-nonce",
            jwks_uri=f"{ISSUER}/keys",
        )


def test_verify_id_token_rejects_wrong_audience(rsa_keypair, jwks_doc, monkeypatch) -> None:
    _patch_jwks(monkeypatch, jwks_doc)
    token = _mint(rsa_keypair, audience="somebody-elses-client-id")
    with pytest.raises(OIDCError):
        verify_id_token(
            token, issuers={ISSUER}, audience=AUDIENCE, algorithms=["RS256"], nonce="the-nonce",
            jwks_uri=f"{ISSUER}/keys",
        )


def test_verify_id_token_accepts_audience_as_a_one_element_list(
    rsa_keypair,
    jwks_doc,
    monkeypatch,
) -> None:
    _patch_jwks(monkeypatch, jwks_doc)
    token = _mint(rsa_keypair, audience=[AUDIENCE])
    claims = verify_id_token(
        token, issuers={ISSUER}, audience=AUDIENCE, algorithms=["RS256"], nonce="the-nonce",
        jwks_uri=f"{ISSUER}/keys",
    )
    assert claims.subject == "subject-123"


def test_verify_id_token_rejects_azp_mismatch(rsa_keypair, jwks_doc, monkeypatch) -> None:
    _patch_jwks(monkeypatch, jwks_doc)
    token = _mint(rsa_keypair, extra={"azp": "a-different-client-id"})
    with pytest.raises(OIDCError, match="azp"):
        verify_id_token(
            token, issuers={ISSUER}, audience=AUDIENCE, algorithms=["RS256"], nonce="the-nonce",
            jwks_uri=f"{ISSUER}/keys",
        )


def test_verify_id_token_rejects_nonce_mismatch(rsa_keypair, jwks_doc, monkeypatch) -> None:
    _patch_jwks(monkeypatch, jwks_doc)
    token = _mint(rsa_keypair, nonce="the-real-nonce")
    with pytest.raises(OIDCError, match="nonce"):
        verify_id_token(
            token, issuers={ISSUER}, audience=AUDIENCE, algorithms=["RS256"],
            nonce="an-attacker-supplied-nonce", jwks_uri=f"{ISSUER}/keys",
        )


def test_verify_id_token_rejects_an_unsigned_or_tampered_token(
    rsa_keypair,
    jwks_doc,
    monkeypatch,
) -> None:
    _patch_jwks(monkeypatch, jwks_doc)
    token = _mint(rsa_keypair)
    tampered = token[:-4] + ("A" if token[-4] != "A" else "B") + token[-3:]
    with pytest.raises(OIDCError):
        verify_id_token(
            tampered, issuers={ISSUER}, audience=AUDIENCE, algorithms=["RS256"], nonce="the-nonce",
            jwks_uri=f"{ISSUER}/keys",
        )


def test_verify_id_token_rejects_a_token_from_a_different_keypair(
    rsa_keypair,
    jwks_doc,
    monkeypatch,
) -> None:
    _patch_jwks(monkeypatch, jwks_doc)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    forged = jwt.encode(
        {
            "iss": ISSUER, "aud": AUDIENCE, "sub": "subject-123", "iat": now, "exp": now + 3600,
            "nonce": "the-nonce",
        },
        other_key,
        algorithm="RS256",
        headers={"kid": KID},  # claims the *real* kid, but is signed by an unrelated key
    )
    with pytest.raises(OIDCError):
        verify_id_token(
            forged, issuers={ISSUER}, audience=AUDIENCE, algorithms=["RS256"], nonce="the-nonce",
            jwks_uri=f"{ISSUER}/keys",
        )


def test_jwks_key_caches_and_only_refetches_once_per_cooldown_on_a_miss(
    rsa_keypair, jwks_doc, monkeypatch
) -> None:
    calls = []

    def fake_get_json(url: str) -> dict[str, Any]:
        calls.append(url)
        return jwks_doc

    monkeypatch.setattr(oidc, "_get_json", fake_get_json)
    jwks_uri = f"{ISSUER}/keys"

    oidc.jwks_key(jwks_uri, KID)
    oidc.jwks_key(jwks_uri, KID)
    assert calls == [jwks_uri]  # second call served entirely from cache

    with pytest.raises(OIDCError):
        oidc.jwks_key(jwks_uri, "an-unknown-kid")
    assert calls == [jwks_uri, jwks_uri]  # one forced refresh on the miss

    with pytest.raises(OIDCError, match="too recently"):
        oidc.jwks_key(jwks_uri, "still-unknown")
    assert calls == [jwks_uri, jwks_uri]  # cooldown: no second forced refresh


# --------------------------------------------------------------------------- linking rules


@pytest.fixture()
def linking_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    db_path = tmp_path / "linking.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    stats_config.reset_settings_cache()
    db_module.dispose_engine()
    db_module.init_db()
    factory = db_module.get_sessionmaker()
    with factory() as db:
        yield db
    db_module.dispose_engine()
    stats_config.reset_settings_cache()


def _claims(**overrides: Any) -> Claims:
    base = dict(subject="sub-1", email="ada@example.com", email_verified=True, name="Ada", raw={})
    base.update(overrides)
    return Claims(**base)


def test_rule1_existing_identity_signs_in_as_that_user(linking_db) -> None:
    user = resolve_login(linking_db, "google", _claims())
    linking_db.commit()
    again = resolve_login(linking_db, "google", _claims(email="different@example.com"))
    assert again.user_id == user.user_id  # matched by subject, not by the (now-different) email


def test_rule2_unverified_email_never_touches_email_lookup(linking_db) -> None:
    existing = User(
        user_id="victim", email="ada@example.com", email_lookup="ada@example.com",
        email_verified_at=None, theme_preference="system", profile_json="{}",
        created_at=_now(), updated_at=_now(),
    )
    linking_db.add(existing)
    linking_db.flush()

    attacker_user = resolve_login(
        linking_db, "google",
        _claims(subject="attacker-sub", email="ada@example.com", email_verified=False),
    )
    assert attacker_user.user_id != existing.user_id
    assert attacker_user.email_lookup is None
    assert attacker_user.email_verified_at is None


def test_rule2_private_relay_creates_its_own_account(linking_db) -> None:
    user = resolve_login(
        linking_db, "apple",
        _claims(
            email="abc123@privaterelay.appleid.com", email_verified=True,
            raw={"is_private_email": "true"},
        ),
    )
    assert user.is_private_relay is True
    assert user.email_verified_at is None
    assert user.email == "abc123@privaterelay.appleid.com"


def test_rule3_new_verified_email_creates_a_verified_account(linking_db) -> None:
    user = resolve_login(linking_db, "google", _claims())
    assert user.email_verified_at is not None
    assert user.password_hash is None


def test_rule4_auto_links_a_passwordless_verified_existing_account(linking_db) -> None:
    existing = User(
        user_id="pre-existing", email="ada@example.com", email_lookup="ada@example.com",
        email_verified_at=_now(), password_hash=None, theme_preference="system",
        profile_json="{}", created_at=_now(), updated_at=_now(),
    )
    linking_db.add(existing)
    linking_db.flush()

    linked = resolve_login(linking_db, "google", _claims())
    assert linked.user_id == existing.user_id


def test_rule5_refuses_to_link_over_a_password_account(linking_db) -> None:
    existing = User(
        user_id="has-a-password", email="ada@example.com", email_lookup="ada@example.com",
        email_verified_at=_now(), password_hash="scrypt$1$1$1$c2FsdA==$ZGVyaXZlZA==",
        theme_preference="system", profile_json="{}", created_at=_now(), updated_at=_now(),
    )
    linking_db.add(existing)
    linking_db.flush()

    with pytest.raises(LinkConflict) as excinfo:
        resolve_login(linking_db, "google", _claims())
    assert excinfo.value.kind == "password_account"
    assert excinfo.value.email_masked == "a***@example.com"

    survivors = linking_db.execute(select(UserIdentity)).scalars().all()
    assert survivors == []  # no identity was attached -- refusal, not a silent merge


def test_rule5_refuses_to_link_over_an_unverified_existing_account(linking_db) -> None:
    existing = User(
        user_id="never-verified", email="ada@example.com", email_lookup="ada@example.com",
        email_verified_at=None, password_hash=None, theme_preference="system",
        profile_json="{}", created_at=_now(), updated_at=_now(),
    )
    linking_db.add(existing)
    linking_db.flush()

    with pytest.raises(LinkConflict) as excinfo:
        resolve_login(linking_db, "google", _claims())
    assert excinfo.value.kind == "unverified_account"


def test_rule6_link_to_attaches_an_identity_to_a_signed_in_user(linking_db) -> None:
    user = User(
        user_id="signed-in", email="signedin@example.com", email_lookup="signedin@example.com",
        password_hash="scrypt$1$1$1$c2FsdA==$ZGVyaXZlZA==", theme_preference="system",
        profile_json="{}", created_at=_now(), updated_at=_now(),
    )
    linking_db.add(user)
    linking_db.flush()

    identity = link_to(linking_db, user, "google", _claims())
    assert identity.user_id == user.user_id


def test_rule6_refuses_to_steal_an_identity_owned_by_someone_else(linking_db) -> None:
    owner = resolve_login(linking_db, "google", _claims(subject="taken-subject"))
    linking_db.flush()

    victim = User(
        user_id="wants-to-link", email="other@example.com", email_lookup="other@example.com",
        password_hash="scrypt$1$1$1$c2FsdA==$ZGVyaXZlZA==", theme_preference="system",
        profile_json="{}", created_at=_now(), updated_at=_now(),
    )
    linking_db.add(victim)
    linking_db.flush()

    with pytest.raises(LinkConflict) as excinfo:
        link_to(linking_db, victim, "google", _claims(subject="taken-subject"))
    assert excinfo.value.kind == "identity_taken"

    identity = linking_db.execute(
        select(UserIdentity).where(
            UserIdentity.provider == "google", UserIdentity.subject == "taken-subject"
        )
    ).scalar_one()
    assert identity.user_id == owner.user_id  # unchanged -- never silently re-pointed


def _now():
    from nbastats.db import utcnow

    return utcnow()


# --------------------------------------------------------------------------- §6.5 invariant


def test_only_two_writers_of_email_verified_at() -> None:
    """``email_verified_at`` must be written only by ``linking.resolve_login`` (rules 3-4) and
    by the routes that call ``tokens.consume(db, "verify"|"email_change", ...)``
    (``routes_auth.py`` today; ``routes_me.py`` once WP2 lands it) — never anywhere else. See
    ``accounts/linking.py``'s module docstring and ``WEB_DESIGN.md`` §6.5.
    """
    package_root = Path(__file__).resolve().parents[1] / "nbastats"
    allowed = {
        package_root / "accounts" / "linking.py",
        package_root / "api" / "routes_auth.py",
        package_root / "api" / "routes_me.py",
    }
    assignment = re.compile(r"\bemail_verified_at\b\s*=(?!=)")

    offenders: list[str] = []
    hits_by_file: dict[Path, int] = {}
    for path in package_root.rglob("*.py"):
        if path.name == "models.py":
            continue  # the column *declaration*, not a write
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "mapped_column" in line:
                continue
            if assignment.search(line):
                hits_by_file[path] = hits_by_file.get(path, 0) + 1
                if path not in allowed:
                    offenders.append(f"{path}:{lineno}: {line.strip()}")

    assert not offenders, (
        "email_verified_at must be written only by accounts/linking.py (rules 3-4) and by "
        "routes_auth.py / routes_me.py (after tokens.consume('verify'|'email_change', ...)); "
        "found an unexpected writer:\n" + "\n".join(offenders)
    )
    # Guard against the assertion above being vacuously true because nothing writes it at all.
    assert hits_by_file.get(package_root / "accounts" / "linking.py", 0) >= 1
    assert hits_by_file.get(package_root / "api" / "routes_auth.py", 0) >= 1


# --------------------------------------------------------------------------- provider_status


def test_provider_status_reflects_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from nbastats.accounts.providers import provider_status

    monkeypatch.delenv("HARDWOOD_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("HARDWOOD_GOOGLE_CLIENT_SECRET", raising=False)
    auth_config.reset_auth_settings_cache()
    disabled = provider_status(auth_config.get_auth_settings())
    assert disabled["google"]["enabled"] is False
    assert disabled["google"]["reason"]

    monkeypatch.setenv("HARDWOOD_GOOGLE_CLIENT_ID", "abc")
    monkeypatch.setenv("HARDWOOD_GOOGLE_CLIENT_SECRET", "def")
    auth_config.reset_auth_settings_cache()
    enabled = provider_status(auth_config.get_auth_settings())
    assert enabled["google"] == {"enabled": True, "reason": None}
    auth_config.reset_auth_settings_cache()


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "/"),
        ("", "/"),
        ("/d/abc", "/d/abc"),
        ("//evil.com", "/"),
        ("/\\evil.com", "/"),
        ("/%5Cevil.com", "/"),
        ("/%2f%2fevil.com", "/"),
        ("/%09//evil.com", "/"),
        ("https://evil.com", "/"),
        ("/legit?next=/other", "/legit?next=/other"),
    ],
)
def test_safe_next(raw: str | None, expected: str) -> None:
    from nbastats.accounts.providers import safe_next

    assert safe_next(raw) == expected


# --------------------------------------------------------------------------- Google end-to-end


@pytest.fixture()
def oauth_client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    db_path = tmp_path / "oauth.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("HARDWOOD_GOOGLE_CLIENT_ID", AUDIENCE)
    monkeypatch.setenv("HARDWOOD_GOOGLE_CLIENT_SECRET", "test-google-secret")
    stats_config.reset_settings_cache()
    auth_config.reset_auth_settings_cache()
    db_module.dispose_engine()
    db_module.init_db()
    routes_auth.reset_auth_route_limiters()

    app = FastAPI()
    api_errors.install_error_handlers(app)
    app.include_router(routes_auth.router, prefix="/v1")
    client = TestClient(app)
    try:
        yield client
    finally:
        db_module.dispose_engine()
        auth_config.reset_auth_settings_cache()
        stats_config.reset_settings_cache()


def test_google_start_sets_a_transaction_and_an_oauth_cookie(
    oauth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        routes_auth, "discovery",
        lambda issuer: {"authorization_endpoint": "https://accounts.google.test/authorize"},
    )
    response = oauth_client.get("/v1/auth/google/start?next=/d/abc", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("https://accounts.google.test/authorize?")
    assert "hw_oauth" in response.cookies

    factory = db_module.get_sessionmaker()
    with factory() as db:
        (txn,) = db.execute(select(OAuthTransaction)).scalars().all()
        assert txn.provider == "google"
        assert txn.next_path == "/d/abc"
        assert txn.intent == "signin"


def test_google_start_sanitises_an_open_redirect_attempt(
    oauth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        routes_auth, "discovery",
        lambda issuer: {"authorization_endpoint": "https://accounts.google.test/authorize"},
    )
    oauth_client.get("/v1/auth/google/start?next=//evil.com", follow_redirects=False)
    factory = db_module.get_sessionmaker()
    with factory() as db:
        (txn,) = db.execute(select(OAuthTransaction)).scalars().all()
        assert txn.next_path == "/"


def test_google_callback_completes_sign_in(
    oauth_client: TestClient, monkeypatch: pytest.MonkeyPatch, rsa_keypair, jwks_doc
) -> None:
    _patch_jwks(monkeypatch, jwks_doc)
    monkeypatch.setattr(
        routes_auth, "discovery",
        lambda issuer: {
            "authorization_endpoint": "https://accounts.google.test/authorize",
            "token_endpoint": "https://accounts.google.test/token",
            "jwks_uri": f"{ISSUER}/keys",
        },
    )
    start = oauth_client.get("/v1/auth/google/start", follow_redirects=False)
    handle_cookie = start.cookies.get("hw_oauth")

    factory = db_module.get_sessionmaker()
    with factory() as db:
        (txn,) = db.execute(select(OAuthTransaction)).scalars().all()
        state = _find_query_param(start.headers["location"], "state")

    id_token = _mint(
        rsa_keypair, nonce=_find_query_param(start.headers["location"], "nonce"),
        audience=AUDIENCE, issuer=google_provider.ISSUER,
    )

    def fake_post(url, data=None, timeout=None, **kwargs):
        class _Resp:
            def raise_for_status(self_inner) -> None:
                return None

            def json(self_inner) -> dict[str, Any]:
                return {"id_token": id_token}

        return _Resp()

    monkeypatch.setattr(routes_auth.httpx, "post", fake_post)

    oauth_client.cookies.set("hw_oauth", handle_cookie)
    callback = oauth_client.get(
        f"/v1/auth/google/callback?code=fake-code&state={state}", follow_redirects=False
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == "http://127.0.0.1:8000/"
    assert "hw_session" in callback.cookies
    # hw_oauth is cleared regardless of outcome.
    assert callback.cookies.get("hw_oauth") in (None, "")

    with factory() as db:
        user = db.execute(select(User)).scalar_one()
        assert user.email == "ada@example.com"
        assert user.email_verified_at is not None
        identity = db.execute(select(UserIdentity)).scalar_one()
        assert identity.provider == "google"


def test_google_callback_rejects_a_state_mismatch(oauth_client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        routes_auth, "discovery",
        lambda issuer: {"authorization_endpoint": "https://accounts.google.test/authorize"},
    )
    start = oauth_client.get("/v1/auth/google/start", follow_redirects=False)
    handle_cookie = start.cookies.get("hw_oauth")
    oauth_client.cookies.set("hw_oauth", handle_cookie)

    response = oauth_client.get(
        "/v1/auth/google/callback?code=fake-code&state=not-the-real-state"
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "oauth_state_mismatch"


def test_google_callback_without_a_transaction_cookie_fails_cleanly(
    oauth_client: TestClient,
) -> None:
    response = oauth_client.get("/v1/auth/google/callback?code=x&state=y")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "oauth_transaction_missing"


def test_google_callback_link_conflict_redirects_without_a_session(
    oauth_client: TestClient, monkeypatch: pytest.MonkeyPatch, rsa_keypair, jwks_doc
) -> None:
    _patch_jwks(monkeypatch, jwks_doc)
    factory = db_module.get_sessionmaker()
    with factory() as db:
        db.add(
            User(
                user_id="has-a-password", email="ada@example.com", email_lookup="ada@example.com",
                email_verified_at=_now(), password_hash="scrypt$1$1$1$c2FsdA==$ZGVyaXZlZA==",
                theme_preference="system", profile_json="{}", created_at=_now(), updated_at=_now(),
            )
        )
        db.commit()

    monkeypatch.setattr(
        routes_auth, "discovery",
        lambda issuer: {
            "authorization_endpoint": "https://accounts.google.test/authorize",
            "token_endpoint": "https://accounts.google.test/token",
            "jwks_uri": f"{ISSUER}/keys",
        },
    )
    start = oauth_client.get("/v1/auth/google/start", follow_redirects=False)
    handle_cookie = start.cookies.get("hw_oauth")
    state = _find_query_param(start.headers["location"], "state")
    nonce = _find_query_param(start.headers["location"], "nonce")
    id_token = _mint(rsa_keypair, nonce=nonce, audience=AUDIENCE, issuer=google_provider.ISSUER)

    def fake_post(url, data=None, timeout=None, **kwargs):
        class _Resp:
            def raise_for_status(self_inner):
                return None

            def json(self_inner):
                return {"id_token": id_token}

        return _Resp()

    monkeypatch.setattr(routes_auth.httpx, "post", fake_post)
    oauth_client.cookies.set("hw_oauth", handle_cookie)

    response = oauth_client.get(
        f"/v1/auth/google/callback?code=fake-code&state={state}", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/auth/link-conflict?provider=google")
    assert "reason=password_account" in response.headers["location"]
    assert "hw_session" not in response.cookies


def _find_query_param(url: str, name: str) -> str:
    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(url).query)
    return query[name][0]
