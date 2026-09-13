"""Generate contracts/presets.json and validate every widget against contracts/widgets.json.

Run it to regenerate the catalog:

    python3 contracts/tools/gen_presets.py > contracts/presets.json

The validation below is also the definition of "a preset widget is valid", so
scripts/check_contracts.py imports `validate()` from here rather than keeping a second copy
that could drift. Printing the document is therefore guarded by __main__: importing this
module loads the catalogs and defines PRESETS, and does nothing else.
"""
import json, os, sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CONTRACTS = os.path.join(_ROOT, "contracts")

CAT = json.load(open(os.path.join(_CONTRACTS, "widgets.json")))
MET = json.load(open(os.path.join(_CONTRACTS, "metrics.json")))
BY_KIND = {w["kind"]: w for w in CAT["widgets"]}
METRIC_KEYS = {m["key"] for m in MET["metrics"]}
METRIC_SCOPE = {m["key"]: m["scope"] for m in MET["metrics"]}
TOKENS = {"$favorite_player", "$favorite_team", "$featured_player", "$featured_team", "$league_leader"}

def w(kind, size, title, **config):
    return {"kind": kind, "size": size, "title": title, "config": config}

def preset(key, name, icon, tagline, accent, widgets):
    out = []
    for i, item in enumerate(widgets):
        item = dict(item)
        item["id"] = "%s.%02d.%s" % (key, i + 1, item["kind"])
        out.append(item)
    return {
        "id": "preset." + key, "presetKey": key, "name": name, "icon": icon,
        "tagline": tagline, "accent": accent, "isPreset": True, "schemaVersion": 1,
        "widgets": out,
    }

