"""Each of a pool's rules against the gateway as it is deployed: what the gateway refuses before it
signs, what it leaves to the stop and the take on the exchange, and what it leaves to the contract
and the keeper alone. `gateway/` here is the code the pools host runs (release 4900bcb1 since
7 October 2026, the same files as this commit's). docs/GATEWAY.md, "The rules, layer by layer",
is the table these tests hold.

Leverage is the one rule with no layer before the fact: the gateway never reads it, and the stop
sits on the drawdown and daily floors. When a leverage check before signing reaches the host
(PR #141 at the time of writing), the two tests that say so here are the ones to turn round, and
the table with them.
"""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest import mock

from eth_account import Account
from hyperliquid.utils.signing import recover_agent_or_user_from_l1_action

from gateway import hl
from gateway.demo_signer import DemoSigner
from gateway.protect import LONG, Market, RuleLimits, lines
from gateway.server import Gateway
from gateway.tests.test_gateway import ACCOUNT, NOW, TESTNET_CAPS
from gateway.tests.test_protect import MARKETS, Exchange, FlowBase, Reader, challenge

BTC = 3


class LeverageHasNoLayerBeforeTheFact(FlowBase):
    """The record of deployment 2 (docs/EVIDENCE.md, "Deployment 2, and the first run where the
    trader was not us"): 0.00297 BTC at 84076 on a challenge of 70 USDC, 249.71 of notional,
    3.57x against a 3x rule. The gateway signed it, and the contract stopped the challenge for
    leverage afterwards."""

    def setUp(self):
        super().setUp()
        self.markets = dict(MARKETS)
        self.markets[BTC] = Market("BTC", Decimal("84076"), 5)
        self.reader = Reader(self.key.address, self.trader.address, challenge(capital=Decimal(70)))
        self.x = Exchange(equity="70", markets=self.markets)
        self.gw = Gateway(self.reader, DemoSigner([self.key], mid=lambda a: self.markets[a].mark, caps=TESTNET_CAPS),
                          submit=self.x.submit, clock=lambda: self.now, venue=self.x)

    def test_the_gateway_reads_no_leverage_rule(self):
        # What the gateway knows of an account's rules: the floors and the target. A leverage rule
        # has nowhere to go, so nothing downstream of this can act on it.
        self.assertEqual(list(RuleLimits.__dataclass_fields__),
                         ["challenge", "daily_loss_bps", "max_drawdown_bps", "drawdown_base", "day",
                          "day_start_equity", "target_bps"])

    def test_an_order_past_the_leverage_rule_is_signed_and_sent(self):
        status, out = self.send(limitPx="84076", size="0.00297")
        self.assertEqual((status, out["status"]), (200, "submitted"))
        self.assertGreater(Decimal("0.00297") * 84076 / 70, 3)  # 3.57x against a 3x rule
        # The stop and the take went first, and the stop is on the floors' line: the same line the
        # account would get under any leverage rule at all.
        protective, order = self.x.sent
        self.assertEqual(protective["grouping"], "positionTpsl")
        self.assertEqual(order["orders"][0]["s"], "0.00297")
        line = lines(challenge(capital=Decimal(70)), Decimal(70), {BTC: {LONG: Decimal("0.00297")}}, self.markets)
        self.assertEqual(out["protection"][0]["stop"], str(line[(BTC, LONG)].stop))

    def test_an_account_far_past_any_leverage_still_opens_while_it_is_above_its_floor(self):
        # 0.02 BTC held on 70 of equity is 24x; above the floors, one more order still goes.
        self.x.positions = {BTC: Decimal("0.02")}
        status, out = self.send(limitPx="84076", size="0.001")
        self.assertEqual((status, out["status"]), (200, "submitted"))


class TheVenueIsHyperliquidsTestnet(unittest.TestCase):
    """The one venue the deployed code knows: Hyperliquid's testnet, for submitting and for reading."""

    def test_orders_are_submitted_to_the_testnet_exchange_and_nowhere_else(self):
        sent = []

        class Answer:
            status_code = 200

            def json(self):
                return {"status": "ok"}

        with mock.patch.object(hl.requests, "post", lambda url, **kw: sent.append(url) or Answer()):
            hl.submit({"type": "noop"}, NOW, {"r": "0x0", "s": "0x0", "v": 27})
        self.assertEqual(sent, ["https://api.hyperliquid-testnet.xyz/exchange"])
        self.assertEqual(hl.TESTNET_INFO_URL, "https://api.hyperliquid-testnet.xyz/info")
        urls = [v for v in vars(hl).values() if isinstance(v, str) and v.startswith("https://")]
        self.assertTrue(urls and all("hyperliquid-testnet.xyz" in u for u in urls), urls)

    def test_a_signature_is_good_on_testnet_only(self):
        # Hyperliquid's phantom agent carries the network: source "b" on testnet, "a" on mainnet.
        # A signature the gateway's signer makes recovers to its key on testnet and to some other
        # address on mainnet, so the same action can't be replayed there.
        key = Account.create()
        action = {"type": "order", "orders": [{"a": BTC, "b": True, "p": "60000", "s": "0.001", "r": False,
                                                "t": {"limit": {"tif": "Gtc"}}}], "grouping": "na"}
        signed = DemoSigner([key], mid=lambda a: Decimal(60000), caps=TESTNET_CAPS).sign(key.address, "order", action, NOW)
        sig = signed.signature
        self.assertEqual(hl.recover_signer(action, sig, NOW).lower(), key.address.lower())
        on_mainnet = recover_agent_or_user_from_l1_action(action, sig, None, NOW, None, True)
        self.assertNotEqual(on_mainnet.lower(), key.address.lower())


if __name__ == "__main__":
    unittest.main()
