"""The keeper's share of the host's minute of Hyperliquid's weight: an active account's positions every
pass, its open orders only when the positions changed or the list is a minute old, the orders a stop
names always read afresh, and what the keeper spent written to its log after every pass. Offline: the
fakes of test_keeper.py and test_keeper_protect.py.

    spike/.venv/bin/python -m unittest ops.tests.test_keeper_weight
"""

from __future__ import annotations

import unittest
from unittest import mock

from gateway import weight
from ops import keeper
from ops.tests.test_keeper import CHALLENGE_A, NOON, POOL_A, KeeperTest, challenge_log
from ops.tests.test_keeper_protect import Chain, stop_order


class OrdersOnChange(KeeperTest):
    def setUp(self):
        super().setUp()
        self.chain = Chain()
        patcher = mock.patch.object(keeper, "c", self.chain)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.logged = []
        loud = mock.patch.object(keeper, "log", side_effect=lambda event, **f: self.logged.append((event, f)))
        loud.start()
        self.addCleanup(loud.stop)
        self.meter = weight.Meter()
        metered = mock.patch.object(keeper, "METER", self.meter)
        metered.start()
        self.addCleanup(metered.stop)
        self.chain.add_pool(POOL_A, stage=keeper.CHALLENGE, challenge=CHALLENGE_A)
        self.chain.add_challenge(CHALLENGE_A, status=keeper.ACTIVE)
        self.chain.logs.append(challenge_log(10, POOL_A))
        self.chain.sizes[CHALLENGE_A.lower()] = [("BTC", "0.005")]
        self.chain.orders[CHALLENGE_A.lower()] = [stop_order("BTC", "A")]
        self.keeper = self.make()

    def a_pass(self) -> list[str]:
        self.chain.reads.clear()
        self.keeper.one_pass()
        return [r for r in self.chain.reads if r != "meta"]

    def test_with_nothing_changed_an_account_costs_its_positions_alone(self):
        self.assertEqual(self.a_pass(), ["clearinghouseState", "frontendOpenOrders"])
        self.assertEqual(self.a_pass(), ["clearinghouseState"])
        self.assertEqual(self.a_pass(), ["clearinghouseState"])

    def test_positions_that_changed_have_the_orders_read_again(self):
        self.a_pass()
        self.chain.sizes[CHALLENGE_A.lower()] = [("BTC", "0.010")]
        self.assertEqual(self.a_pass(), ["clearinghouseState", "frontendOpenOrders"])

    def test_orders_a_minute_old_are_read_again(self):
        self.a_pass()
        keeper.time.time.return_value = NOON + keeper.ORDERS_MAX_AGE_S - 1
        self.assertEqual(self.a_pass(), ["clearinghouseState"])
        keeper.time.time.return_value = NOON + keeper.ORDERS_MAX_AGE_S
        self.assertEqual(self.a_pass(), ["clearinghouseState", "frontendOpenOrders"])

    def test_a_stop_names_orders_read_now_never_a_kept_list(self):
        book = keeper.read_book(CHALLENGE_A)
        kept = keeper.read_book(CHALLENGE_A, reuse_orders=True)
        self.assertIsInstance(kept[1], keeper.Cached)
        self.assertNotIsInstance(book[1], keeper.Cached)
        self.chain.reads.clear()
        keeper.stop_args(CHALLENGE_A, {}, kept)
        self.assertIn("frontendOpenOrders", self.chain.reads, "a kept list is read again before it is named")
        self.chain.reads.clear()
        keeper.stop_args(CHALLENGE_A, {}, book)
        self.assertNotIn("frontendOpenOrders", self.chain.reads, "a list read this pass is used as it is")

    def test_a_breach_found_in_a_pass_that_kept_the_list_cancels_from_a_fresh_one(self):
        self.a_pass()
        self.a_pass()  # the list is kept now
        self.chain.challenges[CHALLENGE_A.lower()]["violation"] = 1
        reads = self.a_pass()
        self.assertIn("frontendOpenOrders", reads)
        self.assertEqual([fn for fn, _ in self.chain.calls_to(CHALLENGE_A)], ["breach"])

    def test_every_pass_writes_what_the_keeper_spent_and_the_accounts_holding_a_position(self):
        self.a_pass()
        said = [f for event, f in self.logged if event == "weight_per_min"]
        self.assertEqual(len(said), 1)
        self.assertEqual(said[0]["accounts_with_position"], 1)
        self.assertEqual(said[0]["by_type"], {"clearinghouseState": 2, "frontendOpenOrders": 20, "meta": 20})
        self.assertEqual(said[0]["total"], 42)
        self.a_pass()
        self.assertEqual([f["by_type"]["frontendOpenOrders"] for e, f in self.logged if e == "weight_per_min"], [20, 20],
                         "the second pass read no orders: the minute still holds the first's 20")


if __name__ == "__main__":
    unittest.main()
