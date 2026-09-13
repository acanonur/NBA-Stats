"""Player search, the player page and the game log.

Three routes, three sharp edges:

* **Search** must be diacritic- and case-insensitive and rank exact prefix matches first,
  then active players, then career minutes. Folding accents cannot be expressed portably in
  SQL (SQLite has no unaccent), so the name index is folded in Python — the scan reads four
  narrow columns and only the ranked winners are loaded in full.
* **Career totals** are aggregated over the seasons that actually recorded each metric, never
  over a player's whole career blindly: a career spanning 1971-1980 has steals from 1973-74
  only, and dividing those by every game he played would understate them.
* **The game log** is keyset paginated on ``(date, gameId)`` — newest first, so a page
  boundary cannot skip or repeat a game when a stat correction rewrites a row.
"""
from __future__ import annotations

import unicodedata
from typing import Any, Iterable, Mapping, Optional, Sequence

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import catalog
from ..metrics import compute_metric
from ..models import (
    PLAYER_SEASON_TOTALS_COLUMNS,
    Game,
    Player,
    PlayerGameAdvanced,
    PlayerGameBasic,
    PlayerSeason,
)
from . import errors
from .deps import (
    SessionDep,
    clamp_limit,
    decode_cursor,
    encode_cursor,
    parse_metric_keys,
    parse_season_type,
    resolve_season,
)
from .schemas import (
    GameLogResponse,
    GameLogRow,
    PlayerDetail,
    PlayerSearchResponse,
    PlayerSearchResult,
    SeasonRow,
)
from .serializers import (
    availability_for,
    combined_availability,
    load_player_teams,
    load_teams,
    player_bio,
    player_game_values,
    player_ref,
    player_season_values,
    season_row,
)

__all__ = [
    "router",
    "DEFAULT_SEASON_METRICS",
    "FALLBACK_GAMELOG_METRICS",
    "default_gamelog_metrics",
    "fold_name",
]

router = APIRouter()

#: What a season line on the player page carries when the caller asks for nothing specific.
DEFAULT_SEASON_METRICS: tuple[str, ...] = (
    "gp",
    "min",
    "pts",
    "reb",
    "ast",
    "stl",
    "blk",
    "tov",
    "fg_pct",
    "fg3_pct",
    "ft_pct",
    "ts_pct",
    "efg_pct",
    "usg_pct",
    "off_rtg",
    "def_rtg",
    "net_rtg",
    "per",
)

#: Used only if ``contracts/widgets.json`` ever loses the ``game_log`` column default.
FALLBACK_GAMELOG_METRICS: tuple[str, ...] = (
    "min",
    "pts",
    "reb",
    "ast",
    "ts_pct",
    "usg_pct",
    "plus_minus",
    "game_score",
)

#: Career rates worth recomputing from summed components rather than averaging: an average
#: of season shooting percentages is not the career shooting percentage.
_DERIVED_FROM_TOTALS = frozenset(
    {
        "fg_pct",
        "fg3_pct",
        "ft_pct",
        "efg_pct",
        "ts_pct",
        "fg3a_rate",
        "ftr",
        "pps",
        "ast_tov",
        "ast_ratio",
        "tov_pct",
    }
)

#: Component columns fed to :func:`nbastats.metrics.compute_metric` for a career line.
_COMPONENT_KEYS: tuple[str, ...] = (
    "pts",
    "fgm",
    "fga",
    "fg3m",
    "fg3a",
    "ftm",
    "fta",
    "oreb",
    "dreb",
    "reb",
    "ast",
    "stl",
    "blk",
    "tov",
    "pf",
)

#: Characters NFKD will not decompose; without these "Luka Dončić" and "Dario Šarić" would
#: still be reachable but "Øystein" would not.
_TRANSLITERATE = str.maketrans(
    {
        "ø": "o",
        "Ø": "O",
        "đ": "d",
        "Đ": "D",
        "ł": "l",
        "Ł": "L",
        "ß": "ss",
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
        "þ": "th",
        "ð": "d",
    }
)


def default_gamelog_metrics() -> list[str]:
    """The game-log preset column set, read from ``contracts/widgets.json``."""
    columns = catalog.widget_config_defaults("game_log").get("columns") or []
    usable = [key for key in columns if catalog.has_metric(key)]
    return usable or list(FALLBACK_GAMELOG_METRICS)


