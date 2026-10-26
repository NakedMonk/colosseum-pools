// node --test app/tests/lending.test.mjs
//
// The Economics page shows two scenarios, base and good, and in place of the third a line: how many buyers a month
// the pool needs to earn what lending stablecoins pays. The table keeps the third scenario's cells; only the page
// leaves them out. The number on the line is the model's own answer, worked out on the page, never written in.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { evaluate, defaultLayout } from "../lib/calc.js";
import { SHOWN_SCENARIOS, LENDING, NEXT_VERSION, lendingDemand, results, specFrom } from "../views/economics.js";

const tables = JSON.parse(readFileSync(new URL("../data/calc_tables.json", import.meta.url), "utf8"));
const words = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#39;/g, "'").replace(/\s+/g, " ");
const spec = (pool) => specFrom({ mode: "default", pool: String(pool), chmode: "real", assetList: "default" });
const page = (pool) => {
  const s = spec(pool);
  const runs = Object.fromEntries(SHOWN_SCENARIOS.map((scenario) => [scenario, evaluate(tables, { ...s, scenario })]));
  return words(results(tables, runs, lendingDemand(tables, s)));
};

test("the page shows base and good; the table still holds bad, cell for cell", () => {
  assert.deepEqual(SHOWN_SCENARIOS, ["base", "good"]);
  assert.ok(tables.scenarios.bad, "the scenario stays in the table");
  const cells = (name) => Object.keys(tables.cells).filter((k) => k.startsWith(`${name}|`)).length;
  assert.ok(cells("bad") > 0);
  assert.equal(cells("bad"), cells("base"));
  const text = page(100_000);
  assert.match(text, /Investor's return, a year, before the platform's charges base 16\.6% · good 35\.2%/);
  // "bad" as a word nowhere in what the page says (the warning badges use it only as a class, which words() drops).
  assert.doesNotMatch(text, /\bbad\b/);
  assert.doesNotMatch(text, /4\.4%/);
  assert.match(text, /at the two scenarios named above/);
  const source = readFileSync(new URL("../views/economics.js", import.meta.url), "utf8");
  assert.match(source, /runs = SHOWN_SCENARIOS\.map\(\(scenario\) => \[scenario, evaluate\(tables, \{ \.\.\.spec, scenario \}\)\]\);/);
});

test("the demand line: the buyers a month at which the pool earns what lending stablecoins pays", () => {
  assert.equal(LENDING.rate, 0.05);
  assert.match(LENDING.source, /^DeFiLlama, 30 Sep 2026, 30-day yields: Maple 5\.1%, Fluid 5\.1%, Aave 4% to 5\.6%$/);
  const s = spec(100_000);
  const d = lendingDemand(tables, s);
  // Held to the model from outside the search: at that demand the base scenario earns 5%, a little less earns less
  // and a little more earns more.
  const at = (demand) => evaluate(tables, { ...s, scenario: "base", demand_per_100k: demand }).annual_return;
  assert.ok(Math.abs(at(d) - 0.05) < 1e-9, `${at(d)}`);
  assert.ok(at(d - 0.1) < 0.05 && at(d + 0.1) > 0.05);
  const text = page(100_000);
  assert.match(text, /To earn what lending stablecoins pays, 5% a year 3\.8 buyers a month for every \$90,000 of seats; the base scenario assumes 15/);
  assert.doesNotMatch(text, /for this pool's/, "a $100,000 pool has exactly $90,000 of seats");
  assert.match(text, /\(DeFiLlama, 30 Sep 2026, 30-day yields: Maple 5\.1%, Fluid 5\.1%, Aave 4% to 5\.6%\)/);
  // A larger pool: the same scale, and what it comes to for the pool's own seats.
  assert.match(page(500_000), /3\.6 buyers a month for every \$90,000 of seats \(17\.9 for this pool's \$450,000\)/);
});

test("the demand line says so when no demand gets there, and moves with the table", () => {
  const s = spec(100_000);
  const runs = Object.fromEntries(SHOWN_SCENARIOS.map((scenario) => [scenario, evaluate(tables, { ...s, scenario })]));
  assert.match(words(results(tables, runs, null)), /no demand the model takes brings this pool to 5% a year/);
  const t = JSON.parse(JSON.stringify(tables));
  t.scenarios.base.demand_per_100k = 20;
  assert.match(words(results(t, runs, lendingDemand(t, s))), /the base scenario assumes 20/);
  assert.deepEqual(defaultLayout(100_000).seats.reduce((a, g) => a + g.F * g.count, 0), tables.demand.reference_seat_capital);
});

test("next to the demo's return, the next version's: the model's, after the platform's charges, and said to be a model", () => {
  // A snapshot of the model of 16 Oct 2026: 14.2% a year after the charges, base, a $100 000 pool, the account closed
  // at +10%. No table on the page computes it, so the page says it is a snapshot (ALGO, 21 Oct 2026).
  assert.deepEqual(NEXT_VERSION, { rate: 0.142, pool: 100_000, target: 0.10, asOf: "16 October 2026" });
  const note = "These figures model the design deployed on testnet, where a challenge trades with the pool's own money. "
    + "The next version, a virtual exam and many traders to one pool, is being built: its model, as it stood on "
    + "16 October 2026, gave the investor about 14% a year after the platform's charges, on a $100,000 pool whose "
    + "funded account closes at its 10% target, base scenario. A snapshot of that model, not worked out on this page, "
    + "and not a promise.";
  for (const pool of [100_000, 500_000, 1_000_000]) {
    const text = page(pool);
    assert.ok(text.includes(note), String(pool));
    // Right under the demo's own return, which stays as it was.
    assert.ok(text.indexOf("Investor's return, a year, before the platform's charges") < text.indexOf(note));
    assert.ok(text.indexOf(note) < text.indexOf("To earn what lending stablecoins pays"));
  }
  assert.match(page(100_000), /before the platform's charges base 16\.6% · good 35\.2%/);
  // The page's opening says its numbers are the design at scale, not the single-seat demo pool; the note agrees.
  assert.doesNotMatch(page(100_000), /figures are for the demo/);
});


test("in paper mode the notes say the virtual exam, not the deployed design and not a tenth of the seat at risk", () => {
  // CodeRabbit, cp#159: the model gives a paper challenge no capital, so the page's own words must follow the mode.
  const s = specFrom({ mode: "default", pool: "100000", chmode: "demo", assetList: "default" });
  const runs = Object.fromEntries(SHOWN_SCENARIOS.map((scenario) => [scenario, evaluate(tables, { ...s, scenario })]));
  const paper = words(results(tables, runs, lendingDemand(tables, s), s.mode));
  assert.ok(paper.includes("These figures model the paper exam: a virtual exam on a counter in the contract, where a "
    + "failed exam leaves the pool's capital untouched. The contracts deployed on testnet do not run it; it belongs "
    + "to the next version. The next version, a virtual exam and many traders to one pool, is being built"), paper);
  assert.ok(paper.includes("a failed exam costs the pool nothing, because the exam is a counter in the contract and "
    + "the pool's capital is not touched, while a failed funded seat risks the whole seat"));
  assert.doesNotMatch(paper, /where a challenge trades with the pool's own money|risks a tenth of the seat/);
  // And the page hands the form's mode to the notes.
  const view = readFileSync(new URL("../views/economics.js", import.meta.url), "utf8");
  assert.match(view, /results\(tables, Object\.fromEntries\(runs\), lendingDemand\(tables, spec\), spec\.mode\)/);
});
