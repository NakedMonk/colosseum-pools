// node --test app/tests/seatfunded.test.mjs
//
// A shared pool's seat through its funded stage, as the three pages that show it read it: the seat's card on the
// shared page (seats in app/views/shared.js), the seat's own pool page (poolView, app/views/pool.js) and the page of
// the challenge its trader passed (challengeView, app/views/challenge.js); then the same seat once its funded term
// has been ended, the stage has settled and the pool has armed it again. The seat is one of the two 10 USDC seats of
// the pool of ten seats (docs/SHARED-POOL.md, 6 November): a 10% target in 7 days, a funded stage of 100 USDC for a
// day, nothing of a challenge's profit and 80% of a funded stage's to the trader.
//
// The views read window.ethers when they load, so, as seriescard.test.mjs does, their functions are lifted out of the
// source and run here with the app's own helpers, a stand-in for the chain reads and HyperCore's API, and a document
// that finds an element by its id in the markup the view has written. What is tested is what the pages say.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { CONFIG } from "../config.js";
import { esc, badge, row, when, duration, pct, render, settle } from "../lib/ui.js";
import { readAll } from "../lib/batch.js";
import { stageName, stageWords, isFundedStage, awaitingKeyWords, poolStatus } from "../lib/stages.js";
import { saleBlocker, topUpAdvice } from "../lib/funding.js";
import { outcomesHtml, totalPriceWords } from "../lib/outcomes.js";
import { poolKind, holdsCode, KIND_NOTE } from "../lib/listing.js";
import { agentTradability, agentNote, agentCardLine } from "../lib/agentcap.js";
import { liveDeployment, isArchive, ARCHIVE, capsByAsset } from "../lib/deployments.js";
import {
  usdcTotal1e8, allOf, withdrawPlan, withdrawOutcome, transferOf, hashesOf, ledgerCutoff, WITHDRAW_WAIT_MS,
  WITHDRAW_POLL_MS,
} from "../lib/withdraw.js";
import { usd, spot1e8 } from "../lib/shared.js";
import { ruleVerdict, liveReadingIsMoot, startSurplus, leverageOf } from "../lib/verdict.js";

// ── the views, lifted ────────────────────────────────────────────────────────────────

const FILES = {
  shared: "../views/shared.js", pool: "../views/pool.js", challenge: "../views/challenge.js",
  trading: "../views/trading.js", pools: "../views/pools.js", chain: "../lib/chain.js",
};
// A checkout with CRLF line ends reads as LF here, as brand.test.mjs does: lift() cuts at "\n}\n".
const TEXT = Object.fromEntries(Object.entries(FILES)
  .map(([key, path]) => [key, readFileSync(new URL(path, import.meta.url), "utf8").replace(/\r\n/g, "\n")]));

/** A top-level function of one of FILES, from `function` or `async function` to its closing brace at the start of a line. */
function lift(file, name) {
  const m = TEXT[file].match(new RegExp(`^(?:export )?((?:async )?function ${name}\\()`, "m"));
  assert.ok(m, `app/${FILES[file].slice(3)} has no top-level function ${name}: this test lifts it by that name`);
  const start = m.index + m[0].length - m[1].length;
  return TEXT[file].slice(start, TEXT[file].indexOf("\n}\n", start) + 2);
}

/** A one-line `export const` of one of FILES, as its source writes it. */
function constant(file, name) {
  const m = TEXT[file].match(new RegExp(`^export const ${name} = (.+);$`, "m"));
  assert.ok(m, `app/${FILES[file].slice(3)} has no one-line constant ${name}`);
  return new Function(`return ${m[1]};`)();
}

// chain.js reads window.ethers when it loads: its formatters and its names for the contracts' enums come from its source.
const { units, short, same } = new Function(
  `${["units", "short", "same"].map((name) => lift("chain", name)).join("\n")}\nreturn { units, short, same };`)();
const STATUS = constant("chain", "STATUS");
const BREACH = constant("chain", "BREACH");
const TESTNET_LINE = constant("pools", "TESTNET_LINE");

const LIFTED = [
  ["shared", "spotOf"], ["shared", "seats"],
  ["pools", "assetNames"], ["pools", "rulesAndTerms"], ["pools", "keyCell"], ["pools", "termsHtml"], ["pools", "rulesHtml"],
  ["trading", "equityPanel"], ["trading", "stopInputs"],
  ["pool", "readPoolSpot"], ["pool", "wireFunding"], ["pool", "poolView"],
  ["challenge", "challengeView"],
].map(([file, name]) => lift(file, name)).join("\n");

