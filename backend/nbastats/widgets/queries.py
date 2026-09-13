"""Reusable, memoised reads for the widget layer.

Every resolver in this package goes through this module rather than writing its own
``select``. There are two reasons, and they are both about a 24-widget dashboard being one
request:

* **No N+1.** A helper that answers "this player's season row" answers it by loading the
  whole season once, keying it by player, and memoising it on the
  :class:`~nbastats.widgets.base.ResolveContext`. The second, fifth and twentieth widget to
  ask hit the dictionary, not SQLite. The same is true for team seasons, the league
  distributions behind every percentile bar, the franchise directory, and the slate.
* **One definition of "qualified".** A league distribution that included every player who
  logged four minutes in November would make every percentile bar a lie. The qualification
  rule lives in :func:`_qualified_player_rows`, is applied once, and matches the rule the
  ingest layer used to write ``league_season`` — so the stored average and the computed
  percentiles describe the same field.

``league_season`` is the source of a metric's league average when the ingest has written one
(``contracts/CONTRACT.md`` §4 leans on it for every "vs league" caption); when it has not, the
distribution is computed on the fly from the season rows that are already in memory.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping, Optional, Sequence

from sqlalchemy import func, select

from .. import catalog
from ..api.serializers import (
    game_ref as serializer_game_ref,
    load_player_teams,
    load_teams,
    metric_value as serializer_metric_value,
    player_game_values,
    player_ref as serializer_player_ref,
    player_season_values,
    team_game_values,
    team_ref as serializer_team_ref,
    team_season_values,
)
from ..metrics import per_mode_convert
from ..models import (
    SHOT_ZONE_LABELS,
    SHOT_ZONES,
    Game,
    LeagueSeason,
    Player,
    PlayerGameAdvanced,
    PlayerGameBasic,
    PlayerSeason,
    ShotZoneSeason,
    Team,
    TeamGame,
    TeamSeason,
)
from ..percentiles import rank_and_percentile, summarize
from .base import ResolveContext

__all__ = [
    "GameLine",
    "TeamGameLine",
    "Distribution",
    "LeaderRow",
    "MIN_DISTRIBUTION_SAMPLE",
    "QUALIFIED_MIN_MINUTES",
    "QUALIFIED_GAME_SHARE",
    "player_season_index",
    "player_season_row",
    "team_season_index",
    "team_season_row",
    "career_season_rows",
    "all_player_seasons",
    "players",
    "player",
    "teams",
    "team",
    "all_teams",
    "player_team_map",
    "season_value",
    "season_values",
    "distribution",
    "featured_subject_id",
    "leaders",
    "player_games",
    "team_games",
    "slate_games",
    "slate_player_lines",
    "shot_zones",
    "league_shot_zones",
    "zone_label",
    "as_json",
    "metric_descriptor_dict",
    "metric_value_dict",
    "player_ref_dict",
    "player_ref_dict_for_team",
    "player_ref_dicts",
    "team_ref_dict",
    "game_ref_dict",
    "subject_dict",
]

#: A distribution needs at least this many values before a percentile means anything.
MIN_DISTRIBUTION_SAMPLE = 2

#: Minutes per game a player must average to count toward a league distribution.
QUALIFIED_MIN_MINUTES = 10.0

#: …and the share of the league leader's games he must have played. Both together are the
#: rule ``nbastats.seed`` used when it wrote ``league_season``, so the stored average and the
#: computed percentiles describe the same field of players.
QUALIFIED_GAME_SHARE = 0.2

_PER_MODES_FROM_TOTALS = ("Per36", "Per100")


# --------------------------------------------------------------------------- row shapes


@dataclass(frozen=True, slots=True)
class GameLine:
    """One player's appearance in one game: the box, the advanced line, and the game."""

    game: Game
    basic: PlayerGameBasic
    advanced: Optional[PlayerGameAdvanced]

    @property
    def is_home(self) -> bool:
        return self.basic.team_id == self.game.home_team_id

    @property
    def opponent_id(self) -> int:
        return self.game.away_team_id if self.is_home else self.game.home_team_id

    @property
    def own_points(self) -> Optional[int]:
        return self.game.home_pts if self.is_home else self.game.away_pts

    @property
    def opponent_points(self) -> Optional[int]:
        return self.game.away_pts if self.is_home else self.game.home_pts

    @property
    def result(self) -> Optional[str]:
        return _result_of(self.own_points, self.opponent_points)

    @property
    def score(self) -> Optional[str]:
        if self.own_points is None or self.opponent_points is None:
            return None
        return f"{self.own_points}-{self.opponent_points}"

    def values(self, keys: Sequence[str]) -> dict[str, Optional[float]]:
        """Era-correct values for ``keys`` from this one game."""
        return player_game_values(self.basic, self.advanced, keys, self.game.season)


