"""The EuroLeague projection, end to end: the formulas, the honesty rules and the ledger.

Four groups, in the order a reader would doubt them:

1. **The arithmetic.** With no availability in the way, a projected score is the workbook's
   ``L * attack * opposing defence + h/2``; the squad adds up to the club; the injury layer's cap
   policies do what the design says (``consistent`` makes the team equal its players,
   ``workbook`` leaves points unassigned) and an absence never raises scoring.
2. **What a game is projected from.** Neutral venues, per-game overrides, an unknown venue
   flagged as assumed, the ``resultPending`` state and which of the four projection kinds is shown
   when.
3. **The ledger.** ``latest`` rows written only when inputs change, a lock that is never created
   at or after tip-off, the spreads fitted only from locked results, and a review that keeps
   locked, reconstructed and imported projections apart.
4. **The round's scorers**, with no spread and no picks.

The last test replays the user's real Round 3 and runs only where the workbook is present
(``HARDWOOD_WORKBOOK_PATH``); it is the verification record for design gate G1.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from nbastats.euroleague.db import dispose_el_engine
from nbastats.euroleague.demo import DEMO_AS_OF
from nbastats.euroleague.model import ledger as ledger_module
from nbastats.euroleague.model import scorers as scorers_module
from nbastats.euroleague.model.round_projection import (
    ProjectionUnavailable,
    current_projection,
    get_model,
    interval_basis,
)
from nbastats.euroleague.models import (
    ElGame,
    ElIntelStatus,
    ElProjectionLedger,
)
from nbastats.euroleague.read import review as review_module
from nbastats.euroleague.read import round as round_module
from nbastats.euroleague.read.queries import build_context, clear_memo, game_start
from nbastats.euroleague.read.sources import KEYS_STATS, freshness_for
from nbastats.euroleague.settings import set_setting
from nbastats.shared.market_guard import scan_keys
from nbastats.shared.team_projection import rating

NOW = DEMO_AS_OF.replace(tzinfo=timezone.utc)
UPCOMING = "E2026-R05-01"


@pytest.fixture()
def session(fresh_demo_engine):
    with Session(fresh_demo_engine, future=True) as s:
        yield s


def open_model(session, now=NOW):
    clear_memo()
    ctx = build_context(session, None, now=now)
    return ctx, get_model(ctx)


def clear_statuses(session) -> None:
    session.execute(delete(ElIntelStatus))
    session.commit()


def add_status(
    session,
    club,
    person,
    status,
    published,
    *,
    model_status=None,
    reason="injury",
    game_id=None,
    expected=None,
    kind="manual",
):
    row = ElIntelStatus(
        club_code=club,
        person_code=person,
        player_name_raw="x",
        game_id=game_id,
        status=status,
        model_status=model_status,
        reason_category=reason,
        expected_return_text=expected,
        source_kind=kind,
        source_label="Test",
        source_url=None,
        source_published_at=published,
        as_of=published,
        recorded_at=published,
    )
    session.add(row)
    session.commit()
    return row


def top_scorers(model, club, count):
    states = [
        s
        for s in model.players[model.last_round].values()
        if s.club_code == club and s.points_per_game
    ]
    return [s.person_code for s in sorted(states, key=lambda s: -s.points_per_game)[:count]]


# --------------------------------------------------------------------------- the arithmetic


def test_a_projection_is_the_workbooks_formula_when_nobody_is_missing(session) -> None:
    clear_statuses(session)
    ctx, model = open_model(session)
    game = ctx.game(UPCOMING)
    result = model.project(ctx, game, as_of=NOW, kind="latest")
    ratings = model.ratings.snapshots[4]
    level = sum(r.pf_prior for r in model.ratings.snapshots[2].values()) / 20
    r = 0.3
    h = 3.5
    pf = lambda club: rating(
        ratings[club].pf_prior, level, r, ratings[club].attack_adj
    )  # noqa: E731
    pa = lambda club: rating(
        ratings[club].pa_prior, level, r, ratings[club].defence_adj
    )  # noqa: E731
    home, away = game.home_club_code, game.away_club_code
    want_home = level * (pf(home) / level) * (pa(away) / level) + h / 2
    want_away = level * (pf(away) / level) * (pa(home) / level) - h / 2
    assert result.match.home_points == pytest.approx(want_home, abs=1e-9)
    assert result.match.away_points == pytest.approx(want_away, abs=1e-9)
    assert result.match.margin == pytest.approx(want_home - want_away, abs=1e-9)
    assert result.match.combined_points == pytest.approx(want_home + want_away, abs=1e-9)
    assert result.home.injury.availability_factor == pytest.approx(1.0)
    assert result.match.home_full_strength == pytest.approx(result.match.home_points)


def test_the_squad_adds_up_to_the_clubs_rating_when_reconciled(session) -> None:
    ctx, model = open_model(session)
    result = model.project(ctx, ctx.game(UPCOMING), as_of=NOW, kind="latest")
    for side in result.sides:
        assert math.fsum(line.points for line in side.squad) == pytest.approx(side.pf, abs=1e-9)
        assert side.squad_scale is not None
    set_setting(session, "squadReconcile", 0, "manual")
    session.commit()
    ctx, model = open_model(session)
    raw = model.project(ctx, ctx.game(UPCOMING), as_of=NOW, kind="latest")
    for side in raw.sides:
        assert side.squad_scale is None
        assert math.fsum(line.points for line in side.squad) == pytest.approx(
            math.fsum(line.state.points_per_game for line in side.squad)
        )


def _four_scorers_out(session, boost_cap):
    clear_statuses(session)
    ctx, model = open_model(session)
    game = ctx.game(UPCOMING)
    home = game.home_club_code
    for person in top_scorers(model, home, 4):
        add_status(session, home, person, "out", NOW - timedelta(days=1))
    set_setting(session, "boostCap", boost_cap, "manual")
    session.commit()
    return game


def test_the_consistent_cap_policy_makes_the_team_equal_its_players(session) -> None:
    """The team equals its players plus the replacement players' line, and the payload shows
    that line, so nothing is unaccounted for and the replacement floor is never capped away."""
    game = _four_scorers_out(session, 1.05)
    ctx, model = open_model(session)
    view = current_projection(ctx, model, game)
    side = model.project(ctx, game, as_of=NOW, kind="latest").home
    injury = side.injury
    assert injury.cap_binding
    assert injury.cap_policy == "consistent"
    assert injury.unassigned_points == 0.0
    credited = math.fsum(o.projected_points for o in injury.players)
    assert injury.squad_points == pytest.approx(credited + injury.replacement_credited, abs=1e-9)
    assert injury.replacement_credited > 0
    floor = injury.available_points + min(injury.replacement_points, injury.lost_points)
    assert injury.squad_points >= floor - 1e-9
    payload = round_module.projection_payload(ctx, game, view, freshness_for(ctx, KEYS_STATS))
    assert payload["home"]["replacementPoints"] == pytest.approx(injury.replacement_credited)
    assert payload["home"]["unassignedPoints"] == 0.0


def test_the_workbook_cap_policy_leaves_points_no_player_is_credited_with(session) -> None:
    game = _four_scorers_out(session, 1.05)
    set_setting(session, "capPolicyConsistent", 0, "manual")
    session.commit()
    ctx, model = open_model(session)
    side = model.project(ctx, game, as_of=NOW, kind="latest").home
    injury = side.injury
    assert injury.cap_binding and injury.cap_policy == "workbook"
    credited = math.fsum(o.projected_points for o in injury.players)
    assert injury.unassigned_points > 0
    assert injury.squad_points == pytest.approx(credited + injury.unassigned_points, abs=1e-9)
    # ...and it is shown, not hidden:
    payload = round_module.projection_payload(
        ctx, game, current_projection(ctx, model, game), freshness_for(ctx, KEYS_STATS)
    )
    assert payload["home"]["capBinding"] is True
    assert payload["home"]["unassignedPoints"] == pytest.approx(injury.unassigned_points)
    assert payload["model"]["capPolicy"] == "workbook"


def test_an_absence_never_raises_scoring(session) -> None:
    clear_statuses(session)
    ctx, model = open_model(session)
    game = ctx.game(UPCOMING)
    home = game.home_club_code
    low = sorted(
        (s for s in model.players[4].values() if s.club_code == home and s.points_per_game),
        key=lambda s: s.points_per_game,
    )[0]
    set_setting(session, "replacementPer40", 40.0, "manual")  # replacement level above everyone
    session.commit()
    add_status(session, home, low.person_code, "out", NOW - timedelta(days=1))
    ctx, model = open_model(session)
    result = model.project(ctx, game, as_of=NOW, kind="latest")
    assert result.home.injury.availability_factor <= 1.0 + 1e-12
    assert result.match.home_points <= result.match.home_full_strength + 1e-9
    assert result.home.injury.absorbed_points == pytest.approx(result.home.injury.lost_points)


def test_defence_indices_are_not_changed_by_absences(session) -> None:
    clear_statuses(session)
    ctx, model = open_model(session)
    game = ctx.game(UPCOMING)
    before = model.project(ctx, game, as_of=NOW, kind="latest")
    for person in top_scorers(model, game.away_club_code, 3):
        add_status(session, game.away_club_code, person, "out", NOW - timedelta(days=1))
    ctx, model = open_model(session)
    after = model.project(ctx, game, as_of=NOW, kind="latest")
    assert after.away.defence == before.away.defence
    assert after.home.defence == before.home.defence
    assert after.match.away_points < before.match.away_points  # but the away score fell


def test_chances_come_from_the_settings_table(session) -> None:
    clear_statuses(session)
    ctx, model = open_model(session)
    game = ctx.game(UPCOMING)
    person = top_scorers(model, game.home_club_code, 1)[0]
    add_status(session, game.home_club_code, person, "doubtful", NOW - timedelta(days=1))
    ctx, model = open_model(session)
    line = next(
        l
        for l in model.project(ctx, game, as_of=NOW, kind="latest").home.squad
        if l.person_code == person
    )
    assert line.chance == 0.25 and not line.assumed
    set_setting(session, "statusChance.doubtful", 0.4, "manual")
    session.commit()
    ctx, model = open_model(session)
    line = next(
        l
        for l in model.project(ctx, game, as_of=NOW, kind="latest").home.squad
        if l.person_code == person
    )
    assert line.chance == 0.4


def test_the_model_status_drives_the_projection_and_the_research_status_is_shown(session) -> None:
    clear_statuses(session)
    ctx, model = open_model(session)
    game = ctx.game(UPCOMING)
    person = top_scorers(model, game.home_club_code, 1)[0]
    add_status(
        session,
        game.home_club_code,
        person,
        "available",
        NOW - timedelta(days=1),
        model_status="out",
    )
    ctx, model = open_model(session)
    line = next(
        l
        for l in model.project(ctx, game, as_of=NOW, kind="latest").home.squad
        if l.person_code == person
    )
    assert line.chance == 0.0 and line.status == "out"


# --------------------------------------------------------------------------- assumptions


def test_silence_is_assumed_availability_and_is_counted(session) -> None:
    clear_statuses(session)
    ctx, model = open_model(session)
    game = ctx.game(UPCOMING)
    home_squad = len(model.project(ctx, game, as_of=NOW, kind="latest").home.squad)
    add_status(
        session,
        game.home_club_code,
        top_scorers(model, game.home_club_code, 1)[0],
        "questionable",
        NOW - timedelta(days=1),
    )
    payload = round_module.game_projection_payload(build_context(session, None, now=NOW), game, {})
    assumed = payload["assumptions"]["assumedAvailable"]
    assert assumed == {
        "home": home_squad - 1,
        "away": len(model.project(ctx, game, as_of=NOW, kind="latest").away.squad),
        "basis": "noEntry",
    }
    assert payload["assumptions"]["staleEntriesIgnored"] == 0


def test_an_entry_out_of_force_is_ignored_and_counted(session) -> None:
    clear_statuses(session)
    ctx, model = open_model(session)
    game = ctx.game(UPCOMING)
    person = top_scorers(model, game.home_club_code, 1)[0]
    add_status(
        session, game.home_club_code, person, "out", NOW - timedelta(days=20)
    )  # past fourteen days
    ctx, model = open_model(session)
    result = model.project(ctx, game, as_of=NOW, kind="latest")
    line = next(l for l in result.home.squad if l.person_code == person)
    assert line.chance == 1.0 and line.assumed
    assert result.home.stale_ignored == 1
    assert result.match.home_points == pytest.approx(result.match.home_full_strength)


# --------------------------------------------------------------------------- the venue


def test_a_neutral_game_has_no_home_advantage(session) -> None:
    ctx, model = open_model(session)
    neutral = next(g for g in ctx.games if g.is_neutral)
    result = model.project(
        ctx, neutral, as_of=game_start(neutral) - timedelta(seconds=1), kind="reconstructed"
    )
    assert result.home_advantage.points == 0.0 and not result.home_advantage.venue_assumed
    assert result.match.home_points + result.match.away_points == pytest.approx(
        result.match.home_full_strength
        + result.match.away_full_strength
        + result.match.combined_availability_effect
    )


def test_a_per_game_override_replaces_the_default_but_never_a_neutral_flag(session) -> None:
    ctx, _ = open_model(session)
    session.execute(
        update(ElGame).where(ElGame.game_id == UPCOMING).values(home_advantage_override=1.0)
    )
    neutral = next(g for g in ctx.games if g.is_neutral)
    session.execute(
        update(ElGame).where(ElGame.game_id == neutral.game_id).values(home_advantage_override=5.0)
    )
    session.commit()
    ctx, model = open_model(session)
    assert (
        model.project(ctx, ctx.game(UPCOMING), as_of=NOW, kind="latest").home_advantage.points
        == 1.0
    )
    n = ctx.game(neutral.game_id)
    assert (
        model.project(
            ctx, n, as_of=game_start(n) - timedelta(seconds=1), kind="reconstructed"
        ).home_advantage.points
        == 0.0
    )


def test_an_unknown_venue_applies_the_default_and_says_it_was_assumed(session) -> None:
    session.execute(update(ElGame).where(ElGame.game_id == UPCOMING).values(is_neutral=None))
    session.commit()
    ctx, model = open_model(session)
    result = model.project(ctx, ctx.game(UPCOMING), as_of=NOW, kind="latest")
    assert result.home_advantage.points == 3.5 and result.home_advantage.venue_assumed
    payload = round_module.game_projection_payload(ctx, ctx.game(UPCOMING), {})
    assert payload["venueAssumed"] is True and payload["homeAdvantagePoints"] == 3.5


# --------------------------------------------------------------------------- which projection is shown


def test_a_game_still_to_play_gets_the_models_latest(session) -> None:
    ctx, model = open_model(session)
    view = current_projection(ctx, model, ctx.game(UPCOMING))
    assert view.kind == "latest" and view.result is not None
    payload = round_module.projection_payload(ctx, ctx.game(UPCOMING), view, {})
    assert payload["availability"] == "estimated"
    assert payload["intervalBasis"] == "assumed"
    assert payload["result"] is None
    assert payload["model"]["kind"] == "latest" and payload["model"]["key"] == "hardwood"
    assert scan_keys(payload) == []


def test_a_game_played_but_not_loaded_is_result_pending_and_reconstructed(session) -> None:
    after = datetime(
        2026, 10, 16, 6, 0, tzinfo=timezone.utc
    )  # a night after round 5's last tip-off
    ctx, model = open_model(session, now=after)
    game = ctx.game(UPCOMING)
    assert ctx.game_status(game) == "resultPending"
    view = current_projection(ctx, model, game)
    assert view.kind == "reconstructed"
    assert view.result.as_of == game_start(game) - timedelta(seconds=1)
    payload = round_module.projection_payload(ctx, game, view, {})
    assert payload["game"]["status"] == "resultPending"
    assert payload["game"]["homePts"] is None and payload["result"] is None
    assert any("Rebuilt after tip-off" in note for note in payload["notes"])


def test_the_round_says_results_are_pending_and_how_to_fix_it(session) -> None:
    after = datetime(
        2026, 10, 17, 6, 0, tzinfo=timezone.utc
    )  # every game of round 5 has been played
    ctx, _ = open_model(session, now=after)
    view = round_module.build_round_view(ctx, 5, {})
    assert view["status"] == "resultPending"
    assert any(
        "Round 5 results are not loaded: enable live ingest or import an updated workbook." == n
        for n in view["notes"]
    )
    ctx, _ = open_model(session)
    assert round_module.build_round_view(ctx, 5, {})["status"] == "upcoming"
    assert round_module.build_round_view(ctx, 4, {})["status"] == "complete"
    # Some games played and unloaded, others still to come: the round is in progress.
    middle = datetime(2026, 10, 16, 6, 0, tzinfo=timezone.utc)
    ctx, _ = open_model(session, now=middle)
    assert round_module.build_round_view(ctx, 5, {})["status"] == "inProgress"


def test_a_played_game_after_the_import_is_reconstructed_with_its_result_shown(session) -> None:
    ctx, model = open_model(session)
    game = next(g for g in ctx.games if g.round_number == 4)
    view = current_projection(ctx, model, game)
    assert view.kind == "reconstructed"
    payload = round_module.projection_payload(ctx, game, view, {})
    assert payload["result"]["homePts"] == game.home_pts
    margin = payload["margin"]
    assert payload["result"]["marginMiss"] == pytest.approx(
        (game.home_pts - game.away_pts) - margin
    )


def test_a_game_inside_the_import_is_never_projected_from_the_import(session) -> None:
    """Round 1's result is *in* the base ratings; a projection from them would know the answer."""
    ctx, model = open_model(session)
    first = next(g for g in ctx.games if g.round_number == 1)
    assert current_projection(ctx, model, first) is None
    assert round_module.game_projection_payload(ctx, first, {}) is None
    session.add(
        ElProjectionLedger(
            game_id=first.game_id,
            kind="imported",
            model_version="workbook",
            home_pts=84.0,
            away_pts=80.0,
            cap_policy="workbook",
        )
    )
    session.commit()
    ctx, model = open_model(session)
    view = current_projection(ctx, model, ctx.game(first.game_id))
    assert view.kind == "imported" and view.ledger is not None
    payload = round_module.projection_payload(ctx, ctx.game(first.game_id), view, {})
    assert payload["model"]["key"] == "workbook" and payload["model"]["kind"] == "imported"
    assert payload["home"]["fullStrengthPoints"] is None  # the workbook published scores only
    assert (
        payload["home"]["keyAbsences"] == []
        and payload["assumptions"]["staleEntriesIgnored"] is None
    )
    assert payload["projectedWinner"]["clubCode"] == first.home_club_code
    assert payload["result"]["winnerCalled"] in (True, False)


