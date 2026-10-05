"""NBA defence by position: where the points a team allows came from, and when to say nothing.

What is asserted, and how
-------------------------
**It reconciles to the score, on the seeded store.** The demo league is the one store with real
volume (thirty teams, hundreds of games, a listed position for every player), so the first test
re-derives every team's buckets from the raw ``player_game_basic`` lines and the
``player_position_season`` listings, with no help from the module under test, and demands that the
buckets add up to ``team_game.opp_pts``, that ``opp_pts`` is what the opponent's own team row says
it scored, and that the payload's headline, buckets, deltas and shares agree with all of it. The
``unknown`` bucket is part of the sum: nothing is dropped or reassigned.

**Where a position comes from, by hand.** A one-game league is small enough to work out on paper:
a hybrid listing splits half and half, a player with no listing, a listing of nulls, a label that
is not on the table (``PG-SG``) and a listing for another season are all ``unknown``, a roster
listing beats the lineup-card label in ``players.position``, and the listing used is the season's
own. Minutes and the per-48 rate come out of the same arithmetic.

**The gates, at their exact thresholds.** The shared core owns the rules; this module proves the
NBA read side wires them to the right numbers: nine games withholds and ten does not,
twenty-four games is provisional and twenty-five is not, a team whose unlisted scorers are exactly
five percent of what it allows is shown and one a hair above is withheld, the league's own coverage
withholds everyone, four teams of five meeting the minimum is enough and three is not, and the
ceiling is a setting.

**A signal, and the absence of one.** A league built so that one team concedes twelve extra points
to centers shows ``worse`` for that team at that position and ``detected`` for the league, and the
very same table over a short window is provisional and shows no band at all; a league with no
positional differences shows ``none detected`` and the sentence that says so.

**Honest edges.** A game whose box score is missing a player is excluded and counted, never
patched; a team with no games gets an empty answer, not an error; a EuroLeague club code is an
unknown team; no payload carries a rank; the table is sorted by a recorded fact; the allocation is
memoised and the memo is keyed on the data.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Iterable, Sequence

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nbastats import nba_matchup
from nbastats.api.errors import ApiError
from nbastats.models import Game, Player, PlayerGameBasic, PlayerPositionSeason, TeamGame
from nbastats.nba_intel import settings as intel_settings
from nbastats.nba_matchup import defense, queries
from nbastats.shared.defense_position import bonferroni_z
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


def keys_of(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [k for k in value] + [x for v in value.values() for x in keys_of(v)]
    if isinstance(value, list):
        return [x for v in value for x in keys_of(v)]
    return []


def bucket(payload: dict[str, Any], position: str) -> dict[str, Any]:
    return next(b for b in payload["buckets"] if b["position"] == position)


# =========================================================================== league builders

#: Slots of a generated roster: 1 guard, 2 forward, 3 center, 4 a player nobody listed.
SLOT_LABELS = {1: "G", 2: "F", 3: "C"}


def round_robin(teams: Sequence[int], cycles: int) -> list[tuple[int, int]]:
    """Every pair twice (each side at home once) per cycle: ``(home, away)`` in play order."""
    pairs = [(a, b) for i, a in enumerate(teams) for b in teams[i + 1 :]]
    out: list[tuple[int, int]] = []
    for _ in range(cycles):
        for a, b in pairs:
            out += [(a, b), (b, a)]
    return out


def add_roster(
    league: LeagueBuilder, team: int, *, unlisted: bool = False, listed: bool = True
) -> None:
    """Players ``team*10+1`` to ``+3`` listed G, F and C (unless ``listed`` is false); ``+4`` has
    no listing when asked for."""
    league.team(team)
    for slot, label in SLOT_LABELS.items():
        league.player(team * 10 + slot)
        if listed:
            league.listing(team * 10 + slot, team, label)
    if unlisted:
        league.player(team * 10 + 4)


def play(
    league: LeagueBuilder,
    schedule: Iterable[tuple[int, int]],
    points: Any,
    *,
    minutes: float = 30.0,
) -> None:
    """Add one final game per ``(home, away)``. ``points(n, team, opponent)`` gives each slot's
    points for one side of game ``n``; the game's score is exactly their sum, so every game
    reconciles. Several games share a date, which is harmless here (nothing is tip-off sensitive).
    """
    for n, (home, away) in enumerate(schedule):
        home_pts, away_pts = points(n, home, away), points(n, away, home)
        league.game(
            f"g{n:03d}",
            (date(2025, 11, 1) + timedelta(days=n // 4)).isoformat(),
            home,
            away,
            sum(home_pts.values()),
            sum(away_pts.values()),
            home_lines=[(home * 10 + slot, minutes, p) for slot, p in home_pts.items()],
            away_lines=[(away * 10 + slot, minutes, p) for slot, p in away_pts.items()],
        )


BASE = {1: 36, 2: 38, 3: 22}


def noisy(n: int, team: int, opponent: int, *, centers_vs: int | None = None) -> dict[int, int]:
    """Roughly 96 points a side, spread across the three slots, with deterministic noise; when
    ``centers_vs`` is the opponent the centers score twelve more (a team that concedes at C)."""
    out: dict[int, int] = {}
    for slot, base in BASE.items():
        wobble = ((n * 7 + team * 13 + slot * 5) % 9) - 4
        extra = 12 if (slot == 3 and opponent == centers_vs) else 0
        out[slot] = base + wobble + extra
    return out


def flat(total: int, unlisted: int = 0) -> Any:
    """A side that always scores ``total``: listed slots share ``total - unlisted`` (roughly in
    the ratio 36:38:22), and slot 4, nobody's position, scores ``unlisted``. No noise at all."""

    def points(n: int, team: int, opponent: int) -> dict[int, int]:
        listed = total - unlisted
        g = round(listed * 36 / 96)
        f = round(listed * 38 / 96)
        out = {1: g, 2: f, 3: listed - g - f}
        if unlisted:
            out[4] = unlisted
        return out

    return points


