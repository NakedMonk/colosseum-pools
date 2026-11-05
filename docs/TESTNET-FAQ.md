# Testnet FAQ

Short answers for outside testers of the pools on Hyperliquid's testnet, one per question, so each can
be passed on alone. The step-by-step pages are [TESTNET-TRADER.md](TESTNET-TRADER.md) for a trader
and [TESTNET-INVESTOR.md](TESTNET-INVESTOR.md) for a depositor; the site is
[pools.usenami.io](https://pools.usenami.io). The tokens are testnet tokens with no value, and the
contracts are not audited.

## How much does it cost? Is the money real?

It is testnet only, so no real money. A challenge on a seat of the shared pool costs its price plus a
0.70 fee that isn't refunded: 2.70, 4.70 or 6.70 test USDC on the three seats. A depositor needs at
least 50 test USDC plus 1 for the deposit's address. Contracts are unaudited.

## How do I get test USDC and HYPE?

Gas: our faucet sends 0.001 HYPE once a UTC day per address: `POST https://pools-api.usenami.io/demo3/v1/faucet`
with `{"address": "0x…"}` ([TESTNET-TRADER.md](TESTNET-TRADER.md), section 2). USDC: Hyperliquid's own
testnet faucet only pays addresses that deposited on mainnet, and it pays on HyperCore. If it doesn't
fund you, open an issue in this repository with your address and we send test USDC (10 to a trader, 60
to a depositor), in the order issues arrive and while our test budget lasts. Traders then move USDC to
HyperEVM with "Transfer to EVM" in the testnet app. Put a little HYPE on HyperCore spot first: the gas
for that transfer is paid from there, so it fails without it. Testnet, no real money, unaudited.

## How does a depositor get in?

Any EVM wallet on HyperEVM Testnet (chain id 998), a little HYPE for gas, and at least 50 + 1 test USDC
on HyperCore spot. On pools.usenami.io go to "Shared pool", press "Open a ticket and send", then "Run a
settlement point". The deposit turns into shares only after that point runs. Anyone can run one, you
included: once your transfer has landed, you need not wait for anyone ([TESTNET-INVESTOR.md](TESTNET-INVESTOR.md),
section 6). Testnet, no real money, unaudited.

## What does a depositor get? Is there profit?

You get shares in the shared pool. The pool earns the exam prices and its 20% of a funded trader's
profit, minus the seats' losses. It takes no fee on that. A deposit doesn't add seats: the three seats
are sealed. A share was worth 1.013459 test USDC on 5 November 2026. Test money, so no yield to speak
of. Testnet, no real money, unaudited.

## How does a trader take a seat? What are the rules?

Seats show up on the site under "Our own pools and shared-pool seats". Press "Pay and start", sign the
approval and the purchase, and trade from the challenge page once the account starts. The rules on all
three seats: a 10% target in 7 days, a 3% daily loss limit, a 6% drawdown, up to 5x leverage, and SOL,
BTC and ETH. Break a rule and the account is stopped. Hit the target with no position open and you
pass. A seat runs one challenge at a time. If the capital has not reached your account within an hour of
the purchase, anyone may abort it and your price comes back; once it has started, the price stays with the
pool ([TESTNET-TRADER.md](TESTNET-TRADER.md), section 4). Testnet, no real money, unaudited.

## What does a trader get for passing?

Pass the exam and you trade a funded stage of 100, 200 or 300 test USDC, by seat, for up to one day,
once a trading key is free. You keep 80% of its profit, the pool takes 20%. The exam itself pays no
share. All test tokens. We don't promise rewards, a token or priority: you get our thanks and a public
on-chain record. Testnet, no real money, unaudited.

## What if I lose?

A trader loses the price and the 0.70 fee (the fee is never refunded). Trading losses on the account
belong to the pool. Depositors carry that through their shares. Everything is test money. Testnet, no
real money, unaudited.

## How do I get out?

Depositors: "Ask to withdraw", allowed 10 minutes after the settlement point that counted your latest
deposit. You keep bearing the pool's
result until the next settlement point pays you at its price. You can't cancel a request. Money tied in
a running exam waits for it to end. Traders: a challenge ends by a pass, a broken rule, the 7-day
deadline or an early forfeit (next answer). Testnet, no real money, unaudited.

## Can I quit a challenge early?

Yes. Send forfeit from the wallet that bought the challenge. That wallet and a little test HYPE for gas
are all it takes: no exchange key, no gateway. Easiest is the site. Connect that wallet, open
pools.usenami.io/#/challenge/<challenge address> and press "Give up". From a terminal, with Foundry:

```bash
cast send <challenge address> "forfeit((uint32,uint64)[],uint32[],bytes32)" "[]" "[]" <32 random bytes, 0x and 64 hex digits> --rpc-url https://rpc.hyperliquid-testnet.xyz/evm --interactive
```

Or, if you trade through our client:

```bash
python -m agents.client --deployment demo2 --wallet <key name> forfeit <challenge address>
```

The empty lists are right if you have no open orders. Positions in the pool's assets are closed in the
same transaction anyway, and the site's button cancels open orders for you. A forfeit on 5 November
used 182,156 gas, about 0.00002 HYPE; the faucet's 0.001 HYPE covers that many times. The challenge
ends as Forfeited and your key is retired. Everything left on the challenge account, gain or loss, goes
back to the pool in one amount (on the shared pool, to its seat). That takes three settle calls anyone
can send; on 5 November our host's keepers sent all three within a minute. You get nothing back. The
exam price and the 0.70 fee stay paid, and a share is only owed on a pass. Testnet, no real money,
unaudited.

## Do you need my private key? Who holds the keys?

You never paste a private key. Your wallet signs requests. The keys that sign on the exchange belong to
our gateway in this demo, not to you. Contracts are unaudited, so please use a fresh test wallet. The
gateway puts a stop and a take-profit on the exchange before an order that can open a position.
Testnet, no real money, unaudited.

## Who are you? Was there an audit?

We are Usenami, building pools on Hyperliquid testnet. The investor's rules live in the contract and the
stop sits on the exchange. There is no external audit and the contracts are unaudited. Every wallet in
the public records so far is ours; you would be among the first outside testers. For support, open an
issue in this repository; there is no support email. Testnet, no real money, unaudited.

## Where is the code?

Here: contracts in `src/`, the gateway in `gateway/`, the site in `app/`. The tester guides are
[TESTNET-TRADER.md](TESTNET-TRADER.md) and [TESTNET-INVESTOR.md](TESTNET-INVESTOR.md). Testnet, no
real money, unaudited.

## Where can I see what happened on chain?

Two pages on the site. "What happened on chain" (`#/evidence`) lists the stops, passes and payouts with
their transactions, and a "Read the receipt" button checks each against the node. "Check it yourself"
(`#/verify`) reads any account. All wallets in those records are ours and some records were staged; the
page says so. The shared pool is `0xa2eEe2CF75d5f740E436a08007345dA5789a4499`. For HyperCore, Hyperliquid's
testnet app has an explorer, [app.hyperliquid-testnet.xyz/explorer](https://app.hyperliquid-testnet.xyz/explorer),
with an address's spot transfers and other exchange actions. For HyperEVM we know of no testnet explorer to
recommend: the explorers in Hyperliquid's docs show mainnet only, so use the two pages. Testnet, no real
money, unaudited.
