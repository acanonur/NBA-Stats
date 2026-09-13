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

The ASGI callable itself is ``nbastats.api.app:app`` (what ``uvicorn`` is pointed at in the
README and in ``scripts/serve_dev.sh``). ``nbastats.api.app`` is the *module*: Python binds a
submodule onto its package the moment anything imports it, so a package attribute of the
same name could not mean the ``FastAPI`` instance for longer than one import.
"""
from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

__all__ = ["create_app", "app"]

#: Names ``__getattr__`` resolves, and where each comes from inside ``.app``.
_LAZY = {"create_app": "create_app", "app": None}


def __getattr__(name: str) -> Any:
    """Resolve ``create_app`` / ``app`` on first access, not at package import.

    :func:`importlib.import_module` rather than ``from . import app``: the ``from`` form goes
    through ``_handle_fromlist``, which probes the package with ``hasattr`` — and that call
    lands straight back in this function, so the lazy import recursed until the interpreter
    stopped it. Every documented entry point into the service went through here, so the
    recursion was reachable from ``from nbastats.api import create_app``.
    """
    if name not in _LAZY:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module: ModuleType = importlib.import_module(f"{__name__}.app")
    attribute = _LAZY[name]
    return module if attribute is None else getattr(module, attribute)
