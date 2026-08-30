"""Tests for confidence scoring and its per-factor decomposition."""

from __future__ import annotations

from confidence import Provenance, score_confidence
from parcels import Parcel


def _p(**kw) -> Parcel:
    base = dict(parcel_id="P", county="C", state="NV", lat=37.0, lon=-117.0,
                acres=100, asking_price=50_000)
    base.update(kw)
    return Parcel(**base)


def _factor(bd, key):
    return next(f for f in bd.factors if f.key == key)


def test_total_pct_in_range():
    bd = score_confidence(_p())
    assert 0.0 <= bd.total_pct <= 100.0


def test_real_listing_beats_synthetic():
    real = _p(source="apify", listing_url="https://mls.example/123",
              days_on_market=120)
    syn = _p(source="synthetic")
    assert score_confidence(real).total_pct > score_confidence(syn).total_pct


def test_sourcing_levels():
    assert _factor(score_confidence(_p(source="apify", listing_url="http://x")),
                   "sourcing").level == Provenance.MEASURED
    assert _factor(score_confidence(_p(source="synthetic", listing_url="http://x")),
                   "sourcing").level == Provenance.ASSUMED
    assert _factor(score_confidence(_p(source="mock")),
                   "sourcing").level == Provenance.MISSING


def test_estimated_headroom_is_flagged():
    est = _p(nearest_substation_headroom_mw=40, headroom_is_estimated=True)
    meas = _p(nearest_substation_headroom_mw=40, headroom_is_estimated=False)
    assert _factor(score_confidence(est), "headroom").level == Provenance.ESTIMATED
    assert _factor(score_confidence(meas), "headroom").level == Provenance.MEASURED
    assert score_confidence(meas).total_pct > score_confidence(est).total_pct


def test_water_rights_db_vs_scraped():
    db = _p(water_rights_acre_feet=180, water_right_status="certificated")
    scraped = _p(water_rights_acre_feet=180)  # no DB status
    assert _factor(score_confidence(db), "water_rights").level == Provenance.MEASURED
    assert _factor(score_confidence(scraped), "water_rights").level == Provenance.ESTIMATED


def test_transmission_measured_when_enriched():
    p = _p(transmission_distance_miles=0.5)
    assert _factor(score_confidence(p), "transmission").level == Provenance.MEASURED
    assert _factor(score_confidence(_p()), "transmission").level == Provenance.MISSING


def test_label_thresholds():
    # A fully-measured, real parcel should land High.
    strong = _p(source="apify", listing_url="http://x", days_on_market=90,
                transmission_distance_miles=0.3, nearest_substation_headroom_mw=50,
                headroom_is_estimated=False, water_rights_acre_feet=100,
                water_right_status="perfected", geothermal_signature=True,
                surface_water_distance_miles=1.0)
    assert score_confidence(strong).label == "High"
    # A bare synthetic parcel should land Low.
    assert score_confidence(_p(source="synthetic")).label == "Low"


def test_limiting_factors_sorted_worst_first():
    bd = score_confidence(_p(source="synthetic"))
    scores = [f.score for f in bd.limiting_factors]
    assert scores == sorted(scores)


def test_as_dict_shape():
    d = score_confidence(_p()).as_dict()
    assert set(d) == {"total_pct", "label", "factors"}
    assert all({"key", "label", "level", "score", "rationale", "weight"} <= set(f)
               for f in d["factors"])
