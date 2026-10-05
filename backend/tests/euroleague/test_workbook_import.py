"""The workbook importer, end to end, on a synthetic workbook of invented clubs and players.

The workbook is built in memory by the ``mini_league`` fixture in the shape of the user's:
every sheet the importer reads, plus the betting columns, whose cells hold sentinel values. The
tests assert what the design promises:

* nothing in a betting cell reaches the store, as text or as a number, anywhere in the file;
* the read map and the ignored lists never meet, and the report *names* what it ignored;
* a zero-attempt percentage is NULL, a DNP has NULL stats, a column the sheet does not carry is
  NULL, minutes become exact seconds;
* the box-score invariants run, and a hard failure quarantines its game (keeping earlier rows);
* the tip-off is the CEST component converted through Europe/Berlin, neutrality needs both
  signs, a status outside the five is rejected, a betting operator's link is withheld;
* an unknown club code aborts and writes nothing; an unreadable sheet is reported, not guessed;
* re-importing the same file is a no-op, a newer file adds its rounds and re-keys a provisional
  fixture to the official game, an official row is never overwritten, a hand-set setting is
  never overwritten, a dry run writes nothing, and the invented store refuses the workbook.
"""

from __future__ import annotations

import io
import json
import re
import struct
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from nbastats.euroleague import PACKAGE_VERSION
from nbastats.euroleague.db import create_el_engine, ensure_identity, init_el_db
from nbastats.euroleague.importers import workbook as wb_module
from nbastats.euroleague.importers.workbook import (
    IGNORED_HEADERS,
    IGNORED_SETTINGS,
    READ_MAP,
    SETTING_MAP,
    GameFacts,
    Line,
    check_game,
    classify_reason,
    import_workbook,
    last_import_failed,
    parse_tipoff,
    workbook_imported,
)
from nbastats.euroleague.models import (
    ElClub,
    ElGame,
    ElPerson,
    ElPersonAlias,
    ElProjectionLedger,
    ElSeason,
)
from nbastats.euroleague.profile import load_club_codes
from nbastats.euroleague.settings import set_setting

SENTINEL_TEXT = ("SENTINEL-OVER", "SENTINEL-LEAN", "SENTINEL-YOURLINE", "SENTINEL-WIN")
SENTINEL_NUMBERS = (
    8888.5,
    8887.25,
    0.987654321,
    0.123456789,
    9191.5,
    7777.7,
    6666.6,
    5555.5,
    4444.4,
)


# --------------------------------------------------------------------------- helpers


def rows(engine: Engine, sql: str, **params: Any) -> list[tuple[Any, ...]]:
    with engine.connect() as connection:
        return [tuple(r) for r in connection.execute(text(sql), params).all()]


def scalar(engine: Engine, sql: str, **params: Any) -> Any:
    return rows(engine, sql, **params)[0][0]


def run(engine: Engine, league: Any, data: bytes | None = None, **kwargs: Any) -> Any:
    payload = data if data is not None else league.workbook()
    return import_workbook(payload, engine, crosswalk=league.crosswalk, **kwargs)


def table_names(engine: Engine) -> list[str]:
    return [r[0] for r in rows(engine, "SELECT name FROM sqlite_master WHERE type='table'")]


def all_cells(engine: Engine) -> list[tuple[str, Any]]:
    cells: list[tuple[str, Any]] = []
    for name in table_names(engine):
        for row in rows(engine, f"SELECT * FROM {name}"):
            cells.extend((name, value) for value in row)
    return cells


def assert_no_sentinels(engine: Engine) -> None:
    """No sentinel anywhere: not in a cell as text or number, and not in the file's bytes."""
    path = Path(str(engine.url.database))
    for name, value in all_cells(engine):
        if isinstance(value, str):
            assert not any(s in value for s in SENTINEL_TEXT), f"{name}: {value!r}"
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            assert not any(abs(value - s) < 1e-9 for s in SENTINEL_NUMBERS), f"{name}: {value!r}"
    engine.dispose()  # checkpoints the write-ahead log into the file
    blob = b"".join(p.read_bytes() for p in path.parent.glob(path.name + "*") if p.is_file())
    for needle in SENTINEL_TEXT:
        assert needle.encode() not in blob
    for number in SENTINEL_NUMBERS:
        assert struct.pack(">d", number) not in blob
        assert struct.pack("<d", number) not in blob


_VOLATILE = {
    "data_source",
    "ingested_at",
    "computed_at",
    "recorded_at",
    "set_at",
    "created_at",
    "imported_at",
    "label",
    "file_sha256",
    "last_success_at",
    "source_sha256",
    "detail_json",
    "id",
    "batch_id",
    "started_at",
    "finished_at",
}


def stable_dump(engine: Engine) -> dict[str, list[str]]:
    dump: dict[str, list[str]] = {}
    for name in sorted(table_names(engine)):
        with engine.connect() as connection:
            result = connection.execute(text(f"SELECT * FROM {name}"))
            keys = list(result.keys())
            body = [{k: v for k, v in zip(keys, row) if k not in _VOLATILE} for row in result.all()]
        dump[name] = sorted(json.dumps(r, sort_keys=True, default=str) for r in body)
    return dump


def second_engine(tmp_path: Path) -> Engine:
    engine = create_el_engine(f"sqlite:///{tmp_path / 'second.db'}")
    init_el_db(engine)
    return engine


def player_names(club: Any) -> list[str]:
    return [p.name for p in club.players]


