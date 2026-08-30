// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IParcelRegistry} from "./interfaces/IParcelRegistry.sol";
import {MilestoneEscrow} from "./MilestoneEscrow.sol";

/// @title EscrowFactory
/// @notice Deploys one {MilestoneEscrow} per verified parcel. A deal can only be
///         opened against a parcel that is currently registered in the
///         {IParcelRegistry} — the on-chain enforcement of "verified first".
contract EscrowFactory {
    IParcelRegistry public immutable registry;

    /// @notice parcelId => most recent escrow deployed for it.
    mapping(bytes32 => address) public escrowOf;
    address[] private _allEscrows;

    event EscrowCreated(bytes32 indexed parcelId, address indexed escrow, uint256 fundingTarget);

    error ParcelNotVerified(bytes32 parcelId);

    constructor(IParcelRegistry registry_) {
        registry = registry_;
    }

    /// @notice Deploy a milestone escrow for a registered parcel.
    /// @dev The caller supplies the roles; the factory only enforces that the
    ///      parcel is verified. Tranche sum becomes the funding target.
    function createEscrow(
        bytes32 parcelId,
        IERC20 fundingToken,
        address developerPayout,
        address admin,
        address oracle,
        address developer,
        string[] calldata titles,
        uint256[] calldata tranches
    ) external returns (address escrow) {
        if (!registry.isRegistered(parcelId)) revert ParcelNotVerified(parcelId);

        MilestoneEscrow deployed = new MilestoneEscrow(
            parcelId, fundingToken, developerPayout, admin, oracle, developer, titles, tranches
        );
        escrow = address(deployed);

        escrowOf[parcelId] = escrow;
        _allEscrows.push(escrow);
        emit EscrowCreated(parcelId, escrow, deployed.fundingTarget());
    }

    function allEscrows() external view returns (address[] memory) {
        return _allEscrows;
    }

    function escrowCount() external view returns (uint256) {
        return _allEscrows.length;
    }
}
