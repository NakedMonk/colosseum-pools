// node --test app/tests/analytics.test.mjs
//
// The site's visitor count: Umami, run by us at usenami.io, for pools.usenami.io (the CTO, 27 October
// 2026). One script, the last line of the head, deferred, with this site's own id: a page without it
// counts nobody, and one with another id counts its visitors into another site.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// Read as LF whatever the checkout's line endings (Gemini on cp#176).
const page = readFileSync(new URL("../index.html", import.meta.url), "utf8").replace(/\r\n/g, "\n");
const SITE = "ec3433e6-cfc2-4459-bc96-4e104ec635ca";
const TAG = `<script defer src="https://usenami.io/umami/script.js" data-website-id="${SITE}"></script>`;

test("the head ends with Umami's script, deferred, with pools.usenami.io's own id", () => {
  assert.ok(page.includes(`  ${TAG}\n</head>`), "the script is the line before </head>");
  assert.equal(page.split(TAG).length - 1, 1, "and is there once");
});

test("no other site's id and no second copy of the script anywhere on the page", () => {
  const ids = [...page.matchAll(/data-website-id="([^"]*)"/g)].map((m) => m[1]);
  assert.deepEqual(ids, [SITE]);
  assert.equal([...page.matchAll(/umami\/script\.js/g)].length, 1);
});
