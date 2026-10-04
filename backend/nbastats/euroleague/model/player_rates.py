"""Player rates and minutes: the workbook's prior, carried forward by the box scores since.

The workbook gives every player a projected number of minutes and a per-40 rate for each stat,
and says how both are to be updated as games are played (Method sections 2 and 3). This module is
that update, and it exists to fix one thing the workbook cannot: **the update must count a game
exactly once.** The imported rates already contain Rounds 1 and 2 (that is what "as of round 2"
means), so adding Round 2's box score to them again would double-count it. The update here uses
*only games with a round after the row's ``as_of_round``*, which is also why a later workbook
import (rows as of round 3) simply replaces the base and the arithmetic carries on from there.

The two updates
---------------
**Rates** (Method, "Player scoring rates": "every EuroLeague minute and point this season is
added to the prior rate. The prior counts as 400 minutes for players with 2025-26 EuroLeague
minutes and 150 for estimated lines")::

    rate40 = (rate40_prior * P + 40 * sum(stat_after)) / (P + sum(minutes_after))

``P`` is the row's ``prior_minutes`` (400 for an official basis, 150 otherwise). The
percentages are updated through their *makes*: a prior of 38% on 4 attempts per 40 is 1.52
makes per 40, which is blended like any other rate, and the new percentage is the blended makes
over the blended attempts (``None`` when the blended attempts are zero: a percentage with no
attempts behind it is not recorded, never 0).

**Minutes** (Method, "Player minutes": ``w = rounds / (rounds + 3)``)::

    m = ((k + 3) * m_implied + sum(minutes_after)) / (k + 3 + n_after)

with ``k`` the row's ``as_of_round``. That is the workbook's blend continued from where it
stopped: ``(1 - w) * preseason + w * average`` is ``(3 * preseason + total) / (k + 3)``, and
adding ``n_after`` more games to the total and the count is the formula above. ``n_after`` is
the number of the *club's* games after the base round, because the workbook's convention is that
**a healthy player who did not play or did not dress counts as 0 minutes in that round**
(a coach leaving a man out is evidence about his role). A round he was missing through an
entry that says he was out, doubtful or questionable is left out of both sums ("a round missed
through injury gives no minutes evidence"), unless the entry's reason is the coach's own decision or
rest, which are choices about minutes. The deciding is the availability layer's (:meth:`~nbastats.
euroleague.read.availability.AvailabilityBook.excused`); this module takes it as a function.

A squad's minutes are then normalised to 200 (five players for forty minutes) and capped at 32 a
man, the workbook's own rule, by :func:`normalise_minutes`. That is applied only once at least one
game has been added: the imported minutes are the workbook's own, already normalised, and
re-scaling them would move a number nobody changed.

Players with no rate of their own
---------------------------------
A row on the ``positionPrior`` basis (the workbook's "est." lines, when estimates are not imported)
has no rates. It starts from the pooled mean of the players with an official basis at his
three-way position, weighted by projected minutes (a pooled rate is total production over total
minutes), with the 150-minute prior. If there are no such players, or he has no projected minutes,
he has no rate and the model says so (``None``) rather than inventing one.

The null rule
-------------
A stat missing from a line (the workbook's box scores carry no fouls drawn, for instance; none of
the stats updated here is missing from either source today, but a live line may omit one) is left
out of *that stat's* sums, and the minutes of that line out of that stat's minutes. A player
never loses production to a column that was not recorded.

Nothing here touches a database: :func:`build_states` takes plain bases and lines. The loading is
in :func:`load_bases` and :func:`load_lines`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Final, Iterable, Mapping, Sequence

from sqlalchemy import select

from ...shared.positions import euroleague_position_bucket, workbook5_to_gfc
from ..models import ElPlayerGame, ElPlayerRate
from ..read.queries import ReadContext, counts_for_scoring

__all__ = [
    "OFFICIAL_BASES",
    "ESTIMATE_BASES",
    "MINUTES_WEIGHT_OFFSET",
    "TEAM_MINUTES",
    "MINUTES_CAP",
    "RATE_KEYS",
    "PlayerBase",
    "PlayerLine",
    "ClubGame",
    "PlayerState",
    "blend_rate",
    "blend_minutes",
    "normalise_minutes",
    "pooled_position_rates",
    "build_states",
    "load_bases",
    "load_lines",
]

#: Bases whose rates the workbook (or the demo) vouches for; the position prior pools these.
OFFICIAL_BASES: Final[tuple[str, ...]] = ("workbookOfficial", "officialUpdate", "syntheticDemo")
#: Bases that are somebody's estimate, shown as such.
ESTIMATE_BASES: Final[tuple[str, ...]] = ("workbookEstimate", "positionPrior")

#: The ``3`` in ``w = rounds / (rounds + 3)``.
MINUTES_WEIGHT_OFFSET: Final = 3
#: Five players for forty minutes (Settings ``C13``), and the most one player is given.
TEAM_MINUTES: Final = 200.0
MINUTES_CAP: Final = 32.0

#: Per-40 rate key to the box-score column it accumulates.
_COUNT_COLUMN: Final[dict[str, str]] = {
    "pts40": "pts",
    "reb40": "reb",
    "ast40": "ast",
    "fg3m40": "fgm3",
    "stl40": "stl",
    "blk40": "blk",
    "tov40": "tov",
    "fga2_40": "fga2",
    "fga3_40": "fga3",
    "fta40": "fta",
}
#: Percentage key to (makes column, attempts rate key).
_PERCENTAGE: Final[dict[str, tuple[str, str]]] = {
    "fg2_pct": ("fgm2", "fga2_40"),
    "fg3_pct": ("fgm3", "fga3_40"),
    "ft_pct": ("ftm", "fta40"),
}
RATE_KEYS: Final[tuple[str, ...]] = (*_COUNT_COLUMN, *_PERCENTAGE)

#: Columns a line must carry for the model (all nullable in the store).
_LINE_COLUMNS: Final[tuple[str, ...]] = (
    "pts",
    "reb",
    "ast",
    "fgm2",
    "fga2",
    "fgm3",
    "fga3",
    "ftm",
    "fta",
    "stl",
    "blk",
    "tov",
)


# --------------------------------------------------------------------------- inputs


@dataclass(frozen=True)
class PlayerBase:
    """One player's imported row: where the update starts."""

    person_code: str
    club_code: str
    as_of_round: int
    basis: str
    prior_minutes: float
    proj_minutes: float | None
    rates: Mapping[str, float | None]
    #: The three-way bucket (``G``, ``F``, ``C``) the position prior pools by.
    bucket: str | None = None

    @property
    def official(self) -> bool:
        return self.basis in OFFICIAL_BASES


