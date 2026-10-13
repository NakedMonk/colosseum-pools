// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Clones} from "@openzeppelin/contracts/proxy/Clones.sol";
import {Rules, Terms, Cancel, Breach, Units} from "../Types.sol";
import {CoreOps} from "../lib/CoreOps.sol";
import {Pool} from "../Pool.sol";
import {PoolFactory} from "../PoolFactory.sol";
import {PaperExam, ExamTerms} from "../exam/PaperExam.sol";

/// @notice One line of the menu.
struct Size {
    /// What the exam is taken on, and what the funded account then holds (1e6 = 1 USDC).
    uint64 size;
    /// What the exam costs, in HyperEVM USDC units (6 decimals).
    uint64 price;
}

/// @notice What happens to a funded account that has reached its target.
enum AfterTarget {
    /// There is no target: the trader ends the stage when they choose, and is paid their share.
    TraderStops,
    /// The stage ends at the target, the trader is paid their share, and the seat is free again.
    AccountCloses,
    /// The stage ends at the target, the trader is paid their share, and may take the same seat again.
    AccountStays
}

/// @notice Everything the creator of a pool sets. Fixed at creation.
struct Plan {
    Size[] menu;
    Rules examRules;
    /// The exam's terms. `capital` is not read: every exam takes the size it was bought at.
    ExamTerms exam;
    Rules fundedRules;
    /// Profit over the funded account's starting equity that counts as the target (bps); zero
    /// with `TraderStops` and only then.
    uint16 fundedTargetBps;
    /// The trader's share of the profit a funded stage made (bps).
    uint16 traderShareBps;
    AfterTarget afterTarget;
    /// The most of the pool's value that may be spoken for at once (bps). The rest is its buffer.
    uint16 inWorkBps;
    /// How many sizes may be spoken for at once, whatever they add up to.
    uint16 maxSeats;
    /// How long a trader who passed has to take the funded account, in seconds.
    uint32 claimWindow;
}

