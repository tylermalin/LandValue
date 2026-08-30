"""Tests for the on-chain payload bridge (Phase 4).

These verify the payload SHAPE and encoding only — no chain interaction.
"""

from __future__ import annotations

import pytest

pytest.importorskip("eth_utils", reason="eth-utils not installed")

from onchain_bridge import (  # noqa: E402
    DEFAULT_MILESTONE_TITLES,
    build_registration,
    default_milestones,
    dossier_hash,
    parcel_id_to_bytes32,
    state_to_bytes2,
)
from report_generator import RankedParcel  # noqa: E402
from scoring import score_parcel  # noqa: E402
from valuation import model_hbu  # noqa: E402


def _ranked(base_parcel):
    base_parcel.transmission_distance_miles = 0.5
    base_parcel.nearest_substation_headroom_mw = 45.0
    s = score_parcel(base_parcel, baseline_per_acre=6000.0,
                     dom_threshold=90, min_headroom_mw=10.0)
    v = model_hbu(base_parcel)
    return RankedParcel(parcel=base_parcel, score=s, valuation=v, rank=1)


# --- keccak / encoding primitives (known vectors) ----------------------------
def test_keccak_empty_vector():
    # keccak256("") — canonical Ethereum test vector.
    assert dossier_hash(b"") == \
        "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"


def test_parcel_id_is_bytes32_hex():
    out = parcel_id_to_bytes32("NV-ESM-0417")
    assert out.startswith("0x") and len(out) == 66  # 0x + 64 hex chars


def test_parcel_id_is_deterministic():
    assert parcel_id_to_bytes32("X") == parcel_id_to_bytes32("X")
    assert parcel_id_to_bytes32("X") != parcel_id_to_bytes32("Y")


def test_state_to_bytes2():
    assert state_to_bytes2("NV") == "0x4e56"   # 'N'=0x4e, 'V'=0x56
    assert state_to_bytes2("A") == "0x4100"    # right-padded
    assert state_to_bytes2("") == "0x0000"


# --- registration payload ----------------------------------------------------
def test_build_registration_fixed_point_conventions(base_parcel):
    ranked = _ranked(base_parcel)
    reg = build_registration(ranked)

    # LAS 89.5 -> 895 (x10).
    assert reg.las_score_x10 == round(ranked.score.total * 10)
    # Multiple in basis points (6.48x -> 64800).
    assert reg.arbitrage_multiple_bps == round(ranked.valuation.arbitrage_multiple * 10_000)
    assert reg.hbu_value_usd == round(ranked.valuation.modeled_hbu_value)
    assert reg.asking_price_usd == round(base_parcel.asking_price)
    assert reg.state_code == "0x4e56"


def test_las_score_is_clamped(base_parcel):
    ranked = _ranked(base_parcel)
    reg = build_registration(ranked)
    assert 0 <= reg.las_score_x10 <= 1000


def test_registration_tuple_arity_matches_struct(base_parcel):
    reg = build_registration(_ranked(base_parcel))
    # IParcelRegistry.Parcel has 9 fields.
    assert len(reg.as_tuple()) == 9
    assert reg.as_dict()["parcelId"].startswith("0x")


def test_dossier_content_hash_differs_from_placeholder(base_parcel):
    ranked = _ranked(base_parcel)
    with_content = build_registration(ranked, dossier_content=b"PDF-BYTES")
    without = build_registration(ranked)
    assert with_content.dossier_hash != without.dossier_hash
    assert with_content.dossier_hash == dossier_hash(b"PDF-BYTES")


# --- milestones --------------------------------------------------------------
def test_default_milestones_sum_exactly_to_target():
    titles, tranches = default_milestones(1_000_000)
    assert titles == DEFAULT_MILESTONE_TITLES
    assert sum(tranches) == 1_000_000
    assert all(t > 0 for t in tranches)


def test_default_milestones_absorbs_rounding_remainder():
    # 333333 * weights won't divide evenly; sum must still be exact.
    titles, tranches = default_milestones(333_333)
    assert sum(tranches) == 333_333


def test_default_milestones_rejects_nonpositive():
    with pytest.raises(ValueError):
        default_milestones(0)


def test_default_milestones_rejects_bad_weights():
    with pytest.raises(ValueError):
        default_milestones(1000, weights=[0.5, 0.5])
