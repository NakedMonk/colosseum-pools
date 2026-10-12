// node --test app/tests/gentle.test.mjs
//
// The gentle reader under the app's read provider (app/lib/gentle.js, app/lib/chain.js, app/app.js).
//
// 12 Oct 2026: a visitor with two tabs of the site and a wallet open got "Could not load this page" on the home page.
// Measured that day against the public HyperEVM RPC: the home page asked 158 calls in 41 HTTP requests, 50 of them
// repeats; the node refuses for the rate with HTTP 200 and ONE object for the whole batch,
// {"jsonrpc":"2.0","id":null,"error":{"code":-32005,"message":"rate limited"}}, no Retry-After; after a burst it
// went on refusing a single call for about 45 seconds. The replies below are that node's, as it gave them.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  GENTLE, READS, RATE_LIMITED, keyOf, refusedForRate, neverChanges, createGentle, waitWords,
} from "../lib/gentle.js";
import { MAX_BATCH } from "../lib/batch.js";

const text = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

/** What the node sent in place of a batch it refused for the rate. */
const REFUSAL = { status: 200, body: { jsonrpc: "2.0", id: null, error: { code: -32005, message: "rate limited" } } };
const RULES = "0x52f6747a";     // rules(), fixed when a pool is set up
const STAGE = "0xc040e6b8";     // stage(), which changes
const POOL = "0x70066669eC7Eb992055c82B615eD822CFF14e44A";
const call = (data, to = POOL) => ({ method: "eth_call", params: [{ to, data }, "latest"] });

/** A node that answers every call with its own data, unless `reply(n, batch)` says otherwise. */
const echo = (batch) => ({ status: 200, body: batch.map((c) => ({ jsonrpc: "2.0", id: c.id, result: `0x${String(c.params[0]?.data ?? c.method).replace(/^0x/, "")}aa` })) });

/**
 * A reader on a clock of the test's own, with every post and every wait written down. A sleep ends only when the
 * clock is moved to it, and the clock moves to the next sleeper by itself whenever nothing else can run: so what
 * happens during a wait happens at the time it would, not after it, and a wait nobody expected cannot hang a test.
 */
function world({ reply = () => null, options = {}, store = null, staticSelectors = new Set([RULES]) } = {}) {
  const w = { t: 0, posts: [], waits: [], sleeps: [], sleepers: [], out: 0, most: 0, armed: false };
  const arm = () => {
    if (w.armed || !w.sleepers.length) return;
    w.armed = true;
    // After every microtask that is due: the posts and their answers of this moment are through.
    setImmediate(() => {
      w.armed = false;
      w.sleepers.sort((a, b) => a.at - b.at);
      const next = w.sleepers.shift();
      w.t = Math.max(w.t, next.at);
      next.wake();
      arm();
    });
  };
  w.go = (promise) => promise;
  w.reader = createGentle({
    now: () => w.t,
    sleep: (ms) => new Promise((wake) => {
      w.sleeps.push(ms);
      w.sleepers.push({ at: w.t + ms, wake });
      arm();
    }),
    schedule: (fn) => queueMicrotask(fn),
    onWait: (wait) => w.waits.push(wait),
    staticSelectors, store, options,
    post: async (batch) => {
      w.out++;
      w.most = Math.max(w.most, w.out);
      w.posts.push({ at: w.t, batch });
      // This post's own number, taken before anything else is sent: the nth request the node saw.
      const n = w.posts.length;
      await Promise.resolve();
      const said = await reply(n, batch);
      w.out--;
      if (said instanceof Error) throw said;
      return said || echo(batch);
    },
  });
  return w;
}

/** A sessionStorage-like thing. */
function memoryStore() {
  const kept = new Map();
  return { kept, getItem: (k) => (kept.has(k) ? kept.get(k) : null), setItem: (k, v) => kept.set(k, String(v)) };
}

