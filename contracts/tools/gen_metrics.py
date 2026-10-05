"""Generate contracts/metrics.json - the shared metric catalog.

Two lists live in the document, and the difference between them is deliberate:

``metrics`` is the NBA catalog. Every key in it must resolve against NBA rows
(``test_every_catalog_metric_resolves_against_a_full_row``) and carries NBA-era availability
(``seasonFrom`` 1946-47 and so on). ``opp_pts`` ("Points Allowed", team scope) joined it when the
team-matchup features needed points allowed to be sortable in a leaderboard and showable in a
``stat_tile``; the column has always existed on ``team_game`` and ``team_season`` and has always
been populated, it only lacked a key.

``leagueMetrics.euroleague`` is the EuroLeague's stat vocabulary, kept apart on purpose. PIR (the
EuroLeague's Performance Index Rating) is the reason: put in ``metrics`` it would be a permanently
null NBA metric that claimed NBA-era availability, and it would fail the "resolves against an NBA
row" tests for the right reason. A new top-level key costs the clients nothing: the iOS decoder
(``MetricCatalogDocument``) reads only the keys it names, and the web embeds the whole document
verbatim, so ``leagueMetrics`` reaches ``contracts.ts`` with no generator change. Entries have the
shape of a ``metrics`` entry plus ``leagues`` and ``perModes``.

Keys shared with the NBA (``pts``, ``reb``, ``fg3m`` ...) are stated again here rather than
pointed at, so a client formats a EuroLeague stat from one place; ``_check_shared_keys`` proves the
restatement still agrees with the NBA entry on name, short name, format, direction and category,
so the two leagues cannot drift into formatting the same stat two ways. The store's column names
differ from these keys in three places (``fgm3`` is ``fg3m``, ``fga3`` is ``fg3a``, ``pir_official``
is ``pir``); the keys here are the wire names.
"""
import json, collections

