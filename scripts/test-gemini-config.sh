#!/usr/bin/env bash
# .gemini/config.yaml against the tree.
#
#   bash scripts/test-gemini-config.sh
#
# What it proves:
#   1. ignore_patterns is a TOP-LEVEL key. Nested under code_review the YAML stays valid and the
#      filter is dead (usenami-platform, a month and a half, CTO 30 Aug 2026);
#   2. every pattern matches at least one tracked file: a pattern that matches nothing is a
#      typo or a moved folder, and the folder it meant is being read;
#   3. no pattern matches the code: contracts, their tests, gateway, ops, agents, app code,
#      stress model, scripts, CI.
# Glob here is a small implementation (`*` inside a segment, `**` across segments), as the
# patterns are written; it checks the LIST, not the bot. Whether Gemini skips a file is seen on
# a pull request.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
python3 - <<'PY'
import re, subprocess, sys

CONFIG = ".gemini/config.yaml"
CODE = re.compile(r"^(src/|test/|gateway/.*\.py$|ops/.*\.(py|sh|service|in)$|agents/.*\.py$"
                  r"|app/(app|config)\.js$|app/index\.html$|app/style\.css$|app/lib/|app/views/|app/tests/[^/]*\.mjs$"
                  r"|stress/.*\.(py|sh)$|spike/.*\.py$|scripts/|\.github/|foundry\.toml$|remappings\.txt$|\.gemini/)")

lines = open(CONFIG, encoding="utf-8").read().splitlines()
fails = []

top = [i for i, l in enumerate(lines) if l.startswith("ignore_patterns:")]
nested = [i for i, l in enumerate(lines) if re.match(r"^\s+ignore_patterns:", l)]
if len(top) != 1 or nested:
    fails.append(f"ignore_patterns must be one top-level key (top-level: {len(top)}, nested: {len(nested)})")

patterns = []
if top:
    for l in lines[top[0] + 1:]:
        if re.match(r"^\S", l):
            break
        m = re.match(r'^\s+-\s+"([^"]+)"', l)
        if m:
            patterns.append(m.group(1))
if not patterns:
    fails.append("no patterns read from ignore_patterns")

def to_re(p):
    out, i = "", 0
    while i < len(p):
        if p.startswith("**/", i):
            out += r"(?:.*/)?"; i += 3
        elif p.startswith("**", i):
            out += r".*"; i += 2
        elif p[i] == "*":
            out += r"[^/]*"; i += 1
        else:
            out += re.escape(p[i]); i += 1
    return re.compile("^" + out + "$")

files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split()
compiled = [(p, to_re(p)) for p in patterns]
for p, rx in compiled:
    if not any(rx.match(f) for f in files):
        fails.append(f"pattern {p!r} matches no tracked file")
code = [f for f in files if CODE.match(f)]
if len(code) < 50:
    fails.append(f"only {len(code)} code files found -- the code list is broken, the check is blind")
for f in code:
    hit = [p for p, rx in compiled if rx.match(f)]
    if hit:
        fails.append(f"code file {f} is ignored by {hit}")

ignored = sum(1 for f in files if any(rx.match(f) for _, rx in compiled))
print(f"patterns {len(patterns)} · tracked files {len(files)} · ignored {ignored} · code files {len(code)}, none ignored"
      if not fails else "\n".join("FAIL  " + x for x in fails))
sys.exit(1 if fails else 0)
PY
