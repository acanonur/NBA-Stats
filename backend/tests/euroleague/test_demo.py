"""The invented league: deterministic, shaped like the workbook, and honest by construction.

The demo is what every committed EuroLeague fixture and every offline EuroLeague test is built
from, so these tests pin its promises: twenty invented clubs, four final rounds and a fifth to
play, one neutral game, the workbook-shaped rounds (NULL where the workbook has nothing) beside
the live-shaped ones, every box score passing the same invariants as a real one, nothing real in
it, and the same rows byte for byte on every run.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from nbastats.euroleague import demo
from nbastats.euroleague.db import (
    StoreKindMismatch,
    create_el_engine,
    ensure_identity,
    init_el_db,
    is_synthetic_store,
)
from nbastats.euroleague.importers.workbook import GameFacts, Line, check_game
from nbastats.euroleague.profile import (
    POSITION5_TO_CODE,
    load_club_codes,
    load_source_denylist,
    is_denied_url,
)


def rows(engine: Engine, sql: str, **params: Any) -> list[tuple[Any, ...]]:
    with engine.connect() as connection:
        return [tuple(r) for r in connection.execute(text(sql), params).all()]


def scalar(engine: Engine, sql: str, **params: Any) -> Any:
    return rows(engine, sql, **params)[0][0]


def dump(engine: Engine) -> str:
    """Every row of every table, in a stable order, as text."""
    out: list[str] = []
    for (name,) in rows(
        engine, "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ):
        out.append(f"== {name}")
        out.extend(sorted(repr(r) for r in rows(engine, f"SELECT * FROM {name}")))
    return "\n".join(out)


# --------------------------------------------------------------------------- determinism


def test_two_seeds_produce_identical_stores(tmp_path: Path) -> None:
    dumps = []
    for name in ("a", "b"):
        engine = create_el_engine(f"sqlite:///{tmp_path / (name + '.db')}")
        init_el_db(engine)
        with Session(engine) as session:
            demo.seed_demo(session)
            session.commit()
        dumps.append(dump(engine))
        engine.dispose()
    assert dumps[0] == dumps[1]
    assert len(dumps[0]) > 100_000


def test_the_demo_never_reads_the_clock() -> None:
    assert demo.DEMO_AS_OF == datetime(2026, 10, 12, 9, 0, 0)
    source = Path(demo.__file__).read_text(encoding="utf-8")
    assert "utcnow" not in source and "datetime.now" not in source and "date.today" not in source


@pytest.mark.parametrize(
    "clock", [datetime(2026, 10, 5, 1, 19), datetime(2026, 9, 1, 12, 0), datetime(2026, 10, 12, 8)]
)
def test_a_store_seeded_before_the_demos_moment_shows_nothing_after_the_clock(
    tmp_path: Path, clock: datetime
) -> None:
    """An app started on 5 October 2026 must not show final scores dated 8 October: the calendar
    moves back by whole days so every result is before the clock and round 5 is still ahead."""
    engine = create_el_engine(f"sqlite:///{tmp_path / 'anchored.db'}")
    init_el_db(engine)
    with Session(engine) as session:
        demo.seed_demo(session, anchor=clock)
        session.commit()
    finals = rows(engine, "SELECT game_date, tipoff_utc FROM el_game WHERE status = 'final'")
    scheduled = rows(engine, "SELECT game_date, tipoff_utc FROM el_game WHERE status = 'scheduled'")
    assert finals and scheduled
    assert max(str(d) for d, _ in finals) < clock.date().isoformat()
    assert max(datetime.fromisoformat(str(t)) for _, t in finals if t is not None) < clock
    assert min(str(d) for d, _ in scheduled) >= clock.date().isoformat()
    assert min(datetime.fromisoformat(str(t)) for _, t in scheduled if t is not None) > clock
    published = rows(engine, "SELECT MAX(source_published_at) FROM el_intel_status")[0][0]
    assert datetime.fromisoformat(str(published)) <= clock
    shift = demo.demo_shift(clock)
    assert shift.total_seconds() % 86400 == 0
    assert clock - timedelta(days=1) < demo.DEMO_AS_OF + shift <= clock
    engine.dispose()
    # no anchor, or a clock at or after the demo's moment: the fixed calendar, unchanged
    assert demo.demo_shift(None) == demo.demo_shift(datetime(2027, 1, 1)) == demo.demo_shift(
        demo.DEMO_AS_OF
    )
    assert not demo.demo_shift(None)


def test_seeding_is_idempotent_and_replace_rebuilds_the_same_rows(
    fresh_demo_engine: Engine,
) -> None:
    before = dump(fresh_demo_engine)
    with Session(fresh_demo_engine) as session:
        again = demo.seed_demo(session)
        session.commit()
    assert again.seeded is False and again.games_final == 40
    assert dump(fresh_demo_engine) == before
    with Session(fresh_demo_engine) as session:
        rebuilt = demo.seed_demo(session, replace=True)
        session.commit()
    assert rebuilt.seeded is True
    assert dump(fresh_demo_engine) == before


def test_the_summary_is_json(fresh_demo_engine: Engine) -> None:
    with Session(fresh_demo_engine) as session:
        summary = demo.seed_demo(session)
    document = json.loads(json.dumps(summary.to_dict()))
    assert (
        document["clubs"] == 20
        and document["gamesFinal"] == 40
        and document["gamesScheduled"] == 10
    )
    assert document["dataThrough"] == "2026-10-09" and document["syncVersion"] == 1


# --------------------------------------------------------------------------- shape


def test_twenty_invented_clubs(demo_engine: Engine) -> None:
    clubs = rows(
        demo_engine,
        "SELECT club_code, name, short_name, country_code FROM el_club ORDER BY club_code",
    )
    assert [c[0] for c in clubs] == [f"ZZ{chr(65 + i)}" for i in range(20)]
    assert len({c[1] for c in clubs}) == 20
    assert {c[3] for c in clubs} <= {"XA", "XB", "XC", "XD"}  # ISO user-assigned: no real country
    assert [c.code for c in demo.demo_clubs()] == [c[0] for c in clubs]
    assert rows(
        demo_engine, "SELECT COUNT(*) FROM el_club_season WHERE coach_name IS NOT NULL"
    ) == [(20,)]
    aliases = rows(demo_engine, "SELECT system, code, club_code FROM el_club_alias")
    assert len(aliases) == 40 and all(code == club for _, code, club in aliases)


def test_squads_of_fourteen_to_eighteen(demo_engine: Engine) -> None:
    sizes = [
        r[0] for r in rows(demo_engine, "SELECT COUNT(*) FROM el_registration GROUP BY club_code")
    ]
    assert len(sizes) == 20 and all(14 <= n <= 18 for n in sizes)
    persons = rows(demo_engine, "SELECT person_code, code_system, name FROM el_person")
    assert len(persons) == sum(sizes)
    assert all(code.startswith("demo-") and system == "demo" for code, system, _ in persons)
    assert len({name for *_, name in persons}) == len(persons)
    labels = {
        r[0] for r in rows(demo_engine, "SELECT DISTINCT position5_workbook FROM el_registration")
    }
    assert labels == {"PG", "SG", "SF", "PF", "C"}


def test_the_workbook_label_and_the_registered_position_sometimes_disagree(
    demo_engine: Engine,
) -> None:
    rows_ = rows(demo_engine, "SELECT position5_workbook, position_code FROM el_registration")
    assert all(code in (1, 2, 3) for _, code in rows_)
    disagree = [1 for label, code in rows_ if POSITION5_TO_CODE[label] != code]
    assert 0 < len(disagree) < len(rows_) * 0.3


def test_four_final_rounds_a_scheduled_one_and_one_neutral_game(demo_engine: Engine) -> None:
    assert rows(
        demo_engine, "SELECT round_number, status, COUNT(*) FROM el_game GROUP BY 1, 2 ORDER BY 1"
    ) == [
        (1, "final", 10),
        (2, "final", 10),
        (3, "final", 10),
        (4, "final", 10),
        (5, "scheduled", 10),
    ]
    assert scalar(demo_engine, "SELECT COUNT(*) FROM el_game WHERE is_neutral = 1") == 1
    neutral = rows(
        demo_engine, "SELECT game_id, round_number, venue_name FROM el_game WHERE is_neutral = 1"
    )
    assert neutral == [(demo.DEMO_NEUTRAL_GAME_ID, 3, "Neutral Court Arena")]
    codes = [
        r[0]
        for r in rows(
            demo_engine, "SELECT game_code FROM el_game WHERE status = 'final' ORDER BY 1"
        )
    ]
    assert codes == list(range(1, 41))
    ids = [
        r[0]
        for r in rows(
            demo_engine, "SELECT game_id FROM el_game WHERE status = 'scheduled' ORDER BY 1"
        )
    ]
    assert ids == [f"E2026-R05-{n:02d}" for n in range(1, 11)]
    for clubs in rows(
        demo_engine, "SELECT home_club_code, away_club_code FROM el_game WHERE round_number = 5"
    ):
        assert clubs[0] != clubs[1]
    per_round = rows(
        demo_engine,
        "SELECT round_number, COUNT(DISTINCT home_club_code) + COUNT(DISTINCT away_club_code) "
        "FROM el_game "
        "GROUP BY 1",
    )
    assert all(n == 20 for _, n in per_round)  # every club plays once a round


def test_scheduled_games_have_no_result_and_no_stats(demo_engine: Engine) -> None:
    row = rows(
        demo_engine,
        "SELECT home_pts, away_pts, stats_status, ot_periods, home_partials_json, tipoff_utc FROM "
        "el_game "
        "WHERE status = 'scheduled' LIMIT 1",
    )[0]
    assert row[:5] == (None, None, "none", None, None) and row[5] is not None
    assert (
        scalar(demo_engine, "SELECT COUNT(*) FROM el_player_game WHERE game_id LIKE 'E2026-R05-%'")
        == 0
    )
    assert (
        scalar(demo_engine, "SELECT COUNT(*) FROM el_team_game WHERE game_id LIKE 'E2026-R05-%'")
        == 0
    )


def test_everything_is_stamped_synthetic(demo_engine: Engine) -> None:
    assert rows(demo_engine, "SELECT league, kind FROM el_store_identity") == [
        ("euroleague", "synthetic")
    ]
    assert {r[0] for r in rows(demo_engine, "SELECT DISTINCT data_source FROM el_game")} == {
        "synthetic-demo"
    }
    assert {r[0] for r in rows(demo_engine, "SELECT DISTINCT source FROM el_registration")} == {
        "synthetic-demo"
    }
    assert {r[0] for r in rows(demo_engine, "SELECT DISTINCT source FROM el_team_rating")} == {
        "syntheticDemo"
    }
    assert {r[0] for r in rows(demo_engine, "SELECT DISTINCT basis FROM el_player_rate")} == {
        "syntheticDemo"
    }
    assert rows(demo_engine, "SELECT sync_version, mode, data_through FROM el_sync_state") == [
        (1, "demo", "2026-10-09")
    ]
    with Session(demo_engine) as session:
        assert is_synthetic_store(session)


# ---------------------------------------- workbook-shaped and live-shaped


def test_rounds_one_and_two_look_like_a_workbook_import(demo_engine: Engine) -> None:
    played = (
        "participation = 'played' AND game_id IN "
        "(SELECT game_id FROM el_game WHERE round_number <= 2)"
    )
    total = scalar(demo_engine, f"SELECT COUNT(*) FROM el_player_game WHERE {played}")
    assert total > 300
    for column in (
        "fouls_drawn",
        "blk_against",
        "plus_minus",
        "is_starter",
        "position_code_at_game",
    ):
        assert (
            scalar(
                demo_engine,
                f"SELECT COUNT(*) FROM el_player_game WHERE {played} AND {column} IS NULL",
            )
            == total
        )
    # the workbook's first round says nothing about partials, venue, attendance or neutrality
    first = rows(
        demo_engine,
        "SELECT home_partials_json, venue_name, attendance, is_neutral, tipoff_utc "
        "FROM el_game WHERE round_number = 1",
    )
    assert first == [(None, None, None, None, None)] * 10
    second = rows(
        demo_engine,
        "SELECT home_partials_json IS NOT NULL, venue_name IS NOT NULL, "
        "attendance IS NOT NULL, is_neutral FROM el_game WHERE round_number = 2",
    )
    assert second == [(1, 1, 1, 0)] * 10


def test_rounds_three_and_four_look_like_live_ingest(demo_engine: Engine) -> None:
    played = (
        "participation = 'played' AND game_id IN "
        "(SELECT game_id FROM el_game WHERE round_number IN (3, 4))"
    )
    total = scalar(demo_engine, f"SELECT COUNT(*) FROM el_player_game WHERE {played}")
    assert total > 300
    for column in (
        "fouls_drawn",
        "blk_against",
        "plus_minus",
        "is_starter",
        "position_code_at_game",
        "dorsal",
    ):
        assert (
            scalar(
                demo_engine,
                f"SELECT COUNT(*) FROM el_player_game WHERE {played} AND {column} IS NULL",
            )
            == 0
        )
    assert (
        scalar(
            demo_engine,
            "SELECT COUNT(*) FROM el_game WHERE round_number IN (3, 4) AND tipoff_utc IS NULL",
        )
        == 0
    )


def test_per_column_divisors_have_something_to_divide(demo_engine: Engine) -> None:
    """Half the lines carry fouls drawn and half do not: averaging all as if recorded would be "
    "wrong."""
    with_fd = scalar(
        demo_engine,
        "SELECT COUNT(*) FROM el_player_game WHERE participation = 'played' AND fouls_drawn IS "
        "NOT NULL",
    )
    played = scalar(
        demo_engine, "SELECT COUNT(*) FROM el_player_game WHERE participation = 'played'"
    )
    assert 0 < with_fd < played
    assert abs(with_fd / played - 0.5) < 0.05


