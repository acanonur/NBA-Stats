"""Fetch NBA player stats from stats.nba.com and write them to CSV.

Two subcommands, mirroring the two SQL queries in this repo:

  * gamelog  - per-game log for a single player/season
               (matches nba_player_match_log.sql)
  * season   - league-wide per-player averages for a season
               (matches nba_player_season_stats.sql)

Examples:
    python fetch_nba_stats.py gamelog --player "LeBron James" --season 2023-24
    python fetch_nba_stats.py gamelog --player "Jayson Tatum" --season 2023-24 \\
        --season-type Playoffs --out tatum_playoffs.csv
    python fetch_nba_stats.py season  --season 2023-24 --out season_2023_24.csv
"""
from __future__ import annotations

import argparse
import csv
import sys

from nba_api.stats.endpoints import leaguedashplayerstats, playergamelog
from nba_api.stats.static import players

SEASON_TYPES = ["Regular Season", "Playoffs", "Pre Season", "All Star"]


def find_player_id(name: str) -> int:
    matches = players.find_players_by_full_name(name)
    if not matches:
        sys.exit(f"No player found matching '{name}'.")
    if len(matches) > 1:
        names = ", ".join(m["full_name"] for m in matches[:5])
        print(f"Multiple matches for '{name}'; using the first: {names}", file=sys.stderr)
    return matches[0]["id"]


def fetch_game_log(name: str, season: str, season_type: str) -> list[dict]:
    player_id = find_player_id(name)
    log = playergamelog.PlayerGameLog(
        player_id=player_id,
        season=season,
        season_type_all_star=season_type,
    )
    return log.get_normalized_dict()["PlayerGameLog"]


def fetch_season_stats(season: str, season_type: str) -> list[dict]:
    stats = leaguedashplayerstats.LeagueDashPlayerStats(
        season=season,
        season_type_all_star=season_type,
        per_mode_detailed="PerGame",
    )
    return stats.get_normalized_dict()["LeagueDashPlayerStats"]


def write_csv(rows: list[dict], path: str) -> None:
    if not rows:
        sys.exit("No rows returned.")
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch NBA stats from stats.nba.com.")
    sub = parser.add_subparsers(dest="command", required=True)

    gl = sub.add_parser("gamelog", help="Per-game log for a single player/season.")
    gl.add_argument("--player", required=True, help="Full player name, e.g. 'LeBron James'")
    gl.add_argument("--season", required=True, help="Season string, e.g. '2023-24'")
    gl.add_argument("--season-type", default="Regular Season", choices=SEASON_TYPES)
    gl.add_argument("--out", default="game_log.csv")

    ss = sub.add_parser("season", help="League-wide per-player season averages.")
    ss.add_argument("--season", required=True, help="Season string, e.g. '2023-24'")
    ss.add_argument("--season-type", default="Regular Season", choices=SEASON_TYPES)
    ss.add_argument("--out", default="season_stats.csv")

    args = parser.parse_args()

    if args.command == "gamelog":
        rows = fetch_game_log(args.player, args.season, args.season_type)
    else:
        rows = fetch_season_stats(args.season, args.season_type)

    write_csv(rows, args.out)
    print(f"Wrote {len(rows)} rows to {args.out}")


if __name__ == "__main__":
    main()
