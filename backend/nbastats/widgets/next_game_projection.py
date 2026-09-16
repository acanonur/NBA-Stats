"""``next_game_projection`` — a projected box score for a player's next game.

The mathematics lives in :mod:`nbastats.projection` and the fitted constants in
:mod:`nbastats.projection_constants`; ``docs/PROJECTION.md`` derives both. **Nothing in this
module reimplements a formula.** Its whole job is the part the engine deliberately does not
do: find the game, load the player's pre-tip-off history out of the store, turn it into the
engine's inputs, and hand the result back in the shape ``contracts/CONTRACT.md`` §4 fixes.

    S_hat = M_hat * r_hat_reg * f_pace * f_opp * f_home * f_rest

Four rules this resolver is built around
----------------------------------------

1.  **Strictly pre-tip-off.** See "the pre-tip-off guarantee" below. A projection that has
    seen the game it is projecting is worth nothing, and the failure is silent — the numbers
    look *better*, not broken — so the exclusion is one predicate applied in one place.

2.  **A projection is never a record.** Every line carries ``availability: "estimated"``
    (``docs/PROJECTION.md`` §7 rule 5), which the engine writes and this module never
    overrides. The result's own availability is ``"estimated"`` for the same reason.

3.  **Degrade honestly.** A player with two games gets a rate shrunk almost entirely to the
    league prior, a dispersion multiplier of 1.0 and a wide interval — and the payload
    reports the exposure behind the estimate (``exposureMinutes``, ``shrinkageWeight``)
    rather than hiding it (§7 rule 4). A missing input produces a neutral factor whose
    *explanation says it is missing*, never a guess.

4.  **No market translation.** No implied probability, no vig, no expected value, no staking,
    nowhere. ``docs/PROJECTION.md`` §6: NBA.com's terms forbid gambling use of their
    statistics, and the source paper makes no profitability claim.

Era gate
--------
``f_pace`` and ``f_opp`` need team pace and defensive rating, which begin with league-wide
possession data in **1996-97** (``contracts/CONTRACT.md`` §6; the widget catalog says the
same with ``availableFrom``). Before that the context factors cannot be computed at all, so a
1985-86 request comes back ``"unavailable"`` with a note — *not* a projection quietly built
from neutral factors, which would be a confident-looking number with a missing term in it.

Two engine features this resolver deliberately does not use
-----------------------------------------------------------
:func:`nbastats.projection.calibrate_context_factors` fits ``f_home`` and ``f_rest`` from
realised-over-raw-predicted ratios, and it wants a *league-wide* panel of raw predictions
with at least 25 rows in every venue and rest bucket. One player never clears that floor, so
fitting per resolve would burn the scan and hand every bucket its default anyway. The
documented defaults are used instead, and each factor's ``explanation`` says out loud that it
is a default rather than a fitted value.

:func:`nbastats.projection.combo`'s correlation is the paper's PTS/REB/AST block, not a
per-player fit; §5 reports it as a population quantity and that is how it is applied.

Payload: ``contracts/CONTRACT.md`` §4 ``next_game_projection``.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Callable, Iterable, NamedTuple, Optional, Sequence

from sqlalchemy import select

from .. import catalog
from .. import projection as P
from .. import projection_constants as C
from ..models import Game, PlayerGameBasic
from . import queries as q
from .base import (
    ResolveContext,
    invalid_config,
    resolve_season,
    resolve_subject_token,
)

__all__ = [
    "resolve",
    "PROJECTION_FROM",
    "era_gap_note",
    "CONTEXT_METRICS",
    "INTERVAL_LEVELS",
    "COMPETITIVE_SEASON_TYPES",
    "FORM_WINDOW_GAMES",
    "DISPERSION_MIN_MINUTES",
    "DISPERSION_POOL_MAX_ROWS",
    "DISPERSION_POOL_MIN_ROWS",
    "THIN_HISTORY_MARKER",
]

#: First season with the possession data ``f_pace`` and ``f_opp`` are built from.
PROJECTION_FROM = "1996-97"

#: The two team metrics the context factors need. Both begin in :data:`PROJECTION_FROM`.
CONTEXT_METRICS: tuple[str, ...] = ("pace", "def_rtg")

#: The widget catalog's ``interval`` enum, as the engine's ``interval_level``. ``"none"``
#: suppresses the bounds but never the mean's provenance.
INTERVAL_LEVELS: dict[str, Optional[float]] = {"80": 0.80, "50": 0.50, "none": None}

#: Season types that count as playing history. An All-Star or pre-season appearance is not
#: evidence about a rotation, and neither belongs in a rate or a dispersion estimate.
COMPETITIVE_SEASON_TYPES: tuple[str, ...] = ("Regular Season", "Playoffs", "Play In")

#: How far back the form/minutes EWMA reaches when the current season has no games yet and
#: the series falls back to the tail of the player's career. One season's worth.
FORM_WINDOW_GAMES = 82

#: Game lines below this many minutes are dropped from every dispersion fit. Garbage time is
#: a different data-generating process, and a two-minute line has no variance information.
DISPERSION_MIN_MINUTES = 5.0

#: Cap on the league sample the pooled ``alpha`` is fitted from, taken as the most recent
#: pre-tip-off lines. Bounds the cost of one resolve however long the season is.
DISPERSION_POOL_MAX_ROWS = 6000

#: Below this many usable pairs the pooled ``alpha`` is thin enough to say so in a note.
DISPERSION_POOL_MIN_ROWS = 200

#: How :func:`nbastats.projection.project_box_score` opens its thin-history note. The engine
#: owns the rule (it owns the exposure and the shrinkage weight); the resolver only has to
#: recognise the sentence so it can lift it into the result's notes.
THIN_HISTORY_MARKER = "Thin history"


# --------------------------------------------------------------- the pre-tip-off guarantee
#
# HOW LEAKAGE IS PREVENTED, in one sentence: every historical read in this module is filtered
# by ``Game.game_date < cutoff``, where ``cutoff`` is the target game's own scheduling date.
#
# * It is a **strict** inequality on the date, not on the game id and not on ``status``, so
#   the target game's rows cannot enter however they are ordered, whatever their id, and
#   whether or not they have been ingested yet. Nor can any *other* game played that day.
# * There is exactly one cutoff value per resolve, and it is passed to all three readers —
#   :func:`_player_history` (the player's own lines), :func:`_team_form` (pace and defensive
#   rating for both teams) and :func:`_dispersion_pool` (the league sample the pooled alpha
#   is fitted from). A future game that has somehow acquired a box score changes nothing.
# * When there is no next game there is no cutoff, and the whole store is history by
#   definition — there is no game being projected to peek at.
#
# ``tests/test_next_game_projection.py::test_a_projection_cannot_see_the_game_it_projects``
# is the executable version of this comment: it fabricates a box score for the target game
# and asserts the payload does not move by so much as a float.


class HistoryRow(NamedTuple):
    """One pre-tip-off appearance: the game it belongs to and the counting line."""

    game_id: str
    game_date: date
    season: str
    season_type: str
    team_id: int
    minutes: Optional[float]
    values: dict[str, Optional[float]]


class PoolRow(NamedTuple):
    """One league line in the dispersion sample: who, how long, and what he did."""

    player_id: int
    minutes: float
    values: dict[str, Optional[float]]


# --------------------------------------------------------------------------- the resolver


def resolve(config: dict[str, Any], ctx: ResolveContext) -> tuple[dict[str, Any], str, list[str]]:
    """Resolve one ``next_game_projection``."""
    # ``notes`` is what turns this result's status into ``"partial"``, so it carries only what
    # is genuinely missing or degraded. ``commentary`` is method detail that belongs in the
    # payload's own ``notes`` array beside the engine's — true, worth saying, not a defect.
    notes: list[str] = []
    commentary: list[str] = []

    player_id = resolve_subject_token(config.get("playerId"), "player", ctx, field="playerId")
    ctx.note_player(player_id)

    season = resolve_season(ctx, config.get("season"))
    season_type = config.get("seasonType") or "Regular Season"
    interval_level = INTERVAL_LEVELS.get(str(config.get("interval") or "80"), 0.80)
    show_combo = bool(config.get("showCombo", True))
    show_factors = bool(config.get("showFactors", True))
    player_ref = q.player_ref_dict(ctx, player_id, season)

    # Era gate first, before a single statistic is read: with no pace and no defensive rating
    # there is no f_pace and no f_opp, so there is no projection to make — only neutral
    # factors to pretend with.
    era_note = era_gap_note(season)
    if era_note is not None:
        notes.append(era_note)
        return _unavailable_payload(player_ref, era_note), "unavailable", notes

    stats = _requested_stats(config.get("stats"), season, notes)

    # --- the game -----------------------------------------------------------------------
    team_id = _player_team_id(ctx, player_id, season, season_type)
    game = _next_game(ctx, team_id, season, season_type) if team_id is not None else None
    cutoff = game.game_date if game is not None else None

    if game is None:
        notes.append(
            f"No scheduled {season} {season_type} game is loaded for this player; the "
            "projection is against a league-average opponent, with neutral venue and rest."
        )

    # --- the player's pre-tip-off history -----------------------------------------------
    history = _player_history(ctx, player_id, cutoff)
    season_rows = [r for r in history if r.season == season and r.season_type == season_type]
    form_rows = season_rows
    if not form_rows:
        form_rows = history[-FORM_WINDOW_GAMES:]
        if form_rows:
            notes.append(
                f"No {season} {season_type} games yet: recent form is taken from this "
                "player's most recent games before it."
            )
    if not history:
        notes.append(
            "No pre-tip-off game history is loaded for this player, so every rate is the "
            "league prior and the dispersion multiplier is exactly 1.0."
        )

    # --- context ------------------------------------------------------------------------
    opponent_id, is_override = _opponent_id(config.get("opponentTeamId"), ctx, game, team_id)
    if is_override:
        commentary.append(
            "Opponent overridden by configuration; f_opp and the pace term describe the "
            "chosen opponent, not the one on the schedule."
        )
    factors, context = _context(
        ctx, team_id, opponent_id, season, season_type, cutoff, game, history, notes
    )

    # --- the projection ------------------------------------------------------------------
    season_minutes_average = _mean([r.minutes for r in season_rows])
    minutes = P.project_minutes(
        [r.minutes for r in form_rows],
        season_average=season_minutes_average,
        level=interval_level,
    )
    stat_inputs = [
        _stat_input(ctx, key, season, season_type, cutoff, season_rows, form_rows, history, notes)
        for key in stats
    ]
    poisson = [s.stat_key.upper() for s in stat_inputs if s.dispersion_alpha <= 0.0]
    if poisson:
        commentary.append(
            f"No overdispersion is detectable in the league sample for {', '.join(poisson)}: "
            "the method-of-moments alpha fits at zero, so the predictive distribution is the "
            "Poisson limit Var = mean rather than a wider negative binomial. A dispersion "
            "multiplier below 1.0 is reported as fitted but cannot narrow an interval past "
            "that floor."
        )

    payload = P.project_box_score(
        stat_inputs,
        minutes=minutes,
        factors=factors,
        interval_level=interval_level,
        minutes_season_average=season_minutes_average,
        include_combo=show_combo,
        player=player_ref,
        game=_game_block(ctx, game, team_id, opponent_id, context, history),
        notes=[],
    )
    if not show_factors:
        # The reader turned the factors off. They are still in the mean — a multiplier is not
        # optional arithmetic — so the payload keeps the key and empties the block rather
        # than pretending the projection was computed without them.
        payload["factors"] = []
    payload["notes"] = _dedupe(list(payload["notes"]) + notes + commentary)
    # The engine decides what counts as thin history (it owns the exposure and the shrinkage
    # weight), and §7 rule 4 says that finding must reach the reader — so its note is lifted
    # into the result's own notes, which is what makes the result ``"partial"``. Promoting the
    # engine's sentence rather than recomputing the rule keeps one threshold, not two.
    notes.extend(
        note for note in payload["notes"] if note.startswith(THIN_HISTORY_MARKER)
    )

    # docs/PROJECTION.md §7 rule 5: a projection is an estimate, never a record.
    return payload, P.AVAILABILITY, notes


# --------------------------------------------------------------------------- config


def _requested_stats(requested: Any, season: str, notes: list[str]) -> list[str]:
    """The projectable statistics this tile asked for, in the order it asked for them.

    The catalog's ``stats`` field is a ``metricList`` over every player metric, so a layout
    may legitimately arrive asking for TS% — which has no per-minute rate, no fitted ``k``
    and no counting distribution. Those are dropped with a note. A tile that asked for
    *nothing* projectable is a configuration error naming its own field, not a tile silently
    re-pointed at some other statistic.
    """
    defaults = catalog.widget_config_defaults("next_game_projection")["stats"]
    keys = list(requested or []) or list(defaults)
    kept: list[str] = []
    dropped: list[str] = []
    era_gated: list[str] = []
    for key in keys:
        if not isinstance(key, str) or not C.has_stat(key):
            dropped.append(str(key))
            continue
        if catalog.metric_availability(key, season, "game") == "unavailable":
            era_gated.append(key)
            continue
        if key not in kept:
            kept.append(key)

    if dropped:
        notes.append(
            "Not a counting statistic with fitted projection constants, so it cannot be "
            f"projected: {', '.join(sorted(set(dropped)))}. "
            f"Projectable statistics are {', '.join(C.PROJECTABLE_STATS)}."
        )
    if era_gated:
        notes.append(
            f"{', '.join(key.upper() for key in era_gated)} was not recorded in {season}, so "
            "it is not projected."
        )
    if not kept:
        raise invalid_config(
            "None of the requested statistics can be projected. Choose from "
            f"{', '.join(C.PROJECTABLE_STATS)}.",
            "stats",
        )
    return kept


def era_gap_note(season: str) -> Optional[str]:
    """The reason a season cannot be projected at all, or ``None`` when it can."""
    gated = [
        key
        for key in CONTEXT_METRICS
        if catalog.metric_availability(key, season, "season") == "unavailable"
    ]
    if not gated:
        return None
    names = " and ".join(catalog.metric(key).get("name", key) for key in gated)
    return (
        f"{names} begin in {PROJECTION_FROM}, with league-wide possession data; the {season} "
        "season has box scores but no pace or defensive rating, so the context factors "
        "f_pace and f_opp cannot be computed. A projection without them would be a confident "
        "number with a missing term in it, so none is offered."
    )


def _unavailable_payload(player_ref: Optional[dict[str, Any]], note: str) -> dict[str, Any]:
    """The §4 payload for a season the projection cannot be made in: shaped, and empty.

    Same treatment as ``shot_profile`` before 1996-97 — every key the client decodes is
    present, every number is ``null``, and the note says why. A 500 would claim the request
    was wrong when the request was fine and history is simply shorter than the widget.

    Built through the engine with no statistics rather than assembled here, so ``method`` and
    the rest of the envelope cannot drift from the payload a real projection produces. Two
    things are then overridden: the minutes, because "0.0" would be a claim that he will not
    play rather than "this was not projected", and the notes, because the reason is the era
    and not the schedule.
    """
    payload = P.project_box_score(
        [],
        minutes=P.MinutesProjection(0.0, 0.0, 0.0),
        factors=(),
        include_combo=False,
        player=player_ref,
        game=None,
        notes=[note],
    )
    payload["projectedMinutes"].update(
        {
            "value": None,
            "displayValue": catalog.EM_DASH,
            "seasonAverage": None,
            "low": None,
            "high": None,
        }
    )
    payload["notes"] = [note]
    return payload


# --------------------------------------------------------------------------- the game


def _player_team_id(
    ctx: ResolveContext, player_id: int, season: str, season_type: str
) -> Optional[int]:
    """The team whose schedule this player's next game comes from.

    The season row first, because that is what the rest of the app shows him with; then the
    team of his most recent appearance, which is what a player with games but no aggregate
    row (a mid-season arrival, a partially ingested season) actually has.
    """
    row = q.player_season_row(ctx, player_id, season, season_type)
    if row is not None and row.team_id:
        return int(row.team_id)
    history = _player_history(ctx, player_id, None)
    for line in reversed(history):
        if line.team_id:
            return int(line.team_id)
    team_row = q.player_team_map(ctx, [player_id], season).get(int(player_id))
    return int(team_row.team_id) if team_row is not None else None


def _reference_date(ctx: ResolveContext) -> Optional[date]:
    """"Now" for the purposes of "next": the caller's ``asOf``, else the store's cursor."""
    return ctx.as_of or ctx.data_through


def _next_game(
    ctx: ResolveContext, team_id: int, season: str, season_type: str
) -> Optional[Game]:
    """The team's next game that has not been played, or ``None``.

    A game is "next" when it is still ``scheduled`` and is not behind the store's own
    freshness cursor. A postponed fixture that the league never removed would otherwise sit
    in the past forever and be projected every night. A ``live`` game is deliberately not
    projected either: half its box score is already written, so a pre-tip-off projection of
    it would be answering a question that is no longer open.
    """
    reference = _reference_date(ctx)

    def _load() -> Optional[Game]:
        statement = (
            select(Game)
            .where(Game.season == season)
            .where(Game.season_type == season_type)
            .where(Game.status == "scheduled")
            .where((Game.home_team_id == int(team_id)) | (Game.away_team_id == int(team_id)))
            .order_by(Game.game_date, Game.game_id)
        )
        if reference is not None:
            statement = statement.where(Game.game_date >= reference)
        return ctx.session.execute(statement.limit(1)).scalars().first()

    return ctx.memo(("next_game", int(team_id), season, season_type, reference), _load)


def _opponent_id(
    configured: Any, ctx: ResolveContext, game: Optional[Game], team_id: Optional[int]
) -> tuple[Optional[int], bool]:
    """``(opponent_team_id, is_override)`` — the configured opponent wins when there is one."""
    if configured is not None:
        override = resolve_subject_token(configured, "team", ctx, field="opponentTeamId")
        return override, True
    if game is None or team_id is None:
        return None, False
    home = game.home_team_id == int(team_id)
    return int(game.away_team_id if home else game.home_team_id), False


def _rest_days(history: Sequence[HistoryRow], game: Optional[Game]) -> Optional[int]:
    """Days between the player's last appearance and the game, or ``None`` when unknown."""
    if game is None or not history:
        return None
    return max((game.game_date - history[-1].game_date).days, 0)