def test_a_dnp_line_has_every_stat_null(demo_engine: Engine) -> None:
    dnp = scalar(demo_engine, "SELECT COUNT(*) FROM el_player_game WHERE participation = 'dnp'")
    assert dnp > 40
    stats = (
        "seconds_played",
        "pts",
        "fgm2",
        "fga2",
        "fgm3",
        "fga3",
        "ftm",
        "fta",
        "oreb",
        "dreb",
        "reb",
        "ast",
        "stl",
        "tov",
        "blk",
        "blk_against",
        "pf",
        "fouls_drawn",
        "plus_minus",
        "pir_official",
    )
    assert (
        scalar(
            demo_engine,
            "SELECT COUNT(*) FROM el_player_game WHERE participation = 'dnp' AND ("
            + " OR ".join(f"{c} IS NOT NULL" for c in stats)
            + ")",
        )
        == 0
    )


# ---------------------------------------- every box score is honest


def _facts(engine: Engine, game_id: str) -> GameFacts:
    game = rows(
        engine,
        "SELECT home_club_code, away_club_code, home_pts, away_pts, home_partials_json, "
        "away_partials_json FROM el_game WHERE game_id = :g",
        g=game_id,
    )[0]
    sides = {game[0]: "home", game[1]: "away"}
    stat_columns = [
        "seconds_played",
        "pts",
        "fgm2",
        "fga2",
        "fgm3",
        "fga3",
        "ftm",
        "fta",
        "oreb",
        "dreb",
        "reb",
        "ast",
        "stl",
        "tov",
        "blk",
        "blk_against",
        "pf",
        "fouls_drawn",
        "plus_minus",
        "pir_official",
    ]
    players = []
    for row in rows(
        engine,
        f"SELECT club_code, participation, is_starter, {', '.join(stat_columns)} "
        "FROM el_player_game WHERE game_id = :g",
        g=game_id,
    ):
        values = dict(zip(stat_columns, row[3:]))
        seconds = values.pop("seconds_played")
        players.append(
            Line(
                side=sides[row[0]],
                name="p",
                participation=row[1],
                is_starter=None if row[2] is None else bool(row[2]),
                seconds=seconds,
                **values,
            )
        )
    teams = {}
    for row in rows(
        engine,
        f"SELECT club_code, {', '.join(stat_columns)}, pts FROM el_team_game WHERE game_id = :g",
        g=game_id,
    ):
        values = dict(zip(stat_columns, row[1:-1]))
        seconds = values.pop("seconds_played")
        values["pts"] = row[-1]
        teams[sides[row[0]]] = Line(side=sides[row[0]], seconds=seconds, **values)
    partials = (json.loads(game[4]), json.loads(game[5])) if game[4] else (None, None)
    return GameFacts(
        final_home=game[2],
        final_away=game[3],
        players=players,
        teams=teams,
        home_partials=partials[0],
        away_partials=partials[1],
        seconds_tolerance=0,
    )


