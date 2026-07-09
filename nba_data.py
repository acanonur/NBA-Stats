"""Data layer for the NBA advanced-stats dashboard.

Wraps nba_api endpoints, adds derived advanced metrics that work for every
era (including pre-1996 seasons where stats.nba.com has no Advanced splits),
and ships a deterministic demo-data generator so the UI can be developed and
demoed without network access to stats.nba.com.

All fetch functions return pandas DataFrames.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Season helpers
# ---------------------------------------------------------------------------

FIRST_BAA_SEASON = 1946          # earliest season on stats.nba.com
FIRST_ADVANCED_SEASON = 1996     # league-wide Advanced splits start 1996-97
LATEST_SEASON_START = 2025       # most recent completed season: 2025-26

SEASON_TYPES = ["Regular Season", "Playoffs"]
PER_MODES = ["PerGame", "Totals", "Per36"]
MEASURE_TYPES = ["Advanced", "Base", "Usage", "Scoring", "Misc", "Defense"]


def season_str(start_year: int) -> str:
    """1997 -> '1997-98'."""
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def all_seasons(first: int = FIRST_ADVANCED_SEASON) -> list[str]:
    """Newest-first list of season strings from `first` through the latest."""
    return [season_str(y) for y in range(LATEST_SEASON_START, first - 1, -1)]


# ---------------------------------------------------------------------------
# Glossary of advanced stats (shown in the dashboard)
# ---------------------------------------------------------------------------

GLOSSARY: dict[str, str] = {
    "TS_PCT": "True Shooting % — points per shooting possession: PTS / (2 × (FGA + 0.44 × FTA)). "
              "Folds free throws and threes into one scoring-efficiency number.",
    "EFG_PCT": "Effective FG% — field-goal % adjusted so a made three counts 1.5×: (FGM + 0.5 × 3PM) / FGA.",
    "USG_PCT": "Usage % — share of team possessions a player finishes (shot, FTs, or turnover) while on the floor.",
    "OFF_RATING": "Offensive Rating — team points scored per 100 possessions while the player is on the floor.",
    "DEF_RATING": "Defensive Rating — team points allowed per 100 possessions while the player is on the floor (lower is better).",
    "NET_RATING": "Net Rating — Offensive Rating minus Defensive Rating.",
    "PIE": "Player Impact Estimate — the share of positive game events (points, boards, dimes, stops…) a player produces. "
           "NBA.com's catch-all impact metric; league average ≈ 10%.",
    "PACE": "Pace — possessions per 48 minutes while the player is on the floor.",
    "AST_PCT": "Assist % — share of teammate field goals the player assisted while on the floor.",
    "AST_TO": "Assist-to-Turnover ratio.",
    "AST_RATIO": "Assist Ratio — assists per 100 possessions used.",
    "OREB_PCT": "Offensive Rebound % — share of available offensive rebounds grabbed while on the floor.",
    "DREB_PCT": "Defensive Rebound % — share of available defensive rebounds grabbed while on the floor.",
    "REB_PCT": "Rebound % — share of all available rebounds grabbed while on the floor.",
    "TM_TOV_PCT": "Turnover % — turnovers per 100 possessions used.",
    "FTr": "Free-Throw Rate — FTA / FGA. How often a player gets to the line per shot attempt.",
    "3PAr": "Three-Point Attempt Rate — 3PA / FGA. Share of shots taken from deep.",
    "PTS_PER36": "Points per 36 minutes — scoring rate independent of playing time.",
    "POSS": "Possessions played.",
    "PLUS_MINUS": "Raw plus/minus — team score margin while the player was on the floor.",
}


# ---------------------------------------------------------------------------
# Derived advanced metrics (era-agnostic — computed from base counting stats)
# ---------------------------------------------------------------------------

def add_derived_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Add TS%, eFG%, FTr, 3PAr, AST/TOV and per-36 rates from base columns.

    Works on any frame with the standard nba_api base columns (PTS, FGA, FTA,
    FGM, FG3M, FG3A, AST, TOV, MIN...). Missing inputs are skipped silently so
    this is safe for early-era seasons where e.g. 3P columns are absent.
    """
    out = df.copy()

    def col(name):
        return pd.to_numeric(out[name], errors="coerce") if name in out else None

    pts, fga, fta = col("PTS"), col("FGA"), col("FTA")
    fgm, fg3m, fg3a = col("FGM"), col("FG3M"), col("FG3A")
    ast, tov, minutes = col("AST"), col("TOV"), col("MIN")

    if pts is not None and fga is not None and fta is not None:
        tsa = fga + 0.44 * fta
        out["TS_PCT"] = (pts / (2 * tsa.replace(0, np.nan))).round(3)
    if fgm is not None and fg3m is not None and fga is not None:
        out["EFG_PCT"] = ((fgm + 0.5 * fg3m) / fga.replace(0, np.nan)).round(3)
    if fta is not None and fga is not None:
        out["FTr"] = (fta / fga.replace(0, np.nan)).round(3)
    if fg3a is not None and fga is not None:
        out["3PAr"] = (fg3a / fga.replace(0, np.nan)).round(3)
    if ast is not None and tov is not None:
        out["AST_TO"] = (ast / tov.replace(0, np.nan)).round(2)
    if minutes is not None:
        m = minutes.replace(0, np.nan)
        for src, dst in [("PTS", "PTS_PER36"), ("REB", "REB_PER36"), ("AST", "AST_PER36")]:
            v = col(src)
            if v is not None:
                out[dst] = (36 * v / m).round(1)
    return out