@dataclass(frozen=True)
class PlayerLine:
    """One played line in a game that counts."""

    person_code: str
    club_code: str
    round: int
    game_id: str
    seconds: int | None
    stats: Mapping[str, int | None]


@dataclass(frozen=True)
class ClubGame:
    """A game a club has played that counts (for the denominator of a minutes average)."""

    club_code: str
    round: int
    game_id: str


@dataclass(frozen=True)
class PlayerState:
    """A player's rates and minutes as of a round."""

    person_code: str
    club_code: str
    as_of_round: int
    basis: str
    prior_minutes: float
    minutes: float | None
    rates: Mapping[str, float | None]
    #: Games he played after the base round.
    games_after: int
    #: Club games after the base round that count toward his minutes average.
    club_games_after: int

    @property
    def estimated(self) -> bool:
        return self.basis in ESTIMATE_BASES

    @property
    def per40(self) -> dict[str, float | None]:
        return {
            key: self.rates.get(key)
            for key in ("pts40", "reb40", "ast40", "fg3m40", "stl40", "blk40", "tov40")
        }

    @property
    def points_per_game(self) -> float | None:
        """Expected points in a game: the per-40 scoring rate times minutes over forty."""
        pts, minutes = self.rates.get("pts40"), self.minutes
        if pts is None or minutes is None:
            return None
        return pts * minutes / 40.0