test("the same call asked twice is asked once, while it is out and for a short time after", async () => {
  const w = world();
  const [a, b, other] = await Promise.all([w.reader.request(call(STAGE)), w.reader.request(call(STAGE)), w.reader.request(call("0xd2ef7398"))]);
  assert.deepEqual(a, { result: "0xc040e6b8aa" });
  assert.deepEqual(b, a);
  assert.deepEqual(other, { result: "0xd2ef7398aa" });
  assert.equal(w.posts.length, 1);
  assert.equal(w.posts[0].batch.length, 2, "two different calls, the repeated one sent once");
  // Each asker has an answer of its own: one that changes its copy does not change another's.
  a.result = "changed";
  assert.equal(b.result, "0xc040e6b8aa");
  // Inside the short time it is not asked again; at its end it is.
  w.t = GENTLE.liveMs - 1;
  assert.deepEqual(await w.reader.request(call(STAGE)), { result: "0xc040e6b8aa" });
  assert.equal(w.posts.length, 1);
  w.t = GENTLE.liveMs;
  await w.reader.request(call(STAGE));
  assert.equal(w.posts.length, 2);
  // The same view of another contract is another call.
  await w.reader.request(call(STAGE, "0xf40da2862f1e3f20ab7d1ed7b5e67a83d333cb3b"));
  assert.equal(w.posts.length, 3);
  assert.equal(keyOf(call(STAGE)), `eth_call:[{"to":"${POOL}","data":"${STAGE}"},"latest"]`);
  const s = w.reader.stats();
  assert.equal(s.shared, 1);
  assert.equal(s.cached, 1);
  assert.equal(s.calls, 4);
});

test("what can change is kept a few seconds, and what a contract never changes is kept for the session", async () => {
  // A verdict or a stage read through this is shown to a visitor as now: a few seconds and no more.
  assert.ok(GENTLE.liveMs >= 1_000 && GENTLE.liveMs <= 5_000);
  assert.ok(GENTLE.staticMs >= 10 * 60_000);
  const store = memoryStore();
  const w = world({ store });
  await w.reader.request(call(RULES));
  await w.reader.request(call(STAGE));
  assert.equal(w.posts.length, 2);
  w.t = GENTLE.liveMs + 1;
  await w.reader.request(call(RULES));
  assert.equal(w.posts.length, 2, "the rules are not asked again");
  await w.reader.request(call(STAGE));
  assert.equal(w.posts.length, 3, "the stage is");
  w.t = GENTLE.staticMs - 1;
  await w.reader.request(call(RULES));
  assert.equal(w.posts.length, 3);
  w.t = GENTLE.staticMs;
  await w.reader.request(call(RULES));
  assert.equal(w.posts.length, 4, "kept for the session, not for ever");

  // Only what never changes goes to the store, and another page of the same tab reads it from there.
  assert.equal(store.kept.size, 1);
  assert.ok([...store.kept.keys()][0].startsWith("pools-rpc-v1:eth_call:"));
  const later = world({ store });
  later.t = GENTLE.staticMs + 5;
  assert.deepEqual(await later.reader.request(call(RULES)), { result: "0x52f6747aaa" });
  assert.equal(later.posts.length, 0);
  later.t = 2 * GENTLE.staticMs + 5;
  await later.reader.request(call(RULES));
  assert.equal(later.posts.length, 1, "what the store holds grows old too");
  // A store that throws is a store that is not there.
  const broken = world({ store: { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("full"); } } });
  assert.deepEqual(await broken.reader.request(call(RULES)), { result: "0x52f6747aaa" });
  assert.equal(broken.posts.length, 1);

  const fixed = new Set([RULES]);
  assert.equal(neverChanges(call(RULES), "0x01", fixed), true);
  assert.equal(neverChanges(call(STAGE), "0x01", fixed), false);
  assert.equal(neverChanges(call(RULES.toUpperCase().replace("0X", "0x")), "0x01", fixed), true);
  // An empty answer may yet become something, and a reading of an older block is not the page's "now".
  assert.equal(neverChanges(call(RULES), "0x", fixed), false);
  assert.equal(neverChanges({ method: "eth_call", params: [{ to: POOL, data: RULES }, "0x10"] }, "0x01", fixed), false);
  assert.equal(neverChanges({ method: "eth_getCode", params: [POOL, "latest"] }, "0x6080"), true);
  assert.equal(neverChanges({ method: "eth_getCode", params: [POOL, "latest"] }, "0x"), false);
  assert.equal(neverChanges({ method: "eth_getTransactionReceipt", params: ["0x01"] }, { blockNumber: "0x5" }), true);
  assert.equal(neverChanges({ method: "eth_getTransactionReceipt", params: ["0x01"] }, null), false);
  assert.equal(neverChanges({ method: "eth_getBlockByNumber", params: ["latest", false] }, { timestamp: "0x1" }), false);
  assert.equal(neverChanges({ method: "eth_chainId", params: [] }, "0x3e6"), true);
});