// ── the chain and HyperCore at one moment of the seat's life ─────────────────────────

const ZERO = "0x0000000000000000000000000000000000000000";
const SHARED = "0x5ba7000000000000000000000000000000000001";
const SEAT = "0x5ea7000000000000000000000000000000000002";
const CHALLENGE = "0xc4a1000000000000000000000000000000000003";
const TRADER = "0x7ade000000000000000000000000000000000004";
const LIVE = liveDeployment();

// A 10 USDC seat of the pool of ten seats, as the chain returns its rules and terms.
const TERMS = { price: 200_000n, capital: 10_000_000n, targetBps: 1000n, duration: 604_800n, traderShareChallengeBps: 0n,
  traderShareFundedBps: 8000n, fundedCapital: 100_000_000n };
const RULES = { dailyLossBps: 300n, maxDrawdownBps: 600n, maxLeverageX100: 500n, assets: [5n, 3n, 4n] };
const PERPS = { 5: "SOL", 3: "BTC", 4: "ETH" };
const FUNDED_TERM = 86_400n;
const CHALLENGE_FEE = 700_000n;
// The challenge was bought at 18:32:47 and started at 18:33:17 on 7 November 2026; its 7 days end at 18:33:17 on the 14th.
const CREATED = 1_794_076_367n;
const DEADLINE = 1_794_681_197n;

/**
 * The reads the three pages make, answered for one moment: the seat's stage, the challenge it still names (until the
 * challenge settles, Pool.onChallengeSettled), that challenge's status, and what HyperCore holds for each account.
 */
function world({ stage, challenge = ZERO, status = 2n, seatSpot, seatEquity = "0.0", challengeEquity = "0.0" }) {
  const seat = {
    target: SEAT,
    rules: async () => RULES, terms: async () => TERMS, stage: async () => stage, owner: async () => SHARED,
    accountReady: async () => true, challenge: async () => challenge,
    fundedTrader: async () => (isFundedStage(stage) ? TRADER : ZERO), earned: async () => 200_000n,
    capitalNeeded: async () => 11_100_000_000n, fundedEndReason: async () => 0n,
    // The stage opened on 100.00: that is where its drawdown and its day count from.
    drawdownBase: async () => 100_000_000n, dayStartEquity: async () => 100_000_000n, violation: async () => 0n,
    startSurplus: async () => 0n,
  };
  const passed = {
    target: CHALLENGE,
    status: async () => status, breachReason: async () => 0n, trader: async () => TRADER, pool: async () => SEAT,
    rules: async () => RULES, terms: async () => TERMS, createdAt: async () => CREATED, deadline: async () => DEADLINE,
    // The pass cut the challenge's key (ChallengeAccount.graduate, _cutAgent): the funded stage trades on a new one.
    agentKey: async () => (Number(status) === 2 ? "0xa6e7000000000000000000000000000000000005" : ZERO),
    payoutOwed: async () => 0n, payoutSent: async () => 0n, capitalArrived: async () => true,
    keySpoiled: async () => false, drawdownBase: async () => 10_000_000n, dayStartEquity: async () => 10_000_000n,
    violation: async () => 0n, startSurplus: async () => 0n,
  };
  const contracts = { [`pool ${SEAT}`]: seat, [`challenge ${CHALLENGE}`]: passed };
  const spot = { [SEAT]: seatSpot };
  const equity = { [SEAT]: seatEquity, [CHALLENGE]: challengeEquity };
  const chain = {
    contract: (kind, address) => {
      const c = contracts[`${kind} ${address}`];
      assert.ok(c, `no ${kind} at ${address} in this test`);
      return c;
    },
    deploymentOf: async () => LIVE,
    currentAddress: () => null, // a visitor without a wallet: neither the seat's owner nor its trader
    same, short, STATUS, BREACH, readAll,
    usd6: (v, shown = 2) => units(v, 6, shown),
    usd8: (v, shown = 2) => units(v, 8, shown),
    factory: () => ({ challengeFee: async () => CHALLENGE_FEE }),
    // The seat's owner is the shared pool, a contract: that is what makes the pool page call it a seat.
    readProvider: { getCode: async (address) => (same(address, SHARED) ? "0x6080" : "0x") },
    assetCaps: async () => [],
  };
  const hl = {
    spot: async (address) => ({ balances: [{ coin: "USDC", total: spot[address] ?? "0.0" }] }),
    spotUsdc: async (address) => Number(spot[address] ?? 0),
    account: async (address) => ({ marginSummary: { accountValue: equity[address] ?? "0.0", totalNtlPos: "0.0" } }),
    positions: async () => [],
    perp: async (index) => ({ index: Number(index), name: PERPS[Number(index)] }),
  };
  return { chain, hl, shared: { fundedTerm: async (address) => (same(address, SEAT) ? FUNDED_TERM : 0n) } };
}

