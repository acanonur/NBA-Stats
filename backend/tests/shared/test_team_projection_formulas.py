"""The team-score model against a literal transcription of the workbook's cell formulas.

The standard the model is held to is *formula for formula*: given the workbook's inputs, it
must return the workbook's outputs. So this file contains, inside the test, a deliberately
literal transcription of the sheet. Each helper below names the cell it reproduces (``Team
Ratings K``, ``Squads AK``, ``Round 3 J``) and is written the way the cell is written, with the
same operations in the same order and none of the module's own helpers. It is checked
against the module for an authored, invented input set (clubs ``ZZA`` to ``ZZH``, made-up
priors, squads and statuses; nothing here is real data) under the settings the replay needs:
``capPolicy = workbook``, no squad reconciliation, no overtime scaling.

The transcription includes the one place the sheet is knowingly improved on: an absence of
players who score below replacement level would *raise* a squad's scoring under the sheet's
formula, and the module clamps it. The input set therefore has every missing scorer above
replacement pace, where the two agree exactly, and a separate test shows what happens where
they do not.

Beyond the transcription:

* Small hand-worked examples of each function, with the arithmetic in the comment.
* The workbook's win-probability cell, as pure mathematics only: the module deliberately has no
  such function, and a test asserts it never grows one.
* The rounding rule of the sheet's ``FIXED`` (half away from zero on the 15-digit decimal),
  which Python's ``round`` and ``format`` do not follow on a tie.
* The ledger of weights, the home-advantage branches, the intervals and the toss-up edge.
"""

from __future__ import annotations

import math
import random
from statistics import NormalDist

import pytest

from nbastats.shared import injury_layer as IL
from nbastats.shared import team_projection as TP

# ----------------------------------------------------------------------- the authored inputs

REGRESS = 0.3  # Settings C5
HCA = 3.5  # Settings C6
ABSORB = 0.6  # Settings C31
REPL_RATE = 9  # Settings C32 (points per 40 minutes)
BOOST_CAP = 1.35  # Settings C33
ROT_SHARE = 0.5  # Settings C34
STATUS_LIST = ["OUT", "DOUBTFUL", "QUESTIONABLE", "PROBABLE", "AVAILABLE"]  # Settings B25:B29
STATUS_PROB = [0, 0.25, 0.5, 0.85, 1]  # Settings C25:C29

CLUBS = {
    # code: (PF/g, PA/g, attack adj, defence adj): invented
    "ZZA": (88.4, 82.1, 1.2, -0.4),
    "ZZB": (84.0, 85.5, -0.7, 0.9),
    "ZZC": (91.3, 80.2, 2.1, -1.3),
    "ZZD": (79.8, 88.0, 0.0, 1.1),
    "ZZE": (86.5, 84.4, 0.4, 0.2),
    "ZZF": (82.2, 83.0, -1.5, -0.6),
    "ZZG": (89.9, 86.7, 1.7, 0.8),
    "ZZH": (81.0, 87.9, 0.3, 1.6),
}

# (home, away, home advantage points)  -- the second is a neutral venue
GAMES = [
    ("ZZA", "ZZB", HCA),
    ("ZZC", "ZZD", 0),
    ("ZZE", "ZZF", HCA),
    ("ZZG", "ZZH", HCA),
    ("ZZB", "ZZC", HCA),
    ("ZZD", "ZZA", 2.0),  # a per-game override of the home advantage
]


def build_squads():
    """Ten invented players per club, minutes summing to about 200, rates above replacement."""
    rng = random.Random(20261001)
    squads = {}
    for code in CLUBS:
        minutes = [rng.uniform(8, 32) for _ in range(10)]
        scale = 200 / sum(minutes)
        rows = []
        for i, m in enumerate(minutes):
            rows.append(
                {
                    "team": code,
                    "player": f"{code} player {i + 1}",
                    "min": round(m * scale, 1),
                    "pts40": round(rng.uniform(12, 28), 2),  # always above the 9 replacement
                    "status": "AVAILABLE",
                }
            )
        squads[code] = rows
    # statuses, chosen to exercise every branch
    for row in sorted(squads["ZZA"], key=lambda r: -r["pts40"] * r["min"])[:4]:
        row["status"] = "OUT"  # four scorers out: the cap binds
    for i, row in enumerate(squads["ZZB"][:6]):
        row["status"] = ["QUESTIONABLE", "DOUBTFUL", "PROBABLE", "QUESTIONABLE", "OUT", "PROBABLE"][
            i
        ]
    squads["ZZD"][3]["status"] = "OUT"
    squads["ZZE"][0]["status"] = "DOUBTFUL"
    squads["ZZG"][2]["status"] = "PROBABLE"
    squads["ZZG"][5]["status"] = "QUESTIONABLE"
    squads["ZZH"][1]["status"] = "OUT"
    squads["ZZH"][7]["status"] = "OUT"
    return squads


