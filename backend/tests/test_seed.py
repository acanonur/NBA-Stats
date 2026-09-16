"""Tests for the synthetic league: reconciliation, era honesty and freshness.

The reconciliation tests are the load-bearing ones. If a player's points do not add up to
the team's, and the team's not to the final score, every widget in the app is quietly wrong.

The identity tests are the honest ones. The seeded league wears real NBA names, ids and
headshots and invents everything else, so they check both halves of that sentence: the names
are the file's, the numbers are stamped ``synthetic-demo``, and a 1985-86 roster never fills
up with players who are in the league today.
"""
from __future__ import annotations

from datetime import date
from typing import Iterator

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from nbastats import catalog, identities
from nbastats.models import (
    SHOT_ZONES,
    Base,
    Game,
    IdCrosswalk,
    LeagueSeason,
    Player,
    PlayerGameAdvanced,
    PlayerGameBasic,
    PlayerSeason,
    ShotZoneSeason,
    SyncState,
    Team,
    TeamSeason,
)
from nbastats.seed import ACTIVE_IDENTITY_FROM, DATA_SOURCE, NBA_TEAMS, seed_database


# --------------------------------------------------------------------------- reference


def test_all_thirty_real_franchises(seeded_db: Session) -> None:
    teams = seeded_db.execute(select(Team)).scalars().all()
    assert len(teams) == 30

    by_abbr = {team.abbr: team for team in teams}
    # Spot-check the ids and alignment the UI shows.
    assert by_abbr["LAL"].team_id == 1610612747
    assert by_abbr["BOS"].team_id == 1610612738
    assert by_abbr["GSW"].team_id == 1610612744
    assert by_abbr["OKC"].team_id == 1610612760
    assert by_abbr["LAL"].conference == "West" and by_abbr["LAL"].division == "Pacific"
    assert by_abbr["BOS"].conference == "East" and by_abbr["BOS"].division == "Atlantic"
    assert by_abbr["MIA"].division == "Southeast"

    # Real NBA.com ids are a contiguous block; no duplicates, no invented ids.
    ids = {team.team_id for team in teams}
    assert ids == {row[0] for row in NBA_TEAMS}
    assert all(1610612737 <= team_id <= 1610612766 for team_id in ids)

    conferences = {team.conference for team in teams}
    assert conferences == {"East", "West"}
    assert sum(1 for t in teams if t.conference == "East") == 15


def test_historical_seasons_only_use_franchises_that_existed(seeded_db: Session) -> None:
    """No Grizzlies in 1992-93 — the franchise did not exist yet."""
    founded = {row[0]: row[6] for row in NBA_TEAMS}
    rows = seeded_db.execute(
        select(Game.season, Game.home_team_id, Game.away_team_id)
    ).all()
    for season, home, away in rows:
        year = catalog.season_sort_key(season)
        assert founded[home] <= year
        assert founded[away] <= year


def test_players_are_plausible(seeded_db: Session, seed_summary: dict) -> None:
    players = seeded_db.execute(select(Player)).scalars().all()
    assert len(players) == seed_summary["players"] > 200
    assert len({player.full_name for player in players}) == len(players)
    assert len({player.bbref_slug for player in players}) == len(players)
    for player in players[:50]:
        assert player.from_year <= player.to_year
        assert player.height and "-" in player.height
        assert 150 <= (player.weight or 0) <= 340


# ------------------------------------------------------------------- real identities


def test_seeded_players_wear_real_nba_identities(
    seeded_db: Session, seed_summary: dict
) -> None:
    """Every name, id and photo in the demo league is the identity file's, verbatim."""
    assert seed_summary["invented_identities"] == 0, "the identity pools should cover this seed"
    assert seed_summary["real_identities_active"] > 0
    assert seed_summary["real_identities_historical"] > 0

    players = seeded_db.execute(select(Player)).scalars().all()
    assert len(players) > 200
    for player in players:
        identity = identities.player(player.player_id)
        assert identity is not None, f"{player.full_name} carries an id nobody has"
        assert player.full_name == identity.name
        assert player.first_name == identity.first_name
        assert player.last_name == identity.last_name
        assert player.headshot_url == identity.headshot_url
        assert player.headshot_url == (
            f"https://cdn.nba.com/headshots/nba/latest/1040x760/{player.player_id}.png"
        )

    # A reader opening the app sees people they have heard of, spelled the way NBA.com
    # spells them: the demo league is full of accented names and keeps every mark.
    assert any(not player.full_name.isascii() for player in players)


