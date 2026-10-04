"""Sources and freshness: where every EuroLeague number came from, and how current it is.

The workbook's "Method / sources" sheet listed where each number came from. A product that shows a
projection owes the same thing live, because the honest answer to "is this current?" is different
for each source and changes while the app is open. This module computes it, once, for two uses:

* ``GET /v1/el/sources``: the full list, which a client renders as a "Sources and freshness"
  panel (:func:`build_source_list`);
* the ``freshness.sources`` block every payload carries, which holds only the sources that fed
  *that* payload (:func:`summaries`), so a payload about injuries does not carry the box-score
  ingest's health.

The four keys (design section 6.5)
----------------------------------
``el.workbook``     the user's workbook: results, squads, ratings, a dated injury list. Imported
                    from the inbox, never live.
``el.dataService``  the EuroLeague's own service: fixtures, results, box scores, squads. Polite,
                    switchable (``HARDWOOD_EL_LIVE``), and the only source that can make Round 3
                    stop being "result pending" without the user doing anything.
``el.manual``       statuses and links a person entered by hand.
``el.news.<id>``    one headline feed. Title, link, date and source name only.

States
------
``ok``, ``stale``, ``disabled``, ``notConfigured``, ``noReportYet``, ``blocked``, ``unreadable``,
``error``. ``stale`` means *something should have arrived and did not*: the service last succeeded
more than a day ago while a game is waiting for its result. A quiet fortnight between rounds is not
stale. ``blocked`` is the service refusing us (the circuit breaker the live ingest opens on a 401,
a 403, a Cloudflare 1015 or three 429s in a row), and it says until when. There is no
``termsNotReviewed``: the terms gate was removed, and ``docs/LEGAL.md`` records the posture
applied to each source instead.

An unreadable or unexpected source state is reported as ``error`` with the reason, never as ``ok``:
a state this module does not recognise must not look healthy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...shared import refs
from ..bootstrap import get_state
from ..config import get_el_settings
from ..db import read_sync_state
from ..models import ElIngestLog, ElIntelNewsFeed, ElIntelStatus, ElSeason, ElSourceState
from ..profile import PROFILE, load_club_codes
from .queries import ReadContext, build_context, naive_utc, round_status, utc_now

__all__ = [
    "STATES",
    "KEYS_STATS",
    "KEYS_AVAILABILITY",
    "SourceInfo",
    "collect",
    "build_source_list",
    "summaries",
    "news_keys",
    "freshness_for",
    "build_meta",
    "build_health",
    "build_sync",
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

#: Sources behind results, ratings and box scores.
KEYS_STATS: Final[tuple[str, ...]] = ("el.workbook", "el.dataService")
#: Sources behind a status entry.
KEYS_AVAILABILITY: Final[tuple[str, ...]] = ("el.workbook", "el.manual")

_STALE_AFTER: Final = timedelta(hours=24)
_DEMO_REASON: Final = "Demo league: this source is not used for invented games."


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


def _waiting_for_results(ctx: ReadContext) -> bool:
    return any(ctx.game_status(g) == "resultPending" for g in ctx.games)


def _workbook(ctx: ReadContext) -> SourceInfo:
    base = dict(
        key="el.workbook", label="Your workbook", kind="workbook", attribution=PROFILE.attribution
    )
    if ctx.is_demo:
        return SourceInfo(
            **base,
            enabled=False,
            state="disabled",
            reason=_DEMO_REASON,
            last_success_at=None,
            last_error=None,
            robots_checked_on=None,
        )
    logs = list(
        ctx.session.execute(
            select(ElIngestLog).where(ElIngestLog.job == "workbook").order_by(ElIngestLog.id)
        ).scalars()
    )
    ok = [r for r in logs if r.status == "ok"]
    failed = [r for r in logs if r.status != "ok"]
    last_ok = ok[-1].finished_at or ok[-1].started_at if ok else None
    if failed and (not ok or failed[-1].id > ok[-1].id):
        return SourceInfo(
            **base,
            enabled=True,
            state="error",
            reason="The newest workbook could not be imported.",
            last_success_at=last_ok,
            last_error=failed[-1].error,
            robots_checked_on=None,
        )
    if not ok:
        return SourceInfo(
            **base,
            enabled=True,
            state="noReportYet",
            reason="No workbook has been imported yet.",
            last_success_at=None,
            last_error=None,
            robots_checked_on=None,
        )
    return SourceInfo(
        **base,
        enabled=True,
        state="ok",
        reason="Imported once; refreshed only when you import a newer workbook.",
        last_success_at=last_ok,
        last_error=None,
        robots_checked_on=None,
    )


def _data_service(ctx: ReadContext) -> SourceInfo:
    base = dict(
        key="el.dataService",
        label="EuroLeague data service",
        kind="dataService",
        attribution=PROFILE.attribution,
    )
    if ctx.is_demo:
        return SourceInfo(
            **base,
            enabled=False,
            state="disabled",
            reason=_DEMO_REASON,
            last_success_at=None,
            last_error=None,
            robots_checked_on=None,
        )
    try:
        live = get_el_settings().live
    except ValueError:  # a malformed switch in the environment: say so rather than guess
        return SourceInfo(
            **base,
            enabled=False,
            state="error",
            reason="HARDWOOD_EL_LIVE is not a valid on/off value.",
            last_success_at=None,
            last_error=None,
            robots_checked_on=None,
        )
    row = ctx.session.get(ElSourceState, "el.dataService")
    if not live:
        return SourceInfo(
            **base,
            enabled=False,
            state="disabled",
            reason="Live ingest is switched off (HARDWOOD_EL_LIVE).",
            last_success_at=row.last_success_at if row else None,
            last_error=row.last_error if row else None,
            robots_checked_on=None,
        )
    if row is None:
        return SourceInfo(
            **base,
            enabled=True,
            state="noReportYet",
            reason="Live ingest has not run yet.",
            last_success_at=None,
            last_error=None,
            robots_checked_on=None,
        )
    now = naive_utc(ctx.now)
    state, reason = row.state, None
    if row.paused_until is not None and row.paused_until > now:
        state, reason = (
            "blocked",
            f"Paused until {refs.rfc3339(row.paused_until)} after the service refused a request.",
        )
    elif state not in STATES:
        state, reason = "error", f"Unrecognised source state {row.state!r}."
    elif (
        state == "ok"
        and _waiting_for_results(ctx)
        and (row.last_success_at is None or now - row.last_success_at > _STALE_AFTER)
    ):
        state, reason = (
            "stale",
            "A game is waiting for its result and the service has not delivered it.",
        )
    return SourceInfo(
        **base,
        enabled=True,
        state=state,
        reason=reason,
        last_success_at=row.last_success_at,
        last_error=row.last_error,
        robots_checked_on=None,
    )


def _manual(ctx: ReadContext) -> SourceInfo:
    base = dict(
        key="el.manual",
        label="Entered by hand",
        kind="manual",
        attribution="Statuses and links entered by hand, each with its source and date.",
    )
    newest = ctx.session.execute(
        select(func.max(ElIntelStatus.recorded_at)).where(
            ElIntelStatus.source_kind.in_(("manual", "pressArticle", "clubStatement")),
            ElIntelStatus.retracts_status_id.is_(None),
        )
    ).scalar_one()
    if newest is None:
        return SourceInfo(
            **base,
            enabled=True,
            state="noReportYet",
            reason="Nothing has been entered by hand yet.",
            last_success_at=None,
            last_error=None,
            robots_checked_on=None,
        )
    return SourceInfo(
        **base,
        enabled=True,
        state="ok",
        reason=None,
        last_success_at=newest,
        last_error=None,
        robots_checked_on=None,
    )


def _feeds(ctx: ReadContext) -> list[SourceInfo]:
    out: list[SourceInfo] = []
    for feed in ctx.session.execute(
        select(ElIntelNewsFeed).order_by(ElIntelNewsFeed.feed_id)
    ).scalars():
        key = f"el.news.{feed.feed_id}"
        base = dict(key=key, label=feed.name, kind="news", attribution=f"Headlines: {feed.name}.")
        state_row = ctx.session.get(ElSourceState, key)
        if not feed.enabled:
            out.append(
                SourceInfo(
                    **base,
                    enabled=False,
                    state="disabled",
                    reason=feed.disabled_reason or "Turned off.",
                    last_success_at=state_row.last_success_at if state_row else None,
                    last_error=state_row.last_error if state_row else None,
                    robots_checked_on=feed.robots_checked_on,
                )
            )
            continue
        last = state_row.last_success_at if state_row else feed.last_fetch_at
        if feed.last_fetch_at is None and state_row is None:
            state, reason = "noReportYet", "This feed has not been fetched yet."
        elif state_row is not None and state_row.state in STATES and state_row.state != "ok":
            state, reason = state_row.state, state_row.last_error
        else:
            state, reason = "ok", None
        out.append(
            SourceInfo(
                **base,
                enabled=True,
                state=state,
                reason=reason,
                last_success_at=last,
                last_error=state_row.last_error if state_row else None,
                robots_checked_on=feed.robots_checked_on,
            )
        )
    return out


def collect(ctx: ReadContext) -> list[SourceInfo]:
    """Every source, in the order the panel shows them (computed once per context)."""
    cached = ctx.cache.get("sources")
    if cached is None:
        cached = ctx.cache["sources"] = [
            _workbook(ctx),
            _data_service(ctx),
            _manual(ctx),
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
    ``syncVersion`` the EuroLeague's own cursor (independent of the NBA's), and ``isDemo`` is what
    a client shows as a banner.
    """
    return refs.freshness_block(
        "euroleague",
        sync_version=ctx.sync_version,
        data_through=ctx.data_through,
        generated_at=ctx.now,
        is_demo=ctx.is_demo,
        sources=summaries(ctx, keys),
    )


