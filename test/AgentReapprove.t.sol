// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.28;

import {CoreSimulatorLib} from "@hyper-evm-lib/test/simulation/CoreSimulatorLib.sol";
import {HLConstants} from "@hyper-evm-lib/src/common/HLConstants.sol";

import {Cancel} from "../src/Types.sol";
import {Pool} from "../src/Pool.sol";
import {ChallengeAccount} from "../src/ChallengeAccount.sol";
import {RuledAccount} from "../src/RuledAccount.sol";
import {PoolHarness} from "./PoolHarness.sol";

/// 19 October 2026: HyperCore forgot a live challenge's agent, approved through CoreWriter with no
/// name fourteen days before, and nothing on chain could approve it again (КОЛИЗЕЙ-2's report). An
/// account that trades now has `reapprove`: open to anyone, at most once an hour, it sends HyperCore
/// the approval of the key the account already has, so it gives nobody a key they did not have.
contract AgentReapproveTest is PoolHarness {

    function _expectApprovalOf(address key) internal {
        vm.expectCall(
            CORE_WRITER,
            abi.encodeWithSignature(
                "sendRawAction(bytes)", abi.encodePacked(uint8(1), HLConstants.ADD_API_WALLET_ACTION, abi.encode(key, ""))
            )
        );
    }

    function _ownerFunded() internal returns (Pool p) {
        p = _readyPool();
        vm.startPrank(investor);
        p.runByOwner(false);
        p.openFundedFor(trader);
        vm.stopPrank();
        CoreSimulatorLib.nextBlock();
    }

    /// A challenge that trades: anyone asks again for the same key, and the key does not change.
    function test_reapprove_aChallengeThatTradesIsAskedAgainForItsOwnKey() public {
        ChallengeAccount ch = _started(_readyPool());
        address key = ch.agentKey();
        assertTrue(key != address(0));
        uint64 at = ch.agentApprovedAt();
        assertEq(at, ch.startedAt(), "approved at the activation");
        vm.prank(stranger);
        vm.expectRevert(abi.encodeWithSelector(RuledAccount.ApprovedLately.selector, at + 1 hours));
        ch.reapprove();
        vm.warp(uint256(at) + 1 hours);
        _expectApprovalOf(key);
        vm.expectEmit(true, false, false, false, address(ch));
        emit RuledAccount.AgentApproved(key);
        vm.prank(stranger);
        ch.reapprove();
        assertEq(ch.agentKey(), key, "the same key");
        assertEq(ch.agentApprovedAt(), block.timestamp);
        assertTrue(registry.isBound(key, address(ch), trader), "still bound to this account and its trader");
        vm.expectRevert(abi.encodeWithSelector(RuledAccount.ApprovedLately.selector, uint64(block.timestamp + 1 hours)));
        ch.reapprove();
    }

    /// Only while it trades: not before the activation, and not once it has stopped.
    function test_reapprove_aChallengeThatDoesNotTradeIsRefused() public {
        Pool p = _readyPool();
        ChallengeAccount ch = _buy(p);
        vm.expectRevert(abi.encodeWithSelector(ChallengeAccount.BadStatus.selector, ChallengeAccount.Status.Created));
        ch.reapprove();
        CoreSimulatorLib.nextBlock();
        ch.activate();
        CoreSimulatorLib.nextBlock();
        vm.warp(block.timestamp + 1 hours);
        (Cancel[] memory c, uint32[] memory a) = _none();
        vm.prank(trader);
        ch.forfeit(c, a, SALT);
        vm.expectRevert(abi.encodeWithSelector(ChallengeAccount.BadStatus.selector, ChallengeAccount.Status.Forfeited));
        ch.reapprove();
    }

    /// A funded stage, a pool's own or a MultiPool's seat alike: asked again for its own key; not
    /// before the stage opens, and not once it closes.
    function test_reapprove_aFundedStageIsAskedAgainForItsOwnKey_andOnlyWhileFunded() public {
        Pool idle = _readyPool();
        vm.expectRevert(abi.encodeWithSelector(Pool.BadStage.selector, Pool.Stage.Idle));
        idle.reapprove();
        Pool p = _ownerFunded();
        address key = p.agentKey();
        vm.warp(block.timestamp + 1 hours);
        _expectApprovalOf(key);
        p.reapprove();
        assertEq(p.agentKey(), key);
        assertTrue(registry.isBound(key, address(p), trader));
        (Cancel[] memory c, uint32[] memory a) = _none();
        vm.prank(investor);
        p.stopFunded(c, a, SALT);
        vm.warp(block.timestamp + 1 hours);
        vm.expectRevert(abi.encodeWithSelector(Pool.BadStage.selector, Pool.Stage.Closing));
        p.reapprove();
    }
}
