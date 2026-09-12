"""The FastAPI application factory.

``create_app()`` assembles the service: the error envelope, the request-id middleware,
permissive CORS for local development, and every router under ``/v1`` behind the API-key gate
and the rate limiter. The startup hook creates the schema and — only when
``HARDWOOD_DEMO_MODE`` is set and the store is empty — seeds the synthetic league, so a fresh
checkout can serve a full dashboard with no network access.

``routes_dashboard`` and ``routes_leaders`` are included **if present**. They are built
alongside this module, and a service that can serve players, teams, games and sync is useful
on its own; an absent widget layer degrades the dashboard route, not the process.

Run it with ``uvicorn nbastats.api.app:app``.
"""
from __future__ import annotations

import importlib
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from .. import API_VERSION
from ..config import get_settings
from ..db import get_sessionmaker, init_db
from ..models import Team
from .deps import enforce_rate_limit, require_api_key
from .errors import REQUEST_ID_HEADER, RequestIdMiddleware, install_error_handlers
from .routes_games import router as games_router
from .routes_meta import router as meta_router
from .routes_players import router as players_router
from .routes_sync import router as sync_router
from .routes_teams import router as teams_router

__all__ = ["create_app", "app", "API_PREFIX", "OPTIONAL_ROUTE_MODULES"]

logger = logging.getLogger("nbastats.api")

API_PREFIX = "/v1"

#: Written by the widget layer. Absent is fine; broken is logged and skipped, because a
#: half-written module during a concurrent build must not take the whole service down.
OPTIONAL_ROUTE_MODULES = ("routes_dashboard", "routes_leaders")

DESCRIPTION = (
    "NBA advanced-stats service for the Hardwood iOS dashboard. Implements "
    "contracts/CONTRACT.md: lowerCamelCase JSON, fractions in [0,1] for percentages, and "
    "era-honest nulls — a stat that did not exist in an era is null, never zero."
)


def _prepare_database() -> None:
    """Create the schema, and seed the demo league when asked and the store is empty."""
    init_db()
    settings = get_settings()
    if not settings.demo_mode:
        return
    with get_sessionmaker()() as session:
        teams = session.execute(select(func.count()).select_from(Team)).scalar_one()
        if teams:
            return
        logger.info("HARDWOOD_DEMO_MODE is set and the database is empty: seeding.")
        from ..seed import seed_database

        summary = seed_database(session)
        session.commit()
        logger.info(
            "seeded %s games across %s seasons", summary["games"], len(summary["seasons"])
        )


def _include_optional_routers(application: FastAPI, dependencies: list) -> None:
    for name in OPTIONAL_ROUTE_MODULES:
        try:
            module = importlib.import_module(f".{name}", __package__)
        except ImportError:
            logger.info("%s is not present; its routes are not served", name)
            continue
        except Exception:  # pragma: no cover - a broken sibling module
            logger.exception("%s could not be imported; its routes are not served", name)
            continue
        router = getattr(module, "router", None)
        if router is None:
            logger.warning("%s has no `router`; nothing to include", name)
            continue
        application.include_router(router, prefix=API_PREFIX, dependencies=dependencies)


def create_app() -> FastAPI:
    """Build the Hardwood API application."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        _prepare_database()
        yield

    application = FastAPI(
        title="Hardwood API",
        version=API_VERSION,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    # Permissive by design: this service is read-only, carries no cookies and is normally
    # reached from a simulator or a LAN device during development. Credentials stay off, so
    # the wildcard origin is legal.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER],
    )
    # Added last, so it is outermost: every response, error envelopes included, gets an id.
    application.add_middleware(RequestIdMiddleware)

    install_error_handlers(application)

    guards = [Depends(require_api_key), Depends(enforce_rate_limit)]
    for router in (meta_router, players_router, teams_router, games_router, sync_router):
        application.include_router(router, prefix=API_PREFIX, dependencies=guards)
    _include_optional_routers(application, guards)

    return application


#: Module-level application so ``uvicorn nbastats.api.app:app`` works unchanged.
app = create_app()