def patch_zip(data: bytes, **replacements: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for item in source.infolist():
            target.writestr(item.filename, replacements.get(item.filename, source.read(item)))
    return out.getvalue()


# --------------------------------------------------------------------------- the happy path


def test_the_workbook_is_imported(el_engine: Engine, mini_league: Any) -> None:
    report = run(el_engine, mini_league)
    assert report.status == "ok", report.error
    assert (report.season, report.as_of_round) == ("E2026", 2)
    assert scalar(el_engine, "SELECT kind FROM el_store_identity") == "workbook"
    assert rows(el_engine, "SELECT season_code, label, is_current FROM el_season") == [
        ("E2026", "2026-27", 1)
    ]
    assert scalar(el_engine, "SELECT COUNT(*) FROM el_club") == 4
    assert scalar(el_engine, "SELECT COUNT(*) FROM el_person") == sum(
        len(c.players) for c in mini_league.clubs
    )
    assert rows(
        el_engine, "SELECT status, stats_status, COUNT(*) FROM el_game GROUP BY 1, 2 ORDER BY 1"
    ) == [
        ("final", "ok", 4),
        ("scheduled", "none", 2),
    ]
    assert report.counts["gamesFinal"] == 4 and report.counts["gamesScheduled"] == 2
    assert [o.status for o in report.sheets if o.role != "notImported"] == ["imported"] * 8
    assert {o.sheet for o in report.sheets if o.status == "notImported"} == {
        "Start Here",
        "R3 Scorers",
        "Game Logs",
        "Latest Games",
    }
    assert scalar(el_engine, "PRAGMA foreign_key_check") is None if False else True
    assert rows(el_engine, "PRAGMA foreign_key_check") == []


def test_the_report_is_json_with_camel_case_keys(el_engine: Engine, mini_league: Any) -> None:
    report = run(el_engine, mini_league)
    document = json.loads(json.dumps(report.to_dict()))
    assert {
        "fileName",
        "sha256",
        "asOfRound",
        "ignoredHeaders",
        "quarantinedGames",
        "softViolations",
        "importerVersion",
    } <= set(document)
    assert document["importerVersion"] == PACKAGE_VERSION
    assert all(re.fullmatch(r"[a-z][A-Za-z0-9]*", key) for key in document)
    text_form = report.render()
    assert "never read" in text_form and "SENTINEL" not in text_form
    assert "SENTINEL" not in json.dumps(document)


def test_sync_state_and_log_after_an_import(el_engine: Engine, mini_league: Any) -> None:
    report = run(el_engine, mini_league)
    assert rows(el_engine, "SELECT sync_version, data_through, mode FROM el_sync_state") == [
        (1, "2026-09-30", "workbook")
    ]
    log = rows(
        el_engine,
        "SELECT job, status, games_written, invariant_failed, source_sha256 FROM el_ingest_log",
    )
    assert log == [("workbook", "ok", 4, None, report.sha256)]
    assert workbook_imported(Session(el_engine), report.sha256)


# --------------------------------------------------------------------------- no betting machinery


def test_no_betting_cell_reaches_the_store(el_engine: Engine, mini_league: Any) -> None:
    report = run(el_engine, mini_league)
    assert report.status == "ok"
    assert_no_sentinels(el_engine)


def test_the_read_map_never_meets_the_ignored_lists() -> None:
    read = {header for headers in READ_MAP.values() for header in headers}
    assert not read & IGNORED_HEADERS
    assert not read & IGNORED_SETTINGS
    assert IGNORED_HEADERS >= {
        "Model line",
        "Your line",
        "P(over)",
        "Lean",
        "Result v line",
        "Home win %",
    }
    assert IGNORED_SETTINGS == {"TotalSD", "PSDBase", "PSDSlope", "EdgeP"}
    assert not set(SETTING_MAP) & IGNORED_SETTINGS
    assert not {key for key, _ in SETTING_MAP.values()} & {"totalSd", "psdBase", "edgeP"}


def test_the_report_names_what_it_ignored_but_never_their_values(
    el_engine: Engine, mini_league: Any
) -> None:
    report = run(el_engine, mini_league)
    assert set(report.ignored_headers) == {
        "Model line",
        "Your line",
        "P(over)",
        "Lean",
        "Result v line",
        "Home win %",
    }
    assert set(report.ignored_settings) == IGNORED_SETTINGS
    assert set(report.recomputed_settings) == {"LgPts", "TeamMinutes"}
    assert report.unknown_settings == ["Mystery"]
    blob = json.dumps(report.to_dict())
    assert not any(s in blob for s in SENTINEL_TEXT)
    assert not any(str(n) in blob for n in SENTINEL_NUMBERS)


def test_the_betting_cells_have_no_influence_on_any_stored_value(
    el_engine: Engine, mini_league: Any, tmp_path: Path
) -> None:
    """The same workbook with ordinary values in the betting cells stores the same rows."""
    run(el_engine, mini_league, mini_league.workbook(with_sentinels=True))
    other = second_engine(tmp_path)
    run(other, mini_league, mini_league.workbook(with_sentinels=False))
    assert stable_dump(el_engine) == stable_dump(other)


# --------------------------------------------------------------------------- settings


def test_settings_map_by_defined_name(el_engine: Engine, mini_league: Any) -> None:
    run(el_engine, mini_league)
    stored = {
        k: (v, p)
        for k, v, p in rows(el_engine, "SELECT key, value, provenance FROM el_model_setting")
    }
    assert stored["priorRegression"] == (0.3, "workbook")
    assert stored["homeAdvantagePoints"] == (3.5, "workbook")
    assert stored["teamSd"] == (9.5, "workbookUnvalidated")
    assert stored["marginSd"] == (11.5, "workbookUnvalidated")
    assert stored["replacementPer40"] == (9.0, "workbook")
    assert stored["absorbShare"] == (0.6, "workbook")
    assert stored["boostCap"] == (1.35, "workbook")
    assert stored["rotationShare"] == (0.5, "workbook")
    assert stored["formWeight"] == (0.25, "workbook")
    assert stored["roundWeight.2"] == (0.09, "workbook")
    assert {k: v for k, (v, _) in stored.items() if k.startswith("statusChance.")} == {
        "statusChance.out": 0.0,
        "statusChance.doubtful": 0.25,
        "statusChance.questionable": 0.5,
        "statusChance.probable": 0.85,
        "statusChance.available": 1.0,
    }
    assert len(stored) == 15
    forbidden = {"totalSd", "psdBase", "psdSlope", "edgeP", "TotalSD", "EdgeP", "Mystery", "LgPts"}
    assert not forbidden & set(stored)


def test_a_hand_set_setting_survives_an_import(el_engine: Engine, mini_league: Any) -> None:
    with Session(el_engine) as session:
        ensure_identity(session, "workbook")
        set_setting(session, "priorRegression", 0.55, "manual")
        set_setting(session, "teamSd", 8.0, "fittedLedger")
        session.commit()
    run(el_engine, mini_league)
    stored = {
        k: (v, p)
        for k, v, p in rows(el_engine, "SELECT key, value, provenance FROM el_model_setting")
    }
    assert stored["priorRegression"] == (0.55, "manual")
    assert stored["teamSd"] == (8.0, "fittedLedger")
    assert stored["marginSd"] == (11.5, "workbookUnvalidated")  # the rest still arrive


# --------------------------------------------------------------------------- clubs and identity


def test_clubs_are_keyed_by_official_code_with_a_workbook_alias(
    el_engine: Engine, mini_league: Any
) -> None:
    run(el_engine, mini_league)
    assert sorted(r[0] for r in rows(el_engine, "SELECT club_code FROM el_club")) == [
        "ZZA",
        "ZZB",
        "ZZC",
        "ZZD",
    ]
    aliases = {
        (s, c): club
        for s, c, club in rows(el_engine, "SELECT system, code, club_code FROM el_club_alias")
    }
    # workbook ZZB is official ZZA, and official ZZB is a different club: lookups go by system
    assert aliases[("workbook", "ZZB")] == "ZZA"
    assert aliases[("official", "ZZB")] == "ZZB"
    assert aliases[("workbook", "ZZA")] == "ZZC"
    assert aliases[("official", "ZZA")] == "ZZA"
    assert len(aliases) == 8
    first = mini_league.clubs[0]
    assert rows(el_engine, "SELECT name, short_name FROM el_club WHERE club_code = 'ZZA'") == [
        (first.spec.name, first.spec.short_name)
    ]
    assert rows(
        el_engine, "SELECT coach_name, home_venue_name FROM el_club_season WHERE club_code = 'ZZA'"
    )[0][0] == (first.spec.coach)


def test_an_unknown_club_code_aborts_and_writes_nothing(
    el_engine: Engine, mini_league: Any
) -> None:
    report = run(el_engine, mini_league, mini_league.workbook(unknown_club=True))
    assert report.status == "aborted"
    assert "QQQ" in (report.error or "") and "nothing was imported" in (report.error or "")
    for table in (
        "el_club",
        "el_person",
        "el_game",
        "el_team_rating",
        "el_model_setting",
        "el_intel_status",
        "el_store_identity",
        "el_season",
    ):
        assert scalar(el_engine, f"SELECT COUNT(*) FROM {table}") == 0, table
    assert rows(el_engine, "SELECT job, status FROM el_ingest_log") == [("workbook", "failed")]


def test_the_committed_crosswalk_is_what_an_import_uses_by_default(
    el_engine: Engine, xl: Any
) -> None:
    """Real club *codes* (facts) with invented players: the default crosswalk maps them."""
    ratings = {
        5: dict(
            enumerate(
                ["Code", "Club", "PF/g 2025-26", "PA/g 2025-26", "Attack adj", "Defence adj"],
                start=1,
            )
        ),
        6: {1: "PRT", 2: "x", 3: 85.0, 4: 84.0, 5: 0.5, 6: -0.5},
        7: {1: "PAR", 2: "y", 3: 86.0, 4: 83.0, 5: 0.25, 6: 0.75},
    }
    data = xl.make([("Team Ratings", ratings), ("Start Here", {2: {2: "Toolkit 2026-27"}})])
    report = import_workbook(data, el_engine)
    assert report.status == "ok", report.error
    crosswalk = load_club_codes()
    partizan, paris = crosswalk.by_workbook("PRT"), crosswalk.by_workbook("PAR")
    assert partizan is not None and paris is not None
    assert sorted(r[0] for r in rows(el_engine, "SELECT club_code FROM el_club")) == sorted(
        [partizan.official_code, paris.official_code]
    )
    aliases = {
        (s, c): club
        for s, c, club in rows(el_engine, "SELECT system, code, club_code FROM el_club_alias")
    }
    assert aliases[("workbook", "PAR")] == paris.official_code
    assert aliases[("workbook", "PRT")] == partizan.official_code == aliases[("official", "PAR")]


# --------------------------------------------------------------------------- squads and rates


def test_squads_become_registrations_and_rates(el_engine: Engine, mini_league: Any) -> None:
    run(el_engine, mini_league)
    club = mini_league.clubs[0]
    star = club.players[0]
    person = rows(
        el_engine, "SELECT person_code, code_system FROM el_person WHERE name = :n", n=star.name
    )
    assert len(person) == 1 and person[0][0].startswith("wb-") and person[0][1] == "workbook"
    registration = rows(
        el_engine,
        "SELECT club_code, position_code, position5_workbook, role_workbook, age_workbook, "
        "active, source "
        "FROM el_registration WHERE person_code = :p",
        p=person[0][0],
    )
    # the workbook knows only its own five-way label; the official position stays unrecorded
    assert registration == [("ZZA", None, star.pos5, star.role, star.age, 1, "workbook")]
    rate = rows(
        el_engine,
        "SELECT as_of_round, proj_minutes, pts40, basis, prior_minutes FROM el_player_rate "
        "WHERE person_code = :p",
        p=person[0][0],
    )
    assert rate == [(2, star.proj_minutes, star.rates["pts40"], "workbookOfficial", 400.0)]


def test_a_zero_attempt_percentage_is_null_never_zero(el_engine: Engine, mini_league: Any) -> None:
    run(el_engine, mini_league)
    zero_attempt_threes = scalar(el_engine, "SELECT COUNT(*) FROM el_player_rate WHERE fga3_40 = 0")
    assert zero_attempt_threes > 0  # the invented centres never shoot threes
    assert scalar(
        el_engine, "SELECT COUNT(*) FROM el_player_rate WHERE fga3_40 = 0 AND fg3_pct IS NULL"
    ) == (zero_attempt_threes)
    assert scalar(el_engine, "SELECT COUNT(*) FROM el_player_rate WHERE fg3_pct = 0") == 0
    # a percentage with attempts behind it is kept
    assert (
        scalar(
            el_engine, "SELECT COUNT(*) FROM el_player_rate WHERE fga3_40 > 0 AND fg3_pct IS NULL"
        )
        == 0
    )


def test_estimate_lines_are_imported_as_estimates_by_default(
    el_engine: Engine, mini_league: Any
) -> None:
    run(el_engine, mini_league)
    estimates = sum(1 for c in mini_league.clubs for p in c.players if not p.established)
    assert estimates > 0
    assert rows(
        el_engine,
        "SELECT basis, prior_minutes, COUNT(*) FROM el_player_rate GROUP BY 1, 2 ORDER BY 1",
    ) == [
        ("workbookEstimate", 150.0, estimates),
        ("workbookOfficial", 400.0, sum(len(c.players) for c in mini_league.clubs) - estimates),
    ]


def test_without_estimates_only_minutes_and_identity_are_kept(
    el_engine: Engine, mini_league: Any
) -> None:
    run(el_engine, mini_league, include_estimates=False)
    estimates = [p for c in mini_league.clubs for p in c.players if not p.established]
    assert (
        scalar(el_engine, "SELECT COUNT(*) FROM el_player_rate WHERE basis = 'workbookEstimate'")
        == 0
    )
    prior = rows(
        el_engine,
        "SELECT person_code, proj_minutes, pts40, fg3_pct, prior_minutes FROM el_player_rate "
        "WHERE basis = 'positionPrior'",
    )
    assert len(prior) == len(estimates)
    assert all(row[2] is None and row[3] is None and row[4] == 150.0 for row in prior)
    assert sorted(r[1] for r in prior) == sorted(p.proj_minutes for p in estimates)
    # the player is still on the squad
    assert scalar(el_engine, "SELECT COUNT(*) FROM el_registration") == sum(
        len(c.players) for c in mini_league.clubs
    )


def test_person_codes_are_deterministic_across_stores(
    el_engine: Engine, mini_league: Any, tmp_path: Path
) -> None:
    run(el_engine, mini_league)
    other = second_engine(tmp_path)
    run(other, mini_league)
    assert rows(el_engine, "SELECT person_code, name FROM el_person ORDER BY 1") == rows(
        other, "SELECT person_code, name FROM el_person ORDER BY 1"
    )


def test_an_alias_to_an_official_person_is_respected(el_engine: Engine, mini_league: Any) -> None:
    """After reconciliation a minted person maps to an official code; a re-import follows it."""
    from nbastats.euroleague.profile import fold_name, workbook_person_code

    star = mini_league.clubs[0].players[0]
    minted = workbook_person_code("ZZA", fold_name(star.name))
    with Session(el_engine) as session:
        ensure_identity(session, "workbook")
        session.add(ElPerson(person_code="900001", code_system="official", name=star.name))
        session.add(
            ElPersonAlias(
                wb_code=minted,
                official_code="900001",
                matched_by="clubAndName",
                matched_at=datetime(2026, 10, 1),
            )
        )
        session.commit()
    run(el_engine, mini_league)
    assert scalar(el_engine, "SELECT COUNT(*) FROM el_person WHERE person_code = :c", c=minted) == 0
    assert (
        scalar(el_engine, "SELECT COUNT(*) FROM el_registration WHERE person_code = '900001'") == 1
    )
    assert (
        scalar(el_engine, "SELECT COUNT(*) FROM el_player_rate WHERE person_code = '900001'") == 1
    )
    assert (
        scalar(el_engine, "SELECT COUNT(*) FROM el_player_game WHERE person_code = '900001'") >= 1
    )


# --------------------------------------------------------------------------- ratings


def test_ratings_and_the_estimate_flag(el_engine: Engine, mini_league: Any) -> None:
    run(el_engine, mini_league)
    ratings = rows(
        el_engine,
        "SELECT club_code, as_of_round, pf_prior, pa_prior, prior_is_estimate, attack_adj, "
        "defence_adj, source, "
        "update_weight, update_basis FROM el_team_rating ORDER BY club_code",
    )
    assert len(ratings) == 4
    assert all(
        r[1] == 2 and r[7] == "workbookImport" and r[8] is None and r[9] is None for r in ratings
    )
    by_club = {r[0]: r for r in ratings}
    # the sheet gives wins and losses to the first three clubs, and none to the last
    assert [by_club[c][4] for c in ("ZZA", "ZZB", "ZZC", "ZZD")] == [0, 0, 0, 1]
    # workbook ZZB (official ZZA) is the first row of the sheet
    assert (by_club["ZZA"][2], by_club["ZZA"][3], by_club["ZZA"][5], by_club["ZZA"][6]) == (
        84.5,
        83.0,
        1.25,
        -0.75,
    )


# --------------------------------------------------------------------------- box scores


def _db_lines(engine: Engine, game_id: str) -> dict[str, tuple[Any, ...]]:
    return {
        name: tuple(row)
        for name, *row in rows(
            engine,
            "SELECT p.name, g.participation, g.seconds_played, g.pts, g.fgm2, g.fga2, g.fgm3, "
            "g.fga3, g.ftm, "
            "g.fta, g.oreb, g.dreb, g.reb, g.ast, g.stl, g.tov, g.blk, g.pf, g.pir_official "
            "FROM el_player_game g JOIN el_person p ON p.person_code = g.person_code WHERE "
            "g.game_id = :g",
            g=game_id,
        )
    }


def test_box_score_lines_are_exact(el_engine: Engine, mini_league: Any) -> None:
    run(el_engine, mini_league)
    for code, game in mini_league.games.items():
        if game.round_number > 2:
            continue
        gid = f"E2026-{code:04d}"
        stored = _db_lines(el_engine, gid)
        expected = [*game.home_lines, *game.away_lines]
        assert set(stored) == {p.name for p, _ in expected}
        for player, line in expected:
            row = stored[player.name]
            if line.participation == "dnp":
                assert row[0] == "dnp" and all(v is None for v in row[1:])
            else:
                # decimal minutes with two places round-trip to the exact second
                assert row == (
                    "played",
                    line.seconds,
                    line.pts,
                    line.fgm2,
                    line.fga2,
                    line.fgm3,
                    line.fga3,
                    line.ftm,
                    line.fta,
                    line.oreb,
                    line.dreb,
                    line.reb,
                    line.ast,
                    line.stl,
                    line.tov,
                    line.blk,
                    line.pf,
                    line.pir_official,
                )


def test_columns_the_sheet_does_not_carry_are_null_not_zero(
    el_engine: Engine, mini_league: Any
) -> None:
    run(el_engine, mini_league)
    total = scalar(el_engine, "SELECT COUNT(*) FROM el_player_game")
    assert total > 0
    for column in (
        "fouls_drawn",
        "blk_against",
        "plus_minus",
        "is_starter",
        "position_code_at_game",
        "dorsal",
    ):
        assert (
            scalar(el_engine, f"SELECT COUNT(*) FROM el_player_game WHERE {column} IS NULL")
            == total
        ), column
    team_total = scalar(el_engine, "SELECT COUNT(*) FROM el_team_game")
    for column in ("fouls_drawn", "blk_against", "plus_minus"):
        assert (
            scalar(el_engine, f"SELECT COUNT(*) FROM el_team_game WHERE {column} IS NULL")
            == team_total
        )


def test_a_dnp_line_has_every_stat_null(el_engine: Engine, mini_league: Any) -> None:
    run(el_engine, mini_league)
    dnp = scalar(el_engine, "SELECT COUNT(*) FROM el_player_game WHERE participation = 'dnp'")
    assert dnp > 0  # the sheet carries zeros for them; none of those zeros was stored
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
        "pf",
        "pir_official",
    )
    assert (
        scalar(
            el_engine,
            "SELECT COUNT(*) FROM el_player_game WHERE participation = 'dnp' AND ("
            + " OR ".join(f"{c} IS NOT NULL" for c in stats)
            + ")",
        )
        == 0
    )


