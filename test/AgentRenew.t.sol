// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.28;

import {CoreSimulatorLib} from "@hyper-evm-lib/test/simulation/CoreSimulatorLib.sol";

import {Cancel} from "../src/Types.sol";
import {Pool} from "../src/Pool.sol";
import {ChallengeAccount} from "../src/ChallengeAccount.sol";
import {RuledAccount} from "../src/RuledAccount.sol";
import {KeyRegistry} from "../src/KeyRegistry.sol";
import {PoolHarness} from "./PoolHarness.sol";

/// 18 October 2026: HyperCore forgot a live challenge's agent, approved through CoreWriter with no
/// name fourteen days before. КОЛИЗЕЙ-2 measured on testnet on 19 October 2026:
/// - an agent approved by name carries its term, `valid_until <ms>`, and HyperCore shows it;
/// - the name is the part before the term, and an approval under the same name replaces the agent;
/// - a term over 180 days from HyperCore's clock is refused without a word;
/// - a second approval of a key HyperCore holds is ignored.
/// So every agent is `pools` for 179 days. A term that runs out while the account trades is renewed
/// in its last fourteen days with a fresh key for the same trader, and the old key is retired for
/// good. A key HyperCore has let go is never approved again: its signed actions could be sent again
/// once their nonces are pruned (Hyperliquid's docs; audit window 2, A4-1).
contract AgentRenewTest is PoolHarness {
    uint256 constant T0 = 1_790_000_000;

    function _ownerFunded() internal returns (Pool p) {
        p = _readyPool();
        vm.startPrank(investor);
        p.runByOwner(false);
        p.openFundedFor(trader);
        vm.stopPrank();
        CoreSimulatorLib.nextBlock();
    }

    /// A challenge started at T0, and the approvals its activation sent.
    function _startedAtT0() internal returns (ChallengeAccount ch, address[] memory keys_, string[] memory names) {
        ch = _buy(_readyPool());
        CoreSimulatorLib.nextBlock();
        vm.warp(T0);
        vm.recordLogs();
        ch.activate();
        (keys_, names) = _approvals();
        CoreSimulatorLib.nextBlock();
    }

    function _approvals() internal returns (address[] memory keys_, string[] memory names) {
        (address[] memory from, uint24[] memory kind, bytes[] memory args) = _actions(vm.getRecordedLogs());
        uint256 n;
        for (uint256 i = 0; i < kind.length; ++i) {
            if (kind[i] == 9) ++n;
        }
        keys_ = new address[](n);
        names = new string[](n);
        uint256 k;
        for (uint256 i = 0; i < kind.length; ++i) {
            if (kind[i] != 9) continue;
            assertTrue(from[i] != address(0));
            (keys_[k], names[k]) = abi.decode(args[i], (address, string));
            ++k;
        }
    }

    /// Byte for byte: started at T0, the key is `pools` until 179 days on, in milliseconds. Renewed
    /// on the first second of the last fourteen days, the trader's fresh key is `pools` until 179 days
    /// from then, in one approval, and the old key is retired.
    function test_renew_aFreshKeyUnderPoolsFor179Days_byteForByte() public {
        (ChallengeAccount ch, address[] memory keys_, string[] memory names) = _startedAtT0();
        address first = ch.agentKey();
        assertEq(keys_.length, 1);
        assertEq(keys_[0], first);
        assertEq(names[0], "pools valid_until 1805465600000");
        assertEq(ch.AGENT_TERM(), 179 days);
        assertEq(ch.RENEW_WINDOW(), 14 days);
        assertEq(ch.agentValidUntil(), 1_805_465_600_000);

        vm.warp(1_804_256_000); // 179 days less 14 on
        vm.recordLogs();
        vm.prank(stranger);
        ch.renewAgent();
        (keys_, names) = _approvals();
        address fresh = ch.agentKey();
        assertTrue(fresh != first && fresh != address(0));
        assertEq(keys_.length, 1, "one approval");
        assertEq(keys_[0], fresh);
        assertEq(names[0], "pools valid_until 1819721600000");
        assertEq(ch.agentValidUntil(), 1_819_721_600_000);
        assertTrue(registry.isBound(fresh, address(ch), trader), "the same trader");
        assertEq(uint8(registry.bindingOf(first).state), uint8(KeyRegistry.State.Retired), "the old key for good");
    }

    /// Only in the last fourteen days of the term, and not once it has ended. After a renewal the
    /// window is the new term's, so a second renewal waits for it.
    function test_renew_onlyInTheLastFourteenDays_andNotAfterTheEnd() public {
        (ChallengeAccount ch,,) = _startedAtT0();
        uint64 ends = uint64(T0 + 179 days);
        uint64 opens = ends - 14 days;
        vm.warp(opens - 1);
        vm.expectRevert(abi.encodeWithSelector(RuledAccount.NotRenewable.selector, opens, ends));
        ch.renewAgent();
        vm.warp(ends);
        vm.expectRevert(abi.encodeWithSelector(RuledAccount.NotRenewable.selector, opens, ends));
        ch.renewAgent();
        vm.warp(ends - 1);
        ch.renewAgent();
        uint64 next = uint64(block.timestamp) + 179 days;
        vm.expectRevert(abi.encodeWithSelector(RuledAccount.NotRenewable.selector, next - 14 days, next));
        ch.renewAgent();
    }

    /// No key is ever approved twice: every approval in the account's life names a key never named
    /// before, and every key renewed away is retired.
    function test_renew_anOldKeyIsNeverApprovedAgain() public {
        (ChallengeAccount ch, address[] memory a0,) = _startedAtT0();
        address[] memory seen = new address[](3);
        seen[0] = a0[0];
        for (uint256 r = 1; r < 3; ++r) {
            vm.warp(uint256(ch.agentApprovedAt()) + 179 days - 14 days);
            vm.recordLogs();
            ch.renewAgent();
            (address[] memory a,) = _approvals();
            assertEq(a.length, 1);
            for (uint256 j = 0; j < r; ++j) {
                assertTrue(a[0] != seen[j], "a key never approved before");
                assertEq(uint8(registry.bindingOf(seen[j]).state), uint8(KeyRegistry.State.Retired));
            }
            seen[r] = a[0];
        }
        assertEq(ch.agentKey(), seen[2]);
    }

    /// The stop after a renewal cuts the fresh key: the keyless address under `pools`, for a term
    /// from now, which replaces whatever HyperCore holds under that name.
    function test_renew_theStopAfterARenewalCutsTheFreshKey() public {
        (ChallengeAccount ch,,) = _startedAtT0();
        vm.warp(uint256(ch.agentApprovedAt()) + 179 days - 14 days);
        ch.renewAgent();
        address fresh = ch.agentKey();
        vm.warp(block.timestamp + 1 days);
        (Cancel[] memory c, uint32[] memory a) = _none();
        vm.recordLogs();
        vm.prank(trader);
        ch.forfeit(c, a, SALT);
        (address[] memory keys_, string[] memory names) = _approvals();
        assertTrue(keys_[0] != fresh && keys_[0] != address(0), "a keyless address");
        assertEq(names[0], _agentName(block.timestamp));
        assertEq(ch.agentKey(), address(0));
        assertEq(uint8(registry.bindingOf(fresh).state), uint8(KeyRegistry.State.Retired));
        vm.recordLogs();
        ch.recut(bytes32(uint256(7)));
        (keys_, names) = _approvals();
        assertEq(names[0], _agentName(block.timestamp), "a recut too");
    }

    /// Only while the challenge trades: not before the activation, and not once it has stopped.
    function test_renew_aChallengeThatDoesNotTradeIsRefused() public {
        Pool p = _readyPool();
        ChallengeAccount ch = _buy(p);
        vm.expectRevert(abi.encodeWithSelector(ChallengeAccount.BadStatus.selector, ChallengeAccount.Status.Created));
        ch.renewAgent();
        CoreSimulatorLib.nextBlock();
        ch.activate();
        CoreSimulatorLib.nextBlock();
        (Cancel[] memory c, uint32[] memory a) = _none();
        vm.prank(trader);
        ch.forfeit(c, a, SALT);
        vm.warp(uint256(ch.agentApprovedAt()) + 179 days - 14 days);
        vm.expectRevert(abi.encodeWithSelector(ChallengeAccount.BadStatus.selector, ChallengeAccount.Status.Forfeited));
        ch.renewAgent();
    }

    /// A funded stage renews for its funded trader, and only while it is funded.
    function test_renew_aFundedStageGetsAFreshKeyForItsTrader_andOnlyWhileFunded() public {
        Pool idle = _readyPool();
        vm.expectRevert(abi.encodeWithSelector(Pool.BadStage.selector, Pool.Stage.Idle));
        idle.renewAgent();
        Pool p = _ownerFunded();
        address key = p.agentKey();
        vm.warp(uint256(p.agentApprovedAt()) + 179 days - 14 days);
        p.renewAgent();
        address fresh = p.agentKey();
        assertTrue(fresh != key);
        assertTrue(registry.isBound(fresh, address(p), trader));
        assertEq(uint8(registry.bindingOf(key).state), uint8(KeyRegistry.State.Retired));
        (Cancel[] memory c, uint32[] memory a) = _none();
        vm.prank(investor);
        p.stopFunded(c, a, SALT);
        vm.warp(uint256(p.agentApprovedAt()) + 179 days - 14 days);
        vm.expectRevert(abi.encodeWithSelector(Pool.BadStage.selector, Pool.Stage.Closing));
        p.renewAgent();
    }

    /// With no free key in the registry the renewal is refused whole: the old key stays, approved,
    /// until a key is published.
    function test_renew_noFreeKeyLeavesTheOldKey() public {
        (ChallengeAccount ch,,) = _startedAtT0();
        address key = ch.agentKey();
        for (uint256 i = 0; i < keys.length; ++i) {
            if (registry.bindingOf(keys[i]).state == KeyRegistry.State.Free) {
                CoreSimulatorLib.forceAccountActivation(keys[i]);
            }
        }
        registry.purgeSpoiled(keys.length);
        assertEq(registry.freeCount(), 0);
        vm.warp(uint256(ch.agentApprovedAt()) + 179 days - 14 days);
        vm.expectRevert(KeyRegistry.NoFreeKey.selector);
        ch.renewAgent();
        assertEq(ch.agentKey(), key);
        assertTrue(registry.isBound(key, address(ch), trader));
    }
}