def test_the_identity_file_supplies_the_franchises(seeded_db: Session) -> None:
    """Team ids, cities and names come from the file; only the alignment is ours."""
    teams = seeded_db.execute(select(Team)).scalars().all()
    assert len(teams) == 30
    for team in teams:
        identity = identities.team(team.abbr)
        assert identity is not None
        assert team.team_id == identity.team_id
        assert team.name == identity.name
        assert team.city == identity.city
        assert team.year_founded == identity.year_founded
        # Conference and division are not in the file — the seeder supplies them.
        assert team.conference in ("East", "West")
        assert team.division


def test_the_crosswalk_keeps_the_real_person_id_and_labels_the_rest(
    seeded_db: Session,
) -> None:
    rows = seeded_db.execute(select(IdCrosswalk)).scalars().all()
    assert rows
    for row in rows:
        # The NBA person id is real; the ESPN and balldontlie ids are made up, so the
        # method column says where the row came from rather than claiming a match.
        assert identities.player(row.nba_person_id) is not None
        assert row.method == DATA_SOURCE


def test_every_seeded_row_is_stamped_synthetic_demo(seeded_db: Session) -> None:
    """No seeded number can be mistaken for an observation."""
    assert DATA_SOURCE == "synthetic-demo"
    stamped = [table.name for table in Base.metadata.sorted_tables if "data_source" in table.c]
    assert len(stamped) == 6, stamped
    for name in stamped:
        sources = seeded_db.execute(
            text(f"SELECT DISTINCT data_source FROM {name}")  # noqa: S608 - table names are ours
        ).scalars().all()
        assert sources == [DATA_SOURCE], f"{name} carries {sources}"


def _season_player_ids(session: Session, season: str) -> set[int]:
    return set(
        session.execute(
            select(PlayerSeason.player_id).where(PlayerSeason.season == season).distinct()
        ).scalars().all()
    )


def test_each_season_draws_from_its_own_era_pool(seeded_db: Session) -> None:
    """1992-93 gets retired players; 2024-25 and 2025-26 get today's."""
    active_ids = {person.player_id for person in identities.active_players()}
    seasons = seeded_db.execute(select(PlayerSeason.season).distinct()).scalars().all()
    assert len(seasons) >= 2
    for season in seasons:
        ids = _season_player_ids(seeded_db, season)
        assert ids, season
        if catalog.season_sort_key(season) >= ACTIVE_IDENTITY_FROM:
            assert ids <= active_ids, season
        else:
            assert ids.isdisjoint(active_ids), season


@pytest.fixture(scope="module")
def era_seeded_db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Session]:
    """A two-season league either side of the identity-pool boundary: 1985-86 and 2025-26."""
    from nbastats.db import create_db_engine, init_db

    path = tmp_path_factory.mktemp("hardwood-eras") / "eras.db"
    engine = create_db_engine(f"sqlite:///{path}")
    init_db(engine)
    with Session(engine, future=True) as session:
        seed_database(
            session, as_of=date(2026, 1, 2), seasons=["1985-86", "2025-26"],
            games_per_team=4, players_per_team=9, include_playoffs=False,
        )
        session.commit()
    session = Session(engine, future=True)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_a_1985_86_roster_never_stars_a_current_player(era_seeded_db: Session) -> None:
    """A 1985-86 season starring Victor Wembanyama would undermine the era work."""
    active_ids = {person.player_id for person in identities.active_players()}
    historical_ids = {person.player_id for person in identities.historical_players()}
    assert 1985 < ACTIVE_IDENTITY_FROM <= 2026

    modern = _season_player_ids(era_seeded_db, "2025-26")
    historic = _season_player_ids(era_seeded_db, "1985-86")
    assert modern and historic
    assert modern.isdisjoint(historic)

    # The modern season is drawn from the active list, the 1985-86 season from the
    # retired list, and neither borrows from the other.
    assert modern <= active_ids
    assert historic <= historical_ids
    assert historic.isdisjoint(active_ids)

    historic_names = set(
        era_seeded_db.execute(
            select(Player.full_name).where(Player.player_id.in_(historic))
        ).scalars().all()
    )
    assert "Victor Wembanyama" not in historic_names
    assert "Luka Dončić" not in historic_names


