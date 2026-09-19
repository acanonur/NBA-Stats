"""Regression tests for the account-layer defects an adversarial review of WP1/WP2 turned up.

Each test here names the behaviour that was wrong and asserts the behaviour that replaces it, so
a future edit that reintroduces one of them fails loudly rather than quietly. They live in their
own module because they cut across ``routes_auth``, ``routes_me``, ``accounts.sessions``,
``accounts.csrf``, ``accounts.store`` and ``api.security`` — grouping them by the *bug* rather
than by the file is what makes the set readable as a checklist.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.requests import Request

from nbastats import config as stats_config
from nbastats import db as db_module
from nbastats.accounts import config as auth_config
from nbastats.accounts import csrf as csrf_module
from nbastats.accounts import sessions as sessions_module
from nbastats.accounts import store
from nbastats.accounts.models import (
    AuthSession,
    AuthToken,
    OAuthTransaction,
    User,
    UserIdentity,
    WebInvite,
)
from nbastats.api import errors as api_errors
from nbastats.api import routes_auth, routes_me
from nbastats.api import security as security_module
from nbastats.db import utcnow

ORIGIN = {"Origin": "http://127.0.0.1:8000"}
PASSWORD = "a reasonable password"


# --------------------------------------------------------------------------- fixtures


def _build_app() -> FastAPI:
    app = FastAPI()
    api_errors.install_error_handlers(app)
    app.include_router(routes_auth.router, prefix="/v1")
    app.include_router(routes_me.router, prefix="/v1")
    return app


@pytest.fixture()
def auth_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_path = tmp_path / "hardening.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("HARDWOOD_SIGNUP_MODE", "open")
    monkeypatch.delenv("HARDWOOD_INVITE_CODE", raising=False)
    stats_config.reset_settings_cache()
    db_module.dispose_engine()
    auth_config.reset_auth_settings_cache()
    db_module.init_db()
    routes_auth.reset_auth_route_limiters()
    routes_me.reset_me_route_limiters()
    yield
    db_module.dispose_engine()
    auth_config.reset_auth_settings_cache()
    stats_config.reset_settings_cache()


@pytest.fixture()
def client(auth_db: None) -> TestClient:
    return TestClient(_build_app())


def _signup_and_login(client: TestClient, email: str, password: str = PASSWORD) -> str:
    signup = client.post(
        "/v1/auth/signup", json={"email": email, "password": password}, headers=ORIGIN
    )
    assert signup.status_code == 202, signup.text
    login = client.post(
        "/v1/auth/login", json={"email": email, "password": password}, headers=ORIGIN
    )
    assert login.status_code == 200, login.text
    return login.json()["csrfToken"]


def _headers(csrf: str) -> dict[str, str]:
    return {**ORIGIN, "X-Hardwood-CSRF": csrf}


def _set_cookies(response: Any) -> list[str]:
    return [value for key, value in response.headers.items() if key.lower() == "set-cookie"]


def _request(**headers: str) -> Request:
    raw = [(key.replace("_", "-").encode("latin-1"), value.encode("latin-1"))
           for key, value in headers.items()]
    return Request(
        {
            "type": "http", "method": "GET", "path": "/", "query_string": b"",
            "headers": raw, "client": ("127.0.0.1", 1234), "scheme": "http",
            "server": ("127.0.0.1", 8000),
        }
    )


# ------------------------------------------- the single-response cookie rule (findings 2, 13)


def test_changing_your_password_keeps_you_signed_in(client: TestClient) -> None:
    """``POST /v1/me/password`` revoked every session including the caller's, then wrote the
    replacement cookie onto FastAPI's *injected* sub-response — which is discarded whenever the
    handler returns a ``Response``. The answer was 200 with no ``Set-Cookie`` at all and every
    subsequent request 401'd, with the SPA holding a CSRF token for a session it never got."""
    csrf = _signup_and_login(client, "ada@example.com")
    response = client.post(
        "/v1/me/password",
        json={"currentPassword": PASSWORD, "newPassword": "a brand new password"},
        headers=_headers(csrf),
    )
    assert response.status_code == 200, response.text
    assert any("hw_session=" in value for value in _set_cookies(response)), response.headers

    assert client.get("/v1/me").status_code == 200
    new_csrf = response.json()["csrfToken"]
    assert client.patch(
        "/v1/me", json={"displayName": "Ada"}, headers=_headers(new_csrf)
    ).status_code == 200


