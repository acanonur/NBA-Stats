"""The projection ledger: what was projected, when, and what became of it.

A projection that can be quietly recomputed after the game is worth nothing as evidence, so the
model keeps a ledger (``el_projection_ledger``) with four kinds of row and one rule each:

``latest``
    The projection as the model stood, written whenever a game tipping within 48 hours gets
    *different inputs* (:func:`refresh_latest`). A row is written only when ``inputs_sha256``
    changes, so the history shows when the model's mind changed and never repeats itself.
``locked``
    A copy of the newest ``latest`` row, made in the hour before tip-off (:func:`lock_game`).
    **A locked row is never created at or after tip-off**: :func:`lock_game` refuses, and takes the
    row's own ``computed_at`` (not the time of locking) so the table can be audited on its face:
    ``computed_at < tip-off`` for every locked row. If no ``latest`` row was recorded before
    tip-off, nothing is locked and the review says "No projection was recorded before tip-off";
    there is no after-the-fact lock to fall back on.
``reconstructed``
    The projection rebuilt after the game from inputs dated strictly before tip-off, for a game
    that was played with no locked row (the Mac was asleep, or the game predates the import).
    ``inputs_cutoff`` is before tip-off by construction, ``computed_at`` is when it was rebuilt,
    and the review reports these separately from locked ones, because "the model was right" means
    something different when the model was asked afterwards.
``imported``
    The workbook's published projection for a round it had already played (scores only; the
    columns the workbook did not publish are NULL, which the schema permits for this kind alone).

The unknown tip-off
-------------------
A fixture imported without a tip-off cannot be told apart from a game about to start, so the lock
deadline is conservative: ``game_date`` at 12:00 in Europe/Berlin, earlier than any tip-off the
EuroLeague schedules. Past it a lock is refused, whatever the real tip-off turns out to be.

Calibration
-----------
The interval around a projection uses the standard deviations in the settings, which the
workbook assumed and nobody has checked (``intervalBasis: "assumed"``). Once 100 or more
``locked`` projections have a final result, :func:`calibrate` replaces them with the root mean
square of the residuals (provenance ``fittedLedger``): of each side's score for ``teamSd`` and of
the margin for ``marginSd``. Root mean square rather than standard deviation about the mean, so a
model that is biased pays for its bias in a wider interval instead of hiding it. Only ``locked``
rows are used, because a reconstructed projection is made with the answer known to exist. A value
the user has set by hand (provenance ``manual``) is never overwritten.

The worker's entry points
-------------------------
:func:`run_refresh` (every 30 minutes), :func:`run_lock` (every 5) and :func:`run_calibrate`
(daily) are what ``nbastats.worker`` calls, by string name. Each opens its own session on the
EuroLeague store, works out for itself what is due and writes nothing when nothing is. They take
the clock as ``now`` so a test, or a laptop waking from sleep, gets the answer for that moment.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import el_session_scope
from ..models import ElGame, ElIntelStatus, ElProjectionLedger
from ..read.queries import (
    BERLIN,
    ReadContext,
    aware,
    build_context,
    game_has_result,
    naive_utc,
)
from ..settings import set_setting
from .ratings import regulation_actual
from .round_projection import (
    MODEL_VERSION,
    Model,
    ProjectionResult,
    ProjectionUnavailable,
    get_model,
)

__all__ = [
    "REFRESH_HORIZON",
    "LOCK_WINDOW",
    "MIN_CALIBRATION_GAMES",
    "LockRefused",
    "lock_deadline",
    "write_row",
    "refresh_latest",
    "lock_game",
    "lock_due",
    "write_reconstructed_rows",
    "CalibrationResult",
    "calibrate",
    "run_refresh",
    "run_lock",
    "run_calibrate",
]

_LOG = logging.getLogger(__name__)

#: A game gets ``latest`` rows once it tips within this long.
REFRESH_HORIZON: Final = timedelta(hours=48)
#: The lock runs in ``[tip - 60 minutes, tip)``.
LOCK_WINDOW: Final = timedelta(minutes=60)
#: Locked games with a result needed before the spreads are fitted from them.
MIN_CALIBRATION_GAMES: Final = 100


class LockRefused(Exception):
    """A lock was asked for at or after tip-off. It is never created then."""


def lock_deadline(game: ElGame) -> datetime:
    """The instant a lock is refused from: tip-off, else noon in Berlin on the game's day."""
    if game.tipoff_utc is not None:
        return aware(game.tipoff_utc)  # type: ignore[return-value]
    return datetime.combine(game.game_date, time(12, 0), tzinfo=BERLIN).astimezone(timezone.utc)


