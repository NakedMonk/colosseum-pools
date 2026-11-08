"""Which Hyperliquid network the tools talk to, and everything that differs between the two.

One switch, COLOSSEUM_NETWORK: "testnet" or "mainnet". Unset, or set to nothing as an env file writes a
variable it means to leave out, it is testnet: every host and test of today keeps working without an edit,
and mainnet is only ever chosen by naming it. Any other value stops the program rather than being read as
one of the two.

The testnet values are the constants the tools used before this module. The mainnet ones come from:

- chain ids and JSON-RPC: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm
- the info and exchange API: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api
- USDC on HyperEVM: https://developers.circle.com/stablecoins/usdc-contract-addresses
- an L1 action's phantom agent `source` and a user-signed action's `hyperliquidChain`: hyperliquid-python-sdk,
  hyperliquid/utils/signing.py (`construct_phantom_agent`, the user-signed payloads). The L1 domain's chain id
  is 1337 on both networks, so it is not here.
- the signer's action name: the enclave signs `hyperliquid_main` with source "a" and `hyperliquid_testnet`
  with source "b" (namixai/signer, poc/enclave/src/signer.rs).

CoreWriter, the system addresses and the precompiles are the same on both networks; they stay in common.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

ENV = "COLOSSEUM_NETWORK"
DEFAULT = "testnet"


@dataclass(frozen=True)
class Network:
    name: str
    chain_id: int            # HyperEVM's, as eth_chainId answers it
    rpc_url: str             # the public JSON-RPC; COLOSSEUM_RPC_URL replaces it (common.py)
    api_url: str             # Hyperliquid's info and exchange API, without the path
    usdc_erc20: str          # USDC on HyperEVM
    signer_exchange: str     # the action name the signer signs this network's orders under
    phantom_source: str      # `source` of an L1 action's phantom agent
    hyperliquid_chain: str   # `hyperliquidChain` of a user-signed action

    @property
    def is_mainnet(self) -> bool:
        """What hyperliquid-python-sdk's signing helpers take as `is_mainnet`."""
        return self.name == "mainnet"


TESTNET = Network(
    name="testnet",
    chain_id=998,
    rpc_url="https://rpc.hyperliquid-testnet.xyz/evm",
    api_url="https://api.hyperliquid-testnet.xyz",
    usdc_erc20="0x2B3370eE501B4a559b57D449569354196457D8Ab",
    signer_exchange="hyperliquid_testnet",
    phantom_source="b",
    hyperliquid_chain="Testnet",
)
MAINNET = Network(
    name="mainnet",
    chain_id=999,
    rpc_url="https://rpc.hyperliquid.xyz/evm",
    api_url="https://api.hyperliquid.xyz",
    usdc_erc20="0xb88339CB7199b77E23DB6E890353E22632Ba630f",
    signer_exchange="hyperliquid_main",
    phantom_source="a",
    hyperliquid_chain="Mainnet",
)
NETWORKS = {n.name: n for n in (TESTNET, MAINNET)}


def select(env: Mapping[str, str] | None = None) -> Network:
    """The network COLOSSEUM_NETWORK names, testnet when it is unset or empty. Anything else stops the
    program: reading a typo as one of the two would be choosing a network for the operator."""
    value = (os.environ if env is None else env).get(ENV, "") or DEFAULT
    try:
        return NETWORKS[value]
    except KeyError:
        raise SystemExit(f"refusing to run: {ENV}={value!r}, expected one of: {', '.join(NETWORKS)}") from None
