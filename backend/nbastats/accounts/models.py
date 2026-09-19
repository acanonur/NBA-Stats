"""SQLAlchemy 2.0 declarative models for Hardwood Web accounts, sessions and saved
dashboards — deliberately on their **own declarative base and their own ``MetaData``**,
never on :class:`nbastats.models.Base`.

Why a second base is the whole point, not a tidiness preference
-----------------------------------------------------------------
``nbastats/seed.py::_clear()`` is::

    def _clear(session):
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(delete(table))

and ``nbastats/api/app.py::_prepare_database()`` calls ``seed_database()`` — which calls
``_clear()`` unconditionally, every time — whenever ``HARDWOOD_DEMO_MODE`` is set and the
``teams`` table is empty. ``backend/scripts/serve_dev.sh`` exports ``HARDWOOD_DEMO_MODE=1``
by default. So: one empty stats table at boot, with account tables on the shared ``Base``,
means every account, session and saved dashboard is deleted, unattended, with no log line
anyone would think to look for. Putting these tables on ``AccountBase.metadata`` instead
means ``Base.metadata.sorted_tables`` — and therefore ``_clear()``, ``_INSERT_ORDER`` in
``seed.py``, and ``render_schema_sql()`` in ``nbastats/models.py`` — never sees them at all.
``tests/test_accounts_schema.py::test_a_user_survives_reseeding_the_stats_tables`` is the
test that proves this empirically; it is the most important test in this module's package.

A second, independent reason: without a separate metadata, whether the account tables show
up in ``nbastats/schema.sql`` (and therefore whether
``tests/test_models.py::test_schema_sql_is_current`` passes) would depend on whether some
earlier-imported test module happened to import ``nbastats.accounts`` first — a flaky test
waiting to happen. With a separate metadata, ``nbastats/models.py::render_schema_sql()``
cannot see these tables no matter what else has been imported.

Consequences this module is built around
------------------------------------------
* **No cross-metadata foreign keys.** ``User.favorite_player_id`` / ``favorite_team_id`` are
  plain nullable ``Integer`` columns with no ``ForeignKey`` — a string FK cannot resolve
  across two ``MetaData`` objects, and the widget layer (``widgets/base.py::
  resolve_subject_token``) already re-validates a favourite on every call and degrades to
  ``$featured_player`` when it is stale. Foreign keys *within* these tables (e.g.
  ``UserIdentity.user_id -> User.user_id``) are declared normally, with ``ON DELETE CASCADE``
  — enforced at the database level once ``nbastats/db.py`` turns SQLite foreign-key checking
  on for its engine.
* **This module imports nothing from ``nbastats.models`` or ``nbastats.db``.** Every
  timestamp column below is a plain ``DateTime`` with no Python-level default; the module
  that creates a row (``sessions.py``, ``store.py``, ``linking.py``, ``admin.py`` — all WP1
  or WP2 territory) sets it explicitly with ``nbastats.db.utcnow()``, exactly the way
  ``seed.py``, ``routes_dashboard.py`` and ``ingest/*.py`` already do for the stats schema.
  That is what keeps ``nbastats/db.py``'s import of :class:`AccountBase` (needed so
  ``init_db()`` can register and create these tables) a clean one-way dependency: db.py ->
  accounts.models, with nothing pulling in the other direction.
* **``create_all`` never ``ALTER``s and there is no Alembic here.** ``User.profile_json`` and
  ``UserDashboard.document_json`` are ``TEXT`` escape hatches so most future fields need no
  DDL migration; every column that could plausibly move house is nullable.
* **Percentages / lowerCamelCase JSON rules do not apply to this module.** Those are
  ``nbastats/models.py`` conventions for stats served on the wire; nothing here is a
  statistic. Wire translation (snake_case columns -> lowerCamelCase JSON) is the API layer's
  job, same as the stats tables.

``nbastats/accounts/schema.sql`` is the committed, generated DDL for exactly the tables
below: regenerate it with ``python3 -m nbastats.accounts.models > nbastats/accounts/
schema.sql`` after any change here, and ``tests/test_accounts_schema.py`` fails the build if
it drifts — the same discipline ``nbastats/schema.sql`` already has for the stats schema.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    delete,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

__all__ = [
    "AccountBase",
    "User",
    "UserIdentity",
    "AuthSession",
    "AuthToken",
    "OAuthTransaction",
    "UserDashboard",
    "WebInvite",
    "THEME_PREFERENCES",
    "PROVIDERS",
    "AUTH_METHODS",
    "TOKEN_PURPOSES",
    "OAUTH_INTENTS",
    "ACCOUNT_TABLE_NAMES",
    "USER_OWNED_TABLES",
    "delete_user",
    "render_schema_sql",
]


class AccountBase(DeclarativeBase):
    """Declarative base for every Hardwood Web account table.

    A distinct class (and therefore a distinct ``MetaData``) from :class:`nbastats.models.
    Base` — see the module docstring for why that separation is load-bearing, not stylistic.
    """


#: ``users.theme_preference`` is a three-way toggle, not free text — enforced with a CHECK
#: constraint below and reused here so the API layer validates against the same tuple.
THEME_PREFERENCES = ("system", "light", "dark")

#: Identity providers a :class:`UserIdentity` row can name. Kept here (not just in
#: ``providers/__init__.py``, which WP1 owns) so this module documents its own column values
#: without depending on a module that does not exist yet.
PROVIDERS = ("google", "apple")

#: How an :class:`AuthSession` was established — carried on the row for the sessions-list UI
#: and audit purposes; never used to grant more or less access than the session itself.
AUTH_METHODS = ("password", "google", "apple")

#: ``auth_tokens.purpose`` values. Minting a token of a given purpose consumes every other
#: outstanding token of that purpose for that user (enforced in ``tokens.py``, WP1).
TOKEN_PURPOSES = ("verify", "reset", "email_change")

#: ``oauth_transactions.intent`` values: a fresh sign-in, versus attaching a provider to an
#: already-signed-in user.
OAUTH_INTENTS = ("signin", "link")


class User(AccountBase):
    """A Hardwood Web account.

    ``email`` may be ``NULL`` and, when it is, ``email_lookup`` may repeat ``NULL`` for many
    rows (SQLite's ``UNIQUE`` treats every ``NULL`` as distinct) — this is deliberate: an
    Apple private-relay user with no ``email`` at all is still exactly one account.
    ``favorite_player_id`` / ``favorite_team_id`` are plain integers with no foreign key; see
    the module docstring for why a cross-metadata FK is not possible and not needed.
    """

    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_email_lookup", "email_lookup"),
        CheckConstraint(
            "theme_preference IN ('system','light','dark')",
            name="ck_users_theme_preference",
        ),
    )

    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    email_lookup: Mapped[str | None] = mapped_column(String(320), nullable=True, unique=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_private_relay: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    given_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    family_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    favorite_player_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    favorite_team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected_dashboard_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    theme_preference: Mapped[str] = mapped_column(String(8), nullable=False, default="system")
    profile_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UserIdentity(AccountBase):
    """A linked Google or Apple identity. ``(provider, subject)`` is the join key — the OIDC
    ``sub`` is never empty and is the only thing an identity is ever matched on; ``email_at_
    link`` is informational only and is never matched against on a later sign-in.
    """

    __tablename__ = "user_identities"
    __table_args__ = (
        UniqueConstraint("provider", "subject", name="uq_user_identities_provider_subject"),
        UniqueConstraint("user_id", "provider", name="uq_user_identities_user_provider"),
        Index("ix_user_identities_user", "user_id"),
    )

    identity_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email_at_link: Mapped[str | None] = mapped_column(String(320), nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuthSession(AccountBase):
    """An opaque server-side session. The cookie value is ``f"{session_id}.{secret}"``; only
    ``sha256(secret)`` is ever stored, in ``token_hash``. ``csrf_hash`` is the sha256 of the
    synchroniser token returned to the client in JSON — there is no CSRF cookie in this
    design (cookies are host-scoped, not port-scoped, so on ``http://localhost:*`` any other
    local server could set one and defeat a double-submit check).
    """

    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_user", "user_id"),
        Index("ix_auth_sessions_idle_expires", "idle_expires_at"),
    )

    session_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    auth_method: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    authenticated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rotated_from: Mapped[str | None] = mapped_column(String(32), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_prefix: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AuthToken(AccountBase):
    """A single-use, hashed token for email verification, password reset or an email change.
    Only ``sha256(raw)`` is ever stored, in ``token_hash``; ``payload_json`` carries purpose-
    specific data such as a pending new address.
    """

    __tablename__ = "auth_tokens"
    __table_args__ = (Index("ix_auth_tokens_user_purpose", "user_id", "purpose"),)

    token_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OAuthTransaction(AccountBase):
    """Server-side state for one in-flight Google or Apple authorization-code round trip,
    keyed by ``handle_hash`` — the sha256 of the ``hw_oauth`` cookie value, a **different**
    random value from ``state_hash`` (the sha256 of the ``state`` sent to the identity
    provider). Splitting them means a stolen ``state`` (it travels in a URL, a log, a
    ``Referer``) has no matching cookie, and an attacker who can only write a cookie has no
    matching ``state`` — both are required, and the row is consumed once.

    ``link_user_id`` is a plain column, not a foreign key: it is only ever read after the
    matching ``User`` row has been confirmed to still exist, and this table's rows expire
    after ten minutes regardless.
    """

    __tablename__ = "oauth_transactions"

    handle_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    next_path: Mapped[str] = mapped_column(String(512), nullable=False)
    intent: Mapped[str] = mapped_column(String(8), nullable=False)
    link_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UserDashboard(AccountBase):
    """One saved dashboard layout. ``document_json`` is the entire ``DashboardLayout`` JSON,
    verbatim — the truth; every other column here is denormalised from it purely so the
    dashboard switcher can list dashboards without parsing every document.

    The primary key is **composite** — ``(user_id, layout_id)`` — not a global PK on the
    client-chosen ``layout_id`` alone: a global unique constraint would turn an insert
    conflict into an existence oracle for another user's layout id, and two users forking the
    same preset naturally produce the same id. Widgets are deliberately not normalised into
    their own rows — a second schema for a dashboard's contents is a second thing that can
    drift from ``DashboardLayout.swift``.
    """

    __tablename__ = "user_dashboards"
    __table_args__ = (Index("ix_dashboards_user_position", "user_id", "position"),)

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    )
    layout_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_json: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    icon: Mapped[str] = mapped_column(String(64), nullable=False)
    accent: Mapped[str] = mapped_column(String(16), nullable=False)
    presentation: Mapped[str] = mapped_column(String(16), nullable=False)
    preset_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tagline: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_preset: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    widget_count: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WebInvite(AccountBase):
    """An invite code minted by ``python3 -m nbastats.accounts.admin invite`` for
    ``HARDWOOD_SIGNUP_MODE=invite`` (the default). ``code`` is a ``secrets.token_urlsafe(16)``
    value and is the primary key — a code is looked up, not enumerated.
    """

    __tablename__ = "web_invites"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    used_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str | None] = mapped_column(String(120), nullable=True)


#: Every table name on :data:`AccountBase.metadata` — used by tests to assert there is no
#: overlap with :data:`nbastats.models.Base.metadata` and that ``init_db()`` created all six.
ACCOUNT_TABLE_NAMES = frozenset(table.name for table in AccountBase.metadata.sorted_tables)


#: Every table whose rows belong to one user, child-first, as :func:`delete_user` sweeps them.
#:
#: This tuple — not the ``ON DELETE CASCADE`` in the DDL — is what actually erases an
#: account. SQLite only honours a foreign key when ``PRAGMA foreign_keys=ON``, and Hardwood
#: deliberately leaves that off: it is a per-connection switch, so turning it on for these six
#: tables would also turn it on for the fourteen stats tables, whose constraints have never been
#: enforced and whose test fixtures do not satisfy them (see ``nbastats/db.py``). The DDL keeps
#: the clause because it documents the relationship and because a Postgres deployment enforces it
#: for free; the sweep below is what makes erasure true on the store this actually runs on.
USER_OWNED_TABLES: tuple[type[AccountBase], ...] = (
    UserIdentity,
    AuthSession,
    AuthToken,
    UserDashboard,
)


def delete_user(session: Session, user_id: str) -> int:
    """Erase a user and everything belonging to them; return the number of rows deleted.

    Children go first, so the account is never left half-erased if the transaction fails
    partway: a surviving ``User`` row with no sessions is recoverable, an orphaned session
    pointing at a deleted user is a credential nobody can revoke through the UI.

    ``oauth_transactions`` is not swept. Its ``link_user_id`` is a plain column rather than a
    foreign key precisely because the row is written *before* anyone is authenticated; those
    rows are single-use and expire in minutes, and the purge job clears them.

    This is the one supported way to delete an account — the ``DELETE /v1/me`` erasure path
    calls it. Deleting a ``User`` row directly leaves its children behind.
    """
    removed = 0
    for model in USER_OWNED_TABLES:
        result = session.execute(delete(model).where(model.user_id == user_id))
        removed += result.rowcount or 0
    result = session.execute(delete(User).where(User.user_id == user_id))
    removed += result.rowcount or 0
    return removed


def render_schema_sql() -> str:
    """Emit the account schema as SQLite DDL — the source of ``nbastats/accounts/schema.sql``.

    Mirrors :func:`nbastats.models.render_schema_sql` exactly, against ``AccountBase.metadata``
    instead of ``Base.metadata``, so the two generated files stay in the same shape.
    """
    from sqlalchemy.dialects import sqlite
    from sqlalchemy.schema import CreateIndex, CreateTable

    dialect = sqlite.dialect()
    lines: list[Any] = [
        "-- Hardwood accounts schema, generated by nbastats.accounts.models.render_schema_sql().",
        "-- Do not edit by hand: run",
        "-- `python3 -m nbastats.accounts.models > nbastats/accounts/schema.sql`.",
        "-- Dialect: SQLite. These tables live on their own MetaData (AccountBase), separate",
        "-- from nbastats/schema.sql -- see nbastats/accounts/models.py for why.",
        "",
    ]
    for table in AccountBase.metadata.sorted_tables:
        lines.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
        lines.append("")
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            lines.append(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")
        if table.indexes:
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":  # pragma: no cover - developer utility
    print(render_schema_sql(), end="")
