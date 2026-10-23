"""The pools host's further instances: a gateway and a keeper for one more deployment beside the
first, on the same host (the CTO, 23 Oct 2026: demo3 beside demo2, whose live challenge stays served
by its own gateway and keeper). ops/host/install.sh asks this module what each instance needs.

The first deployment is named, as before, by /etc/colosseum/deployment, and served by
colosseum-gateway.service and colosseum-keeper.service. Each further one is a file
/etc/colosseum/deployment-<name> holding its deployment's label, made by
`ops/host/bootstrap.sh --instance <name> <label> <port>`, and is served by
colosseum-gateway@<name> and colosseum-keeper@<name>. Its gateway listens where
/etc/colosseum/gateway-<name>.env says (GATEWAY_BIND=127.0.0.1:<port>), and nginx sends
/<name>/v1/order and /<name>/v1/health of pools-api.usenami.io there.

    python -m ops.host_instances list /etc/colosseum             one line per instance: name label port
    python -m ops.host_instances nginx /etc/colosseum            the nginx locations for them
    python -m ops.host_instances deployment-env <label>          COLOSSEUM_DEPLOYMENT, GATEWAY_FACTORY, ...

Testnet only, as the host is.
"""

from __future__ import annotations

import pathlib
import re
import sys
from dataclasses import dataclass

from ops import deployments

# A name is a path segment of the API and part of a unit's name: short, lower-case, and never "v1",
# which the first gateway's own paths begin with.
NAME = re.compile(r"[a-z][a-z0-9]{0,15}")
LABEL = re.compile(r"[a-z0-9-]+")
FIRST_PORT = 8787  # the first gateway's (ops/host/bootstrap.sh)
PORTS = range(8788, 8800)


@dataclass(frozen=True)
class Instance:
    name: str
    label: str
    port: int


def bind_port(env: pathlib.Path) -> int:
    """The port of GATEWAY_BIND in an instance's gateway settings: on 127.0.0.1, one of PORTS."""
    try:
        lines = env.read_text().splitlines()
    except OSError as exc:
        raise SystemExit(f"{env}: {exc.strerror}; an instance's gateway needs its settings") from None
    for line in lines:
        if line.startswith("GATEWAY_BIND="):
            host, _, port = line.split("=", 1)[1].strip().rpartition(":")
            if host != "127.0.0.1" or not port.isdigit() or int(port) not in PORTS:
                raise SystemExit(f"{env}: GATEWAY_BIND must be 127.0.0.1:<{PORTS.start}..{PORTS.stop - 1}>, not {line}")
            return int(port)
    raise SystemExit(f"{env}: no GATEWAY_BIND")


def instances(etc: pathlib.Path) -> list[Instance]:
    """Every further instance named in `etc`, by name. A file that names one badly stops it all: a
    half-read list would leave an instance unrouted, or routed to another's gateway."""
    out = []
    for f in sorted(etc.glob("deployment-*")):
        if f.suffix in (".env", ".next", ".previous") or f.name.endswith(".env.next"):
            continue  # what install.sh writes beside them
        name = f.name[len("deployment-"):]
        if not NAME.fullmatch(name) or name == "v1":
            raise SystemExit(f"{f}: an instance's name is a-z then up to 15 of a-z0-9, and not v1")
        label = f.read_text().strip()
        if not LABEL.fullmatch(label):
            raise SystemExit(f"{f}: odd label {label!r}")
        out.append(Instance(name, label, bind_port(etc / f"gateway-{name}.env")))
    ports = [i.port for i in out]
    if len(set(ports)) != len(ports):
        raise SystemExit(f"two instances listen on one port: {sorted(ports)}")
    labels = [i.label for i in out]
    if len(set(labels)) != len(labels):
        raise SystemExit(f"two instances serve one deployment: {sorted(labels)}")
    return out


def nginx_locations(found: list[Instance]) -> str:
    """The locations that send an instance's paths to its gateway, under the same limits as the first
    gateway's: the zones are shared, so all the instances together keep inside the node's budget."""
    blocks = []
    for i in found:
        blocks.append(f"""    location = /{i.name}/v1/order {{
        limit_req zone=pools_ip burst=10 nodelay;
        limit_req zone=pools_all burst=10 nodelay;
        proxy_pass http://127.0.0.1:{i.port}/v1/order;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_read_timeout 30s;
    }}

    location = /{i.name}/v1/health {{
        limit_req zone=pools_ip burst=10 nodelay;
        proxy_pass http://127.0.0.1:{i.port}/v1/health;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
    }}
""")
    return "\n".join(blocks)


def deployment_env(label: str) -> str:
    """What a gateway and a keeper read of their deployment, from its committed record."""
    if not LABEL.fullmatch(label):
        raise SystemExit(f"odd label {label!r}")
    record = deployments.load(label)
    return (f"COLOSSEUM_DEPLOYMENT={label}\n"
            f"GATEWAY_FACTORY={record['PoolFactory']}\n"
            f"GATEWAY_REGISTRY={record['KeyRegistry']}\n")


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "list":
        for i in instances(pathlib.Path(argv[1])):
            print(f"{i.name} {i.label} {i.port}")
        return 0
    if len(argv) == 2 and argv[0] == "nginx":
        sys.stdout.write(nginx_locations(instances(pathlib.Path(argv[1]))))
        return 0
    if len(argv) == 2 and argv[0] == "deployment-env":
        sys.stdout.write(deployment_env(argv[1]))
        return 0
    print(__doc__.split("\n\n")[2], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
