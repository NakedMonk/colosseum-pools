"""The keeper's share of the host's minute of Hyperliquid's weight: an active account's positions every
pass; its open orders every pass while it holds a position, and while it holds none only when the
positions changed or the list is a minute old; the orders a stop names always read afresh; and what the
keeper spent written to its log after every pass. Offline: the fakes of test_keeper.py and
test_keeper_protect.py, with the keeper's own read_book and its own kept lists.

    spike/.venv/bin/python -m unittest ops.tests.test_keeper_weight
"""

from __future__ import annotations

import unittest
from unittest import mock

from gateway import weight
from ops import keeper
from ops.tests.test_keeper import CHALLENGE_A, NOON, POOL_A, KeeperTest, challenge_log
from ops.tests.test_keeper_protect import Chain, stop_order

# An order resting that may open a position: what an account holding none has on the book.
OPENING = {"coin": "BTC", "side": "B", "sz": "0.005", "isTrigger": False, "reduceOnly": False,
           "isPositionTpsl": False, "orderType": "Limit", "oid": 2, "triggerPx": "0.0"}


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
        # No position, an order resting: the account whose orders list may be kept.
        self.flat()
        self.keeper = self.make()

    def flat(self):
        self.chain.sizes[CHALLENGE_A.lower()] = []
        self.chain.orders[CHALLENGE_A.lower()] = [OPENING]

    def holding(self):
        self.chain.sizes[CHALLENGE_A.lower()] = [("BTC", "0.005")]
        self.chain.orders[CHALLENGE_A.lower()] = [stop_order("BTC", "A")]

    def a_pass(self) -> list[str]:
        self.chain.reads.clear()
        self.keeper.one_pass()
        return [r for r in self.chain.reads if r != "meta"]

    def test_with_no_position_and_nothing_changed_an_account_costs_its_positions_alone(self):
        self.assertEqual(self.a_pass(), ["clearinghouseState", "frontendOpenOrders"])
        self.assertEqual(self.a_pass(), ["clearinghouseState"])
        self.assertEqual(self.a_pass(), ["clearinghouseState"])

    def test_an_account_holding_a_position_has_its_orders_read_every_pass(self):
        self.holding()
        for _ in range(3):
            self.assertEqual(self.a_pass(), ["clearinghouseState", "frontendOpenOrders"])

    def test_a_stop_gone_with_the_position_as_it_was_is_missing_at_the_next_pass(self):
        # A crash: the stop-market fired, nothing filled it, and the exchange took it off. The position
        # is the same size it was; only the orders read now say there is no stop.
        self.holding()
        self.a_pass()
        self.a_pass()
        self.assertEqual([f for e, f in self.logged if e == "protection_missing"], [])
        self.chain.orders[CHALLENGE_A.lower()] = []
        self.assertIn("frontendOpenOrders", self.a_pass())
        self.assertEqual([f["coin"] for e, f in self.logged if e == "protection_missing"], ["BTC"])

    def test_positions_that_changed_have_the_orders_read_again(self):
        self.a_pass()
        self.holding()  # the order that rested filled, and its stop stands
        self.assertEqual(self.a_pass(), ["clearinghouseState", "frontendOpenOrders"])
        self.flat()  # the stop fired: no position again, and the list read with one is not kept
        self.assertEqual(self.a_pass(), ["clearinghouseState", "frontendOpenOrders"])
        self.assertEqual(self.a_pass(), ["clearinghouseState"])

    def test_orders_a_minute_old_are_read_again(self):
        # Their age on the monotonic clock: a wall clock set back or forward does not age them.
        with mock.patch.object(keeper.time, "monotonic", return_value=5000.0) as clock:
            self.a_pass()
            clock.return_value = 5000.0 + keeper.ORDERS_MAX_AGE_S - 1
            keeper.time.time.return_value = NOON + 3600  # the wall clock jumped: nothing to the orders' age
            self.assertEqual(self.a_pass(), ["clearinghouseState"])
            clock.return_value = 5000.0 + keeper.ORDERS_MAX_AGE_S
            self.assertEqual(self.a_pass(), ["clearinghouseState", "frontendOpenOrders"])

    def test_an_orders_read_that_failed_drops_the_list_kept_before_it(self):
        self.a_pass()  # a list kept: no position, an order resting
        self.assertEqual(self.a_pass(), ["clearinghouseState"])
        for failed in ("refused", "not a list"):
            if failed == "refused":
                self.chain.front_fails = True
            else:
                self.chain.orders[CHALLENGE_A.lower()] = {"error": "busy"}
            self.assertIsNone(keeper.venue_book(CHALLENGE_A), failed)  # a read that must read them
            self.chain.front_fails = False
            self.flat()
            self.assertEqual(self.a_pass(), ["clearinghouseState", "frontendOpenOrders"], failed)
            self.assertEqual(self.a_pass(), ["clearinghouseState"])

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
        self.assertEqual(said[0]["accounts_with_position"], 0)
        self.assertEqual(said[0]["by_type"], {"clearinghouseState": 2, "frontendOpenOrders": 20, "meta": 20})
        self.assertEqual(said[0]["total"], 42)
        self.a_pass()
        self.holding()
        self.a_pass()
        self.a_pass()
        said = [f for e, f in self.logged if e == "weight_per_min"]
        self.assertEqual([f["by_type"]["frontendOpenOrders"] for f in said], [20, 20, 40, 60],
                         "the second pass read no orders; with a position each pass reads them")
        self.assertEqual([f["accounts_with_position"] for f in said], [0, 0, 1, 1])


if __name__ == "__main__":
    unittest.main()
