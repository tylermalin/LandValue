"""
valuation.py — Highest-and-Best-Use (HBU) modeling.

Translates a parcel's latent attributes into a modeled HBU value so the dossier
can express the arbitrage as a concrete dollar figure and multiple, not just a
score. These coefficients are deliberately conservative, transparent defaults —
tune them per corridor as real comps accrue.

HBU value = energy/compute node value (per MW of interconnect headroom)
          + water-banking value (per acre-foot of appurtenant rights)
          + resource optionality premium (geothermal / minerals)
          + raw acreage floor.
"""

from __future__ import annotations

from dataclasses import dataclass

from parcels import Parcel

# --- Model coefficients (research-grounded; see methodology for citations) ----
# Firm, interconnection-ready power traded ~$465k/MW in a hot market (Stream's
# 66-acre / 164 MW Young County TX deal, Q1'25). Our headroom is an UNQUEUED
# voltage proxy, so we discount ~2/3 of that firm-queue premium.
VALUE_PER_MW_HEADROOM = 150_000.0     # $/MW of (unqueued, proxied) headroom
# Permanent transferable water rights span ~$609/AF (environmental purchases)
# to $10k+/AF (scarce municipal transfers); conservative mid for an appurtenant
# Western-basin right.
VALUE_PER_ACRE_FOOT = 2_500.0        # $/AF of appurtenant water rights
GEOTHERMAL_PREMIUM = 200_000.0       # optionality placeholder (pending survey)
MINERAL_PREMIUM = 100_000.0          # optionality placeholder (pending title check)
# Remote Western desert raw land: rural comps run ~$1,831/acre (TX ranch) down
# to a few hundred $/acre for BLM-adjacent desert; conservative floor.
RAW_ACRE_FLOOR = 750.0               # $/acre baseline utility floor
# Water-banking / access optionality for a parcel adjacent to surface water,
# scaled linearly to 0 at the proximity threshold. NOT a legal water right.
SURFACE_WATER_PREMIUM = 100_000.0


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


@dataclass
class Valuation:
    asking_price: float
    modeled_hbu_value: float
    energy_component: float
    water_component: float
    resource_component: float
    acreage_component: float
    surface_water_component: float = 0.0

    @property
    def arbitrage_multiple(self) -> float:
        if self.asking_price <= 0:
            return 0.0
        return round(self.modeled_hbu_value / self.asking_price, 2)

    @property
    def arbitrage_gain(self) -> float:
        return self.modeled_hbu_value - self.asking_price


def model_hbu(parcel: Parcel, surface_water_bonus_miles: float = 5.0) -> Valuation:
    energy = parcel.nearest_substation_headroom_mw * VALUE_PER_MW_HEADROOM
    water = parcel.water_rights_acre_feet * VALUE_PER_ACRE_FOOT
    resource = 0.0
    if parcel.geothermal_signature:
        resource += GEOTHERMAL_PREMIUM
    if parcel.mineral_claims:
        resource += MINERAL_PREMIUM
    acreage = parcel.acres * RAW_ACRE_FLOOR

    # Surface-water proximity premium (water banking / access optionality).
    surface_water = 0.0
    dist = parcel.surface_water_distance_miles
    if surface_water_bonus_miles > 0 and dist is not None and dist < surface_water_bonus_miles:
        surface_water = SURFACE_WATER_PREMIUM * _clamp(1.0 - dist / surface_water_bonus_miles)

    total = energy + water + resource + acreage + surface_water
    return Valuation(
        asking_price=parcel.asking_price,
        modeled_hbu_value=total,
        energy_component=energy,
        water_component=water,
        resource_component=resource,
        acreage_component=acreage,
        surface_water_component=surface_water,
    )