def league_of(
    store: Store,
    teams: Sequence[int],
    schedule: Sequence[tuple[int, int]],
    points: Any,
    *,
    unlisted: Iterable[int] = (),
) -> None:
    extra = set(unlisted)
    for team in teams:
        add_roster(store.league, team, unlisted=team in extra)
    play(store.league, schedule, points)
    store.league.commit()
    queries.clear_memo()


def team_payload(store: Store, team: int, **options: Any) -> dict[str, Any]:
    return nba_matchup.defense_by_position(store.session, team=team, now=NOW, **options)


def table_payload(store: Store, **options: Any) -> dict[str, Any]:
    return nba_matchup.defense_by_position(store.session, team=None, now=NOW, **options)


# =========================================================================== the seeded store


def independent_buckets(session: Session, season: str) -> dict[int, dict[str, Any]]:
    """Every team's points allowed by bucket for ``season``'s regular season, from the raw rows.

    Written without the module under test: opposing lines, the season's listings and the stored
    ``opp_pts``, summed in plain Python. ``games`` is how many final games the team played.
    """
    listing = {
        row.player_id: (row.g_weight, row.f_weight, row.c_weight)
        for row in session.execute(
            select(PlayerPositionSeason).where(PlayerPositionSeason.season == season)
        ).scalars()
    }
    games = {
        g.game_id: g
        for g in session.execute(
            select(Game).where(
                Game.season == season, Game.season_type == "Regular Season", Game.status == "final"
            )
        ).scalars()
    }
    lines: dict[tuple[str, int], list[tuple[int, float, int]]] = {}
    for row in session.execute(
        select(PlayerGameBasic).where(PlayerGameBasic.game_id.in_(list(games)))
    ).scalars():
        lines.setdefault((row.game_id, row.team_id), []).append(
            (row.player_id, row.minutes or 0.0, row.pts or 0)
        )
    opp = {
        (row.game_id, row.team_id): row.opp_pts
        for row in session.execute(
            select(TeamGame).where(TeamGame.game_id.in_(list(games)))
        ).scalars()
    }
    out: dict[int, dict[str, Any]] = {}
    for game in games.values():
        for defender, attacker in (
            (game.home_team_id, game.away_team_id),
            (game.away_team_id, game.home_team_id),
        ):
            sums = {"G": 0.0, "F": 0.0, "C": 0.0, "unknown": 0.0}
            minutes = dict(sums)
            for player_id, mins, pts in lines.get((game.game_id, attacker), ()):
                if not mins > 0:
                    continue
                weights = listing.get(player_id)
                if weights is None or any(w is None for w in weights):
                    sums["unknown"] += pts
                    minutes["unknown"] += mins
                    continue
                for name, w in zip(("G", "F", "C"), weights):
                    sums[name] += pts * w
                    minutes[name] += mins * w
            entry = out.setdefault(
                defender,
                {"games": 0, "opp_pts": [], "sum": {k: [] for k in sums}, "min": dict(minutes)},
            )
            entry["games"] += 1
            entry["opp_pts"].append(opp[(game.game_id, defender)])
            for name, value in sums.items():
                entry["sum"][name].append(value)
    return out


def test_the_buckets_reconcile_to_opp_pts_on_the_seeded_store(seeded_db: Session) -> None:
    season = "2025-26"
    truth = independent_buckets(seeded_db, season)
    assert len(truth) == 30

    # The two ways of saying what a team allowed agree on the seed: the stored opp_pts is the
    # opponent's own recorded points, and the opposing lines add up to it (the seeder's invariant,
    # which the allocation relies on to reconcile).
    for game in seeded_db.execute(
        select(Game).where(Game.season == season, Game.status == "final")
    ).scalars():
        rows = {
            r.team_id: r
            for r in seeded_db.execute(
                select(TeamGame).where(TeamGame.game_id == game.game_id)
            ).scalars()
        }
        home, away = rows[game.home_team_id], rows[game.away_team_id]
        assert home.opp_pts == away.pts == game.away_pts
        assert away.opp_pts == home.pts == game.home_pts

    table = nba_matchup.defense_by_position(seeded_db, team=None, season=season, now=NOW)
    league_games = sum(t["games"] for t in truth.values())
    league_papg = sum(sum(t["opp_pts"]) for t in truth.values()) / league_games
    assert table["leaguePointsAllowedPerGame"] == pytest.approx(league_papg, abs=1e-9)
    assert len(table["teams"]) == 30

    for row in table["teams"]:
        team_id = row["team"]["teamId"]
        expect = truth[team_id]
        payload = nba_matchup.defense_by_position(seeded_db, team=team_id, season=season, now=NOW)
        n = expect["games"]
        assert payload["window"] == {"kind": "season", "games": n, "requested": None}
        papg = sum(expect["opp_pts"]) / n
        assert payload["pointsAllowedPerGame"] == pytest.approx(papg, abs=1e-9)
        assert row["pointsAllowedPerGame"] == pytest.approx(papg, abs=1e-9)

        recon = payload["reconciliation"]
        assert recon["unreconciledGames"] == 0, "every seeded game must reconcile"
        assert recon["sumOfBuckets"] == pytest.approx(papg, abs=1e-9)
        assert (
            recon["identity"]
            == "sum(deltaPerGame) = pointsAllowedPerGame - leaguePointsAllowedPerGame"
        )

        total = 0.0
        delta = 0.0
        shares = 0.0
        for position in ("G", "F", "C", "unknown"):
            cell = bucket(payload, position)
            want = sum(expect["sum"][position]) / n
            assert cell["pointsAllowedPerGame"] == pytest.approx(want, abs=1e-9), position
            total += cell["pointsAllowedPerGame"]
            delta += cell["deltaPerGame"]
            shares += cell["share"]
            assert cell["leagueAverage"] == pytest.approx(
                sum(sum(t["sum"][position]) for t in truth.values()) / league_games, abs=1e-9
            )
        assert total == pytest.approx(papg, abs=1e-9)  # the buckets ARE the headline
        assert delta == pytest.approx(papg - league_papg, abs=1e-9)  # and so are the deltas
        assert shares == pytest.approx(1.0, abs=1e-12)
        assert payload["coverage"]["unknown"] == 0.0 and payload["coverage"]["listed"] == 1.0
        assert bucket(payload, "unknown")["pointsAllowedPerGame"] == 0.0  # a recorded zero