def build_source_list(ctx: ReadContext, freshness: dict[str, Any]) -> dict[str, Any]:
    """``SourceList``: every source with its state and what to do about it."""
    boot = get_state()
    return {
        "league": "euroleague",
        "freshness": freshness,
        "sources": [s.payload() for s in collect(ctx)],
        "store": {"state": boot.state, "reason": boot.reason, "kind": ctx.kind},
        "attribution": PROFILE.attribution,
    }


# --------------------------------------------------------------------------- meta, health, sync

_DAY_ONE_REAL: Final = (
    "EuroLeague data comes from your workbook: results and box scores for the rounds it holds, "
    "squads, ratings and a dated list of statuses. A round played after the workbook was exported "
    "shows as result pending until live ingest is on or you import an updated workbook. "
    "Availability is researched and dated, never live."
)
_DAY_ONE_DEMO: Final = (
    "Demo league: invented clubs and players, shown so every screen works offline. "
    "Nothing here is real."
)


def _day_one_notice(ctx: ReadContext) -> str:
    base = _DAY_ONE_DEMO if ctx.is_demo else _DAY_ONE_REAL
    pending = sorted({g.round_number for g in ctx.games if ctx.game_status(g) == "resultPending"})
    if not pending or ctx.is_demo:
        return base
    names = ", ".join(str(n) for n in pending)
    noun = "Round" if len(pending) == 1 else "Rounds"
    return f"{base} {noun} {names}: played, results not loaded."


