"""The network switch: one place for everything that differs between Hyperliquid's testnet and mainnet.

    spike/.venv/bin/python -m unittest ops.tests.test_network

COLOSSEUM_NETWORK picks it (spike/hlspike/network.py). Unset, the tools read exactly the constants they read
before the switch existed; set to mainnet, the values the module's docstring cites; set to anything else,
they refuse to start.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

from hyperliquid.utils import constants
from hyperliquid.utils.signing import construct_phantom_agent

from spike.hlspike import common, network

ROOT = pathlib.Path(__file__).resolve().parents[2]
CLEAN = {k: v for k, v in os.environ.items() if k not in ("COLOSSEUM_NETWORK", "COLOSSEUM_RPC_URL")}


def common_reads(extra: dict) -> subprocess.CompletedProcess:
    code = "from spike.hlspike import common as c; print(c.CHAIN_ID, c.API_URL, c.RPC_URL, c.USDC_ERC20)"
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**CLEAN, **extra},
                          capture_output=True, text=True, timeout=60)


class TheSwitch(unittest.TestCase):
    def test_unset_or_empty_is_testnet_with_the_constants_used_before(self):
        for env in ({}, {network.ENV: ""}):
            self.assertIs(network.select(env), network.TESTNET, env)
        t = network.TESTNET
        self.assertEqual((t.chain_id, t.rpc_url, t.api_url, t.usdc_erc20),
                         (998, "https://rpc.hyperliquid-testnet.xyz/evm", constants.TESTNET_API_URL,
                          "0x2B3370eE501B4a559b57D449569354196457D8Ab"))
        self.assertEqual((t.signer_exchange, t.phantom_source, t.hyperliquid_chain, t.is_mainnet),
                         ("hyperliquid_testnet", "b", "Testnet", False))

    def test_mainnet_is_only_ever_chosen_by_name_and_carries_the_documented_values(self):
        m = network.select({network.ENV: "mainnet"})
        self.assertIs(m, network.MAINNET)
        self.assertEqual((m.chain_id, m.rpc_url, m.api_url, m.usdc_erc20),
                         (999, "https://rpc.hyperliquid.xyz/evm", constants.MAINNET_API_URL,
                          "0xb88339CB7199b77E23DB6E890353E22632Ba630f"))
        self.assertEqual((m.signer_exchange, m.phantom_source, m.hyperliquid_chain, m.is_mainnet),
                         ("hyperliquid_main", "a", "Mainnet", True))

    def test_any_other_value_is_refused_not_read_as_one_of_the_two(self):
        for value in ("Mainnet", "main", "prod", "testnet ", "998"):
            with self.assertRaises(SystemExit) as stop:
                network.select({network.ENV: value})
            self.assertIn(f"{network.ENV}={value!r}", str(stop.exception))

    def test_each_networks_source_is_the_one_the_sdk_signs_it_with(self):
        for n in network.NETWORKS.values():
            self.assertEqual(construct_phantom_agent(b"\x00" * 32, n.is_mainnet)["source"], n.phantom_source, n.name)


class WhatCommonReads(unittest.TestCase):
    def test_unset_it_reads_exactly_what_it_read_before(self):
        run = common_reads({})
        self.assertEqual(run.stdout.split(), ["998", "https://api.hyperliquid-testnet.xyz",
                                              "https://rpc.hyperliquid-testnet.xyz/evm",
                                              "0x2B3370eE501B4a559b57D449569354196457D8Ab"], run.stderr)

    def test_set_to_mainnet_it_reads_mainnet(self):
        run = common_reads({network.ENV: "mainnet"})
        self.assertEqual(run.stdout.split(), ["999", "https://api.hyperliquid.xyz", "https://rpc.hyperliquid.xyz/evm",
                                              "0xb88339CB7199b77E23DB6E890353E22632Ba630f"], run.stderr)

    def test_set_to_anything_else_it_does_not_start(self):
        run = common_reads({network.ENV: "prod"})
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(run.stdout, "")
        self.assertIn("refusing to run: COLOSSEUM_NETWORK='prod'", run.stderr)

    def test_an_api_url_that_is_not_the_sdks_for_its_network_is_refused(self):
        for net in (network.TESTNET, network.MAINNET):
            common.check_api(net)
            wrong = dataclasses.replace(net, api_url="https://api.example.test")
            with self.assertRaises(SystemExit) as stop:
                common.check_api(wrong)
            self.assertIn(f"is not Hyperliquid's {net.name} API", str(stop.exception))


class TheChainCheck(unittest.TestCase):
    """assert_network: the RPC against the network. assert_testnet: the same, for the tools that stay on testnet."""

    def on(self, net, rpc_chain):
        return mock.patch.multiple(common, NETWORK=net, CHAIN_ID=net.chain_id,
                                   rpc=mock.Mock(return_value=hex(rpc_chain)))

    def test_on_testnet_the_message_is_the_one_it_always_was(self):
        with self.on(network.TESTNET, 998):
            common.assert_network()
            common.assert_testnet()
        with self.on(network.TESTNET, 999), self.assertRaises(SystemExit) as stop:
            common.assert_network()
        self.assertEqual(str(stop.exception), "refusing to run: RPC chain id is 999, expected 998")

    def test_on_mainnet_the_rpc_has_to_be_mainnets(self):
        with self.on(network.MAINNET, 999):
            common.assert_network()
        with self.on(network.MAINNET, 998), self.assertRaises(SystemExit) as stop:
            common.assert_network()
        self.assertEqual(str(stop.exception), "refusing to run: RPC chain id is 998, expected 999")

    def test_sending_holds_the_rpc_to_the_network_not_to_testnet(self):
        # send_tx is under every transaction the keepers send; a testnet-only check there would stop them all on
        # mainnet whatever the network said.
        with mock.patch.object(common, "assert_network", side_effect=SystemExit("network checked")) as checked, \
                mock.patch.object(common, "assert_testnet") as testnet_only, self.assertRaises(SystemExit) as stop:
            common.send_tx(mock.Mock(address="0x" + "11" * 20), "0x" + "22" * 20)
        self.assertEqual(str(stop.exception), "network checked")
        checked.assert_called_once_with()
        testnet_only.assert_not_called()

    def test_a_testnet_only_tool_refuses_mainnet_even_with_a_mainnet_rpc(self):
        with self.on(network.MAINNET, 999), self.assertRaises(SystemExit) as stop:
            common.assert_testnet()
        self.assertIn("this tool is testnet only, and COLOSSEUM_NETWORK is mainnet", str(stop.exception))


if __name__ == "__main__":
    unittest.main()