def test_the_matchups_points_allowed_is_the_defence_headline_and_the_buckets_tie_to_it(
    seeded_db: Session,
) -> None:
    """Two views of the same store, built by different code (the team-form arithmetic of the
    matchup and the allocation of the defence), must say the same thing about the same team: the
    matchup's points allowed per game is the defence's headline, and the buckets the matchup
    summarises add up to it."""
    teams = [t for t in seeded_db.execute(select(Game.home_team_id).distinct()).scalars()]
    teams.sort()
    for home, away in zip(teams[0::2], teams[1::2]):
        body = nba_matchup.team_matchup(seeded_db, home=home, away=away, season="2025-26", now=NOW)
        for side in body["teams"]:
            payload = nba_matchup.defense_by_position(
                seeded_db, team=side["team"]["teamId"], season="2025-26", now=NOW
            )
            assert side["games"] == payload["window"]["games"]
            assert side["pointsAllowedPerGame"] == pytest.approx(
                payload["pointsAllowedPerGame"], abs=1e-9
            )
            summary = side["defenseSummary"]
            assert summary["pointsAllowedPerGame"] == payload["pointsAllowedPerGame"]
            assert summary["withheld"] == payload["withheld"]
            assert sum(b["pointsAllowedPerGame"] for b in summary["buckets"]) == pytest.approx(
                side["pointsAllowedPerGame"], abs=1e-9
            )
            assert [b["position"] for b in summary["buckets"]] == ["G", "F", "C", "unknown"]
        assert body["leagueAverage"]["pointsPerGame"] == pytest.approx(
            nba_matchup.defense_by_position(seeded_db, team=None, season="2025-26", now=NOW)[
                "leaguePointsAllowedPerGame"
            ],
            abs=1e-9,
        )  # league points scored per game is league points allowed per game


def test_the_seeded_table_is_sorted_by_a_recorded_fact_and_ranks_nothing(
    seeded_db: Session,
) -> None:
    table = nba_matchup.defense_by_position(seeded_db, team=None, season="2025-26", now=NOW)
    papg = [t["pointsAllowedPerGame"] for t in table["teams"]]
    assert papg == sorted(papg)
    assert not any("rank" in key.lower() for key in keys_of(table))
    assert table["caveat"].startswith("Counts points scored by opposing players listed")
    assert table["scheme"] == "gfc" and table["basis"] == "perGame"
    assert table["regulationMinutes"] == 48 and table["league"] == "nba"
    assert table["freshness"]["isDemo"] is True
    # A 24-game season-to-date is below the 25 that makes a table more than provisional.
    games = {t["games"] for t in table["teams"]}
    assert max(games) <= 25
    for row in table["teams"]:
        assert row["provisional"] is (row["games"] < 25)
        if row["provisional"]:
            assert all(b["band"] is None for b in row["buckets"])


def test_a_season_with_no_positions_listed_says_so_and_withholds_nothing_it_knows(
    store: Store,
) -> None:
    """With no roster listings at all (the roster fetch has not run, or its POSITION field was
    absent) every point is of unknown position: the figures are shown as facts, no index is, and
    the payload says why instead of inventing a split."""
    teams = [1, 2, 3, 4, 5, 6]
    for team in teams:
        add_roster(store.league, team, listed=False)
    play(store.league, round_robin(teams, 3), noisy)
    store.league.commit()
    queries.clear_memo()
    table = table_payload(store)
    assert defense.NOTE_NO_LISTINGS in table["notes"]
    assert table["method"]["positionSource"] is None
    assert table["availability"] == "partial"
    for row in table["teams"]:
        assert row["withheld"]["reason"] == "positionCoverage"
        assert all(b["index"] is None and b["band"] is None for b in row["buckets"])
        assert bucket(row, "unknown")["pointsAllowedPerGame"] == pytest.approx(
            row["pointsAllowedPerGame"], abs=1e-9
        )
        for position in ("G", "F", "C"):
            assert bucket(row, position)["pointsAllowedPerGame"] == 0.0  # a recorded zero


