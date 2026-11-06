#!/usr/bin/env python3
"""What is left on the challenge accounts that have settled, read from the chain. It only reads and needs no key.

    spike/.venv/bin/python scripts/settled_accounts.py [--out FILE]

A settled account can still receive money: a late fill, a send of its own that landed after the settlement, a
donation. `ChallengeAccount.sweep()` sends whatever is there back to the pool, and anyone may call it. This reads
whether there is anything for it to send.

Every account a factory creates, a pool or a challenge, is a clone made with CREATE, so its address follows from the
factory's address and nonce. The accounts are found that way, not from the factory's logs: HyperEVM answers
eth_getLogs over 50 blocks at most and times old ranges out. For each challenge it reads, at one block:
- on HyperEVM: `status()`, `returnAt()`, the HYPE balance and the deployment's USDC balance;
- on HyperCore: the spot USDC balance, the perp account's value and withdrawable amount (the pool's dex too, on a
  builder dex), and every non-funding ledger update with its time.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time
import urllib.request

from eth_utils import keccak, to_checksum_address

RPC = "https://rpc.hyperliquid-testnet.xyz/evm"
INFO = "https://api.hyperliquid-testnet.xyz/info"
ROOT = pathlib.Path(__file__).resolve().parent.parent
# The factories whose challenges are read, and the shared pool whose seats are marked.
RECORDS = ("deployments/testnet-demo2.json", "deployments/testnet-v3-demo3.json")
SHARED = "deployments/testnet-shared-demo2b.json"
# ChallengeAccount.Status, in its order (src/ChallengeAccount.sol).
STATUS = ("None", "Created", "Active", "Breached", "Expired", "Forfeited", "Passed", "Aborted", "Settled")
# Past the factory's nonce: a clone made while this runs is read too, or found missing.
SPARE = 2


def post(url: str, body: dict, tries: int = 5):
    data = json.dumps(body).encode()
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data, {"Content-Type": "application/json",
                                                     "User-Agent": "settled-accounts"})
            with urllib.request.urlopen(req, timeout=20) as response:
                return json.load(response)
        except OSError:
            if attempt == tries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise AssertionError("unreachable")


def rpc(method: str, params: list):
    answer = post(RPC, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if "error" in answer:
        raise RuntimeError(f"{method}: {answer['error']}")
    return answer["result"]


def call(to: str, signature: str, args: tuple[str, ...] = (), block: str = "latest") -> str:
    data = "0x" + keccak(text=signature)[:4].hex() + "".join(a.lower().removeprefix("0x").rjust(64, "0") for a in args)
    return rpc("eth_call", [{"to": to, "data": data}, block])


def words(answer: str) -> list[str]:
    body = answer.removeprefix("0x")
    return [body[i:i + 64] for i in range(0, len(body), 64)]


def uint(answer: str, index: int = 0) -> int:
    return int(words(answer)[index], 16)


def address(answer: str, index: int = 0) -> str:
    return to_checksum_address("0x" + words(answer)[index][-40:])


def created_by(sender: str, nonce: int) -> str:
    """The address CREATE gives the `nonce`-th contract `sender` makes: keccak(rlp([sender, nonce]))[12:]."""
    if nonce == 0:
        encoded_nonce = b"\x80"
    elif nonce < 0x80:
        encoded_nonce = bytes([nonce])
    else:
        raw = nonce.to_bytes((nonce.bit_length() + 7) // 8, "big")
        encoded_nonce = bytes([0x80 + len(raw)]) + raw
    payload = b"\x94" + bytes.fromhex(sender.removeprefix("0x")) + encoded_nonce
    return to_checksum_address(keccak(bytes([0xC0 + len(payload)]) + payload)[12:])


def core(account: str, dexes: list[str]) -> dict:
    spot = post(INFO, {"type": "spotClearinghouseState", "user": account})
    usdc = next((b for b in spot.get("balances", []) if b.get("coin") == "USDC"), {})
    perps = {}
    for dex in dexes:
        body = {"type": "clearinghouseState", "user": account}
        if dex:
            body["dex"] = dex
        state = post(INFO, body)
        perps[dex or "main"] = {"account_value": state.get("marginSummary", {}).get("accountValue", "0"),
                                "withdrawable": state.get("withdrawable", "0"),
                                "positions": len(state.get("assetPositions", []))}
    ledger = post(INFO, {"type": "userNonFundingLedgerUpdates", "user": account, "startTime": 0})
    return {"spot_usdc": usdc.get("total", "0"), "spot_usdc_hold": usdc.get("hold", "0"), "perp": perps,
            "ledger": [movement(account, u) for u in ledger]}


def movement(account: str, update: dict) -> dict:
    """One ledger update, with its direction for this account and not the other party's address."""
    delta = update["delta"]
    to = (delta.get("destination") or "").lower()
    sender = (delta.get("user") or "").lower()
    direction = "in" if to == account.lower() else "out" if sender == account.lower() or to else "own"
    return {"time_ms": update["time"], "type": delta.get("type"), "direction": direction,
            "usdc": delta.get("usdc") or delta.get("amount")}


