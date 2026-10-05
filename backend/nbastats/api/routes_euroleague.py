"""``/v1/el``: the EuroLeague's router, exposed to ``api/app.py`` (a shim; the code is sealed in
``nbastats/euroleague/api/routes.py``). The league is chosen by URL, never by a header."""

from __future__ import annotations

from ..euroleague.api.routes import router

__all__ = ["router"]
