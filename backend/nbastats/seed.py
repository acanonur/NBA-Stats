"""Deterministic synthetic-league generator: the whole app runs with zero network access.

Everything here comes out of ``random.Random(20260912)``, so two runs of the same seed
produce byte-identical data and tests can assert exact reconciliation.

What it builds
--------------
* the 30 real franchises, read from ``data/nba_identities.json`` — real NBA.com team ids,
  abbreviations, cities and names — with conference and division supplied by
  :data:`TEAM_ALIGNMENT`, the two facts that file does not carry
* a pool of players wearing **real NBA names, person ids and headshots**, drawn from the
  same file, each given an archetype (rim-running big, 3-and-D wing, high-usage guard,
  stretch four, bench playmaker) that produces statistically coherent lines

* a full schedule per season, with final scores driven by team strength and home court
* per-game box scores in which **every team total is the sum of its players' lines and the
  team's points equal the final score** — that identity is asserted in the tests
* season aggregates, team seasons, league distribution rows and shot zones

The honesty boundary
--------------------
The identity file states who exists, and nothing else: no rosters, no positions, no
measurements, no statistics — rosters change constantly, so asserting one would be
fabrication. This module therefore borrows names, ids and photos, and **generates everything
else**: the team a player appears on, his position, his physical profile, his career years
and every number in every box score. Two things keep that visible rather than implied —
every row carries ``data_source = "synthetic-demo"``, and ``/v1/meta`` serves
``DEMO_ATTRIBUTION`` from :mod:`nbastats.api.routes_meta`, which says so in a sentence.

Era pools are kept apart for the same reason: seasons from :data:`ACTIVE_IDENTITY_FROM` draw
on today's players, earlier ones on retired players, so no 1985-86 roster can star Victor
Wembanyama. Without the file (or once a pool is exhausted) the generator falls back to
invented names and no headshot, and reports the count in its summary.

Era honesty
-----------
The generator does not decide era rules for itself: it computes every value and then nulls
whatever ``contracts/metrics.json`` says did not exist, via
:func:`nbastats.catalog.metric_availability`. So

* seasons before 1996-97 get **no** ``player_game_advanced`` rows at all, and no per-game
  ratings, plus/minus, pace or possessions anywhere;
* their season-level advanced values (USG%, AST%, rebound rates, PER, WS, BPM, VORP) are
  written with ``is_estimated = True`` — they are box-score derivations, not measurements;
* nothing era-limited is ever written as ``0``.

Where the demo data is deliberately simpler than the league: players do not change teams
mid-season (``player_season`` still keys on ``team_id`` so a traded player is representable),
every game is 240 team-minutes with no overtime, and historical franchises carry their
present-day names.

CLI::

    python3 -m nbastats.seed --db sqlite:///./hardwood.db
    python3 -m nbastats.seed --db sqlite:///./demo.db --seasons 2024-25,2025-26 --as-of 2026-01-02
"""
from __future__ import annotations

import argparse
import math
import random
import statistics
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any, Sequence

from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from . import catalog, identities
from .db import create_db_engine, init_db, utcnow
from .models import (
    SHOT_ZONES,
    Base,
    Game,
    IdCrosswalk,
    IngestLog,
    LeagueSeason,
    Player,
    PlayerGameAdvanced,
    PlayerGameBasic,
    PlayerSeason,
    PLAYER_GAME_ADVANCED_METRIC_COLUMNS,
    PLAYER_GAME_METRIC_COLUMNS,
    PLAYER_SEASON_PER_GAME_COLUMNS,
    PLAYER_SEASON_RATE_COLUMNS,
    PLAYER_SEASON_TOTALS_COLUMNS,
    ShotZoneSeason,
    SyncState,
    Team,
    TeamGame,
    TeamSeason,
    TEAM_GAME_METRIC_COLUMNS,
    TEAM_SEASON_PER_GAME_COLUMNS,
    TEAM_SEASON_RATE_COLUMNS,
    TEAM_SEASON_TOTALS_COLUMNS,
)

__all__ = [
    "SEED",
    "DATA_SOURCE",
    "ACTIVE_IDENTITY_FROM",
    "DEFAULT_SEASONS",
    "DEFAULT_AS_OF",
    "NBA_TEAMS",
    "TEAM_ALIGNMENT",
    "ARCHETYPES",
    "seed_database",
    "main",
]

SEED = 20260912

#: Stamped on every row this module writes, in the ``data_source`` column every fact table
#: carries. Nothing seeded here was observed, so nothing seeded here may look ingested: a
#: row saying ``synthetic-demo`` cannot be mistaken for one saying ``nba-live`` or ``bbref``.
DATA_SOURCE = "synthetic-demo"

#: Player ids for players the identity file could not name (it is missing, or its pool ran
#: dry). Real NBA person ids are well under two million, so this block can never collide.
SYNTHETIC_PLAYER_ID_BASE = 9_000_001

#: Seasons from here on draw their names from the *active* identity list; earlier ones draw
#: from the historical (inactive) list. A 1985-86 season starring Victor Wembanyama would be
#: absurd, and the identity file carries no career years to be cleverer than this with.
ACTIVE_IDENTITY_FROM = 2020

DEFAULT_SEASONS: tuple[str, ...] = ("1985-86", "1992-93", "2005-06", "2024-25", "2025-26")

#: Mid-season "today" for the in-progress season, matching the contract's worked examples.
DEFAULT_AS_OF = date(2026, 1, 2)

#: First season with league-wide play-by-play: advanced box scores, plus/minus, shot charts.
ADVANCED_FROM = 1996

TEAM_MINUTES = 240.0
HOME_EDGE = 2.6  # points per game, split between the two sides


# --------------------------------------------------------------------------- static data

#: Conference and division, the two franchise facts ``data/nba_identities.json`` does not
#: carry. Everything else about a team — id, abbreviation, city, nickname, founding year,
#: display name — comes from that file and is reconciled with this map by abbreviation.
TEAM_ALIGNMENT: dict[str, tuple[str, str]] = {
    "ATL": ("East", "Southeast"), "BOS": ("East", "Atlantic"), "BKN": ("East", "Atlantic"),
    "CHA": ("East", "Southeast"), "CHI": ("East", "Central"), "CLE": ("East", "Central"),
    "DET": ("East", "Central"), "IND": ("East", "Central"), "MIA": ("East", "Southeast"),
    "MIL": ("East", "Central"), "NYK": ("East", "Atlantic"), "ORL": ("East", "Southeast"),
    "PHI": ("East", "Atlantic"), "TOR": ("East", "Atlantic"), "WAS": ("East", "Southeast"),
    "DAL": ("West", "Southwest"), "DEN": ("West", "Northwest"), "GSW": ("West", "Pacific"),
    "HOU": ("West", "Southwest"), "LAC": ("West", "Pacific"), "LAL": ("West", "Pacific"),
    "MEM": ("West", "Southwest"), "MIN": ("West", "Northwest"), "NOP": ("West", "Southwest"),
    "OKC": ("West", "Northwest"), "PHX": ("West", "Pacific"), "POR": ("West", "Northwest"),
    "SAC": ("West", "Pacific"), "SAS": ("West", "Southwest"), "UTA": ("West", "Northwest"),
}

# (team_id, abbr, city, nickname, conference, division, year_founded, name)
#
# Used only when the identity file cannot be read: the demo must still stand up without it.
_FALLBACK_TEAMS: tuple[tuple[int, str, str, str, str, str, int, str], ...] = (
    (1610612737, "ATL", "Atlanta", "Hawks", "East", "Southeast", 1949, "Atlanta Hawks"),
    (1610612738, "BOS", "Boston", "Celtics", "East", "Atlantic", 1946, "Boston Celtics"),
    (1610612739, "CLE", "Cleveland", "Cavaliers", "East", "Central", 1970,
     "Cleveland Cavaliers"),
    (1610612740, "NOP", "New Orleans", "Pelicans", "West", "Southwest", 2002,
     "New Orleans Pelicans"),
    (1610612741, "CHI", "Chicago", "Bulls", "East", "Central", 1966, "Chicago Bulls"),
    (1610612742, "DAL", "Dallas", "Mavericks", "West", "Southwest", 1980,
     "Dallas Mavericks"),
    (1610612743, "DEN", "Denver", "Nuggets", "West", "Northwest", 1976, "Denver Nuggets"),
    (1610612744, "GSW", "San Francisco", "Warriors", "West", "Pacific", 1946,
     "Golden State Warriors"),
    (1610612745, "HOU", "Houston", "Rockets", "West", "Southwest", 1967, "Houston Rockets"),
    (1610612746, "LAC", "Los Angeles", "Clippers", "West", "Pacific", 1970,
     "Los Angeles Clippers"),
    (1610612747, "LAL", "Los Angeles", "Lakers", "West", "Pacific", 1948,
     "Los Angeles Lakers"),
    (1610612748, "MIA", "Miami", "Heat", "East", "Southeast", 1988, "Miami Heat"),
    (1610612749, "MIL", "Milwaukee", "Bucks", "East", "Central", 1968, "Milwaukee Bucks"),
    (1610612750, "MIN", "Minnesota", "Timberwolves", "West", "Northwest", 1989,
     "Minnesota Timberwolves"),
    (1610612751, "BKN", "Brooklyn", "Nets", "East", "Atlantic", 1976, "Brooklyn Nets"),
    (1610612752, "NYK", "New York", "Knicks", "East", "Atlantic", 1946, "New York Knicks"),
    (1610612753, "ORL", "Orlando", "Magic", "East", "Southeast", 1989, "Orlando Magic"),
    (1610612754, "IND", "Indiana", "Pacers", "East", "Central", 1976, "Indiana Pacers"),
    (1610612755, "PHI", "Philadelphia", "76ers", "East", "Atlantic", 1949,
     "Philadelphia 76ers"),
    (1610612756, "PHX", "Phoenix", "Suns", "West", "Pacific", 1968, "Phoenix Suns"),
    (1610612757, "POR", "Portland", "Trail Blazers", "West", "Northwest", 1970,
     "Portland Trail Blazers"),
    (1610612758, "SAC", "Sacramento", "Kings", "West", "Pacific", 1948, "Sacramento Kings"),
    (1610612759, "SAS", "San Antonio", "Spurs", "West", "Southwest", 1976,
     "San Antonio Spurs"),
    (1610612760, "OKC", "Oklahoma City", "Thunder", "West", "Northwest", 1967,
     "Oklahoma City Thunder"),
    (1610612761, "TOR", "Toronto", "Raptors", "East", "Atlantic", 1995, "Toronto Raptors"),
    (1610612762, "UTA", "Utah", "Jazz", "West", "Northwest", 1974, "Utah Jazz"),
    (1610612763, "MEM", "Memphis", "Grizzlies", "West", "Southwest", 1995,
     "Memphis Grizzlies"),
    (1610612764, "WAS", "Washington", "Wizards", "East", "Southeast", 1961,
     "Washington Wizards"),
    (1610612765, "DET", "Detroit", "Pistons", "East", "Central", 1948, "Detroit Pistons"),
    (1610612766, "CHA", "Charlotte", "Hornets", "East", "Southeast", 1988,
     "Charlotte Hornets"),
)


def _team_table() -> tuple[tuple[int, str, str, str, str, str, int, str], ...]:
    """The 30 real franchises, read from the identity file and aligned by abbreviation.

    Anything short of all 30 matching falls back to the table above rather than seeding a
    half-league: the franchises are the one part of the demo that is entirely factual.
    """
    spares = {row[1]: row for row in _FALLBACK_TEAMS}
    rows: list[tuple[int, str, str, str, str, str, int, str]] = []
    for identity in identities.teams():
        alignment = TEAM_ALIGNMENT.get(identity.abbr)
        spare = spares.get(identity.abbr)
        if alignment is None or spare is None:
            continue
        conference, division = alignment
        rows.append(
            (
                identity.team_id,
                identity.abbr,
                identity.city or spare[2],
                identity.nickname or spare[3],
                conference,
                division,
                identity.year_founded if identity.year_founded is not None else spare[6],
                identity.name or spare[7],
            )
        )
    if len(rows) != len(TEAM_ALIGNMENT):
        return _FALLBACK_TEAMS
    return tuple(sorted(rows, key=lambda row: row[0]))


#: (team_id, abbr, city, nickname, conference, division, year_founded, name)
NBA_TEAMS: tuple[tuple[int, str, str, str, str, str, int, str], ...] = _team_table()

