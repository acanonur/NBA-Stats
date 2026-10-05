"""User-scoped dashboard storage: every function here is a query or a write against
``accounts.models.UserDashboard``, gated by the caller's ``user_id`` and nothing else.

The invariant this module exists to hold
-------------------------------------------
Every public function takes ``user_id`` as its second positional parameter, and every query
below filters on it. That is not a style preference: ``test_dashboards_api.py::
test_another_users_layout_id_is_404`` asserts that reading, updating or deleting a layout id
that belongs to somebody else answers **404**, never 403 — a 403 would confirm the id exists,
which is itself information the requester is not entitled to. A composite primary key of
``(user_id, layout_id)`` (see ``accounts/models.py::UserDashboard``) makes "look up this id"
and "look up this id *for this user*" different queries only if a caller remembers to add the
second clause every time; putting ``user_id`` first in every signature here is what makes
forgetting it a visible, awkward thing to type rather than an easy omission.

Concurrency, and why it is optimistic rather than a lock
------------------------------------------------------------
``UserDashboard.revision`` starts at 1 and increments on every successful write.
:func:`update_dashboard` requires the caller's ``expected_revision`` to match the stored value
exactly; a mismatch raises :class:`StaleWrite`, which carries the *server's* current document so
the caller can offer "This dashboard changed on another device" without a second round trip.
There is no row lock: two browser tabs (or a browser tab and an iOS device, via the export/import
file bridge) can each hold a stale copy, and the first one back wins outright while the second
gets a conflict it can resolve instead of a write that silently vanished.

Every write goes through ``accounts.layouts`` before it touches a row
--------------------------------------------------------------------------
:func:`create_dashboard`, :func:`update_dashboard` and :func:`import_envelope` all call
``layouts.migrate_and_normalize()`` (or, for a preset, ``layouts.fork_preset()``, which never
needs migrating — a shipped preset is already valid) before writing anything. That is what
``WEB_DESIGN.md`` §4.3 means by "the store can never hold a dashboard the resolver cannot
resolve": a layout this module accepted is one ``nbastats.widgets`` has already agreed to render,
because both read the same ``nbastats.catalog`` widget specs.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import catalog
from ..db import utcnow
from . import layouts
from .models import UserDashboard

__all__ = [
    "MAX_DASHBOARDS_PER_USER",
    "MAX_WIDGETS_PER_DASHBOARD",
    "MAX_DOCUMENT_BYTES",
    "MAX_IMPORT_BYTES",
    "DashboardNotFound",
    "TooManyDashboards",
    "PayloadTooLarge",
    "StaleWrite",
    "list_dashboards",
    "get_dashboard",
    "create_dashboard",
    "create_from_preset",
    "update_dashboard",
    "delete_dashboard",
    "restore_dashboard",
    "reorder",
    "export_envelope",
    "import_envelope",
]

MAX_DASHBOARDS_PER_USER = 50
MAX_WIDGETS_PER_DASHBOARD = 64
MAX_DOCUMENT_BYTES = 256 * 1024
MAX_IMPORT_BYTES = 1024 * 1024

#: iOS's own export envelope filename and shape (§4.3): ``{"schemaVersion", "updatedAt",
#: "layouts": [...]}`` — ``export_envelope`` produces exactly this, ``import_envelope`` accepts
#: it (plus the two other shapes ``layouts.migrate_collection`` already tolerates).
EXPORT_SCHEMA_VERSION = 1


class DashboardNotFound(Exception):
    """No dashboard with this id belongs to this user — whether because it never existed,
    it belongs to someone else, or it was already soft-deleted. Always a 404 at the route
    layer; never a 403 (see the module docstring)."""


class TooManyDashboards(Exception):
    """The user already holds :data:`MAX_DASHBOARDS_PER_USER` dashboards."""

    def __init__(self, limit: int = MAX_DASHBOARDS_PER_USER) -> None:
        self.limit = limit
        super().__init__(f"You already have {limit} dashboards, which is the most this account can hold.")


class PayloadTooLarge(Exception):
    """The document (or the whole import payload) exceeds this module's byte cap."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"That dashboard is larger than the {limit}-byte limit.")