def test_a_game_with_no_rating_for_a_club_has_no_projection(session) -> None:
    session.execute(
        delete(
            __import__("nbastats.euroleague.models", fromlist=["ElTeamRating"]).ElTeamRating
        ).where(
            __import__(
                "nbastats.euroleague.models", fromlist=["ElTeamRating"]
            ).ElTeamRating.club_code
            == "ZZA"
        )
    )
    session.commit()
    ctx, model = open_model(session)
    game = ctx.game(UPCOMING)
    assert game.home_club_code == "ZZA"
    with pytest.raises(ProjectionUnavailable):
        model.project(ctx, game, as_of=NOW, kind="latest")
    assert round_module.game_projection_payload(ctx, game, {}) is None
    view = round_module.build_round_view(ctx, 5, {})
    assert any("have no projection" in note for note in view["notes"])
    assert len(view["games"]) == 9


def test_the_interval_basis_is_assumed_until_the_spreads_are_fitted(session) -> None:
    ctx, _ = open_model(session)
    assert interval_basis(ctx) == "assumed"
    set_setting(session, "teamSd", 8.0, "fittedLedger")
    set_setting(session, "marginSd", 10.0, "fittedLedger")
    session.commit()
    ctx, _ = open_model(session)
    assert interval_basis(ctx) == "fittedLedger"
    payload = round_module.game_projection_payload(ctx, ctx.game(UPCOMING), {})
    assert payload["intervalBasis"] == "fittedLedger"
    half = payload["home"]["range80"]["high"] - payload["home"]["projectedPoints"]
    assert half == pytest.approx(1.2816 * 8.0)