FIRST_NAMES = (
    "Marcus", "Andre", "Terrance", "Julian", "Dominic", "Isaiah", "Caleb", "Desmond", "Malik",
    "Trevor", "Quentin", "Bryce", "Elias", "Roman", "Xavier", "Damon", "Jalen", "Keon",
    "Nikolai", "Lorenzo", "Amari", "Cedric", "Devin", "Gabriel", "Hakim", "Ivan", "Jarrett",
    "Kirby", "Landon", "Micah", "Nolan", "Omar", "Preston", "Rashad", "Silas", "Tobias",
    "Ulises", "Vance", "Wesley", "Zane", "Aaron", "Brandt", "Colby", "Dante", "Emilio",
    "Franklin", "Grayson", "Harlan", "Ibrahim", "Jonah", "Kellan", "Leland", "Mateo", "Nash",
    "Oscar", "Pierce", "Quincy", "Reid", "Simeon", "Theo", "Uriel", "Victor", "Wyatt",
    "Yusuf", "Zeke", "Alonzo", "Barrett", "Chase", "Dorian", "Everett", "Felix", "Gideon",
    "Hollis", "Irving", "Jamir", "Kwame", "Lucas", "Milan", "Niko", "Otis", "Payton",
    "Rowan", "Sterling", "Tariq", "Vaughn", "Weston", "Zavier", "Amos", "Boris", "Corey",
    "Darius", "Enzo", "Fletcher", "Gustav", "Hector", "Ismael", "Jasper", "Kieran", "Lyle",
    "Marlon", "Neville", "Orlando", "Porter", "Rafael", "Solomon", "Tyrese", "Ugo", "Vincent",
)

LAST_NAMES = (
    "Ashworth", "Barros", "Caldwell", "Dunleavy", "Eastman", "Fontaine", "Grier", "Hollins",
    "Ingram", "Jessup", "Kowalski", "Lindqvist", "Marchetti", "Novak", "Okafor", "Pendleton",
    "Quintero", "Radcliffe", "Sorensen", "Tillman", "Ulrich", "Vasquez", "Whitfield", "Yates",
    "Zimmer", "Abernathy", "Blackwell", "Castellanos", "Delgado", "Ellington", "Fairbanks",
    "Gallagher", "Hammond", "Iverson", "Jankovic", "Kimura", "Langford", "Mbeki", "Nakamura",
    "Ortega", "Petrov", "Quillen", "Rasmussen", "Steadman", "Thibodeaux", "Underwood",
    "Valdez", "Wexler", "Yamamoto", "Zeller", "Amadou", "Bellamy", "Crowder", "Dubois",
    "Eriksen", "Falcone", "Godwin", "Hargrove", "Ivanov", "Jimenez", "Kaminski", "Lachance",
    "Mercer", "Nwosu", "Oyelaran", "Prieto", "Rankin", "Silvera", "Trevino", "Uzoma",
    "Vandermeer", "Wainwright", "Xiong", "Yarborough", "Zabala", "Alvarado", "Brannigan",
    "Cisse", "Drummond", "Escobar", "Fitzgerald", "Grubauer", "Halloran", "Ishikawa",
    "Jovanovic", "Keegan", "Lumley", "Matsuda", "Ndiaye", "Ostrowski", "Paquette", "Rios",
    "Sandoval", "Tanaka", "Urbina", "Villanueva", "Winslow", "Yeboah", "Zaragoza", "Boateng",
    "Chevalier", "Donnelly", "Ferreira", "Gruber", "Haddad", "Ionescu", "Juarez", "Kristof",
    "Lombardi", "Moretti", "Nystrom", "Oduya", "Pashkov", "Rivera", "Schuster", "Toussaint",
)

COUNTRIES = (
    "USA", "USA", "USA", "USA", "USA", "USA", "USA", "USA", "Canada", "France", "Spain",
    "Serbia", "Australia", "Nigeria", "Germany", "Lithuania", "Brazil", "Japan", "Greece",
)

SCHOOLS = (
    "Kentucky", "Duke", "Kansas", "UCLA", "North Carolina", "Michigan State", "Arizona",
    "Villanova", "Gonzaga", "Texas", "Florida", "Connecticut", "Louisville", "Syracuse",
    "Georgetown", "Memphis", "Indiana", "Oregon", "Baylor", "Auburn", "Marquette",
    "Overtime Elite", "G League Ignite", "Real Madrid", "Mega Basket", "ASVEL",
)


@dataclass(frozen=True, slots=True)
class Archetype:
    """Per-minute production rates, relative to an average rotation player (1.0)."""

    key: str
    label: str
    positions: tuple[str, ...]
    usage: float
    three_lean: float
    ft_lean: float
    fg2_skill: float
    fg3_skill: float
    ft_skill: float
    oreb: float
    dreb: float
    ast: float
    stl: float
    blk: float
    tov: float
    pf: float
    height_range: tuple[int, int]
    weight_range: tuple[int, int]
    # 2P attempt split (rim / paint non-rim / mid) and 3P split (corner / above the break).
    two_split: tuple[float, float, float]
    three_split: tuple[float, float]


ARCHETYPES: tuple[Archetype, ...] = (
    Archetype(
        "rim_runner", "Rim-running big", ("C", "F-C"),
        usage=0.86, three_lean=0.10, ft_lean=1.30,
        fg2_skill=1.24, fg3_skill=0.72, ft_skill=0.80,
        oreb=2.45, dreb=1.65, ast=0.55, stl=0.70, blk=2.90, tov=0.95, pf=1.35,
        height_range=(81, 86), weight_range=(235, 285),
        two_split=(0.68, 0.22, 0.10), three_split=(0.45, 0.55),
    ),
    Archetype(
        "three_and_d", "3-and-D wing", ("F", "G-F"),
        usage=0.70, three_lean=1.70, ft_lean=0.58,
        fg2_skill=0.99, fg3_skill=1.10, ft_skill=1.02,
        oreb=0.70, dreb=0.92, ast=0.60, stl=1.38, blk=0.92, tov=0.60, pf=1.08,
        height_range=(77, 81), weight_range=(195, 225),
        two_split=(0.55, 0.15, 0.30), three_split=(0.42, 0.58),
    ),
    Archetype(
        "high_usage_guard", "High-usage guard", ("G", "G-F"),
        usage=1.52, three_lean=1.16, ft_lean=1.12,
        fg2_skill=0.97, fg3_skill=1.00, ft_skill=1.10,
        oreb=0.34, dreb=0.74, ast=2.10, stl=1.22, blk=0.30, tov=1.62, pf=0.85,
        height_range=(72, 78), weight_range=(175, 205),
        two_split=(0.38, 0.22, 0.40), three_split=(0.18, 0.82),
    ),
    Archetype(
        "stretch_four", "Stretch four", ("F", "F-C"),
        usage=0.96, three_lean=1.46, ft_lean=0.80,
        fg2_skill=1.05, fg3_skill=1.05, ft_skill=1.00,
        oreb=1.12, dreb=1.48, ast=0.80, stl=0.92, blk=1.32, tov=0.86, pf=1.16,
        height_range=(79, 83), weight_range=(215, 250),
        two_split=(0.42, 0.24, 0.34), three_split=(0.35, 0.65),
    ),
    Archetype(
        "bench_playmaker", "Bench playmaker", ("G",),
        usage=0.94, three_lean=1.10, ft_lean=0.86,
        fg2_skill=0.95, fg3_skill=0.97, ft_skill=1.06,
        oreb=0.40, dreb=0.72, ast=1.80, stl=1.10, blk=0.34, tov=1.36, pf=1.00,
        height_range=(73, 78), weight_range=(180, 205),
        two_split=(0.40, 0.24, 0.36), three_split=(0.22, 0.78),
    ),
)

ARCHETYPE_WEIGHTS = (0.20, 0.26, 0.20, 0.18, 0.16)

#: League-average shot diet, used for team-level shot profiles.
TEAM_BLEND_ARCHETYPE = Archetype(
    "team", "Team", ("T",),
    usage=1.0, three_lean=1.0, ft_lean=1.0, fg2_skill=1.0, fg3_skill=1.0, ft_skill=1.0,
    oreb=1.0, dreb=1.0, ast=1.0, stl=1.0, blk=1.0, tov=1.0, pf=1.0,
    height_range=(79, 81), weight_range=(215, 225),
    two_split=(0.46, 0.21, 0.33), three_split=(0.27, 0.73),
)


@dataclass(frozen=True, slots=True)
class Era:
    """League-average shape of a season, per 100 possessions unless stated."""

    pace: float           # possessions per 48 minutes
    ortg: float           # points per 100 possessions
    fga_per_100: float
    fg3a_share: float     # 3PA / FGA
    ftr: float            # FTA / FGA
    fg3_pct: float
    ft_pct: float
    oreb_share: float     # OREB / (OREB + opponent DREB)
    ast_per_fgm: float
    stl_per_100: float
    blk_per_100: float
    tov_per_100: float
    pf_per_100: float
    rim_fg_pct: float
    paint_fg_pct: float
    mid_fg_pct: float
    corner_three_pct: float
    above_break_three_pct: float


# Anchored on the real league averages for each of these seasons: pace and points per 100
# set the scoring level, and the shooting shape then follows from the box-score identity
# 2*FGM + 3PM + FTM = PTS.
ERAS: dict[int, Era] = {
    1985: Era(102.1, 108.0, 86.9, 0.037, 0.358, 0.282, 0.756, 0.340, 0.600,
              8.1, 5.1, 16.7, 24.0, 0.590, 0.430, 0.420, 0.290, 0.278),
    1992: Era(96.8, 108.8, 86.6, 0.107, 0.360, 0.336, 0.754, 0.330, 0.610,
              8.7, 5.3, 16.3, 24.3, 0.605, 0.435, 0.415, 0.345, 0.332),
    2005: Era(90.5, 109.0, 88.0, 0.201, 0.339, 0.358, 0.745, 0.285, 0.585,
              7.7, 5.2, 15.7, 25.4, 0.620, 0.440, 0.410, 0.375, 0.352),
    2024: Era(99.0, 114.5, 89.4, 0.424, 0.243, 0.360, 0.782, 0.240, 0.620,
              7.9, 4.9, 13.9, 19.2, 0.655, 0.445, 0.415, 0.385, 0.355),
}

#: Share of missed shots that a player actually rebounds — the rest are team rebounds,
#: balls out of bounds and end-of-period misses, which no box-score line gets credit for.
REBOUNDABLE_SHARE = 0.89

#: Ratio of the league's counted possessions to the box-score estimate.
POSSESSION_CALIBRATION = 0.976

#: Converts a roster's talent surplus into net rating. Tuned so the league's net ratings
#: spread across roughly ±9, the way the real league's do.
STRENGTH_TO_NET_RATING = 100.0

#: First season of the play-in tournament. Earlier brackets take the top eight by record.
PLAY_IN_FROM = 2020


def era_for(year: int) -> Era:
    """League shape for a season, by its start year."""
    for boundary in sorted(ERAS, reverse=True):
        if year >= boundary:
            return ERAS[boundary]
    return ERAS[min(ERAS)]


# --------------------------------------------------------------------------- small helpers


def season_string(year: int) -> str:
    return f"{year}-{(year + 1) % 100:02d}"


def clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def safe_div(numerator: float, denominator: float) -> float | None:
    """Divide, or ``None`` when the denominator is empty — never a fabricated zero."""
    if not denominator:
        return None
    return numerator / denominator


def allocate(total: int, weights: Sequence[float], caps: Sequence[int] | None = None) -> list[int]:
    """Split ``total`` into integers proportional to ``weights``, never exceeding ``caps``.

    Largest-remainder apportionment, repeated while capped players push their surplus onto
    the others. The result always sums to ``total`` when the caps have room for it, which is
    what makes every team total equal the sum of its players' lines.
    """
    count = len(weights)
    if count == 0:
        return []
    if total <= 0:
        return [0] * count
    limits = [total] * count if caps is None else [max(0, int(c)) for c in caps]
    result = [0] * count
    remaining = total
    active = [i for i in range(count) if limits[i] > 0]

    for _ in range(8):
        if remaining <= 0 or not active:
            break
        weight_sum = sum(max(weights[i], 0.0) for i in active)
        if weight_sum <= 0:
            for i in active:
                if remaining <= 0:
                    break
                if limits[i] - result[i] > 0:
                    result[i] += 1
                    remaining -= 1
            active = [i for i in active if limits[i] - result[i] > 0]
            continue
        fractions: list[tuple[float, int]] = []
        assigned = 0
        for i in active:
            share = remaining * max(weights[i], 0.0) / weight_sum
            whole = min(int(share), limits[i] - result[i])
            result[i] += whole
            assigned += whole
            fractions.append((share - int(share), i))
        remaining -= assigned
        fractions.sort(reverse=True)
        for _fraction, i in fractions:
            if remaining <= 0:
                break
            if limits[i] - result[i] > 0:
                result[i] += 1
                remaining -= 1
        active = [i for i in active if limits[i] - result[i] > 0]
    return result