def test_every_final_game_passes_every_hard_invariant(demo_engine: Engine) -> None:
    for (game_id,) in rows(
        demo_engine, "SELECT game_id FROM el_game WHERE status = 'final' ORDER BY 1"
    ):
        result = check_game(_facts(demo_engine, game_id))
        assert not result.hard, (game_id, [v.message for v in result.hard])


def test_the_live_shaped_rounds_pass_the_soft_invariants_too(demo_engine: Engine) -> None:
    for (game_id,) in rows(
        demo_engine,
        "SELECT game_id FROM el_game WHERE round_number >= 3 " "AND status = 'final' ORDER BY 1",
    ):
        result = check_game(_facts(demo_engine, game_id))
        assert result.violations == (), (game_id, [v.message for v in result.violations])


def test_the_sums_agree_in_sql(demo_engine: Engine) -> None:
    assert (
        scalar(
            demo_engine,
            "SELECT COUNT(*) FROM el_team_game t WHERE t.pts != (SELECT SUM(p.pts) FROM "
            "el_player_game p "
            "WHERE p.game_id = t.game_id AND p.club_code = t.club_code)",
        )
        == 0
    )
    assert (
        scalar(
            demo_engine,
            "SELECT COUNT(*) FROM el_team_game t WHERE t.seconds_played != 12000 + 1500 * "
            "(SELECT ot_periods FROM el_game g WHERE g.game_id = t.game_id)",
        )
        == 0
    )
    assert (
        scalar(
            demo_engine,
            "SELECT COUNT(*) FROM el_team_game t WHERE t.seconds_played != "
            "(SELECT SUM(p.seconds_played) FROM el_player_game p WHERE p.game_id = t.game_id "
            "AND p.club_code = t.club_code)",
        )
        == 0
    )
    assert (
        scalar(
            demo_engine,
            "SELECT COUNT(*) FROM el_player_game WHERE participation = 'played' "
            "AND pts != 2 * fgm2 + 3 * fgm3 + ftm",
        )
        == 0
    )
    assert scalar(demo_engine, "SELECT COUNT(*) FROM el_player_game WHERE reb != oreb + dreb") == 0
    # fouls drawn by one side are the personal fouls of the other (live-shaped rounds)
    assert (
        scalar(
            demo_engine,
            "SELECT COUNT(*) FROM el_team_game a JOIN el_team_game b ON a.game_id = b.game_id AND "
            "a.club_code != b.club_code WHERE a.fouls_drawn IS NOT NULL AND a.fouls_drawn != b.pf",
        )
        == 0
    )
    assert scalar(demo_engine, "SELECT COUNT(*) FROM el_player_game WHERE pf > 5") == 0