def _game_block(
    ctx: ResolveContext,
    game: Optional[Game],
    team_id: Optional[int],
    opponent_id: Optional[int],
    context: dict[str, Optional[float]],
    history: Sequence[HistoryRow],
) -> Optional[dict[str, Any]]:
    """The ``game`` object of §4, or ``None`` when there is no scheduled next game.

    ``opponent``, ``opponentDefRtg`` and ``expectedPace`` describe the matchup **that was
    projected**, so under an ``opponentTeamId`` override they are the override's — otherwise
    the block would contradict the ``f_opp`` sitting beside it in ``factors``. The override
    says so in the payload's notes, and ``gameId`` and ``date`` are always the real fixture's.
    """
    if game is None:
        return None
    is_home = team_id is not None and int(game.home_team_id) == int(team_id)
    opponent = q.team(ctx, opponent_id) if opponent_id is not None else None
    rest = _rest_days(history, game)
    return {
        "gameId": game.game_id,
        "date": game.game_date.isoformat(),
        "opponentAbbr": opponent.abbr if opponent is not None else None,
        "opponent": q.team_ref_dict(ctx, opponent_id) if opponent_id is not None else None,
        "isHome": is_home,
        "restDays": rest,
        "isBackToBack": (rest == 0) if rest is not None else None,
        "opponentDefRtg": _round(context.get("opp_def_rtg"), 1),
        "expectedPace": _round(context.get("expected_pace"), 1),
    }


