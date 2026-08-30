// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {IParcelRegistry} from "./interfaces/IParcelRegistry.sol";

/// @title ParcelRegistry
/// @notice Reference implementation of {IParcelRegistry}. The LVE-LAP oracle
///         registers verified parcels; escrow deals read from here.
contract ParcelRegistry is IParcelRegistry, AccessControl {
    /// @notice Role permitted to register / update / retire parcels.
    bytes32 public constant ORACLE_ROLE = keccak256("ORACLE_ROLE");

    mapping(bytes32 => Parcel) private _parcels;
    uint256 private _count;

    constructor(address admin, address oracle) {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(ORACLE_ROLE, oracle);
    }

    /// @inheritdoc IParcelRegistry
    function registerParcel(Parcel calldata parcel) external onlyRole(ORACLE_ROLE) {
        if (_parcels[parcel.parcelId].exists) {
            revert ParcelAlreadyRegistered(parcel.parcelId);
        }
        if (parcel.lasScoreX10 > 1000) {
            revert InvalidScore(parcel.lasScoreX10);
        }

        Parcel storage p = _parcels[parcel.parcelId];
        p.parcelId = parcel.parcelId;
        p.lasScoreX10 = parcel.lasScoreX10;
        p.hbuValueUsd = parcel.hbuValueUsd;
        p.askingPriceUsd = parcel.askingPriceUsd;
        p.arbitrageMultipleBps = parcel.arbitrageMultipleBps;
        p.dossierHash = parcel.dossierHash;
        p.stateCode = parcel.stateCode;
        p.registeredAt = uint64(block.timestamp);
        p.exists = true;

        unchecked {
            ++_count;
        }
        emit ParcelRegistered(
            parcel.parcelId, parcel.lasScoreX10, parcel.hbuValueUsd, parcel.dossierHash
        );
    }

    /// @inheritdoc IParcelRegistry
    function updateDossier(bytes32 parcelId_, bytes32 newDossierHash)
        external
        onlyRole(ORACLE_ROLE)
    {
        Parcel storage p = _parcels[parcelId_];
        if (!p.exists) revert ParcelNotFound(parcelId_);
        p.dossierHash = newDossierHash;
        emit ParcelDossierUpdated(parcelId_, newDossierHash);
    }

    /// @inheritdoc IParcelRegistry
    function retireParcel(bytes32 parcelId_) external onlyRole(ORACLE_ROLE) {
        Parcel storage p = _parcels[parcelId_];
        if (!p.exists) revert ParcelNotFound(parcelId_);
        p.exists = false;
        unchecked {
            --_count;
        }
        emit ParcelRetired(parcelId_);
    }

    /// @inheritdoc IParcelRegistry
    function getParcel(bytes32 parcelId_) external view returns (Parcel memory) {
        Parcel memory p = _parcels[parcelId_];
        if (!p.exists) revert ParcelNotFound(parcelId_);
        return p;
    }

    /// @inheritdoc IParcelRegistry
    function isRegistered(bytes32 parcelId_) external view returns (bool) {
        return _parcels[parcelId_].exists;
    }

    /// @inheritdoc IParcelRegistry
    function parcelCount() external view returns (uint256) {
        return _count;
    }
}
