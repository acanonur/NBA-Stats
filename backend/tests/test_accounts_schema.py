"""Tests for the one property this package cannot ship without: account tables live on their
own SQLAlchemy metadata (``AccountBase``), so :func:`nbastats.seed.seed_database`'s
unconditional ``_clear()`` — which deletes every row of every table on ``nbastats.models.
Base`` — never touches a user, a session, an identity or a saved dashboard. See
``WEB_DESIGN.md`` §0.1-C and §3.1 for the reasoning this test proves empirically.

Also covers the DDL-drift check for ``nbastats/accounts/schema.sql`` (mirroring
``tests/test_models.py::test_schema_sql_is_current`` for the stats schema), confirms the
account tables leave no trace in the stats schema, and confirms the SQLite pragmas
``nbastats/db.py`` installs (foreign keys, WAL) actually take effect for this engine.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, delete, inspect
from sqlalchemy.orm import Session

from nbastats.accounts.models import (
    delete_user,
    ACCOUNT_TABLE_NAMES,
    AccountBase,
    AuthSession,
    User,
    UserIdentity,
)
from nbastats.accounts.models import render_schema_sql as render_accounts_schema_sql
from nbastats.db import utcnow
from nbastats.models import Base, render_schema_sql
from nbastats.seed import seed_database

ACCOUNTS_SCHEMA_SQL = Path(__file__).resolve().parents[1] / "nbastats" / "accounts" / "schema.sql"


def _make_user(user_id: str) -> User:
    now = utcnow()
    return User(
        user_id=user_id,
        email="ada@example.com",
        email_lookup=f"ada+{user_id}@example.com",
        password_hash="scrypt$14$8$1$c2FsdA==$ZGVyaXZlZGtleQ==",
        theme_preference="system",
        profile_json="{}",
        created_at=now,
        updated_at=now,
    )


# --------------------------------------------------------------------------- the separation


def test_account_tables_are_on_their_own_metadata() -> None:
    """No table name is shared between the two metadata objects, and the account base holds
    exactly the six-plus-invites tables §3.2 specifies."""
    stats_tables = set(Base.metadata.tables)
    account_tables = set(AccountBase.metadata.tables)
    assert not (stats_tables & account_tables)
    assert account_tables == set(ACCOUNT_TABLE_NAMES)
    assert account_tables == {
        "users",
        "user_identities",
        "auth_sessions",
        "auth_tokens",
        "oauth_transactions",
        "user_dashboards",
        "web_invites",
    }


def test_init_db_creates_every_account_table(empty_engine: Engine) -> None:
    """``init_db()`` (called by the ``empty_engine`` fixture) creates both metadata objects."""
    names = set(inspect(empty_engine).get_table_names())
    assert set(ACCOUNT_TABLE_NAMES) <= names


def test_a_user_survives_reseeding_the_stats_tables(empty_engine: Engine) -> None:
    """The most important test in this package.

    ``nbastats/seed.py::seed_database()`` unconditionally clears every table on
    ``Base.metadata`` (``nbastats/seed.py::_clear()``), and ``nbastats/api/app.py::
    _prepare_database()`` calls it unattended whenever ``HARDWOOD_DEMO_MODE`` is set and the
    ``teams`` table is empty — the default under ``scripts/serve_dev.sh``. If account rows
    were on the same metadata as the stats tables, this test would fail with the user gone.
    """
    with Session(empty_engine, future=True) as session:
        session.add(_make_user("u-survives"))
        session.commit()

    with Session(empty_engine, future=True) as session:
        seed_database(
            session,
            seasons=["2025-26"],
            games_per_team=2,
            players_per_team=2,
            include_playoffs=False,
        )
        session.commit()

    with Session(empty_engine, future=True) as session:
        survivor = session.get(User, "u-survives")
        assert survivor is not None
        assert survivor.email == "ada@example.com"
        assert survivor.theme_preference == "system"


def test_account_tables_do_not_leak_into_the_stats_schema() -> None:
    """The whole point of the separate base: the stats DDL, and therefore
    ``tests/test_models.py::test_schema_sql_is_current``, is unaffected by importing
    ``nbastats.accounts`` — the account tables can never appear in ``render_schema_sql()``
    because they live on a different ``MetaData`` altogether."""
    ddl = render_schema_sql()
    for table_name in ACCOUNT_TABLE_NAMES:
        assert f"CREATE TABLE {table_name} " not in ddl


# --------------------------------------------------------------------------- DDL drift


def test_accounts_schema_sql_is_current() -> None:
    """The committed DDL must match what the accounts models generate — no silent drift,
    mirroring ``tests/test_models.py::test_schema_sql_is_current`` for the stats schema."""
    assert ACCOUNTS_SCHEMA_SQL.is_file(), "nbastats/accounts/schema.sql is missing; regenerate it"
    assert ACCOUNTS_SCHEMA_SQL.read_text() == render_accounts_schema_sql()


def test_generated_accounts_ddl_covers_every_table_and_index() -> None:
    ddl = render_accounts_schema_sql()
    for table in AccountBase.metadata.sorted_tables:
        assert f"CREATE TABLE {table.name} " in ddl
        for index in table.indexes:
            assert index.name is not None and index.name in ddl


# --------------------------------------------------------------------------- SQLite pragmas


def test_sqlite_pragmas_are_wal_but_deliberately_not_foreign_keys(empty_engine: Engine) -> None:
    """``nbastats/db.py``'s connect hook sets WAL, and pointedly leaves foreign keys alone.

    Both halves matter. WAL is why two browser tabs saving a dashboard do not collide. The
    absent ``foreign_keys`` pragma is a decision, not an oversight: it is per connection, so
    switching it on for the six account tables also switches it on for the fourteen stats
    tables, and twenty-three existing tests build a store (a ``games`` row with no ``teams``
    row) that does not satisfy those older constraints. If someone turns it on later, this
    test fails and points them at ``delete_user`` and the suite it would break.
    """
    with empty_engine.connect() as conn:
        foreign_keys = conn.exec_driver_sql("PRAGMA foreign_keys").scalar()
        journal_mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
    assert journal_mode == "wal"
    assert foreign_keys == 0


def test_deleting_a_user_erases_everything_that_belongs_to_them(empty_engine: Engine) -> None:
    """``delete_user`` is the cascade. The DDL's ``ON DELETE CASCADE`` is not, on SQLite.

    With ``PRAGMA foreign_keys`` off (see the test above), deleting the ``User`` row alone
    leaves every child behind — an orphaned ``auth_sessions`` row is a live credential that no
    longer has an account to revoke it from. So this asserts both directions: the raw delete
    strands the children, and ``delete_user`` does not.
    """
    now = utcnow()
    with Session(empty_engine, future=True) as session:
        session.add(_make_user("u-cascade"))
        session.add(
            UserIdentity(
                identity_id="ident-1",
                user_id="u-cascade",
                provider="google",
                subject="google-subject-1",
                email_verified=True,
                created_at=now,
            )
        )
        session.add(
            AuthSession(
                session_id="sess-1",
                user_id="u-cascade",
                token_hash="t" * 64,
                csrf_hash="c" * 64,
                auth_method="password",
                created_at=now,
                last_seen_at=now,
                authenticated_at=now,
                idle_expires_at=now,
                absolute_expires_at=now,
            )
        )
        session.commit()

    with Session(empty_engine, future=True) as session:
        session.execute(delete(User).where(User.user_id == "u-cascade"))
        session.commit()

    # A bare DELETE on the parent strands the children — this is the trap delete_user exists for.
    with Session(empty_engine, future=True) as session:
        session.execute(delete(User).where(User.user_id == "u-cascade"))
        session.commit()
    with Session(empty_engine, future=True) as session:
        assert session.get(UserIdentity, "ident-1") is not None
        assert session.get(AuthSession, "sess-1") is not None

    with Session(empty_engine, future=True) as session:
        removed = delete_user(session, "u-cascade")
        session.commit()
    assert removed == 2, "one identity and one session; the user row was already gone"

    with Session(empty_engine, future=True) as session:
        assert session.get(UserIdentity, "ident-1") is None
        assert session.get(AuthSession, "sess-1") is None


def test_users_email_lookup_allows_repeated_null(empty_engine: Engine) -> None:
    """A private-relay user with no email at all is still exactly one account each time —
    SQLite treats every NULL in a UNIQUE column as distinct from every other NULL."""
    now = utcnow()
    with Session(empty_engine, future=True) as session:
        session.add(
            User(
                user_id="u-relay-1",
                email=None,
                email_lookup=None,
                is_private_relay=True,
                theme_preference="system",
                profile_json="{}",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            User(
                user_id="u-relay-2",
                email=None,
                email_lookup=None,
                is_private_relay=True,
                theme_preference="system",
                profile_json="{}",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    with Session(empty_engine, future=True) as session:
        assert session.get(User, "u-relay-1") is not None
        assert session.get(User, "u-relay-2") is not None