# --------------------------------------------------------------------------- context


def _context(
    ctx: ResolveContext,
    team_id: Optional[int],
    opponent_id: Optional[int],
    season: str,
    season_type: str,
    cutoff: Optional[date],
    game: Optional[Game],
    history: Sequence[HistoryRow],
    notes: list[str],
) -> tuple[list[P.Factor], dict[str, Optional[float]]]:
    """The four multiplicative factors, and the raw inputs the ``game`` block reports.

    Team pace and opponent defensive rating are the *pre-tip-off* means of each team's own
    game rows — ``tm_pace_pre`` and ``opp_drtg_pre`` in the source pipeline — while the
    league normalisers ``Pace_lg`` and ``DRtg_lg`` are season-level constants, exactly as in
    ``nbaproj/models.py``: they are the units the two ratios are expressed in, not a
    statement about either team. A missing input is left neutral and *says so* in its
    explanation rather than being guessed at.
    """
    league_pace = q.distribution(ctx, "pace", "team", season, season_type).average
    league_drtg = q.distribution(ctx, "def_rtg", "team", season, season_type).average

    team_pace, _team_drtg, team_games = _team_form(ctx, team_id, season, season_type, cutoff)
    opp_pace, opp_drtg, opp_games = _team_form(ctx, opponent_id, season, season_type, cutoff)

    if opponent_id is None:
        # No opponent at all: the league-average one the contract calls for.
        opp_pace, opp_drtg = league_pace, league_drtg
    elif opp_games == 0:
        opp_pace, opp_drtg = league_pace, league_drtg
        notes.append(
            "The opponent has no completed games before this one, so its pace and defensive "
            "rating are the league average."
        )
    if team_id is not None and team_games == 0 and team_pace is None:
        team_pace = league_pace
        notes.append(
            "This player's team has no completed games before this one, so its pace is the "
            "league average."
        )

    rest = _rest_days(history, game)
    is_home = game is not None and team_id is not None and int(game.home_team_id) == int(team_id)
    factors = P.context_factors(
        team_pace,
        opp_pace,
        league_pace,
        opp_drtg,
        league_drtg,
        is_home,
        rest,
    )
    if game is None:
        # Venue is not unknown-but-guessable here, it is undefined: there is no game. A
        # neutral 1.0 that says why beats the home/road multiplier of a game nobody scheduled.
        factors = [
            P.Factor(
                "venue",
                "Venue",
                1.0,
                "No scheduled game, so the venue is unknown; no venue adjustment applied.",
            )
            if factor.key == "venue"
            else factor
            for factor in factors
        ]

    expected_pace: Optional[float] = None
    if team_pace and opp_pace and league_pace:
        # Pace_tm * Pace_opp / Pace_lg — the tempo the two teams are expected to play at,
        # which is f_pace with one league division undone.
        expected_pace = team_pace * opp_pace / league_pace

    return factors, {
        "team_pace": team_pace,
        "opp_pace": opp_pace,
        "opp_def_rtg": opp_drtg,
        "league_pace": league_pace,
        "league_def_rtg": league_drtg,
        "expected_pace": expected_pace,
    }