# --------------------------------------------------------------------- reconciliation


def test_player_points_sum_to_team_totals(seeded_db: Session) -> None:
    """Every team box total is the sum of its players' lines."""
    columns = ("pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb",
               "ast", "stl", "blk", "tov", "pf")
    sums = ", ".join(f"SUM(p.{c}) AS s_{c}" for c in columns)
    checks = " OR ".join(f"agg.s_{c} <> tg.{c}" for c in columns)
    mismatches = seeded_db.execute(
        text(
            f"""
            SELECT COUNT(*) FROM (
                SELECT p.game_id, p.team_id, {sums}
                FROM player_game_basic p GROUP BY p.game_id, p.team_id
            ) agg
            JOIN team_game tg ON tg.game_id = agg.game_id AND tg.team_id = agg.team_id
            WHERE {checks}
            """
        )
    ).scalar_one()
    assert mismatches == 0


def test_team_points_equal_the_final_score(seeded_db: Session) -> None:
    mismatches = seeded_db.execute(
        text(
            """
            SELECT COUNT(*) FROM games g
            JOIN team_game tg ON tg.game_id = g.game_id
            WHERE g.status = 'final'
              AND ((tg.is_home = 1 AND tg.pts <> g.home_pts)
                OR (tg.is_home = 0 AND tg.pts <> g.away_pts))
            """
        )
    ).scalar_one()
    assert mismatches == 0


def test_points_follow_from_the_shooting_line(seeded_db: Session) -> None:
    """PTS = 2*(FGM - 3PM) + 3*3PM + FTM for every single player line."""
    broken = seeded_db.execute(
        text(
            """
            SELECT COUNT(*) FROM player_game_basic
            WHERE pts <> 2 * fgm + COALESCE(fg3m, 0) + ftm
            """
        )
    ).scalar_one()
    assert broken == 0


def test_box_score_invariants_hold(seeded_db: Session) -> None:
    impossible = seeded_db.execute(
        text(
            """
            SELECT COUNT(*) FROM player_game_basic
            WHERE fgm > fga OR ftm > fta
               OR (fg3m IS NOT NULL AND (fg3m > fg3a OR fg3m > fgm))
               OR (reb IS NOT NULL AND oreb IS NOT NULL AND reb <> oreb + dreb)
               OR minutes < 0 OR pts < 0
            """
        )
    ).scalar_one()
    assert impossible == 0


def test_each_team_plays_240_minutes(seeded_db: Session) -> None:
    off_by_more_than_a_rounding_error = seeded_db.execute(
        text(
            """
            SELECT COUNT(*) FROM (
                SELECT game_id, team_id, SUM(minutes) AS m
                FROM player_game_basic GROUP BY game_id, team_id
            ) WHERE ABS(m - 240.0) > 0.01
            """
        )
    ).scalar_one()
    assert off_by_more_than_a_rounding_error == 0