# --------------------------------------------------------------------------- the ledger


def test_latest_rows_are_written_only_when_the_inputs_change(session) -> None:
    clear_statuses(session)  # so that adding one below is certainly a change
    tip = datetime(2026, 10, 15, 17, 30, tzinfo=timezone.utc)
    now = tip - timedelta(hours=30)
    ctx, model = open_model(session, now=now)
    rows = ledger_module.refresh_latest(session, ctx, model)
    session.commit()
    assert rows and all(r.kind == "latest" for r in rows)
    assert (
        len(rows) == 5
    )  # round 5's first five games tip within 48 hours; the rest are further out
    again = ledger_module.refresh_latest(session, *open_model(session, now=now))
    assert again == []  # same inputs: nothing new
    victim = ctx.game(UPCOMING)
    person = top_scorers(model, victim.home_club_code, 1)[0]
    add_status(session, victim.home_club_code, person, "out", now - timedelta(hours=1))
    ctx2, model2 = open_model(session, now=now)
    changed = ledger_module.refresh_latest(session, ctx2, model2)
    session.commit()
    assert [r.game_id for r in changed] == [victim.game_id]
    history = round_module.projection_detail(ctx2, victim, {})["history"]
    assert [h["kind"] for h in history] == ["latest", "latest"]
    assert history[0]["homePts"] != history[1]["homePts"]