@dataclass(frozen=True, slots=True)
class TeamGameLine:
    """One team's side of one game."""

    game: Game
    row: TeamGame

    @property
    def is_home(self) -> bool:
        return bool(self.row.is_home)

    @property
    def opponent_id(self) -> int:
        return self.game.away_team_id if self.is_home else self.game.home_team_id

    @property
    def result(self) -> Optional[str]:
        own = self.game.home_pts if self.is_home else self.game.away_pts
        other = self.game.away_pts if self.is_home else self.game.home_pts
        return _result_of(own, other)

    def values(self, keys: Sequence[str]) -> dict[str, Optional[float]]:
        return team_game_values(self.row, keys, self.game.season)


def _result_of(own: Optional[int], other: Optional[int]) -> Optional[str]:
    if own is None or other is None:
        return None
    if own > other:
        return "W"
    if own < other:
        return "L"
    return "T"


@dataclass(frozen=True, slots=True)
class Distribution:
    """How a whole league's worth of one metric is spread, for one season.

    ``average`` prefers the stored ``league_season`` row, which is what the ingest computed
    over the same qualified field; when there is no stored row it is the mean of ``values``.
    ``values`` is ascending and holds only real numbers — a subject the era never recorded is
    absent rather than present as a zero, which is the difference between an honest
    percentile and a defamatory one.
    """

    metric: str
    subject_type: str
    season: str
    season_type: str
    higher_is_better: bool
    average: Optional[float]
    sample_size: int
    values: tuple[float, ...]
    by_subject: Mapping[int, float]
    ranks: Mapping[int, tuple[int, float]]

    @property
    def usable(self) -> bool:
        """True when there are enough values for a rank or a percentile to mean anything."""
        return self.sample_size >= MIN_DISTRIBUTION_SAMPLE

    def for_subject(self, subject_id: int | None) -> tuple[Optional[int], Optional[float]]:
        """``(rank, percentile)`` for one subject, or ``(None, None)``."""
        if subject_id is None or not self.usable:
            return None, None
        found = self.ranks.get(int(subject_id))
        return (found[0], found[1]) if found else (None, None)

    def delta(self, value: Optional[float]) -> Optional[float]:
        """How far a value sits from the league average, or ``None``."""
        if value is None or self.average is None:
            return None
        return float(value) - float(self.average)


@dataclass(frozen=True, slots=True)
class LeaderRow:
    """One ranked subject in a leaderboard, before it is serialised."""

    rank: int
    subject_id: int
    season: str
    value: Optional[float]
    percentile: Optional[float]
    row: Any


# --------------------------------------------------------------------------- seasons


def player_season_index(
    ctx: ResolveContext, season: str, season_type: str
) -> dict[int, PlayerSeason]:
    """``{player_id: PlayerSeason}`` for one season, loaded once per request.

    A player who changed teams mid-season has one row per team; the row with the most games
    wins, because that is the team the client should name and the line the client should
    show. Callers that need every row (a traded player's split) use
    :func:`career_season_rows`.
    """

    def _load() -> dict[int, PlayerSeason]:
        rows = (
            ctx.session.execute(
                select(PlayerSeason)
                .where(PlayerSeason.season == season)
                .where(PlayerSeason.season_type == season_type)
            )
            .scalars()
            .all()
        )
        out: dict[int, PlayerSeason] = {}
        for row in rows:
            current = out.get(row.player_id)
            if current is None or (row.gp or 0) > (current.gp or 0):
                out[row.player_id] = row
        return out

    return ctx.memo(("player_season_index", season, season_type), _load)


