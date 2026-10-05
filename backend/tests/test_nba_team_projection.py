"""The NBA team-score projection: the design's model, run forward, frozen before tip-off.

What is asserted, and how
-------------------------
**The model is the design's formulas.** Section 8.4 is transcribed here, literally and from the
equations, into :func:`transcribe`: a league level that blends last season's in, ratings regressed
toward it, attack and defence indices, the home advantage split half to each side, and an update
after every game of ``1 / (n + 10)`` times the miss. A ten-game league is projected by the module
and by the transcription and the two must agree to a part in a trillion, including the games two
teams play on the same day (projected from the same state, then applied together) and the games
before there is a league level at all (counted, never projected).

**Nothing from the future.** A game's projection built in a store that also holds every game that
followed it is identical to one built in a store that never did, and the same test shows it can
fail: change an earlier result and a later projection moves. Information is cut off the same way:
a status recorded after the cut-off changes nothing about a reconstruction.

**The frozen projection.** A ``locked`` row is written in the hour before the deadline (the tip-off,
or noon Eastern when none is recorded) and **never at or after it**: the function refuses, the write
refuses, the worker's job writes nothing, and the row's own ``computed_at`` is what is audited. A
projection that was never frozen is *rebuilt* from inputs strictly before the deadline and says so.
The inputs going back to a state the model has been in (a status entered and cleared) must not break
the one-row-per-inputs rule.

**Absences and what the model assumes.** The injury layer's inputs are wired as designed (blended
points and minutes, a replacement rate derived from the NBA's own scoring), a status in force moves
the projection and one that is stale or superseded does not, and every projection says how many
players it assumed would play only because it had no usable entry, and on what basis.

**Honest numbers.** There is no spread, and so no range, until one has been fitted from results; a
fit is never made from the demo league, never overwrites a number a person set, and moves to the
ledger once 300 frozen projections have results. The review separates the projections that were
frozen from those that were rebuilt, leaves toss-ups out of the winners called, and does its
arithmetic by hand here. Projections do not exist before 1996-97.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Sequence

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nbastats import config
from nbastats import db as db_module
from nbastats import nba_matchup
from nbastats.api.errors import ApiError
from nbastats.db import create_db_engine, init_db
from nbastats.models import Game, TeamProjectionLedger
from nbastats.nba_intel import settings as intel_settings
from nbastats.nba_intel import status as intel_status
from nbastats.nba_intel import store as intel_store
from nbastats.nba_intel.models import NbaIntelStatus, NbaIntelTeamReport
from nbastats.nba_matchup import ledger, projection, queries
from nbastats.shared.injury_layer import InjurySettings, PlayerInput, apply_injury_layer
from nbastats.shared.team_projection import Z80
from tests.conftest import SEED_AS_OF
from tests.test_nba_matchup import (  # noqa: F401 - fixtures used by name below
    NOW,
    SEASON,
    LeagueBuilder,
    Store,
    _fresh_memo,
    at,
    close,
    store,
)

UTC_NAIVE = datetime  # the store's convention: naive UTC


def naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None)


# =========================================================================== the transcription


def transcribe(
    rows: Sequence[tuple[str, str, int, int, int, int]],
    *,
    prior: tuple[float, dict[int, tuple[float, float]]] | None = None,
    offset: float = 10.0,
    regression: float = 0.3,
    weight: float = 150.0,
    home: float = 2.5,
) -> tuple[
    dict[str, tuple[float, float]], list[tuple[float, float, float]], dict[int, list[float]]
]:
    """Section 8.4 of the design, from the equations, for regulation-length games.

    ``rows`` are ``(date, game_id, home, away, home_pts, away_pts)``. ``prior`` is last season's
    ``(league level, {team: (points for, points against)})`` for the teams that played enough.
    Returns the projection made before each game that had a league level to project from, the
    residuals ``(home, away, margin)`` of those, and each team's ``[adj_pf, adj_pa, games]``.

    * league level ``L = (n * L_cur + W * L_prev) / (n + W)``: this season's mean points per
      team-game blended with last season's; just one of the two when the other does not exist
    * rating ``= L + (prior - L) * (1 - r) + adj`` for points for and against, the prior restated at
      the current level (``prior * L / L_prev``) and equal to ``L`` for a team with none
    * attack ``A = pf / L``, defence ``D = pa / L``
    * home ``= L * A_home * D_away + h / 2``, away ``= L * A_away * D_home - h / 2``
    * after a game, ``adj += w * (actual - projected)`` with ``w = 1 / (n + offset)``, ``n`` the
      team's game number this season, from the state before the day's games began
    """
    state: dict[int, list[float]] = {}
    league_sum = 0.0
    league_n = 0
    pre: dict[str, tuple[float, float]] = {}
    residuals: list[tuple[float, float, float]] = []
    days: dict[str, list[tuple[str, str, int, int, int, int]]] = {}
    for row in rows:
        days.setdefault(row[0], []).append(row)
    for day in sorted(days):
        current = league_sum / league_n if league_n else None
        if current is None:
            level = prior[0] if prior else None
        elif prior is None:
            level = current
        else:
            level = (league_n * current + weight * prior[0]) / (league_n + weight)
        before = {team: list(values) for team, values in state.items()}
        after = {team: list(values) for team, values in state.items()}
        for _, game_id, h, a, hp, ap in days[day]:
            league_sum += hp + ap
            league_n += 2
            if level is None:
                for team in (h, a):
                    entry = after.setdefault(team, [0.0, 0.0, 0])
                    entry[2] += 1
                continue

            def rate(team: int, which: int) -> float:
                base = level
                if prior is not None and team in prior[1]:
                    base = prior[1][team][which] * level / prior[0]
                adjustment = before.get(team, [0.0, 0.0, 0])[which]
                return level + (base - level) * (1 - regression) + adjustment

            pf_h, pa_h, pf_a, pa_a = rate(h, 0), rate(h, 1), rate(a, 0), rate(a, 1)
            proj_h = level * (pf_h / level) * (pa_a / level) + home / 2
            proj_a = level * (pf_a / level) * (pa_h / level) - home / 2
            pre[game_id] = (proj_h, proj_a)
            residuals.append((hp - proj_h, ap - proj_a, (hp - ap) - (proj_h - proj_a)))
            for team, scored, allowed, p_for, p_against in (
                (h, hp, ap, proj_h, proj_a),
                (a, ap, hp, proj_a, proj_h),
            ):
                games = before.get(team, [0.0, 0.0, 0])[2]
                w = 1.0 / ((games + 1) + offset)
                old = before.get(team, [0.0, 0.0, 0])
                after[team] = [
                    old[0] + w * (scored - p_for),
                    old[1] + w * (allowed - p_against),
                    games + 1,
                ]
        state = after
    return pre, residuals, state


def prior_from(rows: Sequence[tuple[str, str, int, int, int, int]], minimum: int = 10):
    """Last season's league level and the ``(pf, pa)`` of each team with ``minimum`` games."""
    scored: dict[int, list[tuple[int, int]]] = {}
    total = 0.0
    n = 0
    for _, _, h, a, hp, ap in rows:
        scored.setdefault(h, []).append((hp, ap))
        scored.setdefault(a, []).append((ap, hp))
        total += hp + ap
        n += 2
    teams = {
        team: (
            sum(p for p, _ in games) / len(games),
            sum(q for _, q in games) / len(games),
        )
        for team, games in scored.items()
        if len(games) >= minimum
    }
    return total / n, teams


TEN_GAMES: list[tuple[str, str, int, int, int, int]] = [
    ("2025-11-01", "a1", 1, 2, 101, 94),
    ("2025-11-01", "a2", 3, 4, 88, 97),
    ("2025-11-03", "a3", 2, 3, 110, 102),
    ("2025-11-03", "a4", 4, 1, 99, 105),
    ("2025-11-05", "a5", 1, 3, 96, 91),
    ("2025-11-05", "a6", 2, 4, 107, 100),
    ("2025-11-07", "a7", 3, 1, 93, 108),
    ("2025-11-07", "a8", 4, 2, 90, 96),
    ("2025-11-09", "a9", 1, 2, 120, 112),
    ("2025-11-09", "a10", 3, 4, 101, 99),
]


def add_rows(
    league: LeagueBuilder, rows: Sequence[tuple[str, str, int, int, int, int]], **kw: Any
) -> None:
    for day, game_id, h, a, hp, ap in rows:
        league.game(game_id, day, h, a, hp, ap, **kw)


def four_teams(league: LeagueBuilder) -> None:
    for team in (1, 2, 3, 4):
        league.team(team)


def model_of(store: Store, now: datetime = NOW, season: str = SEASON) -> tuple[Any, Any]:
    ctx = queries.build_context(store.session, season, now=now)
    return ctx, projection.get_model(ctx)


# =========================================================================== the formulas


def test_ten_games_are_projected_exactly_as_the_designs_equations_say(store: Store) -> None:
    four_teams(store.league)
    add_rows(store.league, TEN_GAMES)
    store.league.game("s1", "2025-11-12", 1, 3)  # not played: projected from the final state
    store.league.commit()
    ctx, model = model_of(store)
    expected, residuals, _ = transcribe([*TEN_GAMES, ("2025-11-12", "s1", 1, 3, 100, 100)])
    _, _, state = transcribe(TEN_GAMES)  # the state s1 is projected from (s1 itself is unplayed)

    # The first day has no league level to project from: counted, never projected.
    assert "a1" not in model.pre and "a2" not in model.pre
    assert set(model.pre) == {gid for _, gid, *_ in TEN_GAMES} - {"a1", "a2"}
    for game_id, (home, away) in expected.items():
        if game_id == "s1":
            continue
        got = model.pre[game_id]
        assert got.home_points == pytest.approx(home, rel=0, abs=1e-9), game_id
        assert got.away_points == pytest.approx(away, rel=0, abs=1e-9), game_id
    assert len(model.residuals) == 8 and len(residuals) == 9  # the ninth is the unplayed s1's
    # (The two lists agree as sets: within one day the module folds games in game-id order.)
    for got, want in zip(sorted(model.residuals), sorted(residuals[:8])):
        assert got == pytest.approx(want, abs=1e-9)

    final = model.final
    for team in (1, 2, 3, 4):
        assert final.teams[team].adj_pf == pytest.approx(state[team][0], abs=1e-9)
        assert final.teams[team].adj_pa == pytest.approx(state[team][1], abs=1e-9)
        assert final.teams[team].games == state[team][2] == 5

    # And the public payload for the unplayed game is the same arithmetic, to the point.
    detail = nba_matchup.game_projection(store.session, "s1", now=NOW)
    current = detail["current"]
    home, away = expected["s1"]
    assert current["home"]["projectedPoints"] == pytest.approx(home, abs=1e-9)
    assert current["away"]["projectedPoints"] == pytest.approx(away, abs=1e-9)
    assert current["margin"] == pytest.approx(home - away, abs=1e-9)
    assert current["combinedPoints"] == pytest.approx(home + away, abs=1e-9)
    assert current["homeAdvantagePoints"] == 2.5 and current["venueAssumed"] is True
    assert current["isTossUp"] is (abs(home - away) < 0.5)
    level = model.league_level(model.final)
    assert level == pytest.approx(sum(hp + ap for *_, hp, ap in TEN_GAMES) / 20, abs=1e-12)
    # Index form: attack = pf / L, so home = L * A * D + h/2 and the sides differ by the advantage.
    assert current["home"]["attackIndex"] == pytest.approx((level + state[1][0]) / level, abs=1e-9)
    assert current["away"]["defenceIndex"] == pytest.approx((level + state[3][1]) / level, abs=1e-9)
    assert current["availability"] == "estimated"
    assert current["model"]["kind"] == "latest" and current["model"]["version"] == "nba-1"