def read(block: str, dex_names: list[str], seats: set[str], record: dict) -> dict:
    factory = to_checksum_address(record["PoolFactory"])
    usdc = address(call(factory, "usdc()", block=block))
    nonce = int(rpc("eth_getTransactionCount", [factory, block]), 16)
    pools, challenges = [], []
    for n in range(1, nonce + SPARE):
        account = created_by(factory, n)
        if rpc("eth_getCode", [account, block]) in ("0x", None):
            continue
        if uint(call(factory, "isPool(address)", (account,), block)) == 1:
            pools.append({"nonce": n, "pool": account, "shared_pool_seat": account in seats})
            continue
        pool = address(call(account, "pool()", block=block))
        dexes = [""]
        if record.get("label") == "demo3":
            dexes.append(dex_names[uint(call(pool, "dex()", block=block))])
        row = {"nonce": n, "challenge": account, "pool": pool, "shared_pool_seat": pool in seats,
               "status": STATUS[uint(call(account, "status()", block=block))],
               "created_at": uint(call(account, "createdAt()", block=block)),
               "return_at": uint(call(account, "returnAt()", block=block)),
               "evm_hype_wei": str(int(rpc("eth_getBalance", [account, block]), 16)),
               "evm_usdc": str(uint(call(usdc, "balanceOf(address)", (account,), block)))}
        row.update(core(account, dexes))
        challenges.append(row)
    return {"label": record.get("label"), "factory": factory, "factory_nonce": nonce, "usdc": usdc,
            "pools": pools, "challenges": challenges}


def utc(seconds: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(seconds)) if seconds else "-"


def table(result: dict) -> str:
    """The reading as a Markdown table: one row a challenge, the money left on it, where and since when."""
    seat_no = {a: i + 1 for i, a in enumerate(result["shared_pool"]["seats"])}
    rows = ["| Deployment | Pool | Challenge | Status | `returnAt` (UTC) | Last move out on HyperCore (UTC) "
            "| In after it (USDC) | Spot USDC | Perp withdrawable | HYPE on HyperEVM (wei) | USDC on HyperEVM |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for deployment in result["deployments"]:
        for c in deployment["challenges"]:
            outs = [u["time_ms"] for u in c["ledger"] if u["direction"] == "out"]
            last_out = max(outs, default=0)
            # What came in after the last move out: only a settled account's figure says something is left over.
            arrived = sum(float(u["usdc"] or 0) for u in c["ledger"] if u["direction"] == "in" and u["time_ms"] > last_out)
            after = f"{arrived:g}" if c["status"] == "Settled" else "-"
            pool = f"shared pool, seat {seat_no[c['pool']]}" if c["pool"] in seat_no else f"`{c['pool']}`"
            perp = ", ".join(f"{k} {v['withdrawable']}" for k, v in c["perp"].items())
            rows.append(f"| {deployment['label']} | {pool} | `{c['challenge']}` | {c['status']} | {utc(c['return_at'])} "
                        f"| {utc(last_out / 1000)} | {after} | {c['spot_usdc']} | {perp} | {c['evm_hype_wei']} "
                        f"| {c['evm_usdc']} |")
    return "\n".join(rows) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=pathlib.Path, help="write the JSON here as well as to stdout")
    parser.add_argument("--md", type=pathlib.Path, help="write the reading as a Markdown table here")
    options = parser.parse_args()
    out_path = options.out
    head = rpc("eth_getBlockByNumber", ["latest", False])
    block = int(head["number"], 16)
    at = int(head["timestamp"], 16)
    pin = hex(block)
    shared = json.loads((ROOT / SHARED).read_text())
    answer = call(shared["SharedPool"], "seats()", block=pin)
    seats = [address(answer, 2 + i) for i in range(uint(answer, 1))]  # in the pool's own order
    dex_names = [(d or {}).get("name", "") for d in post(INFO, {"type": "perpDexs"})]
    result = {"read_at_block": block, "read_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at)),
              "rpc": RPC, "info": INFO,
              "shared_pool": {"address": to_checksum_address(shared["SharedPool"]), "seats": seats},
              "deployments": [read(pin, dex_names, set(seats), json.loads((ROOT / r).read_text())) for r in RECORDS]}
    text = json.dumps(result, indent=2) + "\n"
    if out_path:
        out_path.write_text(text)
    if options.md:
        options.md.write_text(f"Read at block {block}, {result['read_at']}.\n\n" + table(result))
    print(text, end="")


if __name__ == "__main__":
    main()