def test_overtime_games_are_consistent(demo_engine: Engine) -> None:
    overtime = rows(
        demo_engine,
        "SELECT game_id, home_partials_json, home_pts, away_pts FROM el_game "
        "WHERE ot_periods = 1",
    )
    assert overtime, "the invented league has at least one overtime game"
    for _, partials, home, away in overtime:
        parts = json.loads(partials) if partials else None
        if parts is not None:
            assert len(parts) == 5 and sum(parts) == home
        assert home != away
    for game_id, *_ in overtime:
        assert {
            r[0]
            for r in rows(
                demo_engine, "SELECT seconds_played FROM el_team_game WHERE game_id = :g", g=game_id
            )
        } == {13_500}


def test_partials_sum_to_the_final_from_round_two_on(demo_engine: Engine) -> None:
    for home_json, away_json, home, away in rows(
        demo_engine,
        "SELECT home_partials_json, away_partials_json, home_pts, away_pts FROM el_game "
        "WHERE round_number >= 2 AND status = 'final'",
    ):
        assert sum(json.loads(home_json)) == home and sum(json.loads(away_json)) == away
        assert min(json.loads(home_json) + json.loads(away_json)) >= 6


def test_scores_are_plausible_and_never_tied(demo_engine: Engine) -> None:
    scores = rows(demo_engine, "SELECT home_pts, away_pts FROM el_game WHERE status = 'final'")
    assert all(home != away for home, away in scores)
    assert all(50 < home < 125 and 50 < away < 125 for home, away in scores)
    mean = sum(h + a for h, a in scores) / (2 * len(scores))
    assert 78 < mean < 95


