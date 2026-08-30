"""
lifecycle.py — Full lifecycle optimization plan for a parcel.

Turns the HBU valuation into a stage-by-stage path from raw land to realized
value, aligned 1:1 with the roadmap and the on-chain MilestoneEscrow gates.
Each stage states:

  * what value it unlocks (mapped to specific HBU components),
  * the modeled capital it requires,
  * a timeline band,
  * the key risks, and
  * the escrow milestone that gates its capital drawdown.

Capital figures are MODELED estimates from the transparent coefficients below,
not quotes. They exist to frame the deal, and every coefficient is emitted in
the methodology section so the numbers are defensible, not magic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from parcels import Parcel
from valuation import Valuation

# --- Modeled lifecycle coefficients (see methodology) ------------------------
DUE_DILIGENCE_PCT = 0.05          # of asking price, for title/survey/environmental
INTERCONNECT_PCT_OF_ENERGY = 0.08  # study + upgrades, as a share of energy value
INTERCONNECT_FLOOR = 150_000.0    # minimum interconnection/permitting spend
DEBT_CAPACITY_PCT = 0.60          # financeable share of entitled HBU basis
FINANCING_FEE_PCT = 0.01          # of debt capacity
BUILD_CAPEX_PCT_OF_ENERGY = 0.90  # build path capex, as a share of energy value


@dataclass
class LifecycleStage:
    stage: int
    title: str
    objective: str
    value_unlocked_usd: float
    value_source: str
    capital_required_usd: float
    timeline_months: str
    key_risks: List[str]
    milestone_gate: str
    exit_options: List[str] = field(default_factory=list)


def build_lifecycle(parcel: Parcel, v: Valuation) -> List[LifecycleStage]:
    """Build the 4-stage lifecycle optimization plan for a parcel."""
    optionality = v.water_component + v.resource_component + v.surface_water_component

    s1 = LifecycleStage(
        stage=1,
        title="Acquisition",
        objective="Secure the parcel under contract; clear title, recorded "
                  "easement/ingress, and assessor due diligence.",
        value_unlocked_usd=v.acreage_component + optionality,
        value_source="Acreage floor + resource/water/surface-water optionality "
                     "brought under control",
        capital_required_usd=parcel.asking_price * (1 + DUE_DILIGENCE_PCT),
        timeline_months="0–3",
        key_risks=["Title or easement defects", "Environmental / access issues",
                   "Seller withdrawal or competing bid"],
        milestone_gate="Escrow Milestone 1 (Acquisition)",
    )

    interconnect_cost = max(v.energy_component * INTERCONNECT_PCT_OF_ENERGY,
                            INTERCONNECT_FLOOR)
    s2 = LifecycleStage(
        stage=2,
        title="Interconnection & Permitting",
        objective="File the interconnection queue position against the target "
                  "substation and secure conditional-use / grading permits.",
        value_unlocked_usd=v.energy_component,
        value_source="Energy/compute interconnect value (MW headroom)",
        capital_required_usd=interconnect_cost,
        timeline_months="6–18",
        key_risks=["Interconnection queue delay or withdrawal",
                   "Headroom proxy overstates real available capacity",
                   "Permitting denial or network-upgrade cost surprises"],
        milestone_gate="Escrow Milestone 2 (Interconnection/Permitting)",
    )

    debt_capacity = v.modeled_hbu_value * DEBT_CAPACITY_PCT
    s3 = LifecycleStage(
        stage=3,
        title="Equity Leverage",
        objective="Recapitalize against the entitled, interconnect-ready basis "
                  "to fund the build without diluting the arbitrage.",
        value_unlocked_usd=debt_capacity,
        value_source=f"Financing capacity (~{int(DEBT_CAPACITY_PCT*100)}% of "
                     "entitled HBU basis) — capital efficiency, not new value",
        capital_required_usd=debt_capacity * FINANCING_FEE_PCT,
        timeline_months="3–6",
        key_risks=["Appraisal below modeled HBU value",
                   "Rate / credit environment", "Covenant constraints"],
        milestone_gate="Escrow Milestone 3 (Equity Leverage)",
    )

    s4 = LifecycleStage(
        stage=4,
        title="Exit / JV Build",
        objective="Realize the HBU: build the energy/compute node (or "
                  "agrivoltaics / water bank), or JV / sell the shovel-ready "
                  "position at the modeled HBU value.",
        value_unlocked_usd=v.modeled_hbu_value,
        value_source="Full modeled HBU value realized",
        capital_required_usd=v.energy_component * BUILD_CAPEX_PCT_OF_ENERGY,
        timeline_months="12–36",
        key_risks=["Construction cost / schedule overruns",
                   "Offtake or buyer demand softening",
                   "Technology / use-case obsolescence"],
        milestone_gate="Escrow Milestone 4 (Exit/JV Build)",
        exit_options=[
            "Sell the entitled, interconnect-ready parcel at modeled HBU value",
            "JV with a developer/operator, retaining carried interest",
            "Self-build the HBU use and hold for cash flow",
        ],
    )
    return [s1, s2, s3, s4]


@dataclass
class LifecycleSummary:
    total_capital_usd: float
    modeled_hbu_value_usd: float
    asking_price_usd: float

    @property
    def modeled_net_value_usd(self) -> float:
        """Modeled HBU value less total staged capital (a framing figure)."""
        return self.modeled_hbu_value_usd - self.total_capital_usd


def summarize_lifecycle(stages: List[LifecycleStage], v: Valuation) -> LifecycleSummary:
    # Stage 3 capital is a financing fee; its "value unlocked" is debt capacity,
    # not spend, so total capital = acquisition + interconnect + fee + build.
    total_capital = sum(s.capital_required_usd for s in stages)
    return LifecycleSummary(
        total_capital_usd=total_capital,
        modeled_hbu_value_usd=v.modeled_hbu_value,
        asking_price_usd=v.asking_price,
    )
