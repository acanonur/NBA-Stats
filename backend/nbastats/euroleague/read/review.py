"""The projection review: how the model's projections compare with what happened.

``GET /v1/el/projections/review`` answers one question honestly: *when the model said a score
before the game, how far off was it?* The honesty is in what is allowed to count.

Which projections are reviewed, and under which name
----------------------------------------------------
``locked`` rows (``modelKey: "hardwood"``)
    Frozen before tip-off. These are the only ones that measure the model as a forecaster, and
    the only ones calibration uses.
``imported`` rows (``modelKey: "workbook"``)
    The workbook's own published projections for rounds it had already played (scores only). They
    are reviewed under their own name so the workbook and the model are never blurred together.
``reconstructed`` (a separate block, never folded into the two above)
    Projections made after the game from inputs dated strictly before tip-off, for games that had
    no locked row. They say what the model *would* have said, which is a weaker claim than what it
    *did* say, and a reader who is told "the model called 7 of 10" deserves to know whether the
    model was asked in advance. A game with a locked row is never also counted here. The
    reconstructions are taken from the ledger when the worker has written them and from the model
    when it has not yet, so the review does not wait for a job.

When a final game has none of the three, the review says so ("No projection was recorded before
tip-off") rather than inventing one.

The four numbers
----------------
``winnersCalled`` is how many games the projected winner was right, out of ``decided`` (a
projection with a margin under half a point is a toss-up: it names no winner, is counted in
``tossUps`` and is excluded from the winners, as in the workbook's "winners 5 of 9, plus 1
toss-up"). ``meanAbsMarginMiss``, ``meanAbsScoreMiss`` (over both sides' scores) and
``meanAbsCombinedMiss`` are the average size of the miss in points; the sign is kept per game in
``marginMiss`` (actual margin minus projected margin, as the workbook's R2 Review ``Q``). Actual
scores are the final scores as played, overtime included.

Nothing here compares a projection with an outside number.
"""

from __future__ import annotations

import math
from typing import Any, Final

from sqlalchemy import select

from ...shared.team_projection import TOSS_UP_MARGIN
from ..models import ElGame, ElProjectionLedger
from .queries import ReadContext, game_has_result

__all__ = [
    "NO_PROJECTION_NOTE",
    "ReviewRow",
    "metrics",
    "review_rows",
    "review_blocks",
    "build_review",
]

NO_PROJECTION_NOTE: Final = "No projection was recorded before tip-off"


class ReviewRow:
    """One game's projected scores against its result."""

    __slots__ = ("game", "kind", "model_key", "home", "away")

    def __init__(self, game: ElGame, kind: str, model_key: str, home: float, away: float) -> None:
        self.game, self.kind, self.model_key, self.home, self.away = (
            game,
            kind,
            model_key,
            home,
            away,
        )

    @property
    def margin_miss(self) -> float:
        return (self.game.home_pts - self.game.away_pts) - (self.home - self.away)

    @property
    def combined_miss(self) -> float:
        """Actual combined points less projected combined points, signed like ``margin_miss``
        (negative: the game finished lower than projected). ``meanAbsCombinedMiss`` averages its
        absolute value."""
        return (self.game.home_pts + self.game.away_pts) - (self.home + self.away)

    @property
    def projected_winner(self) -> str | None:
        margin = self.home - self.away
        if abs(margin) < TOSS_UP_MARGIN:
            return None
        return "home" if margin >= 0 else "away"

    @property
    def winner_called(self) -> bool | None:
        winner = self.projected_winner
        if winner is None:
            return None
        actual = "home" if self.game.home_pts > self.game.away_pts else "away"
        return winner == actual


def metrics(rows: list[ReviewRow]) -> dict[str, Any]:
    """The review numbers for a set of rows; the means are ``None`` for an empty set."""
    n = len(rows)
    decided = [r for r in rows if r.winner_called is not None]
    if n == 0:
        mean_margin = mean_score = mean_combined = None
    else:
        mean_margin = math.fsum(abs(r.margin_miss) for r in rows) / n
        mean_score = math.fsum(
            abs(r.game.home_pts - r.home) + abs(r.game.away_pts - r.away) for r in rows
        ) / (2 * n)
        mean_combined = math.fsum(abs(r.combined_miss) for r in rows) / n
    return {
        "games": n,
        "decidedGames": len(decided),
        "winnersCalled": sum(1 for r in decided if r.winner_called),
        "tossUps": n - len(decided),
        "meanAbsMarginMiss": mean_margin,
        "meanAbsScoreMiss": mean_score,
        "meanAbsCombinedMiss": mean_combined,
    }


