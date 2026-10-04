"""An invented EuroLeague, shaped like the workbook, for offline use and CI.

Why a demo exists
-----------------
The real league's data arrives from a workbook and a rate-limited service, neither of which a
test, a screenshot or a first look on a new machine can lean on, and neither of which may be
committed to a public repository. So there is a second league that never existed: twenty
invented clubs (``ZZA`` to ``ZZT``), squads of invented players, four rounds of results with
box scores, and a fifth round still to play. Every committed EuroLeague fixture and every
EuroLeague test that needs a league is built from it.

It is shaped like the workbook on purpose
-----------------------------------------
The league tells the story the real store will tell. Rounds 1 and 2 look like a workbook
import: box-score lines carry no fouls drawn, no blocks against, no plus/minus and no starter
flag (NULL), round 1 has no partials, venue or attendance, and the venue's neutrality is
unknown (NULL, never assumed False). Rounds 3 and 4 look like live ingest: every column is
filled, starters are marked, positions at game time are recorded. The ratings and per-40 rates
are "as of round 2", exactly where an import would leave them, so the model's round updates
have rounds 3 and 4 to work on. One game is played at a neutral site. A few players' five-way
workbook label and their three-way registration disagree, as the real ones do. Mixing the two
shapes is what lets the per-column divisor rule be tested: an average of fouls drawn divides by
the games that recorded them, never by all of them.

Honest by construction
----------------------
Every line passes the same invariants as a real one (:func:`~nbastats.euroleague.importers.
workbook.check_game`): points equal ``2*2PM + 3*3PM + FTM``, rebounds equal offensive plus
defensive, makes never exceed attempts, the players' points equal the team's equal the final
score, team time is 200 minutes plus 25 per overtime, the partials sum to the final, and the
PIR matches its formula where fouls drawn and blocks against are known. :func:`seed_demo`
checks every game before writing it and raises if one fails, so a bug in this generator cannot
silently put an impossible box score into a fixture. A DNP has every stat NULL. A zero-attempt
percentage is NULL.

Deterministic, and independent of everything else
-------------------------------------------------
Everything is drawn from ``random.Random(20261001)``, never from the NBA seeder's stream and
never from the clock, and every timestamp is fixed (:data:`DEMO_AS_OF`), so two runs produce the
same rows byte for byte. A fixture built from the demo can therefore be reproduced and diffed.

Safe to name
------------
Clubs and players are invented from syllables; the status links are on ``example.org``; the
country codes are the ISO user-assigned range (``XA`` to ``XD``) so no real country is implied.
All rows carry ``data_source = 'synthetic-demo'`` and the store is stamped ``synthetic``, which
refuses real writes (and the importer refuses this store), so invented and real rows can never
meet in one file. ``/v1/el/meta.isDemo`` is true.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Final

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..shared.availability import parse_expected_return
from .db import (
    bump_sync_version,
    ensure_identity,
    read_identity,
    read_sync_state,
    sync_mode_for_kind,
)
from .importers.workbook import GameFacts, Line, check_game
from .models import (
    ElBase,
    ElClub,
    ElClubAlias,
    ElClubSeason,
    ElGame,
    ElIntelBatch,
    ElIntelStatus,
    ElPerson,
    ElPlayerGame,
    ElPlayerRate,
    ElRegistration,
    ElSeason,
    ElTeamGame,
    ElTeamRating,
)
from .profile import (
    DATA_SOURCE_SYNTHETIC,
    DEFAULT_PHASE,
    KIND_SYNTHETIC,
    POSITION5_TO_CODE,
    PROFILE,
    official_game_id,
    provisional_game_id,
    season_ref_from_start_year,
)

__all__ = [
    "DEMO_SEED",
    "DEMO_AS_OF",
    "DEMO_SEASON_START_YEAR",
    "DEMO_ROUNDS_FINAL",
    "DEMO_ROUNDS_TOTAL",
    "DEMO_NEUTRAL_GAME_ID",
    "DemoClub",
    "DemoSummary",
    "demo_clubs",
    "seed_demo",
    "clear_store",
    "has_games",
]

DEMO_SEED: Final = 20261001
#: The moment the demo "is" (rounds 1-4 are behind it, round 5 ahead). Every timestamp in the
#: demo's rows is derived from this, never from the clock.
DEMO_AS_OF: Final = datetime(2026, 10, 12, 9, 0, 0)
DEMO_SEASON_START_YEAR: Final = 2026
DEMO_ROUNDS_FINAL: Final = 4
DEMO_ROUNDS_TOTAL: Final = 5
#: The one game played at a neutral site (round 3, the first game of the round).
DEMO_NEUTRAL_GAME_ID: Final = "E2026-0021"
#: The first day of each round, then the next day, in the competition's Berlin calendar.
_ROUND_DAYS: Final = (
    date(2026, 9, 24),
    date(2026, 9, 29),
    date(2026, 10, 1),
    date(2026, 10, 8),
    date(2026, 10, 15),
)
_GAMES_PER_ROUND: Final = 10
_ROSTER_MIN, _ROSTER_MAX = 14, 18
_DRESSED = 12

_CLUB_NAMES: Final[tuple[tuple[str, str], ...]] = (
    ("Alderwick Herons", "Alderwick"),
    ("Brindlemoor Stags", "Brindlemoor"),
    ("Cindervale Foxes", "Cindervale"),
    ("Dunmarrow Wolves", "Dunmarrow"),
    ("Eastmere Falcons", "Eastmere"),
    ("Fernhollow Otters", "Fernhollow"),
    ("Glimmerton Lynx", "Glimmerton"),
    ("Harrowfield Bears", "Harrowfield"),
    ("Ivorymead Hawks", "Ivorymead"),
    ("Juniper Falls Badgers", "Juniper Falls"),
    ("Kestrelburn Owls", "Kestrelburn"),
    ("Larkspur Rams", "Larkspur"),
    ("Mistral Point Gulls", "Mistral Point"),
    ("Northwick Bison", "Northwick"),
    ("Oakhaven Elks", "Oakhaven"),
    ("Pinecrest Panthers", "Pinecrest"),
    ("Quillon Ravens", "Quillon"),
    ("Redmarsh Pikes", "Redmarsh"),
    ("Silverlake Orcas", "Silverlake"),
    ("Thornfield Tigers", "Thornfield"),
)
_COUNTRIES: Final = ("XA", "XB", "XC", "XD")
_GIVEN: Final = (
    "Tamsin",
    "Corvin",
    "Ladric",
    "Mavren",
    "Odrin",
    "Pellam",
    "Quenby",
    "Rosk",
    "Sevrin",
    "Talbot",
    "Ulric",
    "Varek",
    "Wendric",
    "Xanil",
    "Yorrick",
    "Zeline",
    "Arlen",
    "Bastian",
    "Cadrik",
    "Dorian",
    "Elric",
    "Fenwick",
    "Garrow",
    "Hollis",
    "Ivar",
    "Jessamy",
    "Kendrick",
    "Lorcan",
    "Marek",
    "Nevin",
    "Oswin",
    "Perrin",
    "Quillan",
    "Rafe",
    "Sorrel",
    "Tavish",
    "Ugo",
    "Vance",
    "Wilder",
    "Yaric",
    "Zeph",
    "Aldous",
    "Brannoch",
    "Calder",
    "Dacian",
    "Emrys",
    "Faolan",
    "Gideon",
    "Hadric",
    "Isidor",
    "Joran",
    "Kasimir",
    "Leopold",
    "Marlow",
    "Nikolai",
    "Orrin",
    "Phelan",
    "Rurik",
    "Stellan",
    "Torvald",
)
_FAMILY: Final = (
    "Ashgrove",
    "Brakenridge",
    "Corwen",
    "Dellmar",
    "Evenwood",
    "Fallowmere",
    "Garnet",
    "Hallowby",
    "Ingleby",
    "Jorvik",
    "Kelderan",
    "Lindqvist",
    "Marwood",
    "Nethersby",
    "Oldacre",
    "Pellworth",
    "Quartermane",
    "Rookwood",
    "Stillwater",
    "Thackeray",
    "Underhill",
    "Valdemar",
    "Wexcombe",
    "Yarrowby",
    "Zelkova",
    "Aldenmoor",
    "Bexley-Ford",
    "Carrowmore",
    "Drummond-Lee",
    "Eskridge",
    "Fairbourne",
    "Grimsdale",
    "Hartigan",
    "Ironsby",
    "Jessop",
    "Kirkwall",
    "Lockridge",
    "Moorcroft",
    "Northrop",
    "Ormerod",
    "Penhallow",
    "Radcliff",
    "Saltmarsh",
    "Tarrant",
    "Ullswater",
    "Vickery",
    "Whitlock",
    "Yeoman",
    "Abernethy",
    "Blackmere",
    "Cranwell",
    "Dunmore",
    "Ellerby",
    "Foxcroft",
    "Gatehouse",
    "Holloway",
    "Inverlay",
    "Jarrow",
    "Kingsmere",
    "Lavender",
    "Mossop",
    "Netherfield",
    "Oakeshott",
    "Pargeter",
    "Quickfall",
    "Ravenscar",
    "Sheldrake",
    "Thornbury",
    "Upcott",
    "Vansittart",
    "Wetherby",
    "Yardley",
    "Zouche",
    "Ambrose",
    "Beaumont",
    "Calloway",
    "Dacre",
    "Emsworth",
    "Fenimore",
    "Goodricke",
    "Hawkridge",
    "Isherwood",
)

# (two-point weight, three-point weight, free-throw weight, rebound, assist, steal, block, turnover)
_POS_WEIGHTS: Final[dict[str, tuple[float, float, float, float, float, float, float, float]]] = {
    "PG": (0.8, 1.1, 0.9, 0.5, 1.8, 1.3, 0.2, 1.4),
    "SG": (1.0, 1.4, 0.9, 0.6, 1.1, 1.2, 0.3, 1.1),
    "SF": (1.0, 1.0, 1.0, 0.9, 0.8, 1.0, 0.6, 0.9),
    "PF": (1.1, 0.6, 1.0, 1.3, 0.6, 0.8, 1.0, 0.8),
    "C": (1.3, 0.05, 1.1, 1.8, 0.5, 0.5, 2.0, 0.9),
}
_START_POSITIONS: Final = ("PG", "SG", "SF", "PF", "C")
_BENCH_POSITIONS: Final = (
    "PG",
    "SG",
    "SG",
    "SF",
    "PF",
    "PF",
    "C",
    "C",
    "SF",
    "SG",
    "PF",
    "C",
    "PG",
)


@dataclass(frozen=True)
class DemoClub:
    code: str
    name: str
    short_name: str
    country_code: str
    venue: str
    coach: str


@dataclass
class DemoSummary:
    """What :func:`seed_demo` did (or found already in place)."""

    seeded: bool
    clubs: int = 0
    persons: int = 0
    games_final: int = 0
    games_scheduled: int = 0
    player_lines: int = 0
    statuses: int = 0
    data_through: date | None = None
    sync_version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "seeded": self.seeded,
            "clubs": self.clubs,
            "persons": self.persons,
            "gamesFinal": self.games_final,
            "gamesScheduled": self.games_scheduled,
            "playerLines": self.player_lines,
            "statuses": self.statuses,
            "dataThrough": self.data_through.isoformat() if self.data_through else None,
            "syncVersion": self.sync_version,
        }


@dataclass
class _Player:
    code: str
    name: str
    club: str
    pos5: str
    pos_code: int
    registered_code: int
    age: int
    role: str
    dorsal: str
    skill: float
    proj_minutes: float
    rates: dict[str, float | None]
    established: bool


@dataclass
class _Club:
    spec: DemoClub
    attack: float
    defence: float
    players: list[_Player] = field(default_factory=list)


def demo_clubs() -> tuple[DemoClub, ...]:
    """The twenty invented clubs (deterministic; independent of the database)."""
    rng = random.Random(DEMO_SEED)
    coaches = _names(rng, 20)
    clubs = []
    for index, (name, short) in enumerate(_CLUB_NAMES):
        clubs.append(
            DemoClub(
                code="ZZ" + chr(ord("A") + index),
                name=name,
                short_name=short,
                country_code=_COUNTRIES[index % len(_COUNTRIES)],
                venue=f"{short} Hall",
                coach=coaches[index],
            )
        )
    return tuple(clubs)


def _names(rng: random.Random, count: int, taken: set[str] | None = None) -> list[str]:
    taken = taken if taken is not None else set()
    out: list[str] = []
    while len(out) < count:
        name = f"{rng.choice(_GIVEN)} {rng.choice(_FAMILY)}"
        if name not in taken:
            taken.add(name)
            out.append(name)
    return out


# --------------------------------------------------------------------------- squads


def _build_clubs(rng: random.Random) -> list[_Club]:
    specs = demo_clubs()
    # draw the coaches' names first so the squads' names never collide with them
    taken: set[str] = set()
    clubs: list[_Club] = []
    counter = 0
    for spec in specs:
        taken.add(spec.coach)
    for spec in specs:
        club = _Club(spec=spec, attack=rng.gauss(0.0, 2.6), defence=rng.gauss(0.0, 2.6))
        size = rng.randint(_ROSTER_MIN, _ROSTER_MAX)
        positions = list(_START_POSITIONS) + [rng.choice(_BENCH_POSITIONS) for _ in range(size - 5)]
        names = _names(rng, size, taken)
        base = [29, 27, 25, 23, 21, 18, 16, 14, 12, 10, 6, 4, 3, 2, 2, 1, 1, 1]
        minutes = base[:size]
        scale = 200.0 / sum(minutes[:10])
        numbers = rng.sample(range(0, 100), size)
        for rank in range(size):
            counter += 1
            pos5 = positions[rank]
            code = POSITION5_TO_CODE[pos5]
            registered = code
            if rng.random() < 0.12:  # the workbook's label and the registration can disagree
                registered = min(3, max(1, code + rng.choice((-1, 1))))
            established = rank < size - 3
            skill = max(0.35, 1.5 - rank * 0.09 + rng.uniform(-0.12, 0.12))
            club.players.append(
                _Player(
                    code=f"demo-{counter}",
                    name=names[rank],
                    club=spec.code,
                    pos5=pos5,
                    pos_code=code,
                    registered_code=registered,
                    age=rng.randint(19, 37),
                    role=(
                        "Star"
                        if rank == 0
                        else "Starter" if rank < 5 else "Rotation" if rank < 10 else "Bench"
                    ),
                    dorsal=str(numbers[rank]),
                    skill=skill,
                    proj_minutes=round(minutes[rank] * (scale if rank < 10 else 1.0), 1),
                    rates=_rates(rng, pos5, skill),
                    established=established,
                )
            )
        clubs.append(club)
    return clubs


def _rates(rng: random.Random, pos5: str, skill: float) -> dict[str, float | None]:
    w2, w3, wft, wreb, wast, wstl, wblk, wtov = _POS_WEIGHTS[pos5]
    fga2 = round(rng.uniform(5, 11) * w2 * skill, 2)
    fga3 = round(rng.uniform(2, 8) * w3 * skill, 2) if w3 > 0.1 else 0.0
    fta = round(rng.uniform(2, 5) * wft * skill, 2)
    fg2 = round(rng.uniform(0.46, 0.62), 3)
    fg3 = round(rng.uniform(0.30, 0.41), 3)
    ft = round(rng.uniform(0.62, 0.90), 3)
    fg3m = round(fga3 * fg3, 2)
    pts = round(2 * fga2 * fg2 + 3 * fg3m + fta * ft, 2)
    return {
        "pts40": pts,
        "reb40": round(rng.uniform(3, 7) * wreb * 1.2, 2),
        "ast40": round(rng.uniform(1.5, 4.5) * wast, 2),
        "fg3m40": fg3m,
        "stl40": round(rng.uniform(0.6, 1.5) * wstl, 2),
        "blk40": round(rng.uniform(0.1, 0.8) * wblk, 2),
        "tov40": round(rng.uniform(1.4, 3.0) * wtov, 2),
        "fga2_40": fga2,
        "fga3_40": fga3,
        "fta40": fta,
        # A percentage with no attempts behind it is NULL, never a recorded zero.
        "fg2_pct": fg2 if fga2 > 0 else None,
        "fg3_pct": fg3 if fga3 > 0 else None,
        "ft_pct": ft if fta > 0 else None,
    }


# --------------------------------------------------------------------------- schedule


def _round_robin(codes: list[str], rounds: int) -> list[list[tuple[str, str]]]:
    """The circle method: ``rounds`` rounds of ``len(codes) / 2`` pairs, home first."""
    teams = list(codes)
    n = len(teams)
    out: list[list[tuple[str, str]]] = []
    for r in range(rounds):
        pairs = []
        for i in range(n // 2):
            a, b = teams[i], teams[n - 1 - i]
            pairs.append((a, b) if (r + i) % 2 == 0 else (b, a))
        out.append(pairs)
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]
    return out


def _split(rng: random.Random, total: int, parts: int) -> list[int]:
    """``total`` split into ``parts`` plausible period scores that sum to it exactly."""
    weights = [max(0.5, rng.gauss(1.0, 0.16)) for _ in range(parts)]
    scale = total / sum(weights)
    values = [max(6, round(w * scale)) for w in weights]
    values[-1] += total - sum(values)
    if values[-1] < 6:  # rebalance from the largest period
        deficit = 6 - values[-1]
        values[values.index(max(values))] -= deficit
        values[-1] += deficit
    return values


def _scores(rng: random.Random, home: _Club, away: _Club) -> tuple[int, int, int, int | None]:
    """``(home, away, overtime periods, regulation score)`` for one game.

    A ratings-driven expectation plus noise. A tie, and a small fraction of other games, go to
    overtime: the sides are level after regulation at ``regulation`` points each, and one
    overtime period decides it. The last element is ``None`` when there was no overtime.
    """
    mean = PROFILE.home_advantage_default
    expected_home = 85.0 + home.attack + away.defence + mean / 2
    expected_away = 85.0 + away.attack + home.defence - mean / 2
    h = int(round(min(112, max(58, rng.gauss(expected_home, 8.5)))))
    a = int(round(min(112, max(58, rng.gauss(expected_away, 8.5)))))
    if h == a or rng.random() < 0.06:
        regulation = min(h, a) - 6 if h != a else h
        ot_home = rng.randint(7, 15)
        ot_away = ot_home + rng.choice((-3, -2, -1, 1, 2, 3))
        return regulation + ot_home, regulation + ot_away, 1, regulation
    return h, a, 0, None


# --------------------------------------------------------------------------- lines


def _allocate(
    rng: random.Random, weights: list[float], units: int, cap: int | None = None
) -> list[int]:
    """Spread ``units`` among players by weight (each unit drawn independently)."""
    counts = [0] * len(weights)
    live = list(weights)
    for _ in range(units):
        if not any(w > 0 for w in live):
            break
        pick = rng.choices(range(len(live)), weights=live, k=1)[0]
        counts[pick] += 1
        if cap is not None and counts[pick] >= cap:
            live[pick] = 0.0
    return counts


def _seconds(rng: random.Random, weights: list[float], total: int) -> list[int]:
    raw = [w * rng.uniform(0.75, 1.25) for w in weights]
    scale = total / sum(raw)
    values = [max(45, int(r * scale)) for r in raw]
    values[values.index(max(values))] += total - sum(values)
    return values


def _team_lines(
    rng: random.Random,
    club: _Club,
    points: int,
    opponent_points: int,
    ot: int,
    detailed: bool,
) -> tuple[list[tuple[_Player, Line]], Line]:
    """One side's player lines and team totals for a game it scored ``points`` in.

    ``detailed`` games carry fouls drawn, blocks against, plus/minus, starters; the others do
    not (they are shaped like a workbook import). The hidden fouls-drawn and blocks-against
    values are still drawn for every game, so the PIR is the same quantity either way.
    """
    squad = club.players
    dressed = list(squad[:10])
    dressed += rng.sample(squad[10:], min(_DRESSED - 10, len(squad) - 10))
    n_dnp = rng.choice((0, 1, 1, 2))
    dnp_pool = [p for p in dressed if p.proj_minutes < 14]
    dnp = set(id(p) for p in rng.sample(dnp_pool, min(n_dnp, len(dnp_pool))))
    played = [p for p in dressed if id(p) not in dnp]
    team_seconds = PROFILE.team_regulation_seconds + ot * PROFILE.team_overtime_seconds
    seconds = _seconds(rng, [max(p.proj_minutes, 4.0) for p in played], team_seconds)

    # Points: choose the makes so that 2*fgm2 + 3*fgm3 + ftm equals the score exactly.
    fgm3 = round(points * rng.uniform(0.20, 0.32) / 3)
    ftm = round(points * rng.uniform(0.13, 0.21))
    if (points - 3 * fgm3 - ftm) % 2:
        ftm += 1 if ftm < points - 3 * fgm3 else -1
    fgm2 = (points - 3 * fgm3 - ftm) // 2
    weights = [(s / 60.0) * p.skill for p, s in zip(played, seconds)]

    def w(index: int) -> list[float]:
        """Each player's weight for one box-score category (position-shaped)."""
        return [wt * _POS_WEIGHTS[p.pos5][index] for wt, p in zip(weights, played)]

    made2 = _allocate(rng, w(0), fgm2)
    made3 = _allocate(rng, w(1), fgm3)
    madeft = _allocate(rng, w(2), ftm)
    oreb = _allocate(rng, w(3), rng.randint(7, 13))
    dreb = _allocate(rng, w(3), rng.randint(22, 30))
    ast = _allocate(rng, w(4), rng.randint(14, 22))
    stl = _allocate(rng, w(5), rng.randint(4, 9))
    blk = _allocate(rng, w(6), rng.randint(1, 5))
    tov = _allocate(rng, w(7), rng.randint(9, 15))
    pf = _allocate(rng, [s / 60.0 for s in seconds], rng.randint(16, 24), cap=5)
    ba = _allocate(rng, [wt * 0.5 + 0.2 for wt in weights], rng.randint(1, 5))
    fd = _allocate(rng, [wt * 0.8 + 0.3 for wt in weights], rng.randint(14, 22))

    starters = set(sorted(range(len(played)), key=lambda i: -seconds[i])[:5])
    margin = points - opponent_points
    lines: list[tuple[_Player, Line]] = []
    for i, player in enumerate(played):
        miss2 = round(made2[i] * rng.uniform(0.7, 1.3))
        miss3 = round(made3[i] * rng.uniform(1.4, 2.4)) + (
            rng.randint(0, 1) if player.pos5 != "C" and seconds[i] > 600 else 0
        )
        missft = round(madeft[i] * rng.uniform(0.15, 0.45))
        line = Line(
            side="",  # filled by the caller
            name=player.name,
            participation="played",
            seconds=seconds[i],
            pts=2 * made2[i] + 3 * made3[i] + madeft[i],
            fgm2=made2[i],
            fga2=made2[i] + miss2,
            fgm3=made3[i],
            fga3=made3[i] + miss3,
            ftm=madeft[i],
            fta=madeft[i] + missft,
            oreb=oreb[i],
            dreb=dreb[i],
            reb=oreb[i] + dreb[i],
            ast=ast[i],
            stl=stl[i],
            tov=tov[i],
            blk=blk[i],
            pf=pf[i],
        )
        line.pir_official = _pir(line, fd[i], ba[i])
        if detailed:
            line.is_starter = i in starters
            line.fouls_drawn = fd[i]
            line.blk_against = ba[i]
            line.plus_minus = round(margin * seconds[i] / 2400 + rng.gauss(0, 3))
        lines.append((player, line))
    dnp_lines = [
        (p, Line(side="", name=p.name, participation="dnp")) for p in dressed if id(p) in dnp
    ]

    # Team rebounds and turnovers are not credited to any player, so a team's totals can
    # exceed the sum of its players' (the soft invariant allows exactly that).
    team = Line(side="", name=club.spec.name, seconds=team_seconds, pts=points)
    for name in ("fgm2", "fga2", "fgm3", "fga3", "ftm", "fta", "ast", "stl", "blk", "pf"):
        setattr(team, name, sum(getattr(line, name) for _, line in lines))
    team.oreb = sum(line.oreb for _, line in lines) + rng.randint(0, 2)
    team.dreb = sum(line.dreb for _, line in lines) + rng.randint(0, 3)
    team.reb = team.oreb + team.dreb
    team.tov = sum(line.tov for _, line in lines) + rng.randint(0, 2)
    team.pir_official = (
        sum(line.pir_official for _, line in lines)
        + (team.reb - sum(line.reb for _, line in lines))
        - (team.tov - sum(line.tov for _, line in lines))
    )
    if detailed:
        team.fouls_drawn = sum(fd)
        team.blk_against = sum(ba)
        team.plus_minus = margin
    return lines + dnp_lines, team


