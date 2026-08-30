"""Tests for environment loading and validation.

These manipulate os.environ via monkeypatch and force config to load from a
throwaway directory so no real .env is read.
"""

from __future__ import annotations

import pytest

import config as config_mod
from config import ConfigError, load_config

# Every env var config reads — cleared before each test for isolation.
_ENV_KEYS = [
    "APIFY_API_TOKEN", "PROPERTY_SCRAPER_ACTOR_ID", "RUN_MODE",
    "MAX_PRICE_PER_ACRE", "MIN_TRANSMISSION_BUFFER_MILES",
    "MIN_SUBSTATION_HEADROOM_MW", "DAYS_ON_MARKET_THRESHOLD",
    "REGIONAL_BASELINE_PRICE_PER_ACRE", "TARGET_STATES", "TARGET_ZIPS",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    # Point ROOT_DIR at an empty tmp dir so load_dotenv finds no real .env
    # and _ensure_output_dir writes into the sandbox.
    monkeypatch.setattr(config_mod, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "OUTPUT_DIR", tmp_path / "output_reports")
    yield


def test_defaults_resolve_to_mock_without_token():
    cfg = load_config()
    assert cfg.run_mode == "mock"
    assert cfg.max_price_per_acre == 2000.0
    assert cfg.min_substation_headroom_mw == 10.0
    assert cfg.target_states == ["NV", "AZ", "UT", "NM"]
    assert any("MOCK" in w for w in cfg.warnings)


def test_placeholder_token_is_treated_as_absent(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "your_apify_token_here")
    cfg = load_config()
    assert cfg.run_mode == "mock"


def test_real_token_auto_resolves_to_live(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "apify_api_realtoken123")
    cfg = load_config()
    assert cfg.run_mode == "live"
    assert cfg.is_live is True


def test_run_mode_live_without_token_raises(monkeypatch):
    monkeypatch.setenv("RUN_MODE", "live")
    with pytest.raises(ConfigError, match="requires a real APIFY_API_TOKEN"):
        load_config()


def test_run_mode_mock_forces_mock_even_with_token(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "apify_api_realtoken123")
    monkeypatch.setenv("RUN_MODE", "mock")
    assert load_config().run_mode == "mock"


def test_invalid_numeric_threshold_raises(monkeypatch):
    monkeypatch.setenv("MAX_PRICE_PER_ACRE", "abc")
    with pytest.raises(ConfigError, match="MAX_PRICE_PER_ACRE"):
        load_config()


def test_nonpositive_threshold_raises(monkeypatch):
    monkeypatch.setenv("MAX_PRICE_PER_ACRE", "0")
    with pytest.raises(ConfigError, match="MAX_PRICE_PER_ACRE must be > 0"):
        load_config()


def test_negative_dom_threshold_raises(monkeypatch):
    monkeypatch.setenv("DAYS_ON_MARKET_THRESHOLD", "-5")
    with pytest.raises(ConfigError, match="DAYS_ON_MARKET_THRESHOLD"):
        load_config()


def test_custom_target_lists_parse(monkeypatch):
    monkeypatch.setenv("TARGET_STATES", "NV, CA ,TX")
    monkeypatch.setenv("TARGET_ZIPS", "89013, 90001")
    cfg = load_config()
    assert cfg.target_states == ["NV", "CA", "TX"]
    assert cfg.target_zips == ["89013", "90001"]


def test_output_dir_created():
    cfg = load_config()
    assert cfg.output_dir.exists()
