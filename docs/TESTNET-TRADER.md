# Testnet: take a challenge

Testnet only: the USDC and HYPE below are Hyperliquid testnet tokens, with no value. The contracts are
unaudited. The site at [pools.usenami.io](https://pools.usenami.io) lists the pools of the live deployment,
demo2 (`app/config.js`). This page is the trader's side of it; the investor's side is
[TESTNET-INVESTOR.md](TESTNET-INVESTOR.md).

A pool sells one challenge at a time. You pay its price, the pool puts its capital on a new account of yours
on Hyperliquid's exchange, and you trade that account under the pool's rules. Reach the profit target before
the deadline without breaking a rule, and you pass.

## 1. Wallet and network

- Any EVM wallet, on HyperEVM Testnet: chain id 998, RPC `https://rpc.hyperliquid-testnet.xyz/evm`, gas in
  HYPE. The site offers to add the network when you connect.
- The same address is your account on HyperCore, the exchange.

## 2. What you need

- **HYPE on HyperEVM**, a little, for gas: buying a challenge is one or two transactions, an approval when your
  wallet has not already approved enough USDC for that pool, and the purchase. Our faucet gives it:
  `POST https://pools-api.usenami.io/demo3/v1/faucet` with `{"address": "0x…"}` sends 0.001 HYPE to that address
  on HyperEVM, once a UTC day an address, three a network ([GATEWAY.md](GATEWAY.md), `gateway/faucet.py`). It
  sends 1.7 of demo3's own test dollar with it, which demo2 does not take; the HYPE is the chain's gas, so it
  pays here too. A purchase on demo2 took 729,916 gas on 19 Oct 2026, about 0.00007 HYPE at 0.1 gwei, and an
  approval about 47,000 more.
- **USDC on HyperEVM**, the exchange's testnet USDC token `0x2B3370eE501B4a559b57D449569354196457D8Ab`
  (6 decimals): the pool's price plus the platform's fee of 0.70 USDC, which is not refunded (`challengeFee()`
  on the demo2 factory, 700000).
- Hyperliquid's testnet faucet pays only addresses that have deposited on its mainnet, and it pays USDC on
  the exchange, HyperCore, not on HyperEVM (`spike/README.md`).
- **HYPE on HyperCore, a very little, to move USDC to HyperEVM.** The move ("transfer to EVM", a `sendAsset`)
  costs 200,000 gas at the next HyperEVM block's base price, and Hyperliquid takes it in HYPE from your
  HyperCore spot: 0.00002 HYPE at 0.1 gwei, as it took from our own move of 17 Sep 2026. A wallet with no
  HYPE there needs some first: buy a little on Hyperliquid's spot market, or send a little from HyperEVM to
  `0x2222222222222222222222222222222222222222`, which credits it to your HyperCore spot (Hyperliquid's docs:
  "HyperCore <> HyperEVM transfers", and the FAQ "Gas problem on EVM").
- Hyperliquid's testnet faucet does not fund every address. The faucet credits the exchange; move USDC to HyperEVM
  in the Hyperliquid testnet app (transfer to EVM). If the faucet does not fund your address, open an issue in this
  repository with your address, and we send test USDC.

## 3. Pick a pool

On the list, each pool shows its price, profit target, drawdown and funded capital, and its rules under
"Rules and terms". A pool that can sell now says "Open the pool". One that cannot says "See the pool", with the
reason: it is already running a challenge, or its capital is not all there yet.

The seats of the shared pool are pools too, listed under "Our own pools and shared-pool seats". Their capital
belongs to the shared pool's investors ([SHARED-POOL.md](SHARED-POOL.md)), and a seat is bought the same way.

## 4. Take the challenge

1. Open the pool and press "Pay and start". Your wallet asks to approve the price and the fee, unless it has
   already approved enough for that pool, then to buy (`buyChallenge`). The page says what you pay before you press.
2. The pool creates your challenge account and moves its capital there. The keeper starts the challenge once the
   capital has arrived; the account's page then shows it as Active, with its deadline.
3. A trading key is reserved for your account. You never hold it: the pool gateway does ([GATEWAY.md](GATEWAY.md)).

## 5. Trade

On your challenge's page (`#/challenge/<address>`), the trade panel takes an order: asset, side, size and limit
price, then "Sign and send". Your wallet signs the request (EIP-712); the gateway checks that the key is bound to
your account and to you, places a stop and a take on the exchange before an order that may open a position, and
signs the order for Hyperliquid.

The gateway takes at most 12 orders a minute from one network and 15 from everyone together, in bursts of up to
10 (`ops/host/nginx-pools-api.conf.in`); a cancel or a move of a stop or a take counts as one. Past that nginx
answers 429 and the request never reached the gateway: nothing was sent. Three kinds of answer are worth
trying again, since what failed may pass the next time and nothing was sent. A signed request lives at most a
minute (`expiresAt`, `gateway/checks.py`), so after the wait sign the order afresh, with a new nonce and
expiry, rather than sending the refused request again:
- a 429, or `"status": "busy"` (`rate_limited`, `gateway_restarting`, `upstream_busy`): after `Retry-After` or a
  minute;
- `protection_failed`: the stop and the take could not be put on Hyperliquid, so the order was not sent and its
  nonce comes back; a little later;
- `upstream_failed`: a read the order needed failed; a little later.

Every other answer with `"sent": false` is a refusal of this order, such as `at_rule_line`, `market_window` or
`agent_missing`: its `detail` says why, and the same request gets the same answer until that changes
([GATEWAY.md](GATEWAY.md), "Answers").

The rules are the pool's: a daily loss limit, a maximum drawdown from the starting capital, a leverage limit and a
list of assets. Break one and the keeper stops the account on chain with `breach`, which the contract accepts only
when the rule is broken; the challenge ends there.
Reach the target before the deadline with no position open, and the challenge passes; the pool may then fund you on
its funded capital.

## 6. Check it yourself

The state of the contracts and of the accounts above is on chain; who holds the trading keys, and the gateway's
signing, are not. "Check it yourself" on the site (`#/verify`) reads an account, and "What happened on chain"
(`#/evidence`) lists the records this testnet has so far, with their transactions.

## What this is not

- Not money: testnet tokens only.
- Not audited.
- In this demo the gateway holds the trading keys and signs with them. Your wallet signs your requests; the keys
  that sign on the exchange are the gateway's (`GATEWAY_SIGNER=demo`, [GATEWAY.md](GATEWAY.md)).