# --------------------------------------------------------------------------- model inputs


def test_ratings_and_rates_stand_as_of_round_two(demo_engine: Engine) -> None:
    assert rows(demo_engine, "SELECT as_of_round, COUNT(*) FROM el_team_rating GROUP BY 1") == [
        (2, 20)
    ]
    assert rows(
        demo_engine,
        "SELECT as_of_round, COUNT(DISTINCT person_code) FROM el_player_rate GROUP BY 1",
    ) == [(2, scalar(demo_engine, "SELECT COUNT(*) FROM el_person"))]
    assert rows(
        demo_engine, "SELECT club_code FROM el_team_rating WHERE prior_is_estimate = 1"
    ) == [("ZZT",)]
    assert {
        r[0] for r in rows(demo_engine, "SELECT DISTINCT prior_minutes FROM el_player_rate")
    } == {400.0, 150.0}
    assert (
        scalar(demo_engine, "SELECT COUNT(*) FROM el_team_rating WHERE update_weight IS NOT NULL")
        == 0
    )


def test_a_zero_attempt_percentage_is_null_in_the_rates(demo_engine: Engine) -> None:
    zero = scalar(demo_engine, "SELECT COUNT(*) FROM el_player_rate WHERE fga3_40 = 0")
    assert zero > 0
    assert (
        scalar(
            demo_engine, "SELECT COUNT(*) FROM el_player_rate WHERE fga3_40 = 0 AND fg3_pct IS NULL"
        )
        == zero
    )
    assert scalar(demo_engine, "SELECT COUNT(*) FROM el_player_rate WHERE fg3_pct = 0") == 0
    assert (
        scalar(
            demo_engine,
            "SELECT COUNT(*) FROM el_player_rate WHERE fg2_pct > 1 OR fg3_pct > 1 OR ft_pct > 1",
        )
        == 0
    )