def test_season_totals_match_the_game_log(seeded_db: Session) -> None:
    """player_season totals are exactly the sum of that player's games."""
    mismatches = seeded_db.execute(
        text(
            """
            SELECT COUNT(*) FROM player_season ps
            JOIN (
                SELECT p.player_id, g.season, g.season_type,
                       COUNT(*) AS gp, SUM(p.pts) AS pts, SUM(p.ast) AS ast,
                       SUM(p.fga) AS fga
                FROM player_game_basic p
                JOIN games g ON g.game_id = p.game_id
                GROUP BY p.player_id, g.season, g.season_type
            ) agg
              ON agg.player_id = ps.player_id AND agg.season = ps.season
             AND agg.season_type = ps.season_type
            WHERE ps.gp <> agg.gp OR ps.pts_tot <> agg.pts
               OR ps.ast_tot <> agg.ast OR ps.fga_tot <> agg.fga
            """
        )
    ).scalar_one()
    assert mismatches == 0


def test_team_records_add_up(seeded_db: Session) -> None:
    rows = seeded_db.execute(
        select(TeamSeason).where(TeamSeason.season_type == "Regular Season")
    ).scalars().all()
    assert rows
    for row in rows:
        assert row.wins + row.losses == row.gp
        assert row.win_pct == pytest.approx(row.wins / row.gp)


def test_net_rating_tracks_the_scoreboard(seeded_db: Session) -> None:
    """A team's net rating has to agree with the margin it actually produced."""
    rows = seeded_db.execute(
        select(TeamSeason).where(
            TeamSeason.season == "2024-25",
            TeamSeason.season_type == "Regular Season",
        )
    ).scalars().all()
    assert rows
    for row in rows:
        margin_per_100 = 100.0 * (row.pts - row.opp_pts) / row.pace
        assert row.net_rtg == pytest.approx(margin_per_100, abs=0.35)


# ------------------------------------------------------------------------ era honesty


def test_no_advanced_rows_before_1996_97(seeded_db: Session) -> None:
    """Per-game advanced box scores begin with league-wide play-by-play in 1996-97."""
    leaked = seeded_db.execute(
        select(func.count())
        .select_from(PlayerGameAdvanced)
        .join(Game, Game.game_id == PlayerGameAdvanced.game_id)
        .where(Game.season < "1996-97")
    ).scalar_one()
    assert leaked == 0

    # …and the modern seasons do have them.
    modern = seeded_db.execute(
        select(func.count())
        .select_from(PlayerGameAdvanced)
        .join(Game, Game.game_id == PlayerGameAdvanced.game_id)
        .where(Game.season >= "1996-97")
    ).scalar_one()
    assert modern > 0


def test_pre_1997_seasons_are_flagged_estimated(seeded_db: Session) -> None:
    old = seeded_db.execute(
        select(PlayerSeason).where(PlayerSeason.season == "1992-93")
    ).scalars().all()
    assert old, "the fixture needs a pre-advanced season"
    for row in old:
        assert row.is_estimated is True
        # Box-score derivations survive, flagged as estimates…
        assert row.usg_pct is not None
        assert row.per is not None
        assert row.ws is not None
        # …but nothing that needs possession data is invented.
        assert row.off_rtg is None
        assert row.def_rtg is None
        assert row.net_rtg is None
        assert row.pace is None
        assert row.poss is None
        assert row.pie is None
        assert row.plus_minus is None


def test_modern_seasons_are_measured_not_estimated(seeded_db: Session) -> None:
    rows = seeded_db.execute(
        select(PlayerSeason).where(
            PlayerSeason.season == "2024-25",
            PlayerSeason.season_type == "Regular Season",
        )
    ).scalars().all()
    assert rows
    assert all(row.is_estimated is False for row in rows)
    assert all(row.off_rtg is not None for row in rows)
    assert any(row.pie is not None for row in rows)


