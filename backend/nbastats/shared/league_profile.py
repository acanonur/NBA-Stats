"""The two leagues, as data: the constants every shared calculation needs and nothing else.

The NBA and the EuroLeague differ in a handful of numbers that matter to the statistics: how
long a game is, how many games make a defence's average believable, how much home court is
worth, how long an injury entry can be trusted. Hard-coding any of those in a calculation
would make the calculation quietly NBA-shaped, so every calculation in this package takes a
:class:`LeagueProfile` (or the one number it needs from one) and the profiles live here, in
one place, as frozen data.

What a profile is, and is not
-----------------------------
A profile holds *facts about the competition and rules of thumb about its sample sizes*. It
holds no database handle, no URL and no behaviour: a league's storage, ingest and routes are
its own package's business (``nbastats/euroleague/`` for the EuroLeague), and the late-bound
lookup that connects the two is :mod:`nbastats.shared.league_registry`. Keeping the profile
free of all of that is what lets the pure core be imported, unit-tested and read by the
contract generator without dragging a database in.

Some of the numbers are measured and some are defaults, and the difference is recorded rather
than implied:

* ``home_advantage_default`` is 3.5 points for the EuroLeague because the user's workbook sets
  it (``Settings`` ``C6``), and 2.5 for the NBA as a stated default that the NBA read side
  replaces with the fitted mean margin once 300 or more games are final
  (``home_advantage_fit_min_games``). ``home_advantage_basis`` says which.
* The status-to-chance table is the workbook's (``Settings`` ``C25:C29``) for the EuroLeague
  and the same values, labelled a default, for the NBA. The NBA injury report's own wording
  maps onto the same five statuses.
* ``defense.min_games`` and ``provisional_below`` are judgement calls about sample size, not
  fitted quantities (NBA 10 and 25; EuroLeague 6 and 12, because a 38-game season gives a
  EuroLeague team far fewer games to judge).

Time units
----------
Everything here is in whole minutes or seconds of *game* time. ``team_regulation_seconds`` is
five players times the regulation minutes times sixty: 14,400 for the NBA and 12,000 for the
EuroLeague. The NBA stores team minutes as 240, so an NBA reader multiplies by sixty before
handing a game to :mod:`nbastats.shared.team_form`; nothing downstream guesses the unit.

The ``to_contract`` methods return the camel-cased, JSON-ready form that
``contracts/leagues.json`` carries, so the contract generator has one definition to read
instead of a second copy to keep in step.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, Mapping

__all__ = [
    "LEAGUE_KEYS",
    "NBA_KEY",
    "EUROLEAGUE_KEY",
    "STATUS_ORDER",
    "DefenseRules",
    "MatchupRules",
    "StaleRules",
    "LeagueProfile",
    "NBA",
    "EUROLEAGUE",
    "PROFILES",
    "UnknownLeagueError",
    "get_profile",
    "is_league_key",
]

NBA_KEY: Final = "nba"
EUROLEAGUE_KEY: Final = "euroleague"
LEAGUE_KEYS: Final[tuple[str, ...]] = (NBA_KEY, EUROLEAGUE_KEY)

#: The five availability statuses from most to least severe. Defined here, not in
#: :mod:`availability`, because the chance table below is keyed by it and the availability
#: module imports this one.
STATUS_ORDER: Final[tuple[str, ...]] = ("out", "doubtful", "questionable", "probable", "available")

#: Players on court at once. Team time is this many times a single player's floor time.
PLAYERS_ON_COURT: Final = 5


class UnknownLeagueError(ValueError):
    """Raised for a league key that is not ``nba`` or ``euroleague``."""


@dataclass(frozen=True)
class DefenseRules:
    """Sample-size and coverage rules for defence by opponent position (design section 7.2)."""

    #: Fewer reconciled games than this withholds every index (``minimumGames``).
    min_games: int
    #: Fewer than this marks the table ``provisional`` and shows no band.
    provisional_below: int
    #: The most points allowed to unlisted positions, as a fraction, before indices are
    #: withheld. Applies to the team and, separately, to the league.
    unknown_ceiling: float
    #: Below this share of teams meeting ``min_games``, the between-team variance cannot be
    #: estimated and every index is withheld (``leagueSample``).
    league_sample_share: float = 0.8
    #: A position's band appears only when the league's reliability at it reaches this.
    reliability_gate: float = 0.2
    #: Family-wise error rate spread over every displayed cell (Bonferroni).
    familywise_alpha: float = 0.05


@dataclass(frozen=True)
class MatchupRules:
    """Rules for the team-form and matchup block (design section 7.1)."""

    #: Opponent-adjusted points need at least this many qualifying games to be shown.
    adjusted_min_games: int = 5
    #: An opponent must have this many *other* games for a game to qualify.
    adjusted_min_other_games: int = 2
    form_window_default: int = 5
    form_window_min: int = 3
    form_window_max: int = 15


@dataclass(frozen=True)
class StaleRules:
    """When an availability entry or report stops being fresh enough to show without a flag.

    The two leagues age statuses differently because they learn them differently. The NBA
    publishes a report on a schedule, so what matters is how old the *snapshot* is: an hour
    inside a reporting window, a day outside one. The EuroLeague has no schedule, only
    sourced and dated entries, so what matters is the entry's own age or whether the team has
    played since the source was written.
    """

    #: NBA: snapshot age in minutes beyond which it is stale inside a reporting window.
    in_window_minutes: int | None = None
    #: NBA: the same, outside a reporting window.
    outside_window_minutes: int | None = None
    #: EuroLeague: entry age in days beyond which it is stale.
    max_age_days: int | None = None
    #: EuroLeague: an entry is also stale once its team has played since the source date.
    stale_when_team_has_played: bool = False


@dataclass(frozen=True)
class LeagueProfile:
    """Everything a shared calculation needs to know about one league."""

    key: str
    name: str
    api_prefix: str
    schedule_tz: str
    regulation_minutes: int
    overtime_minutes: int
    team_regulation_seconds: int
    per_modes: tuple[str, ...]
    position_buckets: tuple[str, ...]
    #: The finer labels a league's own workbook uses, opt-in and always estimated.
    workbook_position_buckets: tuple[str, ...]
    defense: DefenseRules
    matchup: MatchupRules
    home_advantage_default: float
    #: ``default`` (a stated default) or ``workbook`` (the user's own setting).
    home_advantage_basis: str
    #: Final games needed before the default is replaced by the fitted mean margin; ``None``
    #: when the league never fits one.
    home_advantage_fit_min_games: int | None
    #: Whether the store records neutral-site games. The NBA does not, so every NBA venue is
    #: an assumption.
    tracks_neutral_sites: bool
    #: ``(status, chance)`` pairs, in :data:`STATUS_ORDER`. A tuple of pairs, not a dict, so
    #: the dataclass stays hashable; :meth:`chance_table` gives a fresh dict.
    status_chance: tuple[tuple[str, float], ...]
    status_chance_basis: str
    stale_after: StaleRules
    attribution: str

    # ------------------------------------------------------------------ derived

    @property
    def regulation_seconds(self) -> int:
        """One player's regulation floor time in seconds (2,400 for a 40-minute game)."""
        return self.regulation_minutes * 60

    @property
    def team_overtime_seconds(self) -> int:
        """Team time added by one overtime period: five players for the overtime length."""
        return PLAYERS_ON_COURT * self.overtime_minutes * 60

    def chance_table(self) -> dict[str, float]:
        """The status-to-chance mapping as a new dict the caller may modify."""
        return dict(self.status_chance)

    def team_time_seconds(self, overtime_periods: int = 0) -> int:
        """Expected team time for a game that went ``overtime_periods`` extra periods."""
        if overtime_periods < 0:
            raise ValueError("overtime_periods cannot be negative")
        return self.team_regulation_seconds + overtime_periods * self.team_overtime_seconds

    def to_contract(self) -> dict[str, Any]:
        """The profile as ``contracts/leagues.json`` carries it: camelCase, JSON-ready."""
        stale = self.stale_after
        return {
            "key": self.key,
            "name": self.name,
            "apiPrefix": self.api_prefix,
            "scheduleTz": self.schedule_tz,
            "regulationMinutes": self.regulation_minutes,
            "overtimeMinutes": self.overtime_minutes,
            "teamRegulationSeconds": self.team_regulation_seconds,
            "perModes": list(self.per_modes),
            "positionBuckets": list(self.position_buckets),
            "workbookPositionBuckets": list(self.workbook_position_buckets),
            "defense": {
                "minGames": self.defense.min_games,
                "provisionalBelowGames": self.defense.provisional_below,
                "unknownCeiling": self.defense.unknown_ceiling,
                "leagueSampleShare": self.defense.league_sample_share,
                "reliabilityGate": self.defense.reliability_gate,
                "familywiseAlpha": self.defense.familywise_alpha,
            },
            "matchup": {
                "adjustedMinGames": self.matchup.adjusted_min_games,
                "adjustedMinOtherGames": self.matchup.adjusted_min_other_games,
                "formWindowDefault": self.matchup.form_window_default,
                "formWindowMin": self.matchup.form_window_min,
                "formWindowMax": self.matchup.form_window_max,
            },
            "homeAdvantagePoints": self.home_advantage_default,
            "homeAdvantageBasis": self.home_advantage_basis,
            "homeAdvantageFitMinGames": self.home_advantage_fit_min_games,
            "tracksNeutralSites": self.tracks_neutral_sites,
            "statusChance": dict(self.status_chance),
            "statusChanceBasis": self.status_chance_basis,
            "staleAfter": {
                "inWindowMinutes": stale.in_window_minutes,
                "outsideWindowMinutes": stale.outside_window_minutes,
                "maxAgeDays": stale.max_age_days,
                "staleWhenTeamHasPlayed": stale.stale_when_team_has_played,
            },
            "attribution": self.attribution,
        }


