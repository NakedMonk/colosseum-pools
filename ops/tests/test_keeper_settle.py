"""Settle steps that cannot move: the keeper waits longer after each, and sends none into a book that
has nothing inside the contract's closing band (keeper.settle_due).

    spike/.venv/bin/python -m unittest ops.tests.test_keeper_settle -v
"""

from __future__ import annotations

import re
import unittest
from unittest import mock

from ops import keeper
from ops.tests.test_keeper import CHALLENGE_A, NOON, POOL_A, ROOT, FakeChain, KeeperTest, challenge_log

SHORT = ("BTC", "-0.001", "100")  # mark 100000: a close buys, and the band ends at 105000
LONG = ("BTC", "0.001", "100")    # a close sells, and the band ends at 95000


class Books(FakeChain):
    """FakeChain with sized positions and an order book for each coin."""

    def __init__(self):
        super().__init__()
        self.sizes: dict[str, list[tuple[str, str, str]]] = {}
        self.books: dict[str, tuple[list, list]] = {}
        self.book_reads = 0
        self.books_refused = False

    def info_post(self, body):
        user = str(body.get("user", "")).lower()
        if body["type"] == "clearinghouseState" and user in self.sizes:
            return {"assetPositions": [{"position": {"coin": coin, "szi": szi, "positionValue": value}}
                                       for coin, szi, value in self.sizes[user]]}
        if body["type"] == "l2Book":
            self.book_reads += 1
            if self.books_refused:
                raise RuntimeError("429 Too Many Requests")
            bids, asks = self.books.get(body["coin"], ([], []))
            return {"coin": body["coin"], "levels": [[{"px": px, "sz": sz, "n": 1} for px, sz in bids],
                                                    [{"px": px, "sz": sz, "n": 1} for px, sz in asks]]}
        return super().info_post(body)


class SettleTest(KeeperTest):
    def setUp(self):
        super().setUp()
        self.chain = Books()
        patcher = mock.patch.object(keeper, "c", self.chain)
        patcher.start()
        self.addCleanup(patcher.stop)
        keeper._settle_waits.clear()
        self.addCleanup(keeper._settle_waits.clear)

    def stopped_short(self, asks=(("100500", "1"),)):
        self.follow_a(status=keeper.BREACHED)
        self.chain.sizes[CHALLENGE_A.lower()] = [SHORT]
        self.chain.books["BTC"] = ([("99000", "1")], list(asks))

    def run_passes(self, k, start, seconds, step=30):
        """One pass every `step` seconds; the times at which a settle step went."""
        sent = []
        for t in range(start, start + seconds, step):
            self.at(t)
            before = len(self.chain.calls_to(CHALLENGE_A))
            k.one_pass()
            if len(self.chain.calls_to(CHALLENGE_A)) > before:
                sent.append(t)
        return sent


class TheWaitGrows(SettleTest):
    def test_a_step_that_moves_nothing_is_followed_by_a_wait_twice_as_long_up_to_an_hour(self):
        self.stopped_short()
        sent = self.run_passes(self.make(), NOON, 5 * 3600)
        gaps = [b - a for a, b in zip(sent, sent[1:])]
        self.assertEqual(gaps[:9], [30, 60, 120, 240, 480, 960, 1920, 3600, 3600])
        self.assertEqual(max(gaps), keeper.SETTLE_WAIT_MAX_S)
        self.assertEqual({fn for fn, _ in self.chain.calls_to(CHALLENGE_A)}, {"settle"})
        self.assertTrue(self.logged("settle_backoff"), "each longer wait is said, with the time of the next step")

    def test_the_wait_goes_back_to_one_pass_once_the_position_changes(self):
        self.stopped_short()
        k = self.make()
        sent = self.run_passes(k, NOON, 600)  # steps at 0, 30, 90, 210, 450 s: the next is due at 930 s
        self.assertEqual([t - NOON for t in sent], [0, 30, 90, 210, 450])
        self.chain.sizes[CHALLENGE_A.lower()] = [("BTC", "-0.0004", "40")]  # part of it closed
        sent = self.run_passes(k, NOON + 600, 120)
        self.assertEqual([t - NOON for t in sent], [600, 630, 690], "a moved position is stepped on at once, "
                         "the next pass too, and only a step that moves nothing starts the doubling again")
        self.assertEqual(self.logged("settle_resumed")[0]["reason"], "the position changed")

    def test_a_flat_stopped_account_is_settled_every_pass(self):
        # What a flat account's steps wait for is money landing on HyperCore, not a book.
        self.follow_a(status=keeper.BREACHED)
        sent = self.run_passes(self.make(), NOON, 150)
        self.assertEqual(len(sent), 5)
        self.assertEqual(self.chain.book_reads, 0)


