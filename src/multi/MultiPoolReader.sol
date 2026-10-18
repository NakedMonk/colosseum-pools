// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.28;

import {Units} from "../Types.sol";
import {CoreOps} from "../lib/CoreOps.sol";
import {CodeStore} from "../lib/CodeStore.sol";
import {MultiPool, Plan} from "./MultiPool.sol";

/// @title MultiPoolReader
/// @notice What a MultiPool says about itself that only reads, out of the pool, which is close to
///         the size a contract may have (the CTO, 18 October 2026). It holds nothing, changes
///         nothing, and reads only what the pool shows anyone; the keeper and the scripts read
///         through it.
contract MultiPoolReader {
    error NoSuchLine();

    /// @notice How many more exams of `line` the pool would sell right now, by every limit a sale
    ///         holds it to: the share in work, measured without the leavers' part of the pool as it
    ///         is now; its balance on HyperCore, which has to hold what passes still running will ask
    ///         for, what it sent in the last minute and the leavers' part; the registry's free keys,
    ///         one for every seat spoken for; and its seats.
    function room(MultiPool m, uint256 line) external view returns (uint256 n) {
        Plan memory p = abi.decode(CodeStore.read(m.planData()), (Plan));
        if (line >= p.menu.length) revert NoSuchLine();
        if (m.closing()) return 0;
        uint256 size = p.menu[line].size;
        uint256 held = m.held();
        n = p.maxSeats - held;
        uint256 v = m.value();
        uint256 leaving = m.reserve() == 0 ? 0 : (v * m.reserveShare()) / 1e18;
        n = _least(n, _byShare(m, v > leaving ? v - leaving : 0, p.inWorkBps, size));
        n = _least(n, _byBalance(m, leaving, size));
        uint256 k = m.factory().registry().freeCount();
        n = _least(n, k > held ? k - held : 0);
    }

    function _byShare(MultiPool m, uint256 working, uint256 inWorkBps, uint256 size) internal view returns (uint256) {
        uint256 cap = (working * inWorkBps) / Units.BPS / Units.SPOT_PER_PERP;
        uint256 inWork = m.inWork();
        return cap > inWork ? (cap - inWork) / size : 0;
    }

    function _byBalance(MultiPool m, uint256 leaving, uint256 size) internal view returns (uint256) {
        uint256 recent = block.timestamp > m.lastSendAt() + m.ARM_WAIT() ? 0 : m.inFlight();
        uint256 owed = m.promised() * Units.SPOT_PER_PERP + m.waiting() * Units.NEW_ACCOUNT_FEE + recent + leaving;
        uint256 spot = CoreOps.spotUsdc(address(m));
        return spot > owed ? (spot - owed) / (size * Units.SPOT_PER_PERP + Units.NEW_ACCOUNT_FEE) : 0;
    }

    function _least(uint256 a, uint256 b) internal pure returns (uint256) {
        return a < b ? a : b;
    }

    /// @notice Whether the pool was deployed with the bounds it is offered with: a term of six months
    ///         to two years, fourteen days of notice and renewals of ninety. A page shows any other
    ///         pool as such.
    function productBounds(MultiPool m) external view returns (bool) {
        return m.minTerm() == m.PRODUCT_MIN_TERM() && m.maxTerm() == m.PRODUCT_MAX_TERM()
            && m.notice() == m.MAX_NOTICE() && m.renewal() == m.PRODUCT_RENEWAL();
    }
}