# The workbook's status table (Settings C25:C29) and the NBA default share these five values.
_STATUS_CHANCE: Final[tuple[tuple[str, float], ...]] = (
    ("out", 0.0),
    ("doubtful", 0.25),
    ("questionable", 0.5),
    ("probable", 0.85),
    ("available", 1.0),
)

NBA: Final = LeagueProfile(
    key=NBA_KEY,
    name="NBA",
    api_prefix="/v1",
    schedule_tz="America/New_York",
    regulation_minutes=48,
    overtime_minutes=5,
    team_regulation_seconds=14_400,
    per_modes=("PerGame", "Totals", "Per36", "Per100"),
    position_buckets=("G", "F", "C"),
    workbook_position_buckets=(),
    defense=DefenseRules(min_games=10, provisional_below=25, unknown_ceiling=0.05),
    matchup=MatchupRules(),
    home_advantage_default=2.5,
    home_advantage_basis="default",
    home_advantage_fit_min_games=300,
    tracks_neutral_sites=False,
    status_chance=_STATUS_CHANCE,
    status_chance_basis="default",
    stale_after=StaleRules(in_window_minutes=60, outside_window_minutes=24 * 60),
    attribution="Stats via NBA.com. Injury status from the NBA's official injury report.",
)

EUROLEAGUE: Final = LeagueProfile(
    key=EUROLEAGUE_KEY,
    name="EuroLeague",
    api_prefix="/v1/el",
    schedule_tz="Europe/Berlin",
    regulation_minutes=40,
    overtime_minutes=5,
    team_regulation_seconds=12_000,
    per_modes=("PerGame", "Totals", "Per40"),
    position_buckets=("G", "F", "C"),
    workbook_position_buckets=("PG", "SG", "SF", "PF", "C"),
    defense=DefenseRules(min_games=6, provisional_below=12, unknown_ceiling=0.05),
    matchup=MatchupRules(),
    home_advantage_default=3.5,
    home_advantage_basis="workbook",
    home_advantage_fit_min_games=None,
    tracks_neutral_sites=True,
    status_chance=_STATUS_CHANCE,
    status_chance_basis="workbook",
    stale_after=StaleRules(max_age_days=7, stale_when_team_has_played=True),
    attribution=(
        "EuroLeague statistics from the EuroLeague's data service. "
        "Availability researched from the linked sources."
    ),
)

#: Read-only registry of the profiles by league key.
PROFILES: Final[Mapping[str, LeagueProfile]] = MappingProxyType(
    {NBA_KEY: NBA, EUROLEAGUE_KEY: EUROLEAGUE}
)


def is_league_key(key: object) -> bool:
    """True when ``key`` is exactly one of the league keys (case-sensitive, as on the wire)."""
    return isinstance(key, str) and key in PROFILES


def get_profile(key: str) -> LeagueProfile:
    """The profile for ``key``; raises :class:`UnknownLeagueError` for anything else."""
    try:
        return PROFILES[key]
    except (KeyError, TypeError):
        raise UnknownLeagueError(
            f"unknown league {key!r}; expected one of {', '.join(LEAGUE_KEYS)}"
        ) from None
