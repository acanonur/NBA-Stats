"""Club ratings after each result: weights that continue from the season's start, no double count.

What the workbook does for two rounds by hand, the model does for every round, and the failures
worth catching are the ones the judges found in the design:

* the update weight restarts after the import (round 3 given round 1's weight);
* a game already inside the imported ratings is applied again;
* a projection made *after* a result is the one the result is measured against.

Each test names the rule it holds. The demo league is the fixture: ratings and player rates as of
round 2, rounds 3 and 4 final, round 5 to come.
"""

from __future__ import annotations

from datetime import timedelta, timezone

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from nbastats.euroleague.db import dispose_el_engine
from nbastats.euroleague.demo import DEMO_AS_OF
from nbastats.euroleague.model import ratings as ratings_module
from nbastats.euroleague.model.round_projection import get_model
from nbastats.euroleague.models import (
    ElGame,
    ElPlayerRate,
    ElProjectionLedger,
    ElTeamRating,
)
from nbastats.euroleague.read.queries import build_context, clear_memo, game_start
from nbastats.euroleague.settings import set_setting
from nbastats.shared.team_projection import update_adjustment

NOW = DEMO_AS_OF.replace(tzinfo=timezone.utc)


@pytest.fixture()
def session(fresh_demo_engine):
    with Session(fresh_demo_engine, future=True) as s:
        yield s


def model_of(session):
    clear_memo()
    ctx = build_context(session, None, now=NOW)
    return ctx, get_model(ctx)


def test_only_games_after_the_imported_round_update_anything(session) -> None:
    ctx, model = model_of(session)
    assert model.ratings.base_round == 2
    assert {u.round for u in model.ratings.updates} == {3, 4}
    assert len(model.ratings.updates) == 2 * 20  # two sides of ten games, two rounds
    # Rounds 1 and 2 are final games in the store too, and none of them is applied again.
    assert not [u for u in model.ratings.updates if ctx.games_by_id[u.game_id].round_number <= 2]


def test_the_game_number_counts_from_the_start_of_the_season(session) -> None:
    ctx, model = model_of(session)
    for u in model.ratings.updates:
        assert u.n == u.round, "every club has one game a round, so game n is round n"
    third = [u for u in model.ratings.updates if u.round == 3]
    assert {round(u.weight, 12) for u in third} == {
        round(1 / 12, 12)
    }  # 1 / (3 + 9), not round 1's 0.10
    assert all(u.extrapolated for u in third)
    fourth = [u for u in model.ratings.updates if u.round == 4]
    assert {round(u.weight, 12) for u in fourth} == {round(1 / 13, 12)}


def test_the_workbooks_own_weights_are_not_flagged_as_extrapolated(session) -> None:
    ctx, _ = model_of(session)
    assert ratings_module.weight_for(ctx, 1).weight == pytest.approx(0.1)
    assert not ratings_module.weight_for(ctx, 1).extrapolated
    assert not ratings_module.weight_for(ctx, 2).extrapolated
    assert ratings_module.weight_for(ctx, 3).extrapolated
    # Past the forty-row table the default formula still applies, and says so.
    beyond = ratings_module.weight_for(ctx, 50)
    assert beyond.weight == pytest.approx(1 / 59) and beyond.extrapolated


def test_a_weight_the_user_sets_is_used_and_is_not_extrapolated(session) -> None:
    set_setting(session, "roundWeight.3", 0.2, "manual")
    session.commit()
    ctx, model = model_of(session)
    third = [u for u in model.ratings.updates if u.round == 3]
    assert {u.weight for u in third} == {0.2}
    assert not any(u.extrapolated for u in third)


