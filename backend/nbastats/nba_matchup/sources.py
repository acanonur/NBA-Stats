"""Sources and freshness: where every NBA number came from, and how current it is.

The EuroLeague workbook had a "Method / sources" sheet that said where each number came from. A
product that shows a projection owes the same thing live, because the honest answer to "is this
current?" is different for each source and changes while the app is open. This module computes it
once, for three uses:

* ``GET /v1/sources``: the full list, which a client renders as a "Sources and freshness" panel
  (:func:`build_source_list`);
* the ``freshness.sources`` block every payload carries, which holds only the sources that fed
  *that* payload (:func:`summaries`), so a payload about injuries does not carry the box-score
  ingest's health;
* the NBA's row in ``GET /v1/leagues`` (:func:`league_row`).

The keys (design section 6.5)
-----------------------------
``nba.stats``         the box scores, from the league's stats service: results, team and player
                      lines. Ingested by the live watcher; the demo league's seeder otherwise.
``nba.rosters``       the team rosters, where each player's listed position comes from. Defence by
                      position is empty without it.
``nba.injuryReport``  the league's official injury report, read from its PDF by the worker. It says
                      plainly when its parser has *not yet been confirmed against a real report*:
                      the parser was written against the documented layout and no real report was
                      reachable where it was written, so "it works" is not yet something this
                      installation has shown. The evidence is a counter kept in the database.
``nba.news.<id>``     one headline feed. Title, link, date and source name only.

States
------
``ok``, ``stale``, ``disabled``, ``notConfigured``, ``noReportYet``, ``blocked``, ``unreadable``,
``error``. ``stale`` means *something should have arrived and did not*: the stats ingest last
succeeded more than a day ago while a game is waiting for its result. A quiet fortnight in July is
not stale. ``blocked`` is a host refusing us (the circuit breaker the fetchers open on a 401, a 403
or repeated 429s) and says until when. ``unreadable`` is a source that answered with something its
parser does not recognise: it is reported as exactly that and nothing is guessed. There is no
``termsNotReviewed``: the terms gate was removed, and ``docs/LEGAL.md`` records the posture applied
to each source instead.

An unrecognised stored state is reported as ``error`` with the reason, never as ``ok``: a state
this module does not know must not look healthy.

The day-one notice
------------------
The design asks that what the user gets on day one be stated plainly where the sources are shown, so
``GET /v1/sources`` carries a ``dayOneNotice``: one short paragraph, written from the state of the
store and of each source (:func:`day_one_notice`), saying what is computed from the games, whether
an injury report has been read and what the model assumes until one has, and what a headline is.
It never promises what has not happened: with no report read it says no status is shown.

The demo league
---------------
When the store holds the seeded demo league, ``nba.stats`` is ``ok`` with a reason that says every
game is invented, ``nba.rosters`` explains that positions are the seeder's own labels, and the
injury report is ``disabled`` with the reason that real statuses are not shown next to invented
games. ``freshness.isDemo`` is true throughout, which a client shows as a banner.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Game, IngestLog, PlayerPositionSeason, SyncState
from ..nba_intel import store as intel_store
from ..nba_intel.models import NbaIntelNewsFeed, NbaIntelSourceState
from ..shared import refs
from ..shared.league_profile import NBA_KEY
from . import availability_view
from .availability_view import injuries_switch_on
from .queries import (
    PROFILE,
    ReadContext,
    latest_season,
    loaded_seasons,
    naive_utc,
)

__all__ = [
    "STATES",
    "KEYS_STATS",
    "KEYS_AVAILABILITY",
    "KEYS_MATCHUP",
    "FEATURES",
    "SourceInfo",
    "collect",
    "build_source_list",
    "summaries",
    "news_keys",
    "freshness_for",
    "day_one_notice",
    "league_row",
]

STATES: Final[tuple[str, ...]] = (
    "ok",
    "stale",
    "disabled",
    "notConfigured",
    "noReportYet",
    "blocked",
    "unreadable",
    "error",
)

#: Sources behind results, team lines and positions.
KEYS_STATS: Final[tuple[str, ...]] = ("nba.stats", "nba.rosters")
#: The source behind a status entry.
KEYS_AVAILABILITY: Final[tuple[str, ...]] = ("nba.injuryReport",)
#: The sources behind a matchup or a projection: results, positions, and who is missing.
KEYS_MATCHUP: Final[tuple[str, ...]] = ("nba.stats", "nba.rosters", "nba.injuryReport")

#: What the NBA side can do, by the names a client keys on.
FEATURES: Final[tuple[str, ...]] = (
    "matchup",
    "defenseByPosition",
    "projections",
    "availability",
    "news",
)

_STALE_AFTER: Final = timedelta(hours=24)
_DEMO_STATS: Final = "Demo league: every game here is invented."
_DEMO_ROSTERS: Final = "Demo league: listed positions are the demo's own labels, not a roster."
_ON: Final = {"1", "true", "t", "yes", "y", "on"}


@dataclass(frozen=True)
class SourceInfo:
    key: str
    label: str
    kind: str
    enabled: bool
    state: str
    reason: str | None
    last_success_at: datetime | None
    last_error: str | None
    robots_checked_on: Any
    attribution: str

    def payload(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "enabled": self.enabled,
            "state": self.state,
            "reason": self.reason,
            "lastSuccessAt": refs.rfc3339(self.last_success_at),
            "lastError": self.last_error,
            "robotsCheckedOn": refs.iso_date(self.robots_checked_on),
            "attribution": self.attribution,
        }

    def summary(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "state": self.state,
            "reason": self.reason,
            "lastSuccessAt": refs.rfc3339(self.last_success_at),
        }


def _info(key: str, label: str, kind: str, **fields: Any) -> SourceInfo:
    base: dict[str, Any] = {
        "enabled": True,
        "reason": None,
        "last_success_at": None,
        "last_error": None,
        "robots_checked_on": None,
        "attribution": PROFILE.attribution,
    }
    base.update(fields)
    return SourceInfo(key=key, label=label, kind=kind, **base)


def _waiting_for_results(ctx: ReadContext) -> bool:
    return any(ctx.game_status(g) == "resultPending" for g in ctx.games)


# --------------------------------------------------------------------------- the sources


def _stats(ctx: ReadContext) -> SourceInfo:
    session = ctx.session
    state_row = session.execute(select(SyncState).where(SyncState.id == 1)).scalar_one_or_none()
    last_ok = state_row.last_success_at if state_row is not None else None
    if ctx.is_demo:
        return _info(
            "nba.stats",
            "NBA statistics",
            "stats",
            state="ok",
            reason=_DEMO_STATS,
            last_success_at=last_ok,
        )
    finals = session.execute(
        select(func.count()).select_from(Game).where(Game.status == "final")
    ).scalar_one()
    newest_log = session.execute(
        select(IngestLog).order_by(IngestLog.id.desc()).limit(1)
    ).scalar_one_or_none()
    if (
        newest_log is not None
        and newest_log.status not in ("success", "ok", "running")
        and (
            last_ok is None
            or (newest_log.finished_at or newest_log.started_at or last_ok) > last_ok
        )
    ):
        return _info(
            "nba.stats",
            "NBA statistics",
            "stats",
            state="error",
            reason="The newest ingest run did not finish.",
            last_success_at=last_ok,
            last_error=newest_log.error,
        )
    if not finals:
        return _info(
            "nba.stats",
            "NBA statistics",
            "stats",
            state="noReportYet",
            reason="No game has been ingested yet.",
        )
    now = naive_utc(ctx.now)
    if _waiting_for_results(ctx) and (last_ok is None or now - last_ok > _STALE_AFTER):
        return _info(
            "nba.stats",
            "NBA statistics",
            "stats",
            state="stale",
            reason="A game is waiting for its result and the ingest has not delivered it.",
            last_success_at=last_ok,
        )
    return _info("nba.stats", "NBA statistics", "stats", state="ok", last_success_at=last_ok)


def _rosters(ctx: ReadContext) -> SourceInfo:
    session = ctx.session
    row = session.get(NbaIntelSourceState, "nba.rosters")
    detail = intel_store.source_detail(row)
    listings = session.execute(
        select(func.count())
        .select_from(PlayerPositionSeason)
        .where(PlayerPositionSeason.season == ctx.season)
    ).scalar_one()
    if ctx.is_demo:
        return _info(
            "nba.rosters",
            "Team rosters (listed positions)",
            "rosters",
            state="ok",
            reason=_DEMO_ROSTERS,
            last_success_at=row.last_success_at if row else None,
        )
    if row is None:
        reason = (
            "Rosters have not been fetched yet, so no player has a listed position."
            if not listings
            else "Positions were loaded from a bulk file; the weekly roster fetch has not run."
        )
        return _info(
            "nba.rosters",
            "Team rosters (listed positions)",
            "rosters",
            state="noReportYet",
            reason=reason,
        )
    state, reason = row.state, detail.get("reason")
    if state not in STATES:
        state, reason = "error", f"Unrecognised source state {row.state!r}."
    if row.paused_until is not None and row.paused_until > naive_utc(ctx.now):
        state, reason = (
            "blocked",
            f"Paused until {refs.rfc3339(row.paused_until)} after the service refused a request.",
        )
    return _info(
        "nba.rosters",
        "Team rosters (listed positions)",
        "rosters",
        state=state,
        reason=str(reason) if reason else None,
        last_success_at=row.last_success_at,
        last_error=row.last_error,
    )


def _injury_report(ctx: ReadContext) -> SourceInfo:
    label = "NBA official injury report"
    session = ctx.session
    if not availability_view.statuses_allowed(session):
        return _info(
            "nba.injuryReport",
            label,
            "injuryReport",
            enabled=False,
            state="disabled",
            reason=intel_store.SYNTHETIC_REASON,
        )
    if not injuries_switch_on():
        return _info(
            "nba.injuryReport",
            label,
            "injuryReport",
            enabled=False,
            state="disabled",
            reason="The injury report is switched off (HARDWOOD_NBA_INJURIES).",
        )
    row = session.get(NbaIntelSourceState, intel_store.SOURCE_INJURY_REPORT)
    detail = intel_store.source_detail(row)
    confirmed = intel_store.injury_parser_confirmation(session)
    caveat = (
        None
        if confirmed.confirmed
        else "Not yet confirmed against a real report: the reader was built from the documented "
        "layout and has not read one on this machine."
    )
    if row is None:
        return _info(
            "nba.injuryReport",
            label,
            "injuryReport",
            state="noReportYet",
            reason=" ".join(p for p in ("No injury report has been read yet.", caveat) if p),
        )
    state, reason = row.state, detail.get("reason")
    if state not in STATES:
        state, reason = "error", f"Unrecognised source state {row.state!r}."
    if row.paused_until is not None and row.paused_until > naive_utc(ctx.now):
        state, reason = (
            "blocked",
            f"Paused until {refs.rfc3339(row.paused_until)} after the host refused a request.",
        )
    elif state == "unreadable":
        reason = reason or "The newest report's layout was not recognised; nothing was guessed."
    if state == "ok":
        reason = " ".join(p for p in (str(reason) if reason else None, caveat) if p) or None
    return _info(
        "nba.injuryReport",
        label,
        "injuryReport",
        state=state,
        reason=str(reason) if reason else None,
        last_success_at=row.last_success_at,
        last_error=row.last_error,
    )


def _news_switch_on() -> bool:
    """``HARDWOOD_NEWS``: unset means on; a recognised "on" value means on; anything else, off
    (the safe reading, and the one the worker applies)."""
    raw = os.environ.get("HARDWOOD_NEWS")
    if raw is None or not raw.strip():
        return True
    return raw.strip().lower() in _ON


def _feeds(ctx: ReadContext) -> list[SourceInfo]:
    session = ctx.session
    switch = _news_switch_on()
    out: list[SourceInfo] = []
    for feed in session.execute(
        select(NbaIntelNewsFeed).order_by(NbaIntelNewsFeed.feed_id)
    ).scalars():
        key = intel_store.news_source_key(feed.feed_id)
        state_row = session.get(NbaIntelSourceState, key)
        common = dict(
            robots_checked_on=feed.robots_checked_on,
            attribution=f"Headlines: {feed.name}.",
            last_success_at=state_row.last_success_at if state_row else None,
            last_error=state_row.last_error if state_row else None,
        )
        if not switch:
            out.append(
                _info(
                    key, feed.name, "news", enabled=False, state="disabled",
                    reason="Headlines are switched off (HARDWOOD_NEWS).", **common,
                )
            )  # fmt: skip
            continue
        if not feed.enabled:
            reason = feed.robots_reason or "Turned off."
            if feed.robots_state == "disallowed":
                reason = f"robots.txt disallows fetching this feed: {reason}"
            out.append(
                _info(
                    key, feed.name, "news", enabled=False, state="disabled", reason=reason, **common
                )
            )
            continue
        if feed.last_fetch_at is None and state_row is None:
            state, reason = "noReportYet", "This feed has not been fetched yet."
        elif state_row is not None and state_row.state in STATES and state_row.state != "ok":
            state, reason = state_row.state, state_row.last_error
        else:
            state, reason = "ok", None
        out.append(_info(key, feed.name, "news", state=state, reason=reason, **common))
    return out


def collect(ctx: ReadContext) -> list[SourceInfo]:
    """Every source, in the order the panel shows them (computed once per context)."""
    cached = ctx.cache.get("sources")
    if cached is None:
        cached = ctx.cache["sources"] = [
            _stats(ctx),
            _rosters(ctx),
            _injury_report(ctx),
            *_feeds(ctx),
        ]
    return cached


def summaries(ctx: ReadContext, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """``SourceStateSummary`` blocks for just ``keys`` (a key with no source is left out)."""
    wanted = set(keys)
    return [s.summary() for s in collect(ctx) if s.key in wanted]


def news_keys(ctx: ReadContext) -> tuple[str, ...]:
    return tuple(s.key for s in collect(ctx) if s.kind == "news")


def freshness_for(ctx: ReadContext, keys: tuple[str, ...]) -> dict[str, Any]:
    """The ``freshness`` block of a payload fed by the sources in ``keys``.

    ``dataThrough`` is the day of the newest result in the store (never the fetch date),
    ``syncVersion`` the NBA's own cursor, and ``isDemo`` is what a client shows as a banner.
    """
    return refs.freshness_block(
        NBA_KEY,
        sync_version=ctx.sync_version,
        data_through=ctx.data_through,
        generated_at=ctx.now,
        is_demo=ctx.is_demo,
        sources=summaries(ctx, keys),
    )


_DAY_ONE_DEMO: Final = (
    "Demo league: every game and player here is invented, shown so each screen works offline. "
    "Real injury statuses are not shown next to invented games."
)
_DAY_ONE_HEADLINES: Final = (
    "Headlines are a title, a link, a date and the outlet's name, from the feeds that are on."
)


def day_one_notice(ctx: ReadContext) -> str:
    """What the user has today, in plain words, from the state of the store and its sources.

    Three sentences at most that matter: what is computed from the games, what is known about
    injuries (and what the model assumes while nothing is), and what a headline is. Each is chosen
    by state, so the notice is never more hopeful than the sources beneath it.
    """
    if ctx.is_demo:
        return f"{_DAY_ONE_DEMO} {_DAY_ONE_HEADLINES}"
    infos = {info.key: info for info in collect(ctx)}
    through = f", which run through {refs.iso_date(ctx.data_through)}" if ctx.data_through else ""
    games = (
        "Scores, form, points allowed, defence by position and team projections are computed from "
        f"the games in this store{through}."
    )
    report = infos["nba.injuryReport"]
    if report.state == "disabled":
        injuries = f"Injury statuses are off: {report.reason}"
    elif report.state == "noReportYet":
        injuries = (
            "No injury report has been read yet, so no status is shown and every player is "
            "projected to play. The league publishes its report on game days, so there is none "
            "before the season's first."
        )
    elif report.state == "notConfigured":
        why = report.reason or "its reader is not installed."
        injuries = f"The injury report cannot be read here: {why}"
    elif report.state in ("unreadable", "blocked", "error"):
        injuries = (
            f"The injury report is {report.state}: nothing is guessed, and players without a "
            "status are projected to play."
        )
    else:
        injuries = (
            "Injury statuses are from the league's official report, dated and never presented as "
            "live; players with no status in force are projected to play."
        )
        if not intel_store.injury_parser_confirmation(ctx.session).confirmed:
            injuries += " The reader has not yet been confirmed against a real report."
    return f"{games} {injuries} {_DAY_ONE_HEADLINES}"


def build_source_list(ctx: ReadContext, freshness: dict[str, Any]) -> dict[str, Any]:
    """``SourceList``: every source with its state and what to do about it."""
    return {
        "league": NBA_KEY,
        "freshness": freshness,
        "sources": [s.payload() for s in collect(ctx)],
        "dayOneNotice": day_one_notice(ctx),
        "attribution": PROFILE.attribution,
    }


# --------------------------------------------------------------------------- leagues


def league_row(session: Session, now: datetime | None = None) -> dict[str, Any]:
    """The NBA's row of ``GET /v1/leagues``. Never raises: a store that cannot be read is an
    ``error`` row with the reason, so a client can still show the league as unavailable."""
    row: dict[str, Any] = {
        "key": NBA_KEY,
        "name": PROFILE.name,
        "apiPrefix": PROFILE.api_prefix,
        "enabled": False,
        "state": "notConfigured",
        "reason": "No NBA season is loaded yet.",
        "isDemo": False,
        "currentSeason": None,
        "syncVersion": None,
        "dataThrough": None,
        "regulationMinutes": PROFILE.regulation_minutes,
        "perModes": list(PROFILE.per_modes),
        "positionBuckets": list(PROFILE.position_buckets),
        "features": [],
    }
    try:
        seasons = loaded_seasons(session)
        if not seasons:
            return row
        state = session.execute(select(SyncState).where(SyncState.id == 1)).scalar_one_or_none()
        row.update(
            enabled=True,
            state="ready",
            reason=None,
            isDemo=intel_store.store_is_synthetic(session),
            currentSeason=latest_season(session),
            syncVersion=(state.sync_version or 0) if state is not None else 0,
            dataThrough=refs.iso_date(state.data_through) if state is not None else None,
            features=list(FEATURES),
        )
    except Exception as exc:  # noqa: BLE001 - describing the league must never fail a request
        row.update(
            enabled=False,
            state="error",
            reason=f"The store could not be read: {exc}",
            features=[],
        )
    return row
