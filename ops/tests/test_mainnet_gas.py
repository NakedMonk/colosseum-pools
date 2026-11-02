"""The mainnet gas estimate: what it counts, and at which price."""
import importlib.util
import pathlib
import unittest

SPEC = importlib.util.spec_from_file_location(
    "mainnet_gas", pathlib.Path(__file__).resolve().parents[2] / "scripts" / "mainnet_gas.py")
gas = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gas)


class TestEstimate(unittest.TestCase):
    def test_testnet_only_transactions_are_left_out_and_named(self):
        got = gas.estimate({"PoolImpl": 100, "DemoUsdc": 50, "setFaucet": 7}, 10**9, 10**9, 1.0)
        self.assertEqual(got["gas_total"], 100)
        self.assertEqual(got["transactions"], 1)
        self.assertEqual(got["left_out_as_testnet_only"], ["DemoUsdc", "setFaucet"])

    def test_each_transaction_pays_the_price_of_the_block_it_fits_in(self):
        over = gas.SMALL_BLOCK_GAS + 1
        got = gas.estimate({"small": gas.SMALL_BLOCK_GAS, "big": over}, 10**8, 3 * 10**8, 2.0)
        self.assertEqual(got["need_big_block"], ["big"])
        hype = (gas.SMALL_BLOCK_GAS * 10**8 + over * 3 * 10**8) / 10**18
        self.assertAlmostEqual(got["hype"], hype)
        self.assertAlmostEqual(got["usd"], hype * 2.0)

    def test_cost_is_gas_at_the_price(self):
        got = gas.cost(1_000_000, 10**8, 80.0)
        self.assertAlmostEqual(got["hype"], 0.0001)
        self.assertAlmostEqual(got["usd"], 0.008)

    def test_gas_is_read_from_the_receipts(self):
        receipts = {"0xa": {"status": "0x1", "gasUsed": "0x10"}, "0xb": {"status": "0x1", "gasUsed": "0x20"}}
        self.assertEqual(gas.gas_of({"one": "0xa", "two": "0xb"}, receipts.__getitem__), {"one": 16, "two": 32})

    def test_a_failed_transaction_stops_the_estimate(self):
        with self.assertRaisesRegex(RuntimeError, "one: the transaction 0xa failed"):
            gas.gas_of({"one": "0xa"}, lambda tx: {"status": "0x0", "gasUsed": "0x10"})

    def test_a_transaction_the_node_does_not_know_stops_the_estimate(self):
        with self.assertRaisesRegex(RuntimeError, "one: the node has no receipt for the transaction 0xa"):
            gas.gas_of({"one": "0xa"}, lambda tx: None)


if __name__ == "__main__":
    unittest.main()