class AnEmptyBook(SettleTest):
    def test_no_step_goes_into_a_book_with_nothing_inside_the_band_and_one_line_says_when_the_next_look_is(self):
        self.stopped_short(asks=(("106000", "1"),))  # above 105000: a reduce-only IOC there is rejected
        k = self.make()
        sent = self.run_passes(k, NOON, 2 * 3600)
        self.assertEqual(sent, [], "no settle step into a book that cannot fill it")
        held = self.logged("settle_held")
        self.assertEqual([h["wait_s"] for h in held], [60, 120, 240, 480, 960, 1920, 3600])
        self.assertEqual(held[0]["next_look_at"], NOON + 60)
        self.assertEqual(held[0]["levels"], [{"coin": "BTC", "close": "buy", "mark": "100000", "edge": "105000",
                                              "best": "106000", "fills": False}])
        self.assertIn("500 bps", held[0]["reason"])

    def test_a_level_inside_the_band_ends_the_wait_at_once(self):
        self.stopped_short(asks=())
        k = self.make()
        self.assertEqual(self.run_passes(k, NOON, 600), [])
        self.chain.books["BTC"] = ([("99000", "1")], [("104000", "0.5")])
        sent = self.run_passes(k, NOON + 600, 60)
        self.assertEqual(sent[0], NOON + 600, "the step goes on the first pass that sees the ask, mid-wait")
        self.assertEqual(self.logged("settle_resumed")[0]["reason"], "the book has a level inside the band")

    def test_a_long_is_closed_into_the_bids(self):
        self.follow_a(status=keeper.BREACHED)
        self.chain.sizes[CHALLENGE_A.lower()] = [LONG]
        self.chain.books["BTC"] = ([("94000", "1")], [("100100", "1")])  # the best bid is below 95000
        k = self.make()
        self.assertEqual(self.run_passes(k, NOON, 60), [])
        self.chain.books["BTC"] = ([("95500", "1")], [("100100", "1")])
        self.assertEqual(self.run_passes(k, NOON + 60, 30), [NOON + 60])

    def test_a_book_that_cannot_be_read_holds_nothing_back(self):
        self.stopped_short()
        self.chain.books_refused = True
        self.assertEqual(self.run_passes(self.make(), NOON, 30), [NOON])
        self.assertTrue(self.logged("close_book_read_failed"))

    def test_an_unread_book_does_not_end_a_wait_on_an_empty_one(self):
        self.stopped_short(asks=())
        k = self.make()
        self.run_passes(k, NOON, 30)  # held: nothing in the band, the next look at 60 s
        self.chain.books_refused = True
        self.assertEqual(self.run_passes(k, NOON + 30, 30), [])
        self.assertFalse(self.logged("settle_resumed"))

    def test_a_closing_pool_is_held_back_the_same_way(self):
        self.chain.add_pool(POOL_A, stage=keeper.CLOSING, challenge=keeper.ZERO)
        self.chain.logs.append(challenge_log(10, POOL_A))
        self.chain.sizes[POOL_A.lower()] = [SHORT]
        self.chain.books["BTC"] = ([], [("107000", "1")])
        k = self.make()
        self.at(NOON)
        k.one_pass()
        self.assertEqual(self.chain.calls_to(POOL_A), [])
        self.chain.books["BTC"] = ([], [("101000", "1")])
        self.at(NOON + 30)
        k.one_pass()
        self.assertEqual([fn for fn, _ in self.chain.calls_to(POOL_A)], ["settleFunded"])


class ASeatsFundedStage(SettleTest):
    """A seat of a shared pool is one of the factory's pools, and its funded stage ends in Closing like any
    pool's: the keeper settles it with settleFunded, held back exactly as a challenge's settle is."""

    def closing_seat(self, asks):
        self.chain.add_pool(POOL_A, stage=keeper.CLOSING, challenge=keeper.ZERO)
        self.chain.logs.append(challenge_log(10, POOL_A))
        self.chain.sizes[POOL_A.lower()] = [SHORT]
        self.chain.books["BTC"] = ([("99000", "1")], list(asks))

    def test_a_book_with_no_ask_at_all_holds_settle_funded_back_and_says_so(self):
        self.closing_seat(asks=())
        k = self.make()
        self.at(NOON)
        k.one_pass()
        self.assertEqual(self.chain.calls_to(POOL_A), [])
        held = self.logged("settle_held")
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0]["account"].lower(), POOL_A.lower())
        self.assertEqual(held[0]["levels"], [{"coin": "BTC", "close": "buy", "mark": "100000", "edge": "105000",
                                              "best": None, "fills": False}])
        self.assertEqual(held[0]["next_look_at"], NOON + 60)

    def test_a_level_inside_the_band_takes_one_step_and_a_flat_stage_is_never_held(self):
        self.closing_seat(asks=(("104000", "1"),))
        k = self.make()
        self.at(NOON)
        k.one_pass()
        self.assertEqual([fn for fn, _ in self.chain.calls_to(POOL_A)], ["settleFunded"])
        self.assertEqual(self.logged("settle_held"), [])
        # That step closed the position: the stage settles on at every pass, and no book is read for it.
        self.chain.sizes[POOL_A.lower()] = []
        reads = self.chain.book_reads
        for t in (NOON + 30, NOON + 60):
            self.at(t)
            k.one_pass()
        self.assertEqual([fn for fn, _ in self.chain.calls_to(POOL_A)], ["settleFunded"] * 3)
        self.assertEqual(self.chain.book_reads, reads)


class TheBandIsTheContracts(unittest.TestCase):
    def test_the_slippage_is_ruled_accounts(self):
        source = (ROOT / "src" / "RuledAccount.sol").read_text()
        bps = int(re.search(r"CLOSE_SLIPPAGE_BPS\s*=\s*(\d+)", source).group(1))
        self.assertEqual(keeper.CLOSE_SLIPPAGE_BPS, bps)


if __name__ == "__main__":
    unittest.main()
