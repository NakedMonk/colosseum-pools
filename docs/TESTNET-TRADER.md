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
  on HyperEVM, once a UTC day an address and three times a UTC day from one IP address
  ([GATEWAY.md](GATEWAY.md), `gateway/faucet.py`). It
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

The seats of the shared pools are pools too, listed under "Our own pools and shared-pool seats": the ten of the
current pool and the three of the earlier one. Their capital belongs to the shared pool's investors
([SHARED-POOL.md](SHARED-POOL.md)), each seat's page names its pool on the Investor line, and a seat is bought the
same way.

## 4. Take the challenge

1. Open the pool and press "Pay and start". Your wallet asks to approve the price and the fee, unless it has
   already approved enough for that pool, then to buy (`buyChallenge`). The page says what you pay before you press.
   The first time, the site asks you to tick "I understand this is a testnet demo"; with no wallet in the
   browser it says so and sends nothing.
2. The pool creates your challenge account and moves its capital there. The keeper starts the challenge once the
   capital has arrived; the account's page then shows it as Active, with its deadline.
3. A trading key is reserved for your account. You never hold it: the pool gateway does ([GATEWAY.md](GATEWAY.md)).
4. The deadline is the start plus the pool's `duration`: 7 days on every seat of both shared pools (`terms()`, 604800 s,
   read on 6 November 2026). Until the challenge starts, anyone may abort it
   ([`START_WINDOW`](https://github.com/namixai/colosseum-pools/blob/1a4146bf6848563938db07561b30fc9511d3631a/src/ChallengeAccount.sol#L34),
   [`abort()`](https://github.com/namixai/colosseum-pools/blob/1a4146bf6848563938db07561b30fc9511d3631a/src/ChallengeAccount.sol#L166-L177))
   in two cases. One: an hour after the purchase, if the capital still has not reached the account; capital that
   lands before the call makes `abort()` revert (`CapitalArrived`). Two: at any time, capital or not, if the trading
   key reserved for the account already exists on the exchange (`keySpoiled()`; `activate()` refuses such a key).
   Either way the price comes back to your wallet, the fee does not. Once the challenge has started, the price is the
   pool's ([`onChallengeStarted`](https://github.com/namixai/colosseum-pools/blob/1a4146bf6848563938db07561b30fc9511d3631a/src/Pool.sol#L243-L246))
   and does not come back, however the challenge ends.

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
For HyperCore, Hyperliquid's testnet app has an explorer, [app.hyperliquid-testnet.xyz/explorer](https://app.hyperliquid-testnet.xyz/explorer):
an address's spot transfers and other exchange actions. For HyperEVM we know of no testnet explorer to recommend;
the two pages above read the contracts.

## 7. Quit early

You can quit a challenge early, once it is Active. Send forfeit from the wallet that bought the challenge. That
wallet and a little test HYPE for gas are all it takes: no exchange key, no gateway. `forfeit()` refuses a challenge
still waiting for its capital (Created); section 4, item 4, says when that one can be aborted instead. Easiest is the
site. Connect that wallet, open `pools.usenami.io/#/challenge/<challenge address>` with your challenge's address in
place of `<challenge address>`, and press "Give up". From a terminal, with Foundry:

```bash
cast send <challenge address> "forfeit((uint32,uint64)[],uint32[],bytes32)" "[]" "[]" <32 random bytes, 0x and 64 hex digits> --rpc-url https://rpc.hyperliquid-testnet.xyz/evm --interactive
```

Or, if you trade through our client:

```bash
python -m agents.client --deployment demo2 --wallet <key name> forfeit <challenge address>
```

The empty lists are right if you have no open orders, and the site's button cancels open orders for you. Forfeit
does not close your positions in its own transaction: it sends a reduce-only IOC order for each open position in the
pool's assets (`closePositions` in `src/lib/CoreOps.sol`), and an IOC order may fill only in part, or not at all. A
forfeit on 5 November used 182,156 gas, about 0.00002 HYPE; the faucet's 0.001 HYPE covers that many times. The
challenge ends as Forfeited and your key is retired. Everything left on the challenge account, gain or loss, goes back
to the pool in one amount (on the shared pool, to its seat). That is `settle`, which anyone can send, repeated until
the challenge's `status` is Settled; a step that finds a position still open sends closing orders again and stops
there (`_drainStep` in `src/RuledAccount.sol`). On 5 November our host's keepers needed three calls, all within a
minute, but that was one case, not a rule. Settled does not mean every unit is home: a late fill, a transfer that did
not land, or money sent to the account afterwards stays on it until someone calls `sweep()` on it, which anyone may
do, as often as they like. A call that only moves money to spot says so with `MovedToSpot`;
call it again a block later to send that home (`sweep()` in `src/ChallengeAccount.sol`). You get nothing back. The
exam price and the 0.70 fee stay paid, and a share is only owed on a pass. Testnet, no real money, unaudited.

## What this is not

- Not money: testnet tokens only.
- Not audited.
- In this demo the gateway holds the trading keys and signs with them. Your wallet signs your requests; the keys
  that sign on the exchange are the gateway's (`GATEWAY_SIGNER=demo`, [GATEWAY.md](GATEWAY.md)).