def test_the_seeded_store_lists_a_position_for_every_player_it_scores_with(
    seeded_db: Session,
) -> None:
    table = nba_matchup.defense_by_position(seeded_db, team=None, season="2025-26", now=NOW)
    assert defense.NOTE_NO_LISTINGS not in table["notes"]
    assert "the demo league's own labels" in table["method"]["positionSource"]
    assert table["availability"] == "full"


# =========================================================================== a position, by hand


def hand_league(store: Store) -> None:
    """One game: team 1 hosts team 2 and wins 70 to 64. Team 2's lines are the ones the
    allocation has to place; team 1's single line makes team 2's own defence checkable too."""
    league = store.league
    for team in (1, 2, 3):
        league.team(team)
    for player_id in (11, 21, 22, 23, 24, 25, 26, 27, 28, 29):
        league.player(player_id)
    league.listing(11, 1, "C")
    league.listing(21, 2, "G")
    league.listing(22, 2, "F")
    league.listing(23, 2, "G-F")
    league.listing(24, 2, "C")
    # 25 has no listing at all; 26 has a listing of nulls; 28's label is not on the table;
    # 29 is listed, but for last season only.
    league.listing(26, 2, None)
    league.listing(28, 2, "PG-SG")
    league.listing(29, 2, "C", season="2024-25")
    league.listing(22, 2, "C", season="2024-25")  # last year he was a center; this year a forward
    league.session.flush()
    league.session.get(Player, 22).position = "C"  # the lineup-card label must be ignored
    league.game(
        "g1",
        "2025-11-01",
        1,
        2,
        70,
        64,
        home_lines=[(11, 36.0, 70)],
        away_lines=[
            (21, 30.0, 20),
            (22, 20.0, 10),
            (23, 24.0, 12),
            (24, 10.0, 8),
            (25, 6.0, 5),
            (26, 5.0, 4),
            (27, 0.0, 0),  # played no minutes: contributes nothing
            (28, 4.0, 3),
            (29, 3.0, 2),
        ],
    )
    league.commit()
    queries.clear_memo()


def test_a_positions_points_minutes_and_rate_are_the_hand_calculation(store: Store) -> None:
    hand_league(store)
    payload = team_payload(store, 1)
    assert payload["pointsAllowedPerGame"] == 64
    assert payload["window"] == {"kind": "season", "games": 1, "requested": None}
    # Guards: 20, and half of the guard-forward's 12. Forwards: 10 (not a center: the roster
    # listing wins over the lineup-card label) and the other half. Centers: 8. Unknown: the player
    # nobody listed (5), the listing of nulls (4), the label off the table (3) and the player
    # listed only for last season (2).
    expected = {"G": 26.0, "F": 16.0, "C": 8.0, "unknown": 14.0}
    minutes = {"G": 30.0 + 12.0, "F": 20.0 + 12.0, "C": 10.0, "unknown": 6.0 + 5.0 + 4.0 + 3.0}
    for position, points in expected.items():
        cell = bucket(payload, position)
        assert cell["pointsAllowedPerGame"] == points, position
        assert cell["opponentMinutesPerGame"] == minutes[position], position
        assert close(cell["share"], points / 64)
    assert sum(expected.values()) == 64
    assert close(bucket(payload, "G")["pointsPerRegulationMinutes"], 26 / 42 * 48)
    assert close(bucket(payload, "C")["pointsPerRegulationMinutes"], 8 / 10 * 48)

    # The league is two team-games: this one (64 allowed) and team 2's (70, all to a center).
    league = {"G": 13.0, "F": 8.0, "C": 39.0, "unknown": 7.0}
    assert payload["leaguePointsAllowedPerGame"] == 67
    for position, mean in league.items():
        cell = bucket(payload, position)
        assert cell["leagueAverage"] == mean, position
        assert close(cell["deltaPerGame"], expected[position] - mean)
    assert close(sum(b["deltaPerGame"] for b in payload["buckets"]), 64 - 67)

    # Coverage is in fractions of points allowed, and says how much was of unknown position.
    assert close(payload["coverage"]["unknown"], 14 / 64)
    assert close(payload["coverage"]["listed"], 50 / 64)
    assert payload["coverage"]["workbookListing"] == 0.0

    # One game is far too few to judge a defence: the figures are facts, the indices are withheld.
    assert payload["withheld"] == {
        "reason": "minimumGames",
        "message": "Only 1 game, too few to judge a defence",
    }
    assert payload["provisional"] is True
    for cell in payload["buckets"]:
        assert cell["index"] is None and cell["rawIndex"] is None and cell["band"] is None
    assert payload["availability"] == "partial"  # some points had no listed position

    other = team_payload(store, 2)
    assert bucket(other, "C")["pointsAllowedPerGame"] == 70.0
    assert other["coverage"]["unknown"] == 0.0 and other["availability"] == "full"