def _team_form(
    ctx: ResolveContext,
    team_id: Optional[int],
    season: str,
    season_type: str,
    cutoff: Optional[date],
) -> tuple[Optional[float], Optional[float], int]:
    """``(pace, def_rtg, games)`` from one team's games **strictly before** ``cutoff``."""
    if team_id is None:
        return None, None, 0
    lines = [
        line
        for line in q.team_games(ctx, int(team_id), season, season_type)
        if cutoff is None or line.game.game_date < cutoff
    ]
    pace = _mean([line.row.pace for line in lines])
    drtg = _mean([line.row.def_rtg for line in lines])
    return pace, drtg, len(lines)


# --------------------------------------------------------------------------- history


def _player_history(
    ctx: ResolveContext, player_id: int, cutoff: Optional[date]
) -> list[HistoryRow]:
    """Every competitive appearance this player has made **before** ``cutoff``, oldest first.

    One query per player per request, memoised whole and filtered by date afterwards, so the
    season totals, the career totals, the EWMA inputs and the dispersion residuals are all
    slices of the same list rather than four chances to write the cutoff differently.
    """

    def _load() -> list[HistoryRow]:
        columns = [
            Game.game_id,
            Game.game_date,
            Game.season,
            Game.season_type,
            PlayerGameBasic.team_id,
            PlayerGameBasic.minutes,
            *[getattr(PlayerGameBasic, key) for key in C.PROJECTABLE_STATS],
        ]
        rows = ctx.session.execute(
            select(*columns)
            .join(Game, Game.game_id == PlayerGameBasic.game_id)
            .where(PlayerGameBasic.player_id == int(player_id))
            .where(Game.season_type.in_(COMPETITIVE_SEASON_TYPES))
            .order_by(Game.game_date, Game.game_id)
        ).all()
        return [
            HistoryRow(
                game_id=row[0],
                game_date=row[1],
                season=row[2],
                season_type=row[3],
                team_id=row[4],
                minutes=row[5],
                values=dict(zip(C.PROJECTABLE_STATS, row[6:])),
            )
            for row in rows
        ]

    everything = ctx.memo(("projection_history", int(player_id)), _load)
    if cutoff is None:
        return list(everything)
    # THE cutoff (see "the pre-tip-off guarantee" above): strictly before the target game's
    # own date, so neither that game nor anything else played the same day can enter.
    return [row for row in everything if row.game_date < cutoff]


