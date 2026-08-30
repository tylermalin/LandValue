// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {IMilestoneEscrow} from "./interfaces/IMilestoneEscrow.sol";

/// @title MilestoneEscrow
/// @notice Milestone-gated capital drawdown for one verified parcel. Investors
///         fund in an ERC-20; the developer draws each tranche only after the
///         oracle verifies the corresponding milestone. Milestones advance
///         strictly in order (the roadmap's 4 stages).
/// @dev DEMO / TESTNET SCAFFOLD — unaudited. Do not route mainnet value.
contract MilestoneEscrow is IMilestoneEscrow, AccessControl, ReentrancyGuard {
    using SafeERC20 for IERC20;

    bytes32 public constant ORACLE_ROLE = keccak256("ORACLE_ROLE");
    bytes32 public constant DEVELOPER_ROLE = keccak256("DEVELOPER_ROLE");

    bytes32 private immutable _parcelId;
    IERC20 private immutable _fundingToken;
    uint256 private immutable _fundingTarget;
    address private immutable _developerPayout;

    DealState private _dealState;
    uint256 private _totalDeposited;
    uint256 private _activeIndex;
    Milestone[] private _milestones;
    mapping(address => uint256) private _deposits;

    // Snapshot captured at abort() so refunds are a stable pro-rata split.
    uint256 private _refundBasis;
    uint256 private _abortBalance;

    /// @param parcelId_        registry parcel id this deal funds
    /// @param fundingToken_    ERC-20 used for deposits and drawdowns
    /// @param developerPayout_ address that receives approved tranches
    /// @param admin            holds DEFAULT_ADMIN_ROLE
    /// @param oracle           verifies milestones (ORACLE_ROLE)
    /// @param developer        submits evidence + draws funds (DEVELOPER_ROLE)
    /// @param titles           milestone titles, in execution order
    /// @param tranches         per-milestone capital; sum MUST equal target
    constructor(
        bytes32 parcelId_,
        IERC20 fundingToken_,
        address developerPayout_,
        address admin,
        address oracle,
        address developer,
        string[] memory titles,
        uint256[] memory tranches
    ) {
        require(titles.length == tranches.length && titles.length > 0, "milestones mismatch");
        require(developerPayout_ != address(0), "payout=0");

        uint256 sum;
        for (uint256 i; i < tranches.length; ++i) {
            require(tranches[i] > 0, "tranche=0");
            _milestones.push(Milestone({
                title: titles[i],
                trancheAmount: tranches[i],
                evidenceHash: bytes32(0),
                status: MilestoneStatus.Locked
            }));
            sum += tranches[i];
        }
        require(sum > 0, "target=0");

        _parcelId = parcelId_;
        _fundingToken = fundingToken_;
        _fundingTarget = sum;
        _developerPayout = developerPayout_;
        _dealState = DealState.Funding;

        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(ORACLE_ROLE, oracle);
        _grantRole(DEVELOPER_ROLE, developer);
    }

    // --- funding -------------------------------------------------------------

    /// @inheritdoc IMilestoneEscrow
    function deposit(uint256 amount) external nonReentrant {
        if (_dealState != DealState.Funding) {
            revert WrongDealState(DealState.Funding, _dealState);
        }
        if (amount == 0) revert ZeroAmount();

        uint256 remaining = _fundingTarget - _totalDeposited;
        if (amount > remaining) revert FundingTargetExceeded(amount, remaining);

        _fundingToken.safeTransferFrom(msg.sender, address(this), amount);
        _deposits[msg.sender] += amount;
        _totalDeposited += amount;
        emit Deposited(msg.sender, amount, _totalDeposited);

        // Fully funded -> activate the deal and open the first milestone.
        if (_totalDeposited == _fundingTarget) {
            _dealState = DealState.Active;
            _milestones[0].status = MilestoneStatus.Active;
            emit DealActivated(_totalDeposited);
        }
    }

    // --- milestone state machine --------------------------------------------

    /// @inheritdoc IMilestoneEscrow
    function submitMilestone(uint256 index, bytes32 evidenceHash)
        external
        onlyRole(DEVELOPER_ROLE)
    {
        _requireActiveDeal();
        _requireCurrent(index);
        Milestone storage m = _milestones[index];
        // Re-submittable from Active (first try) or Rejected (after a bounce).
        if (m.status != MilestoneStatus.Active && m.status != MilestoneStatus.Rejected) {
            revert WrongMilestoneStatus(index, MilestoneStatus.Active, m.status);
        }
        m.evidenceHash = evidenceHash;
        m.status = MilestoneStatus.Submitted;
        emit MilestoneSubmitted(index, evidenceHash);
    }

    /// @inheritdoc IMilestoneEscrow
    function approveMilestone(uint256 index) external onlyRole(ORACLE_ROLE) {
        _requireActiveDeal();
        _requireCurrent(index);
        Milestone storage m = _milestones[index];
        if (m.status != MilestoneStatus.Submitted) {
            revert WrongMilestoneStatus(index, MilestoneStatus.Submitted, m.status);
        }
        m.status = MilestoneStatus.Approved;
        emit MilestoneApproved(index);
    }

    /// @inheritdoc IMilestoneEscrow
    function rejectMilestone(uint256 index, string calldata reason)
        external
        onlyRole(ORACLE_ROLE)
    {
        _requireActiveDeal();
        _requireCurrent(index);
        Milestone storage m = _milestones[index];
        if (m.status != MilestoneStatus.Submitted) {
            revert WrongMilestoneStatus(index, MilestoneStatus.Submitted, m.status);
        }
        m.status = MilestoneStatus.Rejected;
        emit MilestoneRejected(index, reason);
    }

    /// @inheritdoc IMilestoneEscrow
    function drawdown(uint256 index) external onlyRole(DEVELOPER_ROLE) nonReentrant {
        _requireActiveDeal();
        _requireCurrent(index);
        Milestone storage m = _milestones[index];
        if (m.status != MilestoneStatus.Approved) {
            revert WrongMilestoneStatus(index, MilestoneStatus.Approved, m.status);
        }

        m.status = MilestoneStatus.Released;
        uint256 amount = m.trancheAmount;

        // Advance the pointer BEFORE the external transfer (checks-effects-interactions).
        bool isLast = index == _milestones.length - 1;
        if (isLast) {
            _dealState = DealState.Completed;
        } else {
            _activeIndex = index + 1;
            _milestones[_activeIndex].status = MilestoneStatus.Active;
        }

        _fundingToken.safeTransfer(_developerPayout, amount);
        emit Drawdown(index, _developerPayout, amount);
        if (isLast) emit DealCompleted();
    }

    // --- termination ---------------------------------------------------------

    /// @inheritdoc IMilestoneEscrow
    function abort(string calldata reason) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_dealState != DealState.Funding && _dealState != DealState.Active) {
            revert WrongDealState(DealState.Active, _dealState);
        }
        _dealState = DealState.Aborted;
        // Snapshot the remaining balance and deposit basis for pro-rata refunds.
        _abortBalance = _fundingToken.balanceOf(address(this));
        _refundBasis = _totalDeposited;
        emit DealAborted(reason);
    }

    /// @inheritdoc IMilestoneEscrow
    function refund() external nonReentrant {
        if (_dealState != DealState.Aborted) {
            revert WrongDealState(DealState.Aborted, _dealState);
        }
        uint256 principal = _deposits[msg.sender];
        if (principal == 0) revert NothingToRefund(msg.sender);

        // Pro-rata share of the balance that remained at abort time.
        uint256 entitlement = (principal * _abortBalance) / _refundBasis;
        _deposits[msg.sender] = 0;

        if (entitlement > 0) {
            _fundingToken.safeTransfer(msg.sender, entitlement);
        }
        emit Refunded(msg.sender, entitlement);
    }

    // --- internal guards -----------------------------------------------------

    function _requireActiveDeal() private view {
        if (_dealState != DealState.Active) {
            revert WrongDealState(DealState.Active, _dealState);
        }
    }

    function _requireCurrent(uint256 index) private view {
        if (index >= _milestones.length) revert MilestoneOutOfRange(index);
        // Strict ordering: only the active milestone can transition.
        if (index != _activeIndex) {
            revert WrongMilestoneStatus(index, MilestoneStatus.Active, _milestones[index].status);
        }
    }

    // --- views ---------------------------------------------------------------

    function parcelId() external view returns (bytes32) { return _parcelId; }
    function fundingToken() external view returns (address) { return address(_fundingToken); }
    function fundingTarget() external view returns (uint256) { return _fundingTarget; }
    function totalDeposited() external view returns (uint256) { return _totalDeposited; }
    function dealState() external view returns (DealState) { return _dealState; }
    function milestoneCount() external view returns (uint256) { return _milestones.length; }
    function activeMilestone() external view returns (uint256) { return _activeIndex; }
    function depositOf(address investor) external view returns (uint256) {
        return _deposits[investor];
    }

    function getMilestone(uint256 index) external view returns (Milestone memory) {
        if (index >= _milestones.length) revert MilestoneOutOfRange(index);
        return _milestones[index];
    }
}