test("calls are gathered into batches of at most twenty, and at most two batches are out at a time", async () => {
  assert.equal(GENTLE.maxBatch, MAX_BATCH);
  assert.ok(GENTLE.maxInFlight >= 1 && GENTLE.maxInFlight <= 2);
  // The node may answer a batch in any order; each asker gets its own answer by the id.
  const w = world({ reply: (n, batch) => ({ status: 200, body: [...echo(batch).body].reverse() }) });
  const selectors = Array.from({ length: 50 }, (_, i) => `0x${(0x1000 + i).toString(16).padStart(8, "0")}`);
  const answers = await Promise.all(selectors.map((s) => w.reader.request(call(s))));
  assert.deepEqual(w.posts.map((p) => p.batch.length), [20, 20, 10]);
  assert.equal(w.most, 2);
  answers.forEach((a, i) => assert.deepEqual(a, { result: `${selectors[i]}aa` }));
  for (const p of w.posts) assert.deepEqual(p.batch.map((c) => c.id), p.batch.map((_, i) => i + 1));
  assert.ok(w.posts[0].batch.every((c) => c.jsonrpc === "2.0"));
  // With one batch out at a time they go one after another.
  const single = world({ options: { maxInFlight: 1 } });
  await Promise.all(selectors.map((s) => single.reader.request(call(s))));
  assert.equal(single.most, 1);
  assert.equal(single.posts.length, 3);
  // A call the node leaves out of its answer fails alone, and says so.
  const short = world({ reply: (n, batch) => ({ status: 200, body: echo(batch).body.slice(1) }) });
  const [lost, found] = await Promise.all([short.reader.request(call(STAGE)), short.reader.request(call(RULES))]);
  assert.match(lost.error.message, /left this call out/);
  assert.deepEqual(found, { result: "0x52f6747aaa" });
});

test("a refusal for the rate is waited out and asked again, and is not an error", async () => {
  // The node's own refusal: HTTP 200, one object, no id.
  assert.equal(refusedForRate(200, REFUSAL.body), true);
  assert.equal(refusedForRate(429, null), true);
  assert.equal(refusedForRate(200, [{ id: 1, result: "0x" }, { id: 2, error: { code: -32005, message: "rate limited" } }]), true);
  assert.equal(refusedForRate(200, { error: { code: -32000, message: "Too Many Requests" } }), true);
  // Not a refusal for the rate: a batch too large, a revert, an answer.
  assert.equal(refusedForRate(200, { id: null, error: { code: -32010, message: "Exceeded max limit of 20" } }), false);
  assert.equal(refusedForRate(200, [{ id: 1, error: { code: 3, message: "execution reverted", data: "0x" } }]), false);
  assert.equal(refusedForRate(200, [{ id: 1, result: "0x01" }]), false);
  assert.equal(refusedForRate(200, null), false);
  assert.deepEqual(RATE_LIMITED, { code: -32005, message: "rate limited" });

  for (const refusal of [REFUSAL, { status: 429, body: null }]) {
    const w = world({ reply: (n) => (n <= 2 ? refusal : null) });
    const answer = await w.go(w.reader.request(call(STAGE)));
    assert.deepEqual(answer, { result: "0xc040e6b8aa" });
    assert.equal(w.posts.length, 3);
    assert.deepEqual(w.posts.map((p) => p.at), [0, 1_000, 3_000]);
    assert.deepEqual(w.sleeps, [1_000, 2_000]);
    assert.deepEqual(w.waits, [
      { attempt: 1, of: 6, delayMs: 1_000, calls: 1 },
      { attempt: 2, of: 6, delayMs: 2_000, calls: 1 },
      null,
    ]);
    assert.equal(w.reader.stats().waits, 2);
    assert.equal(w.reader.stats().gaveUp, 0);
  }
  // Each wait is longer than the one before, and together they outlast the 45 seconds the node held a refusal.
  const delays = [...GENTLE.retryDelaysMs];
  assert.deepEqual(delays, [...delays].sort((a, b) => a - b));
  assert.equal(new Set(delays).size, delays.length);
  const sum = delays.reduce((a, b) => a + b, 0);
  assert.ok(sum >= 45_000 && sum <= 90_000, `${sum} ms in all`);
  assert.ok(delays[0] >= 500);
});

