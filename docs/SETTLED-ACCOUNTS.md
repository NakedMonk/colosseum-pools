# What is left on settled challenge accounts

Testnet only. A challenge account that has settled can still receive money afterwards: a fill that came late, a send
of its own that landed after the settlement, a transfer from anyone. `ChallengeAccount.sweep()`
(`src/ChallengeAccount.sol`) sends whatever is there back to the pool, and anyone may call it. Nothing calls it
today: not the keepers (`ops/keeper.py`, `ops/shared_keeper.py`), the gateway, the agent client or the site. This
page reads whether that leaves money behind.

## How it was read

`scripts/settled_accounts.py` only reads; it sends no transaction and needs no key. Every account a factory creates
is a clone made with CREATE, so the script finds them from the factory's address and nonce, for demo2 and demo3, and
marks the challenges of the shared pool's seats. For each challenge it reads its status and `returnAt` on HyperEVM,
its HYPE and USDC there, and on HyperCore its spot USDC, its perp account (and the pool's dex, on demo3) and its
ledger. The whole reading, at one block, is in [settled-accounts.json](settled-accounts.json); the table below is
written from it by the same script (`--md`).

When an account settled is not in its state, and its `Settled()` event could not be read: the HyperEVM testnet RPC
answers `eth_getLogs` over 50 blocks at most, times older ranges out, and does not serve state at past blocks. The
table gives instead the contract's `returnAt`, when the account first had something to hand back, and the last time
money moved out of it on HyperCore, from its ledger. A transfer into the account after that last move would be money
left behind: "In after it" adds those up.

## Result

Read at block 68886641, 2026-11-06T09:18:17Z:
- The eight settled accounts, all on demo2, hold nothing: 0 USDC on spot, 0 withdrawable on perp and no position,
  0 HYPE and 0 USDC on HyperEVM. Nothing came into any of them after its last move out. They include the challenge
  accounts of the shared pool's three seats.
- The one challenge on demo3 is still active, so it is not counted here.
- `sweep()` on a settled account with nothing on it costs 38,024 gas (`eth_estimateGas`, 6 November 2026). On an
  account that has not settled it reverts with `BadStatus`.

| Deployment | Pool | Challenge | Status | `returnAt` (UTC) | Last move out on HyperCore (UTC) | In after it (USDC) | Spot USDC | Perp withdrawable | HYPE on HyperEVM (wei) | USDC on HyperEVM |
|---|---|---|---|---|---|---|---|---|---|---|
| demo2 | `0x237afA2D58B1612e19D47152FfB2E771c05Fe96D` | `0xBA0c90BB481D6CAd2534AE3885d1CC9B9C30dB54` | Settled | 2026-10-01 13:55:02 | 2026-10-01 13:55:31 | 0 | 0 | main 0.0 | 0 | 0 |
| demo2 | `0xa6d07e9d7354f61b82173F0eBFcF4164EF742096` | `0xa571193573d068414a524Dcb3D134F685f1E022F` | Settled | 2026-10-04 04:04:42 | 2026-10-04 04:04:52 | 0 | 0 | main 0.0 | 0 | 0 |
| demo2 | `0x70066669eC7Eb992055c82B615eD822CFF14e44A` | `0xe3fEf7523607aEF26B3bfC06d414D59525b10dBB` | Settled | 2026-10-19 15:41:09 | 2026-10-19 15:41:10 | 0 | 0 | main 0.0 | 0 | 0 |
| demo2 | `0x7eb971134b0aD523f55C76B44B2fbD6Ed7885371` | `0xC9435A77Aba8bd9FCA244EeD6cE4Ff9727aE9262` | Settled | 2026-10-05 07:01:08 | 2026-10-05 07:01:44 | 0 | 0 | main 0.0 | 0 | 0 |
| demo2 | shared pool, seat 1 | `0x48f948a895F82a32fb39eE57C457c05FdA7A7779` | Settled | 2026-10-12 06:11:00 | 2026-10-12 06:11:37 | 0 | 0 | main 0.0 | 0 | 0 |
| demo2 | shared pool, seat 2 | `0x7aa03B872FAd788030456616f644344dEa209feE` | Settled | 2026-10-12 18:49:49 | 2026-10-12 18:50:25 | 0 | 0 | main 0.0 | 0 | 0 |
| demo2 | shared pool, seat 3 | `0x67de5E792c251384C1c6A80C1e7766E7CCF82ea0` | Settled | 2026-10-13 05:56:49 | 2026-10-13 05:57:02 | 0 | 0 | main 0.0 | 0 | 0 |
| demo2 | `0x70066669eC7Eb992055c82B615eD822CFF14e44A` | `0xb10c3574a915A9f41E62Ba9355E71C2f03c87288` | Settled | 2026-11-05 12:53:58 | 2026-11-05 12:54:08 | 0 | 0 | main 0.0 | 0 | 0 |
| demo3 | `0x69e88Dbe895AfbbD773CB60A09d1113777a6DfB0` | `0xa1e0dDA60928e385fd7d5317Df9F105200aa12F1` | Active | - | - | - | 0 | main 0.0, xyz 9.540986 | 0 | 0 |

## The weekly watch

`.github/workflows/settled-accounts-watch.yml` makes this reading every Monday at 06:41 UTC, and whenever it is run
by hand from the Actions tab (`workflow_dispatch`). It only reads: no key, no transaction, nothing written back to the
repository. The run goes red when an account in the Settled state holds 1 USDC or more, counting its spot USDC on
HyperCore, what its perp accounts would let it withdraw and its USDC on HyperEVM (`--red-above 1`: the script exits 2
and names each such account in the log), and when the reading itself fails, since a run that read nothing must not
look like a run that found nothing. Each run keeps the JSON and the table as an artifact for 90 days. A red run means
`sweep()` has something to send back from the account it names, and anyone may call it. A scheduled run on GitHub can
start hours late; one run by hand starts at once.
