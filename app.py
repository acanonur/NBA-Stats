"""NBA Advanced Stats Dashboard.

Run with:
    streamlit run app.py

Live data comes from stats.nba.com via nba_api. If the network is
unreachable (or NBA_DASH_DEMO=1 is set), the app falls back to a
deterministic synthetic demo dataset so the UI still works.
"""
from __future__ import annotations

import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import nba_data as nd

# ---------------------------------------------------------------------------
# Theme — validated categorical palette + chart chrome (see .streamlit/config.toml)
# ---------------------------------------------------------------------------

SERIES = ["#2a78d6", "#1baf7a", "#eda100", "#008300",
          "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"]  # fixed order, never cycled
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
SURFACE = "#fcfcfb"

PLOTLY_LAYOUT = dict(
    paper_bgcolor=SURFACE,
    plot_bgcolor=SURFACE,
    font=dict(family='system-ui, -apple-system, "Segoe UI", sans-serif',
              color=INK_SECONDARY, size=13),
    xaxis=dict(gridcolor=GRID, zerolinecolor=GRID, linecolor="#c3c2b7",
               title_font_color=INK_MUTED, tickfont_color=INK_MUTED),
    yaxis=dict(gridcolor=GRID, zerolinecolor=GRID, linecolor="#c3c2b7",
               title_font_color=INK_MUTED, tickfont_color=INK_MUTED),
    margin=dict(l=10, r=10, t=90, b=10),
    hoverlabel=dict(bgcolor="#ffffff", font_color=INK, bordercolor=GRID),
    legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
)


def style_chart(fig: go.Figure, title: str, **kwargs) -> go.Figure:
    """Apply the shared chrome; title sits above the legend row."""
    fig.update_layout(**PLOTLY_LAYOUT, **kwargs)
    fig.update_layout(title=dict(text=title, yanchor="top", y=0.97,
                                 font=dict(color=INK, size=15)))
    return fig

DEMO_FORCED = os.environ.get("NBA_DASH_DEMO") == "1"

st.set_page_config(page_title="NBA Advanced Stats", page_icon="🏀", layout="wide")


# ---------------------------------------------------------------------------
# Cached data access with live -> demo fallback
# ---------------------------------------------------------------------------

def _live_or_demo(live_fn, demo_fn, *args, **kwargs):
    """Try the live endpoint; fall back to demo data and remember why."""
    if not DEMO_FORCED:
        try:
            return live_fn(*args, **kwargs), "live"
        except Exception as exc:  # network blocked, timeout, API hiccup…
            st.session_state["last_fetch_error"] = str(exc)
    return demo_fn(*args, **kwargs), "demo"


@st.cache_data(ttl=3600, show_spinner="Fetching league stats from stats.nba.com…")
def league_players(season, season_type, measure_type, per_mode):
    return _live_or_demo(nd.fetch_league_players, nd.demo_league_players,
                         season, season_type, measure_type, per_mode)


@st.cache_data(ttl=3600, show_spinner="Fetching career stats…")
def player_career(player_id, per_mode="PerGame"):
    return _live_or_demo(nd.fetch_player_career, nd.demo_player_career,
                         player_id, per_mode)


@st.cache_data(ttl=3600, show_spinner="Fetching advanced splits…")
def player_advanced(player_id, season_type="Regular Season"):
    return _live_or_demo(nd.fetch_player_advanced_by_year,
                         nd.demo_player_advanced_by_year,
                         player_id, season_type)


@st.cache_data(ttl=86400)
def find_players(query):
    return _live_or_demo(nd.search_players, nd.demo_search_players, query)


def demo_banner(source: str) -> None:
    if source == "demo":
        reason = st.session_state.get("last_fetch_error", "")
        st.warning(
            "**Demo data** — stats.nba.com is unreachable from here, so synthetic "
            "sample data is shown. Run the app on a machine with open internet "
            "access for live stats." + (f"\n\n`{reason[:200]}`" if reason else ""),
            icon="⚠️",
        )