# --------------------------------------------------------------------------- writing rows


def _availability_as_of(result: ProjectionResult) -> datetime | None:
    stamps = [
        line.resolved.row.as_of
        for side in result.sides
        for line in side.squad
        if line.resolved.in_force and isinstance(line.resolved.row, ElIntelStatus)
    ]
    return max(stamps) if stamps else None


def write_row(
    session: Session,
    result: ProjectionResult,
    *,
    kind: str,
    computed_at: datetime,
    inputs_cutoff: datetime,
) -> ElProjectionLedger:
    """Append one ledger row for ``result`` (not committed)."""
    match = result.match
    row = ElProjectionLedger(
        game_id=result.game_id,
        kind=kind,
        model_version=MODEL_VERSION,
        computed_at=naive_utc(computed_at),
        inputs_cutoff=naive_utc(inputs_cutoff),
        home_pts=match.home_points,
        away_pts=match.away_points,
        home_full_strength=match.home_full_strength,
        away_full_strength=match.away_full_strength,
        home_attack_index_after_availability=match.home_attack_after_availability,
        away_attack_index_after_availability=match.away_attack_after_availability,
        home_defence_index=result.home.defence,
        away_defence_index=result.away.defence,
        home_advantage_points=result.home_advantage.points,
        cap_policy=result.cap_policy,
        settings_sha256=result.settings_sha256,
        inputs_sha256=result.inputs_sha256,
        availability_as_of=_availability_as_of(result),
    )
    session.add(row)
    session.flush()
    return row


def _rows(session: Session, game_id: str, kind: str) -> list[ElProjectionLedger]:
    return list(
        session.execute(
            select(ElProjectionLedger)
            .where(ElProjectionLedger.game_id == game_id, ElProjectionLedger.kind == kind)
            .order_by(ElProjectionLedger.ledger_id)
        ).scalars()
    )


def _refresh_one(
    session: Session, ctx: ReadContext, model: Model, game: ElGame
) -> ElProjectionLedger | None:
    """Write a ``latest`` row for ``game`` if its inputs have changed since the last one."""
    try:
        result = model.project(ctx, game, as_of=ctx.now, kind="latest")
    except ProjectionUnavailable:
        return None
    newest = (_rows(session, game.game_id, "latest") or [None])[-1]
    if newest is not None and newest.inputs_sha256 == result.inputs_sha256:
        return None
    return write_row(session, result, kind="latest", computed_at=ctx.now, inputs_cutoff=ctx.now)


def _upcoming(ctx: ReadContext) -> list[ElGame]:
    """Games still to be played whose tip-off is known."""
    return [
        g
        for g in ctx.games
        if g.status == "scheduled" and g.tipoff_utc is not None and not game_has_result(g)
    ]


def refresh_latest(
    session: Session, ctx: ReadContext, model: Model, *, horizon: timedelta = REFRESH_HORIZON
) -> list[ElProjectionLedger]:
    """Write a ``latest`` row for every game tipping within ``horizon`` whose inputs changed."""
    written: list[ElProjectionLedger] = []
    for game in _upcoming(ctx):
        tip = aware(game.tipoff_utc)
        if tip <= ctx.now or tip - ctx.now > horizon:  # type: ignore[operator]
            continue
        row = _refresh_one(session, ctx, model, game)
        if row is not None:
            written.append(row)
    return written


