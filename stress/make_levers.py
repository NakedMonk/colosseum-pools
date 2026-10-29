#!/usr/bin/env python3
"""The three levers an investor sets, run through this package on the same eight crash days.

A pool's worst day depends less on its rules than on three choices around them: where the stop
sits (on the exchange, at the line; or with a keeper that closes a minute later), how much
leverage the seats may take, and which coins they may trade. This runs `pool_stress.py` once for
every combination and writes `results/levers-<date>.json`, one cell per combination, from the
snapshot in `data/`. Nothing in the other result files changes.

    python3 make_levers.py                           # writes results/levers-2026-10-29.json
    python3 make_levers.py --out /tmp/l              # writes elsewhere, e.g. to diff against results/
    python3 make_levers.py --in-work-bps 9000 --out /tmp/l   # the seats at 90% of the pool, as on 29 Sep 2026

The leverage is one for every seat (3x, 5x, 10x), or the one the leverage rule v1.2 (27 Oct 2026) allows
each coin today, BTC and ETH 10x and SOL 5x, on the default list alone: the rule allows none of the
wide list's alts. The seats hold 80% of a $100k pool, the default share of a pool's plan (inWorkBps 8000),
in the proportions of the pool's seats (50 : 25 : 5 : 5 : 5); the rest is its reserve. A share of the
seats' capital does not move with the size of the seats, a share of the pool does, and each cell has both.

The long side only, as in the headline of README.md. A stop at the line has no waiting window, so
only the open is run for it; a keeper's minute has two ends, the open of the next minute and the
worst price inside it, and both are run. Each cell gives the median over the eight days of the
day's median loss, and the worst entry of the worst day, as a share of the seats' capital.

A stop on the exchange closes at the line, and walking the order book comes on top of it. The file
also carries the book: bounds derived here from `results/hl-book-2026-09-27.json`, which
`book/analyze_l2.py` wrote from our own collection of Hyperliquid's public order book (`book/collect_l2.py`,
l2Book, mainnet, 25-27 Sep 2026, a calm market). The raw snapshots are not published.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import statistics
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DATA, RESULTS = os.path.join(HERE, "data"), os.path.join(HERE, "results")
STAMP = "2026-10-29"
DAYS = ("2025-10-10", "2026-02-05", "2026-01-31", "2025-11-03",
        "2026-06-05", "2025-11-04", "2025-11-21", "2025-12-01")
# The two lists of README.md: the wide one (every coin in data/, the seats on the first five by
# name) and the default one, with the big seats on the calm coins.
LISTS = (("wide", None, None, "one seat on each of the first five coins by name: ADA, AVAX, BNB, BTC, DOGE"),
         ("default", "BTCUSDT,ETHUSDT,SOLUSDT", "BTCUSDT,ETHUSDT,SOLUSDT,SOLUSDT,SOLUSDT",
          "BTC, ETH and SOL, the big seats on the calm coins: BTC {0}, ETH {1}, SOL 3 x {2}"))
STOPS = ((0, "exchange", ("open",)), (1, "keeper", ("open", "worst")))
LEVERAGE = (3.0, 5.0, 10.0)
# The leverage rule v1.2 of 27 Oct 2026 (rule 22), coin by coin: the leverage the seats may take today.
RULE, RULE_LEVERAGE = "v1.2", {"BTCUSDT": 10.0, "ETHUSDT": 10.0, "SOLUSDT": 5.0}
RULE_NOTE = "the leverage rule v1.2 of 27 Oct 2026 (rule 22): BTC and ETH 10x, SOL 5x; none of the wide list's alts"
BOOK = os.path.join(RESULTS, "hl-book-2026-09-27.json")
POOL_USD = 100_000
IN_WORK_BPS = 8000                # the share of the pool its seats hold: a pool's plan's default (inWorkBps)
LAYOUT = (50, 25, 5, 5, 5)        # the seats' proportions, the pool's seats of pool_stress.py (POOL_SEATS)


def seats_usd(in_work_bps: int) -> list[float]:
    """The five seats' capital: the share of the pool in work, in the layout's proportions."""
    work = POOL_USD * in_work_bps / 10_000
    return [work * w / sum(LAYOUT) for w in LAYOUT]


def seat_leverage(lev, list_name: str) -> list[float]:
    """Each seat's leverage, in the order of its seats: one for all, or the rule's for each seat's coin."""
    if lev != RULE:
        return [lev] * len(LAYOUT)
    coins = dict((name, seats) for name, _, seats, _ in LISTS)[list_name].split(",")
    return [RULE_LEVERAGE[c] for c in coins]