def _percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile over a sorted sequence."""
    if not values:
        raise ValueError("no values")
    if len(values) == 1:
        return float(values[0])
    position = q * (len(values) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(values[low])
    return float(values[low] + (values[high] - values[low]) * (position - low))


# Registry of metric-key → column mappings, so era rules are applied from the catalog
# rather than restated by hand in the generator.
_COLUMN_GROUPS: dict[str, dict[str, tuple[str, ...]]] = {}


def _register_group(name: str, *mappings: dict[str, str]) -> None:
    merged: dict[str, list[str]] = {}
    for mapping in mappings:
        for key, column in mapping.items():
            merged.setdefault(key, [])
            if column not in merged[key]:
                merged[key].append(column)
    _COLUMN_GROUPS[name] = {key: tuple(cols) for key, cols in merged.items()}


_register_group("player_game", PLAYER_GAME_METRIC_COLUMNS)
_register_group("player_game_advanced", PLAYER_GAME_ADVANCED_METRIC_COLUMNS)
_register_group("team_game", TEAM_GAME_METRIC_COLUMNS)
_register_group(
    "player_season",
    PLAYER_SEASON_PER_GAME_COLUMNS,
    PLAYER_SEASON_TOTALS_COLUMNS,
    PLAYER_SEASON_RATE_COLUMNS,
)
_register_group(
    "team_season",
    TEAM_SEASON_PER_GAME_COLUMNS,
    TEAM_SEASON_TOTALS_COLUMNS,
    TEAM_SEASON_RATE_COLUMNS,
)


@lru_cache(maxsize=256)
def _era_columns(group: str, season: str, granularity: str) -> tuple[frozenset[str], bool]:
    """Columns to null for this era, and whether anything left is merely estimated."""
    unavailable: set[str] = set()
    estimated = False
    for key, columns in _COLUMN_GROUPS[group].items():
        status = catalog.metric_availability(key, season, granularity)
        if status == "unavailable":
            unavailable.update(columns)
        elif status == "estimated":
            estimated = True
    return frozenset(unavailable), estimated


def apply_era_rules(row: dict[str, Any], group: str, season: str, granularity: str) -> bool:
    """Null every column the era did not record; return whether the row is estimated."""
    unavailable, estimated = _era_columns(group, season, granularity)
    for column in unavailable:
        if column in row:
            row[column] = None
    return estimated


# --------------------------------------------------------------------------- row buffering


@lru_cache(maxsize=32)
def _template(model: type[Base]) -> tuple[str, ...]:
    return tuple(column.key for column in model.__table__.columns)


#: Parent tables before child tables, so a store that enforces foreign keys (Postgres)
#: never sees a box-score row land before its game.
_INSERT_ORDER: tuple[str, ...] = tuple(t.name for t in Base.metadata.sorted_tables)


class _Writer:
    """Buffers rows per model and bulk-inserts them in chunks.

    Every buffered row carries the full column set so SQLAlchemy can use one executemany
    statement per chunk; a 100k-row seed is a few seconds rather than a few minutes. A full
    buffer flushes *every* buffer in dependency order rather than just its own, which keeps
    parents ahead of children.
    """

    def __init__(self, session: Session, chunk: int = 4000) -> None:
        self.session = session
        self.chunk = chunk
        self._buffers: dict[str, list[dict[str, Any]]] = {}
        self._models: dict[str, type[Base]] = {}
        self.counts: dict[str, int] = {}

    def add(self, model: type[Base], row: dict[str, Any]) -> None:
        name = model.__tablename__
        complete = {column: row.get(column) for column in _template(model)}
        buffer = self._buffers.setdefault(name, [])
        self._models[name] = model
        buffer.append(complete)
        self.counts[name] = self.counts.get(name, 0) + 1
        if len(buffer) >= self.chunk:
            self.flush()

    def flush(self) -> None:
        for name in _INSERT_ORDER:
            rows = self._buffers.get(name)
            if rows:
                self.session.execute(insert(self._models[name]), rows)
                rows.clear()


# --------------------------------------------------------------------------- league state


@dataclass(slots=True, eq=False)
class PlayerProfile:
    """One demo player: a real person's name, and an invented everything-else.

    ``player_id``, ``first_name``, ``last_name`` and ``headshot_url`` come from
    ``data/nba_identities.json`` — they are the real NBA person, and the only factual thing
    about this object. The archetype, the physical profile, the career years, the team and
    every number generated from them are synthetic, and ``identity_pool`` records which list
    the name was drawn from so a 1985-86 roster can never fill up with today's league.
    """

    player_id: int
    first_name: str
    last_name: str
    archetype: Archetype
    talent: float
    from_year: int
    to_year: int
    birth_year: int
    height_in: int
    weight: int
    country: str
    school: str
    draft_year: int
    draft_round: int | None
    draft_pick: int | None
    jersey: str
    bbref_slug: str
    headshot_url: str | None = None
    identity_pool: str = "invented"
    display_name: str | None = None

    @property
    def full_name(self) -> str:
        return self.display_name or f"{self.first_name} {self.last_name}"

    @property
    def position(self) -> str:
        return self.archetype.positions[0]

    def age_in(self, year: int) -> int:
        return year - self.birth_year


@dataclass(slots=True, eq=False)
class RosterSpot:
    """One player's role on one team for one season."""

    player: PlayerProfile
    minutes_share: float
    role_index: int
    form: float  # season-to-season variation on top of the age curve

    @property
    def rating(self) -> float:
        return self.player.talent * self.form


@dataclass(slots=True, eq=False)
class SeasonTeam:
    """A team's season: roster, strength and the running record used for seeding."""

    team_id: int
    abbr: str
    conference: str
    roster: list[RosterSpot]
    off_adj: float
    def_adj: float
    tempo_adj: float
    three_lean: float
    wins: int = 0
    losses: int = 0


def _age_curve(age: int) -> float:
    """Multiplier peaking at 27 — the shape that makes career arcs look like careers."""
    return clamp(1.0 - 0.0042 * (age - 27) ** 2, 0.55, 1.0)