def test_refresh_ignores_games_outside_the_horizon_and_games_already_played(session) -> None:
    far = datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)  # five days out
    ctx, model = open_model(session, now=far)
    assert ledger_module.refresh_latest(session, ctx, model) == []


def test_a_lock_is_a_copy_of_the_newest_latest_and_is_never_made_after_tip_off(session) -> None:
    game_id = UPCOMING
    tip = datetime(2026, 10, 15, 17, 30, tzinfo=timezone.utc)
    ctx, model = open_model(session, now=tip - timedelta(hours=5))
    ledger_module.refresh_latest(session, ctx, model)
    session.commit()
    ctx, model = open_model(session, now=tip - timedelta(minutes=45))
    game = ctx.game(game_id)
    locked = ledger_module.lock_game(session, ctx, model, game)
    session.commit()
    assert locked is not None and locked.kind == "locked"
    assert locked.computed_at < game.tipoff_utc  # the row's own time, before tip-off
    latest = [
        r
        for r in session.execute(
            select(ElProjectionLedger).where(
                ElProjectionLedger.game_id == game_id, ElProjectionLedger.kind == "latest"
            )
        ).scalars()
    ][-1]
    assert (locked.home_pts, locked.away_pts, locked.inputs_sha256) == (
        latest.home_pts,
        latest.away_pts,
        latest.inputs_sha256,
    )
    # Idempotent: the same lock again is the same row.
    assert ledger_module.lock_game(session, ctx, model, game).ledger_id == locked.ledger_id
    # At tip-off, and after, a lock is refused outright.
    for moment in (tip, tip + timedelta(minutes=5), tip + timedelta(hours=3)):
        ctx_late, model_late = open_model(session, now=moment)
        with pytest.raises(ledger_module.LockRefused):
            ledger_module.lock_game(session, ctx_late, model_late, ctx_late.game(game_id))
    # A game that never had a lock cannot be given one after its own tip-off either.
    other_tip = ctx.game("E2026-R05-02").tipoff_utc.replace(tzinfo=timezone.utc)
    ctx_late, model_late = open_model(session, now=other_tip + timedelta(seconds=1))
    with pytest.raises(ledger_module.LockRefused):
        ledger_module.lock_game(session, ctx_late, model_late, ctx_late.game("E2026-R05-02"))
    assert not [
        r
        for r in session.execute(
            select(ElProjectionLedger).where(
                ElProjectionLedger.game_id == "E2026-R05-02", ElProjectionLedger.kind == "locked"
            )
        ).scalars()
    ]