# ---------------------------------------------------------------------------
# Live fetchers (stats.nba.com via nba_api)
# ---------------------------------------------------------------------------

REQUEST_TIMEOUT = 30


def fetch_league_players(season: str, season_type: str, measure_type: str,
                         per_mode: str) -> pd.DataFrame:
    """League-wide per-player stats for one season (all players)."""
    from nba_api.stats.endpoints import leaguedashplayerstats

    res = leaguedashplayerstats.LeagueDashPlayerStats(
        season=season,
        season_type_all_star=season_type,
        measure_type_detailed_defense=measure_type,
        per_mode_detailed=per_mode,
        timeout=REQUEST_TIMEOUT,
    )
    df = res.get_data_frames()[0]
    if measure_type == "Base":
        df = add_derived_metrics(df)
    return df


def search_players(query: str) -> list[dict]:
    """Match players by (partial) name against nba_api's static roster."""
    from nba_api.stats.static import players

    return players.find_players_by_full_name(query)


def fetch_player_career(player_id: int, per_mode: str = "PerGame") -> pd.DataFrame:
    """Per-season base stats for a player's whole career (every era)."""
    from nba_api.stats.endpoints import playercareerstats

    res = playercareerstats.PlayerCareerStats(
        player_id=player_id, per_mode36=per_mode, timeout=REQUEST_TIMEOUT
    )
    frames = res.get_normalized_dict()
    reg = pd.DataFrame(frames["SeasonTotalsRegularSeason"])
    po = pd.DataFrame(frames["SeasonTotalsPostSeason"])
    reg["SEASON_TYPE"] = "Regular Season"
    po["SEASON_TYPE"] = "Playoffs"
    df = pd.concat([reg, po], ignore_index=True)
    return add_derived_metrics(df)


def fetch_player_advanced_by_year(player_id: int,
                                  season_type: str = "Regular Season") -> pd.DataFrame:
    """Per-season Advanced splits for one player (1996-97 onward)."""
    from nba_api.stats.endpoints import playerdashboardbyyearoveryear

    res = playerdashboardbyyearoveryear.PlayerDashboardByYearOverYear(
        player_id=player_id,
        measure_type_detailed="Advanced",
        season_type_playoffs=season_type,
        timeout=REQUEST_TIMEOUT,
    )
    df = pd.DataFrame(res.get_normalized_dict()["ByYearPlayerDashboard"])
    if "GROUP_VALUE" in df:
        df = df.rename(columns={"GROUP_VALUE": "SEASON_ID"})
    return df.sort_values("SEASON_ID").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Demo mode — deterministic synthetic data, no network required