# --------------------------------------------------------------------------- locking


def lock_game(
    session: Session, ctx: ReadContext, model: Model, game: ElGame
) -> ElProjectionLedger | None:
    """Freeze ``game``'s projection. Never at or after tip-off.

    Raises :class:`LockRefused` when ``ctx.now`` is at or past the deadline. Returns the existing
    locked row if there is one, ``None`` when no ``latest`` row was recorded before the deadline
    (the review will say so), or the new locked row. Refreshes the ``latest`` row first, so the
    projection locked is the freshest the model had before tip-off.
    """
    deadline = lock_deadline(game)
    if ctx.now >= deadline:
        raise LockRefused(f"{game.game_id}: tip-off has passed, so nothing can be locked")
    existing = _rows(session, game.game_id, "locked")
    if existing:
        return existing[-1]
    _refresh_one(session, ctx, model, game)
    cutoff = naive_utc(deadline)
    candidates = [r for r in _rows(session, game.game_id, "latest") if r.computed_at < cutoff]
    if not candidates:
        return None
    source = candidates[-1]
    locked = ElProjectionLedger(
        game_id=source.game_id,
        kind="locked",
        model_version=source.model_version,
        computed_at=source.computed_at,
        inputs_cutoff=source.inputs_cutoff,
        home_pts=source.home_pts,
        away_pts=source.away_pts,
        home_full_strength=source.home_full_strength,
        away_full_strength=source.away_full_strength,
        home_attack_index_after_availability=source.home_attack_index_after_availability,
        away_attack_index_after_availability=source.away_attack_index_after_availability,
        home_defence_index=source.home_defence_index,
        away_defence_index=source.away_defence_index,
        home_advantage_points=source.home_advantage_points,
        cap_policy=source.cap_policy,
        settings_sha256=source.settings_sha256,
        inputs_sha256=source.inputs_sha256,
        availability_as_of=source.availability_as_of,
    )
    session.add(locked)
    session.flush()
    return locked


def lock_due(
    session: Session, ctx: ReadContext, model: Model, *, window: timedelta = LOCK_WINDOW
) -> list[ElProjectionLedger]:
    """Lock every game in ``[tip - window, tip)`` that has no locked row yet."""
    locked: list[ElProjectionLedger] = []
    for game in _upcoming(ctx):
        deadline = lock_deadline(game)
        if not deadline - window <= ctx.now < deadline:
            continue
        if _rows(session, game.game_id, "locked"):
            continue
        row = lock_game(session, ctx, model, game)
        if row is not None:
            locked.append(row)
    return locked


# --------------------------------------------------------------------------- reconstruction


def write_reconstructed_rows(
    session: Session, ctx: ReadContext, model: Model, now: datetime
) -> int:
    """Record the projections the model had to rebuild (no locked row), once per set of inputs."""
    written = 0
    for game_id, result in model.reconstructed.items():
        known = {r.inputs_sha256 for r in _rows(session, game_id, "reconstructed")}
        if result.inputs_sha256 in known:
            continue
        game = ctx.games_by_id[game_id]
        if not result.as_of < aware(game.tipoff_utc or _day_start(game)):  # type: ignore[operator]
            continue  # the schema's rule: a reconstruction's inputs end before tip-off
        write_row(
            session, result, kind="reconstructed", computed_at=now, inputs_cutoff=result.as_of
        )
        written += 1
    return written


def _day_start(game: ElGame) -> datetime:
    return datetime.combine(game.game_date, time(0, 0), tzinfo=BERLIN).astimezone(timezone.utc)


# --------------------------------------------------------------------------- calibration


@dataclass(frozen=True)
class CalibrationResult:
    games: int
    fitted: bool
    team_sd: float | None
    margin_sd: float | None
    skipped: tuple[str, ...] = ()


def _rms(values: list[float]) -> float:
    return math.sqrt(math.fsum(v * v for v in values) / len(values))


