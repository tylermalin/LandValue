"""
scoring.py — Latent Arbitrage Score (LAS) engine.

The LAS is a composite 0–100 score expressing how mispriced a parcel is
relative to its highest-and-best-use (HBU) potential. Each sub-score is
normalized to [0, 1] then combined with the PRD weights:

    Price Arbitrage .......... 30%   (asking $/acre vs. regional baseline)
    Days-on-Market / Lazy .... 20%   (stale listing + non-optimized copy)
    Infrastructure Headroom .. 40%   (substation MW headroom + interconnect)
    Resource Optionality ..... 10%   (water rights, geothermal, minerals)

Keeping the scorer pure (no I/O, no globals) makes each component individually
testable and makes the final score fully explainable — every dossier carries
the sub-score breakdown, not just the headline number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

from parcels import Parcel

# PRD weights — must sum to 1.0.
WEIGHTS: Dict[str, float] = {
    "price_arbitrage": 0.30,
    "days_on_market": 0.20,
    "infrastructure_headroom": 0.40,
    "resource_optionality": 0.10,
}

# Keywords that signal a "lazy"/non-optimized broker listing.
_LAZY_LISTING_MARKERS = (
    "as-is", "as is", "must sell", "motivated seller", "make offer",
    "bring all offers", "raw land", "no utilities", "handyman",
    "priced to sell", "cash only",
)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


@dataclass
class ScoreBreakdown:
    """Explainable decomposition of a parcel's LAS."""

    price_arbitrage: float = 0.0
    days_on_market: float = 0.0
    infrastructure_headroom: float = 0.0
    resource_optionality: float = 0.0
    notes: Dict[str, str] = field(default_factory=dict)

    @property
    def total(self) -> float:
        """Weighted composite on a 0–100 scale."""
        composite = (
            self.price_arbitrage * WEIGHTS["price_arbitrage"]
            + self.days_on_market * WEIGHTS["days_on_market"]
            + self.infrastructure_headroom * WEIGHTS["infrastructure_headroom"]
            + self.resource_optionality * WEIGHTS["resource_optionality"]
        )
        return round(composite * 100.0, 1)

    def as_dict(self) -> Dict[str, float]:
        return {
            "price_arbitrage": round(self.price_arbitrage * 100, 1),
            "days_on_market": round(self.days_on_market * 100, 1),
            "infrastructure_headroom": round(self.infrastructure_headroom * 100, 1),
            "resource_optionality": round(self.resource_optionality * 100, 1),
            "total": self.total,
        }


def _score_price_arbitrage(parcel: Parcel, baseline_per_acre: float) -> tuple[float, str]:
    """Higher when asking $/acre sits far below the regional baseline."""
    if parcel.price_per_acre <= 0 or baseline_per_acre <= 0:
        return 0.0, "No price/acre available"
    discount = (baseline_per_acre - parcel.price_per_acre) / baseline_per_acre
    score = _clamp(discount)
    multiple = baseline_per_acre / parcel.price_per_acre if parcel.price_per_acre else 0
    return score, f"{discount*100:.0f}% below baseline ({multiple:.1f}x arbitrage multiple)"


def _score_days_on_market(parcel: Parcel, dom_threshold: int) -> tuple[float, str]:
    """Stale listings + lazy broker copy signal a distressed / overlooked seller."""
    if dom_threshold <= 0:
        dom_component = 1.0 if parcel.days_on_market > 0 else 0.0
    else:
        # Saturates at 2x the threshold.
        dom_component = _clamp(parcel.days_on_market / (dom_threshold * 2.0))

    text = (parcel.listing_description or "").lower()
    lazy_hits = [m for m in _LAZY_LISTING_MARKERS if m in text]
    lazy_component = _clamp(len(lazy_hits) / 3.0)

    # DOM dominates (0.7) but lazy copy nudges it up (0.3).
    score = _clamp(0.7 * dom_component + 0.3 * lazy_component)
    note = f"{parcel.days_on_market} DOM"
    if lazy_hits:
        note += f"; lazy markers: {', '.join(lazy_hits[:3])}"
    return score, note


def _score_infrastructure(parcel: Parcel, min_headroom_mw: float) -> tuple[float, str]:
    """Substation headroom vs. threshold, discounted by interconnection distance."""
    if parcel.nearest_substation_headroom_mw <= 0:
        return 0.0, "No substation headroom data"

    # Headroom saturates at 5x the minimum requirement.
    ceiling = max(min_headroom_mw * 5.0, min_headroom_mw + 1.0)
    headroom_component = _clamp(parcel.nearest_substation_headroom_mw / ceiling)

    # Closer to transmission = easier interconnect. 0 mi -> 1.0, 3+ mi -> ~0.
    dist = parcel.transmission_distance_miles
    proximity_component = _clamp(1.0 - (dist / 3.0)) if dist is not None else 0.5

    score = _clamp(0.65 * headroom_component + 0.35 * proximity_component)
    note = (
        f"{parcel.nearest_substation_headroom_mw:.0f} MW headroom; "
        f"{dist:.1f} mi to transmission" if dist is not None
        else f"{parcel.nearest_substation_headroom_mw:.0f} MW headroom"
    )
    return score, note


def _score_resource_optionality(
    parcel: Parcel, surface_water_bonus_miles: float
) -> tuple[float, str]:
    """Appurtenant water rights, geothermal, minerals, and surface-water proximity."""
    components = []
    notes = []
    if parcel.water_rights_acre_feet and parcel.water_rights_acre_feet > 0:
        components.append(0.5)
        notes.append(f"{parcel.water_rights_acre_feet:.0f} AF water rights")
    if parcel.geothermal_signature:
        components.append(0.3)
        notes.append("geothermal signature")
    if parcel.mineral_claims:
        components.append(0.3)
        notes.append("mineral/gold claims")

    # Surface-water proximity (USGS NHD): additive booster, linear to the
    # threshold. Distinct from appurtenant water RIGHTS — it signals water-bank
    # / access optionality, not a legal entitlement.
    dist = parcel.surface_water_distance_miles
    if surface_water_bonus_miles > 0 and dist is not None and dist < surface_water_bonus_miles:
        proximity = _clamp(1.0 - dist / surface_water_bonus_miles)
        components.append(0.20 * proximity)
        notes.append(f"surface water {dist:.1f} mi")

    score = _clamp(sum(components))
    return score, ("; ".join(notes) if notes else "No appurtenant resources")


def score_parcel(
    parcel: Parcel,
    *,
    baseline_per_acre: float,
    dom_threshold: int,
    min_headroom_mw: float,
    surface_water_bonus_miles: float = 5.0,
) -> ScoreBreakdown:
    """Compute the full explainable LAS breakdown for a single parcel."""
    breakdown = ScoreBreakdown()

    breakdown.price_arbitrage, breakdown.notes["price_arbitrage"] = _score_price_arbitrage(
        parcel, baseline_per_acre
    )
    breakdown.days_on_market, breakdown.notes["days_on_market"] = _score_days_on_market(
        parcel, dom_threshold
    )
    breakdown.infrastructure_headroom, breakdown.notes["infrastructure_headroom"] = (
        _score_infrastructure(parcel, min_headroom_mw)
    )
    breakdown.resource_optionality, breakdown.notes["resource_optionality"] = (
        _score_resource_optionality(parcel, surface_water_bonus_miles)
    )
    return breakdown
