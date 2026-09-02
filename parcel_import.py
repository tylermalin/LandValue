"""
parcel_import.py — Import candidate parcels from a CSV/JSON you curate.

The most practical way to use the engine without a paid scraper: hand a list of
candidate parcels (from a broker sheet, your own research, an export) with an
APN or address plus the asking price, and let the pipeline resolve coordinates
from free public GIS and run the full analysis.

CSV/JSON columns (only asking_price + one of apn/address are strictly needed;
the rest sharpen scoring):
    apn, address, asking_price, acres, county, state, zip,
    days_on_market, description, listing_url,
    water_rights_acre_feet, geothermal, mineral

Coordinates are optional — if absent, the coordinate-enrichment stage fills them.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import List

from parcels import Parcel


def _num(rec: dict, *keys, default=0.0) -> float:
    for k in keys:
        v = rec.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return default


def _flag(rec: dict, key: str) -> bool:
    v = rec.get(key)
    return str(v).strip().lower() in ("1", "true", "yes", "y") if v is not None else False


def record_to_parcel(rec: dict) -> Parcel | None:
    """Build a Parcel from a curated record. Requires asking_price + (apn|address|coords)."""
    price = _num(rec, "asking_price", "price")
    if price <= 0:
        return None
    apn = (rec.get("apn") or "").strip() or None
    address = (rec.get("address") or "").strip() or None
    lat = _num(rec, "latitude", "lat")
    lon = _num(rec, "longitude", "lon", "lng")
    if not (apn or address or (lat and lon)):
        return None  # nothing to place it on the map

    acres = _num(rec, "acres", default=0.0)
    if acres <= 0:
        acres = 1.0  # avoid divide-by-zero; flagged low elsewhere via $/acre

    return Parcel(
        parcel_id=str(rec.get("id") or rec.get("parcel_id") or apn or address or f"row-{price:.0f}"),
        county=str(rec.get("county") or "Unknown"),
        state=str(rec.get("state") or "").upper()[:2],
        lat=lat, lon=lon,
        acres=acres,
        asking_price=price,
        days_on_market=int(_num(rec, "days_on_market", "dom")),
        listing_description=str(rec.get("description") or ""),
        water_rights_acre_feet=_num(rec, "water_rights_acre_feet", "water_rights"),
        geothermal_signature=_flag(rec, "geothermal"),
        mineral_claims=_flag(rec, "mineral"),
        source="import",
        listing_url=(rec.get("listing_url") or None),
        apn=apn,
        street_address=address,
        coord_source=("listing" if (lat and lon) else None),
    )


def parcels_from_file(path) -> List[Parcel]:
    """Load candidate parcels from a .csv or .json file."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".json":
        records = json.loads(text)
        if isinstance(records, dict):
            records = records.get("parcels", [])
    else:
        records = list(csv.DictReader(text.splitlines()))
    return [pc for pc in (record_to_parcel(r) for r in records) if pc is not None]