def _rounds(ctx: ReadContext) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for number in ctx.rounds():
        games = ctx.games_of_round(number)
        tips = [g.tipoff_utc for g in games if g.tipoff_utc is not None]
        out.append(
            {
                "round": number,
                "phase": games[0].phase_code,
                "firstTipoffUtc": refs.rfc3339(min(tips)) if tips else None,
                "games": len(games),
                "status": round_status(ctx.game_status(g) for g in games),
            }
        )
    return out


def build_meta(session: Session | None, *, now: datetime | None = None) -> dict[str, Any]:
    """``GET /v1/el/meta``: what the league is, what state it is in and what the user can expect.

    Answers in *every* state, including when the store is unusable: a client needs the state and
    the reason to explain an empty screen, so this is the one payload that is never a 503.
    """
    boot = get_state()
    moment = now if now is not None else utc_now()
    head: dict[str, Any] = {
        "league": "euroleague",
        "state": boot.state,
        "reason": boot.reason,
        "isDemo": boot.is_demo,
        "mode": None,
        "seasons": [],
        "currentSeason": None,
        "currentSeasonCode": None,
        "rounds": [],
        "clubs": [],
        "regulationMinutes": PROFILE.regulation_minutes,
        "perModes": list(PROFILE.per_modes),
        "positionBuckets": list(PROFILE.position_buckets),
        "unverifiedClubCodes": [],
        "attribution": PROFILE.attribution,
        "dataThrough": None,
        "dayOneNotice": _DAY_ONE_REAL if not boot.is_demo else _DAY_ONE_DEMO,
        "freshness": None,
    }
    if not boot.ready or session is None:
        return head
    try:
        ctx = build_context(session, None, now=moment)
    except Exception:  # a store with no season yet: say so rather than fail
        head["reason"] = head["reason"] or "No EuroLeague season is loaded yet."
        return head
    seasons = session.execute(select(ElSeason).order_by(ElSeason.start_year)).scalars().all()
    club_codes = set(ctx.clubs)
    head.update(
        {
            "isDemo": ctx.is_demo,
            "mode": ctx.mode,
            "seasons": [
                {"code": s.season_code, "label": s.label, "isCurrent": bool(s.is_current)}
                for s in seasons
            ],
            "currentSeason": ctx.season_label,
            "currentSeasonCode": ctx.season_code,
            "rounds": _rounds(ctx),
            "clubs": [ctx.team_ref(code) for code in sorted(ctx.clubs)],
            "unverifiedClubCodes": [
                code for code in load_club_codes().unverified_official_codes if code in club_codes
            ],
            "dataThrough": refs.iso_date(ctx.data_through),
            "dayOneNotice": _day_one_notice(ctx),
            "freshness": freshness_for(ctx, KEYS_STATS),
        }
    )
    return head


