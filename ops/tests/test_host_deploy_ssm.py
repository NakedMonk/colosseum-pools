"""ops/host/deploy-ssm.sh sends the host only what is on origin/main, and what it asks the host to run
installs exactly the archive it made. Offline: a repository made for the test with an origin beside it,
and an `aws` that writes down what it was asked, keeps what it is given and answers from a script.

    spike/.venv/bin/python -m unittest discover -s ops/tests -t .
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops" / "host" / "deploy-ssm.sh"
INSTANCE = "i-" + "0" * 17  # built, not written: the scrub check rightly stops a literal id
BUCKET = "releases-test"

# An aws that sends nothing. The object it is given lands in $STORE/object, the Run Command parameters
# in $STORE/parameters.json; get-command-invocation answers Status from $STORE/statuses, one line a call
# ("ERR:<code>" fails the call the way the CLI does), and StatusDetails from $STORE/details.
FAKE_AWS = r'''#!/usr/bin/env python3
import os, pathlib, shutil, sys
store = pathlib.Path(os.environ["STORE"])
args = sys.argv[1:]
with open(store / "asked", "a") as asked:
    asked.write(" ".join(args) + "\n")
def after(flag):
    return args[args.index(flag) + 1]
if args[:2] == ["s3", "cp"]:
    (store / "key").write_text(args[-1])
    shutil.copy(args[-2], store / "object")
    shutil.copy(args[-2], store / "uploaded")  # kept after `s3 rm`, for the digest
elif args[:2] == ["s3", "presign"]:
    print("https://releases-test.s3.amazonaws.com/release?X-Amz-Signature=abc&X-Amz-Expires=900")
elif args[:2] == ["s3", "rm"]:
    (store / "object").unlink(missing_ok=True)
elif args[:2] == ["ssm", "send-command"]:
    shutil.copy(after("--parameters").removeprefix("file://"), store / "parameters.json")
    if (store / "send-fails").exists():  # SSM took the request; the answer never came back
        sys.stderr.write("Read timeout on endpoint URL: \"https://ssm.us-east-1.amazonaws.com/\"\n")
        sys.exit(255)
    print("command-1")
elif args[:2] == ["ssm", "get-command-invocation"]:
    field = after("--query")
    if field == "Status":
        lines = (store / "statuses").read_text().splitlines()
        (store / "statuses").write_text("".join(l + "\n" for l in lines[1:]))
        line = lines[0] if lines else "InProgress"
        if line.startswith("ERR:"):
            sys.stderr.write(f"An error occurred ({line[4:]}) when calling the GetCommandInvocation operation\n")
            sys.exit(254)
        print(line)
    elif (store / "no-output").exists():
        sys.exit(255)  # the invocation answered its status, then the API hiccuped
    elif field == "StandardOutputContent":
        print("install: current -> host-says")
    elif field == "StatusDetails":
        print((store / "details").read_text() if (store / "details").exists() else "Failed")
    else:
        print("host-stderr")
'''


class DeploySsm(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = pathlib.Path(tmp.name)
        self.store = self.tmp / "store"
        self.store.mkdir()
        self.env = {**os.environ, "GIT_AUTHOR_NAME": "deploy-test", "GIT_AUTHOR_EMAIL": "none",
                    "GIT_COMMITTER_NAME": "deploy-test", "GIT_COMMITTER_EMAIL": "none",
                    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                    "STORE": str(self.store), "COLOSSEUM_SSM_BUCKET": BUCKET,
                    "COLOSSEUM_SSM_POLL_S": "0"}

        origin = self.tmp / "origin.git"
        self.git("init", "--quiet", "--bare", "--initial-branch=main", str(origin), cwd=self.tmp)
        self.repo = self.tmp / "repo"
        self.git("init", "--quiet", "--initial-branch=main", str(self.repo), cwd=self.tmp)
        (self.repo / "ops" / "host").mkdir(parents=True)
        shutil.copy(SCRIPT, self.repo / "ops" / "host" / "deploy-ssm.sh")
        # The host's install.sh, for the run below: it writes down the commit it was given.
        (self.repo / "ops" / "host" / "install.sh").write_text(
            '#!/usr/bin/env bash\nprintf "%s\\n" "$1" >> "$STORE/installed"\n')
        self.commit("on the default branch")
        self.git("remote", "add", "origin", str(origin))
        self.git("push", "--quiet", "origin", "main")
        self.merged = self.git("rev-parse", "HEAD")
        (self.repo / "later.txt").write_text("not pushed\n")
        self.commit("not on the default branch yet")
        self.unmerged = self.git("rev-parse", "HEAD")

        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "aws"
        fake.write_text(FAKE_AWS)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.env["PATH"] = f"{bin_dir}{os.pathsep}{self.env['PATH']}"

    def git(self, *args, cwd=None):
        out = subprocess.run(["git", *args], cwd=cwd or self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", message)

    def deploy(self, sha, instance=INSTANCE, statuses=("Success",), **env):
        (self.store / "statuses").write_text("".join(s + "\n" for s in statuses))
        return subprocess.run(["bash", "ops/host/deploy-ssm.sh", sha, instance], cwd=self.repo,
                              env={**self.env, **env}, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)

    def asked(self):
        path = self.store / "asked"
        return path.read_text().splitlines() if path.exists() else []

    def commands(self):
        return json.loads((self.store / "parameters.json").read_text())["commands"]

    def test_a_commit_that_is_not_on_origin_main_is_not_sent(self):
        out = self.deploy(self.unmerged)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn(f"deploy-ssm: {self.unmerged} is NOT on origin/main", out.stderr)
        self.assertEqual(self.asked(), [])  # aws was never called: nothing left this machine

    def test_something_that_is_not_an_instance_id_is_refused_before_aws(self):
        out = self.deploy(self.merged, instance="user@pools-host")
        self.assertEqual(out.returncode, 2)
        self.assertEqual(self.asked(), [])

    def test_without_a_bucket_nothing_is_sent(self):
        env = {k: v for k, v in self.env.items() if k != "COLOSSEUM_SSM_BUCKET"}
        out = subprocess.run(["bash", "ops/host/deploy-ssm.sh", self.merged, INSTANCE], cwd=self.repo,
                             env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(self.asked(), [])

    def test_a_commit_on_origin_main_goes_through_s3_and_run_command(self):
        out = self.deploy(self.merged)
        self.assertEqual(out.returncode, 0, out.stderr)
        object_url = (self.store / "key").read_text()
        self.assertRegex(object_url, rf"^s3://{BUCKET}/colosseum-pools/{self.merged}-\d{{8}}T\d{{6}}Z-\d+\.tar\.gz$")
        asked = self.asked()
        self.assertTrue(asked[0].startswith("s3 cp ") and asked[0].endswith(object_url), asked[0])
        # The link outlives the agent's 10 minutes to take the command by curl's 5.
        self.assertEqual(asked[1], f"s3 presign {object_url} --expires-in 900")
        send = asked[2]
        self.assertIn(f"ssm send-command --instance-ids {INSTANCE} --document-name AWS-RunShellScript", send)
        self.assertIn("--timeout-seconds 600", send)
        params = json.loads((self.store / "parameters.json").read_text())
        self.assertEqual(params["executionTimeout"], ["1500"])
        self.assertEqual(asked[-1], f"s3 rm --only-show-errors {object_url}")  # nothing left in the bucket
        self.assertIn("install: current -> host-says", out.stdout)
        self.assertIn(f"deploy-ssm: {self.merged} installed on {INSTANCE}", out.stdout)

    def test_the_host_checks_the_very_archive_that_was_uploaded(self):
        self.assertEqual(self.deploy(self.merged).returncode, 0)
        digest = hashlib.sha256((self.store / "uploaded").read_bytes()).hexdigest()
        script = "\n".join(self.commands())
        self.assertIn(f'echo "{digest}  $archive" | sha256sum -c --quiet -', script)
        self.assertIn(f"bash /opt/colosseum-pools/releases/.{self.merged}.upload/ops/host/install.sh {self.merged}",
                      script)

    def test_two_runs_of_one_commit_never_share_an_object(self):
        # Each run removes its own object: a shared key would let the first take the second's archive.
        self.assertEqual(self.deploy(self.merged).returncode, 0)
        first = (self.store / "key").read_text()
        self.assertEqual(self.deploy(self.merged).returncode, 0)
        self.assertNotEqual((self.store / "key").read_text(), first)

    def test_the_host_waits_while_the_command_runs(self):
        out = self.deploy(self.merged, statuses=("Pending", "InProgress", "InProgress", "Success"))
        self.assertEqual(out.returncode, 0, out.stderr)
        statuses = [a for a in self.asked() if a.endswith("--query Status --output text")]
        self.assertEqual(len(statuses), 4)

    def test_a_failure_on_the_host_fails_here_and_says_what_the_host_said(self):
        out = self.deploy(self.merged, statuses=("InProgress", "Failed"))
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("host-stderr", out.stderr)
        self.assertIn("deploy-ssm: Failed (Failed) on the host; command command-1", out.stderr)
        self.assertTrue(self.asked()[-1].startswith("s3 rm "))  # ended: the archive goes

    def test_no_answer_in_time_cancels_and_keeps_the_archive(self):
        # The command may still start after this script gives up: its archive has to be there.
        out = self.deploy(self.merged, statuses=("InProgress",) * 3, COLOSSEUM_SSM_WAIT_S="0")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("no result after 0s; command command-1 is still InProgress", out.stderr)
        self.assertTrue(any(a.startswith("ssm cancel-command --command-id command-1") for a in self.asked()))
        self.assertFalse(any(a.startswith("s3 rm ") for a in self.asked()))
        self.assertIn(f"the archive stays at {(self.store / 'key').read_text()}", out.stderr)

    def test_an_agent_that_never_takes_the_command_ends_as_ssm_says(self):
        # SSM ends an untaken command after --timeout-seconds as TimedOut; the reason is in StatusDetails.
        (self.store / "details").write_text("DeliveryTimedOut")
        out = self.deploy(self.merged, statuses=("Pending", "TimedOut"), COLOSSEUM_SSM_WAIT_S="60")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("deploy-ssm: TimedOut (DeliveryTimedOut) on the host", out.stderr)
        self.assertEqual(len([a for a in self.asked() if a.endswith("--query Status --output text")]), 2)
        self.assertTrue(self.asked()[-1].startswith("s3 rm "))

    def test_a_send_that_fails_after_it_went_out_keeps_the_archive(self):
        # No id came back, but SSM may have taken the command, and its host would fetch this object.
        (self.store / "send-fails").touch()
        out = self.deploy(self.merged)
        self.assertNotEqual(out.returncode, 0)
        self.assertFalse(any(a.startswith("s3 rm ") for a in self.asked()))
        self.assertIn(f"the archive stays at {(self.store / 'key').read_text()}, because command "
                      "(no id: send-command failed, SSM may have taken it)", out.stderr)

    def test_an_invocation_not_there_yet_is_waited_for(self):
        # More often than the three refusals that end a run: this answer alone is the one to wait out.
        out = self.deploy(self.merged, statuses=("ERR:InvocationDoesNotExist",) * 4 + ("InProgress", "Success"))
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_a_status_that_can_not_be_read_fails_soon_and_keeps_the_archive(self):
        # No permission to read does not pass by waiting: three refusals in a row, then out.
        out = self.deploy(self.merged, statuses=("ERR:AccessDeniedException",) * 3, COLOSSEUM_SSM_WAIT_S="60")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("could not read command command-1", out.stderr)
        self.assertIn("AccessDeniedException", out.stderr)
        self.assertEqual(len([a for a in self.asked() if a.endswith("--query Status --output text")]), 3)
        self.assertFalse(any(a.startswith("s3 rm ") for a in self.asked()))

    def test_output_that_can_not_be_read_does_not_undo_a_release(self):
        (self.store / "no-output").touch()
        out = self.deploy(self.merged)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("warning: could not read the host's stdout", out.stderr)
        self.assertIn(f"deploy-ssm: {self.merged} installed on {INSTANCE}", out.stdout)

    def test_output_that_can_not_be_read_does_not_save_a_failure(self):
        (self.store / "no-output").touch()
        out = self.deploy(self.merged, statuses=("Failed",))
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("deploy-ssm: Failed (no details) on the host", out.stderr)


class HostCommand(unittest.TestCase):
    """The command the host gets, run here: /opt/colosseum-pools moved into a temporary directory, and a
    `curl` that hands over the object the fake aws kept."""

    def setUp(self):
        self.case = DeploySsm("test_a_commit_on_origin_main_goes_through_s3_and_run_command")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        bin_dir = self.case.tmp / "bin"
        (bin_dir / "aws").write_text(FAKE_AWS.replace(
            '(store / "object").unlink(missing_ok=True)', "pass"))  # keep the object for the run below
        curl = bin_dir / "curl"
        curl.write_text('#!/usr/bin/env bash\nwhile [ "$1" != -o ]; do shift; done\ncp "$STORE/object" "$2"\n')
        curl.chmod(curl.stat().st_mode | stat.S_IXUSR)
        if shutil.which("sha256sum") is None:
            shim = bin_dir / "sha256sum"
            shim.write_text('#!/usr/bin/env bash\nshift; shift; exec shasum -a 256 -c -\n')
            shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
        self.base = self.case.tmp / "opt"
        (self.base / "releases").mkdir(parents=True)

    def run_on_host(self):
        script = "\n".join(self.case.commands()).replace("/opt/colosseum-pools", str(self.base))
        return subprocess.run(["bash", "-c", script], env=self.case.env, capture_output=True, text=True)

    def test_it_unpacks_the_commit_and_runs_its_install(self):
        self.assertEqual(self.case.deploy(self.case.merged).returncode, 0)
        out = self.run_on_host()
        self.assertEqual(out.returncode, 0, out.stderr)
        sha = self.case.merged
        self.assertEqual((self.case.store / "installed").read_text().split(), [sha])
        upload = self.base / "releases" / f".{sha}.upload"
        self.assertTrue((upload / "ops" / "host" / "install.sh").exists())
        self.assertFalse((upload / "later.txt").exists())  # not in this commit

    def test_an_archive_that_is_not_the_one_made_here_is_not_unpacked(self):
        self.assertEqual(self.case.deploy(self.case.merged).returncode, 0)
        with open(self.case.store / "object", "ab") as obj:
            obj.write(b"\0")
        out = self.run_on_host()
        self.assertNotEqual(out.returncode, 0)
        self.assertFalse((self.case.store / "installed").exists())
        self.assertFalse((self.base / "releases" / f".{self.case.merged}.upload").exists())


class Script(unittest.TestCase):
    def test_no_address_and_no_account_in_it(self):
        text = SCRIPT.read_text()
        self.assertNotRegex(text, r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
        self.assertNotRegex(text, r"\bi-[0-9a-f]{8,17}\b")
        self.assertNotRegex(text, r"\b\d{12}\b")
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


if __name__ == "__main__":
    unittest.main()
