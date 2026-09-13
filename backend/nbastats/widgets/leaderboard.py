"""``leaderboard`` — the top of the league by any metric, for a season or for all time.

Two scopes, one code path:

``season``    ranks the subjects of one season against each other.
``all_time``  ranks **single seasons** across the whole of league history, so each row names
              the season it is about. Wilt's 1961-62 and Jordan's 1987-88 are two rows, not
              two players.

Qualifiers (``minGames``, ``minMinutesPerGame``, ``positions``, ``teamIds``) are what keep a
leaderboard from being a list of people who played four minutes and made their only shot, and
``ascending`` flips the metric's own direction for the reader who wants the other end of the
table. The era gate runs per row against that row's own season: a 1971-72 line can never
enter a steals leaderboard, because nobody counted steals.

``GET /v1/leaders`` is this same resolver with query parameters instead of a widget config —
see :mod:`nbastats.api.routes_leaders` — which is why :func:`resolve_leaderboard` takes an
``offset`` the widget itself never uses.

Payload: ``contracts/CONTRACT.md`` §4 ``leaderboard``.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from .. import catalog
from . import queries as q
from .base import ResolveContext, attach_availability, resolve_season, resolve_subject_token

__all__ = ["resolve", "resolve_leaderboard", "qualifier_text", "DEFAULT_LIMIT"]

DEFAULT_LIMIT = 10


def resolve(config: dict[str, Any], ctx: ResolveContext) -> tuple[dict[str, Any], str, list[str]]:
    """Resolve one ``leaderboard`` widget."""
    payload, availability, notes, _ = resolve_leaderboard(config, ctx)
    return payload, availability, notes


def resolve_leaderboard(
    config: dict[str, Any], ctx: ResolveContext, *, offset: int = 0
) -> tuple[dict[str, Any], str, list[str], bool]:
    """Build the leaderboard payload, and say whether more rows follow.

    Returns ``(payload, availability, notes, has_more)``. ``has_more`` is what
    ``GET /v1/leaders`` turns into ``nextCursor``; the widget ignores it, because a dashboard
    tile is not paginated.
    """
    notes: list[str] = []
    subject_type = "team" if config.get("subjectType") == "team" else "player"
    metric_key = config.get("metric") or "pie"
    scope = config.get("scope") or "season"
    season = resolve_season(ctx, config.get("season"))
    season_type = config.get("seasonType") or "Regular Season"
    per_mode = config.get("perMode") or "PerGame"
    limit = max(1, int(config.get("limit") or DEFAULT_LIMIT))
    min_games = int(config.get("minGames") or 0)
    min_minutes = float(config.get("minMinutesPerGame") or 0.0)
    positions = list(config.get("positions") or [])
    ascending = bool(config.get("ascending", False))
    secondary_keys = [
        key
        for key in (config.get("secondaryMetrics") or [])
        if key != metric_key and subject_type in catalog.metric(key).get("scope", ())
    ]
    team_ids = _team_ids(config, ctx)

    ranked = q.leaders(
        ctx,
        metric_key,
        subject_type,
        season,
        season_type,
        per_mode=per_mode,
        scope=scope,
        min_games=min_games,
        min_minutes_per_game=min_minutes,
        positions=positions,
        team_ids=team_ids,
        ascending=ascending,
    )

    page = ranked[offset : offset + limit]
    has_more = len(ranked) > offset + limit

    if not ranked:
        notes.append(
            f"No {'team' if subject_type == 'team' else 'player'} met the qualifier for "
            f"{metric_key} in {season if scope == 'season' else 'any season'} "
            f"({season_type})."
        )

    rows = _rows(
        ctx, page, subject_type, season, season_type, per_mode, metric_key,
        secondary_keys, scope,
    )

    era_season = None if scope == "all_time" else season
    availability = attach_availability(notes, [metric_key], era_season, "season")

    payload: dict[str, Any] = {
        "metric": q.metric_descriptor_dict(metric_key),
        "subjectType": subject_type,
        "scope": scope,
        "season": season,
        "seasonType": season_type,
        "perMode": per_mode,
        "qualifier": qualifier_text(
            subject_type, min_games, min_minutes, positions, team_ids, ascending, scope
        ),
        "secondaryMetrics": [q.metric_descriptor_dict(key) for key in secondary_keys],
        "rows": rows,
    }
    return payload, availability, notes, has_more


def _team_ids(config: dict[str, Any], ctx: ResolveContext) -> list[int]:
    """Resolve the ``teamIds`` filter, which may hold ``$``-tokens as well as ids."""
    raw = config.get("teamIds") or []
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[int] = []
    for item in raw:
        resolved = resolve_subject_token(item, "team", ctx, field="teamIds")
        if resolved not in out:
            out.append(resolved)
    return out


def _rows(
    ctx: ResolveContext,
    page: Sequence[q.LeaderRow],
    subject_type: str,
    season: str,
    season_type: str,
    per_mode: str,
    metric_key: str,
    secondary_keys: Sequence[str],
    scope: str,
) -> list[dict[str, Any]]:
    """Serialise one page of ranked subjects, batching every reference lookup.

    An all-time board carries no ``leagueAverage``: its rows span eighty seasons, and there
    is no single league average to measure a 1962 line and a 2025 line against. The
    percentile is still there, and on an all-time board it means what the board means —
    where this season ranks among every qualified season in history.
    """
    if not page:
        return []

    # Warm both caches in one round trip each, so the loop below touches no database.
    if subject_type == "player":
        q.players(ctx, [entry.subject_id for entry in page])
    q.all_teams(ctx)

    # One distribution for the whole board rather than one per row: on a season board every
    # row shares a season, and on an all-time board there is no shared league to average.
    spread = (
        None
        if scope == "all_time"
        else q.distribution(ctx, metric_key, subject_type, season, season_type, per_mode)
    )

    rows: list[dict[str, Any]] = []
    for entry in page:
        row_season = entry.season
        value = q.metric_value_dict(
            metric_key,
            entry.value,
            rank=entry.rank,
            percentile=entry.percentile,
            league_average=spread.average if spread is not None else None,
            delta=spread.delta(entry.value) if spread is not None else None,
            season=row_season,
        )
        secondary = [
            q.metric_value_dict(
                key,
                q.season_value(entry.row, key, row_season, subject_type, per_mode),
                season=row_season,
            )
            for key in secondary_keys
        ]
        if subject_type == "team":
            player_payload: Optional[dict[str, Any]] = None
            team_payload: Optional[dict[str, Any]] = q.team_ref_dict(ctx, entry.subject_id)
        else:
            # The row's own team, not the player's current one: on an all-time board those
            # are decades apart.
            player_payload = q.player_ref_dict_for_team(
                ctx, entry.subject_id, getattr(entry.row, "team_id", None)
            )
            team_payload = None
        rows.append(
            {
                "rank": entry.rank,
                "player": player_payload,
                "team": team_payload,
                "value": value,
                "secondary": secondary,
                "season": row_season,
            }
        )
    return rows


def qualifier_text(
    subject_type: str,
    min_games: int,
    min_minutes: float,
    positions: Sequence[str],
    team_ids: Sequence[int],
    ascending: bool,
    scope: str,
) -> str:
    """The one-line caption under the title: ``"Minimum 15 games and 24.0 minutes per game"``.

    It is written out rather than left for the client to assemble because a leaderboard whose
    filters are invisible is a leaderboard that will be screenshotted and argued about.
    """
    thresholds: list[str] = []
    if min_games > 0:
        thresholds.append(f"{min_games} games")
    if min_minutes > 0 and subject_type == "player":
        thresholds.append(f"{min_minutes:.1f} minutes per game")

    if thresholds:
        sentence = "Minimum " + " and ".join(thresholds)
    elif subject_type == "team":
        sentence = "Every team"
    else:
        sentence = "No minimum"

    extras: list[str] = []
    if positions:
        extras.append("positions " + ", ".join(sorted(str(p) for p in positions)))
    if team_ids:
        extras.append(f"{len(team_ids)} team{'s' if len(team_ids) != 1 else ''}")
    if scope == "all_time":
        extras.append("best single seasons in league history")
    if ascending:
        extras.append("reversed order")

    return sentence + (" · " + " · ".join(extras) if extras else "")