def pct_format(df: pd.DataFrame) -> dict:
    """Column config: render 0-1 ratio columns as percentages."""
    pct_cols = [c for c in df.columns
                if c.endswith("_PCT") or c in ("FTr", "3PAr", "PIE")]
    return {c: st.column_config.NumberColumn(format="%.1f%%")
            for c in pct_cols
            if pd.to_numeric(df[c], errors="coerce").max() is not None
            and pd.to_numeric(df[c], errors="coerce").max() <= 1.5}


def as_pct_view(df: pd.DataFrame) -> pd.DataFrame:
    """Multiply 0-1 ratio columns by 100 so tables read as percentages."""
    out = df.copy()
    for c in out.columns:
        if c.endswith("_PCT") or c in ("FTr", "3PAr", "PIE"):
            v = pd.to_numeric(out[c], errors="coerce")
            if v.max() is not None and v.max() <= 1.5:
                out[c] = (v * 100).round(1)
    return out


# ---------------------------------------------------------------------------
# Header + tabs
# ---------------------------------------------------------------------------

st.title("🏀 NBA Advanced Stats Dashboard")
st.caption(
    "League-wide advanced splits (1996-97 →) and full career stats for every "
    "player back to 1946-47, straight from stats.nba.com."
)

tab_league, tab_player, tab_compare, tab_glossary = st.tabs(
    ["📊 League Explorer", "👤 Player Career", "⚖️ Compare Players", "📖 Glossary"]
)

