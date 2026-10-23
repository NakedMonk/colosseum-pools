"""A further instance on the pools host (the CTO, 23 Oct 2026): demo3's gateway and keeper beside demo2's,
whose live challenge keeps its own. What ops/host_instances.py reads of /etc/colosseum, the nginx it
writes, what install.sh does with both, the units, and what bootstrap.sh writes for an instance.
Offline: a temporary /etc, no systemd, no nginx."""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from ops import host_instances as hi

ROOT = pathlib.Path(__file__).resolve().parents[2]
HOST = ROOT / "ops" / "host"


def etc_with(files: dict[str, str]) -> pathlib.Path:
    d = pathlib.Path(tempfile.mkdtemp())
    for name, text in files.items():
        (d / name).write_text(text)
    return d


DEMO3 = {"deployment": "demo2\n", "deployment.env": "COLOSSEUM_DEPLOYMENT=demo2\n",
         "deployment-demo3": "v3-demo3\n", "deployment-demo3.env": "COLOSSEUM_DEPLOYMENT=v3-demo3\n",
         "gateway-demo3.env": "GATEWAY_SIGNER=demo\nGATEWAY_BIND=127.0.0.1:8788\n"}


class Instances(unittest.TestCase):
    def test_an_instance_is_its_file_its_label_and_its_gateways_port(self):
        self.assertEqual(hi.instances(etc_with(DEMO3)), [hi.Instance("demo3", "v3-demo3", 8788)])
        self.assertEqual(hi.instances(etc_with({"deployment": "demo2\n"})), [], "the first deployment alone: none")

    def test_a_name_label_or_port_out_of_its_rules_stops_the_whole_list(self):
        bad = [
            {"deployment-v1": "x\n", "gateway-v1.env": "GATEWAY_BIND=127.0.0.1:8788\n"},           # the first's paths
            {"deployment-Demo3": "x\n", "gateway-Demo3.env": "GATEWAY_BIND=127.0.0.1:8788\n"},
            {"deployment-demo-3": "x\n", "gateway-demo-3.env": "GATEWAY_BIND=127.0.0.1:8788\n"},
            {"deployment-demo3": "v3 demo3\n", "gateway-demo3.env": "GATEWAY_BIND=127.0.0.1:8788\n"},
            {"deployment-demo3": "v3-demo3\n"},                                                       # no settings
            {"deployment-demo3": "v3-demo3\n", "gateway-demo3.env": "GATEWAY_SIGNER=demo\n"},         # no port
            {"deployment-demo3": "v3-demo3\n", "gateway-demo3.env": "GATEWAY_BIND=127.0.0.1:8787\n"},  # the first's
            {"deployment-demo3": "v3-demo3\n", "gateway-demo3.env": "GATEWAY_BIND=0.0.0.0:8788\n"},    # outside
            {"deployment-demo3": "v3-demo3\n", "gateway-demo3.env": "GATEWAY_BIND=127.0.0.1:9000\n"},
            {"deployment-demo3": "v3-demo3\n", "gateway-demo3.env": "GATEWAY_BIND=127.0.0.1:8788\n",
             "deployment-demo4": "v3-demo4\n", "gateway-demo4.env": "GATEWAY_BIND=127.0.0.1:8788\n"},  # one port
            {"deployment-demo3": "v3-demo3\n", "gateway-demo3.env": "GATEWAY_BIND=127.0.0.1:8788\n",
             "deployment-demo4": "v3-demo3\n", "gateway-demo4.env": "GATEWAY_BIND=127.0.0.1:8789\n"},  # one deployment
            {"deployment": "v3-demo3\n",
             "deployment-demo3": "v3-demo3\n", "gateway-demo3.env": "GATEWAY_BIND=127.0.0.1:8788\n"},  # the first's
        ]
        for files in bad:
            with self.assertRaises(SystemExit, msg=str(files)):
                hi.instances(etc_with(files))

    def test_the_deployment_env_is_the_records_addresses(self):
        record = {"PoolFactory": "0x" + "f1" * 20, "KeyRegistry": "0x" + "f2" * 20}
        with mock.patch.object(hi.deployments, "load", return_value=record) as load:
            text = hi.deployment_env("v3-demo3")
        load.assert_called_once_with("v3-demo3")
        self.assertEqual(text, f"COLOSSEUM_DEPLOYMENT=v3-demo3\nGATEWAY_FACTORY={'0x' + 'f1' * 20}\n"
                               f"GATEWAY_REGISTRY={'0x' + 'f2' * 20}\n")
        with self.assertRaises(SystemExit):
            hi.deployment_env("v3 demo3")

    def test_the_command_line_says_each_instance_on_a_line(self):
        etc = etc_with(DEMO3)
        out = subprocess.run([sys.executable, "-m", "ops.host_instances", "list", str(etc)],
                             cwd=ROOT, capture_output=True, text=True)
        self.assertEqual((out.returncode, out.stdout), (0, "demo3 v3-demo3 8788\n"), out.stderr)


