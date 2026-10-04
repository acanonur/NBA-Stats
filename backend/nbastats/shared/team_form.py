"""Team scoring, points allowed, recent form and opponent-adjusted scoring.

This is the matchup page's arithmetic, written once so the NBA and the EuroLeague cannot
disagree about what "points allowed" means. It answers, for a team and a scope of final
games: how many do they score, how many do they let the other side score, what did the
last few games look like, and how does that compare with what their opponents usually do?

Input and scope
---------------
The caller hands in :class:`TeamGame` rows, one per team per final game: the team's own score,
its opponent's score, whether it was home, and (optionally) the neutral-site flag, the team's
playing time and the overtime count. The caller decides the scope (a season and season type
for the NBA; a season and phase set for the EuroLeague) and what counts as final (EuroLeague
games whose box score failed an invariant are not final here). Nothing in this module knows
about a database, a season string or a league other than through the :class:`LeagueProfile`
it is given. Build one :class:`LeagueIndex` per scope and ask it for every team.

The numbers
-----------
For a team with final games ``g = 1..n``:

* ``games``, ``wins``, ``losses``. A win is a higher score; a tied score (which basketball
  cannot produce, so it can only be bad data) is neither a win nor a loss.
* ``points_per_game`` = mean of own points; ``points_allowed_per_game`` = mean of opponent
  points; ``differential_per_game`` is their difference. Points allowed answers "how many do
  they let opponents score".
* ``points_per_regulation`` and ``points_allowed_per_regulation`` scale each game to
  regulation length, ``pts * R / t`` with ``R`` the team's regulation time and ``t`` the
  team time actually played, so an overtime game does not inflate a defence's average. They
  are ``None`` when ``t`` is unknown for *any* game: a partial mean would be a different
  quantity pretending to be this one.
* ``latest_game`` and ``form`` (the last ``N`` games, newest first, ``N`` between 3 and 15).
  ``last_n`` and ``last10`` summarise the same windows. A window longer than the games played
  simply holds the games played, and says how many.
* ``venue_splits``: home, away and neutral. A game flagged neutral goes **only** in the
  neutral split. A game whose neutral flag is unknown is classed by its home flag, which is
  an assumption the NBA always makes because it does not record neutral sites; the neutral
  split is ``None`` whenever no game in scope has a known neutral flag, since "not tracked"
  and "tracked, none played" are different statements.
* ``adjusted_points_against``: the mean, over qualifying games, of the opponent's points in
  that game minus the opponent's usual output *in its other games in scope*. Negative means
  the team holds opponents below what they normally score. ``adjusted_points_for`` is the
  same idea from the other side: own points minus what that opponent usually allows in its
  other games. A game qualifies when its opponent has at least two other games, and the
  value is ``None`` unless at least five games qualify. Leaving the game itself out of the
  opponent's baseline matters: otherwise a team's own score is part of the yardstick it is
  measured against.

No ranks anywhere: a rank after two games is a coin flip presented as a standing.

Exactness
---------
Sums use :func:`math.fsum`, so means of integer scores are correctly rounded and a hand
calculation matches to the last bit.

Pure and stdlib-only. :meth:`TeamForm.to_payload` renders the wire shape of design sections
7.1 and 9.4; opponents are rendered by a callable the caller supplies (``LeagueTeamRef``
needs names the pure core does not have).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Hashable, Iterable, Mapping

from .league_profile import LeagueProfile
from .refs import iso_date

__all__ = [
    "TeamGame",
    "WindowSummary",
    "Split",
    "VenueSplits",
    "Adjusted",
    "LeagueAverage",
    "TeamForm",
    "LeagueIndex",
    "compute_team_form",
    "form_game_payload",
]


@dataclass(frozen=True)
class TeamGame:
    """One team's side of one final game."""

    game_id: str
    date: date
    team: Hashable
    opponent: Hashable
    is_home: bool
    pts: float
    opp_pts: float
    #: ``None`` means unknown, and is never read as "not neutral".
    is_neutral: bool | None = None
    #: Team playing time in seconds (the NBA reader multiplies ``team_game.minutes`` by 60).
    team_seconds: float | None = None
    overtime_periods: int | None = None
    tipoff_utc: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("pts", "opp_pts"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a number, got {value!r}")
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite, got {value!r}")
        if self.team == self.opponent:
            raise ValueError(f"game {self.game_id}: a team cannot play itself")

    @property
    def result(self) -> str | None:
        """``W`` or ``L`` from the score; ``None`` for a tied score."""
        if self.pts > self.opp_pts:
            return "W"
        if self.pts < self.opp_pts:
            return "L"
        return None


def _mean(values: list[float]) -> float | None:
    return math.fsum(values) / len(values) if values else None


def _naive_utc(value: datetime | None) -> datetime:
    """A comparable naive-UTC stamp; a missing tip-off sorts as the earliest of its day."""
    if value is None:
        return datetime.min
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _newest_first(games: Iterable[TeamGame]) -> list[TeamGame]:
    return sorted(games, key=lambda g: (g.date, _naive_utc(g.tipoff_utc), g.game_id), reverse=True)


@dataclass(frozen=True)
class WindowSummary:
    """Scoring over the most recent ``window`` games (fewer if fewer were played)."""

    window: int
    games: int
    points_per_game: float | None
    points_allowed_per_game: float | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "window": self.window,
            "games": self.games,
            "pointsPerGame": self.points_per_game,
            "pointsAllowedPerGame": self.points_allowed_per_game,
        }


