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

What lives here now, and what WP1 adds next
--------------------------------------------
This package is filled in across two work packages that never touch the same file. WP0
(Foundation) lands first and owns exactly ``models.py``, ``config.py`` and this file — as a
docstring plus the stub below, nothing more. WP1 (Accounts, auth and identity) lands next and
owns everything that actually touches a live session, a password or an identity provider:
``passwords.py``, ``sessions.py``, ``csrf.py``, ``tokens.py``, ``mail.py``, ``linking.py``,
``admin.py`` and the ``providers/`` package. This file deliberately implements none of that —
its only job is to be the one place WP2's ``nbastats/api/deps.py`` imports the public
identity surface from, so ``deps.py`` never has to know WP1's internal module layout.

Do not implement auth logic in this file. If you are WP1, add real imports where the
placeholder section below says to, and nowhere else in this file.
"""
from __future__ import annotations

__all__: list[str] = []

# ---------------------------------------------------------------------------------------
# WP1 fills in the exports below once the modules that define them exist. Per WEB_DESIGN.md's
# WP1 section: "WP1 exports require_user, require_fresh_user, require_write and
# current_session from accounts/__init__.py; WP2 re-exports them through deps.py."
#
# Re-export the names from the module that actually owns the implementation — the same
# pattern nbastats/models.py uses for Base — rather than defining them here:
#
#     from .sessions import current_session                       # noqa: F401
#     from .deps_support import require_user, require_fresh_user, require_write  # noqa: F401
#
# __all__ = ["current_session", "require_user", "require_fresh_user", "require_write"]
#
# (Module names above are illustrative — WP1's own file layout in §2 of WEB_DESIGN.md is
# authoritative.) Nothing else belongs in this file: no session verification, no password
# handling, no route logic.
# ---------------------------------------------------------------------------------------
