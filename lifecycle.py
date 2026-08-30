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

# --- Modeled lifecycle coefficients (research-grounded; see methodology) -----
DUE_DILIGENCE_PCT = 0.04          # of asking price: title/survey/environmental (~3-5%)
INTERCONNECT_COST_PER_MW = 100_000.0  # study + typical network upgrades ($/MW)
INTERCONNECT_FLOOR = 150_000.0    # minimum interconnection/permitting spend
DEBT_CAPACITY_PCT = 0.55          # conservative LTV against entitled HBU basis
FINANCING_FEE_PCT = 0.015         # of debt capacity
DISPOSITION_PCT = 0.02           # sale/JV closing cost (the land-owner's exit path)
# Informational only — the OPERATOR's build cost, not the land owner's. Greenfield
# data-center all-in ~$17.6M/MW (Cushman & Wakefield 2026); site/shell power infra
# is a fraction. Surfaced for the self-build exit option, NOT in the capital total.
BUILD_CAPEX_PER_MW = 10_000_000.0


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

    headroom_mw = parcel.nearest_substation_headroom_mw
    interconnect_cost = max(headroom_mw * INTERCONNECT_COST_PER_MW, INTERCONNECT_FLOOR)
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

    build_capex = headroom_mw * BUILD_CAPEX_PER_MW
    s4 = LifecycleStage(
        stage=4,
        title="Exit / JV Build",
        objective="Realize the HBU: sell or JV the shovel-ready, interconnect-"
                  "ready position at the modeled HBU value (capital-light), or "
                  "self-build the energy/compute node.",
        value_unlocked_usd=v.modeled_hbu_value,
        value_source="Full modeled HBU value realized",
        # Land-owner's exit is a sale/JV: closing cost, not build capex.
        capital_required_usd=v.modeled_hbu_value * DISPOSITION_PCT,
        timeline_months="12–36",
        key_risks=["Buyer / offtake demand softening",
                   "Appraisal or interconnection re-study",
                   "Technology / use-case obsolescence"],
        milestone_gate="Escrow Milestone 4 (Exit/JV Build)",
        exit_options=[
            "Sell the entitled, interconnect-ready parcel at modeled HBU value",
            "JV with a developer/operator, retaining carried interest",
            f"Self-build (operator capex ~{build_capex/1e6:.0f}M @ "
            f"${BUILD_CAPEX_PER_MW/1e6:.0f}M/MW — informational, not in the total)",
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