# --------------------------------------------------------------------------- the arithmetic


def blend_rate(prior: float, prior_minutes: float, total: float, minutes: float) -> float:
    """``(prior * P + 40 * total) / (P + minutes)``: the prior counts as ``P`` minutes."""
    if prior_minutes <= 0:
        raise ValueError("the prior must carry a positive number of minutes")
    return (prior * prior_minutes + 40.0 * total) / (prior_minutes + minutes)


def blend_minutes(implied: float, as_of_round: int, observed_minutes: float, games: int) -> float:
    """``((k + 3) * implied + observed) / (k + 3 + games)``: the workbook's blend, continued."""
    weight = as_of_round + MINUTES_WEIGHT_OFFSET
    return (weight * implied + observed_minutes) / (weight + games)


def normalise_minutes(
    minutes: Mapping[str, float], *, total: float = TEAM_MINUTES, cap: float = MINUTES_CAP
) -> dict[str, float]:
    """Scale a squad's minutes to ``total`` with nobody above ``cap``.

    Everybody is scaled by one factor; whoever that pushes past the cap is held at the cap and
    the rest are scaled again to fill what is left, until nobody is over. A squad too small to
    reach ``total`` under the cap ends with every man at the cap (the workbook flags such squads
    rather than inventing minutes).
    """
    values = {k: float(v) for k, v in minutes.items()}
    if not values or math.fsum(values.values()) <= 0:
        return values
    held: set[str] = set()
    for _ in range(len(values) + 1):
        free = [k for k in values if k not in held]
        remaining = total - cap * len(held)
        mass = math.fsum(values[k] for k in free)
        if not free or mass <= 0 or remaining <= 0:
            break
        factor = remaining / mass
        scaled = {k: values[k] * factor for k in free}
        over = [k for k, v in scaled.items() if v > cap + 1e-12]
        if not over:
            values.update(scaled)
            break
        for key in over:
            values[key] = cap
            held.add(key)
    return values


def pooled_position_rates(bases: Iterable[PlayerBase]) -> dict[str, dict[str, float | None]]:
    """The mean rates of the official-basis players at each three-way bucket.

    Weighted by projected minutes (a pooled rate is total production over total minutes). A
    percentage is pooled through makes over attempts. A bucket with no usable player is absent.
    """
    pooled: dict[str, dict[str, float | None]] = {}
    groups: dict[str, list[PlayerBase]] = {}
    for base in bases:
        if base.official and base.bucket is not None and (base.proj_minutes or 0) > 0:
            groups.setdefault(base.bucket, []).append(base)
    for bucket, members in groups.items():
        rates: dict[str, float | None] = {}
        for key in _COUNT_COLUMN:
            terms = [
                (b.proj_minutes, b.rates.get(key)) for b in members if b.rates.get(key) is not None
            ]
            mass = math.fsum(m for m, _ in terms)  # type: ignore[misc]
            rates[key] = math.fsum(m * v for m, v in terms) / mass if mass > 0 else None  # type: ignore[operator,misc]
        for key, (_, attempts_key) in _PERCENTAGE.items():
            makes = attempts = 0.0
            for b in members:
                pct, att = b.rates.get(key), b.rates.get(attempts_key)
                if att is None:
                    continue
                attempts += b.proj_minutes * att  # type: ignore[operator]
                if pct is not None:
                    makes += b.proj_minutes * att * pct  # type: ignore[operator]
            rates[key] = makes / attempts if attempts > 0 else None
        pooled[bucket] = rates
    return pooled


# --------------------------------------------------------------------------- the update