# --------------------------------------------------------------------------- availability


def test_thirty_invented_statuses_with_example_links(demo_engine: Engine) -> None:
    statuses = rows(
        demo_engine,
        "SELECT status, reason_category, source_kind, source_url, source_published_at, "
        "person_code, "
        "expected_return_text, expected_return_round_from, game_id FROM el_intel_status",
    )
    assert len(statuses) == 30
    assert {s[0] for s in statuses} == {"out", "doubtful", "questionable", "probable", "available"}
    urls = [s[3] for s in statuses if s[3]]
    assert urls and all(re.match(r"https://[a-z.]*example\.org/", u) for u in urls)
    assert not any(is_denied_url(u, load_source_denylist()) for u in urls)
    assert all(s[2] != "manual" or s[3] is None for s in statuses)  # a manual entry carries no link
    assert all(
        s[4] < "2026-10-12 09:00" for s in statuses
    )  # every source predates the demo's "now"
    assert sum(1 for s in statuses if s[5] is None) == 1  # exactly one name matches nobody
    assert any(s[7] is not None for s in statuses) and any(s[8] is not None for s in statuses)
    assert rows(demo_engine, "SELECT source_kind, row_count FROM el_intel_batch") == [
        ("manual", 30)
    ]


# --------------------------------------------------------------------------- nothing real


