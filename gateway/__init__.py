"""The pool gateway (gateway/server.py) and the pieces the keepers and the agents share with it.

NETWORK is the one network all of it talks to. COLOSSEUM_NETWORK picks it, as it does for the ops
scripts, and spike/hlspike/network.py holds the values: unset or empty is testnet, with the values
the gateway always had; `mainnet` is Hyperliquid's mainnet; any other value stops the start."""

from spike.hlspike import network

NETWORK = network.select()
