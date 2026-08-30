"""
confidence.py — Confidence scoring, decomposed by data provenance.

The Latent Arbitrage Score (LAS) measures how *big* an opportunity looks.
Confidence answers a separate question: how much should we *trust* that score,
given where each input came from? A parcel can be high-LAS / low-confidence
(promising but built on estimates) or the reverse.

Confidence is a 0–100% weighted blend of per-factor scores. Each factor is
graded by the provenance of its underlying data:

    MEASURED   — real, sourced data (a live listing, HIFLD geometry, USGS, a
                 verified water-right record)                         -> 1.00
    ESTIMATED  — a modeled proxy (voltage-based headroom, a regional
                 price baseline, an unverified scraped figure)        -> 0.55
    ASSUMED    — a default with no parcel-specific evidence           -> 0.35
    MISSING    — no data at all                                       -> 0.00

Every factor carries a plain-language rationale so the score can be "picked
apart" rather than taken on faith. Weights mirror the LAS weighting so
confidence reflects what actually drives the opportunity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List

from parcels import Parcel


class Provenance(str, Enum):
    MEASURED = "measured"
    ESTIMATED = "estimated"
    ASSUMED = "assumed"
    MISSING = "missing"


LEVEL_SCORE = {
    Provenance.MEASURED: 1.00,
    Provenance.ESTIMATED: 0.55,
    Provenance.ASSUMED: 0.35,
    Provenance.MISSING: 0.00,
}

_REAL_SOURCES = ("apify", "mls", "county", "listing")


@dataclass
class ConfidenceFactor:
    key: str
    label: str
    level: Provenance
    weight: float
    rationale: str

    @property
    def score(self) -> float:
        return LEVEL_SCORE[self.level]


@dataclass
class ConfidenceBreakdown:
    factors: List[ConfidenceFactor] = field(default_factory=list)

    @property
    def total_pct(self) -> float:
        w = sum(f.weight for f in self.factors)
        if w == 0:
            return 0.0
        return round(sum(f.weight * f.score for f in self.factors) / w * 100.0, 1)

    @property
    def label(self) -> str:
        p = self.total_pct
        return "High" if p >= 75 else "Medium" if p >= 50 else "Low"

    @property
    def limiting_factors(self) -> List[ConfidenceFactor]:
        """Factors dragging confidence down most (lowest score first)."""
        return sorted(self.factors, key=lambda f: f.score)

    def as_dict(self) -> dict:
        return {
            "total_pct": self.total_pct,
            "label": self.label,
            "factors": [
                {"key": f.key, "label": f.label, "level": f.level.value,
                 "score": round(f.score * 100), "weight": f.weight,
                 "rationale": f.rationale}
                for f in self.factors
            ],
        }


def _is_real_source(parcel: Parcel) -> bool:
    return any(parcel.source.lower().startswith(s) for s in _REAL_SOURCES)


def score_confidence(parcel: Parcel) -> ConfidenceBreakdown:
    """Decompose confidence for a scored parcel by input provenance."""
    real = _is_real_source(parcel)
    f: List[ConfidenceFactor] = []

    # --- Sourcing / listing provenance ---
    if parcel.listing_url and real:
        f.append(ConfidenceFactor(
            "sourcing", "Listing sourcing", Provenance.MEASURED, 0.15,
            "Sourced from a live listing with a document link."))
    elif parcel.listing_url:
        f.append(ConfidenceFactor(
            "sourcing", "Listing sourcing", Provenance.ASSUMED, 0.15,
            "Illustrative/synthetic parcel; link is a placeholder."))
    else:
        f.append(ConfidenceFactor(
            "sourcing", "Listing sourcing", Provenance.MISSING, 0.15,
            "No listing or source document on file."))

    # --- Pricing ---
    if real and parcel.asking_price > 0:
        f.append(ConfidenceFactor(
            "pricing", "Pricing", Provenance.ESTIMATED, 0.20,
            "Asking price is real, but the regional baseline it is compared "
            "against is a modeled assumption."))
    else:
        f.append(ConfidenceFactor(
            "pricing", "Pricing", Provenance.ASSUMED, 0.20,
            "Asking price and baseline are both synthetic/modeled."))

    # --- Days on market ---
    if real and parcel.days_on_market > 0:
        f.append(ConfidenceFactor(
            "dom", "Days on market", Provenance.MEASURED, 0.10,
            f"{parcel.days_on_market} DOM from the listing."))
    else:
        f.append(ConfidenceFactor(
            "dom", "Days on market", Provenance.MISSING, 0.10,
            "Days-on-market not available from a live listing."))

    # --- Transmission proximity (real HIFLD geometry when loaded) ---
    if parcel.transmission_distance_miles is not None:
        f.append(ConfidenceFactor(
            "transmission", "Transmission proximity", Provenance.MEASURED, 0.15,
            f"{parcel.transmission_distance_miles:.1f} mi, computed from "
            "HIFLD line geometry."))
    else:
        f.append(ConfidenceFactor(
            "transmission", "Transmission proximity", Provenance.MISSING, 0.15,
            "No transmission geometry available for this parcel."))

    # --- Substation headroom (the usual confidence drag: voltage proxy) ---
    if parcel.nearest_substation_headroom_mw <= 0:
        f.append(ConfidenceFactor(
            "headroom", "Substation headroom", Provenance.MISSING, 0.20,
            "No substation headroom data."))
    elif parcel.headroom_is_estimated:
        f.append(ConfidenceFactor(
            "headroom", "Substation headroom", Provenance.ESTIMATED, 0.20,
            "Headroom is a voltage/connectivity PROXY, not interconnection-"
            "queue capacity — verify with the ISO/RTO queue."))
    else:
        f.append(ConfidenceFactor(
            "headroom", "Substation headroom", Provenance.MEASURED, 0.20,
            "Headroom from a sourced capacity figure."))

    # --- Water rights ---
    if parcel.water_right_status:
        f.append(ConfidenceFactor(
            "water_rights", "Water rights", Provenance.MEASURED, 0.10,
            f"{parcel.water_rights_acre_feet:.0f} AF, {parcel.water_right_status} "
            "per the water-rights DB."))
    elif parcel.water_rights_acre_feet > 0:
        f.append(ConfidenceFactor(
            "water_rights", "Water rights", Provenance.ESTIMATED, 0.10,
            "Scraped water-rights figure not confirmed against the state DB."))
    else:
        f.append(ConfidenceFactor(
            "water_rights", "Water rights", Provenance.MISSING, 0.10,
            "No appurtenant water rights recorded."))

    # --- Resource flags (geothermal / mineral) ---
    if parcel.geothermal_signature or parcel.mineral_claims:
        f.append(ConfidenceFactor(
            "resource_flags", "Geothermal / mineral", Provenance.ESTIMATED, 0.05,
            "Optionality flags are indicative and need a survey/title check."))
    else:
        f.append(ConfidenceFactor(
            "resource_flags", "Geothermal / mineral", Provenance.MISSING, 0.05,
            "No geothermal or mineral optionality flagged."))

    # --- Surface water (real USGS NHD when loaded) ---
    if parcel.surface_water_distance_miles is not None:
        f.append(ConfidenceFactor(
            "surface_water", "Surface-water proximity", Provenance.MEASURED, 0.05,
            f"{parcel.surface_water_distance_miles:.1f} mi to USGS-mapped water."))
    else:
        f.append(ConfidenceFactor(
            "surface_water", "Surface-water proximity", Provenance.MISSING, 0.05,
            "No hydrography layer loaded."))

    return ConfidenceBreakdown(factors=f)