# ===========================================================================
# TAB 1 — League Explorer: every player, one season, any measure type
# ===========================================================================
with tab_league:
    c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
    season = c1.selectbox("Season", nd.all_seasons(), index=0,
                          help="League-wide advanced splits exist from 1996-97 onward.")
    season_type = c2.selectbox("Season type", nd.SEASON_TYPES, key="lg_type")
    measure = c3.selectbox("Measure type", nd.MEASURE_TYPES,
                           help="Advanced = ratings, TS%, USG%, PIE… "
                                "Base = counting stats plus derived TS%/eFG%/FTr.")
    per_mode = c4.selectbox("Per mode", nd.PER_MODES,
                            disabled=measure == "Advanced",
                            help="Advanced splits are rate stats already; per-mode applies to the others.")

    df, source = league_players(season, season_type, measure, per_mode)
    demo_banner(source)

    if df.empty:
        st.info("No rows returned for this selection.")
    else:
        fc1, fc2, fc3 = st.columns([2, 2, 4])
        min_gp = fc1.slider("Min games played", 0, 82, 20,
                            help="Filter out small samples that distort rate stats.")
        min_min = fc2.slider("Min minutes/game", 0, 40, 10)
        name_q = fc3.text_input("Search player", placeholder="e.g. Jokic")

        view = df.copy()
        if "GP" in view:
            view = view[pd.to_numeric(view["GP"], errors="coerce") >= min_gp]
        if "MIN" in view and "GP" in view:
            mins = pd.to_numeric(view["MIN"], errors="coerce")
            gp = pd.to_numeric(view["GP"], errors="coerce")
            mpg = mins / gp.replace(0, pd.NA) if mins.max() > 48 else mins
            view = view[mpg >= min_min]
        if name_q:
            view = view[view["PLAYER_NAME"].str.contains(name_q, case=False, na=False)]

        st.markdown(f"**{len(view)} players** · {season} {season_type} · {measure}")

        # --- Leaders bar (single series -> first categorical slot, no legend)
        metric_cols = [c for c in view.columns
                       if pd.api.types.is_numeric_dtype(view[c])
                       and c not in ("PLAYER_ID", "TEAM_ID", "AGE", "CFID")
                       and not c.endswith("_RANK")]
        default_metric = ("PIE" if "PIE" in metric_cols
                          else "TS_PCT" if "TS_PCT" in metric_cols
                          else metric_cols[0]) if metric_cols else None

        if default_metric:
            lc1, lc2 = st.columns([3, 5])
            with lc1:
                lead_metric = st.selectbox("Leaderboard metric", metric_cols,
                                           index=metric_cols.index(default_metric))
                ascending = st.toggle("Lower is better", value=lead_metric == "DEF_RATING",
                                      help="e.g. Defensive Rating")
            top = view.dropna(subset=[lead_metric]).sort_values(
                lead_metric, ascending=ascending).head(15)[::-1]
            lead_vals = pd.to_numeric(top[lead_metric], errors="coerce")
            lead_is_ratio = (lead_vals.max() is not None and lead_vals.max() <= 1.5
                             and (lead_metric.endswith("_PCT")
                                  or lead_metric in ("PIE", "FTr", "3PAr")))
            if lead_is_ratio:
                lead_vals = lead_vals * 100
            fig = go.Figure(go.Bar(
                x=lead_vals, y=top["PLAYER_NAME"], orientation="h",
                marker=dict(color=SERIES[0], cornerradius=4),
                width=0.55,
                hovertemplate="<b>%{y}</b><br>" + lead_metric + ": %{x}<extra></extra>",
            ))
            style_chart(fig, f"Top 15 — {lead_metric}"
                             f"{' (%)' if lead_is_ratio else ''} ({season})",
                        height=460)
            with lc2:
                st.plotly_chart(fig, use_container_width=True)

        # --- Efficiency landscape: USG% vs TS% (Advanced only)
        if {"USG_PCT", "TS_PCT", "MIN"}.issubset(view.columns):
            sc = view.dropna(subset=["USG_PCT", "TS_PCT"])
            fig = go.Figure(go.Scatter(
                x=sc["USG_PCT"] * 100, y=sc["TS_PCT"] * 100, mode="markers",
                marker=dict(size=8 + 14 * (pd.to_numeric(sc["MIN"], errors="coerce")
                                           / max(pd.to_numeric(sc["MIN"], errors="coerce").max(), 1)),
                            color=SERIES[0], opacity=0.55,
                            line=dict(width=2, color=SURFACE)),
                text=sc["PLAYER_NAME"],
                customdata=sc[["TEAM_ABBREVIATION", "MIN"]] if "TEAM_ABBREVIATION" in sc else None,
                hovertemplate="<b>%{text}</b> (%{customdata[0]})<br>"
                              "USG%%: %{x:.1f} · TS%%: %{y:.1f}<br>"
                              "MIN: %{customdata[1]}<extra></extra>",
            ))
            med_ts = (sc["TS_PCT"] * 100).median()
            fig.add_hline(y=med_ts, line_color=INK_MUTED, line_dash="dot",
                          annotation_text="league median TS%",
                          annotation_font_color=INK_MUTED)
            style_chart(fig, "Efficiency landscape — Usage vs True Shooting (bubble = minutes)",
                        height=520)
            fig.update_xaxes(title_text="Usage %")
            fig.update_yaxes(title_text="True Shooting %")
            st.plotly_chart(fig, use_container_width=True)

        # --- Full table (the always-available accessible view)
        st.subheader("All players")
        st.dataframe(as_pct_view(view).reset_index(drop=True),
                     use_container_width=True, height=520)
        st.download_button("⬇️ Download CSV",
                           view.to_csv(index=False).encode(),
                           file_name=f"nba_{measure.lower()}_{season}_{season_type.replace(' ', '_')}.csv",
                           mime="text/csv")

