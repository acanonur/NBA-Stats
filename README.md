# NBA Stats

Tools for pulling and exploring NBA player statistics from stats.nba.com.

## 🏀 Advanced Stats Dashboard

An interactive Streamlit dashboard with **advanced stats for every player,
every season**:

- **League Explorer** — all players for any season from **1996-97 onward**,
  with the full Advanced measure set (OFF/DEF/NET Rating, TS%, eFG%, USG%,
  AST%, REB%, TOV%, PACE, PIE, possessions…) plus Base / Usage / Scoring /
  Misc / Defense splits. Filter by games, minutes, and name; leaderboards,
  a usage-vs-efficiency scatter, and CSV export.
- **Player Career** — any player, any era (back to **1946-47**): season-by-
  season base stats plus per-season Advanced splits (1996-97+). For pre-1996
  seasons the dashboard derives the era-agnostic advanced metrics (TS%, eFG%,
  FTr, 3PAr, AST/TO, per-36 rates) from counting stats. Career trajectory
  charts for up to 4 metrics at once.
- **Compare Players** — overlay up to 4 careers on any advanced metric, by
  calendar season or by career year.
- **Glossary** — plain-English definitions of every advanced stat shown.

### Run it

```bash
pip install -r requirements.txt
streamlit run app.py
```

Data is fetched live from stats.nba.com (via [`nba_api`](https://github.com/swar/nba_api))
and cached for an hour. If stats.nba.com is unreachable, the app falls back to
clearly-labeled synthetic demo data so you can still explore the UI; set
`NBA_DASH_DEMO=1` to force demo mode.

> **Note:** stats.nba.com sometimes throttles or blocks datacenter/cloud IPs.
> The dashboard works best run locally on a residential connection.

### Files

| File | What it is |
|---|---|
| `app.py` | The Streamlit dashboard. |
| `nba_data.py` | Data layer: nba_api fetchers, derived advanced metrics, stat glossary, demo data. |
| `fetch_nba_stats.py` | CLI for exporting game logs / season averages to CSV. |
| `nba_player_match_log.sql` | BigQuery query: per-game player log. |
| `nba_player_season_stats.sql` | BigQuery query: per-season player aggregates. |

## CLI

```bash
python fetch_nba_stats.py gamelog --player "LeBron James" --season 2023-24
python fetch_nba_stats.py season --season 2023-24 --out season_2023_24.csv
```
