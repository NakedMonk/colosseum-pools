// One offer in a few variants: pools of one owner that sell the same challenge on the same capital and differ only in
// what a buyer chooses between, its price, its profit target and its drawdown. The list shows such pools as one card
// with a button for each.
//
// Nothing on chain says pools belong together, and the contracts are not changed for it: a series is read from what
// the list already holds. Each pool keeps its own capital (Pool.buyChallenge holds capital + fundedCapital on the
// pool's own spot), so a series of three is three full capitals, and up to three challenges can run on them at once.
// No browser globals here, so node's test runner loads it.

/** What joins the values of a card's list in its dataset: a status name has spaces in it. */
export const LIST = "|";

/** How many variants a card offers at most. More than that stays as separate cards: nothing is hidden. */
export const MAX_VARIANTS = 3;

/** What variants of one offer may differ in: the buyer's choice. */
export const CHOICE = ["price", "targetBps", "maxDrawdownBps"];

/** Everything else of a pool, which its variants share: who runs it, on what money, for how long and under which
 *  rules. An item from the list (app/views/pools.js, readPoolChain) as it holds them. */
export function shared(item) {
  const { terms, rules } = item;
  return JSON.stringify([
    item.deployment?.label ?? null,
    String(item.owner).toLowerCase(),
    String(terms.capital), String(terms.fundedCapital), String(terms.duration),
    String(terms.traderShareChallengeBps), String(terms.traderShareFundedBps),
    String(rules.dailyLossBps), String(rules.maxLeverageX100),
    [...rules.assets].map(String),
    [...(item.caps ?? [])].map(String),
  ]);
}

/** The buyer's choice of one pool, as a key: two pools with the same one are copies, not variants. */
export function choice(item) {
  return JSON.stringify([String(item.terms.price), String(item.terms.targetBps), String(item.rules.maxDrawdownBps)]);
}

/** Which pools may join a series: an investor's pool of a live deployment. A bench of ours or an archived pool never
 *  does, so the series the list shows is always someone's offer. */
export function mayJoin(item) {
  return item.kind === "pool" && !item.archived;
}

/**
 * The list's pools as cards, in the order given: each entry is `{ variants: [item, …] }`, one item for a pool on its
 * own and two or three for a series, its variants cheapest first. A series stands where its first pool stood.
 * A group that holds two pools with the same choice, or more than MAX_VARIANTS, is not a series: its pools stay
 * separate cards.
 */
export function seriesOf(items) {
  const groups = new Map();
  for (const item of items) {
    if (!mayJoin(item)) continue;
    const key = shared(item);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  const series = new Map();
  for (const group of groups.values()) {
    if (group.length < 2 || group.length > MAX_VARIANTS) continue;
    if (new Set(group.map(choice)).size !== group.length) continue;
    const variants = [...group].sort((a, b) => cmp(a.terms.price, b.terms.price) || cmp(a.index, b.index));
    for (const item of group) series.set(item, variants);
  }
  const out = [];
  const placed = new Set();
  for (const item of items) {
    const variants = series.get(item);
    if (!variants) {
      out.push({ variants: [item] });
    } else if (!placed.has(variants)) {
      placed.add(variants);
      out.push({ variants });
    }
  }
  return out;
}

function cmp(a, b) {
  const x = BigInt(a);
  const y = BigInt(b);
  return x < y ? -1 : x > y ? 1 : 0;
}

/**
 * What the list's search, filters and sorting read from a card (app/views/pools.js, listTools), for a card of
 * several variants. `each` is what one pool's card would carry on its own, the same names as its dataset. A card fits
 * a filter if any variant does, so status and drawdown are lists, joined by LIST (a status name has spaces in it); a
 * sort reads the variant that answers it best: the lowest price, the highest target, the lowest drawdown (the least of
 * the list), the least held, the newest pool.
 */
export function seriesDataset(each) {
  const list = (key) => [...new Set(each.map((d) => d[key] ?? "").filter((v) => v !== ""))].join(LIST);
  // A figure no variant has, or one that is not a number, is left empty rather than written as Infinity or NaN.
  const best = (pick, key) => {
    const nums = each.map((d) => Number(d[key] ?? NaN)).filter(Number.isFinite);
    return nums.length ? String(pick(...nums)) : "";
  };
  return {
    search: each.map((d) => d.search ?? "").join(" "),
    status: list("status"),
    dd: list("dd"),
    target: best(Math.max, "target"),
    price: best(Math.min, "price"),
    funded: best(Math.max, "funded"),
    held: best(Math.min, "held"),
    order: best(Math.max, "order"),
  };
}