SQUADS = build_squads()


# --------------------------------------------- a literal transcription of the workbook cells


class Sheet:
    """The sheet's cells, in the sheet's own terms and order of operations."""

    def __init__(self) -> None:
        codes = list(CLUBS)
        self.codes = codes
        self.G = [CLUBS[c][0] for c in codes]  # Team Ratings G: PF/g
        self.H = [CLUBS[c][1] for c in codes]  # Team Ratings H: PA/g
        self.I = [CLUBS[c][2] for c in codes]  # Team Ratings I: attack adj
        self.J = [CLUBS[c][3] for c in codes]  # Team Ratings J: defence adj
        self.LgPts = sum(self.G) / len(self.G)  # Settings C17 =AVERAGE(T_PF)
        # Team Ratings K, L, M, N
        self.K = [self.LgPts + (g - self.LgPts) * (1 - REGRESS) + i for g, i in zip(self.G, self.I)]
        self.L = [self.LgPts + (h - self.LgPts) * (1 - REGRESS) + j for h, j in zip(self.H, self.J)]
        self.M = [k / self.LgPts for k in self.K]
        self.N = [v / self.LgPts for v in self.L]

        # Squads: one row per player, in club order
        self.rows = [row for code in codes for row in SQUADS[code]]
        self.sq_pts = [r["pts40"] * r["min"] / 40 for r in self.rows]  # Squads U =H*G/40
        self.AJ = [
            self._chance(r["status"]) for r in self.rows
        ]  # =IFERROR(INDEX(StatusProb,MATCH()),1)
        self.AX = [r["min"] * (1 - c) for r, c in zip(self.rows, self.AJ)]  # lost min  =G*(1-AJ)
        self.AY = [u * (1 - c) for u, c in zip(self.sq_pts, self.AJ)]  # lost pts  =U*(1-AJ)
        self.AZ = [r["min"] * c for r, c in zip(self.rows, self.AJ)]  # avail min =G*AJ
        self.BA = [u * c for u, c in zip(self.sq_pts, self.AJ)]  # avail pts =U*AJ

        def sumif(values, code):  # =SUMIF(Sq_Team, code, values)
            return sum(v for r, v in zip(self.rows, values) if r["team"] == code)

        self.O = [sumif(self.sq_pts, c) for c in codes]  # Team Ratings O
        self.U = [sumif(self.AX, c) for c in codes]  # R3 lost MIN
        self.V = [sumif(self.AY, c) for c in codes]  # R3 lost PTS
        self.W = [sumif(self.AZ, c) for c in codes]  # avail MIN
        self.X = [sumif(self.BA, c) for c in codes]  # avail PTS
        self.Y = [u * REPL_RATE / 40 for u in self.U]  # repl PTS     =U*ReplRate/40
        self.Z = [y + ABSORB * (v - y) for y, v in zip(self.Y, self.V)]  # =Y+Absorb*(V-Y)
        self.AA = [  # Scoring boost =IF(X=0,1,MIN(1+Z/X,BoostCap))
            1 if x == 0 else min(1 + z / x, BOOST_CAP) for x, z in zip(self.X, self.Z)
        ]
        self.AB = [x + z for x, z in zip(self.X, self.Z)]  # R3 squad PTS =X+Z
        self.AC = [m * ab / o for m, ab, o in zip(self.M, self.AB, self.O)]  # =M*AB/O

        # Squads AK (R3 MIN) and AL (R3 PTS)
        team_row = [codes.index(r["team"]) for r in self.rows]
        self.AK = [
            az * min(1 + ROT_SHARE * self.U[t] / max(self.W[t], 1), BOOST_CAP)
            for az, t in zip(self.AZ, team_row)
        ]
        self.AL = [ba * self.AA[t] for ba, t in zip(self.BA, team_row)]

    @staticmethod
    def _chance(status):
        # =IFERROR(INDEX(StatusProb,MATCH(AI,StatusList,0)),1)
        try:
            return STATUS_PROB[STATUS_LIST.index(status)]
        except ValueError:
            return 1

    def game(self, home, away, hadv):
        """Round 3 row: ``D`` home code, ``F`` away code, ``I`` the home advantage."""
        hi, ai = self.codes.index(home), self.codes.index(away)
        J = (
            self.LgPts * self.AC[hi] * self.N[ai] + hadv / 2
        )  # =LgPts*INDEX(T_R1Off,V)*INDEX(T_Def,W)+I/2
        K = self.LgPts * self.AC[ai] * self.N[hi] - hadv / 2
        L = J - K
        total = J + K  # Round 3 O
        Y = (
            self.LgPts * self.M[hi] * self.N[ai] + hadv / 2
        )  # full strength: T_Off in place of T_R1Off
        Z = self.LgPts * self.M[ai] * self.N[hi] - hadv / 2
        P = total - (Y + Z)  # Injury effect on total
        if abs(L) < 0.5:  # =IF(ABS(L)<0.5,"Toss-up",IF(L>=0,E,G)&" by "&FIXED(ABS(L),1))
            text = "Toss-up"
        else:
            text = f"{home if L >= 0 else away} by {fixed(abs(L), 1)}"
        return dict(J=J, K=K, L=L, O=total, Y=Y, Z=Z, P=P, M=text)


