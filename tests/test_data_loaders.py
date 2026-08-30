"""Tests for the real GIS loaders (Phase: HIFLD/USGS).

Network is mocked — these verify parsing, derivation, and pagination logic
without hitting live ArcGIS endpoints. A separate opt-in live smoke test lives
at the bottom (skipped unless LVE_LIVE_GIS=1).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import data_loaders as dl
from parcels import synthetic_corridor, synthetic_near_infrastructure


# --- headroom proxy ----------------------------------------------------------
def test_headroom_increases_with_voltage():
    assert dl.estimate_headroom_mw(500, 1) > dl.estimate_headroom_mw(69, 1)


def test_headroom_increases_with_degree():
    assert dl.estimate_headroom_mw(230, 5) > dl.estimate_headroom_mw(230, 1)


def test_headroom_degree_factor_capped():
    # Degree factor saturates at 2x, so a huge degree can't run away.
    hi = dl.estimate_headroom_mw(230, 100)
    base = dl.estimate_headroom_mw(230, 1)
    assert hi <= base * 2.0 + 1e-6


# --- bbox --------------------------------------------------------------------
def test_union_bbox_spans_states():
    b = dl.union_bbox(["NV", "AZ"])
    assert b[0] <= -114.8 and b[2] >= -114.0  # spans both

def test_union_bbox_unknown_state_raises():
    with pytest.raises(ValueError):
        dl.union_bbox(["ZZ"])


# --- substation derivation ---------------------------------------------------
def _lines_fc():
    return {"type": "FeatureCollection", "features": [
        {"properties": {"VOLTAGE": 230, "SUB_1": "ALPHA", "SUB_2": "BETA"},
         "geometry": {"type": "LineString",
                      "coordinates": [[-117.0, 37.0], [-117.5, 37.5]]}},
        {"properties": {"VOLTAGE": 500, "SUB_1": "BETA", "SUB_2": "GAMMA"},
         "geometry": {"type": "LineString",
                      "coordinates": [[-117.5, 37.5], [-118.0, 38.0]]}},
        {"properties": {"VOLTAGE": 120, "SUB_1": "TAP", "SUB_2": "UNKNOWN"},
         "geometry": {"type": "LineString",
                      "coordinates": [[-116.0, 36.0], [-116.1, 36.1]]}},
    ]}


def test_derive_substations_aggregates_by_name():
    fc = dl.derive_substations(_lines_fc())
    names = {f["properties"]["name"] for f in fc["features"]}
    # ALPHA, BETA, GAMMA — TAP and UNKNOWN are filtered out.
    assert names == {"ALPHA", "BETA", "GAMMA"}


def test_derive_substations_degree_and_voltage():
    fc = dl.derive_substations(_lines_fc())
    beta = next(f for f in fc["features"] if f["properties"]["name"] == "BETA")
    # BETA touches both lines -> degree 2, max voltage 500.
    assert beta["properties"]["line_degree"] == 2
    assert beta["properties"]["max_voltage_kv"] == 500
    assert beta["properties"]["headroom_estimated"] is True


# --- transmission fetch + pagination (mocked HTTP) ---------------------------
def test_fetch_transmission_normalizes_voltage(monkeypatch):
    fake = {"features": [
        {"properties": {"VOLTAGE": 345}, "geometry":
         {"type": "LineString", "coordinates": [[-117, 37], [-117.1, 37.1]]}},
    ]}
    monkeypatch.setattr(dl, "_http_get_json", lambda *a, **k: fake)
    fc = dl.fetch_transmission_lines((-118, 37, -116, 38))
    assert fc["features"][0]["properties"]["voltage_kv"] == 345


def test_pagination_stops_on_short_page(monkeypatch):
    # First call returns a full page (2000), second returns a short page -> stop.
    calls = {"n": 0}

    def fake(url, params, **k):
        calls["n"] += 1
        count = 2000 if calls["n"] == 1 else 3
        return {"features": [{"properties": {"VOLTAGE": 230},
                              "geometry": {"type": "LineString",
                                           "coordinates": [[-117, 37], [-117, 37.1]]}}
                             for _ in range(count)]}

    monkeypatch.setattr(dl, "_http_get_json", fake)
    fc = dl.fetch_transmission_lines((-118, 37, -116, 38))
    assert len(fc["features"]) == 2003
    assert calls["n"] == 2


# --- synthetic parcels near real infrastructure ------------------------------
def test_synthetic_near_infrastructure_places_parcels_near_subs(tmp_path):
    subs = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "HUB", "headroom_mw": 50},
         "geometry": {"type": "Point", "coordinates": [-117.5, 37.5]}},
    ]}
    p = tmp_path / "subs.geojson"
    p.write_text(json.dumps(subs))
    parcels = synthetic_near_infrastructure(p, n=20, jitter_deg=0.01)
    assert len(parcels) == 20
    for parcel in parcels:
        assert abs(parcel.lon - (-117.5)) <= 0.01
        assert abs(parcel.lat - 37.5) <= 0.01


def test_synthetic_near_infrastructure_falls_back_when_missing(tmp_path):
    parcels = synthetic_near_infrastructure(tmp_path / "nope.geojson", n=10)
    # Falls back to the fixed corridor generator.
    assert len(parcels) == 10
    assert parcels == synthetic_corridor(n=10)


# --- opt-in live smoke test --------------------------------------------------
@pytest.mark.skipif(os.getenv("LVE_LIVE_GIS") != "1",
                    reason="set LVE_LIVE_GIS=1 to hit live HIFLD endpoint")
def test_live_transmission_fetch_smoke():
    fc = dl.fetch_transmission_lines((-118.5, 37.0, -116.0, 39.5), max_records=50)
    assert fc["features"], "expected some real transmission lines in the NV bbox"
    assert all("voltage_kv" in f["properties"] for f in fc["features"])
