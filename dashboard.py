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

from pathlib import Path

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


def _conf_color(pct: float):
    if pct is None:
        return _COLOR_LOW
    if pct >= 75:
        return _COLOR_HIGH
    if pct >= 50:
        return _COLOR_MID
    return _COLOR_LOW


@st.cache_data(show_spinner="Running pipeline…")
def _run_pipeline(source: str, dataset_size: int, max_price_per_acre: float,
                  min_headroom_mw: float, buffer_miles: float,
                  file_text: str = "", file_fmt: str = "csv"):
    """Execute the pipeline for the chosen data source; return (frame, meta).

    Cached on all inputs so unchanged runs are instant. For 'file' source the
    curated parcels' coordinates are resolved via public GIS inside analyze().
    """
    base = load_config()
    cfg = Config(**{**base.__dict__,
                    "run_mode": "mock",
                    "max_price_per_acre": max_price_per_acre,
                    "min_substation_headroom_mw": min_headroom_mw,
                    "min_transmission_buffer_miles": buffer_miles})

    if source == "file":
        from parcel_import import parcels_from_text
        parcels = parcels_from_text(file_text, file_fmt) if file_text else []
    else:
        # Scattered near REAL substations from data/gis (loaded via data_loaders.py).
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
            "Conf%": rp.confidence.total_pct if rp.confidence else None,
            "Conf": rp.confidence.label if rp.confidence else None,
            "⚑": len(rp.flags or []),
            "flag_msgs": [{"level": fl.level, "message": fl.message}
                          for fl in (rp.flags or [])],
            "Headroom MW": p.nearest_substation_headroom_mw,
            "Water AF": p.water_rights_acre_feet,
            "Coords": p.coord_source or "—",
            # Object columns for the detail panel (pickled by the cache).
            "conf_factors": rp.confidence.as_dict()["factors"] if rp.confidence else [],
            "lifecycle": [
                {"stage": st.stage, "title": st.title,
                 "value": round(st.value_unlocked_usd), "capital": round(st.capital_required_usd),
                 "timeline": st.timeline_months, "gate": st.milestone_gate}
                for st in (rp.lifecycle or [])
            ],
            "lat": p.lat,
            "lon": p.lon,
        })
    return pd.DataFrame(rows)


# --- Sidebar controls --------------------------------------------------------
st.sidebar.title("🛰️ LVE-LAP Controls")
st.sidebar.caption("Land Value Engine · Latent Arbitrage Protocol")

source_label = st.sidebar.radio(
    "Data source", ["Synthetic corridor", "Curated parcels (CSV/JSON)"],
    help="Curated = your own parcels (APN/address + asking price); coordinates "
         "are resolved from free public GIS.")
source = "file" if source_label.startswith("Curated") else "synthetic"

file_text, file_fmt = "", "csv"
if source == "file":
    up = st.sidebar.file_uploader("Upload parcels CSV/JSON", type=["csv", "json"])
    default_path = str(Path(__file__).resolve().parent / "data" / "sample_parcels.csv")
    if up is not None:
        file_text = up.getvalue().decode("utf-8")
        file_fmt = "json" if up.name.lower().endswith(".json") else "csv"
        st.sidebar.caption(f"Loaded {up.name}")
    else:
        path = st.sidebar.text_input("…or path to a file", value=default_path)
        p = Path(path)
        if p.exists():
            file_text = p.read_text(encoding="utf-8")
            file_fmt = "json" if p.suffix.lower() == ".json" else "csv"
        else:
            st.sidebar.warning("File not found — using none.")

dataset_size = st.sidebar.slider("Synthetic corridor size", 20, 500, 150, step=10,
                                 disabled=(source == "file"))
top_n = st.sidebar.slider("Top-N matrix", 10, 100, 50, step=10)
max_ppa = st.sidebar.slider("Max $/acre ceiling", 500, 5000, 2000, step=250)
min_mw = st.sidebar.slider("Min substation headroom (MW)", 0, 60, 10, step=5)
buffer_mi = st.sidebar.slider("Transmission buffer (miles)", 1.0, 6.0, 3.0, step=0.5)
states = st.sidebar.multiselect("States", ["NV", "AZ", "UT", "NM"],
                                default=["NV", "AZ", "UT", "NM"])
min_las = st.sidebar.slider("Min LAS filter", 0, 100, 0, step=5)
min_conf = st.sidebar.slider("Min confidence % filter", 0, 100, 0, step=5)
color_by = st.sidebar.radio("Color map by", ["LAS", "Confidence"], horizontal=True)

df, meta = _run_pipeline(source, dataset_size, float(max_ppa), float(min_mw),
                         float(buffer_mi), file_text, file_fmt)

# Post-pipeline display filters (cheap; not part of the cached pipeline run).
if not df.empty:
    df = df[(df["LAS"] >= min_las) & (df["Conf%"].fillna(0) >= min_conf)]
    # The state filter applies to the synthetic corridor; curated lists are shown
    # as-is (the user chose those parcels regardless of state).
    if source == "synthetic":
        df = df[df["State"].isin(states)]

# --- Header + KPIs -----------------------------------------------------------
st.title("Latent Arbitrage Matrix")
_src_note = ("curated parcels — coordinates resolved from public GIS"
             if source == "file" else "synthetic corridor near real substations")
st.caption(f"Source: {_src_note}. Modeled diagnostics — not appraisals or "
           "investment advice.")

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Ingested", meta["ingested"])
c2.metric("Passed gates", meta["survivors"])
c3.metric("Disqualified", meta["disqualified"])
c4.metric("Shown", len(df))
avg_conf = round(df["Conf%"].dropna().mean(), 1) if not df.empty and df["Conf%"].notna().any() else 0.0
c5.metric("Avg confidence", f"{avg_conf}%")
c6.metric("High-confidence", int((df["Conf%"].fillna(0) >= 75).sum()) if not df.empty else 0)