class Nginx(unittest.TestCase):
    """The locations install.sh puts in the site, by the same awk install.sh runs."""

    def render(self, locations: str) -> str:
        install = (HOST / "install.sh").read_text()
        program = re.search(r"""awk '\n(.*?)' "\$rel/ops/host/nginx-pools-api\.conf\.in" > "\$site\"""", install, re.S).group(1)
        env = {**os.environ, "REAL_IP": "    set_real_ip_from CLOUDFLARE-RANGE;", "LOCATIONS": locations}
        return subprocess.run(["awk", program, str(HOST / "nginx-pools-api.conf.in")], env=env,
                              capture_output=True, text=True, check=True).stdout

    def test_an_instance_is_reached_at_its_name_on_its_own_gateways_port(self):
        site = self.render(hi.nginx_locations([hi.Instance("demo3", "v3-demo3", 8788)]))
        server = site[site.index("server_name pools-api.usenami.io;"):]
        for path, upstream in (("/demo3/v1/order", "http://127.0.0.1:8788/v1/order"),
                               ("/demo3/v1/health", "http://127.0.0.1:8788/v1/health")):
            at = server.index(f"location = {path} {{")
            self.assertIn(f"proxy_pass {upstream};", server[at:server.index("}", at)])
            self.assertLess(at, server.index("location / {"), "inside the API's server, before its 404")
        # The first gateway's paths stay its own, and the zones are the ones the first uses: shared.
        self.assertIn("location = /v1/order {\n        limit_req zone=pools_ip burst=10 nodelay;", server)
        order = server[server.index("location = /demo3/v1/order"):]
        self.assertIn("limit_req zone=pools_ip burst=10 nodelay;\n        limit_req zone=pools_all burst=10 nodelay;", order)
        self.assertNotIn("@INSTANCE_LOCATIONS@", site)
        self.assertNotIn("@CLOUDFLARE_REAL_IP_FROM@", site)
        self.assertIn("set_real_ip_from CLOUDFLARE-RANGE;", site)

    def test_with_no_instance_the_site_is_the_first_gateways_alone(self):
        site = self.render(hi.nginx_locations([]))
        self.assertEqual(site.count("location ="), 2)
        self.assertNotIn("@INSTANCE_LOCATIONS@", site)


class Install(unittest.TestCase):
    def test_install_routes_and_restarts_each_instance_and_stops_on_a_bad_list(self):
        install = (HOST / "install.sh").read_text()
        for unit in ("colosseum-gateway@.service", "colosseum-keeper@.service"):
            self.assertIn(f'install -m 0644 -o root -g root "$rel/ops/host/{unit}" /etc/systemd/system/', install)
        listing = install.index('-m ops.host_instances list /etc/colosseum')
        self.assertLess(listing, install.index('> "$site"'), "the list is read before nginx is touched")
        self.assertIn('|| fail "the instances in /etc/colosseum are not as ops/host_instances.py reads them', install)
        self.assertIn('deployment_env "$label" "/etc/colosseum/deployment-$name.env"', install)
        self.assertIn('units+=("colosseum-gateway@$name" "colosseum-keeper@$name")', install)
        subprocess.run(["bash", "-n", str(HOST / "install.sh")], check=True)

    def test_every_deployments_record_is_checked_before_nginx_routes_to_it(self):
        install = (HOST / "install.sh").read_text()
        check = install.index("for label in $first_label $(awk '{print $2}' <<< \"$instances\"); do")
        self.assertLess(install.index('-m ops.host_instances list /etc/colosseum'), check)
        self.assertLess(check, install.index('> "$site"'), "a label with no record stops the release before nginx")
        loop = install[check:install.index("done", check)]
        self.assertIn('-m ops.host_instances deployment-env "$label") > /dev/null', loop)
        self.assertIn('|| fail "deployment $label: no complete testnet record in this release; nothing switched"', loop)
        self.assertIn('deployment_env "$first_label" /etc/colosseum/deployment.env', install)