def player_season_row(
    ctx: ResolveContext, player_id: int, season: str, season_type: str
) -> Optional[PlayerSeason]:
    """One player's season line, from the index so ten tiles share one query."""
    return player_season_index(ctx, season, season_type).get(int(player_id))


def team_season_index(
    ctx: ResolveContext, season: str, season_type: str
) -> dict[int, TeamSeason]:
    """``{team_id: TeamSeason}`` for one season, loaded once per request."""

    def _load() -> dict[int, TeamSeason]:
        rows = (
            ctx.session.execute(
                select(TeamSeason)
                .where(TeamSeason.season == season)
                .where(TeamSeason.season_type == season_type)
            )
            .scalars()
            .all()
        )
        return {row.team_id: row for row in rows}

    return ctx.memo(("team_season_index", season, season_type), _load)


def team_season_row(
    ctx: ResolveContext, team_id: int, season: str, season_type: str
) -> Optional[TeamSeason]:
    return team_season_index(ctx, season, season_type).get(int(team_id))


def career_season_rows(
    ctx: ResolveContext, player_id: int, season_type: str | None = None
) -> list[PlayerSeason]:
    """Every season row one player has, oldest first, optionally one season type only."""

    def _load() -> list[PlayerSeason]:
        statement = select(PlayerSeason).where(PlayerSeason.player_id == int(player_id))
        rows = ctx.session.execute(statement).scalars().all()
        return sorted(
            rows,
            key=lambda row: (catalog.season_sort_key(row.season), row.season_type, -(row.gp or 0)),
        )

    rows = ctx.memo(("career_season_rows", int(player_id)), _load)
    if season_type is None:
        return list(rows)
    return [row for row in rows if row.season_type == season_type]


def all_player_seasons(ctx: ResolveContext, season_type: str) -> list[PlayerSeason]:
    """Every player-season in the store for one season type — the ``all_time`` scope.

    This is the one deliberately wide read in the module. It is memoised, and the all-time
    leaderboard is the only caller: ranking "the best single seasons in history" cannot be
    answered from one season's index.
    """

    def _load() -> list[PlayerSeason]:
        return list(
            ctx.session.execute(
                select(PlayerSeason).where(PlayerSeason.season_type == season_type)
            )
            .scalars()
            .all()
        )

    return ctx.memo(("all_player_seasons", season_type), _load)


# --------------------------------------------------------------------------- directories


def players(ctx: ResolveContext, player_ids: Iterable[int]) -> dict[int, Player]:
    """``{player_id: Player}`` for the ids given, batched and memoised per id."""
    wanted = {int(pid) for pid in player_ids}
    cache: dict[int, Player] = ctx.memo(("player_cache",), dict)
    missing = sorted(wanted - cache.keys())
    if missing:
        for start in range(0, len(missing), 400):
            chunk = missing[start : start + 400]
            rows = (
                ctx.session.execute(select(Player).where(Player.player_id.in_(chunk)))
                .scalars()
                .all()
            )
            cache.update({row.player_id: row for row in rows})
    return {pid: cache[pid] for pid in wanted if pid in cache}


def player(ctx: ResolveContext, player_id: int) -> Optional[Player]:
    """One ``players`` row, or ``None``."""
    return players(ctx, [player_id]).get(int(player_id))


def all_teams(ctx: ResolveContext) -> dict[int, Team]:
    """The whole franchise directory, loaded once — 30-odd rows, every widget wants them."""

    def _load() -> dict[int, Team]:
        rows = ctx.session.execute(select(Team)).scalars().all()
        return {row.team_id: row for row in rows}

    return ctx.memo(("all_teams",), _load)


def teams(ctx: ResolveContext, team_ids: Iterable[int]) -> dict[int, Team]:
    """``{team_id: Team}`` for the ids given, from the cached directory."""
    directory = all_teams(ctx)
    wanted = {int(tid) for tid in team_ids}
    found = {tid: directory[tid] for tid in wanted if tid in directory}
    absent = sorted(wanted - found.keys())
    if absent:  # a defunct franchise the directory query somehow missed
        found.update(load_teams(ctx.session, absent))
    return found


def team(ctx: ResolveContext, team_id: int) -> Optional[Team]:
    return teams(ctx, [team_id]).get(int(team_id))