def fold_name(text: str | None) -> str:
    """Casefold, strip diacritics and punctuation: ``"Nikola Jokić"`` → ``"nikola jokic"``."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text.translate(_TRANSLITERATE).lower())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in stripped)
    return " ".join(cleaned.split())


def _match_score(folded: str, parts: Sequence[str], needle: str) -> float:
    """How well a folded name matches the folded query; ``0.0`` means no match.

    The ladder is the contract's ranking rule: the whole name, then the name's start, then a
    surname prefix, then any name part, and only then a bare substring.
    """
    if not needle:
        return 0.0
    if folded == needle:
        return 1.0
    if folded.startswith(needle):
        return 0.92
    if parts and parts[-1].startswith(needle):
        return 0.88
    if any(part.startswith(needle) for part in parts):
        return 0.80
    if needle in folded:
        return 0.62
    return 0.0


def _career_minutes(session: Session, player_ids: Sequence[int]) -> dict[int, float]:
    """Total minutes played, used as the last tie-break in search ranking."""
    out: dict[int, float] = {}
    for start in range(0, len(player_ids), 400):
        chunk = player_ids[start : start + 400]
        rows = session.execute(
            select(PlayerSeason.player_id, PlayerSeason.minutes).where(
                PlayerSeason.player_id.in_(chunk)
            )
        ).all()
        for player_id, minutes in rows:
            out[player_id] = out.get(player_id, 0.0) + float(minutes or 0.0)
    return out


def _players_in_season(session: Session, season: str) -> set[int]:
    rows = session.execute(
        select(PlayerSeason.player_id).where(PlayerSeason.season == season).distinct()
    ).scalars()
    return set(rows)


@router.get(
    "/players/search", response_model=PlayerSearchResponse, summary="Search players by name"
)
def search_players(
    session: SessionDep,
    q: str = Query(..., min_length=2, max_length=64, description="At least two characters."),
    limit: int = Query(20, ge=1, le=50),
    active_only: bool = Query(False, alias="activeOnly"),
    season: Optional[str] = Query(None, description="Restrict to players who played it."),
) -> PlayerSearchResponse:
    """Rank name matches: exact prefix first, then active players, then career minutes."""
    needle = fold_name(q)
    if len(needle) < 2:
        raise errors.bad_request("The query must contain at least two letters.", "q")

    season_filter: set[int] | None = None
    if season is not None:
        resolved = resolve_season(session, season, field="season")
        season_filter = _players_in_season(session, str(resolved))

    candidates = session.execute(
        select(Player.player_id, Player.full_name, Player.last_name, Player.is_active)
    ).all()

    scored: list[tuple[float, bool, int]] = []
    for player_id, full_name, last_name, is_active in candidates:
        if active_only and not is_active:
            continue
        if season_filter is not None and player_id not in season_filter:
            continue
        folded = fold_name(full_name)
        parts = folded.split()
        if last_name:
            folded_last = fold_name(last_name)
            if folded_last and (not parts or parts[-1] != folded_last):
                parts = [*parts, folded_last]
        score = _match_score(folded, parts, needle)
        if score > 0:
            scored.append((score, bool(is_active), player_id))

    minutes = _career_minutes(session, [player_id for _, _, player_id in scored])
    scored.sort(key=lambda item: (-item[0], not item[1], -minutes.get(item[2], 0.0), item[2]))
    top = scored[:limit]

    ids = [player_id for _, _, player_id in top]
    rows = {
        row.player_id: row
        for row in session.execute(select(Player).where(Player.player_id.in_(ids)))
        .scalars()
        .all()
    }
    teams = load_player_teams(session, ids)

    results: list[PlayerSearchResult] = []
    for score, _active, player_id in top:
        row = rows.get(player_id)
        if row is None:  # pragma: no cover - only if a player is deleted mid-request
            continue
        base = player_ref(row, teams.get(player_id))
        results.append(
            PlayerSearchResult(
                **base.model_dump(),
                from_year=row.from_year,
                to_year=row.to_year,
                match_score=round(score, 4),
            )
        )
    return PlayerSearchResponse(query=q, results=results, next_cursor=None)


def _load_player(session: Session, player_id: int) -> Player:
    player = session.get(Player, player_id)
    if player is None:
        raise errors.player_not_found(player_id)
    return player


def _career_value(key: str, rows: Sequence[PlayerSeason]) -> Optional[float | int]:
    """One career number for ``key``, honest about which seasons could record it."""
    usable = [row for row in rows if availability_for(key, row.season) != "unavailable"]
    if not usable:
        return None

    if key == "gp":
        total = sum(row.gp or 0 for row in usable)
        return total or None
    if key == "gs":
        present = [row.gs for row in usable if row.gs is not None]
        return sum(present) if present else None

    if key in _DERIVED_FROM_TOTALS:
        components = _summed_components(usable)
        derived = compute_metric(key, row=components)
        if derived is not None:
            return derived

    total_column = PLAYER_SEASON_TOTALS_COLUMNS.get(key)
    if total_column is not None:
        pairs = [
            (row, getattr(row, total_column))
            for row in usable
            if getattr(row, total_column, None) is not None
        ]
        games = sum(row.gp or 0 for row, _ in pairs)
        if pairs and games:
            return float(sum(value for _, value in pairs)) / games
        return None

    # Ratings and model-fitted numbers (ORtg, PER, WS/48): a minutes-weighted mean, which is
    # the only defensible way to fold per-season rates into one career figure here.
    weighted = 0.0
    weight_total = 0.0
    column = key
    for row in usable:
        value = getattr(row, column, None)
        if value is None:
            continue
        weight = float(row.minutes or row.gp or 0) or 1.0
        weighted += float(value) * weight
        weight_total += weight
    return weighted / weight_total if weight_total else None


def _summed_components(rows: Iterable[PlayerSeason]) -> dict[str, float]:
    """Season totals summed into the row shape :func:`compute_metric` reads."""
    components: dict[str, float] = {}
    row_list = list(rows)
    for key in _COMPONENT_KEYS:
        column = PLAYER_SEASON_TOTALS_COLUMNS.get(key)
        if column is None:
            continue
        present = [getattr(row, column) for row in row_list if getattr(row, column, None) is not None]
        if present:
            components[key] = float(sum(present))
    minutes = [row.minutes for row in row_list if row.minutes is not None]
    if minutes:
        components["min"] = float(sum(minutes))
    return components


def _career_row(rows: Sequence[PlayerSeason], keys: Sequence[str]) -> SeasonRow:
    """The ``season = "Career"`` line of the player page."""
    values = {key: _career_value(key, rows) for key in keys}
    seasons = sorted({row.season for row in rows}, key=catalog.season_sort_key)
    availability = combined_availability(keys, seasons or None)
    return season_row(
        None,
        values,
        season="Career",
        season_type=rows[0].season_type if rows else None,
        gp=sum(row.gp or 0 for row in rows) or None,
        availability=availability,
    )


@router.get("/players/{player_id}", response_model=PlayerDetail, summary="One player")
def player_detail(
    session: SessionDep,
    player_id: int,
    metrics: Optional[str] = Query(None, description="Comma-separated metric keys."),
    season_type: Optional[str] = Query(None, alias="seasonType"),
) -> PlayerDetail:
    """Biography, every season the store holds, and the career line.

    ``careerTotals`` covers regular-season rows only — the convention every reference site
    follows — unless ``seasonType`` narrows the page to something else.
    """
    player = _load_player(session, player_id)
    keys = parse_metric_keys(metrics, DEFAULT_SEASON_METRICS, scope="player")
    wanted_type = parse_season_type(season_type) if season_type else None

    statement = select(PlayerSeason).where(PlayerSeason.player_id == player_id)
    if wanted_type is not None:
        statement = statement.where(PlayerSeason.season_type == wanted_type)
    rows = session.execute(statement).scalars().all()

    teams = load_teams(session, {row.team_id for row in rows})
    ordered = sorted(
        rows,
        key=lambda row: (
            catalog.season_sort_key(row.season),
            0 if row.season_type == "Regular Season" else 1,
        ),
        reverse=True,
    )

    seasons = [
        season_row(
            row,
            player_season_values(row, keys, row.season),
            team_abbr=teams[row.team_id].abbr if row.team_id in teams else None,
        )
        for row in ordered
    ]

    career_source = [
        row
        for row in rows
        if row.season_type == (wanted_type or "Regular Season")
    ]
    career = _career_row(career_source, keys) if career_source else None

    latest_team = None
    if ordered:
        latest_team = teams.get(ordered[0].team_id)
    return PlayerDetail(
        player=player_ref(player, latest_team),
        bio=player_bio(player),
        career_totals=career,
        seasons=seasons,
    )


@router.get(
    "/players/{player_id}/gamelog",
    response_model=GameLogResponse,
    summary="A player's games, newest first",
)
def player_gamelog(
    session: SessionDep,
    player_id: int,
    season: str = Query("latest", description="A season string, 'latest', or 'career'."),
    season_type: Optional[str] = Query("Regular Season", alias="seasonType"),
    limit: int = Query(50, ge=1, le=200),
    cursor: Optional[str] = Query(None),
    metrics: Optional[str] = Query(None, description="Comma-separated metric keys."),
) -> GameLogResponse:
    """One page of games. ``values`` carries every requested key, ``null`` where the era
    could not record it."""
    player = _load_player(session, player_id)
    resolved = resolve_season(session, season, allow_career=True)
    wanted_type = parse_season_type(season_type)
    keys = parse_metric_keys(metrics, default_gamelog_metrics(), scope="player")
    page_size = clamp_limit(limit, 50, 200)

    statement = (
        select(PlayerGameBasic, Game)
        .join(Game, Game.game_id == PlayerGameBasic.game_id)
        .where(PlayerGameBasic.player_id == player_id)
        .where(Game.season_type == wanted_type)
    )
    if resolved != "career":
        statement = statement.where(Game.season == resolved)

    keyset = decode_cursor(cursor)
    if keyset is not None:
        after_date = keyset.get("d")
        after_game = keyset.get("g")
        if not isinstance(after_date, str) or not isinstance(after_game, str):
            raise errors.bad_request("The cursor is not valid.", "cursor")
        statement = statement.where(
            (Game.game_date < _as_date(after_date))
            | ((Game.game_date == _as_date(after_date)) & (Game.game_id < after_game))
        )

    statement = statement.order_by(Game.game_date.desc(), Game.game_id.desc()).limit(
        page_size + 1
    )
    pairs = session.execute(statement).all()
    has_more = len(pairs) > page_size
    pairs = pairs[:page_size]

    advanced = _advanced_by_game(session, player_id, [game.game_id for _, game in pairs])
    teams = load_teams(
        session,
        {game.home_team_id for _, game in pairs} | {game.away_team_id for _, game in pairs},
    )

    rows: list[GameLogRow] = []
    for basic, game in pairs:
        is_home = basic.team_id == game.home_team_id
        opponent_id = game.away_team_id if is_home else game.home_team_id
        own_points = game.home_pts if is_home else game.away_pts
        opponent_points = game.away_pts if is_home else game.home_pts
        result = None
        score = None
        if own_points is not None and opponent_points is not None:
            result = "W" if own_points > opponent_points else "L" if own_points < opponent_points else "T"
            score = f"{own_points}-{opponent_points}"
        rows.append(
            GameLogRow(
                game_id=game.game_id,
                date=game.game_date,
                opponent_abbr=teams[opponent_id].abbr if opponent_id in teams else None,
                is_home=is_home,
                result=result,
                score=score,
                started=basic.started,
                minutes=basic.minutes,
                values=player_game_values(basic, advanced.get(game.game_id), keys, game.season),
                availability=combined_availability(keys, game.season, "game"),
            )
        )

    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = encode_cursor({"d": last.date.isoformat(), "g": last.game_id})

    team_map = load_player_teams(
        session, [player_id], None if resolved == "career" else str(resolved)
    )
    return GameLogResponse(
        player=player_ref(player, team_map.get(player_id)),
        season=None if resolved == "career" else resolved,
        season_type=wanted_type,
        rows=rows,
        next_cursor=next_cursor,
    )


def _as_date(value: str) -> Any:
    from datetime import date as _date

    try:
        return _date.fromisoformat(value)
    except ValueError as exc:
        raise errors.bad_request("The cursor is not valid.", "cursor") from exc


def _advanced_by_game(
    session: Session, player_id: int, game_ids: Sequence[str]
) -> Mapping[str, PlayerGameAdvanced]:
    """Advanced lines for one player's games — empty before 1996-97, by construction."""
    if not game_ids:
        return {}
    rows = (
        session.execute(
            select(PlayerGameAdvanced)
            .where(PlayerGameAdvanced.player_id == player_id)
            .where(PlayerGameAdvanced.game_id.in_(list(game_ids)))
        )
        .scalars()
        .all()
    )
    return {row.game_id: row for row in rows}
