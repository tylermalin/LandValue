"""Tests for geospatial enrichment and hard-gate filtering."""

from __future__ import annotations

import copy

import pytest

import json

from parcels import Parcel, mock_parcels
from spatial import (
    SpatialContext,
    _haversine_miles,
    _point_to_segment_miles,
    _polygon_centroid_radius,
    enrich_and_filter,
)


@pytest.fixture
def ctx(cfg) -> SpatialContext:
    return SpatialContext(cfg.transmission_lines_path, cfg.substations_path)


# --- distance primitives -----------------------------------------------------
def test_haversine_zero_distance():
    assert _haversine_miles(37.0, -117.0, 37.0, -117.0) == pytest.approx(0.0)


def test_haversine_one_degree_lat_is_about_69_miles():
    d = _haversine_miles(37.0, -117.0, 38.0, -117.0)
    assert d == pytest.approx(69.0, abs=0.5)


def test_point_to_segment_endpoint():
    # Point coincident with segment endpoint -> ~0 distance.
    d = _point_to_segment_miles((-117.0, 37.0), (-117.0, 37.0), (-117.0, 37.1))
    assert d == pytest.approx(0.0, abs=1e-6)


# --- reference data loading --------------------------------------------------
def test_low_voltage_lines_are_excluded(ctx):
    # The 34 kV distribution line must be filtered out (< 69 kV threshold).
    # 4 sample HV lines remain, each with 3 vertices.
    assert len(ctx.lines) == 4


def test_substations_loaded_with_headroom(ctx):
    headrooms = sorted(h for _, _, h, _, _ in ctx.substations)
    assert 45.0 in headrooms and 60.0 in headrooms


def test_nearest_substation_returns_headroom(ctx):
    headroom, name, estimated = ctx.nearest_substation(37.782, -117.235)
    assert headroom == 45.0
    assert "Esmeralda" in name
    assert estimated is False  # sample data has no estimated flag


# --- gating ------------------------------------------------------------------
def test_clean_parcel_passes_all_gates(cfg, ctx, base_parcel):
    survivors = enrich_and_filter([base_parcel], ctx, cfg)
    assert len(survivors) == 1
    assert base_parcel.passes_spatial_gate
    assert base_parcel.transmission_distance_miles is not None
    assert base_parcel.nearest_substation_headroom_mw == 45.0


def test_landlocked_parcel_is_hard_gated(cfg, ctx, base_parcel):
    base_parcel.landlocked = True
    survivors = enrich_and_filter([base_parcel], ctx, cfg)
    assert survivors == []
    assert "Ingress hard-gate" in base_parcel.disqualification_reason


def test_missing_easement_is_hard_gated(cfg, ctx, base_parcel):
    base_parcel.has_legal_easement = False
    survivors = enrich_and_filter([base_parcel], ctx, cfg)
    assert survivors == []
    assert "easement" in base_parcel.disqualification_reason.lower()


def test_price_ceiling_gate(cfg, ctx, base_parcel):
    base_parcel.asking_price = 5_000_000.0  # ~$7,812/acre > $2k ceiling
    survivors = enrich_and_filter([base_parcel], ctx, cfg)
    assert survivors == []
    assert "acre" in base_parcel.disqualification_reason


def test_transmission_buffer_gate(cfg, ctx, base_parcel):
    # Move far from any transmission line.
    base_parcel.lat, base_parcel.lon = 40.0, -110.0
    survivors = enrich_and_filter([base_parcel], ctx, cfg)
    assert survivors == []
    assert "buffer" in base_parcel.disqualification_reason


def test_substation_headroom_gate(cfg, ctx, base_parcel):
    # Put the parcel near the low-headroom Nye Distribution substation (3 MW),
    # but on an HV line so the buffer gate passes first.
    cfg_low = cfg
    # Nye area is only served by a 34kV line (excluded), so simulate by raising
    # the required headroom above every substation instead.
    object.__setattr__(cfg_low, "min_substation_headroom_mw", 999.0)
    survivors = enrich_and_filter([base_parcel], ctx, cfg_low)
    assert survivors == []
    assert "headroom" in base_parcel.disqualification_reason.lower()


def test_full_mock_corridor_gate_counts(cfg, ctx):
    parcels = mock_parcels()
    survivors = enrich_and_filter(parcels, ctx, cfg)
    # Of 5 mock parcels: UT (over price ceiling) and NV-NYE (landlocked) drop.
    survivor_ids = {p.parcel_id for p in survivors}
    assert survivor_ids == {"NV-ESM-0417", "AZ-MOH-1188", "NM-LUN-2054"}


def test_enrich_does_not_mutate_shared_reference_data(cfg, ctx):
    before = copy.deepcopy(ctx.substations)
    enrich_and_filter(mock_parcels(), ctx, cfg)
    assert ctx.substations == before


# --- hydrography / surface-water proximity -----------------------------------
def _hydro_fixture(tmp_path):
    """A single ~1-mile-square waterbody centered near (37.782, -117.235)."""
    fc = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"GNIS_NAME": "Test Lake"},
        "geometry": {"type": "Polygon", "coordinates": [[
            [-117.24, 37.78], [-117.23, 37.78],
            [-117.23, 37.79], [-117.24, 37.79], [-117.24, 37.78],
        ]]},
    }]}
    p = tmp_path / "hydro.geojson"
    p.write_text(json.dumps(fc))
    return p


def test_polygon_centroid_radius_basic():
    geom = {"type": "Polygon", "coordinates": [[
        [-117.24, 37.78], [-117.23, 37.78], [-117.23, 37.79],
        [-117.24, 37.79], [-117.24, 37.78],
    ]]}
    clon, clat, radius = _polygon_centroid_radius(geom)
    assert -117.24 <= clon <= -117.23 and 37.78 <= clat <= 37.79
    assert radius > 0


def test_polygon_centroid_radius_ignores_non_polygon():
    assert _polygon_centroid_radius({"type": "Point", "coordinates": [0, 0]}) is None


def test_nearest_water_distance_none_without_hydrography(cfg):
    ctx = SpatialContext(cfg.transmission_lines_path, cfg.substations_path)
    assert ctx.nearest_water_distance_miles(37.782, -117.235) is None


def test_nearest_water_distance_small_when_adjacent(cfg, tmp_path):
    ctx = SpatialContext(
        cfg.transmission_lines_path, cfg.substations_path, _hydro_fixture(tmp_path)
    )
    d = ctx.nearest_water_distance_miles(37.785, -117.235)
    assert d is not None and d < 2.0


def test_enrich_sets_surface_water_distance(cfg, tmp_path, base_parcel):
    ctx = SpatialContext(
        cfg.transmission_lines_path, cfg.substations_path, _hydro_fixture(tmp_path)
    )
    enrich_and_filter([base_parcel], ctx, cfg)
    assert base_parcel.surface_water_distance_miles is not None