class StaleWrite(Exception):
    """``PUT``'s ``If-Match`` did not match the stored revision.

    Carries the row as it exists on the server right now, so the caller can answer ``409`` with
    the server's current document in the body (§4.3) rather than making the client re-fetch.
    """

    def __init__(self, current: UserDashboard) -> None:
        self.current = current
        super().__init__("This dashboard changed on another device.")


# --------------------------------------------------------------------------- reads


def list_dashboards(db: Session, user_id: str) -> list[UserDashboard]:
    """Every non-deleted dashboard for this user, in switcher order."""
    return list(
        db.execute(
            select(UserDashboard)
            .where(UserDashboard.user_id == user_id, UserDashboard.deleted_at.is_(None))
            .order_by(UserDashboard.position, UserDashboard.created_at)
        )
        .scalars()
        .all()
    )


def get_dashboard(db: Session, user_id: str, layout_id: str) -> UserDashboard | None:
    """The one dashboard, or ``None`` — never another user's, and never a soft-deleted one."""
    return db.execute(
        select(UserDashboard).where(
            UserDashboard.user_id == user_id,
            UserDashboard.layout_id == layout_id,
            UserDashboard.deleted_at.is_(None),
        )
    ).scalar_one_or_none()


def _get_any(db: Session, user_id: str, layout_id: str) -> UserDashboard | None:
    """Like :func:`get_dashboard`, but also returns a soft-deleted row — used by
    :func:`restore_dashboard` and by the fresh-id collision check in :func:`create_dashboard`."""
    return db.execute(
        select(UserDashboard).where(
            UserDashboard.user_id == user_id, UserDashboard.layout_id == layout_id
        )
    ).scalar_one_or_none()


# --------------------------------------------------------------------------- writes


def _count_active(db: Session, user_id: str) -> int:
    return len(list_dashboards(db, user_id))


def _next_position(db: Session, user_id: str) -> int:
    existing = list_dashboards(db, user_id)
    return (max((row.position for row in existing), default=-1)) + 1


def _document_bytes(layout: dict[str, Any]) -> bytes:
    return layouts.canonical_json(layout).encode("utf-8")


def _clip_widgets(layout: dict[str, Any], notes: list[str]) -> dict[str, Any]:
    widgets = layout.get("widgets") or []
    if len(widgets) <= MAX_WIDGETS_PER_DASHBOARD:
        return layout
    notes.append(
        f"Kept the first {MAX_WIDGETS_PER_DASHBOARD} widgets; a dashboard supports at most "
        f"{MAX_WIDGETS_PER_DASHBOARD}."
    )
    return dict(layout, widgets=widgets[:MAX_WIDGETS_PER_DASHBOARD])


def _row_from_layout(
    *, user_id: str, layout: dict[str, Any], position: int, revision: int, created_at, updated_at
) -> UserDashboard:
    return UserDashboard(
        user_id=user_id,
        layout_id=layout["id"],
        document_json=layouts.canonical_json(layout),
        schema_version=int(layout["schemaVersion"]),
        name=str(layout["name"]),
        icon=str(layout["icon"]),
        accent=str(layout["accent"]),
        presentation=str(layout["presentation"]),
        preset_key=layout.get("presetKey"),
        tagline=layout.get("tagline"),
        is_preset=bool(layout.get("isPreset") or False),
        widget_count=len(layout.get("widgets") or []),
        position=position,
        revision=revision,
        created_at=created_at,
        updated_at=updated_at,
    )


def _apply_layout_to_row(row: UserDashboard, layout: dict[str, Any]) -> None:
    row.document_json = layouts.canonical_json(layout)
    row.schema_version = int(layout["schemaVersion"])
    row.name = str(layout["name"])
    row.icon = str(layout["icon"])
    row.accent = str(layout["accent"])
    row.presentation = str(layout["presentation"])
    row.preset_key = layout.get("presetKey")
    row.tagline = layout.get("tagline")
    row.is_preset = bool(layout.get("isPreset") or False)
    row.widget_count = len(layout.get("widgets") or [])