test("a refusal met by one batch holds every batch", async () => {
  // Thirty calls: two batches out together. The first is refused once; the second is refused too, and then both
  // wait. No batch is sent inside a wait: asking through it only earns another refusal.
  const w = world({ reply: (n) => (n <= 2 ? REFUSAL : null) });
  const selectors = Array.from({ length: 30 }, (_, i) => `0x${(0x2000 + i).toString(16).padStart(8, "0")}`);
  const answers = await w.go(Promise.all(selectors.map((s) => w.reader.request(call(s)))));
  assert.ok(answers.every((a) => a.result));
  assert.equal(w.posts.length, 4);
  assert.deepEqual(w.posts.slice(0, 2).map((p) => p.at), [0, 0]);
  for (const p of w.posts.slice(2)) assert.ok(p.at >= 1_000, `sent again at ${p.at} ms`);
  // The page is told once that the reads go again, when the last waiting batch is through.
  assert.equal(w.waits.filter((x) => x === null).length, 1);
  assert.equal(w.waits[w.waits.length - 1], null);

  // A batch that was still in the queue waits too. Fifty calls: two batches go out, the first is refused and the
  // second answered; the third leaves the queue the moment the second is back, and must not be sent inside the wait.
  const q = world({ reply: (n) => (n === 1 ? REFUSAL : null) });
  const fifty = Array.from({ length: 50 }, (_, i) => `0x${(0x3000 + i).toString(16).padStart(8, "0")}`);
  await q.go(Promise.all(fifty.map((s) => q.reader.request(call(s)))));
  assert.equal(q.posts.length, 4);
  assert.deepEqual(q.posts.slice(0, 2).map((p) => p.at), [0, 0]);
  assert.deepEqual(q.posts.slice(2).map((p) => p.at), [1_000, 1_000], "the queued batch and the refused one both go after the wait");
  assert.deepEqual(q.posts.map((p) => p.batch.length).sort((a, b) => a - b), [10, 20, 20, 20]);
});

test("only when the last wait is spent do the calls fail, with the node's own refusal", async () => {
  const w = world({ reply: () => REFUSAL });
  const [a, b] = await w.go(Promise.all([w.reader.request(call(STAGE)), w.reader.request(call(RULES))]));
  assert.deepEqual(a, { error: { code: -32005, message: "rate limited" } });
  assert.deepEqual(b, a);
  // Asked once and then once after each wait: seven times, a minute apart from first to last.
  assert.equal(w.posts.length, GENTLE.retryDelaysMs.length + 1);
  assert.deepEqual(w.sleeps, [...GENTLE.retryDelaysMs]);
  assert.equal(w.t, 60_000);
  assert.equal(w.waits.filter(Boolean).length, 6);
  assert.equal(w.waits[w.waits.length - 1], null);
  assert.equal(w.reader.stats().gaveUp, 2);
  // A refusal is not an answer: nothing is kept, and the next asking goes to the node.
  const before = w.posts.length;
  w.reader.request(call(STAGE));
  await new Promise((resolve) => setImmediate(resolve));
  assert.ok(w.posts.length > before);
});