def _pir(line: Line, fouls_drawn: int, blocks_against: int) -> int:
    return (
        line.pts
        + line.reb
        + line.ast
        + line.stl
        + line.blk
        + fouls_drawn  # type: ignore[operator]
        - (line.fga2 - line.fgm2)
        - (line.fga3 - line.fgm3)
        - (line.fta - line.ftm)  # type: ignore[operator]
        - line.tov
        - blocks_against
        - line.pf  # type: ignore[operator]
    )


# --------------------------------------------------------------------------- store


_DATA_TABLES = tuple(
    table for table in reversed(ElBase.metadata.sorted_tables) if table.name != "el_store_identity"
)


def has_games(session: Session) -> bool:
    """True when the store already holds at least one game."""
    return bool(session.execute(select(func.count()).select_from(ElGame)).scalar_one())


def clear_store(session: Session) -> None:
    """Delete every data row of a *synthetic* store (never a real one). The identity stays."""
    identity = read_identity(session)
    if identity is not None and identity.kind != KIND_SYNTHETIC:
        raise ValueError("refusing to clear a store that holds real data")
    for table in _DATA_TABLES:
        session.execute(delete(table))
    session.flush()


def seed_demo(
    session: Session, *, replace: bool = False, now: datetime | None = None
) -> DemoSummary:
    """Seed the invented league into a synthetic store. Does not commit.

    A store that already holds games is left alone and reported (``seeded`` is false), unless
    ``replace`` is set, which rebuilds it from scratch (synthetic stores only). A real store
    raises :class:`~nbastats.euroleague.db.StoreKindMismatch`.
    """
    ensure_identity(session, KIND_SYNTHETIC, now or DEMO_AS_OF)
    if has_games(session):
        if not replace:
            return _summary(session, seeded=False)
        clear_store(session)
    moment = now or DEMO_AS_OF
    # Independent streams from one seed: coaches, squads, results, statuses. Changing how one
    # is used can never move the others.
    rng = random.Random(DEMO_SEED + 3)
    clubs = _build_clubs(random.Random(DEMO_SEED + 1))
    by_code = {club.spec.code: club for club in clubs}
    season = season_ref_from_start_year(DEMO_SEASON_START_YEAR)
    session.add(
        ElSeason(
            season_code=season.code,
            competition_code=season.competition_code,
            label=season.label,
            start_year=season.start_year,
            is_current=True,
        )
    )

    for club in clubs:
        spec = club.spec
        session.add(
            ElClub(
                club_code=spec.code,
                tv_code=None,
                name=spec.name,
                short_name=spec.short_name,
                country_code=spec.country_code,
            )
        )
        for system in ("official", "workbook"):
            session.add(ElClubAlias(system=system, code=spec.code, club_code=spec.code))
        session.add(
            ElClubSeason(
                season_code=season.code,
                club_code=spec.code,
                coach_name=spec.coach,
                home_venue_name=spec.venue,
                home_venue_tz="Europe/Berlin",
            )
        )
        for player in club.players:
            session.add(ElPerson(person_code=player.code, code_system="demo", name=player.name))
            session.add(
                ElRegistration(
                    season_code=season.code,
                    club_code=spec.code,
                    person_code=player.code,
                    dorsal=player.dorsal,
                    position_code=player.registered_code,
                    position_name={1: "Guard", 2: "Forward", 3: "Center"}[player.registered_code],
                    position5_workbook=player.pos5,
                    role_workbook=player.role,
                    age_workbook=player.age,
                    active=True,
                    source=DATA_SOURCE_SYNTHETIC,
                )
            )
            session.add(
                ElPlayerRate(
                    season_code=season.code,
                    person_code=player.code,
                    as_of_round=2,
                    club_code=spec.code,
                    proj_minutes=player.proj_minutes,
                    basis="syntheticDemo",
                    prior_minutes=400.0 if player.established else 150.0,
                    **player.rates,
                )
            )
        session.add(
            ElTeamRating(
                season_code=season.code,
                club_code=spec.code,
                as_of_round=2,
                pf_prior=round(85.0 + club.attack, 2),
                pa_prior=round(85.0 + club.defence, 2),
                prior_is_estimate=spec.code == clubs[-1].spec.code,
                attack_adj=round(club.attack * 0.2, 2),
                defence_adj=round(club.defence * 0.2, 2),
                source="syntheticDemo",
                computed_at=moment,
            )
        )
    session.flush()

    rounds = _round_robin([c.spec.code for c in clubs], DEMO_ROUNDS_TOTAL)
    game_code = 0
    last_final: date | None = None
    for round_index, pairs in enumerate(rounds):
        round_number = round_index + 1
        first_day = _ROUND_DAYS[round_index]
        final = round_number <= DEMO_ROUNDS_FINAL
        for slot, (home_code, away_code) in enumerate(pairs):
            home, away = by_code[home_code], by_code[away_code]
            day = first_day + timedelta(days=0 if slot < 5 else 1)
            tip_hour = 17 + (slot % 5)  # UTC; the Berlin date is the same
            tipoff = datetime(day.year, day.month, day.day, tip_hour, 30 + (slot % 2) * 15)
            neutral = final and round_number == 3 and slot == 0
            venue = "Neutral Court Arena" if neutral else home.spec.venue
            if final:
                game_code += 1
                gid = official_game_id(season.code, game_code)
                h, a, ot, regulation = _scores(rng, home, away)
                detailed = round_number >= 3
                last_final = day
                if regulation is None:
                    partials_h, partials_a = _split(rng, h, 4), _split(rng, a, 4)
                else:  # level after four periods, decided in one overtime
                    partials_h = _split(rng, regulation, 4) + [h - regulation]
                    partials_a = _split(rng, regulation, 4) + [a - regulation]
                home_lines, home_team = _team_lines(rng, home, h, a, ot, detailed)
                away_lines, away_team = _team_lines(rng, away, a, h, ot, detailed)
                # Fouls drawn by one side are the other's personal fouls; blocks against one
                # side are the other's blocks. Rewrite the hidden draws to say so exactly.
                if detailed:
                    _tie_fouls(rng, home_lines, home_team, away_team)
                    _tie_fouls(rng, away_lines, away_team, home_team)
                for side, lines, team in (
                    ("home", home_lines, home_team),
                    ("away", away_lines, away_team),
                ):
                    for _, line in lines:
                        line.side = side
                    team.side = side
                facts = GameFacts(
                    final_home=h,
                    final_away=a,
                    players=[line for _, line in home_lines + away_lines],
                    teams={"home": home_team, "away": away_team},
                    home_partials=partials_h if round_number >= 2 else None,
                    away_partials=partials_a if round_number >= 2 else None,
                    seconds_tolerance=0,
                )
                result = check_game(facts)
                if result.hard:  # a bug in this generator must never reach a fixture
                    raise AssertionError(f"demo game {gid} breaks {[v.rule for v in result.hard]}")
                if round_number >= 3 and result.soft:
                    raise AssertionError(f"demo game {gid} breaks {[v.rule for v in result.soft]}")
                session.add(
                    ElGame(
                        game_id=gid,
                        season_code=season.code,
                        game_code=game_code,
                        round_number=round_number,
                        phase_code=DEFAULT_PHASE,
                        game_date=day,
                        tipoff_utc=tipoff if round_number >= 3 else None,
                        venue_name=venue if round_number >= 2 else None,
                        is_neutral=(neutral if round_number >= 2 else None),
                        home_club_code=home_code,
                        away_club_code=away_code,
                        status="final",
                        home_pts=h,
                        away_pts=a,
                        home_partials_json=json.dumps(partials_h) if round_number >= 2 else None,
                        away_partials_json=json.dumps(partials_a) if round_number >= 2 else None,
                        ot_periods=ot,
                        attendance=(rng.randint(3000, 14500) if round_number >= 2 else None),
                        stats_status="ok",
                        data_source=DATA_SOURCE_SYNTHETIC,
                        ingested_at=moment,
                    )
                )
                session.flush()
                for club, lines, team, is_home in (
                    (home, home_lines, home_team, True),
                    (away, away_lines, away_team, False),
                ):
                    opp = away if is_home else home
                    for player, line in lines:
                        played = line.participation == "played"
                        session.add(
                            ElPlayerGame(
                                game_id=gid,
                                person_code=player.code,
                                club_code=club.spec.code,
                                participation=line.participation,
                                is_starter=line.is_starter if played else None,
                                seconds_played=line.seconds if played else None,
                                pts=line.pts,
                                fgm2=line.fgm2,
                                fga2=line.fga2,
                                fgm3=line.fgm3,
                                fga3=line.fga3,
                                ftm=line.ftm,
                                fta=line.fta,
                                oreb=line.oreb,
                                dreb=line.dreb,
                                reb=line.reb,
                                ast=line.ast,
                                stl=line.stl,
                                tov=line.tov,
                                blk=line.blk,
                                blk_against=line.blk_against,
                                pf=line.pf,
                                fouls_drawn=line.fouls_drawn,
                                plus_minus=line.plus_minus,
                                pir_official=line.pir_official,
                                position_code_at_game=(
                                    player.registered_code if detailed else None
                                ),
                                dorsal=player.dorsal if detailed else None,
                            )
                        )
                    scored, allowed = (h, a) if is_home else (a, h)
                    session.add(
                        ElTeamGame(
                            game_id=gid,
                            club_code=club.spec.code,
                            is_home=is_home,
                            opp_club_code=opp.spec.code,
                            won=scored > allowed,
                            seconds_played=team.seconds,
                            pts=scored,
                            opp_pts=allowed,
                            fgm2=team.fgm2,
                            fga2=team.fga2,
                            fgm3=team.fgm3,
                            fga3=team.fga3,
                            ftm=team.ftm,
                            fta=team.fta,
                            oreb=team.oreb,
                            dreb=team.dreb,
                            reb=team.reb,
                            ast=team.ast,
                            stl=team.stl,
                            tov=team.tov,
                            blk=team.blk,
                            blk_against=team.blk_against,
                            pf=team.pf,
                            fouls_drawn=team.fouls_drawn,
                            plus_minus=team.plus_minus,
                            pir_official=team.pir_official,
                        )
                    )
            else:
                session.add(
                    ElGame(
                        game_id=provisional_game_id(season.code, round_number, slot + 1),
                        season_code=season.code,
                        game_code=None,
                        round_number=round_number,
                        phase_code=DEFAULT_PHASE,
                        game_date=day,
                        tipoff_utc=tipoff,
                        venue_name=venue,
                        is_neutral=False,
                        home_club_code=home_code,
                        away_club_code=away_code,
                        status="scheduled",
                        stats_status="none",
                        data_source=DATA_SOURCE_SYNTHETIC,
                        ingested_at=moment,
                    )
                )
    session.flush()

    statuses = _seed_statuses(session, random.Random(DEMO_SEED + 2), clubs, moment)
    bump_sync_version(session, last_final, sync_mode_for_kind(KIND_SYNTHETIC), moment)
    session.flush()
    summary = _summary(session, seeded=True)
    summary.statuses = statuses
    return summary


