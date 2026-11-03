#!/usr/bin/env bash
# Puts one commit of this repository on the pools host through AWS Systems Manager: no ssh, and no
# address of the host anywhere. Testnet only.
#
#   COLOSSEUM_SSM_BUCKET=<bucket> ops/host/deploy-ssm.sh <commit> <instance id>
#
# The same release as ops/host/deploy.sh: only a commit on origin/main, its tracked files as a git
# archive made here, and that commit's ops/host/install.sh run as root on the host. Only the road
# differs. The archive (2.4 MB gzipped on 3 November 2026) is far over what one Run Command carries
# (a document is capped at 64 KB), so it goes to S3, and the host fetches it through a presigned link
# that lives 15 minutes: the instance's role needs no S3 permission, and only this machine's
# environment names the bucket. The host checks the archive's sha256, taken here, before it unpacks
# anything; the object is removed when this script ends. AWS credentials and the region come from
# the aws CLI's own configuration.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
usage="usage: COLOSSEUM_SSM_BUCKET=<bucket> ops/host/deploy-ssm.sh <commit> <instance id>"
sha="$(git rev-parse --verify "${1:?$usage}^{commit}")"
instance="${2:?$usage}"
bucket="${COLOSSEUM_SSM_BUCKET:?$usage}"
poll="${COLOSSEUM_SSM_POLL_S:-5}"
wait_s="${COLOSSEUM_SSM_WAIT_S:-1500}"

[[ "$instance" =~ ^i-[0-9a-f]{8,17}$ ]] || { echo "deploy-ssm: not an instance id: $instance" >&2; exit 2; }

# The same gate as deploy.sh, before anything leaves this machine.
if git merge-base --is-ancestor "$sha" origin/main 2>/dev/null; then
  echo "deploy-ssm: $sha is on origin/main"
else
  echo "deploy-ssm: $sha is NOT on origin/main; nothing was sent. Fetch, or merge it first." >&2
  exit 1
fi

work="$(mktemp -d)"
object="s3://$bucket/colosseum-pools/$sha.tar.gz"
uploaded=""
cleanup() {
  rm -rf "$work"
  [ -z "$uploaded" ] || aws s3 rm --only-show-errors "$object" || true
}
trap cleanup EXIT

git archive --format=tar.gz -o "$work/release.tar.gz" "$sha"
digest="$(shasum -a 256 "$work/release.tar.gz" | cut -d' ' -f1)"
aws s3 cp --only-show-errors "$work/release.tar.gz" "$object"
uploaded=1
url="$(aws s3 presign "$object" --expires-in 900)"

# What runs on the host, as root: fetch, check, unpack where deploy.sh unpacks, install. One bash
# heredoc, so the shell the agent starts doesn't matter.
SHA="$sha" DIGEST="$digest" URL="$url" WAIT_S="$wait_s" python3 - "$work/parameters.json" <<'PY'
import json, os, shlex, sys

sha, digest, url = os.environ["SHA"], os.environ["DIGEST"], os.environ["URL"]
upload = f"/opt/colosseum-pools/releases/.{sha}.upload"
release = [
    'archive="$(mktemp)"',
    "trap 'rm -f \"$archive\"' EXIT",
    f'curl -fsS --max-time 300 -o "$archive" {shlex.quote(url)}',
    f'echo "{digest}  $archive" | sha256sum -c --quiet -',
    f"rm -rf {upload}",
    f"install -d -m 0755 {upload}",
    f'tar -xz --no-same-owner -C {upload} -f "$archive"',
    f"bash {upload}/ops/host/install.sh {sha}",
]
commands = ["bash -euo pipefail <<'RELEASE'", *release, "RELEASE"]
with open(sys.argv[1], "w") as out:
    json.dump({"commands": commands, "executionTimeout": [os.environ["WAIT_S"]]}, out, indent=1)
PY

command_id="$(aws ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
  --comment "colosseum-pools ${sha:0:12}" --parameters "file://$work/parameters.json" \
  --query Command.CommandId --output text)"
echo "deploy-ssm: command $command_id sent to $instance"

ask() {  # <field of the invocation>
  aws ssm get-command-invocation --command-id "$command_id" --instance-id "$instance" \
    --query "$1" --output text
}
deadline=$((SECONDS + wait_s))
while :; do
  # Right after the send the invocation may not exist yet: that reads as still pending.
  status="$(ask Status 2>/dev/null || echo Pending)"
  case "$status" in Success|Failed|Cancelled|TimedOut) break ;; esac
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "deploy-ssm: no result after ${wait_s}s; command $command_id is still $status" >&2
    exit 1
  fi
  sleep "$poll"
done
ask StandardOutputContent
ask StandardErrorContent >&2
if [ "$status" != Success ]; then
  echo "deploy-ssm: $status on the host; command $command_id" >&2
  exit 1
fi
echo "deploy-ssm: $sha installed on $instance"
