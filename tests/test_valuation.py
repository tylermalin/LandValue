"""Tests for HBU valuation modeling."""

from __future__ import annotations

import pytest

from parcels import Parcel
from valuation import (
    GEOTHERMAL_PREMIUM,
    MINERAL_PREMIUM,
    RAW_ACRE_FLOOR,
    SURFACE_WATER_PREMIUM,
    VALUE_PER_ACRE_FOOT,
    VALUE_PER_MW_HEADROOM,
    model_hbu,
)


def test_components_sum_to_total(base_parcel):
    base_parcel.nearest_substation_headroom_mw = 45.0
    v = model_hbu(base_parcel)
    total = (v.energy_component + v.water_component + v.resource_component
             + v.acreage_component + v.surface_water_component)
    assert v.modeled_hbu_value == pytest.approx(total)


def test_surface_water_premium_scales_with_proximity(base_parcel):
    base_parcel.surface_water_distance_miles = 0.0  # adjacent -> full premium
    v = model_hbu(base_parcel, surface_water_bonus_miles=5.0)
    assert v.surface_water_component == pytest.approx(SURFACE_WATER_PREMIUM)


def test_surface_water_premium_zero_beyond_threshold(base_parcel):
    base_parcel.surface_water_distance_miles = 10.0
    v = model_hbu(base_parcel, surface_water_bonus_miles=5.0)
    assert v.surface_water_component == 0.0


def test_surface_water_premium_disabled(base_parcel):
    base_parcel.surface_water_distance_miles = 1.0
    v = model_hbu(base_parcel, surface_water_bonus_miles=0.0)
    assert v.surface_water_component == 0.0


def test_surface_water_absent_is_zero(base_parcel):
    assert base_parcel.surface_water_distance_miles is None
    v = model_hbu(base_parcel)
    assert v.surface_water_component == 0.0


def test_energy_component_scales_with_headroom():
    p = Parcel("P", "C", "NV", 0, 0, acres=1, asking_price=1)
    p.nearest_substation_headroom_mw = 20.0
    v = model_hbu(p)
    assert v.energy_component == pytest.approx(20.0 * VALUE_PER_MW_HEADROOM)


def test_water_component_scales_with_acre_feet():
    p = Parcel("P", "C", "NV", 0, 0, acres=1, asking_price=1, water_rights_acre_feet=50)
    v = model_hbu(p)
    assert v.water_component == pytest.approx(50 * VALUE_PER_ACRE_FOOT)


def test_resource_premiums_are_additive():
    p = Parcel("P", "C", "NV", 0, 0, acres=1, asking_price=1,
               geothermal_signature=True, mineral_claims=True)
    v = model_hbu(p)
    assert v.resource_component == pytest.approx(GEOTHERMAL_PREMIUM + MINERAL_PREMIUM)


def test_acreage_floor():
    p = Parcel("P", "C", "NV", 0, 0, acres=100, asking_price=1)
    v = model_hbu(p)
    assert v.acreage_component == pytest.approx(100 * RAW_ACRE_FLOOR)


def test_arbitrage_multiple_and_gain():
    p = Parcel("P", "C", "NV", 0, 0, acres=100, asking_price=100_000)
    p.nearest_substation_headroom_mw = 10.0  # energy alone = 850k
    v = model_hbu(p)
    assert v.arbitrage_multiple == round(v.modeled_hbu_value / 100_000, 2)
    assert v.arbitrage_gain == pytest.approx(v.modeled_hbu_value - 100_000)


def test_arbitrage_multiple_zero_when_no_asking_price():
    p = Parcel("P", "C", "NV", 0, 0, acres=1, asking_price=0)
    v = model_hbu(p)
    assert v.arbitrage_multiple == 0.0
