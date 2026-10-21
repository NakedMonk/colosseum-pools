"""The keeper on every perp dex it is told to read (HIP-3, part 3): the perps by the number an order
uses for them, an account's positions and orders on every dex in one reading, the stop check and
the stop's cancel and forbidden-asset lists across dexes, and a HIP-3 position still open when the
pool's default says flat over the weekend."""

from __future__ import annotations

import inspect
import unittest
from datetime import datetime, timezone
from unittest import mock

from ops import keeper

ACCOUNT = "0x00000000000000000000000000000000000000a2"
XYZ = 65
XYZ100 = 750000


def utc(*args) -> float:
    return datetime(*args, tzinfo=timezone.utc).timestamp()


class Venue:
    """Hyperliquid's info API for one account on two dexes: Hyperliquid's own and xyz (index 65)."""

    def __init__(self):
        self.bodies = []
        self.positions = {"": [("BTC", "0.001")], "xyz": [("xyz:XYZ100", "0.0005")]}
        self.orders = {"": [], "xyz": []}

    def info_post(self, body):
        self.bodies.append(body)
        dex = body.get("dex", "")
        if body["type"] == "perpDexs":
            return [None] + [{"name": f"d{i}"} for i in range(1, XYZ)] + [{"name": "xyz"}]
        if body["type"] == "meta":
            names = ("xyz:XYZ100", "xyz:TSLA", "xyz:NVDA", "xyz:GOLD") if dex == "xyz" else ("SOL", "APT", "ATOM", "BTC", "ETH")
            return {"universe": [{"name": n} for n in names]}
        if body["type"] == "clearinghouseState":
            return {"marginSummary": {"accountValue": "1"},
                    "assetPositions": [{"position": {"coin": c, "szi": s}} for c, s in self.positions[dex]]}
        if body["type"] == "frontendOpenOrders":
            return list(self.orders[dex])
        raise AssertionError(body)


def stop(coin: str, oid: int, side: str = "A") -> dict:
    return {"coin": coin, "oid": oid, "side": side, "isTrigger": True, "reduceOnly": True,
            "orderType": "Stop Market", "isPositionTpsl": True, "sz": "0", "triggerPx": "1"}


class OnEveryDex(unittest.TestCase):
    def setUp(self):
        self.venue = Venue()
        for target, value in ((keeper, "c"),):
            patcher = mock.patch.object(target, value, self.venue)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in (("PERP_DEXES", ("xyz",)), ("_dex_index", {}), ("_perp_names", {})):
            patcher = mock.patch.object(keeper, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.log = mock.patch.object(keeper, "log").start()
        self.addCleanup(mock.patch.stopall)

    def logged(self, event):
        return [call.kwargs for call in self.log.call_args_list if call.args and call.args[0] == event]

    def test_every_dexs_perps_go_by_the_number_an_order_uses(self):
        names = keeper.perp_index_by_name()
        self.assertEqual((names["BTC"], names["SOL"], names["xyz:XYZ100"], names["xyz:GOLD"]), (3, 0, XYZ100, 750003))
        keeper.perp_index_by_name()
        self.assertEqual([b["type"] for b in self.venue.bodies].count("perpDexs"), 1, "the dex list is read once")

    def test_hyperliquids_own_dex_alone_is_read_as_before(self):
        with mock.patch.object(keeper, "PERP_DEXES", ()):
            names = keeper.perp_index_by_name()
            keeper.read_book(ACCOUNT)
        self.assertNotIn("xyz:XYZ100", names)
        self.assertEqual(self.venue.bodies, [{"type": "meta"}, {"type": "clearinghouseState", "user": ACCOUNT},
                                             {"type": "frontendOpenOrders", "user": ACCOUNT}])

    def test_the_account_is_read_on_every_dex_in_one_shape(self):
        self.venue.orders["xyz"] = [stop("xyz:XYZ100", 77)]
        state, orders = keeper.read_book(ACCOUNT)
        self.assertEqual([p["position"]["coin"] for p in state["assetPositions"]], ["BTC", "xyz:XYZ100"])
        self.assertEqual([o["oid"] for o in orders], [77])
        self.assertIn({"type": "clearinghouseState", "user": ACCOUNT, "dex": "xyz"}, self.venue.bodies)
        self.assertIn({"type": "frontendOpenOrders", "user": ACCOUNT, "dex": "xyz"}, self.venue.bodies)

    def test_a_hip3_position_without_its_stop_is_called_out_and_with_it_is_not(self):
        self.venue.orders[""] = [stop("BTC", 5)]
        self.assertEqual(keeper.unprotected(ACCOUNT), [{"coin": "xyz:XYZ100", "size": "0.0005"}])
        self.venue.orders["xyz"] = [stop("xyz:XYZ100", 77)]
        self.assertEqual(keeper.unprotected(ACCOUNT), [])

    def test_a_stop_cancels_and_names_hip3_perps_by_their_order_number(self):
        names = keeper.perp_index_by_name()
        self.venue.orders["xyz"] = [stop("xyz:XYZ100", 77)]
        cancels, extra = keeper.stop_inputs(ACCOUNT, {3}, names, keeper.read_book(ACCOUNT))
        self.assertEqual(cancels, [(XYZ100, 77)])
        self.assertEqual(extra, [XYZ100], "a perp outside the rules is named by the number the contract is given")

    def test_a_hip3_position_open_over_the_weekend_is_said_and_one_of_hyperliquids_own_is_not(self):
        book = keeper.read_book(ACCOUNT)
        keeper.check_protection(ACCOUNT, book, utc(2026, 10, 23, 20, 29))  # Friday 16:29 New York
        self.assertEqual(self.logged("hip3_open_over_weekend"), [])
        keeper.check_protection(ACCOUNT, book, utc(2026, 10, 23, 20, 30))  # Friday 16:30
        keeper.check_protection(ACCOUNT, book, utc(2026, 11, 8, 23, 4))    # Sunday 18:04, after the clocks changed
        keeper.check_protection(ACCOUNT, book, utc(2026, 11, 8, 23, 5))    # Sunday 18:05: open again
        self.assertEqual(self.logged("hip3_open_over_weekend"),
                         [{"account": ACCOUNT, "coin": "xyz:XYZ100", "size": "0.0005"}] * 2)

    def test_the_keeper_is_told_the_dexes_on_its_command_line(self):
        body = inspect.getsource(keeper.main)
        self.assertIn('p.add_argument("--perp-dexes", default=os.environ.get("COLOSSEUM_PERP_DEXES", "")', body)
        self.assertIn("PERP_DEXES = tuple(d.strip() for d in args.perp_dexes.split(\",\") if d.strip())", body)
        self.assertIn("check_protection(ch, book, now)", inspect.getsource(keeper.challenge_pass))
        self.assertIn("check_protection(pool, book, now)", inspect.getsource(keeper.pool_pass))


if __name__ == "__main__":
    unittest.main()