def _tie_fouls(
    rng: random.Random,
    lines: list[tuple[_Player, Line]],
    team: Line,
    opponent: Line,
) -> None:
    """Make this side's fouls drawn sum to the opponent's personal fouls, and its blocks
    against sum to the opponent's blocks, then recompute each PIR to match.

    Per-player values are redrawn by scoring weight, so the box score is internally exact.
    """
    played = [(p, ln) for p, ln in lines if ln.participation == "played"]
    weights = [float(ln.pts or 0) + 1.0 for _, ln in played]
    drawn = _allocate(rng, weights, opponent.pf or 0)
    against = _allocate(rng, [wt * 0.5 + 0.2 for wt in weights], opponent.blk or 0)
    for (_, ln), fd, ba in zip(played, drawn, against):
        ln.fouls_drawn, ln.blk_against = fd, ba
        ln.pir_official = _pir(ln, fd, ba)
    team.fouls_drawn = sum(drawn)
    team.blk_against = sum(against)
    team.pir_official = (
        sum(ln.pir_official for _, ln in played)
        + ((team.reb or 0) - sum(ln.reb or 0 for _, ln in played))
        - ((team.tov or 0) - sum(ln.tov or 0 for _, ln in played))
    )


# --------------------------------------------------------------------------- statuses

