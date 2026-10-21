"""Hyperliquid pieces the gateway needs: the action hash, recovering who signed an L1 action,
submitting a signed action, the mids the demo's caps count a sell at, and what the stop and the
take are worked out from (gateway/protect.py). Testnet only."""

from __future__ import annotations

import threading
from typing import Any

import requests
from hyperliquid.utils.signing import action_hash, recover_agent_or_user_from_l1_action

from .assets import MAIN_DEX, dex_index_of, order_asset, parse_perp_dexs
from .checks import GatewayError
from .protect import Book, Market, merge_books, parse_book, parse_markets

TESTNET_EXCHANGE_URL = "https://api.hyperliquid-testnet.xyz/exchange"
TESTNET_INFO_URL = "https://api.hyperliquid-testnet.xyz/info"
USER_AGENT = "colosseum-pools-gateway"


def hash_action(action: dict, nonce: int) -> bytes:
    """keccak(msgpack(action) || nonce || no-vault flag): what Hyperliquid signs over.
    The dict's key order is part of the bytes, so the action must travel unchanged."""
    return action_hash(action, None, nonce, None)


def recover_signer(action: dict, signature: dict, nonce: int) -> str:
    """The address Hyperliquid will attribute a testnet L1 action to (phantom agent, source b)."""
    return recover_agent_or_user_from_l1_action(action, signature, None, nonce, None, False)


def _info(body: dict, timeout: float) -> Any:
    resp = requests.post(TESTNET_INFO_URL, json=body, timeout=timeout, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.json()


def _on_dex(body: dict, dex: str) -> dict:
    """An info request for one dex: the first dex is asked with no `dex` field, as before HIP-3."""
    return body if dex == MAIN_DEX else {**body, "dex": dex}


class Venue:
    """The reads the stop and the take are worked out from: every perp's mark, and one account's
    equity, positions and open orders.

    `dexes` are the perp dexes whose markets it reads, Hyperliquid's own first. Each is one
    `metaAndAssetCtxs` request, so a dex that isn't traded is better left out."""

    def __init__(self, timeout: float = 5.0, dexes: tuple[str, ...] = (MAIN_DEX,)):
        self.timeout = timeout
        self.dexes = tuple(dexes)
        self._dex_index: dict[str, int] | None = None

    def dex_index(self, dex: str) -> int:
        """The dex's index, from `perpDexs`: read once, and again only for a name it didn't list."""
        if dex == MAIN_DEX:
            return 0
        if self._dex_index is None or dex not in self._dex_index:
            self._dex_index = parse_perp_dexs(_info({"type": "perpDexs"}, self.timeout))
        if dex not in self._dex_index:
            raise GatewayError(502, "no_such_dex", f"Hyperliquid lists no perp dex named {dex!r}")
        return self._dex_index[dex]

    def markets(self) -> dict[int, Market]:
        out: dict[int, Market] = {}
        for dex in self.dexes:
            index = self.dex_index(dex)  # first: a dex Hyperliquid doesn't list costs no market read
            out.update(parse_markets(_info(_on_dex({"type": "metaAndAssetCtxs"}, dex), self.timeout), index))
        return out

    def book(self, account: str, markets: dict[int, Market]) -> Book:
        """The account on every dex this venue reads: two requests a dex, its state and its orders,
        and the equity summed. An account with separate balances (the contracts set them, action 16)
        holds a part of its equity in each dex's clearinghouse."""
        books = []
        for dex in self.dexes:
            state = _info(_on_dex({"type": "clearinghouseState", "user": account}, dex), self.timeout)
            orders = _info(_on_dex({"type": "frontendOpenOrders", "user": account}, dex), self.timeout)
            books.append(parse_book(state, orders, markets))
        return merge_books(books)


_COINS: dict[int, str] = {}
_COINS_LOCK = threading.Lock()


def coin_of(asset: int, timeout: float = 5.0) -> str:
    """The coin Hyperliquid names an order's asset by ("BTC", "xyz:GOLD"), from its dex's `meta`:
    read once a dex and kept, since an index never changes its name on a dex. One fill at a time
    (the server is threaded), and a dex's names go in whole or not at all (CodeRabbit, cp#161)."""
    with _COINS_LOCK:
        if asset not in _COINS:
            dex = dex_index_of(asset)
            name = MAIN_DEX if dex == 0 else next(
                (n for n, i in parse_perp_dexs(_info({"type": "perpDexs"}, timeout)).items() if i == dex), None)
            if name is None:
                raise GatewayError(502, "no_market_price", f"Hyperliquid lists no perp dex {dex} for asset {asset}")
            names = {}
            for i, entry in enumerate(_info(_on_dex({"type": "meta"}, name), timeout)["universe"]):
                names[order_asset(dex, i)] = entry["name"]
            _COINS.update(names)
    if asset not in _COINS:
        raise GatewayError(502, "no_market_price", f"Hyperliquid lists no perp {asset}")
    return _COINS[asset]


def mids(timeout: float = 5.0, dex: str = MAIN_DEX) -> Any:
    """Every mid of one dex on Hyperliquid's testnet, keyed by coin name ("xyz:GOLD" on a HIP-3
    dex), as strings."""
    return _info(_on_dex({"type": "allMids"}, dex), timeout)


def submit(action: dict, nonce: int, signature: dict, timeout: float = 15.0) -> Any:
    body = {"action": action, "nonce": nonce, "signature": signature, "vaultAddress": None}
    resp = requests.post(TESTNET_EXCHANGE_URL, json=body, timeout=timeout, headers={"User-Agent": USER_AGENT})
    try:
        return resp.json()
    except ValueError:
        return {"status": "unreadable", "http": resp.status_code, "body": resp.text[:500]}


def _confirmed(status: Any) -> bool:
    return status in ("success", "waitingForTrigger") or (isinstance(status, dict) and any(
        isinstance(status.get(outcome), dict) for outcome in ("resting", "filled")))


def venue_outcome(answer: Any) -> tuple[str | None, bool]:
    """(Hyperliquid's reason for refusing, whether it confirmed the action).

    Hyperliquid answers `{"status": "err", "response": reason}` when it refuses the whole
    action, and `{"status": "ok", "response": {"data": {"statuses": [...]}}}` otherwise. A
    trader's order is a limit order without grouping and gets `{"resting": {...}}`,
    `{"filled": {...}}` or `{"error": reason}`; a cancel by oid gets `"success"` or
    `{"error": reason}`. The gateway's own stop and take are position TP/SL orders, which get
    `"waitingForTrigger"` (with no oid), and moving one is a modify, which gets `{"resting": {...}}`
    (measured on testnet, 28 Sep 2026). Anything else, `{}` included, confirms nothing."""
    if not isinstance(answer, dict):
        return None, False
    if answer.get("status") == "err":
        return str(answer.get("response") or "refused")[:300], False
    if answer.get("status") != "ok":
        return None, False
    response = answer.get("response")
    data = response.get("data") if isinstance(response, dict) else None
    statuses = data.get("statuses") if isinstance(data, dict) else None
    if not isinstance(statuses, list) or not statuses:
        return None, False
    for status in statuses:
        if isinstance(status, dict) and "error" in status:
            return str(status["error"])[:300], False
    return None, all(_confirmed(status) for status in statuses)