def test_era_unavailable_stats_are_null_never_zero(seeded_db: Session) -> None:
    """The whole point of the era rules: absence is NULL, and 0 would be a lie."""
    zeros = seeded_db.execute(
        text(
            """
            SELECT COUNT(*) FROM player_game_basic p
            JOIN games g ON g.game_id = p.game_id
            WHERE g.season < '1996-97' AND p.plus_minus IS NOT NULL
            """
        )
    ).scalar_one()
    assert zeros == 0

    team_zeros = seeded_db.execute(
        text(
            """
            SELECT COUNT(*) FROM team_game tg
            JOIN games g ON g.game_id = tg.game_id
            WHERE g.season < '1996-97'
              AND (tg.off_rtg IS NOT NULL OR tg.pace IS NOT NULL
                   OR tg.opp_efg_pct IS NOT NULL)
            """
        )
    ).scalar_one()
    assert team_zeros == 0


def test_seeded_values_agree_with_the_catalog(seeded_db: Session) -> None:
    """Whatever the catalog calls unavailable for a season must be NULL in that season."""
    checks = [
        ("1992-93", "off_rtg", PlayerSeason.off_rtg),
        ("1992-93", "pie", PlayerSeason.pie),
        ("2024-25", "usg_pct", PlayerSeason.usg_pct),
        ("2024-25", "ts_pct", PlayerSeason.ts_pct),
    ]
    for season, metric_key, column in checks:
        availability = catalog.metric_availability(metric_key, season, "season")
        values = seeded_db.execute(
            select(column).where(PlayerSeason.season == season).limit(50)
        ).scalars().all()
        assert values, f"no rows for {season}"
        if availability == "unavailable":
            assert all(value is None for value in values), f"{metric_key} {season}"
        else:
            assert any(value is not None for value in values), f"{metric_key} {season}"


def test_shot_charts_start_in_1996_97(seeded_db: Session) -> None:
    old = seeded_db.execute(
        select(func.count()).select_from(ShotZoneSeason).where(ShotZoneSeason.season < "1996-97")
    ).scalar_one()
    assert old == 0

    zones = seeded_db.execute(
        select(ShotZoneSeason).where(
            ShotZoneSeason.subject_type == "team",
            ShotZoneSeason.season == "2024-25",
            ShotZoneSeason.season_type == "Regular Season",
        )
    ).scalars().all()
    assert zones
    by_subject: dict[int, list[ShotZoneSeason]] = {}
    for zone in zones:
        by_subject.setdefault(zone.subject_id, []).append(zone)
    for subject_zones in list(by_subject.values())[:5]:
        assert {z.zone for z in subject_zones} == set(SHOT_ZONES)
        assert sum(z.share_of_fga for z in subject_zones) == pytest.approx(1.0, abs=1e-6)
        for zone in subject_zones:
            assert 0.0 <= zone.fg_pct <= 1.0


# --------------------------------------------------------------------------- freshness


def test_data_through_is_the_last_final_game(seeded_db: Session, seed_summary: dict) -> None:
    state = seeded_db.execute(select(SyncState)).scalar_one()
    last_final = seeded_db.execute(
        select(func.max(Game.game_date)).where(Game.status == "final")
    ).scalar_one()
    assert state.data_through == last_final
    assert state.data_through == seed_summary["data_through"]
    assert state.sync_version == seed_summary["final_games"] > 0
    assert state.games_ingested == state.sync_version


def test_in_progress_season_has_a_future(seeded_db: Session) -> None:
    """The current season is mid-flight: finals behind ``as_of``, schedule ahead of it."""
    from tests.conftest import SEED_AS_OF

    final_dates = seeded_db.execute(
        select(func.max(Game.game_date)).where(
            Game.season == "2025-26", Game.status == "final"
        )
    ).scalar_one()
    assert final_dates <= SEED_AS_OF

    upcoming = seeded_db.execute(
        select(func.min(Game.game_date)).where(
            Game.season == "2025-26", Game.status == "scheduled"
        )
    ).scalar_one()
    assert upcoming > SEED_AS_OF


def test_scheduled_games_carry_no_box_score(seeded_db: Session) -> None:
    scheduled = seeded_db.execute(
        select(Game).where(Game.status == "scheduled")
    ).scalars().all()
    assert scheduled
    ids = {game.game_id for game in scheduled}
    for game in scheduled[:20]:
        assert game.home_pts is None and game.away_pts is None
        assert game.finalized_at is None

    leaked = seeded_db.execute(
        select(func.count())
        .select_from(PlayerGameBasic)
        .where(PlayerGameBasic.game_id.in_(list(ids)[:400]))
    ).scalar_one()
    assert leaked == 0


