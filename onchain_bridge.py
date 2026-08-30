"""
onchain_bridge.py — Bridge from a scored parcel to the on-chain payload (Phase 4).

Converts a `RankedParcel` (the engine's output) into the exact argument shapes
the Solidity contracts expect:

  * `build_registration()`  -> the `IParcelRegistry.Parcel` struct for `registerParcel`
  * `default_milestones()`  -> (titles, tranches) for `MilestoneEscrow` / factory

This module ONLY builds payloads — it never signs or broadcasts a transaction.
Wiring these into a live send (web3.py / a signer) is a deliberate, separate,
human-authorized step. Fixed-point conventions match the interfaces:
LAS*10, arbitrage multiple in basis points (1x = 10_000), whole-USD amounts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

try:
    from eth_utils import keccak  # type: ignore
except ImportError as exc:  # pragma: no cover - dependency hint
    raise ImportError(
        "onchain_bridge requires `eth-utils` for keccak256 "
        "(pip install eth-utils). See requirements-onchain.txt."
    ) from exc

from report_generator import RankedParcel

# Roadmap tranche split: Acquisition, Interconnection, Equity Leverage, Exit/JV.
DEFAULT_MILESTONE_TITLES = [
    "Acquisition",
    "Interconnection & Permitting",
    "Equity Leverage",
    "Exit / JV Build",
]
DEFAULT_MILESTONE_WEIGHTS = [0.40, 0.20, 0.10, 0.30]


@dataclass(frozen=True)
class RegistrationPayload:
    """Mirror of the Solidity `IParcelRegistry.Parcel` struct (input form).

    `registered_at`/`exists` are placeholders the on-chain contract overwrites;
    they're included so the tuple ABI-encodes against the struct directly.
    """

    parcel_id: str            # bytes32 (0x…)
    las_score_x10: int        # uint16
    hbu_value_usd: int        # uint256
    asking_price_usd: int     # uint256
    arbitrage_multiple_bps: int  # uint32
    dossier_hash: str         # bytes32 (0x…)
    state_code: str           # bytes2 (0x….)
    registered_at: int = 0    # uint64 (set on-chain)
    exists: bool = False      # bool   (set on-chain)

    def as_tuple(self) -> tuple:
        """Struct-ordered tuple for web3 contract calls."""
        return (
            self.parcel_id, self.las_score_x10, self.hbu_value_usd,
            self.asking_price_usd, self.arbitrage_multiple_bps,
            self.dossier_hash, self.state_code, self.registered_at, self.exists,
        )

    def as_dict(self) -> dict:
        return {
            "parcelId": self.parcel_id,
            "lasScoreX10": self.las_score_x10,
            "hbuValueUsd": self.hbu_value_usd,
            "askingPriceUsd": self.asking_price_usd,
            "arbitrageMultipleBps": self.arbitrage_multiple_bps,
            "dossierHash": self.dossier_hash,
            "stateCode": self.state_code,
        }


def parcel_id_to_bytes32(parcel_id: str) -> str:
    """keccak256 of the off-chain parcel identifier, as a 0x-prefixed bytes32."""
    return "0x" + keccak(text=parcel_id).hex()


def dossier_hash(content: bytes) -> str:
    """keccak256 of dossier bytes (stand-in for an IPFS CID digest)."""
    return "0x" + keccak(content).hex()


def state_to_bytes2(state: str) -> str:
    """Pack a 2-letter state code into bytes2 (right-padded with 0x00)."""
    raw = (state or "").upper().encode("ascii")[:2]
    raw = raw.ljust(2, b"\x00")
    return "0x" + raw.hex()


def build_registration(
    ranked: RankedParcel, dossier_content: Optional[bytes] = None
) -> RegistrationPayload:
    """Build the registry payload from a scored/valued parcel."""
    p, s, v = ranked.parcel, ranked.score, ranked.valuation

    las_x10 = max(0, min(1000, round(s.total * 10)))
    dhash = (dossier_hash(dossier_content) if dossier_content is not None
             else parcel_id_to_bytes32(f"dossier:{p.parcel_id}"))

    return RegistrationPayload(
        parcel_id=parcel_id_to_bytes32(p.parcel_id),
        las_score_x10=las_x10,
        hbu_value_usd=int(round(v.modeled_hbu_value)),
        asking_price_usd=int(round(p.asking_price)),
        arbitrage_multiple_bps=int(round(v.arbitrage_multiple * 10_000)),
        dossier_hash=dhash,
        state_code=state_to_bytes2(p.state),
    )


def default_milestones(
    total_usd: int, weights: Optional[List[float]] = None
) -> Tuple[List[str], List[int]]:
    """Split a funding target into roadmap tranches that sum EXACTLY to total.

    The escrow constructor requires sum(tranches) == funding target, so any
    rounding remainder is folded into the final tranche.
    """
    weights = weights or DEFAULT_MILESTONE_WEIGHTS
    if len(weights) != len(DEFAULT_MILESTONE_TITLES):
        raise ValueError("weights must match the number of milestone titles")
    if total_usd <= 0:
        raise ValueError("total_usd must be positive")

    tranches = [int(total_usd * w) for w in weights]
    tranches[-1] += total_usd - sum(tranches)  # absorb rounding remainder
    return list(DEFAULT_MILESTONE_TITLES), tranches
