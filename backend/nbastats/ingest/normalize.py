"""The V2/V3 translation layer: upstream shapes in, Hardwood columns out.

stats.nba.com speaks two dialects, and a pipeline that ignores the difference
silently loses columns:

**V2** (``LeagueGameLog``, ``PlayerGameLogs``, ``LeagueDashPlayerStats``,
``CommonAllPlayers``, ``ScoreboardV2``) returns a ``resultSets`` envelope of
parallel ``headers``/``rowSet`` arrays keyed by ``UPPER_SNAKE_CASE``::

    {"resultSets": [{"name": "LeagueGameLog",
                     "headers": ["GAME_ID", "PTS"], "rowSet": [["0022500512", 118]]}]}

**V3** (``BoxScoreTraditionalV3``, ``BoxScoreAdvancedV3``) returns nested objects
keyed by ``camelCase``, with the numbers one level down under ``statistics`` and a
different column set again::

    {"boxScoreTraditional": {"gameId": "0022500512",
                             "homeTeam": {"teamTricode": "LAL",
                                          "players": [{"personId": 2544,
                                                       "statistics": {"points": 32}}]}}}

Both land in this repo's ``snake_case`` schema through the explicit maps below.
Nothing here touches the database and nothing here computes a statistic: it
renames, parses and types, and that is all.

Rules this module keeps
-----------------------
* **Unknown upstream columns are ignored**, without error. The league adds
  columns (``WNBA_FANTASY_PTS``, tracking extras, rank columns) without notice.
* **A missing *expected* column warns and moves on.** Losing ``PLUS_MINUS``
  should show up in the log, never take down the night's ingest.
* **Percentages are fractions in ``[0, 1]``.** Every stats.nba.com endpoint used
  here already reports fractions (``"ts_pct": 0.615``); :func:`normalize_rows`
  takes ``percent_scale_100`` for sources that do not, so the scale is always a
  declared property of the source rather than a guess about a value.
* **Missing is ``None``, never ``0``.** An empty string, a ``"-"`` and an absent
  key are all missing; a real ``0`` survives as ``0``.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any, Callable, Iterable, Mapping, NamedTuple, Sequence

__all__ = [
    "LEAGUE_GAME_LOG_COLUMNS",
    "PLAYER_GAME_LOGS_BASE_COLUMNS",
    "PLAYER_GAME_LOGS_ADVANCED_COLUMNS",
    "BOX_SCORE_TRADITIONAL_V3_COLUMNS",
    "BOX_SCORE_ADVANCED_V3_COLUMNS",
    "BOX_SCORE_V3_IDENTITY_COLUMNS",
    "BOX_SCORE_V3_TEAM_COLUMNS",
    "LEAGUE_DASH_PLAYER_STATS_COLUMNS",
    "COMMON_ALL_PLAYERS_COLUMNS",
    "SCOREBOARD_GAME_HEADER_COLUMNS",
    "SCOREBOARD_LINE_SCORE_COLUMNS",
    "SEASON_TYPE_BY_GAME_ID_PREFIX",
    "BoxScore",
    "BoxScoreTeam",
    "parse_minutes",
    "parse_game_status",
    "parse_date",
    "parse_int",
    "parse_float",
    "parse_bool",
    "season_string",
    "season_start_year",
    "season_from_game_id",
    "season_type_from_game_id",
    "season_from_season_id",
    "result_set_rows",
    "result_set_names",
    "normalize_rows",
    "normalize_league_game_log",
    "normalize_player_game_logs",
    "normalize_box_score",
    "normalize_league_dash_player_stats",
    "normalize_common_all_players",
    "normalize_scoreboard",
    "opponent_from_matchup",
    "is_home_from_matchup",
]

logger = logging.getLogger("nbastats.ingest.normalize")

#: Values that mean "no value". A real ``0`` is never one of them.
_MISSING = {"", "-", "--", "none", "null", "nan"}

_ISO_MINUTES = re.compile(r"^PT(?:(\d+)M)?(?:([\d.]+)S)?$", re.IGNORECASE)

#: The season-type digit carried by every NBA game id: ``0022500512`` is a
#: regular-season game of 2025-26. ``None`` for an unrecognised code, so a caller
#: falls back to the season type it asked for rather than mislabelling the row.
SEASON_TYPE_BY_GAME_ID_PREFIX: dict[str, str] = {
    "1": "Pre Season",
    "2": "Regular Season",
    "3": "All Star",
    "4": "Playoffs",
    "5": "Play In",
}

#: NBA game ids carry a two-digit season year. The league started in 1946, so a
#: value at or above 46 belongs to the 1900s and anything below it to the 2000s.
_GAME_ID_CENTURY_PIVOT = 46


# --------------------------------------------------------------------------- #
# Scalar parsers
# --------------------------------------------------------------------------- #


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _MISSING
    return False


def parse_int(value: Any) -> int | None:
    """Integer, or ``None`` when the value is missing or not a number."""
    if _is_missing(value) or isinstance(value, bool):
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def parse_float(value: Any) -> float | None:
    """Float, or ``None`` when the value is missing or not a number."""
    if _is_missing(value) or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_bool(value: Any) -> bool | None:
    """Boolean from the several shapes upstream uses (``1``, ``"Y"``, ``True``)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in _MISSING:
        return None
    if text in {"1", "y", "yes", "true", "t"}:
        return True
    if text in {"0", "n", "no", "false", "f"}:
        return False
    return None


