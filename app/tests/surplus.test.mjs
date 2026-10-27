// node --test app/tests/*.test.mjs
// A v3 account's start surplus (self-audit v3, round 1, T1): no part of its equity to its contract,
// so none of the equity the page compares with the floors and the leverage rule.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { startSurplus } from "../lib/verdict.js";

const text = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

test("the start's surplus is read in USDC, and an account without one has none", async () => {
  assert.equal(await startSurplus({ startSurplus: async () => 25_000_000n }), 25);
  // An account before v3, or one built before the fix set: the contract has no such function.
  const missing = Object.assign(new Error("missing revert data"), { code: "CALL_EXCEPTION" });
  assert.equal(await startSurplus({ startSurplus: async () => { throw missing; } }), 0);
  // A read that failed is not a surplus of 0.
  const down = Object.assign(new Error("could not reach the node"), { code: "NETWORK_ERROR" });
  await assert.rejects(startSurplus({ startSurplus: async () => { throw down; } }), /could not reach/);
});

test("the equity panel compares the equity the contract counts", () => {
  const panel = text("../views/trading.js");
  assert.match(panel, /hl\.positions\(account\), startSurplus\(contract\),/);
  assert.match(panel, /const equity = onCore - surplus;/);
  assert.match(text("../lib/chain.js"), /"function startSurplus\(\) view returns \(uint64\)"/);
});
