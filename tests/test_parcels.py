"""Tests for the Parcel domain model and ingestion normalization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from parcels import (
    Parcel, _row_to_parcel, build_run_input, ingest, mock_parcels,
    synthetic_corridor,
)


def test_price_per_acre():
    p = Parcel("P", "C", "NV", 0, 0, acres=100, asking_price=250_000)
    assert p.price_per_acre == 2500.0


def test_price_per_acre_zero_acres_is_safe():
    p = Parcel("P", "C", "NV", 0, 0, acres=0, asking_price=250_000)
    assert p.price_per_acre == 0.0


def test_mock_parcels_shape():
    parcels = mock_parcels()
    assert len(parcels) == 5
    assert all(isinstance(p, Parcel) for p in parcels)
    # One parcel is deliberately landlocked for hard-gate coverage.
    assert any(p.landlocked for p in parcels)


def test_ingest_mock_mode_uses_mock_parcels(cfg):
    assert cfg.run_mode == "mock"
    assert len(ingest(cfg)) == 5


def test_row_to_parcel_maps_aliased_fields():
    row = {
        "id": "X1",
        "county": "Clark",
        "stateCode": "nv",
        "latitude": "36.1",
        "longitude": "-115.2",
        "lotSizeAcres": "40",
        "listPrice": "80000",
        "dom": "120",
        "remarks": "as-is",
        "waterRights": "10",
    }
    p = _row_to_parcel(row)
    assert p is not None
    assert p.parcel_id == "X1"
    assert p.state == "NV"
    assert p.acres == 40.0
    assert p.asking_price == 80_000.0
    assert p.days_on_market == 120
    assert p.water_rights_acre_feet == 10.0


def test_row_to_parcel_rejects_missing_price_or_acres():
    assert _row_to_parcel({"acres": "40", "latitude": "36", "longitude": "-115"}) is None
    assert _row_to_parcel({"price": "1000", "latitude": "36", "longitude": "-115"}) is None


def test_row_to_parcel_rejects_missing_coordinates():
    assert _row_to_parcel({"acres": "40", "price": "1000"}) is None


def test_row_to_parcel_survives_garbage_numeric_values():
    row = {"acres": "not-a-number", "price": "80000",
           "latitude": "36", "longitude": "-115"}
    # acres unparseable -> defaults to 0 -> rejected, but must not raise.
    assert _row_to_parcel(row) is None


def test_synthetic_corridor_is_deterministic():
    a = synthetic_corridor(n=50, seed=7)
    b = synthetic_corridor(n=50, seed=7)
    assert [p.parcel_id for p in a] == [p.parcel_id for p in b]
    assert [p.asking_price for p in a] == [p.asking_price for p in b]


def test_synthetic_corridor_size_and_states():
    parcels = synthetic_corridor(n=200)
    assert len(parcels) == 200
    assert {p.state for p in parcels} <= {"NV", "AZ", "UT", "NM"}
    assert all(p.acres > 0 and p.asking_price > 0 for p in parcels)


# --- live-mode wiring (schema-correct, no network) ---------------------------
@dataclass
class _Cfg:
    target_zips: List[str] = field(default_factory=lambda: ["89013", "86021"])


def test_build_run_input_matches_actor_schema():
    ri = build_run_input(_Cfg())
    assert ri["zip_codes"] == ["89013", "86021"]     # actor's required field name
    assert ri["listing_type"] == "for_sale"
    assert ri["proxyConfiguration"]["useApifyProxy"] is True
    # Must NOT carry the old wrong keys.
    assert "zipCodes" not in ri and "states" not in ri


def test_row_to_parcel_maps_landdotcom_fields():
    # Real Land.com Scraper output field names.
    row = {
        "canonicalUrl": "https://www.land.com/property/abc-123/",
        "county": "Esmeralda", "state": "NV", "zip": "89013",
        "latitude": 37.78, "longitude": -117.23,
        "acres": 640, "price": 920000,
        "pricePerAcre": 1437, "description": "raw land, as-is",
    }
    p = _row_to_parcel(row)
    assert p is not None
    assert p.source == "apify"
    assert p.listing_url == "https://www.land.com/property/abc-123/"
    # No stable id in output -> falls back to the canonical URL.
    assert p.parcel_id == "https://www.land.com/property/abc-123/"
    assert p.state == "NV" and p.acres == 640.0 and p.asking_price == 920000.0


def test_row_to_parcel_id_composite_fallback_without_url():
    row = {"county": "Nye", "zip": "89049", "state": "NV",
           "latitude": 38.0, "longitude": -116.9, "acres": 100, "price": 50000}
    p = _row_to_parcel(row)
    assert p.parcel_id == "Nye-89049-50000"