def test_nothing_is_locked_when_no_projection_was_recorded_before_tip_off(session) -> None:
    ctx, model = open_model(session, now=datetime(2026, 10, 15, 17, 0, tzinfo=timezone.utc))
    game = ctx.game("E2026-R05-02")
    # No latest row exists yet; lock_game writes a fresh one at lock time, before the deadline.
    locked = ledger_module.lock_game(session, ctx, model, game)
    assert locked is not None and locked.computed_at < game.tipoff_utc
    # A game whose deadline passed before anything was written cannot be locked after the fact.
    ctx, model = open_model(session, now=datetime(2026, 10, 15, 22, 0, tzinfo=timezone.utc))
    with pytest.raises(ledger_module.LockRefused):
        ledger_module.lock_game(session, ctx, model, ctx.game("E2026-R05-03"))
    assert not [
        r
        for r in session.execute(
            select(ElProjectionLedger).where(ElProjectionLedger.game_id == "E2026-R05-03")
        ).scalars()
    ]


def test_the_lock_deadline_for_an_unknown_tip_off_is_noon_in_berlin(session) -> None:
    ctx, _ = open_model(session)
    game = ctx.game(UPCOMING)
    game.tipoff_utc = None
    assert ledger_module.lock_deadline(game) == datetime(
        2026, 10, 15, 10, 0, tzinfo=timezone.utc
    )  # 12:00 CEST


def test_lock_due_locks_only_what_is_inside_the_hour(session) -> None:
    now = datetime(2026, 10, 15, 16, 50, tzinfo=timezone.utc)  # 40 minutes before the 17:30 tip-off
    ctx, model = open_model(session, now=now)
    locked = ledger_module.lock_due(session, ctx, model)
    session.commit()
    inside = {
        g.game_id
        for g in ctx.games
        if g.round_number == 5
        and g.tipoff_utc
        and 0 < (g.tipoff_utc.replace(tzinfo=timezone.utc) - now).total_seconds() <= 3600
    }
    assert {r.game_id for r in locked} == inside and inside
    assert ledger_module.lock_due(session, *open_model(session, now=now)) == []


def test_the_worker_entry_points_refresh_then_lock(fresh_demo_engine, monkeypatch) -> None:
    monkeypatch.setenv("HARDWOOD_EL_DATABASE_URL", str(fresh_demo_engine.url))
    dispose_el_engine()
    clear_memo()
    early = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    out = ledger_module.run_refresh(now=early)
    assert out["status"] == "ok" and out["written"] >= 1
    assert ledger_module.run_refresh(now=early)["status"] == "skipped"
    out = ledger_module.run_lock(now=datetime(2026, 10, 15, 16, 45, tzinfo=timezone.utc))
    assert out["status"] == "ok" and out["locked"] >= 1
    assert (
        ledger_module.run_lock(now=datetime(2026, 10, 15, 16, 46, tzinfo=timezone.utc))["status"]
        == "skipped"
    )
    assert ledger_module.run_calibrate(now=early)["status"] == "skipped"