def _totals(
    rows: Iterable[HistoryRow], stat_key: str
) -> tuple[float, float, int]:
    """``(stat total, minutes of exposure, games)`` for one statistic.

    Minutes are accumulated only from games where the statistic itself was recorded, so the
    denominator of a rate is the exposure that actually produced the numerator.
    """
    total = 0.0
    minutes = 0.0
    games = 0
    for row in rows:
        value = row.values.get(stat_key)
        if value is None:
            continue
        total += float(value)
        minutes += float(row.minutes or 0.0)
        games += 1
    return total, minutes, games


def _stat_input(
    ctx: ResolveContext,
    stat_key: str,
    season: str,
    season_type: str,
    cutoff: Optional[date],
    season_rows: Sequence[HistoryRow],
    form_rows: Sequence[HistoryRow],
    history: Sequence[HistoryRow],
    notes: list[str],
) -> P.StatInput:
    """Everything :func:`nbastats.projection.project_box_score` needs for one statistic."""
    season_total, season_minutes, season_games = _totals(season_rows, stat_key)
    career_total, career_minutes, _career_games = _totals(history, stat_key)

    halflife = C.halflife_games(stat_key)
    form_values = [row.values.get(stat_key) for row in form_rows]
    form_minutes = [row.minutes for row in form_rows]
    has_form = any(v is not None for v in form_values) and any(
        (m or 0.0) > 0.0 for m in form_minutes
    )

    rate = P.regressed_rate(
        stat_key,
        season_total=season_total,
        season_minutes=season_minutes,
        career_total=career_total,
        career_minutes=career_minutes,
        form_stat_ewma=P.ewma(form_values, halflife) if has_form else None,
        form_minutes_ewma=P.ewma(form_minutes, halflife) if has_form else None,
    )

    alpha, pool_rows = _pooled_alpha(ctx, stat_key, season, season_type, cutoff)
    if pool_rows < DISPERSION_POOL_MIN_ROWS:
        note = (
            f"The pooled dispersion for {stat_key.upper()} is fitted on only {pool_rows} "
            "league game lines before this game; the interval is correspondingly rough."
        )
        if note not in notes:
            notes.append(note)
    multiplier = _dispersion_multiplier(history, stat_key, alpha)

    return P.StatInput(
        stat_key=stat_key,
        rate=rate,
        dispersion_alpha=alpha,
        dispersion_multiplier=multiplier,
        season_average=(season_total / season_games) if season_games else None,
    )


