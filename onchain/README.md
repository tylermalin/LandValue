# LVE-LAP On-Chain (Phase 4) — Milestone-Gated Capital Drawdowns

> ⚠️ **Demo / testnet scaffold. Unaudited. Do not route mainnet value.**
> These contracts move ERC-20 tokens on-chain; deploy only to a testnet
> (Base Sepolia) and only with test tokens until independently audited.

Tokenized state-machine execution for capital drawdowns on parcels the
Land Value Engine has verified off-chain. Capital is released to the developer
**tranche-by-tranche**, each unlocked only when the oracle verifies the
corresponding milestone — the four roadmap stages:

```
Acquisition → Interconnection & Permitting → Equity Leverage → Exit / JV Build
```

## Contracts

| Contract | Role |
|---|---|
| `ParcelRegistry` | On-chain record of verified parcels (LAS, HBU value, dossier hash). The trust anchor — a deal can only open against a registered parcel. |
| `EscrowFactory` | Deploys one `MilestoneEscrow` per registered parcel; enforces "verified first." |
| `MilestoneEscrow` | The state machine: investors fund an ERC-20; developer draws each tranche after oracle approval. Supports reject/resubmit, abort, and pro-rata refunds. |

Interfaces live in `src/interfaces/`.

## State machine

**Deal:** `Funding → Active → Completed` (or `Aborted`).
Funding activates automatically when deposits reach the target (= Σ tranches).

**Per milestone (strictly in order):**
```
Locked → Active → Submitted → Approved → Released
                     │
                     └─ Rejected ──► (resubmit) ──► Submitted
```
- `submitMilestone` (developer) attaches an evidence hash.
- `approveMilestone` / `rejectMilestone` (oracle) verifies it.
- `drawdown` (developer) releases the tranche to the payout address and opens
  the next milestone; the last one completes the deal.
- `abort` (admin) snapshots the balance; investors `refund` pro-rata.

Roles: `DEFAULT_ADMIN_ROLE`, `ORACLE_ROLE`, `DEVELOPER_ROLE` (OpenZeppelin
`AccessControl`); `ReentrancyGuard` + `SafeERC20` on all value transfers.

## Build & test

```bash
cd onchain
forge install foundry-rs/forge-std openzeppelin/openzeppelin-contracts --no-git
forge build
forge test            # 17 tests: lifecycle, ordering, reject/resubmit, access, refunds
```

## Deploy (testnet)

```bash
export BASE_SEPOLIA_RPC_URL=...     # never commit
export ADMIN_ADDRESS=0x...
export ORACLE_ADDRESS=0x...
forge script script/Deploy.s.sol --rpc-url base_sepolia --broadcast --verify
```

## Bridge from the engine

`../onchain_bridge.py` turns a scored `RankedParcel` into the exact call
arguments — `registerParcel` struct + escrow `(titles, tranches)`:

```python
from onchain_bridge import build_registration, default_milestones
reg = build_registration(ranked_parcel)          # parcelId=keccak256, LAS×10, bps…
titles, tranches = default_milestones(reg.hbu_value_usd)  # sums exactly to target
```

Conventions: `lasScoreX10` = LAS×10, `arbitrageMultipleBps` = multiple×10 000
(1x = 10 000), amounts in whole USD, `parcelId` = `keccak256(id)`,
`stateCode` = 2-byte ASCII.

**The bridge only builds payloads — it never signs or broadcasts.** Sending a
transaction is a separate, explicit, human-authorized step.
