"""One seat of a shared pool through its funded stage, with both keepers taking their turns. Offline.

    spike/.venv/bin/python -m unittest ops.tests.test_seat_funded_path

Every call on this path has a test of its own (test_keeper.py, test_shared_keeper.py). The path itself
needs both keepers, and each acts on a stage the other one's call wrote: the core keeper settles the
passed challenge and opens the funded stage, the shared pool's keeper notes the stage and ends it once
its published term has run, the core keeper settles the stage, and the shared pool's keeper arms the
seat for its next sale. No seat of a shared pool has had a funded stage on testnet yet, so this is
where that hand-off is checked. The fake below moves a stage only when the call that moves it is sent,
and fails on a call the contract would refuse, so a keeper that skips its turn leaves the seat where
it was and the test names the round it stopped in.
"""

from __future__ import annotations

from unittest import mock

from ops import keeper
from ops import shared_keeper as sk
from ops.tests.test_keeper import DAY, NOON, ZERO, FakeChain, KeeperTest, challenge_log

SHARED = "0x00000000000000000000000000000000000000A0"
SEAT = "0x00000000000000000000000000000000000000A1"
CHALLENGE = "0x00000000000000000000000000000000000000A2"
FUNDED_KEY = "0x00000000000000000000000000000000000000D5"
SHARED_KEEPER = "0x00000000000000000000000000000000000000E5"
# A seat of size 10, published with a one-day funded term, as on the shared-demo2c deployment: its
# HyperCore balance must cover 10 of challenge capital, 100 funded and 1 for the challenge's account.
TERM = DAY
NEED = 111 * 10**8
# What the seat holds after its funded stage settles: under NEED, so it has to be armed again.
RETURNED = 110_600_000 * 10**2


class SeatWorld(FakeChain):
    """test_keeper's chain with a shared pool on it. The seat is one of the factory's pools, as on the
    chain; the shared pool's own views answer from `shared`; a sent call moves the stage the way the
    contracts do. A call the contract would revert fails to send, as on the chain, and lands in
    `refused`: both keepers log a failed send and go on, so the test reads the list after each round."""

    USDC_TOKEN = 0

    def __init__(self):
        super().__init__()
        self.shared = {"seats": [SEAT], "queuedShares": 0, "minDeposit": 20 * 10**8, "openTickets": [],
                       "closedTickets": [], "fundedSince": {}, "fundedKey": {}, "fundedTerm": {SEAT.lower(): TERM}}
        self.spots = {SEAT.lower(): 0}
        self.refused: list[str] = []

    def call_view(self, to, signature, types, args, out):
        name = signature.split("(")[0]
        if to.lower() != SHARED.lower():
            return super().call_view(to, signature, types, args, out)
        s = self.shared
        if name == "openTicketCount":
            return (len(s["openTickets"]),)
        if name == "openTickets":
            start, count = args
            return (s["openTickets"][start:start + count],)
        if name in ("fundedSince", "fundedKey", "fundedTerm"):
            return (s[name].get(args[0].lower(), ZERO if name == "fundedKey" else 0),)
        return (list(s[name]) if isinstance(s[name], list) else s[name],)

    def core_spot_balance(self, user, token):
        return {"total": self.spots.get(user.lower(), 0), "hold": 0, "entryNtl": 0}

    def encode_call(self, signature, types=(), args=()):
        return signature.encode()

    def rpc(self, method, params=()):
        if method == "eth_estimateGas":
            # Every estimate passes: whether a call is due is the keepers' judgement under test, and
            # the contract's own guards are checked where the call lands, in transact.
            return hex(150_000)
        return super().rpc(method, params)

    def transact(self, wallet, to, signature, types, args):
        name, at = signature.split("(")[0], to.lower()
        seat = self.pools[SEAT.lower()]
        now = int(keeper.time.time())

        def refuse(why):
            self.refused.append(f"{name} at NOON+{now - NOON}s: {why}")
            raise RuntimeError(f"execution reverted: {why}")

        if at == CHALLENGE.lower() and name == "settle":
            if self.challenges[at]["status"] not in keeper.STOPPED:
                refuse("BadStatus")
        elif at == SEAT.lower() and name == "openFundedStage":
            if seat["stage"] != keeper.PASSED_AWAITING_KEY:
                refuse("BadStage")
        elif at == SHARED.lower() and name == "endFundedTerm":
            since = self.shared["fundedSince"].get(SEAT.lower(), 0)
            if seat["stage"] != keeper.FUNDED or since == 0:
                refuse("NotFunded")
            if now < since + TERM:
                refuse(f"TermNotOver({since + TERM})")
        elif at == SEAT.lower() and name == "settleFunded":
            if seat["stage"] != keeper.CLOSING:
                refuse("BadStage")
        elif at == SHARED.lower() and name == "armSeat":
            if seat["stage"] != keeper.IDLE or seat["challenge"] != ZERO or self.spots[SEAT.lower()] >= NEED:
                refuse("SeatBusy or AlreadyArmed")
        receipt = super().transact(wallet, to, signature, types, args)
        if name == "settle" and at == CHALLENGE.lower():
            # A passed challenge is flat, and one step stands for all of its settling here: how many
            # steps it takes is test_keeper's business. Settled, it lets go of the seat.
            self.challenges[at]["status"] = keeper.SETTLED
            seat["challenge"] = ZERO
        elif name == "openFundedStage":
            seat.update(stage=keeper.FUNDED, agentKey=FUNDED_KEY)
        elif name == "noteFunded" and seat["stage"] == keeper.FUNDED:
            # SharedPool._note: the term runs from the block that first notes the stage under its key.
            if self.shared["fundedSince"].get(SEAT.lower(), 0) == 0 or self.shared["fundedKey"].get(SEAT.lower()) != seat["agentKey"]:
                self.shared["fundedSince"][SEAT.lower()] = now
                self.shared["fundedKey"][SEAT.lower()] = seat["agentKey"]
        elif name == "endFundedTerm":
            self.shared["fundedSince"][SEAT.lower()] = 0
            self.shared["fundedKey"][SEAT.lower()] = ZERO
            seat.update(stage=keeper.CLOSING, cutKey=seat["agentKey"], cutBlock=self.latest)
        elif name == "settleFunded":
            seat["stage"] = keeper.IDLE
            self.spots[SEAT.lower()] = RETURNED
        elif name == "armSeat":
            self.spots[SEAT.lower()] = NEED
        return receipt


