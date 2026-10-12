// Chain reads that go easy on a public node.
//
// Every read of the app goes to one public HyperEVM RPC, from the visitor's own address. Measured on 12 Oct 2026:
// the home page asked it 158 calls in 41 HTTP requests, 50 of them the same call asked twice, and a visitor with
// two tabs and a wallet open got "Could not load this page" on it. The node does not answer 429. It answers
// HTTP 200 with one object for the whole batch, `{"id":null,"error":{"code":-32005,"message":"rate limited"}}`,
// with no Retry-After, and after a burst it kept refusing a single call for about 45 seconds.
//
// So the reads are made gentle in one place, under the provider, where every page's reads pass:
//   - the same call asked twice is asked once: while it is in flight, and for a short time after;
//   - what a contract never changes (its rules, its terms, its owner) is kept for the session;
//   - calls are gathered into batches of at most twenty, and at most two batches are out at a time;
//   - a refusal for the rate is not an error yet: the batch waits and asks again, longer each time, and every
//     other batch waits with it. Only when the last wait is spent do the calls fail, with the node's own refusal.
// No browser globals here, so node's test runner loads it (app/tests/gentle.test.mjs).
import { MAX_BATCH } from "./batch.js";

export const GENTLE = Object.freeze({
  maxBatch: MAX_BATCH,
  /** Batches out at one time. */
  maxInFlight: 2,
  /** How long calls are gathered before a batch goes: one render's reads start within a few milliseconds. */
  gatherMs: 15,
  /** How long an answer that can change is kept. Short on purpose: a verdict or a stage read here is shown as now. */
  liveMs: 3_000,
  /** How long an answer that cannot change is kept. */
  staticMs: 30 * 60_000,
  /** The waits after a refusal for the rate, in turn: a minute in all, a little over what the node held a refusal. */
  retryDelaysMs: Object.freeze([1_000, 2_000, 4_000, 8_000, 15_000, 30_000]),
  /** A request that did not reach the node at all, or met a 5xx, is sent again this many times. */
  networkRetries: 2,
  networkDelayMs: 800,
});

/** The methods whose answers may be shared and kept. Anything else goes to the node every time it is asked. */
export const READS = new Set([
  "eth_call", "eth_getCode", "eth_chainId", "eth_getTransactionReceipt", "eth_getBlockByNumber", "eth_blockNumber",
  "eth_getBalance",
]);

/** The node's code for "rate limited", and what it says. */
export const RATE_LIMITED = Object.freeze({ code: -32005, message: "rate limited" });

/** One call as the cache knows it: the method and its parameters, as sent. */
export function keyOf(call) {
  return `${call.method}:${JSON.stringify(call.params ?? [])}`;
}

const says = (error) => Number(error?.code) === RATE_LIMITED.code || Number(error?.code) === 429
  || /rate limit|too many requests/i.test(String(error?.message || ""));

/**
 * Whether an answer is the node refusing for the rate: HTTP 429, the one object it sends in place of a batch, or a
 * batch in which a call was refused that way.
 */
export function refusedForRate(status, body) {
  if (Number(status) === 429) return true;
  if (Array.isArray(body)) return body.some((item) => says(item?.error));
  return Boolean(body && typeof body === "object" && says(body.error));
}

/**
 * Whether an answer can never change, so that it may be kept for the session. `selectors` are the first four bytes
 * of the views a contract fixes when it is set up. An empty answer is never kept: "no code here" and "0x" can both
 * become something.
 */
export function neverChanges(call, result, selectors = new Set()) {
  const params = call.params ?? [];
  if (call.method === "eth_chainId") return typeof result === "string";
  if (call.method === "eth_getTransactionReceipt") return Boolean(result && result.blockNumber);
  if (call.method === "eth_getCode") return typeof result === "string" && result.length > 2;
  if (call.method === "eth_call") {
    const tx = params[0] || {};
    const data = String(tx.data ?? tx.input ?? "");
    const latest = params[1] === undefined || params[1] === "latest";
    return latest && typeof result === "string" && result.length > 2 && selectors.has(data.slice(0, 10).toLowerCase());
  }
  return false;
}

const copy = (answer) => (answer.thrown ? answer : JSON.parse(JSON.stringify(answer)));
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * The gentle reader. `post(batch)` sends one JSON-RPC batch and resolves to `{ status, body }`, the HTTP status and
 * the parsed answer (null when it was not JSON); it rejects when nothing answered.
 *
 * `request(call)` resolves, never rejects, to one of: `{ result }`, `{ error }` as the node gave it, or `{ thrown }`,
 * an Error for a request that never got an answer. `onWait` is told each wait, `{ attempt, of, delayMs, calls }`, and
 * `null` when the reads go again. `store` is a sessionStorage-like place for what never changes, or null.
 */
