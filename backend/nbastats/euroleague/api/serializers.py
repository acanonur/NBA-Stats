"""Request bodies and the one payload the routes build themselves: the model settings.

Every read payload is built by :mod:`nbastats.euroleague.read`, where both the routes and the
dashboard widgets can reach it; this module holds only what is specific to the HTTP layer.

Request bodies
--------------
Three bodies exist, one per write the design allows, and each is deliberately narrow. Field names
are lowerCamelCase on the wire (Python names are snake_case, aliased through
``pydantic.alias_generators.to_camel``), and every name is on the short list of parameters a new
route may take (``nbastats.shared.market_guard.ALLOWED_PARAMETERS``): a status and where it came
from, a pasted link, a model setting's key and value. **There is no field anywhere that could carry
a number to compare a projection with**, and the model-settings write has no provenance field: a
value written here is ``manual`` by definition.

``extra="forbid"`` on every body, so a client that sends a field this service does not know about
(a line, say) gets a ``400`` naming it instead of having it silently dropped.

The status in the availability body is a plain string on purpose: validating it with a ``Literal``
would turn a typo into a generic ``400 bad_request``. The design has a code for that
(``invalid_status``) and the read layer raises it with the words a person needs.

Model settings
--------------
:func:`model_settings_payload` lists every allowlisted key with its value in force, its provenance
and whether it is still the default, so a screen can show which numbers are the workbook's, which
are Hardwood's and which the user has changed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from ...shared import refs
from ..read.queries import ReadContext

__all__ = [
    "Body",
    "AvailabilityBody",
    "NewsLinkBody",
    "SettingItem",
    "ModelSettingsPatch",
    "model_settings_payload",
]


class Body(BaseModel):
    """Base of every request body: camelCase on the wire, no unknown fields."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class AvailabilityBody(Body):
    """``POST /v1/el/availability``: a status, and where it came from."""

    club_code: str
    status: str
    source_label: str
    source_published_at: datetime | str
    person_code: str | None = None
    player_name: str | None = None
    game_id: str | None = None
    reason_category: str | None = None
    reason_text: str | None = None
    expected_return_text: str | None = None
    source_url: str | None = None


class NewsLinkBody(Body):
    """``POST /v1/el/news/links``: a headline a person pasted."""

    title: str
    link: str
    published_at: datetime | str
    source_name: str
    team_ids: list[str] = []
    player_ids: list[str] = []


class SettingItem(Body):
    key: str
    value: float


class ModelSettingsPatch(Body):
    """``PATCH /v1/el/model-settings``: one or more allowlisted settings."""

    settings: list[SettingItem]


def model_settings_payload(ctx: ReadContext, freshness: dict[str, Any]) -> dict[str, Any]:
    """Every allowlisted setting with its value in force and where that value came from."""
    return {
        "league": "euroleague",
        "freshness": freshness,
        "settings": [
            {
                "key": key,
                "value": setting.value,
                "provenance": setting.provenance,
                "isDefault": setting.is_default,
                "setAt": refs.rfc3339(setting.set_at),
                "description": setting.description,
            }
            for key, setting in ctx.settings.items()
        ],
    }
