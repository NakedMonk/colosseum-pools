"""How Hyperliquid numbers a perp, on the first dex and on a builder-deployed one (HIP-3).

Every perp dex has a universe of its own, listed by `meta` with a `dex` field. The first dex,
Hyperliquid's own, has index 0 and its perps keep their plain index. A perp of any other dex is
numbered 100000 + dex index * 10000 + its index in that dex's `meta`, in orders and in the API
(Hyperliquid's docs, "Asset IDs"). HyperEVM's precompiles number the same perp 100000 less:
dex index * 10000 + index. Measured on testnet on 21 Oct 2026: xyz is dex 65, and xyz:GOLD,
order asset 750003, is 650003 to markPx, perpAssetInfo and position2; 750003 there is an error.
"""

from __future__ import annotations

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