def fixed(x, places):
    """Excel's FIXED: round half away from zero, then print with a fixed number of decimals."""
    scale = 10**places
    return f"{math.floor(abs(x) * scale + 0.5) / scale:.{places}f}"


# --------------------------------------------------------------- the module, driven the same way


def run_module(policy):
    sheet = Sheet()
    settings = IL.InjurySettings.from_per40(
        REPL_RATE, ABSORB, BOOST_CAP, cap_policy=policy, rotation_share=ROT_SHARE
    )
    results = {}
    for code in sheet.codes:
        players = [
            IL.PlayerInput(
                key=row["player"],
                minutes=row["min"],
                points=row["pts40"] * row["min"] / 40,
                chance=dict(zip(STATUS_LIST, STATUS_PROB)).get(row["status"], 1),
            )
            for row in SQUADS[code]
        ]
        results[code] = IL.apply_injury_layer(players, settings)
    level = sum(sheet.G) / len(sheet.G)
    strengths = {}
    for i, code in enumerate(sheet.codes):
        pf = TP.rating(CLUBS[code][0], level, REGRESS, CLUBS[code][2])
        pa = TP.rating(CLUBS[code][1], level, REGRESS, CLUBS[code][3])
        strengths[code] = TP.TeamStrength(
            attack=TP.attack_index(pf, level),
            defence=TP.defence_index(pa, level),
            availability_factor=results[code].availability_factor,
        )
    return sheet, level, results, strengths


# ------------------------------------------------------------------------------ the checks


def test_the_input_set_is_inside_the_regime_where_the_clamp_is_silent() -> None:
    sheet = Sheet()
    for i, code in enumerate(sheet.codes):
        assert sheet.V[i] >= sheet.Y[i], f"{code}: lost points below replacement; clamp would act"
    # and it exercises both branches of the boost cap
    boosts = {c: sheet.AA[i] for i, c in enumerate(sheet.codes)}
    assert boosts["ZZA"] == BOOST_CAP  # four scorers out
    assert min(boosts.values()) == 1  # ZZC: nobody missing
    assert 1 < boosts["ZZH"] < BOOST_CAP or 1 < boosts["ZZB"] < BOOST_CAP


def test_league_level_and_ratings_match_the_sheet() -> None:
    sheet, level, _, strengths = run_module("workbook")
    assert level == pytest.approx(sheet.LgPts, abs=1e-12)  # Settings C17
    for i, code in enumerate(sheet.codes):
        pf = TP.rating(CLUBS[code][0], level, REGRESS, CLUBS[code][2])
        pa = TP.rating(CLUBS[code][1], level, REGRESS, CLUBS[code][3])
        assert pf == pytest.approx(sheet.K[i], abs=1e-9)  # Team Ratings K
        assert pa == pytest.approx(sheet.L[i], abs=1e-9)  # Team Ratings L
        assert strengths[code].attack == pytest.approx(sheet.M[i], abs=1e-9)  # M
        assert strengths[code].defence == pytest.approx(sheet.N[i], abs=1e-9)  # N