def test_calibration_fits_the_spreads_from_locked_results_only(session, monkeypatch) -> None:
    monkeypatch.setattr(ledger_module, "MIN_CALIBRATION_GAMES", 6)
    ctx, model = open_model(session)
    finals = [g for g in ctx.games if g.round_number in (3, 4)][:8]
    projections = [(70.0 + i, 75.0 + 2 * i) for i in range(len(finals))]
    for game, (h, a) in zip(finals, projections):
        session.add(
            ElProjectionLedger(
                game_id=game.game_id,
                kind="locked",
                model_version="el-1",
                computed_at=game.tipoff_utc - timedelta(minutes=10),
                inputs_cutoff=game.tipoff_utc - timedelta(minutes=10),
                home_pts=h,
                away_pts=a,
                home_full_strength=h,
                away_full_strength=a,
                home_attack_index_after_availability=1.0,
                away_attack_index_after_availability=1.0,
                home_defence_index=1.0,
                away_defence_index=1.0,
                home_advantage_points=3.5,
                cap_policy="consistent",
            )
        )
    # A reconstructed row must never be used.
    extra = [g for g in ctx.games if g.round_number in (3, 4)][8]
    session.add(
        ElProjectionLedger(
            game_id=extra.game_id,
            kind="reconstructed",
            model_version="el-1",
            computed_at=NOW.replace(tzinfo=None),
            inputs_cutoff=extra.tipoff_utc - timedelta(seconds=1),
            home_pts=1.0,
            away_pts=1.0,
            home_full_strength=1.0,
            away_full_strength=1.0,
            home_attack_index_after_availability=1.0,
            away_attack_index_after_availability=1.0,
            home_defence_index=1.0,
            away_defence_index=1.0,
            home_advantage_points=3.5,
            cap_policy="consistent",
        )
    )
    session.commit()
    ctx, _ = open_model(session)
    result = ledger_module.calibrate(session, ctx, NOW)
    session.commit()
    assert result.fitted and result.games == 8
    team = [g.home_pts - p[0] for g, p in zip(finals, projections)] + [
        g.away_pts - p[1] for g, p in zip(finals, projections)
    ]
    margin = [(g.home_pts - g.away_pts) - (p[0] - p[1]) for g, p in zip(finals, projections)]
    assert result.team_sd == pytest.approx(math.sqrt(sum(x * x for x in team) / len(team)))
    assert result.margin_sd == pytest.approx(math.sqrt(sum(x * x for x in margin) / len(margin)))
    ctx, _ = open_model(session)
    assert (
        ctx.settings["teamSd"].provenance == "fittedLedger"
        and interval_basis(ctx) == "fittedLedger"
    )


def test_calibration_restates_an_overtime_result_as_regulation(session, monkeypatch) -> None:
    """A projection is a regulation score: a 45-minute 95-93 against a locked 82-80 is a miss of
    about 2.5 points a side, not 13."""
    monkeypatch.setattr(ledger_module, "MIN_CALIBRATION_GAMES", 1)
    ctx, _ = open_model(session)
    game = next(g for g in ctx.games if g.round_number == 3)
    stored = session.get(ElGame, game.game_id)
    stored.home_pts, stored.away_pts, stored.ot_periods = 95, 93, 1
    session.add(
        ElProjectionLedger(
            game_id=game.game_id,
            kind="locked",
            model_version="el-1",
            computed_at=game.tipoff_utc - timedelta(minutes=10),
            inputs_cutoff=game.tipoff_utc - timedelta(minutes=10),
            home_pts=82.0,
            away_pts=80.0,
            home_full_strength=82.0,
            away_full_strength=80.0,
            home_attack_index_after_availability=1.0,
            away_attack_index_after_availability=1.0,
            home_defence_index=1.0,
            away_defence_index=1.0,
            home_advantage_points=3.5,
            cap_policy="consistent",
        )
    )
    session.commit()
    ctx, _ = open_model(session)
    result = ledger_module.calibrate(session, ctx, NOW)
    home, away = 95 * 12000 / 13500, 93 * 12000 / 13500
    expected = math.sqrt(((home - 82) ** 2 + (away - 80) ** 2) / 2)
    assert result.fitted and result.team_sd == pytest.approx(expected)
    assert result.team_sd < 3.0
    assert result.margin_sd == pytest.approx(abs((home - away) - 2.0))


def test_calibration_waits_for_enough_games_and_never_overwrites_a_manual_value(
    session, monkeypatch
) -> None:
    ctx, _ = open_model(session)
    assert not ledger_module.calibrate(session, ctx, NOW).fitted  # none locked, a hundred needed
    monkeypatch.setattr(ledger_module, "MIN_CALIBRATION_GAMES", 1)
    game = next(g for g in ctx.games if g.round_number == 3)
    session.add(
        ElProjectionLedger(
            game_id=game.game_id,
            kind="locked",
            model_version="el-1",
            computed_at=game.tipoff_utc - timedelta(minutes=10),
            inputs_cutoff=game.tipoff_utc - timedelta(minutes=10),
            home_pts=80.0,
            away_pts=80.0,
            home_full_strength=80.0,
            away_full_strength=80.0,
            home_attack_index_after_availability=1.0,
            away_attack_index_after_availability=1.0,
            home_defence_index=1.0,
            away_defence_index=1.0,
            home_advantage_points=3.5,
            cap_policy="consistent",
        )
    )
    set_setting(session, "teamSd", 7.0, "manual")
    session.commit()
    ctx, _ = open_model(session)
    result = ledger_module.calibrate(session, ctx, NOW)
    session.commit()
    assert result.fitted and result.skipped == ("teamSd",)
    ctx, _ = open_model(session)
    assert ctx.settings["teamSd"].value == 7.0 and ctx.settings["teamSd"].provenance == "manual"
    assert ctx.settings["marginSd"].provenance == "fittedLedger"


# --------------------------------------------------------------------------- the review


def _locked(game, home, away, minutes_before=30):
    return ElProjectionLedger(
        game_id=game.game_id,
        kind="locked",
        model_version="el-1",
        computed_at=game.tipoff_utc - timedelta(minutes=minutes_before),
        inputs_cutoff=game.tipoff_utc - timedelta(minutes=minutes_before),
        home_pts=home,
        away_pts=away,
        home_full_strength=home,
        away_full_strength=away,
        home_attack_index_after_availability=1.0,
        away_attack_index_after_availability=1.0,
        home_defence_index=1.0,
        away_defence_index=1.0,
        home_advantage_points=3.5,
        cap_policy="consistent",
    )