def review_rows(ctx: ReadContext, round_number: int | None = None) -> dict[str, list[ReviewRow]]:
    """``{"locked": [...], "imported": [...], "reconstructed": [...]}`` for the finals in scope."""
    games = {
        g.game_id: g
        for g in ctx.games
        if game_has_result(g) and (round_number is None or g.round_number == round_number)
    }
    out: dict[str, list[ReviewRow]] = {"locked": [], "imported": [], "reconstructed": []}
    if not games:
        return out
    ledger: dict[tuple[str, str], ElProjectionLedger] = {}
    for row in ctx.session.execute(
        select(ElProjectionLedger)
        .where(ElProjectionLedger.game_id.in_(list(games)))
        .order_by(ElProjectionLedger.ledger_id)
    ).scalars():
        ledger[(row.game_id, row.kind)] = row  # the newest of each kind wins
    reconstructed: dict[str, tuple[float, float]] = {}
    try:  # imported lazily: the model needs this package, not the other way round
        from ..model.round_projection import ProjectionUnavailable, get_model

        reconstructed = {
            gid: (r.match.home_points, r.match.away_points)
            for gid, r in get_model(ctx).reconstructed.items()
            if gid in games
        }
    except ProjectionUnavailable:
        reconstructed = {}
    for game_id, game in games.items():
        locked = ledger.get((game_id, "locked"))
        imported = ledger.get((game_id, "imported"))
        if locked is not None:
            out["locked"].append(
                ReviewRow(game, "locked", "hardwood", locked.home_pts, locked.away_pts)
            )
        else:
            stored = ledger.get((game_id, "reconstructed"))
            scores = (
                (stored.home_pts, stored.away_pts)
                if stored is not None
                else reconstructed.get(game_id)
            )
            if scores is not None:
                out["reconstructed"].append(ReviewRow(game, "reconstructed", "hardwood", *scores))
        if imported is not None:
            out["imported"].append(
                ReviewRow(game, "imported", "workbook", imported.home_pts, imported.away_pts)
            )
    return out


def review_blocks(rows: dict[str, list[ReviewRow]]) -> dict[str, Any]:
    """``byModel`` and ``reconstructed``: the summary every review and the slate carry."""
    by_model: list[dict[str, Any]] = []
    if rows["locked"]:
        by_model.append({"modelKey": "hardwood", **metrics(rows["locked"])})
    if rows["imported"]:
        by_model.append({"modelKey": "workbook", **metrics(rows["imported"])})
    reconstructed = (
        {"modelKey": "hardwood", **metrics(rows["reconstructed"])}
        if rows["reconstructed"]
        else None
    )
    return {"byModel": by_model, "reconstructed": reconstructed}


def _base_round(ctx: ReadContext) -> int | None:
    from ..model.round_projection import ProjectionUnavailable, get_model

    try:
        return get_model(ctx).base_round
    except ProjectionUnavailable:
        return None


def build_review(
    ctx: ReadContext, *, round_number: int | None, freshness: dict[str, Any]
) -> dict[str, Any]:
    """``ProjectionReview``."""
    rows = review_rows(ctx, round_number)
    blocks = review_blocks(rows)
    listed: list[dict[str, Any]] = []
    for row in (*rows["locked"], *rows["imported"], *rows["reconstructed"]):
        listed.append(
            {
                "game": ctx.game_ref(row.game),
                "kind": row.kind,
                "modelKey": row.model_key,
                "projected": {"homePts": row.home, "awayPts": row.away},
                "result": {"homePts": row.game.home_pts, "awayPts": row.game.away_pts},
                "marginMiss": row.margin_miss,
                "combinedMiss": row.combined_miss,
                "winnerCalled": row.winner_called,
            }
        )
    listed.sort(key=lambda item: (item["game"]["round"], item["game"]["gameId"], item["kind"]))
    covered = {item["game"]["gameId"] for item in listed if item["kind"] != "imported"}
    finals = [
        g
        for g in ctx.games
        if game_has_result(g) and (round_number is None or g.round_number == round_number)
    ]
    base_round = _base_round(ctx)
    pre_base = [g for g in finals if base_round is not None and g.round_number <= base_round]
    missing = [g for g in finals if g.game_id not in covered and g not in pre_base]
    notes = [
        "Reconstructed projections were made after the game from inputs before tip-off; they are "
        "reported apart from locked ones.",
    ]
    if missing:
        notes.append(f"{NO_PROJECTION_NOTE} for {len(missing)} of {len(finals)} final games.")
    if pre_base:
        notes.append(
            f"{len(pre_base)} final game(s) are in or before round {base_round}, which the imported "
            "ratings already contain, so the model made no projection for them; the workbook's own "
            "are reviewed under 'workbook'."
        )
    return {
        "league": "euroleague",
        "scope": {"season": ctx.season_label, "seasonCode": ctx.season_code, "round": round_number},
        "freshness": freshness,
        **blocks,
        "games": listed,
        "notes": notes,
    }
