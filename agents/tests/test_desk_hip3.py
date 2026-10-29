"""The trader's desk on a HIP-3 perp (part 3): the account's list names xyz:XYZ100 by its order
number 750000, and the desk reads that dex for the perp, the account, its orders, the mid and the
market, and sends the order by that number. Offline: the chain, Hyperliquid and the gateway are fakes."""

from __future__ import annotations

import unittest
from unittest import mock

from agents import desk as dk
from agents.tests.fakes import ACCOUNT, FACTORY, NOW, FakeChain, FakeGateway, Wallet

XYZ = 65
XYZ100 = 750000


class HipChain(FakeChain):
    """The fake chain, with the account's list on BTC (3) and xyz:XYZ100 (750000), and Hyperliquid
    answering for dex xyz too."""

    def __init__(self):
        super().__init__()
        self.rules = (500, 1000, 300, (3, XYZ100))
        self.bodies = []
        self.xyz_positions = [{"coin": "xyz:XYZ100", "szi": "0.0005", "entryPx": "29000", "unrealizedPnl": "0.1"}]
        self.xyz_orders = [{"coin": "xyz:XYZ100", "side": "B", "limitPx": "28000", "sz": "0.0005", "oid": 81}]

    def info_post(self, body):
        self.bodies.append(body)
        if body["type"] == "perpDexs":
            return [None] + [{"name": f"d{i}"} for i in range(1, XYZ)] + [{"name": "xyz"}]
        if body.get("dex") != "xyz":
            return super().info_post(body)
        kind = body["type"]
        if kind == "meta":
            return {"universe": [{"name": "xyz:XYZ100", "szDecimals": 4, "maxLeverage": 30},
                                 {"name": "xyz:TSLA", "szDecimals": 3, "maxLeverage": 10}]}
        if kind == "clearinghouseState":
            return {"marginSummary": {"accountValue": "15.0", "totalNtlPos": "14.5", "totalMarginUsed": "0.5"},
                    "assetPositions": [{"position": p} for p in self.xyz_positions]}
        if kind == "openOrders":
            return list(self.xyz_orders)
        if kind == "allMids":
            return {"xyz:XYZ100": "29000", "xyz:TSLA": "250"}
        if kind == "metaAndAssetCtxs":
            ctx = {"midPx": "29000", "markPx": "29010", "oraclePx": "29005", "funding": "0.0000069",
                   "openInterest": "10", "dayNtlVlm": "264580587", "prevDayPx": "28800"}
            return [self.info_post({"type": "meta", "dex": "xyz"}), [ctx, dict(ctx, markPx="250")]]
        raise AssertionError(body)