_STATUS_PLAN: Final[tuple[tuple[str, str, str | None, str | None], ...]] = (
    ("out", "injury", "Rounds 4-5", "pressArticle"),
    ("out", "injury", "Long-term", "clubStatement"),
    ("out", "suspension", "Round 5", "clubStatement"),
    ("out", "injury", "Around 20 Oct", "pressArticle"),
    ("out", "personal", None, "manual"),
    ("doubtful", "injury", "Rounds 5-6", "pressArticle"),
    ("doubtful", "illness", "Uncertain", "clubStatement"),
    ("questionable", "injury", "Round 5", "pressArticle"),
    ("questionable", "injury", "Game-time decision", "pressArticle"),
    ("questionable", "illness", None, "clubStatement"),
    ("probable", "injury", "Expected back", "pressArticle"),
    ("probable", "rest", None, "manual"),
    ("available", "coachDecision", "Squad choice", "boxScoreInference"),
    ("available", "injury", "Playing", "pressArticle"),
)


def _seed_statuses(
    session: Session, rng: random.Random, clubs: list[_Club], moment: datetime
) -> int:
    batch = ElIntelBatch(
        source_kind="manual",
        label="Demo availability",
        file_sha256=None,
        imported_at=moment,
        row_count=0,
    )
    session.add(batch)
    session.flush()
    next_games: dict[str, str] = {}
    for gid, home_code, away_code in session.execute(
        select(ElGame.game_id, ElGame.home_club_code, ElGame.away_club_code).where(
            ElGame.status == "scheduled"
        )
    ):
        next_games[home_code] = gid
        next_games[away_code] = gid
    count = 0
    for index in range(30):
        club = clubs[index % len(clubs)] if index < 28 else rng.choice(clubs)
        status, reason, expected, kind = _STATUS_PLAN[index % len(_STATUS_PLAN)]
        player = club.players[rng.randrange(0, min(len(club.players), 12))]
        published = moment - timedelta(days=rng.randint(1, 12), hours=rng.randint(0, 20))
        if kind == "manual":
            url, label = None, "Entered by hand"
        elif kind == "clubStatement":
            url, label = (
                f"https://clubs.example.org/{club.spec.code.lower()}/statement-{index}",
                f"{club.spec.short_name} statement",
            )
        elif kind == "boxScoreInference":
            url, label = f"https://stats.example.org/box/{index}", "Box score"
        else:
            url, label = f"https://news.example.org/injuries/{index}", "example.org news"
        parsed = parse_expected_return(expected, source_date=published.date())
        person: str | None = player.code
        name = player.name
        if index == 29:  # one entry whose name matches nobody: the review queue's case
            person, name = None, "Unlisted Example Player"
        game_id = next_games.get(club.spec.code) if index % 3 == 0 else None
        session.add(
            ElIntelStatus(
                club_code=club.spec.code,
                person_code=person,
                player_name_raw=name,
                game_id=game_id,
                status=status,
                status_raw=status.upper(),
                model_status=None,
                reason_category=reason,
                reason_text=f"Invented note {index}",
                expected_return_text=expected,
                expected_return_round_from=parsed.round_from,
                expected_return_round_to=parsed.round_to,
                expected_return_date=parsed.date,
                source_kind=kind,
                source_label=label,
                source_url=url,
                source_published_at=published,
                as_of=moment,
                recorded_at=moment,
                batch_id=batch.batch_id,
                provenance_note=f"Demo row {index}",
            )
        )
        count += 1
    batch.row_count = count
    session.flush()
    return count


def _summary(session: Session, *, seeded: bool) -> DemoSummary:
    games = dict(session.execute(select(ElGame.status, func.count()).group_by(ElGame.status)).all())
    state = read_sync_state(session)
    return DemoSummary(
        seeded=seeded,
        clubs=session.execute(select(func.count()).select_from(ElClub)).scalar_one(),
        persons=session.execute(select(func.count()).select_from(ElPerson)).scalar_one(),
        games_final=games.get("final", 0),
        games_scheduled=games.get("scheduled", 0),
        player_lines=session.execute(select(func.count()).select_from(ElPlayerGame)).scalar_one(),
        statuses=session.execute(select(func.count()).select_from(ElIntelStatus)).scalar_one(),
        data_through=state.data_through,
        sync_version=state.sync_version,
    )