def _updated_rates(
    base_rates: Mapping[str, float | None],
    prior_minutes: float,
    lines: Sequence[PlayerLine],
) -> dict[str, float | None]:
    """The prior rates with the lines added, stat by stat, each over its own recorded minutes."""
    out: dict[str, float | None] = dict(base_rates)

    def totals(columns: Sequence[str]) -> tuple[list[float], float]:
        sums = [0.0] * len(columns)
        minutes = 0.0
        for line in lines:
            if line.seconds is None or line.seconds <= 0:
                continue
            values = [line.stats.get(c) for c in columns]
            if any(v is None for v in values):
                continue
            for i, v in enumerate(values):
                sums[i] += v  # type: ignore[operator]
            minutes += line.seconds / 60.0
        return sums, minutes

    for key, column in _COUNT_COLUMN.items():
        prior = base_rates.get(key)
        if prior is None:
            continue
        (total,), minutes = totals([column])
        out[key] = blend_rate(prior, prior_minutes, total, minutes) if minutes > 0 else prior

    for key, (makes_column, attempts_key) in _PERCENTAGE.items():
        attempts_prior = base_rates.get(attempts_key)
        pct_prior = base_rates.get(key)
        if attempts_prior is None:
            continue
        if pct_prior is not None:
            makes_prior: float | None = pct_prior * attempts_prior
        elif attempts_prior == 0:
            makes_prior = 0.0  # no attempts behind it: the prior holds no makes either
        else:
            makes_prior = None
        if makes_prior is None:
            continue
        attempts_column = _COUNT_COLUMN[attempts_key]
        (makes, _), minutes = totals([makes_column, attempts_column])
        new_makes = (
            blend_rate(makes_prior, prior_minutes, makes, minutes) if minutes > 0 else makes_prior
        )
        new_attempts = out.get(attempts_key)
        if new_attempts is None or new_attempts <= 0:
            out[key] = None
        else:
            out[key] = min(1.0, max(0.0, new_makes / new_attempts))
    return out


def build_states(
    bases: Mapping[str, PlayerBase],
    lines: Mapping[str, Sequence[PlayerLine]],
    club_games: Mapping[str, Sequence[ClubGame]],
    excused: Callable[[str, str], bool],
    as_of_round: int,
    *,
    normalise: bool = True,
    team_minutes: float = TEAM_MINUTES,
    cap: float = MINUTES_CAP,
) -> dict[str, PlayerState]:
    """Every player's state as of ``as_of_round``, using only games after his base round.

    ``bases`` maps person to his imported row; ``lines`` maps person to his played lines (any
    rounds; those outside ``(base round, as_of_round]`` are ignored); ``club_games`` maps a club
    to the games that count for it. ``excused(person, game_id)`` says whether a game he missed
    gives no evidence about his role.
    """
    pooled = pooled_position_rates(bases.values())
    states: dict[str, PlayerState] = {}
    for person, base in bases.items():
        rates: dict[str, float | None] = dict(base.rates)
        if base.basis == "positionPrior" and base.bucket in pooled:
            rates = dict(pooled[base.bucket])
        window = [
            g
            for g in club_games.get(base.club_code, ())
            if base.as_of_round < g.round <= as_of_round
        ]
        window_ids = {g.game_id for g in window}
        played = {
            line.game_id: line
            for line in lines.get(person, ())
            if line.game_id in window_ids and line.club_code == base.club_code
        }
        observed = 0.0
        games_counted = 0
        for game in window:
            line = played.get(game.game_id)
            if line is not None:
                if line.seconds is None:
                    continue  # played, minutes not recorded: no evidence either way
                observed += line.seconds / 60.0
                games_counted += 1
            elif excused(person, game.game_id):
                continue
            else:
                games_counted += 1  # a healthy player who did not play: zero minutes
        minutes = base.proj_minutes
        if minutes is not None and window:
            minutes = blend_minutes(minutes, base.as_of_round, observed, games_counted)
        usable = [line for line in played.values() if line.seconds]
        if usable:
            rates = _updated_rates(rates, base.prior_minutes, usable)
        states[person] = PlayerState(
            person_code=person,
            club_code=base.club_code,
            as_of_round=as_of_round,
            basis=base.basis,
            prior_minutes=base.prior_minutes,
            minutes=minutes,
            rates=rates,
            games_after=len(usable),
            club_games_after=games_counted,
        )
    if normalise:
        _normalise_clubs(states, team_minutes=team_minutes, cap=cap)
    return states


