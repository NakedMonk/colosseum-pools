"""The gateway's network. COLOSSEUM_NETWORK picks it (gateway/__init__.py, spike/hlspike/network.py):
unset or empty, the gateway does what it always did on testnet; `mainnet` moves every place it talks
to a network to Hyperliquid's mainnet; any other value stops the start. The demo signer and the
faucet stay on testnet. Offline: nothing is sent.

    spike/.venv/bin/python -m unittest gateway.tests.test_network

The network is read once, when the gateway is imported, so each network is looked at in a Python
of its own, started with the variable set as a host would set it.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import unittest

from gateway import server
from spike.hlspike import network

ROOT = pathlib.Path(__file__).resolve().parents[2]

# What the gateway does on a network, read in a Python of its own: the chain it starts on and the
# node it reads by default, the domain a trader signs under, whom it takes a signature's signer to
# be, the action name it asks the Signer for, where it submits and reads, and what /v1/health says.
PROBE = r'''
import http.client, json, threading
from http.server import ThreadingHTTPServer
from unittest import mock

from eth_account import Account
from hyperliquid.utils.signing import sign_l1_action

from gateway import NETWORK, auth, chain, hl, server, signer

key = Account.create()
action = {"type": "cancel", "cancels": [{"a": 3, "o": 1}]}
out = {"network": NETWORK.name, "chain_id": chain.CHAIN_ID, "rpc": server.DEFAULT_RPC,
       "domain_chain_id": auth.DOMAIN["chainId"]}

# An action signed for each network (source b on testnet, a on mainnet): does it recover to the key?
out["recovers_to_the_key"] = {
    net: hl.recover_signer(action, sign_l1_action(key, action, None, 7, None, net == "mainnet"), 7) == key.address
    for net in ("testnet", "mainnet")}

with mock.patch("requests.post") as post:
    post.return_value.json.return_value = {}
    signer.SignerClient("http://signer.invalid", {key.address: "token"}).sign(key.address, "cancel", action, 7)
    out["signer_exchange"] = json.loads(post.call_args.kwargs["data"])["exchange"]
    post.reset_mock()
    hl.submit(action, 7, {"r": "0x1", "s": "0x1", "v": 27})
    hl.mids()
    out["posted_to"] = [c.args[0] for c in post.call_args_list]

start = {}
for reported in ("0x3e6", "0x3e7"):
    reader = chain.JsonRpcReader("http://rpc.invalid", "0x" + "f1" * 20, "0x" + "f2" * 20)
    reader._rpc = lambda method, params, reported=reported: reported
    try:
        reader.check_chain()
        start[reported] = "started"
    except SystemExit as exc:
        start[reported] = str(exc)
out["start"] = start

httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(None))
threading.Thread(target=httpd.serve_forever, daemon=True).start()
conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
conn.request("GET", "/v1/health")
out["health_chain_id"] = json.loads(conn.getresponse().read())["chain_id"]
httpd.shutdown()

print("PROBE " + json.dumps(out))
'''

# The values the gateway had before COLOSSEUM_NETWORK, written out rather than read from the module.
TESTNET_AS_BEFORE = {
    "network": "testnet",
    "chain_id": 998,
    "rpc": "https://rpc.hyperliquid-testnet.xyz/evm",
    "domain_chain_id": 998,
    "recovers_to_the_key": {"testnet": True, "mainnet": False},
    "signer_exchange": "hyperliquid_testnet",
    "posted_to": ["https://api.hyperliquid-testnet.xyz/exchange", "https://api.hyperliquid-testnet.xyz/info"],
    "start": {"0x3e6": "started", "0x3e7": "refusing to start: RPC chain id is 999, expected 998"},
    "health_chain_id": 998,
}

MAINNET = {
    "network": "mainnet",
    "chain_id": 999,
    "rpc": "https://rpc.hyperliquid.xyz/evm",
    "domain_chain_id": 999,
    "recovers_to_the_key": {"testnet": False, "mainnet": True},
    "signer_exchange": "hyperliquid_main",
    "posted_to": ["https://api.hyperliquid.xyz/exchange", "https://api.hyperliquid.xyz/info"],
    "start": {"0x3e6": "refusing to start: RPC chain id is 998, expected 999", "0x3e7": "started"},
    "health_chain_id": 999,
}


def python(args: list[str], **env: str) -> subprocess.CompletedProcess:
    """A Python started the way the host starts the gateway, with none of this shell's settings for
    it: only the variables given here."""
    base = {k: v for k, v in os.environ.items() if not k.startswith(("GATEWAY_", "SIGNER_", "COLOSSEUM_"))}
    return subprocess.run([sys.executable, *args], cwd=ROOT, env={**base, **env}, capture_output=True,
                          text=True, timeout=120)


def probe(**env: str) -> dict:
    run = python(["-c", PROBE], **env)
    lines = [line for line in run.stdout.splitlines() if line.startswith("PROBE ")]
    if run.returncode != 0 or len(lines) != 1:
        raise AssertionError(f"the probe failed ({run.returncode}):\nstdout: {run.stdout[-2000:]}\n"
                             f"stderr: {run.stderr[-2000:]}")
    return json.loads(lines[0].removeprefix("PROBE "))


def last_line(run: subprocess.CompletedProcess) -> str:
    return run.stderr.strip().splitlines()[-1] if run.stderr.strip() else ""


class TheNetwork(unittest.TestCase):
    def test_unset_or_empty_the_gateway_is_on_testnet_as_before(self):
        self.assertEqual(probe(), TESTNET_AS_BEFORE)
        self.assertEqual(probe(**{network.ENV: ""}), TESTNET_AS_BEFORE)

    def test_set_to_mainnet_every_part_talks_to_mainnet(self):
        """Chain 999 for the start, /v1/health and the trader's domain; mainnet's node and API; the
        phantom agent's source a when a signature is checked; `hyperliquid_main` from the Signer."""
        self.assertEqual(probe(**{network.ENV: "mainnet"}), MAINNET)

    def test_any_other_value_stops_the_start_before_the_settings_are_read(self):
        # No GATEWAY_FACTORY either: a gateway that went on would stop on that instead.
        for value in ("prod", "Mainnet", " testnet"):
            run = python(["-m", "gateway.server"], **{network.ENV: value})
            self.assertEqual((run.returncode, last_line(run)),
                             (1, f"refusing to run: COLOSSEUM_NETWORK={value!r}, expected one of: testnet, mainnet"),
                             run.stderr[-2000:])