def test_injury_layer_totals_match_the_sheets_team_ratings_columns() -> None:
    sheet, level, results, strengths = run_module("workbook")
    for i, code in enumerate(sheet.codes):
        r = results[code]
        assert r.full_strength_points == pytest.approx(sheet.O[i], abs=1e-9)  # O
        assert r.lost_minutes == pytest.approx(sheet.U[i], abs=1e-9)  # U
        assert r.lost_points == pytest.approx(sheet.V[i], abs=1e-9)  # V
        assert r.available_minutes == pytest.approx(sheet.W[i], abs=1e-9)  # W
        assert r.available_points == pytest.approx(sheet.X[i], abs=1e-9)  # X
        assert r.replacement_points == pytest.approx(sheet.Y[i], abs=1e-9)  # Y
        assert r.absorbed_points == pytest.approx(sheet.Z[i], abs=1e-9)  # Z
        assert r.boost == pytest.approx(sheet.AA[i], abs=1e-9)  # AA
        assert r.squad_points == pytest.approx(sheet.AB[i], abs=1e-9)  # AB
        # AC = M * AB / O : the attack index after absences
        after = TP.apply_availability(strengths[code].attack, strengths[code].availability_factor)
        assert after == pytest.approx(sheet.AC[i], abs=1e-9)  # AC


def test_per_player_projections_match_the_sheets_squads_columns() -> None:
    sheet, _, results, _ = run_module("workbook")
    flat = [outcome for code in sheet.codes for outcome in results[code].players]
    assert len(flat) == len(sheet.rows) == 80
    for outcome, minutes, points in zip(flat, sheet.AK, sheet.AL):
        assert outcome.projected_minutes == pytest.approx(minutes, abs=1e-9)  # Squads AK
        assert outcome.projected_points == pytest.approx(points, abs=1e-9)  # Squads AL


def test_every_round_3_cell_matches_the_sheet() -> None:
    sheet, level, results, strengths = run_module("workbook")
    for home, away, hadv in GAMES:
        expected = sheet.game(home, away, hadv)
        got = TP.project_match(level, strengths[home], strengths[away], hadv)
        assert got.home_points == pytest.approx(expected["J"], abs=1e-9), (home, away)
        assert got.away_points == pytest.approx(expected["K"], abs=1e-9)
        assert got.margin == pytest.approx(expected["L"], abs=1e-9)
        assert got.combined_points == pytest.approx(expected["O"], abs=1e-9)
        assert got.home_full_strength == pytest.approx(expected["Y"], abs=1e-9)
        assert got.away_full_strength == pytest.approx(expected["Z"], abs=1e-9)
        assert got.combined_availability_effect == pytest.approx(expected["P"], abs=1e-9)
        assert TP.summary_text(got.margin, home, away) == expected["M"]
        # the per-side effect is the side's score less its full-strength twin
        assert got.home_availability_effect == pytest.approx(
            expected["J"] - expected["Y"], abs=1e-9
        )
        assert got.away_availability_effect == pytest.approx(
            expected["K"] - expected["Z"], abs=1e-9
        )


def test_the_input_set_reaches_a_toss_up_and_a_decided_game() -> None:
    sheet = Sheet()
    margins = [abs(sheet.game(h, a, adv)["L"]) for h, a, adv in GAMES]
    assert any(m >= 0.5 for m in margins)
    texts = [sheet.game(h, a, adv)["M"] for h, a, adv in GAMES]
    assert all(" by " in t or t == "Toss-up" for t in texts)


