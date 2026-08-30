"""Tests for water-rights DB enrichment (Phase 2)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from parcels import Parcel
from water_rights import WaterRight, WaterRightsRepository, enrich_water_rights


def _make_db(path: Path, rows) -> Path:
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE water_rights (
            parcel_id TEXT PRIMARY KEY, state TEXT, right_type TEXT,
            priority_date TEXT, acre_feet REAL, status TEXT)"""
    )
    conn.executemany("INSERT INTO water_rights VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def db(tmp_path) -> Path:
    return _make_db(tmp_path / "wr.sqlite", [
        ("P-ACTIVE", "NV", "surface", "1912-06-01", 180.0, "certificated"),
        ("P-NONE", "AZ", "none", None, 0.0, "none"),
        ("P-REVOKED", "UT", "groundwater", "1950-01-01", 100.0, "revoked"),
    ])


def _parcel(pid: str, af: float = 0.0) -> Parcel:
    return Parcel(pid, "C", "NV", 37.0, -117.0, acres=100, asking_price=50_000,
                  water_rights_acre_feet=af)


# --- repository --------------------------------------------------------------
def test_repo_loads_records(db):
    repo = WaterRightsRepository(db)
    assert repo.available
    assert len(repo) == 3
    assert repo.warning is None


def test_lookup_hit_and_miss(db):
    repo = WaterRightsRepository(db)
    assert repo.lookup("P-ACTIVE").acre_feet == 180.0
    assert repo.lookup("does-not-exist") is None


def test_missing_db_degrades_gracefully(tmp_path):
    repo = WaterRightsRepository(tmp_path / "nope.sqlite")
    assert not repo.available
    assert len(repo) == 0
    assert repo.lookup("anything") is None
    assert "not found" in repo.warning


def test_corrupt_db_degrades_gracefully(tmp_path):
    bad = tmp_path / "bad.sqlite"
    bad.write_text("this is not a sqlite database")
    repo = WaterRightsRepository(bad)
    assert not repo.available
    assert repo.warning is not None


# --- WaterRight.is_active ----------------------------------------------------
def test_is_active_true_for_positive_certificated():
    wr = WaterRight("P", "NV", "surface", "1912", 180.0, "certificated")
    assert wr.is_active


@pytest.mark.parametrize("status,af", [
    ("none", 0.0), ("revoked", 100.0), ("abandoned", 50.0), ("", 10.0),
])
def test_is_active_false_cases(status, af):
    assert not WaterRight("P", "NV", "t", None, af, status).is_active


# --- enrichment --------------------------------------------------------------
def test_db_is_authoritative_over_scraped_value(db):
    # Scraper claimed 5 AF; DB says 180 AF and wins.
    p = _parcel("P-ACTIVE", af=5.0)
    enrich_water_rights([p], WaterRightsRepository(db))
    assert p.water_rights_acre_feet == 180.0
    assert p.water_right_type == "surface"
    assert p.water_right_status == "certificated"
    assert p.water_right_priority_date == "1912-06-01"


def test_inactive_right_zeroes_acre_feet(db):
    # Scraper claimed 40 AF but DB marks the right revoked -> zeroed.
    p = _parcel("P-REVOKED", af=40.0)
    enrich_water_rights([p], WaterRightsRepository(db))
    assert p.water_rights_acre_feet == 0.0
    assert p.water_right_status == "revoked"


def test_no_record_leaves_parcel_untouched(db):
    p = _parcel("P-UNKNOWN", af=25.0)
    enrich_water_rights([p], WaterRightsRepository(db))
    assert p.water_rights_acre_feet == 25.0  # scraped value preserved
    assert p.water_right_type is None


def test_enrich_returns_match_count(db):
    parcels = [_parcel("P-ACTIVE"), _parcel("P-NONE"), _parcel("P-UNKNOWN")]
    matched = enrich_water_rights(parcels, WaterRightsRepository(db))
    assert matched == 2  # P-ACTIVE + P-NONE are in the DB; P-UNKNOWN is not


def test_enrich_is_noop_when_db_missing(tmp_path):
    p = _parcel("P-ACTIVE", af=7.0)
    matched = enrich_water_rights([p], WaterRightsRepository(tmp_path / "nope.sqlite"))
    assert matched == 0
    assert p.water_rights_acre_feet == 7.0
