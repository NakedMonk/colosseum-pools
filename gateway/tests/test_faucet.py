"""The faucet for outside testers (gateway/faucet.py): what it sends, everything it refuses, and that a
refusal sends nothing."""

import json
import os
import pathlib
import tempfile
import threading
import unittest
from decimal import Decimal

import eth_abi
from eth_account import Account
from eth_utils import keccak, to_checksum_address

from gateway.chain import Throttled
from gateway.faucet import (HYPE_DEFAULT, OVER_DAILY, USDC_DEFAULT, Faucet, Ledger, NotSent, Refused, Wallet,
                            faucet_from_env, parse_address)

TOKEN = "0x00000000000000000000000000000000000000d5"
TESTER = to_checksum_address("0x7a3b9c0d1e2f405162738495a6b7c8d9e0f1a2b3")
OTHER = to_checksum_address("0x5c1e9a7b3d2f4e6a8b0c1d2e3f4a5b6c7d8e9f01")
NOW = 1_793_136_000  # 27 Oct 2026, 21:20 UTC
DAY = NOW // 86_400
GWEI = 10**9
# Two clients' networks, as nginx passes them; documentation addresses, and IPv6 as Cloudflare often sees one.
NET_A = "2001:db8::7"
NET_B = "2001:db8::1"


def selector(signature):
    return "0x" + keccak(text=signature)[:4].hex()


def word(n):
    return "0x" + eth_abi.encode(["uint256"], [n]).hex()


class Chain:
    """The node as the faucet sees it: the token's two numbers, the wallet's HYPE, the estimates and the
    sends. Every send is kept, so a test can say nothing went."""

    def __init__(self, faucet_address):
        self.faucet = faucet_address
        self.daily = 1_000 * 10**6
        self.minted = 0
        self.balance = 10**17  # 0.1 HYPE
        self.gas_price = GWEI // 10
        self.sends = []
        self.receipts = {}
        self.refuse_transfer = False
        self.transfer_estimate_error = None
        self.mint_estimate_error = None
        self.read_error = None
        self.send_error = {}  # the index of a send -> the error the node answers it with
        self.no_receipt = set()  # the index of a send whose receipt never comes
        self.reverts = set()  # the index of a send that is mined and reverts

    def __call__(self, method, params):
        if self.read_error is not None and method != "eth_sendRawTransaction":
            raise self.read_error
        if method == "eth_call":
            data = params[0]["data"]
            if data == selector("faucetDaily()"):
                return word(self.daily)
            if data.startswith(selector("faucetMinted(uint256)")):
                assert int(data[10:], 16) == DAY
                return word(self.minted)
            if data == selector("faucet()"):
                return "0x" + eth_abi.encode(["address"], [self.faucet]).hex()
            raise AssertionError(data)
        if method == "eth_getBalance":
            return hex(self.balance)
        if method == "eth_estimateGas":
            call = params[0]
            if "value" in call:
                if self.refuse_transfer:
                    raise RuntimeError("eth_estimateGas: {'code': 3, 'message': 'execution reverted'}")
                if self.transfer_estimate_error is not None:
                    raise self.transfer_estimate_error
                return hex(21_000)
            if self.mint_estimate_error is not None:
                raise self.mint_estimate_error
            return hex(98_267)
        if method == "eth_gasPrice":
            return hex(self.gas_price)
        if method == "eth_getTransactionCount":
            return hex(7)
        if method == "eth_sendRawTransaction":
            index = len(self.sends)
            sender = Account.recover_transaction(params[0])
            self.sends.append(params[0])
            if index in self.send_error:
                raise self.send_error[index]
            tx_hash = "0x" + keccak(bytes.fromhex(params[0][2:])).hex()
            if index not in self.no_receipt:
                self.receipts[tx_hash] = {"status": "0x0" if index in self.reverts else "0x1"}
            assert sender == self.faucet
            return tx_hash
        if method == "eth_getTransactionReceipt":
            return self.receipts.get(params[0])
        raise AssertionError(method)

    def decoded(self, index):
        from eth_account.typed_transactions import TypedTransaction
        from hexbytes import HexBytes
        return TypedTransaction.from_bytes(HexBytes(self.sends[index])).as_dict()


class FaucetTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.account = Account.create(os.urandom(32))
        self.chain = Chain(self.account.address)
        self.clock = lambda: NOW
        self.ledger = Ledger(self.dir.name, per_ip=3, clock=self.clock)
        self.wallet = Wallet(self.chain, self.account, sleep=lambda s: None, wait_s=3)
        self.faucet = Faucet(self.chain, self.account, TOKEN, self.ledger, Decimal(USDC_DEFAULT),
                             Decimal(HYPE_DEFAULT), wallet=self.wallet)

    def tearDown(self):
        self.dir.cleanup()

    def ask(self, address=TESTER, ip=NET_A):
        return self.faucet.grant({"address": address}, ip)

    def journal(self):
        path = pathlib.Path(self.dir.name) / "grants.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def assert_nothing_sent(self, answer, status, code):
        self.assertEqual((answer[0], answer[1]["code"], answer[1]["sent"]), (status, code, False))
        self.assertEqual(self.chain.sends, [])
        self.assertEqual(self.journal(), [])


class AGrant(FaucetTest):
    def test_it_sends_the_hype_first_then_the_exams_dusdc_and_journals_both(self):
        status, out = self.ask()
        self.assertEqual((status, out["status"], out["usdc"], out["hype"]), (200, "sent", "1.7", "0.001"))
        hype, mint = self.chain.decoded(0), self.chain.decoded(1)
        self.assertEqual((to_checksum_address(hype["to"]), hype["value"], hype["data"]), (TESTER, 10**15, b""))
        self.assertEqual(to_checksum_address(mint["to"]), to_checksum_address(TOKEN))
        self.assertEqual(mint["data"][:4], keccak(text="mint(address,uint256)")[:4])
        self.assertEqual(eth_abi.decode(["address", "uint256"], mint["data"][4:]), (TESTER.lower(), 1_700_000))
        # Consecutive nonces, the fee cap at twice the gas price, the estimates with a quarter on top.
        self.assertEqual((hype["nonce"], mint["nonce"]), (7, 8))
        self.assertEqual(hype["maxFeePerGas"], 2 * self.chain.gas_price)
        self.assertEqual((hype["gas"], mint["gas"]), (26_250, 122_833))
        [entry] = self.journal()
        self.assertEqual({k: entry[k] for k in ("address", "status", "usdc", "hype")},
                         {"address": TESTER, "status": "sent", "usdc": "1.7", "hype": "0.001"})
        self.assertEqual((entry["hype_tx"], entry["usdc_tx"]), (out["hype_tx"], out["usdc_tx"]))
        self.assertEqual(entry["t"], "2026-10-27T21:20:00Z")

    def test_a_mixed_case_address_must_be_its_checksum(self):
        self.assertEqual(self.ask(TESTER.lower())[0], 200)
        wrong = TESTER[:2] + TESTER[2:].swapcase()
        self.assertEqual(parse_address({"address": TESTER}), TESTER)
        with self.assertRaises(Exception) as caught:
            parse_address({"address": wrong})
        self.assertEqual(caught.exception.code, "bad_address")


