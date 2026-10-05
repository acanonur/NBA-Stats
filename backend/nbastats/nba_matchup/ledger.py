"""The projection ledger: what was projected, when, and what became of it.

A projection that can be quietly recomputed after the game is worth nothing as evidence, so the
model keeps a ledger (``team_projection_ledger``) with two kinds of row and one rule each:

``latest``
    The projection as the model stood, written whenever a game that tips soon gets *different
    inputs* (:func:`refresh_latest`). A row is written only when ``inputs_sha256`` changes, so the
    history shows when the model's mind changed and never repeats itself. When the inputs go
    *back* to a state the model has been in before (an entry made and then cleared) the table's
    one-row-per-inputs rule forbids a second copy, so the earlier row is brought forward to the
    present instead: it is a ``latest`` row, which is replaced as inputs change by definition.
``locked``
    A copy of the newest ``latest`` row, made in the hour before the lock deadline
    (:func:`lock_game`). **A locked row is never created at or after the deadline**: the function
    refuses, and it takes the row's own ``computed_at`` (not the time of locking), so the table can
    be audited on its face: ``computed_at < deadline`` for every locked row. If no ``latest`` row
    was recorded before the deadline, nothing is locked and the review says so; there is no
    after-the-fact lock to fall back on.

There is deliberately no third kind. A projection rebuilt after the game from inputs dated before
it (the EuroLeague stores those as ``reconstructed``) is computed on read here and never written:
the table that holds the evidence holds only what was said at the time.

The deadline, and why it is noon
--------------------------------
``games.game_date`` carries no hour and the scoreboard's tip-off is often absent, so the deadline
is the recorded tip-off, else **noon Eastern on the game day**, the earliest an NBA game tips. A
game with no recorded tip-off is therefore locked in the hour before noon and a lock is refused from
noon on, whatever the real tip-off turns out to be. That is conservative on purpose: the cost is a
projection that misses the last few hours of news, the benefit is that a locked row can never have
been computed after the game began.

The check that SQL cannot make
------------------------------
"A locked row is computed before tip-off" compares a ledger column with ``game_schedule_detail``,
a different table, which a CHECK cannot read. It is enforced here, in :func:`write_row` (a locked
row at or after the deadline raises) and again in :func:`lock_game`, and a test tries both.

Calibration
-----------
The interval around a projection uses ``teamSd`` and ``marginSd``, which have no default. Once 300
or more locked projections have a final result, :func:`ledger_residuals` gives the root mean square
of their residuals (of each side's score, and of the margin), and
:func:`~nbastats.nba_matchup.projection.calibrate` stores them with provenance ``fittedLedger``.
Only ``locked`` rows are used, because a rebuilt projection is made with the answer known to exist.

The worker's entry points
-------------------------
:func:`run_refresh` (every 30 minutes) and :func:`run_lock` (every five) are what
``nbastats.worker`` calls, by string name. Each opens its own session on the stats store, works out
for itself what is due and writes nothing when nothing is. They take the clock as ``now`` so a test,
or a laptop waking from sleep, gets the answer for that moment.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import Game, TeamGame, TeamProjectionLedger
from .projection import (
    MODEL_VERSION,
    Model,
    ProjectionResult,
    ProjectionUnavailable,
    can_project,
    get_model,
)
from .queries import (
    EASTERN,
    ReadContext,
    aware,
    build_context,
    game_has_result,
    naive_utc,
)

__all__ = [
    "REFRESH_HORIZON",
    "LOCK_WINDOW",
    "MIN_CALIBRATION_GAMES",
    "LockRefused",
    "write_row",
    "refresh_latest",
    "lock_game",
    "lock",
    "lock_due",
    "ledger_residuals",
    "run_refresh",
    "run_lock",
]

_LOG = logging.getLogger(__name__)

#: A game gets ``latest`` rows once its deadline is within this long.
REFRESH_HORIZON: Final = timedelta(hours=48)
#: The lock runs in ``[deadline - 60 minutes, deadline)``.
LOCK_WINDOW: Final = timedelta(minutes=60)
#: Locked games with a result needed before the spreads are fitted from the ledger.
MIN_CALIBRATION_GAMES: Final = 300


class LockRefused(Exception):
    """A lock was asked for at or after the deadline. It is never created then."""


# --------------------------------------------------------------------------- writing rows


def write_row(
    session: Session,
    ctx: ReadContext,
    game: Game,
    result: ProjectionResult,
    *,
    kind: str,
    computed_at: datetime,
    inputs_cutoff: datetime,
) -> TeamProjectionLedger:
    """Append one ledger row for ``result`` (not committed).

    A ``locked`` row computed at or after the game's deadline raises :class:`LockRefused`: this is
    the rule a CHECK constraint would carry if tip-off lived in the same table.
    """
    if kind == "locked" and aware(computed_at) >= ctx.deadline_of(game):  # type: ignore[operator]
        raise LockRefused(f"{game.game_id}: a locked projection must be computed before tip-off")
    match = result.match
    row = TeamProjectionLedger(
        game_id=game.game_id,
        kind=kind,
        model_version=MODEL_VERSION,
        computed_at=naive_utc(computed_at),
        inputs_cutoff=naive_utc(inputs_cutoff),
        home_pts=match.home_points,
        away_pts=match.away_points,
        home_full_strength=match.home_full_strength,
        away_full_strength=match.away_full_strength,
        home_attack_index=result.home.attack,
        away_attack_index=result.away.attack,
        home_defence_index=result.home.defence,
        away_defence_index=result.away.defence,
        home_availability_factor=result.home.availability_factor,
        away_availability_factor=result.away.availability_factor,
        home_advantage_points=result.home_advantage.points,
        team_sd=result.team_sd,
        margin_sd=result.margin_sd,
        availability_snapshot_id=result.snapshot_id,
        settings_sha256=result.settings_sha256,
        inputs_sha256=result.inputs_sha256,
    )
    session.add(row)
    session.flush()
    return row


def _rows(session: Session, game_id: str, kind: str) -> list[TeamProjectionLedger]:
    """A game's rows of one kind, oldest first by the time they were computed (the row id breaks
    a tie). Ordering by computed time and not by id matters once a ``latest`` row can be
    brought forward (:func:`_refresh_one`): the newest row is the one computed last."""
    return list(
        session.execute(
            select(TeamProjectionLedger)
            .where(TeamProjectionLedger.game_id == game_id, TeamProjectionLedger.kind == kind)
            .order_by(TeamProjectionLedger.computed_at, TeamProjectionLedger.ledger_id)
        ).scalars()
    )


def _refresh_one(
    session: Session, ctx: ReadContext, model: Model, game: Game
) -> TeamProjectionLedger | None:
    """Write a ``latest`` row for ``game`` if its inputs have changed since the last one."""
    try:
        result = model.project(ctx, game, as_of=ctx.now, kind="latest")
    except ProjectionUnavailable:
        return None
    rows = _rows(session, game.game_id, "latest")
    if rows and rows[-1].inputs_sha256 == result.inputs_sha256:
        return None
    earlier = next((r for r in rows if r.inputs_sha256 == result.inputs_sha256), None)
    if earlier is not None:
        # The inputs have gone back to a state the model was already in (a status entered, then
        # cleared). The table keeps one row per (game, kind, inputs), so the row is not written
        # twice: the earlier one is brought forward to now, which makes it the newest again, and
        # that is what a lock copies. The numbers are the same to the twelve digits the
        # fingerprint covers; only when it was computed and what it was computed from move.
        earlier.computed_at = naive_utc(ctx.now)
        earlier.inputs_cutoff = naive_utc(ctx.now)
        earlier.availability_snapshot_id = result.snapshot_id
        earlier.settings_sha256 = result.settings_sha256
        session.flush()
        return earlier
    return write_row(
        session, ctx, game, result, kind="latest", computed_at=ctx.now, inputs_cutoff=ctx.now
    )


def _upcoming(ctx: ReadContext) -> list[Game]:
    """Games still to be played that the model can project, soonest first."""
    return [
        g
        for g in ctx.games
        if g.status == "scheduled"
        and not game_has_result(g)
        and can_project(g.season, g.season_type) is None
        and g.home_team_id in ctx.teams
        and g.away_team_id in ctx.teams
    ]


def refresh_latest(
    session: Session, ctx: ReadContext, model: Model, *, horizon: timedelta = REFRESH_HORIZON
) -> list[TeamProjectionLedger]:
    """Write a ``latest`` row for every game whose deadline is within ``horizon`` and whose
    inputs changed."""
    written: list[TeamProjectionLedger] = []
    for game in _upcoming(ctx):
        deadline = ctx.deadline_of(game)
        if deadline <= ctx.now or deadline - ctx.now > horizon:
            continue
        row = _refresh_one(session, ctx, model, game)
        if row is not None:
            written.append(row)
    return written


# --------------------------------------------------------------------------- locking


def lock_game(
    session: Session, ctx: ReadContext, model: Model, game: Game
) -> TeamProjectionLedger | None:
    """Freeze ``game``'s projection. Never at or after the deadline.

    Raises :class:`LockRefused` when ``ctx.now`` is at or past the deadline. Returns the existing
    locked row if there is one, ``None`` when no ``latest`` row was recorded before the deadline
    (the review will say so), or the new locked row. Refreshes the ``latest`` row first, so the
    projection locked is the freshest the model had before the deadline.
    """
    deadline = ctx.deadline_of(game)
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
    locked = TeamProjectionLedger(
        game_id=source.game_id,
        kind="locked",
        model_version=source.model_version,
        computed_at=source.computed_at,
        inputs_cutoff=source.inputs_cutoff,
        home_pts=source.home_pts,
        away_pts=source.away_pts,
        home_full_strength=source.home_full_strength,
        away_full_strength=source.away_full_strength,
        home_attack_index=source.home_attack_index,
        away_attack_index=source.away_attack_index,
        home_defence_index=source.home_defence_index,
        away_defence_index=source.away_defence_index,
        home_availability_factor=source.home_availability_factor,
        away_availability_factor=source.away_availability_factor,
        home_advantage_points=source.home_advantage_points,
        team_sd=source.team_sd,
        margin_sd=source.margin_sd,
        availability_snapshot_id=source.availability_snapshot_id,
        settings_sha256=source.settings_sha256,
        inputs_sha256=source.inputs_sha256,
    )
    session.add(locked)
    session.flush()
    return locked


#: The design's name for it (``ledger.lock()``).
lock = lock_game


def lock_due(
    session: Session, ctx: ReadContext, model: Model, *, window: timedelta = LOCK_WINDOW
) -> list[TeamProjectionLedger]:
    """Lock every game in ``[deadline - window, deadline)`` that has no locked row yet."""
    locked: list[TeamProjectionLedger] = []
    for game in _upcoming(ctx):
        deadline = ctx.deadline_of(game)
        if not deadline - window <= ctx.now < deadline:
            continue
        if _rows(session, game.game_id, "locked"):
            continue
        row = lock_game(session, ctx, model, game)
        if row is not None:
            locked.append(row)
    return locked


# --------------------------------------------------------------------------- calibration


def _rms(values: list[float]) -> float:
    return math.sqrt(math.fsum(v * v for v in values) / len(values))


def ledger_residuals(session: Session, ctx: ReadContext) -> tuple[float, float, int] | None:
    """``(teamSd, marginSd, games)`` from locked projections with results, or ``None`` until there
    are :data:`MIN_CALIBRATION_GAMES` of them.

    The residual of a side is the regulation-scaled actual score less the locked projection, the
    scale the projection is made on; the margin's is the same for home less away.
    """
    rows = session.execute(
        select(TeamProjectionLedger, Game)
        .join(Game, Game.game_id == TeamProjectionLedger.game_id)
        .where(TeamProjectionLedger.kind == "locked", Game.status == "final")
        .order_by(TeamProjectionLedger.ledger_id)
    ).all()
    newest: dict[str, tuple[TeamProjectionLedger, Game]] = {}
    for ledger, game in rows:
        if game_has_result(game):
            newest[game.game_id] = (ledger, game)  # the newest locked row of a game wins
    if len(newest) < MIN_CALIBRATION_GAMES:
        return None
    locked_games = select(TeamProjectionLedger.game_id).where(TeamProjectionLedger.kind == "locked")
    minutes = {
        gid: m
        for gid, m in session.execute(
            select(TeamGame.game_id, TeamGame.minutes).where(
                TeamGame.is_home.is_(True), TeamGame.game_id.in_(locked_games)
            )
        )
    }
    team_residuals: list[float] = []
    margin_residuals: list[float] = []
    for game_id, (ledger, game) in newest.items():
        seconds = (minutes.get(game_id) or 0) * 60.0
        scale = 14_400 / seconds if seconds > 0 else 1.0
        home = game.home_pts * scale - ledger.home_pts
        away = game.away_pts * scale - ledger.away_pts
        team_residuals += [home, away]
        margin_residuals.append(home - away)
    return _rms(team_residuals), _rms(margin_residuals), len(newest)


# --------------------------------------------------------------------------- worker jobs


def _seasons_near(session: Session, now: datetime) -> list[str]:
    """Seasons with a game scheduled within a few days of ``now`` (the jobs only look there)."""
    today: date = aware(now).astimezone(EASTERN).date()  # type: ignore[union-attr]
    rows = session.execute(
        select(Game.season)
        .where(
            Game.status == "scheduled",
            Game.game_date >= today - timedelta(days=2),
            Game.game_date <= today + timedelta(days=4),
        )
        .distinct()
    ).scalars()
    return sorted({s for s in rows if s})


def _run(now: datetime | None, work: Any, verb: str) -> dict[str, Any]:
    moment = aware(now) if now is not None else datetime.now(timezone.utc)
    total = 0
    reasons: list[str] = []
    with session_scope() as session:
        for season in _seasons_near(session, moment):
            try:
                ctx = build_context(session, season, now=moment)
                model = get_model(ctx)
            except ProjectionUnavailable as exc:
                reasons.append(exc.reason)
                continue
            except Exception as exc:  # noqa: BLE001 - one bad season must not stop the others
                reasons.append(str(exc))
                continue
            total += len(work(session, ctx, model))
    if total:
        _LOG.info("projections.%s: %s rows", verb, total)
    return {
        "status": "ok" if total else "skipped",
        verb: total,
        "reason": None if total or not reasons else "; ".join(reasons[:3]),
    }


def run_refresh(now: datetime | None = None) -> dict[str, Any]:
    """Worker job ``projections.refresh`` (every 30 minutes) for the NBA."""
    return _run(now, refresh_latest, "written")


def run_lock(now: datetime | None = None) -> dict[str, Any]:
    """Worker job ``projections.lock`` (every 5 minutes) for the NBA."""
    return _run(now, lock_due, "locked")