class SeatFundedPath(KeeperTest):
    def setUp(self):
        super().setUp()
        self.chain = SeatWorld()
        for module in (keeper, sk):
            patcher = mock.patch.object(module, "c", self.chain)
            patcher.start()
            self.addCleanup(patcher.stop)
        keeper._settle_waits.clear()
        self.addCleanup(keeper._settle_waits.clear)
        self.shared_log: list[tuple[str, dict]] = []
        quiet = mock.patch.object(sk, "log", side_effect=lambda event, **f: self.shared_log.append((event, f)))
        quiet.start()
        self.addCleanup(quiet.stop)
        # The trader has just passed: the challenge is Passed and the seat waits for its funded stage.
        self.chain.add_pool(SEAT, stage=keeper.PASSED_AWAITING_KEY, challenge=CHALLENGE, capitalNeeded=NEED,
                            accountReady=True, agentKey=ZERO)
        self.chain.add_challenge(CHALLENGE, status=keeper.PASSED)
        self.chain.logs.append(challenge_log(10, SEAT, challenge=CHALLENGE))

    def run_rounds(self, rounds, shared_first=False):
        core, shared = self.make(), sk.SharedKeeper(SHARED, object(), SHARED_KEEPER, False)
        for t, expected in rounds:
            self.at(t)
            before = len(self.chain.sent)
            for k in ((shared, core) if shared_first else (core, shared)):
                k.one_pass()
            got = [(to, fn) for to, fn, _ in self.chain.sent[before:]]
            self.assertEqual(self.chain.refused, [], f"round at NOON+{t - NOON}s: a call the contract refuses")
            self.assertEqual(got, [(a.lower(), fn) for a, fn in expected], f"round at NOON+{t - NOON}s")
        return core

    def assert_back_on_sale(self, core):
        seat = self.chain.pools[SEAT.lower()]
        self.assertEqual((seat["stage"], seat["challenge"]), (keeper.IDLE, ZERO))
        self.assertEqual(self.chain.spots[SEAT.lower()], NEED, "armed again for its next sale")
        self.assertEqual(self.chain.shared["fundedSince"][SEAT.lower()], 0)
        self.assertEqual(self.chain.challenges[CHALLENGE.lower()]["status"], keeper.SETTLED)
        self.assertNotIn(keeper.to_checksum_address(SEAT), core.live, "an idle seat with no challenge is let go")

    def test_a_passed_seat_runs_its_term_and_is_armed_again(self):
        core = self.run_rounds([
            (NOON, [(CHALLENGE, "settle"), (SEAT, "openFundedStage"), (SHARED, "noteFunded")]),
            (NOON + 3600, []),
            (NOON + TERM - 30, []),
            (NOON + TERM, [(SHARED, "endFundedTerm")]),
            (NOON + TERM + 60, [(SEAT, "settleFunded"), (SHARED, "armSeat")]),
            (NOON + TERM + 120, []),
        ])
        self.assert_back_on_sale(core)
        ended = [args for to, fn, args in self.chain.sent if fn == "endFundedTerm"]
        self.assertEqual(len(ended), 1)
        self.assertEqual(ended[0][0].lower(), SEAT.lower(), "the seat it ends is named")
        self.assertEqual(len(ended[0][3]), 32, "a fresh salt picks the keyless address")
        self.assertEqual([f["seat"] for e, f in self.shared_log if e == "funded_term_over"], [SEAT])

    def test_a_stage_noted_a_round_late_runs_its_full_term_from_the_note(self):
        # The shared pool's keeper goes first in every round, so it finds the stage only after the core
        # keeper has opened it. SharedPool counts the term from the note, not from the opening.
        core = self.run_rounds([
            (NOON, [(CHALLENGE, "settle"), (SEAT, "openFundedStage")]),
            (NOON + 60, [(SHARED, "noteFunded")]),
            (NOON + TERM, []),
            (NOON + 60 + TERM, [(SHARED, "endFundedTerm"), (SEAT, "settleFunded")]),
            (NOON + 120 + TERM, [(SHARED, "armSeat")]),
        ], shared_first=True)
        self.assert_back_on_sale(core)
