// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.28;

import {CoreSimulatorLib} from "@hyper-evm-lib/test/simulation/CoreSimulatorLib.sol";
import {HLConstants} from "@hyper-evm-lib/src/common/HLConstants.sol";

import {Cancel} from "../src/Types.sol";
import {Pool} from "../src/Pool.sol";
import {ChallengeAccount} from "../src/ChallengeAccount.sol";
import {RuledAccount} from "../src/RuledAccount.sol";
import {PoolHarness} from "./PoolHarness.sol";

/// 18 October 2026: HyperCore forgot a live challenge's agent, approved through CoreWriter with no
/// name fourteen days before, and nothing on chain could approve it again. An agent approved with no
/// name lives for a time HyperCore does not show, and a cleanup pass removes it after; one approved by
/// name carries its term, `valid_until <ms>`, which CoreWriter takes and HyperCore shows (КОЛИЗЕЙ-2,
/// 19 October 2026). Every agent is now approved as `pools` for 180 days, the most HyperCore takes,
/// and an account that trades has `reapprove`: open to anyone, at most once an hour, it approves the
/// key the account already has under the same name for a new term, so it gives nobody a key they did
/// not have.
contract AgentReapproveTest is PoolHarness {

    function _expectApprovalOf(address key, string memory name) internal {
        vm.expectCall(
            CORE_WRITER,
            abi.encodeWithSignature(
                "sendRawAction(bytes)", abi.encodePacked(uint8(1), HLConstants.ADD_API_WALLET_ACTION, abi.encode(key, name))
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
        _expectApprovalOf(key, _agentName(block.timestamp));
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

    /// The name, byte for byte: approved again at 1,790,000,000, the key is `pools` until 180 days
    /// on, in milliseconds; and the stop's keyless address takes that very name, term and all, so it
    /// replaces the key whichever part of the name HyperCore takes for the name.
    function test_agent_isNamedPools_untilTheEndOfItsTerm_andTheStopTakesTheSameName() public {
        Pool p = _readyPool();
        ChallengeAccount ch = _started(p);
        assertEq(ch.AGENT_TERM(), 180 days);
        address key = ch.agentKey();
        vm.warp(1_790_000_000);
        vm.recordLogs();
        ch.reapprove();
        (address[] memory from, uint24[] memory kind, bytes[] memory args) = _actions(vm.getRecordedLogs());
        assertEq(kind.length, 1);
        assertEq(from[0], address(ch));
        assertEq(kind[0], 9);
        assertEq(args[0], abi.encode(key, string("pools valid_until 1805552000000")));
        assertEq(ch.agentValidUntil(), 1_805_552_000_000);
        vm.warp(1_790_000_000 + 1 days);
        (Cancel[] memory c, uint32[] memory a) = _none();
        vm.recordLogs();
        vm.prank(trader);
        ch.forfeit(c, a, SALT);
        (from, kind, args) = _actions(vm.getRecordedLogs());
        assertEq(kind[0], 9);
        (address keyless, string memory name) = abi.decode(args[0], (address, string));
        assertTrue(keyless != key && keyless != address(0));
        assertEq(name, "pools valid_until 1805552000000");
        vm.recordLogs();
        ch.recut(bytes32(uint256(7)));
        (from, kind, args) = _actions(vm.getRecordedLogs());
        (, name) = abi.decode(args[0], (address, string));
        assertEq(name, "pools valid_until 1805552000000", "a recut too");
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
        _expectApprovalOf(key, _agentName(block.timestamp));
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
