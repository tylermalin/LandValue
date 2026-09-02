"""Tests for coordinate resolution from public GIS (county parcels + Census)."""

from __future__ import annotations

import os

import pytest

import parcel_gis as pg
from parcels import Parcel


def _p(**kw) -> Parcel:
    base = dict(parcel_id="P", county="Clark", state="NV", lat=0.0, lon=0.0,
                acres=40, asking_price=250_000)
    base.update(kw)
    return Parcel(**base)


# --- APN -> parcel centroid --------------------------------------------------
def test_parcel_centroid_by_apn(monkeypatch):
    def fake(url, params, timeout=40):
        return {"features": [{"geometry": {"rings": [[
            [-115.14, 36.19], [-115.13, 36.19],
            [-115.13, 36.20], [-115.14, 36.20], [-115.14, 36.19]]]}}]}
    monkeypatch.setattr(pg, "_http_json", fake)
    c = pg.parcel_centroid_by_apn("NV", "13923399049")
    assert c is not None
    lat, lon = c
    assert 36.19 <= lat <= 36.20 and -115.14 <= lon <= -115.13


def test_parcel_centroid_unknown_state(monkeypatch):
    monkeypatch.setattr(pg, "_http_json", lambda *a, **k: {"features": []})
    assert pg.parcel_centroid_by_apn("ZZ", "123") is None


def test_parcel_centroid_no_match(monkeypatch):
    monkeypatch.setattr(pg, "_http_json", lambda *a, **k: {"features": []})
    assert pg.parcel_centroid_by_apn("NV", "does-not-exist") is None


# --- address -> lat/lon (Census) ---------------------------------------------
def test_geocode_address(monkeypatch):
    def fake(url, params, timeout=40):
        return {"result": {"addressMatches": [
            {"coordinates": {"x": -115.135, "y": 36.194}}]}}
    monkeypatch.setattr(pg, "_http_json", fake)
    c = pg.geocode_address("123 Main St, Las Vegas, NV 89101")
    assert c == (36.194, -115.135)


def test_geocode_empty_address():
    assert pg.geocode_address("") is None


# --- resolve / enrich --------------------------------------------------------
def test_resolve_prefers_apn(monkeypatch):
    monkeypatch.setattr(pg, "parcel_centroid_by_apn", lambda s, a: (36.19, -115.13))
    monkeypatch.setattr(pg, "geocode_address", lambda a: (0.0, 0.0))
    p = _p(apn="13923399049")
    assert pg.resolve_coordinates(p) == "county-apn"
    assert p.lat == 36.19 and p.coord_source == "county-apn"


def test_resolve_falls_back_to_geocode(monkeypatch):
    monkeypatch.setattr(pg, "parcel_centroid_by_apn", lambda s, a: None)
    monkeypatch.setattr(pg, "geocode_address", lambda a: (36.2, -115.1))
    p = _p(apn=None, street_address="123 Main St, Las Vegas, NV 89101")
    assert pg.resolve_coordinates(p) == "geocode"
    assert p.coord_source == "geocode"


def test_resolve_noop_when_already_has_coords():
    p = _p(lat=37.0, lon=-117.0)
    assert pg.resolve_coordinates(p) == "listing"


def test_resolve_returns_none_when_nothing_to_use(monkeypatch):
    monkeypatch.setattr(pg, "parcel_centroid_by_apn", lambda s, a: None)
    p = _p(apn=None, street_address=None)
    assert pg.resolve_coordinates(p) is None


def test_enrich_counts(monkeypatch):
    monkeypatch.setattr(pg, "parcel_centroid_by_apn", lambda s, a: (36.1, -115.1))
    parcels = [_p(apn="1"), _p(apn="2"), _p(lat=37.0, lon=-117.0)]
    assert pg.enrich_coordinates(parcels) == 2
    assert all(p.lat and p.lon for p in parcels)


# --- opt-in live smoke test --------------------------------------------------
@pytest.mark.skipif(os.getenv("LVE_LIVE_GIS") != "1",
                    reason="set LVE_LIVE_GIS=1 to hit live NV parcel service")
def test_live_nv_apn_lookup():
    c = pg.parcel_centroid_by_apn("NV", "13923399049")
    assert c is not None
    lat, lon = c
    assert 35 < lat < 42 and -120 < lon < -114  # within Nevada
