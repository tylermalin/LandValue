"""Tests for the Latent Arbitrage Score (LAS) engine."""

from __future__ import annotations

import pytest

from parcels import Parcel
from scoring import WEIGHTS, ScoreBreakdown, score_parcel

SCORE_KW = dict(baseline_per_acre=6000.0, dom_threshold=90, min_headroom_mw=10.0)


def _enrich(parcel: Parcel, *, dist=0.5, headroom=45.0) -> Parcel:
    parcel.transmission_distance_miles = dist
    parcel.nearest_substation_headroom_mw = headroom
    return parcel


def test_weights_sum_to_one():
    assert pytest.approx(sum(WEIGHTS.values()), abs=1e-9) == 1.0


def test_total_is_zero_to_one_hundred(base_parcel):
    b = score_parcel(_enrich(base_parcel), **SCORE_KW)
    assert 0.0 <= b.total <= 100.0


def test_strong_parcel_scores_high(base_parcel):
    b = score_parcel(_enrich(base_parcel), **SCORE_KW)
    # Deep discount + stale listing + big headroom + full optionality.
    assert b.total > 80.0


def test_price_arbitrage_rewards_deep_discount(base_parcel):
    # $1,437/acre vs $6,000 baseline -> ~76% discount.
    b = score_parcel(_enrich(base_parcel), **SCORE_KW)
    assert b.price_arbitrage > 0.7


def test_price_arbitrage_zero_when_above_baseline():
    p = _enrich(Parcel("P", "C", "NV", 37.782, -117.235, acres=10, asking_price=100_000))
    # $10,000/acre > $6,000 baseline -> no arbitrage.
    b = score_parcel(p, **SCORE_KW)
    assert b.price_arbitrage == 0.0


def test_lazy_listing_markers_boost_dom_component():
    stale = _enrich(Parcel("A", "C", "NV", 37.782, -117.235, 100, 50_000,
                           days_on_market=200,
                           listing_description="as-is, must sell, cash only"))
    plain = _enrich(Parcel("B", "C", "NV", 37.782, -117.235, 100, 50_000,
                           days_on_market=200,
                           listing_description="Beautifully maintained parcel"))
    a = score_parcel(stale, **SCORE_KW)
    b = score_parcel(plain, **SCORE_KW)
    assert a.days_on_market > b.days_on_market


def test_infrastructure_rewards_headroom_and_proximity():
    near_big = _enrich(Parcel("A", "C", "NV", 37.782, -117.235, 100, 50_000),
                       dist=0.2, headroom=50.0)
    far_small = _enrich(Parcel("B", "C", "NV", 37.782, -117.235, 100, 50_000),
                        dist=2.9, headroom=10.0)
    a = score_parcel(near_big, **SCORE_KW)
    b = score_parcel(far_small, **SCORE_KW)
    assert a.infrastructure_headroom > b.infrastructure_headroom


def test_resource_optionality_components():
    none = _enrich(Parcel("A", "C", "NV", 37.782, -117.235, 100, 50_000))
    full = _enrich(Parcel("B", "C", "NV", 37.782, -117.235, 100, 50_000,
                          water_rights_acre_feet=100, geothermal_signature=True,
                          mineral_claims=True))
    a = score_parcel(none, **SCORE_KW)
    b = score_parcel(full, **SCORE_KW)
    assert a.resource_optionality == 0.0
    assert b.resource_optionality == pytest.approx(1.0)  # 0.5+0.3+0.3 clamped to 1.0


def test_surface_water_proximity_boosts_resource_score():
    dry = _enrich(Parcel("A", "C", "NV", 37.782, -117.235, 100, 50_000))
    wet = _enrich(Parcel("B", "C", "NV", 37.782, -117.235, 100, 50_000))
    wet.surface_water_distance_miles = 1.0   # close to water
    a = score_parcel(dry, **SCORE_KW)
    b = score_parcel(wet, **SCORE_KW)
    assert b.resource_optionality > a.resource_optionality


def test_surface_water_bonus_zero_beyond_threshold():
    far = _enrich(Parcel("A", "C", "NV", 37.782, -117.235, 100, 50_000))
    far.surface_water_distance_miles = 99.0  # well beyond the 5-mi threshold
    b = score_parcel(far, **SCORE_KW)
    assert b.resource_optionality == 0.0


def test_surface_water_bonus_disabled_when_threshold_zero():
    wet = _enrich(Parcel("A", "C", "NV", 37.782, -117.235, 100, 50_000))
    wet.surface_water_distance_miles = 0.5
    b = score_parcel(wet, **{**SCORE_KW, "surface_water_bonus_miles": 0.0})
    assert b.resource_optionality == 0.0


def test_breakdown_as_dict_scales_to_100(base_parcel):
    b = score_parcel(_enrich(base_parcel), **SCORE_KW)
    d = b.as_dict()
    assert set(d) == {"price_arbitrage", "days_on_market",
                      "infrastructure_headroom", "resource_optionality", "total"}
    assert d["total"] == b.total


def test_missing_infrastructure_data_scores_zero():
    p = Parcel("P", "C", "NV", 37.782, -117.235, 100, 50_000)
    p.transmission_distance_miles = None
    p.nearest_substation_headroom_mw = 0.0
    b = score_parcel(p, **SCORE_KW)
    assert b.infrastructure_headroom == 0.0


def test_empty_breakdown_totals_zero():
    assert ScoreBreakdown().total == 0.0