def test_the_listing_of_the_season_asked_for_is_the_one_used(store: Store) -> None:
    """A player's bucket is one basis per player-season: asked about last season, 29 is a center
    and 22 is a center, while this season 22 is a forward."""
    hand_league(store)
    store.league.game(
        "old",
        "2024-11-01",
        1,
        2,
        90,
        80,
        season="2024-25",
        home_lines=[(11, 36.0, 90)],
        away_lines=[(22, 30.0, 50), (29, 20.0, 30)],
    )
    store.league.commit()
    queries.clear_memo()
    last = nba_matchup.defense_by_position(store.session, team=1, season="2024-25", now=NOW)
    assert bucket(last, "C")["pointsAllowedPerGame"] == 80.0
    assert bucket(last, "F")["pointsAllowedPerGame"] == 0.0
    this = nba_matchup.defense_by_position(store.session, team=1, season="2025-26", now=NOW)
    assert bucket(this, "F")["pointsAllowedPerGame"] == 16.0


def test_a_game_that_does_not_reconcile_is_excluded_and_counted_never_patched(
    store: Store,
) -> None:
    hand_league(store)
    # Team 2 hosts team 1 and scores 60, but the box score is missing a player: 50 points of lines.
    store.league.game(
        "g2",
        "2025-11-03",
        2,
        1,
        60,
        55,
        home_lines=[(21, 30.0, 30), (22, 20.0, 20)],
        away_lines=[(11, 36.0, 55)],
    )
    store.league.commit()
    queries.clear_memo()
    one = team_payload(store, 1)
    two = team_payload(store, 2)
    # Team 1 defended g2 against team 2's 50 listed points: 60 allowed, 50 placed -> unreconciled.
    assert one["reconciliation"]["unreconciledGames"] == 1
    assert one["window"]["games"] == 1  # g1 only; g2 is left out of the average, not patched
    assert one["pointsAllowedPerGame"] == 64
    assert one["availability"] == "partial"
    # Team 2 defended g2 against team 1's single 55-point line, which reconciles.
    assert two["reconciliation"]["unreconciledGames"] == 0
    assert two["window"]["games"] == 2
    assert two["pointsAllowedPerGame"] == (70 + 55) / 2


def test_a_team_with_no_games_gets_an_empty_answer_not_an_error(store: Store) -> None:
    hand_league(store)
    payload = team_payload(store, 3)
    assert payload["window"]["games"] == 0
    assert payload["pointsAllowedPerGame"] is None
    assert payload["reconciliation"]["sumOfBuckets"] is None
    assert payload["availability"] == "unavailable"
    assert payload["withheld"]["reason"] == "minimumGames"
    assert payload["withheld"]["message"] == "Only 0 games, too few to judge a defence"
    assert all(b["pointsAllowedPerGame"] is None for b in payload["buckets"])  # not zero
    assert payload["coverage"] == {"listed": None, "workbookListing": None, "unknown": None}


# =========================================================================== the gates, exact


def six_team_league(store: Store, points: Any = noisy, **kwargs: Any) -> None:
    teams = [1, 2, 3, 4, 5, 6]
    league_of(store, teams, round_robin(teams, 3), points, **kwargs)  # 30 games each


def test_nine_games_withholds_and_ten_does_not(store: Store) -> None:
    six_team_league(store)
    nine = team_payload(store, 3, window=9)
    assert nine["window"] == {"kind": "lastGames", "games": 9, "requested": 9}
    assert nine["withheld"] == {
        "reason": "minimumGames",
        "message": "Only 9 games, too few to judge a defence",
    }
    assert all(b["index"] is None for b in nine["buckets"])
    assert nine["method"]["minimumGames"] == 10
    ten = team_payload(store, 3, window=10)
    assert ten["withheld"] is None and ten["window"]["games"] == 10
    assert all(bucket(ten, p)["index"] is not None for p in ("G", "F", "C"))


def test_twenty_four_games_is_provisional_and_twenty_five_is_not(store: Store) -> None:
    six_team_league(store)
    for window, provisional in ((10, True), (24, True), (25, False), (30, False), (0, False)):
        payload = team_payload(store, 2, window=window)
        assert payload["provisional"] is provisional, window
    assert team_payload(store, 2, window=24)["method"]["provisionalBelowGames"] == 25
    # Provisional never shows a band, even where the shared rules would otherwise allow one.
    assert all(b["band"] is None for b in team_payload(store, 2, window=24)["buckets"])


def test_a_window_is_each_teams_last_games_and_the_league_stays_the_season(
    store: Store,
) -> None:
    six_team_league(store)
    season = team_payload(store, 4)
    last = team_payload(store, 4, window=7)
    assert last["window"] == {"kind": "lastGames", "games": 7, "requested": 7}
    # The league reference is the whole season whatever the window.
    assert last["leaguePointsAllowedPerGame"] == season["leaguePointsAllowedPerGame"]
    for position in ("G", "F", "C"):
        assert bucket(last, position)["leagueAverage"] == bucket(season, position)["leagueAverage"]
    # ... and the team's own figure is the mean of its last seven games, newest first by date.
    rows = store.session.execute(
        select(TeamGame.opp_pts, Game.game_date, Game.game_id)
        .join(Game, Game.game_id == TeamGame.game_id)
        .where(TeamGame.team_id == 4)
    ).all()
    newest = sorted(rows, key=lambda r: (r.game_date, r.game_id), reverse=True)[:7]
    assert last["pointsAllowedPerGame"] == pytest.approx(
        sum(r.opp_pts for r in newest) / 7, abs=1e-9
    )
    # A window longer than the games played holds the games played.
    assert team_payload(store, 4, window=99)["window"]["games"] == 30