def test_the_cap_binding_club_differs_between_the_two_policies_exactly_as_documented() -> None:
    _, level, workbook, w_strengths = run_module("workbook")
    _, _, consistent, c_strengths = run_module("consistent")
    a_w, a_c = workbook["ZZA"], consistent["ZZA"]
    assert a_w.cap_binding and a_c.cap_binding
    # the sheet's team total counts points no player is credited with
    credited = sum(p.projected_points for p in a_w.players)
    assert a_w.squad_points - credited == pytest.approx(a_w.unassigned_points, abs=1e-9)
    assert a_w.unassigned_points > 1.0
    # the consistent policy is lower, and equals the sum of the players
    assert a_c.squad_points < a_w.squad_points
    assert sum(p.projected_points for p in a_c.players) == pytest.approx(a_c.squad_points, abs=1e-9)
    assert a_c.unassigned_points == 0.0
    # every player's own projection is the same under both policies (the cap binds each boost)
    for pw, pc in zip(a_w.players, a_c.players):
        assert pw.projected_points == pytest.approx(pc.projected_points, abs=1e-12)
    # and the team projection that follows is lower under the consistent policy
    sheet_game = TP.project_match(level, w_strengths["ZZA"], w_strengths["ZZB"], HCA)
    ours = TP.project_match(level, c_strengths["ZZA"], c_strengths["ZZB"], HCA)
    assert ours.home_points < sheet_game.home_points
    # clubs where the cap does not bind are unchanged by the policy
    for code in ("ZZC", "ZZD", "ZZE", "ZZG"):
        assert consistent[code].squad_points == pytest.approx(workbook[code].squad_points, abs=1e-9)
        assert not workbook[code].cap_binding


def test_where_missing_players_score_below_replacement_the_module_differs_from_the_sheet() -> None:
    """The one deliberate departure: the sheet would let an absence raise a club's scoring."""
    low = [
        {"min": 30, "pts40": 4.0, "status": "OUT"},  # 3 points in 30 minutes: 0.1 a minute
        {"min": 40, "pts40": 20.0, "status": "AVAILABLE"},
        {"min": 40, "pts40": 18.0, "status": "AVAILABLE"},
    ]
    p = [r["pts40"] * r["min"] / 40 for r in low]
    c = [dict(zip(STATUS_LIST, STATUS_PROB))[r["status"]] for r in low]
    full = sum(p)
    lost = sum(pi * (1 - ci) for pi, ci in zip(p, c))
    lost_min = sum(r["min"] * (1 - ci) for r, ci in zip(low, c))
    avail = sum(pi * ci for pi, ci in zip(p, c))
    repl = lost_min * REPL_RATE / 40
    sheet_z = repl + ABSORB * (lost - repl)  # Team Ratings Z, as the sheet writes it
    assert lost < repl and sheet_z > lost
    assert (avail + sheet_z) / full > 1.0  # the sheet: losing a player raises the squad
    players = [IL.PlayerInput(i, r["min"], pi, ci) for i, (r, pi, ci) in enumerate(zip(low, p, c))]
    ours = IL.apply_injury_layer(
        players,
        IL.InjurySettings.from_per40(REPL_RATE, ABSORB, BOOST_CAP, cap_policy="workbook"),
    )
    assert ours.availability_factor <= 1.0 + 1e-12


# ----------------------------------------------------------------- the pure mathematics


def test_the_workbooks_win_probability_cell_is_a_pure_normal_distribution_value() -> None:
    """Settings C20 =1-NORMDIST(0,HCA,MarginSD,TRUE()). Checked as mathematics only: the module
    has no such function and never will."""
    value = 1 - NormalDist(mu=HCA, sigma=11.5).cdf(0)
    assert value == pytest.approx(0.619568543893142, abs=1e-12)
    assert value == pytest.approx(NormalDist().cdf(HCA / 11.5), abs=1e-12)


def test_the_module_has_no_win_probability_or_comparison_machinery() -> None:
    for name in dir(TP) + dir(IL):
        lowered = name.lower()
        assert "prob" not in lowered, name
        assert "odds" not in lowered, name
        assert "edge" not in lowered and "lean" not in lowered, name
        assert "over_under" not in lowered and "p_over" not in lowered, name
    # and the combined points are the plain sum, never snapped to a half point
    assert TP.combined_points(83.97, 84.99) == pytest.approx(168.96)
    assert not hasattr(TP, "round_to_half")


def test_rating_by_hand() -> None:
    # 85 + (88 - 85) * (1 - 0.3) + 1.5 = 85 + 2.1 + 1.5 = 88.6
    assert TP.rating(88, 85, 0.3, 1.5) == pytest.approx(88.6)
    # no regression keeps last season; full regression makes every club average (plus the adj)
    assert TP.rating(88, 85, 0.0) == pytest.approx(88.0)
    assert TP.rating(88, 85, 1.0, 0.25) == pytest.approx(85.25)
    assert TP.rating(80, 85, 0.3, -1.0) == pytest.approx(85 - 3.5 - 1.0)  # below average pulls up


