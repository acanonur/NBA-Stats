"""Serve the built single-page app: ``web/dist``.

One process, one port, one origin (``WEB_DESIGN.md`` §0) means this module — not a separate
static host, not a CDN — is what answers a browser's very first request. :func:`mount_web` is
called **last** in ``app.py``'s ``create_app()``, after every ``/v1`` router: Starlette tries
routes in registration order, so a request that no real API route claims only ever reaches this
module's catch-all, and a request that *does* match a real route never does.

Why a missing ``web/dist/index.html`` is a warning, never a startup failure
--------------------------------------------------------------------------------
A stats-only checkout (no ``web/`` build, no ``[web]`` extra installed, ``HARDWOOD_WEB=0``) has
to keep serving exactly what it serves today. ``mount_web`` logs one line and returns without
registering a single route in that case, so the rest of the API is unaffected and there is
nothing here for a request to ever 404 against that would not have 404'd before this module
existed.

Why ``/assets`` is mounted before the catch-all, and why its cache header is a year
------------------------------------------------------------------------------------------
Vite fingerprints every build output under ``assets/`` with a content hash in the filename, so
``/assets/index-a1b2c3.js`` is either exactly the file this deployment shipped or a 404 — it can
never *change* at that URL. ``Cache-Control: public, max-age=31536000, immutable`` is therefore
free correctness, not a risk: a new build ships new filenames, and ``index.html`` (which names
those files) is served with ``Cache-Control: no-store`` below specifically so a returning
visitor always fetches the *current* list of fingerprinted assets rather than a cached
``index.html`` pointing at files a deploy already deleted.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from ..accounts.config import get_auth_settings
from . import errors

__all__ = ["router", "mount_web", "resolve_dist_dir"]

logger = logging.getLogger("nbastats.api")

#: Kept for symmetry with every other ``routes_*`` module (``app.py`` inspects each optional
#: module for a ``router`` attribute); this one is not included via that path — the SPA catch-all
#: has to be registered *after* every real router, which ``mount_web`` does explicitly — but the
#: attribute still exists so a future direct ``include_router`` call is not a surprise.
router = APIRouter(include_in_schema=False)

#: ``backend/nbastats/api/routes_web.py`` -> repo root is three ``.parent``s up.
_REPO_ROOT = Path(__file__).resolve().parents[3]

#: A request under any of these is never the SPA's to answer, whether or not a real route
#: happens to claim it right now — serving ``index.html`` for a mistyped ``/v1/`` path or a
#: disabled ``/docs`` would look like the app loaded instead of like the 404 it should be.
#: Compared case-insensitively, and each entry matches the bare segment as well as anything
#: beneath it: ``/v1`` and ``/V1/me`` used to fall through to ``index.html`` with a 200, which
#: makes a mistyped API base URL look like "the app is up" rather than like the 404 it is.
_NEVER_SPA_SEGMENTS = ("v1", "docs", "openapi.json", "redoc")

_WELL_KNOWN_PREFIX = ".well-known/"


def resolve_dist_dir() -> Path:
    """Where the built SPA lives: ``HARDWOOD_WEB_DIST`` if set, else ``<repo>/web/dist``."""
    settings = get_auth_settings()
    if settings.web_dist:
        return Path(settings.web_dist)
    return _REPO_ROOT / "web" / "dist"


class _ImmutableStaticFiles(StaticFiles):
    """``StaticFiles`` with one fixed, year-long, immutable cache header — see the module
    docstring for why that is safe for Vite's content-hashed output specifically."""

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def _is_never_spa(normalized: str) -> bool:
    """True when this path is the API's, the docs', or the schema's — never the SPA's."""
    lowered = normalized.lower()
    return any(
        lowered == segment or lowered.startswith(f"{segment}/")
        for segment in _NEVER_SPA_SEGMENTS
    )


def _is_safe_subpath(root: Path, candidate: Path) -> bool:
    """True when ``candidate`` (already resolved) is ``root`` or strictly inside it — the guard
    against a ``.well-known/../../secret`` traversal attempt."""
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def mount_web(application: FastAPI) -> None:
    """Mount ``/assets`` and register the SPA catch-all, or do nothing at all.

    Must be called after every ``/v1`` router is already registered (``app.py`` calls it last):
    the catch-all below matches literally any path, so anything registered after it would never
    be reached.
    """
    settings = get_auth_settings()
    if not settings.web_enabled:
        logger.info("HARDWOOD_WEB is disabled; the web app is not mounted.")
        return

    dist = resolve_dist_dir()
    index_path = dist / "index.html"
    if not index_path.is_file():
        logger.warning(
            "%s is missing; the web app is not mounted. Build it with `npm --prefix web run "
            "build` (or `scripts/web.sh build`) and try again.",
            index_path,
        )
        return

    assets_dir = dist / "assets"
    if assets_dir.is_dir():
        application.mount(
            "/assets", _ImmutableStaticFiles(directory=str(assets_dir)), name="web-assets"
        )

    well_known_dir = (dist / ".well-known").resolve()

    @application.get("/{full_path:path}", include_in_schema=False, name="web-spa")
    async def spa(full_path: str) -> Response:
        """The SPA fallback: a real 404 for anything under :data:`_NEVER_SPA_SEGMENTS`, the
        matching file for ``/.well-known/*`` (Apple's domain-verification file lives there),
        and ``index.html`` — with ``Cache-Control: no-store`` — for every client route."""
        normalized = full_path.lstrip("/")

        if _is_never_spa(normalized):
            raise errors.ApiError("not_found", "Not found.", http_status=404)

        if normalized.startswith(_WELL_KNOWN_PREFIX):
            candidate = (dist / normalized).resolve()
            if (
                well_known_dir.is_dir()
                and candidate.is_file()
                and _is_safe_subpath(well_known_dir, candidate)
            ):
                return FileResponse(candidate)
            raise errors.ApiError("not_found", "Not found.", http_status=404)

        return FileResponse(index_path, headers={"Cache-Control": "no-store"})

    logger.info("serving the web app from %s", dist)
