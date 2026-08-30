// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {ParcelRegistry} from "../src/ParcelRegistry.sol";
import {EscrowFactory} from "../src/EscrowFactory.sol";
import {IParcelRegistry} from "../src/interfaces/IParcelRegistry.sol";

/// @notice Deploys the ParcelRegistry + EscrowFactory. Roles come from env:
///         ADMIN_ADDRESS and ORACLE_ADDRESS. Run against Base Sepolia first.
///
///   forge script script/Deploy.s.sol \
///     --rpc-url base_sepolia --broadcast --verify
contract Deploy is Script {
    function run() external returns (ParcelRegistry registry, EscrowFactory factory) {
        address admin = vm.envAddress("ADMIN_ADDRESS");
        address oracle = vm.envAddress("ORACLE_ADDRESS");

        vm.startBroadcast();
        registry = new ParcelRegistry(admin, oracle);
        factory = new EscrowFactory(IParcelRegistry(address(registry)));
        vm.stopBroadcast();

        console2.log("ParcelRegistry:", address(registry));
        console2.log("EscrowFactory: ", address(factory));
    }
}