def player_team_map(
    ctx: ResolveContext, player_ids: Iterable[int], season: str | None
) -> dict[int, Team]:
    """The team each player should be shown with for ``season`` (memoised per season)."""
    wanted = sorted({int(pid) for pid in player_ids})
    if not wanted:
        return {}
    key = ("player_team_map", season, tuple(wanted))
    return ctx.memo(key, lambda: load_player_teams(ctx.session, wanted, season))


# --------------------------------------------------------------------------- values


def season_value(
    row: Any,
    metric_key: str,
    season: str,
    subject_type: str = "player",
    per_mode: str = "PerGame",
) -> Optional[float]:
    """One era-correct season value, including the per-modes no column stores.

    ``Per36`` and ``Per100`` are not stored — storing them would duplicate every counting
    column twice — so they are derived here from the season totals plus minutes or
    possessions, exactly as :func:`nbastats.metrics.per_mode_convert` documents. A rate or a
    rating is identical under every per-mode and is read straight from its own column.
    """
    reader = player_season_values if subject_type == "player" else team_season_values
    direct = reader(row, [metric_key], season, per_mode)[metric_key]
    if direct is not None or per_mode not in _PER_MODES_FROM_TOTALS:
        return direct
    if row is None:
        return None
    # A counting stat under Per36/Per100: convert the season total.
    total = reader(row, [metric_key], season, "Totals")[metric_key]
    if total is None:
        return None
    return per_mode_convert(
        total,
        minutes=getattr(row, "minutes", None),
        games=getattr(row, "gp", None),
        mode=per_mode,
        possessions_played=getattr(row, "poss", None),
        team_pace=getattr(row, "pace", None),
    )


def season_values(
    row: Any,
    metric_keys: Sequence[str],
    season: str,
    subject_type: str = "player",
    per_mode: str = "PerGame",
) -> dict[str, Optional[float]]:
    """:func:`season_value` for several metrics, keeping every key in the result."""
    return {
        key: season_value(row, key, season, subject_type, per_mode) for key in metric_keys
    }


# --------------------------------------------------------------------------- distributions


def _qualified_player_rows(rows: Sequence[PlayerSeason]) -> list[PlayerSeason]:
    """The field a player percentile is measured against.

    A distribution over everyone who ever checked in would put a rotation regular in the 90th
    percentile of minutes and make every bar meaningless. The rule — a fifth of the leader's
    games and ten minutes a night — is the one the ingest used for ``league_season``. If it
    leaves fewer than two rows (an eight-game sample in October) the unfiltered field is used
    instead, because a coarse answer beats no answer.
    """
    if not rows:
        return []
    most_games = max((row.gp or 0) for row in rows)
    floor = max(1, int(QUALIFIED_GAME_SHARE * most_games))
    qualified = [
        row
        for row in rows
        if (row.gp or 0) >= floor and (row.min_pg or 0.0) >= QUALIFIED_MIN_MINUTES
    ]
    return qualified if len(qualified) >= MIN_DISTRIBUTION_SAMPLE else list(rows)


def _stored_average(
    ctx: ResolveContext, metric_key: str, subject_type: str, season: str, season_type: str
) -> Optional[LeagueSeason]:
    """The ``league_season`` row for one metric, from a single per-season read."""

    def _load() -> dict[str, LeagueSeason]:
        rows = (
            ctx.session.execute(
                select(LeagueSeason)
                .where(LeagueSeason.subject_type == subject_type)
                .where(LeagueSeason.season == season)
                .where(LeagueSeason.season_type == season_type)
            )
            .scalars()
            .all()
        )
        return {row.metric_key: row for row in rows}

    index = ctx.memo(("league_season", subject_type, season, season_type), _load)
    return index.get(metric_key)