def test_games_on_the_same_day_are_projected_from_the_same_state(store: Store) -> None:
    """If the fold applied a day's games one at a time, a2 would be projected with a1's result
    already in the ratings; the transcription applies the day together, and so must the module."""
    four_teams(store.league)
    # A third team plays on day two against a team that played on day one, and so does a fourth.
    add_rows(store.league, TEN_GAMES[:4])
    store.league.commit()
    ctx, model = model_of(store)
    expected, _, _ = transcribe(TEN_GAMES[:4])
    for game_id in ("a3", "a4"):
        assert model.pre[game_id].home_points == pytest.approx(expected[game_id][0], abs=1e-9)
    # Same instant, same snapshot: the state after day one serves both day-two games.
    assert model.snapshot_for(ctx.start_of(ctx.game("a3"))) is model.snapshot_for(
        ctx.start_of(ctx.game("a4"))
    )
    assert model.snapshot_for(ctx.start_of(ctx.game("a1"))) is model.snapshots[0]


def _leak_store(store: Store, first_home_pts: int) -> tuple[Any, Any]:
    """Day one, then two games on day two that tip 30 minutes apart (19:00 and 19:30 ET)."""
    four_teams(store.league)
    add_rows(store.league, TEN_GAMES[:2])
    store.league.game("e1", "2025-11-03", 1, 3, first_home_pts, 100, tipoff=at("2025-11-04", 0, 0))
    store.league.game("e2", "2025-11-03", 2, 4, 104, 101, tipoff=at("2025-11-04", 0, 30))
    store.league.commit()
    queries.clear_memo()
    return model_of(store)


@pytest.mark.parametrize("first_home_pts", [105, 145])
def test_a_game_that_tipped_earlier_the_same_day_never_informs_a_later_one(
    store: Store, first_home_pts: int
) -> None:
    """A game that tips at 19:00 is not final by a 19:30 tip-off: its result must not reach the
    later game's ratings, league level, squad or team form, whatever its score."""
    ctx, model = _leak_store(store, first_home_pts)
    e1, e2 = ctx.game("e1"), ctx.game("e2")
    assert ctx.start_of(e1) < ctx.start_of(e2)  # the tip-offs are known and differ
    assert not ctx.result_known_by(e1, ctx.start_of(e2))
    assert model.snapshot_for(ctx.start_of(e2)) is model.snapshot_for(ctx.start_of(e1))
    snap = model.snapshot_for(ctx.start_of(e2))
    assert snap.league_n == 4  # day one's two games only
    scope = queries.load_team_games(ctx, before=ctx.start_of(e2))
    assert {g.game_id for g in scope} == {"a1", "a2"}
    # the same projection whatever e1's score: compared against a fixed transcription
    expected, _, _ = transcribe([*TEN_GAMES[:2], ("2025-11-03", "e2", 2, 4, 104, 101)])
    assert model.pre["e2"].home_points == pytest.approx(expected["e2"][0], abs=1e-9)
    assert model.pre["e2"].away_points == pytest.approx(expected["e2"][1], abs=1e-9)


def test_last_seasons_scoring_is_the_prior_and_the_league_level_slides(store: Store) -> None:
    """Last season sets the starting ratings (regressed toward the league), a team with fewer than
    ten games has none, and the level moves from last season's to this season's as games pile up:
    ``(n * L_cur + 150 * L_prev) / (n + 150)``."""
    four_teams(store.league)
    store.league.team(5)
    previous: list[tuple[str, str, int, int, int, int]] = []
    pairs = [(1, 2), (3, 4), (1, 3), (2, 4), (1, 4), (2, 3)]
    n = 0
    for cycle in range(2):
        for a, b in pairs:
            for home, away in ((a, b), (b, a)):
                n += 1
                previous.append(
                    (
                        (date(2024, 11, 1) + timedelta(days=n)).isoformat(),
                        f"p{n:02d}",
                        home,
                        away,
                        96 + (n * 5) % 17 + (3 if home == 1 else 0),
                        92 + (n * 3) % 13 + (4 if away == 2 else 0),
                    )
                )
    previous.append(("2025-03-01", "p98", 5, 1, 90, 99))  # team 5 played once: no prior
    add_rows(store.league, previous, season="2024-25")
    this_season = [
        ("2025-11-01", "c1", 1, 2, 104, 99),
        ("2025-11-02", "c2", 3, 4, 95, 101),
    ]
    add_rows(store.league, this_season)
    store.league.game("s1", "2025-11-10", 5, 1)  # team 5 has no prior; team 1 has
    store.league.game("s2", "2025-11-10", 3, 2)
    store.league.commit()

    prior = prior_from(previous)
    assert set(prior[1]) == {1, 2, 3, 4}  # each of the four played 12 games; team 5 played one
    expected, _, state = transcribe(
        [*this_season, ("2025-11-10", "s1", 5, 1, 0, 0), ("2025-11-10", "s2", 3, 2, 0, 0)],
        prior=prior,
    )
    ctx, model = model_of(store)
    detail = nba_matchup.game_projection(store.session, "s2", now=NOW)["current"]
    assert detail["home"]["projectedPoints"] == pytest.approx(expected["s2"][0], abs=1e-9)
    assert detail["away"]["projectedPoints"] == pytest.approx(expected["s2"][1], abs=1e-9)
    odd = nba_matchup.game_projection(store.session, "s1", now=NOW)["current"]
    assert odd["home"]["projectedPoints"] == pytest.approx(expected["s1"][0], abs=1e-9)
    assert odd["away"]["projectedPoints"] == pytest.approx(expected["s1"][1], abs=1e-9)

    # With no game this season yet the level is last season's, exactly; after four team-games
    # it is the blend, which still leans almost entirely on last season.
    assert model.league_level(model.snapshots[0]) == pytest.approx(prior[0], abs=1e-12)
    current_mean = (104 + 99 + 95 + 101) / 4
    blended = (4 * current_mean + 150 * prior[0]) / (4 + 150)
    assert model.league_level(model.final) == pytest.approx(blended, abs=1e-12)
    assert abs(blended - prior[0]) < abs(current_mean - prior[0]) * 0.03 + 1e-9
    # The design's rating for a team with a prior at the start of a season, from its own formula:
    # L + (PPG_prev - L) * (1 - r), with last season's level equal to the level in force.
    start = model.snapshots[0]
    level = model.league_level(start)
    pf, pa = model.ratings(2, start, level)
    assert pf == pytest.approx(level + (prior[1][2][0] - level) * 0.7, abs=1e-9)
    assert pa == pytest.approx(level + (prior[1][2][1] - level) * 0.7, abs=1e-9)
    assert model.ratings(5, start, level) == (level, level)  # no prior season: starts at L


