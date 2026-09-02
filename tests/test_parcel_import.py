"""Tests for curated-parcel import (CSV/JSON)."""

from __future__ import annotations

import json

from parcel_import import parcels_from_file, record_to_parcel


def test_record_requires_price():
    assert record_to_parcel({"apn": "123"}) is None


def test_record_requires_location_handle():
    # price but no apn/address/coords -> unusable
    assert record_to_parcel({"asking_price": "50000"}) is None


def test_record_from_apn():
    p = record_to_parcel({"apn": "13923399049", "state": "NV", "county": "Clark",
                          "asking_price": "60000", "acres": "5"})
    assert p is not None
    assert p.apn == "13923399049" and p.source == "import"
    assert p.asking_price == 60000.0 and p.coord_source is None  # coords resolved later


def test_record_from_address():
    p = record_to_parcel({"address": "123 Main St, Las Vegas, NV",
                          "asking_price": "40000", "acres": "2"})
    assert p is not None and p.street_address.startswith("123 Main")


def test_record_with_coords_marks_listing():
    p = record_to_parcel({"latitude": "37.1", "longitude": "-117.2",
                          "asking_price": "40000", "acres": "10"})
    assert p.coord_source == "listing"


def test_flags_parse():
    p = record_to_parcel({"apn": "1", "asking_price": "1", "geothermal": "true",
                          "mineral": "yes"})
    assert p.geothermal_signature and p.mineral_claims


def test_parcels_from_csv(tmp_path):
    csv = ("apn,state,asking_price,acres\n"
           "13923399049,NV,60000,5\n"
           "15867,NV,95000,37.7\n")
    f = tmp_path / "p.csv"
    f.write_text(csv)
    parcels = parcels_from_file(f)
    assert len(parcels) == 2
    assert {p.apn for p in parcels} == {"13923399049", "15867"}


def test_parcels_from_json(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"parcels": [
        {"apn": "1", "state": "NV", "asking_price": 50000, "acres": 10}]}))
    parcels = parcels_from_file(f)
    assert len(parcels) == 1 and parcels[0].apn == "1"