test("an error is the call's own, is not waited on and is not kept", async () => {
  // A revert inside a batch: that call fails, the others are answered.
  const revert = { code: 3, message: "execution reverted", data: "0x12345678" };
  const w = world({ reply: (n, batch) => ({ status: 200, body: batch.map((c) => (c.params[0].data === STAGE ? { id: c.id, error: revert } : { id: c.id, result: "0x01" })) }) });
  const [bad, good] = await Promise.all([w.reader.request(call(STAGE)), w.reader.request(call(RULES))]);
  assert.deepEqual(bad, { error: revert });
  assert.deepEqual(good, { result: "0x01" });
  assert.deepEqual(w.sleeps, []);
  await w.reader.request(call(STAGE));
  assert.equal(w.posts.length, 2, "the revert was not kept");
  // The node refusing the batch as a whole for another reason: every call gets that reason, at once.
  const tooLarge = { code: -32010, message: "Exceeded max limit of 20" };
  const refused = world({ reply: () => ({ status: 200, body: { jsonrpc: "2.0", id: null, error: tooLarge } }) });
  assert.deepEqual(await refused.reader.request(call(STAGE)), { error: tooLarge });
  assert.equal(refused.posts.length, 1);
  assert.deepEqual(refused.sleeps, []);
});

test("a request that never got an answer is sent again, then fails the way fetch fails", async () => {
  const down = new TypeError("Failed to fetch");
  const once = world({ reply: (n) => (n <= 2 ? down : null) });
  assert.deepEqual(await once.go(once.reader.request(call(STAGE))), { result: "0xc040e6b8aa" });
  assert.equal(once.posts.length, 3);
  assert.deepEqual(once.sleeps, [GENTLE.networkDelayMs, 2 * GENTLE.networkDelayMs]);
  assert.deepEqual(once.waits, [], "not a rate limit: the page is not told to wait for one");

  const never = world({ reply: () => down });
  const lost = await never.go(never.reader.request(call(STAGE)));
  assert.equal(lost.thrown, down);
  assert.equal(never.posts.length, GENTLE.networkRetries + 1);
  // A gateway in front of the node answering 502 is the same; a 4xx that is not JSON is not worth asking again.
  const gateway = world({ reply: () => ({ status: 502, body: null }) });
  const bad = await gateway.go(gateway.reader.request(call(STAGE)));
  assert.equal(bad.thrown.status, 502);
  assert.equal(gateway.posts.length, GENTLE.networkRetries + 1);
  const page = world({ reply: () => ({ status: 404, body: null }) });
  assert.equal((await page.reader.request(call(STAGE))).thrown.status, 404);
  assert.equal(page.posts.length, 1);
});

test("after a transaction of the visitor's own, what can change is read again", async () => {
  const w = world();
  await Promise.all([w.reader.request(call(STAGE)), w.reader.request(call(RULES))]);
  w.reader.forgetLive();
  await Promise.all([w.reader.request(call(STAGE)), w.reader.request(call(RULES))]);
  assert.equal(w.posts.length, 2);
  assert.deepEqual(w.posts[1].batch.map((c) => c.params[0].data), [STAGE], "the stage is read again, the rules are not");
  // What is not a read is never shared and never kept.
  assert.equal(READS.has("eth_sendRawTransaction"), false);
  assert.equal(READS.has("eth_call"), true);
  const send = { method: "eth_sendRawTransaction", params: ["0xf86b"] };
  await Promise.all([w.reader.request(send), w.reader.request(send)]);
  assert.equal(w.posts[w.posts.length - 1].batch.filter((c) => c.method === "eth_sendRawTransaction").length, 2);
});