PRESETS = [
 preset("daily_recap", "Daily Recap", "sun.horizon", "Last night's slate, decoded.", "orange", [
   w("scoreboard", "large", "Last Night", date="latest", showTopPerformers=True, teamIds=[]),
   w("daily_movers", "large", "Biggest Games", date="latest", metric="game_score", limit=8,
     minMinutes=12.0, direction="best"),
   w("daily_movers", "medium", "Against The Grain", date="latest", metric="ts_pct", limit=6,
     minMinutes=15.0, direction="surprise"),
   w("stat_tile", "small", "Your Player", subjectType="player", subjectId="$favorite_player",
     metric="game_score", secondaryMetrics=["pts", "ts_pct"], season="latest",
     seasonType="Regular Season", perMode="PerGame", showSparkline=True, sparklineWindow=10),
   w("stat_tile", "small", "Your Team", subjectType="team", subjectId="$favorite_team",
     metric="net_rtg", secondaryMetrics=["off_rtg", "def_rtg"], season="latest",
     seasonType="Regular Season", perMode="PerGame", showSparkline=True, sparklineWindow=10),
 ]),
 preset("player_deep_dive", "Player Deep Dive", "person.crop.square", "Everything about one player.", "indigo", [
   w("player_snapshot", "large", "Season Profile", playerId="$favorite_player", season="latest",
     seasonType="Regular Season", showPercentiles=True,
     metrics=["ts_pct", "efg_pct", "usg_pct", "ast_pct", "tov_pct", "reb_pct",
              "off_rtg", "def_rtg", "net_rtg", "pie"]),
   w("game_log", "large", "Recent Games", playerId="$favorite_player", season="latest",
     seasonType="Regular Season", limit=10, highlightSeasonBest=True,
     columns=["min", "pts", "reb", "ast", "ts_pct", "usg_pct", "plus_minus", "game_score"]),
   w("trend_chart", "large", "Efficiency Trend", subjectType="player",
     subjectIds=["$favorite_player"], metric="ts_pct", season="latest",
     seasonType="Regular Season", rollingWindow=5, showLeagueAverage=True, showRawPoints=True),
   w("shot_profile", "large", "Shot Diet", subjectType="player", subjectId="$favorite_player",
     season="latest", seasonType="Regular Season", compareToLeague=True),
   w("career_arc", "large", "Career Arc", playerId="$favorite_player", metric="per",
     seasonType="Regular Season", includePlayoffs=True, xAxis="season"),
 ]),
 preset("advanced_scout", "Advanced Scout", "binoculars", "Two-way impact and the four factors.", "teal", [
   w("team_efficiency", "large", "League Efficiency", season="latest", seasonType="Regular Season",
     sortBy="net_rtg", conference="all", limit=30, style="scatter"),
   w("four_factors", "medium", "Four Factors", teamId="$favorite_team", season="latest",
     seasonType="Regular Season", showOpponent=True, comparison="league"),
   w("leaderboard", "large", "Defensive Anchors", subjectType="player", metric="def_rtg",
     scope="season", season="latest", seasonType="Regular Season", perMode="PerGame",
     limit=10, minGames=15, minMinutesPerGame=24.0, positions=[], teamIds=[],
     secondaryMetrics=["blk_pct", "dreb_pct"], ascending=False),
   w("leaderboard", "large", "On/Off Swing", subjectType="player", metric="net_rtg",
     scope="season", season="latest", seasonType="Regular Season", perMode="PerGame",
     limit=10, minGames=20, minMinutesPerGame=24.0, positions=[], teamIds=[],
     secondaryMetrics=["off_rtg", "def_rtg"], ascending=False),
 ]),
 preset("efficiency_hunt", "Efficiency Hunt", "flame", "Who scores without wasting possessions.", "red", [
   w("leaderboard", "large", "True Shooting", subjectType="player", metric="ts_pct",
     scope="season", season="latest", seasonType="Regular Season", perMode="PerGame",
     limit=12, minGames=15, minMinutesPerGame=24.0, positions=[], teamIds=[],
     secondaryMetrics=["pts", "usg_pct"], ascending=False),
   w("leaderboard", "large", "Usage Kings", subjectType="player", metric="usg_pct",
     scope="season", season="latest", seasonType="Regular Season", perMode="PerGame",
     limit=12, minGames=15, minMinutesPerGame=24.0, positions=[], teamIds=[],
     secondaryMetrics=["ts_pct", "tov_pct"], ascending=False),
   w("comparison", "large", "Volume vs Efficiency", playerIds=["$league_leader", "$favorite_player"],
     metrics=["pts", "usg_pct", "ts_pct", "efg_pct", "ftr", "tov_pct"], season="latest",
     seasonType="Regular Season", normalization="percentile", style="bars"),
   w("stat_tile", "small", "League Average TS%", subjectType="team", subjectId="$featured_team",
     metric="ts_pct", secondaryMetrics=["efg_pct"], season="latest",
     seasonType="Regular Season", perMode="PerGame", showSparkline=False, sparklineWindow=15),
 ]),
 preset("all_time_greats", "All-Time Greats", "crown", "Peak seasons across eighty years.", "amber", [
   w("leaderboard", "large", "Best Seasons Ever: PER", subjectType="player", metric="per",
     scope="all_time", season="latest", seasonType="Regular Season", perMode="PerGame",
     limit=15, minGames=40, minMinutesPerGame=28.0, positions=[], teamIds=[],
     secondaryMetrics=["ws", "pts"], ascending=False),
   w("leaderboard", "large", "Best Seasons Ever: Win Shares", subjectType="player", metric="ws",
     scope="all_time", season="latest", seasonType="Regular Season", perMode="Totals",
     limit=15, minGames=40, minMinutesPerGame=28.0, positions=[], teamIds=[],
     secondaryMetrics=["ws48", "gp"], ascending=False),
   w("career_arc", "large", "Career Arc", playerId="$favorite_player", metric="ws48",
     seasonType="Regular Season", includePlayoffs=True, xAxis="age"),
   w("comparison", "large", "Head To Head", playerIds=["$favorite_player", "$league_leader"],
     metrics=["per", "ws48", "bpm", "vorp", "ts_pct", "usg_pct"], season="latest",
     seasonType="Regular Season", normalization="raw", style="table"),
 ]),
 preset("fantasy_watch", "Fantasy Watch", "dice", "Production, minutes and the nightly swing.", "green", [
   w("daily_movers", "large", "Last Night's Scores", date="latest", metric="fantasy_pts",
     limit=10, minMinutes=10.0, direction="best"),
   w("game_log", "large", "Your Player's Log", playerId="$favorite_player", season="latest",
     seasonType="Regular Season", limit=10, highlightSeasonBest=True,
     columns=["min", "fantasy_pts", "pts", "reb", "ast", "stl", "blk", "tov"]),
   w("leaderboard", "large", "Fantasy Leaders", subjectType="player", metric="fantasy_pts",
     scope="season", season="latest", seasonType="Regular Season", perMode="PerGame",
     limit=12, minGames=5, minMinutesPerGame=15.0, positions=[], teamIds=[],
     secondaryMetrics=["min", "usg_pct"], ascending=False),
   w("trend_chart", "large", "Minutes Trend", subjectType="player", subjectIds=["$favorite_player"],
     metric="min", season="latest", seasonType="Regular Season", rollingWindow=5,
     showLeagueAverage=False, showRawPoints=True),
 ]),
 preset("team_pulse", "Team Pulse", "flag.checkered", "One team, week to week.", "blue", [
   w("stat_tile", "small", "Net Rating", subjectType="team", subjectId="$favorite_team",
     metric="net_rtg", secondaryMetrics=["off_rtg", "def_rtg"], season="latest",
     seasonType="Regular Season", perMode="PerGame", showSparkline=True, sparklineWindow=15),
   w("stat_tile", "small", "Pace", subjectType="team", subjectId="$favorite_team", metric="pace",
     secondaryMetrics=["poss"], season="latest", seasonType="Regular Season", perMode="PerGame",
     showSparkline=True, sparklineWindow=15),
   w("four_factors", "large", "Four Factors", teamId="$favorite_team", season="latest",
     seasonType="Regular Season", showOpponent=True, comparison="league"),
   w("trend_chart", "large", "Net Rating Trend", subjectType="team", subjectIds=["$favorite_team"],
     metric="net_rtg", season="latest", seasonType="Regular Season", rollingWindow=10,
     showLeagueAverage=True, showRawPoints=True),
   w("scoreboard", "large", "Team Schedule", date="latest", showTopPerformers=True,
     teamIds=["$favorite_team"]),
 ]),
 preset("playoff_lab", "Playoff Lab", "trophy", "How the postseason changes everything.", "purple", [
   w("leaderboard", "large", "Playoff Risers", subjectType="player", metric="ts_pct",
     scope="season", season="latest", seasonType="Playoffs", perMode="PerGame",
     limit=12, minGames=4, minMinutesPerGame=20.0, positions=[], teamIds=[],
     secondaryMetrics=["pts", "usg_pct"], ascending=False),
   w("team_efficiency", "large", "Playoff Efficiency", season="latest", seasonType="Playoffs",
     sortBy="net_rtg", conference="all", limit=16, style="table"),
   w("career_arc", "large", "Playoff Career Arc", playerId="$favorite_player", metric="pie",
     seasonType="Playoffs", includePlayoffs=True, xAxis="season"),
   w("comparison", "large", "Regular Season vs Playoffs", playerIds=["$favorite_player", "$league_leader"],
     metrics=["pts", "ts_pct", "usg_pct", "ast_pct", "net_rtg", "pie"], season="latest",
     seasonType="Playoffs", normalization="percentile", style="bars"),
 ]),
 preset("blank", "Blank Canvas", "square.dashed", "Start empty and build your own.", "graphite", []),
]

