"""The shared pool's keeper on the host (ops/host/colosseum-shared-keeper.service): what it runs and how
often, with which key, what it may read, how install.sh puts it in place, and what bootstrap.sh writes
for it. Offline: reads files and runs install.sh's own check of the pool against the records.

    spike/.venv/bin/python -m unittest ops.tests.test_host_shared_keeper
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import unittest

from ops import deployments

ROOT = pathlib.Path(__file__).resolve().parents[2]
HOST = ROOT / "ops" / "host"
UNIT = HOST / "colosseum-shared-keeper.service"


def directives(path: pathlib.Path) -> list[str]:
    text = path.read_text().replace("\\\n", " ")
    return [l for l in text.splitlines() if l and not l.startswith("#")]


def exec_start(path: pathlib.Path) -> str:
    return [l for l in directives(path) if l.startswith("ExecStart=")][0]


class Unit(unittest.TestCase):
    def test_it_runs_the_shared_keeper_for_the_pool_its_settings_name_every_five_minutes(self):
        start = exec_start(UNIT)
        self.assertIn("/opt/colosseum-pools/venv/bin/python -m ops.shared_keeper", start)
        self.assertIn("--deployment ${COLOSSEUM_SHARED_DEPLOYMENT}", start)
        # A pass reads the node about forty times; every thirty seconds would take most of its minute.
        self.assertRegex(start, r"--every 300(\s|$)")
        self.assertRegex(start, r"--queue-every 300(\s|$)")
        self.assertNotIn("--dry-run", start)
        self.assertNotIn("--no-arm", start)
        envs = [l for l in directives(UNIT) if l.startswith("EnvironmentFile=")]
        self.assertEqual(envs, ["EnvironmentFile=/etc/colosseum/shared-keeper.env"])

    def test_it_sends_with_a_key_of_its_own_and_reads_no_other(self):
        self.assertRegex(exec_start(UNIT), r"--wallet shared-keeper(\s|$)")
        self.assertNotRegex(exec_start(HOST / "colosseum-keeper.service"), r"--wallet shared-keeper")
        at = directives(UNIT)
        self.assertIn("User=colosseum-keeper", at)
        inaccessible = [l for l in at if l.startswith("InaccessiblePaths=")]
        self.assertEqual(len(inaccessible), 1)
        for path in ("/var/lib/colosseum-gw", "-/var/lib/colosseum-keeper/secrets", "-/var/lib/colosseum-keeper/instances"):
            self.assertIn(path, inaccessible[0].split("=", 1)[1].split())
        # It keeps no state: nothing on the host is writable to it.
        self.assertEqual([l for l in at if l.startswith("ReadWritePaths=")], [])

    def test_it_is_held_as_tightly_as_the_first_keeper(self):
        own = ("Description=", "EnvironmentFile=", "ExecStart=", "ReadWritePaths=", "InaccessiblePaths=", "RestartSec=")
        first = [l for l in directives(HOST / "colosseum-keeper.service") if not l.startswith(own)]
        at = set(directives(UNIT))
        self.assertEqual([l for l in first if l not in at], [])
        self.assertIn("ProtectProc=invisible", at)


class Install(unittest.TestCase):
    def test_install_puts_it_in_place_and_restarts_it_when_enabled(self):
        install = (HOST / "install.sh").read_text()
        self.assertIn('install -m 0644 -o root -g root "$rel/ops/host/colosseum-shared-keeper.service" /etc/systemd/system/',
                      install)
        self.assertLess(install.index("colosseum-shared-keeper.service"), install.index("systemctl daemon-reload"))
        restart = install.index('for unit in "${units[@]}"; do')
        added = install.index("units+=(colosseum-shared-keeper)")
        self.assertLess(added, restart)
        self.assertGreater(added, install.index("units=(colosseum-gateway colosseum-keeper)"))
        subprocess.run(["bash", "-n", str(HOST / "install.sh")], check=True)

    def test_install_checks_the_pool_it_names_before_anything_switches(self):
        install = (HOST / "install.sh").read_text()
        check = install.index('if [ -f "$shared_env" ]; then')
        self.assertLess(install.index('venv="$base/venv-$req_id"'), check)
        self.assertLess(check, install.index('> "$site"'), "a pool with no record stops the release before nginx")
        self.assertLess(check, install.index('mv -T "$base/current.next" "$base/current"'))
        self.assertIn("shared_env=/etc/colosseum/shared-keeper.env", install)
        self.assertIn('|| fail "shared pool $shared_label: no complete testnet record with a SharedPool in this release; '
                      'nothing switched"', install)
        self.assertIn('[[ "$shared_label" =~ ^[a-z0-9-]+$ ]] || fail', install)

    def test_the_check_takes_a_shared_pool_and_refuses_anything_else(self):
        install = (HOST / "install.sh").read_text()
        code = re.search(r"\"\$venv/bin/python\" -c '([^']+)' \"\$shared_label\"", install).group(1)

        def check(label: str) -> int:
            return subprocess.run([sys.executable, "-c", code, label], cwd=ROOT, capture_output=True).returncode

        self.assertEqual(check("shared-demo2b"), 0)
        self.assertNotEqual(check("demo2"), 0, "a deployment with no shared pool")
        self.assertNotEqual(check("no-such-pool"), 0, "no record at all")


class Bootstrap(unittest.TestCase):
    def test_bootstrap_writes_its_settings_and_its_key_directory_when_missing(self):
        boot = (HOST / "bootstrap.sh").read_text()
        env = re.search(r"/etc/colosseum/shared-keeper.env <<'EOF'\n(.*?)\nEOF", boot, re.S).group(1).splitlines()
        keeper = re.search(r"/etc/colosseum/keeper.env <<'EOF'\n(.*?)\nEOF", boot, re.S).group(1).splitlines()
        self.assertEqual(env[0], "COLOSSEUM_SHARED_DEPLOYMENT=shared-demo2b")
        self.assertEqual(env[1], "COLOSSEUM_KEY_DIR=/var/lib/colosseum-keeper/shared/secrets")
        self.assertEqual(env[2:], [l for l in keeper if l.startswith("COLOSSEUM_RPC_URL=")], "the keepers' node")
        self.assertIn("if [ ! -f /etc/colosseum/shared-keeper.env ]; then", boot)
        self.assertIn("install -d -m 0700 -o colosseum-keeper -g colosseum-keeper /var/lib/colosseum-keeper/shared \\\n"
                      "  /var/lib/colosseum-keeper/shared/secrets", boot)
        subprocess.run(["bash", "-n", str(HOST / "bootstrap.sh")], check=True)

    def test_the_pool_it_serves_is_the_one_the_site_takes_deposits_into(self):
        config = (ROOT / "app" / "config.js").read_text()
        boot = (HOST / "bootstrap.sh").read_text()
        label = re.search(r"^COLOSSEUM_SHARED_DEPLOYMENT=(\S+)$", boot, re.M).group(1)
        self.assertEqual(re.search(r'sharedPoolRecord: "([^"]+)"', config).group(1), label)
        self.assertEqual(re.search(r'sharedPool: "(0x[0-9a-fA-F]{40})"', config).group(1),
                         deployments.load(label)["SharedPool"])


if __name__ == "__main__":
    unittest.main()