def distribution(
    ctx: ResolveContext,
    metric_key: str,
    subject_type: str,
    season: str,
    season_type: str,
    per_mode: str = "PerGame",
) -> Distribution:
    """The league-wide spread of one metric, memoised for the whole request.

    Backed by ``league_season`` for the average the ingest already computed, and by the
    season's own rows for the ranks and percentiles a stored five-number summary cannot
    give. A metric the era never recorded comes back empty rather than full of zeroes.
    """
    key = ("distribution", metric_key, subject_type, season, season_type, per_mode)

    def _load() -> Distribution:
        descriptor = catalog.metric(metric_key)
        higher_is_better = bool(descriptor.get("higherIsBetter", True))

        by_subject: dict[int, float] = {}
        if catalog.metric_availability(metric_key, season, "season") != "unavailable":
            if subject_type == "team":
                rows: Sequence[Any] = list(team_season_index(ctx, season, season_type).values())
                id_column = "team_id"
            else:
                rows = _qualified_player_rows(
                    list(player_season_index(ctx, season, season_type).values())
                )
                id_column = "player_id"
            for row in rows:
                value = season_value(row, metric_key, season, subject_type, per_mode)
                if value is not None:
                    by_subject[int(getattr(row, id_column))] = float(value)

        values = tuple(sorted(by_subject.values()))
        stored = _stored_average(ctx, metric_key, subject_type, season, season_type)
        average: Optional[float] = stored.average if stored is not None else None
        if average is None and values:
            average = summarize(values).mean
        return Distribution(
            metric=metric_key,
            subject_type=subject_type,
            season=season,
            season_type=season_type,
            higher_is_better=higher_is_better,
            average=average,
            sample_size=len(values),
            values=values,
            by_subject=by_subject,
            ranks=rank_and_percentile(by_subject, higher_is_better),
        )

    return ctx.memo(key, _load)


def featured_subject_id(
    ctx: ResolveContext, metric_key: str, subject_type: str
) -> Optional[int]:
    """The subject a ``$featured_*`` / ``$league_leader`` token names for the season.

    The ranking is over the same qualified field a percentile uses, so "the season's scoring
    leader" in January is a rotation player who has actually played, not someone who dropped
    30 in his only appearance.
    """
    spread = distribution(ctx, metric_key, subject_type, ctx.season, "Regular Season")
    if not spread.by_subject:
        return None
    best = min if not spread.higher_is_better else max
    return int(best(spread.by_subject.items(), key=lambda item: item[1])[0])


# --------------------------------------------------------------------------- leaders


def leaders(
    ctx: ResolveContext,
    metric_key: str,
    subject_type: str,
    season: str,
    season_type: str,
    *,
    per_mode: str = "PerGame",
    scope: str = "season",
    min_games: int = 0,
    min_minutes_per_game: float = 0.0,
    positions: Sequence[str] = (),
    team_ids: Sequence[int] = (),
    ascending: bool = False,
) -> list[LeaderRow]:
    """Rank a field of subjects, honouring every qualifier the widget catalog defines.

    ``scope="all_time"`` ranks the best *single seasons* in league history rather than the
    subjects of one season, so each row carries the season it is about. The era gate runs
    per row against that row's own season: a 1971-72 line cannot enter a steals leaderboard,
    because nobody was counting.

    ``ascending`` reverses the metric's own direction — it is the widget catalog's "Reverse
    Order" switch, not a second way of saying "lower is better".
    """
    descriptor = catalog.metric(metric_key)
    higher_is_better = bool(descriptor.get("higherIsBetter", True))
    if ascending:
        higher_is_better = not higher_is_better

    wanted_teams = {int(t) for t in team_ids}
    wanted_positions = {str(p).upper() for p in positions if p}

    entries: list[tuple[int, str, float, Any]] = []

    if subject_type == "team":
        rows: Sequence[Any]
        if scope == "all_time":
            rows = _all_team_seasons(ctx, season_type)
        else:
            rows = list(team_season_index(ctx, season, season_type).values())
        for row in rows:
            if wanted_teams and row.team_id not in wanted_teams:
                continue
            if (row.gp or 0) < min_games:
                continue
            value = season_value(row, metric_key, row.season, "team", per_mode)
            if value is None:
                continue
            entries.append((int(row.team_id), row.season, float(value), row))
    else:
        if scope == "all_time":
            candidates = all_player_seasons(ctx, season_type)
        else:
            candidates = list(player_season_index(ctx, season, season_type).values())
        # Only the position filter needs the ``players`` table; an all-time board would
        # otherwise pull every player who has ever played to answer a question about totals.
        directory = (
            players(ctx, [row.player_id for row in candidates]) if wanted_positions else {}
        )
        for row in candidates:
            if wanted_teams and row.team_id not in wanted_teams:
                continue
            if (row.gp or 0) < min_games:
                continue
            if (row.min_pg or 0.0) < min_minutes_per_game:
                continue
            if wanted_positions:
                position = (directory.get(row.player_id).position or "") if row.player_id in directory else ""
                if not _matches_position(position, wanted_positions):
                    continue
            value = season_value(row, metric_key, row.season, "player", per_mode)
            if value is None:
                continue
            entries.append((int(row.player_id), row.season, float(value), row))

    if not entries:
        return []

    ordered = sorted(entries, key=lambda entry: entry[2], reverse=higher_is_better)
    ranking = rank_and_percentile(
        {index: entry[2] for index, entry in enumerate(ordered)}, higher_is_better
    )
    out: list[LeaderRow] = []
    for index, (subject_id, row_season, value, row) in enumerate(ordered):
        rank, percentile = ranking.get(index, (index + 1, None))
        out.append(
            LeaderRow(
                rank=rank,
                subject_id=subject_id,
                season=row_season,
                value=value,
                percentile=percentile,
                row=row,
            )
        )
    return out