@dataclass(frozen=True)
class Split:
    games: int
    points_per_game: float | None
    points_allowed_per_game: float | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "games": self.games,
            "pointsPerGame": self.points_per_game,
            "pointsAllowedPerGame": self.points_allowed_per_game,
        }


@dataclass(frozen=True)
class VenueSplits:
    home: Split
    away: Split
    #: ``None`` when the neutral flag is not known for any game in scope.
    neutral: Split | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "home": self.home.to_payload(),
            "away": self.away.to_payload(),
            "neutral": self.neutral.to_payload() if self.neutral is not None else None,
        }


@dataclass(frozen=True)
class Adjusted:
    """An opponent-adjusted mean and the number of games that qualified for it."""

    value: float | None
    games: int

    def to_payload(self) -> dict[str, Any]:
        return {"value": self.value, "games": self.games}


@dataclass(frozen=True)
class LeagueAverage:
    """Mean points per team-game over the whole scope, which equals league points allowed."""

    points_per_game: float | None
    teams: int

    def to_payload(self) -> dict[str, Any]:
        return {"pointsPerGame": self.points_per_game, "teams": self.teams}


def form_game_payload(game: TeamGame, opponent_ref: Mapping[str, Any]) -> dict[str, Any]:
    """The ``FormGame`` wire shape for one game, given the opponent's ``LeagueTeamRef``."""
    return {
        "gameId": game.game_id,
        "date": iso_date(game.date),
        "opponent": dict(opponent_ref),
        "isHome": game.is_home,
        "isNeutral": game.is_neutral,
        "teamScore": game.pts,
        "opponentScore": game.opp_pts,
        "result": game.result,
        "overtimePeriods": game.overtime_periods,
    }


@dataclass(frozen=True)
class TeamForm:
    """Everything design section 7.1 lists for one team."""

    team: Hashable
    games: int
    wins: int
    losses: int
    points_per_game: float | None
    points_allowed_per_game: float | None
    differential_per_game: float | None
    points_per_regulation: float | None
    points_allowed_per_regulation: float | None
    latest_game: TeamGame | None
    form: tuple[TeamGame, ...]
    last_n: WindowSummary
    last10: WindowSummary
    venue_splits: VenueSplits
    adjusted_points_against: Adjusted
    adjusted_points_for: Adjusted

    def to_payload(self, opponent_ref: Callable[[Hashable], Mapping[str, Any]]) -> dict[str, Any]:
        """The team's block of ``TeamMatchup.teams[]`` (the fields this module owns).

        ``opponent_ref`` maps an opponent key to its ``LeagueTeamRef``. The caller adds
        ``side``, ``team``, ``availability`` and ``defenseSummary``.
        """

        def render(game: TeamGame | None) -> dict[str, Any] | None:
            if game is None:
                return None
            return form_game_payload(game, opponent_ref(game.opponent))

        return {
            "record": {"wins": self.wins, "losses": self.losses},
            "games": self.games,
            "pointsPerGame": self.points_per_game,
            "pointsAllowedPerGame": self.points_allowed_per_game,
            "differentialPerGame": self.differential_per_game,
            "pointsPerRegulation": self.points_per_regulation,
            "pointsAllowedPerRegulation": self.points_allowed_per_regulation,
            "latestGame": render(self.latest_game),
            "form": [render(game) for game in self.form],
            "lastN": self.last_n.to_payload(),
            "last10": self.last10.to_payload(),
            "venueSplits": self.venue_splits.to_payload(),
            "adjustedPointsAgainst": self.adjusted_points_against.to_payload(),
            "adjustedPointsFor": self.adjusted_points_for.to_payload(),
        }


