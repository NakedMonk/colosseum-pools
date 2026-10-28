// node --test app/tests/contacts.test.mjs
//
// A pool owner's contacts (app/lib/contacts.js, gateway/contacts.py): the page shows a contact only in its one form,
// checked by the same strings the gateway checks with, and the owner's wallet signs the very text the gateway reads.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  FIELDS, FORM_SOURCES, LINK_REL, contactError, contactLinks, contactsMessage, contactsUrl, normalizeContact,
  readContacts, saveContacts,
} from "../lib/contacts.js";

const text = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
// The at sign is put back where the cases file writes {at}, so the repository holds no e-mail address.
const CASES = JSON.parse(text("../../gateway/tests/contacts-cases.json").replaceAll("{at}", "@"));
const EMAIL = ["team", "example.org"].join("@");
const POOL = "0x69e88Dbe895AfbbD773CB60A09d1113777a6DfB0";
const NONCE = 1793140000000;

test("the page checks a contact by the gateway's own strings", () => {
  const py = text("../../gateway/contacts.py");
  const block = py.slice(py.indexOf("FORMS = {"), py.indexOf("}\n", py.indexOf("FORMS = {")));
  const forms = Object.fromEntries([...block.matchAll(/"(\w+)": re\.compile\(r"([^"]+)"\)/g)].map((m) => [m[1], m[2]]));
  assert.deepEqual(forms, FORM_SOURCES);
  assert.deepEqual(Object.keys(forms), FIELDS);
  assert.match(py, /^FIELDS = \("discord", "telegram", "x", "email"\)$/m);
});

test("each contact in its one form, and nothing else", () => {
  for (const [field, values] of Object.entries(CASES.accept)) {
    for (const v of values) assert.equal(contactError(field, v), null, `${field} ${v}`);
  }
  for (const [field, values] of Object.entries(CASES.refuse)) {
    for (const v of values) assert.match(contactError(field, v), /: /, `${field} ${v}`);
  }
  assert.equal(contactError("x", ""), null, "every contact is optional");
  assert.match(contactError("email", ["a".repeat(190), "example.org"].join("@")), /^Email: /);
});

test("a record shows only links in their form: https as given, an email as mailto:", () => {
  const record = { contacts: {
    discord: "https://discord.gg/abc123", telegram: "javascript:alert(1)", x: "https://x.com/example",
    email: EMAIL, website: "https://example.org",
  } };
  assert.deepEqual(contactLinks(record), [
    { field: "discord", label: "Discord", href: "https://discord.gg/abc123", text: "discord.gg/abc123" },
    { field: "x", label: "X", href: "https://x.com/example", text: "x.com/example" },
    { field: "email", label: "Email", href: `mailto:${EMAIL}`, text: EMAIL },
  ]);
  for (const [field, values] of Object.entries(CASES.refuse)) {
    for (const v of values) assert.deepEqual(contactLinks({ contacts: { [field]: v } }), [], `${field} ${v}`);
  }
  assert.deepEqual(contactLinks(null), []);
  assert.deepEqual(contactLinks({ contacts: { x: ["https://x.com/example"] } }), []);
  assert.equal(LINK_REL, "noopener noreferrer nofollow");
});

test("what the owner types becomes the form the gateway keeps", () => {
  assert.equal(normalizeContact("x", "@example"), "https://x.com/example");
  assert.equal(normalizeContact("telegram", " @example_pool "), "https://t.me/example_pool");
  assert.equal(normalizeContact("telegram", "https://t.me/example_pool/"), "https://t.me/example_pool");
  assert.equal(normalizeContact("x", "https://x.com/example//"), "https://x.com/example");
  assert.equal(normalizeContact("email", ` ${EMAIL} `), EMAIL);
  assert.equal(normalizeContact("discord", undefined), "");
});

test("the wallet signs the text the gateway reads: pool, the four contacts, nonce, in that order", () => {
  assert.equal(contactsMessage(POOL, { x: "https://x.com/example" }, NONCE),
    `{"pool":"${POOL}","discord":"","telegram":"","x":"https://x.com/example","email":"","nonce":${NONCE}}`);
  const py = text("../../gateway/contacts.py");
  assert.match(py, /^KEYS = frozenset\(\("pool", "nonce", \*FIELDS\)\)$/m);
});

test("the gateway's path for contacts, on the orders' rule of https", () => {
  assert.equal(contactsUrl("https://pools-api.usenami.io", POOL), `https://pools-api.usenami.io/v1/contacts?pool=${POOL}`);
  assert.equal(contactsUrl("https://pools-api.usenami.io/demo3/"), "https://pools-api.usenami.io/demo3/v1/contacts");
  assert.equal(contactsUrl("http://127.0.0.1:8787", POOL), `http://127.0.0.1:8787/v1/contacts?pool=${POOL}`);
  assert.throws(() => contactsUrl("http://pools-api.usenami.io", POOL), /https/);
});

test("saving signs the owner's contacts and sends the signed text; one out of its form is refused before the wallet", async () => {
  const signed = [];
  const sent = [];
  const signer = { signMessage: async (m) => { signed.push(m); return `0x${"1".repeat(130)}`; } };
  const real = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    sent.push({ url: String(url), init });
    return { ok: true, status: 200, json: async () => ({ status: "kept", kept: true }) };
  };
  try {
    const out = await saveContacts(signer, POOL, { x: "@example", email: EMAIL },
      { base: "https://pools-api.usenami.io", now: () => NONCE });
    assert.deepEqual(out, { http: 200, status: "kept", kept: true });
    assert.deepEqual(signed, [contactsMessage(POOL, { x: "https://x.com/example", email: EMAIL }, NONCE)]);
    assert.equal(sent[0].url, "https://pools-api.usenami.io/v1/contacts");
    assert.equal(sent[0].init.method, "POST");
    assert.deepEqual(JSON.parse(sent[0].init.body), { message: signed[0], signature: `0x${"1".repeat(130)}` });
    await assert.rejects(saveContacts(signer, POOL, { telegram: "https://telegram.me/example_pool" },
      { base: "https://pools-api.usenami.io" }), /^Error: Telegram: /);
    assert.equal(signed.length, 1, "the wallet was not asked for a contact the gateway would refuse");
  } finally {
    globalThis.fetch = real;
  }
});

test("reading a pool's record: none is null, an error says the gateway's words, a page not its answer is no record", async () => {
  const real = globalThis.fetch;
  const answers = [
    { ok: false, status: 404, json: async () => ({ code: "no_contacts" }) },
    { ok: true, status: 200, json: async () => ({ status: "ok", pool: POOL, contacts: { x: "https://x.com/example" } }) },
    { ok: false, status: 400, json: async () => ({ code: "bad_pool", detail: "the pool is an address of 40 hex digits" }) },
    { ok: true, status: 200, json: async () => { throw new SyntaxError("Unexpected token <"); } },
  ];
  const asked = [];
  globalThis.fetch = async (url) => { asked.push(String(url)); return answers.shift(); };
  try {
    assert.equal(await readContacts(POOL, "https://pools-api.usenami.io"), null);
    assert.equal((await readContacts(POOL, "https://pools-api.usenami.io")).contacts.x, "https://x.com/example");
    await assert.rejects(readContacts("0x1", "https://pools-api.usenami.io"), /40 hex digits/);
    await assert.rejects(readContacts(POOL, "https://pools-api.usenami.io"), /answer \(200\) is not one it gives/);
    assert.equal(asked[0], `https://pools-api.usenami.io/v1/contacts?pool=${POOL}`);
  } finally {
    globalThis.fetch = real;
  }
});