class Units(unittest.TestCase):
    @staticmethod
    def directives(path: pathlib.Path) -> list[str]:
        text = path.read_text().replace("\\\n", " ")
        return [l for l in text.splitlines() if l and not l.startswith("#")]

    def test_an_instances_units_are_the_firsts_with_their_own_files_and_keys(self):
        for kind, env, keys_first, keys_instances in (
                ("gateway", "gateway", "-/var/lib/colosseum-gw/keys", "-/var/lib/colosseum-gw/instances"),
                ("keeper", "keeper", "-/var/lib/colosseum-keeper/secrets", "-/var/lib/colosseum-keeper/instances")):
            first = self.directives(HOST / f"colosseum-{kind}.service")
            at = self.directives(HOST / f"colosseum-{kind}@.service")
            differ = sorted(set(first) ^ set(at))
            # Description, the two env files, InaccessiblePaths: each side; and the instance's own two below.
            self.assertEqual(len(differ), 10, differ)
            # Every instance runs as one user, so each sees its own directory alone under instances/.
            self.assertIn(f"TemporaryFileSystem={keys_instances[1:]}:ro", at)
            self.assertIn(f"BindReadOnlyPaths={keys_instances[1:]}/%i", at)
            self.assertIn(f"EnvironmentFile=/etc/colosseum/deployment-%i.env", at)
            self.assertIn(f"EnvironmentFile=/etc/colosseum/{env}-%i.env", at)
            inaccessible = [l for l in at if l.startswith("InaccessiblePaths=")]
            self.assertEqual(len(inaccessible), 1)
            self.assertIn(keys_first, inaccessible[0], "an instance does not read the first's keys")
            first_inaccessible = [l for l in first if l.startswith("InaccessiblePaths=")][0]
            self.assertIn(keys_instances, first_inaccessible, "the first does not read an instance's")
        keeper = [l for l in self.directives(HOST / "colosseum-keeper@.service") if l.startswith("ExecStart=")]
        self.assertEqual(keeper, [l for l in self.directives(HOST / "colosseum-keeper.service") if l.startswith("ExecStart=")],
                         "the same keeper, its state by its deployment's label")


class Bootstrap(unittest.TestCase):
    def env_of(self, name: str) -> list[str]:
        boot = (HOST / "bootstrap.sh").read_text()
        return re.search(rf'"/etc/colosseum/{name}-\$instance\.env" <<EOF\n(.*?)\nEOF', boot, re.S).group(1).splitlines()

    def test_an_instances_gateway_has_its_keys_its_port_and_xyz100(self):
        env = self.env_of("gateway")
        self.assertIn("GATEWAY_KEYS_DIR=/var/lib/colosseum-gw/instances/$instance/keys", env)
        self.assertIn("GATEWAY_BIND=127.0.0.1:$instance_port", env)
        self.assertIn("GATEWAY_PERP_DEXES=xyz", env)
        # The CTO's for demo3: 0.015 of xyz:XYZ100 (750000 on testnet) an order, 8x.
        self.assertIn('GATEWAY_SIZE_CAPS={"3": "0.005", "4": "0.15", "0": "4", "750000": "0.015"}', env)
        self.assertIn('GATEWAY_LEVERAGE_CAPS={"3": "10", "4": "10", "0": "5", "750000": "8"}', env)

    def test_an_instances_keeper_sends_with_its_own_key(self):
        env = self.env_of("keeper")
        self.assertIn("COLOSSEUM_KEY_DIR=/var/lib/colosseum-keeper/instances/$instance/secrets", env)
        self.assertIn("COLOSSEUM_PERP_DEXES=xyz", env)
        self.assertIn("COLOSSEUM_RPC_URL=https://rpc.hyperliquid-testnet.xyz/evm", env)

    def test_an_instance_out_of_the_rules_is_refused_before_anything_is_done(self):
        for args in (["--instance", "v1", "v3-demo3", "8788"], ["--instance", "Demo3", "v3-demo3", "8788"],
                     ["--instance", "demo3", "v3 demo3", "8788"], ["--instance", "demo3", "v3-demo3", "8787"],
                     ["--instance", "demo3", "v3-demo3", "9000"], ["--other"]):
            out = subprocess.run(["bash", str(HOST / "bootstrap.sh"), *args], capture_output=True, text=True)
            self.assertEqual(out.returncode, 2, (args, out.stderr))
            self.assertNotIn("bootstrap: python3.12", out.stdout, "nothing was installed or checked first")


if __name__ == "__main__":
    unittest.main()
