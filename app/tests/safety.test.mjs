// node --test app/tests/safety.test.mjs
//
// docs/SAFETY.md says what the code holds and points every statement at the line or the address that makes it
// true. This holds the pointers to the repository: each `path:line` names a file here and a line that carries the
// quoted text, each address is one of the deployments' own, each page is Hyperliquid's documentation, and every
// source the text cites is in the table and every row of the table is cited. A line that moves, or a check that is
// rewritten, breaks this test rather than the page quietly.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync, readdirSync } from "node:fs";

const root = new URL("../../", import.meta.url);
const read = (path) => readFileSync(new URL(path, root), "utf8").replace(/\r\n/g, "\n");
const PAGE = read("docs/SAFETY.md");
const [BODY, TABLE] = PAGE.split("\n## Sources\n");

function rows() {
  const out = [];
  for (const line of TABLE.split("\n")) {
    const m = line.match(/^\| (S\d+) \| (.+?) \| `(.+)` \|$/);
    if (m) out.push({ id: m[1], where: m[2].replace(/^`|`$/g, ""), text: m[3].replace(/\\\|/g, "|") });
  }
  return out;
}

test("the page keeps its sections, in order", () => {
  const headings = PAGE.split("\n").filter((l) => l.startsWith("## "));
  assert.deepEqual(headings, [
    "## 1. Enforced controls",
    "## 2. Trading and exits",
    "## 3. Operator powers",
    "## 4. Closing",
    "## 5. Availability and recovery",
    "## 6. Audits and reports",
    "## Sources",
  ]);
  assert.match(BODY, /Nothing here promises that capital is kept, that it earns anything,\nor that anyone can leave at once\./);
  assert.match(BODY, /there is no timelock anywhere/);
  assert.match(BODY, /There is no bug bounty\./);
});

test("every source is numbered once, in order, and every one is cited and every citation has a source", () => {
  const ids = rows().map((r) => r.id);
  assert.ok(ids.length >= 60, `${ids.length} rows`);
  assert.deepEqual(ids, ids.map((_, i) => `S${i + 1}`));
  const cited = new Set(BODY.match(/\bS\d+\b/g));
  assert.deepEqual([...cited].filter((c) => !ids.includes(c)), [], "cited without a source");
  assert.deepEqual(ids.filter((id) => !cited.has(id)), [], "a source nothing cites");
});

test("each `path:line` is a line of this repository that carries the quoted text", () => {
  const lines = rows().filter((r) => /^[\w./-]+:\d+$/.test(r.where));
  assert.ok(lines.length >= 50);
  for (const { id, where, text } of lines) {
    const [path, n] = where.split(":");
    assert.ok(existsSync(new URL(path, root)), `${id}: ${path} is not in the repository`);
    const at = read(path).split("\n")[Number(n) - 1];
    assert.ok(at !== undefined, `${id}: ${path} has no line ${n}`);
    assert.ok(at.includes(text), `${id}: ${where} reads ${JSON.stringify(at.trim())}, not ${JSON.stringify(text)}`);
  }
});

test("each address is one of the deployments' own, read with a call", () => {
  const known = [
    ...readdirSync(new URL("deployments/", root)).filter((f) => f.endsWith(".json")).map((f) => read(`deployments/${f}`)),
    read("app/config.js"),
  ].join("\n").toLowerCase();
  const addresses = rows().filter((r) => /^0x[0-9a-fA-F]{40}$/.test(r.where));
  assert.ok(addresses.length >= 4);
  for (const { id, where, text } of addresses) {
    assert.ok(known.includes(where.toLowerCase()), `${id}: ${where} is not in deployments/ or app/config.js`);
    assert.match(text, /^\w+\(\) → \S/, `${id}: say the call and its answer`);
  }
});

test("each page is Hyperliquid's own documentation, and nothing else is cited", () => {
  for (const { id, where } of rows()) {
    if (/^[\w./-]+:\d+$/.test(where) || /^0x[0-9a-fA-F]{40}$/.test(where)) continue;
    assert.match(where, /^https:\/\/hyperliquid\.gitbook\.io\/hyperliquid-docs\/[\w/-]+$/, `${id}: ${where}`);
  }
});
