"""The HTTP surface: ``contracts/CONTRACT.md`` §3, and nothing that is not in it.

Module map::

    schemas      pydantic models for every contract object (camelCase on the wire)
    errors       the error envelope, the codes, the request-id middleware
    deps         session, API-key gate, rate limiter, query-parameter parsing
    serializers  ORM rows -> contract objects, and the one place era rules are applied
    routes_*     one module per group of endpoints
    app          ``create_app()`` and the module-level ``app`` uvicorn serves

Nothing is imported eagerly here: ``nbastats.catalog`` and ``nbastats.metrics`` stay usable
in a process that has no FastAPI installed, and the route modules import each other freely
without a package-level cycle. ``from nbastats.api import create_app`` still works — the
lazy ``__getattr__`` below resolves it on first use.
"""
from __future__ import annotations

from typing import Any

__all__ = ["create_app", "app"]


def __getattr__(name: str) -> Any:
    """Resolve ``create_app`` / ``app`` on first access, not at package import."""
    if name in __all__:
        from . import app as _app_module

        return getattr(_app_module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