test("a read that was out when the visitor's transaction landed is not joined and not kept", async () => {
  // The first reading of the stage is still out when the transaction is mined. It answers with the stage from
  // before; the page, drawn again after the transaction, must not be given that.
  const turn = () => new Promise((resolve) => setImmediate(resolve));
  // The node holds the first two requests until the test lets each go, and answers any later one at once: a request
  // the reader should not have made then shows as a count that is wrong, not as a test that never ends.
  const held = (gates, upTo) => async (n, batch) => {
    if (n <= upTo) await new Promise((open) => gates.push(open));
    return { status: 200, body: batch.map((c) => ({ jsonrpc: "2.0", id: c.id, result: `0x0${n}` })) };
  };
  const gates = [];
  const w = world({ reply: held(gates, 2) });
  const before = w.reader.request(call(STAGE));
  await turn();
  assert.equal(w.posts.length, 1);
  w.reader.forgetLive();
  const after = w.reader.request(call(STAGE));
  await turn();
  assert.equal(w.posts.length, 2, "asked again, not joined to the reading from before the transaction");
  // The old reading comes back first. It goes to who asked for it, and takes nothing of the new one's with it:
  // a third asking, while the second is still out, joins the second.
  gates[0]();
  assert.deepEqual(await before, { result: "0x01" });
  const third = w.reader.request(call(STAGE));
  await turn();
  assert.equal(w.posts.length, 2, "the old answer's return did not unseat the reading that is out");
  gates[1]();
  assert.deepEqual(await after, { result: "0x02" });
  assert.deepEqual(await third, { result: "0x02" });
  // What is kept is the reading made after the transaction, whichever came back last.
  assert.deepEqual(await w.reader.request(call(STAGE)), { result: "0x02" });
  assert.equal(w.posts.length, 2);

  // The same order the other way round: the new reading back first, the old one last. The old one is not kept.
  const late = [];
  const v = world({ reply: held(late, 2) });
  const old = v.reader.request(call(STAGE));
  await turn();
  v.reader.forgetLive();
  const fresh = v.reader.request(call(STAGE));
  await turn();
  late[1]();
  assert.deepEqual(await fresh, { result: "0x02" });
  late[0]();
  assert.deepEqual(await old, { result: "0x01" });
  assert.deepEqual(await v.reader.request(call(STAGE)), { result: "0x02" });
  assert.equal(v.posts.length, 2);

  // What a contract never changes is the same on both sides of any transaction: out or not, it is kept.
  const fixed = [];
  const r = world({ reply: held(fixed, 1) });
  const rules = r.reader.request(call(RULES));
  await turn();
  r.reader.forgetLive();
  fixed[0]();
  assert.deepEqual(await rules, { result: "0x01" });
  assert.deepEqual(await r.reader.request(call(RULES)), { result: "0x01" });
  assert.equal(r.posts.length, 1);
});

