// node --test app/tests/listtools.test.mjs
// The list's search, filters, sorting and pages. listTools() is not exported, and its module reads window.ethers on
// import, so it is lifted out of the source and run here on a small stand-in for the elements it touches.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { pct } from "../lib/ui.js";

const SOURCE = readFileSync(new URL("../views/pools.js", import.meta.url), "utf8");
const START = SOURCE.indexOf("function listTools(page) {");
const END = SOURCE.indexOf("\nexport async function listView");
const PER_PAGE_LINE = SOURCE.match(/export const PER_PAGE = (\d+);/);
assert.ok(START !== -1 && END > START && PER_PAGE_LINE,
  "app/views/pools.js moved listTools, listView or PER_PAGE: this test takes them from the source by those names");
const PER_PAGE = Number(PER_PAGE_LINE[1]);

class Box {
  constructor() {
    this.children = [];
    this.dataset = {};
    this.attrs = {};
    this.hidden = false;
    this.textContent = "";
  }
  replaceChildren(...kids) { this.children = kids; }
  querySelectorAll() { return this.children; }
  setAttribute(name, value) { this.attrs[name] = value; }
  removeAttribute(name) { delete this.attrs[name]; }
  scrollIntoView() {}
}

class Field extends Box {
  constructor(value = "") {
    super();
    this.value = value;
  }
}

/** A list page as listView renders it, with paint()'s part: the cards replaced, then listTools() called again. */
function listPage() {
  const form = new Box();
  form.elements = { q: new Field(), status: new Field(), dd: new Field(), sort: new Field("order-desc") };
  const boxes = {
    "#list-tools": form, "#pools": new Box(), "#others": new Box(), "#others-heading": new Box(), "#archive": new Box(),
    "#archive-block": new Box(), "#shown": new Box(), "#pager": new Box(),
  };
  const environment = {
    $: (selector) => boxes[selector],
    pct,
    PER_PAGE,
    FormData: class { constructor(f) { this.f = f; } get(name) { return this.f.elements[name].value; } },
    Option: class { constructor(label, value) { this.label = label; this.value = value; } },
    document: { createElement: () => new Box() },
  };
  const names = Object.keys(environment);
  const listTools = new Function(...names, `${SOURCE.slice(START, END)}\nreturn listTools;`)(...Object.values(environment));
  const paint = ({ live = [], others = [], archive = [] }) => {
    boxes["#pools"].replaceChildren(...live);
    boxes["#others"].replaceChildren(...others);
    boxes["#others-heading"].hidden = !others.length;
    boxes["#archive"].replaceChildren(...archive);
    boxes["#archive-block"].hidden = !archive.length;
    listTools({});
  };
  const type = (name, value) => {
    form.elements[name].value = value;
    form.oninput();
  };
  const seen = (box = "#pools") => boxes[box].children.filter((c) => !c.hidden);
  return { form, boxes, paint, type, seen, shown: () => boxes["#shown"].textContent, pager: () => boxes["#pager"].children };
}

function card(fields = {}) {
  const c = new Box();
  Object.assign(c.dataset, { search: "", status: "", dd: "1000", target: "1000", price: "1", funded: "1", held: "0", order: "0" }, fields);
  return c;
}
const many = (n) => Array.from({ length: n }, (_, i) => card({ order: String(i), search: `pool${i}` }));

test("a repainted list pages its own cards", () => {
  const page = listPage();
  page.paint({ live: many(45) });
  assert.equal(page.shown(), "1 to 20 of 45 (45 in all)");
  // paint() runs two or three times on each load of the list, each time with new cards.
  const cards = many(45);
  page.paint({ live: cards });
  assert.equal(page.pager().length, 3);
  page.pager()[1].onclick();
  assert.equal(page.shown(), "21 to 40 of 45 (45 in all)");
  assert.equal(page.pager().length, 3);
  assert.equal(page.seen().length, 20);
  assert.ok(page.seen().every((c) => cards.includes(c)));
  page.pager()[2].onclick();
  assert.equal(page.shown(), "41 to 45 of 45 (45 in all)");
  assert.equal(page.seen().length, 5);
});

test("a click on a page keeps the pager's buttons and moves the current mark", () => {
  const page = listPage();
  page.paint({ live: many(45) });
  const buttons = [...page.pager()];
  assert.deepEqual(buttons.map((b) => b.textContent), ["1", "2", "3"]);
  assert.equal(buttons[0].attrs["aria-current"], "page");
  buttons[1].onclick();
  // The button in focus is still on the page.
  assert.ok(page.pager().every((b, i) => b === buttons[i]));
  assert.deepEqual(buttons.map((b) => b.attrs["aria-current"]), [undefined, "page", undefined]);
});