def test_coverage_exactly_at_the_ceiling_is_shown_and_a_hair_above_is_withheld(
    store: Store,
) -> None:
    """Every team has an unlisted scorer, so the league's own share is the same as each team's:
    5 of 100 is exactly the 5% ceiling (shown), 6 of 100 is over it (withheld, in the league's
    name)."""
    teams = [1, 2, 3, 4, 5, 6]
    league_of(store, teams, round_robin(teams, 3), flat(100, 5), unlisted=teams)
    at_ceiling = team_payload(store, 1)
    assert at_ceiling["withheld"] is None
    assert close(at_ceiling["coverage"]["unknown"], 0.05)
    assert bucket(at_ceiling, "unknown")["pointsAllowedPerGame"] == 5.0
    assert all(bucket(at_ceiling, p)["index"] is not None for p in ("G", "F", "C"))
    assert at_ceiling["method"]["coverageCeiling"] == 0.05


def test_the_leagues_coverage_withholds_every_team_and_the_ceiling_is_a_setting(
    store: Store,
) -> None:
    teams = [1, 2, 3, 4, 5, 6]
    league_of(store, teams, round_robin(teams, 3), flat(100, 6), unlisted=teams)
    for team in (1, 4):
        payload = team_payload(store, team)
        assert payload["withheld"]["reason"] == "positionCoverage"
        assert "the league's" in payload["withheld"]["message"]
        assert "6.0%" in payload["withheld"]["message"] and "5%" in payload["withheld"]["message"]
        assert all(b["index"] is None and b["band"] is None for b in payload["buckets"])
        # The recorded facts still show, including the points of unknown position.
        assert bucket(payload, "unknown")["pointsAllowedPerGame"] == 6.0
    table = table_payload(store)
    assert all(t["withheld"]["reason"] == "positionCoverage" for t in table["teams"])

    # The ceiling is the positionCoverageCeiling setting: raise it to exactly 6% and they show.
    intel_settings.set_setting(store.session, "positionCoverageCeiling", 0.06)
    store.session.commit()
    queries.clear_memo()
    shown = team_payload(store, 1)
    assert shown["withheld"] is None
    assert shown["method"]["coverageCeiling"] == 0.06
    intel_settings.set_setting(store.session, "positionCoverageCeiling", 0.059)
    store.session.commit()
    queries.clear_memo()
    assert team_payload(store, 1)["withheld"]["reason"] == "positionCoverage"


def test_one_teams_coverage_is_judged_on_its_own_not_the_leagues(store: Store) -> None:
    """Team 1 alone has an unlisted scorer (25 of its 100 points). Each of the other five teams
    meets it in 6 of its 30 games, so 150 of the 3000 points it allows are of unknown position:
    exactly 5% (shown). Twenty-six is 5.2% (withheld, in that team's name), while the league's
    own share, about 4.3%, is below the ceiling and withholds nobody on that account."""

    def points(unlisted: int) -> Any:
        listed_one = flat(100 - unlisted)
        listed = flat(100)

        def pts(n: int, team: int, opponent: int) -> dict[int, int]:
            if team == 1:
                return {**listed_one(n, team, opponent), 4: unlisted}
            return listed(n, team, opponent)

        return pts

    teams = [1, 2, 3, 4, 5, 6]
    league_of(store, teams, round_robin(teams, 3), points(25), unlisted=[1])
    at_threshold = team_payload(store, 3)
    assert at_threshold["withheld"] is None
    assert close(at_threshold["coverage"]["unknown"], 0.05)

    store.session.rollback()
    store.session.query(PlayerGameBasic).delete()
    store.session.query(TeamGame).delete()
    store.session.query(Game).delete()
    store.session.commit()
    play(store.league, round_robin(teams, 3), points(26))
    store.league.commit()
    queries.clear_memo()
    over = team_payload(store, 3)
    assert over["withheld"]["reason"] == "positionCoverage"
    assert "this team's" in over["withheld"]["message"] and "5.2%" in over["withheld"]["message"]
    # The league as a whole is fine, and team 1 (whose opponents' scorers are all listed) is not
    # withheld for coverage: with only one eligible team there is nothing to compare it with,
    # which is the league-sample reason, not a coverage one.
    assert team_payload(store, 1)["coverage"]["unknown"] == 0.0
    assert team_payload(store, 1)["withheld"]["reason"] == "leagueSample"


def short_schedule(
    full: Sequence[int], short: Sequence[int], *, cycles: int, short_games: int
) -> list[tuple[int, int]]:
    """``full`` teams play each other ``cycles`` times over; each ``short`` team plays exactly
    ``short_games`` games, against the full teams in turn."""
    schedule = round_robin(list(full), cycles)
    for team in short:
        for i in range(short_games):
            opponent = full[i % len(full)]
            schedule.append((team, opponent) if i % 2 == 0 else (opponent, team))
    return schedule