def test_the_update_is_the_workbooks_rule_applied_literally(session) -> None:
    ctx, model = model_of(session)
    base = model.ratings.snapshots[2]
    after = model.ratings.snapshots[3]
    for u in [u for u in model.ratings.updates if u.round == 3]:
        want_attack = update_adjustment(
            base[u.club_code].attack_adj, u.scored, u.projected_scored, u.weight
        )
        want_defence = update_adjustment(
            base[u.club_code].defence_adj, u.allowed, u.projected_allowed, u.weight
        )
        assert after[u.club_code].attack_adj == pytest.approx(want_attack, abs=1e-12)
        assert after[u.club_code].defence_adj == pytest.approx(want_defence, abs=1e-12)
        assert after[u.club_code].update_weight == u.weight
        assert after[u.club_code].update_basis == "reconstructed"
        # The priors never move: only the adjustments do.
        assert after[u.club_code].pf_prior == base[u.club_code].pf_prior


def test_a_round_is_measured_against_the_previous_rounds_ratings(session) -> None:
    ctx, model = model_of(session)
    game = next(g for g in ctx.games if g.round_number == 4 and g.stats_status == "ok")
    update_row = next(
        u
        for u in model.ratings.updates
        if u.game_id == game.game_id and u.club_code == game.home_club_code
    )
    from_round3 = model.project(
        ctx,
        game,
        as_of=game_start(game) - timedelta(seconds=1),
        kind="reconstructed",
        state_round=3,
    )
    from_round2 = model.project(
        ctx,
        game,
        as_of=game_start(game) - timedelta(seconds=1),
        kind="reconstructed",
        state_round=2,
    )
    assert update_row.projected_scored == pytest.approx(from_round3.match.home_points)
    assert update_row.projected_scored != pytest.approx(from_round2.match.home_points)


def test_a_locked_projection_is_used_instead_of_reconstructing(session) -> None:
    ctx, model = model_of(session)
    game = next(g for g in ctx.games if g.round_number == 3 and g.stats_status == "ok")
    tip = game.tipoff_utc
    session.add(
        ElProjectionLedger(
            game_id=game.game_id,
            kind="locked",
            model_version="el-1",
            computed_at=tip - timedelta(minutes=30),
            inputs_cutoff=tip - timedelta(minutes=30),
            home_pts=70.0,
            away_pts=75.0,
            home_full_strength=70.0,
            away_full_strength=75.0,
            home_attack_index_after_availability=1.0,
            away_attack_index_after_availability=1.0,
            home_defence_index=1.0,
            away_defence_index=1.0,
            home_advantage_points=3.5,
            cap_policy="consistent",
            settings_sha256="x",
            inputs_sha256="y",
        )
    )
    session.commit()
    ctx, model = model_of(session)
    here = {u.club_code: u for u in model.ratings.updates if u.game_id == game.game_id}
    assert here[game.home_club_code].projected_scored == 70.0
    assert here[game.home_club_code].projected_allowed == 75.0
    assert here[game.away_club_code].projected_scored == 75.0
    assert {u.basis for u in here.values()} == {"locked"}
    others = [u for u in model.ratings.updates if u.round == 3 and u.game_id != game.game_id]
    assert {u.basis for u in others} == {"reconstructed"}
    assert game.game_id not in model.reconstructed  # nothing was rebuilt for a locked game


def test_reconstruction_uses_only_inputs_published_before_tip_off(session) -> None:
    """A status published after a round-3 game cannot have changed the projection the miss used."""
    ctx, model = model_of(session)
    game = next(g for g in ctx.games if g.round_number == 3 and g.stats_status == "ok")
    result = model.reconstructed[game.game_id]
    assert result.as_of < game.tipoff_utc.replace(tzinfo=timezone.utc)
    assert result.as_of == game.tipoff_utc.replace(tzinfo=timezone.utc) - timedelta(seconds=1)
    assert result.kind == "reconstructed"