class TestnetsOwnParts(unittest.TestCase):
    """The demo signer signs with testnet keys made for the demo, and the faucet hands out the demo's
    dUSDC by a testnet deployment's record. Neither starts on another network."""

    def test_the_demo_signer_and_the_faucet_are_for_testnet_only(self):
        demo, faucet = {"GATEWAY_SIGNER": "demo"}, {"GATEWAY_SIGNER": "signer", "GATEWAY_FAUCET_KEY_FILE": "/k"}
        for env in (demo, faucet, {**demo, **faucet, "GATEWAY_SIGNER": "demo"}):
            self.assertIsNone(server.refuse_testnet_parts(env, network.TESTNET))
        with self.assertRaises(SystemExit) as e:
            server.refuse_testnet_parts(demo, network.MAINNET)
        self.assertEqual(str(e.exception),
                         "refusing to start: the demo signer is for testnet only, and COLOSSEUM_NETWORK is mainnet")
        with self.assertRaises(SystemExit) as e:
            server.refuse_testnet_parts(faucet, network.MAINNET)
        self.assertEqual(str(e.exception), "refusing to start: the faucet is for testnet only, and COLOSSEUM_NETWORK "
                                           "is mainnet; unset GATEWAY_FAUCET_KEY_FILE")
        for env in ({"GATEWAY_SIGNER": "signer"}, {"GATEWAY_SIGNER": "signer", "GATEWAY_FAUCET_KEY_FILE": ""}):
            self.assertIsNone(server.refuse_testnet_parts(env, network.MAINNET))

    def test_the_start_refuses_them_first(self):
        run = python(["-m", "gateway.server"], COLOSSEUM_NETWORK="mainnet", GATEWAY_SIGNER="demo")
        self.assertEqual((run.returncode, last_line(run)),
                         (1, "refusing to start: the demo signer is for testnet only, and COLOSSEUM_NETWORK is mainnet"),
                         run.stderr[-2000:])
        run = python(["-m", "gateway.server"], COLOSSEUM_NETWORK="mainnet", GATEWAY_SIGNER="signer",
                     GATEWAY_FAUCET_KEY_FILE="/nonexistent/faucet.key")
        self.assertEqual((run.returncode, last_line(run)),
                         (1, "refusing to start: the faucet is for testnet only, and COLOSSEUM_NETWORK is mainnet; "
                             "unset GATEWAY_FAUCET_KEY_FILE"),
                         run.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
