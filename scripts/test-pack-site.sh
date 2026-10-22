#!/usr/bin/env bash
# The packer still does what it says: every file of app/ equal to its blob, build.json with the commit
# and nothing else beside them. Packs this checkout's HEAD into a directory of its own and removes it.
set -euo pipefail
cd "$(dirname "$0")/.."
TMP="$(mktemp -d -t packsite.XXXXXX)"; trap 'rm -rf "$TMP"' EXIT
HEAD="$(git rev-parse HEAD)"
n="$(git ls-tree -r HEAD -- app/ | wc -l | tr -d ' ')"
line="$(./scripts/pack-site.sh HEAD "$TMP/out" | tail -1)"
want="files=$n match=$n differ=0 extra=0 build={\"commit\":\"$HEAD\"} "
case "$line" in
  "$want"*) echo "ok   packed $n files and build.json for $HEAD" ;;
  *) echo "FAIL the packer said: $line"; echo "     expected it to start: $want"; exit 1 ;;
esac
[ "$(cat "$TMP/out/build.json")" = "{\"commit\":\"$HEAD\"}" ] || { echo "FAIL build.json"; exit 1; }
if ./scripts/pack-site.sh HEAD "$TMP/out" >/dev/null 2>&1; then echo "FAIL packed over an existing directory"; exit 1; fi
echo "ok   refuses a directory that exists"
# A commit with no site, and one whose app/ holds a symlink: both refused, and nothing written.
none="$(git commit-tree "$(git mktree </dev/null)" -m "no site")"
if ./scripts/pack-site.sh "$none" "$TMP/none" >/dev/null 2>&1 || [ -e "$TMP/none" ]; then echo "FAIL packed a commit with no app/index.html"; exit 1; fi
echo "ok   refuses a commit with no site"
index="$(git rev-parse HEAD:app/index.html)"
link="$(printf 'index.html' | git hash-object -w --stdin)"
app="$(printf '100644 blob %s\tindex.html\n120000 blob %s\tlink.html\n' "$index" "$link" | git mktree)"
linked="$(git commit-tree "$(printf '040000 tree %s\tapp\n' "$app" | git mktree)" -m "a symlink in app")"
if ./scripts/pack-site.sh "$linked" "$TMP/linked" >/dev/null 2>&1 || [ -e "$TMP/linked" ]; then echo "FAIL packed a symlink as a file"; exit 1; fi
echo "ok   refuses a symlink in app/"
echo "test-pack-site: the packer behaves"
