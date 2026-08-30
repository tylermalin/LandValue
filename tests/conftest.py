"""Shared fixtures for the LVE-LAP test suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the project root importable when pytest runs from anywhere.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import Config  # noqa: E402
from parcels import Parcel  # noqa: E402


@pytest.fixture
def cfg(tmp_path) -> Config:
    """A deterministic Config using the repo's sample GIS data.

    Output is redirected to a tmp dir so tests never touch output_reports/.
    Thresholds mirror the shipped defaults so mock-corridor expectations hold.
    """
    return Config(
        apify_token="",
        scraper_actor_id="test-actor",
        run_mode="mock",
        max_price_per_acre=2000.0,
        min_transmission_buffer_miles=3.0,
        min_substation_headroom_mw=10.0,
        days_on_market_threshold=90,
        regional_baseline_price_per_acre=6000.0,
        surface_water_bonus_miles=5.0,
        target_states=["NV", "AZ", "UT", "NM"],
        target_zips=[],
        # Deterministic committed sample GIS keeps the suite stable regardless of
        # what the real loaders write into data/gis/.
        transmission_lines_path=ROOT / "data" / "gis" / "samples" / "transmission_lines.geojson",
        substations_path=ROOT / "data" / "gis" / "samples" / "electric_substations.geojson",
        # Point at a non-existent hydrography file so surface-water proximity is
        # inert in the shared fixture; dedicated tests exercise it explicitly.
        hydrography_path=ROOT / "data" / "gis" / "samples" / "_no_hydrography.geojson",
        water_rights_db_path=ROOT / "data" / "db" / "western_water_rights.sqlite",
        output_dir=tmp_path / "output_reports",
        template_dir=ROOT / "templates",
        warnings=[],
    )


@pytest.fixture
def base_parcel() -> Parcel:
    """A clean, gate-passing parcel with strong latent value.

    Placed on the NV transmission corridor near the Esmeralda Tap substation.
    """
    return Parcel(
        parcel_id="TEST-0001",
        county="Esmeralda",
        state="NV",
        lat=37.782,
        lon=-117.235,
        acres=640.0,
        asking_price=920_000.0,  # ~$1,437/acre — under the $2k ceiling
        days_on_market=214,
        listing_description="Raw land, as-is. Motivated seller. No utilities.",
        water_rights_acre_feet=180.0,
        geothermal_signature=True,
        mineral_claims=True,
    )
