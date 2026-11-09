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
  first, the one the site sends orders to. There is exactly one.
- `testers`: served beside it on the host by its own gateway and keeper, with a test dollar and a faucet
  ([TESTNET-TRADER.md](TESTNET-TRADER.md), [TESTNET-INVESTOR.md](TESTNET-INVESTOR.md)). The site doesn't read it.
- `shared`: a shared pool tended by the shared pool's keeper ([HOSTING.md](HOSTING.md#the-shared-pools-keeper)). It
  has no gateway of its own: its seats are pools made by `factory`, so their challenges trade through that
  deployment's gateway. `sharedPoolRecord` in `app/config.js` picks the one `#/shared` opens.
- `archive`: the site reads it and sells nothing on it (`role: "archive"` in `app/config.js`).
- `retired`: nothing serves it and the site doesn't name it. The record stays, since the evidence pages still point
  at what it made.

## Adding or retiring a deployment

One edit to `deployments/LIVE.json`: in the commit that adds the record, or as soon as the host changes.

- To add one, the deploy writes `deployments/<network>-<label>.json`; add an entry with its role and what serves
  it. The site reads `live` and `archive` entries, so those need an entry in `app/config.js` as well. The test
  names any field that is missing.
- To retire one, set `role` to `retired` and `gateway` and `keeper` to `null`. Keep the record.
- When a gateway or keeper starts or stops on the host, change `gateway` or `keeper` here. This file doesn't
  configure the host: `/etc/colosseum/deployment`, the instances beside it and `shared-keeper.env` decide what it
  serves ([HOSTING.md](HOSTING.md)).

Then run `node --test app/tests/deployments.test.mjs`; it names every place that disagrees.
