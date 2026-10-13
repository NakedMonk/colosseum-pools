// Many reads in one call.
//
// Measured on 13 Oct 2026 against the public HyperEVM RPC: the home page asked the node 91 calls in 12 requests,
// one after another, and took 6.3 seconds; a single request took 0.26 to 0.94 seconds. The node limits the calls of
// an address, and five tabs of the home page asked for more than twice what it allows.
//
// Multicall3 is a contract that makes the calls it is handed and returns their answers. The same code stands at
// the same address on HyperEVM testnet as on Ethereum, Base and Arbitrum: 3808 bytes, compared byte for byte that
// day. Twelve calls of it carrying fifty reads each, 600 reads, were answered with no refusal: the node counts the
// call, not what it carries.
//
// A read made through it is still the visitor's own read of the chain. Nothing of ours stands between: before the
// first one, the app reads the code at the address and holds it to the hash below (app/lib/chain.js), and uses the
// contract only if it is that code.
//
// No browser globals and no library here, so node's test runner loads it (app/tests/multicall.test.mjs).

export const MULTICALL3 = Object.freeze({
  address: "0xcA11bde05977b3631167028862bE2a173976CA11",
  /** keccak256 of the code at the address, as read from HyperEVM testnet, Ethereum, Base and Arbitrum. */
  codeHash: "0xd5c15df687b16f2ff992fc8d767b4216323184a2bbc6ee2f9c398c318e770891",
});

/** aggregate3((address target, bool allowFailure, bytes callData)[]) returns ((bool success, bytes returnData)[]) */
const AGGREGATE3 = "82ad56cb";

const bare = (hex) => String(hex ?? "").replace(/^0x/i, "").toLowerCase();
const word = (n) => n.toString(16).padStart(64, "0");
const isHex = (text) => /^(?:[0-9a-f]{2})*$/.test(text);

/**
 * Whether a call may ride in a pack: a plain read of the latest state, with nothing but an address and data. A call
 * that names a sender, a value or a block is left as it is, since inside the contract the sender would be the
 * contract. A call to the contract itself is left too.
 */
export function packable(call, address = MULTICALL3.address) {
  if (call?.method !== "eth_call") return false;
  const [tx, block, ...more] = call.params ?? [];
  if (more.length || !tx || typeof tx !== "object") return false;
  if (!(block === undefined || block === "latest")) return false;
  const keys = Object.keys(tx);
  if (!keys.every((k) => k === "to" || k === "data" || k === "input")) return false;
  const to = bare(tx.to);
  const data = bare(tx.data ?? tx.input);
  return to.length === 40 && isHex(to) && isHex(data) && data.length >= 8 && to !== bare(address);
}

/** The data of one aggregate3 call that makes `calls`, each `{ to, data }`, and lets any of them fail alone. */
export function encodeAggregate3(calls) {
  const tuples = calls.map(({ to, data }) => {
    const d = bare(data);
    const bytes = d.length / 2;
    return word(BigInt(`0x${bare(to)}`)) + word(1) + word(0x60) + word(bytes) + d.padEnd(Math.ceil(bytes / 32) * 64, "0");
  });
  let offset = calls.length * 32;
  const heads = tuples.map((t) => {
    const head = word(offset);
    offset += t.length / 2;
    return head;
  });
  return `0x${AGGREGATE3}${word(0x20)}${word(calls.length)}${heads.join("")}${tuples.join("")}`;
}

/**
 * What an aggregate3 call returned for `count` calls: `[{ success, data }]` in their order, or null when the
 * answer is not that. Nothing is guessed from an answer that is cut short or points outside itself.
 */
export function decodeAggregate3(hex, count) {
  const h = bare(hex);
  if (!isHex(h) || !h.length) return null;
  const size = h.length / 2;
  const at = (byte) => {
    if (!Number.isSafeInteger(byte) || byte < 0 || byte + 32 > size) throw new RangeError("outside the answer");
    const value = BigInt(`0x${h.slice(byte * 2, byte * 2 + 64)}`);
    if (value > BigInt(size)) throw new RangeError("points outside the answer");
    return Number(value);
  };
  try {
    const list = at(0);
    if (at(list) !== count) return null;
    const items = list + 32;
    const out = [];
    for (let i = 0; i < count; i++) {
      const tuple = items + at(items + i * 32);
      const success = at(tuple);
      if (success !== 0 && success !== 1) return null;
      const bytes = tuple + at(tuple + 32);
      const length = at(bytes);
      if (bytes + 32 + length > size) return null;
      out.push({ success: success === 1, data: `0x${h.slice((bytes + 32) * 2, (bytes + 32 + length) * 2)}` });
    }
    return out;
  } catch {
    return null;
  }
}
