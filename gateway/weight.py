"""What this process spends of Hyperliquid's REST budget, by the weights Hyperliquid publishes
("Rate limits and user limits"): 1,200 a minute per IP, shared by everything on the host.

An info request weighs 2 for `l2Book`, `allMids`, `clearinghouseState`, `orderStatus`,
`spotClearinghouseState` and `exchangeStatus`, 60 for `userRole`, and 20 for every other; an exchange
action weighs 1 plus 1 for every 40 orders, cancels or modifies in it. Every request this process
makes is added as it is sent, refused or not, so the count is what Hyperliquid counts, not what
succeeded. The gateway says the last minute's count in `/v1/health` and both the gateway and the
keeper write it to their logs: the host's ceiling is then a reading, not an estimate from the docs.
Testnet only, as the rest."""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable

LIGHT = frozenset({"l2Book", "allMids", "clearinghouseState", "orderStatus", "spotClearinghouseState", "exchangeStatus"})
WINDOW_S = 60.0


def info_weight(body: dict) -> int:
    kind = body.get("type")
    if kind in LIGHT:
        return 2
    if kind == "userRole":
        return 60
    return 20


def exchange_weight(action: Any) -> int:
    items = 0
    if isinstance(action, dict):
        for name in ("orders", "cancels", "modifies"):
            if isinstance(action.get(name), list):
                items = len(action[name])
                break
    return 1 + items // 40


class Meter:
    """The weight sent in the last minute, by request type. Thread-safe: the gateway's handlers and
    its sweep add to one meter."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, window: float = WINDOW_S):
        self.clock = clock
        self.window = window
        self._sent: deque[tuple[float, str, int]] = deque()
        self._lock = threading.Lock()

    def add(self, kind: str, weight: int) -> None:
        with self._lock:
            self._sent.append((self.clock(), kind, weight))

    def info(self, body: dict) -> None:
        self.add(str(body.get("type")), info_weight(body))

    def exchange(self, action: Any) -> None:
        self.add("exchange", exchange_weight(action))

    def per_minute(self) -> dict:
        """{"total": weight of the last minute, "by_type": {request type: weight}}."""
        with self._lock:
            edge = self.clock() - self.window
            while self._sent and self._sent[0][0] <= edge:
                self._sent.popleft()
            by_type: dict[str, int] = {}
            for _, kind, weight in self._sent:
                by_type[kind] = by_type.get(kind, 0) + weight
        return {"total": sum(by_type.values()), "by_type": dict(sorted(by_type.items()))}


# One per process: everything this process sends to Hyperliquid comes out of the same IP's minute.
METER = Meter()
