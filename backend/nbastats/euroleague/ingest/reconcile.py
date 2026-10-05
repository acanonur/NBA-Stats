"""Reconciliation: making the workbook's identities and the service's identities the same ones.

Why there is anything to reconcile
----------------------------------
The user's workbook and the live service describe the same league in different vocabularies, and
the store holds both:

* **People.** The workbook names a player ``"First Last"`` and the importer minted him a code,
  ``wb-<8 hex>``, from his club and folded name. The service gives him an official code and writes
  his name ``"SURNAME, GIVEN"``. Until they are matched, his round-one box score (workbook) and his
  round-five box score (service) belong to two different people.
* **Games.** A Round 3 fixture the workbook knew about is ``E2026-R03-01``. The service names the
  same game ``E2026-0017``.
* **Clubs.** The workbook's abbreviations are a different system from the official codes (the
  workbook's ``PAR`` is Paris; the official ``PAR`` is believed to be Partizan). The committed
  crosswalk maps one to the other, and **every row of it is unverified**, because it was written
  where no EuroLeague host could be asked.

The rule that governs all three: **a match is made only when it is exact and unique, and anything
else waits for a person.** Nothing is guessed.

People: club and folded name, both orders, unique on both sides
---------------------------------------------------------------
A workbook person is matched to an official one when they are on the *same club this season* and
their folded names are equal (the official name in either order, or the passport name, or a
two-word alias; see :func:`~nbastats.euroleague.ingest.parse.person_variants`), and when the match
is unique from both directions: one official person for that workbook name, and one workbook name
for that official person. The workbook abbreviates a few names (``"A.B. Surname"``), and an
abbreviation matches nothing by this rule, so it stays a ``wb-`` person and appears in
``GET /v1/el/review-queue``, which lists every workbook person that has no
``el_person_alias`` row. Two workbook people who fold to one name, or two official people who do,
are *ambiguous*: neither is matched.

A match re-keys every table that holds a person code, in one transaction (the caller's):
registrations, box-score lines, player rates, availability entries, hand-entered overrides and
headline subjects. Where the official person already has a row at the same key (the box-score
writer had already created his registration) the rows are **merged**: the official row is kept
and the workbook-only facts it lacks (the five-way position label, the role, the age) are copied
in. The ``wb-`` person row itself is kept and an ``el_person_alias`` row records the match; an
availability entry is *identity*-re-keyed and its content is never rewritten, so the append-only
history stays append-only.

Games: round and clubs
----------------------
A provisional game (``E2026-R03-NN``) is re-keyed to the official id when the schedule names a game
in the same season and round between the same two clubs, home and away. Every table that
references a game (lines, team lines, ledger, availability, overrides) moves with it.

Clubs: verified, new or mismatched, and never re-keyed
------------------------------------------------------
The live club list (E4) is compared with the store. A club whose official code is already there is
**verified**. A club that is not there and resembles none that is (no shared broadcast code, no
folded name) is **new** and is created. A club that is not there but shares a broadcast code or a
folded name with a club that is, under a *different* official code, is a **mismatch**: the
crosswalk disagrees with the service. Re-keying a club would rewrite every game, rating and line it
ever had, so this module does not do it. It reports the mismatch (which the probe and the CLI
print) and :func:`find_alias_conflict` makes the writers refuse the club and its games until a
person fixes ``data/club_codes.json``. The same report lists the crosswalk rows the service has
now confirmed, which are the ones to change from ``pending`` to ``fixture``. That edit is a
committed file and needs a recording behind it, so it is a human step.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Collection, Final, Iterable, Mapping, Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ..models import (
    ElClub,
    ElClubAlias,
    ElGame,
    ElIntelNewsSubject,
    ElIntelOverride,
    ElIntelStatus,
    ElPerson,
    ElPersonAlias,
    ElPlayerGame,
    ElPlayerRate,
    ElProjectionLedger,
    ElRegistration,
    ElTeamGame,
)
from ..profile import ClubCrosswalk, fold_name, load_club_codes
from .parse import ClubInfo, RegistrationInfo, person_variants, stored_name_variants

__all__ = [
    "PERSON_TABLES",
    "GAME_TABLES",
    "ClubReport",
    "PeopleReport",
    "ReconcileReport",
    "find_alias_conflict",
    "find_game_by_matchup",
    "rekey_game",
    "rekey_person",
    "reconcile_clubs",
    "reconcile_people",
    "reconcile_all",
    "ensure_aliases",
    "to_json",
]

#: Tables whose rows carry a person code and must move when a person is re-keyed. A test walks
#: ``ElBase.metadata`` and fails if a table gains a ``person_code`` column that is not here (or in
#: :data:`_PERSON_EXEMPT`), so a new table cannot quietly be left pointing at a retired code.
PERSON_TABLES: Final = (
    ElRegistration,
    ElPlayerGame,
    ElPlayerRate,
    ElIntelStatus,
    ElIntelOverride,
    ElIntelNewsSubject,
)
_PERSON_EXEMPT: Final = frozenset({"el_person", "el_person_alias"})

#: Tables whose rows carry a game id and must move when a provisional game is re-keyed.
GAME_TABLES: Final = (
    ElPlayerGame,
    ElTeamGame,
    ElProjectionLedger,
    ElIntelStatus,
    ElIntelOverride,
)
_GAME_EXEMPT: Final = frozenset({"el_game"})


# --------------------------------------------------------------------------------- reports


@dataclass
class ClubReport:
    """What comparing the live club list with the store found."""

    verified: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    #: ``(live code, reason)``: the service disagrees with the store about who a club is.
    mismatches: list[tuple[str, str]] = field(default_factory=list)
    #: Store clubs the service did not list (a club that is not in this season, or a wrong code).
    not_in_live: list[str] = field(default_factory=list)
    #: Crosswalk rows the service has now confirmed: the ones to flip from pending to fixture.
    crosswalk_confirmed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": sorted(self.verified),
            "created": sorted(self.created),
            "mismatches": [{"code": c, "reason": r} for c, r in self.mismatches],
            "notInLive": sorted(self.not_in_live),
            "crosswalkConfirmed": sorted(self.crosswalk_confirmed),
        }


@dataclass
class PeopleReport:
    matched: list[tuple[str, str]] = field(default_factory=list)
    #: ``(club, workbook code, name, reason)``.
    ambiguous: list[tuple[str, str, str, str]] = field(default_factory=list)
    unmatched: list[tuple[str, str, str]] = field(default_factory=list)
    rows_rekeyed: dict[str, int] = field(default_factory=dict)
    registrations_merged: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched": len(self.matched),
            "ambiguous": [
                {"club": c, "code": w, "name": n, "reason": r} for c, w, n, r in self.ambiguous
            ],
            "unmatched": [{"club": c, "code": w, "name": n} for c, w, n in self.unmatched],
            "rowsRekeyed": dict(sorted(self.rows_rekeyed.items())),
            "registrationsMerged": self.registrations_merged,
        }


@dataclass
class ReconcileReport:
    clubs: ClubReport = field(default_factory=ClubReport)
    people: PeopleReport = field(default_factory=PeopleReport)
    games_rekeyed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def needs_attention(self) -> bool:
        return bool(self.clubs.mismatches or self.people.ambiguous or self.people.unmatched)

    def to_dict(self) -> dict[str, Any]:
        return {
            "clubs": self.clubs.to_dict(),
            "people": self.people.to_dict(),
            "gamesRekeyed": [{"from": a, "to": b} for a, b in self.games_rekeyed],
        }

    def render(self) -> str:
        """A short summary for the command line and the probe."""
        c, p = self.clubs, self.people
        lines = [
            f"clubs: {len(c.verified)} verified, {len(c.created)} new, "
            f"{len(c.mismatches)} mismatched, {len(c.not_in_live)} not listed by the service",
            f"people: {len(p.matched)} matched, {len(p.ambiguous)} ambiguous, "
            f"{len(p.unmatched)} unmatched (those wait in the review queue)",
            f"games re-keyed: {len(self.games_rekeyed)}",
        ]
        for code, reason in c.mismatches:
            lines.append(f"  CLUB MISMATCH {code}: {reason}")
        if c.crosswalk_confirmed:
            lines.append(
                "  crosswalk rows confirmed by the service (change 'pending' to 'fixture' in "
                "data/club_codes.json): " + ", ".join(sorted(c.crosswalk_confirmed))
            )
        return "\n".join(lines)


# ----------------------------------------------------------------------------------- clubs


def find_alias_conflict(session: Session, info: ClubInfo) -> str | None:
    """A reason the club ``info`` must not be created, or ``None`` when it may be.

    The reason is that the store already has this club under a *different* official code: a
    broadcast code that an alias already assigns to someone else, or another club with the same
    broadcast code or the same folded name. Creating it would split one club's history in two.
    Returns ``None`` when ``info.code`` is already in the store (it *is* that club).
    """
    if session.get(ElClub, info.code) is not None:
        return None
    if info.tv_code:
        alias = session.get(ElClubAlias, ("tv", info.tv_code))
        if alias is not None and alias.club_code != info.code:
            return (
                f"the broadcast code {info.tv_code} already belongs to club {alias.club_code}, "
                f"but the service says this club is {info.code}"
            )
    folded = fold_name(info.name or "")
    for other in session.execute(select(ElClub).where(ElClub.club_code != info.code)).scalars():
        same_tv = bool(info.tv_code) and other.tv_code == info.tv_code
        same_name = bool(folded) and fold_name(other.name) == folded
        if same_tv or same_name:
            why = "broadcast code" if same_tv else "name"
            return (
                f"the store already has {other.club_code} ({other.name}) with the same {why}, "
                f"but the service says this club is {info.code}; fix data/club_codes.json"
            )
    return None


def ensure_aliases(session: Session, club_code: str, info: ClubInfo) -> int:
    """Make sure the club has its ``official`` and ``tv`` aliases. Returns how many were added."""
    added = 0
    for system, code in (("official", club_code), ("tv", info.tv_code)):
        if not code:
            continue
        row = session.get(ElClubAlias, (system, code))
        if row is None:
            session.add(ElClubAlias(system=system, code=code, club_code=club_code))
            added += 1
    return added


def reconcile_clubs(
    session: Session, live: Sequence[ClubInfo], crosswalk: ClubCrosswalk | None = None
) -> ClubReport:
    """Compare the service's clubs (E4) with the store: verify, create, or report a mismatch.

    Creates nothing that looks like a club the store already has. See the module docstring.
    """
    book = crosswalk if crosswalk is not None else load_club_codes()
    report = ClubReport()
    live_codes = {c.code for c in live}
    for info in live:
        existing = session.get(ElClub, info.code)
        if existing is not None:
            report.verified.append(info.code)
            if info.tv_code and not existing.tv_code:
                existing.tv_code = info.tv_code
            if info.short_name and not existing.short_name:
                existing.short_name = info.short_name
            if info.country_code and not existing.country_code:
                existing.country_code = info.country_code
            ensure_aliases(session, info.code, info)
            row = book.by_official(info.code)
            if row is not None and row.verified != "fixture":
                report.crosswalk_confirmed.append(info.code)
            continue
        conflict = find_alias_conflict(session, info)
        if conflict is not None:
            report.mismatches.append((info.code, conflict))
            continue
        session.add(
            ElClub(
                club_code=info.code,
                tv_code=info.tv_code,
                name=info.name or info.code,
                short_name=info.short_name,
                country_code=info.country_code,
            )
        )
        ensure_aliases(session, info.code, info)
        report.created.append(info.code)
    session.flush()
    for club in session.execute(select(ElClub)).scalars():
        if club.club_code not in live_codes:
            report.not_in_live.append(club.club_code)
    return report


# ----------------------------------------------------------------------------------- games


def find_game_by_matchup(
    session: Session, season_code: str, round_number: int, home: str, away: str
) -> ElGame | None:
    """The stored game for this season, round and (home, away) pair, whatever its id."""
    return (
        session.execute(
            select(ElGame).where(
                ElGame.season_code == season_code,
                ElGame.round_number == round_number,
                ElGame.home_club_code == home,
                ElGame.away_club_code == away,
            )
        )
        .scalars()
        .first()
    )


def rekey_game(session: Session, old: str, new: str, *, game_code: int | None = None) -> None:
    """Move a game, and every row that references it, from id ``old`` to id ``new``.

    The new id must not exist. ``game_code`` is set in the same statement, because a provisional
    game has none and the unique key on ``(season, game_code)`` is what makes the official one
    official. Nothing is lost: the rows keep their content and only the reference changes.
    """
    if old == new:
        return
    if session.get(ElGame, new) is not None:
        raise ValueError(f"cannot re-key {old} to {new}: {new} already exists")
    session.flush()
    for model in GAME_TABLES:
        session.execute(update(model).where(model.game_id == old).values(game_id=new))
    values: dict[str, Any] = {"game_id": new}
    if game_code is not None:
        values["game_code"] = game_code
    session.execute(update(ElGame).where(ElGame.game_id == old).values(**values))
    session.expire_all()


# ---------------------------------------------------------------------------------- people


def _merge_registration(old: ElRegistration, target: ElRegistration) -> None:
    """Copy the facts only the workbook knew into the surviving official registration."""
    for name in (
        "position5_workbook",
        "role_workbook",
        "age_workbook",
        "dorsal",
        "position_code",
        "position_name",
    ):
        if getattr(target, name) is None and getattr(old, name) is not None:
            setattr(target, name, getattr(old, name))


def rekey_person(session: Session, old: str, new: str, report: PeopleReport | None = None) -> None:
    """Move every row that references person ``old`` to person ``new``.

    Where the new person already has a row at the same key, the rows are merged: the surviving
    row is the official one, and for a registration the workbook-only facts it lacks are copied
    across first. Availability entries and overrides are re-keyed by identity only; their content
    (status, source, dates) is never touched.
    """
    session.flush()
    counts = report.rows_rekeyed if report is not None else {}

    def bump(name: str, n: int = 1) -> None:
        counts[name] = counts.get(name, 0) + n

    for reg in list(
        session.execute(select(ElRegistration).where(ElRegistration.person_code == old)).scalars()
    ):
        target = session.get(ElRegistration, (reg.season_code, reg.club_code, new))
        if target is None:
            session.execute(
                update(ElRegistration)
                .where(
                    ElRegistration.season_code == reg.season_code,
                    ElRegistration.club_code == reg.club_code,
                    ElRegistration.person_code == old,
                )
                .values(person_code=new)
            )
            bump("registrations")
        else:
            _merge_registration(reg, target)
            session.delete(reg)
            bump("registrations")
            if report is not None:
                report.registrations_merged += 1
    session.flush()

    for line in list(
        session.execute(select(ElPlayerGame).where(ElPlayerGame.person_code == old)).scalars()
    ):
        if session.get(ElPlayerGame, (line.game_id, new)) is None:
            session.execute(
                update(ElPlayerGame)
                .where(ElPlayerGame.game_id == line.game_id, ElPlayerGame.person_code == old)
                .values(person_code=new)
            )
        else:
            session.delete(line)
        bump("playerLines")
    for rate in list(
        session.execute(select(ElPlayerRate).where(ElPlayerRate.person_code == old)).scalars()
    ):
        if session.get(ElPlayerRate, (rate.season_code, new, rate.as_of_round)) is None:
            session.execute(
                update(ElPlayerRate)
                .where(
                    ElPlayerRate.season_code == rate.season_code,
                    ElPlayerRate.person_code == old,
                    ElPlayerRate.as_of_round == rate.as_of_round,
                )
                .values(person_code=new)
            )
        else:
            session.delete(rate)
        bump("playerRates")
    for subject in list(
        session.execute(
            select(ElIntelNewsSubject).where(ElIntelNewsSubject.person_code == old)
        ).scalars()
    ):
        if session.get(ElIntelNewsSubject, (subject.item_id, subject.club_code, new)) is None:
            session.execute(
                update(ElIntelNewsSubject)
                .where(
                    ElIntelNewsSubject.item_id == subject.item_id,
                    ElIntelNewsSubject.club_code == subject.club_code,
                    ElIntelNewsSubject.person_code == old,
                )
                .values(person_code=new)
            )
        else:
            session.delete(subject)
        bump("newsSubjects")
    session.flush()
    for model, name in ((ElIntelStatus, "statuses"), (ElIntelOverride, "overrides")):
        done = session.execute(
            update(model).where(model.person_code == old).values(person_code=new)
        )
        if done.rowcount:
            bump(name, done.rowcount)
    session.expire_all()


def _official_variants_from_store(
    session: Session, season_code: str
) -> dict[str, dict[str, frozenset[str]]]:
    """``{club: {official person code: variants}}`` from what the store holds (names only)."""
    out: dict[str, dict[str, frozenset[str]]] = {}
    rows = session.execute(
        select(ElRegistration.club_code, ElPerson)
        .join(ElPerson, ElPerson.person_code == ElRegistration.person_code)
        .where(ElRegistration.season_code == season_code, ElPerson.code_system == "official")
    )
    for club, person in rows:
        out.setdefault(club, {})[person.person_code] = stored_name_variants(
            person.name, person.display_name
        )
    return out


def reconcile_people(
    session: Session,
    season_code: str,
    registrations: Mapping[str, Iterable[RegistrationInfo]] | None = None,
    *,
    now: datetime,
    only_clubs: Collection[str] | None = None,
) -> PeopleReport:
    """Match workbook people to official ones, club by club, and re-key what they matched.

    ``registrations`` (``{club code: E5 registrations}``) brings the richer spellings of a fresh
    sweep (passport names, aliases); without it only what the store holds is used. A workbook
    person already aliased is skipped. ``only_clubs`` limits the work to those clubs (a roster
    sweep matches one club at a time as each arrives). See the module docstring for the rule.
    """
    report = PeopleReport()
    official: dict[str, dict[str, frozenset[str]]] = _official_variants_from_store(
        session, season_code
    )
    if registrations is not None:
        for club, regs in registrations.items():
            book = official.setdefault(club, {})
            for reg in regs:
                book[reg.person.code] = book.get(reg.person.code, frozenset()) | person_variants(
                    reg.person
                )
    aliased = {code for (code,) in session.execute(select(ElPersonAlias.wb_code))}
    rows = session.execute(
        select(ElRegistration.club_code, ElPerson)
        .join(ElPerson, ElPerson.person_code == ElRegistration.person_code)
        .where(ElRegistration.season_code == season_code, ElPerson.code_system == "workbook")
        .order_by(ElRegistration.club_code, ElPerson.person_code)
    )
    by_club: dict[str, list[ElPerson]] = {}
    for club, person in rows:
        if only_clubs is not None and club not in only_clubs:
            continue
        if person.person_code not in aliased:
            by_club.setdefault(club, []).append(person)

    for club, workbook_people in sorted(by_club.items()):
        candidates = official.get(club, {})
        # who each folded workbook name could be, and who could claim each official person
        claimed_by: dict[str, list[str]] = {}
        options: dict[str, list[str]] = {}
        for person in workbook_people:
            folded = fold_name(person.name)
            hits = sorted(
                code for code, variants in candidates.items() if folded and folded in variants
            )
            options[person.person_code] = hits
            for code in hits:
                claimed_by.setdefault(code, []).append(person.person_code)
        for person in workbook_people:
            hits = options[person.person_code]
            if not hits:
                report.unmatched.append((club, person.person_code, person.name))
                continue
            if len(hits) > 1:
                report.ambiguous.append(
                    (club, person.person_code, person.name, f"{len(hits)} official people match")
                )
                continue
            target = hits[0]
            if len(claimed_by[target]) > 1:
                report.ambiguous.append(
                    (
                        club,
                        person.person_code,
                        person.name,
                        f"{len(claimed_by[target])} workbook people match official person {target}",
                    )
                )
                continue
            rekey_person(session, person.person_code, target, report)
            session.add(
                ElPersonAlias(
                    wb_code=person.person_code,
                    official_code=target,
                    matched_by="clubAndName",
                    matched_at=now,
                )
            )
            report.matched.append((person.person_code, target))
    session.flush()
    return report


def reconcile_all(
    session: Session,
    season_code: str,
    *,
    clubs: Sequence[ClubInfo] | None = None,
    registrations: Mapping[str, Iterable[RegistrationInfo]] | None = None,
    now: datetime,
    crosswalk: ClubCrosswalk | None = None,
) -> ReconcileReport:
    """Clubs, then people, in the caller's transaction (it commits, or rolls everything back)."""
    report = ReconcileReport()
    if clubs is not None:
        report.clubs = reconcile_clubs(session, clubs, crosswalk)
    report.people = reconcile_people(session, season_code, registrations, now=now)
    return report


def to_json(report: ReconcileReport) -> str:
    """The report as stable JSON, for ``el_ingest_log.detail_json``."""
    return json.dumps(report.to_dict(), sort_keys=True)
