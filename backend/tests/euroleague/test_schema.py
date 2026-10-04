"""The EuroLeague store's schema: its own metadata, its constraints, and its committed data.

What this pins down:

* every ``el_*`` table is on ``ElBase`` and none is on the NBA's or the accounts' metadata, so
  the seeder, ``_INSERT_ORDER`` and ``nbastats/schema.sql`` cannot see them;
* ``euroleague/schema.sql`` is exactly what the models generate (drift fails the build);
* the CHECK constraints that make the invariants structural: a DNP has NULL stats, makes never
  exceed attempts, a final game has a score, a status is one of five, a status needs a link
  unless it is manual or withheld, the model-setting key is on the allowlist;
* there is no betting vocabulary in any table or column name and no terms column anywhere;
* the committed club-code crosswalk and betting-operator denylist are well-formed, and the
  helpers in ``profile`` (ids, season refs, name folding, link screening) behave.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nbastats.accounts.models import AccountBase
from nbastats.euroleague import models, profile
from nbastats.euroleague.models import (
    ElBase,
    ElClub,
    ElGame,
    ElIntelStatus,
    ElModelSetting,
    ElPlayerGame,
    ElProjectionLedger,
    ElSeason,
    ElStoreIdentity,
    ElTeamGame,
    render_schema_sql,
)
from nbastats.euroleague.settings import ALLOWED_KEYS, DEFAULTS
from nbastats.models import Base
from nbastats.models import render_schema_sql as render_nba_schema_sql
from nbastats.shared.market_guard import forbidden_words_in

SCHEMA_SQL = Path(__file__).resolve().parents[2] / "nbastats" / "euroleague" / "schema.sql"
NOW = datetime(2026, 10, 4, 12, 0, 0)


# --------------------------------------------------------------------------- the separation


def test_every_el_table_is_on_its_own_metadata() -> None:
    names = set(ElBase.metadata.tables)
    assert names == set(models.EL_TABLE_NAMES)
    assert len(names) == 26
    assert all(name.startswith("el_") for name in names)
    assert not names & set(Base.metadata.tables)
    assert not names & set(AccountBase.metadata.tables)


def test_the_nba_schema_does_not_describe_the_euroleague() -> None:
    nba = render_nba_schema_sql()
    assert "euroleague" not in nba.lower()
    assert not any(f"CREATE TABLE {name} " in nba for name in models.EL_TABLE_NAMES)


def test_schema_sql_is_current() -> None:
    """``python3 -m nbastats.euroleague.models > nbastats/euroleague/schema.sql``."""
    assert SCHEMA_SQL.read_text(encoding="utf-8") == render_schema_sql()


def test_render_is_deterministic() -> None:
    assert render_schema_sql() == render_schema_sql()


def test_init_creates_every_table(el_engine: Engine) -> None:
    assert set(models.EL_TABLE_NAMES) <= set(inspect(el_engine).get_table_names())


def test_foreign_keys_are_declared_even_though_they_are_not_enforced(el_engine: Engine) -> None:
    inspector = inspect(el_engine)
    game_targets = {fk["referred_table"] for fk in inspector.get_foreign_keys("el_game")}
    assert game_targets == {"el_season", "el_club"}
    assert {fk["referred_table"] for fk in inspector.get_foreign_keys("el_player_game")} == {
        "el_game",
        "el_person",
        "el_club",
    }
    with el_engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 0


# --------------------------------------------------------------------------- vocabulary guard


def _all_names() -> list[str]:
    names: list[str] = []
    for table in ElBase.metadata.sorted_tables:
        names.append(table.name)
        names.extend(column.name for column in table.columns)
    return names


def test_no_betting_word_in_any_table_or_column_name() -> None:
    offenders = {
        name: forbidden_words_in(name) for name in _all_names() if forbidden_words_in(name)
    }
    assert offenders == {}


def test_there_is_no_terms_column_or_table_anywhere() -> None:
    """The terms gate was removed: no terms date, url, outcome or audit table exists."""
    assert [name for name in _all_names() if "terms" in name.lower()] == []


def test_identity_has_exactly_the_stamp_columns() -> None:
    assert {c.name for c in ElStoreIdentity.__table__.columns} == {
        "id",
        "league",
        "kind",
        "created_at",
    }


def test_no_column_holds_a_probability_of_winning_or_a_line() -> None:
    for name in _all_names():
        assert "prob" not in name and "odds" not in name and "line" not in name.split("_")


# --------------------------------------------------------------------------- model settings


def test_setting_allowlist_is_design_section_8_6() -> None:
    expected = {
        "priorRegression",
        "homeAdvantagePoints",
        "teamSd",
        "marginSd",
        "replacementPer40",
        "absorbShare",
        "boostCap",
        "rotationShare",
        "formWeight",
        "capPolicyConsistent",
        "squadReconcile",
        "overtimeScaling",
        "positionCoverageCeiling",
        *(f"roundWeight.{n}" for n in range(1, 41)),
        *(
            f"statusChance.{s}"
            for s in ("out", "doubtful", "questionable", "probable", "available")
        ),
    }
    assert set(ALLOWED_KEYS) == expected
    assert set(models.MODEL_SETTING_KEYS) == expected
    assert set(DEFAULTS) == expected
    assert len(expected) == 58


@pytest.mark.parametrize(
    "key",
    [
        "TotalSD",
        "totalSd",
        "PSDBase",
        "PSDSlope",
        "EdgeP",
        "edgeP",
        "line",
        "modelLine",
        "leanThreshold",
        "roundWeight.0",
        "roundWeight.41",
        "statusChance.maybe",
        "",
    ],
)
def test_the_database_rejects_a_key_off_the_allowlist(el_session: Session, key: str) -> None:
    el_session.add(ElModelSetting(key=key, value=1.0, provenance="manual", set_at=NOW))
    with pytest.raises(IntegrityError):
        el_session.flush()


def test_the_database_accepts_every_allowed_key(el_session: Session) -> None:
    for key in ALLOWED_KEYS:
        el_session.add(ElModelSetting(key=key, value=0.5, provenance="default", set_at=NOW))
    el_session.flush()


def test_provenance_is_a_closed_vocabulary(el_session: Session) -> None:
    el_session.add(ElModelSetting(key="boostCap", value=1.35, provenance="guess", set_at=NOW))
    with pytest.raises(IntegrityError):
        el_session.flush()


# --------------------------------------------------------------------------- helpers to insert


def _season(session: Session) -> None:
    session.add(
        ElSeason(
            season_code="E2026",
            competition_code="E",
            label="2026-27",
            start_year=2026,
            is_current=True,
        )
    )
    for code in ("ZZA", "ZZB"):
        session.add(ElClub(club_code=code, name=f"Club {code}"))
    session.flush()


def _game(session: Session, game_id: str = "E2026-0001", **overrides: object) -> ElGame:
    values: dict[str, object] = dict(
        game_id=game_id,
        season_code="E2026",
        game_code=1,
        round_number=1,
        phase_code="RS",
        game_date=date(2026, 9, 24),
        home_club_code="ZZA",
        away_club_code="ZZB",
        status="final",
        home_pts=80,
        away_pts=77,
        stats_status="ok",
        data_source="synthetic-demo",
        ingested_at=NOW,
    )
    values.update(overrides)
    game = ElGame(**values)
    session.add(game)
    return game


def _flush_fails(session: Session) -> None:
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


# --------------------------------------------------------------------------- games


def test_a_valid_game_is_accepted_and_neutral_may_be_unknown(el_session: Session) -> None:
    _season(el_session)
    game = _game(el_session)
    el_session.flush()
    assert game.is_neutral is None  # unknown is never assumed False


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "final", "home_pts": None},
        {"status": "final", "away_pts": None},
        {"status": "scheduled", "home_pts": None, "away_pts": None, "stats_status": "ok"},
        {"home_club_code": "ZZB", "away_club_code": "ZZB"},
        {"phase_code": "XX"},
        {"status": "abandoned"},
        {"stats_status": "maybe"},
        {"round_number": 0},
        {"data_source": "scraped"},
        {"ot_periods": -1},
        {"attendance": -5},
    ],
)
def test_game_constraints(el_session: Session, overrides: dict[str, object]) -> None:
    _season(el_session)
    _game(el_session, **overrides)
    _flush_fails(el_session)


def test_workbook_and_official_data_sources_are_accepted(el_session: Session) -> None:
    _season(el_session)
    _game(el_session, "E2026-0001", game_code=1, data_source="workbook:ab12cd34")
    _game(el_session, "E2026-0002", game_code=2, data_source="euroleague-v2")
    el_session.flush()


def test_a_game_code_is_unique_within_a_season_but_provisional_ones_are_not(
    el_session: Session,
) -> None:
    _season(el_session)
    _game(
        el_session,
        "E2026-R03-01",
        game_code=None,
        status="scheduled",
        home_pts=None,
        away_pts=None,
        stats_status="none",
    )
    _game(
        el_session,
        "E2026-R03-02",
        game_code=None,
        status="scheduled",
        home_pts=None,
        away_pts=None,
        stats_status="none",
    )
    el_session.flush()  # two NULL game codes are fine
    _game(el_session, "E2026-0001", game_code=7)
    _game(el_session, "E2026-0099", game_code=7)
    _flush_fails(el_session)


# --------------------------------------------------------------------------- lines


def _line_prerequisites(session: Session) -> None:
    _season(session)
    _game(session)
    session.flush()


def test_a_dnp_line_with_every_stat_null_is_accepted(el_session: Session) -> None:
    _line_prerequisites(el_session)
    el_session.add(
        ElPlayerGame(game_id="E2026-0001", person_code="p1", club_code="ZZA", participation="dnp")
    )
    el_session.flush()


@pytest.mark.parametrize(
    "column",
    ["pts", "seconds_played", "plus_minus", "pir_official", "reb", "fouls_drawn", "blk_against"],
)
def test_a_dnp_line_may_not_carry_a_stat_not_even_a_zero(el_session: Session, column: str) -> None:
    """Twelve zeros for a man who never stepped on the floor would drag every average down."""
    _line_prerequisites(el_session)
    el_session.add(
        ElPlayerGame(
            game_id="E2026-0001",
            person_code="p1",
            club_code="ZZA",
            participation="dnp",
            **{column: 0},
        )
    )
    _flush_fails(el_session)


def test_a_played_line_may_have_unrecorded_stats(el_session: Session) -> None:
    """The workbook records no fouls drawn; that is NULL on a played line, not zero."""
    _season(el_session)
    _game(el_session)
    el_session.flush()
    el_session.add(
        ElPlayerGame(
            game_id="E2026-0001",
            person_code="p1",
            club_code="ZZA",
            participation="played",
            seconds_played=1200,
            pts=10,
            fouls_drawn=None,
        )
    )
    el_session.flush()
    row = el_session.get(ElPlayerGame, ("E2026-0001", "p1"))
    assert row is not None and row.fouls_drawn is None


@pytest.mark.parametrize(
    "stats",
    [
        {"fgm2": 5, "fga2": 4},
        {"fgm3": 2, "fga3": 1},
        {"ftm": 3, "fta": 2},
        {"pts": -1},
        {"reb": -2},
        {"seconds_played": -10},
    ],
)
def test_line_constraints(el_session: Session, stats: dict[str, int]) -> None:
    _season(el_session)
    _game(el_session)
    el_session.flush()
    el_session.add(
        ElPlayerGame(
            game_id="E2026-0001", person_code="p1", club_code="ZZA", participation="played", **stats
        )
    )
    _flush_fails(el_session)


def test_plus_minus_and_pir_may_be_negative(el_session: Session) -> None:
    _season(el_session)
    _game(el_session)
    el_session.flush()
    el_session.add(
        ElPlayerGame(
            game_id="E2026-0001",
            person_code="p1",
            club_code="ZZA",
            participation="played",
            plus_minus=-9,
            pir_official=-13,
        )
    )
    el_session.flush()


def test_team_line_constraints(el_session: Session) -> None:
    _season(el_session)
    _game(el_session)
    el_session.flush()
    el_session.add(
        ElTeamGame(
            game_id="E2026-0001",
            club_code="ZZA",
            is_home=True,
            opp_club_code="ZZB",
            pts=80,
            opp_pts=77,
            fgm2=20,
            fga2=10,
        )
    )
    _flush_fails(el_session)


# --------------------------------------------------------------------------- statuses


def _status(**overrides: object) -> ElIntelStatus:
    values: dict[str, object] = dict(
        club_code="ZZA",
        player_name_raw="Invented Player",
        status="out",
        source_kind="pressArticle",
        source_label="example.org",
        source_url="https://example.org/a",
        source_published_at=NOW,
        as_of=NOW,
        recorded_at=NOW,
    )
    values.update(overrides)
    return ElIntelStatus(**values)


def test_a_status_may_be_null_but_not_unknown(el_session: Session) -> None:
    el_session.add(_status(status=None))
    el_session.flush()  # "no status given" is storable: it renders as an em dash
    for bad in ("maybe", "Out", "available "):
        el_session.add(_status(status=bad))
        _flush_fails(el_session)


@pytest.mark.parametrize(
    "overrides",
    [
        {"reason_category": "sulking"},
        {"model_status": "dunno"},
        {"source_kind": "rumour"},
        {"source_url": None, "source_kind": "pressArticle"},
        {"source_url": None, "source_kind": "workbookImport", "source_label": "example.org"},
        {"expected_return_round_from": 5, "expected_return_round_to": 3},
    ],
)
def test_status_constraints(el_session: Session, overrides: dict[str, object]) -> None:
    el_session.add(_status(**overrides))
    _flush_fails(el_session)


def test_a_manual_status_needs_no_link_and_a_withheld_one_keeps_its_label(
    el_session: Session,
) -> None:
    el_session.add(_status(source_kind="manual", source_url=None, source_label="Entered by hand"))
    el_session.add(
        _status(
            source_kind="workbookImport",
            source_url=None,
            source_label=f"example.org {models.WITHHELD_LABEL_SUFFIX}",
        )
    )
    el_session.flush()


def test_no_market_vocabulary_in_the_euroleague_sources() -> None:
    """The prose guard (``test_web_release_hardening``) keeps gambling vocabulary out of the
    sources a reader can see; this package is held to the same list, and to ``moneyline``."""
    import re

    banned = (
        r"odds", r"vig", r"kelly", r"wager\w*", r"sportsbook\w*", r"payout\w*", r"bett?ing",
        r"bookmaker\w*", r"parlay\w*", r"stake", r"staking", r"over/under", r"point spread",
        r"moneyline\w*",
    )  # fmt: skip
    pattern = re.compile(r"\b(?:" + "|".join(banned) + r")\b")
    root = Path(models.__file__).resolve().parent
    scanned, offences = 0, []
    for path in sorted(root.rglob("*.py")):
        scanned += 1
        for found in sorted(set(pattern.findall(path.read_text(encoding="utf-8").lower()))):
            offences.append(f"{path.relative_to(root)} mentions {found!r}")
    assert scanned >= 9
    assert offences == [], offences


def test_the_withheld_suffix_is_one_string_everywhere() -> None:
    assert models.WITHHELD_LABEL_SUFFIX == profile.WITHHELD_SUFFIX
    assert profile.WITHHELD_SUFFIX == "(link withheld: betting operator)"  # the design's wording
    document = json.loads((profile.DATA_DIR / "source_denylist.json").read_text(encoding="utf-8"))
    assert document["withheldSuffix"] == profile.WITHHELD_SUFFIX


# --------------------------------------------------------------------------- ledger


def _ledger(**overrides: object) -> ElProjectionLedger:
    values: dict[str, object] = dict(
        game_id="E2026-0001",
        kind="latest",
        model_version="v1",
        computed_at=NOW,
        inputs_cutoff=NOW,
        home_pts=84.5,
        away_pts=80.25,
        home_full_strength=86.0,
        away_full_strength=81.0,
        home_attack_index_after_availability=1.01,
        away_attack_index_after_availability=0.99,
        home_defence_index=0.98,
        away_defence_index=1.0,
        home_advantage_points=3.5,
        cap_policy="consistent",
    )
    values.update(overrides)
    return ElProjectionLedger(**values)


def test_an_imported_projection_may_hold_scores_only(el_session: Session) -> None:
    el_session.add(
        ElProjectionLedger(
            game_id="E2026-0001",
            kind="imported",
            model_version="workbook",
            home_pts=81.8,
            away_pts=81.4,
            cap_policy="workbook",
        )
    )
    el_session.flush()


def test_every_other_kind_must_be_complete(el_session: Session) -> None:
    el_session.add(_ledger())
    el_session.flush()
    for kind in ("latest", "locked", "reconstructed"):
        el_session.add(_ledger(kind=kind, computed_at=None))
        _flush_fails(el_session)
        el_session.add(_ledger(kind=kind, home_full_strength=None))
        _flush_fails(el_session)


def test_the_ledger_has_no_line_odds_or_total_column() -> None:
    columns = {c.name for c in ElProjectionLedger.__table__.columns}
    assert not {
        c for c in columns if "line" in c.split("_") or "odds" in c or "over" in c.split("_")
    }
    assert "home_pts" in columns and "away_pts" in columns


# --------------------------------------------------------------------------- identity


def test_identity_is_a_singleton_of_one_league_and_three_kinds(el_session: Session) -> None:
    el_session.add(ElStoreIdentity(id=2, league="euroleague", kind="live", created_at=NOW))
    _flush_fails(el_session)
    el_session.add(ElStoreIdentity(id=1, league="nba", kind="live", created_at=NOW))
    _flush_fails(el_session)
    el_session.add(ElStoreIdentity(id=1, league="euroleague", kind="scraped", created_at=NOW))
    _flush_fails(el_session)
    for kind in profile.STORE_KINDS:
        el_session.execute(text("DELETE FROM el_store_identity"))
        el_session.add(ElStoreIdentity(id=1, league="euroleague", kind=kind, created_at=NOW))
        el_session.flush()
    el_session.rollback()


# --------------------------------------------------------------------------- profile helpers


def test_season_refs() -> None:
    assert profile.season_code(2026) == "E2026"
    assert profile.season_label(2026) == "2026-27"
    assert profile.season_label(1999) == "1999-00"
    assert profile.parse_season_ref("E2026") == profile.season_ref_from_start_year(2026)
    assert profile.parse_season_ref("2026-27").code == "E2026"
    assert profile.parse_season_ref(" 2026-27 ").start_year == 2026
    for bad in ("2026-28", "E26", "2026", "latest", "", None, "e2026"):
        assert profile.parse_season_ref(bad) is None


def test_game_ids() -> None:
    assert profile.official_game_id("E2026", 12) == "E2026-0012"
    assert profile.provisional_game_id("E2026", 3, 1) == "E2026-R03-01"
    parts = profile.parse_game_id("E2026-0012")
    assert parts is not None and parts.game_code == 12 and not parts.is_provisional
    parts = profile.parse_game_id("E2026-R03-01")
    assert (
        parts is not None and parts.is_provisional and (parts.round_number, parts.number) == (3, 1)
    )
    assert profile.parse_game_id("0022500001") is None  # an NBA id is neither shape
    assert profile.parse_game_id("E2026-12") is None


def test_the_estimate_label_says_where_the_line_came_from() -> None:
    assert profile.ESTIMATE_BASIS_LABEL == "your estimate; source not recorded"


def test_data_source_labels() -> None:
    assert profile.workbook_data_source("ab12cd34ef56") == "workbook:ab12cd34"
    assert profile.is_workbook_data_source("workbook:ab12cd34")
    assert not profile.is_workbook_data_source("euroleague-v2")
    assert not profile.is_workbook_data_source(None)


def test_codes() -> None:
    assert profile.normalise_club_code(" pan ") == "PAN"
    assert profile.normalise_club_code("") is None
    assert profile.normalise_club_code(7) is None
    assert profile.normalise_person_code("P012345") == "012345"
    assert profile.normalise_person_code(" 012345 ") == "012345"
    assert profile.normalise_person_code("P") == "P"
    assert profile.normalise_person_code("") is None
    assert profile.normalise_person_code(None) is None


def test_fold_name() -> None:
    assert profile.fold_name("Tamsin  Ashgrove") == "tamsin ashgrove"
    assert profile.fold_name("O'Brannoch") == "obrannoch"
    assert profile.fold_name("Ashby-Corwen") == "ashby corwen"
    assert profile.fold_name("Dušana Ðorvić") == "dusana dorvic"
    assert profile.fold_name("İvren") == "ivren"
    assert profile.fold_name("Marcus Jr.") == "marcus jr"  # a suffix is kept, on purpose
    assert profile.fold_name("Marcus") != profile.fold_name("Marcus Jr.")
    assert profile.fold_name(None) == ""
    assert profile.fold_name("   ") == ""


def test_minted_person_codes_are_deterministic_and_distinct() -> None:
    first = profile.workbook_person_code("ZZA", "tamsin ashgrove")
    assert first == profile.workbook_person_code("ZZA", "tamsin ashgrove")
    assert first.startswith("wb-") and len(first) == 11
    assert first != profile.workbook_person_code("ZZB", "tamsin ashgrove")
    assert first != profile.workbook_person_code("ZZA", "tamsin ashgrove", attempt=1)


# --------------------------------------------------------------------------- committed data


def test_the_committed_crosswalk_is_well_formed() -> None:
    crosswalk = profile.load_club_codes()
    assert len(crosswalk.rows) == 20
    assert len({r.workbook_code for r in crosswalk.rows}) == 20
    assert len({r.official_code for r in crosswalk.rows}) == 20
    assert all(r.verified in ("fixture", "pending") for r in crosswalk.rows)
    # none could be verified from the build environment, and the file says so
    assert set(crosswalk.unverified_official_codes) == {r.official_code for r in crosswalk.rows}
    document = json.loads((profile.DATA_DIR / "club_codes.json").read_text(encoding="utf-8"))
    assert "unreachable" in document["note"]


def test_the_workbook_par_and_the_official_par_are_different_clubs() -> None:
    crosswalk = profile.load_club_codes()
    paris = crosswalk.by_workbook("PAR")
    partizan = crosswalk.by_workbook("PRT")
    assert paris is not None and partizan is not None
    assert paris.official_code != partizan.official_code
    assert crosswalk.by_official("PAR") is partizan  # the official PAR is the other club
    assert crosswalk.by_workbook("ZZZ") is None
    assert crosswalk.by_workbook(None) is None


def test_a_malformed_crosswalk_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "codes.json"
    path.write_text(
        json.dumps(
            {
                "clubs": [
                    {
                        "workbookCode": "AAA",
                        "officialCode": "AAA",
                        "name": "A",
                        "verified": "pending",
                    },
                    {
                        "workbookCode": "BBB",
                        "officialCode": "AAA",
                        "name": "B",
                        "verified": "pending",
                    },
                ]
            }
        )
    )
    with pytest.raises(profile.CrosswalkError):
        profile.load_club_codes(path)
    path.write_text(
        json.dumps(
            {
                "clubs": [
                    {"workbookCode": "AAA", "officialCode": "AAA", "name": "A", "verified": "maybe"}
                ]
            }
        )
    )
    with pytest.raises(profile.CrosswalkError):
        profile.load_club_codes(path)
    path.write_text("not json")
    with pytest.raises(profile.CrosswalkError):
        profile.load_club_codes(path)


def test_the_denylist_names_the_two_operators() -> None:
    assert profile.load_source_denylist() >= {"mozzartsport.com", "mozzartbet.com"}


@pytest.mark.parametrize(
    ("url", "denied"),
    [
        ("https://www.mozzartsport.com/kosarka/vesti/x/1", True),
        ("https://mozzartsport.com/x", True),
        ("https://MOZZARTBET.com./x", True),
        ("https://m.mozzartbet.com/x", True),
        ("https://example.org/mozzartsport.com", False),  # a path is not a host
        ("https://notmozzartsport.com/x", False),
        ("https://mozzartsport.com@example.org/x", False),  # the host is example.org
        ("not a url", False),
        ("", False),
        (None, False),
    ],
)
def test_denylist_matches_hosts_only(url: str | None, denied: bool) -> None:
    assert profile.is_denied_url(url) is denied


def test_screening_withholds_the_link_and_keeps_the_label() -> None:
    url, label = profile.screen_source_link("https://www.mozzartsport.com/a", "mozzartsport.com")
    assert url is None
    assert label == "mozzartsport.com (link withheld: betting operator)"
    assert profile.screen_source_link("https://example.org/a", "example.org") == (
        "https://example.org/a",
        "example.org",
    )
    assert profile.screen_source_link(None, "Entered by hand") == (None, "Entered by hand")


def test_a_club_named_after_an_operator_is_data_not_a_match() -> None:
    """The club called 'Partizan Mozzart Bet' is a club name: the list matches hosts only."""
    partizan = profile.load_club_codes().by_workbook("PRT")
    assert partizan is not None and "Mozzart" in partizan.name
    assert not profile.is_denied_url(partizan.name)


# --------------------------------------------------------------------------- model settings (code)


def test_every_setting_has_a_default_with_a_provenance() -> None:
    from nbastats.euroleague.settings import PROVENANCES

    assert all(spec.provenance in PROVENANCES for spec in DEFAULTS.values())
    assert DEFAULTS["teamSd"].provenance == "workbookUnvalidated"  # the unchecked spread says so
    assert DEFAULTS["marginSd"].provenance == "workbookUnvalidated"
    assert DEFAULTS["homeAdvantagePoints"].value == 3.5
    assert (
        DEFAULTS["capPolicyConsistent"].value == 1.0
    )  # the lead's default: team and players agree
    assert DEFAULTS["boostCap"].value == 1.35 and DEFAULTS["absorbShare"].value == 0.6
    assert (
        DEFAULTS["replacementPer40"].value == 9.0
        and DEFAULTS["positionCoverageCeiling"].value == 0.05
    )
    # the workbook's own first two round weights are reproduced by the default 1 / (n + 9)
    assert DEFAULTS["roundWeight.1"].value == pytest.approx(0.10)
    assert DEFAULTS["roundWeight.2"].value == pytest.approx(0.09, abs=0.001)
    assert DEFAULTS["roundWeight.3"].value == pytest.approx(1 / 12)
    chances = {
        k.split(".")[1]: v.value for k, v in DEFAULTS.items() if k.startswith("statusChance.")
    }
    assert chances == {
        "out": 0.0,
        "doubtful": 0.25,
        "questionable": 0.5,
        "probable": 0.85,
        "available": 1.0,
    }


def test_a_default_is_what_you_get_until_something_is_stored(el_session: Session) -> None:
    from nbastats.euroleague.settings import effective_settings, setting_value

    effective = effective_settings(el_session)
    assert set(effective) == set(ALLOWED_KEYS)
    assert all(e.is_default or e.provenance == "workbookUnvalidated" for e in effective.values())
    assert effective["priorRegression"].value == 0.3 and effective["priorRegression"].set_at is None
    assert effective["priorRegression"].is_default and effective["priorRegression"].description
    assert setting_value(el_session, "boostCap") == 1.35


def test_setting_a_value_validates_stores_and_reports_it(el_session: Session) -> None:
    from nbastats.euroleague.settings import effective_settings, set_setting, setting_value

    row = set_setting(el_session, "priorRegression", 0.45, "manual", NOW)
    assert (row.value, row.provenance, row.set_at) == (0.45, "manual", NOW)
    assert setting_value(el_session, "priorRegression") == 0.45
    changed = effective_settings(el_session)["priorRegression"]
    assert (changed.value, changed.provenance, changed.is_default, changed.set_at) == (
        0.45,
        "manual",
        False,
        NOW,
    )
    set_setting(el_session, "priorRegression", 0.5, "manual", datetime(2026, 10, 5))
    assert el_session.query(ElModelSetting).count() == 1  # replaced, not duplicated


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("priorRegression", 1.5),
        ("priorRegression", -0.1),
        ("boostCap", 0.9),
        ("boostCap", 3.5),
        ("teamSd", 0),
        ("marginSd", -1),
        ("homeAdvantagePoints", -1),
        ("absorbShare", 2),
        ("capPolicyConsistent", 0.5),
        ("squadReconcile", 2),
        ("roundWeight.3", 1.2),
        ("statusChance.out", 1.01),
        ("positionCoverageCeiling", 1.5),
        ("priorRegression", float("nan")),
        ("priorRegression", float("inf")),
        ("priorRegression", "0.3"),
        ("priorRegression", None),
        ("priorRegression", True),
        ("totalSd", 13.5),
        ("edgeP", 0.55),
        ("TotalSD", 13.5),
        ("line", 168.5),
    ],
)
def test_a_bad_setting_is_refused_before_the_database_sees_it(
    el_session: Session, key: str, value: object
) -> None:
    from nbastats.euroleague.settings import InvalidSettingError, set_setting, validate_setting

    with pytest.raises(InvalidSettingError):
        validate_setting(key, value)
    with pytest.raises(InvalidSettingError):
        set_setting(el_session, key, value)
    assert el_session.query(ElModelSetting).count() == 0


def test_a_provenance_outside_the_vocabulary_is_refused(el_session: Session) -> None:
    from nbastats.euroleague.settings import InvalidSettingError, set_setting

    with pytest.raises(InvalidSettingError, match="provenance"):
        set_setting(el_session, "boostCap", 1.3, "hunch")


def test_the_switches_take_one_or_zero(el_session: Session) -> None:
    from nbastats.euroleague.settings import set_setting, validate_setting

    for key in ("capPolicyConsistent", "squadReconcile", "overtimeScaling"):
        assert validate_setting(key, 0) == 0.0 and validate_setting(key, 1) == 1.0
    set_setting(el_session, "capPolicyConsistent", 0, "manual")  # 'workbook' policy, selectable
    stored = el_session.get(ElModelSetting, "capPolicyConsistent")
    assert stored is not None and stored.value == 0.0


def test_the_status_chances_are_a_table_that_must_stay_a_table(el_session: Session) -> None:
    from nbastats.euroleague.settings import set_setting, status_chance_table

    assert status_chance_table(el_session) == {
        "out": 0.0,
        "doubtful": 0.25,
        "questionable": 0.5,
        "probable": 0.85,
        "available": 1.0,
    }
    set_setting(el_session, "statusChance.doubtful", 0.3, "manual")
    assert status_chance_table(el_session)["doubtful"] == 0.3


def test_an_import_does_not_rewrite_an_unchanged_setting_or_a_protected_one(
    el_session: Session,
) -> None:
    from nbastats.euroleague.settings import apply_imported_setting, set_setting

    assert apply_imported_setting(el_session, "boostCap", 1.4, "workbook", NOW) is True
    assert (
        apply_imported_setting(el_session, "boostCap", 1.4, "workbook", NOW) is False
    )  # unchanged
    assert (
        apply_imported_setting(el_session, "boostCap", 1.5, "workbook", NOW) is True
    )  # a newer value
    set_setting(el_session, "absorbShare", 0.7, "manual")
    assert apply_imported_setting(el_session, "absorbShare", 0.6, "workbook", NOW) is False
    assert el_session.get(ElModelSetting, "absorbShare").value == 0.7  # type: ignore[union-attr]
    set_setting(el_session, "teamSd", 8.0, "fittedLedger")
    assert apply_imported_setting(el_session, "teamSd", 9.5, "workbookUnvalidated", NOW) is False


def test_the_settings_digest_changes_with_the_constants(el_session: Session) -> None:
    from nbastats.euroleague.settings import set_setting, settings_digest

    first = settings_digest(el_session)
    assert len(first) == 64 and first == settings_digest(el_session)
    set_setting(el_session, "boostCap", 1.4, "manual")
    second = settings_digest(el_session)
    assert second != first
    set_setting(el_session, "boostCap", 1.35, "workbook")  # the default's own number, now sourced
    third = settings_digest(el_session)
    assert third not in (first, second)  # a different provenance is a different input
    set_setting(el_session, "boostCap", 1.35, "default")
    assert settings_digest(el_session) == first  # back to exactly the defaults