class WhatItRefuses(FaucetTest):
    def test_no_address_a_bad_one_or_the_zero_address(self):
        for body in ({}, {"address": "0x123"}, {"address": 7}, {"address": "0x" + "00" * 20}, ["0x"]):
            self.assert_nothing_sent(self.faucet.grant(body, NET_A), 400, "bad_address")

    def test_an_address_once_a_day(self):
        self.assertEqual(self.ask()[0], 200)
        sent = len(self.chain.sends)
        status, out = self.ask(TESTER.lower(), ip=NET_B)
        self.assertEqual((status, out["code"], out["sent"]), (429, "address_served_today", False))
        self.assertEqual(out["retry_after_s"], 86_400 - NOW % 86_400)
        self.assertEqual(len(self.chain.sends), sent)

    def test_a_network_past_its_grants_for_the_day(self):
        for n in range(3):
            self.assertEqual(self.ask(to_checksum_address("0x" + str(n + 1) * 40))[0], 200)
        sent = len(self.chain.sends)
        status, out = self.ask(OTHER)
        self.assertEqual((status, out["code"], out["sent"]), (429, "ip_limit", False))
        self.assertEqual(len(self.chain.sends), sent)
        # Another network is not this one's limit.
        self.assertEqual(self.ask(OTHER, ip=NET_B)[0], 200)

    def test_a_day_the_token_no_longer_covers(self):
        self.chain.minted = self.chain.daily - 1_699_999
        self.assert_nothing_sent(self.ask(), 429, "faucet_daily_limit")

    def test_a_mint_the_token_would_refuse_over_its_day(self):
        self.chain.mint_estimate_error = RuntimeError(
            f"eth_estimateGas: {{'code': 3, 'message': 'execution reverted', 'data': '0x{OVER_DAILY}{'0' * 64}'}}")
        self.assert_nothing_sent(self.ask(), 429, "faucet_daily_limit")

    def test_a_faucet_without_the_hype_for_the_grant(self):
        self.chain.balance = 10**15 - 1
        self.assert_nothing_sent(self.ask(), 429, "faucet_empty")

    def test_a_faucet_with_the_grant_but_not_its_own_gas(self):
        # The grant and the two transactions at the fee cap: 10**15 + (26,250 + 122,833) * 0.2 gwei.
        self.chain.balance = 10**15 + 149_083 * 2 * self.chain.gas_price - 1
        self.assert_nothing_sent(self.ask(), 429, "faucet_empty")
        self.chain.balance += 1
        self.assertEqual(self.ask()[0], 200)

    def test_an_address_that_refuses_hype(self):
        self.chain.refuse_transfer = True
        self.assert_nothing_sent(self.ask(), 400, "cannot_take_hype")

    def test_a_node_failing_the_transfers_estimate_is_not_the_addresss_fault(self):
        self.chain.transfer_estimate_error = RuntimeError("eth_estimateGas: {'code': -32603, 'message': 'internal error'}")
        self.assert_nothing_sent(self.ask(), 424, "upstream_failed")
        self.chain.transfer_estimate_error = None
        self.assertEqual(self.ask()[0], 200)

    def test_a_node_that_throttles_or_fails_the_reads(self):
        self.chain.read_error = Throttled("eth_call: rate limited 5 times in a row")
        self.assert_nothing_sent(self.ask(), 429, "upstream_busy")
        self.chain.read_error = RuntimeError("eth_call: boom")
        self.assert_nothing_sent(self.ask(), 424, "upstream_failed")

    def test_a_grant_while_another_is_being_sent(self):
        self.faucet._lock.acquire()
        try:
            self.assert_nothing_sent(self.ask(), 429, "faucet_busy")
        finally:
            self.faucet._lock.release()

    def test_a_refusal_after_the_reservation_gives_the_grant_back(self):
        self.chain.balance = 0
        self.ask()
        self.ask()
        self.ask()
        self.chain.balance = 10**17
        # Three refusals spent none of the network's three grants, and not the address's either.
        self.assertEqual(self.ask()[0], 200)


