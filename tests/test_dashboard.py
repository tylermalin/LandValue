"""Smoke test for the Streamlit dashboard (Phase 3).

Uses Streamlit's AppTest harness to run the whole script headlessly and assert
it renders without raising. Skipped when Streamlit isn't installed so the core
suite stays dependency-light.
"""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit", reason="streamlit not installed")

from pathlib import Path

from streamlit.testing.v1 import AppTest

DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard.py"


def test_dashboard_runs_and_renders_core_widgets():
    # One full run asserts both "no exception" and "core widgets present" — the
    # AppTest run is the expensive part (it parses real GIS), so we do it once.
    at = AppTest.from_file(str(DASHBOARD), default_timeout=90).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert "Latent Arbitrage Matrix" in [t.value for t in at.title]
    labels = {m.label for m in at.metric}
    assert {"Ingested", "Passed gates", "Disqualified"} <= labels
    assert len(at.dataframe) >= 1  # the Top-N matrix
