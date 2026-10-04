"""The method page: every constant behind the numbers, and where this model differs from the workbook.

``GET /v1/el/method`` is the workbook's "Method" sheet made live, and it is written by us rather
than copied: the constants are read from the store as they stand now, each with its provenance
(a number the workbook's author typed, a number Hardwood fitted from results, a number nobody has
checked, a number the user set), and the two lists of words below say plainly what this model does
differently from the workbook and what it cannot do.

Why provenance is shown on every constant
-----------------------------------------
Three kinds of number look identical on a screen and are not. The workbook's own settings are the
author's judgement. A *default* is Hardwood's starting value where the workbook has none (the
round weight beyond the second round, for one). A *fitted* value has been checked against results.
The two standard deviations that decide the width of every interval are the clearest case: the
workbook assumed them and nobody has tested them, so they are labelled ``workbookUnvalidated`` and
every projection says ``intervalBasis: "assumed"``, until a hundred locked projections have results
and they are fitted. Showing a constant without that label would claim more than is known.

What the lists contain
----------------------
``deviations``: each way a number here can differ from the workbook's, with the setting that
reproduces the workbook where one exists. ``limitations``: what the model does not do. Both are
sentences a user can read, kept next to the code that makes them true.
"""

from __future__ import annotations

from typing import Any, Final

from ...shared.defense_position import LIMITATIONS as DEFENSE_LIMITATIONS
from ...shared.team_projection import TOSS_UP_MARGIN, Z80
from ..model.player_rates import MINUTES_CAP, MINUTES_WEIGHT_OFFSET, TEAM_MINUTES
from ..model.round_projection import ProjectionUnavailable, get_model
from ..model.scorers import FORM_GAMES
from ..profile import PROFILE
from .queries import ReadContext

__all__ = ["DEVIATIONS", "LIMITATIONS", "build_method"]

DEVIATIONS: Final[tuple[str, ...]] = (
    "Actual scores are restated to regulation length before they move a rating, so an overtime "
    "game does not read as a collapse. The workbook did not scale. Set overtimeScaling to 0 to "
    "reproduce it.",
    "The default cap policy is consistent: the limit on a teammate's boost also limits the team "
    "total, so a team's projection equals the sum of its players' projections. The workbook caps "
    "each player but not the team, which can leave points that no player is credited with. Set "
    "capPolicyConsistent to 0 to reproduce the workbook.",
    "Each squad's projected points are scaled so the players add up to the club's rating "
    "(squadReconcile). Set it to 0 to use the squad as typed.",
    "An absence never raises scoring. When the missing players score less than replacement level "
    "would, the points recovered equal the points lost; the workbook's formula would add points.",
    "A round weight beyond the two the workbook states is 1 / (n + 9), where n is the club's game "
    "number counted from the start of the season, and it is marked extrapolated.",
    "Only games after the imported round update ratings and player rates. The imported numbers "
    "already contain the earlier rounds, so using them again would count them twice.",
    "A projection that was never frozen before tip-off is rebuilt from inputs published strictly "
    "before it and labelled reconstructed. The review reports those apart from locked ones.",
    "A scorer's form is the mean of his last ten official EuroLeague games in the store. The "
    "workbook's game logs mix in friendlies, national-team and domestic games; none is imported.",
    "A return expected in rounds a to b keeps an entry in force until the last game of round b "
    "has been played: the span of the absence, as the workbook means it.",
    "Where the workbook gives an 'In model' status it drives the projection; the research status is "
    "what is shown.",
    "Win probability is omitted. The margin and its 80% range say the same thing without "
    "producing a figure. The workbook's other probability columns, its typed comparison numbers "
    "and its hand-picked scorers are neither read nor computed.",
    "A squad's minutes are normalised to 200 and capped at 32 a man only once a game has been "
    "added; the imported minutes are the workbook's own.",
)

LIMITATIONS: Final[tuple[str, ...]] = (
    *DEFENSE_LIMITATIONS,
    "Availability is researched and dated, not live: the EuroLeague's data service has no injury "
    "list. Every status shows who said it and when.",
    "The spread around a projection is the workbook's assumption until enough locked projections "
    "have results to fit it.",
    "Two or three rounds are thin evidence: a rating moves only a fraction of each miss.",
    "Players outside a club's squad list are not modelled, and a player's club is the one he was "
    "listed with when the rates were set.",
    "Home advantage is one league-wide value, adjusted only by a game's own setting (a neutral "
    "venue is zero).",
    "Position tables are often silent: with few games and small differences between clubs, no "
    "club may differ from the league by more than chance, and the page says so.",
    "EuroLeague games only: friendlies, domestic leagues and national-team games are not tracked.",
)


def _structural() -> list[dict[str, Any]]:
    def row(key: str, value: float, provenance: str, description: str) -> dict[str, Any]:
        return {
            "key": key,
            "value": value,
            "provenance": provenance,
            "isDefault": provenance == "default",
            "description": description,
        }

    return [
        row(
            "teamMinutes",
            TEAM_MINUTES,
            "workbook",
            "Player-minutes per team per game: five players for forty minutes.",
        ),
        row("minutesCap", MINUTES_CAP, "workbook", "The most minutes one player is given."),
        row(
            "minutesBlendOffset",
            float(MINUTES_WEIGHT_OFFSET),
            "workbook",
            "The 3 in w = rounds / (rounds + 3) for blending projected minutes with minutes played.",
        ),
        row(
            "ratePriorMinutesOfficial",
            400.0,
            "workbook",
            "Minutes a per-40 rate from official statistics counts for when games are added.",
        ),
        row(
            "ratePriorMinutesEstimate",
            150.0,
            "workbook",
            "Minutes an estimated per-40 rate counts for when games are added.",
        ),
        row(
            "formGames",
            float(FORM_GAMES),
            "workbook",
            "Games a scorer's recent form averages over.",
        ),
        row(
            "intervalZ80", Z80, "workbook", "Multiplier of the standard deviation for an 80% range."
        ),
        row(
            "tossUpMargin",
            TOSS_UP_MARGIN,
            "workbook",
            "A projected margin smaller than this names no winner.",
        ),
        row(
            "defenseMinimumGames",
            float(PROFILE.defense.min_games),
            "default",
            "Games a defence needs before any position index is shown.",
        ),
        row(
            "defenseProvisionalBelowGames",
            float(PROFILE.defense.provisional_below),
            "default",
            "Below this many games position indices show without bands.",
        ),
    ]


def build_method(ctx: ReadContext, freshness: dict[str, Any]) -> dict[str, Any]:
    """``{constants, deviations, limitations}`` (design section 9.3), plus the model's version."""
    constants: list[dict[str, Any]] = []
    for key, setting in ctx.settings.items():
        if key.startswith("roundWeight.") and int(key.split(".", 1)[1]) > 10:
            continue  # the table is forty rows long and identical in form; ten shows the rule
        constants.append(
            {
                "key": key,
                "value": setting.value,
                "provenance": setting.provenance,
                "isDefault": setting.is_default,
                "description": setting.description,
            }
        )
    try:
        level = get_model(ctx).ratings.league_level
    except ProjectionUnavailable:
        level = None
    if level is not None:
        constants.append(
            {
                "key": "leagueAveragePoints",
                "value": level,
                "provenance": "derived",
                "isDefault": False,
                "description": "The mean of the clubs' scoring last season (Settings 'League average team score'); fixed for the season.",
            }
        )
    constants.extend(_structural())
    return {
        "league": "euroleague",
        "freshness": freshness,
        "constants": constants,
        "deviations": list(DEVIATIONS),
        "limitations": list(LIMITATIONS),
    }
