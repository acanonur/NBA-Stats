"""The defects a four-reviewer pass found in the first web release, each pinned by a test.

Every test here is named for the failure it prevents from coming back, not for the function it
calls — these are regression pins, and the value is in the failure mode each one describes.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import timedelta
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from nbastats import config as stats_config
from nbastats.accounts import config as auth_config
from nbastats.accounts import mail, retention
from nbastats.accounts.models import User
from nbastats.api import deps
from nbastats.api.app import create_app
from nbastats.db import get_sessionmaker, init_db, utcnow
from nbastats import db as db_module


@pytest.fixture(autouse=True)
def _clean_settings() -> Iterator[None]:
    auth_config.reset_auth_settings_cache()
    yield
    auth_config.reset_auth_settings_cache()


@contextmanager
def environment(**values: str | None) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    for key, value in values.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    auth_config.reset_auth_settings_cache()
    stats_config.reset_settings_cache()
    deps.reset_rate_limiter()
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        auth_config.reset_auth_settings_cache()
        stats_config.reset_settings_cache()
        deps.reset_rate_limiter()


# ------------------------------------------------------------------ backend/.env is read


def test_a_value_only_in_the_dotenv_file_reaches_the_running_app(tmp_path) -> None:
    """`web.sh setup` writes backend/.env and tells the operator to put their Google
    credentials in it. Nothing in the server ever read that file — only `web.sh doctor` did —
    so doctor reported `google: enabled` while the running process had never seen the id or
    the secret, and there was no log line, no health warning and no button."""
    env_file = tmp_path / "dotenv-probe.env"
    env_file.write_text(
        "# a comment\n"
        "HARDWOOD_GOOGLE_CLIENT_ID=12345-abc.apps.googleusercontent.com\n"
        'HARDWOOD_GOOGLE_CLIENT_SECRET="GOCSPX-testsecret"\n',
        encoding="utf-8",
    )
    with environment(
        HARDWOOD_ENV_FILE=str(env_file),
        HARDWOOD_GOOGLE_CLIENT_ID=None,
        HARDWOOD_GOOGLE_CLIENT_SECRET=None,
    ):
        with TestClient(create_app()) as client:
            methods = client.get("/v1/auth/methods").json()
        assert methods["google"]["enabled"] is True

        settings = auth_config.get_auth_settings()
        assert settings.google_client_id == "12345-abc.apps.googleusercontent.com"
        assert settings.google_client_secret == "GOCSPX-testsecret"


def test_an_exported_variable_still_beats_the_dotenv_file(tmp_path) -> None:
    env_file = tmp_path / "loser.env"
    env_file.write_text("HARDWOOD_SIGNUP_MODE=closed\n", encoding="utf-8")
    with environment(HARDWOOD_ENV_FILE=str(env_file), HARDWOOD_SIGNUP_MODE="invite"):
        auth_config.load_env_file()
        assert auth_config.get_auth_settings().signup_mode == "invite"


# ------------------------------------------------------------------ typos are not tracebacks


def test_a_mistyped_numeric_setting_is_an_operator_message_not_a_valueerror(tmp_path) -> None:
    """`HARDWOOD_SESSION_DAYS=thirty` used to come out of `python -m uvicorn` as two chained
    tracebacks through config.py. The useful sentence was already there; only the twenty lines
    of interpreter internals in front of it had to go."""
    env_file = tmp_path / "typo.env"
    env_file.write_text("HARDWOOD_SESSION_DAYS=thirty\n", encoding="utf-8")
    with environment(HARDWOOD_ENV_FILE=str(env_file), HARDWOOD_SESSION_DAYS=None):
        with pytest.raises(RuntimeError) as caught:
            create_app()
    message = str(caught.value)
    assert "refuses to start" in message
    assert "HARDWOOD_SESSION_DAYS='thirty' is not an integer" in message
    assert str(env_file) in message


def test_env_int_rejects_a_non_integer() -> None:
    with pytest.raises(ValueError, match="HARDWOOD_SESSION_DAYS='x' is not an integer"):
        auth_config.AuthSettings.from_env({"HARDWOOD_SESSION_DAYS": "x"})


# ------------------------------------------------------------------ the rate limiter


def test_a_varying_api_key_header_cannot_buy_a_fresh_rate_limit_bucket(
    seeded_engine: Engine,
) -> None:
    """The bucket key was the raw `X-API-Key` header, used before anything validated it. With
    HARDWOOD_API_KEY unset — the default, and every web deployment — `X-API-Key: bucket-1`,
    `bucket-2`, … turned the one global brake in front of the single worker completely off."""
    with environment(
        HARDWOOD_RATE_LIMIT="3", HARDWOOD_RATE_WINDOW_SECONDS="60", HARDWOOD_API_KEY=None
    ):
        with TestClient(create_app()) as client:
            statuses = [
                client.get("/v1/teams", headers={"X-API-Key": f"bucket-{n}"}).status_code
                for n in range(6)
            ]
    assert statuses[:3] == [200, 200, 200]
    assert 429 in statuses[3:], statuses


def test_a_validated_api_key_still_gets_its_own_bucket(seeded_engine: Engine) -> None:
    with environment(
        HARDWOOD_RATE_LIMIT="3",
        HARDWOOD_RATE_WINDOW_SECONDS="60",
        HARDWOOD_API_KEY="s3cret",
    ):
        with TestClient(create_app()) as client:
            keyed = [
                client.get("/v1/teams", headers={"X-API-Key": "s3cret"}).status_code
                for _ in range(3)
            ]
            # The key's own bucket is spent; a *wrong* key falls through to the address
            # bucket, which is untouched, and is rejected on authentication instead.
            assert keyed == [200, 200, 200]
            assert client.get("/v1/teams", headers={"X-API-Key": "s3cret"}).status_code == 429
            assert client.get("/v1/teams", headers={"X-API-Key": "wrong"}).status_code == 401


# ------------------------------------------------------------------ the SPA catch-all


@pytest.mark.parametrize("path", ["/v1", "/V1/me", "/V1", "/DOCS", "/OpenAPI.json"])
def test_the_spa_never_answers_for_the_api_or_docs_whatever_the_case(
    seeded_engine: Engine, path: str
) -> None:
    """`GET /v1` and `GET /V1/me` used to return index.html with a 200, so a mistyped API base
    URL looked like "the app is up" instead of like the 404 it is."""
    with TestClient(create_app()) as client:
        response = client.get(path)
    assert response.status_code == 404, response.text
    assert "text/html" not in response.headers.get("content-type", "")


# ------------------------------------------------------------------ the import guard


def test_deeply_nested_json_is_a_400_not_a_500(seeded_engine: Engine) -> None:
    """`RecursionError` subclasses `RuntimeError`, not `ValueError`, so 120 KB of nested
    brackets — far under the 1 MiB body cap — escaped the decode guard and became a 500."""
    from nbastats.api import routes_me

    body = ("[" * 60000 + "]" * 60000).encode("ascii")

    class _FakeRequest:
        async def body(self) -> bytes:
            return body

    import anyio

    from nbastats.api.errors import ApiError

    async def run() -> None:
        with pytest.raises(ApiError) as caught:
            await routes_me.bounded_import_payload(_FakeRequest())  # type: ignore[arg-type]
        assert caught.value.code == "bad_request"

    anyio.run(run)


# ------------------------------------------------------------------ mail


def test_a_public_deployment_refuses_to_start_with_the_log_mailer() -> None:
    """`HARDWOOD_MAILER` defaults to `log`, which writes live one-hour account-takeover links
    to stdout. On https with Google configured that used to start silently."""
    settings = auth_config.AuthSettings.from_env(
        {
            "HARDWOOD_PUBLIC_BASE_URL": "https://hardwood.example",
            "HARDWOOD_TRUSTED_PROXY_CIDRS": "10.0.0.0/8",
        }
    )
    reasons = auth_config.startup_refusals(settings)
    assert any("HARDWOOD_MAILER=log" in reason for reason in reasons), reasons
    assert any("account-takeover" in reason for reason in reasons), reasons


def test_a_household_lan_deployment_is_warned_not_refused() -> None:
    """docs/WEB.md §9 documents the LAN deployment as a supported accepted-risk configuration,
    and §6 says operator-run password reset is the story without SMTP. Refusing to start there
    would break a documented path; the operator is told loudly instead."""
    settings = auth_config.AuthSettings.from_env(
        {
            "HARDWOOD_PUBLIC_BASE_URL": "http://192.168.1.50:8000",
            "HARDWOOD_ALLOW_INSECURE_COOKIES": "1",
        }
    )
    assert auth_config.startup_refusals(settings) == []
    warnings = auth_config.startup_warnings(settings)
    assert any("sends no mail" in warning for warning in warnings), warnings


def test_the_log_mailer_is_fine_on_loopback() -> None:
    settings = auth_config.AuthSettings.from_env({})
    assert settings.mailer == "log"
    assert auth_config.startup_refusals(settings) == []


def test_the_stdout_fallback_never_fires_off_loopback(capsys) -> None:
    """The `print` fallback bypassed logging configuration entirely, so an operator could not
    turn it off. It is now gated on the deployment being loopback."""
    lan = auth_config.AuthSettings.from_env(
        {"HARDWOOD_PUBLIC_BASE_URL": "http://192.168.1.50:8000", "HARDWOOD_ALLOW_INSECURE_COOKIES": "1"}
    )
    logging.getLogger("nbastats.accounts.mail").handlers.clear()
    mail.send(mail.Mail(to="a@b.test", subject="s", text="http://x/reset#token=live"), settings=lan)
    assert "live" not in capsys.readouterr().out

    local = auth_config.AuthSettings.from_env({})
    mail.send(mail.Mail(to="a@b.test", subject="s", text="http://x/reset#token=live"), settings=local)
    assert "live" in capsys.readouterr().out


def test_the_methods_payload_says_whether_mail_is_actually_delivered() -> None:
    assert mail.delivers_mail(auth_config.AuthSettings.from_env({})) is False
    smtp = auth_config.AuthSettings.from_env(
        {"HARDWOOD_MAILER": "smtp", "HARDWOOD_SMTP_URL": "smtps://u:p@h:465"}
    )
    assert mail.delivers_mail(smtp) is True


def test_the_reset_link_carries_its_token_in_the_fragment() -> None:
    """`/reset` is an SPA route and the GET does not consume the token, so a query parameter
    left a live single-use credential in uvicorn's access log until the flow finished."""
    from nbastats.api import routes_auth

    settings = auth_config.AuthSettings.from_env({})
    link = routes_auth._link_for(settings, "reset", "TOKEN")
    assert link.endswith("/reset#token=TOKEN")
    assert "?" not in link


