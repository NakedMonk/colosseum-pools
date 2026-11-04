// node --test app/tests/series.test.mjs
// Pools of one owner that differ only in the buyer's choice show as one card with a button for each (app/lib/series.js).
import { test } from "node:test";
import assert from "node:assert/strict";
import { seriesOf, seriesDataset, shared, MAX_VARIANTS, LIST } from "../lib/series.js";

const OWNER = "0x00d014dF2b4Ffdb0654ea079e4792fd15a350Fd4";
const LIVE = { label: "demo2", role: "live" };

/** A pool as the list holds it (app/views/pools.js, readPoolChain): the chain's terms and rules, BigInt as ethers
 *  gives them. */
function pool(index, over = {}) {
  const { terms = {}, rules = {}, ...rest } = over;
  return {
    index, address: `0x${String(index).padStart(40, "0")}`, owner: OWNER, kind: "pool", archived: false,
    deployment: LIVE, caps: [],
    terms: { price: 45_000_000n, capital: 5_000_000_000n, targetBps: 1000n, duration: 7_776_000n,
             traderShareChallengeBps: 0n, traderShareFundedBps: 8000n, fundedCapital: 5_000_000_000n, ...terms },
    rules: { dailyLossBps: 300n, maxDrawdownBps: 600n, maxLeverageX100: 1000n, assets: [3n, 4n], ...rules },
    ...rest,
  };
}
// The three of Breakout's $5K exam the word of 2 Nov 2026 named: $45 at 6% / 10%, $33 at 5% / 12%, $20 at 3% / 9%.
const wide = () => pool(1, { terms: { price: 45_000_000n, targetBps: 1000n }, rules: { maxDrawdownBps: 600n } });
const middle = () => pool(2, { terms: { price: 33_000_000n, targetBps: 1200n }, rules: { maxDrawdownBps: 500n } });
const narrow = () => pool(3, { terms: { price: 20_000_000n, targetBps: 900n }, rules: { maxDrawdownBps: 300n } });

test("three pools of one owner that differ only in price, target and drawdown are one card, cheapest first", () => {
  const [a, b, c] = [wide(), middle(), narrow()];
  const cards = seriesOf([a, b, c]);
  assert.equal(cards.length, 1);
  assert.deepEqual(cards[0].variants, [c, b, a]);
});

test("a series stands where its first pool stood, and a pool on its own keeps its own card", () => {
  const other = pool(9, { owner: "0x1111111111111111111111111111111111111111" });
  const [a, b] = [wide(), middle()];
  const cards = seriesOf([other, a, pool(8, { owner: "0x2222222222222222222222222222222222222222" }), b]);
  assert.deepEqual(cards.map((c) => c.variants.map((v) => v.index)), [[9], [2, 1], [8]]);
});

test("a difference in anything but the buyer's choice keeps the pools apart", () => {
  const changes = {
    owner: { owner: "0x3333333333333333333333333333333333333333" },
    deployment: { deployment: { label: "demo3", role: "live" } },
    capital: { terms: { capital: 10_000_000_000n } },
    fundedCapital: { terms: { fundedCapital: 10_000_000_000n } },
    duration: { terms: { duration: 2_592_000n } },
    traderShareChallengeBps: { terms: { traderShareChallengeBps: 1000n } },
    traderShareFundedBps: { terms: { traderShareFundedBps: 7000n } },
    dailyLossBps: { rules: { dailyLossBps: 400n } },
    maxLeverageX100: { rules: { maxLeverageX100: 500n } },
    assets: { rules: { assets: [3n] } },
    caps: { caps: [1000n, 1000n] },
  };
  for (const [field, change] of Object.entries(changes)) {
    const a = wide();
    const b = middle();
    const moved = { ...b, ...change, terms: { ...b.terms, ...change.terms }, rules: { ...b.rules, ...change.rules } };
    assert.equal(seriesOf([a, moved]).length, 2, field);
    assert.notEqual(shared(a), shared(moved), field);
  }
  // The owner as Hyperliquid and ethers write it, upper or lower case, is one owner.
  assert.equal(seriesOf([wide(), { ...middle(), owner: OWNER.toLowerCase() }]).length, 1);
});

test("copies are not variants, and more than three is not a card", () => {
  assert.equal(seriesOf([wide(), pool(5, { terms: { price: 45_000_000n } })]).length, 2);
  const four = [wide(), middle(), narrow(), pool(4, { terms: { price: 10_000_000n, targetBps: 800n },
    rules: { maxDrawdownBps: 200n } })];
  assert.equal(MAX_VARIANTS, 3);
  assert.equal(seriesOf(four).length, 4);
});

test("a bench of ours and an archived pool never join a series", () => {
  assert.equal(seriesOf([wide(), { ...middle(), kind: "bench" }]).length, 2);
  assert.equal(seriesOf([wide(), { ...middle(), archived: true }]).length, 2);
});

test("a series card offers each variant's status and drawdown to the filters, and its best figure to each sort", () => {
  const each = [
    { search: "0xaa btc", status: "Open", dd: "300", target: "900", price: "20000000", funded: "5000000000",
      held: "10", order: "3" },
    { search: "0xbb btc", status: "Awaiting top-up", dd: "500", target: "1200", price: "33000000", funded: "5000000000",
      held: "4", order: "2" },
    { search: "0xcc btc", status: "Open", dd: "600", target: "1000", price: "45000000", funded: "5000000000",
      held: "7", order: "1" },
    // A pool whose balance was not read has no status yet: it adds nothing to the list.
    { search: "0xdd btc", status: "", dd: "600", target: "1000", price: "45000000", funded: "5000000000",
      held: "9", order: "0" },
  ];
  assert.deepEqual(seriesDataset(each), {
    search: "0xaa btc 0xbb btc 0xcc btc 0xdd btc",
    status: ["Open", "Awaiting top-up"].join(LIST),
    dd: ["300", "500", "600"].join(LIST),
    target: "1200", price: "20000000", funded: "5000000000", held: "4", order: "3",
  });
  // A status name has spaces in it: the list's separator is not one.
  assert.doesNotMatch(LIST, /\s/);
});

test("a figure no variant has, or one that is no number, is left empty rather than Infinity or NaN", () => {
  assert.deepEqual(seriesDataset([]), { search: "", status: "", dd: "", target: "", price: "", funded: "", held: "",
    order: "" });
  const got = seriesDataset([{ search: "0xaa", status: "Open", dd: "500", target: "x", price: "20" }, { search: "0xbb" }]);
  assert.deepEqual(got, { search: "0xaa 0xbb", status: "Open", dd: "500", target: "", price: "20", funded: "", held: "",
    order: "" });
});
