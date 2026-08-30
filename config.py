"""
config.py — Environment & Configuration validator for the Land Value Engine.

Loads settings from a `.env` file (see `.env.example`), validates required
values and file paths, and exposes an immutable `Config` object consumed by the
rest of the pipeline.

Design note: configuration failures should be *loud and early*. A misconfigured
threshold silently corrupts every downstream Latent Arbitrage Score, so we fail
fast at startup rather than producing plausible-but-wrong dossiers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency not yet installed
    def load_dotenv(*_args, **_kwargs):  # type: ignore
        return False


# --- Paths -------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
GIS_DIR = DATA_DIR / "gis"
DB_DIR = DATA_DIR / "db"
OUTPUT_DIR = ROOT_DIR / "output_reports"
TEMPLATE_DIR = ROOT_DIR / "templates"

TRANSMISSION_LINES_PATH = GIS_DIR / "transmission_lines.geojson"
SUBSTATIONS_PATH = GIS_DIR / "electric_substations.geojson"
HYDROGRAPHY_PATH = GIS_DIR / "hydrography.geojson"
WATER_RIGHTS_DB_PATH = DB_DIR / "western_water_rights.sqlite"

# Committed small samples used when real data (fetched via data_loaders.py) is
# absent — keeps a fresh clone runnable without a network fetch.
SAMPLES_DIR = GIS_DIR / "samples"


def _resolve_gis(real: Path) -> Path:
    """Prefer real fetched data; fall back to the committed sample of the same name."""
    if real.exists():
        return real
    return SAMPLES_DIR / real.name

# Placeholder token value from `.env.example`; treated as "not configured".
_TOKEN_PLACEHOLDER = "your_apify_token_here"


class ConfigError(RuntimeError):
    """Raised when the environment is missing or invalid."""


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(float(raw))
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _get_list(name: str, default: List[str]) -> List[str]:
    raw = os.getenv(name)
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Config:
    """Validated runtime configuration for a single engine execution."""

    # Credentials / ingestion
    apify_token: str
    scraper_actor_id: str
    run_mode: str  # "live" or "mock" (resolved from RUN_MODE=auto)

    # Execution thresholds
    max_price_per_acre: float
    min_transmission_buffer_miles: float
    min_substation_headroom_mw: float
    days_on_market_threshold: int
    regional_baseline_price_per_acre: float

    # Surface-water proximity bonus: parcels within this many miles of a mapped
    # waterbody earn a resource-optionality boost (0 disables the signal).
    surface_water_bonus_miles: float = 5.0

    # Targeting
    target_states: List[str] = field(default_factory=lambda: ["NV", "AZ", "UT", "NM"])
    target_zips: List[str] = field(default_factory=list)

    # Paths (kept on the object so downstream modules never hard-code them)
    transmission_lines_path: Path = TRANSMISSION_LINES_PATH
    substations_path: Path = SUBSTATIONS_PATH
    hydrography_path: Path = HYDROGRAPHY_PATH
    water_rights_db_path: Path = WATER_RIGHTS_DB_PATH
    output_dir: Path = OUTPUT_DIR
    template_dir: Path = TEMPLATE_DIR

    warnings: List[str] = field(default_factory=list)

    @property
    def is_live(self) -> bool:
        return self.run_mode == "live"


def load_config() -> Config:
    """Load, resolve, and validate configuration. Raises ConfigError on failure."""
    load_dotenv(ROOT_DIR / ".env")

    warnings: List[str] = []

    token = os.getenv("APIFY_API_TOKEN", "").strip()
    actor_id = os.getenv("PROPERTY_SCRAPER_ACTOR_ID", "").strip()
    requested_mode = os.getenv("RUN_MODE", "auto").strip().lower()

    have_token = bool(token) and token != _TOKEN_PLACEHOLDER

    if requested_mode == "live":
        if not have_token:
            raise ConfigError(
                "RUN_MODE=live requires a real APIFY_API_TOKEN in .env "
                "(copy .env.example to .env and fill it in)."
            )
        run_mode = "live"
    elif requested_mode == "mock":
        run_mode = "mock"
    else:  # auto
        run_mode = "live" if have_token else "mock"
        if run_mode == "mock":
            warnings.append(
                "No APIFY_API_TOKEN configured — running in MOCK mode with "
                "synthetic parcels. Set a token in .env for live ingestion."
            )

    if run_mode == "live" and not actor_id:
        warnings.append(
            "PROPERTY_SCRAPER_ACTOR_ID not set — falling back to the default "
            "'rigelbytes~landdotcom-scraper' actor."
        )

    cfg = Config(
        apify_token=token,
        scraper_actor_id=actor_id or "rigelbytes~landdotcom-scraper",
        run_mode=run_mode,
        max_price_per_acre=_get_float("MAX_PRICE_PER_ACRE", 2000.0),
        min_transmission_buffer_miles=_get_float("MIN_TRANSMISSION_BUFFER_MILES", 3.0),
        min_substation_headroom_mw=_get_float("MIN_SUBSTATION_HEADROOM_MW", 10.0),
        days_on_market_threshold=_get_int("DAYS_ON_MARKET_THRESHOLD", 90),
        regional_baseline_price_per_acre=_get_float(
            "REGIONAL_BASELINE_PRICE_PER_ACRE", 6000.0
        ),
        surface_water_bonus_miles=_get_float("SURFACE_WATER_BONUS_MILES", 5.0),
        target_states=_get_list("TARGET_STATES", ["NV", "AZ", "UT", "NM"]),
        target_zips=_get_list("TARGET_ZIPS", []),
        transmission_lines_path=_resolve_gis(TRANSMISSION_LINES_PATH),
        substations_path=_resolve_gis(SUBSTATIONS_PATH),
        hydrography_path=_resolve_gis(HYDROGRAPHY_PATH),
        warnings=warnings,
    )

    if not TRANSMISSION_LINES_PATH.exists():
        warnings.append(
            "Using sample GIS from data/gis/samples/ — run "
            "`python data_loaders.py --states NV,AZ,UT,NM` for real corridor data."
        )

    _validate_thresholds(cfg)
    _ensure_output_dir(cfg)
    return cfg


def _validate_thresholds(cfg: Config) -> None:
    if cfg.max_price_per_acre <= 0:
        raise ConfigError("MAX_PRICE_PER_ACRE must be > 0")
    if cfg.min_transmission_buffer_miles <= 0:
        raise ConfigError("MIN_TRANSMISSION_BUFFER_MILES must be > 0")
    if cfg.min_substation_headroom_mw <= 0:
        raise ConfigError("MIN_SUBSTATION_HEADROOM_MW must be > 0")
    if cfg.days_on_market_threshold < 0:
        raise ConfigError("DAYS_ON_MARKET_THRESHOLD must be >= 0")
    if cfg.regional_baseline_price_per_acre <= 0:
        raise ConfigError("REGIONAL_BASELINE_PRICE_PER_ACRE must be > 0")
    if cfg.surface_water_bonus_miles < 0:
        raise ConfigError("SURFACE_WATER_BONUS_MILES must be >= 0")


def _ensure_output_dir(cfg: Config) -> None:
    cfg.output_dir.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    try:
        c = load_config()
    except ConfigError as e:
        raise SystemExit(f"[config] ERROR: {e}")
    print("[config] Loaded successfully:")
    print(f"  run_mode                 = {c.run_mode}")
    print(f"  target_states            = {c.target_states}")
    print(f"  max_price_per_acre       = {c.max_price_per_acre}")
    print(f"  transmission_buffer_mi   = {c.min_transmission_buffer_miles}")
    print(f"  min_substation_headroom  = {c.min_substation_headroom_mw} MW")
    print(f"  dom_threshold            = {c.days_on_market_threshold}")
    for w in c.warnings:
        print(f"  [warn] {w}")
