# NEAR HACKATHON: Upools rules on NEAR (design, for CTO confirmation before build)

Status: proposal. Nothing is built yet. Brief: namixai/usenami-platform#1986 (CTO comment of 28.10, Alex's word: two independent entries).

## Entry and track
- Entry: **Upools on NEAR**, independent from the Signer entry (Signer = Chain Signatures / policy contract via `namixai/intent-pay`). Each README names the other (rule Q-015).
- Track: **`defi` (Unstoppable DeFi)**. Why: the contract governs trading capital (pool rules, loss limits, trader admission); it is a DeFi risk primitive, not a real-world payments flow.
- Deadline: 2026-11-14 23:59 UTC (source: #1986 brief). Testnet only.

## Goal
A pool's risk rules and each trader's exam record live in a NEAR contract that anyone can read on chain. A pool's "the trader broke a rule and was stopped" is recorded there with its reason, so an investor verifies a pool from NEAR state, not from our database.

## What the NEAR contract does (`upools-registry`, Rust, near-sdk)
1. `create_pool(rules)` — owner registers a pool: daily loss bps, max drawdown bps, max leverage x100, allowed perps list (same fields as `src/Types.sol` `Rules`). Rules are immutable after creation.
2. `record_exam(pool_id, trader, result, evidence_hash)` — an exam result (passed / failed) for a trader in a pool. Written by the pool's attester account (see risks).
3. `check(pool_id, trader, snapshot)` — pure view: given an equity/position snapshot, returns which rule (if any) is broken. Deterministic, reproducible by anyone.
4. `record_stop(pool_id, trader, rule, snapshot_hash)` — appends a stop event with the broken rule; accepted only if `check` on the submitted snapshot really returns that rule.
5. Views: `get_pool`, `get_trader`, `list_events(pool_id)` for the verify page.

Rule from #1986 kept: no exam → no seat in a multi-trader pool; an owner trades without an exam only in a single-owner pool. No first-loss / tranche logic (none exists in code).

## Minimal scope (solo, by 14.11)
- One NEAR contract + unit tests (`cargo test`) + workspaces/sandbox integration test.
- A seed script that deploys to NEAR testnet and creates one demo pool, one exam record, one stop.
- A static verify page (new, in a separate `near/` folder) that reads views via NEAR RPC.
- README section "NEAR HACKATHON" with run steps from a clean clone, link to the diff, link to the Signer entry.

## Reused vs new
- Reused (concept only): the `Rules` field set, the exam → seat model, "anyone can check a breach" idea, the verify-page UX.
- New: all NEAR code (`near/contract`, `near/scripts`, `near/verify`). No Signer code, no NEAR Intents, no changes to `lib/`, `gateway/`, KOLIZEY tests, or HyperEVM contracts. All commits after 19.10 on branch `near-hackathon`.

## Demo plan (< 5 min video, testnet)
1. 0:00–0:40 Problem: investor cannot verify a prop pool's rules.
2. 0:40–1:30 `create_pool` on NEAR testnet with rules; show tx in explorer.
3. 1:30–2:20 `record_exam` passed for trader A; failed trader B cannot get a seat.
4. 2:20–3:30 Snapshot breaking daily loss → `check` returns the rule → `record_stop`; a forged stop with a valid snapshot is rejected.
5. 3:30–4:30 Verify page: pool rules, exam record, stop reason, all read from chain.
6. 4:30–5:00 Repo, run steps, link to the Signer entry.

## Risks
- Trust in snapshots: in v1 the snapshot is submitted by an attester (our keeper account); the contract proves the rule math, not the HL data origin. Stated openly in README.
- Solo capacity alongside cp#185 and the next contacts PR.
- Reproducibility checks by AI tools: run steps must work from a clean clone.

## Unknowns
- NEAR testnet tx cost for the demo: unknown.
- Whether judges accept a static verify page without hosting: unknown (brief says local is acceptable with video).
- Mainnet NEAR payout account: created by Alex, not by us.
