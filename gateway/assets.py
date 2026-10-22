"""How Hyperliquid numbers a perp, on the first dex and on a builder-deployed one (HIP-3).

Every perp dex has a universe of its own, listed by `meta` with a `dex` field. The first dex,
Hyperliquid's own, has index 0 and its perps keep their plain index. A perp of any other dex is
numbered 100000 + dex index * 10000 + its index in that dex's `meta`, in orders and in the API
(Hyperliquid's docs, "Asset IDs"). HyperEVM's precompiles number the same perp 100000 less:
dex index * 10000 + index. Measured on testnet on 21 Oct 2026: xyz is dex 65, and xyz:GOLD,
order asset 750003, is 650003 to markPx, perpAssetInfo and position2; 750003 there is an error.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

MAIN_DEX = ""          # the first dex's name: `perpDexs` lists it as null
HIP3_BASE = 100_000
DEX_STRIDE = 10_000


def order_asset(dex_index: int, index: int) -> int:
    """The number an order (and the info API) uses for the perp at `index` in dex `dex_index`."""
    if dex_index < 0 or not 0 <= index < DEX_STRIDE:
        raise ValueError(f"no perp number for dex {dex_index}, index {index}")
    return index if dex_index == 0 else HIP3_BASE + dex_index * DEX_STRIDE + index


def precompile_index(asset: int) -> int:
    """The number HyperEVM's precompiles know an order's asset by."""
    return asset - HIP3_BASE if asset >= HIP3_BASE else asset


def dex_index_of(asset: int) -> int:
    """The dex an order's asset belongs to: 0 for Hyperliquid's own."""
    return 0 if asset < HIP3_BASE else (asset - HIP3_BASE) // DEX_STRIDE


# A HIP-3 perp opens nothing while its market is closed or about to be (CTO, 21 Oct 2026: flat over
# the weekend by default). In New York time, so the hours hold through the change of clocks on
# 1 Nov 2026: the CTO's 20:00Z Friday, 22:05Z Sunday and 21:00-22:00Z Monday to Thursday are these
# hours under daylight saving. Monday is 0.
NEW_YORK = ZoneInfo("America/New_York")
WEEKEND_FROM = (4, 16 * 60)          # Friday 16:00, an hour before the Friday close
WEEKEND_UNTIL = (6, 18 * 60 + 5)     # Sunday 18:05, five minutes after the reopening
MAINTENANCE = (17 * 60, 18 * 60)     # Monday to Thursday 17:00-18:00, the daily window


def hip3_closed(asset: int, now: float) -> str | None:
    """Why a HIP-3 perp may not open a position at `now` (unix seconds), or None. Hyperliquid's own
    perps trade around the clock and are never closed here."""
    if dex_index_of(asset) == 0:
        return None
    t = datetime.fromtimestamp(now, NEW_YORK)
    week = (t.weekday(), t.hour * 60 + t.minute)
    if WEEKEND_FROM <= week < WEEKEND_UNTIL:
        return "the weekend: Friday 16:00 to Sunday 18:05, New York time"
    if t.weekday() <= 3 and MAINTENANCE[0] <= week[1] < MAINTENANCE[1]:
        return "the daily maintenance hour, 17:00-18:00 New York time"
    return None


WEEKEND_FLAT_FROM = (4, 16 * 60 + 30)  # Friday 16:30: a HIP-3 position still open from here is flagged
# Friday 16:00 to the close at 17:00: the gateway's sweep closes the HIP-3 positions of a pool that is
# flat over the weekend, so they are flat by 16:30 with time to spare (CTO, 21 Oct 2026).
WEEKEND_CLOSE = ((4, 16 * 60), (4, 17 * 60))


def weekend_close_due(now: float) -> bool:
    """Whether `now` is in the hour before the Friday close, New York time, in which a pool that is flat
    over the weekend has its HIP-3 positions closed."""
    t = datetime.fromtimestamp(now, NEW_YORK)
    return WEEKEND_CLOSE[0] <= (t.weekday(), t.hour * 60 + t.minute) < WEEKEND_CLOSE[1]


def weekend_flat_due(now: float) -> bool:
    """Whether a HIP-3 position should be flat by `now`: from Friday 16:30 to Sunday 18:05, New York time
    (CTO, 21 Oct 2026: flat over the weekend is the pool's default)."""
    t = datetime.fromtimestamp(now, NEW_YORK)
    return WEEKEND_FLAT_FROM <= (t.weekday(), t.hour * 60 + t.minute) < WEEKEND_UNTIL


def parse_perp_dexs(answer: object) -> dict[str, int]:
    """`perpDexs`: each dex's name and its index. The first entry is null, Hyperliquid's own dex."""
    if not isinstance(answer, list) or not answer or answer[0] is not None:
        raise ValueError("perpDexs: not a list that starts with the first dex (null)")
    out = {MAIN_DEX: 0}
    for i, entry in enumerate(answer[1:], start=1):
        name = entry.get("name") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name or name in out:
            raise ValueError(f"perpDexs: entry {i} has no name of its own: {str(entry)[:80]!r}")
        out[name] = i
    return out
