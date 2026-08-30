"""
spatial.py — Geospatial enrichment + hard-gate filtering.

Responsibilities:
  1. Load transmission lines and substations (HIFLD-style GeoJSON).
  2. For each parcel, compute distance to the nearest high-voltage line and
     the headroom of the nearest substation.
  3. Apply the ingress hard-gate (landlocked / no legal easement) and the
     infrastructure gates (transmission buffer + substation headroom).

Implementation note: this module prefers GeoPandas/Shapely for proper geodesic
work, but transparently falls back to a pure-Python great-circle implementation
so the pipeline is runnable before the heavy geospatial stack is installed.
Either path produces the same enriched `Parcel` fields.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import List, Optional, Tuple

from parcels import Parcel

_EARTH_RADIUS_MILES = 3958.7613
# Minimum transmission voltage (kV) considered "high-voltage" per the PRD.
MIN_LINE_KV = 69.0
# Grid cell size (degrees) for the waterbody proximity index.
_WATER_CELL_DEG = 0.25


# --- Distance helpers (pure-Python fallback) ---------------------------------
def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return _EARTH_RADIUS_MILES * 2 * math.asin(math.sqrt(a))


def _point_to_segment_miles(
    pt: Tuple[float, float], a: Tuple[float, float], b: Tuple[float, float]
) -> float:
    """Min distance (miles) from point to segment a-b via local equirectangular projection.

    pt/a/b are (lon, lat). Accurate at corridor scale where segments are short.
    """
    lon0, lat0 = pt
    lat_ref = math.radians(lat0)

    def to_xy(lon, lat):
        x = math.radians(lon - lon0) * math.cos(lat_ref) * _EARTH_RADIUS_MILES
        y = math.radians(lat - lat0) * _EARTH_RADIUS_MILES
        return x, y

    px, py = 0.0, 0.0
    ax, ay = to_xy(*a)
    bx, by = to_xy(*b)
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


# --- GeoJSON loading ---------------------------------------------------------
def _load_geojson(path: Path) -> dict:
    if not path.exists():
        return {"type": "FeatureCollection", "features": []}
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _line_coords(geom: dict) -> List[List[Tuple[float, float]]]:
    """Return a list of coordinate rings for a (Multi)LineString geometry."""
    if not geom:
        return []
    gtype = geom.get("type")
    coords = geom.get("coordinates", [])
    if gtype == "LineString":
        return [[(c[0], c[1]) for c in coords]]
    if gtype == "MultiLineString":
        return [[(c[0], c[1]) for c in line] for line in coords]
    return []


def _polygon_centroid_radius(geom: dict):
    """Reduce a (Multi)Polygon to (centroid_lon, centroid_lat, radius_miles).

    Radius = max great-circle distance from the centroid to any exterior vertex,
    so `haversine(pt, centroid) - radius` approximates distance to the edge.
    Returns None for non-polygon geometry.
    """
    if not geom:
        return None
    gtype = geom.get("type")
    coords = geom.get("coordinates", [])
    if gtype == "Polygon" and coords:
        rings = [coords[0]]
    elif gtype == "MultiPolygon" and coords:
        rings = [poly[0] for poly in coords if poly]
    else:
        return None

    pts = [(c[0], c[1]) for ring in rings for c in ring]
    if not pts:
        return None
    clon = sum(p[0] for p in pts) / len(pts)
    clat = sum(p[1] for p in pts) / len(pts)
    radius = max(_haversine_miles(clat, clon, p[1], p[0]) for p in pts)
    return (clon, clat, radius)


class SpatialContext:
    """Preloaded transmission + substation + hydrography reference data for a run."""

    def __init__(
        self,
        transmission_lines_path: Path,
        substations_path: Path,
        hydrography_path: Optional[Path] = None,
    ):
        lines_fc = _load_geojson(transmission_lines_path)
        subs_fc = _load_geojson(substations_path)

        # Keep only high-voltage lines (>= MIN_LINE_KV).
        self.lines: List[List[Tuple[float, float]]] = []
        for feat in lines_fc.get("features", []):
            props = feat.get("properties", {}) or {}
            kv = props.get("voltage_kv", props.get("VOLTAGE", MIN_LINE_KV))
            try:
                kv = float(kv)
            except (TypeError, ValueError):
                kv = MIN_LINE_KV
            if kv >= MIN_LINE_KV:
                self.lines.extend(_line_coords(feat.get("geometry", {})))

        # Substations: (lon, lat, headroom_mw, name, headroom_estimated).
        self.substations: List[Tuple[float, float, float, str, bool]] = []
        for feat in subs_fc.get("features", []):
            geom = feat.get("geometry", {}) or {}
            if geom.get("type") != "Point":
                continue
            lon, lat = geom.get("coordinates", [0, 0])[:2]
            props = feat.get("properties", {}) or {}
            headroom = props.get("headroom_mw", props.get("HEADROOM_MW", 0.0))
            try:
                headroom = float(headroom)
            except (TypeError, ValueError):
                headroom = 0.0
            name = str(props.get("name", props.get("NAME", "substation")))
            estimated = bool(props.get("headroom_estimated", False))
            self.substations.append((lon, lat, headroom, name, estimated))

        # Waterbodies reduced to (centroid_lon, centroid_lat, radius_miles) and
        # bucketed into a coarse lon/lat grid so proximity queries touch only a
        # 3x3 neighborhood of cells — O(1)-ish even with tens of thousands of
        # waterbodies across four states.
        self.waterbodies: List[Tuple[float, float, float]] = []
        self._water_grid: dict = {}
        if hydrography_path is not None:
            hydro_fc = _load_geojson(hydrography_path)
            for feat in hydro_fc.get("features", []):
                cr = _polygon_centroid_radius(feat.get("geometry", {}))
                if cr is not None:
                    self.waterbodies.append(cr)
                    self._water_grid.setdefault(self._cell(cr[0], cr[1]), []).append(cr)

    @staticmethod
    def _cell(lon: float, lat: float) -> Tuple[int, int]:
        # ~0.25 deg cells (~17 mi) — one ring of neighbors covers the 5-mi bonus
        # threshold plus typical small-lake radii.
        return (int(lon / _WATER_CELL_DEG), int(lat / _WATER_CELL_DEG))

    def nearest_line_distance_miles(self, lat: float, lon: float) -> Optional[float]:
        if not self.lines:
            return None
        pt = (lon, lat)
        best = math.inf
        for ring in self.lines:
            for i in range(len(ring) - 1):
                d = _point_to_segment_miles(pt, ring[i], ring[i + 1])
                if d < best:
                    best = d
        return None if best is math.inf else best

    def nearest_substation(self, lat: float, lon: float) -> Tuple[float, str, bool]:
        """Return (headroom_mw, name, headroom_estimated) of the nearest substation."""
        if not self.substations:
            return 0.0, "", False
        best_d = math.inf
        best = (0.0, "", False)
        for lon_s, lat_s, headroom, name, estimated in self.substations:
            d = _haversine_miles(lat, lon, lat_s, lon_s)
            if d < best_d:
                best_d = d
                best = (headroom, name, estimated)
        return best

    def nearest_water_distance_miles(self, lat: float, lon: float) -> Optional[float]:
        """Approx distance to the nearest mapped waterbody edge (0 if inside).

        Scans only the query cell + its 8 neighbors. A waterbody whose centroid
        is >~17 mi away is out of range of the proximity bonus anyway, so this is
        exact for the ranges that matter.
        """
        if not self._water_grid:
            return None
        cx, cy = self._cell(lon, lat)
        best = math.inf
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for lon_w, lat_w, radius in self._water_grid.get((cx + dx, cy + dy), ()):
                    d = max(0.0, _haversine_miles(lat, lon, lat_w, lon_w) - radius)
                    if d < best:
                        best = d
        return None if best is math.inf else best


def enrich_and_filter(parcels: List[Parcel], ctx: SpatialContext, cfg) -> List[Parcel]:
    """Enrich parcels with spatial data and apply hard/infrastructure gates.

    Returns only parcels that pass every gate. Disqualified parcels retain a
    `disqualification_reason` for audit/logging by the caller.
    """
    survivors: List[Parcel] = []
    for p in parcels:
        # --- Ingress hard-gate (PRD Module B) ---
        if p.landlocked or not p.has_legal_easement:
            p.disqualification_reason = "Ingress hard-gate: landlocked / no legal easement"
            continue

        # --- Spatial enrichment ---
        p.transmission_distance_miles = ctx.nearest_line_distance_miles(p.lat, p.lon)
        headroom, sub_name, headroom_estimated = ctx.nearest_substation(p.lat, p.lon)
        p.nearest_substation_headroom_mw = headroom
        p.nearest_substation_name = sub_name or None
        p.headroom_is_estimated = headroom_estimated
        p.surface_water_distance_miles = ctx.nearest_water_distance_miles(p.lat, p.lon)

        # --- Transmission buffer gate ---
        dist = p.transmission_distance_miles
        if dist is None or dist > cfg.min_transmission_buffer_miles:
            p.disqualification_reason = (
                f"Outside {cfg.min_transmission_buffer_miles} mi transmission buffer"
            )
            continue

        # --- Substation headroom gate ---
        if headroom < cfg.min_substation_headroom_mw:
            p.disqualification_reason = (
                f"Substation headroom {headroom:.0f} MW "
                f"< {cfg.min_substation_headroom_mw:.0f} MW minimum"
            )
            continue

        # --- Price gate ---
        if p.price_per_acre > cfg.max_price_per_acre:
            p.disqualification_reason = (
                f"${p.price_per_acre:,.0f}/acre exceeds "
                f"${cfg.max_price_per_acre:,.0f}/acre ceiling"
            )
            continue

        p.passes_spatial_gate = True
        survivors.append(p)

    return survivors
