// The deployments the site reads (app/config.js): which one is live, which is the archive, and which one an address
// belongs to. No chain reads here; chain.js asks the factories and hands the answers in.
import { CONFIG } from "../config.js";

export const DEPLOYMENTS = CONFIG.deployments;

/** The deployment the gateway and the keepers serve: where pools are bought, traded and opened. */
export function liveDeployment(list = DEPLOYMENTS) {
  return list.find((d) => d.role === "live") ?? null;
}

/** The dex an order number's perp trades on: 0 for Hyperliquid's own, else a builder's (100000 + dex × 10000 + index). */
export function dexOf(asset) {
  return Number(asset) < 100000 ? 0 : Math.floor((Number(asset) - 100000) / 10000);
}

/**
 * Each asset's own leverage cap, times 100, by its coin: the platform's defaults (the CTO, 21 Oct 2026), the ones
 * ops/leverage.py gives a v3 pool's rules. A coin not named here takes the account's cap.
 */
export const ASSET_LEVERAGE_X100 = { BTC: 1000, ETH: 1000, SOL: 500, HYPE: 500, SP500: 1000, XYZ100: 800, CL: 300, BRENTOIL: 200 };

/**
 * The rules createPool takes on `deployment`'s factory. Before v3, the four fields. On a v3 factory (`assetLeverage`
 * in app/config.js) each asset's own cap after them, in the order of `rules.assets`, never over the account's cap:
 * the factory refuses a cap over it, or under 1x. `names` are the assets' names in the same order ("BTC",
 * "xyz:XYZ100"); a builder dex's market goes by its coin.
 */
export function poolRules(deployment, rules, names) {
  const { dailyLossBps, maxDrawdownBps, maxLeverageX100, assets } = rules;
  const out = { dailyLossBps, maxDrawdownBps, maxLeverageX100, assets };
  if (!deployment?.assetLeverage) return out;
  // A v3 account trades on one dex, where its capital is: the factory refuses rules that mix them (BadRules).
  if (new Set(assets.map(dexOf)).size > 1) {
    throw new Error("A pool's assets all trade on one dex: Hyperliquid's own, or one builder's such as xyz. Pick them from one.");
  }
  const coin = (name) => String(name ?? "").split(":").pop();
  out.assetLeverageX100 = assets.map((_, i) => Math.min(ASSET_LEVERAGE_X100[coin(names[i])] ?? maxLeverageX100, maxLeverageX100));
  return out;
}

/** "BTC 10×, ETH 10×, SOL 5×": the platform's default cap of each of `names` that has one, as the new-pool form says it. */
export function defaultCapsText(names) {
  const coin = (name) => String(name ?? "").split(":").pop();
  return names.filter((n) => coin(n) in ASSET_LEVERAGE_X100).map((n) => `${n} ${ASSET_LEVERAGE_X100[coin(n)] / 100}×`).join(", ");
}

/** The first deployment, kept so its records can be read and checked. It sells nothing. */
export function isArchive(deployment) {
  return deployment?.role === "archive";
}

/** Given one answer per deployment in `list`'s order (isPool, isChallenge), the deployment that said yes. */
export function pickDeployment(list, answers) {
  const i = answers.findIndex(Boolean);
  return i < 0 ? null : list[i];
}

/** A deployment named by its factory's address or by its label, as the records page names it. */
export function deploymentNamed(name, list = DEPLOYMENTS) {
  const n = String(name ?? "").toLowerCase();
  return list.find((d) => d.factory.toLowerCase() === n || d.label.toLowerCase() === n) ?? null;
}

export const ARCHIVE = {
  heading: "First deployment, kept as an archive",
  note: "These pools belong to the first deployment. It is kept so its records can be read and checked; the gateway "
    + "and the keepers serve the live deployment, so these pools sell no challenges.",
  poolNote: "This pool belongs to the first deployment, kept as an archive: it sells no challenges, because the "
    + "gateway and the keepers serve the live deployment. Its records can still be read and checked, and its investor "
    + "can still withdraw.",
  challengeNote: "This challenge belongs to the first deployment, kept as an archive: the gateway serves the live "
    + "deployment, so orders for this account are not taken. Its records can still be read and checked.",
};