# ---------------------------------------------------------------------------

_DEMO_PLAYERS = [
    ("Ayla Vantage", "BOS"), ("Marcus Ridge", "LAL"), ("Theo Castellanos", "DEN"),
    ("Jalen Okafor", "OKC"), ("Nikolas Brandt", "MIL"), ("Devon Sparks", "GSW"),
    ("Ilya Petrenko", "NYK"), ("Caleb Fontaine", "MIA"), ("Rui Nakamura", "PHX"),
    ("Andre Beaumont", "PHI"), ("Sam Whitfield", "DAL"), ("Omar Diallo", "MEM"),
    ("Luka Herzog", "SAS"), ("Trey Mercer", "CLE"), ("Kofi Mensah", "TOR"),
    ("Elias Vandermeer", "MIN"), ("Darius Cole", "NOP"), ("Yuto Sakamoto", "SAC"),
    ("Bram Kowalski", "IND"), ("Zion Marsh", "ORL"),
]


def _demo_rng(*keys) -> np.random.Generator:
    return np.random.default_rng(abs(hash(keys)) % (2**32))


def demo_league_players(season: str, season_type: str, measure_type: str,
                        per_mode: str) -> pd.DataFrame:
    """Synthetic league table shaped like the real Advanced/Base endpoints."""
    rng = _demo_rng("league", season, season_type)
    n = len(_DEMO_PLAYERS)
    minutes = rng.uniform(12, 37, n).round(1)
    usage = rng.uniform(0.12, 0.34, n).round(3)
    ts = np.clip(rng.normal(0.575, 0.04, n), 0.45, 0.70).round(3)
    base = {
        "PLAYER_ID": np.arange(900001, 900001 + n),
        "PLAYER_NAME": [p for p, _ in _DEMO_PLAYERS],
        "TEAM_ABBREVIATION": [t for _, t in _DEMO_PLAYERS],
        "AGE": rng.integers(20, 37, n),
        "GP": rng.integers(45, 82, n),
        "MIN": minutes,
    }
    if measure_type == "Advanced":
        off = (100 + 90 * usage * (ts - 0.55) + rng.normal(12, 4, n)).round(1)
        deff = rng.normal(112, 4, n).round(1)
        adv = {
            "OFF_RATING": off, "DEF_RATING": deff,
            "NET_RATING": (off - deff).round(1),
            "TS_PCT": ts, "EFG_PCT": (ts - 0.03).round(3), "USG_PCT": usage,
            "AST_PCT": rng.uniform(0.05, 0.45, n).round(3),
            "AST_TO": rng.uniform(0.8, 4.0, n).round(2),
            "AST_RATIO": rng.uniform(5, 30, n).round(1),
            "OREB_PCT": rng.uniform(0.01, 0.12, n).round(3),
            "DREB_PCT": rng.uniform(0.08, 0.30, n).round(3),
            "REB_PCT": rng.uniform(0.05, 0.20, n).round(3),
            "TM_TOV_PCT": rng.uniform(0.06, 0.18, n).round(3),
            "PACE": rng.normal(99, 3, n).round(1),
            "PIE": rng.uniform(0.05, 0.19, n).round(3),
            "POSS": rng.integers(1500, 5500, n),
        }
        return pd.DataFrame({**base, **adv})
    # Base-shaped demo frame
    fga = rng.uniform(6, 22, n).round(1)
    fg_pct = np.clip(rng.normal(0.47, 0.05, n), 0.38, 0.62)
    fg3a = (fga * rng.uniform(0.1, 0.55, n)).round(1)
    fta = (fga * rng.uniform(0.15, 0.45, n)).round(1)
    fgm, fg3m = (fga * fg_pct).round(1), (fg3a * 0.36).round(1)
    stats = {
        "FGM": fgm, "FGA": fga, "FG_PCT": fg_pct.round(3),
        "FG3M": fg3m, "FG3A": fg3a,
        "FTM": (fta * 0.79).round(1), "FTA": fta,
        "PTS": (2 * (fgm - fg3m) + 3 * fg3m + fta * 0.79).round(1),
        "REB": rng.uniform(2, 12, n).round(1),
        "AST": rng.uniform(1, 10, n).round(1),
        "STL": rng.uniform(0.3, 2.2, n).round(1),
        "BLK": rng.uniform(0.1, 2.5, n).round(1),
        "TOV": rng.uniform(0.8, 4.5, n).round(1),
        "PLUS_MINUS": rng.normal(0, 4, n).round(1),
    }
    return add_derived_metrics(pd.DataFrame({**base, **stats}))


