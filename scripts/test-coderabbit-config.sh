#!/usr/bin/env bash
# .coderabbit.yaml: the bot reviews the first push of a pull request by itself, later pushes on
# request.
#
#   bash scripts/test-coderabbit-config.sh
#
# What it proves:
#   1. reviews.auto_review sets auto_incremental_review: false and
#      auto_pause_after_reviewed_commits: 2, each once, and does not switch automatic review
#      off (enabled: false);
#   2. `reviews` is the only top-level key: everything else stays at CodeRabbit's defaults;
#   3. the check can still go red: the same check run on broken copies of the file fails.
# No YAML parser here (CI's scrub job has no Python packages), so the FORM is strict instead:
# block mappings, two-space indentation, spaces only. A flow-style or tab-indented file is one
# CodeRabbit may read differently from this check, so it fails rather than slips through.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
python3 - <<'PY'
import re, sys

CONFIG = ".coderabbit.yaml"
WANT = {"auto_incremental_review": "false", "auto_pause_after_reviewed_commits": "2"}


def check(text):
    fails = []
    lines = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    if any("\t" in l for l in lines):
        fails.append("tab in indentation: YAML forbids it")
    top = [l for l in lines if re.match(r"^\S", l)]
    if top != ["reviews:"]:
        fails.append(f"top-level keys must be exactly `reviews:`, got {top}")
    block, inside = [], False
    for l in lines:
        if l == "  auto_review:":
            inside = True
            continue
        if inside and re.match(r"^ {0,3}\S", l):
            inside = False
        if inside:
            block.append(l)
    if sum(1 for l in lines if l == "  auto_review:") != 1:
        fails.append("`  auto_review:` must appear once, as a block under reviews")
    seen = {}
    for l in block:
        m = re.fullmatch(r"    ([a-z_]+): *([^ #]+) *(#.*)?", l)
        if not m:
            fails.append(f"auto_review line not in the checked form `    key: value`: {l!r}")
            continue
        if m.group(1) in seen:
            fails.append(f"auto_review.{m.group(1)} given more than once")
        seen[m.group(1)] = m.group(2)
    for k, v in WANT.items():
        if seen.get(k) != v:
            fails.append(f"auto_review.{k} must be {v}, got {seen.get(k, 'nothing')}")
    if seen.get("enabled") == "false":
        fails.append("auto_review.enabled: false switches automatic review off entirely")
    return fails


text = open(CONFIG, encoding="utf-8").read()
fails = check(text)

mutants = {
    "incremental reviews back on": text.replace("auto_incremental_review: false", "auto_incremental_review: true"),
    "incremental key dropped": text.replace("    auto_incremental_review: false\n", ""),
    "pause at the default 5": text.replace("auto_pause_after_reviewed_commits: 2", "auto_pause_after_reviewed_commits: 5"),
    "automatic review off": text.replace("  auto_review:\n", "  auto_review:\n    enabled: false\n"),
    "a second top-level key": text + "chat:\n  auto_reply: true\n",
    "flow style": text.replace("  auto_review:\n    auto_incremental_review: false\n",
                               "  auto_review: {auto_incremental_review: false}\n"),
}
for name, broken in mutants.items():
    if broken == text:
        fails.append(f"mutant {name!r} did not apply: the file changed, update this check")
    elif not check(broken):
        fails.append(f"mutant {name!r} passes: the check is blind to it")

print(f"auto_review checked · {len(mutants)} broken copies all fail" if not fails
      else "\n".join("FAIL  " + x for x in fails))
sys.exit(1 if fails else 0)
PY
