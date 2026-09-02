"""
parcel_gis.py — Resolve real parcel coordinates from free public GIS.

Listing scrapers rarely include precise coordinates, but our spatial gates need
lat/lon. This module fills that gap from FREE public sources — no paid actor:

  * County/state assessor parcel services (ArcGIS): APN -> parcel polygon ->
    centroid. Precise, authoritative. Nevada has a verified statewide service;
    other states are added to the registry as their endpoints are confirmed.
  * US Census Geocoder (national, no key): street address -> lat/lon. A
    fallback when there's no APN or the state has no parcel service yet.

Everything is best-effort and network-guarded: a lookup that fails returns None
and the parcel simply stays un-enriched (and gets gated out for lack of coords).
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import List, Optional, Tuple

from parcels import Parcel

# Verified free statewide/county parcel ArcGIS services (APN -> geometry).
# Extend per state as endpoints are confirmed; AZ/UT/NM welcome here.
PARCEL_SERVICES = {
    "NV": {
        "url": ("https://gis.dot.nv.gov/agsphs/rest/services/Reference/"
                "Statewide_Parcels/MapServer/0"),
        "apn_field": "APN",
    },
}

CENSUS_GEOCODER = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"

LatLon = Tuple[float, float]


def _http_json(url: str, params: dict, timeout: int = 40) -> Optional[dict]:
    try:
        full = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(full, headers={"User-Agent": "LVE-LAP/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - network is best-effort here
        return None


def _centroid_of_rings(rings) -> Optional[LatLon]:
    pts = [p for ring in (rings or []) for p in ring if len(p) >= 2]
    if not pts:
        return None
    clon = sum(p[0] for p in pts) / len(pts)
    clat = sum(p[1] for p in pts) / len(pts)
    return (clat, clon)


def parcel_centroid_by_apn(state: str, apn: str) -> Optional[LatLon]:
    """Look up a parcel polygon by APN in the state's parcel service; return its
    centroid as (lat, lon), or None if unavailable."""
    svc = PARCEL_SERVICES.get((state or "").upper())
    if not svc or not apn:
        return None
    field = svc["apn_field"]
    # APN formats vary (dashes/dots/spaces); try raw then normalized.
    candidates = [apn, re.sub(r"[^A-Za-z0-9]", "", apn)]
    for cand in dict.fromkeys(candidates):  # de-dupe, preserve order
        data = _http_json(f"{svc['url']}/query", {
            "where": f"{field}='{cand}'",
            "outFields": field,
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "json",
        })
        feats = (data or {}).get("features", [])
        if feats:
            c = _centroid_of_rings(feats[0].get("geometry", {}).get("rings"))
            if c:
                return c
    return None


def geocode_address(address: str) -> Optional[LatLon]:
    """Geocode a one-line address via the US Census Geocoder -> (lat, lon)."""
    if not address or not address.strip():
        return None
    data = _http_json(CENSUS_GEOCODER, {
        "address": address,
        "benchmark": "Public_AR_Current",
        "format": "json",
    })
    matches = (data or {}).get("result", {}).get("addressMatches", [])
    if not matches:
        return None
    c = matches[0].get("coordinates", {})
    if "y" in c and "x" in c:
        return (float(c["y"]), float(c["x"]))
    return None


def resolve_coordinates(parcel: Parcel) -> Optional[str]:
    """Fill a parcel's lat/lon from GIS when missing. Returns the method used
    ('county-apn' | 'geocode') or None if it couldn't be resolved."""
    if parcel.lat and parcel.lon:  # already has coordinates
        return parcel.coord_source or "listing"

    # 1. Authoritative: APN -> county parcel geometry.
    c = parcel_centroid_by_apn(parcel.state, parcel.apn) if parcel.apn else None
    if c:
        parcel.lat, parcel.lon = c
        parcel.coord_source = "county-apn"
        return "county-apn"

    # 2. Fallback: geocode a street address.
    if parcel.street_address:
        c = geocode_address(parcel.street_address)
        if c:
            parcel.lat, parcel.lon = c
            parcel.coord_source = "geocode"
            return "geocode"
    return None


def enrich_coordinates(parcels: List[Parcel]) -> int:
    """Resolve coordinates for all parcels missing them. Returns count resolved."""
    resolved = 0
    for p in parcels:
        if p.lat and p.lon:
            if not p.coord_source:
                p.coord_source = "listing"
            continue
        if resolve_coordinates(p):
            resolved += 1
    return resolved