def test_the_home_advantage_is_a_default_until_300_games_then_the_mean_margin(
    store: Store,
) -> None:
    four_teams(store.league)
    margins: list[int] = []
    for n in range(299):
        home, away = (1 + n % 4), (1 + (n + 1 + n // 4) % 4)
        if home == away:
            away = 1 + home % 4
        margin = 1 + (n * 7) % 9  # home wins by 1 to 9
        margins.append(margin)
        store.league.game(
            f"h{n:03d}",
            (date(2025, 10, 1) + timedelta(days=n // 3)).isoformat(),
            home,
            away,
            100 + margin,
            100,
        )
    store.league.commit()
    ctx, model = model_of(store, now=at("2026-12-01"))
    assert model.home_advantage(model.final) == (2.5, "default")
    store.league.game("h299", "2026-08-01", 1, 2, 110, 100)
    margins.append(10)
    store.league.commit()
    queries.clear_memo()
    ctx, model = model_of(store, now=at("2026-12-01"))
    points, basis = model.home_advantage(model.final)
    assert basis == "derived" and points == pytest.approx(sum(margins) / 300, abs=1e-12)
    # A value somebody set by hand is used as set, whatever the games say.
    intel_settings.set_setting(store.session, "homeAdvantagePoints", 3.25)
    store.session.commit()
    queries.clear_memo()
    ctx, model = model_of(store, now=at("2026-12-01"))
    assert model.home_advantage(model.final) == (3.25, "setting")


# =========================================================================== no future leakage


def test_a_games_projection_does_not_depend_on_what_happened_after_it(tmp_path: Path) -> None:
    def build(name: str, rows: Sequence[Any], future: Sequence[Any] = ()) -> dict[str, Any]:
        engine = create_db_engine(f"sqlite:///{tmp_path / name}")
        init_db(engine)
        with Session(engine, future=True) as session:
            league = LeagueBuilder(session)
            four_teams(league)
            add_rows(league, rows)
            add_rows(league, future)
            league.commit()
            queries.clear_memo()
            ctx = queries.build_context(session, SEASON, now=at("2026-02-01"))
            model = projection.get_model(ctx)
            out = {gid: (p.home_points, p.away_points) for gid, p in model.pre.items()}
            out["a9-reconstructed"] = projection_scores(model, ctx, "a9")
        engine.dispose()
        return out

    def projection_scores(model: Any, ctx: Any, game_id: str) -> tuple[float, float]:
        built = model.reconstruct(ctx, ctx.game(game_id))
        return built.match.home_points, built.match.away_points

    upto = TEN_GAMES[:8]  # a1..a8; a9 and a10 are what follow
    plain = build("plain.db", TEN_GAMES)
    blowout = build(
        "blowout.db",
        TEN_GAMES[:8]
        + [("2025-11-09", "a9", 1, 2, 120, 112), ("2025-11-09", "a10", 3, 4, 101, 99)],
        future=[("2025-11-20", "z1", 1, 2, 160, 60), ("2025-11-21", "z2", 3, 4, 40, 150)],
    )
    for game_id, values in plain.items():
        assert blowout[game_id] == pytest.approx(values, abs=1e-12), game_id
    # The same, with the *later* games of the same season simply absent from the store.
    truncated = build("truncated.db", upto + [("2025-11-09", "a9", 1, 2, 120, 112)])
    for game_id in ("a3", "a4", "a5", "a6", "a7", "a8", "a9"):
        assert truncated[game_id] == pytest.approx(plain[game_id], abs=1e-12), game_id
    # ... and the test can fail: change an *earlier* result and a later projection moves.
    changed_rows = [
        ("2025-11-03", "a3", 2, 3, 150, 60) if row[1] == "a3" else row for row in TEN_GAMES
    ]
    moved = build("moved.db", changed_rows)
    assert moved["a3"] == pytest.approx(plain["a3"], abs=1e-12)  # a3 itself is projected before
    assert moved["a5"] != pytest.approx(plain["a5"], abs=1e-6)


def test_a_status_recorded_after_the_cut_off_changes_nothing_about_a_reconstruction(
    store: Store,
) -> None:
    league = squad_league(store)
    league.game("g7", "2025-11-13", 1, 2)  # tips at noon Eastern (17:00Z) as far as we know
    league.commit()
    deadline = at("2025-11-13", 17, 0)
    ctx = queries.build_context(store.session, SEASON, now=at("2025-11-14", 12))
    model = projection.get_model(ctx)
    game = ctx.game("g7")
    clean = model.reconstruct(ctx, game)

    late = intel_store.insert_snapshot(
        store.session,
        source_kind="leagueReport",
        fetched_at=naive(deadline + timedelta(hours=1)),
        parse_status="ok",
        row_count=1,
        slot_at_utc=naive(deadline + timedelta(minutes=59)),
    )
    add_status(
        store.session, late, team=1, player=101, game_id="g7", status="out",
        published=deadline + timedelta(minutes=59), recorded=deadline + timedelta(hours=1),
    )  # fmt: skip
    store.session.commit()
    queries.clear_memo()
    ctx = queries.build_context(store.session, SEASON, now=at("2025-11-14", 12))
    after = projection.get_model(ctx).reconstruct(ctx, ctx.game("g7"))
    assert after.match.home_points == clean.match.home_points
    assert after.home.availability_factor == clean.home.availability_factor == 1.0
    assert after.as_of == deadline - timedelta(seconds=1)  # exactly what was knowable

    early = intel_store.insert_snapshot(
        store.session,
        source_kind="leagueReport",
        fetched_at=naive(deadline - timedelta(hours=2)),
        parse_status="ok",
        row_count=1,
        slot_at_utc=naive(deadline - timedelta(hours=2, minutes=1)),
    )
    add_status(
        store.session, early, team=1, player=101, game_id="g7", status="out",
        published=deadline - timedelta(hours=2, minutes=1),
        recorded=deadline - timedelta(hours=2),
    )  # fmt: skip
    store.session.commit()
    queries.clear_memo()
    ctx = queries.build_context(store.session, SEASON, now=at("2025-11-14", 12))
    known = projection.get_model(ctx).reconstruct(ctx, ctx.game("g7"))
    assert known.home.availability_factor < 1.0
    assert known.match.home_points < clean.match.home_points


# =========================================================================== the injury layer


def squad_league(store: Store) -> LeagueBuilder:
    """Three teams, six final games, and a squad for each side with box-score lines: team 1 has
    a star (29 points in 35 minutes, on average), a starter (18 in 30) and a rotation player
    (10 in 24); teams 2 and 3 have two players each."""
    league = store.league
    for team in (1, 2, 3):
        league.team(team)
    for pid in (101, 102, 103, 201, 202, 301, 302):
        league.player(pid)
    lines = {
        1: [(101, (36, 34, 38, 32), (30, 28, 32, 26)), (102, (30,) * 4, (18, 20, 16, 18)),
            (103, (24,) * 4, (10, 8, 12, 10))],
        2: [(201, (33,) * 4, (22, 20, 24, 22)), (202, (20,) * 4, (8, 10, 6, 8))],
        3: [(301, (31,) * 4, (19, 21, 17, 19)), (302, (22,) * 4, (9, 11, 7, 9))],
    }  # fmt: skip
    schedule = [
        ("g1", "2025-11-01", 1, 2, 100, 90),
        ("g2", "2025-11-03", 2, 3, 110, 105),
        ("g3", "2025-11-05", 3, 1, 98, 102),
        ("g4", "2025-11-07", 1, 3, 120, 100),
        ("g5", "2025-11-09", 2, 1, 95, 99),
        ("g6", "2025-11-11", 3, 2, 101, 107),
    ]
    played = {1: 0, 2: 0, 3: 0}
    for game_id, day, home, away, hp, ap in schedule:
        home_lines, away_lines = [], []
        for team, bucket_ in ((home, home_lines), (away, away_lines)):
            k = played[team]
            for pid, minutes, points in lines[team]:
                bucket_.append((pid, float(minutes[k % len(minutes)]), points[k % len(points)]))
            played[team] += 1
        league.game(game_id, day, home, away, hp, ap, home_lines=home_lines, away_lines=away_lines)
    league.commit()
    return league


def make_snapshot(
    session: Session, slot: datetime, *, fetched: datetime | None = None, parse_status: str = "ok"
) -> Any:
    return intel_store.insert_snapshot(
        session,
        source_kind="leagueReport",
        fetched_at=naive(fetched or slot + timedelta(minutes=1)),
        parse_status=parse_status,
        row_count=0,
        url="https://example.org/report.pdf",
        slot_at_utc=naive(slot),
        report_as_of_utc=naive(slot),
    )


def add_status(
    session: Session,
    snapshot: Any,
    *,
    team: int,
    player: int | None,
    game_id: str | None,
    status: str,
    published: datetime,
    recorded: datetime | None = None,
    expected_return_text: str | None = None,
    expected_return_round_from: int | None = None,
    expected_return_round_to: int | None = None,
    expected_return_date: date | None = None,
    name: str | None = None,
) -> NbaIntelStatus:
    row = NbaIntelStatus(
        team_id=team,
        player_id=player,
        player_name_raw=name or f"Surname, {player}",
        game_id=game_id,
        status=status,
        status_raw=status.title(),
        reason_category="injury",
        reason_text="Injury/Illness - Left Knee; Sprain",
        expected_return_text=expected_return_text,
        expected_return_round_from=expected_return_round_from,
        expected_return_round_to=expected_return_round_to,
        expected_return_date=expected_return_date,
        source_kind="leagueReport",
        source_label=intel_status.REPORT_SOURCE_LABEL,
        source_url="https://example.org/report.pdf",
        source_published_at=naive(published),
        as_of=naive(published),
        recorded_at=naive(recorded or published + timedelta(minutes=1)),
        snapshot_id=snapshot.snapshot_id,
    )
    session.add(row)
    session.flush()
    return row


def team_report(session: Session, snapshot: Any, team: int, game_id: str, state: str) -> None:
    session.add(
        NbaIntelTeamReport(
            snapshot_id=snapshot.snapshot_id, team_id=team, game_id=game_id, state=state
        )
    )
    session.flush()


def test_a_star_being_out_moves_the_projection_exactly_as_the_injury_layer_says(
    store: Store,
) -> None:
    league = squad_league(store)
    league.game("g7", "2025-11-13", 1, 2)
    league.commit()
    slot = at("2025-11-12", 15, 0)
    snap = make_snapshot(store.session, slot)
    add_status(store.session, snap, team=1, player=101, game_id="g7", status="out", published=slot)
    team_report(store.session, snap, 1, "g7", "submitted")
    team_report(store.session, snap, 2, "g7", "submitted")
    store.session.commit()
    queries.clear_memo()

    ctx, model = model_of(store, now=at("2025-11-12", 16, 0))
    start = model.snapshot_for(ctx.start_of(ctx.game("g7")))
    level = model.league_level(start)
    settings = InjurySettings(
        replacement_per_minute=0.52 * level / 240, absorb_share=0.6, boost_cap=1.35,
        cap_policy="consistent", rotation_share=0.5,
    )  # fmt: skip
    # Points and minutes are this season's per-game means (there is no previous season to blend).
    expected = apply_injury_layer(
        [
            PlayerInput(101, minutes=35.0, points=29.0, chance=0.0),
            PlayerInput(102, minutes=30.0, points=18.0, chance=1.0),
            PlayerInput(103, minutes=24.0, points=10.0, chance=1.0),
        ],
        settings,
    )
    assert 0.0 < expected.availability_factor < 1.0
    result = projection.get_model(ctx).project(ctx, ctx.game("g7"), as_of=ctx.now, kind="latest")
    assert result.home.availability_factor == pytest.approx(expected.availability_factor, abs=1e-12)
    assert result.home.injury.absorbed_points == pytest.approx(expected.absorbed_points, abs=1e-12)
    assert result.away.availability_factor == 1.0  # nobody on team 2's report

    payload = nba_matchup.game_projection(store.session, "g7", now=at("2025-11-12", 16, 0))[
        "current"
    ]
    home = payload["home"]
    assert home["availabilityEffect"] < 0 < home["fullStrengthPoints"]
    assert home["projectedPoints"] == pytest.approx(
        home["fullStrengthPoints"] + home["availabilityEffect"], abs=1e-12
    )
    assert home["attackIndexAfterAvailability"] == pytest.approx(
        home["attackIndex"] * expected.availability_factor, abs=1e-12
    )
    assert payload["away"]["availabilityEffect"] == 0.0
    assert payload["combinedAvailabilityEffect"] == pytest.approx(
        home["availabilityEffect"], abs=1e-12
    )
    assert home["capBinding"] is expected.cap_binding
    # The absence is listed with its season-average numbers and its source, aged from the source.
    [absence] = home["keyAbsences"]
    assert absence["player"]["id"] == "101" and absence["status"] == "out"
    assert absence["chanceOfPlaying"] == 0.0
    assert absence["expectedPointsLost"] == pytest.approx(29.0)
    assert absence["expectedMinutesLost"] == pytest.approx(35.0)
    assert absence["inForce"] is True
    assert (
        absence["source"]["kind"] == "leagueReport"
        and absence["source"]["snapshotId"] == snap.snapshot_id
    )
    # Defence is never changed by absences, and the payload says so.
    assert any("Defence indices are not changed by absences" in n for n in payload["notes"])
    full = nba_matchup.game_projection(store.session, "g7", now=at("2025-11-12", 16, 0))["current"]
    assert full["away"]["defenceIndex"] == payload["away"]["defenceIndex"]


def test_every_projection_says_who_it_assumed_would_play_and_on_what_basis(
    store: Store,
) -> None:
    league = squad_league(store)
    league.game("g7", "2025-11-13", 1, 2)
    league.commit()
    now = at("2025-11-12", 16, 0)

    def assumptions() -> dict[str, Any]:
        queries.clear_memo()
        return nba_matchup.game_projection(store.session, "g7", now=now)["current"]["assumptions"]

    # No report has ever been published: everyone is assumed to play, and the basis says why.
    none = assumptions()
    assert none["assumedAvailable"] == {
        "home": 3, "away": 2, "basis": "noReportPublished",
        "homeBasis": "noReportPublished", "awayBasis": "noReportPublished",
    }  # fmt: skip
    assert none["staleEntriesIgnored"] == 0

    slot = at("2025-11-12", 15, 0)
    snap = make_snapshot(store.session, slot)
    add_status(
        store.session, snap, team=1, player=101, game_id="g7", status="questionable", published=slot
    )
    team_report(store.session, snap, 1, "g7", "submitted")
    team_report(store.session, snap, 2, "g7", "notYetSubmitted")
    store.session.commit()
    mixed = assumptions()
    assert mixed["assumedAvailable"]["home"] == 2 and mixed["assumedAvailable"]["away"] == 2
    assert mixed["assumedAvailable"]["homeBasis"] == "notOnSubmittedReport"
    assert mixed["assumedAvailable"]["awayBasis"] == "teamReportPending"
    # The headline basis is the least informed of the two sides.
    assert mixed["assumedAvailable"]["basis"] == "teamReportPending"


def test_an_entry_that_no_longer_counts_is_set_aside_and_counted(store: Store) -> None:
    league = squad_league(store)
    league.game("g7", "2025-11-30", 1, 2)
    league.commit()
    now = at("2025-11-29", 16, 0)
    # (c) too old: fifteen days before the cut-off, not a long-term absence.
    old_slot = now - timedelta(days=15)
    old = make_snapshot(store.session, old_slot)
    add_status(
        store.session, old, team=1, player=102, game_id=None, status="out", published=old_slot
    )
    # (c) exempt: a long-term entry the same age stays in force.
    add_status(
        store.session, old, team=1, player=103, game_id=None, status="out",
        published=old_slot, expected_return_text="Out for the season (surgery)",
    )  # fmt: skip
    store.session.commit()
    queries.clear_memo()
    payload = nba_matchup.game_projection(store.session, "g7", now=now)["current"]
    assert payload["assumptions"]["staleEntriesIgnored"] == 1  # 102 only
    drivers = {a["player"]["id"] for a in payload["home"]["keyAbsences"]}
    assert drivers == {"103"}  # the long-term entry still drives; the old one does not
    [entry] = payload["home"]["keyAbsences"]
    assert entry["isStale"] is True  # a long-term entry is stale after seven days, in force or not

    # (b) superseded by the box score: a game after the entry in which the player played.
    league.game(
        "g6b", "2025-11-20", 1, 3, 101, 99,
        home_lines=[(102, 31.0, 17), (101, 35.0, 28)], away_lines=[(301, 30.0, 18)],
    )  # fmt: skip
    league.commit()
    fresh_slot = at("2025-11-18", 15, 0)
    snap = make_snapshot(store.session, fresh_slot)
    add_status(
        store.session, snap, team=1, player=101, game_id=None, status="out", published=fresh_slot
    )
    store.session.commit()
    queries.clear_memo()
    after = nba_matchup.game_projection(store.session, "g7", now=now)["current"]
    ids = {a["player"]["id"] for a in after["home"]["keyAbsences"]}
    assert "101" not in ids  # he played on the 20th, after the entry of the 18th
    assert after["assumptions"]["staleEntriesIgnored"] == 2  # 102 (old) and 101 (superseded)


def test_an_override_drives_the_projection_until_cleared_or_a_newer_report_arrives(
    store: Store,
) -> None:
    league = squad_league(store)
    league.game("g7", "2025-11-13", 1, 2)
    league.commit()
    now = at("2025-11-12", 16, 0)

    def home_points() -> float:
        queries.clear_memo()
        return nba_matchup.game_projection(store.session, "g7", now=now)["current"]["home"][
            "projectedPoints"
        ]

    clean = home_points()
    override = intel_status.add_override(
        store.session, player_id=101, status="out", team_id=1,
        now=naive(at("2025-11-12", 14, 0)),
    )  # fmt: skip
    store.session.commit()
    assert home_points() < clean
    intel_status.clear_override(
        store.session, override.override_id, now=naive(at("2025-11-12", 15, 0))
    )
    store.session.commit()
    assert home_points() == pytest.approx(clean, abs=1e-12)

    again = intel_status.add_override(
        store.session, player_id=101, status="out", team_id=1, now=naive(at("2025-11-12", 14, 0)),
    )  # fmt: skip
    store.session.commit()
    assert home_points() < clean
    # A league report that arrives after the override has spoken since: the override is obsolete.
    slot = at("2025-11-12", 15, 30)
    snap = make_snapshot(store.session, slot)
    team_report(store.session, snap, 1, "g7", "submitted")
    store.session.commit()
    assert home_points() == pytest.approx(clean, abs=1e-12)
    assert again.override_id is not None


def test_the_demo_league_ignores_statuses_it_should_never_have_been_given(
    store: Store,
) -> None:
    league = squad_league(store)
    league.game("g7", "2025-11-13", 1, 2)
    league.commit()
    now = at("2025-11-12", 16, 0)
    slot = at("2025-11-12", 15, 0)
    snap = make_snapshot(store.session, slot)
    add_status(store.session, snap, team=1, player=101, game_id="g7", status="out", published=slot)
    team_report(store.session, snap, 1, "g7", "submitted")
    store.session.commit()
    queries.clear_memo()
    real = nba_matchup.game_projection(store.session, "g7", now=now)["current"]
    assert real["home"]["availabilityEffect"] < 0
    store.session.query(Game).filter(Game.game_id == "g1").update({"data_source": "synthetic-demo"})
    store.session.commit()
    queries.clear_memo()
    demo = nba_matchup.game_projection(store.session, "g7", now=now)["current"]
    assert demo["home"]["availabilityEffect"] == 0.0
    assert demo["home"]["keyAbsences"] == []
    assert demo["assumptions"]["assumedAvailable"]["basis"] == "noReportPublished"
    assert demo["freshness"]["isDemo"] is True


# =========================================================================== the report


def report_league(store: Store) -> LeagueBuilder:
    """The squad league with two scheduled games on 13 November (no tip-off recorded): team 1
    hosts team 2 (``g7``) and team 3 hosts team 2 (``g8``)."""
    league = squad_league(store)
    league.game("g7", "2025-11-13", 1, 2)
    league.game("g8", "2025-11-13", 3, 2)
    league.commit()
    return league


SLOT = at("2025-11-12", 15, 0)  # 10:00 Eastern


def availability(store: Store, now: datetime, **query: Any) -> dict[str, Any]:
    queries.clear_memo()
    return nba_matchup.availability_report(store.session, now=now, **query)


def entries_of(body: dict[str, Any], team: int) -> list[dict[str, Any]]:
    [found] = [t for t in body["teams"] if t["team"]["teamId"] == team]
    return found["entries"]


def test_the_report_lists_each_team_in_scope_with_the_state_of_its_report(store: Store) -> None:
    report_league(store)
    session = store.session
    snap = make_snapshot(session, SLOT)
    add_status(
        session, snap, team=1, player=101, game_id="g7", status="out", published=SLOT,
        expected_return_round_from=None, expected_return_date=date(2025, 11, 20),
        name="Hargrove, Wyatt",
    )  # fmt: skip
    add_status(session, snap, team=1, player=102, game_id="g7", status="probable", published=SLOT)
    add_status(
        session, snap, team=2, player=201, game_id="g7", status="questionable", published=SLOT
    )
    team_report(session, snap, 1, "g7", "submitted")
    team_report(session, snap, 2, "g7", "submitted")
    team_report(session, snap, 3, "g8", "notYetSubmitted")
    session.commit()

    body = availability(store, SLOT + timedelta(minutes=20), day="2025-11-13")
    assert body["league"] == "nba" and body["state"] == "fresh" and body["message"]
    assert body["asOf"] == "2025-11-12T15:00:00Z" and body["news"] is None
    assert [(t["team"]["teamId"], t["game"]["gameId"]) for t in body["teams"]] == [
        (1, "g7"), (2, "g7"), (3, "g8"), (2, "g8"),
    ]  # fmt: skip
    assert [t["reportState"] for t in body["teams"]] == [
        "submitted", "submitted", "notYetSubmitted", "noReport",
    ]  # fmt: skip
    assert entries_of(body, 3) == [] and body["teams"][3]["entries"] == []

    one = entries_of(body, 1)
    # A matched player is shown by the store's name; only an unmatched one by the printed name.
    assert sorted(e["playerName"] for e in one) == ["Player 101", "Player 102"]
    star = next(e for e in one if e["player"]["id"] == "101")
    assert (
        star["status"] == "out" and star["statusLabel"] == "Out" and star["chanceOfPlaying"] == 0.0
    )
    assert (
        star["modelStatus"] is None
    )  # the workbook's "In model" column does not exist for the NBA
    assert star["reasonCategory"] == "injury" and star["reasonText"].startswith("Injury/Illness")
    assert star["expectedReturn"] == {"roundFrom": None, "roundTo": None, "date": "2025-11-20"}
    assert star["game"]["gameId"] == "g7" and star["isOverride"] is False and star["statusId"]
    assert star["inForce"] is True and star["isStale"] is False and star["ageMinutes"] == 20
    assert (
        star["source"]["kind"] == "leagueReport"
        and star["source"]["snapshotId"] == snap.snapshot_id
    )
    assert star["source"]["publishedAt"] == "2025-11-12T15:00:00Z"
    probable = next(e for e in one if e["player"]["id"] == "102")
    assert probable["chanceOfPlaying"] == 0.85 and probable["expectedReturn"] is None

    # "No report" is null, never "available": a player nobody listed is simply not an entry.
    assert {e["player"]["id"] for e in one} == {"101", "102"}
    assert all(e["status"] is not None for e in one)
    # The same slate for one team only is that team's rows, and a team with no scope game is
    # given its next one.
    only = availability(store, SLOT + timedelta(minutes=20), team=1)
    assert [(t["team"]["teamId"], t["game"]["gameId"]) for t in only["teams"]] == [(1, "g7")]
    assert len(only["teams"][0]["entries"]) == 2
    assert availability(store, SLOT, team=3)["teams"][0]["game"]["gameId"] == "g8"


def test_a_status_is_listed_with_its_age_from_the_source_not_the_fetch(store: Store) -> None:
    report_league(store)
    published = at("2025-11-12", 14, 0)
    snap = make_snapshot(store.session, published, fetched=at("2025-11-12", 15, 55))
    add_status(
        store.session, snap, team=1, player=101, game_id="g7", status="doubtful",
        published=published, recorded=at("2025-11-12", 15, 55),
    )  # fmt: skip
    team_report(store.session, snap, 1, "g7", "submitted")
    store.session.commit()
    [entry] = entries_of(availability(store, at("2025-11-12", 16, 0), team=1), 1)
    assert entry["ageMinutes"] == 120  # two hours since the league said it, not five minutes
    assert entry["source"]["fetchedAt"] == "2025-11-12T15:55:00Z"
    assert entry["source"]["publishedAt"] == "2025-11-12T14:00:00Z"


def test_a_newer_submitted_list_removes_whoever_it_no_longer_names(store: Store) -> None:
    """The report is cumulative per slot: each snapshot restates the team's list for a game. When
    a newer, fully read snapshot says the team submitted and a player the older one listed is not
    on it, he is no longer listed. A snapshot that only partly read adds rows but never removes
    anyone, and a team that has not yet submitted removes no one either."""
    report_league(store)
    session = store.session
    older = make_snapshot(session, SLOT)
    add_status(session, older, team=1, player=101, game_id="g7", status="out", published=SLOT)
    add_status(
        session, older, team=1, player=102, game_id="g7", status="questionable", published=SLOT
    )
    team_report(session, older, 1, "g7", "submitted")
    session.commit()
    now = SLOT + timedelta(hours=3)
    assert {e["player"]["id"] for e in entries_of(availability(store, now, team=1), 1)} == {
        "101",
        "102",
    }

    # A partly read newer snapshot that lists only 101: 102 stays.
    partial = make_snapshot(session, SLOT + timedelta(hours=1), parse_status="partial")
    add_status(
        session, partial, team=1, player=101, game_id="g7", status="out",
        published=SLOT + timedelta(hours=1),
    )  # fmt: skip
    team_report(session, partial, 1, "g7", "submitted")
    session.commit()
    assert {e["player"]["id"] for e in entries_of(availability(store, now, team=1), 1)} == {
        "101",
        "102",
    }

    # A team that has not yet submitted in a newer snapshot: nobody is removed.
    pending = make_snapshot(session, SLOT + timedelta(hours=1, minutes=30))
    team_report(session, pending, 1, "g7", "notYetSubmitted")
    session.commit()
    body = availability(store, now, team=1)
    assert {e["player"]["id"] for e in entries_of(body, 1)} == {"101", "102"}
    assert body["teams"][0]["reportState"] == "notYetSubmitted"  # the newest word is the team's

    # A fully read newer snapshot, submitted, that no longer lists 102: he is gone from the list,
    # and from the projection, which now assumes he plays.
    newer = make_snapshot(session, SLOT + timedelta(hours=2))
    add_status(
        session, newer, team=1, player=101, game_id="g7", status="out",
        published=SLOT + timedelta(hours=2),
    )  # fmt: skip
    team_report(session, newer, 1, "g7", "submitted")
    session.commit()
    body = availability(store, now, team=1)
    assert [e["player"]["id"] for e in entries_of(body, 1)] == ["101"]
    assert body["teams"][0]["reportState"] == "submitted"
    detail = nba_matchup.game_projection(session, "g7", now=now)["current"]
    assert {a["player"]["id"] for a in detail["home"]["keyAbsences"]} == {"101"}


def test_an_old_entry_is_shown_marked_out_of_force_with_the_rule_that_ended_it(
    store: Store,
) -> None:
    report_league(store)
    session = store.session
    now = at("2025-11-12", 16, 0)
    old = now - timedelta(days=15)
    snap = make_snapshot(session, old)
    store.league.player(104)  # on the roster, in no box score at all
    add_status(session, snap, team=1, player=104, game_id=None, status="out", published=old)
    # Superseded by a game he played after the entry (the box score outranks a status).
    seen = make_snapshot(session, at("2025-11-02", 15, 0))
    add_status(
        session, seen, team=1, player=103, game_id=None, status="out",
        published=at("2025-11-02", 15, 0),
    )  # fmt: skip
    session.commit()  # team 1 played g3 on 5 November and g4 on 7 November with 103 in them
    entries = {e["player"]["id"]: e for e in entries_of(availability(store, now, team=1), 1)}
    assert entries["104"]["inForce"] is False and entries["104"]["outOfForceReason"] == "tooOld"
    assert entries["104"]["isStale"] is True
    assert (
        entries["103"]["inForce"] is False and entries["103"]["outOfForceReason"] == "playedSince"
    )
    assert entries["103"]["isStale"] is True
    # Still listed, still sourced and dated: a reader can see why a name no longer drives anything.
    assert entries["104"]["source"]["publishedAt"] == refs_stamp(old)


def refs_stamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_a_later_report_that_could_not_be_read_makes_the_older_list_stale(store: Store) -> None:
    """If the league has published a newer report that this installation could not read, the older
    list can no longer be vouched for, however young it is: its entries turn stale (and the whole
    report says so), while a good report after the bad one makes the list current again."""
    report_league(store)
    session = store.session
    good = make_snapshot(session, SLOT)
    add_status(session, good, team=1, player=101, game_id="g7", status="out", published=SLOT)
    team_report(session, good, 1, "g7", "submitted")
    session.commit()
    now = SLOT + timedelta(hours=1, minutes=10)  # outside a window: a day's grace, so not aged out

    def stale() -> bool:
        [entry] = entries_of(availability(store, now, team=1), 1)
        return entry["isStale"]

    assert stale() is False

    bad = make_snapshot(session, SLOT + timedelta(hours=1), parse_status="headerMismatch")
    session.commit()
    assert bad.snapshot_id and stale() is True
    intel_store.set_source_state(
        session, intel_store.SOURCE_INJURY_REPORT, "unreadable", now=naive(now), error="layout"
    )
    session.commit()
    assert availability(store, now, team=1)["state"] == "unreadable"

    # A failure the clock has not reached yet is not known: a read made before it is not stale.
    before_it = SLOT + timedelta(minutes=30)
    [early] = entries_of(availability(store, before_it, team=1), 1)
    assert early["isStale"] is False
    # And a later good report, which restates the list, is current again.
    again = make_snapshot(session, SLOT + timedelta(hours=1, minutes=5))
    add_status(
        session, again, team=1, player=101, game_id="g7", status="out",
        published=SLOT + timedelta(hours=1, minutes=5),
    )  # fmt: skip
    team_report(session, again, 1, "g7", "submitted")
    session.commit()
    assert stale() is False


def test_a_name_that_matched_nobody_is_shown_as_printed_and_waits_to_be_linked(
    store: Store,
) -> None:
    report_league(store)
    session = store.session
    snap = make_snapshot(session, SLOT)
    unmatched = add_status(
        session, snap, team=1, player=None, game_id="g7", status="questionable",
        published=SLOT, name="Okafor-Lindqvist, Tobias",
    )  # fmt: skip
    team_report(session, snap, 1, "g7", "submitted")
    session.commit()
    now = SLOT + timedelta(minutes=30)
    [entry] = entries_of(availability(store, now, team=1), 1)
    assert entry["player"] is None and entry["playerName"] == "Okafor-Lindqvist, Tobias"
    assert entry["status"] == "questionable" and entry["chanceOfPlaying"] == 0.5
    queue = nba_matchup.availability_view.review_queue(
        queries.build_context(session, SEASON, now=now)
    )
    assert [(i["statusId"], i["playerName"], i["team"]["teamId"]) for i in queue["items"]] == [
        (unmatched.status_id, "Okafor-Lindqvist, Tobias", 1)
    ]
    assert queue["items"][0]["source"]["kind"] == "leagueReport"
    # It does not drive the projection: an unmatched name is never guessed onto a player.
    detail = nba_matchup.game_projection(session, "g7", now=now)["current"]
    assert detail["home"]["keyAbsences"] == []

    # A person links him: the unmatched row is retracted and a replacement names the player.
    intel_status.resolve_review_item(session, unmatched.status_id, 102, now=naive(now))
    session.commit()
    [linked] = entries_of(availability(store, now + timedelta(minutes=1), team=1), 1)
    assert linked["player"]["id"] == "102" and linked["status"] == "questionable"
    queue = nba_matchup.availability_view.review_queue(
        queries.build_context(session, SEASON, now=now + timedelta(minutes=1))
    )
    assert queue["items"] == []


def test_the_statuses_filter_keeps_only_those_entries(store: Store) -> None:
    report_league(store)
    session = store.session
    snap = make_snapshot(session, SLOT)
    for player, status in ((101, "out"), (102, "doubtful"), (103, "probable")):
        add_status(
            session, snap, team=1, player=player, game_id="g7", status=status, published=SLOT
        )
    team_report(session, snap, 1, "g7", "submitted")
    session.commit()
    now = SLOT + timedelta(minutes=5)
    assert len(entries_of(availability(store, now, team=1), 1)) == 3
    both = availability(store, now, team=1, statuses="OUT, doubtful")
    assert {e["status"] for e in entries_of(both, 1)} == {"out", "doubtful"}
    assert (
        availability(store, now, team=1, statuses="probable")["teams"][0]["reportState"]
        == "submitted"
    )
    with pytest.raises(ApiError) as caught:
        availability(store, now, team=1, statuses="out,maybe")
    assert (caught.value.code, caught.value.http_status, caught.value.field) == (
        "invalid_status", 400, "statuses",
    )  # fmt: skip


def test_the_state_of_the_report_as_a_whole_is_fresh_stale_unreadable_or_off(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    report_league(store)
    session = store.session
    assert availability(store, SLOT, day="2025-11-13")["state"] == "noReportYet"
    snap = make_snapshot(session, SLOT)
    team_report(session, snap, 1, "g7", "submitted")
    session.commit()
    # Outside a reporting window (10:00 Eastern the day before, window opens at 17:00) a report can
    # vouch for a day; inside one, only for an hour.
    assert availability(store, SLOT + timedelta(hours=5), day="2025-11-13")["state"] == "fresh"
    assert availability(store, SLOT + timedelta(hours=25), day="2025-11-13")["state"] == "stale"
    inside = availability(store, at("2025-11-12", 23, 0), day="2025-11-13")  # 18:00 Eastern
    assert inside["state"] == "stale" and "past the 60 minutes" in inside["message"]
    fresh = availability(store, at("2025-11-12", 15, 30), day="2025-11-13")
    assert fresh["state"] == "fresh"

    intel_store.set_source_state(
        session, intel_store.SOURCE_INJURY_REPORT, "unreadable",
        now=naive(SLOT), error="the header was not the seven expected names",
    )  # fmt: skip
    session.commit()
    unreadable = availability(store, SLOT + timedelta(minutes=5), day="2025-11-13")
    assert unreadable["state"] == "unreadable"
    assert (
        "nothing was guessed" in unreadable["message"]
        and "seven expected names" in unreadable["message"]
    )

    intel_store.set_source_state(
        session, intel_store.SOURCE_INJURY_REPORT, "ok", now=naive(SLOT), success=True
    )
    session.commit()
    monkeypatch.setenv("HARDWOOD_NBA_INJURIES", "off")
    off = availability(store, SLOT + timedelta(minutes=5), day="2025-11-13")
    assert off["state"] == "disabled" and "HARDWOOD_NBA_INJURIES" in off["message"]


def test_the_demo_league_reports_disabled_with_no_entries_and_refuses_a_write(store: Store) -> None:
    from nbastats.nba_matchup import availability_view

    report_league(store)
    session = store.session
    snap = make_snapshot(session, SLOT)
    add_status(session, snap, team=1, player=101, game_id="g7", status="out", published=SLOT)
    session.commit()
    session.query(Game).filter(Game.game_id == "g1").update({"data_source": "synthetic-demo"})
    session.commit()
    body = availability(store, SLOT + timedelta(minutes=5), day="2025-11-13")
    assert body["state"] == "disabled" and "Demo league" in body["message"] and body["asOf"] is None
    assert all(t["entries"] == [] and t["reportState"] == "noReport" for t in body["teams"])
    ctx = queries.build_context(session, SEASON, now=SLOT)
    with pytest.raises(ApiError) as caught:
        availability_view.record_override(ctx, player_id=101, status="out")
    assert caught.value.code == "bad_request" and "Demo league" in caught.value.message
    assert availability_view.review_queue(ctx)["items"] == []


# =========================================================================== the ledger


def scheduled_league(store: Store, *, tipoff: datetime | None = None) -> LeagueBuilder:
    league = squad_league(store)
    league.game("g7", "2025-11-13", 1, 2, tipoff=tipoff)
    league.commit()
    return league


def context_at(store: Store, now: datetime) -> tuple[Any, Any]:
    queries.clear_memo()
    ctx = queries.build_context(store.session, SEASON, now=now)
    return ctx, projection.get_model(ctx)


def rows_of(store: Store, kind: str, game_id: str = "g7") -> list[TeamProjectionLedger]:
    return list(
        store.session.execute(
            select(TeamProjectionLedger)
            .where(TeamProjectionLedger.game_id == game_id, TeamProjectionLedger.kind == kind)
            .order_by(TeamProjectionLedger.ledger_id)
        ).scalars()
    )


def test_the_lock_is_written_in_the_hour_before_tip_off_and_refused_at_it(store: Store) -> None:
    tip = at("2025-11-13", 19, 0)
    scheduled_league(store, tipoff=tip)
    session = store.session

    # More than an hour out nothing is locked; a latest row is still written (inside 48 hours).
    ctx, model = context_at(store, tip - timedelta(minutes=61))
    assert ledger.lock_due(session, ctx, model) == []
    assert len(ledger.refresh_latest(session, ctx, model)) == 1
    session.commit()
    assert rows_of(store, "locked") == [] and len(rows_of(store, "latest")) == 1

    # Inside the hour: locked, from a row computed before tip-off.
    ctx, model = context_at(store, tip - timedelta(minutes=30))
    [locked] = ledger.lock_due(session, ctx, model)
    session.commit()
    assert locked.kind == "locked" and locked.computed_at < naive(tip)
    # It is a copy of the newest latest row, and keeps that row's own time: the inputs had not
    # changed since the first one was written 61 minutes out, so that is when it was computed.
    assert locked.computed_at == naive(tip - timedelta(minutes=61))
    # Idempotent: a second pass writes nothing and the frozen row is the same one.
    assert ledger.lock_due(session, ctx, model) == []
    assert [r.ledger_id for r in rows_of(store, "locked")] == [locked.ledger_id]

    # At tip-off exactly, and after it, a lock is refused outright.
    for moment in (tip, tip + timedelta(seconds=1), tip + timedelta(hours=1)):
        ctx, model = context_at(store, moment)
        with pytest.raises(ledger.LockRefused):
            ledger.lock_game(session, ctx, model, ctx.game("g7"))
    assert len(rows_of(store, "locked")) == 1


def test_a_locked_row_cannot_be_written_at_or_after_the_deadline_by_any_path(
    store: Store,
) -> None:
    tip = at("2025-11-13", 19, 0)
    scheduled_league(store, tipoff=tip)
    ctx, model = context_at(store, tip - timedelta(minutes=10))
    game = ctx.game("g7")
    result = model.project(ctx, game, as_of=ctx.now, kind="latest")
    ok = ledger.write_row(
        store.session, ctx, game, result, kind="locked",
        computed_at=tip - timedelta(seconds=1), inputs_cutoff=tip - timedelta(seconds=1),
    )  # fmt: skip
    assert ok.kind == "locked" and ok.computed_at < naive(tip)
    store.session.rollback()
    for moment in (tip, tip + timedelta(minutes=5)):
        with pytest.raises(ledger.LockRefused):
            ledger.write_row(
                store.session, ctx, game, result, kind="locked",
                computed_at=moment, inputs_cutoff=moment,
            )  # fmt: skip
    # A *latest* row is not a prediction and is not held to the rule.
    row = ledger.write_row(
        store.session, ctx, game, result, kind="latest",
        computed_at=tip - timedelta(minutes=9), inputs_cutoff=tip - timedelta(minutes=9),
    )  # fmt: skip
    assert row.kind == "latest"
    store.session.rollback()


def test_with_no_recorded_tip_off_the_deadline_is_noon_eastern(store: Store) -> None:
    scheduled_league(store)  # g7 on the 13th, no tip-off recorded: deadline 17:00Z (noon ET)
    session = store.session
    ctx, model = context_at(store, at("2025-11-13", 15, 59))
    assert ledger.lock_due(session, ctx, model) == []  # 61 minutes out
    ctx, model = context_at(store, at("2025-11-13", 16, 30))
    [locked] = ledger.lock_due(session, ctx, model)
    session.commit()
    assert locked.computed_at < naive(at("2025-11-13", 17, 0))
    ctx, model = context_at(store, at("2025-11-13", 17, 0))
    with pytest.raises(ledger.LockRefused):
        ledger.lock_game(session, ctx, model, ctx.game("g7"))
    # A game whose real tip-off turns out to be hours later is still unlockable from noon on: the
    # rule errs towards a projection that missed some news, never one that came after the game.
    assert ctx.deadline_of(ctx.game("g7")) == at("2025-11-13", 17, 0)


def test_a_locked_projection_is_shown_exactly_as_written_whatever_has_changed(
    store: Store,
) -> None:
    tip = at("2025-11-13", 19, 0)
    scheduled_league(store, tipoff=tip)
    ctx, model = context_at(store, tip - timedelta(minutes=20))
    [locked] = ledger.lock_due(store.session, ctx, model)
    store.session.commit()
    frozen = (locked.home_pts, locked.away_pts, locked.computed_at)

    # After tip-off a star is declared out: the frozen projection does not move, and says it is
    # the frozen one; a re-read does not rewrite it.
    slot = tip + timedelta(minutes=5)
    snap = make_snapshot(store.session, slot)
    add_status(store.session, snap, team=1, player=101, game_id="g7", status="out", published=slot)
    store.session.commit()
    detail = nba_matchup.game_projection(store.session, "g7", now=tip + timedelta(hours=1))
    assert detail["current"] is None
    shown = detail["locked"]
    assert shown["model"]["kind"] == "locked"
    assert shown["home"]["projectedPoints"] == pytest.approx(frozen[0], abs=1e-12)
    assert shown["away"]["projectedPoints"] == pytest.approx(frozen[1], abs=1e-12)
    assert shown["model"]["computedAt"] == "2025-11-13T18:40:00Z"
    assert any("not frozen" in note for note in shown["notes"])
    assert shown["home"]["keyAbsences"] == []  # player detail was not frozen with it
    assert shown["assumptions"]["assumedAvailable"] == {"home": None, "away": None, "basis": None}
    row = rows_of(store, "locked")[0]
    assert (row.home_pts, row.away_pts, row.computed_at) == frozen
    assert [h["kind"] for h in detail["history"]][-1] == "locked"


def test_a_projection_that_was_never_frozen_is_rebuilt_and_says_so(store: Store) -> None:
    tip = at("2025-11-13", 19, 0)
    scheduled_league(store, tipoff=tip)
    # Nobody locked anything: after tip-off the model's projection is rebuilt from what was known
    # before it, never from the clock's present.
    detail = nba_matchup.game_projection(store.session, "g7", now=tip + timedelta(hours=1))
    shown = detail["current"]
    assert detail["locked"] is None
    assert shown["model"]["kind"] == "reconstructed"
    assert any("Rebuilt after the lock deadline" in note for note in shown["notes"])
    assert shown["model"]["inputsCutoff"] == "2025-11-13T18:59:59Z"
    # Before tip-off the same game is the model's projection of the moment.
    early = nba_matchup.game_projection(store.session, "g7", now=tip - timedelta(hours=3))
    assert early["current"]["model"]["kind"] == "latest"
    assert early["current"]["home"]["projectedPoints"] == pytest.approx(
        shown["home"]["projectedPoints"], abs=1e-12
    )


def test_inputs_going_back_to_an_earlier_state_do_not_break_the_ledger(store: Store) -> None:
    """A star is entered as out and then cleared: the third state of the inputs equals the first,
    and the table's one-row-per-inputs rule must neither crash the refresh nor repeat a row."""
    tip = at("2025-11-13", 19, 0)
    scheduled_league(store, tipoff=tip)
    session = store.session

    ctx, model = context_at(store, tip - timedelta(hours=6))
    [first] = ledger.refresh_latest(session, ctx, model)
    session.commit()
    original = first.inputs_sha256
    assert ledger.refresh_latest(session, ctx, model) == []  # unchanged inputs: nothing written

    override = intel_status.add_override(
        session,
        player_id=101,
        status="out",
        team_id=1,
        now=naive(tip - timedelta(hours=5, minutes=50)),
    )
    session.commit()
    ctx, model = context_at(store, tip - timedelta(hours=5))
    [second] = ledger.refresh_latest(session, ctx, model)
    session.commit()
    assert second.inputs_sha256 != original and second.home_pts < first.home_pts

    intel_status.clear_override(
        session, override.override_id, now=naive(tip - timedelta(hours=4, minutes=50))
    )
    session.commit()
    later = tip - timedelta(hours=4)
    ctx, model = context_at(store, later)
    [back] = ledger.refresh_latest(session, ctx, model)  # does not raise
    session.commit()
    assert back.ledger_id == first.ledger_id  # the earlier row, brought forward
    assert back.inputs_sha256 == original and back.computed_at == naive(later)
    assert (
        len(rows_of(store, "latest")) == 2
        and len({r.inputs_sha256 for r in rows_of(store, "latest")}) == 2
    )
    assert ledger.refresh_latest(session, ctx, model) == []

    # The newest row is the one computed last, so that is what a lock freezes.
    ctx, model = context_at(store, tip - timedelta(minutes=15))
    [locked] = ledger.lock_due(session, ctx, model)
    session.commit()
    assert locked.home_pts == pytest.approx(first.home_pts, abs=1e-12)  # the restored inputs
    assert locked.inputs_sha256 == original


def test_a_game_that_cannot_be_projected_is_never_locked(store: Store) -> None:
    """A pre-1996 game has no projection to freeze: nothing is written, and nothing is invented."""
    for team in (1, 2):
        store.league.team(team)
    store.league.game("old1", "1992-11-01", 1, 2, 100, 90, season="1992-93")
    store.league.game("old2", "1992-11-10", 1, 2, season="1992-93")
    store.league.commit()
    queries.clear_memo()
    ctx = queries.build_context(store.session, "1992-93", now=at("1992-11-10", 8, 0))
    with pytest.raises(projection.ProjectionUnavailable):
        projection.get_model(ctx)
    from nbastats.nba_matchup.projection import Model

    shell = Model(
        season="1992-93", prior=None, regression=0.3, offset=10.0, league_weight=150.0,
        home_default=2.5, home_fixed=False, home_fit_min=300,
        snapshots=[projection.Snapshot(start=None, teams={})], pre={}, residuals=[],
    )  # fmt: skip
    assert ledger.lock_game(store.session, ctx, shell, ctx.game("old2")) is None
    assert rows_of(store, "latest", "old2") == [] and rows_of(store, "locked", "old2") == []


@pytest.fixture()
def worker_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """The process-wide engine pointed at a fresh store, as the scheduler sees it."""
    url = f"sqlite:///{tmp_path / 'worker.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config.reset_settings_cache()
    db_module.dispose_engine()
    engine = create_db_engine(url)
    init_db(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        db_module.dispose_engine()
        config.reset_settings_cache()


def test_the_workers_jobs_refresh_then_lock_and_stop_at_tip_off(worker_db: Any) -> None:
    tip = at("2025-11-13", 19, 0)
    with Session(worker_db, future=True) as session:
        league = LeagueBuilder(session)
        # squad_league wants the ``store`` fixture; build the same league by hand here.
        for team in (1, 2, 3):
            league.team(team)
        league.game("g1", "2025-11-01", 1, 2, 100, 90)
        league.game("g2", "2025-11-03", 2, 3, 110, 105)
        league.game("g3", "2025-11-05", 3, 1, 98, 102)
        league.game("g7", "2025-11-13", 1, 2, tipoff=tip)
        league.commit()
    queries.clear_memo()
    refreshed = ledger.run_refresh(now=tip - timedelta(hours=2))
    assert refreshed["status"] == "ok" and refreshed["written"] == 1
    assert ledger.run_refresh(now=tip - timedelta(hours=2))["written"] == 0
    assert ledger.run_lock(now=tip - timedelta(hours=2))["locked"] == 0  # outside the hour
    locked = ledger.run_lock(now=tip - timedelta(minutes=45))
    assert locked["status"] == "ok" and locked["locked"] == 1
    # After tip-off the job writes nothing at all.
    queries.clear_memo()
    after = ledger.run_lock(now=tip + timedelta(minutes=1))
    assert after["locked"] == 0 and after["status"] == "skipped"
    with Session(worker_db, future=True) as session:
        kinds = [
            r.kind
            for r in session.execute(
                select(TeamProjectionLedger).order_by(TeamProjectionLedger.ledger_id)
            ).scalars()
        ]
    assert kinds == ["latest", "locked"]


# =========================================================================== the review


def locked_row(game_id: str, home: float, away: float, computed: datetime) -> TeamProjectionLedger:
    return TeamProjectionLedger(
        game_id=game_id, kind="locked", model_version="nba-1", computed_at=naive(computed),
        inputs_cutoff=naive(computed), home_pts=home, away_pts=away,
        home_full_strength=home, away_full_strength=away, home_attack_index=1.0,
        away_attack_index=1.0, home_defence_index=1.0, away_defence_index=1.0,
        home_availability_factor=1.0, away_availability_factor=1.0, home_advantage_points=2.5,
        team_sd=None, margin_sd=None, availability_snapshot_id=None,
        settings_sha256="0" * 64, inputs_sha256=game_id.ljust(64, "0"),
    )  # fmt: skip


def test_the_review_is_the_hand_calculation_and_keeps_rebuilt_projections_apart(
    store: Store,
) -> None:
    league = store.league
    for team in (1, 2, 3):
        league.team(team)
    league.game("w0", "2025-10-20", 1, 3, 101, 97)  # gives the model a league level to start from
    league.game("ga", "2025-11-01", 1, 2, 100, 90)
    league.game("gb", "2025-11-03", 2, 3, 95, 99)
    league.game("gc", "2025-11-05", 3, 1, 110, 100)
    league.game("gd", "2025-11-07", 1, 2, 104, 98)  # never frozen: rebuilt
    league.commit()
    for gid, home, away, day in (
        ("ga", 98.0, 95.0, "2025-11-01"),
        ("gb", 100.0, 99.8, "2025-11-03"),  # margin 0.2: a toss-up, names no winner
        ("gc", 100.0, 104.0, "2025-11-05"),
    ):
        store.session.add(locked_row(gid, home, away, at(day, 0, 0)))
    store.session.commit()
    queries.clear_memo()

    review = nba_matchup.projection_review(store.session, season=SEASON, now=at("2025-12-01"))
    [locked] = review["byModel"]
    # ga: home 100-90 against 98-95 (margin 3): called, miss 7, scores off by 2 and 5, combined 3.
    # gb: home 95-99 against 100-99.8 (0.2): a toss-up, miss -4.2, scores off by 5 and 0.8,
    #     combined 5.8.
    # gc: home 110-100 against 100-104 (-4): not called, miss 14, scores off by 10 and 4,
    #     combined 6.
    assert locked["modelKey"] == "hardwood"
    assert locked["games"] == 3 and locked["decidedGames"] == 2
    assert locked["winnersCalled"] == 1 and locked["tossUps"] == 1
    assert close(locked["meanAbsMarginMiss"], (7 + 4.2 + 14) / 3)
    assert close(locked["meanAbsScoreMiss"], (7 + 5.8 + 14) / 6)
    assert close(locked["meanAbsCombinedMiss"], (3 + 5.8 + 6) / 3)

    rebuilt = review["reconstructed"]
    assert rebuilt is not None and rebuilt["games"] >= 1  # gd (and nothing locked is counted twice)
    by_game = {row["game"]["gameId"]: row for row in review["games"]}
    assert by_game["ga"]["kind"] == "locked" and by_game["ga"]["locked"] == {
        "homePts": 98.0,
        "awayPts": 95.0,
    }
    assert close(by_game["ga"]["marginMiss"], 7.0) and by_game["ga"]["winnerCalled"] is True
    assert by_game["gb"]["winnerCalled"] is None
    assert by_game["gc"]["winnerCalled"] is False and close(by_game["gc"]["marginMiss"], 14.0)
    assert by_game["gd"]["kind"] == "reconstructed" and by_game["gd"]["locked"] is None
    assert not any(
        row["game"]["gameId"] in {"ga", "gb", "gc"} and row["kind"] == "reconstructed"
        for row in review["games"]
    )
    assert any(
        "No projection was recorded before tip-off for 2 of 5 final games" in n
        for n in review["notes"]
    )
    assert not any("rank" in key.lower() for key in _keys(review))


def _keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [k for k in value] + [x for v in value.values() for x in _keys(v)]
    if isinstance(value, list):
        return [x for v in value for x in _keys(v)]
    return []


def test_a_day_with_nothing_to_review_has_null_means_not_zero(store: Store) -> None:
    for team in (1, 2):
        store.league.team(team)
    store.league.game("g1", "2025-11-01", 1, 2, 100, 90)
    store.league.commit()
    review = nba_matchup.projection_review(store.session, day="2025-11-01", now=NOW)
    assert review["byModel"] == [] and review["reconstructed"] is None and review["games"] == []
    from nbastats.nba_matchup import slate

    empty = slate.metrics([])
    assert empty["games"] == 0 and empty["winnersCalled"] == 0
    assert empty["meanAbsMarginMiss"] is None and empty["meanAbsScoreMiss"] is None
    assert empty["meanAbsCombinedMiss"] is None


# =========================================================================== the spread


def test_there_is_no_range_until_a_spread_has_been_fitted(store: Store) -> None:
    four_teams(store.league)
    add_rows(store.league, TEN_GAMES)
    store.league.game("s1", "2025-11-12", 1, 3)
    store.league.commit()

    def current() -> dict[str, Any]:
        queries.clear_memo()
        return nba_matchup.game_projection(store.session, "s1", now=NOW)["current"]

    bare = current()
    assert bare["marginRange80"] is None and bare["intervalBasis"] is None
    assert bare["home"]["range80"] is None and bare["away"]["range80"] is None
    assert any("No spread has been fitted yet" in note for note in bare["notes"])
    spreads = {
        c["key"]: c for c in bare["model"]["constants"] if c["key"] in ("teamSd", "marginSd")
    }
    assert spreads["teamSd"]["value"] is None and spreads["teamSd"]["isDefault"] is True
    assert spreads["marginSd"]["value"] is None

    intel_settings.apply_patch(
        store.session, {"teamSd": 9.0, "marginSd": 12.0}, provenance="manual", now=naive(NOW)
    )
    store.session.commit()
    typed = current()
    assert typed["intervalBasis"] == "assumed"  # typed by a person, never checked against results
    centre = typed["margin"]
    assert typed["marginRange80"] == {
        "low": pytest.approx(centre - Z80 * 12.0),
        "high": pytest.approx(centre + Z80 * 12.0),
    }
    home = typed["home"]["projectedPoints"]
    assert typed["home"]["range80"]["low"] == pytest.approx(home - Z80 * 9.0)
    assert typed["home"]["range80"]["high"] == pytest.approx(home + Z80 * 9.0)


def prior_season_games(count_cycles: int = 4) -> list[tuple[str, str, int, int, int, int]]:
    """Enough games of 2024-25 (120) to clear the 100 a fit needs."""
    rows: list[tuple[str, str, int, int, int, int]] = []
    teams = [1, 2, 3, 4, 5, 6]
    n = 0
    for _ in range(count_cycles):
        for i, a in enumerate(teams):
            for b in teams[i + 1 :]:
                for home, away in ((a, b), (b, a)):
                    n += 1
                    rows.append(
                        (
                            (date(2024, 10, 20) + timedelta(days=n // 2)).isoformat(),
                            f"q{n:03d}",
                            home,
                            away,
                            95 + (n * 7) % 19 + (2 if home in (1, 2) else 0),
                            93 + (n * 5) % 17 + (3 if away == 3 else 0),
                        )
                    )
    return rows


def calibrate_store(store: Store) -> list[tuple[str, str, int, int, int, int]]:
    for team in range(1, 7):
        store.league.team(team)
    rows = prior_season_games()
    add_rows(store.league, rows, season="2024-25")
    store.league.game("c1", "2025-11-01", 1, 2, 104, 99)
    store.league.game("s1", "2025-11-12", 1, 3)
    store.league.commit()
    queries.clear_memo()
    return rows


def rms(values: Sequence[float]) -> float:
    return math.sqrt(math.fsum(v * v for v in values) / len(values))


def test_the_spread_is_fitted_from_last_seasons_walk_forward_residuals(store: Store) -> None:
    rows = calibrate_store(store)
    assert len(rows) == 120
    _, residuals, _ = transcribe(rows)  # the design's model, projected game by game
    team_sd = rms([r for home, away, _ in residuals for r in (home, away)])
    margin_sd = rms([m for _, _, m in residuals])

    ctx = queries.build_context(store.session, SEASON, now=NOW)
    result = projection.calibrate(store.session, ctx, NOW)
    store.session.commit()
    assert result.fitted and result.source == "fittedPrevSeason"
    assert result.games == len(residuals)
    assert result.team_sd == pytest.approx(team_sd, abs=1e-9)
    assert result.margin_sd == pytest.approx(margin_sd, abs=1e-9)

    queries.clear_memo()
    stored = {s.key: s for s in intel_settings.get_all(store.session)}
    assert stored["teamSd"].value == pytest.approx(team_sd, abs=1e-9)
    assert stored["teamSd"].provenance == stored["marginSd"].provenance == "fittedPrevSeason"
    shown = nba_matchup.game_projection(store.session, "s1", now=NOW)["current"]
    assert shown["intervalBasis"] == "fittedPrevSeason"
    assert shown["marginRange80"]["high"] - shown["margin"] == pytest.approx(
        Z80 * margin_sd, abs=1e-9
    )


def test_a_spread_is_never_fitted_from_the_demo_league_or_over_one_a_person_set(
    store: Store,
) -> None:
    calibrate_store(store)
    store.session.query(Game).filter(Game.season == "2024-25").update(
        {"data_source": "synthetic-demo"}
    )
    store.session.commit()
    queries.clear_memo()
    ctx = queries.build_context(store.session, SEASON, now=NOW)
    refused = projection.calibrate(store.session, ctx, NOW)
    assert refused.fitted is False and "Demo league" in (refused.reason or "")
    assert all(
        s.is_default
        for s in intel_settings.get_all(store.session)
        if s.key in ("teamSd", "marginSd")
    )

    store.session.query(Game).update({"data_source": None})
    store.session.commit()
    intel_settings.set_setting(store.session, "teamSd", 8.0, provenance="manual")
    store.session.commit()
    queries.clear_memo()
    ctx = queries.build_context(store.session, SEASON, now=NOW)
    done = projection.calibrate(store.session, ctx, NOW)
    store.session.commit()
    assert done.fitted and done.skipped == ("teamSd",)
    values = {s.key: s for s in intel_settings.get_all(store.session)}
    assert values["teamSd"].value == 8.0 and values["teamSd"].provenance == "manual"
    assert values["marginSd"].provenance == "fittedPrevSeason"


def test_a_store_with_no_previous_season_fits_nothing(store: Store) -> None:
    four_teams(store.league)
    add_rows(store.league, TEN_GAMES)
    store.league.commit()
    ctx = queries.build_context(store.session, SEASON, now=NOW)
    result = projection.calibrate(store.session, ctx, NOW)
    assert result.fitted is False and "no previous season" in (result.reason or "")
    short = LeagueBuilder(store.session)
    short.game("o1", "2024-11-01", 1, 2, 100, 90, season="2024-25")
    short.commit()
    queries.clear_memo()
    ctx = queries.build_context(store.session, SEASON, now=NOW)
    thin = projection.calibrate(store.session, ctx, NOW)
    assert thin.fitted is False and "100 are needed" in (thin.reason or "")


def test_three_hundred_locked_results_move_the_fit_to_the_ledger(store: Store) -> None:
    for team in (1, 2, 3, 4):
        store.league.team(team)
    residual_pairs: list[tuple[float, float]] = []
    for n in range(300):
        home, away = 1 + n % 4, 1 + (n + 1 + (n // 4) % 3) % 4
        hp, ap = 95 + (n * 11) % 23, 90 + (n * 7) % 19
        store.league.game(
            f"l{n:03d}",
            (date(2025, 10, 25) + timedelta(days=n // 4)).isoformat(),
            home,
            away,
            hp,
            ap,
        )
        proj_home, proj_away = 98.0 + (n % 7), 96.0 - (n % 5)
        residual_pairs.append((hp - proj_home, ap - proj_away))
        store.session.add(
            locked_row(f"l{n:03d}", proj_home, proj_away, at("2025-10-25") + timedelta(days=n // 4))
        )
    store.league.commit()
    ctx = queries.build_context(store.session, SEASON, now=at("2026-06-01"))
    # 299 is not enough, 300 is.
    victim = store.session.execute(
        select(TeamProjectionLedger).where(TeamProjectionLedger.game_id == "l299")
    ).scalar_one()
    store.session.delete(victim)
    store.session.commit()
    assert ledger.ledger_residuals(store.session, ctx) is None
    store.session.add(locked_row("l299", 98.0 + (299 % 7), 96.0 - (299 % 5), at("2026-01-01")))
    store.session.commit()
    fit = ledger.ledger_residuals(store.session, ctx)
    assert fit is not None and fit[2] == 300
    team_sd = rms([r for pair in residual_pairs for r in pair])
    margin_sd = rms([h - a for h, a in residual_pairs])
    assert fit[0] == pytest.approx(team_sd, abs=1e-9) and fit[1] == pytest.approx(
        margin_sd, abs=1e-9
    )

    result = projection.calibrate(store.session, ctx, at("2026-06-01"))
    store.session.commit()
    assert result.source == "fittedLedger" and result.games == 300
    values = {s.key: s for s in intel_settings.get_all(store.session)}
    assert values["teamSd"].provenance == "fittedLedger"
    assert values["marginSd"].value == pytest.approx(margin_sd, abs=1e-9)
    # A ledger fit is never replaced by a previous-season one.
    queries.clear_memo()
    ctx = queries.build_context(store.session, SEASON, now=at("2026-06-01"))
    skipped = projection._store_spreads(
        store.session, ctx, 1.0, 1.0, "fittedPrevSeason", at("2026-06-02")
    )
    assert skipped == ("teamSd", "marginSd")


def test_the_calibration_job_reports_and_never_raises(worker_db: Any) -> None:
    with Session(worker_db, future=True) as session:
        assert projection.run_calibrate(now=NOW)["status"] == "skipped"  # no season loaded yet
        league = LeagueBuilder(session)
        league.team(1)
        league.team(2)
        league.game("g1", "2025-11-01", 1, 2, 100, 90)
        league.commit()
    queries.clear_memo()
    result = projection.run_calibrate(now=NOW)
    assert result["status"] == "skipped" and "previous season" in result["reason"]


# =========================================================================== eras and types


def test_a_season_before_1996_97_has_no_projections_and_says_why(seeded_db: Session) -> None:
    first = (
        seeded_db.execute(
            select(Game)
            .where(Game.season == "1992-93", Game.season_type == "Regular Season")
            .order_by(Game.game_date, Game.game_id)
        )
        .scalars()
        .first()
    )
    assert first is not None
    slate = nba_matchup.slate_projections(
        seeded_db, day=first.game_date.isoformat(), season="1992-93", now=NOW
    )
    assert slate["games"] == [] and slate["availability"] == "unavailable"
    assert any("predates" in note for note in slate["notes"])
    body = nba_matchup.team_matchup(seeded_db, game_id=first.game_id, now=NOW)
    assert body["projection"] is None
    assert any("Projections exist from 1996-97" in note for note in body["notes"])
    detail = nba_matchup.game_projection(seeded_db, first.game_id, now=NOW)
    assert detail["current"] is None and detail["locked"] is None
    assert detail["availability"] == "unavailable"
    assert any("Projections exist from 1996-97" in note for note in detail["notes"])


def test_playoff_games_are_projected_from_the_regular_seasons_ratings(store: Store) -> None:
    four_teams(store.league)
    add_rows(store.league, TEN_GAMES)
    store.league.game("p1", "2025-11-12", 1, 2, kind="Playoffs")
    store.league.game("x1", "2025-11-12", 3, 4, kind="Pre Season")
    store.league.commit()
    playoff_detail = nba_matchup.game_projection(store.session, "p1", now=NOW)
    playoff = playoff_detail["current"]
    assert playoff is not None
    assert any("projected from the regular season's ratings" in n for n in playoff["notes"])
    exhibition = nba_matchup.game_projection(store.session, "x1", now=NOW)
    assert exhibition["current"] is None  # a pre-season game is not a contest of full rosters
    assert exhibition["availability"] == "unavailable"
    assert exhibition["notes"] == ["Pre Season games are not projected."]
    assert playoff_detail["availability"] == "estimated" and playoff_detail["notes"] == []


def test_the_model_is_built_once_per_data_state(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    four_teams(store.league)
    add_rows(store.league, TEN_GAMES)
    store.league.commit()
    builds: list[int] = []
    original = projection.Model.build.__func__  # type: ignore[attr-defined]

    def counting(cls: Any, ctx: Any) -> Any:
        builds.append(1)
        return original(cls, ctx)

    monkeypatch.setattr(projection.Model, "build", classmethod(counting))
    first = projection.get_model(queries.build_context(store.session, SEASON, now=NOW))
    second = projection.get_model(queries.build_context(store.session, SEASON, now=NOW))
    assert first is second and len(builds) == 1
    store.league.game("a11", "2025-11-10", 1, 4, 99, 97)
    store.league.commit()
    third = projection.get_model(queries.build_context(store.session, SEASON, now=NOW))
    assert third is not first and len(builds) == 2


def test_the_seeded_store_projects_its_next_slate(seeded_db: Session) -> None:
    """The demo league end to end: the day after the newest final game has scheduled games, each
    with two projected scores, a margin, a winner or a toss-up, no probability, and a review."""
    now = at("2026-03-01", 16, 0)
    slate = nba_matchup.slate_projections(seeded_db, day="next", now=now)
    assert slate["games"], "the seeded league has games scheduled after its as-of date"
    assert slate["date"] is not None and date.fromisoformat(slate["date"]) > SEED_AS_OF - timedelta(
        days=1
    )
    for game in slate["games"]:
        assert game["availability"] == "estimated"
        home, away = game["home"]["projectedPoints"], game["away"]["projectedPoints"]
        assert 60 < away < 160 and 60 < home < 160
        assert game["margin"] == pytest.approx(home - away, abs=1e-9)
        assert game["combinedPoints"] == pytest.approx(home + away, abs=1e-9)
        assert game["isTossUp"] is (abs(game["margin"]) < 0.5)
        assert (game["projectedWinner"] is None) is game["isTossUp"]
        assert game["marginRange80"] is None  # the demo league never fits a spread
        assert game["venueAssumed"] is True
        assert game["model"]["kind"] == "latest"
        assert "winProbability" not in game and "probability" not in " ".join(_keys(game)).lower()
