#!/usr/bin/env bash
# scripts/verify-live.py against a copy of the site on disk, with no network: a pack of this checkout's HEAD
# and the list scripts/pack-site.sh writes beside it. Cloudflare's two tags in index.html are cut, in either
# order, and the copy passes with exit 0; a file that differs or is missing, or anything else on the page,
# fails it with exit 1 and names the file; a list it cannot use stops it with exit 2.
set -euo pipefail
cd "$(dirname "$0")/.."
TMP="$(mktemp -d -t verifylive.XXXXXX)"; trap 'rm -rf "$TMP"' EXIT
./scripts/pack-site.sh HEAD "$TMP/pack" >/dev/null
LIST="$TMP/pack.sha256"
n="$(wc -l < "$LIST" | tr -d ' ')"

# The two tags as Cloudflare writes them before </body>, with its ids and tokens replaced by zeros.
BEACON=$(cat <<'EOF'
<script type="module" src="https://static.cloudflareinsights.com/beacon.min.js/v000000000000" integrity="sha512-AAAA" data-cf-beacon='{"version":"2024.11.0","token":"00000000000000000000000000000000","r":1,"spa":2}' crossorigin="anonymous"></script>
EOF
)
CHALLENGE=$(cat <<'EOF'
<script>(function(){function c(){var b=a.contentDocument||(a.contentWindow&&a.contentWindow.document);if(b){var d=b.createElement('script');d.innerHTML="window.__CF$cv$params={r:'0',t:'0'};var u='/cdn-cgi/challenge-platform/scripts/jsd/main.js?ray=0',a=document.createElement('script');a.src=u;document.getElementsByTagName('head')[0].appendChild(a);";b.getElementsByTagName('head')[0].appendChild(d)}}if(document.body){var a=document.createElement('iframe');a.height=1;a.width=1;document.body.appendChild(a);c()}})();</script>
EOF
)

site() { rm -rf "$TMP/site"; cp -R "$TMP/pack" "$TMP/site"; }
# insert <text>...: puts the texts, in order, right before </body> of the copy's index.html, byte for byte.
insert() {
  python3 -I - "$TMP/site/index.html" "$@" <<'PY'
import sys
path, parts = sys.argv[1], [p.encode() for p in sys.argv[2:]]
html = open(path, "rb").read()
at = html.rindex(b"</body>")
open(path, "wb").write(html[:at] + b"".join(parts) + html[at:])
PY
}
# check <what> <exit wanted> <a line the output must hold> [list]
check() {
  local out code=0
  out="$(python3 scripts/verify-live.py "${4:-$LIST}" "$TMP/site" 2>&1)" || code=$?
  [ "$code" = "$2" ] || { echo "FAIL $1: exit $code, wanted $2"; printf '%s\n' "$out"; exit 1; }
  grep -qF -- "$3" <<<"$out" || { echo "FAIL $1: no line holding: $3"; printf '%s\n' "$out"; exit 1; }
  echo "ok   $1"
}

site
check "the pack itself passes" 0 "match=$n differ=0 of $n"
site; insert "$BEACON"$'\n' "$CHALLENGE"
check "Cloudflare's beacon, then its challenge script, are cut" 0 "challenge script x1 and Web Analytics beacon x1"
site; insert "$CHALLENGE" "$BEACON"$'\n'
check "the challenge script, then the beacon, are cut" 0 "match=$n differ=0 of $n"
site; insert "$BEACON"$'\n' "$CHALLENGE" "<script>alert(1)</script>"
check "anything else on the page fails it" 1 "DIFF index.html"
site; printf '/* one more line */\n' >> "$TMP/site/style.css"
check "a file that differs fails it, by name" 1 "DIFF style.css"
site; rm "$TMP/site/views/pool.js"
check "a file that is missing fails it, by name" 1 "ERR  views/pool.js"
site; : > "$TMP/empty.sha256"
check "an empty list stops it" 2 "names no file" "$TMP/empty.sha256"
printf 'not a sha256 line\n' > "$TMP/odd.sha256"
check "a line that is not a sha256 line stops it" 2 "is not a sha256 line" "$TMP/odd.sha256"
sed -n '1s|  \./.*|  ./../outside.txt|p' "$LIST" > "$TMP/outside.sha256"
check "a file outside the pack stops it" 2 "is not a sha256 line" "$TMP/outside.sha256"
echo "test-verify-live: the check cuts only Cloudflare's two tags and fails on anything else"