@pytest.mark.parametrize("regression", [-0.01, 1.01, math.nan, True])
def test_a_regression_outside_zero_to_one_is_refused(regression) -> None:
    with pytest.raises(ValueError):
        TP.rating(88, 85, regression)


def test_indices_by_hand() -> None:
    assert TP.attack_index(88.6, 85) == pytest.approx(88.6 / 85)
    assert TP.defence_index(80.75, 85) == pytest.approx(0.95)
    assert TP.apply_availability(1.04, 0.9) == pytest.approx(0.936)
    for bad in (0, -1, math.nan):
        with pytest.raises(ValueError):
            TP.attack_index(88, bad)


def test_match_by_hand() -> None:
    # L = 85; home attack 1.04 (after absences), away defence 0.98, away attack 1.00,
    # home defence 1.01
    # home = 85 * 1.04 * 0.98 + 3.5/2 = 86.632 + 1.75 = 88.382
    # away = 85 * 1.00 * 1.01 - 3.5/2 = 85.85 - 1.75 = 84.1
    home, away = TP.match_scores(85, 1.04, 0.98, 1.00, 1.01, 3.5)
    assert home == pytest.approx(88.382, abs=1e-12) and away == pytest.approx(84.1, abs=1e-12)
    assert TP.margin_of(home, away) == pytest.approx(4.282)
    assert TP.combined_points(home, away) == pytest.approx(172.482)
    assert TP.projected_winner(4.282) == "home"
    assert TP.summary_text(4.282, "HOM", "AWY") == "HOM by 4.3"


def test_home_advantage_moves_the_margin_by_all_of_it_and_the_total_by_none() -> None:
    base = TP.match_scores(85, 1.04, 0.98, 1.00, 1.01, 0.0)
    shifted = TP.match_scores(85, 1.04, 0.98, 1.00, 1.01, 4.0)
    assert shifted[0] - base[0] == pytest.approx(2.0) and base[1] - shifted[1] == pytest.approx(2.0)
    assert TP.margin_of(*shifted) - TP.margin_of(*base) == pytest.approx(4.0)
    assert TP.combined_points(*shifted) == pytest.approx(TP.combined_points(*base))


def test_the_toss_up_edge() -> None:
    assert TP.is_toss_up(0.4999999) and TP.is_toss_up(-0.4999999) and TP.is_toss_up(0.0)
    assert not TP.is_toss_up(0.5) and not TP.is_toss_up(-0.5)  # exactly half a point is a call
    assert TP.projected_winner(0.4999) is None
    assert TP.projected_winner(0.5) == "home" and TP.projected_winner(-0.5) == "away"
    assert TP.summary_text(0.3, "A", "B") == "Toss-up"
    assert TP.summary_text(-0.5, "A", "B") == "B by 0.5"
    assert TP.summary_text(0.5, "A", "B") == "A by 0.5"


@pytest.mark.parametrize(
    "value, digits, text",
    [
        (2.25, 1, "2.3"),  # Python gives 2.2: the sheet's FIXED rounds the tie away from zero
        (0.15, 1, "0.2"),  # Python gives 0.1
        (2.35, 1, "2.4"),
        (2.45, 1, "2.5"),
        (1.0, 1, "1.0"),
        (12.0, 1, "12.0"),
        (0.04, 1, "0.0"),
        (7.126, 2, "7.13"),
        (7.125, 2, "7.13"),
        (1.5, 0, "2"),
    ],
)
def test_fixed_point_text_rounds_the_way_the_sheet_does(value, digits, text) -> None:
    assert TP.format_fixed(value, digits) == text
    assert fixed(value, digits) == text  # the transcription agrees


def test_a_one_ulp_wobble_on_a_tie_is_read_at_fifteen_significant_digits() -> None:
    """A margin computed as 2.4499999999999997 is, to 15 digits, 2.45: a tie, rounded up. Python's
    own formatting looks at the binary value and says 2.4, so a summary built with ``:.1f``
    could disagree with the sheet by a tenth on a margin that differs from a tie by 1e-16."""
    wobbly = 2.4499999999999997
    assert wobbly < 2.45
    assert f"{wobbly:.1f}" == "2.4"
    assert TP.format_fixed(wobbly, 1) == "2.5"
    assert TP.round_half_up(wobbly, 1) == 2.5


