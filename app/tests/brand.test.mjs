// node --test app/tests/brand.test.mjs
//
// The v3 mark on the site (the CTO, 28 October 2026): the tab's icon, the phone's home-screen icon,
// the picture a shared link shows, and the mark in the header. The set is the one the CTO accepted on
// 27 October, after Marketing measured it. Each tag names a file the site has, and each file is the
// accepted one: a tag without its file, a file under another name, or another picture is red.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";

const app = (rel) => new URL(`../${rel}`, import.meta.url);
// Read as LF whatever the checkout's line endings, as analytics.test.mjs does.
const page = readFileSync(app("index.html"), "utf8").replace(/\r\n/g, "\n");
const SITE = "https://pools.usenami.io/";

// The accepted files, by sha-256, with the size each one is drawn at.
const FILES = {
  "favicon.ico": { sha256: "a0f0618303e1ccbbed30e23a0c02d6e1c8fa7cfbdc70977bbd53889f6ee0284c", ico: [16, 32, 48] },
  "brand/favicon-16.png": { sha256: "5ad8daeee6dc907966463235796b00067a5e4964b4e335dd2a367ea25f271552", png: [16, 16] },
  "brand/favicon-32.png": { sha256: "6fb8f81c1045cd50b9d8c983da318844d8877ddb98a42869f63166a63c5f2263", png: [32, 32] },
  "brand/favicon-48.png": { sha256: "434e620d1429a4a021c47af6f67ab3a17fb15e3ae85e92121e2f3e23bed0f6a9", png: [48, 48] },
  "brand/apple-touch-180.png": { sha256: "068bf66f46e1f27115f51ba49592287837571a5010150ef327d2d49db9778723", png: [180, 180] },
  "brand/og-image-1200x630.png": { sha256: "3eedcde0c50cd1d58aa4bbf24033fea5d319ee06562a118235173ef18d5e50cb", png: [1200, 630] },
  "brand/mark-small.svg": { sha256: "7e45c72bce04e992eac9d170f8d326e847c6bf0fb082135ac4aa95e267b3a826" },
};

const ICONS = [
  ['<link rel="icon" href="favicon.ico" sizes="16x16 32x32 48x48">', "favicon.ico"],
  ['<link rel="icon" type="image/png" sizes="16x16" href="brand/favicon-16.png">', "brand/favicon-16.png"],
  ['<link rel="icon" type="image/png" sizes="32x32" href="brand/favicon-32.png">', "brand/favicon-32.png"],
  ['<link rel="icon" type="image/png" sizes="48x48" href="brand/favicon-48.png">', "brand/favicon-48.png"],
  ['<link rel="apple-touch-icon" sizes="180x180" href="brand/apple-touch-180.png">', "brand/apple-touch-180.png"],
];
const OG_IMAGE = "brand/og-image-1200x630.png";
const MARK = '<a class="brand" href="#/"><img src="brand/mark-small.svg" alt="" width="24" height="24">Pools';

const once = (tag) => page.split(tag).length - 1;
const head = page.slice(0, page.indexOf("</head>"));

test("the head names the tab's and the home screen's icons, once each, and each one's file is there", () => {
  for (const [tag, file] of ICONS) {
    assert.equal(once(tag), 1, tag);
    assert.ok(head.includes(tag), `${tag} is in the head`);
    assert.ok(existsSync(app(file)), file);
  }
});

test("a shared link shows the v3 picture: og:image on the site's own address, and a large card", () => {
  assert.equal(once(`<meta property="og:image" content="${SITE}${OG_IMAGE}">`), 1);
  assert.ok(existsSync(app(OG_IMAGE)), OG_IMAGE);
  const [w, h] = FILES[OG_IMAGE].png;
  assert.equal(once(`<meta property="og:image:width" content="${w}">`), 1);
  assert.equal(once(`<meta property="og:image:height" content="${h}">`), 1);
  assert.equal(once('<meta name="twitter:card" content="summary_large_image">'), 1);
  assert.equal(once(`<meta property="og:url" content="${SITE}">`), 1);
  // The card says what the page says: the same title and the same description, never a second wording.
  // Each tag has to be there: two missing tags would compare undefined with undefined and pass.
  const meta = (re) => {
    const found = head.match(re);
    assert.ok(found, `${re} is in the head`);
    return found[1];
  };
  assert.equal(meta(/<meta property="og:title" content="([^"]+)">/), meta(/<title>([^<]+)<\/title>/));
  assert.equal(meta(/<meta property="og:description" content="([^"]+)">/), meta(/<meta name="description" content="([^"]+)">/));
});

test("the header shows the mark beside the site's name", () => {
  assert.equal(once(MARK), 1);
  assert.ok(existsSync(app("brand/mark-small.svg")));
});

test("no tag names a file the site does not have", () => {
  const local = [...page.matchAll(/(?:href|src)="([^"#][^"]*)"/g)].map((m) => m[1]).filter((u) => !/^https?:/.test(u));
  const own = [...page.matchAll(/content="https:\/\/pools\.usenami\.io\/([^"]+)"/g)].map((m) => m[1]);
  assert.ok(own.length >= 1, "og:image is on the site's own address");
  for (const file of [...local, ...own]) assert.ok(existsSync(app(file)), file);
});

test("the files are the accepted v3 set, each at its size", () => {
  for (const [file, want] of Object.entries(FILES)) {
    const bytes = readFileSync(app(file));
    assert.equal(createHash("sha256").update(bytes).digest("hex"), want.sha256, file);
    if (want.png) {
      assert.equal(bytes.subarray(1, 4).toString("latin1"), "PNG", file);
      assert.deepEqual([bytes.readUInt32BE(16), bytes.readUInt32BE(20)], want.png, file);
    }
    if (want.ico) {
      assert.equal(bytes.readUInt16LE(2), 1, `${file} is an icon file`);
      const sizes = Array.from({ length: bytes.readUInt16LE(4) }, (_, i) => bytes[6 + 16 * i] || 256);
      assert.deepEqual(sizes, want.ico, file);
    }
  }
});
