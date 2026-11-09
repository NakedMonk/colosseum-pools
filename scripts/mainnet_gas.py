#!/usr/bin/env python3
"""What deploying and running the contracts on Hyperliquid's mainnet costs in gas, from what was measured.

    python3 scripts/mainnet_gas.py [--record deployments/testnet-v3-demo3.json] [--out FILE]

Gas comes from the receipts of the deployment in the record, on testnet: the same EVM, so the same gas for the same
code. Price comes from mainnet now: eth_gasPrice, eth_bigBlockGasPrice and HYPE's mid. It only reads and needs no key.
"""
import argparse
import json
import pathlib
import sys
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from spike.hlspike import network  # noqa: E402

# The measured receipts are testnet's, the prices mainnet's: each from network.py, whichever network is selected.
TESTNET_RPC = network.TESTNET.rpc_url
MAINNET_RPC = network.MAINNET.rpc_url
MAINNET_INFO = network.MAINNET.api_url + "/info"

# On testnet only: mainnet has real USDC and no faucet for testers.
TESTNET_ONLY = {"DemoUsdc", "setFaucet"}
# A small block's gasLimit, read from mainnet on 2 Nov 2026. A transaction above it waits for a big block, and its
# wallet has to ask for those (evmUserModify usingBigBlocks).
SMALL_BLOCK_GAS = 3_000_000
# Measured on testnet receipts on 20 Oct 2026, on the deployment before v3. breach is from docs/HOSTING.md; settle is
# the keeper's own figure and an approximation. activate_dex_second_step is the second activate() of a start on a builder
# dex, from the receipt of 0xb35649d99ca41e5818a1653e7512ae03a77b1e4af6500a2f056df7f1e69863b8 (v3, demo3, xyz,
# 2 Nov 2026): its first call sends the capital to the dex, this one starts the challenge once the dex shows it.
OPERATIONS = {"checkpoint": 41_873, "activate": 168_528, "activate_dex_second_step": 149_244, "breach": 194_818,
              "settle_step": 115_000}


def rpc(url, method, params=()):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": list(params)}).encode()
    req = urllib.request.Request(url, body, {"Content-Type": "application/json", "User-Agent": "mainnet-gas"})
    with urllib.request.urlopen(req, timeout=20) as response:
        answer = json.load(response)
    if "error" in answer:
        raise RuntimeError(f"{method}: {answer['error']}")
    return answer["result"]


def gas_of(transactions, read_receipt):
    """The gas each named transaction used. One that failed says nothing about the code, so it stops the estimate."""
    gas_by_name = {}
    for name, tx in transactions.items():
        receipt = read_receipt(tx)
        if receipt is None:
            raise RuntimeError(f"{name}: the node has no receipt for the transaction {tx}")
        if int(receipt["status"], 16) != 1:
            raise RuntimeError(f"{name}: the transaction {tx} failed, and its gas says nothing")
        gas_by_name[name] = int(receipt["gasUsed"], 16)
    return gas_by_name


def cost(gas, price_wei, hype_usd):
    """Gas at a price: HYPE and dollars."""
    hype = gas * price_wei / 10**18
    return {"gas": gas, "hype": hype, "usd": hype * hype_usd}


def estimate(gas_by_name, gas_price_wei, big_block_price_wei, hype_usd):
    """The deployment on mainnet: each transaction at the price of the block it fits in, without what is testnet's."""
    kept = {name: gas for name, gas in gas_by_name.items() if name not in TESTNET_ONLY}
    big = {name: gas for name, gas in kept.items() if gas > SMALL_BLOCK_GAS}
    small_gas = sum(gas for name, gas in kept.items() if name not in big)
    big_gas = sum(big.values())
    wei = small_gas * gas_price_wei + big_gas * big_block_price_wei
    hype = wei / 10**18
    return {
        "gas_total": small_gas + big_gas,
        "hype": hype,
        "usd": hype * hype_usd,
        "transactions": len(kept),
        "need_big_block": sorted(big),
        "left_out_as_testnet_only": sorted(set(gas_by_name) - set(kept)),
    }


def read(record_path="deployments/testnet-v3-demo3.json"):
    """The estimate, read now: gas from the record's testnet receipts, prices from mainnet. Reads only."""
    record = json.loads(pathlib.Path(record_path).read_text())
    gas_by_name = gas_of(record["tx"], lambda tx: rpc(TESTNET_RPC, "eth_getTransactionReceipt", [tx]))
    gas_price = int(rpc(MAINNET_RPC, "eth_gasPrice"), 16)
    big_block_price = int(rpc(MAINNET_RPC, "eth_bigBlockGasPrice"), 16)
    request = urllib.request.Request(MAINNET_INFO, json.dumps({"type": "allMids"}).encode(),
                                     {"Content-Type": "application/json", "User-Agent": "mainnet-gas"})
    with urllib.request.urlopen(request, timeout=20) as response:
        hype_usd = float(json.load(response)["HYPE"])
    return {
        "read_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "record": str(record_path),
        "record_commit": record["commit"],
        "mainnet": {"gas_price_wei": gas_price, "big_block_gas_price_wei": big_block_price, "hype_usd": hype_usd},
        "deployment": estimate(gas_by_name, gas_price, big_block_price, hype_usd),
        "deployment_at_ten_times_the_price": estimate(gas_by_name, gas_price * 10, big_block_price * 10, hype_usd),
        "operations": {name: cost(gas, gas_price, hype_usd) for name, gas in OPERATIONS.items()},
        "gas_by_transaction": gas_by_name,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", default="deployments/testnet-v3-demo3.json")
    parser.add_argument("--out")
    args = parser.parse_args()
    text = json.dumps(read(args.record), indent=2) + "\n"
    if args.out:
        pathlib.Path(args.out).write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