test("the read provider is the gentle one, and nothing a rule is judged by is kept for the session", () => {
  const chain = text("../lib/chain.js");
  assert.match(chain, /class GentleProvider extends ethers\.JsonRpcProvider \{\s+async _send\(payload\) \{/);
  assert.match(chain, /const answers = await Promise\.all\(list\.map\(\(p\) => gentle\.request\(\{ method: p\.method, params: p\.params \}\)\)\);/);
  assert.match(chain, /const lost = answers\.find\(\(a\) => a\.thrown\);\s+if \(lost\) throw lost\.thrown;/);
  assert.match(chain, /export const readProvider = new GentleProvider\(CONFIG\.rpc, CONFIG\.chainId, PROVIDER_OPTIONS\);/);
  assert.equal((chain.match(/new GentleProvider\(/g) || []).length, 1);
  assert.doesNotMatch(chain, /new ethers\.JsonRpcProvider\(/);
  // What is kept for the session is named, and is only what the contracts fix when they are set up.
  const fixed = JSON.parse(chain.match(/export const FIXED_VIEWS = Object\.freeze\((\[[^\]]*\])\);/)[1].replace(/,\s*\]/, "]"));
  assert.deepEqual(fixed, ["rules()", "terms()", "owner()", "trader()", "pool()", "createdAt()", "factory()", "feeBps()", "minDeposit()", "lock()"]);
  for (const live of ["stage", "status", "violation", "breachReason", "isStopped", "challenge", "accountReady", "capitalNeeded",
    "deadline", "drawdownBase", "dayStartEquity", "balanceOf", "isPool", "isChallenge", "value", "seatsSealed", "freeCount"]) {
    assert.ok(!fixed.some((f) => f.startsWith(`${live}(`)), `${live}() is read again every time`);
  }
  // Each is set once in the contracts: assigned in an initializer or a constructor, or declared immutable.
  const pool = text("../../src/Pool.sol");
  const account = text("../../src/ChallengeAccount.sol");
  const shared = text("../../src/shared/SharedPool.sol");
  assert.match(pool, /owner = owner_;/);
  assert.equal((pool.match(/\bowner = /g) || []).length, 1, "a pool's owner is assigned once");
  assert.equal((pool.match(/_terms = /g) || []).length, 1);
  assert.equal((account.match(/_terms = /g) || []).length, 1);
  assert.equal((account.match(/\btrader = /g) || []).length, 1);
  assert.equal((account.match(/createdAt = /g) || []).length, 1);
  for (const name of ["factory", "minDeposit", "lock", "feeBps"]) assert.match(shared, new RegExp(`public immutable ${name};`), name);
  // A write through the wallet forgets what can change, on both paths the app writes by.
  assert.match(chain, /const receipt = await tx\.wait\(\);\s+forgetLiveReads\(\);/);
  assert.match(text("../views/shared.js"), /const receipt = await tx\.wait\(\);\s+chain\.forgetLiveReads\(\);/);
  assert.match(chain, /store\.getItem\("pools-rpc-v1:probe"\);/);
});

test("the home page reads a few pools at a time, and asks an archived pool nothing about a sale", () => {
  const pools = text("../views/pools.js");
  const atOnce = Number(pools.match(/export const POOLS_AT_ONCE = (\d+);/)[1]);
  // Seven calls a pool: a round has to fit the two batches of twenty that may be out together, and be more than one.
  assert.ok(atOnce >= 2 && atOnce * 7 <= GENTLE.maxInFlight * MAX_BATCH, `${atOnce} pools at a time`);
  assert.match(pools, /items\.push\(\.\.\.\(await chain\.readAll\(\[\.\.\.addresses\.entries\(\)\],\s+\(\[index, address\]\) => readPoolItem\(address, index, deployment\), POOLS_AT_ONCE\)\)\);/);
  assert.doesNotMatch(pools, /items\.push\(await readPoolItem\(/, "a pool at a time is a request for every pool");
  // The three readings a sale is decided by are asked of a pool that can sell, and of no other.
  assert.match(pools, /const sale = archived \? \[null, null, 0n\] : \[pool\.accountReady\(\), pool\.challenge\(\), pool\.capitalNeeded\(\)\];/);
  assert.match(pools, /rulesAndTerms\(pool\), pool\.stage\(\), pool\.owner\(\), hl\.spotUsdc\(address\), \.\.\.sale,/);
  assert.match(pools, /const blocker = archived \? \{ kind: "archived" \}\s+: saleBlocker\(\{ stage, ready, challenge, spot: spotUsdc, needed: Number\(neededSpot\) \/ 1e8 \}\);/);
  assert.ok(pools.indexOf("const archived = isArchive(deployment);") < pools.indexOf("const sale = archived ?"));
});

test("while the reads wait the page says so, and a page that still fails offers to try again", () => {
  assert.equal(waitWords(null), "");
  assert.equal(waitWords({ attempt: 3, of: 6, delayMs: 4_000, calls: 20 }),
    "The public HyperEVM RPC is limiting requests from this browser. Waiting 4 s before asking again (try 3 of 6).");
  assert.match(waitWords({ attempt: 1, of: 6, delayMs: 400, calls: 1 }), /Waiting 1 s/);
  const app = text("../app.js");
  assert.match(app, /const box = \$\("#rpc-wait"\);\s+if \(!box\) return;\s+chain\.onRpcWait\(\(wait\) => \{\s+box\.textContent = waitWords\(wait\);\s+box\.hidden = !wait;\s+\}\);/);
  assert.match(app, /walletButton\(\);\s+rpcWaitNotice\(\);/);
  assert.match(app, /<p><button id="try-again">Try again<\/button><\/p><\/section>`\);\s+\$\("#try-again", page\)\.addEventListener\("click", route\);/);
  assert.match(text("../index.html"), /<div class="notice" id="rpc-wait" role="status" hidden><\/div>/);
});