def test_changing_your_password_revokes_other_sessions_only(client: TestClient) -> None:
    csrf = _signup_and_login(client, "ada@example.com")
    other = TestClient(client.app)
    _signup_and_login(other, "ada@example.com")  # second browser, same account

    assert client.post(
        "/v1/me/password",
        json={"currentPassword": PASSWORD, "newPassword": "a brand new password"},
        headers=_headers(csrf),
    ).status_code == 200

    assert client.get("/v1/me").status_code == 200  # this browser survives
    assert other.get("/v1/me").status_code == 401  # the other one does not


def test_delete_me_soft_deletes_and_clears_the_cookie(client: TestClient) -> None:
    """§4.2: "soft-deletes, revokes every session, clears cookies; ``admin.py purge``
    hard-deletes after 30 days". The route used to call ``delete_user`` inline, erasing the row
    immediately — no recovery window, a dead ``cmd_purge`` branch, and an ``email_lookup`` freed
    for anyone to re-register the same second."""
    csrf = _signup_and_login(client, "ada@example.com")
    response = client.request(
        "DELETE", "/v1/me", json={"confirm": "DELETE"}, headers=_headers(csrf)
    )
    assert response.status_code == 204
    assert any("hw_session=" in value for value in _set_cookies(response)), response.headers
    assert client.get("/v1/me").status_code == 401

    factory = db_module.get_sessionmaker()
    with factory() as db:
        user = db.execute(select(User)).scalar_one()
        assert user.deleted_at is not None
        assert user.is_disabled is True
        assert db.execute(select(AuthSession)).scalars().all() != []  # revoked, not erased
        assert all(row.revoked_at is not None for row in db.execute(select(AuthSession)).scalars())


def test_purge_hard_deletes_a_soft_deleted_account(client: TestClient) -> None:
    """The 30-day branch in ``admin.cmd_purge`` was unreachable while the route erased rows
    itself; with the soft delete restored it is the one erasure path, and it goes through
    ``accounts.models.delete_user``."""
    from nbastats.accounts.models import delete_user

    csrf = _signup_and_login(client, "ada@example.com")
    client.post("/v1/dashboards", json={"presetKey": "daily_recap"}, headers=_headers(csrf))
    client.request("DELETE", "/v1/me", json={"confirm": "DELETE"}, headers=_headers(csrf))

    factory = db_module.get_sessionmaker()
    with factory() as db:
        user = db.execute(select(User)).scalar_one()
        user.deleted_at = utcnow() - timedelta(days=31)
        db.add(user)
        db.commit()

        cutoff = utcnow() - timedelta(days=30)
        stale = db.execute(
            select(User).where(User.deleted_at.isnot(None), User.deleted_at < cutoff)
        ).scalars().all()
        assert len(stale) == 1, "cmd_purge's stale-user query must be able to find the row"
        delete_user(db, stale[0].user_id)
        db.commit()
        assert db.execute(select(User)).scalars().all() == []


# ------------------------------------------------- email change (findings 2, 18, 20)


def _mint_email_change(client: TestClient, csrf: str, new_email: str, monkeypatch) -> str:
    sent: list[str] = []
    monkeypatch.setattr(
        routes_me.mail, "send_email_change_email", lambda to, link: sent.append(link)
    )
    response = client.post(
        "/v1/me/email",
        json={"newEmail": new_email, "currentPassword": PASSWORD},
        headers=_headers(csrf),
    )
    assert response.status_code == 202, response.text
    assert sent, "the confirmation mail must still be dispatched, just not inline"
    return sent[0].rsplit("token=", 1)[1]


def test_confirming_an_email_change_keeps_the_current_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    csrf = _signup_and_login(client, "ada@example.com")
    other = TestClient(client.app)
    _signup_and_login(other, "ada@example.com")

    token = _mint_email_change(client, csrf, "ada2@example.com", monkeypatch)
    confirmed = client.get(f"/v1/me/email/confirm?token={token}", follow_redirects=False)
    assert confirmed.status_code == 303
    assert confirmed.headers["location"].endswith("/settings?emailChanged=1")

    me = client.get("/v1/me")
    assert me.status_code == 200, "confirming your own address must not sign you out"
    assert me.json()["email"] == "ada2@example.com"
    assert other.get("/v1/me").status_code == 401  # every *other* session is revoked