def _normalise_clubs(states: dict[str, PlayerState], *, team_minutes: float, cap: float) -> None:
    by_club: dict[str, list[str]] = {}
    for person, state in states.items():
        by_club.setdefault(state.club_code, []).append(person)
    for people in by_club.values():
        if not any(states[p].club_games_after > 0 for p in people):
            continue  # nothing has been added: the imported minutes are the workbook's own
        with_minutes = {p: states[p].minutes for p in people if states[p].minutes is not None}
        scaled = normalise_minutes(with_minutes, total=team_minutes, cap=cap)  # type: ignore[arg-type]
        for person, value in scaled.items():
            old = states[person]
            states[person] = PlayerState(
                person_code=old.person_code,
                club_code=old.club_code,
                as_of_round=old.as_of_round,
                basis=old.basis,
                prior_minutes=old.prior_minutes,
                minutes=value,
                rates=old.rates,
                games_after=old.games_after,
                club_games_after=old.club_games_after,
            )


# --------------------------------------------------------------------------- loading


def load_bases(ctx: ReadContext) -> dict[str, PlayerBase]:
    """Each person's newest imported rate row for the season, as a :class:`PlayerBase`."""
    rows = ctx.session.execute(
        select(ElPlayerRate)
        .where(ElPlayerRate.season_code == ctx.season_code)
        .order_by(ElPlayerRate.as_of_round)
    ).scalars()
    newest: dict[str, ElPlayerRate] = {}
    for row in rows:  # ascending, so the last write per person is the newest round
        newest[row.person_code] = row
    bases: dict[str, PlayerBase] = {}
    for person, row in newest.items():
        reg = ctx.registrations.get((row.club_code, person))
        bucket = None
        if reg is not None:
            bucket = workbook5_to_gfc(reg.position5_workbook) or euroleague_position_bucket(
                reg.position_code
            )
        bases[person] = PlayerBase(
            person_code=person,
            club_code=row.club_code,
            as_of_round=row.as_of_round,
            basis=row.basis,
            prior_minutes=row.prior_minutes,
            proj_minutes=row.proj_minutes,
            rates={key: getattr(row, key) for key in RATE_KEYS},
            bucket=bucket,
        )
    return bases


def load_lines(
    ctx: ReadContext, after_round: int
) -> tuple[dict[str, list[PlayerLine]], dict[str, list[ClubGame]]]:
    """The played lines, and each club's counting games, for games after ``after_round``.

    Only games in the scoring scope count (final, box score passed its invariants).
    """
    games = {
        g.game_id: g for g in ctx.games if counts_for_scoring(g) and g.round_number > after_round
    }
    lines: dict[str, list[PlayerLine]] = {}
    club_games: dict[str, list[ClubGame]] = {}
    if not games:
        return lines, club_games
    for game in games.values():
        for club in (game.home_club_code, game.away_club_code):
            club_games.setdefault(club, []).append(ClubGame(club, game.round_number, game.game_id))
    rows = ctx.session.execute(
        select(ElPlayerGame).where(
            ElPlayerGame.game_id.in_(list(games)), ElPlayerGame.participation == "played"
        )
    ).scalars()
    for row in rows:
        game = games[row.game_id]
        lines.setdefault(row.person_code, []).append(
            PlayerLine(
                person_code=row.person_code,
                club_code=row.club_code,
                round=game.round_number,
                game_id=row.game_id,
                seconds=row.seconds_played,
                stats={column: getattr(row, column) for column in _LINE_COLUMNS},
            )
        )
    for entries in club_games.values():
        entries.sort(key=lambda g: (g.round, g.game_id))
    return lines, club_games
