"""Hardwood backend: NBA advanced-stats service for the Hardwood iOS dashboard.

The package is layered so that each piece can be used on its own:

``config``   environment-driven settings (no pydantic-settings dependency)
``catalog``  the shared contract catalogs (metrics / widgets / presets), pure stdlib
``models``   SQLAlchemy 2.0 declarative tables mirroring ``contracts/CONTRACT.md``
``db``       engine, session factory and the sync-state singleton helpers
``identities`` real NBA names, person ids, headshots and the 30 franchises, pure stdlib
``seed``     a deterministic synthetic league, wearing those identities, so the app runs
             with zero network access

``API_VERSION`` is what ``GET /v1/health`` reports as ``version``.
"""
from __future__ import annotations

__all__ = ["__version__", "API_VERSION", "SCHEMA_VERSION"]

__version__ = "1.0.0"
API_VERSION = "1.0.0"

#: Version of the shared contract this backend implements (contracts/*.json schemaVersion).
SCHEMA_VERSION = 1