test("a filter goes back to the first page, and a page count that changes rebuilds the pager", () => {
  const page = listPage();
  const live = many(45);
  live.forEach((c, i) => { c.dataset.search = i < 25 ? `aaa${i}` : `bbb${i}`; });
  page.paint({ live });
  page.pager()[2].onclick();
  page.type("q", "aaa");
  assert.equal(page.shown(), "1 to 20 of 25 (45 in all)");
  assert.equal(page.pager().length, 2);
  page.type("q", "aaa1");
  assert.equal(page.pager().length, 0);
  page.type("q", "nothing");
  assert.equal(page.shown(), "0 of 45");
  assert.equal(page.seen().length, 0);
});

test("a block the filters empty hides with its heading and comes back, and only the live pools are counted", () => {
  const page = listPage();
  const named = (n, name) => Array.from({ length: n }, (_, i) => card({ search: `${name}${i}` }));
  page.paint({ live: named(5, "live"), others: named(2, "bench"), archive: named(3, "arch") });
  assert.equal(page.shown(), "1 to 5 of 5 (5 in all)");
  page.type("q", "live");
  assert.equal(page.boxes["#others-heading"].hidden, true);
  assert.equal(page.boxes["#archive-block"].hidden, true);
  page.type("q", "arch");
  assert.equal(page.shown(), "0 of 5");
  assert.equal(page.boxes["#others-heading"].hidden, true);
  assert.equal(page.boxes["#archive-block"].hidden, false);
  assert.equal(page.seen("#archive").length, 3);
  page.type("q", "");
  assert.equal(page.boxes["#others-heading"].hidden, false);
  assert.equal(page.boxes["#archive-block"].hidden, false);
  assert.equal(page.shown(), "1 to 5 of 5 (5 in all)");
});

test("search and the two filters fit a card together, and a search is read as the cards keep it", () => {
  const page = listPage();
  const a = card({ search: "0xabc btc", status: "Open", dd: "500" });
  const b = card({ search: "0xdef btc", status: "Open", dd: "1000" });
  const c = card({ search: "0xabc eth", status: "Funded", dd: "1000" });
  page.paint({ live: [a, b, c] });
  page.type("q", "  0xABC ");
  assert.deepEqual(new Set(page.seen()), new Set([a, c]));
  page.type("status", "Open");
  assert.deepEqual(page.seen(), [a]);
  page.type("q", "");
  assert.deepEqual(new Set(page.seen()), new Set([a, b]));
  page.type("dd", "1000");
  assert.deepEqual(page.seen(), [b]);
});

test("the filters offer what the cards hold, and a choice outlives a repaint", () => {
  const page = listPage();
  const { status, dd } = page.form.elements;
  const paintAll = () => page.paint({ live: [card({ status: "Open", dd: "1000" }), card({ dd: "500" })],
    archive: [card({ status: "Closed", dd: "250" })] });
  paintAll();
  assert.deepEqual(status.children.map((o) => o.value), ["", "Closed", "Open"]);
  assert.deepEqual(dd.children.map((o) => o.value), ["", "250", "500", "1000"]);
  assert.deepEqual(dd.children.map((o) => o.label), ["All", "2.50%", "5%", "10%"]);
  status.value = "Open";
  paintAll();
  assert.equal(status.value, "Open");
  page.paint({ live: [card({ status: "Funded" })] });
  assert.equal(status.value, "");
});

test("sorting goes by the card's figure as a number, each way", () => {
  const page = listPage();
  const named = (order, price) => card({ order, price, search: order });
  const cards = [named("9", "300"), named("10", "20"), named("2", "1000")];
  page.paint({ live: cards });
  const orders = () => page.seen().map((c) => c.dataset.order);
  assert.deepEqual(orders(), ["10", "9", "2"]);
  page.type("sort", "price-asc");
  assert.deepEqual(orders(), ["10", "9", "2"]);
  page.type("sort", "price-desc");
  assert.deepEqual(orders(), ["2", "9", "10"]);
  page.type("sort", "order-asc");
  assert.deepEqual(orders(), ["2", "9", "10"]);
});

test("each sort names a figure the card carries, and Newest comes first", () => {
  const options = [...SOURCE.matchAll(/<option value="(\w+)-(asc|desc)">/g)].map((m) => m[1]);
  assert.ok(options.length >= 6);
  assert.equal(options[0], "order");
  assert.match(SOURCE, /<select name="sort">\s+<option value="order-desc">Newest<\/option>/);
  for (const key of options) assert.match(SOURCE, new RegExp(`card\\.dataset\\.${key} = `));
});

test("the search form stays on the page, from script and with no handler in the markup", () => {
  const page = listPage();
  page.paint({ live: many(1) });
  let held = false;
  page.form.onsubmit({ preventDefault() { held = true; } });
  assert.equal(held, true);
  assert.doesNotMatch(SOURCE, /<form[^>]*\son\w+=/);
});