class OnXyz(unittest.TestCase):
    def setUp(self):
        self.chain = HipChain()
        for patcher in (mock.patch.object(dk, "c", self.chain), mock.patch.object(dk.time, "time", return_value=float(NOW))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.gateway = FakeGateway()
        self.desk = dk.Desk(FACTORY, ACCOUNT, dk.Limits(), self.gateway, Wallet(), True)

    def test_the_list_names_a_hip3_perp_by_its_order_number(self):
        self.assertEqual(self.desk.perps["xyz:XYZ100"], (XYZ100, 4))
        self.assertEqual(self.desk.perps["BTC"], (3, 5))
        self.assertEqual(self.desk.dexes, ["", "xyz"])
        self.assertNotIn("xyz:TSLA", self.desk.perps, "on the dex, not on the account's list")

    def test_the_account_is_read_on_every_dex_its_list_is_on(self):
        view = self.desk.account_view()
        self.assertEqual(view["equity_usdc"], 1015.0, "1000 on Hyperliquid's own dex and 15 on xyz")
        self.assertIn("xyz:XYZ100", [p["coin"] for p in view["positions"]])
        self.assertEqual([o["oid"] for o in view["open_orders"]], [81])
        self.assertIn({"type": "clearinghouseState", "user": ACCOUNT, "dex": "xyz"}, self.chain.bodies)
        self.assertIn({"type": "openOrders", "user": ACCOUNT, "dex": "xyz"}, self.chain.bodies)

    def test_capital_not_yet_on_a_dex_is_a_state_not_an_error(self):
        view = self.desk.account_view()
        self.assertEqual(view["equity_by_dex_usdc"], {"hyperliquid": 1000.0, "xyz": 15.0})
        self.assertEqual(view["capital_not_yet_on"], [])
        self.chain.xyz_positions = []
        real = self.chain.info_post

        def empty_xyz(body):
            if body.get("dex") == "xyz" and body["type"] == "clearinghouseState":
                return {"marginSummary": {"accountValue": "0.0", "totalNtlPos": "0.0", "totalMarginUsed": "0.0"},
                        "assetPositions": []}
            return real(body)
        with mock.patch.object(self.chain, "info_post", empty_xyz):
            view = self.desk.account_view()
        self.assertEqual(view["capital_not_yet_on"], ["xyz"])
        self.assertEqual(view["equity_usdc"], 1000.0)

    def test_the_market_of_a_hip3_perp_is_its_dexs(self):
        view = self.desk.market_view("xyz:XYZ100")
        self.assertEqual((view["mark"], view["max_exchange_leverage"], view["size_decimals"]), (29010.0, 30, 4))
        self.assertIn({"type": "metaAndAssetCtxs", "dex": "xyz"}, self.chain.bodies)

    def test_the_mid_of_a_hip3_perp_is_asked_of_its_dex(self):
        self.assertEqual(dk.market_mid("xyz:XYZ100"), 29000.0)
        self.assertEqual(self.chain.bodies[-1], {"type": "allMids", "dex": "xyz"})

    def test_an_order_on_a_hip3_perp_goes_by_its_order_number(self):
        self.desk.place_order("xyz:XYZ100", "buy", 0.0005, 29000, "limit", False)
        self.assertEqual([(o[1], o[3], o[4]) for o in self.gateway.orders], [(XYZ100, "29000", "0.0005")])

    def test_a_pool_for_sale_names_its_hip3_perps(self):
        shop = dk.Shop(FACTORY, 100.0)
        perps = {tuple(o["rules"]["perps"]) for o in shop.listing()}
        self.assertIn(("BTC", "xyz:XYZ100"), perps)

    def test_a_dex_whose_names_cannot_be_read_is_asked_once_a_listing(self):
        shop = dk.Shop(FACTORY, 100.0)
        self.chain.bodies.clear()
        for asset in (850000, 850001, 850002):  # dex 75, which this fake's perpDexs does not list
            self.assertEqual(shop._name(asset), f"#{asset}")
        self.assertEqual([b["type"] for b in self.chain.bodies], ["perpDexs"])

    def test_a_v3_accounts_own_cap_for_each_asset_is_read_and_shown(self):
        self.assertEqual(self.desk.account_view()["rules"]["max_leverage_by_asset"], {}, "an account before v3 has none")
        self.chain.rules = (500, 1000, 1000, (3, XYZ100), (1000, 800))
        desk = dk.Desk(FACTORY, ACCOUNT, dk.Limits(), self.gateway, Wallet(), True)
        self.assertEqual(desk.account_view()["rules"]["max_leverage_by_asset"], {"BTC": 10.0, "xyz:XYZ100": 8.0})
        self.assertEqual(desk.rules["leverage_x100"], 1000, "the account's own cap, read with them")
        offers = dk.Shop(FACTORY, 100.0).listing()
        self.assertIn({"BTC": 10.0, "xyz:XYZ100": 8.0}, [o["rules"]["max_leverage_by_asset"] for o in offers])

    def test_a_v3_accounts_start_surplus_is_no_part_of_its_equity(self):
        # Self-audit v3, round 1, T1: the contract counts Hyperliquid's equity less the start's surplus.
        self.chain.rules = (500, 1000, 1000, (3, XYZ100), (1000, 800))
        self.chain.surplus = 25_000_000
        desk = dk.Desk(FACTORY, ACCOUNT, dk.Limits(), self.gateway, Wallet(), True)
        # 1000 on Hyperliquid's own dex and 15 on xyz, as Hyperliquid shows it; the contract counts 990.
        self.assertEqual(desk.account_view()["equity_usdc"], 990.0)
        self.assertEqual(desk.equity({"accountValue": "1015"}), 990.0)
        # One built before the fix set has no such function: no surplus, and the desk still opens.
        self.chain.surplus = "revert"
        self.assertEqual(dk.Desk(FACTORY, ACCOUNT, dk.Limits(), self.gateway, Wallet(), True).start_surplus, 0)

    def test_an_account_before_v3_is_not_asked_for_a_surplus(self):
        self.chain.rules = (500, 1000, 300, (3, 4))
        self.chain.surplus = "revert"
        dk.Desk(FACTORY, ACCOUNT, dk.Limits(), self.gateway, Wallet(), True)
        self.assertEqual(self.chain.surplus_reads, 0)

    def test_a_list_on_hyperliquids_own_dex_alone_reads_no_other_dex(self):
        self.chain.rules = (500, 1000, 300, (3, 4))
        self.chain.bodies.clear()
        desk = dk.Desk(FACTORY, ACCOUNT, dk.Limits(), self.gateway, Wallet(), True)
        desk.account_view()
        self.assertEqual(desk.dexes, [""])
        self.assertFalse([b for b in self.chain.bodies if b["type"] == "perpDexs" or "dex" in b])


if __name__ == "__main__":
    unittest.main()