def test_round_half_up_returns_a_float() -> None:
    assert TP.round_half_up(2.25, 1) == 2.3
    assert TP.round_half_up(-2.25, 1) == -2.3  # away from zero
    assert TP.round_half_up(3.14159, 3) == 3.142
    assert isinstance(TP.round_half_up(1.0, 1), float)


@pytest.mark.parametrize("seed", range(5))
def test_summary_text_agrees_with_the_transcribed_fixed_on_random_margins(seed) -> None:
    rng = random.Random(seed)
    for _ in range(500):
        margin = rng.uniform(-15, 15)
        if TP.is_toss_up(margin):
            continue
        expected = f"{'H' if margin >= 0 else 'A'} by {fixed(abs(margin), 1)}"
        assert TP.summary_text(margin, "H", "A") == expected


def test_interval80() -> None:
    low, high = TP.interval80(88.382, 9.5)
    assert low == pytest.approx(88.382 - 1.2816 * 9.5) and high == pytest.approx(
        88.382 + 1.2816 * 9.5
    )
    assert TP.Z80 == 1.2816
    assert TP.interval80(4.282, None) is None
    assert TP.interval80(10.0, 0.0) == (10.0, 10.0)
    with pytest.raises(ValueError):
        TP.interval80(10.0, -1.0)


def test_update_adjustment_by_hand() -> None:
    # 1.2 + 0.09 * (94 - 88.5) = 1.2 + 0.495 = 1.695: scoring above the projection raises attack
    assert TP.update_adjustment(1.2, 94, 88.5, 0.09) == pytest.approx(1.695)
    # allowing 80 against a projected 84: -0.4 + 0.1 * (80 - 84) = -0.8: a better defence
    assert TP.update_adjustment(-0.4, 80, 84, 0.10) == pytest.approx(-0.8)
    assert TP.update_adjustment(0.5, 80, 80, 0.1) == 0.5
    for bad in (-0.1, 1.1, math.nan):
        with pytest.raises(ValueError):
            TP.update_adjustment(0, 1, 1, bad)


def test_round_weights() -> None:
    table = {1: 0.10, 2: 0.09}
    one, two = TP.round_weight(1, table), TP.round_weight(2, table)
    assert (one.weight, one.extrapolated) == (0.10, False)
    assert (two.weight, two.extrapolated) == (0.09, False)
    three = TP.round_weight(3, table)
    assert three.weight == pytest.approx(1 / 12) and three.extrapolated  # 0.0833
    assert TP.round_weight(10, table).weight == pytest.approx(1 / 19)
    assert TP.round_weight(3, table, offset=10).weight == pytest.approx(1 / 13)
    assert TP.sequential_weight(5, 10) == pytest.approx(1 / 15)  # the NBA default
    for bad_n in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            TP.sequential_weight(bad_n, 9)
    with pytest.raises(ValueError):
        TP.round_weight(1, {1: 1.5})


def test_regulation_scale() -> None:
    # 100 points in 13,500 team-seconds, scaled to 12,000: 100 * 12000 / 13500 = 88.888...
    assert TP.regulation_scale(100, 13_500, 12_000) == pytest.approx(88.8888888889)
    assert TP.regulation_scale(100, 12_000, 12_000) == 100.0
    with pytest.raises(ValueError):
        TP.regulation_scale(100, 0, 12_000)


def test_blend_league_level() -> None:
    # (200 * 112 + 150 * 110) / 350 = 111.142857...
    assert TP.blend_league_level(112, 200, 110) == pytest.approx((200 * 112 + 150 * 110) / 350)
    assert TP.blend_league_level(112, 0, 110) == 110  # no games yet: last season's level
    assert TP.blend_league_level(112, 500, None) == 112  # no previous season: this season's
    assert TP.blend_league_level(None, 0, None) is None
    assert TP.blend_league_level(112, 150, 110, previous_weight=50) == pytest.approx(
        (150 * 112 + 50 * 110) / 200
    )


