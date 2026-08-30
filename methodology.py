"""
methodology.py — Assumptions, coefficients, data sources, and caveats.

Emitted with every dossier so each figure is traceable to the coefficient,
threshold, and data source that produced it. The classifier for "actionable"
here is: a reader can reconstruct any number in the report from this section.
"""

from __future__ import annotations

from typing import Dict, List

import lifecycle as lc
import scoring
import valuation as val


def methodology(cfg) -> List[Dict]:
    """Return the methodology as titled sections of (label, value) rows."""
    return [
        {
            "title": "Latent Arbitrage Score — weights",
            "rows": [
                ("Price arbitrage", f"{scoring.WEIGHTS['price_arbitrage']*100:.0f}%"),
                ("Days-on-market / lazy listing", f"{scoring.WEIGHTS['days_on_market']*100:.0f}%"),
                ("Infrastructure headroom", f"{scoring.WEIGHTS['infrastructure_headroom']*100:.0f}%"),
                ("Resource optionality", f"{scoring.WEIGHTS['resource_optionality']*100:.0f}%"),
            ],
        },
        {
            "title": "HBU valuation — coefficients",
            "rows": [
                ("Value per MW of headroom", f"${val.VALUE_PER_MW_HEADROOM:,.0f}"),
                ("Value per acre-foot of water", f"${val.VALUE_PER_ACRE_FOOT:,.0f}"),
                ("Geothermal premium", f"${val.GEOTHERMAL_PREMIUM:,.0f}"),
                ("Mineral/gold premium", f"${val.MINERAL_PREMIUM:,.0f}"),
                ("Raw acreage floor", f"${val.RAW_ACRE_FLOOR:,.0f}/acre"),
                ("Surface-water premium (max)", f"${val.SURFACE_WATER_PREMIUM:,.0f}"),
            ],
        },
        {
            "title": "Execution thresholds (this run)",
            "rows": [
                ("Max price / acre", f"${cfg.max_price_per_acre:,.0f}"),
                ("Min transmission buffer", f"{cfg.min_transmission_buffer_miles} mi"),
                ("Min substation headroom", f"{cfg.min_substation_headroom_mw:.0f} MW"),
                ("Days-on-market threshold", f"{cfg.days_on_market_threshold} days"),
                ("Regional baseline price", f"${cfg.regional_baseline_price_per_acre:,.0f}/acre"),
                ("Surface-water bonus radius", f"{cfg.surface_water_bonus_miles} mi"),
            ],
        },
        {
            "title": "Lifecycle capital model — coefficients",
            "rows": [
                ("Due-diligence budget", f"{lc.DUE_DILIGENCE_PCT*100:.0f}% of asking"),
                ("Interconnection cost", f"{lc.INTERCONNECT_PCT_OF_ENERGY*100:.0f}% of energy value "
                                          f"(floor ${lc.INTERCONNECT_FLOOR:,.0f})"),
                ("Debt capacity", f"{lc.DEBT_CAPACITY_PCT*100:.0f}% of entitled HBU"),
                ("Build capex", f"{lc.BUILD_CAPEX_PCT_OF_ENERGY*100:.0f}% of energy value"),
            ],
        },
        {
            "title": "Data sources",
            "rows": [
                ("Transmission lines", "HIFLD Electric Power Transmission Lines (≥69 kV)"),
                ("Substations", "Derived from HIFLD line endpoints (SUB_1/SUB_2)"),
                ("Hydrography", "USGS National Hydrography Dataset (waterbodies)"),
                ("Water rights", "Western water-rights reference DB"),
                ("Listings", "Apify property scraper (live mode)"),
            ],
        },
        {
            "title": "Caveats",
            "rows": [
                ("Substation headroom", "A voltage/connectivity PROXY, not "
                 "interconnection-queue capacity. Verify with the ISO/RTO queue."),
                ("Regional baseline", "A modeled comp, not a per-parcel appraisal."),
                ("Capital figures", "Modeled from the coefficients above — not quotes."),
                ("State labels", "Approximate (bbox-based) for cross-border parcels."),
                ("On-chain layer", "Unaudited demo/testnet scaffold — no mainnet value."),
                ("Nature of output", "Diagnostic estimates only — not an appraisal, "
                 "financial advice, or an offer to buy/sell."),
            ],
        },
    ]
