"""Tests for HBU valuation modeling."""

from __future__ import annotations

import pytest

from parcels import Parcel
from valuation import (
    GEOTHERMAL_PREMIUM,
    MINERAL_PREMIUM,
    RAW_ACRE_FLOOR,
    VALUE_PER_ACRE_FOOT,
    VALUE_PER_MW_HEADROOM,
    model_hbu,
)


def test_components_sum_to_total(base_parcel):
    base_parcel.nearest_substation_headroom_mw = 45.0
    v = model_hbu(base_parcel)
    total = (v.energy_component + v.water_component
             + v.resource_component + v.acreage_component)
    assert v.modeled_hbu_value == pytest.approx(total)


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