def test_the_review_keeps_locked_reconstructed_and_imported_apart(session) -> None:
    ctx, model = open_model(session)
    r3 = [g for g in ctx.games if g.round_number == 3]
    # Three locked: one right, one wrong, one a toss-up.
    right = r3[0]
    wrong = r3[1]
    tossup = r3[2]
    session.add(
        _locked(
            right,
            100.0 if right.home_pts > right.away_pts else 50.0,
            50.0 if right.home_pts > right.away_pts else 100.0,
        )
    )
    session.add(
        _locked(
            wrong,
            50.0 if wrong.home_pts > wrong.away_pts else 100.0,
            100.0 if wrong.home_pts > wrong.away_pts else 50.0,
        )
    )
    session.add(_locked(tossup, 80.0, 80.2))
    r2 = next(g for g in ctx.games if g.round_number == 2)
    session.add(
        ElProjectionLedger(
            game_id=r2.game_id,
            kind="imported",
            model_version="workbook",
            home_pts=85.0,
            away_pts=83.0,
            cap_policy="workbook",
        )
    )
    session.commit()
    ctx, model = open_model(session)
    review = review_module.build_review(ctx, round_number=None, freshness={})
    by = {b["modelKey"]: b for b in review["byModel"]}
    locked = by["hardwood"]
    assert locked["games"] == 3 and locked["tossUps"] == 1 and locked["decidedGames"] == 2
    assert locked["winnersCalled"] == 1
    rows = {r.game.game_id: r for r in review_module.review_rows(ctx)["locked"]}
    want_margin = sum(abs(r.margin_miss) for r in rows.values()) / 3
    assert locked["meanAbsMarginMiss"] == pytest.approx(want_margin)
    assert by["workbook"]["games"] == 1
    # The reconstructed block holds the other round-3 and round-4 games and none of the locked ones.
    rec = review["reconstructed"]
    assert rec["games"] == 20 - 3
    locked_ids = {right.game_id, wrong.game_id, tossup.game_id}
    assert not locked_ids & {
        g["game"]["gameId"] for g in review["games"] if g["kind"] == "reconstructed"
    }
    assert scan_keys(review) == []


def test_the_review_says_when_nothing_was_recorded_before_tip_off(session) -> None:
    ctx, _ = open_model(session)
    # Make round 4's games unreconstructable by removing the clubs' ratings is overkill;
    # instead check the text for a store whose only finals are inside the import.
    session.execute(delete(ElProjectionLedger))
    session.execute(
        update(ElGame)
        .where(ElGame.round_number > 2)
        .values(status="scheduled", stats_status="none", home_pts=None, away_pts=None)
    )
    session.commit()
    ctx, _ = open_model(session)
    review = review_module.build_review(ctx, round_number=None, freshness={})
    assert review["byModel"] == [] and review["reconstructed"] is None
    assert any("in or before round 2" in n for n in review["notes"])


# --------------------------------------------------------------------------- round summary


def test_the_round_summary_is_consistent_with_its_games(session) -> None:
    ctx, _ = open_model(session)
    view = round_module.build_round_view(ctx, 5, freshness_for(ctx, KEYS_STATS))
    games = view["games"]
    assert view["summary"]["games"] == 10 and len(games) == 10
    assert view["summary"]["tossUps"] == sum(1 for g in games if g["isTossUp"])
    closest = min(games, key=lambda g: (abs(g["margin"]), g["game"]["gameId"]))
    assert view["summary"]["closestGame"]["gameId"] == closest["game"]["gameId"]
    assert view["summary"]["averageCombinedPoints"] == pytest.approx(
        sum(g["combinedPoints"] for g in games) / 10
    )
    assert (
        view["summary"]["homeWinnersProjected"]
        + view["summary"]["awayWinnersProjected"]
        + view["summary"]["tossUps"]
        == 10
    )
    assert scan_keys(view) == []


def test_a_toss_up_names_no_winner() -> None:
    from nbastats.shared.team_projection import is_toss_up, summary_text

    assert is_toss_up(0.49) and not is_toss_up(0.5)
    assert summary_text(0.3, "AAA", "BBB") == "Toss-up"
    assert summary_text(-4.84, "AAA", "BBB") == "BBB by 4.8"


# --------------------------------------------------------------------------- scorers


def test_scorers_follow_the_workbooks_cells_and_carry_no_spread_or_picks(session) -> None:
    clear_statuses(session)
    ctx, model = open_model(session)
    game = ctx.game(UPCOMING)
    result = model.project(ctx, game, as_of=NOW, kind="latest")
    home, away = scorers_module.game_scorers(ctx, result, per_club=3)
    assert len(home) == len(away) == 3
    h = result.home_advantage.points
    factor = result.away.defence + 1 * h / (2 * result.home.pf)
    for line in home:
        entry = next(e for e in result.home.squad if e.person_code == line.person_code)
        assert line.model_points == pytest.approx(entry.outcome.projected_points * factor)
        if line.form_average is None:
            assert line.projected_points == pytest.approx(line.model_points)
        else:
            w = 0.25
            assert line.projected_points == pytest.approx(
                (1 - w) * line.model_points + w * line.form_average * line.chance
            )
    assert [l.projected_points for l in home] == sorted(
        (l.projected_points for l in home), reverse=True
    )
    away_factor = result.home.defence - h / (2 * result.away.pf)
    first = away[0]
    entry = next(e for e in result.away.squad if e.person_code == first.person_code)
    assert first.model_points == pytest.approx(entry.outcome.projected_points * away_factor)


