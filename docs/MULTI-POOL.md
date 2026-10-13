# Design: a pool of many traders

**Status: built and tested offline. Not deployed on any chain, and not yet read by the audit.**
The pools on the testnet are the ones in [DESIGN.md](DESIGN.md) and
[SHARED-POOL.md](SHARED-POOL.md). This document describes `src/multi/MultiPool.sol` and the two
additions to `src/Pool.sol` it needs; nothing here exists on chain.

## What it is

One owner's capital and many traders. The owner sets a plan at creation: a menu of sizes with
their prices, the exam's rules, the funded account's rules, what the trader's share is, what
happens at the funded account's target, and how much of the pool may be in work at once
([POOL-PARAMETERS.md](POOL-PARAMETERS.md)). The plan cannot be changed afterwards.

A trader buys an exam off the menu. The exam is a paper exam ([PAPER-EXAM.md](PAPER-EXAM.md)):
it is traded on nobody's money, so the pool's capital is not touched by anyone who has not
passed. A trader who passes takes a funded account of the size they bought, on the pool's
capital, by their own call. One who does not gives the size back.

## A size is spoken for from the sale

The size of an exam counts as in work from the moment it is bought, although no money moves:
nothing else is sold against it, so the account is there for the trader who passes.

Two limits hold a sale back.

- **The share.** The sizes spoken for may not pass the plan's share of the pool's value: 80% of
  it by default, the rest being the pool's buffer. With a pool of 100 and two seats of 25 sold,
  30 are left: six seats of 5, three of 10 or one of 25.
- **The money.** A passing trader's account is paid from the pool's balance on HyperCore, so
  the size of every exam still running, and the 1 USDC a seat's new account may cost, has to be
  on that balance. Value that sits elsewhere, in a funded seat or as prices paid on HyperEVM,
  does not sell a seat.

Both are counted in the contract's own storage. A balance read from HyperCore is the start of
the block, so two calls in one block would read the same one; a count in storage they do not.
What the pool has sent in the last minute is counted against its balance for that minute,
whether or not it has left it yet. `room(line)` says how many more exams of a size the pool
would sell now, by all three limits: the share, the money and the number of seats.

An exam that ended without a pass, or whose pass nobody took within the plan's window, gives
its size back: `release(exam)`, by anyone.

## From the pass to the account

- `claim(exam)`, by the exam's trader, once it has passed and within the window. The pool picks
  a seat of that size, sends it the money, and from then on the money is the seat's.
- `open(seat)`, by anyone, once the money has arrived: a few seconds later. It starts the funded
  account for that trader. If the money did not arrive, `rearm(seat)` sends what is missing
  after a minute.

A seat is a `Pool` the MultiPool owns and runs. For that, `Pool` gained one switch:

- `runByOwner(traderMayStop)`, once: the pool sells no challenge of its own from then on;
- `openFundedFor(trader)`, by the owner of such a pool: a funded stage for a trader examined
  elsewhere, in full or not at all;
- on such a pool the trader ends the stage themselves only where the owner said so.

Everything after the opening is the funded stage every pool has: its rules, a breach anyone may
call, the settlement and the trader's share. Pools that nobody hands to their owner behave as
before.

## At the target

The plan says one of three things.

- **The trader stops.** There is no target. The trader ends the stage when they choose and is
  paid their share of what it made.
- **The account closes.** `hitTarget(seat)`, by anyone, ends the stage once equity has reached
  the target with nothing open and no rule broken. The trader is paid their share and the seat
  is free again. Before the target the trader cannot end the stage themselves, so a small profit
  cannot be cashed by stopping early.
- **The account stays.** The same, and after the settlement the seat waits for its trader, who
  may take it again without another exam (`reopen(seat)`) within the plan's window.

A breach ends the account in every mode, and its trader is owed nothing.

`finish(seat)`, by anyone, closes the books on a settled stage: the seat's money goes back to
the pool and its size is no longer in work. A freed seat rests for a minute before it serves
again, so that what it sent back has left it.

## The term

A pool runs for a term the plan sets, 6 to 24 months, and then stops.

- **The last fourteen days.** No exam is sold in them: an exam bought then could not lead to
  an account worth having. Exams already running go on, and a trader who passes can still take
  their account. `closing()` says the pool is in this stretch, and `endsAt()` when it stops;
  that is what a notice to traders is written from.
- **Extending.** The owner may move the end further on, by 6 to 24 months counted from the old
  end, at any time before the pool has stopped. Sales resume at once. What stays in the pool
  for the new term is what the owner leaves in it: with 100 put in and 115 there at the end of
  a term, they extend and withdraw the 15, under the same two conditions as any withdrawal.
- **The end.** After `endsAt` nothing is sold, taken or opened. `endSeat(seat)`, by anyone,
  ends each funded stage without a breach, so its trader is paid their share of what it made,
  target or no target. Every size comes back: an exam still running or passed and not taken is
  released, a seat whose money was sent and never opened is emptied, and a seat waiting for its
  trader stops waiting. With nothing in work, all of the pool is the owner's to withdraw.

A pool that has stopped cannot be extended.

## The owner's money

The owner puts capital in by sending USDC to the pool's address on HyperCore, and takes it out
with `withdraw`. What stays has to keep the sizes spoken for inside the plan's share, and the
money promised to running exams where it is. Prices paid for exams are the pool's, on HyperEVM,
and leave with `withdrawEvm` on the same condition.

The owner is a single address. That is an investor on their own. A pool of several investors is
a contract in that place, holding their shares; it is not written yet.

## What anyone can check

The plan is public from the first block. Every sale, pass, claim and freed seat is an event of
this contract or of the exam and the seat, and `value()`, `inWork()` and `room()` can be called
by anyone. Whether a trader passed is the exam's own record, not this contract's and not ours.

## Weak points we know of

- **A minute of caution.** What the pool sent is counted against its balance for a minute even
  after it has left it. A pool that sends something every minute keeps counting, and can refuse
  a withdrawal or a sale it could have afforded. It errs on the side of the trader who was
  promised an account.
- **A seat needs a caller.** `open`, `finish`, `release` and `hitTarget` are anyone's to call,
  and somebody has to. A keeper for this pool is not written.
- **An exam with neither a deadline nor an idle limit** holds its size until it passes, breaks
  a rule, or the pool stops.
- **The exam's price can be small and the account real.** A trader who passes by luck trades the
  pool's capital. What bounds the loss is the funded account's drawdown line and its stop on the
  exchange, as on every pool.
- **The plan is fixed.** A mistake in it is a new pool.

## Tests

`test/multi/`, 49 tests on the hyper-evm-lib simulator: 11 for the pool handed to its owner, 38
for the pool of many traders. 180 mutations (`Y1`–`Y180` in `scripts/mutations.py`), each
caught by a named test:

```
forge test --match-path 'test/multi/*'
python3 scripts/mutations.py $(seq -f 'Y%g' 1 180)
```

## Not done yet

- A pool of several investors, and with it the rule for who stays and who leaves when a term
  is extended.
- The notice itself: the contract says when the pool stops; telling traders is not written.
- A keeper, the app's pages, a deployment script, a run on the testnet, and the audit's reading.