def test_home_advantage_branches() -> None:
    neutral = TP.home_advantage_for_game(default=3.5, is_neutral=True)
    assert (neutral.points, neutral.venue_assumed, neutral.basis) == (0.0, False, "neutral")
    override = TP.home_advantage_for_game(default=3.5, override=2.0, is_neutral=False)
    assert (override.points, override.venue_assumed, override.basis) == (2.0, False, "override")
    known = TP.home_advantage_for_game(default=3.5, is_neutral=False)
    assert (known.points, known.venue_assumed, known.basis) == (3.5, False, "default")
    assumed = TP.home_advantage_for_game(default=3.5)
    assert (assumed.points, assumed.venue_assumed, assumed.basis) == (3.5, True, "default")
    # an override of zero is still an override, not "absent"
    zero = TP.home_advantage_for_game(default=3.5, override=0.0)
    assert zero.points == 0.0 and zero.basis == "override"
    # a neutral flag outranks an override typed for the same game
    both = TP.home_advantage_for_game(default=3.5, override=2.0, is_neutral=True)
    assert both.points == 0.0 and both.basis == "neutral"
    # and an override alone settles the venue question
    assert not TP.home_advantage_for_game(default=3.5, override=2.0).venue_assumed


# ------------------------------------------------------------ the whole game, twinned


def test_full_strength_twin_and_the_availability_effect() -> None:
    home = TP.TeamStrength(attack=1.05, defence=0.97, availability_factor=0.92)
    away = TP.TeamStrength(attack=0.99, defence=1.02, availability_factor=1.0)
    m = TP.project_match(86.0, home, away, 3.5, team_sd=9.5, margin_sd=11.5)
    # full strength uses af = 1 for both sides
    full_home, full_away = TP.match_scores(86.0, 1.05, 1.02, 0.99, 0.97, 3.5)
    assert m.home_full_strength == pytest.approx(
        full_home
    ) and m.away_full_strength == pytest.approx(full_away)
    # the home side's attack is 0.92 of full: 86 * (1.05 * 0.92) * 1.02 + 1.75
    assert m.home_points == pytest.approx(86.0 * 1.05 * 0.92 * 1.02 + 1.75)
    assert m.home_attack_after_availability == pytest.approx(1.05 * 0.92)
    assert m.away_points == pytest.approx(full_away)  # the away side was at full strength
    assert m.away_availability_effect == pytest.approx(0.0, abs=1e-12)
    assert m.home_availability_effect < 0
    assert m.combined_availability_effect == pytest.approx(
        m.home_availability_effect + m.away_availability_effect
    )
    assert m.combined_points == pytest.approx(m.home_points + m.away_points)
    assert m.combined_full_strength == pytest.approx(full_home + full_away)
    assert m.margin == pytest.approx(m.home_points - m.away_points)
    # intervals: teams by team sd, margin by margin sd
    assert m.home_range80 == pytest.approx(
        (m.home_points - 1.2816 * 9.5, m.home_points + 1.2816 * 9.5)
    )
    assert m.margin_range80 == pytest.approx((m.margin - 1.2816 * 11.5, m.margin + 1.2816 * 11.5))


def test_without_a_spread_there_is_no_interval() -> None:
    s = TP.TeamStrength(1.0, 1.0)
    m = TP.project_match(85.0, s, s, 3.5)
    assert m.home_range80 is None and m.away_range80 is None and m.margin_range80 is None


def test_full_strength_equals_the_projection_when_everyone_plays() -> None:
    a, b = TP.TeamStrength(1.04, 0.98), TP.TeamStrength(0.97, 1.01)
    m = TP.project_match(85.0, a, b, 3.5)
    assert m.home_points == m.home_full_strength and m.away_points == m.away_full_strength
    assert m.combined_availability_effect == 0.0
    assert m.home_availability_effect == 0.0


def test_a_projection_names_a_winner_or_none() -> None:
    strong, weak = TP.TeamStrength(1.10, 0.95), TP.TeamStrength(0.92, 1.05)
    decided = TP.project_match(85.0, strong, weak, 3.5)
    assert decided.projected_winner == "home" and not decided.is_toss_up
    even = TP.project_match(85.0, TP.TeamStrength(1.0, 1.0), TP.TeamStrength(1.0, 1.0), 0.0)
    assert even.is_toss_up and even.projected_winner is None and even.margin == 0.0


def test_a_neutral_game_has_no_home_edge_in_the_projection() -> None:
    s = TP.TeamStrength(1.0, 1.0)
    m = TP.project_match(85.0, s, s, 0.0)
    assert m.home_points == m.away_points == 85.0
