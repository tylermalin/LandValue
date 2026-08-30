// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title IParcelRegistry
/// @notice On-chain registry of parcels the Land Value Engine (LVE-LAP) has
///         verified off-chain. A parcel must be registered here before an
///         escrow deal can be opened against it — the registry is the trust
///         anchor linking an off-chain dossier to an on-chain deal.
/// @dev All monetary figures are whole USD (no decimals). Scores are fixed-point:
///      `lasScoreX10` is LAS * 10 (89.5 -> 895); `arbitrageMultipleBps` is the
///      arbitrage multiple in basis points (6.48x -> 64800).
interface IParcelRegistry {
    /// @param parcelId        keccak256 of the off-chain parcel identifier
    /// @param lasScoreX10     Latent Arbitrage Score * 10 (0..1000)
    /// @param hbuValueUsd     modeled highest-and-best-use value, whole USD
    /// @param askingPriceUsd  listed asking price, whole USD
    /// @param arbitrageMultipleBps  HBU/asking in basis points
    /// @param dossierHash     content hash (e.g. IPFS CID digest) of the PDF dossier
    /// @param stateCode       2-letter US state (e.g. "NV") packed into bytes2
    struct Parcel {
        bytes32 parcelId;
        uint16 lasScoreX10;
        uint256 hbuValueUsd;
        uint256 askingPriceUsd;
        uint32 arbitrageMultipleBps;
        bytes32 dossierHash;
        bytes2 stateCode;
        uint64 registeredAt;
        bool exists;
    }

    event ParcelRegistered(
        bytes32 indexed parcelId,
        uint16 lasScoreX10,
        uint256 hbuValueUsd,
        bytes32 dossierHash
    );
    event ParcelDossierUpdated(bytes32 indexed parcelId, bytes32 newDossierHash);
    event ParcelRetired(bytes32 indexed parcelId);

    error ParcelAlreadyRegistered(bytes32 parcelId);
    error ParcelNotFound(bytes32 parcelId);
    error InvalidScore(uint16 lasScoreX10);

    /// @notice Register a newly verified parcel. Restricted to the oracle role.
    function registerParcel(Parcel calldata parcel) external;

    /// @notice Point a parcel at a new dossier hash (re-scored / updated dossier).
    function updateDossier(bytes32 parcelId, bytes32 newDossierHash) external;

    /// @notice Mark a parcel retired (no longer eligible for new deals).
    function retireParcel(bytes32 parcelId) external;

    function getParcel(bytes32 parcelId) external view returns (Parcel memory);
    function isRegistered(bytes32 parcelId) external view returns (bool);
    function parcelCount() external view returns (uint256);
}
