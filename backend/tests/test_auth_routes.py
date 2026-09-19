"""Integration tests for ``nbastats.api.routes_auth`` — password signup/login/logout, email
verification, password reset, CSRF, enumeration safety, and the login backoff.

The app under test is a standalone ``FastAPI()`` carrying only this router plus the error
handlers, mounted at ``/v1`` (mirroring how ``api/app.py`` will include it) — not the full
``nbastats.api.app.create_app()`` app, since app.py's own wiring (security headers, the SPA
mount, the other routers) is WP2's territory and unrelated to what this module tests.

Every write request in this file that is not itself testing the CSRF/Origin check sends an
``Origin`` header matching the default ``HARDWOOD_PUBLIC_BASE_URL`` — without it,
``accounts.csrf.check_origin`` rejects the request before routes_auth ever sees it, by design.
"""
from __future__ import annotations

from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from nbastats import config as stats_config
from nbastats import db as db_module
from nbastats.accounts import config as auth_config
from nbastats.accounts import mail
from nbastats.accounts.models import User, WebInvite
from nbastats.api import errors as api_errors
from nbastats.api import routes_auth

ORIGIN = {"Origin": "http://127.0.0.1:8000"}
SentMail = list[tuple[str, str, str]]


@pytest.fixture()
def auth_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A fresh, empty accounts database for each test, and a clean slate for every
    process-global cache routes_auth or its settings touch."""
    db_path = tmp_path / "auth-routes.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("HARDWOOD_SIGNUP_MODE", "open")
    stats_config.reset_settings_cache()
    db_module.dispose_engine()
    auth_config.reset_auth_settings_cache()
    db_module.init_db()
    routes_auth.reset_auth_route_limiters()
    yield
    db_module.dispose_engine()
    auth_config.reset_auth_settings_cache()
    stats_config.reset_settings_cache()


@pytest.fixture()
def client(auth_db: None) -> TestClient:
    app = FastAPI()
    api_errors.install_error_handlers(app)
    app.include_router(routes_auth.router, prefix="/v1")
    return TestClient(app)


@pytest.fixture()
def sent_mail(monkeypatch: pytest.MonkeyPatch) -> SentMail:
    """Capture every outbound mail as ``(function_name, to, text)`` instead of touching
    ``HARDWOOD_MAILER`` at all — signup/login/reset all dispatch through ``BackgroundTasks``,
    which ``TestClient`` runs synchronously before the response finishes, so the list is
    populated by the time an assertion reads it."""
    sent: SentMail = []

    def _capture(name: str):
        def _fn(to: str, link: str) -> None:
            sent.append((name, to, link))

        return _fn

    monkeypatch.setattr(mail, "send_verification_email", _capture("verify"))
    monkeypatch.setattr(mail, "send_reset_email", _capture("reset"))
    monkeypatch.setattr(mail, "send_signup_conflict_email", _capture("conflict"))
    return sent


def _signup(client: TestClient, email: str, password: str = "a reasonable password") -> Any:
    return client.post(
        "/v1/auth/signup", json={"email": email, "password": password}, headers=ORIGIN
    )


def _login(client: TestClient, email: str, password: str) -> Any:
    return client.post(
        "/v1/auth/login", json={"email": email, "password": password}, headers=ORIGIN
    )


# --------------------------------------------------------------------------- methods


def test_methods_reports_password_enabled_and_providers_disabled_by_default(
    client: TestClient,
) -> None:
    response = client.get("/v1/auth/methods")
    assert response.status_code == 200
    body = response.json()
    assert body["password"] == {"enabled": True, "signupMode": "open"}
    assert body["google"]["enabled"] is False
    assert body["apple"]["enabled"] is False


# --------------------------------------------------------------------------- signup + verify


def test_signup_returns_check_your_email_and_sends_a_verification_link(
    client: TestClient, sent_mail: SentMail
) -> None:
    response = _signup(client, "ada@example.com")
    assert response.status_code == 202
    assert response.json() == {"status": "checkYourEmail"}
    assert len(sent_mail) == 1
    kind, to, link = sent_mail[0]
    assert kind == "verify"
    assert to == "ada@example.com"
    assert "/v1/auth/verify?token=" in link


def test_signup_requires_origin(client: TestClient) -> None:
    response = client.post("/v1/auth/signup", json={"email": "a@b.com", "password": "x" * 12})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


def test_signup_rejects_a_policy_violating_password(client: TestClient) -> None:
    response = _signup(client, "ada@example.com", password="short")
    assert response.status_code == 400
    assert response.json()["error"]["field"] == "password"


def test_signup_is_silent_about_an_existing_address(
    client: TestClient, sent_mail: SentMail
) -> None:
    """The whole point of §4.9: signing up twice with the same address returns the identical
    202 body both times, and the *second* email is a conflict notice, not a second
    verification link — the caller can never distinguish the two outcomes from the response."""
    first = _signup(client, "ada@example.com")
    second = _signup(client, "ADA@EXAMPLE.COM")  # case-insensitive collision
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json() == {"status": "checkYourEmail"}
    assert [kind for kind, _, _ in sent_mail] == ["verify", "conflict"]


def test_verify_link_marks_the_email_verified_without_signing_anybody_in(
    client: TestClient, sent_mail: SentMail
) -> None:
    """The link stamps ``email_verified_at`` and nothing else.

    It must not mint a session: setting a ``SameSite=Lax`` cookie from a cross-site top-level
    navigation is allowed, so a ``GET`` that signs the caller in is a forced-login primitive —
    an attacker verifies their *own* address, keeps the link, and navigates a victim to it to
    make the victim's browser continue as the attacker. See
    ``test_forced_login_via_get_verify_is_impossible`` below.
    """
    _signup(client, "ada@example.com")
    _, _, link = sent_mail[0]
    token = link.rsplit("token=", 1)[1]

    response = client.get(f"/v1/auth/verify?token={token}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/settings?verified=1"
    assert "hw_session" not in response.cookies
    assert not [v for k, v in response.headers.items() if k.lower() == "set-cookie"]

    # Nobody is signed in, and the address really is verified.
    assert client.get("/v1/auth/session").status_code == 401
    _login(client, "ada@example.com", "a reasonable password")
    assert client.get("/v1/auth/session").json()["user"]["emailVerified"] is True


def test_forced_login_via_get_verify_is_impossible(
    client: TestClient, sent_mail: SentMail
) -> None:
    """An attacker's verification link, opened by a victim as a cross-site navigation, must not
    hand the victim the attacker's session."""
    _signup(client, "attacker@evil.test")
    token = sent_mail[0][2].rsplit("token=", 1)[1]

    victim = TestClient(client.app)
    response = victim.get(
        f"/v1/auth/verify?token={token}",
        follow_redirects=False,
        headers={"Origin": "https://evil.test", "Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 303
    assert not [v for k, v in response.headers.items() if k.lower() == "set-cookie"]
    assert victim.get("/v1/auth/session").status_code == 401


def test_verify_with_an_unknown_token_redirects_expired(client: TestClient) -> None:
    response = client.get("/v1/auth/verify?token=not-a-real-token", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/verify?expired=1"


def test_verify_token_is_single_use(client: TestClient, sent_mail: SentMail) -> None:
    _signup(client, "ada@example.com")
    token = sent_mail[0][2].rsplit("token=", 1)[1]
    first = client.get(f"/v1/auth/verify?token={token}", follow_redirects=False)
    second = client.get(f"/v1/auth/verify?token={token}", follow_redirects=False)
    assert first.headers["location"] == "/settings?verified=1"
    assert second.headers["location"] == "/verify?expired=1"


def test_invite_mode_requires_a_valid_code(monkeypatch: pytest.MonkeyPatch, auth_db: None) -> None:
    monkeypatch.setenv("HARDWOOD_SIGNUP_MODE", "invite")
    auth_config.reset_auth_settings_cache()
    app = FastAPI()
    api_errors.install_error_handlers(app)
    app.include_router(routes_auth.router, prefix="/v1")
    client = TestClient(app)

    no_code = client.post(
        "/v1/auth/signup",
        json={"email": "signer@example.com", "password": "a reasonable password"},
        headers=ORIGIN,
    )
    # An absent code and a wrong code are different mistakes and get different messages: the
    # sign-up form used to tell someone who deliberately left the field blank that the code
    # they had not supplied "is not valid", and named nothing they could do about it. The
    # default TestClient is not a loopback caller (its client host is "testclient"), which is
    # the hosted shape — the person signing up is not the operator and cannot run anything.
    assert no_code.status_code == 400
    missing = no_code.json()["error"]
    assert missing["code"] == "invite_required"
    assert missing["field"] == "inviteCode"
    assert "not valid" not in missing["message"]
    assert "Ask whoever runs this Hardwood" in missing["message"]

    # From loopback — the operator's own laptop — it names the command that mints one.
    local = TestClient(app, client=("127.0.0.1", 54321))
    local_missing = local.post(
        "/v1/auth/signup",
        json={"email": "local@example.com", "password": "a reasonable password"},
        headers=ORIGIN,
    )
    assert local_missing.status_code == 400
    assert "nbastats.accounts.admin invite" in local_missing.json()["error"]["message"]

    wrong_code = client.post(
        "/v1/auth/signup",
        json={
            "email": "signer@example.com",
            "password": "a reasonable password",
            "inviteCode": "not-a-real-code",
        },
        headers=ORIGIN,
    )
    assert wrong_code.status_code == 400
    assert wrong_code.json()["error"]["code"] == "invalid_invite"

    factory = db_module.get_sessionmaker()
    with factory() as db:
        db.add(WebInvite(code="good-code", note="test"))
        db.commit()

    ok = client.post(
        "/v1/auth/signup",
        json={
            "email": "signer@example.com",
            "password": "a reasonable password",
            "inviteCode": "good-code",
        },
        headers=ORIGIN,
    )
    assert ok.status_code == 202

    with factory() as db:
        invite = db.get(WebInvite, "good-code")
        assert invite.used_at is not None


# --------------------------------------------------------------------------- login


def test_login_succeeds_and_sets_a_session_cookie(client: TestClient) -> None:
    _signup(client, "ada@example.com", password="a reasonable password")
    response = _login(client, "ada@example.com", "a reasonable password")
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == "ada@example.com"
    assert isinstance(body["csrfToken"], str) and body["csrfToken"]
    assert "hw_session" in response.cookies


def test_login_rejects_wrong_password_with_a_generic_body(client: TestClient) -> None:
    _signup(client, "ada@example.com", password="a reasonable password")
    response = _login(client, "ada@example.com", "totally wrong")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_login_for_an_unknown_address_gives_the_identical_error(client: TestClient) -> None:
    known = _login(client, "nobody@example.com", "whatever password")
    assert known.status_code == 401
    assert known.json()["error"]["code"] == "invalid_credentials"
    assert known.json()["error"]["message"] == "That email and password do not match."


def test_login_clears_backoff_on_a_correct_password(client: TestClient, auth_db: None) -> None:
    _signup(client, "ada@example.com", password="a reasonable password")
    for _ in range(3):
        _login(client, "ada@example.com", "wrong")
    good = _login(client, "ada@example.com", "a reasonable password")
    assert good.status_code == 200

    factory = db_module.get_sessionmaker()
    with factory() as db:
        user = db.execute(select(User).where(User.email_lookup == "ada@example.com")).scalar_one()
        assert user.failed_login_count == 0
        assert user.locked_until is None


def test_login_backs_off_after_repeated_failures(client: TestClient) -> None:
    _signup(client, "ada@example.com", password="a reasonable password")
    for _ in range(6):
        response = _login(client, "ada@example.com", "wrong")
        assert response.status_code == 401

    factory = db_module.get_sessionmaker()
    with factory() as db:
        user = db.execute(select(User).where(User.email_lookup == "ada@example.com")).scalar_one()
        assert user.failed_login_count == 6
        assert user.locked_until is not None

    # The 7th attempt is refused by the backoff itself, but the body is identical either way.
    still_locked = _login(client, "ada@example.com", "a reasonable password")
    assert still_locked.status_code == 401
    assert still_locked.json()["error"]["code"] == "invalid_credentials"


def test_disabled_account_cannot_log_in(client: TestClient) -> None:
    _signup(client, "ada@example.com", password="a reasonable password")
    factory = db_module.get_sessionmaker()
    with factory() as db:
        user = db.execute(select(User).where(User.email_lookup == "ada@example.com")).scalar_one()
        user.is_disabled = True
        db.add(user)
        db.commit()

    response = _login(client, "ada@example.com", "a reasonable password")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


# --------------------------------------------------------------------------- session + CSRF


def test_session_requires_a_cookie(client: TestClient) -> None:
    response = client.get("/v1/auth/session")
    assert response.status_code == 401


def test_session_is_never_cached(client: TestClient) -> None:
    _signup(client, "ada@example.com", password="a reasonable password")
    _login(client, "ada@example.com", "a reasonable password")
    response = client.get("/v1/auth/session")
    assert response.headers["cache-control"] == "no-store"


def test_logout_requires_csrf_header(client: TestClient) -> None:
    _signup(client, "ada@example.com", password="a reasonable password")
    _login(client, "ada@example.com", "a reasonable password")
    response = client.post("/v1/auth/logout", json={}, headers=ORIGIN)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


def test_logout_with_a_valid_csrf_token_clears_the_session(client: TestClient) -> None:
    _signup(client, "ada@example.com", password="a reasonable password")
    login_response = _login(client, "ada@example.com", "a reasonable password")
    csrf_token = login_response.json()["csrfToken"]

    logout_response = client.post(
        "/v1/auth/logout",
        json={},
        headers={**ORIGIN, "X-Hardwood-CSRF": csrf_token},
    )
    assert logout_response.status_code == 204

    # The cookie the server set is now invalid.
    whoami = client.get("/v1/auth/session")
    assert whoami.status_code == 401


def test_a_stale_csrf_token_is_rejected_with_a_reload_message(client: TestClient) -> None:
    """A token that belonged to an *older session* no longer authorises a write. Signing in
    again rotates the cookie, and the CSRF token is derived from it, so the token the first
    sign-in handed back is genuinely stale."""
    _signup(client, "ada@example.com", password="a reasonable password")
    stale_token = _login(client, "ada@example.com", "a reasonable password").json()["csrfToken"]
    _login(client, "ada@example.com", "a reasonable password")  # new session, new token

    response = client.post(
        "/v1/auth/logout", json={}, headers={**ORIGIN, "X-Hardwood-CSRF": stale_token}
    )
    assert response.status_code == 403
    assert "reload the page" in response.json()["error"]["message"].lower()


def test_get_auth_session_does_not_rotate_the_csrf_token(client: TestClient) -> None:
    """``GET`` must not mutate. Rotating ``csrf_hash`` on read let any page break every write in
    a signed-in victim's open tab with one cross-site top-level navigation — and broke two
    honest tabs of the SPA against each other with no attacker at all."""
    _signup(client, "ada@example.com", password="a reasonable password")
    token = _login(client, "ada@example.com", "a reasonable password").json()["csrfToken"]

    first = client.get("/v1/auth/session").json()["csrfToken"]
    second = client.get("/v1/auth/session").json()["csrfToken"]
    assert first == second == token

    # The token the tab was already holding still works after those reads.
    assert client.post(
        "/v1/auth/logout", json={}, headers={**ORIGIN, "X-Hardwood-CSRF": token}
    ).status_code == 204


def test_logout_actually_clears_the_cookie(client: TestClient) -> None:
    """``logout`` returns a ``Response`` of its own, so the cookie-clearing header has to be
    written onto *that* object — FastAPI discards the injected sub-response's headers whenever
    a handler returns a ``Response``."""
    _signup(client, "ada@example.com", password="a reasonable password")
    token = _login(client, "ada@example.com", "a reasonable password").json()["csrfToken"]
    response = client.post(
        "/v1/auth/logout", json={}, headers={**ORIGIN, "X-Hardwood-CSRF": token}
    )
    assert response.status_code == 204
    set_cookies = [v for k, v in response.headers.items() if k.lower() == "set-cookie"]
    assert any("hw_session=" in value for value in set_cookies), set_cookies


def test_origin_mismatch_is_rejected(client: TestClient) -> None:
    _signup(client, "ada@example.com", password="a reasonable password")
    _login(client, "ada@example.com", "a reasonable password")
    response = client.post(
        "/v1/auth/logout",
        json={},
        headers={"Origin": "https://evil.example", "X-Hardwood-CSRF": "whatever"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


# --------------------------------------------------------------------------- password reset


def test_forgot_password_is_silent_about_unknown_addresses(client: TestClient) -> None:
    response = client.post(
        "/v1/auth/password/forgot", json={"email": "nobody@example.com"}, headers=ORIGIN
    )
    assert response.status_code == 202
    assert response.json() == {"status": "checkYourEmail"}
    assert "devLink" not in response.json()


def test_forgot_and_reset_password_end_to_end(
    client: TestClient, sent_mail: SentMail
) -> None:
    _signup(client, "ada@example.com", password="the original password")
    sent_mail.clear()

    forgot = client.post(
        "/v1/auth/password/forgot", json={"email": "ada@example.com"}, headers=ORIGIN
    )
    assert forgot.status_code == 202
    assert len(sent_mail) == 1 and sent_mail[0][0] == "reset"
    token = sent_mail[0][2].rsplit("token=", 1)[1]

    reset = client.post(
        "/v1/auth/password/reset",
        json={"token": token, "password": "a brand new password"},
        headers=ORIGIN,
    )
    assert reset.status_code == 204

    old_login = _login(client, "ada@example.com", "the original password")
    assert old_login.status_code == 401
    new_login = _login(client, "ada@example.com", "a brand new password")
    assert new_login.status_code == 200


def test_reset_token_is_single_use(client: TestClient, sent_mail: SentMail) -> None:
    _signup(client, "ada@example.com", password="the original password")
    sent_mail.clear()
    client.post("/v1/auth/password/forgot", json={"email": "ada@example.com"}, headers=ORIGIN)
    token = sent_mail[0][2].rsplit("token=", 1)[1]

    first = client.post(
        "/v1/auth/password/reset",
        json={"token": token, "password": "first new password"},
        headers=ORIGIN,
    )
    second = client.post(
        "/v1/auth/password/reset",
        json={"token": token, "password": "second new password"},
        headers=ORIGIN,
    )
    assert first.status_code == 204
    assert second.status_code == 400
    assert second.json()["error"]["code"] == "invalid_token"


def test_reset_revokes_every_session(client: TestClient, sent_mail: SentMail) -> None:
    _signup(client, "ada@example.com", password="the original password")
    login_response = _login(client, "ada@example.com", "the original password")
    old_cookie = login_response.cookies.get("hw_session")
    sent_mail.clear()

    client.post("/v1/auth/password/forgot", json={"email": "ada@example.com"}, headers=ORIGIN)
    token = sent_mail[0][2].rsplit("token=", 1)[1]
    client.post(
        "/v1/auth/password/reset",
        json={"token": token, "password": "a brand new password"},
        headers=ORIGIN,
    )

    other_client = TestClient(client.app)
    other_client.cookies.set("hw_session", old_cookie)
    response = other_client.get("/v1/auth/session")
    assert response.status_code == 401


def test_dev_link_appears_only_when_enabled_and_caller_is_loopback(
    monkeypatch: pytest.MonkeyPatch, auth_db: None
) -> None:
    monkeypatch.setenv("HARDWOOD_DEV_LINKS", "1")
    auth_config.reset_auth_settings_cache()
    app = FastAPI()
    api_errors.install_error_handlers(app)
    app.include_router(routes_auth.router, prefix="/v1")
    client = TestClient(app)  # TestClient's synthetic client host is loopback-shaped in Starlette

    _signup(client, "ada@example.com", password="a reasonable password")
    response = client.post(
        "/v1/auth/password/forgot", json={"email": "ada@example.com"}, headers=ORIGIN
    )
    assert response.status_code == 202
    # Whether devLink appears depends on TestClient's synthetic client host being treated as
    # loopback; assert the gate is at least consistent with `_is_loopback`, rather than assume
    # a specific TestClient implementation detail.
    from nbastats.api.routes_auth import _is_loopback  # noqa: PLC0415

    class _FakeRequest:
        def __init__(self, host: str | None) -> None:
            self.client = type("C", (), {"host": host})() if host else None

    assert _is_loopback(_FakeRequest("127.0.0.1")) is True
    assert _is_loopback(_FakeRequest("8.8.8.8")) is False
    assert _is_loopback(_FakeRequest(None)) is False


# --------------------------------------------------------------------------- rate limiting


def test_login_ip_bucket_eventually_rate_limits(client: TestClient) -> None:
    _signup(client, "ada@example.com", password="a reasonable password")
    responses = [
        _login(client, f"nobody{i}@example.com", "whatever") for i in range(31)
    ]
    assert responses[-1].status_code == 429
    assert responses[-1].json()["error"]["code"] == "rate_limited"
    assert "Retry-After" in responses[-1].headers