# key, short, name, category, fmt, higherBetter, availableFrom, scope, formula/def
M = [
    # --- Volume / basic (box score) ---
    ("min",      "MIN",  "Minutes",                "volume",   "minutes",  True,  "1951-52", "pt", "Minutes played."),
    ("pts",      "PTS",  "Points",                 "volume",   "decimal1", True,  "1946-47", "pt", "Points scored."),
    ("reb",      "REB",  "Rebounds",               "volume",   "decimal1", True,  "1950-51", "pt", "Total rebounds. Offensive/defensive split only from 1973-74."),
    ("oreb",     "OREB", "Offensive Rebounds",     "volume",   "decimal1", True,  "1973-74", "pt", "Offensive rebounds."),
    ("dreb",     "DREB", "Defensive Rebounds",     "volume",   "decimal1", True,  "1973-74", "pt", "Defensive rebounds."),
    ("ast",      "AST",  "Assists",                "volume",   "decimal1", True,  "1946-47", "pt", "Assists."),
    ("stl",      "STL",  "Steals",                 "volume",   "decimal1", True,  "1973-74", "pt", "Steals. Officially tracked from 1973-74."),
    ("blk",      "BLK",  "Blocks",                 "volume",   "decimal1", True,  "1973-74", "pt", "Blocked shots. Officially tracked from 1973-74."),
    ("tov",      "TOV",  "Turnovers",              "volume",   "decimal1", False, "1977-78", "pt", "Individual turnovers. Officially tracked from 1977-78."),
    ("pf",       "PF",   "Personal Fouls",         "volume",   "decimal1", False, "1946-47", "pt", "Personal fouls."),
    ("fgm",      "FGM",  "Field Goals Made",       "volume",   "decimal1", True,  "1946-47", "pt", "Field goals made."),
    ("fga",      "FGA",  "Field Goals Attempted",  "volume",   "decimal1", True,  "1946-47", "pt", "Field goals attempted."),
    ("fg3m",     "3PM",  "Three Pointers Made",    "volume",   "decimal1", True,  "1979-80", "pt", "Three-point field goals made. The three-point line arrived in 1979-80."),
    ("fg3a",     "3PA",  "Three Pointers Attempted","volume",  "decimal1", True,  "1979-80", "pt", "Three-point field goals attempted."),
    ("ftm",      "FTM",  "Free Throws Made",       "volume",   "decimal1", True,  "1946-47", "pt", "Free throws made."),
    ("fta",      "FTA",  "Free Throws Attempted",  "volume",   "decimal1", True,  "1946-47", "pt", "Free throws attempted."),
    ("plus_minus","+/-", "Plus/Minus",             "impact",   "plusMinus1", True, "1996-97", "pt", "Team point differential while the player is on the floor."),
    ("gp",       "GP",   "Games Played",           "volume",   "integer",  True,  "1946-47", "pt", "Games played."),
    ("gs",       "GS",   "Games Started",          "volume",   "integer",  True,  "1970-71", "pt", "Games started."),
    ("fantasy_pts","FP", "Fantasy Points",         "volume",   "decimal1", True,  "1977-78", "p",  "NBA fantasy points: PTS + 1.2*REB + 1.5*AST + 3*STL + 3*BLK - TOV."),

    # --- Shooting efficiency ---
    ("fg_pct",   "FG%",  "Field Goal %",           "shooting", "percent1", True,  "1946-47", "pt", "FGM / FGA."),
    ("fg3_pct",  "3P%",  "Three Point %",          "shooting", "percent1", True,  "1979-80", "pt", "3PM / 3PA."),
    ("ft_pct",   "FT%",  "Free Throw %",           "shooting", "percent1", True,  "1946-47", "pt", "FTM / FTA."),
    ("efg_pct",  "eFG%", "Effective FG %",         "shooting", "percent1", True,  "1946-47", "pt", "(FGM + 0.5*3PM) / FGA. Adjusts field goal percentage for the extra value of a three."),
    ("ts_pct",   "TS%",  "True Shooting %",        "shooting", "percent1", True,  "1946-47", "pt", "PTS / (2 * (FGA + 0.44*FTA)). Shooting efficiency including free throws."),
    ("fg3a_rate","3PAr", "Three Point Attempt Rate","shooting","percent1", True,  "1979-80", "pt", "3PA / FGA. Share of field goal attempts taken from three."),
    ("ftr",      "FTr",  "Free Throw Rate",        "shooting", "percent1", True,  "1946-47", "pt", "FTA / FGA. Free throws drawn per field goal attempt."),
    ("pps",      "PPS",  "Points Per Shot",        "shooting", "decimal2", True,  "1946-47", "pt", "PTS / FGA."),

    # --- Rate / advanced (possession based; per-game from 1996-97) ---
    ("off_rtg",  "ORtg", "Offensive Rating",       "efficiency","rating1", True,  "1996-97", "pt", "Points produced per 100 possessions (Dean Oliver)."),
    ("def_rtg",  "DRtg", "Defensive Rating",       "efficiency","rating1", False, "1996-97", "pt", "Points allowed per 100 possessions (Dean Oliver)."),
    ("net_rtg",  "NetRtg","Net Rating",            "efficiency","rating1", True,  "1996-97", "pt", "Offensive Rating minus Defensive Rating."),
    ("usg_pct",  "USG%", "Usage %",                "usage",    "percent1", True,  "1977-78", "pt", "Share of team possessions a player ends while on the floor."),
    ("ast_pct",  "AST%", "Assist %",               "playmaking","percent1",True,  "1946-47", "pt", "Share of teammate field goals a player assists while on the floor."),
    ("ast_tov",  "AST/TO","Assist to Turnover",    "playmaking","decimal2",True,  "1977-78", "pt", "Assists divided by turnovers."),
    ("ast_ratio","ASTr", "Assist Ratio",           "playmaking","decimal1",True,  "1977-78", "pt", "Assists per 100 possessions used."),
    ("oreb_pct", "OREB%","Offensive Rebound %",    "rebounding","percent1",True,  "1973-74", "pt", "Share of available offensive rebounds grabbed while on the floor."),
    ("dreb_pct", "DREB%","Defensive Rebound %",    "rebounding","percent1",True,  "1973-74", "pt", "Share of available defensive rebounds grabbed while on the floor."),
    ("reb_pct",  "REB%", "Rebound %",              "rebounding","percent1",True,  "1973-74", "pt", "Share of available rebounds grabbed while on the floor."),
    ("tov_pct",  "TOV%", "Turnover %",             "usage",    "percent1", False, "1977-78", "pt", "Turnovers per 100 possessions used."),
    # 1977-78, not 1973-74 with the raw steal: STL% is steals over opponent *possessions*, and a
    # possession estimate needs turnovers, which the league did not record until 1977-78. BLK%
    # below genuinely does start with the raw block, because its denominator is opponent two-point
    # attempts (FGA - 3PA) and the 3PA is a true zero before the line existed.
    ("stl_pct",  "STL%", "Steal %",                "defense",  "percent1", True,  "1977-78", "pt", "Share of opponent possessions ending in a steal by the player."),
    ("blk_pct",  "BLK%", "Block %",                "defense",  "percent1", True,  "1973-74", "pt", "Share of opponent two-point attempts blocked by the player."),
    ("pace",     "PACE", "Pace",                   "efficiency","decimal1",True,  "1996-97", "pt", "Possessions per 48 minutes."),
    ("poss",     "POSS", "Possessions",            "efficiency","decimal1",True,  "1996-97", "pt", "Possessions played."),
    ("pie",      "PIE",  "Player Impact Estimate", "impact",   "percent1", True,  "1996-97", "p",  "A player's share of the box-score events generated in the games they played (NBA.com)."),

    # --- Composite / season-level (estimated pre-1997) ---
    ("game_score","GmSc","Game Score",             "impact",   "decimal1", True,  "1973-74", "p",  "Hollinger's single-game rating: PTS + 0.4*FGM - 0.7*FGA - 0.4*(FTA-FTM) + 0.7*OREB + 0.3*DREB + STL + 0.7*AST + 0.7*BLK - 0.4*PF - TOV."),
    ("per",      "PER",  "Player Efficiency Rating","impact",  "decimal1", True,  "1951-52", "p",  "Hollinger's per-minute rating, pace and league adjusted so that the league average is 15.00 every season."),
    ("ws",       "WS",   "Win Shares",             "impact",   "decimal1", True,  "1951-52", "p",  "Estimated wins contributed. OWS + DWS."),
    ("ows",      "OWS",  "Offensive Win Shares",   "impact",   "decimal1", True,  "1951-52", "p",  "Wins contributed by offense."),
    ("dws",      "DWS",  "Defensive Win Shares",   "impact",   "decimal1", True,  "1951-52", "p",  "Wins contributed by defense."),
    ("ws48",     "WS/48","Win Shares per 48",      "impact",   "decimal2", True,  "1951-52", "p",  "Win Shares per 48 minutes. League average is about 0.100."),
    ("bpm",      "BPM",  "Box Plus/Minus",         "impact",   "plusMinus1",True, "1973-74", "p",  "Box-score estimate of points per 100 possessions above a league-average player. Needs steals, blocks and turnovers, so it starts in 1973-74."),
    ("obpm",     "OBPM", "Offensive Box Plus/Minus","impact",  "plusMinus1",True, "1973-74", "p",  "Offensive component of BPM."),
    ("dbpm",     "DBPM", "Defensive Box Plus/Minus","impact",  "plusMinus1",True, "1973-74", "p",  "Defensive component of BPM."),
    ("vorp",     "VORP", "Value Over Replacement", "impact",   "decimal1", True,  "1973-74", "p",  "(BPM + 2.0) * (share of team minutes) * (team games / 82)."),

    # --- Four factors (team-first, also player) ---
    ("opp_efg_pct","Opp eFG%","Opponent eFG %",    "defense",  "percent1", False, "1996-97", "t",  "Opponent effective field goal percentage."),
    ("opp_tov_pct","Opp TOV%","Opponent Turnover %","defense", "percent1", True,  "1996-97", "t",  "Opponent turnovers per 100 possessions."),
    ("opp_oreb_pct","Opp OREB%","Opponent OREB %", "defense",  "percent1", False, "1996-97", "t",  "Opponent offensive rebound percentage."),
    ("opp_ftr",  "Opp FTr","Opponent Free Throw Rate","defense","percent1",False, "1996-97", "t",  "Opponent FTA / FGA."),
    ("opp_pts",  "OPP PTS","Points Allowed",     "scoring",  "decimal1", False, "1946-47", "t",  "Points the team's opponents scored per game."),
    ("wins",     "W",    "Wins",                   "volume",   "integer",  True,  "1946-47", "t",  "Team wins."),
    ("losses",   "L",    "Losses",                 "volume",   "integer",  False, "1946-47", "t",  "Team losses."),
    ("win_pct",  "W%",   "Win Percentage",         "efficiency","percent1",True,  "1946-47", "t",  "Wins / (Wins + Losses)."),
]