class LeagueGenerator:
    """Builds one synthetic league and writes it through a :class:`_Writer`."""

    def __init__(
        self,
        session: Session,
        as_of: date,
        seasons: Sequence[str],
        games_per_team: int,
        players_per_team: int,
        include_playoffs: bool,
    ) -> None:
        self.session = session
        self.rng = random.Random(SEED)
        self.as_of = as_of
        self.seasons = sorted(seasons, key=catalog.season_sort_key)
        self.games_per_team = games_per_team
        self.players_per_team = players_per_team
        self.include_playoffs = include_playoffs
        self.writer = _Writer(session)

        self.teams = {row[0]: row for row in NBA_TEAMS}
        self.players: dict[int, PlayerProfile] = {}
        self._used_names: set[str] = set()
        self._used_slugs: set[str] = set()
        self._next_player_id = SYNTHETIC_PLAYER_ID_BASE
        self._game_sequence: dict[tuple[int, str], int] = {}

        # Real names to hand out, shuffled once by the seeded Random so the demo is not the
        # alphabetical head of the league and is still identical on every run. Two pools,
        # never mixed: today's players for modern seasons, retired ones for the eras.
        self._identity_pools: dict[str, list[identities.PlayerIdentity]] = {
            "active": self._shuffled(identities.active_players()),
            "historical": self._shuffled(identities.historical_players()),
        }
        #: How many players got a real name from each pool, and how many had to be invented.
        self.identity_counts: dict[str, int] = {"active": 0, "historical": 0, "invented": 0}

        # Persistent rosters across seasons, plus the pool of players between contracts.
        self._rosters: dict[int, list[PlayerProfile]] = {}
        self._free_agents: list[PlayerProfile] = []

        # Season accumulators.
        self.player_acc: dict[tuple[int, str, str], dict[str, Any]] = {}
        self.team_acc: dict[tuple[int, str, str], dict[str, Any]] = {}
        self.final_games = 0
        self.scheduled_games = 0
        self.last_final_date: date | None = None

    # ------------------------------------------------------------------ reference rows

    def write_teams(self) -> None:
        for team_id, abbr, city, nickname, conference, division, founded, name in NBA_TEAMS:
            self.writer.add(
                Team,
                {
                    "team_id": team_id,
                    "abbr": abbr,
                    "name": name,
                    "city": city,
                    "nickname": nickname,
                    "conference": conference,
                    "division": division,
                    "is_active": True,
                    "year_founded": founded,
                    "year_last_active": None,
                },
            )

    def active_team_ids(self, year: int) -> list[int]:
        """Franchises that existed in a season — 23 teams in 1985-86, 27 in 1992-93."""
        return [row[0] for row in NBA_TEAMS if row[6] <= year]

    # ------------------------------------------------------------------ player pool

    def _shuffled(
        self, pool: Sequence[identities.PlayerIdentity]
    ) -> list[identities.PlayerIdentity]:
        """Sort by person id, then shuffle with the seeded Random: reproducible either way."""
        ordered = sorted(pool, key=lambda identity: identity.player_id)
        self.rng.shuffle(ordered)
        return ordered

    def identity_pool_for(self, season_year: int) -> str:
        """Which real-name pool a season may draw from. See :data:`ACTIVE_IDENTITY_FROM`."""
        return "active" if season_year >= ACTIVE_IDENTITY_FROM else "historical"

    def _draw_identity(self, season_year: int) -> identities.PlayerIdentity | None:
        """Take the next real identity for this era, or ``None`` when the pool is out.

        Skips anyone already used and anyone whose name is already on a jersey: 38 names in
        the file belong to more than one person ("Charles Smith" to three of them), and two
        players sharing a name in one demo league would read as a bug rather than as the NBA.
        """
        pool = self._identity_pools.get(self.identity_pool_for(season_year), [])
        while pool:
            identity = pool.pop()
            if identity.player_id in self.players or identity.name in self._used_names:
                continue
            return identity
        return None

    def _new_player(self, season_year: int, role_index: int) -> PlayerProfile:
        rng = self.rng
        identity = self._draw_identity(season_year)
        if identity is not None:
            first, last = identity.first_name, identity.last_name
            full_name = identity.name
            player_id = identity.player_id
            headshot_url = identity.headshot_url
            identity_pool = "active" if identity.is_active else "historical"
        else:
            # No identity file, or its pool ran dry. An invented name is honest; reusing a
            # real one, or leaving a roster spot empty, is not.
            while True:
                first = rng.choice(FIRST_NAMES)
                last = rng.choice(LAST_NAMES)
                full_name = f"{first} {last}"
                if full_name not in self._used_names:
                    break
            player_id = self._next_player_id
            self._next_player_id += 1
            headshot_url = None
            identity_pool = "invented"
        self._used_names.add(full_name)
        self.identity_counts[identity_pool] += 1
        archetype = rng.choices(ARCHETYPES, weights=ARCHETYPE_WEIGHTS, k=1)[0]

        # Better players are drafted higher, last longer and are already on the floor.
        talent = clamp(rng.gauss(1.0 - 0.035 * role_index, 0.13), 0.55, 1.65)
        career_length = int(clamp(round(rng.gauss(6.0 + 7.0 * (talent - 0.8), 2.6)), 3, 19))
        seasons_in = rng.randrange(0, max(1, career_length))
        from_year = season_year - seasons_in
        to_year = from_year + career_length - 1
        rookie_age = rng.choice((19, 19, 20, 20, 21, 22, 23))
        birth_year = from_year - rookie_age

        if talent > 1.12:
            draft_round, draft_pick = 1, rng.randrange(1, 15)
        elif talent > 0.92:
            draft_round, draft_pick = 1, rng.randrange(10, 31)
        elif talent > 0.78:
            draft_round, draft_pick = 2, rng.randrange(31, 61)
        else:
            undrafted = rng.random() < 0.4
            draft_round, draft_pick = (
                (None, None) if undrafted else (2, rng.randrange(40, 61))
            )

        # Basketball-Reference's own convention: five of the surname, two of the given name,
        # a counter. Folded to ASCII, so "Dončić" slugs as "doncilu01" exactly as it does
        # there — the accents belong in the display name, never in an identifier.
        folded_last = identities.fold_name(last).replace(" ", "")
        folded_first = identities.fold_name(first).replace(" ", "")
        slug_base = (folded_last[:5] + folded_first[:2]) or "player"
        suffix = 1
        slug = f"{slug_base}{suffix:02d}"
        while slug in self._used_slugs:
            suffix += 1
            slug = f"{slug_base}{suffix:02d}"
        self._used_slugs.add(slug)

        player = PlayerProfile(
            player_id=player_id,
            first_name=first,
            last_name=last,
            archetype=archetype,
            talent=talent,
            from_year=from_year,
            to_year=to_year,
            birth_year=birth_year,
            height_in=rng.randrange(*archetype.height_range),
            weight=rng.randrange(*archetype.weight_range),
            country=rng.choice(COUNTRIES),
            school=rng.choice(SCHOOLS),
            draft_year=from_year,
            draft_round=draft_round,
            draft_pick=draft_pick,
            jersey=str(rng.randrange(0, 56)),
            bbref_slug=slug,
            headshot_url=headshot_url,
            identity_pool=identity_pool,
            display_name=full_name,
        )
        self.players[player.player_id] = player
        return player

    def _available_in(self, player: PlayerProfile, year: int) -> bool:
        """Under contract in ``year``, and from the right era's name pool.

        The second half is the one that matters for honesty: a name drawn from today's
        league never appears in a season before :data:`ACTIVE_IDENTITY_FROM`, and a retired
        name never appears after it, however long the synthetic career happens to run.
        """
        if not (player.from_year <= year <= player.to_year):
            return False
        if player.identity_pool == "invented":
            return True
        return player.identity_pool == self.identity_pool_for(year)

    def build_rosters(self, year: int) -> dict[int, list[RosterSpot]]:
        """Age every roster forward to ``year``, retire, sign, and set the minute shares."""
        rng = self.rng
        rosters: dict[int, list[RosterSpot]] = {}
        for team_id in self.active_team_ids(year):
            current = self._rosters.get(team_id, [])
            kept = [p for p in current if self._available_in(p, year)]
            # Roster churn: a couple of players change address every off-season.
            rng.shuffle(kept)
            churn = min(len(kept), rng.randrange(1, 4))
            if churn:
                self._free_agents.extend(kept[:churn])
                kept = kept[churn:]
            self._rosters[team_id] = kept

        for team_id in self.active_team_ids(year):
            kept = self._rosters[team_id]
            while len(kept) < self.players_per_team:
                signed: PlayerProfile | None = None
                if self._free_agents and rng.random() < 0.55:
                    index = rng.randrange(len(self._free_agents))
                    candidate = self._free_agents[index]
                    if self._available_in(candidate, year):
                        signed = self._free_agents.pop(index)
                if signed is None:
                    signed = self._new_player(year, len(kept))
                kept.append(signed)
            kept.sort(key=lambda p: -(p.talent * _age_curve(p.age_in(year))))
            self._rosters[team_id] = kept
            # Free agents who have retired — or whose era has turned over — go for good.
            self._free_agents = [p for p in self._free_agents if self._available_in(p, year)]
            _resolve_jersey_clashes(kept, rng)

            shares = _minute_shares(len(kept))
            rosters[team_id] = [
                RosterSpot(
                    player=player,
                    minutes_share=shares[index],
                    role_index=index,
                    form=_age_curve(player.age_in(year)) * rng.uniform(0.92, 1.08),
                )
                for index, player in enumerate(kept)
            ]
        return rosters

    def write_players(self) -> None:
        for player in self.players.values():
            feet, inches = divmod(player.height_in, 12)
            birthday = date(player.birth_year, 1, 1) + timedelta(days=self.rng.randrange(0, 365))
            self.writer.add(
                Player,
                {
                    "player_id": player.player_id,
                    "full_name": player.full_name,
                    "first_name": player.first_name,
                    "last_name": player.last_name,
                    "bbref_slug": player.bbref_slug,
                    "position": player.position,
                    "height": f"{feet}-{inches}",
                    "weight": player.weight,
                    "birthdate": birthday,
                    "country": player.country,
                    "school": player.school,
                    "draft_year": player.draft_year,
                    "draft_round": player.draft_round,
                    "draft_pick": player.draft_pick,
                    "from_year": player.from_year,
                    "to_year": player.to_year,
                    "is_active": player.to_year >= catalog.season_sort_key(self.seasons[-1]),
                    "jersey": player.jersey,
                    # Real photo for a real person id, straight from the identity file; the
                    # fallback path (no file, or the pool ran out) has no photo to show.
                    "headshot_url": player.headshot_url,
                },
            )
            self.writer.add(
                IdCrosswalk,
                {
                    "nba_person_id": player.player_id,
                    "bbref_slug": player.bbref_slug,
                    # The NBA person id is real; these two are not, so the method says so.
                    "espn_id": 3000000 + player.player_id % 900000,
                    "balldontlie_id": player.player_id % 100000,
                    "confidence": 1.0,
                    "method": DATA_SOURCE,
                },
            )

    # ------------------------------------------------------------------ season build

    def build_season(self, season: str, rosters: dict[int, list[RosterSpot]]) -> None:
        year = catalog.season_sort_key(season)
        era = era_for(year)
        rng = self.rng

        # Strength is centred on this season's own league, so the average team scores the
        # era's average and only the spread between teams comes from the rosters.
        strengths = {
            team_id: sum(spot.rating * spot.minutes_share for spot in roster)
            for team_id, roster in rosters.items()
        }
        league_strength = statistics.fmean(strengths.values())

        season_teams: dict[int, SeasonTeam] = {}
        for team_id, roster in rosters.items():
            centred = (strengths[team_id] - league_strength) * STRENGTH_TO_NET_RATING
            season_teams[team_id] = SeasonTeam(
                team_id=team_id,
                abbr=self.teams[team_id][1],
                conference=self.teams[team_id][4],
                roster=roster,
                off_adj=centred * 0.6 + rng.gauss(0, 2.2),
                def_adj=-centred * 0.4 + rng.gauss(0, 2.2),
                tempo_adj=rng.gauss(0, 2.4),
                three_lean=clamp(rng.gauss(1.0, 0.14), 0.65, 1.4),
            )

        start, end = _season_calendar(year)
        schedule = _build_schedule(list(season_teams), self.games_per_team, rng)
        dates = _assign_dates(schedule, start, end)

        is_current = season == self.seasons[-1]
        for (home_id, away_id), game_date in zip(schedule, dates):
            if is_current and game_date > self.as_of:
                self._write_scheduled_game(season, "Regular Season", home_id, away_id, game_date)
                continue
            self.simulate_game(
                season, "Regular Season", era,
                season_teams[home_id], season_teams[away_id], game_date,
            )

        if self.include_playoffs and not is_current:
            self.build_postseason(season, era, season_teams, end)

        self._write_team_seasons(season)

    def build_postseason(
        self, season: str, era: Era, season_teams: dict[int, SeasonTeam], regular_end: date
    ) -> None:
        """Play-in, then a 16-team bracket of best-of-seven series."""
        standings: dict[str, list[SeasonTeam]] = {"East": [], "West": []}
        for team in season_teams.values():
            standings[team.conference].append(team)
        for conference in standings:
            standings[conference].sort(
                key=lambda t: (-(t.wins / max(1, t.wins + t.losses)), -t.wins, t.abbr)
            )

        play_in_day = regular_end + timedelta(days=2)
        # The play-in tournament only exists from 2020-21: earlier postseasons seed the top
        # eight in each conference straight from the standings.
        has_play_in = catalog.season_sort_key(season) >= PLAY_IN_FROM
        brackets: dict[str, list[SeasonTeam]] = {}
        for conference, teams in standings.items():
            if len(teams) < 10 or not has_play_in:
                brackets[conference] = teams[:8]
                continue
            seeds = teams[:6]
            seven, eight, nine, ten = teams[6], teams[7], teams[8], teams[9]
            winner_78 = self.simulate_game(season, "Play In", era, seven, eight, play_in_day)
            winner_910 = self.simulate_game(season, "Play In", era, nine, ten, play_in_day)
            loser_78 = eight if winner_78 is seven else seven
            final_spot = self.simulate_game(
                season, "Play In", era, loser_78, winner_910, play_in_day + timedelta(days=2)
            )
            brackets[conference] = seeds + [winner_78, final_spot]

        round_start = play_in_day + timedelta(days=5)
        finalists: list[SeasonTeam] = []
        for bracket in brackets.values():
            if len(bracket) < 8:
                continue
            day = round_start
            pairs = [(bracket[0], bracket[7]), (bracket[3], bracket[4]),
                     (bracket[2], bracket[5]), (bracket[1], bracket[6])]
            while True:
                winners = [self._play_series(season, era, high, low, day) for high, low in pairs]
                day += timedelta(days=18)
                if len(winners) == 1:
                    finalists.append(winners[0])
                    break
                pairs = [(winners[i], winners[i + 1]) for i in range(0, len(winners), 2)]
        if len(finalists) == 2:
            self._play_series(
                season, era, finalists[0], finalists[1], round_start + timedelta(days=54)
            )

    def _play_series(
        self, season: str, era: Era, high: SeasonTeam, low: SeasonTeam, start: date
    ) -> SeasonTeam:
        """Best of seven, 2-2-1-1-1 home court to the higher seed."""
        home_pattern = (True, True, False, False, True, False, True)
        wins = {high.team_id: 0, low.team_id: 0}
        day = start
        for game_number in range(7):
            if max(wins.values()) == 4:
                break
            host, guest = (high, low) if home_pattern[game_number] else (low, high)
            winner = self.simulate_game(season, "Playoffs", era, host, guest, day)
            wins[winner.team_id] += 1
            day += timedelta(days=2 if game_number % 2 == 0 else 3)
        return high if wins[high.team_id] > wins[low.team_id] else low

    # ------------------------------------------------------------------ one game

    def _game_id(self, season: str, season_type: str, year: int) -> str:
        digit = {"Regular Season": "2", "Playoffs": "4", "Play In": "5",
                 "All Star": "3", "Pre Season": "1"}[season_type]
        key = (year, season_type)
        sequence = self._game_sequence.get(key, 0) + 1
        self._game_sequence[key] = sequence
        return f"00{digit}{year % 100:02d}{sequence:05d}"

    def _write_scheduled_game(
        self, season: str, season_type: str, home_id: int, away_id: int, game_date: date
    ) -> None:
        year = catalog.season_sort_key(season)
        self.writer.add(
            Game,
            {
                "game_id": self._game_id(season, season_type, year),
                "game_date": game_date,
                "season": season,
                "season_type": season_type,
                "home_team_id": home_id,
                "away_team_id": away_id,
                "home_pts": None,
                "away_pts": None,
                "status": "scheduled",
                "period": None,
                "clock": None,
                "finalized_at": None,
                "data_source": DATA_SOURCE,
                "ingested_at": utcnow(),
            },
        )
        self.scheduled_games += 1

    def simulate_game(
        self,
        season: str,
        season_type: str,
        era: Era,
        home: SeasonTeam,
        away: SeasonTeam,
        game_date: date,
    ) -> SeasonTeam:
        """Play one game, write every row it produces, and return the winner."""
        rng = self.rng
        year = catalog.season_sort_key(season)
        game_id = self._game_id(season, season_type, year)

        possessions = clamp(
            rng.gauss(era.pace + (home.tempo_adj + away.tempo_adj) / 2, 3.0),
            era.pace - 12,
            era.pace + 12,
        )
        home_ppp = era.ortg + home.off_adj - away.def_adj + HOME_EDGE / 2
        away_ppp = era.ortg + away.off_adj - home.def_adj - HOME_EDGE / 2
        home_pts = int(round(home_ppp * possessions / 100 + rng.gauss(0, 7.5)))
        away_pts = int(round(away_ppp * possessions / 100 + rng.gauss(0, 7.5)))
        home_pts = max(72, home_pts)
        away_pts = max(72, away_pts)
        if home_pts == away_pts:  # no ties: someone wins in the extra period
            if rng.random() < 0.5:
                home_pts += rng.randrange(1, 5)
            else:
                away_pts += rng.randrange(1, 5)

        home_line = _team_shooting_line(rng, era, home, possessions, home_pts)
        away_line = _team_shooting_line(rng, era, away, possessions, away_pts)
        _fill_rebounds_and_playmaking(rng, era, home_line, away_line, possessions)
        _fill_rebounds_and_playmaking(rng, era, away_line, home_line, possessions)
        # A steal is the other team's turnover, so neither side can have more.
        home_line["stl"] = _steals_from(rng, away_line["tov"])
        away_line["stl"] = _steals_from(rng, home_line["tov"])

        rotation_size = 8 if season_type == "Playoffs" else 9
        home_players = _game_rotation(rng, home, rotation_size)
        away_players = _game_rotation(rng, away, rotation_size)

        home_rows = _distribute_team_line(rng, era, home_line, home_players)
        away_rows = _distribute_team_line(rng, era, away_line, away_players)

        pie_denominator = sum(_pie_numerator(row) for row in home_rows + away_rows)
        margin = home_pts - away_pts
        _assign_plus_minus(rng, home_rows, margin)
        _assign_plus_minus(rng, away_rows, -margin)

        advanced_era = year >= ADVANCED_FROM
        # Pace is possessions per 48 minutes, and a game is 48 minutes: the box-score
        # estimate the two teams share *is* the pace.
        self._write_game_rows(
            season, season_type, game_id, game_date, home, away, home_line, away_line,
            home_rows, away_rows, pie_denominator, advanced_era,
            _possessions(home_line, away_line),
        )

        winner = home if home_pts > away_pts else away
        if season_type == "Regular Season":
            # Only the regular season decides the seeding the bracket is built from.
            if home_pts > away_pts:
                home.wins += 1
                away.losses += 1
            else:
                away.wins += 1
                home.losses += 1
        self.final_games += 1
        if self.last_final_date is None or game_date > self.last_final_date:
            self.last_final_date = game_date
        return winner

    def _write_game_rows(
        self,
        season: str,
        season_type: str,
        game_id: str,
        game_date: date,
        home: SeasonTeam,
        away: SeasonTeam,
        home_line: dict[str, int],
        away_line: dict[str, int],
        home_rows: list[dict[str, Any]],
        away_rows: list[dict[str, Any]],
        pie_denominator: float,
        advanced_era: bool,
        pace: float,
    ) -> None:
        # Box scores are final in the small hours of the following morning.
        finalized = datetime.combine(game_date, datetime.min.time()) + timedelta(
            hours=26, minutes=41
        )
        self.writer.add(
            Game,
            {
                "game_id": game_id,
                "game_date": game_date,
                "season": season,
                "season_type": season_type,
                "home_team_id": home.team_id,
                "away_team_id": away.team_id,
                "home_pts": home_line["pts"],
                "away_pts": away_line["pts"],
                "status": "final",
                "period": 4,
                "clock": None,
                "finalized_at": finalized,
                "data_source": DATA_SOURCE,
                "ingested_at": utcnow(),
            },
        )

        for team, line, opponent_line, rows, is_home in (
            (home, home_line, away_line, home_rows, True),
            (away, away_line, home_line, away_rows, False),
        ):
            self._write_team_game(
                season, season_type, game_id, team, line, opponent_line, is_home, pace
            )
            self._accumulate_team(season, season_type, team, line, opponent_line)
            for row in rows:
                self._write_player_game(
                    season, season_type, game_id, team, row, line, opponent_line,
                    pie_denominator, advanced_era, pace,
                )

    def _write_team_game(
        self,
        season: str,
        season_type: str,
        game_id: str,
        team: SeasonTeam,
        line: dict[str, int],
        opponent: dict[str, int],
        is_home: bool,
        pace: float,
    ) -> None:
        possessions = _possessions(line, opponent)
        opponent_possessions = possessions
        row: dict[str, Any] = {
            "game_id": game_id,
            "team_id": team.team_id,
            "is_home": is_home,
            "won": line["pts"] > opponent["pts"],
            "minutes": TEAM_MINUTES,
            "opp_pts": opponent["pts"],
            "plus_minus": float(line["pts"] - opponent["pts"]),
            "fg_pct": safe_div(line["fgm"], line["fga"]),
            "fg3_pct": safe_div(line["fg3m"], line["fg3a"]),
            "ft_pct": safe_div(line["ftm"], line["fta"]),
            "fg3a_rate": safe_div(line["fg3a"], line["fga"]),
            "pps": safe_div(line["pts"], line["fga"]),
            "ts_pct": safe_div(line["pts"], 2 * (line["fga"] + 0.44 * line["fta"])),
            "efg_pct": safe_div(line["fgm"] + 0.5 * line["fg3m"], line["fga"]),
            "tov_pct": safe_div(line["tov"], line["fga"] + 0.44 * line["fta"] + line["tov"]),
            "oreb_pct": safe_div(line["oreb"], line["oreb"] + opponent["dreb"]),
            "ftr": safe_div(line["fta"], line["fga"]),
            "opp_efg_pct": safe_div(opponent["fgm"] + 0.5 * opponent["fg3m"], opponent["fga"]),
            "opp_tov_pct": safe_div(
                opponent["tov"], opponent["fga"] + 0.44 * opponent["fta"] + opponent["tov"]
            ),
            "opp_oreb_pct": safe_div(opponent["oreb"], opponent["oreb"] + line["dreb"]),
            "opp_ftr": safe_div(opponent["fta"], opponent["fga"]),
            "off_rtg": safe_div(100.0 * line["pts"], possessions),
            "def_rtg": safe_div(100.0 * opponent["pts"], opponent_possessions),
            "pace": pace,
            "poss": possessions,
            "data_source": DATA_SOURCE,
        }
        for key in ("pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb",
                    "ast", "stl", "blk", "tov", "pf"):
            row[key] = line[key]
        if row["off_rtg"] is not None and row["def_rtg"] is not None:
            row["net_rtg"] = row["off_rtg"] - row["def_rtg"]
        apply_era_rules(row, "team_game", season, "game")
        self.writer.add(TeamGame, row)

    def _write_player_game(
        self,
        season: str,
        season_type: str,
        game_id: str,
        team: SeasonTeam,
        row: dict[str, Any],
        team_line: dict[str, int],
        opponent_line: dict[str, int],
        pie_denominator: float,
        advanced_era: bool,
        pace: float,
    ) -> None:
        basic: dict[str, Any] = {
            "game_id": game_id,
            "player_id": row["player_id"],
            "team_id": team.team_id,
            "started": row["started"],
            "minutes": row["minutes"],
            "plus_minus": float(row["plus_minus"]),
            "fantasy_pts": _fantasy_points(row),
            "data_source": DATA_SOURCE,
        }
        for key in ("fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb",
                    "ast", "stl", "blk", "tov", "pf", "pts"):
            basic[key] = row[key]
        apply_era_rules(basic, "player_game", season, "game")
        self.writer.add(PlayerGameBasic, basic)

        advanced = _player_advanced(row, team_line, opponent_line, pie_denominator, pace)
        if advanced_era:
            advanced_row: dict[str, Any] = {
                "game_id": game_id,
                "player_id": row["player_id"],
                "is_estimated": False,
                "data_source": DATA_SOURCE,
                **advanced,
            }
            apply_era_rules(advanced_row, "player_game_advanced", season, "game")
            self.writer.add(PlayerGameAdvanced, advanced_row)

        self._accumulate_player(season, season_type, team, row, advanced, pie_denominator)

    # ------------------------------------------------------------------ accumulators

    def _accumulate_player(
        self,
        season: str,
        season_type: str,
        team: SeasonTeam,
        row: dict[str, Any],
        advanced: dict[str, float | None],
        pie_denominator: float,
    ) -> None:
        key = (row["player_id"], season, season_type)
        acc = self.player_acc.get(key)
        if acc is None:
            acc = {
                "player_id": row["player_id"],
                "team_id": team.team_id,
                "gp": 0,
                "gs": 0,
                "minutes": 0.0,
                "plus_minus": 0.0,
                "game_score": 0.0,
                "pie_num": 0.0,
                "pie_den": 0.0,
                "off_rtg_min": 0.0,
                "def_rtg_min": 0.0,
                "rtg_min": 0.0,
                "poss": 0.0,
            }
            for column in ("pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf",
                           "fgm", "fga", "fg3m", "fg3a", "ftm", "fta"):
                acc[column] = 0
            self.player_acc[key] = acc
        acc["gp"] += 1
        acc["gs"] += 1 if row["started"] else 0
        acc["minutes"] += row["minutes"]
        acc["plus_minus"] += row["plus_minus"]
        acc["game_score"] += _game_score(row)
        acc["pie_num"] += _pie_numerator(row)
        acc["pie_den"] += pie_denominator
        acc["poss"] += advanced.get("poss") or 0.0
        if advanced.get("off_rtg") is not None:
            acc["off_rtg_min"] += advanced["off_rtg"] * row["minutes"]
            acc["def_rtg_min"] += (advanced["def_rtg"] or 0.0) * row["minutes"]
            acc["rtg_min"] += row["minutes"]
        for column in ("pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf",
                       "fgm", "fga", "fg3m", "fg3a", "ftm", "fta"):
            acc[column] += row[column]

    def _accumulate_team(
        self, season: str, season_type: str, team: SeasonTeam,
        line: dict[str, int], opponent: dict[str, int],
    ) -> None:
        key = (team.team_id, season, season_type)
        acc = self.team_acc.get(key)
        if acc is None:
            acc = {"gp": 0, "wins": 0, "losses": 0, "poss": 0.0, "opp_poss": 0.0, "minutes": 0.0}
            for column in ("pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf",
                           "fgm", "fga", "fg3m", "fg3a", "ftm", "fta"):
                acc[column] = 0
                acc[f"opp_{column}"] = 0
            self.team_acc[key] = acc
        acc["gp"] += 1
        acc["minutes"] += TEAM_MINUTES
        won = line["pts"] > opponent["pts"]
        acc["wins"] += 1 if won else 0
        acc["losses"] += 0 if won else 1
        possessions = _possessions(line, opponent)
        acc["poss"] += possessions
        acc["opp_poss"] += possessions
        for column in ("pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf",
                       "fgm", "fga", "fg3m", "fg3a", "ftm", "fta"):
            acc[column] += line[column]
            acc[f"opp_{column}"] += opponent[column]

    # ------------------------------------------------------------------ aggregates

    def _write_team_seasons(self, season: str) -> None:
        """Write every season type this season produced: regular season, play-in, playoffs."""
        for (team_id, row_season, row_type), acc in list(self.team_acc.items()):
            if row_season != season:
                continue
            games = acc["gp"]
            if not games:
                continue
            possessions = acc["poss"]
            opponent_possessions = acc["opp_poss"]
            row: dict[str, Any] = {
                "team_id": team_id,
                "season": season,
                "season_type": row_type,
                "gp": games,
                "wins": acc["wins"],
                "losses": acc["losses"],
                "win_pct": acc["wins"] / games,
                "minutes": acc["minutes"],
                "min_pg": acc["minutes"] / games,
                "opp_pts": acc["opp_pts"] / games,
                "fg_pct": safe_div(acc["fgm"], acc["fga"]),
                "fg3_pct": safe_div(acc["fg3m"], acc["fg3a"]),
                "ft_pct": safe_div(acc["ftm"], acc["fta"]),
                "efg_pct": safe_div(acc["fgm"] + 0.5 * acc["fg3m"], acc["fga"]),
                "ts_pct": safe_div(acc["pts"], 2 * (acc["fga"] + 0.44 * acc["fta"])),
                "fg3a_rate": safe_div(acc["fg3a"], acc["fga"]),
                "ftr": safe_div(acc["fta"], acc["fga"]),
                "pps": safe_div(acc["pts"], acc["fga"]),
                "tov_pct": safe_div(acc["tov"], acc["fga"] + 0.44 * acc["fta"] + acc["tov"]),
                "oreb_pct": safe_div(acc["oreb"], acc["oreb"] + acc["opp_dreb"]),
                "dreb_pct": safe_div(acc["dreb"], acc["dreb"] + acc["opp_oreb"]),
                "reb_pct": safe_div(acc["reb"], acc["reb"] + acc["opp_reb"]),
                "opp_efg_pct": safe_div(acc["opp_fgm"] + 0.5 * acc["opp_fg3m"], acc["opp_fga"]),
                "opp_tov_pct": safe_div(
                    acc["opp_tov"], acc["opp_fga"] + 0.44 * acc["opp_fta"] + acc["opp_tov"]
                ),
                "opp_oreb_pct": safe_div(acc["opp_oreb"], acc["opp_oreb"] + acc["dreb"]),
                "opp_ftr": safe_div(acc["opp_fta"], acc["opp_fga"]),
                "off_rtg": safe_div(100.0 * acc["pts"], possessions),
                "def_rtg": safe_div(100.0 * acc["opp_pts"], opponent_possessions),
                "pace": safe_div(possessions, games),
                "poss": possessions,
                "data_source": DATA_SOURCE,
            }
            for column in ("pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf",
                           "fgm", "fga", "fg3m", "fg3a", "ftm", "fta"):
                row[column] = acc[column] / games
            for column in ("pts", "reb", "ast", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta"):
                row[f"{column}_tot"] = acc[column]
            if row["off_rtg"] is not None and row["def_rtg"] is not None:
                row["net_rtg"] = row["off_rtg"] - row["def_rtg"]
            row["is_estimated"] = apply_era_rules(row, "team_season", season, "season")
            self.writer.add(TeamSeason, row)

    def write_player_seasons(self) -> None:
        """Season aggregates, then the league-normalised ratings that depend on them."""
        by_season: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for (player_id, season, season_type), acc in self.player_acc.items():
            team_key = (acc["team_id"], season, season_type)
            team_acc = self.team_acc.get(team_key)
            if team_acc is None or not acc["gp"]:
                continue
            row = self._player_season_row(player_id, season, season_type, acc, team_acc)
            by_season.setdefault((season, season_type), []).append(row)

        for (season, season_type), rows in by_season.items():
            self._apply_season_ratings(season, season_type, rows)
            for row in rows:
                row["is_estimated"] = apply_era_rules(row, "player_season", season, "season")
                self.writer.add(PlayerSeason, row)
            self._write_league_distribution("player", season, season_type, rows)
            self._write_shot_zones(season, season_type, rows)

        team_rows_by_season: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for (team_id, season, season_type), acc in self.team_acc.items():
            games = acc["gp"]
            if not games:
                continue
            team_rows_by_season.setdefault((season, season_type), []).append(
                self._team_distribution_row(team_id, season, acc)
            )
        for (season, season_type), rows in team_rows_by_season.items():
            self._write_league_distribution("team", season, season_type, rows)
            self._write_team_shot_zones(season, season_type, rows)

    def _player_season_row(
        self,
        player_id: int,
        season: str,
        season_type: str,
        acc: dict[str, Any],
        team_acc: dict[str, Any],
    ) -> dict[str, Any]:
        games = acc["gp"]
        minutes = acc["minutes"]
        player = self.players[player_id]
        year = catalog.season_sort_key(season)
        team_minutes = team_acc["minutes"]
        team_possessions = team_acc["poss"]
        opponent_possessions = team_acc["opp_poss"]

        row: dict[str, Any] = {
            "player_id": player_id,
            "season": season,
            "season_type": season_type,
            "team_id": acc["team_id"],
            "age": player.age_in(year),
            "gp": games,
            "gs": acc["gs"],
            "minutes": minutes,
            "min_pg": minutes / games,
            "plus_minus": acc["plus_minus"] / games,
            "plus_minus_tot": acc["plus_minus"],
            "game_score": acc["game_score"] / games,
            "data_source": DATA_SOURCE,
        }
        for column in ("pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf",
                       "fgm", "fga", "fg3m", "fg3a", "ftm", "fta"):
            row[column] = acc[column] / games
            row[f"{column}_tot"] = acc[column]
        fantasy = (
            acc["pts"] + 1.2 * acc["reb"] + 1.5 * acc["ast"]
            + 3.0 * acc["stl"] + 3.0 * acc["blk"] - acc["tov"]
        )
        row["fantasy_pts"] = fantasy / games
        row["fantasy_pts_tot"] = fantasy

        row["fg_pct"] = safe_div(acc["fgm"], acc["fga"])
        row["fg3_pct"] = safe_div(acc["fg3m"], acc["fg3a"])
        row["ft_pct"] = safe_div(acc["ftm"], acc["fta"])
        row["efg_pct"] = safe_div(acc["fgm"] + 0.5 * acc["fg3m"], acc["fga"])
        row["ts_pct"] = safe_div(acc["pts"], 2 * (acc["fga"] + 0.44 * acc["fta"]))
        row["fg3a_rate"] = safe_div(acc["fg3a"], acc["fga"])
        row["ftr"] = safe_div(acc["fta"], acc["fga"])
        row["pps"] = safe_div(acc["pts"], acc["fga"])

        scoring_possessions = acc["fga"] + 0.44 * acc["fta"] + acc["tov"]
        row["usg_pct"] = safe_div(
            scoring_possessions * (team_minutes / 5.0),
            minutes * (team_acc["fga"] + 0.44 * team_acc["fta"] + team_acc["tov"]),
        )
        row["ast_pct"] = safe_div(
            acc["ast"], (minutes / (team_minutes / 5.0)) * team_acc["fgm"] - acc["fgm"]
        )
        row["ast_tov"] = safe_div(acc["ast"], acc["tov"])
        row["ast_ratio"] = safe_div(100.0 * acc["ast"], scoring_possessions + acc["ast"])
        row["tov_pct"] = safe_div(acc["tov"], scoring_possessions)
        row["oreb_pct"] = safe_div(
            acc["oreb"] * (team_minutes / 5.0),
            minutes * (team_acc["oreb"] + team_acc["opp_dreb"]),
        )
        row["dreb_pct"] = safe_div(
            acc["dreb"] * (team_minutes / 5.0),
            minutes * (team_acc["dreb"] + team_acc["opp_oreb"]),
        )
        row["reb_pct"] = safe_div(
            acc["reb"] * (team_minutes / 5.0),
            minutes * (team_acc["reb"] + team_acc["opp_reb"]),
        )
        row["stl_pct"] = safe_div(
            acc["stl"] * (team_minutes / 5.0), minutes * opponent_possessions
        )
        row["blk_pct"] = safe_div(
            acc["blk"] * (team_minutes / 5.0),
            minutes * max(1.0, team_acc["opp_fga"] - team_acc["opp_fg3a"]),
        )
        row["pie"] = safe_div(acc["pie_num"], acc["pie_den"])
        row["poss"] = acc["poss"] or safe_div(team_possessions * minutes, team_minutes / 5.0)
        row["pace"] = safe_div(team_possessions * 48.0, team_acc["gp"] * 48.0)
        if acc["rtg_min"] > 0:
            row["off_rtg"] = acc["off_rtg_min"] / acc["rtg_min"]
            row["def_rtg"] = acc["def_rtg_min"] / acc["rtg_min"]
            row["net_rtg"] = row["off_rtg"] - row["def_rtg"]
        return row

    def _apply_season_ratings(
        self, season: str, season_type: str, rows: list[dict[str, Any]]
    ) -> None:
        """PER, Win Shares, BPM and VORP — each normalised the way the real metric is.

        PER is scaled so the minute-weighted league average is exactly 15.00, BPM so it is
        exactly 0.0, and each team's Win Shares sum to that team's wins.
        """
        raw: dict[int, float] = {}
        for row in rows:
            minutes = row["minutes"] or 1.0
            production = (
                row["pts_tot"] + 0.7 * row["ast_tot"] + 0.7 * row["oreb_tot"]
                + 0.3 * row["dreb_tot"] + row["stl_tot"] + 0.7 * row["blk_tot"]
                - 0.72 * (row["fga_tot"] - row["fgm_tot"])
                - 0.42 * (row["fta_tot"] - row["ftm_tot"])
                - row["tov_tot"] - 0.28 * row["pf_tot"]
            )
            raw[row["player_id"]] = production / minutes
        total_minutes = sum(row["minutes"] or 0.0 for row in rows) or 1.0
        weighted_mean = (
            sum(raw[r["player_id"]] * (r["minutes"] or 0.0) for r in rows) / total_minutes
        )
        scale = 15.0 / weighted_mean if weighted_mean else 1.0

        for row in rows:
            row["per"] = round(clamp(raw[row["player_id"]] * scale, 0.0, 35.0), 2)

        bpm_raw = {row["player_id"]: (row["per"] - 15.0) / 2.2 for row in rows}
        bpm_mean = (
            sum(bpm_raw[r["player_id"]] * (r["minutes"] or 0.0) for r in rows) / total_minutes
        )
        for row in rows:
            bpm = clamp(bpm_raw[row["player_id"]] - bpm_mean, -8.0, 14.0)
            offence_share = clamp(0.62 + 0.25 * ((row["usg_pct"] or 0.2) - 0.2), 0.35, 0.85)
            row["bpm"] = round(bpm, 2)
            row["obpm"] = round(bpm * offence_share, 2)
            row["dbpm"] = round(bpm - bpm * offence_share, 2)

        by_team: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            by_team.setdefault(row["team_id"], []).append(row)
        for team_id, team_rows in by_team.items():
            team_acc = self.team_acc[(team_id, season, season_type)]
            team_games = max(1, team_acc["gp"])
            team_wins = float(team_acc["wins"])
            weights = [
                max(0.05, (r["per"] or 0.0) - 5.0) * (r["minutes"] or 0.0) for r in team_rows
            ]
            weight_sum = sum(weights) or 1.0
            for row, weight in zip(team_rows, weights):
                win_shares = team_wins * weight / weight_sum
                offence_share = clamp(0.5 + 0.9 * ((row["usg_pct"] or 0.2) - 0.2), 0.25, 0.82)
                row["ws"] = round(win_shares, 2)
                row["ows"] = round(win_shares * offence_share, 2)
                row["dws"] = round(win_shares - win_shares * offence_share, 2)
                row["ws48"] = round(
                    clamp(48.0 * win_shares / (row["minutes"] or 1.0), -0.05, 0.32), 3
                )
                minute_share = (row["minutes"] or 0.0) / (team_games * TEAM_MINUTES)
                row["vorp"] = round((row["bpm"] + 2.0) * minute_share * (team_games / 82.0), 2)

    def _team_distribution_row(
        self, team_id: int, season: str, acc: dict[str, Any]
    ) -> dict[str, Any]:
        games = acc["gp"]
        row: dict[str, Any] = {
            "team_id": team_id,
            "subject_id": team_id,
            "gp": games,
            "wins": acc["wins"],
            "losses": acc["losses"],
            "win_pct": acc["wins"] / games,
            "fga_tot": acc["fga"],
            "fgm_tot": acc["fgm"],
            "fg3a_tot": acc["fg3a"],
            "fg3m_tot": acc["fg3m"],
            "fg_pct": safe_div(acc["fgm"], acc["fga"]),
            "fg3_pct": safe_div(acc["fg3m"], acc["fg3a"]),
            "ft_pct": safe_div(acc["ftm"], acc["fta"]),
            "efg_pct": safe_div(acc["fgm"] + 0.5 * acc["fg3m"], acc["fga"]),
            "ts_pct": safe_div(acc["pts"], 2 * (acc["fga"] + 0.44 * acc["fta"])),
            "fg3a_rate": safe_div(acc["fg3a"], acc["fga"]),
            "ftr": safe_div(acc["fta"], acc["fga"]),
            "pps": safe_div(acc["pts"], acc["fga"]),
            "tov_pct": safe_div(acc["tov"], acc["fga"] + 0.44 * acc["fta"] + acc["tov"]),
            "oreb_pct": safe_div(acc["oreb"], acc["oreb"] + acc["opp_dreb"]),
            "dreb_pct": safe_div(acc["dreb"], acc["dreb"] + acc["opp_oreb"]),
            "reb_pct": safe_div(acc["reb"], acc["reb"] + acc["opp_reb"]),
            "opp_efg_pct": safe_div(acc["opp_fgm"] + 0.5 * acc["opp_fg3m"], acc["opp_fga"]),
            "opp_tov_pct": safe_div(
                acc["opp_tov"], acc["opp_fga"] + 0.44 * acc["opp_fta"] + acc["opp_tov"]
            ),
            "opp_oreb_pct": safe_div(acc["opp_oreb"], acc["opp_oreb"] + acc["dreb"]),
            "opp_ftr": safe_div(acc["opp_fta"], acc["opp_fga"]),
            "off_rtg": safe_div(100.0 * acc["pts"], acc["poss"]),
            "def_rtg": safe_div(100.0 * acc["opp_pts"], acc["opp_poss"]),
            "pace": safe_div(acc["poss"], games),
            "poss": acc["poss"],
            "min_pg": acc["minutes"] / games,
            "minutes": acc["minutes"],
        }
        for column in ("pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf",
                       "fgm", "fga", "fg3m", "fg3a", "ftm", "fta"):
            row[column] = acc[column] / games
        if row["off_rtg"] is not None and row["def_rtg"] is not None:
            row["net_rtg"] = row["off_rtg"] - row["def_rtg"]
        return row

    def _write_league_distribution(
        self, subject_type: str, season: str, season_type: str, rows: list[dict[str, Any]]
    ) -> None:
        """Average, spread and percentile anchors for every metric the season recorded."""
        if subject_type == "player":
            groups = _COLUMN_GROUPS["player_season"]
            most_games = max((row["gp"] or 0) for row in rows)
            threshold = max(2, int(0.2 * max(1, most_games)))
            qualified = [
                row for row in rows
                if (row["gp"] or 0) >= threshold and (row["min_pg"] or 0.0) >= 10.0
            ] or rows
        else:
            groups = _COLUMN_GROUPS["team_season"]
            qualified = rows
        in_scope = {m["key"] for m in catalog.metrics_for_scope(subject_type)}

        for metric_key, columns in groups.items():
            if metric_key not in in_scope:
                continue
            if catalog.metric_availability(metric_key, season, "season") == "unavailable":
                continue
            column = columns[0]
            values = sorted(
                float(row[column]) for row in qualified
                if row.get(column) is not None
            )
            if len(values) < 2:
                continue
            self.writer.add(
                LeagueSeason,
                {
                    "subject_type": subject_type,
                    "season": season,
                    "season_type": season_type,
                    "metric_key": metric_key,
                    "sample_size": len(values),
                    "average": statistics.fmean(values),
                    "stddev": statistics.pstdev(values),
                    "p10": _percentile(values, 0.10),
                    "p25": _percentile(values, 0.25),
                    "p50": _percentile(values, 0.50),
                    "p75": _percentile(values, 0.75),
                    "p90": _percentile(values, 0.90),
                    "min_value": values[0],
                    "max_value": values[-1],
                },
            )

    # ------------------------------------------------------------------ shot zones

    def _write_shot_zones(
        self, season: str, season_type: str, rows: list[dict[str, Any]]
    ) -> None:
        if catalog.season_sort_key(season) < ADVANCED_FROM:
            return  # shot charts start with play-by-play in 1996-97
        era = era_for(catalog.season_sort_key(season))
        for row in rows:
            if (row["fga_tot"] or 0) < 20:
                continue
            player = self.players[row["player_id"]]
            self._emit_zone_rows(
                "player", row["player_id"], season, season_type, era,
                player.archetype, row["fga_tot"], row["fgm_tot"],
                row["fg3a_tot"], row["fg3m_tot"], row["gp"],
            )

    def _write_team_shot_zones(
        self, season: str, season_type: str, rows: list[dict[str, Any]]
    ) -> None:
        if catalog.season_sort_key(season) < ADVANCED_FROM:
            return
        era = era_for(catalog.season_sort_key(season))
        for row in rows:
            self._emit_zone_rows(
                "team", row["subject_id"], season, season_type, era, TEAM_BLEND_ARCHETYPE,
                row["fga_tot"], row["fgm_tot"], row["fg3a_tot"], row["fg3m_tot"], row["gp"],
            )

    def _emit_zone_rows(
        self,
        subject_type: str,
        subject_id: int,
        season: str,
        season_type: str,
        era: Era,
        archetype: Archetype,
        fga_total: float,
        fgm_total: float,
        fg3a_total: float,
        fg3m_total: float,
        games: int,
    ) -> None:
        fga_total = int(fga_total or 0)
        fgm_total = int(fgm_total or 0)
        fg3a_total = int(fg3a_total or 0)
        fg3m_total = int(fg3m_total or 0)
        two_attempts = max(0, fga_total - fg3a_total)
        two_makes = max(0, fgm_total - fg3m_total)
        if fga_total <= 0 or games <= 0:
            return

        two_alloc = allocate(two_attempts, archetype.two_split)
        three_alloc = allocate(fg3a_total, archetype.three_split)
        attempts = {
            "rim": two_alloc[0],
            "paint_non_rim": two_alloc[1],
            "mid_range": two_alloc[2],
            "corner_three": three_alloc[0],
            "above_break_three": three_alloc[1],
        }
        zone_rates = {
            "rim": era.rim_fg_pct,
            "paint_non_rim": era.paint_fg_pct,
            "mid_range": era.mid_fg_pct,
            "corner_three": era.corner_three_pct,
            "above_break_three": era.above_break_three_pct,
        }
        two_zones = ("rim", "paint_non_rim", "mid_range")
        three_zones = ("corner_three", "above_break_three")
        two_makes_alloc = allocate(
            two_makes,
            [attempts[z] * zone_rates[z] for z in two_zones],
            [attempts[z] for z in two_zones],
        )
        three_makes_alloc = allocate(
            fg3m_total,
            [attempts[z] * zone_rates[z] for z in three_zones],
            [attempts[z] for z in three_zones],
        )
        makes = dict(zip(two_zones, two_makes_alloc))
        makes.update(dict(zip(three_zones, three_makes_alloc)))

        for zone in SHOT_ZONES:
            zone_attempts = attempts[zone]
            zone_makes = makes[zone]
            points = 3 if zone.endswith("three") else 2
            self.writer.add(
                ShotZoneSeason,
                {
                    "subject_type": subject_type,
                    "subject_id": subject_id,
                    "season": season,
                    "season_type": season_type,
                    "zone": zone,
                    "fga": zone_attempts / games,
                    "fgm": zone_makes / games,
                    "fga_tot": zone_attempts,
                    "fgm_tot": zone_makes,
                    "fg_pct": safe_div(zone_makes, zone_attempts),
                    "share_of_fga": safe_div(zone_attempts, fga_total),
                    "points_per_shot": safe_div(points * zone_makes, zone_attempts),
                },
            )

    # ------------------------------------------------------------------ entry point

    def run(self) -> dict[str, Any]:
        started = utcnow()
        self.write_teams()
        # Rosters for every season first: that settles the player pool, so players are
        # written before the games that reference them.
        rosters = {
            season: self.build_rosters(catalog.season_sort_key(season))
            for season in self.seasons
        }
        self.write_players()
        self.writer.flush()

        for season in self.seasons:
            self.build_season(season, rosters[season])
        self.write_player_seasons()
        self.writer.flush()

        data_through = self.last_final_date
        self.session.add(
            SyncState(
                id=1,
                sync_version=self.final_games,
                data_through=data_through,
                last_run_at=started,
                last_success_at=utcnow(),
                games_ingested=self.final_games,
                notes=(
                    f"Synthetic demo league seeded from random.Random({SEED}). Player names, "
                    "ids and photos are real NBA identities; every number is generated."
                ),
            )
        )
        self.session.add(
            IngestLog(
                started_at=started,
                finished_at=utcnow(),
                job="seed",
                season=self.seasons[-1],
                status="success",
                games_written=self.final_games + self.scheduled_games,
                rows_written=sum(self.writer.counts.values()),
                error=None,
            )
        )
        self.session.flush()

        counts = dict(self.writer.counts)
        return {
            "seasons": list(self.seasons),
            "as_of": self.as_of,
            "teams": counts.get("teams", 0),
            "players": counts.get("players", 0),
            "games": counts.get("games", 0),
            "final_games": self.final_games,
            "scheduled_games": self.scheduled_games,
            "player_game_rows": counts.get("player_game_basic", 0),
            "player_game_advanced_rows": counts.get("player_game_advanced", 0),
            "team_game_rows": counts.get("team_game", 0),
            "player_season_rows": counts.get("player_season", 0),
            "team_season_rows": counts.get("team_season", 0),
            "league_season_rows": counts.get("league_season", 0),
            "shot_zone_rows": counts.get("shot_zone_season", 0),
            "data_through": data_through,
            "sync_version": self.final_games,
            # The exposure behind the names: how many players carry a real NBA identity,
            # from which pool, and how many had to be invented because the file ran out.
            "real_identities_active": self.identity_counts["active"],
            "real_identities_historical": self.identity_counts["historical"],
            "invented_identities": self.identity_counts["invented"],
        }


# --------------------------------------------------------------------------- game maths


def _resolve_jersey_clashes(roster: Sequence[PlayerProfile], rng: random.Random) -> None:
    """No two teammates wear the same number; the senior player keeps his."""
    taken: set[str] = set()
    for player in roster:
        if player.jersey not in taken:
            taken.add(player.jersey)
            continue
        for _ in range(60):
            candidate = str(rng.randrange(0, 100))
            if candidate not in taken:
                player.jersey = candidate
                taken.add(candidate)
                break


def _minute_shares(size: int) -> list[float]:
    """Share of the 240 team minutes by roster slot, normalised to 1.0."""
    base = [0.145, 0.138, 0.130, 0.122, 0.112, 0.090, 0.080, 0.068, 0.062, 0.053]
    if size <= len(base):
        shares = base[:size]
    else:
        shares = base + [0.03] * (size - len(base))
    total = sum(shares)
    return [share / total for share in shares]


def _season_calendar(year: int) -> tuple[date, date]:
    """Opening night and the last day of the regular season."""
    if year >= 2000:
        return date(year, 10, 22), date(year + 1, 4, 13)
    return date(year, 10, 25), date(year + 1, 4, 20)


def _build_schedule(
    team_ids: Sequence[int], games_per_team: int, rng: random.Random
) -> list[tuple[int, int]]:
    """Round-robin cycles until every team has its games, alternating home court."""
    teams: list[int | None] = list(team_ids)
    rng.shuffle(teams)
    if len(teams) % 2:
        teams.append(None)
    size = len(teams)
    rotation = teams[:]
    rounds: list[list[tuple[int, int]]] = []
    for round_index in range(size - 1):
        pairs: list[tuple[int, int]] = []
        for slot in range(size // 2):
            first, second = rotation[slot], rotation[size - 1 - slot]
            if first is None or second is None:
                continue
            pairs.append((first, second) if (round_index + slot) % 2 == 0 else (second, first))
        rounds.append(pairs)
        rotation = [rotation[0], rotation[-1], *rotation[1:-1]]

    played = {team_id: 0 for team_id in team_ids}
    schedule: list[tuple[int, int]] = []
    cycle = 0
    while min(played.values()) < games_per_team and cycle < 24:
        for pairs in rounds:
            for home, away in pairs:
                if cycle % 2:
                    home, away = away, home
                if played[home] >= games_per_team or played[away] >= games_per_team:
                    continue
                schedule.append((home, away))
                played[home] += 1
                played[away] += 1
        cycle += 1
    return schedule


def _assign_dates(schedule: Sequence[tuple[int, int]], start: date, end: date) -> list[date]:
    """Spread the schedule over the calendar, never twice for one team on one day."""
    span = max(1, (end - start).days)
    last_played: dict[int, date] = {}
    dates: list[date] = []
    for index, (home, away) in enumerate(schedule):
        day = start + timedelta(days=int(index * span / max(1, len(schedule))))
        while last_played.get(home) == day or last_played.get(away) == day:
            day += timedelta(days=1)
        last_played[home] = day
        last_played[away] = day
        dates.append(day)
    return dates


def _team_shooting_line(
    rng: random.Random, era: Era, team: SeasonTeam, possessions: float, points: int
) -> dict[str, int]:
    """Team shooting totals that produce exactly ``points``.

    Attempts come from the era's shape and the team's style; the makes are then solved so
    ``2*FGM + 3PM + FTM`` is the final score, which is what lets every player line add up.
    """
    fga = max(55, int(round(era.fga_per_100 * possessions / 100 * rng.uniform(0.94, 1.06))))
    fg3a = int(round(fga * era.fg3a_share * team.three_lean * rng.uniform(0.85, 1.15)))
    fg3a = max(0, min(fg3a, int(fga * 0.62)))
    fta = max(4, int(round(fga * era.ftr * rng.uniform(0.8, 1.25))))
    fg3m = int(round(fg3a * clamp(rng.gauss(era.fg3_pct, 0.09), 0.05, 0.62)))
    ftm = int(round(fta * clamp(rng.gauss(era.ft_pct, 0.07), 0.4, 1.0)))

    # Solve 2*FGM + 3PM + FTM = points. The attempts move around the makes, never the other
    # way, so the points identity holds exactly while the percentages stay believable.
    if (points - fg3m - ftm) % 2:
        if ftm < fta:
            ftm += 1
        elif ftm > 0:
            ftm -= 1
        else:
            fta += 1
            ftm += 1
    while points - fg3m - ftm < 0 and ftm >= 2:
        ftm -= 2
    while points - fg3m - ftm < 2 * fg3m and fg3m >= 2:
        fg3m -= 2
    fgm = (points - fg3m - ftm) // 2

    two_makes = fgm - fg3m
    two_attempts = fga - fg3a
    floor_attempts = int(two_makes / 0.62) + 1
    if two_attempts < floor_attempts:
        fga = fg3a + floor_attempts
    elif two_makes and two_attempts > two_makes / 0.34 and fga > 62:
        fga = fg3a + max(1, int(two_makes / 0.46))
    fga = max(fga, fgm + 4)
    fg3a = max(fg3a, fg3m)
    return {
        "pts": points,
        "fga": fga,
        "fgm": fgm,
        "fg3a": fg3a,
        "fg3m": fg3m,
        "fta": fta,
        "ftm": ftm,
    }


def _steals_from(rng: random.Random, opponent_turnovers: int) -> int:
    """Steals as a share of the opponent's turnovers — never more than they gave away."""
    share = clamp(rng.gauss(0.49, 0.09), 0.20, 0.90)
    return max(0, min(opponent_turnovers, int(round(opponent_turnovers * share))))


def _fill_rebounds_and_playmaking(
    rng: random.Random, era: Era, line: dict[str, int], opponent: dict[str, int], possessions: float
) -> None:
    """Rebounds, assists and the defensive counters, sized against the opponent's misses."""
    own_misses = REBOUNDABLE_SHARE * (
        (line["fga"] - line["fgm"]) + 0.45 * (line["fta"] - line["ftm"])
    )
    opponent_misses = REBOUNDABLE_SHARE * (
        (opponent["fga"] - opponent["fgm"]) + 0.45 * (opponent["fta"] - opponent["ftm"])
    )
    oreb_share = clamp(rng.gauss(era.oreb_share, 0.05), 0.12, 0.48)
    line["oreb"] = max(0, int(round(own_misses * oreb_share)))
    line["dreb"] = max(0, int(round(opponent_misses * (1.0 - oreb_share))))
    line["reb"] = line["oreb"] + line["dreb"]
    line["ast"] = max(0, min(line["fgm"], int(round(line["fgm"] * clamp(
        rng.gauss(era.ast_per_fgm, 0.06), 0.35, 0.85)))))
    line["tov"] = max(2, int(round(era.tov_per_100 * possessions / 100 * rng.uniform(0.75, 1.3))))
    line["pf"] = max(4, int(round(era.pf_per_100 * possessions / 100 * rng.uniform(0.8, 1.2))))
    line["blk"] = max(0, int(round(era.blk_per_100 * possessions / 100 * rng.uniform(0.5, 1.6))))
    line["stl"] = 0  # filled once both turnover counts exist


def _game_rotation(rng: random.Random, team: SeasonTeam, size: int) -> list[dict[str, Any]]:
    """Pick who plays tonight and how long, with the minutes summing to exactly 240."""
    roster = team.roster
    available = list(roster)
    rest = [spot for spot in available if rng.random() < 0.08]
    if len(available) - len(rest) < size:
        rest = rest[: max(0, len(available) - size)]
    active = [spot for spot in available if spot not in rest][: max(size, 5)]
    if len(active) < 5:
        active = available[:5]

    weights = [spot.minutes_share * rng.uniform(0.85, 1.15) for spot in active]
    halves = allocate(int(TEAM_MINUTES * 2), weights)
    rows: list[dict[str, Any]] = []
    for index, (spot, half_minutes) in enumerate(zip(active, halves)):
        rows.append(
            {
                "player_id": spot.player.player_id,
                "spot": spot,
                "started": index < 5,
                "minutes": half_minutes / 2.0,
            }
        )
    return rows


#: Per-player, per-game "form" shock. Real box scores are strongly OVER-dispersed: a 25-point
#: scorer has a game-to-game standard deviation near 8, so variance/mean is about 2.8, not the
#: 1.0 a Poisson process would give. Splitting a nearly-fixed team total with only a narrow
#: jitter produces the opposite — implausibly consistent players — which silently collapses the
#: projection intervals to the Poisson floor and makes the whole dispersion correction
#: undemonstrable.
#:
#: A gamma shock with mean 1 fixes it at the source: a gamma-mixed Poisson count IS negative
#: binomial, which is the distribution the projection model assumes, so the synthetic league and
#: the model now agree about the shape of the world. The coefficients below were calibrated
#: against real variance-to-mean ratios (see tests/test_seed.py).
FORM_SHOCK_CV = {"usage": 0.42, "three": 0.40, "ft": 0.40, "reb": 0.54,
                 "ast": 0.66, "defense": 0.60}


def _form_shock(rng: random.Random, cv: float) -> float:
    """A gamma variate with mean 1 and coefficient of variation ``cv``.

    ``gammavariate(shape, scale)`` has mean shape*scale and variance shape*scale**2, so
    shape = 1/cv**2 and scale = cv**2 give mean 1 and variance cv**2.
    """
    if cv <= 0:
        return 1.0
    shape = 1.0 / (cv * cv)
    return rng.gammavariate(shape, cv * cv)


def _distribute_team_line(
    rng: random.Random, era: Era, line: dict[str, int], rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Hand every team total to the players, so the sums reconcile exactly."""
    minutes = [row["minutes"] for row in rows]
    archetypes = [row["spot"].player.archetype for row in rows]
    ratings = [row["spot"].rating for row in rows]

    usage_weights = [
        m * a.usage * (0.75 + 0.35 * r) * _form_shock(rng, FORM_SHOCK_CV["usage"])
        for m, a, r in zip(minutes, archetypes, ratings)
    ]
    fga = allocate(line["fga"], usage_weights)

    three_weights = [
        max(0.02, f * a.three_lean * _form_shock(rng, FORM_SHOCK_CV["three"]))
        for f, a in zip(fga, archetypes)
    ]
    fg3a = allocate(line["fg3a"], three_weights, caps=fga)

    ft_weights = [
        max(0.05, m * a.ft_lean * (0.8 + 0.4 * r) * _form_shock(rng, FORM_SHOCK_CV["ft"]))
        for m, a, r in zip(minutes, archetypes, ratings)
    ]
    fta = allocate(line["fta"], ft_weights)

    three_make_weights = [
        max(0.001, t * a.fg3_skill * (0.85 + 0.2 * r)) for t, a, r in zip(fg3a, archetypes, ratings)
    ]
    fg3m = allocate(line["fg3m"], three_make_weights, caps=fg3a)

    two_attempts = [f - t for f, t in zip(fga, fg3a)]
    two_make_weights = [
        max(0.001, t * a.fg2_skill * (0.85 + 0.2 * r))
        for t, a, r in zip(two_attempts, archetypes, ratings)
    ]
    two_makes = allocate(line["fgm"] - line["fg3m"], two_make_weights, caps=two_attempts)

    ft_make_weights = [
        max(0.001, t * a.ft_skill * (0.9 + 0.15 * r)) for t, a, r in zip(fta, archetypes, ratings)
    ]
    ftm = allocate(line["ftm"], ft_make_weights, caps=fta)

    def by_rate(total: int, rate: str, shocks: list[float] | None = None) -> list[int]:
        draws = shocks or [_form_shock(rng, FORM_SHOCK_CV["reb"]) for _ in minutes]
        weights = [
            max(0.01, m * getattr(a, rate) * shock)
            for m, a, shock in zip(minutes, archetypes, draws)
        ]
        return allocate(total, weights)

    # A big rebounding night shows up on both boards, so the two categories share one draw.
    # Independent draws cancel on the sum and leave total rebounds under-dispersed.
    reb_shocks = [_form_shock(rng, FORM_SHOCK_CV["reb"]) for _ in minutes]
    oreb = by_rate(line["oreb"], "oreb", reb_shocks)
    dreb = by_rate(line["dreb"], "dreb", reb_shocks)
    assists = allocate(
        line["ast"],
        [max(0.01, m * a.ast * (0.8 + 0.3 * r) * shock)
         for m, a, r, shock in zip(minutes, archetypes, ratings,
                                   [_form_shock(rng, FORM_SHOCK_CV["ast"]) for _ in minutes])],
    )
    steals = by_rate(line["stl"], "stl",
                     [_form_shock(rng, FORM_SHOCK_CV["defense"]) for _ in minutes])
    blocks = by_rate(line["blk"], "blk",
                     [_form_shock(rng, FORM_SHOCK_CV["defense"]) for _ in minutes])
    turnovers = allocate(
        line["tov"],
        [max(0.01, f * 0.55 + m * a.tov * 0.45) for f, m, a in zip(fga, minutes, archetypes)],
    )
    fouls = by_rate(line["pf"], "pf")

    for index, row in enumerate(rows):
        row["fga"] = fga[index]
        row["fg3a"] = fg3a[index]
        row["fta"] = fta[index]
        row["fg3m"] = fg3m[index]
        row["fgm"] = two_makes[index] + fg3m[index]
        row["ftm"] = ftm[index]
        row["oreb"] = oreb[index]
        row["dreb"] = dreb[index]
        row["reb"] = oreb[index] + dreb[index]
        row["ast"] = assists[index]
        row["stl"] = steals[index]
        row["blk"] = blocks[index]
        row["tov"] = turnovers[index]
        row["pf"] = fouls[index]
        row["pts"] = 2 * row["fgm"] + row["fg3m"] + row["ftm"]
    return rows


def _assign_plus_minus(rng: random.Random, rows: list[dict[str, Any]], margin: int) -> None:
    """Player plus/minus summing to five times the final margin — the on-court identity."""
    target = 5 * margin
    raw = [
        margin * (row["minutes"] / 48.0) * rng.uniform(0.5, 1.8) for row in rows
    ]
    values = [int(round(value)) for value in raw]
    index = 0
    guard = 0
    while sum(values) != target and guard < 2000:
        step = 1 if sum(values) < target else -1
        values[index % len(values)] += step
        index += 1
        guard += 1
    for row, value in zip(rows, values):
        row["plus_minus"] = value


def _possessions(line: dict[str, int], opponent: dict[str, int]) -> float:
    """Possessions in a game, Dean Oliver's estimate averaged over the two teams.

    Averaging is not a nicety. Two teams necessarily play the same number of possessions, so
    estimating each side separately (a bad shooting team collects more offensive rebounds,
    which *subtract* from its count) makes net rating drift away from the actual scoring
    margin. Averaging keeps ``off_rtg - def_rtg`` equal to the margin per 100.

    :data:`POSSESSION_CALIBRATION` then trims the couple of percent by which the box-score
    estimate exceeds the league's play-by-play possession count.

    This is deliberately **not** :func:`nbastats.metrics.possessions`, which is the standard
    single-team Basketball-Reference estimate (``FGA + 0.44*FTA - ORB + TOV``) that the ingest
    aggregator and every metric adapter use for real data. The two differ by roughly 2-2.5%, so
    running :func:`nbastats.ingest.aggregate.recompute_season` over a seeded demo database
    rewrites every team rating it wrote here — once, and then idempotently. That is expected:
    the aggregator is canonical for ingested data, and this one exists so the synthetic league
    is internally consistent. Do not "reconcile" them by pointing the aggregator at this
    function; real NBA data has to use the standard estimate.
    """
    own = line["fga"] + 0.44 * line["fta"] - line["oreb"] + line["tov"]
    theirs = opponent["fga"] + 0.44 * opponent["fta"] - opponent["oreb"] + opponent["tov"]
    return max(1.0, POSSESSION_CALIBRATION * 0.5 * (own + theirs))


def _game_score(row: dict[str, Any]) -> float:
    """Hollinger's Game Score, exactly as the metric catalog defines it."""
    return (
        row["pts"] + 0.4 * row["fgm"] - 0.7 * row["fga"] - 0.4 * (row["fta"] - row["ftm"])
        + 0.7 * row["oreb"] + 0.3 * row["dreb"] + row["stl"] + 0.7 * row["ast"]
        + 0.7 * row["blk"] - 0.4 * row["pf"] - row["tov"]
    )


def _fantasy_points(row: dict[str, Any]) -> float:
    return (
        row["pts"] + 1.2 * row["reb"] + 1.5 * row["ast"]
        + 3.0 * row["stl"] + 3.0 * row["blk"] - row["tov"]
    )


def _pie_numerator(row: dict[str, Any]) -> float:
    """The box-score events PIE credits to one player."""
    return (
        row["pts"] + row["fgm"] + row["ftm"] - row["fga"] - row["fta"]
        + row["dreb"] + 0.5 * row["oreb"] + row["ast"] + row["stl"]
        + 0.5 * row["blk"] - row["pf"] - row["tov"]
    )


def _player_advanced(
    row: dict[str, Any],
    team: dict[str, int],
    opponent: dict[str, int],
    pie_denominator: float,
    pace: float,
) -> dict[str, float | None]:
    """Per-game advanced line for one player, from the box scores of both teams."""
    minutes = row["minutes"]
    if minutes <= 0:
        return {key: None for key in PLAYER_GAME_ADVANCED_METRIC_COLUMNS}

    scoring_possessions = row["fga"] + 0.44 * row["fta"] + row["tov"]
    team_scoring_possessions = team["fga"] + 0.44 * team["fta"] + team["tov"]
    minute_share = minutes / (TEAM_MINUTES / 5.0)
    team_possessions = _possessions(team, opponent)
    player_possessions = team_possessions * minutes / 48.0

    # Oliver-style individual rating: points produced (the player's own, plus credit for the
    # ones they set up) per 100 possessions they used. Calibrated so the league lands near
    # the 108-112 range real player offensive ratings sit in.
    points_produced = row["pts"] + 0.85 * row["ast"]
    individual_possessions = scoring_possessions + 0.30 * row["ast"]
    off_rtg = safe_div(100.0 * points_produced, individual_possessions)
    if off_rtg is not None:
        off_rtg = clamp(off_rtg, 75.0, 150.0)
    team_def_rtg = safe_div(100.0 * opponent["pts"], team_possessions)
    stops = row["stl"] + 0.6 * row["blk"] + 0.25 * row["dreb"]
    def_rtg = None
    if team_def_rtg is not None:
        def_rtg = clamp(team_def_rtg - (stops / max(minutes, 1.0)) * 24.0, 85.0, 135.0)

    return {
        "off_rtg": off_rtg,
        "def_rtg": def_rtg,
        "net_rtg": None if off_rtg is None or def_rtg is None else off_rtg - def_rtg,
        "ast_pct": safe_div(row["ast"], minute_share * team["fgm"] - row["fgm"]),
        "ast_tov": safe_div(row["ast"], row["tov"]),
        "ast_ratio": safe_div(100.0 * row["ast"], scoring_possessions + row["ast"]),
        "oreb_pct": safe_div(row["oreb"], minute_share * (team["oreb"] + opponent["dreb"])),
        "dreb_pct": safe_div(row["dreb"], minute_share * (team["dreb"] + opponent["oreb"])),
        "reb_pct": safe_div(row["reb"], minute_share * (team["reb"] + opponent["reb"])),
        "tov_pct": safe_div(row["tov"], scoring_possessions),
        "efg_pct": safe_div(row["fgm"] + 0.5 * row["fg3m"], row["fga"]),
        "ts_pct": safe_div(row["pts"], 2 * (row["fga"] + 0.44 * row["fta"])),
        "usg_pct": safe_div(scoring_possessions, minute_share * team_scoring_possessions),
        "pace": pace,
        "poss": player_possessions,
        "pie": safe_div(_pie_numerator(row), pie_denominator),
        "game_score": _game_score(row),
    }


# --------------------------------------------------------------------------- public API


def _clear(session: Session) -> None:
    """Empty every table so the seeder is idempotent."""
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(delete(table))
    session.flush()


def seed_database(
    session: Session,
    as_of: date | None = None,
    seasons: Sequence[str] | None = None,
    games_per_team: int = 82,
    players_per_team: int = 10,
    include_playoffs: bool = True,
) -> dict[str, Any]:
    """Fill the database with the synthetic league and return a summary.

    ``as_of`` is "today" for the most recent season: games on or before it are ``final``,
    later ones are ``scheduled`` with no box score. ``seasons`` defaults to
    :data:`DEFAULT_SEASONS`. ``games_per_team`` and ``players_per_team`` shrink the league
    for tests; the defaults are a full 82-game season with ten-man rotations.

    The seeder is idempotent: it clears every table first, so re-running replaces the
    league rather than doubling it.
    """
    started = time.perf_counter()
    _clear(session)
    generator = LeagueGenerator(
        session=session,
        as_of=as_of or DEFAULT_AS_OF,
        seasons=list(seasons or DEFAULT_SEASONS),
        games_per_team=games_per_team,
        players_per_team=players_per_team,
        include_playoffs=include_playoffs,
    )
    summary = generator.run()
    summary["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``python3 -m nbastats.seed --db sqlite:///./hardwood.db``."""
    parser = argparse.ArgumentParser(description="Seed the Hardwood demo league.")
    parser.add_argument("--db", dest="database_url", default=None, help="SQLAlchemy URL.")
    parser.add_argument(
        "--as-of", dest="as_of", default=None,
        help="ISO date the in-progress season has played through (default 2026-01-02).",
    )
    parser.add_argument(
        "--seasons", default=None,
        help="Comma-separated season strings (default: %s)." % ",".join(DEFAULT_SEASONS),
    )
    parser.add_argument("--games-per-team", type=int, default=82)
    parser.add_argument("--players-per-team", type=int, default=10)
    parser.add_argument("--no-playoffs", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    engine = create_db_engine(args.database_url)
    init_db(engine)
    seasons = args.seasons.split(",") if args.seasons else None
    as_of = date.fromisoformat(args.as_of) if args.as_of else None

    with Session(engine, future=True) as session:
        summary = seed_database(
            session,
            as_of=as_of,
            seasons=seasons,
            games_per_team=args.games_per_team,
            players_per_team=args.players_per_team,
            include_playoffs=not args.no_playoffs,
        )
        session.commit()

    if not args.quiet:
        print(f"Seeded {engine.url}")
        for key, value in summary.items():
            print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
