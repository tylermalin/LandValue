"""
water_rights.py — Appurtenant water-rights enrichment (PRD roadmap Phase 2).

Reads the western water-rights SQLite reference DB and attaches verified
appurtenant-rights data to parcels at ingestion time. The DB is treated as the
authoritative source: when it holds a record for a parcel, its acre-feet figure
and status override whatever the property scraper reported (scrapers routinely
miss or misreport water rights); when it has no record, the parcel's existing
value is left untouched so live-scraped data still flows through.

The repository degrades gracefully: a missing DB file yields an empty
repository (no enrichment, one warning) rather than crashing the pipeline —
consistent with the mock/PDF fallbacks elsewhere. Phase 2's automated
state water-engineer scrapers will replace the seed data behind this same API.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from parcels import Parcel


@dataclass(frozen=True)
class WaterRight:
    parcel_id: str
    state: str
    right_type: str
    priority_date: Optional[str]
    acre_feet: float
    status: str

    @property
    def is_active(self) -> bool:
        """A right that confers real optionality (not 'none'/revoked)."""
        return self.acre_feet > 0 and (self.status or "").lower() not in (
            "none", "revoked", "abandoned", "",
        )


class WaterRightsRepository:
    """Read-only lookup over the water-rights SQLite DB.

    Loads all rows once into an in-memory index — the reference table is small
    and every parcel triggers a lookup, so a single scan beats per-parcel
    queries. Safe to use whether or not the DB file exists.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._by_parcel: Dict[str, WaterRight] = {}
        self.available: bool = False
        self.warning: Optional[str] = None
        self._load()

    def _load(self) -> None:
        if not self.db_path.exists():
            self.warning = (
                f"Water-rights DB not found at {self.db_path} — skipping "
                f"water-rights enrichment. Run data/db/seed_water_rights.py."
            )
            return
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        except sqlite3.Error as exc:  # pragma: no cover - unusual FS errors
            self.warning = f"Could not open water-rights DB ({exc}); skipping."
            return
        try:
            self._ingest_rows(conn)
            self.available = True
        except sqlite3.Error as exc:
            self.warning = (
                f"Water-rights DB present but unreadable ({exc}); skipping. "
                f"Re-seed with data/db/seed_water_rights.py."
            )
        finally:
            conn.close()

    def _ingest_rows(self, conn: sqlite3.Connection) -> None:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT parcel_id, state, right_type, priority_date, acre_feet, status "
            "FROM water_rights"
        )
        for row in cur:
            wr = WaterRight(
                parcel_id=row["parcel_id"],
                state=row["state"],
                right_type=row["right_type"],
                priority_date=row["priority_date"],
                acre_feet=float(row["acre_feet"] or 0.0),
                status=row["status"] or "",
            )
            self._by_parcel[wr.parcel_id] = wr

    def lookup(self, parcel_id: str) -> Optional[WaterRight]:
        return self._by_parcel.get(parcel_id)

    def __len__(self) -> int:
        return len(self._by_parcel)


def enrich_water_rights(
    parcels: List[Parcel], repo: WaterRightsRepository
) -> int:
    """Attach authoritative water-rights data to parcels in place.

    Returns the number of parcels that matched a DB record. When the repo is
    unavailable this is a no-op returning 0 (parcels keep their existing values).
    """
    matched = 0
    for p in parcels:
        wr = repo.lookup(p.parcel_id)
        if wr is None:
            continue
        matched += 1
        # DB is authoritative for the numeric figure and provenance.
        p.water_rights_acre_feet = wr.acre_feet if wr.is_active else 0.0
        p.water_right_type = wr.right_type
        p.water_right_status = wr.status
        p.water_right_priority_date = wr.priority_date
    return matched
