// node --test app/tests/*.test.mjs
// TooEarly is three different waits, and the page says which one (self-audit v3, round 1).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { friendly, refusalName, refusalWords } from "../lib/ui.js";

const text = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
// What a wallet's estimate throws on a contract's own error: unnamed, with the error's bytes as data
// (the first four bytes of keccak256 of each signature); and how chain.write tags it.
const SELECTOR = { TooEarly: "0x085de625", NotAllowed: "0x3d693ada", NoBreach: "0x5576206e" };
const IFACE = { parseError: (data) => {
  const name = Object.keys(SELECTOR).find((n) => SELECTOR[n] === data);
  if (!name) throw new Error("no matching error");
  return { name };
} };
const refused = (name, kind, method) => {
  const err = Object.assign(new Error("execution reverted (unknown custom error)"), {
    code: "CALL_EXCEPTION", revert: null, data: SELECTOR[name],
    shortMessage: "execution reverted (unknown custom error)",
  });
  err.sentAs = { kind, method, refusal: refusalName(err, IFACE) };
  return err;
};

test("a challenge's TooEarly says which wait it is", () => {
  assert.match(friendly(refused("TooEarly", "challenge", "abort")), /start window/);
  assert.match(friendly(refused("TooEarly", "challenge", "expire")), /deadline has not passed/);
  for (const method of ["graduate", "breach", "forfeit"]) {
    assert.match(friendly(refused("TooEarly", "challenge", method)), /started in this same block/);
  }
});

test("a funded stage's TooEarly is the block it opened in", () => {
  for (const method of ["breach", "stopFunded"]) {
    assert.match(friendly(refused("TooEarly", "pool", method)), /funded stage opened in this same block/);
  }
});

test("a stop the pool does not allow says who may stop it", () => {
  assert.match(friendly(refused("NotAllowed", "pool", "stopFunded")), /only the pool's owner/);
});

test("a refusal with no words of ours keeps its name, and an untagged error gets none", () => {
  assert.equal(friendly(refused("TooEarly", "pool", "abandonFundedStage")), "TooEarly");
  assert.equal(friendly(refused("NoBreach", "pool", "breach")), "NoBreach");
  assert.equal(refusalWords(undefined, "TooEarly"), "");
});

test("the name comes off the error's bytes, and a name ethers already gave wins", () => {
  assert.equal(refusalName(refused("TooEarly", "pool", "breach"), IFACE), "TooEarly");
  // Wrapped, as a wallet's answer reaches the page.
  assert.equal(refusalName({ code: "UNKNOWN_ERROR", error: { data: SELECTOR.NotAllowed } }, IFACE), "NotAllowed");
  assert.equal(refusalName({ revert: { name: "Locked" }, data: SELECTOR.TooEarly }, IFACE), "Locked");
  // An error this contract does not declare, or no data at all, names nothing.
  assert.equal(refusalName({ data: "0xdeadbeef" }, IFACE), "");
  assert.equal(refusalName(new Error("network down"), IFACE), "");
  // A wrapper with bytes that name nothing does not hide the contract's error under it: ethers' own
  // parseError answers null for those, the stand-in above throws, and both go on to the next layer.
  const NULL_IFACE = { parseError: (data) => (Object.values(SELECTOR).includes(data) ? IFACE.parseError(data) : null) };
  for (const iface of [IFACE, NULL_IFACE]) {
    for (const outer of ["0x", "0xdeadbeef"]) {
      assert.equal(refusalName({ code: "UNKNOWN_ERROR", data: outer, error: { data: SELECTOR.TooEarly } }, iface), "TooEarly");
    }
  }
});

test("the pool and the challenge know both refusals by name, and every write says which call it was", () => {
  const chain = text("../lib/chain.js");
  assert.match(chain, /"error TooEarly\(\)",\n  "error NotAllowed\(\)",\n\];/);
  assert.match(chain, /pool: \[\n    \.\.\.RULED,/);
  assert.match(chain, /challenge: \[\n    \.\.\.RULED,/);
  assert.match(chain, /err\.sentAs = \{ kind, method, refusal: refusalName\(err, c\.interface\) \};/);
});