def _matches_position(position: str, wanted: set[str]) -> bool:
    """``"G-F"`` matches a filter of ``{"F"}`` — a combo forward is still a forward."""
    letters = {part.strip().upper() for part in position.replace("/", "-").split("-") if part}
    return bool(letters & wanted)


def _all_team_seasons(ctx: ResolveContext, season_type: str) -> list[TeamSeason]:
    def _load() -> list[TeamSeason]:
        return list(
            ctx.session.execute(
                select(TeamSeason).where(TeamSeason.season_type == season_type)
            )
            .scalars()
            .all()
        )

    return ctx.memo(("all_team_seasons", season_type), _load)


# --------------------------------------------------------------------------- game logs


def player_games(
    ctx: ResolveContext, player_id: int, season: str, season_type: str
) -> list[GameLine]:
    """One player's games for a season, **newest first**, with the advanced line attached.

    Two queries, whatever the season's length: the box lines joined to their games, then the
    advanced rows for those games in one ``IN`` clause. Before 1996-97 the second query
    returns nothing, which is correct — there is no advanced box to attach.
    """

    def _load() -> list[GameLine]:
        pairs = ctx.session.execute(
            select(PlayerGameBasic, Game)
            .join(Game, Game.game_id == PlayerGameBasic.game_id)
            .where(PlayerGameBasic.player_id == int(player_id))
            .where(Game.season == season)
            .where(Game.season_type == season_type)
            .order_by(Game.game_date.desc(), Game.game_id.desc())
        ).all()
        if not pairs:
            return []
        game_ids = [game.game_id for _, game in pairs]
        advanced = {
            row.game_id: row
            for row in ctx.session.execute(
                select(PlayerGameAdvanced)
                .where(PlayerGameAdvanced.player_id == int(player_id))
                .where(PlayerGameAdvanced.game_id.in_(game_ids))
            )
            .scalars()
            .all()
        }
        return [
            GameLine(game=game, basic=basic, advanced=advanced.get(game.game_id))
            for basic, game in pairs
        ]

    return ctx.memo(("player_games", int(player_id), season, season_type), _load)


def team_games(
    ctx: ResolveContext, team_id: int, season: str, season_type: str
) -> list[TeamGameLine]:
    """One team's games for a season, newest first."""

    def _load() -> list[TeamGameLine]:
        pairs = ctx.session.execute(
            select(TeamGame, Game)
            .join(Game, Game.game_id == TeamGame.game_id)
            .where(TeamGame.team_id == int(team_id))
            .where(Game.season == season)
            .where(Game.season_type == season_type)
            .order_by(Game.game_date.desc(), Game.game_id.desc())
        ).all()
        return [TeamGameLine(game=game, row=row) for row, game in pairs]

    return ctx.memo(("team_games", int(team_id), season, season_type), _load)


# --------------------------------------------------------------------------- slates


def slate_games(ctx: ResolveContext, day: date) -> list[Game]:
    """Every game scheduled on one calendar day, home team first for a stable order."""

    def _load() -> list[Game]:
        rows = (
            ctx.session.execute(select(Game).where(Game.game_date == day)).scalars().all()
        )
        return sorted(rows, key=lambda game: game.game_id)

    return ctx.memo(("slate_games", day), _load)


