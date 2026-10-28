"""The demo's faucet for testers from outside: `POST /v1/faucet {"address": "0x…"}` sends that address
one exam in the demo's USDC and the HYPE for the gas of the exam's own transactions, both from the
faucet's wallet (the CTO, 27 Oct 2026). An exam on the demo3 pool costs 1 dUSDC and the platform's 0.7.

Only a gateway whose deployment has a faucet serves it. GATEWAY_FAUCET_KEY_FILE names the wallet's key,
and the deployment's record names the token and the one address the token lets mint up to its daily
limit (DemoUsdc.setFaucet); a key that is not that address stops the start. With no key file the path
answers 404 `no_faucet`.

What it refuses, in words and with nothing sent:
- an address that had its grant this UTC day;
- a client past GATEWAY_FAUCET_PER_IP grants this UTC day, by the address nginx passes as X-Real-IP;
- a day the token's own limit for the faucet (DemoUsdc.faucetDaily) no longer covers;
- a faucet without the HYPE for the grant and for its own two transactions;
- an address that refuses a plain HYPE transfer.

All or nothing, as far as two transactions can be: every check runs before the first one goes. The HYPE
goes first and the dUSDC second, because dUSDC without gas is of no use to a tester and HYPE without
dUSDC costs only the HYPE. If the dUSDC then fails, the answer and the journal say so.

Every grant that sent anything is a line of grants.jsonl in GATEWAY_FAUCET_DIR (by default the
service's own state directory): the address, the time, the amounts and the transactions. The day's
addresses and clients are in state.json beside it, so a restart forgets nothing. A client is kept only
as a hash salted afresh each day, never as its address.

Testnet only, as the rest of the gateway.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

import eth_abi
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_utils import keccak, to_checksum_address

from .chain import CHAIN_ID, Throttled

DAY = 86_400
USDC = Decimal(10**6)
HYPE = Decimal(10**18)
ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
ZERO = "0x" + "00" * 20
# One exam on the demo3 pool: its price, 1 dUSDC, and the platform's fee, 0.7.
USDC_DEFAULT = "1.7"
# The exam's own transactions, measured on 27 Oct 2026: approve 46,736 gas and buyChallenge 876,693, both
# estimated on the demo3 pool from an outside address, and graduate at most 502,235 (forge, the same
# code; forfeit, the other way out, at most 421,762). 1.43 million gas is 0.000143 HYPE at that day's
# 0.1 gwei; 0.001 is seven times that, for a retry or a dearer block.
HYPE_DEFAULT = "0.001"
PER_IP_DEFAULT = 3
# HyperEVM's small blocks come a second apart, and a transfer or a mint fits in one.
RECEIPT_WAIT_S = 20
# On the estimates, as the spike's sender (spike/hlspike/common.py); the fee cap is twice the gas price.
GAS_MARGIN = Decimal("1.25")
OVER_DAILY = keccak(text="OverDaily(uint256)")[:4].hex()
# What HyperEVM's node answers an estimate the call would revert, as it did for a HYPE transfer to the
# demo3 token on 28 Oct 2026: {'code': 3, 'message': 'execution reverted'}. Any other error is the node's.
REVERTED = "execution reverted"


class Refused(Exception):
    """A grant refused before anything was sent. 4xx only: the CDN in front replaces the body of a 5xx
    with its own page (gateway/server.py, `not_sent`), and then nobody reads why."""

    def __init__(self, status: int, code: str, detail: str, **extra: Any):
        super().__init__(code)
        self.status, self.code, self.detail, self.extra = status, code, detail, extra

    def answer(self) -> tuple[int, dict]:
        return self.status, {"status": "refused_by_faucet", "code": self.code, "detail": self.detail,
                             "sent": False, **self.extra}


class NotSent(RuntimeError):
    """The transaction never reached the node, so nothing can have happened on chain."""


def until_tomorrow(now: float) -> int:
    return DAY - int(now) % DAY


def parse_address(body: Any) -> str:
    """The address the grant goes to, checksummed. One written in mixed case must be a valid checksum:
    a typo in it is a different address, and the faucet would pay a stranger."""
    raw = body.get("address") if isinstance(body, dict) else None
    if not isinstance(raw, str) or not ADDRESS.match(raw) or raw.lower() == ZERO:
        raise Refused(400, "bad_address", 'send {"address": "0x…"}, a wallet address of 40 hex digits')
    address = to_checksum_address(raw)
    if raw[2:] != raw[2:].lower() and raw[2:] != raw[2:].upper() and raw != address:
        raise Refused(400, "bad_address", "the address's capitals are not its checksum; copy it again")
    return address


class Ledger:
    """The day's grants on disk: which addresses had one, and how many grants each client took."""

    def __init__(self, directory: str | pathlib.Path, per_ip: int, clock: Callable[[], float] = time.time):
        self.dir = pathlib.Path(directory)
        self.per_ip = per_ip
        self.clock = clock
        self.state_file = self.dir / "state.json"
        self.journal = self.dir / "grants.jsonl"
        try:
            self._state = json.loads(self.state_file.read_text())
        except FileNotFoundError:
            self._state = {}
        except (OSError, ValueError) as exc:
            # Starting afresh would forget who had a grant today, and pay them twice.
            raise SystemExit(f"refusing to start: {self.state_file} cannot be read ({exc}); "
                             "move it aside only if today's grants may be paid again") from None

    def _today(self) -> dict:
        day = int(self.clock()) // DAY
        if self._state.get("day") != day:
            self._state = {"day": day, "salt": secrets.token_hex(16), "addresses": {}, "clients": {}}
        return self._state

    def _client(self, ip: str) -> str:
        return hashlib.sha256((self._state["salt"] + ip).encode()).hexdigest()[:24]

    def _save(self) -> None:
        tmp = self.state_file.with_suffix(".next")
        tmp.write_text(json.dumps(self._state))
        os.replace(tmp, self.state_file)

    def reserve(self, address: str, ip: str) -> int:
        """Takes today's grant for the address and one of the client's, before anything is sent: a
        grant that crashes midway stays taken, so it is never paid twice. Returns the day."""
        state = self._today()
        wait = until_tomorrow(self.clock())
        if address in state["addresses"]:
            raise Refused(429, "address_served_today",
                          "this address had its grant today; the next one opens at 00:00 UTC", retry_after_s=wait)
        client = self._client(ip)
        if state["clients"].get(client, 0) >= self.per_ip:
            raise Refused(429, "ip_limit",
                          f"{self.per_ip} grants a day go to one network, and yours has had them; "
                          "the next opens at 00:00 UTC", retry_after_s=wait)
        state["addresses"][address] = "reserved"
        state["clients"][client] = state["clients"].get(client, 0) + 1
        self._save()
        return state["day"]

    def release(self, address: str, ip: str, day: int) -> None:
        """Gives the grant back when nothing reached the address. A day that has turned over has
        dropped it already."""
        state = self._today()
        if state["day"] != day or state["addresses"].pop(address, None) is None:
            return
        client = self._client(ip)
        left = state["clients"].get(client, 0) - 1
        if left > 0:
            state["clients"][client] = left
        else:
            state["clients"].pop(client, None)
        self._save()

    def settle(self, address: str, day: int, entry: dict) -> None:
        """Records what went: the day's state, and a line of the journal, which is never rewritten."""
        state = self._today()
        if state["day"] == day:
            state["addresses"][address] = entry["status"]
            self._save()
        with open(self.journal, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
            f.flush()
            os.fsync(f.fileno())


class Wallet:
    """The faucet's own transactions on HyperEVM testnet."""

    def __init__(self, rpc: Callable[[str, list], Any], account: LocalAccount,
                 sleep: Callable[[float], None] = time.sleep, wait_s: float = RECEIPT_WAIT_S):
        self.rpc = rpc
        self.account = account
        self.sleep = sleep
        self.wait_s = wait_s

    def send(self, to: str, value: int, data: bytes, gas: int, nonce: int, max_fee: int) -> tuple[str, dict | None]:
        """Signs and sends one transaction and waits for its receipt: the hash and the receipt, or None
        for a receipt that did not come in time. NotSent when the node never took it."""
        tx = {"chainId": CHAIN_ID, "nonce": nonce, "to": to_checksum_address(to), "value": value, "data": data,
              "gas": gas, "type": 2, "maxPriorityFeePerGas": 0, "maxFeePerGas": max_fee}
        try:
            signed = self.account.sign_transaction(tx)
        except Exception as exc:
            raise NotSent(f"signing: {exc}") from exc
        tx_hash = "0x" + signed.hash.hex().removeprefix("0x")
        try:
            self.rpc("eth_sendRawTransaction", ["0x" + signed.raw_transaction.hex().removeprefix("0x")])
        except Throttled as exc:
            raise NotSent(str(exc)) from exc  # every try refused before the node read it
        except RuntimeError as exc:
            # The node answered. "already known" is this very transaction; "nonce too low" may be it,
            # mined already. Either way its receipt says; anything else, it was refused.
            message = str(exc).lower()
            if "already known" not in message and "nonce too low" not in message:
                raise NotSent(str(exc)) from exc
        except Exception:
            pass  # the connection broke: it may have reached the node, so its receipt decides
        waited = 0.0
        while waited < self.wait_s:
            try:
                receipt = self.rpc("eth_getTransactionReceipt", [tx_hash])
            except Exception:
                receipt = None
            if receipt:
                return tx_hash, receipt
            self.sleep(1.0)
            waited += 1.0
        return tx_hash, None


@dataclass(frozen=True)
class Plan:
    hype_gas: int
    usdc_gas: int
    max_fee: int
    nonce: int


def mined(receipt: dict | None) -> bool:
    return bool(receipt) and int(str(receipt.get("status", "0x0")), 16) == 1


class Faucet:
    def __init__(self, rpc: Callable[[str, list], Any], account: LocalAccount, token: str, ledger: Ledger,
                 usdc: Decimal, hype: Decimal, wallet: Wallet | None = None, log: Callable[..., None] | None = None):
        self.rpc = rpc
        self.account = account
        self.token = to_checksum_address(token)
        self.ledger = ledger
        self.usdc = usdc
        self.hype = hype
        self.usdc_units = int(usdc * USDC)
        self.hype_wei = int(hype * HYPE)
        self.wallet = wallet if wallet is not None else Wallet(rpc, account)
        self.log = log or (lambda **_: None)
        self._lock = threading.Lock()

    def summary(self) -> dict:
        """What `/v1/health` says of the faucet: what one grant is, and how many a network gets."""
        return {"usdc": str(self.usdc), "hype": str(self.hype), "per_ip": self.ledger.per_ip}

    def grant(self, body: Any, ip: str) -> tuple[int, dict]:
        try:
            address = parse_address(body)
        except Refused as r:
            return r.answer()
        # One grant at a time: the two transactions of one take consecutive nonces of the one wallet.
        if not self._lock.acquire(blocking=False):
            return Refused(429, "faucet_busy", "another grant is being sent; ask again in a few seconds",
                           retry_after_s=10).answer()
        try:
            return self._grant(address, ip)
        finally:
            self._lock.release()

    def _grant(self, address: str, ip: str) -> tuple[int, dict]:
        try:
            day = self.ledger.reserve(address, ip)
        except Refused as r:
            return r.answer()
        try:
            plan = self._check(address, day)
        except Refused as r:
            self.ledger.release(address, ip, day)
            return r.answer()
        except Throttled:
            self.ledger.release(address, ip, day)
            return Refused(429, "upstream_busy", "the chain node is refusing reads right now; nothing was sent, "
                           "ask again in a moment", retry_after_s=30).answer()
        except Exception as exc:
            self.ledger.release(address, ip, day)
            self.log(event="faucet_read_failed", error=type(exc).__name__)
            return Refused(424, "upstream_failed", "a read of the chain failed; nothing was sent").answer()
        return self._send(address, ip, day, plan)

    def _call(self, signature: str, types: list[str], args: list[Any]) -> int:
        data = keccak(text=signature)[:4] + eth_abi.encode(types, args)
        raw = self.rpc("eth_call", [{"to": self.token, "data": "0x" + data.hex()}, "latest"])
        return eth_abi.decode(["uint256"], bytes.fromhex(raw[2:]))[0]

    def _estimate(self, call: dict) -> int:
        return int(Decimal(int(self.rpc("eth_estimateGas", [{"from": self.account.address, **call}]), 16)) * GAS_MARGIN)

    def mint_data(self, address: str) -> bytes:
        return keccak(text="mint(address,uint256)")[:4] + eth_abi.encode(["address", "uint256"], [address, self.usdc_units])

    def _check(self, address: str, day: int) -> Plan:
        wait = until_tomorrow(self.ledger.clock())
        left = self._call("faucetDaily()", [], []) - self._call("faucetMinted(uint256)", ["uint256"], [day])
        if left < self.usdc_units:
            raise Refused(429, "faucet_daily_limit", "the faucet has given out what the token lets it mint today; "
                          "it opens again at 00:00 UTC", retry_after_s=wait)
        empty = Refused(429, "faucet_empty", "the faucet has no HYPE left for gas, so it sent nothing; "
                        "it is filled by hand")
        balance = int(self.rpc("eth_getBalance", [self.account.address, "latest"]), 16)
        if balance < self.hype_wei:
            raise empty
        try:
            hype_gas = self._estimate({"to": address, "value": hex(self.hype_wei)})
        except Throttled:
            raise
        except RuntimeError as exc:
            if REVERTED not in str(exc):
                raise
            raise Refused(400, "cannot_take_hype", "this address refuses a plain HYPE transfer; "
                          "give the address of a wallet") from None
        try:
            usdc_gas = self._estimate({"to": self.token, "data": "0x" + self.mint_data(address).hex()})
        except Throttled:
            raise
        except RuntimeError as exc:
            if OVER_DAILY in str(exc).lower():
                raise Refused(429, "faucet_daily_limit", "the faucet has given out what the token lets it mint "
                              "today; it opens again at 00:00 UTC", retry_after_s=wait) from None
            raise
        max_fee = int(self.rpc("eth_gasPrice", []), 16) * 2
        if balance < self.hype_wei + (hype_gas + usdc_gas) * max_fee:
            raise empty
        nonce = int(self.rpc("eth_getTransactionCount", [self.account.address, "pending"]), 16)
        return Plan(hype_gas, usdc_gas, max_fee, nonce)

    def _send(self, address: str, ip: str, day: int, plan: Plan) -> tuple[int, dict]:
        try:
            hype_tx, receipt = self.wallet.send(address, self.hype_wei, b"", plan.hype_gas, plan.nonce, plan.max_fee)
        except NotSent as exc:
            self.ledger.release(address, ip, day)
            self.log(event="faucet_not_sent", address=address, error=str(exc)[:200])
            return Refused(424, "upstream_failed", "the HYPE transfer could not be handed to the chain; "
                           "nothing was sent").answer()
        if receipt is None:
            return self._record(address, day, 202, "pending", hype_tx, None,
                                "the HYPE transfer has not shown in a block yet, so the dUSDC was held back; "
                                "this address's grant for today is spent, and the journal has it")
        if not mined(receipt):
            self.ledger.release(address, ip, day)
            self.log(event="faucet_hype_reverted", address=address, tx=hype_tx)
            return 424, {"status": "failed", "code": "hype_reverted", "sent": False, "hype_tx": hype_tx,
                         "detail": "the HYPE transfer was refused on chain and nothing reached the address; "
                                   "ask again in a moment"}
        try:
            usdc_tx, receipt = self.wallet.send(self.token, 0, self.mint_data(address), plan.usdc_gas,
                                                plan.nonce + 1, plan.max_fee)
        except NotSent as exc:
            self.log(event="faucet_not_sent", address=address, error=str(exc)[:200])
            usdc_tx, receipt = None, {"status": "0x0"}
        if receipt is None:
            return self._record(address, day, 202, "pending", hype_tx, usdc_tx,
                                "the HYPE went; the dUSDC has not shown in a block yet, check its transaction")
        if not mined(receipt):
            return self._record(address, day, 424, "partial", hype_tx, usdc_tx,
                                "the HYPE went and the dUSDC did not; this address's grant for today is spent, "
                                "and the journal has it for the team to finish by hand")
        return self._record(address, day, 200, "sent", hype_tx, usdc_tx, None)

    def _record(self, address: str, day: int, http: int, status: str, hype_tx: str, usdc_tx: str | None,
                detail: str | None) -> tuple[int, dict]:
        when = datetime.fromtimestamp(self.ledger.clock(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # The dUSDC counts once its mint went and was not refused: a partial grant's mint may have a hash too.
        usdc = str(self.usdc) if usdc_tx and status != "partial" else "0"
        entry = {"t": when, "address": address, "status": status, "hype": str(self.hype), "hype_tx": hype_tx,
                 "usdc": usdc, "usdc_tx": usdc_tx}
        self.ledger.settle(address, day, entry)
        self.log(event="faucet_grant", **entry)
        payload = {"status": status, "sent": True, "address": address, "hype": str(self.hype), "hype_tx": hype_tx,
                   "usdc": usdc, "usdc_tx": usdc_tx}
        if detail:
            payload["detail"] = detail
        return http, payload


def amount(env: dict, name: str, default: str) -> Decimal:
    try:
        value = Decimal(env.get(name) or default)
    except InvalidOperation:
        raise SystemExit(f"refusing to start: {name} is not a number") from None
    if not value > 0:
        raise SystemExit(f"refusing to start: {name} must be above 0")
    return value


def faucet_from_env(env, rpc: Callable[[str, list], Any], root: pathlib.Path,
                    log: Callable[..., None] | None = None) -> Faucet | None:
    """The faucet GATEWAY_FAUCET_KEY_FILE asks for, checked against the deployment's record and the
    token on chain before the gateway listens; None when the variable is unset."""
    key_file = env.get("GATEWAY_FAUCET_KEY_FILE")
    if not key_file:
        return None
    path = pathlib.Path(key_file)
    try:
        mode, text = path.stat().st_mode, path.read_text()
    except OSError as exc:
        raise SystemExit(f"refusing to start: GATEWAY_FAUCET_KEY_FILE {path} cannot be read "
                         f"({exc.strerror or type(exc).__name__})") from None
    if mode & 0o077:
        raise SystemExit(f"{path} is readable by others; chmod 600 it")
    try:
        account = Account.from_key(text.strip())
    except Exception:
        # Not the parser's words: they could quote the file.
        raise SystemExit(f"refusing to start: GATEWAY_FAUCET_KEY_FILE {path} holds no private key") from None
    label = env.get("COLOSSEUM_DEPLOYMENT", "")
    record_file = root / "deployments" / f"testnet-{label}.json"
    try:
        record = json.loads(record_file.read_text())
    except (OSError, ValueError):
        raise SystemExit(f"refusing to start: the faucet needs the deployment's record, and {record_file} "
                         "cannot be read (COLOSSEUM_DEPLOYMENT)") from None
    named = (record.get("faucet") or {}).get("address", "")
    if not named or named.lower() != account.address.lower():
        raise SystemExit(f"refusing to start: GATEWAY_FAUCET_KEY_FILE is {account.address}, and the record of "
                         f"{label} names {named or 'no faucet'}")
    token = record.get("usdc")
    if not token:
        raise SystemExit(f"refusing to start: the record of {label} names no token for the faucet to mint")
    data = "0x" + keccak(text="faucet()")[:4].hex()
    try:
        raw = rpc("eth_call", [{"to": token, "data": data}, "latest"])
        on_chain = to_checksum_address(eth_abi.decode(["address"], bytes.fromhex(raw[2:]))[0])
    except Exception as exc:
        raise SystemExit(f"refusing to start: the token {token} did not say whom it lets mint "
                         f"({type(exc).__name__})") from None
    if on_chain != account.address:
        raise SystemExit(f"refusing to start: the token {token} mints for {on_chain}, not for {account.address}")
    directory = env.get("GATEWAY_FAUCET_DIR") or env.get("STATE_DIRECTORY", "").split(":")[0]
    if not directory or not os.access(directory, os.W_OK):
        raise SystemExit("refusing to start: the faucet needs a directory it can write, GATEWAY_FAUCET_DIR "
                         "or the service's StateDirectory (docs/HOSTING.md)")
    try:
        per_ip = int(env.get("GATEWAY_FAUCET_PER_IP") or PER_IP_DEFAULT)
    except ValueError:
        raise SystemExit("refusing to start: GATEWAY_FAUCET_PER_IP is not a whole number") from None
    if per_ip < 1:
        raise SystemExit("refusing to start: GATEWAY_FAUCET_PER_IP must be at least 1")
    ledger = Ledger(directory, per_ip)
    return Faucet(rpc, account, token, ledger, amount(env, "GATEWAY_FAUCET_USDC", USDC_DEFAULT),
                  amount(env, "GATEWAY_FAUCET_HYPE", HYPE_DEFAULT), log=log)