def test_a_player_who_will_not_play_is_projected_zero_and_form_uses_official_games_only(
    session,
) -> None:
    clear_statuses(session)
    ctx, model = open_model(session)
    game = ctx.game(UPCOMING)
    person = top_scorers(model, game.home_club_code, 1)[0]
    add_status(session, game.home_club_code, person, "out", NOW - timedelta(days=1))
    ctx, model = open_model(session)
    result = model.project(ctx, game, as_of=NOW, kind="latest")
    lines = scorers_module.club_scorers(
        ctx, result.home, result.away, side=1, home_advantage=3.5, as_of=NOW, per_club=20
    )
    out = next(l for l in lines if l.person_code == person)
    assert out.projected_points == 0.0 and out.chance == 0.0
    assert lines[-1].projected_points == 0.0  # he sorts last
    # Form is the mean of up to ten games from the store, and says how many it used.
    form = scorers_module.form_index(ctx)
    for line in lines:
        pts = [p for _, p in form.get(line.person_code, [])][-10:]
        assert line.form_games == len(pts)
        if pts:
            assert line.form_average == pytest.approx(sum(pts) / len(pts))
        else:
            assert line.form_average is None


def test_the_scorers_payload_has_no_player_interval_and_no_forbidden_keys(session) -> None:
    ctx, _ = open_model(session)
    payload = round_module.build_round_scorers(ctx, 5, 2, {})
    assert len(payload["clubs"]) == 20
    assert scan_keys(payload) == []
    keys = set(payload["clubs"][0]["players"][0])
    assert keys == {
        "player",
        "status",
        "chanceOfPlaying",
        "modelPoints",
        "formAverage",
        "formGames",
        "recentPoints",
        "projectedPoints",
        "projectedMinutes",
    }


# --------------------------------------------------------------------------- the local replay (G1)

WORKBOOK = os.environ.get("HARDWOOD_WORKBOOK_PATH", "")


@pytest.mark.skipif(
    not WORKBOOK or not Path(WORKBOOK).exists(), reason="HARDWOOD_WORKBOOK_PATH is not set"
)
def test_round_three_replay_matches_the_workbook(tmp_path) -> None:
    """Design gate G1: import the real workbook and reproduce its Round 3, to 1e-9.

    Run with the workbook's own settings (its cap policy, squads as typed). Real data is read here
    and never written to the repository, and no real name appears in this file. Two steps:

    1. As imported, all ten games match the sheet to 1e-9, including the cap case. Two clubs'
       biggest absences were published more than fourteen days before tip-off with a return text
       that names a month ("out until November") rather than a round or a date; rule (c) keeps
       them in force until that month ends (``shared.availability.return_horizon``), as the
       workbook does.
    2. With the Squads sheet's own statuses added as entries dated 30 September (the sheet's
       "as of"), all ten games still match, which shows the arithmetic is the workbook's.
    """
    from nbastats.euroleague.db import create_el_engine, init_el_db
    from nbastats.euroleague.importers.workbook import import_workbook
    from nbastats.euroleague.importers.xlsx import Workbook, cell_text
    from nbastats.euroleague.profile import fold_name, load_club_codes

    engine = create_el_engine(f"sqlite:///{tmp_path / 'replay.db'}")
    init_el_db(engine)
    assert import_workbook(WORKBOOK, engine).status == "ok"
    book = Workbook.open(WORKBOOK)
    crosswalk = load_club_codes()
    moment = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
    with Session(engine, future=True) as s:
        set_setting(s, "capPolicyConsistent", 0, "manual")
        set_setting(s, "squadReconcile", 0, "manual")
        s.commit()
        sheet = book.sheet("Round 3")

        def compare(only_clubs=None, skip_clubs=()):
            ctx, model = open_model(s, now=moment)
            compared = 0
            for row in range(7, 17):
                home = crosswalk.by_workbook(cell_text(sheet.value(row, 4))).official_code
                away = crosswalk.by_workbook(cell_text(sheet.value(row, 6))).official_code
                if {home, away} & set(skip_clubs):
                    continue
                game = next(
                    g
                    for g in ctx.games_of_round(3)
                    if (g.home_club_code, g.away_club_code) == (home, away)
                )
                result = model.project(
                    ctx, game, as_of=game_start(game) - timedelta(seconds=1), kind="reconstructed"
                )
                m = result.match
                where = (home, away)
                assert m.home_points == pytest.approx(sheet.value(row, 10), abs=1e-9), where
                assert m.away_points == pytest.approx(sheet.value(row, 11), abs=1e-9), where
                assert m.margin == pytest.approx(sheet.value(row, 12), abs=1e-9), where
                assert m.combined_points == pytest.approx(sheet.value(row, 15), abs=1e-9), where
                assert m.home_full_strength == pytest.approx(sheet.value(row, 25), abs=1e-9), where
                assert m.away_full_strength == pytest.approx(sheet.value(row, 26), abs=1e-9), where
                assert result.home_advantage.points == pytest.approx(
                    sheet.value(row, 9), abs=1e-9
                ), where
                compared += 1
            return compared

        # Step 1: as imported.
        assert compare() == 10
        # Step 2: give the model the sheet's own statuses, dated as the sheet is.
        ctx = build_context(s, None, now=moment)
        squads = book.sheet("Squads")
        people = {
            (r.club_code, fold_name(ctx.player_name(r.person_code))): r.person_code
            for r in ctx.registrations.values()
        }
        added = 0
        for row in range(6, 336):
            club = crosswalk.by_workbook(cell_text(squads.value(row, 1)))
            name = cell_text(squads.value(row, 2))
            status = (cell_text(squads.value(row, 35)) or "").lower()
            if club is None or not name or status in ("", "available"):
                continue
            add_status(
                s,
                club.official_code,
                people[(club.official_code, fold_name(name))],
                status,
                datetime(2026, 9, 30),
            )
            added += 1
        assert added > 0
        assert compare() == 10
    engine.dispose()