def test_four_teams_of_five_meeting_the_minimum_is_enough_and_three_is_not(
    store: Store,
) -> None:
    """The league-sample rule: indices need at least 80% of the league's teams to have played
    enough games to estimate between-team spread. Four of five is exactly 80% (enough)."""
    teams = [1, 2, 3, 4, 5]
    league_of(
        store,
        teams,
        short_schedule([1, 2, 3, 4], [5], cycles=2, short_games=9),
        noisy,
    )
    short = team_payload(store, 5)
    assert short["window"]["games"] == 9
    assert short["withheld"]["reason"] == "minimumGames"
    full = team_payload(store, 1)
    assert full["withheld"] is None and full["window"]["games"] >= 10
    assert all(bucket(full, p)["index"] is not None for p in ("G", "F", "C"))

    # Three of five is 60%: the teams that did play enough are withheld for the league's sample.
    store.session.query(PlayerGameBasic).delete()
    store.session.query(TeamGame).delete()
    store.session.query(Game).delete()
    store.session.commit()
    play(
        store.league,
        short_schedule([1, 2, 3], [4, 5], cycles=3, short_games=9),
        noisy,
    )
    store.league.commit()
    queries.clear_memo()
    third = team_payload(store, 1)
    assert third["window"]["games"] >= 10
    assert third["withheld"]["reason"] == "leagueSample"
    assert "league-wide noise" in third["withheld"]["message"]
    assert team_payload(store, 4)["withheld"]["reason"] == "minimumGames"
    # Only the withheld part changes: the facts stay.
    assert third["pointsAllowedPerGame"] is not None
    assert bucket(third, "C")["pointsAllowedPerGame"] is not None


# =========================================================================== signal and no signal


def test_a_team_that_concedes_at_one_position_shows_worse_there_and_the_league_a_signal(
    store: Store,
) -> None:
    teams = list(range(1, 9))
    league_of(
        store,
        teams,
        round_robin(teams, 2),  # 28 games each: past the 25 that make a table more than provisional
        lambda n, team, opp: noisy(n, team, opp, centers_vs=1),
    )
    one = team_payload(store, 1)
    assert one["window"]["games"] == 28 and one["provisional"] is False
    assert one["method"]["leagueSignal"] == "detected"
    centers = bucket(one, "C")
    assert centers["band"] == "worse"
    assert centers["rawIndex"] > 1.4 and centers["index"] > 1.3 and centers["standardError"] > 0
    assert centers["deltaPerGame"] > 10
    for position in ("G", "F"):  # no signal at the other positions: no band, an index of one
        cell = bucket(one, position)
        assert cell["band"] is None and cell["index"] == 1.0
    assert one["method"]["leagueReliability"]["C"] > 0.9
    assert one["method"]["leagueReliability"]["G"] == 0.0

    # The Bonferroni family is every displayed cell: eight teams with an index, three positions.
    z = bonferroni_z(8 * 3, 0.05)
    assert f"z = {z:.3f}" in one["method"]["bandRule"]
    assert "a Bonferroni family of 24 cells" in one["method"]["bandRule"]

    # The rest of the league holds opponents a little below the (now inflated) average at C.
    table = table_payload(store)
    others = [t for t in table["teams"] if t["team"]["teamId"] != 1]
    assert all(bucket(t, "C")["rawIndex"] < 1.0 for t in others)
    assert {bucket(t, "C")["band"] for t in others} <= {"better", "typical"}
    assert table["teams"][-1]["team"]["teamId"] == 1  # sorted by points allowed: most last
    assert table["methodMessage"] is None  # a signal was found, so no "none detected" sentence

    # Over a short window the very same table is provisional and shows no band at all.
    short = team_payload(store, 1, window=20)
    assert short["provisional"] is True
    assert all(b["band"] is None for b in short["buckets"])
    assert bucket(short, "C")["index"] > 1.3  # the index is still shown; only the band is held back
    assert short["method"]["leagueSignal"] is None  # nothing could be tested yet


def test_a_league_with_no_positional_differences_says_none_detected(store: Store) -> None:
    teams = [1, 2, 3, 4, 5, 6]
    league_of(store, teams, round_robin(teams, 3), flat(100))
    payload = team_payload(store, 2)
    assert payload["method"]["leagueSignal"] == "none detected"
    assert payload["methodMessage"] == (
        "No team's points allowed at this position differ from the league by more than chance "
        "this season"
    )
    assert all(b["band"] is None for b in payload["buckets"])
    assert all(bucket(payload, p)["index"] == 1.0 for p in ("G", "F", "C"))
    assert table_payload(store)["methodMessage"] == payload["methodMessage"]


def test_the_per_minute_basis_controls_for_how_opponents_spread_their_minutes(
    store: Store,
) -> None:
    six_team_league(store)
    per_game = team_payload(store, 2)
    per_minute = team_payload(store, 2, basis="perMinute")
    assert per_game["basis"] == "perGame" and per_minute["basis"] == "perMinute"
    # The recorded rate is the same arithmetic on either basis; the indices differ in what they
    # compare (points per game, or points per opponent floor minute).
    for position in ("G", "F", "C"):
        a, b = bucket(per_game, position), bucket(per_minute, position)
        assert a["pointsAllowedPerGame"] == b["pointsAllowedPerGame"]
        assert close(
            b["pointsPerRegulationMinutes"],
            b["pointsAllowedPerGame"] / b["opponentMinutesPerGame"] * 48,
        )
        assert b["rawIndex"] is not None and b["standardError"] is not None
    # Every line is 30 minutes in this league, so per minute is per game scaled: same index.
    for position in ("G", "F", "C"):
        assert bucket(per_minute, position)["rawIndex"] == pytest.approx(
            bucket(per_game, position)["rawIndex"], rel=1e-9
        )


# =========================================================================== the whole table


