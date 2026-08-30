"""
sanity.py — Plausibility flags for opportunities.

A high Latent Arbitrage Score means nothing if it rests on a data artifact —
a $37/acre asking price, a 340x multiple, or value that leans entirely on the
estimated-headroom proxy. These checks catch "too good to be true" cases and
attach human-readable flags so a reader treats them with the skepticism they
deserve. Flags never change the score; they annotate it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from confidence import ConfidenceBreakdown
from parcels import Parcel
from scoring import ScoreBreakdown
from valuation import Valuation

# Thresholds
MULTIPLE_IMPLAUSIBLE = 25.0   # above this, the multiple is almost certainly an artifact
MULTIPLE_HIGH = 10.0          # high enough to warrant a confidence cross-check
CONFIDENCE_LOW_PCT = 50.0     # below this, data is largely unverified
MIN_PLAUSIBLE_PPA = 100.0     # $/acre below this reads as a bad listing
PROXY_VALUE_SHARE = 0.60      # HBU share from energy that makes proxy risk material
HIGH_LAS = 70.0               # a "top" opportunity worth extra scrutiny


@dataclass
class SanityFlag:
    level: str    # "warn" (likely bad data) | "caution" (real but fragile)
    code: str
    message: str


def sanity_flags(
    parcel: Parcel,
    score: ScoreBreakdown,
    valuation: Valuation,
    confidence: ConfidenceBreakdown,
) -> List[SanityFlag]:
    """Return plausibility flags for an opportunity (empty if it looks sound)."""
    flags: List[SanityFlag] = []
    multiple = valuation.arbitrage_multiple
    conf = confidence.total_pct if confidence else 0.0
    ppa = parcel.price_per_acre

    # 1. Implausible arbitrage multiple — almost always a bad asking price.
    if multiple >= MULTIPLE_IMPLAUSIBLE:
        flags.append(SanityFlag(
            "warn", "implausible_multiple",
            f"Arbitrage multiple {multiple:.0f}x is implausibly high — likely a "
            f"data artifact (verify the asking price)."))
    # 2. High multiple on low-confidence data — real-looking but unverified.
    elif multiple >= MULTIPLE_HIGH and conf < CONFIDENCE_LOW_PCT:
        flags.append(SanityFlag(
            "caution", "high_multiple_low_confidence",
            f"{multiple:.1f}x multiple on {conf:.0f}% confidence — treat as "
            f"unverified until sourced."))

    # 3. Implausibly low asking price per acre.
    if 0 < ppa < MIN_PLAUSIBLE_PPA:
        flags.append(SanityFlag(
            "warn", "implausible_price_per_acre",
            f"${ppa:,.0f}/acre is implausibly low — verify the listing."))

    # 4. Value dominated by the estimated-headroom proxy.
    hbu = valuation.modeled_hbu_value
    if (hbu > 0 and parcel.headroom_is_estimated
            and valuation.energy_component / hbu >= PROXY_VALUE_SHARE):
        share = valuation.energy_component / hbu * 100
        flags.append(SanityFlag(
            "caution", "proxy_dependent_value",
            f"{share:.0f}% of HBU value rests on ESTIMATED headroom — confirm "
            f"real interconnection capacity before relying on it."))

    # 5. A top opportunity with no source document.
    if score.total >= HIGH_LAS and not (
            parcel.listing_url and parcel.source.lower().startswith(
                ("apify", "mls", "county", "listing"))):
        flags.append(SanityFlag(
            "caution", "no_source_document",
            "Top-ranked but has no verified source listing/document on file."))

    return flags
