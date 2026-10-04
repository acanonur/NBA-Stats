"""The FastAPI application factory.

``create_app()`` assembles the service: the error envelope, the request-id middleware,
permissive CORS for local development, and every router under ``/v1`` behind the API-key gate
and the rate limiter. The startup hook creates the schema and — only when
``HARDWOOD_DEMO_MODE`` is set and the store is empty — seeds the synthetic league, so a fresh
checkout can serve a full dashboard with no network access.

``routes_dashboard``, ``routes_leaders``, ``routes_auth`` and ``routes_me`` are included **if
present**. They are built alongside this module, and a service that can serve players, teams,
games and sync is useful on its own; an absent widget layer or an absent accounts feature
degrades those routes, not the process.

Hardwood Web additions, without disturbing any of the above
------------------------------------------------------------------
Four things land here for the web app, each guarded so a stats-only deployment — no ``web/``
build, no ``[web]`` extra, none of the ``HARDWOOD_*`` auth variables set — behaves exactly as it
did before this module grew a web app at all:

* :func:`nbastats.accounts.config.startup_refusals` runs before a single request is served;
  a non-empty result raises ``RuntimeError`` with the operator-facing reason, rather than
  starting a service that would ship session cookies over plaintext or allow open signup on a
  public address with no invite backstop.
* ``docs_url`` / ``openapi_url`` become ``None`` once ``HARDWOOD_PUBLIC_BASE_URL`` is not a
  loopback address — Swagger UI loads a CDN script, and leaving an un-CSP'd, same-origin page
  on the session cookie's own origin is an avoidable enlargement of the XSS surface on a
  deployment anyone else can reach.
* ``nbastats.api.security.SecurityHeadersMiddleware`` and ``ProxyHeadersGuard`` are added
  alongside ``RequestIdMiddleware`` (see that module's docstring for the exact layering and why
  it is exactly this order), and every ``/v1`` router — the five original ones, plus
  ``routes_dashboard`` / ``routes_leaders`` / the new ``routes_fantasy`` — is now gated by
  :func:`nbastats.api.deps.require_api_key_or_session` instead of the bare API-key check, so a
  signed-in browser session is accepted anywhere an API key already was. See that function's
  docstring for why identity is resolved *before* the early return a keyless deployment takes.
* ``nbastats.api.routes_web.mount_web`` is called **last**, after every other router, so the
  SPA's catch-all route (which matches literally any path) never shadows a real one.

None of this requires the accounts feature to be installed. ``AuthSettings`` (WP0) has sensible
defaults with no environment variables set at all, and every accounts-specific optional router
still goes through the same ``ImportError``-tolerant path ``routes_dashboard`` and
``routes_leaders`` already used.

Two leagues, one process, and a wall between them
-------------------------------------------------
Hardwood now serves the EuroLeague beside the NBA, and the way this module wires it is the half
of the isolation that no other file can provide (the rest is in ``tests/test_league_isolation``).
Everything new goes through the optional-router path above, so a checkout without the new
modules, or with one of them half-written, serves exactly what it served before:

* The NBA's matchup, defence, projection, availability, news, league and sources routes
  (``routes_leagues``, ``routes_matchups``, ``routes_defense``, ``routes_projections``,
  ``routes_availability``, ``routes_sources``) are ordinary optional routers under ``/v1`` with
  the standard guards.
* The EuroLeague is ``routes_euroleague``, a three-line shim over a router with ``prefix="/el"``,
  so it lands at ``/v1/el``. The league is chosen by the URL, never by a header, which is half of
  why a EuroLeague row cannot reach an NBA view. It is not a mounted sub-app: it shares this
  application's lifespan, error envelope, middleware and guards, so there is no second startup
  to forget and no second set of guards to get subtly different.
* ``HARDWOOD_EL_ENABLED=0`` skips the EuroLeague entirely: its router is not mounted, its store
  is not opened, and ``GET /v1/leagues`` reports it as disabled. The switch is named in
  :data:`ROUTE_SWITCHES`, and read straight from the environment here because this module must
  never import the EuroLeague package to find out whether it should.
* **This module never imports ``nbastats.euroleague``.** Nothing outside that package may (an AST
  test enforces it, with one named exception: the shim ``routes_euroleague``), so the start-up
  hook below reaches the EuroLeague's bootstrap through :func:`importlib.import_module` by string
  name, inside a ``try`` that turns any failure into "the EuroLeague is off, and here is why".
  A broken EuroLeague store therefore cannot stop the NBA from serving.
* ``/v1/el/health`` answers without a key (it joins ``deps.API_KEY_EXEMPT_PATHS``), because the
  EuroLeague's state is its payload and a load balancer or the Mac's own launch agent must be
  able to ask whether it is up.

Run it with ``uvicorn nbastats.api.app:app``.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
import sys
from contextlib import asynccontextmanager, suppress
from typing import AsyncIterator

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from .. import API_VERSION
from ..accounts.config import (
    get_auth_settings,
    load_env_file,
    startup_refusals,
    startup_warnings,
)
from ..config import get_settings
from ..db import get_sessionmaker, init_db
from ..models import Team
from .deps import enforce_rate_limit, require_api_key_or_session
from .errors import REQUEST_ID_HEADER, RequestIdMiddleware, install_error_handlers
from .routes_fantasy import router as fantasy_router
from .routes_games import router as games_router
from .routes_meta import router as meta_router
from .routes_players import router as players_router
from .routes_sync import router as sync_router
from .routes_teams import router as teams_router
from .routes_web import mount_web
from .security import ProxyHeadersGuard, SecurityHeadersMiddleware

__all__ = [
    "create_app",
    "app",
    "API_PREFIX",
    "OPTIONAL_ROUTE_MODULES",
    "OPTIONAL_ROUTE_GUARDS",
    "ROUTE_SWITCHES",
]

logger = logging.getLogger("nbastats.api")

API_PREFIX = "/v1"

#: Written by the widget layer and by the accounts feature. Absent is fine; broken is logged
#: and skipped, because a half-written module during a concurrent build must not take the whole
#: service down. ``routes_auth`` and ``routes_me`` are here rather than in the mandatory list
#: below for the same reason ``routes_dashboard`` always has been: a missing ``pyjwt``, or a
#: checkout mid-build, must leave the rest of the service exactly as useful as it is today.
#:
#: The last seven are the matchup, defence, projection, availability and league features
#: (``routes_euroleague`` being the EuroLeague's whole router, mounted at ``/v1/el``). They are
#: listed here, and not in the mandatory tuple in :func:`create_app`, because they are written
#: by separate packages: a module that is absent is skipped, one that is broken is logged and
#: skipped, and the rest of the service is exactly as useful as it was without them.
OPTIONAL_ROUTE_MODULES = (
    "routes_dashboard",
    "routes_leaders",
    "routes_auth",
    "routes_me",
    "routes_leagues",
    "routes_matchups",
    "routes_defense",
    "routes_projections",
    "routes_availability",
    "routes_sources",
    "routes_euroleague",
)

#: Optional router module to the environment variable that can switch it off. A value that is
#: plainly false (``0``, ``false``, ``no``, ``off``) skips the module; anything else, including
#: unset, mounts it, so the EuroLeague is on by default on the user's machine and a typo does not
#: silently disable it (the EuroLeague's own settings report a malformed value as an error).
ROUTE_SWITCHES: dict[str, str] = {"routes_euroleague": "HARDWOOD_EL_ENABLED"}

#: The strings an on/off environment variable may use to say "off".
_FALSE_STRINGS = frozenset({"0", "false", "f", "no", "n", "off"})

#: Per-module override of the dependency list ``_include_optional_routers`` applies. Anything
#: not named here gets :data:`_STANDARD_GUARDS` (API key or session, plus the rate limiter) —
#: the same gate the five original routers now use. ``routes_auth`` carries none of that: its
#: own routes are the *only* way to ever obtain a session, so gating them on one would be
#: circular, and an API key is a service credential, not proof of being a particular person, so
#: it is not accepted there either. ``routes_me`` similarly declares its own dependency
#: (``require_user`` / ``require_fresh_user`` / ``require_write``) per route, because different
#: routes under it need different strengths of proof; a single router-wide guard could only ever
#: enforce the weakest of them.
OPTIONAL_ROUTE_GUARDS: dict[str, str] = {"routes_auth": "rate_only", "routes_me": "rate_only"}

DESCRIPTION = (
    "NBA advanced-stats service for the Hardwood iOS dashboard. Implements "
    "contracts/CONTRACT.md: lowerCamelCase JSON, fractions in [0,1] for percentages, and "
    "era-honest nulls — a stat that did not exist in an era is null, never zero."
)


def _switched_off(module_name: str) -> bool:
    """True when ``module_name`` has an environment switch (:data:`ROUTE_SWITCHES`) set to off."""
    variable = ROUTE_SWITCHES.get(module_name)
    if variable is None:
        return False
    return os.environ.get(variable, "").strip().lower() in _FALSE_STRINGS


def _prepare_euroleague() -> None:
    """Start the EuroLeague's own store, or leave it off with a logged reason. Never raises.

    The EuroLeague keeps its own SQLite file with its own tables, and its bootstrap decides what
    that file should hold (nothing, the invented demo league, or the user's workbook) and stamps
    it. This hook only *calls* it, by string name through :func:`importlib.import_module`, because
    nothing outside ``nbastats/euroleague`` may import that package (see the module docstring).
    Every failure mode is the same to the NBA: log it and carry on, so a misconfigured or broken
    EuroLeague turns the EuroLeague off and nothing else. The bootstrap itself never raises; the
    ``try`` is for the import and for the day that stops being true.
    """
    if _switched_off("routes_euroleague"):
        logger.info("HARDWOOD_EL_ENABLED is off: the EuroLeague is not started")
        return
    try:
        bootstrap = importlib.import_module("..euroleague.bootstrap", __package__)
        result = bootstrap.prepare()
    except ImportError:
        logger.info("the EuroLeague package is not present; /v1/el is not served")
        return
    except Exception:  # noqa: BLE001 - the EuroLeague must never take the NBA down
        logger.exception("the EuroLeague could not start; the NBA is unaffected")
        return
    state = getattr(result, "state", "unknown")
    reason = getattr(result, "reason", None)
    logger.info("EuroLeague: %s%s", state, f" ({reason})" if reason else "")


def _prepare_database() -> None:
    """Create the schema, start the EuroLeague's store, and seed the NBA demo league when asked
    and the store is empty."""
    init_db()
    _prepare_euroleague()
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


#: How often the lifespan's retention job runs after its first pass at startup.
RETENTION_PURGE_INTERVAL_SECONDS = 24 * 60 * 60


def _run_retention_purge_once() -> None:
    """One pass of the 30-day erasure the Privacy page promises. Never raises: a locked
    database or a half-migrated store must degrade to "not purged this time", not to a
    service that will not start."""
    try:
        from ..accounts import retention

        with get_sessionmaker()() as db:
            summary = retention.purge(db)
            db.commit()
    except Exception:  # noqa: BLE001 - a retention sweep must never take the process down
        logger.exception("the retention purge failed; it will be retried on the next pass")
        return
    if not summary.is_empty:
        logger.info(
            "retention purge: %s users, %s dashboards, %s expired sessions erased",
            summary.users,
            summary.dashboards,
            summary.sessions,
        )


def _start_retention_purge() -> "asyncio.Task | None":
    """Kick off the daily retention job, or return ``None`` when the accounts feature is not
    installed. ``/legal/privacy`` and the Settings screen both state that a deleted account is
    "erased permanently after 30 days"; before this, only an operator command nobody was told
    to schedule ever did that, so the sentence was false on every default deployment."""
    try:
        importlib.import_module("..accounts.retention", __package__)
    except ImportError:  # pragma: no cover - a stats-only deployment
        return None

    async def loop() -> None:
        while True:
            await asyncio.to_thread(_run_retention_purge_once)
            await asyncio.sleep(RETENTION_PURGE_INTERVAL_SECONDS)

    return asyncio.create_task(loop())


def _guards_for(name: str) -> list:
    """The dependency list one optional router module is included with (see
    :data:`OPTIONAL_ROUTE_GUARDS`)."""
    profile = OPTIONAL_ROUTE_GUARDS.get(name, "standard")
    if profile == "rate_only":
        return [Depends(enforce_rate_limit)]
    return [Depends(require_api_key_or_session), Depends(enforce_rate_limit)]


def _include_optional_routers(application: FastAPI) -> None:
    mounted: set[str] = set()
    for name in OPTIONAL_ROUTE_MODULES:
        if _switched_off(name):
            logger.info(
                "%s is switched off by %s; its routes are not served", name, ROUTE_SWITCHES[name]
            )
            continue
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
        application.include_router(router, prefix=API_PREFIX, dependencies=_guards_for(name))
        mounted.add(name)
    # `/v1/health.authReady` (routes_meta.py) reads this rather than probing the router table
    # itself: whether `routes_auth` actually mounted is exactly "is there any way at all for a
    # browser to obtain a session right now", which no database read can answer any faster.
    application.state.mounted_route_modules = frozenset(mounted)


def _web_origins_from_environment() -> tuple[str, ...]:
    """``HARDWOOD_WEB_ORIGINS``: a comma-separated escape hatch for a deployment that serves the
    SPA from a *different* origin than this API (WEB_DESIGN.md deliberately does not need this
    for the single-origin design itself, but a reverse proxy or a staging split can). Empty by
    default, which is what keeps the existing wildcard-with-no-credentials CORS block untouched.
    """
    raw = os.environ.get("HARDWOOD_WEB_ORIGINS", "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _startup_log_line(auth_settings) -> str:
    """One line naming the three things that most determine what this deployment can do —
    signup mode, which identity providers are lit up, and the address it thinks it is running
    on — so a person reading the log does not have to reconstruct that from four separate
    environment variables.
    """
    providers = ["password"]
    try:
        from ..accounts.providers import provider_status

        status = provider_status(auth_settings)
        providers.extend(name for name, info in status.items() if info.get("enabled"))
    except ImportError:  # nbastats.accounts.providers has not landed yet
        pass
    return (
        f"signup: {auth_settings.signup_mode} | providers: {', '.join(providers)} | "
        f"public: {auth_settings.public_base_url}"
    )


def create_app() -> FastAPI:
    """Build the Hardwood API application."""
    # Before anything reads a setting: `backend/.env` is the file `scripts/web.sh setup`
    # writes and tells the operator to edit, and until this call existed *nothing in the
    # server read it*. Only `web.sh doctor` did, so doctor reported `google: enabled` for
    # credentials the running process had never seen. Both now go through the same
    # `load_env_file`, so they cannot disagree again.
    env_file = load_env_file()
    try:
        auth_settings = get_auth_settings()
    except ValueError as exc:
        # `HARDWOOD_SESSION_DAYS=thirty` and friends. The message from `_env_int`/`_env_bool`
        # names the variable and the bad value; wrap it in the same operator-facing shape the
        # refusals use rather than letting a bare ValueError become twenty lines of
        # interpreter internals under `python -m uvicorn`.
        where = f" in {env_file}" if env_file is not None else ""
        raise RuntimeError(
            f"Hardwood Web refuses to start:\n- {exc}\n"
            f"  Fix that line{where} (or in the environment) and try again."
        ) from exc
    refusals = startup_refusals(auth_settings)
    if refusals:
        raise RuntimeError(
            "Hardwood Web refuses to start:\n" + "\n".join(f"- {reason}" for reason in refusals)
        )
    for warning in startup_warnings(auth_settings):
        logger.warning(warning)
    if env_file is not None:
        logger.info("read configuration from %s", env_file)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        _prepare_database()
        logger.info(_startup_log_line(auth_settings))
        purge_task = _start_retention_purge()
        try:
            yield
        finally:
            if purge_task is not None:
                purge_task.cancel()
                with suppress(asyncio.CancelledError):
                    await purge_task

    # Swagger UI loads its assets from a CDN with no CSP exception carved for them, so an
    # interactive-docs page left reachable on a non-loopback origin is an avoidable enlargement
    # of the XSS surface on the same origin session cookies live on. A loopback deployment (the
    # default) keeps both, unchanged.
    docs_enabled = auth_settings.is_loopback
    application = FastAPI(
        title="Hardwood API",
        version=API_VERSION,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    # Permissive by default: this service is normally read-only from the browser's point of
    # view (no cookie leaves without SameSite already protecting it) and is normally reached
    # from a simulator, a LAN device, or the single origin the web app is served from — see
    # WEB_DESIGN.md §0. Credentials stay off in that default case, so the wildcard origin is
    # legal. HARDWOOD_WEB_ORIGINS is the opt-in escape hatch for a deployment that genuinely
    # serves the SPA from a different origin than this API; it sets `allow_origins` and
    # `allow_credentials` in the same branch so the two can never diverge, and refuses outright
    # to start on the one combination every browser already refuses to honour.
    web_origins = _web_origins_from_environment()
    if "*" in web_origins:
        raise RuntimeError(
            "HARDWOOD_WEB_ORIGINS cannot contain '*': a wildcard origin with credentials is "
            "rejected by every browser."
        )
    cors_kwargs = (
        dict(allow_origins=list(web_origins), allow_credentials=True)
        if web_origins
        else dict(allow_origins=["*"], allow_credentials=False)
    )
    application.add_middleware(
        CORSMiddleware,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER],
        **cors_kwargs,
    )
    # SecurityHeadersMiddleware, then ProxyHeadersGuard, then RequestIdMiddleware — each
    # add_middleware call wraps the ones already registered, so RequestIdMiddleware (added
    # last) stays outermost, exactly as it was before either of the other two existed. See
    # nbastats.api.security's module docstring for the full outer-to-inner request path.
    application.add_middleware(SecurityHeadersMiddleware)
    application.add_middleware(ProxyHeadersGuard)
    application.add_middleware(RequestIdMiddleware)

    install_error_handlers(application)

    guards = [Depends(require_api_key_or_session), Depends(enforce_rate_limit)]
    for router in (
        meta_router,
        players_router,
        teams_router,
        games_router,
        sync_router,
        fantasy_router,
    ):
        application.include_router(router, prefix=API_PREFIX, dependencies=guards)
    _include_optional_routers(application)

    # Last: its catch-all route matches literally any path, so nothing after this point would
    # ever be reached.
    mount_web(application)

    return application


#: Module-level application so ``uvicorn nbastats.api.app:app`` works unchanged.
#:
#: The refusals in ``create_app()`` are written to be read by a person ("Set
#: HARDWOOD_PUBLIC_BASE_URL to an https URL, or ..."). Because this line runs at *import*
#: time and ``scripts/web.sh dev`` ``exec``s uvicorn, a bare ``RuntimeError`` reached the
#: operator as forty-odd lines of runpy/click/importlib frames with the one useful sentence
#: last. Printing the reasons and exiting 2 is the same failure with the noise removed.
try:
    app = create_app()
except RuntimeError as exc:
    print(str(exc), file=sys.stderr, flush=True)
    raise SystemExit(2) from None
