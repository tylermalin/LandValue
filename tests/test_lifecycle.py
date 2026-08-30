"""Tests for the lifecycle-optimization plan."""

from __future__ import annotations

import pytest

from lifecycle import (
    DUE_DILIGENCE_PCT,
    INTERCONNECT_FLOOR,
    build_lifecycle,
    summarize_lifecycle,
)
from parcels import Parcel
from valuation import model_hbu


def _valued(headroom=45.0, **kw):
    base = dict(parcel_id="P", county="C", state="NV", lat=37.0, lon=-117.0,
                acres=640, asking_price=920_000)
    base.update(kw)
    p = Parcel(**base)
    p.nearest_substation_headroom_mw = headroom
    return p, model_hbu(p)


def test_four_stages_in_order():
    p, v = _valued()
    stages = build_lifecycle(p, v)
    assert [s.stage for s in stages] == [1, 2, 3, 4]
    assert [s.title for s in stages] == [
        "Acquisition", "Interconnection & Permitting", "Equity Leverage", "Exit / JV Build"]


def test_stage2_unlocks_energy_value():
    p, v = _valued(headroom=50.0)
    stages = build_lifecycle(p, v)
    assert stages[1].value_unlocked_usd == pytest.approx(v.energy_component)


def test_acquisition_capital_includes_due_diligence():
    p, v = _valued()
    stages = build_lifecycle(p, v)
    assert stages[0].capital_required_usd == pytest.approx(
        p.asking_price * (1 + DUE_DILIGENCE_PCT))


def test_interconnect_floor_applies_when_energy_small():
    p, v = _valued(headroom=1.0)  # tiny energy value -> floor kicks in
    stages = build_lifecycle(p, v)
    assert stages[1].capital_required_usd == pytest.approx(INTERCONNECT_FLOOR)


def test_stage4_has_exit_options():
    p, v = _valued()
    stages = build_lifecycle(p, v)
    assert stages[3].exit_options
    assert stages[3].value_unlocked_usd == pytest.approx(v.modeled_hbu_value)


def test_every_stage_maps_to_an_escrow_milestone():
    p, v = _valued()
    for i, st in enumerate(build_lifecycle(p, v), start=1):
        assert f"Milestone {i}" in st.milestone_gate


def test_summary_net_value():
    p, v = _valued()
    stages = build_lifecycle(p, v)
    s = summarize_lifecycle(stages, v)
    assert s.total_capital_usd == pytest.approx(sum(x.capital_required_usd for x in stages))
    assert s.modeled_net_value_usd == pytest.approx(
        v.modeled_hbu_value - s.total_capital_usd)