export function createGentle({
  post, now = Date.now, sleep = pause, schedule = (fn, ms) => setTimeout(fn, ms), onWait = () => {},
  staticSelectors = new Set(), store = null, storePrefix = "pools-rpc-v1:", options = {},
} = {}) {
  const o = { ...GENTLE, ...options };
  const cache = new Map();     // key -> { at, ttl, answer }
  const flying = new Map();    // key -> the promise of a call that is out or queued
  const queue = [];            // { call, key, kept, resolve }
  const stats = { posts: 0, calls: 0, cached: 0, shared: 0, waits: 0, gaveUp: 0 };
  let inFlight = 0;
  let scheduled = false;
  let coolUntil = 0;
  let waiting = 0;
  // Which side of the visitor's last transaction a call was asked on. A read asked before it may come back after
  // it, with what the chain held before: that answer goes to who asked, and is neither kept nor shared.
  let generation = 0;

  function remembered(key) {
    const hit = cache.get(key);
    if (hit && now() - hit.at < hit.ttl) return hit.answer;
    if (hit) cache.delete(key);
    if (!store) return null;
    try {
      const kept = JSON.parse(store.getItem(storePrefix + key) || "null");
      if (!kept || !(now() - kept.at < o.staticMs)) return null;
      cache.set(key, { at: kept.at, ttl: o.staticMs, answer: { result: kept.result } });
      return { result: kept.result };
    } catch {
      return null;   // storage blocked or full of something else: the node is asked, as if nothing were kept
    }
  }

  function remember(item, answer) {
    if (!item.kept || answer.result === undefined) return;
    const fixed = neverChanges(item.call, answer.result, staticSelectors);
    if (!fixed && item.generation !== generation) return;
    const at = now();
    cache.set(item.key, { at, ttl: fixed ? o.staticMs : o.liveMs, answer });
    if (fixed && store) {
      try {
        store.setItem(storePrefix + item.key, JSON.stringify({ at, result: answer.result }));
      } catch {
        // kept in memory only
      }
    }
  }

  function request(call) {
    const key = keyOf(call);
    const kept = READS.has(call.method);
    if (kept) {
      const answer = remembered(key);
      if (answer) {
        stats.cached++;
        return Promise.resolve(copy(answer));
      }
      const same = flying.get(key);
      if (same) {
        stats.shared++;
        return same.then(copy);
      }
    }
    const asked = new Promise((resolve) => queue.push({ call, key, kept, resolve, generation }));
    if (kept) {
      flying.set(key, asked);
      // Only its own entry: by now a later asking of the same call may be the one that is out.
      asked.then(() => {
        if (flying.get(key) === asked) flying.delete(key);
      });
    }
    if (!scheduled) {
      scheduled = true;
      schedule(() => {
        scheduled = false;
        pump();
      }, o.gatherMs);
    }
    return asked.then(copy);
  }

  function pump() {
    while (inFlight < o.maxInFlight && queue.length) send(queue.splice(0, o.maxBatch));
  }

  /** One batch to the node, once. What came back, sorted into what to do with it. */
  async function ask(items) {
    const body = items.map((item, i) => ({ jsonrpc: "2.0", id: i + 1, method: item.call.method, params: item.call.params ?? [] }));
    for (let n = 0; ; n++) {
      let reply = null;
      let failure = null;
      try {
        reply = await post(body);
      } catch (err) {
        failure = err instanceof Error ? err : new Error(String(err));
      }
      if (reply) {
        stats.posts++;
        stats.calls += items.length;
        if (refusedForRate(reply.status, reply.body)) return { kind: "limited" };
        if (Array.isArray(reply.body)) return { kind: "answers", list: reply.body };
        if (reply.body && typeof reply.body === "object" && reply.body.error) return { kind: "refused", error: reply.body.error };
        // No JSON-RPC answer at all: a gateway's error page, or a body cut short.
        failure = Object.assign(new Error(`The node answered HTTP ${reply.status} with nothing this app can read.`), { status: reply.status });
        if (Number(reply.status) < 500) return { kind: "thrown", thrown: failure };
      }
      if (n >= o.networkRetries) return { kind: "thrown", thrown: failure };
      await sleep(o.networkDelayMs * (n + 1));
    }
  }

  async function send(items) {
    inFlight++;
    let told = false;
    try {
      let outcome;
      for (let attempt = 0; ; attempt++) {
        // A refusal met by one batch holds every batch: asking through it only earns another refusal.
        const hold = coolUntil - now();
        if (hold > 0) await sleep(hold);
        outcome = await ask(items);
        if (outcome.kind !== "limited" || attempt >= o.retryDelaysMs.length) break;
        const delayMs = o.retryDelaysMs[attempt];
        coolUntil = Math.max(coolUntil, now() + delayMs);
        stats.waits++;
        if (!told) waiting++;
        told = true;
        onWait({ attempt: attempt + 1, of: o.retryDelaysMs.length, delayMs, calls: items.length });
        await sleep(delayMs);
      }
      settle(items, outcome);
    } finally {
      if (told && --waiting === 0) onWait(null);
      inFlight--;
      pump();
    }
  }

  function settle(items, outcome) {
    const byId = new Map();
    if (outcome.kind === "answers") for (const a of outcome.list) byId.set(Number(a?.id), a);
    if (outcome.kind === "limited") stats.gaveUp += items.length;
    items.forEach((item, i) => {
      let answer;
      if (outcome.kind === "thrown") answer = { thrown: outcome.thrown };
      else if (outcome.kind === "limited") answer = { error: { ...RATE_LIMITED } };
      else if (outcome.kind === "refused") answer = { error: outcome.error };
      else {
        const a = byId.get(i + 1);
        if (!a) answer = { error: { code: -32000, message: "the node left this call out of its answer" } };
        else if (a.error) answer = { error: a.error };
        else answer = { result: a.result };
      }
      remember(item, answer);
      item.resolve(answer);
    });
  }

  /**
   * Forget what can change: after a transaction of the visitor's own, the page must read what it did. That takes in
   * the reads still out or waiting, which were asked before it: none of them is joined by a later asking, and what
   * they bring back is not kept.
   */
  function forgetLive() {
    generation++;
    flying.clear();
    for (const [key, hit] of cache) if (hit.ttl !== o.staticMs) cache.delete(key);
  }

  return { request, forgetLive, stats: () => ({ ...stats, queued: queue.length, inFlight }) };
}

/** What the page says while the reads wait for the node. */
export function waitWords(wait) {
  if (!wait) return "";
  const seconds = Math.max(1, Math.round(wait.delayMs / 1000));
  return `The public HyperEVM RPC is limiting requests from this browser. Waiting ${seconds} s before asking again `
    + `(try ${wait.attempt} of ${wait.of}).`;
}
