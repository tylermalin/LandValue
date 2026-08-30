"""
seed_water_rights.py — Create/seed the western water-rights SQLite reference DB.

Run once to bootstrap `western_water_rights.sqlite` with a small sample schema.
Phase 2 of the roadmap replaces this seed with automated state water-engineer
database scrapers.

    python data/db/seed_water_rights.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "western_water_rights.sqlite"

SAMPLE_RIGHTS = [
    # (parcel_id, state, right_type, priority_date, acre_feet, status)
    ("NV-ESM-0417", "NV", "surface", "1912-06-01", 180.0, "certificated"),
    ("UT-JUA-0733", "UT", "groundwater", "1955-03-14", 420.0, "perfected"),
    ("AZ-MOH-1188", "AZ", "none", None, 0.0, "none"),
    ("NM-LUN-2054", "NM", "none", None, 0.0, "none"),
]


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS water_rights (
                parcel_id      TEXT PRIMARY KEY,
                state          TEXT NOT NULL,
                right_type     TEXT,
                priority_date  TEXT,
                acre_feet      REAL DEFAULT 0,
                status         TEXT
            )
            """
        )
        conn.executemany(
            "INSERT OR REPLACE INTO water_rights VALUES (?, ?, ?, ?, ?, ?)",
            SAMPLE_RIGHTS,
        )
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM water_rights").fetchone()[0]
        print(f"[seed] wrote {count} water-rights rows to {DB_PATH}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
