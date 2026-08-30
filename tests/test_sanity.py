"""Tests for plausibility / sanity flags."""

from __future__ import annotations

from confidence import score_confidence
from parcels import Parcel
from sanity import sanity_flags
from scoring import score_parcel
from valuation import model_hbu

SCORE_KW = dict(baseline_per_acre=6000.0, dom_threshold=90, min_headroom_mw=10.0)


def _flags(parcel: Parcel):
    parcel.transmission_distance_miles = parcel.transmission_distance_miles or 0.5
    s = score_parcel(parcel, **SCORE_KW)
    v = model_hbu(parcel)
    c = score_confidence(parcel)
    return {f.code for f in sanity_flags(parcel, s, v, c)}


def _p(**kw) -> Parcel:
    base = dict(parcel_id="P", county="C", state="NV", lat=37.0, lon=-117.0,
                acres=100, asking_price=200_000, source="apify",
                listing_url="https://mls.example/1")
    base.update(kw)
    return Parcel(**base)


def test_clean_parcel_has_no_flags():
    p = _p(nearest_substation_headroom_mw=12, days_on_market=100)
    assert _flags(p) == set()


def test_implausible_multiple_flagged():
    # Tiny asking price vs huge headroom -> enormous multiple.
    p = _p(asking_price=5_000, acres=100, nearest_substation_headroom_mw=80,
           headroom_is_estimated=True)
    codes = _flags(p)
    assert "implausible_multiple" in codes


def test_implausible_price_per_acre_flagged():
    p = _p(asking_price=2_000, acres=100)  # $20/acre
    assert "implausible_price_per_acre" in _flags(p)


def test_proxy_dependent_value_flagged():
    # Value dominated by estimated headroom, but multiple kept modest.
    p = _p(asking_price=5_000_000, acres=100, nearest_substation_headroom_mw=60,
           headroom_is_estimated=True)
    assert "proxy_dependent_value" in _flags(p)


def test_no_proxy_flag_when_headroom_measured():
    p = _p(asking_price=5_000_000, acres=100, nearest_substation_headroom_mw=60,
           headroom_is_estimated=False)
    assert "proxy_dependent_value" not in _flags(p)


def test_high_multiple_low_confidence_caution():
    # Synthetic (low confidence), multiple in the 10-25x band.
    p = Parcel(parcel_id="P", county="C", state="NV", lat=37.0, lon=-117.0,
               acres=100, asking_price=120_000, source="synthetic",
               nearest_substation_headroom_mw=15, headroom_is_estimated=False)
    p.transmission_distance_miles = 0.5
    s = score_parcel(p, **SCORE_KW)
    v = model_hbu(p)
    c = score_confidence(p)
    codes = {f.code for f in sanity_flags(p, s, v, c)}
    # Either the high-multiple caution or (if >25x) the implausible warn fires.
    assert "high_multiple_low_confidence" in codes or "implausible_multiple" in codes


def test_no_source_document_flag_for_top_synthetic():
    p = Parcel(parcel_id="P", county="C", state="NV", lat=37.0, lon=-117.0,
               acres=640, asking_price=900_000, source="synthetic",
               days_on_market=300, water_rights_acre_feet=180,
               geothermal_signature=True, mineral_claims=True,
               nearest_substation_headroom_mw=45)
    p.transmission_distance_miles = 0.3
    s = score_parcel(p, **SCORE_KW)
    v = model_hbu(p)
    c = score_confidence(p)
    codes = {f.code for f in sanity_flags(p, s, v, c)}
    assert "no_source_document" in codes


def test_flag_levels_are_valid():
    p = _p(asking_price=5_000, acres=100, nearest_substation_headroom_mw=80,
           headroom_is_estimated=True)
    s = score_parcel(p, **SCORE_KW)
    v = model_hbu(p)
    c = score_confidence(p)
    for f in sanity_flags(p, s, v, c):
        assert f.level in ("warn", "caution")
        assert f.message
