"""The rules a box score must obey before Hardwood believes it (design section 4.5).

Why a box score is checked at all
---------------------------------
The live service is somebody else's database, read through a parser nobody has seen run against
a real response. A box score that quietly disagrees with itself (a player whose points do not
add up, a team whose players scored more than the team did, a game whose minutes are short)
would feed every number the app shows: points allowed, defence by position, the squad's
season averages, the projection's form. Checking is cheap and being wrong is not, so a game
that fails a *hard* rule is **quarantined**: its final score stays on record, its lines are not
written (or, if an earlier good copy exists, it is kept as it was), ``stats_status`` becomes
``quarantined`` and the rule that failed is named in ``el_ingest_log.invariant_failed``. Readers
exclude a quarantined game entirely, because which of two disagreeing numbers is wrong is not
knowable from here. A *soft* rule only warns.

Hard rules
----------
``pts_formula``     per played line (players and team): ``pts == 2*fgm2 + 3*fgm3 + ftm``
``reb_sum``         per line: ``reb == oreb + dreb``
``makes_le_attempts``  per line: no more makes than attempts, for 2s, 3s and free throws
``team_points``     the players' points sum to the team's, and to the game's final score
``final_score``     the team line says what the game's final score says
``team_time``       the players' seconds sum to 12000 (five men, forty minutes) plus 1500 for each
                    overtime, within a tolerance (six seconds for the service's whole seconds;
                    the workbook's two-decimal minutes need only three)
``partials``        quarter and overtime scores sum to the final score, and there are at least four
``dnp_has_stats``   a player with zero seconds has no production in his line (live only)
``duplicate_player``  one person appears once in a game (live only)
``club_mismatch``   every player belongs to the club whose side he is on (live only)

The last three are additions for the live service: the workbook cannot produce a duplicate or a
player on the wrong side, but a response that was parsed wrongly can, and each is a way for lines
to be credited to the wrong person or club without any number looking off.

Soft rules
----------
``reb_vs_team`` / ``tov_vs_team``  the players' rebounds and turnovers do not exceed the team's
(team rebounds and turnovers are credited to the team, not to a player, so they can fall short but
never exceed); ``starters``  five per side when the flag is recorded; ``pir_formula``  the
published performance index equals its formula, when fouls drawn and blocks against are known;
``team_clock``  the team's reported game clock is 2400 seconds (or the players' sum) plus the
overtime (live only). The clock rule is soft because the field is documented from one fixture.

A rule that needs a value the source did not record is **skipped, never failed**: absence is not
a violation, and ``None`` is never read as 0.

Overtime
--------
How many periods were played is taken, in order, from the partials (``len - 4``), from the
stored game's ``ot_periods`` (itself set from E2's partials), and last from the team's seconds. A
disagreement between that count and the seconds is a ``team_time`` failure, which is how a
box score with the wrong minutes cannot be explained away by a guessed overtime.

Agreement with the workbook importer
------------------------------------
``importers/workbook.py`` carries its own ``check_game`` with the same rule names, written for
sheets (no club, no person code, no clock). ``tests/euroleague/test_invariants.py`` runs both over
the same cases and fails when they disagree on a rule name or its hardness, so the two cannot
drift apart while they are two functions; the day the importer imports this module the test
becomes trivially true and stays.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Mapping, Sequence

from ..profile import PROFILE
from .parse import BoxScore, BoxSide, PlayerLine

__all__ = [
    "LIVE_SECONDS_TOLERANCE",
    "WORKBOOK_SECONDS_TOLERANCE",
    "HARD_RULES",
    "SOFT_RULES",
    "Line",
    "Violation",
    "GameFacts",
    "GameCheck",
    "check_game",
    "box_to_facts",
    "describe",
    "rule_names",
]

#: Whole seconds from the service: six seconds of slack for a rounded clock.
LIVE_SECONDS_TOLERANCE: Final = 6
#: The workbook's minutes carry two decimals, so three seconds is what rounding can explain.
WORKBOOK_SECONDS_TOLERANCE: Final = 3

HARD_RULES: Final[tuple[str, ...]] = (
    "pts_formula",
    "reb_sum",
    "makes_le_attempts",
    "team_points",
    "final_score",
    "team_time",
    "partials",
    "dnp_has_stats",
    "duplicate_player",
    "club_mismatch",
)
SOFT_RULES: Final[tuple[str, ...]] = (
    "reb_vs_team",
    "tov_vs_team",
    "starters",
    "pir_formula",
    "team_clock",
)

_SIDES: Final = ("home", "away")

_PIR_PARTS: Final = (
    "pts",
    "reb",
    "ast",
    "stl",
    "blk",
    "tov",
    "pf",
    "fgm2",
    "fga2",
    "fgm3",
    "fga3",
    "ftm",
    "fta",
)


@dataclass
class Line:
    """One player's or one team's line, in the shape the rules need.

    The field names are the workbook importer's, so the two ``check_game`` functions take the
    same data. ``side`` is ``"home"`` or ``"away"``; ``None`` is "not recorded"; a ``dnp`` line
    has every stat ``None``. ``club``, ``person_code``, ``unexplained`` and ``clock_seconds``
    are the live service's additions and default to "not known".
    """

    side: str
    name: str | None = None
    participation: str = "played"
    is_starter: bool | None = None
    seconds: int | None = None
    pts: int | None = None
    fgm2: int | None = None
    fga2: int | None = None
    fgm3: int | None = None
    fga3: int | None = None
    ftm: int | None = None
    fta: int | None = None
    oreb: int | None = None
    dreb: int | None = None
    reb: int | None = None
    ast: int | None = None
    stl: int | None = None
    tov: int | None = None
    blk: int | None = None
    blk_against: int | None = None
    pf: int | None = None
    fouls_drawn: int | None = None
    plus_minus: int | None = None
    pir_official: int | None = None
    club: str | None = None
    person_code: str | None = None
    unexplained: tuple[str, ...] = ()
    clock_seconds: int | None = None


@dataclass(frozen=True)
class Violation:
    """A failed rule. ``kind`` is ``hard`` (quarantines the game) or ``soft`` (warns)."""

    rule: str
    kind: str
    message: str


@dataclass
class GameFacts:
    """What :func:`check_game` needs to know about one game."""

    final_home: int | None
    final_away: int | None
    players: Sequence[Line]
    #: Team totals by side; a side may be missing.
    teams: Mapping[str, Line]
    home_partials: Sequence[int] | None = None
    away_partials: Sequence[int] | None = None
    seconds_tolerance: int = LIVE_SECONDS_TOLERANCE
    #: Overtime periods already recorded for the game (E2's partials), used when the partials
    #: themselves are absent from this call.
    ot_periods: int | None = None
    #: The clubs the two sides must be, as the schedule names them. ``None`` skips the rule.
    home_club: str | None = None
    away_club: str | None = None


@dataclass(frozen=True)
class GameCheck:
    violations: tuple[Violation, ...]
    ot_periods: int | None

    @property
    def hard(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.kind == "hard")

    @property
    def soft(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.kind == "soft")

    @property
    def ok(self) -> bool:
        return not self.hard


def rule_names(violations: Sequence[Violation]) -> list[str]:
    """The distinct rule names, sorted: what ``el_ingest_log.invariant_failed`` records."""
    return sorted({v.rule for v in violations})


def describe(violations: Sequence[Violation], *, limit: int = 6) -> str:
    """One line for a log: the first few messages, then a count of the rest."""
    shown = [f"{v.rule}: {v.message}" for v in violations[:limit]]
    if len(violations) > limit:
        shown.append(f"and {len(violations) - limit} more")
    return "; ".join(shown)


def _known(line: Line, *names: str) -> bool:
    return all(getattr(line, name) is not None for name in names)


def check_game(facts: GameFacts) -> GameCheck:
    """Apply section 4.5 to one game. See the module docstring for the rules."""
    violations: list[Violation] = []

    def hard(rule: str, message: str) -> None:
        violations.append(Violation(rule, "hard", message))

    def soft(rule: str, message: str) -> None:
        violations.append(Violation(rule, "soft", message))

    played = [p for p in facts.players if p.participation == "played"]

    # Identity: who is in the game, once, for the right club.
    seen: dict[str, str] = {}
    for p in facts.players:
        if p.person_code is None:
            continue
        if p.person_code in seen:
            where = "twice on the " + p.side if seen[p.person_code] == p.side else "on both sides"
            hard("duplicate_player", f"{p.name or p.person_code} appears {where}")
        else:
            seen[p.person_code] = p.side
    for side, expected in (("home", facts.home_club), ("away", facts.away_club)):
        if expected is None:
            continue
        wrong = sorted(
            {p.club for p in facts.players if p.side == side and p.club not in (None, expected)}
        )
        if wrong:
            hard(
                "club_mismatch", f"{side} side is {expected} but has players of {', '.join(wrong)}"
            )
    for p in facts.players:
        if p.participation == "dnp" and p.unexplained:
            hard(
                "dnp_has_stats",
                f"{p.side} {p.name or p.person_code}: zero seconds but "
                + ", ".join(p.unexplained)
                + " is not zero",
            )

    # Each line is consistent with itself.
    for line in [*played, *facts.teams.values()]:
        who = f"{line.side} {line.name or 'team'}"
        if _known(line, "pts", "fgm2", "fgm3", "ftm"):
            expected_pts = 2 * line.fgm2 + 3 * line.fgm3 + line.ftm  # type: ignore[operator]
            if line.pts != expected_pts:
                hard(
                    "pts_formula",
                    f"{who}: pts {line.pts} != 2*fgm2 + 3*fgm3 + ftm = {expected_pts}",
                )
        if _known(line, "reb", "oreb", "dreb"):
            if line.reb != line.oreb + line.dreb:  # type: ignore[operator]
                hard("reb_sum", f"{who}: reb {line.reb} != oreb + dreb")
        for made, tried in (("fgm2", "fga2"), ("fgm3", "fga3"), ("ftm", "fta")):
            m, t = getattr(line, made), getattr(line, tried)
            if m is not None and t is not None and m > t:
                hard("makes_le_attempts", f"{who}: {made} {m} > {tried} {t}")

    # Players add up to the team and to the final score.
    finals = {"home": facts.final_home, "away": facts.final_away}
    for side in _SIDES:
        team = facts.teams.get(side)
        side_players = [p for p in played if p.side == side]
        points = [p.pts for p in side_players]
        if side_players and all(v is not None for v in points):
            total = sum(points)  # type: ignore[arg-type]
            if team is not None and team.pts is not None and total != team.pts:
                hard("team_points", f"{side}: players sum to {total}, team line says {team.pts}")
            if finals[side] is not None and total != finals[side]:
                hard(
                    "team_points", f"{side}: players sum to {total}, final score is {finals[side]}"
                )
        if team is not None and team.pts is not None and finals[side] is not None:
            if team.pts != finals[side]:
                hard(
                    "final_score",
                    f"{side}: team line says {team.pts}, final score is {finals[side]}",
                )

    # Partials sum to the final, and say how many periods were played.
    ot_from_partials: int | None = None
    hp, ap = facts.home_partials, facts.away_partials
    if hp is not None and ap is not None:
        if len(hp) != len(ap) or len(hp) < 4:
            hard("partials", f"partials have {len(hp)} and {len(ap)} periods; at least 4 expected")
        else:
            ot_from_partials = len(hp) - 4
            if facts.final_home is not None and sum(hp) != facts.final_home:
                hard("partials", f"home partials sum to {sum(hp)}, final is {facts.final_home}")
            if facts.final_away is not None and sum(ap) != facts.final_away:
                hard("partials", f"away partials sum to {sum(ap)}, final is {facts.final_away}")

    # Team time: five men for forty minutes, plus five men for five minutes per overtime.
    regulation = PROFILE.team_regulation_seconds
    overtime = PROFILE.team_overtime_seconds
    observed: list[int] = []
    for side in _SIDES:
        team = facts.teams.get(side)
        if team is not None and team.seconds is not None:
            observed.append(team.seconds)
    for side in _SIDES:
        side_players = [p for p in played if p.side == side]
        if side_players and all(p.seconds is not None for p in side_players):
            observed.append(sum(p.seconds for p in side_players))  # type: ignore[misc]
    if ot_from_partials is not None:
        ot: int | None = ot_from_partials
    elif facts.ot_periods is not None:
        ot = facts.ot_periods
    elif observed:
        ot = max(0, round((observed[0] - regulation) / overtime))
    else:
        ot = None
    if ot is not None:
        expected_seconds = regulation + ot * overtime
        for side in _SIDES:
            team = facts.teams.get(side)
            if team is not None and team.seconds is not None:
                if abs(team.seconds - expected_seconds) > facts.seconds_tolerance:
                    hard(
                        "team_time",
                        f"{side}: team time {team.seconds}s, expected {expected_seconds}s",
                    )
            side_players = [p for p in played if p.side == side]
            if side_players and all(p.seconds is not None for p in side_players):
                total_seconds = sum(p.seconds for p in side_players)  # type: ignore[misc]
                if abs(total_seconds - expected_seconds) > facts.seconds_tolerance:
                    hard(
                        "team_time",
                        f"{side}: players' time {total_seconds}s, expected {expected_seconds}s",
                    )

    # Soft rules.
    for side in _SIDES:
        team = facts.teams.get(side)
        side_players = [p for p in facts.players if p.side == side]
        side_played = [p for p in side_players if p.participation == "played"]
        if team is not None:
            for column, rule in (("reb", "reb_vs_team"), ("tov", "tov_vs_team")):
                values = [getattr(p, column) for p in side_played]
                team_value = getattr(team, column)
                if values and all(v is not None for v in values) and team_value is not None:
                    if sum(values) > team_value:
                        soft(rule, f"{side}: players' {column} exceed the team's {team_value}")
            if team.clock_seconds is not None and ot is not None:
                clocks = {
                    PROFILE.regulation_seconds + ot * PROFILE.overtime_minutes * 60,
                    regulation + ot * overtime,
                }
                if all(abs(team.clock_seconds - c) > facts.seconds_tolerance for c in clocks):
                    soft(
                        "team_clock",
                        f"{side}: reported clock {team.clock_seconds}s is none of "
                        + ", ".join(f"{c}s" for c in sorted(clocks)),
                    )
        if any(p.is_starter is not None for p in side_players):
            starters = sum(1 for p in side_players if p.is_starter)
            if starters != 5:
                soft("starters", f"{side}: {starters} starters, expected 5")
    for p in played:
        if p.fouls_drawn is None or p.blk_against is None or p.pir_official is None:
            continue
        if not _known(p, *_PIR_PARTS):
            continue
        formula = (
            p.pts  # type: ignore[operator]
            + p.reb
            + p.ast
            + p.stl
            + p.blk
            + p.fouls_drawn
            - (p.fga2 - p.fgm2)  # type: ignore[operator]
            - (p.fga3 - p.fgm3)  # type: ignore[operator]
            - (p.fta - p.ftm)  # type: ignore[operator]
            - p.tov
            - p.blk_against
            - p.pf
        )
        if formula != p.pir_official:
            soft("pir_formula", f"{p.side} {p.name}: pir {p.pir_official} != {formula}")

    if any(v.kind == "hard" and v.rule in ("team_time", "partials") for v in violations):
        ot = None
    return GameCheck(tuple(violations), ot)


# ------------------------------------------------------------------------------ adapter


def _line_of(side: str, p: PlayerLine) -> Line:
    s = p.stats
    return Line(
        side=side,
        name=p.person.name,
        participation=p.participation,
        is_starter=p.is_starter,
        seconds=s.get("seconds_played"),
        pts=s.get("pts"),
        fgm2=s.get("fgm2"),
        fga2=s.get("fga2"),
        fgm3=s.get("fgm3"),
        fga3=s.get("fga3"),
        ftm=s.get("ftm"),
        fta=s.get("fta"),
        oreb=s.get("oreb"),
        dreb=s.get("dreb"),
        reb=s.get("reb"),
        ast=s.get("ast"),
        stl=s.get("stl"),
        tov=s.get("tov"),
        blk=s.get("blk"),
        blk_against=s.get("blk_against"),
        pf=s.get("pf"),
        fouls_drawn=s.get("fouls_drawn"),
        plus_minus=s.get("plus_minus"),
        pir_official=s.get("pir_official"),
        club=p.club_code,
        person_code=p.person.code,
        unexplained=p.unexplained,
    )


def players_seconds(side: BoxSide) -> int:
    """The sum of the seconds of the side's played lines: the team's time, as the store keeps it."""
    return sum(p.seconds or 0 for p in side.players if p.participation == "played")


def _team_line(side: BoxSide) -> Line:
    s = side.totals.stats
    return Line(
        side=side.side,
        name=None,
        seconds=players_seconds(side),
        pts=s.get("pts"),
        fgm2=s.get("fgm2"),
        fga2=s.get("fga2"),
        fgm3=s.get("fgm3"),
        fga3=s.get("fga3"),
        ftm=s.get("ftm"),
        fta=s.get("fta"),
        oreb=s.get("oreb"),
        dreb=s.get("dreb"),
        reb=s.get("reb"),
        ast=s.get("ast"),
        stl=s.get("stl"),
        tov=s.get("tov"),
        blk=s.get("blk"),
        blk_against=s.get("blk_against"),
        pf=s.get("pf"),
        fouls_drawn=s.get("fouls_drawn"),
        plus_minus=s.get("plus_minus"),
        pir_official=s.get("pir_official"),
        club=side.club_code,
        clock_seconds=side.totals.clock_seconds,
    )


def box_to_facts(
    box: BoxScore,
    *,
    final_home: int | None,
    final_away: int | None,
    home_club: str | None = None,
    away_club: str | None = None,
    home_partials: Sequence[int] | None = None,
    away_partials: Sequence[int] | None = None,
    ot_periods: int | None = None,
    seconds_tolerance: int = LIVE_SECONDS_TOLERANCE,
) -> GameFacts:
    """Build the facts for :func:`check_game` from a parsed box score and what E2 said.

    The team's seconds are the players' sum (see :mod:`~nbastats.euroleague.ingest.parse` for why
    the reported ``total.timePlayed`` is a game clock instead); the reported clock rides along
    for the soft ``team_clock`` rule.
    """
    players = [
        *(_line_of("home", p) for p in box.home.players),
        *(_line_of("away", p) for p in box.away.players),
    ]
    return GameFacts(
        final_home=final_home,
        final_away=final_away,
        players=players,
        teams={"home": _team_line(box.home), "away": _team_line(box.away)},
        home_partials=home_partials,
        away_partials=away_partials,
        seconds_tolerance=seconds_tolerance,
        ot_periods=ot_periods,
        home_club=home_club,
        away_club=away_club,
    )