def test_nothing_in_the_demo_is_a_real_club_or_code(demo_engine: Engine) -> None:
    real = load_club_codes()
    real_names = {r.name.lower() for r in real.rows} | {
        (r.short_name or "").lower() for r in real.rows
    }
    real_codes = {r.workbook_code for r in real.rows} | {r.official_code for r in real.rows}
    demo_names = {r[0].lower() for r in rows(demo_engine, "SELECT name FROM el_club")}
    demo_names |= {r[0].lower() for r in rows(demo_engine, "SELECT short_name FROM el_club")}
    demo_codes = {r[0] for r in rows(demo_engine, "SELECT club_code FROM el_club")}
    assert not demo_names & real_names and not demo_codes & real_codes


def test_no_real_person_or_link_appears_in_the_demo(demo_engine: Engine) -> None:
    text_dump = dump(demo_engine)
    for forbidden in (
        "euroleague.net",
        "basketnews",
        "eurohoops",
        "mozzart",
        "realgm",
        "proballers",
    ):
        assert forbidden not in text_dump.lower()
    for url in set(re.findall(r"https?://[^'\", )]+", text_dump)):
        assert "example.org" in url


def test_the_whole_demo_store_has_consistent_references(demo_engine: Engine) -> None:
    assert rows(demo_engine, "PRAGMA foreign_key_check") == []


# --------------------------------------------------------------------------- kinds


def test_a_real_store_refuses_the_invented_league(el_engine: Engine) -> None:
    with Session(el_engine) as session:
        ensure_identity(session, "workbook")
        session.commit()
    with Session(el_engine) as session:
        with pytest.raises(StoreKindMismatch):
            demo.seed_demo(session)
    assert scalar(el_engine, "SELECT COUNT(*) FROM el_club") == 0


def test_clearing_refuses_a_real_store(el_engine: Engine) -> None:
    with Session(el_engine) as session:
        ensure_identity(session, "live")
        with pytest.raises(ValueError, match="real data"):
            demo.clear_store(session)


def test_a_fresh_store_is_stamped_synthetic_by_seeding(el_engine: Engine) -> None:
    assert scalar(el_engine, "SELECT COUNT(*) FROM el_store_identity") == 0
    with Session(el_engine) as session:
        demo.seed_demo(session)
        session.commit()
    assert scalar(el_engine, "SELECT kind FROM el_store_identity") == "synthetic"


def test_the_generator_refuses_to_write_an_impossible_game(
    el_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bug in the generator must raise, not put an impossible box score into a fixture."""
    original = demo._team_lines

    def broken(*args: Any, **kwargs: Any) -> Any:
        lines, team = original(*args, **kwargs)
        for _, line in lines:
            if line.participation == "played":
                line.pts += 1
                break
        return lines, team

    monkeypatch.setattr(demo, "_team_lines", broken)
    with Session(el_engine) as session:
        with pytest.raises(AssertionError, match="demo game"):
            demo.seed_demo(session)
