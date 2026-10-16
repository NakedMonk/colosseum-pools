// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.28;

import {CoreSimulatorLib} from "@hyper-evm-lib/test/simulation/CoreSimulatorLib.sol";

import {PaperExam, ExamTerms} from "../../src/exam/PaperExam.sol";
import {MultiPool, Plan, Size, AfterTarget} from "../../src/multi/MultiPool.sol";
import {PoolHarness} from "../PoolHarness.sol";

/// The deep audit's F4 (16 October 2026), turned round: money reserved for an end that passed may
/// leave once after it, and no more than that part of the pool; any payout spends the reserve. On
/// f8bc60aa a one-unit reserve unlocked any amount on any day of the renewal, and a payout in the
/// window left the reserve for a second one. The stand is the audit's (M2Broad).
contract DeepReserveCapTest is PoolHarness {
    PaperExam examImpl;
    bytes32 constant TERMS = keccak256(bytes(TERMS_TEXT));

    function _examImpl() internal returns (address) {
        if (address(examImpl) == address(0)) examImpl = new PaperExam();
        return address(examImpl);
    }

    /// The plan of test/multi/MultiPool.t.sol: a year's term, 80% in work, the account closes at its target.
    function _plan() internal pure returns (Plan memory p) {
        p.menu = new Size[](3);
        p.menu[0] = Size({size: 5e6, price: 1});
        p.menu[1] = Size({size: 10e6, price: 1});
        p.menu[2] = Size({size: 25e6, price: 1});
        p.examRules = _rules();
        uint16[] memory targets = new uint16[](1);
        targets[0] = 1000;
        p.exam = ExamTerms({
            capital: 0,
            targetsBps: targets,
            stepDuration: 7 days,
            maxIdle: 0,
            minTradingDays: 0,
            costPpm: 0,
            fillDelay: 1,
            fillWindow: 5
        });
        p.fundedRules = _rules();
        p.fundedTargetBps = 1000;
        p.traderShareBps = 8000;
        p.afterTarget = AfterTarget.AccountCloses;
        p.inWorkBps = 8000;
        p.maxSeats = 8;
        p.claimWindow = 7 days;
        p.term = 365 days;
        p.fundedIdle = 30 days;
        p.terms = TERMS;
        p.termsBook = TERMS_BOOK;
    }

    /// The product's bounds: 6 to 24 months, 14 days of notice, 90 days of renewal. Begun by the investor.
    function _pool(uint64 spot1e8) internal returns (MultiPool m) {
        m = new MultiPool(investor, factory, _examImpl(), _plan(), 180 days, 730 days, 14 days, 90 days);
        CoreSimulatorLib.forceSpotBalance(address(m), 0, spot1e8);
        vm.prank(investor);
        m.begin(TERMS);
    }

    /// A one-unit reserve unlocks one unit's part of the pool, not 40 USDC, after the end.
    function test_deep_F4_aOneUnitReserveUnlocksNoMoreThanItsPart() public {
        CoreSimulatorLib.forceAccountActivation(investor);
        MultiPool m = _pool(100e8);
        uint64 end = m.endsAt();
        vm.startPrank(investor);
        m.setReserve(1);
        vm.warp(end + 30 days);
        assertEq(m.endsAt(), end + 90 days);
        vm.expectRevert(MultiPool.Locked.selector);
        m.extend(0, 40e8, 0);
        vm.expectRevert(MultiPool.Locked.selector);
        m.extend(0, 0, 7e6);
        m.extend(0, 1, 0); // its part, once
        assertEq(m.reserve(), 0);
        vm.expectRevert(MultiPool.Locked.selector);
        m.extend(0, 1, 0);
        vm.stopPrank();
    }

    /// A reserve of 10 announced when the pool was worth 100 is the tenth of the pool: after the end
    /// it may take the tenth of what the pool is then, and not a unit more.
    function test_deep_F4_theReserveIsAPartOfThePool_takenAtItsValueThen() public {
        CoreSimulatorLib.forceAccountActivation(investor);
        MultiPool m = _pool(100e8);
        uint64 end = m.endsAt();
        vm.prank(investor);
        m.setReserve(10e8);
        vm.warp(end + 30 days);
        CoreSimulatorLib.forceSpotBalance(address(m), 0, 120e8); // the pool made 20 %
        vm.startPrank(investor);
        vm.expectRevert(MultiPool.Locked.selector);
        m.extend(0, 12e8 + 1, 0);
        m.extend(0, 12e8, 0);
        vm.stopPrank();
        CoreSimulatorLib.nextBlock();
        assertEq(_spot(investor), 12e8, "the tenth of 120");
    }

    /// A payout in the notice window spends the reserve: no second payout after the end.
    function test_deep_F4_aPayoutInTheWindowSpendsTheReserve() public {
        CoreSimulatorLib.forceAccountActivation(investor);
        MultiPool m = _pool(100e8);
        uint64 end = m.endsAt();
        vm.startPrank(investor);
        m.setReserve(10e8);
        vm.warp(end - 1 days);
        m.extend(0, 30e8, 0);
        assertEq(m.reserve(), 0, "spent");
        vm.warp(end + 45 days);
        vm.expectRevert(MultiPool.Locked.selector);
        m.extend(0, 1e8, 0);
        vm.stopPrank();
        CoreSimulatorLib.nextBlock();
        assertEq(_spot(investor), 30e8);
    }

    /// Control, as the audit had it: without a reserve the renewal is locked, and a reserve set after
    /// the end is for the next one.
    function test_deep_F4_control_withoutAReserveTheRenewalIsLocked() public {
        CoreSimulatorLib.forceAccountActivation(investor);
        MultiPool m = _pool(100e8);
        uint64 end = m.endsAt();
        vm.warp(end + 30 days);
        vm.startPrank(investor);
        vm.expectRevert(MultiPool.Locked.selector);
        m.extend(0, 40e8, 0);
        m.setReserve(1);
        assertEq(m.reserveFor(), end + 90 days);
        vm.expectRevert(MultiPool.Locked.selector);
        m.extend(0, 1, 0);
        vm.stopPrank();
    }
}