class LeagueIndex:
    """Every team-game in one scope, indexed by team, built once and queried per team.

    Each game appears once per side. The index keeps both sides because opponent-adjusted
    values need the *other* team's games, and the league average needs them all.
    """

    def __init__(self, games: Iterable[TeamGame]) -> None:
        by_team: dict[Hashable, list[TeamGame]] = {}
        seen: set[tuple[Hashable, str]] = set()
        for game in games:
            key = (game.team, game.game_id)
            if key in seen:
                raise ValueError(f"duplicate row for team {game.team!r} in game {game.game_id}")
            seen.add(key)
            by_team.setdefault(game.team, []).append(game)
        self._by_team = {team: _newest_first(rows) for team, rows in by_team.items()}

    @property
    def teams(self) -> tuple[Hashable, ...]:
        return tuple(self._by_team)

    def games_for(self, team: Hashable) -> list[TeamGame]:
        """The team's games, newest first (a copy); empty for a team with none."""
        return list(self._by_team.get(team, ()))

    def league_average(self) -> LeagueAverage:
        points = [game.pts for rows in self._by_team.values() for game in rows]
        return LeagueAverage(points_per_game=_mean(points), teams=len(self._by_team))


def _window(games: list[TeamGame], size: int) -> WindowSummary:
    chosen = games[:size]
    return WindowSummary(
        window=size,
        games=len(chosen),
        points_per_game=_mean([g.pts for g in chosen]),
        points_allowed_per_game=_mean([g.opp_pts for g in chosen]),
    )


def _split(games: list[TeamGame]) -> Split:
    return Split(
        games=len(games),
        points_per_game=_mean([g.pts for g in games]),
        points_allowed_per_game=_mean([g.opp_pts for g in games]),
    )


def _venue_splits(games: list[TeamGame]) -> VenueSplits:
    neutral = [g for g in games if g.is_neutral is True]
    home = [g for g in games if g.is_neutral is not True and g.is_home]
    away = [g for g in games if g.is_neutral is not True and not g.is_home]
    tracked = any(g.is_neutral is not None for g in games)
    return VenueSplits(
        home=_split(home),
        away=_split(away),
        neutral=_split(neutral) if tracked else None,
    )


def _regulation_mean(games: list[TeamGame], regulation_seconds: int, field: str) -> float | None:
    if not games:
        return None
    scaled: list[float] = []
    for game in games:
        seconds = game.team_seconds
        if seconds is None or not seconds > 0:
            return None
        scaled.append(getattr(game, field) * regulation_seconds / seconds)
    return math.fsum(scaled) / len(scaled)


def _adjusted(
    index: LeagueIndex, games: list[TeamGame], min_other: int, min_games: int
) -> tuple[Adjusted, Adjusted]:
    against: list[float] = []
    scored: list[float] = []
    for game in games:
        others = [row for row in index.games_for(game.opponent) if row.game_id != game.game_id]
        if len(others) < min_other:
            continue
        usual_scored = math.fsum(row.pts for row in others) / len(others)
        usual_allowed = math.fsum(row.opp_pts for row in others) / len(others)
        against.append(game.opp_pts - usual_scored)
        scored.append(game.pts - usual_allowed)
    qualifying = len(against)
    if qualifying < min_games:
        return Adjusted(None, qualifying), Adjusted(None, qualifying)
    return (
        Adjusted(math.fsum(against) / qualifying, qualifying),
        Adjusted(math.fsum(scored) / qualifying, qualifying),
    )


def compute_team_form(
    index: LeagueIndex,
    team: Hashable,
    *,
    profile: LeagueProfile,
    form_window: int | None = None,
) -> TeamForm:
    """Design section 7.1 for ``team`` within ``index``'s scope.

    ``form_window`` defaults to the profile's (5) and must lie within its range (3 to 15);
    a value outside it raises ``ValueError`` so a route can answer 400 instead of silently
    clamping a request.
    """
    rules = profile.matchup
    window = rules.form_window_default if form_window is None else form_window
    if isinstance(window, bool) or not isinstance(window, int):
        raise ValueError(f"form window must be an integer, got {window!r}")
    if not rules.form_window_min <= window <= rules.form_window_max:
        raise ValueError(
            f"form window must be between {rules.form_window_min} and {rules.form_window_max}, "
            f"got {window}"
        )

    games = index.games_for(team)
    ppg = _mean([g.pts for g in games])
    papg = _mean([g.opp_pts for g in games])
    against, scored = _adjusted(
        index, games, rules.adjusted_min_other_games, rules.adjusted_min_games
    )
    return TeamForm(
        team=team,
        games=len(games),
        wins=sum(1 for g in games if g.result == "W"),
        losses=sum(1 for g in games if g.result == "L"),
        points_per_game=ppg,
        points_allowed_per_game=papg,
        differential_per_game=(ppg - papg) if ppg is not None and papg is not None else None,
        points_per_regulation=_regulation_mean(games, profile.team_regulation_seconds, "pts"),
        points_allowed_per_regulation=_regulation_mean(
            games, profile.team_regulation_seconds, "opp_pts"
        ),
        latest_game=games[0] if games else None,
        form=tuple(games[:window]),
        last_n=_window(games, window),
        last10=_window(games, 10),
        venue_splits=_venue_splits(games),
        adjusted_points_against=against,
        adjusted_points_for=scored,
    )