def slate_player_lines(ctx: ResolveContext, game_ids: Sequence[str]) -> list[GameLine]:
    """Every player line from a set of games, in two queries however big the slate.

    This is what both ``scoreboard`` (top performers) and ``daily_movers`` (the whole night's
    best games) read; memoising on the id set means a dashboard carrying both widgets pays
    for it once.
    """
    ids = tuple(sorted(set(game_ids)))
    if not ids:
        return []

    def _load() -> list[GameLine]:
        pairs = ctx.session.execute(
            select(PlayerGameBasic, Game)
            .join(Game, Game.game_id == PlayerGameBasic.game_id)
            .where(PlayerGameBasic.game_id.in_(list(ids)))
        ).all()
        advanced = {
            (row.game_id, row.player_id): row
            for row in ctx.session.execute(
                select(PlayerGameAdvanced).where(PlayerGameAdvanced.game_id.in_(list(ids)))
            )
            .scalars()
            .all()
        }
        return [
            GameLine(
                game=game,
                basic=basic,
                advanced=advanced.get((basic.game_id, basic.player_id)),
            )
            for basic, game in pairs
        ]

    return ctx.memo(("slate_player_lines", ids), _load)


# --------------------------------------------------------------------------- shot zones


def shot_zones(
    ctx: ResolveContext, subject_type: str, subject_id: int, season: str, season_type: str
) -> dict[str, ShotZoneSeason]:
    """``{zone: ShotZoneSeason}`` for one subject. Empty before 1996-97, by construction."""

    def _load() -> dict[str, ShotZoneSeason]:
        rows = (
            ctx.session.execute(
                select(ShotZoneSeason)
                .where(ShotZoneSeason.subject_type == subject_type)
                .where(ShotZoneSeason.subject_id == int(subject_id))
                .where(ShotZoneSeason.season == season)
                .where(ShotZoneSeason.season_type == season_type)
            )
            .scalars()
            .all()
        )
        return {row.zone: row for row in rows}

    return ctx.memo(
        ("shot_zones", subject_type, int(subject_id), season, season_type), _load
    )


def league_shot_zones(
    ctx: ResolveContext, subject_type: str, season: str, season_type: str
) -> dict[str, dict[str, Optional[float]]]:
    """The league's shot diet for a season: ``{zone: {"fgPct": …, "shareOfFga": …}}``.

    Aggregated from attempt totals rather than from an average of per-subject rates: the
    league's rim percentage is the league's makes over the league's attempts, not the mean
    of three hundred players' percentages.
    """

    def _load() -> dict[str, dict[str, Optional[float]]]:
        rows = ctx.session.execute(
            select(
                ShotZoneSeason.zone,
                func.sum(ShotZoneSeason.fga_tot),
                func.sum(ShotZoneSeason.fgm_tot),
            )
            .where(ShotZoneSeason.subject_type == subject_type)
            .where(ShotZoneSeason.season == season)
            .where(ShotZoneSeason.season_type == season_type)
            .group_by(ShotZoneSeason.zone)
        ).all()
        totals = {zone: (float(fga or 0.0), float(fgm or 0.0)) for zone, fga, fgm in rows}
        league_attempts = sum(fga for fga, _ in totals.values())
        out: dict[str, dict[str, Optional[float]]] = {}
        for zone in SHOT_ZONES:
            attempts, makes = totals.get(zone, (0.0, 0.0))
            out[zone] = {
                "fgPct": (makes / attempts) if attempts else None,
                "shareOfFga": (attempts / league_attempts) if league_attempts else None,
            }
        return out

    return ctx.memo(("league_shot_zones", subject_type, season, season_type), _load)


def zone_label(zone: str) -> str:
    """The client-facing name of a court zone (``"rim"`` -> ``"At Rim"``)."""
    return SHOT_ZONE_LABELS.get(zone, zone.replace("_", " ").title())


# --------------------------------------------------------------------------- JSON shapes