# Coordinate provenance summary (curated parcels lean on GIS resolution).
if not df.empty and "Coords" in df:
    prov = ", ".join(f"{n}× {k}" for k, n in df["Coords"].value_counts().items())
    st.caption(f"Coordinate source: {prov}")

if df.empty:
    if source == "file" and meta["ingested"] == 0:
        st.warning("No parcels loaded. Upload a CSV/JSON with an APN or address "
                   "and asking_price per row (see data/sample_parcels.csv).")
    else:
        st.warning("No parcels match the current filters. Loosen the thresholds "
                   "in the sidebar, or check coordinate resolution for curated rows.")
    st.stop()

# --- Map ---------------------------------------------------------------------
st.subheader(f"Corridor map — sized by LAS, colored by {color_by}")
try:
    import pydeck as pdk

    map_df = df.copy()
    if color_by == "Confidence":
        map_df["color"] = map_df["Conf%"].apply(_conf_color)
    else:
        map_df["color"] = map_df["LAS"].apply(_las_color)
    map_df["radius"] = (map_df["LAS"] * 60).clip(lower=1500)
    layer = pdk.Layer(
        "ScatterplotLayer", data=map_df,
        get_position="[lon, lat]", get_fill_color="color",
        get_radius="radius", pickable=True, opacity=0.7,
    )
    view = pdk.ViewState(latitude=float(map_df["lat"].mean()),
                         longitude=float(map_df["lon"].mean()), zoom=5)
    tooltip = {"text": "{Parcel}\nLAS {LAS} · conf {Conf%}% ({Conf})\n{Multiple}x · ${$/acre}/acre"}
    st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view,
                             tooltip=tooltip))
    st.caption("Green = high · amber = medium · grey/red = low. Toggle LAS vs "
               "Confidence in the sidebar.")
except ImportError:
    st.map(df[["lat", "lon"]])
    st.caption("Install `pydeck` for colored markers and tooltips.")

# --- Top-N matrix ------------------------------------------------------------
st.subheader(f"Top {top_n} anomalies")
top_df = df.sort_values("LAS", ascending=False).head(top_n)
st.dataframe(
    top_df.drop(columns=["lat", "lon", "conf_factors", "lifecycle", "flag_msgs"]),
    use_container_width=True, hide_index=True,
    column_config={
        "Asking": st.column_config.NumberColumn(format="$%d"),
        "HBU Value": st.column_config.NumberColumn(format="$%d"),
        "$/acre": st.column_config.NumberColumn(format="$%d"),
        "LAS": st.column_config.ProgressColumn(min_value=0, max_value=100,
                                               format="%.1f"),
        "Conf%": st.column_config.ProgressColumn("Conf%", min_value=0,
                                                 max_value=100, format="%.0f"),
        "Multiple": st.column_config.NumberColumn(format="%.2fx"),
    },
)

# --- Detail panel ------------------------------------------------------------
st.subheader("Parcel detail")
choice = st.selectbox("Select a parcel", top_df["Parcel"].tolist())
row = top_df[top_df["Parcel"] == choice].iloc[0]

for fl in row["flag_msgs"]:
    if fl["level"] == "warn":
        st.error(f"⚠ DATA WARNING — {fl['message']}")
    else:
        st.warning(f"⚑ CAUTION — {fl['message']}")

d1, d2 = st.columns(2)
with d1:
    st.markdown(f"**{row['Parcel']}** — {row['County']} County, {row['State']}")
    st.metric("Asking", f"${row['Asking']:,.0f}")
    st.metric("Modeled HBU Value", f"${row['HBU Value']:,.0f}")
    st.metric("Arbitrage Multiple", f"{row['Multiple']}x")
    st.metric("Confidence", f"{row['Conf%']}%  ({row['Conf']})")
with d2:
    st.markdown("**LAS component breakdown**")
    st.bar_chart(pd.DataFrame({
        "score": [row["Price"], row["DOM"], row["Infra"], row["Resource"]],
    }, index=["Price (30%)", "DOM (20%)", "Infra (40%)", "Resource (10%)"]))
    st.caption(f"Headroom {row['Headroom MW']:.0f} MW · "
               f"Water {row['Water AF']:.0f} AF · Total LAS {row['LAS']}")

# Confidence pick-apart
st.markdown(f"**Confidence — {row['Conf%']}% ({row['Conf']})** · by data provenance")
factors = row["conf_factors"]
if factors:
    fdf = pd.DataFrame(factors)[["label", "level", "score", "rationale"]]
    fdf.columns = ["Factor", "Provenance", "Score", "Why"]
    st.dataframe(
        fdf, use_container_width=True, hide_index=True,
        column_config={"Score": st.column_config.ProgressColumn(
            "Score", min_value=0, max_value=100, format="%d")},
    )

# Lifecycle optimization
stages = row["lifecycle"]
if stages:
    st.markdown("**Lifecycle optimization**")
    ldf = pd.DataFrame(stages)
    ldf = ldf.rename(columns={"stage": "#", "title": "Stage", "value": "Value unlocked",
                              "capital": "Capital", "timeline": "Timeline (mo)", "gate": "Escrow gate"})
    st.dataframe(
        ldf, use_container_width=True, hide_index=True,
        column_config={
            "Value unlocked": st.column_config.NumberColumn(format="$%d"),
            "Capital": st.column_config.NumberColumn(format="$%d"),
        },
    )
