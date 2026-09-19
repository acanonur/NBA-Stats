"""``python3 -m nbastats.accounts.admin <command>`` — the operator's door into the accounts
system when there is no other admin surface (no mail server on a fresh box, no support team,
just whoever is running the process on their own laptop or a small server).

Every command opens its own short session via :func:`nbastats.db.get_sessionmaker`, does one
piece of work, commits, and exits — there is no long-running admin server, and every command is
safe to script (``cron``, a systemd timer for ``purge``) because each is idempotent or
plainly reports what it did.

Commands
----------
``create-user <email> [--password P] [--admin-note NOTE]``
    Create a password account directly, bypassing signup mode and invite codes entirely — the
    only way to get a first account onto a ``HARDWOOD_SIGNUP_MODE=closed`` deployment. Prints
    the generated password when ``--password`` is omitted. Leaves ``email_verified_at`` unset
    (see ``accounts/linking.py``'s module docstring on why exactly two code paths are allowed
    to write it, and this is not one of them) — the operator can run ``reset-password`` for a
    login link, or the user can verify normally.
``list-users``
    One line per account: id, email (or ``<private>``), verified, disabled, provider identities.
``reset-password <email>``
    Mint a single-use, one-hour password-reset token and print the full link — the same link
    ``POST /v1/auth/password/forgot`` would email, useful when there is no mail server
    configured at all.
``invite [--note NOTE] [--days N]``
    Mint an invite code for ``HARDWOOD_SIGNUP_MODE=invite`` (the default).
``export-user <email>``
    Print the account row and its dashboards as JSON — the same shape as the self-service
    ``GET /v1/me/export`` (routes_me.py, WP2), reachable here without a browser.
``delete-user <email>``
    Hard-delete a user and everything :func:`nbastats.accounts.models.delete_user` sweeps.
    Confirmation is the command line itself — there is no "are you sure" prompt, so scripting
    it is not accidentally sabotaged by an unexpected stdin read.
``purge [--older-than-days N]``
    Hard-deletes users and dashboards that were soft-deleted more than ``N`` days ago
    (default 30), and sweeps expired sessions (:func:`nbastats.accounts.sessions.sweep`).
``check-apple``
    A dry-run Apple client-secret mint plus a token-endpoint probe with an intentionally
    invalid ``code``, printing Apple's own error body verbatim. Apple's ``invalid_client``
    response is otherwise a black box; this is the fastest way anyone has found to tell "wrong
    key id" from "wrong team id" from "services id not enabled for Sign in with Apple" apart.
``doctor``
    Diffs each account table's live columns (via ``sqlalchemy.inspect``) against the model
    definitions and prints any drift — the accounts-schema analogue of a migration linter, for
    a project that deliberately has no migration tool (``WEB_DESIGN.md`` §3.1).
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import timedelta
from typing import Sequence
from uuid import uuid4

import httpx
from sqlalchemy import inspect, select

from ..db import get_sessionmaker, init_db, utcnow
from . import passwords, retention, tokens
from .config import get_auth_settings, load_env_file
from .models import (
    AccountBase,
    User,
    UserDashboard,
    UserIdentity,
    WebInvite,
    delete_user,
)
from .providers import apple as apple_provider

__all__ = ["main"]


def _find_user(db, email: str) -> User | None:
    normalized = email.strip().lower()
    return db.execute(
        select(User).where(User.email_lookup == normalized, User.deleted_at.is_(None))
    ).scalar_one_or_none()


def cmd_create_user(args: argparse.Namespace) -> int:
    factory = get_sessionmaker()
    with factory() as db:
        if _find_user(db, args.email) is not None:
            print(f"error: an account already exists for {args.email}", file=sys.stderr)
            return 1
        password = args.password or secrets.token_urlsafe(12)
        now = utcnow()
        user = User(
            user_id=uuid4().hex,
            email=args.email,
            email_lookup=args.email.strip().lower(),
            password_hash=passwords.hash_password(password),
            password_changed_at=now,
            display_name=args.display_name,
            theme_preference="system",
            profile_json="{}",
            created_at=now,
            updated_at=now,
        )
        db.add(user)
        db.commit()
        print(f"created {user.user_id} <{user.email}>")
        if not args.password:
            print(f"password: {password}")
        return 0


def cmd_list_users(_args: argparse.Namespace) -> int:
    factory = get_sessionmaker()
    with factory() as db:
        users = db.execute(select(User).order_by(User.created_at)).scalars().all()
        for user in users:
            identities = db.execute(
                select(UserIdentity.provider).where(UserIdentity.user_id == user.user_id)
            ).scalars().all()
            label = user.email or ("<private relay>" if user.is_private_relay else "<no email>")
            flags = []
            if user.email_verified_at is not None:
                flags.append("verified")
            if user.is_disabled:
                flags.append("disabled")
            if user.deleted_at is not None:
                flags.append("deleted")
            print(
                f"{user.user_id}  {label:<40}  "
                f"{'+'.join(flags) or '-':<20}  {','.join(identities) or '-'}"
            )
        return 0


def cmd_reset_password(args: argparse.Namespace) -> int:
    factory = get_sessionmaker()
    with factory() as db:
        user = _find_user(db, args.email)
        if user is None:
            print(f"error: no account for {args.email}", file=sys.stderr)
            return 1
        raw = tokens.mint(db, user.user_id, "reset")
        db.commit()
        base = get_auth_settings().public_base_url.rstrip("/")
        # Fragment, not query string — see routes_auth._link_for for why.
        print(f"{base}/reset#token={raw}")
        return 0


def cmd_invite(args: argparse.Namespace) -> int:
    factory = get_sessionmaker()
    with factory() as db:
        now = utcnow()
        invite = WebInvite(
            code=secrets.token_urlsafe(16),
            created_by=None,
            note=args.note,
            expires_at=now + timedelta(days=args.days) if args.days else None,
        )
        db.add(invite)
        db.commit()
        print(invite.code)
        return 0


def cmd_export_user(args: argparse.Namespace) -> int:
    factory = get_sessionmaker()
    with factory() as db:
        user = _find_user(db, args.email)
        if user is None:
            print(f"error: no account for {args.email}", file=sys.stderr)
            return 1
        dashboards = db.execute(
            select(UserDashboard).where(
                UserDashboard.user_id == user.user_id, UserDashboard.deleted_at.is_(None)
            )
        ).scalars().all()
        payload = {
            "userId": user.user_id,
            "email": user.email,
            "emailVerified": user.email_verified_at is not None,
            "displayName": user.display_name,
            "createdAt": user.created_at.isoformat() + "Z",
            "dashboards": [json.loads(d.document_json) for d in dashboards],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0


def cmd_delete_user(args: argparse.Namespace) -> int:
    factory = get_sessionmaker()
    with factory() as db:
        user = _find_user(db, args.email)
        if user is None:
            print(f"error: no account for {args.email}", file=sys.stderr)
            return 1
        removed = delete_user(db, user.user_id)
        db.commit()
        print(f"deleted {user.user_id}: {removed} related rows removed")
        return 0


def cmd_purge(args: argparse.Namespace) -> int:
    """Hard-delete what the retention window has run out on.

    The body lives in :mod:`nbastats.accounts.retention` because ``api/app.py``'s lifespan runs
    exactly the same job daily — the Privacy page promises erasure after 30 days, and an
    operator command nobody was told to schedule did not deliver that."""
    factory = get_sessionmaker()
    with factory() as db:
        summary = retention.purge(db, older_than_days=args.older_than_days)
        db.commit()
        print(
            f"purged {summary.users} users, {summary.dashboards} dashboards, "
            f"{summary.sessions} expired sessions, oauth transactions and tokens"
        )
        return 0


def cmd_check_apple(_args: argparse.Namespace) -> int:
    settings = get_auth_settings()
    missing = [
        name
        for name, value in (
            ("HARDWOOD_APPLE_TEAM_ID", settings.apple_team_id),
            ("HARDWOOD_APPLE_KEY_ID", settings.apple_key_id),
            ("HARDWOOD_APPLE_SERVICES_ID", settings.apple_services_id),
            ("HARDWOOD_APPLE_KEY_FILE", settings.apple_key_file),
        )
        if not value
    ]
    if missing:
        print(f"error: not configured — missing {', '.join(missing)}", file=sys.stderr)
        return 1
    try:
        secret = apple_provider.client_secret(settings)
    except OSError as exc:
        print(f"error: could not read HARDWOOD_APPLE_KEY_FILE: {exc}", file=sys.stderr)
        return 1
    print("minted a client secret successfully; probing the token endpoint with a bad code…")
    try:
        response = httpx.post(
            apple_provider.TOKEN,
            data={
                "client_id": settings.apple_services_id,
                "client_secret": secret,
                "code": "not-a-real-code",
                "grant_type": "authorization_code",
                "redirect_uri": f"{settings.public_base_url.rstrip('/')}/v1/auth/apple/callback",
            },
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        print(f"error: could not reach Apple: {exc}", file=sys.stderr)
        return 1
    print(f"HTTP {response.status_code}")
    print(response.text)
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    from ..db import get_engine

    engine = get_engine()
    inspector = inspect(engine)
    live_tables = set(inspector.get_table_names())
    drift = False
    for table in AccountBase.metadata.sorted_tables:
        if table.name not in live_tables:
            print(f"MISSING TABLE: {table.name}")
            drift = True
            continue
        live_columns = {c["name"] for c in inspector.get_columns(table.name)}
        model_columns = {c.name for c in table.columns}
        for missing in sorted(model_columns - live_columns):
            print(f"{table.name}: column {missing!r} is in the model but not in the database")
            drift = True
        for extra in sorted(live_columns - model_columns):
            print(f"{table.name}: column {extra!r} is in the database but not in the model")
            drift = True
    if not drift:
        print("no drift: every account table matches its model")
    return 1 if drift else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m nbastats.accounts.admin")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-user")
    p.add_argument("email")
    p.add_argument("--password")
    p.add_argument("--display-name")
    p.set_defaults(func=cmd_create_user)

    p = sub.add_parser("list-users")
    p.set_defaults(func=cmd_list_users)

    p = sub.add_parser("reset-password")
    p.add_argument("email")
    p.set_defaults(func=cmd_reset_password)

    p = sub.add_parser("invite")
    p.add_argument("--note")
    p.add_argument("--days", type=int, default=None)
    p.set_defaults(func=cmd_invite)

    p = sub.add_parser("export-user")
    p.add_argument("email")
    p.set_defaults(func=cmd_export_user)

    p = sub.add_parser("delete-user")
    p.add_argument("email")
    p.set_defaults(func=cmd_delete_user)

    p = sub.add_parser("purge")
    p.add_argument("--older-than-days", type=int, default=30)
    p.set_defaults(func=cmd_purge)

    p = sub.add_parser("check-apple")
    p.set_defaults(func=cmd_check_apple)

    p = sub.add_parser("doctor")
    p.set_defaults(func=cmd_doctor)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    # `web.sh setup` prints `... admin invite --note "me"` as the very next thing to run, and
    # on a fresh checkout there is no database yet: every subcommand died with sixty lines of
    # `sqlalchemy.exc.OperationalError: no such table: web_invites` until the server had been
    # started once. `init_db` is `create_all` — idempotent, and the same call `api/app.py`'s
    # lifespan makes — so making it unconditional here costs nothing and removes a hidden
    # ordering requirement from the first command a new operator is told to type.
    load_env_file()
    init_db()
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover - developer utility
    raise SystemExit(main())