def test_team_lines_scores_and_overtime(el_engine: Engine, mini_league: Any) -> None:
    run(el_engine, mini_league)
    ot_game = mini_league.games[3]  # the invented league's overtime game
    assert ot_game.ot == 1
    game = rows(
        el_engine,
        "SELECT home_pts, away_pts, ot_periods, home_partials_json, away_partials_json, "
        "home_club_code, away_club_code, game_date FROM el_game WHERE game_id = 'E2026-0003'",
    )[0]
    assert (game[0], game[1], game[2]) == (ot_game.home_pts, ot_game.away_pts, 1)
    assert len(json.loads(game[3])) == 5 and sum(json.loads(game[3])) == game[0]
    assert sum(json.loads(game[4])) == game[1]
    assert (game[5], game[6], game[7]) == (ot_game.home, ot_game.away, "2026-09-29")
    teams = rows(
        el_engine,
        "SELECT club_code, is_home, opp_club_code, won, seconds_played, pts, opp_pts "
        "FROM el_team_game WHERE game_id = 'E2026-0003' ORDER BY is_home DESC",
    )
    assert teams[0][:3] == (ot_game.home, 1, ot_game.away) and teams[0][4] == 13_500
    assert (teams[0][5], teams[0][6]) == (ot_game.home_pts, ot_game.away_pts)
    assert teams[1][1] == 0 and (teams[1][5], teams[1][6]) == (ot_game.away_pts, ot_game.home_pts)
    assert teams[0][3] == (1 if ot_game.home_pts > ot_game.away_pts else 0)
    regulation = rows(
        el_engine, "SELECT seconds_played FROM el_team_game WHERE game_id = 'E2026-0001'"
    )
    assert all(r[0] == 12_000 for r in regulation)