# ===========================================================================
# TAB 2 — Player Career: every season of one player's career
# ===========================================================================
with tab_player:
    q = st.text_input("Player name", placeholder="LeBron James",
                      key="career_query")
    if q:
        matches, m_source = find_players(q)
        if not matches:
            st.info(f"No player found matching “{q}”.")
        else:
            names = {m["full_name"]: m["id"] for m in matches}
            pick = st.selectbox("Select player", list(names)) if len(names) > 1 else list(names)[0]
            pid = names[pick]
            stype = st.radio("Season type", nd.SEASON_TYPES, horizontal=True,
                             key="career_stype")

            career, c_source = player_career(pid)
            adv, a_source = player_advanced(pid, stype)
            demo_banner(c_source)

            cdf = career[career["SEASON_TYPE"] == stype].copy()
            if cdf.empty:
                st.info(f"No {stype} data for {pick}.")
            else:
                last = cdf.iloc[-1]
                st.subheader(pick)
                t1, t2, t3, t4, t5, t6 = st.columns(6)
                t1.metric("Seasons", cdf["SEASON_ID"].nunique())
                t2.metric("Games", int(pd.to_numeric(cdf["GP"], errors="coerce").sum()))
                t3.metric("PPG (last)", f"{last.get('PTS', float('nan')):.1f}")
                ts_series = pd.to_numeric(cdf.get("TS_PCT"), errors="coerce")
                t4.metric("Career-best TS%",
                          f"{ts_series.max() * 100:.1f}%" if ts_series.notna().any() else "—")
                if not adv.empty and "PIE" in adv:
                    t5.metric("Peak PIE", f"{pd.to_numeric(adv['PIE'], errors='coerce').max() * 100:.1f}%")
                    t6.metric("Peak NET RTG", f"{pd.to_numeric(adv['NET_RATING'], errors='coerce').max():+.1f}")

                # merge advanced onto career seasons where available (1996-97+)
                merged = cdf.merge(
                    adv.drop(columns=[c for c in ("GP", "MIN", "TS_PCT", "EFG_PCT", "AST_TO")
                                      if c in adv]),
                    on="SEASON_ID", how="left") if not adv.empty else cdf

                metric_opts = [c for c in
                               ["PTS", "TS_PCT", "EFG_PCT", "USG_PCT", "PIE",
                                "NET_RATING", "OFF_RATING", "DEF_RATING", "AST_PCT",
                                "REB_PCT", "AST_TO", "3PAr", "FTr", "MIN", "REB", "AST"]
                               if c in merged.columns]
                chosen = st.multiselect("Career trajectory metrics", metric_opts,
                                        default=[m for m in ("PTS", "TS_PCT") if m in metric_opts],
                                        max_selections=4)

                # One chart per metric (never dual-axis) — small multiples
                if chosen:
                    cols = st.columns(min(len(chosen), 2))
                    for i, m in enumerate(chosen):
                        y = pd.to_numeric(merged[m], errors="coerce")
                        is_ratio = y.max() is not None and y.max() <= 1.5 and (
                            m.endswith("_PCT") or m in ("PIE", "FTr", "3PAr"))
                        yv = y * 100 if is_ratio else y
                        fig = go.Figure(go.Scatter(
                            x=merged["SEASON_ID"], y=yv, mode="lines+markers",
                            line=dict(color=SERIES[i % len(SERIES)], width=2),
                            marker=dict(size=8, line=dict(width=2, color=SURFACE)),
                            hovertemplate="<b>%{x}</b><br>" + m + ": %{y:.1f}<extra></extra>",
                        ))
                        style_chart(fig, m + (" (%)" if is_ratio else ""),
                                    height=320, showlegend=False)
                        cols[i % len(cols)].plotly_chart(fig, use_container_width=True)

                st.subheader(f"Season-by-season · {stype}")
                st.dataframe(as_pct_view(merged).reset_index(drop=True),
                             use_container_width=True)
                st.download_button("⬇️ Download career CSV",
                                   merged.to_csv(index=False).encode(),
                                   file_name=f"{pick.replace(' ', '_').lower()}_career.csv",
                                   mime="text/csv")
    else:
        st.info("Type a player name to load their full career — any player, any era.")

