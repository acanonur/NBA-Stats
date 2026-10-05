"""``nbastats.api.routes_web`` — the SPA fallback, cache headers, and graceful absence.

Most of these build a throwaway ``web/dist`` under ``tmp_path`` and mount it on a bare
``FastAPI()`` (with ``install_error_handlers`` wired in by hand, since ``routes_web`` raises
``errors.ApiError`` and needs the same translation ``create_app()`` normally provides) — that
isolates ``mount_web`` from the database lifespan the real app needs, so these tests do not
depend on ``conftest.py``'s seeded database at all. The one test that must exercise the real,
fully-wired app (an unknown ``/v1/*`` path is a real 404) uses ``create_app()`` and the
committed ``web/dist``, because that is a property of routing order across the whole app, not
of this module alone.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nbastats.accounts import config as auth_config
from nbastats.api.app import create_app
from nbastats.api.errors import install_error_handlers
from nbastats.api.routes_web import mount_web


@pytest.fixture(autouse=True)
def _reset_auth_settings() -> Iterator[None]:
    auth_config.reset_auth_settings_cache()
    yield
    auth_config.reset_auth_settings_cache()


def _make_dist(tmp_path: Path, *, with_well_known: bool = False) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>Hardwood</body></html>", encoding="utf-8")
    (dist / "assets" / "index-abc123.js").write_text("console.log('hi')", encoding="utf-8")
    if with_well_known:
        (dist / ".well-known").mkdir()
        (dist / ".well-known" / "apple-developer-domain-association.txt").write_text(
            "verification-token", encoding="utf-8"
        )
    return dist


def _bare_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    return app


def test_missing_dist_is_not_mounted_and_logs_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("HARDWOOD_WEB_DIST", str(tmp_path / "does-not-exist"))
    auth_config.reset_auth_settings_cache()
    app = _bare_app()
    with caplog.at_level(logging.WARNING, logger="nbastats.api"):
        mount_web(app)
    assert any("is missing" in record.message for record in caplog.records)
    with TestClient(app) as client:
        response = client.get("/anything")
    assert response.status_code == 404  # no catch-all was ever registered


def test_web_disabled_flag_prevents_mounting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = _make_dist(tmp_path)
    monkeypatch.setenv("HARDWOOD_WEB_DIST", str(dist))
    monkeypatch.setenv("HARDWOOD_WEB", "0")
    auth_config.reset_auth_settings_cache()
    app = _bare_app()
    mount_web(app)
    with TestClient(app) as client:
        response = client.get("/d/abc")
    assert response.status_code == 404


def test_spa_fallback_serves_index_html_with_no_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = _make_dist(tmp_path)
    monkeypatch.setenv("HARDWOOD_WEB_DIST", str(dist))
    auth_config.reset_auth_settings_cache()
    app = _bare_app()
    mount_web(app)
    with TestClient(app) as client:
        response = client.get("/d/abc123?edit=1")
    assert response.status_code == 200
    assert "Hardwood" in response.text
    assert response.headers["cache-control"] == "no-store"


def test_assets_are_served_with_a_year_long_immutable_cache_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = _make_dist(tmp_path)
    monkeypatch.setenv("HARDWOOD_WEB_DIST", str(dist))
    auth_config.reset_auth_settings_cache()
    app = _bare_app()
    mount_web(app)
    with TestClient(app) as client:
        response = client.get("/assets/index-abc123.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_well_known_file_is_served_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = _make_dist(tmp_path, with_well_known=True)
    monkeypatch.setenv("HARDWOOD_WEB_DIST", str(dist))
    auth_config.reset_auth_settings_cache()
    app = _bare_app()
    mount_web(app)
    with TestClient(app) as client:
        response = client.get("/.well-known/apple-developer-domain-association.txt")
    assert response.status_code == 200
    assert response.text == "verification-token"


def test_well_known_missing_file_is_a_real_404_not_the_spa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = _make_dist(tmp_path)  # no .well-known directory at all
    monkeypatch.setenv("HARDWOOD_WEB_DIST", str(dist))
    auth_config.reset_auth_settings_cache()
    app = _bare_app()
    mount_web(app)
    with TestClient(app) as client:
        response = client.get("/.well-known/apple-developer-domain-association.txt")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert "Hardwood" not in response.text  # never the SPA shell


def test_well_known_path_traversal_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = _make_dist(tmp_path, with_well_known=True)
    secret = tmp_path / "secret.txt"
    secret.write_text("do not serve me", encoding="utf-8")
    monkeypatch.setenv("HARDWOOD_WEB_DIST", str(dist))
    auth_config.reset_auth_settings_cache()
    app = _bare_app()
    mount_web(app)
    with TestClient(app) as client:
        response = client.get("/.well-known/../../secret.txt")
    # Whatever the client-side URL normalisation collapses this to (Starlette/httpx resolve
    # `..` before routing sees it, so the handler may never even observe a `.well-known/`
    # prefix), the one outcome that must never happen is the secret file's own contents
    # reaching the response — a 404 from the `.well-known` branch and a same-origin fallback
    # to `index.html` are both acceptable; leaking `secret.txt` is not.
    assert "do not serve me" not in response.text


def test_unknown_v1_path_under_the_catch_all_is_a_real_404_not_the_spa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = _make_dist(tmp_path)
    monkeypatch.setenv("HARDWOOD_WEB_DIST", str(dist))
    auth_config.reset_auth_settings_cache()
    app = _bare_app()
    mount_web(app)
    with TestClient(app) as client:
        response = client.get("/v1/totally-not-a-route")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert "Hardwood" not in response.text


def test_docs_paths_are_a_real_404_when_the_spa_is_all_that_is_mounted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = _make_dist(tmp_path)
    monkeypatch.setenv("HARDWOOD_WEB_DIST", str(dist))
    auth_config.reset_auth_settings_cache()
    app = FastAPI(docs_url=None, openapi_url=None)
    install_error_handlers(app)
    mount_web(app)
    with TestClient(app) as client:
        for path in ("/docs", "/openapi.json", "/redoc"):
            response = client.get(path)
            assert response.status_code == 404, path
            assert "Hardwood" not in response.text, path


def test_the_real_app_serves_the_web_app_and_still_answers_v1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uses the committed ``web/dist`` and the real, fully-wired app: the SPA and the existing
    API surface must coexist, in either order of request."""
    auth_config.reset_auth_settings_cache()
    with TestClient(create_app()) as client:
        health = client.get("/v1/health")
        assert health.status_code == 200

        home = client.get("/")
        assert home.status_code == 200
        assert home.headers["cache-control"] == "no-store"

        missing = client.get("/v1/not-a-real-route")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "not_found"
