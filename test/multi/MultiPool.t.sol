// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.28;

import {CoreSimulatorLib} from "@hyper-evm-lib/test/simulation/CoreSimulatorLib.sol";

import {Rules, Cancel, Breach} from "../../src/Types.sol";
import {Pool} from "../../src/Pool.sol";
import {PoolFactory} from "../../src/PoolFactory.sol";
import {PaperExam, ExamTerms} from "../../src/exam/PaperExam.sol";
import {MultiPool, Plan, Size, AfterTarget} from "../../src/multi/MultiPool.sol";
import {PoolHarness} from "../PoolHarness.sol";

/// One owner's pool of 100 USDC and a menu of 5, 10 and 25, on the simulator. The exams are
/// PaperExam clones, so nothing but the funded accounts ever touches the pool's money.
///
/// The menu's prices are one unit each (a millionth of a USDC) wherever a test counts the
/// share in work: a price paid is the pool's too, and a real one would move the numbers.
contract MultiPoolTest is PoolHarness {
    uint32 constant UNLISTED = 9;
    uint64 constant UP = 786920; // BTC +3%

    PaperExam examImpl;
    address alice = makeAddr("alice");
    address bob = makeAddr("bob");
    address carol = makeAddr("carol");

    function _examImpl() internal returns (address) {
        if (address(examImpl) == address(0)) examImpl = new PaperExam();
        return address(examImpl);
    }

    function _plan(AfterTarget mode) internal pure returns (Plan memory p) {
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
        p.fundedTargetBps = mode == AfterTarget.TraderStops ? 0 : 1000;
        p.traderShareBps = 8000;
        p.afterTarget = mode;
        p.inWorkBps = 8000;
        p.maxSeats = 8;
        p.claimWindow = 7 days;
        p.term = 365 days;
    }

    function _pool(Plan memory p, uint64 spot1e8) internal returns (MultiPool m) {
        m = new MultiPool(investor, factory, _examImpl(), p);
        CoreSimulatorLib.forceSpotBalance(address(m), 0, spot1e8);
    }

    /// The pool most tests use: 100 USDC, 80% in work, the account closes at its target.
    function _pool() internal returns (MultiPool) {
        return _pool(_plan(AfterTarget.AccountCloses), 100e8);
    }

    function _buy(MultiPool m, address who, uint256 line) internal returns (PaperExam e) {
        uint256 price = m.plan().menu[line].price + factory.challengeFee();
        deal(address(usdc), who, price);
        vm.startPrank(who);
        usdc.approve(address(m), price);
        e = PaperExam(m.buy(line));
        vm.stopPrank();
    }

    /// Four times the exam's balance long, 3% up, closed: about 12%, past the 10% target.
    function _pass(PaperExam e, address who) internal {
        (,, uint64 capital,) = e.result();
        uint64 sz = (capital * 4) / BTC_MARK;
        vm.prank(who);
        e.place(BTC, true, sz);
        vm.roll(block.number + 1);
        e.fill();
        CoreSimulatorLib.setMarkPx(BTC, UP);
        vm.prank(who);
        e.place(BTC, false, sz);
        vm.roll(block.number + 1);
        e.fill();
        e.graduate();
        CoreSimulatorLib.setMarkPx(BTC, BTC_MARK);
        assertEq(uint8(e.status()), uint8(PaperExam.Status.Passed));
    }

    function _expire(PaperExam e) internal {
        vm.warp(e.deadline() + 1);
        e.expire();
    }

    /// A passed exam taken and its account started.
    function _fund(MultiPool m, address who, uint256 line) internal returns (Pool seat) {
        PaperExam e = _buy(m, who, line);
        _pass(e, who);
        vm.prank(who);
        seat = Pool(m.claim(address(e)));
        CoreSimulatorLib.nextBlock();
        m.open(address(seat));
        CoreSimulatorLib.nextBlock();
    }

    /// The seat's trader makes about 12% and is flat again.
    function _profit(Pool seat) internal {
        // Four times the capital at 76 400.0, in units of 1e-8 BTC, cut to BTC's five decimals.
        uint64 sz1e8 = ((seat.terms().fundedCapital * 40) / BTC_MARK) * 100 / 1000 * 1000;
        _trade(address(seat), BTC, true, sz1e8);
        CoreSimulatorLib.setMarkPx(BTC, UP);
        _trade(address(seat), BTC, false, sz1e8);
        CoreSimulatorLib.setMarkPx(BTC, BTC_MARK);
    }

    function _settle(Pool seat) internal {
        (Cancel[] memory c, uint32[] memory a) = _none();
        for (uint256 i = 0; i < 8 && seat.stage() != Pool.Stage.Idle; ++i) {
            CoreSimulatorLib.nextBlock();
            seat.settleFunded(c, a);
        }
        assertEq(uint8(seat.stage()), uint8(Pool.Stage.Idle));
    }

    function _seatState(MultiPool m, address seat) internal view returns (MultiPool.SeatState s, address who) {
        (, s,, who,,,) = m.seatInfo(seat);
    }

    function _examState(MultiPool m, address exam) internal view returns (MultiPool.ExamState s, address seat) {
        (, s, seat) = m.examInfo(exam);
    }

    // ── creation ─────────────────────────────────────────────

    function test_multi_startsWithASeatForEveryLine() public {
        MultiPool m = _pool();
        assertEq(m.owner(), investor);
        assertEq(m.value(), 100e8);
        assertEq(m.inWork(), 0);
        Plan memory p = m.plan();
        assertEq(p.menu.length, 3);
        assertEq(p.menu[2].size, 25e6);
        assertEq(p.inWorkBps, 8000);
        address[] memory seats = m.seats();
        assertEq(seats.length, 3);
        for (uint256 i = 0; i < 3; ++i) {
            Pool seat = Pool(seats[i]);
            assertEq(seat.owner(), address(m));
            assertTrue(seat.ownerRun(), "the seat sells no challenge of its own");
            assertFalse(seat.traderMayStop(), "with a target, the trader does not stop it themselves");
            assertEq(seat.terms().fundedCapital, p.menu[i].size);
            assertEq(seat.terms().traderShareFundedBps, 8000);
            (uint8 line, MultiPool.SeatState s,,,,,) = m.seatInfo(seats[i]);
            assertEq(line, i);
            assertEq(uint8(s), uint8(MultiPool.SeatState.Free));
        }
        // Without a target the trader is the one who stops.
        MultiPool free = _pool(_plan(AfterTarget.TraderStops), 100e8);
        assertTrue(Pool(free.seats()[0]).traderMayStop());
    }

    function _expectBadPlan(Plan memory p) internal {
        address impl = _examImpl();
        vm.expectRevert(MultiPool.BadPlan.selector);
        new MultiPool(investor, factory, impl, p);
    }

    function test_multi_plan_menu() public {
        Plan memory p = _plan(AfterTarget.AccountCloses);
        p.menu = new Size[](0);
        _expectBadPlan(p);
        p.menu = new Size[](9);
        for (uint64 i = 0; i < 9; ++i) {
            p.menu[i] = Size({size: (i + 1) * 1e6, price: 1});
        }
        _expectBadPlan(p);
        p = _plan(AfterTarget.AccountCloses);
        p.menu[0].size = 0;
        _expectBadPlan(p);
        p = _plan(AfterTarget.AccountCloses);
        p.menu[1].price = 0;
        _expectBadPlan(p);
        // The same size twice, or a smaller one after a larger.
        p = _plan(AfterTarget.AccountCloses);
        p.menu[1].size = 5e6;
        _expectBadPlan(p);
        p.menu[1].size = 4e6;
        _expectBadPlan(p);
    }

    function test_multi_plan_shareSeatsAndWindow() public {
        Plan memory p = _plan(AfterTarget.AccountCloses);
        p.inWorkBps = 0;
        _expectBadPlan(p);
        p.inWorkBps = 10_001;
        _expectBadPlan(p);
        p = _plan(AfterTarget.AccountCloses);
        p.maxSeats = 0;
        _expectBadPlan(p);
        p.maxSeats = 17;
        _expectBadPlan(p);
        p = _plan(AfterTarget.AccountCloses);
        p.claimWindow = 1 hours - 1;
        _expectBadPlan(p);
        p.claimWindow = 30 days + 1;
        _expectBadPlan(p);
        // The edges that are allowed.
        p = _plan(AfterTarget.AccountCloses);
        p.inWorkBps = 10_000;
        p.maxSeats = 16;
        p.claimWindow = 1 hours;
        assertEq(_pool(p, 0).plan().maxSeats, 16);
        p.claimWindow = 30 days;
        assertEq(_pool(p, 0).plan().claimWindow, 30 days);
    }

    function test_multi_plan_targetGoesWithWhatFollowsIt() public {
        Plan memory p = _plan(AfterTarget.TraderStops);
        p.fundedTargetBps = 1000;
        _expectBadPlan(p);
        p = _plan(AfterTarget.AccountCloses);
        p.fundedTargetBps = 0;
        _expectBadPlan(p);
        p.fundedTargetBps = 10_001;
        _expectBadPlan(p);
        p = _plan(AfterTarget.AccountStays);
        p.fundedTargetBps = 0;
        _expectBadPlan(p);
        p.fundedTargetBps = 10_000;
        assertEq(_pool(p, 0).plan().fundedTargetBps, 10_000);
    }

    function test_multi_plan_ownerExamAndRules() public {
        address impl = _examImpl();
        Plan memory p = _plan(AfterTarget.AccountCloses);
        vm.expectRevert(MultiPool.BadPlan.selector);
        new MultiPool(address(0), factory, impl, p);
        vm.expectRevert(MultiPool.BadPlan.selector);
        new MultiPool(investor, factory, address(0), p);

        // An exam on an asset the platform does not list.
        p.examRules.assets[0] = UNLISTED;
        _expectBadPlan(p);
        // Terms the exam itself refuses.
        p = _plan(AfterTarget.AccountCloses);
        p.exam.fillDelay = 0;
        vm.expectRevert(PaperExam.BadTerms.selector);
        new MultiPool(investor, factory, impl, p);
        // Funded rules the factory refuses.
        p = _plan(AfterTarget.AccountCloses);
        p.fundedRules.maxLeverageX100 = 99;
        vm.expectRevert(PoolFactory.BadRules.selector);
        new MultiPool(investor, factory, impl, p);
    }

    // ── a sale ───────────────────────────────────────────────

    function test_multi_buy_takesThePrice_andStartsAnExamOfThatSize() public {
        Plan memory p = _plan(AfterTarget.AccountCloses);
        p.menu[2].price = 5e6;
        MultiPool m = _pool(p, 100e8);
        vm.prank(operator);
        factory.setChallengeFee(700_000, operator);

        PaperExam e = _buy(m, alice, 2);
        assertEq(usdc.balanceOf(address(m)), 5e6, "the price is the pool's");
        assertEq(usdc.balanceOf(operator), 700_000, "the platform's fee is not");
        assertEq(usdc.balanceOf(alice), 0);
        assertEq(e.pool(), address(m));
        assertEq(e.trader(), alice);
        assertEq(uint8(e.status()), uint8(PaperExam.Status.Active));
        assertEq(e.terms().capital, 25e6, "the exam is taken on the size bought");
        assertEq(e.cash(), 25e6);
        (MultiPool.ExamState s, address seat) = _examState(m, address(e));
        assertEq(uint8(s), uint8(MultiPool.ExamState.Sold));
        assertEq(seat, address(0));
        assertEq(m.inWork(), 25e6);
        assertEq(m.held(), 1);
        assertEq(m.promised(), 25e6);
        assertEq(m.waiting(), 1);
        assertEq(m.value(), 105e8, "nothing left the pool; the price came in");

        vm.expectRevert(MultiPool.NoSuchLine.selector);
        m.buy(3);
        vm.expectRevert(MultiPool.NoSuchLine.selector);
        m.room(3);
    }

    /// A pool of 100 with two seats of 25 sold has 30 left under its 80%: six of 5, three of 10
    /// or one of 25. Bought in one block, so nothing here waits for a balance to move.
    function test_multi_buy_whatIsLeftAfterTwoOfTwentyFive() public {
        MultiPool m = _pool();
        assertEq(m.room(0), 8, "sixteen fit the share; the seats allow eight");
        assertEq(m.room(2), 3);
        _buy(m, alice, 2);
        _buy(m, bob, 2);
        assertEq(m.inWork(), 50e6);
        assertEq(m.room(0), 6);
        assertEq(m.room(1), 3);
        assertEq(m.room(2), 1);

        _buy(m, carol, 2);
        assertEq(m.room(2), 0);
        assertEq(m.room(1), 0);
        assertEq(m.room(0), 1);
        deal(address(usdc), carol, 1);
        vm.startPrank(carol);
        usdc.approve(address(m), 1);
        vm.expectRevert(MultiPool.NoRoom.selector);
        m.buy(1);
        m.buy(0);
        assertEq(m.inWork(), 80e6);
        assertEq(m.room(0), 0);
        deal(address(usdc), carol, 1);
        usdc.approve(address(m), 1);
        vm.expectRevert(MultiPool.NoRoom.selector);
        m.buy(0);
        vm.stopPrank();
    }

    function test_multi_buy_share_boundary() public {
        // 25 is exactly 80% of 31.25.
        MultiPool m = _pool(_plan(AfterTarget.AccountCloses), 31.25e8 - 1);
        deal(address(usdc), alice, 1);
        vm.startPrank(alice);
        usdc.approve(address(m), 1);
        vm.expectRevert(MultiPool.NoRoom.selector);
        m.buy(2);
        vm.stopPrank();
        assertEq(m.room(2), 0);
        CoreSimulatorLib.forceSpotBalance(address(m), 0, 31.25e8);
        assertEq(m.room(2), 1);
        _buy(m, alice, 2);
        assertEq(m.inWork(), 25e6);
    }

    /// Value that is not on the pool's HyperCore balance cannot pay a passing trader's account.
    function test_multi_buy_theMoneyHasToBeWhereAPassIsPaidFrom() public {
        MultiPool m = _pool(_plan(AfterTarget.AccountCloses), 26e8 - 1);
        deal(address(usdc), address(m), 100e6);
        assertGt(m.value(), 125e8, "plenty of value, on the other side");
        assertEq(m.room(2), 0);
        deal(address(usdc), alice, 1);
        vm.startPrank(alice);
        usdc.approve(address(m), 1);
        vm.expectRevert(MultiPool.NoRoom.selector);
        m.buy(2);
        vm.stopPrank();
        // The size and the fee of an account its seat may need: 26.
        CoreSimulatorLib.forceSpotBalance(address(m), 0, 26e8);
        assertEq(m.room(2), 1);
        _buy(m, alice, 2);
        assertEq(m.room(0), 0, "and nothing is left there for a second");
    }

    function test_multi_buy_noMoreSizesThanThePlanSeats() public {
        Plan memory p = _plan(AfterTarget.AccountCloses);
        p.maxSeats = 2;
        MultiPool m = _pool(p, 100e8);
        _buy(m, alice, 0);
        assertEq(m.room(0), 1);
        _buy(m, bob, 0);
        assertEq(m.room(0), 0);
        deal(address(usdc), carol, 1);
        vm.startPrank(carol);
        usdc.approve(address(m), 1);
        vm.expectRevert(MultiPool.Full.selector);
        m.buy(0);
        vm.stopPrank();
    }

    // ── an exam that ends without an account ─────────────────

    function test_multi_release_givesTheSizeBack() public {
        MultiPool m = _pool();
        PaperExam e = _buy(m, alice, 2);
        vm.expectRevert(MultiPool.StillRunning.selector);
        m.release(address(e));
        _expire(e);
        vm.prank(stranger);
        m.release(address(e));
        assertEq(m.inWork(), 0);
        assertEq(m.held(), 0);
        assertEq(m.promised(), 0);
        assertEq(m.waiting(), 0);
        (MultiPool.ExamState s,) = _examState(m, address(e));
        assertEq(uint8(s), uint8(MultiPool.ExamState.Released));
        vm.expectRevert(MultiPool.NotSold.selector);
        m.release(address(e));
        vm.expectRevert(MultiPool.NotSold.selector);
        m.release(address(this));
    }

    function test_multi_release_ofABreachedExam() public {
        MultiPool m = _pool();
        PaperExam e = _buy(m, alice, 2);
        vm.prank(alice);
        e.place(BTC, true, 130);
        vm.roll(block.number + 1);
        e.fill();
        CoreSimulatorLib.setMarkPx(BTC, 740000); // -3% at 4x
        e.breach();
        m.release(address(e));
        assertEq(m.inWork(), 0);
    }

    function test_multi_claimWindow_boundary() public {
        MultiPool m = _pool();
        PaperExam e = _buy(m, alice, 2);
        _pass(e, alice);
        uint256 last = e.passedAt() + 7 days;
        // Through the last second the pass is the trader's to take, and nobody else's to release.
        vm.warp(last);
        vm.expectRevert(MultiPool.StillRunning.selector);
        m.release(address(e));
        uint256 snap = vm.snapshotState();
        vm.prank(alice);
        m.claim(address(e));
        vm.revertToState(snap);
        // One second on it is the other way round.
        vm.warp(last + 1);
        vm.prank(alice);
        vm.expectRevert(MultiPool.TooLate.selector);
        m.claim(address(e));
        m.release(address(e));
        assertEq(m.inWork(), 0);
    }

    // ── the funded account ───────────────────────────────────

    function test_multi_claim_onlyTheTraderOfAPassedExam() public {
        MultiPool m = _pool();
        PaperExam e = _buy(m, alice, 2);
        vm.prank(alice);
        vm.expectRevert(MultiPool.NotPassed.selector);
        m.claim(address(e));
        _pass(e, alice);
        vm.prank(bob);
        vm.expectRevert(MultiPool.NotTrader.selector);
        m.claim(address(e));
        vm.prank(alice);
        vm.expectRevert(MultiPool.NotSold.selector);
        m.claim(address(this));

        vm.prank(alice);
        address seat = m.claim(address(e));
        assertEq(seat, m.seats()[2], "the seat made for that line");
        (MultiPool.ExamState xs, address xseat) = _examState(m, address(e));
        assertEq(uint8(xs), uint8(MultiPool.ExamState.Claimed));
        assertEq(xseat, seat);
        (MultiPool.SeatState ss, address who) = _seatState(m, seat);
        assertEq(uint8(ss), uint8(MultiPool.SeatState.Armed));
        assertEq(who, alice);
        assertEq(m.promised(), 0, "the money is on its way to the seat");
        assertEq(m.waiting(), 0);
        assertEq(m.inWork(), 25e6, "and the size is still spoken for");
        assertEq(m.inFlight(), 26e8, "the size and the fee of the seat's new account");

        vm.prank(alice);
        vm.expectRevert(MultiPool.NotSold.selector);
        m.claim(address(e));
    }

    function test_multi_claim_aSeatInUseIsNotGivenTwice() public {
        MultiPool m = _pool();
        Pool first = _fund(m, alice, 2);
        PaperExam e = _buy(m, bob, 2);
        _pass(e, bob);
        vm.prank(bob);
        address second = m.claim(address(e));
        assertTrue(second != address(first), "the line's only seat is alice's");
        assertEq(m.seats().length, 4);
        (, address who) = _seatState(m, address(first));
        assertEq(who, alice);
        (, who) = _seatState(m, second);
        assertEq(who, bob);
    }

    function test_multi_open_startsTheAccountOnceTheMoneyIsThere() public {
        MultiPool m = _pool();
        PaperExam e = _buy(m, alice, 2);
        _pass(e, alice);
        vm.prank(alice);
        Pool seat = Pool(m.claim(address(e)));
        address other = m.seats()[0];
        // Same block: the seat has no account on HyperCore yet.
        vm.expectRevert(Pool.NotReady.selector);
        m.open(address(seat));
        vm.expectRevert(MultiPool.BadSeat.selector);
        m.open(other);

        CoreSimulatorLib.nextBlock();
        assertEq(_spot(address(seat)), 25e8);
        assertEq(_spot(address(m)), 74e8, "25 sent, and 1 for the seat's account");
        vm.prank(stranger);
        m.open(address(seat));
        assertEq(uint8(seat.stage()), uint8(Pool.Stage.Funded));
        assertEq(seat.fundedTrader(), alice);
        assertEq(seat.fundedStart(), 25e6, "the size the exam was taken on");
        (MultiPool.SeatState ss,) = _seatState(m, address(seat));
        assertEq(uint8(ss), uint8(MultiPool.SeatState.Funded));
        CoreSimulatorLib.nextBlock();
        assertEq(_equity(address(seat)), 25e6);
        assertEq(m.value(), 99e8 + 100, "the pool is what it was, less the account's fee, plus a price of one unit");

        vm.expectRevert(MultiPool.BadSeat.selector);
        m.open(address(seat));
    }

    function test_multi_rearm_sendsAgainWhatDidNotArrive() public {
        MultiPool m = _pool();
        PaperExam e = _buy(m, alice, 2);
        _pass(e, alice);
        vm.prank(alice);
        address seat = m.claim(address(e));
        CoreSimulatorLib.nextBlock();
        // The transfer arrived: nothing to send, however long it has been.
        vm.warp(block.timestamp + 2 minutes);
        address other = m.seats()[0];
        vm.expectRevert(MultiPool.AlreadyArmed.selector);
        m.rearm(seat);
        vm.expectRevert(MultiPool.BadSeat.selector);
        m.rearm(other);

        // Say it had not: the seat is 10 short.
        CoreSimulatorLib.forceSpotBalance(seat, 0, 15e8);
        (,,,, uint64 armedAt,,) = m.seatInfo(seat);
        vm.warp(armedAt + 1 minutes);
        vm.expectRevert(MultiPool.TooSoon.selector);
        m.rearm(seat);
        vm.warp(armedAt + 1 minutes + 1);
        m.rearm(seat);
        assertEq(m.inFlight(), 10e8, "only what is missing, and no fee: the account exists");
        CoreSimulatorLib.nextBlock();
        assertEq(_spot(seat), 25e8);
    }

    function test_multi_target_closesTheAccount_paysTheShare_freesTheSeat() public {
        MultiPool m = _pool();
        CoreSimulatorLib.forceAccountActivation(alice);
        Pool seat = _fund(m, alice, 2);
        (Cancel[] memory c, uint32[] memory a) = _none();

        vm.expectRevert(MultiPool.StillRunning.selector);
        m.finish(address(seat));
        // The trader cannot cash a small profit by stopping early.
        vm.prank(alice);
        vm.expectRevert(Pool.NotAllowed.selector);
        seat.stopFunded(c, a, SALT);

        _profit(seat);
        int64 eq = _equity(address(seat));
        assertGt(eq, 27.5e6);
        uint64 before = _spot(alice);
        vm.prank(stranger);
        m.hitTarget(address(seat), c, a, SALT);
        assertEq(uint8(seat.stage()), uint8(Pool.Stage.Closing));
        vm.expectRevert(MultiPool.BadSeat.selector);
        m.hitTarget(address(seat), c, a, SALT);
        // Through every step of the settlement the pool is worth the same: what the trader is
        // owed is not the pool's before it is paid, while it is being paid, or after.
        uint256 worth = m.value();
        assertEq(worth, 99e8 + 100 + uint256(uint64(eq - 25e6)) * 20);
        for (uint256 i = 0; i < 8 && seat.stage() != Pool.Stage.Idle; ++i) {
            CoreSimulatorLib.nextBlock();
            seat.settleFunded(c, a);
            assertEq(m.value(), worth, "a settlement step moved the pool's value");
        }
        _settle(seat);
        uint64 paid = _spot(alice) - before;
        assertEq(paid, uint64(uint256(int256(eq - 25e6)) * 80), "80% of the profit, in spot units");

        m.finish(address(seat));
        (MultiPool.SeatState ss, address who) = _seatState(m, address(seat));
        assertEq(uint8(ss), uint8(MultiPool.SeatState.Free));
        assertEq(who, address(0));
        assertEq(m.inWork(), 0);
        assertEq(m.held(), 0);
        CoreSimulatorLib.nextBlock();
        assertEq(_spot(address(seat)), 0, "everything went back to the pool");
        assertEq(m.value(), 99e8 + 100 + uint256(uint64(eq - 25e6)) * 20, "the pool kept 20% of the profit");
        vm.expectRevert(MultiPool.BadSeat.selector);
        m.finish(address(seat));
    }

    function test_multi_target_boundary_flat_andNoRuleBroken() public {
        MultiPool m = _pool();
        Pool seat = _fund(m, alice, 2);
        (Cancel[] memory c, uint32[] memory a) = _none();
        // 27.5 is the target on 25.
        _mockMargin(address(seat), 27.5e6 - 1, 0);
        vm.expectRevert(abi.encodeWithSelector(MultiPool.TargetNotMet.selector, int256(27.5e6 - 1), int256(27.5e6)));
        m.hitTarget(address(seat), c, a, SALT);
        // Past it with something open is not it either.
        _mockMargin(address(seat), 30e6, 10e6);
        vm.expectRevert(MultiPool.NotFlat.selector);
        m.hitTarget(address(seat), c, a, SALT);

        _mockMargin(address(seat), 27.5e6, 0);
        m.hitTarget(address(seat), c, a, SALT);
        (,, bool targetHit,,,,) = m.seatInfo(address(seat));
        assertTrue(targetHit);
        assertEq(uint8(seat.stage()), uint8(Pool.Stage.Closing));
    }

    /// Up 20% at midnight and down to +12% the same day: past the target, and over the day's limit.
    function test_multi_target_notWhileARuleIsBroken() public {
        MultiPool m = _pool();
        Pool seat = _fund(m, alice, 2);
        (Cancel[] memory c, uint32[] memory a) = _none();
        _mockMargin(address(seat), 30e6, 0);
        _nextMidnight();
        seat.checkpoint();
        _mockMargin(address(seat), 28e6, 0);
        vm.expectRevert(abi.encodeWithSelector(MultiPool.RuleBroken.selector, Breach.DailyLoss));
        m.hitTarget(address(seat), c, a, SALT);
    }

    function test_multi_withoutATarget_theTraderStops() public {
        MultiPool m = _pool(_plan(AfterTarget.TraderStops), 100e8);
        CoreSimulatorLib.forceAccountActivation(alice);
        Pool seat = _fund(m, alice, 2);
        (Cancel[] memory c, uint32[] memory a) = _none();
        _profit(seat);
        vm.expectRevert(MultiPool.NoTarget.selector);
        m.hitTarget(address(seat), c, a, SALT);
        vm.prank(alice);
        seat.stopFunded(c, a, SALT);
        _settle(seat);
        m.finish(address(seat));
        (MultiPool.SeatState ss,) = _seatState(m, address(seat));
        assertEq(uint8(ss), uint8(MultiPool.SeatState.Free));
        assertEq(m.inWork(), 0);
    }

    function test_multi_accountStays_theTraderTakesTheSeatAgain() public {
        MultiPool m = _pool(_plan(AfterTarget.AccountStays), 100e8);
        CoreSimulatorLib.forceAccountActivation(alice);
        Pool seat = _fund(m, alice, 2);
        (Cancel[] memory c, uint32[] memory a) = _none();
        _profit(seat);
        m.hitTarget(address(seat), c, a, SALT);
        _settle(seat);
        address firstKey = seat.cutKey();

        m.finish(address(seat));
        (MultiPool.SeatState ss, address who) = _seatState(m, address(seat));
        assertEq(uint8(ss), uint8(MultiPool.SeatState.Waiting));
        assertEq(who, alice);
        assertEq(m.inWork(), 25e6, "the size stays spoken for while the seat waits for its trader");
        (,, bool targetHit,,,, uint64 waitUntil) = m.seatInfo(address(seat));
        assertFalse(targetHit);
        assertEq(waitUntil, block.timestamp + 7 days);
        // Nobody frees it while it waits, and nobody else takes it.
        vm.expectRevert(MultiPool.StillRunning.selector);
        m.finish(address(seat));
        vm.prank(bob);
        vm.expectRevert(MultiPool.NotTrader.selector);
        m.reopen(address(seat));

        CoreSimulatorLib.nextBlock();
        vm.prank(alice);
        m.reopen(address(seat));
        assertEq(uint8(seat.stage()), uint8(Pool.Stage.Funded));
        assertEq(seat.fundedTrader(), alice);
        assertTrue(seat.agentKey() != firstKey);
        (ss,) = _seatState(m, address(seat));
        assertEq(uint8(ss), uint8(MultiPool.SeatState.Funded));
        vm.prank(alice);
        vm.expectRevert(MultiPool.BadSeat.selector);
        m.reopen(address(seat));
    }

    function test_multi_accountStays_aSeatNotTakenAgainIsFreed() public {
        MultiPool m = _pool(_plan(AfterTarget.AccountStays), 100e8);
        CoreSimulatorLib.forceAccountActivation(alice);
        Pool seat = _fund(m, alice, 2);
        (Cancel[] memory c, uint32[] memory a) = _none();
        _profit(seat);
        m.hitTarget(address(seat), c, a, SALT);
        _settle(seat);
        m.finish(address(seat));
        (,,,,,, uint64 waitUntil) = m.seatInfo(address(seat));

        vm.warp(waitUntil);
        vm.expectRevert(MultiPool.StillRunning.selector);
        m.finish(address(seat));
        uint256 snap = vm.snapshotState();
        vm.prank(alice);
        m.reopen(address(seat));
        vm.revertToState(snap);

        vm.warp(waitUntil + 1);
        vm.prank(alice);
        vm.expectRevert(MultiPool.TooLate.selector);
        m.reopen(address(seat));
        m.finish(address(seat));
        (MultiPool.SeatState ss,) = _seatState(m, address(seat));
        assertEq(uint8(ss), uint8(MultiPool.SeatState.Free));
        assertEq(m.inWork(), 0);
    }

    /// With the account staying, a breach is still the end of it.
    function test_multi_breach_freesTheSeat_inEveryMode() public {
        MultiPool m = _pool(_plan(AfterTarget.AccountStays), 100e8);
        Pool seat = _fund(m, alice, 2);
        (Cancel[] memory c, uint32[] memory a) = _none();
        // -3% at 4x, closed: about 12% gone, past the 10% drawdown.
        _trade(address(seat), BTC, true, 0.0013e8);
        CoreSimulatorLib.setMarkPx(BTC, 740000);
        _trade(address(seat), BTC, false, 0.0013e8);
        CoreSimulatorLib.setMarkPx(BTC, BTC_MARK);
        assertLt(_equity(address(seat)), 22.5e6);
        vm.prank(stranger);
        seat.breach(c, a, SALT);
        _settle(seat);
        m.finish(address(seat));
        (MultiPool.SeatState ss,) = _seatState(m, address(seat));
        assertEq(uint8(ss), uint8(MultiPool.SeatState.Free));
        assertEq(m.inWork(), 0);
        CoreSimulatorLib.nextBlock();
        assertLt(m.value(), 97e8, "the loss is the pool's");
    }

    function test_multi_seat_restsAMinuteBeforeItIsTakenAgain() public {
        MultiPool m = _pool(_plan(AfterTarget.TraderStops), 100e8);
        CoreSimulatorLib.forceAccountActivation(alice);
        Pool seat = _fund(m, alice, 2);
        (Cancel[] memory c, uint32[] memory a) = _none();
        vm.prank(alice);
        seat.stopFunded(c, a, SALT);
        _settle(seat);
        m.finish(address(seat));
        (,,,,, uint64 restUntil,) = m.seatInfo(address(seat));
        assertEq(restUntil, block.timestamp + 1 minutes);

        PaperExam e = _buy(m, bob, 2);
        _pass(e, bob);
        vm.warp(restUntil);
        uint256 snap = vm.snapshotState();
        vm.prank(bob);
        address fresh = m.claim(address(e));
        assertTrue(fresh != address(seat), "still resting: a new seat is made");
        assertEq(m.seats().length, 4);
        vm.revertToState(snap);

        vm.warp(restUntil + 1);
        vm.prank(bob);
        assertEq(m.claim(address(e)), address(seat), "rested: the same seat serves again");
        assertEq(m.seats().length, 3);
    }

    function test_multi_seat_noMoreOfALineThanThePlanSeats() public {
        Plan memory p = _plan(AfterTarget.TraderStops);
        p.maxSeats = 1;
        MultiPool m = _pool(p, 100e8);
        CoreSimulatorLib.forceAccountActivation(alice);
        Pool seat = _fund(m, alice, 2);
        (Cancel[] memory c, uint32[] memory a) = _none();
        vm.prank(alice);
        seat.stopFunded(c, a, SALT);
        _settle(seat);
        m.finish(address(seat));
        PaperExam e = _buy(m, bob, 2);
        _pass(e, bob);
        vm.prank(bob);
        vm.expectRevert(MultiPool.NoSeatYet.selector);
        m.claim(address(e));
    }

    function test_multi_sweep_takesBackWhatReachedAFreeSeat() public {
        MultiPool m = _pool();
        address seat = m.seats()[0];
        CoreSimulatorLib.forceSpotBalance(seat, 0, 3e8);
        assertEq(m.value(), 100e8, "a free seat is not counted");
        vm.prank(stranger);
        m.sweep(seat);
        (,,,,, uint64 restUntil,) = m.seatInfo(seat);
        assertEq(restUntil, block.timestamp + 1 minutes);
        CoreSimulatorLib.nextBlock();
        assertEq(_spot(seat), 0);
        assertEq(m.value(), 103e8);

        Pool funded = _fund(m, alice, 2);
        vm.expectRevert(MultiPool.BadSeat.selector);
        m.sweep(address(funded));
    }

    // ── value ────────────────────────────────────────────────

    function test_multi_value_countsASeatLessWhatItsTraderIsOwed() public {
        MultiPool m = _pool();
        Pool seat = _fund(m, alice, 2);
        uint256 before = m.value();
        // +5 on the seat: 4 of it would be the trader's.
        _mockMargin(address(seat), 30e6, 0);
        assertEq(m.value(), before + 1e8);
        // A loss is all the pool's.
        _mockMargin(address(seat), 23e6, 0);
        assertEq(m.value(), before - 2e8);
    }

    /// A stage stopped for a broken rule owes its trader nothing, whatever it holds.
    function test_multi_value_afterABreachTheWholeSeatIsThePools() public {
        MultiPool m = _pool();
        Pool seat = _fund(m, alice, 2);
        uint256 before = m.value();
        (Cancel[] memory c, uint32[] memory a) = _none();
        // In profit and over its leverage: 200 of notional on 30.
        _mockMargin(address(seat), 30e6, 200e6);
        assertEq(m.value(), before + 1e8);
        seat.breach(c, a, SALT);
        assertEq(uint8(seat.fundedEndReason()), uint8(Breach.Leverage));
        assertEq(m.value(), before + 5e8);
    }

    // ── the owner ────────────────────────────────────────────

    function test_multi_withdraw_keepsTheShare() public {
        MultiPool m = _pool();
        CoreSimulatorLib.forceAccountActivation(investor);
        _buy(m, alice, 2);
        vm.prank(stranger);
        vm.expectRevert(MultiPool.NotOwner.selector);
        m.withdraw(1e8);

        // 25 in work needs 31.25 to stay; the pool holds 100 and a price of one unit.
        uint64 most = 68.75e8 + 100;
        vm.startPrank(investor);
        vm.expectRevert(MultiPool.BufferNeeded.selector);
        m.withdraw(most + 1);
        vm.expectRevert(MultiPool.BufferNeeded.selector);
        m.withdraw(200e8);
        m.withdraw(most);
        vm.stopPrank();
        CoreSimulatorLib.nextBlock();
        assertEq(_spot(investor), most);
        assertEq(m.room(0), 0, "what is left is exactly the share the 25 needs");
    }

    /// With no buffer in the plan, what holds the owner back is the money promised to the exam,
    /// and what they have already sent out this minute.
    function test_multi_withdraw_leavesWhatRunningExamsMayAsk_andCountsWhatItSent() public {
        // A real date: at the first seconds of a chain "a minute ago" is before its beginning,
        // and a count that never started would look the same as one that did.
        vm.warp(20_000 days);
        Plan memory p = _plan(AfterTarget.AccountCloses);
        p.inWorkBps = 10_000;
        MultiPool m = _pool(p, 100e8);
        CoreSimulatorLib.forceAccountActivation(investor);
        _buy(m, alice, 2);

        vm.startPrank(investor);
        // 100 on the balance, 26 of it promised: 74 can go.
        vm.expectRevert(
            abi.encodeWithSelector(MultiPool.NotEnoughFree.selector, uint64(100e8), uint64(0), uint64(74e8 + 1))
        );
        m.withdraw(74e8 + 1);
        m.withdraw(10e8);
        m.withdraw(4e8);
        assertEq(m.inFlight(), 14e8, "two transfers in a minute add up");
        // Same block: the balance still reads 100, and the 14 are counted all the same.
        vm.expectRevert(
            abi.encodeWithSelector(MultiPool.NotEnoughFree.selector, uint64(100e8), uint64(14e8), uint64(60e8 + 1))
        );
        m.withdraw(60e8 + 1);
        vm.stopPrank();

        // They have left the balance by the next block, and for a minute are counted all the same:
        // the pool cannot tell a transfer that landed from one that has not.
        uint64 sentAt = m.lastSendAt();
        CoreSimulatorLib.nextBlock();
        vm.warp(sentAt + 1 minutes);
        vm.prank(investor);
        vm.expectRevert(
            abi.encodeWithSelector(MultiPool.NotEnoughFree.selector, uint64(86e8), uint64(14e8), uint64(46e8 + 1))
        );
        m.withdraw(46e8 + 1);
        vm.warp(block.timestamp + 1);
        vm.prank(investor);
        m.withdraw(60e8);
        assertEq(m.inFlight(), 60e8, "a new minute starts its own count");
    }

    function test_multi_withdrawEvm_thePrices_onTheSameCondition() public {
        Plan memory p = _plan(AfterTarget.AccountCloses);
        p.menu[2].price = 5e6;
        MultiPool m = _pool(p, 31.25e8);
        _buy(m, alice, 2);
        vm.prank(stranger);
        vm.expectRevert(MultiPool.NotOwner.selector);
        m.withdrawEvm(1);
        // 36.25 of value, 31.25 has to stay: the 5 of the price can go, and not a unit more.
        vm.startPrank(investor);
        vm.expectRevert(MultiPool.BufferNeeded.selector);
        m.withdrawEvm(5e6 + 1);
        m.withdrawEvm(5e6);
        vm.stopPrank();
        assertEq(usdc.balanceOf(investor), 5e6);
        assertEq(usdc.balanceOf(address(m)), 0);
    }

    // ── the term ─────────────────────────────────────────────

    function _approved(MultiPool m, address who) internal {
        deal(address(usdc), who, 1);
        vm.prank(who);
        usdc.approve(address(m), 1);
    }

    function test_multi_term_bounds_andTheEndItSets() public {
        Plan memory p = _plan(AfterTarget.AccountCloses);
        p.term = 180 days - 1;
        _expectBadPlan(p);
        p.term = 730 days + 1;
        _expectBadPlan(p);
        p.term = 180 days;
        assertEq(_pool(p, 0).endsAt(), block.timestamp + 180 days);
        p.term = 730 days;
        assertEq(_pool(p, 0).endsAt(), block.timestamp + 730 days);
    }

    function test_multi_term_noSaleInItsLastFourteenDays() public {
        MultiPool m = _pool();
        uint256 end = m.endsAt();
        // Fourteen days before the end, to the second, is the last moment an exam is sold.
        vm.warp(end - 14 days);
        assertFalse(m.closing());
        assertEq(m.room(0), 8);
        uint256 snap = vm.snapshotState();
        _buy(m, alice, 0);
        vm.revertToState(snap);

        vm.warp(end - 14 days + 1);
        assertTrue(m.closing());
        assertEq(m.room(0), 0);
        _approved(m, alice);
        vm.prank(alice);
        vm.expectRevert(MultiPool.Ending.selector);
        m.buy(0);
    }

    function test_multi_term_extend_movesTheEnd_andSalesResume() public {
        MultiPool m = _pool();
        uint64 end = m.endsAt();
        vm.prank(stranger);
        vm.expectRevert(MultiPool.NotOwner.selector);
        m.extend(365 days);
        vm.startPrank(investor);
        vm.expectRevert(MultiPool.BadPlan.selector);
        m.extend(180 days - 1);
        vm.expectRevert(MultiPool.BadPlan.selector);
        m.extend(730 days + 1);
        vm.stopPrank();

        vm.warp(end - 1 days);
        assertEq(m.room(0), 0);
        vm.prank(investor);
        m.extend(180 days);
        assertEq(m.endsAt(), end + 180 days, "from the old end, not from today");
        assertFalse(m.closing());
        _buy(m, alice, 0);

        // The last second of a term can still be extended; a pool that has stopped cannot.
        vm.warp(m.endsAt());
        vm.prank(investor);
        m.extend(730 days);
        vm.warp(m.endsAt() + 1);
        vm.prank(investor);
        vm.expectRevert(MultiPool.Ended.selector);
        m.extend(180 days);
    }

    function test_multi_term_atTheEnd_theAccountIsClosed_andItsShareIsPaid() public {
        MultiPool m = _pool();
        CoreSimulatorLib.forceAccountActivation(alice);
        Pool seat = _fund(m, alice, 2);
        (Cancel[] memory c, uint32[] memory a) = _none();
        // +3% at 1x, closed: a profit short of the target.
        _trade(address(seat), BTC, true, 0.0003e8);
        CoreSimulatorLib.setMarkPx(BTC, UP);
        _trade(address(seat), BTC, false, 0.0003e8);
        CoreSimulatorLib.setMarkPx(BTC, BTC_MARK);
        int64 eq = _equity(address(seat));
        assertGt(eq, 25e6);
        assertLt(eq, 27.5e6);

        uint64 end = m.endsAt();
        vm.warp(end);
        vm.expectRevert(MultiPool.NotEnded.selector);
        m.endSeat(address(seat), c, a, SALT);
        vm.warp(end + 1);
        address other = m.seats()[0];
        vm.expectRevert(MultiPool.BadSeat.selector);
        m.endSeat(other, c, a, SALT);
        uint64 before = _spot(alice);
        vm.prank(stranger);
        m.endSeat(address(seat), c, a, SALT);
        assertEq(uint8(seat.stage()), uint8(Pool.Stage.Closing));
        assertEq(uint8(seat.fundedEndReason()), uint8(Breach.None));
        vm.expectRevert(MultiPool.BadSeat.selector);
        m.endSeat(address(seat), c, a, SALT);

        _settle(seat);
        assertEq(_spot(alice) - before, uint64(uint256(int256(eq - 25e6)) * 80), "80% of what it made");
        m.finish(address(seat));
        assertEq(m.inWork(), 0);
        assertEq(m.held(), 0);
    }

    /// With the account staying after its target, a pool that has stopped holds no seat for anyone.
    function test_multi_term_afterTheEnd_noSeatWaitsForItsTrader() public {
        MultiPool m = _pool(_plan(AfterTarget.AccountStays), 100e8);
        CoreSimulatorLib.forceAccountActivation(alice);
        CoreSimulatorLib.forceAccountActivation(bob);
        Pool waited = _fund(m, alice, 2);
        Pool late = _fund(m, bob, 1);
        (Cancel[] memory c, uint32[] memory a) = _none();
        uint64 end = m.endsAt();

        // Two days before the end one seat reaches its target and waits for its trader...
        vm.warp(end - 2 days);
        _profit(waited);
        m.hitTarget(address(waited), c, a, SALT);
        _settle(waited);
        m.finish(address(waited));
        (MultiPool.SeatState ss,) = _seatState(m, address(waited));
        assertEq(uint8(ss), uint8(MultiPool.SeatState.Waiting));
        // ...and the other reaches it too, and is still settling when the pool stops.
        _profit(late);
        m.hitTarget(address(late), c, a, SALT);
        _settle(late);

        vm.warp(end + 1);
        vm.prank(alice);
        vm.expectRevert(MultiPool.Ended.selector);
        m.reopen(address(waited));
        m.finish(address(waited));
        (ss,) = _seatState(m, address(waited));
        assertEq(uint8(ss), uint8(MultiPool.SeatState.Free), "five days of its window were left");
        m.finish(address(late));
        (ss,) = _seatState(m, address(late));
        assertEq(uint8(ss), uint8(MultiPool.SeatState.Free), "it never starts to wait");
        assertEq(m.inWork(), 0);
    }

    function test_multi_term_afterTheEnd_nothingOpens_andEverySizeComesBack() public {
        Plan memory p = _plan(AfterTarget.AccountCloses);
        p.exam.stepDuration = 0; // exams that are still running when the pool stops
        MultiPool m = _pool(p, 100e8);
        PaperExam running = _buy(m, alice, 2);
        PaperExam passed = _buy(m, bob, 1);
        PaperExam claimed = _buy(m, carol, 0);
        uint64 end = m.endsAt();

        vm.warp(end - 1 days);
        _pass(passed, bob);
        _pass(claimed, carol);
        vm.prank(carol);
        address seat = m.claim(address(claimed));
        CoreSimulatorLib.nextBlock();
        // Before the end none of this can be undone by a stranger.
        vm.expectRevert(MultiPool.StillRunning.selector);
        m.release(address(running));
        vm.expectRevert(MultiPool.StillRunning.selector);
        m.release(address(passed));
        vm.expectRevert(MultiPool.BadSeat.selector);
        m.finish(seat);

        vm.warp(end + 1);
        vm.prank(bob);
        vm.expectRevert(MultiPool.Ended.selector);
        m.claim(address(passed));
        vm.expectRevert(MultiPool.Ended.selector);
        m.open(seat);

        m.release(address(running));
        m.release(address(passed));
        m.finish(seat);
        assertEq(m.inWork(), 0);
        assertEq(m.held(), 0);
        assertEq(m.promised(), 0);
        CoreSimulatorLib.nextBlock();
        assertEq(_spot(seat), 0, "the money sent to the seat is back with the pool");
        assertEq(_spot(address(m)), 99e8, "all of it but the fee of that seat's account");
    }
}
