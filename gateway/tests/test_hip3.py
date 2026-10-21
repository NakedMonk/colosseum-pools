"""HIP-3 in the gateway, part 1: the perps of more than one dex, numbered the way Hyperliquid numbers
them, each with what its dex says about it, and a market the pool may not open on refused before
anything is signed.

The fixtures follow what Hyperliquid's testnet answered on 19 and 21 Oct 2026: `perpDexs` lists
the first dex as null and xyz at index 65; xyz's `meta` has XYZ100 at 0 and GOLD at 3 (cross margin
allowed), CL at 29 ("noCross" on testnet), BRENTOIL at 49 (delisted) and SP500 at 52 (delisted,
"strictIsolated")."""

from __future__ import annotations

import inspect
import unittest
from decimal import Decimal
from unittest import mock

from gateway import assets, demo_signer, hl, server
from gateway.checks import GatewayError, check_market
from gateway.protect import Market, parse_markets
from gateway.tests.test_protect import BTC, MARKETS, FlowBase

XYZ = 65


def perp_dexs() -> list:
    return [None] + [{"name": f"d{i}", "fullName": "", "deployer": "0x0"} for i in range(1, XYZ)] + [
        {"name": "xyz", "fullName": "XYZ", "deployer": "0x88806a71d74ad0a510b350545c9ae490912f0888"}]


def xyz_meta_and_ctxs() -> list:
    universe = [{"name": f"xyz:F{i}", "szDecimals": 2, "maxLeverage": 10} for i in range(53)]
    universe[0] = {"name": "xyz:XYZ100", "szDecimals": 4, "maxLeverage": 30, "marginTableId": 30}
    universe[3] = {"name": "xyz:GOLD", "szDecimals": 4, "maxLeverage": 25, "marginTableId": 25}
    universe[29] = {"name": "xyz:CL", "szDecimals": 3, "maxLeverage": 20, "marginMode": "noCross"}
    universe[49] = {"name": "xyz:BRENTOIL", "szDecimals": 2, "maxLeverage": 20, "isDelisted": True}
    universe[52] = {"name": "xyz:SP500", "szDecimals": 3, "maxLeverage": 50, "marginMode": "strictIsolated",
                    "isDelisted": True}
    ctxs = [{"markPx": "100.0"} for _ in universe]
    ctxs[0], ctxs[3] = {"markPx": "29000.0"}, {"markPx": "4421.1"}
    return [{"universe": universe}, ctxs]


def main_meta_and_ctxs() -> list:
    universe = [{"name": "SOL", "szDecimals": 2, "maxLeverage": 20}, {"name": "APT", "szDecimals": 2, "maxLeverage": 10},
                {"name": "ATOM", "szDecimals": 2, "maxLeverage": 10, "onlyIsolated": True},
                {"name": "BTC", "szDecimals": 5, "maxLeverage": 40}]
    return [{"universe": universe}, [{"markPx": "150"}, {"markPx": "5"}, {"markPx": "4"}, {"markPx": "60000"}]]