DOMAINS = {
    "ts_pct": (0.35, 0.75), "efg_pct": (0.35, 0.72), "fg_pct": (0.30, 0.70),
    "fg3_pct": (0.15, 0.50), "ft_pct": (0.40, 1.0), "usg_pct": (0.05, 0.42),
    "ast_pct": (0.0, 0.55), "oreb_pct": (0.0, 0.20), "dreb_pct": (0.0, 0.40),
    "reb_pct": (0.0, 0.30), "tov_pct": (0.0, 0.30), "stl_pct": (0.0, 0.05),
    "blk_pct": (0.0, 0.10), "off_rtg": (85.0, 135.0), "def_rtg": (85.0, 135.0),
    "net_rtg": (-25.0, 25.0), "pace": (85.0, 110.0), "pie": (0.0, 0.30),
    "per": (0.0, 35.0), "ws48": (-0.05, 0.32), "bpm": (-8.0, 14.0),
    "fg3a_rate": (0.0, 0.85), "ftr": (0.0, 0.80), "win_pct": (0.0, 1.0),
}
# Metrics whose per-game values are only available from the advanced box score era.
PER_GAME_FROM_1997 = {
    "off_rtg","def_rtg","net_rtg","pace","poss","pie","usg_pct","ast_pct","ast_ratio",
    "oreb_pct","dreb_pct","reb_pct","tov_pct","efg_pct","ts_pct","ast_tov","plus_minus",
}
SEASON_ONLY = {"per","ws","ows","dws","ws48","bpm","obpm","dbpm","vorp","win_pct","wins","losses"}
ESTIMATED_BEFORE_1997 = {"per","ws","ows","dws","ws48","bpm","obpm","dbpm","vorp","usg_pct","ast_pct",
                         "oreb_pct","dreb_pct","reb_pct","tov_pct","stl_pct","blk_pct","off_rtg","def_rtg"}