def calibrate(session: Session, ctx: ReadContext, now: datetime) -> CalibrationResult:
    """Fit ``teamSd`` and ``marginSd`` from locked projections with results, once there are enough.

    A projection is a regulation score, so an overtime result is restated as regulation first
    (``overtimeScaling``, exactly as the rating update does): a 45-minute 95-93 is not a 13-point
    miss of an 82-80 projection.
    """
    finals = {g.game_id: g for g in ctx.games if game_has_result(g)}
    scaling = bool(ctx.setting("overtimeScaling"))
    team_residuals: list[float] = []
    margin_residuals: list[float] = []
    games = 0
    for game_id, game in finals.items():
        locked = _rows(session, game_id, "locked")
        if not locked:
            continue
        row = locked[-1]
        games += 1
        home = regulation_actual(game.home_pts, game.ot_periods, scaling)
        away = regulation_actual(game.away_pts, game.ot_periods, scaling)
        team_residuals.append(home - row.home_pts)
        team_residuals.append(away - row.away_pts)
        margin_residuals.append((home - away) - (row.home_pts - row.away_pts))
    if games < MIN_CALIBRATION_GAMES:
        return CalibrationResult(games, False, None, None)
    team_sd, margin_sd = _rms(team_residuals), _rms(margin_residuals)
    skipped: list[str] = []
    for key, value in (("teamSd", team_sd), ("marginSd", margin_sd)):
        if ctx.settings[key].provenance == "manual":
            skipped.append(key)  # the user's own number outranks a fitted one
            continue
        set_setting(session, key, value, "fittedLedger", naive_utc(now))
    return CalibrationResult(games, True, team_sd, margin_sd, tuple(skipped))


# --------------------------------------------------------------------------- worker jobs


def _open(session: Session, now: datetime | None) -> tuple[ReadContext, Model] | dict[str, Any]:
    moment = aware(now) if now is not None else datetime.now(timezone.utc)
    try:
        ctx = build_context(session, None, now=moment)
        return ctx, get_model(ctx)
    except ProjectionUnavailable as exc:
        return {"status": "skipped", "reason": exc.reason}
    except Exception as exc:  # noqa: BLE001 - no season yet, or a store mid-import
        return {"status": "skipped", "reason": str(exc)}


def run_refresh(now: datetime | None = None) -> dict[str, Any]:
    """Worker job ``projections.refresh`` (every 30 minutes) for the EuroLeague."""
    with el_session_scope() as session:
        opened = _open(session, now)
        if isinstance(opened, dict):
            return opened
        ctx, model = opened
        rows = refresh_latest(session, ctx, model)
        if rows:
            _LOG.info("projections.refresh: %s new latest rows", len(rows))
        return {"status": "ok" if rows else "skipped", "written": len(rows)}


def run_lock(now: datetime | None = None) -> dict[str, Any]:
    """Worker job ``projections.lock`` (every 5 minutes) for the EuroLeague."""
    with el_session_scope() as session:
        opened = _open(session, now)
        if isinstance(opened, dict):
            return opened
        ctx, model = opened
        rows = lock_due(session, ctx, model)
        if rows:
            _LOG.info("projections.lock: locked %s games", len(rows))
        return {"status": "ok" if rows else "skipped", "locked": len(rows)}


def run_calibrate(now: datetime | None = None) -> dict[str, Any]:
    """Worker job ``projections.calibrate`` (daily 05:00) for the EuroLeague."""
    with el_session_scope() as session:
        opened = _open(session, now)
        if isinstance(opened, dict):
            return opened
        ctx, _ = opened
        result = calibrate(session, ctx, ctx.now)
        return {
            "status": "ok" if result.fitted else "skipped",
            "games": result.games,
            "teamSd": result.team_sd,
            "marginSd": result.margin_sd,
            "reason": (
                None
                if result.fitted
                else f"{result.games} locked games; {MIN_CALIBRATION_GAMES} needed"
            ),
        }