def test_team_time_is_stored_exactly_once_the_invariant_accepts_the_game(
    el_engine: Engine, mini_league: Any
) -> None:
    """The sheet types team minutes to two decimals (199.98 is 11999 s). A game the team-time
    invariant accepted lasted exactly 200 minutes (plus 25 per overtime): that is what is stored,
    so points per regulation equals points per game for a club that never went to overtime."""
    play = mini_league._play

    def rounded_minutes(*args: Any) -> Any:
        game = play(*args)
        if game.code == 1:
            game.home_team.seconds, game.away_team.seconds = 11_999, 12_001
        return game

    mini_league._play = rounded_minutes
    assert run(el_engine, mini_league).status == "ok"
    stored = rows(
        el_engine, "SELECT seconds_played FROM el_team_game WHERE game_id = 'E2026-0001'"
    )
    assert sorted(r[0] for r in stored) == [12_000, 12_000]


def test_the_database_agrees_with_itself(el_engine: Engine, mini_league: Any) -> None:
    run(el_engine, mini_league)
    assert (
        scalar(
            el_engine,
            "SELECT COUNT(*) FROM el_team_game t WHERE t.pts != "
            "(SELECT SUM(p.pts) FROM el_player_game p WHERE p.game_id = t.game_id AND p.club_code "
            "= t.club_code)",
        )
        == 0
    )
    assert (
        scalar(
            el_engine,
            "SELECT COUNT(*) FROM el_game g JOIN el_team_game t ON t.game_id = g.game_id AND "
            "t.club_code = g.home_club_code "
            "WHERE g.home_pts != t.pts",
        )
        == 0
    )
    assert (
        scalar(
            el_engine,
            "SELECT COUNT(*) FROM el_player_game WHERE participation = 'played' AND "
            "pts != 2 * fgm2 + 3 * fgm3 + ftm",
        )
        == 0
    )


def test_the_footnote_and_the_leaders_table_are_not_read_as_games(
    el_engine: Engine, mini_league: Any
) -> None:
    report = run(el_engine, mini_league)
    assert report.warnings == []
    assert scalar(el_engine, "SELECT COUNT(*) FROM el_game WHERE status = 'final'") == 4
    assert scalar(el_engine, "SELECT COUNT(*) FROM el_person WHERE name = 'Nobody Real'") == 0


# --------------------------------------------------------------------------- invariants


def test_a_hard_failure_quarantines_its_game_and_only_its_game(
    el_engine: Engine, mini_league: Any
) -> None:
    report = run(el_engine, mini_league, mini_league.workbook(break_game=1))
    assert report.status == "ok"
    assert report.quarantined and report.quarantined[0]["gameId"] == "E2026-0001"
    assert (
        "team_points" in report.quarantined[0]["rules"]
        or "pts_formula" in report.quarantined[0]["rules"]
    )
    assert rows(
        el_engine, "SELECT stats_status, status, home_pts FROM el_game WHERE game_id = 'E2026-0001'"
    ) == [("quarantined", "final", mini_league.games[1].home_pts)]
    assert (
        scalar(el_engine, "SELECT COUNT(*) FROM el_player_game WHERE game_id = 'E2026-0001'") == 0
    )
    assert scalar(el_engine, "SELECT COUNT(*) FROM el_team_game WHERE game_id = 'E2026-0001'") == 0
    assert scalar(el_engine, "SELECT COUNT(*) FROM el_game WHERE stats_status = 'ok'") == 3
    assert scalar(el_engine, "SELECT COUNT(*) FROM el_player_game WHERE game_id = 'E2026-0002'") > 0
    assert "E2026-0001" in scalar(el_engine, "SELECT invariant_failed FROM el_ingest_log")


def test_a_failing_newer_file_keeps_the_previous_good_rows(
    el_engine: Engine, mini_league: Any
) -> None:
    run(el_engine, mini_league)
    before = _db_lines(el_engine, "E2026-0001")
    report = run(
        el_engine,
        mini_league,
        mini_league.workbook(
            break_game=1,
            extra_status_rows=[
                [
                    mini_league.wb("ZZA"),
                    "Someone New",
                    "Ankle",
                    "OUT",
                    "OUT",
                    "Round 5",
                    "https://news.example.org/z",
                    date(2026, 10, 1),
                ]
            ],
        ),
    )
    assert report.status == "ok" and report.quarantined
    assert rows(el_engine, "SELECT stats_status FROM el_game WHERE game_id = 'E2026-0001'") == [
        ("quarantined",)
    ]
    assert _db_lines(el_engine, "E2026-0001") == before  # the earlier good lines are untouched


def test_a_contradictory_result_quarantines_a_new_game_and_is_not_trusted(
    el_engine: Engine, mini_league: Any
) -> None:
    report = run(el_engine, mini_league, mini_league.workbook(wrong_result_game=2))
    assert [q["gameId"] for q in report.quarantined] == ["E2026-0002"]
    assert "final_score" in report.quarantined[0]["rules"]
    assert scalar(el_engine, "SELECT stats_status FROM el_game WHERE game_id = 'E2026-0002'") == (
        "quarantined"
    )
    assert (
        scalar(el_engine, "SELECT COUNT(*) FROM el_player_game WHERE game_id = 'E2026-0002'") == 0
    )


def test_a_contradictory_file_cannot_rewrite_a_game_an_earlier_file_got_right(
    el_engine: Engine, mini_league: Any
) -> None:
    run(el_engine, mini_league)
    game = mini_league.games[2]
    before = rows(
        el_engine,
        "SELECT home_pts, away_pts, raw_sha256, data_source, ingested_at, stats_status "
        "FROM el_game "
        "WHERE game_id = 'E2026-0002'",
    )
    lines = _db_lines(el_engine, "E2026-0002")
    assert before[0][0] == game.home_pts and before[0][5] == "ok"
    report = run(
        el_engine,
        mini_league,
        mini_league.workbook(
            wrong_result_game=2,
            extra_status_rows=[
                [
                    mini_league.wb("ZZA"),
                    "Someone New",
                    "Ankle",
                    "OUT",
                    "OUT",
                    "Round 5",
                    "https://news.example.org/y",
                    date(2026, 10, 1),
                ]
            ],
        ),
    )
    assert report.status == "ok" and [q["gameId"] for q in report.quarantined] == ["E2026-0002"]
    after = rows(
        el_engine,
        "SELECT home_pts, away_pts, raw_sha256, data_source, ingested_at, stats_status "
        "FROM el_game "
        "WHERE game_id = 'E2026-0002'",
    )
    assert (
        after[0][:5] == before[0][:5]
    )  # the score is the earlier file's, not the contradictory one's
    assert after[0][5] == "quarantined"  # but the game is flagged until a consistent file arrives
    assert _db_lines(el_engine, "E2026-0002") == lines
    # a consistent file clears the flag
    again = run(
        el_engine, mini_league, mini_league.workbook(title="Synthetic League 2026-27 Toolkit ")
    )
    assert again.status == "ok" and not again.quarantined
    assert (
        scalar(el_engine, "SELECT stats_status FROM el_game WHERE game_id = 'E2026-0002'") == "ok"
    )
    assert _db_lines(el_engine, "E2026-0002") == lines


def test_partials_that_do_not_sum_to_the_final_quarantine_the_game(
    el_engine: Engine, mini_league: Any
) -> None:
    report = run(el_engine, mini_league, mini_league.workbook(bad_partials_game=3))
    assert [q["gameId"] for q in report.quarantined] == ["E2026-0003"]
    assert "partials" in report.quarantined[0]["rules"]
    assert rows(
        el_engine,
        "SELECT stats_status, home_partials_json FROM el_game WHERE game_id = 'E2026-0003'",
    ) == [
        ("quarantined", None)
    ]  # a partial that contradicts the score is not stored


def _line(side: str, **values: Any) -> Line:
    return Line(side=side, name=values.pop("name", "p"), **values)


def _facts(**overrides: Any) -> GameFacts:
    home = [
        _line(
            "home",
            seconds=12_000,
            pts=10,
            fgm2=2,
            fga2=4,
            fgm3=1,
            fga3=3,
            ftm=3,
            fta=4,
            oreb=1,
            dreb=2,
            reb=3,
            tov=1,
        )
    ]
    away = [
        _line(
            "away",
            seconds=12_000,
            pts=7,
            fgm2=2,
            fga2=3,
            fgm3=0,
            fga3=1,
            ftm=3,
            fta=3,
            oreb=0,
            dreb=1,
            reb=1,
            tov=2,
        )
    ]
    base = dict(
        final_home=10,
        final_away=7,
        players=[*home, *away],
        teams={
            "home": _line("home", seconds=12_000, pts=10, reb=4, tov=2),
            "away": _line("away", seconds=12_000, pts=7, reb=2, tov=3),
        },
    )
    base.update(overrides)
    return GameFacts(**base)


def _rules(result: Any, kind: str) -> set[str]:
    return {v.rule for v in result.violations if v.kind == kind}