# ---- validation -------------------------------------------------------------
def validate(presets=None):
    """Return a list of human-readable problems; empty means every widget is valid.

    Checks each preset widget against contracts/widgets.json (the kind exists, the size is
    offered, required config keys are present, no unknown keys) and against
    contracts/metrics.json (every metric key exists and is in scope), plus the subject tokens
    and the per-field bounds the catalog declares.
    """
    errors = []
    for p in PRESETS if presets is None else presets:
        for item in p["widgets"]:
            spec = BY_KIND.get(item["kind"])
            if spec is None:
                errors.append("%s: unknown widget kind %s" % (p["presetKey"], item["kind"])); continue
            if item["size"] not in spec["sizes"]:
                errors.append("%s/%s: size %s not in %s" % (p["presetKey"], item["kind"], item["size"], spec["sizes"]))
            fields = {c["key"]: c for c in spec["config"]}
            for c in spec["config"]:
                if c["required"] and c["key"] not in item["config"]:
                    errors.append("%s/%s: missing required config %s" % (p["presetKey"], item["kind"], c["key"]))
            for k, v in item["config"].items():
                c = fields.get(k)
                if c is None:
                    errors.append("%s/%s: unknown config key %s" % (p["presetKey"], item["kind"], k)); continue
                t = c["type"]
                if t == "enum" and v not in c["options"]:
                    errors.append("%s/%s.%s: %r not in %s" % (p["presetKey"], item["kind"], k, v, c["options"]))
                if t == "enumList":
                    for x in v:
                        if x not in c["options"]:
                            errors.append("%s/%s.%s: %r not in %s" % (p["presetKey"], item["kind"], k, x, c["options"]))
                if t == "metric" and v not in METRIC_KEYS:
                    errors.append("%s/%s.%s: unknown metric %r" % (p["presetKey"], item["kind"], k, v))
                if t == "metricList":
                    for x in v:
                        if x not in METRIC_KEYS:
                            errors.append("%s/%s.%s: unknown metric %r" % (p["presetKey"], item["kind"], k, x))
                        elif c.get("metricScope") == "player" and "player" not in METRIC_SCOPE[x]:
                            errors.append("%s/%s.%s: metric %r is not a player metric" % (p["presetKey"], item["kind"], k, x))
                    if c.get("maxItems") and len(v) > c["maxItems"]:
                        errors.append("%s/%s.%s: %d items exceeds max %d" % (p["presetKey"], item["kind"], k, len(v), c["maxItems"]))
                if t in ("player", "team", "subject") and isinstance(v, str) and v not in TOKENS:
                    errors.append("%s/%s.%s: unknown subject token %r" % (p["presetKey"], item["kind"], k, v))
                if t in ("playerList", "teamList", "subjectList"):
                    for x in v:
                        if isinstance(x, str) and x not in TOKENS:
                            errors.append("%s/%s.%s: unknown subject token %r" % (p["presetKey"], item["kind"], k, x))
                    if c.get("minItems") and len(v) < c["minItems"]:
                        errors.append("%s/%s.%s: %d items below min %d" % (p["presetKey"], item["kind"], k, len(v), c["minItems"]))
                if t == "int" and not isinstance(v, int):
                    errors.append("%s/%s.%s: expected int, got %r" % (p["presetKey"], item["kind"], k, v))
                if t == "double" and not isinstance(v, float):
                    errors.append("%s/%s.%s: expected double, got %r" % (p["presetKey"], item["kind"], k, v))
                if t == "bool" and not isinstance(v, bool):
                    errors.append("%s/%s.%s: expected bool, got %r" % (p["presetKey"], item["kind"], k, v))

    return errors


#: The preset catalog's content version, served as ``version`` by ``GET /v1/presets``.
#: ``contracts/CONTRACT.md`` §3 promises this *increments* whenever the preset content
#: changes, so bump it by hand in the same commit that edits PRESETS or subjectTokens. It is
#: a counter, not a fingerprint: a client stores the last one it saw and tests ``server > mine``.
VERSION = 1

def document():
    """The contracts/presets.json document, as a dict."""
    return {
    "schemaVersion": 1,
    "version": VERSION,
    "subjectTokens": [
        {"token": "$favorite_player", "summary": "The player the user pinned as a favorite."},
        {"token": "$favorite_team", "summary": "The team the user pinned as a favorite."},
        {"token": "$featured_player", "summary": "The season's leader in PIE, used when no favorite is set."},
        {"token": "$featured_team", "summary": "The season's leader in net rating, used when no favorite is set."},
        {"token": "$league_leader", "summary": "The season's scoring leader."},
    ],
    "presets": PRESETS,
    }


def main():
    errors = validate()
    if errors:
        print("VALIDATION FAILED:", file=sys.stderr)
        for e in errors:
            print("  " + e, file=sys.stderr)
        return 1
    print(json.dumps(document(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
