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

# --- Model coefficients ------------------------------------------------------
VALUE_PER_MW_HEADROOM = 85_000.0      # $/MW of usable interconnect headroom
VALUE_PER_ACRE_FOOT = 4_500.0        # $/AF of appurtenant water rights
GEOTHERMAL_PREMIUM = 250_000.0       # flat premium for a geothermal signature
MINERAL_PREMIUM = 120_000.0          # flat premium for mineral/gold claims
RAW_ACRE_FLOOR = 1_500.0             # $/acre baseline utility floor


@dataclass
class Valuation:
    asking_price: float
    modeled_hbu_value: float
    energy_component: float
    water_component: float
    resource_component: float
    acreage_component: float

    @property
    def arbitrage_multiple(self) -> float:
        if self.asking_price <= 0:
            return 0.0
        return round(self.modeled_hbu_value / self.asking_price, 2)

    @property
    def arbitrage_gain(self) -> float:
        return self.modeled_hbu_value - self.asking_price


def model_hbu(parcel: Parcel) -> Valuation:
    energy = parcel.nearest_substation_headroom_mw * VALUE_PER_MW_HEADROOM
    water = parcel.water_rights_acre_feet * VALUE_PER_ACRE_FOOT
    resource = 0.0
    if parcel.geothermal_signature:
        resource += GEOTHERMAL_PREMIUM
    if parcel.mineral_claims:
        resource += MINERAL_PREMIUM
    acreage = parcel.acres * RAW_ACRE_FLOOR

    total = energy + water + resource + acreage
    return Valuation(
        asking_price=parcel.asking_price,
        modeled_hbu_value=total,
        energy_component=energy,
        water_component=water,
        resource_component=resource,
        acreage_component=acreage,
    )