def parse_minutes(value: Any) -> float | None:
    """Minutes played as a float, from every shape the sources use.

    ==========================  ========
    Input                       Result
    ==========================  ========
    ``"34:12"``                 ``34.2``
    ``"34"`` / ``34`` / ``34.2``  as-is
    ``"PT34M12.00S"`` (V3 ISO)  ``34.2``
    ``None`` / ``""`` / ``"-"``   ``None``
    ==========================  ========

    A DNP is ``None``, not ``0.0``: the player did not play zero minutes, he has
    no minutes line at all. Seconds convert as a fraction of a minute, so
    ``"34:12"`` is ``34.2`` rather than ``34.12``.
    """
    if _is_missing(value) or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    iso = _ISO_MINUTES.match(text)
    if iso:
        minutes = float(iso.group(1) or 0)
        seconds = float(iso.group(2) or 0)
        return minutes + seconds / 60.0
    if ":" in text:
        head, _, tail = text.partition(":")
        minutes = parse_float(head)
        seconds = parse_float(tail)
        if minutes is None:
            return None
        return minutes + (seconds or 0.0) / 60.0
    return parse_float(text)


def parse_date(value: Any) -> date | None:
    """A calendar date from the several formats the sources use.

    Accepts ``date``/``datetime``, ``"2026-01-02"``, ``"2026-01-02T00:00:00"``,
    the V2 log's ``"JAN 02, 2026"``, and ``"01/02/2026"``.
    """
    if _is_missing(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    if "T" in text:
        text = text.split("T", 1)[0]
    if " " in text and "," not in text:
        text = text.split(" ", 1)[0]
    for pattern in ("%Y-%m-%d", "%b %d, %Y", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    logger.warning("unparsed_date value=%r", value)
    return None


def parse_game_status(
    status_id: Any = None, status_text: Any = None
) -> str:
    """Map the upstream game state onto the contract's three statuses.

    ``GAME_STATUS_ID`` is authoritative when present (1 scheduled, 2 live,
    3 final); otherwise the display text decides — ``"Final"`` and ``"Final/OT"``
    are final, a period or clock (``"Q3 4:21"``, ``"Halftime"``) is live, and a
    tip-off time (``"7:30 pm ET"``) is still scheduled. Anything unrecognised is
    ``"scheduled"``, the state that causes no data to be published.
    """
    code = parse_int(status_id)
    if code == 3:
        return "final"
    if code == 2:
        return "live"
    if code == 1:
        return "scheduled"

    text = ("" if status_text is None else str(status_text)).strip().lower()
    if not text:
        return "scheduled"
    if "final" in text or text == "f":
        return "final"
    if any(token in text for token in ("half", "end of", "ot", "q1", "q2", "q3", "q4")):
        return "live"
    if re.match(r"^\d+:\d+", text) and "et" not in text:
        return "live"
    return "scheduled"


# --------------------------------------------------------------------------- #
# Season and game-id vocabulary
# --------------------------------------------------------------------------- #


def season_string(start_year: int) -> str:
    """``2025`` → ``"2025-26"``."""
    return f"{int(start_year)}-{(int(start_year) + 1) % 100:02d}"


def season_start_year(season: str) -> int | None:
    """``"2025-26"`` → ``2025``; ``None`` when the string is not a season."""
    match = re.match(r"^(\d{4})-(\d{2})$", str(season).strip())
    return int(match.group(1)) if match else None


def season_from_game_id(game_id: str) -> str | None:
    """Season encoded in an NBA game id: ``"0022500512"`` → ``"2025-26"``.

    Digits 4-5 are the season's start year modulo 100. The league began in
    1946-47, so ``46``-``99`` are 1946-1999 and ``00``-``45`` are 2000-2045 —
    which keeps ``"0029600001"`` (1996-97) and ``"0020400001"`` (2004-05) apart.
    """
    text = str(game_id or "").strip()
    if len(text) < 5 or not text[3:5].isdigit():
        return None
    two_digit = int(text[3:5])
    year = 1900 + two_digit if two_digit >= _GAME_ID_CENTURY_PIVOT else 2000 + two_digit
    return season_string(year)


def season_type_from_game_id(game_id: str) -> str | None:
    """Season type encoded in an NBA game id, or ``None`` for an unknown code."""
    text = str(game_id or "").strip()
    if len(text) < 3:
        return None
    return SEASON_TYPE_BY_GAME_ID_PREFIX.get(text[2])


def season_from_season_id(season_id: Any) -> str | None:
    """``"22025"`` (V2's ``SEASON_ID``) → ``"2025-26"``."""
    text = str(season_id or "").strip()
    if len(text) == 5 and text.isdigit():
        return season_string(int(text[1:]))
    return None


def opponent_from_matchup(matchup: Any) -> str | None:
    """``"LAL vs. BOS"`` / ``"LAL @ BOS"`` → ``"BOS"``."""
    text = str(matchup or "").strip()
    for separator in (" vs. ", " vs ", " @ "):
        if separator in text:
            return text.split(separator, 1)[1].strip() or None
    return None


def is_home_from_matchup(matchup: Any) -> bool | None:
    """``"LAL vs. BOS"`` is home, ``"LAL @ BOS"`` is away, anything else unknown."""
    text = str(matchup or "")
    if " @ " in text:
        return False
    if " vs" in text:
        return True
    return None


# --------------------------------------------------------------------------- #
# Column maps
# --------------------------------------------------------------------------- #

#: ``LeagueGameLog`` (V2, ``PlayerOrTeam='T'``) → ``team_game`` vocabulary.
LEAGUE_GAME_LOG_COLUMNS: dict[str, str] = {
    "SEASON_ID": "season_id",
    "TEAM_ID": "team_id",
    "TEAM_ABBREVIATION": "team_abbr",
    "TEAM_NAME": "team_name",
    "GAME_ID": "game_id",
    "GAME_DATE": "game_date",
    "MATCHUP": "matchup",
    "WL": "wl",
    "MIN": "minutes",
    "FGM": "fgm",
    "FGA": "fga",
    "FG_PCT": "fg_pct",
    "FG3M": "fg3m",
    "FG3A": "fg3a",
    "FG3_PCT": "fg3_pct",
    "FTM": "ftm",
    "FTA": "fta",
    "FT_PCT": "ft_pct",
    "OREB": "oreb",
    "DREB": "dreb",
    "REB": "reb",
    "AST": "ast",
    "STL": "stl",
    "BLK": "blk",
    "TOV": "tov",
    "PF": "pf",
    "PTS": "pts",
    "PLUS_MINUS": "plus_minus",
}

#: ``PlayerGameLogs`` with ``MeasureType='Base'`` → ``player_game_basic``.
PLAYER_GAME_LOGS_BASE_COLUMNS: dict[str, str] = {
    "SEASON_YEAR": "season",
    "PLAYER_ID": "player_id",
    "PLAYER_NAME": "player_name",
    "TEAM_ID": "team_id",
    "TEAM_ABBREVIATION": "team_abbr",
    "TEAM_NAME": "team_name",
    "GAME_ID": "game_id",
    "GAME_DATE": "game_date",
    "MATCHUP": "matchup",
    "WL": "wl",
    "MIN": "minutes",
    "FGM": "fgm",
    "FGA": "fga",
    "FG_PCT": "fg_pct",
    "FG3M": "fg3m",
    "FG3A": "fg3a",
    "FG3_PCT": "fg3_pct",
    "FTM": "ftm",
    "FTA": "fta",
    "FT_PCT": "ft_pct",
    "OREB": "oreb",
    "DREB": "dreb",
    "REB": "reb",
    "AST": "ast",
    "TOV": "tov",
    "STL": "stl",
    "BLK": "blk",
    "PF": "pf",
    "PTS": "pts",
    "PLUS_MINUS": "plus_minus",
    "NBA_FANTASY_PTS": "fantasy_pts",
}

#: ``PlayerGameLogs`` with ``MeasureType='Advanced'`` → ``player_game_advanced``.
#:
#: ``TM_TOV_PCT`` is an upstream misnomer: on a *player* row it is that player's
#: turnover ratio, not his team's. ``E_``-prefixed columns are the league's own
#: estimates of the same quantity and are deliberately dropped — we keep the
#: measured column so one number has one meaning.
PLAYER_GAME_LOGS_ADVANCED_COLUMNS: dict[str, str] = {
    "SEASON_YEAR": "season",
    "PLAYER_ID": "player_id",
    "PLAYER_NAME": "player_name",
    "TEAM_ID": "team_id",
    "TEAM_ABBREVIATION": "team_abbr",
    "GAME_ID": "game_id",
    "GAME_DATE": "game_date",
    "MIN": "minutes",
    "OFF_RATING": "off_rtg",
    "DEF_RATING": "def_rtg",
    "NET_RATING": "net_rtg",
    "AST_PCT": "ast_pct",
    "AST_TOV": "ast_tov",
    "AST_RATIO": "ast_ratio",
    "OREB_PCT": "oreb_pct",
    "DREB_PCT": "dreb_pct",
    "REB_PCT": "reb_pct",
    "TM_TOV_PCT": "tov_pct",
    "EFG_PCT": "efg_pct",
    "TS_PCT": "ts_pct",
    "USG_PCT": "usg_pct",
    "PACE": "pace",
    "POSS": "poss",
    "PIE": "pie",
}

#: The V3 box score's per-player identity fields (outside ``statistics``).
BOX_SCORE_V3_IDENTITY_COLUMNS: dict[str, str] = {
    "personId": "player_id",
    "firstName": "first_name",
    "familyName": "last_name",
    "nameI": "name_initial",
    "playerSlug": "player_slug",
    "position": "position",
    "comment": "comment",
    "jerseyNum": "jersey",
}

#: The V3 box score's team identity fields.
BOX_SCORE_V3_TEAM_COLUMNS: dict[str, str] = {
    "teamId": "team_id",
    "teamCity": "team_city",
    "teamName": "team_nickname",
    "teamTricode": "team_abbr",
    "teamSlug": "team_slug",
}

#: ``BoxScoreTraditionalV3`` ``statistics`` → ``player_game_basic``/``team_game``.
BOX_SCORE_TRADITIONAL_V3_COLUMNS: dict[str, str] = {
    "minutes": "minutes",
    "fieldGoalsMade": "fgm",
    "fieldGoalsAttempted": "fga",
    "fieldGoalsPercentage": "fg_pct",
    "threePointersMade": "fg3m",
    "threePointersAttempted": "fg3a",
    "threePointersPercentage": "fg3_pct",
    "freeThrowsMade": "ftm",
    "freeThrowsAttempted": "fta",
    "freeThrowsPercentage": "ft_pct",
    "reboundsOffensive": "oreb",
    "reboundsDefensive": "dreb",
    "reboundsTotal": "reb",
    "assists": "ast",
    "steals": "stl",
    "blocks": "blk",
    "turnovers": "tov",
    "foulsPersonal": "pf",
    "points": "pts",
    "plusMinusPoints": "plus_minus",
}

#: ``BoxScoreAdvancedV3`` ``statistics`` → ``player_game_advanced``/``team_game``.
BOX_SCORE_ADVANCED_V3_COLUMNS: dict[str, str] = {
    "minutes": "minutes",
    "offensiveRating": "off_rtg",
    "defensiveRating": "def_rtg",
    "netRating": "net_rtg",
    "assistPercentage": "ast_pct",
    "assistToTurnover": "ast_tov",
    "assistRatio": "ast_ratio",
    "offensiveReboundPercentage": "oreb_pct",
    "defensiveReboundPercentage": "dreb_pct",
    "reboundPercentage": "reb_pct",
    "turnoverRatio": "tov_pct",
    "effectiveFieldGoalPercentage": "efg_pct",
    "trueShootingPercentage": "ts_pct",
    "usagePercentage": "usg_pct",
    "pace": "pace",
    "possessions": "poss",
    "PIE": "pie",
}

#: ``LeagueDashPlayerStats`` (Base and Advanced measure types) → ``player_season``.
LEAGUE_DASH_PLAYER_STATS_COLUMNS: dict[str, str] = {
    "PLAYER_ID": "player_id",
    "PLAYER_NAME": "player_name",
    "TEAM_ID": "team_id",
    "TEAM_ABBREVIATION": "team_abbr",
    "AGE": "age",
    "GP": "gp",
    "W": "wins",
    "L": "losses",
    "W_PCT": "win_pct",
    "MIN": "min_pg",
    "FGM": "fgm",
    "FGA": "fga",
    "FG_PCT": "fg_pct",
    "FG3M": "fg3m",
    "FG3A": "fg3a",
    "FG3_PCT": "fg3_pct",
    "FTM": "ftm",
    "FTA": "fta",
    "FT_PCT": "ft_pct",
    "OREB": "oreb",
    "DREB": "dreb",
    "REB": "reb",
    "AST": "ast",
    "TOV": "tov",
    "STL": "stl",
    "BLK": "blk",
    "PF": "pf",
    "PTS": "pts",
    "PLUS_MINUS": "plus_minus",
    "NBA_FANTASY_PTS": "fantasy_pts",
    "OFF_RATING": "off_rtg",
    "DEF_RATING": "def_rtg",
    "NET_RATING": "net_rtg",
    "AST_PCT": "ast_pct",
    "AST_TO": "ast_tov",
    "AST_RATIO": "ast_ratio",
    "OREB_PCT": "oreb_pct",
    "DREB_PCT": "dreb_pct",
    "REB_PCT": "reb_pct",
    "TM_TOV_PCT": "tov_pct",
    "EFG_PCT": "efg_pct",
    "TS_PCT": "ts_pct",
    "USG_PCT": "usg_pct",
    "PACE": "pace",
    "POSS": "poss",
    "PIE": "pie",
}

#: ``CommonAllPlayers`` → ``players``.
COMMON_ALL_PLAYERS_COLUMNS: dict[str, str] = {
    "PERSON_ID": "player_id",
    "DISPLAY_FIRST_LAST": "full_name",
    "DISPLAY_LAST_COMMA_FIRST": "name_last_first",
    "ROSTERSTATUS": "roster_status",
    "FROM_YEAR": "from_year",
    "TO_YEAR": "to_year",
    "PLAYERCODE": "player_code",
    "PLAYER_SLUG": "player_slug",
    "TEAM_ID": "team_id",
    "TEAM_CITY": "team_city",
    "TEAM_NAME": "team_nickname",
    "TEAM_ABBREVIATION": "team_abbr",
    "GAMES_PLAYED_FLAG": "games_played_flag",
}

#: ``ScoreboardV2``'s ``GameHeader`` result set → the ``games`` row.
SCOREBOARD_GAME_HEADER_COLUMNS: dict[str, str] = {
    "GAME_ID": "game_id",
    "GAME_DATE_EST": "game_date",
    "GAME_STATUS_ID": "status_id",
    "GAME_STATUS_TEXT": "status_text",
    "HOME_TEAM_ID": "home_team_id",
    "VISITOR_TEAM_ID": "away_team_id",
    "SEASON": "season_year",
    "LIVE_PERIOD": "period",
    "LIVE_PC_TIME": "clock",
}

#: ``ScoreboardV2``'s ``LineScore`` result set — the per-team score line.
SCOREBOARD_LINE_SCORE_COLUMNS: dict[str, str] = {
    "GAME_ID": "game_id",
    "TEAM_ID": "team_id",
    "TEAM_ABBREVIATION": "team_abbr",
    "TEAM_CITY_NAME": "team_city",
    "TEAM_NAME": "team_nickname",
    "PTS": "pts",
}

#: Target column → parser. Applied after renaming, so one table types both
#: dialects: ``MIN`` and ``minutes`` both become a float through ``parse_minutes``.
_TARGET_PARSERS: dict[str, Callable[[Any], Any]] = {
    "minutes": parse_minutes,
    "min_pg": parse_minutes,
    "game_date": parse_date,
    "player_id": parse_int,
    "team_id": parse_int,
    "home_team_id": parse_int,
    "away_team_id": parse_int,
    "status_id": parse_int,
    "period": parse_int,
    "age": parse_int,
    "gp": parse_int,
    "gs": parse_int,
    "wins": parse_int,
    "losses": parse_int,
    "from_year": parse_int,
    "to_year": parse_int,
    **{
        column: parse_int
        for column in (
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
            "pts",
        )
    },
    **{
        column: parse_float
        for column in (
            "plus_minus",
            "fantasy_pts",
            "fg_pct",
            "fg3_pct",
            "ft_pct",
            "efg_pct",
            "ts_pct",
            "usg_pct",
            "ast_pct",
            "ast_tov",
            "ast_ratio",
            "oreb_pct",
            "dreb_pct",
            "reb_pct",
            "tov_pct",
            "stl_pct",
            "blk_pct",
            "off_rtg",
            "def_rtg",
            "net_rtg",
            "pace",
            "poss",
            "pie",
            "win_pct",
            "game_score",
        )
    },
}

#: Target columns whose value is a percentage, for ``percent_scale_100``.
_PERCENT_TARGETS = frozenset(
    {
        "fg_pct",
        "fg3_pct",
        "ft_pct",
        "efg_pct",
        "ts_pct",
        "usg_pct",
        "ast_pct",
        "oreb_pct",
        "dreb_pct",
        "reb_pct",
        "tov_pct",
        "stl_pct",
        "blk_pct",
        "pie",
        "win_pct",
    }
)


# --------------------------------------------------------------------------- #
# V2 envelope
# --------------------------------------------------------------------------- #


def result_set_names(payload: Mapping[str, Any]) -> list[str]:
    """Names of the result sets in a V2 payload, for diagnostics."""
    sets = payload.get("resultSets") or payload.get("resultSet") or []
    if isinstance(sets, Mapping):
        sets = [sets]
    return [str(item.get("name", "")) for item in sets if isinstance(item, Mapping)]


def result_set_rows(payload: Mapping[str, Any], name: str | None = None) -> list[dict[str, Any]]:
    """Zip one V2 result set's ``headers`` and ``rowSet`` into a list of dicts.

    ``name`` selects the result set; with ``None`` the first one is used, which is
    what single-set endpoints return. An absent result set gives ``[]`` and a
    warning — an empty slate is a normal Tuesday, not an error.

    A payload that is already normalized (``{"LeagueGameLog": [{...}]}``, as
    ``nba_api``'s ``get_normalized_dict()`` returns) is passed through, so either
    recording style works as a fixture.
    """
    if name is not None and isinstance(payload.get(name), list):
        return [dict(row) for row in payload[name] if isinstance(row, Mapping)]

    sets = payload.get("resultSets") or payload.get("resultSet") or []
    if isinstance(sets, Mapping):
        sets = [sets]
    if not isinstance(sets, Sequence):
        return []

    chosen: Mapping[str, Any] | None = None
    for item in sets:
        if not isinstance(item, Mapping):
            continue
        if name is None or str(item.get("name", "")) == name:
            chosen = item
            break

    if chosen is None:
        logger.warning(
            "missing_result_set name=%s available=%s", name, ",".join(result_set_names(payload))
        )
        return []

    headers = [str(header) for header in chosen.get("headers", [])]
    rows: list[dict[str, Any]] = []
    for raw in chosen.get("rowSet", []) or []:
        if isinstance(raw, Mapping):
            rows.append(dict(raw))
            continue
        rows.append({header: value for header, value in zip(headers, raw)})
    return rows


def normalize_rows(
    rows: Iterable[Mapping[str, Any]],
    mapping: Mapping[str, str],
    *,
    required: Iterable[str] = (),
    percent_scale_100: Iterable[str] = (),
    context: str = "",
    extra: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Rename, type and scale one source's rows into Hardwood columns.

    ``mapping`` is source column → our column. Source columns outside it are
    ignored silently; columns in ``required`` that no row carries produce one
    warning each and are simply absent from the output. ``percent_scale_100``
    names *target* columns whose source reports 0-100 rather than a fraction —
    a property of the feed, declared by the caller, never inferred from a value.
    ``extra`` is merged into every row (season, season type, data source).
    """
    materialized = [row for row in rows if isinstance(row, Mapping)]
    scaled = {column for column in percent_scale_100}
    unknown_scaled = scaled - _PERCENT_TARGETS
    if unknown_scaled:
        logger.warning(
            "percent_scale_on_non_percent context=%s columns=%s",
            context or "?",
            ",".join(sorted(unknown_scaled)),
        )

    if materialized:
        seen = set()
        for row in materialized:
            seen.update(row.keys())
        for column in required:
            if column not in seen:
                logger.warning(
                    "missing_expected_column context=%s column=%s target=%s",
                    context or "?",
                    column,
                    mapping.get(column, "?"),
                )

    out: list[dict[str, Any]] = []
    for row in materialized:
        translated: dict[str, Any] = dict(extra or {})
        for source, target in mapping.items():
            if source not in row:
                continue
            value = row[source]
            parser = _TARGET_PARSERS.get(target)
            parsed = parser(value) if parser else (None if _is_missing(value) else value)
            if parsed is not None and target in scaled and target in _PERCENT_TARGETS:
                parsed = parsed / 100.0
            translated[target] = parsed
        out.append(translated)
    return out


# --------------------------------------------------------------------------- #
# Endpoint normalizers
# --------------------------------------------------------------------------- #


def normalize_league_game_log(
    payload: Mapping[str, Any],
    *,
    season: str | None = None,
    season_type: str | None = None,
) -> list[dict[str, Any]]:
    """``LeagueGameLog`` → one row per team per game.

    Each row also carries ``season``, ``season_type``, ``opponent_abbr``,
    ``is_home`` and ``won``, derived from the id, the ``SEASON_ID`` and the
    ``MATCHUP`` string so the caller does not have to re-parse them.
    """
    rows = normalize_rows(
        result_set_rows(payload, "LeagueGameLog"),
        LEAGUE_GAME_LOG_COLUMNS,
        required=("GAME_ID", "TEAM_ID", "PTS"),
        context="LeagueGameLog",
    )
    for row in rows:
        game_id = row.get("game_id")
        row["season"] = (
            season
            or season_from_season_id(row.pop("season_id", None))
            or season_from_game_id(game_id)
        )
        row.pop("season_id", None)
        row["season_type"] = season_type_from_game_id(game_id) or season_type
        row["opponent_abbr"] = opponent_from_matchup(row.get("matchup"))
        row["is_home"] = is_home_from_matchup(row.get("matchup"))
        wl = row.get("wl")
        row["won"] = None if wl is None else str(wl).upper().startswith("W")
    return rows


def normalize_player_game_logs(
    payload: Mapping[str, Any],
    measure_type: str = "Base",
    *,
    season: str | None = None,
    season_type: str | None = None,
) -> list[dict[str, Any]]:
    """``PlayerGameLogs`` → one row per player per game, Base or Advanced.

    The two measure types share an envelope and an id block but almost no stat
    columns, which is exactly the drift this module exists to absorb.
    """
    advanced = str(measure_type).strip().lower() == "advanced"
    mapping = (
        PLAYER_GAME_LOGS_ADVANCED_COLUMNS if advanced else PLAYER_GAME_LOGS_BASE_COLUMNS
    )
    rows = normalize_rows(
        result_set_rows(payload, "PlayerGameLogs"),
        mapping,
        required=("GAME_ID", "PLAYER_ID", "MIN"),
        context=f"PlayerGameLogs:{'Advanced' if advanced else 'Base'}",
    )
    for row in rows:
        game_id = row.get("game_id")
        row["season"] = season or row.get("season") or season_from_game_id(game_id)
        row["season_type"] = season_type_from_game_id(game_id) or season_type
        if not advanced:
            row["opponent_abbr"] = opponent_from_matchup(row.get("matchup"))
            row["is_home"] = is_home_from_matchup(row.get("matchup"))
            wl = row.get("wl")
            row["won"] = None if wl is None else str(wl).upper().startswith("W")
    return rows


class BoxScoreTeam(NamedTuple):
    """One team's side of a normalized V3 box score."""

    team_id: int | None
    abbr: str | None
    city: str | None
    nickname: str | None
    is_home: bool
    stats: dict[str, Any]
    players: list[dict[str, Any]]


class BoxScore(NamedTuple):
    """A normalized V3 box score: the game's identity plus both team sides."""

    game_id: str | None
    view: str
    home: BoxScoreTeam
    away: BoxScoreTeam
    game_date: date | None

    @property
    def teams(self) -> tuple[BoxScoreTeam, BoxScoreTeam]:
        """Both sides, home first."""
        return (self.home, self.away)

    def players(self) -> list[dict[str, Any]]:
        """Every player row from both sides, each carrying its ``team_id``."""
        return [*self.home.players, *self.away.players]


def _v3_root(payload: Mapping[str, Any], view: str) -> Mapping[str, Any]:
    """The object under the V3 envelope, whichever key this view uses."""
    for key in (f"boxScore{view.capitalize()}", "boxScoreTraditional", "boxScoreAdvanced"):
        node = payload.get(key)
        if isinstance(node, Mapping):
            return node
    return payload if isinstance(payload, Mapping) else {}


def _v3_side(
    node: Mapping[str, Any], is_home: bool, stat_columns: Mapping[str, str], view: str
) -> BoxScoreTeam:
    identity = normalize_rows(
        [node], BOX_SCORE_V3_TEAM_COLUMNS, context=f"BoxScore{view}:team"
    )
    ident = identity[0] if identity else {}
    team_id = ident.get("team_id")

    team_stats = normalize_rows(
        [node.get("statistics") or {}],
        stat_columns,
        context=f"BoxScore{view}:teamStatistics",
    )
    stats = team_stats[0] if team_stats else {}
    stats.update(
        {
            "team_id": team_id,
            "team_abbr": ident.get("team_abbr"),
            "is_home": is_home,
        }
    )

    players: list[dict[str, Any]] = []
    for entry in node.get("players") or []:
        if not isinstance(entry, Mapping):
            continue
        who = normalize_rows(
            [entry], BOX_SCORE_V3_IDENTITY_COLUMNS, context=f"BoxScore{view}:player"
        )[0]
        line = normalize_rows(
            [entry.get("statistics") or {}],
            stat_columns,
            required=("minutes",),
            context=f"BoxScore{view}:playerStatistics",
        )[0]
        line.update(who)
        line["team_id"] = team_id
        line["team_abbr"] = ident.get("team_abbr")
        line["is_home"] = is_home
        # V3 marks a starter by giving him a position; the bench has "".
        position = (who.get("position") or "").strip()
        line["started"] = bool(position)
        line["did_not_play"] = line.get("minutes") is None
        players.append(line)

    return BoxScoreTeam(
        team_id=team_id,
        abbr=ident.get("team_abbr"),
        city=ident.get("team_city"),
        nickname=ident.get("team_nickname"),
        is_home=is_home,
        stats=stats,
        players=players,
    )


def normalize_box_score(payload: Mapping[str, Any], view: str = "traditional") -> BoxScore:
    """``BoxScoreTraditionalV3`` / ``BoxScoreAdvancedV3`` → a :class:`BoxScore`.

    ``view`` is ``"traditional"`` or ``"advanced"`` and selects the statistics
    map; the identity fields are shared. The V3 payload does not always carry the
    game date, so ``game_date`` is ``None`` unless the feed included one — the
    caller supplies it from the scoreboard or the stored row rather than guessing.
    """
    key = str(view).strip().lower()
    if key not in {"traditional", "advanced"}:
        raise ValueError(f"view must be 'traditional' or 'advanced', got {view!r}")
    stat_columns = (
        BOX_SCORE_TRADITIONAL_V3_COLUMNS if key == "traditional" else BOX_SCORE_ADVANCED_V3_COLUMNS
    )

    node = _v3_root(payload, key)
    home_node = node.get("homeTeam") if isinstance(node.get("homeTeam"), Mapping) else {}
    away_node = node.get("awayTeam") if isinstance(node.get("awayTeam"), Mapping) else {}
    if not home_node and not away_node:
        logger.warning("box_score_without_teams game_id=%s view=%s", node.get("gameId"), key)

    game_date = None
    for candidate in ("gameEt", "gameTimeEt", "gameTimeUTC", "gameDate"):
        if node.get(candidate):
            game_date = parse_date(node[candidate])
            if game_date is not None:
                break

    return BoxScore(
        game_id=node.get("gameId"),
        view=key,
        home=_v3_side(home_node, True, stat_columns, key),
        away=_v3_side(away_node, False, stat_columns, key),
        game_date=game_date,
    )


def normalize_league_dash_player_stats(
    payload: Mapping[str, Any], *, season: str | None = None, season_type: str | None = None
) -> list[dict[str, Any]]:
    """``LeagueDashPlayerStats`` → season rows (Base or Advanced measure type)."""
    rows = normalize_rows(
        result_set_rows(payload, "LeagueDashPlayerStats"),
        LEAGUE_DASH_PLAYER_STATS_COLUMNS,
        required=("PLAYER_ID", "GP"),
        context="LeagueDashPlayerStats",
        extra={"season": season, "season_type": season_type},
    )
    return rows


def normalize_common_all_players(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """``CommonAllPlayers`` → ``players`` rows, with the name split out.

    ``DISPLAY_LAST_COMMA_FIRST`` is the reliable split — a name like "Karl-Anthony
    Towns" cannot be split on whitespace — so first/last come from there when the
    upstream supplies it.
    """
    rows = normalize_rows(
        result_set_rows(payload, "CommonAllPlayers"),
        COMMON_ALL_PLAYERS_COLUMNS,
        required=("PERSON_ID", "DISPLAY_FIRST_LAST"),
        context="CommonAllPlayers",
    )
    for row in rows:
        last_first = row.pop("name_last_first", None)
        first = last = None
        if last_first and "," in str(last_first):
            last, _, first = str(last_first).partition(",")
            last, first = last.strip() or None, first.strip() or None
        elif row.get("full_name"):
            parts = str(row["full_name"]).split(" ", 1)
            first = parts[0]
            last = parts[1] if len(parts) > 1 else None
        row["first_name"] = first
        row["last_name"] = last
        status = row.pop("roster_status", None)
        row["is_active"] = None if status is None else bool(parse_int(status))
    return rows


def normalize_scoreboard(
    payload: Mapping[str, Any], *, game_date: date | None = None
) -> list[dict[str, Any]]:
    """``ScoreboardV2`` → one ``games``-shaped row per game on the slate.

    Joins ``GameHeader`` to ``LineScore`` for the scores, maps the status onto
    the contract's three values, and derives season and season type from the game
    id. ``clock`` is normalized to ``None`` when it is blank or zero, so a final
    game never carries a stale clock string.
    """
    headers = normalize_rows(
        result_set_rows(payload, "GameHeader"),
        SCOREBOARD_GAME_HEADER_COLUMNS,
        required=("GAME_ID", "GAME_STATUS_ID"),
        context="ScoreboardV2:GameHeader",
    )
    line_scores = normalize_rows(
        result_set_rows(payload, "LineScore"),
        SCOREBOARD_LINE_SCORE_COLUMNS,
        context="ScoreboardV2:LineScore",
    )
    points: dict[tuple[str, int], int | None] = {}
    for line in line_scores:
        game_id, team_id = line.get("game_id"), line.get("team_id")
        if game_id is not None and team_id is not None:
            points[(str(game_id), int(team_id))] = line.get("pts")

    games: list[dict[str, Any]] = []
    for header in headers:
        game_id = str(header.get("game_id") or "")
        status = parse_game_status(header.pop("status_id", None), header.pop("status_text", None))
        home_id, away_id = header.get("home_team_id"), header.get("away_team_id")
        clock = (str(header.get("clock") or "")).strip()
        season_year = header.pop("season_year", None)
        games.append(
            {
                "game_id": game_id,
                "game_date": header.get("game_date") or game_date,
                "season": season_from_game_id(game_id)
                or (season_string(int(season_year)) if str(season_year).isdigit() else None),
                "season_type": season_type_from_game_id(game_id),
                "home_team_id": home_id,
                "away_team_id": away_id,
                "home_pts": points.get((game_id, int(home_id))) if home_id else None,
                "away_pts": points.get((game_id, int(away_id))) if away_id else None,
                "status": status,
                "period": header.get("period"),
                "clock": clock if clock and clock not in {"0.0", "0:00", "00:00.0"} else None,
            }
        )
    return games