/// @title MultiPool
/// @notice One owner's capital, many traders. A trader buys an exam off the menu; the exam is a
///         PaperExam, traded on nobody's money. From that moment the size is spoken for: nothing
///         else is sold against it. A trader who passes takes a funded account of that size on
///         the pool's capital, by their own call; one who does not gives the size back.
/// @dev A funded account is a `Pool` this contract owns and runs (`Pool.runByOwner`), so the
///      funded stage is the one every pool has. The owner is a single address here: an investor
///      on their own, or a contract that holds many investors' shares.
///
///      Two limits hold a sale back. The sizes spoken for may not pass `inWorkBps` of the pool's
///      value; and the money for every exam still running has to be on the pool's own HyperCore
///      balance, where a passing trader's account will be paid from. Both are counted in this
///      contract's storage, so two sales in one block see each other; a balance read from
///      HyperCore is the start of the block and never alone decides anything here. What the pool
///      has sent and may not show in that balance yet is counted against it for `ARM_WAIT`.
contract MultiPool {
    using SafeERC20 for IERC20;

    enum ExamState {
        None,
        Sold,
        Claimed,
        Released
    }

    enum SeatState {
        None,
        Free,
        Armed,
        Funded,
        /// Settled after its target; its trader may take it again until `waitUntil`.
        Waiting
    }

    struct ExamInfo {
        uint8 line;
        ExamState state;
        address seat;
    }

    struct SeatInfo {
        uint8 line;
        SeatState state;
        bool targetHit;
        address trader;
        uint64 armedAt;
        /// A freed seat is not taken again before this: what it sent back may not have left yet.
        uint64 restUntil;
        uint64 waitUntil;
    }

    uint256 public constant MAX_MENU = 8;
    uint256 public constant MAX_SEATS = 16;
    uint32 public constant MIN_CLAIM_WINDOW = 1 hours;
    uint32 public constant MAX_CLAIM_WINDOW = 30 days;
    /// How long a transfer between HyperCore accounts is given to show in a balance.
    uint64 public constant ARM_WAIT = 1 minutes;

    address public immutable owner;
    PoolFactory public immutable factory;
    IERC20 public immutable usdc;
    address public immutable examImpl;

    Plan internal _plan;

    /// Sizes spoken for, added up (1e6 = 1 USDC): exams running or passed and not yet taken, and
    /// funded accounts until their seat is free again.
    uint256 public inWork;
    /// How many sizes that is.
    uint256 public held;
    /// The part of `inWork` whose money is still on the pool's own balance: exams not yet taken.
    uint256 public promised;
    /// How many exams that is.
    uint256 public waiting;

    mapping(address exam => ExamInfo) public examInfo;
    mapping(address seat => SeatInfo) public seatInfo;
    address[] internal _seats;

    /// What this pool sent from its HyperCore balance within the last `ARM_WAIT` (1e8 = 1 USDC).
    uint64 public inFlight;
    uint64 public lastSendAt;

    event ExamSold(address indexed exam, address indexed trader, uint256 line, uint64 size, uint64 price);
    event ExamFeePaid(address indexed trader, address indexed recipient, uint256 fee);
    event ExamReleased(address indexed exam);
    event Claimed(address indexed exam, address indexed trader, address indexed seat);
    event SeatCreated(address indexed seat, uint256 line);
    event SeatArmed(address indexed seat, uint64 amount);
    event SeatOpened(address indexed seat, address indexed trader);
    event TargetHit(address indexed seat, address indexed trader, int256 equity);
    event SeatWaiting(address indexed seat, address indexed trader, uint64 until);
    event SeatFreed(address indexed seat, uint64 returned);
    event Swept(address indexed seat, uint64 amount);
    event Withdrawn(address indexed to, uint64 amount);
    event WithdrawnEvm(address indexed to, uint256 amount);

    error BadPlan();
    error NotOwner();
    error NoSuchLine();
    error Full();
    error NoRoom();
    error NotSold();
    error StillRunning();
    error NotPassed();
    error NotTrader();
    error TooLate();
    error NoSeatYet();
    error NotEnoughFree(uint64 spot, uint64 inFlight, uint64 cost);
    error BadSeat();
    error TooSoon();
    error AlreadyArmed();
    error NoTarget();
    error NotFlat();
    error RuleBroken(Breach reason);
    error TargetNotMet(int256 equity, int256 target);
    error BufferNeeded();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor(address owner_, PoolFactory factory_, address examImpl_, Plan memory plan_) {
        if (owner_ == address(0) || examImpl_ == address(0)) revert BadPlan();
        owner = owner_;
        factory = factory_;
        usdc = factory_.usdc();
        examImpl = examImpl_;

        uint256 lines = plan_.menu.length;
        if (lines == 0 || lines > MAX_MENU) revert BadPlan();
        for (uint256 i = 0; i < lines; ++i) {
            Size memory m = plan_.menu[i];
            if (m.size == 0 || m.price == 0) revert BadPlan();
            if (i != 0 && m.size <= plan_.menu[i - 1].size) revert BadPlan();
            _plan.menu.push(m);
        }
        if (plan_.inWorkBps == 0 || plan_.inWorkBps > Units.BPS) revert BadPlan();
        if (plan_.maxSeats == 0 || plan_.maxSeats > MAX_SEATS) revert BadPlan();
        if (plan_.claimWindow < MIN_CLAIM_WINDOW || plan_.claimWindow > MAX_CLAIM_WINDOW) revert BadPlan();
        if (plan_.afterTarget == AfterTarget.TraderStops) {
            if (plan_.fundedTargetBps != 0) revert BadPlan();
        } else if (plan_.fundedTargetBps == 0 || plan_.fundedTargetBps > Units.BPS) {
            revert BadPlan();
        }
        for (uint256 i = 0; i < plan_.examRules.assets.length; ++i) {
            if (!factory_.isPlatformAsset(plan_.examRules.assets[i])) revert BadPlan();
        }
        // The exam's own answer on its rules and terms, asked once and before anything is sold.
        plan_.exam.capital = plan_.menu[0].size;
        PaperExam(examImpl_).check(plan_.examRules, plan_.exam);

        _plan.examRules = plan_.examRules;
        _plan.exam = plan_.exam;
        _plan.fundedRules = plan_.fundedRules;
        _plan.fundedTargetBps = plan_.fundedTargetBps;
        _plan.traderShareBps = plan_.traderShareBps;
        _plan.afterTarget = plan_.afterTarget;
        _plan.inWorkBps = plan_.inWorkBps;
        _plan.maxSeats = plan_.maxSeats;
        _plan.claimWindow = plan_.claimWindow;

        // One seat for every line, so the factory has said yes to the funded rules and to every
        // size before the first exam is sold on them.
        for (uint256 i = 0; i < lines; ++i) {
            _newSeat(uint8(i));
        }
    }

    // ── views ────────────────────────────────────────────────────

    function plan() external view returns (Plan memory) {
        return _plan;
    }

    function seats() external view returns (address[] memory) {
        return _seats;
    }

    /// @notice The pool's value (1e8 = 1 USDC): its spot on HyperCore, its USDC on HyperEVM, and
    ///         every seat in use at its spot and perp value, less what its trader is owed if the
    ///         stage ended now without a breach.
    function value() public view returns (uint256) {
        int256 v = int256(uint256(CoreOps.spotUsdc(address(this))));
        v += int256(usdc.balanceOf(address(this)) * Units.SPOT_PER_PERP);
        uint256 n = _seats.length;
        for (uint256 i = 0; i < n; ++i) {
            address seat = _seats[i];
            if (seatInfo[seat].state == SeatState.Free) continue;
            v += _seatValue(Pool(seat));
        }
        return v > 0 ? uint256(v) : 0;
    }

    /// @notice How many more exams of this line the pool would sell right now.
    function room(uint256 line) external view returns (uint256) {
        if (line >= _plan.menu.length) revert NoSuchLine();
        uint64 size = _plan.menu[line].size;
        uint256 n = _plan.maxSeats - held;
        uint256 cap = (value() * _plan.inWorkBps) / Units.BPS / Units.SPOT_PER_PERP;
        uint256 byShare = cap > inWork ? (cap - inWork) / size : 0;
        if (byShare < n) n = byShare;
        uint256 each = uint256(size) * Units.SPOT_PER_PERP + Units.NEW_ACCOUNT_FEE;
        uint256 owed = _owedOnCore() + _recent();
        uint256 spot = CoreOps.spotUsdc(address(this));
        uint256 byBalance = spot > owed ? (spot - owed) / each : 0;
        return byBalance < n ? byBalance : n;
    }

    // ── a trader ─────────────────────────────────────────────────

    /// @notice Buys an exam of the menu's `line`. The size is spoken for from this call on.
    function buy(uint256 line) external returns (address exam) {
        if (line >= _plan.menu.length) revert NoSuchLine();
        Size memory m = _plan.menu[line];
        if (held >= _plan.maxSeats) revert Full();
        uint256 inWorkAfter = inWork + m.size;
        if (inWorkAfter * Units.SPOT_PER_PERP * Units.BPS > value() * _plan.inWorkBps) revert NoRoom();
        inWork = inWorkAfter;
        held += 1;
        promised += m.size;
        waiting += 1;
        // The money a pass will ask for has to be where it will be paid from, now.
        if (CoreOps.spotUsdc(address(this)) < _owedOnCore() + _recent()) revert NoRoom();

        usdc.safeTransferFrom(msg.sender, address(this), m.price);
        uint256 fee = factory.challengeFee();
        if (fee != 0) {
            address recipient = factory.feeRecipient();
            usdc.safeTransferFrom(msg.sender, recipient, fee);
            emit ExamFeePaid(msg.sender, recipient, fee);
        }

        exam = Clones.clone(examImpl);
        ExamTerms memory t = _plan.exam;
        t.capital = m.size;
        PaperExam(exam).initialize(msg.sender, _plan.examRules, t);
        examInfo[exam] = ExamInfo({line: uint8(line), state: ExamState.Sold, seat: address(0)});
        emit ExamSold(exam, msg.sender, line, m.size, m.price);
    }

    /// @notice Takes the funded account an exam earned. Only its trader, only once it has passed,
    ///         and within the pool's window after the pass. The money leaves for the seat in this
    ///         call; `open` starts the account once it is there.
    function claim(address exam) external returns (address seat) {
        ExamInfo storage x = examInfo[exam];
        if (x.state != ExamState.Sold) revert NotSold();
        (PaperExam.Status s, address trader,, uint64 passedAt) = PaperExam(exam).result();
        if (s != PaperExam.Status.Passed) revert NotPassed();
        if (msg.sender != trader) revert NotTrader();
        if (block.timestamp > uint256(passedAt) + _plan.claimWindow) revert TooLate();

        seat = _takeSeat(x.line);
        x.state = ExamState.Claimed;
        x.seat = seat;
        promised -= _plan.menu[x.line].size;
        waiting -= 1;
        SeatInfo storage si = seatInfo[seat];
        si.state = SeatState.Armed;
        si.trader = trader;
        emit Claimed(exam, trader, seat);
        _arm(seat, si);
    }

    /// @notice Takes a seat again after its target, for the trader it is waiting for.
    function reopen(address seat) external {
        SeatInfo storage si = seatInfo[seat];
        if (si.state != SeatState.Waiting) revert BadSeat();
        if (msg.sender != si.trader) revert NotTrader();
        if (block.timestamp > si.waitUntil) revert TooLate();
        si.state = SeatState.Funded;
        Pool(seat).openFundedFor(si.trader);
        emit SeatOpened(seat, si.trader);
    }

    // ── anyone ─────────────────────────────────────────────────�

    /// @notice Gives back the size of an exam that ended without a pass, or whose pass nobody
    ///         took in time.
    function release(address exam) external {
        ExamInfo storage x = examInfo[exam];
        if (x.state != ExamState.Sold) revert NotSold();
        (PaperExam.Status s,,, uint64 passedAt) = PaperExam(exam).result();
        bool over = s == PaperExam.Status.Breached || s == PaperExam.Status.Expired
            || (s == PaperExam.Status.Passed && block.timestamp > uint256(passedAt) + _plan.claimWindow);
        if (!over) revert StillRunning();
        x.state = ExamState.Released;
        uint64 size = _plan.menu[x.line].size;
        promised -= size;
        waiting -= 1;
        _unhold(size);
        emit ExamReleased(exam);
    }

    /// @notice Starts the funded account on a seat whose money has arrived.
    function open(address seat) external {
        SeatInfo storage si = seatInfo[seat];
        if (si.state != SeatState.Armed) revert BadSeat();
        si.state = SeatState.Funded;
        Pool p = Pool(seat);
        if (!p.accountReady()) p.prepareAccount();
        p.openFundedFor(si.trader);
        emit SeatOpened(seat, si.trader);
    }

    /// @notice Sends a seat its money again if, a minute after the first time, it is not there.
    function rearm(address seat) external {
        SeatInfo storage si = seatInfo[seat];
        if (si.state != SeatState.Armed) revert BadSeat();
        if (block.timestamp <= si.armedAt + ARM_WAIT) revert TooSoon();
        if (!_arm(seat, si)) revert AlreadyArmed();
    }

    /// @notice Ends a funded stage that has reached the pool's target with nothing open and no
    ///         rule broken. The trader is paid their share when the stage settles.
    function hitTarget(address seat, Cancel[] calldata cancels, uint32[] calldata extraAssets, bytes32 salt) external {
        if (_plan.afterTarget == AfterTarget.TraderStops) revert NoTarget();
        SeatInfo storage si = seatInfo[seat];
        Pool p = Pool(seat);
        if (si.state != SeatState.Funded || p.stage() != Pool.Stage.Funded) revert BadSeat();
        if (CoreOps.margin(seat).ntlPos != 0) revert NotFlat();
        Breach reason = p.violation(new uint32[](0));
        if (reason != Breach.None) revert RuleBroken(reason);
        int256 start = p.fundedStart();
        int256 eq = CoreOps.equity(seat);
        int256 target = start + (start * int256(uint256(_plan.fundedTargetBps))) / int256(uint256(Units.BPS));
        if (eq < target) revert TargetNotMet(eq, target);
        si.targetHit = true;
        emit TargetHit(seat, si.trader, eq);
        p.stopFunded(cancels, extraAssets, salt);
    }

    /// @notice Once a seat's stage has settled: frees the seat and takes its money back, or,
    ///         where the plan lets the account stay after its target, holds it for its trader.
    function finish(address seat) external {
        SeatInfo storage si = seatInfo[seat];
        if (si.state != SeatState.Funded && si.state != SeatState.Waiting) revert BadSeat();
        if (Pool(seat).stage() != Pool.Stage.Idle) revert StillRunning();
        if (si.state == SeatState.Funded && si.targetHit && _plan.afterTarget == AfterTarget.AccountStays) {
            si.targetHit = false;
            si.state = SeatState.Waiting;
            si.waitUntil = uint64(block.timestamp) + _plan.claimWindow;
            emit SeatWaiting(seat, si.trader, si.waitUntil);
            return;
        }
        if (si.state == SeatState.Waiting && block.timestamp <= si.waitUntil) revert StillRunning();

        uint64 back = CoreOps.spotUsdc(seat);
        si.state = SeatState.Free;
        si.targetHit = false;
        si.trader = address(0);
        si.restUntil = uint64(block.timestamp) + ARM_WAIT;
        _unhold(_plan.menu[si.line].size);
        if (back != 0) Pool(seat).withdrawOnCore(back);
        emit SeatFreed(seat, back);
    }

    /// @notice Takes back whatever reached a free seat after it was freed.
    function sweep(address seat) external {
        SeatInfo storage si = seatInfo[seat];
        if (si.state != SeatState.Free) revert BadSeat();
        uint64 left = CoreOps.spotUsdc(seat);
        si.restUntil = uint64(block.timestamp) + ARM_WAIT;
        if (left != 0) Pool(seat).withdrawOnCore(left);
        emit Swept(seat, left);
    }

    // ── the owner ────────────────────────────────────────────────

    /// @notice Sends `amount1e8` of the pool's HyperCore balance to the owner. What stays has to
    ///         keep the sizes spoken for inside the plan's share, and the money promised to
    ///         running exams where it is.
    function withdraw(uint64 amount1e8) external onlyOwner {
        _keepShare(amount1e8);
        _spend(amount1e8, _owedOnCore());
        uint64 sent = CoreOps.sendableTo(owner, amount1e8);
        CoreOps.sendUsdc(owner, sent);
        emit Withdrawn(owner, sent);
    }

    /// @notice Sends `amount` of the USDC this pool holds on HyperEVM, the exams' prices, to the
    ///         owner, on the same condition about the share.
    function withdrawEvm(uint256 amount) external onlyOwner {
        _keepShare(amount * Units.SPOT_PER_PERP);
        usdc.safeTransfer(owner, amount);
        emit WithdrawnEvm(owner, amount);
    }

    // ── internals ───────────────────────────────────────────────�

    function _newSeat(uint8 line) internal returns (address seat) {
        uint64 size = _plan.menu[line].size;
        // A seat sells no challenge, so only the funded half of its terms is ever read; the
        // rest are the smallest values the factory takes.
        Terms memory t = Terms({
            price: 1,
            capital: size,
            targetBps: 1,
            duration: 1,
            traderShareChallengeBps: 0,
            traderShareFundedBps: _plan.traderShareBps,
            fundedCapital: size
        });
        seat = factory.createPool(_plan.fundedRules, t);
        Pool(seat).runByOwner(_plan.afterTarget == AfterTarget.TraderStops);
        seatInfo[seat].line = line;
        seatInfo[seat].state = SeatState.Free;
        _seats.push(seat);
        emit SeatCreated(seat, line);
    }

    /// @dev A free seat of this line that has rested, or a new one while the line has fewer
    ///      than `maxSeats`.
    function _takeSeat(uint8 line) internal returns (address) {
        uint256 ofLine;
        uint256 n = _seats.length;
        for (uint256 i = 0; i < n; ++i) {
            SeatInfo storage si = seatInfo[_seats[i]];
            if (si.line != line) continue;
            if (si.state == SeatState.Free && block.timestamp > si.restUntil) return _seats[i];
            ++ofLine;
        }
        if (ofLine >= _plan.maxSeats) revert NoSeatYet();
        return _newSeat(line);
    }

    /// @dev Tops the seat up to its size. False if it already holds it.
    function _arm(address seat, SeatInfo storage si) internal returns (bool) {
        uint64 need = _plan.menu[si.line].size * Units.SPOT_PER_PERP;
        uint64 has = CoreOps.spotUsdc(seat);
        if (has >= need) return false;
        uint64 amount = need - has;
        uint64 cost = CoreOps.exists(seat) ? amount : amount + Units.NEW_ACCOUNT_FEE;
        _spend(cost, _owedOnCore());
        si.armedAt = uint64(block.timestamp);
        CoreOps.sendUsdc(seat, amount);
        emit SeatArmed(seat, amount);
        return true;
    }

    /// @dev Counts `cost` against the pool's HyperCore balance, together with what it sent in
    ///      the last `ARM_WAIT` and with `keep`, which has to stay. The balance is the start of
    ///      the block; the count is not.
    function _spend(uint64 cost, uint256 keep) internal {
        uint64 recent = _recent();
        uint64 spot = CoreOps.spotUsdc(address(this));
        if (spot < uint256(recent) + cost + keep) revert NotEnoughFree(spot, recent, cost);
        inFlight = recent + cost;
        lastSendAt = uint64(block.timestamp);
    }

    function _recent() internal view returns (uint64) {
        return block.timestamp > lastSendAt + ARM_WAIT ? 0 : inFlight;
    }

    /// @dev What the exams still running may ask of the pool's HyperCore balance (1e8): their
    ///      sizes, and for each the fee of an account its seat may not have yet.
    function _owedOnCore() internal view returns (uint256) {
        return promised * Units.SPOT_PER_PERP + waiting * Units.NEW_ACCOUNT_FEE;
    }

    function _unhold(uint64 size) internal {
        inWork -= size;
        held -= 1;
    }

    function _keepShare(uint256 out1e8) internal view {
        uint256 v = value();
        if (out1e8 > v) revert BufferNeeded();
        if (inWork * Units.SPOT_PER_PERP * Units.BPS > (v - out1e8) * _plan.inWorkBps) revert BufferNeeded();
    }

    function _seatValue(Pool p) internal view returns (int256 v) {
        int256 eq = CoreOps.equity(address(p));
        v = int256(uint256(CoreOps.spotUsdc(address(p)))) + eq * int256(uint256(Units.SPOT_PER_PERP));
        Pool.Stage st = p.stage();
        if (st == Pool.Stage.Funded) {
            v -= _share(eq - int256(p.fundedStart()));
        } else if (st == Pool.Stage.Closing) {
            if (p.fundedEndReason() != Breach.None) return v;
            if (!p.fundedResultTaken()) {
                v -= _share(eq - int256(p.fundedStart()));
            } else if (!p.fundedPayoutDone()) {
                v -= int256(uint256(p.fundedPayoutOwed()));
            } else if (
                CoreOps.spotUsdc(address(p)) + p.fundedPayoutSent() > p.fundedPayoutSpotBefore()
                    && block.timestamp <= p.fundedPayoutAt() + p.PAYOUT_WAIT()
            ) {
                // Sent and not yet gone from the seat's balance: still the trader's.
                v -= int256(uint256(p.fundedPayoutSent()));
            }
        }
    }

    /// The trader's share of `profit1e6`, in 1e8 units, the way `Pool` computes it; nothing on a loss.
    function _share(int256 profit1e6) internal view returns (int256) {
        if (profit1e6 <= 0) return 0;
        return (profit1e6 * int256(uint256(_plan.traderShareBps)) * int256(uint256(Units.SPOT_PER_PERP)))
            / int256(uint256(Units.BPS));
    }
}