def test_overtime_actuals_are_restated_to_regulation_length(session) -> None:
    game = (
        session.execute(
            select(ElGame)
            .where(ElGame.round_number == 3, ElGame.stats_status == "ok")
            .order_by(ElGame.game_id)
        )
        .scalars()
        .first()
    )
    session.execute(update(ElGame).where(ElGame.game_id == game.game_id).values(ot_periods=1))
    session.commit()
    ctx, model = model_of(session)
    home = next(
        u
        for u in model.ratings.updates
        if u.game_id == game.game_id and u.club_code == game.home_club_code
    )
    assert home.scored == pytest.approx(game.home_pts * 12000 / 13500)
    assert home.allowed == pytest.approx(game.away_pts * 12000 / 13500)
    set_setting(session, "overtimeScaling", 0, "manual")
    session.commit()
    ctx, model = model_of(session)
    home = next(
        u
        for u in model.ratings.updates
        if u.game_id == game.game_id and u.club_code == game.home_club_code
    )
    assert home.scored == game.home_pts  # the workbook's own behaviour, on request


def test_regulation_actual_helper_is_exact_and_leaves_regulation_games_alone() -> None:
    assert ratings_module.regulation_actual(90, 0, True) == 90.0
    assert ratings_module.regulation_actual(90, None, True) == 90.0
    assert ratings_module.regulation_actual(95, 1, True) == pytest.approx(95 * 12000 / 13500)
    assert ratings_module.regulation_actual(105, 2, True) == pytest.approx(105 * 12000 / 15000)
    assert ratings_module.regulation_actual(95, 1, False) == 95.0


def test_the_league_level_is_the_mean_of_the_priors_and_fixed_for_the_season(session) -> None:
    ctx, model = model_of(session)
    priors = [r.pf_prior for r in model.ratings.snapshots[2].values()]
    assert model.ratings.league_level == pytest.approx(sum(priors) / len(priors))
    assert model.ratings.regression == 0.3
    # Round 4 ratings: same L (the league level never moves with results).
    assert ratings_module.league_level(model.ratings.snapshots[4]) == pytest.approx(
        model.ratings.league_level
    )


def test_persisting_writes_completed_rounds_once(session) -> None:
    ctx, model = model_of(session)
    written = ratings_module.persist_book(session, ctx, model.ratings, NOW)
    session.commit()
    assert written == 40  # twenty clubs, rounds 3 and 4
    rows = (
        session.execute(select(ElTeamRating).where(ElTeamRating.source == "roundUpdate"))
        .scalars()
        .all()
    )
    assert {r.as_of_round for r in rows} == {3, 4}
    assert {r.update_basis for r in rows} == {"reconstructed"}
    assert all(r.update_weight in (1 / 12, 1 / 13) for r in rows)
    again = ratings_module.persist_book(session, ctx, model.ratings, NOW)
    assert again == 0


def test_an_incomplete_round_is_not_persisted(session) -> None:
    ctx, _ = model_of(session)
    victim = next(g for g in ctx.games if g.round_number == 4)
    session.execute(
        update(ElGame)
        .where(ElGame.game_id == victim.game_id)
        .values(status="scheduled", stats_status="none", home_pts=None, away_pts=None)
    )
    session.commit()
    ctx, model = model_of(session)
    ratings_module.persist_book(session, ctx, model.ratings, NOW)
    session.commit()
    rounds = {
        r.as_of_round
        for r in session.execute(
            select(ElTeamRating).where(ElTeamRating.source == "roundUpdate")
        ).scalars()
    }
    assert rounds == {3}  # round 4 is provisional until its last game is in


def test_stored_round_update_rows_are_never_taken_as_the_base(session) -> None:
    ctx, model = model_of(session)
    ratings_module.persist_book(session, ctx, model.ratings, NOW)
    session.commit()
    clear_memo()
    ctx2 = build_context(session, None, now=NOW)
    base_round, base = ratings_module.load_base_ratings(ctx2)
    assert base_round == 2 and {r.source for r in base.values()} == {"syntheticDemo"}
    again = get_model(ctx2)
    assert again.ratings.snapshots[4]["ZZA"].attack_adj == pytest.approx(
        model.ratings.snapshots[4]["ZZA"].attack_adj
    )