def demo_search_players(query: str) -> list[dict]:
    q = query.lower()
    return [
        {"id": 900001 + i, "full_name": name, "is_active": True}
        for i, (name, _) in enumerate(_DEMO_PLAYERS)
        if q in name.lower()
    ]


def demo_player_career(player_id: int, per_mode: str = "PerGame") -> pd.DataFrame:
    """Synthetic 12-season career arc with a mid-career peak."""
    rng = _demo_rng("career", player_id)
    start = int(rng.integers(2008, 2014))
    seasons = [season_str(y) for y in range(start, start + 12)]
    n = len(seasons)
    arc = np.sin(np.linspace(0.3, np.pi - 0.3, n))          # rise / peak / decline
    pts = (8 + 20 * arc + rng.normal(0, 1.2, n)).round(1)
    fga = (pts / rng.uniform(1.15, 1.35, n)).round(1)
    fta = (fga * rng.uniform(0.2, 0.4, n)).round(1)
    fg3a = (fga * np.linspace(0.15, 0.45, n)).round(1)
    df = pd.DataFrame({
        "SEASON_ID": seasons,
        "SEASON_TYPE": "Regular Season",
        "PLAYER_AGE": np.arange(20, 20 + n),
        "TEAM_ABBREVIATION": rng.choice([t for _, t in _DEMO_PLAYERS], n),
        "GP": rng.integers(55, 82, n),
        "MIN": (20 + 16 * arc).round(1),
        "PTS": pts, "FGA": fga, "FTA": fta, "FG3A": fg3a,
        "FGM": (fga * np.clip(0.42 + 0.06 * arc, 0, 1)).round(1),
        "FG3M": (fg3a * 0.36).round(1),
        "FTM": (fta * 0.8).round(1),
        "REB": (3 + 5 * arc).round(1),
        "AST": (2 + 5 * arc).round(1),
        "STL": (0.5 + arc).round(1),
        "BLK": (0.3 + 0.8 * arc).round(1),
        "TOV": (1 + 2 * arc).round(1),
    })
    return add_derived_metrics(df)


def demo_player_advanced_by_year(player_id: int,
                                 season_type: str = "Regular Season") -> pd.DataFrame:
    career = demo_player_career(player_id)
    rng = _demo_rng("adv", player_id)
    n = len(career)
    arc = np.sin(np.linspace(0.3, np.pi - 0.3, n))
    off = (104 + 12 * arc + rng.normal(0, 1.5, n)).round(1)
    deff = (114 - 5 * arc + rng.normal(0, 1.5, n)).round(1)
    return pd.DataFrame({
        "SEASON_ID": career["SEASON_ID"],
        "GP": career["GP"], "MIN": career["MIN"],
        "OFF_RATING": off, "DEF_RATING": deff, "NET_RATING": (off - deff).round(1),
        "TS_PCT": career["TS_PCT"], "EFG_PCT": career["EFG_PCT"],
        "USG_PCT": (0.16 + 0.14 * arc).round(3),
        "AST_PCT": (0.10 + 0.20 * arc).round(3),
        "AST_TO": career["AST_TO"],
        "REB_PCT": (0.06 + 0.08 * arc).round(3),
        "TM_TOV_PCT": (0.14 - 0.03 * arc).round(3),
        "PACE": rng.normal(99, 2, n).round(1),
        "PIE": (0.07 + 0.09 * arc).round(3),
    })