# --------------------------------------------------------------------------- dispersion
#
# The per-player multiplier of eq. 10.5 only means anything against a POOLED alpha: fitting
# alpha on the same player's residuals and then measuring those residuals against it would
# return ~1.0 by construction and say nothing. So alpha is fitted league-wide on
# pre-tip-off lines, and the player's z^2 is measured against it — which is what
# ``run_dist2.py`` does with its training seasons.
#
# Both sides build mu the same way: minutes actually played times that player's padding-shrunk
# rate over the same window. Consistency between the two matters more than sophistication —
# any common bias in mu cancels in the ratio, which is all the multiplier is.


def _dispersion_pool(
    ctx: ResolveContext, season: str, season_type: str, cutoff: Optional[date]
) -> list[PoolRow]:
    """The league sample the pooled ``alpha`` is fitted from: recent pre-tip-off lines."""

    def _load() -> list[PoolRow]:
        columns = [
            PlayerGameBasic.player_id,
            PlayerGameBasic.minutes,
            *[getattr(PlayerGameBasic, key) for key in C.PROJECTABLE_STATS],
        ]
        statement = (
            select(*columns)
            .join(Game, Game.game_id == PlayerGameBasic.game_id)
            .where(Game.season == season)
            .where(Game.season_type == season_type)
            .where(PlayerGameBasic.minutes >= DISPERSION_MIN_MINUTES)
        )
        if cutoff is not None:
            # The same strict cutoff the player's own history uses.
            statement = statement.where(Game.game_date < cutoff)
        statement = statement.order_by(Game.game_date.desc(), Game.game_id.desc()).limit(
            DISPERSION_POOL_MAX_ROWS
        )
        return [
            PoolRow(
                player_id=int(row[0]),
                minutes=float(row[1] or 0.0),
                values=dict(zip(C.PROJECTABLE_STATS, row[2:])),
            )
            for row in ctx.session.execute(statement).all()
        ]

    return ctx.memo(("projection_dispersion_pool", season, season_type, cutoff), _load)