def test_check_game_passes_a_consistent_game() -> None:
    result = check_game(_facts())
    assert result.violations == () and result.ot_periods == 0


def test_check_game_hard_rules() -> None:
    bad_points = _facts()
    bad_points.players[0].pts = 11
    assert "pts_formula" in _rules(check_game(bad_points), "hard")
    assert "team_points" in _rules(check_game(bad_points), "hard")

    bad_rebounds = _facts()
    bad_rebounds.players[0].reb = 4
    assert "reb_sum" in _rules(check_game(bad_rebounds), "hard")

    over = _facts()
    over.players[0].fga2 = 1
    assert "makes_le_attempts" in _rules(check_game(over), "hard")

    assert "final_score" in _rules(check_game(_facts(final_home=11)), "hard")
    assert "partials" in _rules(
        check_game(_facts(home_partials=[3, 3, 3, 2], away_partials=[2, 2, 2, 1])), "hard"
    )
    assert "partials" in _rules(
        check_game(_facts(home_partials=[5, 5], away_partials=[3, 4])), "hard"
    )


def test_check_game_team_time_and_overtime() -> None:
    assert check_game(_facts()).violations == ()
    short = _facts()
    short.teams["home"].seconds = 11_990
    assert "team_time" in _rules(check_game(short), "hard")
    players_short = _facts()
    players_short.players[0].seconds = 11_000
    assert "team_time" in _rules(check_game(players_short), "hard")

    overtime = _facts()
    for line in (*overtime.players, *overtime.teams.values()):
        line.seconds = 13_500
    result = check_game(overtime)
    assert result.violations == () and result.ot_periods == 1
    five_periods = _facts(home_partials=[3, 2, 2, 1, 2], away_partials=[2, 1, 1, 1, 2])
    for line in (*five_periods.players, *five_periods.teams.values()):
        line.seconds = 13_500
    assert check_game(five_periods).ot_periods == 1


def test_check_game_time_tolerance_boundaries() -> None:
    inside = _facts(seconds_tolerance=3)
    inside.teams["home"].seconds = 12_003
    assert check_game(inside).violations == ()
    outside = _facts(seconds_tolerance=3)
    outside.teams["home"].seconds = 12_004
    assert "team_time" in _rules(check_game(outside), "hard")
    live = _facts(seconds_tolerance=6)
    live.teams["home"].seconds = 12_006
    assert check_game(live).violations == ()


def test_check_game_skips_what_was_not_recorded() -> None:
    """Absence is not a violation: a missing value skips the rule that needs it."""
    facts = _facts()
    for line in facts.players:
        line.reb = line.oreb = line.dreb = line.fgm2 = line.fga2 = None
    facts.teams = {}
    facts.final_away = None
    assert check_game(facts).violations == ()
    assert check_game(GameFacts(None, None, [], {})).violations == ()


def test_a_dnp_line_is_never_checked() -> None:
    facts = _facts()
    facts.players.append(Line(side="home", name="bench", participation="dnp"))
    assert check_game(facts).violations == ()


def test_check_game_soft_rules() -> None:
    reb = _facts()
    reb.teams["home"].reb = 2
    result = check_game(reb)
    assert _rules(result, "soft") == {"reb_vs_team"} and not result.hard
    tov = _facts()
    tov.teams["away"].tov = 1
    assert _rules(check_game(tov), "soft") == {"tov_vs_team"}

    starters = _facts()
    starters.players[0].is_starter = True
    assert _rules(check_game(starters), "soft") == {"starters"}
    five = _facts()
    five.players = [
        *five.players,
        *[Line(side="home", name=f"s{i}", participation="dnp", is_starter=True) for i in range(4)],
    ]
    five.players[0].is_starter = (
        True  # the home side now has five; the away side's flags are unknown
    )
    assert "starters" not in _rules(check_game(five), "soft")
    five.players[1].is_starter = False  # the away side now says it has none
    assert _rules(check_game(five), "soft") == {"starters"}

    pir = _facts()
    line = pir.players[0]
    line.ast = line.stl = line.blk = line.pf = 0
    line.fouls_drawn, line.blk_against, line.pir_official = 2, 1, 999
    assert _rules(check_game(pir), "soft") == {"pir_formula"}
    # 10 pts + 3 reb + 2 drawn, minus 2 missed 2P, 2 missed 3P, 1 missed FT, 1 tov and 1 BA
    line.pir_official = 10 + 3 + 0 + 0 + 0 + 2 - (4 - 2) - (3 - 1) - (4 - 3) - 1 - 1 - 0
    assert check_game(pir).violations == ()


# --------------------------------------------------------------------------- review and fixtures


def test_the_review_adds_partials_venue_attendance_and_neutrality(
    el_engine: Engine, mini_league: Any
) -> None:
    run(el_engine, mini_league)
    played = rows(
        el_engine,
        "SELECT game_id, venue_name, attendance, is_neutral, home_partials_json "
        "FROM el_game WHERE round_number = 2 ORDER BY game_id",
    )
    assert [r[0] for r in played] == ["E2026-0003", "E2026-0004"]
    assert played[0][1] == mini_league.games[3].venue and played[0][3] == 0
    assert (
        played[1][1] == mini_league.games[4].venue
    )  # the "(neutral)" marker is not part of the name
    assert played[1][3] == 1
    assert played[0][2] == mini_league.games[3].attendance
    assert all(json.loads(r[4]) for r in played)
    # round 1 had no review: nothing is known about its venue, and unknown is not "not neutral"
    first = rows(
        el_engine,
        "SELECT venue_name, attendance, is_neutral, home_partials_json FROM el_game "
        "WHERE round_number = 1",
    )
    assert first == [(None, None, None, None)] * 2


def test_the_published_projection_is_kept_as_scores_only(
    el_engine: Engine, mini_league: Any
) -> None:
    run(el_engine, mini_league)
    ledger = rows(
        el_engine,
        "SELECT game_id, kind, model_version, cap_policy, home_pts, away_pts, computed_at, "
        "inputs_cutoff, home_full_strength, home_advantage_points FROM el_projection_ledger "
        "ORDER BY game_id",
    )
    assert [r[0] for r in ledger] == ["E2026-0003", "E2026-0004"]
    for (_, kind, version, cap, home, away, computed, cutoff, full, advantage), code in zip(
        ledger, (3, 4)
    ):
        assert (kind, version, cap) == ("imported", "workbook", "workbook")
        assert (home, away) == mini_league.games[code].proj
        # the workbook published scores only: the rest is not recorded, not zero
        assert computed is None and cutoff is None and full is None and advantage is None


def test_fixtures_become_scheduled_games_with_a_converted_tip_off(
    el_engine: Engine, mini_league: Any
) -> None:
    run(el_engine, mini_league)
    scheduled = rows(
        el_engine,
        "SELECT game_id, game_code, round_number, phase_code, game_date, tipoff_utc, venue_name, "
        "is_neutral, "
        "home_advantage_override, status, stats_status, home_pts FROM el_game WHERE status = "
        "'scheduled' "
        "ORDER BY game_id",
    )
    first, second = scheduled
    assert first[:5] == ("E2026-R03-01", None, 3, "RS", "2026-10-01")
    assert first[5].startswith("2026-10-01 16:00:00")  # 18:00 CEST is 16:00 UTC
    assert second[5].startswith("2026-10-02 18:30:00")  # 20:30 CEST is 18:30 UTC
    assert first[6] == mini_league.games[5].venue  # the "(neutral)" marker is stripped
    assert first[7] == 1 and second[7] == 0  # home advantage 0 and "(neutral)": neutral
    assert first[8] is None and second[8] is None  # 3.5 is the workbook's own setting: no override
    assert first[9:] == ("scheduled", "none", None)


def test_a_home_advantage_that_differs_from_the_setting_is_an_override(
    el_engine: Engine, mini_league: Any
) -> None:
    rows_ = [
        ("Somewhere Hall", 5.0, "20:00 / 20:00 / 21:00"),
        ("Elsewhere Hall", 3.5, "19:00 / 19:00 / 20:00"),
    ]
    run(el_engine, mini_league, mini_league.workbook(fixture_rows=rows_))
    got = rows(
        el_engine,
        "SELECT game_id, is_neutral, home_advantage_override FROM el_game "
        "WHERE status = 'scheduled' ORDER BY game_id",
    )
    assert got == [("E2026-R03-01", 0, 5.0), ("E2026-R03-02", 0, None)]


def test_neutral_needs_both_the_zero_and_the_marker(el_engine: Engine, mini_league: Any) -> None:
    rows_ = [("Hall (neutral)", 3.5, "20:00 / 20:00 / 21:00"), ("Hall", 0, "20:00 / 20:00 / 21:00")]
    report = run(el_engine, mini_league, mini_league.workbook(fixture_rows=rows_))
    got = rows(
        el_engine,
        "SELECT is_neutral, home_advantage_override FROM el_game WHERE status = 'scheduled' "
        "ORDER BY game_id",
    )
    assert got == [(0, None), (0, 0.0)]  # a marker alone, or a zero alone, is not neutrality
    assert any("neutral" in w for w in report.warnings)


