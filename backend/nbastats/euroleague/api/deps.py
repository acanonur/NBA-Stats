"""Request plumbing for ``/v1/el``: the EuroLeague session, the clock, and who may write.

The EuroLeague router never uses ``nbastats.api.deps.get_db``. That dependency yields a session on
the NBA's store, and a EuroLeague request handed one would read NBA tables under EuroLeague
names. Everything here yields a session on the EuroLeague's own store
(:func:`nbastats.euroleague.db.get_el_sessionmaker`), and the NBA guards that *are* shared (the API
key and session checks, the rate limiter) are applied one level up, when ``api/app.py`` includes the
router, so this module only adds what is specific to the EuroLeague.

Three dependencies, three jobs
------------------------------
:func:`require_ready`
    The state gate. The store's state (``ready``, ``disabled``, ``misconfigured``,
    ``notConfigured`` or ``error``) was decided once, at start-up, by
    :func:`nbastats.euroleague.bootstrap.prepare`. Anything but ``ready`` answers
    ``503 league_unavailable`` with the reason, *before a session is opened*: a misconfigured store
    (one file shared with the NBA) must not even be touched. ``/meta`` and ``/health`` are the two
    routes that do not use it, because the reason is exactly what they exist to tell.
:func:`get_el_session`
    A request-scoped session, closed when the response is done. Nothing in the read path commits;
    the two writes commit explicitly through :func:`commit_after`.
:func:`get_now`
    The clock, as a dependency, so a test or a fixture can hold it still with
    ``app.dependency_overrides[get_now]``. The read side never calls ``datetime.now`` itself.

Who may write
-------------
Three routes write: a status (``POST /availability``, ``DELETE /availability/{statusId}``), a
pasted link (``POST /news/links``) and a model setting (``PATCH /model-settings``). Each needs
**either** the API key on a request with no ``Origin`` (the native Mac app) **or** a signed-in
browser session with its CSRF token and a same-origin ``Origin`` (:func:`require_el_write`, which
is :func:`nbastats.api.deps.require_key_or_session_write`). With neither configured nor presented
the answer is ``401``: a write endpoint with no credential is the one thing this service does not
offer, and ``X-Hardwood-Client`` is not a credential. The reads follow the same rule as the NBA's,
through the guards the app applies to every router.

The errors the design adds (``league_unavailable``, ``club_not_found``, ``invalid_status``) are
built in :mod:`nbastats.euroleague.read.queries`, because ``api/errors.py`` is not this package's
to edit; they use the same :class:`~nbastats.api.errors.ApiError` envelope.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from ...api import errors
from ...api.deps import require_key_or_session_write
from ..bootstrap import BootstrapResult, get_state
from ..db import get_el_sessionmaker
from ..read.queries import league_unavailable, utc_now

__all__ = [
    "get_now",
    "require_ready",
    "get_el_session",
    "get_maybe_session",
    "require_el_write",
    "entered_by",
    "commit_after",
    "NowDep",
    "ElSession",
    "MaybeSession",
]


def get_now() -> datetime:
    """The clock (aware UTC). Override it in a test to hold time still."""
    return utc_now()


def require_ready() -> BootstrapResult:
    """``503 league_unavailable`` unless the EuroLeague started cleanly and holds a source."""
    state = get_state()
    if not state.ready:
        reason = state.reason or "The EuroLeague is not available on this server."
        raise league_unavailable(reason)
    return state


def get_el_session(_ready: Annotated[BootstrapResult, Depends(require_ready)]) -> Iterator[Session]:
    """A session on the EuroLeague store for one request."""
    session = get_el_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


def get_maybe_session() -> Iterator[Session | None]:
    """A session when the store is ready, else ``None``. For ``/meta`` and ``/health``, which
    answer in every state because the state is their message."""
    if not get_state().ready:
        yield None
        return
    session = get_el_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


NowDep = Annotated[datetime, Depends(get_now)]
ElSession = Annotated[Session, Depends(get_el_session)]
MaybeSession = Annotated[Session | None, Depends(get_maybe_session)]


def require_el_write(request: Request) -> None:
    """The native app's API key, or a browser session with its CSRF token; otherwise ``401``.

    The same shared gate the NBA's writes use
    (:func:`nbastats.api.deps.require_key_or_session_write`): the key is compared in constant
    time, only when the service is configured with one, and only on a request that carries no
    ``Origin``; a keyless server refuses every write that has no session.
    """
    require_key_or_session_write(request)


def entered_by(request: Request) -> int | None:
    """The signed-in user's id for a write, or ``None`` when the API key authorised it."""
    user = getattr(request.state, "user", None)
    return getattr(user, "user_id", None) if user is not None else None


def commit_after(session: Session) -> None:
    """Commit a write, turning a database failure into a clean 500 rather than a half-write."""
    try:
        session.commit()
    except Exception as exc:  # noqa: BLE001 - the envelope's own 500 says what to do
        session.rollback()
        raise errors.internal_error("The change could not be saved.") from exc