# ------------------------------------------------------------------ retention


def test_the_thirty_day_erasure_actually_erases(tmp_path, monkeypatch) -> None:
    """`/legal/privacy` and the Settings screen both promise erasure after 30 days. Only an
    operator CLI nobody was told to schedule ever did it; the app now runs the same job."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'retention.db'}")
    stats_config.reset_settings_cache()
    db_module.dispose_engine()
    init_db()
    try:
        with get_sessionmaker()() as db:
            now = utcnow()
            old = User(
                user_id="old",
                email="old@zed.test",
                email_lookup="old@zed.test",
                created_at=now,
                updated_at=now,
            )
            old.deleted_at = now - timedelta(days=45)
            recent = User(
                user_id="recent",
                email="new@zed.test",
                email_lookup="new@zed.test",
                created_at=now,
                updated_at=now,
            )
            recent.deleted_at = now - timedelta(days=2)
            db.add_all([old, recent])
            db.commit()

            summary = retention.purge(db)
            db.commit()
            assert summary.users == 1
            assert db.get(User, "old") is None
            assert db.get(User, "recent") is not None

            # Idempotent: a second pass finds nothing left to do.
            assert retention.purge(db).users == 0
    finally:
        db_module.dispose_engine()
        stats_config.reset_settings_cache()


# ------------------------------------------------------------------ the operator CLI


def test_the_invite_command_works_on_a_database_that_does_not_exist_yet(
    tmp_path, monkeypatch, capsys
) -> None:
    """`web.sh setup` prints this command as the next thing to run, and on a fresh checkout it
    died with sixty lines of `no such table: web_invites` until the server had been started
    once — which nothing said was a prerequisite."""
    from nbastats.accounts import admin

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'brand-new.db'}")
    monkeypatch.setenv("HARDWOOD_ENV_FILE", str(tmp_path / "absent.env"))
    stats_config.reset_settings_cache()
    db_module.dispose_engine()
    try:
        assert admin.main(["invite", "--note", "me"]) == 0
        code = capsys.readouterr().out.strip()
        assert len(code) > 10
    finally:
        db_module.dispose_engine()
        stats_config.reset_settings_cache()


# ------------------------------------------------------------------ the vocabulary guard


def test_no_market_vocabulary_anywhere_the_reader_can_see_it() -> None:
    """WEB_DESIGN.md §7.8/§11 said this grep was extended to `web/src/**`. It never was, and
    "fantasy numbers that never pretend to be a betting line" reached the signed-out landing
    page. A disclaimer that names the thing still puts the thing on the page."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    # Word-boundary matched, because "navigate" contains "vig" and "mistake" contains
    # "stake". "line" is banned as *vocabulary* but is unbannable as a token (lineHeight,
    # polyline, a line of text), so the phrase people actually reach for is what is caught:
    # "betting" in any form.
    banned = (
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
    )
    pattern = re.compile(r"\b(?:" + "|".join(banned) + r")\b")
    scanned = 0
    offences: list[str] = []
    roots = [
        root / "web" / "src",
        root / "backend" / "nbastats" / "accounts",
        root / "backend" / "nbastats" / "api",
        root / "backend" / "nbastats" / "widgets",
    ]
    for base in roots:
        for path in sorted(base.rglob("*")):
            if path.suffix not in {".ts", ".tsx", ".css", ".py"}:
                continue
            if "__tests__" in path.parts or path.name.endswith(".test.ts"):
                continue
            if path.name == __file__.rsplit("/", 1)[-1]:
                continue
            scanned += 1
            text = path.read_text(encoding="utf-8").lower()
            for found in sorted(set(pattern.findall(text))):
                offences.append(f"{path.relative_to(root)} mentions {found!r}")
    assert scanned > 200, f"only scanned {scanned} files; the walk is wrong"
    assert offences == [], offences
