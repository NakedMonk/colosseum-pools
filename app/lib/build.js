// Which commit of namixai/colosseum-pools this copy of the site was packed from, for anyone who wants to
// check the site against the code. scripts/pack-site.sh writes it into build.json beside index.html when
// it packs a commit for upload. The repository's own app/ has no such file, so a copy served straight from
// a checkout shows nothing rather than a commit it may not be.
//
// No browser globals here, so node's test runner loads it (app/tests/build.test.mjs).

export const REPO = "https://github.com/namixai/colosseum-pools";
export const BUILD_FILE = "build.json";

/** The footer's link for what build.json holds, or null for anything that is not a full commit hash. */
export function buildLink(build) {
  const sha = typeof build?.commit === "string" ? build.commit : "";
  if (!/^[0-9a-f]{40}$/.test(sha)) return null;
  return { text: `Commit ${sha.slice(0, 8)}`, href: `${REPO}/commit/${sha}`, title: sha };
}

/**
 * The footer's link for the commit the pool gateway on the host says it runs: `commit` in its /v1/health, which a
 * gateway deployed by ops/host/deploy.sh carries (docs/GATEWAY.md). Null for an answer without a full commit hash,
 * as from a gateway deployed before it named one: the footer then says nothing about the host rather than guess.
 */
export function gatewayLink(health) {
  const link = buildLink({ commit: health?.commit });
  return link && { ...link, text: `Gateway ${health.commit.slice(0, 8)}` };
}
