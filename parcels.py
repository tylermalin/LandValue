"""
parcels.py — Domain model + ingestion for candidate land parcels.

`Parcel` is the ubiquitous-language object that flows through the whole
pipeline: ingestion -> spatial enrichment -> scoring -> reporting. It is the
single seam between "messy external data" (Apify rows, GIS joins, assessor
records) and "clean internal data" the rest of the engine reasons about.

Ingestion has two backends:
  * `fetch_live_parcels`  — Apify actor run (requires APIFY_API_TOKEN)
  * `mock_parcels`        — deterministic synthetic corridor for offline runs
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Parcel:
    """A single candidate land asset."""

    parcel_id: str
    county: str
    state: str
    lat: float
    lon: float
    acres: float
    asking_price: float
    days_on_market: int = 0
    listing_description: str = ""

    # Legal / ingress
    landlocked: bool = False
    has_legal_easement: bool = True

    # Enriched by the spatial stage (None until enriched).
    transmission_distance_miles: Optional[float] = None
    nearest_substation_headroom_mw: float = 0.0
    nearest_substation_name: Optional[str] = None
    headroom_is_estimated: bool = False  # True when headroom is a voltage proxy
    surface_water_distance_miles: Optional[float] = None  # USGS NHD proximity

    # Resource optionality
    water_rights_acre_feet: float = 0.0
    geothermal_signature: bool = False
    mineral_claims: bool = False

    # Water-right provenance (populated by the water-rights enrichment stage).
    water_right_type: Optional[str] = None
    water_right_status: Optional[str] = None
    water_right_priority_date: Optional[str] = None

    # Provenance / source documents (for confidence + actionable links).
    source: str = "unknown"          # e.g. "apify:<actor>", "mock", "synthetic"
    listing_url: Optional[str] = None
    assessor_url: Optional[str] = None
    apn: Optional[str] = None         # assessor parcel number
    source_date: Optional[str] = None
    street_address: Optional[str] = None  # for geocoding fallback
    coord_source: Optional[str] = None    # listing | county-apn | geocode

    # Populated by the spatial gate; True means it survived filtering.
    passes_spatial_gate: bool = False
    disqualification_reason: Optional[str] = None

    @property
    def price_per_acre(self) -> float:
        if self.acres <= 0:
            return 0.0
        return self.asking_price / self.acres


# --- Live ingestion (Apify) --------------------------------------------------
# Default LandWatch searches for the corridor when SEARCH_URLS is unset.
_DEFAULT_SEARCH_URLS = [
    "https://www.landwatch.com/nevada-land-for-sale",
    "https://www.landwatch.com/arizona-land-for-sale",
    "https://www.landwatch.com/utah-land-for-sale",
    "https://www.landwatch.com/new-mexico-land-for-sale",
]


def build_run_input(cfg) -> dict:
    """Build the actor run input for the memo23/landwatch-scraper (URL-driven).

    Schema: startUrls (required landwatch.com URLs), maxItems (per-run cap —
    bounds cost), proxy. This actor returns lat/lon, APN, and address — what the
    spatial gates need. Corridor/price filtering happens in our own gates.
    """
    return {
        "startUrls": list(cfg.search_urls) or list(_DEFAULT_SEARCH_URLS),
        "maxItems": cfg.max_items,
        "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
    }


def fetch_live_parcels(cfg) -> List["Parcel"]:
    """Run the configured Apify actor and normalize rows into Parcels.

    Kept defensive: external scraper schemas drift, so every field read is
    guarded and unmappable rows are skipped rather than crashing the run.
    """
    from apify_client import ApifyClient  # imported lazily; live-mode only

    client = ApifyClient(cfg.apify_token)
    run = client.actor(cfg.scraper_actor_id).call(run_input=build_run_input(cfg))
    if run is None:
        return []

    # apify-client 3.x returns a pydantic Run object (snake_case attrs); older
    # versions returned a dict. Support both.
    def field(obj, attr, key):
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, attr, None)

    status = field(run, "status", "status")
    dataset_id = field(run, "default_dataset_id", "defaultDatasetId")
    if status and str(status) != "SUCCEEDED":
        raise RuntimeError(
            f"Apify actor run did not succeed (status={status}). Check the run "
            f"log in the Apify console; a common cause is proxy blocking."
        )
    if not dataset_id:
        return []

    parcels: List[Parcel] = []
    blocked = 0
    seen = 0
    for row in client.dataset(dataset_id).iterate_items():
        seen += 1
        if row.get("type") == "land_blocked":  # actor's anti-bot sentinel
            blocked += 1
            continue
        parcel = _row_to_parcel(row)
        if parcel is not None:
            parcels.append(parcel)
    # If every record was a block sentinel, surface it rather than "0 results".
    if not parcels and blocked and blocked == seen:
        raise RuntimeError(
            f"Scraper was blocked on all {blocked} requests (anti-bot). Try again "
            f"later, narrow SEARCH_URLS, or switch actors."
        )
    return parcels


# Nested containers land scrapers commonly wrap fields in (e.g. memo23).
_NESTED_KEYS = ("propertyData", "basicInfo", "address", "listingDetail", "details")


def _deep_get(row: dict, *keys):
    """First non-empty value for any key, searched top-level then nested objects.

    Robust across actor schemas: flat (Land.com) and nested (memo23 LandWatch,
    which wraps lat/lon/address under propertyData/basicInfo/address).
    """
    containers = [row]
    for sub in _NESTED_KEYS:
        v = row.get(sub)
        if isinstance(v, dict):
            containers.append(v)
    for c in containers:
        for k in keys:
            v = c.get(k)
            if v not in (None, ""):
                return v
    return None


def _row_to_parcel(row: dict) -> Optional[Parcel]:
    """Best-effort normalization of a scraper row into a Parcel (schema-agnostic)."""
    def num(*keys, default=0.0):
        v = _deep_get(row, *keys)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    acres = num("acres", "lotSizeAcres", "acreage")
    price = num("price", "askingPrice", "listPrice")
    if acres <= 0 or price <= 0:
        return None

    lat = num("latitude", "lat")
    lon = num("longitude", "lng", "lon")

    listing_url = _deep_get(row, "url", "canonicalUrl", "listingUrl", "link", "detailUrl")
    apn = _deep_get(row, "parcelId", "apn", "parcelNumber")

    # Build a one-line street address for the geocoding fallback.
    street = _deep_get(row, "address1", "streetAddress", "street")
    city = _deep_get(row, "city", "siteCity", "SiteCity")
    zip_c = _deep_get(row, "zip", "zipCode", "postalCode")
    st = str(_deep_get(row, "state", "stateCode") or "").upper()[:2]
    addr_parts = [str(x) for x in (street, city, f"{st} {zip_c}".strip()) if x]
    street_address = ", ".join(addr_parts) if street and (city or zip_c) else None

    # Keep parcels missing coordinates ONLY if we can resolve them later (APN or
    # a geocodable address); otherwise there is nothing to place on the map.
    has_coords = not (lat == 0.0 and lon == 0.0)
    if not has_coords and not (apn or street_address):
        return None
    # Stable id: explicit id, else APN, else listing URL, else a composite.
    zip_code = _deep_get(row, "zip", "zipCode", "postalCode") or ""
    county = str(_deep_get(row, "county", "countyName") or "Unknown")
    parcel_id = str(
        _deep_get(row, "id", "pid", "mlsId") or apn or listing_url
        or f"{county}-{zip_code}-{int(price)}"
    )
    source_date = _deep_get(row, "publishedAt", "listedDate", "listDate")

    return Parcel(
        parcel_id=parcel_id,
        county=county,
        state=str(_deep_get(row, "state", "stateCode") or "").upper()[:2],
        lat=lat,
        lon=lon,
        acres=acres,
        asking_price=price,
        days_on_market=int(num("daysOnMarket", "dom")),
        listing_description=str(_deep_get(row, "description", "descriptionText",
                                          "remarks", "summary") or ""),
        landlocked=bool(_deep_get(row, "landlocked") or False),
        has_legal_easement=bool(_deep_get(row, "hasEasement")
                                if _deep_get(row, "hasEasement") is not None else True),
        water_rights_acre_feet=num("waterRightsAcreFeet", "waterRights"),
        geothermal_signature=bool(_deep_get(row, "geothermal") or False),
        mineral_claims=bool(_deep_get(row, "mineralClaims") or False),
        source="apify",
        listing_url=listing_url,
        apn=(str(apn) if apn else None),
        source_date=(str(source_date) if source_date else None),
        street_address=street_address,
        coord_source=("listing" if has_coords else None),
    )


# --- Mock ingestion (offline / demo) -----------------------------------------
def mock_parcels() -> List["Parcel"]:
    """Deterministic synthetic corridor across NV/AZ/UT/NM.

    Coordinates are placed near the mock transmission lines/substations in
    data/gis so the spatial stage produces meaningful joins out of the box.

    Provenance fields carry illustrative example.com links so the dossier's
    "source documents" section renders in demos; live data supplies real URLs.
    """
    parcels = [
        Parcel(
            parcel_id="NV-ESM-0417",
            county="Esmeralda",
            state="NV",
            lat=37.782,
            lon=-117.235,
            acres=640.0,
            asking_price=920_000.0,  # ~$1,437/acre
            days_on_market=214,
            listing_description="Raw land, as-is. Motivated seller, bring all offers. No utilities.",
            water_rights_acre_feet=180.0,
            geothermal_signature=True,
            mineral_claims=True,
            listing_url="https://example.com/listing/NV-ESM-0417",
            assessor_url="https://example.com/assessor/esmeralda/007-041-17",
            apn="007-041-17",
            source_date="2025-11-02",
        ),
        Parcel(
            parcel_id="AZ-MOH-1188",
            county="Mohave",
            state="AZ",
            lat=35.190,
            lon=-114.053,
            acres=320.0,
            asking_price=560_000.0,  # $1,750/acre
            days_on_market=132,
            listing_description="Priced to sell. Cash only. Handyman special, off-grid.",
            water_rights_acre_feet=0.0,
            geothermal_signature=False,
            mineral_claims=True,
        ),
        Parcel(
            parcel_id="UT-JUA-0733",
            county="Juab",
            state="UT",
            lat=39.700,
            lon=-112.680,
            acres=1280.0,
            asking_price=3_100_000.0,  # ~$2,422/acre
            days_on_market=61,
            listing_description="Well-marketed development parcel near infrastructure.",
            water_rights_acre_feet=420.0,
            geothermal_signature=True,
            mineral_claims=False,
        ),
        Parcel(
            parcel_id="NM-LUN-2054",
            county="Luna",
            state="NM",
            lat=32.180,
            lon=-107.760,
            acres=480.0,
            asking_price=384_000.0,  # $800/acre
            days_on_market=305,
            listing_description="As is. Must sell. Raw desert acreage.",
            water_rights_acre_feet=0.0,
            geothermal_signature=False,
            mineral_claims=False,
        ),
        # Should be hard-gated out (landlocked, no easement).
        Parcel(
            parcel_id="NV-NYE-9001",
            county="Nye",
            state="NV",
            lat=38.010,
            lon=-116.900,
            acres=200.0,
            asking_price=150_000.0,
            days_on_market=410,
            listing_description="Landlocked parcel, no recorded access.",
            landlocked=True,
            has_legal_easement=False,
        ),
    ]
    for p in parcels:
        if p.source == "unknown":
            p.source = "mock"
        if p.listing_url is None:
            p.listing_url = f"https://example.com/listing/{p.parcel_id}"
    return parcels


def ingest(cfg) -> List["Parcel"]:
    """Entry point: choose backend based on resolved run mode."""
    if cfg.is_live:
        return fetch_live_parcels(cfg)
    return mock_parcels()


# Approx state bounding boxes for labeling synthetic parcels: (lon0,lat0,lon1,lat1).
_STATE_BOXES = {
    "NV": (-120.01, 35.00, -114.04, 42.00),
    "AZ": (-114.82, 31.33, -109.05, 37.00),
    "UT": (-114.05, 36.99, -109.04, 42.00),
    "NM": (-109.05, 31.33, -103.00, 37.00),
}


def _state_from_lonlat(lon: float, lat: float) -> str:
    """Best-effort 2-letter state label from coordinates (illustrative only)."""
    for code, (x0, y0, x1, y1) in _STATE_BOXES.items():
        if x0 <= lon <= x1 and y0 <= lat <= y1:
            return code
    return "--"


def synthetic_near_infrastructure(
    substations_path, n: int = 120, seed: int = 42, jitter_deg: float = 0.015
) -> List["Parcel"]:
    """Generate demo parcels jittered around REAL substations from a GeoJSON file.

    Reads substation points produced by `data_loaders.py` and scatters parcels
    within ~1 mile of them, so generated parcels genuinely sit near live
    transmission infrastructure and exercise the gates against real geometry.
    Falls back to the fixed `synthetic_corridor` if the file is missing/empty.
    """
    import json
    import random
    from pathlib import Path

    path = Path(substations_path)
    if not path.exists():
        return synthetic_corridor(n=n, seed=seed)
    fc = json.loads(path.read_text(encoding="utf-8"))
    subs = [f for f in fc.get("features", [])
            if (f.get("geometry") or {}).get("type") == "Point"]
    if not subs:
        return synthetic_corridor(n=n, seed=seed)

    rng = random.Random(seed)
    parcels: List[Parcel] = []
    for i in range(n):
        sub = rng.choice(subs)
        lon, lat = sub["geometry"]["coordinates"][:2]
        acres = rng.choice([80, 160, 320, 640, 1280])
        price_per_acre = round(rng.uniform(400, 3200), 2)
        parcels.append(Parcel(
            parcel_id=f"SYN-{1000 + i}",
            county=str(sub.get("properties", {}).get("name", "Unknown"))[:24],
            state=_state_from_lonlat(lon, lat),
            lat=lat + rng.uniform(-jitter_deg, jitter_deg),
            lon=lon + rng.uniform(-jitter_deg, jitter_deg),
            acres=float(acres),
            asking_price=round(acres * price_per_acre, 2),
            days_on_market=rng.choice([12, 45, 78, 110, 180, 260, 340]),
            listing_description=rng.choice(_LAZY_SNIPPETS),
            landlocked=(rng.random() < 0.05),
            has_legal_easement=(rng.random() > 0.04),
            water_rights_acre_feet=(round(rng.uniform(40, 500), 0)
                                    if rng.random() < 0.4 else 0.0),
            geothermal_signature=(rng.random() < 0.2),
            mineral_claims=(rng.random() < 0.25),
            source="synthetic",
        ))
    return parcels


# --- Synthetic corridor (dashboard / load testing) ---------------------------
# Cluster centers near the sample substations so generated parcels land inside
# the transmission buffer and inherit real headroom via the spatial join.
_CORRIDOR_CLUSTERS = [
    # (county, state, lat, lon, has_water, geothermal_bias, mineral_bias)
    ("Esmeralda", "NV", 37.782, -117.235, True, 0.35, 0.30),
    ("Mohave", "AZ", 35.190, -114.045, False, 0.10, 0.40),
    ("Juab", "UT", 39.697, -112.672, True, 0.30, 0.15),
    ("Luna", "NM", 32.178, -107.758, False, 0.05, 0.20),
]

_LAZY_SNIPPETS = [
    "Raw land, as-is. Motivated seller.", "Priced to sell, cash only.",
    "Must sell. Bring all offers.", "Off-grid acreage, no utilities.",
    "Well-marketed development parcel near infrastructure.",
    "Handyman special, make offer.",
]


def synthetic_corridor(n: int = 120, seed: int = 42) -> List["Parcel"]:
    """Deterministic pseudo-random corridor of `n` parcels for the dashboard.

    Spreads parcels around the sample substations with jitter, varied pricing
    (some deliberately above the ceiling), stale/fresh listings, and mixed
    resource optionality — enough spread to populate a Top-N matrix and map.
    """
    import random

    rng = random.Random(seed)
    parcels: List[Parcel] = []
    for i in range(n):
        county, state, clat, clon, has_water, geo_bias, min_bias = rng.choice(
            _CORRIDOR_CLUSTERS
        )
        lat = clat + rng.uniform(-0.03, 0.03)
        lon = clon + rng.uniform(-0.03, 0.03)
        acres = rng.choice([80, 160, 320, 640, 1280])
        # Price/acre skewed low so many parcels clear the arbitrage bar.
        price_per_acre = round(rng.uniform(400, 3200), 2)
        parcels.append(Parcel(
            parcel_id=f"{state}-{county[:3].upper()}-{1000 + i}",
            county=county,
            state=state,
            lat=lat,
            lon=lon,
            acres=float(acres),
            asking_price=round(acres * price_per_acre, 2),
            days_on_market=rng.choice([12, 45, 78, 110, 180, 260, 340]),
            listing_description=rng.choice(_LAZY_SNIPPETS),
            landlocked=(rng.random() < 0.06),
            has_legal_easement=(rng.random() > 0.04),
            water_rights_acre_feet=(round(rng.uniform(40, 500), 0)
                                    if has_water and rng.random() < 0.5 else 0.0),
            geothermal_signature=(rng.random() < geo_bias),
            mineral_claims=(rng.random() < min_bias),
            source="synthetic",
        ))
    return parcels
