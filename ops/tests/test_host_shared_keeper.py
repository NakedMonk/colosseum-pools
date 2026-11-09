"""The shared pool's keeper on the host (ops/host/colosseum-shared-keeper.service): what it runs and how
often, with which key, what it may read, how install.sh puts it in place, and what bootstrap.sh writes
for it. Offline: reads files and runs install.sh's own check of the pool against the records.

    spike/.venv/bin/python -m unittest ops.tests.test_host_shared_keeper
"""

from __future__ import annotations

import pathlib
import re
import shlex
import subprocess
import sys
import tempfile
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
        self.assertIn('shared_exact="$(grep -E \'^COLOSSEUM_SHARED_DEPLOYMENT=[a-z0-9-]+(,[a-z0-9-]+)*$\' '
                      '"$shared_env" || true)"', install)

    def test_the_check_takes_a_shared_pool_and_refuses_anything_else(self):
        install = (HOST / "install.sh").read_text()
        code = re.search(r"\"\$venv/bin/python\" -c '([^']+)' \"\$shared_label\"", install).group(1)

        def check(label: str) -> int:
            return subprocess.run([sys.executable, "-c", code, label], cwd=ROOT, capture_output=True).returncode

        self.assertEqual(check("shared-demo2b"), 0)
        self.assertEqual(check("shared-demo2,shared-demo2b"), 0, "two pools, one process")
        self.assertNotEqual(check("demo2"), 0, "a deployment with no shared pool")
        self.assertNotEqual(check("no-such-pool"), 0, "no record at all")
        self.assertNotEqual(check("shared-demo2b,demo2"), 0, "one of two with no shared pool")
        self.assertNotEqual(check("shared-demo2b,no-such-pool"), 0, "one of two with no record")
        self.assertNotEqual(check("shared-demo2b,shared-demo2b"), 0, "the same pool twice")

    def run_check(self, settings: str | None) -> subprocess.CompletedProcess:
        """install.sh's own block for the shared pool, run against a settings file of the test's: the
        release is this checkout and the venv's python is this one."""
        install = (HOST / "install.sh").read_text()
        block = install[install.index("shared_env=/etc/colosseum/shared-keeper.env"):]
        block = block[:block.index("\nfi\n") + 4]
        tmp = pathlib.Path(tempfile.mkdtemp())
        (tmp / "venv" / "bin").mkdir(parents=True)
        (tmp / "venv" / "bin" / "python").symlink_to(sys.executable)
        env_file = tmp / "shared-keeper.env"
        if settings is not None:
            env_file.write_text(settings)
        script = ('set -euo pipefail\nfail() { printf "install: %s\\n" "$*" >&2; exit 1; }\n'
                  f'rel={shlex.quote(str(ROOT))}\nvenv={shlex.quote(str(tmp / "venv"))}\n'
                  + block.replace("/etc/colosseum/shared-keeper.env", str(env_file)) + 'echo "label=${shared_label:-}"\n')
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    def test_the_label_is_taken_only_as_systemd_would_pass_it_and_anything_else_stops_the_release(self):
        rest = "COLOSSEUM_KEY_DIR=/var/lib/colosseum-keeper/shared/secrets\nCOLOSSEUM_RPC_URL=https://x\n"
        ok = self.run_check("COLOSSEUM_SHARED_DEPLOYMENT=shared-demo2b\n" + rest)
        self.assertEqual((ok.returncode, ok.stdout), (0, "label=shared-demo2b\n"), ok.stderr)
        two = self.run_check("COLOSSEUM_SHARED_DEPLOYMENT=shared-demo2,shared-demo2b\n" + rest)
        self.assertEqual((two.returncode, two.stdout), (0, "label=shared-demo2,shared-demo2b\n"), two.stderr)
        for settings, why in (
                ("COLOSSEUM_SHARED_DEPLOYMENT = shared-demo2b\n", "spaces around ="),
                ("COLOSSEUM_SHARED_DEPLOYMENT=shared-demo2 b\n", "a space inside the label"),
                ("COLOSSEUM_SHARED_DEPLOYMENT=shared-demo2, shared-demo2b\n", "a space after the comma"),
                ("COLOSSEUM_SHARED_DEPLOYMENT=shared-demo2b,\n", "a comma with nothing after it"),
                ("COLOSSEUM_SHARED_DEPLOYMENT=,shared-demo2b\n", "a comma with nothing before it"),
                ("COLOSSEUM_SHARED_DEPLOYMENT=shared-demo2,,shared-demo2b\n", "two commas"),
                ("COLOSSEUM_SHARED_DEPLOYMENT=shared-demo2b # pool 2\n", "a comment after the value"),
                ('COLOSSEUM_SHARED_DEPLOYMENT="shared-demo2b"\n', "quotes"),
                ("COLOSSEUM_SHARED_DEPLOYMENT=\n", "an empty value"),
                ("COLOSSEUM_SHARED_DEPLOYMENT=shared-demo2b\r\n", "a CRLF line end"),
                ("# COLOSSEUM_SHARED_DEPLOYMENT=shared-demo2b\n", "the line commented out"),
                ("COLOSSEUM_SHARED_DEPLOYMENT=shared-demo2b\nCOLOSSEUM_SHARED_DEPLOYMENT=shared-demo2b\n", "written twice"),
                ("# COLOSSEUM_SHARED_DEPLOYMENT=x\nCOLOSSEUM_SHARED_DEPLOYMENT=shared-demo2b\n", "a commented copy beside it"),
                ("", "no such line")):
            out = self.run_check(settings + rest)
            self.assertNotEqual(out.returncode, 0, why)
            self.assertIn("must name the pool on one line of its own, COLOSSEUM_SHARED_DEPLOYMENT=<label>", out.stderr, why)
            self.assertNotIn("label=", out.stdout, why)
        self.assertIn("it has: no such line;", self.run_check(rest).stderr)
        self.assertIn("it has: 1:COLOSSEUM_SHARED_DEPLOYMENT = shared-demo2b;",
                      self.run_check("COLOSSEUM_SHARED_DEPLOYMENT = shared-demo2b\n").stderr)
        # Well written, but no shared pool in its record: the record's refusal, not the format's.
        demo2 = self.run_check("COLOSSEUM_SHARED_DEPLOYMENT=demo2\n")
        self.assertNotEqual(demo2.returncode, 0)
        self.assertIn("shared pool demo2: no complete testnet record with a SharedPool", demo2.stderr)
        # No settings file on the host: nothing to check, nothing stops.
        self.assertEqual(self.run_check(None).returncode, 0)