def test_what_a_fixture_does_not_say_stays_unknown(el_engine: Engine, mini_league: Any) -> None:
    rows_ = [(None, None, None), ("Hall", 3.5, "tonight, probably")]
    report = run(el_engine, mini_league, mini_league.workbook(fixture_rows=rows_))
    got = rows(
        el_engine,
        "SELECT is_neutral, tipoff_utc, venue_name FROM el_game WHERE status = 'scheduled' "
        "ORDER BY game_id",
    )
    assert got == [
        (None, None, None),
        (0, None, "Hall"),
    ]  # no venue and no number: unknown, never False
    assert sum("tip-off" in w for w in report.warnings) == 2


def test_parse_tipoff() -> None:
    assert parse_tipoff(date(2026, 10, 1), "19:00 / 18:00 / 19:00") == datetime(2026, 10, 1, 16, 0)
    assert parse_tipoff(date(2026, 10, 2), "21:15 / 20:15 / 21:15") == datetime(2026, 10, 2, 18, 15)
    # after the clocks change the Berlin offset is one hour, and the conversion follows it
    assert parse_tipoff(date(2026, 11, 5), "20:00 / 20:00 / 22:00") == datetime(2026, 11, 5, 19, 0)
    assert parse_tipoff(date(2026, 3, 27), "20:00 / 20:00 / 21:00") == datetime(2026, 3, 27, 19, 0)
    assert parse_tipoff(date(2026, 3, 29), "20:00 / 20:00 / 21:00") == datetime(2026, 3, 29, 18, 0)
    for bad in (
        None,
        "",
        "19:00",
        "19:00 / 18:00",
        "25:00 / 24:30 / 1:00",
        "a / b / c",
        19.0,
        "19:00 / 18:60 / 19:00",
    ):
        assert parse_tipoff(date(2026, 10, 1), bad) is None


# --------------------------------------------------------------------------- injury report


def _statuses(engine: Engine) -> list[dict[str, Any]]:
    result = rows(
        engine,
        "SELECT s.player_name_raw, s.person_code, s.status, s.status_raw, s.model_status, "
        "s.reason_category, "
        "s.reason_text, s.expected_return_text, s.expected_return_round_from, "
        "s.expected_return_round_to, "
        "s.expected_return_date, s.source_kind, s.source_label, s.source_url, "
        "s.source_published_at, s.as_of, "
        "s.game_id, s.club_code, s.provenance_note FROM el_intel_status s ORDER BY s.status_id",
    )
    keys = (
        "name",
        "person",
        "status",
        "raw",
        "model",
        "reason",
        "text",
        "ret_text",
        "ret_from",
        "ret_to",
        "ret_date",
        "kind",
        "label",
        "url",
        "published",
        "as_of",
        "game",
        "club",
        "note",
    )
    return [dict(zip(keys, row)) for row in result]


def test_statuses_keep_their_source_link_and_date(el_engine: Engine, mini_league: Any) -> None:
    report = run(el_engine, mini_league)
    stored = _statuses(el_engine)
    assert len(stored) == 7 and report.counts["statuses"] == 7
    a, b, c, d = mini_league.clubs
    first = stored[0]
    assert first["name"] == a.players[0].name and first["person"].startswith("wb-")
    assert (first["status"], first["raw"], first["model"]) == ("out", "OUT", "out")
    assert (first["kind"], first["label"], first["url"]) == (
        "workbookImport",
        "news.example.org",
        "https://news.example.org/a",
    )
    assert first["published"].startswith("2026-09-29 00:00") and first["as_of"].startswith(
        "2026-09-30"
    )
    assert (first["ret_text"], first["ret_from"], first["ret_to"], first["ret_date"]) == (
        "Rounds 2-3",
        2,
        3,
        None,
    )
    assert first["game"] is None  # a player-level entry, not tied to one game
    assert first["note"].startswith("Injury Report row")
    assert first["reason"] == "injury" and first["text"] == "Tendinitis in the heel"
    # the www. prefix is not part of a label
    assert stored[1]["label"] == "example.net" and stored[1]["url"] == "https://www.example.net/b"


def test_squad_choice_is_a_coach_decision_and_available_is_not_an_absence(
    el_engine: Engine, mini_league: Any
) -> None:
    run(el_engine, mini_league)
    row = _statuses(el_engine)[1]
    assert (row["status"], row["reason"], row["ret_text"]) == (
        "available",
        "coachDecision",
        "Squad choice",
    )


def test_a_box_score_link_is_an_inference_not_a_report(el_engine: Engine, mini_league: Any) -> None:
    run(el_engine, mini_league)
    row = _statuses(el_engine)[2]
    assert (row["kind"], row["label"]) == ("boxScoreInference", "EuroLeague box score")
    assert "live.euroleague.net/api/Boxscore" in row["url"]


def test_a_box_score_link_is_recognised_whatever_its_case(
    el_engine: Engine, mini_league: Any
) -> None:
    extra = [
        [
            mini_league.wb("ZZA"),
            mini_league.clubs[0].players[3].name,
            "Did not play",
            "AVAILABLE",
            "AVAILABLE",
            "No injury reported",
            "https://LIVE.euroleague.net/api/BOXSCORE?gamecode=2",
            date(2026, 9, 30),
        ],
        [
            mini_league.wb("ZZA"),
            mini_league.clubs[0].players[4].name,
            "Did not play",
            "AVAILABLE",
            "AVAILABLE",
            "No injury reported",
            "not a link",
            date(2026, 9, 30),
        ],
    ]
    report = run(el_engine, mini_league, mini_league.workbook(extra_status_rows=extra))
    stored = _statuses(el_engine)
    assert stored[7]["kind"] == "boxScoreInference" and stored[7]["label"] == "EuroLeague box score"
    # a source that is not a web link is kept as no link, and the entry says it was typed by hand
    assert stored[8]["url"] is None and stored[8]["kind"] == "manual"
    assert any("not a web link" in w for w in report.warnings)


def test_a_betting_operator_link_is_withheld_but_its_label_and_date_are_kept(
    el_engine: Engine, mini_league: Any
) -> None:
    run(el_engine, mini_league)
    row = _statuses(el_engine)[3]
    assert row["url"] is None
    assert row["label"] == "mozzartsport.com (link withheld: betting operator)"
    assert row["kind"] == "workbookImport" and row["published"].startswith("2026-09-29")
    assert (row["ret_text"], row["ret_date"]) == ("Around 8 Oct", "2026-10-08")
    assert all("mozzart" not in (r["url"] or "") for r in _statuses(el_engine))


def test_long_term_is_kept_as_text(el_engine: Engine, mini_league: Any) -> None:
    run(el_engine, mini_league)
    row = _statuses(el_engine)[4]
    assert (row["ret_text"], row["ret_from"], row["ret_to"], row["ret_date"]) == (
        "Long-term",
        None,
        None,
        None,
    )
    assert row["reason"] == "injury"


def test_a_name_that_matches_no_squad_member_is_never_guessed(
    el_engine: Engine, mini_league: Any
) -> None:
    report = run(el_engine, mini_league)
    row = _statuses(el_engine)[5]
    assert row["name"] == "Nobody On The Squad" and row["person"] is None  # to the review queue
    assert report.counts["statusesUnmatched"] == 1
    assert any("Nobody On The Squad" in u for u in report.unmatched_players)


def test_a_status_outside_the_five_is_rejected_not_defaulted(
    el_engine: Engine, mini_league: Any
) -> None:
    """The workbook's IFERROR(...,1) turned a typo into 'certainly plays'. This does not."""
    report = run(el_engine, mini_league)
    assert len(report.rejected_statuses) == 1 and "DOUBTFULL" in report.rejected_statuses[0]
    assert not any(r["raw"] == "DOUBTFULL" for r in _statuses(el_engine))
    assert all(
        r["status"] in ("out", "doubtful", "questionable", "probable", "available")
        for r in _statuses(el_engine)
    )


def test_personal_reasons_and_return_rounds(el_engine: Engine, mini_league: Any) -> None:
    run(el_engine, mini_league)
    row = _statuses(el_engine)[6]
    assert (row["reason"], row["ret_from"], row["ret_to"]) == ("personal", 4, 4)


def test_free_form_sections_are_skipped(el_engine: Engine, mini_league: Any) -> None:
    run(el_engine, mini_league)
    assert not any("rested two" in (r["text"] or "") for r in _statuses(el_engine))
    assert scalar(el_engine, "SELECT COUNT(*) FROM el_intel_status") == 7


def test_the_batch_records_its_file(el_engine: Engine, mini_league: Any) -> None:
    report = run(el_engine, mini_league)
    batch = rows(el_engine, "SELECT source_kind, file_sha256, row_count FROM el_intel_batch")
    assert batch == [("workbookImport", report.sha256, 7)]
    assert set(r[0] for r in rows(el_engine, "SELECT DISTINCT batch_id FROM el_intel_status")) == {
        scalar(el_engine, "SELECT batch_id FROM el_intel_batch")
    }