def test_confirming_an_email_change_that_was_taken_meanwhile_is_not_a_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``users.email_lookup`` is UNIQUE, and the collision check only ran at mint time. A
    stale token therefore raised an unhandled ``IntegrityError`` whose rollback also undid
    ``tokens.consume``, so the link stayed "valid" and 500'd forever."""
    csrf = _signup_and_login(client, "ada@example.com")
    token = _mint_email_change(client, csrf, "shared@example.com", monkeypatch)

    squatter = TestClient(client.app)
    _signup_and_login(squatter, "shared@example.com")

    confirmed = client.get(f"/v1/me/email/confirm?token={token}", follow_redirects=False)
    assert confirmed.status_code == 303
    assert confirmed.headers["location"].endswith("/settings?emailTaken=1")
    assert client.get("/v1/me").json()["email"] == "ada@example.com"


def test_change_email_dispatches_mail_after_the_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both branches must cost the same wall clock, so the send is a background task rather
    than an inline SMTP round trip on the free-address branch only."""
    import inspect

    source = inspect.getsource(routes_me.change_email)
    assert "background_tasks.add_task" in source
    assert "mail.send_email_change_email(" not in source


# ------------------------------------------------------------- unlink rotation (finding 17)


def test_unlinking_a_provider_rotates_and_evicts_other_sessions(client: TestClient) -> None:
    csrf = _signup_and_login(client, "ada@example.com")
    factory = db_module.get_sessionmaker()
    with factory() as db:
        user = db.execute(select(User)).scalar_one()
        db.add(
            UserIdentity(
                identity_id="i" * 32, user_id=user.user_id, provider="google",
                subject="sub-1", email_at_link="ada@example.com", email_verified=True,
                created_at=utcnow(),
            )
        )
        db.commit()

    attacker = TestClient(client.app)
    attacker.cookies.update(client.cookies)  # a session somebody else got hold of
    assert attacker.get("/v1/me").status_code == 200

    response = client.request("DELETE", "/v1/me/identities/google", headers=_headers(csrf))
    assert response.status_code == 200, response.text
    assert any("hw_session=" in value for value in _set_cookies(response))
    assert attacker.get("/v1/me").status_code == 401, (
        "removing the provider is the one remediation the UI offers; it has to evict the "
        "session an attacker was holding"
    )


# --------------------------------------------------------------- PATCH /v1/me (finding 16)


def test_patch_me_can_clear_a_favourite_with_an_explicit_null(client: TestClient) -> None:
    csrf = _signup_and_login(client, "ada@example.com")
    assert client.patch(
        "/v1/me", json={"favoritePlayerId": 2544}, headers=_headers(csrf)
    ).json()["favoritePlayerId"] == 2544
    cleared = client.patch("/v1/me", json={"favoritePlayerId": None}, headers=_headers(csrf))
    assert cleared.json()["favoritePlayerId"] is None
    # An omitted key still leaves the stored value alone.
    client.patch("/v1/me", json={"favoriteTeamId": 1610612747}, headers=_headers(csrf))
    kept = client.patch("/v1/me", json={"displayName": "Ada"}, headers=_headers(csrf))
    assert kept.json()["favoriteTeamId"] == 1610612747


def test_patch_me_refuses_a_dashboard_this_account_does_not_own(client: TestClient) -> None:
    csrf = _signup_and_login(client, "ada@example.com")
    response = client.patch(
        "/v1/me", json={"selectedDashboardId": "somebody-elses-layout"}, headers=_headers(csrf)
    )
    assert response.status_code == 400
    assert response.json()["error"]["field"] == "selectedDashboardId"

    created = client.post(
        "/v1/dashboards", json={"presetKey": "daily_recap"}, headers=_headers(csrf)
    )
    layout_id = created.json()["layout"]["id"]
    ok = client.patch(
        "/v1/me", json={"selectedDashboardId": layout_id}, headers=_headers(csrf)
    )
    assert ok.status_code == 200
    assert ok.json()["selectedDashboardId"] == layout_id


# ------------------------------------------------------------------ import cap (finding 12)


def test_import_over_the_byte_cap_is_a_413(client: TestClient) -> None:
    csrf = _signup_and_login(client, "ada@example.com")
    filler = "x" * 4096
    envelope = {
        "schemaVersion": 1,
        "layouts": [
            {"id": f"big-{index}", "name": filler, "widgets": []}
            for index in range(store.MAX_IMPORT_BYTES // 4200 + 40)
        ],
    }
    body = json.dumps(envelope)
    assert len(body) > store.MAX_IMPORT_BYTES
    response = client.post(
        "/v1/dashboards/import",
        content=body,
        headers={**_headers(csrf), "Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_import_still_works_under_the_cap(client: TestClient) -> None:
    csrf = _signup_and_login(client, "ada@example.com")
    envelope = {"schemaVersion": 1, "layouts": [{"id": "l1", "name": "Board", "widgets": []}]}
    response = client.post("/v1/dashboards/import", json=envelope, headers=_headers(csrf))
    assert response.status_code == 200, response.text
    assert [row["layoutId"] for row in response.json()["imported"]] == ["l1"]


# --------------------------------------------------------------- cache headers (finding 11)


def test_dashboard_responses_are_never_cacheable() -> None:
    for path in ("/v1/dashboards", "/v1/dashboards/abc", "/v1/dashboards/export"):
        headers = dict(security_module.security_headers_for(path))
        assert headers[b"cache-control"] == b"no-store", path
        assert headers[b"vary"] == b"Cookie", path


def test_dashboard_export_carries_no_store_end_to_end(client: TestClient) -> None:
    csrf = _signup_and_login(client, "ada@example.com")
    client.post("/v1/dashboards", json={"presetKey": "daily_recap"}, headers=_headers(csrf))
    app = _build_app()
    app.add_middleware(security_module.SecurityHeadersMiddleware)
    mounted = TestClient(app)
    mounted.cookies.update(client.cookies)
    response = mounted.get("/v1/dashboards/export")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


# ------------------------------------------------------------------ Origin check (finding 7)


def test_localhost_origin_is_accepted_on_a_loopback_deployment(client: TestClient) -> None:
    """``http://localhost:8000`` is the spelling most people type against the default
    ``http://127.0.0.1:8000``; rejecting it made every write in the product fail with advice
    ("reload the page") that could never work."""
    for origin in ("http://localhost:8000", "http://127.0.0.1:8000"):
        response = client.post(
            "/v1/auth/signup",
            json={"email": f"ada-{origin[7:12]}@example.com", "password": PASSWORD},
            headers={"Origin": origin},
        )
        assert response.status_code == 202, (origin, response.text)


def test_a_foreign_origin_is_still_rejected(client: TestClient) -> None:
    response = client.post(
        "/v1/auth/login",
        json={"email": "ada@example.com", "password": PASSWORD},
        headers={"Origin": "https://evil.test"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


def test_a_non_loopback_deployment_keeps_exact_host_matching(
    auth_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HARDWOOD_PUBLIC_BASE_URL", "https://hardwood.example.com")
    auth_config.reset_auth_settings_cache()
    csrf_module.check_origin(_request(origin="https://hardwood.example.com"))
    with pytest.raises(Exception):
        csrf_module.check_origin(_request(origin="http://localhost:8000"))


# ------------------------------------------------------ cookie names / prefixes (finding 3)


def test_the_session_cookie_is_read_under_one_name_when_secure(
    auth_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``__Host-`` exists to stop a sibling host writing our cookie. Reading the unprefixed
    name as a fallback while ``Secure`` is on threw that away entirely."""
    monkeypatch.setenv("HARDWOOD_PUBLIC_BASE_URL", "https://hardwood.example.com")
    auth_config.reset_auth_settings_cache()
    assert sessions_module.read_session_cookie(_request(cookie="hw_session=planted")) is None
    assert (
        sessions_module.read_session_cookie(_request(cookie="__Host-hw_session=ours")) == "ours"
    )

    monkeypatch.setenv("HARDWOOD_PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    auth_config.reset_auth_settings_cache()
    assert sessions_module.read_session_cookie(_request(cookie="hw_session=ours")) == "ours"


def test_the_oauth_handle_is_read_under_one_name_when_secure(
    auth_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HARDWOOD_PUBLIC_BASE_URL", "https://hardwood.example.com")
    auth_config.reset_auth_settings_cache()
    settings = auth_config.get_auth_settings()

    handle = "a-handle"
    now = utcnow()
    factory = db_module.get_sessionmaker()
    with factory() as db:
        db.add(
            OAuthTransaction(
                handle_hash=routes_auth._hash_hex(handle), provider="google",
                state_hash="s" * 64, nonce="n", code_verifier="v",
                redirect_uri="https://hardwood.example.com/v1/auth/google/callback",
                next_path="/", intent="signin", link_user_id=None,
                created_at=now, expires_at=now + timedelta(seconds=600),
            )
        )
        db.commit()

    with factory() as db:
        planted = routes_auth._consume_transaction(
            db, _request(cookie=f"hw_oauth={handle}"), settings
        )
        assert planted is None, "an unprefixed handle must not be honoured over https"
        ours = routes_auth._consume_transaction(
            db, _request(cookie=f"__Host-hw_oauth={handle}"), settings
        )
        assert ours is not None


# --------------------------------------------------------------- CSRF stability (finding 6)


def test_the_csrf_token_is_derived_and_stable(client: TestClient) -> None:
    csrf = _signup_and_login(client, "ada@example.com")
    factory = db_module.get_sessionmaker()
    with factory() as db:
        before = db.execute(select(AuthSession.csrf_hash)).scalars().all()
    for _ in range(3):
        assert client.get("/v1/auth/session").json()["csrfToken"] == csrf
    with factory() as db:
        assert db.execute(select(AuthSession.csrf_hash)).scalars().all() == before


def test_a_cross_site_get_cannot_break_an_open_tab(client: TestClient) -> None:
    csrf = _signup_and_login(client, "ada@example.com")
    attacker_navigation = TestClient(client.app)
    attacker_navigation.cookies.update(client.cookies)
    for _ in range(3):
        attacker_navigation.get(
            "/v1/auth/session",
            headers={"Origin": "https://evil.test", "Sec-Fetch-Site": "cross-site"},
        )
    assert client.patch(
        "/v1/me", json={"displayName": "Ada"}, headers=_headers(csrf)
    ).status_code == 200


# ----------------------------------------------------------------------- sweep (finding 19)


def test_sweep_prunes_oauth_transactions_and_tokens(auth_db: None) -> None:
    factory = db_module.get_sessionmaker()
    past = utcnow() - timedelta(hours=1)
    with factory() as db:
        user = User(
            user_id="u" * 32, email="ada@example.com", email_lookup="ada@example.com",
            theme_preference="system", profile_json="{}",
            created_at=utcnow(), updated_at=utcnow(),
        )
        db.add(user)
        db.add(
            OAuthTransaction(
                handle_hash="h" * 64, provider="google", state_hash="s" * 64, nonce="n",
                code_verifier="a-plaintext-pkce-verifier",
                redirect_uri="http://127.0.0.1:8000/v1/auth/google/callback",
                next_path="/", intent="signin", link_user_id=None,
                created_at=past, expires_at=past,
            )
        )
        db.add(
            AuthToken(
                token_id="t" * 32, user_id=user.user_id, purpose="reset",
                token_hash="x" * 64, payload_json=None, created_at=past, expires_at=past,
            )
        )
        db.commit()
        assert sessions_module.sweep(db) == 2
        db.commit()
        assert db.execute(select(OAuthTransaction)).scalars().all() == []
        assert db.execute(select(AuthToken)).scalars().all() == []


# --------------------------------------------------------------- invite handling (4 and 8)


def _invite_client(monkeypatch: pytest.MonkeyPatch, **env: str) -> TestClient:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    auth_config.reset_auth_settings_cache()
    routes_auth.reset_auth_route_limiters()
    return TestClient(_build_app())


def test_open_mode_enforces_the_configured_invite_code(
    auth_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``startup_refusals`` sells ``HARDWOOD_INVITE_CODE`` as the mandatory backstop for a
    public ``open`` deployment, but nothing in the request path had ever compared anything to
    it: any stranger got a real account."""
    client = _invite_client(
        monkeypatch, HARDWOOD_SIGNUP_MODE="open", HARDWOOD_INVITE_CODE="the-backstop-code"
    )
    assert client.post(
        "/v1/auth/signup", json={"email": "a@example.com", "password": PASSWORD}, headers=ORIGIN
    ).status_code == 400
    assert client.post(
        "/v1/auth/signup",
        json={"email": "b@example.com", "password": PASSWORD, "inviteCode": "wrong"},
        headers=ORIGIN,
    ).status_code == 400
    assert client.post(
        "/v1/auth/signup",
        json={"email": "c@example.com", "password": PASSWORD,
              "inviteCode": "the-backstop-code"},
        headers=ORIGIN,
    ).status_code == 202

    factory = db_module.get_sessionmaker()
    with factory() as db:
        assert [u.email for u in db.execute(select(User)).scalars()] == ["c@example.com"]


def test_invite_mode_accepts_the_shared_code_as_well_as_a_row(
    auth_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _invite_client(
        monkeypatch, HARDWOOD_SIGNUP_MODE="invite", HARDWOOD_INVITE_CODE="the-backstop-code"
    )
    assert client.post(
        "/v1/auth/signup",
        json={"email": "c@example.com", "password": PASSWORD,
              "inviteCode": "the-backstop-code"},
        headers=ORIGIN,
    ).status_code == 202


def test_an_invite_is_burned_whether_or_not_the_address_was_free(
    auth_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Burning the code only when an account was created leaked exactly the bit §4.9 spends the
    whole route hiding: reuse the code and a 202 means the probed address existed, a 400 means
    it did not."""
    client = _invite_client(monkeypatch, HARDWOOD_SIGNUP_MODE="invite")
    factory = db_module.get_sessionmaker()
    with factory() as db:
        db.add(User(
            user_id="u" * 32, email="taken@example.com", email_lookup="taken@example.com",
            theme_preference="system", profile_json="{}",
            created_at=utcnow(), updated_at=utcnow(),
        ))
        for code in ("CODE-A", "CODE-B"):
            db.add(WebInvite(code=code))
        db.commit()

    def _signup(email: str, code: str) -> int:
        return client.post(
            "/v1/auth/signup",
            json={"email": email, "password": PASSWORD, "inviteCode": code},
            headers=ORIGIN,
        ).status_code

    assert _signup("taken@example.com", "CODE-A") == 202  # collision branch
    assert _signup("throwaway@example.com", "CODE-A") == 400  # CODE-A was burned anyway
    assert _signup("free@example.com", "CODE-B") == 202  # new-account branch
    assert _signup("throwaway2@example.com", "CODE-B") == 400

    with factory() as db:
        burned = {row.code: row.used_by for row in db.execute(select(WebInvite)).scalars()}
    assert set(burned) == {"CODE-A", "CODE-B"}
    assert burned["CODE-A"] is None, "nobody redeemed it; it is spent all the same"
    assert burned["CODE-B"] is not None


# ---------------------------------------------------------- provider linking (findings 9, 10)


def test_apple_authorize_url_carries_a_pkce_challenge(
    auth_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_exchange_apple_code`` sends a ``code_verifier``; RFC 7636 §4.6 makes a redemption that
    presents one for a code issued with no challenge a failure, so the pair has to match."""
    settings = auth_config.get_auth_settings()
    url = routes_auth._authorize_url(
        "apple", settings, state="st", nonce="no", code_challenge="the-challenge",
        redirect_uri="https://hardwood.example.com/v1/auth/apple/callback",
    )
    assert "code_challenge=the-challenge" in url
    assert "code_challenge_method=S256" in url


def test_link_start_builds_a_link_intent_transaction(
    auth_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without this route ``_finish_oauth``'s ``intent == "link"`` branch and ``linking.link_to``
    were unreachable, and rule 5's "sign in with your password first, then link Google from
    Settings" named an action the API did not expose."""
    monkeypatch.setenv("HARDWOOD_GOOGLE_CLIENT_ID", "a-client-id")
    monkeypatch.setenv("HARDWOOD_GOOGLE_CLIENT_SECRET", "a-secret")
    auth_config.reset_auth_settings_cache()
    monkeypatch.setattr(
        routes_auth, "discovery",
        lambda issuer: {"authorization_endpoint": "https://accounts.google.test/authorize"},
    )
    client = TestClient(_build_app())
    csrf = _signup_and_login(client, "ada@example.com")

    unprotected = client.post("/v1/me/identities/google/start", headers=ORIGIN)
    assert unprotected.status_code == 403  # CSRF is mandatory: §6.4's link-capture attack

    response = client.post("/v1/me/identities/google/start", headers=_headers(csrf))
    assert response.status_code == 200, response.text
    assert response.json()["redirectUrl"].startswith("https://accounts.google.test/authorize?")
    assert any("hw_oauth=" in value for value in _set_cookies(response))

    factory = db_module.get_sessionmaker()
    with factory() as db:
        (txn,) = db.execute(select(OAuthTransaction)).scalars().all()
        user = db.execute(select(User)).scalar_one()
        assert txn.intent == "link"
        assert txn.link_user_id == user.user_id