def test_final_games_are_complete(seeded_db: Session) -> None:
    incomplete = seeded_db.execute(
        text(
            """
            SELECT COUNT(*) FROM games
            WHERE status = 'final'
              AND (home_pts IS NULL OR away_pts IS NULL OR finalized_at IS NULL
                   OR home_pts = away_pts)
            """
        )
    ).scalar_one()
    assert incomplete == 0


def test_every_final_game_has_two_team_rows(seeded_db: Session) -> None:
    bad = seeded_db.execute(
        text(
            """
            SELECT COUNT(*) FROM (
                SELECT g.game_id, COUNT(tg.team_id) AS n
                FROM games g LEFT JOIN team_game tg ON tg.game_id = g.game_id
                WHERE g.status = 'final'
                GROUP BY g.game_id
            ) WHERE n <> 2
            """
        )
    ).scalar_one()
    assert bad == 0


# --------------------------------------------------------------- distributions & scale


def test_league_distribution_rows_are_ordered(seeded_db: Session) -> None:
    rows = seeded_db.execute(select(LeagueSeason)).scalars().all()
    assert rows
    for row in rows:
        assert row.sample_size >= 2
        assert row.min_value <= row.p10 <= row.p25 <= row.p50 <= row.p75 <= row.p90
        assert row.p90 <= row.max_value
        assert row.min_value <= row.average <= row.max_value
        assert row.stddev >= 0.0
    subject_types = {row.subject_type for row in rows}
    assert subject_types == {"player", "team"}


def test_league_distributions_respect_the_era(seeded_db: Session) -> None:
    for row in seeded_db.execute(select(LeagueSeason)).scalars().all():
        assert catalog.metric_availability(row.metric_key, row.season, "season") != "unavailable"


def test_modern_league_averages_look_like_the_nba(seeded_db: Session) -> None:
    """Sanity-check the synthetic league against the real one it is imitating."""
    teams = seeded_db.execute(
        select(TeamSeason).where(
            TeamSeason.season == "2024-25", TeamSeason.season_type == "Regular Season"
        )
    ).scalars().all()
    assert len(teams) == 30
    points = sum(t.pts for t in teams) / len(teams)
    off_rtg = sum(t.off_rtg for t in teams) / len(teams)
    pace = sum(t.pace for t in teams) / len(teams)
    three_rate = sum(t.fg3a_rate for t in teams) / len(teams)
    assert 105.0 < points < 122.0
    assert 108.0 < off_rtg < 120.0
    assert 93.0 < pace < 106.0
    assert 0.33 < three_rate < 0.50


def test_1992_93_looks_like_1992_93(seeded_db: Session) -> None:
    teams = seeded_db.execute(
        select(TeamSeason).where(
            TeamSeason.season == "1992-93", TeamSeason.season_type == "Regular Season"
        )
    ).scalars().all()
    assert teams
    points = sum(t.pts for t in teams) / len(teams)
    three_rate = sum(t.fg3a_rate for t in teams) / len(teams)
    assert 98.0 < points < 114.0
    # The three-point line existed but nobody lived behind it yet.
    assert 0.05 < three_rate < 0.18


def test_archetypes_produce_different_players(seeded_db: Session) -> None:
    """Bigs rebound and block; guards pass. Otherwise the leaderboards are meaningless."""
    rows = seeded_db.execute(
        select(PlayerSeason, Player)
        .join(Player, Player.player_id == PlayerSeason.player_id)
        .where(
            PlayerSeason.season == "2024-25",
            PlayerSeason.season_type == "Regular Season",
            PlayerSeason.min_pg > 20,
        )
    ).all()
    assert rows
    guards = [season for season, player in rows if player.position == "G"]
    centres = [season for season, player in rows if player.position in ("C", "F-C")]
    assert guards and centres
    assert (sum(s.ast for s in guards) / len(guards)) > (
        sum(s.ast for s in centres) / len(centres)
    )
    assert (sum(s.blk for s in centres) / len(centres)) > (
        sum(s.blk for s in guards) / len(guards)
    )
    assert (sum(s.oreb for s in centres) / len(centres)) > (
        sum(s.oreb for s in guards) / len(guards)
    )


