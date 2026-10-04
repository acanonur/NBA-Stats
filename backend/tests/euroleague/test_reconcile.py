"""Reconciliation: workbook identities become official ones, exactly and uniquely, or not at all.

The store these tests build looks the way a workbook import leaves it: a club, people the importer
minted codes for (``wb-...``) with registrations, a provisional game id, box-score lines, a player
rate, a dated availability entry, a hand-entered override and a headline subject, all pointing at
the minted codes. Then the live service's registrations arrive (the authored E5 fixture, invented
names), and the tests ask what moved, what merged, what was left alone and, above all, what was
*not* guessed.

Nothing here is real: clubs are ``ZZ*``, players are fantasy-named, and the crosswalk is built in
the test.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nbastats.euroleague.ingest import parse, reconcile, write
from nbastats.euroleague.ingest.parse import ClubInfo
from nbastats.euroleague.models import (
    ElBase,
    ElClub,
    ElClubAlias,
    ElGame,
    ElIntelNewsItem,
    ElIntelNewsSubject,
    ElIntelOverride,
    ElIntelStatus,
    ElPerson,
    ElPersonAlias,
    ElPlayerGame,
    ElPlayerRate,
    ElProjectionLedger,
    ElRegistration,
    ElSeason,
    ElTeamGame,
)
from nbastats.euroleague.profile import ClubCodeRow, ClubCrosswalk

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "euroleague" / "authored"
NOW = datetime(2031, 10, 4, 9, 0)
SEASON = "E2031"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture()
def squad() -> list[parse.RegistrationInfo]:
    return list(parse.parse_people(load("people_ZZA.json"), expect_club="ZZA").items)


@pytest.fixture()
def session(el_engine: Any) -> Any:
    with Session(el_engine, future=True) as s:
        write.ensure_live_writer(s)
        s.add(
            ElSeason(
                season_code=SEASON,
                competition_code="E",
                label="2031-32",
                start_year=2031,
                is_current=True,
            )
        )
        for code, tv, name in (
            ("ZZA", "ZHB", "Zenith Harbour BC"),
            ("ZZB", "BMF", "Brightmoor Falcons"),
        ):
            s.add(ElClub(club_code=code, tv_code=tv, name=name))
        s.commit()
        yield s


# ---------------------------------------------------------------------------- builders


def add_wb_person(
    s: Session,
    code: str,
    name: str,
    club: str = "ZZA",
    *,
    pos5: str | None = "PG",
    role: str | None = "Starter",
    age: int | None = 27,
) -> None:
    s.add(ElPerson(person_code=code, code_system="workbook", name=name))
    s.add(
        ElRegistration(
            season_code=SEASON,
            club_code=club,
            person_code=code,
            position5_workbook=pos5,
            role_workbook=role,
            age_workbook=age,
            active=True,
            source="workbook",
        )
    )
    s.flush()


def add_footprint(s: Session, code: str, club: str = "ZZA") -> None:
    """One row in every table that references a person, all for ``code``."""
    s.add(
        ElPlayerGame(
            game_id="E2031-R01-01", person_code=code, club_code=club, participation="played", pts=9
        )
    )
    s.add(
        ElPlayerRate(
            season_code=SEASON,
            person_code=code,
            as_of_round=2,
            club_code=club,
            basis="workbookOfficial",
            prior_minutes=400.0,
            pts40=18.5,
        )
    )
    s.add(
        ElIntelStatus(
            club_code=club,
            person_code=code,
            player_name_raw="raw name",
            status="out",
            source_kind="workbookImport",
            source_label="a source",
            source_url="https://example.org/a",
            source_published_at=NOW,
            as_of=NOW,
            recorded_at=NOW,
            reason_text="sore knee",
        )
    )
    s.add(ElIntelOverride(person_code=code, club_code=club, status="doubtful", created_at=NOW))
    item = ElIntelNewsItem(
        feed_id=None,
        title="t",
        link="https://example.org/n",
        published_at=NOW,
        fetched_at=NOW,
        source_name="s",
    )
    s.add(item)
    s.flush()
    s.add(ElIntelNewsSubject(item_id=item.item_id, club_code=club, person_code=code))
    s.flush()


def add_provisional_game(s: Session, game_id: str = "E2031-R01-01") -> None:
    s.add(
        ElGame(
            game_id=game_id,
            season_code=SEASON,
            game_code=None,
            round_number=1,
            phase_code="RS",
            game_date=date(2031, 10, 2),
            home_club_code="ZZA",
            away_club_code="ZZB",
            status="final",
            home_pts=84,
            away_pts=79,
            stats_status="ok",
            data_source="workbook:abcd1234",
            ingested_at=NOW,
        )
    )
    s.flush()


def official(s: Session, squad: list[parse.RegistrationInfo]) -> None:
    write.upsert_people(s, SEASON, "ZZA", squad, now=NOW)
    s.flush()


# ---------------------------------------------------------------------------- people


def test_a_workbook_person_matches_by_club_and_folded_name_in_any_published_order(
    session: Session, squad: list[parse.RegistrationInfo]
) -> None:
    first, second, third = squad[0].person, squad[1].person, squad[2].person
    add_wb_person(session, "wb-00000001", first.name)  # "Given Surname"
    add_wb_person(
        session, "wb-00000002", f"{second.official_name.replace(',', '')}"
    )  # "SURNAME GIVEN"
    add_wb_person(
        session, "wb-00000003", parse.friendly_name(third.abbreviated_name or "")
    )  # "G. Surname"
    official(session, squad)
    report = reconcile.reconcile_people(session, SEASON, {"ZZA": squad}, now=NOW)
    assert sorted(report.matched) == [
        ("wb-00000001", first.code),
        ("wb-00000002", second.code),
        ("wb-00000003", third.code),
    ]
    aliases = {
        a.wb_code: (a.official_code, a.matched_by)
        for a in session.execute(select(ElPersonAlias)).scalars()
    }
    assert aliases["wb-00000001"] == (first.code, "clubAndName")
    assert report.unmatched == [] and report.ambiguous == []


def test_every_table_that_references_a_person_moves_with_him(
    session: Session, squad: list[parse.RegistrationInfo]
) -> None:
    target = squad[0].person
    add_wb_person(session, "wb-00000001", target.name)
    add_footprint(session, "wb-00000001")
    official(session, squad)
    session.commit()
    report = reconcile.reconcile_people(session, SEASON, {"ZZA": squad}, now=NOW)
    session.commit()
    code = target.code
    assert report.rows_rekeyed == {
        "registrations": 1,
        "playerLines": 1,
        "playerRates": 1,
        "newsSubjects": 1,
        "statuses": 1,
        "overrides": 1,
    }
    for model in (ElPlayerGame, ElPlayerRate, ElIntelStatus, ElIntelOverride, ElIntelNewsSubject):
        codes = {c for (c,) in session.execute(select(model.person_code))}
        assert codes == {code}, model.__tablename__
    assert "wb-00000001" not in {c for (c,) in session.execute(select(ElRegistration.person_code))}


def test_a_registration_is_merged_not_duplicated_and_keeps_what_only_the_workbook_knew(
    session: Session, squad: list[parse.RegistrationInfo]
) -> None:
    target = squad[0]
    add_wb_person(session, "wb-00000001", target.person.name, pos5="SG", role="Rotation", age=31)
    official(
        session, squad
    )  # the official registration already exists, with the service's position
    session.commit()
    report = reconcile.reconcile_people(session, SEASON, {"ZZA": squad}, now=NOW)
    session.commit()
    rows = list(
        session.execute(
            select(ElRegistration).where(ElRegistration.person_code == target.person.code)
        ).scalars()
    )
    assert len(rows) == 1 and report.registrations_merged == 1
    row = rows[0]
    assert row.source == "euroleague-v2" and row.position_code == target.position_code
    assert (row.position5_workbook, row.role_workbook, row.age_workbook) == ("SG", "Rotation", 31)


def test_reconciling_twice_changes_nothing(
    session: Session, squad: list[parse.RegistrationInfo]
) -> None:
    add_wb_person(session, "wb-00000001", squad[0].person.name)
    add_footprint(session, "wb-00000001")
    official(session, squad)
    first = reconcile.reconcile_people(session, SEASON, {"ZZA": squad}, now=NOW)
    session.commit()
    second = reconcile.reconcile_people(session, SEASON, {"ZZA": squad}, now=NOW)
    assert len(first.matched) == 1 and second.matched == [] and second.rows_rekeyed == {}
    assert session.query(ElPersonAlias).count() == 1


def test_an_abbreviation_that_the_service_does_not_publish_is_never_guessed(
    session: Session, squad: list[parse.RegistrationInfo]
) -> None:
    surname = squad[3].person.name.split()[-1]
    add_wb_person(session, "wb-00000009", f"Q. {surname}")  # right club, plausible initial: wrong
    add_wb_person(session, "wb-00000008", "Nobody Atall")
    official(session, squad)
    report = reconcile.reconcile_people(session, SEASON, {"ZZA": squad}, now=NOW)
    assert report.matched == []
    assert {u[1] for u in report.unmatched} == {"wb-00000008", "wb-00000009"}
    assert session.query(ElPersonAlias).count() == 0  # they stay in the review queue


def test_two_workbook_names_that_fold_together_match_neither(
    session: Session, squad: list[parse.RegistrationInfo]
) -> None:
    name = squad[0].person.name
    add_wb_person(session, "wb-00000001", name)
    add_wb_person(session, "wb-00000002", name.upper())  # folds to the same thing
    official(session, squad)
    report = reconcile.reconcile_people(session, SEASON, {"ZZA": squad}, now=NOW)
    assert report.matched == []
    assert len(report.ambiguous) == 2
    assert all("workbook people match official person" in a[3] for a in report.ambiguous)


def test_two_official_people_with_one_name_match_nobody(
    session: Session, squad: list[parse.RegistrationInfo]
) -> None:
    twin = parse.parse_people(load("people_ZZA.json")).items[1]
    clone = parse.RegistrationInfo(
        person=parse.PersonInfo(
            code="999999",
            official_name=twin.person.official_name,
            name=twin.person.name,
            abbreviated_name=twin.person.abbreviated_name,
        ),
        club_code="ZZA",
        season_code=SEASON,
        dorsal="99",
        position_code=2,
        position_name="Forward",
        active=True,
        start_date=None,
        end_date=None,
    )
    add_wb_person(session, "wb-00000001", twin.person.name)
    official(session, [*squad, clone])
    report = reconcile.reconcile_people(session, SEASON, {"ZZA": [*squad, clone]}, now=NOW)
    assert report.matched == [] and len(report.ambiguous) == 1
    assert "2 official people match" in report.ambiguous[0][3]


def test_a_person_is_only_matched_within_his_own_club(
    session: Session, squad: list[parse.RegistrationInfo]
) -> None:
    add_wb_person(session, "wb-00000001", squad[0].person.name, club="ZZB")  # same name, other club
    official(session, squad)
    report = reconcile.reconcile_people(session, SEASON, {"ZZA": squad}, now=NOW)
    assert report.matched == []
    assert report.unmatched == [("ZZB", "wb-00000001", squad[0].person.name)]


def test_only_clubs_limits_the_work(session: Session, squad: list[parse.RegistrationInfo]) -> None:
    add_wb_person(session, "wb-00000001", squad[0].person.name)
    official(session, squad)
    nothing = reconcile.reconcile_people(
        session, SEASON, {"ZZA": squad}, now=NOW, only_clubs={"ZZB"}
    )
    assert nothing.matched == [] and nothing.unmatched == []
    done = reconcile.reconcile_people(session, SEASON, {"ZZA": squad}, now=NOW, only_clubs={"ZZA"})
    assert len(done.matched) == 1


def test_without_a_sweep_the_stored_names_and_published_abbreviation_still_match(
    session: Session, squad: list[parse.RegistrationInfo]
) -> None:
    person = squad[2].person
    add_wb_person(session, "wb-00000001", person.name)
    add_wb_person(session, "wb-00000002", parse.friendly_name(person.abbreviated_name or "") + " ")
    official(session, squad)
    report = reconcile.reconcile_people(session, SEASON, None, now=NOW)
    # both fold to a stored spelling of the same official person, so neither is unique: ambiguous
    assert report.matched == [] and len(report.ambiguous) == 2
    other = squad[4].person
    add_wb_person(session, "wb-00000003", other.name)
    again = reconcile.reconcile_people(session, SEASON, None, now=NOW)
    assert [m[0] for m in again.matched] == ["wb-00000003"]


def test_an_availability_entry_is_re_keyed_by_identity_and_its_content_is_untouched(
    session: Session, squad: list[parse.RegistrationInfo]
) -> None:
    add_wb_person(session, "wb-00000001", squad[0].person.name)
    add_footprint(session, "wb-00000001")
    official(session, squad)
    session.commit()

    def snapshot() -> list[dict[str, Any]]:
        rows = session.execute(select(ElIntelStatus)).scalars()
        return [
            {
                c.name: getattr(r, c.name)
                for c in ElIntelStatus.__table__.columns
                if c.name != "person_code"
            }
            for r in rows
        ]

    before = snapshot()
    reconcile.reconcile_people(session, SEASON, {"ZZA": squad}, now=NOW)
    session.commit()
    assert snapshot() == before
    assert session.execute(select(ElIntelStatus.person_code)).scalar_one() == squad[0].person.code


def test_a_workbook_person_without_a_registration_is_never_touched(
    session: Session, squad: list[parse.RegistrationInfo]
) -> None:
    session.add(
        ElPerson(person_code="wb-0000000f", code_system="workbook", name=squad[0].person.name)
    )
    official(session, squad)
    report = reconcile.reconcile_people(session, SEASON, {"ZZA": squad}, now=NOW)
    assert report.matched == []


# ------------------------------------------------------------------- the table coverage


def test_every_table_with_a_person_code_is_handled_by_rekey_person() -> None:
    carrying = {
        name
        for name, table in ElBase.metadata.tables.items()
        if "person_code" in table.c and name not in reconcile._PERSON_EXEMPT
    }
    handled = {model.__tablename__ for model in reconcile.PERSON_TABLES}
    assert (
        carrying == handled
    ), f"a table gained a person_code and rekey_person does not know: {carrying ^ handled}"


def test_every_table_with_a_game_id_is_handled_by_rekey_game() -> None:
    carrying = {
        name
        for name, table in ElBase.metadata.tables.items()
        if "game_id" in table.c and name not in reconcile._GAME_EXEMPT
    }
    handled = {model.__tablename__ for model in reconcile.GAME_TABLES}
    assert (
        carrying == handled
    ), f"a table gained a game_id and rekey_game does not know: {carrying ^ handled}"


# --------------------------------------------------------------------------------- games


def test_a_provisional_game_is_re_keyed_with_everything_that_points_at_it(session: Session) -> None:
    add_provisional_game(session)
    session.add(
        ElPlayerGame(
            game_id="E2031-R01-01", person_code="p1", club_code="ZZA", participation="played", pts=2
        )
    )
    session.add(
        ElTeamGame(
            game_id="E2031-R01-01",
            club_code="ZZA",
            is_home=True,
            opp_club_code="ZZB",
            pts=84,
            opp_pts=79,
        )
    )
    session.add(
        ElProjectionLedger(
            game_id="E2031-R01-01",
            kind="imported",
            model_version="workbook",
            home_pts=80.0,
            away_pts=78.0,
            cap_policy="workbook",
        )
    )
    session.add(
        ElIntelStatus(
            club_code="ZZA",
            person_code="p1",
            game_id="E2031-R01-01",
            player_name_raw="n",
            status="out",
            source_kind="manual",
            source_label="l",
            source_published_at=NOW,
            as_of=NOW,
            recorded_at=NOW,
        )
    )
    session.add(
        ElIntelOverride(person_code="p1", game_id="E2031-R01-01", status="out", created_at=NOW)
    )
    session.commit()
    found = reconcile.find_game_by_matchup(session, SEASON, 1, "ZZA", "ZZB")
    assert found is not None and found.game_id == "E2031-R01-01"
    reconcile.rekey_game(session, "E2031-R01-01", "E2031-0001", game_code=1)
    session.commit()
    game = session.get(ElGame, "E2031-0001")
    assert game is not None and game.game_code == 1 and session.get(ElGame, "E2031-R01-01") is None
    for model in reconcile.GAME_TABLES:
        ids = {g for (g,) in session.execute(select(model.game_id))}
        assert ids == {"E2031-0001"}, model.__tablename__


def test_rekeying_onto_an_existing_id_is_refused(session: Session) -> None:
    add_provisional_game(session)
    session.add(
        ElGame(
            game_id="E2031-0001",
            season_code=SEASON,
            game_code=1,
            round_number=2,
            phase_code="RS",
            game_date=date(2031, 10, 9),
            home_club_code="ZZB",
            away_club_code="ZZA",
            status="scheduled",
            stats_status="none",
            data_source="euroleague-v2",
            ingested_at=NOW,
        )
    )
    session.commit()
    with pytest.raises(ValueError, match="already exists"):
        reconcile.rekey_game(session, "E2031-R01-01", "E2031-0001", game_code=1)
    assert session.get(ElGame, "E2031-R01-01") is not None
    reconcile.rekey_game(session, "E2031-0001", "E2031-0001")  # same id: a no-op


# --------------------------------------------------------------------------------- clubs


def crosswalk() -> ClubCrosswalk:
    return ClubCrosswalk(
        (
            ClubCodeRow("WZA", "ZZA", "ZHB", "Zenith Harbour BC", "Harbour", "pending"),
            ClubCodeRow("WZB", "ZZB", "BMF", "Brightmoor Falcons", "Brightmoor", "fixture"),
            ClubCodeRow("WZD", "ZZD", "DMW", "Dunmere Wolves", "Dunmere", "pending"),
        )
    )


def test_clubs_are_verified_created_or_reported_and_never_rekeyed(session: Session) -> None:
    live = [
        ClubInfo(code="ZZA", tv_code="ZHB", name="Zenith Harbour BC"),  # in the store: verified
        ClubInfo(code="ZZE", tv_code="ELM", name="Elmswood Lynx"),  # unseen: created
        ClubInfo(code="ZZX", tv_code="BMF", name="Some Other Name"),  # the store has BMF as ZZB
    ]
    report = reconcile.reconcile_clubs(session, live, crosswalk())
    assert report.verified == ["ZZA"] and report.created == ["ZZE"]
    assert [c for c, _ in report.mismatches] == ["ZZX"]
    assert "ZZB" in report.mismatches[0][1] and "broadcast code" in report.mismatches[0][1]
    assert report.crosswalk_confirmed == ["ZZA"]  # ZZB is already 'fixture'; ZZE is not in the book
    assert report.not_in_live == ["ZZB"]
    assert session.get(ElClub, "ZZX") is None and session.get(ElClub, "ZZE") is not None
    systems = {
        (a.system, a.code, a.club_code) for a in session.execute(select(ElClubAlias)).scalars()
    }
    assert {("official", "ZZE", "ZZE"), ("tv", "ELM", "ZZE"), ("official", "ZZA", "ZZA")} <= systems


def test_a_club_with_a_known_name_under_a_new_code_is_a_mismatch(session: Session) -> None:
    report = reconcile.reconcile_clubs(
        session, [ClubInfo(code="ZZY", tv_code="NEW", name="Brightmoor  FALCONS")], crosswalk()
    )
    assert [c for c, _ in report.mismatches] == ["ZZY"] and "same name" in report.mismatches[0][1]
    assert session.get(ElClub, "ZZY") is None


def test_an_existing_club_is_only_filled_in_never_renamed(session: Session) -> None:
    club = session.get(ElClub, "ZZA")
    club.short_name = None
    reconcile.reconcile_clubs(
        session,
        [
            ClubInfo(
                code="ZZA",
                tv_code="ZHB",
                name="A New Name",
                short_name="Harbour",
                country_code="UTO",
            )
        ],
        crosswalk(),
    )
    assert (club.name, club.short_name, club.country_code) == (
        "Zenith Harbour BC",
        "Harbour",
        "UTO",
    )


def test_find_alias_conflict_is_none_for_a_club_already_in_the_store(session: Session) -> None:
    assert (
        reconcile.find_alias_conflict(
            session, ClubInfo(code="ZZA", tv_code="OTHER", name="Whatever")
        )
        is None
    )
    assert reconcile.find_alias_conflict(session, ClubInfo(code="ZZQ", tv_code="ZHB")) is not None
    assert (
        reconcile.find_alias_conflict(
            session, ClubInfo(code="ZZQ", tv_code="QQQ", name="Quite New")
        )
        is None
    )


def test_the_tv_alias_is_checked_as_well_as_the_club_row(session: Session) -> None:
    session.add(ElClubAlias(system="tv", code="XYZ", club_code="ZZB"))
    session.flush()
    conflict = reconcile.find_alias_conflict(
        session, ClubInfo(code="ZZQ", tv_code="XYZ", name="Fresh")
    )
    assert conflict is not None and "already belongs to club ZZB" in conflict


def test_reconcile_all_runs_clubs_then_people_and_renders(
    session: Session, squad: list[parse.RegistrationInfo]
) -> None:
    add_wb_person(session, "wb-00000001", squad[0].person.name)
    official(session, squad)
    report = reconcile.reconcile_all(
        session,
        SEASON,
        clubs=[ClubInfo(code="ZZA", tv_code="ZHB"), ClubInfo(code="ZZX", tv_code="BMF")],
        registrations={"ZZA": squad},
        now=NOW,
        crosswalk=crosswalk(),
    )
    assert len(report.people.matched) == 1 and report.clubs.mismatches
    assert report.needs_attention
    text = report.render()
    assert (
        "1 matched" in text
        and "CLUB MISMATCH ZZX" in text
        and "change 'pending' to 'fixture'" in text
    )
    data = json.loads(reconcile.to_json(report))
    assert data["people"]["matched"] == 1 and data["clubs"]["mismatches"][0]["code"] == "ZZX"
    clean = reconcile.reconcile_all(session, SEASON, now=NOW)
    assert not clean.needs_attention