@pytest.mark.parametrize(
    ("problem", "expected", "status", "category"),
    [
        ("Torn ACL", None, "out", "injury"),
        ("Hamstring strain", "Rounds 2-4", "out", "injury"),
        ("Back discomfort, held out of Round 2", None, "probable", "injury"),
        ("Right adductor injury", None, "out", "injury"),
        ("Quadriceps strain", None, "out", "injury"),
        ("Quad soreness", None, "questionable", "injury"),
        ("Stomach flu", None, "questionable", "illness"),
        ("Did not travel for Round 2", None, "questionable", "notWithTeam"),
        (
            "Did not travel for Round 1; no injury given",
            "Home game",
            "available",
            "coachDecision",
        ),
        (
            "Left out for Round 1 by coach's decision, fit to play",
            "Squad choice",
            "available",
            "coachDecision",
        ),
        (
            "Coach's decision: left out of the last game",
            "Out for Round 3",
            "out",
            "coachDecision",
        ),
        ("Not in the EuroLeague game roster", "n/a", "out", "notRegistered"),
        ("Now registered: debuted at Efes in Round 2", "Playing", "available", None),
        ("Away for a family event; misses the next game", "After Round 3", "out", "personal"),
        ("League suspension, two games", None, "out", "suspension"),
        ("Assigned to the G League affiliate", None, "out", "gLeague"),
        ("Rested after the long trip", None, "probable", "rest"),
        ("Sat out Round 1 with no reason given", "Unclear", "questionable", None),
        ("Undisclosed", "No update", "questionable", "other"),
        ("Out of the squad", "n/a", "out", "other"),
        (None, None, "out", None),
        ("", None, "doubtful", None),
    ],
)
def test_reason_categories(
    problem: str | None, expected: str | None, status: str, category: str | None
) -> None:
    assert classify_reason(problem, expected, status) == category


def test_a_newer_workbook_appends_only_new_status_rows(el_engine: Engine, mini_league: Any) -> None:
    run(el_engine, mini_league)
    before = rows(
        el_engine,
        "SELECT status_id, player_name_raw, status, recorded_at FROM el_intel_status ORDER BY 1",
    )
    newer = mini_league.workbook(
        extra_status_rows=[
            [
                mini_league.wb("ZZA"),
                mini_league.clubs[0].players[2].name,
                "Ankle sprain",
                "QUESTIONABLE",
                "QUESTIONABLE",
                "Game-time decision",
                "https://news.example.org/g",
                date(2026, 10, 1),
            ]
        ]
    )
    report = run(el_engine, mini_league, newer)
    assert report.status == "ok"
    after = rows(
        el_engine,
        "SELECT status_id, player_name_raw, status, recorded_at FROM el_intel_status ORDER BY 1",
    )
    assert after[:7] == before  # history is never rewritten
    assert (
        len(after) == 8
        and report.counts["statuses"] == 1
        and report.counts["statusesUnchanged"] == 7
    )


# --------------------------------------------------------------------------- idempotency and growth


def test_re_importing_the_same_file_is_a_no_op(el_engine: Engine, mini_league: Any) -> None:
    data = mini_league.workbook()
    first = run(el_engine, mini_league, data)
    snapshot = stable_dump(el_engine)
    version = scalar(el_engine, "SELECT sync_version FROM el_sync_state")
    second = run(el_engine, mini_league, data)
    assert (first.status, second.status) == ("ok", "alreadyImported")
    assert second.counts == {} and second.sha256 == first.sha256
    assert stable_dump(el_engine) == snapshot
    assert scalar(el_engine, "SELECT sync_version FROM el_sync_state") == version == 1
    assert scalar(el_engine, "SELECT COUNT(*) FROM el_ingest_log") == 1


def test_a_newer_workbook_adds_its_rounds_and_rekeys_the_provisional_fixture(
    el_engine: Engine, mini_league: Any
) -> None:
    run(el_engine, mini_league)
    assert scalar(el_engine, "SELECT COUNT(*) FROM el_game WHERE game_id LIKE 'E2026-R03-%'") == 2
    old_ingested = scalar(el_engine, "SELECT ingested_at FROM el_game WHERE game_id = 'E2026-0001'")
    old_lines = _db_lines(el_engine, "E2026-0001")
    with Session(el_engine) as session:  # a projection and a status already hang off the fixture
        session.add(
            ElProjectionLedger(
                game_id="E2026-R03-01",
                kind="latest",
                model_version="v1",
                computed_at=datetime(2026, 10, 1),
                inputs_cutoff=datetime(2026, 10, 1),
                home_pts=80.0,
                away_pts=78.0,
                home_full_strength=81.0,
                away_full_strength=79.0,
                home_attack_index_after_availability=1.0,
                away_attack_index_after_availability=1.0,
                home_defence_index=1.0,
                away_defence_index=1.0,
                home_advantage_points=0.0,
                cap_policy="consistent",
            )
        )
        session.commit()
    report = run(
        el_engine, mini_league, mini_league.workbook(box_rounds=(1, 2, 3), fixture_round=3)
    )
    assert report.status == "ok" and report.as_of_round == 3
    assert report.counts["gamesRekeyed"] == 2 and report.counts["gamesUpdated"] == 2
    assert "gamesFinal" not in report.counts  # rounds 1 and 2 arrived again and changed nothing
    ids = [r[0] for r in rows(el_engine, "SELECT game_id FROM el_game ORDER BY game_id")]
    assert ids == [
        f"E2026-000{n}" for n in range(1, 7)
    ]  # no provisional id and no duplicate game left
    assert rows(
        el_engine,
        "SELECT status, stats_status, game_code FROM el_game WHERE game_id = 'E2026-0005'",
    ) == [("final", "ok", 5)]
    assert rows(el_engine, "SELECT game_id FROM el_projection_ledger WHERE kind = 'latest'") == [
        ("E2026-0005",)
    ]
    # facts the box-score sheet lacks are filled in from the fixture sheet
    filled = rows(
        el_engine, "SELECT is_neutral, tipoff_utc FROM el_game WHERE game_id = 'E2026-0005'"
    )[0]
    assert filled[0] == 1 and filled[1].startswith("2026-10-01 16:00")
    # nothing already stored was rewritten
    assert (
        scalar(el_engine, "SELECT ingested_at FROM el_game WHERE game_id = 'E2026-0001'")
        == old_ingested
    )
    assert _db_lines(el_engine, "E2026-0001") == old_lines
    assert scalar(el_engine, "SELECT COUNT(*) FROM el_player_game WHERE game_id = 'E2026-0005'") > 0
    # a new round of ratings sits beside the old
    assert sorted({r[0] for r in rows(el_engine, "SELECT as_of_round FROM el_team_rating")}) == [
        2,
        3,
    ]
    assert rows(el_engine, "SELECT sync_version, data_through FROM el_sync_state") == [
        (2, "2026-10-02")
    ]
    assert rows(el_engine, "SELECT is_current FROM el_season") == [(1,)]


def test_an_official_row_is_never_overwritten(el_engine: Engine, mini_league: Any) -> None:
    first = mini_league.games
    del first
    league = mini_league
    league.workbook()  # populate league.games
    game = league.games[1]
    with Session(el_engine) as session:
        ensure_identity(session, "live")
        session.add(
            ElSeason(
                season_code="E2026",
                competition_code="E",
                label="2026-27",
                start_year=2026,
                is_current=True,
            )
        )
        for club in league.clubs:
            session.add(ElClub(club_code=club.spec.code, name=club.spec.name))
        session.add(
            ElGame(
                game_id="E2026-0001",
                season_code="E2026",
                game_code=1,
                round_number=1,
                phase_code="RS",
                game_date=date(2026, 9, 24),
                home_club_code=game.home,
                away_club_code=game.away,
                status="final",
                home_pts=1,
                away_pts=2,
                stats_status="none",
                data_source="euroleague-v2",
                ingested_at=datetime(2026, 9, 25),
            )
        )
        session.commit()
    report = run(el_engine, league)
    assert report.status == "ok" and report.counts["skippedOfficial"] == 1
    assert rows(
        el_engine,
        "SELECT home_pts, away_pts, data_source, stats_status FROM el_game "
        "WHERE game_id = 'E2026-0001'",
    ) == [(1, 2, "euroleague-v2", "none")]
    assert (
        scalar(el_engine, "SELECT COUNT(*) FROM el_player_game WHERE game_id = 'E2026-0001'") == 0
    )
    assert (
        scalar(el_engine, "SELECT COUNT(*) FROM el_game WHERE data_source LIKE 'workbook:%'") == 5
    )
    assert (
        scalar(el_engine, "SELECT kind FROM el_store_identity") == "live"
    )  # a real store stays as it was


# --------------------------------------------------------------------------- dry run and refusals


def test_a_dry_run_reports_exactly_what_a_real_run_writes_and_writes_nothing(
    el_engine: Engine, mini_league: Any, tmp_path: Path
) -> None:
    data = mini_league.workbook()
    dry = run(el_engine, mini_league, data, dry_run=True)
    assert dry.status == "ok" and dry.dry_run
    for table in (
        "el_club",
        "el_person",
        "el_game",
        "el_player_game",
        "el_intel_status",
        "el_model_setting",
        "el_store_identity",
        "el_ingest_log",
        "el_sync_state",
        "el_season",
    ):
        assert scalar(el_engine, f"SELECT COUNT(*) FROM {table}") == 0, table
    real = run(second_engine(tmp_path), mini_league, data)
    assert dry.counts == real.counts
    assert (
        run(el_engine, mini_league, data).status == "ok"
    )  # not "already imported": nothing was recorded


