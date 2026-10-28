// node --test app/tests/*.test.mjs
// A v3 account's start surplus (self-audit v3, round 1, T1): no part of its equity to its contract,
// so none of the equity the page compares with the floors and the leverage rule.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { startSurplus, leverageOf } from "../lib/verdict.js";

const text = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

test("the start's surplus is read in USDC, and an account without one has none", async () => {
  const v3 = { assetLeverage: true };
  assert.equal(await startSurplus({ startSurplus: async () => 25_000_000n }, v3), 25);
  // A v3 account built before the fix set: the contract has no such function.
  const missing = Object.assign(new Error("missing revert data"), { code: "CALL_EXCEPTION" });
  assert.equal(await startSurplus({ startSurplus: async () => { throw missing; } }, v3), 0);
  // A read that failed is not a surplus of 0.
  const down = Object.assign(new Error("could not reach the node"), { code: "NETWORK_ERROR" });
  await assert.rejects(startSurplus({ startSurplus: async () => { throw down; } }, v3), /could not reach/);
  // An account of a deployment before v3 is not asked at all, so a node that is down costs it no panel.
  const asked = { startSurplus: async () => { throw down; } };
  assert.equal(await startSurplus(asked, {}), 0);
  assert.equal(await startSurplus(asked, undefined), 0);
});

test("a position open on no equity is over the leverage rule, not 0x", () => {
  assert.equal(leverageOf(300, 100), 3);
  assert.equal(leverageOf(0, 100), 0);
  assert.equal(leverageOf(0, 0), 0);
  assert.equal(leverageOf(50, 0), Infinity);
  assert.equal(leverageOf(50, -10), Infinity);
  assert.match(text("../views/trading.js"), /Number\.isFinite\(lev\) \? `\$\{lev\.toFixed\(2\)\}×` : "no equity"/);
});

test("the equity panel compares the equity the contract counts", () => {
  const panel = text("../views/trading.js");
  assert.match(panel, /hl\.positions\(account\), startSurplus\(contract, deployment\),/);
  // Both pages hand the panel the deployment their account belongs to.
  assert.match(text("../views/pool.js"), /stopped: Number\(stage\) === 3, deployment \}/);
  assert.match(text("../views/challenge.js"), /finished: s > 2, deployment \}/);
  assert.match(panel, /const equity = onCore - surplus;/);
  assert.match(text("../lib/chain.js"), /"function startSurplus\(\) view returns \(uint64\)"/);
});
