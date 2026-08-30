// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {MilestoneEscrow} from "../src/MilestoneEscrow.sol";
import {IMilestoneEscrow} from "../src/interfaces/IMilestoneEscrow.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";
import {MockERC20} from "./mocks/MockERC20.sol";

contract MilestoneEscrowTest is Test {
    MilestoneEscrow internal escrow;
    MockERC20 internal token;

    address internal admin = makeAddr("admin");
    address internal oracle = makeAddr("oracle");
    address internal developer = makeAddr("developer");
    address internal payout = makeAddr("payout");
    address internal alice = makeAddr("alice");
    address internal bob = makeAddr("bob");

    bytes32 internal constant PARCEL = keccak256("NV-ESM-0417");

    // Four roadmap tranches summing to 1,000,000.
    uint256[] internal tranches = [400_000, 200_000, 100_000, 300_000];
    uint256 internal constant TARGET = 1_000_000;

    function setUp() public {
        token = new MockERC20();
        string[] memory titles = new string[](4);
        titles[0] = "Acquisition";
        titles[1] = "Interconnection & Permitting";
        titles[2] = "Equity Leverage";
        titles[3] = "Exit / JV Build";

        escrow = new MilestoneEscrow(
            PARCEL, IERC20(address(token)), payout, admin, oracle, developer, titles, tranches
        );

        token.mint(alice, TARGET);
        token.mint(bob, TARGET);
    }

    // --- helpers -------------------------------------------------------------
    function _fund(address who, uint256 amount) internal {
        vm.startPrank(who);
        token.approve(address(escrow), amount);
        escrow.deposit(amount);
        vm.stopPrank();
    }

    function _advance(uint256 index) internal {
        vm.prank(developer);
        escrow.submitMilestone(index, keccak256(abi.encode("evidence", index)));
        vm.prank(oracle);
        escrow.approveMilestone(index);
        vm.prank(developer);
        escrow.drawdown(index);
    }

    // --- construction --------------------------------------------------------
    function test_constructor_sets_target_and_milestones() public view {
        assertEq(escrow.fundingTarget(), TARGET);
        assertEq(escrow.milestoneCount(), 4);
        assertEq(uint256(escrow.dealState()), uint256(IMilestoneEscrow.DealState.Funding));
        assertEq(escrow.parcelId(), PARCEL);
    }

    function test_constructor_reverts_on_length_mismatch() public {
        string[] memory titles = new string[](1);
        titles[0] = "only-one";
        uint256[] memory t = new uint256[](2);
        t[0] = 1; t[1] = 2;
        vm.expectRevert(bytes("milestones mismatch"));
        new MilestoneEscrow(PARCEL, IERC20(address(token)), payout, admin, oracle, developer, titles, t);
    }

    // --- funding -------------------------------------------------------------
    function test_partial_then_full_funding_activates_deal() public {
        _fund(alice, 600_000);
        assertEq(uint256(escrow.dealState()), uint256(IMilestoneEscrow.DealState.Funding));
        _fund(bob, 400_000);
        assertEq(uint256(escrow.dealState()), uint256(IMilestoneEscrow.DealState.Active));
        // First milestone opens.
        assertEq(uint256(escrow.getMilestone(0).status), uint256(IMilestoneEscrow.MilestoneStatus.Active));
    }

    function test_overfunding_reverts() public {
        vm.startPrank(alice);
        token.approve(address(escrow), TARGET + 1);
        vm.expectRevert(
            abi.encodeWithSelector(IMilestoneEscrow.FundingTargetExceeded.selector, TARGET + 1, TARGET)
        );
        escrow.deposit(TARGET + 1);
        vm.stopPrank();
    }

    function test_deposit_after_active_reverts() public {
        _fund(alice, TARGET);
        vm.startPrank(bob);
        token.approve(address(escrow), 1);
        vm.expectRevert(
            abi.encodeWithSelector(
                IMilestoneEscrow.WrongDealState.selector,
                IMilestoneEscrow.DealState.Funding,
                IMilestoneEscrow.DealState.Active
            )
        );
        escrow.deposit(1);
        vm.stopPrank();
    }

    // --- happy path ----------------------------------------------------------
    function test_full_lifecycle_releases_every_tranche_in_order() public {
        _fund(alice, TARGET);
        for (uint256 i; i < 4; ++i) {
            _advance(i);
            assertEq(
                uint256(escrow.getMilestone(i).status),
                uint256(IMilestoneEscrow.MilestoneStatus.Released)
            );
        }
        assertEq(uint256(escrow.dealState()), uint256(IMilestoneEscrow.DealState.Completed));
        assertEq(token.balanceOf(payout), TARGET);
        assertEq(token.balanceOf(address(escrow)), 0);
    }

    function test_drawdown_pays_exact_tranche() public {
        _fund(alice, TARGET);
        _advance(0);
        assertEq(token.balanceOf(payout), 400_000);
        assertEq(escrow.activeMilestone(), 1);
    }

    // --- ordering + status guards --------------------------------------------
    function test_cannot_submit_out_of_order() public {
        _fund(alice, TARGET);
        vm.prank(developer);
        vm.expectRevert(); // index 1 is Locked / not the active milestone
        escrow.submitMilestone(1, keccak256("x"));
    }

    function test_cannot_approve_before_submit() public {
        _fund(alice, TARGET);
        vm.prank(oracle);
        vm.expectRevert(
            abi.encodeWithSelector(
                IMilestoneEscrow.WrongMilestoneStatus.selector,
                uint256(0),
                IMilestoneEscrow.MilestoneStatus.Submitted,
                IMilestoneEscrow.MilestoneStatus.Active
            )
        );
        escrow.approveMilestone(0);
    }

    function test_cannot_drawdown_before_approve() public {
        _fund(alice, TARGET);
        vm.prank(developer);
        escrow.submitMilestone(0, keccak256("x"));
        vm.prank(developer);
        vm.expectRevert(); // status Submitted, not Approved
        escrow.drawdown(0);
    }

    // --- reject + resubmit ---------------------------------------------------
    function test_reject_then_resubmit_and_approve() public {
        _fund(alice, TARGET);
        vm.prank(developer);
        escrow.submitMilestone(0, keccak256("bad"));
        vm.prank(oracle);
        escrow.rejectMilestone(0, "insufficient evidence");
        assertEq(uint256(escrow.getMilestone(0).status), uint256(IMilestoneEscrow.MilestoneStatus.Rejected));

        // Developer can resubmit from Rejected.
        vm.prank(developer);
        escrow.submitMilestone(0, keccak256("good"));
        vm.prank(oracle);
        escrow.approveMilestone(0);
        vm.prank(developer);
        escrow.drawdown(0);
        assertEq(token.balanceOf(payout), 400_000);
    }

    // --- access control ------------------------------------------------------
    function test_only_developer_can_submit() public {
        _fund(alice, TARGET);
        // Read the role BEFORE pranking so the view call doesn't consume the prank.
        bytes32 devRole = escrow.DEVELOPER_ROLE();
        vm.expectRevert(
            abi.encodeWithSelector(
                IAccessControl.AccessControlUnauthorizedAccount.selector, alice, devRole
            )
        );
        vm.prank(alice);
        escrow.submitMilestone(0, keccak256("x"));
    }

    function test_only_oracle_can_approve() public {
        _fund(alice, TARGET);
        vm.prank(developer);
        escrow.submitMilestone(0, keccak256("x"));
        vm.prank(developer);
        vm.expectRevert(); // developer lacks ORACLE_ROLE
        escrow.approveMilestone(0);
    }

    // --- abort + pro-rata refund ---------------------------------------------
    function test_abort_during_funding_refunds_full() public {
        _fund(alice, 600_000);
        vm.prank(admin);
        escrow.abort("deal fell through");
        assertEq(uint256(escrow.dealState()), uint256(IMilestoneEscrow.DealState.Aborted));

        uint256 before = token.balanceOf(alice);
        vm.prank(alice);
        escrow.refund();
        assertEq(token.balanceOf(alice) - before, 600_000);
    }

    function test_abort_after_drawdown_refunds_pro_rata() public {
        _fund(alice, 600_000); // 60%
        _fund(bob, 400_000);   // 40%
        _advance(0);           // 400k drawn -> 600k remains

        vm.prank(admin);
        escrow.abort("halted mid-build");

        vm.prank(alice);
        escrow.refund();
        vm.prank(bob);
        escrow.refund();

        // Remaining 600k split 60/40.
        assertEq(token.balanceOf(alice), (TARGET - 600_000) + 360_000);
        assertEq(token.balanceOf(bob), (TARGET - 400_000) + 240_000);
        assertEq(token.balanceOf(payout), 400_000);
    }

    function test_double_refund_reverts() public {
        _fund(alice, TARGET);
        vm.prank(admin);
        escrow.abort("x");
        vm.prank(alice);
        escrow.refund();
        vm.prank(alice);
        vm.expectRevert(
            abi.encodeWithSelector(IMilestoneEscrow.NothingToRefund.selector, alice)
        );
        escrow.refund();
    }

    function test_only_admin_can_abort() public {
        _fund(alice, TARGET);
        vm.prank(developer);
        vm.expectRevert();
        escrow.abort("nope");
    }
}