def book_bounds(seats: list[float], path: str = BOOK) -> dict:
    """What walking the book costs, in basis points of the mid, for each coin and measured size.

    Each cell takes the worse of the two sides, closing a long into the bids or a short into the asks,
    since a seat's side is not known in advance: the median and the 99th percentile over every snapshot.
    Half the median spread is the least a market close costs at any size."""
    with open(path, encoding="utf-8") as f:
        r = json.load(f)
    coins = r["coins"]
    first = min(c["first_utc"] for c in coins.values())[:10]
    last = max(c["last_utc"] for c in coins.values())[:10]
    return {
        "source": "stress/results/hl-book-2026-09-27.json",
        "measured": (f"our own collection of Hyperliquid's public order book (l2Book, mainnet), "
                     f"{r['meta']['series']['files']} hourly files of snapshots from {first} to {last}, "
                     f"a calm market; the repository holds the derived bounds and the scripts that derive "
                     f"them, not the raw snapshots"),
        "half_spread_bps": {coin: round(c["spread_bps"]["median"] / 2, 3) for coin, c in coins.items()},
        "walk_bps": {coin: {str(size): {stat: round(max(c["cost_bps"][side][str(size)][stat]
                                                        for side in ("sell", "buy")), 3)
                                       for stat in ("median", "p99")}
                            for size in r["sizes_usd"]}
                     for coin, c in coins.items()},
        "seats_usd": [round(x, 2) for x in seats],
        "note": ("upper bounds: a seat's notional is priced at the nearest measured size at or above it, on the "
                 "worse side of the book; the taker fee is already inside the cells, so only walking the book "
                 "is added"),
    }


def run(lag: int, levs: list[float], symbols: str | None, seats: str | None, mode: str, out: str,
        capital: list[float]) -> dict:
    cmd = [sys.executable, os.path.join(HERE, "pool_stress.py"), "--data-dir", DATA, "--days", ",".join(DAYS),
           "--entries", "minute", "--seats-report", "--lag", str(lag), "--lev", str(levs[0]), "--exec", mode,
           "--seat-lev", ",".join(map(repr, levs)), "--seats-usd", ",".join(map(repr, capital)),
           "--pool-usd", str(POOL_USD), "--json", out]
    if symbols:
        cmd += ["--symbols", symbols, "--seat-coins", seats]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"pool_stress.py failed ({r.returncode}): {r.stderr[-400:]}")
    with open(out, encoding="utf-8") as f:
        rows = [x for x in json.load(f)["пул"] if x["сторона"] == "лонг"]
    if sorted(x["сутки"] for x in rows) != sorted(DAYS):
        raise SystemExit(f"expected the eight days, got {[x['сутки'] for x in rows]}")
    worst = max(rows, key=lambda x: x["худший_убыток_%_капитала_мест"])
    return {
        "median_of_days_pct": round(statistics.median(x["убыток_пула_медиана_%"] for x in rows), 2),
        "worst_day": worst["сутки"],
        "worst_entry": worst["худший_вход"],
        "worst_pct": worst["худший_убыток_%_капитала_мест"],
        "worst_usd": worst["худший_убыток_usd"],
        "seats_hit_on_worst": worst["мест_пробито_в_худшем"],
        "seats_liquidated_on_worst": worst["мест_ликвидировано_в_худшем"],
        "days_with_liquidations": sum(1 for x in rows if x["мест_ликвидировано_в_худшем"]),
        "worst_pct_of_pool": worst["худший_убыток_%_пула"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=RESULTS)
    ap.add_argument("--in-work-bps", type=int, default=IN_WORK_BPS,
                    help="the share of the pool the seats hold, in basis points (9000 as on 29 Sep 2026)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    capital = seats_usd(a.in_work_bps)
    cells = []
    with tempfile.TemporaryDirectory() as tmp:
        for (lag, stop, modes), lev, (name, symbols, seats, _) in itertools.product(STOPS, LEVERAGE + (RULE,), LISTS):
            if lev == RULE and name != "default":
                continue                            # the rule allows none of the wide list's alts
            levs = seat_leverage(lev, name)
            for mode in modes:
                tag = lev if lev == RULE else f"{lev:.0f}x"
                out = os.path.join(tmp, f"{stop}-{tag}-{name}-{mode}.json")
                cells.append({"stop": stop, "lag": lag, "leverage": lev, "list": name, "exec": mode,
                              **run(lag, levs, symbols, seats, mode, out, capital), "seat_leverage": levs})
                c = cells[-1]
                print(f"{stop:8} {tag:4} {name:7} {mode:5} median {c['median_of_days_pct']:>5}%  "
                      f"worst {c['worst_pct']:>5}% of the seats, {c['worst_pct_of_pool']:>5}% of the pool  "
                      f"liquidated {c['seats_liquidated_on_worst']}")
    payload = {
        "produced": STAMP,
        "note": ("The same snapshot and rules as the other result files: 3% a day, 6% from the start, a 4.5 bps "
                 "taker fee, maintenance margin 2%, five seats in the proportions 50 : 25 : 5 : 5 : 5 all entering "
                 "the same minute, long side. A stop on the exchange closes at the line itself: that is the line, "
                 "not a fill in a real book, and the book in a cascade has not been measured."),
        "pool": {"usd": POOL_USD, "in_work_bps": a.in_work_bps, "seats_usd": [round(x, 2) for x in capital],
                 "note": ("the seats hold this share of the pool and the rest is its reserve; a cell's worst_pct is "
                          "a share of the seats' capital, its worst_pct_of_pool a share of the whole pool")},
        "rule": {"leverage": RULE, "seat_leverage": {c[:-4]: v for c, v in RULE_LEVERAGE.items()}, "note": RULE_NOTE},
        "days": list(DAYS),
        "lists": {name: desc.format(*(f"{x / 1000:.1f}".removesuffix(".0") + "k" for x in capital))
                  for name, _, _, desc in LISTS},
        "cells": cells,
        "book": book_bounds(capital),
    }
    path = os.path.join(a.out, f"levers-{STAMP}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"{len(cells)} cells: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
