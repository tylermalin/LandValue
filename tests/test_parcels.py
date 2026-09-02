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
    search_urls: List[str] = field(default_factory=list)
    max_items: int = 25


def test_build_run_input_url_based_schema():
    ri = build_run_input(_Cfg(search_urls=["https://www.landwatch.com/x"]))
    assert ri["startUrls"] == ["https://www.landwatch.com/x"]  # actor's field name
    assert ri["maxItems"] == 25
    assert ri["proxy"]["apifyProxyGroups"] == ["RESIDENTIAL"]
    # Must NOT carry other actors' key names.
    assert "zip_codes" not in ri and "searchUrls" not in ri


def test_build_run_input_defaults_to_corridor_urls():
    ri = build_run_input(_Cfg())  # no SEARCH_URLS set
    assert ri["startUrls"]
    assert all("landwatch.com" in u for u in ri["startUrls"])


def test_row_to_parcel_maps_nested_landwatch_fields():
    # memo23/landwatch-scraper nests fields under propertyData / address.
    row = {
        "id": "LW-123", "url": "https://www.landwatch.com/property/123",
        "propertyData": {"latitude": 37.78, "longitude": -117.23,
                         "acres": 40, "price": 250000, "parcelId": "007-041-17"},
        "address": {"city": "Pahrump", "state": "NV", "zip": "89048",
                    "county": "Nye"},
        "descriptionText": "raw desert acreage",
    }
    p = _row_to_parcel(row)
    assert p is not None
    assert p.parcel_id == "LW-123"
    assert p.apn == "007-041-17"                    # from nested propertyData
    assert p.state == "NV" and p.county == "Nye"    # from nested address
    assert p.lat == 37.78 and p.lon == -117.23
    assert p.acres == 40.0 and p.asking_price == 250000.0
    assert p.listing_description == "raw desert acreage"


def test_row_to_parcel_id_composite_fallback_without_url():
    row = {"county": "Nye", "zip": "89049", "state": "NV",
           "latitude": 38.0, "longitude": -116.9, "acres": 100, "price": 50000}
    p = _row_to_parcel(row)
    assert p.parcel_id == "Nye-89049-50000"
