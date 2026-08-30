"""
dashboard.py — Streamlit dashboard for the Top-N Latent Arbitrage matrix (Phase 3).

Real-time map + sortable matrix over the LVE-LAP pipeline. Reuses the exact
same analysis path as the CLI (`master_engine.analyze`) so the dashboard and the
PDF dossiers can never disagree.

Run:
    pip install -r requirements-dashboard.txt
    streamlit run dashboard.py
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from config import Config, load_config
from master_engine import AnalysisResult, analyze
from parcels import synthetic_near_infrastructure
from report_generator import RankedParcel

st.set_page_config(page_title="LVE-LAP · Latent Arbitrage Matrix",
                   page_icon="🛰️", layout="wide")

# LAS band colors for the map (r, g, b).
_COLOR_HIGH = [46, 125, 50]     # green   >= 65
_COLOR_MID = [245, 158, 11]     # amber   40–65
_COLOR_LOW = [156, 163, 175]    # grey    < 40


def _las_color(score: float):
    if score >= 65:
        return _COLOR_HIGH
    if score >= 40:
        return _COLOR_MID
    return _COLOR_LOW


@st.cache_data(show_spinner=False)
def _run_pipeline(dataset_size: int, max_price_per_acre: float,
                  min_headroom_mw: float, buffer_miles: float):
    """Execute the pipeline with dashboard-controlled thresholds; return a frame.

    Cached on its inputs so slider tweaks that don't change them are instant.
    """
    base = load_config()
    cfg = Config(**{**base.__dict__,
                    "run_mode": "mock",
                    "max_price_per_acre": max_price_per_acre,
                    "min_substation_headroom_mw": min_headroom_mw,
                    "min_transmission_buffer_miles": buffer_miles})
    # Parcels are scattered near REAL substations from data/gis (loaded via
    # data_loaders.py); falls back to the fixed corridor if no real data present.
    parcels = synthetic_near_infrastructure(cfg.substations_path, n=dataset_size)
    result: AnalysisResult = analyze(cfg, parcels=parcels)
    return _to_frame(result.ranked), _meta(result)


def _meta(result: AnalysisResult) -> dict:
    return {
        "ingested": result.ingested,
        "survivors": len(result.ranked),
        "disqualified": len(result.disqualified),
        "water_rights_matched": result.water_rights_matched,
    }


def _to_frame(ranked: list[RankedParcel]) -> pd.DataFrame:
    rows = []
    for rp in ranked:
        p, s, v = rp.parcel, rp.score, rp.valuation
        rows.append({
            "Rank": rp.rank,
            "Parcel": p.parcel_id,
            "County": p.county,
            "State": p.state,
            "Acres": p.acres,
            "$/acre": round(p.price_per_acre, 0),
            "Asking": p.asking_price,
            "HBU Value": round(v.modeled_hbu_value, 0),
            "Multiple": v.arbitrage_multiple,
            "LAS": s.total,
            "Price": s.price_arbitrage * 100,
            "DOM": s.days_on_market * 100,
            "Infra": s.infrastructure_headroom * 100,
            "Resource": s.resource_optionality * 100,
            "Headroom MW": p.nearest_substation_headroom_mw,
            "Water AF": p.water_rights_acre_feet,
            "lat": p.lat,
            "lon": p.lon,
        })
    return pd.DataFrame(rows)


# --- Sidebar controls --------------------------------------------------------
st.sidebar.title("🛰️ LVE-LAP Controls")
st.sidebar.caption("Land Value Engine · Latent Arbitrage Protocol")

dataset_size = st.sidebar.slider("Synthetic corridor size", 20, 500, 150, step=10)
top_n = st.sidebar.slider("Top-N matrix", 10, 100, 50, step=10)
max_ppa = st.sidebar.slider("Max $/acre ceiling", 500, 5000, 2000, step=250)
min_mw = st.sidebar.slider("Min substation headroom (MW)", 0, 60, 10, step=5)
buffer_mi = st.sidebar.slider("Transmission buffer (miles)", 1.0, 6.0, 3.0, step=0.5)
states = st.sidebar.multiselect("States", ["NV", "AZ", "UT", "NM"],
                                default=["NV", "AZ", "UT", "NM"])
min_las = st.sidebar.slider("Min LAS filter", 0, 100, 0, step=5)

df, meta = _run_pipeline(dataset_size, float(max_ppa), float(min_mw), float(buffer_mi))

# Post-pipeline display filters (cheap; not part of the cached pipeline run).
if not df.empty:
    df = df[df["State"].isin(states) & (df["LAS"] >= min_las)]

# --- Header + KPIs -----------------------------------------------------------
st.title("Latent Arbitrage Matrix")
st.caption("Western US corridor · NV · AZ · UT · NM — "
           "modeled diagnostics, not appraisals or investment advice.")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Ingested", meta["ingested"])
c2.metric("Passed gates", meta["survivors"])
c3.metric("Disqualified", meta["disqualified"])
c4.metric("Water-rights enriched", meta["water_rights_matched"])
c5.metric("Shown", len(df))

if df.empty:
    st.warning("No parcels match the current filters. Loosen the thresholds "
               "in the sidebar.")
    st.stop()

# --- Map ---------------------------------------------------------------------
st.subheader("Corridor map — sized & colored by LAS")
try:
    import pydeck as pdk

    map_df = df.copy()
    map_df["color"] = map_df["LAS"].apply(_las_color)
    map_df["radius"] = (map_df["LAS"] * 60).clip(lower=1500)
    layer = pdk.Layer(
        "ScatterplotLayer", data=map_df,
        get_position="[lon, lat]", get_fill_color="color",
        get_radius="radius", pickable=True, opacity=0.7,
    )
    view = pdk.ViewState(latitude=float(map_df["lat"].mean()),
                         longitude=float(map_df["lon"].mean()), zoom=5)
    tooltip = {"text": "{Parcel}\nLAS {LAS} · {Multiple}x\n${$/acre}/acre"}
    st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view,
                             tooltip=tooltip))
except ImportError:
    st.map(df[["lat", "lon"]])
    st.caption("Install `pydeck` for LAS-colored markers and tooltips.")

# --- Top-N matrix ------------------------------------------------------------
st.subheader(f"Top {top_n} anomalies")
top_df = df.sort_values("LAS", ascending=False).head(top_n)
st.dataframe(
    top_df.drop(columns=["lat", "lon"]),
    use_container_width=True, hide_index=True,
    column_config={
        "Asking": st.column_config.NumberColumn(format="$%d"),
        "HBU Value": st.column_config.NumberColumn(format="$%d"),
        "$/acre": st.column_config.NumberColumn(format="$%d"),
        "LAS": st.column_config.ProgressColumn(min_value=0, max_value=100,
                                               format="%.1f"),
        "Multiple": st.column_config.NumberColumn(format="%.2fx"),
    },
)

# --- Detail panel ------------------------------------------------------------
st.subheader("Parcel detail")
choice = st.selectbox("Select a parcel", top_df["Parcel"].tolist())
row = top_df[top_df["Parcel"] == choice].iloc[0]

d1, d2 = st.columns(2)
with d1:
    st.markdown(f"**{row['Parcel']}** — {row['County']} County, {row['State']}")
    st.metric("Asking", f"${row['Asking']:,.0f}")
    st.metric("Modeled HBU Value", f"${row['HBU Value']:,.0f}")
    st.metric("Arbitrage Multiple", f"{row['Multiple']}x")
with d2:
    st.markdown("**LAS component breakdown**")
    st.bar_chart(pd.DataFrame({
        "score": [row["Price"], row["DOM"], row["Infra"], row["Resource"]],
    }, index=["Price (30%)", "DOM (20%)", "Infra (40%)", "Resource (10%)"]))
    st.caption(f"Headroom {row['Headroom MW']:.0f} MW · "
               f"Water {row['Water AF']:.0f} AF · Total LAS {row['LAS']}")