def test_a_newer_import_replaces_the_base_and_only_later_rounds_update(session) -> None:
    """Rows as of round 3 (ratings *and* rates) make round 3 part of the base: no double count."""
    for rating in (
        session.execute(select(ElTeamRating).where(ElTeamRating.as_of_round == 2)).scalars().all()
    ):
        session.add(
            ElTeamRating(
                season_code=rating.season_code,
                club_code=rating.club_code,
                as_of_round=3,
                pf_prior=rating.pf_prior,
                pa_prior=rating.pa_prior,
                prior_is_estimate=rating.prior_is_estimate,
                attack_adj=rating.attack_adj + 1.0,
                defence_adj=rating.defence_adj,
                source="workbookImport",
                computed_at=NOW.replace(tzinfo=None),
            )
        )
    for rate in (
        session.execute(select(ElPlayerRate).where(ElPlayerRate.as_of_round == 2)).scalars().all()
    ):
        session.add(
            ElPlayerRate(
                season_code=rate.season_code,
                person_code=rate.person_code,
                as_of_round=3,
                club_code=rate.club_code,
                proj_minutes=rate.proj_minutes,
                basis=rate.basis,
                prior_minutes=rate.prior_minutes,
                pts40=rate.pts40,
                reb40=rate.reb40,
                ast40=rate.ast40,
                fg3m40=rate.fg3m40,
                stl40=rate.stl40,
                blk40=rate.blk40,
                tov40=rate.tov40,
                fga2_40=rate.fga2_40,
                fg2_pct=rate.fg2_pct,
                fga3_40=rate.fga3_40,
                fg3_pct=rate.fg3_pct,
                fta40=rate.fta40,
                ft_pct=rate.ft_pct,
            )
        )
    session.commit()
    ctx, model = model_of(session)
    assert model.ratings.base_round == 3
    assert {u.round for u in model.ratings.updates} == {4}
    assert {u.n for u in model.ratings.updates} == {4}  # still the club's fourth game of the season
    assert model.ratings.snapshots[3]["ZZA"].attack_adj == pytest.approx(
        session.get(ElTeamRating, ("E2026", "ZZA", 3)).attack_adj
    )
    # The players' base moved with it: round 3's game is inside it, not added on top.
    states = model.players[3]
    rate = session.get(ElPlayerRate, ("E2026", next(iter(states)), 3))
    assert states[rate.person_code].rates["pts40"] == rate.pts40
    assert states[rate.person_code].club_games_after == 0


def test_the_worker_job_rates_completed_rounds_and_records_the_reconstructions(
    fresh_demo_engine, monkeypatch
) -> None:
    monkeypatch.setenv("HARDWOOD_EL_DATABASE_URL", str(fresh_demo_engine.url))
    dispose_el_engine()
    clear_memo()
    first = ratings_module.run_ratings(now=NOW)
    assert first["status"] == "ok"
    assert first["ratingRows"] == 40 and first["reconstructed"] == 20
    with Session(fresh_demo_engine, future=True) as s:
        rows = (
            s.execute(select(ElProjectionLedger).where(ElProjectionLedger.kind == "reconstructed"))
            .scalars()
            .all()
        )
        assert len(rows) == 20
        for row in rows:
            game = s.get(ElGame, row.game_id)
            assert row.inputs_cutoff < game.tipoff_utc  # the schema's rule for a reconstruction
            assert row.computed_at == NOW.replace(
                tzinfo=None
            )  # when it was rebuilt, not when it tipped
    second = ratings_module.run_ratings(now=NOW)
    assert second["status"] == "skipped"
    assert second["ratingRows"] == 0 and second["reconstructed"] == 0


def test_the_worker_job_skips_when_there_is_nothing_to_rate(tmp_path, monkeypatch) -> None:
    from nbastats.euroleague.db import create_el_engine, init_el_db

    engine = create_el_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    init_el_db(engine)
    monkeypatch.setenv("HARDWOOD_EL_DATABASE_URL", str(engine.url))
    dispose_el_engine()
    result = ratings_module.run_ratings(now=NOW)
    assert result["status"] == "skipped"
    engine.dispose()