# ===========================================================================
# TAB 3 — Compare Players
# ===========================================================================
with tab_compare:
    qc = st.text_input("Search players to add", placeholder="Type a name, then pick below",
                       key="cmp_query")
    if "cmp_selected" not in st.session_state:
        st.session_state.cmp_selected = {}  # name -> id, insertion order = color slot

    if qc:
        matches, _ = find_players(qc)
        options = {m["full_name"]: m["id"] for m in matches[:8]}
        add = st.selectbox("Matches", ["—"] + list(options), key="cmp_add")
        if add != "—" and add not in st.session_state.cmp_selected:
            if len(st.session_state.cmp_selected) >= 4:
                st.warning("Compare up to 4 players — remove one first.")
            else:
                st.session_state.cmp_selected[add] = options[add]

    selected = st.session_state.cmp_selected
    if selected:
        remove = st.multiselect("Comparing (deselect to remove)", list(selected),
                                default=list(selected))
        for name in list(selected):
            if name not in remove:
                del selected[name]

    if not selected:
        st.info("Add 2–4 players to compare their career arcs on any advanced metric.")
    else:
        stype_c = st.radio("Season type", nd.SEASON_TYPES, horizontal=True, key="cmp_stype")
        frames = {}
        any_demo = False
        for name, pid in selected.items():
            career, s1 = player_career(pid)
            adv, s2 = player_advanced(pid, stype_c)
            any_demo = any_demo or "demo" in (s1, s2)
            cdf = career[career["SEASON_TYPE"] == stype_c]
            merged = cdf.merge(
                adv.drop(columns=[c for c in ("GP", "MIN", "TS_PCT", "EFG_PCT", "AST_TO")
                                  if c in adv]),
                on="SEASON_ID", how="left") if not adv.empty else cdf
            frames[name] = merged
        if any_demo:
            demo_banner("demo")

        common = set.intersection(*(set(f.columns) for f in frames.values())) if frames else set()
        metric_opts = [c for c in
                       ["PTS", "TS_PCT", "USG_PCT", "PIE", "NET_RATING", "OFF_RATING",
                        "DEF_RATING", "AST_PCT", "REB_PCT", "EFG_PCT", "AST_TO",
                        "3PAr", "FTr", "MIN", "REB", "AST"] if c in common]
        mc1, mc2 = st.columns([3, 3])
        metric = mc1.selectbox("Metric", metric_opts,
                               index=metric_opts.index("TS_PCT") if "TS_PCT" in metric_opts else 0)
        x_mode = mc2.radio("X-axis", ["Season number (career year)", "Calendar season"],
                           horizontal=True)

        fig = go.Figure()
        for slot, (name, f) in enumerate(frames.items()):  # slot fixed by add order
            y = pd.to_numeric(f[metric], errors="coerce")
            is_ratio = y.max() is not None and y.max() <= 1.5 and (
                metric.endswith("_PCT") or metric in ("PIE", "FTr", "3PAr"))
            yv = y * 100 if is_ratio else y
            x = list(range(1, len(f) + 1)) if x_mode.startswith("Season number") else f["SEASON_ID"]
            fig.add_trace(go.Scatter(
                x=x, y=yv, mode="lines+markers", name=name,
                line=dict(color=SERIES[slot], width=2),
                marker=dict(size=8, line=dict(width=2, color=SURFACE)),
                customdata=f["SEASON_ID"],
                hovertemplate="<b>" + name + "</b> · %{customdata}<br>"
                              + metric + ": %{y:.1f}<extra></extra>",
            ))
        style_chart(fig, f"{metric}{' (%)' if is_ratio else ''} — career comparison ({stype_c})",
                    height=520)
        fig.update_xaxes(title_text="Career year" if x_mode.startswith("Season number") else "Season")
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Side-by-side career table")
        stack = pd.concat(
            [f.assign(PLAYER=name) for name, f in frames.items()], ignore_index=True)
        lead = [c for c in ("PLAYER", "SEASON_ID", "TEAM_ABBREVIATION", "GP", "MIN") if c in stack]
        st.dataframe(as_pct_view(stack[lead + [c for c in stack.columns if c not in lead]]),
                     use_container_width=True, height=420)

# ===========================================================================
# TAB 4 — Glossary
# ===========================================================================
with tab_glossary:
    st.subheader("What the advanced stats mean")
    st.markdown(
        "League-wide **Advanced** splits (ratings, USG%, PIE, PACE…) exist on "
        "stats.nba.com from **1996-97** onward. For earlier eras the dashboard "
        "computes the era-agnostic metrics (TS%, eFG%, FTr, 3PAr, AST/TO, per-36 "
        "rates) directly from each player's counting stats, so career pages work "
        "all the way back to 1946-47."
    )
    for k, v in nd.GLOSSARY.items():
        st.markdown(f"**{k}** — {v}")