def test_the_table_lists_every_team_sorted_by_points_allowed_with_no_rank(
    store: Store,
) -> None:
    six_team_league(store)
    table = table_payload(store, window=12)
    assert table["window"] == {"kind": "lastGames", "requested": 12}
    assert [t["team"]["id"] for t in table["teams"]] != []
    papg = [t["pointsAllowedPerGame"] for t in table["teams"]]
    assert papg == sorted(papg) and len(papg) == 6
    assert not any("rank" in key.lower() for key in keys_of(table))
    assert table["scheme"] == "gfc"
    first = table["teams"][0]
    assert set(first) >= {
        "team",
        "games",
        "pointsAllowedPerGame",
        "buckets",
        "provisional",
        "withheld",
    }
    assert [b["position"] for b in first["buckets"]] == ["G", "F", "C", "unknown"]
    assert table["leaguePointsAllowedPerGame"] == pytest.approx(
        sum(t["pointsAllowedPerGame"] for t in table["teams"]) / 6, rel=0.05
    )


def test_the_allocation_is_computed_once_per_data_state(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    six_team_league(store)
    calls: list[int] = []
    original = defense._allocate

    def counting(ctx: Any) -> Any:
        calls.append(1)
        return original(ctx)

    monkeypatch.setattr(defense, "_allocate", counting)
    team_payload(store, 1)
    team_payload(store, 2, window=5)
    table_payload(store, basis="perMinute")
    assert len(calls) == 1, "windows, teams and bases are cuts of one memoised allocation"
    store.league.game("late", "2025-12-30", 1, 2, 100, 90, home_lines=[(11, 30.0, 100)])
    store.league.commit()
    team_payload(store, 1)
    assert len(calls) == 2, "a new game changes the data key, so the allocation is redone"


def test_a_cut_off_keeps_only_games_that_started_before_it(store: Store) -> None:
    six_team_league(store)
    ctx = queries.build_context(store.session, SEASON, now=NOW)
    cutoff = at("2025-11-05", 5, 0)  # midnight Eastern of the 5th
    cut = defense.build_table(ctx, before=cutoff)
    full = defense.build_table(ctx)
    played = {
        t: sum(
            1
            for g in ctx.games
            if g.game_date < date(2025, 11, 5) and t in (g.home_team_id, g.away_team_id)
        )
        for t in range(1, 7)
    }
    for team in range(1, 7):
        assert cut.team(team).games == played[team]
        assert full.team(team).games == 30
    assert cut.league.team_games == sum(played.values())


# =========================================================================== bad input


def test_defence_refuses_what_it_cannot_honestly_answer(store: Store) -> None:
    hand_league(store)
    session = store.session
    with pytest.raises(ApiError) as club:  # a EuroLeague club code is not an NBA team
        nba_matchup.defense_by_position(session, team="ZZA", now=NOW)
    assert (club.value.code, club.value.http_status) == ("team_not_found", 404)
    with pytest.raises(ApiError) as unknown:
        nba_matchup.defense_by_position(session, team=999, now=NOW)
    assert unknown.value.code == "team_not_found"
    with pytest.raises(ApiError) as basis:
        nba_matchup.defense_by_position(session, team=1, basis="perHundred", now=NOW)
    assert (basis.value.code, basis.value.field) == ("bad_request", "basis")
    for window in (-1, -10):
        with pytest.raises(ApiError) as bad:
            nba_matchup.defense_by_position(session, team=1, window=window, now=NOW)
        assert (bad.value.code, bad.value.field) == ("bad_request", "window")
    with pytest.raises(ApiError) as euro:
        nba_matchup.defense_by_position(session, team=1, season="E2026", now=NOW)
    assert euro.value.code == "bad_request"
    with pytest.raises(ApiError) as missing:
        nba_matchup.defense_by_position(session, team=1, season="2030-31", now=NOW)
    assert (missing.value.code, missing.value.http_status) == ("season_not_loaded", 422)
    with pytest.raises(ApiError) as kind:
        nba_matchup.defense_by_position(session, team=1, season_type="Exhibition", now=NOW)
    assert (kind.value.code, kind.value.field) == ("bad_request", "seasonType")
    # The EuroLeague's five-way scheme is not offered here: the NBA publishes three positions.
    assert "scheme" not in "".join(
        p.name
        for p in __import__("inspect")
        .signature(nba_matchup.defense_by_position)
        .parameters.values()
    )


def test_the_season_type_is_the_one_asked_for(store: Store) -> None:
    hand_league(store)
    store.league.game(
        "po",
        "2026-05-01",
        2,
        1,
        99,
        88,
        kind="Playoffs",
        home_lines=[(21, 30.0, 99)],
        away_lines=[(11, 36.0, 88)],
    )
    store.league.commit()
    queries.clear_memo()
    regular = team_payload(store, 1)
    playoffs = team_payload(store, 1, season_type="Playoffs")
    assert regular["seasonType"] == "Regular Season" and regular["window"]["games"] == 1
    assert playoffs["seasonType"] == "Playoffs" and playoffs["window"]["games"] == 1
    assert playoffs["pointsAllowedPerGame"] == 99
    assert bucket(playoffs, "G")["pointsAllowedPerGame"] == 99.0
    assert any("Playoffs games of 2025-26 only." in note for note in playoffs["notes"])


def test_the_seeded_store_is_as_demo_as_it_says(seeded_db: Session) -> None:
    """Sanity for the fixtures that follow: the store these tests lean on is the demo league,
    and every payload says so."""
    table = nba_matchup.defense_by_position(seeded_db, team=None, season="2025-26", now=NOW)
    assert table["freshness"]["isDemo"] is True
    assert SEED_AS_OF == date(2026, 3, 1)
    assert math.isfinite(table["leaguePointsAllowedPerGame"])