metrics = []
for key, short, name, cat, fmt, hib, avail, scope, gloss in M:
    d = {
        "key": key,
        "name": name,
        "shortName": short,
        "category": cat,
        "format": fmt,
        "higherIsBetter": hib,
        "scope": [s for s in (("player" if "p" in scope else None), ("team" if "t" in scope else None)) if s],
        "availability": {
            "seasonFrom": avail,
            "perGameFrom": "1996-97" if key in PER_GAME_FROM_1997 else avail,
            "seasonLevelOnly": key in SEASON_ONLY,
            "estimatedBefore": "1996-97" if key in ESTIMATED_BEFORE_1997 else None,
        },
        "domain": ({"min": DOMAINS[key][0], "max": DOMAINS[key][1]} if key in DOMAINS else None),
        "glossary": gloss,
    }
    metrics.append(d)

keys = [m["key"] for m in metrics]
assert len(keys) == len(set(keys)), "duplicate metric key"

cats = collections.OrderedDict([
    ("volume", "Volume"), ("shooting", "Shooting"), ("efficiency", "Efficiency"),
    ("usage", "Usage"), ("playmaking", "Playmaking"), ("rebounding", "Rebounding"),
    ("defense", "Defense"), ("impact", "Impact"), ("scoring", "Scoring"),
])
for m in metrics:
    assert m["category"] in cats, m["category"]

# --------------------------------------------------------------------------- EuroLeague