class Numbering(unittest.TestCase):
    def test_an_order_numbers_a_hip3_perp_100000_above_its_dex_and_index_and_a_precompile_100000_below(self):
        self.assertEqual(assets.order_asset(0, 3), 3, "Hyperliquid's own dex keeps the plain index")
        self.assertEqual(assets.order_asset(XYZ, 3), 750003, "xyz:GOLD on testnet")
        self.assertEqual(assets.order_asset(1, 52), 110052, "xyz:SP500 on mainnet, where xyz is dex 1")
        self.assertEqual(assets.precompile_index(750003), 650003, "measured: markPx(650003) is xyz:GOLD's mark")
        self.assertEqual(assets.precompile_index(110052), 10052)
        self.assertEqual(assets.precompile_index(3), 3)
        self.assertEqual(assets.dex_index_of(750029), XYZ)
        self.assertEqual(assets.dex_index_of(4), 0)
        for bad in ((0, 10_000), (XYZ, -1), (-1, 0)):
            with self.assertRaises(ValueError, msg=str(bad)):
                assets.order_asset(*bad)

    def test_perp_dexs_name_each_dex_by_its_index_and_the_first_is_hyperliquids_own(self):
        names = assets.parse_perp_dexs(perp_dexs())
        self.assertEqual((names[assets.MAIN_DEX], names["xyz"], len(names)), (0, XYZ, XYZ + 1))
        for bad in ({}, [], [{"name": "xyz"}], [None, {"fullName": "no name"}], [None, {"name": "a"}, {"name": "a"}]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                assets.parse_perp_dexs(bad)


class Markets(unittest.TestCase):
    def test_a_hip3_dex_is_keyed_by_order_number_with_delisting_and_margin_mode(self):
        m = parse_markets(xyz_meta_and_ctxs(), XYZ)
        self.assertEqual(m[750003], Market("xyz:GOLD", Decimal("4421.1"), 4, max_leverage=25))
        self.assertEqual((m[750000].coin, m[750000].margin_mode, m[750000].delisted), ("xyz:XYZ100", None, False))
        self.assertEqual((m[750029].coin, m[750029].margin_mode), ("xyz:CL", "noCross"))
        self.assertEqual((m[750049].coin, m[750049].delisted), ("xyz:BRENTOIL", True))
        self.assertEqual((m[750052].coin, m[750052].delisted, m[750052].margin_mode), ("xyz:SP500", True, "strictIsolated"))
        self.assertNotIn(3, m, "no plain index from a HIP-3 dex")

    def test_hyperliquids_own_dex_keeps_plain_numbers_and_reads_the_older_isolated_flag(self):
        m = parse_markets(main_meta_and_ctxs())
        self.assertEqual((m[BTC].coin, m[BTC].mark, m[BTC].margin_mode, m[BTC].max_leverage), ("BTC", Decimal("60000"), None, 40))
        self.assertEqual(m[2].margin_mode, "noCross", "onlyIsolated is the older field for the same thing")


class Reads(unittest.TestCase):
    def answers(self):
        self.bodies = []

        def info(body, timeout):
            self.bodies.append(body)
            if body["type"] == "perpDexs":
                return perp_dexs()
            if body["type"] == "metaAndAssetCtxs":
                return xyz_meta_and_ctxs() if body.get("dex") == "xyz" else main_meta_and_ctxs()
            if body["type"] == "allMids":
                return {"xyz:GOLD": "4421.1"} if body.get("dex") == "xyz" else {"BTC": "60000"}
            raise AssertionError(body)
        return mock.patch.object(hl, "_info", info)

    def test_each_dex_is_one_request_with_its_name_and_the_dex_list_is_read_once(self):
        with self.answers():
            venue = hl.Venue(dexes=(hl.MAIN_DEX, "xyz"))
            first, second = venue.markets(), venue.markets()
        self.assertEqual(first, second)
        self.assertEqual((first[BTC].coin, first[750003].coin), ("BTC", "xyz:GOLD"))
        self.assertEqual(self.bodies, [{"type": "metaAndAssetCtxs"}, {"type": "perpDexs"},
                                       {"type": "metaAndAssetCtxs", "dex": "xyz"},
                                       {"type": "metaAndAssetCtxs"}, {"type": "metaAndAssetCtxs", "dex": "xyz"}])

    def test_by_default_only_hyperliquids_own_dex_is_read(self):
        with self.answers():
            markets = hl.Venue().markets()
        self.assertEqual(self.bodies, [{"type": "metaAndAssetCtxs"}])
        self.assertEqual(set(markets), {0, 1, 2, 3})

    def test_a_dex_hyperliquid_does_not_list_is_an_error_after_one_fresh_read(self):
        with self.answers():
            venue = hl.Venue(dexes=(hl.MAIN_DEX, "nope"))
            with self.assertRaises(GatewayError) as e:
                venue.markets()
        self.assertEqual(e.exception.code, "no_such_dex")
        self.assertEqual([b["type"] for b in self.bodies], ["metaAndAssetCtxs", "perpDexs"])

    def test_mids_are_asked_of_one_dex(self):
        with self.answers():
            self.assertEqual(hl.mids(), {"BTC": "60000"})
            self.assertEqual(hl.mids(dex="xyz"), {"xyz:GOLD": "4421.1"})
        self.assertEqual(self.bodies, [{"type": "allMids"}, {"type": "allMids", "dex": "xyz"}])

    def test_a_hip3_perps_mid_is_its_dexs(self):
        asked = []
        with mock.patch.dict(demo_signer.COINS, {750003: "xyz:GOLD"}), \
                mock.patch.object(hl, "mids", lambda timeout=5.0, dex=hl.MAIN_DEX: asked.append(dex) or {"xyz:GOLD": "4421.1"}):
            self.assertEqual(demo_signer.market_mid(750003), Decimal("4421.1"))
        self.assertEqual(asked, ["xyz"])

    def test_the_gateway_reads_the_dexes_it_is_told_to_besides_hyperliquids_own(self):
        body = inspect.getsource(server.main)
        self.assertIn('os.environ.get("GATEWAY_PERP_DEXES", "")', body)
        self.assertIn("venue=hl.Venue(dexes=(hl.MAIN_DEX, *dexes))", body)


class MarketCheck(unittest.TestCase):
    def test_a_perp_that_is_delisted_or_not_cross_margin_is_refused(self):
        m = parse_markets(xyz_meta_and_ctxs(), XYZ)
        check_market(750003, m)  # GOLD: cross margin allowed, listed
        check_market(750000, m)  # XYZ100
        cases = {750049: (403, "market_delisted"), 750052: (403, "market_delisted"), 750029: (403, "margin_mode"),
                 999_999: (502, "no_market_price")}
        for asset, want in cases.items():
            with self.assertRaises(GatewayError, msg=str(asset)) as e:
                check_market(asset, m)
            self.assertEqual((e.exception.status, e.exception.code), want, asset)

    def test_any_margin_mode_at_all_is_refused_a_new_one_too(self):
        for mode in ("noCross", "strictIsolated", "isolatedSomeNewWay"):
            with self.assertRaises(GatewayError, msg=mode) as e:
                check_market(1, {1: Market("X", Decimal(1), 2, margin_mode=mode)})
            self.assertEqual(e.exception.code, "margin_mode")
            self.assertIn(mode, e.exception.detail)


class Refusals(FlowBase):
    """Before anything is signed or placed: no stop, no take, no order."""

    def test_an_order_on_a_delisted_perp_sends_nothing(self):
        self.x.markets_now[BTC] = Market("BTC", Decimal("60000"), 5, delisted=True)
        status, out = self.send()
        self.assertEqual((status, out["code"], out["sent"]), (403, "market_delisted", False))
        self.assertEqual(self.x.sent, [])

    def test_an_order_on_a_perp_without_cross_margin_sends_nothing(self):
        self.x.markets_now[BTC] = Market("BTC", Decimal("60000"), 5, margin_mode="noCross")
        status, out = self.send()
        self.assertEqual((status, out["code"], out["sent"]), (403, "margin_mode", False))
        self.assertEqual(self.x.sent, [])

    def test_closing_a_position_on_a_delisted_perp_still_goes(self):
        # Delisting must not trap a position the account already holds.
        self.x.positions = {BTC: Decimal("0.005")}
        self.x.markets_now[BTC] = Market("BTC", Decimal("60000"), 5, delisted=True)
        status, out = self.send(isBuy=False, reduceOnly=True, limitPx="59000")
        self.assertEqual((status, out["status"]), (200, "submitted"))

    def test_an_order_on_an_ordinary_perp_is_unchanged(self):
        self.assertEqual(MARKETS[BTC].margin_mode, None)
        status, out = self.send()
        self.assertEqual((status, out["status"]), (200, "submitted"))


if __name__ == "__main__":
    unittest.main()
