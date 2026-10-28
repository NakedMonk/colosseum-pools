// A pool owner's contacts for its traders and co-investors, kept by the gateway and signed by the owner's wallet
// (gateway/contacts.py; Alex's word to Grok, the CTO's design on 28 Oct 2026). Nothing of it is on chain.
//
// The page shows a contact only as one of the links below, checked here again whatever the gateway answered:
// the hosts are the CTO's list, the scheme https alone, and nothing after the name. No script, no picture,
// no preview of the other site. A view that renders a link still passes its href and text through esc() and
// gives it LINK_REL.
import { CONFIG } from "../config.js";
import { orderUrl } from "./gateway.js";

export const FIELDS = ["discord", "telegram", "x", "email"];

// The very strings of FORMS in gateway/contacts.py, each matched whole; app/tests/contacts.test.mjs holds them equal.
export const FORM_SOURCES = {
  discord: String.raw`https://(?:discord\.gg/|discord\.com/invite/)[A-Za-z0-9-]{2,32}`,
  telegram: String.raw`https://t\.me/(?:[A-Za-z][A-Za-z0-9_]{4,31}|\+[A-Za-z0-9_-]{8,40})`,
  x: String.raw`https://(?:x|twitter)\.com/[A-Za-z0-9_]{1,15}`,
  email: String.raw`[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,24}`,
};
const FORMS = Object.fromEntries(Object.entries(FORM_SOURCES).map(([k, src]) => [k, new RegExp(`^(?:${src})$`)]));
const MAX_FIELD = 200;

const LABELS = { discord: "Discord", telegram: "Telegram", x: "X", email: "Email" };
const SHAPES = {
  discord: "an invite link, https://discord.gg/… or https://discord.com/invite/…",
  telegram: "https://t.me/ and the name or the invite",
  x: "https://x.com/ and the handle",
  email: "an e-mail address",
};
export const LINK_REL = "noopener noreferrer nofollow";

/** What the owner typed, in the one form the gateway keeps: trimmed, a handle given as @name made a link. */
export function normalizeContact(field, raw) {
  let value = String(raw ?? "").trim();
  if (!value) return "";
  if (field === "x" && /^@\w{1,15}$/.test(value)) value = `https://x.com/${value.slice(1)}`;
  if (field === "telegram" && /^@\w{5,32}$/.test(value)) value = `https://t.me/${value.slice(1)}`;
  if (field !== "email") value = value.replace(/\/$/, "");
  return value;
}

/** Null for a contact the gateway keeps, or what is wrong with it, in words for the form. */
export function contactError(field, value) {
  if (value === "") return null;
  if (typeof value !== "string" || value.length > MAX_FIELD || !FORMS[field].test(value)) {
    return `${LABELS[field]}: ${SHAPES[field]}`;
  }
  return null;
}

/** The text the owner's wallet signs: the gateway reads these keys and no other. */
export function contactsMessage(pool, contacts, nonce) {
  const given = Object.fromEntries(FIELDS.map((f) => [f, contacts[f] ?? ""]));
  return JSON.stringify({ pool, ...given, nonce });
}

/** Where the gateway answers for one pool: the orders' rule, https or this machine alone. */
export function contactsUrl(base, pool) {
  const url = new URL(orderUrl(base));
  url.pathname = url.pathname.replace(/\/v1\/order$/, "/v1/contacts");
  if (pool !== undefined) url.searchParams.set("pool", pool);
  return url.toString();
}

/** The links a pool's record may show, in the order of FIELDS: only a contact in its form, an email as mailto:. */
export function contactLinks(record) {
  const contacts = (record && record.contacts) || {};
  return FIELDS.filter((f) => typeof contacts[f] === "string" && contacts[f] && contactError(f, contacts[f]) === null)
    .map((f) => ({
      field: f,
      label: LABELS[f],
      href: f === "email" ? `mailto:${contacts[f]}` : contacts[f],
      text: f === "email" ? contacts[f] : contacts[f].replace(/^https:\/\//, ""),
    }));
}

/** The pool's last record, or null when its owner has left none or this gateway keeps none. */
export async function readContacts(pool, base = CONFIG.gateway) {
  const res = await fetch(contactsUrl(base, pool));
  if (res.status === 404) return null;
  const payload = await res.json().catch(() => ({ status: "bad_response" }));
  if (!res.ok) throw new Error(payload.detail || `the gateway answered ${res.status}`);
  return payload;
}

/**
 * Signs the owner's contacts with the wallet that owns the pool and sends them; a contact out of its form is
 * refused here, before the wallet is asked. The nonce is the time in milliseconds: the gateway keeps a record
 * only above the pool's last one.
 */
export async function saveContacts(signer, pool, typed, { base = CONFIG.gateway, now = Date.now } = {}) {
  const contacts = Object.fromEntries(FIELDS.map((f) => [f, normalizeContact(f, typed[f])]));
  const wrong = FIELDS.map((f) => contactError(f, contacts[f])).filter(Boolean);
  if (wrong.length) throw new Error(wrong.join("; "));
  const message = contactsMessage(pool, contacts, now());
  const signature = await signer.signMessage(message);
  const res = await fetch(contactsUrl(base), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, signature }),
  });
  const payload = await res.json().catch(() => ({ status: "bad_response" }));
  return { http: res.status, ...payload };
}