# key, short, name, category, fmt, higherBetter, glossary. Scope is player and team for all of
# them (a team row carries the same columns as a player line) and availability is the same for
# all of them, so neither is repeated per row.
#
# Availability says "2026-27": the first EuroLeague season Hardwood's store is built to hold (the
# user's workbook). It is a statement about Hardwood's data, not about when the EuroLeague began
# publishing a stat, which nothing here could verify; a deliberate backfill of earlier seasons
# would move this one constant. Three stats are not in every game: the workbook's box scores carry
# no fouls drawn, blocks against or plus/minus, so those games hold a null (a dash), never a 0, and
# the averages divide by the games that do carry the stat. Each glossary says so.
EL_FIRST_SEASON = "2026-27"
EL_PER_MODES = ["PerGame", "Totals", "Per40"]
EL_NOT_IN_WORKBOOK = " Not recorded for games imported from a workbook; shown as a dash there, never 0."
EL_M = [
    ("min",         "MIN",   "Minutes",                 "volume",   "minutes",   True,  "Minutes played, from whole seconds on the box score."),
    ("pts",         "PTS",   "Points",                  "volume",   "decimal1",  True,  "Points scored."),
    ("reb",         "REB",   "Rebounds",                "volume",   "decimal1",  True,  "Total rebounds: offensive plus defensive."),
    ("oreb",        "OREB",  "Offensive Rebounds",      "volume",   "decimal1",  True,  "Offensive rebounds."),
    ("dreb",        "DREB",  "Defensive Rebounds",      "volume",   "decimal1",  True,  "Defensive rebounds."),
    ("ast",         "AST",   "Assists",                 "volume",   "decimal1",  True,  "Assists."),
    ("stl",         "STL",   "Steals",                  "volume",   "decimal1",  True,  "Steals."),
    ("blk",         "BLK",   "Blocks",                  "volume",   "decimal1",  True,  "Shots blocked."),
    ("tov",         "TOV",   "Turnovers",               "volume",   "decimal1",  False, "Turnovers."),
    ("pf",          "PF",    "Personal Fouls",          "volume",   "decimal1",  False, "Personal fouls committed."),
    ("fgm2",        "2PM",   "Two Pointers Made",       "volume",   "decimal1",  True,  "Two-point field goals made."),
    ("fga2",        "2PA",   "Two Pointers Attempted",  "volume",   "decimal1",  True,  "Two-point field goals attempted."),
    ("fg2_pct",     "2P%",   "Two Point %",             "shooting", "percent1",  True,  "2PM / 2PA. No attempts is a dash, not 0%."),
    ("fg3m",        "3PM",   "Three Pointers Made",     "volume",   "decimal1",  True,  "Three-point field goals made."),
    ("fg3a",        "3PA",   "Three Pointers Attempted","volume",   "decimal1",  True,  "Three-point field goals attempted."),
    ("fg3_pct",     "3P%",   "Three Point %",           "shooting", "percent1",  True,  "3PM / 3PA. No attempts is a dash, not 0%."),
    ("ftm",         "FTM",   "Free Throws Made",        "volume",   "decimal1",  True,  "Free throws made."),
    ("fta",         "FTA",   "Free Throws Attempted",   "volume",   "decimal1",  True,  "Free throws attempted."),
    ("ft_pct",      "FT%",   "Free Throw %",            "shooting", "percent1",  True,  "FTM / FTA. No attempts is a dash, not 0%."),
    ("blk_against", "BLKA",  "Blocks against",          "volume",   "decimal1",  False, "Shots of the player's own that were blocked." + EL_NOT_IN_WORKBOOK),
    ("fouls_drawn", "FD",    "Fouls drawn",             "volume",   "decimal1",  True,  "Fouls committed against the player." + EL_NOT_IN_WORKBOOK),
    ("plus_minus",  "+/-",   "Plus/Minus",              "impact",   "plusMinus1",True,  "Team point differential while the player is on the floor." + EL_NOT_IN_WORKBOOK),
    ("pir",         "PIR",   "Performance Index Rating","impact",   "decimal1",  True,  "Performance Index Rating as published by the EuroLeague; not recomputed by Hardwood."),
]
# Stats a per-40 rate is not a statistic for: minutes per forty minutes is always forty.
EL_NO_PER40 = {"min"}
EL_DOMAINS = {"fg2_pct": (0.30, 0.70), "fg3_pct": DOMAINS["fg3_pct"], "ft_pct": DOMAINS["ft_pct"]}

