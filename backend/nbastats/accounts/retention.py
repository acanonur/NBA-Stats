"""The 30-day erasure the product promises, as code that actually runs.

``DELETE /v1/me`` is a *soft* delete: it stamps ``users.deleted_at``, disables the account and
revokes every session, so a person who deletes by accident can be restored by the operator.
``/legal/privacy`` and the Settings screen both then say the account "is erased permanently
after 30 days" — and until this module existed nothing in the shipped product ever made that
true. The only purge was ``python3 -m nbastats.accounts.admin purge``, an operator command no
document told anybody to schedule, so a default deployment kept the email address, the scrypt
hash, the OAuth subject id and the saved dashboards forever.

:func:`purge` is that command's body, lifted out so two callers share one implementation:

* ``admin.py purge`` — unchanged, still there for an operator who wants it on a cron.
* ``api/app.py``'s lifespan — once at startup and once a day thereafter, so the promise holds
  on a deployment nobody schedules anything on.

It is idempotent and bounded: it only ever touches rows whose ``deleted_at`` is already older
than the cutoff, plus the expired-session/token sweep that has always been safe to repeat.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from .models import User, UserDashboard, delete_user

__all__ = ["PurgeSummary", "RETENTION_DAYS", "purge"]

#: The number the Privacy page and the Settings screen both name. One constant, so the copy
#: and the job cannot drift.
RETENTION_DAYS = 30


@dataclass(frozen=True, slots=True)
class PurgeSummary:
    users: int
    dashboards: int
    sessions: int

    @property
    def is_empty(self) -> bool:
        return self.users == 0 and self.dashboards == 0 and self.sessions == 0


def purge(db: Session, *, older_than_days: int = RETENTION_DAYS) -> PurgeSummary:
    """Hard-delete everything soft-deleted longer than ``older_than_days`` ago, and sweep
    expired sessions, OAuth transactions and tokens. Does not commit — the caller does."""
    cutoff = utcnow() - timedelta(days=older_than_days)

    stale_users = (
        db.execute(select(User).where(User.deleted_at.isnot(None), User.deleted_at < cutoff))
        .scalars()
        .all()
    )
    for user in stale_users:
        delete_user(db, user.user_id)

    stale_dashboards = (
        db.execute(
            select(UserDashboard).where(
                UserDashboard.deleted_at.isnot(None), UserDashboard.deleted_at < cutoff
            )
        )
        .scalars()
        .all()
    )
    for dashboard in stale_dashboards:
        db.delete(dashboard)

    from .sessions import sweep

    swept = sweep(db)
    return PurgeSummary(users=len(stale_users), dashboards=len(stale_dashboards), sessions=swept)
