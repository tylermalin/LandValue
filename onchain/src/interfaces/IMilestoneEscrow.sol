// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title IMilestoneEscrow
/// @notice Milestone-gated capital drawdown escrow for a single verified parcel.
///         Investors fund the deal in an ERC-20 stablecoin; the developer draws
///         capital tranche-by-tranche only as each milestone is verified by the
///         oracle. Mirrors the LVE-LAP 4-stage execution roadmap.
/// @dev Demo / testnet scaffold. No mainnet value should flow through an
///      unaudited deployment.
interface IMilestoneEscrow {
    /// Deal-level lifecycle.
    enum DealState {
        Funding,    // accepting investor deposits toward the target
        Active,     // fully funded; milestones in progress
        Completed,  // every milestone released
        Aborted     // cancelled; investors may reclaim remaining balance
    }

    /// Per-milestone lifecycle. Milestones advance strictly in order.
    enum MilestoneStatus {
        Locked,     // prior milestone not yet released
        Active,     // eligible for evidence submission
        Submitted,  // developer submitted completion evidence
        Approved,   // oracle verified; tranche unlocked for drawdown
        Released,   // tranche drawn by developer
        Rejected    // oracle rejected; returns to Active for re-submission
    }

    /// Stages align 1:1 with the roadmap: Acquisition, Interconnection/Permitting,
    /// Equity Leverage, Exit/JV Build.
    struct Milestone {
        string title;
        uint256 trancheAmount;   // capital released on approval, in funding token units
        bytes32 evidenceHash;    // hash of off-chain completion evidence
        MilestoneStatus status;
    }

    event Deposited(address indexed investor, uint256 amount, uint256 totalDeposited);
    event DealActivated(uint256 totalDeposited);
    event MilestoneSubmitted(uint256 indexed index, bytes32 evidenceHash);
    event MilestoneApproved(uint256 indexed index);
    event MilestoneRejected(uint256 indexed index, string reason);
    event Drawdown(uint256 indexed index, address indexed to, uint256 amount);
    event DealCompleted();
    event DealAborted(string reason);
    event Refunded(address indexed investor, uint256 amount);

    error WrongDealState(DealState expected, DealState actual);
    error WrongMilestoneStatus(uint256 index, MilestoneStatus expected, MilestoneStatus actual);
    error MilestoneOutOfRange(uint256 index);
    error FundingTargetExceeded(uint256 attempted, uint256 remaining);
    error NothingToRefund(address investor);
    error ZeroAmount();

    // --- funding ---
    function deposit(uint256 amount) external;

    // --- milestone state machine ---
    function submitMilestone(uint256 index, bytes32 evidenceHash) external;
    function approveMilestone(uint256 index) external;
    function rejectMilestone(uint256 index, string calldata reason) external;
    function drawdown(uint256 index) external;

    // --- termination ---
    function abort(string calldata reason) external;
    function refund() external;

    // --- views ---
    function parcelId() external view returns (bytes32);
    function fundingToken() external view returns (address);
    function fundingTarget() external view returns (uint256);
    function totalDeposited() external view returns (uint256);
    function dealState() external view returns (DealState);
    function milestoneCount() external view returns (uint256);
    function getMilestone(uint256 index) external view returns (Milestone memory);
    function activeMilestone() external view returns (uint256);
    function depositOf(address investor) external view returns (uint256);
}
