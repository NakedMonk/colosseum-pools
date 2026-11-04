// node --test app/tests/seriescard.test.mjs
// The list's card for pools of one owner that differ only in the buyer's choice (seriesCard in app/views/pools.js),
// against a single pool's card (poolCard). The view reads window.ethers on import, so the card functions are lifted out
// of the source and run here with the app's own helpers, a stand-in for chain's two formatters and for the document.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { esc, pct, duration, badge, row } from "../lib/ui.js";
import { poolStatus } from "../lib/stages.js";
import { totalPriceWords } from "../lib/outcomes.js";
import { capsByAsset } from "../lib/deployments.js";
import { KIND_NOTE, LIST_KEPT, cardBlockerLine } from "../lib/listing.js";
import { agentTradability, agentCardLine } from "../lib/agentcap.js";
import { seriesOf, seriesDataset } from "../lib/series.js";

const SOURCE = readFileSync(new URL("../views/pools.js", import.meta.url), "utf8");

/** A top-level function of the view, from `function name(` to its closing brace at the start of a line. */
function lift(name) {
  const start = SOURCE.indexOf(`function ${name}(`);
  assert.ok(start !== -1, `app/views/pools.js has no function ${name}: this test lifts it by that name`);
  return SOURCE.slice(start, SOURCE.indexOf("\n}\n", start) + 2);
}

// chain.js reads window.ethers when it loads; these are its two formatters as the cards use them.
const chain = {
  short: (addr) => (addr ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : "—"),
  usd6: (v) => (Number(v) / 1e6).toFixed(2),
};
const document = { createElement: (tag) => ({ tag, className: "", dataset: {}, innerHTML: "" }) };
const environment = { chain, document, esc, pct, duration, badge, row, poolStatus, totalPriceWords, capsByAsset, KIND_NOTE,
  LIST_KEPT, cardBlockerLine, agentTradability, agentCardLine, seriesDataset };
const lifted = ["keyCell", "termsHtml", "rulesHtml", "poolCard", "seriesCard"].map(lift).join("\n");
const { poolCard, seriesCard, keyCell } = new Function(...Object.keys(environment),
  `${lifted}\nreturn { poolCard, seriesCard, keyCell };`)(...Object.values(environment));

// What paint() does with each card of #pools, taken from its own line, so a change there is what is tested here.
const PAINT = SOURCE.match(/\$\("#pools", page\)\.replaceChildren\(\.\.\.seriesOf\(ordered\.filter\(\(i\) => i\.kind === "pool"\)\)\.map\((.+)\)\);/);
assert.ok(PAINT, "paint() fills #pools with seriesOf(...).map(...): this test reads that line");
const toCard = new Function("poolCard", "seriesCard", "fee", `return ${PAINT[1]};`);

const OWNER = "0x1111111111111111111111111111111111111111";
function pool(index, address, { price, targetBps, maxDrawdownBps, spotUsdc = 300 }) {
  return {
    index, address, owner: OWNER, kind: "pool", archived: false, deployment: { label: "demo2" }, caps: [],
    stage: 0, blocker: spotUsdc === null ? undefined : null, spotUsdc, assets: "BTC, ETH",
    terms: { price, capital: 100_000_000n, targetBps, duration: 2_592_000n, traderShareChallengeBps: 2000n,
             traderShareFundedBps: 8000n, fundedCapital: 200_000_000n },
    rules: { dailyLossBps: 500n, maxDrawdownBps, maxLeverageX100: 300n, assets: [3n, 4n] },
  };
}
const A = "0xA1b2000000000000000000000000000000003333";
const C = "0xC3d4000000000000000000000000000000004444";
const E = "0xE5f6000000000000000000000000000000005555";
const three = () => [
  pool(1, A, { price: 5_000_000n, targetBps: 1000n, maxDrawdownBps: 1000n }),
  pool(2, C, { price: 3_000_000n, targetBps: 800n, maxDrawdownBps: 800n }),
  pool(3, E, { price: 8_000_000n, targetBps: 1200n, maxDrawdownBps: 1200n, spotUsdc: null }),
];
const FEE = 1_000_000n;
const cards = (items, fee = FEE) => seriesOf(items).map((group) => toCard(poolCard, seriesCard, fee)(group));
/** The card's variant lines, in the order they are shown. */
const variantLines = (card) => card.innerHTML.split('<div class="pool-line series-variant">').slice(1);
const TOTAL = "You pay in total";

test("three pools of one owner that differ only in the buyer's choice are one card with a line for each", () => {
  const items = three();
  const [card, ...rest] = cards(items);
  assert.equal(rest.length, 0);
  assert.equal(card.className, "card pool-row series-row");
  // One line a variant, cheapest first, each to its own pool's page.
  const lines = [...card.innerHTML.matchAll(/<div class="pool-line series-variant">[\s\S]*?href="#\/pool\/(0x[0-9a-fA-F]{40})"/g)]
    .map((m) => m[1]);
  assert.deepEqual(lines, [C, A, E]);
  // A balance not read yet: the variant is shown, its spot as "…", and it is only seen, not opened.
  const last = variantLines(card).at(-1);
  assert.ok(last.includes(keyCell("HyperCore spot", "…")));
  assert.match(last, /See the pool/);
  // What the list's search, filters and sorting read is the variants' own cards, joined.
  const ordered = [items[1], items[0], items[2]];
  assert.deepEqual(card.dataset, seriesDataset(ordered.map((v) => ({ ...poolCard(v, FEE).dataset }))));
});

test("each variant says what its own buyer pays in all, and with no fee read no total is said", () => {
  const [card] = cards(three());
  const lines = variantLines(card);
  assert.equal(lines.length, 3);
  // Cheapest first: C at 3, A at 5, E at 8, each with the fee on its own price. The head the variants share has none.
  [3_000_000n, 5_000_000n, 8_000_000n].forEach((price, i) =>
    assert.ok(lines[i].includes(keyCell(TOTAL, esc(totalPriceWords(price, FEE)))), `variant ${i + 1}: its own total`));
  assert.equal(card.innerHTML.split(TOTAL).length - 1, 3);
  const [unpriced] = cards(three(), null);
  assert.ok(!unpriced.innerHTML.includes(TOTAL));
});

test("a variant whose balance could not be read says so, where one not read yet says …", () => {
  const items = three();
  items[2] = { ...items[2], unread: true };
  const [card] = cards(items);
  const last = variantLines(card).at(-1);
  assert.ok(last.includes(keyCell("HyperCore spot", esc(LIST_KEPT.unread))));
  assert.ok(!last.includes(keyCell("HyperCore spot", "…")));
  assert.match(last, /See the pool/);
});

test("a pool with no pair is the pool card it always was", () => {
  const lone = pool(4, "0x9999000000000000000000000000000000006666", { price: 4_000_000n, targetBps: 1000n,
    maxDrawdownBps: 1000n });
  const [card, ...rest] = cards([lone]);
  assert.equal(rest.length, 0);
  assert.equal(card.className, "card pool-row");
  assert.deepEqual(card, poolCard(lone, FEE));
});