class WhenASendFails(FaucetTest):
    def test_a_hype_transfer_the_node_refuses_sends_nothing_and_gives_the_grant_back(self):
        self.chain.send_error[0] = RuntimeError("eth_sendRawTransaction: {'message': 'insufficient funds'}")
        status, out = self.ask()
        self.assertEqual((status, out["code"], out["sent"]), (424, "upstream_failed", False))
        self.assertEqual(len(self.chain.sends), 1)  # handed over and refused: no mint follows
        self.assertEqual(self.journal(), [])
        self.chain.send_error.clear()
        self.assertEqual(self.ask()[0], 200)

    def test_a_reverted_hype_transfer_holds_the_dusdc_back(self):
        self.chain.reverts.add(0)
        status, out = self.ask()
        self.assertEqual((status, out["code"]), (424, "hype_reverted"))
        self.assertEqual(len(self.chain.sends), 1)

    def test_a_hype_transfer_with_no_receipt_holds_the_dusdc_back_and_spends_the_day(self):
        self.chain.no_receipt.add(0)
        status, out = self.ask()
        self.assertEqual((status, out["status"], out["usdc_tx"], out["usdc"]), (202, "pending", None, "0"))
        self.assertEqual(len(self.chain.sends), 1)
        self.assertEqual([e["status"] for e in self.journal()], ["pending"])
        self.assertEqual(self.ask()[1]["code"], "address_served_today")

    def test_a_mint_that_fails_after_the_hype_is_partial(self):
        self.chain.reverts.add(1)
        status, out = self.ask()
        self.assertEqual((status, out["status"], out["usdc"]), (424, "partial", "0"))
        self.assertEqual([e["status"] for e in self.journal()], ["partial"])
        self.assertEqual(self.ask()[1]["code"], "address_served_today")

    def test_a_mint_the_node_refuses_is_partial_too(self):
        self.chain.send_error[1] = RuntimeError("eth_sendRawTransaction: {'message': 'nonce too high'}")
        status, out = self.ask()
        self.assertEqual((status, out["status"], out["usdc_tx"]), (424, "partial", None))

    def test_a_send_already_known_is_waited_for_and_a_throttled_one_never_left(self):
        def known(method, params):
            if method == "eth_sendRawTransaction":
                raise RuntimeError("eth_sendRawTransaction: {'message': 'already known'}")
            return {"status": "0x1"}

        def throttled(method, params):
            raise Throttled("eth_sendRawTransaction: rate limited 5 times in a row")

        _, receipt = Wallet(known, self.account, sleep=lambda s: None, wait_s=2).send(TESTER, 1, b"", 21_000, 0, GWEI)
        self.assertEqual(receipt, {"status": "0x1"})
        with self.assertRaises(NotSent):
            Wallet(throttled, self.account, sleep=lambda s: None, wait_s=2).send(TESTER, 1, b"", 21_000, 0, GWEI)

    def test_a_send_whose_connection_broke_is_decided_by_its_receipt(self):
        def broke(method, params):
            if method == "eth_sendRawTransaction":
                raise ConnectionError("reset by peer")
            return None

        tx_hash, receipt = Wallet(broke, self.account, sleep=lambda s: None, wait_s=2).send(TESTER, 1, b"", 21_000, 0, GWEI)
        self.assertTrue(tx_hash.startswith("0x"))
        self.assertIsNone(receipt)


class TheDay(FaucetTest):
    def test_a_restart_remembers_the_days_grants_and_a_new_day_forgets_them(self):
        self.assertEqual(self.ask()[0], 200)
        again = Faucet(self.chain, self.account, TOKEN, Ledger(self.dir.name, per_ip=3, clock=self.clock),
                       Decimal("1.7"), Decimal("0.001"), wallet=self.wallet)
        self.assertEqual(again.grant({"address": TESTER}, NET_A)[1]["code"], "address_served_today")
        tomorrow = Ledger(self.dir.name, per_ip=3, clock=lambda: NOW + 86_400)
        self.assertEqual(tomorrow.reserve(TESTER, NET_A), DAY + 1)

    def test_a_grant_cut_short_after_its_first_send_stays_taken(self):
        """The grant is on disk before anything goes: a gateway killed between the two sends pays no one twice."""
        self.chain.send_error[0] = SystemExit("the service was stopped")
        with self.assertRaises(SystemExit):
            self.ask()
        self.assertEqual(len(self.chain.sends), 1)
        with self.assertRaises(Refused) as caught:
            Ledger(self.dir.name, per_ip=3, clock=self.clock).reserve(TESTER, NET_B)
        self.assertEqual(caught.exception.code, "address_served_today")

    def test_the_state_keeps_no_client_address(self):
        self.ask()
        state = (pathlib.Path(self.dir.name) / "state.json").read_text()
        self.assertNotIn(NET_A, state)
        self.assertNotIn(NET_A, (pathlib.Path(self.dir.name) / "grants.jsonl").read_text())

    def test_an_unreadable_state_stops_the_start(self):
        (pathlib.Path(self.dir.name) / "state.json").write_text("{not json")
        with self.assertRaises(SystemExit):
            Ledger(self.dir.name, per_ip=3)