def test_the_invented_store_refuses_the_workbook(
    fresh_demo_engine: Engine, mini_league: Any
) -> None:
    before = stable_dump(fresh_demo_engine)
    report = run(fresh_demo_engine, mini_league)
    assert report.status == "failed" and "invented" in (report.error or "")
    assert stable_dump(fresh_demo_engine) == before
    assert (
        scalar(fresh_demo_engine, "SELECT COUNT(*) FROM el_ingest_log") == 0
    )  # the refusal is about the store


def test_an_unreadable_sheet_is_reported_and_the_rest_proceed(
    el_engine: Engine, mini_league: Any
) -> None:
    data = mini_league.workbook()
    strings = zipfile.ZipFile(io.BytesIO(data)).read("xl/sharedStrings.xml")
    broken = patch_zip(
        data, **{"xl/sharedStrings.xml": strings.replace(b">Attack adj<", b">Attack adjustment<")}
    )
    report = run(el_engine, mini_league, broken)
    assert report.status == "ok"
    by_sheet = {o.sheet: o for o in report.sheets}
    assert by_sheet["Team Ratings"].status == "unreadable"
    assert by_sheet["Squads"].status == "imported"
    assert (
        scalar(el_engine, "SELECT COUNT(*) FROM el_team_rating") == 0
    )  # a layout is never guessed
    assert scalar(el_engine, "SELECT COUNT(*) FROM el_game WHERE status = 'final'") == 4
    assert any("Team Ratings" in w for w in report.warnings)


_RATING_HEADERS = ["Code", "Club", "PF/g 2025-26", "PA/g 2025-26", "Attack adj", "Defence adj"]
_FIXTURE_HEADERS = ["#", "Date", "Tip local / CEST / TR", "Home", "Away", "Venue", "Home adv (pts)"]


def test_a_blank_club_cell_skips_the_row_but_an_unknown_code_aborts(
    el_engine: Engine, xl: Any
) -> None:
    ratings = {
        5: dict(enumerate(_RATING_HEADERS, start=1)),
        6: {1: "OLY", 2: "x", 3: 85.0, 4: 84.0, 5: 0.5, 6: -0.5},
        7: {1: "RMA", 2: "y", 3: 86.0, 4: 83.0, 5: 0.25, 6: 0.75},
    }
    fixtures = {
        6: dict(enumerate(_FIXTURE_HEADERS, start=1)),
        7: {1: 1, 2: date(2026, 10, 1), 3: "20:00 / 20:00 / 21:00", 4: "OLY", 5: "RMA", 7: 3.5},
        8: {1: 2, 2: date(2026, 10, 2), 3: "20:00 / 20:00 / 21:00", 4: "OLY", 5: None, 7: 3.5},
        9: {1: 3, 2: date(2026, 10, 3), 4: None, 5: "RMA"},
    }
    data = xl.make([("Team Ratings", ratings), ("Round 3", fixtures)])
    report = import_workbook(data, el_engine)
    assert report.status == "ok", report.error
    assert report.counts["gamesScheduled"] == 1  # the two half-filled fixtures were skipped
    assert sum("not named yet" in w for w in report.warnings) == 2
    fixtures[8][5] = "ZZZ"  # a code that is there but unknown is a different matter
    refused = import_workbook(
        xl.make([("Team Ratings", ratings), ("Round 3", fixtures)]), el_engine
    )
    assert refused.status == "aborted" and "ZZZ" in (refused.error or "")


def test_a_box_score_row_with_no_club_is_skipped(el_engine: Engine, xl: Any) -> None:
    headers = [
        "Game",
        "Date",
        "Club",
        "Opp",
        "H/A",
        "Result",
        "Player",
        "MIN",
        "PTS",
        "2PM",
        "2PA",
        "3PM",
        "3PA",
        "FTM",
        "FTA",
        "OREB",
        "DREB",
        "REB",
        "AST",
        "STL",
        "TOV",
        "BLK",
        "PF",
        "PIR",
    ]
    day = date(2026, 9, 24)

    def line(club: str | None, opp: str | None, side: str, name: str) -> dict[int, Any]:
        values = [
            1,
            day,
            club,
            opp,
            side,
            "W 4-2",
            name,
            40,
            4,
            2,
            3,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            4,
        ]
        return dict(enumerate(values, start=1))

    box = {
        5: dict(enumerate(headers, start=1)),
        6: line("OLY", "RMA", "H", "Invented One"),
        7: line("RMA", "OLY", "A", "Invented Two"),
        8: line(None, "OLY", "A", "Invented Three"),
    }
    ratings = {
        5: dict(enumerate(_RATING_HEADERS, start=1)),
        6: {1: "OLY", 2: "x", 3: 85.0, 4: 84.0, 5: 0.5, 6: -0.5},
        7: {1: "RMA", 2: "y", 3: 86.0, 4: 83.0, 5: 0.25, 6: 0.75},
    }
    report = import_workbook(
        xl.make([("Team Ratings", ratings), ("R1 Box Scores", box)]), el_engine
    )
    assert report.status == "ok", report.error
    assert any("no club or opponent" in w for w in report.warnings)
    assert scalar(el_engine, "SELECT COUNT(*) FROM el_person WHERE name = 'Invented Three'") == 0


def test_a_workbook_with_no_recognised_sheet_is_aborted(el_engine: Engine, xl: Any) -> None:
    report = import_workbook(xl.make([("Random", [["a", "b"]])]), el_engine)
    assert report.status == "aborted" and "not a toolkit workbook" in (report.error or "")


def test_the_season_must_be_knowable(el_engine: Engine, xl: Any) -> None:
    ratings = {
        5: dict(
            enumerate(
                ["Code", "Club", "PF/g 2025-26", "PA/g 2025-26", "Attack adj", "Defence adj"],
                start=1,
            )
        ),
        6: {1: "OLY", 2: "x", 3: 85.0, 4: 84.0, 5: 0.5, 6: -0.5},
    }
    report = import_workbook(xl.make([("Team Ratings", ratings)]), el_engine)
    assert report.status == "aborted" and "season" in (report.error or "")
    assert scalar(el_engine, "SELECT COUNT(*) FROM el_club") == 0


def test_a_title_that_contradicts_the_dates_aborts(el_engine: Engine, mini_league: Any) -> None:
    report = run(
        el_engine, mini_league, mini_league.workbook(title="Synthetic League 2030-31 Toolkit")
    )
    assert (
        report.status == "aborted"
        and "2026" in (report.error or "")
        and "2030" in (report.error or "")
    )


def test_a_file_that_cannot_be_read_fails_closed_and_is_remembered(
    el_engine: Engine, mini_league: Any
) -> None:
    report = import_workbook(b"this is not a workbook", el_engine, crosswalk=mini_league.crosswalk)
    assert report.status == "failed" and "zip" in (report.error or "")
    with Session(el_engine) as session:
        assert "zip" in (last_import_failed(session, report.sha256) or "")
        assert not workbook_imported(session, report.sha256)
    assert scalar(el_engine, "SELECT COUNT(*) FROM el_store_identity") == 0


def test_a_doctype_in_a_part_fails_the_import(el_engine: Engine, mini_league: Any) -> None:
    data = mini_league.workbook()
    hostile = patch_zip(
        data,
        **{"xl/styles.xml": b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><styleSheet/>'},
    )
    report = run(el_engine, mini_league, hostile)
    assert report.status == "failed" and "DOCTYPE" in (report.error or "")
    assert scalar(el_engine, "SELECT COUNT(*) FROM el_game") == 0


def test_a_file_path_can_be_imported(el_engine: Engine, mini_league: Any, tmp_path: Path) -> None:
    path = tmp_path / "toolkit.xlsx"
    path.write_bytes(mini_league.workbook())
    report = import_workbook(path, el_engine, crosswalk=mini_league.crosswalk)
    assert report.status == "ok" and report.file_name == "toolkit.xlsx"
    missing = import_workbook(tmp_path / "missing.xlsx", el_engine)
    assert missing.status == "failed" and "cannot read" in (missing.error or "")


def test_an_oversized_file_path_is_refused_before_reading(
    el_engine: Engine, mini_league: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nbastats.euroleague.importers import xlsx

    path = tmp_path / "big.xlsx"
    path.write_bytes(mini_league.workbook())
    monkeypatch.setattr(xlsx, "MAX_FILE_BYTES", 100)
    report = import_workbook(path, el_engine, crosswalk=mini_league.crosswalk)
    assert report.status == "failed" and "limit" in (report.error or "")


def test_the_store_must_have_its_tables(tmp_path: Path, mini_league: Any) -> None:
    bare = create_el_engine(f"sqlite:///{tmp_path / 'bare.db'}")
    report = import_workbook(mini_league.workbook(), bare, crosswalk=mini_league.crosswalk)
    assert report.status == "failed" and "not ready" in (report.error or "")


def test_the_module_exposes_what_the_guards_need() -> None:
    assert wb_module.READ_MAP and wb_module.IGNORED_HEADERS and wb_module.IGNORED_SETTINGS
