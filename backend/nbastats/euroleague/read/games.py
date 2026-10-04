"""Games and box scores: the schedule, the results, and every player's line.

``GET /v1/el/games`` lists games as ``GameRefL`` objects. ``GET /v1/el/games/{gameId}`` is the box
score for one of them, whatever its season: the game id carries the season (``E2026-0012``), so
the route reads the season from the game and not from a parameter.

What a box score is allowed to say
----------------------------------
A line is exactly what the source recorded. A player who was listed but did not play
(``participation: "dnp"``) has ``stats: null``, because a dozen zeros for a man who never stepped
on the floor would be a claim; a statistic the source did not record for a game (the workbook's
box scores carry no fouls drawn, blocks against or plus/minus) is ``null`` in that line. A
shooting percentage is a fraction, and ``null`` when there were no attempts.

A game whose box score failed an invariant (``statsStatus: "quarantined"``) shows its result and
*no lines*, with a note saying why: the lines and the score disagreed, and which is wrong is not
knowable here. A game not yet played has no lines and says so.

Where a game's rows came from (``sourceRef``) is shown plainly: the workbook (and which file, by
the first eight characters of its hash), the EuroLeague's data service, or the invented demo
league. Raw payloads are never served.
"""

from __future__ import annotations

import json
from typing import Any, Final

from sqlalchemy import select

from ...shared import refs
from ..models import ElGame, ElPlayerGame, ElTeamGame
from .queries import ReadContext, bad_request, game_start, parse_phases
from .stats import line_payload

__all__ = ["build_games", "build_box_score", "source_ref_of"]

_STATS_NOTES: Final = {
    "none": "No box score is stored for this game yet.",
    "quarantined": (
        "The box score did not agree with the final score, so its lines are not shown."
    ),
}


def build_games(
    ctx: ReadContext,
    *,
    round_number: int | None,
    club_code: str | None,
    phase: str | None,
    freshness: dict[str, Any],
) -> dict[str, Any]:
    """``GameRefL`` for the games matching the filters, in schedule order."""
    club = ctx.require_club(club_code) if club_code else None
    phases = parse_phases(phase)
    if round_number is not None and round_number not in ctx.rounds():
        raise bad_request(f"There is no round {round_number} in this season.", "round")
    games = [
        g
        for g in ctx.games
        if (round_number is None or g.round_number == round_number)
        and (club is None or club in (g.home_club_code, g.away_club_code))
        and (phases is None or g.phase_code in phases)
    ]
    games.sort(key=lambda g: (g.round_number, game_start(g), g.game_id))
    return {
        "league": "euroleague",
        "season": ctx.season_label,
        "seasonCode": ctx.season_code,
        "freshness": freshness,
        "games": [ctx.game_ref(g) for g in games],
        "notes": ["EuroLeague games only: friendlies and domestic games are not tracked."],
    }


def source_ref_of(game: ElGame) -> dict[str, Any]:
    """Where a game's rows came from, in words a reader can use."""
    source = game.data_source
    if source.startswith("workbook:"):
        kind, label = "workbook", f"Your workbook ({source.split(':', 1)[1]})"
    elif source == "euroleague-v2":
        kind, label = "dataService", "EuroLeague data service"
    else:
        kind, label = "demo", "Invented demo league"
    return {"kind": kind, "label": label, "ingestedAt": refs.rfc3339(game.ingested_at)}


def _partials(game: ElGame) -> dict[str, list[int]] | None:
    if game.home_partials_json is None or game.away_partials_json is None:
        return None
    try:
        home, away = json.loads(game.home_partials_json), json.loads(game.away_partials_json)
    except ValueError:
        return None
    if not (isinstance(home, list) and isinstance(away, list)):
        return None
    return {"home": home, "away": away}


def build_box_score(ctx: ReadContext, game: ElGame, freshness: dict[str, Any]) -> dict[str, Any]:
    """``ElBoxScore``."""
    teams: list[dict[str, Any]] = []
    notes = ["EuroLeague games only."]
    if game.stats_status == "ok":
        session = ctx.session
        totals = {
            row.club_code: row
            for row in session.execute(
                select(ElTeamGame).where(ElTeamGame.game_id == game.game_id)
            ).scalars()
        }
        lines: dict[str, list[ElPlayerGame]] = {}
        for row in session.execute(
            select(ElPlayerGame).where(ElPlayerGame.game_id == game.game_id)
        ).scalars():
            lines.setdefault(row.club_code, []).append(row)
        for club in (game.home_club_code, game.away_club_code):
            players = sorted(
                lines.get(club, []),
                key=lambda r: (
                    r.participation != "played",
                    -(r.seconds_played or 0),
                    ctx.player_name(r.person_code).lower(),
                    r.person_code,
                ),
            )
            total = totals.get(club)
            teams.append(
                {
                    "team": ctx.team_ref(club),
                    "totals": line_payload(total) if total is not None else None,
                    "players": [
                        {
                            "player": ctx.player_ref(r.person_code, club),
                            "participation": r.participation,
                            "isStarter": r.is_starter if r.participation == "played" else None,
                            "stats": line_payload(r) if r.participation == "played" else None,
                        }
                        for r in players
                    ],
                }
            )
    else:
        notes.append(_STATS_NOTES[game.stats_status])
    return {
        "league": "euroleague",
        "game": ctx.game_ref(game),
        "partials": _partials(game) if game.status == "final" else None,
        "attendance": game.attendance if game.status == "final" else None,
        "statsStatus": game.stats_status,
        "teams": teams,
        "freshness": freshness,
        "sourceRef": source_ref_of(game),
        "notes": notes,
    }