class Isolation(unittest.TestCase):
    def test_no_other_keeper_reads_or_replaces_its_key(self):
        for unit in ("colosseum-keeper.service", "colosseum-keeper@.service"):
            inaccessible = [l for l in directives(HOST / unit) if l.startswith("InaccessiblePaths=")]
            self.assertEqual(len(inaccessible), 1, unit)
            self.assertIn("-/var/lib/colosseum-keeper/shared", inaccessible[0].split("=", 1)[1].split(), unit)


class Bootstrap(unittest.TestCase):
    def test_bootstrap_writes_its_settings_and_its_key_directory_when_missing(self):
        boot = (HOST / "bootstrap.sh").read_text()
        env = re.search(r"/etc/colosseum/shared-keeper.env <<'EOF'\n(.*?)\nEOF", boot, re.S).group(1).splitlines()
        keeper = re.search(r"/etc/colosseum/keeper.env <<'EOF'\n(.*?)\nEOF", boot, re.S).group(1).splitlines()
        self.assertEqual(env[0], "COLOSSEUM_SHARED_DEPLOYMENT=shared-demo2b,shared-demo2c")
        self.assertEqual(env[1], "COLOSSEUM_KEY_DIR=/var/lib/colosseum-keeper/shared/secrets")
        self.assertEqual(env[2:], [l for l in keeper if l.startswith("COLOSSEUM_RPC_URL=")], "the keepers' node")
        self.assertIn("if [ ! -f /etc/colosseum/shared-keeper.env ]; then", boot)
        self.assertIn("install -d -m 0700 -o colosseum-keeper -g colosseum-keeper /var/lib/colosseum-keeper/shared \\\n"
                      "  /var/lib/colosseum-keeper/shared/secrets", boot)
        subprocess.run(["bash", "-n", str(HOST / "bootstrap.sh")], check=True)

    def test_the_pool_the_site_takes_deposits_into_is_among_the_pools_it_keeps(self):
        """The site's shared page takes deposits into one pool; the default bootstrap writes names it among
        the pools the keeper is started for, and the whole line is one install.sh's own check would pass."""
        config = (ROOT / "app" / "config.js").read_text()
        boot = (HOST / "bootstrap.sh").read_text()
        line = re.search(r"^COLOSSEUM_SHARED_DEPLOYMENT=(\S+)$", boot, re.M).group(1)
        labels = line.split(",")
        site = re.search(r'sharedPoolRecord: "([^"]+)"', config).group(1)
        self.assertIn(site, labels)
        self.assertEqual(re.search(r'sharedPool: "(0x[0-9a-fA-F]{40})"', config).group(1),
                         deployments.load(site)["SharedPool"])
        install = (HOST / "install.sh").read_text()
        code = re.search(r"\"\$venv/bin/python\" -c '([^']+)' \"\$shared_label\"", install).group(1)
        passed = subprocess.run([sys.executable, "-c", code, line], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(passed.returncode, 0, f"install.sh would refuse bootstrap's default: {passed.stderr}")


if __name__ == "__main__":
    unittest.main()