def _padding_rates(
    rows: Iterable[Any], stat_key: str, key: Callable[[Any], Any]
) -> dict[Any, float]:
    """``{group: padding-estimator rate}`` — ``(sum stat + k*mu) / (sum minutes + k)``.

    The same estimator ``regressed_rate`` uses, applied group by group. ``key`` chooses the
    grouping: the player for the league pool, the season for one player's own history.
    """
    consts = C.stat_constants(stat_key)
    totals: dict[Any, list[float]] = {}
    for row in rows:
        value = row.values.get(stat_key)
        minutes = float(row.minutes or 0.0)
        if value is None or minutes < DISPERSION_MIN_MINUTES:
            continue
        entry = totals.setdefault(key(row), [0.0, 0.0])
        entry[0] += float(value)
        entry[1] += minutes
    return {
        group: (total + consts.k_minutes * consts.league_rate_per_min)
        / (minutes + consts.k_minutes)
        for group, (total, minutes) in totals.items()
    }


def _observed_and_predicted(
    rows: Sequence[Any], stat_key: str, key: Callable[[Any], Any]
) -> tuple[list[float], list[float]]:
    """Paired ``(y, mu)`` for a set of game lines, ``mu = minutes * rate(group)``."""
    rates = _padding_rates(rows, stat_key, key)
    observed: list[float] = []
    predicted: list[float] = []
    for row in rows:
        value = row.values.get(stat_key)
        minutes = float(row.minutes or 0.0)
        if value is None or minutes < DISPERSION_MIN_MINUTES:
            continue
        rate = rates.get(key(row))
        if rate is None or rate <= 0.0:
            continue
        observed.append(float(value))
        predicted.append(minutes * rate)
    return observed, predicted


