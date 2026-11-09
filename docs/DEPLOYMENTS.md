# Deployments

`deployments/LIVE.json` lists every deployment this repository holds a record for, with the role each one plays
today and what serves it. `app/tests/deployments.test.mjs` checks the list against the records, against the
networks in `spike/hlspike/network.py` and against `app/config.js`: change a deployment in one place and not the
others, and CI goes red. Anything that watches the deployments from outside the repository should read this list
instead of keeping a copy.

## An entry

| field | what it holds |
|---|---|
| `label` | the name the tools and the host use (`--deployment`, `COLOSSEUM_DEPLOYMENT`) |
| `network` | `testnet` or `mainnet`, as named in `spike/hlspike/network.py` |
| `chainId` | that network's HyperEVM chain id; the record's `chain_id` matches it |
| `role` | what the deployment is for today, one of the roles below |
| `record` | `deployments/<network>-<label>.json`, written by the deploy |
| `factory` | the record's `PoolFactory`; for a shared pool, the factory its seats come from |
| `pool` | the record's `SharedPool`; `null` for a factory's deployment, which has many pools |
| `gateway` | where the deployment's own gateway answers, as in `<gateway>/v1/health`; `null` if it has none |
| `deployer` | the record's `deployer` |
| `keeper` | the address the keeper tending it on the host sends from; `null` if nothing tends it |

`v3-demo3` is the odd one out: its record was written by hand and says `"label": "demo3"`. The tools, the host and
this list go by the file name.

## Roles

- `live`: the deployment the site sells challenges on (`role: "live"` in `app/config.js`). Its gateway is the host's
  first, the one the site sends orders to. A network has one at most.
- `testers`: served beside it on the host by its own gateway and keeper, with a test dollar and a faucet
  ([TESTNET-TRADER.md](TESTNET-TRADER.md), [TESTNET-INVESTOR.md](TESTNET-INVESTOR.md)). The site doesn't read it.
- `shared`: a shared pool tended by the shared pool's keeper ([HOSTING.md](HOSTING.md#the-shared-pools-keeper)). It
  has no gateway of its own: its seats are pools made by `factory`, so their challenges trade through that
  deployment's gateway. `sharedPoolRecord` in `app/config.js` picks the one `#/shared` opens.
- `staged`: deployed and its record committed, but nothing serves it yet and the site doesn't read it: no gateway,
  no keeper. A new deployment stays `staged` until its gateway and keeper run.
- `archive`: the site reads it and sells nothing on it (`role: "archive"` in `app/config.js`).
- `retired`: nothing serves it and the site doesn't name it. The record stays, since the evidence pages still point
  at what it made.

## Adding or retiring a deployment

One edit to `deployments/LIVE.json`: in the commit that adds the record, or as soon as the host changes.

- To add one, the deploy writes `deployments/<network>-<label>.json`; add an entry with its role and what serves
  it, `staged` until its gateway and keeper run. The site reads `live` and `archive` entries of its own network, so
  those need an entry in `app/config.js` as well. The test names any field that is missing.
- To retire one, set `role` to `retired` and `gateway` and `keeper` to `null`. Keep the record.
- When a gateway or keeper starts or stops on the host, change `gateway` or `keeper` here. This file doesn't
  configure the host: `/etc/colosseum/deployment`, the instances beside it and `shared-keeper.env` decide what it
  serves ([HOSTING.md](HOSTING.md)).

Then run `node --test app/tests/deployments.test.mjs`; it names every place that disagrees.

## Mainnet records

There are none yet. A mainnet deployment will be listed the way a testnet one is, under its own network: its record
is `deployments/mainnet-<label>.json` with `"chain_id": 999` and `"network": "mainnet"`, and its entry here says
`"network": "mainnet"`, `"chainId": 999` and that `record`. 999 is mainnet's chain id in `spike/hlspike/network.py`.

The record has the fields of `deployments/testnet-v3-demo3.json` (the contracts, `block`, `commit`, `deployer`,
`operator`, `platform_assets`, `published_keys`, `tx`, `codehash`, `initcode_keccak`) except testnet's own test
dollar and faucet: on mainnet `usdc` is Circle's, `usdc_erc20` in network.py, and there is no `faucet`.

Where each value comes from:

| value | source |
|---|---|
| `factory` | the deploy writes `PoolFactory` into the record as its transaction lands |
| `pool` | for a shared pool, `ops/deploy_shared.py` writes `SharedPool`; a factory's deployment has none |
| `deployer` | the deploy key's address, which the deploy writes into the record |
| `gateway` | the host instance that serves it ([HOSTING.md](HOSTING.md#a-further-instance-a-second-deployment-on-the-same-host)); only here, not in the record |
| `keeper` | the keeper key made on the host for that instance (`ops/make_demo_keys.py --wallet keeper`) and given HYPE; only here |
| perp numbers | `platform_assets` in the record, numbered as orders number them: a HIP-3 perp is 100000 + dex × 10000 + its index in that dex's `meta` (`gateway/assets.py`, `app/lib/hl.js`) |

Mainnet's perp numbers, read on 19 and 21 October 2026, are in [HOSTING.md](HOSTING.md): BTC 0, ETH 1, SOL 5,
HYPE 159; xyz is dex 1, so `xyz:XYZ100` is 110000. Read them again from mainnet's `perpDexs` and `meta` just before
`setPlatformAssets`, since a dex's list can grow; `ops/deploy_testnet.py` still holds testnet's (BTC 3, ETH 4,
SOL 0).

No tool loads a mainnet record yet: `ops/deployments.py` and the deploy scripts take `testnet-<label>.json` with
chain id 998 only. This list and its test do take one. They check a mainnet entry against mainnet's chain id from
network.py, allow one `live` deployment per network, and hold `app/config.js` only to the entries of the app's own
network, the one whose chain id is `CONFIG.chainId`. A mainnet example inside the test, not in `deployments/`, goes
through the same checks.

## The app on mainnet (planned)

The app is built for testnet only: `app/config.js` holds testnet's network values and a copy of its deployments, and
`app/lib/hlsend.js` signs spot transfers with `hyperliquidChain: "Testnet"`. The plan is for one commit to pack a
site for either network, with testnet's pack staying byte for byte what it is now:

1. `spike/hlspike/network.py` gains the two values the app needs and the module lacks: the chain's name and the
   Hyperliquid app's address (mainnet's has not been checked against Hyperliquid's docs yet). The hex chain id is
   the chain id written in hex.
2. A script writes `app/config.js` for a network from network.py and from that network's entries here (the `live`
   and `archive` ones, and the `shared` one the site opens), with their records. The committed `app/config.js` is
   its testnet output, and a test holds the two equal. `hlsend.js` takes `hyperliquidChain` from the config.
3. `scripts/pack-site.sh` takes `--network`. For mainnet it writes the script's mainnet output in place of
   `config.js` and says so in `build.json`; every other file is checked against its blob as now, and
   `scripts/verify-live.py` checks the site against the pack's own list as before.

The config goes into the pack rather than being fetched when the page loads, so a mainnet site cannot fall back to
testnet's values because a file failed to arrive. LIVE.json does not hold everything `app/config.js` does yet: which
`shared` entry the site opens (`sharedPoolRecord`), `liveRunPool` (a pool opened after the deploy) and
`assetLeverage` (a v3 factory). Each needs a field here or a place in the script. The pages' own testnet wording
("Testnet. Mock USDC. Unaudited." in `app/views/pools.js`) needs mainnet wording as well; that is copy, not
configuration.
