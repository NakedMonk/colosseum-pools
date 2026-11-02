#!/usr/bin/env python3
"""A stop's 1-in-100 overshoot at the leverage rule v1.2: how far past the rule line a seat is closed.

The Economics page prices a failed challenge and a failed funded seat at the rule line plus this overshoot, and warns
when the reserve does not cover it. It is measured here on the default list, each coin at the leverage the rule gives
it (BTC and ETH 10x, SOL 5x; make_levers.py has the rule), over the eight crash days of the snapshot: a seat enters at
every minute of the day, on both sides, and the keeper closes it a minute late, at the open and at the worst price of
the window. The coins weigh the same. This writes `results/overshoot-v1.2-<date>.json`.

    python3 make_overshoot.py                         # writes results/overshoot-v1.2-2026-11-02.json
    python3 make_overshoot.py --out /tmp/o            # writes elsewhere, e.g. to diff against results/
    python3 make_overshoot.py --lev 5 --symbols "" --out /tmp/o
                                                      # every coin of the snapshot at 5x, as the run files of 25 Sep 2026
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile

import pool_stress as PS
from make_cascade import DEFAULT_LIST, leverage_words
from make_levers import DATA, DAYS, RESULTS, RULE, RULE_LEVERAGE, RULE_NOTE

HERE = os.path.dirname(os.path.abspath(__file__))
STAMP = "2026-11-02"
SIDES = {"лонг": "long", "шорт": "short"}
FIELDS = {"mean": "перелёт_средн_пп", "median": "перелёт_медиана_пп", "p95": "перелёт_p95_пп", "p99": "перелёт_p99_пп",
          "max": "перелёт_макс_пп", "share_beyond_cap": "хуже_просадки_%", "share_liquidated": "ликвидаций_%"}


def run(symbols: str, coin_lev: dict[str, float], lev: float, mode: str, out: str) -> dict:
    cmd = [sys.executable, os.path.join(HERE, "pool_stress.py"), "--data-dir", DATA, "--days", ",".join(DAYS),
           "--entries", "minute", "--lag", "1", "--exec", mode, "--lev", repr(lev), "--json", out]
    if symbols:
        cmd += ["--symbols", symbols]
    if coin_lev:
        cmd += ["--coin-lev", ",".join(f"{c}:{v!r}" for c, v in coin_lev.items())]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"pool_stress.py failed ({r.returncode}): {r.stderr[-400:]}")
    with open(out, encoding="utf-8") as f:
        res = json.load(f)
    if sorted(res["сутки"]) != sorted(DAYS):
        raise SystemExit(f"expected the eight days, got {res['сутки']}")
    return res


def block(summary: dict) -> dict:
    """One side, one way of executing: the summary in shares of the seat's capital, as the page's table holds it."""
    return {**{name: round(summary[key] / 100, 6) for name, key in FIELDS.items()}, "episodes": summary["эпизодов"]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=RESULTS)
    ap.add_argument("--symbols", default=DEFAULT_LIST, help="comma separated; empty is every coin of the snapshot")
    ap.add_argument("--lev", type=float, default=None, help="one leverage for every coin instead of the rule's")
    a = ap.parse_args()
    if a.lev is not None and not (a.lev > 0 and math.isfinite(a.lev)):
        raise SystemExit(f"--lev is a leverage, finite and above zero: {a.lev}")
    os.makedirs(a.out, exist_ok=True)
    named = [c.strip() for c in a.symbols.split(",") if c.strip()]
    a.symbols = ",".join(named)
    coins = named or PS.symbols()
    if not a.lev and (off := [c for c in coins if c not in RULE_LEVERAGE]):
        raise SystemExit(f"the rule gives no leverage for {', '.join(off)}: name its coins, or pass --lev for one leverage")
    coin_lev = {} if a.lev else {c: float(RULE_LEVERAGE[c]) for c in coins}
    overshoot: dict = {side: {} for side in SIDES.values()}
    by_coin: dict = {}
    with tempfile.TemporaryDirectory() as tmp:
        for mode in ("open", "worst"):
            res = run(a.symbols, coin_lev, a.lev or max(coin_lev.values()), mode, os.path.join(tmp, f"{mode}.json"))
            for summary in res["сводки"]:
                side = SIDES[summary["сводка"].split(",")[0]]
                overshoot[side][mode] = block(summary)
                print(f"{side:5} {mode:5} p99 {summary['перелёт_p99_пп']:>6} pp, mean {summary['перелёт_средн_пп']:>5} pp, "
                      f"beyond the cap {summary['хуже_просадки_%']:>5}%, liquidated {summary['ликвидаций_%']:>5}%")
            for coin in coins if coin_lev else []:
                only = run(coin, {coin: coin_lev[coin]} if coin_lev else {}, a.lev or coin_lev[coin], mode,
                           os.path.join(tmp, f"{coin}-{mode}.json"))
                for summary in only["сводки"]:
                    by_coin.setdefault(coin, {}).setdefault(SIDES[summary["сводка"].split(",")[0]], {})[mode] = block(summary)
    long = overshoot["long"]
    payload = {
        "produced": STAMP,
        "note": ("The snapshot and rules of the other result files (3% a day, 6% from the start, a 4.5 bps taker fee, "
                 "maintenance margin 2%) on the eight crash days: a seat enters at every minute of the day, on both sides, "
                 "the keeper closes it a minute late, at the open of the next minute and at the worst price inside it. "
                 "An overshoot is how far past the rule line the seat is closed, as a share of its capital, among the "
                 "seats that reached the line. The coins weigh the same."),
        "days": list(DAYS),
        "rule": {"leverage": RULE if not a.lev else None, "note": RULE_NOTE if not a.lev else f"every coin at {a.lev:g}x"},
        "coins": coins,
        "leverage": leverage_words(coins, [coin_lev[c] for c in coins]) if coin_lev else f"{a.lev:g}x each",
        "coin_leverage": coin_lev,
        "o99": {"low": min(long["open"]["p99"], long["worst"]["p99"]), "high": max(long["open"]["p99"], long["worst"]["p99"])},
        "overshoot": overshoot,
        "by_coin": by_coin,
    }
    path = os.path.join(a.out, f"overshoot-v1.2-{STAMP}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"o99 {payload['o99']}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