league_metrics_euroleague = []
for key, short, name, cat, fmt, hib, gloss in EL_M:
    league_metrics_euroleague.append({
        "key": key,
        "name": name,
        "shortName": short,
        "category": cat,
        "format": fmt,
        "higherIsBetter": hib,
        "scope": ["player", "team"],
        "availability": {
            "seasonFrom": EL_FIRST_SEASON,
            "perGameFrom": EL_FIRST_SEASON,
            "seasonLevelOnly": False,
            "estimatedBefore": None,
        },
        "domain": ({"min": EL_DOMAINS[key][0], "max": EL_DOMAINS[key][1]} if key in EL_DOMAINS else None),
        "glossary": gloss,
        "leagues": ["euroleague"],
        "perModes": [m for m in EL_PER_MODES if not (m == "Per40" and key in EL_NO_PER40)],
    })


def _check_shared_keys():
    """A key in both lists must be formatted the same way in both."""
    nba = {m["key"]: m for m in metrics}
    for entry in league_metrics_euroleague:
        twin = nba.get(entry["key"])
        if twin is None:
            continue
        for field in ("name", "shortName", "format", "higherIsBetter", "category"):
            assert entry[field] == twin[field], (
                f"leagueMetrics.euroleague.{entry['key']}.{field} = {entry[field]!r} but "
                f"metrics.{entry['key']}.{field} = {twin[field]!r}"
            )


_check_shared_keys()
el_keys = [m["key"] for m in league_metrics_euroleague]
assert len(el_keys) == len(set(el_keys)), "duplicate EuroLeague metric key"
for m in league_metrics_euroleague:
    assert m["category"] in cats, m["category"]
    assert m["format"] in {"integer", "decimal1", "decimal2", "percent1", "percent2", "rating1",
                           "plusMinus1", "minutes"}, m["format"]

doc = {
    "schemaVersion": 1,
    "categories": [{"key": k, "name": v} for k, v in cats.items()],
    "formats": [
        {"key": "integer",    "decimals": 0, "suffix": None, "multiplier": 1},
        {"key": "decimal1",   "decimals": 1, "suffix": None, "multiplier": 1},
        {"key": "decimal2",   "decimals": 2, "suffix": None, "multiplier": 1},
        {"key": "percent1",   "decimals": 1, "suffix": "%",  "multiplier": 100},
        {"key": "percent2",   "decimals": 2, "suffix": "%",  "multiplier": 100},
        {"key": "rating1",    "decimals": 1, "suffix": None, "multiplier": 1},
        {"key": "plusMinus1", "decimals": 1, "suffix": None, "multiplier": 1, "signed": True},
        {"key": "minutes",    "decimals": 1, "suffix": None, "multiplier": 1},
    ],
    "eraBoundaries": [
        {"season": "1946-47", "label": "BAA begins", "detail": "Basic box score: FG, FT, PTS, PF, AST."},
        {"season": "1950-51", "label": "Rebounds tracked", "detail": "Total rebounds only; no offensive/defensive split."},
        {"season": "1951-52", "label": "Minutes played tracked", "detail": "Enables per-minute metrics such as PER and WS/48."},
        {"season": "1973-74", "label": "Steals, blocks, OREB/DREB split", "detail": "First season BPM and VORP can be computed."},
        {"season": "1977-78", "label": "Individual turnovers tracked", "detail": "Enables USG% and TOV%."},
        {"season": "1979-80", "label": "Three point line introduced", "detail": "3PM/3PA and 3PAr begin."},
        {"season": "1996-97", "label": "Advanced box scores and play-by-play", "detail": "First season with true per-game ratings, plus/minus and shot charts."},
        {"season": "2013-14", "label": "Player tracking league-wide", "detail": "SportVU, then Sony Hawk-Eye from 2023-24."},
        {"season": "2016-17", "label": "Hustle stats", "detail": "Box outs added around 2019-20."},
    ],
    "metrics": metrics,
    "leagueMetrics": {"euroleague": league_metrics_euroleague},
}
print(json.dumps(doc, indent=2))