# A widget payload crosses the wire as a plain ``dict`` inside ``ResolveResult.payload``,
# which is typed ``dict[str, Any]``. Pydantic serialises an ``Any`` field by duck typing,
# and a nested model reached that way is **not** guaranteed to be dumped by alias — which
# would put ``player_id`` on the wire where the Swift client expects ``playerId``. So every
# contract object is converted to its final camelCase dict here, once, and resolvers only
# ever assemble plain dicts.


def as_json(model: Any) -> Any:
    """One contract model as the exact JSON the client decodes (camelCase, nulls kept)."""
    return model.model_dump(mode="json", by_alias=True)


def metric_descriptor_dict(metric_key: str) -> dict[str, Any]:
    """One ``contracts/metrics.json`` entry, copied verbatim.

    Copied rather than referenced: the catalog is a process-wide cache, and a resolver that
    mutated a descriptor would corrupt every later response.
    """
    return deepcopy(dict(catalog.metric(metric_key)))


def metric_value_dict(
    metric_key: str,
    value: Optional[float],
    *,
    rank: Optional[int] = None,
    percentile: Optional[float] = None,
    league_average: Optional[float] = None,
    delta: Optional[float] = None,
    season: str | Sequence[str] | None = None,
    granularity: str = "season",
    availability: Optional[str] = None,
) -> dict[str, Any]:
    """A ``MetricValue`` as JSON, with the era rules already applied.

    Everything era-related goes through :func:`nbastats.api.serializers.metric_value`, so a
    metric the season never recorded is ``null`` with ``"unavailable"`` and an em dash — a
    widget cannot accidentally publish a zero.
    """
    return as_json(
        serializer_metric_value(
            metric_key,
            value,
            rank,
            percentile,
            league_average,
            delta,
            season,
            granularity=granularity,
            availability=availability,
        )
    )


def player_ref_dict(
    ctx: ResolveContext, player_id: int, season: str | None = None
) -> Optional[dict[str, Any]]:
    """One ``PlayerRef`` as JSON, with the team the player should be shown with."""
    row = player(ctx, player_id)
    if row is None:
        return None
    team_row = player_team_map(ctx, [player_id], season).get(int(player_id))
    return as_json(serializer_player_ref(row, team_row))


def player_ref_dict_for_team(
    ctx: ResolveContext, player_id: int, team_id: int | None
) -> Optional[dict[str, Any]]:
    """A ``PlayerRef`` naming an explicit team rather than the player's latest one.

    A leaderboard row is about one season, so it must name the team of *that* season: an
    all-time board that labelled Kareem's 1971-72 with "LAL" would be wrong by a decade.
    """
    row = player(ctx, player_id)
    if row is None:
        return None
    team_row = team(ctx, team_id) if team_id else None
    return as_json(serializer_player_ref(row, team_row))


def player_ref_dicts(
    ctx: ResolveContext, player_ids: Sequence[int], season: str | None = None
) -> dict[int, dict[str, Any]]:
    """``{player_id: PlayerRef JSON}`` for many players, in two queries rather than 2N."""
    ids = [int(pid) for pid in player_ids]
    directory = players(ctx, ids)
    team_map = player_team_map(ctx, ids, season)
    return {
        pid: as_json(serializer_player_ref(directory[pid], team_map.get(pid)))
        for pid in ids
        if pid in directory
    }


def team_ref_dict(ctx: ResolveContext, team_id: int) -> Optional[dict[str, Any]]:
    """One ``TeamRef`` as JSON."""
    row = team(ctx, team_id)
    return as_json(serializer_team_ref(row)) if row is not None else None


def game_ref_dict(ctx: ResolveContext, game: Game) -> dict[str, Any]:
    """One ``GameRef`` as JSON, resolving both franchises from the cached directory."""
    return as_json(serializer_game_ref(game, teams=all_teams(ctx)))


def subject_dict(
    ctx: ResolveContext, subject_type: str, subject_id: int, season: str | None = None
) -> dict[str, Any]:
    """The ``{"type", "player", "team"}`` wrapper ``stat_tile`` and ``shot_profile`` carry."""
    if subject_type == "team":
        return {"type": "team", "player": None, "team": team_ref_dict(ctx, subject_id)}
    return {
        "type": "player",
        "player": player_ref_dict(ctx, subject_id, season),
        "team": None,
    }
