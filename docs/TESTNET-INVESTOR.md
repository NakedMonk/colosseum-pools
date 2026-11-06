# Testnet: your pool on demo3

Testnet only: the USDC and HYPE below are Hyperliquid testnet tokens. demo3 is the deployment of
factory v3, from commit `23772e2d`; its addresses are in `deployments/testnet-v3-demo3.json`.
Paths under `src/` below are at that commit
([tree](https://github.com/namixai/colosseum-pools/tree/23772e2dbf9f4913dc265efa68768030ab7d4d2a/src)):
`main` still holds the contracts from before v3.
A pool made by the demo3 factory's `createPool` has one investor, its owner. To put money in, you
create a pool of your own and fund it, and it sells challenges to traders on the rules you set.
The other way in is a share of the shared pool on demo2, which many investors hold together and the site
takes deposits into: section 6.

## 1. Wallet and network

- Any EVM wallet, on HyperEVM Testnet: chain id 998, RPC `https://rpc.hyperliquid-testnet.xyz/evm`,
  gas in HYPE (`app/config.js`). The same address is your account on HyperCore, the exchange.
- demo3 factory `0x070D0c7dd79D41e4329B2d231b308BA88089d81a`, dUSDC `0xa73D107dC507851CE4dD4A426e163e2F3368300D`.

## 2. What you need

- **HYPE on HyperEVM**, for gas: creating the pool, preparing its account and withdrawing are
  HyperEVM transactions. Our faucet sends 0.001 HYPE once a UTC day an address
  ([TESTNET-TRADER.md](TESTNET-TRADER.md), section 2).
- **USDC on HyperCore spot**, for the pool's capital. This is the exchange's own testnet USDC, not
  a HyperEVM token. Our faucet does not give it; we send it on request: open an issue in this
  repository with your address and the amount.
- **dUSDC only pays for a challenge.** A trader pays the price plus the platform's fee, 0.7 dUSDC
  (`challenge_fee` 700000, 6 decimals). Only our operator and our faucet wallet mint it
  (`src/demo/DemoUsdc.sol`). You do not need any.

## 3. Deposit: create and fund the pool

The site has no form for demo3 yet (section 5): any tool that sends a contract call will do, for
example Foundry's `cast`.

1. `createPool(rules, terms)` on the factory; you are the pool's owner. Units (`src/Types.sol`):
   price in dUSDC, 6 decimals; `capital` and `fundedCapital` in USD, 1e6 = 1 USDC. The factory
   refuses a daily loss or drawdown of 0 or of 10,000 bps and over, leverage under 100 (1x), no asset
   or more than 16, an asset it does not list (`isPlatformAsset`) or assets on two dexes, and a
   price, capital, funded capital, target or duration of 0 (`src/PoolFactory.sol`). Each asset's
   own cap, `assetLeverageX100`, is either empty or one value per asset, each from 100 (1x) to
   `maxLeverageX100`; anything else is refused too.
2. Send USDC from your HyperCore spot to the pool's address, as a spot transfer. A transfer from
   HyperEVM credits nothing to a contract (`app/lib/hlsend.js`). The pool needs `capitalNeeded()`:
   capital plus funded capital plus 1 USDC for the challenge's new account (`src/Pool.sol`). The
   first transfer to the new pool costs you 1 USDC more, for its account (`Units.NEW_ACCOUNT_FEE`).
   Our own demo3 pool, `0x69e88Dbe895AfbbD773CB60A09d1113777a6DfB0`, has capital 10 and funded
   capital 30, so `capitalNeeded()` is 41 USDC.
3. `prepareAccount()` on the pool, once the transfer has landed. Anyone may call it. From then on
   the pool sells a challenge whenever it is idle and holds `capitalNeeded()` on spot.

## 4. Withdraw

- `withdrawOnCore(amount1e8)`: the owner only, and only while the pool is idle (`stage()` 0). It
  sends spot USDC to your HyperCore address; 1e8 = 1 USDC. In any other stage nothing can be
  withdrawn: during a challenge `capital` is on the challenge's own account, and the rest stays on
  the pool's spot for the funded stage.
- `withdrawEarned()`: the challenge prices the pool has earned, in dUSDC.
- Meanwhile (`src/Pool.sol`, `src/ChallengeAccount.sol`): a sale moves `capital` to the challenge's
  account, and what is left at its end comes back, less the trader's share of the profit if they
  passed. A funded trader trades `fundedCapital` on the pool's own account; at the end the capital
  stays and only the trader's share of the profit leaves. A loss is the pool's.

## 5. What you see

- The site, pools.usenami.io, reads the demo2 and demo deployments only (`app/config.js`). A demo3
  pool's page there says "Not a pool".
- On HyperEVM, read the pool: `stage()` (0 idle, 1 challenge, 2 funded, 3 closing, 4 passed and
  waiting for a key), `capitalNeeded()`, `terms()`, `rules()`, `challenge()`, `earned()`.
- On HyperCore, its balance: `spotClearinghouseState` for the pool's address at
  `https://api.hyperliquid-testnet.xyz/info` (`app/lib/hl.js`).

## 6. Shares of the shared pool (demo2)

The site's "Shared pool" page (`#/shared`) takes deposits into one pool,
`0xa2eEe2CF75d5f740E436a08007345dA5789a4499` (`sharedPool` in `app/config.js`), and does each step below
from your wallet. How the pool values, counts and pays is in [SHARED-POOL.md](SHARED-POOL.md).

- **What you need.** USDC on your HyperCore spot: at least the pool's smallest deposit, 50 USDC
  (`minDeposit()`, 1e8 = 1 USDC), and 1 USDC more, which HyperCore charges for creating your deposit's
  address. HYPE on HyperEVM for gas (section 2): opening a deposit and asking to withdraw are HyperEVM
  transactions.
- **Deposit.** "Open a ticket and send": your wallet sends `openTicket()`, which gives this deposit an
  address of its own, then signs a spot transfer of the amount to that address on HyperCore. A ticket that
  holds less than the smallest deposit is not counted and stays open; the page offers to send to it again.
- **Shares.** A deposit becomes shares at the next settlement point, at the pool's value before it. Anyone
  can run one: "Run a settlement point" on the same page (`settle`). While `blocker()` names something, a
  point waits, and the page says what.
- **The seats are fixed.** The pool's seats were published, and the book sealed, before the first deposit
  (`seatsSealed()`); no deposit adds a seat. A deposit lies on the pool's spot as its free money and tops up
  a seat whose capital a challenge has used (`armSeat`, open to anyone). A share earns what the seats earn:
  the challenge prices, less their losses, and the pool's part of a funded stage's profit. This pool takes
  no fee on it (`feeBps()` is 0).
- **Withdraw.** "Ask to withdraw" (`requestRedeem`), from ten minutes after your latest deposit (`lock()`,
  600 s). The shares stay yours and keep bearing the pool's result until a settlement point pays them, at
  that point's price, and a request can't be taken back. A point pays from the pool's free money; while
  that is short, `releaseSeat` (open to anyone) brings an idle seat's capital back first. Capital in a
  running challenge or funded stage waits for its end.
- The platform's own starting shares never leave the pool.
