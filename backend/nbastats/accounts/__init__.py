"""The accounts package: web sign-in, sessions and saved dashboards for Hardwood Web.

Why this is a separate package rather than new modules under ``nbastats/`` directly
-------------------------------------------------------------------------------
Two decisions bind everything placed under here, both recorded (and verified) in
``WEB_DESIGN.md``:

* **Account tables live on their own SQLAlchemy declarative base**, ``accounts.models.
  AccountBase``, never on ``nbastats.models.Base``. ``nbastats/seed.py::_clear()`` deletes
  every table on ``Base.metadata`` on every call, and ``nbastats/api/app.py::
  _prepare_database()`` calls the seeder unattended whenever ``HARDWOOD_DEMO_MODE`` is set
  and the ``teams`` table is empty — which ``scripts/serve_dev.sh`` makes the default. Putting
  user accounts on the shared base would silently delete every account, session and saved
  dashboard the first time somebody boots the demo against an empty stats database. See
  ``accounts/models.py`` for the base itself and the six tables on it.
* **Auth configuration lives on its own frozen dataclass**, ``accounts.config.AuthSettings``,
  separate from ``nbastats.config.Settings``. A stats-only deployment sets none of the
  ``HARDWOOD_*`` auth variables; folding cookies, OIDC and SMTP settings into the same
  dataclass as ``DATABASE_URL`` would force every caller of ``nbastats.config.get_settings()``
  to reason about them regardless.

What lives here now, and what WP1 added
------------------------------------------
This package was filled in across two work packages that never touched the same file. WP0
(Foundation) landed first and owns exactly ``models.py``, ``config.py`` and this file's
docstring. WP1 (Accounts, auth and identity) landed next and owns everything that actually
touches a live session, a password or an identity provider: ``passwords.py``, ``sessions.py``,
``csrf.py``, ``tokens.py``, ``mail.py``, ``linking.py``, ``admin.py`` and the ``providers/``
package. This file implements none of that itself — it only re-exports the public identity
surface, the same pattern ``nbastats/models.py`` uses for ``Base`` — so that
``nbastats/api/deps.py`` (WP2) can import ``require_user`` and friends without knowing WP1's
internal module layout, and so a future reshuffle of *which* WP1 module defines
``current_session`` never touches a caller outside this package.

Nothing else belongs in this file: no session verification, no password handling, no route
logic — all of that lives in the modules imported below.
"""
from __future__ import annotations

from typing import Any

__all__ = ["current_session", "require_user", "require_fresh_user", "require_write"]

#: Which submodule actually defines each re-exported name. Resolved on first access, never at
#: import time — see :func:`__getattr__`.
_LAZY_EXPORTS = {
    "current_session": ".sessions",
    "require_user": ".sessions",
    "require_fresh_user": ".sessions",
    "require_write": ".csrf",
}


def __getattr__(name: str) -> Any:
    """Resolve the re-exported identity surface on first use (PEP 562).

    These used to be plain ``from .csrf import …`` / ``from .sessions import …`` statements at
    module scope, which meant that importing *anything* from this package —
    ``accounts.config`` in ``api/app.py``, ``accounts.models`` in ``api/deps.py``,
    ``accounts.config`` again in ``api/routes_meta.py`` — first executed ``csrf`` and
    ``sessions`` in full. ``deps.py`` carries a documented ``try/except ImportError`` fallback
    for "the accounts feature has not landed", and WEB_DESIGN.md §4.5 promises that in that case
    "the service is byte-identically today's service"; with eager re-exports here that fallback
    was unreachable, because three unconditional module-scope imports had already crashed the
    whole service before the ``try`` was reached.

    Deferring costs one ``sys.modules`` lookup per attribute access and makes
    ``accounts.{config,models}`` importable without pulling in the session machinery, so a
    checkout missing a session dependency degrades the way it is documented to.
    """
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(target, __name__), name)
    globals()[name] = value  # cache, so this runs once per name per process
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