/** A document that knows an element by the id it has in the markup written so far, page and panels alike. */
function documentFor() {
  const page = { innerHTML: "" };
  const elements = new Map();
  const markup = () => [page.innerHTML, ...[...elements.values()].map((e) => e.innerHTML)].join("\n");
  const $ = (selector) => {
    const id = selector.replace(/^#/, "");
    if (!markup().includes(`id="${id}"`)) return null;
    if (!elements.has(id)) {
      elements.set(id, { id, innerHTML: "", textContent: "", removed: false, remove() { this.removed = true; } });
    }
    return elements.get(id);
  };
  const failures = () => [...elements.values()].map((e) => e.textContent).filter((t) => t.startsWith("Could not load"));
  return { page, $, el: (id) => elements.get(id), failures };
}

/** The three pages over one moment of the chain, each opened as a visitor opens it. */
function pages(moment) {
  const { chain, hl, shared } = world(moment);
  const doc = documentFor();
  const pending = [];
  const wired = [];
  const environment = {
    chain, hl, CONFIG, ZERO, TESTNET_LINE, LEDGER_LOOKBACK_MS: 10 * 60_000,
    esc, badge, row, when, duration, pct, render, $: doc.$,
    wire: (button) => { if (button) wired.push(button.id); },
    // A panel the view leaves loading in the background is waited for before anything is read.
    settle: (promise, box) => { const done = settle(promise, box); pending.push(done); return done; },
    stageName, nameOf: stageName, isFundedStage, awaitingKeyWords, poolStatus, saleBlocker, topUpAdvice, outcomesHtml,
    totalPriceWords, poolKind, holdsCode, KIND_NOTE, agentTradability, agentNote, agentCardLine, isArchive, ARCHIVE,
    capsByAsset, usd, spot1e8, ruleVerdict, liveReadingIsMoot, startSurplus, leverageOf,
    usdcTotal1e8, allOf, withdrawPlan, withdrawOutcome, transferOf, hashesOf, ledgerCutoff, WITHDRAW_WAIT_MS,
    WITHDRAW_POLL_MS,
    tradePanel: async () => { throw new Error("a visitor gets no trade panel"); },
    sendUsdc: async () => { throw new Error("nothing is sent in this test"); },
    window: { ethers: { getAddress: (address) => address } },
  };
  const views = new Function(...Object.keys(environment),
    `${LIFTED}\nreturn { seats, poolView, challengeView, keyCell };`)(...Object.values(environment));
  const drained = async () => { while (pending.length) await pending.shift(); };
  return {
    keyCell: views.keyCell,
    async seatCard() {
      const box = { innerHTML: "" };
      await views.seats(box, shared, [SEAT]);
      return box.innerHTML;
    },
    async poolPage() {
      await views.poolView(SEAT, doc.page);
      await drained();
      return { ...doc, wired };
    },
    async challengePage() {
      await views.challengeView(CHALLENGE, doc.page);
      await drained();
      return { ...doc, wired };
    },
  };
}

const CHALLENGE_LINK = `<a href="#/challenge/${CHALLENGE}">${short(CHALLENGE)}</a>`;
const kv = (label) => new RegExp(`<span>${label.replace(/[()]/g, "\\$&")}</span>`);

// ── the funded stage ─────────────────────────────────────────────────────────────────

test("the seat's card on the shared page through the pass: Active until the deadline, then Funded with the day's term, 100.00, 0% and 80%", async () => {
  // Before the pass: the challenge runs, and the card says until when.
  const running = await pages({ stage: 1n, challenge: CHALLENGE, status: 2n, seatSpot: "100.0" }).seatCard();
  assert.ok(running.includes(badge("Challenge", "")));
  assert.ok(running.includes(row("Challenge", `${CHALLENGE_LINK}, Active, until 2026-11-14 18:33 UTC`)));

  // The pass has opened the funded stage, and the passed challenge has not settled yet, so the seat still names it.
  // The funded capital went to the perp side (Pool.openFundedStage), and nothing is left on spot.
  const passed = await pages({ stage: 2n, challenge: CHALLENGE, status: 6n, seatSpot: "0.0", seatEquity: "100.0" }).seatCard();
  assert.ok(passed.includes(`<h3><span class="mono">${short(SEAT)}</span> ${badge("Funded", "")}</h3>`));
  // Passed: nothing runs until the deadline any more, though the deadline stays on the chain.
  assert.ok(passed.includes(row("Challenge", `${CHALLENGE_LINK}, Passed`)));
  assert.doesNotMatch(passed, /until/);
  assert.ok(passed.includes(row("On HyperCore spot", "0.00 USDC")));

  // The challenge has settled: its 11.00 came back to the seat's spot, and the seat no longer names a challenge.
  const settled = await pages({ stage: 2n, seatSpot: "11.0", seatEquity: "100.0" }).seatCard();
  assert.doesNotMatch(settled, kv("Challenge"));
  assert.ok(settled.includes(row("On HyperCore spot", "11.00 USDC")));

  for (const card of [passed, settled]) {
    assert.ok(card.includes(badge("Funded", "")));
    // The pool of ten seats publishes a day's funded stage for every seat.
    assert.ok(card.includes(row("Longest funded stage", "1 d")));
    assert.ok(card.includes(row("Funded capital after passing", "100.00 USDC")));
    assert.ok(card.includes(row("Trader's share of the challenge profit", "0%")));
    assert.ok(card.includes(row("Trader's share of the funded profit", "80%")));
    assert.ok(card.includes(`<a href="#/pool/${SEAT}">The seat's pool page →</a>`));
  }
});

test("the seat's pool page on its funded stage: Funded, its trader, the stage's equity on HyperCore, and no end button for a visitor", async () => {
  const p = pages({ stage: 2n, seatSpot: "11.0", seatEquity: "100.0" });
  const page = await p.poolPage();
  const head = page.page.innerHTML;
  assert.ok(head.includes(`<h2>Pool ${short(SEAT)} ${badge("Funded", "")}</h2>`));
  assert.ok(head.includes(p.keyCell("Funded capital after passing", "100.00 USDC")));
  assert.ok(head.includes(KIND_NOTE.seat));
  assert.ok(head.includes(row("Investor", `<span class="mono">${SHARED}</span>`)));
  assert.ok(head.includes(row("Funded trader", `<span class="mono">${TRADER}</span>`)));
  assert.ok(head.includes(row("What the pool is doing", esc(stageWords(2)))));
  // The challenge row is for a challenge on the seat; on the funded stage there is none to link.
  assert.doesNotMatch(head, kv("Current challenge"));
  // The terms the trader was funded on: 100.00, and 0% and 80% of the profit.
  assert.ok(head.includes(row("Funded capital after passing", "100.00 USDC")));
  assert.ok(head.includes(row("Trader's share of the challenge profit", "0%")));
  assert.ok(head.includes(row("Trader's share of the funded profit", "80%")));

  assert.match(page.el("buy").innerHTML, /The pool is taken \(Funded\)\./);
  assert.doesNotMatch(page.el("buy").innerHTML, /id="buy-btn"/);

  // The stage itself, as HyperCore holds it: 100.00 against a floor 6% under it and a day's floor 3% under it.
  assert.equal(page.el("funded").removed, false);
  const equity = page.el("equity").innerHTML;
  assert.ok(equity.includes(row("Equity (HyperCore)", "100.00 USDC")));
  assert.match(equity, /<span>Drawdown floor<\/span><span>94\.00 USDC\s*<\/span>/);
  assert.match(equity, /<span>Today&#39;s floor<\/span><span>97\.00 USDC\s*<\/span>/);
  assert.ok(equity.includes(row("Positions", "none")));
  assert.ok(equity.includes(row("Contract's verdict", badge("inside the rules", "ok"))));

  // Anyone may stop a broken rule and take the day's snapshot. Ending the stage is the seat's owner's -- the shared
  // pool, a contract -- or its trader's; a visitor is neither, and the page offers it no such button.
  const actions = page.el("funded-actions").innerHTML;
  assert.match(actions, /<button id="breach">Stop: a rule is broken<\/button>/);
  assert.match(actions, /<button id="checkpoint" class="secondary">Take today's snapshot<\/button>/);
  assert.doesNotMatch(actions, /id="stop"/);
  assert.equal(page.el("investor").removed, true);
  assert.deepEqual(page.failures(), []);
});

test("the passed challenge's page: Passed, the 11.00 target and the deadline, a 0.00 share at 0%, then Settled with nothing left to do", async () => {
  const page = await pages({ stage: 2n, challenge: CHALLENGE, status: 6n, seatSpot: "0.0", seatEquity: "100.0",
    challengeEquity: "11.0" }).challengePage();
  const head = page.page.innerHTML;
  assert.ok(head.includes(`<h2>Challenge ${short(CHALLENGE)} ${badge("Passed", "ok")}</h2>`));
  assert.ok(head.includes(row("Trader", `<span class="mono">${TRADER}</span>`)));
  assert.ok(head.includes(row("Pool", `<a href="#/pool/${SEAT}">${short(SEAT)}</a>`)));
  assert.ok(head.includes(row("Capital", "10.00 USDC, target 11.00 USDC (+10%)")));
  assert.ok(head.includes(row("Created", "2026-11-07 18:32 UTC")));
  assert.ok(head.includes(row("Deadline", "2026-11-14 18:33 UTC")));
  assert.ok(head.includes(row("Agent key", "none (cut)")));
  assert.ok(head.includes(row("Trader's share", "0.00 USDC owed, 0.00 sent")));

  // The account met its target and stopped trading: no stop recorded, and the figures are said as what is left.
  const equity = page.el("equity").innerHTML;
  assert.ok(equity.includes(row("Equity (HyperCore)", "11.00 USDC")));
  assert.match(equity, /<span>Rules<\/span><span><span class="badge ok">none broken<\/span>/);
  assert.equal(page.el("trade").removed, true);
  // Its capital still has to go back to the seat: settling is what is left, and passing or stopping is not.
  const actions = page.el("actions").innerHTML;
  assert.match(actions, /<button id="settle">Settle one step<\/button>/);
  assert.doesNotMatch(actions, /id="graduate"|id="breach"|id="expire"|id="checkpoint"/);
  assert.deepEqual(page.failures(), []);

  const done = await pages({ stage: 2n, status: 8n, seatSpot: "11.0", seatEquity: "100.0" }).challengePage();
  assert.ok(done.page.innerHTML.includes(`<h2>Challenge ${short(CHALLENGE)} ${badge("Settled", "ok")}</h2>`));
  assert.ok(done.el("equity").innerHTML.includes(row("Equity (HyperCore)", "0.00 USDC")));
  assert.equal(done.el("actions").innerHTML, `<p class="muted">Nothing left to do.</p>`);
  assert.deepEqual(done.failures(), []);
});

// ── after the funded stage ───────────────────────────────────────────────────────────

test("the seat after its funded term is ended and the pool arms it: Idle, 111.00 on HyperCore, no challenge, and a challenge for sale", async () => {
  // endFundedTerm stopped the stage, settleFunded brought it to Idle, and armSeat topped the seat's spot up to what a
  // sale needs: 10 for the challenge, 100 for its funded stage, 1 for creating the challenge's account.
  const moment = { stage: 0n, seatSpot: "111.0" };
  const card = await pages(moment).seatCard();
  assert.ok(card.includes(`<h3><span class="mono">${short(SEAT)}</span> ${badge("Idle", "ok")}</h3>`));
  assert.ok(card.includes(row("On HyperCore spot", "111.00 USDC")));
  assert.doesNotMatch(card, kv("Challenge"));
  assert.ok(card.includes(row("Longest funded stage", "1 d")));

  const p = pages(moment);
  const page = await p.poolPage();
  const head = page.page.innerHTML;
  assert.ok(head.includes(`<h2>Pool ${short(SEAT)} ${badge("Idle", "ok")}</h2>`));
  assert.ok(head.includes(p.keyCell("HyperCore spot", "111.00 USDC")));
  assert.ok(head.includes(row("HyperCore spot", "111.00 USDC (needs 111.00 to sell a challenge)")));
  assert.ok(head.includes(row("What the pool is doing", "Idle: it can sell a challenge.")));
  assert.doesNotMatch(head, kv("Funded trader"));
  assert.doesNotMatch(head, kv("Current challenge"));
  assert.doesNotMatch(head, /short/);
  assert.match(page.el("buy").innerHTML, /<button id="buy-btn">Pay and start<\/button>/);
  assert.ok(page.wired.includes("buy-btn"));
  assert.equal(page.el("funded").removed, true);
  assert.deepEqual(page.failures(), []);
});
