// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.28;

import {CoreSimulatorLib} from "@hyper-evm-lib/test/simulation/CoreSimulatorLib.sol";
import {PrecompileLib} from "@hyper-evm-lib/src/PrecompileLib.sol";
import {HLConstants} from "@hyper-evm-lib/src/common/HLConstants.sol";

import {Rules, Terms, Cancel, Breach} from "../src/Types.sol";
import {Pool} from "../src/Pool.sol";
import {ChallengeAccount} from "../src/ChallengeAccount.sol";
import {PoolFactory} from "../src/PoolFactory.sol";
import {CoreOps} from "../src/lib/CoreOps.sol";
import {PoolHarness} from "./PoolHarness.sol";

/// HIP-3: markets on a builder-deployed perp dex (the CTO, 21 October 2026). КОЛИЗЕЙ-2's spike q9 on
/// testnet measured what these tests hold to:
/// - a contract moves its capital to the dex with sendAsset (action 13) from spot, and
///   accountMarginSummary(dex, account) reads it;
/// - CoreWriter's order and cancel take the asset's order id, 100000 + dex * 10000 + index;
/// - the precompiles take the id less 100000, and a wrong one reads another dex's market.
/// The simulator has dex 1 with USDC as its collateral; its first market here is "xyz:XYZ100".
contract Hip3Test is PoolHarness {
    uint32 constant DEX = 1;
    uint32 constant XYZ = 110_000; // the order id: 100000 + 1 * 10000 + 0
    uint32 constant XYZ_PX = 10_000; // what the precompiles take
    uint64 constant XYZ_MARK = 29_333_00; // 29,333.00 with 6 - szDecimals = 2 decimals

    function setUp() public override {
        super.setUp();
        hyperCore.registerPerpAssetInfo(
            XYZ_PX,
            PrecompileLib.PerpAssetInfo({
                coin: "xyz:XYZ100", marginTableId: 30, szDecimals: 4, maxLeverage: 30, onlyIsolated: false
            })
        );
        CoreSimulatorLib.setMarkPx(XYZ_PX, XYZ_MARK);
        uint32[] memory listed = new uint32[](1);
        listed[0] = XYZ;
        string[] memory names = new string[](1);
        names[0] = "xyz:XYZ100";
        vm.prank(operator);
        factory.setPlatformAssets(listed, names, true);
    }

    function _xyzRules() internal pure returns (Rules memory r) {
        r = _rules();
        r.assets[0] = XYZ;
    }

    function _xyzPool() internal returns (Pool p) {
        vm.prank(investor);
        p = Pool(factory.createPool(_xyzRules(), _terms()));
        CoreSimulatorLib.forceSpotBalance(address(p), 0, 1000e8);
        p.prepareAccount();
    }

    function test_hip3_theIdsAndTheDex() public pure {
        assertEq(CoreOps.pxIndex(3), 3, "the main dex: one number");
        assertEq(CoreOps.pxIndex(99_999), 99_999);
        assertEq(CoreOps.pxIndex(100_000), 0);
        assertEq(CoreOps.pxIndex(750_000), 650_000, "xyz's first market, as q9 read it");
        assertEq(CoreOps.dexOf(3), 0);
        assertEq(CoreOps.dexOf(750_000), 65);
        assertEq(CoreOps.dexOf(759_999), 65);
        assertEq(CoreOps.dexOf(760_000), 66);
    }

    /// The account's dex is its markets', its capital goes there by sendAsset at the activation,
    /// and its equity is the main dex's and that dex's.
    function test_hip3_aChallengesCapitalGoesToItsDex_andItsEquityIsRead_there() public {
        Pool p = _xyzPool();
        assertEq(p.dex(), DEX);
        ChallengeAccount ch = _buy(p);
        assertEq(ch.dex(), DEX);
        CoreSimulatorLib.nextBlock();
        vm.recordLogs();
        ch.activate();
        (, uint24[] memory kind, bytes[] memory args) = _actions(vm.getRecordedLogs());
        bool sent;
        for (uint256 i = 0; i < kind.length; ++i) {
            assertTrue(kind[i] != 7, "no class transfer to the main dex");
            if (kind[i] == 13) {
                assertEq(args[i], abi.encode(address(ch), address(0), HLConstants.SPOT_DEX, DEX, uint64(0), uint64(100e8)));
                sent = true;
            }
        }
        assertTrue(sent, "the capital is sent to the dex");
        CoreSimulatorLib.nextBlock();
        assertEq(PrecompileLib.accountMarginSummary(DEX, address(ch)).accountValue, 100e6);
        assertEq(ch.accountEquity(), 100e6, "the main dex's and the dex's");
        assertEq(uint8(ch.violation(new uint32[](0))), uint8(Breach.None));
    }

    /// A funded stage on the dex has its capital sent there, and its start is the dex's equity.
    function test_hip3_aFundedStageOnTheDexHasItsCapitalThere() public {
        Pool p = _xyzPool();
        vm.startPrank(investor);
        p.runByOwner(false);
        vm.recordLogs();
        p.openFundedFor(trader);
        vm.stopPrank();
        (, uint24[] memory kind, bytes[] memory args) = _actions(vm.getRecordedLogs());
        bool sent;
        for (uint256 i = 0; i < kind.length; ++i) {
            assertTrue(kind[i] != 7, "no class transfer to the main dex");
            if (kind[i] == 13) {
                assertEq(args[i], abi.encode(address(p), address(0), HLConstants.SPOT_DEX, DEX, uint64(0), uint64(200e8)));
                sent = true;
            }
        }
        assertTrue(sent);
        assertEq(p.fundedStart(), 200e6);
        CoreSimulatorLib.nextBlock();
        assertEq(p.accountEquity(), 200e6);
    }

    /// A position on the dex is read by the index and closed by the order id.
    function test_hip3_aPositionIsReadByTheIndex_andClosedByTheOrderId() public {
        ChallengeAccount ch = _buy(_xyzPool());
        CoreSimulatorLib.nextBlock();
        ch.activate();
        CoreSimulatorLib.nextBlock();
        vm.mockCall(
            HLConstants.POSITION2_PRECOMPILE_ADDRESS,
            abi.encode(address(ch), XYZ_PX),
            abi.encode(PrecompileLib.Position({szi: 30, entryNtl: 88e6, isolatedRawUsd: 0, leverage: 20, isIsolated: false}))
        );
        (Cancel[] memory c, uint32[] memory a) = _none();
        vm.recordLogs();
        vm.prank(trader);
        ch.forfeit(c, a, SALT);
        (, uint24[] memory kind, bytes[] memory args) = _actions(vm.getRecordedLogs());
        bool closed;
        for (uint256 i = 0; i < kind.length; ++i) {
            if (kind[i] != 1) continue;
            (uint32 asset, bool isBuy, uint64 px, uint64 sz, bool reduceOnly,,) =
                abi.decode(args[i], (uint32, bool, uint64, uint64, bool, uint8, uint128));
            assertEq(asset, XYZ, "the order names the order id");
            assertFalse(isBuy);
            // The dex's mark less 5 %, 27,866.35, five integer digits so rounded to 27,866, in 1e8.
            assertEq(px, 2_786_600_000_000, "priced from the index's mark");
            assertEq(sz, 30 * 1e4, "0.0030 in 1e8, at the market's four size decimals");
            assertTrue(reduceOnly);
            closed = true;
        }
        assertTrue(closed);
    }

    /// Settling moves the dex's whole value back to spot by sendAsset, and nothing counts as off the
    /// perp side until the dex is empty.
    function test_hip3_settlingMovesTheDexsValueBackToSpot() public {
        ChallengeAccount ch = _buy(_xyzPool());
        CoreSimulatorLib.nextBlock();
        ch.activate();
        CoreSimulatorLib.nextBlock();
        (Cancel[] memory c, uint32[] memory a) = _none();
        vm.prank(trader);
        ch.forfeit(c, a, SALT);
        vm.recordLogs();
        ch.settle(c, a);
        (, uint24[] memory kind, bytes[] memory args) = _actions(vm.getRecordedLogs());
        bool back;
        for (uint256 i = 0; i < kind.length; ++i) {
            if (kind[i] == 13) {
                assertEq(args[i], abi.encode(address(ch), address(0), DEX, HLConstants.SPOT_DEX, uint64(0), uint64(100e8)));
                back = true;
            }
        }
        assertTrue(back, "the dex's value is sent back to spot");
        assertEq(uint8(ch.status()), uint8(ChallengeAccount.Status.Forfeited), "not settled while the dex holds it");
        CoreSimulatorLib.nextBlock();
        assertEq(PrecompileLib.accountMarginSummary(DEX, address(ch)).accountValue, 0);
    }

    /// Listing checks the market's name: one id off reads another market, and is refused.
    function test_hip3_listingChecksTheMarketsName() public {
        uint32[] memory one = new uint32[](1);
        one[0] = XYZ;
        string[] memory wrong = new string[](1);
        wrong[0] = "xyz:TSLA";
        vm.prank(operator);
        vm.expectRevert(abi.encodeWithSelector(PoolFactory.NotThatMarket.selector, XYZ, "xyz:XYZ100"));
        factory.setFundedAssets(one, wrong, true);
        one[0] = XYZ + 1; // nothing registered there in the simulator: no name
        string[] memory right = new string[](1);
        right[0] = "xyz:XYZ100";
        vm.prank(operator);
        vm.expectRevert(abi.encodeWithSelector(PoolFactory.NotThatMarket.selector, XYZ + 1, ""));
        factory.setFundedAssets(one, right, true);
        one[0] = XYZ;
        vm.prank(operator);
        factory.setFundedAssets(one, right, true);
        assertTrue(factory.isFundedAsset(XYZ));
    }

    /// One dex an account: rules naming markets on two dexes are refused.
    function test_hip3_rulesOnTwoDexesAreRefused() public {
        Rules memory r = _rules();
        uint32[] memory both = new uint32[](2);
        both[0] = BTC;
        both[1] = XYZ;
        r.assets = both;
        Terms memory t = _terms();
        vm.prank(investor);
        vm.expectRevert(PoolFactory.BadRules.selector);
        factory.createPool(r, t);
    }
}