def build_health(session: Session | None, *, now: datetime | None = None) -> dict[str, Any]:
    """``GET /v1/el/health``: the EuroLeague's cursor and state only. Never needs a key, never a 503."""
    boot = get_state()
    moment = now if now is not None else utc_now()
    out: dict[str, Any] = {
        "league": "euroleague",
        "status": "ok" if boot.ready else "unavailable",
        "state": boot.state,
        "reason": boot.reason,
        "isDemo": boot.is_demo,
        "syncVersion": None,
        "dataThrough": None,
        "generatedAt": refs.rfc3339(moment),
    }
    if boot.ready and session is not None:
        state = read_sync_state(session)
        out["syncVersion"] = state.sync_version or 0
        out["dataThrough"] = refs.iso_date(state.data_through)
    return out


def build_sync(session: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """``GET /v1/el/sync``: the EuroLeague's freshness cursor, independent of the NBA's."""
    moment = now if now is not None else utc_now()
    state = read_sync_state(session)
    boot = get_state()
    return {
        "league": "euroleague",
        "syncVersion": state.sync_version or 0,
        "dataThrough": refs.iso_date(state.data_through),
        "mode": state.mode,
        "isDemo": boot.is_demo or state.mode == "demo",
        "lastSuccessAt": refs.rfc3339(state.last_success_at),
        "pausedUntil": refs.rfc3339(state.paused_until),
        "generatedAt": refs.rfc3339(moment),
    }