def test_playoffs_exist_and_are_shorter(seeded_db: Session) -> None:
    playoff_games = seeded_db.execute(
        select(func.count()).select_from(Game).where(
            Game.season == "2024-25", Game.season_type == "Playoffs"
        )
    ).scalar_one()
    assert 50 <= playoff_games <= 105

    series = seeded_db.execute(
        select(TeamSeason).where(
            TeamSeason.season == "2024-25", TeamSeason.season_type == "Playoffs"
        )
    ).scalars().all()
    assert len(series) >= 16
    assert max(row.gp for row in series) <= 28


def test_play_in_only_exists_in_the_modern_era(seeded_db: Session) -> None:
    seasons = seeded_db.execute(
        select(Game.season).where(Game.season_type == "Play In").distinct()
    ).scalars().all()
    for season in seasons:
        assert catalog.season_sort_key(season) >= 2020


# ------------------------------------------------------------------------ determinism


def test_seed_is_deterministic(empty_engine: Engine, tmp_path) -> None:
    """The same seed produces the same league, twice — the same numbers *and* the same people."""
    from nbastats.db import create_db_engine, init_db

    def fingerprint(engine: Engine) -> tuple:
        with Session(engine, future=True) as session:
            return (
                session.execute(
                    text("SELECT COUNT(*), SUM(pts), SUM(fga) FROM player_game_basic")
                ).one(),
                session.execute(
                    text("SELECT COUNT(*), SUM(home_pts), SUM(away_pts) FROM games")
                ).one(),
                session.execute(text("SELECT COUNT(*) FROM players")).one(),
                # Which real identities were drawn, and onto which team they landed: the
                # draw runs off the same seeded Random as everything else, so it has to be
                # reproducible too.
                tuple(
                    session.execute(
                        text(
                            "SELECT p.player_id, p.full_name, p.headshot_url, ps.team_id "
                            "FROM players p JOIN player_season ps "
                            "ON ps.player_id = p.player_id "
                            "ORDER BY p.player_id, ps.season, ps.season_type"
                        )
                    ).all()
                ),
            )

    with Session(empty_engine, future=True) as session:
        first = seed_database(
            session, as_of=date(2026, 1, 2), seasons=["2024-25"],
            games_per_team=4, players_per_team=9, include_playoffs=False,
        )
        session.commit()

    second_engine = create_db_engine(f"sqlite:///{tmp_path / 'again.db'}")
    init_db(second_engine)
    with Session(second_engine, future=True) as session:
        second = seed_database(
            session, as_of=date(2026, 1, 2), seasons=["2024-25"],
            games_per_team=4, players_per_team=9, include_playoffs=False,
        )
        session.commit()

    assert fingerprint(empty_engine) == fingerprint(second_engine)
    for key in ("players", "games", "final_games", "player_game_rows"):
        assert first[key] == second[key]
    second_engine.dispose()


def test_seeding_twice_replaces_rather_than_duplicates(empty_engine: Engine) -> None:
    with Session(empty_engine, future=True) as session:
        for _ in range(2):
            seed_database(
                session, as_of=date(2026, 1, 2), seasons=["2024-25"],
                games_per_team=4, players_per_team=9, include_playoffs=False,
            )
            session.commit()
        games = session.execute(select(func.count()).select_from(Game)).scalar_one()
        teams = session.execute(select(func.count()).select_from(Team)).scalar_one()
    assert teams == 30
    assert games == 60  # 30 teams x 4 games / 2