def _pooled_alpha(
    ctx: ResolveContext,
    stat_key: str,
    season: str,
    season_type: str,
    cutoff: Optional[date],
) -> tuple[float, int]:
    """``(alpha, sample size)`` in ``Var = mu + alpha*mu^2``, fitted league-wide."""

    def _fit() -> tuple[float, int]:
        pool = _dispersion_pool(ctx, season, season_type, cutoff)
        observed, predicted = _observed_and_predicted(pool, stat_key, lambda row: row.player_id)
        return P.fit_dispersion(observed, predicted), len(observed)

    return ctx.memo(
        ("projection_alpha", stat_key, season, season_type, cutoff), _fit
    )


def _dispersion_multiplier(
    history: Sequence[HistoryRow], stat_key: str, alpha: float
) -> float:
    """``c_hat`` of eq. 10.5 for one player: his own volatility, shrunk toward 1.0 at 60 games.

    His residuals are measured season by season — the rate that predicts a 24-year-old's
    games is not the rate that predicts a 34-year-old's — so the multiplier measures
    volatility rather than aging. With no usable history it is exactly 1.0, which is the
    honest degradation and not a fallback.
    """
    observed, predicted = _observed_and_predicted(
        history, stat_key, lambda row: (row.season, row.season_type)
    )
    residuals = P.standardised_sq_residuals(observed, predicted, alpha)
    return P.shrunken_dispersion_multiplier(residuals, len(residuals))


# --------------------------------------------------------------------------- small helpers


def _mean(values: Iterable[Any]) -> Optional[float]:
    """Mean of the values that are actually there, or ``None`` when none of them are."""
    usable = [float(v) for v in values if v is not None]
    return sum(usable) / len(usable) if usable else None


def _round(value: Optional[float], places: int) -> Optional[float]:
    return None if value is None else round(float(value), places)


def _dedupe(values: Sequence[str]) -> list[str]:
    """Keep the first of each note. Two readers saying the same thing is noise, not emphasis."""
    return list(dict.fromkeys(values))
