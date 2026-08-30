"""
data_loaders.py — Real GIS ingestion from HIFLD + USGS (replaces sample data).

Fetches live infrastructure/hydrography for a bounding box and writes the same
GeoJSON files `spatial.py` already consumes, so the pipeline picks up real data
with no other changes:

  * Transmission lines  — HIFLD "Electric Power Transmission Lines" (ArcGIS)
  * Substations         — DERIVED from transmission-line endpoints + SUB_1/SUB_2
                          names (HIFLD's public substation point layer is gone).
                          Headroom is an explicit voltage-based PROXY, not real
                          interconnection-queue capacity — see estimate_headroom_mw.
  * Hydrography         — USGS National Hydrography Dataset waterbodies (ArcGIS)

Zero third-party deps: uses urllib. Networked; run explicitly, not in tests.

    python data_loaders.py --states NV,AZ,UT,NM
    python data_loaders.py --bbox -118,37,-116,39
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
GIS_DIR = ROOT / "data" / "gis"

HIFLD_TRANSMISSION = (
    "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/ArcGIS/rest/services/"
    "Electric_Power_Transmission_Lines/FeatureServer/0"
)
USGS_NHD = "https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer"
USGS_WATERBODY_LAYER = 10  # "Waterbody - Small Scale"

MIN_LINE_KV = 69.0
DEFAULT_MAX_RECORDS = 20_000  # safety cap; a hit is logged, never silent

# Approx state bounding boxes: (lon_min, lat_min, lon_max, lat_max).
STATE_BBOX: Dict[str, Tuple[float, float, float, float]] = {
    "NV": (-120.01, 35.00, -114.04, 42.00),
    "AZ": (-114.82, 31.33, -109.05, 37.00),
    "UT": (-114.05, 36.99, -109.04, 42.00),
    "NM": (-109.05, 31.33, -103.00, 37.00),
}

BBox = Tuple[float, float, float, float]


def _log(msg: str) -> None:
    print(f"[loaders] {msg}")


def _http_get_json(url: str, params: dict, timeout: int = 60, retries: int = 3) -> dict:
    """GET a JSON endpoint with basic retry/backoff."""
    query = urllib.parse.urlencode(params)
    full = f"{url}?{query}"
    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": "LVE-LAP/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - network is inherently flaky
            last_exc = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET failed after {retries} tries: {full[:120]}… ({last_exc})")


def union_bbox(states: List[str]) -> BBox:
    """Union the bounding boxes of the given states."""
    boxes = [STATE_BBOX[s] for s in states if s in STATE_BBOX]
    if not boxes:
        raise ValueError(f"no known bboxes for states={states}")
    return (
        min(b[0] for b in boxes), min(b[1] for b in boxes),
        max(b[2] for b in boxes), max(b[3] for b in boxes),
    )


def _query_arcgis_features(
    service_layer: str, bbox: BBox, where: str, out_fields: str,
    max_records: int = DEFAULT_MAX_RECORDS,
) -> List[dict]:
    """Page through an ArcGIS FeatureServer/MapServer query, returning GeoJSON features."""
    xmin, ymin, xmax, ymax = bbox
    features: List[dict] = []
    offset = 0
    page = 2000
    while len(features) < max_records:
        data = _http_get_json(f"{service_layer}/query", {
            "where": where,
            "geometry": f"{xmin},{ymin},{xmax},{ymax}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "outSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": out_fields,
            "resultOffset": offset,
            "resultRecordCount": page,
            "f": "geojson",
        })
        batch = data.get("features", [])
        features.extend(batch)
        # Last page when the server returns fewer than a full page (or nothing).
        if len(batch) < page:
            break
        offset += len(batch)
    if len(features) >= max_records:
        _log(f"WARNING: hit max_records={max_records} cap for {service_layer.split('/')[-3]}; "
             f"result is TRUNCATED — narrow the bbox or raise --max-records.")
    return features[:max_records]


# --- Transmission lines ------------------------------------------------------
def fetch_transmission_lines(bbox: BBox, min_kv: float = MIN_LINE_KV,
                             max_records: int = DEFAULT_MAX_RECORDS) -> dict:
    """Fetch HIFLD transmission lines >= min_kv within bbox as a GeoJSON FC."""
    _log(f"Fetching transmission lines (>= {min_kv:.0f} kV) …")
    feats = _query_arcgis_features(
        HIFLD_TRANSMISSION, bbox,
        where=f"VOLTAGE>={int(min_kv)}",
        out_fields="VOLTAGE,VOLT_CLASS,OWNER,SUB_1,SUB_2",
        max_records=max_records,
    )
    # Normalize VOLTAGE -> voltage_kv so spatial.py reads it directly.
    for f in feats:
        props = f.get("properties", {}) or {}
        props["voltage_kv"] = props.get("VOLTAGE")
    _log(f"  {len(feats)} transmission line features.")
    return {"type": "FeatureCollection", "name": "transmission_lines", "features": feats}


# --- Substation derivation ---------------------------------------------------
# Rough single-circuit thermal ratings by voltage class (MVA). Used only for the
# headroom PROXY below — replace with ISO/RTO interconnection-queue data for real
# available capacity.
_VOLTAGE_MVA = [(69, 90), (115, 180), (138, 250), (161, 300),
                (230, 600), (345, 1200), (500, 2600), (765, 4000)]
_SPARE_FRACTION = 0.12  # assumed spare capacity — a placeholder, not measured


def estimate_headroom_mw(max_kv: float, degree: int) -> float:
    """Voltage/connectivity PROXY for substation headroom (NOT real capacity).

    Bigger voltage class and more connected lines imply a more capable hub. This
    exists so the pipeline has a headroom signal from HIFLD alone; swap it for
    interconnection-queue data when available. Explicitly flagged in output.
    """
    capacity = _VOLTAGE_MVA[0][1]
    for kv, mva in _VOLTAGE_MVA:
        if max_kv >= kv:
            capacity = mva
    degree_factor = min(1.0 + 0.10 * max(0, degree - 1), 2.0)
    return round(capacity * _SPARE_FRACTION * degree_factor, 1)


def derive_substations(lines_fc: dict) -> dict:
    """Derive substation points from transmission-line endpoints + SUB names.

    Each line contributes its two endpoints (SUB_1 at the start, SUB_2 at the
    end). Substations are aggregated by name; degree = #connected lines,
    max voltage = max over connected lines. Headroom is the proxy above.
    """
    acc: Dict[str, dict] = {}

    def endpoints(geom: dict) -> List[Tuple[float, float]]:
        if not geom:
            return []
        t, c = geom.get("type"), geom.get("coordinates", [])
        if t == "LineString" and c:
            return [tuple(c[0][:2]), tuple(c[-1][:2])]
        if t == "MultiLineString" and c:
            return [tuple(c[0][0][:2]), tuple(c[-1][-1][:2])]
        return []

    for f in lines_fc.get("features", []):
        props = f.get("properties", {}) or {}
        kv = float(props.get("VOLTAGE") or props.get("voltage_kv") or 0)
        pts = endpoints(f.get("geometry", {}))
        if len(pts) != 2:
            continue
        for name_key, pt in zip(("SUB_1", "SUB_2"), pts):
            name = (props.get(name_key) or "").strip()
            if not name or name.upper() in ("UNKNOWN", "NOT AVAILABLE", "TAP"):
                continue
            s = acc.setdefault(name, {"lon": pt[0], "lat": pt[1],
                                      "max_kv": 0.0, "degree": 0})
            s["degree"] += 1
            s["max_kv"] = max(s["max_kv"], kv)
            # Keep the highest-voltage endpoint's coordinates.
            if kv >= s["max_kv"]:
                s["lon"], s["lat"] = pt[0], pt[1]

    feats = []
    for name, s in acc.items():
        feats.append({
            "type": "Feature",
            "properties": {
                "name": name,
                "headroom_mw": estimate_headroom_mw(s["max_kv"], s["degree"]),
                "max_voltage_kv": s["max_kv"],
                "line_degree": s["degree"],
                "headroom_estimated": True,
                "headroom_source": "voltage-proxy",
            },
            "geometry": {"type": "Point", "coordinates": [s["lon"], s["lat"]]},
        })
    _log(f"  derived {len(feats)} substations from line endpoints "
         f"(headroom = voltage proxy).")
    return {"type": "FeatureCollection", "name": "electric_substations", "features": feats}


# --- Hydrography (USGS NHD) ---------------------------------------------------
def fetch_hydrography(bbox: BBox, max_records: int = DEFAULT_MAX_RECORDS) -> dict:
    """Fetch USGS NHD waterbodies within bbox as a GeoJSON FC (optionality signal)."""
    _log("Fetching USGS NHD waterbodies …")
    layer = f"{USGS_NHD}/{USGS_WATERBODY_LAYER}"
    feats = _query_arcgis_features(
        layer, bbox, where="1=1", out_fields="GNIS_NAME,AREASQKM,FTYPE",
        max_records=max_records,
    )
    _log(f"  {len(feats)} waterbody features.")
    return {"type": "FeatureCollection", "name": "hydrography", "features": feats}


# --- Orchestration -----------------------------------------------------------
def _write(fc: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fc), encoding="utf-8")
    _log(f"  wrote {len(fc.get('features', []))} features -> {path.relative_to(ROOT)}")


def refresh(bbox: BBox, out_dir: Path = GIS_DIR, min_kv: float = MIN_LINE_KV,
            include_hydrography: bool = True,
            max_records: int = DEFAULT_MAX_RECORDS) -> None:
    """Fetch all layers for bbox and write the GeoJSON files the pipeline reads."""
    lines = fetch_transmission_lines(bbox, min_kv=min_kv, max_records=max_records)
    subs = derive_substations(lines)
    _write(lines, out_dir / "transmission_lines.geojson")
    _write(subs, out_dir / "electric_substations.geojson")
    if include_hydrography:
        hydro = fetch_hydrography(bbox, max_records=max_records)
        _write(hydro, out_dir / "hydrography.geojson")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch real HIFLD/USGS GIS data")
    ap.add_argument("--states", help="comma list, e.g. NV,AZ,UT,NM")
    ap.add_argument("--bbox", help="lon_min,lat_min,lon_max,lat_max (overrides --states)")
    ap.add_argument("--min-kv", type=float, default=MIN_LINE_KV)
    ap.add_argument("--max-records", type=int, default=DEFAULT_MAX_RECORDS)
    ap.add_argument("--no-hydro", action="store_true", help="skip USGS hydrography")
    args = ap.parse_args(argv)

    if args.bbox:
        parts = [float(x) for x in args.bbox.split(",")]
        if len(parts) != 4:
            ap.error("--bbox needs 4 comma-separated numbers")
        bbox = (parts[0], parts[1], parts[2], parts[3])
    elif args.states:
        bbox = union_bbox([s.strip().upper() for s in args.states.split(",")])
    else:
        ap.error("provide --states or --bbox")

    _log(f"bbox = {bbox}")
    refresh(bbox, min_kv=args.min_kv, include_hydrography=not args.no_hydro,
            max_records=args.max_records)
    _log("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