def create_dashboard(db: Session, user_id: str, raw: Any) -> tuple[UserDashboard, list[str]]:
    """Migrate ``raw`` and store it as a brand-new dashboard.

    Propagates ``layouts.LayoutTooNew`` / ``NotALayout`` / ``UnreadableLayout`` unchanged — the
    route layer maps each to the contract's error codes. Raises :class:`TooManyDashboards` at
    the cap and :class:`PayloadTooLarge` over the size cap.
    """
    if _count_active(db, user_id) >= MAX_DASHBOARDS_PER_USER:
        raise TooManyDashboards()

    result = layouts.migrate_and_normalize(raw)
    notes = list(result.notes)
    layout = _clip_widgets(result.layout, notes)

    # A client-supplied id that collides with a dashboard this user already holds (active or
    # soft-deleted) would otherwise silently overwrite it on the next line — POST creates,
    # it never updates. Minting a fresh id here is the same repair migrate_document already
    # makes for two widgets that share an id, applied one level up.
    if _get_any(db, user_id, layout["id"]) is not None:
        layout = dict(layout, id=layouts.fresh_id())
        notes.append("This dashboard's id was already in use on your account; it was given a new one.")

    document = _document_bytes(layout)
    if len(document) > MAX_DOCUMENT_BYTES:
        raise PayloadTooLarge(MAX_DOCUMENT_BYTES)

    now = utcnow()
    row = _row_from_layout(
        user_id=user_id,
        layout=layout,
        position=_next_position(db, user_id),
        revision=1,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    return row, notes


def create_from_preset(db: Session, user_id: str, preset_key: str) -> tuple[UserDashboard, list[str]]:
    """Fork a shipped preset (``catalog.UnknownPresetError`` propagates on a bad key) and store
    the fork. A preset's own widgets are already catalog-valid, so this skips ``normalize()``
    and only enforces the same caps :func:`create_dashboard` does."""
    if _count_active(db, user_id) >= MAX_DASHBOARDS_PER_USER:
        raise TooManyDashboards()

    preset = catalog.preset(preset_key)
    layout, _id_map = layouts.fork_preset(preset)
    notes: list[str] = []
    layout = _clip_widgets(layout, notes)

    if _get_any(db, user_id, layout["id"]) is not None:  # astronomically unlikely; cheap to guard
        layout = dict(layout, id=layouts.fresh_id())

    document = _document_bytes(layout)
    if len(document) > MAX_DOCUMENT_BYTES:
        raise PayloadTooLarge(MAX_DOCUMENT_BYTES)

    now = utcnow()
    row = _row_from_layout(
        user_id=user_id,
        layout=layout,
        position=_next_position(db, user_id),
        revision=1,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    return row, notes


def update_dashboard(
    db: Session, user_id: str, layout_id: str, raw: Any, *, expected_revision: int
) -> tuple[UserDashboard, list[str]]:
    """Whole-document replace, gated by optimistic concurrency.

    Raises :class:`DashboardNotFound` (404), :class:`StaleWrite` (409, carrying the server's
    current row) or a ``layouts`` migration error, in that order, before anything is written.
    """
    row = get_dashboard(db, user_id, layout_id)
    if row is None:
        raise DashboardNotFound()
    if row.revision != expected_revision:
        raise StaleWrite(row)

    result = layouts.migrate_and_normalize(raw)
    notes = list(result.notes)
    layout = _clip_widgets(result.layout, notes)
    # A PUT to /v1/dashboards/{layoutId} always targets that id, whatever the body's own `id`
    # field says — the URL is the identity, not the document.
    layout = dict(layout, id=layout_id)

    document = _document_bytes(layout)
    if len(document) > MAX_DOCUMENT_BYTES:
        raise PayloadTooLarge(MAX_DOCUMENT_BYTES)

    _apply_layout_to_row(row, layout)
    row.revision += 1
    row.updated_at = utcnow()
    db.flush()
    return row, notes


def delete_dashboard(db: Session, user_id: str, layout_id: str) -> None:
    """Soft delete: ``deleted_at`` is stamped, the 30-day grace period ``admin.py purge``
    eventually sweeps starts now. Raises :class:`DashboardNotFound` if there is nothing active
    to delete."""
    row = get_dashboard(db, user_id, layout_id)
    if row is None:
        raise DashboardNotFound()
    row.deleted_at = utcnow()
    db.flush()


def restore_dashboard(db: Session, user_id: str, layout_id: str) -> UserDashboard:
    """Undo a soft delete within the grace period. Raises :class:`DashboardNotFound` if the row
    does not exist at all, or exists but was never deleted (nothing to restore)."""
    row = _get_any(db, user_id, layout_id)
    if row is None or row.deleted_at is None:
        raise DashboardNotFound()
    row.deleted_at = None
    row.updated_at = utcnow()
    db.flush()
    return row


def reorder(db: Session, user_id: str, layout_ids: list[str]) -> None:
    """Set the switcher order from ``layout_ids``. An id that does not name one of this user's
    active dashboards is silently skipped rather than treated as an error — the switcher can
    only ever offer ids it already showed the caller."""
    rows = {row.layout_id: row for row in list_dashboards(db, user_id)}
    for position, layout_id in enumerate(layout_ids):
        row = rows.get(layout_id)
        if row is not None:
            row.position = position
    db.flush()


# --------------------------------------------------------------------------- export / import


def export_envelope(db: Session, user_id: str) -> dict[str, Any]:
    """The iOS ``Layouts.json`` envelope, verbatim: every active dashboard's own document,
    already-migrated and therefore already valid, in switcher order."""
    rows = list_dashboards(db, user_id)
    updated_at = max((row.updated_at for row in rows), default=utcnow())
    return {
        "schemaVersion": EXPORT_SCHEMA_VERSION,
        "updatedAt": updated_at.replace(microsecond=0).isoformat() + "Z",
        "layouts": [json.loads(row.document_json) for row in rows],
    }


def import_envelope(
    db: Session, user_id: str, raw: Any
) -> tuple[list[UserDashboard], list[str], list[str]]:
    """Import the iOS envelope, a bare array, or a single bare layout (``layouts.
    migrate_collection`` already tolerates all three). Returns ``(imported, notes, failures)``:
    one row per layout that made it in, every migration note from every layout in order, and one
    line per layout that could not be imported at all (an unreadable, too-new or non-layout
    entry, or one that arrived after the account's dashboard cap was already reached).

    Raises :class:`PayloadTooLarge` before touching the database when ``raw`` is text or bytes
    over :data:`MAX_IMPORT_BYTES`.

    That guard only fires for a caller that hands this function the *undecoded* payload (the
    admin CLI, a test, a future file importer). It is **not** what protects the HTTP route:
    ``POST /v1/dashboards/import`` reads and measures the request body itself before parsing it,
    precisely because a route that declares its body as ``dict | list`` has already let FastAPI
    decode an unbounded payload into memory by the time this line runs, making the check
    unreachable and the contracted ``413`` impossible to produce.
    """
    if isinstance(raw, (str, bytes, bytearray)) and len(raw) > MAX_IMPORT_BYTES:
        raise PayloadTooLarge(MAX_IMPORT_BYTES)

    results, parse_failures = layouts.migrate_collection(raw)
    imported: list[UserDashboard] = []
    notes: list[str] = []
    failures: list[str] = list(parse_failures)

    for result in results:
        if _count_active(db, user_id) >= MAX_DASHBOARDS_PER_USER:
            failures.append(
                f"“{result.layout.get('name', 'Dashboard')}” was not imported: this "
                f"account already holds {MAX_DASHBOARDS_PER_USER} dashboards."
            )
            continue

        layout = _clip_widgets(result.layout, notes)
        if _get_any(db, user_id, layout["id"]) is not None:
            layout = dict(layout, id=layouts.fresh_id())

        document = _document_bytes(layout)
        if len(document) > MAX_DOCUMENT_BYTES:
            failures.append(
                f"“{layout.get('name', 'Dashboard')}” was not imported: it is larger "
                f"than the {MAX_DOCUMENT_BYTES}-byte limit."
            )
            continue

        now = utcnow()
        row = _row_from_layout(
            user_id=user_id,
            layout=layout,
            position=_next_position(db, user_id),
            revision=1,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.flush()
        imported.append(row)
        notes.extend(result.notes)

    return imported, notes, failures