class FromEnv(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = pathlib.Path(self.dir.name)
        self.account = Account.create(os.urandom(32))
        (self.root / "deployments").mkdir()
        self.record = {"usdc": TOKEN, "faucet": {"address": self.account.address, "daily": 10**9}}
        (self.root / "deployments" / "testnet-v3-demo3.json").write_text(json.dumps(self.record))
        self.key = self.root / "faucet.key"
        self.key.write_text(self.account.key.hex())
        os.chmod(self.key, 0o600)
        (self.root / "state").mkdir()
        self.env = {"GATEWAY_FAUCET_KEY_FILE": str(self.key), "COLOSSEUM_DEPLOYMENT": "v3-demo3",
                    "STATE_DIRECTORY": str(self.root / "state")}
        self.chain = Chain(self.account.address)

    def test_no_key_file_no_faucet(self):
        self.assertIsNone(faucet_from_env({}, self.chain, self.root))

    def test_the_faucet_the_record_and_the_token_agree_on(self):
        faucet = faucet_from_env(self.env, self.chain, self.root)
        self.assertEqual(faucet.summary(), {"usdc": "1.7", "hype": "0.001", "per_ip": 3})
        self.assertEqual(faucet.ledger.dir, self.root / "state")

    def test_what_stops_the_start(self):
        cases = {
            "a key the record does not name": lambda: self.record["faucet"].update(address=OTHER),
            "a record with no faucet": lambda: self.record.pop("faucet"),
            "a token that mints for another": lambda: setattr(self.chain, "faucet", OTHER),
            "no directory to write": lambda: self.env.pop("STATE_DIRECTORY"),
            "a key others can read": lambda: os.chmod(self.key, 0o644),
            "a grant of nothing": lambda: self.env.update(GATEWAY_FAUCET_HYPE="0"),
            "no grants for a network": lambda: self.env.update(GATEWAY_FAUCET_PER_IP="0"),
            "no key file": lambda: self.key.unlink(),
            "a key file with no key in it": lambda: self.key.write_text("not-a-key-0123"),
            "a record with no token": lambda: self.record.pop("usdc"),
            "a token that does not answer whom it lets mint":
                lambda: setattr(self.chain, "read_error", RuntimeError("eth_call: {'code': 3, 'message': 'execution reverted'}")),
        }
        for name, spoil in cases.items():
            with self.subTest(name):
                self.setUp()
                spoil()
                (self.root / "deployments" / "testnet-v3-demo3.json").write_text(json.dumps(self.record))
                with self.assertRaises(SystemExit) as stop:
                    faucet_from_env(self.env, self.chain, self.root)
                # Words for the service's log, not a traceback, and never the key file's text.
                self.assertIsInstance(stop.exception.code, str)
                self.assertNotIn("not-a-key-0123", stop.exception.code)
                self.assertNotIn(self.account.key.hex()[2:], stop.exception.code)


class OverHttp(unittest.TestCase):
    """The path on a real socket: nginx's X-Real-IP is the client, and a gateway without a faucet says so."""

    def serve(self, faucet):
        from http.server import ThreadingHTTPServer

        from gateway.server import make_handler

        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(object(), "", faucet=faucet))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def post(self, port, body, headers=None):
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/v1/faucet", body=body, headers={"Content-Type": "application/json", **(headers or {})})
        resp = conn.getresponse()
        out = resp.status, json.loads(resp.read() or b"null")
        conn.close()
        return out

    def test_the_client_is_the_address_nginx_passes(self):
        seen = []

        class Stub:
            def grant(self, body, ip):
                seen.append((body, ip))
                return 200, {"status": "sent"}

        port = self.serve(Stub())
        self.assertEqual(self.post(port, json.dumps({"address": TESTER}), {"X-Real-IP": NET_A})[0], 200)
        self.post(port, json.dumps({"address": TESTER}))
        self.assertEqual(seen, [({"address": TESTER}, NET_A), ({"address": TESTER}, "127.0.0.1")])
        self.assertEqual(self.post(port, "{nope")[0], 400)

    def test_a_gateway_without_a_faucet(self):
        status, out = self.post(self.serve(None), json.dumps({"address": TESTER}))
        self.assertEqual((status, out["code"]), (404, "no_faucet"))


if __name__ == "__main__":
    unittest.main()
